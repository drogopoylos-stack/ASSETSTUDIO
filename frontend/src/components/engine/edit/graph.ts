// The graph editor's arithmetic: how a value travels from one key to the next.
//
// Blender's default key is a Bezier with "auto clamped" handles: the curve passes smoothly
// through every key, lies flat on a peak or a trough so it never overshoots it, and the handles
// can be dragged to shape a move by hand. Here a handle is a slope (value per frame) on each side
// of a key, `ti` coming in and `to` going out; with neither set the key is automatic. The span
// between two keys is a cubic Hermite with those slopes, which is a Bezier with its handles a
// third of the span long, so what is drawn is what plays.
//
// The other interpolations are Blender's too: constant, linear, and the "dynamic effects" back,
// bounce and elastic, which shape the span from the key they start at.
//
// Plain numbers, no three, no React.

export const EASES = ["bezier", "linear", "constant", "smooth", "back", "bounce", "elastic"] as const;
export type Ease = (typeof EASES)[number];

export interface GraphKey { t: number; v: number; ease?: Ease; ti?: number; to?: number }

const smooth = (u: number) => u * u * (3 - 2 * u);
const back = (u: number) => {
  // Blender's "Back", in and out: a little behind the start, a little past the end.
  const s = 1.70158 * 1.525;
  return u < 0.5 ? (Math.pow(2 * u, 2) * ((s + 1) * 2 * u - s)) / 2 : (Math.pow(2 * u - 2, 2) * ((s + 1) * (u * 2 - 2) + s) + 2) / 2;
};
const bounce = (u: number) => {
  const n = 7.5625, d = 2.75;
  if (u < 1 / d) return n * u * u;
  if (u < 2 / d) return n * (u -= 1.5 / d) * u + 0.75;
  if (u < 2.5 / d) return n * (u -= 2.25 / d) * u + 0.9375;
  return n * (u -= 2.625 / d) * u + 0.984375;
};
const elastic = (u: number) => (u <= 0 ? 0 : u >= 1 ? 1 : Math.pow(2, -10 * u) * Math.sin((u * 10 - 0.75) * ((2 * Math.PI) / 3)) + 1);

/** The automatic slope at key i: the line from its neighbours, flat at a peak, a trough or an
 *  end, and never steep enough to carry the curve past the next key (Fritsch-Carlson). */
export function autoSlope(keys: GraphKey[], i: number): number {
  if (i <= 0 || i >= keys.length - 1) return 0;
  const p = keys[i - 1], k = keys[i], n = keys[i + 1];
  const d0 = (k.v - p.v) / Math.max(1e-9, k.t - p.t), d1 = (n.v - k.v) / Math.max(1e-9, n.t - k.t);
  if (d0 * d1 <= 0) return 0;
  const m = (n.v - p.v) / Math.max(1e-9, n.t - p.t);
  const lim = 3 * Math.min(Math.abs(d0), Math.abs(d1));
  return Math.sign(m) * Math.min(Math.abs(m), lim);
}

export const slopeIn = (keys: GraphKey[], i: number) => keys[i].ti ?? keys[i].to ?? autoSlope(keys, i);
export const slopeOut = (keys: GraphKey[], i: number) => keys[i].to ?? keys[i].ti ?? autoSlope(keys, i);

/** The value at frame t. Before the first key and after the last it holds. */
export function sampleKeys(keys: GraphKey[], t: number): number {
  if (!keys.length) return 0;
  if (t <= keys[0].t) return keys[0].v;
  const last = keys.length - 1;
  if (t >= keys[last].t) return keys[last].v;
  let i = 0;
  while (i < last && keys[i + 1].t <= t) i++;
  const a = keys[i], b = keys[i + 1];
  const span = b.t - a.t;
  if (span <= 0) return b.v;
  const u = (t - a.t) / span;
  switch (a.ease) {
    case "constant": return a.v;
    case "smooth": return a.v + (b.v - a.v) * smooth(u);
    case "back": return a.v + (b.v - a.v) * back(u);
    case "bounce": return a.v + (b.v - a.v) * bounce(u);
    case "elastic": return a.v + (b.v - a.v) * elastic(u);
    case "bezier": {
      // Bezier: cubic Hermite with the two slopes, scaled from per-frame to per-span.
      const m0 = slopeOut(keys, i) * span, m1 = slopeIn(keys, i + 1) * span;
      const u2 = u * u, u3 = u2 * u;
      return (2 * u3 - 3 * u2 + 1) * a.v + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * b.v + (u3 - u2) * m1;
    }
    // A key with no interpolation named is linear, as it always was before there were others.
    default: return a.v + (b.v - a.v) * u;
  }
}

/** The curve as points to draw, `steps` per frame between from and to. */
export function curvePoints(keys: GraphKey[], from: number, to: number, steps = 2): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  const n = Math.max(2, Math.ceil((to - from) * steps));
  for (let j = 0; j <= n; j++) { const t = from + ((to - from) * j) / n; out.push([t, sampleKeys(keys, t)]); }
  return out;
}

/** Where the handles of key i are drawn: a third of the way to each neighbour, along its slopes. */
export function handlePoints(keys: GraphKey[], i: number): { in: [number, number] | null; out: [number, number] | null } {
  const k = keys[i];
  const dl = i > 0 ? (k.t - keys[i - 1].t) / 3 : 0, dr = i < keys.length - 1 ? (keys[i + 1].t - k.t) / 3 : 0;
  return {
    in: dl > 0 ? [k.t - dl, k.v - slopeIn(keys, i) * dl] : null,
    out: dr > 0 ? [k.t + dr, k.v + slopeOut(keys, i) * dr] : null,
  };
}

/** Drag a handle to a point: the slope it now makes. `broken` moves that side alone, otherwise
 *  both sides stay in line (Blender's "aligned"). Returns the key's new ti and to. */
export function dragHandle(keys: GraphKey[], i: number, side: "in" | "out", to: [number, number], broken = false): { ti: number; to: number } {
  const k = keys[i];
  const dt = side === "in" ? k.t - to[0] : to[0] - k.t;
  const s = Math.abs(dt) < 1e-6 ? 0 : side === "in" ? (k.v - to[1]) / dt : (to[1] - k.v) / dt;
  const slope = isFinite(s) ? s : 0;
  if (!broken) return { ti: slope, to: slope };
  return side === "in" ? { ti: slope, to: slopeOut(keys, i) } : { ti: slopeIn(keys, i), to: slope };
}

/** Move key i to (t, v); the keys stay in time order. Returns the key's new index. A key cannot
 *  land on a frame another key holds: it stops a hundredth of a frame short. */
export function moveKey(keys: GraphKey[], i: number, t: number, v: number): number {
  const k = keys[i];
  let nt = t;
  while (keys.some((o, j) => j !== i && Math.abs(o.t - nt) < 1e-6)) nt += nt >= k.t ? -0.01 : 0.01;
  k.t = nt;
  k.v = v;
  keys.sort((a, b) => a.t - b.t);
  return keys.indexOf(k);
}

/** The range that shows every key of these tracks, with a margin: the graph's "frame all". */
export function graphBounds(tracks: GraphKey[][], from: number, to: number): { t0: number; t1: number; v0: number; v1: number } {
  let v0 = Infinity, v1 = -Infinity, t0 = from, t1 = to;
  for (const keys of tracks) {
    for (const [, v] of curvePoints(keys, from, to, 1)) { v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
    for (const k of keys) { v0 = Math.min(v0, k.v); v1 = Math.max(v1, k.v); t0 = Math.min(t0, k.t); t1 = Math.max(t1, k.t); }
  }
  if (!isFinite(v0)) { v0 = -1; v1 = 1; }
  if (v1 - v0 < 1e-3) { v0 -= 0.5; v1 += 0.5; }
  const m = (v1 - v0) * 0.12;
  return { t0, t1: t1 > t0 ? t1 : t0 + 1, v0: v0 - m, v1: v1 + m };
}
