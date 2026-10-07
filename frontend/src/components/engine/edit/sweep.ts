// Sweeps on plain arrays: tubes and profiles along paths, the rolled rims of shells, and a two-bone
// reach for an arm or a leg that must hold something.
//
// Both goblin builders wrote their own sweeps; the Blender builder ranked "parallel-transport tube
// sweeps" among its high-value methods ("the thick rolled rim look everywhere": helmet rim band,
// pauldron rims, shield rim, tusks). A Frenet frame flips where a curve straightens or turns the
// other way; a parallel-transport frame (here Wang et al.'s double reflection, a rotation-minimising
// frame) never does, and a closed loop has its leftover twist spread evenly so the ends meet.
// `reach` is what the user asked for after the A/B: hands placed on a sword grip and a shield handle.
// Plain arrays in, plain arrays out; nothing here imports an engine.

import type { V3 } from "./ops";

/** Options for `sweepArrays`. */
export interface SweepOptions {
  /** Tube radius in metres, or a function of t (0 at the path's start, 1 at its end). With a
   *  `profile` it scales the profile (default 1 then; 0.02 for a plain tube). */
  radius?: number | ((t: number) => number);
  /** Sides of a round tube (default 12). Ignored when there is a profile. */
  sides?: number;
  /** A closed 2D outline in the frame's (normal, binormal) plane, either winding; scaled by radius. */
  profile?: Array<[number, number]>;
  /** The path is a loop: the tube joins itself with no seam and no twist step. */
  closed?: boolean;
  /** Close the two ends of an open tube with flat caps (default true). */
  caps?: boolean;
  /** Catmull-Rom points per path segment (default 0: the path as given, corners mitred). */
  smooth?: number;
  /** Which way the frame's normal points at the start (default: whichever axis is most across the path). */
  up?: V3;
  /** Degrees: profile corners that turn more than this get hard edges (split normals). Default: every
   *  corner of a profile of six points or fewer (a bar, a diamond blade), else corners over 40. */
  sharp?: number;
}

type P3 = [number, number, number];

const sub = (a: V3, b: V3): P3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): P3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V3) => Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
const scale = (a: V3, s: number): P3 => [a[0] * s, a[1] * s, a[2] * s];
const norm = (a: V3): P3 => { const l = len(a); return l > 0 ? [a[0] / l, a[1] / l, a[2] / l] : [0, 0, 0]; };
const finite3 = (v: unknown): v is V3 => Array.isArray(v) && v.length >= 3 && isFinite(+v[0]) && isFinite(+v[1]) && isFinite(+v[2]);

function cleanPoints(path: V3[], eps: number): P3[] {
  const out: P3[] = [];
  for (const p of path || []) {
    if (!finite3(p)) continue;
    const q: P3 = [+p[0], +p[1], +p[2]];
    const l = out[out.length - 1];
    if (l && Math.abs(l[0] - q[0]) + Math.abs(l[1] - q[1]) + Math.abs(l[2] - q[2]) <= eps) continue;
    out.push(q);
  }
  return out;
}

/** Centripetal Catmull-Rom through the points, `per` points per segment (loops wrap around). */
function catmullRom(P: P3[], per: number, closed: boolean): P3[] {
  const n = P.length;
  if (n < 3 || per < 2) return P.slice();
  const at = (i: number): P3 => {
    if (closed) return P[((i % n) + n) % n];
    if (i < 0) return [2 * P[0][0] - P[1][0], 2 * P[0][1] - P[1][1], 2 * P[0][2] - P[1][2]];
    if (i >= n) return [2 * P[n - 1][0] - P[n - 2][0], 2 * P[n - 1][1] - P[n - 2][1], 2 * P[n - 1][2] - P[n - 2][2]];
    return P[i];
  };
  const knot = (a: V3, b: V3) => Math.max(1e-9, Math.sqrt(len(sub(b, a))));
  const out: P3[] = [];
  const segs = closed ? n : n - 1;
  for (let i = 0; i < segs; i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
    const t1 = knot(p0, p1), t2 = t1 + knot(p1, p2), t3 = t2 + knot(p2, p3);
    for (let s = 0; s < per; s++) {
      const t = t1 + ((t2 - t1) * s) / per;
      const q: P3 = [0, 0, 0];
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
  if (!closed) out.push(P[n - 1]);
  return out;
}

/** Ear clipping of a simple counter-clockwise polygon. */
function earClip(poly: Array<[number, number]>): number[] {
  const idx = poly.map((_, i) => i), out: number[] = [];
  const cr = (a: number, b: number, c: number) =>
    (poly[b][0] - poly[a][0]) * (poly[c][1] - poly[a][1]) - (poly[b][1] - poly[a][1]) * (poly[c][0] - poly[a][0]);
  const inside = (p: number, a: number, b: number, c: number) => cr(a, b, p) >= 0 && cr(b, c, p) >= 0 && cr(c, a, p) >= 0;
  for (let guard = 0; idx.length > 3 && guard < 100000; guard++) {
    let cut = false;
    for (let i = 0; i < idx.length; i++) {
      const a = idx[(i + idx.length - 1) % idx.length], b = idx[i], c = idx[(i + 1) % idx.length];
      if (cr(a, b, c) <= 1e-14) continue;
      let blocked = false;
      for (const p of idx) if (p !== a && p !== b && p !== c && inside(p, a, b, c)) { blocked = true; break; }
      if (blocked) continue;
      out.push(a, b, c); idx.splice(i, 1); cut = true;
      break;
    }
    if (!cut) break;
  }
  for (let i = 1; i + 1 < idx.length; i++) out.push(idx[0], idx[i], idx[i + 1]);
  return out;
}

/**
 * Sweep a round tube or any closed 2D profile along a path, with parallel-transport frames: the
 * cross-section never twists or flips, whatever the path does. Corners of an unsmoothed path are
 * mitred (the section widens across the bend) so the tube keeps its thickness; `smooth` rounds the
 * path through Catmull-Rom points instead. A `closed` path joins seamlessly, its leftover twist
 * spread along the loop. Returns triangles wound outward with `uv` (u around the section 0..1 with
 * one duplicated seam column, v along the path 0..1) and `normal`: smooth round a tube and across
 * its seam, split at a profile's hard corners (see `sharp`: a diamond blade stays crisp), flat on the
 * caps. Rolled rims, handles, tusks, straps, cables, blades.
 */
export function sweepArrays(path: V3[], opts: SweepOptions = {}): { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal: Float32Array } {
  const closed = !!opts.closed;
  let ext = 0;
  for (const p of path || []) if (finite3(p)) ext = Math.max(ext, Math.abs(+p[0]), Math.abs(+p[1]), Math.abs(+p[2]));
  const eps = Math.max(1e-12, ext * 1e-9);
  let pts = cleanPoints(path, eps);
  if (closed && pts.length > 2 && len(sub(pts[0], pts[pts.length - 1])) <= eps) pts.pop();
  if (pts.length < 2) throw new Error("sweepArrays: the path needs at least two distinct [x, y, z] points");
  if (closed && pts.length < 3) throw new Error("sweepArrays: a closed path needs at least three points");
  const per = Math.round(opts.smooth ?? 0);
  if (per >= 2) pts = catmullRom(pts, per, closed);
  const n = pts.length;
  // Arc length, for the radius function and v.
  const s = new Float64Array(n + 1);
  for (let i = 1; i <= n; i++) s[i] = s[i - 1] + (i < n ? len(sub(pts[i], pts[i - 1])) : closed ? len(sub(pts[0], pts[n - 1])) : 0);
  const total = closed ? s[n] : s[n - 1];
  // Tangents, and the mitre at each corner.
  const T: P3[] = [], bend: P3[] = [], mitre: number[] = [];
  for (let i = 0; i < n; i++) {
    const hasIn = closed || i > 0, hasOut = closed || i < n - 1;
    const dIn = hasIn ? norm(sub(pts[i], pts[(i - 1 + n) % n])) : null;
    const dOut = hasOut ? norm(sub(pts[(i + 1) % n], pts[i])) : null;
    let t: P3;
    if (dIn && dOut) { t = norm([dIn[0] + dOut[0], dIn[1] + dOut[1], dIn[2] + dOut[2]]); if (!len(t)) t = dIn; }
    else t = (dIn || dOut)!;
    T.push(t);
    let k: P3 = [0, 0, 0], f = 1;
    if (dIn && dOut) {
      const kv = sub(dOut, dIn);
      if (len(kv) > 1e-6) { k = norm(kv); f = 1 / Math.max(0.25, dot(t, dIn)); }
    }
    bend.push(k); mitre.push(f);
  }
  // The first frame: the normal as close to `up` as the tangent allows.
  let up: P3 = finite3(opts.up) ? [+opts.up[0], +opts.up[1], +opts.up[2]] : [0, 0, 0];
  let N0 = norm(sub(up, scale(T[0], dot(up, T[0]))));
  if (len(N0) < 0.5) {
    const a = Math.abs(T[0][0]), b = Math.abs(T[0][1]), c = Math.abs(T[0][2]);
    up = a <= b && a <= c ? [1, 0, 0] : b <= c ? [0, 1, 0] : [0, 0, 1];
    N0 = norm(sub(up, scale(T[0], dot(up, T[0]))));
  }
  // Parallel transport by double reflection (Wang, Juttler, Zheng and Liu 2008).
  const step = (i: number, j: number, Ni: P3): P3 => {
    const v1 = sub(pts[j], pts[i]), c1 = dot(v1, v1);
    let r: P3 = Ni;
    if (c1 > 1e-30) {
      const rL = sub(Ni, scale(v1, (2 / c1) * dot(v1, Ni)));
      const tL = sub(T[i], scale(v1, (2 / c1) * dot(v1, T[i])));
      const v2 = sub(T[j], tL), c2 = dot(v2, v2);
      r = c2 > 1e-30 ? sub(rL, scale(v2, (2 / c2) * dot(v2, rL))) : rL;
    }
    return norm(sub(r, scale(T[j], dot(r, T[j]))));
  };
  const Nf: P3[] = [N0];
  for (let i = 0; i + 1 < n; i++) Nf.push(step(i, i + 1, Nf[i]));
  if (closed) {
    // Round the loop once more: the frame comes back turned by some angle; undo it gradually.
    const back = step(n - 1, 0, Nf[n - 1]);
    const alpha = Math.atan2(dot(cross(back, N0), T[0]), dot(back, N0));
    for (let i = 1; i < n; i++) {
      const phi = (alpha * s[i]) / total, b = cross(T[i], Nf[i]);
      Nf[i] = norm([Nf[i][0] * Math.cos(phi) + b[0] * Math.sin(phi), Nf[i][1] * Math.cos(phi) + b[1] * Math.sin(phi), Nf[i][2] * Math.cos(phi) + b[2] * Math.sin(phi)]);
    }
  }
  // The section: a counter-clockwise outline in (normal, binormal).
  let prof: Array<[number, number]>;
  const hasProfile = Array.isArray(opts.profile) && opts.profile.length >= 3;
  if (hasProfile) {
    prof = opts.profile!.filter((p) => Array.isArray(p) && isFinite(+p[0]) && isFinite(+p[1])).map((p): [number, number] => [+p[0], +p[1]]);
    if (prof.length > 3 && Math.abs(prof[0][0] - prof[prof.length - 1][0]) + Math.abs(prof[0][1] - prof[prof.length - 1][1]) < 1e-12) prof.pop();
    if (prof.length < 3) throw new Error("sweepArrays: a profile needs at least three [x, y] points");
    let area = 0;
    for (let j = 0; j < prof.length; j++) { const a = prof[j], b = prof[(j + 1) % prof.length]; area += a[0] * b[1] - a[1] * b[0]; }
    if (area < 0) prof.reverse();
  } else {
    const sides = Math.max(3, Math.round(opts.sides ?? 12));
    prof = [];
    for (let j = 0; j < sides; j++) { const a = (2 * Math.PI * j) / sides; prof.push([Math.cos(a), Math.sin(a)]); }
  }
  const m = prof.length;
  const rad = opts.radius ?? (hasProfile ? 1 : 0.02);
  const rOf = (t: number) => { const r = typeof rad === "function" ? Number(rad(t)) : rad; return isFinite(r) ? r : 0; };
  const perim: number[] = [0];
  for (let j = 0; j < m; j++) perim.push(perim[j] + Math.hypot(prof[(j + 1) % m][0] - prof[j][0], prof[(j + 1) % m][1] - prof[j][1]));
  const pTotal = perim[m] || 1;
  const rings = closed ? n + 1 : n;
  // Columns of a ring: a profile corner sharper than 40 degrees is two vertices (one per side, each
  // with its own side's normal), so a blade or a bar keeps hard edges; the first point is repeated
  // at the end of the ring (u = 1) for the UV seam.
  const sharp: boolean[] = [];
  const sharpDeg = typeof opts.sharp === "number" && isFinite(opts.sharp) ? opts.sharp : m <= 6 ? 0 : 40;
  const cosS = Math.cos((Math.min(180, Math.max(0, sharpDeg)) * Math.PI) / 180);
  for (let j = 0; j < m; j++) {
    if (!hasProfile) { sharp.push(false); continue; }
    const a = prof[(j - 1 + m) % m], b = prof[j], c = prof[(j + 1) % m];
    const l1 = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1, l2 = Math.hypot(c[0] - b[0], c[1] - b[1]) || 1;
    sharp.push(((b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])) / (l1 * l2) < cosS - 1e-12);
  }
  const startCol: number[] = new Array(m + 1).fill(-1), endCol: number[] = new Array(m + 1).fill(-1), colPt: number[] = [], colU: number[] = [];
  const addCol = (j: number) => { colPt.push(j % m); colU.push(perim[j] / pTotal); return colPt.length - 1; };
  for (let j = 0; j <= m; j++) {
    if (j === 0) startCol[0] = addCol(0);
    else if (j === m) endCol[m] = addCol(m);
    else if (sharp[j]) { endCol[j] = addCol(j); startCol[j] = addCol(j); }
    else endCol[j] = startCol[j] = addCol(j);
  }
  const cols = colPt.length;
  const pos: number[] = [], uv: number[] = [], idx: number[] = [];
  const ringPoint = (i: number, x: number, y: number, r: number): P3 => {
    const Ni = Nf[i], Bi = cross(T[i], Ni);
    let o: P3 = [r * (x * Ni[0] + y * Bi[0]), r * (x * Ni[1] + y * Bi[1]), r * (x * Ni[2] + y * Bi[2])];
    if (mitre[i] !== 1) { const k = bend[i], g = dot(o, k) * (mitre[i] - 1); o = [o[0] + g * k[0], o[1] + g * k[1], o[2] + g * k[2]]; }
    return [pts[i][0] + o[0], pts[i][1] + o[1], pts[i][2] + o[2]];
  };
  for (let ri = 0; ri < rings; ri++) {
    const i = ri % n, t = closed ? s[ri] / total : total > 0 ? s[i] / total : 0;
    const r = rOf(closed && ri === n ? 0 : t);
    for (let c = 0; c < cols; c++) {
      const q = ringPoint(i, prof[colPt[c]][0], prof[colPt[c]][1], r);
      pos.push(q[0], q[1], q[2]);
      uv.push(colU[c], t);
    }
  }
  for (let ri = 0; ri + 1 < rings; ri++) for (let j = 0; j < m; j++) {
    const a = ri * cols + startCol[j], b = ri * cols + endCol[j + 1], c = (ri + 1) * cols + endCol[j + 1], d = (ri + 1) * cols + startCol[j];
    idx.push(a, b, c, a, c, d);
  }
  // Normals from the faces, each weighted by its angle at the vertex (a quad split in two counts once,
  // whichever way it was split); the seam copies (and a loop's last ring) share theirs.
  const bodyVerts = pos.length / 3, nrm = new Float64Array(bodyVerts * 3);
  const P3at = (v: number): P3 => [pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]];
  for (let k = 0; k < idx.length; k += 3) {
    const vs = [idx[k], idx[k + 1], idx[k + 2]], ps = vs.map(P3at);
    const f = norm(cross(sub(ps[1], ps[0]), sub(ps[2], ps[0])));
    for (let c = 0; c < 3; c++) {
      const e1 = norm(sub(ps[(c + 1) % 3], ps[c])), e2 = norm(sub(ps[(c + 2) % 3], ps[c]));
      const ang = Math.acos(Math.max(-1, Math.min(1, dot(e1, e2))));
      nrm[vs[c] * 3] += f[0] * ang; nrm[vs[c] * 3 + 1] += f[1] * ang; nrm[vs[c] * 3 + 2] += f[2] * ang;
    }
  }
  const share = (u: number, w: number) => {
    for (let k = 0; k < 3; k++) { const sum = nrm[u * 3 + k] + nrm[w * 3 + k]; nrm[u * 3 + k] = sum; nrm[w * 3 + k] = sum; }
  };
  if (!sharp[0]) for (let ri = 0; ri < rings; ri++) share(ri * cols + startCol[0], ri * cols + endCol[m]);
  if (closed) for (let c = 0; c < cols; c++) share(c, n * cols + c);
  const normal: number[] = [];
  for (let v = 0; v < bodyVerts; v++) {
    const l = Math.hypot(nrm[v * 3], nrm[v * 3 + 1], nrm[v * 3 + 2]) || 1;
    normal.push(nrm[v * 3] / l, nrm[v * 3 + 1] / l, nrm[v * 3 + 2] / l);
  }
  if (!closed && opts.caps !== false) {
    let convex = true;
    for (let j = 0; j < m && convex; j++) {
      const a = prof[j], b = prof[(j + 1) % m], c = prof[(j + 2) % m];
      if ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < -1e-12) convex = false;
    }
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, cx = 0, cy = 0;
    for (const p of prof) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); cx += p[0] / m; cy += p[1] / m; }
    const fan = convex ? null : earClip(prof);
    for (const end of [0, n - 1]) {
      const r = rOf(end === 0 ? 0 : 1), base = pos.length / 3;
      const fn: P3 = end === 0 ? scale(T[0], -1) : T[n - 1];
      const put = (q: P3, u: number, v: number) => { pos.push(q[0], q[1], q[2]); uv.push(u, v); normal.push(fn[0], fn[1], fn[2]); };
      for (const p of prof) put(ringPoint(end, p[0], p[1], r), (p[0] - x0) / (x1 - x0 || 1), (p[1] - y0) / (y1 - y0 || 1));
      const tri = (a: number, b: number, c: number) => (end === 0 ? idx.push(a, c, b) : idx.push(a, b, c));
      if (fan) for (let k = 0; k < fan.length; k += 3) tri(base + fan[k], base + fan[k + 1], base + fan[k + 2]);
      else {
        const c = pos.length / 3;
        put(ringPoint(end, cx, cy, r), (cx - x0) / (x1 - x0 || 1), (cy - y0) / (y1 - y0 || 1));
        for (let j = 0; j < m; j++) tri(c, base + j, base + ((j + 1) % m));
      }
    }
  }
  return { pos: Float32Array.from(pos), idx: Uint32Array.from(idx), uv: Float32Array.from(uv), normal: Float32Array.from(normal) };
}

// ------------------------------------------------------------------ borders

const hashCell = (x: number, y: number, z: number): number =>
  (Math.imul(x | 0, 73856093) ^ Math.imul(y | 0, 19349663) ^ Math.imul(z | 0, 83492791)) >>> 0;

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

interface Loops { P: Float64Array; loops: number[][]; inward: P3[][]; normal: P3[][] }

/** Welded boundary loops with, at each point, the direction into the surface and its normal. */
function loopsOf(pos: Float32Array, idx: Uint32Array, tol?: number): Loops {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < pos.length; i++) { lo = Math.min(lo, pos[i]); hi = Math.max(hi, pos[i]); }
  const ext = hi >= lo ? (hi - lo) * Math.sqrt(3) : 0;
  const { map, rep, count: n } = weldIds(pos, typeof tol === "number" && tol > 0 ? tol : Math.max(1e-12, ext * 1e-6));
  const P = new Float64Array(n * 3);
  for (let v = 0; v < n; v++) { const i = rep[v] * 3; P[v * 3] = pos[i]; P[v * 3 + 1] = pos[i + 1]; P[v * 3 + 2] = pos[i + 2]; }
  // Undirected use of every edge; an edge used once is open, kept with its direction and its face.
  const use = new Map<number, number>();
  const key = (a: number, b: number) => (a < b ? a * n + b : b * n + a);
  const tris: number[] = [];
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const a = map[idx[t]], b = map[idx[t + 1]], c = map[idx[t + 2]];
    if (a === b || b === c || a === c) continue;
    tris.push(a, b, c);
    for (const [u, v] of [[a, b], [b, c], [c, a]]) { const k = key(u, v); use.set(k, (use.get(k) || 0) + 1); }
  }
  const out = new Map<number, Array<{ to: number; third: number; used: boolean }>>();
  for (let t = 0; t < tris.length; t += 3) {
    for (let e = 0; e < 3; e++) {
      const a = tris[t + e], b = tris[t + (e + 1) % 3], c = tris[t + (e + 2) % 3];
      if (use.get(key(a, b)) !== 1) continue;
      const l = out.get(a);
      if (l) l.push({ to: b, third: c, used: false }); else out.set(a, [{ to: b, third: c, used: false }]);
    }
  }
  const at = (v: number): P3 => [P[v * 3], P[v * 3 + 1], P[v * 3 + 2]];
  const loops: number[][] = [], inward: P3[][] = [], normal: P3[][] = [];
  const starts = [...out.keys()].sort((a, b) => a - b);
  for (const s0 of starts) {
    for (const first of out.get(s0)!) {
      if (first.used) continue;
      const ids: number[] = [], ins: P3[] = [], nrm: P3[] = [];
      let v = s0, e: { to: number; third: number; used: boolean } | undefined = first;
      for (let guard = 0; e && !e.used && guard < 10000000; guard++) {
        e.used = true;
        const a = at(v), b = at(e.to), c = at(e.third);
        const ab = sub(b, a), ac = sub(c, a);
        const L2 = dot(ab, ab) || 1;
        ins.push(norm(sub(ac, scale(ab, dot(ac, ab) / L2))));
        nrm.push(norm(cross(ab, ac)));
        ids.push(v);
        v = e.to;
        if (v === s0) break;
        e = (out.get(v) || []).find((x) => !x.used);
      }
      // The directions belong to the edge leaving each point; average with the edge arriving.
      const k = ids.length;
      const insV: P3[] = [], nrmV: P3[] = [];
      for (let i = 0; i < k; i++) {
        const p = (i - 1 + k) % k;
        insV.push(norm([ins[i][0] + ins[p][0], ins[i][1] + ins[p][1], ins[i][2] + ins[p][2]]));
        nrmV.push(norm([nrm[i][0] + nrm[p][0], nrm[i][1] + nrm[p][1], nrm[i][2] + nrm[p][2]]));
      }
      loops.push(ids); inward.push(insV); normal.push(nrmV);
    }
  }
  return { P, loops, inward, normal };
}

/**
 * The open borders of a mesh as loops of points, welded by position (`tol`, default a millionth of
 * the mesh's size), so a UV seam made of vertex copies is not a border. Each loop runs the way its
 * faces wind: counter-clockwise seen from the side the faces face. The first point is not repeated
 * at the end. A closed shape has none; a helmet shell or a pauldron has one per rim.
 */
export function boundaryLoops(pos: Float32Array, idx: Uint32Array, tol?: number): V3[][] {
  const L = loopsOf(pos, idx, tol);
  return L.loops.map((ids) => ids.map((v): V3 => [L.P[v * 3], L.P[v * 3 + 1], L.P[v * 3 + 2]]));
}

/**
 * A rolled rim on every open border of a shell: a closed tube of `radius` swept along each boundary
 * loop (the rolled edge of a helmet, a pauldron, a shield). `inset` moves the tube's centre into the
 * surface, away from the edge (metres; negative moves it out past the edge); `lift` moves it along the
 * surface normal (half a solidify thickness centres it on a thick shell). `relax` passes of smoothing
 * take the stair-steps out of a remeshed border. Loops shorter than three points are skipped. Returns
 * one mesh of all the tubes with `uv` and smooth `normal`, as `sweepArrays` does.
 */
export function rimArrays(pos: Float32Array, idx: Uint32Array,
  opts: { radius: number; sides?: number; inset?: number; lift?: number; relax?: number; tol?: number }): { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal: Float32Array } {
  if (!opts || !(opts.radius > 0)) throw new Error("rimArrays: radius must be above zero, in metres");
  const L = loopsOf(pos, idx, opts.tol);
  const inset = opts.inset ?? 0, lift = opts.lift ?? 0, relax = Math.max(0, Math.round(opts.relax ?? 0));
  const parts: Array<{ pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal: Float32Array }> = [];
  L.loops.forEach((ids, li) => {
    const k = ids.length;
    if (k < 3) return;
    let pts: P3[] = ids.map((v, i): P3 => {
      const w = L.inward[li][i], nn = L.normal[li][i];
      return [L.P[v * 3] + inset * w[0] + lift * nn[0], L.P[v * 3 + 1] + inset * w[1] + lift * nn[1], L.P[v * 3 + 2] + inset * w[2] + lift * nn[2]];
    });
    // Taubin passes along the loop: in, then a little further out, so the loop does not shrink.
    for (let it = 0; it < relax; it++) {
      for (const f of [0.5, -0.53]) {
        pts = pts.map((p, i): P3 => {
          const a = pts[(i - 1 + k) % k], b = pts[(i + 1) % k];
          return [p[0] + f * ((a[0] + b[0]) / 2 - p[0]), p[1] + f * ((a[1] + b[1]) / 2 - p[1]), p[2] + f * ((a[2] + b[2]) / 2 - p[2])];
        });
      }
    }
    parts.push(sweepArrays(pts, { radius: opts.radius, sides: opts.sides ?? 12, closed: true, up: L.normal[li][0] }));
  });
  let np = 0, ni = 0;
  for (const p of parts) { np += p.pos.length; ni += p.idx.length; }
  const outPos = new Float32Array(np), outIdx = new Uint32Array(ni), outUv = new Float32Array((np / 3) * 2), outN = new Float32Array(np);
  let po = 0, io = 0;
  for (const p of parts) {
    outPos.set(p.pos, po); outUv.set(p.uv, (po / 3) * 2); outN.set(p.normal, po);
    for (let i = 0; i < p.idx.length; i++) outIdx[io + i] = p.idx[i] + po / 3;
    po += p.pos.length; io += p.idx.length;
  }
  return { pos: outPos, idx: outIdx, uv: outUv, normal: outN };
}

// ------------------------------------------------------------------ reach

/**
 * Two-bone reach for an arm or a leg: where the elbow (or knee) goes so a chain of `lengths`
 * [upper, lower] from `root` ends exactly on `target`, bending toward the point `pole`.
 * The pole is a point the joint should point at: behind for an elbow, in front for a knee. In
 * range, `end` IS `target` and
 * `reached` is true; out of range the chain points straight at the target, fully extended; too
 * close, it folds as far as it can along the line to the target. Place the fist on the grip with it.
 */
export function reach(root: V3, target: V3, lengths: [number, number], pole: V3): { joint: V3; end: V3; reached: boolean } {
  if (!finite3(root) || !finite3(target)) throw new Error("reach: root and target must be [x, y, z]");
  const L1 = Number(lengths?.[0]), L2 = Number(lengths?.[1]);
  if (!(L1 > 0) || !(L2 > 0)) throw new Error("reach: lengths must be two lengths above zero, [upper, lower]");
  const R: P3 = [+root[0], +root[1], +root[2]];
  const toT = sub(target, R), d = len(toT);
  const poleV: P3 = finite3(pole) ? sub(pole, R) : [0, 0, 0];
  let dir: P3 = d > 1e-12 ? scale(toT, 1 / d) : len(poleV) > 1e-12 ? norm(poleV) : [0, -1, 0];
  // The bend direction: the pole, off the line from the root to the target.
  let side = sub(poleV, scale(dir, dot(poleV, dir)));
  if (len(side) < 1e-9) {
    const a = Math.abs(dir[0]), b = Math.abs(dir[1]), c = Math.abs(dir[2]);
    side = cross(dir, a <= b && a <= c ? [1, 0, 0] : b <= c ? [0, 1, 0] : [0, 0, 1]);
  }
  side = norm(side);
  const along = (k: number): P3 => [R[0] + dir[0] * k, R[1] + dir[1] * k, R[2] + dir[2] * k];
  if (d > L1 + L2) return { joint: along(L1), end: along(L1 + L2), reached: false };
  if (d < Math.abs(L1 - L2) || d <= 1e-12) {
    if (Math.abs(L1 - L2) <= 1e-12 && d <= 1e-12) {
      return { joint: [R[0] + side[0] * L1, R[1] + side[1] * L1, R[2] + side[2] * L1], end: [+target[0], +target[1], +target[2]], reached: true };
    }
    if (d <= 1e-12) dir = norm(poleV).some((x) => x !== 0) ? norm(poleV) : dir;
    return { joint: along(L1 >= L2 ? L1 : -L1), end: along(Math.abs(L1 - L2)), reached: false };
  }
  const a = (L1 * L1 - L2 * L2 + d * d) / (2 * d), h = Math.sqrt(Math.max(0, L1 * L1 - a * a));
  return {
    joint: [R[0] + dir[0] * a + side[0] * h, R[1] + dir[1] * a + side[1] * h, R[2] + dir[2] * a + side[2] * h],
    end: [+target[0], +target[1], +target[2]],
    reached: true,
  };
}
