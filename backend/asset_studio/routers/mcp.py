"""GET /api/mcp/catalog — the Studio engine as MCP tools, for the stdio server in mcp_engine.py.

The server asks once per session, with the session's folder, and gets back exactly the tools
whose notes that session would get (see mcp_catalog). Nothing here runs a tool: each call goes
to the ordinary endpoint, so the MCP tools and the curl calls are the same calls.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import mcp_catalog
from ..config import settings

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/catalog")
def catalog(request: Request, cwd: str = "") -> dict:
    if settings.get("cc_mcp", True) is False:
        return {"ok": True, "instructions": "", "tools": [], "off": True}
    return mcp_catalog.catalog(request.app, cwd)
