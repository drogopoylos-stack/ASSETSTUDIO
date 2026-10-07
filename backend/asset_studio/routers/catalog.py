"""Asset catalog: list/filter, detail, edit metadata, serve files, summary."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..config import DATA_DIR
from ..models import Asset

router = APIRouter(prefix="/api", tags=["catalog"])


def _safe(path: str) -> Path:
    p = Path(path).resolve()
    if DATA_DIR.resolve() not in p.parents and p != DATA_DIR.resolve():
        raise HTTPException(403, "path outside data dir")
    if not p.exists():
        raise HTTPException(404, "file not found")
    return p


@router.get("/assets")
def list_assets(
    stage: Optional[str] = None,
    target_game: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
):
    return db.list_assets(stage=stage, target_game=target_game, tag=tag, search=search,
                          limit=limit, offset=offset)


@router.get("/catalog/summary")
def summary():
    return db.stats_summary()


@router.get("/assets/{asset_id}", response_model=Asset)
def get_asset(asset_id: str):
    a = db.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    return a


class AssetPatch(BaseModel):
    tags: Optional[list[str]] = None
    target_game: Optional[str] = None
    license: Optional[str] = None
    commercial_ok: Optional[bool] = None
    name: Optional[str] = None


@router.patch("/assets/{asset_id}", response_model=Asset)
def patch_asset(asset_id: str, patch: AssetPatch):
    a = db.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    data = a.model_dump()
    for k, v in patch.model_dump(exclude_none=True).items():
        data[k] = v
    updated = Asset.model_validate(data)
    db.save_asset(updated)
    return updated


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, delete_file: bool = False):
    a = db.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    if delete_file:
        for p in (a.path, a.preview_path):
            try:
                if p and Path(p).exists():
                    os.remove(p)
            except OSError:
                pass
    db.delete_asset(asset_id)
    return {"ok": True}


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: str):
    a = db.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    p = _safe(a.path)
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=mt, filename=p.name)


@router.get("/assets/{asset_id}/preview")
def asset_preview(asset_id: str):
    a = db.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    target = a.preview_path or a.path
    p = _safe(target)
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=mt)


@router.get("/file")
def serve_file(path: str = Query(...)):
    """Serve any file under the data dir (previews, turntables, job outputs)."""
    p = _safe(path)
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=mt)
