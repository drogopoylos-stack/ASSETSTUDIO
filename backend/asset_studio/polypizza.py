"""Poly Pizza: low-poly game models, many animated, CC0 or CC-BY — the creatures Poly Haven has not.

It holds Quaternius's animated animals, characters and monsters (CC0) among more than 10 000 models.
The API needs a key (a free account); it is kept in the OS keychain as "polypizza" and never sent
anywhere but api.poly.pizza. A download goes into the game with a LICENSE.txt that carries the
credit a CC-BY model must show, and Blender measures it and renders it front and side, because a
library model comes at any scale.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.poly.pizza/v1.1"
HOSTS = {"api.poly.pizza", "static.poly.pizza"}
SCRIPTS = Path(__file__).resolve().parent / "blender_scripts"


def _key() -> str:
    from . import keychain
    return keychain.get_key("polypizza") or ""


def _get(url: str, auth: bool, timeout: float = 60) -> bytes:
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in HOSTS:
        raise ValueError("refused: %s is not a Poly Pizza host" % host)
    headers = {"User-Agent": "AssetStudio/0.1"}
    if auth:
        headers["X-Auth-Token"] = _key()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def _row(x: dict) -> dict:
    lic = str(x.get("Licence") or "")
    return {
        "id": x.get("ID"), "title": x.get("Title"), "licence": lic, "cc0": lic.upper().startswith("CC0"),
        "creator": (x.get("Creator") or {}).get("Username"), "attribution": x.get("Attribution") or "",
        "animated": bool(x.get("Animated")), "triangles": x.get("Tri Count"), "category": x.get("Category"),
        "tags": x.get("Tags") or [], "thumb": x.get("Thumbnail"), "download": x.get("Download"),
    }


def status() -> dict:
    return {"ok": True, "key": bool(_key())}


def search(q: str, animated: bool = False, licence: str = "", category: str = "", limit: int = 30) -> dict:
    """Models whose title or tags match `q`. `licence`: "CC0" keeps only those that need no credit."""
    if not _key():
        return {"ok": False, "error": "no Poly Pizza key: store one in the keychain as 'polypizza'"}
    q = (q or "").strip()
    if not q:
        return {"ok": False, "error": "q is required"}
    params = {"Limit": max(1, min(32, int(limit or 30)))}
    if animated:
        params["Animated"] = 1
    if category:
        params["Category"] = category
    try:
        d = json.loads(_get(API + "/search/" + urllib.parse.quote(q) + "?" + urllib.parse.urlencode(params), auth=True))
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": "Poly Pizza did not answer: %s" % str(ex)[:200]}
    rows = [_row(x) for x in d.get("results") or []]
    if animated:
        rows = [r for r in rows if r["animated"]]
    if licence.upper() == "CC0":
        rows = [r for r in rows if r["cc0"]]
    return {"ok": True, "total": d.get("total"), "rows": rows}


def _blender() -> str:
    from .cc_session import _blender_exe
    return _blender_exe()


def download(project: str, model_id: str) -> dict:
    """One model into the game: the .glb, LICENSE.txt with its credit, renders and its true size."""
    from . import workspace
    from .polyhaven import _game_root
    if not re.match(r"^[A-Za-z0-9_-]{4,32}$", model_id or ""):
        return {"ok": False, "error": "that is not a Poly Pizza model id"}
    if not _key():
        return {"ok": False, "error": "no Poly Pizza key: store one in the keychain as 'polypizza'"}
    try:
        root = workspace._check_browse(project)
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": str(ex)}
    game = _game_root(root.parent if root.is_file() else root)
    try:
        x = json.loads(_get(API + "/model/" + model_id, auth=True))
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": "no model %s on Poly Pizza: %s" % (model_id, str(ex)[:160])}
    row = _row(x)
    if not row["download"]:
        return {"ok": False, "error": "Poly Pizza gave no file for " + model_id}
    base = game / ("public/assets/polypizza" if (game / "public").is_dir() else "assets/polypizza")
    folder = base / model_id
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", str(row["title"] or model_id).lower()).strip("_") or model_id
    glb = folder / (slug + ".glb")
    data = _get(row["download"], auth=False, timeout=180)
    if data[:4] != b"glTF":
        return {"ok": False, "error": "the file Poly Pizza gave is not a .glb"}
    glb.write_bytes(data)
    credit = row["attribution"] or "%s by %s (Poly Pizza, %s)" % (row["title"], row["creator"], row["licence"])
    (folder / "LICENSE.txt").write_text(
        "%s\n\nLicence: %s\nCreator: %s\nSource: https://poly.pizza/m/%s\nDownloaded: %s\n\n%s\n" % (
            row["title"], row["licence"], row["creator"], model_id, time.strftime("%Y-%m-%d"),
            "CC0: no credit is needed." if row["cc0"] else "CC-BY: show this credit where the game lists its credits:\n" + credit),
        encoding="utf-8")
    meta = {**row, "file": glb.name}
    exe = _blender()
    if exe:
        r = subprocess.run([exe, "--background", "--factory-startup", "--python", str(SCRIPTS / "glb_ref.py"), "--",
                            json.dumps({"glb": str(glb), "out": str(folder)})],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        line = next((ln for ln in (r.stdout or "").splitlines() if ln.startswith("REF|")), "")
        if line:
            meta.update(json.loads(line[4:]))
    (folder / "polypizza.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    rel = folder.resolve().relative_to(game.resolve()).as_posix()
    out = {"ok": True, **meta, "folder": rel, "main": rel + "/" + glb.name, "main_path": str(glb),
           "license": rel + "/LICENSE.txt", "credit_needed": not row["cc0"], "credit": "" if row["cc0"] else credit}
    for v in ("front", "side"):
        if (folder / (v + ".png")).is_file():
            out[v + "_path"] = str(folder / (v + ".png"))
    out["scale_note"] = ("a library model comes at any scale: dimensions_m is what the file measures, not the real "
                         "creature — scale it to the real size before use")
    return out


def remove_downloaded(project: str, model_id: str) -> None:
    """For tests and for a person who changes their mind: the model's folder out of the game."""
    from . import workspace
    from .polyhaven import _game_root
    root = workspace._check_browse(project)
    game = _game_root(root.parent if root.is_file() else root)
    for base in (game / "public/assets/polypizza", game / "assets/polypizza"):
        if (base / model_id).is_dir():
            shutil.rmtree(base / model_id)
