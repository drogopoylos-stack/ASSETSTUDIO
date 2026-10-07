"""What the Studio's engine window shows: the games, the generations, and what is happening now.

The forge already renders an asset and writes a contact sheet. A sheet is a photograph, though —
it cannot be orbited, and it cannot be compared with the version before it except by eye. So every
forge run now records the CODE beside the picture, and that one addition is what turns a folder of
PNGs into an engine: the window can re-run any past generation in its own renderer and let you
turn it around.

Three questions this module answers, and each is one HTTP call so the window can poll cheaply:

  * which projects are games, what engine each one runs, and where it is served from;
  * what has been made, newest first, grouped by project, by day and by kind;
  * whether an agent is building something right now.

Nothing here starts a browser or a dev server on its own. The window is a viewer; the agents are
what make things happen, and a viewer that quietly launches Chrome because someone opened a panel
would be a bad citizen on a machine that is already running a game, a build and a model.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from . import perf
from .config import DATA_DIR, settings

_LIVE_DIR = DATA_DIR / "live"
_REVIEW_DIR = DATA_DIR / "review"

# A generation id is "<project-slug>/<file-stem>" and is used to build a path, so it is validated
# rather than trusted: anything outside this shape never reaches the filesystem.
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}/[A-Za-z0-9._-]{1,80}$")

_BUSY_WINDOW = 300.0          # an agent that forged in the last five minutes is "working"
_PROJECT_TTL = 20.0           # the window polls; the project list must not restat the disk each time
_proj_cache: tuple[float, list] = (0.0, [])
# An engine is installed once and then sits there. Re-probing node_modules on every poll cost
# 68 ms across 35 projects for an answer that had not changed since the last npm install.
_ENGINE_TTL = 300.0
_engine_cache: dict = {}


def enabled() -> bool:
    """The engine rides the live link. One switch, so there is one thing to turn off."""
    return bool(settings.get("cc_live", True))


# ---------------------------------------------------------------------------
# Recording a generation
# ---------------------------------------------------------------------------
def record(project: str, label: str, code: str, engine: str, stats: dict, views: list,
           sheet: str, ok: bool, error: str = "", engine_url: str = "",
           tags: Optional[list] = None, category: str = "",
           extra: Optional[dict] = None) -> str:
    """Write the generation beside its sheet. Returns the id, or "" if it could not be written.

    A run with NO SHEET is recorded too, and that is the whole reason a person can watch an agent
    work. The cheap half of the loop draws no picture on purpose — measure, move a part, measure
    again — and while those runs were skipped here, the Engine window showed nothing for minutes
    at a time and an agent looked idle while it was busy. The code is what the window replays, and
    a sheetless run has the code.

    Deliberately best-effort: a forge that renders correctly must never fail because the history
    could not be written. The picture is the deliverable; this is the archive.
    """
    try:
        if sheet:
            p = Path(sheet)
            if not p.exists():
                return ""
            gid, out, sheet_path = "%s/%s" % (p.parent.name, p.stem), p.with_suffix(".json"), str(p)
        else:
            from .live import _slug
            folder = _LIVE_DIR / _slug(project)
            folder.mkdir(parents=True, exist_ok=True)
            stem = "forge-%d" % int(time.time() * 1000)
            gid, out, sheet_path = "%s/%s" % (folder.name, stem), folder / (stem + ".json"), ""
        rec = {
            "id": gid,
            "kind": "forge",
            "ts": time.time(),
            "project": str(Path(project)),
            "project_name": Path(project).name,
            "label": label or "",
            "engine": engine or "",
            "engine_url": engine_url or "",
            "views": list(views or []),
            "stats": stats or {},
            "sheet": sheet_path,
            "ok": bool(ok),
            "error": (error or "")[:2000],
            "code": code or "",
        }
        # What the agent asked for, kept beside what it got: the angle it was standing at and the
        # parts it moved. That is what lets the window put a watcher where the agent was.
        if isinstance(extra, dict):
            rec.update({k: v for k, v in extra.items() if k not in ("id", "kind", "code")})
        # The shelves: what it is and what it depicts, from the agent's tags first, else a guess
        # from the label, the code's own names and the material names.
        try:
            from . import engine_tags as _tags
            rec.update(_tags.classify("forge", label or "", code or "", (stats or {}).get("material_names"),
                                      tags, category))
        except Exception:
            pass
        out.write_text(json.dumps(rec), encoding="utf-8")
        return rec["id"]
    except Exception:
        return ""


def _read(path: Path) -> Optional[dict]:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def generation(gid: str) -> dict:
    """One generation in full, including the code, so the window can re-run it."""
    if not _ID_RE.match(str(gid or "")):
        return {"ok": False, "error": "not a generation id"}
    slug, stem = str(gid).split("/", 1)
    path = _LIVE_DIR / slug / (stem + ".json")
    try:
        # The id is validated above, but resolve and re-check anyway: one guard that can be
        # reasoned about beats two that each assume the other did the work.
        if not path.resolve().is_relative_to(_LIVE_DIR.resolve()):
            return {"ok": False, "error": "outside the data directory"}
    except Exception:
        return {"ok": False, "error": "bad path"}
    rec = _read(path)
    if not rec:
        return {"ok": False, "error": "no such generation"}
    return {"ok": True, **rec}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def _row(rec: dict) -> dict:
    st = rec.get("stats") or {}
    return {
        "id": rec.get("id", ""), "kind": "forge", "ts": rec.get("ts", 0),
        "project": rec.get("project", ""), "project_name": rec.get("project_name", ""),
        "label": rec.get("label", ""), "engine": rec.get("engine", ""),
        "sheet": rec.get("sheet", ""), "ok": rec.get("ok", True),
        "views": rec.get("views", []),
        "triangles": st.get("triangles"), "materials": st.get("materials"),
        "meshes": st.get("meshes"), "flat": st.get("flat", False),
        "framed": st.get("framed", ""), "replayable": bool(rec.get("code")),
        "type": rec.get("type") or ("asset" if rec.get("code") else "picture"),
        "subject": rec.get("subject") or "other", "subject_by": rec.get("subject_by") or "auto",
        "tags": list(rec.get("tags") or []),
    }


def _forge_rows() -> list[dict]:
    """Every forge record, its shelves filled in — and saved once, so a guess is not remade forever."""
    from . import engine_tags as _tags
    rows: list[dict] = []
    for jf in _LIVE_DIR.glob("*/forge-*.json"):
        rec = _read(jf)
        if not rec:
            continue
        try:
            if _tags.apply(rec):
                # Keep the file's own time: "latest" is judged by mtime, and a shelf label filled in
                # today must not make a record from last month look like this morning's.
                import os
                st = jf.stat()
                jf.write_text(json.dumps(rec), encoding="utf-8")
                os.utime(jf, (st.st_atime, st.st_mtime))
        except Exception:
            pass
        rows.append(_row(rec))
    return rows


def _review_rows() -> list[dict]:
    """Review sheets have no record of their own; the file name is all there is to shelve them by."""
    from . import engine_tags as _tags
    rows: list[dict] = []
    for png in _REVIEW_DIR.glob("*/*.png"):
        try:
            st = png.stat()
        except OSError:
            continue
        subject, _score = _tags.guess_subject(png.stem)
        rows.append({"id": "", "kind": "review", "ts": st.st_mtime,
                     "project": "", "project_name": png.parent.name,
                     "label": png.stem, "engine": "", "sheet": str(png),
                     "ok": True, "views": [], "replayable": False,
                     "type": "sheet", "subject": subject, "subject_by": "auto", "tags": []})
    return rows


def history(project: str = "", limit: int = 80, kind: str = "", type: str = "", subject: str = "",
            q: str = "") -> dict:
    """Everything that has been made, newest first, with the shelves the window sorts it onto.

    Four facets: project, type (asset / scene / picture / sheet), subject (what it depicts) and a
    free-text search. Counts per project are over everything; counts per type and per subject are
    over the rows that pass the project and search filters, so a chip says how many it would show.

    Review sheets have no project path, only the folder name they were rendered under. They belong
    to a project's history all the same, so a project filter keeps the sheets whose folder is that
    project's name — and drops the rest, which used to be every sheet on the machine.
    """
    want = str(project or "").strip().lower()
    try:
        forge = _forge_rows()
    except Exception:
        forge = []
    try:
        sheets = _review_rows() if kind in ("", "all", "review") else []
    except Exception:
        sheets = []
    by_project: dict[str, int] = {}
    for r in forge + sheets:
        name = r.get("project_name") or "—"
        by_project[name] = by_project.get(name, 0) + 1
    rows = forge
    if want:
        rows = [r for r in rows if str(r.get("project", "")).lower() == want]
        ps = _slug(Path(want).name)
        rows += [r for r in sheets if ps and _slug(r["project_name"]) == ps]
    else:
        rows = rows + sheets
    if kind and kind not in ("", "all"):
        rows = [r for r in rows if r["kind"] == kind]
    needle = str(q or "").strip().lower()
    if needle:
        parts = needle.split()

        def hit(r: dict) -> bool:
            hay = " ".join([str(r.get("label", "")), str(r.get("project_name", "")), str(r.get("id", "")),
                            str(r.get("subject", "")), str(r.get("type", "")), " ".join(r.get("tags") or []),
                            str(r.get("engine", ""))]).lower()
            return all(part in hay for part in parts)
        rows = [r for r in rows if hit(r)]
    by_type: dict[str, int] = {}
    by_subject: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        by_subject[r["subject"]] = by_subject.get(r["subject"], 0) + 1
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    if type and type != "all":
        rows = [r for r in rows if r["type"] == type]
    if subject and subject != "all":
        rows = [r for r in rows if r["subject"] == subject]
    rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
    by_day: dict[str, int] = {}
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(r.get("ts", 0)))
        by_day[day] = by_day.get(day, 0) + 1
    return {"ok": True, "total": len(rows), "items": rows[:max(1, min(500, int(limit or 80)))],
            "by_project": by_project, "by_day": by_day, "by_kind": by_kind,
            "by_type": by_type, "by_subject": by_subject}


def retag(gid: str, subject: str = "", tags: Optional[list] = None) -> dict:
    """A person's word on what a generation depicts. It sticks: no later guess overwrites it."""
    from . import engine_tags as _tags
    if not _ID_RE.match(str(gid or "")):
        return {"ok": False, "error": "not a generation id"}
    slug, stem = str(gid).split("/", 1)
    path = _LIVE_DIR / slug / (stem + ".json")
    rec = _read(path) if path.is_file() else None
    if not rec:
        return {"ok": False, "error": "no such generation"}
    if subject:
        if subject not in _tags.SUBJECTS:
            return {"ok": False, "error": "unknown subject; one of " + ", ".join(_tags.SUBJECTS)}
        rec["subject"], rec["subject_by"] = subject, "user"
    if tags is not None:
        rec["tags"] = _tags.normalise_tags(tags)
    _tags.apply(rec)
    try:
        path.write_text(json.dumps(rec), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"could not write: {e}"}
    return {"ok": True, **_row(rec)}


# ---------------------------------------------------------------------------
# The games
# ---------------------------------------------------------------------------
def projects(fresh: bool = False) -> dict:
    """The pinned workspaces that are actually games, with what is known about each.

    Cached for a few seconds because the window polls it and each entry costs a directory probe
    and a look at the listening ports.
    """
    global _proj_cache
    now = time.time()
    if not fresh and now - _proj_cache[0] < _PROJECT_TTL:
        return {"ok": True, "projects": _proj_cache[1], "cached": True}
    out: list[dict] = []
    try:
        from . import workspace, dev_server, live
        from .cc_session import _has_renderable
        tabs = {}
        try:
            for t in (live.status().get("tabs") or []):
                tabs[str(Path(t.get("project", "")).resolve()).lower()] = t
        except Exception:
            pass
        for root in workspace.roots():
            path = str(root.get("path") or "")
            if not path:
                continue
            p = Path(path)
            try:
                playable = _has_renderable(path)
            except Exception:
                playable = False
            if not playable:
                continue
            key = ""
            try:
                key = str(p.resolve()).lower()
            except Exception:
                key = path.lower()
            tab = tabs.get(key) or {}
            found = _engine_in(p)
            game_root = Path(found["root"])
            script = ""
            try:
                script = dev_server.script_for(game_root) or ""
            except Exception:
                pass
            out.append({
                "path": path,
                "root": found["root"],          # where the game actually lives, often a subfolder
                "sub": found["sub"],
                "name": root.get("name") or p.name,
                "engine": tab.get("engine", "") or found["engine"],
                "module": found["engine"],      # what is installed, whatever a live tab reports
                "forgeable": found["engine"] in _FORGEABLE,
                "game": bool(found["engine"]) or bool(script),
                "open": bool(tab),
                "dev_script": script,
            })
    except Exception as e:                               # pragma: no cover - defensive
        return {"ok": False, "error": str(e), "projects": []}
    # Games first, then the ones already open, then by name — the window shows this list as-is.
    out.sort(key=lambda r: (not r["game"], not r["open"], r["name"].lower()))
    _proj_cache = (now, out)
    return {"ok": True, "projects": out, "games": sum(1 for r in out if r["game"]),
            "cached": False}


_GAMES_TTL = 8.0
_games_cache: dict = {}
# How deep inside a workspace a game may sit. `brainrot 3d game research crazygames/rot-rush` is
# one level; nobody nests a second game two levels down without also giving it its own workspace.
_GAME_DEPTH = 2


def _is_game_folder(d: Path) -> tuple[bool, str, str]:
    """Is this folder a game in its own right? Returns (yes, engine, dev script)."""
    from . import dev_server
    script = ""
    try:
        script = dev_server.script_for(d) or ""
    except Exception:
        script = ""
    engine = ""
    try:
        for kind, (pkg, rel) in _MODULES.items():
            if (d / "node_modules" / pkg / rel).exists():
                engine = kind
                break
        if not engine:
            got = _loose_build(d, list(_MODULES))
            engine = got[1] if got else ""
    except Exception:
        pass
    if engine or script:
        return True, engine, script
    # A page with no package and no engine folder is still a game you can open and edit.
    try:
        if (d / "index.html").is_file():
            return True, engine, script
    except OSError:
        pass
    return False, engine, script


def games(path: str, fresh: bool = False) -> dict:
    """EVERY game in one workspace, each with its own server — not the first one found.

    A research workspace here holds three: rot-haul on 5178, rot-rush on 5179, rot-arena on 5181.
    They share a parent folder and nothing else — different engines, different ports, different
    asset trees. "The project's dev server" is therefore a question with three answers, and
    answering it with the first one served every one of rot-rush's four hundred files from
    rot-haul, where none of them exist. Anything that opens an asset has to ask which game owns
    it first, so this is the list that answer comes from.
    """
    root = Path(path)
    key = str(root).lower()
    now = time.time()
    hit = _games_cache.get(key)
    if not fresh and hit and now - hit[0] < _GAMES_TTL:
        return {"ok": True, "games": hit[1], "cached": True}
    if not root.exists():
        return {"ok": False, "error": "no such folder: %s" % path, "games": []}
    try:
        from . import assets_index, dev_server
    except Exception:                                    # pragma: no cover - defensive
        return {"ok": False, "error": "the asset index is unavailable", "games": []}

    found: list[dict] = []
    seen: set[str] = set()

    def consider(d: Path, depth: int) -> None:
        if len(found) >= 12:
            return
        try:
            k = str(d.resolve()).lower()
        except OSError:
            return
        if k in seen:
            return
        seen.add(k)
        ok, engine, script = _is_game_folder(d)
        if ok:
            try:
                sub = "" if d == root else d.relative_to(root).as_posix()
            except ValueError:
                sub = ""
            found.append({
                "root": str(d), "sub": sub, "name": sub or d.name,
                "engine": engine, "dev_script": script, "dev_url": "", "servers": [],
            })
        if depth >= _GAME_DEPTH:
            return
        try:
            kids = sorted([c for c in d.iterdir() if c.is_dir()], key=lambda c: c.name.lower())
        except OSError:
            return
        for c in kids[:_SUB_SCAN]:
            if assets_index._skip_dir(c.name):
                continue
            consider(c, depth + 1)

    consider(root, 0)
    # The port probe costs about 50 ms each, so it is done once per game and cached with the list.
    for g in found:
        try:
            servers = dev_server.running_servers(Path(g["root"]))
        except Exception:
            servers = []
        g["servers"] = servers
        g["dev_url"] = servers[0]["url"] if servers else ""
    # A game that is actually being served first, then the ones with a script, then by name. The
    # window shows this list as-is and picks the first.
    found.sort(key=lambda g: (not g["dev_url"], not g["dev_script"], g["name"].lower()))
    _games_cache[key] = (now, found)
    return {"ok": True, "games": found, "cached": False}


def game_for(path: str, sub: str) -> dict:
    """The game inside `path` that owns a file under `sub` — the asset index's `root` field."""
    want = str(sub or "").replace("\\", "/").strip("/").lower()
    gs = (games(path).get("games") or [])
    best = None
    for g in gs:
        gsub = str(g.get("sub") or "").lower()
        if not gsub:
            continue
        if want == gsub or want.startswith(gsub + "/"):
            if best is None or len(gsub) > len(str(best.get("sub") or "")):
                best = g
    if best:
        return best
    for g in gs:
        if not g.get("sub"):
            return g
    return gs[0] if gs else {}


def project_detail(path: str) -> dict:
    """One project, in full — including where it is served from.

    The dev-server lookup lives here and not in the list because it costs about 50 ms: across 35
    projects that was 1.7 seconds of a list the window polls, for a figure only the project you
    clicked on needs.
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "no such folder: %s" % path}
    found = _engine_in(p)
    root = Path(found["root"])
    out = {"ok": True, "path": str(p), "name": p.name, **found}
    try:
        from . import dev_server
        out["dev_url"] = dev_server.running_url(root) or dev_server.running_url(p) or ""
        out["dev_script"] = dev_server.script_for(root) or ""
    except Exception:
        out["dev_url"], out["dev_script"] = "", ""
    try:
        from . import live
        key = str(p.resolve()).lower()
        for t in (live.status().get("tabs") or []):
            if str(Path(t.get("project", "")).resolve()).lower() == key:
                out["tab"] = t
                break
    except Exception:
        pass
    out["forgeable"] = found["engine"] in _FORGEABLE
    return out


_MODULES = {
    "three": ("three", "build/three.module.js"),
    "playcanvas": ("playcanvas", "build/playcanvas.mjs"),
    "babylon": ("@babylonjs/core", "index.js"),
    "phaser": ("phaser", "dist/phaser.esm.js"),
    "pixi": ("pixi.js", "dist/pixi.min.mjs"),
}
# AN ENGINE DOES NOT HAVE TO LIVE IN node_modules.
#
# The table above is the npm layout, and it is what a Vite game has. A hand-written page, a
# vendored build or a scratch harness keeps the engine file beside the HTML instead — the A/B
# harness is exactly that: `three.module.js` next to `index.html`, no package.json anywhere. For
# those the node_modules look finds nothing, and the window said "no three.js build found for this
# project", which was true of the folder it looked in and false of the project.
#
# Filenames only, in a few conventional folders. Not a disk walk: an engine build that is not at
# the root or one predictable folder down is not something to go hunting for.
_LOOSE = {
    "three": ("three.module.js", "three.module.min.js", "three.webgpu.js", "three.js", "three.min.js"),
    "playcanvas": ("playcanvas.mjs", "playcanvas.js", "playcanvas.min.js"),
    "babylon": ("babylon.js", "babylon.max.js"),
    "phaser": ("phaser.esm.js", "phaser.js", "phaser.min.js"),
    "pixi": ("pixi.min.mjs", "pixi.mjs", "pixi.min.js", "pixi.js"),
}
_LOOSE_DIRS = ("", "build", "dist", "js", "lib", "vendor", "public", "static", "src", "assets")

# Engines the forge can actually BUILD an asset with. The rest are detected so the window can
# label the project honestly, but previewing an asset in them is not offered.
_FORGEABLE = ("three", "playcanvas")


def _loose_build(base: Path, kinds) -> Optional[tuple]:
    """(folder, kind, filename) of an engine build kept loose in this folder, or None.

    The folder is returned as well as the file, because a split build's sibling has to be resolved
    against the build's OWN directory and nothing wider."""
    for kind in kinds:
        for d in _LOOSE_DIRS:
            here = (base / d) if d else base
            for name in _LOOSE.get(kind, ()):
                try:
                    cand = here / name
                    if cand.is_file():
                        return here.resolve(), kind, name
                except OSError:
                    continue
    return None
_SUB_SCAN = 24                 # immediate subdirectories to look in, and no deeper


def _engine_in(project: Path) -> dict:
    """Which engine this project runs, and WHERE — the game is often not at the root.

    `fight strength brainrots` keeps its game in `arena/`, so a root-only look found nothing and
    reported a real PlayCanvas game as "not a game". One level down is the difference between a
    useful list and an empty one; two levels down is a disk scan nobody asked for.
    """
    key = str(project)
    hit = _engine_cache.get(key)
    if hit and time.time() - hit[0] < _ENGINE_TTL:
        return hit[1]

    def look(d: Path) -> str:
        for kind, (pkg, rel) in _MODULES.items():
            try:
                if (d / "node_modules" / pkg / rel).exists():
                    return kind
            except Exception:
                continue
        # ...and then the same question of a project that keeps its engine beside the page.
        got = _loose_build(d, list(_MODULES))
        return got[1] if got else ""

    def done(v: dict) -> dict:
        _engine_cache[key] = (time.time(), v)
        return v

    # FIRST, before any look at all. A home directory is not a game even when it has a
    # node_modules in it — and this one does, so checking only the subfolder path still reported
    # "Administrator" as a three.js game. Resolved and case-folded, because on Windows the pinned
    # root and Path.home() routinely differ in case.
    try:
        here = project.resolve()
        home = Path.home().resolve()
        if str(here).lower() == str(home).lower() or here.parent == here or here in home.parents:
            return done({"engine": "", "root": str(project), "sub": ""})
    except Exception:
        pass
    kind = look(project)
    if kind:
        return done({"engine": kind, "root": str(project), "sub": ""})
    try:
        n = 0
        for child in project.iterdir():
            if n >= _SUB_SCAN:
                break
            if not child.is_dir() or child.name.startswith(".") or child.name == "node_modules":
                continue
            n += 1
            kind = look(child)
            if kind:
                return done({"engine": kind, "root": str(child), "sub": child.name})
    except Exception:
        pass
    return done({"engine": "", "root": str(project), "sub": ""})


def module_path(project: str, engine: str = "", file: str = "") -> Optional[Path]:
    """The project's own engine build, so the window renders with what the game renders with.

    Serving it from here rather than from the dev server means the window works with no dev server
    running and no cross-origin question to answer.

    Looks in the GAME root, not only the folder that was passed. A generation records whatever
    project the forge was called with, which for an `arena`-style repo is the workspace root while
    node_modules sits a level down — so a straight `<project>/node_modules` look failed on exactly
    the games this is for.

    `file` names a sibling inside the same package, for a split build (see `module_source`). It is
    confined to that package directory and nothing else.
    """
    try:
        root = Path(project).resolve()
    except Exception:
        return None
    roots = [root]
    try:
        found = _engine_in(root)
        alt = Path(found["root"]).resolve()
        if alt != root:
            roots.append(alt)
        if found["engine"] and not engine:
            engine = found["engine"]
    except Exception:
        pass
    kinds = [engine] if engine in _MODULES else list(_MODULES)
    for base in roots:
        for kind in kinds:
            pkg, rel = _MODULES[kind]
            pkg_dir = base / "node_modules" / pkg
            cands = [pkg_dir / (file or rel)]
            # module_source names a sibling as the import wrote it: `./three.core.js` inside
            # build/three.module.js becomes file=three.core.js, which lives in build/, not at the
            # package root. Found only at the root, every npm three from r163 on answered a 404
            # here, hidden by the viewport falling back to the Studio's own copy.
            if file:
                cands.append(pkg_dir / Path(rel).parent / file)
            for cand in cands:
                try:
                    if not cand.exists() or not cand.is_file():
                        continue
                    # Confined to the package, so `file` can never walk out of it.
                    if cand.resolve().is_relative_to(pkg_dir.resolve()):
                        return cand.resolve()
                except Exception:
                    continue
    # Nothing installed. Try a build kept beside the page, confined to the folder it was found in
    # exactly as the package case is confined to its package.
    for base in roots:
        got = _loose_build(base, kinds)
        if not got:
            continue
        here, _kind, name = got
        try:
            cand = here / (file or name)
            if cand.is_file() and cand.resolve().is_relative_to(here):
                return cand.resolve()
        except Exception:
            continue
    return None


# three.js became a SPLIT build at r163: `three.module.js` opens with
# `import { … } from './three.core.js'`. Serving one file meant the browser then asked for that
# sibling relative to the ENDPOINT path — `/api/engine/three.core.js` — which is not a route, so
# the import 404'd and every three project from r163 on failed to load. Measured: r185 and r183
# broke, r160 and every PlayCanvas build were fine because they are single-file.
#
# Rewriting the specifier to point back at this endpoint is the smallest fix that works for any
# split package, not just three's one line.
_REL_IMPORT = re.compile(r"""(\bfrom\s*|\bimport\s*\(?\s*)(['"])\.{1,2}/([\w.\-/]+)\2""")
_SRC_TTL = 300.0
_src_cache: dict = {}


_BARE_THREE = re.compile(r"(\bfrom\s*)(['\"])three\2")


def module_source(project: str, engine_kind: str = "", file: str = "") -> Optional[tuple]:
    """(text, media type) for an engine build, with its relative imports pointed back here."""
    p = module_path(project, engine_kind, file)
    if not p:
        return None
    try:
        stamp = p.stat().st_mtime
    except OSError:
        return None
    key = (str(p), stamp, str(project), str(engine_kind))
    hit = _src_cache.get(key)
    if hit and time.time() - hit[0] < _SRC_TTL:
        return hit[1], "text/javascript"
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return None
    from urllib.parse import quote

    def point_here(m):
        base = "/api/engine/module?project=%s&engine_kind=%s&file=%s" % (
            quote(str(project), safe=""), quote(str(engine_kind), safe=""),
            quote(m.group(3), safe=""))
        return "%s%s%s%s" % (m.group(1), m.group(2), base, m.group(2))

    out = _REL_IMPORT.sub(point_here, text)
    # AND THE BARE ONES. `three/examples/jsm/loaders/GLTFLoader.js` imports from 'three' by name,
    # which a browser cannot resolve at all — and even if it could, it would be a SECOND three,
    # whose classes the editor's scene would not recognise. Pointed at this endpoint it is the
    # same instance the viewport is already using, which is the only version that can share a
    # scene with it.
    here = "/api/engine/module?project=%s&engine_kind=%s" % (
        quote(str(project), safe=""), quote(str(engine_kind or "three"), safe=""))
    out = _BARE_THREE.sub(lambda m: "%s%s%s%s" % (m.group(1), m.group(2), here, m.group(2)), out)
    if len(_src_cache) > 12:                      # a handful of engines, not a general cache
        _src_cache.clear()
    _src_cache[key] = (time.time(), out)
    return out, "text/javascript"


# The Studio's own three, copied into the bundle by `npm run build:three`. It is what a project
# with no three of its own gets, so a PlayCanvas or Phaser game can still open a .glb.
STUDIO_THREE = "/vendor/three/three.module.js"


def three_url(project: str) -> str:
    """The three the editor should run for this project: its own if it has one, ours if not.

    ONE RULE, used by the loader below and by the viewport in the window, because a GLTFLoader
    bound to one copy of three and a viewport running another produces meshes the scene refuses —
    the exact failure the `from 'three'` rewrite exists to prevent.
    """
    from urllib.parse import quote
    if module_path(project, "three") is not None:
        return "/api/engine/module?project=%s&engine_kind=three" % quote(str(project), safe="")
    return STUDIO_THREE


VENDOR = Path(__file__).resolve().parent / "vendor"
# The three files a glTF loader is (the loader, and the two helpers it imports), plus the exporter
# the forge writes a bench out with. Vendored rather than looked for, because half these games do
# not install three at all — they drop a single `three.module.js` into a `vendor/` folder, and a
# package that is not there has no examples in it.
_LOADER_FILES = {"GLTFLoader.js", "BufferGeometryUtils.js", "SkeletonUtils.js", "GLTFExporter.js"}


def _own_three(url: str) -> str:
    """The viewport's three URL if it is one this machine serves, else "".

    It is spliced into JavaScript the server hands back, so it is allowed to be exactly three
    things: the Studio's vendored three, this backend's module endpoint, or a loopback dev server
    the viewport has already imported from. Nothing with a quote, a space or a newline in it.
    """
    from urllib.parse import urlparse
    u = str(url or "").strip()
    if not u or len(u) > 800 or any(c in u for c in "'\"\\\n\r\t <>`"):
        return ""
    if u.startswith("/"):
        if u.startswith("//"):
            return ""
        return u if (u.startswith(STUDIO_THREE) or u.startswith("/api/engine/module?")) else ""
    try:
        p = urlparse(u)
    except Exception:
        return ""
    if p.scheme in ("http", "https") and (p.hostname or "") in ("127.0.0.1", "localhost", "::1"):
        return u
    return ""


def loader_source(project: str, name: str = "GLTFLoader.js", three: str = "") -> Optional[tuple]:
    """A glTF loader wired to the three the VIEWPORT is running, so what it builds joins its scene.

    `three` is the exact module URL the viewport imported, and it wins. The viewport takes the
    first three that loads from a list that can end on ANOTHER project's build — a .glb in a
    PlayCanvas workspace opened on Dino Smash's r160 — while this loader used to bind to the
    owning project's three or the Studio's r183 by a rule of its own. Two rules, two copies: the
    r160 renderer called `material.onBuild` on r183 materials, threw inside the frame loop, and the
    viewport went blank under a full outliner and a healthy triangle count. Module identity is by
    URL string, so the only rule that cannot disagree is "the URL the viewport actually imported".
    `three_url(project)` remains the answer when none is given.

    Two rewrites and nothing else: `from 'three'` becomes that URL, and the loader's own two
    relative imports come back here carrying the same URL, so the helpers share it too.
    """
    if name not in _LOADER_FILES:
        return None
    f = VENDOR / name
    if not f.is_file():
        return None
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return None
    from urllib.parse import quote
    proj = quote(str(project), safe="")
    given = _own_three(three)
    three_u = given or three_url(project)
    out = _BARE_THREE.sub(lambda m: "%s%s%s%s" % (m.group(1), m.group(2), three_u, m.group(2)), text)
    tail = ("&three=%s" % quote(given, safe="")) if given else ""

    def sibling(m):
        base = "/api/engine/loader?project=%s&name=%s%s" % (proj, quote(Path(m.group(3)).name, safe=""), tail)
        return "%s%s%s%s" % (m.group(1), m.group(2), base, m.group(2))

    out = _REL_IMPORT.sub(sibling, out)
    return out, "text/javascript"


def sheet_path(path: str) -> Optional[Path]:
    """A sheet to display, confined to the Studio's own data directory."""
    try:
        p = Path(path).resolve()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") \
                and p.is_relative_to(DATA_DIR.resolve()):
            return p
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# What is happening now
# ---------------------------------------------------------------------------
_AGENTS_TTL = 3.0
_agents_cache: tuple = (0.0, [])


def _working_agents() -> list:
    """Every subagent working right now, for the sessions this backend is actually running.

    THE WINDOW KNEW WHAT WAS RENDERED AND NEVER WHO WAS WORKING. Open it while an agent is
    modelling and it shows the last thing anybody rendered, with no sign that anything is
    happening — which reads as "nothing is running" when the truth is "this one is not going
    through the forge". An agent driving Blender, or reading, or thinking through a long turn,
    produces no generations for minutes and is not idle.

    SCOPED TO THE LIVE SESSIONS, and that is not an optimisation detail. Asking all 35 pinned
    workspaces measured 4.4 SECONDS, on a call the window polls every two — which is precisely the
    shape of the bug that once filled the browser's connection pool and left the chat blank. An
    agent runs inside a chat session, the running sessions are already known, and there are one or
    two of them. Cached besides, so two panels asking at once cost one answer.
    """
    global _agents_cache
    now = time.time()
    if now - _agents_cache[0] < _AGENTS_TTL:
        return _agents_cache[1]
    out: list = []
    try:
        from . import subagents
        from . import cc_session
        for pid in list(cc_session._live.keys()):
            for a in subagents.working(pid):
                row = {
                    "agent_id": a.get("agent_id", ""),
                    "project": pid,
                    "description": str(a.get("description") or a.get("agent_type") or "")[:80],
                    "model": a.get("model", ""),
                    # `moving` is "it wrote within the running window". Quiet is not stopped: a
                    # long turn writes nothing at all until the whole message lands.
                    "moving": bool(a.get("moving")),
                    "idle_s": round(now - float(a.get("updated") or 0), 1),
                    "tools": a.get("tools") or 0,
                    "tokens": a.get("tokens") or 0,
                    "phase": "", "tool": "", "detail": "",
                }
                # Only for the handful that are live. `working` answers from the parent's summary,
                # which does not carry what the agent is DOING; that is one bounded read of the
                # tail of its own transcript, and it is the line the user actually wants.
                try:
                    f = subagents._file_for(pid, row["agent_id"])
                    act = subagents._last_activity(f) if f else {}
                    row["phase"] = act.get("phase") or ""
                    row["tool"] = act.get("tool") or ""
                    row["detail"] = str(act.get("detail") or "")[:120]
                except Exception:
                    pass
                out.append(row)
    except Exception:
        out = []
    out.sort(key=lambda r: (not r["moving"], r["idle_s"]))
    out = out[:12]
    _agents_cache = (now, out)
    return out


def _activity() -> list:
    """What the forge is doing this second, for the window's live line."""
    try:
        from . import live
        return live.activity()
    except Exception:                                    # pragma: no cover - defensive
        return []


_RECORDS_MEMO = perf.Memo(limit=4)


def _forge_records() -> list:
    """(mtime, path) for every forge record, re-listed only when data/live changes.

    The glob walked every project folder and stat-ed every record on EVERY call — and this runs on
    the engine pill's poll, on a file open and on a workspace switch. Measured at 316-552 ms each
    time. A new record creates a file inside one of those folders, which moves that folder's own
    date, so the stamp notices it without touching the records. `recent` is still counted here, on
    every call, because it depends on the clock rather than on the files.
    """
    def build() -> list:
        out = []
        try:
            for jf in _LIVE_DIR.glob("*/forge-*.json"):
                try:
                    out.append((jf.stat().st_mtime, jf))
                except OSError:
                    continue
        except Exception:
            return []
        return out

    return _RECORDS_MEMO.get("live", perf.dir_stamp(_LIVE_DIR), build)


def state() -> dict:
    """One call for the status pill: is the engine in use, and by what."""
    now = time.time()
    tabs, avail, why = [], False, ""
    try:
        from . import live
        st = live.status()
        tabs = st.get("tabs") or []
        avail, why = bool(st.get("available")), str(st.get("why") or "")
    except Exception as e:                               # pragma: no cover - defensive
        why = str(e)
    recent, last = 0, None
    for mt, jf in _forge_records():
        if now - mt < _BUSY_WINDOW:
            recent += 1
        if last is None or mt > last[0]:
            last = (mt, jf)
    latest = None
    if last:
        rec = _read(last[1]) or {}
        latest = {"id": rec.get("id", ""), "label": rec.get("label", ""),
                  "project_name": rec.get("project_name", ""), "engine": rec.get("engine", ""),
                  "ts": rec.get("ts", last[0]), "sheet": rec.get("sheet", ""),
                  "ok": rec.get("ok", True)}
    view = settings.get("cc_engine_view") or {}
    # IN USE means an agent USED it lately, the same five minutes as a generation, not that a tab it
    # opened is still open. A tab outlives its agent's last call by minutes (live.reap_idle_tabs),
    # and every open one used to count, so an agent that had finished a day earlier still read as
    # working.
    for t in tabs:
        t["active"] = float(t.get("idle_s") or 0) < _BUSY_WINDOW
    in_use = sum(1 for t in tabs if t["active"])
    return {"ok": True, "enabled": enabled(), "available": avail, "why": why,
            "tabs": tabs, "open_games": len(tabs), "in_use": in_use,
            "recent_generations": recent, "busy": recent > 0 or in_use > 0,
            # The shared judging resolution, so the window and the agents agree on one number.
            "view": {"w": int(view.get("w") or 1280), "h": int(view.get("h") or 720),
                     "label": str(view.get("label") or "")},
            "latest": latest,
            # WHAT IS HAPPENING RIGHT NOW, not what happened. A build is milliseconds, a sweep of
            # 36 angles is five seconds, and both used to be invisible until the run finished.
            "activity": _activity(),
            # Who is working. A generation says what was made; this says what is being made, and
            # by an agent that may never call the forge at all.
            "agents": _working_agents()}
