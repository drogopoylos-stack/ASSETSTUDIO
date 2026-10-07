"""Seeing the assets, finding the code, and a forge that heals - package D of the agent-scene-tools
contract (data/contracts/agent-scene-tools.md).

Four halves, each held to the facts that forced it:

  CODE HINTS  - `pillar-2` is named by `'pillar-' + (i + 1)`, not by the string "pillar-2", so a
                hint that only greps for the name finds nothing. The tiers, the format, the skipped
                folders, the cache and the speed on a real 4.6 MB workspace.
  FIND        - `code=1` puts those hints on every match, preferring the game the tab serves.
  THE HEAL    - a tab opened before its import map fails every bare `import 'three'` until it is
                reloaded. The state machine, against a fake page: reload only when it can help,
                an import map when it cannot, and an answer with the one-line fix otherwise.
  THE SHEET   - one labelled grid instead of N thumbnails, inside the pixel budget a vision model
                keeps, with a failed asset drawn as a tile that says why.

Plain script, PASS/FAIL lines and a total, like engine_test.py. Runs with no browser; the real-game
checks are skipped when those folders are not on this machine.
"""
import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio.config import DATA_DIR              # noqa: E402
from asset_studio import code_hints as CH              # noqa: E402
from asset_studio import asset_sheet as AS             # noqa: E402
from asset_studio import live as L                     # noqa: E402
from asset_studio import live_forge as LF              # noqa: E402

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
    print("  SKIP  %s  (%s)" % (name, why))


TMP = Path(tempfile.mkdtemp(prefix="pkgD-vis-", dir=str(DATA_DIR / "tmp")))


def write(rel, text):
    p = TMP / "game" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# =============================================================================== code hints
print("\nCode hints: the line that named it")
write("src/main.js", """import * as THREE from 'three';
import { buildPillar } from './assets.js';
// The player walks; the pillars stand.
const player = new THREE.Mesh();
player.name = 'player';
[[0, 0], [1, 1]].forEach(([x, z], i) => {
  const p = buildPillar();
  p.name = 'pillar-' + (i + 1);
});
const e = new Entity(`face-${m}`);
const s = new Entity(`${a.key}-still`);
const lb = new THREE.Group(); lb.name = 'label';
function playerFootY() { return 0; }
""")
write("src/assets.js", """import * as THREE from 'three';
export function buildPillar({ height = 3 } = {}) {
  const g = new THREE.Group();
  g.name = 'pillar';
  return g;
}
""")
write("node_modules/fake/index.js", "x.name = 'pillar-'; const player = 1;\n")
write("dist/bundle.js", "x.name = 'player';\n")
write("backup/old.js", "x.name = 'player';\n")
write(".studio/cache.js", "x.name = 'player';\n")
write("src/big.js", "// " + "x" * 820_000 + "\nconst hugeName = 'bigthing';\n")
write("tests/player.test.js", "it('names the player', () => expect(p.name).toBe('player'));\n")
G = str(TMP / "game")
CH.invalidate()

h = CH.hints_for(G, ["player", "pillar-2", "face-idle", "zz-still", "label.003", "/0/2", "", "player"])
check("exact quoted name first: 'player'", h["player"][:1] == ["src/main.js:5: player.name = 'player';"], h["player"])
check("...then the declaration of it", "src/main.js:4: const player = new THREE.Mesh();" in h["player"], h["player"])
check("...and a test file's literal ranks after the game's code",
      not h["player"] or not h["player"][0].startswith("tests/"), h["player"])
check("pillar-2: the numbered stem 'pillar-' first",
      h["pillar-2"][:1] == ["src/main.js:8: p.name = 'pillar-' + (i + 1);"], h["pillar-2"])
check("pillar-2: then the builder's own bare 'pillar'", "src/assets.js:4: g.name = 'pillar';" in h["pillar-2"], h["pillar-2"])
check("pillar-2: then the builder buildPillar",
      "src/assets.js:2: export function buildPillar({ height = 3 } = {}) {" in h["pillar-2"], h["pillar-2"])
check("face-idle: a template head `face-${m}`", any("`face-${m}`" in x for x in h["face-idle"]), h["face-idle"])
check("zz-still: a template tail `${a.key}-still`", any("-still`" in x for x in h["zz-still"]), h["zz-still"])
check("label.003: the duplicate suffix goes, 'label' is found", any("'label'" in x for x in h["label.003"]), h["label.003"])
check("an unnamed object's path key finds nothing", h["/0/2"] == [])
check("an empty name finds nothing, and names are answered once", h[""] == [] and list(h).count("player") == 1)
allh = [x for v in h.values() for x in v]
check("node_modules, dist, a backup folder and .studio are never searched",
      not any(x.startswith(("node_modules/", "dist/", "backup/", ".studio/")) for x in allh), allh)
check("a file over 800 KB is left out", CH.hints_for(G, ["bigthing"])["bigthing"] == [])
check("every hint is '<file>:<line>: <source>' and at most 160 characters",
      all(len(x) <= 160 and x.count(":") >= 2 and x.split(":")[1].isdigit() for x in allh), allh)
hp = CH.hints_for(G, ["player"], per_name=1)
check("per_name is respected", len(hp["player"]) == 1)
check("playerFootY is not a builder of 'player'", not any("playerFootY" in x for x in h["player"]), h["player"])
_said = [i for i, x in enumerate(h["player"]) if x.startswith("src/main.js:3:")]
check("a comment mentioning the word waits until every code tier is used",
      all(i == len(h["player"]) - 1 for i in _said)
      and not any(x.startswith("src/main.js:3:") for x in CH.hints_for(G, ["player"], per_name=2)["player"]),
      h["player"])
long_line = "const t = [" + ", ".join("'x%d'" % i for i in range(60)) + ", 'needle-name', 'y'];\n"
write("src/table.js", long_line)
time.sleep(CH._WALK_TTL + 0.1)
lh = CH.hints_for(G, ["needle-name"])["needle-name"]
check("a long line is cut AROUND the match, not from its start",
      bool(lh) and "'needle-name'" in lh[0] and lh[0].split(": ", 1)[1].startswith("…"), lh)
write("src/assets.js", "\n\n" + (TMP / "game/src/assets.js").read_text(encoding="utf-8"))
time.sleep(CH._WALK_TTL + 0.1)
h2 = CH.hints_for(G, ["pillar-2"])["pillar-2"]
check("an edited file is re-read: the builder's line moved from 4 to 6",
      "src/assets.js:6: g.name = 'pillar';" in h2, h2)
check("...and only the changed file was read again", CH.stats(G)["reread"] <= 1, CH.stats(G))
write("other/src/scene.js", "cam.name = 'camera';\n")
write("zgame/src/scene.js", "cam.name = 'camera';\n")
time.sleep(CH._WALK_TTL + 0.1)
check("without a preference the folders tie alphabetically",
      CH.hints_for(G, ["camera"])["camera"][0].startswith("other/"))
check("prefer puts the served game's line first",
      CH.hints_for(G, ["camera"], prefer="zgame")["camera"][0].startswith("zgame/"))
check("a merged-mesh piece key is searched by its mesh's name",
      CH.hints_for(G, ["player~t12-40"])["player~t12-40"][:1] == h["player"][:1])
_old_max = CH.MAX_FILES
CH.MAX_FILES = 2
CH.invalidate(G)
check("the file ceiling is reported, not silent", CH.stats(G)["truncated"] is True)
CH.MAX_FILES = _old_max
CH.invalidate(G)

PROOF = Path(r"C:/Users/Administrator/Desktop/studio proof game")
ROT = Path(r"C:/Users/Administrator/Desktop/brainrot 3d game research crazygames")
if (PROOF / "src" / "main.js").is_file():
    ph = CH.hints_for(str(PROOF), ["pillar-2", "crystal-3", "player"])
    check("proof game: pillar-2 -> main.js 'pillar-' + (i + 1)",
          bool(ph["pillar-2"]) and "p.name = 'pillar-' + (i + 1);" in ph["pillar-2"][0], ph["pillar-2"])
    check("proof game: crystal-3 -> main.js 'crystal-' + (i + 1), then buildCrystal",
          bool(ph["crystal-3"]) and "'crystal-' + (i + 1)" in ph["crystal-3"][0]
          and any("buildCrystal" in x for x in ph["crystal-3"]), ph["crystal-3"])
    check("proof game: player -> player.name = 'player'",
          bool(ph["player"]) and ph["player"][0].endswith("player.name = 'player';"), ph["player"])
else:
    skipped("proof game hints", "no proof game on this machine")
if (ROT / "rot-rush" / "src").is_dir():
    names = ["runner", "base-props", "tungtungsahur-still", "gear-head-RUNNER", "TungTungSahur_Armature"]
    CH.invalidate(str(ROT))
    t0 = time.perf_counter()
    rh = CH.hints_for(str(ROT), names, prefer="rot-rush")
    cold = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    CH.hints_for(str(ROT), names, prefer="rot-rush")
    warm = (time.perf_counter() - t0) * 1000
    time.sleep(CH._WALK_TTL + 0.1)
    t0 = time.perf_counter()
    CH.hints_for(str(ROT), names, prefer="rot-rush")
    rewalk = (time.perf_counter() - t0) * 1000
    print("        rot-rush workspace: %s, cold %.0f ms, warm %.0f ms, warm with a re-walk %.0f ms"
          % (CH.stats(str(ROT)), cold, warm, rewalk))
    check("rot-rush: under 300 ms warm, even when the walk is redone", warm < 300 and rewalk < 300,
          (warm, rewalk))
    check("rot-rush: runner -> new pc.Entity('runner')",
          bool(rh["runner"]) and "new pc.Entity('runner')" in rh["runner"][0], rh["runner"])
    check("rot-rush: base-props -> this.emit(..., 'base-props', ...)",
          bool(rh["base-props"]) and "'base-props'" in rh["base-props"][0], rh["base-props"])
    check("rot-rush: tungtungsahur-still -> the `${a.key}-still` template",
          bool(rh["tungtungsahur-still"]) and "-still`" in rh["tungtungsahur-still"][0], rh["tungtungsahur-still"])
    check("rot-rush: gear-head-RUNNER -> the `gear-head-${evo.label}` template",
          bool(rh["gear-head-RUNNER"]) and "`gear-head-${" in rh["gear-head-RUNNER"][0], rh["gear-head-RUNNER"])
    check("rot-rush: a node from inside a model finds the spec row that loads it",
          bool(rh["TungTungSahur_Armature"]) and "brainrot-assets.ts" in rh["TungTungSahur_Armature"][0],
          rh["TungTungSahur_Armature"])
else:
    skipped("rot-rush hints", "no brainrot workspace on this machine")

# =============================================================================== find
print("\nfind: code=1 puts the hints on every match")
_real_guard, _real_run, _real_bridge = L._guard, L._run, L._bridge
L._guard = lambda project, forge=False: None
L._run = lambda fn: {"engine": "three", "matches": [{"name": "player", "path": "a"},
                                                     {"name": "pillar-2", "path": "b"}]}
try:
    got = L.find(G, "p", 10, code=True)
    rows = got.get("matches") or []
    check("each match carries `code`", all(isinstance(r.get("code"), list) for r in rows) and len(rows) == 2, got)
    check("...with that name's hints", rows and rows[0]["code"][:1] == CH.hints_for(G, ["player"])["player"][:1], rows)
    got = L.find(G, "p", 10)
    check("without code=1 nothing is added", all("code" not in r for r in got.get("matches") or []), got)
    check("the batch op reaches the same option (find accepts code as a keyword)",
          "code" in L.find.__code__.co_varnames)
finally:
    L._guard, L._run, L._bridge = _real_guard, _real_run, _real_bridge
from asset_studio import preview_server as PS     # noqa: E402
PS._servers["__pkgD_fake__"] = {"port": 65001, "last": time.time()}
PS._servers[str((TMP / "game" / "zgame").resolve())] = {"port": 65002, "last": time.time()}
L._SERVING.clear()
check("the served folder is read from a static server this Studio started",
      L._serving_root(G, "http://127.0.0.1:65002/") == "zgame", L._serving_root(G, "http://127.0.0.1:65002/"))
check("no URL, no preference", L._serving_root(G, "") == "")
PS._servers.pop("__pkgD_fake__", None)
PS._servers.pop(str((TMP / "game" / "zgame").resolve()), None)

# =============================================================================== the heal
print("\nThe heal: what an import failure is, and what fixes it")
chrome = 'TypeError: Failed to resolve module specifier "three". Relative references must start with either "/", "./", or "../".'
check("Chrome's bare-specifier message is an import failure, naming 'three'",
      L.import_failure(chrome) and L.bare_specifier(chrome) == "three")
check("...in single quotes too (an import evaluated as statements)",
      L.bare_specifier("Failed to resolve module specifier 'three/addons/x.js'") == "three/addons/x.js")
check("Safari's and Firefox's wording count",
      L.import_failure("Importing a module script failed.") and
      L.import_failure("error loading dynamically imported module: http://x/a.js"))
check("Chrome's failed fetch counts", L.import_failure("TypeError: Failed to fetch dynamically imported module: http://x/a.js"))
check("an ordinary error does not", not L.import_failure("TypeError: x is not a function"))
mp = L._import_map_for("three/addons/controls/OrbitControls.js",
                       "http://127.0.0.1:5000/node_modules/three/build/three.module.js")
check("the map sends three, three/ and three/addons/ to the forge's own engine",
      mp == {"imports": {"three": "http://127.0.0.1:5000/node_modules/three/build/three.module.js",
                         "three/": "http://127.0.0.1:5000/node_modules/three/",
                         "three/addons/": "http://127.0.0.1:5000/node_modules/three/examples/jsm/"}}, mp)
check("a PlayCanvas map is the one name",
      L._import_map_for("playcanvas", "http://h/pc.mjs") == {"imports": {"playcanvas": "http://h/pc.mjs"}})
write("node_modules/three/package.json", json.dumps({"name": "three", "exports": {".": {"import": "./build/three.module.js"}}}))
fix = L._import_fix(G, chrome)
check("the one-line fix is the import map, with the installed entry",
      '"three":"./node_modules/three/build/three.module.js"' in fix and fix.startswith("add to index.html"), fix)
fix2 = L._import_fix(G, 'Failed to resolve module specifier "cannon-es"')
check("a package that is not installed says npm install", "npm install cannon-es" in fix2, fix2)
write("package.json", json.dumps({"scripts": {"dev": "vite"}}))
fix3 = L._import_fix(G, 'Failed to resolve module specifier "cannon-es"')
check("a game with a dev script is sent to it", "npm run dev" in fix3, fix3)
(TMP / "game" / "package.json").unlink()
check("a missing file's fix names the URL", "http://x/a.js did not load" in L._import_fix(G, "Failed to fetch dynamically imported module: http://x/a.js"))


class FakePage:
    """Just enough of a live tab to drive the heal: it records what was asked of it."""

    def __init__(self, stale=False, served=True, document=False, late_ok=False, engine="three"):
        self.calls, self.raws, self.asks = [], [], []
        self.stale, self.served, self.document = stale, served, document
        self.late_ok, self.engine = late_ok, engine

    async def call(self, method, params=None):
        self.calls.append((method, params or {}))
        return {}

    async def raw(self, expr, wait=True):
        s = str(expr)
        self.raws.append(s[:80])
        if s.startswith("window.__forge ? (window.__forge.version"):
            return LF.FORGE_VERSION
        if "document.readyState" in s:
            return "complete"
        if s == "location.href":
            return "http://127.0.0.1:5000/"
        if s == "!!window.__live":
            return True
        if "engineHref" in s:
            return "http://127.0.0.1:5000/node_modules/three/build/three.module.js"
        if "__forge.ctx().engine" in s:
            return self.engine
        if "__studioImportMap" in s:
            return "inserted"
        return None

    async def ask(self, expr, depth=6):
        s = str(expr)
        self.asks.append(s[:80])
        if s.startswith("__forge.mapState"):
            return {"document": self.document, "served": self.served, "stale": self.stale}
        if s.startswith("__forge.lateMap"):
            return {"ok": self.late_ok, "error": "" if self.late_ok else "Failed to resolve module specifier 'three'"}
        if s.startswith("__forge.ensure"):
            return {"ok": True, "engine": self.engine}
        return {}


E = {"project": G, "url": "http://127.0.0.1:5000/"}
page, st = FakePage(stale=True), {}
r = asyncio.run(L._heal_imports(page, dict(E), chrome, "{}", st))
check("a stale tab (served html maps it, the document does not) is reloaded, and worth a retry",
      r is True and any(m == "Page.reload" for m, _ in page.calls) and "reloaded the tab" in " ".join(st["did"]), st)
check("...the shim is registered for the new document before the reload",
      [m for m, _ in page.calls][:2] == ["Page.addScriptToEvaluateOnNewDocument", "Page.reload"], page.calls)
check("...and the forge is put back in the reloaded page", any(a.startswith("__forge.ensure") for a in page.asks))
page, st = FakePage(stale=False, served=False, late_ok=True), {}
r = asyncio.run(L._heal_imports(page, dict(E), chrome, "{}", st))
check("no map anywhere: NO reload, and a late map is tried",
      r is True and st.get("map") == "late" and not any(m == "Page.reload" for m, _ in page.calls), st)
page, st = FakePage(stale=False, served=False, late_ok=False), {}
r = asyncio.run(L._heal_imports(page, dict(E), chrome, "{}", st))
starts = [p for m, p in page.calls if m == "Page.addScriptToEvaluateOnNewDocument" and "__studioImportMap" in p.get("source", "")]
check("a refused late map goes in at document start, on one reload",
      r is True and st.get("map") == "document-start" and len(starts) == 1
      and sum(1 for m, _ in page.calls if m == "Page.reload") == 1, (st, page.calls))
check("...carrying the map for the forge's own engine URL",
      bool(starts) and "node_modules/three/build/three.module.js" in starts[0]["source"])
page, st = FakePage(stale=False, served=False), {}
r = asyncio.run(L._heal_imports(page, dict(E), 'Failed to resolve module specifier "cannon-es"', "{}", st))
check("a bare name that is not the engine gets neither a reload nor a map",
      r is False and not page.calls and st.get("step") == 2, (st, page.calls))
_real_status = L._status_of
L._status_of = lambda url: 404
page, st = FakePage(), {}
r = asyncio.run(L._heal_imports(page, dict(E), "Failed to fetch dynamically imported module: http://x/a.js", "{}", st))
check("a module that is simply not there (404) is not reloaded for", r is False and not page.calls
      and "answers 404" in " ".join(st["did"]), st)
L._status_of = lambda url: 200
page, st = FakePage(), {}
r = asyncio.run(L._heal_imports(page, dict(E), "Failed to fetch dynamically imported module: http://x/a.js", "{}", st))
check("...but one that answers 200 NOW is: the page remembered a failure", r is True
      and any(m == "Page.reload" for m, _ in page.calls), st)
L._status_of = _real_status


async def _run_healing(errors, page, after=None):
    seq = list(errors)
    tries = []

    async def attempt():
        err = seq.pop(0) if seq else ""
        tries.append(err)
        return {"e": err}, err
    st = {}
    val = await L._healing(page, dict(E), "{}", attempt, st, after)
    return val, st, tries


bench = []


async def put_back():
    bench.append(1)

page = FakePage(stale=True)
val, st, tries = asyncio.run(_run_healing([chrome, ""], page, put_back))
check("healing: fail, reload, succeed - healed, and tried twice", st.get("healed") is True and len(tries) == 2, st)
check("...and the bench is put back after the reload", bench == [1])
page = FakePage(stale=False, served=False, late_ok=False)
val, st, tries = asyncio.run(_run_healing([chrome, chrome, chrome, chrome], page))
check("healing never loops: at most the map step, then the answer with the fix",
      st.get("healed") is False and len(tries) == 2 and st.get("fix", "").startswith("add to index.html"), (st, tries))
rep = L._heal_report(dict(st))
check("the report says not healed and carries the fix", rep["healed"] is False and "fix" in rep, rep)
page = FakePage(stale=False, served=False, late_ok=True)
val, st, tries = asyncio.run(_run_healing([chrome, ""], page))
rep = L._heal_report(st)
check("healed by an import map, the report still carries the fix for the GAME",
      rep["healed"] is True and rep.get("map") == "late" and "importmap" in rep.get("fix", ""), rep)
line = L._heal_finding(rep)
check("the finding says only this tab has the map", line and line[0].startswith("HEALED") and "Only THIS tab" in line[0], line)
check("an unhealed build's finding leads with IMPORT FAILED and the fix",
      L._heal_finding({"healed": False, "did": ["x"], "specifier": "cannon-es", "fix": "npm install cannon-es"})[0]
      .startswith("IMPORT FAILED on 'cannon-es'"))
check("nothing happened, nothing said", L._heal_report({}) == {} and L._heal_finding({}) == [])


async def _hb():
    page = FakePage(stale=True)
    seq = [chrome, ""]

    async def run():
        return seq.pop(0) if seq else ""
    return await L.heal_build(page, dict(E), "{}", run)
err, rep = asyncio.run(_hb())
check("heal_build (for animate, terrain, debug) heals and reports", err == "" and rep.get("healed") is True, rep)

multi = ("could not import src/assets.js: Failed to fetch dynamically imported module: "
         "http://127.0.0.1:5000/@fs/C:/g/src/assets.js \u2014 tried 3 URLs (http://127.0.0.1:5000/@fs/C:/g/src/assets.js, "
         "http://127.0.0.1:5000/assets.js, http://127.0.0.1:8777/api/engine/source?path=C%3A%2Fg) [at http://127.0.0.1:5000/]")
L._status_of = lambda url: 200 if "api/engine/source" in url else 404


class ProbePage(FakePage):
    async def raw(self, expr, wait=True):
        if str(expr).startswith("import("):
            return "Failed to resolve module specifier 'three'"
        return await super().raw(expr, wait)

page, st = ProbePage(stale=True), {}
r = asyncio.run(L._heal_imports(page, dict(E), multi, "{}", st))
check("openAsset's 'tried 3 URLs': each URL is asked, and the one that answers explains it",
      st.get("specifier") == "three" and "asked each URL" in " ".join(st["did"]) and r is True, st)
L._status_of = lambda url: 404
page, st = ProbePage(), {}
r = asyncio.run(L._heal_imports(page, dict(E), multi, "{}", st))
check("...and when none answers, nothing is reloaded", r is False and not page.calls, st)
L._status_of = _real_status

print("\nThe page's half")
check("the forge carries engineHref, mapState and lateMap",
      all(("F.%s = " % f) in LF.FORGE for f in ("engineHref", "mapState", "lateMap")))
check("...and its version moved, so a stale tab's script is replaced", LF.FORGE_VERSION >= 15)
check("the document-start map waits for the first module script and yields to the page's own map",
      "__MAP__" in LF.IMPORT_MAP_AT_START and "MutationObserver" in LF.IMPORT_MAP_AT_START
      and "'the page has its own'" in LF.IMPORT_MAP_AT_START and "t === 'module'" in LF.IMPORT_MAP_AT_START)
check("mapState: `three` alone does not count as mapping `three/addons/x.js`",
      "k === spec || (k.charAt(k.length - 1) === '/' && String(spec).indexOf(k) === 0)" in LF.FORGE)
check("the forge, thumbnails, pictures and aim all build through the heal",
      L.forge.__code__.co_consts is not None and "_healing" in open(L.__file__, encoding="utf-8").read().split("def _run_pictures")[1].split("def _count_tries")[0]
      and "_healing" in open(L.__file__, encoding="utf-8").read().split("def _thumb_batch")[1].split("def _self_base")[0]
      and "_healing" in open(L.__file__, encoding="utf-8").read().split("\ndef aim(")[1].split("\n# ----")[0])

# =============================================================================== the sheet
print("\nThe asset sheet: one grid, inside the budget")
for n in (1, 2, 5, 12, 24, 40, 64):
    cols, tile = AS.plan(n, 192)
    rows = (n + cols - 1) // cols
    w = max(AS.MIN_WIDTH, AS.PAD + cols * (tile + AS.PAD))
    h = AS.HEAD + rows * (tile + AS.LABEL + AS.PAD) + AS.PAD
    if not (w <= AS.SHEET_MAX and h <= AS.SHEET_MAX and w * h <= AS.SHEET_AREA and tile <= 192):
        check("plan %d fits 1568 px and 1.15 Mpx" % n, False, (cols, tile, w, h))
        break
else:
    check("every plan from 1 to 64 assets fits 1568 px and 1.15 Mpx, tiles no bigger than asked", True)
check("24 assets are a 6 x 4 grid, not a tall strip", AS.plan(24, 192) == (6, 184), AS.plan(24, 192))
check("ids: commas, a JSON array or a list",
      AS._ids_of("a, b,,c") == ["a", "b", "c"] and AS._ids_of('["x","y"]') == ["x", "y"] and AS._ids_of(["z"]) == ["z"])

from PIL import Image                                   # noqa: E402
thumb = TMP / "thumb.png"
Image.new("RGB", (192, 192), (30, 160, 90)).save(thumb)
img = TMP / "pic.png"
Image.new("RGBA", (64, 32), (200, 40, 40, 255)).save(img)
rows_fake = [
    {"id": "code:src/assets.js#buildPillar", "type": "code", "name": "buildPillar", "file": "src/assets.js"},
    {"id": "spec:src/t.js#T[x]", "type": "spec", "name": "Broken Thing", "file": "src/t.js"},
    {"id": "audio:a.mp3", "type": "audio", "name": "a", "file": "a.mp3"},
    {"id": "image:pic.png", "type": "image", "name": "pic", "file": "pic.png", "path": str(img)},
]
from asset_studio import assets_index as AI           # noqa: E402
_real_list = AI.list_assets
_real_thumbs = L.thumbs
AI.list_assets = lambda project, type="", subject="", q="", fresh=False, root="": {
    "ok": True, "items": rows_fake, "total": len(rows_fake)}
asked = []


def fake_thumbs(project, ids=None, size=288, limit=120, engine=""):
    asked.append(list(ids or []))
    return {"ok": True, "thumbs": {"code:src/assets.js#buildPillar": str(thumb)},
            "errors": {"spec:src/t.js#T[x]": "built 9441 triangles and the frame is empty"},
            "rendered": 1, "cached": 0}
L.thumbs = fake_thumbs
try:
    res = AS.sheet(G, size=160)
    cells = res.get("cells") or []
    check("one PNG under data/live/<project>/sheets/", res.get("ok") and Path(res["sheet"]).is_file()
          and Path(res["sheet"]).parent.name == "sheets" and Path(res["sheet"]).is_relative_to(DATA_DIR / "live"), res)
    check("cells in listing order, numbered, with row and col",
          [c["n"] for c in cells] == [1, 2, 3, 4] and all("row" in c and "col" in c for c in cells), cells)
    check("a drawn asset is ok", cells[0]["ok"] is True and "reason" not in cells[0], cells[0])
    check("a failed asset says why, in the renderer's words",
          cells[1]["ok"] is False and "9441 triangles" in cells[1]["reason"], cells[1])
    check("an audio file is a tile that says there is nothing to draw",
          cells[2]["ok"] is False and "audio" in cells[2]["reason"], cells[2])
    check("audio is never sent to the renderer", "audio:a.mp3" not in asked[-1], asked)
    with Image.open(res["sheet"]) as im:
        size_ok = list(im.size) == res["size"]
        c = res["cols"]
        x = AS.PAD + (1 % c) * (res["tile"] + AS.PAD) + res["tile"] - 6
        y = AS.HEAD + (1 // c) * (res["tile"] + AS.LABEL + AS.PAD) + res["tile"] - 6
        px = im.convert("RGB").getpixel((x, y))
    check("the reported size is the file's", size_ok)
    check("the failed tile is drawn in the failure colour", px == AS.FAIL_BG, px)
    again = AS.sheet(G, size=160)
    check("the same assets and pictures make the same file, reused", again["sheet"] == res["sheet"] and again["reused"] is True)
    check("fresh recomposes it", AS.sheet(G, size=160, fresh=True)["reused"] is False)
    miss = AS.sheet(G, ids="code:src/assets.js#buildPillar,code:nope#x", size=160)
    mc = miss.get("cells") or []
    check("an id that does not exist is a tile saying so, and listed in `missing`",
          len(mc) == 2 and mc[1]["ok"] is False and "no asset with this id" in mc[1]["reason"]
          and miss.get("missing") == ["code:nope#x"], miss)
    L.thumbs = lambda *a, **k: {"ok": False, "error": "The forge is off. Turn it on in Settings."}
    off = AS.sheet(G, size=160)
    oc = off.get("cells") or []
    check("the renderer refused: every drawable tile carries the refusal",
          oc[0]["ok"] is False and "forge is off" in oc[0]["reason"], oc[0])
    check("...and a picture that needs no browser is still drawn", oc[3]["ok"] is True, oc[3])
    AI.list_assets = lambda *a, **k: {"ok": True, "items": [], "total": 0}
    check("nothing matched is an error that repeats the filters", AS.sheet(G, q="zzz")["ok"] is False)
    check("a folder that does not exist is refused", AS.sheet(str(TMP / "nope"))["ok"] is False)
finally:
    AI.list_assets = _real_list
    L.thumbs = _real_thumbs

from asset_studio.routers import engine as RE          # noqa: E402
check("the route is mounted at /api/engine/asset-sheet",
      any(getattr(r, "path", "") == "/api/engine/asset-sheet" for r in RE.router.routes))

# ------------------------------------------------------------------------ tidy
# The sheets this test composed live under ITS project's own slug - that folder and no other. A
# glob on "game-*" would have taken a real project that happens to be called "game" with it.
_mine = DATA_DIR / "live" / L._slug(G)
shutil.rmtree(TMP, ignore_errors=True)
if _mine.is_dir() and _mine.name == L._slug(G):
    shutil.rmtree(_mine, ignore_errors=True)
CH.invalidate()
print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)
