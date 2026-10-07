"""One-time repair of the two cost ledgers, after the streaming running-total bug.

THE BUG IT UNDOES. The Studio drives one long-lived `claude -p --input-format stream-json`
process per project, and in streaming-input mode the CLI's `result` event carries the RUNNING
TOTAL FOR THAT WHOLE PROCESS. `cc_session` banked that figure per result, so every finished turn
re-charged every turn before it. Measured on this machine: $379.73 banked against the $162.51 the
CLI's own rate card had actually reported — 2.34x over. Fourteen of the rows were not turns at
all: the CLI emits a `result` per background agent too (`num_turns: 0`, no tokens), and those
carried the previous turn's cost a second time.

`cc_session` now takes the delta against each process's high-water mark and skips the
notifications, so NEW rows are right. This fixes the rows already on disk, because otherwise the
Dashboard would keep showing a number that is wrong by a factor of two and a half — and a
"we fixed the accounting" that leaves the wrong figure on screen is not a fix.

HOW THE REPAIR SEGMENTS. A CLI call's running total only ever goes UP, so a value LOWER than the
one before it marks the start of a new process. Rows that did no work are dropped rather than
segmented — they are exactly the ones that break that rule, because they report 0 in the middle
of a live call. The method is the same one an outside audit had to reconstruct by hand; what it
needed and did not have was a per-row call id, so every repaired row now carries one and this can
never have to be guessed at again.

SAFE BY CONSTRUCTION. Both files are copied aside before a byte is written, the whole thing runs
once and records that it did, and a tree that already carries call ids is left alone. Amounts are
the CLI's own list-price estimates either way — on a subscription none of this is what you pay.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from . import fsutil
from .config import DATA_DIR

TURNS = DATA_DIR / "turns.jsonl"
SPEND = DATA_DIR / "spend.json"
MARKER = DATA_DIR / ".ledger-repaired"
_KEEP_DAYS = 60          # spend.py's window; the repair rebuilds that file, so it honours it


def _backup(p: Path) -> str:
    if not p.exists():
        return ""
    dst = p.with_suffix(p.suffix + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        dst.write_bytes(p.read_bytes())
        return dst.name
    except OSError:
        return ""


def _read_turns() -> list[dict]:
    if not TURNS.exists():
        return []
    rows = []
    for line in TURNS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def repair() -> dict:
    """Rebuild both ledgers from the turn rows. Idempotent; returns a small report."""
    if MARKER.exists():
        return {"ok": True, "already": True}

    rows = _read_turns()
    if not rows:
        _mark({"rows": 0, "note": "no turn ledger to repair"})
        return {"ok": True, "rows": 0}

    # A tree that already has call ids has been repaired (or was written after the fix) — doing
    # this twice would treat an already-correct delta as a running total and lose the lot.
    if all("call" in r for r in rows):
        _mark({"rows": len(rows), "note": "already carried call ids"})
        return {"ok": True, "rows": len(rows), "already": True}

    call = 1
    high = 0.0
    fixed: list[dict] = []
    dropped = 0
    # The original, over-counted total — taken BEFORE any row is touched, so the report can say
    # how much of the old figure was double-counting.
    before = round(sum(float(r.get("cost") or 0) for r in rows), 2)
    for r in rows:
        try:
            cost = float(r.get("cost") or 0)
            tokens = int(r.get("tokens") or 0)
        except (TypeError, ValueError):
            cost, tokens = 0.0, 0
        if tokens <= 0 and cost <= 0:          # a notification, not a turn
            dropped += 1
            continue
        if cost < high:                        # lower than before => a new CLI process
            call += 1
            high = 0.0
        r["cost"] = round(max(0.0, cost - high), 6)
        r["call"] = call
        high = max(high, cost)
        fixed.append(r)

    after = round(sum(float(r["cost"]) for r in fixed), 2)

    turns_backup = _backup(TURNS)
    spend_backup = _backup(SPEND)

    try:
        tmp = TURNS.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(r) for r in fixed) + "\n", encoding="utf-8")
        fsutil.replace(tmp, TURNS)
    except OSError as e:
        return {"ok": False, "error": f"could not rewrite turns.jsonl: {e}"}

    # spend.json is an aggregate of exactly these rows, so rebuild it from them rather than
    # leaving a total that disagrees with the ledger underneath it.
    projects: dict[str, dict] = {}
    days: dict[str, float] = {}
    for r in fixed:
        pid = str(r.get("project") or "")
        if not pid:
            continue
        p = projects.setdefault(pid, {"total": 0.0, "turns": 0, "tokens": 0,
                                      "by_model": {}, "last": 0.0})
        cost = float(r["cost"])
        p["total"] = round(p["total"] + cost, 6)
        p["turns"] += 1
        p["tokens"] += int(r.get("tokens") or 0)
        m = str(r.get("model") or "unknown").split("[")[0]
        p["by_model"][m] = round(p["by_model"].get(m, 0.0) + cost, 6)
        p["last"] = max(float(p["last"] or 0), float(r.get("ts") or 0))
        day = datetime.fromtimestamp(float(r.get("ts") or time.time())).strftime("%Y-%m-%d")
        days[day] = round(days.get(day, 0.0) + cost, 6)
    for k in sorted(days)[:-_KEEP_DAYS] if len(days) > _KEEP_DAYS else []:
        days.pop(k, None)

    try:
        tmp = SPEND.with_suffix(".tmp")
        tmp.write_text(json.dumps({"projects": projects, "days": days}), encoding="utf-8")
        fsutil.replace(tmp, SPEND)
    except OSError as e:
        return {"ok": False, "error": f"could not rewrite spend.json: {e}"}

    report = {"ok": True, "rows": len(fixed), "dropped": dropped, "calls": call,
              "before": before, "after": after,
              "saved": round(before - after, 2),
              "turns_backup": turns_backup, "spend_backup": spend_backup}
    _mark(report)
    return report


def _mark(report: dict) -> None:
    try:
        MARKER.write_text(json.dumps({"at": time.time(), **report}, indent=1), encoding="utf-8")
    except OSError:
        pass
