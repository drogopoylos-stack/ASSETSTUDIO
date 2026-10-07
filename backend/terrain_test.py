# -*- coding: utf-8 -*-
"""Can an agent sculpt a world over HTTP, and get numbers back instead of a picture?

Everything here runs offline. The terrain maths lives in terrain.ts and is being written by
somebody else while this is written, so the page is STUBBED: a fake CDP session answers the same
JSON envelope the real one does, which proves the wiring — the route, the action shapes, the
journal, the findings, the error paths — without a browser and without the bodies having landed.

The two failures this is really guarding against:

  * a stroke per request. A valley is a dozen strokes and a round trip per stroke is a turn of
    the model per stroke, so `brush` takes a LIST and a dragged stroke is expanded here, not by
    the caller;
  * a throw that comes back as `null`. `applyBrush is not built yet` has to reach the agent as a
    stack with the console beside it, or the agent debugs the endpoint instead of the terrain.

Run:  backend/.venv/Scripts/python.exe backend/terrain_test.py
"""
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session as cc          # noqa: E402
from asset_studio import live as L                 # noqa: E402
from asset_studio import live_terrain as T         # noqa: E402
from asset_studio.routers import live as R         # noqa: E402

passed = 0
fails = []
skips = 0


def ok(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
        return
    fails.append(name + ("   <- " + str(extra) if extra else ""))
    print("  FAIL  %s   %s" % (name, extra))


def skipped(name, why):
    global skips
    skips += 1
    print("  SKIP  %s   (%s)" % (name, why))


PROJ = str(Path(tempfile.mkdtemp(prefix="terrain-proj-")))
(Path(PROJ) / "src").mkdir(exist_ok=True)


# ---------------------------------------------------------------- the route
print("One endpoint, and it says what it takes")
paths = {r.path for r in R.router.routes}
ok("POST /api/live/terrain is registered", "/api/live/terrain" in paths)
body = R.TerrainBody(project="x")
ok("report is the default action, so the cheap answer is the one you get by accident",
   body.action == "report")
ok("brush takes a list of strokes, never one", isinstance(R.TerrainBody(project="x").strokes, list))
for field in ("size", "res", "maxHeight", "seed", "origin", "strokes", "items", "layers",
              "maxSlope", "views", "path", "name", "engine", "lod", "draw"):
    ok("...and the body has a place for %s" % field, field in R.TerrainBody.model_fields)
ok("draw is unset rather than false, so 'leave it out' and 'skip it' are different",
   R.TerrainBody(project="x").draw is None)

print("\nEvery action the contract named has a shape a person can read")
for act in ("make", "brush", "layers", "scatter", "report", "look", "glb", "code", "save",
            "load", "plan"):
    ok("%s has one" % act, act in T._SHAPES and len(T._SHAPES[act]) > 12)
ok("undo is there too, because a stroke read back as worse has to be putbackable",
   "undo" in T._SHAPES)
ok("the brush shape names all nine kinds",
   all(k in T._SHAPES["brush"] for k in ("raise", "lower", "smooth", "flatten", "noise", "erode",
                                         "paint", "scatter", "erase")))
ok("...and says a dragged stroke's seconds are spread, not repeated",
   "spread along the line" in T._SHAPES["brush"])
ok("report says it is the default and costs no picture", "default action" in T._SHAPES["report"])
ok("...and that it carries the drainage once anything has been eroded",
   "drains" in T._SHAPES["report"])
ok("look says how to ask for the drainage instead of the layers",
   '"flow"' in T._SHAPES["look"] and "false-colour" in T._SHAPES["look"])
ok("plan says the field is CHANGED, which is the one thing a caller must know before it runs",
   "THE FIELD IS CHANGED" in T._SHAPES["plan"])
ok("...and that the answer is numbers, not a picture", "no picture" in T._SHAPES["plan"])
ok("...and how to put it back", '"action":"undo"' in T._SHAPES["plan"])

print("\nWhich overlay was asked for, and how it got here")
ok("a flow overlay can be a plain option", T._flow_mode({"flow": True}, [])[0] == "flow")
ok("...or a view name, which is the spelling that survives the router",
   T._flow_mode({}, ["top", "flow"]) == ("flow", ["top"]))
ok("...and cut is the other one", T._flow_mode({}, ["cut"])[0] == "cut")
ok("a view that is a camera angle is left alone",
   T._flow_mode({}, ["3q", "az=35,el=12"]) == ("", ["3q", "az=35,el=12"]))
ok("and a word that is neither is not an overlay", T._flow_mode({"flow": "sideways"}, [])[0] == "")

print("\nA wrong action answers with the table instead of a shrug")
T._guard_real = T._guard
T._guard = lambda project, forge=False: None
got = T.terrain(PROJ, {"action": "sculpt"})
ok("it refuses", got.get("ok") is False, got.get("error"))
ok("...and names the action it was given", "sculpt" in str(got.get("error")))
ok("...and hands back every shape", set(got.get("actions") or {}) == set(T._SHAPES))
ok("help asks for the same table with no work", T.terrain(PROJ, {"action": "help"}).get("actions"))
ok("brush with no strokes says so and shows the shape",
   T.terrain(PROJ, {"action": "brush"}).get("shape") == T._SHAPES["brush"])
ok("scatter with no items points at the brush that paints them",
   'kind:"scatter"' in str(T.terrain(PROJ, {"action": "scatter"}).get("error")))
ok("layers with no layers refuses too",
   T.terrain(PROJ, {"action": "layers"}).get("ok") is False)


# ---------------------------------------------------------------- strokes
print("\nOne request, many strokes - and a drag is a line, not a crater")
one = T._expand([{"kind": "raise", "x": 5, "z": 6, "radius": 30, "strength": 0.4}])[0]
ok("a plain stroke passes through", len(one) == 1 and one[0]["x"] == 5)
ok("...with the brush's own defaults filled in",
   one[0]["falloff"] == 0.6 and one[0]["seconds"] == 1.0)
line, notes = T._expand([{"kind": "lower", "x": -100, "z": 0, "to": [100, 0], "radius": 40,
                          "strength": 0.8, "seconds": 2.0}])
ok("a stroke with `to` becomes a run of steps", len(line) == 11, len(line))
ok("...spaced at half a radius, so the dabs overlap",
   abs((line[1]["x"] - line[0]["x"]) - 20.0) < 0.01, line[1]["x"] - line[0]["x"])
ok("...starting and ending exactly where it was told",
   line[0]["x"] == -100 and line[-1]["x"] == 100)
ok("...and the SECONDS ARE SPREAD along it: a drag is one dose of brush",
   abs(sum(s["seconds"] for s in line) - 2.0) < 1e-9, sum(s["seconds"] for s in line))
ok("...and it says what it did", "became 11 steps" in (notes[0] if notes else ""))
ok("the caller can ask for its own step count",
   len(T._expand([{"x": 0, "z": 0, "to": [10, 0], "radius": 40, "steps": 4}])[0]) == 4)
ok("a mad step count is capped",
   len(T._expand([{"x": 0, "z": 0, "to": [10, 0], "steps": 99999}])[0]) == T._MAX_STEPS)
ok("and a mad request is capped, so one call cannot hang the tab",
   len(T._expand([{"x": 0, "z": 0}] * 9000)[0]) == T._MAX_STROKES)
ok("the optional brush fields ride along only when given",
   "layer" in T._expand([{"kind": "paint", "layer": 2}])[0][0]
   and "layer" not in T._expand([{"kind": "raise"}])[0][0])

print("\nA field the contract gains today works today")
one = T._expand([{"kind": "scatter", "asset": "pine", "density": 0.004, "maxSlope": 12,
                  "spacing": 6, "scaleRange": [0.8, 1.3]}])[0][0]
ok("the scatter brush's slope filter is forwarded, not filtered out", one.get("maxSlope") == 12)
ok("...and its spacing", one.get("spacing") == 6)
ok("...and a range, which is a list and not a number", one.get("scaleRange") == [0.8, 1.3])
typo = T._expand([{"kind": "raise", "radus": 40}])
ok("a misspelt field is still sent: it may be one terrain.ts gained today",
   typo[0][0].get("radus") == 40)
ok("...but it is named, because a silent no-op costs an hour",
   any("radus" in n for n in typo[1]), typo[1])
if contract_src := (Path(__file__).resolve().parent.parent / "frontend" / "src" / "components" /
                    "engine" / "edit" / "terrain.ts"):
    if contract_src.is_file():
        block = re.search(r"export interface Brush \{(.*?)\n\}", contract_src.read_text(
            encoding="utf-8"), re.S)
        named = set(re.findall(r"^\s{2}(\w+)\??:", block.group(1), re.M)) if block else set()
        ok("the known fields ARE the contract's Brush, read out of it rather than listed",
           named and named == T._brush_fields(), sorted(named ^ T._brush_fields()))
        ok("...and the fallback, for an install with no source beside it, is not wildly stale",
           len(T._FALLBACK_FIELDS & named) >= len(named) - 2,
           sorted(named - T._FALLBACK_FIELDS))
    else:
        skipped("the known-field list matches the contract", "terrain.ts is not there")

print("\nA resolution the quad-tree can split")
ok("257 is already right", T._snap_res(257) == 257)
ok("300 becomes the nearest 2^n+1", T._snap_res(300) == 257)
ok("400 rounds the other way", T._snap_res(400) == 513)
ok("nothing is smaller than 33", T._snap_res(4) == 33)
ok("and nothing is bigger than a million samples", T._snap_res(9999) == T._MAX_RES)


# ---------------------------------------------------------------- the glue
print("\nThe page glue only ever calls the contract")
js = T._script()
ok("the version token is substituted",
   "__TERRAIN_VERSION__" not in js and ("T.version = %d;" % T.TERRAIN_VERSION) in js)
contract = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "components" /
            "engine" / "edit" / "terrain.ts")
if not contract.is_file():
    skipped("every M.x in the glue is exported by terrain.ts", "terrain.ts is not there")
else:
    exported = set(re.findall(r"export function (\w+)", contract.read_text(encoding="utf-8")))
    used = set(re.findall(r"\bM\.(\w+)\(", js))
    ok("every terrain function the glue calls is in the contract", used <= exported,
       sorted(used - exported))
    ok("...and it calls enough of it to be doing the job",
       {"makeTerrain", "applyBrush", "report", "terrainMesh", "serialize", "deserialize",
        "emitBuilder", "heightAt", "normalAt", "undoPatch"} <= used, sorted(used))
    ok("the maths is NOT reimplemented in Python: no heightfield arithmetic here",
       "def _height" not in Path(T.__file__).read_text(encoding="utf-8"))
node = shutil.which("node")
if not node:
    skipped("node --check accepts the glue", "node is not installed")
else:
    tmpjs = Path(tempfile.gettempdir()) / "studio-terrain-check.js"
    tmpjs.write_text(js, encoding="utf-8")
    p = subprocess.run([node, "--check", str(tmpjs)], capture_output=True, text=True)
    ok("node --check accepts the glue", p.returncode == 0, (p.stderr or "")[:300])


# ---------------------------------------------------------------- the tiles, actually run
#
# WHY THIS RUNS THE GLUE INSTEAD OF ASSERTING ABOUT IT. The region redraw is arithmetic — which
# tiles a rectangle dirties, padded by the lod, with the sample on a tile boundary belonging to
# both — and every way of getting it wrong is invisible. One tile too few leaves a seam lit
# differently from its neighbour, or a one-cell strip of sky between two patches; one tile too
# many is a silent return to rebuilding everything while the answer still says "4 of 64". A
# picture shows the first only from certain angles and the second never. So the real glue is run
# in node against a fake terrain module that RECORDS every region it is asked for.
FAKE = r"""
/* SHARED THROUGH THE GLOBAL, not through the export. The glue imports every module under
   a fresh query string - a tab remembers a failed import for its whole life - and node
   gives a queried specifier its own module instance, so `M.calls` here and the array the
   glue pushes to are two different arrays. That looked exactly like a redraw that asked
   for no regions at all. */
export const calls = (globalThis.__CALLS = globalThis.__CALLS || []);
export function cellSize(s) { return s.size / (s.res - 1); }
export function makeTerrain(spec) {
  const s = Object.assign({ size: 512, res: 513, maxHeight: 48, origin: [0, 0, 0], seed: 1 },
                          spec || {});
  const n = s.res * s.res;
  const splat = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) splat[i * 4] = 255;
  return { spec: s, height: new Float32Array(n), splat: splat,
           layers: [{ name: 'grass', colour: '#5f7a3f', tiling: 8 }], scatter: [] };
}
export function terrainMesh(t, opts) {
  const res = t.spec.res, cell = cellSize(t.spec);
  const r = (opts && opts.region) || { x0: 0, z0: 0, w: res, h: res };
  const lod = Math.max(1, Math.floor((opts && opts.lod) || 1));
  calls.push({ x0: r.x0, z0: r.z0, w: r.w, h: r.h, lod: lod });
  const line = (start, count) => {
    const out = [], last = start + count - 1;
    for (let v = start; v <= last; v += lod) out.push(v);
    if (out.length && out[out.length - 1] !== last) out.push(last);
    return out;
  };
  const cols = line(r.x0, r.w), rows = line(r.z0, r.h);
  const verts = cols.length * rows.length;
  const positions = new Float32Array(verts * 3);
  const colors = new Float32Array(verts * 4);
  let v = 0;
  for (const j of rows) for (const i of cols) {
    positions[v * 3] = t.spec.origin[0] + i * cell;
    positions[v * 3 + 2] = t.spec.origin[2] + j * cell;
    colors[v * 4] = 0.5; colors[v * 4 + 1] = 0.5; colors[v * 4 + 2] = 0.5; colors[v * 4 + 3] = 1;
    v++;
  }
  const quads = (cols.length - 1) * (rows.length - 1);
  return { positions, colors, normals: new Float32Array(verts * 3),
           uvs: new Float32Array(verts * 2), indices: new Uint32Array(quads * 6) };
}
/* A patch of exactly the samples the disc covers, which is what the real one answers with. */
export function applyBrush(t, b, x, z, dt) {
  const res = t.spec.res, cell = cellSize(t.spec);
  const rad = (+b.radius || 20) / cell;
  const cx = (x - t.spec.origin[0]) / cell, cz = (z - t.spec.origin[2]) / cell;
  const x0 = Math.max(0, Math.ceil(cx - rad)), x1 = Math.min(res - 1, Math.floor(cx + rad));
  const z0 = Math.max(0, Math.ceil(cz - rad)), z1 = Math.min(res - 1, Math.floor(cz + rad));
  if (x1 < x0 || z1 < z0) {
    return { x0: 0, z0: 0, w: 0, h: 0, stats: { kind: b.kind, ms: 0, samples: 0 } };
  }
  if (b.kind === 'erode') t.__eroded = (t.__eroded || 0) + 1;
  return { x0: x0, z0: z0, w: x1 - x0 + 1, h: z1 - z0 + 1,
           height: new Float32Array((x1 - x0 + 1) * (z1 - z0 + 1)),
           stats: { kind: b.kind, ms: 1, samples: (x1 - x0 + 1) * (z1 - z0 + 1),
                    moved: b.kind === 'erode' ? 2.5 : 1, droplets: b.kind === 'erode' ? 900 : 0 } };
}
export function undoPatch() {}
export function heightAt() { return 0; }
export function normalAt() { return [0, 1, 0]; }
export function report(t, maxSlope) {
  return { lo: 0, hi: 24, slopes: [0.5, 0.2, 0.2, 0.1, 0], walkable: 0.8,
           coverage: [{ layer: 'grass', share: 1 }], scatter: (t.scatter || []).length,
           triangles: (t.spec.res - 1) * (t.spec.res - 1) * 2, maxSlope: maxSlope };
}
export function serialize() { return '{}'; }
export function deserialize(s) { return makeTerrain({}); }
export function emitBuilder() { return 'x'; }
export function collider(t) { return { kind: 'heightfield', res: t.spec.res, size: t.spec.size,
                                       maxHeight: t.spec.maxHeight, origin: t.spec.origin }; }
/* Drainage down one column of samples, so a test can ask what colour a wet vertex came out. */
export function flowField(t) {
  if (!t.__eroded) return null;
  const res = t.spec.res, n = res * res;
  const flow = new Float32Array(n), cut = new Float32Array(n);
  for (let j = 0; j < res; j++) {
    const o = j * res + Math.floor(res / 2);
    flow[o] = 1; cut[o] = 0.8;
    if (j > 0) { flow[o - 1] = 0.4; cut[o - 1] = -0.3; }
  }
  return { res, flow, cut, droplets: 900 * t.__eroded, strokes: t.__eroded, peak: 1200 };
}
/* A search that keeps SIXTY-FIVE strokes: more than the answer lists, which is the case the
   journal got wrong on a real field. Every one of them lands in the same corner, so the tiles it
   dirties stay countable. */
export function plan(t, goal) {
  const before = report(t, goal.maxSlope || 30);
  const steps = [
    { brush: { kind: 'raise', radius: 60, strength: 0.5, falloff: 0.6 }, x: 100, z: 100, dt: 1,
      why: 'relief was short', score: 0.4 },
    { brush: { kind: 'smooth', radius: 80, strength: 0.4, falloff: 0.6 }, x: 100, z: 100, dt: 1,
      why: 'walkable was short', score: 0.1 }];
  while (steps.length < 65) {
    steps.push({ brush: { kind: 'smooth', radius: 80, strength: 0.4, falloff: 0.6 },
                 x: 100, z: 100, dt: 1, why: 'walkable was short', score: 0.05 });
  }
  const patches = steps.map((s) => applyBrush(t, s.brush, s.x, s.z, s.dt));
  const after = Object.assign({}, before, { walkable: 0.72 });
  return { steps, patches, before, after, tried: 9, ms: 812, met: true, notes: [] };
}
"""

HARNESS = r"""
import * as M from './fake-terrain.mjs';
import fs from 'node:fs';

/* The smallest three.js the glue can build a mesh with. Nothing here renders; the point is that
   the glue's own node-making code runs, so a mistake in it is a thrown error and not a guess. */
class BufferAttribute { constructor(a, n) { this.array = a; this.itemSize = n; } }
class BufferGeometry {
  constructor() { this.attributes = {}; this.index = null; }
  setAttribute(k, v) { this.attributes[k] = v; }
  setIndex(v) { this.index = v; }
  computeVertexNormals() {}
  dispose() { DISPOSED.geometry++; }
}
class Mesh { constructor(g, m) { this.geometry = g; this.material = m; this.parent = null; } }
class MeshStandardMaterial { constructor(o) { Object.assign(this, o || {}); }
                             dispose() { DISPOSED.material++; } }
const DISPOSED = { geometry: 0, material: 0 };
const THREE = { BufferAttribute, BufferGeometry, Mesh, MeshStandardMaterial };

const ROOT = { children: [] };
const added = [];
const CTX = {
  engine: 'three', THREE, root: ROOT,
  renderer: { outputColorSpace: 'srgb-linear' },     /* transfer off: no double gamma in the way */
  add(n) {
    n.parent = { remove: (x) => { const i = added.indexOf(x); if (i >= 0) added.splice(i, 1); } };
    added.push(n);
  },
};
globalThis.window = { performance: globalThis.performance, __forge: { ctx: () => CTX } };

const src = fs.readFileSync(process.argv[2], 'utf8');
(0, eval)(src);
const T = window.__terra;
/* .href, not .pathname: a Windows path out of a file URL comes back as /C:/… and every loader
   that is handed it goes looking for C:\C:\… */
const url = new URL('./fake-terrain.mjs', import.meta.url).href;

const out = {};
const loaded = await T.load([url]);
out.loaded = loaded;
if (!loaded.ok) { console.log(JSON.stringify(out)); process.exit(0); }

const LOG = globalThis.__CALLS || M.calls;
const regions = () => LOG.splice(0, LOG.length);
T.make({ size: 512, res: 513, maxHeight: 48, seed: 1 });

/* 1. the first draw builds every tile, and the tiles cover the field with a shared seam */
let d = T.draw(1, 0, {});
const first = regions();
out.first = { rebuilt: d.rebuilt, tiles: d.tiles, regions: first.length };
const span = 512;
let covered = new Set(), seams = 0, widest = 0;
for (const r of first) {
  widest = Math.max(widest, r.w, r.h);
  for (let i = r.x0; i < r.x0 + r.w; i++) covered.add(i);
}
const xs = [...new Set(first.map((r) => r.x0))].sort((a, b) => a - b);
for (let i = 1; i < xs.length; i++) {
  const prev = first.find((r) => r.x0 === xs[i - 1]);
  if (prev.x0 + prev.w - 1 === xs[i]) seams++;
}
out.cover = { samples: covered.size, need: span + 1, seams, columns: xs.length, widest };

/* 2. a draw with nothing dirtied rebuilds nothing at all */
d = T.draw(1, 0, {});
out.idle = { rebuilt: d.rebuilt, regions: regions().length, triangles: d.triangles };

/* 3. one 32-unit stroke in the middle of the field */
let s = T.strokes([{ kind: 'raise', x: 256, z: 256, radius: 32, strength: 0.6, falloff: 0.6,
                     seconds: 1 }], true);
d = T.draw(1, 0, {});
out.one = { rebuilt: d.rebuilt, tiles: d.tiles, regions: regions().length, wrote: s.wrote,
            missed: s.missed, touched: s.touched, share: s.share,
            rebuilt_triangles: d.rebuilt_triangles, triangles: d.triangles };

/* 4. a stroke sitting exactly ON a tile boundary must dirty the tiles on both sides of it */
T.strokes([{ kind: 'raise', x: 64, z: 64, radius: 1, strength: 0.5, falloff: 0.6, seconds: 1 }],
          true);
d = T.draw(1, 0, {});
out.seam = { rebuilt: d.rebuilt, regions: regions().map((r) => r.x0 + ',' + r.z0).sort() };

/* 5. a stroke entirely off the edge of the field writes nothing and redraws nothing */
s = T.strokes([{ kind: 'raise', x: -400, z: 0, radius: 32, strength: 0.6, falloff: 0.6,
                 seconds: 1 }], true);
d = T.draw(1, 0, {});
out.off = { wrote: s.wrote, missed: s.missed, rebuilt: d.rebuilt, regions: regions().length };

/* 6. a layer colour is blended into every vertex, so it is the one edit that IS global */
T.setLayers([{ index: 0, colour: '#883322' }]);
d = T.draw(1, 0, {});
out.layers = { rebuilt: d.rebuilt, regions: regions().length };

/* 7. the drainage, as an overlay and as numbers */
out.dry = T.flow(0.3);
T.strokes([{ kind: 'erode', x: 256, z: 256, radius: 40, strength: 0.5, falloff: 0.6,
             seconds: 1 }], true);
out.wet = T.flow(0.3);
d = T.draw(1, 0, { flow: 'flow' });
out.overlay = { rebuilt: d.rebuilt, flow: d.flow, shading: d.shading, regions: regions().length };
/* the middle column of samples is the river: those vertices must NOT be the dry grey */
const mid = added.find((n) => n.geometry && n.geometry.attributes.color);
let wetVerts = 0, dryVerts = 0;
for (const n of added) {
  const col = n.geometry && n.geometry.attributes.color;
  if (!col) continue;
  for (let v = 0; v < col.array.length; v += 3) {
    const b = col.array[v + 2];
    if (b > 0.30) wetVerts++; else dryVerts++;
  }
}
out.paint = { wet: wetVerts, dry: dryVerts };
d = T.draw(1, 0, { flow: 'cut' });
out.cut = { rebuilt: d.rebuilt, flow: d.flow, shading: (d.shading || '').slice(0, 40) };
d = T.draw(1, 0, {});
out.back = { rebuilt: d.rebuilt, flow: d.flow || '', shading: d.shading };

/* 8. the round-one path, kept as the thing the tiles are measured against */
regions();
d = T.draw(1, 0, { whole: true });
out.whole = { tiles: d.tiles, rebuilt: d.rebuilt, regions: regions() };

/* 9. plan: the field changes, the tiles it dirtied are the ones redrawn, and no Patch escapes */
T.draw(1, 0, {});
regions();
const p = T.plan({ walkable: 0.7 });
/* Fetched separately and as a STRING, because the envelope cuts every array at sixty. */
const rp = JSON.parse(T.planReplay());
d = T.draw(1, 0, {});
out.plan = { ok: p.ok, steps: p.steps.length, replay: rp.length, of: p.of,
             inAnswer: 'replay' in p, replay_of: p.replay_of,
             hasPatches: 'patches' in p, undo: p.undo,
             tried: p.tried, met: p.met, before: !!p.before, after: !!p.after,
             rebuilt: d.rebuilt, tiles: d.tiles, keys: Object.keys(p.steps[0] || {}).sort(),
             replayKeys: Object.keys(rp[0] || {}).sort() };
out.state = T.state();
console.log(JSON.stringify(out));
"""

if not node:
    skipped("the glue's tiling is run in node", "node is not installed")
else:
    print("\nThe region redraw, run in node against a module that records every region asked for")
    work = Path(tempfile.mkdtemp(prefix="terrain-tiles-"))
    (work / "fake-terrain.mjs").write_text(FAKE, encoding="utf-8")
    (work / "harness.mjs").write_text(HARNESS, encoding="utf-8")
    (work / "glue.js").write_text(js, encoding="utf-8")
    p = subprocess.run([node, str(work / "harness.mjs"), str(work / "glue.js")],
                       capture_output=True, text=True, cwd=str(work))
    try:
        H = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:
        H = {}
        ok("the harness ran", False, ((p.stderr or "") + (p.stdout or ""))[:600])
    if H:
        ok("the glue imports a terrain module and drives it", (H.get("loaded") or {}).get("ok"))
        f = H.get("first") or {}
        ok("the field is cut into tiles, not drawn as one mesh", f.get("tiles") == 64, f)
        ok("...and the first draw builds all of them", f.get("rebuilt") == 64
           and f.get("regions") == 64, f)
        c = H.get("cover") or {}
        ok("...covering every sample in the field, with none left out",
           c.get("samples") == c.get("need"), c)
        ok("...and each tile SHARES its edge column with the next, or the sky shows through",
           c.get("seams") == c.get("columns", 0) - 1, c)
        ok("...and no tile is secretly the whole field", c.get("widest") == 65, c)
        idle = H.get("idle") or {}
        ok("a draw with nothing dirtied rebuilds NOTHING", idle.get("rebuilt") == 0
           and idle.get("regions") == 0, idle)
        ok("...and still reports the whole field's triangles, not zero",
           idle.get("triangles") == 524288, idle)
        one = H.get("one") or {}
        ok("a 32-unit stroke rebuilds four tiles of sixty-four", one.get("rebuilt") == 4, one)
        ok("...which is the whole point: %s of %s triangles"
           % ("{:,}".format(one.get("rebuilt_triangles", 0)),
              "{:,}".format(one.get("triangles", 0))),
           0 < one.get("rebuilt_triangles", 0) <= one.get("triangles", 1) / 8, one)
        ok("...and the answer says which samples it touched",
           (one.get("touched") or {}).get("w") == 65, one.get("touched"))
        seam = H.get("seam") or {}
        ok("a stroke ON a tile boundary rebuilds the tiles on BOTH sides of it",
           seam.get("rebuilt") == 4, seam)
        off = H.get("off") or {}
        ok("a stroke off the edge of the field writes nothing", off.get("missed") == 1
           and off.get("wrote") == 0, off)
        ok("...and costs no redraw at all", off.get("rebuilt") == 0, off)
        lay = H.get("layers") or {}
        ok("a layer colour change rebuilds every tile, because it is in every vertex",
           lay.get("rebuilt") == 64, lay)
        dry, wet = H.get("dry") or {}, H.get("wet") or {}
        ok("with nothing eroded the report says there is no drainage, and why",
           dry.get("has") is False and "eroded" in str(dry.get("why")), dry)
        ok("after an erode it says how much of the field drains", wet.get("has") is True
           and 0 < wet.get("drains", 0) < 1, wet)
        ok("...and how much of that is channel, at the threshold it used",
           wet.get("channels", 0) > 0 and wet.get("channel") == 0.3, wet)
        ok("...and the droplets and strokes behind it, so a stroke that did nothing is visible",
           wet.get("droplets") == 900 and wet.get("strokes") == 1, wet)
        ok("...and the deepest cut and thickest deposit, in world units",
           wet.get("cut_deepest") == 0.8 and wet.get("fill_thickest") == 0.3, wet)
        ov = H.get("overlay") or {}
        ok("asking for the drainage repaints every tile", ov.get("rebuilt") == 64, ov)
        ok("...and SAYS the picture is false colour, not the layers",
           "FALSE COLOUR" in str(ov.get("shading")) and "NOT the layer colours"
           in str(ov.get("shading")), ov.get("shading"))
        paint = H.get("paint") or {}
        ok("...and the vertices over the water really are painted differently from the dry ones",
           paint.get("wet", 0) > 0 and paint.get("dry", 0) > paint.get("wet", 0), paint)
        ok("cut is an overlay of its own: what the water took away against what it laid down",
           (H.get("cut") or {}).get("flow") == "cut")
        back = H.get("back") or {}
        ok("...and the next plain draw puts the layers back", back.get("flow") == ""
           and "FALSE" not in str(back.get("shading")), back)
        whole = H.get("whole") or {}
        wr = (whole.get("regions") or [{}])[0]
        ok("`whole` still draws the round-one single mesh, which is what the tiles are measured "
           "against", whole.get("tiles") == 1 and len(whole.get("regions") or []) == 1
           and wr.get("w") == 513 and wr.get("h") == 513, whole.get("regions"))
        pl = H.get("plan") or {}
        ok("plan comes back with the steps it kept", pl.get("ok") and pl.get("of") == 65, pl)
        # THE BUG A LIVE RUN FOUND. One list was the answer AND the journal, so a search that kept
        # 62 strokes journalled the 60 that fitted the display cap, and a reloaded tab quietly held
        # a different field from the one it had just been answered about.
        ok("...capped at what a person can read", pl.get("steps") == T._PLAN_STEPS, pl)
        ok("...but the REPLAY list is every one of them, because the journal is not a summary",
           pl.get("replay") == 65, pl)
        ok("...and it comes back OUTSIDE the answer, because the envelope cuts an array at 60",
           pl.get("inAnswer") is False and pl.get("replay_of") == 65, pl)
        ok("...and it is the strokes without the sentences: the prose is for the reader only",
           pl.get("replayKeys") == ["brush", "dt", "x", "z"], pl.get("replayKeys"))
        ok("...and the report either side of it", pl.get("before") and pl.get("after"), pl)
        ok("...and how many it tried, including the ones it rolled back", pl.get("tried") == 9)
        ok("...and NO patches: they are megabytes of before-image the envelope would cut",
           pl.get("hasPatches") is False, pl)
        ok("...but they are held for undo, so a plan is one POST from being put back",
           pl.get("undo") == 65, pl)
        ok("...and only the tiles its strokes dirtied are redrawn, not the field",
           0 < pl.get("rebuilt", 0) < pl.get("tiles", 64), pl)
        ok("...and each step says which part of the goal it was for",
           pl.get("keys") == ["brush", "dt", "score", "why", "x", "z"], pl.get("keys"))
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- the numbers, read out loud
print("\nThe report, said in sentences before anybody opens a picture")
flat = {"lo": 0, "hi": 0, "walkable": 1.0, "slopes": [1, 0, 0, 0, 0], "coverage": [],
        "scatter": 0, "triangles": 100, "spec": {"maxHeight": 48}}
ok("a field nothing has touched says so", any("flat" in f for f in T._findings(flat, {}, 30)))
clip = dict(flat, hi=48.0, lo=0.0)
ok("a field pressed against maxHeight says it is clipping",
   any("clipping" in f for f in T._findings(clip, {}, 30)))
steepish = dict(flat, hi=30, walkable=0.31, slopes=[0.2, 0.2, 0.2, 0.2, 0.2])
lines = T._findings(steepish, {}, 30)
ok("an unwalkable world is named as one",
   any("cannot be stood on" in f for f in lines), lines)
ok("...and a cliff-heavy one too", any("reads as cliff" in f for f in lines))
empty_layer = dict(flat, hi=10, coverage=[{"layer": "grass", "share": 1.0},
                                          {"layer": "rock", "share": 0.0}])
ok("a layer nothing has painted is named",
   any("'rock' covers nothing" in f for f in T._findings(empty_layer, {}, 30)))
trees = dict(flat, hi=10, scatter=240, scatter_steep=11)
ok("trees standing on a cliff are counted, which report() cannot know",
   any("11 of 240" in f for f in T._findings(trees, {}, 30)))
ok("a coarsened preview admits it",
   any("lod 4" in f for f in T._findings(dict(flat, hi=9),
                                         {"lod": 4, "triangles": 250000}, 30)))
ok("a picture showing only some of the scatter admits that too",
   any("shows 1200 of 5000" in f for f in T._findings(dict(flat, hi=9),
                                                      {"scatter_shown": 1200,
                                                       "scatter_total": 5000}, 30)))
ok("a partial redraw says how much of the field it actually rebuilt",
   any("redrew 4 of 64 tiles - 32,768 of 524,288 triangles - in 6.2 ms" in f
       for f in T._findings(dict(flat, hi=9), {"tiles": 64, "rebuilt": 4, "ms": 6.2,
                                               "rebuilt_triangles": 32768,
                                               "triangles": 524288}, 30)),
   T._findings(dict(flat, hi=9), {"tiles": 64, "rebuilt": 4, "ms": 6.2,
                                  "rebuilt_triangles": 32768, "triangles": 524288}, 30))
ok("...and a full one does not brag about it",
   not any("redrew" in f for f in T._findings(dict(flat, hi=9),
                                              {"tiles": 64, "rebuilt": 64, "ms": 60}, 30)))
ok("a PlayCanvas with no vertex colours is reported, not quietly rendered grey",
   any("diffuseVertexColor" in f for f in T._findings(
       dict(flat, hi=9), {"shading": "flat: this PlayCanvas has no diffuseVertexColor, so the "
                                     "layers are not shown"}, 30)))


# ---------------------------------------------------------------- writing into the game
print("\nIt writes into the project and nowhere else")
p, why = T._inside(PROJ, "", "buildTerrain.js")
ok("with no path it lands in src/ when there is one", p.parent.name == "src", str(p))
p, why = T._inside(PROJ, "levels/one.js", "x.js")
ok("a relative path is relative to the project",
   str(p).startswith(str(Path(PROJ).resolve())), str(p))
p, why = T._inside(PROJ, "../../escape.js", "x.js")
ok("a path outside the project is refused", p is None and "outside the project" in why, why)
p, why = T._inside(PROJ, r"C:\Windows\Temp\evil.js", "x.js")
ok("...including an absolute one", p is None, str(p))


# ---------------------------------------------------------------- the journal
print("\nForty strokes do not die with the tab")
T._journal_set(PROJ, [])
T._record(PROJ, "make", {}, {"size": 512, "res": 257}, [], [])
ok("make starts the journal", [r["action"] for r in T._journal(PROJ)] == ["make"])
T._record(PROJ, "brush", {}, {}, [{"kind": "raise", "x": 1}], T._journal(PROJ))
T._record(PROJ, "brush", {}, {}, [{"kind": "lower", "x": 2}], T._journal(PROJ))
ok("strokes are appended", len(T._journal(PROJ)) == 3)
T._record(PROJ, "undo", {}, {}, [], T._journal(PROJ))
ok("an undo drops the request it undid, so a replay cannot bring it back",
   [r["action"] for r in T._journal(PROJ)] == ["make", "brush"])
ok("...and the one it kept is the older one",
   T._journal(PROJ)[1]["strokes"][0]["kind"] == "raise")
T._record(PROJ, "load", {"name": "valley"}, {}, [], T._journal(PROJ))
ok("a load replaces the journal with itself: nothing before it matters",
   [r["action"] for r in T._journal(PROJ)] == ["load"])
T._journal_set(PROJ, [{"action": "make", "spec": {}}])
for i in range(T._JOURNAL_MAX + 40):
    T._record(PROJ, "brush", {}, {}, [{"kind": "raise", "x": i}], T._journal(PROJ))
rows = T._journal(PROJ)
ok("a long session is capped so a cold replay stays quick", len(rows) <= T._JOURNAL_MAX, len(rows))
ok("...and the make at the head is never the thing dropped", rows[0]["action"] == "make")
ok("...and the newest stroke survived", rows[-1]["strokes"][0]["x"] == T._JOURNAL_MAX + 39)
T._journal_set(PROJ, [])


# ---------------------------------------------------------------- the bench, stubbed
print("\nEnd to end against a stubbed page (terrain.ts's bodies are somebody else's job)")


class _FakeLive:
    """Answers the same envelope `_call` builds, so the real code path runs unchanged."""

    def __init__(self, page):
        self.page = page
        self.seen = []

    async def raw(self, expr, wait=True):
        self.seen.append(expr)
        # Kept on the PAGE, not on the session: a session lives for one request and is thrown
        # away, and the thing worth asserting is what a request ASKED THE PAGE FOR.
        self.page.setdefault("__seen", []).append(expr)
        if expr.startswith("window.__terra ?"):
            return self.page.get("__version", -1)
        if expr.lstrip().startswith("(() =>"):
            self.page["__version"] = T.TERRAIN_VERSION
            return None
        m = re.search(r"__terra\.(\w+)\(", expr)
        if not m:
            return None
        v = self.page.get(m.group(1), {})
        if callable(v):
            v = v(expr)
        # Enveloped or raw, told apart the way the page tells them apart. A big string CANNOT go
        # through the envelope: `__live.json` cuts every string at 2000 characters, which once
        # wrote a 2,001-byte terrain builder with a perfect header and no end to it.
        if "__live.mark()" not in expr:
            return v
        if isinstance(v, dict) and "__throw" in v:
            return json.dumps({"v": None, "e": v["__throw"],
                               "c": v.get("c") or [{"kind": "error", "text": v["__throw"]}]})
        return json.dumps({"v": v, "e": None, "c": []})

    async def ask(self, expr, depth=6):
        if expr.startswith("__forge.ensure"):
            return self.page.get("ensure", {"ok": True, "engine": "three"})
        return {}


class _FakeWs:
    async def close(self):
        return None


def stub(page):
    """Point live_terrain at a page that answers, and leave the rest of the module real."""
    async def _session(entry, want_url="", wait_ms=0):
        return _FakeWs(), _FakeLive(page)

    async def _forge_script(live):
        return "current"

    async def _bridge(live, entry):
        return {}
    T._session, T._forge_script, T._bridge = _session, _forge_script, _bridge
    T._forge_open = lambda project: ({"project": project, "url": "http://x/"}, "")
    T._look_state = lambda e, given: {}


REPORT = {"lo": 0.0, "hi": 22.4, "slopes": [0.42, 0.31, 0.18, 0.06, 0.03],
          "coverage": [{"layer": "grass", "share": 0.71}, {"layer": "rock", "share": 0.29}],
          "walkable": 0.73, "scatter": 118, "triangles": 131072, "scatter_steep": 4,
          "drawn_triangles": 131072,
          "spec": {"size": 512, "res": 257, "maxHeight": 48, "seed": 7,
                   "layers": [{"i": 0, "name": "grass", "colour": "#5f7a3f"},
                              {"i": 1, "name": "rock", "colour": "#7b7b74"}],
                   "scatter": 118, "strokes": 14}}
PAGE = {"load": {"ok": True, "from": "forge-ops.js"},
        "state": {"has": True, "module": "forge-ops.js", "strokes": 14},
        "spec": REPORT["spec"], "make": REPORT["spec"],
        "strokes": {"applied": 11, "undo": 11}, "undo": {"undone": 11},
        "setLayers": {"set": [0, 1]}, "place": {"added": 40, "total": 118},
        "report": REPORT,
        "draw": {"ok": True, "triangles": 131072, "vertices": 66049, "lod": 1,
                 "scatter_shown": 118, "scatter_total": 118, "shading": "vertex colours"},
        # A real builder is tens of kilobytes of inlined heightfield, which is the whole point of
        # the test below it.
        "emit": "// buildTerrain\n" + ("const H=[%s];\n" % ",".join(["0.5"] * 9000)),
        "dump": '{"spec":{"res":257},"height":"AAAA"}'}

stub(PAGE)
T._journal_set(PROJ, [])
got = T.terrain(PROJ, {"action": "make", "size": 512, "res": 300, "maxHeight": 48, "seed": 7})
ok("make answers ok", got.get("ok") is True, got.get("error"))
ok("...and says a res that is not 2^n+1 was snapped",
   any("257 was used" in f for f in got.get("findings") or []), got.get("findings"))
ok("...and the report comes back WITHOUT being asked for: numbers are the default answer",
   got.get("report", {}).get("walkable") == 0.73)
ok("...and the field is journalled", [r["action"] for r in T._journal(PROJ)] == ["make"])

got = T.terrain(PROJ, {"action": "brush",
                       "strokes": [{"kind": "lower", "x": -120, "z": 0, "to": [120, 0],
                                    "radius": 48, "strength": 0.7, "seconds": 2}]})
ok("a whole dragged valley is one request", got.get("ok") and got.get("applied") == 11, got)
ok("...and the answer says how to take it back", "undo" in got.get("undo", ""))
ok("...and the numbers arrive with it, so the next stroke is chosen from them",
   got["report"]["walkable"] == 0.73 and got["report"]["lo"] == 0.0)
ok("...and it is journalled with the EXPANDED strokes, so a replay draws the same line",
   len(T._journal(PROJ)[-1]["strokes"]) == 11)
ok("the finding names the trees on the cliff",
   any("4 of 118" in f for f in got.get("findings") or []), got.get("findings"))
ok("...and the answer says which samples the strokes actually touched",
   got.get("touched") is None or "w" in got["touched"])

# A STROKE OUTSIDE THE FIELD. The field runs from its ORIGIN, so a 512-unit world at origin 0 runs
# x 0..512 and a stroke at x = -60 is off the end of it. It applies, it reports `applied: 1`, and
# it moves nothing. That is an hour of an agent's life, and it happened to the writer of this on
# the day it was written.
PAGE["strokes"] = {"applied": 2, "undo": 0, "wrote": 0, "missed": 2}
astray = T.terrain(PROJ, {"action": "brush",
                          "strokes": [{"kind": "raise", "x": -60, "z": 40, "radius": 32},
                                      {"kind": "raise", "x": -80, "z": 40, "radius": 32}]})
ok("a stroke that wrote nothing is named, not quietly counted as applied",
   any("2 of 2 strokes wrote nothing" in f for f in astray.get("findings") or []),
   astray.get("findings"))
ok("...and the answer says where the field actually is",
   any("x 0..512" in f for f in astray.get("findings") or []), astray.get("findings"))
ok("...as the FIRST thing said, before the slope histogram nobody asked about",
   "wrote nothing" in (astray.get("findings") or [""])[0])
PAGE["strokes"] = {"applied": 11, "undo": 11, "wrote": 11, "missed": 0}

ok("layers come back echoed", T.terrain(PROJ, {"action": "layers", "layers": [
    {"index": 0, "name": "grass", "colour": "#5f7a3f", "tiling": 8}]}).get("layers") ==
   {"set": [0, 1]})
ok("scatter says how many landed",
   T.terrain(PROJ, {"action": "scatter", "items": [{"asset": "pine", "x": 1, "z": 2}]})
   .get("scatter", {}).get("added") == 40)
ok("undo says how much it put back",
   T.terrain(PROJ, {"action": "undo"}).get("undone") == 11)

print("\nThe report is the default and never costs an image")
rep = T.terrain(PROJ, {})
ok("no action at all still answers", rep.get("ok") is True)
ok("...with the report", rep.get("report", {}).get("triangles") == 131072)
ok("...and no picture anywhere in it", "sheet" not in json.dumps(rep) and "look" not in rep)
ok("...and it says which module the page actually used", rep.get("module") == "forge-ops.js")

print("\nlook and glb hand off, and close their own row first")
T._live_look, T._live_export = T._live.look, T._live.export_glb
T._live.look = lambda project, **kw: {"ok": True, "sheet": "sheet.png", "views": kw.get("views"),
                                      "ref": kw.get("ref"), "label": kw.get("label")}
T._live.export_glb = lambda project, path: {"ok": True, "path": path, "bytes": 4964964}
shot = T.terrain(PROJ, {"action": "look"})
ok("look draws, then delegates to the forge's own camera", shot.get("ok")
   and (shot.get("look") or {}).get("sheet") == "sheet.png", shot.get("look"))
ok("...with top and a three-quarter by default", (shot.get("look") or {})["views"] == ["top", "3q"])
ok("...and the numbers come with the picture, not instead of it",
   shot.get("report", {}).get("walkable") == 0.73)
ok("...and no activity row is left running - that is the 27-minute bug",
   not [r for r in L.activity() if r.get("running") and r.get("project") == str(PROJ)],
   [r for r in L.activity() if r.get("running")])
ok("...and the project's stored reference is left OUT: it is a character, and this is a hillside",
   (shot.get("look") or {}).get("ref") == "none", shot.get("look"))
ok("...though a caller who names one still gets it",
   (T.terrain(PROJ, {"action": "look", "ref": "ref.png"}).get("look") or {}).get("ref")
   == "ref.png")

print("\nThe drainage, as a picture and as a number")
PAGE["__seen"] = []
PAGE["report"] = dict(REPORT, flow={"has": True, "drains": 0.34, "channels": 0.021,
                                    "channel": 0.3, "droplets": 41000, "strokes": 6,
                                    "peak": 1180.0, "cut_deepest": 2.4, "fill_thickest": 0.9,
                                    "net": 0.0, "res": 257})
PAGE["draw"] = dict(PAGE["draw"], flow="flow", tiles=64, rebuilt=64,
                    shading="FALSE COLOUR: the drainage ... - these are NOT the layer colours")
wet = T.terrain(PROJ, {"action": "look", "views": ["flow", "top"]})
ok("a view of 'flow' is an overlay, not a camera angle: it never reaches the camera",
   (wet.get("look") or {})["views"] == ["top"], (wet.get("look") or {}).get("views"))
ok("...and the page was told to draw it", any('"flow": "flow"' in e
                                              for e in PAGE["__seen"]), PAGE["__seen"][-3:])
ok("...and the answer SAYS the picture is not the layers",
   any("FALSE" in f for f in wet.get("findings") or []), wet.get("findings"))
ok("...with a legend, because blue meaning water is a convention and not a fact",
   "grey is ground no droplet crossed" in str(wet.get("legend")))
ok("...and says the bench stays false-coloured until something redraws it",
   "false-coloured until" in str(wet.get("next")))
ok("cut is the other overlay: what the water took away against what it laid down",
   "took away" in str(T.terrain(PROJ, {"action": "look", "views": ["cut"]}).get("legend")))
ok("the report carries the drainage whether or not a picture was asked for",
   T.terrain(PROJ, {})["report"]["flow"]["drains"] == 0.34)
plain = T.terrain(PROJ, {})
ok("...and reads it out: how much drains, how much is channel, and what it cost",
   any("34% of the field drains" in f and "2.1% of it is channel" in f
       for f in plain.get("findings") or []), plain.get("findings"))
ok("...and the deepest cut, which is the only number that says erode did anything",
   any("deepest cut 2.40" in f for f in plain.get("findings") or []), plain.get("findings"))

# THE ONE THING A DRAINAGE MAP CAN GET WRONG WITHOUT ANY NUMBER MOVING. A river brush and
# paint-by-flow both assume the busiest samples are the carved ones. Measured on a real field they
# were the opposite: the busy ground averaged -3.46 (laid down) and the quiet ground +0.54 (cut).
PAGE["report"] = dict(REPORT, flow=dict(plain["report"]["flow"], busiest_cut=-3.456,
                                        quietest_cut=0.543))
crossed = T.terrain(PROJ, {})
ok("when the busiest ground is where the water DROPPED its load, the answer says so",
   any("CAREFUL" in f and "LAID GROUND DOWN" in f for f in crossed["findings"]),
   crossed["findings"])
ok("...and says what it means for the brush that reads it",
   any("follows the silt and not the gullies" in f for f in crossed["findings"]))
PAGE["report"] = dict(REPORT, flow=dict(plain["report"]["flow"], busiest_cut=2.1,
                                        quietest_cut=0.1))
agrees = T.terrain(PROJ, {})
ok("and when they agree, it says that too, because silence would not be an answer",
   any("also the ones cut deepest" in f for f in agrees["findings"]), agrees["findings"])
PAGE["report"] = dict(REPORT, flow={"has": False, "why": "nothing has been eroded yet, so no "
                                                         "water has run"})
ok("with nothing eroded it says so, rather than saying nothing",
   any("no drainage: nothing has been eroded" in f
       for f in T.terrain(PROJ, {}).get("findings") or []))
PAGE["draw"] = dict(PAGE["draw"], flow="", flow_missing="nothing has been eroded yet",
                    shading="vertex colours")
ok("an overlay asked for and not delivered is admitted, not quietly skipped",
   any("drawn without one" in f
       for f in T.terrain(PROJ, {"action": "look", "views": ["flow"]}).get("findings") or []))
PAGE["draw"].pop("flow_missing")
PAGE["report"] = REPORT

print("\nplan: the report as a fitness function, which is the thing Gaea charges for")
ok("a goal written the contract's way is taken whole",
   T._goal({"goal": {"walkable": 0.7, "relief": [8, 40]}})["relief"] == [8, 40])
ok("...and written flat, the way the rest of this endpoint reads",
   T._goal({"walkable": 0.7})["walkable"] == 0.7)
ok("...and the nested one wins, so the two can never disagree in silence",
   T._goal({"goal": {"walkable": 0.7}, "walkable": 0.2})["walkable"] == 0.7)
ok("the body's own maxSlope default is not mistaken for something somebody asked for",
   "maxSlope" not in T._goal({"maxSlope": 30.0}))
ok("...but a maxSlope that was chosen is kept", T._goal({"maxSlope": 12})["maxSlope"] == 12)
ok("a goal of nothing is not a goal", not T._wants({"budget": 40}))
ok("...while any one of walkable, relief or coverage is",
   T._wants({"relief": [4, 20]}) and T._wants({"coverage": {"grass": 0.5}}))
no_goal = T.terrain(PROJ, {"action": "plan"})
ok("plan with nothing to aim at refuses", no_goal.get("ok") is False)
ok("...and names the ONE reason a goal that was sent might not have arrived",
   "TerrainBody" in str(no_goal.get("error")) and "pydantic" in str(no_goal.get("error")),
   no_goal.get("error"))
ok("...and shows the shape", no_goal.get("shape") == T._SHAPES["plan"])

BEFORE = {"lo": 2.0, "hi": 21.0, "walkable": 0.44, "slopes": [0.2, 0.2, 0.2, 0.2, 0.2],
          "coverage": [{"layer": "grass", "share": 0.30}], "scatter": 0, "triangles": 131072}
AFTER = {"lo": 1.0, "hi": 27.0, "walkable": 0.71, "slopes": [0.4, 0.3, 0.2, 0.1, 0.0],
         "coverage": [{"layer": "grass", "share": 0.62}], "scatter": 0, "triangles": 131072}
PAGE["planReplay"] = json.dumps(
    [{"brush": {"kind": "smooth", "radius": 90, "strength": 0.5, "falloff": 0.6},
      "x": 120, "z": 300, "dt": 1.0},
     {"brush": {"kind": "paint", "radius": 140, "layer": 0, "strength": 0.8, "falloff": 0.5},
      "x": 260, "z": 260, "dt": 1.0}])
PAGE["plan"] = {"ok": True, "of": 2, "tried": 17, "ms": 2840, "met": True, "notes": [],
                "undo": 2, "replay_of": 2, "before": BEFORE, "after": AFTER,
                "steps": [{"brush": {"kind": "smooth", "radius": 90, "strength": 0.5,
                                     "falloff": 0.6}, "x": 120, "z": 300, "dt": 1.0,
                           "why": "walkable was 26 points short", "score": 0.18},
                          {"brush": {"kind": "paint", "radius": 140, "layer": 0, "strength": 0.8,
                                     "falloff": 0.5}, "x": 260, "z": 260, "dt": 1.0,
                           "why": "grass covered a third of what was asked", "score": 0.02}]}
T._journal_set(PROJ, [{"action": "make", "spec": {"size": 512, "res": 257}}])
planned = T.terrain(PROJ, {"action": "plan",
                           "goal": {"walkable": 0.7, "relief": [8, 40],
                                    "coverage": {"grass": 0.6}, "budget": 24}})
ok("plan answers ok", planned.get("ok") is True, planned.get("error"))
ok("...with the report from BEFORE it ran", planned["before"]["walkable"] == 0.44)
ok("...and the report from after", planned["after"]["walkable"] == 0.71)
ok("...and the strokes it kept, each saying which part of the goal it was for",
   [s["why"] for s in planned["plan"]["steps"]][0] == "walkable was 26 points short")
ok("...and how many it TRIED, including the ones it rolled back",
   planned["plan"]["tried"] == 17)
ok("...and NO picture: the whole point is that the numbers decided",
   "look" not in planned and "sheet" not in json.dumps(planned))
ok("the findings read the goal back, part by part, either side of the search",
   any("walkable 0.44 -> 0.71 (asked for 0.70): met." in f for f in planned["findings"]),
   planned["findings"])
ok("...including the relief band, which is two numbers and not one",
   any("relief 19.0 -> 26.0 (asked for 8 to 40): met." in f for f in planned["findings"]),
   planned["findings"])
ok("...and each layer that was named", any("layer 'grass' 0.30 -> 0.62" in f
                                           for f in planned["findings"]), planned["findings"])
ok("...and what the search cost", any("2 strokes kept out of 17 tried, in 2840 ms" in f
                                      for f in planned["findings"]))
# LOOKED AT ONCE, ON A REAL FIELD: a plan that met grass 0.56 and rock 0.24 exactly had painted
# three overlapping discs across a hillside. Every number was green and the ground was not
# shippable, because coverage is a share and nothing in the report is a pattern.
ok("...and warns that a coverage target met by paint discs is still a share and not a pattern",
   any("is a share, not a pattern" in f for f in planned["findings"]), planned["findings"])
ok("...and names the brush that would place them by the drainage instead",
   any('"flow":true' in f for f in planned["findings"]))
ok("...and that the whole thing can be put back", "undo" in planned)
kept = T._journal(PROJ)[-1]
ok("the journal stores the STROKES it kept, not the search",
   kept["action"] == "brush" and len(kept["strokes"]) == 2, kept)
ok("...in the shape a replay can hand straight to the page",
   kept["strokes"][0]["kind"] == "smooth" and kept["strokes"][0]["x"] == 120
   and kept["strokes"][0]["seconds"] == 1.0, kept["strokes"][0])
ok("...so an undo after a plan drops exactly that one entry",
   (T.terrain(PROJ, {"action": "undo"}) or {}).get("ok")
   and [r["action"] for r in T._journal(PROJ)] == ["make"], T._journal(PROJ))
# The answer prints two of the five it kept; the journal must still get five.
PAGE["plan"] = dict(PAGE["plan"], of=5, replay_of=5)
PAGE["planReplay"] = json.dumps([{"brush": {"kind": "smooth", "radius": 90}, "x": i * 10,
                                  "z": 0, "dt": 1.0} for i in range(5)])
T._journal_set(PROJ, [{"action": "make", "spec": {}}])
big = T.terrain(PROJ, {"action": "plan", "goal": {"walkable": 0.7}})
ok("a plan whose answer was capped still journals every stroke it applied",
   len(T._journal(PROJ)[-1]["strokes"]) == 5, T._journal(PROJ)[-1])
ok("...and says how many of them the answer is showing",
   "2 of 5 steps are listed" in str(big["plan"].get("shown")), big["plan"].get("shown"))
PAGE["plan"] = dict(PAGE["plan"], of=900, replay_of=3)
PAGE["planReplay"] = json.dumps([{"brush": {"kind": "smooth"}, "x": 1, "z": 1, "dt": 1.0}] * 3)
T._journal_set(PROJ, [{"action": "make", "spec": {}}])
short = T.terrain(PROJ, {"action": "plan", "goal": {"walkable": 0.7}})
ok("and when even the replay list was cut, it says the journal cannot rebuild this field",
   any("897 strokes short" in f and '"action":"save"' in f for f in short["findings"]),
   [f for f in short["findings"] if "short of this one" in f])
PAGE["planReplay"] = "[]"
PAGE["plan"] = {"ok": True, "of": 0, "tried": 31, "ms": 4001, "met": False, "undo": 0,
                "before": BEFORE, "after": BEFORE, "steps": [],
                "notes": ["no stroke tried moved walkable towards 0.95; the field may be too "
                          "small for the relief asked of it"]}
T._journal_set(PROJ, [{"action": "make", "spec": {}}])
gave_up = T.terrain(PROJ, {"action": "plan", "goal": {"walkable": 0.95}})
ok("a search that reached nothing still answers with both reports",
   gave_up.get("ok") and gave_up["met"] is False and gave_up["before"] and gave_up["after"])
ok("...and says what it could not reach", any("no stroke tried moved walkable" in f
                                              for f in gave_up["findings"]), gave_up["findings"])
ok("...and journals NOTHING, because the field is where it was",
   [r["action"] for r in T._journal(PROJ)] == ["make"], T._journal(PROJ))
# The replay list is fetched by a SECOND call to the page. A stale one - the previous plan's, if
# anything ever went wrong between the two - must not be journalled as this field's history.
PAGE["planReplay"] = json.dumps([{"brush": {"kind": "smooth"}, "x": 9, "z": 9, "dt": 1.0}] * 4)
T._journal_set(PROJ, [{"action": "make", "spec": {}}])
T.terrain(PROJ, {"action": "plan", "goal": {"walkable": 0.95}})
ok("...even if the raw stroke list that came back says otherwise: `of` is what the page stands "
   "behind", [r["action"] for r in T._journal(PROJ)] == ["make"], T._journal(PROJ))
PAGE["planReplay"] = "[]"
PAGE["plan"] = {"ok": False, "error": "this build of terrain.ts has no plan()"}
missing_plan = T.terrain(PROJ, {"action": "plan", "goal": {"walkable": 0.7}})
ok("a bundle whose plan() has not landed says so as itself",
   missing_plan.get("ok") is False and "no plan()" in str(missing_plan.get("error")))
ok("...and the fix is the command to run", "build:ops" in str(missing_plan.get("fix")))
PAGE["plan"] = {"__throw": "Error: plan is not built yet\n    at plan (forge-ops.js:9100:9)"}
threw_plan = T.terrain(PROJ, {"action": "plan", "goal": {"walkable": 0.7}})
ok("and a throw out of the search comes back as the stack, like every other throw here",
   threw_plan.get("ok") is False and "forge-ops.js:9100" in str(threw_plan.get("error")))
exported = T.terrain(PROJ, {"action": "glb", "path": "out/terrain.glb"})
ok("glb exports the bench through the forge's own exporter",
   exported.get("ok") and (exported.get("glb") or {}).get("bytes") == 4964964, exported.get("glb"))
ok("...to a path inside the project", str((exported.get("glb") or {}).get("path", ""))
   .startswith(str(Path(PROJ).resolve())))
T._live.export_glb = lambda project, path: {"ok": False, "error": "the page has been reloaded"}
ok("an export that fails makes the whole answer a failure",
   T.terrain(PROJ, {"action": "glb", "path": "out/x.glb"}).get("ok") is False)
T._live.look, T._live.export_glb = T._live_look, T._live_export

print("\nsave, load and code")
saved = T.terrain(PROJ, {"action": "save", "name": "valley"})
ok("save writes the serialized field", saved.get("ok") and Path(saved["saved"]).is_file(), saved)
ok("...exactly what serialize() gave",
   Path(saved["saved"]).read_text(encoding="utf-8") == PAGE["dump"])
ok("...and the journal collapses to that one load",
   [r["action"] for r in T._journal(PROJ)] == ["load"])
loaded = T.terrain(PROJ, {"action": "load", "name": "valley"})
ok("load reads it back", loaded.get("ok") is True, loaded.get("error"))
gone = T.terrain(PROJ, {"action": "load", "name": "nosuch"})
ok("a load of nothing says so plainly", gone.get("ok") is False
   and "no save called nosuch" in str(gone.get("error")), gone.get("error"))

wrote = T.terrain(PROJ, {"action": "code", "name": "buildTerrain", "path": "src/terrain.js"})
ok("code writes the emitted builder into the game", wrote.get("ok")
   and Path(wrote["wrote"]).read_text(encoding="utf-8") == PAGE["emit"], wrote)
ok("...ALL of it: a builder is tens of kilobytes and the envelope cuts a string at 2000",
   wrote.get("bytes", 0) > 30000, wrote.get("bytes"))
ok("...and says the game needs nothing from the Studio to call it",
   "nothing in it needs the Studio" in wrote.get("next", ""))
PAGE["emit"] = "// buildTerrain\nconst H=[0.5,0.5,0.5…"
cut = T.terrain(PROJ, {"action": "code", "path": "src/cut.js"})
ok("a builder that came back truncated is refused, not written",
   cut.get("ok") is False and "truncated" in str(cut.get("error")), cut.get("error"))
ok("...and no half a file was left in the game", not (Path(PROJ) / "src" / "cut.js").exists())
PAGE["emit"] = "ERR:TypeError: emitBuilder is not a function\n    at __terra.emit"
threw_emit = T.terrain(PROJ, {"action": "code", "path": "src/threw.js"})
ok("a throw inside emitBuilder comes back as the throw",
   threw_emit.get("ok") is False and "not a function" in str(threw_emit.get("error")))
PAGE["emit"] = ""
ok("an emitBuilder that gives nothing back is a failure, not an empty file",
   T.terrain(PROJ, {"action": "code", "path": "src/empty.js"}).get("ok") is False)
ok("...and no empty file was left behind", not (Path(PROJ) / "src" / "empty.js").exists())
PAGE["emit"] = "export function buildTerrain(){}\n"

print("\nA page that was reloaded rebuilds itself")
T._journal_set(PROJ, [{"action": "make", "spec": {"size": 512, "res": 257}},
                      {"action": "brush", "strokes": [{"kind": "raise", "x": 0, "z": 0}]},
                      {"action": "brush", "strokes": [{"kind": "smooth", "x": 4, "z": 0}]}])
empty_then_full = {"has": False}


def _state_once(_expr):
    # Empty the first time it is asked, full afterwards - a tab that lost the field and got it
    # back is exactly what the replay is for.
    if empty_then_full["has"]:
        return {"has": True, "module": "forge-ops.js", "strokes": 2}
    empty_then_full["has"] = True
    return {"has": False}


PAGE["state"] = _state_once
back = T.terrain(PROJ, {"action": "report"})
ok("the journal is replayed into the empty page", back.get("ok") is True, back.get("error"))
ok("...and the answer SAYS it happened", "3 journalled actions" in back.get("restored", ""),
   back.get("restored"))
PAGE["state"] = {"has": True, "module": "forge-ops.js", "strokes": 14}

print("\nWhen it cannot work, it says the one line that would fix it")
PAGE["load"] = {"ok": False, "error": "forge-ops.js loaded, but it exports no makeTerrain"}
missing = T.terrain(PROJ, {"action": "report"})
ok("a bundle with no terrain in it is named", missing.get("ok") is False
   and "no makeTerrain" in str(missing.get("error")), missing.get("error"))
ok("...and the fix is the command to run", "npm run build:ops" in missing.get("fix", ""))
ok("...and both URLs it tried are listed", len(missing.get("tried") or []) == 2)
PAGE["load"] = {"ok": True, "from": "forge-ops.js"}

PAGE["strokes"] = {"__throw": "Error: terrain.applyBrush is not built yet\n    at applyBrush "
                              "(forge-ops.js:1200:11)\n    at __terra.strokes"}
threw = T.terrain(PROJ, {"action": "brush", "strokes": [{"kind": "raise", "x": 0, "z": 0}]})
ok("a throw from terrain.ts comes back as a failure", threw.get("ok") is False)
ok("...with the REAL stack, not a summary", "at applyBrush (forge-ops.js:1200:11)"
   in str(threw.get("error")), threw.get("error"))
ok("...with the console beside it, errors first",
   (threw.get("console") or [{}])[0].get("kind") == "error", threw.get("console"))
ok("...and it says a missing body is not a broken endpoint",
   "not built yet" in threw.get("hint", ""))
ok("...and NOTHING was journalled, so a replay cannot repeat a stroke that never landed",
   all(r["action"] != "brush" or r["strokes"][0]["kind"] != "raise"
       for r in T._journal(PROJ)[-1:]), T._journal(PROJ)[-1:])
PAGE["strokes"] = {"applied": 11, "undo": 11}

PAGE["state"] = {"has": False}
T._journal_set(PROJ, [])
none = T.terrain(PROJ, {"action": "report"})
ok("no field and no journal is a clear refusal", none.get("ok") is False
   and "no terrain for this project" in str(none.get("error")))
ok("...and it shows how to start one", '"action":"make"' in none.get("fix", ""))
PAGE["state"] = {"has": True, "module": "forge-ops.js", "strokes": 14}

PAGE["ensure"] = {"ok": False, "error": "no WebGL context"}
ok("a studio that will not start is reported as itself",
   "no WebGL" in str(T.terrain(PROJ, {"action": "report"}).get("error")))
PAGE["ensure"] = {"ok": True, "engine": "three"}

print("\nclear")
PAGE["forget"] = {"ok": True}
cleared = T.terrain(PROJ, {"action": "clear"})
ok("clear takes the field off the bench", cleared.get("ok") and cleared.get("cleared"), cleared)
ok("...and empties the journal with it", T._journal(PROJ) == [])


# ---------------------------------------------------------------- the note
print("\nThe agent note: one paragraph, paid for by every turn")
note = cc._forge_note()
ok("terrain is in the forge note", "/api/live/terrain" in note)
ok("...and it says the strokes go in a list", "strokes" in note)
ok("...and that the numbers come back without a picture",
   "report" in note and "numbers" in note.lower())
ok("...and that a wrong action prints the shapes, so the note need not",
   "action" in note)
# READ THE CEILING, DO NOT REPEAT IT. Both suites held the number 4550, one of them was raised,
# and the note passed here and failed there inside the same minute. live_test.py owns it.
_ceil = 4550
try:
    import re as _re
    _lt = io.open(str(Path(__file__).resolve().parent / "live_test.py"), encoding="utf-8").read()
    _m = _re.search(r"len\(_fn\)\s*<\s*(\d+)", _lt)
    if _m:
        _ceil = int(_m.group(1))
except OSError:
    pass
ok("the note is still under the ceiling live_test holds it to", len(note) < _ceil,
   "%d against %d" % (len(note), _ceil))
print("      the note is %d characters, %d under the ceiling of %d"
      % (len(note), _ceil - len(note), _ceil))

T._guard = T._guard_real
shutil.rmtree(PROJ, ignore_errors=True)
# The journal and the saves live under data/live, not under the project, so removing the project
# leaves them behind — twelve of them accumulated before anybody noticed.
shutil.rmtree(T._dir(PROJ).parent, ignore_errors=True)

print("\n  %d passed, %d failed, %d skipped" % (passed, len(fails), skips))
for f in fails:
    print("  FAIL  " + f)
sys.exit(1 if fails else 0)
