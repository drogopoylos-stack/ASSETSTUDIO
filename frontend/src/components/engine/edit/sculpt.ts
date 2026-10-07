// Sculpting on plain arrays: the brushes of a sculpt program, as data an agent can write.
//
// The goblin A/B (data/ab/goblin) came down to this. The Blender builder had no sculpt brushes
// headless either, so it wrote them in numpy - grooves along 3D polylines pushed along the normals,
// Gaussian pushes, noise - on a 2.2 mm voxel remesh of the head (260k triangles), decimated that to
// 6.8k and baked the sculpt onto it: the GLB's face shows wrinkles the low mesh does not have. The
// forge builder had none of it: "every wrinkle is a hand-placed capsule".
//
// These are those brushes, done the way a sculpt program does them:
//   - a stroke follows a 3D polyline (or sits at a point) that is laid onto the surface first, so a
//     path written from rough coordinates still cuts at the depth asked;
//   - the directional brushes push along the surface's AREA normal under the path (the average over
//     the radius), the way Blender's Draw and Crease do, so an old bump is not amplified;
//   - noise is a function of position, never of vertex order: the same surface sculpts the same
//     however it is indexed;
//   - mirror measures both copies on the same surface and unites them, so a stroke that crosses the
//     mirror plane is not applied twice and a symmetric mesh stays exactly symmetric;
//   - `densifyArrays` gives a coarse mesh the vertices a stroke needs, by longest-edge bisection
//     (no slivers), keeping a seam made of vertex copies closed.
// Plain arrays in, plain arrays out; nothing here imports an engine.

import type { V3 } from "./ops";

/**
 * A sculpt stroke: crease or ridge along `path`, or inflate, pinch, smooth, flatten, noise, move or
 * clay at `at` or along `path`, within `radius` metres; `depth` is + out, - in; no path and no at
 * means the whole mesh. Strokes run in order, each on the surface the one before it left.
 *
 * - `crease` cuts a groove along `path` (a wrinkle, a lid line, a toe gap): V-bottomed and soft at
 *   the shoulders by default, `depth` deep (default -0.35 * radius), sides drawn in by `strength`.
 * - `ridge` raises a welt along `path` (a brow ridge, a vein, a lip edge): `depth` high (default
 *   +0.35 * radius), round-topped by default.
 * - `inflate` pushes every vertex out along its own normal by up to `depth` (default 0.2 * radius):
 *   cheeks, knuckles, a swollen lid. A negative depth deflates.
 * - `pinch` draws the surface toward the path (or point) by `strength` (0..0.95, default 0.5):
 *   sharpens a crease or a ridge that is already there.
 * - `smooth` evens the surface (`strength` 0..1, default 0.5): a brush takes out dents, grooves and
 *   lumps up to its own size and keeps the bend of the surface under them, so it does not sink; with
 *   no `path` and no `at` it smooths the whole mesh's noise and keeps its volume.
 * - `flatten` pulls toward the plane of the surface under the brush (or the plane normal to `dir`)
 *   by `strength` (default 1): the planes of a helmet, a flattened nose bridge.
 * - `noise` displaces along the normals by coherent 3D noise of the position: `freq` cycles per
 *   metre (default 3 / radius), `octaves` (3), `seed` (1), amplitude `depth` (default
 *   0.1 * radius); `dir` stretches it into streaks along that direction. Skin lumps, scruff, dents.
 * - `move` shifts the region by `dir` (metres), fading with the falloff: pull an ear tip, a lip.
 * - `clay` builds material up to a flat top `depth` above the plane of the surface under the brush
 *   (default 0.25 * radius), or scrapes down to it with a negative depth: brow masses, pads.
 *
 * `path` is a 3D polyline and `at` a single dab; either is laid onto the surface before the stroke:
 * each point is dropped onto the plane of the surface nearest it (found up to 8 times the radius
 * away, else the stroke throws), so rough coordinates still cut at the depth asked. No `path` and no
 * `at` means the whole mesh (inflate, noise, smooth and move only). Nothing farther than `radius`
 * from the laid path moves. Densify first so edges are shorter than a third of the smallest radius.
 */
export interface Stroke {
  /** The brush; see the list above. */
  kind: "crease" | "ridge" | "inflate" | "pinch" | "smooth" | "flatten" | "noise" | "move" | "clay";
  /** The polyline to follow, metres (crease, ridge, pinch, clay; optional for the rest). */
  path?: V3[];
  /** A point, for a dab when there is no path. */
  at?: V3;
  /** Influence radius in metres: a groove's half-width, a dab's reach. Nothing farther moves. */
  radius: number;
  /** Along the normal in metres, + out and - in. Defaults per kind are listed above. */
  depth?: number;
  /** 0..1: smooth passes, pinch pull, flatten and clay reach, a crease's pull of its sides. */
  strength?: number;
  /** How the effect fades out to the radius (default 'smooth'; clay's default is 'plateau', full strength
   *  to half the radius, which gives it a flat top). A crease or ridge uses `profile` instead. */
  falloff?: "smooth" | "sharp" | "gauss" | "linear" | "constant" | "plateau";
  /** Cross-section of a crease or ridge: 'v' sharp bottom with soft shoulders (crease default),
   *  'u' rounded (ridge default), 'round' a half circle (a cord, or a gouge). */
  profile?: "v" | "u" | "round";
  /** Scale of the radius AND the depth at the path's start and end: [1, 0.2] is a wrinkle that fades out. */
  taper?: [number, number];
  /** move: the offset in metres. noise: the streak direction. flatten: the plane's normal. */
  dir?: V3;
  /** noise: cycles per metre (default 3 / radius). */
  freq?: number;
  /** noise: layers of finer detail (default 3). */
  octaves?: number;
  /** noise: which noise (default 1). The same seed at the same positions gives the same displacement. */
  seed?: number;
  /** Apply again mirrored across the plane x = 0 ('x'), y = 0 or z = 0; the two copies unite. */
  mirror?: "x" | "y" | "z";
  /** 0..1 weight per vertex from its position and normal before the stroke; mirrored with the stroke. */
  mask?: (p: V3, n: V3) => number;
  /** Follow a smooth curve through the path points (centripetal Catmull-Rom) instead of straight segments. */
  curve?: boolean;
  /** noise with `dir`: how many times longer a streak is than it is wide (default 6). */
  stretch?: number;
}

// ================================================================== welding
//
// A procedural mesh holds several copies of a corner (split for UVs or hard edges). A stroke must
// move all of them together or the seam opens, so everything here works on one vertex per position.

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

/** One id per position: vertices within `tol` of an earlier one take its id. The 27 cells around
 *  each point are searched, so a pair that straddles a cell border still finds each other. */
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

/** Open addressing on an unordered pair of ints: the edge tables of densify. */
class PairTable {
  ka!: Int32Array; kb!: Int32Array; val!: Int32Array; mask = 0; count = 0;
  constructor(expected: number) { this.alloc(expected); }
  private alloc(expected: number) {
    let s = 64;
    while (s < expected * 2) s *= 2;
    this.ka = new Int32Array(s).fill(-1); this.kb = new Int32Array(s); this.val = new Int32Array(s); this.mask = s - 1;
  }
  private slot(a: number, b: number): number {
    let h = Math.imul(a, 0x9e3779b1) ^ Math.imul(b + 0x7f4a7c15, 0x85ebca77);
    h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
    h ^= h >>> 12;
    return (h >>> 0) & this.mask;
  }
  /** The value stored for (a, b), or -1. */
  get(a: number, b: number): number {
    if (a > b) { const t = a; a = b; b = t; }
    for (let h = this.slot(a, b); ; h = (h + 1) & this.mask) {
      const k = this.ka[h];
      if (k === -1) return -1;
      if (k === a && this.kb[h] === b) return this.val[h];
    }
  }
  /** Store `v` for (a, b) (which must not be there yet). */
  set(a: number, b: number, v: number): void {
    if ((this.count + 1) * 2 > this.mask + 1) this.grow();
    if (a > b) { const t = a; a = b; b = t; }
    let h = this.slot(a, b);
    while (this.ka[h] !== -1) h = (h + 1) & this.mask;
    this.ka[h] = a; this.kb[h] = b; this.val[h] = v; this.count++;
  }
  /** The id of (a, b), numbered in order of first sight. */
  id(a: number, b: number): number {
    const v = this.get(a, b);
    if (v >= 0) return v;
    const id = this.count;
    this.set(a, b, id);
    return id;
  }
  private grow() {
    const { ka, kb, val } = this;
    this.alloc(this.mask + 1);
    const n = this.count; this.count = 0;
    for (let h = 0; h < ka.length; h++) if (ka[h] !== -1) this.set(ka[h], kb[h], val[h]);
    this.count = n;
  }
}

// ================================================================== the welded surface

interface WMesh {
  /** Welded vertices. */
  n: number;
  /** Input vertex -> welded vertex. */
  map: Uint32Array;
  /** Welded positions as given. */
  P0: Float64Array;
  /** Welded triangles, the ones that collapsed in the weld dropped. */
  T: Uint32Array;
  /** Unique neighbours, CSR. */
  off: Uint32Array; adj: Uint32Array;
  /** 1 for a vertex on an open edge. */
  border: Uint8Array;
  meanEdge: number;
}

function weldMesh(pos: Float32Array, idx: Uint32Array): WMesh {
  const ext = extentOf(pos);
  const { map, rep, count: n } = weldIds(pos, Math.max(1e-12, ext * 1e-6));
  const P0 = new Float64Array(n * 3);
  for (let w = 0; w < n; w++) { const i = rep[w] * 3; P0[w * 3] = pos[i]; P0[w * 3 + 1] = pos[i + 1]; P0[w * 3 + 2] = pos[i + 2]; }
  const T0 = new Uint32Array(idx.length - (idx.length % 3));
  let m = 0;
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const a = map[idx[t]], b = map[idx[t + 1]], c = map[idx[t + 2]];
    if (a === b || b === c || a === c) continue;
    T0[m++] = a; T0[m++] = b; T0[m++] = c;
  }
  const T = T0.slice(0, m);
  // Every triangle lists each corner's two partners; an edge seen once is an open edge.
  const roff = new Uint32Array(n + 1);
  for (let i = 0; i < m; i++) roff[T[i] + 1] += 2;
  for (let v = 0; v < n; v++) roff[v + 1] += roff[v];
  const raw = new Uint32Array(roff[n]);
  const fill = roff.slice(0, n);
  for (let t = 0; t < m; t += 3) {
    const a = T[t], b = T[t + 1], c = T[t + 2];
    raw[fill[a]++] = b; raw[fill[a]++] = c;
    raw[fill[b]++] = c; raw[fill[b]++] = a;
    raw[fill[c]++] = a; raw[fill[c]++] = b;
  }
  const off = new Uint32Array(n + 1), adj = new Uint32Array(raw.length), border = new Uint8Array(n);
  let k = 0, sum = 0, cnt = 0;
  for (let v = 0; v < n; v++) {
    const s = roff[v], e = roff[v + 1];
    for (let i = s + 1; i < e; i++) {
      const x = raw[i];
      let j = i - 1;
      while (j >= s && raw[j] > x) { raw[j + 1] = raw[j]; j--; }
      raw[j + 1] = x;
    }
    off[v] = k;
    for (let i = s; i < e;) {
      const x = raw[i];
      let j = i + 1;
      while (j < e && raw[j] === x) j++;
      if (j - i === 1) border[v] = 1;
      adj[k++] = x;
      if (x > v) {
        sum += Math.sqrt((P0[x * 3] - P0[v * 3]) ** 2 + (P0[x * 3 + 1] - P0[v * 3 + 1]) ** 2 + (P0[x * 3 + 2] - P0[v * 3 + 2]) ** 2);
        cnt++;
      }
      i = j;
    }
  }
  off[n] = k;
  return { n, map, P0, T, off, adj: adj.slice(0, k), border, meanEdge: cnt ? sum / cnt : Math.max(ext * 0.01, 1e-6) };
}

/** Area-weighted vertex normals: A holds the sums (their length is the area around the vertex), N the unit vectors. */
function normalsInto(P: Float64Array, T: Uint32Array, A: Float64Array, N: Float64Array): void {
  A.fill(0);
  for (let t = 0; t < T.length; t += 3) {
    const a = T[t] * 3, b = T[t + 1] * 3, c = T[t + 2] * 3;
    const e1x = P[b] - P[a], e1y = P[b + 1] - P[a + 1], e1z = P[b + 2] - P[a + 2];
    const e2x = P[c] - P[a], e2y = P[c + 1] - P[a + 1], e2z = P[c + 2] - P[a + 2];
    const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
    A[a] += nx; A[a + 1] += ny; A[a + 2] += nz;
    A[b] += nx; A[b + 1] += ny; A[b + 2] += nz;
    A[c] += nx; A[c + 1] += ny; A[c + 2] += nz;
  }
  for (let i = 0; i < A.length; i += 3) {
    const l = Math.sqrt(A[i] * A[i] + A[i + 1] * A[i + 1] + A[i + 2] * A[i + 2]);
    if (l > 0) { N[i] = A[i] / l; N[i + 1] = A[i + 1] / l; N[i + 2] = A[i + 2] / l; } else { N[i] = 0; N[i + 1] = 0; N[i + 2] = 0; }
  }
}

// ================================================================== densify

/**
 * Split every edge longer than `maxEdge` (metres), pass after pass until none is, so strokes have
 * vertices to move. Longest-edge bisection (Rivara): a triangle is always cut across its longest
 * edge, so no pass makes a sliver, and the surface does not move - every new vertex is the midpoint
 * of an edge. Copies of a vertex (a UV seam, a hard edge) are split alike, so the seam stays closed
 * when the result is sculpted. 20k -> 200k triangles takes well under two seconds.
 */
export function densifyArrays(pos: Float32Array, idx: Uint32Array, maxEdge: number): { pos: Float32Array; idx: Uint32Array } {
  if (!(maxEdge > 0)) throw new Error("densifyArrays: maxEdge must be a length above zero, in metres");
  const n0 = Math.floor(pos.length / 3);
  const weld = weldIds(pos, Math.max(1e-12, extentOf(pos) * 1e-6));
  let cap = Math.max(64, n0 * 2);
  let P = new Float64Array(cap * 3);
  let C = new Int32Array(cap);          // the weld id of every vertex, midpoints included
  for (let i = 0; i < n0 * 3; i++) P[i] = pos[i];
  for (let i = 0; i < n0; i++) C[i] = weld.map[i];
  let nv = n0, nc = weld.count;
  let tris: Uint32Array;
  {
    const t0 = new Uint32Array(idx.length - (idx.length % 3));
    let m = 0;
    for (let t = 0; t + 2 < idx.length; t += 3) {
      const a = idx[t], b = idx[t + 1], c = idx[t + 2];
      if (a >= n0 || b >= n0 || c >= n0) continue;
      if (C[a] === C[b] || C[b] === C[c] || C[a] === C[c]) continue;
      t0[m++] = a; t0[m++] = b; t0[m++] = c;
    }
    tris = t0.slice(0, m);
  }
  const max2 = maxEdge * maxEdge;
  const grow = () => {
    cap *= 2;
    const P2 = new Float64Array(cap * 3); P2.set(P); P = P2;
    const C2 = new Int32Array(cap); C2.set(C); C = C2;
  };
  for (let pass = 0; pass < 48; pass++) {
    const nt = tris.length / 3;
    // Edges by weld id, so both sides of a seam are one edge and are split alike.
    const et = new PairTable(nt * 2);
    const te = new Int32Array(nt * 3);
    const len2 = new Float64Array(nt * 3);
    for (let t = 0; t < nt; t++) {
      for (let k = 0; k < 3; k++) {
        const i = tris[t * 3 + k], j = tris[t * 3 + (k + 1) % 3];
        const before = et.count;
        const e = et.id(C[i], C[j]);
        te[t * 3 + k] = e;
        if (et.count !== before) len2[e] = (P[i * 3] - P[j * 3]) ** 2 + (P[i * 3 + 1] - P[j * 3 + 1]) ** 2 + (P[i * 3 + 2] - P[j * 3 + 2]) ** 2;
      }
    }
    const ne = et.count;
    const mark = new Uint8Array(ne);
    let any = false;
    for (let e = 0; e < ne; e++) if (len2[e] > max2) { mark[e] = 1; any = true; }
    if (!any) break;
    const longest = new Uint8Array(nt);
    for (let t = 0; t < nt; t++) {
      const l0 = len2[te[t * 3]], l1 = len2[te[t * 3 + 1]], l2 = len2[te[t * 3 + 2]];
      longest[t] = l0 >= l1 && l0 >= l2 ? 0 : l1 >= l2 ? 1 : 2;
    }
    // Rivara's closure: a triangle with any edge to split must be split across its longest.
    const eoff = new Int32Array(ne + 1);
    for (let c = 0; c < nt * 3; c++) eoff[te[c] + 1]++;
    for (let e = 0; e < ne; e++) eoff[e + 1] += eoff[e];
    const etri = new Int32Array(nt * 3), cur = eoff.slice(0, ne);
    for (let c = 0; c < nt * 3; c++) etri[cur[te[c]]++] = (c / 3) | 0;
    const stack: number[] = [];
    for (let t = 0; t < nt; t++) {
      if ((mark[te[t * 3]] | mark[te[t * 3 + 1]] | mark[te[t * 3 + 2]]) && !mark[te[t * 3 + longest[t]]]) stack.push(t);
    }
    while (stack.length) {
      const t = stack.pop()!;
      const e = te[t * 3 + longest[t]];
      if (mark[e]) continue;
      mark[e] = 1;
      for (let q = eoff[e]; q < eoff[e + 1]; q++) {
        const t2 = etri[q];
        if (!mark[te[t2 * 3 + longest[t2]]]) stack.push(t2);
      }
    }
    // Midpoints: one vertex per split edge per index pair (copies stay copies), one weld id per edge.
    const midC = new Int32Array(ne).fill(-1);
    const mt = new PairTable(nt);
    const mid = (i: number, j: number, e: number): number => {
      const have = mt.get(i, j);
      if (have >= 0) return have;
      if (nv >= cap) grow();
      P[nv * 3] = (P[i * 3] + P[j * 3]) * 0.5;
      P[nv * 3 + 1] = (P[i * 3 + 1] + P[j * 3 + 1]) * 0.5;
      P[nv * 3 + 2] = (P[i * 3 + 2] + P[j * 3 + 2]) * 0.5;
      if (midC[e] < 0) midC[e] = nc++;
      C[nv] = midC[e];
      mt.set(i, j, nv);
      return nv++;
    };
    let grows = nt;
    for (let t = 0; t < nt; t++) grows += mark[te[t * 3]] + mark[te[t * 3 + 1]] + mark[te[t * 3 + 2]];
    if (grows > 20e6) {
      throw new Error(`densifyArrays: maxEdge ${maxEdge} m would make more than 20 million triangles (the mesh is ${extentOf(pos).toFixed(3)} m across); use a larger maxEdge`);
    }
    const out = new Uint32Array(grows * 3);
    let o = 0;
    const emit = (a: number, b: number, c: number) => { out[o++] = a; out[o++] = b; out[o++] = c; };
    for (let t = 0; t < nt; t++) {
      const b0 = t * 3;
      if (!(mark[te[b0]] | mark[te[b0 + 1]] | mark[te[b0 + 2]])) { emit(tris[b0], tris[b0 + 1], tris[b0 + 2]); continue; }
      // Rotate so the edge cut first (the longest, which the closure marked) is v0-v1.
      let L = longest[t];
      if (!mark[te[b0 + L]]) L = mark[te[b0]] ? 0 : mark[te[b0 + 1]] ? 1 : 2;
      const v0 = tris[b0 + L], v1 = tris[b0 + (L + 1) % 3], v2 = tris[b0 + (L + 2) % 3];
      const e0 = te[b0 + L], e1 = te[b0 + (L + 1) % 3], e2 = te[b0 + (L + 2) % 3];
      const m0 = mid(v0, v1, e0);
      const has1 = mark[e1] === 1, has2 = mark[e2] === 1;
      if (has1 && has2) {
        const m1 = mid(v1, v2, e1), m2 = mid(v2, v0, e2);
        emit(v0, m0, m2); emit(m0, v1, m1); emit(m2, m1, v2); emit(m0, m1, m2);
      } else if (has1) {
        const m1 = mid(v1, v2, e1);
        emit(v0, m0, v2); emit(m0, v1, m1); emit(m0, m1, v2);
      } else if (has2) {
        const m2 = mid(v2, v0, e2);
        emit(v0, m0, m2); emit(m0, v2, m2); emit(m0, v1, v2);
      } else {
        emit(v0, m0, v2); emit(m0, v1, v2);
      }
    }
    tris = out.slice(0, o);
  }
  return { pos: Float32Array.from(P.subarray(0, nv * 3)), idx: tris };
}

// ================================================================== noise
//
// Gradient noise of the position (Perlin's improved gradients on a hashed lattice), summed over
// octaves with a fixed rotation between them so no two octaves share a lattice axis. A seed and a
// position give one number, whatever the vertex is called or where it sits in the arrays.

function hash4(x: number, y: number, z: number, seed: number): number {
  let h = Math.imul(x | 0, 0x27d4eb2d) ^ Math.imul(y | 0, 0x165667b1) ^ Math.imul(z | 0, 0x1b873593) ^ Math.imul(seed | 0, 0x68e31da5);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return (h ^ (h >>> 16)) >>> 0;
}

function grad(h: number, x: number, y: number, z: number): number {
  switch (h % 12) {
    case 0: return x + y;
    case 1: return y - x;
    case 2: return x - y;
    case 3: return -x - y;
    case 4: return x + z;
    case 5: return z - x;
    case 6: return x - z;
    case 7: return -x - z;
    case 8: return y + z;
    case 9: return z - y;
    case 10: return y - z;
    default: return -y - z;
  }
}

const fade5 = (t: number) => t * t * t * (t * (t * 6 - 15) + 10);

function perlin(x: number, y: number, z: number, seed: number): number {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
  const fx = x - xi, fy = y - yi, fz = z - zi;
  const u = fade5(fx), v = fade5(fy), w = fade5(fz);
  const n000 = grad(hash4(xi, yi, zi, seed), fx, fy, fz);
  const n100 = grad(hash4(xi + 1, yi, zi, seed), fx - 1, fy, fz);
  const n010 = grad(hash4(xi, yi + 1, zi, seed), fx, fy - 1, fz);
  const n110 = grad(hash4(xi + 1, yi + 1, zi, seed), fx - 1, fy - 1, fz);
  const n001 = grad(hash4(xi, yi, zi + 1, seed), fx, fy, fz - 1);
  const n101 = grad(hash4(xi + 1, yi, zi + 1, seed), fx - 1, fy, fz - 1);
  const n011 = grad(hash4(xi, yi + 1, zi + 1, seed), fx, fy - 1, fz - 1);
  const n111 = grad(hash4(xi + 1, yi + 1, zi + 1, seed), fx - 1, fy - 1, fz - 1);
  const x00 = n000 + u * (n100 - n000), x10 = n010 + u * (n110 - n010);
  const x01 = n001 + u * (n101 - n001), x11 = n011 + u * (n111 - n011);
  const y0 = x00 + v * (x10 - x00), y1 = x01 + v * (x11 - x01);
  return y0 + w * (y1 - y0);
}

// An orthonormal matrix (Quilez's m3): each octave is turned before it is scaled.
const ROT = [0.0, 0.8, 0.6, -0.8, 0.36, -0.48, -0.6, -0.48, 0.64];
// Gradient noise summed over octaves has an RMS of about 0.17; this makes it 0.3, peaks near 1.
const NOISE_GAIN = 1.8;

function fbm3(x: number, y: number, z: number, octaves: number, seed: number): number {
  let sum = 0, amp = 1, norm = 0;
  for (let o = 0; o < octaves; o++) {
    sum += amp * perlin(x, y, z, seed + o * 7919);
    norm += amp;
    amp *= 0.5;
    const nx = (ROT[0] * x + ROT[1] * y + ROT[2] * z) * 2.03 + 17.31;
    const ny = (ROT[3] * x + ROT[4] * y + ROT[5] * z) * 2.03 + 3.97;
    const nz = (ROT[6] * x + ROT[7] * y + ROT[8] * z) * 2.03 + 11.53;
    x = nx; y = ny; z = nz;
  }
  return norm > 0 ? sum / norm : 0;
}

// ================================================================== stroke settings

const KINDS = ["crease", "ridge", "inflate", "pinch", "smooth", "flatten", "noise", "move", "clay"];
const NEEDS_PATH = new Set(["crease", "ridge", "pinch", "flatten", "clay"]);
const G45 = Math.exp(-4.5);
const FALLOFF: Record<string, (x: number) => number> = {
  smooth: (x) => 1 - x * x * (3 - 2 * x),
  sharp: (x) => (1 - x) * (1 - x),
  gauss: (x) => (Math.exp(-4.5 * x * x) - G45) / (1 - G45),
  linear: (x) => 1 - x,
  constant: () => 1,
  plateau: (x) => (x <= 0.5 ? 1 : 1 - (2 * x - 1) * (2 * x - 1) * (3 - 2 * (2 * x - 1))),
};
// Cross-sections, 1 on the path and 0 at the radius. 'v' has a kink on the path (the sharp bottom
// of a wrinkle) and a zero slope at the radius (the soft shoulder); 'u' is smooth at both.
const PROFILE: Record<string, (x: number) => number> = {
  v: (x) => (1 - x) * (1 - x),
  u: (x) => 0.5 + 0.5 * Math.cos(Math.PI * x),
  round: (x) => Math.sqrt(Math.max(0, 1 - x * x)),
};
const DEPTH: Record<string, number> = { crease: -0.35, ridge: 0.35, inflate: 0.2, clay: 0.25, noise: 0.1, move: 0.25 };

interface Resolved {
  index: number; kind: string; radius: number; depth: number; strength: number; pinch: number;
  fall: (x: number) => number; prof: (x: number) => number;
  t0: number; t1: number; dir: V3 | null;
  freq: number; octaves: number; seed: number; stretch: number; off: V3;
  mirror: number; mask: ((p: V3, n: V3) => number) | null; curve: boolean; points: V3[] | null;
}

const finite3 = (v: unknown): v is V3 =>
  Array.isArray(v) && v.length >= 3 && isFinite(+v[0]) && isFinite(+v[1]) && isFinite(+v[2]);
const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);

function cleanPath(pts: unknown[]): V3[] {
  const out: V3[] = [];
  for (const p of pts) {
    if (!finite3(p)) continue;
    const q: V3 = [+p[0], +p[1], +p[2]];
    const l = out[out.length - 1];
    if (l && Math.abs(l[0] - q[0]) + Math.abs(l[1] - q[1]) + Math.abs(l[2] - q[2]) < 1e-12) continue;
    out.push(q);
  }
  return out;
}

function resolve(s: Stroke, i: number): Resolved {
  const where = `sculpt: stroke ${i}`;
  if (!s || typeof s !== "object") throw new Error(`${where} is not an object`);
  const kind = String(s.kind);
  if (!KINDS.includes(kind)) throw new Error(`${where} has kind "${kind}"; use one of ${KINDS.join(", ")}`);
  const radius = Number(s.radius);
  if (!(radius > 0) || !isFinite(radius)) throw new Error(`${where} (${kind}) needs a radius above zero, in metres`);
  let points: V3[] | null = null;
  if (Array.isArray(s.path) && s.path.length) {
    points = cleanPath(s.path);
    if (!points.length) throw new Error(`${where} (${kind}): the path has no usable [x, y, z] points`);
  } else if (s.at !== undefined && s.at !== null) {
    if (!finite3(s.at)) throw new Error(`${where} (${kind}): at must be [x, y, z]`);
    points = [[+s.at[0], +s.at[1], +s.at[2]]];
  }
  if (!points && NEEDS_PATH.has(kind)) throw new Error(`${where}: a ${kind} stroke needs a path (or at)`);
  const profile = s.profile ?? (kind === "ridge" ? "u" : "v");
  const prof = PROFILE[profile];
  if (!prof) throw new Error(`${where}: profile "${profile}"; use v, u or round`);
  const falloff = s.falloff ?? (kind === "clay" ? "plateau" : "smooth");
  const fall = FALLOFF[falloff];
  if (!fall) throw new Error(`${where}: falloff "${falloff}"; use ${Object.keys(FALLOFF).join(", ")}`);
  const depth = typeof s.depth === "number" && isFinite(s.depth) ? s.depth : (DEPTH[kind] ?? 0) * radius;
  const given = typeof s.strength === "number" && isFinite(s.strength) ? s.strength : NaN;
  const pick = (d: number) => (isNaN(given) ? d : given);
  let strength = 1, pinch = 0;
  if (kind === "smooth") strength = clamp(pick(0.5), 0, 1);
  else if (kind === "pinch") strength = clamp(pick(0.5), 0, 0.95);
  else if (kind === "flatten" || kind === "clay") strength = clamp(pick(1), 0, 1);
  else if (kind === "crease" || kind === "ridge") pinch = clamp(pick(profile === "v" ? 0.5 : 0), 0, 0.9);
  const tp = s.taper;
  const t0 = Array.isArray(tp) && isFinite(+tp[0]) ? Math.max(0, +tp[0]) : 1;
  const t1 = Array.isArray(tp) && isFinite(+tp[1]) ? Math.max(0, +tp[1]) : 1;
  let dir: V3 | null = finite3(s.dir) ? [+s.dir[0], +s.dir[1], +s.dir[2]] : null;
  if (dir && (kind === "flatten" || kind === "noise")) {
    const l = Math.sqrt(dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2]);
    dir = l > 1e-12 ? [dir[0] / l, dir[1] / l, dir[2] / l] : null;
  }
  let mirror = -1;
  if (s.mirror !== undefined && s.mirror !== null) {
    mirror = ["x", "y", "z"].indexOf(String(s.mirror));
    if (mirror < 0) throw new Error(`${where}: mirror "${s.mirror}"; use x, y or z`);
  }
  if (s.mask !== undefined && s.mask !== null && typeof s.mask !== "function") throw new Error(`${where}: mask must be a function (p, n) => 0..1`);
  const seed = typeof s.seed === "number" && isFinite(s.seed) ? Math.floor(s.seed) : 1;
  const h = (k: number) => (hash4(seed, k, 7 * k + 3, 91) / 4294967296) * 64;
  return {
    index: i, kind, radius, depth, strength, pinch, fall, prof, t0, t1, dir,
    freq: typeof s.freq === "number" && s.freq > 0 ? s.freq : 3 / radius,
    octaves: clamp(Math.round(typeof s.octaves === "number" && isFinite(s.octaves) ? s.octaves : 3), 1, 8),
    seed, stretch: typeof s.stretch === "number" && s.stretch >= 1 ? s.stretch : 6, off: [h(1), h(2), h(3)],
    mirror, mask: typeof s.mask === "function" ? s.mask : null, curve: !!s.curve, points,
  };
}

/** The stroke's noise at a point; with a mirror, blended with its mirror image across a band half a
 *  feature wide, so the field is exactly symmetric and still smooth on the plane. */
function noiseAt(s: Resolved, x: number, y: number, z: number): number {
  if (s.mirror < 0) return noiseField(s, x, y, z);
  const a = s.mirror === 0 ? x : s.mirror === 1 ? y : z;
  const b = 0.5 / s.freq;
  const t = clamp((a + b) / (2 * b), 0, 1), w = t * t * (3 - 2 * t);
  let v = 0;
  if (w > 0) v += w * noiseField(s, x, y, z);
  if (w < 1) v += (1 - w) * noiseField(s, s.mirror === 0 ? -x : x, s.mirror === 1 ? -y : y, s.mirror === 2 ? -z : z);
  return v;
}

function noiseField(s: Resolved, x: number, y: number, z: number): number {
  if (s.dir) {
    // Streaks: squeeze the coordinate along the direction, so features run long that way.
    const d = s.dir, k = (x * d[0] + y * d[1] + z * d[2]) * (1 - 1 / s.stretch);
    x -= k * d[0]; y -= k * d[1]; z -= k * d[2];
  }
  return NOISE_GAIN * fbm3(x * s.freq + s.off[0], y * s.freq + s.off[1], z * s.freq + s.off[2], s.octaves, s.seed);
}

// ================================================================== the working surface

interface Grid { x0: number; y0: number; z0: number; inv: number; nx: number; ny: number; nz: number; start: Int32Array; items: Int32Array }
interface Copy { W: Float64Array; U: Float64Array; stamp: Int32Array; list: Int32Array; count: number; sid: number }

interface Ctx {
  M: WMesh; n: number; P: Float64Array; A: Float64Array; N: Float64Array;
  grid: Grid | null; gridStart: Int32Array | null; gridItems: Int32Array; gridCell: Int32Array;
  gx: Float64Array; gu: Float64Array; gseg: Int32Array; gstamp: Int32Array; glist: Int32Array; gid: number;
  copies: Copy[]; fit: Float64Array; loc: Int32Array | null;
}

const makeCopy = (n: number): Copy => ({ W: new Float64Array(n), U: new Float64Array(n * 3), stamp: new Int32Array(n), list: new Int32Array(n), count: 0, sid: 0 });

/** A uniform grid over the welded vertices, rebuilt per stroke (the surface moves). */
function buildGrid(ctx: Ctx, cell: number): Grid {
  const { P, n } = ctx;
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (let v = 0; v < n; v++) {
    const x = P[v * 3], y = P[v * 3 + 1], z = P[v * 3 + 2];
    if (x < x0) x0 = x; if (x > x1) x1 = x;
    if (y < y0) y0 = y; if (y > y1) y1 = y;
    if (z < z0) z0 = z; if (z > z1) z1 = z;
  }
  let c = Math.max(cell, 1e-9), nx = 1, ny = 1, nz = 1;
  for (let g = 0; g < 400; g++) {
    nx = Math.floor((x1 - x0) / c) + 1; ny = Math.floor((y1 - y0) / c) + 1; nz = Math.floor((z1 - z0) / c) + 1;
    if (nx * ny * nz <= 1 << 20) break;
    c *= 1.2;
  }
  const cells = nx * ny * nz, inv = 1 / c;
  let start = ctx.gridStart;
  if (!start || start.length < cells + 1) start = ctx.gridStart = new Int32Array(cells + 1);
  start.fill(0, 0, cells + 1);
  const cid = ctx.gridCell, items = ctx.gridItems;
  for (let v = 0; v < n; v++) {
    const ix = Math.min(nx - 1, Math.floor((P[v * 3] - x0) * inv));
    const iy = Math.min(ny - 1, Math.floor((P[v * 3 + 1] - y0) * inv));
    const iz = Math.min(nz - 1, Math.floor((P[v * 3 + 2] - z0) * inv));
    const id = ix + nx * (iy + ny * iz);
    cid[v] = id; start[id]++;
  }
  for (let q = 0, acc = 0; q < cells; q++) { const k = start[q]; start[q] = acc; acc += k; }
  // Placing moves every cell's start to its end: cell q then spans [start[q - 1], start[q]).
  for (let v = 0; v < n; v++) items[start[cid[v]]++] = v;
  return { x0, y0, z0, inv, nx, ny, nz, start, items };
}

const cellOf = (v: number, o: number, inv: number, nn: number) => {
  const i = Math.floor((v - o) * inv);
  return i < 0 ? 0 : i >= nn ? nn - 1 : i;
};

/** The surface under a point: the nearest vertex (searched out to 8 R), then the centroid and area
 *  normal of the vertices within R of it, on its sheet (its normal decides which side counts).
 *  out = [cx, cy, cz, nx, ny, nz]; false when no vertex is that near. */
function fitPlane(ctx: Ctx, x: number, y: number, z: number, R: number, out: Float64Array): boolean {
  const g = ctx.grid!, { P, A, N } = ctx;
  let best = -1;
  for (let reachR = R; reachR <= 8 * R * 1.0001 && best < 0; reachR *= 2) {
    let bd = reachR * reachR;
    const i0 = cellOf(x - reachR, g.x0, g.inv, g.nx), i1 = cellOf(x + reachR, g.x0, g.inv, g.nx);
    const j0 = cellOf(y - reachR, g.y0, g.inv, g.ny), j1 = cellOf(y + reachR, g.y0, g.inv, g.ny);
    const k0 = cellOf(z - reachR, g.z0, g.inv, g.nz), k1 = cellOf(z + reachR, g.z0, g.inv, g.nz);
    for (let k = k0; k <= k1; k++) for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
      const cell = i + g.nx * (j + g.ny * k);
      for (let q = cell ? g.start[cell - 1] : 0, e = g.start[cell]; q < e; q++) {
        const v = g.items[q] * 3;
        const d2 = (P[v] - x) ** 2 + (P[v + 1] - y) ** 2 + (P[v + 2] - z) ** 2;
        if (d2 < bd) { bd = d2; best = v; }
      }
    }
  }
  if (best < 0) return false;
  const bx = P[best], by = P[best + 1], bz = P[best + 2], rx = N[best], ry = N[best + 1], rz = N[best + 2];
  const R2 = R * R;
  const i0 = cellOf(bx - R, g.x0, g.inv, g.nx), i1 = cellOf(bx + R, g.x0, g.inv, g.nx);
  const j0 = cellOf(by - R, g.y0, g.inv, g.ny), j1 = cellOf(by + R, g.y0, g.inv, g.ny);
  const k0 = cellOf(bz - R, g.z0, g.inv, g.nz), k1 = cellOf(bz + R, g.z0, g.inv, g.nz);
  let sw = 0, cx = 0, cy = 0, cz = 0, ax = 0, ay = 0, az = 0;
  for (let k = k0; k <= k1; k++) for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
    const cell = i + g.nx * (j + g.ny * k);
    for (let q = cell ? g.start[cell - 1] : 0, e = g.start[cell]; q < e; q++) {
      const v = g.items[q] * 3;
      const d2 = (P[v] - bx) ** 2 + (P[v + 1] - by) ** 2 + (P[v + 2] - bz) ** 2;
      if (d2 >= R2 || A[v] * rx + A[v + 1] * ry + A[v + 2] * rz <= 0) continue;
      const f = 1 - d2 / R2, w = f * f;
      sw += w; cx += w * P[v]; cy += w * P[v + 1]; cz += w * P[v + 2];
      ax += w * A[v]; ay += w * A[v + 1]; az += w * A[v + 2];
    }
  }
  const l = Math.sqrt(ax * ax + ay * ay + az * az);
  if (!(sw > 0) || !(l > 0)) {
    // A lone vertex: its own position and normal.
    if (!(rx * rx + ry * ry + rz * rz > 0)) return false;
    out[0] = bx; out[1] = by; out[2] = bz; out[3] = rx; out[4] = ry; out[5] = rz;
    return true;
  }
  out[0] = cx / sw; out[1] = cy / sw; out[2] = cz / sw; out[3] = ax / l; out[4] = ay / l; out[5] = az / l;
  return true;
}

function catmullRom(P: V3[], per: number): V3[] {
  const n = P.length;
  if (n < 3 || per < 2) return P.slice();
  const out: V3[] = [];
  const at = (i: number): V3 => {
    if (i < 0) return [2 * P[0][0] - P[1][0], 2 * P[0][1] - P[1][1], 2 * P[0][2] - P[1][2]];
    if (i >= n) return [2 * P[n - 1][0] - P[n - 2][0], 2 * P[n - 1][1] - P[n - 2][1], 2 * P[n - 1][2] - P[n - 2][2]];
    return P[i];
  };
  const knot = (a: V3, b: V3) => Math.max(1e-9, Math.sqrt(Math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2)));
  for (let i = 0; i < n - 1; i++) {
    const p0 = at(i - 1), p1 = P[i], p2 = P[i + 1], p3 = at(i + 2);
    const t1 = knot(p0, p1), t2 = t1 + knot(p1, p2), t3 = t2 + knot(p2, p3);
    for (let s = 0; s < per; s++) {
      const t = t1 + ((t2 - t1) * s) / per;
      const q: V3 = [0, 0, 0];
      for (let k = 0; k < 3; k++) {
        const a1 = ((t1 - t) / t1) * p0[k] + (t / t1) * p1[k];
        const a2 = ((t2 - t) / (t2 - t1)) * p1[k] + ((t - t1) / (t2 - t1)) * p2[k];
        const a3 = ((t3 - t) / (t3 - t2)) * p2[k] + ((t - t2) / (t3 - t2)) * p3[k];
        const b1 = ((t2 - t) / t2) * a1 + (t / t2) * a2;
        const b2 = ((t3 - t) / (t3 - t1)) * a2 + ((t - t1) / (t3 - t1)) * a3;
        q[k] = ((t2 - t) / (t2 - t1)) * b1 + ((t - t1) / (t2 - t1)) * b2;
      }
      out.push(q);
    }
  }
  out.push(P[n - 1]);
  return out;
}

interface Samples { k: number; S: Float64Array; N: Float64Array; C: Float64Array; tau: Float64Array; rad: Float64Array; ok: Uint8Array }

/** The path cut into short steps, each laid onto the surface, with the surface's plane there. */
function makeSamples(ctx: Ctx, pts: V3[], s: Resolved): Samples {
  const me = ctx.M.meanEdge;
  const path = s.curve && pts.length > 2 ? catmullRom(pts, 16) : pts;
  let total = 0;
  const seg: number[] = [];
  for (let i = 0; i + 1 < path.length; i++) {
    const l = Math.sqrt((path[i + 1][0] - path[i][0]) ** 2 + (path[i + 1][1] - path[i][1]) ** 2 + (path[i + 1][2] - path[i][2]) ** 2);
    seg.push(l); total += l;
  }
  const ds = Math.max(me * 0.75, s.radius * Math.max(s.t0, s.t1) * 0.3, total / 20000);
  const flat: number[] = [], tt: number[] = [];
  let acc = 0;
  for (let i = 0; i + 1 < path.length; i++) {
    const a = path[i], b = path[i + 1], l = seg[i];
    const m = Math.max(1, Math.ceil(l / ds));
    for (let j = 0; j < m; j++) {
      const f = j / m;
      flat.push(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f);
      tt.push(total > 0 ? (acc + l * f) / total : 0);
    }
    acc += l;
  }
  const last = path[path.length - 1];
  flat.push(last[0], last[1], last[2]); tt.push(path.length > 1 ? 1 : 0);
  const k = tt.length;
  const S = new Float64Array(k * 3), N = new Float64Array(k * 3), C = new Float64Array(k * 3);
  const tau = new Float64Array(k), rad = new Float64Array(k), ok = new Uint8Array(k);
  const f = ctx.fit;
  for (let i = 0; i < k; i++) {
    tau[i] = s.t0 + (s.t1 - s.t0) * tt[i];
    rad[i] = s.radius * tau[i];
    const R = Math.max(rad[i], 2 * me);
    let x = flat[i * 3], y = flat[i * 3 + 1], z = flat[i * 3 + 2];
    let good = false;
    // Drop the point onto the plane under it, then measure the plane again where it landed.
    for (let pass = 0; pass < 2; pass++) {
      if (!fitPlane(ctx, x, y, z, R, f)) break;
      const h = (x - f[0]) * f[3] + (y - f[1]) * f[4] + (z - f[2]) * f[5];
      x -= h * f[3]; y -= h * f[4]; z -= h * f[5];
      good = true;
    }
    ok[i] = good ? 1 : 0;
    S[i * 3] = x; S[i * 3 + 1] = y; S[i * 3 + 2] = z;
    if (good) for (let q = 0; q < 3; q++) { C[i * 3 + q] = f[q]; N[i * 3 + q] = f[3 + q]; }
  }
  return { k, S, N, C, tau, rad, ok };
}

/** Every vertex within the (tapered) radius of the laid path: its distance as a fraction of the
 *  radius there, the nearest segment and where on it. Returns how many were found. */
function gather(ctx: Ctx, sm: Samples): number {
  const g = ctx.grid!, P = ctx.P;
  const gid = ++ctx.gid;
  let count = 0;
  const segs = sm.k === 1 ? 1 : sm.k - 1;
  for (let sgi = 0; sgi < segs; sgi++) {
    const a = sgi, b = sm.k === 1 ? 0 : sgi + 1;
    if (!sm.ok[a] || !sm.ok[b]) continue;
    const ra = sm.rad[a], rb = sm.rad[b], rm = Math.max(ra, rb);
    if (!(rm > 0)) continue;
    const ax = sm.S[a * 3], ay = sm.S[a * 3 + 1], az = sm.S[a * 3 + 2];
    const abx = sm.S[b * 3] - ax, aby = sm.S[b * 3 + 1] - ay, abz = sm.S[b * 3 + 2] - az;
    const L2 = abx * abx + aby * aby + abz * abz, invL2 = L2 > 1e-24 ? 1 / L2 : 0;
    const i0 = cellOf(Math.min(ax, ax + abx) - rm, g.x0, g.inv, g.nx), i1 = cellOf(Math.max(ax, ax + abx) + rm, g.x0, g.inv, g.nx);
    const j0 = cellOf(Math.min(ay, ay + aby) - rm, g.y0, g.inv, g.ny), j1 = cellOf(Math.max(ay, ay + aby) + rm, g.y0, g.inv, g.ny);
    const k0 = cellOf(Math.min(az, az + abz) - rm, g.z0, g.inv, g.nz), k1 = cellOf(Math.max(az, az + abz) + rm, g.z0, g.inv, g.nz);
    for (let k = k0; k <= k1; k++) for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
      const cell = i + g.nx * (j + g.ny * k);
      for (let q = cell ? g.start[cell - 1] : 0, e = g.start[cell]; q < e; q++) {
        const v = g.items[q];
        const px = P[v * 3] - ax, py = P[v * 3 + 1] - ay, pz = P[v * 3 + 2] - az;
        let u = (px * abx + py * aby + pz * abz) * invL2;
        u = u < 0 ? 0 : u > 1 ? 1 : u;
        const dx = px - u * abx, dy = py - u * aby, dz = pz - u * abz;
        const r = ra + (rb - ra) * u;
        const d2 = dx * dx + dy * dy + dz * dz;
        if (!(r > 0) || d2 >= r * r) continue;
        const x = Math.sqrt(d2) / r;
        if (ctx.gstamp[v] !== gid) {
          ctx.gstamp[v] = gid; ctx.gx[v] = x; ctx.gseg[v] = sgi; ctx.gu[v] = u; ctx.glist[count++] = v;
        } else if (x < ctx.gx[v]) { ctx.gx[v] = x; ctx.gseg[v] = sgi; ctx.gu[v] = u; }
      }
    }
  }
  return count;
}

const facing = (d: number) => { const f = (d + 0.25) * 2; return f <= 0 ? 0 : f >= 1 ? 1 : f; };

function maskAt(s: Resolved, o: number, P: Float64Array, N: Float64Array, mir: number): number {
  const p: V3 = [P[o], P[o + 1], P[o + 2]], n: V3 = [N[o], N[o + 1], N[o + 2]];
  if (mir >= 0) { p[mir] = -p[mir]; n[mir] = -n[mir]; }
  const m = Number(s.mask!(p, n));
  return m > 0 ? (m < 1 ? m : 1) : 0;
}

/** One copy of a stroke (the original, or its mirror image), measured on the current surface:
 *  per vertex a weight 0..1 and the displacement it would make at full weight. */
function evalCopy(ctx: Ctx, s: Resolved, copy: number, sid: number): void {
  const cp = ctx.copies[copy];
  cp.sid = sid * 2 + copy + 1;
  cp.count = 0;
  const mir = copy === 1 ? s.mirror : -1;
  const { P, N } = ctx;
  const md: V3 | null = s.dir ? (mir >= 0 ? [mir === 0 ? -s.dir[0] : s.dir[0], mir === 1 ? -s.dir[1] : s.dir[1], mir === 2 ? -s.dir[2] : s.dir[2]] : s.dir) : null;
  const record = (v: number, w: number, ux: number, uy: number, uz: number) => {
    if (!(w > 0)) return;
    cp.stamp[v] = cp.sid; cp.W[v] = w;
    cp.U[v * 3] = ux; cp.U[v * 3 + 1] = uy; cp.U[v * 3 + 2] = uz;
    cp.list[cp.count++] = v;
  };
  if (!s.points) {
    // The whole mesh: inflate, noise, smooth, move.
    for (let v = 0; v < ctx.n; v++) {
      const o = v * 3;
      const m = s.mask ? maskAt(s, o, P, N, mir) : 1;
      if (!(m > 0)) continue;
      if (s.kind === "smooth") record(v, m, 0, 0, 0);
      else if (s.kind === "move" && md) record(v, m, md[0], md[1], md[2]);
      else {
        const d = s.kind === "noise" ? s.depth * noiseAt(s, P[o], P[o + 1], P[o + 2]) : s.depth;
        record(v, m, d * N[o], d * N[o + 1], d * N[o + 2]);
      }
    }
    return;
  }
  const pts = mir >= 0 ? s.points.map((q): V3 => [mir === 0 ? -q[0] : q[0], mir === 1 ? -q[1] : q[1], mir === 2 ? -q[2] : q[2]]) : s.points;
  const sm = makeSamples(ctx, pts, s);
  if (!sm.ok.some((x) => x === 1)) {
    if (copy === 1) return;   // the mirror side has no surface: an asymmetric mesh
    let best = Infinity;
    for (let v = 0; v < ctx.n; v++) best = Math.min(best, (P[v * 3] - pts[0][0]) ** 2 + (P[v * 3 + 1] - pts[0][1]) ** 2 + (P[v * 3 + 2] - pts[0][2]) ** 2);
    throw new Error(`sculpt: stroke ${s.index} (${s.kind}): its ${s.points.length > 1 ? "path" : "point"} is nowhere near the surface `
      + `(the nearest vertex is ${Math.sqrt(best).toFixed(4)} m from its first point, the radius ${s.radius} m)`);
  }
  const count = gather(ctx, sm);
  for (let i = 0; i < count; i++) {
    const v = ctx.glist[i], o = v * 3;
    const x = ctx.gx[v], u = ctx.gu[v], a = ctx.gseg[v], b = sm.k === 1 ? 0 : a + 1;
    const tau = sm.tau[a] + (sm.tau[b] - sm.tau[a]) * u;
    let Nx = sm.N[a * 3] + (sm.N[b * 3] - sm.N[a * 3]) * u, Ny = sm.N[a * 3 + 1] + (sm.N[b * 3 + 1] - sm.N[a * 3 + 1]) * u, Nz = sm.N[a * 3 + 2] + (sm.N[b * 3 + 2] - sm.N[a * 3 + 2]) * u;
    const nl = Math.sqrt(Nx * Nx + Ny * Ny + Nz * Nz) || 1;
    Nx /= nl; Ny /= nl; Nz /= nl;
    const px = P[o], py = P[o + 1], pz = P[o + 2];
    const vx = px - (sm.S[a * 3] + (sm.S[b * 3] - sm.S[a * 3]) * u);
    const vy = py - (sm.S[a * 3 + 1] + (sm.S[b * 3 + 1] - sm.S[a * 3 + 1]) * u);
    const vz = pz - (sm.S[a * 3 + 2] + (sm.S[b * 3 + 2] - sm.S[a * 3 + 2]) * u);
    const nvx = N[o], nvy = N[o + 1], nvz = N[o + 2];
    const m = s.mask ? maskAt(s, o, P, N, mir) : 1;
    if (!(m > 0)) continue;
    const face = facing(nvx * Nx + nvy * Ny + nvz * Nz);
    switch (s.kind) {
      case "crease":
      case "ridge": {
        // Along the stroke's normal by the profile; the sides drawn toward the line in the tangent plane.
        const h = vx * Nx + vy * Ny + vz * Nz, d = s.depth * tau;
        record(v, s.prof(x) * face * m, d * Nx - s.pinch * (vx - h * Nx), d * Ny - s.pinch * (vy - h * Ny), d * Nz - s.pinch * (vz - h * Nz));
        break;
      }
      case "inflate": {
        const d = s.depth * tau;
        record(v, s.fall(x) * m, d * nvx, d * nvy, d * nvz);
        break;
      }
      case "pinch": {
        const h = vx * Nx + vy * Ny + vz * Nz;
        let lx = vx - h * Nx, ly = vy - h * Ny, lz = vz - h * Nz;
        if (sm.k > 1) {
          let tx = sm.S[b * 3] - sm.S[a * 3], ty = sm.S[b * 3 + 1] - sm.S[a * 3 + 1], tz = sm.S[b * 3 + 2] - sm.S[a * 3 + 2];
          const tl = Math.sqrt(tx * tx + ty * ty + tz * tz);
          if (tl > 0) {
            tx /= tl; ty /= tl; tz /= tl;
            const g = lx * tx + ly * ty + lz * tz;
            lx -= g * tx; ly -= g * ty; lz -= g * tz;
          }
        }
        record(v, s.fall(x) * face * m * s.strength, -lx, -ly, -lz);
        break;
      }
      case "flatten": {
        const cx = sm.C[a * 3] + (sm.C[b * 3] - sm.C[a * 3]) * u, cy = sm.C[a * 3 + 1] + (sm.C[b * 3 + 1] - sm.C[a * 3 + 1]) * u, cz = sm.C[a * 3 + 2] + (sm.C[b * 3 + 2] - sm.C[a * 3 + 2]) * u;
        const qx = md ? md[0] : Nx, qy = md ? md[1] : Ny, qz = md ? md[2] : Nz;
        const h = (px - cx) * qx + (py - cy) * qy + (pz - cz) * qz;
        record(v, s.fall(x) * face * m * s.strength, -h * qx, -h * qy, -h * qz);
        break;
      }
      case "clay": {
        const cx = sm.C[a * 3] + (sm.C[b * 3] - sm.C[a * 3]) * u, cy = sm.C[a * 3 + 1] + (sm.C[b * 3 + 1] - sm.C[a * 3 + 1]) * u, cz = sm.C[a * 3 + 2] + (sm.C[b * 3 + 2] - sm.C[a * 3 + 2]) * u;
        const gap = s.depth * tau - ((px - cx) * Nx + (py - cy) * Ny + (pz - cz) * Nz);
        if (s.depth >= 0 ? gap > 0 : gap < 0) record(v, s.fall(x) * face * m * s.strength, gap * Nx, gap * Ny, gap * Nz);
        break;
      }
      case "noise": {
        const d = s.depth * tau * noiseAt(s, px, py, pz);
        record(v, s.fall(x) * m, d * nvx, d * nvy, d * nvz);
        break;
      }
      case "move": {
        if (md) record(v, s.fall(x) * m, md[0], md[1], md[2]);
        else { const d = s.depth * tau; record(v, s.fall(x) * m, d * Nx, d * Ny, d * Nz); }
        break;
      }
      default:   // smooth
        record(v, s.fall(x) * m, 0, 0, 0);
    }
  }
}

/** Move the surface by the copies. Both were measured on the same surface; where they overlap the
 *  stronger weight wins and the directions blend, so a stroke on the mirror plane is not doubled. */
function applyCopies(ctx: Ctx, copies: number): void {
  const P = ctx.P, c0 = ctx.copies[0];
  if (copies === 1) {
    for (let i = 0; i < c0.count; i++) {
      const v = c0.list[i], w = c0.W[v], o = v * 3;
      P[o] += w * c0.U[o]; P[o + 1] += w * c0.U[o + 1]; P[o + 2] += w * c0.U[o + 2];
    }
    return;
  }
  const c1 = ctx.copies[1];
  for (let i = 0; i < c0.count; i++) {
    const v = c0.list[i], o = v * 3, w0 = c0.W[v];
    const w1 = c1.stamp[v] === c1.sid ? c1.W[v] : 0;
    if (!(w1 > 0)) { P[o] += w0 * c0.U[o]; P[o + 1] += w0 * c0.U[o + 1]; P[o + 2] += w0 * c0.U[o + 2]; continue; }
    const k = Math.max(w0, w1) / (w0 + w1);
    P[o] += k * (w0 * c0.U[o] + w1 * c1.U[o]);
    P[o + 1] += k * (w0 * c0.U[o + 1] + w1 * c1.U[o + 1]);
    P[o + 2] += k * (w0 * c0.U[o + 2] + w1 * c1.U[o + 2]);
  }
  for (let i = 0; i < c1.count; i++) {
    const v = c1.list[i];
    if (c0.stamp[v] === c0.sid) continue;
    const o = v * 3, w1 = c1.W[v];
    P[o] += w1 * c1.U[o]; P[o + 1] += w1 * c1.U[o + 1]; P[o + 2] += w1 * c1.U[o + 2];
  }
}

/** Smoothing over the copies' union, which must not shrink what it smooths.
 *  - A brush (`path` or `at`): Laplacian steps less the bend of the surface under the brush. The
 *    Laplacian of a dent or a ridge sums to about zero over a region that holds it; the bend of the
 *    surface does not. So the region's mean normal Laplacian, measured once, is the bend, and taking
 *    it out of every step lets a groove diffuse away to the brush's rim while a sphere stays a sphere.
 *  - The whole mesh: each step less its twice-neighbour-averaged self (the part the neighbourhood
 *    shares, which is the local bend), so noise goes and the volume stays.
 *  Open borders stay put. */
function runSmooth(ctx: Ctx, s: Resolved, copies: number): void {
  const { P, M, N } = ctx;
  const c0 = ctx.copies[0], c1 = copies > 1 ? ctx.copies[1] : null;
  const L = new Int32Array(c0.count + (c1 ? c1.count : 0));
  const Wt = new Float64Array(L.length);
  let cnt = 0;
  for (let i = 0; i < c0.count; i++) {
    const v = c0.list[i];
    if (M.border[v]) continue;
    let w = c0.W[v];
    if (c1 && c1.stamp[v] === c1.sid) w = Math.max(w, c1.W[v]);
    L[cnt] = v; Wt[cnt++] = w;
  }
  if (c1) for (let i = 0; i < c1.count; i++) {
    const v = c1.list[i];
    if (M.border[v] || c0.stamp[v] === c0.sid) continue;
    L[cnt] = v; Wt[cnt++] = c1.W[v];
  }
  if (!cnt) return;
  // A brush smooths what is about its own size: a groove h edges wide takes about h^2 passes to
  // diffuse away, so strength 1 is (radius in edges)^2 passes (at least 20, at most 400).
  const span = s.points ? (s.radius * Math.max(s.t0, s.t1)) / M.meanEdge : 0;
  const iters = Math.max(1, Math.round(s.strength * Math.min(400, Math.max(20, span * span))));
  if (!ctx.loc) ctx.loc = new Int32Array(ctx.n).fill(-1);
  const loc = ctx.loc;
  for (let i = 0; i < cnt; i++) loc[L[i]] = i;
  const B = new Float64Array(cnt * 3), C = new Float64Array(cnt * 3), D = new Float64Array(cnt * 3);
  // The umbrella Laplacian of every vertex in the brush, scaled by its weight, into B.
  const laplace = (scale: number) => {
    for (let i = 0; i < cnt; i++) {
      const v = L[i], o = v * 3, s0 = M.off[v], s1 = M.off[v + 1];
      let dx = 0, dy = 0, dz = 0;
      if (s1 > s0) {
        let ax = 0, ay = 0, az = 0;
        for (let q = s0; q < s1; q++) { const j = M.adj[q] * 3; ax += P[j]; ay += P[j + 1]; az += P[j + 2]; }
        const inv = 1 / (s1 - s0), lam = scale * Wt[i];
        dx = lam * (ax * inv - P[o]); dy = lam * (ay * inv - P[o + 1]); dz = lam * (az * inv - P[o + 2]);
      }
      B[i * 3] = dx; B[i * 3 + 1] = dy; B[i * 3 + 2] = dz;
    }
  };
  if (s.points) {
    laplace(1);
    let kw = 0, sw = 0;
    for (let i = 0; i < cnt; i++) { const o = L[i] * 3; kw += B[i * 3] * N[o] + B[i * 3 + 1] * N[o + 1] + B[i * 3 + 2] * N[o + 2]; sw += Wt[i]; }
    const kappa = sw > 0 ? kw / sw : 0;   // the bend, as a Laplacian along the normal
    for (let it = 0; it < iters; it++) {
      laplace(0.5);
      for (let i = 0; i < cnt; i++) {
        const o = L[i] * 3, k = 0.5 * Wt[i] * kappa;
        P[o] += B[i * 3] - k * N[o]; P[o + 1] += B[i * 3 + 1] - k * N[o + 1]; P[o + 2] += B[i * 3 + 2] - k * N[o + 2];
      }
    }
  } else {
    // Average of a per-vertex vector over the neighbours (zero outside the brush).
    const spread = (src: Float64Array, dst: Float64Array) => {
      for (let i = 0; i < cnt; i++) {
        const v = L[i], s0 = M.off[v], s1 = M.off[v + 1];
        let x = 0, y = 0, z = 0;
        for (let q = s0; q < s1; q++) { const j = loc[M.adj[q]]; if (j >= 0) { x += src[j * 3]; y += src[j * 3 + 1]; z += src[j * 3 + 2]; } }
        const inv = s1 > s0 ? 1 / (s1 - s0) : 0;
        dst[i * 3] = x * inv; dst[i * 3 + 1] = y * inv; dst[i * 3 + 2] = z * inv;
      }
    };
    for (let it = 0; it < iters; it++) {
      laplace(1);
      spread(B, C);
      spread(C, D);
      for (let i = 0; i < cnt; i++) {
        const o = L[i] * 3;
        P[o] += B[i * 3] - D[i * 3]; P[o + 1] += B[i * 3 + 1] - D[i * 3 + 1]; P[o + 2] += B[i * 3 + 2] - D[i * 3 + 2];
      }
    }
  }
  for (let i = 0; i < cnt; i++) loc[L[i]] = -1;
}

/**
 * Apply sculpt strokes in order and return the new positions (same length and order as `pos`; the
 * triangles are unchanged). Copies of a vertex (seams) are welded by position inside, so they move
 * together, and a vertex no stroke reaches keeps its exact coordinates. See `Stroke` for the brushes.
 * 200k triangles and 20 strokes run in well under three seconds. Throws, naming the stroke, on an
 * unknown kind, a missing radius or path, or a path nowhere near the surface.
 */
export function sculptArrays(pos: Float32Array, idx: Uint32Array, strokes: Stroke[]): Float32Array {
  const out = new Float32Array(pos);
  if (!Array.isArray(strokes) || !strokes.length) return out;
  const list = strokes.map(resolve);
  if (pos.length < 9 || idx.length < 3) return out;
  const M = weldMesh(pos, idx);
  const n = M.n;
  const ctx: Ctx = {
    M, n, P: Float64Array.from(M.P0), A: new Float64Array(n * 3), N: new Float64Array(n * 3),
    grid: null, gridStart: null, gridItems: new Int32Array(n), gridCell: new Int32Array(n),
    gx: new Float64Array(n), gu: new Float64Array(n), gseg: new Int32Array(n), gstamp: new Int32Array(n), glist: new Int32Array(n), gid: 0,
    copies: [makeCopy(n)], fit: new Float64Array(6), loc: null,
  };
  list.forEach((s, i) => {
    const copies = s.mirror >= 0 ? 2 : 1;
    if (copies > 1 && ctx.copies.length < 2) ctx.copies.push(makeCopy(n));
    normalsInto(ctx.P, M.T, ctx.A, ctx.N);
    if (s.points) ctx.grid = buildGrid(ctx, Math.max(s.radius * Math.max(s.t0, s.t1), 1.5 * M.meanEdge));
    for (let c = 0; c < copies; c++) evalCopy(ctx, s, c, i + 1);
    if (s.kind === "smooth") runSmooth(ctx, s, copies);
    else applyCopies(ctx, copies);
  });
  // Write back as a displacement, so every copy of a vertex keeps its own exact position plus the move.
  const P = ctx.P, P0 = M.P0, map = M.map;
  for (let i = 0; i < map.length; i++) {
    const w = map[i] * 3;
    const dx = P[w] - P0[w], dy = P[w + 1] - P0[w + 1], dz = P[w + 2] - P0[w + 2];
    if (dx !== 0 || dy !== 0 || dz !== 0) { out[i * 3] = pos[i * 3] + dx; out[i * 3 + 1] = pos[i * 3 + 1] + dy; out[i * 3 + 2] = pos[i * 3 + 2] + dz; }
  }
  return out;
}
