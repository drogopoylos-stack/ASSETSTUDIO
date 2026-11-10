"""The end of a /gamemodel request: the finished model into the game AND into the Catalog, in one step.

An agent builds on the forge bench, where the person watches it live in the Studio Engine. When the
model is done, `publish` writes what the bench holds to <game>/assets/models/<name>.glb, has Blender
render a front view for the Catalog's thumbnail, and files a copy on the Catalog shelf it belongs to,
so the model is in the game, in the Library and in the Catalog without three calls to get wrong.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .config import DATA_DIR

SCRIPTS = Path(__file__).resolve().parent / "blender_scripts"


def _game(project: str) -> Path:
    from . import workspace
    from .polyhaven import _game_root
    root = workspace._check_browse(project)
    return _game_root(root.parent if root.is_file() else root)


def publish(project: str, name: str, category: str = "", license: str = "", tags: list | None = None,
            prompt: str = "") -> dict:
    from . import live
    if not (name or "").strip():
        return {"ok": False, "error": "name is required: what the model is called in the Catalog"}
    try:
        game = _game(project)
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": str(ex)}
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60] or "model"
    dest = game / ("public/assets/models" if (game / "public").is_dir() else "assets/models") / (slug + ".glb")
    exp = live.export_glb(project, str(dest))
    if not exp.get("ok"):
        return {"ok": False, "error": "the bench could not be exported: " + str(exp.get("error", ""))}

    # The Catalog's thumbnail: Blender's front view of the exported file.
    preview = ""
    try:
        from .cc_session import _blender_exe
        exe = _blender_exe()
        shots = DATA_DIR / "tmp" / "publish" / slug
        if exe:
            r = subprocess.run([exe, "--background", "--factory-startup", "--python", str(SCRIPTS / "glb_ref.py"), "--",
                                json.dumps({"glb": str(dest), "out": str(shots)})],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            if (shots / "front.png").is_file():
                preview = str(shots / "front.png")
    except Exception:                                    # noqa: BLE001
        pass                                             # a model with no thumbnail is still a model

    from .routers.catalog import ImportBody, import_model
    try:
        asset = import_model(ImportBody(path=str(dest), name=name.strip(), category=category or "", preview_path=preview or None,
                                        tags=list(tags or []) + ["gamemodel"], prompt=prompt, target_game=game.name,
                                        license=license or "own work", commercial_ok=True))
    except Exception as ex:                              # noqa: BLE001
        detail = getattr(ex, "detail", None) or str(ex)
        return {"ok": False, "error": "exported to the game, but the Catalog did not take it: %s" % str(detail)[:300],
                "path": str(dest)}
    rel = dest.resolve().relative_to(game.resolve()).as_posix()
    return {"ok": True, "game_path": rel, "path": str(dest), "bytes": exp.get("bytes"), "stats": exp.get("stats"),
            "catalog_id": asset.id, "catalog_path": asset.path, "category": asset.category, "preview": bool(preview)}
