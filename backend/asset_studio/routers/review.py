"""Visual review — deterministic contact sheets an agent can actually judge from.

Every handler is sync, so FastAPI runs it in the threadpool: a render takes seconds of real time
and must never sit on the event loop.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import review

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/status")
def status():
    return review.status()


class RenderBody(BaseModel):
    project: str = ""             # absolute folder of the game
    project_id: str = ""          # only decides where the sheet is filed
    mode: str = "scene"           # "scene" (the real game) | "isolate" (one asset)
    target: str = ""              # isolate: name in review/targets.js
    action: str = ""              # name in window.__review.actions -- fire it, then look
    js: str = ""                  # or just run this expression; needs nothing from the game
    reset: str = ""               # action to clear state first (makes a warm tab reusable)
    session: str = ""             # reuse a warm tab under this key: no reload, no warm-up
    auto_times: bool = False      # probe cheaply, then sample across the event it finds
    probe_step_ms: int = 25
    probe_steps: int = 60
    auto_plate: bool = False      # re-run without the trigger and diff against it
    plate: dict = {}              # or spell the plate out as spec overrides
    clip: bool = False            # also write a deterministic animated WebP
    clip_fps: int = 30
    clip_width: int = 640
    module: str = "/review/targets.js"
    url: str = ""                 # override the auto-discovered origin
    label: str = ""
    times: list[int] = []         # ms on the sample timeline
    input: list[dict] = []        # {at, type, key|x,y}; negative `at` fires during warm-up
    warmup_ms: int = 1200
    size: list[int] = [640, 400]
    scale: float = 0              # device pixels per CSS pixel; 0 = follow `quality`
    quality: str = ""             # draft | normal | high; "" = the Settings default
    background: str = "#12151c"
    seed: int = 1234567


@router.post("/render")
def render(body: RenderBody):
    spec = body.model_dump()
    project = spec.pop("project", "")
    pid = spec.pop("project_id", "")
    if not project:
        return {"ok": False, "error": "project (an absolute folder) is required"}
    return review.render(project, spec, project_id=pid)


@router.get("/actions")
def actions(project: str, url: str = ""):
    """What can be fired in this game? Empty means it has registered nothing yet."""
    return review.actions(project, url)


@router.get("/targets")
def targets(project: str, module: str = "/review/targets.js"):
    return review.targets(project, module)


@router.get("/browsers")
def browser_list():
    """What is running, whose it is, and what it is doing — the detail behind the status pill."""
    return review.browsers()


class KillBody(BaseModel):
    pids: list[int] = []          # empty means "every stray", never our own


@router.post("/browsers/kill")
def browser_kill(body: KillBody):
    pids = body.pids
    if not pids:
        pids = [b["pid"] for b in review.browsers()["strays"]]
    return review.kill(pids)


@router.post("/stop")
def stop():
    """Close our own review browser. It reopens by itself on the next review."""
    return {"ok": review.shutdown()}
