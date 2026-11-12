"""DeepSeek Creator mode: the official DeepSeek Harness app, run for one folder and shown in the Studio.

See `deepseek_creator` for why this is the full app and not the chat box's SDK runtime.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import deepseek_creator

router = APIRouter(prefix="/api/deepseek/creator", tags=["deepseek"])


@router.get("/status")
def status():
    """Whether the app is installed, Node's version, an install in progress, and the running servers."""
    return deepseek_creator.status()


@router.post("/install")
def install():
    """Install the DeepSeek Harness app into the Studio's data folder, in the background."""
    return deepseek_creator.install()


class FolderBody(BaseModel):
    folder: str = ""


@router.post("/open")
def open_(body: FolderBody):
    """Start the app for one folder (or reuse its server) and return its URL, token included."""
    return deepseek_creator.open_(body.folder)


@router.post("/stop")
def stop(body: FolderBody):
    """Stop the app of one folder, or of every folder when none is named."""
    return deepseek_creator.stop(body.folder)
