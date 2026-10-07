// Paint on the model: the part with no GPU in it.
//
// The shaders in paintGpu.ts follow the formulas in paintCore.ts line for line, so this file is
// where the arithmetic of paint is held to account: how hard an edge falls off, how far apart a
// stroke's dabs are, what Multiply over a transparent layer gives, what one undo takes back, and
// that a paint file read back is the paint file written. What the GPU does with them is proved in
// the real Edit tab (data/tmp/paint_proof/prove_paint.py).
//
// Run: npm run test:paint

import {
  BLEND_INDEX, BLEND_MODES, DEFAULT_BRUSH, DabPlacer, MAX_REPLAY_DABS, PAINT_TOOLS, PRESETS, PaintHistory, TIP_KINDS,
  TOOL_DEFAULTS, bakeStroke, blendChannel, capStrokes, compositeOver, dirtyTiles, falloff, hexToRgb, layerId,
  linearToSrgb, mulberry32, paintDocPath, paintKey, parsePaintDoc, pressureOf, pushRecent, recordBytes, rgbToHex,
  srgbToLinear, tileRuns, tipMask, uvSignature, triangleIslands, encodePNG, crc32, bytesToBase64, base64ToBytes,
  premultiply, unpremultiply, addUvTriangles, uvTriangleSetSignature, closestOnTriangle, buildPosGrid, nearestOnGrid,
  buildUvGrid, uvLocate, lerpTri,
  type BrushSettings, type PaintDoc, type RGBA,
} from "../src/components/engine/edit/paintCore";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
const near = (a: number, b: number, eps = 1e-6) => Math.abs(a - b) <= eps;

// ------------------------------------------------------------------ falloff
{
  ok("a dab is full in the middle", falloff(0, 0.5) === 1);
  ok("and nothing at the rim", falloff(1, 0.5) === 0 && falloff(1.2, 0.9) === 0);
  ok("a hard brush is solid out to its hardness", falloff(0.79, 0.8) === 1);
  ok("then falls smoothly", falloff(0.9, 0.8) > 0 && falloff(0.9, 0.8) < 1);
  let mono = true;
  for (let r = 0; r < 1; r += 0.01) if (falloff(r + 0.01, 0.3) > falloff(r, 0.3) + 1e-12) mono = false;
  ok("the fall never rises again", mono);
  ok("a soft brush has half its strength near the middle of its radius", near(falloff(0.5, 0), 0.5, 1e-9));
}

// ------------------------------------------------------------------ pressure
{
  ok("a mouse is full pressure, whatever it reports", pressureOf({ pressure: 0.5, pointerType: "mouse" }) === 1);
  ok("a pen reads its pressure", pressureOf({ pressure: 0.3, pointerType: "pen" }) === 0.3);
  ok("a pen that reports nothing counts as half", pressureOf({ pressure: 0, pointerType: "pen" }) === 0.5);
  ok("touch with no pressure is full", pressureOf({ pressure: 0, pointerType: "touch" }) === 1);
}

// ------------------------------------------------------------------ colour
{
  ok("hex to rgb", JSON.stringify(hexToRgb("#ff8000")) === JSON.stringify([1, 128 / 255, 0]));
  ok("three-digit hex", rgbToHex(hexToRgb("#fa0")) === "#ffaa00");
  ok("rgb to hex round-trips", rgbToHex(hexToRgb("#1a2b3c")) === "#1a2b3c");
  ok("a bad hex is black, never a throw", rgbToHex(hexToRgb("nope")) === "#000000");
  let worst = 0;
  for (let i = 0; i <= 255; i++) worst = Math.max(worst, Math.abs(Math.round(linearToSrgb(srgbToLinear(i / 255)) * 255) - i));
  ok("sRGB and linear round-trip every 8-bit value", worst === 0, "worst " + worst);
  let r = pushRecent([], "#ff0000");
  r = pushRecent(r, "#00FF00");
  r = pushRecent(r, "#ff0000");
  ok("recent colours: newest first, no duplicates", JSON.stringify(r) === JSON.stringify(["#ff0000", "#00ff00"]));
  let many: string[] = [];
  for (let i = 0; i < 30; i++) many = pushRecent(many, rgbToHex([i / 30, 0, 0]));
  ok("and at most twelve", many.length === 12);
}

// ------------------------------------------------------------------ blending
{
  ok("normal gives the paint", blendChannel("normal", 0.2, 0.7) === 0.7);
  ok("multiply", near(blendChannel("multiply", 0.5, 0.5), 0.25));
  ok("screen", near(blendChannel("screen", 0.5, 0.5), 0.75));
  ok("overlay darkens a dark backdrop", blendChannel("overlay", 0.2, 0.5) < 0.2 + 1e-9);
  ok("add saturates at 1", blendChannel("add", 0.8, 0.5) === 1);
  ok("darken and lighten", blendChannel("darken", 0.3, 0.6) === 0.3 && blendChannel("lighten", 0.3, 0.6) === 0.6);
  ok("every mode has a shader index, all different",
    new Set(BLEND_MODES.map((m) => BLEND_INDEX[m])).size === BLEND_MODES.length);

  const half = compositeOver([0, 0, 0], 1, [1, 1, 1], 0.5, "normal");
  ok("50% white over opaque black is 50% grey, opaque", near(half.c[0], 0.5) && near(half.a, 1));
  const onEmpty = compositeOver([0, 0, 0], 0, [0.2, 0.4, 0.6], 0.7, "multiply");
  ok("multiply on a transparent backdrop paints its own colour", near(onEmpty.c[1], 0.4) && near(onEmpty.a, 0.7));
  const mult = compositeOver([0.5, 0.5, 0.5], 1, [0.5, 0.5, 0.5], 1, "multiply");
  ok("multiply on an opaque backdrop multiplies", near(mult.c[0], 0.25));

  // Baking a stroke into a premultiplied layer.
  const empty: RGBA = [0, 0, 0, 0];
  const red = bakeStroke(empty, [1, 0, 0], 1, "normal", false);
  ok("a full stroke on an empty layer is the colour, opaque", near(red[0], 1) && near(red[3], 1) && near(red[1], 0));
  const halfRed = bakeStroke(empty, [1, 0, 0], 0.5, "normal", false);
  ok("half coverage is half alpha, premultiplied", near(halfRed[0], 0.5) && near(halfRed[3], 0.5));
  const twice = bakeStroke(halfRed, [1, 0, 0], 0.5, "normal", false);
  ok("two half strokes of one colour reach 75%", near(twice[3], 0.75));
  const erased = bakeStroke(red, [0, 0, 0], 0.25, "normal", true);
  ok("an eraser at 25% leaves 75%", near(erased[3], 0.75) && near(erased[0], 0.75));
  const gone = bakeStroke(red, [0, 0, 0], 1, "normal", true);
  ok("a full eraser leaves nothing", gone.every((v) => near(v, 0)));
}

// ------------------------------------------------------------------ dab spacing
{
  const brush: BrushSettings = { ...DEFAULT_BRUSH, size: 20, spacing: 0.25, pressureSize: false };
  const p = new DabPlacer(brush, 1);
  const first = p.begin({ x: 0, y: 0, pressure: 1 });
  ok("a stroke starts with one dab where the pen touched", first.length === 1 && first[0].x === 0 && first[0].y === 0);
  const line = p.moveTo({ x: 100, y: 0, pressure: 1 });
  ok("a 100 px line at 5 px spacing puts twenty dabs", line.length === 20, String(line.length));
  let evenly = true;
  let prev = 0;
  for (const d of line) { if (!near(d.x - prev, 5, 1e-6)) evenly = false; prev = d.x; }
  ok("evenly, 5 px apart", evenly);

  // The same line in many short moves is spaced the same.
  const q = new DabPlacer(brush, 1);
  q.begin({ x: 0, y: 0, pressure: 1 });
  const parts: number[] = [];
  for (let x = 3; x <= 100; x += 3) for (const d of q.moveTo({ x, y: 0, pressure: 1 })) parts.push(d.x);
  let same = parts.length >= 19;
  for (let i = 0; i < parts.length; i++) if (!near(parts[i], (i + 1) * 5, 1e-6)) same = false;
  ok("a stroke of 33 short moves is spaced like one long move", same, parts.slice(0, 5).join(","));

  const still = new DabPlacer(brush, 1);
  still.begin({ x: 5, y: 5, pressure: 1 });
  ok("a pen that does not move adds nothing", still.moveTo({ x: 5, y: 5, pressure: 1 }).length === 0);

  const pr: BrushSettings = { ...DEFAULT_BRUSH, size: 40, spacing: 0.1, pressureSize: true };
  const pp = new DabPlacer(pr, 3);
  const light = pp.begin({ x: 0, y: 0, pressure: 0 })[0];
  ok("pressure 0 still paints a small dab, not none", light.size > 0 && light.size < 40 * 0.2);
  ok("full pressure paints the full size", near(pp.sizeAt(1), 40));
  const soft: BrushSettings = { ...DEFAULT_BRUSH, flow: 0.5, pressureOpacity: true };
  const sp = new DabPlacer(soft, 3);
  ok("pressure on opacity scales the dab's alpha", near(sp.begin({ x: 0, y: 0, pressure: 0.5 })[0].alpha, 0.25));

  const jit: BrushSettings = { ...DEFAULT_BRUSH, sizeJitter: 0.5, angleJitter: 90, scatter: 0.5 };
  const a1 = new DabPlacer(jit, 42), a2 = new DabPlacer(jit, 42);
  a1.begin({ x: 0, y: 0, pressure: 1 }); a2.begin({ x: 0, y: 0, pressure: 1 });
  const s1 = JSON.stringify(a1.moveTo({ x: 200, y: 50, pressure: 1 }));
  const s2 = JSON.stringify(a2.moveTo({ x: 200, y: 50, pressure: 1 }));
  ok("a jittered stroke is the same stroke again with the same seed", s1 === s2);
  const a3 = new DabPlacer(jit, 43);
  a3.begin({ x: 0, y: 0, pressure: 1 });
  ok("and a different one with another seed", JSON.stringify(a3.moveTo({ x: 200, y: 50, pressure: 1 })) !== s1);

  const fol: BrushSettings = { ...DEFAULT_BRUSH, followStroke: true, angle: 0, spacing: 0.5, size: 10 };
  const fp = new DabPlacer(fol, 1);
  fp.begin({ x: 0, y: 0, pressure: 1 });
  const up = fp.moveTo({ x: 0, y: 50, pressure: 1 });
  ok("a tip that follows the stroke turns with it", up.length > 0 && near(up[0].angle, Math.PI / 2, 1e-9));

  const tiny: BrushSettings = { ...DEFAULT_BRUSH, size: 0.5, spacing: 0.01 };
  const tp = new DabPlacer(tiny, 1);
  tp.begin({ x: 0, y: 0, pressure: 1 });
  ok("a jump with a tiny brush is capped, not a stall", tp.moveTo({ x: 100000, y: 0, pressure: 1 }).length <= 4096);
}

// ------------------------------------------------------------------ stamps
{
  for (const k of TIP_KINDS) {
    const m = tipMask(k, 64, 7);
    let sum = 0;
    for (const v of m) sum += v;
    ok("the " + k + " stamp has paint in it", m.length === 64 * 64 && sum > 0);
    ok("the " + k + " stamp is the same every time", tipMask(k, 64, 7).join(",") === m.join(","));
  }
  const round = tipMask("round", 64);
  ok("the round stamp is solid in the middle and empty in the corner", round[32 * 64 + 32] === 255 && round[0] === 0);
  const sq = tipMask("square", 64);
  ok("the square stamp fills near its corner, where a disk would not", sq[8 * 64 + 8] === 255 && round[8 * 64 + 8] === 0);
  const s7 = tipMask("splatter", 64, 7), s8 = tipMask("splatter", 64, 8);
  ok("another seed splatters differently", s7.join(",") !== s8.join(","));
  let chalkHoles = 0, chalkInside = 0;
  const ch = tipMask("chalk", 64, 7);
  for (let j = 16; j < 48; j++) for (let i = 16; i < 48; i++) { chalkInside++; if (ch[j * 64 + i] < 128) chalkHoles++; }
  ok("chalk leaves small gaps inside its rim", chalkHoles > chalkInside * 0.05 && chalkHoles < chalkInside * 0.7,
    chalkHoles + " of " + chalkInside);
}

// ------------------------------------------------------------------ undo
{
  const h = new PaintHistory(10_000, 1);
  const tile = (bytes: number) => ({ x: 0, y: 0, w: 1, h: 1, before: new Uint8Array(bytes), after: new Uint8Array(bytes) });
  let freed = 0;
  h.push({ label: "a", target: "t", layer: "L1", tiles: [tile(1000)], free: () => freed++ });
  h.push({ label: "b", target: "t", layer: "L1", tiles: [tile(1000)] });
  ok("two strokes, two steps", h.depth === 2 && h.topLabel === "b");
  ok("a step's size counts its tiles", recordBytes({ label: "", target: "", layer: "", tiles: [tile(1000)] }) === 2064);
  const u = h.takeUndo();
  ok("undo gives back the last stroke", u?.label === "b" && h.depth === 1 && h.redoDepth === 1);
  const r = h.takeRedo();
  ok("redo puts it back", r?.label === "b" && h.depth === 2 && h.redoDepth === 0);
  h.takeUndo();
  h.push({ label: "c", target: "t", layer: "L1", tiles: [tile(10)] });
  ok("a new stroke after an undo drops the redo", h.redoDepth === 0 && h.topLabel === "c");
  h.push({ label: "big", target: "t", layer: "L1", tiles: [tile(4000)] });
  ok("past the memory cap the oldest steps go, and free what they held", h.bytes <= 10_000 && freed === 1, h.bytes + " / freed " + freed);
  const keep = new PaintHistory(1, 3);
  for (let i = 0; i < 6; i++) keep.push({ label: "s" + i, target: "t", layer: "L", tiles: [tile(100)] });
  ok("but never fewer than the minimum, however big", keep.depth === 3);
  keep.clear();
  ok("clear empties both lists", keep.depth === 0 && keep.redoDepth === 0 && keep.bytes === 0);

  const reduced = new Uint8Array(4 * 4 * 4);
  reduced[(1 * 4 + 1) * 4 + 3] = 9;          // tile (1,1) painted
  const dt = dirtyTiles(reduced, 4, 4, 1);
  ok("one painted tile with its ring is nine tiles", dt.length === 9 && dt.includes(0) && dt.includes(10));
  const edge = new Uint8Array(4 * 4 * 4);
  edge[0] = 1;
  ok("a corner tile's ring stays inside the grid", dirtyTiles(edge, 4, 4, 1).length === 4);
  const runs = tileRuns([0, 1, 2, 5], 4, 64, 256, 256);
  ok("adjacent tiles read as one run", runs.length === 2 && runs[0].w === 192 && runs[1].x === 64 && runs[1].y === 64);
  const clip = tileRuns([3], 4, 64, 200, 200);
  ok("a run at the edge is clipped to the texture", clip[0].w === 200 - 192 && clip[0].h === 64);
}

// ------------------------------------------------------------------ keys
{
  const k = (key: string, more: Record<string, any> = {}) => paintKey({ key, ...more });
  ok("B brush, E eraser, G fill, I eyedropper",
    k("b")?.kind === "tool" && (k("e") as any).tool === "eraser" && (k("g") as any).tool === "fill" && (k("i") as any).tool === "picker");
  ok("S clone, R smudge, L blur", (k("s") as any).tool === "clone" && (k("r") as any).tool === "smudge" && (k("l") as any).tool === "blur");
  ok("] grows, [ shrinks", (k("]") as any).factor > 1 && (k("[") as any).factor < 1);
  ok("Shift+] is harder", (k("}", { shiftKey: true }) as any).delta > 0);
  ok("3 is 30% opacity, 0 is 100%", (k("3", { code: "Digit3" }) as any).value === 0.3 && (k("0", { code: "Digit0" }) as any).value === 1);
  ok("Shift+5 sets flow", k("%", { code: "Digit5", shiftKey: true })?.kind === "flow");
  ok("Ctrl+Z undoes, Ctrl+Shift+Z and Ctrl+Y redo",
    k("z", { ctrlKey: true })?.kind === "undo" && k("Z", { ctrlKey: true, shiftKey: true })?.kind === "redo" && k("y", { ctrlKey: true })?.kind === "redo");
  ok("X swaps, D resets, M mirrors, P leaves", k("x")?.kind === "swap" && k("d")?.kind === "defaults" && k("m")?.kind === "mirror" && k("p")?.kind === "leave");
  ok("Alt with a letter is left alone (it is the eyedropper click)", k("b", { altKey: true }) === null);
  ok("a key paint does not use is not taken", k("q") === null && k("Tab") === null);
}

// ------------------------------------------------------------------ presets and tools
{
  ok("every tool has a brush of its own", PAINT_TOOLS.every((t) => !!TOOL_DEFAULTS[t]));
  ok("every preset is for a real tool with a real tip",
    PRESETS.every((p) => PAINT_TOOLS.includes(p.tool) && (!p.brush.tip || TIP_KINDS.includes(p.brush.tip))));
  ok("preset ids are unique", new Set(PRESETS.map((p) => p.id)).size === PRESETS.length);
  ok("the pens a painter asks for are there", ["ink", "hard", "soft", "marker", "chalk", "splatter"].every((id) => PRESETS.some((p) => p.id === id)));
}

// ------------------------------------------------------------------ the paint file
{
  ok("the paint file sits beside the edits", paintDocPath("C:/a/orc.glb.edits.json") === "C:/a/orc.glb.paint.json");
  ok("layer ids are new", layerId(["L1", "L2"]) === "L3" && layerId([]) === "L1");
  const doc: PaintDoc = {
    version: 1, asset: "C:/a/orc.js", updated: 1,
    targets: [{
      key: "orc:1024x1024:body", name: "orc", size: [1024, 1024], scale: 1, uvSig: "abc:1:2", made: null, active: "L2",
      layers: [
        { id: "L1", name: "Base", visible: true, opacity: 1, blend: "normal", png: "iVBORw0KGgo=" },
        { id: "L2", name: "Scars", visible: false, opacity: 0.4, blend: "multiply", png: "data:image/png;base64,AAAA" },
      ],
      strokes: [{
        tool: "brush", layer: "L2", color: "#FF0000", opacity: 1, hardness: 0.8, tip: "chalk", blend: "normal",
        dabs: [{ k: "body", p: [0, 1, 2], n: [0, 0, 1], r: 0.05, a: 1, g: 0, s: 5 }], fill: null,
      }],
      unreplayable: 2,
    }],
  };
  const back = parsePaintDoc(JSON.stringify(doc))!;
  ok("a paint file reads back", !!back && back.targets.length === 1 && back.targets[0].layers.length === 2);
  ok("with its layer settings", back.targets[0].layers[1].blend === "multiply" && back.targets[0].layers[1].visible === false
    && back.targets[0].layers[1].opacity === 0.4);
  ok("a data: prefix on a layer is taken off", back.targets[0].layers[1].png === "AAAA");
  ok("stroke colours come back normalised", back.targets[0].strokes[0].color === "#ff0000");
  ok("and the stroke's dabs", back.targets[0].strokes[0].dabs[0].r === 0.05);
  ok("junk is not a paint file", parsePaintDoc("not json") === null && parsePaintDoc("{}") === null);
  const bad = parsePaintDoc(JSON.stringify({ version: 1, targets: [
    { key: "x", size: [0, 10], layers: [] },
    { key: "y", size: [16, 16], layers: [{ id: 3, png: "a" }, { id: "L1", png: "b", blend: "weird", opacity: 7 }],
      strokes: [{ tool: "paint", layer: "L1", dabs: [] }, { tool: "brush", layer: "L1", dabs: [{ k: "m", p: [0, 0], n: [0, 0, 1] }] }] },
  ] }))!;
  ok("a target with no size is dropped; a broken layer is dropped; a wild value is clamped",
    bad.targets.length === 1 && bad.targets[0].layers.length === 1 && bad.targets[0].layers[0].blend === "normal"
    && bad.targets[0].layers[0].opacity === 1);
  ok("a stroke with no sound dab is dropped", bad.targets[0].strokes.length === 0);
  const fills = parsePaintDoc(JSON.stringify({ version: 1, targets: [{ key: "f", size: [16, 16], layers: [], strokes: [
    { tool: "fill", layer: "L1", dabs: [], fill: { k: "torso", tri: 12, island: true, p: [0.1, 0.2, 0.3], w: [1, 2, 3] } },
    { tool: "fill", layer: "L1", dabs: [], fill: { k: "arm", tri: 4, island: false, p: [0, "x", 1], w: null } },
  ] }] }))!;
  const f0 = fills.targets[0].strokes[0].fill!, f1 = fills.targets[0].strokes[1].fill!;
  ok("a fill keeps the point it was clicked at, on the mesh and in the asset",
    f0.island && f0.tri === 12 && f0.p!.join() === "0.1,0.2,0.3" && f0.w!.join() === "1,2,3");
  ok("and a broken point is left out, not the fill", fills.targets[0].strokes.length === 2 && !f1.island && f1.p === undefined && f1.w === undefined);
  const withU = parsePaintDoc(JSON.stringify({ version: 1, targets: [{ key: "u", size: [16, 16], layers: [], strokes: [
    { tool: "brush", layer: "L1", dabs: [{ k: "m", p: [0, 0, 0], n: [0, 0, 1], u: [0.25, 0.5], vt: [0.1, -0.2, -0.97] }, { k: "m", p: [0, 0, 0], n: [0, 0, 1], u: [1, "x"], vt: [1, 2] }] },
    { tool: "fill", layer: "L1", dabs: [], fill: { k: "m", tri: 0, island: true, u: [0.1, 0.2] } },
  ] }] }))!;
  ok("a dab and a fill keep their texture point; a broken one is left out",
    withU.targets[0].strokes[0].dabs[0].u!.join() === "0.25,0.5" && withU.targets[0].strokes[0].dabs[1].u === undefined
    && withU.targets[0].strokes[1].fill!.u!.join() === "0.1,0.2");
  ok("a screen dab keeps the direction it was painted from; a broken one is left out",
    withU.targets[0].strokes[0].dabs[0].vt!.join() === "0.1,-0.2,-0.97" && withU.targets[0].strokes[0].dabs[1].vt === undefined);

  const uv = new Float32Array([0, 0, 1, 0, 0, 1]);
  const s1 = uvSignature(uv, [0, 1, 2]);
  ok("a layout signature is stable", s1 === uvSignature(new Float32Array([0, 0, 1, 0, 0, 1]), [0, 1, 2]));
  ok("and changes when the layout changes", s1 !== uvSignature(new Float32Array([0, 0, 1, 0, 0, 0.5]), [0, 1, 2])
    && s1 !== uvSignature(uv, [0, 2, 1]));
  ok("but not for a change below 1e-5", s1 === uvSignature(new Float32Array([0, 0, 1 + 1e-7, 0, 0, 1]), [0, 1, 2]));

  // The layout as a set of texture triangles: two quads in one mesh, or the same quads as two
  // meshes with their triangles in another order and their corners turned, are one layout.
  const quadUV = new Float32Array([0, 0, 0.5, 0, 0.5, 0.5, 0, 0.5, 0.5, 0.5, 1, 0.5, 1, 1, 0.5, 1]);
  const merged = new Set<number>();
  addUvTriangles(merged, quadUV, [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7], 8);
  const split = new Set<number>();
  addUvTriangles(split, new Float32Array([0.5, 0.5, 1, 0.5, 1, 1, 0.5, 1]), [3, 0, 2, 1, 2, 0], 4);
  addUvTriangles(split, new Float32Array([0, 0, 0.5, 0, 0.5, 0.5, 0, 0.5]), [2, 0, 1, 3, 0, 2], 4);
  ok("merging parts into one mesh, or splitting them, keeps the layout", uvTriangleSetSignature(merged) === uvTriangleSetSignature(split)
    && merged.size === 4, uvTriangleSetSignature(merged) + " vs " + uvTriangleSetSignature(split));
  const moved = new Set<number>();
  const quadUV2 = quadUV.slice(); quadUV2[12] = 0.9;
  addUvTriangles(moved, quadUV2, [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7], 8);
  ok("but a moved texture corner is another layout", uvTriangleSetSignature(moved) !== uvTriangleSetSignature(merged));
  const twice = new Set<number>(merged);
  addUvTriangles(twice, quadUV, [0, 1, 2, 0, 2, 3], 8);
  ok("and a second part on the same texture triangles is the same layout", uvTriangleSetSignature(twice) === uvTriangleSetSignature(merged));
  const group = new Set<number>();
  addUvTriangles(group, quadUV, [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7], 8, 6, 6);
  ok("a material group takes only its own triangles", group.size === 2);
  const flat = new Set<number>();
  addUvTriangles(flat, new Float32Array([0, 0, 0.5, 0, 0.5, 0.5, 0, 0, 0.5, 0.5, 0, 0.5]), null, 6);
  const flatSame = new Set<number>();
  addUvTriangles(flatSame, quadUV, [0, 1, 2, 0, 2, 3], 8);
  ok("a geometry with no index gives the same triangles as its indexed twin", uvTriangleSetSignature(flat) === uvTriangleSetSignature(flatSame));

  const strokes = Array.from({ length: 10 }, (_, i) => ({
    tool: "brush" as const, layer: "L1", color: "#000000", opacity: 1, hardness: 1, tip: "round" as const, blend: "normal" as const,
    dabs: Array.from({ length: 100 }, () => ({ k: "m", p: [i, 0, 0] as [number, number, number], n: [0, 0, 1] as [number, number, number], r: 1, a: 1, g: 0, s: 0 })),
  }));
  const capped = capStrokes(strokes, 450);
  ok("too many replay dabs drop the OLDEST strokes and say how many", capped.kept.length === 4 && capped.dropped === 6
    && capped.kept[0].dabs[0].p[0] === 6);
  ok("the default cap is generous", MAX_REPLAY_DABS >= 50000);
}

// ------------------------------------------------------------------ a point on a rebuilt surface
{
  const tri = (p: number[], a: number[], b: number[], c: number[]) => closestOnTriangle(p, a, b, c);
  const A = [0, 0, 0], B = [1, 0, 0], C = [0, 1, 0];
  const inside = tri([0.25, 0.25, 2], A, B, C);
  ok("the nearest point of a triangle to a point above it is straight below", near(inside.point[0], 0.25) && near(inside.point[1], 0.25)
    && near(inside.point[2], 0) && near(inside.b[0] + inside.b[1] + inside.b[2], 1));
  const corner = tri([-1, -1, 0], A, B, C);
  ok("beyond a corner, the corner", near(corner.point[0], 0) && near(corner.point[1], 0) && corner.b[0] === 1);
  const edge = tri([0.5, -2, 0], A, B, C);
  ok("beyond an edge, the edge", near(edge.point[0], 0.5) && near(edge.point[1], 0) && near(edge.b[1], 0.5));
  const hyp = tri([1, 1, 0], A, B, C);
  ok("beyond the long edge, its middle", near(hyp.point[0], 0.5) && near(hyp.point[1], 0.5));
  const deg = tri([3, 0, 0], A, A, B);
  ok("a triangle with no area still answers, without NaN", deg.point.every((v) => isFinite(v)) && near(deg.point[0], 1));

  // A 20 x 20 grid of quads on the ground (800 triangles), and a point above it.
  const N = 20, verts: number[] = [], uvs: number[] = [], idx: number[] = [];
  for (let j = 0; j <= N; j++) for (let i = 0; i <= N; i++) { verts.push(i / N, 0, j / N); uvs.push(i / N, j / N); }
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const a = j * (N + 1) + i, b = a + 1, c = a + N + 1, d = c + 1;
    idx.push(a, c, b, b, c, d);
  }
  const pos = new Float32Array(verts), uv = new Float32Array(uvs);
  const grid = buildPosGrid(pos, idx, pos.length / 3);
  const hit = nearestOnGrid(grid, [0.337, 0.25, 0.611]);
  ok("the nearest surface point is found through the grid", !!hit && near(hit.point[0], 0.337, 1e-6) && near(hit.point[1], 0, 1e-6)
    && near(hit.point[2], 0.611, 1e-6) && near(hit.dist, 0.25, 1e-6), JSON.stringify(hit));
  const off = nearestOnGrid(grid, [1.5, 0, 0.5]);
  ok("from outside the mesh too", !!off && near(off.point[0], 1, 1e-6) && near(off.dist, 0.5, 1e-6));
  ok("and nothing beyond the distance asked for", nearestOnGrid(grid, [0.5, 0.3, 0.5], 0.2) === null && nearestOnGrid(grid, [0.5, 0.3, 0.5], 0.31) !== null);
  // The grid gives what a loop over every triangle gives.
  let same = 0;
  for (let k = 0; k < 50; k++) {
    const p = [Math.sin(k * 12.9898) * 0.8 + 0.5, Math.sin(k * 78.233) * 0.3, Math.sin(k * 37.719) * 0.8 + 0.5];
    const h = nearestOnGrid(grid, p)!;
    let bestD = Infinity;
    for (let t = 0; t < idx.length / 3; t++) {
      const v = (q: number) => [pos[idx[t * 3 + q] * 3], pos[idx[t * 3 + q] * 3 + 1], pos[idx[t * 3 + q] * 3 + 2]];
      const q = closestOnTriangle(p, v(0), v(1), v(2));
      bestD = Math.min(bestD, Math.hypot(q.point[0] - p[0], q.point[1] - p[1], q.point[2] - p[2]));
    }
    if (near(h.dist, bestD, 1e-9)) same++;
  }
  ok("the grid finds the same nearest point as a loop over every triangle", same === 50, same + "/50");

  const ug = buildUvGrid(uv, idx, pos.length / 3);
  const at = uvLocate(ug, 0.337, 0.611);
  const back = at.length ? lerpTri(pos, 3, idx, at[0].tri, at[0].b) : [];
  ok("a texture point finds its triangle, and the surface point under it", at.length >= 1 && at[0].d === 0
    && near(back[0], 0.337, 1e-6) && near(back[2], 0.611, 1e-6), JSON.stringify(at[0]));
  ok("a point on an edge between two triangles finds both", uvLocate(ug, 0.5, 0.5).length >= 2);
  ok("a point outside the texture triangles finds none", uvLocate(ug, 1.5, 0.5).length === 0);
  // The texture transform is part of texture space.
  const xf = [2, 0, 0, 0, 2, 0, 0.1, 0.2, 1];   // scale 2, then offset (0.1, 0.2)
  const ugx = buildUvGrid(uv, idx, pos.length / 3, xf);
  const atx = uvLocate(ugx, 0.1 + 2 * 0.337, 0.2 + 2 * 0.611);
  const bx = atx.length ? lerpTri(pos, 3, idx, atx[0].tri, atx[0].b) : [];
  ok("through the texture's own transform", atx.length >= 1 && near(bx[0], 0.337, 1e-6) && near(bx[2], 0.611, 1e-6));
  // The same bit of surface after a slider made the mesh 1.2 times as tall: found by its texture point.
  const tall = new Float32Array(pos.length);
  for (let i = 0; i < pos.length; i += 3) { tall[i] = pos[i]; tall[i + 1] = pos[i + 1]; tall[i + 2] = pos[i + 2] * 1.2; }
  const t2 = lerpTri(tall, 3, idx, at[0].tri, at[0].b);
  ok("a texture point names the same bit of surface after the shape changed", near(t2[2], 0.611 * 1.2, 1e-6) && near(t2[0], 0.337, 1e-6));
}

// ------------------------------------------------------------------ islands
{
  // Two quads that share no corner: two islands. Indexed, so the index decides.
  const pos = new Float32Array(8 * 3);
  const uv = new Float32Array(8 * 2);
  const idx = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7];
  const isl = triangleIslands(idx, uv, pos, 8);
  ok("two separate quads are two islands", isl[0] === isl[1] && isl[2] === isl[3] && isl[0] !== isl[2]);
  // The same two triangles with no index: corners joined by position and UV.
  const p2 = new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0]);
  const u2 = new Float32Array([0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]);
  const i2 = triangleIslands(null, u2, p2, 6);
  ok("an unindexed quad's two triangles weld into one island", i2[0] === i2[1]);
  const u3 = new Float32Array([0, 0, 1, 0, 1, 1, 0.5, 0.5, 0.9, 0.9, 0.5, 0.9]);
  const i3 = triangleIslands(null, u3, p2, 6);
  ok("but not across a UV seam, where the same corner has two UVs", i3[0] !== i3[1]);
}

// ------------------------------------------------------------------ PNG
{
  const zlib = await import("node:zlib");
  const deflate = async (raw: Uint8Array) => new Uint8Array(zlib.deflateSync(raw));
  ok("crc32 of IEND is the one every PNG ends with", (crc32(new TextEncoder().encode("IEND")) ^ 0xffffffff) >>> 0 === 0xae426082);
  const w = 5, h = 3;
  const px = new Uint8Array(w * h * 4);
  for (let i = 0; i < px.length; i++) px[i] = (i * 37 + 11) & 0xff;
  const png = await encodePNG(px, w, h, deflate);
  ok("a PNG starts with its signature", [137, 80, 78, 71].every((v, i) => png[i] === v));
  // Read it back by hand: IHDR, then IDAT, then inflate and drop the filter bytes.
  const dv = new DataView(png.buffer);
  ok("IHDR says 5 x 3, 8-bit RGBA", dv.getUint32(16) === 5 && dv.getUint32(20) === 3 && png[24] === 8 && png[25] === 6);
  let o = 8, idat: Uint8Array | null = null, crcOk = true;
  while (o < png.length) {
    const len = dv.getUint32(o);
    const type = String.fromCharCode(png[o + 4], png[o + 5], png[o + 6], png[o + 7]);
    const want = dv.getUint32(o + 8 + len);
    if (((crc32(png, o + 4, o + 8 + len) ^ 0xffffffff) >>> 0) !== want) crcOk = false;
    if (type === "IDAT") idat = png.subarray(o + 8, o + 8 + len);
    o += 12 + len;
  }
  ok("every chunk's checksum is right", crcOk);
  const raw = new Uint8Array(zlib.inflateSync(idat!));
  let same = raw.length === (w * 4 + 1) * h;
  for (let y = 0; y < h && same; y++) {
    if (raw[y * (w * 4 + 1)] !== 0) same = false;
    for (let i = 0; i < w * 4; i++) if (raw[y * (w * 4 + 1) + 1 + i] !== px[y * w * 4 + i]) { same = false; break; }
  }
  ok("and the pixels come back byte for byte", same);
  ok("base64 round-trips", base64ToBytes(bytesToBase64(png)).join(",") === png.join(","));
  const opaque = new Uint8Array([200, 100, 50, 255, 10, 20, 30, 255]);
  ok("premultiply leaves opaque pixels alone, and so does its inverse",
    premultiply(opaque).join(",") === opaque.join(",") && unpremultiply(opaque).join(",") === opaque.join(","));
  const halfA = premultiply(new Uint8Array([200, 100, 50, 128]));
  ok("half alpha halves the colour", halfA[0] === 100 && halfA[3] === 128);
  ok("and a clear pixel unpremultiplies to clear", unpremultiply(new Uint8Array([0, 0, 0, 0])).every((v) => v === 0));
}

// ------------------------------------------------------------------ the asset's layer stack
{
  const withStack = parsePaintDoc(JSON.stringify({
    version: 1, layers: [{ id: "L1", name: "Base", opacity: 1 }, { id: "L2", name: "Scars", blend: "multiply", visible: false }, { id: "L2", name: "dup" }],
    targets: [{ key: "t", size: [8, 8], layers: [{ id: "L1", png: "" }] }],
  }))!;
  ok("the asset's layer stack reads back, bottom first, without duplicates",
    withStack.layers!.length === 2 && withStack.layers![1].name === "Scars" && withStack.layers![1].blend === "multiply" && !withStack.layers![1].visible);
  const noStack = parsePaintDoc(JSON.stringify({ version: 1, targets: [{ key: "t", size: [8, 8], layers: [{ id: "A", name: "Only", png: "" }] }] }))!;
  ok("an older file with no stack takes it from its first texture", noStack.layers!.length === 1 && noStack.layers![0].name === "Only");
}

// ------------------------------------------------------------------ determinism
{
  const a = mulberry32(5), b = mulberry32(5);
  let same = true;
  for (let i = 0; i < 100; i++) if (a() !== b()) same = false;
  ok("the generator repeats itself for one seed", same);
  const c = mulberry32(0);
  ok("and a zero seed still makes numbers in 0..1", (() => { for (let i = 0; i < 100; i++) { const v = c(); if (!(v >= 0 && v < 1)) return false; } return true; })());
}

console.log("\n  " + pass + " passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
