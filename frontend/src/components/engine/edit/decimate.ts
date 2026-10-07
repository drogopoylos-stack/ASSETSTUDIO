// Quadric edge collapse on plain arrays: Blender's Decimate (Collapse) for an AI that sculpts dense
// and ships light.
//
// The goblin A/B: the Blender builder decimated a 260k-triangle sculpt to 6.8k and baked the sculpt
// onto it; the forge builder had to write its own QEM ("the biggest missing capability") and still
// shipped two non-manifold edges from it. This is Garland and Heckbert's algorithm with the parts a
// shipping mesh needs:
//   - every edge is priced by the sum of squared distances to the planes of the faces its two ends
//     stand for (area weighted), and the cheapest goes first, to the point that minimises it;
//   - open borders, UV seams and creases sharper than `featureAngle` carry extra planes through the
//     edge, so silhouettes, seams and hard edges stay where they are;
//   - UVs and colours are carried per wedge (a corner's attribute set): a UV seam is two wedges on
//     one position and collapses only along itself, both sides together, so it stays a seam; inside
//     an island the attributes enter the price (Hoppe's attribute quadrics), so a colour edge or a
//     stretched UV is kept, and each new vertex gets the attributes that fit best;
//   - a collapse is refused if it would turn a face over, pinch the surface (the link condition) or
//     leave a fin; open borders are never joined to anything, so no hole is made or closed.
// Plain arrays in, plain arrays out; nothing here imports an engine.

/** Options for `decimateArrays`. */
export interface DecimateOptions {
  /** Triangles to keep. */
  target?: number;
  /** Or the fraction of triangles to keep (default 0.5 when neither is given). */
  ratio?: number;
  /** Open borders: 'weight' (default) lets them simplify along themselves under heavy planes;
   *  'lock' keeps every border vertex exactly. */
  boundary?: "lock" | "weight";
  /** Degrees; an edge sharper than this gets planes that hold the crease (default 50). */
  featureAngle?: number;
  /** Per-vertex UV pairs, carried and interpolated; a UV seam stays a seam. */
  uv?: Float32Array;
  /** Per-vertex colours, 3 or 4 per vertex, carried and interpolated. */
  color?: Float32Array;
  /** Stop before collapses whose error (the RMS distance to the merged planes, metres) exceeds this. */
  maxError?: number;
  /** How much a UV error costs next to a position error (default 1: one UV unit = the mesh's size). */
  uvWeight?: number;
  /** How much a colour error costs (default 0.1: a full colour swing = a tenth of the mesh's size). */
  colorWeight?: number;
}

/** What `decimateArrays` returns: the new mesh, its carried attributes, how many edges were
 *  collapsed and the largest collapse error (metres). */
export interface Decimated {
  pos: Float32Array; idx: Uint32Array; uv?: Float32Array; color?: Float32Array;
  collapsed: number; error: number;
}

// ------------------------------------------------------------------ helpers

const hashCell = (x: number, y: number, z: number): number =>
  (Math.imul(x | 0, 73856093) ^ Math.imul(y | 0, 19349663) ^ Math.imul(z | 0, 83492791)) >>> 0;

function extentOf(pos: ArrayLike<number>): number {
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (let i = 0; i + 2 < pos.length; i += 3) {
    const x = pos[i], y = pos[i + 1], z = pos[i + 2];
    if (x < x0) x0 = x; if (x > x1) x1 = x;
    if (y < y0) y0 = y; if (y > y1) y1 = y;
    if (z < z0) z0 = z; if (z > z1) z1 = z;
  }
  return x1 >= x0 ? Math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2) : 0;
}

/** One id per position (within tol), searching the 27 cells around each point. */
function weldIds(pos: ArrayLike<number>, tol: number): { map: Uint32Array; rep: Uint32Array; count: number } {
  const n = Math.floor(pos.length / 3);
  const map = new Uint32Array(n), rep = new Uint32Array(n);
  let size = 16;
  while (size < n * 2) size *= 2;
  const mask = size - 1;
  const head = new Int32Array(size).fill(-1), next = new Int32Array(n);
  const t = Math.max(tol, 1e-30), inv = 1 / t, tol2 = t * t;
  let count = 0;
  for (let i = 0; i < n; i++) {
    const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
    const cx = Math.floor(x * inv), cy = Math.floor(y * inv), cz = Math.floor(z * inv);
    let found = -1;
    for (let dx = -1; dx <= 1 && found < 0; dx++) {
      for (let dy = -1; dy <= 1 && found < 0; dy++) {
        for (let dz = -1; dz <= 1 && found < 0; dz++) {
          for (let r = head[hashCell(cx + dx, cy + dy, cz + dz) & mask]; r >= 0; r = next[r]) {
            const j = rep[r] * 3;
            const ox = pos[j] - x, oy = pos[j + 1] - y, oz = pos[j + 2] - z;
            if (ox * ox + oy * oy + oz * oz <= tol2) { found = r; break; }
          }
        }
      }
    }
    if (found >= 0) { map[i] = found; continue; }
    const h = hashCell(cx, cy, cz) & mask;
    rep[count] = i; next[count] = head[h]; head[h] = count; map[i] = count; count++;
  }
  return { map, rep, count };
}

/** Unordered pairs of ints to ids, numbered in order of first sight. */
class PairIds {
  ka: Int32Array; kb: Int32Array; val: Int32Array; mask: number; count = 0;
  constructor(expected: number) {
    let s = 64;
    while (s < expected * 2) s *= 2;
    this.ka = new Int32Array(s).fill(-1); this.kb = new Int32Array(s); this.val = new Int32Array(s); this.mask = s - 1;
  }
  id(a: number, b: number): number {
    if (a > b) { const t = a; a = b; b = t; }
    let h = Math.imul(a, 0x9e3779b1) ^ Math.imul(b + 0x7f4a7c15, 0x85ebca77);
    h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
    h = ((h ^ (h >>> 12)) >>> 0) & this.mask;
    for (;;) {
      const k = this.ka[h];
      if (k === -1) { this.ka[h] = a; this.kb[h] = b; this.val[h] = this.count; return this.count++; }
      if (k === a && this.kb[h] === b) return this.val[h];
      h = (h + 1) & this.mask;
    }
  }
}

/** A binary min-heap of edge candidates in parallel typed arrays. */
class EdgeHeap {
  cost: Float64Array; u: Int32Array; v: Int32Array; vu: Int32Array; vv: Int32Array; size = 0;
  constructor(cap: number) {
    cap = Math.max(16, cap);
    this.cost = new Float64Array(cap); this.u = new Int32Array(cap); this.v = new Int32Array(cap); this.vu = new Int32Array(cap); this.vv = new Int32Array(cap);
  }
  private grow() {
    const cap = this.cost.length * 2;
    const c = new Float64Array(cap); c.set(this.cost); this.cost = c;
    for (const k of ["u", "v", "vu", "vv"] as const) { const a = new Int32Array(cap); a.set(this[k]); this[k] = a; }
  }
  private swap(i: number, j: number) {
    const c = this.cost[i]; this.cost[i] = this.cost[j]; this.cost[j] = c;
    let t = this.u[i]; this.u[i] = this.u[j]; this.u[j] = t;
    t = this.v[i]; this.v[i] = this.v[j]; this.v[j] = t;
    t = this.vu[i]; this.vu[i] = this.vu[j]; this.vu[j] = t;
    t = this.vv[i]; this.vv[i] = this.vv[j]; this.vv[j] = t;
  }
  push(cost: number, u: number, v: number, vu: number, vv: number) {
    if (this.size === this.cost.length) this.grow();
    let i = this.size++;
    this.cost[i] = cost; this.u[i] = u; this.v[i] = v; this.vu[i] = vu; this.vv[i] = vv;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.cost[p] <= this.cost[i]) break;
      this.swap(i, p); i = p;
    }
  }
  /** Remove the cheapest; its fields are left at index `size` (read them right after). */
  pop(): number {
    const last = --this.size;
    this.swap(0, last);
    let i = 0;
    for (;;) {
      const l = i * 2 + 1, r = l + 1;
      let m = i;
      if (l < last && this.cost[l] < this.cost[m]) m = l;
      if (r < last && this.cost[r] < this.cost[m]) m = r;
      if (m === i) break;
      this.swap(i, m); i = m;
    }
    return last;
  }
}

const INTERIOR = 0, BORDER = 1, SEAM = 2, LOCKED = 3;
const BORDER_W = 100, SEAM_W = 100, FEATURE_W = 10;

/**
 * Garland-Heckbert quadric edge collapse to `target` triangles (or `ratio`), keeping silhouettes,
 * creases sharper than `featureAngle` (50), open borders and UV seams, and never flipping a face.
 * Open borders slide along themselves (`boundary` 'weight', the default) or stay exactly ('lock').
 * `uv` and `color` (3 or 4 per
 * vertex) are carried: each new vertex gets the attributes that fit its faces best, and a seam keeps
 * its two sides. No face is turned over, no hole opened or closed, no fin made. Copies of a vertex
 * with equal attributes are merged, so the result is welded except along seams. `maxError` (metres)
 * refuses collapses that would move the surface further. Returns the mesh, `collapsed` (how many
 * edges went) and `error` (the largest collapse error, metres). 260k -> 7k triangles in about a second.
 */
export function decimateArrays(pos: Float32Array, idx: Uint32Array, opts: DecimateOptions = {}): Decimated {
  const nIn = Math.floor(pos.length / 3);
  const boundary = opts.boundary ?? "weight";
  if (boundary !== "lock" && boundary !== "weight") throw new Error(`decimateArrays: boundary "${boundary}"; use lock or weight`);
  const featureAngle = typeof opts.featureAngle === "number" && isFinite(opts.featureAngle) ? opts.featureAngle : 50;
  const uvIn = opts.uv ?? null;
  if (uvIn && uvIn.length < nIn * 2) throw new Error(`decimateArrays: uv holds ${uvIn.length / 2} pairs for ${nIn} vertices`);
  const colIn = opts.color ?? null;
  let cc = 0;
  if (colIn) {
    cc = nIn ? colIn.length / nIn : 0;
    if (cc !== 3 && cc !== 4) throw new Error(`decimateArrays: color must hold 3 or 4 numbers per vertex (got ${colIn.length} for ${nIn} vertices)`);
  }
  const UVO = uvIn ? 2 : 0, K = UVO + cc, AS = 1 + 14 * K;
  const ext = extentOf(pos), size = ext || 1;
  const cw = new Float64Array(K);
  for (let j = 0; j < K; j++) cw[j] = j < UVO ? ((opts.uvWeight ?? 1) * size) ** 2 : ((opts.colorWeight ?? 0.1) * size) ** 2;
  const attrOf = (i: number, j: number) => (j < UVO ? uvIn![i * 2 + j] : colIn![i * cc + j - UVO]);

  // ---- one vertex per position
  const { map: vid, rep, count: nv } = weldIds(pos, Math.max(1e-12, ext * 1e-6));
  const P = new Float64Array(nv * 3);
  for (let v = 0; v < nv; v++) { const i = rep[v] * 3; P[v * 3] = pos[i]; P[v * 3 + 1] = pos[i + 1]; P[v * 3 + 2] = pos[i + 2]; }

  // ---- wedges: one per (position, attribute set)
  let nw = nv;
  const wedgeOf = new Int32Array(nIn);
  let wVid: Int32Array, wAttr: Float64Array;
  if (!K) {
    for (let i = 0; i < nIn; i++) wedgeOf[i] = vid[i];
    wVid = new Int32Array(nv);
    for (let v = 0; v < nv; v++) wVid[v] = v;
    wAttr = new Float64Array(0);
  } else {
    wVid = new Int32Array(nIn); wAttr = new Float64Array(nIn * K);
    const vHead = new Int32Array(nv).fill(-1), wNext = new Int32Array(nIn);
    nw = 0;
    for (let i = 0; i < nIn; i++) {
      const v = vid[i];
      let found = -1;
      for (let w = vHead[v]; w >= 0 && found < 0; w = wNext[w]) {
        let same = true;
        for (let j = 0; j < K; j++) if (Math.abs(wAttr[w * K + j] - attrOf(i, j)) > 1e-6) { same = false; break; }
        if (same) found = w;
      }
      if (found < 0) {
        found = nw++;
        wVid[found] = v;
        for (let j = 0; j < K; j++) wAttr[found * K + j] = attrOf(i, j);
        wNext[found] = vHead[v]; vHead[v] = found;
      }
      wedgeOf[i] = found;
    }
  }

  // ---- triangles, the ones the weld collapsed dropped
  const ntIn = Math.floor(idx.length / 3);
  const tv = new Int32Array(ntIn * 3), tw = new Int32Array(ntIn * 3);
  let nt = 0;
  for (let t = 0; t < ntIn; t++) {
    const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
    if (a >= nIn || b >= nIn || c >= nIn) continue;
    const va = vid[a], vb = vid[b], vc = vid[c];
    if (va === vb || vb === vc || va === vc) continue;
    tv[nt * 3] = va; tv[nt * 3 + 1] = vb; tv[nt * 3 + 2] = vc;
    tw[nt * 3] = wedgeOf[a]; tw[nt * 3 + 1] = wedgeOf[b]; tw[nt * 3 + 2] = wedgeOf[c];
    nt++;
  }
  const target = Math.max(1, Math.floor(typeof opts.target === "number" && isFinite(opts.target) ? opts.target
    : Math.round((typeof opts.ratio === "number" && isFinite(opts.ratio) ? opts.ratio : 0.5) * ntIn)));
  const talive = new Uint8Array(nt).fill(1);
  const head = new Int32Array(nv).fill(-1), cnext = new Int32Array(nt * 3), triCount = new Int32Array(nv);
  for (let c = nt * 3 - 1; c >= 0; c--) { const v = tv[c]; cnext[c] = head[v]; head[v] = c; triCount[v]++; }

  // ---- edges: open borders (one face), seams (two faces, different wedges), fins (three or more)
  const et = new PairIds(nt * 2);
  const ea = new Int32Array(nt * 3), eb = new Int32Array(nt * 3), ecnt = new Int32Array(nt * 3);
  const ef0 = new Int32Array(nt * 3), ef1 = new Int32Array(nt * 3), ew0 = new Int32Array(nt * 3), ew1 = new Int32Array(nt * 3);
  const eseam = new Uint8Array(nt * 3);
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) {
    const c0 = t * 3 + k, c1 = t * 3 + (k + 1) % 3;
    const a = tv[c0], b = tv[c1];
    const before = et.count, e = et.id(a, b);
    const wa = a < b ? tw[c0] : tw[c1], wb = a < b ? tw[c1] : tw[c0];
    if (et.count !== before) { ea[e] = Math.min(a, b); eb[e] = Math.max(a, b); ef0[e] = t; ew0[e] = wa; ew1[e] = wb; ecnt[e] = 1; }
    else {
      ecnt[e]++;
      if (ecnt[e] === 2) ef1[e] = t;
      if (ew0[e] !== wa || ew1[e] !== wb) eseam[e] = 1;
    }
  }
  const ne = et.count;
  const nBorder = new Int32Array(nv), nSeam = new Int32Array(nv), nFin = new Int32Array(nv), nEdge = new Int32Array(nv);
  for (let e = 0; e < ne; e++) {
    const a = ea[e], b = eb[e];
    nEdge[a]++; nEdge[b]++;
    if (ecnt[e] === 1) { nBorder[a]++; nBorder[b]++; }
    else if (ecnt[e] > 2) { nFin[a]++; nFin[b]++; }
    else if (eseam[e]) { nSeam[a]++; nSeam[b]++; }
  }
  const wCount = new Int32Array(nv), wSeen = new Uint8Array(nw);
  for (let c = 0; c < nt * 3; c++) { const w = tw[c]; if (!wSeen[w]) { wSeen[w] = 1; wCount[tv[c]]++; } }
  // What each vertex may do. A border vertex slides along its border, a seam vertex along its seam
  // (both sides at once), an interior one anywhere; anything else (a corner, a fin, a seam meeting
  // a border, a fan that is not one disc) stays where it is.
  const cls = new Uint8Array(nv);
  for (let v = 0; v < nv; v++) {
    if (!triCount[v] || nFin[v]) cls[v] = LOCKED;
    else if (nBorder[v]) cls[v] = boundary === "lock" || nBorder[v] !== 2 || nSeam[v] || wCount[v] !== 1 || triCount[v] !== nEdge[v] - 1 ? LOCKED : BORDER;
    else if (nSeam[v]) cls[v] = nSeam[v] !== 2 || wCount[v] !== 2 || triCount[v] !== nEdge[v] ? LOCKED : SEAM;
    else cls[v] = wCount[v] !== 1 || triCount[v] !== nEdge[v] ? LOCKED : INTERIOR;
  }

  // ---- quadrics: the faces' planes, area weighted, plus planes through borders, seams and creases
  const Q = new Float64Array(nv * 10), Aw = new Float64Array(nv);
  const addPlane = (v: number, a: number, b: number, c: number, d: number, w: number) => {
    const q = v * 10;
    Q[q] += w * a * a; Q[q + 1] += w * a * b; Q[q + 2] += w * a * c; Q[q + 3] += w * a * d; Q[q + 4] += w * b * b;
    Q[q + 5] += w * b * c; Q[q + 6] += w * b * d; Q[q + 7] += w * c * c; Q[q + 8] += w * c * d; Q[q + 9] += w * d * d;
  };
  const FN = new Float64Array(nt * 3);
  // The original surface each vertex stands for, as an area-weighted normal; merged on collapse, it
  // stops a face from turning away from the surface a little at a time over many collapses.
  const NA = new Float64Array(nv * 3);
  for (let t = 0; t < nt; t++) {
    const a = tv[t * 3] * 3, b = tv[t * 3 + 1] * 3, c = tv[t * 3 + 2] * 3;
    const e1x = P[b] - P[a], e1y = P[b + 1] - P[a + 1], e1z = P[b + 2] - P[a + 2];
    const e2x = P[c] - P[a], e2y = P[c + 1] - P[a + 1], e2z = P[c + 2] - P[a + 2];
    let nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
    const l = Math.sqrt(nx * nx + ny * ny + nz * nz);
    if (!(l > 0)) continue;
    nx /= l; ny /= l; nz /= l;
    FN[t * 3] = nx; FN[t * 3 + 1] = ny; FN[t * 3 + 2] = nz;
    const d = -(nx * P[a] + ny * P[a + 1] + nz * P[a + 2]), area = l / 2;
    for (let k = 0; k < 3; k++) {
      const v = tv[t * 3 + k];
      addPlane(v, nx, ny, nz, d, area); Aw[v] += area;
      NA[v * 3] += nx * area; NA[v * 3 + 1] += ny * area; NA[v * 3 + 2] += nz * area;
    }
  }
  const edgePlane = (e: number, t: number, w: number) => {
    const a = ea[e], b = eb[e];
    const ex = P[b * 3] - P[a * 3], ey = P[b * 3 + 1] - P[a * 3 + 1], ez = P[b * 3 + 2] - P[a * 3 + 2];
    const fx = FN[t * 3], fy = FN[t * 3 + 1], fz = FN[t * 3 + 2];
    let mx = ey * fz - ez * fy, my = ez * fx - ex * fz, mz = ex * fy - ey * fx;
    const l = Math.sqrt(mx * mx + my * my + mz * mz);
    if (!(l > 0)) return;
    mx /= l; my /= l; mz /= l;
    const d = -(mx * P[a * 3] + my * P[a * 3 + 1] + mz * P[a * 3 + 2]), len2 = ex * ex + ey * ey + ez * ez;
    addPlane(a, mx, my, mz, d, w * len2); addPlane(b, mx, my, mz, d, w * len2);
  };
  const cosF = Math.cos((featureAngle * Math.PI) / 180);
  for (let e = 0; e < ne; e++) {
    if (ecnt[e] === 1) edgePlane(e, ef0[e], BORDER_W);
    else if (ecnt[e] === 2) {
      const t0 = ef0[e], t1 = ef1[e];
      if (eseam[e]) { edgePlane(e, t0, SEAM_W); edgePlane(e, t1, SEAM_W); }
      else if (FN[t0 * 3] * FN[t1 * 3] + FN[t0 * 3 + 1] * FN[t1 * 3 + 1] + FN[t0 * 3 + 2] * FN[t1 * 3 + 2] < cosF) {
        edgePlane(e, t0, FEATURE_W); edgePlane(e, t1, FEATURE_W);
      }
    }
  }

  // ---- attribute quadrics per wedge (Hoppe): each face's attributes as a linear field s = g.p + d.
  // Per wedge: [area, then per channel: S g (3), S g g^T (6), S d, S d g (3), S d^2], area weighted.
  const AQ = new Float64Array(K ? nw * AS : 0);
  if (K) for (let t = 0; t < nt; t++) {
    const a = tv[t * 3] * 3, b = tv[t * 3 + 1] * 3, c = tv[t * 3 + 2] * 3;
    const w0 = tw[t * 3], w1 = tw[t * 3 + 1], w2 = tw[t * 3 + 2];
    const e1x = P[b] - P[a], e1y = P[b + 1] - P[a + 1], e1z = P[b + 2] - P[a + 2];
    const e2x = P[c] - P[a], e2y = P[c + 1] - P[a + 1], e2z = P[c + 2] - P[a + 2];
    const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
    const A2 = nx * nx + ny * ny + nz * nz;
    if (!(A2 > 0)) continue;
    const area = Math.sqrt(A2) / 2;
    // g . e1 = s1 - s0 and g . e2 = s2 - s0 with g in the face's plane.
    const px = e2y * nz - e2z * ny, py = e2z * nx - e2x * nz, pz = e2x * ny - e2y * nx;   // e2 x n
    const qx = ny * e1z - nz * e1y, qy = nz * e1x - nx * e1z, qz = nx * e1y - ny * e1x;   // n x e1
    for (const w of [w0, w1, w2]) AQ[w * AS] += area;
    for (let j = 0; j < K; j++) {
      const s0 = wAttr[w0 * K + j], d1 = wAttr[w1 * K + j] - s0, d2 = wAttr[w2 * K + j] - s0;
      const gx = (d1 * px + d2 * qx) / A2, gy = (d1 * py + d2 * qy) / A2, gz = (d1 * pz + d2 * qz) / A2;
      const d = s0 - (gx * P[a] + gy * P[a + 1] + gz * P[a + 2]);
      for (const w of [w0, w1, w2]) {
        const o = w * AS + 1 + 14 * j;
        AQ[o] += area * gx; AQ[o + 1] += area * gy; AQ[o + 2] += area * gz;
        AQ[o + 3] += area * gx * gx; AQ[o + 4] += area * gx * gy; AQ[o + 5] += area * gx * gz;
        AQ[o + 6] += area * gy * gy; AQ[o + 7] += area * gy * gz; AQ[o + 8] += area * gz * gz;
        AQ[o + 9] += area * d; AQ[o + 10] += area * d * gx; AQ[o + 11] += area * d * gy; AQ[o + 12] += area * d * gz;
        AQ[o + 13] += area * d * d;
      }
    }
  }
  /** Add the attribute error of wedges wa+wb, the attributes left free, into the quadric q. */
  const attrInto = (wa: number, wb: number, q: Float64Array) => {
    const sw = AQ[wa * AS] + AQ[wb * AS];
    if (!(sw > 0)) return;
    for (let j = 0; j < K; j++) {
      const oa = wa * AS + 1 + 14 * j, ob = wb * AS + 1 + 14 * j, k = cw[j];
      const Sx = AQ[oa] + AQ[ob], Sy = AQ[oa + 1] + AQ[ob + 1], Sz = AQ[oa + 2] + AQ[ob + 2];
      const Sd = AQ[oa + 9] + AQ[ob + 9];
      q[0] += k * (AQ[oa + 3] + AQ[ob + 3] - (Sx * Sx) / sw);
      q[1] += k * (AQ[oa + 4] + AQ[ob + 4] - (Sx * Sy) / sw);
      q[2] += k * (AQ[oa + 5] + AQ[ob + 5] - (Sx * Sz) / sw);
      q[3] += k * (AQ[oa + 10] + AQ[ob + 10] - (Sx * Sd) / sw);
      q[4] += k * (AQ[oa + 6] + AQ[ob + 6] - (Sy * Sy) / sw);
      q[5] += k * (AQ[oa + 7] + AQ[ob + 7] - (Sy * Sz) / sw);
      q[6] += k * (AQ[oa + 11] + AQ[ob + 11] - (Sy * Sd) / sw);
      q[7] += k * (AQ[oa + 8] + AQ[ob + 8] - (Sz * Sz) / sw);
      q[8] += k * (AQ[oa + 12] + AQ[ob + 12] - (Sz * Sd) / sw);
      q[9] += k * (AQ[oa + 13] + AQ[ob + 13] - (Sd * Sd) / sw);
    }
  };
  /** The attribute error of wedges wa+wb at p with the attributes held at wedge wk's. */
  const attrAt = (wa: number, wb: number, wk: number, x: number, y: number, z: number): number => {
    const sw = AQ[wa * AS] + AQ[wb * AS];
    let e = 0;
    for (let j = 0; j < K; j++) {
      const oa = wa * AS + 1 + 14 * j, ob = wb * AS + 1 + 14 * j, s = wAttr[wk * K + j];
      const Sx = AQ[oa] + AQ[ob], Sy = AQ[oa + 1] + AQ[ob + 1], Sz = AQ[oa + 2] + AQ[ob + 2];
      const pAp = (AQ[oa + 3] + AQ[ob + 3]) * x * x + 2 * (AQ[oa + 4] + AQ[ob + 4]) * x * y + 2 * (AQ[oa + 5] + AQ[ob + 5]) * x * z
        + (AQ[oa + 6] + AQ[ob + 6]) * y * y + 2 * (AQ[oa + 7] + AQ[ob + 7]) * y * z + (AQ[oa + 8] + AQ[ob + 8]) * z * z;
      const lin = 2 * ((AQ[oa + 10] + AQ[ob + 10]) * x + (AQ[oa + 11] + AQ[ob + 11]) * y + (AQ[oa + 12] + AQ[ob + 12]) * z);
      const Sd = AQ[oa + 9] + AQ[ob + 9];
      e += cw[j] * Math.max(0, sw * s * s - 2 * s * (Sx * x + Sy * y + Sz * z + Sd) + pAp + lin + AQ[oa + 13] + AQ[ob + 13]);
    }
    return e;
  };
  const qEval = (q: Float64Array, x: number, y: number, z: number) =>
    q[0] * x * x + 2 * q[1] * x * y + 2 * q[2] * x * z + 2 * q[3] * x + q[4] * y * y + 2 * q[5] * y * z + 2 * q[6] * y + q[7] * z * z + 2 * q[8] * z + q[9];

  // ---- the collapse machinery
  const alive = new Uint8Array(nv).fill(1), ver = new Int32Array(nv);
  const stamp = new Int32Array(nv), stamp2 = new Int32Array(nv);
  let stampId = 0, stampId2 = 0;
  const shT = [0, 0, 0], pairR = [0, 0], pairK = [0, 0];
  let shN = 0, pairN = 0;
  let eCost = 0, eErr = 0, eRem = 0, eKeep = 0, eFull = false, eX = 0, eY = 0, eZ = 0;
  const q = new Float64Array(10);
  const tiny = 1e-14 * size * size;
  let qualityGuard = true;

  const prune = (v: number) => {
    let prev = -1;
    for (let c = head[v]; c >= 0;) {
      const nx = cnext[c];
      if (!talive[(c / 3) | 0]) { if (prev < 0) head[v] = nx; else cnext[prev] = nx; } else prev = c;
      c = nx;
    }
  };
  const wedgeIn = (t: number, x: number) => (tv[t * 3] === x ? tw[t * 3] : tv[t * 3 + 1] === x ? tw[t * 3 + 1] : tw[t * 3 + 2]);
  const movable = (x: number, border: boolean, seam: boolean) => {
    const k = cls[x];
    return k === INTERIOR ? !border && !seam : k === BORDER ? border : k === SEAM ? seam : false;
  };

  /** Price the edge (u, v): which end goes, where the survivor stands, the cost. False if it cannot go. */
  const evaluate = (u: number, v: number): boolean => {
    prune(u);
    shN = 0;
    for (let c = head[u]; c >= 0; c = cnext[c]) {
      const b0 = ((c / 3) | 0) * 3;
      if (tv[b0] === v || tv[b0 + 1] === v || tv[b0 + 2] === v) { if (shN < 3) shT[shN] = b0 / 3; shN++; }
    }
    if (shN === 0 || shN > 2) return false;
    const border = shN === 1;
    const seam = shN === 2 && (wedgeIn(shT[0], u) !== wedgeIn(shT[1], u) || wedgeIn(shT[0], v) !== wedgeIn(shT[1], v));
    const mu = movable(u, border, seam), mv = movable(v, border, seam);
    if (!mu && !mv) return false;
    const full = mu && mv;
    const rem = mu ? u : v, keep = rem === u ? v : u;
    pairN = 0;
    for (let s = 0; s < shN; s++) {
      const wr = wedgeIn(shT[s], rem);
      if (pairN && pairR[0] === wr) continue;
      pairR[pairN] = wr; pairK[pairN] = wedgeIn(shT[s], keep); pairN++;
    }
    for (let k = 0; k < 10; k++) q[k] = Q[u * 10 + k] + Q[v * 10 + k];
    let cost: number;
    if (full) {
      if (K) for (let k = 0; k < pairN; k++) attrInto(pairR[k], pairK[k], q);
      const ux = P[u * 3], uy = P[u * 3 + 1], uz = P[u * 3 + 2], vx = P[v * 3], vy = P[v * 3 + 1], vz = P[v * 3 + 2];
      const mx = (ux + vx) / 2, my = (uy + vy) / 2, mz = (uz + vz) / 2;
      cost = Infinity;
      // The point that minimises the quadric, when it is well defined.
      const a00 = q[0], a01 = q[1], a02 = q[2], a11 = q[4], a12 = q[5], a22 = q[7];
      const c00 = a11 * a22 - a12 * a12, c01 = a02 * a12 - a01 * a22, c02 = a01 * a12 - a02 * a11;
      const det = a00 * c00 + a01 * c01 + a02 * c02;
      const sc = Math.max(Math.abs(a00), Math.abs(a11), Math.abs(a22));
      if (sc > 0 && Math.abs(det) > 1e-10 * sc * sc * sc) {
        const c11 = a00 * a22 - a02 * a02, c12 = a01 * a02 - a00 * a12, c22 = a00 * a11 - a01 * a01;
        const bx = -q[3], by = -q[6], bz = -q[8];
        let ox = (c00 * bx + c01 * by + c02 * bz) / det, oy = (c01 * bx + c11 * by + c12 * bz) / det, oz = (c02 * bx + c12 * by + c22 * bz) / det;
        const dx = vx - ux, dy = vy - uy, dz = vz - uz, L2 = dx * dx + dy * dy + dz * dz;
        let use = true;
        if (cls[u] !== INTERIOR) {
          // Borders and seams: the survivor stays on the edge it slides along.
          const t = L2 > 0 ? Math.max(0, Math.min(1, ((ox - ux) * dx + (oy - uy) * dy + (oz - uz) * dz) / L2)) : 0.5;
          ox = ux + dx * t; oy = uy + dy * t; oz = uz + dz * t;
        } else if ((ox - mx) ** 2 + (oy - my) ** 2 + (oz - mz) ** 2 > 4 * L2) use = false;
        if (use) { cost = qEval(q, ox, oy, oz); eX = ox; eY = oy; eZ = oz; }
      }
      let c = qEval(q, mx, my, mz);
      if (c < cost) { cost = c; eX = mx; eY = my; eZ = mz; }
      c = qEval(q, ux, uy, uz);
      if (c < cost) { cost = c; eX = ux; eY = uy; eZ = uz; }
      c = qEval(q, vx, vy, vz);
      if (c < cost) { cost = c; eX = vx; eY = vy; eZ = vz; }
    } else {
      eX = P[keep * 3]; eY = P[keep * 3 + 1]; eZ = P[keep * 3 + 2];
      cost = qEval(q, eX, eY, eZ);
      if (K) for (let k = 0; k < pairN; k++) cost += attrAt(pairR[k], pairK[k], pairK[k], eX, eY, eZ);
    }
    eCost = cost > 0 ? cost : 0;
    eErr = Math.sqrt(eCost / Math.max(Aw[u] + Aw[v], 1e-30));
    eRem = rem; eKeep = keep; eFull = full;
    return true;
  };

  /** Faces around x (not the ones on the edge to `other`) with x moved to the survivor's place:
   *  false if one would turn over or vanish. Tracks the worst shape before and after. */
  let qBefore = Infinity, qAfter = Infinity;
  const faceCheck = (x: number, other: number): boolean => {
    for (let c = head[x]; c >= 0; c = cnext[c]) {
      const t = (c / 3) | 0;
      if (!talive[t]) continue;
      const i0 = tv[t * 3], i1 = tv[t * 3 + 1], i2 = tv[t * 3 + 2];
      if (i0 === other || i1 === other || i2 === other) continue;
      const ax = P[i0 * 3], ay = P[i0 * 3 + 1], az = P[i0 * 3 + 2];
      const bx = P[i1 * 3], by = P[i1 * 3 + 1], bz = P[i1 * 3 + 2];
      const cx = P[i2 * 3], cy = P[i2 * 3 + 1], cz = P[i2 * 3 + 2];
      let e1x = bx - ax, e1y = by - ay, e1z = bz - az, e2x = cx - ax, e2y = cy - ay, e2z = cz - az;
      const n0x = e1y * e2z - e1z * e2y, n0y = e1z * e2x - e1x * e2z, n0z = e1x * e2y - e1y * e2x;
      const l0 = Math.sqrt(n0x * n0x + n0y * n0y + n0z * n0z);
      const s0 = e1x * e1x + e1y * e1y + e1z * e1z + e2x * e2x + e2y * e2y + e2z * e2z + (cx - bx) ** 2 + (cy - by) ** 2 + (cz - bz) ** 2;
      // The same triangle with x at the new place.
      const Ax = i0 === x ? eX : ax, Ay = i0 === x ? eY : ay, Az = i0 === x ? eZ : az;
      const Bx = i1 === x ? eX : bx, By = i1 === x ? eY : by, Bz = i1 === x ? eZ : bz;
      const Cx = i2 === x ? eX : cx, Cy = i2 === x ? eY : cy, Cz = i2 === x ? eZ : cz;
      e1x = Bx - Ax; e1y = By - Ay; e1z = Bz - Az; e2x = Cx - Ax; e2y = Cy - Ay; e2z = Cz - Az;
      const n1x = e1y * e2z - e1z * e2y, n1y = e1z * e2x - e1x * e2z, n1z = e1x * e2y - e1y * e2x;
      const l1 = Math.sqrt(n1x * n1x + n1y * n1y + n1z * n1z);
      if (!(l1 > tiny)) return false;
      if (n0x * n1x + n0y * n1y + n0z * n1z < 0.2 * l0 * l1) return false;
      // Against the original surface under the three corners (the moved corner carries both patches).
      let mx = 0, my = 0, mz = 0, ms = 0;
      for (const vi of [i0, i1, i2]) {
        let px = NA[vi * 3], py = NA[vi * 3 + 1], pz = NA[vi * 3 + 2];
        if (vi === x) { px += NA[other * 3]; py += NA[other * 3 + 1]; pz += NA[other * 3 + 2]; }
        mx += px; my += py; mz += pz; ms += Math.sqrt(px * px + py * py + pz * pz);
      }
      const ml = Math.sqrt(mx * mx + my * my + mz * mz);
      if (ml > 0.3 * ms && n1x * mx + n1y * my + n1z * mz < 0.2 * l1 * ml) return false;
      const s1 = e1x * e1x + e1y * e1y + e1z * e1z + e2x * e2x + e2y * e2y + e2z * e2z + (Cx - Bx) ** 2 + (Cy - By) ** 2 + (Cz - Bz) ** 2;
      const qb = s0 > 0 ? (3.4641016 * l0) / s0 : 0, qa = s1 > 0 ? (3.4641016 * l1) / s1 : 0;
      if (qb < qBefore) qBefore = qb;
      if (qa < qAfter) qAfter = qa;
    }
    return true;
  };

  /** The link condition (no pinch), a fan left round every vertex opposite the edge, no flips. */
  const validCollapse = (): boolean => {
    const rem = eRem, keep = eKeep;
    prune(rem); prune(keep);
    stampId++;
    for (let c = head[rem]; c >= 0; c = cnext[c]) {
      const b0 = ((c / 3) | 0) * 3;
      for (let k = 0; k < 3; k++) stamp[tv[b0 + k]] = stampId;
    }
    stampId2++;
    let common = 0;
    for (let c = head[keep]; c >= 0; c = cnext[c]) {
      const b0 = ((c / 3) | 0) * 3;
      for (let k = 0; k < 3; k++) {
        const o = tv[b0 + k];
        if (o === keep || o === rem || stamp[o] !== stampId || stamp2[o] === stampId2) continue;
        stamp2[o] = stampId2; common++;
      }
    }
    if (common !== shN) return false;
    for (let s = 0; s < shN; s++) {
      const b0 = shT[s] * 3;
      const o = tv[b0] !== rem && tv[b0] !== keep ? tv[b0] : tv[b0 + 1] !== rem && tv[b0 + 1] !== keep ? tv[b0 + 1] : tv[b0 + 2];
      if (nBorder[o] === 0 ? triCount[o] <= 3 : triCount[o] <= 1) return false;
    }
    qBefore = Infinity; qAfter = Infinity;
    if (!faceCheck(rem, keep)) return false;
    if (eFull && !faceCheck(keep, rem)) return false;
    if (qualityGuard && qAfter < 0.05 && qAfter < qBefore) return false;
    if (UVO && !uvCheck()) return false;
    return true;
  };

  /** With UVs: no face around the collapse may turn over in UV space (a mirrored patch of texture). */
  const newUv = new Float64Array(4);
  const uvCheck = (): boolean => {
    const rem = eRem, keep = eKeep;
    // The UVs the kept wedges will have: the best fit at the new place for a full collapse.
    for (let k = 0; k < pairN; k++) {
      const w = pairK[k], r = pairR[k];
      newUv[k * 2] = wAttr[w * K]; newUv[k * 2 + 1] = wAttr[w * K + 1];
      const sw = AQ[r * AS] + AQ[w * AS];
      if (eFull && sw > 0) for (let j = 0; j < 2; j++) {
        const oa = r * AS + 1 + 14 * j, ob = w * AS + 1 + 14 * j;
        newUv[k * 2 + j] = ((AQ[oa] + AQ[ob]) * eX + (AQ[oa + 1] + AQ[ob + 1]) * eY + (AQ[oa + 2] + AQ[ob + 2]) * eZ + AQ[oa + 9] + AQ[ob + 9]) / sw;
      }
    }
    const us = [0, 0, 0], vs = [0, 0, 0];
    for (let pass = 0; pass < (eFull ? 2 : 1); pass++) {
      const x = pass === 0 ? rem : keep, other = pass === 0 ? keep : rem;
      for (let c = head[x]; c >= 0; c = cnext[c]) {
        const t = (c / 3) | 0;
        if (!talive[t]) continue;
        const b0 = t * 3;
        if (tv[b0] === other || tv[b0 + 1] === other || tv[b0 + 2] === other) continue;
        for (let k = 0; k < 3; k++) { const w = tw[b0 + k]; us[k] = wAttr[w * K]; vs[k] = wAttr[w * K + 1]; }
        const before = (us[1] - us[0]) * (vs[2] - vs[0]) - (us[2] - us[0]) * (vs[1] - vs[0]);
        for (let k = 0; k < 3; k++) {
          const w = tw[b0 + k], vx = tv[b0 + k];
          if (vx !== rem && vx !== keep) continue;
          for (let q = 0; q < pairN; q++) {
            if ((vx === rem && pairR[q] === w) || (vx === keep && eFull && pairK[q] === w)) { us[k] = newUv[q * 2]; vs[k] = newUv[q * 2 + 1]; break; }
          }
        }
        const after = (us[1] - us[0]) * (vs[2] - vs[0]) - (us[2] - us[0]) * (vs[1] - vs[0]);
        if (Math.abs(before) > 1e-14 && before * after <= 0) return false;
      }
    }
    return true;
  };

  let aliveTris = nt, collapsed = 0, maxErr = 0;
  const doCollapse = () => {
    const rem = eRem, keep = eKeep;
    for (let s = 0; s < shN; s++) {
      const t = shT[s];
      talive[t] = 0; aliveTris--;
      for (let k = 0; k < 3; k++) triCount[tv[t * 3 + k]]--;
    }
    let last = -1;
    for (let c = head[rem]; c >= 0; c = cnext[c]) {
      last = c;
      if (!talive[(c / 3) | 0]) continue;
      tv[c] = keep; triCount[keep]++;
      const w = tw[c];
      let m = -1;
      for (let k = 0; k < pairN; k++) if (pairR[k] === w) m = pairK[k];
      if (m >= 0) tw[c] = m; else wVid[w] = keep;
    }
    triCount[rem] = 0;
    if (last >= 0) { cnext[last] = head[keep]; head[keep] = head[rem]; }
    head[rem] = -1;
    for (let k = 0; k < 10; k++) Q[keep * 10 + k] += Q[rem * 10 + k];
    Aw[keep] += Aw[rem];
    NA[keep * 3] += NA[rem * 3]; NA[keep * 3 + 1] += NA[rem * 3 + 1]; NA[keep * 3 + 2] += NA[rem * 3 + 2];
    if (K) for (let k = 0; k < pairN; k++) { const a = pairR[k] * AS, b = pairK[k] * AS; for (let j = 0; j < AS; j++) AQ[b + j] += AQ[a + j]; }
    if (eFull) {
      P[keep * 3] = eX; P[keep * 3 + 1] = eY; P[keep * 3 + 2] = eZ;
      // Each kept wedge takes the attributes that fit its (merged) faces best at the new place.
      if (K) for (let k = 0; k < pairN; k++) {
        const w = pairK[k], sw = AQ[w * AS];
        if (!(sw > 0)) continue;
        for (let j = 0; j < K; j++) {
          const o = w * AS + 1 + 14 * j;
          wAttr[w * K + j] = (AQ[o] * eX + AQ[o + 1] * eY + AQ[o + 2] * eZ + AQ[o + 9]) / sw;
        }
      }
    }
    alive[rem] = 0; ver[keep]++;
  };

  const heap = new EdgeHeap(ne * 2);
  const nbr: number[] = [];
  const pushAround = (x: number) => {
    prune(x);
    stampId++;
    nbr.length = 0;
    for (let c = head[x]; c >= 0; c = cnext[c]) {
      const b0 = ((c / 3) | 0) * 3;
      for (let k = 0; k < 3; k++) { const o = tv[b0 + k]; if (o !== x && stamp[o] !== stampId) { stamp[o] = stampId; nbr.push(o); } }
    }
    for (const o of nbr) if (evaluate(x, o)) heap.push(eCost, x, o, ver[x], ver[o]);
  };
  const maxError = typeof opts.maxError === "number" && opts.maxError >= 0 ? opts.maxError : Infinity;
  for (let round = 0; round < 8 && aliveTris > target; round++) {
    heap.size = 0;
    for (let v = 0; v < nv; v++) {
      if (!alive[v] || !triCount[v]) continue;
      prune(v);
      stampId++;
      nbr.length = 0;
      for (let c = head[v]; c >= 0; c = cnext[c]) {
        const b0 = ((c / 3) | 0) * 3;
        for (let k = 0; k < 3; k++) { const o = tv[b0 + k]; if (o > v && stamp[o] !== stampId) { stamp[o] = stampId; nbr.push(o); } }
      }
      for (const o of nbr) if (evaluate(v, o)) heap.push(eCost, v, o, ver[v], ver[o]);
    }
    const before = collapsed;
    while (heap.size && aliveTris > target) {
      const i = heap.pop();
      const u = heap.u[i], v = heap.v[i];
      if (!alive[u] || !alive[v] || ver[u] !== heap.vu[i] || ver[v] !== heap.vv[i]) continue;
      if (!evaluate(u, v) || eErr > maxError || !validCollapse()) continue;
      const keep = eKeep, err = eErr;
      doCollapse();
      collapsed++;
      if (err > maxErr) maxErr = err;
      pushAround(keep);
    }
    if (collapsed === before) {
      if (!qualityGuard) break;
      qualityGuard = false;   // stuck on shape alone: let thin triangles through for the rest
    }
  }

  // ---- out: one vertex per wedge still in use
  const outOf = new Int32Array(nw).fill(-1), order = new Int32Array(nw);
  const outIdx = new Uint32Array(aliveTris * 3);
  let no = 0, io = 0;
  for (let t = 0; t < nt; t++) {
    if (!talive[t]) continue;
    for (let k = 0; k < 3; k++) {
      const w = tw[t * 3 + k];
      if (outOf[w] < 0) { outOf[w] = no; order[no++] = w; }
      outIdx[io++] = outOf[w];
    }
  }
  const outPos = new Float32Array(no * 3);
  for (let i = 0; i < no; i++) { const v = wVid[order[i]] * 3; outPos[i * 3] = P[v]; outPos[i * 3 + 1] = P[v + 1]; outPos[i * 3 + 2] = P[v + 2]; }
  const res: Decimated = { pos: outPos, idx: outIdx.slice(0, io), collapsed, error: maxErr };
  if (uvIn) {
    const uv = new Float32Array(no * 2);
    for (let i = 0; i < no; i++) { uv[i * 2] = wAttr[order[i] * K]; uv[i * 2 + 1] = wAttr[order[i] * K + 1]; }
    res.uv = uv;
  }
  if (colIn) {
    const col = new Float32Array(no * cc);
    for (let i = 0; i < no; i++) for (let j = 0; j < cc; j++) col[i * cc + j] = wAttr[order[i] * K + UVO + j];
    res.color = col;
  }
  return res;
}
