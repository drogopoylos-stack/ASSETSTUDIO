// A hand for a game: one light mesh, a skeleton, weights from that skeleton, and correctives that
// put the skin back on the anatomy in every pose.
//
// hands.ts makes the best-shaped hand this Studio can make, but as a SHAPE: every pose is its own
// field, polygonised into its own mesh of 15,000 to 29,000 triangles. A game cannot animate that.
// It needs the Blender result: one mesh, an armature, weights, poses as bone rotations. In Blender
// that result costs three manual passes, and each one is where hands usually go wrong:
//
//   weights     automatic (heat) weights bleed between fingers that touch and blur the knuckles,
//               so somebody paints them by hand;
//   poses       a bent finger made only by bones (linear blend skinning) loses volume at every
//               joint and the knuckles turn into pinched creases;
//   correctives so somebody sculpts a corrective shape key for each bad pose, by eye.
//
// Here all three come from what hands.ts already knows, and they are measured instead of judged:
//
//   * THE SKELETON is the field's own joints. Measured over every pose hands.ts makes: the finger
//     bases never move and the three phalanges keep their lengths, so each finger is a rigid chain
//     and bones reproduce it exactly. (Only the thumb's metacarpal changes length, by up to 6 mm of
//     28 in a grip; the corrective takes the rest.)
//   * THE WEIGHTS come from the skeleton's geometry: a vertex belongs to the nearest bone by distance
//     over that bone's radius, and it is blended between two bones only inside a band at the joint
//     plane. No weight can reach a neighbouring finger, because the band is along the finger.
//   * A CORRECTIVE is the difference between the skinned pose and the field of that same pose: each
//     vertex is projected onto the posed field's surface, only if it lands on the same part of the
//     hand it came from, and the difference is stored in rest space. The skinned hand with its
//     corrective then IS the anatomical hand, to the error `fit` reports.
//
// Correctives are per finger (each finger's share of the fist), so any mix of curls has one, and
// one for the grip, whose handle channel the fist does not have.
//
// Plain numbers and arrays, deterministic, no three. The mesh comes in from outside (ops.ts makes
// it with isosurface + decimate), because this file must not import ops.ts, which imports it.

import { handField, type Hand, type HandOpts, type HandPart, type HandPose, type ThumbPose } from "./hands";

type V3 = [number, number, number];
type M3 = number[];          // 3x3, column-major: [x0,x1,x2, y0,y1,y2, z0,z1,z2]

export interface RigBone {
  name: string;
  /** Index of the parent bone, -1 for the root. Parents always come before children. */
  parent: number;
  /** Rest head and tail, in the hand frame (metres). */
  head: V3;
  tail: V3;
  /** Which part of the hand the bone moves: a finger name, 'thumb', or 'palm'. */
  part: HandPart | "palm";
}

/** What a pose asks for: the hands.ts options that shape a pose. */
export interface HandPoseSpec {
  pose?: HandPose;
  curl?: number[];
  thumb?: ThumbPose;
  gripRadius?: number;
}

export interface PoseFit {
  /** Distance from the skinned vertices to the pose's own surface, millimetres. */
  meanMm: number;
  p95Mm: number;
  maxMm: number;
  /** Share of vertices more than half a millimetre INSIDE the pose's surface: lost volume. */
  sunk: number;
}

export interface HandPoseResult {
  /** Per bone, the skinning matrix (posed world x rest inverse), column-major 4x4, packed. */
  skin: Float32Array;
  /** Per bone, the posed world transform: position and quaternion [x, y, z, w]. Rest bones carry
   *  no rotation, so this is also each bone's world rotation relative to its rest. */
  heads: V3[];
  rotations: [number, number, number, number][];
  /** The morph weights this pose wants, by name. */
  morphs: Record<string, number>;
  /** The pose's own field: the shape the skinned hand is fitted to. */
  hand: Hand;
}

export interface HandRig {
  side: "L" | "R";
  bones: RigBone[];
  /** 4 bone indices and 4 weights per vertex. */
  skinIndex: Uint16Array;
  skinWeight: Float32Array;
  /** Correctives in REST space (glTF morph targets are added before skinning). */
  morphs: { name: string; delta: Float32Array }[];
  /** Which part each vertex belongs to in the rest field. */
  parts: HandPart[];
  /** Bone matrices and morph weights for a pose. */
  pose(spec: HandPoseSpec): HandPoseResult;
  /** Skin the rest mesh into a pose: with or without its correctives. */
  deform(spec: HandPoseSpec, correctives?: boolean): Float32Array;
  /** How far the deformed mesh is from the pose's own surface. */
  fit(spec: HandPoseSpec, correctives?: boolean): PoseFit;
  /** The options the hand was made with, resolved. */
  opts: HandOpts;
}

const FINGER_NAMES4: HandPart[] = ["index", "middle", "ring", "little"];
const FINGER_NAMES3: HandPart[] = ["index", "middle", "little"];

// ------------------------------------------------------------------ vectors
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: V3): V3 => { const l = len(a); return l > 1e-12 ? [a[0] / l, a[1] / l, a[2] / l] : [0, 0, 1]; };
const lerp3 = (a: V3, b: V3, t: number): V3 => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const smooth = (e0: number, e1: number, x: number) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };

const m3mul = (a: M3, v: V3): V3 => [a[0] * v[0] + a[3] * v[1] + a[6] * v[2], a[1] * v[0] + a[4] * v[1] + a[7] * v[2], a[2] * v[0] + a[5] * v[1] + a[8] * v[2]];
const m3mm = (a: M3, b: M3): M3 => {
  const o: M3 = new Array(9).fill(0);
  for (let c = 0; c < 3; c++) for (let r = 0; r < 3; r++) o[c * 3 + r] = a[r] * b[c * 3] + a[3 + r] * b[c * 3 + 1] + a[6 + r] * b[c * 3 + 2];
  return o;
};
const m3t = (a: M3): M3 => [a[0], a[3], a[6], a[1], a[4], a[7], a[2], a[5], a[8]];
const cols = (x: V3, y: V3, z: V3): M3 => [x[0], x[1], x[2], y[0], y[1], y[2], z[0], z[1], z[2]];
const IDENT: M3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];

function m3inv(m: M3): M3 | null {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-12) return null;
  const k = 1 / det;
  return [A * k, -(b * i - c * h) * k, (b * f - c * e) * k, B * k, (a * i - c * g) * k, -(a * f - c * d) * k, C * k, -(a * h - b * g) * k, (a * e - b * d) * k];
}

function quatOf(m: M3): [number, number, number, number] {
  // column-major m: m00=m[0], m10=m[1], m20=m[2], m01=m[3], m11=m[4], m21=m[5], m02=m[6], m12=m[7], m22=m[8]
  const m00 = m[0], m10 = m[1], m20 = m[2], m01 = m[3], m11 = m[4], m21 = m[5], m02 = m[6], m12 = m[7], m22 = m[8];
  const tr = m00 + m11 + m22;
  let x: number, y: number, z: number, w: number;
  if (tr > 0) { const s = 0.5 / Math.sqrt(tr + 1); w = 0.25 / s; x = (m21 - m12) * s; y = (m02 - m20) * s; z = (m10 - m01) * s; }
  else if (m00 > m11 && m00 > m22) { const s = 2 * Math.sqrt(1 + m00 - m11 - m22); w = (m21 - m12) / s; x = 0.25 * s; y = (m01 + m10) / s; z = (m02 + m20) / s; }
  else if (m11 > m22) { const s = 2 * Math.sqrt(1 + m11 - m00 - m22); w = (m02 - m20) / s; x = (m01 + m10) / s; y = 0.25 * s; z = (m12 + m21) / s; }
  else { const s = 2 * Math.sqrt(1 + m22 - m00 - m11); w = (m10 - m01) / s; x = (m02 + m20) / s; y = (m12 + m21) / s; z = 0.25 * s; }
  const l = Math.hypot(x, y, z, w) || 1;
  const sg = w < 0 ? -1 : 1;
  return [sg * x / l, sg * y / l, sg * z / l, sg * w / l];
}

/** Distance from p to the segment a-b, and how far along it (0..1). */
function segDist(p: V3, a: V3, b: V3): { d: number; t: number } {
  const ab = sub(b, a), l2 = dot(ab, ab);
  const t = l2 > 1e-18 ? Math.min(1, Math.max(0, dot(sub(p, a), ab) / l2)) : 0;
  return { d: len(sub(p, lerp3(a, b, t))), t };
}

// ------------------------------------------------------------------ the skeleton

export interface Layout { bones: RigBone[]; radius: number[] }

/** Bones from a set of joints, always in the same order, so a pose's joints map onto the rest's. */
export function layout(h: Hand): Layout {
  const J = h.joints;
  const names = h.fingers === 3 ? FINGER_NAMES3 : FINGER_NAMES4;
  const bones: RigBone[] = [];
  const radius: number[] = [];
  // The root covers the heel and the middle of the palm, not the knuckles: reaching them with a wide
  // radius, it won the skin between the fingers.
  const mid = lerp3([0, 0, 0], J.fingers[Math.min(1, J.fingers.length - 1)][0], 0.7);
  bones.push({ name: "hand", parent: -1, head: [0, 0, 0], tail: mid, part: "palm" });
  radius.push(Math.max(...J.fingerRadii.map((r) => r[0])) * 1.8);
  J.fingers.forEach((c, i) => {
    const n = names[i];
    const r = J.fingerRadii[i];
    // The metacarpal starts a little way out from the wrist joint, where it does in a hand.
    const meta = bones.push({ name: n + "_meta", parent: 0, head: lerp3([0, 0, 0], c[0], 0.18), tail: c[0], part: "palm" }) - 1;
    radius.push(r[0] * 1.6);
    const p1 = bones.push({ name: n + "_1", parent: meta, head: c[0], tail: c[1], part: n }) - 1;
    radius.push(r[0]);
    const p2 = bones.push({ name: n + "_2", parent: p1, head: c[1], tail: c[2], part: n }) - 1;
    radius.push(r[1]);
    bones.push({ name: n + "_3", parent: p2, head: c[2], tail: c[4], part: n });
    radius.push(r[2]);
  });
  const T = J.thumb, tr = J.thumbRadii;
  const t0 = bones.push({ name: "thumb_meta", parent: 0, head: T[0], tail: T[1], part: "thumb" }) - 1;
  radius.push(tr[0]);
  const t1 = bones.push({ name: "thumb_1", parent: t0, head: T[1], tail: T[2], part: "thumb" }) - 1;
  radius.push(tr[1]);
  bones.push({ name: "thumb_2", parent: t1, head: T[2], tail: T[4], part: "thumb" });
  radius.push(tr[2]);
  return { bones, radius };
}

/** Each bone's rest frame (columns: across, up, along) and its posed one, by parallel transport:
 *  a child's "across" axis is its parent's, carried by the parent's rotation, so no twist is added
 *  that the joints did not ask for. */
function rotations(rest: RigBone[], posed: RigBone[]): M3[] {
  const R: M3[] = [];
  const restX: V3[] = [];
  const posedX: V3[] = [];
  for (let b = 0; b < rest.length; b++) {
    const zr = norm(sub(rest[b].tail, rest[b].head));
    const zp = norm(sub(posed[b].tail, posed[b].head));
    const p = rest[b].parent;
    const refR: V3 = p < 0 ? [1, 0, 0] : restX[p];
    const refP: V3 = p < 0 ? [1, 0, 0] : posedX[p];
    let xr = sub(refR, mul(zr, dot(refR, zr)));
    if (len(xr) < 1e-6) xr = cross([0, 1, 0], zr);
    xr = norm(xr);
    // The rest across axis, carried into the pose by the parent's rotation, then made square to the bone.
    const carried = p < 0 ? xr : m3mul(R[p], xr);
    let xp = sub(carried, mul(zp, dot(carried, zp)));
    if (len(xp) < 1e-6) xp = refP;
    xp = norm(xp);
    const Fr = cols(xr, cross(zr, xr), zr);
    const Fp = cols(xp, cross(zp, xp), zp);
    // The root does not turn: a hand is placed by its own transform, not by its root bone.
    R.push(p < 0 ? IDENT : m3mm(Fp, m3t(Fr)));
    restX.push(xr);
    posedX.push(xp);
  }
  return R;
}

// ------------------------------------------------------------------ weights

/**
 * Four bones and weights per vertex.
 *
 * The nearest bone wins, measured as distance over that bone's radius. Two bones share a vertex
 * only near the joint between them: across a band of `band` radii centred on the plane through the
 * joint, square to the two bones' mean direction. The palm is the exception: the root and the
 * metacarpals blend smoothly by distance, because a palm bends as one surface.
 */
function weights(pos: Float32Array, L: Layout, parts: HandPart[], band = 0.6): { index: Uint16Array; weight: Float32Array } {
  const n = pos.length / 3, B = L.bones;
  const index = new Uint16Array(n * 4), weight = new Float32Array(n * 4);
  const kids: number[][] = B.map(() => []);
  B.forEach((b, i) => { if (b.parent >= 0) kids[b.parent].push(i); });
  const palm = B.map((b) => b.part === "palm");
  const dir = B.map((b) => norm(sub(b.tail, b.head)));
  const nd = new Float64Array(B.length);
  const metaOf = (b: RigBone) => b.name.replace(/_meta$/, "");
  for (let v = 0; v < n; v++) {
    const p: V3 = [pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]];
    // THE ANATOMY DECIDES WHICH BONES MAY HAVE A VERTEX. A thumb vertex takes thumb bones and the root;
    // a finger vertex its own finger's bones and the palm's. The skin between two digits is nearest to
    // whichever bone is nearest, and that was the other digit's as often as its own.
    const vp = parts[v];
    const digit = vp !== "palm" && vp !== "wrist" && vp !== "nail";
    let best = -1;
    for (let b = 0; b < B.length; b++) {
      const bp = B[b].part;
      const ok = !digit || bp === vp || (bp === "palm" && (vp === "thumb" ? B[b].parent < 0 : metaOf(B[b]) === vp || B[b].parent < 0));
      nd[b] = ok ? segDist(p, B[b].head, B[b].tail).d / L.radius[b] : Infinity;
      if (best < 0 || nd[b] < nd[best]) best = b;
    }
    const w = new Map<number, number>();
    const joint = (parent: number, child: number, s: number) => {
      // s: signed distance past the joint, along the bones, in radii of the child.
      const wc = smooth(-band, band, s);
      return { wp: 1 - wc, wc };
    };
    if (palm[best]) {
      // The palm: a soft blend over the palm bones by distance.
      let tot = 0;
      const k = 5;
      for (let b = 0; b < B.length; b++) if (palm[b]) { const e = Math.exp(-k * (nd[b] - nd[best])); if (e > 0.02) { w.set(b, e); tot += e; } }
      for (const [b, e] of w) w.set(b, e / tot);
      // ...and a finger base takes over at its knuckle: the thumb from the root, only near the thumb.
      for (const c of kids[best]) {
        if (palm[c] || nd[c] > nd[best] + 0.5) continue;
        const a = norm(add(dir[best], dir[c]));
        const s = dot(sub(p, B[c].head), a) / L.radius[c];
        const { wc } = joint(best, c, s);
        if (wc > 0) { for (const [b, e] of w) w.set(b, e * (1 - wc)); w.set(c, (w.get(c) || 0) + wc); }
      }
    } else {
      const par = B[best].parent;
      const a0 = par >= 0 ? norm(add(dir[par], dir[best])) : dir[best];
      const sHead = par >= 0 ? dot(sub(p, B[best].head), a0) / L.radius[best] : Infinity;
      const child = kids[best].find((c) => !palm[c]);
      const a1 = child !== undefined ? norm(add(dir[best], dir[child])) : dir[best];
      const sTail = child !== undefined ? dot(sub(p, B[child].head), a1) / L.radius[child] : -Infinity;
      if (sHead < band && par >= 0) {
        const { wp, wc } = joint(par, best, sHead);
        w.set(par, wp);
        w.set(best, wc);
      } else if (sTail > -band && child !== undefined) {
        const { wp, wc } = joint(best, child, sTail);
        w.set(best, wp);
        w.set(child, wc);
      } else {
        w.set(best, 1);
      }
    }
    const top = [...w.entries()].filter(([, e]) => e > 1e-4).sort((x, y) => y[1] - x[1]).slice(0, 4);
    const tot = top.reduce((s, [, e]) => s + e, 0) || 1;
    top.forEach(([b, e], j) => { index[v * 4 + j] = b; weight[v * 4 + j] = e / tot; });
  }
  return { index, weight };
}

// ------------------------------------------------------------------ skinning

function skinMatrices(rest: RigBone[], posed: RigBone[]): { R: M3[]; t: V3[] } {
  const R = rotations(rest, posed);
  // posed world = T(head_p) R ; rest world = T(head_r) ; skin = T(head_p) R T(-head_r)
  const t = rest.map((b, i) => sub(posed[i].head, m3mul(R[i], b.head)));
  return { R, t };
}

function blended(v: number, index: Uint16Array, weight: Float32Array, R: M3[], t: V3[]): { M: M3; tr: V3 } {
  const M: M3 = new Array(9).fill(0);
  const tr: V3 = [0, 0, 0];
  for (let j = 0; j < 4; j++) {
    const w = weight[v * 4 + j];
    if (!w) continue;
    const b = index[v * 4 + j];
    for (let k = 0; k < 9; k++) M[k] += R[b][k] * w;
    tr[0] += t[b][0] * w; tr[1] += t[b][1] * w; tr[2] += t[b][2] * w;
  }
  return { M, tr };
}

function skinAll(pos: Float32Array, index: Uint16Array, weight: Float32Array, R: M3[], t: V3[], morph?: Float32Array): Float32Array {
  const n = pos.length / 3, out = new Float32Array(pos.length);
  for (let v = 0; v < n; v++) {
    const p: V3 = [pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]];
    if (morph) { p[0] += morph[v * 3]; p[1] += morph[v * 3 + 1]; p[2] += morph[v * 3 + 2]; }
    const { M, tr } = blended(v, index, weight, R, t);
    const q = add(m3mul(M, p), tr);
    out[v * 3] = q[0]; out[v * 3 + 1] = q[1]; out[v * 3 + 2] = q[2];
  }
  return out;
}

// ------------------------------------------------------------------ correctives

/** Pull skinned vertices onto the pose's own surface. Returns the WORLD (hand frame) moves. A vertex
 *  that would land on another part of the hand, or would have to move further than `maxMove`, keeps
 *  its skinned place: a fist presses fingers together, and the nearest surface there may be the
 *  neighbour's. */
/** Why vertices were refused in the last projection: for tests and tuning. */
export const projectStats = { moved: 0, noConverge: 0, tooFar: 0, otherPart: 0, pairs: {} as Record<string, number> };

function project(skinned: Float32Array, h: Hand, parts: HandPart[], maxMove: number): Float32Array {
  const n = skinned.length / 3, out = new Float32Array(skinned.length);
  Object.assign(projectStats, { moved: 0, noConverge: 0, tooFar: 0, otherPart: 0, pairs: {} });
  const e = h.cell * 0.3;
  const f = h.field;
  for (let v = 0; v < n; v++) {
    let x = skinned[v * 3], y = skinned[v * 3 + 1], z = skinned[v * 3 + 2];
    const x0 = x, y0 = y, z0 = z;
    let ok = false;
    for (let it = 0; it < 8; it++) {
      const d = f(x, y, z);
      if (Math.abs(d) < 1e-5) { ok = true; break; }
      const gx = (f(x + e, y, z) - f(x - e, y, z)) / (2 * e);
      const gy = (f(x, y + e, z) - f(x, y - e, z)) / (2 * e);
      const gz = (f(x, y, z + e) - f(x, y, z - e)) / (2 * e);
      const g2 = gx * gx + gy * gy + gz * gz;
      if (g2 < 1e-10) break;
      let s = d / g2;
      const step = Math.abs(s) * Math.sqrt(g2);
      if (step > h.cell) s *= h.cell / step;
      x -= gx * s; y -= gy * s; z -= gz * s;
      ok = Math.abs(f(x, y, z)) < h.cell * 0.05;
    }
    const mv = Math.hypot(x - x0, y - y0, z - z0);
    if (!ok) { projectStats.noConverge++; continue; }
    if (mv > maxMove) { projectStats.tooFar++; continue; }
    // Refused only when it lands on ANOTHER digit: the thumb's base and every knuckle sit where the
    // labels change from a digit to the palm, and staying on the same surface there is right.
    const digit = (p: HandPart) => (p === "palm" || p === "wrist" || p === "nail" ? "" : p);
    const lab = h.partAt(x, y, z), want = parts[v];
    const same = !digit(lab) || !digit(want) || digit(lab) === digit(want);
    if (!same) { projectStats.otherPart++; const k = want + ">" + lab; projectStats.pairs[k] = (projectStats.pairs[k] || 0) + 1; continue; }
    projectStats.moved++;
    out[v * 3] = x - x0; out[v * 3 + 1] = y - y0; out[v * 3 + 2] = z - z0;
  }
  return out;
}

/** The refused vertices get the average of their neighbours' moves, repeated until it settles, so they
 *  do not leave a dent beside neighbours that moved. A vertex that WAS projected keeps its move: an
 *  average over it only made the corrective smaller. */
function smoothMoves(d: Float32Array, idx: Uint32Array, passes = 12): Float32Array {
  const n = d.length / 3;
  const nb: number[][] = Array.from({ length: n }, () => []);
  for (let i = 0; i < idx.length; i += 3) {
    const a = idx[i], b = idx[i + 1], c = idx[i + 2];
    nb[a].push(b, c); nb[b].push(a, c); nb[c].push(a, b);
  }
  const fixed = new Uint8Array(n);
  for (let v = 0; v < n; v++) fixed[v] = d[v * 3] || d[v * 3 + 1] || d[v * 3 + 2] ? 1 : 0;
  let cur = d;
  for (let p = 0; p < passes; p++) {
    const nx = new Float32Array(cur.length);
    for (let v = 0; v < n; v++) {
      if (fixed[v]) { nx[v * 3] = cur[v * 3]; nx[v * 3 + 1] = cur[v * 3 + 1]; nx[v * 3 + 2] = cur[v * 3 + 2]; continue; }
      let sx = 0, sy = 0, sz = 0, c = 0;
      for (const u of nb[v]) { sx += cur[u * 3]; sy += cur[u * 3 + 1]; sz += cur[u * 3 + 2]; c++; }
      if (c) { nx[v * 3] = sx / c; nx[v * 3 + 1] = sy / c; nx[v * 3 + 2] = sz / c; }
    }
    cur = nx;
  }
  return cur;
}

/** World moves to rest-space morph deltas: through the inverse of each vertex's blended rotation. */
function toRest(moves: Float32Array, index: Uint16Array, weight: Float32Array, R: M3[], t: V3[]): Float32Array {
  const n = moves.length / 3, out = new Float32Array(moves.length);
  for (let v = 0; v < n; v++) {
    if (!moves[v * 3] && !moves[v * 3 + 1] && !moves[v * 3 + 2]) continue;
    const { M } = blended(v, index, weight, R, t);
    const inv = m3inv(M);
    if (!inv) continue;
    const q = m3mul(inv, [moves[v * 3], moves[v * 3 + 1], moves[v * 3 + 2]]);
    out[v * 3] = q[0]; out[v * 3 + 1] = q[1]; out[v * 3 + 2] = q[2];
  }
  return out;
}

function fitOf(p: Float32Array, h: Hand): PoseFit {
  const n = p.length / 3, d: number[] = [];
  let sunk = 0;
  for (let v = 0; v < n; v++) {
    const f = h.field(p[v * 3], p[v * 3 + 1], p[v * 3 + 2]);
    d.push(Math.abs(f) * 1000);
    if (f < -0.0005) sunk++;
  }
  d.sort((a, b) => a - b);
  const r = (x: number) => Math.round(x * 100) / 100;
  return { meanMm: r(d.reduce((s, x) => s + x, 0) / (n || 1)), p95Mm: r(d[Math.floor(n * 0.95)] || 0), maxMm: r(d[n - 1] || 0), sunk: r(sunk / (n || 1)) };
}

// ------------------------------------------------------------------ the rig

/** The rest pose the mesh must be made in: open, with the fingers fanned out so none touch. */
export function restOpts(opts: HandOpts): HandOpts {
  return { ...opts, pose: "open", curl: undefined, thumb: undefined, spread: opts.spread ?? 2.2 };
}

/**
 * Rig a hand mesh. `pos`/`idx` must be the surface of `handField(restOpts(opts))` in the hand
 * frame (ops.riggedHand makes it). `opts.pose` is ignored: the rest pose is always that one.
 */
export function rigHand(opts: HandOpts, pos: Float32Array, idx: Uint32Array): HandRig {
  const base: HandOpts = { ...opts, pose: "open", curl: undefined, thumb: undefined, spread: undefined };
  const rest = handField(restOpts(opts));
  const L = layout(rest);
  const n = pos.length / 3;
  const parts: HandPart[] = [];
  for (let v = 0; v < n; v++) parts.push(rest.partAt(pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]));
  const { index, weight } = weights(pos, L, parts);
  const maxMove = Math.max(...rest.joints.fingerRadii.map((r) => r[0])) * 0.6;
  const fingerNames = rest.fingers === 3 ? FINGER_NAMES3 : FINGER_NAMES4;

  const posedLayout = (h: Hand) => layout(h).bones;
  const corrective = (spec: HandPoseSpec) => {
    const h = handField({ ...base, ...spec });
    const { R, t } = skinMatrices(L.bones, posedLayout(h));
    const skinned = skinAll(pos, index, weight, R, t);
    const moves = smoothMoves(project(skinned, h, parts, maxMove), idx);
    return toRest(moves, index, weight, R, t);
  };

  // The fist, split by finger: each finger's own vertices (and the thumb's) get their share, so a
  // single curled finger has a corrective too. The palm goes with the finger nearest it.
  const fist = corrective({ pose: "fist" });
  const owner = new Int16Array(n).fill(-1);          // finger index, 99 for the thumb
  for (let v = 0; v < n; v++) {
    let bw = 0, bb = 0;
    for (let j = 0; j < 4; j++) if (weight[v * 4 + j] > bw) { bw = weight[v * 4 + j]; bb = index[v * 4 + j]; }
    const b = L.bones[bb];
    if (b.part === "thumb") owner[v] = 99;
    else {
      const f = fingerNames.indexOf(b.part as HandPart);
      if (f >= 0) owner[v] = f;
      else if (b.name.endsWith("_meta")) owner[v] = fingerNames.indexOf(b.name.replace("_meta", "") as HandPart);
    }
  }
  const morphs: { name: string; delta: Float32Array }[] = [];
  fingerNames.forEach((name, f) => {
    const d = new Float32Array(pos.length);
    for (let v = 0; v < n; v++) if (owner[v] === f) { d[v * 3] = fist[v * 3]; d[v * 3 + 1] = fist[v * 3 + 1]; d[v * 3 + 2] = fist[v * 3 + 2]; }
    morphs.push({ name: name + "_curl", delta: d });
  });
  // The thumb's share, and the palm's: what the root bone carries (the heel, the back of the hand)
  // follows the curl of the fingers as a whole.
  for (const [name, who] of [["thumb_curl", 99], ["palm_curl", -1]] as [string, number][]) {
    const d = new Float32Array(pos.length);
    for (let v = 0; v < n; v++) if (owner[v] === who) { d[v * 3] = fist[v * 3]; d[v * 3 + 1] = fist[v * 3 + 1]; d[v * 3 + 2] = fist[v * 3 + 2]; }
    morphs.push({ name, delta: d });
  }
  // The grip as a whole: its channel and its heel do not exist in the fist.
  morphs.push({ name: "grip", delta: corrective({ pose: "grip" }) });
  const restCurl = handField({ ...base, pose: "fist" });

  const fistFlex = restCurl.joints.flexion.map((f) => f[0] + f[1] + f[2]);
  // How far the thumb has turned: the angle of its two outer bones away from rest, as a share of
  // the fist's. A tip distance counted a thumb that only slid with the palm as a bent one.
  const thumbDirs = (h: Hand) => [norm(sub(h.joints.thumb[2], h.joints.thumb[1])), norm(sub(h.joints.thumb[4], h.joints.thumb[2]))];
  const restDirs = thumbDirs(rest);
  const turn = (h: Hand) => thumbDirs(h).reduce((s, d, i) => s + Math.acos(Math.min(1, Math.max(-1, dot(d, restDirs[i])))), 0);
  const thumbSpan = turn(restCurl) || 1;

  const pose = (spec: HandPoseSpec): HandPoseResult => {
    const h = handField({ ...base, ...spec });
    const posed = posedLayout(h);
    const { R, t } = skinMatrices(L.bones, posed);
    const skin = new Float32Array(L.bones.length * 16);
    R.forEach((m, b) => {
      skin.set([m[0], m[1], m[2], 0, m[3], m[4], m[5], 0, m[6], m[7], m[8], 0, t[b][0], t[b][1], t[b][2], 1], b * 16);
    });
    const m: Record<string, number> = {};
    for (const mo of morphs) m[mo.name] = 0;
    if (h.pose === "grip") m.grip = 1;
    else {
      h.joints.flexion.forEach((fx, f) => { m[fingerNames[f] + "_curl"] = Math.min(1, Math.max(0, (fx[0] + fx[1] + fx[2]) / (fistFlex[f] || 1))); });
      m.thumb_curl = Math.min(1, Math.max(0, turn(h) / thumbSpan));
      const fs = fingerNames.map((f) => m[f + "_curl"]);
      m.palm_curl = fs.reduce((a, b) => a + b, 0) / (fs.length || 1);
    }
    return { skin, heads: posed.map((b) => b.head), rotations: R.map(quatOf), morphs: m, hand: h };
  };

  const deform = (spec: HandPoseSpec, correctives = true): Float32Array => {
    const r = pose(spec);
    const { R, t } = skinMatrices(L.bones, posedLayout(r.hand));
    let morph: Float32Array | undefined;
    if (correctives) {
      morph = new Float32Array(pos.length);
      for (const mo of morphs) {
        const w = r.morphs[mo.name] || 0;
        if (!w) continue;
        for (let i = 0; i < morph.length; i++) morph[i] += mo.delta[i] * w;
      }
    }
    return skinAll(pos, index, weight, R, t, morph);
  };

  return {
    side: rest.side,
    bones: L.bones,
    skinIndex: index,
    skinWeight: weight,
    morphs,
    parts,
    pose,
    deform,
    fit: (spec, correctives = true) => fitOf(deform(spec, correctives), handField({ ...base, ...spec })),
    opts: base,
  };
}
