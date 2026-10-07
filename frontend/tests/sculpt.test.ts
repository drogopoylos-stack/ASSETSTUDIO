// Sculpt, decimate and sweep, tested on the numbers that would be wrong if the algorithms were.
//
// Run from frontend/:
//   npx esbuild tests/sculpt.test.ts --bundle --format=esm --platform=node --outfile=../data/tmp/sculpt.test.mjs --log-level=warning && node ../data/tmp/sculpt.test.mjs
// Add --perf after the .mjs for the contract's timings on real sizes (a 260k-triangle head).

import { checkArrays, isosurfaceArrays, type V3 } from "../src/components/engine/edit/ops";
import { buildBVH, nearestBVH, unwrapArrays } from "../src/components/engine/edit/model";
import { densifyArrays, sculptArrays, type Stroke } from "../src/components/engine/edit/sculpt";
import { decimateArrays } from "../src/components/engine/edit/decimate";
import { boundaryLoops, reach, rimArrays, sweepArrays } from "../src/components/engine/edit/sweep";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => { if (cond) { pass++; return; } fails.push(name + (extra ? "  <- " + extra : "")); };
const near = (name: string, got: number, want: number, tol: number) => ok(name, Math.abs(got - want) <= tol, "got " + got + ", want " + want + " +- " + tol);
const notes: string[] = [];
const PERF = process.argv.includes("--perf");

type Mesh = { pos: Float32Array; idx: Uint32Array; uv?: Float32Array };

// ------------------------------------------------------------------ meshes
/** A grid in the XY plane from -half to half, n by n quads, facing +Z. */
function grid(n: number, half = 1): Mesh {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) P.push(-half + (2 * half * i) / n, -half + (2 * half * j) / n, 0);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const a = j * (n + 1) + i, b = a + 1, c = a + n + 1, d = c + 1;
    I.push(a, b, d, a, d, c);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

/** The same grid with every quad left of x = 0 split along the other diagonal: a mirror-symmetric mesh. */
function symGrid(n: number, half = 1): Mesh {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) P.push(-half + (2 * half * i) / n, -half + (2 * half * j) / n, 0);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const a = j * (n + 1) + i, b = a + 1, c = a + n + 1, d = c + 1;
    if (2 * i + 1 >= n) I.push(a, b, d, a, d, c); else I.push(a, b, c, b, d, c);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

function icosphere(level: number, r = 1): Mesh {
  const t = (1 + Math.sqrt(5)) / 2;
  const V: V3[] = ([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]] as V3[])
    .map((p) => { const l = Math.hypot(...p); return [p[0] / l, p[1] / l, p[2] / l] as V3; });
  let F = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
    [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]];
  for (let l = 0; l < level; l++) {
    const cache = new Map<string, number>();
    const mid = (a: number, b: number) => {
      const k = a < b ? a + "," + b : b + "," + a;
      let m = cache.get(k);
      if (m === undefined) {
        const p: V3 = [(V[a][0] + V[b][0]) / 2, (V[a][1] + V[b][1]) / 2, (V[a][2] + V[b][2]) / 2];
        const len = Math.hypot(...p);
        m = V.length; V.push([p[0] / len, p[1] / len, p[2] / len]); cache.set(k, m);
      }
      return m;
    };
    const F2: number[][] = [];
    for (const [a, b, c] of F) { const ab = mid(a, b), bc = mid(b, c), ca = mid(c, a); F2.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]); }
    F = F2;
  }
  return { pos: Float32Array.from(V.flat().map((x) => x * r)), idx: Uint32Array.from(F.flat()) };
}

function torus(R: number, r: number, nu: number, nv: number): Mesh {
  const P: number[] = [], I: number[] = [];
  for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) {
    const u = (2 * Math.PI * i) / nu, v = (2 * Math.PI * j) / nv;
    P.push((R + r * Math.cos(v)) * Math.cos(u), r * Math.sin(v), (R + r * Math.cos(v)) * Math.sin(u));
  }
  for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) {
    const a = i * nv + j, b = ((i + 1) % nu) * nv + j, c = ((i + 1) % nu) * nv + ((j + 1) % nv), d = i * nv + ((j + 1) % nv);
    I.push(a, b, c, a, c, d);
  }
  const m = { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
  if (checkArrays(m.pos, m.idx).volume < 0) for (let t = 0; t < m.idx.length; t += 3) { const x = m.idx[t + 1]; m.idx[t + 1] = m.idx[t + 2]; m.idx[t + 2] = x; }
  return m;
}

/** An open tube along +Y with a UV seam at angle 0 (the column is duplicated with u = 0 and u = 1). */
function seamTube(segs: number, rows: number, r = 0.5, h = 2): Mesh {
  const P: number[] = [], UV: number[] = [], I: number[] = [];
  for (let i = 0; i <= rows; i++) for (let j = 0; j <= segs; j++) {
    const a = (2 * Math.PI * j) / segs;
    P.push(r * Math.cos(a), (h * i) / rows, -r * Math.sin(a));
    UV.push(j / segs, i / rows);
  }
  for (let i = 0; i < rows; i++) for (let j = 0; j < segs; j++) {
    const a = i * (segs + 1) + j, b = a + 1, c = a + segs + 2, d = a + segs + 1;
    I.push(a, b, c, a, c, d);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I), uv: Float32Array.from(UV) };
}

/** A unit cube as six separate quads (24 vertices): a procedural box, every corner three copies. */
function cube(size = 1): Mesh {
  const h = size / 2, P: number[] = [], I: number[] = [];
  const faces: Array<[V3, V3, V3]> = [
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
    [[0, -1, 0], [1, 0, 0], [0, 0, 1]], [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
  ];
  for (const [n, u, v] of faces) {
    const base = P.length / 3;
    for (const [su, sv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      for (let k = 0; k < 3; k++) P.push(n[k] * h + u[k] * su * h + v[k] * sv * h);
    }
    I.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

const at = (p: ArrayLike<number>, i: number): V3 => [p[i * 3], p[i * 3 + 1], p[i * 3 + 2]];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
const faceN = (m: Mesh, t: number): V3 => {
  const a = at(m.pos, m.idx[t * 3]), b = at(m.pos, m.idx[t * 3 + 1]), c = at(m.pos, m.idx[t * 3 + 2]);
  return cross(sub(b, a), sub(c, a));
};
const centroid = (m: Mesh, t: number): V3 => {
  const a = at(m.pos, m.idx[t * 3]), b = at(m.pos, m.idx[t * 3 + 1]), c = at(m.pos, m.idx[t * 3 + 2]);
  return [(a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3];
};
const maxEdge = (m: Mesh) => {
  let e = 0;
  for (let t = 0; t < m.idx.length; t += 3) for (let k = 0; k < 3; k++) e = Math.max(e, len(sub(at(m.pos, m.idx[t + k]), at(m.pos, m.idx[t + (k + 1) % 3]))));
  return e;
};
const meanEdge = (m: Mesh) => {
  let e = 0, c = 0;
  for (let t = 0; t < m.idx.length; t += 3) for (let k = 0; k < 3; k++) { e += len(sub(at(m.pos, m.idx[t + k]), at(m.pos, m.idx[t + (k + 1) % 3]))); c++; }
  return e / c;
};
const area = (m: Mesh) => { let s = 0; for (let t = 0; t < m.idx.length / 3; t++) s += len(faceN(m, t)) / 2; return s; };
const minQuality = (m: Mesh) => {
  let q = Infinity;
  for (let t = 0; t < m.idx.length / 3; t++) {
    const a = at(m.pos, m.idx[t * 3]), b = at(m.pos, m.idx[t * 3 + 1]), c = at(m.pos, m.idx[t * 3 + 2]);
    const s = dot(sub(b, a), sub(b, a)) + dot(sub(c, b), sub(c, b)) + dot(sub(a, c), sub(a, c));
    q = Math.min(q, (2 * Math.sqrt(3) * len(faceN(m, t))) / s);
  }
  return q;
};
/** The same mesh with its vertices listed in reverse order: the positions are unchanged. */
const reversed = (m: Mesh): Mesh => {
  const n = m.pos.length / 3, pos = new Float32Array(m.pos.length);
  for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) pos[(n - 1 - i) * 3 + k] = m.pos[i * 3 + k];
  return { pos, idx: m.idx.map((i) => n - 1 - i) };
};
const time = <T>(fn: () => T): [T, number] => { const t0 = performance.now(); const r = fn(); return [r, performance.now() - t0]; };

// ================================================================== densify
{
  const g = grid(4, 1);
  const d = densifyArrays(g.pos, g.idx, 0.1);
  ok("densify: every edge is at most maxEdge", maxEdge(d) <= 0.1 + 1e-6, String(maxEdge(d)));
  near("densify: the surface does not move (area)", area(d), 4, 1e-4);
  ok("densify: no face turns over", Array.from({ length: d.idx.length / 3 }, (_, t) => faceN(d, t)[2] > 0).every(Boolean));
  ok("densify: longest-edge bisection makes no slivers", minQuality(d) > 0.4, String(minQuality(d)));
  ok("densify: many more triangles", d.idx.length / 3 > 1000, String(d.idx.length / 3));
  let guarded = false;
  try { densifyArrays(g.pos, g.idx, 1e-5); } catch (e) { guarded = /20 million/.test(String(e)); }
  ok("densify: a maxEdge that would explode the mesh is refused, saying why", guarded);
  const d2 = densifyArrays(g.pos, g.idx, 0.1);
  ok("densify: deterministic", d2.pos.length === d.pos.length && d2.pos.every((v, i) => v === d.pos[i]) && d2.idx.every((v, i) => v === d.idx[i]));
  // A procedural box: every corner three copies. Split alike on both sides of every seam, it stays closed.
  const c = cube(1);
  const dc = densifyArrays(c.pos, c.idx, 0.08);
  const k = checkArrays(dc.pos, dc.idx);
  ok("densify: a box of separate faces stays closed once welded", k.holes === 0 && k.nonManifoldEdges === 0 && !k.inverted, JSON.stringify([k.holes, k.nonManifoldEdges]));
  near("densify: and keeps its volume", k.volume, 1, 1e-5);
  // Sculpted across its seams, the box still closes: copies move together.
  const sc = sculptArrays(dc.pos, dc.idx, [{ kind: "inflate", at: [0.5, 0.5, 0.2], radius: 0.4, depth: 0.1 }, { kind: "crease", path: [[0.5, 0.3, -0.3], [0.5, 0.5, 0], [0.3, 0.5, 0.3]], radius: 0.1 }]);
  const ks = checkArrays(sc, dc.idx);
  ok("sculpt: strokes across a seam of vertex copies leave no crack", ks.holes === 0 && ks.nonManifoldEdges === 0, JSON.stringify([ks.holes, ks.nonManifoldEdges]));
}

// ================================================================== sculpt: crease
const plane = grid(120, 0.06);   // 1 mm spacing, 12 cm across
const nP = plane.pos.length / 3;
{
  const r = 0.006, depth = -0.002;
  const out = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: [[-0.04, 0, 0], [0.04, 0, 0]], radius: r, depth }]);
  let under = 0, underN = 0, farMoved = 0, farN = 0, flips = 0;
  const at25: number[] = [];
  for (let i = 0; i < nP; i++) {
    const x = plane.pos[i * 3], y = plane.pos[i * 3 + 1];
    const dx = Math.max(0, Math.abs(x) - 0.04), dist = Math.hypot(dx, y);
    if (Math.abs(x) < 0.03 && Math.abs(y) < 1e-6) { under += out[i * 3 + 2]; underN++; }
    if (dist >= r) { farN++; if (out[i * 3] !== plane.pos[i * 3] || out[i * 3 + 1] !== plane.pos[i * 3 + 1] || out[i * 3 + 2] !== plane.pos[i * 3 + 2]) farMoved++; }
    if (Math.abs(x) < 0.03 && Math.abs(Math.abs(y) - 0.002) < 1e-6) at25.push(out[i * 3 + 2]);
  }
  for (let t = 0; t < plane.idx.length / 3; t++) if (faceN({ pos: out, idx: plane.idx }, t)[2] <= 0) flips++;
  near("crease: a groove of the asked depth under the path", under / Math.max(1, underN), depth, Math.abs(depth) * 0.03);
  ok("crease: vertices beyond the radius do not move at all", farMoved === 0 && farN > 10000, farMoved + " of " + farN);
  const v25 = at25.reduce((a, b) => a + b, 0) / at25.length / depth;
  ok("crease: a V - a third of the radius out it is only ~44% as deep", v25 > 0.4 && v25 < 0.49, String(v25));
  ok("crease: no face turns over", flips === 0, String(flips));
  // Its sides are drawn in toward the line, and never past it (the order across the groove holds).
  let order = true, drawn = 0;
  for (let j = 50; j < 70; j++) {
    const i = j * 121 + 60, i2 = (j + 1) * 121 + 60;
    if (out[i2 * 3 + 1] <= out[i * 3 + 1]) order = false;
    if (Math.abs(out[i * 3 + 1]) < Math.abs(plane.pos[i * 3 + 1]) - 1e-7) drawn++;
  }
  ok("crease: the sides are pinched in without crossing", order && drawn > 6, JSON.stringify([order, drawn]));
  const u = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: [[-0.04, 0, 0], [0.04, 0, 0]], radius: r, depth, profile: "u" }]);
  let s25 = 0, c25 = 0;
  for (let i = 0; i < nP; i++) if (Math.abs(plane.pos[i * 3]) < 0.03 && Math.abs(Math.abs(plane.pos[i * 3 + 1]) - 0.002) < 1e-6) { s25 += u[i * 3 + 2]; c25++; }
  ok("crease: a 'u' profile is rounded where the 'v' is sharp (~75% there)", s25 / c25 / depth > 0.7 && s25 / c25 / depth < 0.8, String(s25 / c25 / depth));
  // A path written a little above the surface still cuts at full depth: it is laid onto it first.
  const lifted = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: [[-0.04, 0, 0.003], [0.04, 0, 0.003]], radius: r, depth }]);
  near("crease: a path off the surface is laid onto it", lifted[(60 * 121 + 60) * 3 + 2], depth, Math.abs(depth) * 0.03);
  // Taper scales the radius and the depth along the path.
  const tp = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: [[-0.04, 0, 0], [0.04, 0, 0]], radius: r, depth, taper: [1, 0.2] }]);
  const zAt = (x: number) => { const i = 60 * 121 + Math.round((x + 0.06) / 0.001); return tp[i * 3 + 2]; };
  ok("crease: taper - full depth at the start, a fifth at the end", Math.abs(zAt(-0.04) - depth) < 1e-4 && Math.abs(zAt(0.04) - depth * 0.2) < 1e-4, JSON.stringify([zAt(-0.04), zAt(0.04)]));
  // A ridge goes the other way.
  const rd = sculptArrays(plane.pos, plane.idx, [{ kind: "ridge", path: [[-0.04, 0, 0], [0.04, 0, 0]], radius: r }]);
  near("ridge: raised by 0.35 * radius by default", rd[(60 * 121 + 60) * 3 + 2], 0.35 * r, 1e-5);
  // A curve through the points passes the middle point smoothly, a polyline turns a corner there.
  const bent: V3[] = [[-0.03, -0.02, 0], [0, 0.01, 0], [0.03, -0.02, 0]];
  const pl = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: bent, radius: 0.003, depth: -0.001 }]);
  const cv = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: bent, radius: 0.003, depth: -0.001, curve: true }]);
  const probe = (69 * 121 + 60 - 5) * 3 + 2;   // (x = -0.005, y = 0.009): inside the curve's arc, off the polyline's corner
  ok("crease: curve follows a smooth arc through the points", Math.abs(cv[probe] - pl[probe]) > 1e-4, JSON.stringify([pl[probe], cv[probe]]));
}

// ================================================================== sculpt: mirror
{
  const plane = symGrid(120, 0.06);
  const mirrorOf = (i: number) => { const row = Math.floor(i / 121), col = i % 121; return row * 121 + (120 - col); };
  const out = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: [[0.02, -0.03, 0], [0.025, 0.03, 0]], radius: 0.005, depth: -0.0015, mirror: "x" }]);
  const zR = out[(60 * 121 + 60 + 22) * 3 + 2], zL = out[(60 * 121 + 60 - 22) * 3 + 2];
  ok("mirror: the stroke lands on both sides", zR < -0.001 && zL < -0.001, JSON.stringify([zR, zL]));
  let asym = 0;
  for (let i = 0; i < nP; i++) {
    const j = mirrorOf(i);
    asym = Math.max(asym, Math.abs(out[i * 3] + out[j * 3]), Math.abs(out[i * 3 + 1] - out[j * 3 + 1]), Math.abs(out[i * 3 + 2] - out[j * 3 + 2]));
  }
  ok("mirror: the result is exactly symmetric", asym < 1e-7, String(asym));
  // A stroke across the plane is not applied twice where the copies overlap.
  const across = sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: [[-0.03, 0, 0], [0.03, 0, 0]], radius: 0.005, depth: -0.0015, mirror: "x" }]);
  near("mirror: a stroke across the mirror plane is not doubled", across[(60 * 121 + 60) * 3 + 2], -0.0015, 0.00005);
  // Mirror across another axis, and a smooth, a noise and an inflate mirrored too.
  const multi = sculptArrays(plane.pos, plane.idx, [
    { kind: "inflate", at: [0.02, 0.01, 0], radius: 0.01, depth: 0.001, mirror: "x" },
    { kind: "noise", radius: 0.01, depth: 0.0005, freq: 150, mirror: "x" },
    { kind: "smooth", at: [0.015, 0.0, 0], radius: 0.01, strength: 0.5, mirror: "x" },
    { kind: "clay", path: [[0.01, -0.02, 0], [0.02, 0.02, 0]], radius: 0.006, depth: 0.0008, mirror: "x" },
  ]);
  let asym2 = 0;
  for (let i = 0; i < nP; i++) { const j = mirrorOf(i); asym2 = Math.max(asym2, Math.abs(multi[i * 3] + multi[j * 3]), Math.abs(multi[i * 3 + 1] - multi[j * 3 + 1]), Math.abs(multi[i * 3 + 2] - multi[j * 3 + 2])); }
  ok("mirror: inflate, noise, smooth and clay stay symmetric too", asym2 < 1e-7, String(asym2));
}

// ================================================================== sculpt: noise
{
  const s: Stroke = { kind: "noise", radius: 0.02, depth: 0.001, freq: 60, seed: 7 };
  const a = sculptArrays(plane.pos, plane.idx, [s]);
  const b = sculptArrays(plane.pos, plane.idx, [s]);
  ok("noise: deterministic for a seed", a.every((v, i) => v === b[i]));
  const c = sculptArrays(plane.pos, plane.idx, [{ ...s, seed: 8 }]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff = Math.max(diff, Math.abs(a[i] - c[i]));
  ok("noise: another seed gives another surface", diff > 2e-4, String(diff));
  // By position, never by index: the same surface listed in another order sculpts the same.
  const r = reversed(plane);
  const ar = sculptArrays(r.pos, r.idx, [s]);
  let perm = 0;
  for (let i = 0; i < nP; i++) perm = Math.max(perm, Math.abs(ar[(nP - 1 - i) * 3 + 2] - a[i * 3 + 2]));
  ok("noise: a function of position, not of vertex order", perm < 1e-9, String(perm));
  // Coherent: neighbours move together; spread and peak close to the amplitude asked.
  let sum2 = 0, peak = 0, step = 0, steps = 0;
  for (let i = 0; i < nP; i++) { const z = a[i * 3 + 2]; sum2 += z * z; peak = Math.max(peak, Math.abs(z)); if (i % 121 < 120) { step += Math.abs(a[(i + 1) * 3 + 2] - z); steps++; } }
  const rms = Math.sqrt(sum2 / nP);
  ok("noise: coherent (neighbours differ far less than the relief)", step / steps < 0.2 * rms, JSON.stringify([step / steps, rms]));
  ok("noise: peaks near the asked amplitude", peak > 0.5 * 0.001 && peak < 1.6 * 0.001, String(peak));
  notes.push(`noise: rms ${(rms / 0.001).toFixed(3)} and peak ${(peak / 0.001).toFixed(3)} of depth (freq 60/m over 12 cm)`);
  // Streaks: longer along dir than across.
  const st = sculptArrays(plane.pos, plane.idx, [{ kind: "noise", radius: 0.02, depth: 0.001, freq: 80, dir: [1, 0, 0], seed: 3 }]);
  let gx = 0, gy = 0;
  for (let j = 1; j < 120; j++) for (let i = 1; i < 120; i++) { const k = j * 121 + i; gx += Math.abs(st[(k + 1) * 3 + 2] - st[k * 3 + 2]); gy += Math.abs(st[(k + 121) * 3 + 2] - st[k * 3 + 2]); }
  ok("noise: dir makes streaks along it", gy > 2.5 * gx, JSON.stringify([gx, gy]));
}

// ================================================================== sculpt: the other brushes
{
  const sphere = icosphere(4, 1);
  const n = sphere.pos.length / 3;
  const radii = (p: Float32Array) => { const r: number[] = []; for (let i = 0; i < n; i++) r.push(Math.hypot(p[i * 3], p[i * 3 + 1], p[i * 3 + 2])); return r; };
  const stats = (r: number[]) => { const m = r.reduce((a, b) => a + b, 0) / r.length; return { mean: m, sd: Math.sqrt(r.reduce((a, b) => a + (b - m) ** 2, 0) / r.length) }; };
  const rough = sculptArrays(sphere.pos, sphere.idx, [{ kind: "noise", radius: 1, depth: 0.03, freq: 12, seed: 2 }]);
  const before = stats(radii(rough));
  const smooth = sculptArrays(rough, sphere.idx, [{ kind: "smooth", radius: 1, strength: 1 }]);
  const after = stats(radii(smooth));
  ok("smooth: takes the lumps out of the whole mesh", after.sd < before.sd * 0.45, JSON.stringify([before.sd, after.sd]));
  ok("smooth: and does not shrink it", Math.abs(after.mean - before.mean) < 0.004 * before.mean, JSON.stringify([before.mean, after.mean]));
  // Inflate at a point: the top moves out by the depth, nothing beyond the radius moves.
  const inf = sculptArrays(sphere.pos, sphere.idx, [{ kind: "inflate", at: [0, 1, 0], radius: 0.3, depth: 0.05 }]);
  let top = -1, moved = 0;
  for (let i = 0; i < n; i++) {
    if (sphere.pos[i * 3 + 1] > 0.9999) top = i;
    const d = Math.hypot(sphere.pos[i * 3], sphere.pos[i * 3 + 1] - 1, sphere.pos[i * 3 + 2]);
    if (d >= 0.3 && (inf[i * 3] !== sphere.pos[i * 3] || inf[i * 3 + 1] !== sphere.pos[i * 3 + 1] || inf[i * 3 + 2] !== sphere.pos[i * 3 + 2])) moved++;
  }
  near("inflate: the point under the dab moves out by the depth", Math.hypot(inf[top * 3], inf[top * 3 + 1], inf[top * 3 + 2]), 1.05, 0.002);
  ok("inflate: nothing beyond the radius moves", moved === 0, String(moved));
  // Move: the region follows dir, fading out.
  const mv = sculptArrays(sphere.pos, sphere.idx, [{ kind: "move", at: [0, 1, 0], radius: 0.4, dir: [0.1, 0, 0] }]);
  near("move: the centre moves by dir", mv[top * 3] - sphere.pos[top * 3], 0.1, 0.002);
  // A local smooth takes a crease back out, and on a clean sphere it moves nothing (no sinking).
  const fine = icosphere(6, 1);
  let ftop = -1;
  for (let i = 0; i < fine.pos.length / 3; i++) if (fine.pos[i * 3 + 1] > 0.99999) ftop = i;
  const fdip = (p: Float32Array) => 1 - Math.hypot(p[ftop * 3], p[ftop * 3 + 1], p[ftop * 3 + 2]);
  const lane: V3[] = [[-0.3, 0.95, 0], [0.3, 0.95, 0]];
  const cr = sculptArrays(fine.pos, fine.idx, [{ kind: "crease", path: lane, radius: 0.06, depth: -0.02 }]);
  const soft = sculptArrays(cr, fine.idx, [{ kind: "smooth", path: lane, radius: 0.12, strength: 0.5 }]);
  const cs = sculptArrays(cr, fine.idx, [{ kind: "smooth", path: lane, radius: 0.12, strength: 1 }]);
  ok("smooth: a local smooth softens a crease, more with more strength", fdip(soft) < 0.45 * fdip(cr) && fdip(cs) < 0.3 * fdip(cr) && fdip(cs) < fdip(soft), JSON.stringify([fdip(cr), fdip(soft), fdip(cs)]));
  const clean = sculptArrays(fine.pos, fine.idx, [{ kind: "smooth", at: [0, 1, 0], radius: 0.3, strength: 1 }]);
  ok("smooth: a local smooth does not sink a curved surface", Math.abs(fdip(clean)) < 2e-4, String(fdip(clean)));
  // Flatten: a bumpy patch goes flat.
  const bumpy = sculptArrays(plane.pos, plane.idx, [{ kind: "noise", radius: 0.06, depth: 0.001, freq: 80, seed: 5 }]);
  const flat = sculptArrays(bumpy, plane.idx, [{ kind: "flatten", at: [0, 0, 0], radius: 0.02 }]);
  let vb = 0, vf = 0, cnt = 0;
  const mB = (() => { let s = 0, c = 0; for (let i = 0; i < nP; i++) if (Math.hypot(plane.pos[i * 3], plane.pos[i * 3 + 1]) < 0.006) { s += flat[i * 3 + 2]; c++; } return s / c; })();
  for (let i = 0; i < nP; i++) if (Math.hypot(plane.pos[i * 3], plane.pos[i * 3 + 1]) < 0.006) { vb += bumpy[i * 3 + 2] ** 2; vf += (flat[i * 3 + 2] - mB) ** 2; cnt++; }
  ok("flatten: the patch under the brush goes flat", vf < 0.05 * vb, JSON.stringify([vb / cnt, vf / cnt]));
  // Clay: a flat-topped layer; a second pass over it adds nothing where it already stands high.
  const clay = sculptArrays(plane.pos, plane.idx, [{ kind: "clay", path: [[-0.03, 0, 0], [0.03, 0, 0]], radius: 0.008, depth: 0.001 }]);
  let topOk = 0, topN = 0;
  for (let i = 0; i < nP; i++) if (Math.abs(plane.pos[i * 3]) < 0.025 && Math.abs(plane.pos[i * 3 + 1]) < 0.0035) { topN++; if (Math.abs(clay[i * 3 + 2] - 0.001) < 0.00005) topOk++; }
  ok("clay: builds a flat top at the depth", topOk === topN && topN > 100, topOk + " of " + topN);
  const peak = sculptArrays(plane.pos, plane.idx, [{ kind: "inflate", at: [0, 0, 0], radius: 0.003, depth: 0.003 }]);
  const onPeak = sculptArrays(peak, plane.idx, [{ kind: "clay", path: [[-0.03, 0, 0], [0.03, 0, 0]], radius: 0.008, depth: 0.001 }]);
  const ic = 60 * 121 + 60;
  ok("clay: fills up to its top and never cuts what stands higher", onPeak[ic * 3 + 2] === peak[ic * 3 + 2] && onPeak[(60 * 121 + 80) * 3 + 2] > 0.0009, JSON.stringify([peak[ic * 3 + 2], onPeak[ic * 3 + 2]]));
  // Pinch: the surface is drawn toward the line.
  const pin = sculptArrays(plane.pos, plane.idx, [{ kind: "pinch", path: [[-0.03, 0, 0], [0.03, 0, 0]], radius: 0.006, strength: 0.6 }]);
  const iy = 63 * 121 + 60;
  ok("pinch: vertices move toward the path, not along it", pin[iy * 3 + 1] < plane.pos[iy * 3 + 1] && Math.abs(pin[iy * 3] - plane.pos[iy * 3]) < 1e-9, JSON.stringify([plane.pos[iy * 3 + 1], pin[iy * 3 + 1]]));
  // Mask: nothing moves where it says 0.
  const mk = sculptArrays(plane.pos, plane.idx, [{ kind: "inflate", at: [0, 0, 0], radius: 0.02, depth: 0.002, mask: (p) => (p[0] > 0 ? 1 : 0) }]);
  let leftMoved = 0, rightMoved = 0;
  for (let i = 0; i < nP; i++) { if (mk[i * 3 + 2] !== 0) { if (plane.pos[i * 3] < 0) leftMoved++; else rightMoved++; } }
  ok("mask: weight 0 keeps a vertex still", leftMoved === 0 && rightMoved > 100, JSON.stringify([leftMoved, rightMoved]));
  // Errors that name the stroke.
  const throws = (f: () => unknown, re: RegExp) => { try { f(); return false; } catch (e) { return re.test(String(e)); } };
  ok("errors: an unknown kind is named", throws(() => sculptArrays(plane.pos, plane.idx, [{ kind: "carve" as Stroke["kind"], radius: 1 }]), /stroke 0 has kind "carve"/));
  ok("errors: a crease needs a path", throws(() => sculptArrays(plane.pos, plane.idx, [{ kind: "crease", radius: 0.01 }]), /needs a path/));
  ok("errors: a path far from the surface says so", throws(() => sculptArrays(plane.pos, plane.idx, [{ kind: "crease", path: [[0, 0, 1], [0.01, 0, 1]], radius: 0.01 }]), /nowhere near the surface/));
  ok("errors: a radius is required", throws(() => sculptArrays(plane.pos, plane.idx, [{ kind: "inflate", radius: 0 }]), /radius above zero/));
}

// ================================================================== decimate
{
  const noFlipsRadial = (m: Mesh, outward: (c: V3) => V3) => {
    let bad = 0;
    for (let t = 0; t < m.idx.length / 3; t++) if (dot(faceN(m, t), outward(centroid(m, t))) <= 0) bad++;
    return bad;
  };
  const sphere = icosphere(5, 1);   // 20,480 triangles
  const v0 = checkArrays(sphere.pos, sphere.idx).volume;
  const d = decimateArrays(sphere.pos, sphere.idx, { target: 1000 });
  const k = checkArrays(d.pos, d.idx);
  ok("decimate: a sphere to the target", d.idx.length / 3 <= 1000 && d.idx.length / 3 >= 950, String(d.idx.length / 3));
  ok("decimate: sphere volume within 2%", Math.abs(k.volume / v0 - 1) < 0.02, String(k.volume / v0));
  ok("decimate: sphere stays closed and manifold", k.holes === 0 && k.nonManifoldEdges === 0 && !k.inverted && k.degenerate === 0, JSON.stringify([k.holes, k.nonManifoldEdges, k.degenerate]));
  ok("decimate: no face on the sphere turns over", noFlipsRadial(d, (c) => c) === 0, String(noFlipsRadial(d, (c) => c)));
  ok("decimate: reports its collapses and error", d.collapsed > 9000 && d.error > 0 && d.error < 0.05, JSON.stringify([d.collapsed, d.error]));
  const d2 = decimateArrays(sphere.pos, sphere.idx, { target: 1000 });
  ok("decimate: deterministic", d2.pos.length === d.pos.length && d2.pos.every((v, i) => v === d.pos[i]) && d2.idx.every((v, i) => v === d.idx[i]));
  const rt = decimateArrays(sphere.pos, sphere.idx, { ratio: 0.1 });
  ok("decimate: ratio", Math.abs(rt.idx.length / 3 - 2048) <= 2, String(rt.idx.length / 3));
  const me = decimateArrays(sphere.pos, sphere.idx, { target: 100, maxError: 0.002 });
  ok("decimate: maxError stops before the surface moves further", me.idx.length / 3 > 100 && me.error <= 0.002, JSON.stringify([me.idx.length / 3, me.error]));

  const tor = torus(1, 0.3, 160, 60);   // 19,200 triangles
  const tv0 = checkArrays(tor.pos, tor.idx).volume;
  const dt = decimateArrays(tor.pos, tor.idx, { target: 1200 });
  const kt = checkArrays(dt.pos, dt.idx);
  ok("decimate: torus volume within 2%", Math.abs(kt.volume / tv0 - 1) < 0.02, String(kt.volume / tv0));
  ok("decimate: torus stays closed and manifold", kt.holes === 0 && kt.nonManifoldEdges === 0 && !kt.inverted, JSON.stringify([kt.holes, kt.nonManifoldEdges]));
  const torOut = (c: V3): V3 => { const l = Math.hypot(c[0], c[2]) || 1; return [c[0] - c[0] / l, c[1], c[2] - c[2] / l]; };
  ok("decimate: no face on the torus turns over", noFlipsRadial(dt, torOut) === 0, String(noFlipsRadial(dt, torOut)));

  // A UV seam stays a seam: both sides keep their own u, no triangle straddles it, borders hold.
  const tube = seamTube(64, 40);
  const du = decimateArrays(tube.pos, tube.idx, { target: 400, uv: tube.uv });
  const nu = du.pos.length / 3;
  let straddle = 0, uvErr = 0, seam0 = 0, seam1 = 0, offBorder = 0;
  for (let t = 0; t < du.idx.length / 3; t++) {
    const us = [0, 1, 2].map((k2) => du.uv![du.idx[t * 3 + k2] * 2]);
    if (Math.max(...us) - Math.min(...us) > 0.5) straddle++;
  }
  for (let i = 0; i < nu; i++) {
    const x = du.pos[i * 3], y = du.pos[i * 3 + 1], z = du.pos[i * 3 + 2];
    let ang = Math.atan2(-z, x) / (2 * Math.PI);
    if (ang < 0) ang += 1;
    const u = du.uv![i * 2], v = du.uv![i * 2 + 1];
    if (Math.abs(z) < 1e-6 && x > 0) { if (u < 0.01) seam0++; else if (u > 0.99) seam1++; }
    else uvErr = Math.max(uvErr, Math.abs(u - ang));
    uvErr = Math.max(uvErr, Math.abs(v - y / 2));
    const onBorder = Math.abs(y) < 1e-6 || Math.abs(y - 2) < 1e-6;
    if ((y < 1e-3 || y > 2 - 1e-3) && !onBorder) offBorder++;
  }
  ok("decimate: the seam keeps both sides (u = 0 and u = 1 at the same places)", seam0 >= 2 && seam0 === seam1, JSON.stringify([seam0, seam1]));
  ok("decimate: no triangle straddles the seam in UV", straddle === 0, String(straddle));
  ok("decimate: carried UVs still match the surface", uvErr < 0.02, String(uvErr));
  ok("decimate: open borders stay on their rims", offBorder === 0, String(offBorder));
  ok("decimate: a UV-seamed tube still has exactly its two rims", boundaryLoops(du.pos, du.idx).length === 2, String(boundaryLoops(du.pos, du.idx).length));
  ok("decimate: the UV tube reached its target", du.idx.length / 3 <= 400, String(du.idx.length / 3));

  // Open borders: a flat square keeps its outline exactly; 'lock' keeps every border vertex.
  const sq = grid(40, 1);
  const ds = decimateArrays(sq.pos, sq.idx, { target: 50 });
  let outline = true;
  const bl = boundaryLoops(ds.pos, ds.idx);
  for (const p of bl[0] || []) if (Math.abs(Math.abs(p[0]) - 1) > 1e-6 && Math.abs(Math.abs(p[1]) - 1) > 1e-6) outline = false;
  near("decimate: a square keeps its area", area(ds), 4, 1e-6);
  ok("decimate: and its outline (and corners)", bl.length === 1 && outline && ds.idx.length / 3 <= 50, JSON.stringify([bl.length, outline, ds.idx.length / 3]));
  const dl = decimateArrays(sq.pos, sq.idx, { target: 50, boundary: "lock" });
  ok("decimate: boundary 'lock' keeps all 160 border vertices", boundaryLoops(dl.pos, dl.idx)[0].length === 160, String(boundaryLoops(dl.pos, dl.idx)[0]?.length));

  // Creases: a densified box decimates back to a box.
  const c = cube(1);
  const dc = densifyArrays(c.pos, c.idx, 0.05);
  const db = decimateArrays(dc.pos, dc.idx, { target: 60 });
  const kb = checkArrays(db.pos, db.idx);
  near("decimate: a box keeps its volume (creases held)", kb.volume, 1, 1e-4);
  ok("decimate: and its corners", Math.abs(kb.bounds.size[0] - 1) < 1e-6 && Math.abs(kb.bounds.size[1] - 1) < 1e-6 && kb.holes === 0, JSON.stringify(kb.bounds.size));

  // A triangle soup (no shared vertices, as a smooth-shaded export holds) is welded first.
  const soupPos = new Float32Array(sphere.idx.length * 3);
  for (let i = 0; i < sphere.idx.length; i++) for (let k2 = 0; k2 < 3; k2++) soupPos[i * 3 + k2] = sphere.pos[sphere.idx[i] * 3 + k2];
  const soup = decimateArrays(soupPos, Uint32Array.from({ length: sphere.idx.length }, (_, i) => i), { target: 1000 });
  const ksoup = checkArrays(soup.pos, soup.idx);
  ok("decimate: a triangle soup is welded and decimated closed", soup.idx.length / 3 <= 1000 && ksoup.holes === 0 && ksoup.boundaryEdges === 0 && ksoup.duplicates === 0, JSON.stringify([soup.idx.length / 3, ksoup.holes, ksoup.boundaryEdges]));
  // Two separate spheres decimated as one mesh: both stay closed, neither vanishes.
  const pair = { pos: new Float32Array(sphere.pos.length * 2), idx: new Uint32Array(sphere.idx.length * 2) };
  pair.pos.set(sphere.pos);
  for (let i = 0; i < sphere.pos.length; i += 3) { pair.pos[sphere.pos.length + i] = sphere.pos[i] + 3; pair.pos[sphere.pos.length + i + 1] = sphere.pos[i + 1] * 0.2; pair.pos[sphere.pos.length + i + 2] = sphere.pos[i + 2] * 0.2; }
  pair.idx.set(sphere.idx);
  for (let i = 0; i < sphere.idx.length; i++) pair.idx[sphere.idx.length + i] = sphere.idx[i] + sphere.pos.length / 3;
  const dp = decimateArrays(pair.pos, pair.idx, { target: 60 });
  const kp = checkArrays(dp.pos, dp.idx);
  let leftN = 0, rightN = 0;
  for (let i = 0; i < dp.pos.length / 3; i++) if (dp.pos[i * 3] > 1.5) rightN++; else leftN++;
  ok("decimate: separate parts each stay closed and none vanishes", kp.holes === 0 && kp.nonManifoldEdges === 0 && leftN >= 4 && rightN >= 4 && dp.idx.length / 3 <= 60, JSON.stringify([kp.holes, kp.nonManifoldEdges, leftN, rightN, dp.idx.length / 3]));
  const empty = decimateArrays(new Float32Array(0), new Uint32Array(0), { target: 10 });
  ok("decimate: an empty mesh comes back empty", empty.pos.length === 0 && empty.idx.length === 0 && empty.collapsed === 0);
  // Many islands (smart UV project): the target is reached, no UV triangle turns over, UVs still fit.
  const uw = unwrapArrays(sphere.pos, sphere.idx, { angle: 50 });
  const du2 = decimateArrays(uw.pos, uw.idx, { target: 3000, uv: uw.uv });
  let uvFlip = 0, uvNeg = 0;
  for (let t = 0; t < uw.idx.length / 3; t++) {
    const [a, b, c] = [0, 1, 2].map((k2) => uw.idx[t * 3 + k2]);
    if ((uw.uv[b * 2] - uw.uv[a * 2]) * (uw.uv[c * 2 + 1] - uw.uv[a * 2 + 1]) - (uw.uv[c * 2] - uw.uv[a * 2]) * (uw.uv[b * 2 + 1] - uw.uv[a * 2 + 1]) <= 0) uvNeg++;
  }
  for (let t = 0; t < du2.idx.length / 3; t++) {
    const [a, b, c] = [0, 1, 2].map((k2) => du2.idx[t * 3 + k2]);
    if ((du2.uv![b * 2] - du2.uv![a * 2]) * (du2.uv![c * 2 + 1] - du2.uv![a * 2 + 1]) - (du2.uv![c * 2] - du2.uv![a * 2]) * (du2.uv![b * 2 + 1] - du2.uv![a * 2 + 1]) <= 0) uvFlip++;
  }
  const bvhU = buildBVH(uw.pos, uw.idx);
  let uvFit = 0;
  for (let t = 0; t < du2.idx.length / 3; t++) {
    const cc = centroid(du2, t);
    const cu = [0, 1, 2].reduce((acc, k2) => acc + du2.uv![du2.idx[t * 3 + k2] * 2], 0) / 3, cvv = [0, 1, 2].reduce((acc, k2) => acc + du2.uv![du2.idx[t * 3 + k2] * 2 + 1], 0) / 3;
    const hit = nearestBVH(bvhU, cc)!;
    const [a, b, c] = [0, 1, 2].map((k2) => uw.idx[hit.tri * 3 + k2]);
    const A = at(uw.pos, a), B = at(uw.pos, b), C = at(uw.pos, c), p = hit.point;
    const v0 = sub(B, A), v1 = sub(C, A), v2 = sub(p, A);
    const d00 = dot(v0, v0), d01 = dot(v0, v1), d11 = dot(v1, v1), d20 = dot(v2, v0), d21 = dot(v2, v1), den = d00 * d11 - d01 * d01;
    const wb = (d11 * d20 - d01 * d21) / den, wc = (d00 * d21 - d01 * d20) / den, wa = 1 - wb - wc;
    const su = wa * uw.uv[a * 2] + wb * uw.uv[b * 2] + wc * uw.uv[c * 2], sv = wa * uw.uv[a * 2 + 1] + wb * uw.uv[b * 2 + 1] + wc * uw.uv[c * 2 + 1];
    if (Math.hypot(su - cu, sv - cvv) < 0.01) uvFit++;
  }
  notes.push(`decimate with ${uw.islands} UV islands: ${uw.idx.length / 3} -> ${du2.idx.length / 3} tris, UV fit ${(uvFit / (du2.idx.length / 3) * 100).toFixed(1)}% of faces within 0.01`);
  ok("decimate: with many UV islands the target is still reached", du2.idx.length / 3 <= 3000, String(du2.idx.length / 3));
  ok("decimate: no UV triangle turns over", uvNeg === 0 && uvFlip === 0, JSON.stringify([uvNeg, uvFlip]));
  ok("decimate: carried UVs fit the surface (95% of faces within 0.01)", uvFit >= 0.95 * (du2.idx.length / 3), String(uvFit / (du2.idx.length / 3)));
  const kuv = checkArrays(du2.pos, du2.idx);
  ok("decimate: and the seams never open (closed once welded)", kuv.holes === 0 && kuv.nonManifoldEdges === 0, JSON.stringify([kuv.holes, kuv.nonManifoldEdges]));

  // Colours are carried: a linear ramp comes back exact at the new vertices.
  const cp = grid(30, 1);
  const col = new Float32Array((cp.pos.length / 3) * 3);
  for (let i = 0; i < cp.pos.length / 3; i++) { col[i * 3] = (cp.pos[i * 3] + 1) / 2; col[i * 3 + 1] = 0.5; col[i * 3 + 2] = (cp.pos[i * 3 + 1] + 1) / 2; }
  for (let i = 0; i < cp.pos.length / 3; i++) cp.pos[i * 3 + 2] = 0.1 * Math.sin(cp.pos[i * 3] * 2) * Math.cos(cp.pos[i * 3 + 1] * 2);
  const dcol = decimateArrays(cp.pos, cp.idx, { target: 200, color: col });
  let cErr = 0;
  for (let i = 0; i < dcol.pos.length / 3; i++) cErr = Math.max(cErr, Math.abs(dcol.color![i * 3] - (dcol.pos[i * 3] + 1) / 2), Math.abs(dcol.color![i * 3 + 2] - (dcol.pos[i * 3 + 1] + 1) / 2));
  ok("decimate: colours are carried and fit the new vertices", cErr < 0.01 && dcol.color!.length === dcol.pos.length, String(cErr));
}

// ================================================================== sweep
{
  const tube = sweepArrays([[0, 0, 0], [0, 0, 1]], { radius: 0.1, sides: 16 });
  let rOk = true;
  for (let i = 0; i < 17 * 2; i++) if (Math.abs(Math.hypot(tube.pos[i * 3], tube.pos[i * 3 + 1]) - 0.1) > 1e-6) rOk = false;
  ok("sweep: a straight tube's rings sit at the radius", rOk);
  const kt = checkArrays(tube.pos, tube.idx);
  near("sweep: a capped tube is closed, outward, with the prism's volume", kt.volume, 0.01 * (16 / 2) * Math.sin((2 * Math.PI) / 16), 1e-6);
  ok("sweep: closed once welded", kt.holes === 0 && kt.nonManifoldEdges === 0, JSON.stringify([kt.holes, kt.nonManifoldEdges]));
  ok("sweep: uv in 0..1", tube.uv.every((v) => v >= 0 && v <= 1) && tube.uv.length === (tube.pos.length / 3) * 2);

  // Normals: radial on a round tube, shared across the UV seam, flat on the caps.
  let radial = 1, seamSame = true;
  for (let i = 0; i < 17 * 2; i++) { const nx = tube.normal[i * 3], ny = tube.normal[i * 3 + 1]; const l = Math.hypot(tube.pos[i * 3], tube.pos[i * 3 + 1]); radial = Math.min(radial, (nx * tube.pos[i * 3] + ny * tube.pos[i * 3 + 1]) / l); }
  for (let ring = 0; ring < 2; ring++) for (let k2 = 0; k2 < 3; k2++) if (tube.normal[(ring * 17) * 3 + k2] !== tube.normal[(ring * 17 + 16) * 3 + k2]) seamSame = false;
  ok("sweep: a round tube's normals point straight out", radial > 0.999, String(radial));
  ok("sweep: the UV seam's two copies share one normal (no shading seam)", seamSame);
  const capN = tube.normal.slice(17 * 2 * 3);
  let capsOk = true;
  for (let i = 0; i < capN.length / 3; i++) if (Math.abs(Math.abs(capN[i * 3 + 2]) - 1) > 1e-6) capsOk = false;
  ok("sweep: caps are flat, facing along the path", capsOk && capN[2] < 0 && capN[capN.length - 1] > 0);
  // A diamond blade section keeps hard edges: each side's vertices carry that side's normal.
  const blade = sweepArrays([[0, 0, 0], [0, 1, 0]], { profile: [[1, 0], [0, 0.2], [-1, 0], [0, -0.2]], radius: 0.03, caps: false });
  let crisp = 0;
  for (let t = 0; t < blade.idx.length / 3; t++) {
    const f = faceN(blade, t), fl = len(f);
    for (let k2 = 0; k2 < 3; k2++) { const v = blade.idx[t * 3 + k2]; crisp = Math.max(crisp, 1 - dot(f, at(blade.normal, v)) / fl); }
  }
  ok("sweep: a diamond profile keeps crisp edges (normals split at the corners)", crisp < 1e-6 && blade.pos.length / 3 === 2 * 8, JSON.stringify([crisp, blade.pos.length / 3]));

  // A helix: the frame turns smoothly, never flips.
  const helix: V3[] = [];
  for (let i = 0; i <= 400; i++) { const a = (i / 400) * 8 * Math.PI; helix.push([0.5 * Math.cos(a), 0.3 * (a / (2 * Math.PI)), 0.5 * Math.sin(a)]); }
  const hs = sweepArrays(helix, { radius: 0.05, sides: 8, caps: false });
  let worst = 0, perp = 0;
  const frame = (i: number): V3 => { const p = at(hs.pos, i * 9), c = helix[i]; const d = sub(p, c); const l = len(d); return [d[0] / l, d[1] / l, d[2] / l]; };
  for (let i = 0; i < 400; i++) {
    const a = frame(i), b = frame(i + 1);
    worst = Math.max(worst, Math.acos(Math.min(1, dot(a, b))) * (180 / Math.PI));
    const t = sub(helix[i + 1], helix[Math.max(0, i - 1)]);
    perp = Math.max(perp, Math.abs(dot(a, t)) / len(t));
  }
  ok("sweep: along a helix the frame never flips (under 6 degrees a step)", worst < 6, String(worst));
  ok("sweep: the section stays square to the path", perp < 1e-3, String(perp));
  // An S-curve (an inflection, where a Frenet frame flips): no jump either.
  const S: V3[] = [];
  for (let i = 0; i <= 200; i++) { const x = (i / 200) * 4 - 2; S.push([x, Math.tanh(3 * x) * 0.5, 0.2 * Math.sin(x)]); }
  const ss = sweepArrays(S, { radius: 0.05, sides: 8, caps: false });
  let worstS = 0;
  for (let i = 0; i < 200; i++) {
    const a = sub(at(ss.pos, i * 9), S[i]), b = sub(at(ss.pos, (i + 1) * 9), S[i + 1]);
    worstS = Math.max(worstS, Math.acos(Math.min(1, dot(a, b) / (len(a) * len(b)))) * (180 / Math.PI));
  }
  ok("sweep: through an inflection the frame does not flip", worstS < 10, String(worstS));
  // A closed knot: it joins itself with no seam.
  const knot: V3[] = [];
  for (let i = 0; i < 300; i++) { const a = (i / 300) * 2 * Math.PI; knot.push([(2 + Math.cos(3 * a)) * Math.cos(2 * a), (2 + Math.cos(3 * a)) * Math.sin(2 * a), Math.sin(3 * a)]); }
  const ks = sweepArrays(knot, { radius: 0.2, sides: 10, closed: true });
  let same = true;
  for (let j = 0; j <= 10; j++) for (let k2 = 0; k2 < 3; k2++) if (ks.pos[j * 3 + k2] !== ks.pos[(300 * 11 + j) * 3 + k2]) same = false;
  ok("sweep: a closed path's last ring is its first", same);
  let joinN = true;
  for (let j = 0; j <= 10; j++) for (let k2 = 0; k2 < 3; k2++) if (ks.normal[j * 3 + k2] !== ks.normal[(300 * 11 + j) * 3 + k2]) joinN = false;
  ok("sweep: and shares its normals, so the join does not show", joinN);
  const kk = checkArrays(ks.pos, ks.idx);
  ok("sweep: a closed knot tube is watertight once welded", kk.holes === 0 && kk.nonManifoldEdges === 0 && kk.volume > 0, JSON.stringify([kk.holes, kk.nonManifoldEdges, kk.volume]));
  let worstK = 0;
  for (let i = 0; i < 300; i++) {
    const a = sub(at(ks.pos, i * 11), knot[i]), b = sub(at(ks.pos, (i + 1) * 11), knot[(i + 1) % 300]);
    worstK = Math.max(worstK, Math.acos(Math.min(1, dot(a, b) / (len(a) * len(b)))) * (180 / Math.PI));
  }
  ok("sweep: and its twist is spread, not stepped, at the join", worstK < 12, String(worstK));
  // A square profile, either winding: the prism's volume, outward.
  const sqCCW = sweepArrays([[0, 0, 0], [1, 0, 0]], { profile: [[-1, -1], [1, -1], [1, 1], [-1, 1]], radius: 0.05 });
  const sqCW = sweepArrays([[0, 0, 0], [1, 0, 0]], { profile: [[-1, 1], [1, 1], [1, -1], [-1, -1]], radius: 0.05 });
  near("sweep: a square profile makes a square bar", checkArrays(sqCCW.pos, sqCCW.idx).volume, 0.01, 1e-6);
  near("sweep: whichever way the profile winds", checkArrays(sqCW.pos, sqCW.idx).volume, 0.01, 1e-6);
  // A tapered radius and an L corner that keeps its thickness (mitred).
  const tap = sweepArrays([[0, 0, 0], [0, 1, 0]], { radius: (t) => 0.1 * (1 - t) + 0.01, sides: 12, caps: false });
  near("sweep: radius as a function of t", Math.hypot(tap.pos[13 * 3], tap.pos[13 * 3 + 2]), 0.01, 1e-6);
  const L = sweepArrays([[0, 0, 0], [1, 0, 0], [1, 1, 0]], { radius: 0.1, sides: 16, caps: false });
  // Mitred: the corner ring lies on BOTH straight tubes (distance r from each segment's line).
  let thick = 0;
  const lineD = (p: V3, a: V3, dir: V3) => len(sub(sub(p, a), [dir[0] * dot(sub(p, a), dir), dir[1] * dot(sub(p, a), dir), dir[2] * dot(sub(p, a), dir)]));
  for (let ring = 0; ring < 3; ring++) for (let j = 0; j <= 16; j++) {
    const p = at(L.pos, ring * 17 + j);
    if (ring < 2) thick = Math.max(thick, Math.abs(lineD(p, [0, 0, 0], [1, 0, 0]) - 0.1));
    if (ring > 0) thick = Math.max(thick, Math.abs(lineD(p, [1, 0, 0], [0, 1, 0]) - 0.1));
  }
  ok("sweep: a sharp corner is mitred, the tube keeps its thickness", thick < 1e-6, String(thick));

  // Boundary loops.
  const open = sweepArrays([[0, 0, 0], [0, 1, 0]], { radius: 0.2, sides: 16, caps: false });
  const lo = boundaryLoops(open.pos, open.idx);
  ok("boundaryLoops: an open tube has two rims (the UV seam copies are not a border)", lo.length === 2 && lo.every((l) => l.length === 16), JSON.stringify(lo.map((l) => l.length)));
  const gl = boundaryLoops(grid(10, 1).pos, grid(10, 1).idx);
  let signed = 0;
  if (gl[0]) for (let i = 0; i < gl[0].length; i++) { const a = gl[0][i], b = gl[0][(i + 1) % gl[0].length]; signed += a[0] * b[1] - b[0] * a[1]; }
  ok("boundaryLoops: a sheet has one loop, counter-clockwise seen from its face", gl.length === 1 && gl[0].length === 40 && signed > 0, JSON.stringify([gl.length, gl[0]?.length, signed]));
  ok("boundaryLoops: a closed shape has none", boundaryLoops(icosphere(2).pos, icosphere(2).idx).length === 0);
  ok("boundaryLoops: a UV-seamed tube still has two", boundaryLoops(seamTube(24, 6).pos, seamTube(24, 6).idx).length === 2);

  // A rim on a dome: a closed tube along the edge; inset moves it up into the shell.
  const dome: Mesh = (() => {
    const P: number[] = [], I: number[] = [], nu = 48, nv = 12;
    P.push(0, 1, 0);
    for (let j = 1; j <= nv; j++) for (let i = 0; i < nu; i++) {
      const th = (j / nv) * (Math.PI / 2), ph = (2 * Math.PI * i) / nu;
      P.push(Math.sin(th) * Math.cos(ph), Math.cos(th), -Math.sin(th) * Math.sin(ph));
    }
    for (let i = 0; i < nu; i++) I.push(0, 1 + i, 1 + ((i + 1) % nu));
    for (let j = 1; j < nv; j++) for (let i = 0; i < nu; i++) {
      const a = 1 + (j - 1) * nu + i, b = 1 + (j - 1) * nu + ((i + 1) % nu), c = a + nu, d = b + nu;
      I.push(a, c, d, a, d, b);
    }
    return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
  })();
  ok("dome: faces outward (test mesh)", checkArrays(dome.pos, dome.idx).holes > 0 && dot(faceN(dome, 0), [0, 1, 0]) > 0);
  const rim = rimArrays(dome.pos, dome.idx, { radius: 0.03, sides: 10 });
  const rk = checkArrays(rim.pos, rim.idx);
  let onEdge = 0;
  for (let i = 0; i < rim.pos.length / 3; i++) onEdge = Math.max(onEdge, Math.abs(Math.hypot(Math.hypot(rim.pos[i * 3], rim.pos[i * 3 + 2]) - 1, rim.pos[i * 3 + 1]) - 0.03));
  ok("rim: one closed tube along the dome's edge", rk.holes === 0 && rk.volume > 0 && rim.idx.length / 3 === 48 * 10 * 2, JSON.stringify([rk.holes, rim.idx.length / 3]));
  ok("rim: centred on the edge", onEdge < 2e-3, String(onEdge));
  const rimIn = rimArrays(dome.pos, dome.idx, { radius: 0.03, sides: 10, inset: 0.05 });
  let minY = Infinity;
  for (let i = 0; i < rimIn.pos.length / 3; i++) minY = Math.min(minY, rimIn.pos[i * 3 + 1]);
  ok("rim: inset moves it into the shell, away from the edge", minY > 0.015, String(minY));
}

// ================================================================== reach
{
  const root: V3 = [0, 1.4, 0], target: V3 = [0.3, 1.0, 0.25], pole: V3 = [0, 1.2, -1];
  const r = reach(root, target, [0.3, 0.28], pole);
  ok("reach: in range the end IS the target", r.reached && r.end[0] === target[0] && r.end[1] === target[1] && r.end[2] === target[2]);
  near("reach: the upper bone keeps its length", len(sub(r.joint, root)), 0.3, 1e-12);
  near("reach: the lower bone keeps its length", len(sub(r.end, r.joint)), 0.28, 1e-12);
  const axis = sub(target, root), mid: V3 = [root[0] + axis[0] * 0.5, root[1] + axis[1] * 0.5, root[2] + axis[2] * 0.5];
  const toPole = sub(pole, root), side = sub(toPole, [axis[0] * (dot(toPole, axis) / dot(axis, axis)), axis[1] * (dot(toPole, axis) / dot(axis, axis)), axis[2] * (dot(toPole, axis) / dot(axis, axis))]);
  ok("reach: the elbow bends toward the pole", dot(sub(r.joint, mid), side) > 0);
  const far = reach(root, [2, 1.4, 0], [0.3, 0.28], pole);
  ok("reach: out of range, a straight arm pointing at the target", !far.reached && len(cross(sub(far.end, root), [1, 0, 0])) < 1e-12 && Math.abs(len(sub(far.end, root)) - 0.58) < 1e-12 && Math.abs(len(sub(far.joint, root)) - 0.3) < 1e-12 && far.end[0] > 0);
  const close = reach(root, [0.01, 1.4, 0], [0.3, 0.2], pole);
  ok("reach: too close, folded as far as it goes", !close.reached && Math.abs(len(sub(close.end, root)) - 0.1) < 1e-12 && Math.abs(len(sub(close.end, close.joint)) - 0.2) < 1e-12);
  const edge = reach([0, 0, 0], [0.58, 0, 0], [0.3, 0.28], [0, 1, 0]);
  ok("reach: exactly at full stretch is a hit", edge.reached && edge.end[0] === 0.58 && Math.abs(len(edge.joint) - 0.3) < 1e-12);
}

// ================================================================== the contract's sizes and times
if (PERF) {
  const smin = (a: number, b: number, k: number) => { const h = Math.max(k - Math.abs(a - b), 0) / k; return Math.min(a, b) - h * h * k * 0.25; };
  const ell = (x: number, y: number, z: number, c: V3, r: V3) => {
    const dx = (x - c[0]) / r[0], dy = (y - c[1]) / r[1], dz = (z - c[2]) / r[2];
    return (Math.sqrt(dx * dx + dy * dy + dz * dz) - 1) * Math.min(r[0], r[1], r[2]);
  };
  const head = (x: number, y: number, z: number) => {
    const ax = Math.abs(x);
    let d = ell(x, y, z, [0, 0.03, -0.01], [0.075, 0.085, 0.09]);
    d = smin(d, ell(x, y, z, [0, -0.04, 0.02], [0.055, 0.05, 0.06]), 0.02);
    d = smin(d, ell(x, y, z, [0, -0.005, 0.085], [0.012, 0.022, 0.018]), 0.01);
    d = smin(d, ell(x, y, z, [0, 0.035, 0.07], [0.055, 0.012, 0.02]), 0.012);
    d = smin(d, ell(ax, y, z, [0.04, -0.015, 0.05], [0.022, 0.02, 0.02]), 0.012);
    d = smin(d, ell(ax, y, z, [0.078, 0, -0.005], [0.01, 0.028, 0.018]), 0.008);
    d = smin(d, ell(x, y, z, [0, -0.08, 0.045], [0.022, 0.018, 0.018]), 0.012);
    d = -smin(-d, ell(ax, y, z, [0.028, 0.015, 0.078], [0.013, 0.01, 0.01]), 0.008);
    return d;
  };
  const project = (m: Mesh) => {
    const h = 1e-5;
    for (let i = 0; i < m.pos.length; i += 3) {
      let x = m.pos[i], y = m.pos[i + 1], z = m.pos[i + 2];
      for (let it = 0; it < 3; it++) {
        const f = head(x, y, z);
        const gx = (head(x + h, y, z) - head(x - h, y, z)) / (2 * h), gy = (head(x, y + h, z) - head(x, y - h, z)) / (2 * h), gz = (head(x, y, z + h) - head(x, y, z - h)) / (2 * h);
        const g2 = gx * gx + gy * gy + gz * gz || 1;
        x -= (f * gx) / g2; y -= (f * gy) / g2; z -= (f * gz) / g2;
      }
      m.pos[i] = x; m.pos[i + 1] = y; m.pos[i + 2] = z;
    }
  };
  const box: [V3, V3] = [[-0.1, -0.11, -0.11], [0.1, 0.13, 0.12]];
  const iso = (h: number): Mesh => isosurfaceArrays(head, box[0], box[1], [Math.round(0.2 / h), Math.round(0.24 / h), Math.round(0.23 / h)]);
  const tris = (m: Mesh) => m.idx.length / 3;
  // Densify: about 20k -> about 200k.
  let small = iso(0.0036);
  for (let s = 0; s < 6 && Math.abs(tris(small) - 20000) > 1500; s++) small = iso(0.0036 * Math.sqrt(tris(small) / 20000));
  const me0 = meanEdge(small);
  let edge = me0 / Math.sqrt(10) * 1.25, dense = densifyArrays(small.pos, small.idx, edge);
  for (let s = 0; s < 8 && Math.abs(tris(dense) - 200000) > 12000; s++) { edge *= Math.sqrt(tris(dense) / 200000); dense = densifyArrays(small.pos, small.idx, edge); }
  const [dz, tDensify] = time(() => densifyArrays(small.pos, small.idx, edge));
  notes.push(`densifyArrays ${tris(small)} -> ${tris(dz)} tris: ${tDensify.toFixed(0)} ms (target < 2000)`);
  ok("perf: densify 20k -> 200k under 2 s", tDensify < 2000 && tris(dz) > 180000, `${tDensify.toFixed(0)} ms, ${tris(dz)} tris`);
  // The sculpt: 20 strokes on the ~200k head, projected smooth onto its field first.
  project(dz);
  const strokes: Stroke[] = [
    { kind: "crease", path: [[-0.035, 0.05, 0.078], [-0.012, 0.056, 0.084], [0.012, 0.056, 0.084], [0.035, 0.05, 0.078]], radius: 0.0035, curve: true, mirror: "x" },
    { kind: "crease", path: [[-0.03, 0.062, 0.074], [0, 0.066, 0.08], [0.03, 0.062, 0.074]], radius: 0.003, taper: [0.4, 0.4], curve: true },
    { kind: "crease", path: [[-0.006, 0.03, 0.09], [-0.004, 0.045, 0.088]], radius: 0.0025, taper: [1, 0.3], mirror: "x" },
    { kind: "crease", path: [[0.048, 0.02, 0.06], [0.058, 0.026, 0.05]], radius: 0.0025, taper: [1, 0.2], mirror: "x" },
    { kind: "crease", path: [[0.048, 0.012, 0.062], [0.058, 0.008, 0.052]], radius: 0.0025, taper: [1, 0.2], mirror: "x" },
    { kind: "crease", path: [[0.016, -0.01, 0.09], [0.026, -0.03, 0.085], [0.025, -0.05, 0.075]], radius: 0.004, curve: true, mirror: "x" },
    { kind: "ridge", path: [[-0.04, 0.04, 0.072], [0, 0.043, 0.082], [0.04, 0.04, 0.072]], radius: 0.006, depth: 0.002, curve: true },
    { kind: "inflate", at: [0.04, -0.015, 0.068], radius: 0.02, depth: 0.003, mirror: "x" },
    { kind: "inflate", at: [0, -0.012, 0.1], radius: 0.01, depth: 0.002 },
    { kind: "pinch", path: [[-0.03, 0.062, 0.074], [0.03, 0.062, 0.074]], radius: 0.004, strength: 0.4 },
    { kind: "flatten", at: [0, 0.07, 0.07], radius: 0.015, strength: 0.5 },
    { kind: "clay", path: [[-0.02, -0.055, 0.075], [0.02, -0.055, 0.075]], radius: 0.006, depth: 0.0015 },
    { kind: "move", at: [0.085, 0.02, -0.005], radius: 0.02, dir: [0.006, 0.004, -0.002], mirror: "x" },
    { kind: "noise", radius: 0.02, depth: 0.0004, freq: 150, octaves: 3, seed: 3 },
    { kind: "noise", at: [0, -0.07, 0.06], radius: 0.03, depth: 0.0006, freq: 300, dir: [0, 1, 0], seed: 4 },
    { kind: "smooth", at: [0, 0.07, 0.07], radius: 0.01, strength: 0.3 },
    { kind: "crease", path: [[-0.02, -0.03, 0.093], [0, -0.028, 0.098], [0.02, -0.03, 0.093]], radius: 0.002, curve: true },
    { kind: "inflate", at: [0.03, 0.02, 0.08], radius: 0.008, depth: -0.0015, mirror: "x" },
    { kind: "crease", path: [[0.075, 0.03, 0.0], [0.08, 0.0, 0.01], [0.075, -0.02, 0.0]], radius: 0.003, mirror: "x" },
    { kind: "smooth", radius: 0.02, strength: 0.2 },
  ];
  const [sculpted, tSculpt] = time(() => sculptArrays(dz.pos, dz.idx, strokes));
  notes.push(`sculptArrays ${tris(dz)} tris x ${strokes.length} strokes: ${tSculpt.toFixed(0)} ms (target < 3000)`);
  ok("perf: sculpt 200k x 20 strokes under 3 s", tSculpt < 3000, `${tSculpt.toFixed(0)} ms`);
  const ksc = checkArrays(sculpted, dz.idx);
  ok("perf: the sculpted head is still closed", ksc.holes === 0 && ksc.nonManifoldEdges === 0 && !ksc.inverted, JSON.stringify([ksc.holes, ksc.nonManifoldEdges]));
  // Decimate: a 260k head, sculpted, down to 7k.
  let e2 = edge * Math.sqrt(tris(dz) / 260000), big = densifyArrays(small.pos, small.idx, e2);
  for (let s = 0; s < 8 && Math.abs(tris(big) - 260000) > 10000; s++) { e2 *= Math.sqrt(tris(big) / 260000); big = densifyArrays(small.pos, small.idx, e2); }
  project(big);
  const high = { pos: sculptArrays(big.pos, big.idx, strokes), idx: big.idx };
  const before = checkArrays(high.pos, high.idx);
  const [low, tDec] = time(() => decimateArrays(high.pos, high.idx, { target: 7000 }));
  const after = checkArrays(low.pos, low.idx);
  notes.push(`decimateArrays ${tris(high)} -> ${tris(low)} tris: ${tDec.toFixed(0)} ms (target < 8000), max error ${(low.error * 1000).toFixed(3)} mm`);
  notes.push(`  checkArrays before: holes ${before.holes}, non-manifold ${before.nonManifoldEdges}, volume ${before.volume}; after: holes ${after.holes}, non-manifold ${after.nonManifoldEdges}, volume ${after.volume}, degenerate ${after.degenerate}`);
  ok("perf: decimate 260k -> 7k under 8 s", tDec < 8000 && tris(low) <= 7000 && tris(low) > 6800, `${tDec.toFixed(0)} ms, ${tris(low)} tris`);
  ok("perf: no new holes, no fins, not inside out", after.holes === before.holes && after.nonManifoldEdges === 0 && !after.inverted, JSON.stringify([before.holes, after.holes, after.nonManifoldEdges]));
  ok("perf: volume kept within 1%", Math.abs(after.volume / before.volume - 1) < 0.01, String(after.volume / before.volume));
  // No flipped face: every low face agrees with the high surface nearest its centre.
  const bvh = buildBVH(high.pos, high.idx);
  let flipped = 0, worstDist = 0;
  for (let t = 0; t < tris(low); t++) {
    const c = centroid(low, t), nl = faceN(low, t);
    const hit = nearestBVH(bvh, c)!;
    worstDist = Math.max(worstDist, Math.sqrt(hit.d2));
    if (dot(nl, faceN(high, hit.tri)) <= 0) flipped++;
  }
  notes.push(`  flipped faces against the high surface: ${flipped}; farthest face centre from it ${(worstDist * 1000).toFixed(3)} mm`);
  ok("perf: no flipped faces", flipped === 0, String(flipped));
  // The same, carrying UVs from a smart projection of the 260k mesh (seams everywhere).
  const [uwh, tUw] = time(() => unwrapArrays(high.pos, high.idx, { angle: 60 }));
  const [lowUv, tDecUv] = time(() => decimateArrays(uwh.pos, uwh.idx, { target: 7000, uv: uwh.uv }));
  const kl = checkArrays(lowUv.pos, lowUv.idx);
  notes.push(`decimateArrays with UVs (${uwh.islands} islands, unwrap ${tUw.toFixed(0)} ms): ${tris(uwh)} -> ${tris(lowUv)} tris in ${tDecUv.toFixed(0)} ms; holes ${kl.holes}, non-manifold ${kl.nonManifoldEdges}`);
  ok("perf: decimate 260k with UV seams under 8 s, closed", tDecUv < 8000 && kl.holes === 0 && kl.nonManifoldEdges === 0, `${tDecUv.toFixed(0)} ms, ${tris(lowUv)} tris`);
}

// ------------------------------------------------------------------
for (const n of notes) console.log("  " + n);
console.log(pass + " checks passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL " + f);
process.exit(fails.length ? 1 : 0);
