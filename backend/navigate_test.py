# -*- coding: utf-8 -*-
"""Navigate: the pure parts, held without a browser.

The browser half — reaching the app through a closure, the Studio camera, a real locate — is
proved on the running game by hand and cannot run here. What CAN run here is everything a wrong
number would come from: the scorer, the candidate list, the pose arithmetic in the page script,
the note, and the switch.

Run:  backend/.venv/Scripts/python.exe backend/navigate_test.py
"""
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import live_navigate as N          # noqa: E402
from asset_studio import cc_session as cc            # noqa: E402
from asset_studio.config import DEFAULT_SETTINGS as DEFAULTS   # noqa: E402

passed = 0
fails = []


def ok(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
        return
    fails.append(name + ("   <- " + str(extra) if extra else ""))
    print("  FAIL  %s   %s" % (name, extra))


# ---------------------------------------------------------------- the scorer
print("The scorer ranks, and says how sure it is")
from PIL import Image, ImageDraw  # noqa: E402


def plaza(shift=0, hue=(200, 120, 220), bg=(30, 40, 60)):
    im = Image.new("RGB", (320, 180), bg)
    d = ImageDraw.Draw(im)
    for i in range(6):
        x = 20 + i * 48 + shift
        d.rectangle([x, 90, x + 30, 150], fill=hue)
        d.ellipse([x + 4, 40, x + 26, 62], fill=(250, 220, 90))
    d.rectangle([0, 150, 320, 180], fill=(90, 60, 30))
    return im


same = plaza()
ok("the same picture scores ~1", N.score_images(same, plaza()) > 0.97, N.score_images(same, plaza()))
near = N.score_images(same, plaza(shift=6))
far = N.score_images(same, plaza(shift=60))
other = N.score_images(same, Image.new("RGB", (320, 180), (200, 200, 200)))
ok("a slightly different angle scores lower", 0.6 < near < 0.99, near)
ok("...and a very different one lower still", far < near, "%s vs %s" % (far, near))
ok("an unrelated frame scores low", other < 0.45, other)
ok("the score is in 0..1", 0 <= other <= 1 and 0 <= near <= 1)
ok("brightness alone does not fool it",
   N.score_images(same, Image.eval(plaza(), lambda v: min(255, int(v * 1.4)))) > 0.8,
   N.score_images(same, Image.eval(plaza(), lambda v: min(255, int(v * 1.4)))))

print("\nHow much changed, at the same pose")
ok("nothing changed reads 0", N.changed_pct(same, plaza()) == 0.0, N.changed_pct(same, plaza()))
moved = N.changed_pct(same, plaza(shift=40))
ok("a moved row of stands reads as a real change", 5 < moved < 60, moved)
ok("a repaint reads as everything", N.changed_pct(same, Image.new("RGB", (320, 180), (255, 255, 255))) > 90)

# ---------------------------------------------------------------- the candidates
print("\nThe candidates: the hint first, then size, then the sweep")
things = [
    {"name": "plot-pedestals", "center": [0, 1, 20], "radius": 12, "min": [-10, 0, 8], "max": [10, 2, 32]},
    {"name": "daily-cases", "center": [13.5, 1.5, 48], "radius": 5, "min": [12, 0, 40], "max": [15, 3, 56]},
    {"name": "treadmill-left", "center": [-6, 0.5, 4], "radius": 2.5, "min": [-8, 0, 2], "max": [-4, 1, 6]},
    {"name": "wall", "center": [0, 4, 60], "radius": 40, "min": [-40, 0, 58], "max": [40, 8, 62]},
]
bounds = {"min": [-40, 0, 0], "max": [40, 8, 62]}
game_pose = {"pos": [0, 6, -10], "yaw": 0, "pitch": -25}
c = N.candidates(things, bounds, game_pose, hint="the treadmill platform", cap=40)
ok("a hinted thing leads", c[0]["why"].startswith("treadmill-left"), c[0])
ok("...with four bearings", sum(1 for x in c if x["why"] == "treadmill-left (hint)") == 4)
ok("the biggest thing comes before the smaller ones", [x["why"] for x in c].index("wall") < [x["why"] for x in c].index("daily-cases"))
ok("the game camera's pitch is kept", all(abs(x["pitch"] + 25) < 1e-6 for x in c if not x["why"].endswith("(hint, close)")))
ok("the sweep fills what is left", any(x["why"] == "sweep" for x in c) and len(c) == 40, len(c))
ok("the sweep stands at the game camera's height", all(x["at"][1] == 6 for x in c if x["why"] == "sweep"))
ok("distance scales with the thing", next(x for x in c if x["why"].startswith("wall"))["distance"] > next(x for x in c if x["why"].startswith("treadmill"))["distance"])
ok("the cap is a cap", len(N.candidates(things, bounds, game_pose, cap=8)) == 8)
ok("no things and no bounds is an empty list, not a crash", N.candidates([], None, None) == [])
ok("a pitch that looks up is not copied", all(x["pitch"] == -20 for x in N.candidates(things, None, {"pitch": 15, "yaw": 0}, cap=4)))
ok("names work like hint words", N.candidates(things, None, game_pose, names=["Daily Cases"], cap=4)[0]["why"].startswith("daily"))

sky = {"name": "sky", "center": [0, 0, 500], "radius": 400, "min": [-400, -400, 100], "max": [400, 400, 900]}
t2 = N.trim_outliers(things + [sky])
ok("the sky dome is scenery, not a place", all(t["name"] != "sky" for t in t2) and len(t2) == len(things))
ok("...so it never leads the candidates", all(not x["why"].startswith("sky") for x in N.candidates(things + [sky], bounds, game_pose, cap=40)))
ok("a small scene is left alone", len(N.trim_outliers(things[:3])) == 3)
ok("a row that is not a dict is dropped, not a crash", N.trim_outliers(["junk", things[0], things[1], things[2], things[3]]) == things)

print("\nA name said outright wins over a shared word")
plats = [{"name": "platform-%d" % i, "center": [15, 2, 120 * i], "radius": 20, "min": [0, 0, 120 * i - 20], "max": [30, 4, 120 * i + 20]} for i in range(1, 11)]
ok("a number is a hint word", "8" in N._words("platform 8"))
ok("names normalise", N._norm("Platform_8") == "platform-8" == N._norm("platform 8"))
c8 = N.candidates(plats + things, long_b if "long_b" in dir() else None, game_pose, hint="platform 8 treadmill x5 speed", cap=40)
ok("platform-8 leads when the hint says platform 8", c8[0]["why"].startswith("platform-8"), c8[0]["why"])
ok("a thing candidate carries its name and its centre, so goto can reveal the right instance",
   c8[0].get("name") == "platform-8" and c8[0].get("look_at") == [15, 2, 960])
close = [x for x in c8 if x["why"] == "platform-8 (hint, close)"]
ok("a hinted place also gets two close, low views — the way a player photographs it",
   len(close) == 2 and all(x["pitch"] == -8.0 and x["distance"] <= 18.0 for x in close), close)
ok("...and an unhinted one does not", not any(x["why"].endswith("(hint, close)") for x in N.candidates(things, None, game_pose, cap=40)))
ok("...and platform-1 does not sneak in on the shared word first", not c8[0]["why"].startswith("platform-1"))
ok("without a number every platform is equal and size decides",
   N.candidates(plats + things, None, game_pose, hint="platform", cap=8)[0]["why"].startswith("platform"))

print("\nA long world is swept along its axis")
long_b = {"min": [-29, -6, -15], "max": [60, 16, 1471]}
sw = N.sweep(long_b, game_pose, -20, 0, 48)
ok("a 1,470 m track gets standpoints along Z", len(sw) == 48 and all(x["axis"] == [0.0, 0.0, 1.0] for x in sw))
ok("...each looking up and down the track", {x["yaw"] for x in sw} == {0.0, 180.0})
ok("...at the game camera's height", all(x["at"][1] == 6 for x in sw))
ok("...centred across the track", all(abs(x["at"][0] - 15.5) < 1e-6 for x in sw))
ok("...spaced to the length", abs(sw[0]["spacing"] - 1486 / 24) < 0.01, sw[0]["spacing"])
ok("a square world keeps the grid", all(x.get("axis") is None for x in N.sweep(bounds, game_pose, -20, 0, 36)))
ok("no room, no sweep", N.sweep(long_b, game_pose, -20, 0, 0) == [])
ok("candidates hand the room left to the sweep", sum(1 for x in N.candidates(things, long_b, game_pose, cap=60) if x["why"] == "sweep") > 20)
ra = N.refinements(sw[3])
ok("a sweep standpoint refines by turning and by sliding along the axis", len(ra) == 4 and sum(1 for x in ra if x["why"] == "refine along the axis") == 2)
ok("...half a spacing each way", abs(abs(ra[2]["at"][2] - sw[3]["at"][2]) - sw[3]["spacing"] / 2) < 2e-3,
   (ra[2]["at"][2], sw[3]["at"][2], sw[3]["spacing"]))

r = N.refinements(c[0])
ok("refinement turns, tilts and steps in and out", len(r) == 6 and {x["why"] for x in r} == {"refine yaw", "refine pitch", "refine distance"})
ok("...and never tilts above the horizon", all(x["pitch"] <= 10 for x in r))
ok("a sweep point only turns", len(N.refinements({"at": [0, 6, 0], "yaw": 90, "pitch": -20})) == 2)

# ---------------------------------------------------------------- the page script
print("\nThe page script parses, and its camera arithmetic is right")
node = shutil.which("node")
if not node:
    print("  SKIP  no node on PATH")
else:
    js = N.NAV + r"""
    ;(function () {
      var DEG = Math.PI / 180;
      var forward = function (yaw, pitch) {
        var cy = Math.cos(yaw * DEG), sy = Math.sin(yaw * DEG), cp = Math.cos(pitch * DEG), sp = Math.sin(pitch * DEG);
        return [-sy * cp, sp, -cy * cp];
      };
      var r = function (v) { return v.map(function (x) { return Math.round(x * 1000) / 1000; }); };
      console.log(JSON.stringify({
        nav: typeof window.__nav, goto: typeof window.__nav.goto, where: typeof window.__nav.where,
        locateParts: typeof window.__nav.things + typeof window.__nav.bounds + typeof window.__nav.gamePose,
        rafWrapped: window.requestAnimationFrame !== window.__origRaf,
        f0: r(forward(0, 0)), f90: r(forward(90, 0)), fUp: r(forward(0, 90)), fDown: r(forward(0, -30))
      }));
    })();
    """
    shim = ("var window = globalThis; window.__origRaf = function (cb) { return 1; }; "
            "window.requestAnimationFrame = window.__origRaf; var document = { title: 'x' }; "
            "var performance = { now: function () { return 0; } };\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(shim + js)
        path = f.name
    try:
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        got = json.loads((out.stdout or "").strip().splitlines()[-1]) if out.returncode == 0 and out.stdout.strip() else {}
        ok("NAV parses and installs __nav", got.get("nav") == "object", (out.stderr or "")[:200])
        ok("...with goto and where", got.get("goto") == "function" and got.get("where") == "function")
        ok("...and the locate helpers", got.get("locateParts") == "functionfunctionfunction")
        ok("requestAnimationFrame is wrapped", got.get("rafWrapped") is True)
        ok("yaw 0 looks down -Z", got.get("f0") == [0, 0, -1], got.get("f0"))
        ok("yaw 90 looks down -X", got.get("f90") == [-1, 0, 0], got.get("f90"))
        ok("pitch 90 looks up", got.get("fUp") == [0, 1, 0], got.get("fUp"))
        ok("pitch -30 looks down", got.get("fDown") and got["fDown"][1] < 0 and got["fDown"][2] < 0, got.get("fDown"))
    finally:
        os.unlink(path)

# ---------------------------------------------------------------- the switch and the note
print("\nIts own switch, its own note")
ok("cc_navigate is a setting, off by default", DEFAULTS.get("cc_navigate") is False)
note = cc._navigate_note()
ok("the note names the three calls", "/api/live/goto" in note and "/api/live/where" in note and "/api/live/locate" in note)
ok("...and the reload loop", 'pose:"last"' in note and "changed_pct" in note)
ok("...and the one line that unlocks a stubborn game", "window.__game = { app }" in note)
ok("...and says a low score is a guess", "guess" in note)
ok("it stays a paragraph", len(note) < 1900, len(note))
rows = {r["key"]: r for r in cc.agent_notes_catalog()["notes"]}
ok("the catalogue row exists with a measured cost", "cc_navigate" in rows and rows["cc_navigate"]["tokens"] > 100,
   rows.get("cc_navigate"))
print("  (the navigate note measures %s tokens)" % rows.get("cc_navigate", {}).get("tokens"))

src = (Path(__file__).resolve().parent / "asset_studio" / "live.py").read_text(encoding="utf-8")
ok("the bridge falls through to the closure hunt", "hunt_closures" in src and "asyncio.wait_for(hunt_closures(s)" in src)
routes = (Path(__file__).resolve().parent / "asset_studio" / "routers" / "live.py").read_text(encoding="utf-8")
ok("the three routes exist", all(('"/%s"' % r) in routes for r in ("goto", "where", "locate")))

print("\n  %d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAIL  " + f)
sys.exit(1 if fails else 0)
