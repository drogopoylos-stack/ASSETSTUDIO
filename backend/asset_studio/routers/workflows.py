"""Workflows — live dashboard of the Workflow tool's multi-agent runs (phases + agents)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import workflows

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("")
def list_workflows(limit: int = 80):
    """Every workflow run on disk — live ones first, then most-recent."""
    return workflows.list_workflows(limit=limit)


@router.get("/{project_id}/{run_id}")
def workflow_detail(project_id: str, run_id: str):
    """Full run: phases, every agent's live state/tokens/activity, logs, and result."""
    wf = workflows.get_workflow(project_id, run_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    return wf


@router.get("/{project_id}/{run_id}/agent/{agent_id}")
def workflow_agent(project_id: str, run_id: str, agent_id: str):
    """A compact timeline of what one agent actually did (thinking / tools / text)."""
    return workflows.agent_tail(project_id, run_id, agent_id)
