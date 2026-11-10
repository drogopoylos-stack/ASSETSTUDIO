"""Poly Haven in the Studio: free (CC0) models, textures and HDRIs, found, downloaded and kept with
their license — and used as REFERENCES, which is what Blender users take them for.

Three uses, in the order they pay:

  * A REFERENCE for something the forge builds. Each model comes with a clay render (the shape and
    nothing else, what a silhouette score wants), its textured render, and its real size in metres.
    An agent sets the clay render as the forge's `ref` and scores its own build against it, and checks
    its proportions against the real dimensions instead of guessing them.
  * A PART of the game: the glTF goes into the project, where the Library lists it and the scene
    tools place it, like any model of the game's own.
  * MATERIALS and LIGHT: a texture set (colour, normal, roughness, AO, height) with a material.json
    that names each map, and an HDRI for the environment.

Every download writes LICENSE.txt beside the files: the source page, the authors, the date, and the
words of Poly Haven's license page as they read that day. The assets are CC0, so a game may ship them
without credit; the file is there so that is never a question.

Only Poly Haven's own hosts are fetched (api.polyhaven.com, dl.polyhaven.org, cdn.polyhaven.com),
every file is checked against the md5 the API gives, and a path inside a model's file list may not
leave the asset's folder. Files land in `<project>/public/assets/polyhaven/` when the project has a
`public/` folder (a Vite game serves that), else in `<project>/assets/polyhaven/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

API = "https://api.polyhaven.com"
HOSTS = {"api.polyhaven.com", "dl.polyhaven.org", "cdn.polyhaven.com", "polyhaven.com"}
UA = "AssetStudio/1.0 (local game-asset tool)"
TYPES = {"models": 2, "textures": 1, "hdris": 0}
RESOLUTIONS = ("1k", "2k", "4k", "8k")
INDEX_TTL = 24 * 3600.0
MAX_BYTES = 600 * 1024 * 1024           # one download, all files together
_CACHE = DATA_DIR / "polyhaven"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_lock = threading.Lock()

# The maps a texture set is made of, as Poly Haven names them, and what a material calls them.
TEXTURE_MAPS = {"Diffuse": "baseColor", "nor_gl": "normal", "Rough": "roughness", "AO": "ao",
                "Displacement": "height", "arm": "arm", "Metal": "metalness"}


def _get(url: str, timeout: float = 60.0) -> bytes:
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in HOSTS:
        raise ValueError("refused: %s is not a Poly Haven host" % host)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str) -> dict:
    return json.loads(_get(url, 30.0).decode("utf-8"))


def _check_id(asset_id: str) -> str:
    a = str(asset_id or "").strip()            # Poly Haven ids keep their case: Barrel_01
    if not _ID.match(a):
        raise ValueError("not a Poly Haven asset id: %r" % asset_id)
    return a


def _check_type(kind: str) -> str:
    k = str(kind or "models").strip().lower()
    k = {"model": "models", "texture": "textures", "hdri": "hdris"}.get(k, k)
    if k not in TYPES:
        raise ValueError("type is models, textures or hdris")
    return k


# --------------------------------------------------------------------------------------- the index
def index(kind: str = "models", fresh: bool = False) -> dict:
    """Every asset of one type, cached on disk for a day: the list is a few hundred kilobytes."""
    kind = _check_type(kind)
    _CACHE.mkdir(parents=True, exist_ok=True)
    p = _CACHE / ("assets-%s.json" % kind)
    with _lock:
        if not fresh and p.is_file() and time.time() - p.stat().st_mtime < INDEX_TTL:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        data = _json("%s/assets?t=%s" % (API, kind))
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
        return data


def _thumb(asset_id: str, size: int = 256) -> str:
    return "https://cdn.polyhaven.com/asset_img/thumbs/%s.png?width=%d&height=%d" % (asset_id, size, size)


def _row(asset_id: str, a: dict) -> dict:
    row = {"id": asset_id, "name": a.get("name") or asset_id, "type": next((k for k, v in TYPES.items() if v == a.get("type")), ""),
           "categories": a.get("categories") or [], "tags": (a.get("tags") or [])[:10],
           "authors": list((a.get("authors") or {}).keys()), "downloads": a.get("download_count") or 0,
           "thumb": _thumb(asset_id), "page": "https://polyhaven.com/a/%s" % asset_id, "license": "CC0"}
    if a.get("dimensions"):
        # Poly Haven gives millimetres; the Studio works in metres.
        row["dimensions_m"] = [round(float(x) / 1000.0, 4) for x in a["dimensions"]]
    if a.get("polycount"):
        row["polycount"] = a["polycount"]
    return row


def search(kind: str = "models", q: str = "", category: str = "", limit: int = 60, offset: int = 0) -> dict:
    """Assets whose name, tags or categories hold every word of `q`, most downloaded first."""
    try:
        kind = _check_type(kind)
        data = index(kind)
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": "Poly Haven could not be reached: %s" % str(ex)[:300]}
    words = [w for w in re.split(r"\s+", str(q or "").lower()) if w]
    cat = str(category or "").strip().lower()
    rows = []
    for aid, a in data.items():
        cats = [str(c).lower() for c in a.get("categories") or []]
        if cat and cat not in cats:
            continue
        hay = " ".join([aid, str(a.get("name") or "").lower(), " ".join(cats),
                        " ".join(str(t).lower() for t in a.get("tags") or [])])
        if all(w in hay for w in words):
            rows.append((aid, a))
    rows.sort(key=lambda r: -(r[1].get("download_count") or 0))
    total = len(rows)
    lim = max(1, min(200, int(limit or 60)))
    off = max(0, int(offset or 0))
    cats: dict = {}
    for _aid, a in data.items():
        for c in a.get("categories") or []:
            cats[c] = cats.get(c, 0) + 1
    return {"ok": True, "type": kind, "total": total, "rows": [_row(aid, a) for aid, a in rows[off:off + lim]],
            "categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])[:40]), "license": "CC0"}


# --------------------------------------------------------------------------------------- download
def _folder(project: str, kind: str, asset_id: str) -> tuple[Path, str]:
    from . import workspace
    root = workspace._check_browse(project)
    if not root.is_dir():
        raise ValueError("the project is not a folder: %s" % project)
    base = root / "public" if (root / "public").is_dir() else root
    folder = base / "assets" / "polyhaven" / kind / asset_id
    return folder, base.relative_to(root).as_posix() if base != root else ""


def _safe_rel(rel: str) -> str:
    r = str(rel or "").replace("\\", "/").strip("/")
    if not r or r.startswith("/") or re.search(r"(^|/)\.\.(/|$)", r) or ":" in r:
        raise ValueError("refused: a file path in the asset's list leaves its folder: %r" % rel)
    return r


def _license_text() -> str:
    """The words of Poly Haven's license page, cached for a week; a short fixed text if unreachable."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    p = _CACHE / "license.txt"
    if p.is_file() and time.time() - p.stat().st_mtime < 7 * 24 * 3600:
        return p.read_text(encoding="utf-8")
    try:
        html = _get("https://polyhaven.com/license", 30.0).decode("utf-8", "replace")
        t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"&#x27;", "'", t).replace("&quot;", '"').replace("&amp;", "&")
        t = re.sub(r"\s+", " ", t)
        i = t.find("Asset License")
        text = t[i:i + 2400].strip() if i >= 0 else t[:2400]
        p.write_text(text, encoding="utf-8")
        return text
    except Exception:
        return ("Poly Haven: all assets (HDRIs, textures and 3D models) are licensed as CC0. "
                "See https://polyhaven.com/license (the page could not be fetched when this file was written).")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _plan(kind: str, files: dict, res: str) -> tuple[list[tuple[str, str, int, str]], str, dict]:
    """(relative path, url, size, md5) for every file, the main file, and texture map roles."""
    out: list = []
    main = ""
    roles: dict = {}
    if kind == "models":
        g = ((files.get("gltf") or {}).get(res) or {}).get("gltf")
        if not g:
            raise ValueError("no glTF at %s for this model" % res)
        main = _safe_rel(urllib.parse.urlparse(g["url"]).path.rsplit("/", 1)[-1])
        out.append((main, g["url"], int(g.get("size") or 0), g.get("md5") or ""))
        for rel, f in (g.get("include") or {}).items():
            out.append((_safe_rel(rel), f["url"], int(f.get("size") or 0), f.get("md5") or ""))
    elif kind == "textures":
        for name, role in TEXTURE_MAPS.items():
            f = ((files.get(name) or {}).get(res) or {})
            f = f.get("jpg") or f.get("png")
            if not f:
                continue
            rel = _safe_rel(urllib.parse.urlparse(f["url"]).path.rsplit("/", 1)[-1])
            out.append((rel, f["url"], int(f.get("size") or 0), f.get("md5") or ""))
            roles[role] = rel
        if not out:
            raise ValueError("no maps at %s for this texture" % res)
        main = roles.get("baseColor") or out[0][0]
    else:
        f = ((files.get("hdri") or {}).get(res) or {}).get("hdr")
        if not f:
            raise ValueError("no .hdr at %s for this HDRI" % res)
        main = _safe_rel(urllib.parse.urlparse(f["url"]).path.rsplit("/", 1)[-1])
        out.append((main, f["url"], int(f.get("size") or 0), f.get("md5") or ""))
    return out, main, roles


def pack_glb(gltf_path: Path) -> Path:
    """The glTF, its buffers and its images packed into ONE .glb beside it.

    A .gltf names its .bin and its textures by relative path, and a loader handed the file through an
    endpoint resolves them against the endpoint, so the forge's bench answered "Failed to load buffer".
    One file has nothing to resolve: the bench, the Library's viewer and a game all load it the same."""
    import base64
    folder = gltf_path.parent
    g = json.loads(gltf_path.read_text(encoding="utf-8"))
    blob = bytearray()

    def take(uri: str) -> bytes:
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        f = (folder / _safe_rel(urllib.parse.unquote(uri))).resolve()
        if folder.resolve() not in f.parents:
            raise ValueError("refused: %s leaves the asset's folder" % uri)
        return f.read_bytes()

    def put(data: bytes) -> int:
        while len(blob) % 4:
            blob.append(0)
        at = len(blob)
        blob.extend(data)
        return at

    starts = [put(take(b["uri"])) if b.get("uri") else put(b"") for b in g.get("buffers") or []]
    for v in g.get("bufferViews") or []:
        v["byteOffset"] = int(v.get("byteOffset") or 0) + starts[int(v.get("buffer") or 0)]
        v["buffer"] = 0
    views = g.setdefault("bufferViews", [])
    for im in g.get("images") or []:
        uri = im.pop("uri", None)
        if not uri:
            continue
        data = take(uri)
        at = put(data)
        views.append({"buffer": 0, "byteOffset": at, "byteLength": len(data)})
        im["bufferView"] = len(views) - 1
        im["mimeType"] = "image/png" if uri.lower().endswith(".png") or data[1:4] == b"PNG" else "image/jpeg"
    while len(blob) % 4:
        blob.append(0)
    g["buffers"] = [{"byteLength": len(blob)}]
    js = json.dumps(g, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    import struct
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob)
    dest = gltf_path.with_suffix(".glb")
    _write(dest, out)
    return dest


def download(project: str, asset_id: str, kind: str = "models", res: str = "1k") -> dict:
    """Fetch one asset into the project, with its references and its license. Idempotent: a file
    already there with the right md5 is not fetched again."""
    try:
        asset_id = _check_id(asset_id)
        kind = _check_type(kind)
        if res not in RESOLUTIONS:
            raise ValueError("res is one of %s" % ", ".join(RESOLUTIONS))
        folder, base_rel = _folder(project, kind, asset_id)
        info = _json("%s/info/%s" % (API, asset_id))
        if TYPES[kind] != info.get("type"):
            raise ValueError("%s is not one of the %s" % (asset_id, kind))
        files = _json("%s/files/%s" % (API, asset_id))
        plan, main, roles = _plan(kind, files, res)
    except PermissionError as ex:
        return {"ok": False, "error": str(ex)}
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": str(ex)[:400]}
    total = sum(s for _r, _u, s, _m in plan)
    if total > MAX_BYTES:
        return {"ok": False, "error": "%s at %s is %d MB; the limit is %d MB — take a smaller resolution"
                % (asset_id, res, total // 2**20, MAX_BYTES // 2**20)}
    fetched, kept, got = [], [], 0
    try:
        for rel, url, _size, md5 in plan:
            dest = folder / rel
            if dest.is_file() and md5 and hashlib.md5(dest.read_bytes()).hexdigest() == md5:
                kept.append(rel)
                continue
            data = _get(url, 300.0)
            if md5 and hashlib.md5(data).hexdigest() != md5:
                raise ValueError("%s did not match its md5: the download was damaged, nothing was kept" % rel)
            _write(dest, data)
            fetched.append(rel)
            got += len(data)
        refs = {}
        pics = [("reference.png", "https://cdn.polyhaven.com/asset_img/primary/%s.png?height=1024" % asset_id)]
        if kind == "models":
            pics.append(("reference-clay.png", "https://cdn.polyhaven.com/asset_img/renders/%s/clay.png?height=1024" % asset_id))
        for name, url in pics:
            try:
                _write(folder / name, _get(url, 60.0))
                refs[name.replace(".png", "").replace("-", "_")] = name
            except Exception:
                pass
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": str(ex)[:400], "fetched": fetched}

    glb = ""
    if kind == "models":
        try:
            glb = pack_glb(folder / main).name
        except Exception as ex:                          # noqa: BLE001
            return {"ok": False, "error": "the glTF could not be packed into one .glb: %s" % str(ex)[:300]}
    row = _row(asset_id, info)
    meta = {**row, "resolution": res, "main": glb or main, "gltf": main if glb else "", "files": [r for r, *_ in plan], "references": refs,
            "downloaded": time.strftime("%Y-%m-%d"), "source": "https://polyhaven.com/a/%s" % asset_id,
            "description": info.get("description") or ""}
    if roles:
        meta["maps"] = roles
        (folder / "material.json").write_text(json.dumps({"name": row["name"], "maps": roles,
                                                          "note": "normal is OpenGL convention (+Y), as three.js and glTF want"},
                                                         indent=2) + "\n", encoding="utf-8")
    (folder / "polyhaven.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    lic = ("%s\nSource: %s\nAuthors: %s\nDownloaded: %s, resolution %s\nLicense: CC0 1.0 (public domain). Use in any project, "
           "commercial included; no credit is required.\n\nThe license page, as it read on that day "
           "(https://polyhaven.com/license):\n\n%s\n" % (row["name"], meta["source"], ", ".join(row["authors"]) or "Poly Haven",
                                                        meta["downloaded"], res, _license_text()))
    (folder / "LICENSE.txt").write_text(lic, encoding="utf-8")

    root = Path(project).resolve()
    rel_folder = folder.resolve().relative_to(root).as_posix()
    # ABSOLUTE where another call takes the path: the forge's `ref` and /api/live/glb read a path as
    # given, and a project-relative one answered "no such file".
    out = {"ok": True, "id": asset_id, "type": kind, "name": row["name"], "folder": rel_folder,
           "main": rel_folder + "/" + (glb or main), "main_path": str(folder.resolve() / (glb or main)),
           "references": {k: str(folder.resolve() / v) for k, v in refs.items()},
           "license": rel_folder + "/LICENSE.txt", "fetched": len(fetched), "kept": len(kept),
           "bytes": got, "resolution": res}
    if row.get("dimensions_m"):
        out["dimensions_m"] = row["dimensions_m"]
    if roles:
        out["maps"] = {k: rel_folder + "/" + v for k, v in roles.items()}
        out["material"] = rel_folder + "/material.json"
    if base_rel:
        out["served_as"] = "/" + folder.resolve().relative_to((root / base_rel).resolve()).as_posix() + "/" + (glb or main)
    return out


def _game_root(start: Path) -> Path:
    """The game folder `start` is in: the nearest folder up that has Poly Haven downloads, or else
    the nearest that is a game (package.json, studio.game.json). An editor knows the file it has
    open and the workspace it is in, not always the game between them."""
    from . import workspace
    first_game = None
    for d in [start, *start.parents]:
        try:
            workspace._check_browse(str(d))
        except Exception:                                # noqa: BLE001
            break
        if (d / "public" / "assets" / "polyhaven").is_dir() or (d / "assets" / "polyhaven").is_dir():
            return d
        if first_game is None and ((d / "package.json").is_file() or (d / "studio.game.json").is_file()):
            first_game = d
    return first_game or start


def downloaded(project: str) -> dict:
    """What this project already has from Poly Haven: its polyhaven.json files. `project` may be
    any folder or file inside the game; the paths are relative to the game folder, `root`."""
    from . import workspace
    try:
        root = workspace._check_browse(project)
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": str(ex)}
    root = _game_root(root.parent if root.is_file() else root)
    rows = []
    for base in (root / "public" / "assets" / "polyhaven", root / "assets" / "polyhaven"):
        if not base.is_dir():
            continue
        for meta in base.glob("*/*/polyhaven.json"):
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                continue
            rel = meta.parent.resolve().relative_to(root.resolve()).as_posix()
            rows.append({"id": m.get("id"), "name": m.get("name"), "type": m.get("type"), "folder": rel,
                         "main": rel + "/" + str(m.get("main") or ""), "resolution": m.get("resolution"),
                         "references": {k: rel + "/" + v for k, v in (m.get("references") or {}).items()},
                         "dimensions_m": m.get("dimensions_m"),
                         "maps": {k: rel + "/" + v for k, v in (m.get("maps") or {}).items()}})
    return {"ok": True, "root": str(root), "rows": sorted(rows, key=lambda r: (r["type"] or "", r["name"] or ""))}
