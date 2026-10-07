# -*- coding: utf-8 -*-
"""A GLB anything can read.

A game's models are usually compressed — Draco for geometry, meshopt for buffers, KTX2 for
textures — and every one of those needs a decoder that the ENGINE has to be told about. A game
tells its own engine at startup. The Studio's viewport, the forge and `<model-viewer>` are not
that game's startup, so a compressed model reaches them as a WebAssembly error about a magic word,
which is a true statement about bytes and no help at all.

Decompressing is not the game's job or the viewer's job. It is a property of the file, so it is
done once, here, with the toolchain that already ships on this machine:

    gltf-transform copy in.glb out.glb

Measured on a real brainrot: 117 KB Draco in, 968 KB plain out, 0.4 seconds. Eight times the
bytes, over localhost, in exchange for a model that every renderer on the machine can open with
no decoder at all. Cached on the source file's mtime, so it is paid once per model.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

CACHE = DATA_DIR / "glb"
# The extensions that need a decoder the viewer does not have. KTX2 is deliberately not here:
# a compressed TEXTURE still draws, it simply falls back, while compressed GEOMETRY draws nothing.
NEEDS_DECODE = ("KHR_draco_mesh_compression", "EXT_meshopt_compression")
_TIMEOUT = 60


def extensions(path: Path) -> list:
    """The extensions a .glb says it uses, read from its JSON chunk alone.

    Twelve bytes of header and one chunk, so this is a stat and a small read rather than a parse
    of a model that may be a hundred megabytes."""
    try:
        with path.open("rb") as fh:
            head = fh.read(20)
            if len(head) < 20 or head[:4] != b"glTF":
                return []
            (chunk_len,) = struct.unpack("<I", head[12:16])
            if head[16:20] != b"JSON":
                return []
            body = fh.read(min(chunk_len, 4_000_000))
        doc = json.loads(body.decode("utf-8", "replace"))
        used = doc.get("extensionsUsed") or []
        return [str(x) for x in used if isinstance(x, str)]
    except Exception:
        return []


def needs_decode(path: Path) -> bool:
    return any(x in NEEDS_DECODE for x in extensions(path))


def _cache_file(src: Path) -> Path:
    try:
        stamp = src.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = "%s|%d" % (str(src.resolve()).lower(), stamp)
    return CACHE / (hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:20] + ".glb")


def plain(path: str) -> dict:
    """The same model with nothing left that needs a decoder.

    Returns the original when it never needed decoding — no copy, no cache, no cost.
    """
    src = Path(path)
    if not src.is_file() or src.suffix.lower() not in (".glb", ".gltf"):
        return {"ok": False, "error": "not a model file"}
    used = extensions(src)
    if not any(x in NEEDS_DECODE for x in used):
        return {"ok": True, "path": str(src), "decoded": False, "extensions": used}
    out = _cache_file(src)
    if out.is_file():
        return {"ok": True, "path": str(out), "decoded": True, "cached": True, "extensions": used}
    exe = shutil.which("gltf-transform")
    if not exe:
        return {"ok": False, "error": "this model is compressed and gltf-transform is not "
                                      "installed, so it cannot be decoded here",
                "extensions": used, "path": str(src)}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.glb")
    try:
        r = subprocess.run([exe, "copy", str(src), str(tmp)], capture_output=True, text=True,
                           timeout=_TIMEOUT, shell=False)
    except Exception as ex:
        return {"ok": False, "error": "could not decode: %s" % str(ex)[:200], "path": str(src)}
    if r.returncode != 0 or not tmp.is_file():
        return {"ok": False, "error": ("gltf-transform failed: " + (r.stderr or r.stdout or ""))[:400],
                "path": str(src)}
    try:
        tmp.replace(out)
    except OSError:
        shutil.copy2(tmp, out)
    return {"ok": True, "path": str(out), "decoded": True, "cached": False, "extensions": used}


def resolve(project: str, file: str) -> Optional[Path]:
    """A project-relative model path, made absolute and kept inside the project."""
    try:
        root = Path(project).resolve()
        p = (root / str(file).replace("\\", "/").lstrip("/")).resolve()
        if p.is_file() and (root == p or root in p.parents):
            return p
    except Exception:
        pass
    return None
