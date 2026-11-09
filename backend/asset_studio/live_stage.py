# -*- coding: utf-8 -*-
"""The running 2D game's objects as tools: Phaser, PixiJS and HTML pages.

live_scene.py answers these questions for a 3D game, with world metres, ground tests and triangle
hits. None of that means anything in a 2D game, so its page script refuses one. This module keeps
the same routes and the same file, and hands the work to `stageCall` in the Studio's runtime
(frontend/src/components/engine/edit/stage.ts), the code a shipped game applies its edits with.

Same document, same undo stack, same locks as live_scene: an edit saved here is `parts[key]` in
`studio.edits.json`, with x and y in `pos` and the turn in `rot[2]`.

Units an agent sees: positions in the object's own coordinates, `move` in pixels of the page, turns
in DEGREES. The file keeps radians, as it does for a 3D game.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import live as L
from . import live_scene as S

# What the shim reports for a page with no 3D engine it can reach. A page with a WebGL canvas it
# cannot reach still has its HTML: KAPOW draws a WebGL surface under a React interface.
STAGE_ENGINES = {"phaser", "pixi", "dom", "canvas2d", "webgl"}
_OPEN_3D = {"three", "playcanvas", "babylon", "cocos"}


def handles(project: str) -> bool:
    """Should this project's scene calls come here? Decided by what the open tab reported."""
    return str(L._entry(project).get("engine") or "") in STAGE_ENGINES


def _runtime_url(project: str) -> str:
    # The file's time in the address. A module is cached by its URL for the life of the page, so a
    # rebuilt runtime would otherwise be ignored by every tab that is already open.
    from .config import FRONTEND_DIST
    rt = S._rt(project)["runtime"]
    try:
        return "%s?v=%d" % (rt, int((Path(FRONTEND_DIST) / "studio-runtime.js").stat().st_mtime))
    except OSError:
        return rt


def _call(project: str, op: str, args: Optional[dict] = None) -> Any:
    target = "(window.__live && __live.stageTarget ? __live.stageTarget() : document)"
    expr = ("(async()=>{const t=%s;if(!t)return {ok:false,use3d:true};"
            "const R=await import(%s);return R.stageCall(t,%s,%s);})()"
            % (target, json.dumps(_runtime_url(project)), json.dumps(op), json.dumps(args or {})))
    return S._in_tab(project, expr)


def _refused(res: Any) -> Optional[dict]:
    if not isinstance(res, dict):
        return {"ok": False, "error": "the page returned nothing"}
    if res.get("use3d"):
        return {"ok": False, "error": "this page has a three.js or PlayCanvas scene: use the 3D editor", "use3d": True}
    if not res.get("ok"):
        return res if res.get("error") else {"ok": False, "error": "the page refused the call"}
    return None


# --------------------------------------------------------------------------------------- reading
def view(project: str) -> dict:
    """Everything on screen, in page pixels. The Edit tab polls this to draw its boxes, so it is not
    behind `cc_scene_edit`, the switch of the agents' tools: objects() checks that one."""
    bad = L._guard(project)
    if bad:
        return bad
    e = L._entry(project)
    bad = S._need_open(e)
    if bad:
        return bad
    try:
        res = _call(project, "list")
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": str(ex)[:600]}
    return _refused(res) or res


def objects(project: str, q: str = "", pick=None, limit: int = 60) -> dict:
    """The same rows for an agent: filtered, capped, with turns in degrees."""
    res = view(project)
    if not res.get("ok"):
        return res
    rows = list(res.get("rows") or [])
    W, H = (res.get("view") or [0, 0])[:2]
    if pick not in (None, "", []):
        try:
            xy = [float(p) for p in (pick if isinstance(pick, (list, tuple)) else str(pick).split(","))]
        except ValueError:
            xy = []
        if len(xy) != 2:
            return {"ok": False, "error": "pick is x,y in 0..1 of the view (0,0 top left), for example pick=0.5,0.5"}
        px, py = xy[0] * (W or 1), xy[1] * (H or 1)
        under = [r for r in rows if r.get("visible")
                 and r["box"][0] <= px <= r["box"][0] + r["box"][2] and r["box"][1] <= py <= r["box"][1] + r["box"][3]]
        rows = list(reversed(under))              # the last one drawn is the one on top
    if q:
        low = str(q).lower()
        rows = [r for r in rows if low in str(r.get("key", "")).lower() or low in str(r.get("name", "")).lower()
                or low in str(r.get("text", "")).lower() or low in str(r.get("texture", "")).lower()]
    total = len(rows)
    out = []
    for r in rows[:max(1, min(500, int(limit or 60)))]:
        row = dict(r)
        row["rot"] = round(math.degrees(float(r.get("rot") or 0)), 3)
        out.append(row)
    return {"ok": True, "engine": res.get("engine"), "units": "pixels and degrees", "view": res.get("view"),
            "rows": out, "total": total, "saved": res.get("saved") or []}


# --------------------------------------------------------------------------------------- changing
def _num_list(v, n: int) -> Optional[list]:
    """A list of at least n numbers, or None. A 3D-shaped [x, y, z] is taken as it is written."""
    if isinstance(v, (list, tuple)) and len(v) >= n and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v[:n]):
        return [float(x) for x in v[:n]]
    return None


def _turn(v) -> Optional[float]:
    """A turn in degrees, as one number or as [0, 0, degrees]; radians for the page."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return math.radians(float(v))
    got = _num_list(v, 3)
    return math.radians(got[2]) if got else None


_KEYS = {"target", "name", "pos", "position", "move", "rot", "rotation", "rotate", "scale", "alpha", "visible"}


def _specs(body: dict) -> tuple[list, str, list]:
    raw = body.get("edits") or []
    if not isinstance(raw, list):
        return [], "edits must be a list of edits", []
    if not raw:
        one = {k: body.get(k) for k in _KEYS if body.get(k) not in (None, "")}
        raw = [one] if one else []
    if not raw:
        return [], ("nothing to do: give a target and what to change (pos, move, rot, rotate, scale, alpha, "
                    "visible), or edits:[…], undo:true, clear:[keys]|\"all\""), []
    specs, ignored = [], []
    for i, sp in enumerate(raw):
        if not isinstance(sp, dict):
            return [], "edit %d is not an object" % i, []
        target = str(sp.get("target") or sp.get("name") or "").strip()
        if not target:
            return [], "edit %d has no target (a key or a name from /api/live/objects)" % i, []
        out: dict = {"target": target}
        pos = sp.get("pos") if sp.get("pos") is not None else sp.get("position")
        if pos is not None:
            out["pos"] = _num_list(pos, 2)
            if out["pos"] is None:
                return [], "edit %d: pos is [x, y]" % i, []
        if sp.get("move") is not None:
            out["move"] = _num_list(sp["move"], 2)
            if out["move"] is None:
                return [], "edit %d: move is [dx, dy] in page pixels" % i, []
        rot = sp.get("rot") if sp.get("rot") is not None else sp.get("rotation")
        if rot is not None:
            out["rot"] = _turn(rot)
            if out["rot"] is None:
                return [], "edit %d: rot is a number of degrees" % i, []
        if sp.get("rotate") is not None:
            out["rotate"] = _turn(sp["rotate"])
            if out["rotate"] is None:
                return [], "edit %d: rotate is a number of degrees" % i, []
        if sp.get("scale") is not None:
            sc = sp["scale"]
            if isinstance(sc, (int, float)) and not isinstance(sc, bool) and math.isfinite(sc):
                out["scale"] = float(sc)
            else:
                out["scale"] = _num_list(sc, 2)
                if out["scale"] is None:
                    return [], "edit %d: scale is a number or [x, y]" % i, []
        if sp.get("alpha") is not None:
            if not isinstance(sp["alpha"], (int, float)) or isinstance(sp["alpha"], bool):
                return [], "edit %d: alpha is a number from 0 to 1" % i, []
            out["alpha"] = float(sp["alpha"])
        if isinstance(sp.get("visible"), bool):
            out["visible"] = sp["visible"]
        if len(out) == 1:
            return [], "edit %d names %r but changes nothing" % (i, target), []
        ignored += [k for k in sp if k not in _KEYS]
        specs.append(out)
    return specs, "", sorted(set(ignored))


def _written(vals: dict) -> dict:
    out = dict(vals or {})
    if "rot" in out and isinstance(out["rot"], list) and len(out["rot"]) >= 3:
        out["rot"] = round(math.degrees(out["rot"][2]), 3)
    return out


def edit(project: str, body: dict) -> Optional[dict]:
    """An edit of a 2D game, in the same call shape as live_scene.edit. `undo` and `clear` stay in
    live_scene, which knows the record this writes. None: this page turned out to be 3D."""
    specs, err, ignored = _specs(body)
    if err:
        return {"ok": False, "error": err}
    save = bool(body.get("save"))
    if save:
        _doc, derr = S.read_doc(project)
        if derr:
            return {"ok": False, "error": derr}
    for sp in specs:
        if save:
            sp["save"] = True
    with S._lock_for(project):
        try:
            res = _call(project, "edit", {"specs": specs})
        except Exception as ex:                          # noqa: BLE001
            return {"ok": False, "error": str(ex)[:600]}
        if isinstance(res, dict) and res.get("use3d"):
            return None
        bad = _refused(res)
        if bad:
            return bad
        results = res.get("results") or []
        applied = [r for r in results if r.get("ok")]
        disk = None
        info: dict = {}
        if save and applied:
            doc, derr = S.read_doc(project)
            if derr:
                info["error"] = derr
            else:
                created = doc is None
                doc = doc if doc is not None else S.new_doc()
                disk = []
                info["created"] = created
                if res.get("engine") == "dom":
                    # What tells the shim to apply this file to the page itself: a document exists on
                    # every page, so it must not be taken for the target of a 3D game's saved edits.
                    doc["stage"] = "dom"
                for r in applied:
                    vals = dict(r.get("save") or {})
                    tf = {k: v for k, v in vals.items() if k in ("pos", "rot", "scale")}
                    pid = r.get("placed")
                    if pid:
                        found, prev = S.merge_placed_fields(doc, pid, tf) if tf else (False, None)
                        if found:
                            disk.append({"section": "placed", "id": pid, "prev": prev})
                            vals = {k: v for k, v in vals.items() if k not in tf}
                        if "alpha" in vals or "hidden" in vals:
                            for it in doc.get("placed") or []:
                                if isinstance(it, dict) and it.get("id") == pid:
                                    if not found:
                                        disk.append({"section": "placed", "id": pid, "prev": dict(it)})
                                    it.update({k: vals[k] for k in ("alpha", "hidden") if k in vals})
                        r["saved"] = True
                        continue
                    if vals:
                        disk.append({"section": "parts", "key": r["key"], "prev": S.merge_part(doc, r["key"], vals)})
                    r["saved"] = True
                try:
                    info.update(S.write_doc(project, doc))
                except Exception as ex:                  # noqa: BLE001
                    disk = None
                    info["error"] = "could not write %s: %s" % (S.edits_path(project), ex)
                    for r in applied:
                        r["saved"] = False
        if applied:
            S._push_undo(project, {"kind": "edit", "at": time.time(), "stage": True,
                                   "what": ", ".join(r["key"] for r in applied)[:200],
                                   "live": [{"key": r["key"], "vals": r["undo"]} for r in applied],
                                   "disk": disk, "created": bool(disk is not None and info.get("created"))})
    for r in results:
        r.setdefault("saved", False)
        r.pop("undo", None)
        written = r.pop("save", None)
        if r.get("saved") and written:
            r["written"] = _written(written)
        for side in ("before", "after"):
            if isinstance(r.get(side), dict):
                r[side] = _written(r[side])
    out: dict = {"ok": all(r.get("ok") for r in results), "engine": res.get("engine"), "units": "pixels and degrees"}
    if len(results) == 1 and not body.get("edits"):
        out.update(results[0])
        out["ok"] = bool(results[0].get("ok"))
    else:
        out["results"] = results
        out["failed"] = sum(1 for r in results if not r.get("ok"))
    if save:
        out["file"] = str(S.edits_path(project))
        out.update({k: v for k, v in info.items() if k in ("bak", "error")})
    if ignored:
        out["ignored"] = ignored
    if applied:
        out["undo"] = "POST /api/live/edit {\"project\": …, \"undo\": true} puts this call back%s" % (
            ", live and on disk" if save else "")
    return out


def restore(project: str, items: list) -> dict:
    """Put objects back to the values an undo record kept."""
    res = _call(project, "restore", {"items": items})
    return res if isinstance(res, dict) else {"ok": False, "error": "the page returned nothing"}


def unplace(project: str, ids: list) -> dict:
    res = _call(project, "unplace", {"ids": ids})
    return res if isinstance(res, dict) else {"ok": False, "error": "the page returned nothing"}


def sync(project: str, entries: list) -> dict:
    """What an undo does to placements: each one put back as the item it was, or taken out."""
    placed, removed, failed = [], [], []
    for en in reversed(entries or []):
        pid, put = en.get("id"), en.get("put")
        if put:
            got = _call(project, "place", {"item": put}) or {}
            (placed if got.get("ok") else failed).append(pid)
        else:
            unplace(project, [pid])
            removed.append(pid)
    return {"ok": True, "placed": placed, "removed": removed, "failed": failed}


def pause(project: str, on: bool) -> dict:
    """Hold the game still, so a moving object can be selected. HTML has no loop to hold."""
    bad = L._guard(project)
    if bad:
        return bad
    bad = S._need_open(L._entry(project))
    if bad:
        return bad
    try:
        res = _call(project, "pause", {"on": bool(on)})
    except Exception as ex:                              # noqa: BLE001
        return {"ok": False, "error": str(ex)[:600]}
    return _refused(res) or res


# --------------------------------------------------------------------------------------- placing
def served_path(project: str, rel: str) -> str:
    """A project file as the game's server names it: a `public/` folder is served as its root."""
    s = str(rel or "").replace("\\", "/").lstrip("/")
    return re.sub(r"^(?:.*/)?public/", "", s, count=1)


def _image_of(asset) -> str:
    if isinstance(asset, dict):
        for k in ("image", "url", "path", "file", "model"):
            if isinstance(asset.get(k), str) and asset[k].strip():
                return asset[k].strip()
        return ""
    return str(asset or "").strip()


_IMAGE = re.compile(r"\.(png|jpe?g|webp|gif|svg|avif)$", re.I)


def place(project: str, body: dict) -> Optional[dict]:
    """Put an image of the project into the running 2D game. `save` keeps it. None: this page is 3D."""
    rel = _image_of(body.get("asset"))
    if not rel:
        return {"ok": False, "error": "asset is the path of an image in this project"}
    if not _IMAGE.search(rel.split("?")[0]):
        return {"ok": False, "error": "a 2D game places images only: %s is not one" % rel}
    found = S._project_file(project, rel)
    if not found:
        return {"ok": False, "error": "no such image in this project: %s" % rel}
    save = bool(body.get("save"))
    if save:
        _doc, derr = S.read_doc(project)
        if derr:
            return {"ok": False, "error": derr}
    pos = _num_list(body.get("pos") if body.get("pos") is not None else body.get("at"), 2)
    name = str(body.get("name") or "").strip() or Path(found).stem
    pid = str(body.get("id") or "").strip() or S._new_id()
    turn = _turn(body.get("rot")) if body.get("rot") is not None else 0.0
    sc = body.get("scale")
    if isinstance(sc, (int, float)) and not isinstance(sc, bool):
        scale = [float(sc), float(sc)]
    else:
        scale = _num_list(sc, 2) or [1.0, 1.0]
    with S._lock_for(project):
        try:
            if pos is None:
                got = _call(project, "at", {})
                if isinstance(got, dict) and got.get("use3d"):
                    return None
                bad = _refused(got)
                if bad:
                    return bad
                pos = got.get("pos") or [0.0, 0.0]
            item = {"id": pid, "name": name, "ref": {"kind": "image", "url": served_path(project, found)},
                    "pos": [float(pos[0]), float(pos[1]), 0.0], "rot": [0.0, 0.0, turn or 0.0],
                    "scale": [scale[0], scale[1], 1.0]}
            res = _call(project, "place", {"item": item})
        except Exception as ex:                          # noqa: BLE001
            return {"ok": False, "error": str(ex)[:600]}
        if isinstance(res, dict) and res.get("use3d"):
            return None
        bad = _refused(res)
        if bad:
            return bad
        disk = None
        created = False
        out = {"ok": True, "id": pid, "key": res.get("key"), "name": name, "pos": item["pos"][:2],
               "url": item["ref"]["url"], "saved": False}
        if save:
            doc, derr = S.read_doc(project)
            if derr:
                out["error"] = derr
            else:
                created = doc is None
                doc = doc if doc is not None else S.new_doc()
                prev = S.merge_placed(doc, item)
                disk = [{"section": "placed", "id": pid, "prev": prev}]
                try:
                    S.write_doc(project, doc)
                    out["saved"] = True
                    out["file"] = str(S.edits_path(project))
                except Exception as ex:                  # noqa: BLE001
                    disk = None
                    out["error"] = "could not write %s: %s" % (S.edits_path(project), ex)
        S._push_undo(project, {"kind": "place", "at": time.time(), "stage": True, "what": name,
                               "placed_live": [{"id": pid, "put": None}], "disk": disk, "created": created})
    return out
