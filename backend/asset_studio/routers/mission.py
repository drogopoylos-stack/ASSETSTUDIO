"""Mission Control endpoints — a command center for all Claude Code projects."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import cc_session, mission, usage

router = APIRouter(prefix="/api/mission", tags=["mission"])


@router.get("/overview")
def overview(force: bool = False):
    """Projects + active agents + running localhost servers + headline counts."""
    return mission.overview(force=force)


@router.get("/projects/{project_id}/feed")
def project_feed(project_id: str, limit: int = 50, session: str = "", kinds: str = ""):
    """Terminal-style live tail of a project's transcript (default newest, or a
    specific `session`), plus live status and any non-claude agent stdout."""
    return {"id": project_id, **mission.project_feed(project_id, limit, session, kinds)}


@router.get("/projects/{project_id}/sessions")
def project_sessions(project_id: str):
    """All conversations for a project (newest first) — for clear/new + reopen."""
    return {"id": project_id, "sessions": mission.list_sessions(project_id)}


@router.get("/usage/accounts")
async def usage_accounts():
    """Every account the Plan usage bar can show, whether it is connected, and how to connect it."""
    from .. import usage_accounts as ua
    return {"accounts": await run_in_threadpool(ua.accounts)}


@router.get("/usage")
async def usage_summary(force: bool = False, provider: str = "auto"):
    """What is left on one account: plan windows (Claude, Codex) or the API balance (DeepSeek,
    Kimi, OpenRouter). `auto` is the first one that answers, Claude first."""
    from .. import usage_accounts as ua
    if provider != "auto" and not ua.known(provider):
        return {"available": False, "windows": [], "error": "Unknown usage provider"}

    async def claude() -> dict:
        data = await run_in_threadpool(usage.compute, force=force)
        out = {**data, "provider": "claude"}
        if not data.get("available"):
            out["needs_connect"] = not await run_in_threadpool(ua._claude_connected)
        return out

    if provider == "claude":
        return await claude()
    if provider != "auto":
        return await run_in_threadpool(ua.read, provider, force)
    first = await claude()
    if first.get("available"):
        return first
    for pid in ("codex", "deepseek", "kimi", "openrouter"):
        data = await run_in_threadpool(ua.read, pid, force)
        if data.get("available"):
            return data
    return first


@router.get("/projects/{project_id}/context")
def project_context(project_id: str):
    """Context-window fill (% used / % until auto-compact) for a session."""
    return mission.project_context(project_id)


@router.get("/projects/{project_id}/todos")
def project_todos(project_id: str):
    """The standing phase list (TodoWrite) behind the "3/9" counter, in full."""
    return mission.project_todos(project_id)


@router.get("/projects/{project_id}/subagents")
def project_subagents(project_id: str, session: str = "", files: bool = False):
    """Every subagent this project has run: its bill, its footprint and whether it is still going.

    Through `mission` rather than straight to the Claude module, so a Codex conversation — whose
    agents live under its own thread ids — is served the same list instead of an empty one.
    """
    return mission.project_subagents(project_id, session=session, with_files=files)


@router.get("/projects/{project_id}/subagents/collisions")
def project_subagent_collisions(project_id: str, root: str = ""):
    """Files that more than one RUNNING subagent has edited — parallel work about to go wrong."""
    return {"collisions": mission.project_subagent_collisions(project_id, root)}


@router.get("/projects/{project_id}/subagents/{agent_id}")
def project_subagent(project_id: str, agent_id: str, limit: int = 400):
    """One subagent in full: its own timeline, drawn by the same builder as the main feed."""
    return mission.project_subagent(project_id, agent_id, limit=limit)


@router.get("/projects/{project_id}/phase-history")
def project_phase_history(project_id: str):
    """Earlier phase sets, newest first.

    The agent rewrites its phase file whole, so without this the steps of a finished build vanish
    the moment the next set is written — exactly when you would want to look at what it did.
    """
    return mission.phase_history(project_id)


class OpenBody(BaseModel):
    path: str
    target: str = "vscode"  # "vscode" | "folder"


@router.post("/open")
def open_project(body: OpenBody):
    return mission.open_in_editor(body.path, body.target)


# --- send a message to a Claude Code session -------------------------------
@router.get("/claude")
def claude_status():
    return cc_session.status()


@router.get("/spend")
def agent_spend():
    """What the agent work has cost, banked from each turn's own `total_cost_usd`.

    Separate from the catalog's generation cost on purpose: they are different orders of
    magnitude and adding them would hide the larger one inside the smaller one's card."""
    from .. import spend
    return spend.totals()


@router.get("/spend/breakdown")
def agent_spend_breakdown(project: str = ""):
    """What the money BOUGHT: cost per tool, per kind of round trip, and where the context sat.

    A project total cannot tell you that Bash is a third of the bill. This prices each request from
    the transcript's own token counts (see cost_breakdown.py) and sums it by tool name."""
    from .. import cost_breakdown
    return cost_breakdown.for_project(project)


@router.get("/claude/version")
def claude_version():
    """The version of the binary the Studio resolved, and what is published. Cached and
    refreshed on a worker thread — see cli_version for why PATH's answer is not the answer."""
    from .. import cli_version
    d = cli_version.info()
    d["busy"] = cli_version.busy_sessions()
    return d


class InstallBody(BaseModel):
    target: str = ""          # a dist-tag or an exact version; empty = the newest published


@router.post("/claude/install")
def claude_install(body: InstallBody):
    """Run the CLI's own installer. User-initiated only: it replaces the binary that every NEW
    session will start from. Processes already running keep the image they loaded."""
    from .. import cli_version
    return cli_version.install(body.target)


@router.get("/projects/{project_id}/speed")
def project_speed(project_id: str, limit: int = 400, all_projects: bool = False):
    """Output tokens per second: live for the current turn, plus every finished turn grouped by
    model and effort — so 'is Fable faster than Opus at max?' is a number, not a guess."""
    from .. import speed
    live = cc_session.live_state(project_id)
    return {"live": {"working": bool(live.get("working")), "tps": live.get("tps", 0.0),
                     "tokens": live.get("tokens", 0), "gen_s": live.get("gen_s", 0.0),
                     "elapsed": live.get("elapsed", 0.0), "model": live.get("model", "")},
            **speed.summary("" if all_projects else project_id, limit)}


@router.get("/agents")
def agents_list():
    """Installed/available AI coding agents (Claude, Codex, Gemini, Cursor…)."""
    from .. import agents
    return {"agents": agents.list_agents()}


@router.get("/last-agent")
def last_agent(path: str = ""):
    """The engine last used in this folder, so switching workspace can restore it."""
    return mission.last_agent_for(path)


@router.get("/slash-commands")
def slash_commands(project_id: str = ""):
    return {"commands": mission.slash_commands(project_id)}


class SendBody(BaseModel):
    message: str
    model: str = "default"
    permission_mode: str = "acceptEdits"
    fork: bool = False
    images: list[str] = []
    effort: str = "default"
    agent: str = "claude"
    new_session: bool = False
    session: str = ""
    path: str = ""
    thinking: bool = False
    steer: bool = False
    companions: list[dict] = []   # co-agents to run alongside (each: {id, model, effort, permission_mode})


@router.post("/projects/{project_id}/send")
def send_message(project_id: str, body: SendBody):
    return cc_session.send(project_id, body.message, body.model, body.permission_mode,
                           body.fork, body.images, body.effort, body.agent,
                           body.new_session, body.session, body.path, body.thinking,
                           companions=body.companions, steer=body.steer)


@router.get("/attention")
def attention():
    """Who needs you, across every project — the one line at the top of the workspace.

    Off by the switch means an EMPTY answer, not a 404: the bar draws nothing and stops polling,
    and a backend that predates the switch is told apart from one that has it turned off.
    """
    from ..config import settings
    if settings.get("needs_you", True) is False:
        return {"ok": True, "off": True, "at": 0.0, "rows": [],
                "counts": {"blocked": 0, "working": 0, "idle": 0, "agents": 0}}
    return mission.attention()


@router.get("/live-status")
def live_status():
    """Busy state for every live streaming session — drives per-project working indicators."""
    return cc_session.live_status()


@router.get("/projects/{project_id}/live")
def project_live(project_id: str):
    """Instant live progress for the working indicator: real-time output tokens + the current
    activity (Thinking / Editing X / Running …) + elapsed seconds this turn. Reads the live
    session directly so it ticks the moment Claude generates — no transcript-poll lag."""
    return {"id": project_id, **cc_session.live_state(project_id)}


@router.get("/projects/{project_id}/agent-log")
def agent_log(project_id: str, lines: int = 120):
    """Tail of the headless agent's stdout (for non-claude agents whose output
    doesn't land in the Claude transcript)."""
    return {"id": project_id, "log": cc_session.log_tail(project_id, lines),
            "sending": cc_session.is_sending(project_id)}


# --- checkpoints (review & revert AI edits) --------------------------------
@router.get("/projects/{project_id}/checkpoints")
def checkpoints_list(project_id: str):
    from .. import checkpoints
    return {"id": project_id, "checkpoints": checkpoints.list_checkpoints(project_id)}


class CheckpointBody(BaseModel):
    label: str = ""
    agent: str = ""      # which engine the user had selected when they pressed "Checkpoint now"


@router.post("/projects/{project_id}/checkpoints")
def checkpoint_create(project_id: str, body: CheckpointBody):
    from .. import checkpoints
    return checkpoints.create(project_id, label=body.label, kind="manual", agent=body.agent)


@router.get("/projects/{project_id}/checkpoints/{cid}/diff")
def checkpoint_diff(project_id: str, cid: str):
    from .. import checkpoints
    return checkpoints.diff(project_id, cid)


class RestoreBody(BaseModel):
    files: Optional[list[str]] = None
    force: bool = False          # restore even while an agent is writing in this folder


@router.post("/projects/{project_id}/checkpoints/{cid}/restore")
def checkpoint_restore(project_id: str, cid: str, body: RestoreBody):
    """A checkpoint frames the FOLDER, not one agent. With two agents in one folder, a restore
    taken for one of them would put the other's files back under it mid-turn — so it is refused
    while any engine is working there, and the caller can confirm and send `force`."""
    from .. import checkpoints
    if not body.force:
        busy = cc_session.folder_busy(project_id)
        if busy:
            return {"ok": False, "busy": busy, "error": (
                "%s is working in this folder right now. A restore would overwrite its edits."
                % ", ".join(cc_session.engine_name(f) for f in busy))}
    return checkpoints.restore(project_id, cid, only=body.files)


@router.get("/projects/{project_id}/sending")
def sending(project_id: str):
    # A prefixed feed polls only its engine (engines.for_feed); a Claude pane must not borrow
    # Codex's busy state just because both engines share the folder.
    busy = cc_session.is_sending(project_id)
    return {"sending": busy, "pending": 1 if busy else 0}


@router.post("/projects/{project_id}/cancel-send")
def cancel_send(project_id: str, agent: str = ""):
    """Stop the turn. `agent=codex` stops only Codex; a caller that names no agent stops the
    Claude session as it always did, and a Codex turn in the same folder with it."""
    from .. import engines
    eng = engines.for_agent(agent)
    if eng is not engines.CLAUDE:
        return eng.cancel(eng.feed_id(engines.bare_folder(project_id)))
    if agent and agent != "claude" and not mission.alt_prefix(project_id):
        project_id = agent + "--" + project_id
    out = cc_session.cancel(project_id)
    if not agent and engines.CODEX.is_sending(project_id):
        engines.CODEX.cancel(project_id)
    return out


# --- scheduled (delayed) messages -------------------------------------------
class ScheduleBody(BaseModel):
    project_id: str
    path: str = ""
    root_name: str = ""
    message: str
    send_at: float                 # epoch seconds at which to send
    model: str = "default"
    permission_mode: str = "acceptEdits"
    effort: str = "default"
    fork: bool = False
    thinking: bool = False
    agent: str = "claude"
    new_session: bool = False
    session: str = ""
    images: list[str] = []
    # Recurrence. Empty weekdays = fire once, which is what every job created before this
    # existed looks like, so old callers and old rows keep their old behaviour untouched.
    # Monday is 0, matching datetime.weekday(). repeat_time is local "HH:MM".
    repeat_weekdays: list[int] = []
    repeat_time: str = ""


@router.get("/scheduled")
def scheduled_list():
    from .. import scheduled
    return {"jobs": scheduled.list_jobs(), "paused": scheduled.paused()}


class SchedulePause(BaseModel):
    paused: bool


@router.put("/scheduled/paused")
def scheduled_pause(body: SchedulePause):
    """Pause or resume EVERY scheduled and recurring message at once."""
    from .. import scheduled
    return {"paused": scheduled.set_paused(body.paused)}


@router.post("/scheduled")
def scheduled_create(body: ScheduleBody):
    from .. import scheduled
    return scheduled.create_job(body.model_dump())


@router.delete("/scheduled/{job_id}")
def scheduled_cancel(job_id: str):
    from .. import scheduled
    return {"ok": scheduled.cancel_job(job_id)}


class RewindBody(BaseModel):
    uuid: str
    message: str
    model: str = "default"
    permission_mode: str = "acceptEdits"
    fork: bool = False
    effort: str = "default"
    thinking: bool = False
    session: str = ""
    path: str = ""
    restore_files: bool = False


@router.post("/projects/{project_id}/rewind")
def rewind(project_id: str, body: RewindBody):
    return cc_session.rewind(project_id, body.uuid, body.message, model=body.model,
                             permission_mode=body.permission_mode, fork=body.fork,
                             effort=body.effort, thinking=body.thinking, session=body.session,
                             path=body.path, restore_files=body.restore_files)
