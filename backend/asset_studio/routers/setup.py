"""In-app installer endpoints — install the optional tools from the UI."""
from __future__ import annotations

from fastapi import APIRouter

from .. import setup_installer as si

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/tasks")
def tasks():
    return {"tasks": si.tasks()}


@router.post("/install/{task_id}")
def install(task_id: str):
    return si.install(task_id)


@router.get("/status/{task_id}")
def status(task_id: str):
    return si.status(task_id)
