"""Project dev servers (Vite, Next, CRA…) — find one that is already running, or start it.

A built ``dist/`` page is what a static server can run, so the localhost button prefers it. But a
build is a snapshot: once the source moves on, opening it shows an old version of the game with
no hint that anything is wrong. The live answer is the project's own dev server, which serves the
current source and hot-reloads it.

This module never guesses a port. Starting a server means reading the URL the tool itself prints
(Vite, Next and CRA all print one), and finding an existing server means asking the OS which
ports are listening and which of those belong to a process running inside this project.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .config import settings
from typing import Optional

_URL_RE = re.compile(rb"https?://(?:localhost|127\.0\.0\.1|\[::1\]):(\d+)[^\s\x1b]*")
_DEV_SCRIPTS = ("dev", "start", "serve", "dev:web")

# project path -> {"proc": Popen, "url": str, "started": float, "log": [str]}
_running: dict[str, dict] = {}
_lock = threading.Lock()


def script_for(project: Path) -> Optional[str]:
    """The npm script that runs this project's dev server, or None if it has none."""
    try:
        pkg = json.loads((project / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = pkg.get("scripts") or {}
    if not isinstance(scripts, dict):
        return None
    for name in _DEV_SCRIPTS:
        if name in scripts:
            return name
    return None


def _listening() -> dict[int, int]:
    """{pid: port} for every locally-listening socket we can attribute to a process."""
    out: dict[int, int] = {}
    try:
        import psutil
        for c in psutil.net_connections(kind="inet"):
            if c.status == "LISTEN" and c.pid and c.laddr:
                port = c.laddr.port
                # keep the LOWEST port per pid: Vite's extra sockets (HMR, preview) sit above it
                if c.pid not in out or port < out[c.pid]:
                    out[c.pid] = port
    except Exception:
        pass
    return out


_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
_probe_cache: dict[str, tuple[float, str]] = {}
_PROBE_TTL = 20.0


def _title_of(url: str) -> str:
    """What the server at `url` calls its front page, or "".

    Three servers can all be rooted in the same project and serve completely different things —
    one the built game, one a directory listing of the repo, one a probe harness. The port alone
    cannot tell them apart, and the working directory is identical for all three. The page title
    is what actually distinguishes them, so it is worth one short request."""
    now = time.time()
    hit = _probe_cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    title = ""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=0.7) as r:
            body = r.read(4096)
        m = _TITLE_RE.search(body)
        if m:
            title = " ".join(m.group(1).decode("utf-8", "replace").split())[:70]
    except Exception:
        pass
    _probe_cache[url] = (now + _PROBE_TTL, title)
    return title


def running_servers(project: Path) -> list[dict]:
    """Every local server held by a process running inside this project, lowest port first.

    NOT filtered by process name, and that is the fix. The old check required "node" in the
    process name, so a game served by `python -m http.server` was invisible — a project with
    three live servers on 8080, 8090 and 8097 showed none of them, and the menu fell back to
    listing .html files to serve through the Studio's own port instead. What makes a port this
    project's server is the WORKING DIRECTORY of the process holding it. The language that
    started it is not evidence of anything.

    The Studio's own port is excluded: it is the thing showing you this menu, not your game.
    """
    out: list[dict] = []
    # The home folder is registered as a workspace on this machine, and EVERY project sits
    # inside it — so a plain "is the cwd under the root" test handed it every server running
    # anywhere, including three belonging to PowderPeaks. A folder that contains all your
    # projects is not one of them. Same rule the graph builder uses, imported rather than
    # restated, so the two can never drift apart.
    try:
        from .graphify_index import absurd_root
        if absurd_root(str(project)):
            return out
    except Exception:
        pass
    root = str(project.resolve()).lower()
    try:
        import psutil
    except Exception:
        return out
    try:
        mine = int(settings.get("port", 8777) or 8777)
    except Exception:
        mine = 8777
    seen: set[int] = set()
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception:
        return out
    for c in conns:
        if c.status != "LISTEN" or not c.pid or not c.laddr:
            continue
        # Never offer a server the STUDIO itself is running. Its configured port is the obvious
        # one, but `open_in_browser` also spins up ad-hoc file servers on random high ports from
        # inside this very process — and their working directory is the Studio folder, so they
        # looked exactly like the user's app when the Studio folder was the open workspace.
        # Matching on our own pid catches all of them, present and future.
        if c.pid == os.getpid():
            continue
        port = int(c.laddr.port)
        if port == mine or port in seen:
            continue
        host = str(c.laddr.ip or "")
        if host not in ("127.0.0.1", "0.0.0.0", "::", "::1"):
            continue
        try:
            proc = psutil.Process(c.pid)
            cwd = (proc.cwd() or "").lower()
            name = proc.name() or ""
        except Exception:
            continue
        if not (cwd == root or cwd.startswith(root + os.sep)):
            continue
        seen.add(port)
        url = f"http://127.0.0.1:{port}/"
        out.append({"url": url, "port": port, "proc": name, "title": _title_of(url)})
    # A directory listing is a working server that is not an app. Rank it last, so "the game"
    # wins over "the folder the game is in" even when the folder got the lower port. This is the
    # difference between showing the servers and picking the right one.
    def _rank(d: dict):
        t = (d.get("title") or "").lower()
        return (1 if (not t or t.startswith("directory listing")) else 0, d["port"])

    out.sort(key=_rank)
    return out


def running_url(project: Path) -> str:
    """The URL of a dev server already serving this project, or "".

    Covers one the Studio started AND one the user started in their own terminal.
    """
    key = str(project.resolve()).lower()
    with _lock:
        rec = _running.get(key)
        if rec and rec["proc"].poll() is None and rec.get("url"):
            return str(rec["url"])
    servers = running_servers(project)
    return servers[0]["url"] if servers else ""


def describe(project: Path) -> dict:
    """What is live now and what could be started — for the localhost menu."""
    servers = running_servers(project)
    url = running_url(project)
    script = script_for(project) or ""
    if not (url or script or servers):
        return {}
    return {"url": url, "script": script, "servers": servers}


def start(project: Path, timeout: float = 60.0) -> dict:
    """Start the project's dev server and return once it prints its URL.

    Returns {"ok", "url", "script", "error", "log"}. Never raises — a project that cannot start
    (no node_modules, port taken by --strictPort) must degrade to the static build, not break
    the button."""
    project = Path(project).resolve()
    key = str(project).lower()
    live = running_url(project)
    if live:
        return {"ok": True, "url": live, "script": "", "reused": True}
    script = script_for(project)
    if not script:
        return {"ok": False, "error": f"{project.name} has no dev script in package.json"}
    if not (project / "node_modules").exists():
        return {"ok": False, "error": f"{project.name} has no node_modules — run `npm install` there first"}

    npm = "npm.cmd" if os.name == "nt" else "npm"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    try:
        proc = subprocess.Popen([npm, "run", script], cwd=str(project),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as e:
        return {"ok": False, "error": f"could not run `npm run {script}`: {e}"}

    log: list[str] = []
    found: dict[str, str] = {}

    def pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if len(log) < 200:
                log.append(line)
            if "url" not in found:
                m = _URL_RE.search(raw)
                if m:
                    found["url"] = m.group(0).decode("utf-8", "replace").rstrip("/") + "/"

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "url" in found:
            with _lock:
                _running[key] = {"proc": proc, "url": found["url"], "started": time.time(), "log": log}
            return {"ok": True, "url": found["url"], "script": script, "log": log[-12:]}
        if proc.poll() is not None:
            return {"ok": False, "error": f"`npm run {script}` exited immediately",
                    "script": script, "log": log[-25:]}
        time.sleep(0.25)
    # still alive but silent — leave it running, the port may appear shortly
    with _lock:
        _running[key] = {"proc": proc, "url": "", "started": time.time(), "log": log}
    late = running_url(project)
    if late:
        return {"ok": True, "url": late, "script": script, "log": log[-12:]}
    return {"ok": False, "error": f"`npm run {script}` did not report a URL within {int(timeout)}s",
            "script": script, "log": log[-25:]}


def _kill_tree(proc) -> bool:
    """Our dev server and everything it started, leaf first. By PID only, never by name.

    On Windows a started dev server is a TREE: cmd.exe (npm.cmd) -> node npm-cli -> cmd.exe ->
    node serve.mjs. Killing the top cmd.exe alone left the node that holds the port running, so
    the port stayed taken and the next start moved to port+1 (measured on a new game)."""
    pid = int(getattr(proc, "pid", 0) or 0)
    if pid:
        try:
            import psutil
            kids = psutil.Process(pid).children(recursive=True)
            for k in reversed(kids):
                try:
                    k.kill()
                except Exception:
                    pass
            psutil.wait_procs(kids, timeout=3)
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        return False
    return True


def stop(project: Path) -> bool:
    key = str(Path(project).resolve()).lower()
    with _lock:
        rec = _running.pop(key, None)
    if not rec:
        return False
    return _kill_tree(rec["proc"])


def stop_all() -> int:
    with _lock:
        recs = list(_running.values())
        _running.clear()
    n = 0
    for rec in recs:
        if _kill_tree(rec["proc"]):
            n += 1
    return n
