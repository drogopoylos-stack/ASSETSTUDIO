"""/gamemodel's last step: the bench's model into the game and the Catalog."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import gamemodel

router = APIRouter(prefix="/api/gamemodel", tags=["gamemodel"])


class PublishBody(BaseModel):
    project: str
    name: str
    category: str = ""
    license: str = ""
    tags: list[str] = []
    prompt: str = ""


@router.post("/publish")
async def publish(body: PublishBody):
    """Export what the forge bench holds into <game>/assets/models/, render a thumbnail, file it in the Catalog."""
    return await run_in_threadpool(gamemodel.publish, body.project, body.name, body.category, body.license,
                                   body.tags, body.prompt)
