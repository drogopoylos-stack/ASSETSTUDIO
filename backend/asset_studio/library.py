"""The model library: every generated 3D model, filed by category in one folder you can browse.

A job writes its output into its own scratch folder, ``data/jobs/<job id>/``, among inputs, logs and
intermediate files. That is no place to look for "the sword we made last week". So when a job
finishes, each ``model`` asset it produced is moved to::

    data/library/models/<category>/<name>-<asset id>.glb      (+ .preview.png beside it)

and the asset's ``path`` follows it, so the Catalog, downloads and later pipeline steps all read
the library copy. The folder stays under DATA_DIR, which is what the catalog's file routes serve.

The category is the asset's ``category`` field. The job request carries it (an agent sends
``"category": "weapon"``); without one the model goes to ``unsorted/`` until someone files it from
the Catalog. Changing the category or the name in the Catalog moves the file to match.

The asset id in the file name keeps two models of the same name apart, and lets a file found in
Explorer be traced back to its catalog row.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, JOBS_DIR
from .models import Asset, AssetType

LIBRARY_DIR = DATA_DIR / "library"
MODELS_DIR = LIBRARY_DIR / "models"
UNSORTED = "unsorted"

# The shelves the Catalog offers. An agent may send any other slug too ("vegetation"), and it
# becomes a shelf of its own; these are only the ones that always show.
CATEGORIES: list[tuple[str, str]] = [
    ("main-character", "Main characters"),
    ("character", "Characters & NPCs"),
    ("enemy", "Enemies"),
    ("creature", "Creatures"),
    ("weapon", "Weapons"),
    ("armor", "Armor & gear"),
    ("item", "Items & pickups"),
    ("prop", "Props"),
    ("environment", "Environment"),
    ("building", "Buildings"),
    ("vehicle", "Vehicles"),
    ("other", "Other"),
]
_LABELS = dict(CATEGORIES)

# What people (and agents) actually type, mapped to a shelf.
_ALIASES = {
    "main": "main-character", "hero": "main-character", "heroes": "main-character",
    "player": "main-character", "protagonist": "main-character", "main-characters": "main-character",
    "characters": "character", "npc": "character", "npcs": "character",
    "enemies": "enemy", "monster": "enemy", "monsters": "enemy", "boss": "enemy", "bosses": "enemy",
    "creatures": "creature", "animal": "creature", "animals": "creature",
    "weapons": "weapon",
    "armour": "armor", "armors": "armor", "gear": "armor",
    "items": "item", "pickup": "item", "pickups": "item", "loot": "item",
    "props": "prop",
    "environments": "environment", "env": "environment", "nature": "environment",
    "buildings": "building", "architecture": "building", "structure": "building",
    "vehicles": "vehicle",
}

# Formats that carry their textures inside the one file. Anything else (.gltf, .obj) brings its
# sidecar files along in a folder of its own.
_SINGLE_FILE = {".glb", ".fbx", ".vrm", ".usdz", ".ply", ".stl", ".blend"}
_SIDECARS = {".bin", ".mtl", ".png", ".jpg", ".jpeg", ".webp", ".tga", ".ktx2"}


def normalize_category(value: Optional[str]) -> str:
    """'Main characters' -> 'main-character', 'Weapons' -> 'weapon', '' -> ''."""
    slug = _slug(value or "")
    if not slug or slug == UNSORTED:
        return ""
    return _ALIASES.get(slug, slug)


def category_label(slug: str) -> str:
    if not slug:
        return "Unsorted"
    return _LABELS.get(slug) or slug.replace("-", " ").capitalize()


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].strip("-")


def in_library(path: Optional[str]) -> bool:
    if not path:
        return False
    try:
        return MODELS_DIR.resolve() in Path(path).resolve().parents
    except OSError:
        return False


def _target(asset: Asset) -> Path:
    """Where this asset's model file belongs, from its category and name."""
    src = Path(asset.path)
    stem = _slug(Path(asset.name).stem if asset.name.lower().endswith(src.suffix.lower())
                 else asset.name) or "model"
    base = f"{stem}-{asset.id}"
    shelf = MODELS_DIR / (asset.category or UNSORTED)
    if src.suffix.lower() in _SINGLE_FILE:
        return shelf / f"{base}{src.suffix.lower()}"
    return shelf / base / src.name


def _unit(path: Path) -> Path:
    """The thing to move: the file, or the folder a multi-file model was given."""
    return path.parent if path.suffix.lower() not in _SINGLE_FILE else path


def _move_or_copy(src: Path, dst: Path, move: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dst))
    elif src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _owned_by_job(path: Path, job_id: Optional[str]) -> bool:
    """A file in the job's own scratch folder is the job's to give away. Anything else (an input,
    a file a provider pointed at) is copied, never moved out from under its owner."""
    if not job_id:
        return False
    try:
        return (Path(JOBS_DIR) / job_id).resolve() in path.resolve().parents
    except OSError:
        return False


def file_asset(asset: Asset) -> Asset:
    """Put a model asset on its library shelf and return it with the new paths. Not a model, a
    missing file, or already in place: returned unchanged. Never raises; a failed move leaves the
    asset where it was, which is still a working asset."""
    if asset.type != AssetType.model or not asset.path:
        return asset
    src = Path(asset.path)
    if not src.is_file():
        return asset
    dst = _target(asset)
    try:
        if src.resolve() == dst.resolve():
            return asset
    except OSError:
        return asset
    try:
        if in_library(asset.path):
            # Re-shelving (new category or name): move the whole unit, file or folder.
            old_unit, new_unit = _unit(src), _unit(dst)
            if old_unit != src and new_unit != dst:
                _move_or_copy(old_unit, new_unit, move=True)
                # A preview inside the folder travelled with it.
                if asset.preview_path:
                    prev = Path(asset.preview_path)
                    if old_unit.resolve() in prev.resolve().parents:
                        asset = asset.model_copy(
                            update={"preview_path": str(new_unit / prev.relative_to(old_unit))})
            else:
                _move_or_copy(src, dst, move=True)
        elif src.suffix.lower() in _SINGLE_FILE:
            _move_or_copy(src, dst, move=_owned_by_job(src, asset.job_id))
        else:
            move = _owned_by_job(src, asset.job_id)
            _move_or_copy(src, dst, move=move)
            for side in src.parent.iterdir():
                if side.is_file() and side.suffix.lower() in _SIDECARS and side != src \
                        and ".preview." not in side.name and ".thumb." not in side.name:
                    shutil.copy2(side, dst.parent / side.name)
    except OSError as e:
        print(f"[library] could not file {asset.id} ({src}): {e}")
        return asset

    data = asset.model_dump()
    data["path"] = str(dst.resolve())
    if asset.preview_path and Path(asset.preview_path).is_file():
        prev = Path(asset.preview_path)
        new_prev = dst.parent / f"{dst.stem}.preview{prev.suffix.lower()}"
        try:
            if prev.resolve() != new_prev.resolve():
                own = in_library(str(prev)) or _owned_by_job(prev, asset.job_id)
                _move_or_copy(prev, new_prev, move=own)
            data["preview_path"] = str(new_prev.resolve())
        except OSError as e:
            print(f"[library] could not file the preview of {asset.id}: {e}")
    _prune_empty(src.parent)
    return Asset.model_validate(data)


def _prune_empty(folder: Path) -> None:
    """Remove a shelf (or model folder) a move left empty. Only inside the library."""
    try:
        while folder != MODELS_DIR and MODELS_DIR.resolve() in folder.resolve().parents:
            if not folder.exists():
                folder = folder.parent
                continue
            if any(folder.iterdir()):
                return
            folder.rmdir()
            folder = folder.parent
    except OSError:
        pass


def name_from_job(asset: Asset, job, index: int = 0) -> Asset:
    """'Viking rune axe' is a better name than 'comfyui3d-1a2b3c.glb'. Applied only while the asset
    still carries its bare file name, so a provider that named it keeps that name. The source, in
    order: params["name"], the job label an agent sent, the prompt. The Studio's own form labels
    its jobs "Generate 3D · <provider>", which names the tool, not the thing, so that is skipped."""
    if asset.name != Path(asset.path).name:
        return asset
    label = (job.label or "").strip()
    if " · " in label:
        label = ""
    name = str(job.params.get("name") or "").strip() or label or (asset.prompt or "").strip()
    name = " ".join(name.split())[:80].strip()
    if name:
        asset.name = name if index == 0 else f"{name} ({index + 1})"
    return asset


def backfill() -> int:
    """File every model asset still sitting in a job folder. Runs at startup; cheap when there is
    nothing to do, and safe to run again."""
    from . import db
    moved = 0
    for a in db.list_assets(type="model", limit=100000):
        if in_library(a.path) or not a.path or not Path(a.path).is_file():
            continue
        filed = file_asset(a)
        if filed.path != a.path:
            db.save_asset(filed)
            moved += 1
    return moved


def shelf_dir(category: str = "") -> Path:
    return MODELS_DIR / (normalize_category(category) or UNSORTED) if category else MODELS_DIR
