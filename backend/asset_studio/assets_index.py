"""What a project already HAS — so the Engine window shows a game's dinos and brainrots, not only
what the forge made this week.

Four kinds of thing are found, all read-only and all cheap:

    code    an exported function or class that builds something visual (buildDino, makeCrate,
            class Actors): its name says so, or its body touches meshes, geometry, materials,
            entities or textures.
    spec    one entry of an exported table of specs — SPECIES, ENEMIES, PROPS — where every
            entry has a name or an id and the first carries a visual field (color, scale, model,
            sprite…). This is where a game keeps its fifteen dinos; the builder is one function.
    model   a .glb / .gltf / .fbx / .obj / .stl / .ply file.
    image   a .png / .jpg / .webp / .svg / .gif file, outside screenshot folders.

Every item gets the same `subject` shelf the history uses (engine_tags), guessed from its own
name, its table's name and the folder it sits in. Build outputs, backups, node_modules, review
shots and the like are skipped, so a game with a deploy/ copy is listed once. Cached per project
for a short while; the window polls.
"""
from __future__ import annotations

import json
import os
import re
import struct
import threading
import time
from pathlib import Path
from typing import Optional

from . import engine_tags

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "out", "deploy", ".next", ".vite", ".cache", "coverage",
             "vendor", "third_party", "graphify-out", ".studio-uploads", "__pycache__", ".venv", "venv",
             "review", "reviews", "shots", "screenshots", "playershots", "tests", "test", "__tests__", "tools",
             "research", "reference", "references", "docs", "doc", "kiln", "meshy-out", "backup", "backups",
             "bak", "old", "tmp", "temp", "logs", "log", "insp", "_gate", "motion-src", "dont look here"}
SKIP_PREFIX = ("backup", "review", "shots", "old_", "old-", ".")
SKIP_SUFFIX = ("-legacy", "_legacy", "-old", "_old", "-bak", "_bak", "-backup", "_backup")
# ANYWHERE IN THE NAME, not only at the ends. A real project had `_asset_backup_orig`, which
# begins with an underscore, so every prefix rule missed it — and because os.walk visits folders
# in sorted order, an underscore sorts first, so 442 backed-up sprites ate the whole item budget
# before the walk ever reached the game's own `js/`. The index then reported a game with no code
# in it at all.
SKIP_CONTAINS = ("_backup", "backup_", "_orig", "-orig", "_bak_", "deploy2", "planner-main")
MODEL_EXT = {".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply", ".vox"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
CODE_EXT = {".js", ".mjs", ".ts", ".tsx", ".jsx"}
# Walking a tree is stat calls; reading and parsing is the expensive part. So the walk is allowed
# to see a lot, and the budget that matters is applied afterwards, in an order that cannot lose the
# game while it lists its sprites.
MAX_FILES = 40_000
MAX_DEPTH = 7
MAX_CODE_BYTES = 400_000
MAX_ITEMS = 4000
# HOW MANY PICTURES ONE FOLDER MAY CONTRIBUTE. A sprite dump is not more important than the rest
# of the project put together, and 400 frames of one animation tell a person nothing that 60 do
# not. Everything is still counted; only the listing is capped, and the count says by how much.
MAX_PER_FOLDER = 60
AUDIO_EXT = {".mp3", ".ogg", ".wav", ".m4a"}
# A texture is a picture a material samples; a sprite is a picture drawn as itself. Nothing in a
# file says which, so this reads the folder and the suffix conventions every pipeline uses.
_TEXTURE_HINT = ("texture", "textures", "material", "materials", "tile", "tiles", "pbr", "matcap",
                 "skybox", "cubemap", "hdri", "env")
_TEXTURE_SUFFIX = ("_albedo", "_basecolor", "_base_color", "_normal", "_nrm", "_rough", "_roughness",
                   "_metal", "_metallic", "_ao", "_occlusion", "_height", "_disp", "_emissive",
                   "_spec", "_gloss", "_opacity", "_mask")
_TTL = 20.0

# EXPORTED OR NOT. A game written as classic <script> files — no modules, no exports, functions
# hung off the window — is still a game, and three real ones here are written exactly that way.
# Requiring `export` reported them as projects with no code in them at all. The builder/table
# filters below still decide what COUNTS; this only decides what is looked at.
_FN = re.compile(r"^[ \\t]{0,2}(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.M)
_ARROW = re.compile(r"^[ \\t]{0,2}(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]{0,80})?=\s*(?:async\s*)?\(?[^=\n]{0,120}?\)?\s*=>", re.M)
_CLS = re.compile(r"^[ \\t]{0,2}(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)", re.M)
_TABLE = re.compile(r"^(?:export\s+)?(?:(?:const|let|var)\s+)?"
                    r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)"
                    r"\s*(?::\s*[\w$<>\[\]. ,|]+?)?\s*=\s*\[", re.M)
_VISUAL = re.compile(r"THREE\.|\bnew\s+(?:Mesh|Group|Sprite|Points|Line|InstancedMesh|SkinnedMesh)\s*\(|BufferGeometry|Geometry\s*\("
                     r"|Material\s*\(|pc\.Entity|new\s+Entity\s*\(|addComponent\(\s*['\"](?:render|model|sprite|element|particlesystem)"
                     r"|createMesh|CanvasTexture|Texture\s*\(|StandardMaterial")
_MATERIAL_ONLY = re.compile(r"Material\s*\(")
_MESHY = re.compile(r"\bnew\s+(?:THREE\.)?(?:Mesh|Group|Sprite|InstancedMesh|SkinnedMesh)\s*\(|pc\.Entity|new\s+Entity\s*\(|addComponent\(|createMesh")
_BUILDER_PREFIX = re.compile(r"^(?:build|create|make|spawn|gen|generate|construct|assemble|forge|model|mesh)[A-Z_]")
_BUILDER_SUFFIX = re.compile(r"(?:Mesh|Model|Geometry|Factory|Asset|Character|Creature|Enemy|Monster|Prop|Dino|Brainrot|Actor|Actors|"
                             r"Vehicle|Weapon|Building|Tree|Rock|World|Level|Terrain|Fx|Effects?)$")
_NOT_BUILDER = re.compile(r"^(?:preload|load|dispose|update|tick|animate|render|init|setup|main|start|stop|resize|handle|on[A-Z]|"
                          r"use[A-Z]|get[A-Z]|set[A-Z]|is[A-Z]|has[A-Z]|to[A-Z]|from[A-Z]|parse|format|fmt|save|persist|compute|"
                          r"draw(?:Cards?)$|clamp|lerp|rand|random|noise|ease|mix|apply|register|connect|attach|detach)")
_ENTRY_KEY = re.compile(r"\b(?:id|key|kind|type|slug)\s*:\s*['\"]([^'\"]{1,60})['\"]")
_ENTRY_NAME = re.compile(r"\bname\s*:\s*['\"]([^'\"]{1,80})['\"]")
# A field that says the entry has a SHAPE. `color`, `icon` and `desc` alone do not: an upgrade card
# has those too, and an upgrade card is not an asset.
_ENTRY_VISUAL = re.compile(r"\b(?:body|accent|belly|scale|model|mesh|glb|gltf|sprite|texture|geometry|parts|"
                           r"size|bw|bh|bd|head|legs|arms|radius|height|width|shape|palette|material|mat|handle|trim|sig|"
                           r"skin|fur|eyes|limbs|wings|tail)\s*:")

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _skip_dir(name: str) -> bool:
    low = name.lower()
    if low in SKIP_DIRS or low.startswith(SKIP_PREFIX) or low.endswith(SKIP_SUFFIX):
        return True
    return any(t in low for t in SKIP_CONTAINS)


def _engine_of(text: str) -> str:
    if re.search(r"\bTHREE\b|from\s+['\"]three|import\s*\(\s*['\"]three", text):
        return "three"
    if re.search(r"\bpc\.|from\s+['\"]playcanvas|import\s*\(\s*['\"]playcanvas", text):
        return "playcanvas"
    return ""


def _span_after(text: str, start: int, limit: int = 30_000) -> str:
    """The body that follows an export, up to the next top-level export or the cap."""
    nxt = text.find("\nexport ", start)
    end = nxt if nxt != -1 else len(text)
    return text[start:min(end, start + limit)]


def _array_span(text: str, open_idx: int, limit: int = 250_000) -> str:
    """The text of a `[ … ]` array starting at open_idx, by bracket depth; strings are skipped."""
    depth = 0
    i = open_idx
    n = min(len(text), open_idx + limit)
    quote = ""
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
        i += 1
    return text[open_idx:n]


# `model: U_ballerinacappucina_glb` names a variable, and the variable is declared at the top of
# the file as `const U_ballerinacappucina_glb = new URL('../assets/brainrots/ballerinacappucina.glb',
# import.meta.url).href`. Two regexes join those, and thirty creatures that are GLB files rather
# than code stop being un-previewable: there is nothing to build, only something to show.
_URL_CONST = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*new\s+URL\(\s*['\"]([^'\"]+)['\"]", re.M)
_ASSET_REF = re.compile(r"\b([A-Za-z_$][\w$]*_(?:glb|gltf))\b")
_ASSET_LIT = re.compile(r"['\"]([^'\"]+\.(?:glb|gltf))['\"]")


# WHICH NODES OF THE FILE AN ENTRY IS. One GLB often holds a character and its four repaints, or
# three characters: `nodes: ["Labubu_Default", "Labubu_Golden", …]`, the first being the one the
# game draws, and `parts: [...]` the extras it always shows. Only a list of plain strings counts.
_ENTRY_LIST = re.compile(r"\b(nodes|parts)\s*:\s*\[([^\[\]]*)\]")
_STRING = re.compile(r"""['"]([^'"]{1,120})['"]""")


def _names_of(entry: str) -> dict:
    out: dict = {}
    for key, body in _ENTRY_LIST.findall(entry):
        if key in out:
            continue
        names = _STRING.findall(body)
        rest = _STRING.sub("", body).replace(",", "").strip()
        if names and not rest:
            out[key] = names[:16]
    return out


def _model_of(entry: str, urls: dict, folder: Path, root: Path) -> str:
    """The model file one table entry points at, project-relative, or ''."""
    raw = ""
    m = _ASSET_LIT.search(entry)
    if m:
        raw = m.group(1)
    else:
        for name in _ASSET_REF.findall(entry):
            if name in urls:
                raw = urls[name]
                break
    if not raw:
        return ""
    try:
        # Both sides resolved, for the reason `_deps` gives: a root reached through a short 8.3
        # name is not a prefix of the long path its files resolve to.
        p = (folder / raw).resolve()
        if p.is_file():
            return p.relative_to(root.resolve()).as_posix()
    except Exception:
        pass
    return ""


# WHAT THE FILE ITSELF IMPORTS. A builder is rarely self-contained: `proplib.ts` needs the colour
# script out of `palette.ts` before it can paint a candy tree, and without it every colour comes
# back as whatever the Studio invented. The imports are recorded here so the thing that opens the
# asset can load them too and use the game's own values.
_IMPORT = re.compile(r"""(?:^|
)\s*import\s+(?:type\s+)?(?:[^'"
]*?from\s*)?['"]([^'"
]+)['"]""")


def _deps(text: str, folder: Path, root: Path) -> list[str]:
    """The local files this one imports, project-relative — at most a handful, resolved."""
    out: list[str] = []
    # BOTH SIDES RESOLVED. `../sim/palette.ts` has to be resolved to be found at all, and a
    # resolved path is not relative to an unresolved root: on Windows a workspace reached through
    # a short 8.3 name ("ADMINI~1") comes back long, and every dependency was silently dropped.
    try:
        root = root.resolve()
    except OSError:
        pass
    for spec in _IMPORT.findall(text[:MAX_CODE_BYTES]):
        if not spec.startswith("."):
            continue
        base = folder / spec
        for cand in (base, Path(str(base) + ".ts"), Path(str(base) + ".js"), Path(str(base) + ".tsx"),
                     Path(str(base) + ".mjs"), base / "index.ts", base / "index.js"):
            try:
                if cand.is_file():
                    rel = cand.resolve().relative_to(root).as_posix()
                    if rel not in out:
                        out.append(rel)
                    break
            except (OSError, ValueError):
                continue
        if len(out) >= 8:
            break
    return out


def _entries(arr: str) -> list[str]:
    """The top-level `{ … }` objects of an array literal, as text."""
    out: list[str] = []
    depth = 0
    start = -1
    quote = ""
    i = 0
    n = len(arr)
    while i < n:
        c = arr[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(arr[start:i + 1])
                start = -1
                if len(out) >= 200:
                    break
        i += 1
    return out


def _subject(name: str, *context: str) -> str:
    """The shelf: the item's own name first, then what surrounds it (its table, file, folder) at a
    third of the weight — so a "creatures" table shelves "Blorbo Fantanto", but "Stone Hammer" is
    a weapon whatever folder it is in."""
    subject, _score = engine_tags.guess_subject(name, context=[c for c in context if c])
    return subject


def _owner(root: Path, p: Path) -> str:
    """Which GAME inside this workspace owns this file, as a project-relative folder.

    A research workspace holds three games — `rot-haul`, `rot-rush`, `rot-haul-LEGACY` — each with
    its own package.json and its own dev server. A file belonging to one of them cannot be served
    by another's server, and the whole workspace has only ever had one origin, so thirty creatures
    from the second game failed to import with a path that looked perfectly reasonable. The nearest
    ancestor holding a package.json is the answer, and "" means the workspace root itself.
    """
    try:
        cur = p.parent
        while True:
            if (cur / "package.json").is_file():
                rel = cur.relative_to(root).as_posix()
                return "" if rel == "." else rel
            if cur == root or root not in cur.parents:
                return ""
            cur = cur.parent
    except Exception:
        return ""


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _scan_code(root: Path, p: Path, text: str, items: list[dict]) -> None:
    rel = _rel(root, p)
    owner = _owner(root, p)
    folder = " ".join(Path(rel).parts[:-1])
    stem = p.stem
    eng = _engine_of(text)
    st = p.stat()
    seen: set[str] = set()

    def line_of(idx: int) -> int:
        return text.count("\n", 0, idx) + 1

    # ---- spec tables first, so a builder in the same file is known to have things to build
    urls = {name: path for name, path in _URL_CONST.findall(text)}
    deps = _deps(text, p.parent, root)
    for m in _TABLE.finditer(text):
        tname = m.group(1)
        arr = _array_span(text, m.end() - 1)
        ents = _entries(arr)
        if len(ents) < 2:
            continue
        if not _ENTRY_VISUAL.search(ents[0]):
            continue
        keyed = 0
        for k, e in enumerate(ents):
            km = _ENTRY_KEY.search(e)
            nm = _ENTRY_NAME.search(e)
            if not km and not nm:
                continue
            keyed += 1
            key = km.group(1) if km else ""
            name = nm.group(1) if nm else key
            row = {
                "id": "spec:%s#%s[%s]" % (rel, tname, key or k),
                "type": "spec", "name": name, "file": rel, "root": owner,
                "model": _model_of(e, urls, p.parent, root), "path": str(p), "line": line_of(m.start()) + arr.count("\n", 0, arr.find(e)),
                "export": tname, "table": tname, "key": key, "index": k, "engine": eng, "deps": deps,
                "subject": _subject(name or key, tname, stem, folder), "tags": [tname.lower()],
                "size": st.st_size, "mtime": st.st_mtime,
            }
            if row["model"]:
                row.update(_names_of(e))
            items.append(row)
            if len(items) >= MAX_ITEMS:
                return
        if keyed < 2:
            # nothing was really named; drop what was added for this table
            items[:] = [it for it in items if not (it["type"] == "spec" and it["file"] == rel and it["table"] == tname)]

    # ---- builders: functions, arrows and classes that make something visual
    for rx, kind in ((_FN, "function"), (_ARROW, "function"), (_CLS, "class")):
        for m in rx.finditer(text):
            name = m.group(1)
            if name in seen or _NOT_BUILDER.match(name):
                continue
            body = _span_after(text, m.end())
            named = bool(_BUILDER_PREFIX.match(name) or _BUILDER_SUFFIX.search(name))
            exported = text[max(0, m.start() - 24):m.start() + 7].find("export") >= 0
            if not exported and not named:
                continue
            # A class that wraps a builder from the same file ("class Dino { … buildDino(spec) … }")
            # is an asset too, even when the class body itself never touches a mesh.
            visual = bool(_VISUAL.search(body)) or (kind == "class" and any(b in body for b in seen if b != name))
            if not named and not visual:
                continue
            if kind == "function" and not named and not _MESHY.search(body):
                # touches a material or a texture but never makes a thing: a helper, not an asset
                if _MATERIAL_ONLY.search(body):
                    subj = "material"
                else:
                    continue
            else:
                subj = _subject(name, stem, folder)
                if subj == "other" and _MATERIAL_ONLY.search(body) and not _MESHY.search(body):
                    subj = "material"
            seen.add(name)
            items.append({
                "id": "code:%s#%s" % (rel, name),
                "type": "code", "name": name, "file": rel, "root": owner, "path": str(p), "line": line_of(m.start()),
                "export": name, "table": "", "key": "", "index": -1, "engine": eng, "deps": deps,
                "subject": subj, "tags": [kind], "size": st.st_size, "mtime": st.st_mtime,
            })
            if len(items) >= MAX_ITEMS:
                return


# WHAT A MODEL FILE HOLDS. A game often ships a clip beside its model — `Labubu_idle.glb` is eleven
# bones and one animation, with no mesh at all — and a shelf that offered it as a model placed an
# empty group, which the note then called "placed". The glTF JSON says it for the cost of one small
# read, so a model row carries its mesh and clip counts, and a file with no mesh is tagged
# "animation" (or "empty"). Remembered per file by size and time, so a rescan reads nothing again.
MAX_GLTF_JSON = 16_000_000
_model_meta_seen: dict[str, tuple[int, float, dict]] = {}


def _model_meta(p: Path, st: os.stat_result) -> dict:
    ext = p.suffix.lower()
    if ext not in (".glb", ".gltf"):
        return {}
    key = str(p)
    hit = _model_meta_seen.get(key)
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime:
        return hit[2]
    meta: dict = {}
    try:
        doc = None
        with open(p, "rb") as f:
            if ext == ".glb":
                head = f.read(20)
                if len(head) == 20 and head[:4] == b"glTF":
                    n, kind = struct.unpack_from("<II", head, 12)
                    if kind == 0x4E4F534A and 0 < n <= MAX_GLTF_JSON:
                        doc = json.loads(f.read(n))
            elif st.st_size <= MAX_GLTF_JSON:
                doc = json.loads(f.read())
        if isinstance(doc, dict):
            meta = {"meshes": len(doc.get("meshes") or []), "anims": len(doc.get("animations") or [])}
    except (OSError, ValueError, struct.error):
        meta = {}
    _model_meta_seen[key] = (st.st_size, st.st_mtime, meta)
    return meta


def _media_type(rel: str, stem: str, ext: str) -> str:
    if ext in MODEL_EXT:
        return "model"
    if ext in AUDIO_EXT:
        return "audio"
    low = (rel + " " + stem).lower().replace("\\", "/")
    parts = set(low.split("/")[:-1] and low.split("/") or [])
    if any(h in parts for h in _TEXTURE_HINT) or any(("/" + h + "/") in ("/" + low) for h in _TEXTURE_HINT):
        return "texture"
    if stem.lower().endswith(_TEXTURE_SUFFIX):
        return "texture"
    return "image"


def scan(project: str) -> dict:
    """Everything a game has, in an order that cannot lose the game.

    The walk collects; the budget is spent afterwards. Code and models are listed in full because
    a project has tens of them and each one is a thing you can place. Pictures are listed per
    folder, because a project has thousands and the four-hundredth frame of one animation is not
    what anyone came to find. Nothing is hidden: `counts` is the truth about how many there are,
    and `capped` names the folders that were trimmed.
    """
    root = Path(project)
    items: list[dict] = []
    files_seen = 0
    truncated = False
    if not root.is_dir():
        return {"ok": False, "error": "no such folder", "items": [], "files_seen": 0}
    code_files: list[Path] = []
    media: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d)
                             # The Studio's own new-game templates: two builders each, which are not
                             # this project's assets. By path, because a game's "templates" can be real.
                             and not (d.lower() == "new_game" and Path(dirpath).name.lower() == "templates")
                             ) if rel_depth < MAX_DEPTH else []
        for fn in sorted(filenames):
            files_seen += 1
            if files_seen > MAX_FILES:
                truncated = True
                break
            ext = Path(fn).suffix.lower()
            low = fn.lower()
            if ext in CODE_EXT:
                if ".min." in low or low.endswith((".d.ts", ".test.ts", ".test.js", ".spec.ts",
                                                   ".spec.js", ".config.js", ".config.ts")):
                    continue
                code_files.append(Path(dirpath) / fn)
            elif ext in MODEL_EXT or ext in IMAGE_EXT or ext in AUDIO_EXT:
                media.append(Path(dirpath) / fn)
        if truncated:
            break

    # 1. THE CODE, ALWAYS AND FIRST. It is what makes the game, and it is what can be placed more
    #    of; a project that lists sprites and no builders is unusable as a palette.
    for p in code_files:
        try:
            if p.stat().st_size > MAX_CODE_BYTES:
                continue
            _scan_code(root, p, p.read_text(encoding="utf-8", errors="replace"), items)
        except OSError:
            continue

    # 2. THE MEDIA, models before pictures, and no folder allowed to crowd out the others.
    counts: dict = {}
    capped: dict = {}
    per_folder: dict = {}
    order = {"model": 0, "audio": 1, "texture": 2, "image": 3}
    rows = []
    for p in media:
        rel = _rel(root, p)
        kind = _media_type(rel, p.stem, p.suffix.lower())
        counts[kind] = counts.get(kind, 0) + 1
        rows.append((order.get(kind, 9), kind, rel, p))
    for _, kind, rel, p in sorted(rows, key=lambda r: (r[0], r[2])):
        folder = str(Path(rel).parent)
        if kind in ("image", "texture"):
            n = per_folder.get(folder, 0)
            if n >= MAX_PER_FOLDER:
                capped[folder] = capped.get(folder, 0) + 1
                continue
            per_folder[folder] = n + 1
        if len(items) >= MAX_ITEMS:
            truncated = True
            break
        try:
            st = p.stat()
        except OSError:
            continue
        row = {
            "id": kind + ":" + rel,
            "type": kind,
            "name": p.stem, "file": rel, "root": _owner(root, p), "path": str(p), "line": 0,
            "export": "", "table": "", "key": "",
            "index": -1, "engine": "", "subject": _subject(p.stem, " ".join(Path(rel).parts[:-1])),
            "tags": [p.suffix.lower().lstrip(".")],
            "size": st.st_size, "mtime": st.st_mtime,
        }
        if kind == "model":
            meta = _model_meta(p, st)
            if meta:
                row.update(meta)
                if not meta["meshes"]:
                    row["tags"].append("animation" if meta["anims"] else "empty")
        items.append(row)
    for it in items:
        if it["type"] in ("code", "spec"):
            counts[it["type"]] = counts.get(it["type"], 0) + 1
    return {"ok": True, "project": str(root), "items": items, "files_seen": files_seen,
            "truncated": truncated, "totals": counts,
            "capped": {k: v for k, v in sorted(capped.items(), key=lambda kv: -kv[1])[:8]},
            "scanned": time.time()}


def _cached(project: str, fresh: bool = False) -> dict:
    key = str(Path(project)).lower()
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and now - hit[0] < _TTL:
            return hit[1]
    res = scan(project)
    with _lock:
        _cache[key] = (now, res)
    return res


def rescan(project: str) -> dict:
    return _cached(project, fresh=True)


def list_assets(project: str, type: str = "", subject: str = "", q: str = "", fresh: bool = False,
                root: str = "") -> dict:
    """The project's assets on shelves, with counts per type and subject over the whole project.

    `root` narrows it to ONE game inside the workspace. A research folder here holds rot-haul and
    rot-rush; four hundred assets from two different games on one shelf is a list nobody can read,
    and half of them cannot even be opened from the other one's server.
    """
    res = _cached(project, fresh)
    if not res.get("ok"):
        return res
    rows = list(res["items"])
    games = sorted({str(r.get("root") or "") for r in rows})
    want_root = str(root or "").strip()
    if want_root:
        rows = [r for r in rows if str(r.get("root") or "") == want_root]
    needle = str(q or "").strip().lower()
    if needle:
        parts = needle.split()

        def hit(r: dict) -> bool:
            hay = " ".join([r["name"], r["file"], r.get("table", ""), r.get("key", ""), r["subject"], r["type"],
                            " ".join(r.get("tags") or [])]).lower()
            return all(pt in hay for pt in parts)
        rows = [r for r in rows if hit(r)]
    by_type: dict[str, int] = {}
    by_subject: dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        by_subject[r["subject"]] = by_subject.get(r["subject"], 0) + 1
    if type and type != "all":
        rows = [r for r in rows if r["type"] == type]
    if subject and subject != "all":
        rows = [r for r in rows if r["subject"] == subject]
    # Specs and code first (they are what a person is usually after), then models, then images;
    # within a kind, by folder and name — and a model file with no mesh in it after the ones that
    # have one, because it places nothing you can see.
    order = {"spec": 0, "code": 1, "model": 2, "texture": 3, "audio": 4, "image": 5}
    rows.sort(key=lambda r: (order.get(r["type"], 9), r.get("meshes") == 0, r["file"].lower(),
                             r.get("index", 0), r["name"].lower()))
    return {"ok": True, "project": res["project"], "total": len(rows), "items": rows[:MAX_ITEMS],
            "by_type": by_type, "by_subject": by_subject, "files_seen": res["files_seen"],
            # Which games this workspace holds, so the window can offer the switch at all.
            "games": games,
            "truncated": res["truncated"], "scanned": res["scanned"],
            # What is really on disk, against what is listed. A folder of 800 sprites is listed 60
            # at a time; saying so is the difference between a cap and a lie.
            "totals": res.get("totals") or {}, "capped": res.get("capped") or {}}
