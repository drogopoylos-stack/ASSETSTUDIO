"""Small shared helpers: thumbnails, file info, slugs, image dims, mesh info."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

THUMB_SIZE = (384, 384)
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tga"}
_MESH_EXT = {".glb", ".gltf", ".obj", ".fbx", ".ply", ".stl"}


def slugify(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "asset").strip()).strip("-_")
    return (s or "asset")[:maxlen].lower()


def file_size(path: str | Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXT


def is_mesh(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _MESH_EXT


def image_dims(path: str | Path) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return None, None


def make_thumbnail(src: str | Path, dest: str | Path, size: tuple[int, int] = THUMB_SIZE) -> Optional[str]:
    """Create a PNG thumbnail of an image; returns dest path or None on failure."""
    try:
        from PIL import Image

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGBA")
            im.thumbnail(size)
            im.save(dest, "PNG")
        return str(dest)
    except Exception:
        return None


def mesh_info(path: str | Path) -> dict:
    """Best-effort poly/vertex count + bounds using trimesh."""
    info: dict = {}
    try:
        import trimesh

        scene = trimesh.load(str(path), force="scene")
        meshes = [g for g in scene.geometry.values()] if hasattr(scene, "geometry") else [scene]
        faces = sum(int(getattr(m, "faces", []).__len__()) for m in meshes if hasattr(m, "faces"))
        verts = sum(int(getattr(m, "vertices", []).__len__()) for m in meshes if hasattr(m, "vertices"))
        info["polys"] = faces
        info["vertices"] = verts
        info["meshes"] = len(meshes)
        try:
            b = scene.bounds
            if b is not None:
                info["bounds"] = [list(map(float, b[0])), list(map(float, b[1]))]
        except Exception:
            pass
    except Exception as e:  # pragma: no cover
        info["mesh_info_error"] = str(e)
    return info


def human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"
