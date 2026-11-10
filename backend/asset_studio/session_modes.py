"""The approval mode of each project's agent, kept by the server.

The chat box used to keep the mode in the browser's localStorage only. A second device — a phone,
a laptop at home, any remote browser — had nothing stored, sent its fallback ("acceptEdits"), and
because a send whose mode differs from the running session's RESTARTS that session
(`cc_session._send_streaming`, `want_sig`), one message from a remote device quietly took a
full-access session down to "approve every command". Whoever was not at the desk to click
approve was stuck.

So the mode a person picks is saved here, per folder and per agent, and a send that names no mode
gets this one. The chat box on every device reads it when it opens, so all of them agree.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Optional

from .config import DATA_DIR

_FILE = DATA_DIR / "session_modes.json"
_LOCK = threading.Lock()
# Claude Code's modes and the other engines' own words for theirs (Codex "full", and so on).
_MODE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


def _key(project_id: str, agent: str) -> str:
    # One folder, two ids: the Workspace lower-cases the drive letter, the CLI does not.
    pid = re.sub(r"^([A-Za-z])--", lambda m: m.group(1).lower() + "--", project_id or "")
    return pid + "|" + (agent or "claude")


def _read() -> dict:
    try:
        d = json.loads(_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def get(project_id: str, agent: str = "claude") -> str:
    """The mode picked for this folder and agent, or "" when none was ever picked."""
    v = _read().get(_key(project_id, agent))
    return v if isinstance(v, str) and _MODE.match(v) else ""


def put(project_id: str, agent: str, mode: str) -> bool:
    if not project_id or not isinstance(mode, str) or not _MODE.match(mode):
        return False
    with _LOCK:
        d = _read()
        d[_key(project_id, agent)] = mode
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.part")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(_FILE)
    return True


def resolve(project_id: str, agent: str, mode: Optional[str], fallback: str = "acceptEdits") -> str:
    """The mode a send runs with: the one it names, else the one saved for this folder, else the
    Settings default, else `fallback`."""
    if mode:
        return mode
    saved = get(project_id, agent)
    if saved:
        return saved
    try:
        from .config import settings
        d = (settings.get("session_defaults") or {}).get("permission_mode")
        if isinstance(d, str) and _MODE.match(d) and (agent or "claude") == "claude":
            return d
    except Exception:
        pass
    return fallback
