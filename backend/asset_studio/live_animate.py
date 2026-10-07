# -*- coding: utf-8 -*-
"""Judge an animation: turn the asset, run the cycle, and measure the feet against the floor.

Everything else the forge does is a STILL. A gait bug does not live in a still — a foot that
slides while it is planted, or hangs a centimetre above the floor, looks perfectly correct in any
single frame and from every angle. It is only visible across time, against a fixed ground.

So this is the forge's loop with one more axis, and the same rule as the rest of it: numbers
before pictures. The picture shows you the run; the findings tell you which foot, on which frame,
and by how much.

THREE THINGS IN THE STUDIO HAD TO STOP BEING HELPFUL.

  1. `F.stance()` takes the floor to be the lowest mesh in the model, recomputed per call. A dino
     floating four centimetres up therefore reports perfect contact — and so does one that RISES
     mid-stride, on every single frame. Right for judging one pose; it hides the exact fault this
     exists to find. `F.trackAt(names, ground)` measures against a ground the caller fixes.
  2. `F.view()` slides the ground PLANE to sit under whatever it just framed. The floor in the
     picture would follow the feet. `F.lock()` puts it where the caller says it is and leaves it.
  3. `F.view()` also re-frames on the current bounds. Frame each frame on its own and the subject
     re-centres, cancelling out the motion being judged — the variant sweep's trap, one axis over.
     The camera is fixed once, over the union of every sampled time.

TWO PASSES, AND A REBUILD BETWEEN THEM. The first pass measures with no pictures, which is what
gives the union bounds. Then the asset is rebuilt from the same code and re-armed, so the second
pass starts from exactly the same state — which `delta` drivers require, since they can only step
forward.
"""
from __future__ import annotations

import json
import math
import time
from typing import Optional

from . import live as L
from . import review as _review
from .config import settings

_MAX_FRAMES = 24               # a strip nobody can read is not a better strip
_MIN_FRAMES = 2
_DEFAULT_FRAMES = 8

# What counts as a foot when the caller does not say. Deliberately generous: a missed foot is
# reported as missing by name, which is recoverable, while a wrong guess is silent.
_FOOTY = ("foot", "feet", "toe", "paw", "hoof", "heel", "claw", "talon", "sole", "leg", "shin")


def _times(times: Optional[list], frames: int, duration: float) -> list:
    """The moments to sample. Explicit beats a count; a count beats a guess."""
    if times:
        out = sorted({round(float(t), 4) for t in times if isinstance(t, (int, float))})
        return out[:_MAX_FRAMES]
    n = int(frames or _DEFAULT_FRAMES)
    n = max(_MIN_FRAMES, min(_MAX_FRAMES, n))
    d = float(duration or 1.0)
    # The last sample lands one step BEFORE the end, not on it. A loop's first and last frame are
    # the same pose, and a strip that shows it twice wastes a panel and reads as a stutter.
    return [round(d * i / n, 4) for i in range(n)]


def _auto_feet(parts: list) -> list:
    """Parts whose name suggests they touch the ground."""
    out = []
    for p in parts or []:
        name = str((p or {}).get("name") or p or "")
        low = name.lower()
        if any(k in low for k in _FOOTY) and name not in out:
            out.append(name)
    return out[:6]


def _hyp(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _gait(rows: list, times: list, names: list, tol: float) -> dict:
    """The report. What a person would say after watching the cycle twenty times.

    `rows` is one `trackAt` per sampled time, in time order.
    """
    out: dict = {"findings": [], "feet": {}, "support": {}, "root": {}}
    if not rows or not names:
        return out

    # ---- did the whole asset travel, or is this a cycle in place?
    #
    # THIS DECIDES WHETHER SLIDING IS A BUG. In a cycle played in place the world does not move,
    # so a planted foot MUST slide backwards — that is what makes it look like walking. Flagging
    # it there would be wrong, and flagging nothing when the root does move would miss the real
    # thing. So the question is asked once, first, and every later judgement depends on it.
    roots = [r.get("root") for r in rows if r.get("root")]
    travel = 0.0
    if len(roots) > 1:
        travel = max(_hyp((r[0], r[2]), (roots[0][0], roots[0][2])) for r in roots)
    in_place = travel <= tol * 2
    out["root"] = {"travel": round(travel, 4), "in_place": in_place}

    # ---- per foot
    slides = {}
    for n in names:
        seq = [(r.get("parts") or {}).get(n) for r in rows]
        seen = [s for s in seq if s]
        if not seen:
            out["findings"].append(
                'NOT FOUND: nothing in this asset has "%s" in its name, so it was not measured. '
                'Name the part, or pass the names you do have in `track`.' % n)
            out["feet"][n] = {"found": False}
            continue

        gaps = [s["gap"] for s in seen]
        lowest, highest = min(gaps), max(gaps)
        downs = [i for i, s in enumerate(seq) if s and s["down"]]

        # Contiguous runs of contact, and how far the foot travelled inside each one.
        best_travel, best_run = 0.0, ()
        run: list = []

        def close(r):
            nonlocal best_travel, best_run
            if len(r) < 2:
                return
            pts = [seq[i]["at"] for i in r]
            far = max(_hyp(pts[a], pts[b]) for a in range(len(pts)) for b in range(a + 1, len(pts)))
            if far > best_travel:
                best_travel, best_run = far, (times[r[0]], times[r[-1]])

        for i, s in enumerate(seq):
            if s and s["down"]:
                run.append(i)
            else:
                close(run)
                run = []
        close(run)

        slides[n] = best_travel
        out["feet"][n] = {
            "found": True, "frames_down": len(downs),
            "closest": round(lowest, 4), "furthest": round(highest, 4),
            "slide": round(best_travel, 4),
            "slide_between": list(best_run) if best_run else [],
        }

        # ---- the findings, worst first
        if lowest > tol:
            out["findings"].append(
                "FLOATING: %s never reaches the ground. Its closest is %.3f above it, across all "
                "%d frames." % (n, lowest, len(seq)))
        if lowest < -tol:
            worst = times[gaps.index(lowest)]
            out["findings"].append(
                "SINKING: %s goes %.3f BELOW the ground at t=%.2f." % (n, -lowest, worst))
        if not in_place and best_travel > tol and len(downs) > 1:
            out["findings"].append(
                "FOOT SLIDE: %s travels %.3f along the ground while it is planted (t=%.2f to "
                "%.2f). The asset moves, so a planted foot should stay where it was put."
                % (n, best_travel, best_run[0], best_run[1]))

    # ---- support: is anything ever holding it up?
    none_down = [times[i] for i, r in enumerate(rows)
                 if not any((r.get("parts") or {}).get(n, {}).get("down") for n in names)]
    out["support"] = {"frames_with_nothing_down": none_down,
                      "of": len(rows)}
    if none_down and len(none_down) == len(rows):
        out["findings"].insert(0,
            "NOTHING EVER TOUCHES THE GROUND. Not one tracked part reaches it on any frame — "
            "check that `ground` is where you think it is (it is %.3f) before reading anything "
            "else here." % (rows[0].get("ground", 0.0)))
    elif none_down:
        out["findings"].append(
            "NO SUPPORT at t=%s: every tracked part is off the ground. A run has airborne frames "
            "and a walk does not, so this is only a fault if it was meant to be a walk."
            % ", ".join("%.2f" % t for t in none_down[:6]))

    # ---- an in-place cycle: the feet must agree with each other
    #
    # A treadmill foot is SUPPOSED to slide. What is never supposed to happen is two planted feet
    # sliding at different rates — that is the one thing that reads as skating whichever way the
    # cycle is played.
    if in_place:
        moving = {n: v for n, v in slides.items() if v > tol}
        if len(moving) > 1:
            lo, hi = min(moving.values()), max(moving.values())
            if hi > lo * 1.6 and hi - lo > tol:
                worst = max(moving, key=lambda k: moving[k])
                least = min(moving, key=lambda k: moving[k])
                out["findings"].append(
                    "FEET DISAGREE: this is a cycle in place, so a planted foot is meant to slide "
                    "— but %s slides %.3f while %s slides only %.3f. Planted feet must travel at "
                    "the same rate or the walk skates." % (worst, hi, least, lo))
        out["findings"].append(
            "IN PLACE: the asset does not travel (root moved %.3f), so sliding feet are expected "
            "and are not reported as a fault. Drive the root if you want them judged as planted."
            % travel)
    return out


@L._settles
def animate(project: str, js: str = "", drive: str = "auto", fn: str = "", clip: str = "",
            mode: str = "absolute", step: float = 0.0, duration: float = 0.0,
            times: Optional[list] = None, frames: int = 0, view: str = "side",
            ground: float = 0.0, track: Optional[list] = None, margin: float = 1.15,
            label: str = "", quality: str = "", numbers: bool = False, engine: str = "",
            width: int = 0, height: int = 0, params: Optional[dict] = None) -> dict:
    """Run the cycle, photograph it on one fixed camera, and report the feet as numbers."""
    if not settings.get("cc_animate", False):
        return {"ok": False,
                "error": "Animation review is off. Turn it on in Settings → Studio engine → "
                         "Animation review."}
    bad = L._guard(project, forge=True)
    if bad:
        return bad

    e = L._entry(project)
    if not e or not e.get("target"):
        return {"ok": False, "error": "no page open for this project — POST /api/live/open first"}

    pref = L._engine_pref
    w = int(width or pref("forge_width") or 900)
    h = int(height or pref("forge_height") or 900)
    opts = json.dumps({"width": w, "height": h, "background": pref("forge_background") or "#1a1e26",
                       "ground": True, "engine": engine or "", "sky": True})
    arm_opts = json.dumps({"drive": drive, "clip": clip, "mode": mode,
                           "step": float(step or 0), "duration": float(duration or 0)})

    def build_body(code: str) -> str:
        return ("(async()=>{const __c=__forge.ctx();const {pc,THREE,engine,app,device,"
                "renderer,scene,camera,root,forge,add,clear,log,params}=__c;"
                "let __e;try{await (async()=>{%s})();}catch(err){__e=String(err&&err.stack||err);}"
                "return JSON.stringify({e:__e});})()\n//# sourceURL=studio-forge.js" % code)

    def pose_body(expr: str, t: float) -> str:
        # The `fn` driver is evaluated exactly the way the build code is — same scope, same file
        # name in a stack trace — with `t` bound. `new Function` is not available here (the page's
        # CSP forbids it), and this needs nothing it would have given.
        return ("(async()=>{const __c=__forge.ctx();const {pc,THREE,engine,app,device,"
                "renderer,scene,camera,root,forge,add,clear,log,params}=__c;const t=%r;"
                "let __e;try{await (async()=>{%s})();}catch(err){__e=String(err&&err.stack||err);}"
                "if(root&&root.updateMatrixWorld)root.updateMatrixWorld(true);"
                "return JSON.stringify({e:__e});})()\n//# sourceURL=studio-forge.js" % (float(t), expr))

    async def go():
        ws, live = await L._session(e)
        try:
            await L._forge_script(live)
            built = await live.ask("__forge.ensure(%s)" % opts)
            if not (built or {}).get("ok"):
                return {"error": (built or {}).get("error") or "the forge would not start"}

            async def rebuild():
                if not js:
                    return ""
                await live.raw("__forge.clear()")
                await live.raw("__forge.params = %s" % json.dumps(params or {}))
                got = json.loads(await live.raw(build_body(js)) or "{}")
                return (got or {}).get("e") or ""

            # Healed like forge, thumbs and aim: a tab opened before index.html had its import map
            # failed every `import './src/...'` with a bare-specifier error until it was reloaded.
            err, heal = await L.heal_build(live, e, opts, rebuild)
            if heal:
                healed["report"] = heal
            if err:
                return {"error": err, "where": "building the asset"}

            armed = await live.ask("__forge.arm(%s)" % arm_opts, depth=4) or {}
            if armed.get("error") and drive != "fn":
                return {"armed": armed, "error": armed["error"]}

            dur = float(duration or armed.get("duration") or 0) or 1.0
            ts = _times(times, frames, dur)

            names = [str(x) for x in (track or []) if str(x).strip()]
            if not names:
                parts = await live.ask("__forge.parts()", depth=4) or []
                names = _auto_feet(parts)

            # ---- pass one: measure only. No pictures, so it is cheap, and it is what gives the
            # camera something to be fixed to.
            rows, boxes = [], []
            for t in ts:
                if drive == "fn" or armed.get("how") == "fn":
                    if not fn:
                        return {"error": 'drive:"fn" needs an `fn` expression to advance the asset'}
                    got = json.loads(await live.raw(pose_body(fn, t)) or "{}")
                    if (got or {}).get("e"):
                        return {"error": got["e"], "where": "advancing to t=%.3f" % t}
                else:
                    p = await live.ask("__forge.pose(%r)" % float(t), depth=3) or {}
                    if not p.get("ok"):
                        return {"error": p.get("error") or "could not advance", "at": t}
                rows.append(await live.ask("__forge.trackAt(%s,%r)"
                                           % (json.dumps(names), float(ground)), depth=4) or {})
                boxes.append(await live.ask("__forge.bounds()", depth=3) or {})

            # ---- the union, so nothing leaves the frame and nothing re-centres
            lo = [1e9, 1e9, 1e9]
            hi = [-1e9, -1e9, -1e9]
            for b in boxes:
                c, sz = b.get("c"), b.get("size")
                if not c or not sz:
                    continue
                for k in range(3):
                    lo[k] = min(lo[k], c[k] - sz[k] / 2.0)
                    hi[k] = max(hi[k], c[k] + sz[k] / 2.0)
            if lo[0] > hi[0]:
                return {"error": "nothing was built to photograph"}
            centre = [(lo[k] + hi[k]) / 2.0 for k in range(3)]
            radius = max(1e-4, math.dist(lo, hi) / 2.0)

            if numbers:
                return {"rows": rows, "times": ts, "names": names, "armed": armed,
                        "stats": await live.ask("__forge.stats()", depth=4) or {}, "shots": []}

            # ---- pass two: the same run again, photographed. Rebuilt first so a delta driver,
            # which can only step forward, starts from where it started the first time.
            err = await rebuild()
            if err:
                return {"error": err, "where": "rebuilding for the pictures"}
            await live.ask("__forge.arm(%s)" % arm_opts, depth=4)

            shots = []
            for i, t in enumerate(ts):
                if drive == "fn" or armed.get("how") == "fn":
                    json.loads(await live.raw(pose_body(fn, t)) or "{}")
                else:
                    await live.ask("__forge.pose(%r)" % float(t), depth=3)
                if i == 0:
                    img = await live.raw("__forge.lock(%s,%r,%s,%r,%r)"
                                         % (json.dumps(view), float(margin), json.dumps(centre),
                                            float(radius), float(ground)))
                else:
                    img = await live.raw("__forge.shot()")
                shots.append(("t=%.2f" % t, img))

            return {"rows": rows, "times": ts, "names": names, "armed": armed, "shots": shots,
                    "stats": await live.ask("__forge.stats()", depth=4) or {},
                    "framed": {"centre": [round(x, 3) for x in centre], "radius": round(radius, 3)}}
        finally:
            await ws.close()

    healed: dict = {}
    L._doing(project, "animating", "%s · %s" % (label or "cycle", view))
    try:
        got = L._run(go)
    except Exception as ex:                                  # noqa: BLE001
        return {"ok": False, "error": str(ex).strip()[:1200]}
    if got.get("error"):
        out = {"ok": False, "error": got["error"]}
        for k in ("where", "armed", "at"):
            if got.get(k):
                out[k] = got[k]
        if (got.get("armed") or {}).get("looked_for"):
            out["looked_for"] = got["armed"]["looked_for"]
        if healed.get("report"):
            out["heal"] = healed["report"]
        return out

    rows, ts, names = got["rows"], got["times"], got["names"]
    tol = float((rows[0] if rows else {}).get("tolerance") or 0.01)
    report = _gait(rows, ts, names, tol)

    res: dict = {
        "ok": True,
        # FINDINGS FIRST, always. The strip shows you the run; these say which foot and when.
        "findings": report["findings"],
        "feet": report["feet"],
        "support": report["support"],
        "root": report["root"],
        "tracked": names,
        "times": ts,
        "ground": float(ground),
        "tolerance": round(tol, 4),
        "drive": (got.get("armed") or {}).get("how", ""),
        "duration": (got.get("armed") or {}).get("duration", 0),
        "stats": got.get("stats") or {},
        "framed": got.get("framed") or {},
        "contacts": [
            {"t": ts[i],
             "down": [n for n in names if ((r.get("parts") or {}).get(n, {}) or {}).get("down")],
             "gap": {n: ((r.get("parts") or {}).get(n, {}) or {}).get("gap")
                     for n in names if (r.get("parts") or {}).get(n)}}
            for i, r in enumerate(rows)
        ],
    }
    if healed.get("report"):
        res["heal"] = healed["report"]
        try:
            res["findings"][:0] = list(L._heal_finding(healed["report"]) or [])
        except Exception:                                    # noqa: BLE001
            pass
    if not names:
        res["findings"].insert(0,
            "NOTHING WAS TRACKED. No part name looked like a foot, so only the pictures are here. "
            "Pass `track:[\"footL\",\"footR\"]` and the contact numbers come with it.")
    missing = sorted({m for r in rows for m in (r.get("missing") or [])})
    if missing:
        res["missing"] = missing

    if got.get("shots"):
        labels = [lab for lab, _ in got["shots"]]
        frames_img = [L._decode_shot(img) for _, img in got["shots"]]
        frames_img = [f for f in frames_img if f is not None]
        if frames_img:
            want_q = str(quality or L._engine_pref("forge_quality")
                         or settings.get("cc_review_quality") or "normal").lower()
            budget = _review.QUALITY.get(want_q, _review.QUALITY["normal"])[0]
            sub = "%s · %d frames over %.2fs · ground %.3f" % (
                res["drive"] or "?", len(frames_img), (ts[-1] if ts else 0) or 0, float(ground))
            sheet = _review._sheet(frames_img, labels, label or "cycle", sub, budget)
            out = L._LIVE_DIR / L._slug(project) / ("cycle-%d.png" % int(time.time() * 1000))
            out.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(out)
            res["sheet"] = str(out)
    L._done(project, "animated", ", ".join(res["findings"][:1])[:120] or "no fault found")
    return res
