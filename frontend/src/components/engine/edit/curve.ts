// Curves: Blender's curve object with a bevel, as a placed thing a game can rebuild.
//
// A curve is a few control points and how to dress them: a radius, a profile (round, square, a
// flat strap), how smooth the path between the points is, a taper from one end to the other,
// open or closed. The mesh is never saved, only that recipe, so the game's runtime builds the
// same tube the editor showed, and moving a point is one small edit, not a new mesh.
//
// The sweep itself is sweep.ts: Catmull-Rom through the points and frames that never flip.
//
// Plain numbers and arrays, deterministic, no three.

import { sweepArrays } from "./sweep";

type V3 = [number, number, number];

export const CURVE_PROFILES = ["round", "square", "flat"] as const;
export type CurveProfile = (typeof CURVE_PROFILES)[number];

export interface CurveSpec {
  /** Control points, in the curve object's own space. Two at least, three for a closed curve. */
  points: V3[];
  /** Thickness: the radius of a round profile, half the width of the others (default 0.05). */
  radius?: number;
  profile?: CurveProfile;
  /** Sides of a round profile (default 12). */
  sides?: number;
  /** Path points between two control points (default 8; 0 keeps the corners). */
  smooth?: number;
  /** The radius at the start and at the end, as a share of `radius` (default [1, 1]). */
  taper?: [number, number];
  /** A loop: the last point joins the first. */
  closed?: boolean;
  /** Flat ends on an open curve (default true). */
  caps?: boolean;
}

/** Outlines in the frame's (normal, binormal) plane, scaled by the radius. */
const OUTLINES: Record<string, Array<[number, number]>> = {
  square: [[-1, -1], [1, -1], [1, 1], [-1, 1]],
  flat: [[-1, -0.2], [1, -0.2], [1, 0.2], [-1, 0.2]],
};

/** A new curve: an S across a metre and a half, off the ground. */
export function defaultCurve(): CurveSpec {
  return { points: [[-0.75, 0.5, 0], [-0.25, 0.85, 0.3], [0.25, 0.15, -0.3], [0.75, 0.5, 0]], radius: 0.05, profile: "round", sides: 12, smooth: 8 };
}

const num = (v: any, lo: number, hi: number, d: number) => (typeof v === "number" && isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d);

/** A curve as it comes from a file: bad points dropped, numbers held to sane ranges. Null when
 *  too few points are left to make a curve. */
export function cleanCurve(raw: any): CurveSpec | null {
  if (!raw || typeof raw !== "object" || !Array.isArray(raw.points)) return null;
  const points = raw.points
    .filter((p: any) => Array.isArray(p) && p.length >= 3 && p.slice(0, 3).every((x: any) => typeof x === "number" && isFinite(x)))
    .map((p: any) => [p[0], p[1], p[2]] as V3);
  const closed = raw.closed === true;
  if (points.length < (closed ? 3 : 2)) return null;
  const c: CurveSpec = { points, radius: num(raw.radius, 1e-4, 100, 0.05) };
  c.profile = CURVE_PROFILES.includes(raw.profile) ? raw.profile : "round";
  c.sides = Math.round(num(raw.sides, 3, 64, 12));
  c.smooth = Math.round(num(raw.smooth, 0, 32, 8));
  if (Array.isArray(raw.taper) && raw.taper.length >= 2) c.taper = [num(raw.taper[0], 0, 10, 1), num(raw.taper[1], 0, 10, 1)];
  if (closed) c.closed = true;
  if (raw.caps === false) c.caps = false;
  return c;
}

/** The tube: positions, normals, UVs and triangles, ready for a mesh in either engine. */
export function curveArrays(spec: CurveSpec): { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal: Float32Array } {
  const c = cleanCurve(spec);
  if (!c) throw new Error("a curve needs at least two points (three when closed)");
  const r = c.radius!;
  const [a, b] = c.taper || [1, 1];
  const prof = c.profile !== "round" ? OUTLINES[c.profile!] : undefined;
  return sweepArrays(c.points, {
    // With a profile the radius scales the outline; a taper of 0 would fold the tube to a line.
    radius: a === 1 && b === 1 ? r : (t: number) => r * Math.max(1e-3, a + (b - a) * t),
    sides: c.sides,
    profile: prof,
    closed: c.closed,
    caps: c.caps !== false,
    smooth: c.smooth,
  });
}

/** Where to put a new point: past the end, one step on in the direction the curve was going. */
export function extendCurve(c: CurveSpec, after = c.points.length - 1): V3[] {
  const p = c.points;
  const i = Math.max(0, Math.min(p.length - 1, after));
  const q = p[i];
  const prev = p[i > 0 ? i - 1 : Math.min(1, p.length - 1)];
  if (i < p.length - 1) {
    // Between two points: half way.
    const n = p[i + 1];
    return [...p.slice(0, i + 1), [(q[0] + n[0]) / 2, (q[1] + n[1]) / 2, (q[2] + n[2]) / 2] as V3, ...p.slice(i + 1)];
  }
  const d: V3 = i > 0 ? [q[0] - prev[0], q[1] - prev[1], q[2] - prev[2]] : [0.5, 0, 0];
  return [...p, [q[0] + d[0], q[1] + d[1], q[2] + d[2]] as V3];
}
