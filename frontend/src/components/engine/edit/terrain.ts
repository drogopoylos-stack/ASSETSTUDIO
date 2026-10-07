/**
 * TERRAIN — the heightfield, the brushes, the layers and the scatter.
 *
 * THIS FILE IS THE CONTRACT. Three pieces of the Studio are being built against it at once: the
 * engine itself (here), the editor's Terrain mode, and the HTTP API an agent sculpts through. The
 * signatures below are fixed; the bodies are not. Anything added must be added here first.
 *
 * Pure logic on purpose — no DOM, no three, no PlayCanvas. That is what lets the same code run in
 * the editor's viewport, in a test with no browser, and inside the forge bench when an agent asks
 * for a stroke over HTTP. The renderers take `terrainMesh()` and make of it what they will.
 *
 * Sizes. `res` is samples per side and `size` is world units per side, so a 513-sample field over
 * 512 units is one sample per unit and the corners line up. Height is stored 0..1 and multiplied
 * by `maxHeight` at read time, so a field can be re-scaled without touching a sample.
 */

/** How big the ground is, and how finely it is sampled. */
export interface TerrainSpec {
  /** World units across. Square. */
  size: number;
  /** Samples per side. 2^n + 1, so the centre of a quad-tree split lands on a sample. */
  res: number;
  /** What a stored height of 1.0 means, in world units. */
  maxHeight: number;
  /** Where the field's south-west corner sits, so a world can hold more than one. */
  origin: [number, number, number];
  seed: number;
}

/** One paintable surface. Four of them fit in the splat map's four bytes. */
export interface TerrainLayer {
  name: string;
  /** Flat colour, used when there is no texture and as the tint under one. */
  colour: string;
  /** Optional image, resolved by the caller — this file never loads anything. */
  texture?: string;
  /** World units per texture repeat. */
  tiling: number;
}

/** One thing standing on the ground. */
export interface ScatterItem {
  /** Whatever the caller uses to name an asset: a Library id, a builder name, a file. */
  asset: string;
  at: [number, number, number];
  /** Radians about Y. */
  rot: number;
  scale: number;
  /** Set when the item was placed by a brush rather than by hand. */
  painted?: boolean;
  /**
   * Lean off vertical, radians about X and Z, applied after `rot`.
   *
   * ADDED: `Brush.jitter` promises a lean and had nowhere to land. Absent means dead upright, so
   * everything written before this field still reads the same.
   */
  tilt?: [number, number];
  /**
   * The slope limit, in degrees, of the stroke that planted this — not the slope it stands on.
   *
   * ADDED because two legitimate numbers disagreed in silence: a stroke planted at 12 degrees and
   * a `report(t, 30)` answered "none on steep ground" about ground no item had ever been offered.
   * Absent on anything placed by hand, which is not a planting rule and must not raise the note.
   */
  maxSlope?: number;
}

export interface TerrainData {
  spec: TerrainSpec;
  /** res * res, 0..1, row-major from the south-west corner. */
  height: Float32Array;
  /** res * res * 4 — one byte of weight per layer, summing to 255 at every sample. */
  splat: Uint8Array;
  layers: TerrainLayer[];
  scatter: ScatterItem[];
  /** ADDED, round two. Where the water ran, accumulated over every erode stroke. Absent until
   *  the first one.
   *
   *  READ IT THROUGH `flowField(t)`, and do not write it at all. What is stored here is the RAW
   *  accumulation; what `flowField()` hands back is the same numbers with `flow` normalised to
   *  0..1. The store has to be raw so an undone stroke can put its rectangle back exactly — see
   *  the note on `FlowField.flow`. */
  flow?: FlowField;
}

export type BrushKind =
  | "raise" | "lower" | "smooth" | "flatten" | "noise" | "erode"
  | "paint" | "scatter" | "erase"
  /** ADDED, round two: cut a channel along the drainage the erosion already found. Needs a flow
   *  field — with none it does nothing and says so in `stats`. */
  | "river";

export interface Brush {
  kind: BrushKind;
  /** World units. */
  radius: number;
  /** 0..1. What one second of a held brush does. */
  strength: number;
  /** 0 = a cylinder, 1 = a smooth bell. */
  falloff: number;
  /** `flatten` levels to this world height; when absent it takes the height under the cursor. */
  target?: number;
  /** `paint` writes this layer. */
  layer?: number;
  /** `scatter` places this asset. */
  asset?: string;
  /** `scatter`: items per square world unit per second. */
  density?: number;
  /** `scatter`: how far a placed item may lean from vertical, in radians. */
  jitter?: number;

  // ---- ADDED, all optional: a brush that leaves them out behaves as it did. ----
  /** `scatter`: degrees of slope above which nothing is planted. Default 30 — steeper than that
   *  and a tree stands on air, which is the one scatter fault everybody notices. */
  maxSlope?: number;
  /** `scatter`: closest two items may stand, world units. Default is set from `density`; this is
   *  what stops a held brush stacking twenty trees on one spot. */
  spacing?: number;
  /** `scatter`: the band of world height to plant in. Slope keeps trees off the cliffs; this is
   *  what keeps them out of the sea and off the snow line. */
  minHeight?: number;
  maxHeight?: number;
  /** `scatter`: the range a placed item's scale is drawn from. Default [0.85, 1.2]. */
  scaleRange?: [number, number];
  /** `noise`: world units per bump. Default a third of the radius. */
  freq?: number;
  /** `erode`: droplets this stroke runs, overriding the count taken from area and strength. */
  droplets?: number;
  /** `erode`: the angle of repose in degrees. Ground steeper than this slides downhill, which is
   *  what stops water carving two-cell needles it can never knock over. Default 42; 90 turns it
   *  off and leaves pure hydraulic erosion. */
  talus?: number;

  // ---- ADDED, round two. ----
  /** `paint`: weight the stroke by the drainage instead of painting one layer flat. Rock in the
   *  channels, silt where the water dropped its load, the dry layer between — see `flowLayers`.
   *  With no flow field the stroke paints `layer` as it always did AND says so in `stats.note`,
   *  because a paint that quietly ignored the flag is indistinguishable from one that worked.
   *
   *  `erode`: `false` turns the flow RECORDING off for this stroke. It is on by default, because
   *  the field is meant to accumulate over every erode stroke — but it is two Float32Arrays the
   *  size of the field (2 MB on a 513², 8 MB on a 1025²) plus a rectangle on every undo entry,
   *  and a caller who will never paint by drainage can decline to pay for it. */
  flow?: boolean;
  /** `river`: world units of channel cut per SECOND, at the busiest sample, at strength 1 and a
   *  brush weight of 1. Default 1.5.
   *
   *  The contract first said "how deep the channel is cut, world units", which would have made
   *  this the one brush that converges on a depth instead of being a rate — and decision 1 says
   *  every brush is a rate, so two frames at dt=1/60 and one call at dt=1/30 have to cut the
   *  same channel. A converging river needs a bed height to converge ON, and the only honest one
   *  is the surrounding ground, which moves as you cut. Rate it is. */
  depth?: number;
  /** `river` and `paint {flow:true}`: how much of the drainage counts as a channel, 0..1.
   *  Default 0.30 — lower carves more of the field, higher keeps to the main stems, exactly as
   *  the contract promised.
   *
   *  A RANK, NOT A LEVEL. It selects the wettest `0.10 * 0.1^channel` of the ground the water
   *  crossed in view: 0 is the wettest tenth, 0.30 the wettest 5%, 0.55 the wettest 2.8%, 1 the
   *  wettest 1%. The contract called it "the share of peak flow", and measured against the flow
   *  map that exists now that selects between 0.4% and 4.6% of four different fields for the one
   *  number 0.30 — see `channelLevel` for why, and for what a level cannot do here. */
  channel?: number;
}

/**
 * What one stroke changed, and enough of what was there before to put it back.
 *
 * A rectangle in SAMPLE space, not world space: undo has to be exact, and a world-space circle
 * rounds differently on the way back.
 */
export interface Patch {
  x0: number;
  z0: number;
  w: number;
  h: number;
  /** The heights that were there before, w*h. Absent when the stroke did not move the ground. */
  height?: Float32Array;
  /** The splat that was there before, w*h*4. */
  splat?: Uint8Array;
  /** Items this stroke added, so undo can remove them again. */
  scatterAdded?: ScatterItem[];
  /** Items this stroke removed, with the index each sat at. */
  scatterRemoved?: Array<{ at: number; item: ScatterItem }>;
  /** ADDED, round two. The drainage record as it stood before an `erode` stroke wrote into it,
   *  so undo puts the flow map back as well as the ground. Absent unless the stroke recorded
   *  flow. Without it a stroke that `plan` rolled back would leave channels behind on ground it
   *  no longer cut, and the next `paint {flow:true}` would paint rock down a gully that is not
   *  there. */
  flow?: FlowPatch;
  /** ADDED: what the stroke cost and did. An agent sculpting over HTTP has no picture, so the
   *  droplet count and the millisecond are the only way it can tell a stroke landed. */
  stats?: PatchStats;
}

/** The rectangle of the flow map a stroke overwrote, and the scalars that travel with it. */
export interface FlowPatch {
  x0: number;
  z0: number;
  w: number;
  h: number;
  /** w*h of raw accumulation, and w*h of world-unit cut. */
  flow: Float32Array;
  cut: Float32Array;
  peak: number;
  droplets: number;
  strokes: number;
  rev: number;
  box: [number, number, number, number];
  /** There was no flow field at all before this stroke, so undo removes the one it made rather
   *  than restoring an empty one — that is what keeps `t.flow === undefined` after a rollback. */
  fresh?: boolean;
}

export interface PatchStats {
  kind: BrushKind;
  /** Wall-clock for this one step. */
  ms: number;
  /** Samples the stroke wrote. */
  samples: number;
  /** `erode` only. */
  droplets?: number;
  /** World units of ground moved, summed over the rectangle — the honest "did anything happen". */
  moved?: number;
  placed?: number;
  removed?: number;
  /** ADDED, round two. Why a stroke did less than it looks as though it should have. A `river`
   *  with no drainage to follow is the case this exists for: it can only do nothing, and a
   *  silent no-op is the worst answer an agent with no picture can be handed. */
  note?: string;
  /** `river`: samples that were inside the channel. Zero with a note is the honest no-op. */
  channel?: number;
}

export interface MeshArrays {
  positions: Float32Array;
  normals: Float32Array;
  uvs: Float32Array;
  /** RGBA per vertex, ALREADY BLENDED: the layer colours mixed by the splat weights, so a
   *  renderer with no shader still shows the layers. Do NOT multiply by a layer colour again — a
   *  caller that did got a pure white first render. The raw weights are in `weights`. */
  colors: Float32Array;
  indices: Uint32Array;
  /** ADDED: the raw 0..1 layer weights, RGBA per vertex.
   *
   *  `colors` had to become the layers' colours BLENDED by those weights. Handing a plain
   *  `vertexColors: true` material the weights themselves paints layer 0 red and layer 1 green —
   *  it shows that there are layers, not the layers, and the promise above is the second one.
   *  A shader that wants to sample four textures reads this instead. */
  weights?: Float32Array;
}

export interface Collider {
  kind: "heightfield";
  res: number;
  size: number;
  maxHeight: number;
  origin: [number, number, number];
  /** row-major, 0..1, the same array the field holds. */
  height: Float32Array;
}

/* ===========================================================================================
 * HOW IT WORKS, and the four decisions that cost the most to get right.
 *
 * 1. EVERY BRUSH IS A RATE. `applyBrush(t, b, x, z, dt)` multiplies by dt, never by "one click",
 *    so 60 frames of a held stroke and one call with dt=1 land in the same place. The blends
 *    (smooth, flatten, paint) use 1-exp(-rate*dt) rather than a clamped linear step, because the
 *    linear one overshoots the moment a frame is slow and a dropped frame then shows as a divot.
 *
 * 2. UNDO IS THE BEFORE-IMAGE OF A RECTANGLE. Every stroke returns one. A sculpt stroke carries
 *    only heights, a paint stroke only the splat, a scatter stroke only the list delta — a 64x64
 *    rectangle is 16 KB of heights, so a hundred-stroke undo stack is 1.6 MB instead of the 4 MB
 *    per stroke a whole-field snapshot of a 513² costs.
 *
 * 3. EROSION IS DROPLETS, AND DROPLETS CONSERVE MASS. Everything a droplet picks up it puts down,
 *    including the load it is still carrying when it dies, so `report().lo/hi` closes in but the
 *    volume does not drift. That is what makes it safe to hold the brush: the ground does not
 *    slowly evaporate. A droplet is killed two cells inside the recorded rectangle, so no grain
 *    of sediment lands where undo cannot reach it.
 *
 * 4. THE FIELD IS THE ONLY SOURCE OF NORMALS. `terrainMesh({region})` computes its normals from
 *    the whole field, not from the patch — a patch that differentiated only its own vertices gets
 *    edge normals a few degrees off its neighbour's and the seam lights up as a grid across the
 *    ground. Same reason UVs are field-wide rather than 0..1 inside the region.
 *
 * Speed, measured on this desk (Node 24, one core of a Ryzen; see tests/terrain.test.ts, which
 * prints these on every run):
 *
 *      513x513 field (263k samples, 524k triangles), 32-unit brush, ONE frame at dt=1/60
 *        raise / lower ............  0.16 ms
 *        smooth ...................  0.27 ms
 *        flatten ..................  0.14 ms
 *        noise ....................  0.26 ms
 *        paint ....................  0.27 ms
 *        scatter ..................  0.04 ms
 *        erode ....................  0.41 ms   (54 droplets and one talus sweep)
 *        mesh, the brush's region .  0.55 ms
 *      and the whole-field work, which is not per frame
 *        mesh, all 524k triangles . 13 ms
 *        report ................... 14 ms
 *        serialize ................ 48 ms
 *        erode, a WHOLE second .... 19 ms      (3,217 droplets, 60 talus sweeps)
 *
 *    Every brush fits inside a 16 ms frame with room left for the renderer. Erosion is the
 *    expensive one and it is still 2.5% of a frame, because dt scales the droplet count: a whole
 *    second of it is 3,217 droplets and 19 ms, and it is holding the brush that carves the
 *    ground, never one heavy call. `smooth` at dt=1 is the exception that costs more than its
 *    per-frame price - it sub-steps sixty times, on purpose, so one call means one second.
 * =========================================================================================== */

// ------------------------------------------------------------------ small maths

const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const now = () => (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now());

/** Mulberry32, the same generator `ops.rng` uses. Copied rather than imported: this file is
 *  bundled into every emitted terrain builder, and a builder that imports the Studio is not a
 *  standalone builder. */
function rng(seed = 1): () => number {
  let a = (seed >>> 0) || 1;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** One integer out of several, so a stroke's randomness can be keyed to WHERE it is rather than
 *  to when it ran. Two strokes at the same place with the same seed then agree. */
function hashInts(...v: number[]): number {
  let h = 2166136261;
  for (const n of v) {
    h ^= n | 0;
    h = Math.imul(h, 16777619);
    h ^= h >>> 13;
  }
  return h >>> 0;
}

const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10);
const hash2 = (x: number, y: number, seed: number) => {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ seed;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};

/** Value noise in two dimensions, 0..1. */
export function noise2(x: number, y: number, seed = 1): number {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = fade(x - xi), yf = fade(y - yi);
  const a = hash2(xi, yi, seed), b = hash2(xi + 1, yi, seed);
  const c = hash2(xi, yi + 1, seed), d = hash2(xi + 1, yi + 1, seed);
  const top = a + (b - a) * xf, bot = c + (d - c) * xf;
  return top + (bot - top) * yf;
}

/** Octaves of it. Terrain wants ridges inside hills inside continents; one octave is a bedsheet. */
export function fbm2(x: number, y: number, octaves = 4, gain = 0.5, lacunarity = 2, seed = 1): number {
  let amp = 1, freq = 1, sum = 0, norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amp * noise2(x * freq, y * freq, seed + i * 977);
    norm += amp;
    amp *= gain;
    freq *= lacunarity;
  }
  return norm > 0 ? sum / norm : 0;
}

// ------------------------------------------------------------------ the field

/** World units between two samples. */
export function cellSize(s: TerrainSpec): number {
  return s.size / Math.max(1, s.res - 1);
}

/** A fresh field sits a quarter of the way up its range: 16 of the default 64 units to dig into.
 *  Starting at zero is the obvious choice and it makes the first `lower` stroke a silent no-op,
 *  because a stored height cannot go below 0. */
const BASE = 0.25;

const PALETTE: TerrainLayer[] = [
  { name: "ground", colour: "#7d8a6a", tiling: 8 },
  { name: "dirt", colour: "#8f7a5c", tiling: 8 },
  { name: "rock", colour: "#8d8f93", tiling: 12 },
  { name: "sand", colour: "#d9d2bd", tiling: 6 },
];

/** A flat field with one layer, ready to be sculpted. */
export function makeTerrain(spec?: Partial<TerrainSpec>): TerrainData {
  const size = Math.max(1e-3, spec?.size ?? 512);
  const res = Math.max(2, Math.round(spec?.res ?? 257));
  const maxHeight = Math.max(1e-6, spec?.maxHeight ?? 64);
  // The default origin puts the FLAT SURFACE at y=0, not the field's floor: a game drops a
  // character at y=0 and expects to land on the ground, not 16 units under it.
  const origin: [number, number, number] = spec?.origin
    ? [spec.origin[0], spec.origin[1], spec.origin[2]]
    : [0, -BASE * maxHeight, 0];
  const s: TerrainSpec = { size, res, maxHeight, origin, seed: spec?.seed ?? 1 };

  const n = res * res;
  const height = new Float32Array(n);
  height.fill(BASE);
  const splat = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) splat[i * 4] = 255;

  return { spec: s, height, splat, layers: [{ ...PALETTE[0] }], scatter: [] };
}

const idxOf = (res: number, i: number, j: number) => j * res + i;

/** A sample, with the edges extended rather than wrapped — a brush at the border reads the border
 *  again instead of the far side of the map. */
function sampleH(t: TerrainData, i: number, j: number): number {
  const r = t.spec.res;
  const ci = i < 0 ? 0 : i >= r ? r - 1 : i;
  const cj = j < 0 ? 0 : j >= r ? r - 1 : j;
  return t.height[cj * r + ci];
}

/** World x/z to fractional sample coordinates. */
function toSample(s: TerrainSpec, x: number, z: number): [number, number] {
  const c = cellSize(s);
  return [(x - s.origin[0]) / c, (z - s.origin[2]) / c];
}

/** The ground height in world units at a world x/z, bilinear between samples. */
export function heightAt(t: TerrainData, x: number, z: number): number {
  const s = t.spec;
  const [fi, fj] = toSample(s, x, z);
  // Clamped, not wrapped and not NaN: a character walking off the edge of the world should keep
  // standing on the last row of ground rather than fall through a NaN.
  const i = clamp(Math.floor(fi), 0, s.res - 1);
  const j = clamp(Math.floor(fj), 0, s.res - 1);
  const cx = clamp(fi - i, 0, 1), cz = clamp(fj - j, 0, 1);
  const h00 = sampleH(t, i, j), h10 = sampleH(t, i + 1, j);
  const h01 = sampleH(t, i, j + 1), h11 = sampleH(t, i + 1, j + 1);
  const top = h00 + (h10 - h00) * cx;
  const bot = h01 + (h11 - h01) * cx;
  return s.origin[1] + (top + (bot - top) * cz) * s.maxHeight;
}

/** The surface normal at a world x/z. */
export function normalAt(t: TerrainData, x: number, z: number): [number, number, number] {
  const c = cellSize(t.spec);
  const hl = heightAt(t, x - c, z), hr = heightAt(t, x + c, z);
  const hd = heightAt(t, x, z - c), hu = heightAt(t, x, z + c);
  const nx = hl - hr, ny = 2 * c, nz = hd - hu;
  const len = Math.hypot(nx, ny, nz) || 1;
  return [nx / len, ny / len, nz / len];
}

/** Slope in degrees at a sample, from the two neighbours that exist. */
function slopeDegAt(t: TerrainData, i: number, j: number): number {
  const s = t.spec;
  const c = cellSize(s);
  const i0 = Math.max(0, i - 1), i1 = Math.min(s.res - 1, i + 1);
  const j0 = Math.max(0, j - 1), j1 = Math.min(s.res - 1, j + 1);
  // The span, not a hard 2*c: at the border one neighbour is the sample itself and dividing by
  // two cells there reports half the real slope, which quietly makes every edge look walkable.
  const dx = (sampleH(t, i1, j) - sampleH(t, i0, j)) * s.maxHeight / ((i1 - i0) * c || c);
  const dz = (sampleH(t, i, j1) - sampleH(t, i, j0)) * s.maxHeight / ((j1 - j0) * c || c);
  return Math.atan(Math.hypot(dx, dz)) * 180 / Math.PI;
}

// ------------------------------------------------------------------ rectangles, capture, undo

interface Rect { x0: number; z0: number; w: number; h: number }

function clampRect(res: number, x0: number, z0: number, w: number, h: number): Rect {
  const ax = Math.max(0, Math.floor(x0)), az = Math.max(0, Math.floor(z0));
  const bx = Math.min(res, Math.floor(x0) + Math.max(0, Math.ceil(w)));
  const bz = Math.min(res, Math.floor(z0) + Math.max(0, Math.ceil(h)));
  return { x0: ax, z0: az, w: Math.max(0, bx - ax), h: Math.max(0, bz - az) };
}

/** The sample rectangle a world-space disc covers, padded by `pad` samples. */
function discRect(t: TerrainData, x: number, z: number, radius: number, pad = 0): Rect {
  const s = t.spec;
  const c = cellSize(s);
  const [fi, fj] = toSample(s, x, z);
  const r = radius / c + pad;
  return clampRect(s.res, Math.floor(fi - r), Math.floor(fj - r),
                   Math.ceil(fi + r) - Math.floor(fi - r) + 1,
                   Math.ceil(fj + r) - Math.floor(fj - r) + 1);
}

function grabHeight(t: TerrainData, r: Rect): Float32Array {
  const out = new Float32Array(r.w * r.h);
  const res = t.spec.res;
  for (let j = 0; j < r.h; j++) {
    out.set(t.height.subarray((r.z0 + j) * res + r.x0, (r.z0 + j) * res + r.x0 + r.w), j * r.w);
  }
  return out;
}

function grabSplat(t: TerrainData, r: Rect): Uint8Array {
  const out = new Uint8Array(r.w * r.h * 4);
  const res = t.spec.res;
  for (let j = 0; j < r.h; j++) {
    const from = ((r.z0 + j) * res + r.x0) * 4;
    out.set(t.splat.subarray(from, from + r.w * 4), j * r.w * 4);
  }
  return out;
}

/** The heights, splat and scatter inside a sample rectangle, as they are now. */
export function capture(t: TerrainData, x0: number, z0: number, w: number, h: number): Patch {
  const r = clampRect(t.spec.res, x0, z0, w, h);
  const p: Patch = { x0: r.x0, z0: r.z0, w: r.w, h: r.h };
  if (!r.w || !r.h) return p;
  p.height = grabHeight(t, r);
  p.splat = grabSplat(t, r);
  const c = cellSize(t.spec);
  const items: Array<{ at: number; item: ScatterItem }> = [];
  for (let k = 0; k < t.scatter.length; k++) {
    const it = t.scatter[k];
    const fi = (it.at[0] - t.spec.origin[0]) / c, fj = (it.at[2] - t.spec.origin[2]) / c;
    if (fi >= r.x0 && fi < r.x0 + r.w && fj >= r.z0 && fj < r.z0 + r.h) items.push({ at: k, item: it });
  }
  // A snapshot records what is standing here as "removed", meaning: if this is gone when the patch
  // is applied, put it back. `undoPatch` skips anything still present, so re-applying a snapshot
  // of an untouched region is a no-op rather than a doubling.
  if (items.length) p.scatterRemoved = items;
  return p;
}

/** Put a captured patch back. */
export function undoPatch(t: TerrainData, p: Patch): void {
  if (!p || !p.w || !p.h) {
    if (p?.flow) restoreFlow(t, p.flow);
    if (p?.scatterAdded || p?.scatterRemoved) restoreScatter(t, p);
    return;
  }
  const res = t.spec.res;
  if (p.height && p.height.length >= p.w * p.h) {
    for (let j = 0; j < p.h; j++) {
      t.height.set(p.height.subarray(j * p.w, j * p.w + p.w), (p.z0 + j) * res + p.x0);
    }
  }
  if (p.splat && p.splat.length >= p.w * p.h * 4) {
    for (let j = 0; j < p.h; j++) {
      t.splat.set(p.splat.subarray(j * p.w * 4, j * p.w * 4 + p.w * 4), ((p.z0 + j) * res + p.x0) * 4);
    }
  }
  if (p.flow) restoreFlow(t, p.flow);
  restoreScatter(t, p);
}

function restoreScatter(t: TerrainData, p: Patch): void {
  if (p.scatterAdded?.length) {
    const gone = new Set(p.scatterAdded);
    let w = 0;
    // In place. The editor hands `t.scatter` straight to an instanced mesh and keeps the
    // reference; swapping in a filtered copy leaves it drawing the trees you just undid.
    for (let i = 0; i < t.scatter.length; i++) if (!gone.has(t.scatter[i])) t.scatter[w++] = t.scatter[i];
    t.scatter.length = w;
  }
  if (p.scatterRemoved?.length) {
    const here = new Set(t.scatter);
    const back = p.scatterRemoved.slice().sort((a, b) => a.at - b.at);
    for (const e of back) {
      if (here.has(e.item)) continue;
      t.scatter.splice(Math.min(e.at, t.scatter.length), 0, e.item);
    }
  }
}

// ------------------------------------------------------------------ the splat, exactly

/**
 * Write four weights that sum to exactly 255.
 *
 * Largest-remainder: floor everything, then hand the leftover units to whichever channel lost the
 * most to rounding. Rounding each channel independently and hoping is off by one about half the
 * time, and a splat that sums to 254 shows as a dark seam wherever two layers meet, because the
 * missing weight is missing colour.
 */
function writeSplat(splat: Uint8Array, o: number, w0: number, w1: number, w2: number, w3: number): void {
  const w = [w0, w1, w2, w3];
  const total = w[0] + w[1] + w[2] + w[3];
  if (!(total > 0)) { splat[o] = 255; splat[o + 1] = 0; splat[o + 2] = 0; splat[o + 3] = 0; return; }
  const k = 255 / total;
  let sum = 0;
  const fl = [0, 0, 0, 0];
  const rem = [0, 0, 0, 0];
  for (let i = 0; i < 4; i++) {
    const v = w[i] * k;
    fl[i] = Math.floor(v);
    rem[i] = v - fl[i];
    sum += fl[i];
  }
  let left = 255 - sum;
  while (left > 0) {
    let best = 0;
    for (let i = 1; i < 4; i++) if (rem[i] > rem[best]) best = i;
    fl[best]++;
    rem[best] = -1;
    left--;
  }
  splat[o] = fl[0]; splat[o + 1] = fl[1]; splat[o + 2] = fl[2]; splat[o + 3] = fl[3];
}

/** Painting an index the layer list has not reached yet fills it in rather than refusing. The
 *  HTTP API hands us `layer: 2` on a terrain that still has one layer; refusing there would make
 *  "paint some rock on it" a two-call operation for no reason. */
function ensureLayer(t: TerrainData, i: number): number {
  const k = clamp(Math.round(i), 0, 3);
  while (t.layers.length <= k) t.layers.push({ ...PALETTE[Math.min(t.layers.length, 3)] });
  return k;
}

// ------------------------------------------------------------------ brushes

/** 1 at the centre, 0 outside. `falloff` mixes between a cylinder and a smoothstep bell, so the
 *  parameter is linear in the thing you can see rather than in an exponent. */
function weightAt(d: number, radius: number, falloff: number): number {
  if (d >= radius) return 0;
  const u = 1 - d / radius;
  const bell = u * u * (3 - 2 * u);
  return 1 + clamp01(falloff) * (bell - 1);
}

/** Blends approach their target instead of stepping toward it: at dt=0.5 twice and dt=1 once the
 *  answer is the same, and no frame length can overshoot and ring. */
const approach = (rate: number, dt: number) => 1 - Math.exp(-rate * dt);

const SMOOTH_RATE = 6;
const FLATTEN_RATE = 6;
const PAINT_RATE = 8;
/** Strength 1 held for one second moves the ground by a quarter of its range. Full range in a
 *  second turned a 64-unit field into a cliff before the mouse had travelled a centimetre. */
const NOISE_AMP = 0.25;

/**
 * One brush step at a world position. `dt` is seconds since the last step, so a stroke is
 * frame-rate independent and a test can apply a whole second in one call.
 *
 * Returns the BEFORE image of everything it touched: push that on an undo stack.
 */
export function applyBrush(t: TerrainData, b: Brush, x: number, z: number,
                           dt: number): Patch {
  const t0 = now();
  const s = t.spec;
  const cell = cellSize(s);
  // A brush thinner than a sample writes nothing at all and reads as a broken tool; half a cell
  // is the smallest radius that always covers one sample.
  const radius = Math.max(cell * 0.5, b.radius || 0);
  const strength = clamp(b.strength ?? 0, 0, 1);
  const fo = clamp01(b.falloff ?? 1);

  if (!(dt > 0) || !(strength > 0)) return { x0: 0, z0: 0, w: 0, h: 0 };

  if (b.kind === "scatter") return scatterStroke(t, b, x, z, dt, radius, fo, t0);
  if (b.kind === "erase") return eraseStroke(t, b, x, z, radius, t0);
  if (b.kind === "river") return riverStroke(t, b, x, z, dt, radius, fo, strength, t0);

  const paints = b.kind === "paint";
  // Erosion runs droplets that flow downhill out of the brush; the rectangle has to be wider than
  // the disc or a droplet deposits sediment outside the before-image and undo leaves a lump.
  const pad = b.kind === "erode" ? clamp(Math.round(radius / cell * 0.5), 4, 64) : 0;
  const r = discRect(t, x, z, radius, pad);
  const p: Patch = { x0: r.x0, z0: r.z0, w: r.w, h: r.h };
  if (!r.w || !r.h) { p.stats = { kind: b.kind, ms: now() - t0, samples: 0 }; return p; }

  const before = paints ? undefined : grabHeight(t, r);
  if (before) p.height = before;
  if (paints) p.splat = grabSplat(t, r);

  let droplets: number | undefined;
  let note: string | undefined;
  if (b.kind === "erode") {
    capped = null;
    droplets = erodeStroke(t, b, x, z, dt, r, radius, fo, strength, p);
  } else if (paints) {
    let map: Int8Array | null = null;
    if (b.flow) {
      if (flowField(t)) {
        // The choice FIRST, then the layers, then the map: `ensureLayer` lengthens `t.layers`,
        // and the fallbacks read that length, so choosing after creating picks different layers
        // on the second stroke than on the first.
        const pick = flowLayerChoice(t, b.channel !== undefined ? { channel: b.channel } : undefined);
        ensureLayer(t, pick.channelLayer);
        ensureLayer(t, pick.siltLayer);
        ensureLayer(t, pick.dryLayer);
        map = flowLayers(t, { ...pick, region: r });
      } else {
        note = "paint: `flow: true`, but nothing has been eroded here yet, so there is no"
          + " drainage to follow — this stroke painted layer " + (b.layer ?? 0) + " flat."
          + " An `erode` stroke over this ground is what fills the flow map in.";
      }
    }
    paintStroke(t, b, x, z, dt, r, radius, fo, strength, map);
  } else if (b.kind === "smooth") {
    // The one brush that does not compose over dt. Raise adds; flatten and paint approach a
    // target that stands still; smooth's target is the neighbourhood, and the neighbourhood moves
    // as the pass writes into it. One call at dt=1 came out 1.2% of the height range away from
    // sixty calls at 1/60 - a stroke that came out different on a slow machine. Sub-stepped at
    // 1/60 the two are bit-identical, and at the dt an editor actually passes this is one step.
    const steps = Math.min(64, Math.max(1, Math.round(dt * 60)));
    let src = before!;
    for (let k = 0; k < steps; k++) {
      if (k > 0) src = grabHeight(t, r);
      sculptStroke(t, b, x, z, dt / steps, r, radius, fo, strength, src);
    }
  } else {
    sculptStroke(t, b, x, z, dt, r, radius, fo, strength, before!);
  }

  let moved = 0;
  if (before) {
    const res = s.res;
    for (let j = 0; j < r.h; j++) {
      for (let i = 0; i < r.w; i++) {
        moved += Math.abs(t.height[(r.z0 + j) * res + r.x0 + i] - before[j * r.w + i]);
      }
    }
    moved *= s.maxHeight;
  }
  // `note` already carries the river's honest no-op. Append rather than replace: two things
  // can be worth saying about one stroke.
  if (capped) {
    const cn = "capped at " + capped[0].toLocaleString() + " droplets of "
      + capped[1].toLocaleString() + " - hold the brush instead of asking for "
      + (Math.round(dt * 10) / 10) + "s of a " + Math.round(radius) + "-unit one at once";
    note = note ? note + "; " + cn : cn;
  }
  p.stats = { kind: b.kind, ms: now() - t0, samples: r.w * r.h, moved, droplets,
              ...(note ? { note } : {}) };
  return p;
}

function sculptStroke(t: TerrainData, b: Brush, x: number, z: number, dt: number,
                      r: Rect, radius: number, fo: number, strength: number,
                      before: Float32Array): void {
  const s = t.spec;
  const res = s.res;
  const cell = cellSize(s);
  const [ci, cj] = toSample(s, x, z);
  const rs = radius / cell;

  const dir = b.kind === "lower" ? -1 : 1;
  const featureFreq = 1 / Math.max(cell, b.freq ?? radius / 3);
  // `flatten` with no target takes the ground under the cursor ONCE, before the stroke starts.
  // Re-reading it per sample flattens each sample to itself, which is the null operation people
  // report as "flatten does nothing on a slope".
  const targetNorm = b.kind !== "flatten" ? 0
    : b.target !== undefined ? clamp01((b.target - s.origin[1]) / s.maxHeight)
    : clamp01((heightAt(t, x, z) - s.origin[1]) / s.maxHeight);

  for (let j = 0; j < r.h; j++) {
    const sj = r.z0 + j;
    const dz = sj - cj;
    for (let i = 0; i < r.w; i++) {
      const si = r.x0 + i;
      const dx = si - ci;
      const d = Math.hypot(dx, dz);
      const w = weightAt(d, rs, fo);
      if (w <= 0) continue;
      const k = sj * res + si;
      const h = t.height[k];
      let nh = h;
      if (b.kind === "raise" || b.kind === "lower") {
        nh = h + dir * strength * w * dt;
      } else if (b.kind === "smooth") {
        // The source is the BEFORE image, never the live field. Averaging in place walks
        // row-major over cells this same pass already changed and drags the whole patch
        // south-east — a smear, not a smooth.
        let sum = 0, n = 0;
        for (let oj = -1; oj <= 1; oj++) {
          for (let oi = -1; oi <= 1; oi++) {
            const qi = si + oi, qj = sj + oj;
            if (qi < 0 || qj < 0 || qi >= res || qj >= res) continue;
            const li = qi - r.x0, lj = qj - r.z0;
            sum += (li >= 0 && lj >= 0 && li < r.w && lj < r.h)
              ? before[lj * r.w + li] : t.height[qj * res + qi];
            n++;
          }
        }
        nh = h + (sum / n - h) * approach(SMOOTH_RATE * strength * w, dt);
      } else if (b.kind === "flatten") {
        nh = h + (targetNorm - h) * approach(FLATTEN_RATE * strength * w, dt);
      } else if (b.kind === "noise") {
        const wx = s.origin[0] + si * cell, wz = s.origin[2] + sj * cell;
        // Keyed to world position and the field's seed, so going over the same ground twice
        // deepens the same bumps. Keyed to a call counter it would converge on white noise.
        const n = fbm2(wx * featureFreq, wz * featureFreq, 3, 0.5, 2, s.seed) * 2 - 1;
        nh = h + n * NOISE_AMP * strength * w * dt;
      }
      t.height[k] = clamp01(nh);
    }
  }
}

/** `map`, when given, says which layer each SAMPLE wants — that is `flow: true`. It is a
 *  whole-field Int8Array so the index arithmetic is the same one the splat uses; -1 means leave
 *  this sample alone, which is how ground no droplet ever crossed keeps the paint it had. */
function paintStroke(t: TerrainData, b: Brush, x: number, z: number, dt: number,
                     r: Rect, radius: number, fo: number, strength: number,
                     map?: Int8Array | null): void {
  const s = t.spec;
  const res = s.res;
  const cell = cellSize(s);
  const [ci, cj] = toSample(s, x, z);
  const rs = radius / cell;
  const flat = ensureLayer(t, b.layer ?? 0);

  for (let j = 0; j < r.h; j++) {
    const dz = r.z0 + j - cj;
    for (let i = 0; i < r.w; i++) {
      const dx = r.x0 + i - ci;
      const w = weightAt(Math.hypot(dx, dz), rs, fo);
      if (w <= 0) continue;
      const k = (r.z0 + j) * res + r.x0 + i;
      const L = map ? map[k] : flat;
      if (L < 0) continue;
      const o = k * 4;
      const cur = t.splat[o + L];
      // Toward 255, never past it: the layer approaches full cover and the other three shrink in
      // proportion, so no weight is created and none is lost. The rest of the sum keeps its
      // internal ratios, which is what stops a third layer vanishing when you paint over a second.
      const gain = (255 - cur) * approach(PAINT_RATE * strength * w, dt);
      if (gain < 1e-6) continue;
      const nl = cur + gain;
      const rest = 255 - cur;
      const scale = rest > 1e-9 ? (255 - nl) / rest : 0;
      writeSplat(t.splat, o,
        L === 0 ? nl : t.splat[o] * scale,
        L === 1 ? nl : t.splat[o + 1] * scale,
        L === 2 ? nl : t.splat[o + 2] * scale,
        L === 3 ? nl : t.splat[o + 3] * scale);
    }
  }
}

// ------------------------------------------------------------------ hydraulic erosion

/**
 * A raindrop falls in the brush, runs downhill, carries what it tears up and drops it where the
 * ground flattens out. Thousands of them, and a smooth lump becomes ground that it has rained on:
 * ridges sharpen, gullies fork, and fans of sediment spread at the foot of every slope.
 *
 * This is the brush the other editors do not have. Unity ships no erosion at all; Godot ships no
 * terrain; Blender's is a texture-space displacement, not a simulation. People buy Gaea or World
 * Machine to get what this function does, and then export a heightmap and lose the ability to
 * touch it again. Here it is a brush: hold it over one hillside for a second and only that
 * hillside erodes.
 *
 * TUNING. The droplet physics runs in WORLD units, not in stored 0..1 heights, so the same
 * constants behave the same on a 64-unit field and a 600-unit one. The conversion is
 * `maxHeight / cell` — how much world height one unit of stored height is worth per cell of
 * travel. Without it, doubling `res` halved every slope the droplets could feel and erosion on a
 * fine field did visibly nothing.
 */
const ERO = {
  /** How much of its old direction a droplet keeps. Near 1 and it ploughs straight through hills;
   *  0 and it turns so sharply it digs pits instead of channels. */
  inertia: 0.05,
  /** Sediment a droplet can hold per unit of slope, speed and water. */
  capacity: 3,
  deposit: 0.3,
  erode: 0.3,
  evaporate: 0.02,
  gravity: 10,
  /** Below this slope a droplet still holds a little, or a flat run drops its whole load in one
   *  cell and leaves a pimple. */
  minSlope: 0.01,
  /** A droplet that has not died in 48 steps is circling a basin; letting it run to 200 cost 4x
   *  the time for no visible change. */
  steps: 48,
  /** Droplets per square world unit per second at strength 1. A 32-unit brush is 3217 m², so a
   *  full-strength second is ~3200 droplets. */
  rate: 1,
  /** Degrees. Loose ground steeper than this slides. 42 is roughly dry scree; sand is nearer 34
   *  and wet clay holds past 50. */
  talus: 42,
  /** How fast it slides, per second. Capped at half the excess per pass, which is the most that
   *  can move without the two cells swapping which is higher. */
  slide: 8,
  /**
   * Most droplets ONE CALL will run when the count came from area and time.
   *
   * `rate` is linear in area x seconds, so a 230-unit brush for 3 s asks for 348,999 droplets and
   * blocks for 23.4 seconds — measured over HTTP. The dose that does anything is measured per
   * SAMPLE, not per second: at about two droplets a sample the brush has already taken a third of
   * the relief off, so a request that large at full strength is destructive whatever it costs.
   *
   * 60,000 is ~0.9 s here, and is what a 32-unit brush earns from eighteen unbroken seconds of
   * holding. A held brush never reaches it; only a single enormous `seconds` does, and that one
   * is told. An explicit `Brush.droplets` is not clamped — naming the number is deciding.
   */
  cap: 60000,
};

/**
 * The angle of repose, run once per erosion stroke.
 *
 * Mass-conserving by construction: whatever leaves a cell arrives at its lowest neighbour in the
 * same statement. Row-major and in place on purpose - a cell that has already received this pass
 * can pass it on, which is what lets a collapse travel the length of a slope in one sweep instead
 * of one cell per sweep.
 */
function talusPass(t: TerrainData, r: Rect, dt: number, deg: number,
                   ci: number, cj: number, rs: number, fo: number,
                   cut?: Float32Array | null): void {
  if (deg >= 89.5 || r.w < 3 || r.h < 3) return;
  const s = t.spec;
  const res = s.res;
  const H = t.height;
  // The threshold in STORED height, so it means the same angle whatever `res` and `maxHeight` are.
  const drop = Math.tan(deg * Math.PI / 180) * cellSize(s) / s.maxHeight;
  // Sub-stepped at 1/60, like smooth and for the same reason: half the excess is the most one
  // sweep may move, so a call with dt=0.5 cannot slide half a second's worth in a single sweep.
  // Un-stepped it left a needle at 87 degrees where thirty frames of 1/60 took it to 74.
  const steps = Math.min(64, Math.max(1, Math.round(dt * 60)));
  const rate = Math.min(0.5, ERO.slide * (dt / steps));
  for (let p = 0; p < steps; p++) {
    for (let j = r.z0 + 1; j < r.z0 + r.h - 1; j++) {
      for (let i = r.x0 + 1; i < r.x0 + r.w - 1; i++) {
        const k = j * res + i;
        const h = H[k];
        let lo = h - drop, at = -1;
        if (H[k - 1] < lo) { lo = H[k - 1]; at = k - 1; }
        if (H[k + 1] < lo) { lo = H[k + 1]; at = k + 1; }
        if (H[k - res] < lo) { lo = H[k - res]; at = k - res; }
        if (H[k + res] < lo) { lo = H[k + res]; at = k + res; }
        if (at < 0) continue;
        const w = weightAt(Math.hypot(i - ci, j - cj), rs, fo);
        if (w <= 0) continue;
        // Half the excess is the ceiling: move more and the two cells swap which is higher, and
        // the slope oscillates instead of settling.
        const move = (h - lo - drop) * 0.5 * rate * w;
        H[k] = h - move;
        H[at] += move;
        // Scree counts as sediment: the fan at the foot of a cliff is exactly what the silt
        // layer is for. Inside the `if`, so the cells that never slide cost nothing.
        if (cut) { const m = move * s.maxHeight; cut[k] += m; cut[at] -= m; }
      }
    }
  }
}

/** Set by `erodeStroke` when it clamped, read by `applyBrush` on the very next line. A return
 *  value would be cleaner and would change the signature of the one function the whole feature
 *  runs through; this is read once, immediately, on the same synchronous path. */
let capped: [number, number] | null = null;

function erodeStroke(t: TerrainData, b: Brush, x: number, z: number, dt: number,
                     r: Rect, radius: number, fo: number, strength: number,
                     p?: Patch): number {
  const s = t.spec;
  const res = s.res;
  const cell = cellSize(s);
  const H = t.height;
  const vscale = s.maxHeight / cell;
  const mh = s.maxHeight;
  const [ci, cj] = toSample(s, x, z);
  const rs = radius / cell;

  // The drainage record. Opened BEFORE anything writes — the talus pass below moves ground on the
  // zero-droplet path too, and a before-image taken after it is not a before-image.
  let F: FlowField | null = null;
  let FL: Float32Array | null = null;
  let CT: Float32Array | null = null;
  if (b.flow !== false) {
    const fresh = !t.flow || t.flow.res !== res || t.flow.flow.length !== res * res;
    F = ensureFlow(t);
    if (p) p.flow = grabFlow(t, F, r, fresh);
    FL = F.flow;
    CT = F.cut;
  }
  const done = (n: number): number => {
    if (F) {
      let peak = F.peak;
      for (let j = 0; j < r.h; j++) {
        const row = (r.z0 + j) * res + r.x0;
        for (let i = 0; i < r.w; i++) { const v = F.flow[row + i]; if (v > peak) peak = v; }
      }
      F.peak = peak;
      F.droplets += n;
      F.strokes += 1;
      F.rev++;
      growFlowBox(F, r);
      staleFlow(F);
    }
    return n;
  };

  const rnd = rng(hashInts(s.seed, Math.round(x * 16), Math.round(z * 16), 0x40d0));
  const named = b.droplets !== undefined;
  const asked = named
    ? Math.max(0, b.droplets as number)
    : ERO.rate * Math.PI * radius * radius * strength * dt;
  // THE CEILING, AND IT SAYS SO. See ERO.cap. Only the count taken from area and time is clamped;
  // a caller who named `droplets` has decided and is left alone.
  const want = named ? asked : Math.min(asked, ERO.cap);
  capped = want < asked ? [Math.round(want), Math.round(asked)] : null;
  // Stochastic rounding, so 0.4 of a droplet per frame is 4 droplets in ten frames rather than
  // none ever. A held brush at 60 fps depends on it.
  let n = Math.floor(want);
  if (rnd() < want - n) n++;
  if (n <= 0) { talusPass(t, r, dt, b.talus ?? ERO.talus, ci, cj, rs, fo, CT); return done(0); }

  // Two cells in from the recorded rectangle: the deposit kernel reaches one cell and the read
  // reaches one more, so this is the last position from which every write still lands inside.
  const lo_i = r.x0 + 2, hi_i = r.x0 + r.w - 3;
  const lo_j = r.z0 + 2, hi_j = r.z0 + r.h - 3;
  if (hi_i <= lo_i || hi_j <= lo_j) return done(0);

  const put = (px: number, pz: number, amount: number) => {
    const i = Math.floor(px), j = Math.floor(pz);
    const cx = px - i, cz = pz - j;
    const w00 = (1 - cx) * (1 - cz), w10 = cx * (1 - cz), w01 = (1 - cx) * cz, w11 = cx * cz;
    const a = j * res + i;
    H[a] = clamp01(H[a] + amount * w00);
    H[a + 1] = clamp01(H[a + 1] + amount * w10);
    H[a + res] = clamp01(H[a + res] + amount * w01);
    H[a + res + 1] = clamp01(H[a + res + 1] + amount * w11);
    // Ground laid down is a NEGATIVE cut. Recorded from the same statement that moves it, so the
    // two can never disagree about how much went where.
    if (CT) {
      const g = amount * mh;
      CT[a] -= g * w00;
      CT[a + 1] -= g * w10;
      CT[a + res] -= g * w01;
      CT[a + res + 1] -= g * w11;
    }
  };

  for (let d = 0; d < n; d++) {
    // Uniform over the disc: sqrt, or every droplet crowds the middle and the rim never erodes.
    const ang = rnd() * Math.PI * 2;
    const rad = Math.sqrt(rnd()) * rs;
    let px = ci + Math.cos(ang) * rad;
    let pz = cj + Math.sin(ang) * rad;
    if (px < lo_i || px > hi_i || pz < lo_j || pz > hi_j) continue;

    let dx = 0, dz = 0, speed = 1, water = 1, sed = 0;

    for (let step = 0; step < ERO.steps; step++) {
      const i = Math.floor(px), j = Math.floor(pz);
      const cx = px - i, cz = pz - j;
      const a = j * res + i;
      // The discharge, spread over the same four samples the height is read from. Bilinear and
      // not nearest: a nearest-cell tally draws the drainage as a staircase, and a staircase
      // thresholded at 0.3 of the peak is a dashed line of channel instead of a channel.
      const h00 = H[a], h10 = H[a + 1], h01 = H[a + res], h11 = H[a + res + 1];
      const h = h00 * (1 - cx) * (1 - cz) + h10 * cx * (1 - cz) + h01 * (1 - cx) * cz + h11 * cx * cz;
      const gx = (h10 - h00) * (1 - cz) + (h11 - h01) * cz;
      const gz = (h01 - h00) * (1 - cx) + (h11 - h10) * cx;

      dx = dx * ERO.inertia - gx * (1 - ERO.inertia);
      dz = dz * ERO.inertia - gz * (1 - ERO.inertia);
      const len = Math.hypot(dx, dz);
      if (len < 1e-9) { const t2 = rnd() * Math.PI * 2; dx = Math.cos(t2); dz = Math.sin(t2); }
      else { dx /= len; dz /= len; }

      const nx = px + dx, nz = pz + dz;
      if (nx < lo_i || nx > hi_i || nz < lo_j || nz > hi_j) { if (sed > 0) put(px, pz, sed / vscale); sed = 0; break; }

      const ni = Math.floor(nx), nj = Math.floor(nz);
      const ncx = nx - ni, ncz = nz - nj;
      const b2 = nj * res + ni;
      const nh = H[b2] * (1 - ncx) * (1 - ncz) + H[b2 + 1] * ncx * (1 - ncz)
        + H[b2 + res] * (1 - ncx) * ncz + H[b2 + res + 1] * ncx * ncz;
      const dh = (nh - h) * vscale;

      // THE DRAINAGE, recorded here and not at the top of the step, and weighted by the DROP.
      //
      // This is discharge times the height the water fell through in this cell — stream power,
      // the quantity that decides whether running water cuts. It is recorded only where the
      // water is going DOWNHILL, and it is recorded after `dh` is known, which is why the block
      // sits here rather than where the droplet's position is first read.
      //
      // Counting plain water volume instead, at every step, is what the first version did, and
      // it pointed at the wrong ground. Measured on four fields: the samples above twice the
      // mean averaged cut -5.3 (ground LAID DOWN) and the ones below half the mean averaged
      // +1.3 (ground taken away). Droplets converge on the low flats and stop there, so a
      // volume tally peaks exactly where the sediment settles — and `flowLayers`, `paint
      // {flow:true}` and `river` were all aimed at the silt. Weighted by the drop the numbers
      // invert: +2.8 in the busiest samples against +0.03 in the quietest.
      //
      // Bilinear over the same four samples the height is read from, not nearest: a nearest-cell
      // tally draws the drainage as a staircase, and a staircase thresholded is a dashed line of
      // channel instead of a channel.
      if (FL && dh < 0) {
        const q = water * -dh;
        FL[a] += q * (1 - cx) * (1 - cz);
        FL[a + 1] += q * cx * (1 - cz);
        FL[a + res] += q * (1 - cx) * cz;
        FL[a + res + 1] += q * cx * cz;
      }

      const cap = Math.max(-dh, ERO.minSlope) * speed * water * ERO.capacity;

      if (sed > cap || dh > 0) {
        // Uphill: put back exactly enough to fill the step, never more — that is what turns a
        // gully's end into a fan instead of a wall.
        const amount = dh > 0 ? Math.min(dh, sed) : (sed - cap) * ERO.deposit;
        if (amount > 0) { sed -= amount; put(px, pz, amount / vscale); }
      } else {
        // Only the PICKUP is scaled by the brush falloff, never the deposit. Scaling both loses
        // sediment at the rim and the field slowly sinks; scaling neither leaves a stamped circle.
        const w = weightAt(Math.hypot(px - ci, pz - cj), rs, fo);
        const amount = Math.min((cap - sed) * ERO.erode, -dh) * w;
        if (amount > 0) {
          // Spread over 3x3 rather than one cell: a point pickup carves a one-pixel needle that
          // the next droplet falls into, and the field fills with spikes.
          let tw = 0;
          for (let oj = -1; oj <= 1; oj++) for (let oi = -1; oi <= 1; oi++) {
            const ww = 1.5 - Math.hypot(oi, oj);
            if (ww > 0) tw += ww;
          }
          const inv = amount / vscale / (tw || 1);
          for (let oj = -1; oj <= 1; oj++) for (let oi = -1; oi <= 1; oi++) {
            const ww = 1.5 - Math.hypot(oi, oj);
            if (ww <= 0) continue;
            const k = (j + oj) * res + (i + oi);
            H[k] = clamp01(H[k] - inv * ww);
            if (CT) CT[k] += inv * ww * mh;
          }
          sed += amount;
        }
      }

      speed = Math.sqrt(Math.max(0, speed * speed - dh * ERO.gravity));
      water *= 1 - ERO.evaporate;
      px = nx; pz = nz;
      if (water < 0.01) { if (sed > 0) put(px, pz, sed / vscale); sed = 0; break; }
    }
    // Whatever is still in the water when the droplet runs out of steps goes back on the ground.
    // Dropping it is a mass leak: a hundred strokes of erosion and the whole field has sunk.
    if (sed > 0) put(px, pz, sed / vscale);
  }
  // After the water, not before: the droplets are what undercut the columns this knocks down.
  talusPass(t, r, dt, b.talus ?? ERO.talus, ci, cj, rs, fo, CT);
  return done(n);
}

// ------------------------------------------------------------------ scatter

/** A bucket grid over the standing items, so a candidate's spacing test looks at the nine cells
 *  around it instead of at all 20,000 trees. Rebuilt per stroke: O(n) once beats O(n) per
 *  candidate, and a stroke places tens of candidates. */
function scatterGrid(items: ScatterItem[], cell: number): Map<number, number[]> {
  const g = new Map<number, number[]>();
  const c = Math.max(1e-3, cell);
  for (let k = 0; k < items.length; k++) {
    const key = (Math.floor(items[k].at[0] / c) * 73856093) ^ (Math.floor(items[k].at[2] / c) * 19349663);
    const b = g.get(key);
    if (b) b.push(k); else g.set(key, [k]);
  }
  return g;
}

function scatterStroke(t: TerrainData, b: Brush, x: number, z: number, dt: number,
                       radius: number, fo: number, t0: number): Patch {
  const s = t.spec;
  const r = discRect(t, x, z, radius, 1);
  const p: Patch = { x0: r.x0, z0: r.z0, w: r.w, h: r.h };
  const density = b.density ?? 0.02;
  const area = Math.PI * radius * radius;
  // `strength` is deliberately NOT a factor here: `density` already says how many per second, and
  // multiplying by a strength slider left at 0.5 would silently halve the number the user typed.
  const want = density * area * dt;

  const rnd = rng(hashInts(s.seed, Math.round(x * 16), Math.round(z * 16), 0x5ca7));
  let n = Math.floor(want);
  if (rnd() < want - n) n++;
  if (n <= 0) { p.stats = { kind: "scatter", ms: now() - t0, samples: 0, placed: 0 }; return p; }

  const maxSlope = b.maxSlope ?? 30;
  const loY = b.minHeight ?? -Infinity, hiY = b.maxHeight ?? Infinity;
  const spacing = Math.max(cellSize(s) * 0.5, b.spacing ?? 0.75 / Math.sqrt(Math.max(1e-6, density)));
  const sp2 = spacing * spacing;
  const grid = scatterGrid(t.scatter, spacing);
  const asset = b.asset || "item";
  const [smin, smax] = b.scaleRange ?? [0.85, 1.2];
  const jitter = b.jitter ?? 0;
  const added: ScatterItem[] = [];

  const near = (px: number, pz: number): boolean => {
    const ci = Math.floor(px / spacing), cj = Math.floor(pz / spacing);
    for (let oj = -1; oj <= 1; oj++) {
      for (let oi = -1; oi <= 1; oi++) {
        const bucket = grid.get(((ci + oi) * 73856093) ^ ((cj + oj) * 19349663));
        if (!bucket) continue;
        for (const k of bucket) {
          const it = t.scatter[k];
          if (!it) continue;
          const ddx = it.at[0] - px, ddz = it.at[2] - pz;
          if (ddx * ddx + ddz * ddz < sp2) return true;
        }
      }
    }
    return false;
  };

  for (let k = 0; k < n; k++) {
    const ang = rnd() * Math.PI * 2;
    const rad = Math.sqrt(rnd()) * radius;
    const px = x + Math.cos(ang) * rad;
    const pz = z + Math.sin(ang) * rad;
    const roll = rnd(), rr = rnd(), rs2 = rnd(), rj = rnd(), rjd = rnd();
    // Every candidate burns the same five numbers whether or not it is placed, so a rejected
    // candidate does not shift the stream and the same seed keeps giving the same trees.
    if (roll > weightAt(Math.hypot(px - x, pz - z), radius, fo)) continue;
    const y = heightAt(t, px, pz);
    if (y < loY || y > hiY) continue;
    const nrm = normalAt(t, px, pz);
    if (Math.acos(clamp(nrm[1], -1, 1)) * 180 / Math.PI > maxSlope) continue;
    if (near(px, pz)) continue;
    const item: ScatterItem = {
      asset,
      at: [px, y, pz],
      rot: rr * Math.PI * 2,
      scale: smin + (smax - smin) * rs2,
      painted: true,
      maxSlope,
    };
    if (jitter > 0) {
      const lean = rj * jitter, dir = rjd * Math.PI * 2;
      item.tilt = [Math.cos(dir) * lean, Math.sin(dir) * lean];
    }
    const at = t.scatter.length;
    t.scatter.push(item);
    added.push(item);
    const key = (Math.floor(px / spacing) * 73856093) ^ (Math.floor(pz / spacing) * 19349663);
    const bucket = grid.get(key);
    if (bucket) bucket.push(at); else grid.set(key, [at]);
  }

  if (added.length) p.scatterAdded = added;
  p.stats = { kind: "scatter", ms: now() - t0, samples: 0, placed: added.length };
  return p;
}

function eraseStroke(t: TerrainData, b: Brush, x: number, z: number, radius: number, t0: number): Patch {
  const r = discRect(t, x, z, radius, 1);
  const p: Patch = { x0: r.x0, z0: r.z0, w: r.w, h: r.h };
  const r2 = radius * radius;
  // Absolute, not weighted by strength or falloff. A partial erase leaves a tree standing exactly
  // where you dragged, and the only recovery is to drag again — the tool reads as broken.
  // With `asset` set it erases only that kind, which is how you thin one species out of a mixed
  // wood without replanting the rest.
  const only = b.asset;
  const removed: Array<{ at: number; item: ScatterItem }> = [];
  // The grid, not a scan. `scatterNear` looks at the nine buckets around the brush; the old loop
  // took a hypot per tree on the whole field, so dragging an erase over empty ground on a
  // 20,000-tree map cost 20,000 distance tests a frame to remove nothing.
  void r2;
  const hits = scatterNear(t, x, z, radius);
  let kill: Set<number> | null = null;
  for (const i of hits) {
    if (only && t.scatter[i].asset !== only) continue;
    (kill ??= new Set<number>()).add(i);
  }
  if (kill) {
    // The compaction still walks the list, because the array has to close up — but only when
    // something is actually being removed, and `removed` comes out in ascending index order,
    // which is what `undoPatch` splices back against.
    let w = 0;
    for (let i = 0; i < t.scatter.length; i++) {
      const it = t.scatter[i];
      if (kill.has(i)) { removed.push({ at: i, item: it }); continue; }
      t.scatter[w++] = it;
    }
    t.scatter.length = w;
  }
  if (removed.length) p.scatterRemoved = removed;
  p.stats = { kind: "erase", ms: now() - t0, samples: 0, removed: removed.length };
  return p;
}

// ------------------------------------------------------------------ the mesh

/** sRGB hex to the linear 0..1 a renderer actually multiplies with.
 *
 *  three's ColorManagement reads a colour ATTRIBUTE as linear already, so handing it the sRGB
 *  bytes renders every layer washed out — grass at #4a7c3f arrives looking like #7aa86f. The same
 *  is true of PlayCanvas with gamma correction on, which is its default. */
function hexLinear(hex: string): [number, number, number] {
  const s = (hex || "#808080").replace("#", "");
  const n = s.length === 3
    ? parseInt(s[0] + s[0] + s[1] + s[1] + s[2] + s[2], 16)
    : parseInt(s.slice(0, 6).padEnd(6, "0"), 16);
  const to = (v: number) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  return [to(((n >> 16) & 255) / 255), to(((n >> 8) & 255) / 255), to((n & 255) / 255)];
}

function layerColours(t: TerrainData): number[][] {
  const out: number[][] = [];
  for (let i = 0; i < 4; i++) out.push(hexLinear(t.layers[i]?.colour ?? PALETTE[i].colour));
  return out;
}

/** The sampled row or column a mesh at this lod uses, with the last sample always in it. */
function lodLine(start: number, count: number, lod: number): number[] {
  const out: number[] = [];
  const last = start + count - 1;
  for (let v = start; v <= last; v += lod) out.push(v);
  if (out.length && out[out.length - 1] !== last) out.push(last);
  return out;
}

/**
 * Triangles for a renderer.
 *
 * `lod` skips samples: 1 is every sample, 2 is every other. `region` limits it to a sample
 * rectangle, so an editor can rebuild the patch a brush touched instead of the whole field.
 */
export function terrainMesh(t: TerrainData,
                            opts?: { lod?: number; region?: { x0: number; z0: number; w: number; h: number } },
                            ): MeshArrays {
  const s = t.spec;
  const res = s.res;
  const cell = cellSize(s);
  const lod = Math.max(1, Math.floor(opts?.lod ?? 1));
  const reg = opts?.region
    ? clampRect(res, opts.region.x0, opts.region.z0, opts.region.w, opts.region.h)
    : { x0: 0, z0: 0, w: res, h: res };

  // The stepped list, with the last sample forced in. Dropping it because (w-1) is not a multiple
  // of the lod leaves a strip of missing ground between two patches, and the sky shows through.
  // Shared with `terrainChunk`, which has to know the same vertex grid to hang a skirt on it.
  const cols = lodLine(reg.x0, reg.w, lod);
  const rows = lodLine(reg.z0, reg.h, lod);
  const nx = cols.length, nz = rows.length;

  const verts = nx * nz;
  const positions = new Float32Array(verts * 3);
  const normals = new Float32Array(verts * 3);
  const uvs = new Float32Array(verts * 2);
  const colors = new Float32Array(verts * 4);
  const weights = new Float32Array(verts * 4);
  const quads = nx > 1 && nz > 1 ? (nx - 1) * (nz - 1) : 0;
  const indices = new Uint32Array(quads * 6);
  const L = layerColours(t);
  const inv = 1 / Math.max(1, res - 1);

  for (let jj = 0; jj < nz; jj++) {
    const j = rows[jj];
    for (let ii = 0; ii < nx; ii++) {
      const i = cols[ii];
      const v = jj * nx + ii;
      const h = t.height[j * res + i];
      positions[v * 3] = s.origin[0] + i * cell;
      positions[v * 3 + 1] = s.origin[1] + h * s.maxHeight;
      positions[v * 3 + 2] = s.origin[2] + j * cell;

      // Differenced over `lod` cells, and always against the WHOLE field. Two patches at the same
      // lod then agree exactly along their shared edge; against the patch alone they differ by a
      // few degrees and the seam draws itself as a lit grid.
      const span = lod * cell;
      const hl = sampleH(t, i - lod, j), hr = sampleH(t, i + lod, j);
      const hd = sampleH(t, i, j - lod), hu = sampleH(t, i, j + lod);
      const nxv = (hl - hr) * s.maxHeight, nyv = 2 * span, nzv = (hd - hu) * s.maxHeight;
      const len = Math.hypot(nxv, nyv, nzv) || 1;
      normals[v * 3] = nxv / len;
      normals[v * 3 + 1] = nyv / len;
      normals[v * 3 + 2] = nzv / len;

      // Field-wide UVs, not 0..1 inside the region: a patch with its own 0..1 tiles its texture
      // at a different scale from the field around it.
      uvs[v * 2] = i * inv;
      uvs[v * 2 + 1] = j * inv;

      const o = (j * res + i) * 4;
      const w0 = t.splat[o] / 255, w1 = t.splat[o + 1] / 255, w2 = t.splat[o + 2] / 255, w3 = t.splat[o + 3] / 255;
      weights[v * 4] = w0; weights[v * 4 + 1] = w1; weights[v * 4 + 2] = w2; weights[v * 4 + 3] = w3;
      colors[v * 4] = L[0][0] * w0 + L[1][0] * w1 + L[2][0] * w2 + L[3][0] * w3;
      colors[v * 4 + 1] = L[0][1] * w0 + L[1][1] * w1 + L[2][1] * w2 + L[3][1] * w3;
      colors[v * 4 + 2] = L[0][2] * w0 + L[1][2] * w1 + L[2][2] * w2 + L[3][2] * w3;
      colors[v * 4 + 3] = 1;
    }
  }

  let q = 0;
  for (let jj = 0; jj + 1 < nz; jj++) {
    for (let ii = 0; ii + 1 < nx; ii++) {
      const a = jj * nx + ii, b = a + 1, c = a + nx, d = c + 1;
      // (a, c, b) and (b, c, d): counter-clockwise seen from above, so the front face is the one
      // you stand on. The other winding renders a hole with backface culling on.
      indices[q++] = a; indices[q++] = c; indices[q++] = b;
      indices[q++] = b; indices[q++] = c; indices[q++] = d;
    }
  }

  return { positions, normals, uvs, colors, indices, weights };
}

/** The field as a physics heightfield, in the form every engine here can take. */
export function collider(t: TerrainData): Collider {
  return {
    kind: "heightfield",
    res: t.spec.res,
    size: t.spec.size,
    maxHeight: t.spec.maxHeight,
    origin: [t.spec.origin[0], t.spec.origin[1], t.spec.origin[2]],
    // The same array, not a copy: a 513² copy is a megabyte, and a collider handed a stale copy
    // is worse than no collider — the player walks on ground that is no longer there.
    height: t.height,
  };
}

// ------------------------------------------------------------------ the numbers

/** Numbers a person or an agent can judge the ground by, with no picture. */
export interface TerrainReport {
  /** Lowest and highest world height. */
  lo: number;
  hi: number;
  /** Share of the field under each slope band, in degrees: 0-5, 5-15, 15-30, 30-45, 45+. */
  slopes: number[];
  /** Share of the field each layer covers, most first. */
  coverage: Array<{ layer: string; share: number }>;
  /** Share walkable at or under `maxSlope` degrees. */
  walkable: number;
  scatter: number;
  triangles: number;
  /** ADDED: the distinct slope limits the scattered items were planted under, lowest first. */
  plantedUnder?: number[];
  /** ADDED: plain sentences about the numbers above. Chiefly: when this report's idea of "steep"
   *  is not the one the ground was planted under, which is a disagreement neither number can show
   *  on its own. */
  notes?: string[];
}

export function report(t: TerrainData, maxSlope = 30): TerrainReport {
  const s = t.spec;
  const res = s.res;
  const n = res * res;
  let lo = Infinity, hi = -Infinity;
  const slopes = [0, 0, 0, 0, 0];
  let walk = 0;
  const cover = [0, 0, 0, 0];

  for (let j = 0; j < res; j++) {
    for (let i = 0; i < res; i++) {
      const h = t.height[j * res + i];
      if (h < lo) lo = h;
      if (h > hi) hi = h;
      const deg = slopeDegAt(t, i, j);
      slopes[deg < 5 ? 0 : deg < 15 ? 1 : deg < 30 ? 2 : deg < 45 ? 3 : 4]++;
      if (deg <= maxSlope) walk++;
      const o = (j * res + i) * 4;
      cover[0] += t.splat[o]; cover[1] += t.splat[o + 1];
      cover[2] += t.splat[o + 2]; cover[3] += t.splat[o + 3];
    }
  }

  const coverage = cover
    .map((v, i) => ({ layer: t.layers[i]?.name ?? "layer " + i, share: v / (n * 255) }))
    .filter((c, i) => i < t.layers.length || c.share > 0)
    .sort((a, b) => b.share - a.share);

  // Two legitimate slope numbers can disagree without either being wrong, and the reader has no
  // way to see it: `walkable` is measured at whatever this call was given, while every planted
  // item was refused ground steeper than whatever ITS stroke was given. Say it out loud.
  const limits = new Set<number>();
  for (const it of t.scatter) if (typeof it.maxSlope === "number") limits.add(it.maxSlope);
  const plantedUnder = [...limits].sort((a, b) => a - b);
  const notes: string[] = [];
  if (plantedUnder.some((v) => Math.abs(v - maxSlope) > 0.5)) {
    const most = Math.max(...plantedUnder);
    notes.push(t.scatter.length + " scattered item" + (t.scatter.length === 1 ? " was" : "s were")
      + " planted under a slope limit of " + plantedUnder.join(" and ")
      + " degrees, but this report measures walkable at " + maxSlope
      + ". They are not the same question: nothing was ever offered ground steeper than " + most
      + " degrees, so a scatter count that looks complete against this report is not."
      + " report(t, " + plantedUnder[0] + ") compares like with like.");
  }

  return {
    lo: s.origin[1] + lo * s.maxHeight,
    hi: s.origin[1] + hi * s.maxHeight,
    slopes: slopes.map((v) => v / n),
    coverage,
    walkable: walk / n,
    scatter: t.scatter.length,
    triangles: (res - 1) * (res - 1) * 2,
    plantedUnder,
    notes,
  };
}

// ------------------------------------------------------------------ save and load

// Base64 written out rather than taken from `btoa`: this runs in a node test with no DOM, and
// `String.fromCharCode(...bytes)` on a megabyte of heights overflows the argument stack anyway.
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
// Pure, so a bundle that never decodes a height map (studio-runtime.js) leaves the table out.
const B64R = /* @__PURE__ */ (() => {
  const r = new Int16Array(128).fill(-1);
  for (let i = 0; i < 64; i++) r[B64.charCodeAt(i)] = i;
  return r;
})();

export function b64encode(bytes: Uint8Array): string {
  let out = "";
  const chunk: string[] = [];
  const n = bytes.length;
  for (let i = 0; i < n; i += 3) {
    const a = bytes[i], b = i + 1 < n ? bytes[i + 1] : 0, c = i + 2 < n ? bytes[i + 2] : 0;
    const v = (a << 16) | (b << 8) | c;
    chunk.push(B64[(v >> 18) & 63], B64[(v >> 12) & 63],
      i + 1 < n ? B64[(v >> 6) & 63] : "=", i + 2 < n ? B64[v & 63] : "=");
    if (chunk.length >= 8192) { out += chunk.join(""); chunk.length = 0; }
  }
  return out + chunk.join("");
}

export function b64decode(s: string): Uint8Array {
  let len = s.length;
  while (len > 0 && s.charCodeAt(len - 1) === 61) len--;
  const out = new Uint8Array((len * 3) >> 2);
  let o = 0, buf = 0, bits = 0;
  for (let i = 0; i < len; i++) {
    const code = s.charCodeAt(i);
    const v = code < 128 ? B64R[code] : -1;
    if (v < 0) continue;
    buf = (buf << 6) | v;
    bits += 6;
    if (bits >= 8) { bits -= 8; out[o++] = (buf >> bits) & 255; }
  }
  return o === out.length ? out : out.subarray(0, o);
}

// Explicit little-endian rather than a view on the buffer: the view is host order, and a save
// file that only opens on the machine that wrote it is not a save file.
function f32bytes(a: Float32Array): Uint8Array {
  const out = new Uint8Array(a.length * 4);
  const dv = new DataView(out.buffer);
  for (let i = 0; i < a.length; i++) dv.setFloat32(i * 4, a[i], true);
  return out;
}

function bytesF32(b: Uint8Array): Float32Array {
  const out = new Float32Array(b.length >> 2);
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  for (let i = 0; i < out.length; i++) out[i] = dv.getFloat32(i * 4, true);
  return out;
}

/**
 * JSON with the typed arrays base64'd. Round-trips through `deserialize` exactly.
 *
 * THE FLOW MAP SURVIVES, and it is worth saying why and at what price. An agent that erodes,
 * saves, reloads and then paints by drainage must not silently get a flat paint — that is a bug
 * with no symptom until someone looks at the ground. So it is written, but not the way the
 * heights are:
 *
 *   - only the rectangle that has ever received water. Outside it every sample is exactly zero,
 *     and a brush-sized erode on a big field then costs a few KB instead of megabytes.
 *   - 16-bit, against the peak (flow) and against the largest deposit (cut). Flow is read as a
 *     share of the peak and thresholded at 0.30; a resolution of 1/65535 is four orders finer
 *     than any decision made with it.
 *
 * A whole-field erode on a 513² is still 1.4 MB of base64, about the same again as the heights.
 * `serialize(t, { flow: false })` leaves it out when that is the wrong trade.
 */
export function serialize(t: TerrainData, opts?: { flow?: boolean }): string {
  return JSON.stringify({
    terrain: 1,
    spec: {
      size: t.spec.size, res: t.spec.res, maxHeight: t.spec.maxHeight,
      origin: [t.spec.origin[0], t.spec.origin[1], t.spec.origin[2]], seed: t.spec.seed,
    },
    layers: t.layers.map((l) => ({ name: l.name, colour: l.colour, texture: l.texture, tiling: l.tiling })),
    scatter: t.scatter,
    height: b64encode(f32bytes(t.height)),
    splat: b64encode(t.splat),
    flow: opts?.flow === false ? undefined : packFlow(t.flow),
  });
}

export function deserialize(s: string): TerrainData {
  const j = typeof s === "string" ? JSON.parse(s) : s;
  if (!j || !j.spec) throw new Error("terrain.deserialize: not a terrain document");
  const spec: TerrainSpec = {
    size: Number(j.spec.size) || 512,
    res: Math.max(2, Math.round(Number(j.spec.res) || 257)),
    maxHeight: Number(j.spec.maxHeight) || 64,
    origin: [Number(j.spec.origin?.[0]) || 0, Number(j.spec.origin?.[1]) || 0, Number(j.spec.origin?.[2]) || 0],
    seed: Number(j.spec.seed) || 1,
  };
  const n = spec.res * spec.res;
  const height = bytesF32(b64decode(String(j.height || "")));
  const splat = b64decode(String(j.splat || ""));
  if (height.length < n) throw new Error("terrain.deserialize: the field is " + height.length + " samples, the spec says " + n);
  const t: TerrainData = {
    spec,
    height: height.length === n ? height : height.slice(0, n),
    splat: splat.length === n * 4 ? splat : new Uint8Array(n * 4),
    layers: Array.isArray(j.layers) && j.layers.length
      ? j.layers.map((l: any) => ({ name: String(l?.name ?? "layer"), colour: String(l?.colour ?? "#808080"), texture: l?.texture, tiling: Number(l?.tiling) || 8 }))
      : [{ ...PALETTE[0] }],
    scatter: Array.isArray(j.scatter) ? j.scatter.map((it: any) => ({
      asset: String(it?.asset ?? "item"),
      at: [Number(it?.at?.[0]) || 0, Number(it?.at?.[1]) || 0, Number(it?.at?.[2]) || 0] as [number, number, number],
      rot: Number(it?.rot) || 0,
      scale: Number(it?.scale) || 1,
      ...(it?.painted ? { painted: true } : {}),
      ...(it?.maxSlope !== undefined ? { maxSlope: Number(it.maxSlope) } : {}),
      ...(it?.tilt ? { tilt: [Number(it.tilt[0]) || 0, Number(it.tilt[1]) || 0] as [number, number] } : {}),
    })) : [],
  };
  if (splat.length !== n * 4) for (let i = 0; i < n; i++) t.splat[i * 4] = 255;
  const flow = unpackFlow(j.flow, spec.res);
  if (flow) t.flow = flow;
  return t;
}

// ------------------------------------------------------------------ the emitted builder

/** A run-length pass over the splat's 4-byte tuples, used only if it actually wins.
 *  A field painted one layer everywhere is 263,000 identical tuples — 1 MB raw, 5 KB coded. A
 *  field with noise painted into it has no runs and the coding would ADD 25%, so both are
 *  measured and the smaller one is emitted with a flag. */
function rleSplat(splat: Uint8Array): Uint8Array | null {
  const out: number[] = [];
  const n = splat.length >> 2;
  let i = 0;
  while (i < n) {
    const o = i * 4;
    const a = splat[o], b = splat[o + 1], c = splat[o + 2], d = splat[o + 3];
    let run = 1;
    while (run < 255 && i + run < n) {
      const p = (i + run) * 4;
      if (splat[p] !== a || splat[p + 1] !== b || splat[p + 2] !== c || splat[p + 3] !== d) break;
      run++;
    }
    out.push(run, a, b, c, d);
    i += run;
    if (out.length >= splat.length) return null;
  }
  return Uint8Array.from(out);
}

const RESERVED = new Set(["heightAt", "normalAt", "layers", "scatter", "spec", "default"]);

/**
 * The game's own code for this ground.
 *
 * This project builds assets IN CODE, so terrain has to leave the editor as a function the game
 * calls — not as a binary the editor alone can read. The emitted module carries the field, the
 * layers and the scatter, and needs nothing from the Studio at run time.
 */
export function emitBuilder(t: TerrainData, name?: string,
                            engine?: "three" | "playcanvas",
                            opts?: {
                              lod?: number;
                              /** PlayCanvas only. The four-layer splat material is appended by
                               *  default — `false` emits the smaller file whose ground is one
                               *  averaged colour a vertex, which is every file emitted before
                               *  this and all a stock StandardMaterial can do. */
                              splat?: boolean;
                            }): string {
  const eng = engine === "playcanvas" ? "playcanvas" : "three";
  let fn = String(name || "buildTerrain").replace(/[^A-Za-z0-9_$]/g, "") || "buildTerrain";
  if (/^[0-9]/.test(fn)) fn = "_" + fn;
  if (RESERVED.has(fn)) fn = fn + "Terrain";

  // Emitting at a lower resolution is a real option, not a nicety: a 513² field is 700 KB of
  // base64 in the game's source tree, and most games do not need a sample per metre.
  let src = t;
  const lod = Math.max(1, Math.floor(opts?.lod ?? 1));
  if (lod > 1 && (t.spec.res - 1) % lod === 0) src = downsample(t, lod);

  const s = src.spec;
  const n = s.res * s.res;

  // 16-bit heights. At the default 64-unit range that is 0.001 units of error — a millimetre if a
  // unit is a metre — for half the bytes of a float32 field.
  const q = new Uint8Array(n * 2);
  for (let i = 0; i < n; i++) {
    const v = Math.round(clamp01(src.height[i]) * 65535);
    q[i * 2] = v & 255;
    q[i * 2 + 1] = (v >> 8) & 255;
  }

  let splatB64 = "";
  let splatRle = false;
  let plain = true;
  for (let i = 0; i < n && plain; i++) if (src.splat[i * 4] !== 255) plain = false;
  if (!plain) {
    const rle = rleSplat(src.splat);
    if (rle) { splatB64 = b64encode(rle); splatRle = true; }
    else splatB64 = b64encode(src.splat);
  }

  // `texture` travels. It shipped stripped, so a field painted with four images emitted four
  // flat colours and the game got none of them — and nothing in the emitted file said a texture
  // had ever existed. The module still loads nothing (it imports nothing, by design); it carries
  // the PATH so the game can load it and hand it back in `{ textures }`.
  type EmitLayer = { name: string; colour: string; tiling: number; texture?: string };
  const L: EmitLayer[] = src.layers.slice(0, 4).map((l) => ({
    name: l.name, colour: l.colour, tiling: l.tiling,
    ...(l.texture ? { texture: l.texture } : {}),
  }));
  while (L.length < 1) L.push({ name: PALETTE[0].name, colour: PALETTE[0].colour, tiling: PALETTE[0].tiling });
  const r3 = (v: number) => Math.round(v * 1000) / 1000;
  const items = src.scatter.map((it) => [it.asset, r3(it.at[0]), r3(it.at[1]), r3(it.at[2]),
    r3(it.rot), r3(it.scale), r3(it.tilt?.[0] ?? 0), r3(it.tilt?.[1] ?? 0)]);

  const head = [
    "// " + fn + " — ground built in the Studio's terrain editor, emitted as code.",
    "//",
    "// Standalone: this module imports nothing. The field is 16-bit (" +
      (s.maxHeight / 65535).toFixed(5) + " world units of error), the splat is " +
      (plain ? "one layer everywhere and not stored" : splatRle ? "run-length coded" : "raw") +
      ", and " + items.length + " scattered item" + (items.length === 1 ? "" : "s") + " ride along as a list.",
    "//",
    "// " + (L.some((l) => l.texture)
      ? "Layer texture paths travel with the layers (" + L.filter((l) => l.texture).length
        + " of " + L.length + " have one). This module loads nothing — read " + fn
        + ".layers[i].texture, load it yourself, and pass the loaded textures back in { textures }."
      : "No layer carries a texture, so the four colours are the whole surface."),
    "//",
    "//   " + (eng === "three"
      ? "const ground = " + fn + "(THREE);  scene.add(ground);   // four layers, blended per pixel"
      : "const ground = " + fn + "(pc, app);  app.root.addChild(ground);   // four layers, blended per pixel"),
    "//   " + fn + "(" + (eng === "three" ? "THREE" : "pc, app")
      + ", { textures: [grass, dirt, rock, sand] });   // the images " + fn + ".layers names",
    "//   " + fn + "(" + (eng === "three" ? "THREE" : "pc, app")
      + ", { splat: false });                          // flat vertex colour, as it used to be",
    "//   const y = " + fn + ".heightAt(x, z);            // stand something on it",
    "//   " + fn + "(" + (eng === "three" ? "THREE" : "pc, app") + ", { place: (item) => makeTree(item) });   // grow the scatter",
    "//",
    "// No timestamp on purpose: this file gets committed, and a date in it makes every re-emit a diff.",
    "",
    "const RES = " + s.res + ", SIZE = " + s.size + ", MAXH = " + s.maxHeight + ";",
    "const OX = " + s.origin[0] + ", OY = " + s.origin[1] + ", OZ = " + s.origin[2] + ";",
    "const CELL = SIZE / (RES - 1);",
    "const LAYERS = " + JSON.stringify(L) + ";",
    "const SCATTER_RAW = " + JSON.stringify(items) + ";",
    "const HEIGHT_B64 = " + JSON.stringify(b64encode(q)) + ";",
    "const SPLAT_B64 = " + JSON.stringify(splatB64) + ";",
    "const SPLAT_RLE = " + (splatRle ? "true" : "false") + ";",
    "",
    "const B64 = " + JSON.stringify(B64) + ";",
    "function unb64(s) {",
    "  let len = s.length;",
    "  while (len > 0 && s.charCodeAt(len - 1) === 61) len--;",
    "  const out = new Uint8Array((len * 3) >> 2);",
    "  const R = new Int16Array(128).fill(-1);",
    "  for (let i = 0; i < 64; i++) R[B64.charCodeAt(i)] = i;",
    "  let o = 0, buf = 0, bits = 0;",
    "  for (let i = 0; i < len; i++) {",
    "    const c = s.charCodeAt(i); const v = c < 128 ? R[c] : -1;",
    "    if (v < 0) continue;",
    "    buf = (buf << 6) | v; bits += 6;",
    "    if (bits >= 8) { bits -= 8; out[o++] = (buf >> bits) & 255; }",
    "  }",
    "  return out;",
    "}",
    "",
    "let _H = null;",
    "function field() {",
    "  if (_H) return _H;",
    "  const b = unb64(HEIGHT_B64);",
    "  _H = new Float32Array(RES * RES);",
    "  for (let i = 0; i < _H.length; i++) _H[i] = (b[i * 2] | (b[i * 2 + 1] << 8)) / 65535;",
    "  return _H;",
    "}",
    "",
    "let _S = null;",
    "function splat() {",
    "  if (_S) return _S;",
    "  const n = RES * RES;",
    "  _S = new Uint8Array(n * 4);",
    "  if (!SPLAT_B64) { for (let i = 0; i < n; i++) _S[i * 4] = 255; return _S; }",
    "  const b = unb64(SPLAT_B64);",
    "  if (!SPLAT_RLE) { _S.set(b.subarray(0, n * 4)); return _S; }",
    "  let o = 0;",
    "  for (let i = 0; i + 4 < b.length; i += 5) {",
    "    for (let k = 0; k < b[i] && o < n; k++, o++) {",
    "      _S[o * 4] = b[i + 1]; _S[o * 4 + 1] = b[i + 2]; _S[o * 4 + 2] = b[i + 3]; _S[o * 4 + 3] = b[i + 4];",
    "    }",
    "  }",
    "  return _S;",
    "}",
    "",
    "function at(i, j) {",
    "  const H = field();",
    "  const ci = i < 0 ? 0 : i >= RES ? RES - 1 : i;",
    "  const cj = j < 0 ? 0 : j >= RES ? RES - 1 : j;",
    "  return H[cj * RES + ci];",
    "}",
    "",
    "/** World height at a world x/z — the same bilinear read the editor used, so a character",
    " *  stands on the drawn ground rather than a few centimetres inside it. */",
    "export function heightAt(x, z) {",
    "  const fi = (x - OX) / CELL, fj = (z - OZ) / CELL;",
    "  const i = Math.min(RES - 1, Math.max(0, Math.floor(fi)));",
    "  const j = Math.min(RES - 1, Math.max(0, Math.floor(fj)));",
    "  const cx = Math.min(1, Math.max(0, fi - i)), cz = Math.min(1, Math.max(0, fj - j));",
    "  const a = at(i, j) + (at(i + 1, j) - at(i, j)) * cx;",
    "  const b = at(i, j + 1) + (at(i + 1, j + 1) - at(i, j + 1)) * cx;",
    "  return OY + (a + (b - a) * cz) * MAXH;",
    "}",
    "",
    "export function normalAt(x, z) {",
    "  const l = heightAt(x - CELL, z), r = heightAt(x + CELL, z);",
    "  const d = heightAt(x, z - CELL), u = heightAt(x, z + CELL);",
    "  const nx = l - r, ny = 2 * CELL, nz = d - u;",
    "  const k = Math.hypot(nx, ny, nz) || 1;",
    "  return [nx / k, ny / k, nz / k];",
    "}",
    "",
    "export const layers = LAYERS;",
    "export const scatter = SCATTER_RAW.map(function (s) {",
    "  return { asset: s[0], at: [s[1], s[2], s[3]], rot: s[4], scale: s[5], tilt: [s[6], s[7]] };",
    "});",
    "",
    "function lin(hex) {",
    "  const s = String(hex || '#808080').replace('#', '');",
    "  const n = parseInt(s.length === 3 ? s[0] + s[0] + s[1] + s[1] + s[2] + s[2] : s.slice(0, 6), 16) || 0;",
    "  const f = (v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));",
    "  return [f(((n >> 16) & 255) / 255), f(((n >> 8) & 255) / 255), f((n & 255) / 255)];",
    "}",
    "",
    "function arrays(lod) {",
    "  const step = Math.max(1, Math.floor(lod || 1));",
    "  const line = [];",
    "  for (let v = 0; v < RES; v += step) line.push(v);",
    "  if (line[line.length - 1] !== RES - 1) line.push(RES - 1);",
    "  const nx = line.length, verts = nx * nx;",
    "  const pos = new Float32Array(verts * 3), nrm = new Float32Array(verts * 3);",
    "  const uv = new Float32Array(verts * 2), col = new Float32Array(verts * 4);",
    "  // The RAW 0..1 layer weights, in this same vertex order. Emitted from here rather than",
    "  // recomputed by anything downstream: a second copy of the stepping rule is a weight array",
    "  // one row out of step, which paints the hilltops with the valley's dirt and says nothing",
    "  // about which row it was.",
    "  const wgt = new Float32Array(verts * 4);",
    "  const idx = new Uint32Array((nx - 1) * (nx - 1) * 6);",
    "  const S = splat();",
    "  const C = [0, 1, 2, 3].map((i) => lin((LAYERS[i] || LAYERS[0]).colour));",
    "  const span = step * CELL;",
    "  for (let jj = 0; jj < nx; jj++) {",
    "    const j = line[jj];",
    "    for (let ii = 0; ii < nx; ii++) {",
    "      const i = line[ii], v = jj * nx + ii;",
    "      pos[v * 3] = OX + i * CELL;",
    "      pos[v * 3 + 1] = OY + at(i, j) * MAXH;",
    "      pos[v * 3 + 2] = OZ + j * CELL;",
    "      const gx = (at(i - step, j) - at(i + step, j)) * MAXH;",
    "      const gz = (at(i, j - step) - at(i, j + step)) * MAXH;",
    "      const k = Math.hypot(gx, 2 * span, gz) || 1;",
    "      nrm[v * 3] = gx / k; nrm[v * 3 + 1] = 2 * span / k; nrm[v * 3 + 2] = gz / k;",
    "      uv[v * 2] = i / (RES - 1); uv[v * 2 + 1] = j / (RES - 1);",
    "      const o = (j * RES + i) * 4;",
    "      const w0 = S[o] / 255, w1 = S[o + 1] / 255, w2 = S[o + 2] / 255, w3 = S[o + 3] / 255;",
    "      wgt[v * 4] = w0; wgt[v * 4 + 1] = w1; wgt[v * 4 + 2] = w2; wgt[v * 4 + 3] = w3;",
    "      col[v * 4] = C[0][0] * w0 + C[1][0] * w1 + C[2][0] * w2 + C[3][0] * w3;",
    "      col[v * 4 + 1] = C[0][1] * w0 + C[1][1] * w1 + C[2][1] * w2 + C[3][1] * w3;",
    "      col[v * 4 + 2] = C[0][2] * w0 + C[1][2] * w1 + C[2][2] * w2 + C[3][2] * w3;",
    "      col[v * 4 + 3] = 1;",
    "    }",
    "  }",
    "  let q = 0;",
    "  for (let jj = 0; jj + 1 < nx; jj++) {",
    "    for (let ii = 0; ii + 1 < nx; ii++) {",
    "      const a = jj * nx + ii, b = a + 1, c = a + nx, d = c + 1;",
    "      idx[q++] = a; idx[q++] = c; idx[q++] = b;",
    "      idx[q++] = b; idx[q++] = c; idx[q++] = d;",
    "    }",
    "  }",
    "  return { pos, nrm, uv, col, idx, w: wgt };",
    "}",
    "",
    "",
    "/** The raw 0..1 layer weights, four per vertex, in the SAME order arrays() lays out — one",
    " *  implementation of the vertex order, not two.",
    " *",
    " *  IN THE HEADER, NOT IN THE three BRANCH. It sat with the three code, and the PlayCanvas",
    " *  splat section calls it — so an emitted PlayCanvas file threw `<fn>Weights is not defined`",
    " *  the moment its ground was drawn. Both engines read `arrays()`; both get this. */",
    "export function " + fn + "Weights(lod) { return arrays(lod || 1).w; }",
  ];

  const three = [
    "/* -----------------------------------------------------------------------------------------",
    " * THE FOUR-LAYER SPLAT MATERIAL, for three.",
    " *",
    " * WHY IT IS HERE AND ON BY DEFAULT: the whole premise of this file is that ground leaves the",
    " * Studio as the game's own code. It shipped drawing one averaged colour a vertex — which is",
    " * all a stock material can do with a vertex colour — so the emitted file looked WORSE than",
    " * the editor it came out of, and only a game that knew to pass its own { material } could",
    " * fix it. Now the default is the four layers blended per pixel, each tiled in world space,",
    " * each free to carry a texture.",
    " *",
    " * It patches two of three's own chunks and nothing else, so the game's lights, shadows, fog",
    " * and tone mapping are untouched. `{ splat: false }` gives back the flat vertex colour, and",
    " * so does passing your own `{ material }`.",
    " *",
    " * THE ONE THING THAT WILL CATCH YOU: on the splat path the geometry's `color` attribute",
    " * carries the RAW WEIGHTS, not the blended colour, because the shader does the blend and",
    " * needs the four weights to do it. Hand those weights to a plain `vertexColors: true`",
    " * material and layer 0 paints red and layer 1 green. `{ splat: false }` swaps the attribute",
    " * back, and `group.userData.terrain.weighted` says which one is in there.",
    " * -------------------------------------------------------------------------------------- */",
    "",
    "function splatWhite(THREE) {",
    "  const t = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);",
    "  t.needsUpdate = true;",
    "  return t;",
    "}",
    "",
    "/** The material alone, for a game that builds its own mesh from `arrays()`.",
    " *  @param opts { textures: [t0, t1, t2, t3], roughness, tint } */",
    "export function " + fn + "Material(THREE, opts) {",
    "  const o = opts || {};",
    "  const mat = new THREE.MeshStandardMaterial({",
    "    vertexColors: true,",
    "    roughness: o.roughness === undefined ? 0.96 : o.roughness,",
    "    metalness: 0,",
    "  });",
    "  mat.name = 'terrain-splat';",
    "  const white = splatWhite(THREE);",
    "  const cols = [], has = [0, 0, 0, 0], tile = [8, 8, 8, 8], texs = [];",
    "  for (let i = 0; i < 4; i++) {",
    "    const l = LAYERS[i] || LAYERS[0] || {};",
    "    const c = lin(l.colour);",
    "    cols.push(new THREE.Vector3(c[0], c[1], c[2]));",
    "    const tex = (o.textures && o.textures[i]) || null;",
    "    texs.push(tex || white);",
    "    has[i] = tex ? 1 : 0;",
    "    tile[i] = l.tiling > 0 ? l.tiling : 8;",
    "  }",
    "  const u = {",
    "    uSplatCol: { value: cols },",
    "    uSplatHas: { value: new THREE.Vector4(has[0], has[1], has[2], has[3]) },",
    "    uSplatTile: { value: new THREE.Vector4(tile[0], tile[1], tile[2], tile[3]) },",
    "    uSplatTex0: { value: texs[0] }, uSplatTex1: { value: texs[1] },",
    "    uSplatTex2: { value: texs[2] }, uSplatTex3: { value: texs[3] },",
    "  };",
    "  mat.userData.splat = true;",
    "  mat.userData.splatUniforms = u;",
    "  mat.onBeforeCompile = function (shader) {",
    "    for (const k in u) shader.uniforms[k] = u[k];",
    "    // World xz, carried through myself: three only declares a world-position varying when",
    "    // something else in the material happens to need one, and tiling off vUv would restart",
    "    // the grain at every field's own corner instead of running continuously across them.",
    "    shader.vertexShader = 'varying vec3 vSplatW;\\n' + shader.vertexShader.replace(",
    "      '#include <begin_vertex>',",
    "      '#include <begin_vertex>\\n  vSplatW = (modelMatrix * vec4(transformed, 1.0)).xyz;');",
    "    shader.fragmentShader = SPLAT_HEAD + shader.fragmentShader.replace(",
    "      '#include <color_fragment>', SPLAT_BODY);",
    "  };",
    "  // Without this, three caches one compiled program for both a textured and an untextured",
    "  // terrain material and the second one silently gets the first one's branch.",
    "  mat.customProgramCacheKey = function () { return 'terrain-splat-' + has.join(''); };",
    "  mat.needsUpdate = true;",
    "  return mat;",
    "}",
    "",
    "const SPLAT_HEAD = [",
    "  'varying vec3 vSplatW;',",
    "  'uniform vec3 uSplatCol[4];',",
    "  'uniform vec4 uSplatHas;',",
    "  'uniform vec4 uSplatTile;',",
    "  'uniform sampler2D uSplatTex0;',",
    "  'uniform sampler2D uSplatTex1;',",
    "  'uniform sampler2D uSplatTex2;',",
    "  'uniform sampler2D uSplatTex3;',",
    "  ''",
    "].join('\\n');",
    "",
    "// A vertex whose weights were never written sums to zero, and dividing by that is a black",
    "// hole in the middle of the field. The floor makes it layer 0, which is what a fresh field",
    "// is everywhere else. The * 2.0 on a texture is the rule the editor's viewport uses: the",
    "// colour is already in `base`, so a texture only MODULATES it and a mid-grey one leaves the",
    "// ground exactly as bright as it was.",
    "const SPLAT_BODY = [",
    "  '  vec4 sw = vColor;',",
    "  '  float ss = sw.r + sw.g + sw.b + sw.a;',",
    "  '  sw = ss > 1e-4 ? sw / ss : vec4(1.0, 0.0, 0.0, 0.0);',",
    "  '  vec3 sbase = uSplatCol[0] * sw.r + uSplatCol[1] * sw.g + uSplatCol[2] * sw.b + uSplatCol[3] * sw.a;',",
    "  '  vec3 sgrain = vec3(1.0);',",
    "  '  if (uSplatHas.x + uSplatHas.y + uSplatHas.z + uSplatHas.w > 0.5) {',",
    "  '    sgrain = vec3(0.0);',",
    "  '    sgrain += (uSplatHas.x > 0.5 ? texture2D(uSplatTex0, vSplatW.xz / max(0.001, uSplatTile.x)).rgb * 2.0 : vec3(1.0)) * sw.r;',",
    "  '    sgrain += (uSplatHas.y > 0.5 ? texture2D(uSplatTex1, vSplatW.xz / max(0.001, uSplatTile.y)).rgb * 2.0 : vec3(1.0)) * sw.g;',",
    "  '    sgrain += (uSplatHas.z > 0.5 ? texture2D(uSplatTex2, vSplatW.xz / max(0.001, uSplatTile.z)).rgb * 2.0 : vec3(1.0)) * sw.b;',",
    "  '    sgrain += (uSplatHas.w > 0.5 ? texture2D(uSplatTex3, vSplatW.xz / max(0.001, uSplatTile.w)).rgb * 2.0 : vec3(1.0)) * sw.a;',",
    "  '  }',",
    "  '  diffuseColor.rgb *= sbase * sgrain;'",
    "].join('\\n');",
    "",
    "/** @param THREE the three namespace.",
    " *  @param opts { lod, place, material, castShadow, splat, textures, roughness } */",
    "export function " + fn + "(THREE, opts) {",
    "  const o = opts || {};",
    "  const a = arrays(o.lod || 1);",
    "  const splat = !o.material && o.splat !== false;",
    "  const g = new THREE.BufferGeometry();",
    "  g.setAttribute('position', new THREE.BufferAttribute(a.pos, 3));",
    "  g.setAttribute('normal', new THREE.BufferAttribute(a.nrm, 3));",
    "  g.setAttribute('uv', new THREE.BufferAttribute(a.uv, 2));",
    "  // WEIGHTS on the splat path, the blended colour otherwise. See the note above.",
    "  g.setAttribute('color', new THREE.BufferAttribute(splat ? a.w : a.col, 4));",
    "  g.setIndex(new THREE.BufferAttribute(a.idx, 1));",
    "  g.computeBoundingSphere();",
    "  const mat = o.material || (splat",
    "    ? " + fn + "Material(THREE, o)",
    "    : new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, metalness: 0 }));",
    "  const mesh = new THREE.Mesh(g, mat);",
    "  mesh.name = " + JSON.stringify(fn) + ";",
    "  mesh.receiveShadow = true;",
    "  // A quarter-million-vertex mesh in the shadow map costs more than the hills' own shadows",
    "  // are worth; ask for it with { castShadow: true } if the sun is low in your game.",
    "  mesh.castShadow = !!o.castShadow;",
    "  const group = new THREE.Group();",
    "  group.name = " + JSON.stringify(fn) + ";",
    "  group.add(mesh);",
    "  if (o.place) {",
    "    for (const s of scatter) {",
    "      const node = o.place(s);",
    "      if (!node) continue;",
    "      node.position.set(s.at[0], s.at[1], s.at[2]);",
    "      node.rotation.set(s.tilt[0], s.rot, s.tilt[1]);",
    "      node.scale.setScalar(s.scale);",
    "      group.add(node);",
    "    }",
    "  }",
    "  group.userData.terrain = { heightAt, normalAt, layers, scatter, res: RES, size: SIZE,",
    "                             weighted: splat };",
    "  return group;",
    "}",
  ];

  const pc = [
    "/** @param pc the playcanvas namespace. @param app the pc.Application.",
    " *  @param opts { lod, place, material, castShadow, splat, textures, roughness }",
    " *",
    " *  THE FOUR LAYERS ARE THE DEFAULT. When the splat section is appended to this file it",
    " *  defines <fn>Material, and this builder uses it without being asked — the ground a",
    " *  PlayCanvas game gets is the ground the editor showed, not one averaged colour a vertex.",
    " *  With no splat section, or with { splat: false }, or with your own { material }, it falls",
    " *  back to a StandardMaterial on the blended vertex colour exactly as before.",
    " *",
    " *  On the splat path the mesh's COLOUR attribute carries the raw weights, not the blend:",
    " *  PlayCanvas gives a shader one free four-component vertex attribute and the shader does",
    " *  the blend itself. Handing it the blend paints the whole field one layer. */",
    "export function " + fn + "(pc, app, opts) {",
    "  const o = opts || {};",
    "  const a = arrays(o.lod || 1);",
    "  const wantSplat = !o.material && o.splat !== false && typeof " + fn + "Material === 'function';",
    "  const splatMat = wantSplat ? " + fn + "Material(pc, app, o) : null;",
    "  const splat = !!(splatMat && splatMat.userData && splatMat.userData.splat);",
    "  const mesh = new pc.Mesh(app.graphicsDevice);",
    "  mesh.setPositions(a.pos);",
    "  mesh.setNormals(a.nrm);",
    "  mesh.setUvs(0, a.uv);",
    "  mesh.setColors(splat ? a.w : a.col, 4);",
    "  mesh.setIndices(a.idx);",
    "  mesh.update(pc.PRIMITIVE_TRIANGLES);",
    "  const mat = o.material || splatMat || new pc.StandardMaterial();",
    "  if (!o.material && !splatMat) {",
    "    mat.diffuseVertexColor = true;",
    "    mat.useMetalness = false;",
    "    mat.gloss = 0.05;",
    "    mat.update();",
    "  }",
    "  const node = new pc.GraphNode();",
    "  const mi = new pc.MeshInstance(mesh, mat, node);",
    "  mi.receiveShadow = true;",
    "  mi.castShadow = !!o.castShadow;",
    "  const entity = new pc.Entity(" + JSON.stringify(fn) + ");",
    "  entity.addComponent('render', { meshInstances: [mi] });",
    "  if (o.place) {",
    "    for (const s of scatter) {",
    "      const child = o.place(s);",
    "      if (!child) continue;",
    "      child.setLocalPosition(s.at[0], s.at[1], s.at[2]);",
    "      child.setLocalEulerAngles(s.tilt[0] * 180 / Math.PI, s.rot * 180 / Math.PI, s.tilt[1] * 180 / Math.PI);",
    "      child.setLocalScale(s.scale, s.scale, s.scale);",
    "      entity.addChild(child);",
    "    }",
    "  }",
    "  entity.terrain = { heightAt, normalAt, layers, scatter, res: RES, size: SIZE,",
    "                     weighted: splat };",
    "  return entity;",
    "}",
  ];

  // The header above promises `<name>.heightAt(x, z)`. It shipped as a named export only, so
  // anyone who followed the header got a TypeError on their first line. Both now work, and
  // hanging them off the builder is the friendlier half: one import, one name to remember.
  const tail = [
    "",
    fn + ".heightAt = heightAt;",
    fn + ".normalAt = normalAt;",
    fn + ".layers = layers;",
    fn + ".scatter = scatter;",
    "",
    "export default " + fn + ";",
    "",
  ];
  // THE PLAYCANVAS FILE IS FINISHED HERE, NOT AT THE CALL SITE.
  //
  // The splat section used to be appended by the editor's Emit button, so `emitBuilder` called
  // any other way — the HTTP `code` action, a test, a script — produced a file whose ground is
  // one averaged colour a vertex. One function, two outputs, decided by who asked. It cannot be
  // in both places: two `export function <fn>Material` in one file is a SyntaxError.
  //
  // The emitted `<fn>(pc, app)` already reaches for `<fn>Material` when it is there and falls
  // back cleanly when it is not, so this only ever adds; `{splat: false}` at run time restores
  // the flat material for a game that wants it.
  const body = head.concat(eng === "three" ? three : pc).concat(tail).join("\n");
  const wantSplat = opts?.splat !== false;
  return eng === "playcanvas" && wantSplat ? body + pcSplatSource(fn) : body;
}

/** Every `lod`-th sample, for an emit that does not need a sample per metre. Point sampling, not
 *  an average: an averaged field rounds off every ridge the erosion brush just cut. */
export function downsample(t: TerrainData, lod: number): TerrainData {
  const step = Math.max(1, Math.floor(lod));
  const s = t.spec;
  if (step === 1 || (s.res - 1) % step !== 0) return t;
  const res = (s.res - 1) / step + 1;
  const height = new Float32Array(res * res);
  const splat = new Uint8Array(res * res * 4);
  for (let j = 0; j < res; j++) {
    for (let i = 0; i < res; i++) {
      const src = (j * step) * s.res + i * step;
      height[j * res + i] = t.height[src];
      splat[(j * res + i) * 4] = t.splat[src * 4];
      splat[(j * res + i) * 4 + 1] = t.splat[src * 4 + 1];
      splat[(j * res + i) * 4 + 2] = t.splat[src * 4 + 2];
      splat[(j * res + i) * 4 + 3] = t.splat[src * 4 + 3];
    }
  }
  return {
    spec: { ...s, res, origin: [s.origin[0], s.origin[1], s.origin[2]] },
    height, splat,
    layers: t.layers.map((l) => ({ ...l })),
    scatter: t.scatter.slice(),
  };
}

/* ===========================================================================================
 * ROUND TWO — THE CONTRACT, NOW FILLED IN.
 *
 * The signatures below were written before the work, against three agents building at once: the
 * engine (here), the editor's Terrain mode, and the HTTP API an agent sculpts through.
 *
 * A LESSON FROM ROUND ONE, WHICH COST TWO OF THE THREE AGENTS A RENDER: the prose in a contract
 * is part of the contract. `MeshArrays.colors` said "the splat weights" when it holds the blended
 * colour, and two independent agents shipped a white field. So where the contract turned out to
 * be wrong, the comment is corrected here and the correction says what it used to say:
 *
 *   1. `FlowField.flow` was specified as 0..1. It is the RAW accumulation, and `flowField(t)`
 *      hands back the normalised view. Normalising the store means rescaling the whole field
 *      whenever the peak grows, and (x/m)*m is not x in float32 — a stroke `plan` rolled back
 *      could then not be undone exactly. See the note on the field itself.
 *   2. `Brush.depth` was "how deep the channel is cut". It is a RATE, world units per second,
 *      because decision 1 at the top of this file says every brush is one.
 *   3. `flowLayers`' channel layer defaulted to "the last one", which on a one-layer field is
 *      layer 0 — the same as the dry layer, so the whole call would have been a silent no-op on
 *      exactly the field a first-time caller has. It defaults to the palette's own slot instead.
 *   4. `Chunk.skirt`'s default of "one cell of the chunk's own lod" is not "exactly the worst
 *      crack a one-step lod difference can open" — the crack is set by how rough the ground is,
 *      not by how big a cell is. On a cliff a one-cell skirt leaves the sky showing through:
 *      measured on a 8-sample step terrain, lod 4 opens a 28.8-unit crack where one cell is 8.
 *      The default is now the measured deviation along the chunk's own edges, floored at one
 *      cell so flat ground still gets a rim.
 *   5. `FlowField.flow` was specified as "how much water crossed this sample". Counting water
 *      volume points at the WRONG GROUND, and the HTTP agent caught it with a number before
 *      anybody caught it in a picture. On four eroded fields the samples above twice the mean
 *      volume averaged `cut` -5.3 — ground LAID DOWN — and the ones below half the mean averaged
 *      +1.3, ground taken away. Droplets converge on the low flats and stop there, so a volume
 *      tally peaks exactly where the sediment settles, and `flowLayers`, `paint {flow:true}` and
 *      `river` were all pointed at the silt. It is stream power now — discharge times the drop,
 *      accumulated only on downhill steps — and the same measurement comes out +2.8 against
 *      +0.03. Two candidate causes were on the table and the other one, an inverted `cut`, was
 *      ruled out in a minute by checking `cut` against the height change: 4,629 of 4,629 samples
 *      agree with the doc and `cut + heightChange` is exactly 0. Both checks are in the suite.
 *   6. `flowLayers`' rule — "rock where the water stripped it, silt where the water dropped its
 *      load" — named two different measurements and then gave them one test to share. Ranking
 *      the wet ground of an eroded hill and reading the terrain under each band:
 *
 *          the driest 10%   height 39.8   slope 37.8 deg   0.84 ABOVE its neighbours   cut +0.60
 *          the middle       height 33.9   slope 14.2 deg   0.56 below                  cut +2.20
 *          the wettest 10%  height 32.6   slope  6.6 deg   level with them             cut -6.56
 *
 *      That ranking is by the OLD volume measure, and it is the evidence for correction 5 as
 *      well: the busiest ground was the low, flat, sediment-filled valley floor. Either way
 *      `cut` is the measurement that separates stripped from filled, and the rule uses it —
 *      flow decides what is a channel, `cut` decides what a channel is made of.
 * ========================================================================================= */

// ------------------------------------------------------------------ the flow map

/**
 * Where the water ran.
 *
 * The droplet pass already computes this and used to throw it away: every droplet knows the
 * samples it crossed and how much it carried. Keeping it is what separates ground that has been
 * eroded from ground that has been eroded AND looks like it — paint follows the drainage instead
 * of a slope threshold, dirt collects in the gullies instead of scattering, and a river has
 * somewhere to be.
 *
 * Why a slope threshold cannot do this: the slope on the side of a gully and the slope on a plain
 * hillside are the same number. Only the water knows which one is a gully.
 */
export interface FlowField {
  res: number;
  /**
   * res*res. HOW HARD THE WATER WORKED at this sample: discharge times the height it fell
   * through, summed over every downhill step of every droplet of every stroke, RAW. Divide by
   * `peak` for the 0..1 share, or read the whole field through `flowField(t)`, which hands back
   * these same numbers with `flow` already normalised.
   *
   * IT IS NOT A WATER-VOLUME COUNT, and the contract's "how much water crossed this sample" is
   * the thing that had to change. A volume count points at the wrong ground. Measured on four
   * eroded fields, samples above twice the mean volume averaged `cut` -5.3 — ground the water
   * LAID DOWN — while the ones below half the mean averaged +1.3, ground it took away. The
   * droplets converge on the low flats and stop there, so a volume tally peaks precisely where
   * the sediment settles, and every consumer of it was aimed at the silt: rock painted on the
   * deposition fans, a river carved along them. Weighted by the drop, the same measurement comes
   * out +2.8 against +0.03 and the busiest samples sit 0.78 units BELOW their neighbours.
   * Stream power is also the quantity that physically decides whether running water cuts, so
   * this is the honest measure as well as the useful one.
   *
   * THE CONTRACT ALSO SAID 0..1 AND IT COULD NOT BE. Storing it normalised means dividing the
   * whole field again every time a stroke raises the peak, and an undone stroke then cannot put
   * it back: (x/m)*m is not x in float32, so a candidate `plan` rejected would leave a trace in
   * the drainage even though the ground was bit-identical. Raw in the store, normalised in the
   * view, exact in both directions.
   *
   * 0 is ground no downhill water ever crossed, and `flowLayers` leaves that ground alone.
   */
  flow: Float32Array;
  /**
   * res*res, world units. POSITIVE WHERE THE PASS TOOK GROUND AWAY, NEGATIVE WHERE IT LAID
   * GROUND DOWN. Say it the other way round as well, because this is the comment that will be
   * read in a hurry: `cut[k]` is exactly the NEGATIVE of the height change at k in world units,
   * so a sample that fell by 2 units has `cut = +2` and a sample that rose by 2 has `cut = -2`.
   *
   * That is not an assumption, it is checked: erode a field, compare `cut` against the height
   * before and after sample by sample, and `cut + heightChange` is 0 at every sample the pass
   * touched — 4,629 of 4,629, no exceptions. The suite runs that check, because when the flow
   * map turned out to point at the silt this sign was one of the two possible causes and ruling
   * it out with a number took a minute where arguing about it would have taken an hour.
   *
   * It counts the droplets' pickup and deposit AND the talus slide, which is scree and belongs
   * with the sediment. Sums to about zero, because droplets conserve mass. Never normalised.
   */
  cut: Float32Array;
  /** Droplets summed into it, and how many strokes contributed. */
  droplets: number;
  strokes: number;
  /** The busiest sample's raw accumulation, before normalising — so two fields can be compared,
   *  and so `flowField()` knows what to divide by. */
  peak: number;
  /** Bumped by every stroke that writes here. `flowField()`'s normalised array is cached against
   *  it, so reading the flow every frame during a paint stroke costs one comparison. */
  rev: number;
  /** The sample rectangle that has ever received water, [x0, z0, x1, z1), half-open. Everything
   *  outside it is exactly zero, which is what makes a brush-sized erode cheap to save. */
  box: [number, number, number, number];
}

/** The normalised view, cached per field and rebuilt when `rev` moves. */
const flowCache = new WeakMap<FlowField, { rev: number; flow: Float32Array }>();

function staleFlow(f: FlowField): void {
  const c = flowCache.get(f);
  // -1, not delete: the array is kept for the next read to refill, so a held erode brush does
  // not allocate a field-sized Float32Array every frame someone looks at the drainage.
  if (c) c.rev = -1;
}

function ensureFlow(t: TerrainData): FlowField {
  const n = t.spec.res * t.spec.res;
  const cur = t.flow;
  if (cur && cur.res === t.spec.res && cur.flow.length === n && cur.cut.length === n) return cur;
  const f: FlowField = {
    res: t.spec.res,
    flow: new Float32Array(n),
    cut: new Float32Array(n),
    droplets: 0, strokes: 0, peak: 0, rev: 0,
    box: [0, 0, 0, 0],
  };
  t.flow = f;
  return f;
}

function growFlowBox(f: FlowField, r: Rect): void {
  if (!r.w || !r.h) return;
  if (f.box[2] <= f.box[0] || f.box[3] <= f.box[1]) {
    f.box = [r.x0, r.z0, r.x0 + r.w, r.z0 + r.h];
    return;
  }
  f.box = [
    Math.min(f.box[0], r.x0), Math.min(f.box[1], r.z0),
    Math.max(f.box[2], r.x0 + r.w), Math.max(f.box[3], r.z0 + r.h),
  ];
}

function grabFlow(t: TerrainData, f: FlowField, r: Rect, fresh: boolean): FlowPatch {
  const res = t.spec.res;
  const flow = new Float32Array(r.w * r.h);
  const cut = new Float32Array(r.w * r.h);
  for (let j = 0; j < r.h; j++) {
    const from = (r.z0 + j) * res + r.x0;
    flow.set(f.flow.subarray(from, from + r.w), j * r.w);
    cut.set(f.cut.subarray(from, from + r.w), j * r.w);
  }
  return {
    x0: r.x0, z0: r.z0, w: r.w, h: r.h, flow, cut,
    peak: f.peak, droplets: f.droplets, strokes: f.strokes, rev: f.rev,
    box: [f.box[0], f.box[1], f.box[2], f.box[3]],
    ...(fresh ? { fresh: true } : {}),
  };
}

function restoreFlow(t: TerrainData, fp: FlowPatch): void {
  if (fp.fresh) { t.flow = undefined; return; }
  const f = t.flow;
  if (!f || f.res !== t.spec.res) return;
  const res = t.spec.res;
  for (let j = 0; j < fp.h; j++) {
    const to = (fp.z0 + j) * res + fp.x0;
    f.flow.set(fp.flow.subarray(j * fp.w, j * fp.w + fp.w), to);
    f.cut.set(fp.cut.subarray(j * fp.w, j * fp.w + fp.w), to);
  }
  f.peak = fp.peak;
  f.droplets = fp.droplets;
  f.strokes = fp.strokes;
  f.box = [fp.box[0], fp.box[1], fp.box[2], fp.box[3]];
  // `rev` goes back to what it was, and the cache is marked stale separately. Bumping it instead
  // would be simpler and would make an undone stroke NOT bit-identical, which is the one thing
  // `plan` needs from this function.
  f.rev = fp.rev;
  staleFlow(f);
}

/**
 * The flow field, or null when nothing has been eroded yet.
 *
 * Cheap: it hands back the accumulation the strokes already built, and it does NOT run erosion.
 * The `flow` array is normalised 0..1 against `peak` and cached against `rev`, so reading it
 * repeatedly between strokes costs one comparison. `cut` is the store's own array — a caller
 * that writes into either of them is corrupting the record.
 */
export function flowField(t: TerrainData): FlowField | null {
  const f = t.flow;
  if (!f || !f.strokes || !(f.peak > 0)) return null;
  let c = flowCache.get(f);
  if (!c || c.rev !== f.rev) {
    const arr = c && c.flow.length === f.flow.length ? c.flow : new Float32Array(f.flow.length);
    const k = 1 / f.peak;
    for (let i = 0; i < arr.length; i++) arr[i] = f.flow[i] * k;
    c = { rev: f.rev, flow: arr };
    flowCache.set(f, c);
  }
  return { ...f, flow: c.flow };
}

/**
 * The 0..1 drainage share at a world x/z, bilinear, 0 where nothing has been eroded.
 *
 * A share of the busiest sample's STREAM POWER, not of a water volume — see `FlowField.flow`.
 * The one-line version of `flowField()`, for a caller asking "is this spot in a channel?" — a
 * game placing a bridge, or an agent checking one coordinate over HTTP.
 */
export function flowAt(t: TerrainData, x: number, z: number): number {
  const f = flowField(t);
  if (!f) return 0;
  const s = t.spec;
  const [fi, fj] = toSample(s, x, z);
  const i = clamp(Math.floor(fi), 0, s.res - 1);
  const j = clamp(Math.floor(fj), 0, s.res - 1);
  const cx = clamp(fi - i, 0, 1), cz = clamp(fj - j, 0, 1);
  const at = (a: number, b: number) => f.flow[clamp(b, 0, s.res - 1) * s.res + clamp(a, 0, s.res - 1)];
  const top = at(i, j) + (at(i + 1, j) - at(i, j)) * cx;
  const bot = at(i, j + 1) + (at(i + 1, j + 1) - at(i, j + 1)) * cx;
  return top + (bot - top) * cz;
}

/**
 * The flow value above which a sample counts as channel, for a `channel` of 0..1.
 *
 * A RANK, NOT A LEVEL, and that is a correction to the contract worth reading. `channel` was
 * specified as "the share of peak flow that counts as a channel", and with the volume tally it
 * replaced that was workable — barely. Stream power is heavy-tailed: on four eroded fields, "0.30
 * of the peak" selected between 0.4% and 4.6% of the ground, a factor of eleven, and after the
 * river's 3x3 blur one of those cases came out at 35 samples out of 31,000 under the brush. One
 * number cannot be a level on a distribution that moves like that.
 *
 * So `channel` chooses a SHARE OF THE WET GROUND IN VIEW instead: `0.10 * 0.1^channel`. That is
 *
 *     channel 0    -> the wettest tenth of the ground the water crossed
 *     channel 0.30 -> the wettest 5.0%   (the default: a channel network)
 *     channel 0.55 -> the wettest 2.8%   (the main stems)
 *     channel 1    -> the wettest 1%     (the trunk alone)
 *
 * The direction the contract promised is intact — lower carves more of the field, higher keeps to
 * the main stems — and the number now means the same thing on every terrain.
 *
 * A log histogram rather than a sort: 2048 buckets and one pass, so a whole 513 field costs a
 * third of a millisecond instead of the thirty a sort of 263,000 floats costs, and a held paint
 * brush can afford to ask every frame.
 */
function channelLevel(flow: Float32Array, res: number, reg: Rect, channel: number): number {
  const share = 0.10 * Math.pow(0.1, clamp01(channel));
  const BUCKETS = 2048;
  const hist = new Int32Array(BUCKETS);
  let wet = 0;
  // log10 from 1e-6 up to 1, which is six decades — below that a sample is noise from a droplet
  // that clipped the corner of the cell.
  const bucketOf = (v: number) => {
    const b = Math.floor((Math.log10(v) + 6) / 6 * (BUCKETS - 1));
    return b < 0 ? 0 : b >= BUCKETS ? BUCKETS - 1 : b;
  };
  for (let j = 0; j < reg.h; j++) {
    const row = (reg.z0 + j) * res + reg.x0;
    for (let i = 0; i < reg.w; i++) {
      const v = flow[row + i];
      if (v > 1e-6) { hist[bucketOf(v)]++; wet++; }
    }
  }
  if (!wet) return Infinity;
  const want = Math.max(1, Math.round(wet * share));
  let seen = 0;
  for (let b = BUCKETS - 1; b >= 0; b--) {
    seen += hist[b];
    if (seen >= want) return Math.pow(10, (b / (BUCKETS - 1)) * 6 - 6);
  }
  return 0;
}

/** Which of the four layers is the rock, the silt and the dry one, by name where a name says so. */
function nameLike(t: TerrainData, words: string[]): number {
  for (let i = 0; i < t.layers.length && i < 4; i++) {
    const n = (t.layers[i]?.name || "").toLowerCase();
    for (const w of words) if (n.includes(w)) return i;
  }
  return -1;
}

interface FlowChoice { channel: number; channelLayer: number; siltLayer: number; dryLayer: number }

/**
 * The three layers `flowLayers` will write, resolved once.
 *
 * THE CONTRACT SAID the channel layer falls back to "the last one". On the one-layer field a
 * first-time caller has, the last one is layer 0 — which is also the dry layer, so every sample
 * would be painted the colour it already was and the whole feature would look like it did
 * nothing. The fallback is the PALETTE's own slot instead: 2 is rock, 1 is dirt, 0 is ground, and
 * `paint` fills a missing slot in with that palette entry (see `ensureLayer`). With three or more
 * layers already named, "the last one" is back to being the sensible answer.
 */
function flowLayerChoice(t: TerrainData, opts?: {
  channel?: number; channelLayer?: number; siltLayer?: number; dryLayer?: number;
}): FlowChoice {
  const rock = nameLike(t, ["rock", "stone", "cliff", "scree", "granite", "slate"]);
  const silt = nameLike(t, ["dirt", "silt", "mud", "sand", "soil", "clay", "gravel"]);
  const dry = nameLike(t, ["grass", "ground", "moss", "turf", "meadow"]);
  return {
    channel: clamp01(opts?.channel ?? 0.30),
    channelLayer: clamp(Math.round(opts?.channelLayer ?? (rock >= 0 ? rock : Math.max(2, t.layers.length - 1))), 0, 3),
    siltLayer: clamp(Math.round(opts?.siltLayer ?? (silt >= 0 ? silt : 1)), 0, 3),
    dryLayer: clamp(Math.round(opts?.dryLayer ?? (dry >= 0 ? dry : 0)), 0, 3),
  };
}

/**
 * Which layer index the drainage says each sample should be, or -1 for "leave it alone".
 *
 * The rule terrain painting actually wants and almost never gets: rock where the water stripped
 * the ground, silt where it dropped its load, grass between. `paint` with `flow: true` uses this;
 * it is exported so a caller can see the decision without painting.
 *
 * TWO SIGNALS, NOT ONE, and that is a correction to the contract — see #6 in the banner above.
 * `cut` says what happened to the ground (positive: taken away; negative: laid down) and `flow`
 * says how hard the water worked. Both are needed. With the flow map's first, water-volume
 * definition the busiest samples were the LOW, FLAT valley floors where the sediment settled, so
 * a flow threshold on its own painted the valleys as rock and the gully walls as grass; that
 * definition is gone (see #5) but the lesson stands, because "the water was busy here" and "the
 * water took ground away here" are still not the same sentence. Rendered side by side against a
 * shaded relief, the two-signal rule puts rock along the branching gully network and silt in the
 * basins between; the one-signal rule put a grey blob in the middle of the brush.
 *
 * -1 is not a failure. Ground no droplet ever crossed has no opinion attached to it, and
 * overwriting it would turn "paint by drainage" into "repaint the world", which is the version of
 * this feature that people turn off.
 */
export function flowLayers(t: TerrainData, opts?: {
  /** How much of the drainage is channel, 0..1: the wettest `0.10 * 0.1^channel` of the ground
   *  the water crossed inside `region`. Default 0.30, which is the wettest 5%. A rank rather
   *  than a level — `channelLevel` has the measurement that forced that. */
  channel?: number;
  /** Layer written in a channel. Default: the layer named like rock/stone, else palette slot 2
   *  (or the last layer, once there are three or more). */
  channelLayer?: number;
  /** Layer written where sediment settled. Default: the layer named like dirt/sand/silt, else 1. */
  siltLayer?: number;
  /** Layer written on the dry flats. Default: the layer named like grass/ground, else 0. */
  dryLayer?: number;
  /** How much of the biggest strip in view counts as stripped ground, 0..1. Default 0.05. Raise
   *  it and only the hardest-scoured walls come out as rock; drop it to 0 and every sample the
   *  water took anything from does.
   *
   *  This and `channel` are different questions and both are needed: `channel` asks how busy the
   *  water was, `strip` asks what it did to the ground. On eroded ground the two agree about most
   *  samples, so `strip` is the one that moves the rock share and `channel` decides the shape of
   *  the network inside it. */
  strip?: number;
  /** The same, for ground the water FILLED. Default 0.05 of the deepest deposit in view. */
  silt?: number;
  /** ADDED: only decide inside this sample rectangle. The array is still the whole field, so the
   *  index arithmetic matches the splat's; outside the rectangle it is -1. A held `paint` brush
   *  uses it so a 32-unit stroke does not think about a 513² field sixty times a second. */
  region?: { x0: number; z0: number; w: number; h: number };
}): Int8Array {
  const res = t.spec.res;
  const out = new Int8Array(res * res).fill(-1);
  const f = flowField(t);
  if (!f) return out;

  const pick = flowLayerChoice(t, opts);
  const reg = opts?.region
    ? clampRect(res, opts.region.x0, opts.region.z0, opts.region.w, opts.region.h)
    : { x0: 0, z0: 0, w: res, h: res };
  if (!reg.w || !reg.h) return out;

  // Both thresholds are a share of the biggest move in view, never an absolute depth: a gentle
  // stroke on soft ground and a second of full-strength erosion move amounts three orders apart,
  // and a fixed number would call one of them "no sediment anywhere".
  let deepest = 0, highest = 0;
  for (let j = 0; j < reg.h; j++) {
    const row = (reg.z0 + j) * res + reg.x0;
    for (let i = 0; i < reg.w; i++) {
      const c = f.cut[row + i];
      if (-c > deepest) deepest = -c;
      if (c > highest) highest = c;
    }
  }
  const siltAt = deepest * clamp01(opts?.silt ?? 0.05);
  const stripAt = highest * clamp01(opts?.strip ?? 0.05);
  const level = channelLevel(f.flow, res, reg, pick.channel);

  for (let j = 0; j < reg.h; j++) {
    const row = (reg.z0 + j) * res + reg.x0;
    for (let i = 0; i < reg.w; i++) {
      const k = row + i;
      const fl = f.flow[k];
      const ct = f.cut[k];
      if (fl <= 0 && ct === 0) continue;
      const laid = siltAt > 0 && -ct >= siltAt;
      out[k] = fl >= level ? (laid ? pick.siltLayer : pick.channelLayer)
        : laid ? pick.siltLayer
        : (stripAt > 0 && ct >= stripAt) ? pick.channelLayer
        : pick.dryLayer;
    }
  }
  return out;
}

/**
 * `river` — cut a channel along the drainage the erosion already found.
 *
 * A rate, like every other brush: `depth` world units a second at the busiest sample. The mask is
 * the flow BLURRED over 3x3 first, so the banks get a share of the cut and the result is a valley
 * rather than a one-sample slot the camera cannot see into.
 *
 * With no flow field it does nothing and says so. That is the whole reason `stats.note` exists.
 */
function riverStroke(t: TerrainData, b: Brush, x: number, z: number, dt: number,
                     radius: number, fo: number, strength: number, t0: number): Patch {
  const f = flowField(t);
  if (!f) {
    return {
      x0: 0, z0: 0, w: 0, h: 0,
      stats: {
        kind: "river", ms: now() - t0, samples: 0, moved: 0, channel: 0,
        note: "river: this terrain has no flow field, so there is no drainage to cut along and"
          + " nothing was changed. `river` follows the water the droplets already found — run an"
          + " `erode` stroke over this ground first, then come back.",
      },
    };
  }

  const s = t.spec;
  const res = s.res;
  const cell = cellSize(s);
  const r = discRect(t, x, z, radius, 1);
  const p: Patch = { x0: r.x0, z0: r.z0, w: r.w, h: r.h };
  if (!r.w || !r.h) { p.stats = { kind: "river", ms: now() - t0, samples: 0, channel: 0 }; return p; }

  const before = grabHeight(t, r);
  p.height = before;
  const [ci, cj] = toSample(s, x, z);
  const rs = radius / cell;
  const ch = clamp01(b.channel ?? 0.30);
  const depth = Math.max(0, b.depth ?? 1.5);
  const H = t.height;

  // TWO PASSES, and the reason is the shape of the flow map. Stream power is spiky: one sample
  // somewhere in the field can be twenty times the busiest channel under this brush, and a depth
  // ramped against that global peak cuts a pinprick and calls it a river. The first pass finds
  // the busiest channel HERE; the second ramps against it, so `depth` means what it says at the
  // deepest point of the watercourse you are actually pointing at.
  const blurred = new Float32Array(r.w * r.h);
  const wt = new Float32Array(r.w * r.h);
  let hit = 0;
  let peakSeen = 0;
  let moved = 0;
  for (let j = 0; j < r.h; j++) {
    const sj = r.z0 + j;
    const dz = sj - cj;
    for (let i = 0; i < r.w; i++) {
      const si = r.x0 + i;
      const w = weightAt(Math.hypot(si - ci, dz), rs, fo);
      if (w <= 0) continue;
      // 3x3 mean of the normalised flow. One sample of channel is a slot; three is a valley.
      let sum = 0, cnt = 0;
      for (let oj = -1; oj <= 1; oj++) {
        const qj = sj + oj;
        if (qj < 0 || qj >= res) continue;
        for (let oi = -1; oi <= 1; oi++) {
          const qi = si + oi;
          if (qi < 0 || qi >= res) continue;
          sum += f.flow[qj * res + qi];
          cnt++;
        }
      }
      const fl = cnt ? sum / cnt : 0;
      blurred[j * r.w + i] = fl;
      wt[j * r.w + i] = w;
      if (fl > peakSeen) peakSeen = fl;
    }
  }

  // The rank, taken over the BLURRED values under this brush — the same numbers the cut is
  // decided by, so the share asked for is the share cut. Taking it over the raw field instead
  // let the blur push a whole watercourse under the line.
  let level = Infinity;
  {
    const BUCKETS = 2048;
    const hist = new Int32Array(BUCKETS);
    let wet = 0;
    for (let o = 0; o < blurred.length; o++) {
      const v = blurred[o];
      if (wt[o] > 0 && v > 1e-6) {
        const bb = Math.floor((Math.log10(v) + 6) / 6 * (BUCKETS - 1));
        hist[bb < 0 ? 0 : bb >= BUCKETS ? BUCKETS - 1 : bb]++;
        wet++;
      }
    }
    if (wet) {
      const want = Math.max(1, Math.round(wet * 0.10 * Math.pow(0.1, ch)));
      let seen = 0;
      for (let b = BUCKETS - 1; b >= 0; b--) {
        seen += hist[b];
        if (seen >= want) { level = Math.pow(10, (b / (BUCKETS - 1)) * 6 - 6); break; }
      }
    }
  }
  hit = 0;
  for (let o = 0; o < blurred.length; o++) if (wt[o] > 0 && blurred[o] >= level) hit++;
  const span = Math.max(1e-9, peakSeen - level);
  for (let j = 0; j < r.h; j++) {
    for (let i = 0; i < r.w; i++) {
      const o = j * r.w + i;
      const w = wt[o];
      if (w <= 0) continue;
      const fl = blurred[o];
      if (fl < level) continue;
      // Square-rooted, so the banks of a channel get a real share of the cut instead of a knife
      // edge at the threshold and nothing either side of it.
      const ramp = Math.sqrt(clamp01((fl - level) / span));
      const drop = depth * ramp * w * strength * dt / s.maxHeight;
      const k = (r.z0 + j) * res + r.x0 + i;
      const nh = clamp01(H[k] - drop);
      moved += Math.abs(nh - H[k]);
      H[k] = nh;
    }
  }

  const stats: PatchStats = {
    kind: "river", ms: now() - t0, samples: r.w * r.h, moved: moved * s.maxHeight, channel: hit,
  };
  if (!hit) {
    stats.note = "river: no water has run under this brush, so there was no channel to cut."
      + " `channel` picks the wettest " + (0.10 * Math.pow(0.1, ch) * 100).toFixed(1)
      + "% of the ground the water crossed HERE, and none of it is under the brush at all —"
      + " erode this spot first, or move the brush onto ground that has been eroded.";
  }
  p.stats = stats;
  return p;
}

/** Base64 of the 16-bit flow map, cropped to the box that has ever received water. */
function packFlow(f: FlowField | undefined): unknown {
  if (!f || !f.strokes || !(f.peak > 0)) return undefined;
  const x0 = f.box[0], z0 = f.box[1];
  const w = Math.max(0, f.box[2] - x0), h = Math.max(0, f.box[3] - z0);
  if (!w || !h) return undefined;
  const res = f.res;
  let cutScale = 0;
  for (let j = 0; j < h; j++) {
    const row = (z0 + j) * res + x0;
    for (let i = 0; i < w; i++) { const a = Math.abs(f.cut[row + i]); if (a > cutScale) cutScale = a; }
  }
  const fb = new Uint8Array(w * h * 2);
  const cb = new Uint8Array(w * h * 2);
  const kf = 65535 / f.peak;
  const kc = cutScale > 0 ? 32767 / cutScale : 0;
  for (let j = 0; j < h; j++) {
    const row = (z0 + j) * res + x0;
    for (let i = 0; i < w; i++) {
      const o = (j * w + i) * 2;
      const v = clamp(Math.round(f.flow[row + i] * kf), 0, 65535);
      fb[o] = v & 255; fb[o + 1] = (v >> 8) & 255;
      // Signed, biased by 32768 so the byte pair is unsigned on the way out and the sign of a
      // deposit survives a language with no int16 literal.
      const c = clamp(Math.round(f.cut[row + i] * kc) + 32768, 0, 65535);
      cb[o] = c & 255; cb[o + 1] = (c >> 8) & 255;
    }
  }
  return {
    x0, z0, w, h, peak: f.peak, cutScale,
    droplets: f.droplets, strokes: f.strokes,
    f: b64encode(fb), c: b64encode(cb),
  };
}

function unpackFlow(j: any, res: number): FlowField | undefined {
  if (!j || typeof j !== "object") return undefined;
  const x0 = Math.max(0, Math.round(Number(j.x0) || 0));
  const z0 = Math.max(0, Math.round(Number(j.z0) || 0));
  const w = Math.max(0, Math.round(Number(j.w) || 0));
  const h = Math.max(0, Math.round(Number(j.h) || 0));
  const peak = Number(j.peak) || 0;
  if (!w || !h || !(peak > 0) || x0 + w > res || z0 + h > res) return undefined;
  const fb = b64decode(String(j.f || ""));
  const cb = b64decode(String(j.c || ""));
  if (fb.length < w * h * 2) return undefined;
  const n = res * res;
  const f: FlowField = {
    res, flow: new Float32Array(n), cut: new Float32Array(n),
    droplets: Math.max(0, Math.round(Number(j.droplets) || 0)),
    strokes: Math.max(1, Math.round(Number(j.strokes) || 1)),
    peak, rev: 0, box: [x0, z0, x0 + w, z0 + h],
  };
  const cutScale = Number(j.cutScale) || 0;
  const kf = peak / 65535;
  const kc = cutScale / 32767;
  for (let jj = 0; jj < h; jj++) {
    const row = (z0 + jj) * res + x0;
    for (let i = 0; i < w; i++) {
      const o = (jj * w + i) * 2;
      f.flow[row + i] = (fb[o] | (fb[o + 1] << 8)) * kf;
      if (cb.length >= w * h * 2 && kc > 0) f.cut[row + i] = ((cb[o] | (cb[o + 1] << 8)) - 32768) * kc;
    }
  }
  return f;
}

/* ------------------------------------------------------------------------------------------
 * CHUNKS.
 *
 * `terrainMesh` takes a `lod` and the viewport ignored it, because one mesh cannot have two
 * levels of detail and nothing was cutting the field up. A 1025 square field is 2.1 M triangles
 * in one draw call that no frustum can reject. A chunk is a square of samples with its own mesh,
 * its own bounding sphere, and a SKIRT — a rim of downward geometry that hides the crack where a
 * coarse chunk meets a fine one, which is cheaper and steadier than stitching the edge samples.
 * ---------------------------------------------------------------------------------------- */

/** A chunk without its mesh: enough to cull it, pick its detail, and decide to build it. */
export interface ChunkRef {
  /** Grid position. cx runs west to east, cz south to north. */
  cx: number;
  cz: number;
  /** The sample rectangle it covers, INCLUSIVE of the row and column it shares with the chunk
   *  next door — two neighbours must evaluate the same height on the seam or the ground splits. */
  region: { x0: number; z0: number; w: number; h: number };
  /** World-space centre of its bounding sphere, and the radius. Y comes from the real heights in
   *  the region, not from the field's overall range, or every sphere is as tall as the tallest
   *  mountain and the cull rejects nothing. */
  centre: [number, number, number];
  radius: number;
  /** World-space axis-aligned box, min then max. */
  min: [number, number, number];
  max: [number, number, number];
}

export interface Chunk extends ChunkRef {
  /** 1 = every sample. Always a power of two, and never so coarse the region loses its last row —
   *  `lodLine` forces the last sample in whatever the step is, so the seam sample always exists. */
  lod: number;
  /** World units the skirt hangs down. 0 means none was built. The bounds above INCLUDE it, so a
   *  frustum cull cannot clip a chunk whose skirt is the visible part. */
  skirt: number;
  mesh: MeshArrays;
}

const CHUNK = 64;

function chunkSide(res: number, chunk: number): number {
  return Math.max(1, Math.ceil((res - 1) / Math.max(1, chunk)));
}

function chunkRegion(res: number, cx: number, cz: number, chunk: number): Rect {
  const x0 = clamp(cx * chunk, 0, Math.max(0, res - 1));
  const z0 = clamp(cz * chunk, 0, Math.max(0, res - 1));
  // +1 sample: the last column of this chunk IS the first column of the next one, evaluated from
  // the same field entry, so the two meshes put a vertex at exactly the same height on the seam.
  return { x0, z0, w: Math.min(res, x0 + chunk + 1) - x0, h: Math.min(res, z0 + chunk + 1) - z0 };
}

function chunkRef(t: TerrainData, cx: number, cz: number, chunk: number): ChunkRef {
  const s = t.spec;
  const res = s.res;
  const cell = cellSize(s);
  const r = chunkRegion(res, cx, cz, chunk);
  let lo = Infinity, hi = -Infinity;
  for (let j = 0; j < r.h; j++) {
    const row = (r.z0 + j) * res + r.x0;
    for (let i = 0; i < r.w; i++) {
      const v = t.height[row + i];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!(lo <= hi)) { lo = 0; hi = 0; }
  const min: [number, number, number] = [
    s.origin[0] + r.x0 * cell, s.origin[1] + lo * s.maxHeight, s.origin[2] + r.z0 * cell];
  const max: [number, number, number] = [
    s.origin[0] + (r.x0 + r.w - 1) * cell, s.origin[1] + hi * s.maxHeight,
    s.origin[2] + (r.z0 + r.h - 1) * cell];
  return { cx, cz, region: r, ...sphereOf(min, max) };
}

function sphereOf(min: [number, number, number], max: [number, number, number]): {
  centre: [number, number, number]; radius: number; min: [number, number, number]; max: [number, number, number];
} {
  const centre: [number, number, number] = [
    (min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  const radius = Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2;
  return { centre, radius, min, max };
}

/** Every chunk of the field, cheap — no meshes are built. `chunk` is samples per side, default 64. */
export function chunkGrid(t: TerrainData, chunk?: number): ChunkRef[] {
  const c = Math.max(1, Math.floor(chunk ?? CHUNK));
  const n = chunkSide(t.spec.res, c);
  const out: ChunkRef[] = [];
  for (let cz = 0; cz < n; cz++) for (let cx = 0; cx < n; cx++) out.push(chunkRef(t, cx, cz, c));
  return out;
}

/**
 * The chunks a changed rectangle of samples dirties, including the neighbours across a seam.
 *
 * Two reasons a stroke reaches further than its own rectangle, and both are in here:
 *   - the regions OVERLAP by one sample, so a stroke that touched the shared column belongs to
 *     both chunks and both have to be rebuilt or the seam tears;
 *   - normals are differenced against the neighbour, so a height changed at sample 63 moves the
 *     normal at 64 as well. The rectangle is grown by one sample before the overlap test for
 *     exactly that. A chunk drawn at lod L differences over L samples and wants L — pass a
 *     rectangle already grown by L if you draw coarse chunks next to a live brush.
 */
export function chunksFor(t: TerrainData, x0: number, z0: number, w: number, h: number,
                          chunk?: number): ChunkRef[] {
  const res = t.spec.res;
  const c = Math.max(1, Math.floor(chunk ?? CHUNK));
  const n = chunkSide(res, c);
  const r = clampRect(res, x0, z0, w, h);
  if (!r.w || !r.h) return [];
  const ax = Math.max(0, r.x0 - 1), az = Math.max(0, r.z0 - 1);
  const bx = Math.min(res - 1, r.x0 + r.w), bz = Math.min(res - 1, r.z0 + r.h);
  const lo = (v: number) => clamp(Math.ceil(v / c) - 1, 0, n - 1);
  const hi = (v: number) => clamp(Math.floor(v / c), 0, n - 1);
  const out: ChunkRef[] = [];
  for (let cz = lo(az); cz <= hi(bz); cz++) {
    for (let cx = lo(ax); cx <= hi(bx); cx++) out.push(chunkRef(t, cx, cz, c));
  }
  return out;
}

/**
 * The worst gap a lod-`lod` edge on this chunk can open against a full-detail neighbour: how far
 * the real ground strays from the straight line the coarse mesh draws along the same edge.
 *
 * THIS IS WHAT THE SKIRT HAS TO COVER, and it is why the contract's "one cell of the chunk's own
 * lod" was not it. The crack is set by how rough the ground is, not by how wide a cell is: on a
 * 64-unit cliff at lod 4 the deviation measured here is tens of units and a one-cell skirt shows
 * the sky through the join.
 */
function edgeDeviation(t: TerrainData, r: Rect, lod: number): number {
  if (lod <= 1) return 0;
  const s = t.spec;
  const res = s.res;
  const H = t.height;
  let worst = 0;
  const scan = (list: number[], fixed: number, horiz: boolean) => {
    for (let k = 0; k + 1 < list.length; k++) {
      const a = list[k], b = list[k + 1];
      const ha = horiz ? H[fixed * res + a] : H[a * res + fixed];
      const hb = horiz ? H[fixed * res + b] : H[b * res + fixed];
      for (let v = a + 1; v < b; v++) {
        const lin = ha + (hb - ha) * ((v - a) / (b - a));
        const real = horiz ? H[fixed * res + v] : H[v * res + fixed];
        const d = Math.abs(real - lin);
        if (d > worst) worst = d;
      }
    }
  };
  const cols = lodLine(r.x0, r.w, lod);
  const rows = lodLine(r.z0, r.h, lod);
  scan(cols, r.z0, true);
  scan(cols, r.z0 + r.h - 1, true);
  scan(rows, r.x0, false);
  scan(rows, r.x0 + r.w - 1, false);
  return worst * s.maxHeight;
}

/**
 * A rim of downward geometry round the outside of a chunk mesh.
 *
 * The ring is walked so that the interior is always on the same side, which is what lets one
 * winding rule serve all four edges: south west-to-east, east south-to-north, north east-to-west,
 * west north-to-south, and the last vertex closes back onto the first. The skirt vertices copy
 * the rim's NORMAL rather than facing outward — a vertical wall lit as though it were the ground
 * beside it is invisible through a crack, which is the entire job.
 */
function withSkirt(m: MeshArrays, nx: number, nz: number, drop: number): MeshArrays {
  if (!(drop > 0) || nx < 2 || nz < 2) return m;
  const ring: number[] = [];
  for (let i = 0; i < nx; i++) ring.push(i);
  for (let j = 1; j < nz; j++) ring.push(j * nx + nx - 1);
  for (let i = nx - 2; i >= 0; i--) ring.push((nz - 1) * nx + i);
  for (let j = nz - 2; j >= 1; j--) ring.push(j * nx);

  const V = nx * nz;
  const P = ring.length;
  const positions = new Float32Array((V + P) * 3);
  const normals = new Float32Array((V + P) * 3);
  const uvs = new Float32Array((V + P) * 2);
  const colors = new Float32Array((V + P) * 4);
  const weights = new Float32Array((V + P) * 4);
  positions.set(m.positions);
  normals.set(m.normals);
  uvs.set(m.uvs);
  colors.set(m.colors);
  if (m.weights) weights.set(m.weights);

  for (let k = 0; k < P; k++) {
    const a = ring[k], b = V + k;
    positions[b * 3] = m.positions[a * 3];
    positions[b * 3 + 1] = m.positions[a * 3 + 1] - drop;
    positions[b * 3 + 2] = m.positions[a * 3 + 2];
    normals[b * 3] = m.normals[a * 3];
    normals[b * 3 + 1] = m.normals[a * 3 + 1];
    normals[b * 3 + 2] = m.normals[a * 3 + 2];
    uvs[b * 2] = m.uvs[a * 2];
    uvs[b * 2 + 1] = m.uvs[a * 2 + 1];
    for (let c = 0; c < 4; c++) {
      colors[b * 4 + c] = m.colors[a * 4 + c];
      if (m.weights) weights[b * 4 + c] = m.weights[a * 4 + c];
    }
  }

  const indices = new Uint32Array(m.indices.length + P * 6);
  indices.set(m.indices);
  let q = m.indices.length;
  for (let k = 0; k < P; k++) {
    const k2 = (k + 1) % P;
    const a = ring[k], b = ring[k2], a2 = V + k, b2 = V + k2;
    indices[q++] = a; indices[q++] = b; indices[q++] = a2;
    indices[q++] = b; indices[q++] = b2; indices[q++] = a2;
  }
  return { positions, normals, uvs, colors, indices, weights };
}

/** One chunk's mesh. Normals still come from the WHOLE field — see decision 4 above. */
export function terrainChunk(t: TerrainData, cx: number, cz: number, opts?: {
  chunk?: number;
  /** Force a level. Omit and it stays at 1; `pickLod` is the thing that chooses. */
  lod?: number;
  /** World units of skirt. Default: the deepest the ground strays from this chunk's own coarse
   *  edges, floored at one cell of its lod so flat ground still gets a rim. 0 turns it off. */
  skirt?: number;
}): Chunk {
  const s = t.spec;
  const c = Math.max(1, Math.floor(opts?.chunk ?? CHUNK));
  const ref = chunkRef(t, cx, cz, c);
  const r = ref.region;
  const cells = Math.max(1, Math.min(r.w, r.h) - 1);
  let lod = Math.max(1, Math.floor(opts?.lod ?? 1));
  // Down to a power of two, and never past the chunk's own cell count: a step wider than the
  // chunk is two vertices a side whatever number you write, and calling that "lod 64" when it is
  // really lod 32 makes a neighbour's one-step-finer test wrong.
  lod = 1 << Math.floor(Math.log2(clamp(lod, 1, cells)));

  const mesh0 = terrainMesh(t, { lod, region: r });
  const cell = cellSize(s);
  const skirt = opts?.skirt !== undefined
    ? Math.max(0, opts.skirt)
    : Math.max(lod * cell, edgeDeviation(t, r, lod));
  const nx = lodLine(r.x0, r.w, lod).length;
  const nz = lodLine(r.z0, r.h, lod).length;
  const mesh = skirt > 0 ? withSkirt(mesh0, nx, nz, skirt) : mesh0;

  const min: [number, number, number] = [ref.min[0], ref.min[1] - skirt, ref.min[2]];
  return { cx, cz, region: r, lod, skirt, mesh, ...sphereOf(min, ref.max) };
}

/**
 * The detail level for a chunk seen from `eye`, as a power of two.
 *
 * Distance in radii, not in world units, so the same rule holds for a 64-unit field and a 4096:
 * inside `near` radii the chunk is at full detail, and the step doubles with every doubling of
 * the distance after that.
 */
export function pickLod(ref: ChunkRef, eye: [number, number, number], opts?: {
  /** Within this many radii, full detail. Default 3. */
  near?: number;
  /** Coarsest level allowed. Default 8. */
  max?: number;
}): number {
  const near = Math.max(1e-6, opts?.near ?? 3);
  const cap = 1 << Math.max(0, Math.floor(Math.log2(Math.max(1, Math.floor(opts?.max ?? 8)))));
  const radius = ref.radius > 1e-9 ? ref.radius : 1e-9;
  const d = Math.hypot(eye[0] - ref.centre[0], eye[1] - ref.centre[1], eye[2] - ref.centre[2]);
  const k = d / (radius * near);
  if (!(k > 1)) return 1;
  let lod = 1;
  while (lod * 2 <= cap && k >= lod * 2) lod *= 2;
  return lod;
}

/* ------------------------------------------------------------------------------------------
 * SCATTER, AT THE SIZE A REAL FOREST IS.
 *
 * One clone per tree caps out around 3,000 and erase was a linear scan of every item on the
 * field. Both are fixed by the same thing: the items sorted by asset, with a grid over them.
 * ---------------------------------------------------------------------------------------- */

export interface ScatterBatch {
  asset: string;
  count: number;
  /** count * 16, column-major 4x4 world matrices — the layout InstancedMesh.setMatrixAt and
   *  PlayCanvas's instancing vertex buffer both take, so neither has to be rebuilt per renderer.
   *  Composed T * Rx(tilt[0]) * Ry(rot) * Rz(tilt[1]) * S(scale), which is what three's default
   *  Euler order 'XYZ' does — the same pose `emitBuilder` writes for a cloned node, so switching
   *  a scene from clones to instances does not move a single tree. */
  matrices: Float32Array;
  /** count entries: where each instance sits in `t.scatter`, so a click on instance 7 can name
   *  the item it is. */
  index: Int32Array;
}

/** The scatter grouped by asset, ready for one draw call each. Sorted by asset name, so two
 *  builds of the same field hand the renderer its batches in the same order. */
export function scatterBatches(t: TerrainData): ScatterBatch[] {
  const by = new Map<string, number[]>();
  for (let i = 0; i < t.scatter.length; i++) {
    const a = t.scatter[i].asset || "item";
    const b = by.get(a);
    if (b) b.push(i); else by.set(a, [i]);
  }
  const out: ScatterBatch[] = [];
  for (const asset of [...by.keys()].sort()) {
    const list = by.get(asset)!;
    const count = list.length;
    const matrices = new Float32Array(count * 16);
    const index = new Int32Array(count);
    for (let k = 0; k < count; k++) {
      const it = t.scatter[list[k]];
      index[k] = list[k];
      const sc = it.scale || 1;
      const tx = it.tilt?.[0] ?? 0, tz = it.tilt?.[1] ?? 0;
      const ca = Math.cos(tx), sa = Math.sin(tx);
      const cb = Math.cos(it.rot || 0), sb = Math.sin(it.rot || 0);
      const cc = Math.cos(tz), sc2 = Math.sin(tz);
      const o = k * 16;
      matrices[o] = cb * cc * sc;
      matrices[o + 1] = (ca * sc2 + sa * cc * sb) * sc;
      matrices[o + 2] = (sa * sc2 - ca * cc * sb) * sc;
      matrices[o + 3] = 0;
      matrices[o + 4] = -cb * sc2 * sc;
      matrices[o + 5] = (ca * cc - sa * sc2 * sb) * sc;
      matrices[o + 6] = (sa * cc + ca * sc2 * sb) * sc;
      matrices[o + 7] = 0;
      matrices[o + 8] = sb * sc;
      matrices[o + 9] = -sa * cb * sc;
      matrices[o + 10] = ca * cb * sc;
      matrices[o + 11] = 0;
      matrices[o + 12] = it.at[0];
      matrices[o + 13] = it.at[1];
      matrices[o + 14] = it.at[2];
      matrices[o + 15] = 1;
    }
    out.push({ asset, count, matrices, index });
  }
  return out;
}

/**
 * A uniform grid over the scatter, kept beside the terrain and rebuilt only when the list moves.
 *
 * Validity is checked by length AND by the identity of up to sixteen sampled items, because undo
 * can put back exactly as many items as it took out: a length check alone would then hand back a
 * grid pointing at trees that are no longer there. A pure APPEND — which is what a scatter stroke
 * is — extends the grid instead of rebuilding it, so planting stays O(placed).
 *
 * In a WeakMap, not on `TerrainData`: it is a cache, and it must not appear in `serialize`.
 */
interface ScatterIndex {
  cell: number;
  grid: Map<number, number[]>;
  len: number;
  probeAt: number[];
  probe: ScatterItem[];
}

const scatterIndexes = new WeakMap<TerrainData, ScatterIndex>();

const bucketKey = (i: number, j: number) => (Math.imul(i, 73856093) ^ Math.imul(j, 19349663)) | 0;

function probeOf(items: ScatterItem[]): { probeAt: number[]; probe: ScatterItem[] } {
  const probeAt: number[] = [];
  const probe: ScatterItem[] = [];
  const step = Math.max(1, Math.floor(items.length / 16));
  for (let i = 0; i < items.length && probeAt.length < 16; i += step) { probeAt.push(i); probe.push(items[i]); }
  if (items.length) {
    const last = items.length - 1;
    if (probeAt[probeAt.length - 1] !== last) { probeAt.push(last); probe.push(items[last]); }
  }
  return { probeAt, probe };
}

function addToGrid(ix: ScatterIndex, items: ScatterItem[], from: number, to: number): void {
  for (let k = from; k < to; k++) {
    const key = bucketKey(Math.floor(items[k].at[0] / ix.cell), Math.floor(items[k].at[2] / ix.cell));
    const b = ix.grid.get(key);
    if (b) b.push(k); else ix.grid.set(key, [k]);
  }
}

function scatterIndex(t: TerrainData): ScatterIndex {
  const items = t.scatter;
  const cur = scatterIndexes.get(t);
  if (cur) {
    let ok = cur.len <= items.length;
    if (ok) for (let i = 0; i < cur.probeAt.length; i++) {
      if (items[cur.probeAt[i]] !== cur.probe[i]) { ok = false; break; }
    }
    if (ok && cur.len === items.length) return cur;
    if (ok && items.length > cur.len) {
      addToGrid(cur, items, cur.len, items.length);
      cur.len = items.length;
      const p = probeOf(items);
      cur.probeAt = p.probeAt;
      cur.probe = p.probe;
      return cur;
    }
  }
  // Aim at a couple of items a bucket: too fine and the nine-cell walk misses neighbours a query
  // radius away, too coarse and every bucket is a linear scan again.
  const cell = Math.max(1e-3, t.spec.size / clamp(Math.round(Math.sqrt(Math.max(1, items.length) / 2)), 1, 512));
  const ix: ScatterIndex = { cell, grid: new Map(), len: items.length, probeAt: [], probe: [] };
  addToGrid(ix, items, 0, items.length);
  const p = probeOf(items);
  ix.probeAt = p.probeAt;
  ix.probe = p.probe;
  scatterIndexes.set(t, ix);
  return ix;
}

/**
 * Indices into `t.scatter` of everything within `r` world units of (x, z), inclusive.
 *
 * O(items in range), not O(items on the field). `erase` uses it; so does a click.
 */
export function scatterNear(t: TerrainData, x: number, z: number, r: number): number[] {
  const out: number[] = [];
  if (!t.scatter.length || !(r >= 0)) return out;
  const ix = scatterIndex(t);
  const r2 = r * r;
  const i0 = Math.floor((x - r) / ix.cell), i1 = Math.floor((x + r) / ix.cell);
  const j0 = Math.floor((z - r) / ix.cell), j1 = Math.floor((z + r) / ix.cell);
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      const b = ix.grid.get(bucketKey(i, j));
      if (!b) continue;
      for (const k of b) {
        const it = t.scatter[k];
        if (!it) continue;
        const dx = it.at[0] - x, dz = it.at[2] - z;
        if (dx * dx + dz * dz <= r2) out.push(k);
      }
    }
  }
  return out;
}

/* ------------------------------------------------------------------------------------------
 * PLAN.
 *
 * `report()` is already a fitness function: walkable share, slope bands, relief, coverage. Given
 * a target it can be searched. This is the one thing here that neither Unity nor Godot has and
 * Gaea charges for — and it exists only because the report was written first.
 * ---------------------------------------------------------------------------------------- */

export interface PlanGoal {
  /** Share of the field that must be walkable, 0..1. */
  walkable?: number;
  /** The degrees `walkable` is measured at. Default 30, the same as `report`. */
  maxSlope?: number;
  /** World units between the lowest and highest ground, as [min, max]. */
  relief?: [number, number];
  /** Layer name to target share, 0..1. Only the named layers are judged. */
  coverage?: Record<string, number>;
  /** Confine every stroke to this rectangle of samples. */
  region?: { x0: number; z0: number; w: number; h: number };
  /** Most strokes it may spend. Default 24. */
  budget?: number;
  /** Wall-clock ceiling, ms. Default 4000 — it stops and reports what it reached. */
  ms?: number;
  seed?: number;
}

export interface PlanStep {
  brush: Brush;
  x: number;
  z: number;
  dt: number;
  /** Plain words: which part of the goal this stroke was for. */
  why: string;
  /** Distance to the goal after this stroke. It falls, or the stroke is rolled back. */
  score: number;
}

export interface PlanResult {
  steps: PlanStep[];
  /** One per accepted step, newest last — `undoPatch` them in reverse to put the field back. */
  patches: Patch[];
  before: TerrainReport;
  after: TerrainReport;
  /** Strokes evaluated, including the ones rolled back. */
  tried: number;
  ms: number;
  /** Every part of the goal was reached. */
  met: boolean;
  /** What it could not reach, and why. Never empty when `met` is false. */
  notes: string[];
}

/** How far off the goal a report is. Every term is a share of the field, so they add up. */
const PLAN_TOL = { walkable: 0.02, coverage: 0.03 };

function shareOf(rep: TerrainReport, name: string): number {
  for (const c of rep.coverage) if (c.layer === name) return c.share;
  return 0;
}

function goalDistance(rep: TerrainReport, goal: PlanGoal): number {
  let d = 0;
  if (goal.walkable !== undefined) d += Math.abs(rep.walkable - clamp01(goal.walkable));
  if (goal.relief) {
    const rel = rep.hi - rep.lo;
    const lo = Math.min(goal.relief[0], goal.relief[1]), hi = Math.max(goal.relief[0], goal.relief[1]);
    const miss = rel < lo ? lo - rel : rel > hi ? rel - hi : 0;
    // Divided by the band's top so relief lands on the same 0..1 scale as a coverage share and
    // one term cannot drown the others just because the field is measured in big units.
    d += miss / Math.max(1e-6, hi);
  }
  if (goal.coverage) {
    for (const name of Object.keys(goal.coverage)) {
      d += Math.abs(shareOf(rep, name) - clamp01(goal.coverage[name]));
    }
  }
  return d;
}

/**
 * Search strokes until the report matches the goal. THE FIELD IS CHANGED.
 *
 * Every candidate is applied, scored with `report`, and kept only if the distance to the goal
 * fell — a rejected stroke is undone with the patch it returned, so a rejected candidate leaves
 * the field bit-identical, drainage map included.
 *
 * First-improvement hill climbing, not best-of: scoring a candidate costs a whole `report`, and
 * applying the winner a second time after undoing everything would double the only expensive
 * part. The candidate LIST is ordered by which part of the goal is furthest off, so the first
 * thing tried is usually the thing that helps.
 */
export function plan(t: TerrainData, goal: PlanGoal): PlanResult {
  const t0 = now();
  const s = t.spec;
  const res = s.res;
  const cell = cellSize(s);
  const maxSlope = goal.maxSlope ?? 30;
  const budget = Math.max(0, Math.floor(goal.budget ?? 24));
  const msCap = Math.max(0, goal.ms ?? 4000);
  const rnd = rng(hashInts(goal.seed ?? s.seed, res, budget, 0x91a2));
  const notes: string[] = [];

  // A goal that names a layer the field has not got can never be met by painting — so make it,
  // before the `before` report, or the two reports are not comparable.
  if (goal.coverage) {
    for (const name of Object.keys(goal.coverage)) {
      if (t.layers.some((l) => l.name === name)) continue;
      if (t.layers.length >= 4) {
        notes.push("coverage names the layer '" + name + "', which this terrain has not got, and"
          + " all four splat channels are already spoken for. That part of the goal was skipped.");
        continue;
      }
      const p = PALETTE[Math.min(t.layers.length, 3)];
      t.layers.push({ name, colour: p.colour, tiling: p.tiling });
      notes.push("added the layer '" + name + "' the goal asked for; it started at 0 coverage.");
    }
  }

  const before = report(t, maxSlope);
  const reg = goal.region
    ? clampRect(res, goal.region.x0, goal.region.z0, goal.region.w, goal.region.h)
    : { x0: 0, z0: 0, w: res, h: res };
  const spanW = Math.max(cell, (reg.w - 1) * cell);
  const spanH = Math.max(cell, (reg.h - 1) * cell);
  const wx0 = s.origin[0] + reg.x0 * cell, wz0 = s.origin[2] + reg.z0 * cell;

  const steps: PlanStep[] = [];
  const patches: Patch[] = [];
  let rep = before;
  let dist = goalDistance(rep, goal);
  let tried = 0;
  let stop = "";
  // A round of candidates that all fail is usually the POSITIONS, not the brushes: nine strokes
  // were offered at nine spots the dice chose and none of them happened to be where the goal
  // needed work. Stopping there left walkable stuck at 0.813 of a 0.85 target with most of its
  // budget unspent; re-rolling a few times gets it to the tolerance. Six is where the return
  // stopped paying for the reports it costs.
  const BARREN = 6;
  let barren = 0;

  while (steps.length < budget) {
    if (now() - t0 > msCap) { stop = "the " + msCap + " ms ceiling was reached"; break; }
    const cands = planCandidates(t, goal, rep, maxSlope, rnd, {
      wx0, wz0, spanW, spanH, cell, maxHeight: s.maxHeight, layers: t.layers,
    });
    if (!cands.length) { stop = "no brush left that moves any part of this goal"; break; }

    let took = false;
    for (const c of cands) {
      if (now() - t0 > msCap) { stop = "the " + msCap + " ms ceiling was reached"; break; }
      tried++;
      const patch = applyBrush(t, c.brush, c.x, c.z, c.dt);
      const after = report(t, maxSlope);
      const d = goalDistance(after, goal);
      // A hair of tolerance: a stroke that moves the distance by 1e-12 is float noise, and
      // accepting it burns a step out of the budget for nothing.
      if (d < dist - 1e-6) {
        steps.push({ brush: c.brush, x: c.x, z: c.z, dt: c.dt, why: c.why, score: d });
        patches.push(patch);
        rep = after;
        dist = d;
        took = true;
        break;
      }
      undoPatch(t, patch);
    }
    if (stop) break;
    if (took) barren = 0;
    else if (++barren >= BARREN) {
      stop = "the last " + BARREN + " rounds of candidates all made the distance worse or left it flat";
      break;
    }
    if (planMet(rep, goal)) break;
  }
  if (!stop && steps.length >= budget) stop = "the budget of " + budget + " strokes ran out";

  const after = report(t, maxSlope);
  const met = planMet(after, goal);
  if (!met) {
    if (goal.walkable !== undefined && Math.abs(after.walkable - goal.walkable) > PLAN_TOL.walkable) {
      notes.push("walkable reached " + after.walkable.toFixed(3) + " against a target of "
        + goal.walkable + " (+-" + PLAN_TOL.walkable + "), measured at " + maxSlope + " degrees.");
    }
    if (goal.relief) {
      const rel = after.hi - after.lo;
      const lo = Math.min(goal.relief[0], goal.relief[1]), hi = Math.max(goal.relief[0], goal.relief[1]);
      if (rel < lo || rel > hi) {
        notes.push("relief reached " + rel.toFixed(1) + " units against a band of "
          + lo + ".." + hi + ".");
        if (hi > s.maxHeight || lo > s.maxHeight) {
          notes.push("that band is outside the field's own range: a stored height is 0..1 and"
            + " maxHeight is " + s.maxHeight + ", so nothing here can be more than " + s.maxHeight
            + " units from top to bottom. Raise `spec.maxHeight` or lower the band.");
        }
      }
    }
    if (goal.coverage) {
      for (const name of Object.keys(goal.coverage)) {
        const got = shareOf(after, name);
        if (Math.abs(got - goal.coverage[name]) > PLAN_TOL.coverage) {
          notes.push("coverage of '" + name + "' reached " + got.toFixed(3)
            + " against a target of " + goal.coverage[name] + ".");
        }
      }
    }
    notes.push("it stopped because " + (stop || "the search ended") + ": " + steps.length
      + " stroke" + (steps.length === 1 ? "" : "s") + " kept out of " + tried + " tried, in "
      + Math.round(now() - t0) + " ms.");
  }

  return { steps, patches, before, after, tried, ms: now() - t0, met, notes };
}

function planMet(rep: TerrainReport, goal: PlanGoal): boolean {
  if (goal.walkable !== undefined && Math.abs(rep.walkable - goal.walkable) > PLAN_TOL.walkable) return false;
  if (goal.relief) {
    const rel = rep.hi - rep.lo;
    const lo = Math.min(goal.relief[0], goal.relief[1]), hi = Math.max(goal.relief[0], goal.relief[1]);
    if (rel < lo || rel > hi) return false;
  }
  if (goal.coverage) {
    for (const name of Object.keys(goal.coverage)) {
      if (Math.abs(shareOf(rep, name) - goal.coverage[name]) > PLAN_TOL.coverage) return false;
    }
  }
  return true;
}

interface PlanCand { brush: Brush; x: number; z: number; dt: number; why: string; need: number }

/**
 * The strokes worth trying next, most-wrong part of the goal first.
 *
 * AIMED, not fixed. The first version offered every remedy at one size and the search overshot
 * every time: told to build 20-30 units of relief on flat ground it raised 48 in one stroke, then
 * spent the rest of its budget failing to take 18 back. A brush is a rate, so the size of the
 * bite is `dt`, and `dt` is computed from how far off the goal is — a raise of D world units is
 * D / (maxHeight * strength) seconds. Each remedy is then offered at that size, a third of it and
 * three times it, so a first-improvement search has somewhere to go when the aim is off.
 */
function planCandidates(t: TerrainData, goal: PlanGoal, rep: TerrainReport, maxSlope: number,
                        rnd: () => number,
                        box: { wx0: number; wz0: number; spanW: number; spanH: number;
                               cell: number; maxHeight: number; layers: TerrainLayer[] }): PlanCand[] {
  const out: PlanCand[] = [];
  const big = Math.min(box.spanW, box.spanH) * 0.30;
  const mid = Math.min(box.spanW, box.spanH) * 0.18;
  const small = Math.min(box.spanW, box.spanH) * 0.10;
  // Centres are held a full REACH inside the region, so `region` confines the stroke rather than
  // merely aiming it. Reach is not the radius for `erode`: its droplets are free inside a
  // rectangle padded well past the disc, and a candidate placed a radius from the edge would run
  // water out of the region the goal asked it to stay in.
  const spot = (radius: number, kind: BrushKind): [number, number] => {
    const reach = kind === "erode"
      ? radius + clamp(Math.round(radius / box.cell * 0.5), 4, 64) * box.cell
      : radius + box.cell;
    const mx = Math.max(0, box.spanW - 2 * reach), mz = Math.max(0, box.spanH - 2 * reach);
    return [box.wx0 + Math.min(reach, box.spanW / 2) + rnd() * mx,
            box.wz0 + Math.min(reach, box.spanH / 2) + rnd() * mz];
  };
  /**
   * One remedy at three sizes: the aimed one, three times it, and a third of it.
   *
   * The ORDER matters and got this wrong once. Sizing the three by how much of the goal each
   * would close put the smallest bite first, so the search took the tiniest step that improved
   * anything, every time — 40 strokes to move walkable 0.22 where 8 aimed ones do the same job.
   * All three carry the remedy's own `need`, and the sort below is stable, so the aimed size
   * leads and the other two are there for when the aim is off.
   */
  const add = (brush: Brush, radius: number, dt: number, why: string, need: number) => {
    const [x, z] = spot(radius, brush.kind);
    for (const k of [1, 3, 0.34]) {
      out.push({ brush: { ...brush, radius }, x, z, dt: clamp(dt * k, 0.01, 4), why, need });
    }
  };
  const bite = (v: number) => clamp(v, 0.02, 1);

  if (goal.walkable !== undefined) {
    const gap = goal.walkable - rep.walkable;
    if (gap > PLAN_TOL.walkable) {
      const dt = bite(gap * 5);
      add({ kind: "smooth", radius: big, strength: 1, falloff: 0.6 }, big, dt, "walkable is short: smooth the ground flatter", gap);
      add({ kind: "flatten", radius: mid, strength: 1, falloff: 0.8 }, mid, dt, "walkable is short: flatten a shelf", gap);
      add({ kind: "smooth", radius: mid, strength: 1, falloff: 1 }, mid, dt, "walkable is short: smooth a smaller patch", gap);
    } else if (gap < -PLAN_TOL.walkable) {
      const dt = bite(-gap * 5);
      add({ kind: "noise", radius: big, strength: 0.8, falloff: 0.5 }, big, dt, "too much of it is walkable: rough it up", -gap);
      add({ kind: "raise", radius: small, strength: 0.7, falloff: 1 }, small, dt, "too much of it is walkable: push a knoll up", -gap);
      add({ kind: "erode", radius: mid, strength: 1, falloff: 0.6 }, mid, dt, "too much of it is walkable: cut gullies into it", -gap);
    }
  }

  if (goal.relief) {
    const rel = rep.hi - rep.lo;
    const lo = Math.min(goal.relief[0], goal.relief[1]), hi = Math.max(goal.relief[0], goal.relief[1]);
    if (rel < lo) {
      // Aim: a raise of D world units at strength S takes D / (maxHeight * S) seconds. Landing
      // in the middle of the band rather than on its floor leaves room for the next stroke.
      const want = (lo + hi) / 2 - rel;
      const need = (lo - rel) / Math.max(1e-6, hi);
      add({ kind: "raise", radius: mid, strength: 0.9, falloff: 1 }, mid, bite(want / (box.maxHeight * 0.9)), "not enough relief: raise a summit", need);
      add({ kind: "lower", radius: mid, strength: 0.9, falloff: 1 }, mid, bite(want / (box.maxHeight * 0.9)), "not enough relief: dig a basin", need);
      add({ kind: "noise", radius: big, strength: 1, falloff: 0.4 }, big, bite(want / (box.maxHeight * 0.25)), "not enough relief: add bumps everywhere", need);
    } else if (rel > hi) {
      const want = rel - (lo + hi) / 2;
      const need = (rel - hi) / Math.max(1e-6, hi);
      add({ kind: "smooth", radius: big, strength: 1, falloff: 0.5 }, big, bite(want / box.maxHeight * 4), "too much relief: take the tops off", need);
      add({ kind: "flatten", radius: big, strength: 1, falloff: 0.6 }, big, bite(want / box.maxHeight * 4), "too much relief: level a wide area", need);
      add({ kind: "smooth", radius: mid, strength: 1, falloff: 1 }, mid, bite(want / box.maxHeight * 4), "too much relief: smooth one hilltop", need);
    }
  }

  if (goal.coverage) {
    for (const name of Object.keys(goal.coverage)) {
      const want = clamp01(goal.coverage[name]);
      const got = shareOf(rep, name);
      const i = box.layers.findIndex((l) => l.name === name);
      if (i < 0 || i > 3) continue;
      const miss = Math.abs(got - want);
      // A stroke of radius r covers pi*r^2 of a spanW*spanH field, so the radius that covers the
      // share still missing is sqrt(miss * area / pi). Aiming this one is the difference between
      // hitting 0.35 and stopping at 0.41.
      const area = box.spanW * box.spanH;
      const aimR = clamp(Math.sqrt(Math.max(1e-9, miss) * area / Math.PI), box.cell, big);
      if (got < want - PLAN_TOL.coverage) {
        add({ kind: "paint", radius: aimR, strength: 1, falloff: 0.5, layer: i }, aimR, 1,
            "'" + name + "' is under its share: paint more of it", miss);
        add({ kind: "paint", radius: aimR, strength: 1, falloff: 1, layer: i }, aimR, 0.5,
            "'" + name + "' is under its share: paint a softer patch", miss);
      } else if (got > want + PLAN_TOL.coverage) {
        // Paint whatever is furthest UNDER its own target over the top; failing that, layer 0.
        let other = 0, worst = -1;
        for (let k = 0; k < box.layers.length && k < 4; k++) {
          if (k === i) continue;
          const nm = box.layers[k].name;
          const w = (goal.coverage[nm] ?? 0) - shareOf(rep, nm);
          if (w > worst) { worst = w; other = k; }
        }
        add({ kind: "paint", radius: aimR, strength: 1, falloff: 0.5, layer: other }, aimR, 1,
            "'" + name + "' is over its share: paint '" + (box.layers[other]?.name ?? other)
            + "' over some of it", miss);
        add({ kind: "paint", radius: aimR, strength: 1, falloff: 1, layer: other }, aimR, 0.5,
            "'" + name + "' is over its share: a softer patch of '"
            + (box.layers[other]?.name ?? other) + "'", miss);
      }
    }
  }

  void maxSlope;
  out.sort((a, b) => b.need - a.need);
  return out;
}

/**
 * The fragment chunk: PlayCanvas's `getAlbedo()`, replaced.
 *
 * Only the albedo. The lighting, the shadows, the fog and the tone map stay PlayCanvas's own, so
 * ground lit by the game's sun looks like the rest of the game and not like a Studio preview.
 *
 * The `* 2.0` on a texture is the three viewport's rule kept identical: the colour is already in
 * `base`, so a texture only MODULATES it, and a texture that averages mid grey doubled leaves the
 * ground exactly as bright as it was. Switching one on adds grain, not a colour shift.
 *
 * `vPositionW.xz / tiling` is world-space tiling, not UV tiling: two fields side by side then
 * share one continuous grain instead of each restarting its texture at its own corner.
 */
// Pure, for the same reason: only a terrain build needs the chunk, and every importer of ops.ts
// paid 1.5 KB for it, the runtime included.
export const PC_SPLAT_CHUNK = /* @__PURE__ */ [
  "uniform vec3 material_diffuse;",
  "uniform vec3 uSplatCol0;",
  "uniform vec3 uSplatCol1;",
  "uniform vec3 uSplatCol2;",
  "uniform vec3 uSplatCol3;",
  "uniform vec4 uSplatHas;",
  "uniform vec4 uSplatTile;",
  "uniform sampler2D uSplatTex0;",
  "uniform sampler2D uSplatTex1;",
  "uniform sampler2D uSplatTex2;",
  "uniform sampler2D uSplatTex3;",
  "void getAlbedo() {",
  "    vec4 w = vVertexColor;",
  // A vertex whose weights were never written sums to zero, and dividing by that is a black hole
  // in the middle of the field. The floor makes it layer 0, which is what a fresh field is
  // everywhere else.
  "    float s = w.r + w.g + w.b + w.a;",
  "    w = s > 1e-4 ? w / s : vec4(1.0, 0.0, 0.0, 0.0);",
  "    vec3 base = uSplatCol0 * w.r + uSplatCol1 * w.g + uSplatCol2 * w.b + uSplatCol3 * w.a;",
  "    vec3 grain = vec3(1.0);",
  "    if (uSplatHas.x + uSplatHas.y + uSplatHas.z + uSplatHas.w > 0.5) {",
  "        grain = vec3(0.0);",
  "        grain += (uSplatHas.x > 0.5 ? texture2D(uSplatTex0, vPositionW.xz / max(0.001, uSplatTile.x)).rgb * 2.0 : vec3(1.0)) * w.r;",
  "        grain += (uSplatHas.y > 0.5 ? texture2D(uSplatTex1, vPositionW.xz / max(0.001, uSplatTile.y)).rgb * 2.0 : vec3(1.0)) * w.g;",
  "        grain += (uSplatHas.z > 0.5 ? texture2D(uSplatTex2, vPositionW.xz / max(0.001, uSplatTile.z)).rgb * 2.0 : vec3(1.0)) * w.b;",
  "        grain += (uSplatHas.w > 0.5 ? texture2D(uSplatTex3, vPositionW.xz / max(0.001, uSplatTile.w)).rgb * 2.0 : vec3(1.0)) * w.a;",
  "    }",
  "    dAlbedo = material_diffuse.rgb * base * grain;",
  "}",
].join("\n");

/**
 * The same material, as source for a file that imports nothing.
 *
 * The emitted builder is standalone by design, so it cannot import this module — and a second
 * copy of a shader is exactly the thing that gets fixed in one place and not the other. The GLSL
 * is therefore not copied: `PC_SPLAT_CHUNK` is written into the file as one string literal, from
 * this constant, so a change here is in the next emit.
 *
 * WHAT IT LEANS ON in the file it is appended to, all of them names `emitBuilder` has always
 * written: `RES`, `LAYERS`, `splat()` and the builder itself. Nothing else. It adds three
 * exports and changes none, so a game already calling `<fn>()` keeps exactly what it had.
 */
export function pcSplatSource(fn: string): string {
  return [
    "",
    "/* -----------------------------------------------------------------------------------------",
    " * THE FOUR-LAYER SPLAT MATERIAL, for PlayCanvas.",
    " *",
    " * " + fn + "() on its own draws the ground in ONE averaged colour a vertex — all a stock",
    " * StandardMaterial can do with vertex colour. " + fn + "Splat() draws the same ground with",
    " * the four layers blended per pixel, each tiled in world space, each free to carry a texture:",
    " *",
    " *   const ground = " + fn + "Splat(pc, app);",
    " *   app.root.addChild(ground);",
    " *   // with real textures, in layer order:",
    " *   " + fn + "Splat(pc, app, { textures: [grassTex, dirtTex, rockTex, sandTex] });",
    " *",
    " * It replaces one shader chunk, getAlbedo, so the game's own lighting, shadows, fog and tone",
    " * mapping are untouched. On a PlayCanvas too old for chunk overrides it falls back to the",
    " * flat vertex colour rather than failing.",
    " * -------------------------------------------------------------------------------------- */",
    "",
    "const SPLAT_CHUNK = " + JSON.stringify(PC_SPLAT_CHUNK) + ";",
    "",
    "function splatLin(hex) {",
    "  const s = String(hex || '#808080').replace('#', '');",
    "  const n = parseInt(s.length === 3 ? s[0] + s[0] + s[1] + s[1] + s[2] + s[2] : s.slice(0, 6), 16) || 0;",
    "  const f = (v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));",
    "  return [f(((n >> 16) & 255) / 255), f(((n >> 8) & 255) / 255), f((n & 255) / 255)];",
    "}",
    "",
    "// The chunk API moved at PlayCanvas 2.7: a Map behind getShaderChunks where it used to be a",
    "// plain object. Both are tried; false means neither took.",
    "function splatChunk(pc, mat, src) {",
    "  try {",
    "    const chunks = typeof mat.getShaderChunks === 'function' ? mat.getShaderChunks(pc.SHADERLANGUAGE_GLSL || 'glsl') : null;",
    "    if (chunks && typeof chunks.set === 'function') { chunks.set('diffusePS', src); return true; }",
    "  } catch (e) { /* older engine */ }",
    "  try {",
    "    if (mat.chunks && typeof mat.chunks === 'object') {",
    "      const api = pc.CHUNKAPI_2_5 || pc.CHUNKAPI_1_70 || pc.CHUNKAPI_1_65;",
    "      const next = Object.assign({}, mat.chunks, { diffusePS: src });",
    "      if (api) next.APIVersion = api;",
    "      mat.chunks = next;",
    "      return true;",
    "    }",
    "  } catch (e) { /* no chunk system */ }",
    "  return false;",
    "}",
    "",
    "function splatWhite(pc, device) {",
    "  const t = new pc.Texture(device, { width: 1, height: 1, mipmaps: false });",
    "  try { const px = t.lock(); px[0] = 255; px[1] = 255; px[2] = 255; px[3] = 255; t.unlock(); } catch (e) {}",
    "  return t;",
    "}",
    "",
    "/** The material alone, for a game that builds its own mesh.",
    " *  @param opts { textures: [t0, t1, t2, t3], roughness } */",
    "export function " + fn + "Material(pc, app, opts) {",
    "  const o = opts || {};",
    "  const mat = new pc.StandardMaterial();",
    "  mat.name = 'terrain-splat';",
    "  mat.useMetalness = false;",
    "  mat.gloss = Math.max(0, Math.min(1, 1 - (o.roughness === undefined ? 0.96 : o.roughness)));",
    "  mat.diffuseVertexColor = true;",
    "  const ok = splatChunk(pc, mat, SPLAT_CHUNK);",
    "  mat.userData = mat.userData || {};",
    "  mat.userData.splat = ok;",
    "  if (!ok) { mat.update(); return mat; }",
    "  const white = splatWhite(pc, app.graphicsDevice);",
    "  const has = [0, 0, 0, 0], tile = [8, 8, 8, 8];",
    "  for (let i = 0; i < 4; i++) {",
    "    const l = LAYERS[i] || LAYERS[0] || {};",
    "    const c = splatLin(l.colour);",
    "    mat.setParameter('uSplatCol' + i, [c[0], c[1], c[2]]);",
    "    const tex = (o.textures && o.textures[i]) || null;",
    "    mat.setParameter('uSplatTex' + i, tex || white);",
    "    has[i] = tex ? 1 : 0;",
    "    tile[i] = l.tiling > 0 ? l.tiling : 8;",
    "  }",
    "  mat.setParameter('uSplatHas', has);",
    "  mat.setParameter('uSplatTile', tile);",
    "  mat.update();",
    "  return mat;",
    "}",
    "",
    "// `" + fn + "Weights` is emitted by the builder above, from `arrays().w`. It used to be",
    "// written again here, repeating the vertex stepping by hand — and a weight array one row out",
    "// of step paints the hilltops with the valley's dirt, with nothing in the picture to say so.",
    "/** The ground, with the four layers blended per pixel. " + fn + "'s options, plus textures. */",
    "export function " + fn + "Splat(pc, app, opts) {",
    "  const o = opts || {};",
    "  const mat = o.material || " + fn + "Material(pc, app, o);",
    "  const entity = " + fn + "(pc, app, Object.assign({}, o, { material: mat }));",
    "  if (mat.userData && mat.userData.splat) {",
    "    // The colour attribute carries the WEIGHTS on this path, not the blended colour: the",
    "    // shader does the blend and needs the four weights to do it. Handing it the blend instead",
    "    // paints the whole field one layer, because the blend's alpha is a constant 1.0.",
    "    const mi = entity.render.meshInstances[0];",
    "    mi.mesh.setColors(" + fn + "Weights(o.lod || 1), 4);",
    "    mi.mesh.update(pc.PRIMITIVE_TRIANGLES);",
    "  }",
    "  return entity;",
    "}",
    "",
  ].join("\n");
}
