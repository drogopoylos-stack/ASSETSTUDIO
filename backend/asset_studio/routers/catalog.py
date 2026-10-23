"""Asset catalog: list/filter, detail, edit metadata, serve files, summary."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db, library
from ..config import DATA_DIR
from ..models import Asset, AssetType, StageType

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
    category: Optional[str] = None,
    type: Optional[str] = None,
):
    # category=unsorted asks for the shelf with no category; no category param means every shelf.
    cat = None if category is None or category == "" else library.normalize_category(category)
    return db.list_assets(stage=stage, target_game=target_game, tag=tag, search=search,
                          limit=limit, offset=offset, category=cat, type=type or None)


@router.get("/catalog/categories")
def categories(type: Optional[str] = None):
    """The library shelves with how many assets each holds: the fixed ones first (also when
    empty, so the Catalog always offers them), then any shelf an agent invented, then unsorted."""
    counts = db.category_counts(type or None)
    out = [{"id": cid, "label": label, "count": counts.get(cid, 0)}
           for cid, label in library.CATEGORIES]
    known = {cid for cid, _ in library.CATEGORIES}
    for cid in sorted(c for c in counts if c and c not in known):
        out.append({"id": cid, "label": library.category_label(cid), "count": counts[cid]})
    out.append({"id": library.UNSORTED, "label": "Unsorted", "count": counts.get("", 0)})
    return {"categories": out, "total": sum(counts.values()),
            "folder": str(library.MODELS_DIR)}


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
    category: Optional[str] = None       # a shelf id; "" or "unsorted" takes it off its shelf


@router.patch("/assets/{asset_id}", response_model=Asset)
def patch_asset(asset_id: str, patch: AssetPatch):
    a = db.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    data = a.model_dump()
    for k, v in patch.model_dump(exclude_none=True).items():
        data[k] = v
    if patch.category is not None:
        data["category"] = library.normalize_category(patch.category)
    updated = Asset.model_validate(data)
    # A new shelf or name moves the model file to match (library.py). A model outside the
    # library (an import from your own folder) is filed now, by copy.
    if updated.type == AssetType.model and (
            updated.category != a.category or updated.name != a.name or not library.in_library(a.path)):
        updated = library.file_asset(updated)
    db.save_asset(updated)
    return updated


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, delete_file: bool = False):
    a = db.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    if delete_file:
        # A multi-file model (.gltf/.obj) has a folder of its own on its library shelf.
        unit = Path(a.path).parent if a.path else None
        if unit and library.in_library(a.path) and unit.name.endswith(f"-{a.id}"):
            import shutil
            shutil.rmtree(unit, ignore_errors=True)
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


# ---------------------------------------------------------------------------
# The model library (library.py): the folder every generated 3D model is filed in.
# ---------------------------------------------------------------------------
class RevealBody(BaseModel):
    asset_id: Optional[str] = None
    category: Optional[str] = None


@router.post("/library/reveal")
def reveal_library(body: RevealBody):
    """Open the library in Explorer: an asset's file selected, a shelf, or the whole library."""
    import subprocess
    if body.asset_id:
        a = db.get_asset(body.asset_id)
        if not a or not a.path or not Path(a.path).exists():
            raise HTTPException(404, "asset file not found")
        target, select = Path(a.path), True
    else:
        target, select = library.shelf_dir(body.category or ""), False
        target.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            if select:
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target.parent if select else target)])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"could not open file explorer: {e}")
    return {"ok": True, "path": str(target)}


class ImportBody(BaseModel):
    path: str
    name: str = ""
    category: str = ""
    preview_path: Optional[str] = None
    tags: list[str] = []
    prompt: str = ""
    target_game: str = ""
    license: str = ""
    commercial_ok: Optional[bool] = None


@router.post("/library/import", response_model=Asset)
def import_model(body: ImportBody):
    """Add a 3D model made outside a Studio job (Blender, a download, an older run) to the
    library and the Catalog. The file is COPIED onto its shelf; the original stays where it is."""
    from ..util import is_mesh, mesh_info
    src = Path(body.path)
    if not src.is_file():
        raise HTTPException(404, f"file not found: {body.path}")
    if not is_mesh(str(src)):
        raise HTTPException(400, "not a 3D model file")
    meta: dict[str, Any] = {"imported_from": str(src)}
    try:
        meta.update(mesh_info(str(src)))
    except Exception:
        pass
    asset = Asset(
        name=body.name.strip() or src.stem, stage=StageType.gen3d, type=AssetType.model,
        path=str(src.resolve()), preview_path=body.preview_path, size_bytes=src.stat().st_size,
        meta=meta, tags=body.tags, target_game=body.target_game, provider_id="import",
        prompt=body.prompt, license=body.license, commercial_ok=body.commercial_ok,
        category=library.normalize_category(body.category))
    filed = library.file_asset(asset)
    if not library.in_library(filed.path):
        raise HTTPException(500, "could not copy the model into the library")
    db.save_asset(filed)
    return filed
