"""Workflow runs — the Workflow tool's multi-agent orchestration — for the Studio dashboard.

Claude Code journals every workflow run to disk; the Studio reads those journals so it can show
agents working through phases, live. TWO on-disk shapes exist and BOTH are supported:

  Consolidated journal — written when a run FINISHES:
    <project>/<session>/workflows/wf_<runId>.json
  Live, per-agent files — the only thing on disk WHILE a run is in flight:
    <project>/<session>/subagents/workflows/wf_<runId>/agent-<id>.jsonl
    <project>/<session>/workflows/scripts/<name>-wf_<runId>.js   (carries the workflow name)

  Loose Task-tool subagents — a fan-out that is NOT a Workflow-tool run, so it has NO wf_ journal
  and NO wf_ dir; the transcripts sit DIRECTLY under subagents/:
    <project>/<session>/subagents/agent-<id>.jsonl
  Mission Control shows these (it rglobs EVERY agent-*.jsonl); the Workflows tab now surfaces each
  such live session as a synthetic run (id "sub-<session>"). This was the long-standing gap where
  "agents working right now" showed in the Control tab but never here — because those agents are
  plain Task subagents, not a Workflow-tool run.

A run is "live" when its agent transcripts were touched in the last LIVE_WINDOW seconds — LIVE_WINDOW
is pinned to Mission Control's own window (mission.AGENT_ACTIVE_WINDOW), so a live agent appears and
disappears in BOTH tabs in lockstep. Live runs + their agents show even before any consolidated
journal exists (mid-run there is no journal, which is why live agents used to vanish here).
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import Optional

from . import mission

# Pin to Mission Control's window EXACTLY so a live agent shows/hides in the Workflows tab in lockstep
# with the Control tab — same signal, so they must agree (this was 90s here vs 180s in Control before).
LIVE_WINDOW = float(mission.AGENT_ACTIVE_WINDOW)   # agent transcript touched within this ⇒ live
LOOSE_PREFIX = "sub-"                    # synthetic run id for a batch of plain Task-tool subagents
LOOSE_SHOW_WINDOW = 600.0                # agents within 10 min of the batch's own activity belong to it
# A loose fan-out has no journal, so when its agents went idle the whole run VANISHED from the
# tab — you could watch it work and then never review it. The transcripts are still on disk, so
# a finished batch stays listed (marked done) for this long, newest first and capped.
LOOSE_KEEP_WINDOW = 14 * 24 * 3600.0
LOOSE_MAX_RUNS = 15
_DONE_STATUS = {"completed", "killed", "error", "failed", "canceled", "cancelled"}

# parse caches: a finished journal is re-read only when its mtime changes; live (journal-less) runs
# are cheap and few, so they are re-derived from disk every call.
_sum_cache: dict[str, tuple[float, dict]] = {}
_name_cache: dict[str, str] = {}


# --- paths ------------------------------------------------------------------
def _projects_root() -> Path:
    return mission.claude_projects_dir()


def _project_name(pid: str, pdir: Path) -> str:
    """Friendly project name (the working-dir basename), cached per project id."""
    if pid in _name_cache:
        return _name_cache[pid]
    name = pid
    try:
        jsonls = list(pdir.glob("*.jsonl"))
        if jsonls:
            newest = max(jsonls, key=lambda f: f.stat().st_mtime)
            cwd = mission._meta_from_head(newest).get("cwd")
            if cwd:
                name = Path(cwd).name or pid
    except Exception:
        pass
    _name_cache[pid] = name
    return name


def _iter_runs():
    """Yield (project_id, project_dir, run_id, journal|None, agent_dir|None) for every workflow run
    — discovered from the consolidated journal AND/OR the live per-agent directory, so a run that
    only has per-agent files (i.e. it's still running) is included."""
    root = _projects_root()
    if not root.exists():
        return
    for pdir in root.iterdir():
        if not pdir.is_dir():
            continue
        runs: dict[str, dict] = {}
        try:
            for jp in pdir.glob("*/workflows/wf_*.json"):           # finished-run journal
                runs.setdefault(jp.stem, {})["journal"] = jp
            for ad in pdir.glob("*/subagents/workflows/wf_*"):      # live per-agent dir
                if ad.is_dir():
                    runs.setdefault(ad.name, {})["agent_dir"] = ad
        except OSError:
            continue
        for rid, p in runs.items():
            yield pdir.name, pdir, rid, p.get("journal"), p.get("agent_dir")


def _run_agent_dir(pdir: Path, run_id: str) -> Optional[Path]:
    safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
    try:
        for ad in pdir.glob(f"*/subagents/workflows/{safe}"):
            if ad.is_dir():
                return ad
    except OSError:
        pass
    return None


def _find_journal(pdir: Path, run_id: str) -> Optional[Path]:
    safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
    try:
        for jp in pdir.glob("*/workflows/wf_*.json"):
            if jp.stem in (safe, f"wf_{safe}") or jp.stem.endswith(safe):
                return jp
    except OSError:
        pass
    return None


def _scripts_name(pdir: Path, run_id: str, session: str = "") -> str:
    """Workflow name from <session>/workflows/scripts/<name>-wf_<id>.js (live runs have no journal).

    The script is saved under the project folder of the directory the CLI was IN when it called
    the tool, and the agents under the folder of the session's project. Those differ whenever the
    shell had moved into a subfolder: a run launched from backend/ put its script under
    `...-STUDIO-backend` and its agents under `...-STUDIO`, and the tab named it "workflow". The
    session id is the same in both places, so the second look is by session."""
    safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
    globs = [(pdir, f"*/workflows/scripts/*{safe}*.js")]
    sess = "".join(c for c in session if c.isalnum() or c in "-_")
    if sess:
        globs.append((_projects_root(), f"*/{sess}/workflows/scripts/*{safe}*.js"))
    for base, pat in globs:
        try:
            for sp in base.glob(pat):
                nm = sp.stem
                cut = nm.rfind("-wf_")
                nm = nm[:cut] if cut > 0 else nm
                return nm.strip() or "workflow"
        except OSError:
            continue
    return "workflow"


# --- parsing: consolidated journal ------------------------------------------
def _norm_state(s: Optional[str]) -> str:
    s = (s or "").lower()
    if s in ("done", "complete", "completed", "success", "succeeded"):
        return "done"
    if s in ("error", "failed", "fail"):
        return "error"
    if s in ("queued", "pending"):
        return "queued"
    return "running"   # start / progress / running / anything in-flight


def _start_ms(d: dict) -> int:
    st = d.get("startTime")
    if isinstance(st, (int, float)):
        return int(st) if st > 1e11 else int(st * 1000) if st > 1e9 else 0
    ts = d.get("timestamp")
    if isinstance(ts, str):
        try:
            return int(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            pass
    return 0


def _agents_from_progress(wp: list) -> list[dict]:
    """Flatten workflowProgress agent entries → one clean record per agent (latest state)."""
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for e in wp or []:
        if not isinstance(e, dict) or e.get("type") != "workflow_agent":
            continue
        aid = e.get("agentId") or f"i{e.get('index')}"
        if aid not in by_id:
            order.append(aid)
        by_id[aid] = e
    agents = []
    for aid in order:
        e = by_id[aid]
        agents.append({
            "index": e.get("index"),
            "agentId": aid,
            "label": e.get("label") or "",
            "phaseIndex": e.get("phaseIndex") or 0,
            "phaseTitle": e.get("phaseTitle") or "",
            "model": e.get("model") or "",
            "state": _norm_state(e.get("state")),
            "rawState": e.get("state") or "",
            "tokens": int(e.get("tokens") or 0),
            "toolCalls": int(e.get("toolCalls") or 0),
            "durationMs": int(e.get("durationMs") or 0),
            "lastTool": e.get("lastToolName") or "",
            "lastToolSummary": e.get("lastToolSummary") or "",
            "promptPreview": e.get("promptPreview") or "",
            "resultPreview": e.get("resultPreview") or "",
            "startedAt": e.get("startedAt"),
            "queuedAt": e.get("queuedAt"),
            "lastProgressAt": e.get("lastProgressAt"),
            "attempt": e.get("attempt") or 1,
        })
    return agents


def _phases(d: dict, agents: list[dict]) -> list[dict]:
    """Phase rows (title + detail + roll-up of their agents' state) for the pipeline view."""
    raw = d.get("phases") or []
    rows: list[dict] = []
    if raw:
        defs = [(i + 1, (p.get("title") if isinstance(p, dict) else str(p)),
                 (p.get("detail") if isinstance(p, dict) else "")) for i, p in enumerate(raw)]
    else:
        seen: dict[int, str] = {}
        for a in agents:
            pi = a.get("phaseIndex") or 0
            if pi and pi not in seen:
                seen[pi] = a.get("phaseTitle") or f"Phase {pi}"
        defs = [(pi, seen[pi], "") for pi in sorted(seen)] or [(0, "Run", "")]
    for idx, title, detail in defs:
        mine = [a for a in agents if (a.get("phaseIndex") or 0) == idx]
        done = sum(1 for a in mine if a["state"] == "done")
        err = sum(1 for a in mine if a["state"] == "error")
        run = sum(1 for a in mine if a["state"] == "running")
        if run:
            state = "running"
        elif mine and (done + err) == len(mine):
            state = "done"
        elif mine:
            state = "running"
        else:
            state = "pending"
        rows.append({"index": idx, "title": title, "detail": detail,
                     "agentCount": len(mine), "done": done, "error": err,
                     "running": run, "state": state})
    return rows


def _task_str(d: dict, limit: int = 0) -> str:
    a = d.get("args")
    s = a if isinstance(a, str) else (json.dumps(a, ensure_ascii=False) if a not in (None, "") else "")
    return s[:limit] if limit else s


# --- parsing: live per-agent transcripts ------------------------------------
def _agent_label(af: Path) -> str:
    """A short label for a workflow agent = its first user prompt (first line of its jsonl)."""
    try:
        with af.open("r", encoding="utf-8", errors="ignore") as fh:
            first = fh.readline()
        o = json.loads(first)
        c = (o.get("message") or {}).get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip():
                    return b["text"].strip()[:140]
        elif isinstance(c, str) and c.strip():
            return c.strip()[:140]
        if o.get("slug"):
            return str(o["slug"])[:140]
    except Exception:
        pass
    return af.stem.replace("agent-", "")[:12]


_USAGE_CACHE: dict[str, tuple] = {}      # path -> (mtime, size, tokens, tool_calls, prompt)
_USAGE_CACHE_MAX = 400


def _real_tokens(af: Path) -> int:
    """An agent's NEW tokens (input, cache writes, output; each API call once), from the same
    ledger as the subagent card - so a card, a pill and this tab never disagree about one agent.
    See agent_usage for why neither the journal's figure nor a sum over lines is that number."""
    try:
        from . import subagents
        return int(subagents._ledger(af).get("tokens") or 0)
    except Exception:
        return 0


def _agent_usage(af: Path) -> tuple[int, int, str, str, dict, dict, float, float]:
    """(new tokens, tool calls, the FULL prompt) read from one agent's transcript.

    The consolidated journal — which is where per-agent tokens normally come from — is only
    written when a run FINISHES. So while a run was live, every agent reported 0 tokens: exactly
    when you are watching, the number was meaningless. The transcript is on disk the whole time,
    so the same figures are derived from it.

    Cheap on purpose: a substring test skips lines that cannot contain either, so a large
    transcript costs a scan rather than a full JSON parse, and the result is cached on the file's
    (mtime, size) — a live agent only re-reads when it has actually written something."""
    try:
        st = af.stat()
    except OSError:
        return (0, 0, "", "", {}, {}, 0.0, 0.0)
    key = str(af)
    hit = _USAGE_CACHE.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2:]
    tokens = tools = 0
    prompt = ""
    model = ""
    counts: dict[str, int] = {}
    files: dict[str, int] = {}
    first_ts = last_ts = 0.0
    try:
        with af.open("rb") as fh:
            for i, raw in enumerate(fh):
                if i == 0:
                    prompt = _first_prompt(raw)
                if (b'"output_tokens"' not in raw and b'"tool_use"' not in raw
                        and b'"model"' not in raw and b'"timestamp"' not in raw):
                    continue          # nothing we need on this line — skip the parse
                try:
                    o = json.loads(raw)
                except Exception:
                    continue
                ts = _ts_ms(o.get("timestamp"))
                if ts:
                    first_ts = ts if not first_ts else min(first_ts, ts)
                    last_ts = max(last_ts, ts)
                msg = o.get("message") or {}
                if not model and msg.get("model"):
                    model = str(msg["model"])      # what is ACTUALLY serving this agent
                for b in (msg.get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tools += 1
                        nm = str(b.get("name") or "tool")
                        counts[nm] = counts.get(nm, 0) + 1
                        # which files it CHANGED — the most useful thing to know about an agent
                        # after the fact, and it is already sitting in the tool input
                        if nm in ("Edit", "Write", "NotebookEdit") and len(files) < 60:
                            fp = (b.get("input") or {}).get("file_path")
                            if fp:
                                files[str(fp)] = files.get(str(fp), 0) + 1
    except OSError:
        return (0, 0, prompt, "", {}, {}, 0.0, 0.0)
    # It added up `output_tokens` line by line: output only, and the partial counts of a call's
    # early lines on top. The Workflows tab then said one thing while a run was live and another
    # (the journal's context size) once it had finished.
    tokens = _real_tokens(af)
    if len(_USAGE_CACHE) >= _USAGE_CACHE_MAX:
        _USAGE_CACHE.clear()
    val = (tokens, tools, prompt, model, counts, files, first_ts, last_ts)
    _USAGE_CACHE[key] = (st.st_mtime, st.st_size, *val)
    return val


def _ts_ms(ts) -> float:
    """A transcript timestamp as epoch ms (they are ISO strings), or 0."""
    if isinstance(ts, (int, float)):
        return float(ts) if ts > 1e11 else float(ts) * 1000
    if isinstance(ts, str) and ts:
        try:
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            return 0.0
    return 0.0


def _first_prompt(raw: bytes) -> str:
    """The COMPLETE first user message — what the workflow script actually told this agent to do.
    ``_agent_label`` truncates the same text to 140 chars for the card; this is the full thing."""
    try:
        o = json.loads(raw)
    except Exception:
        return ""
    c = (o.get("message") or {}).get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts = [b.get("text", "") for b in c
                 if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip()]
        if parts:
            return "\n".join(parts).strip()
    return ""


def _journal_of(ad: Path) -> Optional[dict]:
    """The run's own journal, for a Workflow run; None for a loose batch of Task agents."""
    if not ad.name.startswith("wf_"):
        return None
    try:
        from . import subagents
        return subagents.wf_journal(ad)
    except Exception:
        return None


def _agent_live(af: Path, mt: float, now: float, jr: Optional[dict]) -> bool:
    """Is this agent still working?

    "Written within LIVE_WINDOW" was the only test, and for a Workflow agent it is wrong in both
    directions. One thinking through a long turn writes nothing for minutes, so it was shown DONE
    while it worked — the tab read "running 3, done 1" with none of the four finished. The run's
    journal knows better: a "result" line means done, and no result means it is still owed one,
    for as long as the rail and the bottom bar say so too (the subagents module's open window)."""
    if jr is None:
        return (now - mt) < LIVE_WINDOW
    if af.stem[len("agent-"):] in jr["done"]:
        return False
    try:
        from . import subagents
        window = subagents.OPEN_WINDOW
    except Exception:
        window = LIVE_WINDOW
    return (now - mt) < window


def _dir_liveness(ad: Optional[Path], now: float) -> tuple[bool, int, float]:
    """(is_live, running_count, newest_mtime) from a run's agent transcripts (mtime = activity)."""
    if not ad:
        return (False, 0, 0.0)
    jr = _journal_of(ad)
    newest = 0.0
    running = 0
    try:
        for af in ad.glob("agent-*.jsonl"):
            try:
                mt = af.stat().st_mtime
            except OSError:
                continue
            newest = max(newest, mt)
            if _agent_live(af, mt, now, jr):
                running += 1
    except OSError:
        pass
    return (running > 0, running, newest)


def _agents_from_dir(ad: Path, now: float, since: float = 0.0,
                     until: float = 0.0) -> list[dict]:
    """Build the agent list straight from per-agent transcripts (live runs have no journal).
    ``since`` (epoch secs, 0 = no cut) keeps only agents touched after it — used to scope a loose
    subagent batch to its recent agents instead of a whole session's accumulated history."""
    agents: list[dict] = []
    try:
        files = sorted(ad.glob("agent-*.jsonl"))
    except OSError:
        files = []
    # A Workflow run names its agents and their phases in its journal and in each agent's
    # .meta.json. Without them every card was titled with the harness preamble its prompt
    # starts with, and every agent sat in one phase called "Run".
    jr = _journal_of(ad)
    phases = list(jr["phases"]) if jr is not None else []   # a copy: the journal dict is cached
    for af in files:
        try:
            mt = af.stat().st_mtime
        except OSError:
            continue
        if since and mt < since:
            continue
        if until and mt > until:
            continue
        running = _agent_live(af, mt, now, jr)
        label = ""
        phase_title = ""
        phase_index = 0
        if jr is not None:
            aid = af.stem[len("agent-"):]
            try:
                from . import subagents
                meta = subagents.agent_meta(af)
            except Exception:
                meta = {}
            label = jr["label"].get(aid) or str(meta.get("description") or "")
            phase_title = jr["phase"].get(aid) or str(meta.get("workflowPhase") or "")
            if phase_title:
                if phase_title not in phases:
                    phases.append(phase_title)
                phase_index = phases.index(phase_title) + 1
        label = label[:140] or _agent_label(af)
        # real figures from the transcript — the journal that normally carries them does not
        # exist until the run finishes, which is why a live run used to show 0 tokens per agent
        tokens, tools, prompt, model, tool_counts, files, first_ts, last_ts = _agent_usage(af)
        agents.append({
            "index": len(agents), "agentId": af.stem.replace("agent-", ""), "label": label,
            "phaseIndex": phase_index, "phaseTitle": phase_title, "model": model,
            "state": "running" if running else "done",
            "rawState": "running" if running else "done",
            "tokens": tokens, "toolCalls": tools,
            "durationMs": int(last_ts - first_ts) if (first_ts and last_ts > first_ts) else 0,
            "startedMs": int(first_ts) if first_ts else 0,
            "toolCounts": tool_counts, "files": files,
            "lastTool": "", "lastToolSummary": "", "promptPreview": prompt or label,
            "resultPreview": "",
            "startedAt": None, "queuedAt": None, "lastProgressAt": int(mt * 1000), "attempt": 1,
        })
    return agents


def _overlay_real_tokens(agents: list[dict], agent_dir: Optional[Path]) -> Optional[int]:
    """Replace each agent's journal `tokens` with what its own transcript says it used.

    The journal's figure is Claude Code's `totalTokens`: the usage of the agent's LAST call added
    up, which is the size of its context when it stopped - 533k for the v3.1 tree agent, whose new
    tokens were 747k and whose calls moved 33.6M in all. Returns the run's total, or None when no
    transcript is on disk (then the journal's figures stay, for want of anything better)."""
    if agent_dir is None or not agents:
        return None
    total, found = 0, False
    for a in agents:
        f = agent_dir / ("agent-%s.jsonl" % a.get("agentId"))
        if f.is_file():
            a["tokens"] = _real_tokens(f)
            found = True
        total += int(a.get("tokens") or 0)
    return total if found else None


def _summarize(pid: str, pdir: Path, jp: Path, agent_dir: Optional[Path], full: bool = False) -> Optional[dict]:
    """Summary from the consolidated journal, with live state overlaid from the agent transcripts
    when the run is still in flight (a journal can be written before the run truly finishes)."""
    try:
        d = json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return None
    run_id = d.get("runId") or jp.stem
    status = (d.get("status") or "").lower()
    try:
        mtime = jp.stat().st_mtime
    except OSError:
        mtime = 0.0
    now = time.time()
    agents = _agents_from_progress(d.get("workflowProgress") or [])
    real_total = _overlay_real_tokens(agents, agent_dir)
    phases = _phases(d, agents)
    done = sum(1 for a in agents if a["state"] == "done")
    err = sum(1 for a in agents if a["state"] == "error")
    running = sum(1 for a in agents if a["state"] == "running")
    live = (status == "running") or (status not in _DONE_STATUS and (now - mtime) < LIVE_WINDOW)
    # Overlay live activity from the per-agent transcripts for non-finished runs.
    if status not in _DONE_STATUS:
        d_live, d_running, d_newest = _dir_liveness(agent_dir, now)
        if d_live:
            live = True
            running = max(running, d_running)
            mtime = max(mtime, d_newest)
    cur_phase = max([a.get("phaseIndex") or 0 for a in agents], default=0)
    out = {
        "runId": run_id,
        "project_id": pid,
        "project_name": _project_name(pid, pdir),
        "name": d.get("workflowName") or "workflow",
        "task": _task_str(d, 320),
        "description": (d.get("summary") or "")[:220],
        "status": status or ("running" if live else "done"),
        "live": bool(live),
        "agentCount": int(d.get("agentCount") or len(agents)),
        "done": done, "error": err, "running": running,
        "phaseCount": len(phases), "curPhase": cur_phase,
        "totalTokens": real_total if real_total is not None else int(d.get("totalTokens") or 0),
        "totalToolCalls": int(d.get("totalToolCalls") or 0),
        "durationMs": int(d.get("durationMs") or 0),
        "startMs": _start_ms(d),
        "updatedMs": int(mtime * 1000),
        "errorMsg": (d.get("error") or "")[:300] if d.get("error") else "",
    }
    if full:
        res = d.get("result")
        out["phases"] = phases
        out["agents"] = agents
        out["logs"] = [(l if isinstance(l, str) else json.dumps(l, ensure_ascii=False))
                       for l in (d.get("logs") or [])][-300:]
        out["defaultModel"] = d.get("defaultModel") or ""
        out["scriptName"] = Path(d.get("scriptPath") or "").name
        out["taskFull"] = _task_str(d)
        out["summary"] = d.get("summary") or ""
        out["result"] = (res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, indent=1))[:12000] if res else ""
    return out


def _rollup(out: dict, agents: list[dict], newest: float) -> None:
    """Header figures derived from the agents themselves.

    Tokens, tool calls, elapsed and start all normally come from the consolidated journal, which
    is only written when a run FINISHES — so a live run showed "0 TOKENS · 0 TOOL CALLS · 0s
    ELAPSED" in the header while the agent cards underneath it read 182k and 47 tools. The agents
    already carry the real numbers; the header just has to add them up."""
    out["totalTokens"] = sum(a.get("tokens") or 0 for a in agents)
    out["totalToolCalls"] = sum(a.get("toolCalls") or 0 for a in agents)
    starts = [a["startedMs"] for a in agents if a.get("startedMs")]
    if starts:
        out["startMs"] = min(starts)
        # elapsed = first agent started -> last activity seen, which for a live run keeps ticking
        out["durationMs"] = max(0, int(newest * 1000) - out["startMs"])
    tools: dict[str, int] = {}
    for a in agents:
        for k, v in (a.get("toolCounts") or {}).items():
            tools[k] = tools.get(k, 0) + v
    out["toolCounts"] = sorted(({"name": k, "count": v} for k, v in tools.items()),
                               key=lambda x: -x["count"])
    models = sorted({a.get("model") for a in agents if a.get("model")})
    out["models"] = models
    if models and not out.get("defaultModel"):
        out["defaultModel"] = models[0]


def _summarize_dir(pid: str, pdir: Path, run_id: str, ad: Path, full: bool = False) -> dict:
    """Summary for a run that has only per-agent transcripts (i.e. it's running, no journal yet)."""
    now = time.time()
    agents = _agents_from_dir(ad, now)
    phases = _phases({}, agents)
    is_live, running, newest = _dir_liveness(ad, now)
    done = sum(1 for a in agents if a["state"] == "done")
    out = {
        "runId": run_id,
        "project_id": pid,
        "project_name": _project_name(pid, pdir),
        "name": _scripts_name(pdir, run_id, ad.parent.parent.parent.name),
        "task": "",
        "description": "",
        "status": "running" if is_live else "done",
        "live": bool(is_live),
        "agentCount": len(agents),
        "done": done, "error": 0, "running": running,
        "phaseCount": len(phases), "curPhase": 0,
        # Roll the agents up, or the header reads "0 tokens" while the cards below it add up to
        # thousands — the run-level figure comes from the journal, which does not exist yet.
        "totalTokens": 0, "totalToolCalls": 0, "durationMs": 0, "startMs": 0,
        "updatedMs": int(newest * 1000),
        "errorMsg": "",
    }
    _rollup(out, agents, newest)
    if full:
        out["phases"] = phases
        out["agents"] = agents
        out["logs"] = []
        out["defaultModel"] = ""
        out["scriptName"] = ""
        out["taskFull"] = ""
        out["summary"] = ""
        out["result"] = ""
    return out


# --- parsing: loose Task-tool subagents (a fan-out, NOT a Workflow-tool run) ---
def _loose_batches(subdir: Path) -> list[tuple[float, float, list]]:
    """A session's subagents/ accumulates EVERY Task agent it ever spawned, so one folder is
    usually several separate fan-outs. Cluster them by activity gap — measured on a real session:
    3 agents, a 4-hour gap, 6 agents, an hour gap, 3 agents — three runs, not twelve loose files.
    Returns (first_mtime, last_mtime, files) per batch, oldest first."""
    try:
        fs = sorted(((f.stat().st_mtime, f) for f in subdir.glob("agent-*.jsonl")),
                    key=lambda x: x[0])
    except OSError:
        return []
    batches: list[tuple[float, float, list]] = []
    cur: list = []
    for mt, f in fs:
        if cur and (mt - cur[-1][0]) > LOOSE_SHOW_WINDOW:
            batches.append((cur[0][0], cur[-1][0], [x[1] for x in cur]))
            cur = []
        cur.append((mt, f))
    if cur:
        batches.append((cur[0][0], cur[-1][0], [x[1] for x in cur]))
    return batches


def _loose_run_id(session_id: str, first_mt: float) -> str:
    """Stable id for one batch. Anchored to its FIRST activity, so a live batch keeps the same id
    as more agents join it (anchoring to the newest would change it mid-run and drop the UI's
    selection)."""
    return f"{LOOSE_PREFIX}{session_id}@{int(first_mt)}"


def _loose_anchor(run_id: str) -> float:
    """The batch timestamp out of a batch run id, or 0 for an old id with no suffix."""
    raw = run_id[len(LOOSE_PREFIX):]
    if "@" in raw:
        try:
            return float(raw.split("@", 1)[1])
        except ValueError:
            return 0.0
    return 0.0


def _split_loose_id(run_id: str) -> str:
    """The session id back out of a batch run id (ids carry an @<timestamp> suffix)."""
    raw = run_id[len(LOOSE_PREFIX):]
    return raw.split("@", 1)[0]


def _loose_stats(subdir: Path, now: float) -> tuple[int, int, float]:
    """(agents_in_show_window, running_now, newest_mtime) for a session's plain subagents — the
    agent-*.jsonl DIRECTLY under subagents/ (a NON-recursive glob, so subagents/workflows/wf_*/ is
    excluded and never double-counted with wf runs). mtime = activity, exactly like Mission Control."""
    total = running = 0
    newest = 0.0
    try:
        mtimes = []
        for af in subdir.glob("agent-*.jsonl"):
            try:
                mtimes.append(af.stat().st_mtime)
            except OSError:
                continue
        newest = max(mtimes) if mtimes else 0.0
        for mt in mtimes:
            # grouped around the batch's OWN last activity, so a finished fan-out still has its
            # agents (anchoring this to `now` made an idle batch report zero of them)
            if (newest - mt) < LOOSE_SHOW_WINDOW:
                total += 1
                if (now - mt) < LIVE_WINDOW:
                    running += 1
    except OSError:
        pass
    return total, running, newest


def _loose_summary(pid: str, pdir: Path, sid: str, subdir: Path,
                   total: int, running: int, newest: float, now: float, full: bool = False,
                   first: float = 0.0) -> dict:
    """Summarize a batch of loose Task-tool subagents as a synthetic run (runId 'sub-<session>')."""
    is_live = running > 0
    out = {
        "runId": _loose_run_id(sid, first or newest),
        "project_id": pid,
        "project_name": _project_name(pid, pdir),
        "name": "subagents",
        "task": ("live Task-tool subagents" if is_live else "Task-tool subagents"),
        "description": "A fan-out of Task-tool subagents (not a Workflow-tool run).",
        "status": "running" if is_live else "done",
        "live": bool(is_live),
        "agentCount": total,
        "done": max(0, total - running), "error": 0, "running": running,
        "phaseCount": 1, "curPhase": 0,
        "totalTokens": 0, "totalToolCalls": 0, "durationMs": 0,
        "startMs": 0,
        "updatedMs": int(newest * 1000),
        "errorMsg": "",
    }
    if full:
        agents = _agents_from_dir(subdir, now, since=(first or newest) - 1,
                                  until=newest + 1)
        out["agents"] = agents
        out["phases"] = _phases({}, agents)
        out["phaseCount"] = len(out["phases"])
        out["agentCount"] = len(agents)
        out["done"] = sum(1 for a in agents if a["state"] == "done")
        out["running"] = sum(1 for a in agents if a["state"] == "running")
        _rollup(out, agents, newest)
        out["logs"] = []
        out["defaultModel"] = ""
        out["scriptName"] = ""
        out["taskFull"] = ""
        out["summary"] = ""
        out["result"] = ""
    return out


def _loose_runs(now: float, full: bool = False):
    """Yield a synthetic summary for every session with LIVE loose Task-tool subagents — so the
    Workflows tab shows 'agents working right now' the same as the Control tab (which rglobs these
    very transcripts). Only live batches surface: a loose fan-out has no wf_ journal to persist a
    finished run, and dropping it when idle keeps this in lockstep with Mission Control."""
    root = _projects_root()
    if not root.exists():
        return
    found = []
    for pdir in root.iterdir():
        if not pdir.is_dir():
            continue
        try:
            subdirs = [s for s in pdir.glob("*/subagents") if s.is_dir()]
        except OSError:
            continue
        for subdir in subdirs:
            for first, last, files in _loose_batches(subdir):
                if (now - last) > LOOSE_KEEP_WINDOW:
                    continue          # too old to be worth listing
                running = sum(1 for f in files if (now - f.stat().st_mtime) < LIVE_WINDOW)
                found.append((last, first, pdir, subdir, len(files), running))
    found.sort(key=lambda x: -x[0])
    for last, first, pdir, subdir, total, running in found[:LOOSE_MAX_RUNS]:
        yield _loose_summary(pdir.name, pdir, subdir.parent.name, subdir,
                             total, running, last, now, full, first=first)


# --- public api -------------------------------------------------------------
def list_workflows(limit: int = 80) -> dict:
    """Every workflow run (live first, then most-recent). Finished runs are cached by mtime;
    live (journal-less) runs are re-derived from disk each call so their agents tick in real time."""
    items: list[dict] = []
    for pid, pdir, rid, jp, ad in _iter_runs():
        if jp is not None:
            try:
                mt = jp.stat().st_mtime
            except OSError:
                continue
            key = str(jp)
            cached = _sum_cache.get(key)
            # only re-stat the agent dir for a possibly-live journal (recent mtime); finished runs
            # short-circuit via the cache so polling stays cheap across a big history.
            recent = (time.time() - mt) < LIVE_WINDOW
            if cached and cached[0] == mt and not recent:
                s = cached[1]
            else:
                # The agent folder always: the run's tokens come from its agents' transcripts
                # (read once, then remembered on disk). A finished run's status stops the live
                # overlay from touching it; the cache above keeps it from being re-derived.
                s = _summarize(pid, pdir, jp, ad, full=False)
                if s is None:
                    continue
                _sum_cache[key] = (mt, s)
            items.append(s)
        elif ad is not None:
            items.append(_summarize_dir(pid, pdir, rid, ad, full=False))
    # Plain Task-tool subagents (a live fan-out with no wf_ journal) — surface each live session as a
    # synthetic run so "agents working right now" appears here too, in lockstep with Mission Control.
    for s in _loose_runs(time.time(), full=False):
        items.append(s)
    items.sort(key=lambda x: (not x["live"], -(x.get("updatedMs") or 0)))
    return {"workflows": items[:limit], "live": sum(1 for x in items if x["live"]), "total": len(items)}


def get_workflow(project_id: str, run_id: str) -> Optional[dict]:
    root = _projects_root()
    pdir = root / project_id
    if not pdir.exists():
        return None
    if run_id.startswith(LOOSE_PREFIX):       # synthetic loose Task-subagent run
        sid = "".join(c for c in _split_loose_id(run_id) if c.isalnum() or c in "-_")
        subdir = pdir / sid / "subagents"
        if not subdir.is_dir():
            return None
        now = time.time()
        # Pick the BATCH this id names. Using session-wide stats here showed one agent out of the
        # whole folder, because the window then collapsed onto the single newest transcript.
        batches = _loose_batches(subdir)
        if not batches:
            return None
        anchor = _loose_anchor(run_id)
        pick = None
        if anchor:
            pick = min(batches, key=lambda b: abs(b[0] - anchor))
            if abs(pick[0] - anchor) > LOOSE_SHOW_WINDOW:
                pick = None
        first, last, files = pick or batches[-1]
        running = sum(1 for f in files if (now - f.stat().st_mtime) < LIVE_WINDOW)
        return _loose_summary(project_id, pdir, sid, subdir, len(files), running, last, now,
                              full=True, first=first)
    jp = _find_journal(pdir, run_id)
    ad = _run_agent_dir(pdir, run_id)
    if jp is not None:
        return _summarize(project_id, pdir, jp, ad, full=True)
    if ad is not None:
        return _summarize_dir(project_id, pdir, run_id, ad, full=True)
    return None


# --- peek into a single agent's transcript ---------------------------------
_TOOL_VERB = {"Edit": "Edited", "MultiEdit": "Edited", "Write": "Wrote", "Read": "Read",
              "Bash": "Ran", "Grep": "Searched", "Glob": "Globbed", "WebSearch": "Web search",
              "WebFetch": "Fetched", "Task": "Spawned agent", "TodoWrite": "Planned",
              "StructuredOutput": "Returned result"}


def agent_tail(project_id: str, run_id: str, agent_id: str, kb: int = 80) -> dict:
    """A compact, human-readable timeline of what one workflow agent actually did
    (thinking snippets, tool calls, and its text) — for the click-to-expand agent view."""
    root = _projects_root()
    pdir = root / project_id
    if not pdir.exists():
        return {"events": [], "error": "project not found"}
    if run_id.startswith(LOOSE_PREFIX):       # loose Task-subagent → transcript sits under subagents/
        # strip the @<timestamp> batch suffix first: filtering it out character-by-character
        # glued the digits onto the session id and pointed at a folder that does not exist
        sid = "".join(c for c in _split_loose_id(run_id) if c.isalnum() or c in "-_")
        d = pdir / sid / "subagents"
    else:
        d = _run_agent_dir(pdir, run_id)
        if d is None:
            jp = _find_journal(pdir, run_id)
            if jp is not None:
                d = jp.parent.parent / "subagents" / "workflows" / jp.stem
    if d is None or not d.exists():
        return {"events": [], "error": "agent transcript not found"}
    safe = "".join(c for c in agent_id if c.isalnum() or c in "-_")
    af = None
    for cand in (d / f"agent-{safe}.jsonl", d / f"{safe}.jsonl"):
        if cand.exists():
            af = cand
            break
    if af is None:
        for f in d.glob("agent-*.jsonl"):
            if safe in f.stem:
                af = f
                break
    if af is None:
        return {"events": [], "error": "agent transcript not found"}
    # The FULL prompt this agent was given. The journal only carries a preview, and the card only
    # shows 140 characters, so "what exactly did it ask this agent to do" was unanswerable from
    # the UI. It is the first line of the transcript, read separately from the tail below.
    tokens, tools, prompt, _model, tool_counts, files, _f, _l = _agent_usage(af)
    try:
        raw = af.read_bytes()[-kb * 1024:].decode("utf-8", "ignore")
    except OSError as e:
        return {"events": [], "prompt": prompt, "error": str(e)}
    events: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "assistant":
            continue
        for b in ((o.get("message") or {}).get("content") or []):
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "thinking":
                t = (b.get("thinking") or "").strip()
                if t:
                    events.append({"kind": "thinking", "text": t[:600]})
            elif bt == "text":
                t = (b.get("text") or "").strip()
                if t:
                    events.append({"kind": "text", "text": t[:1200]})
            elif bt == "tool_use":
                name = b.get("name") or "tool"
                inp = b.get("input") or {}
                detail = ""
                if name in ("WebSearch",):
                    detail = inp.get("query") or ""
                elif name in ("WebFetch", "Read"):
                    detail = inp.get("url") or inp.get("file_path") or ""
                elif name in ("Bash",):
                    detail = (inp.get("command") or "").splitlines()[0] if inp.get("command") else ""
                elif name in ("Grep", "Glob"):
                    detail = inp.get("pattern") or ""
                events.append({"kind": "tool", "tool": name,
                               "verb": _TOOL_VERB.get(name, name), "text": str(detail)[:160]})
    return {"events": events[-80:], "file": af.name, "prompt": prompt,
            "tokens": tokens, "toolCalls": tools,
            # what it actually reached for, most-used first — the "tools" tab
            "toolCounts": sorted(({"name": k, "count": v} for k, v in tool_counts.items()),
                                 key=lambda x: -x["count"]),
            "files": sorted(({"path": k, "count": v} for k, v in files.items()),
                            key=lambda x: -x["count"])}
