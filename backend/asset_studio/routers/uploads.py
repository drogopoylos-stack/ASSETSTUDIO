"""Upload arbitrary reference files (e.g. images) to use as job inputs.

Saved under DATA_DIR/uploads and returned as an absolute path, which the job
queue accepts directly as an input (alongside catalog asset ids)."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import DATA_DIR

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

_UPLOAD_DIR = DATA_DIR / "uploads"


@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    src = Path(file.filename or "ref.png")
    ext = src.suffix.lower() or ".png"
    safe = "".join(c for c in src.stem if c.isalnum() or c in " ._-").strip()[:40] or "ref"
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    dest = _UPLOAD_DIR / f"{ts}-{safe}{ext}"
    dest.write_bytes(data)
    return {"ok": True, "path": str(dest.resolve()), "name": dest.name}
