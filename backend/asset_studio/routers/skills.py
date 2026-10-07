"""Skills — list + enable/disable from the UI, for the engine the pane is set to.

Not "Claude Code skills": the DeepSeek Harness runtime discovers `SKILL.md` from its own roots and
honours the same `disable-model-invocation` frontmatter flag, so the Studio mounts its shared
library into that runtime (see `deepseek_session._skill_dirs`) and this one panel manages both.
Codex has no per-task skill mechanism and honestly answers with nothing.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import skills

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
def list_skills(project_id: str = "", agent: str = ""):
    """`agent` decides whose library: Claude's home, the DSH roots, or nothing for Codex."""
    return {"skills": skills.list_skills(project_id, agent), "agent": agent}


class ToggleBody(BaseModel):
    enabled: bool
    project_id: str = ""
    agent: str = ""


@router.post("/{skill_id}")
def toggle(skill_id: str, body: ToggleBody):
    return skills.set_skill(skill_id, body.enabled, body.project_id, body.agent)
