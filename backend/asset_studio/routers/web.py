"""The web an agent can actually reach.

Same contract as /api/graphify/query: one curl, a compact JSON answer, no setup in the project.
An agent does not need to know that a stealth browser exists, only that this endpoint returns the
page where its own WebFetch returned 403.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import web_tools

router = APIRouter(prefix="/api/web", tags=["web"])


@router.get("/status")
def status():
    return web_tools.status()


@router.post("/install")
def install():
    return web_tools.ensure_installed()


class FetchBody(BaseModel):
    url: str
    mode: str = "auto"        # auto | http | stealth
    chars: int = 20000
    timeout: float = 120.0


@router.post("/fetch")
def fetch(body: FetchBody):
    """One page as readable text, climbing past whatever is in the way.

    `tried` comes back with the answer so the caller can see what the page did — a plain request
    refused with 403 and a stealth browser getting 200 is worth knowing, and it is the difference
    between "the site is down" and "the site does not like robots".
    """
    return web_tools.fetch(body.url, body.mode, body.chars, body.timeout)


@router.get("/search")
def search(q: str, n: int = 8, timeout: float = 120.0):
    """Web search with no API key and no account."""
    return web_tools.search(q, n, timeout)
