"""Tiny on-demand static file server for "Run" on .html files.

ES-module HTML (import maps, `import './foo.js'`) does NOT work from a double-clicked
``file://`` page (browser CORS blocks the module fetch). The fix the user hits every time is
"serve the folder over http and open localhost". This module automates exactly that: it starts a
throwaway static server rooted at a file's folder, bound to 127.0.0.1 on an ephemeral port, reuses
one server per folder, and reaps idle ones. Self-contained ``file://``-safe pages work through it
too, so "always serve html" is the safe universal choice.

Never blocks the event loop: each server runs in its own daemon thread; callers (a sync FastAPI
route, i.e. off the loop in a threadpool) only touch the fast start/lookup path.
"""
from __future__ import annotations

import functools
import http.server
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

_REAP_IDLE = 30 * 60          # stop a folder's server after 30 min unused
_servers: dict[str, dict] = {}   # abs folder -> {httpd, thread, port, last}
_lock = threading.Lock()
_reaper: threading.Thread | None = None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a):   # silence per-request stderr spam
        pass

    def log_request(self, code="-", size="-"):
        # EVERY REQUEST IS USE. `last` moved only when serve_dir() was called, so a page that stayed
        # open and kept fetching - the forge bench an agent works on for an hour - lost its server
        # 30 minutes after it was STARTED, and the next call built on a dead origin.
        _touch(getattr(self, "directory", ""))


def _touch(folder: str) -> None:
    rec = _servers.get(str(folder or ""))
    if rec is not None:
        rec["last"] = time.time()


def _reap_once(now: float | None = None) -> int:
    """Stop every server unused for `_REAP_IDLE`; how many were stopped."""
    now = time.time() if now is None else now
    stopped = 0
    with _lock:
        for folder, rec in list(_servers.items()):
            if now - rec["last"] > _REAP_IDLE:
                try:
                    rec["httpd"].shutdown()
                except Exception:
                    pass
                # AND CLOSE THE SOCKET. shutdown() only ends the serve loop: the socket went on
                # listening, so a request sat in the backlog and waited forever - a 45 s hang in
                # the forge. A closed port refuses the connect (Windows takes 2 s to say so).
                try:
                    rec["httpd"].server_close()
                except Exception:
                    pass
                _servers.pop(folder, None)
                stopped += 1
    return stopped


def _ensure_reaper() -> None:
    global _reaper
    if _reaper is not None:
        return

    def loop() -> None:
        while True:
            time.sleep(60)
            _reap_once()

    _reaper = threading.Thread(target=loop, name="preview-reaper", daemon=True)
    _reaper.start()


def serve_dir(folder: str) -> int:
    """Start (or reuse) a static server rooted at ``folder``; return its localhost port."""
    folder = str(Path(folder).resolve())
    if not os.path.isdir(folder):
        raise NotADirectoryError(folder)
    with _lock:
        rec = _servers.get(folder)
        if rec is not None:
            rec["last"] = time.time()
            return rec["port"]
        handler = functools.partial(_QuietHandler, directory=folder)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        httpd.daemon_threads = True
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, name=f"preview:{port}", daemon=True)
        thread.start()
        _servers[folder] = {"httpd": httpd, "thread": thread, "port": port, "last": time.time()}
        _ensure_reaper()
        return port


def serve_file(path) -> str:
    """Serve a file's containing folder and return the http URL that opens the file."""
    p = Path(path).resolve()
    port = serve_dir(str(p.parent))
    return f"http://127.0.0.1:{port}/{quote(p.name)}"


def status() -> dict:
    with _lock:
        return {"servers": [{"folder": f, "port": r["port"]} for f, r in _servers.items()]}
