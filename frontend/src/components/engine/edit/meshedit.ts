// Blender's edit-mode tools, for a mesh a program made: extrude, inset, loop cut, knife, merge,
// delete, fill, bridge, subdivide and move.
//
// THE MESH IS REBUILT BY ITS CODE, SO A TOOL IS A STEP, NOT A CHANGE. Every build makes the mesh
// again from nothing, and an extrude made yesterday has to happen again today, on today's mesh. So
// what the editor saves is the list of steps, per mesh, in order: "extrude the faces whose corners
// are at these places, by 0.2". The steps run after the hand-moved vertices and before the
// modifier stack, which is Blender's own order: edit mode is the base mesh, modifiers come after.
//
// A STEP FINDS ITS CORNERS BY PLACE. An index means nothing once the code changes; a place keeps its
// meaning as long as the code still puts a corner there. The places are the corners as they stood
// when the step was made, so a later step finds the corners an earlier step made.
//
// Every tool works on the WELDED mesh (one vertex per place) and gives back flat-shaded-by-angle
// triangles, so a hard edge stays hard. UVs and vertex colours do not survive a change of
// topology; add an Unwrap modifier after the steps, as in Blender after modelling.
//
// Plain numbers and arrays, deterministic, no three.

import { sculptArrays, type Stroke } from "./sculpt";

type V3 = [number, number, number];

export type TopoOp = "extrude" | "inset" | "loopcut" | "knife" | "merge" | "delete" | "fill" | "bridge" | "subdivide" | "move"
  | "sculpt" | "densify";
export const TOPO_OPS: TopoOp[] = ["extrude", "inset", "loopcut", "knife", "merge", "delete", "fill", "bridge", "subdivide", "move",
  "sculpt", "densify"];
export const SCULPT_BRUSHES: Stroke["kind"][] = ["clay", "inflate", "crease", "ridge", "smooth", "flatten", "pinch", "move", "noise"];

export interface TopoStep {
  /** Stable key of the mesh (as VertEdit.mesh). */
  mesh: string;
  op: TopoOp;
  /** The selected corners, by place, in the mesh's own frame, as they were when the step was made. */
  sel: V3[];
  args?: Record<string, number | string | boolean>;
  /** 'move': where each corner went. */
  moves?: { at: V3; to: V3 }[];
  /** 'sculpt': the stroke's path on the surface, in the mesh's own frame. */
  path?: V3[];
  off?: boolean;
}

/** A welded mesh: one vertex per place. */
export interface WMesh { pos: number[]; tris: number[] }

export interface StepResult {
  mesh: WMesh;
  /** The vertices the tool leaves selected, as Blender does: the new faces after an extrude. */
  out: number[];
  /** Set when the step could not run as asked. The mesh is then the input, unchanged. */
  error?: string;
}

const WELD_TOL = 1e-6;
const SEL_TOL = 1.5e-3;

// ------------------------------------------------------------------ vectors
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: V3): V3 => { const l = len(a); return l > 1e-15 ? [a[0] / l, a[1] / l, a[2] / l] : [0, 0, 0]; };

const P = (m: WMesh, i: number): V3 => [m.pos[i * 3], m.pos[i * 3 + 1], m.pos[i * 3 + 2]];
const addV = (m: WMesh, p: V3): number => { m.pos.push(p[0], p[1], p[2]); return m.pos.length / 3 - 1; };
const nVerts = (m: WMesh) => m.pos.length / 3;
const nTris = (m: WMesh) => m.tris.length / 3;
const triNormal = (m: WMesh, t: number): V3 => {
  const a = P(m, m.tris[t * 3]), b = P(m, m.tris[t * 3 + 1]), c = P(m, m.tris[t * 3 + 2]);
  return cross(sub(b, a), sub(c, a));
};
const ek = (a: number, b: number) => (a < b ? a + "," + b : b + "," + a);
const copy = (m: WMesh): WMesh => ({ pos: m.pos.slice(), tris: m.tris.slice() });

// ------------------------------------------------------------------ in and out

/** One vertex per place. `idx` null means consecutive triangles. */
export function weld(pos: ArrayLike<number>, idx: ArrayLike<number> | null): WMesh {
  const n = Math.floor(pos.length / 3);
  const map = new Int32Array(n);
  const seen = new Map<string, number>();
  const out: WMesh = { pos: [], tris: [] };
  for (let i = 0; i < n; i++) {
    const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
    const k = Math.round(x / WELD_TOL) + "," + Math.round(y / WELD_TOL) + "," + Math.round(z / WELD_TOL);
    let v = seen.get(k);
    if (v === undefined) { v = out.pos.length / 3; out.pos.push(x, y, z); seen.set(k, v); }
    map[i] = v;
  }
  const count = idx ? idx.length : n;
  for (let t = 0; t + 2 < count; t += 3) {
    const a = map[idx ? idx[t] : t], b = map[idx ? idx[t + 1] : t + 1], c = map[idx ? idx[t + 2] : t + 2];
    if (a !== b && b !== c && a !== c) out.tris.push(a, b, c);
  }
  return out;
}

/**
 * Triangles to draw: a corner is shared by the faces round it whose normals are within `angle`
 * degrees of each other, so a hard edge the tools made (an extrude's rim) stays hard and a curved
 * surface stays smooth. Unindexed, one normal per corner.
 */
export function unweld(m: WMesh, angle = 35): { pos: Float32Array; normal: Float32Array } {
  const nt = nTris(m);
  const fn: V3[] = [];
  for (let t = 0; t < nt; t++) fn.push(triNormal(m, t));
  const unit = fn.map(norm);
  const around: number[][] = Array.from({ length: nVerts(m) }, () => []);
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) around[m.tris[t * 3 + k]].push(t);
  const cosA = Math.cos((angle * Math.PI) / 180);
  const pos = new Float32Array(nt * 9), normal = new Float32Array(nt * 9);
  for (let t = 0; t < nt; t++) {
    for (let k = 0; k < 3; k++) {
      const v = m.tris[t * 3 + k];
      let s: V3 = [0, 0, 0];
      for (const u of around[v]) if (dot(unit[u], unit[t]) >= cosA) s = add(s, fn[u]);
      const nn = norm(s);
      const o = (t * 3 + k) * 3;
      pos[o] = m.pos[v * 3]; pos[o + 1] = m.pos[v * 3 + 1]; pos[o + 2] = m.pos[v * 3 + 2];
      normal[o] = nn[0]; normal[o + 1] = nn[1]; normal[o + 2] = nn[2];
    }
  }
  return { pos, normal };
}

/** Drop vertices no triangle uses, and triangles that collapsed. */
function compact(m: WMesh): { mesh: WMesh; remap: Int32Array } {
  const remap = new Int32Array(nVerts(m)).fill(-1);
  const out: WMesh = { pos: [], tris: [] };
  for (let t = 0; t < nTris(m); t++) {
    const a = m.tris[t * 3], b = m.tris[t * 3 + 1], c = m.tris[t * 3 + 2];
    if (a === b || b === c || a === c) continue;
    for (const v of [a, b, c]) if (remap[v] < 0) { remap[v] = out.pos.length / 3; out.pos.push(m.pos[v * 3], m.pos[v * 3 + 1], m.pos[v * 3 + 2]); }
    out.tris.push(remap[a], remap[b], remap[c]);
  }
  return { mesh: out, remap };
}

/** The vertices at the given places: each place takes the nearest vertex within SEL_TOL. */
export function findVerts(m: WMesh, places: V3[], tol = SEL_TOL): Set<number> {
  const cell = tol * 2;
  const grid = new Map<string, number[]>();
  for (let i = 0; i < nVerts(m); i++) {
    const k = Math.floor(m.pos[i * 3] / cell) + "," + Math.floor(m.pos[i * 3 + 1] / cell) + "," + Math.floor(m.pos[i * 3 + 2] / cell);
    const l = grid.get(k); if (l) l.push(i); else grid.set(k, [i]);
  }
  const out = new Set<number>();
  for (const p of places) {
    const cx = Math.floor(p[0] / cell), cy = Math.floor(p[1] / cell), cz = Math.floor(p[2] / cell);
    let best = -1, bd = tol;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      for (const i of grid.get((cx + dx) + "," + (cy + dy) + "," + (cz + dz)) || []) {
        const d = len(sub(P(m, i), p));
        if (d <= bd) { bd = d; best = i; }
      }
    }
    if (best >= 0) out.add(best);
  }
  return out;
}

const selFaces = (m: WMesh, S: Set<number>): number[] => {
  const f: number[] = [];
  for (let t = 0; t < nTris(m); t++) if (S.has(m.tris[t * 3]) && S.has(m.tris[t * 3 + 1]) && S.has(m.tris[t * 3 + 2])) f.push(t);
  return f;
};

/** Directed edges of a set of faces that no other face of the set has reversed: its rim, each
 *  edge in its face's winding. */
function rimOf(m: WMesh, faces: number[]): Array<[number, number, number]> {
  const dir = new Set<string>();
  for (const t of faces) for (let k = 0; k < 3; k++) dir.add(m.tris[t * 3 + k] + ">" + m.tris[t * 3 + (k + 1) % 3]);
  const out: Array<[number, number, number]> = [];
  for (const t of faces) for (let k = 0; k < 3; k++) {
    const a = m.tris[t * 3 + k], b = m.tris[t * 3 + (k + 1) % 3];
    if (!dir.has(b + ">" + a)) out.push([a, b, t]);
  }
  return out;
}

// ------------------------------------------------------------------ the tools

/** Extrude the selected faces as one region, along the region's mean normal by `distance`. */
export function extrude(m0: WMesh, S: Set<number>, distance: number): StepResult {
  const faces = selFaces(m0, S);
  if (!faces.length) return { mesh: m0, out: [], error: "extrude needs selected faces" };
  const m = copy(m0);
  let n: V3 = [0, 0, 0];
  for (const t of faces) n = add(n, triNormal(m, t));
  n = norm(n);
  if (!len(n)) return { mesh: m0, out: [], error: "the selected faces cancel out: no direction to extrude in" };
  const rim = rimOf(m, faces);
  const twin = new Map<number, number>();
  for (const t of faces) for (let k = 0; k < 3; k++) {
    const v = m.tris[t * 3 + k];
    if (!twin.has(v)) twin.set(v, addV(m, add(P(m, v), mul(n, distance))));
  }
  for (const t of faces) for (let k = 0; k < 3; k++) m.tris[t * 3 + k] = twin.get(m.tris[t * 3 + k])!;
  // A wall on every rim edge a->b: (a, b, b') and (a, b', a') face outward for a face wound round +n.
  for (const [a, b] of rim) {
    const a2 = twin.get(a)!, b2 = twin.get(b)!;
    m.tris.push(a, b, b2, a, b2, a2);
  }
  return { mesh: m, out: [...twin.values()] };
}

/** Inset the selected faces as one region: a new rim `thickness` inside the old one. */
export function inset(m0: WMesh, S: Set<number>, thickness: number): StepResult {
  const faces = selFaces(m0, S);
  if (!faces.length) return { mesh: m0, out: [], error: "inset needs selected faces" };
  const m = copy(m0);
  const rim = rimOf(m, faces);
  if (!rim.length) return { mesh: m0, out: [], error: "the selection is closed: it has no rim to inset" };
  // Each rim vertex moves into the region along the mean of its rim edges' inward perpendiculars,
  // lengthened at a corner so the new rim stays `thickness` from both edges.
  const perps = new Map<number, V3[]>();
  for (const [a, b, t] of rim) {
    const fnrm = norm(triNormal(m, t));
    const inward = norm(cross(fnrm, sub(P(m, b), P(m, a))));
    for (const v of [a, b]) { const l = perps.get(v); if (l) l.push(inward); else perps.set(v, [inward]); }
  }
  const twin = new Map<number, number>();
  for (const [v, ps] of perps) {
    let d: V3 = [0, 0, 0];
    for (const q of ps) d = add(d, q);
    d = norm(d);
    const c = Math.max(0.35, Math.min(...ps.map((q) => dot(q, d))));
    twin.set(v, addV(m, add(P(m, v), mul(d, thickness / c))));
  }
  for (const t of faces) for (let k = 0; k < 3; k++) { const v = m.tris[t * 3 + k]; if (twin.has(v)) m.tris[t * 3 + k] = twin.get(v)!; }
  for (const [a, b] of rim) m.tris.push(a, b, twin.get(b)!, a, twin.get(b)!, twin.get(a)!);
  const out = new Set<number>();
  for (const t of faces) for (let k = 0; k < 3; k++) out.add(m.tris[t * 3 + k]);
  return { mesh: m, out: [...out] };
}

/** Collapse the selected vertices into one, at their middle. */
export function merge(m0: WMesh, S: Set<number>): StepResult {
  if (S.size < 2) return { mesh: m0, out: [], error: "merge needs two or more selected vertices" };
  const m = copy(m0);
  let c: V3 = [0, 0, 0];
  for (const v of S) c = add(c, P(m, v));
  const at = addV(m, mul(c, 1 / S.size));
  for (let i = 0; i < m.tris.length; i++) if (S.has(m.tris[i])) m.tris[i] = at;
  const r = compact(m);
  return { mesh: r.mesh, out: r.remap[at] >= 0 ? [r.remap[at]] : [] };
}

/** Delete the selected faces; with no whole face selected, every face that touches a selected vertex. */
export function remove(m0: WMesh, S: Set<number>): StepResult {
  let faces = new Set(selFaces(m0, S));
  if (!faces.size) {
    for (let t = 0; t < nTris(m0); t++) if (S.has(m0.tris[t * 3]) || S.has(m0.tris[t * 3 + 1]) || S.has(m0.tris[t * 3 + 2])) faces.add(t);
  }
  if (!faces.size) return { mesh: m0, out: [], error: "nothing selected to delete" };
  const m: WMesh = { pos: m0.pos.slice(), tris: [] };
  for (let t = 0; t < nTris(m0); t++) if (!faces.has(t)) m.tris.push(m0.tris[t * 3], m0.tris[t * 3 + 1], m0.tris[t * 3 + 2]);
  return { mesh: compact(m).mesh, out: [] };
  // (faces is a Set so a face counted twice is deleted once)
}

/** The mesh's open rims (edges with one face) whose both ends are selected, chained into loops. */
function selectedRims(m: WMesh, S: Set<number>): number[][] {
  const dir = new Set<string>();
  for (let t = 0; t < nTris(m); t++) for (let k = 0; k < 3; k++) dir.add(m.tris[t * 3 + k] + ">" + m.tris[t * 3 + (k + 1) % 3]);
  const next = new Map<number, number>();
  for (let t = 0; t < nTris(m); t++) for (let k = 0; k < 3; k++) {
    const a = m.tris[t * 3 + k], b = m.tris[t * 3 + (k + 1) % 3];
    if (!dir.has(b + ">" + a) && S.has(a) && S.has(b)) next.set(a, b);
  }
  const loops: number[][] = [];
  const used = new Set<number>();
  for (const start of next.keys()) {
    if (used.has(start)) continue;
    const loop: number[] = [];
    let v = start;
    while (!used.has(v) && next.has(v)) { used.add(v); loop.push(v); v = next.get(v)!; }
    if (v === start && loop.length >= 3) loops.push(loop);
  }
  return loops;
}

/** Close each selected hole: a fan from its middle, wound to face the same way as its rim. */
export function fill(m0: WMesh, S: Set<number>): StepResult {
  const loops = selectedRims(m0, S);
  if (!loops.length) return { mesh: m0, out: [], error: "fill needs a selected open rim (a hole's edge)" };
  const m = copy(m0);
  const out: number[] = [];
  for (const loop of loops) {
    let c: V3 = [0, 0, 0];
    for (const v of loop) c = add(c, P(m, v));
    const mid = addV(m, mul(c, 1 / loop.length));
    out.push(mid, ...loop);
    // The rim runs a->b in its face's winding, so the new face runs b->a.
    for (let i = 0; i < loop.length; i++) m.tris.push(loop[(i + 1) % loop.length], loop[i], mid);
  }
  return { mesh: m, out };
}

/** Join two selected rims with a band of faces. The rims may have different numbers of edges. */
export function bridge(m0: WMesh, S: Set<number>): StepResult {
  const loops = selectedRims(m0, S);
  if (loops.length !== 2) return { mesh: m0, out: [], error: "bridge needs exactly two selected open rims (found " + loops.length + ")" };
  const m = copy(m0);
  const [A, B0] = loops;
  // The second rim runs the other way round, facing the first; start it at the vertex nearest A[0].
  const Br = B0.slice().reverse();
  let off = 0, bd = Infinity;
  for (let j = 0; j < Br.length; j++) { const d = len(sub(P(m, Br[j]), P(m, A[0]))); if (d < bd) { bd = d; off = j; } }
  const B = Br.slice(off).concat(Br.slice(0, off));
  const n = A.length, k = B.length;
  let i = 0, j = 0;
  while (i < n || j < k) {
    const ai = A[i % n], ai1 = A[(i + 1) % n], bj = B[j % k], bj1 = B[(j + 1) % k];
    const takeA = j >= k || (i < n && len(sub(P(m, ai1), P(m, bj))) <= len(sub(P(m, bj1), P(m, ai))));
    if (takeA) { m.tris.push(ai1, ai, bj); i++; }
    else { m.tris.push(bj, bj1, ai); j++; }
  }
  return { mesh: m, out: [...A, ...B] };
}

/** Split every triangle along the marked edges (1, 2 or 3 of them), so a subdivided region joins its
 *  neighbours with no crack. */
function splitMarked(m: WMesh, mid: Map<string, number>): void {
  const old = m.tris;
  m.tris = [];
  for (let t = 0; t < old.length / 3; t++) {
    const v = [old[t * 3], old[t * 3 + 1], old[t * 3 + 2]];
    const ms = [0, 1, 2].map((k) => mid.get(ek(v[k], v[(k + 1) % 3])));
    const cnt = ms.filter((x) => x !== undefined).length;
    if (cnt === 0) { m.tris.push(v[0], v[1], v[2]); continue; }
    if (cnt === 3) {
      const [m0, m1, m2] = ms as number[];
      m.tris.push(v[0], m0, m2, m0, v[1], m1, m2, m1, v[2], m0, m1, m2);
      continue;
    }
    // Rotate so the marked edges are v0-v1 (and v1-v2 when two are marked).
    const r = cnt === 1 ? ms.findIndex((x) => x !== undefined) : (ms.findIndex((x) => x === undefined) + 1) % 3;
    const a = v[r], b = v[(r + 1) % 3], c = v[(r + 2) % 3];
    const mab = ms[r]!, mbc = ms[(r + 1) % 3];
    if (cnt === 1) { m.tris.push(a, mab, c, mab, b, c); continue; }
    // Two marked: v0-v1 and v1-v2.
    m.tris.push(a, mab, c, mab, b, mbc!, mab, mbc!, c);
  }
}

/** Each selected face into four; the faces beside them split to meet without a crack. */
export function subdivide(m0: WMesh, S: Set<number>): StepResult {
  const faces = selFaces(m0, S);
  if (!faces.length) return { mesh: m0, out: [], error: "subdivide needs selected faces" };
  const m = copy(m0);
  const mid = new Map<string, number>();
  for (const t of faces) for (let k = 0; k < 3; k++) {
    const a = m.tris[t * 3 + k], b = m.tris[t * 3 + (k + 1) % 3], key = ek(a, b);
    if (!mid.has(key)) mid.set(key, addV(m, mul(add(P(m, a), P(m, b)), 0.5)));
  }
  splitMarked(m, mid);
  return { mesh: m, out: [...S, ...mid.values()] };
}

/**
 * A loop cut through the ring of quads that crosses the selected edge, `cuts` new loops spaced
 * evenly. The triangles are paired into quads first (two triangles across their longest edge, when
 * they are nearly flat), as Blender's own quads would be.
 */
export function loopcut(m0: WMesh, S: Set<number>, cuts = 1): StepResult {
  const m = copy(m0);
  // the selected edge: two selected vertices that share a triangle
  let edge: [number, number] | null = null;
  for (let t = 0; t < nTris(m) && !edge; t++) for (let k = 0; k < 3 && !edge; k++) {
    const a = m.tris[t * 3 + k], b = m.tris[t * 3 + (k + 1) % 3];
    if (S.has(a) && S.has(b)) edge = [a, b];
  }
  if (!edge) return { mesh: m0, out: [], error: "loop cut needs a selected edge (two corners side by side)" };
  const quads = pairQuads(m);
  const byEdge = new Map<string, number[]>();
  quads.forEach((q, i) => { for (let k = 0; k < 4; k++) { const key = ek(q[k], q[(k + 1) % 4]); const l = byEdge.get(key); if (l) l.push(i); else byEdge.set(key, [i]); } });
  const opposite = (q: number[], a: number, b: number): [number, number] | null => {
    const i = q.indexOf(a), j = q.indexOf(b);
    if (i < 0 || j < 0) return null;
    // a->b is q[i]->q[i+1] or the reverse; the opposite edge, aligned the same way, is q[i+3]->q[i+2].
    if ((i + 1) % 4 === j) return [q[(i + 3) % 4], q[(i + 2) % 4]];
    if ((j + 1) % 4 === i) return [q[(i + 1) % 4], q[(i + 2) % 4]];
    return null;
  };
  // Walk both ways from the start edge, each way into its own quad.
  let closed = false;
  const walk = (first: number) => {
    const edges: Array<[number, number]> = [], qs: number[] = [];
    let cur: [number, number] = edge!, qi = first;
    const seen = new Set<number>();
    while (qi >= 0 && !seen.has(qi)) {
      seen.add(qi);
      const o = opposite(quads[qi], cur[0], cur[1]);
      if (!o) break;
      qs.push(qi);
      if (ek(o[0], o[1]) === ek(edge![0], edge![1])) { closed = true; break; }
      edges.push(o);
      const nextQ = (byEdge.get(ek(o[0], o[1])) || []).filter((q) => q !== qi);
      cur = o;
      qi = nextQ.length ? nextQ[0] : -1;
    }
    return { edges, qs };
  };
  const startQ = byEdge.get(ek(edge[0], edge[1])) || [];
  if (!startQ.length) return { mesh: m0, out: [], error: "no quads cross that edge: loop cut works on quad-like surfaces" };
  const fwd = walk(startQ[0]);
  const back = !closed && startQ.length > 1 ? walk(startQ[1]) : { edges: [], qs: [] };
  const ring: Array<[number, number]> = [...back.edges.slice().reverse(), edge, ...fwd.edges];
  const ringQuads = [...back.qs, ...fwd.qs];
  if (!ringQuads.length) return { mesh: m0, out: [], error: "no quads cross that edge: loop cut works on quad-like surfaces" };
  // New vertices on every ring edge, stored low index -> high index.
  const pts = new Map<string, number[]>();
  const out: number[] = [];
  for (const [x, y] of ring) {
    const key = ek(x, y);
    if (pts.has(key)) continue;
    const lo = Math.min(x, y), hi = Math.max(x, y);
    const list: number[] = [];
    for (let c = 1; c <= cuts; c++) { const t = c / (cuts + 1); const v = addV(m, add(mul(P(m, lo), 1 - t), mul(P(m, hi), t))); list.push(v); out.push(v); }
    pts.set(key, list);
  }
  const along = (x: number, y: number): number[] => { const l = pts.get(ek(x, y))!; return x < y ? l : l.slice().reverse(); };
  // Rebuild each ring quad as cuts+1 quads, in its own winding.
  const drop = new Set<number>();
  const keepTris: number[] = [];
  const quadTris = quadTriIndex(m, quads);
  for (const qi of ringQuads) for (const t of quadTris[qi]) drop.add(t);
  for (let t = 0; t < nTris(m); t++) if (!drop.has(t)) keepTris.push(m.tris[t * 3], m.tris[t * 3 + 1], m.tris[t * 3 + 2]);
  for (const qi of ringQuads) {
    const q = quads[qi];
    // find the two ring edges in this quad
    let k0 = -1;
    for (let k = 0; k < 4; k++) if (pts.has(ek(q[k], q[(k + 1) % 4])) && pts.has(ek(q[(k + 2) % 4], q[(k + 3) % 4]))) { k0 = k; break; }
    if (k0 < 0) continue;
    const a = q[k0], b = q[(k0 + 1) % 4], c = q[(k0 + 2) % 4], d = q[(k0 + 3) % 4];
    // a->b and c->d are the ring edges; d is across from a.
    const side1 = [a, ...along(a, b), b];
    const side2 = [d, ...along(d, c), c];
    for (let s = 0; s + 1 < side1.length; s++) {
      const p0 = side1[s], p1 = side1[s + 1], p2 = side2[s + 1], p3 = side2[s];
      keepTris.push(p0, p1, p2, p0, p2, p3);
    }
  }
  m.tris = keepTris;
  return { mesh: m, out };
}

/** Triangles paired into quads: across a shared edge, both nearly flat (within 30 degrees), the
 *  longer shared edge first, as a quad mesh triangulated is. Unpaired triangles are left out. */
function pairQuads(m: WMesh): number[][] {
  const nt = nTris(m);
  const fn = Array.from({ length: nt }, (_, t) => norm(triNormal(m, t)));
  const byEdge = new Map<string, number[]>();
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) { const key = ek(m.tris[t * 3 + k], m.tris[t * 3 + (k + 1) % 3]); const l = byEdge.get(key); if (l) l.push(t); else byEdge.set(key, [t]); }
  const cands: Array<{ score: number; t: number; u: number; a: number; b: number }> = [];
  for (const [key, ts] of byEdge) {
    if (ts.length !== 2) continue;
    const [a, b] = key.split(",").map(Number);
    const [t, u] = ts;
    if (dot(fn[t], fn[u]) < Math.cos(Math.PI / 6)) continue;
    cands.push({ score: len(sub(P(m, a), P(m, b))), t, u, a, b });
  }
  cands.sort((x, y) => y.score - x.score);
  const used = new Set<number>();
  const quads: number[][] = [];
  for (const c of cands) {
    if (used.has(c.t) || used.has(c.u)) continue;
    // The quad in t's winding: t = (a, b, x) rotated so the shared edge is its last edge.
    const tv = [m.tris[c.t * 3], m.tris[c.t * 3 + 1], m.tris[c.t * 3 + 2]];
    const uv = [m.tris[c.u * 3], m.tris[c.u * 3 + 1], m.tris[c.u * 3 + 2]];
    const x = tv.find((v) => v !== c.a && v !== c.b)!, y = uv.find((v) => v !== c.a && v !== c.b)!;
    const i = tv.indexOf(x);
    const q = [x, tv[(i + 1) % 3], y, tv[(i + 2) % 3]];
    quads.push(q);
    used.add(c.t); used.add(c.u);
  }
  return quads;
}

function quadTriIndex(m: WMesh, quads: number[][]): number[][] {
  const byKey = new Map<string, number>();
  for (let t = 0; t < nTris(m); t++) byKey.set([m.tris[t * 3], m.tris[t * 3 + 1], m.tris[t * 3 + 2]].sort((a, b) => a - b).join(","), t);
  return quads.map((q) => {
    const out: number[] = [];
    for (const tri of [[q[0], q[1], q[2]], [q[0], q[2], q[3]], [q[0], q[1], q[3]], [q[1], q[2], q[3]]]) {
      const t = byKey.get(tri.slice().sort((a, b) => a - b).join(","));
      if (t !== undefined && !out.includes(t)) out.push(t);
    }
    return out;
  });
}

/** Cut along a plane: every crossed triangle (the selected ones, or all with nothing selected) is
 *  split where the plane crosses it. The new vertices are left selected. */
export function knife(m0: WMesh, S: Set<number>, point: V3, normal: V3): StepResult {
  const n = norm(normal);
  if (!len(n)) return { mesh: m0, out: [], error: "knife needs a plane normal" };
  const m = copy(m0);
  const scope = selFaces(m, S);
  const inScope = new Set(scope.length ? scope : Array.from({ length: nTris(m) }, (_, t) => t));
  const eps = 1e-7;
  const sd = (v: number) => dot(sub(P(m, v), point), n);
  const mid = new Map<string, number>();
  for (const t of inScope) for (let k = 0; k < 3; k++) {
    const a = m.tris[t * 3 + k], b = m.tris[t * 3 + (k + 1) % 3];
    const da = sd(a), db = sd(b);
    if ((da > eps && db < -eps) || (da < -eps && db > eps)) {
      const key = ek(a, b);
      if (!mid.has(key)) { const s = da / (da - db); mid.set(key, addV(m, add(P(m, a), mul(sub(P(m, b), P(m, a)), s)))); }
    }
  }
  if (!mid.size) return { mesh: m0, out: [], error: "the plane crosses none of those faces" };
  // Only triangles in scope are split; a neighbour out of scope that shares a cut edge is split too,
  // so the surface stays closed.
  splitMarked(m, mid);
  return { mesh: m, out: [...mid.values()] };
}

/** Move corners: each `at` place to its `to` place. */
export function move(m0: WMesh, moves: { at: V3; to: V3 }[]): StepResult {
  const m = copy(m0);
  const out: number[] = [];
  for (const mv of moves) {
    const v = [...findVerts(m, [mv.at])][0];
    if (v === undefined) continue;
    m.pos[v * 3] = mv.to[0]; m.pos[v * 3 + 1] = mv.to[1]; m.pos[v * 3 + 2] = mv.to[2];
    out.push(v);
  }
  if (!out.length && moves.length) return { mesh: m0, out: [], error: "none of the moved corners is in this mesh any more" };
  return { mesh: m, out };
}

/**
 * LONGEST-EDGE CLOSURE: every triangle with a marked edge has its longest edge marked too, repeated
 * until nothing changes. Splitting a triangle across a short edge alone makes a sliver, and slivers
 * split again make worse ones; through the longest edge, the halves keep their shape (Rivara's
 * bisection). New midpoints are made for the edges this adds.
 */
function closeLongest(m: WMesh, mid: Map<string, number>): void {
  for (let guard = 0; guard < 64; guard++) {
    let added = 0;
    for (let t = 0; t < nTris(m); t++) {
      const v = [m.tris[t * 3], m.tris[t * 3 + 1], m.tris[t * 3 + 2]];
      const keys = [0, 1, 2].map((k) => ek(v[k], v[(k + 1) % 3]));
      if (!keys.some((k) => mid.has(k))) continue;
      let lk = 0, ll = -1;
      for (let k = 0; k < 3; k++) { const l = len(sub(P(m, v[k]), P(m, v[(k + 1) % 3]))); if (l > ll) { ll = l; lk = k; } }
      if (mid.has(keys[lk])) continue;
      mid.set(keys[lk], addV(m, mul(add(P(m, v[lk]), P(m, v[(lk + 1) % 3])), 0.5)));
      added++;
    }
    if (!added) return;
  }
}

/**
 * Split the long edges near a path until none near it is longer than `maxEdge`, so a brush has
 * vertices to move where it works and nowhere else. A whole-mesh densify of a large model for a
 * small brush would multiply it many times over; this refines the band the stroke touches.
 */
export function refineNear(m0: WMesh, path: V3[], reach: number, maxEdge: number, maxTris = 400000): WMesh {
  const m = copy(m0);
  if (!path.length || !(maxEdge > 0)) return m;
  const near = (p: V3) => {
    for (let i = 0; i < path.length; i++) {
      const a = path[i], b = path[Math.min(i + 1, path.length - 1)];
      const ab = sub(b, a), l2 = dot(ab, ab);
      const t = l2 > 1e-18 ? Math.max(0, Math.min(1, dot(sub(p, a), ab) / l2)) : 0;
      if (len(sub(p, add(a, mul(ab, t)))) <= reach) return true;
    }
    return false;
  };
  for (let pass = 0; pass < 12 && nTris(m) < maxTris; pass++) {
    const mid = new Map<string, number>();
    for (let t = 0; t < nTris(m); t++) for (let k = 0; k < 3; k++) {
      const a = m.tris[t * 3 + k], b = m.tris[t * 3 + (k + 1) % 3], key = ek(a, b);
      if (mid.has(key)) continue;
      const pa = P(m, a), pb = P(m, b);
      if (len(sub(pa, pb)) <= maxEdge) continue;
      const c = mul(add(pa, pb), 0.5);
      if (!near(c) && !near(pa) && !near(pb)) continue;
      mid.set(key, addV(m, c));
    }
    if (!mid.size) break;
    closeLongest(m, mid);
    splitMarked(m, mid);
  }
  return m;
}

/** One brush stroke (sculpt.ts) along `path`, after refining the surface under it. */
export function sculptStep(m0: WMesh, path: V3[], args: Record<string, any> = {}): StepResult {
  const kind = String(args.brush || "clay") as Stroke["kind"];
  if (!SCULPT_BRUSHES.includes(kind)) return { mesh: m0, out: [], error: "unknown brush " + kind };
  const radius = num(args, "radius", 0);
  if (!(radius > 0)) return { mesh: m0, out: [], error: "a sculpt stroke needs a radius" };
  if (!path.length) return { mesh: m0, out: [], error: "a sculpt stroke needs a path on the surface" };
  const m = args.refine === false ? copy(m0) : refineNear(m0, path, radius * 1.3, radius / 3);
  const stroke: Stroke = { kind, radius };
  if (kind === "move") {
    // A drag: the region under the start goes to the end.
    stroke.at = path[0];
    stroke.dir = sub(path[path.length - 1], path[0]);
  } else if (path.length === 1) stroke.at = path[0];
  else stroke.path = path;
  if (typeof args.depth === "number" && isFinite(args.depth)) stroke.depth = args.depth;
  if (typeof args.strength === "number" && isFinite(args.strength)) stroke.strength = args.strength;
  if (args.mirror === "x" || args.mirror === "y" || args.mirror === "z") stroke.mirror = args.mirror;
  if (kind !== "move" && path.length > 2) stroke.curve = true;
  try {
    const pos = sculptArrays(new Float32Array(m.pos), new Uint32Array(m.tris), [stroke]);
    const out: WMesh = { pos: Array.from(pos), tris: m.tris };
    return { mesh: out, out: [] };
  } catch (e: any) {
    return { mesh: m0, out: [], error: String(e?.message || e) };
  }
}

/** Split every edge longer than `maxEdge`, everywhere: a mesh made fine enough to sculpt anywhere. */
export function densify(m0: WMesh, maxEdge: number, maxTris = 400000): StepResult {
  if (!(maxEdge > 0)) return { mesh: m0, out: [], error: "densify needs a positive edge length" };
  const m = copy(m0);
  for (let pass = 0; pass < 16 && nTris(m) < maxTris; pass++) {
    const mid = new Map<string, number>();
    for (let t = 0; t < nTris(m); t++) for (let k = 0; k < 3; k++) {
      const a = m.tris[t * 3 + k], b = m.tris[t * 3 + (k + 1) % 3], key = ek(a, b);
      if (!mid.has(key) && len(sub(P(m, a), P(m, b))) > maxEdge) mid.set(key, addV(m, mul(add(P(m, a), P(m, b)), 0.5)));
    }
    if (!mid.size) break;
    closeLongest(m, mid);
    splitMarked(m, mid);
  }
  return { mesh: m, out: [] };
}

// ------------------------------------------------------------------ one step, by name

const num = (a: Record<string, any> | undefined, k: string, d: number) => (a && typeof a[k] === "number" && isFinite(a[k]) ? a[k] : d);
const v3 = (a: Record<string, any> | undefined, k: string): V3 | null => {
  const s = a?.[k];
  if (typeof s !== "string") return null;
  const p = s.split(",").map(Number);
  return p.length === 3 && p.every((x) => isFinite(x)) ? (p as V3) : null;
};

export function runStep(m: WMesh, step: TopoStep): StepResult {
  if (step.op === "move") return move(m, step.moves || []);
  if (step.op === "sculpt") return sculptStep(m, step.path || [], step.args);
  if (step.op === "densify") return densify(m, num(step.args, "edge", 0));
  const S = findVerts(m, step.sel || []);
  if ((step.sel || []).length && S.size < Math.min(1, step.sel.length)) return { mesh: m, out: [], error: "the selected corners are not in this mesh any more" };
  const a = step.args;
  switch (step.op) {
    case "extrude": return extrude(m, S, num(a, "distance", 0.1));
    case "inset": return inset(m, S, num(a, "thickness", 0.02));
    case "loopcut": return loopcut(m, S, Math.max(1, Math.min(16, Math.round(num(a, "cuts", 1)))));
    case "merge": return merge(m, S);
    case "delete": return remove(m, S);
    case "fill": return fill(m, S);
    case "bridge": return bridge(m, S);
    case "subdivide": return subdivide(m, S);
    case "knife": {
      const p = v3(a, "point"), n = v3(a, "normal");
      if (!p || !n) return { mesh: m, out: [], error: "knife needs args.point and args.normal as \"x,y,z\"" };
      return knife(m, S, p, n);
    }
    default: return { mesh: m, out: [], error: "unknown step " + step.op };
  }
}

/** Every step for one mesh, in order, on its positions and index. A step that cannot run is
 *  skipped and named; the ones after it still run. */
export function runSteps(pos: ArrayLike<number>, idx: ArrayLike<number> | null, steps: TopoStep[]):
  { pos: Float32Array; normal: Float32Array; tris: number; errors: string[]; last: number[]; mesh: WMesh } {
  let m = weld(pos, idx);
  const errors: string[] = [];
  let last: number[] = [];
  for (const s of steps) {
    if (s.off) continue;
    const r = runStep(m, s);
    if (r.error) { errors.push(s.op + ": " + r.error); continue; }
    m = r.mesh;
    last = r.out;
  }
  const u = unweld(m);
  return { pos: u.pos, normal: u.normal, tris: nTris(m), errors, last, mesh: m };
}

/** Steps as they come from a file: only real ops, real numbers and real places. */
export function cleanSteps(raw: any): TopoStep[] {
  const out: TopoStep[] = [];
  const isV3 = (v: any) => Array.isArray(v) && v.length >= 3 && v.slice(0, 3).every((x: any) => typeof x === "number" && isFinite(x));
  for (const s of Array.isArray(raw) ? raw : []) {
    if (!s || typeof s.mesh !== "string" || !TOPO_OPS.includes(s.op)) continue;
    const step: TopoStep = { mesh: s.mesh, op: s.op, sel: (Array.isArray(s.sel) ? s.sel : []).filter(isV3).map((v: any) => v.slice(0, 3) as V3) };
    if (s.args && typeof s.args === "object") {
      step.args = {};
      for (const [k, v] of Object.entries(s.args)) if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") step.args[k] = v as any;
    }
    if (s.op === "move") step.moves = (Array.isArray(s.moves) ? s.moves : []).filter((mv: any) => mv && isV3(mv.at) && isV3(mv.to))
      .map((mv: any) => ({ at: mv.at.slice(0, 3) as V3, to: mv.to.slice(0, 3) as V3 }));
    if (s.op === "sculpt") step.path = (Array.isArray(s.path) ? s.path : []).filter(isV3).map((v: any) => v.slice(0, 3) as V3);
    if (s.off === true) step.off = true;
    out.push(step);
  }
  return out;
}
