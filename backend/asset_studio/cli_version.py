"""Which `claude` binary the Studio actually runs, and whether a newer one is published.

``cc_session.find_claude`` documents how this goes wrong: the VSCode extension held 2.1.233 while
the installed CLI was already 2.1.250 — seventeen versions behind, for weeks, with nothing
anywhere saying so. It is invisible precisely because ``claude --version`` in a terminal reports
whatever is first on PATH, so the machine looks up to date while every Studio session runs the old
binary. So the version reported here is the version of the binary the STUDIO resolved.

The published versions come from the npm registry's dist-tags, which is where the CLI's own
installer reads them. Both channels are reported because they genuinely differ, and picking the
wrong one installs the older build: on 2026-09-01 ``next`` was 2.1.257 while ``latest`` was still
2.1.252, so ``claude install latest --force`` would have gone backwards from 257.

Everything here runs on a worker thread and is cached on disk. Nothing blocks the event loop, and
the registry is not asked more than once every CHECK_EVERY seconds.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from typing import Optional

from . import fsutil
from .config import DATA_DIR

DIST_TAGS = "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags"
CHECK_EVERY = 6 * 3600.0
_CACHE_F = DATA_DIR / "cli_version.json"
_VER_RE = re.compile(r"(\d+\.\d+\.\d+)")

_lock = threading.Lock()
_state: dict = {}
_checking = False
# Windows hides the console window a subprocess would otherwise flash up.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _read() -> dict:
    global _state
    if not _state:
        try:
            d = json.loads(_CACHE_F.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _state = d
        except Exception:
            _state = {}
    return _state


def _write(d: dict) -> None:
    global _state
    _state = d
    try:
        _CACHE_F.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_F.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        fsutil.replace(tmp, _CACHE_F)
    except OSError:
        pass


def _tuple(v: str) -> tuple:
    m = _VER_RE.search(v or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0, 0, 0)


def _local_version(exe: str) -> str:
    """`<exe> --version` for the binary the Studio resolved — not whatever PATH answers."""
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                           creationflags=_NO_WINDOW)
        m = _VER_RE.search((r.stdout or "") + (r.stderr or ""))
        return m.group(1) if m else ""
    except Exception:
        return ""


def _published() -> dict:
    try:
        import httpx
        r = httpx.get(DIST_TAGS, timeout=15)
        r.raise_for_status()
        d = r.json()
        return {k: str(v) for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}
    except Exception:
        return {}


def _do_check() -> None:
    global _checking
    try:
        from . import cc_session
        exe = cc_session.find_claude() or ""
        tags = _published()
        cur = _local_version(exe)
        latest, nxt = tags.get("latest", ""), tags.get("next", "")
        # "Newer" means newer than what we run, on either channel. `next` is reported separately
        # so the UI can say which command actually installs it.
        newest = max([v for v in (latest, nxt) if v], key=_tuple, default="")
        _write({
            "path": exe, "version": cur,
            "latest": latest, "next": nxt,
            "newest": newest,
            "update_available": bool(newest and cur and _tuple(newest) > _tuple(cur)),
            "on_channel": ("next" if nxt and cur == nxt else "latest" if latest and cur == latest
                           else "pinned"),
            "checked_at": time.time(),
        })
    finally:
        with _lock:
            _checking = False


def refresh_soon(force: bool = False) -> None:
    """Kick a check on a worker thread if the cached one is stale. Never blocks the caller."""
    global _checking
    d = _read()
    with _lock:
        if _checking:
            return
        if not force and time.time() - float(d.get("checked_at", 0) or 0) < CHECK_EVERY:
            return
        _checking = True
    threading.Thread(target=_do_check, daemon=True).start()


def info() -> dict:
    """What is known right now, plus a background refresh when it has gone stale."""
    refresh_soon()
    d = dict(_read())
    d["checking"] = _checking
    if not d.get("version"):
        # first call of a fresh install — say what binary we resolved even before the check lands
        try:
            from . import cc_session
            d.setdefault("path", cc_session.find_claude() or "")
        except Exception:
            pass
    return d


def busy_sessions() -> list[str]:
    """Projects with a turn in flight. Replacing the binary does not touch a RUNNING process —
    Windows keeps the image the process loaded — but a session that respawns mid-job would come
    back on a different build, so the UI names them and lets the user decide."""
    out = []
    try:
        from . import cc_session
        with cc_session._live_lock:
            for pid, live in list(cc_session._live.items()):
                if getattr(live, "turn_active", False):
                    out.append(pid)
    except Exception:
        pass
    return out


def install(target: str = "") -> dict:
    """Run the CLI's own installer. `target` is a dist-tag (`latest`, `stable`) or an exact
    version; empty means the newest published on either channel.

    An exact version is the default because a tag can point backwards: `latest` was 2.1.252 on
    the day `next` was 2.1.257.
    """
    from . import cc_session
    exe = cc_session.find_claude()
    if not exe:
        return {"ok": False, "error": "No claude binary found."}
    d = _read()
    tgt = target or d.get("newest") or "latest"
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,32}", tgt):
        return {"ok": False, "error": "Not a version or channel name: %r" % tgt}
    try:
        r = subprocess.run([exe, "install", tgt, "--force"], capture_output=True, text=True,
                           timeout=900, creationflags=_NO_WINDOW)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # The binary on disk has just changed, so the two places that remember where it is must forget
    # now rather than in thirty seconds: the resolved path, and the "which CLIs are installed" list.
    try:
        cc_session.invalidate_claude()
        from . import agents as _agents
        _agents.invalidate()
    except Exception:
        pass
    _do_check()                       # report the version that is now on disk, not the old one
    now = _read().get("version", "")
    return {"ok": _tuple(now) >= _tuple(tgt) if _VER_RE.search(tgt) else r.returncode == 0,
            "target": tgt, "version": now, "output": out[-2000:]}
