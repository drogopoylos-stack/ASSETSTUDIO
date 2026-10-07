"""Subagents — what the Task/Agent tool actually did, not just what it reported.

A subagent is a black box in most tools: one call goes out, one paragraph comes back, and the
cost, the reasoning and every file it touched are gone. None of that is a limitation of the CLI.
It writes the whole thing down:

    ~/.claude/projects/<project>/<session>/subagents/agent-<agentId>.jsonl

Every entry there carries ``isSidechain: true`` and the ``agentId``, and the parent transcript's
``toolUseResult`` carries the SAME id alongside the summary the model was given:

    agentId, agentType, resolvedModel, status, prompt,
    totalDurationMs, totalTokens, totalToolUseCount,
    toolStats{readCount, searchCount, bashCount, editFileCount, linesAdded, linesRemoved,
              otherToolCount},
    usage{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, ...}

So the id is the join. This module reads both sides and hands the UI a subagent it can inspect:
its own timeline, its bill, and — because the transcript holds its real tool calls — the exact
files it edited. That last one is what makes a collision between two agents detectable rather
than a matter of guesswork.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from . import agent_usage
from . import engines
from . import mission
from . import perf
from . import pricing

# A subagent transcript written within this many seconds is still being written to.
RUNNING_WINDOW = 90.0
# A dispatched background agent that has gone quiet has NOT stopped. Measured on a real Fable run:
# five minutes between progress records while it worked through one long turn. At 90 seconds it
# vanished from the pill, the header and the feed, which reads as "it died" — so `running` (is it
# moving) and `open` (was it dispatched and never reported an outcome) are now separate questions.
OPEN_WINDOW = 1800.0
# Bytes from the end of an agent's own transcript when asking what it is doing. One turn is never
# larger than this, and reading the whole file to answer a status line would defeat the purpose.
_ACT_TAIL = 48_000
# Input keys worth showing, best first: the file matters more than the flags, the command more
# than the shell.
_ACT_KEYS = ("file_path", "path", "notebook_path", "command", "pattern", "url", "query",
             "description", "prompt")
# Content blocks that count as the agent having said something. An attachment or a system record
# is bookkeeping around the message and must not be read as the message's last word.
_BLOCK_KINDS = ("tool_use", "tool_result", "text", "thinking")
# HOW MUCH OF A TRANSCRIPT TO READ, COUNTED IN RECORDS.
#
# It used to be counted in bytes, and that number cannot be right for two agents at once. A
# coding agent writes records of about 1.5 KB. An agent driving Blender writes a viewport
# screenshot as inline base64 and its records reach 641 KB — four hundred times larger.
#
# Measured on a real run: 51 records, 2.6 MB, the largest 641 KB. The 48 KB activity window
# therefore covered 0.07 of ONE record, so it never contained a single complete line and the
# status read "(nothing)". The 512 KB panel window recovered 2 records out of 51, so the
# subagent panel said "nothing recorded yet" about an agent that was six tool calls in. The agent
# was fine. The window was the wrong shape.
#
# So: read backwards until there are enough COMPLETE records, whatever they weigh, with a byte
# ceiling so a pathological file still cannot be read whole.
_TAIL_RECORDS = 400            # the panel shows a timeline; this is generous for one
_ACT_RECORDS = 12              # the status line only needs the last tool call
_TAIL_CAP = 24 * 1024 * 1024   # never read more than this from one file, whatever it holds

# An image is a FACT, not a payload. The timeline needs to know a screenshot came back; it does
# not need the pixels, and carrying them costs a JSON parse, a REST response and a React render
# apiece. Replaced in the raw text, so the megabyte is never built into a Python object at all.
# Base64 holds no quote and no backslash, so cutting inside the quotes leaves valid JSON.
_BIG_BLOB = re.compile(r'"[A-Za-z0-9+/=]{2000,}"')


def _scrub(line: str) -> str:
    return _BIG_BLOB.sub(lambda m: '"(binary, %d KB omitted)"' % (len(m.group(0)) // 1024), line)


def _tail_records(path: Path, want: int, cap: int = _TAIL_CAP) -> list[str]:
    """The last `want` COMPLETE records of a transcript, however large each one is.

    Doubling seek-backs rather than one fixed window: the common case reads 64 KB and stops, and
    the screenshot case keeps going until it has real records instead of returning half of one."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    step = 64 * 1024
    while True:
        start = max(0, size - step)
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                if start:
                    fh.readline()          # drop the partial record the seek landed inside
                blob = fh.read(min(step + (1 << 20), cap))
        except OSError:
            return []
        lines = [ln for ln in blob.decode("utf-8", "ignore").splitlines() if ln.strip()]
        if len(lines) >= want or start == 0 or step >= cap:
            return lines[-want:]
        step *= 4


def _dirs(project_id: str) -> list[Path]:
    """Every `subagents/` folder for this project, newest first."""
    try:
        root, pdir, _ = mission.project_dir(project_id)
        pdir = pdir.resolve()
        if root.resolve() not in pdir.parents:
            return []
    except Exception:
        return []
    out: list[Path] = []
    try:
        for sd in pdir.iterdir():
            if sd.is_dir() and sd.name != "memory":
                sub = sd / "subagents"
                if sub.is_dir():
                    out.append(sub)
    except OSError:
        return []
    return sorted(out, key=lambda d: d.stat().st_mtime, reverse=True)


def _file_for(project_id: str, agent_id: str) -> Optional[Path]:
    name = f"agent-{agent_id}.jsonl"
    dirs = _dirs(project_id)
    for d in dirs:
        f = d / name
        if f.is_file():
            return f
    # A Workflow agent, one folder down. Asked second so a Task agent costs what it always did.
    for d in dirs:
        for run, _ended in _wf_runs(d):
            f = run / name
            if f.is_file():
                return f
    return None


def _session_of(f: Path) -> str:
    """The session that owns an agent transcript, whichever of the two folders it is in."""
    for p in f.parents:
        if p.name == "subagents":
            return p.parent.name
    return f.parent.parent.name


# ---------------------------------------------------------------------------
# Workflow-tool agents
# ---------------------------------------------------------------------------
# A Workflow run writes its agents ONE FOLDER DOWN, not beside the Task agents:
#
#     <session>/subagents/workflows/wf_<run>/agent-<id>.jsonl       its own transcript
#     <session>/subagents/workflows/wf_<run>/agent-<id>.meta.json   its label and its phase
#     <session>/subagents/workflows/wf_<run>/journal.jsonl          "started" and "result", per agent
#     <session>/workflows/wf_<run>.json                             written when the whole run ends
#
# Every count in this module scanned the first folder only. Four Opus 5.5 builders then worked
# for twenty minutes while the rail, the bottom bar and the needs-you strip all said 0, and only
# the Workflows tab knew. The model had nothing to do with it: the folder did.
#
# The parent transcript cannot close these agents the way it closes a Task agent. A Workflow is
# ONE background task, launched with a run id and reported once, when the whole run ends. So the
# run's own journal is the record: a "result" line ends one agent, and the consolidated file ends
# them all, the ones the script skipped included. Until then the agent is owed a result, exactly
# like a background Task agent, and it goes on counting while it is quiet inside a long turn.
_wf_journal_cache: dict = {}
_meta_cache: dict = {}
_wf_model_cache: dict = {}
_WF_CACHE_MAX = 512


def _wf_runs(d: Path) -> list[tuple[Path, float]]:
    """Every Workflow run under one `subagents/` folder, and when it ended (0.0 = still going).

    Two scandirs, and the second is only made when there is a run at all. On Windows a scandir
    entry carries its own dates, so this opens nothing."""
    try:
        with os.scandir(d / "workflows") as it:
            runs = [Path(e.path) for e in it if e.name.startswith("wf_") and e.is_dir()]
    except OSError:
        return []
    if not runs:
        return []
    ended: dict = {}
    try:
        with os.scandir(d.parent / "workflows") as it:
            for e in it:
                if e.name.startswith("wf_") and e.name.endswith(".json"):
                    try:
                        ended[e.name[:-5]] = e.stat().st_mtime or 1.0
                    except OSError:
                        ended[e.name[:-5]] = 1.0
    except OSError:
        pass
    return [(r, ended.get(r.name, 0.0)) for r in runs]


def _wf_ended(run: Path) -> float:
    """When this run ended, from its consolidated file; 0.0 while it is still going."""
    try:
        return (run.parent.parent.parent / "workflows" / (run.name + ".json")).stat().st_mtime or 1.0
    except OSError:
        return 0.0


def _wf_agent_files(run: Path) -> list[tuple[Path, float]]:
    """(transcript, mtime) for every agent of one run, from one scandir."""
    out: list[tuple[Path, float]] = []
    try:
        with os.scandir(run) as it:
            for e in it:
                if e.name.startswith("agent-") and e.name.endswith(".jsonl"):
                    try:
                        out.append((Path(e.path), e.stat().st_mtime))
                    except OSError:
                        continue
    except OSError:
        pass
    return out


def wf_journal(run: Path) -> dict:
    """What a run's journal says: which agents have returned, and each one's label and phase.

    `phases` is the order the phases first appear in, which is the order the script ran them.
    Re-read only when the journal changes; it is a few hundred bytes per agent."""
    jp = run / "journal.jsonl"
    empty = {"done": set(), "label": {}, "phase": {}, "phases": []}
    try:
        st = jp.stat()
    except OSError:
        return empty
    key = (st.st_mtime_ns, st.st_size)
    hit = _wf_journal_cache.get(str(jp))
    if hit and hit[0] == key:
        return hit[1]
    out = {"done": set(), "label": {}, "phase": {}, "phases": []}
    try:
        with jp.open("rb") as fh:
            for raw in fh:
                try:
                    o = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue            # a line still being written; the next read has it
                if not isinstance(o, dict):
                    continue
                aid = str(o.get("agentId") or "")
                if not aid:
                    continue
                if o.get("type") == "result":
                    out["done"].add(aid)
                if o.get("label"):
                    out["label"][aid] = str(o["label"])
                ph = str(o.get("phase") or "")
                if ph:
                    out["phase"][aid] = ph
                    if ph not in out["phases"]:
                        out["phases"].append(ph)
    except OSError:
        return empty
    if len(_wf_journal_cache) >= _WF_CACHE_MAX:
        _wf_journal_cache.pop(next(iter(_wf_journal_cache)), None)
    _wf_journal_cache[str(jp)] = (key, out)
    return out


def agent_meta(f: Path) -> dict:
    """An agent's `.meta.json`, written once at launch beside its transcript.

    Every agent has one, a Task agent and a Workflow agent alike: `description` is the name it was
    given, `agentType`, and for a Workflow agent `workflowPhase`. A Task agent's also says `model`."""
    key = str(f)
    got = _meta_cache.get(key)
    if got is not None:
        return got
    try:
        got = json.loads(f.with_name(f.stem + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}                        # not written yet: ask again next time
    if not isinstance(got, dict):
        got = {}
    if len(_meta_cache) >= _WF_CACHE_MAX:
        _meta_cache.pop(next(iter(_meta_cache)), None)
    _meta_cache[key] = got
    return got


def _wf_model(f: Path) -> str:
    """The model a Workflow agent runs on, from its newest records. Found once, then remembered.

    The rail's rows come from mtimes and carry no bill, so without this a live Workflow agent
    showed no model at all — and "which model is it" is the first thing anyone asks of it."""
    key = str(f)
    got = _wf_model_cache.get(key)
    if got:
        return got
    for raw in reversed(_tail_records(f, _ACT_RECORDS)):
        if '"model"' not in raw:
            continue
        try:
            o = json.loads(_scrub(raw))
        except ValueError:
            continue
        m = str(((o.get("message") or {}) if isinstance(o, dict) else {}).get("model") or "")
        if m and not m.startswith("<"):   # "<synthetic>" is the CLI's own message, not a model
            if len(_wf_model_cache) >= _WF_CACHE_MAX:
                _wf_model_cache.pop(next(iter(_wf_model_cache)), None)
            _wf_model_cache[key] = m
            return m
    return ""


def _wf_row(f: Path, run: Path, ended_at: float) -> dict:
    """What is known about one Workflow agent without opening its transcript."""
    aid = f.stem[len("agent-"):]
    jr = wf_journal(run)
    meta = agent_meta(f)
    row = {
        "agent_type": str(meta.get("agentType") or "workflow-subagent"),
        "description": mission._clean(jr["label"].get(aid) or str(meta.get("description") or ""), 200),
        "workflow": run.name,
        "workflow_phase": jr["phase"].get(aid) or str(meta.get("workflowPhase") or ""),
        # Dispatched by a background task and owed a result: a quiet one still counts.
        "background": True,
    }
    if aid in jr["done"]:
        row["status"] = "completed"
    elif ended_at:
        # The run ended and this agent never returned: skipped, failed or stopped with it.
        row["status"] = "stopped"
    return row


# A FEW PARSED TAILS, no more. `_list_uncached` asks the same file for its ledger and then for
# its file list, one after the other, so without this the first look at a project read every
# transcript twice. Kept small on purpose: a parsed tail is the heaviest thing in this module,
# where the two answers derived from it are a dozen numbers and a list of paths.
_ENTRIES_MAX = 8
_entries_cache: dict = {}


def _entries(path: Path, records: int = _TAIL_RECORDS) -> list[dict]:
    """The tail of one transcript, as parsed entries — read once for the questions that share it."""
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size, records)
    except OSError:
        return _entries_read(path, records)
    got = _entries_cache.get(key)
    if got is None:
        got = _entries_read(path, records)
        if len(_entries_cache) >= _ENTRIES_MAX:
            _entries_cache.pop(next(iter(_entries_cache)), None)   # oldest out, not everything
        _entries_cache[key] = got
    # THE LIST ITSELF, not a copy. Every caller here reads it and derives something smaller; the
    # entries are never edited, and copying a parsed tail would undo the point of holding one.
    return got


def _entries_read(path: Path, records: int = _TAIL_RECORDS) -> list[dict]:
    """The tail of one transcript, as parsed entries.

    Counted in RECORDS. A byte window is the wrong shape for this file: the same 512 KB is three
    hundred records of a coding agent and less than one record of an agent taking screenshots.
    """
    out: list[dict] = []
    for raw in _tail_records(path, records):
        try:
            o = json.loads(_scrub(raw))
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


# ---------------------------------------------------------------------------
# What one subagent touched
# ---------------------------------------------------------------------------
_EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
_READ_TOOLS = {"Read", "Grep", "Glob"}


def _paths_in(inp: dict) -> list[str]:
    out = []
    for k in ("file_path", "notebook_path", "path"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v)
    return out


_TOUCHED_MAX = 512
_touched_cache: dict = {}


def touched(project_id: str, agent_id: str, path: Optional[Path] = None) -> dict:
    """The files a subagent edited and read — read once per version of its transcript.

    THE SAME 116 FILES `_ledger` READS, through a different door. The panel beside the
    conversation asks for these, so `/subagents?files=true` re-opened every agent this project
    ever ran on every look: 3.8 to 6.1 seconds, once per workspace switch, while `/subagents`
    without them was already down to 0.10. Remembered against the file's own mtime and size, the
    same way and for the same reason — append-only means that key names its contents exactly.
    """
    # `path` when the caller already holds the file, which the list always does.
    f = path or _file_for(project_id, agent_id)
    if not f:
        return {"edited": [], "read": [], "commands": 0}
    try:
        st = f.stat()
        key = (str(f), st.st_mtime_ns, st.st_size)
    except OSError:
        return _touched_read(f)
    got = _touched_cache.get(key)
    if got is None:
        got = _touched_read(f)
        if len(_touched_cache) >= _TOUCHED_MAX:
            _touched_cache.pop(next(iter(_touched_cache)), None)   # oldest out, not everything
        _touched_cache[key] = got
    # A copy of the lists, so a caller that sorts or trims one does not edit what the next reads.
    return {"edited": list(got["edited"]), "read": list(got["read"]),
            "commands": got["commands"]}


def _touched_read(f: Path) -> dict:
    """The files a subagent edited and read, from ITS OWN tool calls.

    The summary the model gets counts edits (`editFileCount`) but names no file, so this is the
    only place the paths exist. Exact — no guessing from timestamps, which is what you would be
    reduced to if several agents ran at once.
    """
    edited: dict[str, int] = {}
    read: dict[str, int] = {}
    commands = 0
    for o in _entries(f):
        c = (o.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name") or ""
            inp = b.get("input") or {}
            if name in ("Bash", "PowerShell"):
                commands += 1
            bucket = edited if name in _EDIT_TOOLS else read if name in _READ_TOOLS else None
            if bucket is None:
                continue
            for p in _paths_in(inp):
                bucket[p] = bucket.get(p, 0) + 1
    return {
        "edited": [{"path": p, "times": n} for p, n in sorted(edited.items(), key=lambda x: -x[1])],
        "read": [{"path": p, "times": n} for p, n in sorted(read.items(), key=lambda x: -x[1])][:40],
        "commands": commands,
    }


# ---------------------------------------------------------------------------
# The bill
# ---------------------------------------------------------------------------
# Two sources, and neither is complete on its own.
#
# A FOREGROUND agent's result carries the lot: toolStats, usage, totalDurationMs. A BACKGROUND
# one (`isAsync`) is recorded the moment it is launched, so its record holds the prompt, the
# model and an output file — and no numbers at all, because it has not done anything yet.
# Nothing ever goes back to fill them in.
#
# So the numbers are computed from the agent's OWN transcript, which every agent has, and the
# parent's figures are laid over the top wherever they exist. Every subagent then shows a bill,
# and the authoritative version wins where it was recorded.

_SEARCH_TOOLS = {"Grep", "Glob", "ToolSearch"}


def _epoch(iso: str) -> float:
    if not iso:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# ONE SUBAGENT'S NUMBERS, REMEMBERED UNTIL ITS FILE CHANGES.
#
# `list_for` reads every `agent-*.jsonl` this project has ever written — 116 of them here — and it
# sits behind the conversation feed on a 3-second cache. So one poll in every three paid the whole
# scan, and measured against the running backend that poll took THIRTEEN SECONDS. Six of those in
# flight is a browser's entire connection budget for the origin, which is why changing workspace
# waited on nothing you could point at.
#
# A subagent transcript is APPEND-ONLY, and all but the one still running finished long ago. Keyed
# on mtime and size the memo is exact rather than approximate: a file cannot grow without both
# moving, so this repeats no work rather than serving anything stale.
_LEDGER_MAX = 512
_ledger_cache: dict = {}
# The parent transcript's scan position, kept across restarts (see `_summary_records`).
_SCAN_DISK = perf.Cursor("parent_scan")
# The same answers, kept on disk, so the first look at a project after a restart is not a re-read
# of every agent transcript it owns. Keyed by path + size + date; see `_ledger`.
# v2: the first one holds the old line sums (every call counted two or three times) and would
# go on serving them for every finished agent, because a finished file never changes its key.
_LEDGER_DISK = perf.DiskMemo("agent_ledgers_v2", limit=4000)


def _ledger(path: Path) -> dict:
    """Duration, tokens and tool counts for one subagent — read once per version of its file."""
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return _ledger_read(path)
    got = _ledger_cache.get(key)
    if got is None:
        # ...AND ACROSS RESTARTS. A finished agent's transcript never changes, so its bill never
        # does either — but the memory cache died with the process, and the first look at a project
        # read every agent transcript again. Measured on a workspace with 137 agents and 1.5 GB of
        # them: 6.5 s, every restart, while somebody watched an empty pane. The size and date are
        # in the key, so an agent still being written is re-read as before.
        got = _LEDGER_DISK.get("%s|%d|%d" % (str(path), st.st_mtime_ns, st.st_size),
                               lambda: _ledger_read(path))
        if len(_ledger_cache) >= _LEDGER_MAX:
            _ledger_cache.pop(next(iter(_ledger_cache)), None)   # oldest out, not everything
        _ledger_cache[key] = got
    # A COPY. The caller merges the parent's figures into this and stamps `running`, `updated` and
    # the rest onto it; handing out the remembered dict would let one request edit another's.
    out = dict(got)
    out["usage"] = dict(got.get("usage") or {})
    out["stats"] = dict(got.get("stats") or {})
    return out


# ---------------------------------------------------------------------------
# ONE TRANSCRIPT, READ FRONT TO BACK ONCE - THEN ONLY WHAT WAS APPENDED.
#
# The bill used to come from the last 400 records, added up line by line, and both halves were
# wrong. A long agent's first calls were never counted (the v3.1 tree agent: 94 of its 115 tool
# calls, and a duration that started halfway), and the lines of one call were counted two or three
# times (see agent_usage). Reading the whole file every time it grows would be the third mistake:
# that agent's transcript reached 17.8 MB, and the card polls every few seconds. So each transcript
# keeps a scan that remembers where it stopped; an append-only file makes that exact.
_SCANS: dict = {}
_SCANS_MAX = 256
_SCAN_LOCK = threading.Lock()
_CHUNK = 4 * 1024 * 1024


class _Scan:
    __slots__ = ("pos", "calls", "tool_ids", "tools", "stats", "first", "last", "model")

    def __init__(self) -> None:
        self.pos = 0
        self.calls = agent_usage.Calls()
        self.tool_ids: set = set()
        self.tools = 0
        self.stats = {"read": 0, "search": 0, "bash": 0, "edits": 0, "added": 0, "removed": 0,
                      "other": 0}
        self.first = 0.0
        self.last = 0.0
        self.model = ""


def _scan_line(s: _Scan, raw: bytes) -> None:
    """One record. Only the first one and the model's own answers are parsed: a tool result can be
    a 600 KB screenshot, and nothing on the card comes from one."""
    if not raw.strip():
        return
    if s.first and b'"usage"' not in raw and b'"tool_use"' not in raw:
        return
    try:
        o = json.loads(_scrub(raw.decode("utf-8", "ignore")))
    except ValueError:
        return
    if not isinstance(o, dict):
        return
    t = _epoch(str(o.get("timestamp") or ""))
    if t:
        s.first = s.first or t
        s.last = max(s.last, t)
    m = o.get("message")
    if not isinstance(m, dict):
        return
    # Claude Code writes "type": "assistant"; an older or hand-made record says it in the message.
    if o.get("type") != "assistant" and m.get("role") != "assistant":
        return
    model = str(m.get("model") or "")
    if model and not model.startswith("<"):        # "<synthetic>" is the CLI's own message
        s.model = model
    u = m.get("usage")
    if isinstance(u, dict) and u:
        s.calls.add(agent_usage.call_key(o), u)
    c = m.get("content")
    if not isinstance(c, list):
        return
    for b in c:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        tid = str(b.get("id") or "")
        if tid:
            if tid in s.tool_ids:
                continue
            s.tool_ids.add(tid)
        s.tools += 1
        name = b.get("name") or ""
        inp = b.get("input") or {}
        st = s.stats
        if name in _EDIT_TOOLS:
            st["edits"] += 1
            if name == "Write":
                st["added"] += len(str(inp.get("content") or "").split("\n"))
            elif name == "MultiEdit":
                for e in inp.get("edits") or []:
                    a, r, _h = mission._diff_stat(e.get("old_string", ""), e.get("new_string", ""))
                    st["added"] += a
                    st["removed"] += r
            else:
                a, r, _h = mission._diff_stat(inp.get("old_string", ""), inp.get("new_string", ""))
                st["added"] += a
                st["removed"] += r
        elif name in ("Read", "NotebookRead"):
            st["read"] += 1
        elif name in _SEARCH_TOOLS:
            st["search"] += 1
        elif name in ("Bash", "PowerShell"):
            st["bash"] += 1
        else:
            st["other"] += 1


def _scan(path: Path) -> _Scan:
    """This transcript's scan, brought up to date. Call with _SCAN_LOCK held."""
    key = str(path)
    s = _SCANS.pop(key, None)                 # taken out and put back: the dict order is the LRU
    try:
        size = path.stat().st_size
    except OSError:
        size = -1
    if s is None or 0 <= size < s.pos:        # new, or rewritten shorter: from the start
        s = _Scan()
    if size > s.pos:
        try:
            with path.open("rb") as fh:
                fh.seek(s.pos)
                base, carry = s.pos, b""
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    data = carry + chunk
                    end = data.rfind(b"\n")
                    if end < 0:
                        carry = data
                        continue
                    for raw in data[:end].split(b"\n"):
                        _scan_line(s, raw)
                    base += end + 1
                    carry = data[end + 1:]
                # A last line still being written is left for the next pass.
                s.pos = base
        except OSError:
            pass
    _SCANS[key] = s
    if len(_SCANS) > _SCANS_MAX:
        _SCANS.pop(next(iter(_SCANS)), None)
    return s


def _ledger_read(path: Path) -> dict:
    """Duration, tokens and tool counts of one subagent, from ALL of its own transcript."""
    with _SCAN_LOCK:
        s = _scan(path)
        t = s.calls.totals()
        stats = dict(s.stats)
        tools, first, last, model = s.tools, s.first, s.last, s.model
        calls, inherited, peak = len(s.calls), s.calls.first, s.calls.peak
    return {
        "ms": int(max(0.0, last - first) * 1000),
        "tools": tools,
        # API calls, each once. It used to be the number of lines, two or three per call.
        "turns": calls,
        "model": model,
        "usage": t,
        # THE HEADLINE: what the agent really added - input it had not sent before, and what it
        # wrote. The same context read again from the cache on every call is `usage.cache_read`,
        # shown apart: counted in, it made a 747k agent read as 33.6M.
        "tokens": agent_usage.new_tokens(t),
        # Everything that crossed the wire, cache reads included - for whoever wants that sum.
        "wire": agent_usage.wire_tokens(t),
        # What it STARTED with, and how full it ever got. Summing usage across calls cannot answer
        # either question: every call re-sends the same growing prefix.
        "inherited": inherited,
        "peak": peak,
        "ctx_max": mission.model_window(model),
        # List price for this agent's own tokens. Not what a Max plan is charged - the UI says
        # so - but the only figure that makes two agents or two models comparable.
        "cost": pricing.cost(model, t),
        "stats": stats,
    }


def _merge(computed: dict, parent: dict) -> dict:
    """The parent's figures win where it has them; the computed ones fill every gap."""
    out = dict(computed)
    for k, v in (parent or {}).items():
        if v in (None, "", 0, {}, []):
            continue
        if k in ("usage", "stats") and isinstance(v, dict):
            merged = dict(out.get(k) or {})
            merged.update({kk: vv for kk, vv in v.items() if vv})
            out[k] = merged
        else:
            out[k] = v
    # The bill has to match the usage that is on screen. The parent's figures win above, so the
    # price is worked out again from whatever survived rather than from what we computed first.
    if out.get("usage"):
        out["cost"] = pricing.cost(str(out.get("model") or ""), out["usage"])
    return out


# ---------------------------------------------------------------------------
# One subagent, in full
# ---------------------------------------------------------------------------
def detail(project_id: str, agent_id: str, limit: int = 400) -> dict:
    """A subagent's own timeline, rendered with the SAME event builder as the main feed.

    Reusing it is the point: a subagent's thinking, its diffs and its commands then look exactly
    like the parent's, because they are drawn by the same code rather than by a second, lesser
    renderer that drifts away from it.
    """
    if engines.native(project_id):
        return engines.native(project_id).subagent_detail(project_id, agent_id, limit)
    f = _file_for(project_id, agent_id)
    if not f:
        return {"ok": False, "error": "no transcript for that agent", "lines": []}
    events: list = []
    started = ended = ""
    for o in _entries(f):
        ts = o.get("timestamp") or ""
        if ts:
            started = started or ts
            ended = ts
        typ = o.get("type")
        m = o.get("message") or {}
        c = m.get("content")
        if typ == "user":
            if isinstance(c, str) and c.strip():
                events.append({"kind": "user", "ts": ts, "text": mission._clean(c)})
            elif isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and (b.get("text") or "").strip():
                        events.append({"kind": "user", "ts": ts, "text": mission._clean_md(b["text"])})
                    elif b.get("type") == "tool_result":
                        txt, ok = mission._result_text(b)
                        if txt.strip():
                            events.append({"kind": "result", "ts": ts, "ok": ok,
                                           "text": mission._clean_out(txt)})
        elif typ == "assistant" and isinstance(c, list):
            thought = False
            for b in c:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt in ("thinking", "redacted_thinking"):
                    txt = mission._clean(b.get("thinking") or "")
                    if txt or not thought:
                        thought = True
                        events.append({"kind": "thinking", "ts": ts, "text": txt})
                elif bt == "text" and (b.get("text") or "").strip():
                    events.append({"kind": "text", "ts": ts, "text": mission._clean_md(b["text"])})
                elif bt == "tool_use":
                    events.append(mission._tool_event(b, ts))
    sess = _session_of(f)
    base = _summaries(project_id, {sess}).get(agent_id) or {}
    if f.parent.name.startswith("wf_") and f.parent.parent.name == "workflows":
        base = _merge(_wf_row(f, f.parent, _wf_ended(f.parent)), base)
    row = _merge(_ledger(f), base)
    row.update({
        "agent_id": agent_id,
        "running": time.time() - f.stat().st_mtime <= RUNNING_WINDOW
        and str(row.get("status") or "") != "completed",
        "started": started,
        "ended": ended,
        "session": sess,
        "bytes": f.stat().st_size,
    })
    return {"ok": True, "agent": row, "lines": events[-limit:],
            "touched": touched(project_id, agent_id)}


# ---------------------------------------------------------------------------
# Every subagent of a project
# ---------------------------------------------------------------------------
# Measured at 0.14s for 15 agents, 0.20s with the file lists. That is fine once and wasteful
# forty times a minute: the feed asks on every poll whenever a card is on screen, and the bottom
# bar asks as well. Nothing here changes faster than a subagent writes a line, so a few seconds
# of cache turns a per-poll cost into a per-few-seconds one and the numbers still tick visibly.
_LIST_TTL = 3.0
_list_cache: dict = {}


def list_for(project_id: str, session: str = "", with_files: bool = False) -> dict:
    """Every subagent this project has run, newest first, with its bill and its footprint."""
    if engines.native(project_id):
        return engines.native(project_id).subagents(project_id)
    key = (project_id, session, with_files)
    hit = _list_cache.get(key)
    now = time.time()
    if hit and now - hit[0] < _LIST_TTL:
        return hit[1]
    out = _list_uncached(project_id, session, with_files)
    # STAMPED WHEN THE WORK FINISHED, not when it started. Stamping with the earlier `now` meant
    # any scan slower than the TTL produced an entry that was already expired on arrival — so a
    # slow project could never be cached at all, and every single poll paid the full scan. The
    # cache was mathematically incapable of hitting in exactly the case it existed for.
    done = time.time()
    _list_cache[key] = (done, out)
    if len(_list_cache) > 64:                    # a long session opens a handful of projects
        for k in [k for k, v in _list_cache.items() if done - v[0] > 60]:
            _list_cache.pop(k, None)
    return out


def _list_uncached(project_id: str, session: str = "", with_files: bool = False) -> dict:
    dirs = _dirs(project_id)
    summaries = _summaries(project_id, {d.parent.name for d in dirs} or None)
    seen: dict = {}
    now = time.time()
    for d in dirs:
        if session and d.parent.name != session:
            continue
        files: list = [(f, f.stat().st_mtime, None, 0.0) for f in d.glob("agent-*.jsonl")]
        # Workflow agents. A run still going shows every agent it has, the finished ones too, so
        # "3 working, 1 done" reads as one run. A run that has ENDED shows here for half an hour
        # and then only in the Workflows tab, which keeps the whole history: one earlier run on
        # this machine had 202 agents, and reading 202 transcripts is a scan this poll must not do.
        for run, ended_at in _wf_runs(d):
            if ended_at and now - ended_at > OPEN_WINDOW:
                continue
            for f, mt in _wf_agent_files(run):
                if ended_at and now - mt > OPEN_WINDOW:
                    continue
                files.append((f, mt, run, ended_at))
        for f, mt, run, ended_at in sorted(files, key=lambda x: x[1], reverse=True):
            aid = f.stem[len("agent-"):]
            if aid in seen:
                continue
            base = summaries.get(aid) or {}
            if run is not None:
                base = _merge(_wf_row(f, run, ended_at), base)
            row = _merge(_ledger(f), base)
            row["agent_id"] = aid
            # A launch record says the agent STARTED and never that it stopped, so a background
            # agent would look finished the moment it went quiet. Its own transcript is the only
            # thing that knows: still being written to = still working. And a recorded outcome
            # ends it — unless the agent has written since, which makes that outcome stale.
            row["running"] = (now - mt) <= RUNNING_WINDOW and not _finished(row, mt)
            row["updated"] = mt
            row["idle_s"] = round(now - mt, 1)
            # Dispatched and never heard from again is not the same as finished. A background
            # agent goes quiet for minutes inside one long turn, and dropping it at 90 seconds
            # made a working agent look dead everywhere at once. `open` keeps it on screen; the
            # idle time beside it is what tells the user whether to worry.
            # `_finished` and nothing else. This used to ALSO test the status unconditionally,
            # which is a different question with a different answer: it treats any recorded
            # outcome as the last word, so an agent that was resumed after completing could never
            # show as working again. One test, in one place, or the panel and the bar disagree —
            # and they did, in both directions, on the same afternoon.
            row["open"] = (bool(row.get("background"))
                           and not _finished(row, mt)
                           and (now - mt) <= OPEN_WINDOW)
            # Only for the one or two agents that are actually live: reading the tail of every
            # agent this project ever ran, to fill a status line, is the mistake this file exists
            # to avoid.
            if row["running"] or row["open"]:
                act = _last_activity(f)
                if act:
                    row["activity"] = act
            row["session"] = d.parent.name
            if with_files:
                row["touched"] = touched(project_id, aid, f)
            seen[aid] = row
    rows = sorted(seen.values(), key=lambda r: r.get("updated", 0), reverse=True)
    return {
        "agents": rows,
        "running": sum(1 for r in rows if r.get("running")),
        # Quiet but not finished. Counted apart from `running` so the UI can say "working" and
        # "dispatched, quiet for 5m" differently instead of showing nothing for both.
        "open": sum(1 for r in rows if r.get("open") and not r.get("running")),
        # New tokens, and the cache reads apart - the same split the card shows.
        "tokens": sum(int(r.get("tokens") or 0) for r in rows),
        "cache_read": sum(int((r.get("usage") or {}).get("cache_read") or 0) for r in rows),
    }


def _last_activity(path: Path) -> dict:
    """What this agent is doing right now, read from the end of its own transcript.

    TWO questions, not one, and answering only the first is what made a working agent look dead.

    The tool it last asked for is the easy half: the newest `tool_use` block. But a tool that
    FINISHED thirteen minutes ago is not what the agent is doing, and showing it as though it were
    is worse than showing nothing — the panel read "Bash python -" for thirteen minutes while the
    agent was in fact writing a very long file, and the only honest conclusion available from the
    screen was that the command had hung. It had not: it returned in 0.3 seconds.

    So `phase` answers the second half. The newest real message record decides it. An assistant
    message ending in a `tool_use` means that tool is still running, because its result would have
    been appended after it. Anything else — a `tool_result`, an attachment, plain text — means the
    tool is done and the agent is generating.

    That distinction is the whole explanation for a transcript that has gone quiet: a turn writes
    NOTHING until the message completes, so a long think plus a large file is many minutes of
    silence with nothing wrong. `phase` plus the idle time says so instead of leaving the user to
    guess.

    Bounded on purpose: the tail only, newest record first. The transcript of a long agent runs to
    megabytes and this is called from a status poll.
    """
    tail = _tail_records(path, _ACT_RECORDS)
    if not tail:
        return {}

    phase = ""
    out: dict = {}
    for raw in reversed(tail):
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            o = json.loads(_scrub(raw))
        except Exception:
            continue
        content = (o.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if not phase:
            # The newest message with real content settles it, and only that one.
            tail_kind = ""
            for b in content:
                if isinstance(b, dict) and b.get("type") in _BLOCK_KINDS:
                    tail_kind = b.get("type")
            if tail_kind:
                phase = "tool" if (o.get("type") == "assistant" and tail_kind == "tool_use") \
                    else "generating"
        for b in reversed(content):
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            out = {"tool": str(b.get("name") or "tool"), "detail": ""}
            for k in _ACT_KEYS:
                v = inp.get(k)
                if isinstance(v, str) and v.strip():
                    out["detail"] = " ".join(v.split())[:140]
                    break
            break
        if out and phase:
            break
    if not (out or phase):
        return {}
    out.setdefault("tool", "")
    out.setdefault("detail", "")
    # No record at all still means it is doing something -- it exists and has not finished.
    out["phase"] = phase or "generating"
    return out


def working(project_id: str, window: float = OPEN_WINDOW) -> list:
    """THE ONE DEFINITION OF "an agent is working", used by every screen that shows a count.

    There used to be three, and the user saw all three at once. The chat feed counted any agent
    file touched in the last 180 seconds, so a finished agent still counted and a quiet one did
    not. The left rail counted 90 seconds with an outcome check. The bottom-bar pill counted 90
    seconds plus a 30-minute "dispatched and never reported" window. One fan-out therefore read
    as 2 under the chat, 0 in the rail and 1 in the bar, and nothing on screen said which was
    right. A number that disagrees with itself is worse than no number.

    An agent counts if it is MOVING (it wrote within the running window) or if it is OWED (it was
    dispatched in the background and no outcome has been recorded). Each row carries `moving`, so
    a caller that wants to say "3 working, 1 quiet" can, without asking a second question.
    """
    cut = time.time() - RUNNING_WINDOW
    out = []
    for a in running(project_id, window=window):
        a["moving"] = float(a.get("updated") or 0) > cut
        if a["moving"] or a.get("background"):
            out.append(a)
    return out


def open_background(project_id: str, window: float = OPEN_WINDOW) -> list:
    """Background agents that were dispatched and have not reported an outcome.

    A wider window than `running` on purpose. This answers "would something be destroyed if the
    session were interrupted right now", and a background agent can sit quiet for many minutes
    inside one long tool call without having stopped. Being wrong here costs the user an agent,
    so the question is asked generously.
    """
    return [a for a in running(project_id, window=window) if a.get("background")]


def running(project_id: str, window: float = RUNNING_WINDOW) -> list:
    """The subagents of this project that are still working.

    THE LEFT RAIL ASKS THIS EVERY 1.5 SECONDS, so what it costs is what the whole app costs.

    It used to answer through `list_for`, which builds the full picture of every subagent the
    project has ever run: the parent transcript joined for each prompt, label and bill, plus each
    agent's own transcript opened for its token ledger. On a real project here that is 523 MB of
    parent transcripts and 125 agent files — seconds of work, repeated every poll, to print a
    number that is almost always zero.

    A count needs two facts per agent: was its file written inside the window, and did the parent
    record it as completed. The first is an mtime. The second is now free, because the parent scan
    is incremental. So nothing is opened unless something is genuinely running, and the common
    answer — nothing is — costs one scandir per session folder.
    """
    cut = time.time() - window
    fresh: list[tuple[str, str, float]] = []          # (agent_id, session, mtime)
    wf_fresh: list[tuple[Path, str, float, Path]] = []  # Workflow agents: (file, session, mtime, run)
    for d in _dirs(project_id):
        try:
            with os.scandir(d) as it:
                for e in it:
                    if not (e.name.startswith("agent-") and e.name.endswith(".jsonl")):
                        continue
                    try:
                        mt = e.stat().st_mtime
                    except OSError:
                        continue
                    if mt > cut:
                        fresh.append((e.name[6:-6], d.parent.name, mt))
        except OSError:
            continue
        # Workflow agents, one folder down. A run that has ended owes nothing, so it is not
        # even listed; the rest is one scandir per run that is still going.
        for run, ended_at in _wf_runs(d):
            if ended_at:
                continue
            for f, mt in _wf_agent_files(run):
                if mt > cut:
                    wf_fresh.append((f, d.parent.name, mt, run))
    if not fresh and not wf_fresh:
        return []
    # Only the sessions that actually hold a live agent, and only their new bytes. Never an empty
    # set: `_summaries` reads EVERY parent transcript when it is given none.
    summaries = _summaries(project_id, {s for _, s, _ in fresh}) if fresh else {}
    seen: dict = {}
    for aid, sess, mt in sorted(fresh, key=lambda r: r[2], reverse=True):
        if aid in seen:
            continue
        row = dict(summaries.get(aid) or {})
        # A launch record says the agent STARTED and never that it stopped; the outcome is
        # written later — as `completed` on a foreground agent, or as a queued notification on a
        # background one. Recent writes AND no outcome newer than them = still working.
        if _finished(row, mt):
            continue
        row.update({"agent_id": aid, "session": sess, "updated": mt, "running": True})
        seen[aid] = row
    for f, sess, mt, run in sorted(wf_fresh, key=lambda r: r[2], reverse=True):
        aid = f.stem[len("agent-"):]
        if aid in seen:
            continue
        row = _wf_row(f, run, 0.0)
        if _finished(row, mt):           # its "result" line is in the journal
            continue
        row.update({"agent_id": aid, "session": sess, "updated": mt, "running": True})
        model = _wf_model(f)
        if model:
            row["model"] = model
        seen[aid] = row
    return list(seen.values())


# ---------------------------------------------------------------------------
# Two agents in the same file
# ---------------------------------------------------------------------------
def _symbols_for(root: str, paths: list) -> dict:
    """Ask the code graph which symbols live in these files.

    Best effort by design: the graph is built in the background and may not be ready yet, and
    "both agents wrote this FILE" is worth saying even when the symbol names are not to hand.
    """
    out: dict = {}
    if not root or not paths:
        return out
    try:
        import httpx
    except Exception:
        return out
    for p in list(paths)[:12]:
        try:
            r = httpx.get("http://127.0.0.1:8777/api/graphify/query",
                          params={"root": root, "q": os.path.basename(p)}, timeout=1.5)
            names = [m.get("name", "") for m in (r.json().get("same_file") or [])
                     if isinstance(m, dict) and m.get("name")]
            if names:
                out[p] = names[:12]
        except Exception:
            continue
    return out


def collisions(project_id: str, root_path: str = "") -> list:
    """Files that more than one RUNNING subagent has edited.

    This is the failure mode of parallel work, and it stays invisible until the second agent
    overwrites the first one's edit. Both sides come from the agents' own tool calls, so a
    warning here means both really did write that file — not merely that they overlapped in time.
    """
    live = running(project_id)
    if len(live) < 2:
        return []
    by_file: dict = {}
    for a in live:
        for e in touched(project_id, a["agent_id"])["edited"]:
            by_file.setdefault(e["path"], set()).add(a["agent_id"])
    clashes = {p: ids for p, ids in by_file.items() if len(ids) > 1}
    if not clashes:
        return []
    syms = _symbols_for(root_path, list(clashes))
    return [{"path": p, "agents": sorted(ids), "symbols": syms.get(p, [])}
            for p, ids in sorted(clashes.items())]


# ---------------------------------------------------------------------------
# What the parent recorded about each subagent
# ---------------------------------------------------------------------------
# One cursor per transcript: path -> (bytes_consumed, records_so_far).
# Only complete lines are ever consumed, so a half-written line at the tail of a LIVE transcript
# is re-read next time rather than lost.
_scan_cursor: dict[str, tuple[int, list]] = {}
_scan_lock = threading.Lock()
_SCAN_MAX = 96


def _summary_records(path: Path) -> list:
    """Every subagent record the parent wrote in this transcript — reading only what is new.

    THIS IS THE HOT PATH OF THE WHOLE APP, and it used to re-read the file from byte zero on
    every call. The left rail polls the running-agent count every 1.5s; that count comes from
    here; and one real project holds 523 MB of transcripts across nine sessions. Measured inside
    the backend, with a turn streaming and the GIL contended, a single call took 6 to 10 SECONDS.
    A new one started every 1.5s, so five or six were always in flight — and a browser allows six
    connections per origin. The pool was permanently full, every other request queued behind it,
    and the conversation feed simply never loaded. That is the blank chat, and why an idle CPU and
    a healthy backend were consistent with it.

    A transcript is append-only, so the fix is a cursor: remember where the last read stopped and
    parse only the bytes added since. The first call after a restart pays the full scan once. A
    transcript that SHRANK was truncated (the Studio rewinds conversations by truncating them),
    so that one is read again from the start.

    Returns finished records rather than raw entries, which also bounds what the cache holds: a
    parsed entry carries the agent's whole prompt and result, a record carries them trimmed.
    """
    key = str(path)
    try:
        size = path.stat().st_size
    except OSError:
        with _scan_lock:
            _scan_cursor.pop(key, None)
        return []

    # These endpoints run in a threadpool, so two requests can reach the same file at once.
    # Unlocked, both would read the same bytes and append the same records to the SAME list.
    # The scan is milliseconds now, so serialising it costs nothing worth measuring.
    with _scan_lock:
        seen = _scan_cursor.get(key)
        if seen is None:
            # ...AND THE PLACE SURVIVES A RESTART. The cursor above dies with the process, so the
            # first look at a project after one read the parent transcript from byte zero again:
            # 1.6 s for a 1 GB session, while somebody waited for the chat to appear. The saved
            # place is only trusted when the 64 bytes at its boundary are unchanged.
            saved = _SCAN_DISK.get(key, size)
            if saved:
                seen = (saved[0], list(saved[1]))
                _scan_cursor[key] = seen
        if seen and seen[0] <= size:
            pos, rows = seen[0], seen[1]
            if pos == size:
                return rows                  # nothing appended since the last look
        else:
            pos, rows = 0, []                # first sight, or the file was truncated

        try:
            with path.open("rb") as fh:
                if pos:
                    fh.seek(pos)
                for raw in fh:
                    # A partial final line means the CLI is mid-write. Stop before it and leave
                    # the cursor put, so the finished line is picked up on the next call.
                    if not raw.endswith(b"\n"):
                        break
                    pos += len(raw)
                    # TWO kinds of line matter, and only one of them was ever read. A `toolUseResult`
                    # carries a foreground agent's numbers; a <task-notification> carries a
                    # BACKGROUND one's outcome, which is the only place that outcome is ever written.
                    if b'"agentId"' not in raw and b"task-notification" not in raw:
                        continue
                    try:
                        o = json.loads(raw.decode("utf-8", "ignore"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(o, dict):
                        row = _record(o) or _notify_record(o)
                        if row:
                            rows.append(row)
        except OSError:
            return rows

        _scan_cursor[key] = (pos, rows)
        if len(_scan_cursor) > _SCAN_MAX:
            _scan_cursor.pop(next(iter(_scan_cursor)), None)   # oldest out, never a full flush
        _SCAN_DISK.put(key, pos, rows)
        return rows


def _record(o: dict) -> Optional[dict]:
    """One subagent record out of a parent transcript entry, or None."""
    tr = o.get("toolUseResult")
    if not isinstance(tr, dict) or not tr.get("agentId"):
        return None
    st = tr.get("toolStats") or {}
    content = tr.get("content")
    row = {
        "agent_id": str(tr["agentId"]),
        "agent_type": str(tr.get("agentType") or ""),
        "description": mission._clean(str(tr.get("description") or ""), 200),
        "model": str(tr.get("resolvedModel") or ""),
        "status": str(tr.get("status") or ""),
        "background": bool(tr.get("isAsync")),
        "output_file": str(tr.get("outputFile") or ""),
        "prompt": mission._clean(str(tr.get("prompt") or ""), 6000),
        "result": mission._clean_out(content, 6000) if isinstance(content, str) else "",
        "ts": o.get("timestamp") or "",
    }
    if tr.get("totalDurationMs"):
        row["ms"] = int(tr["totalDurationMs"])
    # NOT `totalTokens`, and NOT `usage` below. Both are the agent's LAST call - `totalTokens` is
    # that call's usage added up, which is the size of its context at the end. Laid over the
    # agent's own figures they made a 24-call agent show one call: 5,430 written of 19,444.
    if tr.get("totalToolUseCount"):
        row["tools"] = int(tr["totalToolUseCount"])
    if st:
        row["stats"] = {
            "read": int(st.get("readCount") or 0),
            "search": int(st.get("searchCount") or 0),
            "bash": int(st.get("bashCount") or 0),
            "edits": int(st.get("editFileCount") or 0),
            "added": int(st.get("linesAdded") or 0),
            "removed": int(st.get("linesRemoved") or 0),
            "other": int(st.get("otherToolCount") or 0),
        }
    return row


# A background task's outcome, queued into the conversation as a notification.
_NOTIFY_ID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
_NOTIFY_STATUS = re.compile(r"<status>\s*([^<]*?)\s*</status>")
_NOTIFY_FILE = re.compile(r"<output-file>\s*([^<]*?)\s*</output-file>")
_NOTIFY_SUMMARY = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.S)

# An outcome has been recorded. Anything else means the task is still open.
_TERMINAL = {"completed", "stopped", "failed", "error", "cancelled", "canceled",
             "killed", "timed_out", "timeout"}


def _notify_record(o: dict) -> Optional[dict]:
    """One background task's OUTCOME, read out of the notification queued for the parent.

    A BACKGROUND agent never gets a `toolUseResult` carrying its numbers. The parent writes
    `status: async_launched` when it dispatches one and never says another word about it — so
    reading only `toolUseResult`, as this module did, left every background agent this project
    has ever run stuck at "async_launched": no verdict, no result, nothing to show when one
    finished. Sixty-nine of them, against forty-two foreground agents that all reported fine.

    The outcome is written, just somewhere else: the CLI queues a <task-notification> into the
    conversation carrying the task id, its status, a one-line summary and the output file. That
    is the completion record for a background agent, and this reads it.
    """
    if o.get("type") != "queue-operation":
        return None
    text = o.get("content")
    if not isinstance(text, str) or "<task-notification>" not in text:
        return None
    m = _NOTIFY_ID.search(text)
    if not m:
        return None
    row: dict = {"agent_id": m.group(1), "background": True}
    st = _NOTIFY_STATUS.search(text)
    if st and st.group(1):
        row["status"] = st.group(1)
        # WHEN the verdict was reached, which is what makes it possible to tell a current one
        # from a stale one. See _finished().
        row["status_at"] = _epoch(str(o.get("timestamp") or ""))
    of = _NOTIFY_FILE.search(text)
    if of and of.group(1):
        row["output_file"] = of.group(1)
    sm = _NOTIFY_SUMMARY.search(text)
    if sm and sm.group(1):
        row["result"] = mission._clean(sm.group(1), 2000)
    return row


# THE RACE, MEASURED. An outcome is recorded in the parent's transcript, and the agent's OWN
# transcript is flushed a fraction of a second afterwards. Measured on two real agents: the
# verdict landed 0.1 SECONDS before the last write, every time.
#
# Compared exactly, that reads as "the verdict is older than the file, so it is stale" — and a
# finished agent then went on counting for the whole thirty-minute window. That is what put
# "2 agents" on the bar with nothing delegated and an empty panel beside it. It also had a second
# cost nobody had seen yet: `open_background` guards /compact, a new conversation and the restart
# warning, so every one of those would have refused for half an hour AFTER the agents were done.
#
# The rule this margin protects is about a verdict that is MINUTES stale — a resume reporting an
# agent it could not find as "stopped" while that agent was mid-write. Those measured 152s and
# 440s. Ten seconds separates the two cases cleanly and leaves the real rule intact.
_VERDICT_GRACE = 10.0


def _finished(row: dict, mtime: float) -> bool:
    """Has this agent's outcome been recorded — and is that verdict still true?

    A verdict meaningfully older than the agent's own last write is stale, and this is not
    hypothetical. On resume, the CLI reports any background agent it cannot find a completion
    record for as "stopped"; one of those was writing its transcript at that very moment, so the
    rail said one agent was running while the conversation said it had stopped. The file is live
    evidence and the notification is a statement made at a point in time, so the newer one wins —
    but only when it is newer by more than the two writes take to land.
    """
    st = str(row.get("status") or "").lower()
    if st not in _TERMINAL:
        return False
    at = float(row.get("status_at") or 0.0) or _epoch(str(row.get("ts") or ""))
    if not at:
        return True                       # nothing to compare against — take the verdict
    return at >= mtime - _VERDICT_GRACE


def _summaries(project_id: str, sessions: Optional[set] = None) -> dict:
    """The parent's own record of each subagent, keyed by agent id.

    The parent transcript is where the prompt, the label and (for a foreground agent) the bill
    live. The subagent transcript has none of that, so without this join a finished agent shows
    its work and not what it was asked for or what it cost.
    """
    out: dict = {}
    try:
        root, pdir, _ = mission.project_dir(project_id)
        pdir = pdir.resolve()
        if root.resolve() not in pdir.parents:
            return out
    except Exception:
        return out
    # Every session that owns a subagents folder, not only the newest — the agents on screen are
    # often from the conversation you were in an hour ago.
    files = [pdir / (sid + ".jsonl") for sid in sorted(sessions)] if sessions else list(pdir.glob("*.jsonl"))
    for f in files:
        for row in _summary_records(f):
            # Merge rather than replace: a background agent can be recorded twice, once at
            # launch with the prompt and once at collection with the numbers.
            out[row["agent_id"]] = _merge(out.get(row["agent_id"]) or {}, row)
    return out
