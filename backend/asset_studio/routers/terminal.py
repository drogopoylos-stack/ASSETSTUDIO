"""Terminal panes — launch another coding agent in its own interface.

The socket polls a buffer the reader thread fills, rather than awaiting the pty itself. That
keeps every blocking read on its own thread: this router only ever does `await asyncio.sleep`,
so a terminal that hangs cannot take the event loop with it.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import cc_session, terminal

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

_POLL = 0.03          # 30 ms — below what a person can see, far above what the loop notices


@router.get("/agents")
def list_agents():
    """The launcher list: which CLIs are installed, and how to install the rest."""
    ok, why = terminal.available()
    return {"ok": ok, "error": why, "agents": terminal.agents()}


@router.get("/sessions")
def list_sessions():
    return {"terminals": terminal.listing()}


class LaunchBody(BaseModel):
    agent: str = ""
    cwd: str = ""
    cols: int = 100
    rows: int = 30
    install: bool = False
    model: str = ""      # Codex: the chat box's model for this folder
    effort: str = ""     # Codex: ...and its effort


@router.post("/launch")
def launch(body: LaunchBody):
    """Start an agent CLI, or run its installer, in a fresh terminal."""
    if body.install:
        return terminal.install(body.agent, body.cwd, body.cols, body.rows)
    return terminal.launch(body.agent, body.cwd, body.cols, body.rows, body.model, body.effort)


class ClaudeBody(BaseModel):
    """The chat's own settings ride along, so the terminal is the same session in every way
    that matters — same model, same effort, same permission mode, same system notes."""
    project_id: str = ""
    cols: int = 100
    rows: int = 30
    model: str = "default"
    permission_mode: str = "default"
    effort: str = "default"
    # The chat's conversation picker, mirrored: "" + fresh=False means the pinned one.
    session: str = ""
    fresh: bool = False
    cwd: str = ""              # the workspace folder, for a project with no transcript yet


@router.post("/claude")
def claude(body: ClaudeBody):
    """Hand this project's CONVERSATION to a real Claude Code terminal.

    Not a new chat: it resumes the same session id the chat was on, and writes to the same
    transcript, so closing the terminal hands everything back to the feed. Refuses while a
    turn is running rather than throwing away the answer being written.
    """
    if not body.project_id:
        return {"ok": False, "error": "project_id is required"}
    return cc_session.become_terminal(body.project_id, body.cols, body.rows,
                                      body.model, body.permission_mode, body.effort,
                                      body.session, body.fresh, body.cwd)


@router.delete("/{tid}")
def close(tid: str):
    return {"ok": terminal.kill(tid)}


@router.websocket("/ws/{tid}")
async def ws_terminal(sock: WebSocket, tid: str):
    await sock.accept()
    if not terminal.info(tid).get("ok"):
        await sock.send_json({"type": "error", "error": "no such terminal"})
        await sock.close()
        return

    # Replay what happened before this socket existed, so switching tabs and coming back
    # redraws the agent's screen instead of showing an empty box. attach() also clears the
    # undelivered tail, which is the same bytes — sending both drew it twice.
    back = terminal.attach(tid)
    if back:
        await sock.send_json({"type": "out", "data": back})

    async def to_client() -> None:
        # Everything in here is wrapped, because this runs as a bare task: an exception would
        # otherwise be swallowed by asyncio, output would simply stop, and the pane would show
        # a terminal that looks alive and has gone deaf. Say so instead.
        try:
            while True:
                out = terminal.drain(tid)
                if out is None:                 # the terminal was closed under us
                    await sock.send_json({"type": "closed"})
                    return
                if out:
                    await sock.send_json({"type": "out", "data": out})
                await asyncio.sleep(_POLL)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            try:
                await sock.send_json({"type": "error", "error": f"output stopped: {e}"})
            except Exception:
                pass

    pump = asyncio.create_task(to_client())
    try:
        while True:
            msg = await sock.receive_json()
            kind = msg.get("type")
            if kind == "in":
                terminal.write(tid, str(msg.get("data") or ""))
            elif kind == "resize":
                terminal.resize(tid, int(msg.get("cols") or 100), int(msg.get("rows") or 30))
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        pump.cancel()
        # The terminal deliberately OUTLIVES the socket. Closing the page should not kill the
        # agent mid-edit; the idle reaper in terminal.py collects it if nobody comes back.
