"""Codex in the chat box: is it installed, is it signed in, which models it offers.

The conversation itself goes through /api/mission/projects/{id}/send like every other agent's;
these are the controls around it. Every call here runs on FastAPI's worker threads, because each
one may wait on the Codex app-server (starting it takes about a second).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import codex_app

router = APIRouter(prefix="/api/codex", tags=["codex"])


@router.get("/status")
def status(fresh: bool = False):
    """Installed? Which version, and is a newer one out? Signed in, how, as whom? A sign-in in
    progress, and the page or code it needs?"""
    return codex_app.status(fresh=fresh)


@router.post("/recheck")
def recheck():
    """Read the sign-in afresh - after `codex login` in a terminal, or a sign-in finished in the browser."""
    return codex_app.recheck()


class LoginBody(BaseModel):
    kind: str = "chatgpt"          # "chatgpt" (browser) | "device" (a code) | "apikey"
    api_key: str = ""
    open_browser: bool = True


@router.post("/login")
def login(body: LoginBody):
    return codex_app.login(body.kind, body.api_key, open_browser=body.open_browser)


@router.post("/login/cancel")
def login_cancel():
    return codex_app.login_cancel()


@router.post("/logout")
def logout():
    return codex_app.logout()


@router.get("/models")
def models(refresh: bool = False):
    """The models this Codex offers the signed-in account, each with its own reasoning efforts."""
    return codex_app.models(refresh=refresh)


class InstallBody(BaseModel):
    update: bool = True


@router.post("/install")
def install(body: InstallBody):
    """npm install -g @openai/codex@latest, in the background. Poll /status for its progress."""
    return codex_app.install(update=body.update)


class SandboxBody(BaseModel):
    cwd: str = ""


@router.post("/sandbox")
def sandbox(body: SandboxBody):
    """Set up Codex's own non-admin Windows sandbox (used by the "Ask" and "Read only" modes)."""
    return codex_app.sandbox_setup(body.cwd)
