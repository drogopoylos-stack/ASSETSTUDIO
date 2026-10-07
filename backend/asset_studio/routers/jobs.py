"""Job submission, status, cancel, and one-click multi-stage pipeline."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..models import Job, JobRequest, JobStatus
from ..jobs.queue import queue

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/jobs", response_model=Job)
def submit_job(req: JobRequest):
    from ..providers.registry import get_provider

    if get_provider(req.provider_id) is None:
        raise HTTPException(400, f"unknown provider '{req.provider_id}'")
    return queue.submit(req)


@router.get("/jobs")
def list_jobs(status: Optional[str] = None, limit: int = 100):
    # merge live (in-memory) + persisted, de-duplicated, newest first
    live = {j.id: j for j in queue.active()}
    rows = db.list_jobs(status=status, limit=limit)
    merged = {j.id: j for j in rows}
    merged.update(live)
    out = sorted(merged.values(), key=lambda j: j.created_at, reverse=True)
    if status:
        out = [j for j in out if j.status.value == status]
    return out[:limit]


@router.get("/jobs/active")
def active_jobs():
    return queue.active()


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str):
    job = queue.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    ok = queue.cancel(job_id)
    if not ok:
        raise HTTPException(409, "job not cancelable")
    return {"ok": True}


# --- pipeline (chain stages one-click) -------------------------------------
class PipelineStep(BaseModel):
    stage: str
    provider_id: str
    params: dict[str, Any] = {}


class PipelineRequest(BaseModel):
    steps: list[PipelineStep]
    inputs: list[str] = []
    target_game: str = ""
    tags: list[str] = []
    label: str = "pipeline"


async def _await_job(job_id: str, timeout: float = 1800.0) -> Optional[Job]:
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        job = queue.get(job_id)
        if job and job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.canceled):
            return job
        await asyncio.sleep(0.35)
    return queue.get(job_id)


@router.post("/pipeline")
async def run_pipeline(req: PipelineRequest):
    """Run steps sequentially, feeding each step's output assets into the next."""
    from ..providers.registry import get_provider

    cur_inputs = list(req.inputs)
    job_ids: list[str] = []
    results = []
    for i, step in enumerate(req.steps):
        if get_provider(step.provider_id) is None:
            raise HTTPException(400, f"step {i}: unknown provider '{step.provider_id}'")
        job = queue.submit(JobRequest(
            stage=step.stage, provider_id=step.provider_id, params=step.params,
            inputs=cur_inputs, target_game=req.target_game, tags=req.tags,
            label=f"{req.label} [{i+1}/{len(req.steps)}] {step.stage}",
        ))
        job_ids.append(job.id)
        done = await _await_job(job.id)
        results.append(done)
        if not done or done.status != JobStatus.succeeded:
            return {"ok": False, "failed_step": i, "job_ids": job_ids,
                    "error": (done.error if done else "timeout"), "results": results}
        if done.outputs:
            cur_inputs = [a.id for a in done.outputs]  # chain forward
    return {"ok": True, "job_ids": job_ids, "final_assets": cur_inputs, "results": results}
