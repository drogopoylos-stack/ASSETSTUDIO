"""Async job queue + worker pool.

Submitting a :class:`JobRequest` returns immediately with a queued :class:`Job`;
a pool of workers picks it up, runs the provider in a thread (providers block),
streams progress over the event bus, then persists the job + any produced assets.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from pathlib import Path
from typing import Optional

from .. import db
from ..config import JOBS_DIR, settings
from ..events import bus
from ..models import (
    Asset,
    Job,
    JobRequest,
    JobStatus,
    ProgressEvent,
    WSEventType,
)


class JobQueue:
    def __init__(self, concurrency: int = 2):
        self.concurrency = concurrency
        self._jobs: dict[str, Job] = {}
        self._cancel: set[str] = set()
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # THE ONE-GPU GATE. Local providers do not merely compete for the card, they evict each
        # other: ComfyUI answers a second prompt by offloading the first model into host RAM, and
        # one MiniMax H3 clip already holds ~13.8 GB of VRAM and ~30.8 GB private (measured —
        # providers/comfy_common.py:265). Created in `start()` because a Semaphore binds to a loop.
        self._local_gate: Optional[asyncio.Semaphore] = None

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._local_gate = asyncio.Semaphore(1)
        bus.bind_loop(self._loop)
        for i in range(self.concurrency):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"job-worker-{i}"))

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    # --- submit / query ----------------------------------------------------
    def submit(self, req: JobRequest) -> Job:
        job = Job(
            stage=req.stage,
            provider_id=req.provider_id,
            params=req.params,
            inputs=req.inputs,
            label=req.label,
            target_game=req.target_game,
            tags=req.tags,
            step="queued",
        )
        self._jobs[job.id] = job
        db.save_job(job)
        self._q.put_nowait(job.id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id) or db.get_job(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.canceled):
            return False
        self._cancel.add(job_id)
        if job.status == JobStatus.queued:
            self._finish(job, JobStatus.canceled, error="canceled before start")
        return True

    def active(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.status in (JobStatus.queued, JobStatus.running)]

    # --- worker ------------------------------------------------------------
    async def _worker(self, idx: int) -> None:
        while True:
            job_id = await self._q.get()
            job = self._jobs.get(job_id)
            try:
                if not job or job_id in self._cancel:
                    continue
                await self._run_job_gated(job)
            except Exception:  # pragma: no cover - safety net
                if job:
                    self._finish(job, JobStatus.failed, error=traceback.format_exc())
            finally:
                self._q.task_done()

    async def _run_job_gated(self, job: Job) -> None:
        """Run a job, holding THE ONE-GPU GATE if it is a local one.

        The gate lives here rather than inside a provider because every local provider shares the
        one card — ComfyUI for video and images, and the in-process torch models (TripoSR, the local
        diffusers, RealESRGAN). Two at once is not slower, it is worse: ComfyUI answers a second
        prompt by offloading the model it is holding into host RAM. `serialize_local_jobs` turns
        that off; the job waits with a step a person can read rather than appearing stuck."""
        from ..providers.registry import get_provider
        provider = get_provider(job.provider_id)
        local = getattr(getattr(provider, "kind", None), "value", "") == "local"
        gate = self._local_gate
        if not (local and gate is not None and settings.get("serialize_local_jobs", True)):
            await self._run_job(job)
            return
        job.step = "waiting for the GPU — another local job is running"
        db.save_job(job)
        await bus.publish(self._evt(job, step=job.step))
        await gate.acquire()
        try:
            await self._run_job(job)
        finally:
            gate.release()

    async def _run_job(self, job: Job) -> None:
        from ..providers.registry import get_provider  # lazy to avoid import cycle
        from ..providers.base import JobContext

        # STOP MEANS STOP, EVEN WHILE THE JOB IS STILL WAITING FOR THE CARD.
        #
        # `cancel()` finishes a QUEUED job on the spot — there is nothing yet to interrupt — and
        # `_finish` DISCARDS the id from `self._cancel` as it writes the final row. So it is
        # `job.status` that remembers the decision, not the cancel set: a waiter parked on the GPU
        # gate (or on a busy worker) would otherwise wake up, run the job it was told to drop, spend
        # the card on it, and then overwrite the canceled row with whatever the run ended as. The
        # gate made that window wide; this closes it for every path, gated or not.
        if job.status != JobStatus.queued:
            return

        provider = get_provider(job.provider_id)
        if provider is None:
            self._finish(job, JobStatus.failed, error=f"Unknown provider '{job.provider_id}'")
            return

        # preflight safety guards (disk + monthly spend cap)
        guard = self._preflight(provider)
        if guard:
            self._finish(job, JobStatus.failed, error=guard)
            return

        # auto-start the local server this provider depends on, if configured
        await self._ensure_service(job, provider)

        avail, reason = provider.is_available()
        if not avail:
            self._finish(job, JobStatus.failed, error=reason or "provider unavailable")
            return

        job.status = JobStatus.running
        job.started_at = time.time()
        job.step = "starting"
        db.save_job(job)
        await bus.publish(self._evt(job, step="starting"))

        # resolve inputs (asset ids or file paths)
        inputs, input_assets = self._resolve_inputs(job.inputs)
        workdir = Path(JOBS_DIR) / job.id
        workdir.mkdir(parents=True, exist_ok=True)

        ctx = JobContext(
            job=job,
            inputs=inputs,
            input_assets=input_assets,
            workdir=workdir,
            settings=settings.all(),
            emit=bus.publish_threadsafe,
            is_canceled=lambda: job.id in self._cancel,
        )

        loop = asyncio.get_running_loop()
        from .. import gpu_memory
        gpu_memory.note_busy_start()
        try:
            assets: list[Asset] = await loop.run_in_executor(None, provider.run, ctx)
        except Exception:
            # A provider that bails out because the user hit Stop is a cancel, not a failure.
            if job.id in self._cancel:
                self._finish(job, JobStatus.canceled, error="canceled")
            else:
                self._finish(job, JobStatus.failed, error=traceback.format_exc())
            return
        finally:
            gpu_memory.note_busy_end()

        if job.id in self._cancel:
            self._finish(job, JobStatus.canceled, error="canceled")
            return

        # persist assets
        for asset in assets or []:
            db.save_asset(asset)
            job.outputs.append(asset)
            await bus.publish(
                ProgressEvent(type=WSEventType.asset_created, job_id=job.id, asset=asset)
            )

        job.cost = ctx.cost
        self._finish(job, JobStatus.succeeded)

    # --- preflight + service autostart ------------------------------------
    def _preflight(self, provider) -> str:
        from ..system_stats import collect

        try:
            st = collect()
            min_free = float(settings.get("min_free_gb", 3) or 0)
            if st.disk and min_free and st.disk.free_gb < min_free:
                return (f"Low disk: {st.disk.free_gb:.1f} GB free (min {min_free} GB). "
                        "Free space, or lower the free disk floor in Settings > Cost > Limits.")
            # A LOCAL JOB IS REFUSED WHEN THE MACHINE IS ALREADY OUT OF MEMORY. This is the gap
            # that froze this PC: the disk floor and the spend cap were the only preflight checks,
            # and neither knows what a heavy local job costs. One MiniMax H3 clip holds ~30.8 GB of
            # private working set (comfy_common.py:265); the next job on top of that pushes the rest
            # of the system into the page file, and then EVERY process stops answering — the chat
            # included. Refusing with a sentence is recoverable; a frozen desktop is not.
            #
            # THE SENTENCE NAMES SOMETHING THE USER CAN ACTUALLY DO. It used to say "Press Free
            # memory", and there is no control by that name: releasing cached models is a CLICK ON
            # THE RAM OR VRAM READING in the bottom status bar — which is where the number that made
            # them look lives. An instruction pointing at a control that does not exist is worse than
            # none: it is a dead end that reads as the app being broken.
            if getattr(getattr(provider, "kind", None), "value", "") == "local":
                floor = float(settings.get("min_free_ram_gb", 4) or 0)
                total = float(st.ram_total_gb or 0)
                free = max(0.0, total - float(st.ram_used_gb or 0))
                if floor and total and free < floor:
                    return (f"Low memory: {free:.1f} GB free of {total:.0f} GB (min {floor:.0f} GB). "
                            "A local job now would push the rest into the page file and freeze the "
                            "PC. Click the RAM or VRAM reading in the bottom bar to hand cached model "
                            "memory back, wait for the running job to release the card, or lower the "
                            "free RAM floor in Settings > Cost > Limits.")
        except Exception:
            pass
        cap = float(settings.get("monthly_spend_cap_usd", 0) or 0)
        if cap and getattr(provider, "kind", None) and provider.kind.value == "api":
            try:
                spent = db.month_to_date_cost()
                if spent >= cap:
                    return (f"Monthly spend cap reached (${spent:.2f} / ${cap:.2f}). "
                            "Raise it in Settings or use a free/local provider.")
            except Exception:
                pass
        return ""

    async def _ensure_service(self, job: Job, provider) -> None:
        if not settings.get("auto_start_services", True):
            return
        try:
            from ..services import manager
        except Exception:
            return
        spec = manager.service_for_provider(provider.id)
        if not spec or not spec.command.strip() or manager.is_reachable(spec.health_url):
            return
        job.status = JobStatus.running
        job.step = f"starting {spec.name}…"
        await bus.publish(self._evt(job, step=job.step))
        loop = asyncio.get_running_loop()
        ok, reason = await loop.run_in_executor(None, manager.ensure, spec.id, 120.0)
        job.logs.append(f"started {spec.name}" if ok else f"could not start {spec.name}: {reason}")

    # --- helpers -----------------------------------------------------------
    def _resolve_inputs(self, inputs: list[str]):
        paths: list[str] = []
        assets: list[Asset] = []
        for ref in inputs:
            asset = db.get_asset(ref)
            if asset and Path(asset.path).exists():
                paths.append(asset.path)
                assets.append(asset)
                continue
            p = Path(ref)
            if p.exists():
                paths.append(str(p.resolve()))
        return paths, assets

    def _evt(self, job: Job, step: Optional[str] = None) -> ProgressEvent:
        return ProgressEvent(
            type=WSEventType.job_update,
            job_id=job.id,
            status=job.status,
            progress=job.progress,
            step=step if step is not None else job.step,
            eta_seconds=job.eta_seconds,
            job=job,
        )

    def _finish(self, job: Job, status: JobStatus, error: str = "") -> None:
        job.status = status
        job.finished_at = time.time()
        if status == JobStatus.succeeded:
            job.progress = 1.0
            job.step = "done"
        if error:
            job.error = error
            if status == JobStatus.failed:
                job.step = "failed"
                try:
                    from ..errors import classify_error
                    from ..providers.registry import get_provider
                    job.error_hint = classify_error(error, get_provider(job.provider_id))
                except Exception:
                    pass
        db.save_job(job)
        self._cancel.discard(job.id)
        # publish final state (threadsafe — may be called from worker thread paths)
        bus.publish_threadsafe(self._evt(job))


queue = JobQueue(concurrency=2)
