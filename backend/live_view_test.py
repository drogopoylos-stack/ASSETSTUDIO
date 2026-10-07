"""The agent's live view — the picture of every change, the camera on /shot, /watch, and `stale`.

Five halves, none of which needs the Studio running.

The MATHS: the camera that frames a box (the contract's d = r / sin(fov/2) x 1.15, angles relative to
the game camera's heading, the Studio camera's own near plane), checked by projecting the box's eight
corners through that camera with a plain pinhole — not by comparing the function with itself.

The PICTURES: a sheet of two synthetic frames, measured pixel by pixel: sizes within 1568 px and
1.15 Mpx, the before box amber and the after box green where the boxes say, a caption line under each.

STALE: a temp folder with real files and real mtimes, and the page's answer faked: changed after the
page loaded and fetched before the change is stale; hot-swapped (fetched after) is not; never loaded is
not, unless the resource list is full; node_modules, dist, dot folders and studio.edits.json never count.

The ORCHESTRATION, with the page replaced by a fake that records every call: the put-back runs and the
view is handed back even when a capture throws half way; a look never runs without a live tab; edit
and place pass the right states and keep the page's private fields out of the answer; /shot without a
target is exactly the old shot.

The PAGE: the scene script and the navigator script together in node, against a real three.js scene
drawn by a fake renderer the way three draws: the Studio camera takes the view and gives it back, the
game camera never moves, a /goto view is put back exactly, a switched-off place is held on while it is
photographed, and an edit hands back its exact after-state.
"""
import asyncio
import base64
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import live as L                  # noqa: E402
from asset_studio import live_scene as S            # noqa: E402
from asset_studio import live_view as V             # noqa: E402
from asset_studio import live_navigate as NAVMOD    # noqa: E402
from asset_studio.config import DATA_DIR            # noqa: E402

# This suite tests what the live link DOES, with the page faked. A new PC starts with the link's
# switch off (Settings -> Planning), and every edit here was refused before it reached the fake.
L.enabled = lambda: True

ok = fail = skip = 0


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
    print("  SKIP  %s — %s" % (name, why))


def near(a, b, tol=1e-6):
    try:
        return len(a) == len(b) and all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
    except Exception:
        return False


STUDIO = Path(__file__).resolve().parent.parent
FRONT = STUDIO / "frontend"
TMP = DATA_DIR / "tmp" / ("live_view_test-%d" % int(time.time() * 1000))
TMP.mkdir(parents=True, exist_ok=True)


def project(v, cam_pos, look_at, fov_v, aspect):
    """A plain pinhole: world point -> (x, y) in 0..1 of the frame, y down; None behind the camera."""
    f = [look_at[i] - cam_pos[i] for i in range(3)]
    fl = math.sqrt(sum(c * c for c in f))
    f = [c / fl for c in f]
    up = [0.0, 1.0, 0.0]
    r = [f[1] * up[2] - f[2] * up[1], f[2] * up[0] - f[0] * up[2], f[0] * up[1] - f[1] * up[0]]
    rl = math.sqrt(sum(c * c for c in r)) or 1.0
    r = [c / rl for c in r]
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
    d = [v[i] - cam_pos[i] for i in range(3)]
    z = sum(d[i] * f[i] for i in range(3))
    if z <= 0:
        return None
    t = math.tan(math.radians(fov_v) / 2)
    x = sum(d[i] * r[i] for i in range(3)) / (z * t * aspect)
    y = sum(d[i] * u[i] for i in range(3)) / (z * t)
    return ((x + 1) / 2, (1 - y) / 2)


def corners(lo, hi):
    return [[hi[0] if i & 1 else lo[0], hi[1] if i & 2 else lo[1], hi[2] if i & 4 else lo[2]] for i in range(8)]


try:
    # ================================================================ the words
    print("What `look` may say")
    real_pref = L._engine_pref
    try:
        L._engine_pref = lambda k, d=None: True if k == "scene_look" else d
        check("left out, one edit takes a picture when scene_look is on (the default)", V.look_request(None) == ("3q", ""))
        check("…a batch does not, unless it asks", V.look_request(None, batch=True) == (None, "")
              and V.look_request("side", batch=True) == ("side", ""))
        L._engine_pref = lambda k, d=None: False if k == "scene_look" else d
        check("…and with scene_look off, one edit takes none", V.look_request(None) == (None, ""))
    finally:
        L._engine_pref = real_pref
    check("true is the 3/4 view, false is none", V.look_request(True) == ("3q", "") and V.look_request(False) == (None, ""))
    check("every angle word is taken, any case", all(V.look_request(w.upper())[0] == w for w in V.LOOK_WORDS))
    bad = V.look_request("sideways")
    check("a word that means nothing is refused with the words that do", bad[0] is None and "player" in bad[1], bad)

    # ================================================================ the camera
    print("\nThe camera that frames a box")
    lo, hi = [-0.5, 0.0, -0.5], [0.5, 1.0, 0.5]
    fr = V.frame(lo, hi, game_yaw=0, angle="3q", fov=50, aspect=16 / 9, near=0.1, far=100)
    r = math.sqrt(3.0) / 2
    check("d = r / sin(fov/2) x 1.15, r = half the diagonal", abs(fr["distance"] - r / math.sin(math.radians(25)) * 1.15) < 1e-3
          and abs(fr["radius"] - r) < 1e-3, fr)
    check("…looking at the middle of the box", near(fr["look_at"], [0, 0.5, 0], 1e-4), fr["look_at"])
    pts = [project(c, fr["pos"], fr["look_at"], 50, 16 / 9) for c in corners(lo, hi)]
    check("all eight corners land inside the frame (pinhole projection)", all(p and 0 <= p[0] <= 1 and 0 <= p[1] <= 1 for p in pts), pts)
    check("…and fill a real share of it (not a speck)", max(p[1] for p in pts) - min(p[1] for p in pts) > 0.45, pts)
    # The camera sits on the side the angle says, measured from the pose itself.
    for word, (dyaw, pitch) in V.ANGLES.items():
        f = V.frame(lo, hi, game_yaw=30, angle=word, fov=50, near=0.1, far=100)
        fwd = [f["look_at"][i] - f["pos"][i] for i in range(3)]
        fl = math.sqrt(sum(c * c for c in fwd))
        yaw = math.degrees(math.atan2(-fwd[0], -fwd[2]))
        pit = math.degrees(math.asin(fwd[1] / fl))
        want = ((30 + dyaw + 180) % 360) - 180
        dy = abs(((yaw - want) + 180) % 360 - 180)
        if not (abs(pit - pitch) < 0.01 and (dy < 0.01 or word == "top")):
            check("angle %s: yaw %.2f pitch %.2f" % (word, yaw, pit), False, (want, pitch))
            break
    else:
        check("every angle is relative to the game camera's heading (front = yaw+0, 3q +35, side +90, back +180; top straight down)", True)
    tall = V.frame([0, 0, 0], [0.4, 3.0, 0.4], angle="front", fov=50, aspect=390 / 844, near=0.1)
    pts = [project(c, tall["pos"], tall["look_at"], 50, 390 / 844) for c in corners([0, 0, 0], [0.4, 3.0, 0.4])]
    check("a phone held upright frames by its width: the whole box stays in frame", all(p and 0 <= p[0] <= 1 and 0 <= p[1] <= 1 for p in pts), pts)
    rr = V.frame([15.386, 4.839, 59.306], [16.914, 7.165, 60.889], game_yaw=180, fov=72, near=4.2, far=640)
    check("rot-rush's 4.2 m near plane: the distance stays the formula's (3.13 m), the Studio camera's near drops",
          abs(rr["distance"] - 3.132) < 0.01 and rr["near"] < 1.0, rr)
    ok_near = V.frame(lo, hi, fov=50, near=0.1)
    check("…and a normal near plane is left alone", ok_near["near"] == 0.1, ok_near)
    far_ = V.frame([-500, 0, -500], [500, 10, 500], fov=50, near=0.1, far=200)
    check("a box past the far plane is cut there, and the answer says so", far_["distance"] <= 180.0001 and far_["notes"], far_)
    wide = V.frame(lo, hi, fov=50, grow=2.0)
    check("a watch frames twice the radius", abs(wide["radius"] - 2 * r) < 1e-3 and wide["distance"] > fr["distance"] * 1.9, wide)
    check("the union of boxes is the box of all of them", V.union([([0, 0, 0], [1, 1, 1]), ([2, -1, 0], [3, 0, 2]), None])
          == ([0, -1, 0], [3, 1, 2]))
    check("a thing with no body is framed as a small cube round its origin", V.box_of({"min": None, "max": None, "world": [1, 2, 3]})
          == ([0.7, 1.7, 2.7], [1.3, 2.3, 3.3]))

    # ================================================================ the pictures
    print("\nThe sheet: before | after, outlined, captioned")
    from PIL import Image, ImageDraw
    b_img = Image.new("RGB", (1280, 720), (40, 60, 90))
    a_img = Image.new("RGB", (1280, 720), (40, 60, 90))
    ImageDraw.Draw(b_img).rectangle([300, 300, 500, 500], fill=(255, 255, 255))
    ImageDraw.Draw(a_img).rectangle([700, 300, 900, 500], fill=(255, 255, 255))
    bb, ab = [300 / 1280, 300 / 720, 500 / 1280, 500 / 720], [700 / 1280, 300 / 720, 900 / 1280, 500 / 720]
    sheet = V.compose_pair(b_img, a_img, [("crate-1", bb)], [("crate-1", ab)], "before · crate-1 · rests on floor",
                           "after · moved 1.00 m · ground gap 0.00 → floats 0.42 m", "crate-1 — moved 1.00 m")
    W, H = sheet.size
    check("within 1568 px wide and 1.15 Mpx", W <= 1568 and W * H <= 1_150_000, sheet.size)
    pw, ph = V.pair_panel_size(1280, 720)
    top = V.GAP + 28

    def px(x, y):
        return sheet.getpixel((int(x), int(y)))
    # The left edge of each box, a pixel inside the dark rim: the colour it was drawn in.
    bx = V.GAP + bb[0] * pw + 1
    ax = V.GAP + pw + V.GAP + ab[0] * pw + 1
    my = top + (bb[1] + bb[3]) / 2 * ph
    check("the before box is amber where the box says", px(bx, my) == V.AMBER, (px(bx, my), V.AMBER))
    check("the after box is green where the box says", px(ax, my) == V.GREEN, (px(ax, my), V.GREEN))
    check("…and the after panel is not outlined where the before box was", px(V.GAP + pw + V.GAP + bb[0] * pw + 1, my) != V.GREEN)
    cap = sheet.crop((V.GAP + 14, top + ph + 4, V.GAP + pw - 10, top + ph + 24)).convert("L")
    check("a caption line is written under each panel", max(cap.getdata()) > 150, max(cap.getdata()))
    many = V.compose_pair(b_img, a_img, [("a", bb), ("b", ab)], [("a", ab), ("b", bb)], "before · 2 objects", "after · a moved; b moved", "2 objects")
    check("a batch outlines every touched thing on one sheet, labelled", many.size == sheet.size
          and many.getpixel((int(V.GAP + pw + V.GAP + bb[0] * pw + 1), int(my))) == V.GREEN)
    portrait = V.compose_pair(Image.new("RGB", (390, 844)), Image.new("RGB", (390, 844)), [], [], "b", "a", "t")
    check("a phone-shaped frame still fits the budget", portrait.size[0] <= 1568 and portrait.size[0] * portrait.size[1] <= 1_150_000, portrait.size)
    d = ImageDraw.Draw(sheet)
    cut = V.fit_text(d, "x" * 400, V._font(14), 200)
    check("a caption too long for its panel is cut with an ellipsis, never wrapped", cut.endswith("…") and V._text_w(d, cut, V._font(14)) <= 200)
    cols, cw, chh = V.plan_grid(16, 16 / 9)
    rows = (16 + cols - 1) // cols
    Wg, Hg = cols * cw + V.GAP * (cols + 1), 30 + rows * (chh + 24 + V.GAP) + V.GAP
    check("sixteen watch frames fit the budget too", Wg <= 1568 and Wg * Hg <= 1_150_000, (cols, cw, Wg, Hg))
    crop, nb = V.cover(Image.new("RGB", (1280, 720)), [0.4, 0.4, 0.6, 0.6], 640, 640)
    check("size [w,h]: scaled to cover and cut from the middle, the box moved with it", crop.size == (640, 640)
          and near(nb, [(0.4 * 1138 - 249) / 640, 0.4, (0.6 * 1138 - 249) / 640, 0.6], 0.01), nb)

    print("\nCaptions say what changed")
    res = {"key": "pillar-2", "changed": ["pos"], "before": {"world": [5, 0, -5]}, "after": {"world": [6, 0, -5]},
           "ground_before": {"support": "floor", "gap": 0.0}, "ground": {"support": "floor", "gap": 0.42},
           "overlaps": [{"key": "crate-2", "share": 0.3}]}
    t, cb, ca = V.edit_captions([res])
    check("moved, the footing before and after, and what it now runs into",
          "moved 1.00 m" in ca and "ground gap 0.00 → floats 0.42 m" in ca and "overlaps: crate-2" in ca, ca)
    check("the before caption says where it stood", "rests on floor" in cb and "pillar-2" in cb, cb)
    rot = {"key": "k", "changed": ["rot"], "before": {"rot": [0, 10, 0]}, "after": {"rot": [0, 100, 0]}}
    check("a turn about one axis reads as one", "turned 90° about y" in V.edit_captions([rot])[2], V.edit_captions([rot]))
    sc = {"key": "k", "changed": ["scale"], "before": {"scale": [1, 1, 1]}, "after": {"scale": [2, 2, 2]}}
    check("a uniform scale reads as one number", "scaled 1 → 2" in V.edit_captions([sc])[2])
    hid = {"key": "k", "changed": ["hidden"], "before": {"visible": True}, "after": {"visible": False}}
    check("a hide says hidden", "hidden" in V.edit_captions([hid])[2])
    t, cb, ca = V.edit_captions([res, rot])
    check("a batch names each object and what it did", "pillar-2 moved 1.00 m" in ca and "k turned" in ca and "2 objects" in t, ca)
    t, cb, ca = V.place_captions({"key": "crystal-6", "ground": {"support": "floor", "gap": 0}, "overlaps": []})
    check("a place: not there before, placed and standing after", "not there yet" in cb and "placed crystal-6" in ca and "rests on floor" in ca, (cb, ca))

    print("\nDid it stick?")
    check("a position the game put back is caught", V.drifted({"pos": [0, 6.5, 0]}, {"pos": [0, 5.5, 0]}) == ["pos"])
    check("…a position that stuck is not", V.drifted({"pos": [0, 6.5, 0]}, {"pos": [0, 6.5, 0.004]}) == [])
    check("three euler: 180 and -180 degrees are the same turn", V.drifted({"rot": [0, math.pi, 0]}, {"rot": [0, -math.pi, 0]}) == [])
    check("PlayCanvas quaternions: a 90 degree difference is caught, q and -q are the same",
          V.drifted({"q": [0, 0, 0, 1]}, {"q": [0, 0.7071068, 0, 0.7071068]}) == ["rot"]
          and V.drifted({"q": [0, 0, 0, 1]}, {"q": [0, 0, 0, -1]}) == [])
    check("only the fields the edit set are compared", V.drifted({"pos": [1, 2, 3]}, {"pos": [1, 2, 3], "rot": [9, 9, 9]}) == [])
    check("partial state: a move puts back only pos, so the game's own spin keeps going",
          V.partial_local({"pos": [1, 2, 3], "rot": [0, 1, 0], "order": "XYZ", "scale": [1, 1, 1], "on": True}, ["pos"]) == {"pos": [1, 2, 3]})
    check("…a turn puts back rot, its order and a PlayCanvas quaternion",
          set(V.partial_local({"pos": [0, 0, 0], "rot": [0, 1, 0], "order": "XYZ", "q": [0, 0, 0, 1], "on": True}, ["rot"])) == {"rot", "order", "q"})
    check("…and nothing touched puts nothing back (not the whole transform)",
          V.partial_local({"pos": [1, 2, 3], "rot": [0, 1, 0], "scale": [1, 1, 1], "on": True}, []) == {})
    nothing = V.look_edit(str(TMP), {"target": "T"}, [{"ok": True, "key": "crystal-1", "undo": {"pos": [0, 0, 0]},
                                                       "redo": {"pos": [0, 0, 0]}, "changed": [], "before": {"world": [0, 0, 0]},
                                                       "after": {"world": [0, 0, 0]}}], "3q")
    check("an edit that changed nothing (a drop with nothing below) takes no look, and says where to look instead",
          nothing.get("ok") is False and "nothing changed" in nothing.get("error", "") and "/api/live/shot" in nothing["error"], nothing)

    print("\nWhat a strip of frames says")
    times = [0.0, 0.2, 0.4, 0.6]
    f = V.watch_findings(times, [0, 0, 0, 0], [None] * 4)
    check("nothing changed: STILL", f and f[0].startswith("STILL"), f)
    f = V.watch_findings(times, [0, 0, 0, 0], [None] * 4, input_at=0.1)
    check("…and after an input, NO RESPONSE", any(x.startswith("NO RESPONSE") for x in f), f)
    f = V.watch_findings(times, [0, 0.1, 6.0, 2.0], [None, None, [0.4, 0.31, 0.52, 0.6], [0.41, 0.3, 0.5, 0.58]], input_at=0.1,
                         target_box=[0.38, 0.2, 0.55, 0.9], target="player")
    check("motion: where, from when, and the peak", any("x .40–.52, y .30–.60 from t=0.40 s" in x and "peak 6.0%" in x for x in f), f)
    check("…the first change after the input", any("first change after the input at t=0.10 s: t=0.40 s" in x for x in f), f)
    check("…and whether it is the target that moved", any("overlaps player's box" in x for x in f), f)
    f = V.watch_findings(times, [0, 0.1, 0.2, 0.1], [None, [0.49, 0.36, 0.52, 0.54], None, None], input_at=0.3)
    check("a small thing moving (0.1% of a wide view) still counts once its region is found", any(x.startswith("moved") for x in f)
          and any("already moving before the input" in x for x in f), f)
    f = V.watch_findings(times, [0, 80.0, 1.0, 0], [None, [0, 0, 1, 1], [0.1, 0.1, 0.2, 0.2], None])
    check("the whole view changing is called a cut", any("whole view changed" in x for x in f), f)
    f = V.watch_findings(times, [0, 0.2, 22.0, 18.0], [None, [0.5, 0.4, 0.52, 0.5], [0, 0.08, 1.0, 0.78], [0, 0.1, 1.0, 0.8]], input_at=0.1)
    check("a change from edge to edge without a target: the game's camera is moving, and a target fixes it",
          any("camera is moving" in x and "target" in x for x in f), f)
    f = V.watch_findings(times, [0, 0.2, 22.0, 18.0], [None, [0.5, 0.4, 0.52, 0.5], [0, 0.08, 1.0, 0.78], None], target_box=[0.4, 0.2, 0.6, 0.9])
    check("…not said when a Studio camera was already holding still on a target", not any("camera is moving" in x for x in f), f)
    fa = Image.new("RGB", (640, 360), (30, 30, 30))
    fb = fa.copy()
    ImageDraw.Draw(fb).rectangle([256, 108, 320, 216], fill=(240, 240, 240))
    pct, mb = V.motion(fa, fb)
    check("motion between two frames: a percent and the box it happened in", pct > 1.0 and mb and abs(mb[0] - 0.4) < 0.03
          and abs(mb[2] - 0.5) < 0.03 and abs(mb[1] - 0.3) < 0.04 and abs(mb[3] - 0.6) < 0.04, (pct, mb))
    noisy = fa.copy()
    noisy.putpixel((100, 100), (255, 255, 255))
    check("…and a single flickering pixel draws no box", V.motion(fa, noisy)[1] is None)

    # ================================================================ stale
    print("\nStale: the page runs code older than the files")
    G = TMP / "game"
    for rel in ("index.html", "src/main.js", "src/assets.js", "src/hot.js", "tests/unit.js", "public/data.json",
                "node_modules/three/three.module.js", "dist/game.js", ".studio/x.js", "studio.edits.json", "README.md"):
        p = G / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    files, trunc = V.scan_sources(G)
    check("the scan sees source files and nothing under node_modules, dist or dot folders, nor the sidecar",
          set(files) == {"index.html", "src/main.js", "src/assets.js", "src/hot.js", "tests/unit.js", "public/data.json"} and not trunc,
          sorted(files))
    now = time.time()
    origin = (now - 100) * 1000.0                    # the page loaded 100 s ago
    ages = {"index.html": now - 200, "src/main.js": now - 50, "src/assets.js": now - 50, "src/hot.js": now - 50,
            "tests/unit.js": now - 50, "public/data.json": now - 50}
    fetched = {"/src/main.js": origin + 5, "/src/assets.js": origin + 10, "/src/hot.js": (now - 40) * 1000.0,
               "/data.json": origin + 20}
    got = V.stale_files(ages, origin, fetched, entries=12, doc_path="/")
    names = [r for r, _ in got]
    check("loaded, then changed on disk: stale", "src/main.js" in names and "src/assets.js" in names, names)
    check("hot-swapped (fetched again after the change, as Vite does): not stale", "src/hot.js" not in names, names)
    check("changed but never loaded by the page (a test file): not stale", "tests/unit.js" not in names, names)
    check("a Vite public/ file is matched at the root it is served from", "public/data.json" in names, names)
    check("older than the page: not stale", "index.html" not in names, names)
    got2 = V.stale_files(dict(ages, **{"index.html": now - 10}), origin, fetched, entries=12, doc_path="/")
    check("the page's own HTML changed after it loaded: stale", "index.html" in [r for r, _ in got2])
    got3 = V.stale_files(ages, origin, {}, entries=250, doc_path="/")
    check("the resource list is full: a changed file it cannot vouch for is NOT counted stale",
          "tests/unit.js" not in [r for r, _ in got3], got3)
    may = V.maybe_stale(ages, origin, {}, True, doc_path="/")
    check("…it is a `maybe`, and a lock file or build config never is",
          "tests/unit.js" in [r for r, _ in may] and not any(r.endswith("package.json") for r, _ in may), may)
    check("a whole list gives no `maybe` at all", V.maybe_stale(ages, origin, {}, False, doc_path="/") == [])
    mans = V._stale_answer([], origin, may)
    check("the answer for a full list says maybe, and to reload only if the picture disagrees",
          mans and mans.get("maybe") is True and "full" in mans["note"] and "reload" in mans["note"], mans)
    check("a sure file outranks the maybes", V._stale_answer(got, origin, may).get("maybe") is None)
    ans = V._stale_answer(got, origin)
    check("the answer: up to 5 files, when the page loaded, and what to do", ans and len(ans["files"]) <= 5 and ans["since"]
          and "reload" in ans["note"], ans)
    check("nothing stale: no `stale` at all", V._stale_answer([], origin) is None)
    (G / "src" / "new.js").write_text("y", encoding="utf-8")
    again, _t = V.scan_sources(G)
    check("the scan is cached for two seconds (a batch of calls walks the tree once)", "src/new.js" not in again)
    V._SCAN.clear()
    check("…and seen after the cache", "src/new.js" in V.scan_sources(G)[0])
    big = TMP / "big"
    big.mkdir()
    for i in range(2105):
        (big / ("f%04d.js" % i)).write_text("", encoding="utf-8")
    bf, bt = V.scan_sources(big)
    check("never past 2,000 files", len(bf) == 2000 and bt, (len(bf), bt))

    # ================================================================ orchestration, page faked
    print("\nThe look, with the page replaced")
    frame_png = io.BytesIO()
    Image.new("RGB", (320, 180), (10, 20, 30)).save(frame_png, "JPEG")
    FRAME = frame_png.getvalue()

    class FakeLive:
        def __init__(self):
            self.calls = []

        async def ask(self, expr, depth=6):
            self.calls.append(("ask", expr))
            if expr.startswith("__nav.goto("):
                return {"ok": True, "pose": {"pos": [1, 2, 3], "yaw": 35, "pitch": -25, "fov": 50}, "in_view": [{"name": "crate-1"}]}
            return {}

        async def raw(self, expr, wait=True):
            self.calls.append(("raw", expr[:40]))
            return {"origin": time.time() * 1000, "fetched": {}, "entries": 0, "doc": "/", "scheme": "http:"}

        async def call(self, method, params=None):
            self.calls.append(("call", method))
            return {"data": base64.b64encode(FRAME).decode()}

    def fake_page_factory(log, fail_on=None):
        async def fake_page(live, expr):
            log.append(expr.split("(")[0])
            if fail_on and expr.startswith(fail_on):
                raise RuntimeError("the page went away")
            if expr.startswith("__scene.viewBegin"):
                return {"ok": True, "game": {"pos": [0, 6, 13], "yaw": 0, "pitch": -30, "fov": 55}, "aspect": 16 / 9,
                        "near": 0.1, "far": 100, "vw": 1280, "vh": 720}
            if expr.startswith("__scene.viewEnd"):
                return {"ok": True, "view": "released", "game": {"pos": [0, 6, 13], "yaw": 0, "pitch": -30, "fov": 55}}
            if expr.startswith("__scene.viewDrawing"):
                return True
            if expr.startswith("__scene.screenBoxes"):
                return {"ok": True, "items": {"crate-1": {"box": [0.4, 0.4, 0.6, 0.6], "local": {"pos": [1, 0, 0]}}}}
            if expr.startswith("__scene.drawReady"):
                return {"ready": True, "known": True}
            if expr.startswith("__scene.reveal"):
                return []
            return {"ok": True}
        return fake_page

    real = (V._page, V._prepare)

    async def no_prepare(live, e):
        return None

    try:
        V._prepare = no_prepare
        for fail_on, label in ((None, "a clean look"), ("__scene.screenBoxes", "a look whose page fails after the first frame"),
                               ("__scene.restore([{\"key\": \"crate-1\", \"local\": {\"pos\": [0", "a look whose before-state put-back throws")):
            log = []
            V._page = fake_page_factory(log, fail_on)
            live = FakeLive()
            before_js = "__scene.restore([{\"key\": \"crate-1\", \"local\": {\"pos\": [0, 0, 0]}}], {})"
            after_js = "__scene.restore([{\"key\": \"crate-1\", \"local\": {\"pos\": [1, 0, 0]}}], {})"
            out = asyncio.run(V._look_session(live, str(TMP / "game"), {"opened": time.time(), "url": ""}, ["crate-1"],
                                              [0, 0, 0], [1, 1, 1], "3q", before_js, after_js, {}))
            ends = [x for x in log if x == "__scene.viewEnd"]
            unrev = [x for x in log if x == "__scene.unreveal"]
            check("%s: the view is handed back exactly once, after the reveals are put back" % label,
                  len(ends) == 1 and unrev and log.index("__scene.unreveal") < log.index("__scene.viewEnd"), log)
            if fail_on and "restore" in fail_on:
                check("…the change is put back even though the before-state threw (the game is left as the edit left it)",
                      log.count("__scene.restore") >= 2 and out["errors"], (log, out.get("errors")))
            elif fail_on:
                check("…and the error is in the answer, not a crash", out["errors"] and "went away" in out["errors"][0], out.get("errors"))
            else:
                check("…both frames were taken with ONE camera placement", len([c for c in live.calls if c[0] == "ask" and "goto" in c[1]]) == 1
                      and out.get("before") and out.get("after") and not out["errors"], (live.calls, out.get("errors")))
                check("…the change was put back after the before frame", log.count("__scene.restore") == 2, log)
        log = []
        V._page = fake_page_factory(log)
        out = asyncio.run(V._look_session(FakeLive(), str(TMP / "game"), {"opened": time.time(), "url": ""}, ["crate-1"],
                                          [0, 0, 0], [1, 1, 1], "player", "__scene.restore([], {})", "__scene.restore([], {})", {}))
        check("\"player\": no Studio camera is placed, the game's own camera is used", "__scene.viewGame" in log and "shot" not in out, log)
        log = []
        V._page = fake_page_factory(log)
        out = asyncio.run(V._look_session(FakeLive(), str(TMP / "game"), {"opened": time.time(), "url": ""}, ["crate-1"],
                                          [0, 0, 0], [1, 1, 1], "3q", "__scene.restore([], {})", "__scene.restore([], {})", {}, place=True))
        seq = [x for x in log if x in ("__scene.restore", "__scene.drawReady")]
        check("a place: hidden first, then shown, then the engine asked whether it is drawn", seq[:3] == ["__scene.restore", "__scene.restore", "__scene.drawReady"], log)
    finally:
        V._page, V._prepare = real

    check("no live tab: no look, and no browser touched", V.look_edit(str(TMP), {"target": ""}, [
        {"ok": True, "key": "a", "undo": {}, "redo": {}, "before": {"world": [0, 0, 0]}, "after": {"world": [1, 0, 0]}}], "3q")
        == {"ok": False, "error": "no live tab to photograph"})
    check("an older page that hands back no after-state: said so, nothing guessed",
          "older page" in V.look_edit(str(TMP), {"target": "x"}, [{"ok": True, "key": "a", "undo": {}, "before": {}, "after": {}}], "3q")["error"])

    browser_ok, why = L.available()
    print("\nEdit and place hand the look what it needs, and keep the page's own fields to themselves")
    if not browser_ok:
        skipped("edit and place with the page faked", "no browser on this machine: %s" % why)
    seen = {}
    calls = []

    def fake_in_tab(project, expr):
        calls.append(expr)
        if expr.startswith("__scene.edit("):
            seen["opts"] = json.loads(expr[expr.rindex(", {") + 2:-1])
            return {"ok": True, "engine": "three", "results": [{
                "ok": True, "key": "crate-1", "name": "crate-1", "how": "key", "before": {"world": [0, 0, 0], "min": [0, 0, 0], "max": [1, 1, 1]},
                "after": {"world": [1, 0, 0], "min": [1, 0, 0], "max": [2, 1, 1]}, "ground": {"support": "floor", "gap": 0},
                "ground_before": {"support": "floor", "gap": 0}, "overlaps": [], "save": {"pos": [1, 0, 0]},
                "undo": {"pos": [0, 0, 0]}, "redo": {"pos": [1, 0, 0]}, "changed": ["pos"]}]}
        if expr.startswith("__scene.place("):
            return {"ok": True, "engine": "three", "key": "box", "name": "box", "id": "p1", "how": "a box", "where": "at",
                    "after": {"min": [0, 0, 0], "max": [1, 1, 1], "visible": True}, "ground": {}, "overlaps": [],
                    "item": {"id": "p1", "name": "box", "ref": {"kind": "primitive", "shape": "box"}}}
        return {"ok": False}

    real_in_tab, real_le, real_lp, real_rm = S._in_tab, V.look_edit, V.look_place, S._runtime_missing
    PJ = TMP / "proj"
    PJ.mkdir()
    ent = L._entry(str(PJ))
    ent["url"], ent["target"] = "http://127.0.0.1:1/fake", "T1"
    try:
        if not browser_ok:
            raise StopIteration
        S._in_tab = fake_in_tab
        S._runtime_missing = lambda: ""
        V.look_edit = lambda project, e, applied, angle, scene=None: (seen.__setitem__("look", (angle, json.loads(json.dumps(applied)))) or
                                                                      {"ok": True, "sheet": "s.png"})
        V.look_place = lambda project, e, res, angle, scene=None: (seen.__setitem__("place", (angle, res)) or {"ok": True, "sheet": "p.png"})
        r = S.edit(str(PJ), {"target": "crate-1", "move": [1, 0, 0], "look": "side", "code": False})
        check("an edit with look: the page is asked for the before-footing", seen.get("opts", {}).get("look") is True, seen.get("opts"))
        check("…the look gets the angle and the applied result with its undo AND redo", seen["look"][0] == "side"
              and seen["look"][1][0].get("redo") == {"pos": [1, 0, 0]} and seen["look"][1][0].get("undo") == {"pos": [0, 0, 0]}, seen.get("look"))
        check("…the answer carries the look, and no page-private field reaches the agent", r.get("look", {}).get("sheet") == "s.png"
              and all(k not in r for k in ("redo", "ground_before", "save")) and isinstance(r.get("undo"), str), r)
        seen.clear()
        r = S.edit(str(PJ), {"target": "crate-1", "move": [1, 0, 0], "look": False, "code": False})
        check("look:false: no picture, no before-footing asked", "look" not in seen and "look" not in r and not seen.get("opts", {}).get("look"), r)
        seen.clear()
        r = S.edit(str(PJ), {"edits": [{"target": "crate-1", "move": [1, 0, 0]}], "code": False})
        check("a batch without look takes no picture", "look" not in seen and "look" not in r, r)
        ops = L._batch_ops()
        r = ops["edit"](project=str(PJ), target="crate-1", move=[1, 0, 0], code=False)
        check("an edit inside /api/live/batch takes no picture unless it asks (a batch is a script)",
              "look" not in seen and "look" not in r and r.get("ok"), r)
        r = ops["edit"](project=str(PJ), target="crate-1", move=[1, 0, 0], code=False, look="side")
        check("…and one that asks gets its picture", seen.get("look", (None,))[0] == "side" and r.get("look", {}).get("sheet") == "s.png", r)
        seen.clear()
        check("shot and watch are batch ops too", ops.get("shot") is V.shot and ops.get("watch") is V.watch,
              sorted(ops))
        n0 = len(calls)
        r = S.edit(str(PJ), {"target": "crate-1", "move": [1, 0, 0], "look": "sideways"})
        check("a look word that means nothing is refused before the game is touched", r.get("ok") is False and len(calls) == n0, r)
        check("`look` is a known field, never reported as ignored", "look" not in (S.edit(str(PJ), {"target": "crate-1", "move": [0, 0, 0],
                                                                                                   "look": False}).get("ignored") or []))
        r = S.place(str(PJ), {"asset": "box", "at": [0, 0, 0], "look": "front", "code": False})
        check("a place with look: the look gets the page's answer and the angle", seen.get("place", (None,))[0] == "front"
              and seen["place"][1].get("key") == "box" and r.get("look", {}).get("sheet") == "p.png", (seen.get("place"), r))
        n0 = len(calls)
        r = S.place(str(PJ), {"asset": "box", "look": 3})
        check("…and a bad look word on place is refused before the game is touched", r.get("ok") is False and len(calls) == n0, r)
    except StopIteration:
        pass
    finally:
        S._in_tab, V.look_edit, V.look_place, S._runtime_missing = real_in_tab, real_le, real_lp, real_rm
        ent["url"], ent["target"] = "", ""
        S._UNDO.pop(L.key_for(str(PJ)), None)

    print("\n/shot without a target is the shot it always was")
    real_shot, real_sn = L.shot, V.stale_now
    try:
        L.shot = lambda project, path="", full=False: {"ok": True, "path": "x.png", "bytes": 3, "args": [project, path, full]}
        V.stale_now = lambda project, e: None
        r = V.shot(str(PJ), "out.png", True)
        check("no target: exactly live.shot's answer, same arguments", r == {"ok": True, "path": "x.png", "bytes": 3,
                                                                            "args": [str(PJ), "out.png", True]}, r)
        V.stale_now = lambda project, e: {"files": ["src/a.js"], "n": 1, "since": "t", "note": "n"}
        r = V.shot(str(PJ))
        check("…plus `stale`, only when the page runs old code", r.get("stale", {}).get("files") == ["src/a.js"] and r.get("path") == "x.png", r)
    finally:
        L.shot, V.stale_now = real_shot, real_sn
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from asset_studio.routers import live as RL
        app = FastAPI()
        app.include_router(RL.router)
        got = {}
        real_vs, real_vw = V.shot, V.watch
        V.shot = lambda *a, **k: (got.__setitem__("shot", a), {"ok": True})[1]
        V.watch = lambda *a, **k: (got.__setitem__("watch", a), {"ok": True})[1]
        try:
            cl = TestClient(app)
            cl.post("/api/live/shot", json={"project": "P"})
            check("the route: an old body {project} reaches the plain shot (no target, no camera)", got.get("shot", ())[:4] == ("P", "", False, ""),
                  got.get("shot"))
            cl.post("/api/live/shot", json={"project": "P", "target": "crate-1", "angle": "side", "distance": 4, "box": False, "size": [640, 480]})
            check("…target, angle, distance, box and size arrive as given", got.get("shot", ())[3:8] == ("crate-1", "side", 4.0, False, [640, 480]),
                  got.get("shot"))
            cl.post("/api/live/watch", json={"project": "P", "seconds": 1.5, "frames": 5, "input": [{"type": "key", "key": " "}], "at_ms": 200})
            check("the watch route passes seconds, frames, input and at_ms", got.get("watch", ())[:3] == ("P", 1.5, 5)
                  and got["watch"][5] == [{"type": "key", "key": " "}] and got["watch"][6] == 200, got.get("watch"))
        finally:
            V.shot, V.watch = real_vs, real_vw
    except Exception as ex:                             # noqa: BLE001
        skipped("the routes through FastAPI", str(ex)[:200])

    print("\nRefused before a browser is touched")
    if not browser_ok:
        # Not a pass: without a browser _guard refuses first, and none of the reasons is reached.
        skipped("every bad shot and watch request is refused with its reason", "no browser here")
    else:
        for call, word in ((lambda: V.watch(str(PJ), seconds=0.1), "seconds"), (lambda: V.watch(str(PJ), frames=20), "frames"),
                           (lambda: V.watch(str(PJ), at_ms=99999), "at_ms"), (lambda: V.watch(str(PJ), input="space"), "input"),
                           (lambda: V.shot(str(PJ), target="x", angle="up"), "angle"), (lambda: V.shot(str(PJ), target="x", size=[1, 2]), "size"),
                           (lambda: V.shot(str(PJ), target="x", distance=-1), "distance"), (lambda: V.watch(str(PJ)), "open")):
            r = call()
            if not (r.get("ok") is False and word in r.get("error", "")):
                check("a bad request is refused with its reason (%s)" % word, False, r)
                break
        else:
            check("every bad shot and watch request is refused with its reason, before a browser is touched", True)

    # ================================================================ the page, in node
    print("\nThe page scripts together, against a real three.js scene")
    three = FRONT / "node_modules" / "three" / "build" / "three.module.js"
    node = shutil.which("node")
    if not node or not three.is_file():
        skipped("the page in node", "needs node and three under frontend/node_modules")
    else:
        HARNESS = r"""
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const A = JSON.parse(process.argv[2]);
const T = await import(pathToFileURL(A.three).href);
globalThis.window = globalThis;
globalThis.innerWidth = 1280; globalThis.innerHeight = 720; globalThis.devicePixelRatio = 1;
globalThis.location = { href: 'http://127.0.0.1:9/', pathname: '/' };
const rafq = [];
globalThis.requestAnimationFrame = (cb) => { rafq.push(cb); return rafq.length; };
const R = {};
const near = (a, b, t = 1e-9) => a.length === b.length && a.every((x, i) => Math.abs(x - b[i]) <= t);

/* The world: a floor, a crate, a player, and platform-8 with a sign on it, which the game switches
   off on every frame (rot-rush does exactly that). */
const scene = new T.Scene();
const floor = new T.Mesh(new T.BoxGeometry(40, 0.2, 40), new T.MeshBasicMaterial()); floor.name = 'floor'; floor.position.y = -0.1; scene.add(floor);
const crate = new T.Group(); crate.name = 'crate-1';
const cm = new T.Mesh(new T.BoxGeometry(1, 1, 1), new T.MeshBasicMaterial()); cm.name = 'crate-box'; cm.position.y = 0.5; crate.add(cm);
crate.position.set(2, 0, -1); scene.add(crate);
const player = new T.Mesh(new T.CapsuleGeometry(0.3, 0.8, 4, 8), new T.MeshBasicMaterial()); player.name = 'player'; player.position.set(0, 0.7, 3); scene.add(player);
const platform = new T.Group(); platform.name = 'platform-8'; platform.position.set(-6, 0, -8); scene.add(platform);
const sign = new T.Mesh(new T.BoxGeometry(1.6, 1, 0.1), new T.MeshBasicMaterial()); sign.name = 'sign'; sign.position.y = 1; platform.add(sign);
platform.visible = false;
scene.updateMatrixWorld(true);
/* The game's camera is NOT in its scene, as the proof game's is not. */
const camera = new T.PerspectiveCamera(55, 1280 / 720, 0.1, 100); camera.name = 'camera';
camera.position.set(0, 6, 10); camera.lookAt(0, 0.5, 0); camera.updateMatrixWorld(); camera.updateProjectionMatrix();
/* A renderer that draws the way three does: world matrices, then scene.onBeforeRender with the camera. */
const renderer = {
  domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1280, height: 720 }) },
  render(sc, cam) { sc.updateMatrixWorld(); if (cam.parent === null) cam.updateMatrixWorld(); sc.onBeforeRender(this, sc, cam, null);
                    this.last = cam; this.platformShown = platform.visible; }
};
function gameFrame() { platform.visible = false; renderer.render(scene, camera); requestAnimationFrame(gameFrame); }
requestAnimationFrame(gameFrame);
const tick = (n) => { for (let i = 0; i < n; i++) rafq.splice(0).forEach((cb) => cb(performance.now())); };
globalThis.__live = { scenes: () => [scene], reach: () => ({ engine: 'three', reachable: true, at: '__live.scenes()[0]' }),
                      pin: () => true, pinned: null, pinnedKind: '' };
(0, eval)(readFileSync(A.nav, 'utf8'));
(0, eval)(readFileSync(A.script, 'utf8'));
const S = globalThis.__scene, N = globalThis.__nav;
S.boxes(['crate-1']);                                   // installs the scene hook
tick(3);
const gm0 = Array.from(camera.matrixWorld.elements);

/* 1. a look: begin, the Studio camera placed, its own near plane, drawing, boxes, the view handed back */
const b = S.viewBegin();
R.begin = { ok: b.ok, game: b.game, studio_on: b.studio_on, near: b.near };
const g = N.goto({ look_at: [2, 0.5, -1], distance: 3, yaw: 35, pitch: -25, limit: 8 });
R.goto_ok = g.ok;
R.near_set = S.viewNear(0.02);
R.near_now = N.ours.near;
tick(3);
R.drawing = S.viewDrawing();
R.drawn_by_studio = renderer.last === N.ours;
const sb = S.screenBoxes(['crate-1', 'player', 'nothing-here']);
R.box = sb.items['crate-1'];
R.box_camera_studio = sb.camera.studio;
R.missing = sb.items['nothing-here'];
const e1 = S.viewEnd();
R.end = { view: e1.view, game: e1.game };
R.near_back = N.ours.near;
R.on_after = N.on;
tick(3);
R.drawn_by_game_after = renderer.last === camera;
R.game_cam_unmoved = near(Array.from(camera.matrixWorld.elements), gm0);

/* 2. an agent's /goto view is up: a look puts it back exactly, and keeps it on */
N.goto({ look_at: [-2, 0.5, 2], distance: 5, yaw: -40, pitch: -10 });
tick(2);
const agentPos = N.ours.position.toArray(), agentQ = N.ours.quaternion.toArray(), agentTarget = JSON.stringify(N.target);
const b2 = S.viewBegin();
R.begin2_studio_on = b2.studio_on;
N.goto({ look_at: [2, 0.5, -1], distance: 3, yaw: 35, pitch: -25 });
tick(2);
R.moved_for_look = !near(N.ours.position.toArray(), agentPos, 1e-6);
const e2 = S.viewEnd();
R.end2 = e2.view;
R.agent_view_back = near(N.ours.position.toArray(), agentPos) && near(N.ours.quaternion.toArray(), agentQ) && JSON.stringify(N.target) === agentTarget;
R.agent_view_on = N.on === true;
tick(2);
R.agent_view_drawing = renderer.last === N.ours;
/* "player" while a /goto view is up: the game's camera for the look, the /goto view after it */
S.viewBegin(); S.viewGame(); tick(2);
R.player_uses_game = renderer.last === camera;
S.viewEnd(); tick(2);
R.player_then_goto_back = renderer.last === N.ours && N.on === true;
N.release(); tick(2);
R.released_to_game = renderer.last === camera && N.on === false;

/* 3. reveal: platform-8 is held on while it is photographed, though the game hides it every frame */
S.viewBegin();
R.revealed = S.reveal(['sign']);
tick(1);
R.shown_at_render = renderer.platformShown === true;
tick(2);
R.still_shown_at_render = renderer.platformShown === true;
R.unrevealed = S.unreveal();
tick(2);
R.hidden_again = renderer.platformShown === false && platform.visible === false;
S.viewEnd();

/* 4. what is in the picture, and whether it is drawn */
R.in_view = S.inView(8);
R.draw_ready = S.drawReady(['crate-1']);

/* 5. an edit hands back its exact after-state, and its footing before when a look is coming */
const ed = (await S.edit([{ target: 'crate-1', move: [0.5, 0, 0] }], { look: true })).results[0];
R.edit = { redo_pos: ed.redo && ed.redo.pos, crate_pos: crate.position.toArray(), ground_before: ed.ground_before, has_undo: !!ed.undo };
const ed2 = (await S.edit([{ target: 'crate-1', move: [-0.5, 0, 0] }], {})).results[0];
R.edit_no_look_no_footing = ed2.ground_before === undefined;
process.stdout.write(JSON.stringify(R) + '\n', () => process.exit(0));
"""
        (TMP / "nav.js").write_text(NAVMOD.NAV, encoding="utf-8")
        (TMP / "scene.js").write_text(S.page_script(), encoding="utf-8")
        (TMP / "harness.mjs").write_text(HARNESS, encoding="utf-8")
        run = subprocess.run([node, str(TMP / "harness.mjs"), json.dumps({"three": str(three), "nav": str(TMP / "nav.js"),
                                                                            "script": str(TMP / "scene.js")})],
                             capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
        try:
            R = json.loads(run.stdout.strip().splitlines()[-1])
        except Exception:
            R = None
        check("the harness ran", R is not None, (run.stdout[-1200:], run.stderr[-1200:]))
        if R:
            check("viewBegin knows the game camera's pose and near plane", R["begin"]["ok"] and R["begin"]["game"]
                  and abs(R["begin"]["game"]["fov"] - 55) < 1e-6 and R["begin"]["studio_on"] is False and R["begin"]["near"] == 0.1, R["begin"])
            check("the Studio camera is placed, lowers its OWN near plane, and is the one drawing", R["goto_ok"] and R["near_set"] == 0.02
                  and R["near_now"] == 0.02 and R["drawing"] and R["drawn_by_studio"], R)
            check("screen boxes are taken under the Studio camera, with each object's own values", R["box"]["box"] and R["box_camera_studio"]
                  and R["box"]["local"]["pos"] == [2, 0, -1], R["box"])
            check("…a key that does not exist says so instead of a box", R["missing"]["box"] is None and "nothing" in R["missing"]["error"], R["missing"])
            check("viewEnd releases the view: the game camera draws again, the near plane is put back",
                  R["end"]["view"] == "released" and R["on_after"] is False and R["drawn_by_game_after"] and R["near_back"] == 0.1, R)
            check("THE GAME CAMERA NEVER MOVED: its world matrix is bit-for-bit what it was", R["game_cam_unmoved"], R)
            check("…and the game pose viewEnd reports is the one viewBegin reported", R["end"]["game"] == R["begin"]["game"], (R["end"], R["begin"]))
            check("an agent's /goto view is put back exactly and left on (not released)",
                  R["begin2_studio_on"] and R["moved_for_look"] and R["end2"] == "restored" and R["agent_view_back"]
                  and R["agent_view_on"] and R["agent_view_drawing"], R)
            check("\"player\" uses the game's camera even over a /goto view, and gives the /goto view back",
                  R["player_uses_game"] and R["player_then_goto_back"] and R["released_to_game"], R)
            check("a switched-off place is revealed for the picture and HELD against the game's own hide",
                  R["revealed"] == ["platform-8"] and R["shown_at_render"] and R["still_shown_at_render"], R)
            check("…and put back off afterwards", R["unrevealed"] == 1 and R["hidden_again"], R)
            check("in the picture: things first, the floor that fills the frame last", R["in_view"] and R["in_view"][0] != "floor"
                  and (R["in_view"].index("floor") > R["in_view"].index("crate-1") if "floor" in R["in_view"] else True), R["in_view"])
            check("three draws a new mesh on its first frame: drawReady says ready", R["draw_ready"]["ready"] is True, R["draw_ready"])
            check("an edit hands back its exact after-state (redo) beside undo", R["edit"]["redo_pos"] == R["edit"]["crate_pos"] and R["edit"]["has_undo"], R["edit"])
            check("…and its footing before, only when a look asked for it", R["edit"]["ground_before"] and R["edit"]["ground_before"].get("support") == "floor"
                  and R["edit_no_look_no_footing"], R["edit"])
finally:
    shutil.rmtree(TMP, ignore_errors=True)
    V._SCAN.clear()

print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)
