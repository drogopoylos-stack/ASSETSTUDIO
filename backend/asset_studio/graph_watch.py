"""Keep the code graph current for every project being WORKED IN — not just the one on screen.

The filesystem watcher was kept alive by the Workspace tab polling for changes, so it only ever
covered the root you were looking at. Working on two or three projects at once meant the other
two went stale the moment you switched away: an agent running live in one project while you read
another was the normal case, and the busiest project was the one not being watched.

Being worked in is a backend fact, not a UI one:

  * a live agent session for that project — the strongest signal there is, and it covers
    "Claude Code is running in brainrot while I sit in this tab"
  * a transcript written within the last few minutes, which catches a project between turns

Those roots are re-registered on a short loop so the watcher's TTL never drops them, and the
graph rebuild that already fires on save then applies to all of them. Capped, because watching
forty projects would mean forty OS watchers and a rebuild storm to keep graphs current for work
that is not happening.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .config import settings

_TICK = 15.0          # comfortably inside fswatch's 45s TTL
_MAX_ROOTS = 8        # a person works on a few projects, not forty
_started = False


def active_roots() -> list[str]:
    """Absolute paths of the projects currently being worked in, best signal first."""
    roots: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        if not p:
            return
        try:
            rp = str(Path(p).resolve())
        except OSError:
            return
        k = rp.lower()
        if k in seen or not Path(rp).is_dir():
            return
        seen.add(k)
        roots.append(rp)

    # 1. a live agent process — this is the case the whole module exists for
    try:
        from . import cc_session
        for live in list(cc_session._live.values()):
            if getattr(live, "alive", False):
                sig = getattr(live, "sig", ())
                if len(sig) > 1:
                    add(str(sig[1]))
    except Exception:
        pass

    # 2. a project whose transcript moved recently (between turns, or another tool driving it)
    try:
        from . import mission
        # The light index, not the overview. `active` is the same test the overview applies — a
        # transcript touched within PROJECT_ACTIVE_WINDOW — computed here from the index's own
        # last_activity, so this watcher no longer starts a full scan on every tick.
        now = time.time()
        for p in mission.project_index():
            if p.get("path") and (now - float(p.get("last_activity") or 0)) < mission.PROJECT_ACTIVE_WINDOW:
                add(str(p["path"]))
    except Exception:
        pass

    return roots[:_MAX_ROOTS]


def _tick() -> None:
    if not settings.get("cc_graphify", True):
        return
    from . import fswatch
    for r in active_roots():
        fswatch.ensure_watching(r)      # no-op while a root is paused for a rename


def start() -> None:
    """Idempotent."""
    global _started
    if _started:
        return
    _started = True

    def loop() -> None:
        while True:
            time.sleep(_TICK)
            try:
                _tick()
            except Exception:           # a background keeper must never die
                pass

    threading.Thread(target=loop, daemon=True).start()
