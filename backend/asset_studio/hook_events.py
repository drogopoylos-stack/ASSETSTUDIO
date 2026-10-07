"""What the session hook recorded, read back for the meter and the phases panel.

``session_hook.py`` appends one JSON line per event to ``DATA_DIR/hooks/<project>.jsonl``. This
side reads it. Two questions are answered here, and neither could be answered from the transcript
in time:

``compact_state`` — is a compaction running, and if one just finished, how big is the context it
left behind? ``PostCompact`` carries the summary itself, so the post-compact fill is measured
rather than assumed.

``task_runs`` — when did each phase run actually start? ``TaskCreated`` fires once and never
again, so its timestamps are immutable. The store files under ``<home>/tasks/<session>/`` are
rewritten whenever a phase changes status, which is why grouping runs off THEIR timestamps put
eleven days of phases in one list.

The file is trimmed here rather than in the hook: the hook must stay a fast, append-only write
that can never fail a turn.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

HOOK_DIR = DATA_DIR / "hooks"
# Two creations further apart than this belong to different runs. One assistant turn writes its
# whole phase list in a few seconds, so this is generous by an order of magnitude and still far
# tighter than the twenty-minute rule the file timestamps forced.
RUN_GAP = 90.0
# A compaction that started and never reported finishing is treated as over after this, so a
# crashed or cancelled compact cannot leave the meter saying "compacting…" for ever.
COMPACT_STALE = 20 * 60.0
_MAX_BYTES = 512 * 1024
_TRIM_TO = 256 * 1024
# Roughly four characters to the token. Only ever applied to the compaction summary, where being
# 10% out is invisible next to the 25,000-token constant it replaces.
_CHARS_PER_TOKEN = 4.0

_cache: dict[str, tuple[float, float, list[dict]]] = {}   # project -> (mtime, size, rows)


def file_for(project_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", project_id or "unknown")
    return HOOK_DIR / f"{safe}.jsonl"


def _trim(path: Path) -> None:
    """Keep the tail. Called only when the file has actually grown past the cap."""
    try:
        data = path.read_bytes()
        if len(data) <= _MAX_BYTES:
            return
        cut = data[-_TRIM_TO:]
        nl = cut.find(b"\n")           # never leave a half line at the top
        path.write_bytes(cut[nl + 1:] if nl >= 0 else cut)
    except OSError:
        pass


def events(project_id: str) -> list[dict]:
    """Every recorded event for a project, oldest first. Re-read only when the file changed."""
    path = file_for(project_id)
    try:
        st = path.stat()
    except OSError:
        return []
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    if st.st_size > _MAX_BYTES:
        _trim(path)
        try:
            st = path.stat()
        except OSError:
            return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue          # a line caught mid-append; the next read gets it whole
                if isinstance(o, dict) and o.get("event"):
                    rows.append(o)
    except OSError:
        return []
    _cache[key] = (st.st_mtime, st.st_size, rows)
    return rows


def compact_state(project_id: str, session: str = "") -> dict:
    """Where the latest compaction got to, and what it left behind.

    Returns ``{}`` when this project has never compacted under the hook. Otherwise:
    ``running`` while a PreCompact has no PostCompact after it, ``summary_tokens`` once one has
    landed — the size of the text that became the new context, which is what the meter needs
    during the gap before the next real turn reports usage.
    """
    pre: Optional[dict] = None
    post: Optional[dict] = None
    for o in events(project_id):
        if o.get("agent"):
            continue                  # a subagent's own compaction is not this session's meter
        if session and o.get("session") and o["session"] != session:
            continue
        if o["event"] == "PreCompact":
            pre, post = o, None
        elif o["event"] == "PostCompact":
            post = o
    if not pre and not post:
        return {}
    running = bool(pre and not post) and (time.time() - float(pre.get("at", 0) or 0)) < COMPACT_STALE
    out = {"running": running,
           "trigger": str((pre or post or {}).get("trigger") or ""),
           "started": float((pre or {}).get("at", 0) or 0),
           "ended": float((post or {}).get("at", 0) or 0)}
    if post:
        out["summary_chars"] = int(post.get("summary_chars") or 0)
        out["summary"] = str(post.get("summary") or "")
        out["summary_tokens"] = int(round(out["summary_chars"] / _CHARS_PER_TOKEN))
    return out


def task_runs(project_id: str, session: str = "") -> list[dict]:
    """The phase runs, oldest first, from creation events that can never be rewritten.

    Each run is ``{started, ended, ids, subjects}``. ``ids`` is what lets the panel decide which
    of the files under ``<home>/tasks/<session>/`` belong to the newest run — the question the
    file timestamps answer wrongly.
    """
    made = [o for o in events(project_id)
            if o["event"] == "TaskCreated" and not o.get("agent")
            and (not session or not o.get("session") or o["session"] == session)]
    done = {o.get("task_id"): float(o.get("at", 0) or 0) for o in events(project_id)
            if o["event"] == "TaskCompleted" and not o.get("agent")}
    runs: list[dict] = []
    for o in made:
        at = float(o.get("at", 0) or 0)
        if not runs or at - runs[-1]["last"] > RUN_GAP:
            runs.append({"started": at, "last": at, "ids": [], "subjects": []})
        runs[-1]["last"] = at
        runs[-1]["ids"].append(str(o.get("task_id") or ""))
        runs[-1]["subjects"].append(str(o.get("subject") or ""))
    for r in runs:
        finished = [done[i] for i in r["ids"] if i in done]
        r["ended"] = max(finished) if len(finished) == len(r["ids"]) and finished else 0.0
        r["count"] = len(r["ids"])
    return runs


def instructions(project_id: str, session: str = "") -> dict:
    """Every instruction file the newest session loaded, and what it weighs.

    Standing instructions are the one part of a context window nobody can see. A CLAUDE.md
    imports another, a plugin adds a third, and a skill's frontmatter is read before any of them
    — all of it billed on every single request of the session, and none of it visible anywhere.
    ``InstructionsLoaded`` names each file and says why it was taken on; the size is read off the
    file here, so the hook stays a short write.

    Only the newest session's loads are reported: an older session's files were loaded into a
    context that no longer exists, and adding them would inflate the total.
    """
    rows = [o for o in events(project_id) if o["event"] == "InstructionsLoaded" and not o.get("agent")]
    if session:
        rows = [o for o in rows if not o.get("session") or o["session"] == session]
    elif rows:
        newest = rows[-1].get("session") or ""
        rows = [o for o in rows if (o.get("session") or "") == newest]
    files: dict[str, dict] = {}
    for o in rows:
        p = str(o.get("file_path") or "")
        if not p or p in files:
            continue
        try:
            chars = os.path.getsize(p)
        except OSError:
            chars = 0
        files[p] = {
            "path": p,
            "name": os.path.basename(p) or p,
            "kind": str(o.get("memory_type") or ""),
            "reason": str(o.get("load_reason") or ""),
            # Imported by another file rather than loaded in its own right — worth showing,
            # because that is how a context grows without anyone choosing it.
            "parent": os.path.basename(str(o.get("parent") or "")),
            "tokens": int(round(chars / _CHARS_PER_TOKEN)),
        }
    out = sorted(files.values(), key=lambda f: -f["tokens"])
    return {"files": out, "tokens": sum(f["tokens"] for f in out), "count": len(out)}


def latest_run_ids(project_id: str, session: str = "") -> set[str]:
    """Task ids belonging to the newest run, or an empty set when nothing was recorded — in
    which case the caller keeps its own heuristic, so a session that predates the hook is
    grouped exactly as it was before."""
    runs = task_runs(project_id, session)
    return set(runs[-1]["ids"]) if runs else set()
