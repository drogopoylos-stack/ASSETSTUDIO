"""Skills — list + enable/disable from the UI, for the engine the pane is set to.

Not "Claude Code skills": the DeepSeek Harness runtime discovers `SKILL.md` from its own roots and
honours the same `disable-model-invocation` frontmatter flag, so the Studio mounts its shared
library into that runtime (see `deepseek_session._skill_dirs`) and this one panel manages both.
Codex has no skill loader of its own, so it answers with the shared library and the
project's own roots; the enabled ones reach the engine as the thread's standing instructions.

The routes under `/sync` and `/pack` carry the library to another computer (see `skill_sync`). They
come before `/{skill_id}`, which would otherwise take those words for a skill.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import skill_sync, skills

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
def list_skills(project_id: str = "", agent: str = ""):
    """`agent` decides whose library: Claude's home, the DSH roots, or nothing for Codex."""
    return {"skills": skills.list_skills(project_id, agent), "agent": agent, "groups": skills.GROUPS}


@router.get("/sync")
def sync_status():
    return skill_sync.last_report()


@router.post("/sync")
def sync_now():
    return skill_sync.sync()


@router.post("/pack/export")
def pack_export():
    return skill_sync.export_pack()


@router.get("/pack/file/{name}")
def pack_download(name: str):
    p = skill_sync.pack_file(name)
    if p is None:
        raise HTTPException(404, "no such pack")
    return FileResponse(p, media_type="application/zip", filename=p.name)


@router.post("/pack/import")
async def pack_import(file: UploadFile = File(...), overwrite: bool = False):
    fd, name = tempfile.mkstemp(prefix="skill-pack-", suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        return await run_in_threadpool(skill_sync.import_pack, Path(name), overwrite)
    except (ValueError, zipfile.BadZipFile) as e:
        raise HTTPException(400, str(e)) from None
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


class ToggleBody(BaseModel):
    enabled: bool
    project_id: str = ""
    agent: str = ""


@router.post("/{skill_id}")
def toggle(skill_id: str, body: ToggleBody):
    return skills.set_skill(skill_id, body.enabled, body.project_id, body.agent)
