"""Auto-learn (zero-token skill mining) — status for the composer toggle + a manual kick."""
from __future__ import annotations

from fastapi import APIRouter

from .. import autolearn

router = APIRouter(prefix="/api/autolearn", tags=["autolearn"])


@router.get("/status")
def autolearn_status():
    return autolearn.status()


@router.post("/run")
def autolearn_run():
    """Scan + distill right now (background). No-op while the toggle is off."""
    return autolearn.run_now()
