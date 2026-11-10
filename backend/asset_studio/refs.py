"""Human references at real size, for what Poly Haven does not have.

Poly Haven has props, furniture, plants, rocks and statues, and no human and no creature. Two free
sources fill that, both made with Blender in background mode, on the Studio's own Blender profile
(data/blender_profile), so the person's Blender and its add-ons are never touched:

  * Blender Studio's Human Base Meshes (CC0): realistic and stylised bodies, heads, hands, feet,
    eyes, jaws and skulls at real size. Downloaded and exported once (`prepare`).
  * MPFB2, MakeHuman for Blender (its output is CC0): a human made to order — height in metres,
    gender, age, muscle, weight, proportions — optionally rigged (Unreal-style game bones, or
    Mixamo's names so Mixamo animations drive it) (`human`).

Every reference is a folder with model.glb (Y up, metres), front.png, side.png and meta.json (real
size, licence, source). Paths come back absolute, which is what the forge's `ref` and the bench take.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

REF = DATA_DIR / "references"
SOURCES = REF / "sources"
PROFILE = DATA_DIR / "blender_profile"
SCRIPTS = Path(__file__).resolve().parent / "blender_scripts"

HBM_URL = "https://download.blender.org/demo/asset-bundles/human-base-meshes/human-base-meshes-bundle-v1.2.0.zip"
MPFB_URL = ("https://extensions.blender.org/download/sha256:c075b3e9b94b8e5556a5f03f4653602d02ea306de08126e8f55ed6dee28a2dad/"
            "add-on-mpfb-v2.0.18.zip")
MPFB_SHA = "c075b3e9b94b8e5556a5f03f4653602d02ea306de08126e8f55ed6dee28a2dad"
RIGS = ("", "game_engine", "mixamo", "default")


def _blender() -> str:
    from .cc_session import _blender_exe
    return _blender_exe()


def _run(args: list[str], timeout: float, profile: bool = False) -> str:
    import os
    env = dict(os.environ)
    if profile:
        env["BLENDER_USER_RESOURCES"] = str(PROFILE)
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return (r.stdout or "") + (r.stderr or "")


def _fetch(url: str, dest: Path, timeout: float = 600) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "AssetStudio/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def status() -> dict:
    return {"humans": (REF / "humans").is_dir() and any((REF / "humans").glob("*/meta.json")),
            "mpfb": (PROFILE / "extensions" / "user_default" / "mpfb").is_dir(),
            "blender": bool(_blender())}


def prepare(force: bool = False) -> dict:
    """Get both sources ready: the base meshes exported, MPFB2 installed in the Studio's profile.
    Minutes the first time (two downloads of about 50 MB, then Blender), nothing after."""
    exe = _blender()
    if not exe:
        return {"ok": False, "error": "Blender was not found. Set tools.blender_path in Settings."}
    done = []
    if force or not status()["humans"]:
        z = SOURCES / "human-base-meshes-v1.2.0.zip"
        if not z.is_file():
            _fetch(HBM_URL, z)
        unpacked = SOURCES / "hbm"
        unpacked.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zz:
            for m in zz.namelist():
                # Only the .blend is used; a path that leaves the folder is never written.
                if m.endswith(".blend") and "/" not in m.strip("/") and ".." not in m:
                    zz.extract(m, unpacked)
        blend = next(unpacked.glob("*.blend"), None)
        if not blend:
            return {"ok": False, "error": "the Human Base Meshes bundle held no .blend"}
        log = _run([exe, "--background", "--factory-startup", str(blend), "--python", str(SCRIPTS / "hbm_export.py"),
                    "--", str(REF / "humans")], timeout=1800)
        if "REF_DONE|" not in log:
            return {"ok": False, "error": "the base meshes did not export: " + log[-600:]}
        done.append("human base meshes")
    if force or not status()["mpfb"]:
        z = SOURCES / "mpfb-2.0.18.zip"
        if not z.is_file():
            _fetch(MPFB_URL, z)
        if hashlib.sha256(z.read_bytes()).hexdigest() != MPFB_SHA:
            z.unlink(missing_ok=True)
            return {"ok": False, "error": "the MPFB2 download did not match its published checksum"}
        PROFILE.mkdir(parents=True, exist_ok=True)
        log = _run([exe, "--factory-startup", "--command", "extension", "install-file", "-r", "user_default", str(z)],
                   timeout=600, profile=True)
        if not status()["mpfb"]:
            return {"ok": False, "error": "MPFB2 did not install: " + log[-600:]}
        done.append("MPFB2")
    return {"ok": True, "prepared": done, **status()}


def _row(meta_file: Path) -> Optional[dict]:
    try:
        m = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    d = meta_file.parent
    files = m.get("files") or {}
    return {**m, "folder": str(d), "model_path": str(d / files.get("model", "model.glb")),
            "front_path": str(d / files.get("front", "front.png")), "side_path": str(d / files.get("side", "side.png"))}


def search(q: str = "", kind: str = "", limit: int = 50) -> dict:
    """References whose name, kind, style or sex hold every word of `q`."""
    rows = []
    for sub in ("humans", "generated"):
        for f in sorted((REF / sub).glob("*/meta.json")):
            r = _row(f)
            if r:
                rows.append(r)
    words = [w for w in re.split(r"[\W_]+", (q or "").lower()) if w]
    # Whole words: "male" must not find "female".
    toks = lambda r: set(re.split(r"[\W_]+", " ".join(str(r.get(k, "")) for k in ("name", "kind", "style", "sex", "id")).lower()))  # noqa: E731
    hits = [r for r in rows if all(any(t.startswith(w) for t in toks(r)) for w in words) and (not kind or r.get("kind") == kind)]
    return {"ok": True, "total": len(hits), "rows": hits[:max(1, min(200, limit))], **({"hint": "POST /api/refs/prepare first"} if not rows else {})}


def _num(v, lo, hi, d):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return d
    return max(lo, min(hi, f)) if f == f else d


def human(body: dict) -> dict:
    """A human made to order, at real size. The same request twice returns the same folder."""
    if not status()["mpfb"]:
        r = prepare()
        if not r.get("ok"):
            return r
    rig = str(body.get("rig") or "")
    if rig not in RIGS:
        return {"ok": False, "error": "rig is one of: none, " + ", ".join(r for r in RIGS if r)}
    p = {"height_m": _num(body.get("height_m"), 0.5, 3.0, 1.75), "gender": _num(body.get("gender"), 0, 1, 0.5),
         "age_years": _num(body.get("age_years"), 1, 90, 25), "muscle": _num(body.get("muscle"), 0, 1, 0.5),
         "weight": _num(body.get("weight"), 0, 1, 0.5), "proportions": _num(body.get("proportions"), 0, 1, 0.5), "rig": rig}
    key = hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10]
    name = re.sub(r"[^a-z0-9]+", "_", str(body.get("name") or "human").lower()).strip("_")[:40] or "human"
    out = REF / "generated" / f"{name}_{key}"
    if not (out / "meta.json").is_file():
        log = _run([_blender(), "--background", "--python", str(SCRIPTS / "mpfb_human.py"), "--",
                    json.dumps({**p, "out": str(out), "name": body.get("name") or "human"})], timeout=900, profile=True)
        if "HUMAN|" not in log:
            return {"ok": False, "error": "the human was not made: " + log[-600:]}
    r = _row(out / "meta.json") or {}
    if body.get("project"):
        r = {**r, **copy_to(body["project"], r)}
    return {"ok": True, **r}


def copy_to(project: str, row: dict) -> dict:
    """The reference's files into the game, under assets/references/, for a placeholder in the scene."""
    from . import workspace
    from .polyhaven import _game_root
    root = workspace._check_browse(project)
    game = _game_root(root.parent if root.is_file() else root)
    base = game / ("public/assets/references" if (game / "public").is_dir() else "assets/references")
    dest = base / Path(row["folder"]).name
    dest.mkdir(parents=True, exist_ok=True)
    for f in ("model.glb", "front.png", "side.png", "meta.json"):
        s = Path(row["folder"]) / f
        if s.is_file():
            shutil.copy2(s, dest / f)
    rel = dest.resolve().relative_to(game.resolve()).as_posix()
    return {"project_folder": rel, "project_model": rel + "/model.glb"}


def get(ref_id: str, project: str = "") -> dict:
    """One reference by its id ("hbm/body_male_realistic", "human/<folder>")."""
    for r in search("", "", 500)["rows"]:
        if r.get("id") == ref_id:
            return {"ok": True, **r, **(copy_to(project, r) if project else {})}
    return {"ok": False, "error": "no reference %r — GET /api/refs/search lists them" % ref_id}
