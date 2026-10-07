// The modelling operations that separate a game-ready asset from a pile of primitives, on plain
// arrays, for both engines and for node.
//
// These are the four tools Blender's quality rests on and the Studio did not have: Catmull-Clark
// subdivision with creases (smooth surfaces from quads, hard edges kept), bevel (rounded edges that
// catch light), UV unwrap with seams and packing (textures that fit), and baking (detail from a
// high-poly or from occlusion written into maps). Beside them: bone-heat skin weights, a voxel
// remesh, and a relax pass — the sculptor's clean-up.
//
// Everything works on triangles in and triangles out, because that is what both engines hold.
// Where an algorithm needs polygons — subdivision and bevel both do — `quadsFromTris` recovers the
// quads a procedural mesh was built from first, pairing triangles across their most planar shared
// edge. Nothing here imports an engine.

import { isosurfaceArrays, weldArrays, type V3 } from "./ops";

// ------------------------------------------------------------------ small vector helpers
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: V3): V3 => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const at = (pos: ArrayLike<number>, i: number): V3 => [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]];
const DEG = 180 / Math.PI;

/** Newell's method: a robust normal for any polygon, planar or not. */
function polyNormal(pts: V3[]): V3 {
  const n: V3 = [0, 0, 0];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n[0] += (a[1] - b[1]) * (a[2] + b[2]);
    n[1] += (a[2] - b[2]) * (a[0] + b[0]);
    n[2] += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return norm(n);
}

function centroid(pts: V3[]): V3 {
  const c: V3 = [0, 0, 0];
  for (const p of pts) { c[0] += p[0]; c[1] += p[1]; c[2] += p[2]; }
  return mul(c, 1 / Math.max(1, pts.length));
}

// ------------------------------------------------------------------ polygons
export interface PolyMesh { pos: Float32Array; faces: number[][] }

/**
 * Recover quads from triangle pairs. Two triangles sharing an edge become one quad when they are
 * nearly coplanar (within `angle` degrees) and the quad is convex; pairs are taken most-planar
 * first, and the flattest, squarest candidates win. Leftover triangles stay triangles.
 */
export function quadsFromTris(pos: Float32Array, idx: Uint32Array, angle = 40): PolyMesh {
  const nt = idx.length / 3;
  const normals: V3[] = new Array(nt);
  for (let t = 0; t < nt; t++) normals[t] = polyNormal([at(pos, idx[t * 3]), at(pos, idx[t * 3 + 1]), at(pos, idx[t * 3 + 2])]);
  const edges = new Map<string, number[]>();
  for (let t = 0; t < nt; t++) for (let e = 0; e < 3; e++) {
    const a = idx[t * 3 + e], b = idx[t * 3 + (e + 1) % 3];
    const k = a < b ? a + "," + b : b + "," + a;
    const l = edges.get(k); if (l) l.push(t); else edges.set(k, [t]);
  }
  const cosLimit = Math.cos((angle * Math.PI) / 180);
  const cands: Array<{ score: number; a: number; b: number; quad: number[] }> = [];
  for (const [k, tris] of edges) {
    if (tris.length !== 2) continue;
    const [ta, tb] = tris;
    const d = dot(normals[ta], normals[tb]);
    if (d < cosLimit) continue;
    const [ea, eb] = k.split(",").map(Number);
    // The quad, in triangle A's winding: opposite of A, then the shared edge as A walks it, then
    // opposite of B.
    const A = [idx[ta * 3], idx[ta * 3 + 1], idx[ta * 3 + 2]], B = [idx[tb * 3], idx[tb * 3 + 1], idx[tb * 3 + 2]];
    const oa = A.find((v) => v !== ea && v !== eb)!, ob = B.find((v) => v !== ea && v !== eb)!;
    const ia = A.indexOf(oa);
    const va = A[(ia + 1) % 3], vb = A[(ia + 2) % 3];   // A = (oa, va, vb)
    const quad = [oa, va, ob, vb];
    const P = quad.map((v) => at(pos, v));
    const n = normals[ta];
    let convex = true, shape = 0;
    for (let i = 0; i < 4; i++) {
      const p0 = P[(i + 3) % 4], p1 = P[i], p2 = P[(i + 1) % 4];
      const e1 = norm(sub(p1, p0)), e2 = norm(sub(p2, p1));
      if (dot(cross(e1, e2), n) <= 1e-9) { convex = false; break; }
      shape += Math.abs(dot(e1, e2));   // 0 for a right angle
    }
    if (!convex) continue;
    cands.push({ score: (1 - d) * 4 + shape, a: ta, b: tb, quad });
  }
  cands.sort((p, q) => p.score - q.score);
  const used = new Uint8Array(nt);
  const faces: number[][] = [];
  for (const c of cands) {
    if (used[c.a] || used[c.b]) continue;
    used[c.a] = used[c.b] = 1;
    faces.push(c.quad);
  }
  for (let t = 0; t < nt; t++) if (!used[t]) faces.push([idx[t * 3], idx[t * 3 + 1], idx[t * 3 + 2]]);
  return { pos, faces };
}

/** Polygons back to triangles: a quad along its shorter diagonal, anything else as a fan. */
export function triangulate(faces: number[][], pos: ArrayLike<number>): Uint32Array {
  const out: number[] = [];
  for (const f of faces) {
    if (f.length < 3) continue;
    if (f.length === 3) { out.push(f[0], f[1], f[2]); continue; }
    if (f.length === 4) {
      const d02 = len(sub(at(pos, f[0]), at(pos, f[2]))), d13 = len(sub(at(pos, f[1]), at(pos, f[3])));
      if (d02 <= d13) out.push(f[0], f[1], f[2], f[0], f[2], f[3]);
      else out.push(f[1], f[2], f[3], f[1], f[3], f[0]);
      continue;
    }
    for (let i = 1; i + 1 < f.length; i++) out.push(f[0], f[i], f[i + 1]);
  }
  return Uint32Array.from(out);
}

interface Edge { a: number; b: number; faces: number[]; sharp: number }

/** The edge table of a polygon mesh: each undirected edge, the faces on it, and its sharpness. */
function edgesOf(faces: number[][]): { list: Edge[]; byKey: Map<string, number> } {
  const list: Edge[] = [];
  const byKey = new Map<string, number>();
  faces.forEach((f, fi) => {
    for (let i = 0; i < f.length; i++) {
      const a = f[i], b = f[(i + 1) % f.length];
      const k = a < b ? a + "," + b : b + "," + a;
      let e = byKey.get(k);
      if (e === undefined) { e = list.length; byKey.set(k, e); list.push({ a: Math.min(a, b), b: Math.max(a, b), faces: [], sharp: 0 }); }
      list[e].faces.push(fi);
    }
  });
  return { list, byKey };
}

const dihedral = (n0: V3, n1: V3) => Math.acos(Math.max(-1, Math.min(1, dot(n0, n1)))) * DEG;

// ------------------------------------------------------------------ Catmull-Clark
/**
 * Catmull-Clark subdivision on the recovered quads, with creases.
 *
 * The smooth rules are Catmull and Clark's; the sharp ones are Pixar's: an edge with sharpness
 * stays a crease (its edge point is the midpoint, its vertices follow the curve rule) for that
 * many levels, and a fraction blends. Boundaries are creases with a corner at every valence-two
 * vertex, so an open sheet keeps its outline. Creases are found by angle — an edge sharper than
 * `creaseAngle` degrees — which is how a procedural hard-surface piece already says where its
 * corners are. `sharpness` Infinity keeps them forever; 1 softens them after the first level.
 */
export function subdivideCC(pos: Float32Array, idx: Uint32Array, levels = 1,
  opts: { creaseAngle?: number; sharpness?: number; quadAngle?: number; weldTol?: number } = {}): { pos: Float32Array; idx: Uint32Array } {
  const { creaseAngle = 0, sharpness = Infinity, quadAngle = 40, weldTol = 1e-5 } = opts;
  const w = weldArrays(pos, idx, weldTol);
  let V: V3[] = []; for (let i = 0; i < w.pos.length; i += 3) V.push([w.pos[i], w.pos[i + 1], w.pos[i + 2]]);
  let F = quadsFromTris(w.pos, w.idx, quadAngle).faces;
  // Sharpness per edge, at level 0: by angle, and every boundary.
  let { list: E, byKey } = edgesOf(F);
  const fn = F.map((f) => polyNormal(f.map((v) => V[v])));
  for (const e of E) {
    if (e.faces.length !== 2) e.sharp = Infinity;
    else if (creaseAngle > 0 && dihedral(fn[e.faces[0]], fn[e.faces[1]]) > creaseAngle) e.sharp = sharpness;
  }
  for (let level = 0; level < Math.max(0, Math.round(levels)); level++) {
    const nV = V.length, nE = E.length;
    const facePt: V3[] = F.map((f) => centroid(f.map((v) => V[v])));
    // Edge points.
    const edgePt: V3[] = new Array(nE);
    for (let ei = 0; ei < nE; ei++) {
      const e = E[ei];
      const mid = mul(add(V[e.a], V[e.b]), 0.5);
      const t = Math.min(1, Math.max(0, e.sharp));
      if (e.faces.length === 2 && t < 1) {
        const smooth = mul(add(add(V[e.a], V[e.b]), add(facePt[e.faces[0]], facePt[e.faces[1]])), 0.25);
        edgePt[ei] = t <= 0 ? smooth : add(mul(smooth, 1 - t), mul(mid, t));
      } else edgePt[ei] = mid;
    }
    // Vertex points.
    const vEdges: number[][] = Array.from({ length: nV }, () => []);
    const vFaces: number[][] = Array.from({ length: nV }, () => []);
    E.forEach((e, ei) => { vEdges[e.a].push(ei); vEdges[e.b].push(ei); });
    F.forEach((f, fi) => { for (const v of f) vFaces[v].push(fi); });
    const vertPt: V3[] = new Array(nV);
    for (let v = 0; v < nV; v++) {
      const P = V[v];
      const n = vEdges[v].length;
      const sharpE = vEdges[v].filter((ei) => E[ei].sharp > 0);
      if (n < 2) { vertPt[v] = P; continue; }
      // Smooth rule.
      let Q: V3 = [0, 0, 0], R: V3 = [0, 0, 0];
      for (const fi of vFaces[v]) Q = add(Q, facePt[fi]);
      Q = mul(Q, 1 / Math.max(1, vFaces[v].length));
      for (const ei of vEdges[v]) R = add(R, mul(add(V[E[ei].a], V[E[ei].b]), 0.5));
      R = mul(R, 1 / n);
      const smooth = mul(add(add(Q, mul(R, 2)), mul(P, n - 3)), 1 / n);
      let creased: V3 = P;
      let t = 0;
      if (sharpE.length >= 3 || (n === 2 && sharpE.length === 2)) { creased = P; t = 1; }   // a corner
      else if (sharpE.length === 2) {
        const [e1, e2] = sharpE.map((ei) => (E[ei].a === v ? V[E[ei].b] : V[E[ei].a]));
        creased = mul(add(add(e1, e2), mul(P, 6)), 1 / 8);
        t = Math.min(1, Math.max(0, (E[sharpE[0]].sharp + E[sharpE[1]].sharp) / 2));
      }
      vertPt[v] = t <= 0 ? smooth : t >= 1 ? creased : add(mul(smooth, 1 - t), mul(creased, t));
    }
    // New topology: for each face corner a quad of (vertex, next edge, face, previous edge).
    const NV: V3[] = vertPt.concat(edgePt, facePt);
    const eBase = nV, fBase = nV + nE;
    const NF: number[][] = [];
    const edgeIndex = (a: number, b: number) => byKey.get(a < b ? a + "," + b : b + "," + a)!;
    // Sharpness carries to the two halves of an edge; new edges from the face point are smooth.
    const nextSharp = new Map<string, number>();
    F.forEach((f, fi) => {
      const m = f.length;
      for (let i = 0; i < m; i++) {
        const v = f[i], vn = f[(i + 1) % m], vp = f[(i + m - 1) % m];
        const eNext = edgeIndex(v, vn), ePrev = edgeIndex(vp, v);
        NF.push([v, eBase + eNext, fBase + fi, eBase + ePrev]);
        for (const [x, y, s] of [[v, eBase + eNext, E[eNext].sharp], [v, eBase + ePrev, E[ePrev].sharp]] as Array<[number, number, number]>) {
          const k = x < y ? x + "," + y : y + "," + x;
          nextSharp.set(k, s === Infinity ? Infinity : Math.max(0, s - 1));
        }
      }
    });
    V = NV; F = NF;
    ({ list: E, byKey } = edgesOf(F));
    for (const e of E) {
      const k = e.a + "," + e.b;
      if (e.faces.length !== 2) e.sharp = Infinity;
      else e.sharp = nextSharp.get(k) ?? 0;
    }
  }
  const outPos = new Float32Array(V.length * 3);
  V.forEach((p, i) => { outPos[i * 3] = p[0]; outPos[i * 3 + 1] = p[1]; outPos[i * 3 + 2] = p[2]; });
  return { pos: outPos, idx: triangulate(F, outPos) };
}

// ------------------------------------------------------------------ bevel
/**
 * Round the sharp edges. Every face is inset along its bevelled edges, each bevelled edge gets a
 * strip of `segments` quads on a circular arc tangent to both faces, and the holes left at the
 * vertices are filled. Edges are bevelled when their dihedral angle exceeds `angle` degrees, so a
 * flat sheet of quads is left alone and a box gets its twelve. The width is clamped so no face can
 * turn inside out. Meant for closed meshes; an open boundary is kept, not bevelled.
 */
export function bevelArrays(pos: Float32Array, idx: Uint32Array,
  opts: { width?: number; segments?: number; angle?: number; quadAngle?: number; weldTol?: number } = {}): { pos: Float32Array; idx: Uint32Array } {
  const { width = 0.05, segments = 3, angle = 30, quadAngle = 40, weldTol = 1e-5 } = opts;
  const S = Math.max(1, Math.round(segments));
  const w0 = weldArrays(pos, idx, weldTol);
  const V: V3[] = []; for (let i = 0; i < w0.pos.length; i += 3) V.push([w0.pos[i], w0.pos[i + 1], w0.pos[i + 2]]);
  const F = quadsFromTris(w0.pos, w0.idx, quadAngle).faces;
  const { list: E, byKey } = edgesOf(F);
  const fn = F.map((f) => polyNormal(f.map((v) => V[v])));
  const key = (a: number, b: number) => (a < b ? a + "," + b : b + "," + a);
  const bevelled = new Uint8Array(E.length);
  let any = false;
  E.forEach((e, ei) => { if (e.faces.length === 2 && dihedral(fn[e.faces[0]], fn[e.faces[1]]) > angle) { bevelled[ei] = 1; any = true; } });
  if (!any) return { pos: w0.pos, idx: w0.idx };
  // Clamp: no inset may cross the middle of its face.
  let wmax = width;
  F.forEach((f) => {
    const c = centroid(f.map((v) => V[v]));
    for (let i = 0; i < f.length; i++) {
      if (!bevelled[byKey.get(key(f[i], f[(i + 1) % f.length]))!]) continue;
      const a = V[f[i]], b = V[f[(i + 1) % f.length]];
      const ab = sub(b, a), t = Math.max(0, Math.min(1, dot(sub(c, a), ab) / (dot(ab, ab) || 1)));
      wmax = Math.min(wmax, 0.45 * len(sub(c, add(a, mul(ab, t)))));
    }
  });
  const w = Math.max(1e-6, wmax);

  const NV: V3[] = [];
  const push = (p: V3) => { NV.push(p); return NV.length - 1; };
  const NF: number[][] = [];
  // Inset faces: offset the bevelled edges inward in the face plane; each corner is where its two
  // (possibly offset) edge lines meet.
  const corner: number[][] = F.map(() => []);
  F.forEach((f, fi) => {
    const m = f.length, n = fn[fi];
    const P = f.map((v) => V[v]);
    const lines = P.map((a, i) => {
      const b = P[(i + 1) % m];
      const d = norm(sub(b, a));
      const inward = norm(cross(n, d));      // points into the face for a CCW face
      const off = bevelled[byKey.get(key(f[i], f[(i + 1) % m]))!] ? w : 0;
      return { p: add(a, mul(inward, off)), d };
    });
    for (let i = 0; i < m; i++) {
      const L1 = lines[(i + m - 1) % m], L2 = lines[i];
      // Intersect the two lines in the face plane: p1 + s*d1 = p2 + t*d2.
      const c = cross(L1.d, L2.d);
      const cl = dot(c, c);
      let pt: V3;
      if (cl < 1e-14) pt = L2.p;   // parallel: the corner sits on the second line's start
      else {
        const s = dot(cross(sub(L2.p, L1.p), L2.d), c) / cl;
        pt = add(L1.p, mul(L1.d, s));
      }
      corner[fi].push(push(pt));
    }
    NF.push(corner[fi].slice());
  });
  // Strips along each bevelled edge.
  const slerp = (a: V3, b: V3, t: number): V3 => {
    const d = Math.max(-1, Math.min(1, dot(a, b)));
    const th = Math.acos(d);
    if (th < 1e-6) return a;
    const s = Math.sin(th);
    return norm(add(mul(a, Math.sin((1 - t) * th) / s), mul(b, Math.sin(t * th) / s)));
  };
  E.forEach((e, ei) => {
    if (!bevelled[ei]) return;
    const [fA0, fB0] = e.faces;
    // A is the face that walks the edge a->b; B walks b->a.
    const walks = (fi: number, a: number, b: number) => { const f = F[fi]; const i = f.indexOf(a); return i >= 0 && f[(i + 1) % f.length] === b; };
    const fA = walks(fA0, e.a, e.b) ? fA0 : fB0, fB = fA === fA0 ? fB0 : fA0;
    const iA0 = F[fA].indexOf(e.a), iA1 = F[fA].indexOf(e.b), iB0 = F[fB].indexOf(e.a), iB1 = F[fB].indexOf(e.b);
    const a0 = corner[fA][iA0], a1 = corner[fA][iA1], b0 = corner[fB][iB0], b1 = corner[fB][iB1];
    const rows: number[][] = [[a0, a1]];
    for (let k = 1; k < S; k++) {
      const t = k / S;
      const row: number[] = [];
      for (const [ai, bi] of [[a0, b0], [a1, b1]]) {
        const pa = NV[ai], pb = NV[bi];
        const nA = fn[fA], nB = fn[fB];
        const phi = Math.acos(Math.max(-1, Math.min(1, dot(nA, nB))));
        const chord = len(sub(pa, pb));
        const r = phi > 1e-6 ? chord / (2 * Math.sin(phi / 2)) : 0;
        const c = sub(pa, mul(nA, r));
        row.push(push(add(c, mul(slerp(nA, nB, t), r))));
      }
      rows.push(row);
    }
    rows.push([b0, b1]);
    for (let k = 0; k + 1 < rows.length; k++) NF.push([rows[k][1], rows[k][0], rows[k + 1][0], rows[k + 1][1]]);
  });
  // Fill the holes the strips leave at the vertices: small boundary loops, fanned from their centre.
  const { list: E2 } = edgesOf(NF);
  const dirOf = new Map<string, [number, number]>();
  NF.forEach((f) => { for (let i = 0; i < f.length; i++) { const a = f[i], b = f[(i + 1) % f.length]; dirOf.set(key(a, b), [a, b]); } });
  const open = E2.filter((e) => e.faces.length === 1);
  const next = new Map<number, number>();
  for (const e of open) { const d = dirOf.get(key(e.a, e.b))!; next.set(d[1], d[0]); }   // walk against the faces' direction
  const seen = new Set<number>();
  const limit = 8 * w * (S + 1) + 1e-6;
  for (const start of next.keys()) {
    if (seen.has(start)) continue;
    const loop: number[] = [];
    let v = start;
    for (let guard = 0; guard < 10000 && !seen.has(v) && next.has(v); guard++) { seen.add(v); loop.push(v); v = next.get(v)!; }
    if (loop.length < 3) continue;
    let per = 0;
    for (let i = 0; i < loop.length; i++) per += len(sub(NV[loop[i]], NV[loop[(i + 1) % loop.length]]));
    if (per > limit) continue;   // an open boundary of the mesh, not a hole the bevel made
    const c = push(centroid(loop.map((i) => NV[i])));
    for (let i = 0; i < loop.length; i++) NF.push([loop[i], loop[(i + 1) % loop.length], c]);
  }
  const outPos = new Float32Array(NV.length * 3);
  NV.forEach((p, i) => { outPos[i * 3] = p[0]; outPos[i * 3 + 1] = p[1]; outPos[i * 3 + 2] = p[2]; });
  const tri = triangulate(NF, outPos);
  const w1 = weldArrays(outPos, tri, weldTol);
  return { pos: w1.pos, idx: w1.idx };
}

// ------------------------------------------------------------------ vertex normals
/** Area-weighted vertex normals, for the bakes and the curvature. */
export function vertexNormalsOf(pos: Float32Array, idx: Uint32Array): Float32Array {
  const n = new Float32Array(pos.length);
  for (let t = 0; t < idx.length; t += 3) {
    const a = idx[t], b = idx[t + 1], c = idx[t + 2];
    const A = at(pos, a), B = at(pos, b), C = at(pos, c);
    const fn = cross(sub(B, A), sub(C, A));   // twice the area, along the normal
    for (const v of [a, b, c]) { n[v * 3] += fn[0]; n[v * 3 + 1] += fn[1]; n[v * 3 + 2] += fn[2]; }
  }
  for (let i = 0; i < n.length; i += 3) {
    const l = Math.hypot(n[i], n[i + 1], n[i + 2]) || 1;
    n[i] /= l; n[i + 1] /= l; n[i + 2] /= l;
  }
  return n;
}

// ------------------------------------------------------------------ BVH
//
// A bounding volume hierarchy over triangles, for the rays the bakes and the heat weights cast and
// the nearest-point queries the remesh makes. Median split on the widest axis, eight triangles a
// leaf, flat typed arrays so it is cheap to build and to walk.

export interface BVH {
  pos: Float32Array; idx: Uint32Array;
  min: Float32Array; max: Float32Array; left: Int32Array; right: Int32Array; start: Int32Array; count: Int32Array;
  order: Uint32Array; n: number;
}

export function buildBVH(pos: Float32Array, idx: Uint32Array): BVH {
  const nt = idx.length / 3;
  const cen = new Float32Array(nt * 3);
  const tmin = new Float32Array(nt * 3), tmax = new Float32Array(nt * 3);
  for (let t = 0; t < nt; t++) {
    for (let k = 0; k < 3; k++) {
      const a = pos[idx[t * 3] * 3 + k], b = pos[idx[t * 3 + 1] * 3 + k], c = pos[idx[t * 3 + 2] * 3 + k];
      tmin[t * 3 + k] = Math.min(a, b, c); tmax[t * 3 + k] = Math.max(a, b, c); cen[t * 3 + k] = (a + b + c) / 3;
    }
  }
  const order = new Uint32Array(nt); for (let i = 0; i < nt; i++) order[i] = i;
  const cap = Math.max(1, 2 * nt);
  const min = new Float32Array(cap * 3), max = new Float32Array(cap * 3);
  const left = new Int32Array(cap), right = new Int32Array(cap), start = new Int32Array(cap), count = new Int32Array(cap);
  let n = 0;
  const build = (s: number, e: number): number => {
    const node = n++;
    let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (let i = s; i < e; i++) { const t = order[i]; for (let k = 0; k < 3; k++) { mn[k] = Math.min(mn[k], tmin[t * 3 + k]); mx[k] = Math.max(mx[k], tmax[t * 3 + k]); } }
    for (let k = 0; k < 3; k++) { min[node * 3 + k] = mn[k]; max[node * 3 + k] = mx[k]; }
    if (e - s <= 8) { left[node] = -1; right[node] = -1; start[node] = s; count[node] = e - s; return node; }
    const size = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]];
    const axis = size[0] >= size[1] && size[0] >= size[2] ? 0 : size[1] >= size[2] ? 1 : 2;
    const slice = Array.from(order.subarray(s, e)).sort((p, q) => cen[p * 3 + axis] - cen[q * 3 + axis]);
    order.set(slice, s);
    const mid = (s + e) >> 1;
    start[node] = s; count[node] = 0;
    left[node] = build(s, mid);
    right[node] = build(mid, e);
    return node;
  };
  if (nt) build(0, nt);
  return { pos, idx, min, max, left, right, start, count, order, n };
}

function rayBox(bvh: BVH, node: number, o: V3, inv: V3, tMax: number): boolean {
  let t0 = 0, t1 = tMax;
  for (let k = 0; k < 3; k++) {
    const a = (bvh.min[node * 3 + k] - o[k]) * inv[k], b = (bvh.max[node * 3 + k] - o[k]) * inv[k];
    t0 = Math.max(t0, Math.min(a, b)); t1 = Math.min(t1, Math.max(a, b));
    if (t1 < t0) return false;
  }
  return true;
}

function rayTri(bvh: BVH, t: number, o: V3, d: V3, tMax: number): { t: number; u: number; v: number } | null {
  const { pos, idx } = bvh;
  const A = at(pos, idx[t * 3]), B = at(pos, idx[t * 3 + 1]), C = at(pos, idx[t * 3 + 2]);
  const e1 = sub(B, A), e2 = sub(C, A);
  const p = cross(d, e2), det = dot(e1, p);
  if (Math.abs(det) < 1e-12) return null;
  const f = 1 / det, s = sub(o, A);
  const u = f * dot(s, p); if (u < -1e-6 || u > 1 + 1e-6) return null;
  const q = cross(s, e1);
  const v = f * dot(d, q); if (v < -1e-6 || u + v > 1 + 1e-6) return null;
  const tt = f * dot(e2, q);
  return tt > 1e-7 && tt < tMax ? { t: tt, u, v } : null;
}

/** The nearest hit along a ray, or null. */
export function raycastBVH(bvh: BVH, o: V3, d: V3, tMax = Infinity): { t: number; tri: number; u: number; v: number } | null {
  if (!bvh.n) return null;
  const inv: V3 = [1 / (d[0] || 1e-20), 1 / (d[1] || 1e-20), 1 / (d[2] || 1e-20)];
  let best: { t: number; tri: number; u: number; v: number } | null = null;
  let tBest = tMax;
  const stack = [0];
  while (stack.length) {
    const node = stack.pop()!;
    if (!rayBox(bvh, node, o, inv, tBest)) continue;
    if (bvh.left[node] < 0) {
      for (let i = bvh.start[node]; i < bvh.start[node] + bvh.count[node]; i++) {
        const t = bvh.order[i];
        const h = rayTri(bvh, t, o, d, tBest);
        if (h) { tBest = h.t; best = { t: h.t, tri: t, u: h.u, v: h.v }; }
      }
    } else { stack.push(bvh.left[node], bvh.right[node]); }
  }
  return best;
}

/** Any hit within tMax: what an occlusion ray asks. */
export function occludedBVH(bvh: BVH, o: V3, d: V3, tMax: number): boolean {
  if (!bvh.n) return false;
  const inv: V3 = [1 / (d[0] || 1e-20), 1 / (d[1] || 1e-20), 1 / (d[2] || 1e-20)];
  const stack = [0];
  while (stack.length) {
    const node = stack.pop()!;
    if (!rayBox(bvh, node, o, inv, tMax)) continue;
    if (bvh.left[node] < 0) {
      for (let i = bvh.start[node]; i < bvh.start[node] + bvh.count[node]; i++) if (rayTri(bvh, bvh.order[i], o, d, tMax)) return true;
    } else { stack.push(bvh.left[node], bvh.right[node]); }
  }
  return false;
}

function closestOnTri(p: V3, A: V3, B: V3, C: V3): V3 {
  // Ericson, Real-Time Collision Detection 5.1.5.
  const ab = sub(B, A), ac = sub(C, A), ap = sub(p, A);
  const d1 = dot(ab, ap), d2 = dot(ac, ap);
  if (d1 <= 0 && d2 <= 0) return A;
  const bp = sub(p, B), d3 = dot(ab, bp), d4 = dot(ac, bp);
  if (d3 >= 0 && d4 <= d3) return B;
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) { const v = d1 / (d1 - d3); return add(A, mul(ab, v)); }
  const cp = sub(p, C), d5 = dot(ab, cp), d6 = dot(ac, cp);
  if (d6 >= 0 && d5 <= d6) return C;
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) { const w = d2 / (d2 - d6); return add(A, mul(ac, w)); }
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) { const w = (d4 - d3) / ((d4 - d3) + (d5 - d6)); return add(B, mul(sub(C, B), w)); }
  const denom = 1 / (va + vb + vc), v = vb * denom, w = vc * denom;
  return add(A, add(mul(ab, v), mul(ac, w)));
}

/** The nearest point on the surface, its triangle, and the squared distance. */
export function nearestBVH(bvh: BVH, p: V3): { d2: number; tri: number; point: V3 } | null {
  if (!bvh.n) return null;
  let best: { d2: number; tri: number; point: V3 } | null = null;
  let d2Best = Infinity;
  const boxD2 = (node: number) => {
    let d2 = 0;
    for (let k = 0; k < 3; k++) {
      const v = p[k], lo = bvh.min[node * 3 + k], hi = bvh.max[node * 3 + k];
      if (v < lo) d2 += (lo - v) * (lo - v); else if (v > hi) d2 += (v - hi) * (v - hi);
    }
    return d2;
  };
  const stack: number[] = [0];
  while (stack.length) {
    const node = stack.pop()!;
    if (boxD2(node) >= d2Best) continue;
    if (bvh.left[node] < 0) {
      for (let i = bvh.start[node]; i < bvh.start[node] + bvh.count[node]; i++) {
        const t = bvh.order[i];
        const q = closestOnTri(p, at(bvh.pos, bvh.idx[t * 3]), at(bvh.pos, bvh.idx[t * 3 + 1]), at(bvh.pos, bvh.idx[t * 3 + 2]));
        const d = sub(q, p), d2 = dot(d, d);
        if (d2 < d2Best) { d2Best = d2; best = { d2, tri: t, point: q }; }
      }
    } else {
      const l = bvh.left[node], r = bvh.right[node];
      // Nearer child first, so the bound tightens early.
      if (boxD2(l) < boxD2(r)) stack.push(r, l); else stack.push(l, r);
    }
  }
  return best;
}

// ------------------------------------------------------------------ unwrap
//
// Blender's Smart UV Project, which is what game meshes get unwrapped with in practice: faces are
// grouped into islands that face roughly the same way (within `angle` degrees of the island's
// first face) without crossing a seam (an edge sharper than `seamAngle`), each island is projected
// flat along its direction, turned to its tightest box, and the boxes are shelf-packed into the
// square with a margin. The projection is one-to-one in world units, so texel density is uniform
// across the whole mesh. Vertices are split at island borders; the returned mesh carries a `uv`.

export interface Unwrapped { pos: Float32Array; idx: Uint32Array; uv: Float32Array; islands: number }

export function unwrapArrays(pos: Float32Array, idx: Uint32Array,
  opts: { angle?: number; seamAngle?: number; margin?: number; islandMax?: number; weldTol?: number } = {}): Unwrapped {
  const { angle = 66, seamAngle = 66, margin = 0.02, islandMax = 4000, weldTol = 1e-5 } = opts;
  const w = weldArrays(pos, idx, weldTol);
  const P = w.pos, I = w.idx;
  const nt = I.length / 3;
  if (!nt) return { pos: P, idx: I, uv: new Float32Array(0), islands: 0 };
  const fn: V3[] = new Array(nt);
  for (let t = 0; t < nt; t++) fn[t] = polyNormal([at(P, I[t * 3]), at(P, I[t * 3 + 1]), at(P, I[t * 3 + 2])]);
  // Adjacency across edges that are not seams.
  const edgeTris = new Map<string, number[]>();
  for (let t = 0; t < nt; t++) for (let e = 0; e < 3; e++) {
    const a = I[t * 3 + e], b = I[t * 3 + (e + 1) % 3];
    const k = a < b ? a + "," + b : b + "," + a;
    const l = edgeTris.get(k); if (l) l.push(t); else edgeTris.set(k, [t]);
  }
  const adj: number[][] = Array.from({ length: nt }, () => []);
  const cosSeam = Math.cos((seamAngle * Math.PI) / 180);
  for (const tris of edgeTris.values()) {
    if (tris.length !== 2) continue;
    if (dot(fn[tris[0]], fn[tris[1]]) >= cosSeam) { adj[tris[0]].push(tris[1]); adj[tris[1]].push(tris[0]); }
  }
  // Islands by flood fill, held within `angle` of the seed's normal.
  const cosAngle = Math.cos((angle * Math.PI) / 180);
  const island = new Int32Array(nt).fill(-1);
  const seeds: V3[] = [];
  const members: number[][] = [];
  for (let s = 0; s < nt; s++) {
    if (island[s] >= 0) continue;
    const id = seeds.length;
    seeds.push(fn[s]);
    const list = [s]; island[s] = id;
    const queue = [s];
    while (queue.length && list.length < islandMax) {
      const t = queue.shift()!;
      for (const u of adj[t]) {
        if (island[u] >= 0 || dot(fn[u], fn[s]) < cosAngle) continue;
        island[u] = id; list.push(u); queue.push(u);
      }
    }
    members.push(list);
  }
  // Project each island, split its vertices, and find its tightest box.
  const outPos: number[] = [], outIdx: number[] = [], outUv: number[] = [];
  const boxes: Array<{ id: number; w: number; h: number; uvStart: number; count: number }> = [];
  members.forEach((list, id) => {
    const n = seeds[id];
    const helper: V3 = Math.abs(n[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
    const u = norm(cross(helper, n)), v = cross(n, u);
    const local = new Map<number, number>();
    const uvStart = outUv.length;
    const pts2: number[][] = [];
    for (const t of list) {
      const tri: number[] = [];
      for (let e = 0; e < 3; e++) {
        const vi = I[t * 3 + e];
        let li = local.get(vi);
        if (li === undefined) {
          li = outPos.length / 3;
          local.set(vi, li);
          const p = at(P, vi);
          outPos.push(p[0], p[1], p[2]);
          pts2.push([dot(p, u), dot(p, v)]);
          outUv.push(0, 0);
        }
        tri.push(li);
      }
      outIdx.push(tri[0], tri[1], tri[2]);
    }
    // Tightest box over rotations, in steps of three degrees.
    let bestA = 0, bestArea = Infinity, bestB = [0, 0, 0, 0];
    for (let deg = 0; deg < 90; deg += 3) {
      const a = (deg * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (const [x, y] of pts2) { const rx = x * c - y * s, ry = x * s + y * c; x0 = Math.min(x0, rx); x1 = Math.max(x1, rx); y0 = Math.min(y0, ry); y1 = Math.max(y1, ry); }
      const area = (x1 - x0) * (y1 - y0);
      if (area < bestArea) { bestArea = area; bestA = a; bestB = [x0, x1, y0, y1]; }
    }
    const c = Math.cos(bestA), s = Math.sin(bestA);
    let [x0, x1, y0, y1] = bestB;
    // Landscape, so the shelves stack well.
    const landscape = x1 - x0 >= y1 - y0;
    pts2.forEach(([x, y], i) => {
      let rx = x * c - y * s - x0, ry = x * s + y * c - y0;
      if (!landscape) { const t = rx; rx = ry; ry = (x1 - x0) - t; }
      outUv[uvStart + i * 2] = rx; outUv[uvStart + i * 2 + 1] = ry;
    });
    const bw = landscape ? x1 - x0 : y1 - y0, bh = landscape ? y1 - y0 : x1 - x0;
    boxes.push({ id, w: bw, h: bh, uvStart, count: pts2.length });
  });
  // Shelf-pack into a square. The side starts from the total area and grows until it all fits.
  let total = 0; for (const b of boxes) total += (b.w + 1e-9) * (b.h + 1e-9);
  const orderB = boxes.slice().sort((p, q) => q.h - p.h);
  let side = Math.sqrt(total) * 1.1 || 1;
  const gap = () => side * margin;
  let placed: Array<{ b: typeof boxes[number]; x: number; y: number }> = [];
  for (let attempt = 0; attempt < 40; attempt++) {
    placed = [];
    let x = gap(), y = gap(), rowH = 0, fits = true;
    for (const b of orderB) {
      if (x + b.w + gap() > side) { x = gap(); y += rowH + gap(); rowH = 0; }
      if (y + b.h + gap() > side || b.w + 2 * gap() > side) { fits = false; break; }
      placed.push({ b, x, y });
      x += b.w + gap(); rowH = Math.max(rowH, b.h);
    }
    if (fits) break;
    side *= 1.12;
  }
  for (const { b, x, y } of placed) {
    for (let i = 0; i < b.count; i++) {
      outUv[b.uvStart + i * 2] = (outUv[b.uvStart + i * 2] + x) / side;
      outUv[b.uvStart + i * 2 + 1] = (outUv[b.uvStart + i * 2 + 1] + y) / side;
    }
  }
  return { pos: Float32Array.from(outPos), idx: Uint32Array.from(outIdx), uv: Float32Array.from(outUv), islands: boxes.length };
}

// ------------------------------------------------------------------ baking
//
// Maps written in UV space by walking every texel of every triangle. Three bakes: ambient
// occlusion (hemisphere rays against the mesh itself), curvature (the bend of the surface, the
// mask an edge-wear or dirt layer wants), and a tangent-space normal map that carries a high-poly's
// detail onto the low-poly's UVs. Every bake dilates its edges so a bilinear lookup never bleeds.

export interface Baked { width: number; height: number; data: Uint8ClampedArray }

interface Texel { x: number; y: number; p: V3; n: V3; tri: number; bu: number; bv: number }

/** Every texel each triangle covers, with the surface point and normal under it. */
function rasterise(pos: Float32Array, idx: Uint32Array, uv: Float32Array, normals: Float32Array, size: number, visit: (t: Texel) => void) {
  const nt = idx.length / 3;
  for (let t = 0; t < nt; t++) {
    const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
    const ax = uv[a * 2] * size, ay = uv[a * 2 + 1] * size, bx = uv[b * 2] * size, by = uv[b * 2 + 1] * size, cx = uv[c * 2] * size, cy = uv[c * 2 + 1] * size;
    const x0 = Math.max(0, Math.floor(Math.min(ax, bx, cx))), x1 = Math.min(size - 1, Math.ceil(Math.max(ax, bx, cx)));
    const y0 = Math.max(0, Math.floor(Math.min(ay, by, cy))), y1 = Math.min(size - 1, Math.ceil(Math.max(ay, by, cy)));
    const det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay);
    if (Math.abs(det) < 1e-12) continue;
    const A = at(pos, a), B = at(pos, b), C = at(pos, c);
    const NA = at(normals, a), NB = at(normals, b), NC = at(normals, c);
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      const px = x + 0.5, py = y + 0.5;
      let l1 = ((bx - px) * (cy - py) - (cx - px) * (by - py)) / det;
      let l2 = ((cx - px) * (ay - py) - (ax - px) * (cy - py)) / det;
      let l3 = 1 - l1 - l2;
      // A little slack on the edges, so neighbouring triangles leave no crack between them.
      const eps = -0.02;
      if (l1 < eps || l2 < eps || l3 < eps) continue;
      l1 = Math.max(0, l1); l2 = Math.max(0, l2); l3 = Math.max(0, 1 - l1 - l2);
      const p: V3 = [A[0] * l1 + B[0] * l2 + C[0] * l3, A[1] * l1 + B[1] * l2 + C[1] * l3, A[2] * l1 + B[2] * l2 + C[2] * l3];
      const n = norm([NA[0] * l1 + NB[0] * l2 + NC[0] * l3, NA[1] * l1 + NB[1] * l2 + NC[1] * l3, NA[2] * l1 + NB[2] * l2 + NC[2] * l3]);
      visit({ x, y, p, n, tri: t, bu: l2, bv: l3 });
    }
  }
}

/** Grow the painted texels outward `steps` times, so filtering never reads the void. */
function dilate(img: Baked, mask: Uint8Array, steps: number) {
  const { width: W, height: H, data } = img;
  let cur = mask;
  for (let s = 0; s < steps; s++) {
    const next = new Uint8Array(cur);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      if (cur[y * W + x]) continue;
      let r = 0, g = 0, b = 0, a = 0, k = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const xx = x + dx, yy = y + dy;
        if (xx < 0 || yy < 0 || xx >= W || yy >= H || !cur[yy * W + xx]) continue;
        const o = (yy * W + xx) * 4;
        r += data[o]; g += data[o + 1]; b += data[o + 2]; a += data[o + 3]; k++;
      }
      if (!k) continue;
      const o = (y * W + x) * 4;
      data[o] = r / k; data[o + 1] = g / k; data[o + 2] = b / k; data[o + 3] = a / k;
      next[y * W + x] = 1;
    }
    cur = next;
  }
}

function hammersley(i: number, n: number, rot: number): [number, number] {
  let bits = i;
  bits = ((bits << 16) | (bits >>> 16)) >>> 0;
  bits = (((bits & 0x55555555) << 1) | ((bits & 0xAAAAAAAA) >>> 1)) >>> 0;
  bits = (((bits & 0x33333333) << 2) | ((bits & 0xCCCCCCCC) >>> 2)) >>> 0;
  bits = (((bits & 0x0F0F0F0F) << 4) | ((bits & 0xF0F0F0F0) >>> 4)) >>> 0;
  bits = (((bits & 0x00FF00FF) << 8) | ((bits & 0xFF00FF00) >>> 8)) >>> 0;
  return [(i + 0.5) / n, ((bits / 4294967296) + rot) % 1];
}

/** Ambient occlusion: how much of the sky each texel can see, with rays no longer than `distance`. */
export function bakeAO(pos: Float32Array, idx: Uint32Array, uv: Float32Array, size = 256,
  opts: { rays?: number; distance?: number; bias?: number; dilate?: number; occluder?: { pos: Float32Array; idx: Uint32Array } } = {}): Baked {
  const { rays = 32, distance = Infinity, bias = 1e-3, dilate: steps = 4 } = opts;
  const occ = opts.occluder || { pos, idx };
  const bvh = buildBVH(occ.pos, occ.idx);
  const normals = vertexNormalsOf(pos, idx);
  const img: Baked = { width: size, height: size, data: new Uint8ClampedArray(size * size * 4) };
  const mask = new Uint8Array(size * size);
  let ext = 0; for (const v of pos) ext = Math.max(ext, Math.abs(v));
  const reach = isFinite(distance) ? distance : ext * 4 + 1;
  rasterise(pos, idx, uv, normals, size, (tx) => {
    const n = tx.n;
    const helper: V3 = Math.abs(n[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
    const t = norm(cross(helper, n)), b = cross(n, t);
    const o = add(tx.p, mul(n, bias + reach * 1e-4));
    const rot = ((tx.x * 7919 + tx.y * 104729) % 1000) / 1000;
    let hits = 0;
    for (let i = 0; i < rays; i++) {
      const [e1, e2] = hammersley(i, rays, rot);
      // Cosine-weighted hemisphere.
      const r = Math.sqrt(e1), phi = 2 * Math.PI * e2;
      const x = r * Math.cos(phi), y = r * Math.sin(phi), z = Math.sqrt(Math.max(0, 1 - e1));
      const d = norm(add(add(mul(t, x), mul(b, y)), mul(n, z)));
      if (occludedBVH(bvh, o, d, reach)) hits++;
    }
    const ao = 1 - hits / rays;
    const k = (tx.y * size + tx.x) * 4, g = Math.round(ao * 255);
    img.data[k] = g; img.data[k + 1] = g; img.data[k + 2] = g; img.data[k + 3] = 255;
    mask[tx.y * size + tx.x] = 1;
  });
  dilate(img, mask, steps);
  return img;
}

/** Curvature: mid-grey is flat, brighter is convex (an edge), darker is concave (a crease). */
export function bakeCurvature(pos: Float32Array, idx: Uint32Array, uv: Float32Array, size = 256,
  opts: { scale?: number; dilate?: number } = {}): Baked {
  const { scale = 1, dilate: steps = 4 } = opts;
  const normals = vertexNormalsOf(pos, idx);
  const nv = pos.length / 3;
  const sum = new Float64Array(nv), cnt = new Uint32Array(nv);
  for (let t = 0; t < idx.length; t += 3) {
    for (let e = 0; e < 3; e++) {
      const i = idx[t + e], j = idx[t + (e + 1) % 3];
      const d = sub(at(pos, j), at(pos, i)), dn = sub(at(normals, j), at(normals, i));
      const l2 = dot(d, d) || 1e-12;
      const k = dot(dn, d) / l2;   // positive where the normals spread apart: convex
      sum[i] += k; cnt[i]++; sum[j] += k; cnt[j]++;
    }
  }
  const curv = new Float32Array(nv);
  let ext = 0; for (const v of pos) ext = Math.max(ext, Math.abs(v));
  for (let i = 0; i < nv; i++) curv[i] = cnt[i] ? (sum[i] / cnt[i]) * ext * scale : 0;
  const curvAsNormals = new Float32Array(nv * 3);
  for (let i = 0; i < nv; i++) curvAsNormals[i * 3] = curv[i];
  const img: Baked = { width: size, height: size, data: new Uint8ClampedArray(size * size * 4) };
  const mask = new Uint8Array(size * size);
  // Ride the rasteriser's interpolation by putting the value where a normal's x would go.
  rasterise(pos, idx, uv, curvAsNormals, size, (tx) => {
    const a = idx[tx.tri * 3], b = idx[tx.tri * 3 + 1], c = idx[tx.tri * 3 + 2];
    const v = curv[a] * (1 - tx.bu - tx.bv) + curv[b] * tx.bu + curv[c] * tx.bv;
    const g = Math.round(Math.max(0, Math.min(255, 128 + v * 127)));
    const k = (tx.y * size + tx.x) * 4;
    img.data[k] = g; img.data[k + 1] = g; img.data[k + 2] = g; img.data[k + 3] = 255;
    mask[tx.y * size + tx.x] = 1;
  });
  dilate(img, mask, steps);
  return img;
}

/** Per-vertex tangents from the UVs, for a tangent-space normal map. */
export function tangentsOf(pos: Float32Array, idx: Uint32Array, uv: Float32Array, normals: Float32Array): { t: Float32Array; b: Float32Array } {
  const nv = pos.length / 3;
  const T = new Float32Array(nv * 3), B = new Float32Array(nv * 3);
  for (let k = 0; k < idx.length; k += 3) {
    const a = idx[k], b = idx[k + 1], c = idx[k + 2];
    const e1 = sub(at(pos, b), at(pos, a)), e2 = sub(at(pos, c), at(pos, a));
    const du1 = uv[b * 2] - uv[a * 2], dv1 = uv[b * 2 + 1] - uv[a * 2 + 1], du2 = uv[c * 2] - uv[a * 2], dv2 = uv[c * 2 + 1] - uv[a * 2 + 1];
    const det = du1 * dv2 - du2 * dv1;
    if (Math.abs(det) < 1e-14) continue;
    const r = 1 / det;
    const t: V3 = [(e1[0] * dv2 - e2[0] * dv1) * r, (e1[1] * dv2 - e2[1] * dv1) * r, (e1[2] * dv2 - e2[2] * dv1) * r];
    const bt: V3 = [(e2[0] * du1 - e1[0] * du2) * r, (e2[1] * du1 - e1[1] * du2) * r, (e2[2] * du1 - e1[2] * du2) * r];
    for (const v of [a, b, c]) { for (let q = 0; q < 3; q++) { T[v * 3 + q] += t[q]; B[v * 3 + q] += bt[q]; } }
  }
  for (let v = 0; v < nv; v++) {
    const n = at(normals, v);
    let t = at(T, v);
    t = norm(sub(t, mul(n, dot(n, t))));   // Gram-Schmidt against the normal
    const bRaw = at(B, v);
    const handed = dot(cross(n, t), bRaw) < 0 ? -1 : 1;
    const b = mul(cross(n, t), handed);
    for (let q = 0; q < 3; q++) { T[v * 3 + q] = t[q]; B[v * 3 + q] = b[q]; }
  }
  return { t: T, b: B };
}

/** A tangent-space normal map: the high-poly's normals, found by a ray from each low-poly texel,
 *  written in the low-poly's tangent frame. Flat blue where nothing is hit. */
export function bakeNormalMap(low: { pos: Float32Array; idx: Uint32Array; uv: Float32Array }, high: { pos: Float32Array; idx: Uint32Array }, size = 512,
  opts: { distance?: number; dilate?: number } = {}): Baked {
  const { distance = 0.1, dilate: steps = 4 } = opts;
  const bvh = buildBVH(high.pos, high.idx);
  const hn = vertexNormalsOf(high.pos, high.idx);
  const ln = vertexNormalsOf(low.pos, low.idx);
  const { t: LT, b: LB } = tangentsOf(low.pos, low.idx, low.uv, ln);
  const img: Baked = { width: size, height: size, data: new Uint8ClampedArray(size * size * 4) };
  const mask = new Uint8Array(size * size);
  rasterise(low.pos, low.idx, low.uv, ln, size, (tx) => {
    const a = low.idx[tx.tri * 3], b = low.idx[tx.tri * 3 + 1], c = low.idx[tx.tri * 3 + 2];
    const w0 = 1 - tx.bu - tx.bv;
    const T = norm([LT[a * 3] * w0 + LT[b * 3] * tx.bu + LT[c * 3] * tx.bv, LT[a * 3 + 1] * w0 + LT[b * 3 + 1] * tx.bu + LT[c * 3 + 1] * tx.bv, LT[a * 3 + 2] * w0 + LT[b * 3 + 2] * tx.bu + LT[c * 3 + 2] * tx.bv]);
    const Bt = norm([LB[a * 3] * w0 + LB[b * 3] * tx.bu + LB[c * 3] * tx.bv, LB[a * 3 + 1] * w0 + LB[b * 3 + 1] * tx.bu + LB[c * 3 + 1] * tx.bv, LB[a * 3 + 2] * w0 + LB[b * 3 + 2] * tx.bu + LB[c * 3 + 2] * tx.bv]);
    const n = tx.n;
    // From just outside the low surface, look inward; then the other way, and keep the nearer.
    const start = add(tx.p, mul(n, distance));
    let hit = raycastBVH(bvh, start, mul(n, -1), distance * 2);
    const back = raycastBVH(bvh, sub(tx.p, mul(n, distance)), n, distance * 2);
    if (back && (!hit || Math.abs(back.t - distance) < Math.abs(hit.t - distance))) hit = back;
    let nh: V3 = n;
    if (hit) {
      const ha = high.idx[hit.tri * 3], hb = high.idx[hit.tri * 3 + 1], hc = high.idx[hit.tri * 3 + 2];
      const wa = 1 - hit.u - hit.v;
      nh = norm([hn[ha * 3] * wa + hn[hb * 3] * hit.u + hn[hc * 3] * hit.v, hn[ha * 3 + 1] * wa + hn[hb * 3 + 1] * hit.u + hn[hc * 3 + 1] * hit.v, hn[ha * 3 + 2] * wa + hn[hb * 3 + 2] * hit.u + hn[hc * 3 + 2] * hit.v]);
      if (dot(nh, n) < 0) nh = mul(nh, -1);
    }
    const x = dot(nh, T), y = dot(nh, Bt), z = dot(nh, n);
    const k = (tx.y * size + tx.x) * 4;
    img.data[k] = Math.round((x * 0.5 + 0.5) * 255); img.data[k + 1] = Math.round((y * 0.5 + 0.5) * 255); img.data[k + 2] = Math.round((z * 0.5 + 0.5) * 255); img.data[k + 3] = 255;
    mask[tx.y * size + tx.x] = 1;
  });
  dilate(img, mask, steps);
  return img;
}

// ------------------------------------------------------------------ the welded graph
//
// Weights, relaxing and curvature all want one vertex per position with edges between them, and a
// procedural mesh has three or four copies of every corner. This maps each original vertex to its
// welded twin, so a result found on the graph can be written back onto every copy.

interface Graph { n: number; map: Uint32Array; wpos: Float32Array; adj: number[][]; boundary: Uint8Array }

function graphOf(pos: Float32Array, idx: Uint32Array, tol = 1e-5): Graph {
  const nv = pos.length / 3;
  const map = new Uint32Array(nv);
  const keys = new Map<string, number>();
  const wp: number[] = [];
  const q = 1 / tol;
  for (let i = 0; i < nv; i++) {
    const k = Math.round(pos[i * 3] * q) + "," + Math.round(pos[i * 3 + 1] * q) + "," + Math.round(pos[i * 3 + 2] * q);
    let w = keys.get(k);
    if (w === undefined) { w = wp.length / 3; keys.set(k, w); wp.push(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]); }
    map[i] = w;
  }
  const n = wp.length / 3;
  const adjSets: Array<Set<number>> = Array.from({ length: n }, () => new Set<number>());
  const edgeUse = new Map<string, number>();
  for (let t = 0; t < idx.length; t += 3) {
    for (let e = 0; e < 3; e++) {
      const a = map[idx[t + e]], b = map[idx[t + (e + 1) % 3]];
      if (a === b) continue;
      adjSets[a].add(b); adjSets[b].add(a);
      const k = a < b ? a + "," + b : b + "," + a;
      edgeUse.set(k, (edgeUse.get(k) || 0) + 1);
    }
  }
  const boundary = new Uint8Array(n);
  for (const [k, c] of edgeUse) if (c === 1) { const [a, b] = k.split(",").map(Number); boundary[a] = 1; boundary[b] = 1; }
  return { n, map, wpos: Float32Array.from(wp), adj: adjSets.map((s) => [...s]), boundary };
}

// ------------------------------------------------------------------ heat weights
//
// Bone heat, after Baran and Popović: each vertex is a heat source for the nearest bone it can SEE
// (a ray to the bone that passes through the mesh means the bone is on the other side of a wall,
// which is how a hand's vertices stop bleeding onto the other hand). The heat diffuses over the
// surface with a Laplacian, so the result is smooth along the skin and sharp where the bones are
// close to it. One solve per bone; the solutions sum to one by construction.

export function heatWeightsArrays(pos: Float32Array, idx: Uint32Array, bones: Array<{ head: V3; tail: V3 }>,
  opts: { maxBones?: number; heat?: number; visibility?: boolean; iterations?: number; tol?: number } = {}): { index: Uint16Array; weight: Float32Array } {
  const { maxBones = 4, heat = 1, visibility = true, iterations = 400, tol = 1e-5 } = opts;
  const nv = pos.length / 3, nb = bones.length;
  const index = new Uint16Array(nv * maxBones), weight = new Float32Array(nv * maxBones);
  if (!nb || !nv) return { index, weight };
  const g = graphOf(pos, idx, tol);
  const n = g.n;
  const normals = vertexNormalsOf(pos, idx);
  const wn = new Float32Array(n * 3);
  for (let i = 0; i < nv; i++) { const w = g.map[i]; wn[w * 3] += normals[i * 3]; wn[w * 3 + 1] += normals[i * 3 + 1]; wn[w * 3 + 2] += normals[i * 3 + 2]; }
  const bvh = visibility ? buildBVH(pos, idx) : null;
  let ext = 0; for (const v of pos) ext = Math.max(ext, Math.abs(v));
  const closest = (p: V3, b: { head: V3; tail: V3 }): V3 => {
    const d = sub(b.tail, b.head), l2 = dot(d, d) || 1e-12;
    const t = Math.max(0, Math.min(1, dot(sub(p, b.head), d) / l2));
    return add(b.head, mul(d, t));
  };
  // Heat per welded vertex, on its nearest visible bone — shared out when two bones are equally
  // near, as they are all around a joint, so the ring at a joint starts halfway rather than on
  // whichever bone was listed first.
  const H = new Float64Array(n);
  const sources: number[][] = new Array(n);
  for (let i = 0; i < n; i++) {
    const p = at(g.wpos, i);
    const cands = bones.map((b, j) => { const c = closest(p, b); const d = sub(c, p); return { j, c, d2: dot(d, d) }; }).sort((x, y) => x.d2 - y.d2);
    let visible = cands;
    if (bvh) {
      const nrm = norm(at(wn, i));
      const o = add(p, mul(nrm, ext * 1e-4));
      const seen = cands.slice(0, 4).filter((cnd) => {
        const dir = sub(cnd.c, p), dist = len(dir);
        return dist < 1e-9 || !occludedBVH(bvh, o, mul(dir, 1 / dist), dist * (1 - 1e-3));
      });
      if (seen.length) visible = seen;
    }
    const best = visible[0];
    sources[i] = visible.filter((c) => c.d2 <= best.d2 * (1 + 1e-6) + 1e-12).map((c) => c.j);
    H[i] = heat / (best.d2 + (ext * 1e-3) ** 2);
  }
  // Conjugate gradient on (L + H) w = H p, per bone that owns any heat.
  const deg = g.adj.map((a) => a.length);
  const apply = (x: Float64Array, out: Float64Array) => {
    for (let i = 0; i < n; i++) {
      let s = (deg[i] + H[i] + 1e-9) * x[i];
      for (const j of g.adj[i]) s -= x[j];
      out[i] = s;
    }
  };
  const solve = (b: Float64Array): Float64Array => {
    const x = new Float64Array(n), r = Float64Array.from(b), p = Float64Array.from(b), Ap = new Float64Array(n);
    let rr = 0; for (let i = 0; i < n; i++) rr += r[i] * r[i];
    const stop = Math.max(1e-20, rr * 1e-12);
    for (let it = 0; it < iterations && rr > stop; it++) {
      apply(p, Ap);
      let pAp = 0; for (let i = 0; i < n; i++) pAp += p[i] * Ap[i];
      if (pAp <= 0) break;
      const alpha = rr / pAp;
      let rr2 = 0;
      for (let i = 0; i < n; i++) { x[i] += alpha * p[i]; r[i] -= alpha * Ap[i]; rr2 += r[i] * r[i]; }
      const beta = rr2 / rr; rr = rr2;
      for (let i = 0; i < n; i++) p[i] = r[i] + beta * p[i];
    }
    return x;
  };
  const W: Float64Array[] = [];
  for (let j = 0; j < nb; j++) {
    let any = false;
    const b = new Float64Array(n);
    for (let i = 0; i < n; i++) if (sources[i].includes(j)) { b[i] = H[i] / sources[i].length; any = true; }
    W.push(any ? solve(b) : new Float64Array(n));
  }
  // Top bones per vertex, normalised, written onto every copy of the welded vertex.
  for (let i = 0; i < nv; i++) {
    const w = g.map[i];
    const ranked = W.map((col, j) => ({ j, v: Math.max(0, col[w]) })).sort((x, y) => y.v - x.v).slice(0, maxBones);
    let sum = 0; for (const r of ranked) sum += r.v;
    ranked.forEach((r, k) => { index[i * maxBones + k] = r.j; weight[i * maxBones + k] = sum > 0 ? r.v / sum : k === 0 ? 1 : 0; });
  }
  return { index, weight };
}

// ------------------------------------------------------------------ remesh
/**
 * Voxel remesh: the closed shape is sampled as a signed distance on a grid (sign by ray parity,
 * distance from the nearest triangle) and skinned again with the isosurface, so a body glued
 * together from primitives becomes one evenly meshed surface with no seams, at the resolution
 * asked for. What a sculptor reaches for before smoothing.
 */
export function remeshArrays(pos: Float32Array, idx: Uint32Array, opts: { res?: number; margin?: number } = {}): { pos: Float32Array; idx: Uint32Array } {
  const { res = 48, margin = 0.06 } = opts;
  const bvh = buildBVH(pos, idx);
  const lo: V3 = [Infinity, Infinity, Infinity], hi: V3 = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < pos.length; i += 3) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], pos[i + k]); hi[k] = Math.max(hi[k], pos[i + k]); }
  const size = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  const big = Math.max(size[0], size[1], size[2]) || 1;
  const pad = big * margin;
  const min: V3 = [lo[0] - pad, lo[1] - pad, lo[2] - pad], max: V3 = [hi[0] + pad, hi[1] + pad, hi[2] + pad];
  const R: V3 = [Math.max(4, Math.round((res * (size[0] + 2 * pad)) / (big + 2 * pad))), Math.max(4, Math.round((res * (size[1] + 2 * pad)) / (big + 2 * pad))), Math.max(4, Math.round((res * (size[2] + 2 * pad)) / (big + 2 * pad)))];
  const h = [(max[0] - min[0]) / R[0], (max[1] - min[1]) / R[1], (max[2] - min[2]) / R[2]];
  const sx = R[0] + 1, sy = R[1] + 1, sz = R[2] + 1;
  const grid = new Float32Array(sx * sy * sz);
  const dir: V3 = [1, 0, 0];
  for (let k = 0; k < sz; k++) for (let j = 0; j < sy; j++) for (let i = 0; i < sx; i++) {
    const p: V3 = [min[0] + i * h[0], min[1] + j * h[1], min[2] + k * h[2]];
    const near = nearestBVH(bvh, p);
    let d = near ? Math.sqrt(near.d2) : big;
    // Parity along +x: an odd number of crossings means inside.
    let crossings = 0, o: V3 = p, guard = 0;
    for (;;) {
      const hit = raycastBVH(bvh, o, dir, big * 4);
      if (!hit || guard++ > 64) break;
      crossings++;
      o = [o[0] + dir[0] * (hit.t + big * 1e-6), o[1], o[2]];
    }
    if (crossings % 2 === 1) d = -d;
    grid[i + sx * (j + sy * k)] = d;
  }
  const field = (x: number, y: number, z: number) => {
    const i = Math.max(0, Math.min(sx - 1, Math.round((x - min[0]) / h[0])));
    const j = Math.max(0, Math.min(sy - 1, Math.round((y - min[1]) / h[1])));
    const k = Math.max(0, Math.min(sz - 1, Math.round((z - min[2]) / h[2])));
    return grid[i + sx * (j + sy * k)];
  };
  return isosurfaceArrays(field, min, max, R, 0);
}

// ------------------------------------------------------------------ relax
/**
 * Taubin smoothing: a Laplacian step in, then a slightly larger one out, `iterations` times, so
 * the surface loses its jaggedness without shrinking the way plain smoothing does. Boundary
 * vertices stay where they are. The mesh keeps its triangles; only positions move.
 */
export function relaxArrays(pos: Float32Array, idx: Uint32Array, opts: { iterations?: number; lambda?: number; mu?: number; tol?: number } = {}): Float32Array {
  const { iterations = 5, lambda = 0.5, mu = -0.53, tol = 1e-5 } = opts;
  const g = graphOf(pos, idx, tol);
  const n = g.n;
  let cur = Float32Array.from(g.wpos);
  const step = (factor: number) => {
    const next = Float32Array.from(cur);
    for (let i = 0; i < n; i++) {
      const a = g.adj[i];
      if (!a.length || g.boundary[i]) continue;
      let cx = 0, cy = 0, cz = 0;
      for (const j of a) { cx += cur[j * 3]; cy += cur[j * 3 + 1]; cz += cur[j * 3 + 2]; }
      cx /= a.length; cy /= a.length; cz /= a.length;
      next[i * 3] += factor * (cx - cur[i * 3]); next[i * 3 + 1] += factor * (cy - cur[i * 3 + 1]); next[i * 3 + 2] += factor * (cz - cur[i * 3 + 2]);
    }
    cur = next;
  };
  for (let it = 0; it < iterations; it++) { step(lambda); step(mu); }
  const out = new Float32Array(pos.length);
  for (let i = 0; i < pos.length / 3; i++) { const w = g.map[i]; out[i * 3] = cur[w * 3]; out[i * 3 + 1] = cur[w * 3 + 1]; out[i * 3 + 2] = cur[w * 3 + 2]; }
  return out;
}

export { isosurfaceArrays as _isosurface };
