# -*- coding: utf-8 -*-
"""The bench, after the goblin A/B (data/ab/goblin/engine/CONTRACT.md, section C, and items 8-10).

One brief, two builders - Blender and the forge - and every fault the forge builder or the blind
graders found in the bench itself is a check here:

   1  focus:["sword"] framed the whole figure: `pick` saw meshes only, and sword was a group
   2  FACING said az=90 was the subject's right side; under glTF it is its LEFT
   3  four false UPSIDE DOWN lines on parts with no texture that could be upside down, and the
      advice "set flipY = true" for textures a glTF loader made the right way up
   4  "a vertex tint 42.9 times lighter" on every part carrying baked occlusion
   5  the camera could not choose a side; now it is solved from anchors, and parts snap to pixels
   6  267 open edges hidden inside the helmet read like the one real gap at the wrist
   7  "paler and flatter": the render's luminance spread against the reference's
   8  placement was a projected bounding box; it is the part's visible pixels now
   9  handL and handR merged into one `hand` for OFFSET
  10  a buckle 4.6 cm off the midline that nobody saw from a three-quarter view; a snap that
      would slide a centred part sideways

The offline half needs nothing. The browser half builds a synthetic knight in a real page on this
process's own headless Chrome (like live_test.py) and is skipped, not failed, with no Chrome or no
three.js build on disk. The real goblin GLBs are read, never written, and skipped when absent.

Run:  cd backend ; ./.venv/Scripts/python.exe goblin_bench_test.py
"""
import io
import json
import math
import shutil
import struct
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np                                     # noqa: E402
from PIL import Image                                  # noqa: E402

from asset_studio import live as L                     # noqa: E402
from asset_studio import live_detail as D              # noqa: E402
from asset_studio import live_forge as LF              # noqa: E402

ok = fail = skip = 0
ROOT = HERE.parent
GOBLIN = ROOT / "data" / "ab" / "goblin"


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, str(extra)[:600]))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s - %s" % (name, why))


FORGE = LF.FORGE
SRC = (HERE / "asset_studio" / "live.py").read_text(encoding="utf-8")

# ------------------------------------------------------------------------------------ 2. FACING
print("2. FACING tells the truth")
check("az=90 is the subject's LEFT side, az=270 its right",
      "az=90 at its LEFT side (+X)" in L.FACING and "az=270 at its right side (-X)" in L.FACING)
check("...and it says why: glTF faces +Z with its own left at +X",
      "faces +Z and its own left is +X" in L.FACING)
check("...and what the presets `side` and `left` show",
      "`side` (az=90) shows its left side" in L.FACING and "shows its right side" in L.FACING)
check("aim answers with that sentence", "lines.append(FACING)" in SRC)
check("the page's own comments agree (DIRS and dirOf)",
      "`side` stands at +X and shows the subject's LEFT side" in FORGE
      and "90 at its LEFT side" in FORGE and "90 at its right side" not in FORGE)
check("the comment beside the view spec agrees", "90 at its LEFT side - glTF's convention" in SRC)
check("the preset directions themselves are unchanged",
      "side: [1, 0, 0]" in FORGE and "left: [-1, 0, 0]" in FORGE)
check("...and so is the stance, which nothing asked to change",
      "var side = (k.z < b.c[2] ? 'front' : 'rear') + (k.x < b.c[0] ? '-left' : '-right');" in FORGE)

# ------------------------------------------------------------------ 3. UPSIDE DOWN, only if true
print("\n3. UPSIDE DOWN only where it can be true")
_gl = [{"material": "atlas", "slot": "map", "flipY": False, "source": "bitmap", "gltf": True}]
_cv = [{"material": "face", "slot": "map", "flipY": False, "source": "canvas", "gltf": False}]
check("a glTF loader's texture never gets 'set flipY = true'", D._texture_hint(_gl, "three") == "")
check("...a canvas the code drew still does", "flipY = true" in D._texture_hint(_cv, "three"))
_n = {"match": 0.60, "upside_down": 0.73, "mirrored": 0.5, "structure": 30.0}
_leg = D.findings([{"target": "leg", "numbers": _n, "verdict": "upside_down", "textures": []}],
                  "three", "az=338,el=38")
check("a part with no texture is not called UPSIDE DOWN",
      not any(x.startswith("UPSIDE DOWN") for x in _leg), _leg)
check("...it gets a softer DETAIL line naming the likelier cause, the reference search",
      _leg and _leg[0].startswith("DETAIL: `leg` scores 0.73 turned upside down against 0.60")
      and "reference search" in _leg[0] and "no texture at all" in _leg[0], _leg[:1])
_fist = D.findings([{"target": "fist", "numbers": _n, "verdict": "upside_down", "textures": _gl}],
                   "three", "front")
check("a part whose only texture came from a glTF file: DETAIL, and it says so",
      _fist[0].startswith("DETAIL: `fist`") and "glTF file" in _fist[0], _fist[:1])
_face = D.findings([{"target": "face", "numbers": _n, "verdict": "upside_down", "textures": _cv}],
                   "three", "front")
check("a canvas face that matches upside down is still UPSIDE DOWN, with the fix",
      _face[0].startswith("UPSIDE DOWN: `face`") and "flipY = true" in _face[0], _face[:1])
_data = [{"material": "skin", "slot": "map", "flipY": False, "source": "data", "gltf": False}]
check("texels the code wrote (a DataTexture) can be the wrong way up too",
      D.flip_doubtful({"verdict": "upside_down", "textures": _data}) == "")
check("a row whose textures were never looked up is left as it was",
      D.flip_doubtful({"verdict": "upside_down"}) == "")
_sm = D.summary([{"target": "leg", "numbers": _n, "verdict": "upside_down", "textures": []}])
check("the JSON half says doubtful_flip, not upside_down, and why",
      _sm[0]["verdict"] == "doubtful_flip" and "no texture" in _sm[0].get("why", ""), _sm)
check("the page marks what GLTFLoader made (userData.mimeType) and what sits under a loaded GLB",
      "t.userData && t.userData.mimeType" in FORGE and "obj.__forgeGlb = true;" in FORGE
      and "gltf: gl" in FORGE)

# ------------------------------------------------------------------------- 4. the tint rule
print("\n4. The vertex-tint line fires only for light painted in")
_hair = L._health_finding([{"part": "hair", "tris": 26530, "normals": True, "degenerate": 0,
                            "tint": 2.84, "tint_p5": 0.50, "tint_p95": 1.42}])
check("the hair: 0.50..1.42 of its material's colour is named, with both numbers",
      len(_hair) == 1 and "from 0.50 to 1.42" in _hair[0] and "before any lamp" in _hair[0], _hair)
_ao = L._health_finding([{"part": "head", "tris": 9000, "normals": True, "degenerate": 0,
                          "tint": 3.33, "tint_p5": 0.30, "tint_p95": 1.00}])
check("baked occlusion (0.30..1.00) is never flagged", _ao == [], _ao)
_alb = L._health_finding([{"part": "tunic", "tris": 900, "normals": True, "tint": 19.0,
                           "tint_p5": 0.05, "tint_p95": 0.95}])
check("painted albedo (all under 1) is never flagged, however wide", _alb == [], _alb)
_warm = L._health_finding([{"part": "cheek", "tris": 900, "normals": True, "tint": 1.3,
                            "tint_p5": 1.0, "tint_p95": 1.3}])
check("a tint that brightens a little but is narrow is not flagged", _warm == [], _warm)
check("a row from an older page (min and max only) keeps the old rule",
      any("vertex tint" in x for x in L._health_finding([{"part": "hair", "tris": 9, "tint": 2.84}])))
check("the page sends percentiles, reads normalised and RGBA colours, and only when the material "
      "uses them", "r2.tint_p5 = " in FORGE and "r2.tint_p95 = " in FORGE
      and "colScale(ca.array, !!ca.normalized)" in FORGE and "hm[hi].vertexColors" in FORGE)

# ------------------------------------------------------------------- 5. the camera from anchors
print("\n5. The camera, solved from anchors")
_PTS = {"apex": [0.02, 1.00, 0.02], "nose": [0.00, 0.72, 0.21], "buckle": [0.00, 0.45, 0.16],
        "earL": [0.36, 0.86, -0.02], "earR": [-0.35, 0.84, -0.03], "swordtip": [-0.10, 0.02, 0.30],
        "fistL": [0.28, 0.48, 0.12]}
_C, _RAD = np.array([0.0, 0.5, 0.02]), 0.62


def synth(names, az, el, zoom, s=610.0, t=(231.0, 244.0), noise=0.0, seed=3, lohi=None):
    rng = np.random.default_rng(seed)
    P = np.array([_PTS[n] for n in names], float)
    u, v, *_ = L._cam_project(P, _C, _RAD, az, el, zoom, 1.06, False)
    q = np.stack([u, -v], -1)
    px = s * q + np.array(t) + (rng.normal(0, noise, q.shape) if noise else 0.0)
    rows = []
    for i, n in enumerate(names):
        r = {"i": i, "found": True, "at": _PTS[n], "where": "top", "names": [n], "nodes": 1}
        if lohi and n in lohi:
            r["lo"], r["hi"] = lohi[n]
        rows.append(r)
    facts = {"ok": True, "c": list(_C), "radius": _RAD, "fov": 38, "ortho": False,
             "canvas": [640, 480], "rows": rows, "mid": 0.0, "height": 1.0}
    specs = [{"n": i + 1, "at": {"part": n, "where": "top"}, "px": [float(a), float(b)],
              "solve": True, "label": n} for i, (n, (a, b)) in enumerate(zip(names, px))]
    return facts, specs


_f, _s = synth(["apex", "nose", "buckle", "earL", "earR", "swordtip"], 338.0, 38.0, 1.3, noise=0.5)
_sol = L._solve_anchors(_f, _s, 1.06, (447, 481))
check("six anchors, 0.5 px of noise: the known camera comes back within 2 degrees",
      _sol.get("ok") and abs(((_sol["az"] - 338.0 + 180) % 360) - 180) <= 2.0
      and abs(_sol["el"] - 38.0) <= 2.0, _sol.get("view"))
check("...and within 2 px on average, with the zoom (perspective) solved too",
      _sol.get("mean_px", 99) <= 2.0 and not _sol.get("zoom_held") and abs(_sol["zoom"] - 1.3) < 0.15,
      (_sol.get("mean_px"), _sol.get("zoom")))
check("...and the answer is a view the bench renders", L._view_ok(_sol["view"]) == _sol["view"],
      _sol["view"])
_f3, _s3 = synth(["apex", "earL", "swordtip"], 20.0, 25.0, 1.0)
_sol3 = L._solve_anchors(_f3, _s3, 1.06)
check("three anchors hold the zoom at 1 and still find the camera within 2 degrees and 2 px",
      _sol3.get("ok") and _sol3["zoom_held"] and abs(_sol3["az"] - 20.0) <= 2.0
      and abs(_sol3["el"] - 25.0) <= 2.0 and _sol3["mean_px"] <= 2.0, _sol3.get("view"))
check("...and the SOLVED line says the zoom was held, and why",
      "zoom is held at 1" in L._anchor_lines(_sol3)[0], L._anchor_lines(_sol3)[0])
_fl, _sl = synth(["apex", "nose", "buckle"], 346.0, 38.0, 1.0)
_soll = L._solve_anchors(_fl, _sl, 1.06)
check("three anchors down the centre line are called poorly conditioned, plainly",
      any("poorly conditioned" in w and "one line in the picture" in w for w in _soll["warnings"]),
      _soll.get("warnings"))
_f2, _s2 = synth(["apex", "nose"], 0.0, 20.0, 1.0)
_sol2 = L._solve_anchors(_f2, _s2, 1.06)
check("fewer than three usable anchors is refused with the reason",
      not _sol2.get("ok") and "at least 3 anchors" in _sol2.get("error", ""), _sol2)
_bad = L._anchor_specs([{"at": {"part": "helmet", "where": "top"}, "px": [1, 2]}, {"px": [1, 2]},
                        {"at": [0, 1, 2]}, "x"])
check("anchor specs: good ones kept, each bad one named",
      len(_bad[0]) == 1 and len(_bad[1]) == 3 and _bad[0][0]["at"] == {"part": "helmet", "where": "top"},
      _bad)
check("a spec can leave an anchor out of the solve (solve:false) and only snap it",
      L._anchor_specs([{"at": [0, 0, 0], "px": [0, 0], "solve": False}])[0][0]["solve"] is False)

print("\n5. A snap lands on its pixel")
# The ear is displaced after the picture was taken; the camera comes from the other anchors.
_fs, _ss = synth(["apex", "nose", "buckle", "earR", "swordtip", "earL"], 338.0, 38.0, 1.3)
_ss[5]["solve"] = False
_fs["rows"][5]["at"] = [0.36 + 0.04, 0.86 - 0.03, -0.02 + 0.02]
_sols = L._solve_anchors(_fs, _ss, 1.06)
_ear = [x for x in _sols["snap"] if x["part"] == "earL"][0]
check("the moved ear is reported off its pixel", _ear["error_px"] > 5.0, _ear)
_moved = np.array(_fs["rows"][5]["at"]) + np.array(_ear["move"])
_u, _v, *_ = L._cam_project(_moved[None, :], np.array(_fs["c"]), _RAD, _sols["az"], _sols["el"],
                            _sols["zoom"], 1.06, False)
_P = np.array([[_u[0], -_v[0]]])
_all = [np.array(r["at"]) for r in _fs["rows"]]
_uu, _vv, *_ = L._cam_project(np.array(_all[:5]), np.array(_fs["c"]), _RAD, _sols["az"], _sols["el"],
                              _sols["zoom"], 1.06, False)
_sim = L._similarity(np.stack([_uu, -_vv], -1), np.array([sp["px"] for sp in _ss[:5]]))
_land = _sim[0] * _P[0] + _sim[1]
check("the snap moves it onto its pixel within 1 px",
      float(np.linalg.norm(_land - np.array(_ss[5]["px"]))) < 1.0,
      float(np.linalg.norm(_land - np.array(_ss[5]["px"]))))

print("\n10b. A snap never moves a centred part sideways")
_box = {"buckle": ([-0.03, 0.42, 0.14], [0.03, 0.48, 0.18])}
_fb, _sb = synth(["apex", "nose", "earL", "earR", "swordtip", "buckle"], 338.0, 30.0, 1.2, lohi=_box)
_sb[5]["solve"] = False
_sb[5]["px"] = [_sb[5]["px"][0] + 2.0, _sb[5]["px"][1] - 3.0]       # a pixel it can reach in y and z
_solb = L._solve_anchors(_fb, _sb, 1.06)
_bk = [x for x in _solb["snap"] if x["part"] == "buckle"][0]
check("a centred buckle is moved in y and z only (x untouched)",
      _bk.get("centred") and abs(_bk["move"][0]) < 1e-9 and "camera_off_deg" not in _bk, _bk)
_sb[5]["px"] = [_sb[5]["px"][0] + 60.0, _sb[5]["px"][1]]            # far to one side: the camera
_solc = L._solve_anchors(_fb, _sb, 1.06)
_bc = [x for x in _solc["snap"] if x["part"] == "buckle"][0]
check("...and when only a sideways move would reach its pixel, the answer is the camera",
      _bc.get("camera_off_deg") and "do not move a centred part sideways" in _bc.get("note", "")
      and _bc["move"] == [0.0, 0.0, 0.0], _bc)
check("...said as a SNAP line", any("a centred part: the camera is off by ~" in x
                                    for x in L._anchor_lines(_solc)), L._anchor_lines(_solc)[-3:])
_box2 = {"buckle": ([0.016, 0.42, 0.14], [0.076, 0.48, 0.18])}     # 4.6 cm to its left
_fo, _so = synth(["apex", "nose", "earL", "earR", "swordtip", "buckle"], 338.0, 30.0, 1.2, lohi=_box2)
_so[5]["solve"] = False
_solo = L._solve_anchors(_fo, _so, 1.06)
_bo = [x for x in _solo["snap"] if x["part"] == "buckle"][0]
check("an off-centre buckle is carried back onto the midline, never further out",
      abs(_bo["move"][0] - (-0.046)) < 1e-6, _bo)
_so[5]["solve"] = True
_solw = L._solve_anchors(_fo, _so, 1.06)
check("...and used as a camera anchor it is named: it pulls the camera toward its own mistake",
      any(w.startswith("ANCHORS: `buckle` is a centred part sitting 4.6 cm left of the midline")
          for w in _solw.get("warnings") or []), _solw.get("warnings"))

# ---------------------------------------------------------------------- 6. holes you can see
print("\n6. Visible holes, not hidden ones")
_h = L._hole_finding({"rows": [{"part": "bracerR", "visible": 1, "hidden": 3,
                                "holes": [{"across": 0.031, "at": [0.21, 0.52, 0.1]}]}]})
check("the HOLE line reads as the contract wrote it",
      _h and _h[0].startswith("HOLE: `bracerR` has an open edge loop 3.1 cm across that can be seen "
                              "from outside"), _h)
check("a loop that ends inside another part is not a line", L._hole_finding(
    {"rows": [{"part": "helmetDome", "open_edges": 267, "hidden": 3, "visible": 0, "holes": []}]}) == [])
_many = L._hole_finding({"rows": [{"part": "p%d" % i, "holes": [{"across": 0.02}]} for i in range(7)]})
check("more than four is summed up, not listed", len(_many) == 5 and "3 more open loops" in _many[-1], _many)
check("the page asks the three questions: hidden, plugged, and a seam of one surface cut in two",
      all(("state = '%s'" % s) in FORGE for s in ("hidden", "plugged", "seam", "pinhole", "shell", "hole")))

# ------------------------------------------------------------------------------ 7. contrast
print("\n7. Contrast against the reference")
# A blue plate, so no grey of the subject's own ramp is ever the backdrop's colour.
_ref = Image.new("RGB", (200, 300), (40, 60, 200))
_px = np.asarray(_ref).copy()
_px[40:260, 50:150] = np.linspace(20, 215, 100).astype(np.uint8)[None, :, None]
_refp = Path(tempfile.mkdtemp(prefix="goblin-bench-")) / "ref.png"
Image.fromarray(_px).save(_refp)
_ren = Image.new("RGB", (240, 320), (26, 30, 38))
_rp = np.asarray(_ren).copy()
_rp[50:270, 60:170] = np.linspace(95, 220, 110).astype(np.uint8)[None, :, None]
_con = L._contrast(Image.fromarray(_rp), str(_refp))
_cl = L._contrast_finding(_con)
check("a pale, flat render against a deep one: the spreads are measured on the subject alone",
      _con and _con["render"][0] > 90 and _con["reference"][0] < 40, _con)
check("...and said in the contract's words",
      _cl.startswith("CONTRAST: the render spans %d..%d where the reference spans %d..%d - the darks "
                     "are missing (cavities, creases, under the brim)"
                     % (round(_con["render"][0]), round(_con["render"][2]),
                        round(_con["reference"][0]), round(_con["reference"][2]))), _cl)
check("a render as deep as its reference says nothing",
      L._contrast_finding({"render": [40, 120, 200], "reference": [31, 118, 205]}) == "")
check("a spread under 70% of the reference's is named even when the darks are there",
      "squeezed" in L._contrast_finding({"render": [40, 90, 120], "reference": [31, 118, 205]})
      or "lights are missing" in L._contrast_finding({"render": [40, 90, 120], "reference": [31, 118, 205]}))

# ------------------------------------------------------------------------ 9. twins apart
print("\n9. One hand is not both hands")
check("sided names are read in every common spelling",
      [D.side_key(n) for n in ("handL", "hand_r", "Hand.Left", "leftHand", "L_hand", "earR")]
      == [("hand", "L"), ("hand", "R"), ("hand", "L"), ("hand", "L"), ("hand", "L"), ("ear", "R")])
check("...and not where it would be a guess", D.side_key("girl") is None and D.side_key("shieldHandle") is None)
check("`hand` reaching handL and handR (and the shield's handle) becomes the two hands, exactly",
      D.split_twins(["face", "hand"], {"hand": ["handR", "handL", "shieldHandle"]})
      == ["face", "=handL", "=handR"])
check("hair with numbered strands stays one target; two numbered copies split",
      D.split_twins(["hair"], {"hair": ["hair", "hairStrand1", "hairStrand2", "hairStrand3"]}) == ["hair"]
      and D.split_twins(["arm"], {"arm": ["arm.001", "arm.002"]}) == ["=arm.001", "=arm.002"])
check("the page takes '=name' as exactly that name", "if (q.charAt(0) === '=') return exactNamed(" in FORGE)
check("the detail pass splits before it runs, and shows the name without the mark",
      "_ld.split_twins(targets" in SRC and D.label_of("=handL") == "handL")

# -------------------------------------------------------------------- 8. placement as pixels
print("\n8. Placement is the part's pixels")
check("the page renders an id pass with no multisampling and reads it back exactly",
      "var idPixels = function ()" in FORGE and "new T.WebGLRenderTarget(W, H" in FORGE
      and "readRenderTargetPixels" in FORGE and "T.LinearSRGBColorSpace" in FORGE)
check("every old field keeps its meaning; the pixels are ADDED beside them",
      "row.px_box = p.box; row.px_at = p.at;" in FORGE and "box: [r3(x0), r3(y0), r3(x1), r3(y1)]," in FORGE)
_old = {"view": "front", "parts": [{"name": "clasp", "at": [0.50, 0.60], "tris": 240}]}
_new = {"view": "front", "parts": [{"name": "clasp", "at": [0.50, 0.60], "tris": 240,
                                    "px_at": [0.52, 0.66], "px_box": [0.47, 0.6, 0.57, 0.72], "px": 300}]}
check("the PLACED line uses the visible pixels when they are there",
      "clasp at (0.52, 0.66)" in L._placement_finding(_new) and "VISIBLE pixels" in L._placement_finding(_new))
check("...and reads exactly as before when they are not",
      "clasp at (0.50, 0.60)" in L._placement_finding(_old) and "VISIBLE" not in L._placement_finding(_old))
check("drift still compares the box centres it always compared",
      L._drift_finding(_new, {"view": "front", "parts": [{"name": "clasp", "at": [0.40, 0.60]}]}).startswith(
          "SINCE YOUR LAST SHOT"))

print("\nThe new checks sit behind one switch")
_pref = L._engine_pref
try:
    L._engine_pref = lambda k, d=None: False if k == "forge_checks" else _pref(k, d)
    check("forge_checks off: HOLE, CONTRAST and SYMMETRY are not asked for", L._checks_on() is False)
finally:
    L._engine_pref = _pref
check("...on unless it is switched off", L._checks_on() is True or _pref("forge_checks") is False)
check("every page call and every contrast is behind it",
      SRC.count('(await live.ask("__forge.holes()", depth=6) or {}) if _checks_on() else {}') == 2
      and SRC.count('(await live.ask("__forge.symmetry()", depth=5) or {}) if _checks_on() else {}') == 3
      and SRC.count("_checks_on() else _contrast(") + SRC.count(") if _checks_on() else {}\n") >= 3)

# -------------------------------------------------------------------- 10a. symmetry, from files
print("\n10a. Symmetry, from the boxes alone")
_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def glb_symmetry(path):
    """What F.symmetry answers for a GLB - exact world vertex boxes, surface areas and the groups
    each part sits in - read from the file itself, with no browser."""
    data = Path(path).read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    j = json.loads(data[20:20 + n])
    b0 = 20 + n + 8

    def acc(i):
        a = j["accessors"][i]
        bv = j["bufferViews"][a["bufferView"]]
        dt, k = np.dtype(_CT[a["componentType"]]), _NC[a["type"]]
        off = b0 + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        st = bv.get("byteStride") or dt.itemsize * k
        raw = np.frombuffer(data, np.uint8, st * (a["count"] - 1) + dt.itemsize * k, off)
        out = np.lib.stride_tricks.as_strided(raw, (a["count"], dt.itemsize * k), (st, 1)).copy()
        return out.view(dt).reshape(a["count"], k)

    def local(nd):
        if "matrix" in nd:
            return np.array(nd["matrix"], float).reshape(4, 4).T
        x, y, z, w = nd.get("rotation", [0, 0, 0, 1])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.eye(4)
        M[:3, :3] = R * np.array(nd.get("scale", [1, 1, 1]), float)[None, :]
        M[:3, 3] = nd.get("translation", [0, 0, 0])
        return M

    parts, lo, hi = {}, np.full(3, np.inf), np.full(3, -np.inf)

    def walk(i, W, ups):
        nd = j["nodes"][i]
        M = W @ local(nd)
        for pr in (j["meshes"][nd["mesh"]]["primitives"] if "mesh" in nd else []):
            P = acc(pr["attributes"]["POSITION"]).astype(float) @ M[:3, :3].T + M[:3, 3]
            ix = acc(pr["indices"]).ravel().astype(np.int64) if "indices" in pr else np.arange(len(P))
            T = P[ix.reshape(-1, 3)]
            ar = float(np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1).sum() / 2)
            nm = nd.get("name") or "(unnamed)"
            e = parts.setdefault(nm, {"name": nm, "lo": P.min(0), "hi": P.max(0), "area": 0.0,
                                      "under": list(ups)})
            e["lo"], e["hi"] = np.minimum(e["lo"], P.min(0)), np.maximum(e["hi"], P.max(0))
            e["area"] += ar
            lo[:], hi[:] = np.minimum(lo, P.min(0)), np.maximum(hi, P.max(0))
        for c in nd.get("children", []):
            walk(c, M, tuple(ups) + ((nd["name"],) if nd.get("name") else ()))

    for r in j["scenes"][j.get("scene", 0)]["nodes"]:
        walk(r, np.eye(4), ())
    return {"parts": [{"name": p["name"], "lo": [float(v) for v in p["lo"]],
                       "hi": [float(v) for v in p["hi"]], "area": p["area"], "under": p["under"]}
                      for p in parts.values()],
            "box": {"lo": [float(v) for v in lo], "hi": [float(v) for v in hi]}}


for _who, _want in (("forge", ("`tassets` sits 5.8 cm left of the middle (x = +0.058)",
                               "`buckle`, a centred part, sits 4.6 cm left of the middle (x = +0.046)")),
                    ("blender", ("`buckle`, a centred part, sits 6.6 cm left of the middle (x = +0.066)",))):
    _glb = GOBLIN / _who / "goblin.glb"
    if not _glb.is_file():
        skipped("the %s goblin's SYMMETRY lines" % _who, "no GLB at %s" % _glb)
        continue
    _lines = L._symmetry_finding(glb_symmetry(_glb))
    for _w in _want:
        check("the %s goblin: SYMMETRY: %s" % (_who, _w.split(" sits")[0]),
              any(x.startswith("SYMMETRY: " + _w) for x in _lines), _lines)
    check("the %s goblin: its held sword is not a midline part" % _who,
          not any("`blade`" in x or "`sword`" in x for x in _lines), _lines)


def _part(name, lo, hi, area=1.0, under=()):
    return {"name": name, "lo": list(lo), "hi": list(hi), "area": area, "under": list(under)}


_SYM = {"box": {"lo": [-0.4, 0.0, -0.2], "hi": [0.4, 1.0, 0.3]}, "parts": [
    _part("torso", (-0.25, 0.3, -0.15), (0.25, 0.9, 0.15), 2.0),
    _part("head", (-0.18, 0.94, -0.16), (0.18, 1.0, 0.2), 1.2),
    _part("buckle", (-0.03, 0.42, 0.14), (0.03, 0.48, 0.18), 0.02),
    _part("earL", (0.2, 0.9, -0.02), (0.36, 0.98, 0.02), 0.1),
    _part("earR", (-0.36, 0.9, -0.02), (-0.2, 0.98, 0.02), 0.1),
    _part("handL", (0.3, 0.4, 0.0), (0.4, 0.5, 0.1), 0.05),
    _part("handR", (-0.4, 0.4, 0.0), (-0.3, 0.5, 0.1), 0.05),
    _part("blade", (-0.3, 0.0, 0.1), (0.1, 0.6, 0.12), 0.3, ("sword",))]}
check("a symmetric figure has no SYMMETRY line", L._symmetry_finding(_SYM) == [],
      L._symmetry_finding(_SYM))
_asym = json.loads(json.dumps(_SYM))
_asym["parts"][3]["area"] = 0.1 * 1.25 ** 2
check("twins of different sizes are named, by surface (a posed limb is not a size)",
      "SYMMETRY: `earL` is 25% larger than `earR`" in " ".join(L._symmetry_finding(_asym)),
      L._symmetry_finding(_asym))
_prop = {"box": _SYM["box"], "parts": [p for p in _SYM["parts"] if not p["name"].startswith(("ear", "hand"))]
         + [_part("buckle2", (0.02, 0.4, 0.1), (0.1, 0.5, 0.2))]}
check("a subject with no left/right pair has no midline to keep", L._symmetry_finding(_prop) == [])

print("\n11. A part that floats")
_g = L._gap_finding({"rows": [{"parts": ["pauldronL"], "group": "goblin", "gap": 0.018},
                              {"parts": ["blade", "grip"], "group": "sword", "gap": 0.04}]})
check("GAP reads as asked", _g[0] == "GAP: `pauldronL` floats 1.8 cm off every other part - from behind it "
                                    "reads as a gap", _g)
check("...and an unheld prop is named as one, by its group", _g[1].startswith("GAP: `sword` (blade, grip) floats "
                                                                            "4.0 cm") and "hand does not touch" in _g[1], _g)
check("nothing floating, nothing said", L._gap_finding({"rows": []}) == [] and L._gap_finding(None) == [])

# ============================================================================ the browser half
KNIGHT = r"""
export function build(T) {
  const root = new T.Group(); root.name = 'knight';
  const mat = (c, vc) => new T.MeshStandardMaterial({ color: c, roughness: 0.6, vertexColors: !!vc });
  const mesh = (name, g, c, p, vc) => {
    const m = new T.Mesh(g, mat(c, vc)); m.name = name;
    if (p) m.position.set(p[0], p[1], p[2]);
    root.add(m); return m;
  };
  mesh('torso', new T.BoxGeometry(0.5, 0.6, 0.3), 0x8899aa, [0, 0.6, 0]);
  mesh('head', new T.SphereGeometry(0.18, 32, 16), 0x77aa55, [0, 1.12, 0.02]);
  const nose = mesh('nose', new T.ConeGeometry(0.04, 0.12, 12), 0xdd9966, [0, 1.1, 0.24]);
  nose.rotation.x = Math.PI / 2;
  mesh('neck', new T.CylinderGeometry(0.07, 0.07, 0.36, 16, 1, true), 0x77aa55, [0, 0.93, 0]);
  mesh('armL', new T.CylinderGeometry(0.05, 0.05, 0.5, 16), 0x77aa55, [0.34, 0.62, 0]);
  mesh('sleeveL', new T.CylinderGeometry(0.08, 0.08, 0.2, 16, 1, true), 0xaa3333, [0.34, 0.72, 0]);
  const bg = new T.BoxGeometry(0.2, 0.2, 0.2), keep = [];
  for (const gr of bg.groups) {
    if (gr.materialIndex === 2) continue;
    for (let i = gr.start; i < gr.start + gr.count; i++) keep.push(bg.index.array[i]);
  }
  bg.setIndex(keep); bg.clearGroups();
  mesh('crate', bg, 0x996633, [0.5, 0.1, 0.35]);
  mesh('cape', new T.PlaneGeometry(0.5, 0.7, 4, 4), 0x223388, [0, 0.62, -0.2]);
  const sword = new T.Group(); sword.name = 'sword'; sword.position.set(-0.4, 0.45, 0.15);
  const blade = new T.Mesh(new T.BoxGeometry(0.04, 0.6, 0.01), mat(0xcccccc)); blade.name = 'blade';
  blade.position.y = -0.2; sword.add(blade);
  const guard = new T.Mesh(new T.BoxGeometry(0.16, 0.03, 0.03), mat(0x886633)); guard.name = 'guard';
  guard.position.y = 0.11; sword.add(guard);
  root.add(sword);
  // A group `crest` (a plume inside) beside a MESH `crestPin`: `crest` must keep finding the pin.
  const crest = new T.Group(); crest.name = 'crest'; crest.position.set(0, 1.36, -0.02);
  const plume = new T.Mesh(new T.BoxGeometry(0.02, 0.08, 0.14), mat(0xcc2222)); plume.name = 'plume';
  crest.add(plume); root.add(crest);
  mesh('crestPin', new T.BoxGeometry(0.02, 0.02, 0.02), 0xdddd22, [0, 1.3, 0.1]);
  const hg = new T.SphereGeometry(0.19, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2);
  const hn = hg.attributes.position.count, hc = new Float32Array(hn * 3);
  for (let i = 0; i < hn; i++) { const k = 0.5 + 0.92 * (i % 23) / 22; hc[3 * i] = hc[3 * i + 1] = hc[3 * i + 2] = k; }
  hg.setAttribute('color', new T.BufferAttribute(hc, 3));
  mesh('hair', hg, 0x7a4a2a, [0, 1.12, 0.02], true);
  const bb = new T.BoxGeometry(0.12, 0.1, 0.2, 4, 4, 4);
  const bn = bb.attributes.position.count, bc = new Uint8Array(bn * 4);
  for (let i = 0; i < bn; i++) {
    const k = i === 7 ? 5 : Math.round(255 * (0.3 + 0.7 * (i % 17) / 16));
    bc[4 * i] = bc[4 * i + 1] = bc[4 * i + 2] = k; bc[4 * i + 3] = 255;
  }
  bb.setAttribute('color', new T.BufferAttribute(bc, 4, true));
  mesh('bootL', bb, 0x553322, [0.12, 0.05, 0.03], true);
  const c = document.createElement('canvas'); c.width = c.height = 8;
  const tg = new T.CanvasTexture(c); tg.userData.mimeType = 'image/png'; tg.flipY = false;
  mesh('badge', new T.BoxGeometry(0.04, 0.04, 0.01), 0xffffff, [0.1, 0.8, 0.16]).material.map = tg;
  const tc = new T.CanvasTexture(document.createElement('canvas')); tc.flipY = false;
  mesh('decal', new T.BoxGeometry(0.04, 0.04, 0.01), 0xffffff, [-0.1, 0.8, 0.16]).material.map = tc;
  return root;
}
export function buildSymmetric(T, buckleX) {
  const root = new T.Group(); root.name = 'twin';
  const add = (name, g, p) => { const m = new T.Mesh(g, new T.MeshStandardMaterial()); m.name = name;
                                m.position.set(p[0], p[1], p[2]); root.add(m); return m; };
  add('torso', new T.BoxGeometry(0.5, 0.6, 0.3), [0, 0.6, 0]);
  add('head', new T.SphereGeometry(0.18, 24, 12), [0, 1.06, 0]);          // sits on the torso
  add('earL', new T.ConeGeometry(0.05, 0.2, 8), [0.2, 1.1, 0]);
  add('earR', new T.ConeGeometry(0.05, 0.2, 8), [-0.2, 1.1, 0]);
  add('legL', new T.CylinderGeometry(0.07, 0.07, 0.3, 12), [0.12, 0.15, 0]);
  add('legR', new T.CylinderGeometry(0.07, 0.07, 0.3, 12), [-0.12, 0.15, 0]);
  add('buckle', new T.BoxGeometry(0.06, 0.05, 0.03), [buckleX || 0, 0.5, 0.16]);
  add('handL', new T.BoxGeometry(0.08, 0.08, 0.08), [0.29, 0.45, 0.05]);
  add('handR', new T.BoxGeometry(0.08, 0.08, 0.08), [-0.29, 0.45, 0.05]);
  // A sword held in the right hand: its grip runs through the fist, so the hand joins it to the body.
  const sword = new T.Group(); sword.name = 'sword'; sword.position.set(-0.29, 0.45, 0.05); root.add(sword);
  const grip = new T.Mesh(new T.CylinderGeometry(0.015, 0.015, 0.14, 8), new T.MeshStandardMaterial());
  grip.name = 'grip'; sword.add(grip);
  const blade = new T.Mesh(new T.BoxGeometry(0.04, 0.5, 0.01), new T.MeshStandardMaterial());
  blade.name = 'blade'; blade.position.set(0, -0.32, 0); sword.add(blade);
  return root;
}
// A face painted on a canvas: right way up, or upside down (flipY:false puts the canvas's top row
// at the bottom of the plane in three.js) - the roblox-boy fault the UPSIDE DOWN line exists for.
export function kid(T, flip) {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d');
  x.fillStyle = '#fdd9b3'; x.fillRect(0, 0, 256, 256);
  x.fillStyle = '#000000'; x.fillRect(70, 45, 30, 64); x.fillRect(156, 45, 30, 64); x.fillRect(58, 150, 140, 62);
  x.fillStyle = '#ffffff'; x.fillRect(66, 150, 124, 14);
  x.fillStyle = '#f21833'; x.fillRect(78, 182, 100, 26);
  const t = new T.CanvasTexture(c); t.flipY = !flip;
  if (T.SRGBColorSpace) t.colorSpace = T.SRGBColorSpace;
  const root = new T.Group(); root.name = 'kid';
  const add = (name, g, m, p) => { const o = new T.Mesh(g, m); o.name = name; o.position.set(p[0], p[1], p[2]); root.add(o); return o; };
  add('head', new T.BoxGeometry(0.5, 0.5, 0.5), new T.MeshStandardMaterial({ color: 0xfdd9b3 }), [0, 1.25, 0]);
  add('face', new T.PlaneGeometry(0.44, 0.44), new T.MeshBasicMaterial({ map: t }), [0, 1.25, 0.2505]);
  add('torso', new T.BoxGeometry(0.6, 0.8, 0.35), new T.MeshStandardMaterial({ color: 0x3355aa }), [0, 0.6, 0]);
  return root;
}
"""
INDEX = """<!doctype html><html><head><meta charset="utf-8"><title>goblin bench test</title></head>
<body><script type="module">
import * as THREE from '/three.module.js';
const r = new THREE.WebGLRenderer(); r.setSize(64, 64); document.body.appendChild(r.domElement);
r.render(new THREE.Scene(), new THREE.PerspectiveCamera());
</script></body></html>
"""


class _Say:
    """The settings, with the forge and the live link answered ON. Nothing is written."""

    def __init__(self, real, **over):
        self._real, self._over = real, over

    def get(self, k, d=None):
        return self._over[k] if k in self._over else self._real.get(k, d)

    def __getattr__(self, n):
        return getattr(self._real, n)


def _three_build():
    for d in (ROOT / "frontend" / "node_modules" / "three" / "build", GOBLIN / "judge"):
        if (d / "three.module.js").is_file() and (d / "three.core.js").is_file():
            return d
    return None


print("\nThe page, in a real browser")
_tb = _three_build()
avail, why = L.available()
if not avail:
    skipped("the bench's page-side changes, on a real page", why)
elif _tb is None:
    skipped("the bench's page-side changes, on a real page", "no three.js build on disk")
else:
    _real = L.settings
    L.settings = _Say(_real, cc_forge=True, cc_live=True)
    PROJ = Path(tempfile.mkdtemp(prefix="goblin-bench-page-"))
    try:
        for f in ("three.module.js", "three.core.js"):
            shutil.copyfile(_tb / f, PROJ / f)
        (PROJ / "index.html").write_text(INDEX, encoding="utf-8")
        (PROJ / "knight.js").write_text(KNIGHT, encoding="utf-8")
        P = str(PROJ)
        BUILD = "const m = await import('/knight.js?v=' + Date.now()); add(m.build(THREE)); return 1;"
        TRUE = "az=338,el=30,zoom=1.2"
        r = L.forge(P, BUILD, views=[TRUE], width=640, height=480, numbers=True, ref="none",
                    detail=False, label="goblin bench test", tags=["test"])
        check("the knight builds on the page's own three", r.get("ok") and r.get("engine") == "three",
              r.get("error"))
        ev = lambda js: L.evaluate(P, js).get("value")         # noqa: E731
        F = r.get("findings") or []

        # 1 ------------------------------------------------------------------------------------
        check("1. pick() takes a group when no mesh matches: `sword` is its blade and its guard",
              ev("__forge.pick('sword')") == 2)
        check("1. ...and a word that finds a mesh finds exactly that mesh, as it always did",
              ev("[__forge.pick('crest'), __forge.pick('=crest'), __forge.pick('blade'), __forge.pick('arm')]")
              == [1, 1, 1, 1])
        got = ev("(__forge.view('front',1.06,0,'sword'), {f: __forge.stats().framed, "
                 "s: __forge.boxOf('sword'), t: __forge.boxOf('torso')})")
        check("1. focus:[\"sword\"] frames the sword - the group, not the figure",
              got["f"] == "focus" and got["s"]["box"][3] - got["s"]["box"][1] > 0.7
              and (got["t"]["box"][2] > 1.0 or got["t"]["box"][0] > 0.9 or got["t"]["box"][1] < 0.0), got)
        check("1. a name nothing has is still said", ev(
            "(__forge.view('front',1.06,0,'nosuchpart'), __forge.stats().framed)") == "no-such-part")

        # 3 ------------------------------------------------------------------------------------
        facts = {x: ev("__forge.texFacts(%s)" % json.dumps(x)) for x in ("badge", "decal")}
        check("3. a texture GLTFLoader made (userData.mimeType) is marked gltf on the page",
              facts["badge"] and facts["badge"][0]["gltf"] is True, facts["badge"])
        check("3. ...a canvas the code made is not", facts["decal"] and facts["decal"][0]["gltf"] is False,
              facts["decal"])

        # 4 ------------------------------------------------------------------------------------
        hl = ev("__forge.health()")
        hair = [x for x in hl if x["part"] == "hair"][0]
        boot = [x for x in hl if x["part"] == "bootL"][0]
        check("4. the page reads the hair's float colours as 0.5..1.42 (percentiles)",
              0.5 <= hair["tint_p5"] < 0.6 and 1.3 < hair["tint_p95"] <= 1.42, hair)
        check("4. ...and the boot's byte colours as occlusion that never passes 1.0",
              0.29 <= boot["tint_p5"] <= 0.35 and boot["tint_p95"] <= 1.0, boot)
        check("4. the hair is named and the boot is not",
              any(x.startswith("MESH: `hair` carries a vertex tint") for x in F)
              and not any(x.startswith("MESH: `bootL`") for x in F), [x for x in F if x.startswith("MESH")])

        # 6 ------------------------------------------------------------------------------------
        hr = {x["part"]: x for x in (r.get("holes") or {}).get("parts") or []}
        check("6. the lidless crate is the one visible HOLE", [x for x in F if x.startswith("HOLE")]
              and all("`crate`" in x for x in F if x.startswith("HOLE"))
              and any("28.3 cm across" in x for x in F), [x for x in F if x.startswith("HOLE")])
        hs = ev("__forge.holes()")
        by = {x["part"]: x for x in hs["rows"]}
        check("6. a tube ending inside torso and head is hidden at both ends", by["neck"]["hidden"] == 2, by.get("neck"))
        check("6. a sleeve round an arm, and hair over a head, are plugged",
              by["sleeveL"]["plugged"] == 2 and by["hair"]["plugged"] == 1, (by.get("sleeveL"), by.get("hair")))
        check("6. a cape is the rim of an open shell", by["cape"]["shell"] == 1, by.get("cape"))

        # 11 -----------------------------------------------------------------------------------
        gp = ev("__forge.gaps()")
        floating = sorted(p for r0 in gp["rows"] for p in r0["parts"])
        check("11. what floats is what the knight has off its body: crate, boot, cape, the unheld sword",
              floating == ["blade", "bootL", "cape", "crate", "guard"], floating)
        check("11. ...and a neck pushed INTO the torso joins it, though no vertex is near its face",
              not any("neck" in r0["parts"] for r0 in gp["rows"]), gp["rows"])
        check("11. said as GAP lines, a centimetre-sure", any(x.startswith("GAP: `cape` floats 5.0 cm off every "
                                                                        "other part") for x in F), [x for x in F if x.startswith("GAP")])

        # 8 ------------------------------------------------------------------------------------
        wh = {x["name"]: x for x in ev("(__forge.view(%s,1.06), __forge.where())" % json.dumps(TRUE))}
        check("8. placement carries the visible pixels beside the projected box",
              wh["head"]["px"] > 100 and len(wh["head"]["px_at"]) == 2 and len(wh["head"]["box"]) == 4,
              wh.get("head"))
        check("8. ...a head half under its hair is placed by what shows, not by its box",
              wh["head"]["px_box"][1] > wh["head"]["box"][1] + 0.05, (wh["head"]["px_box"], wh["head"]["box"]))
        check("8. ...and an unhidden box's pixels land on its projected box within a pixel or two",
              all(abs(a - b) < 0.006 for a, b in zip(wh["torso"]["px_box"], wh["torso"]["box"])),
              (wh["torso"]["px_box"], wh["torso"]["box"]))

        # 9 ------------------------------------------------------------------------------------
        check("9. the page lists which names a word reaches, and takes '=name' exactly",
              ev("__forge.matches(['arm'])") == {"arm": ["armL"]}
              and ev("[__forge.pick('=armL'), __forge.pick('=sword'), __forge.pick('=nope')]") == [1, 2, 0])

        # 5 ------------------------------------------------------------------------------------
        lk = L.look(P, views=[TRUE], frames=True, width=640, height=480, ref="none", detail=False)
        frame = [f for f in lk.get("frames") or [] if "REFERENCE" not in f][0]
        CROP, K = (60, 20, 600, 470), 0.8
        im = Image.open(frame).convert("RGB").crop(CROP)
        im.resize((round(im.width * K), round(im.height * K)), Image.LANCZOS).save(PROJ / "ref.png")
        A = [("head", "top"), ("nose", "front"), ("sword", "bottom"), ("armL", "bottom"),
             ("crate", "centre"), ("torso", "left")]
        fa = ev("__forge.anchorFacts(%s)" % json.dumps([{"at": {"part": p, "where": w}} for p, w in A]))
        pj = ev("(__forge.view(%s,1.06), __forge.project(%s))" % (json.dumps(TRUE),
                                                                   json.dumps([x["at"] for x in fa["rows"]])))
        anchors = [{"at": {"part": p, "where": w}, "px": [(q[0] - CROP[0]) * K, (q[1] - CROP[1]) * K]}
                   for (p, w), q in zip(A, pj["px"])]
        a = L.aim(P, "", anchors=anchors, ref=str(PROJ / "ref.png"))
        sv = a.get("solved") or {}
        check("5. on the page: a known camera comes back from six anchors within 2 degrees",
              a.get("ok") and abs(sv.get("az", 0) - 338.0) <= 2.0 and abs(sv.get("el", 0) - 30.0) <= 2.0,
              a.get("error") or sv)
        check("5. ...within 2 px, and the page's own camera agrees with the solve's model",
              sv.get("mean_px", 99) <= 2.0 and (sv.get("check_px") is not None and sv["check_px"] < 0.05), sv)
        check("5. ...and the answer leads with SOLVED and the true FACING",
              (a.get("findings") or [""])[0].startswith("SOLVED: the reference was taken from")
              and any(x.startswith("FACING: az=0") for x in a.get("findings") or []), (a.get("findings") or [])[:2])
        # The crate is knocked out of place; the camera comes from the rest; the snap puts it back.
        for x in anchors:
            if x["at"]["part"] == "crate":
                x["solve"] = False
        L.look(P, edits=[{"target": "crate", "move": [0.06, 0.05, -0.04]}], views=["front"], detail=False, ref="none")
        a2 = L.aim(P, "", anchors=anchors)
        s2 = [x for x in a2.get("snap") or [] if x["part"] == "crate"][0]
        check("5. the knocked crate is reported off its pixel, with an edit", s2["error_px"] > 5 and s2.get("edit"), s2)
        L.look(P, edits=[s2["edit"]], views=["front"], detail=False, ref="none")
        a3 = L.aim(P, "", anchors=anchors)
        s3 = [x for x in a3.get("snap") or [] if x["part"] == "crate"][0]
        check("5. ...and after the edit it lands within 1 px of its pixel", s3["error_px"] < 1.0, s3)
        via = L.batch(P, [{"op": "aim", "anchors": anchors}])
        check("5. anchors reach aim through /batch today (the routes need one field each)",
              via.get("ok") and (via["results"][0].get("solved") or {}).get("view"), via.get("results", [{}])[0].get("error"))

        # 10a ----------------------------------------------------------------------------------
        SYM = "const m = await import('/knight.js?v=' + Date.now()); add(m.buildSymmetric(THREE, %s)); return 1;"
        s0 = L.forge(P, SYM % "0", views=["front"], width=480, height=480, numbers=True, ref="none",
                     detail=False, label="goblin bench sym")
        check("10a. a symmetric figure: no SYMMETRY line on the page",
              s0.get("ok") and not any(x.startswith("SYMMETRY") for x in s0.get("findings") or []),
              [x for x in s0.get("findings") or [] if x.startswith("SYMMETRY")])
        s1 = L.forge(P, SYM % "0.05", views=["front"], width=480, height=480, numbers=True, ref="none",
                     detail=False, label="goblin bench sym")
        check("10a. its buckle 5 cm to the left: the page says so, in the subject's own terms",
              any(x.startswith("SYMMETRY: `buckle`, a centred part, sits 5.0 cm left of the middle")
                  for x in s1.get("findings") or []), [x for x in s1.get("findings") or [] if x.startswith("SYMM")])
        _pref2 = L._engine_pref
        try:
            L._engine_pref = lambda k, d=None: False if k == "forge_checks" else _pref2(k, d)
            s2 = L.forge(P, SYM % "0.05", views=["front"], width=480, height=480, numbers=True, ref="none",
                         detail=False, label="goblin bench sym")
        finally:
            L._engine_pref = _pref2
        check("10a. forge_checks off: the same build says nothing new",
              s2.get("ok") and not any(x.startswith(("SYMMETRY", "HOLE", "CONTRAST", "GAP"))
                                       for x in s2.get("findings") or [])
              and "holes" not in s2, [x for x in s2.get("findings") or [] if x.startswith(("SYMM", "HOLE", "GAP"))])

        # 11 -----------------------------------------------------------------------------------
        check("11. a figure whose sword is in its hand has no GAP line",
              not any(x.startswith("GAP") for x in s1.get("findings") or []),
              [x for x in s1.get("findings") or [] if x.startswith("GAP")])
        g1 = L.look(P, edits=[{"target": "sword", "move": [0, 0, 0.12]}], views=["front"], detail=False, ref="none")
        check("11. ...moved out of the hand, it is a held prop the hand does not touch",
              any(x.startswith("GAP: `sword` (") and "a held prop the hand does not touch" in x
                  for x in g1.get("findings") or []), [x for x in g1.get("findings") or [] if x.startswith("GAP")])

        # 3 and 4, on the true case they were made for --------------------------------------------
        KID = "const m = await import('/knight.js?v=' + Date.now()); add(m.kid(THREE, %s)); return 1;"
        L.forge(P, KID % "false", views=["front"], width=480, height=560, ref="none", detail=False,
                label="goblin bench kid")
        lk2 = L.look(P, views=["front"], frames=True, ref="none", detail=False)
        shutil.copyfile([f for f in lk2.get("frames") or [] if "REFERENCE" not in f][0], PROJ / "kid.png")
        k2 = L.forge(P, KID % "true", views=["front"], width=480, height=560, ref=str(PROJ / "kid.png"),
                     detail=["face"], tags=["character"], numbers=True, label="goblin bench kid")
        up = [x for x in k2.get("findings") or [] if x.startswith("UPSIDE DOWN: `face`")]
        check("3. a face really painted upside down on a canvas is still UPSIDE DOWN, with the fix",
              up and "flipY:false" in up[0] and "Set flipY = true" in up[0],
              [x for x in k2.get("findings") or [] if x.startswith(("UPSIDE", "DETAIL"))])
    finally:
        try:
            L.close(str(PROJ))
        except Exception:
            pass
        try:
            from asset_studio import review
            review.shutdown()                # the Chrome this process started, by its own pid
        except Exception:
            pass
        L.settings = _real
        shutil.rmtree(PROJ, ignore_errors=True)
        shutil.rmtree(L._LIVE_DIR / L._slug(str(PROJ)), ignore_errors=True)

shutil.rmtree(_refp.parent, ignore_errors=True)
print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)
