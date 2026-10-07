"""Claude Code login state + in-app re-login (browser) when auth fails with a 401."""
from __future__ import annotations

from fastapi import APIRouter

from .. import claude_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/claude")
def claude_status():
    """Current Claude login state — `needs_login` drives the re-login popup."""
    return claude_auth.status()


@router.post("/claude/login")
def claude_login():
    """Open Claude Code in a terminal so the user can (re)authenticate in the browser."""
    return claude_auth.open_login()


@router.post("/claude/recheck")
def claude_recheck():
    """Re-verify auth (after the user logs in) and return the refreshed status."""
    return claude_auth.recheck()


@router.get("/claude/limits")
def claude_limits():
    """Live usage-limit buckets (5h session / weekly / per-model e.g. Fable) — for the top bar."""
    return claude_auth.usage_limits()
