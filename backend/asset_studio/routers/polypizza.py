"""Poly Pizza: search low-poly game models (many animated creatures), download one into a game."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import polypizza

router = APIRouter(prefix="/api/polypizza", tags=["polypizza"])


@router.get("/status")
def status():
    """Whether a key is stored. The key itself is never returned."""
    return polypizza.status()


@router.get("/search")
def search(q: str, animated: bool = False, licence: str = "", category: str = "", limit: int = 30):
    """Models matching q. animated=true for rigged and animated ones; licence=CC0 for no-credit ones."""
    return polypizza.search(q, animated, licence, category, limit)


class DownloadBody(BaseModel):
    project: str
    id: str


@router.post("/download")
async def download(body: DownloadBody):
    """The model into the game's assets/polypizza/<id>/, with LICENSE.txt, renders and its measured size."""
    return await run_in_threadpool(polypizza.download, body.project, body.id)
