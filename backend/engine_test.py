"""Does the engine window get the truth, and can it be talked into reading the wrong file?

Two halves.

The FACTS: a generation is recorded beside its sheet with the code intact, the history groups it
the way the window shelves it, and the project list finds the games — including the ones that live
in a subfolder, which is most of them here.

The GUARDS: three of these endpoints take a path or an id from the caller and turn it into a file
read. Those are worth more test than the rest of the module put together, because a mistake there
is not a wrong answer on screen — it is the contents of an unrelated file.
"""
import io
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import engine                    # noqa: E402
from asset_studio.config import DATA_DIR           # noqa: E402

ok = fail = skip = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s — %s" % (name, why))


SLUG = "enginetest-%d" % int(time.time())
SHEET_DIR = DATA_DIR / "live" / SLUG
SHEET_DIR.mkdir(parents=True, exist_ok=True)
SHEET = SHEET_DIR / "forge-1.png"
SHEET.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)      # enough to be a file with a .png suffix

CODE = "const g = new THREE.Group(); add(g); return 1;"

try:
    print("A generation is recorded beside its picture")
    gid = engine.record(str(Path.cwd()), "test asset", CODE, "three",
                        {"triangles": 36, "materials": 1, "framed": "fit"},
                        ["3q", "front"], str(SHEET), True)
    check("record returns an id", gid == "%s/forge-1" % SLUG, gid)
    check("and writes the record beside the sheet", SHEET.with_suffix(".json").exists())

    got = engine.generation(gid)
    check("the generation comes back", got.get("ok") is True, got.get("error"))
    # The code is the whole point. Without it a sheet is a photograph and cannot be re-run.
    check("with the code intact", got.get("code") == CODE, got.get("code"))
    check("and its stats", (got.get("stats") or {}).get("triangles") == 36, got.get("stats"))

    print("\nA path from the caller never becomes an arbitrary file read")
    for bad in ("../../../../windows/win.ini", "..%2f..%2fsecrets", "C:/Windows/win.ini",
                "slug/../../escape", "slug\\..\\escape", "", "onlyoneparta",
                "a/b/c", "sl ug/forge-1", "slug/forge-1;rm"):
        r = engine.generation(bad)
        if r.get("ok"):
            check("generation refuses %r" % bad, False, r)
            break
    else:
        check("generation refuses every traversal shape tried", True)
    check("…and still accepts the real one", engine.generation(gid).get("ok") is True)

    check("a sheet inside the data directory is served",
          engine.sheet_path(str(SHEET)) is not None)
    outside = Path("C:/Windows/win.ini")
    check("a file outside it is not",
          engine.sheet_path(str(outside)) is None)
    check("nor is a non-image inside it",
          engine.sheet_path(str(SHEET.with_suffix(".json"))) is None)

    print("\nThe history shelves what was made")
    h = engine.history(limit=500)
    ours = [r for r in h["items"] if r.get("id") == gid]
    check("the new generation is in the history", len(ours) == 1, len(ours))
    check("marked replayable, because the code was kept",
          bool(ours and ours[0].get("replayable")), ours[:1])
    check("grouped by kind", "forge" in h["by_kind"], h["by_kind"])
    check("grouped by day", len(h["by_day"]) >= 1, list(h["by_day"])[:3])
    check("grouped by project", len(h["by_project"]) >= 1, list(h["by_project"])[:3])
    check("a kind filter really filters",
          all(r["kind"] == "forge" for r in engine.history(limit=50, kind="forge")["items"]))
    check("the limit is honoured", len(engine.history(limit=3)["items"]) <= 3)

    print("\nThe games")
    pr = engine.projects(fresh=True)
    check("the project list comes back", pr.get("ok") is True, pr.get("error"))
    games = [p for p in pr["projects"] if p["game"]]
    if not games:
        # A new PC has no projects yet: that says something about the desk, not the engine.
        skipped("some of them are games", "no game projects on this PC yet")
    else:
        check("some of them are games", len(games) >= 1, len(games))
    # The commonest layout here: the repo holds the game in a subfolder beside its tools.
    subs = [p for p in games if p["sub"]]
    check("a game in a subfolder is found where it actually lives",
          all(p["root"].endswith(p["sub"]) for p in subs), [(p["name"], p["sub"]) for p in subs[:3]])
    check("an engine is named for the forgeable ones",
          all(p["engine"] in ("three", "playcanvas") for p in games if p["forgeable"]),
          [(p["name"], p["engine"]) for p in games if p["forgeable"]][:4])
    # A home directory with a stray node_modules is not a game. It was reported as one.
    home = str(Path.home()).lower()
    check("the home directory is never a game",
          not any(p["path"].lower() == home and p["game"] for p in pr["projects"]))
    # The window polls this; the first call may probe the disk, the rest must not.
    engine.projects(fresh=True)
    t0 = time.time()
    engine.projects(fresh=True)
    warm = (time.time() - t0) * 1000
    check("a warm list is fast enough to poll", warm < 400, "%.0f ms" % warm)

    print("\nThe engine module is served from the project, and only from it")
    real = [p for p in games if p["forgeable"]]
    if not real:
        skipped("the project's own engine build is found", "no three.js or PlayCanvas project here")
    else:
        mp = engine.module_path(real[0]["root"])
        check("the build is found", mp is not None and mp.exists(), str(mp))
        check("and it is inside that project",
              mp is not None and str(mp).lower().startswith(real[0]["root"].lower()), str(mp))
    # An EMPTY folder, not the data directory. The data directory used to answer None only because
    # the look was `node_modules` and nothing else; it holds `enginetest/`, whose pages keep
    # three.module.js and phaser.min.js beside them, and that folder genuinely IS a three project
    # under the rule the window needs. Asserting on a folder that happens to contain no engine
    # tested the accident; this tests the intent.
    _empty = DATA_DIR / "tmp" / "engine_test_empty"
    _empty.mkdir(parents=True, exist_ok=True)
    check("a folder with no engine gets nothing", engine.module_path(str(_empty)) is None)
    check("a folder that does not exist gets nothing",
          engine.module_path("C:/definitely/not/here") is None)

    # AN ENGINE KEPT BESIDE THE PAGE COUNTS. The A/B harness is one file and one page, no
    # package.json anywhere, and the window told the user "no three.js build found for this
    # project" - true of node_modules, false of the project. That is the shape a hand-written
    # page, a vendored build and every scratch harness have.
    _loose = DATA_DIR / "tmp" / "engine_test_loose"
    _loose.mkdir(parents=True, exist_ok=True)
    (_loose / "three.module.js").write_text("export const REVISION = '169';" + chr(10),
                                            encoding="utf-8")
    check("a build beside the page is found",
          str(engine.module_path(str(_loose)) or "").endswith("three.module.js"),
          engine.module_path(str(_loose)))
    check("and the project reads as that engine",
          engine._engine_in(_loose).get("engine") == "three", engine._engine_in(_loose))
    check("a sibling of that build is served too, and only from its own folder",
          engine.module_path(str(_loose), "three", "three.core.js") is None
          and engine.module_path(str(_loose), "three", "../../settings.json") is None)
    # A generation records whatever project the forge was called with. For an arena-style repo
    # that is the workspace ROOT while node_modules sits a level down, and a straight
    # <project>/node_modules look failed on exactly the games this feature is for.
    subs = [p for p in games if p["sub"] and p["forgeable"]]
    if not subs:
        skipped("a game in a subfolder resolves from its workspace root", "none here")
    else:
        check("a game in a subfolder resolves from its workspace root",
              engine.module_path(subs[0]["path"]) is not None, subs[0]["name"])
    check("a sibling cannot walk out of the package",
          engine.module_path(str(Path.cwd()), "three", "../../../../windows/win.ini") is None)

    print("\nA split engine build still imports")
    # three.js became a split build at r163: three.module.js opens with
    # `import … from './three.core.js'`. Served as one file, the browser resolved that against
    # /api/engine/ and got a 404 — every three project from r163 on failed to load.
    three = [p for p in games if p["module"] == "three"]
    if not three:
        skipped("relative imports are pointed back at the endpoint", "no three project here")
    else:
        hit = None
        for p in three:
            got = engine.module_source(p["path"], "three")
            if got and "/api/engine/module?project=" in got[0]:
                hit = (p, got[0])
                break
        if hit is None:
            # Every three here is pre-r163. That is a fine outcome, not a failure.
            src = engine.module_source(three[0]["path"], "three")
            check("a single-file build is served unchanged",
                  bool(src) and "/api/engine/module?project=" not in src[0], three[0]["name"])
        else:
            p, text = hit
            import re as _re
            left = _re.findall(r"""\bfrom\s*['"]\.{1,2}/""", text)
            check("the sibling import is rewritten to this endpoint", True, p["name"])
            check("and no relative import is left to 404 on", not left, left[:3])
            # The sibling's path is whatever the rewrite named: `build/three.core.js` in an npm
            # install, `three.core.js` in a folder that vendors three beside its index.html.
            import urllib.parse as _up
            _m = _re.search(r"file=([^&'\"]+)", text)
            _sib = _up.unquote(_m.group(1)) if _m else "build/three.core.js"
            check("the sibling itself is served",
                  engine.module_source(p["path"], "three", _sib) is not None, _sib)

    print("\nWhat is happening now")
    st = engine.state()
    check("state answers", st.get("ok") is True, st)
    check("it reports the switch", isinstance(st.get("enabled"), bool))
    check("it counts recent generations", st.get("recent_generations", 0) >= 1,
          st.get("recent_generations"))
    check("and names the latest", (st.get("latest") or {}).get("label") == "test asset",
          st.get("latest"))
finally:
    shutil.rmtree(SHEET_DIR, ignore_errors=True)

print("\nShelves: what it is and what it depicts")
from asset_studio import engine_tags as tags   # noqa: E402
s1, _ = tags.guess_subject("dino v3", "const g = new THREE.Group(); g.name='dino'", ["skin", "sail", "tooth"])
check("a dino is a creature", s1 == "creature", s1)
check("a tower is a building", tags.guess_subject("stone tower")[0] == "building")
check("an unnamed run with nothing to go on is 'other'", tags.guess_subject("", "const m = 1;")[0] == "other")
check("a smoke test is a test", tags.guess_subject("forge test")[0] == "test")
check("but a test OF a dino is still a dino", tags.guess_subject("dino test", "", ["skin", "sail"])[0] == "creature")
check("camelCase names are read", tags.guess_subject("", "function buildKnightHelmet(){}")[0] == "character")
check("a review sheet is a sheet", tags.guess_type("review") == "sheet")
check("code with fog and two lights is a scene",
      tags.guess_type("forge", "", "new THREE.Fog(1,2,3); new THREE.DirectionalLight(); new THREE.AmbientLight();") == "scene")
check("one thing is an asset", tags.guess_type("forge", "lantern", "add(new THREE.Mesh())") == "asset")
check("a run kept without code is a picture", tags.guess_type("forge", "x", "", has_code=False) == "picture")
c1 = tags.classify("forge", "hero", "", [], tags=["Character", "player 1"])
check("an agent's tag decides", c1["subject"] == "character" and c1["subject_by"] == "agent", c1)
check("tags are cleaned", c1["tags"] == ["character", "player-1"], c1["tags"])
rec1 = {"kind": "forge", "label": "dino", "code": "x", "stats": {}, "subject": "building", "subject_by": "user"}
tags.apply(rec1)
check("a person's choice is never overwritten", rec1["subject"] == "building")
rec2 = {"kind": "forge", "label": "dino", "code": "x", "stats": {"material_names": ["skin"]}}
check("a bare record is filled in", tags.apply(rec2) and rec2["subject"] == "creature" and rec2["type"] == "asset", rec2)
h = engine.history(limit=5, type="sheet")
check("the history answers the type filter", h.get("ok") and all(r["type"] == "sheet" for r in h["items"]))
check("and counts the shelves", isinstance(h.get("by_type"), dict) and isinstance(h.get("by_subject"), dict))

print("\nThe Library: what a project already has")
import tempfile as _tf
from asset_studio import assets_index as _ai
_proj = Path(_tf.mkdtemp(prefix="engine-lib-"))
(_proj / "src").mkdir()
(_proj / "assets" / "dinos").mkdir(parents=True)
(_proj / "node_modules" / "x").mkdir(parents=True)
(_proj / "src" / "economy.js").write_text("""
export const SPECIES = [
  { id: 'trex', name: 'Baby T-Rex', icon: 'x', color: 0xff0000, scale: 1.0, belly: 0xffffff },
  { id: 'bronto', name: 'Lazy Bronto', icon: 'x', color: 0x00ff00, scale: 1.5, belly: 0xffffff },
];
export const UPGRADES = [
  { id: 'power', name: 'POWER', icon: 'x', base: 1, per: 2 },
  { id: 'speed', name: 'SPEED', icon: 'x', base: 1, per: 2 },
];
export function upgradeCost(u, lvl) { return u.base * lvl; }
""", encoding="utf-8")
(_proj / "src" / "dinos.js").write_text("""
import * as THREE from 'three';
export function buildDino(spec) { const g = new THREE.Group(); g.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial())); return g; }
export function toonMat(c) { return new THREE.MeshToonMaterial({ color: c }); }
export function lerp(a, b, t) { return a + (b - a) * t; }
export class Dino { constructor(spec) { this.mesh = buildDino(spec); } }
""", encoding="utf-8")
(_proj / "src" / "tower.ts").write_text("export const makeStoneTower = (h: number) => { const e = new pc.Entity('tower'); e.addComponent('render', { type: 'box' }); return e; };\n", encoding="utf-8")
(_proj / "assets" / "dinos" / "trex.glb").write_bytes(b"glTF" + b"\0" * 32)
(_proj / "assets" / "wall.png").write_bytes(b"\x89PNG" + b"\0" * 16)
(_proj / "node_modules" / "x" / "index.js").write_text("export function buildJunk(){ return new THREE.Mesh(); }", encoding="utf-8")
try:
    lib = _ai.list_assets(str(_proj), fresh=True)
    names = {(a["type"], a["name"]) for a in lib["items"]}
    check("the species table is listed, one entry each", ("spec", "Baby T-Rex") in names and ("spec", "Lazy Bronto") in names, sorted(names))
    check("an upgrade table is not an asset", not any(t == "spec" and n in ("POWER", "SPEED") for t, n in names), sorted(names))
    check("the builder is listed", ("code", "buildDino") in names)
    check("a class that builds is listed", ("code", "Dino") in names)
    check("a bare helper is not", ("code", "lerp") not in names and ("code", "upgradeCost") not in names)
    check("a material helper is shelved as material", any(a["name"] == "toonMat" and a["subject"] == "material" for a in lib["items"]))
    check("a PlayCanvas arrow builder is listed", ("code", "makeStoneTower") in names)
    check("model and image files are listed", ("model", "trex") in names and ("image", "wall") in names)
    check("node_modules is skipped", ("code", "buildJunk") not in names)
    byn = {a["name"]: a for a in lib["items"]}
    check("a dino is a creature from its table and folder", byn["Baby T-Rex"]["subject"] == "creature" and byn["trex"]["subject"] == "creature", (byn["Baby T-Rex"]["subject"], byn["trex"]["subject"]))
    check("a stone tower is a building", byn["makeStoneTower"]["subject"] == "building", byn["makeStoneTower"]["subject"])
    check("the engine is read off the file", byn["buildDino"]["engine"] == "three" and byn["makeStoneTower"]["engine"] == "playcanvas")
    f2 = _ai.list_assets(str(_proj), subject="creature", q="rex")
    check("the shelves and the search filter", {a["name"] for a in f2["items"]} == {"Baby T-Rex", "trex"}, [a["name"] for a in f2["items"]])
    check("counts follow the search, not the shelf", f2["by_type"].get("spec") == 1 and f2["by_type"].get("model") == 1, f2["by_type"])
    f3 = _ai.list_assets(str(_proj), subject="creature")
    check("and without a search they cover the whole project", f3["by_type"].get("spec") == 2 and f3["by_type"].get("code") >= 3, f3["by_type"])
finally:
    shutil.rmtree(_proj, ignore_errors=True)

print("\nWhat a model file holds, and which of its nodes an entry is")
# rot-rush ships a clip beside every model — `Labubu_idle.glb`, eleven bones and one animation —
# and the Add panel placed one as an empty group it called "placed". And one GLB holds a
# character and its repaints, so a table entry names the node the game draws.
import json as _json
import struct as _struct


def _glb(doc: dict) -> bytes:
    body = _json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    return b"glTF" + _struct.pack("<II", 2, 20 + len(body)) + _struct.pack("<II", len(body), 0x4E4F534A) + body


_m = Path(_tf.mkdtemp(prefix="engine-models-"))
try:
    (_m / "src").mkdir()
    (_m / "assets" / "brainrots").mkdir(parents=True)
    (_m / "assets" / "brainrots" / "body.glb").write_bytes(_glb({"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {}}]}]}))
    (_m / "assets" / "brainrots" / "body_idle.glb").write_bytes(_glb({"asset": {"version": "2.0"}, "animations": [{"channels": [], "samplers": []}]}))
    (_m / "assets" / "brainrots" / "broken.glb").write_bytes(b"glTF" + b"\0" * 32)
    (_m / "src" / "roster.ts").write_text("""
export const ROSTER = [
  { key: "Alpha", name: "Alpha", model: '../assets/brainrots/body.glb', nodes: ["Alpha", "Alpha_Rare"], parts: ["Crown"] },
  { key: "Beta", name: "Beta", model: '../assets/brainrots/body.glb', nodes: ["Beta.001"], parts: [{ x: 1 }] },
];
""", encoding="utf-8")
    mods = _ai.list_assets(str(_m), type="model", fresh=True)["items"]
    by = {a["name"]: a for a in mods}
    check("a model file says how many meshes it has", by["body"].get("meshes") == 1 and by["body"].get("anims") == 0, by["body"])
    check("a clip file says it has none, and is tagged", by["body_idle"].get("meshes") == 0 and "animation" in by["body_idle"]["tags"], by["body_idle"])
    check("a file with meshes comes before one without", [a["name"] for a in mods].index("body") < [a["name"] for a in mods].index("body_idle"), [a["name"] for a in mods])
    check("a file that cannot be read is listed, and claims nothing", "meshes" not in by["broken"] and "animation" not in by["broken"]["tags"], by["broken"])
    specs = {a["name"]: a for a in _ai.list_assets(str(_m), type="spec")["items"]}
    check("an entry names its nodes, the drawn one first", specs["Alpha"].get("nodes") == ["Alpha", "Alpha_Rare"], specs["Alpha"])
    check("and the parts it always shows", specs["Alpha"].get("parts") == ["Crown"], specs["Alpha"])
    check("a node name is kept as the game wrote it", specs["Beta"].get("nodes") == ["Beta.001"], specs["Beta"])
    check("a list of objects is not a list of names", "parts" not in specs["Beta"], specs["Beta"])
finally:
    shutil.rmtree(_m, ignore_errors=True)

print("\nThe index, on a game shaped like the ones it used to get wrong")
# A classic game: script files wrapped in an IIFE, tables hung off one namespace object, a folder
# of sprites, and a backup folder whose name starts with an underscore — which sorts first, and
# used to eat the whole item budget before the walk ever reached the game's own code.
_g = Path(_tf.mkdtemp(prefix="engine-index-"))
try:
    (_g / "js").mkdir()
    (_g / "assets" / "sprites").mkdir(parents=True)
    (_g / "assets" / "textures").mkdir(parents=True)
    (_g / "audio").mkdir()
    (_g / "_asset_backup_orig" / "h").mkdir(parents=True)
    (_g / "js" / "data.js").write_text(
        "'use strict';\n"
        "const DATA = {};\n"
        "DATA.EVOLUTIONS = [\n"
        "  { id: 'caveman', name: 'CAVEMAN', body: { c: '#8a5' }, weapon: 'club' },\n"
        "  { id: 'viking',  name: 'VIKING',  body: { c: '#59a' }, weapon: 'axe' },\n"
        "  { id: 'knight',  name: 'KNIGHT',  body: { c: '#aaa' }, weapon: 'sword' },\n"
        "];\n", encoding="utf-8")
    (_g / "js" / "render.js").write_text(
        "(function () {\n"
        "  'use strict';\n"
        "  function buildAvatar(spec) {\n"
        "    const g = new THREE.Group();\n"
        "    g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)));\n"
        "    return g;\n"
        "  }\n"
        "  function smoothstep(a, b, x) { return new THREE.Vector3(x, x, x); }\n"
        "  function fbm(x) { return new THREE.Vector3(x, x, x); }\n"
        "})();\n", encoding="utf-8")
    for _i in range(120):
        (_g / "assets" / "sprites" / ("frame%03d.png" % _i)).write_bytes(b"x")
    for _n in ("wall_albedo.png", "wall_normal.png", "floor_rough.png"):
        (_g / "assets" / "textures" / _n).write_bytes(b"x")
    (_g / "audio" / "hit.ogg").write_bytes(b"x")
    for _i in range(200):
        (_g / "_asset_backup_orig" / "h" / ("old%03d.png" % _i)).write_bytes(b"x")

    _got = _ai.list_assets(str(_g), fresh=True)
    _items = _got.get("items") or []
    _names = {i["name"] for i in _items}
    _kinds = {}
    for _i2 in _items:
        _kinds[_i2["type"]] = _kinds.get(_i2["type"], 0) + 1
    check("a table hung off a namespace object is found",
          "CAVEMAN" in _names and "KNIGHT" in _names, sorted(n for n in _names if n.isupper())[:5])
    check("a builder inside an IIFE is found", "buildAvatar" in _names)
    check("...and the maths helpers beside it are not",
          "smoothstep" not in _names and "fbm" not in _names,
          [n for n in ("smoothstep", "fbm") if n in _names])
    check("a backup folder is not the project's art",
          not any("_asset_backup_orig" in i["file"] for i in _items),
          [i["file"] for i in _items if "backup" in i["file"]][:3])
    check("textures are told apart from sprites", _kinds.get("texture", 0) == 3, _kinds)
    check("audio is listed too", _kinds.get("audio", 0) == 1, _kinds)
    check("one sprite folder cannot crowd out the rest",
          _kinds.get("image", 0) <= _ai.MAX_PER_FOLDER and _kinds.get("spec", 0) == 3, _kinds)
    check("...and the totals say how many there really are",
          (_got.get("totals") or {}).get("image") == 120 and bool(_got.get("capped")),
          [_got.get("totals"), _got.get("capped")])
finally:
    shutil.rmtree(_g, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\nA workspace that holds more than one game")
# The real shape of the research folder here: two games side by side, each with its own
# package.json and its own dev script, and a legacy copy of one of them that is not a game at all.
_w = Path(_tf.mkdtemp(prefix="engine-games-"))
try:
    for name, port in (("rot-haul", 5178), ("rot-rush", 5179)):
        d = _w / name / "src" / "render"
        d.mkdir(parents=True)
        (_w / name / "package.json").write_text(
            '{"scripts": {"dev": "vite --port %d"}, "dependencies": {"playcanvas": "^2"}}' % port,
            encoding="utf-8")
        (d / "props.ts").write_text(
            "import { shade } from '../sim/palette.ts';\n"
            "const NATURE = [{ id: 'nature.tree', name: 'Tree', size: [1,2,1], build(p){ return []; } },\n"
            "                { id: 'nature.rock', name: 'Rock', size: [1,1,1], build(p){ return []; } }];\n"
            "export const PROPS = [...NATURE];\n", encoding="utf-8")
        (_w / name / "src" / "sim").mkdir(parents=True)
        (_w / name / "src" / "sim" / "palette.ts").write_text("export const SHADE = 1;\n", encoding="utf-8")
    (_w / "rot-haul-LEGACY").mkdir()
    (_w / "rot-haul-LEGACY" / "package.json").write_text('{"scripts": {"dev": "vite"}}', encoding="utf-8")

    _res = engine.games(str(_w), fresh=True)
    _subs = [g["sub"] for g in _res.get("games") or []]
    check("every game in the workspace is found, not just the first",
          "rot-haul" in _subs and "rot-rush" in _subs, _subs)
    check("...and a legacy copy is not one of them", "rot-haul-LEGACY" not in _subs, _subs)
    check("each one knows its own dev script",
          all(g["dev_script"] == "dev" for g in _res["games"] if g["sub"]), _res["games"])
    check("the game that owns a file is the one asked for it",
          engine.game_for(str(_w), "rot-rush").get("sub") == "rot-rush",
          engine.game_for(str(_w), "rot-rush").get("sub"))
    check("...including a file deep inside it",
          engine.game_for(str(_w), "rot-rush/src/render/props.ts").get("sub") == "rot-rush")

    print("\nOne shelf per game, and what a builder needs to be called")
    _all = _ai.list_assets(str(_w), fresh=True)
    check("the shelf says which games it found", set(_all.get("games") or []) >= {"rot-haul", "rot-rush"},
          _all.get("games"))
    _rush = _ai.list_assets(str(_w), root="rot-rush")
    check("asking for one game gives only that game's things",
          bool(_rush["items"]) and all(i["root"] == "rot-rush" for i in _rush["items"]),
          sorted({i["root"] for i in _rush["items"]}))
    check("...and half of what the whole workspace has",
          _rush["total"] * 2 == _all["total"], (_rush["total"], _all["total"]))
    _tree = next((i for i in _rush["items"] if i.get("key") == "nature.tree"), None)
    check("an entry of a table the module never exports is still indexed", _tree is not None)
    check("...and carries the game that owns it", (_tree or {}).get("root") == "rot-rush")
    check("...and the file's own imports, so its real palette can be read too",
          "rot-rush/src/sim/palette.ts" in ((_tree or {}).get("deps") or []), (_tree or {}).get("deps"))

    print("\nThe thumbnail renderer calls the shared convention, not its own copy")
    from asset_studio import live as _live
    _js = _live._thumb_js(_tree or {})
    check("it imports asset-open.js from the Studio", "/asset-open.js" in _js)
    check("...and hands it the whole index row", "nature.tree" in _js and "rot-rush" in _js)
    check("...and no longer carries a builder search of its own",
          "looksBuilder" not in _js and "__partsToObject" not in _js)
    check("...but still knows how THIS page loads a model file",
          "instantiateRenderEntity" in _js and "GLTFLoader" in _js)
finally:
    shutil.rmtree(_w, ignore_errors=True)

# ---------------------------------------------------------------- one three, by URL
#
# The viewport takes the first three that loads; the loader used to pick by a rule of its own.
# On the Ballerina GLB those were r160 and r183, the r160 renderer threw on r183 materials every
# frame, and the viewport was blank under a full outliner. Now the loader is handed the URL the
# viewport imported, and that URL wins.
print("\nThe loader binds to the three the VIEWPORT loaded")
_own = "/vendor/three/three.module.js"
_s, _ = engine.loader_source("C:/nowhere-at-all", "GLTFLoader.js", three=_own)
check("from 'three' becomes the URL it was handed", ("'%s'" % _own) in _s or ('"%s"' % _own) in _s)
check("...and no bare 'three' is left", "from 'three'" not in _s and 'from "three"' not in _s)
check("...and the loader's own helpers carry the same three",
      "&three=%2Fvendor%2Fthree%2Fthree.module.js" in _s, "the helpers would bind by the old rule")
_ep = "/api/engine/module?project=C%3A%5CDino%20Smash&engine_kind=three"
_s, _ = engine.loader_source("C:/nowhere-at-all", "GLTFLoader.js", three=_ep)
check("this backend's module endpoint is accepted", ("'%s'" % _ep) in _s)
_dev = "http://127.0.0.1:5179/node_modules/three/build/three.module.js"
_s, _ = engine.loader_source("C:/nowhere-at-all", "GLTFLoader.js", three=_dev)
check("a loopback dev server is accepted whole", ("'%s'" % _dev) in _s)
for _bad in ("https://cdn.example/three.module.js", "//evil.example/three.js", "javascript:alert(1)",
             "/vendor/three/three.module.js'; import('x", "/etc/passwd", "/api/engine/module?x=1 y"):
    _s, _ = engine.loader_source("C:/nowhere-at-all", "GLTFLoader.js", three=_bad)
    check("refused, falls back to the old rule: %r" % _bad[:34],
          _bad not in _s and engine.STUDIO_THREE in _s and "&three=" not in _s)
_s, _ = engine.loader_source("C:/nowhere-at-all", "GLTFLoader.js")
check("nothing given: the old rule, unchanged", engine.STUDIO_THREE in _s and "&three=" not in _s)

print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)
