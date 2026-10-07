// The terrain tool: the part a hand touches, with no browser anywhere near it.
//
// The check this file exists for is the third one down. A held stroke is hundreds of applyBrush
// calls and has to be ONE undo, and it has to put the ground back EXACTLY — not approximately,
// not to three decimals. That is the property no amount of looking at the screen can confirm and
// the one that costs a person their afternoon when it is wrong.
//
// It runs against a reference engine written here rather than against terrain.ts. Two reasons:
// this suite tests the TOOL, and a failure has to name the tool rather than whatever the engine
// was doing that hour; and terrain.ts was being written in another window while this was. The
// last section runs the same stroke through the real engine when it is built, and says so out
// loud when it is not.
//
// Run: npm run test:terraintool

import {
  BRUSHES, BRUSH_BY_KIND, CHUNK_ABOVE, CHUNK_SAMPLES, DEFAULT_BRUSH, DEFAULT_LAYERS, RADIUS_MAX, RADIUS_MIN,
  TerrainHistory, TerrainTool, builderName, emitRes, ensureLayers, packTerrain, pickEmitEngine,
  scatterAssetOf, terrainBuilderPath, terrainSidecarPath, unpackTerrain, usedPalette, wantChunks,
  type AssetRow, type ScatterAsset, type TerrainEngine,
} from "../src/components/engine/edit/terrainTool";
import {
  PC_SPLAT_CHUNK, hexToLinear, pcSetChunk, pcSplatColours, pcSplatMaterial, pcSplatSource, pcTerrainEntity,
} from "../src/components/engine/edit/pcmirror";
import type { Brush, Patch, ScatterItem, TerrainData } from "../src/components/engine/edit/terrain";
// The real engine, for the last section. Imported for its side-effect-free module object only —
// every function in it throws until it is built, so nothing here calls one outside a try.
import * as ENGINE from "../src/components/engine/edit/terrain";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
function eq(name: string, got: unknown, want: unknown) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  ok(name, a === b, "got " + a + ", want " + b);
}

// ------------------------------------------------------------------ a reference engine
//
// Small, honest, and shaped exactly like the contract: heights 0..1 row-major, a brush that
// returns the BEFORE image of the rectangle it touched. Enough for the tool to be judged by.

function field(res = 33, size = 32): TerrainData {
  return {
    spec: { size, res, maxHeight: 10, origin: [0, 0, 0], seed: 1 },
    height: new Float32Array(res * res).fill(0.5),
    splat: (() => { const s = new Uint8Array(res * res * 4); for (let i = 0; i < res * res; i++) s[i * 4] = 255; return s; })(),
    layers: DEFAULT_LAYERS.map((l) => ({ ...l })),
    scatter: [],
  };
}

const clampi = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function rectFor(t: TerrainData, x: number, z: number, radius: number) {
  const per = (t.spec.res - 1) / t.spec.size;          // samples per world unit
  const cx = (x - t.spec.origin[0]) * per, cz = (z - t.spec.origin[2]) * per;
  const r = radius * per;
  const x0 = clampi(Math.floor(cx - r), 0, t.spec.res - 1);
  const z0 = clampi(Math.floor(cz - r), 0, t.spec.res - 1);
  const x1 = clampi(Math.ceil(cx + r), 0, t.spec.res - 1);
  const z1 = clampi(Math.ceil(cz + r), 0, t.spec.res - 1);
  return { x0, z0, w: Math.max(0, x1 - x0 + 1), h: Math.max(0, z1 - z0 + 1), cx, cz, r };
}

const REF: TerrainEngine = {
  capture(t, x0, z0, w, h) {
    const height = new Float32Array(w * h);
    const splat = new Uint8Array(w * h * 4);
    for (let j = 0; j < h; j++) {
      for (let i = 0; i < w; i++) {
        const s = (z0 + j) * t.spec.res + (x0 + i);
        height[j * w + i] = t.height[s];
        for (let c = 0; c < 4; c++) splat[(j * w + i) * 4 + c] = t.splat[s * 4 + c];
      }
    }
    return { x0, z0, w, h, height, splat };
  },
  heightAt(t, x, z) {
    const per = (t.spec.res - 1) / t.spec.size;
    const i = clampi(Math.round((x - t.spec.origin[0]) * per), 0, t.spec.res - 1);
    const j = clampi(Math.round((z - t.spec.origin[2]) * per), 0, t.spec.res - 1);
    return t.height[j * t.spec.res + i] * t.spec.maxHeight;
  },
  applyBrush(t, b, x, z, dt) {
    const R = rectFor(t, x, z, b.radius);
    if (b.kind === "scatter") {
      const item: ScatterItem = { asset: b.asset || "?", at: [x, 0, z], rot: 0, scale: 1, painted: true };
      t.scatter.push(item);
      return { x0: R.x0, z0: R.z0, w: 0, h: 0, scatterAdded: [item] };
    }
    if (b.kind === "erase") {
      const gone: Array<{ at: number; item: ScatterItem }> = [];
      for (let i = t.scatter.length - 1; i >= 0; i--) {
        const s = t.scatter[i];
        if (Math.hypot(s.at[0] - x, s.at[2] - z) <= b.radius) { gone.push({ at: i, item: s }); t.scatter.splice(i, 1); }
      }
      return { x0: R.x0, z0: R.z0, w: 0, h: 0, scatterRemoved: gone.reverse() };
    }
    const before = REF.capture(t, R.x0, R.z0, R.w, R.h);
    const paint = b.kind === "paint";
    const dir = b.kind === "lower" ? -1 : 1;
    for (let j = 0; j < R.h; j++) {
      for (let i = 0; i < R.w; i++) {
        const gx = R.x0 + i, gz = R.z0 + j;
        const d = Math.hypot(gx - R.cx, gz - R.cz);
        if (d > R.r) continue;
        const fall = R.r > 0 ? 1 - (d / R.r) * b.falloff : 1;
        const s = gz * t.spec.res + gx;
        if (paint) {
          const L = clampi(b.layer ?? 0, 0, 3);
          const add = Math.min(255, Math.round(255 * b.strength * dt * fall * 8));
          let rest = 255 - Math.min(255, t.splat[s * 4 + L] + add);
          t.splat[s * 4 + L] = Math.min(255, t.splat[s * 4 + L] + add);
          for (let c = 0; c < 4; c++) {
            if (c === L) continue;
            const take = Math.min(t.splat[s * 4 + c], rest);
            t.splat[s * 4 + c] = take;
            rest -= take;
          }
        } else {
          t.height[s] = clampi(t.height[s] + dir * b.strength * dt * fall * 0.5, 0, 1);
        }
      }
    }
    const p: Patch = { x0: R.x0, z0: R.z0, w: R.w, h: R.h };
    if (paint) p.splat = before.splat; else p.height = before.height;
    return p;
  },
  undoPatch(t, p) {
    if (p.height) {
      for (let j = 0; j < p.h; j++) for (let i = 0; i < p.w; i++) {
        t.height[(p.z0 + j) * t.spec.res + (p.x0 + i)] = p.height[j * p.w + i];
      }
    }
    if (p.splat) {
      for (let j = 0; j < p.h; j++) for (let i = 0; i < p.w; i++) {
        const s = (p.z0 + j) * t.spec.res + (p.x0 + i), d = j * p.w + i;
        for (let c = 0; c < 4; c++) t.splat[s * 4 + c] = p.splat[d * 4 + c];
      }
    }
    for (const it of p.scatterAdded || []) { const k = t.scatter.indexOf(it); if (k >= 0) t.scatter.splice(k, 1); }
    for (const r of p.scatterRemoved || []) t.scatter.splice(Math.min(r.at, t.scatter.length), 0, r.item);
  },
};

const snap = (t: TerrainData) => Array.from(t.height).join(",");
const snapSplat = (t: TerrainData) => Array.from(t.splat).join(",");

/** Hold the button down and drag, the way a hand does: one down, many moves, one up. */
function stroke(tool: TerrainTool, t: TerrainData, pts: Array<[number, number]>, ms = 16) {
  let now = 1000;
  tool.down(t, { x: pts[0][0], z: pts[0][1] }, 100, now);
  for (let i = 1; i < pts.length; i++) {
    now += ms;
    tool.move(t, { x: pts[i][0], z: pts[i][1] }, 100 + i, now);
  }
  return tool.up(t);
}

// ---- a held stroke is ONE undo -----------------------------------------------------------
console.log("A held stroke is one undo");
{
  const t = field();
  const before = snap(t);
  const tool = new TerrainTool(REF);
  tool.setKind("raise");
  const pts: Array<[number, number]> = [];
  for (let i = 0; i < 120; i++) pts.push([4 + i * 0.2, 16]);
  const pushed = stroke(tool, t, pts);

  ok("the stroke changed the ground", snap(t) !== before);
  ok("the stroke was pushed", pushed);
  eq("one hundred and twenty moves left ONE undo step", tool.history.depth, 1);

  tool.history.undo(t);
  ok("undo puts the ground back exactly, sample for sample", snap(t) === before,
     "the whole field must match, not merely be close");
  eq("and the stack is empty again", tool.history.depth, 0);
}

// ---- redo ---------------------------------------------------------------------------------
console.log("\nRedo");
{
  const t = field();
  const before = snap(t);
  const tool = new TerrainTool(REF);
  stroke(tool, t, [[8, 8], [10, 8], [12, 9], [14, 11]]);
  const after = snap(t);
  ok("the stroke moved something", after !== before);

  tool.history.undo(t);
  ok("undone", snap(t) === before);
  eq("one step waiting to be redone", tool.history.redoDepth, 1);

  tool.history.redo(t);
  ok("redo restores the stroke exactly", snap(t) === after);
  eq("...and it is back on the undo stack", tool.history.depth, 1);
  eq("...with nothing left to redo", tool.history.redoDepth, 0);

  // Ten laps. A redo built from a re-capture rather than a replay must not drift.
  for (let i = 0; i < 10; i++) { tool.history.undo(t); tool.history.redo(t); }
  ok("ten undo/redo laps still land on the same ground", snap(t) === after);
}

// ---- a new stroke ends the redo branch ----------------------------------------------------
{
  const t = field();
  const tool = new TerrainTool(REF);
  stroke(tool, t, [[8, 8], [9, 8]]);
  tool.history.undo(t);
  eq("a redo is waiting", tool.history.redoDepth, 1);
  stroke(tool, t, [[20, 20], [21, 20]]);
  eq("a new stroke throws the redo branch away", tool.history.redoDepth, 0);
}

// ---- overlapping stamps: the EARLIEST value wins -------------------------------------------
console.log("\nCoalescing keeps the value from before the stroke, not before the stamp");
{
  const t = field();
  const before = snap(t);
  const tool = new TerrainTool(REF);
  tool.setKind("raise");
  tool.brush.radius = 6;
  // Back and forth over the same ground: every sample is touched four or five times, so a merge
  // that kept the LAST before-image would restore the ground half-dug.
  stroke(tool, t, [[16, 16], [18, 16], [16, 16], [18, 16], [16, 16]]);
  ok("the ground moved", snap(t) !== before);
  tool.history.undo(t);
  ok("re-crossed ground still comes back exactly", snap(t) === before);
}

// ---- a ragged union rectangle is still exact -----------------------------------------------
{
  const t = field();
  // A corner of the field that the stroke never reaches, moved by hand first. The stroke's union
  // rectangle will cover it (an L-shaped drag makes a rectangle with empty corners); undo must
  // leave it alone rather than write a zero into it.
  t.height[3 * t.spec.res + 3] = 0.9;
  const before = snap(t);
  const tool = new TerrainTool(REF);
  tool.brush.radius = 2;
  stroke(tool, t, [[2, 12], [2, 4], [12, 4]]);       // an L: its bounding box holds (3,3)
  tool.history.undo(t);
  ok("a sample inside the union but outside the stroke is untouched by undo", snap(t) === before,
     "unseen samples must be filled from the field as it is now, not from zero");
}

// ---- painting -----------------------------------------------------------------------------
console.log("\nPaint is undone the same way");
{
  const t = field();
  const before = snapSplat(t);
  const tool = new TerrainTool(REF);
  tool.setKind("paint");
  tool.brush.layer = 1;
  eq("paint with a layer chosen is not blocked", tool.blocked(), "");
  stroke(tool, t, [[16, 16], [18, 16], [20, 17]]);
  ok("the splat changed", snapSplat(t) !== before);
  eq("one undo step for the whole paint stroke", tool.history.depth, 1);
  tool.history.undo(t);
  ok("the splat comes back exactly", snapSplat(t) === before);
}

// ---- scatter and erase ---------------------------------------------------------------------
console.log("\nScatter and erase");
{
  const t = field();
  const tool = new TerrainTool(REF);
  tool.setKind("scatter");
  eq("scatter with no asset says what it wants", tool.blocked(), "choose an asset to scatter");
  tool.brush.asset = "tree.oak";
  eq("...and stops saying it once one is chosen", tool.blocked(), "");
  stroke(tool, t, [[10, 10], [12, 10], [14, 10], [16, 10]]);
  ok("trees were placed", t.scatter.length > 1, String(t.scatter.length));
  const planted = t.scatter.length;
  eq("the whole scatter drag is one undo step", tool.history.depth, 1);
  tool.history.undo(t);
  eq("undo takes every one of them back off", t.scatter.length, 0);
  tool.history.redo(t);
  eq("redo puts them all back", t.scatter.length, planted);

  // Erase, then undo: the list must come back whole, in order.
  const names = t.scatter.map((s) => s.asset + "@" + s.at[0]).join("|");
  tool.setKind("erase");
  tool.brush.radius = 30;
  stroke(tool, t, [[13, 10]]);
  ok("erase removed them", t.scatter.length < planted, String(t.scatter.length));
  tool.history.undo(t);
  eq("undo restores the list whole", t.scatter.map((s) => s.asset + "@" + s.at[0]).join("|"), names);
}

// ---- a click that changes nothing costs no undo slot ----------------------------------------
{
  const t = field();
  const tool = new TerrainTool(REF);
  tool.setKind("scatter");                            // blocked: no asset chosen
  const pushed = stroke(tool, t, [[10, 10], [11, 10]]);
  ok("a blocked brush does nothing", !pushed && tool.history.depth === 0,
     "an undo slot spent on nothing is an undo that stops meaning anything");
}

// ---- fifty deep -------------------------------------------------------------------------
{
  const t = field(65, 64);
  const tool = new TerrainTool(REF);
  tool.brush.radius = 1.5;
  for (let i = 0; i < 60; i++) stroke(tool, t, [[2 + i, 30], [3 + i, 30]]);
  ok("the stack is at least fifty deep", tool.history.depth >= 50, String(tool.history.depth));
}

// ---- [ and ] --------------------------------------------------------------------------------
console.log("\nThe brush keys");
{
  const tool = new TerrainTool(REF);
  tool.brush.radius = 8;
  tool.key("]");
  ok("] grows the brush", tool.brush.radius > 8, String(tool.brush.radius));
  const grown = tool.brush.radius;
  tool.key("[");
  ok("[ shrinks it again", tool.brush.radius < grown);
  ok("...back to about where it started", Math.abs(tool.brush.radius - 8) < 0.01, String(tool.brush.radius));

  // A ratio, not a fixed step: the same two keys have to work at both ends of the range.
  tool.brush.radius = 0.3;
  tool.key("[");
  ok("a tiny brush still shrinks by something useful", tool.brush.radius < 0.3 && tool.brush.radius >= RADIUS_MIN,
     String(tool.brush.radius));
  for (let i = 0; i < 200; i++) tool.key("[");
  eq("and it stops at the floor", tool.brush.radius, RADIUS_MIN);
  for (let i = 0; i < 400; i++) tool.key("]");
  eq("and at the ceiling", tool.brush.radius, RADIUS_MAX);

  eq("[ reports what it changed", new TerrainTool(REF).key("["), "radius");
  eq("an unrelated key is left alone", tool.key("q"), "");
  eq("...including the ones the editor already owns", tool.key("Tab"), "");
}

// ---- nine brushes, nine keys ------------------------------------------------------------
{
  eq("there are nine brushes", BRUSHES.length, 9);
  eq("every brush the contract names has a definition",
     BRUSHES.map((b) => b.kind).sort().join(","),
     ["raise", "lower", "smooth", "flatten", "noise", "erode", "paint", "scatter", "erase"].sort().join(","));
  const keys = BRUSHES.map((b) => b.key);
  eq("no two brushes share a key", new Set(keys).size, keys.length);
  ok("every key is one letter", keys.every((k) => k.length === 1 && /[a-z]/.test(k)), keys.join(""));

  // The keys the editor uses in EVERY mode. A brush that took one of these would break something
  // a person still needs while they are sculpting.
  for (const taken of ["z", "a", "h", "i", "g"]) {
    ok('"' + taken + '" is left to the editor', !keys.includes(taken),
       "z shades, a selects all, h hides, i keys, g moves");
  }
  // Seven of nine. Only scatter and erase lose their initial, to smooth and erode.
  eq("every brush that can have its own initial has it",
     BRUSHES.filter((b) => b.key !== b.kind[0]).map((b) => b.kind).sort(), ["erase", "scatter"]);

  const tool = new TerrainTool(REF);
  for (const b of BRUSHES) {
    eq("pressing " + b.key + " arms " + b.kind, tool.key(b.key), b.kind);
    eq("...and the tool is holding it", tool.brush.kind, b.kind);
  }
  eq("the shift key of a capital still arms it", new TerrainTool(REF).key("F"), "flatten");
}

// ---- shift-drag sets strength, and is not a stroke ------------------------------------------
console.log("\nShift-drag adjusts strength");
{
  const t = field();
  const before = snap(t);
  const tool = new TerrainTool(REF);
  tool.brush.strength = 0.5;
  tool.down(t, { x: 16, z: 16 }, 400, 1000, { shift: true });
  tool.move(t, { x: 16, z: 16 }, 550, 1016, { shift: true });
  ok("dragging right raises the strength", tool.brush.strength > 0.5, String(tool.brush.strength));
  ok("150 px is half the range", Math.abs(tool.brush.strength - 1.0) < 0.001, String(tool.brush.strength));
  tool.move(t, { x: 16, z: 16 }, 100, 1032, { shift: true });
  ok("dragging left lowers it", tool.brush.strength < 0.5, String(tool.brush.strength));
  tool.move(t, { x: 16, z: 16 }, -900, 1048, { shift: true });
  eq("and it cannot go below zero", tool.brush.strength, 0);
  const pushed = tool.up(t);
  ok("a strength drag never touched the ground", snap(t) === before);
  ok("...and never went on the undo stack", !pushed && tool.history.depth === 0);
}

// ---- flatten reads its height once, at the start of the stroke -------------------------------
{
  const t = field();
  t.height.fill(0.25);
  const tool = new TerrainTool(REF);
  tool.setKind("flatten");
  ok("flatten has no target until a stroke starts", tool.brush.target === undefined);
  tool.down(t, { x: 16, z: 16 }, 100, 1000);
  eq("it takes the height under the cursor, in world units", tool.brush.target, 0.25 * 10);
  tool.up(t);
}

// ---- the tool reports the rectangle to rebuild ------------------------------------------------
{
  const t = field(65, 64);
  const tool = new TerrainTool(REF);
  tool.brush.radius = 4;
  tool.down(t, { x: 10, z: 10 }, 100, 1000);
  const r = tool.move(t, { x: 30, z: 10 }, 140, 1016);
  ok("a move hands back a rectangle", !!r);
  ok("...that covers the whole swept path, not just where the pointer stopped",
     !!r && r.w > 20 && r.h > 4 && r.h < 20, JSON.stringify(r));
  ok("...in sample coordinates inside the field",
     !!r && r.x0 >= 0 && r.z0 >= 0 && r.x0 + r.w <= t.spec.res && r.z0 + r.h <= t.spec.res, JSON.stringify(r));
  tool.up(t);
}

// ---- a fast flick is stamped along its path, not at its ends ----------------------------------
{
  const t = field(129, 128);
  const tool = new TerrainTool(REF);
  tool.setKind("raise");
  tool.brush.radius = 2;
  tool.brush.strength = 1;
  tool.down(t, { x: 10, z: 64 }, 100, 1000);
  tool.move(t, { x: 110, z: 64 }, 800, 1100);          // 100 units in one frame
  tool.up(t);
  const mid = t.height[64 * 129 + 60];                 // one sample per unit here, so x=60 is 60
  ok("the middle of a fast flick was painted", mid > 0.5,
     "a stroke stamped only at the pointer's samples leaves a dotted line: " + mid);
}

// ---- the scatter palette takes the Library's shape ---------------------------------------------
console.log("\nThe scatter palette is the project's own asset index");
{
  // The four row shapes /api/engine/assets returns, verbatim in the fields that matter.
  const spec: AssetRow = { id: "a1", type: "spec", name: "Oak", file: "rot-rush/src/proplib.ts",
    export: "PROPS", table: "PROPS", key: "nature.tree", index: 4, root: "rot-rush", deps: ["./palette.ts"] };
  const code: AssetRow = { id: "a2", type: "code", name: "buildRock", file: "src/rock.ts", export: "buildRock", root: "" };
  const model: AssetRow = { id: "a3", type: "model", name: "bush", file: "assets/bush.glb" };
  const image: AssetRow = { id: "a4", type: "image", name: "leaf", file: "assets/leaf.png" };
  const pointsAtModel: AssetRow = { id: "a5", type: "spec", name: "Statue", file: "src/props.ts", model: "assets/statue.glb" };

  eq("a spec row keeps the table and the entry that finds it",
     scatterAssetOf(spec).ref,
     { kind: "code", spec: true, file: "rot-rush/src/proplib.ts", root: "rot-rush", table: "PROPS",
       key: "nature.tree", index: 4, export: "PROPS", deps: ["./palette.ts"] });
  eq("a builder row is the file and its export",
     scatterAssetOf(code).ref, { kind: "code", file: "src/rock.ts", root: "", export: "buildRock", deps: [] });
  eq("a model row is its file", scatterAssetOf(model).ref, { kind: "model", url: "assets/bush.glb" });
  eq("a sprite row is an image", scatterAssetOf(image).ref, { kind: "image", url: "assets/leaf.png" });
  eq("a spec that names a model is the model", scatterAssetOf(pointsAtModel).ref, { kind: "model", url: "assets/statue.glb" });
  eq("the id is carried through, because that is what a ScatterItem stores", scatterAssetOf(spec).id, "a1");
  eq("a row with no name falls back to its id", scatterAssetOf({ id: "a9", type: "code", name: "", file: "x.ts" }).name, "a9");
}

// ---- the sidecar round-trips --------------------------------------------------------------------
console.log("\nThe sidecar");
{
  eq("terrain sits beside the edits, same stem",
     terrainSidecarPath("C:/g/src/level.edits.json"), "C:/g/src/level.terrain.json");
  eq("...and beside a keyed sidecar too",
     terrainSidecarPath("C:/g/src/proplib.tree.edits.json"), "C:/g/src/proplib.tree.terrain.json");
  eq("a live game's root sidecar gets one too",
     terrainSidecarPath("C:/g/studio.edits.json"), "C:/g/studio.terrain.json");

  const palette: ScatterAsset[] = [
    { id: "a1", name: "Oak", ref: { kind: "code", spec: true, file: "p.ts", key: "nature.tree" } },
    { id: "a2", name: "Rock", ref: { kind: "model", url: "r.glb" } },
  ];
  const text = packTerrain("FIELD-BASE64", palette);
  const back = unpackTerrain(text);
  ok("it reads back", !!back);
  eq("the field is byte for byte what serialize gave us", back!.field, "FIELD-BASE64");
  eq("and every recipe survived", back!.palette, palette);

  ok("nonsense reads as no terrain rather than throwing", unpackTerrain("{{{") === null);
  ok("an empty file reads as no terrain", unpackTerrain("") === null);
  ok("a document with no field is refused", unpackTerrain('{"version":1,"palette":[]}') === null);
  eq("a palette entry with a made-up kind is dropped, and the rest kept",
     unpackTerrain('{"field":"F","palette":[{"id":"x","ref":{"kind":"wormhole"}},{"id":"y","ref":{"kind":"model","url":"a.glb"}}]}')!.palette.map((p) => p.id),
     ["y"]);

  const scatter: ScatterItem[] = [{ asset: "a2", at: [1, 0, 1], rot: 0, scale: 1 }];
  eq("only the recipes the ground actually stands on are written",
     usedPalette(scatter, palette).map((p) => p.id), ["a2"]);
}

// ---- the defaults ----------------------------------------------------------------------------
{
  eq("four layers, one per splat byte", DEFAULT_LAYERS.length, 4);
  ok("every layer has a colour and a tiling",
     DEFAULT_LAYERS.every((l) => /^#[0-9a-f]{6}$/i.test(l.colour) && l.tiling > 0));

  // makeTerrain ships ONE layer and the engine grows the array only when a brush paints into a
  // higher slot. A palette you cannot click until after you have painted is not a palette.
  const one = { ...field(), layers: [{ name: "ground", colour: "#7d8a6a", tiling: 8 }] } as TerrainData;
  ensureLayers(one);
  eq("a one-layer field is padded to four", one.layers.length, 4);
  eq("...without touching the one it had", one.layers[0].name, "ground");
  eq("...and the rest are the engine's own names", one.layers.slice(1).map((l) => l.name), ["dirt", "rock", "sand"]);
  const four = field();
  four.layers[2] = { name: "mine", colour: "#123456", tiling: 3 };
  ensureLayers(four);
  eq("a field that already has four is left alone", four.layers[2].name, "mine");
  eq("...and stays four", four.layers.length, 4);
  ok("the default brush is ready to use", !!BRUSH_BY_KIND[DEFAULT_BRUSH.kind]);
  ok("...and inside its own range", DEFAULT_BRUSH.radius >= RADIUS_MIN && DEFAULT_BRUSH.radius <= RADIUS_MAX);
}

// ---- the history on its own -------------------------------------------------------------------
{
  const t = field();
  const h = new TerrainHistory(REF, 3);
  const b: Brush = { ...DEFAULT_BRUSH, radius: 3 };
  for (let i = 0; i < 5; i++) {
    h.begin("s" + i);
    h.add(REF.applyBrush(t, b, 8 + i, 8, 0.5));
    h.end(t);
  }
  eq("the stack honours its own ceiling", h.depth, 3);
  eq("and it is the OLDEST that falls off", h.topLabel, "s4");
  h.clear();
  eq("clear empties both halves", h.depth + h.redoDepth, 0);
  ok("undo on an empty stack is null, not a throw", h.undo(t) === null);
  ok("redo on an empty stack is null, not a throw", h.redo(t) === null);
}

// ---- what it costs, on the size the brief named -------------------------------------------------
//
// Printed, never asserted: a timing that fails the suite on a busy machine is a timing nobody
// keeps. The number in terrainTool's header comes from this.
console.log("\nCost of a stroke on a 513-sample field");
{
  const t = field(513, 512);
  const tool = new TerrainTool(REF);
  tool.setKind("raise");
  tool.brush.radius = 24;
  const N = 120;
  const t0 = Date.now();
  let now = 1000;
  tool.down(t, { x: 40, z: 256 }, 100, now);
  for (let i = 1; i <= N; i++) { now += 16; tool.move(t, { x: 40 + i * 3, z: 256 + Math.sin(i / 8) * 40 }, 100 + i, now); }
  tool.up(t);
  const ms = Date.now() - t0;
  console.log("  " + N + " pointer moves, radius 24, reference engine: " + ms + " ms total, "
    + (ms / N).toFixed(3) + " ms a move");
  const t1 = Date.now();
  tool.history.undo(t);
  console.log("  one undo of that whole stroke: " + (Date.now() - t1) + " ms");
}

// ---- and against the real engine, once it exists --------------------------------------------------
console.log("\nAgainst terrain.ts itself");
{
  const engine: any = ENGINE;
  let built = false;
  try { engine.makeTerrain({ res: 33, size: 32 }); built = true; } catch { built = false; }
  if (!built) {
    console.log("  !! terrain.ts is not built yet — the engine checks below did not run.");
    console.log("  !! (its functions still throw \"is not built yet\"; re-run this suite when they do)");
  } else {
    const t: TerrainData = engine.makeTerrain({ res: 65, size: 64, maxHeight: 20 });
    const before = Array.from(t.height).join(",");
    const tool = new TerrainTool();                   // the REAL engine, not REF
    tool.setKind("raise");
    tool.brush.radius = 6;
    const pts: Array<[number, number]> = [];
    for (let i = 0; i < 60; i++) pts.push([10 + i * 0.6, 32 + Math.sin(i / 6) * 6]);
    const pushed = stroke(tool, t, pts);
    ok("a real stroke moves the real field", Array.from(t.height).join(",") !== before);
    ok("...and is one undo", pushed && tool.history.depth === 1, String(tool.history.depth));
    tool.history.undo(t);
    ok("...that puts the real field back exactly", Array.from(t.height).join(",") === before);

    // THE RELOAD. Sculpt, paint, scatter, write the sidecar the editor would write, read it back
    // the way the editor reads it, and compare every sample. This is the whole promise of "it
    // saves" minus the HTTP call in the middle.
    const saved: TerrainData = engine.makeTerrain({ res: 65, size: 64, maxHeight: 20 });
    ensureLayers(saved);
    const st = new TerrainTool();
    st.brush.radius = 9;
    st.setKind("raise");
    stroke(st, saved, [[16, 32], [22, 30], [28, 31], [34, 34]]);
    st.setKind("paint");
    st.brush.layer = 2;
    stroke(st, saved, [[20, 32], [26, 31], [32, 32]]);
    st.setKind("scatter");
    st.brush.asset = "a1";
    // Density is items per square world unit PER SECOND, and the stroke helper's steps are 16 ms
    // apart: at the default 0.05 a radius-9 brush asks for a fifth of a tree and plants nothing.
    st.brush.density = 1.5;
    stroke(st, saved, [[40, 40], [42, 40], [44, 41], [46, 41]], 250);
    const pal: ScatterAsset[] = [
      { id: "a1", name: "Oak", ref: { kind: "code", spec: true, file: "p.ts", key: "nature.tree" } },
      { id: "unused", name: "Rock", ref: { kind: "model", url: "r.glb" } },
    ];
    const text = packTerrain(engine.serialize(saved), usedPalette(saved.scatter, pal));
    const doc = unpackTerrain(text);
    ok("the sidecar reads back", !!doc);
    const back: TerrainData = engine.deserialize(doc!.field);
    ok("every height sample survives the reload",
       Array.from(back.height).join(",") === Array.from(saved.height).join(","));
    ok("so does every splat byte",
       Array.from(back.splat).join(",") === Array.from(saved.splat).join(","));
    eq("so do the four layers", back.layers.map((l) => l.name), saved.layers.map((l) => l.name));
    eq("and everything standing on the ground", back.scatter.length, saved.scatter.length);
    ok("the scattered items keep their asset and their place",
       JSON.stringify(back.scatter) === JSON.stringify(saved.scatter),
       JSON.stringify(back.scatter.slice(0, 1)));
    eq("only the recipes the ground actually uses are written", doc!.palette.map((p) => p.id), ["a1"]);

    // A FRAME OF A HELD STROKE, on the size the brief named: the brush step AND the mesh the
    // viewport has to rebuild because of it. Anything else is half the answer.
    const big: TerrainData = engine.makeTerrain({ res: 513, size: 512, maxHeight: 60 });
    const bt = new TerrainTool();
    bt.setKind("raise");
    bt.brush.radius = 24;
    const N = 120;
    let now = 1000;
    let brushNs = 0, meshNs = 0, cells = 0;
    bt.down(big, { x: 40, z: 256 }, 100, now);
    const hr = () => Number(process.hrtime.bigint());
    for (let i = 1; i <= N; i++) {
      now += 16;
      const a = hr();
      const rect = bt.move(big, { x: 40 + i * 3, z: 256 + Math.sin(i / 9) * 60 }, 100 + i, now);
      const b = hr();
      brushNs += b - a;
      if (!rect) continue;
      // What the viewport does with the answer: one grown region, the way TerrainView does it.
      const g = { x0: Math.max(0, rect.x0 - 1), z0: Math.max(0, rect.z0 - 1), w: 0, h: 0 };
      g.w = Math.min(513, rect.x0 + rect.w + 1) - g.x0;
      g.h = Math.min(513, rect.z0 + rect.h + 1) - g.z0;
      const c = hr();
      engine.terrainMesh(big, { region: g });
      meshNs += hr() - c;
      cells += g.w * g.h;
    }
    const e0 = hr();
    bt.up(big);
    const endMs = (hr() - e0) / 1e6;
    const w0 = hr();
    engine.terrainMesh(big);
    const wholeMs = (hr() - w0) / 1e6;
    const frame = (brushNs + meshNs) / N / 1e6;
    console.log("  513\u00b2 (263,169 samples), radius 24, " + N + " moves of a held stroke:");
    console.log("    brush            " + (brushNs / N / 1e6).toFixed(3) + " ms a frame");
    console.log("    region rebuild   " + (meshNs / N / 1e6).toFixed(3) + " ms a frame ("
      + Math.round(cells / N).toLocaleString() + " samples)");
    console.log("    ONE FRAME        " + frame.toFixed(3) + " ms  ("
      + (frame / 16.67 * 100).toFixed(1) + "% of a 60 fps budget)");
    console.log("    closing the stroke, once  " + endMs.toFixed(2) + " ms");
    console.log("    the whole field instead   " + wholeMs.toFixed(2) + " ms a frame  ("
      + (wholeMs / Math.max(0.001, meshNs / N / 1e6)).toFixed(0) + "x the region)");
  }
}

// ------------------------------------------------------------------ round two
//
// THE EMIT PATH, THE CHUNK DECISION AND THE PLAYCANVAS SPLAT.
//
// The one that earns its place is the last block. A shader cannot be checked by reading it, but
// WHICH ARRAY reaches the colour attribute can — and that single choice is what turned a field of
// olive ground cream last round. Here the emitted PlayCanvas module is actually executed against
// a stub engine, and the bytes that arrive at `setColors` are compared with both candidates.

console.log("\nThe emitted builder");
{
  eq("the builder sits beside the sidecar", terrainBuilderPath("C:/g/src/level.edits.json"),
     "C:/g/src/level.terrain.js");
  eq("a live game's builder sits at the root", terrainBuilderPath("/g/studio.edits.json"),
     "/g/studio.terrain.js");
  eq("the extension can be asked for", terrainBuilderPath("/g/a.edits.json", ".mjs"), "/g/a.terrain.mjs");

  eq("a named file names its export", builderName("/g/src/hills.terrain.js"), "buildHillsTerrain");
  eq("...through a hyphen too", builderName("north-ridge.terrain.js"), "buildNorthRidgeTerrain");
  eq("a file that just says terrain does not repeat itself", builderName("/g/terrain.js"), "buildTerrain");
  eq("...nor does the live game's", builderName("/g/studio.terrain.js"), "buildTerrain");
  eq("a name that would start with a digit is still an identifier",
     builderName("2nd.terrain.js").startsWith("build_"), true);

  eq("full detail is every sample", emitRes(513, 1), 513);
  eq("half detail halves the cells, not the samples", emitRes(513, 2), 257);
  eq("a step that does not divide the field is refused", emitRes(500, 3), 500);
}

console.log("\nWhich engine the ground is emitted for");
{
  const tabs = [{ project: "C:/games/rot-rush", engine: "playcanvas" }];
  const a = pickEmitEngine("C:\\games\\rot-rush", tabs, "three", false);
  eq("the RUNNING game wins over everything else", a.engine, "playcanvas");
  eq("...and the panel says why", a.why, "the running game is playcanvas");
  eq("...and knows it is running", a.running, true);

  const b = pickEmitEngine("C:/games/other", tabs, "playcanvas", false);
  eq("with no tab, the project's own engine decides", b.engine, "playcanvas");
  eq("...and it is not running", b.running, false);

  const c = pickEmitEngine("C:/games/other", [], "", true);
  eq("with no engine at all, the asset's own code decides", c.engine, "playcanvas");
  const d = pickEmitEngine("C:/games/other", null, "", false);
  eq("and with nothing to go on it is three, said out loud", d.engine, "three");
  ok("...with the guess admitted", /assumed/.test(d.why), d.why);
}

console.log("\nWhen the ground is drawn as chunks");
{
  eq("a 129 field is one mesh", wantChunks("auto", 129), false);
  eq("so is a 257", wantChunks("auto", 257), false);
  eq("a 513 is chunked", wantChunks("auto", 513), true);
  eq("and a 1025", wantChunks("auto", 1025), true);
  eq("always means always", wantChunks("on", 65), true);
  eq("never means never", wantChunks("off", 2049), false);
  ok("the threshold is a power of two plus one, like every res in the panel",
     Number.isInteger(Math.log2(CHUNK_ABOVE - 1)));
  ok("a chunk is a power of two of samples", Number.isInteger(Math.log2(CHUNK_SAMPLES)));
}

// ------------------------------------------------------------------ the PlayCanvas splat
//
// A stub engine: every call the material and the mesh make, recorded. No GPU, no canvas, and no
// PlayCanvas — which is the point, because the check is about which numbers go where.

function stubPc(opts: { chunks?: "map" | "object" | "none" } = {}) {
  const kind = opts.chunks || "map";
  const calls: any = { setColors: null, params: {}, chunks: new Map<string, string>() };
  class Mesh {
    positions: any = null; normals: any = null; uvs: any = null; colors: any = null; indices: any = null;
    updated = 0;
    setPositions(v: any) { this.positions = v; }
    setNormals(v: any) { this.normals = v; }
    setUvs(_i: number, v: any) { this.uvs = v; }
    setColors(v: any, n: number) { this.colors = v; calls.setColors = { v, n }; }
    setIndices(v: any) { this.indices = v; }
    update() { this.updated++; }
  }
  class StandardMaterial {
    name = ""; useMetalness = true; gloss = 0; diffuseVertexColor = false;
    userData: any = {}; updated = 0;
    diffuse = { set: () => {} };
    private _old: any = {};
    getShaderChunks() { return kind === "map" ? calls.chunks : undefined; }
    get chunks() { return kind === "object" ? this._old : undefined; }
    set chunks(v: any) { if (kind === "object") this._old = v; }
    setParameter(k: string, v: any) { calls.params[k] = v; }
    update() { this.updated++; }
  }
  class Entity {
    name: string; render: any = null;
    constructor(n: string) { this.name = n; }
    addComponent(_k: string, cfg: any) { this.render = cfg; }
  }
  const pc: any = {
    Mesh, StandardMaterial, Entity,
    GraphNode: class {},
    MeshInstance: class { constructor(public mesh: any, public material: any, public node: any) {} },
    Texture: class { lock() { return new Uint8Array(4); } unlock() {} },
    PRIMITIVE_TRIANGLES: 4,
    SHADERLANGUAGE_GLSL: "glsl",
    CHUNKAPI_2_5: "2.5",
  };
  if (kind === "none") delete (StandardMaterial.prototype as any).getShaderChunks;
  return { pc, app: { graphicsDevice: {} }, calls };
}

const LAYERS4 = DEFAULT_LAYERS.map((l) => ({ ...l }));

console.log("\nThe PlayCanvas splat material");
{
  const [r, g, b] = hexToLinear("#7d8a6a");
  ok("hex arrives linear, not sRGB", r < 0.5 / 1 && r > 0.15 && g > r && b < g, [r, g, b].join(","));
  eq("a bad colour still gives three numbers", hexToLinear("nonsense").length, 3);

  const { pc, app, calls } = stubPc();
  const mat = pcSplatMaterial(pc, app, LAYERS4);
  eq("the chunk installed", mat.userData.splat, true);
  eq("...as diffusePS", calls.chunks.get("diffusePS"), PC_SPLAT_CHUNK);
  eq("the varying is asked for, or the chunk reads a name nothing declared", mat.diffuseVertexColor, true);
  ok("four layer colours went in", ["uSplatCol0", "uSplatCol1", "uSplatCol2", "uSplatCol3"]
     .every((k) => Array.isArray(calls.params[k]) && calls.params[k].length === 3));
  eq("no textures means no texture flags", calls.params.uSplatHas, [0, 0, 0, 0]);
  eq("the tiling is the palette's own", calls.params.uSplatTile, LAYERS4.map((l) => l.tiling));
  ok("all four samplers are bound anyway — an unbound one renders black",
     [0, 1, 2, 3].every((i) => !!calls.params["uSplatTex" + i]));

  const withTex = stubPc();
  pcSplatMaterial(withTex.pc, withTex.app, LAYERS4, { textures: [null, { fake: 1 }, null, null] });
  eq("a layer with a texture raises its flag and only its own", withTex.calls.params.uSplatHas, [0, 1, 0, 0]);

  const old = stubPc({ chunks: "object" });
  const om = pcSplatMaterial(old.pc, old.app, LAYERS4);
  eq("an engine older than 2.7 takes the chunk through the object", om.userData.splat, true);
  eq("...with the API version it needs", (om as any).chunks.APIVersion, "2.5");

  const none = stubPc({ chunks: "none" });
  const nm = pcSplatMaterial(none.pc, none.app, LAYERS4);
  eq("an engine with no chunk system says so instead of pretending", nm.userData.splat, false);
  eq("...and pcSetChunk agrees", pcSetChunk(none.pc, nm, "diffusePS", "x"), false);
}

console.log("\nWhich array reaches the colour attribute — the cream-field check");
{
  const colors = new Float32Array([0.5, 0.4, 0.3, 1, 0.5, 0.4, 0.3, 1]);
  const weights = new Float32Array([1, 0, 0, 0, 0.25, 0.75, 0, 0]);
  const m = { positions: new Float32Array(6), normals: new Float32Array(6), uvs: new Float32Array(4),
              colors, indices: new Uint32Array([0, 1, 0]), weights };
  ok("with the shader in, the mesh gets the WEIGHTS", pcSplatColours(m, true) === weights);
  ok("without it, the mesh gets the blended COLOURS", pcSplatColours(m, false) === colors);
  ok("an engine that hands back no weights falls back to the colours rather than to nothing",
     pcSplatColours({ colors, indices: m.indices } as any, true) === colors);

  const a = stubPc();
  pcTerrainEntity(a.pc, a.app, m, LAYERS4);
  ok("...and the entity actually uploads the weights", a.calls.setColors.v === weights);
  eq("four components, not three", a.calls.setColors.n, 4);

  const b = stubPc({ chunks: "none" });
  pcTerrainEntity(b.pc, b.app, m, LAYERS4);
  ok("with no shader it uploads the blend, so the ground is flat and right rather than primary",
     b.calls.setColors.v === colors);
}

console.log("\nThe emitted PlayCanvas module, run");
{
  const engine: any = ENGINE;
  let built = false;
  try { engine.makeTerrain({ res: 33, size: 32 }); built = true; } catch { built = false; }
  if (!built) {
    console.log("  !! terrain.ts is not built yet — the emit checks below did not run.");
  } else {
    const t: TerrainData = ensureLayers(engine.makeTerrain({ res: 33, size: 32, maxHeight: 10 }));
    // Two layers painted, so the weights are not all layer 0 and a mistake has somewhere to show.
    engine.applyBrush(t, { kind: "raise", radius: 8, strength: 1, falloff: 0.7 }, 16, 16, 1);
    engine.applyBrush(t, { kind: "paint", radius: 6, strength: 1, falloff: 0.5, layer: 2 }, 16, 16, 1);
    t.scatter.push({ asset: "oak", at: [4, 0, 4], rot: 0.5, scale: 1.1 });

    const three = engine.emitBuilder(t, "buildTerrain", "three");
    ok("a three emit is unchanged by any of this", /THREE.BufferGeometry/.test(three)
       && !/uSplatCol0/.test(three));

    const fn = "buildHillsTerrain";
    // No `+ pcSplatSource(fn)`: emitBuilder writes it. Appending it as well is a duplicate
    // `export function` and a SyntaxError, which is exactly why it can only live in one place.
    const src = engine.emitBuilder(t, fn, "playcanvas");
    ok("the splat is appended once", (src.match(/const SPLAT_CHUNK/g) || []).length === 1);
    ok("the shader in the file is this module's, not a copy of it", src.includes(JSON.stringify(PC_SPLAT_CHUNK)));

    // Run it. `export` is stripped rather than parsed away, so the file's own statements execute
    // exactly as written — an emitted file that does not RUN is the failure worth catching.
    const body = src.replace(/^export default .*$/gm, "").replace(/^export /gm, "")
      + "\nreturn { " + fn + ", " + fn + "Splat, " + fn + "Material, " + fn + "Weights };";
    let mod: any = null;
    let threw = "";
    try { mod = new Function(body)(); } catch (e: any) { threw = String(e?.message || e); }
    ok("the emitted PlayCanvas module parses and runs", !!mod, threw);

    if (mod) {
      const { pc, app, calls } = stubPc();
      const ent = mod[fn + "Splat"](pc, app);
      eq("it builds an entity", ent.name, fn);
      const mi = ent.render.meshInstances[0];
      eq("with a render component and one mesh instance", !!mi, true);
      eq("the material is the splat one", mi.material.userData.splat, true);
      eq("...with the four layer colours bound", Object.keys(calls.params).filter((k) => k.startsWith("uSplatCol")).length, 4);

      // The last setColors wins, and it must be the weights — which means the LAST thing the
      // builder did was replace the blended colours the plain path uploaded.
      const w = mod[fn + "Weights"](1);
      eq("the colour attribute ends up holding the weights", calls.setColors.v, w);
      eq("one weight per vertex of the mesh", w.length, (mi.mesh.positions.length / 3) * 4);

      // And the weights are the FIELD's, sample for sample, in the mesh's own vertex order.
      const res = t.spec.res;
      let worst = 0;
      for (let j = 0; j < res; j++) {
        for (let i = 0; i < res; i++) {
          const v = (j * res + i) * 4, o = (j * res + i) * 4;
          for (let k = 0; k < 4; k++) worst = Math.max(worst, Math.abs(w[v + k] - t.splat[o + k] / 255));
        }
      }
      ok("every weight is the field's own, to a byte", worst < 1 / 255 + 1e-6, "worst " + worst);

      // The same weights `terrainMesh` hands the three viewport, so the two renderers cannot
      // disagree about which layer a sample is.
      const mesh = engine.terrainMesh(t);
      let diff = 0;
      for (let i = 0; i < w.length; i++) diff = Math.max(diff, Math.abs(w[i] - (mesh.weights as Float32Array)[i]));
      ok("and the same ones the three viewport blends", diff < 1e-6, "worst " + diff);

      const flat = mod[fn](pc, app);
      ok("the plain builder still works and is untouched", !!flat.render.meshInstances[0]);
    }
  }
}

// ------------------------------------------------------------------------------------------
console.log("\n  " + pass + " passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
