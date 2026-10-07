// Procedural materials, baked into textures: Blender's shader nodes plus a Cycles bake, in plain
// arrays.
//
// The goblin A/B (data/ab/goblin/) is why this exists. Both builders modelled the same way; what
// the Blender one had was a surface: orange rust on grey steel from noise blotches, voronoi pits
// and streaks, bright wear on the convex edges, dark dirt in the cavities and under the helmet rim
// from occlusion, wood grain, cloth weave, strand streaks, all baked into ONE atlas with a correct
// tangent-space normal map. The forge one had to write its texel painter by hand, and the blind
// graders called it "paler and flatter", "little ambient occlusion", "beige steel that looks like
// ceramic". Here that recipe is a library:
//
//   bakeFunction  any colour function of (position, normal, curvature, occlusion) into the UVs,
//                 optionally read from a HIGH mesh under the texel (how a sculpt reaches a low mesh)
//   Mask/Layer    the node graph: noise, voronoi, streaks, curvature, occlusion, facing, region
//   MATERIALS     ready materials: steel, rustySteel, paintedSteel, leather, wood, cloth, skin,
//                 bone, gold, stone
//   bakeMaterial  one part: base colour (sRGB), ORM (occlusion, roughness, metal), normal map
//   bakeAtlas     every part of an asset into ONE texture set (unwrap with atlasArrays first)
//   applyMaps     the maps onto a three material with the right colour spaces and normal sign
//
// THE NORMAL MAP CONVENTION, worked out from three r183 and r185 (GLTFLoader, GLTFExporter,
// normal_fragment_maps, perturbNormal2Arb):
//   - A baked map is written in the glTF convention: R = +U (tangent), G = toward the TOP of the
//     image, which is DECREASING v in these UVs (v = row / height, glTF's own UV space, where row
//     0 is the first row of the PNG), B = out of the surface. A flat surface is (128, 128, 255).
//   - three, on a mesh without a `tangent` attribute, builds its frame from the UV derivatives:
//     T = direction of increasing u, B = direction of INCREASING v. For a flipY:false texture that
//     B points DOWN the image, the opposite of the map's green, so the material needs
//     normalScale = (s, -s). With three's own computeTangents() the bitangent is also +v, so the
//     same sign holds. This is what the forge builder found by trial.
//   - GLTFLoader does exactly this on import: a mesh with no tangents gets normalScale.y *= -1.
//   - GLTFExporter r183 (the frontend's) ignores normalScale.y and writes the image as it is: a
//     glTF-convention image, correct in every viewer, reloaded with y = -1. The r185.1 exporter the
//     backend vendors for /api/live/export bakes the sign into the image instead: without tangents
//     it flips green only when normalScale.y > 0, so y = -1 again writes the image untouched; with
//     tangents it flips green when y < 0 and exports the tangents, which is also self-consistent.
//   So: glTF-convention image + normalScale (1, -1) on a flipY:false DataTexture gives the same
//   picture in the forge, in the exported GLB, and in the GLB reloaded by three. applyMaps does it.
//   Proved on the bench (2026-09-24): forge render vs /api/live/export + /api/live/glb reload, same
//   camera, 0 of 159k subject pixels differ; the same render with (1, +1) moves 26% of them.
//   Keep meshes WITHOUT a tangent attribute (three's default). With computeTangents() the forge is
//   still right, but only the r185 exporter keeps the GLB right; r183 would write +v tangents
//   beside an up-the-image green, inverted in every viewer.
//   The map is baked against three's derivative frame exactly (T = grad u, B = grad v, scaled
//   together), so it is exact in three even where an island's UVs are stretched; where the UVs are
//   conformal it equals the MikkTSpace-style frame other viewers use.
//
// Everything is seeded and deterministic (never Math.random), and nothing here imports an engine
// or touches the DOM: it runs in node tests and inside a forge page alike.

import type { Baked } from "./model";
import type { V3 } from "./ops";
import { smoothNormalsWelded, weldMap } from "./atlas";

// ================================================================== public types
/** What a painter sees at one texel. `curvature` in -1..1 (convex > 0), `ao` 0..1 (1 = open sky). */
export interface Texel { p: V3; n: V3; uv: [number, number]; tri: number; curvature: number; ao: number }

/**
 * A 0..1 weight over the surface. Every field given is MULTIPLIED (all must hold), and so is every
 * mask in `mul`; `max` then takes the largest of that product and each of its masks (either may
 * hold; a mask with only `max` is just their largest); `invert` flips the result last. So "rust in
 * cavities, broken up by noise" is `{ mul: [{ max: [{ ao }, { curvature }] }, { noise }] }`.
 * Positions are world metres, frequencies cycles per metre.
 *
 * - `noise`: fractal gradient noise, remapped so its value is spread evenly over 0..1: `lo`/`hi`
 *   are area fractions (lo 0.7, hi 0.8 covers about a quarter of the surface). `warp` swirls it.
 *   Defaults lo 0.4, hi 0.6, octaves 4.
 * - `voronoi`: `cells` per metre. `spots`: 1 at each cell's point falling to 0 at a per-cell
 *   random radius (rust dots, pits, knots); `cracks`: 1 on the borders between cells (stone, dried
 *   leather). Defaults spots lo 0.35 hi 0.6, cracks lo 0.75 hi 0.92.
 * - `streaks`: noise stretched `stretch` times along `dir`: rust runs, brushed metal, wood grain,
 *   strands. lo/hi as for noise.
 * - `curvature`: convex edges (wear) when lo > 0, cavities when hi < 0; the ramp always runs from
 *   the end nearer zero (0) to the end farther from it (1), whichever order they are given in.
 * - `ao`: occluded places (dirt): 1 at or below the smaller bound, 0 at or above the larger.
 * - `up`: the normal's y: 0 at `lo`, 1 at `hi` (lo 0.3, hi 0.8: dust on top; lo 0.2, hi -0.6: under).
 * - `region(p, n)`: anything else, 0..1.
 */
export interface Mask {
  noise?: { freq: number; octaves?: number; lo?: number; hi?: number; seed?: number; warp?: number };
  voronoi?: { cells: number; jitter?: number; lo?: number; hi?: number; mode?: 'spots' | 'cracks'; seed?: number };
  streaks?: { dir: V3; freq: number; stretch: number; lo?: number; hi?: number; seed?: number };
  curvature?: { lo: number; hi: number };  // convex edges (wear) when lo > 0, cavities when hi < 0
  ao?: { lo: number; hi: number };         // occluded places (dirt)
  up?: { lo: number; hi: number };         // n.y (dust on top, drips)
  region?: (p: V3, n: V3) => number;       // anything else, 0..1
  invert?: boolean; mul?: Mask[]; max?: Mask[];
}

/**
 * One coat over the base, weighted by `mask` times `opacity` (default 1). Colours are sRGB hex.
 * `blend` is how the colour goes on (normal lerps toward it; multiply, overlay, screen, add as in
 * any paint program, all in sRGB); roughness and metal always lerp toward the layer's value.
 * `height` is in bump units (the material's `bump` metres each): with blend 'add' it is added
 * (pits -1, bumps +1), with 'multiply' it scales what is below, otherwise the height lerps toward
 * it, so a paint layer at +0.3 over bare metal leaves a step where it chips.
 */
export interface Layer { name?: string; color?: string; roughness?: number; metal?: number; height?: number;
  mask?: Mask; opacity?: number; blend?: 'normal' | 'multiply' | 'overlay' | 'screen' | 'add' }

/** A material as layers over a base. `bump`: metres of relief per unit of layer height (default 0.0004). */
export interface SmartMaterial { base: { color: string; roughness: number; metal?: number }; layers: Layer[]; bump?: number }

type Mesh = { pos: Float32Array; idx: Uint32Array };
type Target = { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal?: Float32Array };
type AOOption = false | { rays?: number; distance?: number; step?: number };

// ================================================================== small maths
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
/** smoothstep that also runs downhill: 0 at a, 1 at b, whichever is larger. */
const sstep = (a: number, b: number, x: number) => {
  if (a === b) return x >= a ? 1 : 0;
  let t = (x - a) / (b - a);
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return t * t * (3 - 2 * t);
};

function hexToRgb(hex: string | number | undefined, fallback: [number, number, number] = [0.5, 0.5, 0.5]): [number, number, number] {
  if (typeof hex === "number") return [((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255];
  if (typeof hex !== "string") return fallback;
  let h = hex.trim().replace(/^#/, "");
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return fallback;
  const n = parseInt(h, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/** Mulberry32, the same generator ops.rng uses. */
function mulberry(seed: number): () => number {
  let a = (seed >>> 0) || 1;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash32(x: number, y: number, z: number, seed: number): number {
  let h = Math.imul(x | 0, 0x8da6b343) ^ Math.imul(y | 0, 0xd8163841) ^ Math.imul(z | 0, 0xcb1ab31f) ^ Math.imul(seed | 0, 0x165667b1);
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
  h = Math.imul(h ^ (h >>> 12), 0x297a2d39);
  return (h ^ (h >>> 15)) >>> 0;
}

// ================================================================== noise
// Ken Perlin's improved gradient noise, with a permutation shuffled per seed.
const permCache = new Map<number, Uint8Array>();
function perm(seed: number): Uint8Array {
  const key = seed | 0;
  let p = permCache.get(key);
  if (p) return p;
  const r = mulberry(key * 2654435761 + 17);
  const a = new Uint8Array(256);
  for (let i = 0; i < 256; i++) a[i] = i;
  for (let i = 255; i > 0; i--) { const j = Math.floor(r() * (i + 1)); const t = a[i]; a[i] = a[j]; a[j] = t; }
  p = new Uint8Array(512);
  for (let i = 0; i < 512; i++) p[i] = a[i & 255];
  permCache.set(key, p);
  return p;
}

const grad = (h: number, x: number, y: number, z: number) => {
  const k = h & 15;
  const u = k < 8 ? x : y, v = k < 4 ? y : k === 12 || k === 14 ? x : z;
  return ((k & 1) ? -u : u) + ((k & 2) ? -v : v);
};

function perlin(P: Uint8Array, x: number, y: number, z: number): number {
  const fx = Math.floor(x), fy = Math.floor(y), fz = Math.floor(z);
  const X = fx & 255, Y = fy & 255, Z = fz & 255;
  x -= fx; y -= fy; z -= fz;
  const u = x * x * x * (x * (x * 6 - 15) + 10), v = y * y * y * (y * (y * 6 - 15) + 10), w = z * z * z * (z * (z * 6 - 15) + 10);
  const A = P[X] + Y, AA = P[A] + Z, AB = P[A + 1] + Z, B = P[X + 1] + Y, BA = P[B] + Z, BB = P[B + 1] + Z;
  const x1 = x - 1, y1 = y - 1, z1 = z - 1;
  const g000 = grad(P[AA], x, y, z), g100 = grad(P[BA], x1, y, z), g010 = grad(P[AB], x, y1, z), g110 = grad(P[BB], x1, y1, z);
  const g001 = grad(P[AA + 1], x, y, z1), g101 = grad(P[BA + 1], x1, y, z1), g011 = grad(P[AB + 1], x, y1, z1), g111 = grad(P[BB + 1], x1, y1, z1);
  const a0 = g000 + u * (g100 - g000), a1 = g010 + u * (g110 - g010), a2 = g001 + u * (g101 - g001), a3 = g011 + u * (g111 - g011);
  const b0 = a0 + v * (a1 - a0), b1 = a2 + v * (a3 - a2);
  return b0 + w * (b1 - b0);
}

/** Standard deviation of one octave of `perlin`, measured over 2M samples for three seeds
 *  (0.275, 0.267, 0.269); fbm's spread then follows fbmSD (4 octaves: 0.166 predicted, 0.165 measured). */
const PERLIN_SD = 0.270;
const sdCache = new Map<number, number>();
/** The spread of `fbmRaw` with this many octaves (amplitudes halve, octaves independent). */
function fbmSD(oct: number): number {
  let s = sdCache.get(oct);
  if (s !== undefined) return s;
  let a = 1, sa = 0, sa2 = 0;
  for (let i = 0; i < oct; i++) { sa += a; sa2 += a * a; a *= 0.5; }
  s = PERLIN_SD * Math.sqrt(sa2) / sa;
  sdCache.set(oct, s);
  return s;
}

function fbmRaw(P: Uint8Array, x: number, y: number, z: number, oct: number): number {
  let sum = 0, amp = 1, norm = 0, f = 1;
  for (let i = 0; i < oct; i++) {
    sum += amp * perlin(P, x * f + i * 31.7, y * f + i * 17.3, z * f + i * 11.9);
    norm += amp; amp *= 0.5; f *= 2.03;
  }
  return sum / norm;
}

/** fbm remapped through its own (near-normal) distribution, so the value is spread evenly over 0..1. */
function fbmU(P: Uint8Array, x: number, y: number, z: number, oct: number): number {
  const v = fbmRaw(P, x, y, z, oct) / fbmSD(oct);
  return 1 / (1 + Math.exp(-1.702 * v));
}

// Voronoi (Worley) in 3D: the nearest and second-nearest feature points, and the nearest one's hash.
let VOR_F1 = 0, VOR_F2 = 0, VOR_ID = 0;
function voronoi(x: number, y: number, z: number, jitter: number, seed: number) {
  const cx = Math.floor(x), cy = Math.floor(y), cz = Math.floor(z);
  let f1 = 1e9, f2 = 1e9, id = 0;
  for (let dz = -1; dz <= 1; dz++) for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
    const gx = cx + dx, gy = cy + dy, gz = cz + dz;
    const h = hash32(gx, gy, gz, seed);
    const px = gx + 0.5 + jitter * ((h & 1023) / 1023 - 0.5);
    const py = gy + 0.5 + jitter * (((h >>> 10) & 1023) / 1023 - 0.5);
    const pz = gz + 0.5 + jitter * (((h >>> 20) & 1023) / 1023 - 0.5);
    const ex = px - x, ey = py - y, ez = pz - z;
    const d = ex * ex + ey * ey + ez * ez;
    if (d < f1) { f2 = f1; f1 = d; id = h; } else if (d < f2) f2 = d;
  }
  VOR_F1 = Math.sqrt(f1); VOR_F2 = Math.sqrt(f2); VOR_ID = id;
}

// ================================================================== the BVH the bakes cast into
//
// Flat typed arrays, binned-SAH build, allocation-free traversal: millions of occlusion rays is
// what an AO bake is, and model.ts's BVH allocates a handful of arrays per triangle test.

interface FastBVH { box: Float32Array; kid: Int32Array; tri: Float32Array; id: Int32Array; nodes: number; nt: number }
const STACK = new Int32Array(1024);

function buildFastBVH(pos: Float32Array, idx: Uint32Array): FastBVH {
  const nt = (idx.length / 3) | 0;
  const lo = new Float32Array(nt * 3), hi = new Float32Array(nt * 3), ce = new Float32Array(nt * 3);
  for (let t = 0; t < nt; t++) {
    const a = idx[t * 3] * 3, b = idx[t * 3 + 1] * 3, c = idx[t * 3 + 2] * 3;
    for (let k = 0; k < 3; k++) {
      const x = pos[a + k], y = pos[b + k], z = pos[c + k];
      const mn = x < y ? (x < z ? x : z) : (y < z ? y : z), mx = x > y ? (x > z ? x : z) : (y > z ? y : z);
      lo[t * 3 + k] = mn; hi[t * 3 + k] = mx; ce[t * 3 + k] = (mn + mx) * 0.5;
    }
  }
  const order = new Int32Array(nt);
  for (let i = 0; i < nt; i++) order[i] = i;
  const cap = Math.max(2, 2 * nt);
  const box = new Float32Array(cap * 6), kid = new Int32Array(cap * 2);
  let nodes = 1;
  const BINS = 16;
  const bCnt = new Int32Array(BINS), bBox = new Float64Array(BINS * 6), rArea = new Float64Array(BINS), rCnt = new Int32Array(BINS);
  const area = (x0: number, y0: number, z0: number, x1: number, y1: number, z1: number) => {
    const dx = x1 - x0, dy = y1 - y0, dz = z1 - z0;
    return dx < 0 ? 0 : dx * dy + dy * dz + dz * dx;
  };
  const stack: number[] = [];
  if (nt) stack.push(0, 0, nt); else { kid[0] = -1; kid[1] = 0; box.fill(0, 0, 6); }
  while (stack.length) {
    const end = stack.pop()!, start = stack.pop()!, node = stack.pop()!;
    let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
    let cx0 = Infinity, cy0 = Infinity, cz0 = Infinity, cx1 = -Infinity, cy1 = -Infinity, cz1 = -Infinity;
    for (let i = start; i < end; i++) {
      const t = order[i] * 3;
      if (lo[t] < x0) x0 = lo[t]; if (lo[t + 1] < y0) y0 = lo[t + 1]; if (lo[t + 2] < z0) z0 = lo[t + 2];
      if (hi[t] > x1) x1 = hi[t]; if (hi[t + 1] > y1) y1 = hi[t + 1]; if (hi[t + 2] > z1) z1 = hi[t + 2];
      const cx = ce[t], cy = ce[t + 1], cz = ce[t + 2];
      if (cx < cx0) cx0 = cx; if (cx > cx1) cx1 = cx; if (cy < cy0) cy0 = cy; if (cy > cy1) cy1 = cy; if (cz < cz0) cz0 = cz; if (cz > cz1) cz1 = cz;
    }
    const o = node * 6;
    box[o] = x0; box[o + 1] = y0; box[o + 2] = z0; box[o + 3] = x1; box[o + 4] = y1; box[o + 5] = z1;
    const count = end - start;
    const leaf = () => { kid[node * 2] = -1 - start; kid[node * 2 + 1] = count; };
    if (count <= 4) { leaf(); continue; }
    const ex = cx1 - cx0, ey = cy1 - cy0, ez = cz1 - cz0;
    const axis = ex >= ey && ex >= ez ? 0 : ey >= ez ? 1 : 2;
    const cmin = axis === 0 ? cx0 : axis === 1 ? cy0 : cz0;
    const ext = axis === 0 ? ex : axis === 1 ? ey : ez;
    let mid = -1;
    if (ext > 1e-20) {
      const k = (BINS * (1 - 1e-6)) / ext;
      bCnt.fill(0);
      for (let b = 0; b < BINS; b++) { bBox[b * 6] = bBox[b * 6 + 1] = bBox[b * 6 + 2] = Infinity; bBox[b * 6 + 3] = bBox[b * 6 + 4] = bBox[b * 6 + 5] = -Infinity; }
      for (let i = start; i < end; i++) {
        const t = order[i];
        const b = ((ce[t * 3 + axis] - cmin) * k) | 0;
        bCnt[b]++;
        const q = b * 6, t3 = t * 3;
        if (lo[t3] < bBox[q]) bBox[q] = lo[t3]; if (lo[t3 + 1] < bBox[q + 1]) bBox[q + 1] = lo[t3 + 1]; if (lo[t3 + 2] < bBox[q + 2]) bBox[q + 2] = lo[t3 + 2];
        if (hi[t3] > bBox[q + 3]) bBox[q + 3] = hi[t3]; if (hi[t3 + 1] > bBox[q + 4]) bBox[q + 4] = hi[t3 + 1]; if (hi[t3 + 2] > bBox[q + 5]) bBox[q + 5] = hi[t3 + 2];
      }
      let ax0 = Infinity, ay0 = Infinity, az0 = Infinity, ax1 = -Infinity, ay1 = -Infinity, az1 = -Infinity, cnt = 0;
      for (let b = BINS - 1; b > 0; b--) {
        const q = b * 6;
        if (bCnt[b]) {
          if (bBox[q] < ax0) ax0 = bBox[q]; if (bBox[q + 1] < ay0) ay0 = bBox[q + 1]; if (bBox[q + 2] < az0) az0 = bBox[q + 2];
          if (bBox[q + 3] > ax1) ax1 = bBox[q + 3]; if (bBox[q + 4] > ay1) ay1 = bBox[q + 4]; if (bBox[q + 5] > az1) az1 = bBox[q + 5];
        }
        cnt += bCnt[b]; rCnt[b] = cnt; rArea[b] = cnt ? area(ax0, ay0, az0, ax1, ay1, az1) : 0;
      }
      let best = Infinity, bestB = -1, lc = 0;
      ax0 = ay0 = az0 = Infinity; ax1 = ay1 = az1 = -Infinity;
      for (let b = 0; b < BINS - 1; b++) {
        const q = b * 6;
        if (bCnt[b]) {
          if (bBox[q] < ax0) ax0 = bBox[q]; if (bBox[q + 1] < ay0) ay0 = bBox[q + 1]; if (bBox[q + 2] < az0) az0 = bBox[q + 2];
          if (bBox[q + 3] > ax1) ax1 = bBox[q + 3]; if (bBox[q + 4] > ay1) ay1 = bBox[q + 4]; if (bBox[q + 5] > az1) az1 = bBox[q + 5];
        }
        lc += bCnt[b];
        if (!lc || !rCnt[b + 1]) continue;
        const cost = lc * area(ax0, ay0, az0, ax1, ay1, az1) + rCnt[b + 1] * rArea[b + 1];
        if (cost < best) { best = cost; bestB = b; }
      }
      const parentA = area(x0, y0, z0, x1, y1, z1);
      if (bestB >= 0 && count <= 12 && parentA + best >= count * parentA) { leaf(); continue; }
      if (bestB >= 0) {
        let i = start, j = end - 1;
        while (i <= j) {
          const t = order[i];
          if ((((ce[t * 3 + axis] - cmin) * k) | 0) <= bestB) i++;
          else { order[i] = order[j]; order[j] = t; j--; }
        }
        mid = i;
      }
    }
    if (mid <= start || mid >= end) {
      if (count <= 12) { leaf(); continue; }
      mid = (start + end) >> 1;
    }
    const l = nodes++, r = nodes++;
    kid[node * 2] = l; kid[node * 2 + 1] = r;
    stack.push(l, start, mid, r, mid, end);
  }
  const tri = new Float32Array(nt * 9), id = new Int32Array(nt);
  for (let s = 0; s < nt; s++) {
    const t = order[s];
    id[s] = t;
    const a = idx[t * 3] * 3, b = idx[t * 3 + 1] * 3, c = idx[t * 3 + 2] * 3, q = s * 9;
    tri[q] = pos[a]; tri[q + 1] = pos[a + 1]; tri[q + 2] = pos[a + 2];
    tri[q + 3] = pos[b] - pos[a]; tri[q + 4] = pos[b + 1] - pos[a + 1]; tri[q + 5] = pos[b + 2] - pos[a + 2];
    tri[q + 6] = pos[c] - pos[a]; tri[q + 7] = pos[c + 1] - pos[a + 1]; tri[q + 8] = pos[c + 2] - pos[a + 2];
  }
  return { box, kid, tri, id, nodes, nt };
}

/** Anything between tmin and tmax along the ray? The occlusion query. */
function occludedFast(B: FastBVH, ox: number, oy: number, oz: number, dx: number, dy: number, dz: number, tmin: number, tmax: number): boolean {
  if (!B.nt) return false;
  const box = B.box, kid = B.kid, T = B.tri;
  const ix = 1 / dx, iy = 1 / dy, iz = 1 / dz;
  let sp = 0;
  STACK[sp++] = 0;
  while (sp > 0) {
    const n = STACK[--sp], o = n * 6;
    let t0 = (box[o] - ox) * ix, t1 = (box[o + 3] - ox) * ix;
    if (t0 > t1) { const s = t0; t0 = t1; t1 = s; }
    let tn = t0 > tmin ? t0 : tmin, tf = t1 < tmax ? t1 : tmax;
    t0 = (box[o + 1] - oy) * iy; t1 = (box[o + 4] - oy) * iy;
    if (t0 > t1) { const s = t0; t0 = t1; t1 = s; }
    if (t0 > tn) tn = t0; if (t1 < tf) tf = t1;
    t0 = (box[o + 2] - oz) * iz; t1 = (box[o + 5] - oz) * iz;
    if (t0 > t1) { const s = t0; t0 = t1; t1 = s; }
    if (t0 > tn) tn = t0; if (t1 < tf) tf = t1;
    if (tn > tf) continue;
    const k = kid[n * 2];
    if (k < 0) {
      const s0 = -1 - k, s1 = s0 + kid[n * 2 + 1];
      for (let s = s0; s < s1; s++) {
        const q = s * 9;
        const e1x = T[q + 3], e1y = T[q + 4], e1z = T[q + 5], e2x = T[q + 6], e2y = T[q + 7], e2z = T[q + 8];
        const px = dy * e2z - dz * e2y, py = dz * e2x - dx * e2z, pz = dx * e2y - dy * e2x;
        const det = e1x * px + e1y * py + e1z * pz;
        if (det > -1e-20 && det < 1e-20) continue;
        const inv = 1 / det;
        const sx = ox - T[q], sy = oy - T[q + 1], sz = oz - T[q + 2];
        const u = (sx * px + sy * py + sz * pz) * inv;
        if (u < 0 || u > 1) continue;
        const qx = sy * e1z - sz * e1y, qy = sz * e1x - sx * e1z, qz = sx * e1y - sy * e1x;
        const v = (dx * qx + dy * qy + dz * qz) * inv;
        if (v < 0 || u + v > 1) continue;
        const t = (e2x * qx + e2y * qy + e2z * qz) * inv;
        if (t > tmin && t < tmax) return true;
      }
    } else { STACK[sp++] = k; STACK[sp++] = kid[n * 2 + 1]; }
  }
  return false;
}

let HIT_T = 0, HIT_TRI = -1, HIT_U = 0, HIT_V = 0;
/** The hit along the ray (0..tmax) nearest to distance tRef: the high surface nearest the low one. */
function nearestHitFast(B: FastBVH, ox: number, oy: number, oz: number, dx: number, dy: number, dz: number, tmax: number, tRef: number): boolean {
  HIT_TRI = -1;
  if (!B.nt) return false;
  const box = B.box, kid = B.kid, T = B.tri;
  const ix = 1 / dx, iy = 1 / dy, iz = 1 / dz;
  let best = Infinity;
  let sp = 0;
  STACK[sp++] = 0;
  while (sp > 0) {
    const n = STACK[--sp], o = n * 6;
    let t0 = (box[o] - ox) * ix, t1 = (box[o + 3] - ox) * ix;
    if (t0 > t1) { const s = t0; t0 = t1; t1 = s; }
    let tn = t0 > 0 ? t0 : 0, tf = t1 < tmax ? t1 : tmax;
    t0 = (box[o + 1] - oy) * iy; t1 = (box[o + 4] - oy) * iy;
    if (t0 > t1) { const s = t0; t0 = t1; t1 = s; }
    if (t0 > tn) tn = t0; if (t1 < tf) tf = t1;
    t0 = (box[o + 2] - oz) * iz; t1 = (box[o + 5] - oz) * iz;
    if (t0 > t1) { const s = t0; t0 = t1; t1 = s; }
    if (t0 > tn) tn = t0; if (t1 < tf) tf = t1;
    if (tn > tf || tf < tRef - best || tn > tRef + best) continue;
    const k = kid[n * 2];
    if (k < 0) {
      const s0 = -1 - k, s1 = s0 + kid[n * 2 + 1];
      for (let s = s0; s < s1; s++) {
        const q = s * 9;
        const e1x = T[q + 3], e1y = T[q + 4], e1z = T[q + 5], e2x = T[q + 6], e2y = T[q + 7], e2z = T[q + 8];
        const px = dy * e2z - dz * e2y, py = dz * e2x - dx * e2z, pz = dx * e2y - dy * e2x;
        const det = e1x * px + e1y * py + e1z * pz;
        if (det > -1e-20 && det < 1e-20) continue;
        const inv = 1 / det;
        const sx = ox - T[q], sy = oy - T[q + 1], sz = oz - T[q + 2];
        const u = (sx * px + sy * py + sz * pz) * inv;
        if (u < -1e-7 || u > 1 + 1e-7) continue;
        const qx = sy * e1z - sz * e1y, qy = sz * e1x - sx * e1z, qz = sx * e1y - sy * e1x;
        const v = (dx * qx + dy * qy + dz * qz) * inv;
        if (v < -1e-7 || u + v > 1 + 1e-7) continue;
        const t = (e2x * qx + e2y * qy + e2z * qz) * inv;
        if (t < 0 || t > tmax) continue;
        const d = t > tRef ? t - tRef : tRef - t;
        if (d < best) { best = d; HIT_T = t; HIT_TRI = B.id[s]; HIT_U = u; HIT_V = v; }
      }
    } else {
      // Nearer child first, so the bound tightens early.
      const l = k, r = kid[n * 2 + 1];
      const cl = (box[l * 6] + box[l * 6 + 3]) * dx + (box[l * 6 + 1] + box[l * 6 + 4]) * dy + (box[l * 6 + 2] + box[l * 6 + 5]) * dz;
      const cr = (box[r * 6] + box[r * 6 + 3]) * dx + (box[r * 6 + 1] + box[r * 6 + 4]) * dy + (box[r * 6 + 2] + box[r * 6 + 5]) * dz;
      if (cl < cr) { STACK[sp++] = r; STACK[sp++] = l; } else { STACK[sp++] = l; STACK[sp++] = r; }
    }
  }
  return HIT_TRI >= 0;
}

// ================================================================== curvature
//
// Two parts. The smooth part is the mean curvature a vertex sees along its edges, with normals that
// do not average across creases (so a flat face of a box is flat, not a blend of its corners). The
// sharp part is a band along every crease sharper than `crease` degrees, and along every open
// border: `radius` wide, 1 on a right-angled convex edge, -1 in a right-angled groove, falling
// linearly across the band. Without it a coarse box had every texel on its edge ring, i.e. every
// face was "worn"; with it the wear is a band of the asked width whatever the tessellation, which
// is what Blender's bevel node gives a Cycles bake.

interface CurvInfo { ck: Float32Array; hs: Int32Array; he: Int32Array; ep: Float32Array; es: Float32Array }

function curvatureInfo(pos: Float32Array, idx: Uint32Array, creaseDeg = 35): CurvInfo {
  const nt = (idx.length / 3) | 0;
  const { map, count } = weldMap(pos);
  const W = new Int32Array(nt * 3);
  for (let k = 0; k < nt * 3; k++) W[k] = map[idx[k]];
  const fn = new Float32Array(nt * 3), fa = new Float32Array(nt);
  for (let t = 0; t < nt; t++) {
    const a = idx[t * 3] * 3, b = idx[t * 3 + 1] * 3, c = idx[t * 3 + 2] * 3;
    const e1x = pos[b] - pos[a], e1y = pos[b + 1] - pos[a + 1], e1z = pos[b + 2] - pos[a + 2];
    const e2x = pos[c] - pos[a], e2y = pos[c + 1] - pos[a + 1], e2z = pos[c + 2] - pos[a + 2];
    const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
    const l = Math.hypot(nx, ny, nz);
    fa[t] = l * 0.5;
    if (l > 1e-30) { fn[t * 3] = nx / l; fn[t * 3 + 1] = ny / l; fn[t * 3 + 2] = nz / l; }
  }
  // Faces round each welded vertex.
  const vs = new Int32Array(count + 1);
  for (let k = 0; k < nt * 3; k++) vs[W[k] + 1]++;
  for (let i = 0; i < count; i++) vs[i + 1] += vs[i];
  const fill = vs.slice(0, count), vf = new Int32Array(nt * 3);
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) vf[fill[W[t * 3 + k]]++] = t;
  const cosC = Math.cos((creaseDeg * Math.PI) / 180);
  // Corner normals that stop at creases.
  const cn = new Float32Array(nt * 9);
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) {
    const v = W[t * 3 + k];
    let sx = 0, sy = 0, sz = 0;
    for (let i = vs[v]; i < vs[v + 1]; i++) {
      const f = vf[i];
      if (fn[f * 3] * fn[t * 3] + fn[f * 3 + 1] * fn[t * 3 + 1] + fn[f * 3 + 2] * fn[t * 3 + 2] < cosC) continue;
      sx += fn[f * 3] * fa[f]; sy += fn[f * 3 + 1] * fa[f]; sz += fn[f * 3 + 2] * fa[f];
    }
    const l = Math.hypot(sx, sy, sz) || 1;
    const q = t * 9 + k * 3;
    cn[q] = sx / l; cn[q + 1] = sy / l; cn[q + 2] = sz / l;
  }
  // Curvature along each edge of each triangle, summed on its two corners.
  const ks = new Float32Array(nt * 3);
  for (let t = 0; t < nt; t++) for (let e = 0; e < 3; e++) {
    const i = e, j = (e + 1) % 3;
    const a = idx[t * 3 + i] * 3, b = idx[t * 3 + j] * 3;
    const dx = pos[b] - pos[a], dy = pos[b + 1] - pos[a + 1], dz = pos[b + 2] - pos[a + 2];
    const l2 = dx * dx + dy * dy + dz * dz;
    if (l2 < 1e-30) continue;
    const qi = t * 9 + i * 3, qj = t * 9 + j * 3;
    const kk = ((cn[qj] - cn[qi]) * dx + (cn[qj + 1] - cn[qi + 1]) * dy + (cn[qj + 2] - cn[qi + 2]) * dz) / l2;
    ks[t * 3 + i] += kk; ks[t * 3 + j] += kk;
  }
  // Averaged over the corner's smoothing group, so it is continuous across the corners that share it.
  const ck = new Float32Array(nt * 3);
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) {
    const v = W[t * 3 + k];
    let s = 0, c = 0;
    for (let i = vs[v]; i < vs[v + 1]; i++) {
      const f = vf[i];
      if (fn[f * 3] * fn[t * 3] + fn[f * 3 + 1] * fn[t * 3 + 1] + fn[f * 3 + 2] * fn[t * 3 + 2] < cosC) continue;
      const kf = W[f * 3] === v ? 0 : W[f * 3 + 1] === v ? 1 : 2;
      s += ks[f * 3 + kf]; c += 2;
    }
    ck[t * 3 + k] = c ? s / c : 0;
  }
  // Creases and open borders.
  const edges = new Map<number, number>();
  const hard: number[] = [], hardS: number[] = [];
  const hardV: number[] = [];
  const addHard = (va: number, vb: number, pa: number, pb: number, s: number) => {
    hard.push(pos[pa], pos[pa + 1], pos[pa + 2], pos[pb], pos[pb + 1], pos[pb + 2]);
    hardS.push(s); hardV.push(va, vb);
  };
  for (let t = 0; t < nt; t++) for (let e = 0; e < 3; e++) {
    const a = W[t * 3 + e], b = W[t * 3 + (e + 1) % 3];
    if (a === b) continue;
    const key = a < b ? a * count + b : b * count + a;
    const o = edges.get(key);
    if (o === undefined) edges.set(key, t * 3 + e);
    else if (o >= 0) {
      edges.set(key, -2);   // two faces: decided here
      const t1 = (o / 3) | 0, t2 = t;
      const d = fn[t1 * 3] * fn[t2 * 3] + fn[t1 * 3 + 1] * fn[t2 * 3 + 1] + fn[t1 * 3 + 2] * fn[t2 * 3 + 2];
      if (d >= cosC) continue;
      // Convex when the far corner of the second face lies below the first face's plane.
      const far = idx[t2 * 3 + (e + 2) % 3] * 3, pa = idx[t * 3 + e] * 3;
      const s = (pos[far] - pos[pa]) * fn[t1 * 3] + (pos[far + 1] - pos[pa + 1]) * fn[t1 * 3 + 1] + (pos[far + 2] - pos[pa + 2]) * fn[t1 * 3 + 2];
      const ang = Math.acos(Math.max(-1, Math.min(1, d)));
      addHard(a, b, pa, idx[t * 3 + (e + 1) % 3] * 3, (s < 0 ? 1 : -1) * Math.min(1, ang / (Math.PI / 2)));
    } else edges.set(key, -3);   // a third face: non-manifold, left alone
  }
  for (const [key, o] of edges) {
    if (o < 0) continue;
    // An open border is the edge of a plate: worn like a right-angled convex edge.
    const t = (o / 3) | 0, e = o % 3;
    void key;
    addHard(W[t * 3 + e], W[t * 3 + (e + 1) % 3], idx[t * 3 + e] * 3, idx[t * 3 + (e + 1) % 3] * 3, 1);
  }
  // Candidate hard edges per triangle: those touching any of its corners.
  const nh = hardS.length;
  const hvS = new Int32Array(count + 1);
  for (let h = 0; h < nh; h++) { hvS[hardV[h * 2] + 1]++; hvS[hardV[h * 2 + 1] + 1]++; }
  for (let i = 0; i < count; i++) hvS[i + 1] += hvS[i];
  const hvF = hvS.slice(0, count), hvL = new Int32Array(nh * 2);
  for (let h = 0; h < nh; h++) { hvL[hvF[hardV[h * 2]]++] = h; hvL[hvF[hardV[h * 2 + 1]]++] = h; }
  const hs = new Int32Array(nt + 1);
  const he: number[] = [];
  for (let t = 0; t < nt; t++) {
    hs[t] = he.length;
    for (let k = 0; k < 3; k++) {
      const v = W[t * 3 + k];
      for (let i = hvS[v]; i < hvS[v + 1]; i++) {
        const h = hvL[i];
        let dup = false;
        for (let j = hs[t]; j < he.length; j++) if (he[j] === h) { dup = true; break; }
        if (!dup) he.push(h);
      }
    }
  }
  hs[nt] = he.length;
  return { ck, hs, he: Int32Array.from(he), ep: Float32Array.from(hard), es: Float32Array.from(hardS) };
}

function curvatureAt(ci: CurvInfo, t: number, wa: number, wb: number, wc: number, px: number, py: number, pz: number, radius: number): number {
  const k = wa * ci.ck[t * 3] + wb * ci.ck[t * 3 + 1] + wc * ci.ck[t * 3 + 2];
  let c = Math.tanh(k * radius);
  const ep = ci.ep;
  for (let i = ci.hs[t]; i < ci.hs[t + 1]; i++) {
    const h = ci.he[i], q = h * 6;
    const ax = ep[q], ay = ep[q + 1], az = ep[q + 2];
    const ux = ep[q + 3] - ax, uy = ep[q + 4] - ay, uz = ep[q + 5] - az;
    const wx = px - ax, wy = py - ay, wz = pz - az;
    const l2 = ux * ux + uy * uy + uz * uz;
    let s = l2 > 0 ? (wx * ux + wy * uy + wz * uz) / l2 : 0;
    s = s < 0 ? 0 : s > 1 ? 1 : s;
    const dx = wx - ux * s, dy = wy - uy * s, dz = wz - uz * s;
    const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (d < radius) c += ci.es[h] * (1 - d / radius);
  }
  return c > 1 ? 1 : c < -1 ? -1 : c;
}

// ================================================================== rasterising the UVs
//
// Every texel a triangle TOUCHES is claimed (its centre within 0.75 px of the triangle), the
// nearest triangle winning: texel centres inside a triangle always win over neighbours' borders.
// Border texels keep their unclamped barycentrics, so what is painted there is the surface
// continued a fraction of a texel past its edge, which is what a bilinear lookup at an island's
// edge wants to read.

interface Raster { W: number; H: number; owner: Int32Array; b1: Float32Array; b2: Float32Array }

function rasterParts(parts: Target[], triBase: Int32Array, W: number, H: number): Raster {
  const owner = new Int32Array(W * H).fill(-1);
  const b1 = new Float32Array(W * H), b2 = new Float32Array(W * H);
  const dist = new Float32Array(W * H).fill(Infinity);
  for (let pi = 0; pi < parts.length; pi++) {
    const { idx, uv } = parts[pi];
    const nt = (idx.length / 3) | 0;
    for (let t = 0; t < nt; t++) {
      const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
      const ax = uv[a * 2] * W, ay = uv[a * 2 + 1] * H, bx = uv[b * 2] * W, by = uv[b * 2 + 1] * H, cx = uv[c * 2] * W, cy = uv[c * 2 + 1] * H;
      const det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay);
      if (!(Math.abs(det) > 1e-12)) continue;
      const ad = Math.abs(det);
      const la = Math.hypot(cx - bx, cy - by), lb = Math.hypot(ax - cx, ay - cy), lc = Math.hypot(bx - ax, by - ay);
      const ha = ad / (la || 1e-30), hb = ad / (lb || 1e-30), hc = ad / (lc || 1e-30);
      const x0 = Math.max(0, Math.floor(Math.min(ax, bx, cx) - 1)), x1 = Math.min(W - 1, Math.ceil(Math.max(ax, bx, cx) + 1));
      const y0 = Math.max(0, Math.floor(Math.min(ay, by, cy) - 1)), y1 = Math.min(H - 1, Math.ceil(Math.max(ay, by, cy) + 1));
      const g = triBase[pi] + t;
      const inv = 1 / det;
      for (let y = y0; y <= y1; y++) {
        const py = y + 0.5;
        for (let x = x0; x <= x1; x++) {
          const px = x + 0.5;
          const l0 = ((bx - px) * (cy - py) - (cx - px) * (by - py)) * inv;
          const l1 = ((cx - px) * (ay - py) - (ax - px) * (cy - py)) * inv;
          const l2 = 1 - l0 - l1;
          let d = 0;
          if (l0 < 0) { const q = -l0 * ha; if (q > d) d = q; }
          if (l1 < 0) { const q = -l1 * hb; if (q > d) d = q; }
          if (l2 < 0) { const q = -l2 * hc; if (q > d) d = q; }
          if (d > 0.75) continue;
          const k = y * W + x;
          if (d < dist[k]) { dist[k] = d; owner[k] = g; b1[k] = l1; b2[k] = l2; }
        }
      }
    }
  }
  return { W, H, owner, b1, b2 };
}

/** Islands of a UV mesh: triangles joined through shared vertices, where two vertices at the same
 *  position with the same UV count as shared (a UV sphere's seam copies, a soup's corners). */
function triIslands(pos: Float32Array, idx: Uint32Array, uv: Float32Array): { isl: Int32Array; count: number } {
  const nv = (pos.length / 3) | 0;
  const parent = new Int32Array(nv);
  for (let i = 0; i < nv; i++) parent[i] = i;
  const find = (a: number): number => { while (parent[a] !== a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; };
  const { map } = weldMap(pos);
  const firstAt = new Map<number, number[]>();
  for (let v = 0; v < nv; v++) {
    const list = firstAt.get(map[v]);
    if (!list) { firstAt.set(map[v], [v]); continue; }
    for (const o of list) {
      if (Math.abs(uv[o * 2] - uv[v * 2]) < 1e-7 && Math.abs(uv[o * 2 + 1] - uv[v * 2 + 1]) < 1e-7) {
        const ra = find(o), rb = find(v);
        if (ra !== rb) parent[rb] = ra;
        break;
      }
    }
    list.push(v);
  }
  const nt = (idx.length / 3) | 0;
  for (let t = 0; t < nt; t++) {
    const ra = find(idx[t * 3]), rb = find(idx[t * 3 + 1]), rc = find(idx[t * 3 + 2]);
    if (rb !== ra) parent[rb] = ra;
    const rc2 = find(rc);
    if (rc2 !== ra) parent[rc2] = ra;
  }
  const id = new Int32Array(nv).fill(-1);
  let count = 0;
  const isl = new Int32Array(nt);
  for (let t = 0; t < nt; t++) {
    const r = find(idx[t * 3]);
    if (id[r] < 0) id[r] = count++;
    isl[t] = id[r];
  }
  return { isl, count };
}

/** Nearest-covered-texel fill, breadth first: `src[k]` is the covered texel k copies, -1 if unreached. */
function fillIndex(covered: Uint8Array, W: number, H: number, steps: number): Int32Array {
  const src = new Int32Array(W * H).fill(-1);
  const queue = new Int32Array(W * H);
  const depth = new Uint16Array(W * H);
  let qh = 0, qt = 0;
  for (let k = 0; k < W * H; k++) if (covered[k]) { src[k] = k; queue[qt++] = k; }
  const lim = Math.min(65000, Math.max(0, steps));
  while (qh < qt) {
    const k = queue[qh++];
    const dd = depth[k];
    if (dd >= lim) continue;
    const x = k % W, y = (k / W) | 0;
    for (let dy = -1; dy <= 1; dy++) {
      const yy = y + dy;
      if (yy < 0 || yy >= H) continue;
      for (let dx = -1; dx <= 1; dx++) {
        const xx = x + dx;
        if (xx < 0 || xx >= W || (!dx && !dy)) continue;
        const j = yy * W + xx;
        if (src[j] >= 0) continue;
        src[j] = src[k]; depth[j] = dd + 1; queue[qt++] = j;
      }
    }
  }
  return src;
}

function applyFill(img: Baked, src: Int32Array, covered: Uint8Array, rest: [number, number, number, number]) {
  const d = img.data;
  for (let k = 0; k < src.length; k++) {
    if (covered[k]) continue;
    const s = src[k], o = k * 4;
    if (s >= 0) { const q = s * 4; d[o] = d[q]; d[o + 1] = d[q + 1]; d[o + 2] = d[q + 2]; d[o + 3] = d[q + 3]; }
    else { d[o] = rest[0]; d[o + 1] = rest[1]; d[o + 2] = rest[2]; d[o + 3] = rest[3]; }
  }
}

// ================================================================== the surface under every texel
//
// One pass that every bake shares: rasterise all parts, then for every covered texel the low
// point and render normal, the high point / normal / curvature when a high mesh is given (a ray
// along the low normal, the hit nearest the low surface), and occlusion. Occlusion is the costly
// part, so it is traced once per (cell of `step` texels, island) at the covered texel nearest the
// cell's centre, and every texel takes a tent-weighted mean of the samples of its own island in
// the 3x3 cells round it: the same picture for a fraction of the rays, and the noise smoothed.

interface Part extends Target { high?: Mesh }
interface Surface {
  W: number; H: number;
  cov: Int32Array;            // covered texel indices
  owner: Int32Array; b1: Float32Array; b2: Float32Array;   // per texel (raster)
  triBase: Int32Array; triPart: Int32Array; triIsland: Int32Array;
  normals: Float32Array[];    // render normals per part
  SP: Float32Array; SN: Float32Array; SC: Float32Array; SH: Uint8Array; AO: Float32Array;   // per covered texel
}

const AO_BUDGET = 150000;

function aoDirections(rays: number): Float32Array {
  const out = new Float32Array(rays * 3);
  for (let i = 0; i < rays; i++) {
    let bits = i;
    bits = ((bits << 16) | (bits >>> 16)) >>> 0;
    bits = (((bits & 0x55555555) << 1) | ((bits & 0xaaaaaaaa) >>> 1)) >>> 0;
    bits = (((bits & 0x33333333) << 2) | ((bits & 0xcccccccc) >>> 2)) >>> 0;
    bits = (((bits & 0x0f0f0f0f) << 4) | ((bits & 0xf0f0f0f0) >>> 4)) >>> 0;
    bits = (((bits & 0x00ff00ff) << 8) | ((bits & 0xff00ff00) >>> 8)) >>> 0;
    const e1 = (i + 0.5) / rays, e2 = bits / 4294967296;
    const r = Math.sqrt(e1), phi = 2 * Math.PI * e2;
    out[i * 3] = r * Math.cos(phi); out[i * 3 + 1] = r * Math.sin(phi); out[i * 3 + 2] = Math.sqrt(Math.max(0, 1 - e1));
  }
  return out;
}

function bboxDiag(meshes: Array<{ pos: Float32Array }>): number {
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (const m of meshes) {
    const p = m.pos;
    for (let i = 0; i + 2 < p.length; i += 3) {
      if (p[i] < x0) x0 = p[i]; if (p[i] > x1) x1 = p[i];
      if (p[i + 1] < y0) y0 = p[i + 1]; if (p[i + 1] > y1) y1 = p[i + 1];
      if (p[i + 2] < z0) z0 = p[i + 2]; if (p[i + 2] > z1) z1 = p[i + 2];
    }
  }
  return isFinite(x0) ? Math.hypot(x1 - x0, y1 - y0, z1 - z0) : 1;
}

function mergeMeshes(meshes: Mesh[]): Mesh {
  let np = 0, ni = 0;
  for (const m of meshes) { np += m.pos.length; ni += m.idx.length; }
  const pos = new Float32Array(np), idx = new Uint32Array(ni);
  let po = 0, io = 0;
  for (const m of meshes) {
    pos.set(m.pos, po);
    const base = po / 3;
    for (let i = 0; i < m.idx.length; i++) idx[io + i] = m.idx[i] + base;
    po += m.pos.length; io += m.idx.length;
  }
  return { pos, idx };
}

const LIFT = new Float64Array(3);
/** Hanika's shadow-terminator lift ("Hacking the Shadow Terminator", 2021): how far above the flat
 *  triangle the smooth surface its vertex normals describe lies at this point, so an occlusion ray
 *  does not start inside its neighbours on a coarse curved mesh. It only lifts, never pushes down.
 *  `a`, `b`, `c` are offsets into pos/N (vertex x 3). */
function liftOf(pos: Float32Array, N: Float32Array, a: number, b: number, c: number, wa: number, wb: number, wc: number,
  px: number, py: number, pz: number): Float64Array {
  let lx = 0, ly = 0, lz = 0;
  if (wa > 0) { const d = (px - pos[a]) * N[a] + (py - pos[a + 1]) * N[a + 1] + (pz - pos[a + 2]) * N[a + 2]; if (d < 0) { lx -= wa * d * N[a]; ly -= wa * d * N[a + 1]; lz -= wa * d * N[a + 2]; } }
  if (wb > 0) { const d = (px - pos[b]) * N[b] + (py - pos[b + 1]) * N[b + 1] + (pz - pos[b + 2]) * N[b + 2]; if (d < 0) { lx -= wb * d * N[b]; ly -= wb * d * N[b + 1]; lz -= wb * d * N[b + 2]; } }
  if (wc > 0) { const d = (px - pos[c]) * N[c] + (py - pos[c + 1]) * N[c + 1] + (pz - pos[c + 2]) * N[c + 2]; if (d < 0) { lx -= wc * d * N[c]; ly -= wc * d * N[c + 1]; lz -= wc * d * N[c + 2]; } }
  LIFT[0] = lx; LIFT[1] = ly; LIFT[2] = lz;
  return LIFT;
}

function surface(parts: Part[], W: number, H: number, opts: {
  ao?: AOOption; distance?: number; curvature?: boolean; curvatureRadius?: number;
}): Surface {
  const np = parts.length;
  for (let i = 0; i < np; i++) {
    const p = parts[i], nv = (p.pos.length / 3) | 0;
    if (!p.uv || p.uv.length < nv * 2) throw new Error("bake: part " + i + " has no uv for its " + nv + " vertices - unwrap it first (atlasArrays)");
  }
  const triBase = new Int32Array(np + 1);
  for (let i = 0; i < np; i++) triBase[i + 1] = triBase[i] + ((parts[i].idx.length / 3) | 0);
  const ntAll = triBase[np];
  const triPart = new Int32Array(ntAll), triIsland = new Int32Array(ntAll);
  let islBase = 0;
  for (let i = 0; i < np; i++) {
    const { isl, count } = triIslands(parts[i].pos, parts[i].idx, parts[i].uv);
    for (let t = 0; t < isl.length; t++) { triPart[triBase[i] + t] = i; triIsland[triBase[i] + t] = islBase + isl[t]; }
    islBase += count;
  }
  const normals = parts.map((p) => (p.normal && p.normal.length === p.pos.length ? p.normal : smoothNormalsWelded(p.pos, p.idx)));
  const R = rasterParts(parts, triBase, W, H);
  const { owner, b1, b2 } = R;
  let nc = 0;
  for (let k = 0; k < W * H; k++) if (owner[k] >= 0) nc++;
  const cov = new Int32Array(nc);
  nc = 0;
  for (let k = 0; k < W * H; k++) if (owner[k] >= 0) cov[nc++] = k;

  const radius = opts.curvatureRadius && opts.curvatureRadius > 0 ? opts.curvatureRadius : 0.01;
  const wantCurv = opts.curvature !== false;
  const curvLow = parts.map((p) => (wantCurv ? curvatureInfo(p.pos, p.idx) : null));
  const highs = parts.map((p) => (p.high && p.high.idx.length ? p.high : null));
  const highBVH = highs.map((h) => (h ? buildFastBVH(h.pos, h.idx) : null));
  const highN = highs.map((h) => (h ? smoothNormalsWelded(h.pos, h.idx, 80) : null));
  const curvHigh = highs.map((h) => (h && wantCurv ? curvatureInfo(h.pos, h.idx) : null));
  const partDiag = parts.map((p) => bboxDiag([p]));

  // Occlusion samples: one per (cell, island).
  const aoOpt = opts.ao;
  const wantAO = aoOpt !== false && nc > 0;
  const aoO = (aoOpt || {}) as { rays?: number; distance?: number; step?: number };
  const step = Math.max(1, Math.round(aoO.step && aoO.step > 0 ? aoO.step : Math.sqrt(nc / AO_BUDGET)));
  const cw = Math.ceil(W / step), chh = Math.ceil(H / step);
  const cellS = new Int32Array(wantAO ? cw * chh : 0).fill(-1);   // covered index of the primary sample
  const cellD = new Float32Array(wantAO ? cw * chh : 0);
  const extra = new Map<number, number>();                           // cell * islands + island -> covered index
  const extraD = new Map<number, number>();
  const hasExtra = new Uint8Array(wantAO ? cw * chh : 0);
  const nIsl = Math.max(1, islBase);
  if (wantAO) {
    for (let ci = 0; ci < nc; ci++) {
      const k = cov[ci], x = k % W, y = (k / W) | 0;
      const cx = (x / step) | 0, cy = (y / step) | 0, cell = cy * cw + cx;
      const ddx = x + 0.5 - (cx + 0.5) * step, ddy = y + 0.5 - (cy + 0.5) * step, d = ddx * ddx + ddy * ddy;
      const isl = triIsland[owner[k]];
      const cur = cellS[cell];
      if (cur < 0) { cellS[cell] = ci; cellD[cell] = d; continue; }
      if (triIsland[owner[cov[cur]]] === isl) { if (d < cellD[cell]) { cellS[cell] = ci; cellD[cell] = d; } continue; }
      const key = cell * nIsl + isl;
      const e = extraD.get(key);
      if (e === undefined || d < e) { extra.set(key, ci); extraD.set(key, d); hasExtra[cell] = 1; }
    }
  }
  // slot[ci]: which sample a covered texel is (-1: none); sampleCi: the reverse.
  const slot = new Int32Array(wantAO ? nc : 0).fill(-1);
  const sampleList: number[] = [];
  if (wantAO) {
    for (let c = 0; c < cellS.length; c++) { const s = cellS[c]; if (s >= 0 && slot[s] < 0) { slot[s] = sampleList.length; sampleList.push(s); } }
    for (const s of extra.values()) if (slot[s] < 0) { slot[s] = sampleList.length; sampleList.push(s); }
  }
  const ns = sampleList.length;
  const AOo = new Float32Array(ns * 3);   // lifted ray origins
  const AOs = new Float32Array(ns).fill(1);

  const SP = new Float32Array(nc * 3), SN = new Float32Array(nc * 3), SC = new Float32Array(nc), SH = new Uint8Array(nc);
  for (let ci = 0; ci < nc; ci++) {
    const isS = wantAO && slot[ci] >= 0;
    const k = cov[ci], g = owner[k], pi = triPart[g], t = g - triBase[pi];
    const P = parts[pi], pos = P.pos, idx = P.idx, N = normals[pi];
    const wb = b1[k], wc = b2[k], wa = 1 - wb - wc;
    const a = idx[t * 3] * 3, b = idx[t * 3 + 1] * 3, c = idx[t * 3 + 2] * 3;
    let px = wa * pos[a] + wb * pos[b] + wc * pos[c], py = wa * pos[a + 1] + wb * pos[b + 1] + wc * pos[c + 1], pz = wa * pos[a + 2] + wb * pos[b + 2] + wc * pos[c + 2];
    let nx = wa * N[a] + wb * N[b] + wc * N[c], ny = wa * N[a + 1] + wb * N[b + 1] + wc * N[c + 1], nz = wa * N[a + 2] + wb * N[b + 2] + wc * N[c + 2];
    let nl = Math.hypot(nx, ny, nz) || 1;
    nx /= nl; ny /= nl; nz /= nl;
    let curv = 0, fromHigh = false;
    // Shading-point lift for the occlusion rays (Hanika's shadow-terminator fix), on the mesh the
    // point is on: the flat triangle sits below the smooth surface its normals describe.
    let lx = 0, ly = 0, lz = 0;
    const hb = highBVH[pi];
    if (hb) {
      const dist = opts.distance && opts.distance > 0 ? opts.distance : partDiag[pi] * 0.02;
      if (nearestHitFast(hb, px + nx * dist, py + ny * dist, pz + nz * dist, -nx, -ny, -nz, dist * 2, dist)) {
        const hm = highs[pi]!, hn = highN[pi]!, th = HIT_TRI, u = HIT_U, v = HIT_V, w0 = 1 - u - v;
        const ha = hm.idx[th * 3] * 3, hb2 = hm.idx[th * 3 + 1] * 3, hc = hm.idx[th * 3 + 2] * 3;
        px = w0 * hm.pos[ha] + u * hm.pos[hb2] + v * hm.pos[hc];
        py = w0 * hm.pos[ha + 1] + u * hm.pos[hb2 + 1] + v * hm.pos[hc + 1];
        pz = w0 * hm.pos[ha + 2] + u * hm.pos[hb2 + 2] + v * hm.pos[hc + 2];
        let hx = w0 * hn[ha] + u * hn[hb2] + v * hn[hc], hy = w0 * hn[ha + 1] + u * hn[hb2 + 1] + v * hn[hc + 1], hz = w0 * hn[ha + 2] + u * hn[hb2 + 2] + v * hn[hc + 2];
        const hl = Math.hypot(hx, hy, hz) || 1;
        hx /= hl; hy /= hl; hz /= hl;
        if (hx * nx + hy * ny + hz * nz < 0) { hx = -hx; hy = -hy; hz = -hz; }
        nx = hx; ny = hy; nz = hz;
        if (curvHigh[pi]) curv = curvatureAt(curvHigh[pi]!, th, w0, u, v, px, py, pz, radius);
        fromHigh = true;
        if (isS) {
          const L = liftOf(hm.pos, hn, ha, hb2, hc, w0, u, v, px, py, pz);
          lx = L[0]; ly = L[1]; lz = L[2];
        }
      }
    }
    if (!fromHigh) {
      if (curvLow[pi]) curv = curvatureAt(curvLow[pi]!, t, wa, wb, wc, px, py, pz, radius);
      if (isS) {
        const L = liftOf(pos, N, a, b, c, wa, wb, wc, px, py, pz);
        lx = L[0]; ly = L[1]; lz = L[2];
      }
    }
    SP[ci * 3] = px; SP[ci * 3 + 1] = py; SP[ci * 3 + 2] = pz;
    SN[ci * 3] = nx; SN[ci * 3 + 1] = ny; SN[ci * 3 + 2] = nz;
    SC[ci] = curv; SH[ci] = fromHigh ? 1 : 0;
    if (isS) { const q = slot[ci] * 3; AOo[q] = px + lx; AOo[q + 1] = py + ly; AOo[q + 2] = pz + lz; }
  }

  const AO = new Float32Array(nc).fill(1);
  if (wantAO) {
    const occ = mergeMeshes(parts.map((p) => p.high || p));
    const occDiag = bboxDiag([occ]);
    const bvh = buildFastBVH(occ.pos, occ.idx);
    const rays = Math.max(1, Math.round(aoO.rays && aoO.rays > 0 ? aoO.rays : 16));
    const reach = aoO.distance && aoO.distance > 0 ? aoO.distance : occDiag * 0.08;
    const eps = occDiag * 2e-6 + 1e-7;
    const dirs = aoDirections(rays);
    for (let si = 0; si < ns; si++) {
      const ci = sampleList[si];
      const nx = SN[ci * 3], ny = SN[ci * 3 + 1], nz = SN[ci * 3 + 2];
      // An orthonormal frame round n (Duff et al. 2017), turned by a per-texel angle.
      const sgn = nz >= 0 ? 1 : -1, aa = -1 / (sgn + nz), bb = nx * ny * aa;
      const tx = 1 + sgn * nx * nx * aa, ty = sgn * bb, tz = -sgn * nx;
      const sx = bb, sy = sgn + ny * ny * aa, sz = -ny;
      const rot = (hash32(cov[ci], 7, 11, 3) / 4294967296) * Math.PI * 2, cr = Math.cos(rot), sr = Math.sin(rot);
      const ox = AOo[si * 3] + nx * eps * 10, oy = AOo[si * 3 + 1] + ny * eps * 10, oz = AOo[si * 3 + 2] + nz * eps * 10;
      let hits = 0;
      for (let r = 0; r < rays; r++) {
        const lx0 = dirs[r * 3], ly0 = dirs[r * 3 + 1], lz0 = dirs[r * 3 + 2];
        const lx1 = lx0 * cr - ly0 * sr, ly1 = lx0 * sr + ly0 * cr;
        const dx = tx * lx1 + sx * ly1 + nx * lz0, dy = ty * lx1 + sy * ly1 + ny * lz0, dz = tz * lx1 + sz * ly1 + nz * lz0;
        if (occludedFast(bvh, ox, oy, oz, dx, dy, dz, eps, reach)) hits++;
      }
      AOs[si] = 1 - hits / rays;
    }
    // Every texel: the tent-weighted samples of its own island in the 3x3 cells round it.
    const inv = 1 / (1.5 * step);
    for (let ci = 0; ci < nc; ci++) {
      const k = cov[ci], x = k % W, y = (k / W) | 0, isl = triIsland[owner[k]];
      const cx = (x / step) | 0, cy = (y / step) | 0;
      let sw = 0, sv = 0;
      for (let gy = cy - 1; gy <= cy + 1; gy++) {
        if (gy < 0 || gy >= chh) continue;
        for (let gx = cx - 1; gx <= cx + 1; gx++) {
          if (gx < 0 || gx >= cw) continue;
          const cell = gy * cw + gx;
          for (let pass = 0; pass < 2; pass++) {
            let s: number;
            if (pass === 0) { s = cellS[cell]; if (s < 0 || triIsland[owner[cov[s]]] !== isl) continue; }
            else { if (!hasExtra[cell]) break; const e = extra.get(cell * nIsl + isl); if (e === undefined) break; s = e; }
            const kk = cov[s];
            const wx = 1 - Math.abs((kk % W) - x) * inv, wy = 1 - Math.abs(((kk / W) | 0) - y) * inv;
            if (wx > 0 && wy > 0) { const w = wx * wy; sw += w; sv += w * AOs[slot[s]]; }
          }
        }
      }
      AO[ci] = sw > 0 ? sv / sw : slot[ci] >= 0 ? AOs[slot[ci]] : 1;
    }
  }
  return { W, H, cov, owner, b1, b2, triBase, triPart, triIsland, normals, SP, SN, SC, SH, AO };
}

function sizeOf(size: number | [number, number]): [number, number] {
  const s = Array.isArray(size) ? size : [size, size];
  const w = Math.max(1, Math.round(Number(s[0]) || 256)), h = Math.max(1, Math.round(Number(s[1] ?? s[0]) || 256));
  return [w, h];
}

// ================================================================== bakeFunction
/** Run fn at every covered texel of the unwrapped mesh; dilated so filtering never reads the void.
 *  With `high`, p/n/curvature/ao come from the HIGH mesh under the texel (ray along the low normal),
 *  which is how a sculpt's wrinkles reach a low mesh's colour.
 *
 *  `fn` returns [r, g, b, a?] in 0..1 (a defaults to 1); the texel's objects are fresh each call.
 *  `distance` is how far from the low surface the high one is looked for (default 2% of the
 *  mesh's diagonal). `ao` traces `rays` (16) of up to `distance` (8% of the diagonal) against the
 *  high mesh or the target; `ao: false` skips it and reports 1. `curvature: false` reports 0.
 *  `curvatureRadius` (metres, default 0.01) is the width of a crease's band and the radius at which
 *  a rounded edge reads 0.76. `dilate` is how many texels the result grows past the islands
 *  (default: the whole image, so every mip level is clean). The target may carry the `normal` its
 *  geometry is drawn with; otherwise `smoothNormalsWelded` is assumed. */
export function bakeFunction(target: { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal?: Float32Array },
  size: number | [number, number], fn: (t: Texel) => [number, number, number, number?],
  opts: { high?: { pos: Float32Array; idx: Uint32Array }; distance?: number; ao?: false | { rays?: number; distance?: number };
           curvature?: boolean; dilate?: number; curvatureRadius?: number } = {}): Baked {
  const [W, H] = sizeOf(size);
  const part: Part = { ...(target as Target), high: opts.high };
  const S = surface([part], W, H, { ao: opts.ao, distance: opts.distance, curvature: opts.curvature, curvatureRadius: opts.curvatureRadius });
  const img: Baked = { width: W, height: H, data: new Uint8ClampedArray(W * H * 4) };
  const covered = new Uint8Array(W * H);
  const d = img.data;
  for (let ci = 0; ci < S.cov.length; ci++) {
    const k = S.cov[ci];
    const x = k % W, y = (k / W) | 0;
    const out = fn({
      p: [S.SP[ci * 3], S.SP[ci * 3 + 1], S.SP[ci * 3 + 2]],
      n: [S.SN[ci * 3], S.SN[ci * 3 + 1], S.SN[ci * 3 + 2]],
      uv: [(x + 0.5) / W, (y + 0.5) / H],
      tri: S.owner[k],
      curvature: S.SC[ci],
      ao: S.AO[ci],
    });
    const o = k * 4;
    d[o] = Math.round(clamp01(Number(out?.[0]) || 0) * 255);
    d[o + 1] = Math.round(clamp01(Number(out?.[1]) || 0) * 255);
    d[o + 2] = Math.round(clamp01(Number(out?.[2]) || 0) * 255);
    const a = out?.[3];
    d[o + 3] = Math.round(clamp01(a === undefined || a === null || !isFinite(a) ? 1 : a) * 255);
    covered[k] = 1;
  }
  const steps = opts.dilate === undefined ? Infinity : opts.dilate;
  applyFill(img, fillIndex(covered, W, H, steps), covered, [0, 0, 0, 0]);
  return img;
}

// ================================================================== masks and layers, compiled
interface Ctx { px: number; py: number; pz: number; nx: number; ny: number; nz: number; c: number; ao: number; id: number }
type MaskFn = (c: Ctx) => number;
/** One raw field value per texel, shared by every mask that reads the same field (same kind,
 *  frequency, octaves, seed, warp): a rust preset reads its patch field five times, at bands. */
type Slot = { id: number; v: number };
type Shared = Map<string, Slot>;
const slotFor = (shared: Shared, key: string): Slot => {
  let s = shared.get(key);
  if (!s) { s = { id: -1, v: 0 }; shared.set(key, s); }
  return s;
};

function compileMask(m: Mask | undefined, shared: Shared = new Map()): MaskFn | null {
  if (!m || typeof m !== "object") return null;
  const cheap: MaskFn[] = [], costly: MaskFn[] = [];
  if (m.curvature) {
    const { lo, hi } = m.curvature;
    if (lo > 0 && hi > 0) { const a = Math.min(lo, hi), b = Math.max(lo, hi); cheap.push((c) => sstep(a, b, c.c)); }
    else if (lo < 0 && hi < 0) { const a = Math.max(lo, hi), b = Math.min(lo, hi); cheap.push((c) => sstep(a, b, c.c)); }
    else cheap.push((c) => sstep(lo, hi, c.c));
  }
  if (m.ao) { const a = Math.min(m.ao.lo, m.ao.hi), b = Math.max(m.ao.lo, m.ao.hi); cheap.push((c) => 1 - sstep(a, b, c.ao)); }
  if (m.up) { const { lo, hi } = m.up; cheap.push((c) => sstep(lo, hi, c.ny)); }
  if (typeof m.region === "function") {
    const r = m.region;
    cheap.push((c) => clamp01(Number(r([c.px, c.py, c.pz], [c.nx, c.ny, c.nz])) || 0));
  }
  if (m.noise) {
    const { freq, octaves = 4, lo = 0.4, hi = 0.6, seed = 1, warp = 0 } = m.noise;
    const P = perm(seed), Pw = perm(seed + 7919), oct = Math.max(1, Math.min(8, Math.round(octaves)));
    const slot = slotFor(shared, "n|" + freq + "|" + oct + "|" + seed + "|" + warp);
    costly.push((c) => {
      if (slot.id !== c.id) {
        let x = c.px * freq, y = c.py * freq, z = c.pz * freq;
        if (warp) {
          const wx = fbmRaw(Pw, x + 5.2, y + 1.3, z + 7.7, 2), wy = fbmRaw(Pw, x + 9.1, y + 2.8, z + 3.4, 2), wz = fbmRaw(Pw, x + 4.6, y + 8.3, z + 0.9, 2);
          const s = warp / fbmSD(2) * 0.5;
          x += wx * s; y += wy * s; z += wz * s;
        }
        slot.v = fbmU(P, x, y, z, oct); slot.id = c.id;
      }
      return sstep(lo, hi, slot.v);
    });
  }
  if (m.streaks) {
    const { dir, freq, stretch, lo = 0.4, hi = 0.6, seed = 1 } = m.streaks;
    const dl = Math.hypot(dir[0], dir[1], dir[2]) || 1;
    const dx = dir[0] / dl, dy = dir[1] / dl, dz = dir[2] / dl;
    const k = 1 / Math.max(1e-3, stretch) - 1;
    const P = perm(seed + 104729);
    const slot = slotFor(shared, "s|" + dx + "|" + dy + "|" + dz + "|" + freq + "|" + stretch + "|" + seed);
    costly.push((c) => {
      if (slot.id !== c.id) {
        const x = c.px * freq, y = c.py * freq, z = c.pz * freq;
        const a = (x * dx + y * dy + z * dz) * k;
        slot.v = fbmU(P, x + dx * a, y + dy * a, z + dz * a, 3); slot.id = c.id;
      }
      return sstep(lo, hi, slot.v);
    });
  }
  if (m.voronoi) {
    const { cells, jitter = 1, mode = "spots", seed = 1 } = m.voronoi;
    const cracks = mode === "cracks";
    const lo = m.voronoi.lo ?? (cracks ? 0.75 : 0.35), hi = m.voronoi.hi ?? (cracks ? 0.92 : 0.6);
    const s = (seed * 7919 + 13) | 0;
    const slot = slotFor(shared, "v|" + cells + "|" + jitter + "|" + seed + "|" + mode);
    costly.push((c) => {
      if (slot.id !== c.id) {
        voronoi(c.px * cells, c.py * cells, c.pz * cells, jitter, s);
        if (cracks) slot.v = 1 - Math.min(1, (VOR_F2 - VOR_F1) * 4);
        else {
          const size = 0.3 + 0.7 * (hash32(VOR_ID | 0, 5, 9, s) / 4294967296);
          slot.v = Math.max(0, 1 - VOR_F1 / (0.55 * size));
        }
        slot.id = c.id;
      }
      return sstep(lo, hi, slot.v);
    });
  }
  const muls = (m.mul || []).map((x) => compileMask(x, shared)).filter((f): f is MaskFn => !!f);
  const maxs = (m.max || []).map((x) => compileMask(x, shared)).filter((f): f is MaskFn => !!f);
  const own = cheap.concat(costly, muls);
  const inv = !!m.invert;
  const nOwn = own.length, nMax = maxs.length;
  return (c) => {
    let v = 1;
    for (let i = 0; i < nOwn; i++) { v *= own[i](c); if (v <= 0) { v = 0; break; } }
    if (nMax) {
      let mv = nOwn ? v : 0;
      for (let i = 0; i < nMax && mv < 1; i++) { const x = maxs[i](c); if (x > mv) mv = x; }
      v = mv;
    }
    return inv ? 1 - v : v;
  };
}

interface CLayer { mask: MaskFn | null; opacity: number; col: [number, number, number] | null; blend: number; rough: number; metal: number; height: number; hasR: boolean; hasM: boolean; hasH: boolean }
interface CMat { base: [number, number, number]; rough: number; metal: number; layers: CLayer[]; bump: number }

const BLENDS: Record<string, number> = { normal: 0, multiply: 1, overlay: 2, screen: 3, add: 4 };

function compileMaterial(m: SmartMaterial, shared: Shared): CMat {
  const base = m && m.base ? m.base : { color: "#808080", roughness: 0.5 };
  return {
    base: hexToRgb(base.color),
    rough: clamp01(base.roughness ?? 0.5),
    metal: clamp01(base.metal ?? 0),
    bump: typeof m?.bump === "number" ? m.bump : 0.0004,
    layers: (m?.layers || []).map((L) => ({
      mask: compileMask(L.mask, shared),
      opacity: typeof L.opacity === "number" ? L.opacity : 1,
      col: L.color !== undefined ? hexToRgb(L.color) : null,
      blend: BLENDS[L.blend || "normal"] ?? 0,
      rough: clamp01(L.roughness ?? 0), metal: clamp01(L.metal ?? 0), height: L.height ?? 0,
      hasR: typeof L.roughness === "number", hasM: typeof L.metal === "number", hasH: typeof L.height === "number",
    })),
  };
}

const overlay = (a: number, b: number) => (a < 0.5 ? 2 * a * b : 1 - 2 * (1 - a) * (1 - b));

// ================================================================== bake a material, or many
function bakeParts(parts: Array<Part & { mat: SmartMaterial }>, W: number, H: number,
  opts: { ao?: AOOption; aoInColor?: number; distance?: number; curvatureRadius?: number; dilate?: number }): { baseColor: Baked; orm: Baked; normal: Baked } {
  const S = surface(parts, W, H, { ao: opts.ao, distance: opts.distance, curvatureRadius: opts.curvatureRadius });
  const shared: Shared = new Map();
  const mats = parts.map((p) => compileMaterial(p.mat, shared));
  const aoIn = clamp01(opts.aoInColor ?? 0.5);
  const nc = S.cov.length;
  const baseColor: Baked = { width: W, height: H, data: new Uint8ClampedArray(W * H * 4) };
  const orm: Baked = { width: W, height: H, data: new Uint8ClampedArray(W * H * 4) };
  const normal: Baked = { width: W, height: H, data: new Uint8ClampedArray(W * H * 4) };
  const height = new Float32Array(W * H);
  const covered = new Uint8Array(W * H);
  const ctx: Ctx = { px: 0, py: 0, pz: 0, nx: 0, ny: 1, nz: 0, c: 0, ao: 1, id: -1 };
  const bc = baseColor.data, om = orm.data;
  for (let ci = 0; ci < nc; ci++) {
    const k = S.cov[ci];
    const M = mats[S.triPart[S.owner[k]]];
    ctx.px = S.SP[ci * 3]; ctx.py = S.SP[ci * 3 + 1]; ctx.pz = S.SP[ci * 3 + 2];
    ctx.nx = S.SN[ci * 3]; ctx.ny = S.SN[ci * 3 + 1]; ctx.nz = S.SN[ci * 3 + 2];
    ctx.c = S.SC[ci]; ctx.ao = S.AO[ci]; ctx.id = ci;
    let r = M.base[0], g = M.base[1], b = M.base[2], ro = M.rough, me = M.metal, h = 0;
    for (let li = 0; li < M.layers.length; li++) {
      const L = M.layers[li];
      if (!(L.opacity > 0)) continue;
      let w = L.mask ? L.mask(ctx) : 1;
      w *= L.opacity;
      if (!(w > 0)) continue;
      if (w > 1) w = 1;
      if (L.col) {
        const lr = L.col[0], lg = L.col[1], lb = L.col[2];
        let tr = lr, tg = lg, tb = lb;
        switch (L.blend) {
          case 1: tr = r * lr; tg = g * lg; tb = b * lb; break;
          case 2: tr = overlay(r, lr); tg = overlay(g, lg); tb = overlay(b, lb); break;
          case 3: tr = 1 - (1 - r) * (1 - lr); tg = 1 - (1 - g) * (1 - lg); tb = 1 - (1 - b) * (1 - lb); break;
          case 4: tr = r + lr; tg = g + lg; tb = b + lb; break;
        }
        r += (tr - r) * w; g += (tg - g) * w; b += (tb - b) * w;
      }
      if (L.hasR) ro += (L.rough - ro) * w;
      if (L.hasM) me += (L.metal - me) * w;
      if (L.hasH) {
        if (L.blend === 4) h += L.height * w;
        else if (L.blend === 1) h += (h * L.height - h) * w;
        else h += (L.height - h) * w;
      }
    }
    const ao = S.AO[ci];
    const f = 1 - aoIn * (1 - ao);
    const o = k * 4;
    bc[o] = Math.round(clamp01(r * f) * 255); bc[o + 1] = Math.round(clamp01(g * f) * 255); bc[o + 2] = Math.round(clamp01(b * f) * 255); bc[o + 3] = 255;
    om[o] = Math.round(clamp01(ao) * 255); om[o + 1] = Math.round(clamp01(ro) * 255); om[o + 2] = Math.round(clamp01(me) * 255); om[o + 3] = 255;
    height[k] = h * M.bump;
    covered[k] = 1;
  }
  // The normal map: the high normal (if any) and the height's slope, in three's derivative frame,
  // written in the glTF convention (see the top of the file).
  const nd = normal.data;
  const triIsland = S.triIsland;
  const pu = new Float64Array(3), pv = new Float64Array(3);
  for (let ci = 0; ci < nc; ci++) {
    const k = S.cov[ci], x = k % W, y = (k / W) | 0;
    const g = S.owner[k], pi = S.triPart[g], t = g - S.triBase[pi];
    const P = parts[pi], pos = P.pos, idx = P.idx, uv = P.uv, Nn = S.normals[pi];
    const ia = idx[t * 3], ib = idx[t * 3 + 1], ic = idx[t * 3 + 2];
    // The triangle's UV Jacobian: dp/du and dp/dv.
    const e1x = pos[ib * 3] - pos[ia * 3], e1y = pos[ib * 3 + 1] - pos[ia * 3 + 1], e1z = pos[ib * 3 + 2] - pos[ia * 3 + 2];
    const e2x = pos[ic * 3] - pos[ia * 3], e2y = pos[ic * 3 + 1] - pos[ia * 3 + 1], e2z = pos[ic * 3 + 2] - pos[ia * 3 + 2];
    const du1 = uv[ib * 2] - uv[ia * 2], dv1 = uv[ib * 2 + 1] - uv[ia * 2 + 1], du2 = uv[ic * 2] - uv[ia * 2], dv2 = uv[ic * 2 + 1] - uv[ia * 2 + 1];
    const det = du1 * dv2 - du2 * dv1;
    const o = k * 4;
    covered[k] = 1;
    if (!(Math.abs(det) > 1e-20)) { nd[o] = 128; nd[o + 1] = 128; nd[o + 2] = 255; nd[o + 3] = 255; continue; }
    const r = 1 / det;
    pu[0] = (e1x * dv2 - e2x * dv1) * r; pu[1] = (e1y * dv2 - e2y * dv1) * r; pu[2] = (e1z * dv2 - e2z * dv1) * r;
    pv[0] = (e2x * du1 - e1x * du2) * r; pv[1] = (e2y * du1 - e1y * du2) * r; pv[2] = (e2z * du1 - e1z * du2) * r;
    const wb = S.b1[k], wc = S.b2[k], wa = 1 - wb - wc;
    let Nx = wa * Nn[ia * 3] + wb * Nn[ib * 3] + wc * Nn[ic * 3], Ny = wa * Nn[ia * 3 + 1] + wb * Nn[ib * 3 + 1] + wc * Nn[ic * 3 + 1], Nz = wa * Nn[ia * 3 + 2] + wb * Nn[ib * 3 + 2] + wc * Nn[ic * 3 + 2];
    const Nl = Math.hypot(Nx, Ny, Nz) || 1;
    Nx /= Nl; Ny /= Nl; Nz /= Nl;
    // three: T = sgn (dp/dv x N) / M, B = sgn (N x dp/du) / M, M the larger of the two lengths.
    let Tx = pv[1] * Nz - pv[2] * Ny, Ty = pv[2] * Nx - pv[0] * Nz, Tz = pv[0] * Ny - pv[1] * Nx;
    let Bx = Ny * pu[2] - Nz * pu[1], By = Nz * pu[0] - Nx * pu[2], Bz = Nx * pu[1] - Ny * pu[0];
    const D = Nx * (pu[1] * pv[2] - pu[2] * pv[1]) + Ny * (pu[2] * pv[0] - pu[0] * pv[2]) + Nz * (pu[0] * pv[1] - pu[1] * pv[0]);
    const lt = Math.hypot(Tx, Ty, Tz), lb = Math.hypot(Bx, By, Bz), Mx = Math.max(lt, lb);
    if (!(Mx > 1e-30) || !(Math.abs(D) > 1e-30)) { nd[o] = 128; nd[o + 1] = 128; nd[o + 2] = 255; nd[o + 3] = 255; continue; }
    const sg = D > 0 ? 1 : -1;
    Tx *= sg / Mx; Ty *= sg / Mx; Tz *= sg / Mx; Bx *= sg / Mx; By *= sg / Mx; Bz *= sg / Mx;
    // Base normal in that frame: the high normal, or straight out.
    let a = 0, bq = 0, cq = 1;
    if (S.SH[ci]) {
      const hx = S.SN[ci * 3], hy = S.SN[ci * 3 + 1], hz = S.SN[ci * 3 + 2];
      cq = hx * Nx + hy * Ny + hz * Nz;
      const qx = hx - cq * Nx, qy = hy - cq * Ny, qz = hz - cq * Nz;
      const tt = Tx * Tx + Ty * Ty + Tz * Tz, tb = Tx * Bx + Ty * By + Tz * Bz, bb2 = Bx * Bx + By * By + Bz * Bz;
      const rt = qx * Tx + qy * Ty + qz * Tz, rb = qx * Bx + qy * By + qz * Bz;
      const dd = tt * bb2 - tb * tb;
      if (Math.abs(dd) > 1e-30) { a = (rt * bb2 - rb * tb) / dd; bq = (rb * tt - rt * tb) / dd; }
      if (cq < 0.05) cq = 0.05;
    }
    // The height's slope, per UV unit, from neighbours on the same island.
    const isl = triIsland[g];
    const hAt = (xx: number, yy: number): number => {
      if (xx < 0 || yy < 0 || xx >= W || yy >= H) return NaN;
      const j = yy * W + xx, gg = S.owner[j];
      return gg >= 0 && triIsland[gg] === isl ? height[j] : NaN;
    };
    const h0 = height[k];
    const hl = hAt(x - 1, y), hr = hAt(x + 1, y), hd = hAt(x, y - 1), hu = hAt(x, y + 1);
    let hU = 0, hV = 0;
    if (hl === hl && hr === hr) hU = (hr - hl) * W * 0.5; else if (hr === hr) hU = (hr - h0) * W; else if (hl === hl) hU = (h0 - hl) * W;
    if (hd === hd && hu === hu) hV = (hu - hd) * H * 0.5; else if (hu === hu) hV = (hu - h0) * H; else if (hd === hd) hV = (h0 - hd) * H;
    // n' = n_base - grad h. grad u = (dp/dv x N) / D and grad v = (N x dp/du) / D, so three's
    // T = grad u / S and B = grad v / S with S = Mx / |D|: grad h = hU grad u + hV grad v = S (hU T + hV B).
    const Sg = Mx / Math.abs(D);
    const X = a - hU * Sg, Y = bq - hV * Sg, Z = cq;
    const L = Math.hypot(X, Y, Z) || 1;
    // glTF convention: green = -(the +v component).
    nd[o] = Math.round((X / L * 0.5 + 0.5) * 255);
    nd[o + 1] = Math.round((-Y / L * 0.5 + 0.5) * 255);
    nd[o + 2] = Math.round((Z / L * 0.5 + 0.5) * 255);
    nd[o + 3] = 255;
  }
  const steps = opts.dilate === undefined ? Infinity : opts.dilate;
  const src = fillIndex(covered, W, H, steps);
  applyFill(baseColor, src, covered, [128, 128, 128, 255]);
  applyFill(orm, src, covered, [255, 128, 0, 255]);
  applyFill(normal, src, covered, [128, 128, 255, 255]);
  return { baseColor, orm, normal };
}

/** Bake a smart material to base colour (sRGB), ORM (R occlusion, G roughness, B metal, linear) and a
 *  tangent-space normal map from the layers' heights (+ the high mesh's normals when given). The
 *  normal map is written in the glTF convention (+Y green = +V).
 *
 *  "+V" is V as OpenGL and Blender count it, up the image. In these UVs (glTF's: v = row / height,
 *  row 0 the top of the PNG) that is DECREASING v: green points toward the top of the picture.
 *  Put it on a flipY:false texture with normalScale (1, -1): `applyMaps` does. `aoInColor` (default 0.5)
 *  darkens the colour by the occlusion, colour x (1 - aoInColor x (1 - ao)), because three's aoMap
 *  only darkens ambient light and the bench has no shadows: without it cavities do not read.
 *  `ao` defaults to 16 rays reaching 8% of the diagonal, against the high mesh when given.
 *  Extra options as for bakeFunction: `distance`, `curvatureRadius`, `dilate`; the target may carry
 *  the `normal` its geometry is drawn with. */
export function bakeMaterial(target: { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal?: Float32Array },
  mat: SmartMaterial, size: number | [number, number],
  opts: { high?: { pos: Float32Array; idx: Uint32Array }; ao?: false | { rays?: number; distance?: number }; aoInColor?: number /* 0..1 */;
          distance?: number; curvatureRadius?: number; dilate?: number } = {}):
  { baseColor: Baked; orm: Baked; normal: Baked } {
  const [W, H] = sizeOf(size);
  return bakeParts([{ ...(target as Target), high: opts.high, mat }], W, H, opts);
}

/** Several parts, each with its own material, into ONE texture set in one shared atlas.
 *  Unwrap them together first (`atlasArrays`), so their islands share the square without overlap.
 *  Occlusion is traced against ALL parts, so the helmet darkens the face under its rim and the
 *  plates darken each other where they overlap. Each part may carry `high` (baked onto it) and the
 *  `normal` its geometry is drawn with (atlasArrays returns one). Options as for bakeMaterial. */
export function bakeAtlas(parts: Array<{ pos: Float32Array; idx: Uint32Array; uv: Float32Array; mat: SmartMaterial; high?: { pos: Float32Array; idx: Uint32Array }; normal?: Float32Array }>,
  size: number, opts: { ao?: false | { rays?: number; distance?: number }; aoInColor?: number;
    distance?: number; curvatureRadius?: number; dilate?: number } = {}): { baseColor: Baked; orm: Baked; normal: Baked } {
  const [W, H] = sizeOf(size);
  return bakeParts(parts as Array<Part & { mat: SmartMaterial }>, W, H, opts);
}

// ================================================================== the ready materials
type Opts = Record<string, unknown> | undefined;
const num = (o: Opts, k: string, d: number) => (o && typeof o[k] === "number" && isFinite(o[k] as number) ? (o[k] as number) : d);
const str = (o: Opts, k: string, d: string) => (o && typeof o[k] === "string" ? (o[k] as string) : d);
const vec = (o: Opts, k: string, d: V3): V3 => {
  const v = o && o[k];
  return Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number") ? (v as V3) : d;
};

/** Shared by the metals: grease and handling marks in the roughness, fine scratches, dents. */
function metalWear(sc: number, seed: number, scratch: string, amount: number): Layer[] {
  return [
    { name: "smudges", roughness: 0.62, mask: { noise: { freq: 6 / sc, octaves: 4, lo: 0.5, hi: 0.85, seed } }, opacity: 0.7 },
    { name: "dents", height: -1, blend: "add", mask: { voronoi: { cells: 14 / sc, mode: "spots", lo: 0.0, hi: 0.9, seed: seed + 2 } }, opacity: 0.7 },
    { name: "scratches", color: scratch, roughness: 0.24, height: -0.25,
      mask: { max: [
        { streaks: { dir: [0.35, 1, 0.15], freq: 90 / sc, stretch: 30, lo: 0.955, hi: 0.975, seed: seed + 3 } },
        { streaks: { dir: [1, -0.2, 0.4], freq: 70 / sc, stretch: 26, lo: 0.96, hi: 0.98, seed: seed + 4 } },
      ] }, opacity: 0.8 * amount },
  ];
}

/** Ready materials, each a function of a few colours: steel, rustySteel, paintedSteel, leather, wood,
 *  cloth, skin, bone, gold, stone. `skin` darkens and warms cavities and reddens thin convex places.
 *
 *  Every one takes `scale` (1 = tuned for a figure about a metre tall: frequencies divide by it,
 *  relief multiplies), `seed`, `dirt` and `wear` (0..1+, amounts), and its colours: `color` for
 *  all, plus steel/rustySteel `rust`, paintedSteel `paint` and `metal`, wood `dir` (grain axis),
 *  cloth `dir`, skin `flush` (the red of thin places) and `cavity`, stone `moss` (0..1), and
 *  rustySteel `rustAmount` (0..1, default 0.35). Returns a SmartMaterial, so any field can be
 *  changed or a layer added before baking. */
export const MATERIALS: Record<string, (o?: Record<string, unknown>) => SmartMaterial> = {
  steel: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 11), wear = num(o, "wear", 1), dirt = num(o, "dirt", 1);
    return {
      // Satin, not chrome: a light warm grey with a moderate metal, which reads as steel under a
      // studio rig with or without an environment map (the goblin reference's helmet).
      base: { color: str(o, "color", "#a8a7a3"), roughness: 0.42, metal: 0.6 },
      bump: 0.0004 * sc,
      layers: [
        { name: "tone", color: "#8c8b87", mask: { noise: { freq: 3 / sc, octaves: 3, lo: 0.35, hi: 0.75, seed: seed + 9 } }, opacity: 0.5 },
        ...metalWear(sc, seed, "#d0cdc8", 1),
        { name: "pits", color: "#57524c", height: -1, roughness: 0.6,
          mask: { voronoi: { cells: 260 / sc, mode: "spots", lo: 0.6, hi: 0.78, seed: seed + 6 } }, opacity: 0.55 },
        { name: "edge wear", color: "#d8d6d2", roughness: 0.24, metal: 0.8,
          mask: { curvature: { lo: 0.2, hi: 0.55 }, mul: [{ noise: { freq: 45 / sc, lo: 0.2, hi: 0.55, seed: seed + 5 } }] }, opacity: wear },
        { name: "cavity grime", color: "#2c2824", roughness: 0.85, metal: 0.1,
          mask: { max: [{ curvature: { lo: -0.1, hi: -0.55 } }, { ao: { lo: 0.4, hi: 0.85 } }] }, opacity: 0.85 * dirt },
      ],
    };
  },
  rustySteel: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 21), wear = num(o, "wear", 1), dirt = num(o, "dirt", 1);
    const cover = Math.max(0, Math.min(1, num(o, "rustAmount", 0.35)));
    const rust = str(o, "rust", "#9c5226");
    // Where rust may be: one broad noise field; blotches, flecks and patches all live inside it.
    const zone = (lo: number, hi: number): Mask => ({ noise: { freq: 3.5 / sc, octaves: 4, lo, hi, warp: 0.5, seed: seed + 10 } });
    const t = 1 - cover;
    // Rust blotches: voronoi cells of random size (the Blender builder's "random-cell voronoi dots").
    const blot = (lo: number, hi: number): Mask => ({ voronoi: { cells: 34 / sc, mode: "spots", lo, hi, seed: seed + 13 } });
    const ragged: Mask = { noise: { freq: 70 / sc, octaves: 3, lo: 0.2, hi: 0.5, seed: seed + 18 } };
    // Larger patches where the corrosion joined up; ragged and mottled inside, not a flat colour.
    const patch: Mask = { noise: { freq: 7 / sc, octaves: 5, lo: 1 - cover * 0.45, hi: 1 - cover * 0.3, warp: 0.6, seed: seed + 11 } };
    return {
      base: { color: str(o, "color", "#a3a19d"), roughness: 0.52, metal: 0.5 },
      bump: 0.0005 * sc,
      layers: [
        { name: "tone", color: "#878580", mask: { noise: { freq: 3 / sc, octaves: 3, lo: 0.3, hi: 0.8, seed: seed + 9 } }, opacity: 0.55 },
        ...metalWear(sc, seed, "#cfcbc5", 0.6),
        { name: "pits", color: "#4e4740", height: -1.2, roughness: 0.7,
          mask: { voronoi: { cells: 240 / sc, mode: "spots", lo: 0.55, hi: 0.75, seed: seed + 6 } }, opacity: 0.7 },
        { name: "stain", color: "#b08a68", blend: "multiply", roughness: 0.66, mask: zone(t - 0.25, t + 0.1), opacity: 0.4 },
        { name: "rust blotches", color: rust, roughness: 0.9, metal: 0.05, height: -0.4, mask: { mul: [blot(0.42, 0.66), zone(t - 0.25, t), ragged] } },
        { name: "rust cores", color: "#5c2d16", roughness: 0.95, metal: 0.02, height: -0.8, mask: { mul: [blot(0.8, 0.94), zone(t - 0.2, t)] } },
        { name: "rust flecks", color: "#a9592a", roughness: 0.9, metal: 0.05, height: -0.5,
          mask: { mul: [{ voronoi: { cells: 110 / sc, mode: "spots", lo: 0.58, hi: 0.78, seed: seed + 16 } }, zone(t - 0.35, t - 0.05)] } },
        { name: "rust patches", color: "#7e4121", roughness: 0.92, metal: 0.04, height: -0.35,
          mask: { mul: [patch, { noise: { freq: 45 / sc, octaves: 3, lo: 0.12, hi: 0.4, seed: seed + 19 } }] } },
        { name: "rust mottling", color: "#57290f", roughness: 0.95,
          mask: { mul: [patch, { noise: { freq: 28 / sc, octaves: 4, lo: 0.5, hi: 0.8, seed: seed + 20 } }] }, opacity: 0.7 },
        { name: "rust grain", height: 0.6, blend: "add",
          mask: { mul: [{ max: [blot(0.55, 0.75), patch] },
                        { noise: { freq: 300 / sc, octaves: 2, lo: 0.3, hi: 0.8, seed: seed + 17 } }] } },
        { name: "rust in cavities", color: "#6a341a", roughness: 0.95, metal: 0.02, height: -0.3,
          mask: { mul: [{ max: [{ ao: { lo: 0.5, hi: 0.85 } }, { curvature: { lo: -0.08, hi: -0.45 } }] },
                        { noise: { freq: 16 / sc, octaves: 3, lo: 0.3, hi: 0.6, seed: seed + 12 } }] } },
        { name: "rust streaks", color: "#9a6a48", roughness: 0.7, blend: "multiply",
          mask: { streaks: { dir: [0, 1, 0], freq: 60 / sc, stretch: 14, lo: 0.8, hi: 0.93, seed: seed + 15 },
                  mul: [{ up: { lo: 0.6, hi: 0.1 } }, zone(t - 0.12, t + 0.05)] }, opacity: 0.4 },
        { name: "edge wear", color: "#d3d0cb", roughness: 0.28, metal: 0.75, height: 0.1,
          mask: { curvature: { lo: 0.25, hi: 0.6 }, mul: [{ noise: { freq: 40 / sc, lo: 0.2, hi: 0.55, seed: seed + 5 } }] }, opacity: wear },
        { name: "dirt", color: "#2a2420", roughness: 0.92, metal: 0.05,
          mask: { max: [{ ao: { lo: 0.35, hi: 0.75 } }, { curvature: { lo: -0.35, hi: -0.8 } }] }, opacity: 0.75 * dirt },
      ],
    };
  },
  paintedSteel: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 31), wear = num(o, "wear", 1), dirt = num(o, "dirt", 1);
    const paint = str(o, "paint", str(o, "color", "#6b221c"));
    // Chips: along the convex edges, and small irregular flakes where a broad noise allows them.
    const chips: Mask = { max: [
      { curvature: { lo: 0.15, hi: 0.45 }, mul: [{ noise: { freq: 34 / sc, lo: 0.3, hi: 0.6, seed: seed + 5 } }] },
      { mul: [{ noise: { freq: 32 / sc, octaves: 3, lo: 0.93 - 0.04 * wear, hi: 0.95 - 0.04 * wear, warp: 0.35, seed: seed + 6 } },
              { noise: { freq: 4 / sc, octaves: 2, lo: 0.45, hi: 0.7, seed: seed + 7 } }] },
    ] };
    return {
      base: { color: str(o, "metal", "#6e7277"), roughness: 0.48, metal: 0.8 },
      bump: 0.0004 * sc,
      layers: [
        ...metalWear(sc, seed, "#a9aeb3", 0.3),
        { name: "paint", color: paint, roughness: 0.55, metal: 0, height: 0.4, mask: { ...chips, invert: true } },
        { name: "paint fade", color: "#ffffff", blend: "overlay", mask: { up: { lo: 0.2, hi: 0.9 }, mul: [{ ...chips, invert: true }] }, opacity: 0.25 },
        { name: "paint mottling", color: "#000000", blend: "overlay", mask: { noise: { freq: 8 / sc, octaves: 4, lo: 0.4, hi: 0.9, seed: seed + 9 }, mul: [{ ...chips, invert: true }] }, opacity: 0.2 },
        { name: "rust in chips", color: "#7a3c1c", roughness: 0.9, metal: 0.05,
          mask: { mul: [chips, { noise: { freq: 50 / sc, lo: 0.35, hi: 0.65, seed: seed + 8 } }] }, opacity: 0.8 },
        { name: "dirt", color: "#2b2521", roughness: 0.9, metal: 0.05,
          mask: { max: [{ ao: { lo: 0.4, hi: 0.8 } }, { curvature: { lo: -0.2, hi: -0.7 } }] }, opacity: 0.75 * dirt },
      ],
    };
  },
  leather: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 41), wear = num(o, "wear", 1), dirt = num(o, "dirt", 1);
    const color = str(o, "color", "#6a4128");
    return {
      base: { color, roughness: 0.62, metal: 0 },
      bump: 0.0003 * sc,
      layers: [
        { name: "mottling", color: "#4e2e1b", mask: { noise: { freq: 7 / sc, octaves: 4, lo: 0.35, hi: 0.85, seed } }, opacity: 0.55 },
        { name: "grain", height: -0.6, blend: "add", mask: { voronoi: { cells: 420 / sc, mode: "cracks", lo: 0.55, hi: 0.95, seed: seed + 1 } }, opacity: 0.8 },
        { name: "pores", height: 0.5, blend: "add", mask: { noise: { freq: 700 / sc, octaves: 2, lo: 0.3, hi: 0.9, seed: seed + 2 } }, opacity: 0.6 },
        { name: "creases", color: "#3a2214", roughness: 0.7, height: -1,
          mask: { voronoi: { cells: 30 / sc, mode: "cracks", lo: 0.82, hi: 0.95, seed: seed + 3 }, mul: [{ noise: { freq: 9 / sc, lo: 0.4, hi: 0.7, seed: seed + 4 } }] }, opacity: 0.8 },
        { name: "scuffs", color: "#9a6c48", roughness: 0.45,
          mask: { curvature: { lo: 0.15, hi: 0.5 }, mul: [{ noise: { freq: 35 / sc, lo: 0.3, hi: 0.65, seed: seed + 5 } }] }, opacity: 0.85 * wear },
        { name: "dirt", color: "#22170f", roughness: 0.8,
          mask: { max: [{ ao: { lo: 0.4, hi: 0.85 } }, { curvature: { lo: -0.15, hi: -0.6 } }] }, opacity: 0.7 * dirt },
      ],
    };
  },
  wood: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 51), wear = num(o, "wear", 1), dirt = num(o, "dirt", 1);
    const color = str(o, "color", "#7a5332"), dir = vec(o, "dir", [0, 1, 0]);
    return {
      base: { color, roughness: 0.72, metal: 0 },
      bump: 0.0005 * sc,
      layers: [
        { name: "boards", color: "#5e3d22", mask: { noise: { freq: 4 / sc, octaves: 2, lo: 0.3, hi: 0.8, seed } }, opacity: 0.6 },
        { name: "grain", color: "#4a2f19", roughness: 0.8, height: -0.6,
          mask: { streaks: { dir, freq: 60 / sc, stretch: 18, lo: 0.58, hi: 0.78, seed: seed + 1 } } },
        { name: "fine grain", color: "#3f2714", height: -0.4,
          mask: { streaks: { dir, freq: 180 / sc, stretch: 30, lo: 0.7, hi: 0.9, seed: seed + 2 } }, opacity: 0.6 },
        { name: "knots", color: "#3a2212", roughness: 0.6, height: -0.8,
          mask: { voronoi: { cells: 9 / sc, mode: "spots", lo: 0.72, hi: 0.86, seed: seed + 3 } } },
        { name: "edge wear", color: "#a47a52", roughness: 0.55, mask: { curvature: { lo: 0.2, hi: 0.55 } }, opacity: 0.8 * wear },
        { name: "dirt", color: "#20150c", roughness: 0.85,
          mask: { max: [{ ao: { lo: 0.4, hi: 0.85 } }, { curvature: { lo: -0.15, hi: -0.6 } }] }, opacity: 0.75 * dirt },
      ],
    };
  },
  cloth: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 61), wear = num(o, "wear", 1), dirt = num(o, "dirt", 1);
    const color = str(o, "color", "#8a2c24");
    const d = vec(o, "dir", [0, 1, 0]);
    const cx: V3 = Math.abs(d[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0];
    return {
      base: { color, roughness: 0.92, metal: 0 },
      bump: 0.00025 * sc,
      layers: [
        { name: "dye", color: "#000000", blend: "overlay", mask: { noise: { freq: 6 / sc, octaves: 4, lo: 0.3, hi: 0.9, seed } }, opacity: 0.35 },
        { name: "weave", color: "#000000", blend: "overlay", height: -0.8,
          mask: { max: [
            { streaks: { dir: d, freq: 380 / sc, stretch: 40, lo: 0.45, hi: 0.75, seed: seed + 1 } },
            { streaks: { dir: cx, freq: 380 / sc, stretch: 40, lo: 0.45, hi: 0.75, seed: seed + 2 } },
          ] }, opacity: 0.35 },
        { name: "fuzz", color: "#ffffff", blend: "overlay", roughness: 1,
          mask: { curvature: { lo: 0.1, hi: 0.5 }, mul: [{ noise: { freq: 60 / sc, lo: 0.2, hi: 0.7, seed: seed + 3 } }] }, opacity: 0.35 * wear },
        { name: "stains", color: "#2c1a12", blend: "multiply", mask: { noise: { freq: 5 / sc, octaves: 5, lo: 0.8, hi: 0.92, warp: 0.7, seed: seed + 4 } }, opacity: 0.45 * dirt },
        { name: "dirt", color: "#1f140e", roughness: 0.95,
          mask: { max: [{ ao: { lo: 0.4, hi: 0.85 } }, { curvature: { lo: -0.15, hi: -0.6 } }] }, opacity: 0.7 * dirt },
      ],
    };
  },
  skin: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 71), dirt = num(o, "dirt", 1);
    const color = str(o, "color", "#c69a7c");
    const flush = str(o, "flush", "#d4745a"), cavity = str(o, "cavity", "#6a3824");
    return {
      base: { color, roughness: 0.58, metal: 0 },
      bump: 0.00015 * sc,
      layers: [
        { name: "mottling", color: "#000000", blend: "overlay", mask: { noise: { freq: 9 / sc, octaves: 4, lo: 0.25, hi: 0.95, seed } }, opacity: 0.22 },
        { name: "blotches", color: flush, mask: { noise: { freq: 5 / sc, octaves: 4, lo: 0.62, hi: 0.92, warp: 0.5, seed: seed + 1 } }, opacity: 0.2 },
        { name: "pores", height: -0.8, blend: "add", roughness: 0.64, mask: { voronoi: { cells: 900 / sc, mode: "spots", lo: 0.5, hi: 0.8, seed: seed + 2 } }, opacity: 0.8 },
        // Thin convex places (nose, ear rims, knuckles) flush toward a warm red.
        { name: "flush", color: flush, roughness: 0.5, mask: { curvature: { lo: 0.12, hi: 0.55 } }, opacity: 0.65 },
        // Cavities darken AND warm: a multiply by a warm brown, not a grey.
        { name: "cavities", color: cavity, blend: "multiply", roughness: 0.66,
          mask: { max: [{ curvature: { lo: -0.08, hi: -0.6 } }, { ao: { lo: 0.45, hi: 0.9 } }] }, opacity: 0.8 },
        { name: "grime", color: "#3a2a1c", roughness: 0.75, mask: { ao: { lo: 0.3, hi: 0.65 } }, opacity: 0.5 * dirt },
      ],
    };
  },
  bone: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 81), dirt = num(o, "dirt", 1), wear = num(o, "wear", 1);
    const color = str(o, "color", "#d8ccae");
    return {
      base: { color, roughness: 0.55, metal: 0 },
      bump: 0.00025 * sc,
      layers: [
        { name: "yellowing", color: "#b39a64", mask: { noise: { freq: 5 / sc, octaves: 4, lo: 0.35, hi: 0.9, seed } }, opacity: 0.5 },
        { name: "stains", color: "#7a6040", blend: "multiply", mask: { streaks: { dir: [0, 1, 0], freq: 30 / sc, stretch: 6, lo: 0.65, hi: 0.9, seed: seed + 1 } }, opacity: 0.35 },
        { name: "pores", height: -0.8, blend: "add", mask: { voronoi: { cells: 260 / sc, mode: "spots", lo: 0.45, hi: 0.75, seed: seed + 2 } }, opacity: 0.7 },
        { name: "cracks", color: "#5a4630", height: -1, roughness: 0.7, mask: { voronoi: { cells: 18 / sc, mode: "cracks", lo: 0.9, hi: 0.97, seed: seed + 3 } }, opacity: 0.7 },
        { name: "polish", color: "#f0e8d4", roughness: 0.35, mask: { curvature: { lo: 0.2, hi: 0.6 } }, opacity: 0.6 * wear },
        { name: "dirt", color: "#3c2c1a", roughness: 0.8, mask: { max: [{ ao: { lo: 0.45, hi: 0.9 } }, { curvature: { lo: -0.1, hi: -0.6 } }] }, opacity: 0.8 * dirt },
      ],
    };
  },
  gold: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 91), dirt = num(o, "dirt", 1), wear = num(o, "wear", 1);
    return {
      base: { color: str(o, "color", "#d4a544"), roughness: 0.3, metal: 1 },
      bump: 0.0003 * sc,
      layers: [
        ...metalWear(sc, seed, "#f2d27a", 0.6),
        { name: "polish", color: "#f4d27c", roughness: 0.16, mask: { curvature: { lo: 0.15, hi: 0.5 } }, opacity: 0.9 * wear },
        { name: "tarnish", color: "#3f2e12", roughness: 0.7, metal: 0.4,
          mask: { max: [{ ao: { lo: 0.45, hi: 0.9 } }, { curvature: { lo: -0.08, hi: -0.5 } }] }, opacity: 0.85 * dirt },
      ],
    };
  },
  stone: (o) => {
    const sc = num(o, "scale", 1), seed = num(o, "seed", 101), dirt = num(o, "dirt", 1), wear = num(o, "wear", 1), moss = num(o, "moss", 0);
    const color = str(o, "color", "#8a857c");
    const layers: Layer[] = [
      { name: "tone", color: "#6d685f", mask: { noise: { freq: 3 / sc, octaves: 5, lo: 0.3, hi: 0.85, warp: 0.5, seed } }, opacity: 0.7 },
      { name: "speckle", color: "#b3aea4", mask: { voronoi: { cells: 160 / sc, mode: "spots", lo: 0.6, hi: 0.8, seed: seed + 1 } }, opacity: 0.6 },
      { name: "grit", height: 0.8, blend: "add", mask: { noise: { freq: 220 / sc, octaves: 3, lo: 0.2, hi: 0.9, seed: seed + 2 } }, opacity: 0.7 },
      { name: "pits", height: -1.2, blend: "add", color: "#4a463f", mask: { voronoi: { cells: 60 / sc, mode: "spots", lo: 0.6, hi: 0.8, seed: seed + 3 } }, opacity: 0.8 },
      { name: "cracks", color: "#3a3630", height: -1.5, roughness: 0.95,
        mask: { voronoi: { cells: 7 / sc, mode: "cracks", lo: 0.9, hi: 0.975, seed: seed + 4 }, mul: [{ noise: { freq: 5 / sc, lo: 0.3, hi: 0.6, seed: seed + 5 } }] } },
      { name: "chipped edges", color: "#b0aba0", roughness: 0.8, mask: { curvature: { lo: 0.2, hi: 0.6 } }, opacity: 0.7 * wear },
      { name: "dirt", color: "#2a2620", roughness: 0.95, mask: { max: [{ ao: { lo: 0.4, hi: 0.85 } }, { curvature: { lo: -0.15, hi: -0.6 } }] }, opacity: 0.8 * dirt },
    ];
    if (moss > 0) layers.push({ name: "moss", color: "#4d5d2a", roughness: 0.95, height: 0.6,
      mask: { up: { lo: 0.25, hi: 0.8 }, mul: [{ noise: { freq: 6 / sc, octaves: 5, lo: 1 - moss * 0.8, hi: 1.05 - moss * 0.6, warp: 0.6, seed: seed + 6 } }] } });
    return { base: { color, roughness: 0.88, metal: 0 }, bump: 0.0006 * sc, layers };
  },
};

// ================================================================== onto a three material
/** Put the maps on a three material with the right colour spaces and the right normal-map sign for a
 *  flipY:false DataTexture, so the forge render AND the exported GLB look the same.
 *
 *  baseColor -> `map` (SRGBColorSpace); orm -> `aoMap`, `roughnessMap` and `metalnessMap` (one
 *  linear texture, as glTF packs it; roughness and metalness set to 1 so the map rules); normal ->
 *  `normalMap` (linear) with normalScale (s, -s), s = opts.normalScale (default 1). Why -s: the
 *  map is glTF-convention (green up the image), three's frame on a mesh without tangents (or with
 *  three's computeTangents) has its bitangent along +v, which is DOWN a flipY:false image; see the
 *  top of material.ts for the exporter and loader halves. Textures are flipY:false, linear
 *  filtered with mipmaps (a DataTexture defaults to nearest and none), clamped at the edges.
 *  `THREE` is the engine module the material belongs to. Returns the material. */
export function applyMaps(THREE: any, material: any, maps: { baseColor?: Baked; orm?: Baked; normal?: Baked },
  opts: { normalScale?: number; anisotropy?: number } = {}): any {
  const T = THREE;
  const tex = (b: Baked, srgb: boolean) => {
    const t = new T.DataTexture(b.data, b.width, b.height, T.RGBAFormat);
    t.flipY = false;
    if (srgb) t.colorSpace = T.SRGBColorSpace ?? t.colorSpace;
    else if (T.NoColorSpace !== undefined) t.colorSpace = T.NoColorSpace;
    t.magFilter = T.LinearFilter;
    t.minFilter = T.LinearMipmapLinearFilter;
    t.generateMipmaps = true;
    t.wrapS = t.wrapT = T.ClampToEdgeWrapping;
    t.anisotropy = opts.anisotropy ?? 4;
    t.needsUpdate = true;
    return t;
  };
  if (maps.baseColor) {
    material.map = tex(maps.baseColor, true);
    if (material.color && typeof material.color.set === "function") material.color.set(0xffffff);
  }
  if (maps.orm) {
    const t = tex(maps.orm, false);
    material.aoMap = t;
    if ("roughnessMap" in material || material.isMeshStandardMaterial) { material.roughnessMap = t; material.roughness = 1; }
    if ("metalnessMap" in material || material.isMeshStandardMaterial) { material.metalnessMap = t; material.metalness = 1; }
  }
  if (maps.normal) {
    material.normalMap = tex(maps.normal, false);
    const s = typeof opts.normalScale === "number" ? opts.normalScale : 1;
    if (material.normalScale && typeof material.normalScale.set === "function") material.normalScale.set(s, -s);
    else if (T.Vector2) material.normalScale = new T.Vector2(s, -s);
    if (T.TangentSpaceNormalMap !== undefined) material.normalMapType = T.TangentSpaceNormalMap;
  }
  material.needsUpdate = true;
  return material;
}
