"""Human references at real size: search, make one to order, copy one into a game."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import refs

router = APIRouter(prefix="/api/refs", tags=["refs"])


@router.get("/search")
def search(q: str = "", kind: str = "", limit: int = 50):
    """CC0 human bodies, heads, hands, feet, eyes, jaws and skulls at real size, with renders."""
    return refs.search(q, kind, limit)


@router.get("/status")
def status():
    return {"ok": True, **refs.status()}


@router.post("/prepare")
async def prepare(force: bool = False):
    """Download and export the base meshes, install MPFB2 in the Studio's Blender profile."""
    return await run_in_threadpool(refs.prepare, force)


class HumanBody(BaseModel):
    height_m: float = 1.75
    gender: float = 0.5
    age_years: float = 25
    muscle: float = 0.5
    weight: float = 0.5
    proportions: float = 0.5
    rig: str = ""
    name: str = "human"
    project: Optional[str] = None


@router.post("/human")
async def human(body: HumanBody):
    """A human made to order with MPFB2, at the asked height, optionally rigged."""
    return await run_in_threadpool(refs.human, body.model_dump())


class GetBody(BaseModel):
    id: str
    project: str = ""


@router.post("/get")
def get(body: GetBody):
    """One reference by id, copied into the game when a project is given."""
    return refs.get(body.id, body.project)
