"""What one finished turn actually cost — banked, so the feed can say it under the answer.

Every number here was already computed and then thrown away. `cc_session` builds a `turn` dict at
the moment the CLI reports `result` — tokens, generating seconds, wall seconds, model, effort,
messages, and the CLI's own price for the turn — hands it to `speed` and `spend`, and lets it go.
Neither of those is a per-turn record the feed can read back:

* ``speed`` DROPS a turn that is too small to be a useful speed sample (under a few hundred
  tokens, or under a second of generating). Most turns in a conversation are exactly that, and a
  summary bar that appears on some answers and not others reads as broken.
* ``spend`` accumulates. It knows the month, not the answer you are looking at.

So this is the third consumer, and the only one that keeps every turn. It is deliberately dumb:
append a line, trim when the file gets big, read it back by project.

**The cost figure already includes the subagents.** The CLI prices the whole turn on its own rate
card, and a Task the turn spawned is part of that turn. The subagent numbers the feed shows
beside it are a SLICE of this total, never an addition to it — see `spend.py`, which learned the
same lesson the expensive way.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from . import fsutil
from .config import DATA_DIR

STORE = DATA_DIR / "turns.jsonl"
_lock = threading.Lock()

# Big enough that a long day of work is all still there, small enough to read in one gulp.
_MAX_BYTES = 900_000
_KEEP = 4000

# The file is re-read only when it has actually changed. The feed polls, and a poll that parses a
# megabyte of JSON every 1.2 seconds is how a chat window starts costing more than the agent.
_cache: tuple = (0.0, 0, [])


def record(project_id: str, model: str, effort: str, tokens: int, gen_s: float, wall_s: float,
           messages: int = 0, cost: float = 0.0, compacting: bool = False,
           call: int = 0, basis: str = "list") -> dict:
    """Bank one finished turn. Every turn, however small — that is the whole point of this file.

    `cost` is the price of THIS TURN, which is not the number the CLI hands over. In streaming
    input mode the CLI reports a RUNNING TOTAL for the whole process, so `cc_session` takes the
    delta against that process's high-water mark and passes the result here. `call` is that
    process's id: without it a row cannot be attributed to the run that produced it, which is how
    a 2.34x over-count stayed invisible for so long.
    """
    now = time.time()
    row = {
        "ts": round(now, 1),                       # when the turn ENDED
        "started": round(now - max(0.0, wall_s), 1),
        "project": project_id,
        "model": (model or "").strip() or "unknown",
        "effort": (effort or "default").strip().lower(),
        "tokens": int(tokens),
        "gen_s": round(float(gen_s), 2),
        "wall_s": round(float(wall_s), 2),
        "messages": int(messages),
        "cost": round(float(cost), 6),
        "compacting": bool(compacting),
        "call": int(call),                         # which CLI process produced this row
        # WHERE THE MONEY CAME FROM: "list" (the rate card), "override" (a rate the user typed),
        # or "unknown" (no rate for this model — the tokens are real, the dollars are unmeasured).
        "basis": str(basis or "list"),
    }
    try:
        with _lock:
            STORE.parent.mkdir(parents=True, exist_ok=True)
            with open(STORE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            _trim_locked()
    except OSError:
        pass                                        # a summary bar is never worth an exception
    return row


def _trim_locked() -> None:
    try:
        if STORE.stat().st_size < _MAX_BYTES:
            return
        lines = STORE.read_text(encoding="utf-8", errors="replace").splitlines()[-_KEEP:]
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        fsutil.replace(tmp, STORE)
    except OSError:
        pass


def _all() -> list:
    global _cache
    try:
        st = STORE.stat()
    except OSError:
        return []
    if _cache[0] == st.st_mtime and _cache[1] == st.st_size:
        return _cache[2]
    rows = []
    try:
        for line in STORE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue                            # a half-written line is not a reason to fail
    except OSError:
        return []
    _cache = (st.st_mtime, st.st_size, rows)
    return rows


def tokens_since(seconds: float) -> int:
    """Output tokens banked in the last `seconds`. This is what the token-budget gauge reads.

    A rolling window computed from the turn ledger rather than a running counter, so it survives a
    restart and can never drift from the turns it claims to describe."""
    cutoff = time.time() - max(0.0, float(seconds or 0))
    return sum(int(r.get("tokens") or 0) for r in _all() if float(r.get("ts") or 0) >= cutoff)


def for_project(project_id: str, since: float = 0.0, limit: int = 400) -> list:
    """This project's finished turns, oldest first."""
    rows = [r for r in _all()
            if r.get("project") == project_id and float(r.get("ts") or 0) >= since]
    return rows[-limit:] if limit else rows


def at(project_id: str, start: float, end: float) -> Optional[dict]:
    """The turn that ENDED inside this window, if there is one.

    The feed knows where a turn begins — a user message — and where the next one begins. It does
    not know the CLI's own numbers. Joining on the END time is what marries the two, and the end
    is the reliable half: a turn's start can be minutes before its first visible event while the
    model thinks, but the `result` that ends it is stamped the moment the answer is complete.
    """
    best = None
    for r in for_project(project_id):
        ts = float(r.get("ts") or 0)
        if start <= ts <= end and (best is None or ts > float(best.get("ts") or 0)):
            best = r
    return best
