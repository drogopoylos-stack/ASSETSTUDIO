"""System telemetry + app info + restart/version (for in-app updates)."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__, keychain
from ..config import FRONTEND_DIST, settings
from ..providers.registry import LOAD_ERRORS
from ..system_stats import collect

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/stats")
def stats():
    return collect()


_STARTED_AT = time.time()
_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_id() -> str:
    """A fingerprint of the backend Python actually on disk, right now.

    Taken from the size and mtime of every .py under the package — cheap enough to compute on a
    poll, and it changes the moment a file is edited. Compared against the one taken at import,
    this answers the question the Studio could not: is the process serving me older than the code
    I am looking at? Nothing else knew. Every backend change had to end with a human being told
    to restart, and a forgotten restart looks exactly like a feature that does not work.
    """
    h = hashlib.md5()
    try:
        for root, dirs, files in os.walk(_PKG):
            dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".venv"))
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                try:
                    st = os.stat(os.path.join(root, f))
                except OSError:
                    continue
                rel = os.path.relpath(os.path.join(root, f), _PKG)
                h.update(f"{rel}:{st.st_size}:{int(st.st_mtime)}|".encode())
    except OSError:
        return ""
    return h.hexdigest()[:12]


_CODE_AT_BOOT = _code_id()


@router.get("/version")
def version():
    """A build id that changes whenever the served UI is rebuilt, so the frontend
    can detect 'an update landed' and offer to reload without a manual restart.

    ``code_stale`` is the backend half of the same question: the Python on disk has been edited
    since this process imported it, so what is running is not what is written."""
    build = ""
    try:
        build = hashlib.md5((FRONTEND_DIST / "index.html").read_bytes()).hexdigest()[:12]
    except Exception:
        pass
    now = _code_id()
    return {"version": __version__, "build": build,
            "code": _CODE_AT_BOOT, "code_now": now,
            "code_stale": bool(now and _CODE_AT_BOOT and now != _CODE_AT_BOOT),
            "started_at": _STARTED_AT,
            "uptime_s": round(time.time() - _STARTED_AT, 1),
            # Not "what would be lost" — a session outlives a restart and is re-adopted. It is
            # what is IN FLIGHT, so the UI can say "three turns are running" before you restart.
            "busy": _busy_now()}


def _busy_now() -> dict:
    """What is in flight right now — NOT what a restart destroys.

    Sessions survive a restart by design (see `restart` below), so this is information, not a
    warning. Kept separate from the restart decision on purpose: the two were conflated once and
    the UI told the user a restart would kill their agents, which is not true."""
    out = {"sessions": 0, "turns": 0, "agents": 0, "projects": [], "stranded": []}
    try:
        from .. import cc_session
        st = cc_session.live_status()
        out["sessions"] = len(st.get("statuses") or {})
        busy = [p for p, v in (st.get("statuses") or {}).items() if v]
        out["turns"] = len(busy)
        out["projects"] = busy[:8]
        out["agents"] = int(st.get("running_agents_total") or 0)
        out["stranded"] = _stranded_by_restart(st)
        out["unkeepered"] = _sessions_without_a_keeper(st)
    except Exception:
        pass
    return out


def _has_keeper(project_id: str) -> bool:
    """Is this session's stdin held by a keeper rather than by this backend?

    THE WHOLE QUESTION A RESTART TURNS ON. A stream-json session exits one second after its stdin
    closes; when the backend owned that pipe, stopping the backend ended the session and every
    background agent inside it. A keeper holds it instead, and then a restart genuinely costs
    nothing. Sessions started before the keeper existed do not have one, which is why this is
    asked per session and not answered once for the whole app."""
    try:
        from .. import cc_session
        live = cc_session._live.get(project_id)
        return bool(live is not None and cc_session.keeper_alive(getattr(live, "inbox", "")))
    except Exception:
        return False


def _sessions_without_a_keeper(st: dict) -> list:
    """Sessions a restart really would end: the ones with nobody holding their stdin.

    In practice this is only ever a session started by a backend older than the keeper. It empties
    itself: restart once, and every session after that has one."""
    try:
        return [pid for pid in (st.get("statuses") or {}) if not _has_keeper(pid)][:12]
    except Exception:
        return []


def _stranded_by_restart(st: dict) -> list:
    """Background agents a restart would strand, named so the user can decide.

    A session whose stdin a keeper holds is not affected by a restart at all — the process is not
    touched, the next backend adopts it and writes to the same mailbox, and the agents inside it
    never notice. Those are skipped here, because warning about work that is not at risk trains
    the user to click through the warning that matters.

    What remains is a session with no keeper, and for that one the old account still holds. Two
    agents died exactly this way: the backend restarted at 12:24:23, and at 12:38:48 both wrote
    `[Request interrupted by user]` and stopped, one of them 109 tool calls and 9 file edits into
    its task. Nothing reported it until a new session started four hours later and could only say
    "stopped, no completion record found".
    """
    out: list = []
    try:
        from .. import subagents
        seen: set = set()
        for pid in (st.get("statuses") or {}):
            if pid in seen or _has_keeper(pid):
                continue
            seen.add(pid)
            for a in subagents.open_background(pid):
                out.append({"project": pid,
                            "agent": str(a.get("description") or a.get("agent_id") or "")[:60]})
    except Exception:
        pass
    return out[:12]


class RestartBody(BaseModel):
    force: bool = False       # accepted and ignored; kept so an older client still parses


@router.post("/restart")
def restart(body: RestartBody | None = None):
    """Re-exec the backend so new code takes effect. This does NOT stop the agents.

    Both halves of that are load-bearing, and one of them was missing for a long time. A session
    starts with CREATE_BREAKAWAY_FROM_JOB in its own process group, and its stdout goes to a LOG
    FILE rather than a pipe — a pipe would die with this process and take the session with it.
    That covered the output side. The INPUT side was still a pipe this backend owned, and a
    stream-json session exits one second after its stdin closes, so a restart ended every session
    regardless. A keeper process holds stdin now (`cc_session._start_keeper`), so the session is
    untouched: `adopt_orphans` takes it back over and writes to the same mailbox, with no respawn
    and nothing killed.

    The only thing killed is a stream carrying our marker that is NOT in the registry: an
    untracked duplicate, which doubles a project's API calls and is what burned the rate limit
    once already.

    `busy` is still returned, because "three turns are mid-flight" is worth knowing before you
    restart even when nothing is lost by it.
    """
    busy = _busy_now()

    # The one thing a restart really does cost. See `_stranded_by_restart`: an inherited session
    # cannot be written to, so a background agent loses the client it reports through and is
    # interrupted with nothing recorded. Say so and ask again, rather than finding out hours later.
    stranded = busy.get("stranded") or []
    if stranded and not (body and body.force):
        names = ", ".join(s["agent"] for s in stranded[:3])
        more = "" if len(stranded) <= 3 else " and %d more" % (len(stranded) - 3)
        return {"ok": False, "needs_force": True, "busy": busy,
                "error": "%d background agent%s working (%s%s). A restart cannot keep their "
                         "reporting channel open — they would be interrupted with no result "
                         "recorded. Restart anyway, or wait for them."
                         % (len(stranded), "s" if len(stranded) > 1 else "", names, more)}

    def _do():
        time.sleep(0.4)  # let this response flush first
        _relaunch()
    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "restarting": True, "busy": busy}


def _relaunch() -> None:
    """Start a replacement, then leave — WITHOUT os.execv.

    Windows `execv` is not the image replacement it is on POSIX: the C runtime creates a new
    process and then exits this one, so the two overlap and the replacement can reach bind()
    while this process still owns port 8777. (This codebase already records execv misbehaving on
    Windows, in tools/browse.py, where it mangles a path with spaces.)

    A detached spawn makes the order explicit. The child is created, this process exits at once
    and drops the socket, and the child spends a second or more importing before it binds.

    UNDER THE DESKTOP APP THIS PATH IS NOT USED. The launcher supervises the backend — respawn on
    exit, plus a 6s health watchdog — so a backend that ends itself here gets a COMPETING backend
    started underneath it, and both then fight for the port. The window calls the launcher
    instead (`studioBridge.restartBackend`). This remains the plain-browser path, where there is
    no supervisor to race.

    The Claude sessions are untouched either way: spawned breakaway, with stdout on a log file
    rather than a pipe, so they outlive this process and the next one adopts them.
    """
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    cwd = os.path.dirname(_PKG)          # .../backend, so `-m asset_studio.main` resolves
    for creation in (flags, 0):          # a job object may forbid breakaway; plain is fine too
        try:
            subprocess.Popen([sys.executable, "-m", "asset_studio.main"],
                             cwd=cwd, close_fds=True, creationflags=creation)
            break
        except Exception:
            continue
    else:
        return                           # no replacement started — better to keep serving
    os._exit(0)                          # drop the listening socket NOW, before the child binds


@router.get("/info")
def info():
    return {
        "version": __version__,
        "keychain_backend": keychain.backend_name(),
        "data_dir": str(settings.path.parent),
        "provider_load_errors": LOAD_ERRORS,
        "host": settings.get("host"),
        "port": settings.get("port"),
    }


@router.get("/gpu")
def gpu_status():
    """Live VRAM usage + how long until idle models are auto-released."""
    from .. import gpu_memory
    return gpu_memory.vram_status()


@router.post("/free-gpu")
def free_gpu():
    """Drop cached GPU models now and empty the CUDA allocator (manual reclaim).

    The provider walk inside ``free_now`` reaches the ComfyUI family through
    ``ComfyUnloadMixin.unload``; the extra forced call is what makes this button mean *everything*.
    ``free_comfy`` normally skips itself when the card looks empty (a couple of GETs) so the
    periodic reaper stays quiet — but a model can be offloaded to HOST RAM while the card looks
    free, and that is precisely the 30 GB a finished video leaves behind. A click is an explicit
    "hand it all back", so it does not get to be skipped.
    """
    from .. import gpu_memory
    from ..providers.comfy_common import comfy_base, free_comfy

    out = gpu_memory.free_now("manual", force=True)
    try:
        out["comfyui"] = free_comfy(comfy_base(), force=True)
    except Exception as e:  # noqa: BLE001 - the CUDA half already succeeded; report, never raise
        out["comfyui"] = {"ok": False, "error": str(e)}
    return out
