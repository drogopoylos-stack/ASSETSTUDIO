"""Google Antigravity CLI (`agy`): its model list, and Nano Banana images for any agent.

    GET  /api/antigravity/status           installed? where?
    GET  /api/antigravity/models?refresh=1 [[id, label], …] from `agy models`
    POST /api/antigravity/image            {prompt, out?, refs?: [paths], model?, timeout_s?}

The image route is what a Claude, Codex or DeepSeek agent calls with curl when it needs a
picture: the image is made by Antigravity's own image generator on the Google sign-in, so no
Gemini API key is involved (see antigravity_session.generate_image).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from .. import antigravity_session as agy

router = APIRouter(prefix="/api/antigravity", tags=["antigravity"])


@router.get("/status")
def status():
    exe = agy.find_agy()
    return {"installed": bool(exe), "path": exe or ""}


@router.get("/models")
def models(refresh: bool = False):
    return {"models": agy.models(refresh=refresh)}


class ImageBody(BaseModel):
    prompt: str
    out: str = ""
    refs: Optional[list[str]] = None
    model: str = "default"
    timeout_s: int = 300


@router.post("/image")
def image(body: ImageBody):
    # a plain def: FastAPI runs it on a worker thread, so a 30-60 s generation never blocks the loop
    return agy.generate_image(body.prompt, out=body.out, refs=body.refs, model=body.model,
                              timeout_s=max(30, min(int(body.timeout_s or 300), 900)))
