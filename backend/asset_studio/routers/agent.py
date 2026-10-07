"""Autonomous agent + Compare endpoints (headless-drivable)."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agent import loop as agent_loop
from ..models import AgentGoal, AgentRun, StageType

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/run", response_model=AgentRun)
async def run(goal: AgentGoal, background: bool = False):
    if background:
        asyncio.create_task(agent_loop.run_agent(goal))
        # return an immediately-queued shell run
        run = AgentRun(goal=goal)
        agent_loop._RUNS[run.id] = run
        return run
    return await agent_loop.run_agent(goal)


@router.get("/runs/{run_id}", response_model=AgentRun)
def get_run(run_id: str):
    run = agent_loop.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@router.get("/runs")
def list_runs():
    return list(agent_loop._RUNS.values())


class CompareRequest(BaseModel):
    stage: StageType
    prompt: str
    provider_a: str
    provider_b: str
    params: dict[str, Any] = {}
    inputs: list[str] = []


@router.post("/compare", response_model=AgentRun)
async def compare(req: CompareRequest):
    return await agent_loop.compare(
        req.stage, req.prompt, req.provider_a, req.provider_b, req.params, req.inputs
    )
