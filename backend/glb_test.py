# -*- coding: utf-8 -*-
"""The decoder, end to end: a Draco model becomes one anything can open."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from asset_studio import glb, engine  # noqa: E402

import os                                             # noqa: E402

ok = fail = skip = 0


def check(name, cond, got=None):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, repr(got)[:160]))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s - %s" % (name, why))


# THE DRACO HALF NEEDS A REAL COMPRESSED MODEL, and a checkout carries none: a Draco GLB is
# megabytes and belongs to somebody's game, not to this repository. Point STUDIO_TEST_GLB at a
# project that holds one to run this half. On a machine without it the half is skipped and says
# so, because a test that only passes at one desk is a test nobody trusts.
B = os.environ.get("STUDIO_TEST_GLB") or \
    r"C:\Users\Administrator\Desktop\brainrot 3d game research crazygames"
MODEL = os.environ.get("STUDIO_TEST_GLB_FILE") or \
    "rot-rush/src/assets/brainrots/bombardinocrocodillo.glb"
src = glb.resolve(B, MODEL)
print("\nA compressed model, made openable")
if not src:
    skipped("a Draco model decodes", "no model at " + os.path.join(B, MODEL))
else:
    check("the file is found inside the project", bool(src))
    check("...and it really is Draco", "KHR_draco_mesh_compression" in glb.extensions(src), glb.extensions(src))
    r = glb.plain(str(src))
    check("it decodes", r.get("ok") and r.get("decoded"), r)
    out = Path(r["path"])
    check("...into a file with no compression left", not glb.needs_decode(out), glb.extensions(out))
    check("...that is bigger, as decompression is", out.stat().st_size > src.stat().st_size,
          [src.stat().st_size, out.stat().st_size])
    r2 = glb.plain(str(src))
    check("the second call is the cache", r2.get("cached") is True, r2)
    check("a model that needs nothing is served as itself",
          glb.plain(str(out))["path"] == str(out), glb.plain(str(out)))

# This one needs no model at all. It is the containment rule, and it must hold everywhere.
check("nothing outside the project can be asked for",
      glb.resolve(B, "../../../Windows/System32/drivers/etc/hosts") is None)

# THE TWO KINDS OF PROJECT, both built here so this holds on any machine. A game that ships three
# must get ITS three — a mesh the loader builds then joins the same scene the viewport is already
# showing, and a second copy would produce meshes that scene refuses. A game that has none must
# still be able to open a .glb, because a model is a FILE and showing one shares nothing with the
# game. Getting this backwards is what drew an empty grid for every PlayCanvas project.
import re                                            # noqa: E402
import tempfile                                      # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="studio-three-"))
HAS3 = _TMP / "has-three"
NO3 = _TMP / "no-three"
(HAS3 / "node_modules" / "three" / "build").mkdir(parents=True, exist_ok=True)
(HAS3 / "node_modules" / "three" / "build" / "three.module.js").write_text(
    "export const Group = 1;\n", encoding="utf-8")
NO3.mkdir(parents=True, exist_ok=True)

print("\nWhich three a project gets")
check("a project that ships three gets its own",
      engine.three_url(str(HAS3)).startswith("/api/engine/module"), engine.three_url(str(HAS3)))
check("a project with none gets the Studio's",
      engine.three_url(str(NO3)) == engine.STUDIO_THREE, engine.three_url(str(NO3)))

print("\nA loader bound to whichever three the viewport will run")
t, media = engine.loader_source(str(HAS3))
check("the loader is served", len(t) > 50_000 and media == "text/javascript", len(t))
check("...with no bare `three` import left", not re.search(r"from\s*['\"]three['\"]", t))
check("...pointing at this project's engine", "/api/engine/module?project=" in t)
check("...and its own two helpers coming back here", t.count("/api/engine/loader?project=") >= 2)

t2, _ = engine.loader_source(str(NO3))
check("a project with no three still gets a working loader", len(t2) > 50_000, len(t2))
check("...with no bare `three` left to fail on", not re.search(r"from\s*['\"]three['\"]", t2))
check("...bound to the Studio's own three, not to a 404", engine.STUDIO_THREE in t2)

check("its helpers are served too", bool(engine.loader_source(B, "SkeletonUtils.js")))
# THE EXPORTER. /api/live/export used to 404 on it and then import three itself as "the exporter"
# on a page that loads three from its root ("Ex is not a constructor"). Served like the loader,
# and bound to the exact three the forge page names.
_ex = engine.loader_source(str(NO3), "GLTFExporter.js", three="http://127.0.0.1:5999/three.module.js")
check("the glTF exporter is served", bool(_ex) and "class GLTFExporter" in _ex[0])
check("...bound to the page's own three", bool(_ex) and "'http://127.0.0.1:5999/three.module.js'" in _ex[0]
      and not re.search(r"from\s*['\"]three['\"]", _ex[0]))
from asset_studio import live_forge as _lf  # noqa: E402
check("the forge takes a module only if it has the class",
      "F.exampleModule = async function" in _lf.FORGE and "typeof C === 'function'" in _lf.FORGE
      and _lf.FORGE.count("F.exampleModule(") == 2)
check("...and the engine-relative guess only for a build/ layout",
      "if (/build\\/three[^/]*$/.test(eng))" in _lf.FORGE)
check("and nothing else is", engine.loader_source(B, "../../../secrets.js") is None)

print("\nWhat the workspace says a model IS")
# THE CONTRACT THE EDITOR READS. A .glb has no text, and `read_file` correctly returns none. The
# Engine window used to default that missing field to "" and open an editor over the emptiness --
# the file named in the header, nothing in the viewport, and "the asset did not build" as the only
# word on the subject. The window's half of this is held in frontend/tests/openmodel.test.ts.
from asset_studio import workspace as _ws  # noqa: E402

_M = _TMP / "says-model"
_M.mkdir(parents=True, exist_ok=True)
# read_file refuses a path outside every open folder, and a temp dir is outside all of them. What
# is under test here is how a file is CLASSIFIED, not who may look at it, so the gate is told the
# truth about this one directory and put back afterwards.
_was = _ws._browse_roots
_ws._browse_roots = lambda fresh=False: [_M.resolve()]
try:
    _glb = _M / "thing.glb"
    _glb.write_bytes(b"glTF" + bytes(16))
    _txt = _M / "thing.js"
    _txt.write_text("export const build = () => null;\n", encoding="utf-8")

    _r = _ws.read_file(str(_glb))
    check("a .glb is a model", _r.get("kind") == "model", _r.get("kind"))
    check("...and carries no text at all", "text" not in _r, sorted(_r))
    check("...but says how big it is", _r.get("size", 0) > 0, _r.get("size"))

    _r2 = _ws.read_file(str(_txt))
    check("a source file still comes back as text", _r2.get("kind") == "text", _r2.get("kind"))
    check("...with the text in it", _r2.get("text", "").startswith("export const build"))
finally:
    _ws._browse_roots = _was

print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
raise SystemExit(0 if not fail else 2)
