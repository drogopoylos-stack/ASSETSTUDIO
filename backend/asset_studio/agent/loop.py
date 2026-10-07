"""Autonomous agent loop: generate → render/see → judge → re-prompt or switch
provider, keeping the best result. Also powers Compare (A/B two providers).

Everything here is callable head-less (CLI / HTTP / MCP) so an external AI agent
can drive iteration without the GUI.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

from .. import db
from ..config import settings
from ..events import bus
from ..models import (
    AgentGoal,
    AgentIteration,
    AgentRun,
    JobRequest,
    JobStatus,
    ProgressEvent,
    WSEventType,
)
from ..jobs.queue import queue
from ..providers.registry import get_provider, providers_for
from .judge import judge_asset

_RUNS: dict[str, AgentRun] = {}


def get_run(run_id: str) -> Optional[AgentRun]:
    return _RUNS.get(run_id)


async def _await_job(job_id: str, timeout: float = 900.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = queue.get(job_id)
        if job and job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.canceled):
            return job
        await asyncio.sleep(0.4)
    return queue.get(job_id)


def _default_provider(goal: AgentGoal) -> str:
    if goal.provider_id:
        return goal.provider_id
    dflt = settings.get("default_providers", {}).get(goal.stage.value)
    if dflt and get_provider(dflt):
        return dflt
    avail = [p for p in providers_for(goal.stage.value) if p.is_available()[0]]
    return avail[0].id if avail else (providers_for(goal.stage.value) or [None])[0]


async def _emit(run: AgentRun, message: str):
    await bus.publish(
        ProgressEvent(
            type=WSEventType.agent_update,
            data={"run_id": run.id, "message": message,
                  "status": run.status.value, "best_score": run.best_score,
                  "iterations": len(run.iterations)},
        )
    )


async def run_agent(goal: AgentGoal) -> AgentRun:
    run = AgentRun(goal=goal, status=JobStatus.running)
    _RUNS[run.id] = run

    # provider rotation: explicit compare set, else single default
    providers = goal.compare_providers or [_default_provider(goal)]
    providers = [p for p in providers if p and get_provider(p)]
    if not providers:
        run.status = JobStatus.failed
        run.error = "no available provider for stage"
        run.finished_at = time.time()
        return run

    base_prompt = goal.prompt
    seed = int(goal.params.get("seed", -1) or -1)
    rng = random.Random(seed if seed >= 0 else None)

    await _emit(run, f"starting: providers={providers}, max_iter={goal.max_iterations}")
    for i in range(goal.max_iterations):
        provider_id = providers[i % len(providers)]
        params = dict(goal.params)
        # progressively fold judge suggestions into the prompt + jiggle the seed
        if i > 0 and run.iterations and run.iterations[-1].judge:
            sugg = run.iterations[-1].judge.suggestions
            if sugg and "prompt" in {p.name for p in get_provider(provider_id).params}:
                params["prompt"] = f"{base_prompt}, {sugg}"
        if "seed" in {p.name for p in get_provider(provider_id).params} and seed < 0:
            params["seed"] = rng.randint(1, 2**31 - 1)
        params.setdefault("prompt", base_prompt)

        job = queue.submit(JobRequest(
            stage=goal.stage, provider_id=provider_id, params=params,
            inputs=goal.inputs, target_game=goal.target_game,
            label=f"agent {run.id} #{i+1}",
        ))
        await _emit(run, f"iter {i+1}: {provider_id} job {job.id}")
        done = await _await_job(job.id)

        it = AgentIteration(index=i, provider_id=provider_id, job_id=job.id)
        if not done or done.status != JobStatus.succeeded or not done.outputs:
            it.judge = None
            run.iterations.append(it)
            await _emit(run, f"iter {i+1}: job failed ({done.error if done else 'timeout'})")
            continue

        asset = done.outputs[0]
        it.asset_id = asset.id
        verdict = await asyncio.get_running_loop().run_in_executor(None, judge_asset, asset, goal)
        it.judge = verdict
        run.iterations.append(it)
        await _emit(run, f"iter {i+1}: score={verdict.score:.2f} — {verdict.reasoning[:80]}")

        if verdict.score > run.best_score:
            run.best_score = verdict.score
            run.best_asset_id = asset.id

        if verdict.accept:
            await _emit(run, f"accepted at score {verdict.score:.2f}")
            break

    run.status = JobStatus.succeeded
    run.finished_at = time.time()
    await _emit(run, f"done — best score {run.best_score:.2f}, asset {run.best_asset_id}")
    return run


async def compare(stage, prompt: str, provider_a: str, provider_b: str,
                  params: dict | None = None, inputs: list[str] | None = None) -> AgentRun:
    """Run one prompt through two providers and judge both; best wins."""
    goal = AgentGoal(
        stage=stage, prompt=prompt, params=params or {}, inputs=inputs or [],
        max_iterations=2, compare_providers=[provider_a, provider_b], accept_threshold=2.0,
    )
    return await run_agent(goal)
