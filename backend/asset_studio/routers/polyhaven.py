"""Poly Haven: search, download into a project, and what a project already has."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import polyhaven

router = APIRouter(prefix="/api/polyhaven", tags=["polyhaven"])


@router.get("/search")
def search(type: str = "models", q: str = "", category: str = "", limit: int = 60, offset: int = 0):
    """CC0 models, textures or HDRIs whose name, tags or categories hold every word of `q`."""
    return polyhaven.search(type, q, category, limit, offset)


class DownloadBody(BaseModel):
    project: str
    id: str
    type: str = "models"
    res: str = "1k"


@router.post("/download")
def download(body: DownloadBody):
    """Fetch one asset into the project with its reference renders and LICENSE.txt."""
    return polyhaven.download(body.project, body.id, body.type, body.res)


@router.get("/downloaded")
def downloaded(project: str):
    return polyhaven.downloaded(project)
