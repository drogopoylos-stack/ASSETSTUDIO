// The operations library, tested on real geometry rather than on a toy array.
//
// Every check here stands for a fault that renders perfectly and is still wrong: a seam that is
// not welded, a mirrored half that is inside out, a subdivision that opens a hole in a closed
// solid. None of those show up in a picture, which is why they are worth a test each.
//
// Run: npm run test:ops

import {
  arrayArrays, checkArrays, deformArrays, displaceArrays, fbm, flipArrays, mirrorArrays, noise3,
  rng, scatterArrays, simplifyArrays, smoothCornerNormals, smoothNormalsArrays, solidifyArrays,
  subdivideArrays, uvArrays, vertexNormals, weldArrays, type V3,
} from "../src/components/engine/edit/ops";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
};
const near = (name: string, got: number, want: number, tol = 1e-4) =>
  ok(name, Math.abs(got - want) <= tol, "got " + got + ", want " + want);
const eq = (name: string, got: unknown, want: unknown) =>
  ok(name, JSON.stringify(got) === JSON.stringify(want), "got " + JSON.stringify(got) + ", want " + JSON.stringify(want));

// ------------------------------------------------------------------ fixtures
/** A unit cube as six separate quads: 24 vertices, 12 triangles, every seam open.
 *  This is exactly the shape a procedural asset built from primitives has. */
function cube(size = 1): { pos: Float32Array; idx: Uint32Array } {
  const h = size / 2;
  const P: number[] = [];
  const I: number[] = [];
  // (normal, u, v) with u x v = normal, so every quad is wound counter-clockwise from outside.
  const faces: Array<[V3, V3, V3]> = [
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    [[-1, 0, 0], [0, 0, 1], [0, 1, 0]],
    [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
    [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
    [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
  ];
  for (const [n, u, v] of faces) {
    const c: V3 = [n[0] * h, n[1] * h, n[2] * h];
    const corner = (su: number, sv: number): V3 => [
      c[0] + u[0] * su * h + v[0] * sv * h,
      c[1] + u[1] * su * h + v[1] * sv * h,
      c[2] + u[2] * su * h + v[2] * sv * h,
    ];
    const base = P.length / 3;
    for (const [su, sv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) P.push(...corner(su, sv));
    I.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

/** A flat grid in XZ: an OPEN surface, for the boundary and solidify checks. */
function plane(n = 4, size = 2): { pos: Float32Array; idx: Uint32Array } {
  const P: number[] = [];
  const I: number[] = [];
  for (let i = 0; i <= n; i++) {
    for (let j = 0; j <= n; j++) P.push((i / n - 0.5) * size, 0, (j / n - 0.5) * size);
  }
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const a = i * (n + 1) + j;
      I.push(a, a + 1, a + n + 1, a + 1, a + n + 2, a + n + 1);
    }
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

// ------------------------------------------------------------------ weld
{
  const c = cube();
  eq("a cube of loose quads has 24 vertices", c.pos.length / 3, 24);
  const before = checkArrays(c.pos, c.idx);
  eq("and every edge is a border", before.boundaryEdges, 24);
  ok("so it is not a closed solid", before.notes.some((s) => /open edge/.test(s)));

  const w = weldArrays(c.pos, c.idx, 1e-4);
  eq("welding leaves 8 vertices", w.pos.length / 3, 8);
  eq("and merged 16", w.merged, 16);
  eq("with all 12 triangles kept", w.idx.length / 3, 12);
  const after = checkArrays(w.pos, w.idx);
  eq("now closed", after.boundaryEdges, 0);
  eq("and manifold", after.nonManifoldEdges, 0);
  near("with the volume of a unit cube", after.volume, 1, 1e-5);
  ok("and nothing to report", after.notes[0].startsWith("Nothing wrong") || after.notes.every((s) => !/INSIDE OUT|degenerate|not welded/.test(s)), after.notes.join(" | "));
}
{
  // The failure a naive hash makes: two vertices a hair apart across a cell boundary.
  const pos = Float32Array.from([0, 0, 0, 1, 0, 0, 0, 1, 0, 0.99999, 0, 0, 0, 1, 0, 1, 1, 0]);
  const idx = Uint32Array.from([0, 1, 2, 3, 4, 5]);
  const w = weldArrays(pos, idx, 1e-3);
  ok("a pair straddling a cell boundary still welds", w.merged >= 1, "merged " + w.merged);
}
{
  const pos = Float32Array.from([0, 0, 0, 0, 0, 0, 0, 0, 0]);
  const w = weldArrays(pos, Uint32Array.from([0, 1, 2]), 1e-4);
  eq("a collapsed triangle is dropped", w.idx.length, 0);
}

// ------------------------------------------------------------------ normals
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const hard = smoothCornerNormals(w.pos, w.idx, 40);
  // At 40 degrees the 90-degree corners of a cube stay hard, so each corner keeps its face normal.
  let axisAligned = 0;
  for (let i = 0; i < hard.normal.length; i += 3) {
    const a = [Math.abs(hard.normal[i]), Math.abs(hard.normal[i + 1]), Math.abs(hard.normal[i + 2])];
    if (Math.max(...a) > 0.999) axisAligned++;
  }
  eq("a cube stays faceted at 40 degrees", axisAligned, hard.normal.length / 3);

  const soft = smoothCornerNormals(w.pos, w.idx, 120);
  let rounded = 0;
  for (let i = 0; i < soft.normal.length; i += 3) {
    const a = [Math.abs(soft.normal[i]), Math.abs(soft.normal[i + 1]), Math.abs(soft.normal[i + 2])];
    if (Math.max(...a) < 0.99) rounded++;
  }
  ok("and goes round at 120", rounded === soft.normal.length / 3, rounded + " of " + soft.normal.length / 3);
  eq("corner normals are one per corner", soft.pos.length / 3, w.idx.length);
  for (let i = 0; i < soft.normal.length; i += 3) {
    const l = Math.hypot(soft.normal[i], soft.normal[i + 1], soft.normal[i + 2]);
    if (Math.abs(l - 1) > 1e-3) { ok("every normal is unit length", false, "len " + l); break; }
  }
  pass++;
}
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const n = smoothNormalsArrays(w.pos, w.idx, 40);
  eq("indexed smoothing writes one normal per vertex", n.length / 3, w.pos.length / 3);
}

// ------------------------------------------------------------------ subdivision
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const s1 = subdivideArrays(w.pos, w.idx, 1);
  eq("one level makes four triangles from each", s1.idx.length / 3, 48);
  const chk = checkArrays(s1.pos, s1.idx);
  eq("a closed solid stays closed", chk.boundaryEdges, 0);
  eq("and manifold", chk.nonManifoldEdges, 0);
  ok("and the right way out", !chk.inverted, "volume " + chk.volume);
  ok("it shrinks towards the limit surface, as Catmull-Clark and Loop both do",
    chk.volume < 1 && chk.volume > 0.2, "volume " + chk.volume);

  const s2 = subdivideArrays(w.pos, w.idx, 2);
  eq("two levels, sixteen times", s2.idx.length / 3, 192);
  eq("still closed", checkArrays(s2.pos, s2.idx).boundaryEdges, 0);
}
{
  // The failure that makes naive subdivision useless: a border that creeps inwards.
  const p = plane(2, 2);
  const before = checkArrays(p.pos, p.idx);
  const s = subdivideArrays(p.pos, p.idx, 1);
  const after = checkArrays(s.pos, s.idx);
  ok("an open surface keeps its border", after.boundaryEdges === before.boundaryEdges * 2,
    before.boundaryEdges + " -> " + after.boundaryEdges);
  near("and keeps its extent", after.bounds.size[0], 2, 1e-6);
  near("in both directions", after.bounds.size[2], 2, 1e-6);
}

// ------------------------------------------------------------------ mirror
{
  // Half a cube: the +X three faces only, welded. Mirroring should close it into a whole one.
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const m = mirrorArrays(w.pos, w.idx, 0, true, 1e-4);
  const chk = checkArrays(m.pos, m.idx);
  ok("a mirrored solid is not inside out", !chk.inverted, "volume " + chk.volume);
  ok("and its volume is positive", chk.volume > 0, String(chk.volume));
}
{
  // THE test that matters. A cube sitting entirely on one side of the plane, mirrored, must come
  // out as two solids of the same handedness: volume 2. A reflection reverses handedness, so
  // without the winding flip the reflected half has volume -1, the two cancel, and the total is
  // zero — while the picture looks completely normal from the outside.
  const c = cube();
  const off = new Float32Array(c.pos);
  for (let i = 0; i < off.length; i += 3) off[i] += 1;          // now x is 0.5 .. 1.5
  const w = weldArrays(off, c.idx, 1e-4);
  near("the offset cube alone", checkArrays(w.pos, w.idx).volume, 1, 1e-5);

  const m = mirrorArrays(w.pos, w.idx, 0, true, 1e-4);
  const chk = checkArrays(m.pos, m.idx);
  near("mirrored, it is two solids of the same handedness", chk.volume, 2, 1e-4);
  eq("both closed", chk.boundaryEdges, 0);
  near("spanning both sides", chk.bounds.size[0], 3, 1e-5);

  // And prove the assertion has teeth: skip the flip and the volumes really do cancel.
  const naive = mirrorArrays(w.pos, w.idx, 0, false);
  const bad = new Uint32Array(naive.idx);
  for (let t = naive.idx.length / 2; t + 2 < bad.length; t += 3) {
    const s = bad[t + 1]; bad[t + 1] = bad[t + 2]; bad[t + 2] = s;   // undo the flip
  }
  near("without the flip the halves cancel", checkArrays(naive.pos, bad).volume, 0, 1e-5);
}
{
  const c = cube();
  const m = mirrorArrays(c.pos, c.idx, 0, false);
  eq("without welding it is two copies", m.pos.length / 3, 48);
  eq("and twice the triangles", m.idx.length / 3, 24);
}

// ------------------------------------------------------------------ array
{
  const c = cube();
  const a = arrayArrays(c.pos, c.idx, 3, [1, 0, 0], false);
  eq("three copies", a.pos.length / 3, 72);
  eq("with three times the triangles", a.idx.length / 3, 36);
  const chk = checkArrays(a.pos, a.idx);
  near("spanning three cubes", chk.bounds.size[0], 3, 1e-5);
  eq("one copy is the original", arrayArrays(c.pos, c.idx, 1, [1, 0, 0]).pos.length, c.pos.length);
}

// ------------------------------------------------------------------ deform
{
  const c = cube();
  const t = deformArrays(c.pos, "taper", 0.5, 1);
  let topMax = 0, botMax = 0;
  for (let i = 0; i < t.length / 3; i++) {
    const y = c.pos[i * 3 + 1];
    const r = Math.max(Math.abs(t[i * 3]), Math.abs(t[i * 3 + 2]));
    if (y > 0) topMax = Math.max(topMax, r); else botMax = Math.max(botMax, r);
  }
  near("the top of a taper is halved", topMax, 0.25, 1e-5);
  near("and the bottom is untouched", botMax, 0.5, 1e-5);
}
{
  const c = cube();
  const t = deformArrays(c.pos, "twist", Math.PI / 2, 1);
  eq("twist keeps the vertex count", t.length, c.pos.length);
  let moved = 0;
  for (let i = 0; i < t.length; i++) if (Math.abs(t[i] - c.pos[i]) > 1e-6) moved++;
  ok("and actually moves the top", moved > 0, String(moved));
}
{
  const flat = deformArrays(Float32Array.from([0, 0, 0, 1, 0, 0]), "taper", 0.5, 1);
  eq("a mesh with no extent along the axis is left alone", Array.from(flat), [0, 0, 0, 1, 0, 0]);
}

// ------------------------------------------------------------------ displace
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const n = vertexNormals(w.pos, w.idx);
  const d = displaceArrays(w.pos, n, 0.1, 3, 3, 7);
  let maxMove = 0;
  for (let i = 0; i < d.length; i++) maxMove = Math.max(maxMove, Math.abs(d[i] - w.pos[i]));
  ok("displacement stays within the amplitude", maxMove <= 0.1 + 1e-6, String(maxMove));
  ok("and does something", maxMove > 1e-4, String(maxMove));
  const again = displaceArrays(w.pos, n, 0.1, 3, 3, 7);
  eq("the same seed gives the same result", Array.from(again), Array.from(d));
  const other = displaceArrays(w.pos, n, 0.1, 3, 3, 8);
  ok("a different seed does not", JSON.stringify(Array.from(other)) !== JSON.stringify(Array.from(d)));
}

// ------------------------------------------------------------------ solidify
{
  const p = plane(2, 2);
  const n = vertexNormals(p.pos, p.idx);
  const s = solidifyArrays(p.pos, p.idx, n, 0.1);
  const chk = checkArrays(s.pos, s.idx);
  eq("a thickened surface is closed", chk.boundaryEdges, 0);
  ok("and has volume", Math.abs(chk.volume) > 1e-6, String(chk.volume));
  near("as thick as asked", chk.bounds.size[1], 0.1, 1e-5);
}

// ------------------------------------------------------------------ scatter
{
  const p = plane(4, 2);
  const pts = scatterArrays(p.pos, p.idx, 500, 3);
  eq("as many points as asked", pts.length, 500);
  let inside = 0;
  for (const q of pts) {
    if (Math.abs(q.p[1]) < 1e-6 && Math.abs(q.p[0]) <= 1.0001 && Math.abs(q.p[2]) <= 1.0001) inside++;
  }
  eq("every point is on the surface", inside, 500);
  // Area-weighted means an even spread over an even grid: each quadrant gets about a quarter.
  const quad = [0, 0, 0, 0];
  for (const q of pts) quad[(q.p[0] > 0 ? 1 : 0) + (q.p[2] > 0 ? 2 : 0)]++;
  ok("and the spread is even", quad.every((c) => c > 500 * 0.15), quad.join(","));
  eq("the same seed gives the same points", scatterArrays(p.pos, p.idx, 5, 3).map((q) => q.p),
    pts.slice(0, 5).map((q) => q.p));
  eq("no triangles, no points", scatterArrays(new Float32Array(0), new Uint32Array(0), 10).length, 0);
}

// ------------------------------------------------------------------ uv
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  for (const mode of ["box", "cylinder", "sphere"] as const) {
    const uv = uvArrays(w.pos, w.idx, mode, 1);
    eq("two numbers per vertex (" + mode + ")", uv.length / 2, w.pos.length / 3);
    let inRange = true;
    for (const v of uv) if (!(v >= -0.001 && v <= 1.001)) inRange = false;
    ok("and they land in 0..1 (" + mode + ")", inRange);
  }
}

// ------------------------------------------------------------------ simplify
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const s3 = subdivideArrays(w.pos, w.idx, 2);
  const s = simplifyArrays(s3.pos, s3.idx, 0.4);
  ok("simplifying reduces the triangles", s.idx.length < s3.idx.length, s3.idx.length + " -> " + s.idx.length);
  ok("and reports how far", s.ratio < 1 && s.ratio > 0, String(s.ratio));
  eq("a cell of zero is a no-op", simplifyArrays(s3.pos, s3.idx, 0).idx.length, s3.idx.length);
}

// ------------------------------------------------------------------ the report
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const flipped = flipArrays(w.idx);
  const chk = checkArrays(w.pos, flipped);
  ok("an inside-out solid is caught", chk.inverted, "volume " + chk.volume);
  ok("and named first", /INSIDE OUT/.test(chk.notes[0]), chk.notes[0]);
  near("its volume is the negative of the right one", chk.volume, -1, 1e-5);
  const back = checkArrays(w.pos, flipArrays(flipped));
  ok("flipping twice is the original", !back.inverted);
  near("with the volume back", back.volume, 1, 1e-5);
}
{
  const c = cube();
  const chk = checkArrays(c.pos, c.idx);
  ok("an unwelded mesh is called out", chk.notes.some((s) => /not welded/.test(s)), chk.notes.join(" | "));
  eq("with the duplicate count", chk.duplicates, 16);
}
{
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const chk = checkArrays(w.pos, w.idx, vertexNormals(w.pos, w.idx), uvArrays(w.pos, w.idx));
  ok("a clean mesh is reported clean", chk.notes.length === 1 && /Nothing wrong/.test(chk.notes[0]), chk.notes.join(" | "));
  ok("with normals", chk.hasNormals);
  ok("and uvs", chk.hasUVs);
}
{
  // THE distinction. Loose pieces and a real hole both show open edges as authored, and they
  // need opposite fixes: one is "weld it", the other is "you are missing geometry". Reporting
  // them as the same number is how an agent ends up welding a mesh that has a hole in it.
  const c = cube();
  const loose = checkArrays(c.pos, c.idx);
  eq("a pile of loose quads shows open edges", loose.boundaryEdges, 24);
  eq("but none of them survive a weld", loose.holes, 0);
  ok("so it is called unwelded, not holed", loose.notes.some((s) => /not welded/.test(s)), loose.notes.join(" | "));
  ok("and not called a hole", !loose.notes.some((s) => /real hole/.test(s)));
  // The volume comes from the WELDED form, so a loose-quad cube still measures as a cube.
  near("with the true volume", loose.volume, 1, 1e-5);

  // Now take a face away: that IS a hole, and welding cannot close it.
  const holed = Uint32Array.from(Array.from(c.idx).slice(0, c.idx.length - 6));
  const chk = checkArrays(c.pos, holed);
  ok("a missing face survives the weld", chk.holes > 0, String(chk.holes));
  ok("and is called a real hole", chk.notes.some((s) => /real hole/.test(s)), chk.notes.join(" | "));
}
{
  // A mesh with per-corner normals shares no vertices at all. Judged as authored it looks like
  // ten thousand holes; judged after welding it is exactly what it is.
  const c = cube();
  const w = weldArrays(c.pos, c.idx, 1e-4);
  const s = smoothCornerNormals(w.pos, w.idx, 120);
  const idx = new Uint32Array(s.pos.length / 3);
  for (let i = 0; i < idx.length; i++) idx[i] = i;
  const chk = checkArrays(s.pos, idx, s.normal);
  eq("every edge is a border as authored", chk.boundaryEdges, 36);
  eq("and not one is a hole", chk.holes, 0);
  ok("so smooth shading is not reported as damage", !chk.notes.some((s2) => /real hole/.test(s2)), chk.notes.join(" | "));
  near("and the volume is still right", chk.volume, 1, 1e-5);
}
{
  const empty = checkArrays(new Float32Array(0), new Uint32Array(0));
  eq("an empty mesh does not crash", empty.triangles, 0);
  eq("and has a zero box", empty.bounds.size, [0, 0, 0]);
}
{
  // A non-manifold fin: three triangles sharing one edge.
  const pos = Float32Array.from([0, 0, 0, 1, 0, 0, 0.5, 1, 0, 0.5, -1, 0, 0.5, 0, 1]);
  const idx = Uint32Array.from([0, 1, 2, 0, 3, 1, 0, 1, 4]);
  const chk = checkArrays(pos, idx);
  ok("a shared edge used three times is caught", chk.nonManifoldEdges >= 1, String(chk.nonManifoldEdges));
  ok("and named", chk.notes.some((s) => /three or more/.test(s)), chk.notes.join(" | "));
}

// ------------------------------------------------------------------ noise
{
  const a = rng(42);
  const b = rng(42);
  ok("the same seed gives the same stream", a() === b() && a() === b());
  const r = rng(1);
  let lo = 1, hi = 0;
  for (let i = 0; i < 5000; i++) { const v = r(); lo = Math.min(lo, v); hi = Math.max(hi, v); }
  ok("and stays inside 0..1", lo >= 0 && hi < 1, lo + ".." + hi);
  ok("covering the range", lo < 0.01 && hi > 0.99, lo + ".." + hi);
}
{
  eq("noise is deterministic", noise3(1.5, 2.5, 3.5, 9), noise3(1.5, 2.5, 3.5, 9));
  ok("and seeded", noise3(1.5, 2.5, 3.5, 9) !== noise3(1.5, 2.5, 3.5, 10));
  let lo = 1, hi = 0;
  for (let i = 0; i < 2000; i++) {
    const v = noise3(i * 0.37, i * 0.11, i * 0.53, 5);
    lo = Math.min(lo, v); hi = Math.max(hi, v);
  }
  ok("noise stays inside 0..1", lo >= 0 && hi <= 1, lo + ".." + hi);
  // Smooth, not white: two nearby samples must be close, or displacement looks like static.
  const d = Math.abs(noise3(4.0, 2.0, 1.0, 5) - noise3(4.001, 2.0, 1.0, 5));
  ok("and is smooth", d < 0.02, String(d));
  let flo = 1, fhi = 0;
  for (let i = 0; i < 500; i++) { const v = fbm(i * 0.3, 0, 0, 4, 0.5, 2, 3); flo = Math.min(flo, v); fhi = Math.max(fhi, v); }
  ok("fbm stays inside 0..1", flo >= 0 && fhi <= 1, flo + ".." + fhi);
}

// ---- by name: the keys, the applier, the scene report --------------------------------------
//
// These need real objects, so three runs headless here: the same engine the editor drives, with
// no browser. What is checked is the promise the whole editor rests on — a key written in the
// editor is the key a game finds, and a sidecar applied by the game does what the editor showed.
import { applyEdits, makeOps, sceneReport, stableKeys } from "../src/components/engine/edit/ops";
// A three.js build, from wherever this machine has one. `data/` is NOT in the repository - it
// holds settings, keys and copies of other people's projects - so a fresh checkout has only the
// second candidate, which npm writes the moment `npm install` runs. Without this, `npm test`
// failed on a clean clone with ERR_MODULE_NOT_FOUND and told nobody why.
const { existsSync } = await import("node:fs");
const { pathToFileURL } = await import("node:url");
const threeAt = (): string => {
  const here = new URL(".", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
  for (const c of ["../enginetest/three.module.js",
                   "../../frontend/node_modules/three/build/three.module.js",
                   "../../node_modules/three/build/three.module.js"]) {
    const abs = new URL(c, import.meta.url);
    const file = decodeURIComponent(abs.pathname).replace(/^\/([A-Za-z]:)/, "$1");
    if (existsSync(file)) return pathToFileURL(file).href;
  }
  throw new Error("no three.js build found near " + here
    + " - run `npm install` in frontend/, which puts one in node_modules");
};
const THREE: any = await import(threeAt());
{
  const T = THREE;
  const mk = (name: string) => { const m = new T.Mesh(new T.BoxGeometry(1, 1, 1), new T.MeshStandardMaterial()); m.name = name; return m; };
  const root = new T.Group();
  const a = mk("leg"), b = mk("leg"), c = mk("body"), d = mk("");
  const g = new T.Group(); g.name = "arm"; g.add(d);
  root.add(a, b, c, g);
  const keys = stableKeys(root);
  eq("a unique name is its own key", keys.get(c), "body");
  eq("repeated names get Blender's suffixes, in order", [keys.get(a), keys.get(b)], ["leg.000", "leg.001"]);
  eq("an unnamed object is keyed by its path", keys.get(d), "/3/0");
  eq("a group is keyed like anything else", keys.get(g), "arm");

  const sun = new T.DirectionalLight(0xffffff, 1); sun.name = "sun";
  const cam = new T.PerspectiveCamera(50, 1, 0.1, 100); cam.name = "cam";
  root.add(sun, cam);
  const r = applyEdits(root, {
    parts: {
      body: { pos: [1, 2, 3], hidden: true, color: "#ff0000", roughness: 0.2 },
      "leg.001": { scale: [2, 2, 2] },
      sun: { intensity: 4, color: "#00ff00", shadow: true },
      cam: { fov: 30, near: 0.5 },
      ghost: { pos: [0, 0, 0] },
    },
    mods: [],
  });
  eq("four parts applied, the missing one named", [r.parts, r.missing], [4, ["ghost"]]);
  eq("position by key", [c.position.x, c.position.y, c.position.z], [1, 2, 3]);
  ok("hidden", c.visible === false);
  eq("a mesh's colour goes to its material", c.material.color.getHexString(), "ff0000");
  near("and so does roughness", c.material.roughness, 0.2);
  eq("the second leg scaled, the first not", [b.scale.x, a.scale.x], [2, 1]);
  eq("a light's colour, intensity and shadow", [sun.color.getHexString(), sun.intensity, sun.castShadow], ["00ff00", 4, true]);
  eq("a camera's fov and near", [cam.fov, cam.near], [30, 0.5]);
  ok("and its projection was rebuilt", Math.abs(cam.projectionMatrix.elements[5] - 1 / Math.tan((30 * Math.PI) / 360)) < 1e-6);

  const ops = makeOps(T);
  // A whole-asset modifier runs on every part in place, so names, materials and bakes survive.
  const r2 = applyEdits(root, { parts: {}, mods: [{ op: "weld", target: "", args: { tol: 1e-4 } }] }, ops);
  eq("one modifier applied, no errors", [r2.mods, r2.errors], [1, []]);
  const kinds = root.children.map((o: any) => (o.isMesh ? "mesh" : o.isLight ? "light" : o.isCamera ? "camera" : o.type)).sort();
  eq("a whole-asset weld keeps every part, the light and the camera", kinds, ["Group", "camera", "light", "mesh", "mesh", "mesh"]);
  ok("and the parts keep their names", !!root.getObjectByName("body") && !!root.getObjectByName("arm"));
  // Only the union operations merge: skin (and remesh) turn the parts into one mesh.
  const r3s = applyEdits(root, { parts: {}, mods: [{ op: "skin", target: "", args: { tol: 1e-4 } }] }, ops);
  eq("skin applied", [r3s.mods, r3s.errors], [1, []]);
  const kinds2 = root.children.map((o: any) => (o.isMesh ? "mesh" : o.isLight ? "light" : o.isCamera ? "camera" : o.type)).sort();
  eq("a whole-asset skin leaves one mesh and keeps the light and the camera", kinds2, ["camera", "light", "mesh"]);
  ok("the merged mesh is named so it can be keyed", root.children.some((o: any) => o.name === "merged"));
  const r3 = applyEdits(root, { parts: {}, mods: [{ op: "weld", target: "", args: {} }] });
  ok("a stack without the ops is refused, not ignored", r3.errors.length === 1 && /makeOps/.test(r3.errors[0]), r3.errors.join("|"));
}
{
  const T = THREE;
  const std = () => new T.MeshStandardMaterial();
  const scene = new T.Group();
  const floor = new T.Mesh(new T.PlaneGeometry(10, 10), std()); floor.name = "floor"; floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true;
  const box = new T.Mesh(new T.BoxGeometry(1, 1, 1), std()); box.name = "crate"; box.position.y = 0.5; box.castShadow = true;
  const sunk = new T.Mesh(new T.BoxGeometry(1, 1, 1), std()); sunk.name = "crate"; sunk.position.y = -0.3;
  const high = new T.Mesh(new T.SphereGeometry(0.3, 8, 6), std()); high.name = "balloon"; high.position.y = 3;
  const sun = new T.DirectionalLight(0xffffff, 2); sun.name = "sun"; sun.castShadow = true;
  const cam = new T.PerspectiveCamera(); cam.name = "cam";
  scene.add(floor, box, sunk, high, sun, cam);
  const rep = sceneReport(scene);
  eq("meshes and cameras counted", [rep.meshes, rep.cameras], [4, 1]);
  eq("lights by type", rep.lights, { directional: 1 });
  eq("shadows: one caster, one receiver, one light", rep.shadows, { casters: 1, receivers: 1, lightsCasting: 1 });
  eq("the duplicate name is reported", rep.duplicates, ["crate"]);
  eq("what is below the floor", rep.belowFloor, ["crate"]);
  eq("what floats", rep.floating, ["balloon"]);
  eq("the ground plane is paper-thin, and is named", rep.paperThin, ["floor"]);
  ok("a note names the duplicate", rep.notes.some((n) => /crate/.test(n) && /twice/.test(n)), rep.notes.join(" | "));
  const bare = sceneReport(new T.Group().add(box.clone()));
  ok("no light and no camera are said plainly", /No lights/.test(bare.notes.join(" ")) && /No camera/.test(bare.notes.join(" ")), bare.notes.join(" | "));
  ok("a report on nothing does not throw", sceneReport(null).notes.length === 0);
}

// ---- hull, boolean, isosurface, inverse kinematics ------------------------------------------
//
// Four more operations, each checked on the number that would be wrong if the algorithm were: a
// hull that leaves a point outside, a boolean whose volume is not the arithmetic, a skin that is
// not closed, a chain whose segments changed length.
import { blobBounds, blobField, csgArrays, fabrik, hullArrays, isosurfaceArrays } from "../src/components/engine/edit/ops";
{
  let seed = 12345;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
  const pts: number[] = [];
  for (let i = 0; i < 8; i++) pts.push(i & 1 ? 1 : -1, i & 2 ? 1 : -1, i & 4 ? 1 : -1);
  for (let i = 0; i < 300; i++) pts.push((rnd() * 2 - 1) * 0.9, (rnd() * 2 - 1) * 0.9, (rnd() * 2 - 1) * 0.9);
  const h = hullArrays(Float32Array.from(pts));
  ok("a hull comes back", !!h);
  if (h) {
    eq("a cube with a cloud inside hulls to twelve triangles on eight corners", [h.idx.length / 3, h.pos.length / 3], [12, 8]);
    const c = checkArrays(h.pos, h.idx);
    near("its volume is the cube's", c.volume, 8, 1e-6);
    ok("outward and closed", !c.inverted && c.holes === 0, JSON.stringify([c.inverted, c.holes]));
  }
  const cloud: number[] = [];
  for (let i = 0; i < 500; i++) {
    const a = rnd() * 6.2832, b = Math.acos(rnd() * 2 - 1), r = Math.cbrt(rnd());
    cloud.push(r * Math.sin(b) * Math.cos(a), r * Math.sin(b) * Math.sin(a), r * Math.cos(b));
  }
  const h2 = hullArrays(Float32Array.from(cloud))!;
  const c2 = checkArrays(h2.pos, h2.idx);
  ok("a cloud's hull is closed and outward", c2.holes === 0 && !c2.inverted, JSON.stringify([c2.holes, c2.volume]));
  let worst = -Infinity;
  for (let t = 0; t < h2.idx.length; t += 3) {
    const A = h2.idx[t] * 3, B = h2.idx[t + 1] * 3, C = h2.idx[t + 2] * 3;
    const ux = h2.pos[B] - h2.pos[A], uy = h2.pos[B + 1] - h2.pos[A + 1], uz = h2.pos[B + 2] - h2.pos[A + 2];
    const vx = h2.pos[C] - h2.pos[A], vy = h2.pos[C + 1] - h2.pos[A + 1], vz = h2.pos[C + 2] - h2.pos[A + 2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
    const w = nx * h2.pos[A] + ny * h2.pos[A + 1] + nz * h2.pos[A + 2];
    for (let i = 0; i < cloud.length; i += 3) worst = Math.max(worst, nx * cloud[i] + ny * cloud[i + 1] + nz * cloud[i + 2] - w);
  }
  ok("no point of the cloud lies outside its hull", worst < 1e-5, String(worst));
  eq("a flat cloud has no hull, and says so", hullArrays(Float32Array.from([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0, 0.5, 0.5, 0])), null);
}
{
  const shift = (g: { pos: Float32Array; idx: Uint32Array }, dx: number) =>
    ({ pos: g.pos.map((v, i) => (i % 3 === 0 ? v + dx : v)), idx: g.idx });
  const A = cube(1), B = shift(cube(1), 0.5);
  const vol = (op: "union" | "subtract" | "intersect") => { const r = csgArrays(A, B, op); return checkArrays(r.pos, r.idx); };
  const u = vol("union"), s = vol("subtract"), x = vol("intersect");
  near("union of two half-overlapping unit cubes is 1.5", u.volume, 1.5, 2e-3);
  near("subtraction leaves 0.5", s.volume, 0.5, 2e-3);
  near("intersection is 0.5", x.volume, 0.5, 2e-3);
  ok("all three come out facing outward", !u.inverted && !s.inverted && !x.inverted);
  ok("the pieces have real triangles, not a pile of shards", u.triangles < 400 && x.triangles < 200, JSON.stringify([u.triangles, x.triangles]));
}
{
  const sphere = (x: number, y: number, z: number) => x * x + y * y + z * z - 1;
  const r = isosurfaceArrays(sphere, [-1.5, -1.5, -1.5], [1.5, 1.5, 1.5], 40, 0);
  const c = checkArrays(r.pos, r.idx);
  near("a unit sphere's skin holds a unit sphere's volume, within two percent", c.volume, 4.18879, 0.09);
  eq("closed as built: every quad edge is shared", c.boundaryEdges, 0);
  eq("and manifold", c.nonManifoldEdges, 0);
  ok("outward", !c.inverted);
  const balls = [{ p: [0, 0, 0] as V3, r: 1 }, { p: [1.2, 0, 0] as V3, r: 0.8 }];
  const box = blobBounds(balls, 1.5, 48);
  const b = isosurfaceArrays(blobField(balls), box.min, box.max, box.res, 0);
  const cb = checkArrays(b.pos, b.idx);
  ok("two blobs blend into one closed body bigger than either", cb.boundaryEdges === 0 && !cb.inverted && cb.volume > 4.18, JSON.stringify([cb.boundaryEdges, cb.volume]));
}
{
  const chain: V3[] = [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]];
  const lens = (js: V3[]) => js.slice(1).map((p, i) => Math.hypot(p[0] - js[i][0], p[1] - js[i][1], p[2] - js[i][2]));
  const a = fabrik(chain, [2, 1, 0]);
  ok("a reachable target is reached", a.reached && a.distance < 1e-3, String(a.distance));
  ok("every segment keeps its length", lens(a.joints).every((l) => Math.abs(l - 1) < 1e-6), JSON.stringify(lens(a.joints)));
  eq("the root does not move", a.joints[0], [0, 0, 0]);
  const b = fabrik(chain, [10, 0, 0]);
  ok("out of reach: not reached, and stretched straight at it", !b.reached && Math.abs(b.joints[3][0] - 3) < 1e-6 && lens(b.joints).every((l) => Math.abs(l - 1) < 1e-6));
}
{
  const T = THREE;
  const scene = new T.Scene();
  scene.background = new T.Color(0x000000);
  scene.fog = new T.Fog(0x000000, 1, 10);
  applyEdits(scene, { parts: {}, mods: [], world: { background: "#ff0000", fog: { type: "linear", color: "#00ff00", near: 2, far: 20 } } });
  eq("a world edit changes the background in place", scene.background.getHexString(), "ff0000");
  eq("and the fog in place", [scene.fog.color.getHexString(), scene.fog.near, scene.fog.far], ["00ff00", 2, 20]);
  const r2 = applyEdits(scene, { parts: {}, mods: [], world: { fog: { type: "exp2", color: "#0000ff", density: 0.1 } } });
  ok("a new kind of fog without THREE is refused, not faked", r2.errors.length === 1 && /THREE/.test(r2.errors[0]), r2.errors.join("|"));
  applyEdits(scene, { parts: {}, mods: [], world: { fog: { type: "exp2", color: "#0000ff", density: 0.1 } } }, undefined, T);
  ok("with THREE it is made", !!scene.fog?.isFogExp2 && Math.abs(scene.fog.density - 0.1) < 1e-9);
  applyEdits(scene, { parts: {}, mods: [], world: { fog: null } }, undefined, T);
  ok("null switches the fog off", scene.fog === null);
}

// ---- playcanvas: snapshot, applier, mirror ---------------------------------------------------
//
// A fake entity tree with the PlayCanvas surface the code reads — names, children, local
// transforms as {x,y,z}, meshInstances with getPositions and friends, light and camera components.
// What is checked: the keys are the same function as three's, the snapshot carries what the mirror
// needs, the mirror is a faithful three tree, and the sidecar lands on the entities.
import { hexToRgb01, pcApply, pcSnapshot, quatFromEulerXYZ, walkTree } from "../src/components/engine/edit/ops";
import { buildPcMirror } from "../src/components/engine/edit/pcmirror";
{
  const T = THREE;
  const vec = (x = 0, y = 0, z = 0) => ({ x, y, z });
  const fakeMesh = (g: { pos: Float32Array; idx: Uint32Array }) => ({
    getPositions(a: number[]) { a.length = 0; for (const v of g.pos) a.push(v); return g.pos.length / 3; },
    getNormals(a: number[]) { a.length = 0; return 0; },
    getUvs(_c: number, a: number[]) { a.length = 0; return 0; },
    getColors(a: number[]) { a.length = 0; return 0; },
    getIndices(a: number[]) { a.length = 0; for (const v of g.idx) a.push(v); return g.idx.length; },
  });
  const colorObj = (r: number, g: number, b: number) => ({ r, g, b, set(rr: number, gg: number, bb: number) { this.r = rr; this.g = gg; this.b = bb; return this; } });
  const fakeEntity = (name: string, extra: any = {}) => {
    const e: any = {
      name, enabled: true, children: [] as any[], _pos: vec(), _rot: { x: 0, y: 0, z: 0, w: 1 }, _scale: vec(1, 1, 1),
      getLocalPosition() { return this._pos; }, getLocalRotation() { return this._rot; }, getLocalScale() { return this._scale; },
      setLocalPosition(x: number, y: number, z: number) { this._pos = vec(x, y, z); },
      setLocalRotation(x: number, y: number, z: number, w: number) { this._rot = { x, y, z, w }; },
      setLocalScale(x: number, y: number, z: number) { this._scale = vec(x, y, z); },
      addChild(c: any) { this.children.push(c); c.parent = this; },
      ...extra,
    };
    return e;
  };
  const mat = { name: "skin", diffuse: colorObj(0.2, 0.6, 0.3), metalness: 0.1, useMetalness: true, gloss: 0.3, cull: 1, blendType: 3, updated: 0, update() { this.updated++; } };
  const root = fakeEntity("forge-subject");
  const body = fakeEntity("body", { render: { meshInstances: [{ mesh: fakeMesh(cube(1)), material: mat }] } });
  body._pos = vec(1, 2, 3);
  const legA = fakeEntity("leg", { render: { meshInstances: [{ mesh: fakeMesh(cube(0.5)), material: mat }] } });
  const legB = fakeEntity("leg", { render: { meshInstances: [{ mesh: fakeMesh(cube(0.5)), material: mat }] } });
  const sun = fakeEntity("sun", { light: { type: "directional", color: colorObj(1, 0.9, 0.8), intensity: 2, range: 10, innerConeAngle: 40, outerConeAngle: 45, castShadows: true, enabled: true } });
  const cam = fakeEntity("cam", { camera: { fov: 50, nearClip: 0.1, farClip: 200, projection: 0, orthoHeight: 10, clearColor: { r: 0.1, g: 0.2, b: 0.3, a: 1, set(r: number, g: number, b: number) { this.r = r; this.g = g; this.b = b; return this; } } } });
  root.addChild(body); body.addChild(legA); body.addChild(legB); root.addChild(sun); root.addChild(cam);
  root._app = { scene: { ambientLight: colorObj(0.2, 0.2, 0.25), fog: "linear", fogColor: colorObj(0.5, 0.5, 0.6), fogStart: 5, fogEnd: 50, fogDensity: 0.01 } };

  // Keys: the same function, the same answers, on a tree with no traverse().
  const keys = stableKeys(root);
  eq("entity keys match three's rule", [keys.get(body), keys.get(legA), keys.get(legB), keys.get(sun)], ["body", "leg.000", "leg.001", "sun"]);
  let n = 0; walkTree(root, () => n++);
  eq("walkTree visits every node", n, 6);

  // Snapshot.
  const snap = pcSnapshot(root);
  eq("snapshot counts", snap.counts, { entities: 6, meshes: 3, triangles: 36 });
  const sb = snap.root.children[0];
  eq("entity transform and name carried", [sb.name, sb.pos], ["body", [1, 2, 3]]);
  eq("material approximated: colour, metalness, roughness from gloss", [sb.meshes![0].material.color, sb.meshes![0].material.metalness, sb.meshes![0].material.roughness], [[0.2, 0.6, 0.3], 0.1, 0.7]);
  eq("light carried", snap.root.children[1].light?.type, "directional");
  eq("camera carried with its clear colour", [snap.root.children[2].camera?.fov, snap.world?.background], [50, [0.1, 0.2, 0.3]]);
  eq("legacy fog carried as linear", snap.world?.fog, { type: "linear", color: [0.5, 0.5, 0.6], start: 5, end: 50 });

  // Mirror.
  const mirror = buildPcMirror(T, snap, { aspect: 1.5 });
  const mBody = mirror.root.getObjectByName("body");
  ok("a one-mesh entity mirrors as a Mesh", !!mBody?.isMesh, mBody?.type);
  eq("mirror position", [mBody.position.x, mBody.position.y, mBody.position.z], [1, 2, 3]);
  eq("mirror has the same triangles", mBody.geometry.index.count / 3, 12);
  const mSun = mirror.root.getObjectByName("sun");
  ok("a light-only entity mirrors as a light with a target under it", !!mSun?.isDirectionalLight && mSun.target.parent === mSun && mSun.target.position.y === -1);
  ok("a camera-only entity mirrors as a camera", !!mirror.root.getObjectByName("cam")?.isPerspectiveCamera);
  ok("the mirror's world carries the clear colour and the fog", !!mirror.world?.background && !!mirror.world?.fog?.isFog);
  const mKeys = stableKeys(mirror.root);
  eq("the mirror keys equal the entity keys", [...mKeys.values()].filter((k) => !k.startsWith("/")).sort(), [...keys.values()].filter((k) => !k.startsWith("/")).sort().concat(["ambient"]).sort());

  // Apply: the sidecar the editor writes, onto the entities.
  const r = pcApply(root, {
    parts: {
      body: { pos: [4, 5, 6], rot: [0, Math.PI / 2, 0], color: "#ff0000", roughness: 0.2 },
      "leg.001": { hidden: true },
      sun: { intensity: 5, color: "#00ff00", shadow: false },
      cam: { fov: 30, near: 0.5 },
      ghost: { pos: [0, 0, 0] },
    },
    world: { background: "#102030", fog: { type: "exp2", color: "#ffffff", density: 0.05 } },
    mods: [],
  });
  eq("four parts applied, the ghost named", [r.parts, r.missing, r.errors], [4, ["ghost"], []]);
  eq("position landed", body._pos, vec(4, 5, 6));
  const q = quatFromEulerXYZ(0, Math.PI / 2, 0);
  ok("rotation landed as a quaternion", Math.abs(body._rot.y - q[1]) < 1e-9 && Math.abs(body._rot.w - q[3]) < 1e-9);
  ok("the second leg is disabled, the first is not", legB.enabled === false && legA.enabled === true);
  eq("material colour and gloss changed, and update() was called", [mat.diffuse.r, mat.diffuse.g, mat.gloss, mat.updated > 0], [1, 0, 0.8, true]);
  eq("light intensity, colour and shadows", [sun.light.intensity, sun.light.color.g, sun.light.castShadows], [5, 1, false]);
  eq("camera fov and near", [cam.camera.fov, cam.camera.nearClip], [30, 0.5]);
  eq("clear colour follows the world background", [cam.camera.clearColor.r, cam.camera.clearColor.g], hexToRgb01("#102030").slice(0, 2));
  eq("legacy fog switched to exp2", [root._app.scene.fog, root._app.scene.fogDensity], ["exp2", 0.05]);
  ok("a stack without the ops is refused", /makeOpsPc/.test(pcApply(root, { parts: {}, mods: [{ op: "weld", target: "", args: {} }] }).errors[0] || ""));

  // The same rotation, read back through three, is the euler that went in.
  const probe = new T.Object3D();
  probe.quaternion.set(q[0], q[1], q[2], q[3]);
  near("the XYZ euler survives the round trip", probe.rotation.y, Math.PI / 2, 1e-9);
}

// ---- modular kits: the bay rule, facades, the perimeter, the kit, instancing -----------------
//
// The rule a configurator building lives by — bays are repeated, never stretched — is arithmetic,
// and arithmetic that is one bay out renders perfectly. So it is checked on the lengths people
// type, which are exactly the ones floating point gets wrong: 4.8 / 1.6 is 2.9999999999999996, and
// a floor() with no slack builds two shop stalls where three fit.
import {
  bayLayout, facadePlan, facadeSlots, makeOpsPc, mat4Decompose, mat4Mul, perimeterSlots, placementMatrix,
  type BaySpread,
} from "../src/components/engine/edit/ops";
{
  // Each of these was measured in node before it was written here: a bare floor gets all five wrong.
  const hard: Array<[number, number, number]> = [[4.8, 1.6, 3], [9.6, 1.6, 6], [0.6, 0.2, 3], [1.4 * 3, 1.4, 3], [0.7 * 3, 0.7, 3]];
  for (const [L, b, want] of hard) eq(L + " m of " + b + " m bays is " + want, bayLayout(L, b).count, want);
  ok("the trap is real: a bare floor gets 4.8 / 1.6 wrong", Math.floor(4.8 / 1.6) === 2);
  eq("a hair short is still short: 9.59 m holds seven 1.2 m bays", bayLayout(9.59, 1.2).count, 7);
  const a = bayLayout(4.8, 1.6);
  ok("an exact fit leaves nothing over, and fits", Math.abs(a.leftover) < 1e-9 && a.fits, JSON.stringify(a));
  near("and its bays touch", a.gap, 0, 1e-9);
}
{
  // A thousand random runs over every spread: the count is the most that fit, the bay is never
  // stretched, and margins, bays and gaps add back up to the length exactly.
  const r = rng(2026);
  const spreads: BaySpread[] = ["even", "around", "between", "centre"];
  let notMax = 0, stretched = 0, uneven = 0, outside = 0, worstSum = 0, worstSpace = 0;
  for (let i = 0; i < 1000; i++) {
    const L = 0.5 + r() * 20, b = 0.2 + r() * 3;
    const m = r() < 0.5 ? 0 : r() * 0.8, g = r() < 0.5 ? 0 : r() * 0.5;
    const lay = bayLayout(L, b, { margin: m, gap: g, spread: spreads[i % 4] });
    const n = lay.count;
    const fitsN = (k: number) => k === 0 || k * b + (k - 1) * g + 2 * m <= L + 1e-9;
    if (!fitsN(n) || fitsN(n + 1)) notMax++;
    if (lay.bay !== b) stretched++;
    if (!n) continue;
    worstSum = Math.max(worstSum, Math.abs(2 * lay.end + n * b + (n - 1) * lay.gap - L));
    for (let k = 1; k < n; k++) if (Math.abs(lay.centres[k] - lay.centres[k - 1] - lay.pitch) > 1e-9) uneven++;
    if (lay.centres[0] - b / 2 < lay.from + m - 1e-9 || lay.centres[n - 1] + b / 2 > lay.from + L - m + 1e-9) outside++;
    const sp = lay.spaces.reduce((acc, s) => acc + s.width, 0);
    worstSpace = Math.max(worstSpace, Math.abs(sp + n * b - (L - 2 * m)));
  }
  eq("the count is always the most whole bays that fit", notMax, 0);
  eq("and a bay is never stretched", stretched, 0);
  ok("margins, bays and gaps add back up to the length", worstSum < 1e-9, String(worstSum));
  eq("the pitch is even", uneven, 0);
  eq("no bay crosses a margin", outside, 0);
  ok("the spaces are exactly what the bays leave", worstSpace < 1e-9, String(worstSpace));
}
{
  const even = bayLayout(5.8, 1.2);
  eq("5.8 m of 1.2 m bays: four", even.count, 4);
  near("even: the leftover 1.0 is five gaps of 0.2", even.gap, 0.2);
  near("...and each end gets the same", even.end, 0.2);
  eq("...so the bays sit symmetric about the middle", even.centres.map((c) => +c.toFixed(9)), [-2.1, -0.7, 0.7, 2.1]);
  const around = bayLayout(5.8, 1.2, { spread: "around" });
  near("around: each bay centred in a slot of 5.8 / 4", around.pitch, 1.45);
  near("...so an end gets half a gap", around.end, 0.125);
  const between = bayLayout(5.8, 1.2, { spread: "between" });
  near("between: the ends flush", between.end, 0);
  near("...and a third of the leftover between each pair", between.gap, 1 / 3);
  const centre = bayLayout(5.8, 1.2, { spread: "centre" });
  near("centre: packed", centre.gap, 0);
  near("...with the leftover split at the two ends", centre.end, 0.5);
  eq("a single bay 'between' is centred", bayLayout(2, 1.2, { spread: "between" }).centres, [0]);
  eq("origin 'start' measures from 0", bayLayout(4.8, 1.6, { origin: "start" }).centres.map((c) => +c.toFixed(9)), [0.8, 2.4, 4]);
  const kept = bayLayout(5.8, 1.2, { margin: 0.4, gap: 0.1 });
  eq("margins and a minimum gap: (5.8 - 0.8 + 0.1) / 1.3 is 3.9, so three", kept.count, 3);
  eq("the spaces are two ends and two gaps, the margins not in them", kept.spaces.map((s) => +s.width.toFixed(9)), [0.3, 0.4, 0.4, 0.3]);
}
{
  // The configurator's width slider, 5.8 m to 9.8 m: it adds bays and never changes one.
  let prev = 0, grew = 0, shrank = 0, changed = 0;
  const counts: number[] = [];
  for (let k = 58; k <= 98; k++) {
    const lay = bayLayout(k / 10, 1.4, { margin: 0.35 });
    if (lay.bay !== 1.4) changed++;
    if (lay.count < prev) shrank++;
    if (prev && lay.count > prev) grew++;
    prev = lay.count;
    counts.push(lay.count);
  }
  eq("widening never removes a bay", shrank, 0);
  eq("and never changes the bay", changed, 0);
  eq("5.8 m holds three window bays and 9.8 m six", [counts[0], counts[counts.length - 1]], [3, 6]);
  eq("so three were added on the way", grew, 3);
}
{
  const forced = bayLayout(2, 1.2, { min: 2 });
  eq("min wins over fitting", forced.count, 2);
  ok("and says the bays do not fit", !forced.fits && forced.leftover < 0, JSON.stringify(forced));
  eq("they overlap evenly inside the run rather than shrink", [forced.bay, forced.centres.map((c) => +c.toFixed(9))], [1.2, [-0.4, 0.4]]);
  eq("max caps a ribbon window", bayLayout(20, 0.75, { max: 8, spread: "centre" }).count, 8);
  eq("max 0 is none", bayLayout(20, 1, { max: 0 }).count, 0);
  const eaten = bayLayout(1, 0.2, { margin: 0.6 });
  eq("margins that eat the run leave no bays, and nothing overlaps", [eaten.count, eaten.fits], [0, true]);
  eq("nothing silly throws: zero, negative, NaN",
    [bayLayout(0, 1).count, bayLayout(5, 0).count, bayLayout(NaN, 1).count, bayLayout(5, -1).count, bayLayout(5, NaN).count], [0, 0, 0, 0, 0]);
  const tooBig = bayLayout(3, 4);
  eq("a bay longer than the run: none, and the whole run is one space", [tooBig.count, tooBig.spaces.length, tooBig.spaces[0]?.width], [0, 1, 3]);
}
{
  const lay = facadeSlots({ length: 9.8, bay: (f) => (f === 0 ? 3.2 : 1.4), floors: 3, floorHeight: 3 });
  eq("a bay size per floor: three shop bays below, seven windows above", lay.floors.map((f) => f.count), [3, 7, 7]);
  eq("a slot for each", lay.slots.length, 17);
  eq("the floors stack", lay.floors.map((f) => f.y), [0, 3, 6]);
  eq("and the height adds up", lay.height, 9);
  ok("only the top floor is top", lay.slots.every((s) => s.top === (s.floor === 2)));
  ok("first and last mark the ends of each floor", lay.slots.filter((s) => s.first).length === 3 && lay.slots.filter((s) => s.last).length === 3);
  const side = facadeSlots({ length: 6, bay: 1.5, floors: 1, at: [5, 0, 0], yaw: Math.PI / 2 });
  const s0 = side.slots[0], sN = side.slots[side.slots.length - 1];
  ok("a wall facing +x stands at its x", side.slots.every((s) => Math.abs(s.position[0] - 5) < 1e-9));
  ok("and runs front to back, left to right as seen from outside", s0.position[2] > 0 && sN.position[2] < 0, JSON.stringify([s0.position, sN.position]));
  near("its slots carry the wall's yaw", s0.yaw, Math.PI / 2);
  const mixed = facadeSlots({ length: 6.4, bay: 0.75, floors: 2, perFloor: (f) => (f === 1 ? { spread: "centre", max: 4 } : null) });
  eq("perFloor overrides one floor's spread and count", [mixed.floors[0].count, mixed.floors[1].count, +mixed.floors[1].gap.toFixed(9)], [8, 4, 0]);
}
{
  let asked = 0;
  const widths: Record<string, number> = { shop: 3.0, door: 1.0 };
  const plan = facadePlan({ length: 6.4, bay: 1.6, floors: 1, spread: "centre" }, (s) => {
    asked++;
    if (s.bay === 0) return { piece: "shop", span: 2 };
    if (s.bay === 2) return "door";
    if (s.bay === 3) return "ghost";
    return null;
  }, (n) => widths[n]);
  eq("a span of two is asked about once", asked, 3);
  eq("the shop is centred on the two bays it covers", +plan.placements.shop[0].position![0].toFixed(9), -1.6);
  eq("the door takes its own bay", +plan.placements.door[0].position![0].toFixed(9), 0.8);
  eq("a name the kit lacks is reported, not placed", [plan.missing, plan.placements.ghost], [["ghost"], undefined]);
  eq("and no slot was left empty", plan.empty, 0);
  const filled = facadePlan({ length: 6.4, bay: 1.6, floors: 1, margin: 0.2, fill: "strip" }, (s) => (s.bay === 1 ? null : "win"),
    (n) => ({ win: 1.0, strip: 0.5 } as Record<string, number>)[n]);
  const stripW = filled.placements.strip.reduce((acc, p) => acc + (p.scale as V3)[0] * 0.5, 0);
  near("the filler closes exactly what no piece covers, between the margins", stripW + filled.placements.win.length * 1.0, 6.4 - 0.4, 1e-9);
  eq("one filler per open stretch: both ends and the empty middle bay with its gaps", filled.placements.strip.length, 3);
  ok("and only the filler is ever scaled", filled.placements.win.every((p) => p.scale === undefined));
  const street = facadePlan({ length: 4, bay: 2, floors: 1, at: [0, 0, 3] }, () => ({ piece: "crate", offset: [0, 0, 1.5], flip: true }));
  ok("an offset in z stands a piece out in the street", street.placements.crate.every((p) => Math.abs(p.position![2] - 4.5) < 1e-9));
  ok("and a flip is passed through", street.placements.crate.every((p) => p.flip === true));
}
{
  const p = perimeterSlots(8, 6, 0.5, { corner: 0.25 });
  eq("four corners, each where its side ends", p.corners.map((c) => c.position.map((v) => +v.toFixed(9))), [[4, 0, 3], [4, 0, -3], [-4, 0, -3], [-4, 0, 3]]);
  eq("the corner yaws follow the sides", p.corners.map((c) => +c.yaw.toFixed(6)), [0, 1.570796, 3.141593, -1.570796]);
  eq("each side is its own bay layout: the 8 m sides take 15, the 6 m sides 11", p.sides.map((s) => s.count), [15, 11, 15, 11]);
  let inward = 0, off = 0;
  for (const e of p.edges) {
    if (Math.sin(e.yaw) * e.position[0] + Math.cos(e.yaw) * e.position[2] <= 0) inward++;
    const onX = Math.abs(Math.abs(e.position[0]) - 4) < 1e-9, onZ = Math.abs(Math.abs(e.position[2]) - 3) < 1e-9;
    if (!onX && !onZ) off++;
  }
  eq("every edge slot faces outward", inward, 0);
  eq("and sits on the rectangle", off, 0);
  eq("an inset moves the corners in", perimeterSlots(8, 6, 0.5, { inset: 0.5 }).corners[0].position.map((v) => +v.toFixed(9)), [3.5, 0, 2.5]);
}
{
  const T = THREE;
  const det3 = (m: ArrayLike<number>) => m[0] * (m[5] * m[10] - m[6] * m[9]) - m[4] * (m[1] * m[10] - m[2] * m[9]) + m[8] * (m[1] * m[6] - m[2] * m[5]);
  const o = new T.Object3D();
  o.position.set(1, 2, 3); o.rotation.set(0.3, 0.7, -0.2); o.scale.set(2, 0.5, 1.5); o.updateMatrix();
  const mine = placementMatrix({ position: [1, 2, 3], rotation: [0.3, 0.7, -0.2], scale: [2, 0.5, 1.5] });
  ok("position, rotation and scale compose the way three does", mine.every((v, i) => Math.abs(v - o.matrix.elements[i]) < 1e-9), JSON.stringify(mine));
  near("a yaw of PI/2 turns the front (+z) to face +x", placementMatrix({ yaw: Math.PI / 2 })[8], 1, 1e-9);
  const f = placementMatrix({ flip: true, yaw: 0.4 });
  ok("a flip mirrors", det3(f) < 0);
  const d = mat4Decompose(f);
  ok("and decomposes to a negative x scale, as three's does", Math.abs(d.scale[0] + 1) < 1e-9, JSON.stringify(d.scale));
  eq("a bare position is a placement", placementMatrix([1, 2, 3]).slice(12, 15), [1, 2, 3]);
  eq("a Matrix4 passes through", placementMatrix(o.matrix), Array.from(o.matrix.elements));
  const r = rng(7);
  let worst = 0;
  for (let i = 0; i < 200; i++) {
    const m = placementMatrix({
      position: [r() * 10 - 5, r() * 10, r() * 10 - 5], rotation: [r() * 6, r() * 6, r() * 6],
      scale: [0.2 + r() * 3, 0.2 + r() * 3, 0.2 + r() * 3], flip: r() < 0.3,
    });
    const dd = mat4Decompose(m);
    const back = new T.Matrix4().compose(new T.Vector3(...dd.position), new T.Quaternion(...dd.quaternion), new T.Vector3(...dd.scale));
    for (let k = 0; k < 16; k++) worst = Math.max(worst, Math.abs(back.elements[k] - m[k]));
  }
  ok("decompose then compose gives the matrix back, mirrors included", worst < 1e-9, String(worst));
}
{
  // The kit on real three objects: measured, pivots normalised, the source left alone.
  const T = THREE;
  const std = new T.MeshStandardMaterial();
  const box = (w: number, h: number, d: number, name: string) => { const m = new T.Mesh(new T.BoxGeometry(w, h, d), std); m.name = name; return m; };
  const src = new T.Group();
  const wall = box(1.2, 3, 0.2, "wall"); wall.position.set(5, 7, 1);        // pack files lay pieces out anywhere
  const post = new T.Group(); post.name = "post"; post.position.set(-3, 0, 2);
  const shaft = box(0.2, 2.6, 0.2, "shaft"); shaft.position.y = 1.5;
  const cap = box(0.3, 0.2, 0.3, "cap"); cap.position.y = 2.9;
  post.add(shaft, cap);
  const bad = box(1.19, 3, 0.2, "bad");
  const nameless = box(1, 1, 1, "");
  const dupA = box(1, 1, 1, "dup"), dupB = box(2, 2, 2, "dup");
  const hollow = new T.Group(); hollow.name = "hollow";
  src.add(wall, post, bad, nameless, dupA, dupB, hollow);
  const ops = makeOps(T);
  const kit = ops.kit(src, { anchor: "back", anchors: { post: "bottom" }, grid: 0.2 });
  eq("pieces by name; the nameless and the hollow left out", kit.names, ["wall", "post", "bad", "dup"]);
  const w = kit.get("wall");
  // Six places, not nine: a BoxGeometry stores its corners as float32, so 0.6 is 0.600000024.
  eq("back anchor: back face on z = 0, centred, standing on y = 0", [w.min, w.max].map((v) => v.map((n) => +n.toFixed(6))), [[-0.6, 0, 0], [0.6, 3, 0.2]]);
  eq("measured in grid cells", w.cells, [6, 15, 1]);
  eq("and on the grid", w.offGrid, []);
  ok("a one-mesh piece is single", w.single);
  const p = kit.get("post");
  near("bottom anchor: stands on y = 0", p.min[1], 0, 1e-9);
  near("centred in x", p.min[0] + p.max[0], 0, 1e-9);
  near("and in z", p.min[2] + p.max[2], 0, 1e-9);
  ok("two meshes is not single", !p.single && p.meshes.length === 2);
  eq("its triangles counted", p.triangles, 24);
  eq("the off-grid piece is caught", kit.get("bad").offGrid, ["x"]);
  ok("and named in the notes with its error", kit.notes.some((n) => /^bad is 1\.190 wide: -0\.010 off the 0\.2 grid/.test(n)), kit.notes.join(" | "));
  ok("the nameless child is counted", kit.notes.some((n) => /1 child node\(s\) have no name/.test(n)));
  ok("the repeated name is named", kit.notes.some((n) => /"dup" names more than one node/.test(n)));
  ok("the hollow group is named", kit.notes.some((n) => /^hollow has no mesh/.test(n)));
  eq("the first of a repeated name wins", kit.get("dup").size.map((v) => +v.toFixed(9)), [1, 1, 1]);
  let threw = "";
  try { kit.get("walll"); } catch (e: any) { threw = String(e?.message || e); }
  ok("a typo throws, and lists what there is", /no piece "walll"/.test(threw) && /wall, post, bad, dup/.test(threw), threw);
  ok("the source is left alone", wall.position.x === 5 && wall.parent === src && post.position.x === -3);

  // A Z-up exporter puts its turn on an ancestor; a piece found by `match` must keep it.
  const zup = new T.Group(); zup.name = "RootNode"; zup.rotation.x = -Math.PI / 2;
  const tileA = box(1, 2, 3, "tileA"); tileA.position.set(9, 9, 9);
  const tileB = box(1, 2, 3, "tileB");
  zup.add(tileA, tileB);
  const pack = new T.Group(); pack.add(zup);
  const k2 = ops.kit(pack, { match: /^tile/g, anchor: "corner" });
  eq("match finds pieces at any depth, a /g RegExp included", k2.names, ["tileA", "tileB"]);
  eq("and a piece keeps the turn its ancestors gave it: 1 x 2 x 3 stood up is 1 x 3 x 2", k2.get("tileA").size.map((v) => +v.toFixed(9)), [1, 3, 2]);
  eq("the corner anchor puts the min corner at the origin", k2.get("tileA").min.map((v) => +v.toFixed(9)), [0, 0, 0]);
  eq("a record is a kit too, named by its keys", ops.kit({ a: box(1, 1, 1, "x"), b: post }).names, ["a", "b"]);
  // "wall": centred, back on z = 0, and the height it was modelled at kept — a window keeps its sill,
  // where "back" would stand it on the ground.
  const win = new T.Group(); win.name = "win";
  const glass = box(1.1, 1.3, 0.1, "glass"); glass.position.set(2, 0.95 + 0.65, 3); win.add(glass);
  eq("'wall' keeps a window's sill 0.95 up, centres it and backs it on z = 0", ops.kit({ win }, { anchor: "wall" }).get("win").min.map((v) => +v.toFixed(6)), [-0.55, 0.95, 0]);
  eq("'back' would have stood it on the floor", ops.kit({ win }, { anchor: "back" }).get("win").min.map((v) => +v.toFixed(6)), [-0.55, 0, 0]);
  eq("a null axis keeps the modeller's pivot on that axis", ops.kit({ win }, { anchor: [null, 0, null] }).get("win").min.map((v) => +v.toFixed(6)), [1.45, 0, 2.95]);
  eq("'origin' keeps all three", ops.kit({ win }, { anchor: "origin" }).get("win").min.map((v) => +v.toFixed(6)), [1.45, 0.95, 2.95]);

  // repeat: one mesh is one InstancedMesh; anything else is clones sharing their geometry.
  const at = [{ position: [0, 0, 0] as V3 }, { position: [2, 0, 0] as V3 }, { position: [4, 0, 0] as V3, yaw: Math.PI / 2 }];
  const walls = ops.repeat(w, at);
  const im = walls.children[0];
  ok("a single-mesh piece becomes ONE InstancedMesh", walls.children.length === 1 && im.isInstancedMesh && im.count === 3, walls.children.map((c: any) => c.type).join());
  eq("named for the piece", [walls.name, im.name], ["wall", "wall_inst"]);
  const M = new T.Matrix4();
  im.getMatrixAt(0, M);
  eq("the first copy's mesh centre is 1.5 up and 0.1 off the wall", [M.elements[12], M.elements[13], M.elements[14]].map((v) => +v.toFixed(6)), [0, 1.5, 0.1]);
  near("the turned copy faces +x: it reaches 0.2 past its own x", im.boundingBox.max.x, 4.2, 1e-6);
  near("every copy stands on the floor", im.boundingBox.min.y, 0, 1e-9);
  ok("the bounding sphere holds every copy, not just the one at the origin", im.boundingSphere.radius > 2, String(im.boundingSphere?.radius));
  eq("repeat says what it did", walls.userData.repeat, { piece: "wall", count: 3, instanced: true, draws: 1, triangles: 36 });
  const posts = ops.repeat(p, at);
  ok("a two-mesh piece is cloned", posts.children.length === 3 && posts.children.every((c: any) => !c.isInstancedMesh));
  eq("clones are named in order", posts.children.map((c: any) => c.name), ["post_0", "post_1", "post_2"]);
  let shared = 0;
  posts.children[1].traverse((c: any) => { if (c.isMesh && (c.geometry === shaft.geometry || c.geometry === cap.geometry)) shared++; });
  eq("and every clone shares the source's geometry", shared, 2);
  const always = ops.repeat(p, at, { instance: "always" });
  eq("'always' instances each mesh of a multi-mesh piece", always.children.map((c: any) => c.name).sort(), ["post_cap", "post_shaft"]);
  ok("...one copy per placement in each", always.children.every((c: any) => c.isInstancedMesh && c.count === 3));
  const never = ops.repeat(w, at, { instance: "never" });
  ok("'never' clones even a single mesh", never.children.length === 3 && !never.children[0].isInstancedMesh);
  ok("one copy is a clone, not an InstancedMesh of one", !ops.repeat(w, [[1, 0, 0]]).children[0].isInstancedMesh);
  const mir = ops.repeat(w, [{ position: [0, 0, 0] }, { position: [2, 0, 0] }, { position: [4, 0, 0], flip: true }]);
  eq("a mirrored copy gets a draw of its own", mir.children.map((c: any) => c.name), ["wall_inst", "wall_inst_mirrored"]);
  const mm = mir.children[1];
  ok("that node is itself mirrored, so its faces stay the right way out", mm.scale.x === -1 && mm.count === 1);
  mm.getMatrixAt(0, M);
  mm.updateMatrix();
  const net = new T.Matrix4().multiplyMatrices(mm.matrix, M).elements;
  const want = mat4Mul(placementMatrix({ position: [4, 0, 0], flip: true }), w.meshes[0].matrix);
  ok("and net, the copy is exactly the placement asked for", want.every((v, i) => Math.abs(v - net[i]) < 1e-9), JSON.stringify([Array.from(net), want]));
  let threw2 = "";
  try { ops.repeat(undefined, at); } catch (e: any) { threw2 = String(e?.message || e); }
  ok("repeating nothing says where pieces come from", /kit\.get\(name\)/.test(threw2), threw2);

  // facade: widening adds window bays and, past a threshold, a second shop — never a wider one.
  const fk = ops.kit({ win: box(1.0, 1.2, 0.1, "win"), shop: box(3.0, 2.6, 0.3, "shop"), strip: box(1, 3, 0.05, "strip") }, { anchor: "back" });
  const front = (width: number) => ops.facade(fk, { length: width, bay: (f) => (f === 0 ? 3.2 : 1.4), floors: 3, margin: 0.3, name: "front" },
    (s) => (s.floor === 0 ? "shop" : "win"));
  const count = (g: any, name: string) => g.getObjectByName(name)?.userData?.repeat?.count || 0;
  const narrow = front(5.8), wide = front(9.8);
  eq("5.8 m: one shop, and three windows a floor", [count(narrow, "front_shop"), count(narrow, "front_win")], [1, 6]);
  eq("9.8 m: two shops, and six windows a floor", [count(wide, "front_shop"), count(wide, "front_win")], [2, 12]);
  let unit = 0;
  const pos = new T.Vector3(), quat = new T.Quaternion(), scl = new T.Vector3();
  const wins = wide.getObjectByName("front_win").children[0];
  for (let i = 0; i < wins.count; i++) {
    wins.getMatrixAt(i, M);
    M.decompose(pos, quat, scl);
    if (Math.abs(scl.x - 1) < 1e-9 && Math.abs(scl.y - 1) < 1e-9 && Math.abs(scl.z - 1) < 1e-9) unit++;
  }
  eq("and not one window is scaled", unit, 12);
  eq("the facade reports its floors", wide.userData.facade.floors.map((f: any) => f.count), [2, 6, 6]);
  const holes = ops.facade(fk, { length: 5.8, bay: 1.4, floors: 1, margin: 0.3, fill: "strip", name: "side" }, (s) => (s.bay === 1 ? null : "win"));
  eq("a filler closes the empty bay and the leftover", holes.getObjectByName("side_strip")?.userData?.repeat?.count, 3);
  eq("two facades in one scene, no name used twice", sceneReport(new T.Group().add(narrow, holes)).duplicates, []);
  eq("a piece the kit lacks is named in the facade's report", ops.facade(fk, { length: 4, bay: 1, floors: 1 }, () => "chimney").userData.facade.missing, ["chimney"]);
}
{
  // The same kit on PlayCanvas entities, read by duck type the way the rest of the pc code is.
  class Ent {
    name: string; children: Ent[] = []; parent: Ent | null = null; enabled = true; render: any = null;
    p: V3 = [0, 0, 0]; q: [number, number, number, number] = [0, 0, 0, 1]; s: V3 = [1, 1, 1];
    constructor(name = "") { this.name = name; }
    addChild(c: Ent) { this.children.push(c); c.parent = this; }
    setLocalPosition(x: number, y: number, z: number) { this.p = [x, y, z]; }
    setLocalRotation(x: number, y: number, z: number, w: number) { this.q = [x, y, z, w]; }
    setLocalScale(x: number, y: number, z: number) { this.s = [x, y, z]; }
    getWorldTransform(): { data: number[] } {
      const l = Array.from(new THREE.Matrix4().compose(new THREE.Vector3(...this.p), new THREE.Quaternion(...this.q), new THREE.Vector3(...this.s)).elements) as number[];
      return { data: this.parent ? mat4Mul(this.parent.getWorldTransform().data, l) : l };
    }
    clone(): Ent {
      const c = new Ent(this.name);
      c.p = [...this.p] as V3; c.q = [...this.q] as [number, number, number, number]; c.s = [...this.s] as V3; c.enabled = this.enabled;
      if (this.render) c.render = { meshInstances: this.render.meshInstances.map((mi: any) => ({ mesh: mi.mesh, node: c })) };
      for (const k of this.children) c.addChild(k.clone());
      return c;
    }
  }
  const pops = makeOpsPc({ Entity: Ent }, null);
  const root = new Ent("pack");
  const wallE = new Ent("wall");
  wallE.render = { meshInstances: [{ mesh: { aabb: { center: { x: 0, y: 0, z: 0 }, halfExtents: { x: 0.6, y: 1.5, z: 0.1 } }, primitive: [{ count: 36 }] }, node: wallE }] };
  wallE.setLocalPosition(4, 2, 0);
  root.addChild(wallE);
  const pk = pops.kit(root, { anchor: "back", grid: 0.2 });
  const pw = pk.get("wall");
  eq("the kit measures entities the same way", [pw.min, pw.max].map((v) => v.map((n) => +n.toFixed(9))), [[-0.6, 0, 0], [0.6, 3, 0.2]]);
  eq("and counts their triangles", pw.triangles, 12);
  const row = pops.repeat(pw, [{ position: [1, 0, 0] }, { position: [3, 0, 0], yaw: Math.PI }]);
  eq("PlayCanvas copies are clones, named in order", row.children.map((c: Ent) => c.name), ["wall_0", "wall_1"]);
  const deepest = (e: Ent) => { let n = e; while (n.children.length) n = n.children[0]; const d = n.getWorldTransform().data; return [d[12], d[13], d[14]].map((v) => +v.toFixed(9)); };
  eq("a turned clone lands where its placement says", deepest(row.children[1]), [3, 1.5, -0.1]);
  eq("and repeat says it cloned", row.userData.repeat.instanced, false);
  const pf = pops.facade(pk, { length: 5.8, bay: 1.4, floors: 2, margin: 0.3, name: "front" }, () => "wall");
  eq("a PlayCanvas facade places what a three one does", pf.userData.facade.placed, { wall: 6 });
}

// ------------------------------------------------------------------
console.log(pass + " checks passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL " + f);
process.exit(fails.length ? 1 : 0);
