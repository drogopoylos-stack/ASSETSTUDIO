// Stylised hands as ONE signed-distance field, and the maths that puts a gripping fist on a handle.
//
// Why this exists: the goblin A/B (data/ab/goblin/). The user's verdict was that the Studio goblin
// beat the Blender one everywhere except the hands — "badly placed and not completely created":
// mitten fists, an open hand floating beside the shield, an open cuff at the wrist. Every
// character an agent builds has hands and most of them hold something, so a hand is an engine
// capability here instead of a pile of capsules placed by eye each time.
//
// What a hand is, in this file: a palm slab with a domed back, knuckle heads, a distal pad, thenar
// and hypothenar pads; fingers of three phalanges each (round cones), with knuckle bulges on the
// outside of every joint, flesh bunched on the inside of every bent joint, and nail plates; a
// thumb of metacarpal + two phalanges with its own nail; a wrist stub closed by a rounded cap.
// Every part is joined by a polynomial smooth minimum, so the surface is ONE closed skin with
// webbing between the fingers and no seam anywhere — polygonise it with ops.isosurface.
//
// Poses are joint angles, except 'grip', which is SOLVED: each finger is a chain of tangents
// round the handle's circle (every phalanx rests on the handle, the joints stand off it, as real
// fingers do), balanced so each phalanx touches near its middle; the handle is placed so that
// chain closes with the handle pressed 0.4 of its radius into the palm; the heel of the hand
// closes round its back; the thumb goes round the other way (behind, under, then over the index
// finger — the handle leaves the fist through the ring of thumb and palm); and the handle's
// cylinder is carved out of the finished field so the channel is exactly the handle's size.
// gripPlacement then turns and moves the hand so that channel lies on a world-space handle with
// the knuckles toward a direction.
//
// Two rules the pictures taught (data/tmp/hands/ has the sheets): nothing may merely KISS — a
// finger resting tangent on the palm, or a thumb tip lying on a finger, polygonises into specks
// and films, so a fist is solid inside (a block under each run of curled fingers) and contacts
// press in; and a thumb must never cross the handle's channel, or the carve slices it into a
// sliver.
//
// Plain numbers and arrays only: no three, no DOM, deterministic (no randomness at all). This file
// must NOT import ops.ts — ops.ts imports it.

type V3 = [number, number, number];

/** The pose names. 'grip' closes round a handle of `gripRadius` (a channel is carved for it);
 *  'fist' is a closed punching fist; 'open' is flat with the fingers spread; 'relaxed' is the
 *  natural cascade of a hand at rest; 'point' has the index finger straight and the rest curled. */
export type HandPose = "fist" | "grip" | "open" | "relaxed" | "point";
/** Where the thumb goes: 'wrap' over the front of the curled fingers (a fist), 'along' the handle
 *  (or alongside the index finger), 'up' extended (thumbs-up in a fist; spread in an open hand). */
export type ThumbPose = "wrap" | "along" | "up";
/** The label partAt() gives a point: which part of the hand is nearest. For vertex colours. */
export type HandPart = "palm" | "wrist" | "thumb" | "nail" | "index" | "middle" | "ring" | "little";

/** Options for handField. Only `side` is required; everything else has a stylised default. */
export interface HandOpts {
  /** The character's own side. L is exactly the mirror image of R across the frame's x = 0 plane. */
  side: "L" | "R";
  /** Palm width in metres (default 0.05). Everything else scales with it. */
  size?: number;
  /** 4 (default) or 3 — stylised hands often have three fingers and a thumb. */
  fingers?: 3 | 4;
  /** Default 'relaxed'. See HandPose. */
  pose?: HandPose;
  /** 'grip': radius of the handle the fingers close round, metres (default 0.13 x size). */
  gripRadius?: number;
  /** 0..1, how thick and toy-like: fatter fingers, thicker palm, shorter fingers (default 0.5). */
  chunk?: number;
  /** Middle-finger length / palm length (default 0.9 at chunk 0.5; 1.0 at chunk 0, 0.8 at 1). */
  fingerLength?: number;
  /** Knuckle bulge, 0..1 (default 0.5). */
  knuckles?: number;
  /** Nail plates on the finger and thumb tips (default true). */
  nails?: boolean;
  /** Per finger 0..1 (index first), overrides the pose's curl for that finger: 0 open, 1 fist. */
  curl?: number[];
  /** Default: 'wrap' for fist, grip and point; 'up' for open; a resting thumb for relaxed. */
  thumb?: ThumbPose;
  /** Wrist stub length past the wrist joint, as a fraction of `size` (default 0.55; 0 = none). */
  wrist?: number;
}

/**
 * THE HAND FRAME — the coordinates the field is built in, in metres, right-handed:
 *
 *   origin  the wrist joint: the centre of the wrist where the forearm meets the hand
 *   +Z      along the hand, wrist -> knuckles. The fingers point along +Z in 'open'.
 *   +Y      the BACK of the hand. The palm faces -Y.
 *   X       across the knuckle row. RIGHT hand: thumb and index finger on +X, little finger on -X.
 *           LEFT hand: the exact mirror image across x = 0, so its thumb is on -X.
 *   -Z      the forearm. The wrist stub runs from the origin along -Z and ends in a closed cap.
 *
 * So R is a true right hand: its thumb side is back x fingers (+Y x +Z = +X). Hung at a glTF
 * character's right side (the character faces +Z) with the palm to the thigh, the frame maps
 * X -> +Z, Y -> -X, Z -> -Y: thumb forward, as it should be.
 *
 * In 'grip' (and 'fist', 'point') the fingers curl toward -Y and back toward -Z round a channel
 * that runs parallel to X, under the front of the palm just behind the knuckle line. For the
 * default grip (size 0.05, gripRadius 0.0065) gripCentre is about [0, -0.0126, 0.0384] and
 * knuckles about [0, 0.81, 0.58]; in a fist the channel is filled (gripRadius 0) and gripCentre
 * is the middle of the curl, about [0, -0.0094, 0.0426]. Everything scales with `size`.
 */
export interface HandFrame {
  /** Point: the wrist joint, [0, 0, 0]. Feed the placed (world) one to reach() for the forearm. */
  wrist: V3;
  /** Point: on the grip channel's axis, in the middle of the finger span. gripPlacement puts
   *  THIS point on the handle. For 'open'/'relaxed': where a handle laid on the palm would sit. */
  gripCentre: V3;
  /** Unit direction along the channel, from the little finger toward the index finger — the
   *  thumb side. R: [1, 0, 0]; L: [-1, 0, 0]. A sword held the ordinary way leaves the fist along
   *  it: gripPlacement points it at the handle's `b` end. */
  gripAxis: V3;
  /** Unit direction, perpendicular to gripAxis, from the grip axis toward the knuckle row (the
   *  MCP heads): the way the knuckles of the fist face, between +Y (back) and +Z (fingers). */
  knuckles: V3;
  /** Unit direction the palm faces: [0, -1, 0]. */
  palm: V3;
  /** Unit direction the back of the hand faces: [0, 1, 0]. */
  back: V3;
  /** Unit direction the fingers point when open, wrist -> knuckles: [0, 0, 1]. */
  fingers: V3;
  /** Unit direction from the wrist toward the elbow: [0, 0, -1]. */
  forearm: V3;
  /** Point: the middle finger's knuckle (MCP head). */
  knuckleRow: V3;
  /** Point: the centre of the palm slab. */
  palmCentre: V3;
  /** Point: inside the thumb's tip (the centre of its last sphere). The thumb is where this says. */
  thumbTip: V3;
  /** Points: inside each finger's tip, index first. */
  tips: V3[];
  /** 'grip': the radius the channel was carved for. A closed fist ('fist', 'point', or every
   *  finger curled): 0. 'open'/'relaxed': the gripRadius option, the handle gripCentre assumes. */
  gripRadius: number;
}

/** The skeleton the field was built from, in the hand frame — for rigging, rings, colours. */
export interface HandJoints {
  /** Per finger (index first): [MCP, PIP, DIP, tip centre, tip end]. */
  fingers: V3[][];
  /** [CMC, MCP, IP, tip centre, tip end]. */
  thumb: V3[];
  /** Radii at those joints, same layout (the tip end repeats the tip's radius). */
  fingerRadii: number[][];
  thumbRadii: number[];
  /** Flexion of each finger's MCP, PIP, DIP joint, degrees (after the grip solve and clamps). */
  flexion: number[][];
}

/** What handField returns. */
export interface Hand {
  /** The signed distance in the hand frame, metres: < 0 inside. Allocation-free, deterministic. */
  field: (x: number, y: number, z: number) => number;
  /** A box that holds the whole surface with room for isosurface to close it. */
  min: V3;
  max: V3;
  frame: HandFrame;
  joints: HandJoints;
  /** A voxel size, metres, that keeps the creases between fingers crisp (about a quarter of a
   *  finger's radius). A coarser grid still closes, it just rounds the creases away. */
  cell: number;
  /** Per-axis isosurface counts for CUBIC cells with `n` cells along the longest side of the
   *  box (default: whatever `cell` asks for, capped at 128). Pass it as isosurface's `res`. */
  grid: (n?: number) => V3;
  /** Which part is nearest a point (for vertex colours: nails lighter, a darker palm). */
  partAt: (x: number, y: number, z: number) => HandPart;
  /** 0..1: how much nail plate is at a point — a soft mask for painting the nails (1 on a plate,
   *  fading to 0 across about a millimetre of skin at size 0.05), so the colour edge is smooth
   *  where partAt's label would step from vertex to vertex. Always 0 with `nails: false`. */
  nail: (x: number, y: number, z: number) => number;
  /** The options it was built with, resolved: side, pose, finger count, and where the thumb went
   *  ('rest' is the relaxed pose's own thumb). */
  side: "L" | "R";
  pose: HandPose;
  fingers: 3 | 4;
  thumb: ThumbPose | "rest";
}

/** What gripPlacement returns: everything in the same space as the handle (the parent's). */
export interface GripPlacementResult {
  /** Where the hand frame's origin (the wrist joint) goes. */
  position: V3;
  /** The hand frame's rotation, [x, y, z, w], w >= 0 — three's Quaternion order. */
  quaternion: [number, number, number, number];
  /** Rotation and position as a column-major 4x4 (three's Matrix4.fromArray / elements). */
  matrix: number[];
  /** The world wrist point: feed it to reach(shoulder, wrist, [upper, fore], pole). */
  wrist: V3;
  /** Unit direction from the wrist toward where the elbow should be for a straight wrist. */
  forearm: V3;
  /** The handle point the grip centre was put on. */
  gripPoint: V3;
  /** Unit handle direction the grip axis now lies along (a -> b). */
  axis: V3;
  /** Unit direction the knuckles (or `aim`) now face: `face` made perpendicular to the handle. */
  knuckles: V3;
  /** True when `face` was (nearly) parallel to the handle and a default twist was used. */
  fallback: boolean;
}

// ------------------------------------------------------------------ small vector helpers
const DEG = Math.PI / 180;
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: V3): V3 => { const l = len(a); return l > 1e-12 ? [a[0] / l, a[1] / l, a[2] / l] : [0, 0, 0]; };
const lerp3 = (a: V3, b: V3, t: number): V3 => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
/** v without its component along the unit vector `axis`. */
const perp = (v: V3, axis: V3): V3 => sub(v, mul(axis, dot(v, axis)));
/** Some unit vector perpendicular to v. */
function anyPerp(v: V3): V3 {
  const a: V3 = Math.abs(v[0]) < 0.6 ? [1, 0, 0] : Math.abs(v[1]) < 0.6 ? [0, 1, 0] : [0, 0, 1];
  return norm(perp(a, norm(v)));
}
const mirrorX = (p: V3): V3 => [-p[0], p[1], p[2]];

// ------------------------------------------------------------------ distance primitives
type SDF = (x: number, y: number, z: number) => number;

/** Polynomial smooth minimum: exactly min(a, b) once |a - b| >= k, a fillet of radius ~k below. */
function smin(a: number, b: number, k: number): number {
  const m = a < b ? a : b;
  if (k <= 0) return m;
  const h = k - Math.abs(a - b);
  return h > 0 ? m - (h * h) / (4 * k) : m;
}
function smax(a: number, b: number, k: number): number { return -smin(-a, -b, k); }

/** A round cone from a (radius ra) to b (radius rb). Distance to the segment minus the local
 *  radius: exact for a capsule, a hair conservative for the gentle tapers used here. */
function cone(a: V3, b: V3, ra: number, rb: number): SDF {
  const ax = a[0], ay = a[1], az = a[2];
  const bx = b[0] - ax, by = b[1] - ay, bz = b[2] - az;
  const l2 = bx * bx + by * by + bz * bz;
  const inv = l2 > 1e-18 ? 1 / l2 : 0;
  const dr = rb - ra;
  return (x, y, z) => {
    const px = x - ax, py = y - ay, pz = z - az;
    let h = (px * bx + py * by + pz * bz) * inv;
    h = h < 0 ? 0 : h > 1 ? 1 : h;
    const dx = px - bx * h, dy = py - by * h, dz = pz - bz * h;
    return Math.sqrt(dx * dx + dy * dy + dz * dz) - (ra + dr * h);
  };
}

function sphere(c: V3, r: number): SDF {
  const cx = c[0], cy = c[1], cz = c[2];
  return (x, y, z) => { const dx = x - cx, dy = y - cy, dz = z - cz; return Math.sqrt(dx * dx + dy * dy + dz * dz) - r; };
}

/** An ellipsoid with axes u, v, w (orthonormal) and radii r (Quilez's k0*(k0-1)/k1 distance). */
function ellipsoid(c: V3, u: V3, v: V3, w: V3, r: V3): SDF {
  const cx = c[0], cy = c[1], cz = c[2];
  const ux = u[0], uy = u[1], uz = u[2], vx = v[0], vy = v[1], vz = v[2], wx = w[0], wy = w[1], wz = w[2];
  const ia = 1 / r[0], ib = 1 / r[1], ic = 1 / r[2];
  const ia2 = ia * ia, ib2 = ib * ib, ic2 = ic * ic;
  const rmin = Math.min(r[0], r[1], r[2]);
  return (x, y, z) => {
    const px = x - cx, py = y - cy, pz = z - cz;
    const a = px * ux + py * uy + pz * uz, b = px * vx + py * vy + pz * vz, e = px * wx + py * wy + pz * wz;
    const k0 = Math.sqrt(a * a * ia2 + b * b * ib2 + e * e * ic2);
    const k1 = Math.sqrt(a * a * ia2 * ia2 + b * b * ib2 * ib2 + e * e * ic2 * ic2);
    return k1 > 1e-12 ? (k0 * (k0 - 1)) / k1 : -rmin;
  };
}

/** An elliptic cylinder from a to b (width rx along u, thickness ry along v) with ellipsoidal
 *  end caps of depth rc: the wrist stub. */
function ellCapsule(a: V3, b: V3, u: V3, rx: number, ry: number, rc: number): SDF {
  const w = norm(sub(b, a)), L = len(sub(b, a));
  const uu = norm(perp(u, w)), vv = cross(w, uu);
  const ax = a[0], ay = a[1], az = a[2];
  const ia2 = 1 / (rx * rx), ib2 = 1 / (ry * ry), ic2 = 1 / (rc * rc);
  const rmin = Math.min(rx, ry, rc);
  return (x, y, z) => {
    const px = x - ax, py = y - ay, pz = z - az;
    const t = px * w[0] + py * w[1] + pz * w[2];
    const o = t < 0 ? t : t > L ? t - L : 0;
    const qa = px * uu[0] + py * uu[1] + pz * uu[2], qb = px * vv[0] + py * vv[1] + pz * vv[2];
    const k0 = Math.sqrt(qa * qa * ia2 + qb * qb * ib2 + o * o * ic2);
    const k1 = Math.sqrt(qa * qa * ia2 * ia2 + qb * qb * ib2 * ib2 + o * o * ic2 * ic2);
    return k1 > 1e-12 ? (k0 * (k0 - 1)) / k1 : -rmin;
  };
}

/** The palm: a box from z0 to z1, half-width hw0 -> hw1 and half-thickness ht0 -> ht1 along z,
 *  every edge rounded by `round`, the back domed by `dome` (the middle of the back stands higher
 *  than its sides, as the metacarpal arch makes it). */
function palmSlab(z0: number, z1: number, hw0: number, hw1: number, ht0: number, ht1: number, round: number, dome: number): SDF {
  const cz = (z0 + z1) / 2, hz = (z1 - z0) / 2, span = z1 - z0;
  return (x, y, z) => {
    let t = (z - z0) / span;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const hw = hw0 + (hw1 - hw0) * t;
    let ht = ht0 + (ht1 - ht0) * t;
    if (y > 0) { const s = x / hw; ht += dome * (1 - (s * s > 1 ? 1 : s * s)); }
    const qx = Math.abs(x) - hw + round, qy = Math.abs(y) - ht + round, qz = Math.abs(z - cz) - hz + round;
    const ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0, oz = qz > 0 ? qz : 0;
    const m = qx > qy ? (qx > qz ? qx : qz) : (qy > qz ? qy : qz);
    return Math.sqrt(ox * ox + oy * oy + oz * oz) + (m < 0 ? m : 0) - round;
  };
}

/** A box from lo to hi with every edge rounded by `round`. */
function roundBox(lo: V3, hi: V3, round: number): SDF {
  const cx = (lo[0] + hi[0]) / 2, cy = (lo[1] + hi[1]) / 2, cz = (lo[2] + hi[2]) / 2;
  const hx = Math.max((hi[0] - lo[0]) / 2, round), hy = Math.max((hi[1] - lo[1]) / 2, round), hz = Math.max((hi[2] - lo[2]) / 2, round);
  return (x, y, z) => {
    const qx = Math.abs(x - cx) - hx + round, qy = Math.abs(y - cy) - hy + round, qz = Math.abs(z - cz) - hz + round;
    const ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0, oz = qz > 0 ? qz : 0;
    const m = qx > qy ? (qx > qz ? qx : qz) : (qy > qz ? qy : qz);
    return Math.sqrt(ox * ox + oy * oy + oz * oz) + (m < 0 ? m : 0) - round;
  };
}

// ------------------------------------------------------------------ groups
interface Prim { f: SDF; k: number; sub: boolean; nail: boolean }
interface Group { prims: Prim[]; k: number; c: V3; r: number; part: HandPart; lo: V3; hi: V3 }

class GroupBuilder {
  prims: Prim[] = [];
  lo: V3 = [Infinity, Infinity, Infinity];
  hi: V3 = [-Infinity, -Infinity, -Infinity];
  constructor(public part: HandPart, public k: number) {}
  private grow(c: V3, r: number) {
    for (let i = 0; i < 3; i++) { this.lo[i] = Math.min(this.lo[i], c[i] - r); this.hi[i] = Math.max(this.hi[i], c[i] + r); }
  }
  cone(a: V3, b: V3, ra: number, rb: number, k: number) {
    this.prims.push({ f: cone(a, b, ra, rb), k, sub: false, nail: false });
    this.grow(a, ra); this.grow(b, rb);
  }
  sphere(c: V3, r: number, k: number, subtract = false) {
    this.prims.push({ f: sphere(c, r), k, sub: subtract, nail: false });
    if (!subtract) this.grow(c, r);
  }
  ellipsoid(c: V3, u: V3, v: V3, w: V3, r: V3, k: number, nail = false) {
    this.prims.push({ f: ellipsoid(c, u, v, w, r), k, sub: false, nail });
    this.grow(c, Math.max(r[0], r[1], r[2]));
  }
  raw(f: SDF, lo: V3, hi: V3, k: number) {
    this.prims.push({ f, k, sub: false, nail: false });
    for (let i = 0; i < 3; i++) { this.lo[i] = Math.min(this.lo[i], lo[i]); this.hi[i] = Math.max(this.hi[i], hi[i]); }
  }
  done(): Group {
    const c: V3 = lerp3(this.lo, this.hi, 0.5);
    return { prims: this.prims, k: this.k, c, r: len(sub(this.hi, c)), part: this.part, lo: this.lo, hi: this.hi };
  }
}

function evalGroup(g: Group, x: number, y: number, z: number): number {
  const P = g.prims;
  let d = 1e9;
  for (let i = 0; i < P.length; i++) {
    const p = P[i];
    const v = p.f(x, y, z);
    d = p.sub ? smax(d, -v, p.k) : smin(d, v, p.k);
  }
  return d;
}

// ------------------------------------------------------------------ poses
// Degrees of flexion at the MCP, PIP and DIP joints. Positive bends toward the palm (-Y).
const OPEN_FLEX = [3, 5, 3];
const FIST_FLEX = [86, 92, 62];           // a fist; `curl` blends from OPEN_FLEX toward it
const RELAX_FLEX = [[14, 24, 12], [20, 32, 15], [26, 38, 17], [32, 44, 19]];
const MAX_FLEX = [98, 112, 88];
const SEG = [0.47, 0.3, 0.23];             // each phalanx's share of a finger's centreline
const SPLAY4 = [7, 1, -5, -12];
const SPLAY3 = [8, 0, -10];

interface FingerSpec { name: HandPart; len: number; rad: number; zBack: number; yArch: number; splay: number }
const FINGERS4: FingerSpec[] = [
  { name: "index", len: 0.92, rad: 1.0, zBack: 0.035, yArch: 0.0, splay: SPLAY4[0] },
  { name: "middle", len: 1.0, rad: 1.03, zBack: 0.0, yArch: 0.012, splay: SPLAY4[1] },
  { name: "ring", len: 0.95, rad: 0.98, zBack: 0.03, yArch: 0.004, splay: SPLAY4[2] },
  { name: "little", len: 0.78, rad: 0.88, zBack: 0.1, yArch: -0.02, splay: SPLAY4[3] },
];
const FINGERS3: FingerSpec[] = [
  { name: "index", len: 0.93, rad: 1.0, zBack: 0.03, yArch: 0.0, splay: SPLAY3[0] },
  { name: "middle", len: 1.0, rad: 1.03, zBack: 0.0, yArch: 0.01, splay: SPLAY3[1] },
  { name: "little", len: 0.86, rad: 0.95, zBack: 0.07, yArch: -0.012, splay: SPLAY3[2] },
];

interface FingerChain {
  spec: FingerSpec;
  J: V3[];        // MCP, PIP, DIP, tip centre
  r: number[];    // radii at those four
  d: V3[];        // unit direction of each phalanx
  b: V3[];        // unit "back" (dorsal) normal of each phalanx
  flex: number[]; // degrees
  curled: boolean;
}

/** Forward kinematics of one finger: flexion (degrees) at each joint from its MCP joint J0. */
function fingerChain(spec: FingerSpec, J0: V3, splayDeg: number, flexDeg: number[], l: number[], r: number[]): FingerChain {
  const sp = splayDeg * DEG;
  const d0: V3 = [Math.sin(sp), 0, Math.cos(sp)];
  const Y: V3 = [0, 1, 0];
  const J: V3[] = [J0];
  const d: V3[] = [], b: V3[] = [];
  let th = 0;
  for (let k = 0; k < 3; k++) {
    th += flexDeg[k] * DEG;
    const dk = add(mul(d0, Math.cos(th)), mul(Y, -Math.sin(th)));
    const bk = add(mul(d0, Math.sin(th)), mul(Y, Math.cos(th)));
    d.push(dk); b.push(bk);
    J.push(add(J[k], mul(dk, l[k])));
  }
  return { spec, J, r, d, b, flex: flexDeg.slice(), curled: flexDeg[0] + flexDeg[1] > 100 };
}

/**
 * The tangent length from the MCP joint that lets all three phalanges rest on the handle as near
 * their middles as a chain of tangents can: from each joint the two tangent lengths are equal,
 * so t2 = l1 - t1 and t3 = l2 - t2, and this t1 minimises the three offsets from the midpoints.
 */
function balancedT1(l: number[]): number { return Math.max(0.12 * l[0], (2.5 * l[0] - 1.5 * l[1] + 0.5 * l[2]) / 3); }

/**
 * Where the handle's centre goes, in the reference finger's (z, y) plane, so that finger wraps it
 * as a balanced chain of tangents (see balancedT1) with the centre at height `yTarget` (which
 * sets how far the handle presses into the palm). Two MCP angles reach that height; the larger —
 * the finger going down in front of the handle, as in a real power grip — is taken.
 */
function handleCentre(J0: V3, l: number[], R: number, yTarget: number): { zc: number; yc: number } {
  const t1 = balancedT1(l);
  const rho = Math.hypot(t1, R), phi = Math.atan2(R, t1);
  const q = (J0[1] - yTarget) / rho;
  let a1 = q >= 1 ? Math.PI / 2 - phi : Math.PI - Math.asin(Math.max(-1, q)) - phi;
  a1 = clamp(a1, 40 * DEG, MAX_FLEX[0] * DEG);
  return { zc: J0[2] + t1 * Math.cos(a1) - R * Math.sin(a1), yc: J0[1] - t1 * Math.sin(a1) - R * Math.cos(a1) };
}

/**
 * The grip solve for one finger, in its own (z, y) plane: each phalanx is laid along the tangent
 * from its joint to the circle of radius R round the handle centre C, the circle on the palm side
 * of the phalanx, so every phalanx rests on the handle and the joints stand off it — how real
 * fingers close on a bar. Each joint's flexion is clamped to what a finger can do.
 */
function wrapFlexion(J0: [number, number], C: [number, number], R: number, l: number[]): number[] {
  // Directions are angles th with d = (cos th, -sin th) in (z, y); the palm-side normal is
  // n = (-sin th, -cos th). For v = C - J = rho (cos psi, sin psi): v.n = -rho sin(th + psi).
  const flex: number[] = [];
  let jz = J0[0], jy = J0[1];
  let prev = 0;
  for (let k = 0; k < 3; k++) {
    const vz = C[0] - jz, vy = C[1] - jy;
    const rho = Math.hypot(vz, vy);
    const psi = Math.atan2(vy, vz);
    let th = rho > R ? -psi - Math.asin(R / rho) : -psi - Math.PI / 2;
    // The representative nearest the previous direction, going round the curl's way.
    while (th - prev > Math.PI) th -= 2 * Math.PI;
    while (th - prev < -Math.PI) th += 2 * Math.PI;
    const f = clamp((th - prev) / DEG, 0, MAX_FLEX[k]);
    flex.push(f);
    prev += f * DEG;
    jz += Math.cos(prev) * l[k];
    jy += -Math.sin(prev) * l[k];
  }
  return flex;
}

/** Two-bone reach: the middle joint between root and target with bone lengths l1, l2, bending
 *  toward `pole` (a direction). Out of reach: a straight chain pointing at the target. */
function twoBone(root: V3, target: V3, l1: number, l2: number, pole: V3): { mid: V3; end: V3 } {
  const dv = sub(target, root);
  let dist = len(dv);
  const dir: V3 = dist > 1e-12 ? mul(dv, 1 / dist) : [0, 0, 1];
  if (dist >= l1 + l2) return { mid: add(root, mul(dir, l1)), end: add(root, mul(dir, l1 + l2)) };
  dist = Math.max(dist, Math.abs(l1 - l2) + 1e-9);
  const a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist);
  const h = Math.sqrt(Math.max(l1 * l1 - a * a, 0));
  let pv = perp(pole, dir);
  pv = len(pv) > 1e-9 ? norm(pv) : anyPerp(dir);
  return { mid: add(add(root, mul(dir, a)), mul(pv, h)), end: add(root, mul(dir, dist)) };
}

/** Radial unit vector of angle a (radians) in the (z, y) plane: +Z at 0, +Y at 90 degrees. */
const radial = (a: number): V3 => [0, Math.sin(a), Math.cos(a)];

// ------------------------------------------------------------------ the right hand
interface Built {
  groups: Group[];
  carve: { yc: number; zc: number; r: number; k: number } | null;
  frame: HandFrame;
  joints: HandJoints;
  lo: V3; hi: V3;
  cell: number;
  pose: HandPose;
  thumb: ThumbPose | "rest";
  names: HandPart[];
}

function buildRight(o: HandOpts): Built {
  const W = o.size && o.size > 0 ? o.size : 0.05;
  const ch = clamp(o.chunk ?? 0.5, 0, 1);
  const nF: 3 | 4 = o.fingers === 3 ? 3 : 4;
  const pose: HandPose = o.pose ?? "relaxed";
  const kn = clamp(o.knuckles ?? 0.5, 0, 1);
  const nails = o.nails ?? true;
  const fl = o.fingerLength && o.fingerLength > 0 ? o.fingerLength : 0.9 + (0.5 - ch) * 0.2;
  const gUser = o.gripRadius && o.gripRadius > 0 ? o.gripRadius : 0.13 * W;
  const wristLen = Math.max(0, o.wrist ?? 0.55) * W;
  const X: V3 = [1, 0, 0], Y: V3 = [0, 1, 0], Z: V3 = [0, 0, 1];

  const Lp = W * (1.0 - 0.1 * ch);            // wrist joint -> knuckle line
  const T = W * (0.3 + 0.1 * ch);             // palm thickness at the knuckles
  const ht1 = T / 2, ht0 = T * 0.58;          // half-thickness at the knuckles, at the heel
  const palmFace = -ht1;                      // the palm's surface under the knuckles
  const specs = nF === 4 ? FINGERS4 : FINGERS3;
  const rf0 = W * (nF === 4 ? 0.115 + 0.05 * ch : 0.152 + 0.06 * ch);
  const yM = ht1 - rf0 * 1.3;                 // finger joint line: the knuckles stand above it

  // ---- finger bases, radii, phalanx lengths. Neighbours overlap by the same share of their
  //      radii (pressed together, a crease between), the row centred across the palm.
  const rads = specs.map((s) => rf0 * s.rad);
  const xs: number[] = [0];
  for (let i = 1; i < nF; i++) xs.push(xs[i - 1] - (rads[i - 1] + rads[i]) * 0.88);
  {
    const shift = ((xs[0] + rads[0]) + (xs[nF - 1] - rads[nF - 1])) / 2;
    for (let i = 0; i < nF; i++) xs[i] -= shift;
  }
  const J0s: V3[] = specs.map((s, i) => [xs[i], yM + s.yArch * W, Lp - s.zBack * W]);
  const lens = specs.map((s, i) => {
    const Lc = fl * Lp * s.len - rads[i] * 0.9;
    return SEG.map((f) => f * Lc);
  });

  // ---- the handle the curled fingers close round ('grip'): the middle finger wraps it as a
  //      balanced chain of tangents with the handle pressed 0.4 of its radius into the palm
  const g = pose === "grip" ? gUser : 0;
  const ref = 1;                              // the middle finger sets the handle
  let zc = 0.8 * Lp, yc = palmFace;
  if (pose === "grip") ({ zc, yc } = handleCentre(J0s[ref], lens[ref], g + rads[ref] * 0.78, palmFace - 0.6 * g));

  // ---- flexion per finger
  const curlOf = (i: number): number | null => (o.curl && typeof o.curl[i] === "number" ? clamp(o.curl[i], 0, 1) : null);
  const fingers: FingerChain[] = specs.map((spec, i) => {
    const c = curlOf(i);
    let flex: number[];
    let splay = spec.splay;
    if (c !== null) {
      flex = OPEN_FLEX.map((a, k) => lerp(a, FIST_FLEX[k], c));
      splay *= 1 - c;
    } else if (pose === "open") {
      flex = OPEN_FLEX.slice();
    } else if (pose === "relaxed") {
      flex = RELAX_FLEX[nF === 4 ? i : [0, 1, 3][i]].slice();
      splay *= 0.45;
    } else if (pose === "point" && i === 0) {
      flex = [2, 4, 2];
      splay = 2;
    } else if (pose === "grip") {
      const t1 = balancedT1(lens[i]);
      const rho = Math.hypot(J0s[i][2] - zc, J0s[i][1] - yc);
      const R = clamp(Math.sqrt(Math.max(rho * rho - t1 * t1, 0)), g + rads[i] * 0.5, g + rads[i] * 0.95);
      flex = wrapFlexion([J0s[i][2], J0s[i][1]], [zc, yc], R, lens[i]);
      splay = 0;
    } else {
      // a fist: the proximal phalanges straight down (the punching face), the middle ones
      // folded under the palm, the tips tucked into it; the little finger a little tighter
      flex = i === nF - 1 ? [FIST_FLEX[0] + 4, FIST_FLEX[1] + 3, FIST_FLEX[2]] : FIST_FLEX.slice();
      splay = 0;
    }
    const r = rads[i];
    return fingerChain(spec, J0s[i], splay, flex, lens[i], [r, r * 0.95, r * 0.91, r * 0.9]);
  });
  // Curled fingers (a fist, the three of 'point', or any `curl` near 1) fold under the palm.
  const curledIdx = fingers.map((f, i) => (f.curled ? i : -1)).filter((i) => i >= 0);
  const fistLike = pose !== "grip" && curledIdx.length > 0;
  // The grip centre is the handle's in 'grip'; the curl's when the hand is a fist; otherwise
  // where a handle laid across the palm would sit.
  const closed = pose === "grip" || (fistLike && (pose === "fist" || pose === "point" || curledIdx.length === nF));
  if (fistLike) {
    // The fist's curl centre: between the palm and the folded middle phalanges.
    const f = fingers[curledIdx.includes(ref) ? ref : curledIdx[0]];
    zc = (f.J[1][2] + f.J[2][2]) / 2 - 0.1 * f.r[1];
    yc = (palmFace + (f.J[1][1] + f.J[2][1]) / 2 + f.r[1]) / 2;
  }

  // ---- the thumb: CMC (inside the thenar) -> MCP -> IP -> tip
  const rt = rf0 * 1.2;
  const tl = fl / 0.9;
  const lM = 0.56 * W, lP1 = 0.38 * W * tl, lP2 = 0.27 * W * tl;
  const cmc: V3 = [0.34 * W, -0.1 * W, 0.26 * Lp];
  const tr = [rt * 1.3, rt * 1.05, rt * 0.97, rt * 0.93];
  const thumbMode: ThumbPose | "rest" = o.thumb ?? (pose === "open" ? "up" : pose === "relaxed" ? "rest" : "wrap");
  let tIP: V3, tTip: V3, tBack: V3;
  let tMcp: V3 | null = null;
  const anyCurled = curledIdx.length > 0;
  if (thumbMode === "wrap" && pose === "grip") {
    // Round the handle the other way from the fingers: from the thenar behind it, under it
    // beside the index finger, and over the index finger's middle phalanx at the front. The
    // handle leaves the fist through the ring of thumb and palm, as in a real hammer grip.
    // (over the first two fingers that close on the handle: a straight trigger finger is skipped)
    const ia = curledIdx.length ? curledIdx[0] : 0, ib = curledIdx.length > 1 ? curledIdx[1] : Math.min(ia + 1, nF - 1);
    const fa = fingers[ia], fb = fingers[ib];
    const C: V3 = [0, yc, zc];
    const xOut = xs[ia] + fa.r[0];
    tMcp = add(add(C, mul(radial(208 * DEG), g + tr[1] * 0.92)), [xOut + rt * 0.3, 0, 0]);
    tIP = add(add(C, mul(radial(282 * DEG), g + fa.r[1] * 0.7 + tr[2] * 0.85)), [xOut - fa.r[0] * 0.15, 0, 0]);
    const target: V3 = add(lerp3(fb.J[1], fb.J[2], 0.3), mul(fb.b[1], fb.r[1] + rt * 0.5));
    target[0] = lerp(fa.J[1][0], fb.J[1][0], 0.55);
    tTip = add(tIP, mul(norm(sub(target, tIP)), lP2));
    tBack = norm(perp(sub(tTip, C), X));
  } else if (thumbMode === "wrap" && anyCurled) {
    // Across the front of the curled fingers at their middle knuckles: the IP joint beside the
    // first curled finger, the tip over it toward the next — the fist in the reference picture.
    const fa = fingers[curledIdx[0]], fb = fingers[curledIdx[Math.min(1, curledIdx.length - 1)]];
    const outA = norm(add(fa.b[0], fa.b[1])), outB = norm(add(fb.b[0], fb.b[1]));
    tIP = add(add(fa.J[1], mul(outA, fa.r[1] * 0.55 + rt * 0.55)), mul(X, fa.r[1] * 0.95));
    const target: V3 = add(lerp3(fb.J[1], fb.J[2], 0.12), mul(outB, fb.r[1] + rt * 0.62));
    target[0] = fb === fa ? fa.J[1][0] - fa.r[1] * 0.4 : lerp(fa.J[1][0], fb.J[1][0], 0.7);
    tTip = add(tIP, mul(norm(sub(target, tIP)), lP2));
    tBack = norm(add(outA, outB));
  } else if (thumbMode === "wrap") {
    // No curled fingers: folded across the palm.
    tIP = [0.22 * W, palmFace - rt * 0.55, 0.62 * Lp];
    tTip = add(tIP, mul(norm([-0.9, -0.05, 0.35]), lP2));
    tBack = [0, -1, 0];
  } else if (thumbMode === "along" && pose === "grip") {
    // Along the handle, on its back beside the index finger, pointing out of the thumb side.
    const ang = 150 * DEG, rr = g + rt * 0.82;
    const base = add([0, yc, zc], mul(radial(ang), rr));
    tIP = add(base, [xs[0] + rads[0] * 0.4 + lP1 * 0.55, 0, 0]);
    tTip = add(tIP, [lP2, 0, 0]);
    tBack = radial(ang);
  } else if (thumbMode === "along" && anyCurled) {
    // A fist with the thumb laid down the side of the index finger.
    const fa = fingers[0];
    tIP = add(lerp3(fa.J[0], fa.J[1], 0.35), mul(X, fa.r[0] + rt * 0.7));
    tTip = add(tIP, mul(norm(add(fa.d[0], mul(X, -0.15))), lP2));
    tBack = norm(add(X, mul(fa.b[0], 0.6)));
  } else if (thumbMode === "along") {
    // Open hand, thumb laid alongside the index finger.
    tIP = add(cmc, [0.18 * W, -0.05 * W, lM + lP1 * 0.85]);
    tTip = add(tIP, mul(norm([0.12, 0.02, 1]), lP2));
    tBack = norm([0.75, 0.66, 0]);
  } else if (thumbMode === "up" && anyCurled) {
    // Thumbs-up: the metacarpal along the side of the palm, the thumb straight out of the side
    // of the fist along +X (the grip axis), just above the curled index finger.
    tMcp = [xs[0] + rads[0] * 0.75, yM - 0.05 * W, 0.6 * Lp];
    tIP = add(tMcp, mul(norm([1, 0.16, 0.05]), lP1));
    tTip = add(tIP, mul(norm([1, 0.1, -0.1]), lP2));
    tBack = norm([0, 0.35, -0.94]);
  } else if (thumbMode === "up") {
    // Spread out to the side, a little below the palm plane.
    const dM = norm([0.55, -0.3, 0.78]);
    tMcp = add(cmc, mul(dM, lM));
    tIP = add(tMcp, mul(norm([0.75, -0.08, 0.66]), lP1));
    tTip = add(tIP, mul(norm([0.6, 0.06, 0.8]), lP2));
    tBack = norm([0.62, 0.78, 0]);
  } else {
    // At rest: slightly flexed, the tip near the side of the index finger.
    const dM = norm([0.42, -0.42, 0.8]);
    tMcp = add(cmc, mul(dM, lM));
    tIP = add(tMcp, mul(norm([0.4, -0.3, 0.87]), lP1));
    tTip = add(tIP, mul(norm([0.18, -0.34, 0.92]), lP2));
    tBack = norm([0.8, 0.5, 0]);
  }
  if (!tMcp) {
    // The metacarpal gives a little (0.9x..1.25x; it lives inside the thenar mass) so the chain
    // always reaches with a gentle bend at the thumb's knuckle, bulging out and toward the palm.
    const need = len(sub(tIP, cmc));
    const lMe = clamp(need - lP1 * 0.8, lM * 0.9, lM * 1.25);
    tMcp = twoBone(cmc, tIP, lMe, lP1, [0.8, -0.6, 0]).mid;
  }
  const tDir = norm(sub(tTip, tIP));
  tBack = norm(perp(tBack, tDir));
  if (len(tBack) < 0.5) tBack = anyPerp(tDir);

  // ---- groups
  const groups: Group[] = [];
  const knob = 0.62 + 0.32 * kn;

  // palm: slab, knuckle heads, the pad under the finger bases, hypothenar (+ the grip's heel pad)
  const palm = new GroupBuilder("palm", 0);
  const pz0 = 0.06 * Lp, pz1 = Lp - 0.03 * W;
  const hw0 = 0.36 * W, hw1 = 0.5 * W, dome = 0.06 * W;
  palm.raw(palmSlab(pz0, pz1, hw0, hw1, ht0, ht1, Math.min(ht1, hw0) * 0.85, dome),
    [-hw1, -ht0, pz0], [hw1, ht0 + dome, pz1], 0);
  for (let i = 0; i < nF; i++) {
    const f = fingers[i];
    const out = norm(add(Y, f.b[0]));
    palm.sphere(add(add(f.J[0], mul(out, f.r[0] * 0.32)), mul(Z, -f.r[0] * 0.12)), f.r[0] * knob, f.r[0] * 0.45);
  }
  palm.ellipsoid([0, yM - rf0 * 0.5, Lp - 0.1 * W], X, Y, Z, [0.44 * W, 0.1 * W, 0.12 * W], 0.07 * W);
  palm.ellipsoid([-0.26 * W, -ht0 * 0.55, 0.42 * Lp], X, Y, Z, [0.17 * W, 0.12 * W, 0.3 * Lp], 0.07 * W);
  if (pose === "grip" || fistLike) {
    if (pose === "grip") {
      const cx = curledIdx.length ? curledIdx.map((i) => xs[i]) : xs;
      const xa = Math.max(...cx), xb = Math.min(...cx);
      // The heel of the hand closes round the back of the handle, reaching the finger tips so
      // they sink into it instead of kissing it; if the handle does not reach the palm, a
      // cushion fills between (the carve shapes both to the handle).
      const rh = 0.62 * g + 0.07 * W;
      const hc = add([0, yc, zc], mul(radial(205 * DEG), g + 0.2 * rh));
      palm.cone([xa + 0.02 * W, hc[1], hc[2]], [xb - 0.02 * W, hc[1], hc[2]], rh, rh, 0.08 * W);
      const gap = palmFace - (yc + g);
      if (gap > -0.25 * g) {
        palm.ellipsoid([0, (palmFace + yc + g) / 2, zc], X, Y, Z, [0.44 * W, Math.max(gap, 0) / 2 + 0.3 * g, 0.9 * g], 0.06 * W);
      }
    } else {
      // A fist is solid inside: under each run of neighbouring curled fingers, a block from the
      // palm down to their folded middle phalanges, tips to middle knuckles, so no finger merely
      // kisses the palm or its neighbour under it — a tangent contact polygonises into specks
      // and films. One block per run, so a straight finger between two curled ones stays free.
      const runs: number[][] = [];
      for (const i of curledIdx) {
        const last = runs[runs.length - 1];
        if (last && last[last.length - 1] === i - 1) last.push(i); else runs.push([i]);
      }
      for (const run of runs) {
        const cf = run.map((i) => fingers[i]);
        const yLo = Math.min(...cf.map((f) => Math.min(f.J[1][1], f.J[2][1]))) + 0.25 * rf0;
        const zLo = Math.min(...cf.map((f) => f.J[3][2])), zHi = Math.max(...cf.map((f) => f.J[1][2])) - 0.45 * rf0;
        const lo: V3 = [Math.min(...run.map((i) => xs[i])) - 0.2 * rf0, yLo, zLo];
        const hi: V3 = [Math.max(...run.map((i) => xs[i])) + 0.2 * rf0, palmFace + 0.5 * ht1, zHi];
        palm.raw(roundBox(lo, hi, 0.45 * rf0), lo, hi, 0.3 * rf0);
      }
    }
  }
  groups.push(palm.done());

  // wrist stub, closed by a rounded cap
  if (wristLen > 0) {
    const wr = new GroupBuilder("wrist", 0.2 * W);
    const rc = Math.min(0.18 * W, wristLen * 0.8);
    const a: V3 = [0, -0.01 * W, 0.3 * Lp], b: V3 = [0, -0.02 * W, -(wristLen - rc)];
    wr.raw(ellCapsule(a, b, X, 0.34 * W, 0.24 * W, rc), [-0.34 * W, -0.26 * W, -wristLen], [0.34 * W, 0.23 * W, 0.3 * Lp + rc], 0);
    groups.push(wr.done());
  }

  // fingers
  for (let i = 0; i < nF; i++) {
    const f = fingers[i];
    const G = new GroupBuilder(f.spec.name, rf0 * 0.34);
    const [r0, r1, r2, r3] = f.r;
    G.cone(f.J[0], f.J[1], r0, r1, 0);
    G.cone(f.J[1], f.J[2], r1, r2, 0);
    G.cone(f.J[2], f.J[3], r2, r3, 0);
    for (let k = 1; k <= 2; k++) {
      // the knuckle on the outside of the joint, the flesh bunched on its inside
      const out = norm(add(f.b[k - 1], f.b[k]));
      G.sphere(add(f.J[k], mul(out, f.r[k] * 0.3)), f.r[k] * (knob - 0.04 * k), f.r[k] * 0.3);
      const bend = Math.min(1, f.flex[k] / 90);
      if (bend > 0.25) G.sphere(add(f.J[k], mul(out, -f.r[k] * (0.2 + 0.25 * bend))), f.r[k] * (0.72 + 0.12 * bend), f.r[k] * 0.4);
    }
    if (nails) {
      const w = f.d[2], v = f.b[2], u = norm(cross(v, w));
      const c = add(add(f.J[2], mul(w, lens[i][2] * 0.6 + r3 * 0.1)), mul(v, r3 * 0.84));
      G.ellipsoid(c, u, v, w, [r3 * 0.7, r3 * 0.2, (lens[i][2] + r3) * 0.34], r3 * 0.1, true);
    }
    groups.push(G.done());
  }

  // thumb: the base (metacarpal, thenar, proximal phalanx) blends broadly into the palm; the
  // distal phalanx and nail blend tightly, so the thumb reads as a thumb where it lies on a finger
  {
    const base = new GroupBuilder("thumb", rt * 0.45);
    base.cone(cmc, tMcp, tr[0], tr[1], 0);
    base.cone(tMcp, tIP, tr[1], tr[2], 0);
    const ax = norm(sub(tMcp, cmc));
    const tu = norm(perp(X, ax)), tv = cross(ax, tu);
    // the thenar mass round the metacarpal, leaning into the palm
    const thC = add(lerp3(cmc, tMcp, 0.4), [-0.08 * W, -0.02 * W, -0.01 * W]);
    base.ellipsoid(thC, tu, tv, ax, [0.23 * W, 0.19 * W, len(sub(tMcp, cmc)) * 0.5 + 0.06 * W], 0.08 * W);
    // the web between thumb and index finger
    // (with a handle, the web runs to the palm just behind the handle's top instead, so it never
    //  crosses the channel)
    const ix = fingers[0];
    const webEnd: V3 = pose === "grip"
      ? add(add([0, yc, zc], mul(radial(135 * DEG), g + rt * 0.5)), [xs[0], 0, 0])
      : lerp3(ix.J[0], ix.J[1], 0.12);
    base.cone(tMcp, webEnd, tr[1] * 0.72, ix.r[0] * 0.62, rt * 0.4);
    const outM = norm(perp(tBack, norm(sub(tIP, tMcp))));
    base.sphere(add(tMcp, mul(outM, tr[1] * 0.28)), tr[1] * knob, tr[1] * 0.35);
    groups.push(base.done());

    const tip = new GroupBuilder("thumb", rt * 0.3);
    tip.cone(tIP, tTip, tr[2], tr[3], 0);
    tip.sphere(add(tIP, mul(norm(add(tBack, outM)), tr[2] * 0.3)), tr[2] * (knob - 0.04), tr[2] * 0.3);
    if (nails) {
      const w = tDir, v = tBack, u = norm(cross(v, w));
      const c = add(add(tIP, mul(w, lP2 * 0.6 + tr[3] * 0.1)), mul(v, tr[3] * 0.84));
      tip.ellipsoid(c, u, v, w, [tr[3] * 0.72, tr[3] * 0.2, (lP2 + tr[3]) * 0.34], tr[3] * 0.1, true);
    }
    groups.push(tip.done());
  }

  // ---- bounds: every group's own box, plus room for the blends and for the grid to close
  const margin = 0.07 * W;
  const lo: V3 = [Infinity, Infinity, Infinity], hi: V3 = [-Infinity, -Infinity, -Infinity];
  for (const G of groups) for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], G.lo[i] - margin); hi[i] = Math.max(hi[i], G.hi[i] + margin); }

  // ---- frame and joints
  const gc: V3 = closed ? [0, yc, zc] : [0, palmFace - gUser, 0.62 * Lp];
  const mf = fingers[Math.min(ref, nF - 1)];
  const kr = add(mf.J[0], mul(norm(add(Y, mf.b[0])), mf.r[0] * 0.32));
  const kd = norm(perp(sub(kr, gc), X));
  const frame: HandFrame = {
    wrist: [0, 0, 0], gripCentre: gc, gripAxis: [1, 0, 0], knuckles: len(kd) > 0.5 ? kd : [0, 0.7071, 0.7071], palm: [0, -1, 0],
    back: [0, 1, 0], fingers: [0, 0, 1], forearm: [0, 0, -1],
    knuckleRow: kr, palmCentre: [0, 0, (pz0 + pz1) / 2], thumbTip: tTip,
    tips: fingers.map((f) => f.J[3]), gripRadius: pose === "grip" ? g : closed ? 0 : gUser,
  };
  const joints: HandJoints = {
    fingers: fingers.map((f) => [f.J[0], f.J[1], f.J[2], f.J[3], add(f.J[3], mul(f.d[2], f.r[3]))]),
    thumb: [cmc, tMcp, tIP, tTip, add(tTip, mul(tDir, tr[3]))],
    fingerRadii: fingers.map((f) => [f.r[0], f.r[1], f.r[2], f.r[3], f.r[3]]),
    thumbRadii: [tr[0], tr[1], tr[2], tr[3], tr[3]],
    flexion: fingers.map((f) => f.flex.map((a) => Math.round(a * 10) / 10)),
  };
  const carve = pose === "grip" ? { yc, zc, r: g * 0.985, k: rf0 * 0.22 } : null;
  return { groups, carve, frame, joints, lo, hi, cell: rf0 * 0.26, pose, thumb: thumbMode, names: specs.map((s) => s.name) };
}

function compileField(B: Built): SDF {
  const groups = B.groups;
  const n = groups.length;
  const gx = new Float64Array(n), gy = new Float64Array(n), gz = new Float64Array(n), gr = new Float64Array(n), gk = new Float64Array(n);
  for (let i = 0; i < n; i++) { gx[i] = groups[i].c[0]; gy[i] = groups[i].c[1]; gz[i] = groups[i].c[2]; gr[i] = groups[i].r; gk[i] = groups[i].k; }
  const cv = B.carve;
  const cyc = cv ? cv.yc : 0, czc = cv ? cv.zc : 0, cr = cv ? cv.r : 0, ck = cv ? cv.k : 0;
  return (x, y, z) => {
    let D = 1e9;
    for (let i = 0; i < n; i++) {
      if (i > 0) {
        const dx = x - gx[i], dy = y - gy[i], dz = z - gz[i];
        const lower = Math.sqrt(dx * dx + dy * dy + dz * dz) - gr[i];
        if (lower > D + gk[i]) continue;
      }
      D = smin(D, evalGroup(groups[i], x, y, z), gk[i]);
    }
    if (cv) {
      const dy = y - cyc, dz = z - czc;
      D = smax(D, cr - Math.sqrt(dy * dy + dz * dz), ck);
    }
    return D;
  };
}

function compilePartAt(B: Built): (x: number, y: number, z: number) => HandPart {
  return (x, y, z) => {
    let best = Infinity;
    let part: HandPart = "palm";
    for (const G of B.groups) {
      const d = evalGroup(G, x, y, z);
      if (d < best) { best = d; part = G.part; }
    }
    // A nail wins inside its own plate (it is a thin layer over the finger it sits on).
    for (const G of B.groups) {
      for (const p of G.prims) if (p.nail && p.f(x, y, z) < best + 1e-4) return "nail";
    }
    return part;
  };
}

function compileNail(B: Built): (x: number, y: number, z: number) => number {
  const nails: Prim[] = [];
  for (const G of B.groups) for (const p of G.prims) if (p.nail) nails.push(p);
  return (x, y, z) => {
    let w = 0;
    for (let i = 0; i < nails.length; i++) {
      const p = nails[i];
      let t = 1 - p.f(x, y, z) / (p.k * 1.6);
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const s = t * t * (3 - 2 * t);
      if (s > w) w = s;
    }
    return w;
  };
}

// ------------------------------------------------------------------ public
/**
 * The whole hand as ONE signed-distance field (< 0 inside) in the HAND FRAME (see HandFrame):
 * wrist joint at the origin, fingers along +Z when open, back of the hand +Y, palm -Y; the RIGHT
 * hand's thumb on +X, the LEFT hand the exact mirror image across x = 0. Palm, three-segment
 * fingers with knuckles, thumb, nails and a capped wrist stub are joined by smooth minimum, so
 * `ops.isosurface(field, min, max, hand.grid(64))` gives one closed, seamless surface.
 *
 * Poses: 'grip' closes the fingers round a channel of `gripRadius` along X at frame.gripCentre
 * (solved: every phalanx rests on the handle; the handle's cylinder is carved out so the channel
 * is exactly its size); 'fist' is closed; 'open', 'relaxed' and 'point' are what they say; `curl`
 * overrides single fingers. `thumb` picks 'wrap' (over the front of the fingers), 'along' or
 * 'up'. Put the result on a handle with gripPlacement.
 *
 * @example const h = handField({ side: "R", pose: "grip", gripRadius: 0.012, size: 0.06 });
 *          const geo = ops.isosurface(h.field, h.min, h.max, h.grid(56));   // hand frame
 *          const at = gripPlacement({ a: pommel, b: guard, face: [0, 0, 1], hand: h });
 *          mesh.position.fromArray(at.position); mesh.quaternion.fromArray(at.quaternion);
 */
export function handField(o: HandOpts): Hand {
  const side: "L" | "R" = o.side === "L" ? "L" : "R";
  const B = buildRight(o);
  const fR = compileField(B);
  const partR = compilePartAt(B);
  const nailR = compileNail(B);
  const box = sub(B.hi, B.lo);
  const longest = Math.max(box[0], box[1], box[2]);
  const grid = (n?: number): V3 => {
    const N = n && n > 0 ? n : Math.min(128, Math.ceil(longest / B.cell));
    return [Math.max(8, Math.round((N * box[0]) / longest)), Math.max(8, Math.round((N * box[1]) / longest)), Math.max(8, Math.round((N * box[2]) / longest))];
  };
  const common = { joints: B.joints, cell: B.cell, grid, side, pose: B.pose, fingers: (B.names.length === 3 ? 3 : 4) as 3 | 4, thumb: B.thumb };
  if (side === "R") return { field: fR, min: B.lo, max: B.hi, frame: B.frame, partAt: partR, nail: nailR, ...common };
  const F = B.frame;
  const frame: HandFrame = {
    wrist: mirrorX(F.wrist), gripCentre: mirrorX(F.gripCentre), gripAxis: mirrorX(F.gripAxis), knuckles: mirrorX(F.knuckles),
    palm: mirrorX(F.palm), back: mirrorX(F.back), fingers: mirrorX(F.fingers), forearm: mirrorX(F.forearm),
    knuckleRow: mirrorX(F.knuckleRow), palmCentre: mirrorX(F.palmCentre), thumbTip: mirrorX(F.thumbTip),
    tips: F.tips.map(mirrorX), gripRadius: F.gripRadius,
  };
  const J = B.joints;
  const joints: HandJoints = { ...J, fingers: J.fingers.map((c) => c.map(mirrorX)), thumb: J.thumb.map(mirrorX) };
  return {
    field: (x, y, z) => fR(-x, y, z),
    partAt: (x, y, z) => partR(-x, y, z),
    nail: (x, y, z) => nailR(-x, y, z),
    min: [-B.hi[0], B.lo[1], B.lo[2]], max: [-B.lo[0], B.hi[1], B.hi[2]],
    frame, ...common, joints,
  };
}

/** Rotation (row-major 3x3) -> unit quaternion [x, y, z, w] with w >= 0 (three's convention). */
function quatOf(m: number[]): [number, number, number, number] {
  const m00 = m[0], m01 = m[1], m02 = m[2], m10 = m[3], m11 = m[4], m12 = m[5], m20 = m[6], m21 = m[7], m22 = m[8];
  const tr = m00 + m11 + m22;
  let x: number, y: number, z: number, w: number;
  if (tr > 0) {
    const s = 0.5 / Math.sqrt(tr + 1);
    w = 0.25 / s; x = (m21 - m12) * s; y = (m02 - m20) * s; z = (m10 - m01) * s;
  } else if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    w = (m21 - m12) / s; x = 0.25 * s; y = (m01 + m10) / s; z = (m02 + m20) / s;
  } else if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    w = (m02 - m20) / s; x = (m01 + m10) / s; y = 0.25 * s; z = (m12 + m21) / s;
  } else {
    const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
    w = (m10 - m01) / s; x = (m02 + m20) / s; y = (m12 + m21) / s; z = 0.25 * s;
  }
  const l = Math.hypot(x, y, z, w) || 1;
  const sg = w < 0 ? -1 / l : 1 / l;
  return [x * sg, y * sg, z * sg, w * sg];
}

/**
 * Where a hand goes so its grip channel lies ON a handle from `a` to `b`, with its grip centre at
 * `t` along it (default 0.5), and its knuckles turned toward `face`. The hand's gripAxis (little
 * finger -> thumb side) is laid along a -> b, so for a sword give a = the pommel end, b = the
 * guard end: the blade then leaves the fist on the thumb side, the ordinary grip (swap them for
 * an ice-pick grip). Works for both sides and any handle direction.
 *
 * `face` is made perpendicular to the handle. When it is (nearly) parallel to the handle the
 * twist is undetermined; the fallback turns the knuckles toward +Z (the way a glTF character
 * faces), or +Y when the handle itself runs along Z, and says `fallback: true`.
 *
 * `aim` (hand frame, default frame.knuckles) picks which way of the hand is turned toward
 * `face`: frame.back to show the back of the hand, frame.forearm with face = elbow - handle point
 * to line the wrist up with the arm (a straight wrist).
 *
 * Everything in and out is in ONE space (the parent's): position + quaternion (or matrix) move a
 * mesh built in the hand frame onto the handle; `wrist` is where the forearm must end.
 */
export function gripPlacement(o: { a: V3; b: V3; t?: number; face: V3; hand: Hand; aim?: V3 }): GripPlacementResult {
  const t = o.t ?? 0.5;
  const P = lerp3(o.a, o.b, t);
  const hv = sub(o.b, o.a);
  const H: V3 = len(hv) > 1e-12 ? norm(hv) : [1, 0, 0];
  const g = norm(o.hand.frame.gripAxis);
  let k = perp(o.aim ?? o.hand.frame.knuckles, g);
  k = len(k) > 1e-9 ? norm(k) : anyPerp(g);
  let F = perp(o.face, H);
  let fallback = false;
  if (len(F) <= 1e-3 * Math.max(len(o.face), 1e-12) || len(o.face) < 1e-12) {
    fallback = true;
    F = perp([0, 0, 1], H);
    if (len(F) < 0.2) F = perp([0, 1, 0], H);
  }
  const K = norm(F);
  const e3 = cross(g, k), E3 = cross(H, K);
  // R maps the hand basis (g, k, e3) onto the world basis (H, K, E3): R = H g' + K k' + E3 e3'.
  const R: number[] = [];
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) R.push(H[r] * g[c] + K[r] * k[c] + E3[r] * e3[c]);
  const rot = (v: V3): V3 => [R[0] * v[0] + R[1] * v[1] + R[2] * v[2], R[3] * v[0] + R[4] * v[1] + R[5] * v[2], R[6] * v[0] + R[7] * v[1] + R[8] * v[2]];
  const position = sub(P, rot(o.hand.frame.gripCentre));
  const wrist = add(rot(o.hand.frame.wrist), position);
  const matrix = [R[0], R[3], R[6], 0, R[1], R[4], R[7], 0, R[2], R[5], R[8], 0, position[0], position[1], position[2], 1];
  return {
    position, quaternion: quatOf(R), matrix, wrist,
    forearm: norm(rot(o.hand.frame.forearm)), gripPoint: P, axis: H, knuckles: K, fallback,
  };
}
