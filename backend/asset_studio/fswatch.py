"""Live filesystem watcher for the workspace explorer.

Watches the workspace root(s) the UI is currently looking at and bumps a global
monotonic ``version`` whenever files change on disk (Claude writing files, an
external editor, a build step…). The frontend cheaply polls ``changes_since`` and,
when the version moves, refreshes the file tree and reloads open editors — so edits
show up near-instantly instead of waiting on a slow per-file poll.

Uses ``watchfiles`` (Rust-backed, native OS notifications) which is already a
dependency of the dev server, so there's nothing new to install.
"""
from __future__ import annotations

import threading
import time
from collections import deque
import pathlib
from pathlib import Path
from typing import Optional

_lock = threading.RLock()
_version = 0
_recent: "deque[tuple[int, str]]" = deque(maxlen=4000)   # (version, changed-path)
_thread: Optional[threading.Thread] = None
_stop: Optional[threading.Event] = None
_watched: tuple = ()                    # the root set the live thread is watching
_requested: dict[str, float] = {}       # root path -> last time the UI asked about it
_TTL = 45.0                             # drop a root nothing asked about this long ago
# A rename needs the OS directory handle released. Something else now re-registers active
# roots in the background, so pause() has to hold them off or it would re-grab the handle
# mid-rename — the exact failure pause() exists to prevent.
_paused: dict[str, float] = {}          # root (normalised) -> do not re-watch before this time
_PAUSE_HOLD = 25.0

# noisy generated dirs — never worth watching (and they'd flood the change feed)
_IGNORE = {".git", "node_modules", "dist", "build", "out", "target", ".venv", "venv",
           "__pycache__", ".next", ".turbo", ".cache", ".idea", ".pytest_cache",
           ".mypy_cache", ".gradle", "bin", "obj", ".parcel-cache"}


def _keep(_change, path: str) -> bool:
    """watchfiles filter: ignore changes inside generated/noise directories."""
    segs = path.replace("\\", "/").split("/")
    return not any(s in _IGNORE for s in segs)


# Extensions whose change means the CODE GRAPH is now out of date. Editing a .png or a .md does
# not move a symbol, so those must not trigger a rebuild.
_CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".go",
             ".rs", ".java", ".kt", ".cs", ".cpp", ".hpp", ".c", ".h", ".rb", ".php", ".swift",
             ".gd", ".lua", ".sh", ".ps1"}


def _graph_touch(paths: list[str]) -> None:
    """Keep each workspace's code graph current as the code changes — with nobody in the loop.

    The graph used to be rebuilt only when a workspace was opened or a chat message was sent, so
    an hour of hand-editing left it describing the past, and a project never opened in the Studio
    had none at all. Editing code IS the signal, so the watcher that already sees every save now
    schedules the rebuild. `refresh()` is debounced and rate-limited, so a burst of saves costs
    one build."""
    try:
        from .config import settings
        if not settings.get("cc_graphify", True):
            return
        from . import graphify_index
        roots: dict[str, list[str]] = {}
        with _lock:
            watched = list(_watched)
        for p in paths:
            if Path(p).suffix.lower() not in _CODE_EXT:
                continue
            low = p.replace("\\", "/").lower()
            for r in watched:
                if low.startswith(r.replace("\\", "/").lower().rstrip("/") + "/"):
                    roots.setdefault(r, []).append(p)
                    break
        for r, changed in roots.items():
            # Record what moved BEFORE asking for a rebuild. Until that build lands, every answer
            # about this project carries the list of files it does not know about yet.
            graphify_index.note_change(r, changed)
            graphify_index.refresh(r)
    except Exception:
        pass


def _run(paths: list[str], stop: threading.Event) -> None:
    global _version
    try:
        from watchfiles import watch
        for batch in watch(*paths, stop_event=stop, watch_filter=_keep,
                           debounce=250, rust_timeout=1000, yield_on_timeout=False):
            with _lock:
                _version += 1
                v = _version
                for _ct, p in batch:
                    _recent.append((v, p))
            _graph_touch([p for _ct, p in batch])
    except Exception:
        pass


def _restart_locked() -> None:
    """(Re)spawn the watcher thread iff the live root set changed. Caller holds _lock."""
    global _thread, _stop, _watched
    now = time.time()
    roots = sorted({r for r, t in _requested.items()
                    if now - t < _TTL and r and Path(r).exists()})
    key = tuple(roots)
    if key == _watched and _thread is not None and _thread.is_alive():
        return
    if _stop is not None:
        _stop.set()
    _watched = key
    if not roots:
        _thread, _stop = None, None
        return
    _stop = threading.Event()
    _thread = threading.Thread(target=_run, args=(roots, _stop), daemon=True)
    _thread.start()


def _norm(p: str) -> str:
    """Compare paths by their RESOLVED form. Windows hands out both the short name
    (C:/Users/ADMINI~1/...) and the long one for the same folder, so a raw string compare let a
    paused root be re-registered under its other spelling — defeating the pause during a rename,
    which is the one thing it exists to prevent."""
    try:
        return str(pathlib.Path(p).resolve()).replace("\\", "/").rstrip("/").lower()
    except (OSError, ValueError):
        return (p or "").replace("\\", "/").rstrip("/").lower()


def ensure_watching(root: str) -> None:
    """Mark ``root`` as in use; (re)start the watcher to cover it. Ignored while the root is
    paused for a rename."""
    if not root:
        return
    with _lock:
        until = _paused.get(_norm(root), 0.0)
        if until and time.time() < until:
            return
        _requested[root] = time.time()
        _restart_locked()


def pause(root: str, timeout: float = 5.0) -> None:
    """Stop the watcher and forget ``root`` — it's about to be renamed/deleted, and the
    watcher holds an OS directory handle that makes os.rename fail on Windows. The next
    ensure_watching (the UI polls every second) restarts coverage automatically."""
    global _thread, _stop, _watched
    with _lock:
        _paused[_norm(root)] = time.time() + _PAUSE_HOLD
        target = _norm(root)
        for k in [k for k in _requested if _norm(k) == target]:
            _requested.pop(k, None)
        th, st = _thread, _stop
        _thread, _stop, _watched = None, None, ()
        if st is not None:
            st.set()
    if th is not None:
        th.join(timeout=timeout)   # join OUTSIDE the lock — the watch loop takes it to bump versions


def changes_since(since: int) -> dict:
    """Current version + the distinct paths that changed after ``since``.

    ``since < 0`` (or already current) returns no paths — used by the client to
    prime its baseline on first poll without triggering a spurious refresh."""
    with _lock:
        if since < 0 or since >= _version:
            return {"version": _version, "paths": []}
        seen: set[str] = set()
        out: list[str] = []
        for v, p in _recent:
            if v > since and p not in seen:
                seen.add(p)
                out.append(p)
        return {"version": _version, "paths": out}
