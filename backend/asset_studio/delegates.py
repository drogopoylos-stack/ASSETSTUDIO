"""Work the agent handed to ANOTHER engine — shown as the subagent it is.

WHY THIS FILE EXISTS. Ask Claude for "an image of a snow leopard" and it does not draw it: it
calls the Studio's own job queue, which calls GPT (or ComfyUI, or Tripo). That is a delegation in
every sense — another model, its own prompt, its own bill, its own minutes — and it was the one
delegation the Studio could not show you. The Subagents panel, the pill under the chat and the
"who is running" list all read the Task tool's fan-out and nothing else, so a two-minute image
job looked like an agent that had simply gone quiet, and the only trace was a row on the Jobs
page that said nothing about who had asked for it.

So a job started BY A CONVERSATION is recorded against that conversation here, and read back in
the shape every subagent consumer already speaks (`subagents.list_for`). Nothing is copied: the
row holds the job id, and the status, cost and outcome are read from the job itself each time, so
a card can never disagree with the Jobs page.

WHO OWNS A JOB. Three answers, in order:
  1. `owner` in the request — the agent notes tell the agent to send it, and it is the only
     answer that is certain.
  2. the `x-studio-feed` header — what the Studio's own MCP server sends on every forwarded call.
  3. the one conversation that is mid-turn, when exactly one is, and the request did not come
     from the Studio's UI (which sends `x-studio-origin: ui`). A person pressing Generate on the
     Stages page is NOT a delegation, and must never be filed as one.
Nothing is guessed beyond that: with two agents working and no owner given, the job is filed
under neither.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from . import fsutil
from .config import DATA_DIR

_DIR = DATA_DIR / "delegates"
# Rows kept per conversation. The panel shows the newest first and a conversation that has
# generated sixty assets does not need the sixty-first remembered here — the Jobs page keeps
# the full history.
_MAX = 60
_lock = threading.Lock()
# A job that has not reached a terminal status within this long is no longer called "running":
# the backend may have been restarted under it, and a card that spins forever is worse than one
# that says it stopped.
_STALE_S = 3 * 3600

_TERMINAL = ("succeeded", "failed", "canceled")


def _file(feed: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in (feed or "unknown"))[:120]
    return _DIR / f"{safe}.json"


def _read(feed: str) -> list[dict]:
    try:
        with _file(feed).open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    rows = data.get("rows") if isinstance(data, dict) else data
    return [r for r in (rows or []) if isinstance(r, dict)]


def _write(feed: str, rows: list[dict]) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    p = _file(feed)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"rows": rows[-_MAX:]}, ensure_ascii=False), encoding="utf-8")
    fsutil.replace(tmp, p)


# --------------------------------------------------------------------------------------------
# recording
# --------------------------------------------------------------------------------------------
def note_job(feed: str, job) -> None:
    """File a submitted job against the conversation that asked for it. Never raises: a missed
    row costs a card, and a refused generation would cost the user their work."""
    if not feed:
        return
    try:
        params = dict(getattr(job, "params", None) or {})
        row = {
            "job_id": str(getattr(job, "id", "") or ""),
            "at": float(getattr(job, "created_at", 0) or time.time()),
            "stage": str(getattr(getattr(job, "stage", ""), "value", getattr(job, "stage", "")) or ""),
            "provider": str(getattr(job, "provider_id", "") or ""),
            "label": str(getattr(job, "label", "") or ""),
            "prompt": str(params.get("prompt") or params.get("text") or "")[:2000],
            "model": str(params.get("model") or params.get("model_name") or ""),
        }
        if not row["job_id"]:
            return
        with _lock:
            rows = [r for r in _read(feed) if r.get("job_id") != row["job_id"]]
            rows.append(row)
            _write(feed, rows)
    except Exception:
        pass


def forget(feed: str) -> None:
    """Drop this conversation's rows — used by the tests and by a cleared conversation."""
    try:
        _file(feed).unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------------
def _job(job_id: str):
    try:
        from .jobs.queue import queue
        return queue.get(job_id)
    except Exception:
        return None


def _provider_name(pid: str) -> str:
    try:
        from .providers.registry import get_provider
        p = get_provider(pid)
        info = getattr(p, "info", None)
        name = getattr(info, "name", "") if info is not None else ""
        return str(name or pid)
    except Exception:
        return pid


_STAGE_WORD = {"image2d": "image", "process2d": "image edit", "gen3d": "3D model",
               "texture": "texture", "rig": "rig", "optimize": "optimize", "qa": "check"}


def _card(row: dict) -> Optional[dict]:
    """One job as a subagent card. `None` when the job is no longer on record at all."""
    job = _job(str(row.get("job_id") or ""))
    at = float(row.get("at") or 0)
    stage = str(row.get("stage") or "")
    provider = str(row.get("provider") or "")
    status = str(getattr(getattr(job, "status", ""), "value", "") or "") if job is not None else ""
    if job is None and not status:
        status = "gone"
    started = float(getattr(job, "started_at", 0) or at) if job is not None else at
    finished = float(getattr(job, "finished_at", 0) or 0) if job is not None else 0
    live = status not in _TERMINAL and status != "gone"
    if live and not finished and time.time() - max(at, started) > _STALE_S:
        live, status = False, "stopped"
    ms = int(1000 * ((finished or time.time()) - (started or at))) if (started or at) else 0
    out_file = ""
    result = ""
    if job is not None:
        for a in (getattr(job, "outputs", None) or []):
            out_file = str(getattr(a, "path", "") or "")
            if out_file:
                break
        result = out_file or str(getattr(job, "error", "") or "")
    name = _provider_name(provider)
    word = _STAGE_WORD.get(stage, stage or "job")
    model = str(row.get("model") or "")
    desc = f"{word} · {name}" + (f" ({model})" if model else "")
    return {
        "agent_id": "job-" + str(row.get("job_id") or ""),
        "description": desc,
        "agent_type": provider or "studio-job",
        "model": model or provider,
        # The panels tell a Task-tool agent from work sent to another engine by this.
        "source": "studio",
        "status": status or "queued",
        "running": bool(live),
        # `open` is what the lists filter on ("dispatched, no outcome yet"), and a queued job is
        # exactly that: waiting its turn behind another generation.
        "open": bool(live),
        "prompt": str(row.get("prompt") or ""),
        "result": result[:4000],
        "output_file": out_file,
        "ms": ms,
        "tools": 0,
        "tokens": 0,
        "cost": float(getattr(job, "cost", 0) or 0) if job is not None else 0.0,
        "ts": at,
        "job_id": str(row.get("job_id") or ""),
        "step": str(getattr(job, "step", "") or "") if job is not None else "",
        "progress": float(getattr(job, "progress", 0) or 0) if job is not None else 0.0,
    }


def cards(feed: str) -> list[dict]:
    """This conversation's delegated jobs as subagent cards, newest first."""
    out = []
    for row in _read(feed):
        try:
            c = _card(row)
        except Exception:
            c = None
        if c:
            out.append(c)
    out.sort(key=lambda c: c.get("ts") or 0, reverse=True)
    return out


def merge(feed: str, data: dict) -> dict:
    """Add this conversation's job cards to a subagents answer, and keep its totals honest."""
    try:
        cs = cards(feed)
    except Exception:
        return data
    if not cs:
        return data
    agents = list(data.get("agents") or []) + cs
    agents.sort(key=lambda a: a.get("ts") or 0, reverse=True)
    out = dict(data)
    out["agents"] = agents
    out["running"] = int(data.get("running") or 0) + sum(1 for c in cs if c.get("running"))
    out["open"] = int(data.get("open") or 0) + sum(1 for c in cs if c.get("open"))
    out["delegated"] = len(cs)
    return out


def detail(feed: str, agent_id: str, limit: int = 400) -> Optional[dict]:
    """The pane view of one delegated job: the card, and the job's own log as a timeline.

    `None` when the id is not a job of this conversation, so the caller falls through to the
    engine's own reader.
    """
    if not agent_id.startswith("job-"):
        return None
    jid = agent_id[4:]
    row = next((r for r in _read(feed) if str(r.get("job_id") or "") == jid), None)
    if row is None:
        return None
    card = _card(row) or {}
    job = _job(jid)
    lines = []
    if card.get("prompt"):
        lines.append({"kind": "user", "ts": _hms(card.get("ts") or 0), "at": card.get("ts") or 0,
                      "text": card["prompt"]})
    for msg in (list(getattr(job, "logs", None) or [])[-limit:] if job is not None else []):
        lines.append({"kind": "tool", "ts": "", "at": 0, "text": str(msg)})
    if card.get("output_file"):
        lines.append({"kind": "text", "ts": "", "at": 0, "text": "Wrote " + card["output_file"]})
    elif job is not None and getattr(job, "error", ""):
        lines.append({"kind": "error", "ts": "", "at": 0, "text": str(job.error)})
    return {"ok": True, "agent": card, "lines": lines[-limit:], "source": "studio"}


def _hms(at: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(at))
    except (ValueError, OSError):
        return ""


def running_by_feed() -> dict[str, int]:
    """How many delegated jobs are in flight, per conversation — for the rail's badge.

    One entry per FEED (never a second one for the bare folder: the caller keys its own map by
    both ids and would count this twice). Reads the directory, so a conversation that has never
    delegated anything costs nothing.
    """
    out: dict[str, int] = {}
    try:
        files = list(_DIR.glob("*.json"))
    except OSError:
        return out
    for f in files:
        feed = f.stem
        try:
            n = sum(1 for c in cards(feed) if c.get("running"))
        except Exception:
            n = 0
        if n:
            out[feed] = n
    return out


# --------------------------------------------------------------------------------------------
# attribution
# --------------------------------------------------------------------------------------------
def owner_for(explicit: str = "", header_feed: str = "", from_ui: bool = False) -> str:
    """Which conversation a job belongs to. See the module docstring for the order."""
    for cand in (explicit, header_feed):
        if cand and str(cand).strip():
            return str(cand).strip()
    if from_ui:
        return ""
    try:
        from . import cc_session
        st = cc_session.live_status()
    except Exception:
        return ""
    busy = {k for k, v in (st.get("statuses") or {}).items() if v}
    if not busy:
        return ""
    # The map is keyed by both the prefixed id and the bare folder, so one working conversation
    # appears under two keys. Collapse to folders: two keys for one agent is still one agent.
    from . import engines
    folders = {engines.bare_folder(fid) for fid in busy}
    if len(folders) != 1:
        return ""
    # Which conversation in that folder, asked of each engine rather than read off a map keyed
    # by both ids: with Claude and Codex both mid-turn in one folder there is no single owner.
    feeds = engines.busy_feeds(next(iter(folders)))
    return feeds[0] if len(feeds) == 1 else ""
