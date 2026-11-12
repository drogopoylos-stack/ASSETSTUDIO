"""THE CACHE GUARD: stop paying to write a long conversation again for no reason.

A Claude session's prompt cache lives ONE HOUR after its last request (the CLI writes it with
`ttl: 1h`). While it lives, every request reads the conversation at $0.20 per million tokens on
Opus 5.5. When it is lost, the next request writes ALL of it again at $8 per million. On a session
of 800k tokens that is about $6.50 for one request that did nothing new.

Measured on the KAPOW session, 2026-11-05..12, the cache was thrown away in three ways:

1. EXPIRY: 16 times. The session sat idle past the hour and the next turn wrote 300k-900k tokens
   again, about $84 in the week. The CLI has the answer built in: its `SleepCompactor` compacts a
   large idle session while its cache is still warm. A compaction READS the cache (it sends the
   same messages as `cacheSafeParams` with `skipCacheWrite: true`), so it costs the read plus a
   summary, and the turn after the hour writes 30-50k tokens instead of 800k. But the CLI only arms
   it on a cloud host (`isSleepCompactionHost` = CLAUDE_CODE_SLEEP_COMPACT && CLAUDE_CODE_REMOTE),
   and CLAUDE_CODE_REMOTE changes far more than this. So the Studio does the same job itself:
   `decide` runs on the reaper's minute tick, with the CLI's own numbers (fire at 0.9 of the
   lifetime, not below 150k tokens), and `hold_reap` keeps such a process alive until it has.
2. A NEW CLI BUILD: once, $4.12 on 2026-11-12 14:55. The Studio stops an idle process after 15
   minutes and resumes it on the next message. On the SAME build a resume keeps the cache (checked
   on about 60 resumes inside the hour: all of them read it). On a NEW build the request prefix
   moves and everything is written again. `warm_binary` resumes a warm, large session on the build
   it started on; the CLI keeps its old builds in ~/.local/share/claude/versions, and one runs
   from there as it is (checked: `versions/2.1.314 --version` answers `2.1.314`).
3. OLD SCREENSHOTS STRIPPED: 8 times. See read_shrink.py.

This module holds the per-project facts the guard needs across a backend restart (the last
request, the context size, the cache lifetime, the CLI build) and the two decisions as pure
functions, so the tests can drive them with a clock.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

from . import fsutil
from .config import DATA_DIR, settings

STORE = DATA_DIR / "cache_guard.json"
TTL_1H = 3600.0
# The compaction's own first request must land while the cache is warm, so it is never STARTED
# later than this before the expiry. The reaper ticks once a minute.
MARGIN_S = 240.0
# Below this a build switch costs too little to be worth holding an old build for: 50k tokens
# written again is $0.40 on Opus 5.5.
HOLD_MIN_TOKENS = 50_000
_SAVE_EVERY = 20.0

_lock = threading.Lock()
_mem: Optional[dict] = None
_saved_at = [0.0]
_dirty = [False]


def enabled() -> bool:
    return bool(settings.get("cc_cache_guard", True))


def min_tokens() -> int:
    try:
        v = int(settings.get("cc_cache_guard_min_tokens", 150000) or 150000)
    except (TypeError, ValueError):
        v = 150000
    return max(20_000, v)


def after_s() -> float:
    try:
        v = float(settings.get("cc_cache_guard_after_s", 3000) or 3000)
    except (TypeError, ValueError):
        v = 3000.0
    return min(max(600.0, v), TTL_1H - MARGIN_S - 60.0)


# --- the per-project record ------------------------------------------------------------------

def _load() -> dict:
    global _mem
    if _mem is None:
        try:
            d = json.loads(STORE.read_text(encoding="utf-8"))
            _mem = d if isinstance(d, dict) else {}
        except Exception:
            _mem = {}
    return _mem


def _save(force: bool = False) -> None:
    if not _dirty[0]:
        return
    now = time.time()
    if not force and now - _saved_at[0] < _SAVE_EVERY:
        return
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_mem or {}), encoding="utf-8")
        fsutil.replace(tmp, STORE)
        _saved_at[0] = now
        _dirty[0] = False
    except OSError:
        pass


def note_request(project_id: str, session_id: str, version: str, ctx: int, ttl: float,
                 at: Optional[float] = None) -> None:
    """One main-thread API request: the time the cache was last used, and how big it is."""
    if not project_id:
        return
    with _lock:
        rec = _load().setdefault(project_id, {})
        if session_id:
            rec["session_id"] = session_id
        if version:
            rec["version"] = version
        rec["last_api"] = float(at if at is not None else time.time())
        rec["ctx"] = int(ctx or 0)
        if ttl:
            rec["ttl"] = float(ttl)
        _dirty[0] = True
        _save()


def note_version(project_id: str, session_id: str, version: str) -> None:
    if not project_id or not version:
        return
    with _lock:
        rec = _load().setdefault(project_id, {})
        if session_id and rec.get("session_id") not in (None, "", session_id):
            # another conversation in the same folder: its cache facts are not this one's
            rec.clear()
        if session_id:
            rec["session_id"] = session_id
        rec["version"] = version
        _dirty[0] = True
        _save(force=True)


def note_compacted(project_id: str, at: Optional[float] = None) -> None:
    """After a compaction the context is small again, so nothing large is left to protect."""
    if not project_id:
        return
    with _lock:
        rec = _load().setdefault(project_id, {})
        rec["ctx"] = 0
        rec["compacted_at"] = float(at if at is not None else time.time())
        _dirty[0] = True
        _save(force=True)


def note_guard(project_id: str, at: float) -> None:
    if not project_id:
        return
    with _lock:
        rec = _load().setdefault(project_id, {})
        rec["guard_at"] = float(at)
        _dirty[0] = True
        _save(force=True)


def record(project_id: str) -> dict:
    with _lock:
        return dict(_load().get(project_id) or {})


def flush() -> None:
    with _lock:
        _save(force=True)


# --- the two decisions -----------------------------------------------------------------------

def decide(*, enabled: bool, alt: bool, alive: bool, busy: bool, ctx: int, ttl: float,
           last_api: float, guard_at: float, now: float, min_tok: int, after: float,
           background: Union[bool, Callable[[], bool]] = False) -> str:
    """'compact' when a /compact must be sent NOW; otherwise the one word that says why not.

    The order is cheapest first; `background` may be a callable because asking whether an agent
    is still owed a result reads files, and most ticks are settled before that question."""
    if not enabled:
        return "off"
    if alt:
        return "other_engine"          # Kimi / Qwen: another vendor's cache, other rules
    if not alive:
        return "gone"
    if last_api <= 0:
        return "no_request_seen"
    if ttl < TTL_1H:
        return "short_ttl"             # a 5-minute cache: compacting every 4 minutes is worse
    if ctx < min_tok:
        return "small"
    if guard_at >= last_api:
        return "done"                  # once per quiet spell; the next request re-arms it
    if now < last_api + after:
        return "early"
    if now > last_api + ttl - MARGIN_S:
        return "too_late"              # the cache is cold or about to be: compacting buys nothing
    if busy:
        return "busy"
    bg = background() if callable(background) else background
    if bg:
        return "background"            # /compact would interrupt an agent still owed a result
    return "compact"


def hold_reap(*, enabled: bool, alt: bool, ctx: int, ttl: float, last_api: float,
              guard_at: float, now: float, min_tok: int) -> bool:
    """Keep an idle process alive while the guard still has a compaction to make.

    The reaper stops a quiet session after 15 minutes. Its cache would live 45 more, and the
    guard needs the process to send /compact into. Afterwards, or once the cache is cold, the
    usual reap applies."""
    if not enabled or alt or ttl < TTL_1H or ctx < min_tok or last_api <= 0:
        return False
    if guard_at >= last_api:
        return False
    return now < last_api + ttl - MARGIN_S


# --- the CLI build a warm session resumes on --------------------------------------------------

def versions_dir() -> Path:
    return Path.home() / ".local" / "share" / "claude" / "versions"


def _is_native(exe: str) -> bool:
    """The copy the native installer keeps in ~/.local/bin. A pinned path or the editor's binary
    is somebody's deliberate choice and is never swapped."""
    try:
        p = Path(exe).resolve()
        home_bin = (Path.home() / ".local" / "bin").resolve()
    except OSError:
        return False
    return os.path.normcase(str(p.parent)) == os.path.normcase(str(home_bin)) and \
        p.stem.lower() == "claude"


_ver_cache: dict[tuple, str] = {}
_VER_RE = re.compile(r"(\d+\.\d+\.\d+)")


def binary_version(exe: str) -> str:
    """`<exe> --version`, remembered for as long as the file on disk is the same file."""
    try:
        st = os.stat(exe)
    except OSError:
        return ""
    key = (os.path.normcase(exe), st.st_mtime_ns, st.st_size)
    if key in _ver_cache:
        return _ver_cache[key]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                           creationflags=flags)
        m = _VER_RE.search((r.stdout or "") + (r.stderr or ""))
        v = m.group(1) if m else ""
    except Exception:
        v = ""
    if v:
        _ver_cache[key] = v
    return v


def warm_binary(project_id: str, session_id: str, exe: str,
                now: Optional[float] = None) -> tuple[str, str]:
    """(the binary to launch, why) for a resume of `session_id`.

    The installed build, unless ALL of these hold: the guard is on; the last process of this very
    conversation ran another build; its cache is still warm; the conversation is large enough for
    a full write to matter; the binary is the native install; and the old build is still on disk.
    Then the old build, so the resume reads the cache instead of writing it. The new build arrives
    with the next cold start or after the next compaction, when it costs next to nothing."""
    if not enabled() or not exe or not session_id:
        return exe, ""
    now = time.time() if now is None else now
    rec = record(project_id)
    if rec.get("session_id") != session_id:
        return exe, ""
    old = str(rec.get("version") or "")
    ttl = float(rec.get("ttl") or 0)
    last = float(rec.get("last_api") or 0)
    ctx = int(rec.get("ctx") or 0)
    if not old or ttl < TTL_1H or last <= 0 or now > last + ttl - 60 or ctx < HOLD_MIN_TOKENS:
        return exe, ""
    if not _is_native(exe):
        return exe, ""
    cur = binary_version(exe)
    if not cur or cur == old:
        return exe, ""
    cand = versions_dir() / old
    if not cand.is_file():
        return exe, "build %s is no longer on disk; resuming on %s (one full cache write)" % (old, cur)
    mins = max(0, int((last + ttl - now) // 60))
    return str(cand), ("resumed on %s, the build this conversation started on: its cache "
                       "(%d tokens) is warm for %d more minutes, and %s would write it again"
                       % (old, ctx, mins, cur))


def status(lives: list[dict], now: Optional[float] = None) -> dict:
    """What the guard knows and will do, per live session, for the API and the feature check."""
    now = time.time() if now is None else now
    out = []
    for lv in lives:
        last = float(lv.get("last_api") or 0)
        ttl = float(lv.get("ttl") or 0)
        out.append({
            "project": lv.get("project"),
            "ctx_tokens": int(lv.get("ctx") or 0),
            "cache_ttl_s": ttl,
            "idle_s": round(now - last, 1) if last else None,
            "expires_in_s": round(last + ttl - now, 1) if (last and ttl) else None,
            "guard_at": lv.get("guard_at") or 0,
            "cli_version": lv.get("version") or "",
            "next": lv.get("next") or "",
        })
    return {"enabled": enabled(), "min_tokens": min_tokens(), "after_s": after_s(),
            "image_shrink": bool(settings.get("cc_image_shrink", True)),
            "compact_window": int(settings.get("cc_compact_window", 0) or 0),
            "sessions": out}
