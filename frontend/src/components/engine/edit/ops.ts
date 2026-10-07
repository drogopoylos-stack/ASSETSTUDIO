// The operations library: what an agent writing procedural code has instead of Blender's
// modifier stack.
//
// This is the gap that mattered. An agent can already build shapes out of primitives — what it
// could not do was the twenty operations that turn a pile of primitives into a model: weld the
// seams, smooth by angle, subdivide, mirror, array, deform, displace, scatter, solidify, cut a
// hole. Blender has all of them and calls them modifiers. Godot has CSG. Unity has ProBuilder.
// Three.js has none of them, which is why hand-written procedural assets look hand-written.
//
// Two rules shaped the file:
//
//   1. THE ALGORITHMS TAKE PLAIN ARRAYS. Every core routine works on Float32Array positions and
//      a Uint32Array index, with a thin three.js wrapper beside it. That is testable in node with
//      no browser and no engine, which for geometry code — where an off-by-one in a winding order
//      is invisible until it is rendered — is the difference between tested and hoped-for.
//   2. AGENTS CANNOT LOOK. `check()` returns a defect report as numbers: degenerate triangles,
//      unwelded seams, inverted winding, missing normals and UVs. Blender makes a human look at
//      a screen; this hands the same judgement to something that cannot see one.

export type V3 = [number, number, number];
export interface Mesh { pos: Float32Array; idx: Uint32Array; normal?: Float32Array; uv?: Float32Array }

const EPS = 1e-9;

// ------------------------------------------------------------------ small maths
/** Deterministic noise needs a deterministic source. Mulberry32: tiny, fast, well distributed. */
export function rng(seed = 1): () => number {
  let a = (seed >>> 0) || 1;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10);
const hash3 = (x: number, y: number, z: number, seed: number) => {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(z | 0, 2147483647) ^ seed;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};

/** Value noise in three dimensions, in 0..1. Enough for scales, bark, rock and dents. */
export function noise3(x: number, y: number, z: number, seed = 1): number {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
  const xf = fade(x - xi), yf = fade(y - yi), zf = fade(z - zi);
  const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
  const c = (dx: number, dy: number, dz: number) => hash3(xi + dx, yi + dy, zi + dz, seed);
  const x00 = lerp(c(0, 0, 0), c(1, 0, 0), xf);
  const x10 = lerp(c(0, 1, 0), c(1, 1, 0), xf);
  const x01 = lerp(c(0, 0, 1), c(1, 0, 1), xf);
  const x11 = lerp(c(0, 1, 1), c(1, 1, 1), xf);
  return lerp(lerp(x00, x10, yf), lerp(x01, x11, yf), zf);
}

/** Several octaves of it, which is what makes a surface look like a material rather than a pattern. */
export function fbm(x: number, y: number, z: number, octaves = 4, gain = 0.5, lacunarity = 2, seed = 1): number {
  let amp = 1, freq = 1, sum = 0, norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amp * noise3(x * freq, y * freq, z * freq, seed + i * 977);
    norm += amp;
    amp *= gain;
    freq *= lacunarity;
  }
  return norm > 0 ? sum / norm : 0;
}

// ------------------------------------------------------------------ indexing
/** Give a non-indexed soup an index, so the topology operations have something to work with. */
export function indexOf(pos: Float32Array, idx?: Uint32Array | null): Uint32Array {
  if (idx && idx.length) return idx;
  const n = pos.length / 3;
  const out = new Uint32Array(n);
  for (let i = 0; i < n; i++) out[i] = i;
  return out;
}

/**
 * Merge vertices that sit within `tol` of each other — Blender's Merge by Distance.
 *
 * THE operation. A creature built from separate primitives has a crack at every join, and no
 * amount of material work hides a seam that is really there: the light finds it, the silhouette
 * finds it, and smooth shading cannot cross it because the two sides do not share a vertex.
 *
 * The cell size is the tolerance, and the 27 neighbouring cells are searched, so a pair that
 * straddles a cell boundary still finds each other. A plain hash of the rounded coordinate —
 * which is the obvious implementation — misses exactly those pairs and welds about half of them.
 */
export function weldArrays(pos: Float32Array, idx: Uint32Array, tol = 1e-4):
  { pos: Float32Array; idx: Uint32Array; map: Uint32Array; merged: number } {
  const n = pos.length / 3;
  const cell = Math.max(tol, 1e-7);
  const buckets = new Map<string, number[]>();
  const map = new Uint32Array(n);
  const keep: number[] = [];
  const tol2 = tol * tol;

  for (let i = 0; i < n; i++) {
    const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
    const cx = Math.floor(x / cell), cy = Math.floor(y / cell), cz = Math.floor(z / cell);
    let found = -1;
    for (let dx = -1; dx <= 1 && found < 0; dx++) {
      for (let dy = -1; dy <= 1 && found < 0; dy++) {
        for (let dz = -1; dz <= 1 && found < 0; dz++) {
          const b = buckets.get((cx + dx) + "," + (cy + dy) + "," + (cz + dz));
          if (!b) continue;
          for (const j of b) {
            const ox = pos[j * 3] - x, oy = pos[j * 3 + 1] - y, oz = pos[j * 3 + 2] - z;
            if (ox * ox + oy * oy + oz * oz <= tol2) { found = j; break; }
          }
        }
      }
    }
    if (found >= 0) { map[i] = map[found]; continue; }
    const key = cx + "," + cy + "," + cz;
    let b = buckets.get(key);
    if (!b) { b = []; buckets.set(key, b); }
    b.push(i);
    map[i] = keep.length;
    keep.push(i);
  }

  const outPos = new Float32Array(keep.length * 3);
  for (let k = 0; k < keep.length; k++) {
    const s = keep[k] * 3;
    outPos[k * 3] = pos[s];
    outPos[k * 3 + 1] = pos[s + 1];
    outPos[k * 3 + 2] = pos[s + 2];
  }
  // A triangle whose corners collapsed onto each other is no longer a triangle. Dropping it here
  // is what stops a welded mesh reporting thousands of zero-area faces afterwards.
  const outIdx: number[] = [];
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const a = map[idx[t]], b = map[idx[t + 1]], c = map[idx[t + 2]];
    if (a === b || b === c || a === c) continue;
    outIdx.push(a, b, c);
  }
  return { pos: outPos, idx: Uint32Array.from(outIdx), map, merged: n - keep.length };
}

// ------------------------------------------------------------------ normals
export function faceNormal(pos: Float32Array, a: number, b: number, c: number): V3 {
  const ax = pos[a * 3], ay = pos[a * 3 + 1], az = pos[a * 3 + 2];
  const e1x = pos[b * 3] - ax, e1y = pos[b * 3 + 1] - ay, e1z = pos[b * 3 + 2] - az;
  const e2x = pos[c * 3] - ax, e2y = pos[c * 3 + 1] - ay, e2z = pos[c * 3 + 2] - az;
  const nx = e1y * e2z - e1z * e2y;
  const ny = e1z * e2x - e1x * e2z;
  const nz = e1x * e2y - e1y * e2x;
  const l = Math.hypot(nx, ny, nz) || 1;
  return [nx / l, ny / l, nz / l];
}

/**
 * Blender's Shade Auto Smooth: average the neighbours of a face only where the crease between
 * them is gentler than `angle`. A hard edge stays hard and a curve goes smooth, in one pass.
 *
 * Averaging everything is what "computeVertexNormals" does, and it rounds off the corners of a
 * box; averaging nothing is flat shading, and it facets a sphere. Neither is what anybody wants,
 * which is why every serious tool has this operation and three.js does not.
 */
export function smoothNormalsArrays(pos: Float32Array, idx: Uint32Array, angleDeg = 40): Float32Array {
  const tris = idx.length / 3;
  const fn = new Float32Array(tris * 3);
  for (let t = 0; t < tris; t++) {
    const n = faceNormal(pos, idx[t * 3], idx[t * 3 + 1], idx[t * 3 + 2]);
    fn[t * 3] = n[0]; fn[t * 3 + 1] = n[1]; fn[t * 3 + 2] = n[2];
  }
  // Faces touching each vertex.
  const at: number[][] = [];
  for (let t = 0; t < tris; t++) {
    for (let k = 0; k < 3; k++) {
      const v = idx[t * 3 + k];
      (at[v] || (at[v] = [])).push(t);
    }
  }
  const cosLimit = Math.cos((angleDeg * Math.PI) / 180);
  const out = new Float32Array(pos.length);
  for (let t = 0; t < tris; t++) {
    for (let k = 0; k < 3; k++) {
      const v = idx[t * 3 + k];
      let nx = 0, ny = 0, nz = 0;
      for (const o of at[v] || []) {
        const d = fn[t * 3] * fn[o * 3] + fn[t * 3 + 1] * fn[o * 3 + 1] + fn[t * 3 + 2] * fn[o * 3 + 2];
        if (d < cosLimit) continue;
        nx += fn[o * 3]; ny += fn[o * 3 + 1]; nz += fn[o * 3 + 2];
      }
      const l = Math.hypot(nx, ny, nz) || 1;
      // An indexed mesh has ONE normal per vertex, so a vertex on a crease gets the average of
      // whichever face asked last. Splitting it would change the index buffer; that is what
      // `smooth()` does on the three side, by writing per-corner normals instead.
      out[v * 3] = nx / l; out[v * 3 + 1] = ny / l; out[v * 3 + 2] = nz / l;
    }
  }
  return out;
}

/** Per-corner normals, which is what a crease actually needs: the same position, two normals. */
export function smoothCornerNormals(pos: Float32Array, idx: Uint32Array, angleDeg = 40):
  { pos: Float32Array; normal: Float32Array } {
  const tris = idx.length / 3;
  const fn = new Float32Array(tris * 3);
  for (let t = 0; t < tris; t++) {
    const n = faceNormal(pos, idx[t * 3], idx[t * 3 + 1], idx[t * 3 + 2]);
    fn[t * 3] = n[0]; fn[t * 3 + 1] = n[1]; fn[t * 3 + 2] = n[2];
  }
  const at: number[][] = [];
  for (let t = 0; t < tris; t++) {
    for (let k = 0; k < 3; k++) { const v = idx[t * 3 + k]; (at[v] || (at[v] = [])).push(t); }
  }
  const cosLimit = Math.cos((angleDeg * Math.PI) / 180);
  const outPos = new Float32Array(tris * 9);
  const outNor = new Float32Array(tris * 9);
  for (let t = 0; t < tris; t++) {
    for (let k = 0; k < 3; k++) {
      const v = idx[t * 3 + k];
      let nx = 0, ny = 0, nz = 0;
      for (const o of at[v] || []) {
        const d = fn[t * 3] * fn[o * 3] + fn[t * 3 + 1] * fn[o * 3 + 1] + fn[t * 3 + 2] * fn[o * 3 + 2];
        if (d < cosLimit) continue;
        nx += fn[o * 3]; ny += fn[o * 3 + 1]; nz += fn[o * 3 + 2];
      }
      const l = Math.hypot(nx, ny, nz) || 1;
      const w = (t * 3 + k) * 3;
      outPos[w] = pos[v * 3]; outPos[w + 1] = pos[v * 3 + 1]; outPos[w + 2] = pos[v * 3 + 2];
      outNor[w] = nx / l; outNor[w + 1] = ny / l; outNor[w + 2] = nz / l;
    }
  }
  return { pos: outPos, normal: outNor };
}

// ------------------------------------------------------------------ subdivision
const edgeKey = (a: number, b: number) => (a < b ? a + "_" + b : b + "_" + a);

/**
 * Loop subdivision — the triangle cousin of Blender's Subdivision Surface modifier.
 *
 * Each triangle becomes four, new vertices land on the 3/8-1/8 stencil, and the old ones move
 * by the valence rule. A boundary edge keeps its own 1/2-1/2 rule so an open surface does not
 * shrink away from its own border, which is the failure that makes naive subdivision unusable
 * on anything that is not a closed solid.
 */
export function subdivideArrays(pos: Float32Array, idx: Uint32Array, levels = 1):
  { pos: Float32Array; idx: Uint32Array } {
  let P = pos, I = idx;
  for (let l = 0; l < Math.max(0, Math.min(4, levels)); l++) ({ pos: P, idx: I } = subdivideOnce(P, I));
  return { pos: P, idx: I };
}

function subdivideOnce(pos: Float32Array, idx: Uint32Array): { pos: Float32Array; idx: Uint32Array } {
  const nv = pos.length / 3;
  const tris = idx.length / 3;
  const faces: Record<string, number[]> = {};
  const opposite: Record<string, number[]> = {};
  const neighbours: Set<number>[] = [];
  for (let i = 0; i < nv; i++) neighbours.push(new Set());

  for (let t = 0; t < tris; t++) {
    const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
    const pairs: Array<[number, number, number]> = [[a, b, c], [b, c, a], [c, a, b]];
    for (const [u, v, w] of pairs) {
      const k = edgeKey(u, v);
      (faces[k] || (faces[k] = [])).push(t);
      (opposite[k] || (opposite[k] = [])).push(w);
      neighbours[u].add(v);
      neighbours[v].add(u);
    }
  }

  const outPos: number[] = [];
  for (let i = 0; i < nv; i++) outPos.push(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);

  // New vertex on each edge.
  const edgeVert = new Map<string, number>();
  for (const k of Object.keys(faces)) {
    const [us, vs] = k.split("_");
    const u = +us, v = +vs;
    const opp = opposite[k];
    let x: number, y: number, z: number;
    if (opp.length >= 2) {
      x = (3 / 8) * (pos[u * 3] + pos[v * 3]) + (1 / 8) * (pos[opp[0] * 3] + pos[opp[1] * 3]);
      y = (3 / 8) * (pos[u * 3 + 1] + pos[v * 3 + 1]) + (1 / 8) * (pos[opp[0] * 3 + 1] + pos[opp[1] * 3 + 1]);
      z = (3 / 8) * (pos[u * 3 + 2] + pos[v * 3 + 2]) + (1 / 8) * (pos[opp[0] * 3 + 2] + pos[opp[1] * 3 + 2]);
    } else {
      x = 0.5 * (pos[u * 3] + pos[v * 3]);
      y = 0.5 * (pos[u * 3 + 1] + pos[v * 3 + 1]);
      z = 0.5 * (pos[u * 3 + 2] + pos[v * 3 + 2]);
    }
    edgeVert.set(k, outPos.length / 3);
    outPos.push(x, y, z);
  }

  // Old vertices move.
  const boundary = new Set<number>();
  for (const k of Object.keys(faces)) {
    if (faces[k].length >= 2) continue;
    const [us, vs] = k.split("_");
    boundary.add(+us);
    boundary.add(+vs);
  }
  for (let i = 0; i < nv; i++) {
    const nb = [...neighbours[i]];
    if (!nb.length) continue;
    if (boundary.has(i)) {
      // On a border, only the two border neighbours count, or the edge creeps inwards.
      const ends = nb.filter((j) => (faces[edgeKey(i, j)] || []).length < 2);
      if (ends.length !== 2) continue;
      for (let c = 0; c < 3; c++) {
        outPos[i * 3 + c] = 0.75 * pos[i * 3 + c] + 0.125 * (pos[ends[0] * 3 + c] + pos[ends[1] * 3 + c]);
      }
      continue;
    }
    const n = nb.length;
    const beta = n === 3 ? 3 / 16 : 3 / (8 * n);
    for (let c = 0; c < 3; c++) {
      let sum = 0;
      for (const j of nb) sum += pos[j * 3 + c];
      outPos[i * 3 + c] = pos[i * 3 + c] * (1 - n * beta) + beta * sum;
    }
  }

  const outIdx: number[] = [];
  for (let t = 0; t < tris; t++) {
    const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
    const ab = edgeVert.get(edgeKey(a, b))!;
    const bc = edgeVert.get(edgeKey(b, c))!;
    const ca = edgeVert.get(edgeKey(c, a))!;
    outIdx.push(a, ab, ca, ab, b, bc, ca, bc, c, ab, bc, ca);
  }
  return { pos: Float32Array.from(outPos), idx: Uint32Array.from(outIdx) };
}

// ------------------------------------------------------------------ transforms
/**
 * Mirror across a plane and weld the seam — Blender's Mirror modifier.
 *
 * Reflecting reverses the handedness of every triangle, so the winding has to be flipped too or
 * the whole mirrored half renders inside out. That failure is invisible in a lit render, which
 * is exactly why the face-orientation overlay exists.
 */
export function mirrorArrays(pos: Float32Array, idx: Uint32Array, axis: 0 | 1 | 2 = 0, weldSeam = true, tol = 1e-4):
  { pos: Float32Array; idx: Uint32Array } {
  const n = pos.length / 3;
  const outPos = new Float32Array(pos.length * 2);
  outPos.set(pos, 0);
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < 3; c++) outPos[(n + i) * 3 + c] = c === axis ? -pos[i * 3 + c] : pos[i * 3 + c];
  }
  const outIdx = new Uint32Array(idx.length * 2);
  outIdx.set(idx, 0);
  for (let t = 0; t + 2 < idx.length; t += 3) {
    outIdx[idx.length + t] = idx[t] + n;
    outIdx[idx.length + t + 1] = idx[t + 2] + n;   // reversed: a reflection flips the winding
    outIdx[idx.length + t + 2] = idx[t + 1] + n;
  }
  if (!weldSeam) return { pos: outPos, idx: outIdx };
  const w = weldArrays(outPos, outIdx, tol);
  return { pos: w.pos, idx: w.idx };
}

/** Copies along an offset — Blender's Array modifier. */
export function arrayArrays(pos: Float32Array, idx: Uint32Array, count: number, offset: V3, weldJoins = false, tol = 1e-4):
  { pos: Float32Array; idx: Uint32Array } {
  const c = Math.max(1, Math.floor(count));
  const n = pos.length / 3;
  const outPos = new Float32Array(pos.length * c);
  const outIdx = new Uint32Array(idx.length * c);
  for (let k = 0; k < c; k++) {
    for (let i = 0; i < n; i++) {
      outPos[(k * n + i) * 3] = pos[i * 3] + offset[0] * k;
      outPos[(k * n + i) * 3 + 1] = pos[i * 3 + 1] + offset[1] * k;
      outPos[(k * n + i) * 3 + 2] = pos[i * 3 + 2] + offset[2] * k;
    }
    for (let t = 0; t < idx.length; t++) outIdx[k * idx.length + t] = idx[t] + k * n;
  }
  if (!weldJoins) return { pos: outPos, idx: outIdx };
  const w = weldArrays(outPos, outIdx, tol);
  return { pos: w.pos, idx: w.idx };
}

export type DeformKind = "taper" | "twist" | "bend" | "stretch";

/**
 * Blender's Simple Deform, along one axis, over the bounding range of the mesh.
 *
 * `amount` is read as: taper — the scale at the far end (0.5 halves it); twist — radians over the
 * whole length; bend — radians over the whole length; stretch — the scale at the far end.
 */
export function deformArrays(pos: Float32Array, kind: DeformKind, amount: number, axis: 0 | 1 | 2 = 1): Float32Array {
  const out = new Float32Array(pos);
  const n = pos.length / 3;
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) {
    const v = pos[i * 3 + axis];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const span = hi - lo;
  if (!(span > EPS)) return out;
  const others: [number, number] = axis === 0 ? [1, 2] : axis === 1 ? [0, 2] : [0, 1];
  for (let i = 0; i < n; i++) {
    const t = (pos[i * 3 + axis] - lo) / span;
    const a = others[0], b = others[1];
    if (kind === "taper" || kind === "stretch") {
      const s = 1 + (amount - 1) * t;
      out[i * 3 + a] = pos[i * 3 + a] * s;
      out[i * 3 + b] = pos[i * 3 + b] * s;
      if (kind === "stretch") out[i * 3 + axis] = lo + (pos[i * 3 + axis] - lo) * (2 - s);
    } else if (kind === "twist") {
      const ang = amount * t;
      const ca = Math.cos(ang), sa = Math.sin(ang);
      const x = pos[i * 3 + a], y = pos[i * 3 + b];
      out[i * 3 + a] = x * ca - y * sa;
      out[i * 3 + b] = x * sa + y * ca;
    } else {
      // Bend wraps the axis onto an arc of `amount` radians, keeping arc length.
      const ang = amount * t;
      const r = span / (Math.abs(amount) > EPS ? amount : EPS);
      const off = pos[i * 3 + a];
      out[i * 3 + axis] = lo + (r - off) * Math.sin(ang);
      out[i * 3 + a] = r - (r - off) * Math.cos(ang);
    }
  }
  return out;
}

/** Push every vertex along its normal by a noise field — scales, bark, rock, dents. */
export function displaceArrays(pos: Float32Array, normal: Float32Array, amp: number, freq = 3, octaves = 3, seed = 1): Float32Array {
  const out = new Float32Array(pos);
  const n = pos.length / 3;
  for (let i = 0; i < n; i++) {
    const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
    const d = (fbm(x * freq, y * freq, z * freq, octaves, 0.5, 2, seed) - 0.5) * 2 * amp;
    out[i * 3] = x + (normal[i * 3] || 0) * d;
    out[i * 3 + 1] = y + (normal[i * 3 + 1] || 0) * d;
    out[i * 3 + 2] = z + (normal[i * 3 + 2] || 0) * d;
  }
  return out;
}

/** Thicken an open surface — Blender's Solidify. The rim closes the two shells into a solid. */
export function solidifyArrays(pos: Float32Array, idx: Uint32Array, normal: Float32Array, thickness: number):
  { pos: Float32Array; idx: Uint32Array } {
  const n = pos.length / 3;
  const outPos = new Float32Array(pos.length * 2);
  outPos.set(pos, 0);
  for (let i = 0; i < n; i++) {
    outPos[(n + i) * 3] = pos[i * 3] - (normal[i * 3] || 0) * thickness;
    outPos[(n + i) * 3 + 1] = pos[i * 3 + 1] - (normal[i * 3 + 1] || 0) * thickness;
    outPos[(n + i) * 3 + 2] = pos[i * 3 + 2] - (normal[i * 3 + 2] || 0) * thickness;
  }
  const out: number[] = [];
  for (let t = 0; t + 2 < idx.length; t += 3) {
    out.push(idx[t], idx[t + 1], idx[t + 2]);
    out.push(idx[t] + n, idx[t + 2] + n, idx[t + 1] + n);     // inner shell faces the other way
  }
  // A border edge belongs to one face only; those are the ones that need a wall.
  const seen = new Map<string, number>();
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const tri = [idx[t], idx[t + 1], idx[t + 2]];
    for (let k = 0; k < 3; k++) {
      const key = edgeKey(tri[k], tri[(k + 1) % 3]);
      seen.set(key, (seen.get(key) || 0) + 1);
    }
  }
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const tri = [idx[t], idx[t + 1], idx[t + 2]];
    for (let k = 0; k < 3; k++) {
      const a = tri[k], b = tri[(k + 1) % 3];
      if ((seen.get(edgeKey(a, b)) || 0) !== 1) continue;
      out.push(a, b, b + n, a, b + n, a + n);
    }
  }
  return { pos: outPos, idx: Uint32Array.from(out) };
}

// ------------------------------------------------------------------ sampling
/**
 * Points spread over a surface, area-weighted — Blender's scatter, and how scales, spikes,
 * feathers, rivets and foliage get placed without a hand-written loop per feature.
 */
export function scatterArrays(pos: Float32Array, idx: Uint32Array, count: number, seed = 1):
  Array<{ p: V3; n: V3 }> {
  const tris = idx.length / 3;
  if (!tris || count <= 0) return [];
  const areas = new Float64Array(tris);
  let total = 0;
  for (let t = 0; t < tris; t++) {
    const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
    const e1x = pos[b * 3] - pos[a * 3], e1y = pos[b * 3 + 1] - pos[a * 3 + 1], e1z = pos[b * 3 + 2] - pos[a * 3 + 2];
    const e2x = pos[c * 3] - pos[a * 3], e2y = pos[c * 3 + 1] - pos[a * 3 + 1], e2z = pos[c * 3 + 2] - pos[a * 3 + 2];
    const cx = e1y * e2z - e1z * e2y, cy = e1z * e2x - e1x * e2z, cz = e1x * e2y - e1y * e2x;
    // Area-weighted, not per-triangle. Uniform per triangle clumps every point onto the dense
    // parts of the mesh, which on a creature means all the scales end up on its face.
    total += (areas[t] = Math.hypot(cx, cy, cz) / 2);
  }
  if (!(total > 0)) return [];
  const cum = new Float64Array(tris);
  let run = 0;
  for (let t = 0; t < tris; t++) { run += areas[t]; cum[t] = run / total; }

  const rand = rng(seed);
  const out: Array<{ p: V3; n: V3 }> = [];
  for (let i = 0; i < count; i++) {
    const r = rand();
    let lo = 0, hi = tris - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (cum[mid] < r) lo = mid + 1; else hi = mid; }
    const t = lo;
    const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
    let u = rand(), v = rand();
    if (u + v > 1) { u = 1 - u; v = 1 - v; }
    const w = 1 - u - v;
    out.push({
      p: [
        pos[a * 3] * w + pos[b * 3] * u + pos[c * 3] * v,
        pos[a * 3 + 1] * w + pos[b * 3 + 1] * u + pos[c * 3 + 1] * v,
        pos[a * 3 + 2] * w + pos[b * 3 + 2] * u + pos[c * 3 + 2] * v,
      ],
      n: faceNormal(pos, a, b, c),
    });
  }
  return out;
}

// ------------------------------------------------------------------ uvs
export type UvMode = "box" | "cylinder" | "sphere";

/** UVs, so a texture can be put on it at all. Box projection is the safe default; a cylinder is
 *  right for a limb or a trunk, a sphere for a head. */
export function uvArrays(pos: Float32Array, idx: Uint32Array, mode: UvMode = "box", scale = 1): Float32Array {
  const n = pos.length / 3;
  const uv = new Float32Array(n * 2);
  let minX = Infinity, minY = Infinity, minZ = Infinity, maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < n; i++) {
    minX = Math.min(minX, pos[i * 3]); maxX = Math.max(maxX, pos[i * 3]);
    minY = Math.min(minY, pos[i * 3 + 1]); maxY = Math.max(maxY, pos[i * 3 + 1]);
    minZ = Math.min(minZ, pos[i * 3 + 2]); maxZ = Math.max(maxZ, pos[i * 3 + 2]);
  }
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, cz = (minZ + maxZ) / 2;
  const sx = Math.max(EPS, maxX - minX), sy = Math.max(EPS, maxY - minY), sz = Math.max(EPS, maxZ - minZ);
  const nrm = normalsFor(pos, idx);

  for (let i = 0; i < n; i++) {
    const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
    if (mode === "cylinder") {
      uv[i * 2] = ((Math.atan2(z - cz, x - cx) / (Math.PI * 2)) + 0.5) * scale;
      uv[i * 2 + 1] = ((y - minY) / sy) * scale;
    } else if (mode === "sphere") {
      const dx = x - cx, dy = y - cy, dz = z - cz;
      const r = Math.hypot(dx, dy, dz) || 1;
      uv[i * 2] = ((Math.atan2(dz, dx) / (Math.PI * 2)) + 0.5) * scale;
      uv[i * 2 + 1] = (Math.acos(Math.min(1, Math.max(-1, dy / r))) / Math.PI) * scale;
    } else {
      // Box: project onto whichever plane the surface faces most, so a face never smears.
      const ax = Math.abs(nrm[i * 3]), ay = Math.abs(nrm[i * 3 + 1]), az = Math.abs(nrm[i * 3 + 2]);
      if (ax >= ay && ax >= az) { uv[i * 2] = ((z - minZ) / sz) * scale; uv[i * 2 + 1] = ((y - minY) / sy) * scale; }
      else if (ay >= az) { uv[i * 2] = ((x - minX) / sx) * scale; uv[i * 2 + 1] = ((z - minZ) / sz) * scale; }
      else { uv[i * 2] = ((x - minX) / sx) * scale; uv[i * 2 + 1] = ((y - minY) / sy) * scale; }
    }
  }
  return uv;
}

function normalsFor(pos: Float32Array, idx: Uint32Array): Float32Array {
  const out = new Float32Array(pos.length);
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const a = idx[t], b = idx[t + 1], c = idx[t + 2];
    const n = faceNormal(pos, a, b, c);
    for (const v of [a, b, c]) {
      out[v * 3] += n[0]; out[v * 3 + 1] += n[1]; out[v * 3 + 2] += n[2];
    }
  }
  for (let i = 0; i < out.length; i += 3) {
    const l = Math.hypot(out[i], out[i + 1], out[i + 2]) || 1;
    out[i] /= l; out[i + 1] /= l; out[i + 2] /= l;
  }
  return out;
}
export { normalsFor as vertexNormals };

// ------------------------------------------------------------------ simplify
/**
 * Grid-cluster decimation: collapse everything inside a cell to one vertex.
 *
 * It is NOT quadric error simplification, and it does not pretend to be — it does not preserve
 * silhouettes as carefully and it will round a sharp corner. What it does do is run in one pass
 * on any mesh however broken, which is what a level-of-detail chain for a background prop needs.
 */
export function simplifyArrays(pos: Float32Array, idx: Uint32Array, cell: number):
  { pos: Float32Array; idx: Uint32Array; ratio: number } {
  if (!(cell > 0)) return { pos, idx, ratio: 1 };
  const n = pos.length / 3;
  const snapped = new Float32Array(pos.length);
  for (let i = 0; i < n * 3; i++) snapped[i] = Math.round(pos[i] / cell) * cell;
  const w = weldArrays(snapped, idx, cell * 0.25);
  return { pos: w.pos, idx: w.idx, ratio: idx.length ? w.idx.length / idx.length : 1 };
}

// ------------------------------------------------------------------ the report
export interface Defects {
  vertices: number;
  triangles: number;
  degenerate: number;
  /** Vertices that sit on top of another but are not the same vertex: an unwelded seam. */
  duplicates: number;
  /** Edges used by only one triangle, AS AUTHORED. High on a mesh made of loose parts. */
  boundaryEdges: number;
  /** Open edges that survive a weld. THIS is a hole; `boundaryEdges` may just be loose pieces. */
  holes: number;
  /** Edges used by three or more: the mesh folds back on itself there. */
  nonManifoldEdges: number;
  /** Signed volume of the welded form. Negative means the whole thing is inside out. */
  volume: number;
  inverted: boolean;
  hasNormals: boolean;
  hasUVs: boolean;
  bounds: { min: V3; max: V3; size: V3 };
  /** Plain sentences, in the order they are worth acting on. */
  notes: string[];
}

/**
 * The defect report.
 *
 * This is the operation that has no equivalent in Blender, Godot or Unity, and it exists because
 * the user of this library cannot see. Every one of these numbers stands for a fault that is
 * invisible in a lit render and obvious in a wireframe — and an agent only ever gets the render.
 */
export function checkArrays(pos: Float32Array, idx: Uint32Array, normal?: Float32Array | null, uv?: Float32Array | null): Defects {
  const n = pos.length / 3;
  const tris = idx.length / 3;

  // Everything topological is measured TWICE: once as authored, and once on the welded form.
  //
  // Measuring only one of them gives a confidently wrong answer in a very common case. A mesh
  // with per-corner normals — which is what smooth shading by angle produces — has no shared
  // vertices at all, so as authored every edge is a border and every vertex a duplicate. Judged
  // that way a perfectly good model reads as ten thousand holes. And a mesh made of loose
  // primitives, judged only after welding, reads as flawless while the light finds every crack.
  const raw = topology(pos, idx);
  const w = weldArrays(pos, idx, 1e-5);
  const welded = topology(w.pos, w.idx);

  const degenerate = raw.degenerate;
  const volume = welded.volume;
  const boundaryEdges = raw.boundary;
  const holes = welded.boundary;
  const nonManifoldEdges = welded.nonManifold;
  const duplicates = w.merged;

  let minX = Infinity, minY = Infinity, minZ = Infinity, maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < n; i++) {
    minX = Math.min(minX, pos[i * 3]); maxX = Math.max(maxX, pos[i * 3]);
    minY = Math.min(minY, pos[i * 3 + 1]); maxY = Math.max(maxY, pos[i * 3 + 1]);
    minZ = Math.min(minZ, pos[i * 3 + 2]); maxZ = Math.max(maxZ, pos[i * 3 + 2]);
  }
  if (!n) { minX = minY = minZ = maxX = maxY = maxZ = 0; }

  const notes: string[] = [];
  const solid = holes === 0;
  if (solid && volume < 0) notes.push("INSIDE OUT — every face points inwards. Reverse the winding, or the model renders as a hole in the world.");
  if (degenerate) notes.push(degenerate + " degenerate triangle" + (degenerate === 1 ? "" : "s") + " with no area. They shade as black specks. Weld, which drops them.");
  if (holes > 0) {
    notes.push(holes + " open edge" + (holes === 1 ? "" : "s") + " survive a weld — that is a real hole in the surface. "
      + "Fine on a flat piece; on a closed shape it is missing geometry.");
  } else if (boundaryEdges > 0) {
    notes.push(boundaryEdges + " open edge" + (boundaryEdges === 1 ? "" : "s") + ", but the seams are not welded — "
      + "welding closes every one of them, drops " + duplicates + " duplicate vertices, and lets smooth shading cross the joins.");
  }
  if (nonManifoldEdges) notes.push(nonManifoldEdges + " edge" + (nonManifoldEdges === 1 ? "" : "s") + " used by three or more faces. Booleans and subdivision misbehave there.");
  if (!normal || !normal.length) notes.push("No normals. It will shade flat, faceted and dull.");
  if (!uv || !uv.length) notes.push("No UVs. No image texture can be put on it.");
  if (!notes.length) notes.push("Nothing wrong found: closed, welded, wound the right way, with normals and UVs.");

  return {
    vertices: n, triangles: tris, degenerate, duplicates, boundaryEdges, holes, nonManifoldEdges,
    volume: Math.round(volume * 1e6) / 1e6,
    inverted: solid && volume < 0,
    hasNormals: !!(normal && normal.length),
    hasUVs: !!(uv && uv.length),
    bounds: {
      min: [minX, minY, minZ], max: [maxX, maxY, maxZ],
      size: [maxX - minX, maxY - minY, maxZ - minZ],
    },
    notes,
  };
}

/** Edge use, degenerate faces and signed volume for one index buffer. */
function topology(pos: Float32Array, idx: Uint32Array):
  { degenerate: number; volume: number; boundary: number; nonManifold: number } {
  const tris = idx.length / 3;
  let degenerate = 0;
  let volume = 0;
  const edges = new Map<string, number>();
  for (let t = 0; t < tris; t++) {
    const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
    if (a === b || b === c || a === c) { degenerate++; continue; }
    const ax = pos[a * 3], ay = pos[a * 3 + 1], az = pos[a * 3 + 2];
    const bx = pos[b * 3], by = pos[b * 3 + 1], bz = pos[b * 3 + 2];
    const cx = pos[c * 3], cy = pos[c * 3 + 1], cz = pos[c * 3 + 2];
    const e1x = bx - ax, e1y = by - ay, e1z = bz - az;
    const e2x = cx - ax, e2y = cy - ay, e2z = cz - az;
    const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
    if (Math.hypot(nx, ny, nz) < 1e-12) degenerate++;
    // Six times the signed volume of the tetrahedron to the origin; summed, it is the volume.
    volume += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6;
    for (const [u, v] of [[a, b], [b, c], [c, a]] as Array<[number, number]>) {
      const k = edgeKey(u, v);
      edges.set(k, (edges.get(k) || 0) + 1);
    }
  }
  let boundary = 0, nonManifold = 0;
  for (const c of edges.values()) {
    if (c === 1) boundary++;
    else if (c > 2) nonManifold++;
  }
  return { degenerate, volume, boundary, nonManifold };
}

/** Reverse every triangle. The fix for a mesh the report calls inside out. */
export function flipArrays(idx: Uint32Array): Uint32Array {
  const out = new Uint32Array(idx.length);
  for (let t = 0; t + 2 < idx.length; t += 3) {
    out[t] = idx[t];
    out[t + 1] = idx[t + 2];
    out[t + 2] = idx[t + 1];
  }
  return out;
}

// ==================================================================== three.js
//
// Everything above is arrays. Everything below is the same operations wearing a BufferGeometry,
// which is what asset code actually holds. `T` is passed in rather than imported for the reason
// the whole editor passes it in: two copies of three in one page is a very long afternoon.

export interface Ops {
  merge(object: any): any;
  weld(geo: any, tol?: number): any;
  smooth(geo: any, angleDeg?: number): any;
  subdivide(geo: any, levels?: number): any;
  mirror(geo: any, axis?: 0 | 1 | 2, weldSeam?: boolean): any;
  array(geo: any, count: number, offset: V3, weldJoins?: boolean): any;
  deform(geo: any, kind: DeformKind, amount: number, axis?: 0 | 1 | 2): any;
  displace(geo: any, amp: number, freq?: number, octaves?: number, seed?: number): any;
  solidify(geo: any, thickness: number): any;
  scatter(geo: any, count: number, seed?: number): Array<{ p: V3; n: V3 }>;
  uv(geo: any, mode?: UvMode, scale?: number): any;
  simplify(geo: any, cell: number): any;
  flip(geo: any): any;
  check(target: any): Defects;
  /** The whole pipeline in one call: flatten, weld, smooth. What turns a pile into a model. */
  skin(object: any, opts?: { tol?: number; angle?: number; subdivide?: number }): any;
  /** The convex wrapping of an object, a geometry or a flat list of points. Godot's collision shape. */
  hull(target: any): any;
  /** Union, subtract or intersect two closed objects or geometries, in world space. */
  boolean(a: any, b: any, op?: BooleanOp): any;
  /** A skin over a field: below zero is inside. Surface nets, the table-free marching cubes. */
  isosurface(field: (x: number, y: number, z: number) => number, min: V3, max: V3, res?: V3 | number, iso?: number): any;
  /** Spheres blended into one organic body, smoothed and ready. The fast way to a creature's mass. */
  blobs(balls: Blob[], opts?: { res?: number; margin?: number; angle?: number }): any;
  /** FABRIK on a chain of joints; see rig.ts for the version that poses bones. */
  ik: typeof fabrik;
  // ---- the modelling tools (model.ts) ----
  /** Catmull-Clark on the recovered quads. `creaseAngle` keeps edges sharper than that; `sharpness` says for how many levels. */
  subsurf(geo: any, levels?: number, opts?: { creaseAngle?: number; sharpness?: number }): any;
  /** Round every edge sharper than `angle` degrees with `segments` steps of `width`. */
  bevel(geo: any, opts?: { width?: number; segments?: number; angle?: number }): any;
  /** Smart projection unwrap: islands by facing, packed with a margin. Sets `uv` and `uv1` (`uv1: false` leaves the second set out of the GLB). */
  unwrap(geo: any, opts?: { angle?: number; seamAngle?: number; margin?: number; uv1?: boolean }): any;
  /** One even closed surface from anything closed, at `res` voxels across. */
  remesh(geo: any, opts?: { res?: number }): any;
  /** Taubin smoothing: jaggedness out, volume kept, boundary fixed. */
  relax(geo: any, opts?: { iterations?: number; lambda?: number; mu?: number }): any;
  /** Bone-heat skin weights, as skinIndex/skinWeight arrays for `maxBones` per vertex. */
  heatWeights(geo: any, bones: Array<{ head: V3; tail: V3 }>, opts?: { maxBones?: number; visibility?: boolean }): { index: Uint16Array; weight: Float32Array };
  /** Ambient occlusion into the geometry's own UVs. Unwrap first. */
  bakeAO(geo: any, size?: number, opts?: { rays?: number; distance?: number }): Baked;
  /** Curvature (convex bright, concave dark) into the geometry's UVs: the mask for wear and dirt. */
  bakeCurvature(geo: any, size?: number, opts?: { scale?: number }): Baked;
  /** A tangent-space normal map: `high`'s detail onto `low`'s UVs. */
  bakeNormalMap(low: any, high: any, size?: number, opts?: { distance?: number }): Baked;
  /** A baked map as a texture of this engine, ready to put on a material. */
  texture(baked: Baked, opts?: { srgb?: boolean }): any;
  noise: typeof noise3;
  fbm: typeof fbm;
  rng: typeof rng;
  // ---- materials (material.ts, atlas.ts): Blender's shader nodes plus a Cycles bake ----
  /** Any colour function of the surface (point, normal, curvature, occlusion) baked into the geometry's UVs; with `high`, read from the high mesh under each texel. */
  bake(geo: any, size: number | [number, number], fn: (t: Texel) => [number, number, number, number?],
    opts?: { high?: any; distance?: number; ao?: false | { rays?: number; distance?: number }; curvature?: boolean; dilate?: number; curvatureRadius?: number }): Baked;
  /** A smart material (a name in `materials` or your own layers) into base colour, ORM and a normal map, in the geometry's UVs; `high` bakes a sculpt's detail on. */
  bakeMaterial(geo: any, mat: SmartMaterial | string, size?: number | [number, number],
    opts?: { high?: any; ao?: false | { rays?: number; distance?: number }; aoInColor?: number; distance?: number; curvatureRadius?: number; dilate?: number }): { baseColor: Baked; orm: Baked; normal: Baked };
  /** Several geometries unwrapped into ONE atlas (`weights` scale a part's texel share); vertex colours are carried through. */
  atlas(geos: any[], opts?: { margin?: number; angle?: number; weights?: Array<number | undefined> }): any[];
  /** Every part with its own material into ONE texture set: the atlas geometries to draw, and the maps for one material. */
  bakeAtlas(parts: Array<{ geo: any; mat: SmartMaterial | string; weight?: number; high?: any }>, size?: number,
    opts?: { margin?: number; angle?: number; ao?: false | { rays?: number; distance?: number }; aoInColor?: number; distance?: number; curvatureRadius?: number; dilate?: number }): { geos: any[]; maps: { baseColor: Baked; orm: Baked; normal: Baked } };
  /** Baked maps onto a material with the right colour spaces, filtering and normal-map sign, so the forge and the exported GLB agree. */
  applyMaps(material: any, maps: { baseColor?: Baked; orm?: Baked; normal?: Baked }, opts?: { normalScale?: number; anisotropy?: number }): any;
  /** Ready smart materials: steel, rustySteel, paintedSteel, leather, wood, cloth, skin, bone, gold, stone. */
  materials: typeof MATERIALS;
  // ---- characters (hands.ts) ----
  /** A complete stylised hand (palm, fingers of three segments, thumb, wrist stub) as ONE smooth surface in a pose; `paint` puts skin and nail colours into vertex colours (three). */
  hand(opts: HandOpts, res?: number, paint?: { skin?: string; nail?: string }): any;
  /** Put a hand ON a handle from `a` to `b` (at `t` along it), knuckles toward `face`: moves and turns the object; `wrist` in the answer is where its forearm must end. */
  grip(object: any, o: { a: V3; b: V3; t?: number; face: V3; aim?: V3; hand?: Hand }): GripPlacementResult;
  // ---- sculpt, then a game budget (sculpt.ts, decimate.ts, sweep.ts): Blender's sculpt → decimate → bake, in code ----
  /** Split every edge longer than `maxEdge` metres, so strokes have vertices to move; the surface does not move. uv is not carried: densify and sculpt BEFORE unwrap. */
  densify(geo: any, maxEdge: number): any;
  /** Brush strokes as data, applied in order (crease, ridge, inflate, pinch, smooth, flatten, noise, move, clay: see Stroke). Same triangles and uv, new positions; `angle` (default 80) keeps sharper edges hard. */
  sculpt(geo: any, strokes: Stroke[], opts?: { angle?: number }): any;
  /** Quadric edge collapse to `target` triangles (or `ratio`): creases, open borders, UV seams and vertex colours are kept. Keep the sculpt as the `high` of bakeMaterial. */
  decimate(geo: any, opts?: DecimateOptions & { angle?: number }): any;
  /** A tube, or any closed `profile`, swept along a path with frames that never flip: straps, belts, cords, grips, horns. */
  sweep(path: V3[], opts?: SweepOptions): any;
  /** A rolled tube along every open border of a shell, in its own space: the rim of a helmet, a pauldron, a shield. */
  rim(geo: any, opts: { radius: number; sides?: number; inset?: number; lift?: number; relax?: number; tol?: number }): any;
  /** The open borders of a mesh as loops of points; a closed surface has none. */
  borders(geo: any, tol?: number): V3[][];
  /** Two-bone reach: where the elbow goes so a chain of [upper, lower] from `root` ends exactly on `target` (a fist on its grip). */
  reach: typeof reach;
  // ---- modular kits (see "modular kits" below: bayLayout, facadeSlots, perimeterSlots) ----
  /** Named pieces out of a GLB scene or a record of objects, measured, every pivot moved to one anchor so they snap on one grid. */
  kit(source: any, opts?: KitOptions): Kit;
  /** Copies of one piece at every placement: ONE InstancedMesh when the piece is a single mesh, clones otherwise. */
  repeat(piece: KitPiece | any, at: PlacementLike[], opts?: RepeatOptions): any;
  /** A whole wall, floor by floor: whole bays, the leftover spread evenly, and `pick(slot)` names the piece for each (floor, bay). */
  facade(kit: Kit, opts: FacadeOptions, pick: FacadePicker): any;
  /** THE modular rule on one run: how many whole bays fit and where, with the leftover spread evenly. Never stretches a bay. */
  bays: typeof bayLayout;
  /** The same rule round a rectangle: slots facing outward for roof trims, dentils, railings and corner posts. */
  perimeter: typeof perimeterSlots;
}

function read(geo: any): { pos: Float32Array; idx: Uint32Array } {
  const p = geo?.attributes?.position;
  if (!p) return { pos: new Float32Array(0), idx: new Uint32Array(0) };
  const pos = p.array instanceof Float32Array ? p.array : Float32Array.from(p.array);
  const idx = geo.index ? Uint32Array.from(geo.index.array) : indexOf(pos, null);
  return { pos, idx };
}

function write(T: any, pos: Float32Array, idx: Uint32Array | null, normal?: Float32Array, uv?: Float32Array): any {
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(pos, 3));
  if (idx) g.setIndex(new T.Uint32BufferAttribute(idx, 1));
  if (normal) g.setAttribute("normal", new T.Float32BufferAttribute(normal, 3));
  if (uv) g.setAttribute("uv", new T.Float32BufferAttribute(uv, 2));
  if (!normal) g.computeVertexNormals();
  return g;
}

export function makeOps(T: any): Ops {
  /**
   * Flatten a whole object tree into ONE geometry in world space.
   *
   * This is the operation the rest depend on. An asset built as thirty primitives cannot be
   * welded, smoothed or subdivided while it is still thirty objects — the seams are between
   * meshes, and no per-mesh operation can reach across. Merging first is what makes a procedural
   * creature into a single surface, which is the difference the Blender comparison came down to.
   */
  function merge(object: any): any {
    const parts: Array<{ pos: Float32Array; idx: Uint32Array }> = [];
    object.updateMatrixWorld?.(true);
    const inv = object.isObject3D && object.matrixWorld
      ? new T.Matrix4().copy(object.matrixWorld).invert() : new T.Matrix4();
    const v = new T.Vector3();
    const walk = (o: any) => {
      if (o.isMesh && o.geometry?.attributes?.position && o.visible !== false) {
        const { pos, idx } = read(o.geometry);
        const m = new T.Matrix4().multiplyMatrices(inv, o.matrixWorld);
        const out = new Float32Array(pos.length);
        for (let i = 0; i < pos.length; i += 3) {
          v.set(pos[i], pos[i + 1], pos[i + 2]).applyMatrix4(m);
          out[i] = v.x; out[i + 1] = v.y; out[i + 2] = v.z;
        }
        parts.push({ pos: out, idx });
      }
      for (const c of o.children || []) walk(c);
    };
    walk(object);
    let nPos = 0, nIdx = 0;
    for (const p of parts) { nPos += p.pos.length; nIdx += p.idx.length; }
    const pos = new Float32Array(nPos);
    const idx = new Uint32Array(nIdx);
    let po = 0, io = 0, base = 0;
    for (const p of parts) {
      pos.set(p.pos, po);
      for (let i = 0; i < p.idx.length; i++) idx[io + i] = p.idx[i] + base;
      base += p.pos.length / 3;
      po += p.pos.length;
      io += p.idx.length;
    }
    return write(T, pos, idx);
  }

  // How the modular-kit ops touch three: groups, clones, transforms, InstancedMesh. The layout
  // itself is engine-neutral and shared with makeOpsPc.
  const kitEngine = threeKitEngine(T);

  // The bakes want arrays and the normals the geometry is DRAWN with, so what is painted is what
  // the lamp sees. An object tree is merged first, like every other op here.
  function bakeTarget(geo: any, what: string): { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal?: Float32Array } {
    const g = geo?.isObject3D ? merge(geo) : geo;
    const { pos, idx } = read(g);
    const uv = g?.attributes?.uv?.array;
    if (!uv) throw new Error(what + ": the geometry has no uv — unwrap it first (ops.unwrap, or ops.atlas for several parts)");
    const n = g?.attributes?.normal?.array;
    return { pos, idx, uv: uv instanceof Float32Array ? uv : Float32Array.from(uv), normal: n ? Float32Array.from(n) : undefined };
  }
  function highOf(high: any): { pos: Float32Array; idx: Uint32Array } | undefined {
    if (!high) return undefined;
    return read(high.isObject3D ? merge(high) : high);
  }
  function materialOf(mat: SmartMaterial | string): SmartMaterial {
    if (typeof mat !== "string") return mat;
    const make = MATERIALS[mat];
    if (!make) throw new Error("no material '" + mat + "' — ops.materials has: " + Object.keys(MATERIALS).join(", "));
    return make();
  }

  const ops: Ops = {
    merge,
    weld(geo, tol = 1e-4) {
      const { pos, idx } = read(geo);
      const w = weldArrays(pos, idx, tol);
      return write(T, w.pos, w.idx);
    },
    smooth(geo, angleDeg = 40) {
      const { pos, idx } = read(geo);
      const s = smoothCornerNormals(pos, idx, angleDeg);
      // Corner normals mean no index: the same position carries two normals across a crease,
      // which is the whole point and cannot be expressed in an indexed buffer.
      return write(T, s.pos, null, s.normal);
    },
    subdivide(geo, levels = 1) {
      const { pos, idx } = read(geo);
      const s = subdivideArrays(pos, idx, levels);
      return write(T, s.pos, s.idx);
    },
    mirror(geo, axis = 0, weldSeam = true) {
      const { pos, idx } = read(geo);
      const m = mirrorArrays(pos, idx, axis, weldSeam);
      return write(T, m.pos, m.idx);
    },
    array(geo, count, offset, weldJoins = false) {
      const { pos, idx } = read(geo);
      const a = arrayArrays(pos, idx, count, offset, weldJoins);
      return write(T, a.pos, a.idx);
    },
    deform(geo, kind, amount, axis = 1) {
      const { pos, idx } = read(geo);
      return write(T, deformArrays(pos, kind, amount, axis), idx);
    },
    displace(geo, amp, freq = 3, octaves = 3, seed = 1) {
      const { pos, idx } = read(geo);
      const n = normalsFor(pos, idx);
      return write(T, displaceArrays(pos, n, amp, freq, octaves, seed), idx);
    },
    solidify(geo, thickness) {
      const { pos, idx } = read(geo);
      const n = normalsFor(pos, idx);
      const s = solidifyArrays(pos, idx, n, thickness);
      return write(T, s.pos, s.idx);
    },
    scatter(geo, count, seed = 1) {
      const { pos, idx } = read(geo);
      return scatterArrays(pos, idx, count, seed);
    },
    uv(geo, mode = "box", scale = 1) {
      const { pos, idx } = read(geo);
      const g = write(T, pos, idx);
      g.setAttribute("uv", new T.Float32BufferAttribute(uvArrays(pos, idx, mode, scale), 2));
      return g;
    },
    simplify(geo, cell) {
      const { pos, idx } = read(geo);
      const s = simplifyArrays(pos, idx, cell);
      return write(T, s.pos, s.idx);
    },
    flip(geo) {
      const { pos, idx } = read(geo);
      return write(T, pos, flipArrays(idx));
    },
    check(target) {
      const geo = target?.isObject3D ? merge(target) : target;
      const { pos, idx } = read(geo);
      return checkArrays(pos, idx, geo?.attributes?.normal?.array, geo?.attributes?.uv?.array);
    },
    skin(object, opts = {}) {
      const { tol = 1e-3, angle = 40, subdivide: levels = 0 } = opts;
      let geo = object?.isObject3D ? merge(object) : object;
      let { pos, idx } = read(geo);
      const w = weldArrays(pos, idx, tol);
      pos = w.pos; idx = w.idx;
      if (levels > 0) ({ pos, idx } = subdivideArrays(pos, idx, levels));
      const s = smoothCornerNormals(pos, idx, angle);
      geo = write(T, s.pos, null, s.normal);
      geo.userData = { welded: w.merged, triangles: idx.length / 3 };
      return geo;
    },
    hull(target) {
      let pts: Float32Array;
      if (target instanceof Float32Array) pts = target;
      else if (Array.isArray(target)) pts = Float32Array.from(target.flat(2) as number[]);
      else pts = read(target?.isObject3D ? merge(target) : target).pos;
      const h = hullArrays(pts);
      if (!h) throw new Error("hull: the points are flat or a line, nothing to wrap");
      const s = smoothCornerNormals(h.pos, h.idx, 0);   // flat faces: a hull is all creases
      const g = write(T, s.pos, null, s.normal);
      g.userData = { triangles: h.idx.length / 3 };
      return g;
    },
    boolean(a, b, op = "union") {
      const A = read(a?.isObject3D ? merge(a) : a), B = read(b?.isObject3D ? merge(b) : b);
      const r = csgArrays(A, B, op);
      const s = smoothCornerNormals(r.pos, r.idx, 40);
      const g = write(T, s.pos, null, s.normal);
      g.userData = { triangles: r.idx.length / 3, op };
      return g;
    },
    isosurface(field, min, max, res = 32, iso = 0) {
      const r = isosurfaceArrays(field, min, max, res, iso);
      const s = smoothCornerNormals(r.pos, r.idx, 80);
      return write(T, s.pos, null, s.normal);
    },
    blobs(balls, opts = {}) {
      const { res = 48, margin = 1.5, angle = 80 } = opts;
      const box = blobBounds(balls, margin, res);
      const r = isosurfaceArrays(blobField(balls), box.min, box.max, box.res, 0);
      const s = smoothCornerNormals(r.pos, r.idx, angle);
      const g = write(T, s.pos, null, s.normal);
      g.userData = { triangles: r.idx.length / 3, balls: balls.length };
      return g;
    },
    ik: fabrik,
    subsurf(geo, levels = 1, opts = {}) {
      const { pos, idx } = read(geo);
      const r = subdivideCC(pos, idx, levels, opts);
      const s = smoothCornerNormals(r.pos, r.idx, opts.creaseAngle ? Math.min(89, opts.creaseAngle) : 60);
      return write(T, s.pos, null, s.normal);
    },
    bevel(geo, opts = {}) {
      const { pos, idx } = read(geo);
      const r = bevelArrays(pos, idx, opts);
      const s = smoothCornerNormals(r.pos, r.idx, 40);
      return write(T, s.pos, null, s.normal);
    },
    unwrap(geo, opts = {}) {
      const { pos, idx } = read(geo);
      const r = unwrapArrays(pos, idx, opts);
      const g = write(T, r.pos, r.idx, normalsFor(r.pos, r.idx), r.uv);
      // Both names, because three reads lightmaps and ambient occlusion from the second set.
      // `uv1: false` leaves it out: exported, an unused second set is a TEXCOORD_1 of dead weight.
      if ((opts as { uv1?: boolean }).uv1 !== false) g.setAttribute("uv1", g.attributes.uv.clone());
      g.userData = { ...(g.userData || {}), islands: r.islands };
      return g;
    },
    remesh(geo, opts = {}) {
      const { pos, idx } = read(geo);
      const r = remeshArrays(pos, idx, opts);
      const s = smoothCornerNormals(r.pos, r.idx, 80);
      return write(T, s.pos, null, s.normal);
    },
    relax(geo, opts = {}) {
      const { pos, idx } = read(geo);
      const p = relaxArrays(pos, idx, opts);
      const g = write(T, p, idx, normalsFor(p, idx));
      if (geo?.attributes?.uv) g.setAttribute("uv", geo.attributes.uv.clone());
      return g;
    },
    heatWeights(geo, bones, opts = {}) {
      const { pos, idx } = read(geo);
      return heatWeightsArrays(pos, idx, bones, opts);
    },
    bakeAO(geo, size = 256, opts = {}) {
      const { pos, idx } = read(geo);
      const uv = geo?.attributes?.uv?.array;
      if (!uv) throw new Error("bakeAO: the geometry has no uv — unwrap it first");
      return bakeAO(pos, idx, uv instanceof Float32Array ? uv : Float32Array.from(uv), size, opts);
    },
    bakeCurvature(geo, size = 256, opts = {}) {
      const { pos, idx } = read(geo);
      const uv = geo?.attributes?.uv?.array;
      if (!uv) throw new Error("bakeCurvature: the geometry has no uv — unwrap it first");
      return bakeCurvature(pos, idx, uv instanceof Float32Array ? uv : Float32Array.from(uv), size, opts);
    },
    bakeNormalMap(low, high, size = 512, opts = {}) {
      const L = read(low), H = read(high?.isObject3D ? merge(high) : high);
      const uv = low?.attributes?.uv?.array;
      if (!uv) throw new Error("bakeNormalMap: the low-poly has no uv — unwrap it first");
      return bakeNormalMap({ pos: L.pos, idx: L.idx, uv: uv instanceof Float32Array ? uv : Float32Array.from(uv) }, H, size, opts);
    },
    texture(baked, opts = {}) {
      const tex = new T.DataTexture(baked.data, baked.width, baked.height, T.RGBAFormat);
      if (opts.srgb && T.SRGBColorSpace) tex.colorSpace = T.SRGBColorSpace;
      tex.flipY = false;
      tex.needsUpdate = true;
      return tex;
    },
    bake(geo, size, fn, opts = {}) {
      return bakeFunction(bakeTarget(geo, "bake"), size, fn, { ...opts, high: highOf(opts.high) });
    },
    bakeMaterial(geo, mat, size = 1024, opts = {}) {
      return bakeMaterial(bakeTarget(geo, "bakeMaterial"), materialOf(mat), size, { ...opts, high: highOf(opts.high) });
    },
    atlas(geos, opts = {}) {
      const src = geos.map((g) => (g?.isObject3D ? merge(g) : g));
      const out = atlasArrays(src.map((g, i) => {
        const { pos, idx } = read(g);
        const n = g?.attributes?.normal?.array;
        return { pos, idx, weight: opts.weights?.[i], normal: n ? Float32Array.from(n) : undefined };
      }), opts);
      return out.map((r, i) => {
        const g = write(T, r.pos, r.idx, r.normal, r.uv);
        // Vertex colours ride through the island split: `map` says which input vertex each came from.
        const col = src[i]?.attributes?.color;
        if (col && r.map) {
          const k = col.itemSize || 3, a = new Float32Array(r.map.length * k);
          for (let v = 0; v < r.map.length; v++) for (let c = 0; c < k; c++) a[v * k + c] = col.array[r.map[v] * k + c];
          g.setAttribute("color", new T.Float32BufferAttribute(a, k, !!col.normalized));
        }
        g.userData = { ...(g.userData || {}), atlas: true };
        return g;
      });
    },
    bakeAtlas(parts, size = 2048, opts = {}) {
      const geos = ops.atlas(parts.map((p) => p.geo), { margin: opts.margin, angle: opts.angle, weights: parts.map((p) => p.weight) });
      const arrays = geos.map((g, i) => ({ ...bakeTarget(g, "bakeAtlas"), mat: materialOf(parts[i].mat), high: highOf(parts[i].high) }));
      return { geos, maps: bakeAtlas(arrays, size, opts) };
    },
    applyMaps(material, maps, opts = {}) {
      return applyMaps(T, material, maps, opts);
    },
    materials: MATERIALS,
    hand(opts, res = 56, paint) {
      const h = handField(opts);
      const s = handSurface(h, res);
      const g = write(T, s.pos, s.idx, s.normal);
      if (paint) {
        // T.Color converts the hex from sRGB to the linear space vertex colours live in.
        const skin = new T.Color(paint.skin || "#9f9c53"), nail = new T.Color(paint.nail || "#d9cfae");
        const c = new Float32Array(s.pos.length);
        for (let i = 0; i < s.pos.length; i += 3) {
          const k = h.nail(s.pos[i], s.pos[i + 1], s.pos[i + 2]);
          c[i] = skin.r + (nail.r - skin.r) * k; c[i + 1] = skin.g + (nail.g - skin.g) * k; c[i + 2] = skin.b + (nail.b - skin.b) * k;
        }
        g.setAttribute("color", new T.Float32BufferAttribute(c, 3));
      }
      // Not enumerable: a GLB export copies userData into extras, and a field of closures has no place there.
      Object.defineProperty(g.userData, "hand", { value: h, enumerable: false, configurable: true });
      return g;
    },
    grip(object, o) {
      const hand: Hand | undefined = o.hand || object?.geometry?.userData?.hand || object?.userData?.hand;
      if (!hand) throw new Error("grip: no hand — pass { hand } or a mesh whose geometry came from ops.hand");
      const p = gripPlacement({ a: o.a, b: o.b, t: o.t, face: o.face, aim: o.aim, hand });
      object.position.fromArray(p.position);
      object.quaternion.fromArray(p.quaternion);
      object.updateMatrix?.();
      return p;
    },
    // The topology is kept as it came: a soup stays a soup, and smoothNormalsWelded shades across
    // the copies within `angle`, so a crease an agent split on purpose stays hard.
    densify(geo, maxEdge) {
      const { pos, idx } = read(geo?.isObject3D ? merge(geo) : geo);
      const r = densifyArrays(pos, idx, maxEdge);
      const g = write(T, r.pos, r.idx, smoothNormalsWelded(r.pos, r.idx, 80));
      g.userData = { triangles: r.idx.length / 3 };
      return g;
    },
    sculpt(geo, strokes, opts = {}) {
      const src = geo?.isObject3D ? merge(geo) : geo;
      const { pos, idx } = read(src);
      const p = sculptArrays(pos, idx, strokes);
      const g = write(T, p, src?.index ? idx : null, smoothNormalsWelded(p, idx, opts.angle ?? 80));
      // Same vertices in the same order, so every per-vertex attribute still fits.
      for (const name of ["uv", "uv1", "color"]) if (src?.attributes?.[name]) g.setAttribute(name, src.attributes[name].clone());
      g.userData = { triangles: idx.length / 3 };
      return g;
    },
    decimate(geo, opts = {}) {
      const src = geo?.isObject3D ? merge(geo) : geo;
      const { pos, idx } = read(src);
      const colA = src?.attributes?.color;
      const r = decimateArrays(pos, idx, { ...opts, uv: opts.uv ?? attrFloats(src?.attributes?.uv), color: opts.color ?? attrFloats(colA) });
      const g = write(T, r.pos, r.idx, smoothNormalsWelded(r.pos, r.idx, opts.angle ?? 60), r.uv);
      if (r.color) g.setAttribute("color", new T.Float32BufferAttribute(r.color, r.color.length / (r.pos.length / 3)));
      g.userData = { triangles: r.idx.length / 3, collapsed: r.collapsed, error: r.error };
      return g;
    },
    sweep(path, opts = {}) {
      const r = sweepArrays(path, opts);
      const g = write(T, r.pos, r.idx, r.normal, r.uv);
      g.userData = { triangles: r.idx.length / 3 };
      return g;
    },
    rim(geo, opts) {
      const { pos, idx } = read(geo?.isObject3D ? merge(geo) : geo);
      const r = rimArrays(pos, idx, opts);
      const g = write(T, r.pos, r.idx, r.normal, r.uv);
      g.userData = { triangles: r.idx.length / 3 };
      return g;
    },
    borders(geo, tol) {
      const { pos, idx } = read(geo?.isObject3D ? merge(geo) : geo);
      return boundaryLoops(pos, idx, tol);
    },
    reach,
    noise: noise3,
    fbm,
    rng,
    kit: (source, opts) => kitWith(kitEngine, source, opts),
    repeat: (piece, at, opts) => repeatWith(kitEngine, piece, at, opts),
    facade: (kit, opts, pick) => facadeWith(kitEngine, kit, opts, pick),
    bays: bayLayout,
    perimeter: perimeterSlots,
  };
  return ops;
}

// ------------------------------------------------------------------ convex hull
//
// Quickhull, in three dimensions. What Godot makes a collision shape from, and the fastest way
// to get a tight, closed, convex proxy for anything: hand it every vertex of a model and it
// returns the wrapping. Outward winding is guaranteed by construction — every face is oriented
// away from the centroid of the first tetrahedron, which stays strictly inside the hull.

interface HullFace { a: number; b: number; c: number; n: V3; d: number; out: number[]; alive: boolean }

export function hullArrays(points: Float32Array | number[]): { pos: Float32Array; idx: Uint32Array } | null {
  const P = points instanceof Float32Array ? points : Float32Array.from(points);
  const n = Math.floor(P.length / 3);
  if (n < 4) return null;
  const px = (i: number): V3 => [P[i * 3], P[i * 3 + 1], P[i * 3 + 2]];
  const lo: V3 = [Infinity, Infinity, Infinity], hi: V3 = [-Infinity, -Infinity, -Infinity];
  const ext = [0, 0, 0, 0, 0, 0];
  for (let i = 0; i < n; i++) {
    for (let k = 0; k < 3; k++) {
      const v = P[i * 3 + k];
      if (v < lo[k]) { lo[k] = v; ext[k * 2] = i; }
      if (v > hi[k]) { hi[k] = v; ext[k * 2 + 1] = i; }
    }
  }
  const scale = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) || 1;
  const eps = scale * 1e-7;
  const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);

  // The first tetrahedron: the two farthest extremes, the point farthest from their line, the
  // point farthest from that plane. Any of them degenerate means the cloud is flat or a line.
  let p0 = 0, p1 = 0, best = -1;
  for (let i = 0; i < 6; i++) for (let j = i + 1; j < 6; j++) {
    const d = len(sub(px(ext[i]), px(ext[j])));
    if (d > best) { best = d; p0 = ext[i]; p1 = ext[j]; }
  }
  if (best < eps) return null;
  const A = px(p0), B = px(p1), AB = sub(B, A);
  let p2 = -1; best = -1;
  for (let i = 0; i < n; i++) {
    const d = len(cross(AB, sub(px(i), A))) / len(AB);
    if (d > best) { best = d; p2 = i; }
  }
  if (p2 < 0 || best < eps) return null;
  const C = px(p2);
  const N0 = cross(AB, sub(C, A));
  const N0l = len(N0);
  let p3 = -1; best = -1;
  for (let i = 0; i < n; i++) {
    const d = Math.abs(dot(N0, sub(px(i), A))) / N0l;
    if (d > best) { best = d; p3 = i; }
  }
  if (p3 < 0 || best < eps) return null;
  const D = px(p3);
  const centroid: V3 = [(A[0] + B[0] + C[0] + D[0]) / 4, (A[1] + B[1] + C[1] + D[1]) / 4, (A[2] + B[2] + C[2] + D[2]) / 4];

  const faces: HullFace[] = [];
  const makeFace = (a: number, b: number, c: number): HullFace | null => {
    const pa = px(a);
    let nn = cross(sub(px(b), pa), sub(px(c), pa));
    const l = len(nn);
    if (l < 1e-20) return null;
    nn = [nn[0] / l, nn[1] / l, nn[2] / l];
    let d = dot(nn, pa);
    if (dot(nn, centroid) - d > 0) { [b, c] = [c, b]; nn = [-nn[0], -nn[1], -nn[2]]; d = -d; }
    const f: HullFace = { a, b, c, n: nn, d, out: [], alive: true };
    faces.push(f);
    return f;
  };
  const dist = (f: HullFace, i: number) => dot(f.n, px(i)) - f.d;
  const assign = (candidates: number[], targets: HullFace[]) => {
    for (const i of candidates) {
      let bestF: HullFace | null = null, bestD = eps;
      for (const f of targets) { const d = dist(f, i); if (d > bestD) { bestD = d; bestF = f; } }
      if (bestF) bestF.out.push(i);
    }
  };
  const first = [makeFace(p0, p1, p2), makeFace(p0, p1, p3), makeFace(p0, p2, p3), makeFace(p1, p2, p3)].filter(Boolean) as HullFace[];
  const rest: number[] = [];
  for (let i = 0; i < n; i++) if (i !== p0 && i !== p1 && i !== p2 && i !== p3) rest.push(i);
  assign(rest, first);

  // Grow: take a face with points outside it, its farthest point becomes a vertex, every face it
  // can see goes, and the ring of edges left behind (the horizon) is stitched to the new vertex.
  let cursor = 0;
  let guard = 0;
  while (cursor < faces.length && guard++ < 200000) {
    const f = faces[cursor];
    if (!f.alive || !f.out.length) { cursor++; continue; }
    let eye = f.out[0], bestD = -1;
    for (const i of f.out) { const d = dist(f, i); if (d > bestD) { bestD = d; eye = i; } }
    const visible: HullFace[] = [];
    for (const g of faces) if (g.alive && dist(g, eye) > eps) visible.push(g);
    const edges = new Set<string>();
    for (const g of visible) { edges.add(g.a + "," + g.b); edges.add(g.b + "," + g.c); edges.add(g.c + "," + g.a); }
    const horizon: Array<[number, number]> = [];
    for (const g of visible) {
      for (const [a, b] of [[g.a, g.b], [g.b, g.c], [g.c, g.a]] as Array<[number, number]>) {
        if (!edges.has(b + "," + a)) horizon.push([a, b]);
      }
    }
    const orphans: number[] = [];
    for (const g of visible) { g.alive = false; for (const i of g.out) if (i !== eye) orphans.push(i); g.out = []; }
    const fresh: HullFace[] = [];
    for (const [a, b] of horizon) { const nf = makeFace(a, b, eye); if (nf) fresh.push(nf); }
    assign(orphans, fresh);
  }

  const remap = new Map<number, number>();
  const pos: number[] = [];
  const idx: number[] = [];
  const at = (i: number) => {
    let r = remap.get(i);
    if (r === undefined) { r = pos.length / 3; remap.set(i, r); pos.push(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]); }
    return r;
  };
  for (const f of faces) if (f.alive) idx.push(at(f.a), at(f.b), at(f.c));
  return { pos: Float32Array.from(pos), idx: Uint32Array.from(idx) };
}

// ------------------------------------------------------------------ boolean
//
// Constructive solid geometry on a BSP tree — Evan Wallace's csg.js, on plain arrays. Union,
// subtraction and intersection of two closed meshes, which is how a window gets cut into a wall
// and a handle grows out of a mug. Godot ships this as CSG nodes; here it is a call.
//
// Honest limits, the same as every BSP boolean: both inputs must be closed and outward; the
// output is correct but not pretty (long thin triangles at the cuts — run `weld` and `smooth`
// after), and two meshes of thousands of faces take a second or two. Coplanar faces are handled.

interface CVert { p: V3; n: V3 }
interface CPoly { v: CVert[]; n: V3; w: number }
interface BSP { n: V3 | null; w: number; front: BSP | null; back: BSP | null; polys: CPoly[] }

const CSG_EPS = 1e-5;

function cPlane(a: V3, b: V3, c: V3): { n: V3; w: number } | null {
  const ab: V3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const ac: V3 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n: V3 = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]];
  const l = Math.hypot(n[0], n[1], n[2]);
  if (l < 1e-20) return null;
  n[0] /= l; n[1] /= l; n[2] /= l;
  return { n, w: n[0] * a[0] + n[1] * a[1] + n[2] * a[2] };
}

function cSplit(pn: V3, pw: number, poly: CPoly, eps: number, coFront: CPoly[], coBack: CPoly[], front: CPoly[], back: CPoly[]) {
  const COPLANAR = 0, FRONT = 1, BACK = 2, SPANNING = 3;
  let type = 0;
  const types: number[] = [];
  for (const v of poly.v) {
    const t = pn[0] * v.p[0] + pn[1] * v.p[1] + pn[2] * v.p[2] - pw;
    const ty = t < -eps ? BACK : t > eps ? FRONT : COPLANAR;
    type |= ty;
    types.push(ty);
  }
  switch (type) {
    case COPLANAR: (pn[0] * poly.n[0] + pn[1] * poly.n[1] + pn[2] * poly.n[2] > 0 ? coFront : coBack).push(poly); break;
    case FRONT: front.push(poly); break;
    case BACK: back.push(poly); break;
    case SPANNING: {
      const f: CVert[] = [], b: CVert[] = [];
      for (let i = 0; i < poly.v.length; i++) {
        const j = (i + 1) % poly.v.length;
        const ti = types[i], tj = types[j];
        const vi = poly.v[i], vj = poly.v[j];
        if (ti !== BACK) f.push(vi);
        if (ti !== FRONT) b.push(ti !== BACK ? { p: vi.p.slice() as V3, n: vi.n.slice() as V3 } : vi);
        if ((ti | tj) === SPANNING) {
          const di = pn[0] * vi.p[0] + pn[1] * vi.p[1] + pn[2] * vi.p[2] - pw;
          const dj = pn[0] * vj.p[0] + pn[1] * vj.p[1] + pn[2] * vj.p[2] - pw;
          const t = di / (di - dj);
          const v: CVert = {
            p: [vi.p[0] + (vj.p[0] - vi.p[0]) * t, vi.p[1] + (vj.p[1] - vi.p[1]) * t, vi.p[2] + (vj.p[2] - vi.p[2]) * t],
            n: [vi.n[0] + (vj.n[0] - vi.n[0]) * t, vi.n[1] + (vj.n[1] - vi.n[1]) * t, vi.n[2] + (vj.n[2] - vi.n[2]) * t],
          };
          f.push(v);
          b.push({ p: v.p.slice() as V3, n: v.n.slice() as V3 });
        }
      }
      if (f.length >= 3) front.push({ v: f, n: poly.n, w: poly.w });
      if (b.length >= 3) back.push({ v: b, n: poly.n, w: poly.w });
      break;
    }
  }
}

function cFlip(poly: CPoly): CPoly {
  return {
    v: poly.v.slice().reverse().map((v) => ({ p: v.p, n: [-v.n[0], -v.n[1], -v.n[2]] as V3 })),
    n: [-poly.n[0], -poly.n[1], -poly.n[2]], w: -poly.w,
  };
}

function bspNew(): BSP { return { n: null, w: 0, front: null, back: null, polys: [] }; }

function bspBuild(node: BSP, polys: CPoly[], eps: number) {
  if (!polys.length) return;
  if (!node.n) { node.n = polys[0].n.slice() as V3; node.w = polys[0].w; }
  const front: CPoly[] = [], back: CPoly[] = [];
  for (const p of polys) cSplit(node.n, node.w, p, eps, node.polys, node.polys, front, back);
  if (front.length) { if (!node.front) node.front = bspNew(); bspBuild(node.front, front, eps); }
  if (back.length) { if (!node.back) node.back = bspNew(); bspBuild(node.back, back, eps); }
}

function bspInvert(node: BSP) {
  node.polys = node.polys.map(cFlip);
  if (node.n) { node.n = [-node.n[0], -node.n[1], -node.n[2]]; node.w = -node.w; }
  if (node.front) bspInvert(node.front);
  if (node.back) bspInvert(node.back);
  const t = node.front; node.front = node.back; node.back = t;
}

function bspClipPolys(node: BSP, polys: CPoly[], eps: number): CPoly[] {
  if (!node.n) return polys.slice();
  let front: CPoly[] = [], back: CPoly[] = [];
  for (const p of polys) cSplit(node.n, node.w, p, eps, front, back, front, back);
  if (node.front) front = bspClipPolys(node.front, front, eps);
  back = node.back ? bspClipPolys(node.back, back, eps) : [];
  return front.concat(back);
}

function bspClipTo(node: BSP, other: BSP, eps: number) {
  node.polys = bspClipPolys(other, node.polys, eps);
  if (node.front) bspClipTo(node.front, other, eps);
  if (node.back) bspClipTo(node.back, other, eps);
}

function bspAll(node: BSP, out: CPoly[] = []): CPoly[] {
  out.push(...node.polys);
  if (node.front) bspAll(node.front, out);
  if (node.back) bspAll(node.back, out);
  return out;
}

function toPolys(pos: Float32Array, idx: Uint32Array): CPoly[] {
  const out: CPoly[] = [];
  for (let t = 0; t < idx.length; t += 3) {
    const a = idx[t] * 3, b = idx[t + 1] * 3, c = idx[t + 2] * 3;
    const pa: V3 = [pos[a], pos[a + 1], pos[a + 2]], pb: V3 = [pos[b], pos[b + 1], pos[b + 2]], pc: V3 = [pos[c], pos[c + 1], pos[c + 2]];
    const pl = cPlane(pa, pb, pc);
    if (!pl) continue;
    out.push({ v: [{ p: pa, n: pl.n }, { p: pb, n: pl.n }, { p: pc, n: pl.n }], n: pl.n, w: pl.w });
  }
  return out;
}

export type BooleanOp = "union" | "subtract" | "intersect";

export function csgArrays(A: { pos: Float32Array; idx: Uint32Array }, B: { pos: Float32Array; idx: Uint32Array }, op: BooleanOp)
  : { pos: Float32Array; idx: Uint32Array } {
  let lo = Infinity, hi = -Infinity;
  for (const p of [A.pos, B.pos]) for (let i = 0; i < p.length; i++) { if (p[i] < lo) lo = p[i]; if (p[i] > hi) hi = p[i]; }
  const eps = CSG_EPS * Math.max(1e-6, hi - lo);
  const a = bspNew(), b = bspNew();
  bspBuild(a, toPolys(A.pos, A.idx), eps);
  bspBuild(b, toPolys(B.pos, B.idx), eps);
  if (op === "union") {
    bspClipTo(a, b, eps); bspClipTo(b, a, eps); bspInvert(b); bspClipTo(b, a, eps); bspInvert(b);
    bspBuild(a, bspAll(b), eps);
  } else if (op === "subtract") {
    bspInvert(a); bspClipTo(a, b, eps); bspClipTo(b, a, eps); bspInvert(b); bspClipTo(b, a, eps); bspInvert(b);
    bspBuild(a, bspAll(b), eps); bspInvert(a);
  } else {
    bspInvert(a); bspClipTo(b, a, eps); bspInvert(b); bspClipTo(a, b, eps); bspClipTo(b, a, eps);
    bspBuild(a, bspAll(b), eps); bspInvert(a);
  }
  const P: number[] = [], I: number[] = [];
  for (const poly of bspAll(a)) {
    const base = P.length / 3;
    for (const v of poly.v) P.push(v.p[0], v.p[1], v.p[2]);
    for (let i = 2; i < poly.v.length; i++) I.push(base, base + i - 1, base + i);
  }
  // The cuts leave every polygon with its own vertices; welding at the working tolerance gives
  // back shared edges, which is what `check` and `smooth` need to see a surface rather than shards.
  const w = weldArrays(Float32Array.from(P), Uint32Array.from(I), eps);
  return { pos: w.pos, idx: w.idx };
}

// ------------------------------------------------------------------ isosurface
//
// A skin over a scalar field: what marching cubes does, done with naive surface nets instead.
// Same job — inside is where the field is below the level, the surface is where it crosses —
// with no lookup tables at all: one vertex per crossed cell, placed at the mean of the crossings
// on its edges, and one quad per crossed grid edge. The result is closed and manifold wherever
// the field is inside the box, and smoother than marching cubes' output for the same grid,
// which is what an organic body wants. `blobField` turns a handful of spheres into such a field.

export function isosurfaceArrays(field: (x: number, y: number, z: number) => number, min: V3, max: V3,
  res: V3 | number = 32, iso = 0): { pos: Float32Array; idx: Uint32Array } {
  const R: V3 = typeof res === "number" ? [res, res, res] : res;
  const nx = Math.max(2, Math.round(R[0])), ny = Math.max(2, Math.round(R[1])), nz = Math.max(2, Math.round(R[2]));
  const hx = (max[0] - min[0]) / nx, hy = (max[1] - min[1]) / ny, hz = (max[2] - min[2]) / nz;
  const sx = nx + 1, sy = ny + 1, sz = nz + 1;
  const S = new Float32Array(sx * sy * sz);
  const si = (i: number, j: number, k: number) => i + sx * (j + sy * k);
  for (let k = 0; k < sz; k++) for (let j = 0; j < sy; j++) for (let i = 0; i < sx; i++) {
    S[si(i, j, k)] = field(min[0] + i * hx, min[1] + j * hy, min[2] + k * hz) - iso;
  }
  const cellIndex = new Int32Array(nx * ny * nz).fill(-1);
  const ci = (i: number, j: number, k: number) => i + nx * (j + ny * k);
  const pos: number[] = [];
  // Corner c0..c7 = (i+dx, j+dy, k+dz) with dx = bit0, dy = bit1, dz = bit2; the twelve edges.
  const E = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
  const corner = new Float32Array(8);
  for (let k = 0; k < nz; k++) for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    let mask = 0;
    for (let c = 0; c < 8; c++) {
      const v = S[si(i + (c & 1), j + ((c >> 1) & 1), k + ((c >> 2) & 1))];
      corner[c] = v;
      if (v < 0) mask |= 1 << c;
    }
    if (mask === 0 || mask === 255) continue;
    let ax = 0, ay = 0, az = 0, cnt = 0;
    for (const [a, b] of E) {
      const va = corner[a], vb = corner[b];
      if ((va < 0) === (vb < 0)) continue;
      const t = va / (va - vb);
      const x0 = a & 1, y0 = (a >> 1) & 1, z0 = (a >> 2) & 1;
      const x1 = b & 1, y1 = (b >> 1) & 1, z1 = (b >> 2) & 1;
      ax += x0 + (x1 - x0) * t; ay += y0 + (y1 - y0) * t; az += z0 + (z1 - z0) * t;
      cnt++;
    }
    cellIndex[ci(i, j, k)] = pos.length / 3;
    pos.push(min[0] + (i + ax / cnt) * hx, min[1] + (j + ay / cnt) * hy, min[2] + (k + az / cnt) * hz);
  }
  const idx: number[] = [];
  // For every grid edge with a crossing, the four cells around it make a quad. The cell offsets
  // follow one pattern rotated through the axes, so all three orientations agree: counter-
  // clockwise seen from the positive axis when the inside is at the edge's first sample.
  const Q = [[-1, -1], [0, -1], [0, 0], [-1, 0]];
  const quad = (cells: number[], flip: boolean) => {
    if (cells.some((c) => c < 0)) return;
    const [a, b, c, d] = flip ? [cells[0], cells[3], cells[2], cells[1]] : cells;
    idx.push(a, b, c, a, c, d);
  };
  for (let k = 0; k < sz; k++) for (let j = 0; j < sy; j++) for (let i = 0; i < sx; i++) {
    const s0 = S[si(i, j, k)];
    if (i < nx && j > 0 && k > 0 && j < ny && k < nz) {
      const s1 = S[si(i + 1, j, k)];
      if ((s0 < 0) !== (s1 < 0)) quad(Q.map(([u, v]) => cellIndex[ci(i, j + u, k + v)]), s0 >= 0);
    }
    if (j < ny && k > 0 && i > 0 && k < nz && i < nx) {
      const s1 = S[si(i, j + 1, k)];
      if ((s0 < 0) !== (s1 < 0)) quad(Q.map(([u, v]) => cellIndex[ci(i + v, j, k + u)]), s0 >= 0);
    }
    if (k < nz && i > 0 && j > 0 && i < nx && j < ny) {
      const s1 = S[si(i, j, k + 1)];
      if ((s0 < 0) !== (s1 < 0)) quad(Q.map(([u, v]) => cellIndex[ci(i + u, j + v, k)]), s0 >= 0);
    }
  }
  return { pos: Float32Array.from(pos), idx: Uint32Array.from(idx) };
}

export interface Blob { p: V3; r: number }

/** Metaballs: below zero inside, zero on the skin, one sphere's worth of pull each. */
export function blobField(balls: Blob[]): (x: number, y: number, z: number) => number {
  return (x, y, z) => {
    let s = 0;
    for (const b of balls) {
      const dx = x - b.p[0], dy = y - b.p[1], dz = z - b.p[2];
      s += (b.r * b.r) / (dx * dx + dy * dy + dz * dz + 1e-9);
    }
    return 1 - s;
  };
}

/** The box that holds every ball with room for the blend, and a grid with `res` cells along its
 *  longest side. Returned so a caller can reuse the same box for a second pass. */
export function blobBounds(balls: Blob[], margin = 1.5, res = 48): { min: V3; max: V3; res: V3 } {
  const min: V3 = [Infinity, Infinity, Infinity], max: V3 = [-Infinity, -Infinity, -Infinity];
  for (const b of balls) for (let k = 0; k < 3; k++) {
    min[k] = Math.min(min[k], b.p[k] - b.r * margin);
    max[k] = Math.max(max[k], b.p[k] + b.r * margin);
  }
  const size = [max[0] - min[0], max[1] - min[1], max[2] - min[2]];
  const big = Math.max(size[0], size[1], size[2]) || 1;
  const r: V3 = [Math.max(4, Math.round((res * size[0]) / big)), Math.max(4, Math.round((res * size[1]) / big)), Math.max(4, Math.round((res * size[2]) / big))];
  return { min, max, res: r };
}

// ------------------------------------------------------------------ inverse kinematics
//
// FABRIK: forward and backward reaching. A chain of joints, a fixed root, a target for the tip;
// each pass slides the joints along the lines to their neighbours so every segment keeps its
// length. It converges in a handful of passes, never explodes, and needs no Jacobian — which is
// why it is the solver in most game engines' feet and hands. Positions only: turning them back
// into bone rotations is the rig's job (`solveChain` in rig.ts).

export function fabrik(joints: V3[], target: V3, opts: { iterations?: number; tolerance?: number } = {})
  : { joints: V3[]; reached: boolean; distance: number } {
  const n = joints.length;
  const out = joints.map((j) => j.slice() as V3);
  if (n < 2) return { joints: out, reached: false, distance: Infinity };
  const lens: number[] = [];
  let total = 0;
  for (let i = 0; i < n - 1; i++) {
    const l = Math.hypot(out[i + 1][0] - out[i][0], out[i + 1][1] - out[i][1], out[i + 1][2] - out[i][2]);
    lens.push(l); total += l;
  }
  const root = out[0].slice() as V3;
  const iterations = opts.iterations ?? 16;
  const tol = opts.tolerance ?? total * 1e-4;
  const dist = (a: V3, b: V3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  const towards = (from: V3, to: V3, l: number): V3 => {
    const d = dist(from, to) || 1e-12;
    const t = l / d;
    return [from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t, from[2] + (to[2] - from[2]) * t];
  };
  if (dist(root, target) >= total) {
    // Out of reach: the chain points at the target, fully extended.
    for (let i = 0; i < n - 1; i++) out[i + 1] = towards(out[i], target, lens[i]);
    return { joints: out, reached: false, distance: dist(out[n - 1], target) };
  }
  let d = dist(out[n - 1], target);
  for (let it = 0; it < iterations && d > tol; it++) {
    out[n - 1] = target.slice() as V3;
    for (let i = n - 2; i >= 0; i--) out[i] = towards(out[i + 1], out[i], lens[i]);
    out[0] = root.slice() as V3;
    for (let i = 0; i < n - 1; i++) out[i + 1] = towards(out[i], out[i + 1], lens[i]);
    d = dist(out[n - 1], target);
  }
  return { joints: out, reached: d <= tol, distance: d };
}

// The modelling tools live in model.ts and ride along in this bundle, so `forge-ops.js` is one
// import for everything: subsurf, bevel, unwrap, the bakes, heat weights, remesh, relax.
export * from "./model";
// The Blender recipe in code, from the goblin A/B (data/ab/goblin/, 2026-09-24): smart materials
// baked into one atlas with a correct normal map (material.ts, atlas.ts).
export * from "./material";
export * from "./atlas";
// Complete stylised hands as one smooth surface, and the grip that puts a fist on a handle (hands.ts).
export * from "./hands";
// Sculpt as data (densify, then strokes), quadric decimation to a game budget, and swept tubes,
// rolled rims and two-bone reach (sculpt.ts, decimate.ts, sweep.ts).
export * from "./sculpt";
export * from "./decimate";
export * from "./sweep";

// ------------------------------------------------------------------ a scene, by name
//
// Everything the editor saves is keyed by NAME, because a name is the only thing that survives
// running the code again. These three functions are the other half of that promise: the same
// keys the editor uses, computed anywhere; the modifier stack, applied anywhere; and the whole
// sidecar, applied to a freshly built scene by the game itself. Without them an edit made in the
// editor would only ever exist in the editor.

/**
 * A stable key for every object under a root. A unique name is its own key; a repeated name gets
 * Blender's `.001` suffix in traversal order; an unnamed object falls back to its index path,
 * which survives a rebuild of the same code. The editor and `applyEdits` both use this, so a key
 * written by one is always found by the other.
 */
export function stableKeys(root: any): Map<any, string> {
  const keys = new Map<any, string>();
  const counts = new Map<string, number>();
  // A piece the editor split out of a merged mesh carries its own key (`mesh~t12-40`, see
  // pieces.ts): the game has no object by that name to count, so it is kept out of the counts.
  const pieceOf = (o: any): string => (typeof o?.userData?.pieceKey === "string" ? o.userData.pieceKey : "");
  // A PLACEMENT is not the game's either. A copy of a brainrot carries the same inner names as the
  // one the game drew, and counting them together renumbered the game's own keys while the copy
  // existed — 347 of them on rot-rush — so an edit made then was saved under a key that meant
  // another object after a reload. A placed subtree is kept out of the counts and keyed apart: its
  // root by its name, or `name#<placement id>` when the game or another placement has that name,
  // and everything inside it under the root's key. live_scene.py's page script does the same.
  const placedOf = (o: any): string => String(o?.userData?.studioPlaced || o?.__studioPlaced || "");
  // By `children`, not by `traverse`: a three Object3D has both, a PlayCanvas entity has only the
  // first, and the keys have to be the same function for both or the sidecar means two things.
  const count = (o: any) => {
    if (o !== root && placedOf(o)) return;
    if (o !== root && o.name && !pieceOf(o)) counts.set(o.name, (counts.get(o.name) || 0) + 1);
    for (const c of o?.children || []) count(c);
  };
  count(root);
  const used = new Map<string, number>();
  const placed: [any, string][] = [];
  const walk = (o: any, path: string) => {
    if (o !== root) {
      if (placedOf(o)) { placed.push([o, path]); return; }
      const n: string = o.name || "";
      if (pieceOf(o)) keys.set(o, pieceOf(o));
      else if (n && counts.get(n) === 1) keys.set(o, n);
      else if (n) { const i = used.get(n) || 0; used.set(n, i + 1); keys.set(o, n + "." + String(i).padStart(3, "0")); }
      else keys.set(o, path);
    }
    (o.children || []).forEach((c: any, i: number) => walk(c, path + "/" + i));
  };
  walk(root, "");
  if (placed.length) {
    const names = new Map<string, number>();
    for (const [o] of placed) if (o.name) names.set(o.name, (names.get(o.name) || 0) + 1);
    for (const [o, path] of placed) {
      const n: string = o.name || "";
      const k = n && !counts.has(n) && names.get(n) === 1 ? n : (n || path) + "#" + placedOf(o);
      keys.set(o, k);
      for (const [c, ck] of stableKeys(o)) keys.set(c, k + "/" + ck);
    }
  }
  return keys;
}

/** Every node under (and including) a root, engine-neutral. */
export function walkTree(o: any, cb: (o: any) => void) {
  cb(o);
  for (const c of o?.children || []) walkTree(c, cb);
}

// ---------------------------------------------------------------------------
// VERTICES THE HAND MOVED
//
// Blender stores its mesh, so vertex 4127 is vertex 4127 forever, and edit mode is easy. Ours is
// generated. The code runs again every time a parameter moves, and the vertex buffer is rebuilt
// from nothing, so an index is worthless as a name: the same index is a different corner of the
// model after any change that alters a segment count.
//
// A vertex is therefore keyed the only way that survives -- BY WHERE IT WAS. The key is its rest
// position inside the geometry that holds it, bucketed to a tolerance, and every rebuild re-binds
// against the new buffer. A parameter change elsewhere leaves the key pointing at the same corner
// of the model. A change that moves this region orphans the edit, and the report SAYS so instead
// of silently moving some other vertex, which is the failure that would destroy trust in the
// whole feature.
//
// The document only ever stores vertices. An edge dragged in the editor is two entries and a face
// is three, because "edge 12" is no more stable than "vertex 4127" and one concept is enough.

export interface VertEdit {
  /** Stable key of the MESH, not of the named part above it. A part can be a group of several
   *  meshes at different transforms, and a position only means one thing inside the geometry
   *  that actually holds it. */
  mesh: string;
  /** Where the vertex was when the code built it. The FIRST key. */
  at: V3;
  /** Where the hand put it. */
  to: V3;
  /** Which corner it was, counting in the order the code emitted them. The SECOND key, used only
   *  when `sig` still matches — see `applyVertEdits`. */
  g?: number;
  /** The mesh's shape when the edit was made: corners and faces. If a rebuild changes either,
   *  the ordinal means nothing and the edit is orphaned rather than guessed at. */
  sig?: string;
}

/** The shape of a mesh, coarse enough to be stable and specific enough to be worth trusting.
 *  Same corner count and same face count means the builder ran the same path. */
export function topoSig(corners: number, faces: number): string {
  return corners + ":" + faces;
}

/** Vertices closer together than this are one corner of the model. Generated geometry is full of
 *  them: a box is 24 vertices for 8 corners, and moving one of a coincident three tears the
 *  surface open. Everything in the bucket moves together. */
export const VERT_TOL = 1e-3;

/** The bucket a rest position falls in. `-0` stringifies as `"0"`, so a mirrored seam that lands
 *  exactly on the axis does not key itself twice. */
export function vertKey(x: number, y: number, z: number, tol = VERT_TOL): string {
  const q = (v: number) => String(Math.round(v / tol));
  return q(x) + "," + q(y) + "," + q(z);
}

export interface BoundVerts {
  /** Buffer index, to where that vertex must go. One saved edit fills several entries when the
   *  corner is duplicated, which in generated geometry is the normal case rather than the odd one. */
  found: Map<number, V3>;
  /** Edits whose vertex is not in this mesh any more. The code changed under them. */
  orphans: VertEdit[];
}

/**
 * Match saved edits to the vertices of a freshly built buffer.
 *
 * The exact bucket first, which is the common case and is a hash lookup. A miss falls back to the
 * 26 neighbouring buckets with a real distance test, so a vertex that rebuilt a hair the other
 * side of a bucket edge is found rather than orphaned by a rounding boundary.
 */
/**
 * Every vertex bucketed by place: the corners of the mesh.
 *
 * The one grouping rule in the codebase. The editor draws these as the dots you click, the binder
 * matches saved edits against them, and a corner has to mean the same thing to both or what you
 * select is not what gets saved.
 */
export function groupVerts(pos: Float32Array, tol = VERT_TOL):
  { groups: number[][]; groupOf: Int32Array; buckets: Map<string, number[]> } {
  const n = Math.max(0, Math.floor(pos.length / 3));
  const groups: number[][] = [];
  const groupOf = new Int32Array(n).fill(-1);
  const buckets = new Map<string, number[]>();
  const ordinal = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    const k = vertKey(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2], tol);
    let g = ordinal.get(k);
    if (g === undefined) {
      g = groups.length;
      const b: number[] = [];
      groups.push(b);
      buckets.set(k, b);
      ordinal.set(k, g);
    }
    groups[g].push(i);
    groupOf[i] = g;
  }
  return { groups, groupOf, buckets };
}

export function bindVerts(pos: Float32Array, verts: VertEdit[], tol = VERT_TOL): BoundVerts {
  const found = new Map<number, V3>();
  const orphans: VertEdit[] = [];
  if (!verts.length || !pos?.length) {
    for (const v of verts) orphans.push(v);
    return { found, orphans };
  }

  const { buckets } = groupVerts(pos, tol);
  const tol2 = tol * tol;
  for (const v of verts) {
    const ax = v.at[0], ay = v.at[1], az = v.at[2];
    let hits = buckets.get(vertKey(ax, ay, az, tol));
    if (!hits) {
      const near: number[] = [];
      const qx = Math.round(ax / tol), qy = Math.round(ay / tol), qz = Math.round(az / tol);
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          for (let dz = -1; dz <= 1; dz++) {
            const b = buckets.get((qx + dx) + "," + (qy + dy) + "," + (qz + dz));
            if (!b) continue;
            for (const i of b) {
              const ex = pos[i * 3] - ax, ey = pos[i * 3 + 1] - ay, ez = pos[i * 3 + 2] - az;
              if (ex * ex + ey * ey + ez * ez <= tol2) near.push(i);
            }
          }
        }
      }
      hits = near.length ? near : undefined;
    }
    if (!hits || !hits.length) { orphans.push(v); continue; }
    for (const i of hits) found.set(i, v.to);
  }
  return { found, orphans };
}

/**
 * The buffer as the CODE built it, cached on the object.
 *
 * Both keys are read from this and never from the live buffer. `at` is a position the code
 * produced, so it can only match the code's own numbers; and the hand's offset has to be measured
 * from the code's position or a second application adds it a second time.
 *
 * Set only if absent, which is exactly right: a rebuild makes new objects with empty userData, so
 * the snapshot is always of the freshest build and nothing has to be invalidated.
 */
export function restBuffer(o: any): Float32Array | null {
  const a = o?.geometry?.attributes?.position?.array as Float32Array | undefined;
  if (!a) return null;
  if (!o.userData) o.userData = {};
  if (!o.userData.__rest || o.userData.__rest.length !== a.length) {
    o.userData.__rest = new Float32Array(a);
  }
  return o.userData.__rest as Float32Array;
}

/**
 * A COPY of the buffer with the saved moves in it. Pure, so it is testable without a renderer.
 *
 * TWO KEYS, TRIED IN ORDER, and the order is the whole design.
 *
 * First the PLACE. If the code put the vertex back where it was — which is what happens whenever
 * the parameter you moved affects some other part of the model — the saved absolute position is
 * applied. Absolute, so applying the document twice cannot drift.
 *
 * Then the ORDINAL, and only if `faces` says the mesh is still the same shape. A parameter that
 * re-displaces every vertex (noise amplitude, radius, a warp) leaves the corner COUNT and the face
 * count alone: the builder ran the same path and emitted the same corners in the same order, just
 * in different places. Corner 47 is still corner 47, so the hand's OFFSET is carried onto wherever
 * the code has now put it. This is the case a stored-mesh editor never has to solve and cannot.
 *
 * Otherwise the edit is orphaned and named. Snapping to "the nearest vertex" at this point would
 * deform an unrelated part of the model, and nobody would trust the feature again.
 */
export function applyVertEdits(
  pos: Float32Array, verts: VertEdit[], tol = VERT_TOL, faces = -1, rest?: Float32Array | null,
): { pos: Float32Array; moved: number; byPlace: number; byOrdinal: number; orphans: VertEdit[] } {
  // BOTH KEYS READ THE REST BUFFER. Without `rest` the live buffer is the best available guess,
  // which is correct on a fresh build and is what a caller with no snapshot gets.
  const base = rest && rest.length === pos.length ? rest : pos;
  const b = bindVerts(base, verts, tol);
  const byPlace = b.found.size;
  const orphans: VertEdit[] = [];

  if (b.orphans.length) {
    const { groups } = groupVerts(base, tol);
    const sig = topoSig(groups.length, faces);
    for (const v of b.orphans) {
      const g = v.g;
      // Both halves of the signature have to agree, and the ordinal has to exist. `faces` of -1
      // means the caller could not say, and an unknown shape is not a matching one.
      if (faces < 0 || !v.sig || v.sig !== sig || typeof g !== "number" || !groups[g]) {
        orphans.push(v);
        continue;
      }
      const i0 = groups[g][0];
      const dx = v.to[0] - v.at[0], dy = v.to[1] - v.at[1], dz = v.to[2] - v.at[2];
      // From the REST position. From the live one, a second application would add the offset a
      // second time and the peak would climb a little further every rebuild.
      const to: V3 = [base[i0 * 3] + dx, base[i0 * 3 + 1] + dy, base[i0 * 3 + 2] + dz];
      for (const i of groups[g]) b.found.set(i, to);
    }
  }

  if (!b.found.size) return { pos, moved: 0, byPlace: 0, byOrdinal: 0, orphans };
  const out = new Float32Array(pos);
  for (const [i, to] of b.found) {
    out[i * 3] = to[0];
    out[i * 3 + 1] = to[1];
    out[i * 3 + 2] = to[2];
  }
  return { pos: out, moved: b.found.size, byPlace, byOrdinal: b.found.size - byPlace, orphans };
}

/** Every saved edit onto a freshly built tree, grouped by the mesh that holds it.
 *
 *  Runs BEFORE the modifier stack, because a modifier rewrites the vertex buffer. In Blender you
 *  move vertices on the base mesh and the stack evaluates on top of the result; this is that same
 *  order, and any other one would make a subdivide silently discard your hand work. */
export function applyVerts(root: any, verts: VertEdit[] | undefined, out?: AppliedEdits): number {
  const list = (Array.isArray(verts) ? verts : []).filter(
    (v) => v && v.mesh && Array.isArray(v.at) && Array.isArray(v.to));
  if (!list.length) return 0;

  const byMesh = new Map<string, VertEdit[]>();
  for (const v of list) {
    const g = byMesh.get(v.mesh);
    if (g) g.push(v);
    else byMesh.set(v.mesh, [v]);
  }
  const byKey = new Map<string, any>();
  for (const [o, k] of stableKeys(root)) byKey.set(k, o);

  let moved = 0;
  for (const [key, group] of byMesh) {
    const o = byKey.get(key);
    const attr = o?.geometry?.attributes?.position;
    if (!attr?.array) { out?.missing.push(key); continue; }
    // The face count completes the signature. Indexed or not, it is the number of triangles the
    // builder emitted, and it changes the moment a segment count does.
    const faces = o.geometry.index
      ? Math.floor((o.geometry.index.count || 0) / 3)
      : Math.floor((attr.count || 0) / 3);
    const r = applyVertEdits(attr.array as Float32Array, group, VERT_TOL, faces, restBuffer(o));
    if (r.moved) {
      (attr.array as Float32Array).set(r.pos);
      attr.needsUpdate = true;
      // The surface moved, so the shading has to follow it. Without this the model lights as if
      // nothing had happened and the edit looks like it did not take.
      o.geometry.computeVertexNormals?.();
      o.geometry.computeBoundingSphere?.();
      moved += r.moved;
    }
    for (const v of r.orphans) {
      out?.errors.push("vertex at " + v.at.map((n) => n.toFixed(3)).join(", ")
        + " is not in " + key + " any more");
    }
  }
  return moved;
}

/** One modifier, run over one geometry (or, for `skin`, a whole object). Null for an unknown op. */
export function applyMod(ops: Ops, m: { op: string; args?: Record<string, any> }, geo: any, object?: any): any {
  const a = m.args || {};
  const num = (k: string, d: number) => (typeof a[k] === "number" ? (a[k] as number) : d);
  switch (m.op) {
    case "weld": return ops.weld(geo, num("tol", 1e-3));
    case "smooth": return ops.smooth(geo, num("angle", 40));
    case "subdivide": return ops.subdivide(geo, num("levels", 1));
    case "mirror": return ops.mirror(geo, (num("axis", 0) as 0 | 1 | 2), a.weld !== false);
    case "solidify": return ops.solidify(geo, num("thickness", 0.05));
    case "displace": return ops.displace(geo, num("amp", 0.05), num("freq", 3), num("octaves", 3), num("seed", 1));
    case "simplify": return ops.simplify(geo, num("cell", 0.05));
    case "flip": return ops.flip(geo);
    case "skin": return ops.skin(object || geo, { tol: num("tol", 1e-3), angle: num("angle", 40), subdivide: num("levels", 0) });
    case "subsurf": return ops.subsurf(geo, num("levels", 1), { creaseAngle: num("crease", 0), sharpness: num("sharp", 99) });
    case "bevel": return ops.bevel(geo, { width: num("width", 0.05), segments: num("segments", 3), angle: num("angle", 30) });
    case "unwrap": return ops.unwrap(geo, { angle: num("angle", 66), margin: num("margin", 0.02) });
    case "remesh": return ops.remesh(geo, { res: num("res", 48) });
    case "relax": return ops.relax(geo, { iterations: num("iterations", 5) });
    default: return null;
  }
}

const OVERRIDE_NUMBERS = ["roughness", "metalness", "intensity", "distance", "angle", "penumbra", "decay", "fov", "near", "far", "zoom"];

export interface AppliedEdits { parts: number; mods: number; verts: number; missing: string[]; errors: string[] }

import {
  bakeAO, bakeCurvature, bakeNormalMap, bevelArrays, heatWeightsArrays, relaxArrays, remeshArrays, subdivideCC, unwrapArrays,
  type Baked,
} from "./model";
import { movePiece, parsePieceKey } from "./pieces";
import { MATERIALS, applyMaps, bakeAtlas, bakeFunction, bakeMaterial, type SmartMaterial, type Texel } from "./material";
import { atlasArrays } from "./atlas";
import { gripPlacement, handField, type GripPlacementResult, type Hand, type HandOpts } from "./hands";
import { densifyArrays, sculptArrays, type Stroke } from "./sculpt";
import { decimateArrays, type DecimateOptions } from "./decimate";
import { boundaryLoops, reach, rimArrays, sweepArrays, type SweepOptions } from "./sweep";
import { smoothNormalsWelded } from "./atlas";

/** A three attribute as plain floats, read through getX so a normalised or interleaved buffer comes
 *  out in its real 0..1 values; undefined when there is none. */
function attrFloats(a: any): Float32Array | undefined {
  if (!a || !a.count || !a.itemSize) return undefined;
  const k = a.itemSize, out = new Float32Array(a.count * k);
  for (let i = 0; i < a.count; i++) {
    out[i * k] = a.getX(i);
    if (k > 1) out[i * k + 1] = a.getY(i);
    if (k > 2) out[i * k + 2] = a.getZ(i);
    if (k > 3) out[i * k + 3] = a.getW(i);
  }
  return out;
}

/** A hand's surface from its field: surface nets, then normals from the field's own gradient (they
 *  read better than averaged face normals in the creases between fingers). */
function handSurface(h: Hand, res: number): { pos: Float32Array; idx: Uint32Array; normal: Float32Array } {
  const r = isosurfaceArrays(h.field, h.min, h.max, h.grid(res), 0);
  const e = h.cell * 0.35, n = new Float32Array(r.pos.length);
  for (let i = 0; i < r.pos.length; i += 3) {
    const x = r.pos[i], y = r.pos[i + 1], z = r.pos[i + 2];
    const gx = h.field(x + e, y, z) - h.field(x - e, y, z);
    const gy = h.field(x, y + e, z) - h.field(x, y - e, z);
    const gz = h.field(x, y, z + e) - h.field(x, y, z - e);
    const l = Math.hypot(gx, gy, gz) || 1;
    n[i] = gx / l; n[i + 1] = gy / l; n[i + 2] = gz / l;
  }
  return { pos: r.pos, idx: r.idx, normal: n };
}

/**
 * Move a piece of a merged three mesh by moving its corners, from the arrays the code built. The
 * rest copy lives on the geometry, so a second edit starts from the build, not from the first edit.
 */
function movePieceThree(host: any, t0: number, t1: number, o: any): { moved: number; error: string } {
  let mesh: any = host?.isMesh ? host : null;
  if (!mesh) host?.traverse?.((c: any) => { if (!mesh && c.isMesh) mesh = c; });
  const g = mesh?.geometry, pa = g?.attributes?.position;
  if (!pa) return { moved: 0, error: "holds no mesh" };
  const na = g.attributes.normal || null;
  const ud = g.userData || (g.userData = {});
  if (!ud.__studioRest) {
    const restIdx = mesh.userData?.restIndex || g.index?.array || null;
    ud.__studioRest = {
      pos: Float32Array.from(pa.array), nor: na ? Float32Array.from(na.array) : null,
      idx: restIdx ? Uint32Array.from(restIdx) : null,
    };
  }
  const r = movePiece(ud.__studioRest, { pos: pa.array, nor: na ? na.array : null }, t0, t1, o);
  if (!r.error) {
    pa.needsUpdate = true;
    if (na) na.needsUpdate = true;
    g.computeBoundingSphere?.();
    g.computeBoundingBox?.();
  }
  return r;
}

/** The same on a PlayCanvas mesh. The rest copy lives on the pc.Mesh, where the snapshot finds it,
 *  so a mirror taken after an edit still shows the corners the code built. */
function movePiecePc(e: any, t0: number, t1: number, o: any): { moved: number; error: string } {
  const mi = ((e?.render && e.render.meshInstances) || (e?.model && e.model.meshInstances) || [])[0];
  const mesh = mi?.mesh;
  if (!mesh?.getPositions || !mesh.setPositions) return { moved: 0, error: "holds no mesh" };
  if (!mesh.__studioRest) {
    const P: number[] = [], N: number[] = [], I: number[] = [];
    mesh.getPositions(P);
    if (typeof mesh.getNormals === "function") mesh.getNormals(N);
    if (typeof mesh.getIndices === "function") mesh.getIndices(I);
    const pos = Float32Array.from(P), nor = N.length === P.length ? Float32Array.from(N) : null;
    mesh.__studioRest = { pos, nor, idx: I.length ? Uint32Array.from(I) : null };
    mesh.__studioLive = { pos: pos.slice(), nor: nor ? nor.slice() : null };
  }
  const live = mesh.__studioLive;
  const r = movePiece(mesh.__studioRest, live, t0, t1, o);
  if (r.error) return r;
  mesh.setPositions(live.pos);
  if (live.nor && typeof mesh.setNormals === "function") mesh.setNormals(live.nor);
  // 4 is PRIMITIVE_TRIANGLES in every PlayCanvas; true recomputes the box, which culling reads.
  mesh.update(4, true);
  return r;
}

/**
 * Put a sidecar back on top of a freshly built scene. What a game calls right after `build()`,
 * so the moves, hides, colours and modifiers made in the editor reach the player.
 *
 *   const root = build(THREE);
 *   applyEdits(root, await (await fetch('./scene.edits.json')).json(), makeOps(THREE));
 *
 * Overrides first, then the stack, in that order for a reason: a merge bakes world transforms,
 * so a modifier that ran before a move would weld the model in the wrong pose.
 */
// ---------------------------------------------------------------------------
// PLACING THINGS
//
// A viewer shows what the code made. An editor lets you put something there that the code did
// not. Those are different operations and only the first one existed.
//
// The problem a procedural game sets is that there is no scene file to write into: the world is
// a function, and next reload it runs again and your rock is gone. So a placement is not stored
// in the scene — it is stored BESIDE it, in the same sidecar that already holds "move this part,
// hide that one", and it is re-applied after the code has built the world. That is the only shape
// that survives a game whose level is a program.
//
// A reference says WHAT to place, never a live object, because the sidecar is JSON on disk:
//
//   {kind:'primitive', shape:'box',  color:'#88aa44'}      nothing needed from the project
//   {kind:'code',  file:'src/dinos.js', export:'buildDino'} the game's own builder, called
//   {kind:'model', url:'assets/rock.glb'}                   a file the game ships
//   {kind:'image', url:'assets/leaf.png'}                   a sprite, upright, alpha kept
//
// `resolve` is supplied by the caller and not by this file, because the editor reaches a module
// through the Studio's own proxy and a running game reaches it relative to itself. Same document,
// two hosts, one applier.
// ---------------------------------------------------------------------------

export interface PlacedRef {
  kind: "primitive" | "code" | "model" | "image" | "clone";
  shape?: string;
  color?: string;
  file?: string;
  export?: string;
  url?: string;
  args?: any[];
  /** clone: the stable key of an object the game's own code built. The document cannot hold a
   *  copy of a mesh, but it can hold "another one of THAT", and the applier makes it after the
   *  game has built the original. */
  of?: string;
  /** spec: which game inside the workspace owns the file, which table it came from and which
   *  entry. The entry is found by its own id at build time, because the table it was declared in
   *  is very often a module-private const the game never exports. */
  root?: string;
  table?: string;
  key?: string;
  index?: number;
  deps?: string[];
  /** True when this is one entry of a table rather than a whole exported builder. */
  spec?: boolean;
  /** model: the nodes of the file a table entry names — the first is the one the game draws, the
   *  rest its repaints — and the extra parts it always shows. */
  nodes?: string[];
  parts?: string[];
}

export interface PlacedItem {
  id: string;
  name: string;
  ref: PlacedRef;
  pos: [number, number, number];
  rot: [number, number, number];
  scale: [number, number, number];
}

export const PRIMITIVES = ["box", "sphere", "cylinder", "cone", "plane", "torus",
                           "pointLight", "spotLight", "dirLight"] as const;

/** The shapes an editor can place with nothing but the engine — a cube to block out a level with,
 *  and the three lights, because a scene you cannot light is a scene you cannot judge. */
export function makePrimitive(T: any, shape: string, color = "#9aa7b8"): any {
  const mat = () => new T.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0.05 });
  switch (shape) {
    case "sphere": return new T.Mesh(new T.SphereGeometry(0.5, 32, 20), mat());
    case "cylinder": return new T.Mesh(new T.CylinderGeometry(0.5, 0.5, 1, 28), mat());
    case "cone": return new T.Mesh(new T.ConeGeometry(0.5, 1, 28), mat());
    case "plane": {
      const m = new T.Mesh(new T.PlaneGeometry(1, 1),
                           new T.MeshStandardMaterial({ color, side: T.DoubleSide, roughness: 0.9 }));
      m.rotation.x = -Math.PI / 2;
      return m;
    }
    case "torus": return new T.Mesh(new T.TorusGeometry(0.4, 0.16, 16, 40), mat());
    case "pointLight": { const l = new T.PointLight(color, 2, 0, 2); l.castShadow = true; return l; }
    case "spotLight": { const l = new T.SpotLight(color, 4, 0, Math.PI / 6, 0.4, 2); l.castShadow = true; return l; }
    case "dirLight": { const l = new T.DirectionalLight(color, 1.4); l.castShadow = true; return l; }
    default: return new T.Mesh(new T.BoxGeometry(1, 1, 1), mat());
  }
}

/** A picture standing up in the world: one quad, alpha kept, lit flatly so a sprite reads as art
 *  rather than as a surface. Two-sided, because a flat thing seen from behind should not vanish. */
export function makeSprite(T: any, url: string): any {
  const tex = new T.TextureLoader().load(url);
  tex.colorSpace = (T as any).SRGBColorSpace || tex.colorSpace;
  const m = new T.Mesh(new T.PlaneGeometry(1, 1),
                       new T.MeshBasicMaterial({ map: tex, transparent: true, alphaTest: 0.02,
                                                 side: T.DoubleSide, toneMapped: false }));
  m.userData.studioSprite = url;
  return m;
}

/** Put the placements into the scene. Returns what it managed to place and what it could not.
 *
 *  Every placed object is marked, and the mark is what makes the difference between "delete" and
 *  "hide" later: a thing the editor added can really be removed, and a thing the game's own code
 *  built can only be hidden, because the code will build it again on the next run. */
export async function applyPlaced(
  root: any, placed: PlacedItem[] | undefined, T: any,
  resolve: (ref: PlacedRef) => Promise<any>,
): Promise<{ added: number; errors: string[] }> {
  const out = { added: 0, errors: [] as string[] };
  if (!root || !Array.isArray(placed) || !placed.length) return out;
  for (const it of placed) {
    if (!it || !it.ref) continue;
    let obj: any = null;
    try {
      obj = it.ref.kind === "primitive" ? makePrimitive(T, it.ref.shape || "box", it.ref.color)
          : it.ref.kind === "image" && it.ref.url ? makeSprite(T, it.ref.url)
          : it.ref.kind === "clone" ? cloneOf(root, it.ref.of || "")
          : await resolve(it.ref);
    } catch (e: any) {
      out.errors.push(`${it.name || it.id}: ${e?.message || e}`);
      continue;
    }
    if (!obj) { out.errors.push(`${it.name || it.id}: nothing came back`); continue; }
    obj.name = it.name || obj.name || it.id;
    if (it.pos) obj.position.set(it.pos[0], it.pos[1], it.pos[2]);
    if (it.rot) obj.rotation.set(it.rot[0], it.rot[1], it.rot[2]);
    if (it.scale) obj.scale.set(it.scale[0], it.scale[1], it.scale[2]);
    obj.userData.studioPlaced = it.id;
    root.add(obj);
    out.added++;
  }
  return out;
}

/** Another one of the object the game calls `key`. Geometry and materials are shared by clone(),
 *  so a hundred copies of one rock cost one rock. */
function cloneOf(root: any, key: string): any {
  if (!key) return null;
  for (const [o, k] of stableKeys(root)) {
    if (k === key) return o.clone(true);
  }
  return null;
}

/** Was this object put here by the editor, or by the game? The answer decides whether "delete"
 *  can mean delete. */
export function placedIdOf(o: any): string {
  for (let n = o; n; n = n.parent) {
    const id = n.userData?.studioPlaced;
    if (id) return String(id);
  }
  return "";
}

export function applyEdits(root: any, edits: any, ops?: Ops, T?: any): AppliedEdits {
  const out: AppliedEdits = { parts: 0, mods: 0, verts: 0, missing: [], errors: [] };
  if (!root?.traverse) return out;
  applyWorld(root, edits?.world, out, T);
  const byKey = new Map<string, any>();
  for (const [o, k] of stableKeys(root)) byKey.set(k, o);
  for (const [key, o] of Object.entries<any>(edits?.parts || {})) {
    const t = byKey.get(key);
    if (!t) {
      // A PIECE of a merged mesh has no object of its own in a game, so its corners are moved.
      const pk = parsePieceKey(key);
      const host = pk ? byKey.get(pk.mesh) : null;
      if (pk && host) {
        const r = movePieceThree(host, pk.t0, pk.t1, o);
        if (r.error) out.errors.push(key + ": " + r.error); else out.parts++;
        continue;
      }
      out.missing.push(key);
      continue;
    }
    if (o.pos) t.position.set(o.pos[0], o.pos[1], o.pos[2]);
    if (o.rot) t.rotation.set(o.rot[0], o.rot[1], o.rot[2]);
    if (o.scale) t.scale.set(o.scale[0], o.scale[1], o.scale[2]);
    if (o.hidden !== undefined) t.visible = !o.hidden;
    if (typeof o.shadow === "boolean") t.castShadow = o.shadow;
    if (o.color && t.isLight && t.color?.set) t.color.set(o.color);
    for (const k of OVERRIDE_NUMBERS) if (typeof o[k] === "number" && k in t) t[k] = o[k];
    if (t.isCamera) t.updateProjectionMatrix?.();
    if (!t.isLight && (o.color || o.roughness !== undefined || o.metalness !== undefined)) {
      t.traverse((c: any) => {
        for (const m of Array.isArray(c.material) ? c.material : c.material ? [c.material] : []) {
          if (o.color && m.color?.set) m.color.set(o.color);
          if (o.roughness !== undefined && "roughness" in m) m.roughness = o.roughness;
          if (o.metalness !== undefined && "metalness" in m) m.metalness = o.metalness;
        }
      });
    }
    out.parts++;
  }
  root.updateMatrixWorld?.(true);
  // Hand-moved vertices are part of the base mesh, so they land before the stack runs over it.
  out.verts = applyVerts(root, edits?.verts, out);
  const mods: any[] = Array.isArray(edits?.mods) ? edits.mods.filter((m: any) => m && !m.off) : [];
  if (mods.length && !ops) { out.errors.push("the stack needs the operations: pass makeOps(THREE)"); return out; }
  for (const m of mods) {
    try {
      // A modifier on the whole asset runs on every part in place — Blender's per-object stack —
      // so the parts keep their names, materials and bakes. Only the operations whose whole point
      // is the UNION of the parts merge them first: skin and remesh.
      const perMesh = !!m.target || !(m.op === "skin" || m.op === "remesh");
      if (perMesh) {
        const o = m.target ? byKey.get(m.target) : root;
        if (!o) { out.missing.push(m.target); continue; }
        let touched = 0;
        o.traverse((c: any) => {
          if (!c.isMesh || !c.geometry) return;
          const next = applyMod(ops!, m, c.geometry);
          if (!next) return;
          c.geometry.dispose?.();
          c.geometry = next;
          touched++;
        });
        if (!touched) { out.errors.push(m.op + ": " + m.target + " holds no mesh"); continue; }
      } else {
        const merged = ops!.merge(root);
        const next = applyMod(ops!, m, merged, root);
        if (!next) { out.errors.push(m.op + ": produced nothing"); continue; }
        // There is no three here to make a Mesh with, and there does not need to be: the first
        // mesh in the tree is one, and a childless clone of it is a Mesh with its class intact.
        let proto: any = null;
        root.traverse((c: any) => { if (!proto && c.isMesh) proto = c; });
        if (!proto) { out.errors.push(m.op + ": nothing in the tree is a mesh"); continue; }
        const mesh = proto.clone(false);
        mesh.geometry = next;
        mesh.name = "merged";
        mesh.position.set(0, 0, 0);
        mesh.rotation.set(0, 0, 0);
        mesh.scale.set(1, 1, 1);
        // Lights and cameras are not geometry and survive the merge; everything else is now
        // inside the one mesh. The first material found is kept, because a merge has no way to
        // keep several and painting the whole model grey would be a worse surprise than one colour.
        const keep = root.children.filter((c: any) => c.isLight || c.isCamera);
        for (const c of root.children.slice()) root.remove(c);
        root.add(mesh, ...keep);
      }
      out.mods++;
    } catch (e: any) {
      out.errors.push(m.op + ": " + String(e?.message || e).slice(0, 160));
    }
  }
  return out;
}

/**
 * The world half of a sidecar: background and fog, onto the Scene in the tree.
 *
 * Works without three when the scene already has a Color or a Fog to change in place; MAKING a
 * fog where there was none needs the constructors, so pass THREE for that and the error says so.
 */
export function applyWorld(root: any, w: any, out?: AppliedEdits, T?: any) {
  if (!w || typeof w !== "object") return;
  let sc: any = root.isScene ? root : null;
  if (!sc) root.traverse((o: any) => { if (!sc && o.isScene) sc = o; });
  if (!sc) { out?.errors.push("world: nothing in the tree is a Scene"); return; }
  if (typeof w.background === "string") {
    if (sc.background?.isColor) sc.background.set(w.background);
    else if (T?.Color) sc.background = new T.Color(w.background);
    else out?.errors.push("world: no Color to set the background on; pass THREE");
  }
  if (w.fog === null) sc.fog = null;
  else if (w.fog && typeof w.fog === "object") {
    const f = w.fog;
    const wantExp = f.type === "exp2";
    const have = sc.fog;
    const sameKind = have && (wantExp ? !!have.isFogExp2 : !!have.isFog);
    if (sameKind) {
      have.color.set(f.color);
      if (wantExp) { if (typeof f.density === "number") have.density = f.density; }
      else { if (typeof f.near === "number") have.near = f.near; if (typeof f.far === "number") have.far = f.far; }
    } else if (T?.Fog && T?.FogExp2) {
      sc.fog = wantExp ? new T.FogExp2(f.color, f.density ?? 0.05) : new T.Fog(f.color, f.near ?? 1, f.far ?? 100);
    } else {
      out?.errors.push("world: no fog of that kind to change in place; pass THREE to make one");
    }
  }
}

// ------------------------------------------------------------------ playcanvas
//
// The other engine this Studio builds in, given the same editor. Nothing here imports PlayCanvas:
// entities are read by duck type, so this runs inside a game page, inside the Studio's own hidden
// app, or in a test with a fake tree. Three pieces, mirror images of the three.js ones:
//
//   pcSnapshot(entity)   the entity tree as plain JSON — transforms, meshes as flat arrays,
//                        materials, lights, cameras, the scene's world. What the editor mirrors.
//   pcApply(entity, e)   the SAME sidecar the editor writes, applied to PlayCanvas entities by the
//                        same stable keys. What the game calls after it builds, or the live link
//                        calls while it runs.
//   makeOpsPc(pc, dev)   the modifier stack on pc.Mesh: the array algorithms are shared, only the
//                        read and write of a mesh differ.
//
// Two conventions to know. A PlayCanvas light shines down its entity's NEGATIVE Y; three's shines
// at a target, so the mirror hangs a target under the entity at (0,-1,0). And a sidecar rotation is
// a three-style XYZ euler in radians, so it is turned into a quaternion here with three's own
// formula rather than handed to setLocalEulerAngles, whose order and units differ.

/** A number array on the wire. Plain in memory; packed as base64 of a typed array when the
 *  snapshot has to travel as text (`pack`), which is two to three times smaller than decimal JSON
 *  and parses without building a million-element array first. `i8n`/`u8n` are normalised bytes:
 *  a normal or a colour needs no more. */
export type PcArr = number[] | { b64: string; t: "f32" | "i8n" | "u8n" | "u16" | "u32" };

/** One picture, once, however many materials and mesh instances draw with it. */
export interface PcSnapTexture {
  /** Level 0 as a PNG data URL; "" when its pixels could not be read back (another origin, a
   *  compressed format). */
  url: string;
  w: number; h: number;
  name?: string;
  /** Magnified by picking one texel — pixel art and palette sheets, where a blend of two
   *  neighbours is a colour the artist never chose. */
  nearest?: boolean;
  mips?: boolean;
  wrapU?: "repeat" | "clamp" | "mirror";
  wrapV?: "repeat" | "clamp" | "mirror";
  srgb?: boolean;
}

/** A material slot's texture, and how the game samples it. */
export interface PcSnapMap {
  /** Index into `PcSnapshot.textures`. */
  tex: number;
  /** The channel an opacity map reads. */
  ch?: string;
  /** The UV set, when it is not the first. */
  uv?: number;
  /** PlayCanvas's own tiling and offset, as the game set them; the mirror converts. */
  tiling?: [number, number];
  offset?: [number, number];
}

export interface PcSnapMaterial {
  name?: string; color: V3; metalness: number; roughness: number; emissive?: V3;
  opacity?: number; transparent?: boolean; doubleSided?: boolean; unlit?: boolean;
  /** The diffuse map as a data URL — the first snapshot format, still read by the mirror. */
  map?: string;
  diffuseMap?: PcSnapMap; emissiveMap?: PcSnapMap; opacityMap?: PcSnapMap; normalMap?: PcSnapMap;
  /** Kept apart from `emissive`, the way the game keeps it. */
  emissiveIntensity?: number;
  /** A colour the game set per mesh instance is already LINEAR; a material's is sRGB. */
  colorLinear?: boolean;
  emissiveLinear?: boolean;
  /** Whether the vertex colour tints the albedo, the glow, or both. PlayCanvas asks per slot;
   *  three has one switch, for the albedo only. */
  vcDiffuse?: boolean;
  vcEmissive?: boolean;
  blend?: "normal" | "additive" | "premultiplied" | "multiply";
  depthWrite?: boolean;
  alphaTest?: number;
  fog?: boolean;
  backSide?: boolean;
}
/** Geometry, once per mesh however many instances draw it. */
export interface PcSnapGeom {
  positions: PcArr; normals?: PcArr; uvs?: PcArr; uvs1?: PcArr; colors?: PcArr; indices?: PcArr;
  /** Components per colour: 3 or 4. */
  colorSize?: number;
}
export interface PcSnapMesh {
  /** Inline geometry: the first snapshot format, still read by the mirror. */
  positions?: PcArr; normals?: PcArr; uvs?: PcArr; colors?: PcArr; indices?: PcArr;
  /** Index into `PcSnapshot.geoms`. */
  geo?: number;
  /** The mesh node relative to its entity, column-major, when they are not the same node. */
  local?: number[];
  material: PcSnapMaterial;
  /** Bent by a skeleton. Baked in the pose it had at the moment of the snapshot, in the entity's
   *  own frame, because a skinned mesh's vertices mean nothing without its bones. */
  skinned?: boolean;
}
export interface PcSnapLight {
  type: "directional" | "point" | "spot"; color: V3; intensity: number; range: number;
  inner: number; outer: number; shadows: boolean; enabled: boolean;
}
export interface PcSnapCamera {
  fov: number; near: number; far: number; ortho: boolean; orthoHeight: number; clearColor?: V3;
}
export interface PcSnapEntity {
  name: string; enabled: boolean; pos: V3; rot: [number, number, number, number]; scale: V3;
  meshes?: PcSnapMesh[]; light?: PcSnapLight; camera?: PcSnapCamera; children: PcSnapEntity[];
  /** The model file the game built this from — its container's URL — when it came out of one.
   *  What lets a placed file take the game's own paint (see gamedress.ts). */
  src?: string;
}
export interface PcSnapshot {
  engine: "playcanvas";
  /** 2 carries the texture and geometry tables below. Absent on the first format. */
  version?: number;
  root: PcSnapEntity;
  world: { ambient?: V3; background?: V3; fog?: { type: "linear" | "exp2"; color: V3; start?: number; end?: number; density?: number } | null } | null;
  counts: { entities: number; meshes: number; triangles: number; textures?: number; skinned?: number; hidden?: number };
  textures?: PcSnapTexture[];
  geoms?: PcSnapGeom[];
}

// ---- packing numbers for the wire ------------------------------------------------------------

function bytesToB64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + 0x8000)));
  }
  return btoa(s);
}

function b64ToBytes(b64: string): Uint8Array {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

/** An array as base64 of the typed array named by `t`. Normalised kinds clamp first. */
export function packArr(a: ArrayLike<number>, t: "f32" | "i8n" | "u8n" | "u16" | "u32"): PcArr {
  const n = a.length;
  let typed: Float32Array | Int8Array | Uint8Array | Uint16Array | Uint32Array;
  if (t === "f32") typed = Float32Array.from(a);
  else if (t === "i8n") { typed = new Int8Array(n); for (let i = 0; i < n; i++) typed[i] = Math.round(Math.max(-1, Math.min(1, a[i])) * 127); }
  else if (t === "u8n") { typed = new Uint8Array(n); for (let i = 0; i < n; i++) typed[i] = Math.round(Math.max(0, Math.min(1, a[i])) * 255); }
  else if (t === "u16") typed = Uint16Array.from(a);
  else typed = Uint32Array.from(a);
  return { b64: bytesToB64(new Uint8Array(typed.buffer, typed.byteOffset, typed.byteLength)), t };
}

/** The other half: a packed or plain array as the typed array a geometry takes. Floats for every
 *  vertex stream, 16 or 32 bits for an index. Null for nothing. */
export function unpackArr(a: PcArr | undefined | null, index = false): Float32Array | Uint16Array | Uint32Array | null {
  if (!a) return null;
  if (Array.isArray(a)) {
    if (!a.length) return null;
    if (!index) return Float32Array.from(a);
    let max = 0;
    for (const v of a) if (v > max) max = v;
    return max < 65536 ? Uint16Array.from(a) : Uint32Array.from(a);
  }
  const bytes = b64ToBytes(a.b64);
  const buf = bytes.buffer;
  if (a.t === "f32") return new Float32Array(buf, 0, bytes.byteLength >> 2);
  if (a.t === "u16") return new Uint16Array(buf, 0, bytes.byteLength >> 1);
  if (a.t === "u32") return new Uint32Array(buf, 0, bytes.byteLength >> 2);
  const out = new Float32Array(bytes.byteLength);
  if (a.t === "i8n") { const s = new Int8Array(buf); for (let i = 0; i < out.length; i++) out[i] = Math.max(-1, s[i] / 127); }
  else for (let i = 0; i < out.length; i++) out[i] = bytes[i] / 255;
  return out;
}

const col3 = (c: any, d: V3 = [0.8, 0.8, 0.8]): V3 => (c && typeof c.r === "number" ? [c.r, c.g, c.b] : d);

/** PlayCanvas's blend constants, unchanged since 1.0. BLEND_NONE (3) is absent: it does not blend. */
const PC_BLEND: Record<number, PcSnapMaterial["blend"]> = {
  0: "normal", 1: "additive", 2: "normal", 4: "premultiplied", 5: "multiply", 6: "additive", 7: "multiply", 8: "normal",
};
/** What a normalised integer stream divides by, by PlayCanvas TYPE_* (INT8, UINT8, INT16, UINT16). */
const NORM_DIV: Record<number, number> = { 0: 127, 1: 255, 2: 32767, 3: 65535 };
/** Bytes per texel of the uncompressed 8-bit formats a data texture uses: L8, LA8, RGB8, RGBA8,
 *  SRGB8, SRGBA8, R8. */
const PC_BPP: Record<number, number> = { 1: 1, 2: 2, 6: 3, 7: 4, 19: 3, 20: 4, 52: 1 };
const PC_WRAP: Record<number, PcSnapTexture["wrapU"]> = { 0: "repeat", 1: "clamp", 2: "mirror" };

/** A vertex stream through the mesh's own getter, scaled back to what it means when the vertex
 *  buffer stores it as normalised integers — a quantised glTF, or colour bytes. */
function readStream(mesh: any, semantic: string, read: (out: number[]) => number): number[] | null {
  const out: number[] = [];
  let n = 0;
  try { n = read(out); } catch { return null; }
  if (!n || !out.length) return null;
  let el: any = null;
  try { el = (mesh?.vertexBuffer?.format?.elements || []).find((e: any) => e?.name === semantic) || null; } catch { /* no buffer */ }
  const div = el && el.normalize ? NORM_DIV[el.dataType] : 0;
  if (div) for (let i = 0; i < out.length; i++) out[i] /= div;
  return out;
}

/** Level 0 of a PlayCanvas texture as a PNG, and how it is sampled. Null when the pixels cannot
 *  be read back: a compressed format, a cube map, or an image from another origin. */
function readTexture(t: any, maxSide: number): PcSnapTexture | null {
  if (!t || t.cubemap || t._cubemap || t.volume || typeof document === "undefined") return null;
  const src = typeof t.getSource === "function" ? t.getSource() : t._levels?.[0];
  if (!src) return null;
  // Magnified by one texel: palette sheets and pixel art. Kept at full size up to 2048, because a
  // palette halved is a palette whose every other colour is gone.
  const nearest = t.magFilter === 0;
  const cap = nearest ? Math.max(maxSide, 2048) : maxSide;
  let canvas: HTMLCanvasElement | null = null;
  let w = 0, h = 0;
  if (ArrayBuffer.isView(src)) {
    w = t.width | 0; h = t.height | 0;
    const bpp = PC_BPP[t.format as number] || 0;
    const s = src as unknown as ArrayLike<number>;
    if (!w || !h || !bpp || s.length < w * h * bpp) return null;
    const px = new Uint8ClampedArray(w * h * 4);
    for (let i = 0, j = 0; i < w * h; i++, j += bpp) {
      const r = s[j], g = bpp >= 3 ? s[j + 1] : r, b = bpp >= 3 ? s[j + 2] : r;
      px[i * 4] = r; px[i * 4 + 1] = g; px[i * 4 + 2] = b;
      px[i * 4 + 3] = bpp === 4 ? s[j + 3] : bpp === 2 ? s[j + 1] : 255;
    }
    canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    canvas.getContext("2d")!.putImageData(new ImageData(px, w, h), 0, 0);
  } else {
    w = src.naturalWidth || src.videoWidth || src.width || 0;
    h = src.naturalHeight || src.videoHeight || src.height || 0;
    if (!w || !h) return null;
  }
  const k = Math.min(1, cap / Math.max(w, h));
  const W = Math.max(1, Math.round(w * k)), H = Math.max(1, Math.round(h * k));
  let url = "";
  const own = canvas || (typeof HTMLCanvasElement !== "undefined" && src instanceof HTMLCanvasElement ? src : null);
  if (own && W === w && H === h) url = own.toDataURL("image/png");
  else {
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const g = c.getContext("2d")!;
    if (nearest) g.imageSmoothingEnabled = false;
    g.drawImage(canvas || src, 0, 0, W, H);
    url = c.toDataURL("image/png");
  }
  if (!url || url.length < 32) return null;
  const out: PcSnapTexture = { url, w: W, h: H };
  if (t.name) out.name = String(t.name).slice(0, 80);
  if (nearest) out.nearest = true;
  if (t.mipmaps !== false) out.mips = true;
  const wu = PC_WRAP[t.addressU as number], wv = PC_WRAP[t.addressV as number];
  if (wu && wu !== "repeat") out.wrapU = wu;
  if (wv && wv !== "repeat") out.wrapV = wv;
  if (t.srgb === true || t.encoding === "srgb" || t.format === 19 || t.format === 20) out.srgb = true;
  return out;
}

/**
 * A PlayCanvas entity tree as plain JSON: what the editor mirrors.
 *
 * FIVE THINGS DECIDE WHETHER THE MIRROR LOOKS LIKE THE GAME, and the first version had none:
 *  - A game puts a texture on ONE MESH INSTANCE, not on the material (`setParameter(
 *    'texture_emissiveMap', tex)`): one shared sign material, a hundred signs. Reading only the
 *    material drew every sign as the material's black.
 *  - The glow is the EMISSIVE slot, with its own map, its own intensity and its own vertex-colour
 *    switch. A sign is black albedo plus a painted emissive; the albedo alone is a black board.
 *  - Opacity is a map, a channel and a blend mode, per material.
 *  - A picture is stored ONCE, in `textures`, however many meshes draw it. Per mesh, the brainrot
 *    palette went across forty times.
 *  - A skinned mesh is posed by its bones. Its vertices without them are a bind pose in some
 *    other frame, so they are baked here, in the pose of this moment, in the entity's frame.
 *
 * `pack` turns every number array into base64 of a typed array, for the trip through a file.
 */
export function pcSnapshot(root: any, opts: { textures?: boolean; maxTexture?: number; app?: any; pack?: boolean; skin?: boolean } = {}): PcSnapshot {
  const counts: PcSnapshot["counts"] = { entities: 0, meshes: 0, triangles: 0 };
  let clearColor: V3 | undefined;
  const pack = !!opts.pack;
  const wantTex = opts.textures !== false && typeof document !== "undefined";
  const textures: PcSnapTexture[] = [];
  const texIndex = new Map<any, number>();
  const geoms: PcSnapGeom[] = [];
  const geoIndex = new Map<any, { geo: number; tris: number }>();
  let skinned = 0, hidden = 0;
  const app = opts.app || root?._app || (root?.getApplication && root.getApplication());

  // WHICH FILE A MODEL CAME FROM. A container names every render it makes "<file>/render/<n>" and
  // the entity keeps that render's id, so the file is two look-ups away. Remembered per id.
  const srcById = new Map<number, string>();
  const srcOf = (e: any): string => {
    const id = e?.render?.asset;
    if (typeof id !== "number" || !app?.assets?.get) return "";
    const hit = srcById.get(id);
    if (hit !== undefined) return hit;
    let s = "";
    try {
      const name = String(app.assets.get(id)?.name || "");
      const cut = name.lastIndexOf("/render/");
      if (cut > 0) {
        const file = name.slice(0, cut);
        const box = typeof app.assets.find === "function" ? app.assets.find(file, "container") : null;
        s = String(box?.file?.url || file).split(/[?#]/)[0];
      }
    } catch { s = ""; }
    srcById.set(id, s);
    return s;
  };

  const takeTexture = (t: any): number => {
    if (!t || typeof t !== "object" || !wantTex) return -1;
    const hit = texIndex.get(t);
    if (hit !== undefined) return hit;
    let tex: PcSnapTexture | null = null;
    try { tex = readTexture(t, opts.maxTexture || 1024); } catch { tex = null; }
    const i = tex ? textures.push(tex) - 1 : -1;
    texIndex.set(t, i);
    return i;
  };
  // A mesh instance's own value for a uniform, which beats the material's.
  const param = (mi: any, name: string): any => {
    try {
      const p = typeof mi?.getParameter === "function" ? mi.getParameter(name) : mi?.parameters?.[name];
      return p ? p.data : undefined;
    } catch { return undefined; }
  };
  const mapOf = (m: any, mi: any, slot: "diffuse" | "emissive" | "opacity" | "normal"): PcSnapMap | undefined => {
    const t = param(mi, "texture_" + slot + "Map") || m?.[slot + "Map"] || (slot === "diffuse" ? m?.colorMap : null);
    const tex = takeTexture(t);
    if (tex < 0) return undefined;
    const out: PcSnapMap = { tex };
    const ch = m?.[slot + "MapChannel"];
    if (slot === "opacity") out.ch = typeof ch === "string" && ch ? ch : "a";
    const uv = m?.[slot + "MapUv"];
    if (typeof uv === "number" && uv > 0) out.uv = uv;
    const til = m?.[slot + "MapTiling"], off = m?.[slot + "MapOffset"];
    if (til && typeof til.x === "number" && (til.x !== 1 || til.y !== 1)) out.tiling = [til.x, til.y];
    if (off && typeof off.x === "number" && (off.x !== 0 || off.y !== 0)) out.offset = [off.x, off.y];
    return out;
  };
  const takeMaterial = (m: any, mi: any): PcSnapMaterial => {
    if (!m) return { color: [0.8, 0.8, 0.8], metalness: 0, roughness: 0.6 };
    const out: PcSnapMaterial = {
      name: m.name || undefined,
      color: col3(m.diffuse || m.color),
      metalness: m.useMetalness === false ? 0 : typeof m.metalness === "number" ? m.metalness : 0,
      roughness: typeof m.gloss === "number" ? 1 - m.gloss : typeof m.shininess === "number" ? 1 - m.shininess / 100 : 0.6,
    };
    const pd = param(mi, "material_diffuse");
    if (pd && pd.length >= 3) { out.color = [pd[0], pd[1], pd[2]]; out.colorLinear = true; }
    const pe = param(mi, "material_emissive");
    if (pe && pe.length >= 3) {
      if (pe[0] || pe[1] || pe[2]) { out.emissive = [pe[0], pe[1], pe[2]]; out.emissiveLinear = true; }
    } else if (m.emissive && (m.emissive.r || m.emissive.g || m.emissive.b)) {
      out.emissive = [m.emissive.r, m.emissive.g, m.emissive.b];
    }
    const pk = param(mi, "material_emissiveIntensity");
    const k = typeof pk === "number" ? pk : typeof m.emissiveIntensity === "number" ? m.emissiveIntensity : 1;
    if (out.emissive && k !== 1) out.emissiveIntensity = k;
    const po = param(mi, "material_opacity");
    const opacity = typeof po === "number" ? po : typeof m.opacity === "number" ? m.opacity : 1;
    if (opacity < 1) out.opacity = opacity;
    // BLEND_NONE is 3 in every PlayCanvas since 1.0; anything else blends.
    if (typeof m.blendType === "number" && m.blendType !== 3) { out.transparent = true; out.blend = PC_BLEND[m.blendType] || "normal"; }
    if (typeof m.alphaTest === "number" && m.alphaTest > 0) out.alphaTest = m.alphaTest;
    if (m.depthWrite === false) out.depthWrite = false;
    if (m.cull === 0) out.doubleSided = true;
    else if (m.cull === 2) out.backSide = true;
    if (m.useFog === false) out.fog = false;
    if (m.useLighting === false || (!m.diffuse && m.color)) out.unlit = true;
    out.vcDiffuse = !!(m.diffuseVertexColor || (!m.diffuse && m.vertexColors));
    out.vcEmissive = !!m.emissiveVertexColor;
    const dm = mapOf(m, mi, "diffuse"); if (dm) out.diffuseMap = dm;
    const em = mapOf(m, mi, "emissive"); if (em) out.emissiveMap = em;
    const om = mapOf(m, mi, "opacity"); if (om) out.opacityMap = om;
    const nm = mapOf(m, mi, "normal"); if (nm) out.normalMap = nm;
    return out;
  };
  /** The pose of this moment, baked: every vertex through its bones' world matrices and their
   *  inverse bind poses, then into the entity's own frame, which is where the mirror hangs it. */
  const bakeSkin = (mi: any, e: any): { pos: number[]; nor: number[] | null } | null => {
    const si = mi?.skinInstance, mesh = mi?.mesh;
    const bones: any[] = si?.bones || [];
    const ibp: any[] = si?.skin?.inverseBindPose || [];
    if (!bones.length || !ibp.length || typeof mesh?.getVertexStream !== "function" || !e?.getWorldTransform) return null;
    const pos = readStream(mesh, "POSITION", (o) => mesh.getPositions(o));
    if (!pos) return null;
    const nv = pos.length / 3;
    const bi: number[] = [], bw: number[] = [];
    try { mesh.getVertexStream("BLENDINDICES", bi); mesh.getVertexStream("BLENDWEIGHT", bw); } catch { return null; }
    if (bi.length < nv || bw.length < nv) return null;
    const ci = Math.round(bi.length / nv), cw = Math.round(bw.length / nv);
    const nor = typeof mesh.getNormals === "function" ? readStream(mesh, "NORMAL", (o) => mesh.getNormals(o)) : null;
    const inv = mat4Invert(Array.from(e.getWorldTransform().data as ArrayLike<number>));
    if (!inv) return null;
    const M = bones.map((b: any, j: number) => {
      const ib = ibp[j];
      if (!b?.getWorldTransform || !ib?.data) return null;
      return mat4Mul(inv, mat4Mul(Array.from(b.getWorldTransform().data as ArrayLike<number>), Array.from(ib.data as ArrayLike<number>)));
    });
    const P = new Array<number>(pos.length);
    const N = nor && nor.length === pos.length ? new Array<number>(nor.length) : null;
    const lanes = Math.min(ci, cw);
    for (let v = 0; v < nv; v++) {
      const px = pos[v * 3], py = pos[v * 3 + 1], pz = pos[v * 3 + 2];
      let x = 0, y = 0, z = 0, nx = 0, ny = 0, nz = 0, ws = 0;
      for (let l = 0; l < lanes; l++) {
        const w = bw[v * cw + l];
        if (!w) continue;
        const m = M[bi[v * ci + l] | 0];
        if (!m) continue;
        ws += w;
        x += w * (m[0] * px + m[4] * py + m[8] * pz + m[12]);
        y += w * (m[1] * px + m[5] * py + m[9] * pz + m[13]);
        z += w * (m[2] * px + m[6] * py + m[10] * pz + m[14]);
        if (N) {
          const qx = nor![v * 3], qy = nor![v * 3 + 1], qz = nor![v * 3 + 2];
          nx += w * (m[0] * qx + m[4] * qy + m[8] * qz);
          ny += w * (m[1] * qx + m[5] * qy + m[9] * qz);
          nz += w * (m[2] * qx + m[6] * qy + m[10] * qz);
        }
      }
      if (ws > 0) { P[v * 3] = x / ws; P[v * 3 + 1] = y / ws; P[v * 3 + 2] = z / ws; }
      else { P[v * 3] = px; P[v * 3 + 1] = py; P[v * 3 + 2] = pz; }
      if (N) {
        if (ws > 0) {
          const l = Math.hypot(nx, ny, nz) || 1;
          N[v * 3] = nx / l; N[v * 3 + 1] = ny / l; N[v * 3 + 2] = nz / l;
        } else { N[v * 3] = nor![v * 3]; N[v * 3 + 1] = nor![v * 3 + 1]; N[v * 3 + 2] = nor![v * 3 + 2]; }
      }
    }
    return { pos: P, nor: N };
  };
  /** One geometry record per pc.Mesh, reused by every instance that draws it. A baked skin is
   *  never shared: two characters on one mesh stand in two poses. */
  const takeGeom = (mesh: any, baked: { pos: number[]; nor: number[] | null } | null): { geo: number; tris: number } | null => {
    if (!baked) { const hit = geoIndex.get(mesh); if (hit) return hit; }
    // A mesh whose pieces the Studio has moved keeps what the code built beside it; the mirror
    // wants THAT, and puts the moves back on itself from the sidecar.
    const rest = !baked && mesh.__studioRest ? mesh.__studioRest : null;
    const positions = baked ? baked.pos : rest ? Array.from(rest.pos as Float32Array) : readStream(mesh, "POSITION", (o) => mesh.getPositions(o));
    if (!positions || !positions.length) return null;
    const g: PcSnapGeom = { positions: pack ? packArr(positions, "f32") : positions };
    const normals = baked ? baked.nor
      : rest?.nor ? Array.from(rest.nor as Float32Array)
      : typeof mesh.getNormals === "function" ? readStream(mesh, "NORMAL", (o) => mesh.getNormals(o)) : null;
    if (normals && normals.length === positions.length) g.normals = pack ? packArr(normals, "i8n") : normals;
    if (typeof mesh.getUvs === "function") {
      const uvs = readStream(mesh, "TEXCOORD0", (o) => mesh.getUvs(0, o));
      if (uvs && uvs.length) g.uvs = pack ? packArr(uvs, "f32") : uvs;
      const uvs1 = readStream(mesh, "TEXCOORD1", (o) => mesh.getUvs(1, o));
      if (uvs1 && uvs1.length) g.uvs1 = pack ? packArr(uvs1, "f32") : uvs1;
    }
    const colors = typeof mesh.getColors === "function" ? readStream(mesh, "COLOR", (o) => mesh.getColors(o)) : null;
    if (colors && colors.length) {
      let big = 0;
      for (const v of colors) if (v > big) big = v;
      const c = big > 1 ? colors.map((v) => v / 255) : colors;
      const per = Math.round(c.length / (positions.length / 3));
      if (per === 3 || per === 4) { g.colors = pack ? packArr(c, "u8n") : c; g.colorSize = per; }
    }
    const indices: number[] = [];
    if (typeof mesh.getIndices === "function" && mesh.getIndices(indices) && indices.length) {
      let max = 0;
      for (const v of indices) if (v > max) max = v;
      g.indices = pack ? packArr(indices, max < 65536 ? "u16" : "u32") : indices;
    }
    const rec = { geo: geoms.push(g) - 1, tris: Math.round((indices.length ? indices.length : positions.length / 3) / 3) };
    if (!baked) geoIndex.set(mesh, rec);
    return rec;
  };
  const takeMesh = (mi: any, e: any): PcSnapMesh | null => {
    const mesh = mi?.mesh;
    if (!mesh || typeof mesh.getPositions !== "function") return null;
    // Triangle lists only. A line or point mesh drawn as triangles is noise across the screen.
    const prim = mesh.primitive?.[0]?.type;
    if (typeof prim === "number" && prim !== 4) return null;
    const baked = mi.skinInstance && opts.skin !== false ? bakeSkin(mi, e) : null;
    const g = takeGeom(mesh, baked);
    if (!g) return null;
    const out: PcSnapMesh = { geo: g.geo, material: takeMaterial(mi.material, mi) };
    if (baked) { out.skinned = true; skinned++; }
    else if (mi.node && mi.node !== e && mi.node.getWorldTransform && e.getWorldTransform) {
      const ew = Array.from(e.getWorldTransform().data as ArrayLike<number>);
      const nw = Array.from(mi.node.getWorldTransform().data as ArrayLike<number>);
      const inv = mat4Invert(ew);
      if (inv) out.local = mat4Mul(inv, nw);
    }
    counts.meshes++;
    counts.triangles += g.tris;
    return out;
  };
  const take = (e: any): PcSnapEntity => {
    counts.entities++;
    if (e.enabled === false) hidden++;
    const p = e.getLocalPosition ? e.getLocalPosition() : { x: 0, y: 0, z: 0 };
    const q = e.getLocalRotation ? e.getLocalRotation() : { x: 0, y: 0, z: 0, w: 1 };
    const s = e.getLocalScale ? e.getLocalScale() : { x: 1, y: 1, z: 1 };
    const out: PcSnapEntity = {
      name: e.name || "", enabled: e.enabled !== false,
      pos: [p.x, p.y, p.z], rot: [q.x, q.y, q.z, q.w], scale: [s.x, s.y, s.z], children: [],
    };
    const mis = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
    const meshes: PcSnapMesh[] = [];
    for (const mi of mis) { const m = takeMesh(mi, e); if (m) meshes.push(m); }
    if (meshes.length) {
      out.meshes = meshes;
      const src = srcOf(e);
      if (src) out.src = src;
    }
    const l = e.light;
    if (l) {
      out.light = {
        type: l.type === "point" || l.type === "spot" ? l.type : "directional", color: col3(l.color, [1, 1, 1]),
        intensity: typeof l.intensity === "number" ? l.intensity : 1, range: typeof l.range === "number" ? l.range : 10,
        inner: typeof l.innerConeAngle === "number" ? l.innerConeAngle : 40, outer: typeof l.outerConeAngle === "number" ? l.outerConeAngle : 45,
        shadows: !!l.castShadows, enabled: l.enabled !== false,
      };
    }
    const c = e.camera;
    if (c) {
      out.camera = {
        fov: typeof c.fov === "number" ? c.fov : 45, near: typeof c.nearClip === "number" ? c.nearClip : 0.1,
        far: typeof c.farClip === "number" ? c.farClip : 1000, ortho: c.projection === 1,
        orthoHeight: typeof c.orthoHeight === "number" ? c.orthoHeight : 10,
      };
      if (c.clearColor && typeof c.clearColor.r === "number") { out.camera.clearColor = col3(c.clearColor); if (!clearColor) clearColor = out.camera.clearColor; }
    }
    // What the editor placed into the game (`pcPlace`) is left out: the mirror puts it back from
    // the sidecar, and taking it here as well would draw every placement twice.
    for (const k of e.children || []) if (!k.__studioPlaced) out.children.push(take(k));
    return out;
  };
  const rootSnap = take(root);
  // The scene's world, from the app the root belongs to.
  let world: PcSnapshot["world"] = null;
  const sc = app?.scene;
  if (sc) {
    world = {};
    if (sc.ambientLight) world.ambient = col3(sc.ambientLight);
    let type = "", color: any = null, start = 0, end = 0, density = 0;
    if (typeof sc.fog === "string") { type = sc.fog; color = sc.fogColor; start = sc.fogStart; end = sc.fogEnd; density = sc.fogDensity; }
    else if (sc.fog && typeof sc.fog === "object") { type = sc.fog.type; color = sc.fog.color; start = sc.fog.start; end = sc.fog.end; density = sc.fog.density; }
    world.fog = !type || type === "none" ? null
      : type === "linear" ? { type: "linear", color: col3(color, [0.5, 0.5, 0.5]), start, end }
      : { type: "exp2", color: col3(color, [0.5, 0.5, 0.5]), density };
  }
  if (clearColor) { world = world || {}; world.background = clearColor; }
  if (textures.length) counts.textures = textures.length;
  if (skinned) counts.skinned = skinned;
  if (hidden) counts.hidden = hidden;
  return { engine: "playcanvas", version: 2, root: rootSnap, world, counts, textures, geoms };
}

/** three's own XYZ-order euler to quaternion, so a sidecar rotation means one thing in both engines. */
export function quatFromEulerXYZ(x: number, y: number, z: number): [number, number, number, number] {
  const c1 = Math.cos(x / 2), c2 = Math.cos(y / 2), c3 = Math.cos(z / 2);
  const s1 = Math.sin(x / 2), s2 = Math.sin(y / 2), s3 = Math.sin(z / 2);
  return [
    s1 * c2 * c3 + c1 * s2 * s3,
    c1 * s2 * c3 - s1 * c2 * s3,
    c1 * c2 * s3 + s1 * s2 * c3,
    c1 * c2 * c3 - s1 * s2 * s3,
  ];
}

export function hexToRgb01(hex: string): V3 {
  const h = String(hex).replace(/^#/, "").padStart(6, "0").slice(-6);
  const n = parseInt(h, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

// ------------------------------------------------------------------ placing into a running game
//
// "Add" in the editor puts a model in the MIRROR; a game the Studio is editing live has to get it
// too, or the level the person is building exists only in the editor. A bundled PlayCanvas game
// has no `pc` on window and, like rot-rush, often no container loader until play starts — so the
// model does not travel as a GLB at all. The editor, which already has it loaded, sends it as
// plain arrays (`threeToPcPlace`), and the game builds it with ITS OWN classes, found on objects it
// already has (`pcPlace`). No second copy of the engine, nothing for the game to have imported.

export interface PcPlaceMesh {
  name: string;
  positions: PcArr; normals?: PcArr; uvs?: PcArr; colors?: PcArr; colorSize?: number; indices?: PcArr;
  /** Column-major, relative to the placed root. */
  local: number[];
  /** sRGB, the way a PlayCanvas material takes a colour. */
  color: V3;
  opacity?: number;
  /** A PNG data URL. */
  map?: string;
  nearest?: boolean;
  unlit?: boolean;
  doubleSided?: boolean;
}
export interface PcPlaceSpec {
  id: string;
  name: string;
  pos: V3; rot: V3; scale: V3;
  meshes: PcPlaceMesh[];
}

/**
 * A three object — a model the editor placed — as arrays a PlayCanvas game can build from. A
 * skinned mesh is taken in the pose its bones hold now, because a static copy of a rig with no
 * skeleton beside it is only right in that pose.
 */
export function threeToPcPlace(T: any, obj: any, meta: { id: string; name: string }, pack = true): PcPlaceSpec {
  obj.updateMatrixWorld(true);
  const inv = new T.Matrix4().copy(obj.matrixWorld).invert();
  const meshes: PcPlaceMesh[] = [];
  const pictures = new Map<any, string>();
  const picture = (tex: any): string => {
    const img = tex?.image;
    if (!img || typeof document === "undefined") return "";
    const hit = pictures.get(img);
    if (hit !== undefined) return hit;
    let url = "";
    try {
      const w = img.naturalWidth || img.width || 0, h = img.naturalHeight || img.height || 0;
      if (w && h) {
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        const g = c.getContext("2d")!;
        // three's default flips a picture on upload; drawn upright here, PlayCanvas then samples
        // it with no flip, so a flipped three texture has to arrive flipped.
        if (tex.flipY) { g.translate(0, h); g.scale(1, -1); }
        g.drawImage(img, 0, 0);
        url = c.toDataURL("image/png");
      }
    } catch { url = ""; }
    pictures.set(img, url);
    return url;
  };
  const arr = (a: ArrayLike<number>, t: "f32" | "u16" | "u32" | "u8n"): PcArr => (pack ? packArr(a, t) : Array.from(a));
  const v = new T.Vector3();
  obj.traverse((m: any) => {
    if (!m.isMesh || !m.geometry?.attributes?.position) return;
    for (let p = m; p && p !== obj; p = p.parent) if (p.visible === false) return;
    const g = m.geometry;
    const pa = g.attributes.position;
    const n = pa.count;
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      if (m.isSkinnedMesh && typeof m.getVertexPosition === "function") m.getVertexPosition(i, v);
      else v.fromBufferAttribute(pa, i);
      pos[i * 3] = v.x; pos[i * 3 + 1] = v.y; pos[i * 3 + 2] = v.z;
    }
    const out: PcPlaceMesh = {
      name: m.name || "mesh",
      positions: arr(pos, "f32"),
      local: new T.Matrix4().multiplyMatrices(inv, m.matrixWorld).toArray(),
      color: [1, 1, 1],
    };
    const na = g.attributes.normal;
    if (na) { const nor = new Float32Array(n * 3); for (let i = 0; i < n; i++) { v.fromBufferAttribute(na, i); nor[i * 3] = v.x; nor[i * 3 + 1] = v.y; nor[i * 3 + 2] = v.z; } out.normals = arr(nor, "f32"); }
    const ua = g.attributes.uv;
    if (ua) { const uv = new Float32Array(n * 2); for (let i = 0; i < n; i++) { uv[i * 2] = ua.getX(i); uv[i * 2 + 1] = ua.getY(i); } out.uvs = arr(uv, "f32"); }
    const ca = g.attributes.color;
    if (ca) {
      const k = ca.itemSize;
      const col = new Float32Array(n * k);
      for (let i = 0; i < n; i++) for (let j = 0; j < k; j++) col[i * k + j] = Math.max(0, Math.min(1, ca.getComponent ? ca.getComponent(i, j) : ca.array[i * k + j]));
      out.colors = arr(col, "u8n");
      out.colorSize = k;
    }
    if (g.index) out.indices = arr(g.index.array, n > 65535 ? "u32" : "u16");
    const mat = Array.isArray(m.material) ? m.material[0] : m.material;
    if (mat) {
      if (mat.color) {
        const c = { r: 1, g: 1, b: 1 };
        if (T.SRGBColorSpace && mat.color.getRGB) mat.color.getRGB(c, T.SRGBColorSpace); else { c.r = mat.color.r; c.g = mat.color.g; c.b = mat.color.b; }
        out.color = [c.r, c.g, c.b];
      }
      if (mat.map) {
        const url = picture(mat.map);
        if (url) out.map = url;
        if (mat.map.magFilter === T.NearestFilter) out.nearest = true;
      }
      if (mat.transparent && mat.opacity < 1) out.opacity = mat.opacity;
      if (mat.side === T.DoubleSide) out.doubleSided = true;
      if (mat.isMeshBasicMaterial) out.unlit = true;
    }
    meshes.push(out);
  });
  return {
    id: meta.id, name: meta.name,
    pos: [obj.position.x, obj.position.y, obj.position.z],
    rot: [obj.rotation.x, obj.rotation.y, obj.rotation.z],
    scale: [obj.scale.x, obj.scale.y, obj.scale.z],
    meshes,
  };
}

/** The game's own classes, from objects it already has: a bundled game has no `pc` to ask. */
function pcClassesOf(app: any): { Entity: any; Mesh: any; MeshInstance: any; Material: any; Texture: any } | null {
  let Mesh: any = null, MeshInstance: any = null, Material: any = null, Texture: any = null;
  walkTree(app.root, (e) => {
    for (const mi of (e.render?.meshInstances || e.model?.meshInstances || [])) {
      if (!MeshInstance && mi?.mesh) { MeshInstance = mi.constructor; Mesh = mi.mesh.constructor; }
      const m = mi?.material;
      if (m && !Material && "diffuseMap" in m && "emissiveMap" in m && typeof m.update === "function") Material = m.constructor;
      if (m && !Texture) {
        for (const slot of ["diffuseMap", "emissiveMap", "opacityMap", "normalMap"]) {
          const t = m[slot];
          if (t && typeof t.setSource === "function") { Texture = t.constructor; break; }
        }
      }
    }
  });
  const Entity = app?.root?.constructor;
  return Entity && Mesh && MeshInstance && Material ? { Entity, Mesh, MeshInstance, Material, Texture } : null;
}

/**
 * Build a placed model in a running PlayCanvas game, as a child of the app's root named for the
 * placement, so the sidecar's key for it is the same on both sides. An earlier copy of the same
 * placement is replaced, which makes sending it again harmless.
 */
export async function pcPlace(app: any, spec: PcPlaceSpec): Promise<{ ok: boolean; error?: string; meshes?: number; textures?: number }> {
  if (!app?.root || !spec) return { ok: false, error: "no app, or nothing to place" };
  const K = pcClassesOf(app);
  if (!K) return { ok: false, error: "the game has no mesh and material of its own to learn its classes from" };
  const device = app.graphicsDevice;
  for (const c of [...(app.root.children || [])]) if (c.__studioPlaced === spec.id) c.destroy?.();
  const root = new K.Entity(spec.name);
  root.__studioPlaced = spec.id;
  root.setLocalPosition(spec.pos[0], spec.pos[1], spec.pos[2]);
  const q = quatFromEulerXYZ(spec.rot[0], spec.rot[1], spec.rot[2]);
  root.setLocalRotation(q[0], q[1], q[2], q[3]);
  root.setLocalScale(spec.scale[0], spec.scale[1], spec.scale[2]);
  const made = new Map<string, any>();
  const texture = async (url: string, nearest: boolean): Promise<any> => {
    if (!K.Texture || typeof Image === "undefined") return null;
    const key = url.length + ":" + url.slice(-64) + (nearest ? ":n" : "");
    if (made.has(key)) return made.get(key);
    const img: HTMLImageElement | null = await new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = url; });
    if (!img) { made.set(key, null); return null; }
    // Sized before the picture goes in: a texture made at its default 4x4 and then given a big
    // picture is a sub-image upload WebGL refuses — the fault rot-rush's own loader documents.
    const t = new K.Texture(device, {
      name: "studio-placed", width: img.width, height: img.height, mipmaps: !nearest,
      minFilter: nearest ? 0 : 5, magFilter: nearest ? 0 : 1, srgb: true,
    });
    t.setSource(img);
    made.set(key, t);
    return t;
  };
  let textures = 0;
  for (const m of spec.meshes || []) {
    const mesh = new K.Mesh(device);
    mesh.setPositions(unpackArr(m.positions) as Float32Array);
    const nor = unpackArr(m.normals); if (nor) mesh.setNormals(nor as Float32Array);
    const uv = unpackArr(m.uvs); if (uv) mesh.setUvs(0, uv as Float32Array);
    const col = unpackArr(m.colors); if (col && typeof mesh.setColors === "function") mesh.setColors(col as Float32Array, m.colorSize || 4);
    const idx = unpackArr(m.indices, true); if (idx) mesh.setIndices(idx as Uint16Array | Uint32Array);
    mesh.update(4, true);
    const mat = new K.Material();
    try { mat.diffuse.set(m.color[0], m.color[1], m.color[2]); } catch { /* a material with no diffuse */ }
    if (col) mat.diffuseVertexColor = true;
    if (m.map) { const t = await texture(m.map, !!m.nearest); if (t) { mat.diffuseMap = t; textures++; } }
    if (m.opacity !== undefined && m.opacity < 1) { mat.opacity = m.opacity; mat.blendType = 2; }
    if (m.doubleSided) mat.cull = 0;
    if (m.unlit) mat.useLighting = false;
    mat.update();
    const child = new K.Entity(m.name || "mesh");
    const d = mat4Decompose(m.local);
    child.setLocalPosition(d.position[0], d.position[1], d.position[2]);
    child.setLocalRotation(d.quaternion[0], d.quaternion[1], d.quaternion[2], d.quaternion[3]);
    child.setLocalScale(d.scale[0], d.scale[1], d.scale[2]);
    child.addComponent("render", { meshInstances: [new K.MeshInstance(mesh, mat)] });
    root.addChild(child);
  }
  app.root.addChild(root);
  return { ok: true, meshes: (spec.meshes || []).length, textures };
}

/** Take a placement back out of a running PlayCanvas game. */
export function pcUnplace(app: any, id: string): number {
  let n = 0;
  for (const c of [...(app?.root?.children || [])]) if (c.__studioPlaced === id) { c.destroy?.(); n++; }
  return n;
}

/** The sidecar onto PlayCanvas entities. `ops` from makeOpsPc for a modifier stack; `pc` only
 *  for a whole-asset modifier, which has to make a MeshInstance and a render component. */
export function pcApply(root: any, edits: any, ops?: Ops, pc?: any): AppliedEdits {
  const out: AppliedEdits = { parts: 0, mods: 0, verts: 0, missing: [], errors: [] };
  if (!root) return out;
  const byKey = new Map<string, any>();
  for (const [o, k] of stableKeys(root)) byKey.set(k, o);
  const materialsOf = (e: any): any[] => {
    const mis = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
    const set = new Set<any>();
    for (const mi of mis) if (mi.material) set.add(mi.material);
    return [...set];
  };
  for (const [key, o] of Object.entries<any>(edits?.parts || {})) {
    const t = byKey.get(key);
    if (!t) {
      // A PIECE of a merged mesh: no entity of its own, so its corners are moved in the mesh.
      const pk = parsePieceKey(key);
      const host = pk ? byKey.get(pk.mesh) : null;
      if (pk && host) {
        try {
          const r = movePiecePc(host, pk.t0, pk.t1, o);
          if (r.error) out.errors.push(key + ": " + r.error); else out.parts++;
        } catch (e: any) { out.errors.push(key + ": " + String(e?.message || e).slice(0, 120)); }
        continue;
      }
      out.missing.push(key);
      continue;
    }
    try {
      if (o.pos && t.setLocalPosition) t.setLocalPosition(o.pos[0], o.pos[1], o.pos[2]);
      if (o.rot && t.setLocalRotation) { const q = quatFromEulerXYZ(o.rot[0], o.rot[1], o.rot[2]); t.setLocalRotation(q[0], q[1], q[2], q[3]); }
      if (o.scale && t.setLocalScale) t.setLocalScale(o.scale[0], o.scale[1], o.scale[2]);
      if (o.hidden !== undefined) t.enabled = !o.hidden;
      const light = t.light, cam = t.camera;
      if (light) {
        if (o.color) { const c = hexToRgb01(o.color); light.color = light.color.set(c[0], c[1], c[2]); }
        if (typeof o.intensity === "number") light.intensity = o.intensity;
        if (typeof o.distance === "number") light.range = o.distance;
        if (typeof o.angle === "number") light.outerConeAngle = (o.angle * 180) / Math.PI;
        if (typeof o.penumbra === "number") light.innerConeAngle = light.outerConeAngle * (1 - o.penumbra);
        if (typeof o.shadow === "boolean") light.castShadows = o.shadow;
      }
      if (cam) {
        if (typeof o.fov === "number") cam.fov = o.fov;
        if (typeof o.near === "number") cam.nearClip = o.near;
        if (typeof o.far === "number") cam.farClip = o.far;
      }
      if (!light && (o.color || o.roughness !== undefined || o.metalness !== undefined)) {
        for (const m of materialsOf(t)) {
          if (o.color && m.diffuse?.set) { const c = hexToRgb01(o.color); m.diffuse.set(c[0], c[1], c[2]); }
          if (o.roughness !== undefined) { if ("gloss" in m) m.gloss = 1 - o.roughness; else if ("shininess" in m) m.shininess = (1 - o.roughness) * 100; }
          if (o.metalness !== undefined) { m.metalness = o.metalness; if ("useMetalness" in m) m.useMetalness = true; }
          m.update?.();
        }
      }
      out.parts++;
    } catch (e: any) {
      out.errors.push(key + ": " + String(e?.message || e).slice(0, 120));
    }
  }
  const w = edits?.world;
  if (w && typeof w === "object") {
    const app = root._app || (root.getApplication && root.getApplication());
    if (typeof w.background === "string") {
      const c = hexToRgb01(w.background);
      walkTree(root, (e) => { if (e.camera?.clearColor?.set) e.camera.clearColor = e.camera.clearColor.set(c[0], c[1], c[2], 1); });
    }
    const sc = app?.scene;
    if (sc && w.fog !== undefined) {
      const f = w.fog;
      const type = f === null ? "none" : f.type === "exp2" ? "exp2" : "linear";
      const c = f ? hexToRgb01(f.color) : null;
      if (sc.fog && typeof sc.fog === "object") {
        sc.fog.type = type;
        if (c) sc.fog.color.set(c[0], c[1], c[2]);
        if (f && typeof f.near === "number") sc.fog.start = f.near;
        if (f && typeof f.far === "number") sc.fog.end = f.far;
        if (f && typeof f.density === "number") sc.fog.density = f.density;
      } else {
        sc.fog = type;
        if (c && sc.fogColor?.set) sc.fogColor.set(c[0], c[1], c[2]);
        if (f && typeof f.near === "number") sc.fogStart = f.near;
        if (f && typeof f.far === "number") sc.fogEnd = f.far;
        if (f && typeof f.density === "number") sc.fogDensity = f.density;
      }
    } else if (!sc && w.fog !== undefined) {
      out.errors.push("world: fog needs the app; pass an entity that belongs to one");
    }
  }
  const mods: any[] = Array.isArray(edits?.mods) ? edits.mods.filter((m: any) => m && !m.off) : [];
  if (mods.length && !ops) { out.errors.push("the stack needs the operations: pass makeOpsPc(pc, device)"); return out; }
  for (const m of mods) {
    try {
      const perMesh = !!m.target || !(m.op === "skin" || m.op === "remesh");
      if (perMesh) {
        const e = m.target ? byKey.get(m.target) : root;
        if (!e) { out.missing.push(m.target); continue; }
        let touched = 0;
        walkTree(e, (n) => {
          const mis = (n.render && n.render.meshInstances) || (n.model && n.model.meshInstances) || [];
          for (const mi of mis) {
            const next = applyMod(ops!, m, mi.mesh);
            if (next) { mi.mesh = next; touched++; }
          }
        });
        if (!touched) { out.errors.push(m.op + ": " + m.target + " holds no mesh"); continue; }
      } else {
        if (!pc) { out.errors.push(m.op + ": a whole-asset modifier needs pc as well"); continue; }
        const merged = ops!.merge(root);
        const next = applyMod(ops!, m, merged, root);
        if (!next) { out.errors.push(m.op + ": produced nothing"); continue; }
        let mat: any = null;
        walkTree(root, (n) => { if (!mat) for (const mi of (n.render?.meshInstances || n.model?.meshInstances || [])) { if (mi.material) { mat = mi.material; break; } } });
        for (const c of (root.children || []).slice()) if (!c.light && !c.camera) c.destroy?.();
        const holder = new pc.Entity("merged");
        holder.addComponent("render", { meshInstances: [new pc.MeshInstance(next, mat || new pc.StandardMaterial())] });
        root.addChild(holder);
      }
      out.mods++;
    } catch (e: any) {
      out.errors.push(m.op + ": " + String(e?.message || e).slice(0, 160));
    }
  }
  return out;
}

/** The operations on pc.Mesh. The algorithms are the array functions above; only how a mesh is
 *  read and written differs from three, and `merge` bakes world transforms the same way. */
export function makeOpsPc(pc: any, device: any): Ops {
  const readMesh = (mesh: any): { pos: Float32Array; idx: Uint32Array } => {
    const P: number[] = [];
    if (!mesh?.getPositions || !mesh.getPositions(P)) return { pos: new Float32Array(0), idx: new Uint32Array(0) };
    const I: number[] = [];
    const pos = Float32Array.from(P);
    const idx = mesh.getIndices && mesh.getIndices(I) && I.length ? Uint32Array.from(I) : indexOf(pos, null);
    return { pos, idx };
  };
  const writeMesh = (pos: Float32Array, idx: Uint32Array | null, normal?: Float32Array, uv?: Float32Array): any => {
    const opts: any = {};
    if (idx) opts.indices = Array.from(idx);
    if (normal) opts.normals = Array.from(normal);
    else { const n = normalsFor(pos, idx || indexOf(pos, null)); opts.normals = Array.from(n); }
    if (uv) opts.uvs = Array.from(uv);
    return pc.createMesh(device, Array.from(pos), opts);
  };
  const read = (g: any) => (g?.getPositions ? readMesh(g) : { pos: new Float32Array(0), idx: new Uint32Array(0) });
  function merge(entity: any): any {
    const P: number[] = [];
    const I: number[] = [];
    entity.syncHierarchy?.();
    walkTree(entity, (n) => {
      if (n.enabled === false) return;
      const mis = (n.render?.meshInstances || n.model?.meshInstances || []);
      for (const mi of mis) {
        const { pos, idx } = readMesh(mi.mesh);
        if (!pos.length) continue;
        const m = (mi.node || n).getWorldTransform().data;
        const base = P.length / 3;
        for (let i = 0; i < pos.length; i += 3) {
          const x = pos[i], y = pos[i + 1], z = pos[i + 2];
          P.push(m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]);
        }
        for (let i = 0; i < idx.length; i++) I.push(base + idx[i]);
      }
    });
    return writeMesh(Float32Array.from(P), Uint32Array.from(I));
  }
  const kitEngine = pcKitEngine(pc);
  const ops: Ops = {
    merge,
    weld(geo, tol = 1e-3) { const { pos, idx } = read(geo); const w = weldArrays(pos, idx, tol); return writeMesh(w.pos, w.idx); },
    smooth(geo, angleDeg = 40) { const { pos, idx } = read(geo); const s = smoothCornerNormals(pos, idx, angleDeg); return writeMesh(s.pos, null, s.normal); },
    subdivide(geo, levels = 1) { const { pos, idx } = read(geo); const s = subdivideArrays(pos, idx, levels); return writeMesh(s.pos, s.idx); },
    mirror(geo, axis = 0, weldSeam = true) { const { pos, idx } = read(geo); const m = mirrorArrays(pos, idx, axis, weldSeam); return writeMesh(m.pos, m.idx); },
    array(geo, count, offset, weldJoins = false) { const { pos, idx } = read(geo); const a = arrayArrays(pos, idx, count, offset, weldJoins); return writeMesh(a.pos, a.idx); },
    deform(geo, kind, amount, axis = 1) { const { pos, idx } = read(geo); return writeMesh(deformArrays(pos, kind, amount, axis), idx); },
    displace(geo, amp, freq = 3, octaves = 3, seed = 1) { const { pos, idx } = read(geo); return writeMesh(displaceArrays(pos, normalsFor(pos, idx), amp, freq, octaves, seed), idx); },
    solidify(geo, thickness) { const { pos, idx } = read(geo); const s = solidifyArrays(pos, idx, normalsFor(pos, idx), thickness); return writeMesh(s.pos, s.idx); },
    scatter(geo, count, seed = 1) { const { pos, idx } = read(geo); return scatterArrays(pos, idx, count, seed); },
    uv(geo, mode = "box", scale = 1) { const { pos, idx } = read(geo); return writeMesh(pos, idx, undefined, uvArrays(pos, idx, mode, scale)); },
    simplify(geo, cell) { const { pos, idx } = read(geo); const s = simplifyArrays(pos, idx, cell); return writeMesh(s.pos, s.idx); },
    flip(geo) { const { pos, idx } = read(geo); return writeMesh(pos, flipArrays(idx)); },
    check(target) { const geo = target?.getPositions ? target : merge(target); const { pos, idx } = read(geo); return checkArrays(pos, idx, undefined, undefined); },
    skin(object, opts = {}) {
      const { tol = 1e-3, angle = 40, subdivide: levels = 0 } = opts;
      let { pos, idx } = read(object?.getPositions ? object : merge(object));
      const w = weldArrays(pos, idx, tol);
      pos = w.pos; idx = w.idx;
      if (levels > 0) ({ pos, idx } = subdivideArrays(pos, idx, levels));
      const s = smoothCornerNormals(pos, idx, angle);
      return writeMesh(s.pos, null, s.normal);
    },
    hull(target) {
      const pts = target instanceof Float32Array ? target : read(target?.getPositions ? target : merge(target)).pos;
      const h = hullArrays(pts);
      if (!h) throw new Error("hull: the points are flat or a line, nothing to wrap");
      const s = smoothCornerNormals(h.pos, h.idx, 0);
      return writeMesh(s.pos, null, s.normal);
    },
    boolean(a, b, op = "union") {
      const A = read(a?.getPositions ? a : merge(a)), B = read(b?.getPositions ? b : merge(b));
      const r = csgArrays(A, B, op);
      const s = smoothCornerNormals(r.pos, r.idx, 40);
      return writeMesh(s.pos, null, s.normal);
    },
    isosurface(field, min, max, res = 32, iso = 0) { const r = isosurfaceArrays(field, min, max, res, iso); const s = smoothCornerNormals(r.pos, r.idx, 80); return writeMesh(s.pos, null, s.normal); },
    blobs(balls, opts = {}) {
      const { res = 48, margin = 1.5, angle = 80 } = opts;
      const box = blobBounds(balls, margin, res);
      const r = isosurfaceArrays(blobField(balls), box.min, box.max, box.res, 0);
      const s = smoothCornerNormals(r.pos, r.idx, angle);
      return writeMesh(s.pos, null, s.normal);
    },
    ik: fabrik,
    subsurf(geo, levels = 1, opts = {}) { const { pos, idx } = read(geo); const r = subdivideCC(pos, idx, levels, opts); const s = smoothCornerNormals(r.pos, r.idx, opts.creaseAngle ? Math.min(89, opts.creaseAngle) : 60); return writeMesh(s.pos, null, s.normal); },
    bevel(geo, opts = {}) { const { pos, idx } = read(geo); const r = bevelArrays(pos, idx, opts); const s = smoothCornerNormals(r.pos, r.idx, 40); return writeMesh(s.pos, null, s.normal); },
    unwrap(geo, opts = {}) { const { pos, idx } = read(geo); const r = unwrapArrays(pos, idx, opts); return writeMesh(r.pos, r.idx, normalsFor(r.pos, r.idx), r.uv); },
    remesh(geo, opts = {}) { const { pos, idx } = read(geo); const r = remeshArrays(pos, idx, opts); const s = smoothCornerNormals(r.pos, r.idx, 80); return writeMesh(s.pos, null, s.normal); },
    relax(geo, opts = {}) { const { pos, idx } = read(geo); const p = relaxArrays(pos, idx, opts); return writeMesh(p, idx, normalsFor(p, idx)); },
    heatWeights(geo, bones, opts = {}) { const { pos, idx } = read(geo); return heatWeightsArrays(pos, idx, bones, opts); },
    bakeAO(geo, size = 256, opts = {}) {
      const { pos, idx } = read(geo);
      const U: number[] = [];
      if (!geo?.getUvs || !geo.getUvs(0, U) || !U.length) throw new Error("bakeAO: the mesh has no uv — unwrap it first");
      return bakeAO(pos, idx, Float32Array.from(U), size, opts);
    },
    bakeCurvature(geo, size = 256, opts = {}) {
      const { pos, idx } = read(geo);
      const U: number[] = [];
      if (!geo?.getUvs || !geo.getUvs(0, U) || !U.length) throw new Error("bakeCurvature: the mesh has no uv — unwrap it first");
      return bakeCurvature(pos, idx, Float32Array.from(U), size, opts);
    },
    bakeNormalMap(low, high, size = 512, opts = {}) {
      const L = read(low), H = read(high?.getPositions ? high : merge(high));
      const U: number[] = [];
      if (!low?.getUvs || !low.getUvs(0, U) || !U.length) throw new Error("bakeNormalMap: the low-poly has no uv — unwrap it first");
      return bakeNormalMap({ pos: L.pos, idx: L.idx, uv: Float32Array.from(U) }, H, size, opts);
    },
    texture(baked) {
      const tex = new pc.Texture(device, { width: baked.width, height: baked.height, format: pc.PIXELFORMAT_RGBA8, mipmaps: true });
      const px = tex.lock();
      px.set(baked.data);
      tex.unlock();
      return tex;
    },
    bake(geo, size, fn, opts = {}) {
      return bakeFunction(pcBakeTarget(geo, "bake"), size, fn, { ...opts, high: pcHighOf(opts.high) });
    },
    bakeMaterial(geo, mat, size = 1024, opts = {}) {
      return bakeMaterial(pcBakeTarget(geo, "bakeMaterial"), pcMaterialOf(mat), size, { ...opts, high: pcHighOf(opts.high) });
    },
    atlas(geos, opts = {}) {
      const src = geos.map((g) => (g?.getPositions ? g : merge(g)));
      return atlasArrays(src.map((g, i) => { const { pos, idx } = read(g); return { pos, idx, weight: opts.weights?.[i] }; }), opts)
        .map((r) => writeMesh(r.pos, r.idx, r.normal, r.uv));
    },
    bakeAtlas(parts, size = 2048, opts = {}) {
      const geos = ops.atlas(parts.map((p) => p.geo), { margin: opts.margin, angle: opts.angle, weights: parts.map((p) => p.weight) });
      const arrays = geos.map((g, i) => ({ ...pcBakeTarget(g, "bakeAtlas"), mat: pcMaterialOf(parts[i].mat), high: pcHighOf(parts[i].high) }));
      return { geos, maps: bakeAtlas(arrays, size, opts) };
    },
    applyMaps() {
      throw new Error("applyMaps is three-only; on PlayCanvas set material.diffuseMap = ops.texture(maps.baseColor), material.normalMap = ops.texture(maps.normal), material.aoMap = ops.texture(maps.orm) and call material.update()");
    },
    materials: MATERIALS,
    hand(opts, res = 56) {
      const h = handField(opts);
      const s = handSurface(h, res);
      const mesh = writeMesh(s.pos, s.idx, s.normal);
      Object.defineProperty(mesh, "__hand", { value: h, enumerable: false, configurable: true });
      return mesh;
    },
    grip(entity, o) {
      const mi = entity?.render?.meshInstances?.[0] || entity?.model?.meshInstances?.[0];
      const hand: Hand | undefined = o.hand || entity?.__hand || mi?.mesh?.__hand;
      if (!hand) throw new Error("grip: no hand — pass { hand } or an entity whose mesh came from ops.hand");
      const p = gripPlacement({ a: o.a, b: o.b, t: o.t, face: o.face, aim: o.aim, hand });
      entity.setLocalPosition(p.position[0], p.position[1], p.position[2]);
      entity.setLocalRotation(new pc.Quat(p.quaternion[0], p.quaternion[1], p.quaternion[2], p.quaternion[3]));
      return p;
    },
    densify(geo, maxEdge) {
      const { pos, idx } = read(geo?.getPositions ? geo : merge(geo));
      const r = densifyArrays(pos, idx, maxEdge);
      return writeMesh(r.pos, r.idx, smoothNormalsWelded(r.pos, r.idx, 80));
    },
    sculpt(geo, strokes, opts = {}) {
      const src = geo?.getPositions ? geo : merge(geo);
      const { pos, idx } = read(src);
      const p = sculptArrays(pos, idx, strokes);
      const U: number[] = [];
      const uv = src?.getUvs && src.getUvs(0, U) && U.length ? Float32Array.from(U) : undefined;
      return writeMesh(p, idx, smoothNormalsWelded(p, idx, opts.angle ?? 80), uv);
    },
    decimate(geo, opts = {}) {
      const src = geo?.getPositions ? geo : merge(geo);
      const { pos, idx } = read(src);
      const U: number[] = [];
      const uv = opts.uv ?? (src?.getUvs && src.getUvs(0, U) && U.length ? Float32Array.from(U) : undefined);
      const r = decimateArrays(pos, idx, { ...opts, uv });
      return writeMesh(r.pos, r.idx, smoothNormalsWelded(r.pos, r.idx, opts.angle ?? 60), r.uv);
    },
    sweep(path, opts = {}) { const r = sweepArrays(path, opts); return writeMesh(r.pos, r.idx, r.normal, r.uv); },
    rim(geo, opts) { const { pos, idx } = read(geo?.getPositions ? geo : merge(geo)); const r = rimArrays(pos, idx, opts); return writeMesh(r.pos, r.idx, r.normal, r.uv); },
    borders(geo, tol) { const { pos, idx } = read(geo?.getPositions ? geo : merge(geo)); return boundaryLoops(pos, idx, tol); },
    reach,
    noise: noise3,
    fbm,
    rng,
    kit: (source, opts) => kitWith(kitEngine, source, opts),
    repeat: (piece, at, opts) => repeatWith(kitEngine, piece, at, opts),
    facade: (kit, opts, pick) => facadeWith(kitEngine, kit, opts, pick),
    bays: bayLayout,
    perimeter: perimeterSlots,
  };
  function pcBakeTarget(geo: any, what: string): { pos: Float32Array; idx: Uint32Array; uv: Float32Array; normal?: Float32Array } {
    const g = geo?.getPositions ? geo : merge(geo);
    const { pos, idx } = read(g);
    const U: number[] = [], N: number[] = [];
    if (!g?.getUvs || !g.getUvs(0, U) || !U.length) throw new Error(what + ": the mesh has no uv — unwrap it first (ops.unwrap, or ops.atlas for several parts)");
    const hasN = !!(g.getNormals && g.getNormals(N) && N.length === pos.length);
    return { pos, idx, uv: Float32Array.from(U), normal: hasN ? Float32Array.from(N) : undefined };
  }
  function pcHighOf(high: any): { pos: Float32Array; idx: Uint32Array } | undefined {
    if (!high) return undefined;
    return read(high.getPositions ? high : merge(high));
  }
  function pcMaterialOf(mat: SmartMaterial | string): SmartMaterial {
    if (typeof mat !== "string") return mat;
    const make = MATERIALS[mat];
    if (!make) throw new Error("no material '" + mat + "' — ops.materials has: " + Object.keys(MATERIALS).join(", "));
    return make();
  }
  return ops;
}

// Column-major 4x4, as both engines store them.
export function mat4Mul(a: number[], b: number[]): number[] {
  const o = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
  }
  return o;
}

export function mat4Invert(m: number[]): number[] | null {
  const [a00, a01, a02, a03, a10, a11, a12, a13, a20, a21, a22, a23, a30, a31, a32, a33] = m;
  const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10;
  const b03 = a01 * a12 - a02 * a11, b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12;
  const b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30, b08 = a20 * a33 - a23 * a30;
  const b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
  let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
  if (!det) return null;
  det = 1 / det;
  return [
    (a11 * b11 - a12 * b10 + a13 * b09) * det, (a02 * b10 - a01 * b11 - a03 * b09) * det, (a31 * b05 - a32 * b04 + a33 * b03) * det, (a22 * b04 - a21 * b05 - a23 * b03) * det,
    (a12 * b08 - a10 * b11 - a13 * b07) * det, (a00 * b11 - a02 * b08 + a03 * b07) * det, (a32 * b02 - a30 * b05 - a33 * b01) * det, (a20 * b05 - a22 * b02 + a23 * b01) * det,
    (a10 * b10 - a11 * b08 + a13 * b06) * det, (a01 * b08 - a00 * b10 - a03 * b06) * det, (a30 * b04 - a31 * b02 + a33 * b00) * det, (a21 * b02 - a20 * b04 - a23 * b00) * det,
    (a11 * b07 - a10 * b09 - a12 * b06) * det, (a00 * b09 - a01 * b07 + a02 * b06) * det, (a31 * b01 - a30 * b03 - a32 * b00) * det, (a20 * b03 - a21 * b01 + a22 * b00) * det,
  ];
}

// ------------------------------------------------------------------ the scene report
//
// `check()` says whether a MESH is sound. This says whether a SCENE is: what a reviewer would
// eyeball in the first five seconds, as numbers, so an agent that cannot look can still know.

export interface SceneReport {
  meshes: number;
  triangles: number;
  materials: number;
  lights: Record<string, number>;
  cameras: number;
  shadows: { casters: number; receivers: number; lightsCasting: number };
  bounds: { min: V3; max: V3; size: V3 } | null;
  /** Objects whose lowest point is below the floor by more than a hair. */
  belowFloor: string[];
  /** Objects floating clear of the floor when the scene otherwise stands on it. */
  floating: string[];
  /** Names used more than once. Every by-name edit on these lands on the first one only. */
  duplicates: string[];
  unnamed: number;
  /** Objects at least a thousand times longer than they are thick: almost always a mistake, and
   *  the ground plane, which is the one legitimate case and is named in the note. */
  paperThin: string[];
  notes: string[];
}

export function sceneReport(root: any): SceneReport {
  const r: SceneReport = {
    meshes: 0, triangles: 0, materials: 0, lights: {}, cameras: 0,
    shadows: { casters: 0, receivers: 0, lightsCasting: 0 },
    bounds: null, belowFloor: [], floating: [], duplicates: [], unnamed: 0, paperThin: [], notes: [],
  };
  if (!root?.traverse) return r;
  root.updateMatrixWorld?.(true);
  const mats = new Set<any>();
  const names = new Map<string, number>();
  const boxes: Array<{ name: string; min: V3; max: V3 }> = [];
  const lo: V3 = [Infinity, Infinity, Infinity];
  const hi: V3 = [-Infinity, -Infinity, -Infinity];
  root.traverse((o: any) => {
    if (o === root) return;
    if (o.name) names.set(o.name, (names.get(o.name) || 0) + 1); else r.unnamed++;
    if (o.isLight) {
      const t = String(o.type || "Light").replace(/Light$/, "").toLowerCase() || "light";
      r.lights[t] = (r.lights[t] || 0) + 1;
      if (o.castShadow) r.shadows.lightsCasting++;
      return;
    }
    if (o.isCamera) { r.cameras++; return; }
    if (!o.isMesh || !o.geometry?.attributes?.position) return;
    r.meshes++;
    const g = o.geometry;
    r.triangles += g.index ? g.index.count / 3 : g.attributes.position.count / 3;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) if (m) mats.add(m);
    if (o.castShadow) r.shadows.casters++;
    if (o.receiveShadow) r.shadows.receivers++;
    if (!g.boundingBox) g.computeBoundingBox?.();
    const bb = g.boundingBox;
    if (!bb) return;
    // The eight corners through the world matrix, without a Box3 (no engine here).
    const e = o.matrixWorld?.elements;
    if (!e) return;
    const mn: V3 = [Infinity, Infinity, Infinity], mx: V3 = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < 8; i++) {
      const x = i & 1 ? bb.max.x : bb.min.x, y = i & 2 ? bb.max.y : bb.min.y, z = i & 4 ? bb.max.z : bb.min.z;
      const wx = e[0] * x + e[4] * y + e[8] * z + e[12];
      const wy = e[1] * x + e[5] * y + e[9] * z + e[13];
      const wz = e[2] * x + e[6] * y + e[10] * z + e[14];
      mn[0] = Math.min(mn[0], wx); mn[1] = Math.min(mn[1], wy); mn[2] = Math.min(mn[2], wz);
      mx[0] = Math.max(mx[0], wx); mx[1] = Math.max(mx[1], wy); mx[2] = Math.max(mx[2], wz);
    }
    boxes.push({ name: o.name || "(unnamed mesh)", min: mn, max: mx });
    for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], mn[k]); hi[k] = Math.max(hi[k], mx[k]); }
  });
  r.materials = mats.size;
  r.triangles = Math.round(r.triangles);
  for (const [n, c] of names) if (c > 1) r.duplicates.push(n);
  if (boxes.length) {
    const size: V3 = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
    r.bounds = { min: lo, max: hi, size };
    const extent = Math.max(size[0], size[1], size[2]) || 1;
    const hair = extent * 0.002;
    // Something stands on the floor when anything's lowest point is at it — not when the scene's
    // lowest point is, because one sunk crate would then hide every balloon.
    const standsOnFloor = boxes.some((b) => Math.abs(b.min[1]) <= hair);
    for (const b of boxes) {
      if (b.min[1] < -hair) r.belowFloor.push(b.name);
      else if (standsOnFloor && b.min[1] > extent * 0.05 && b.max[1] - b.min[1] < extent * 0.5) r.floating.push(b.name);
      const s = [b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]].sort((p, q) => q - p);
      if (s[0] > 0 && s[2] * 1000 < s[0]) r.paperThin.push(b.name);
    }
  }
  const n = r.notes;
  const lightCount = Object.values(r.lights).reduce((a, c) => a + c, 0);
  if (!lightCount) n.push("No lights. The scene is unlit unless the game adds its own.");
  if (!r.cameras) n.push("No camera. The game must supply one; the editor frames it for you.");
  if (r.shadows.lightsCasting && !r.shadows.receivers) n.push(r.shadows.lightsCasting + " light(s) cast shadows but no mesh receives them: set receiveShadow on the floor.");
  if (r.shadows.lightsCasting && !r.shadows.casters) n.push(r.shadows.lightsCasting + " light(s) cast shadows but no mesh casts one: set castShadow on the objects.");
  if (r.duplicates.length) n.push("Names used twice: " + r.duplicates.slice(0, 6).join(", ") + (r.duplicates.length > 6 ? "…" : "") + ". A by-name edit reaches only the first; name them apart.");
  if (r.unnamed > 0) n.push(r.unnamed + " object(s) have no name. They can be selected but an edit on them is keyed by position in the tree, which a code change moves.");
  if (r.belowFloor.length) n.push("Below the floor: " + r.belowFloor.slice(0, 6).join(", ") + ".");
  if (r.floating.length) n.push("Floating above the floor: " + r.floating.slice(0, 6).join(", ") + ". Meant to?");
  if (r.paperThin.length) n.push("Paper-thin: " + r.paperThin.slice(0, 6).join(", ") + ". Right for a ground plane, wrong for anything else.");
  if (r.bounds) {
    const s = r.bounds.size;
    const big = Math.max(s[0], s[1], s[2]);
    if (big > 500) n.push("The scene spans " + Math.round(big) + " units. If a unit is a metre, that is a town; a camera far plane and a shadow map both suffer.");
    if (big < 0.05) n.push("The scene spans " + big.toFixed(3) + " units. If a unit is a metre, it is a coin.");
  }
  if (!n.length) n.push("Nothing to flag: lit, framed, named, and standing on the floor.");
  return r;
}

// ------------------------------------------------------------------ modular kits
//
// A configurator building is not one mesh. It is a KIT — a wall bay, a window bay, a shop front, a
// corner post, each made once — plus the RULES that place them. Widen the building and it gains
// window bays; it never stretches the ones it has. That rule is what separates a modular asset from
// a scaled one, and it is arithmetic, so it lives here as arithmetic: tested in node, shared by both
// engines, with a thin layer beside it that measures pieces and places copies.
//
//   bayLayout(length, bay)       THE rule: the whole bays that fit, the leftover spread evenly.
//   facadeSlots(opts)            the rule on every floor of one wall, as (floor, bay) slots.
//   facadePlan(opts, pick)       those slots with a piece in each: spans and fillers resolved.
//   perimeterSlots(w, d, step)   the rule round a rectangle: roof trims, dentils, corner posts.
//   placementMatrix(p)           a slot (position, yaw, flip, scale) as the 4x4 both engines take.
//
// On the Ops object, for both engines: `kit(source)` measures named pieces and puts every pivot on
// one anchor; `repeat(piece, at)` draws the copies, instanced when the piece is one mesh;
// `facade(kit, opts, pick)` is a whole wall in one call; `bays` and `perimeter` are the two pure
// layouts again, so one object carries everything.
//
// THE CONVENTION every piece follows, so pieces from different sources snap together: metres, y up,
// the piece's FRONT faces +z, and a wall runs along its own +x with bay 0 on the left of someone
// standing outside looking at it. A wall at yaw 0 faces +z; at yaw PI/2 it faces +x.

/** How the leftover length is shared out. The bays keep their size in every mode; only space moves.
 *  `even`    equal gaps at both ends and between the bays (CSS space-evenly). The default.
 *  `around`  every bay centred in an equal slot, so an end gets half a gap (space-around).
 *  `between` the ends flush, all of it between the bays (space-between); a single bay is centred.
 *  `centre`  the bays packed at the minimum gap and the run centred: a ribbon window, a row of shops. */
export type BaySpread = "even" | "around" | "between" | "centre";

export interface BayOptions {
  /** Kept clear at EACH end before the first bay: a corner post, a pier. Not part of `spaces`. Default 0. */
  margin?: number;
  /** The least space between two bays. Default 0: bays may touch. */
  gap?: number;
  /** Fewest bays. Wins over fitting, and `fits` then says so: the bays overlap evenly, they never shrink. Default 0. */
  min?: number;
  /** Most bays: a ribbon window that should stop at eight panes. Default no limit. */
  max?: number;
  spread?: BaySpread;
  /** Where 0 is: "centre" runs -length/2..length/2 (a model centred on that axis), "start" runs 0..length. */
  origin?: "centre" | "start";
}

export interface BayLayout {
  length: number;
  /** The bay exactly as given. Never changed: that is the whole rule. */
  bay: number;
  count: number;
  /** The centre of each bay along the run, left to right. */
  centres: number[];
  /** Between two neighbouring bays: the minimum gap plus its share of the leftover. */
  gap: number;
  /** Centre to centre: bay + gap. */
  pitch: number;
  margin: number;
  /** From each end of the run to the nearest bay's edge, margin included. Half the length when no bay fits. */
  end: number;
  /** Where the run starts: -length/2, or 0 for origin "start". */
  from: number;
  /** What the whole bays left over, before it was shared out. Below zero only when `min` forced more bays than fit. */
  leftover: number;
  fits: boolean;
  /** Every stretch between the margins that is not a bay, ends included: where a filler goes. */
  spaces: Array<{ centre: number; width: number }>;
}

/**
 * THE MODULAR RULE, on one run: how many whole bays fit in `length`, and where each one goes.
 *
 * A bay is repeated, never stretched. Widen a facade from 5.8 m to 9.8 m and it gains window bays;
 * the ones it had stay exactly as wide as the piece that fills them. What does not make a whole bay
 * is the LEFTOVER, and it is shared out as space — evenly by default, see `BaySpread` — so the wall
 * reads as designed at every width instead of piling the remainder up at one end.
 *
 * The count is the largest n with n bays, n - 1 minimum gaps and two margins inside the length. The
 * floor carries a hair of slack, because 4.8 / 1.6 is 2.9999999999999996 in floating point (and
 * 9.6 / 1.6 is 5.999999999999999), so the obvious `Math.floor` quietly loses a bay on exactly the
 * lengths people type.
 */
export function bayLayout(length: number, bay: number, opts: BayOptions = {}): BayLayout {
  const L = Number.isFinite(length) && length > 0 ? length : 0;
  const b = Number.isFinite(bay) && bay > 0 ? bay : 0;
  const margin = Math.max(0, Number(opts.margin) || 0);
  const minGap = Math.max(0, Number(opts.gap) || 0);
  const lo = Math.max(0, Math.floor(Number(opts.min) || 0));
  const hiRaw = Number(opts.max);
  const hi = Number.isFinite(hiRaw) ? Math.max(lo, Math.floor(hiRaw)) : Infinity;
  const tol = 1e-9 * Math.max(1, L);
  const usable = L - 2 * margin;
  let n = 0;
  if (b > 0) {
    const ratio = (usable + minGap) / (b + minGap);
    n = ratio > 0 ? Math.floor(ratio + 1e-9 * Math.max(1, ratio)) : 0;
    n = Math.min(hi, Math.max(lo, n));
  }
  let leftover = n > 0 ? usable - n * b - (n - 1) * minGap : Math.max(0, usable);
  if (leftover < 0 && leftover > -tol) leftover = 0;      // the same float hair, the other way
  const fits = leftover >= 0;
  let endGap = 0;
  let gap = minGap;
  if (n > 0) {
    const spread = opts.spread || "even";
    if (leftover < 0 || spread === "between") {
      // Space-between — or forced to overlap by `min`, which is the same arithmetic with a negative
      // leftover: the ends stay on the margins and the difference goes between the bays.
      if (n > 1) gap = minGap + leftover / (n - 1);
      else endGap = leftover / 2;
    } else if (spread === "around") {
      endGap = leftover / n / 2;
      gap = minGap + leftover / n;
    } else if (spread === "centre") {
      endGap = leftover / 2;
    } else {
      endGap = leftover / (n + 1);
      gap = minGap + endGap;
    }
  }
  const from = opts.origin === "start" ? 0 : -L / 2;
  const centres: number[] = [];
  for (let i = 0; i < n; i++) centres.push(from + margin + endGap + b / 2 + i * (b + gap));
  const spaces: Array<{ centre: number; width: number }> = [];
  const space = (a: number, w: number) => { if (w > tol) spaces.push({ centre: a + w / 2, width: w }); };
  if (n > 0) {
    space(from + margin, centres[0] - b / 2 - (from + margin));
    for (let i = 0; i + 1 < n; i++) space(centres[i] + b / 2, centres[i + 1] - centres[i] - b);
    space(centres[n - 1] + b / 2, from + L - margin - (centres[n - 1] + b / 2));
  } else {
    space(from + margin, usable);
  }
  return {
    length: L, bay: b, count: n, centres, gap, pitch: b + gap, margin,
    end: n > 0 ? margin + endGap : L / 2, from, leftover, fits, spaces,
  };
}

export interface FacadeOptions extends BayOptions {
  /** The wall's length along its own x. */
  length: number;
  /** A bay's width: one number, or one per floor (shop fronts below, windows above). */
  bay: number | ((floor: number) => number);
  /** Storeys. Default 1. */
  floors?: number;
  /** Storey height: one number, or one per floor. Default 3. */
  floorHeight?: number | ((floor: number) => number);
  /** Any BayOptions, floor by floor: a packed shop floor under evenly spread windows. */
  perFloor?: (floor: number) => BayOptions | null | undefined;
  /** The centre of the wall's foot (its start, with origin "start"). Default the origin. */
  at?: V3;
  /** Which way the wall faces: 0 faces +z and runs along +x; PI/2 faces +x. Default 0. */
  yaw?: number;
  /** facade() only: the group's name, and the prefix of every piece set in it. */
  name?: string;
  /** facade() only: handed to repeat() for every piece. */
  instance?: RepeatOptions["instance"];
  /** facade() and facadePlan() only: a featureless piece, stretched to close every stretch of wall no
   *  piece covers. The ONE thing ever scaled, so give it nothing to distort: a plain wall strip. */
  fill?: string;
}

export interface FacadeSlot {
  floor: number;
  bay: number;
  /** Bays on this floor. */
  count: number;
  floors: number;
  first: boolean;
  last: boolean;
  top: boolean;
  /** Along the wall, from its centre (or its start, with origin "start"). */
  x: number;
  /** The floor's foot, above the wall's. */
  y: number;
  width: number;
  height: number;
  /** World position of the bay's foot on the wall line. With `yaw`, a placement repeat() takes as it is. */
  position: V3;
  yaw: number;
}

export interface FacadeFloor extends BayLayout { floor: number; y: number; height: number }

export interface FacadeLayout {
  floors: FacadeFloor[];
  slots: FacadeSlot[];
  /** Every storey added up. */
  height: number;
}

/** A point given in a wall's own frame — x along it, y up, z out of it — in the world. */
function wallPoint(at: V3, yaw: number, x: number, y: number, z: number): V3 {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  return [at[0] + x * c + z * s, at[1] + y, at[2] - x * s + z * c];
}

/**
 * The modular rule on every floor of one wall: a slot for each (floor, bay), with its world
 * position and the wall's yaw, ready to take a piece.
 *
 * Floors may differ — `bay` and `floorHeight` take a function of the floor, `perFloor` any other
 * BayOptions — because a real facade is a floor of wide shop bays under a stack of narrow window
 * bays, and the two do not have to line up.
 */
export function facadeSlots(opts: FacadeOptions): FacadeLayout {
  const floors = Math.max(0, Math.floor(Number(opts.floors ?? 1)) || 0);
  const at: V3 = Array.isArray(opts.at) && opts.at.length === 3 ? opts.at : [0, 0, 0];
  const yaw = Number(opts.yaw) || 0;
  const out: FacadeLayout = { floors: [], slots: [], height: 0 };
  let y = 0;
  for (let f = 0; f < floors; f++) {
    const bay = Number(typeof opts.bay === "function" ? opts.bay(f) : opts.bay);
    const hRaw = typeof opts.floorHeight === "function" ? opts.floorHeight(f) : opts.floorHeight;
    const h = Math.max(0, Number(hRaw ?? 3) || 0);
    const own = (opts.perFloor && opts.perFloor(f)) || {};
    const lay = bayLayout(opts.length, bay, {
      margin: opts.margin, gap: opts.gap, min: opts.min, max: opts.max, spread: opts.spread, origin: opts.origin, ...own,
    });
    out.floors.push({ ...lay, floor: f, y, height: h });
    lay.centres.forEach((x, i) => out.slots.push({
      floor: f, bay: i, count: lay.count, floors, first: i === 0, last: i === lay.count - 1, top: f === floors - 1,
      x, y, width: lay.bay, height: h, position: wallPoint(at, yaw, x, y, 0), yaw,
    }));
    y += h;
  }
  out.height = y;
  return out;
}

/** What `pick` may answer for a slot: a piece's name, or this, or nothing to leave the bay empty. */
export interface FacadePick {
  piece: string;
  /** Bays this piece covers, from this one rightwards: a double shop front is 2. It is centred on
   *  them, and the slots it covers are not asked about. Clipped at the end of the floor. */
  span?: number;
  /** Mirror it in its own x: the same door, hinged on the other side. */
  flip?: boolean;
  /** A nudge in the wall's frame — x along it, y up, z out of it. A piece with any z stands off the
   *  wall (a street prop) and does not count as covering it. */
  offset?: V3;
  /** An extra turn about y, on top of the wall's. */
  yaw?: number;
  scale?: number | V3;
}

/** Asked once per slot, left to right, floor by floor. */
export type FacadePicker = (slot: FacadeSlot) => FacadePick | string | null | undefined | false;

export interface FacadePlan {
  layout: FacadeLayout;
  /** Piece name to where every copy goes, in slot order: ready for repeat(). */
  placements: Record<string, Placement[]>;
  /** Names `pick` asked for that the kit does not have. Nothing is placed for them. */
  missing: string[];
  /** Slots `pick` left empty. */
  empty: number;
  /** Copies placed, fillers included. */
  placed: number;
}

/**
 * The slots of a facade with a piece in each: what facade() builds, as plain data.
 *
 * `widthOf(name)` says how wide a piece is, and `undefined` means the kit has no such piece — which
 * puts it in `missing` instead of throwing half way up a building. Without it every name is taken,
 * a piece is as wide as the bays it spans, and a filler is taken to be one metre wide.
 *
 * With `opts.fill`, every stretch of a floor between its margins that no piece covers gets one copy
 * of the filler, scaled to fit: empty slots, the shared-out leftover and the gaps beside a piece
 * narrower than its bay alike.
 */
export function facadePlan(opts: FacadeOptions, pick: FacadePicker,
                           widthOf?: (piece: string) => number | undefined): FacadePlan {
  const layout = facadeSlots(opts);
  const at: V3 = Array.isArray(opts.at) && opts.at.length === 3 ? opts.at : [0, 0, 0];
  const yaw = Number(opts.yaw) || 0;
  const placements: Record<string, Placement[]> = {};
  const missing = new Set<string>();
  let empty = 0;
  let placed = 0;
  const put = (name: string, p: Placement) => { (placements[name] || (placements[name] = [])).push(p); placed++; };
  for (const fl of layout.floors) {
    const slots = layout.slots.filter((s) => s.floor === fl.floor);
    const covered: Array<[number, number]> = [];
    for (let i = 0; i < slots.length; i++) {
      const s = slots[i];
      const got = pick(s);
      const want: FacadePick | null = typeof got === "string" ? (got ? { piece: got } : null)
        : got && typeof got === "object" && typeof got.piece === "string" && got.piece ? got : null;
      if (!want) { empty++; continue; }
      const span = Math.max(1, Math.min(slots.length - i, Math.floor(Number(want.span) || 1)));
      const last = slots[i + span - 1];
      i += span - 1;                                  // the slots it covers are not asked about
      const width = widthOf ? widthOf(want.piece) : undefined;
      if (widthOf && width === undefined) { missing.add(want.piece); continue; }
      const off: V3 = Array.isArray(want.offset) && want.offset.length === 3 ? want.offset : [0, 0, 0];
      const x = (s.x + last.x) / 2 + off[0];
      put(want.piece, {
        position: wallPoint(at, yaw, x, s.y + off[1], off[2]),
        yaw: yaw + (Number(want.yaw) || 0),
        ...(want.flip ? { flip: true } : {}),
        ...(want.scale !== undefined ? { scale: want.scale } : {}),
      });
      if (!off[2]) {
        const sx = typeof want.scale === "number" ? Math.abs(want.scale)
          : Array.isArray(want.scale) ? Math.abs(want.scale[0]) : 1;
        const w = (width ?? (last.x - s.x + fl.bay)) * sx;
        covered.push([x - w / 2, x + w / 2]);
      }
    }
    if (!opts.fill) continue;
    const fw = widthOf ? widthOf(opts.fill) : 1;
    if (fw === undefined) { missing.add(opts.fill); continue; }
    if (!(fw > 0)) continue;
    const a = fl.from + fl.margin, b = fl.from + fl.length - fl.margin;
    covered.sort((p, q) => p[0] - q[0]);
    let cur = a;
    const free: Array<[number, number]> = [];
    for (const [lo, hi] of covered) {
      if (lo > cur) free.push([cur, Math.min(lo, b)]);
      cur = Math.max(cur, hi);
      if (cur >= b) break;
    }
    if (cur < b) free.push([cur, b]);
    for (const [lo, hi] of free) {
      if (hi - lo <= 1e-3) continue;
      put(opts.fill, { position: wallPoint(at, yaw, (lo + hi) / 2, fl.y, 0), yaw, scale: [(hi - lo) / fw, 1, 1] });
    }
  }
  return { layout, placements, missing: [...missing], empty, placed };
}

export interface PerimeterOptions {
  /** Kept clear at both ends of every side, for the corner piece. Default 0. */
  corner?: number;
  /** Move every side in (+) or out (-) of the rectangle given. Default 0. */
  inset?: number;
  gap?: number;
  spread?: BaySpread;
  /** The rectangle's centre; its y is the height of every slot. Default the origin. */
  at?: V3;
}

export interface PerimeterSlot {
  /** 0 front (+z), 1 right (+x), 2 back (-z), 3 left (-x). */
  side: number;
  index: number;
  count: number;
  corner: boolean;
  position: V3;
  /** Facing outward: a piece whose front is +z faces away from the rectangle. */
  yaw: number;
  /** 0..1 along its side, left to right seen from outside. A corner is 1: it ends its side. */
  t: number;
}

export interface PerimeterLayout {
  edges: PerimeterSlot[];
  corners: PerimeterSlot[];
  /** Each side's own bay layout: front, right, back, left. */
  sides: BayLayout[];
}

const SIDE_YAW = [0, Math.PI / 2, Math.PI, -Math.PI / 2];

/**
 * The modular rule round a rectangle: dentils under a cornice, balusters, merlons on a parapet,
 * posts at the corners. Each side is a bayLayout of its own, so a longer side gets more pieces and
 * none is stretched, and every slot faces OUTWARD.
 *
 * Sides run front, right, back, left — clockwise seen from above — and corner k is where side k
 * ends, so a corner piece modelled for the front-right corner turns correctly onto the other three.
 */
export function perimeterSlots(width: number, depth: number, spacing: number, opts: PerimeterOptions = {}): PerimeterLayout {
  const inset = Number(opts.inset) || 0;
  const w = Math.max(0, (Number(width) || 0) - 2 * inset);
  const d = Math.max(0, (Number(depth) || 0) - 2 * inset);
  const at: V3 = Array.isArray(opts.at) && opts.at.length === 3 ? opts.at : [0, 0, 0];
  const corner = Math.max(0, Number(opts.corner) || 0);
  const out: PerimeterLayout = { edges: [], corners: [], sides: [] };
  for (let s = 0; s < 4; s++) {
    const len = s % 2 === 0 ? w : d;
    const out2 = s % 2 === 0 ? d / 2 : w / 2;          // from the centre out to this side
    const yaw = SIDE_YAW[s];
    const lay = bayLayout(len, spacing, { margin: corner, gap: opts.gap, spread: opts.spread });
    out.sides.push(lay);
    lay.centres.forEach((u, i) => out.edges.push({
      side: s, index: i, count: lay.count, corner: false,
      position: wallPoint(at, yaw, u, 0, out2), yaw, t: len > 0 ? (u + len / 2) / len : 0.5,
    }));
    out.corners.push({
      side: s, index: 0, count: 1, corner: true, position: wallPoint(at, yaw, len / 2, 0, out2), yaw, t: 1,
    });
  }
  return out;
}

/** Where one copy goes. A facade or perimeter slot already is one; anything else can be written by hand. */
export interface Placement {
  position?: V3;
  /** Turn about y, radians. Applied before `rotation`. */
  yaw?: number;
  /** Euler XYZ in radians, after the yaw: the order three uses. */
  rotation?: V3;
  scale?: number | V3;
  /** Mirror in the piece's own x, before anything else. */
  flip?: boolean;
}

/** A Placement, a bare position [x, y, z], or a column-major 4x4: an array, a three Matrix4, a pc.Mat4. */
export type PlacementLike = Placement | V3 | ArrayLike<number> | { elements: ArrayLike<number> } | { data: ArrayLike<number> };

const MIRROR_X = [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const rotXm = (a: number) => { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]; };
const rotYm = (a: number) => { const c = Math.cos(a), s = Math.sin(a); return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]; };
const rotZm = (a: number) => { const c = Math.cos(a), s = Math.sin(a); return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; };

/** A placement as the column-major 4x4 both engines store: translate, yaw, rotation, scale, mirror — read right to left. */
export function placementMatrix(p: PlacementLike): number[] {
  const any = p as any;
  const m16 = any && typeof any.length === "number" && any.length >= 16 ? any
    : any?.elements?.length >= 16 ? any.elements : any?.data?.length >= 16 ? any.data : null;
  if (m16) return Array.from({ length: 16 }, (_, i) => Number(m16[i]) || 0);
  const q: Placement = any && typeof any.length === "number"
    ? { position: [Number(any[0]) || 0, Number(any[1]) || 0, Number(any[2]) || 0] }
    : (any || {});
  const pos = Array.isArray(q.position) ? q.position : [0, 0, 0];
  let m = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, Number(pos[0]) || 0, Number(pos[1]) || 0, Number(pos[2]) || 0, 1];
  if (q.yaw) m = mat4Mul(m, rotYm(Number(q.yaw)));
  if (Array.isArray(q.rotation)) {
    const r = q.rotation;
    m = mat4Mul(m, mat4Mul(mat4Mul(rotXm(Number(r[0]) || 0), rotYm(Number(r[1]) || 0)), rotZm(Number(r[2]) || 0)));
  }
  if (q.scale !== undefined && q.scale !== null) {
    const s = typeof q.scale === "number" ? [q.scale, q.scale, q.scale] : q.scale;
    m = mat4Mul(m, [Number(s[0]), 0, 0, 0, 0, Number(s[1]), 0, 0, 0, 0, Number(s[2]), 0, 0, 0, 0, 1]);
  }
  if (q.flip) m = mat4Mul(m, MIRROR_X);
  return m;
}

/** A column-major 4x4 back into position, rotation (a quaternion x, y, z, w) and scale. A mirror
 *  comes out as a negative x scale, which is how three decomposes one too. */
export function mat4Decompose(m: ArrayLike<number>): { position: V3; quaternion: [number, number, number, number]; scale: V3 } {
  let sx = Math.hypot(m[0], m[1], m[2]);
  const sy = Math.hypot(m[4], m[5], m[6]);
  const sz = Math.hypot(m[8], m[9], m[10]);
  if (det3(m) < 0) sx = -sx;
  const ix = sx ? 1 / sx : 0, iy = sy ? 1 / sy : 0, iz = sz ? 1 / sz : 0;
  // The rotation, row then column, with the scale divided out of each column.
  const r00 = m[0] * ix, r10 = m[1] * ix, r20 = m[2] * ix;
  const r01 = m[4] * iy, r11 = m[5] * iy, r21 = m[6] * iy;
  const r02 = m[8] * iz, r12 = m[9] * iz, r22 = m[10] * iz;
  let x: number, y: number, z: number, w: number;
  const tr = r00 + r11 + r22;
  if (tr > 0) {
    const s = 0.5 / Math.sqrt(tr + 1);
    w = 0.25 / s; x = (r21 - r12) * s; y = (r02 - r20) * s; z = (r10 - r01) * s;
  } else if (r00 > r11 && r00 > r22) {
    const s = 2 * Math.sqrt(1 + r00 - r11 - r22);
    w = (r21 - r12) / s; x = 0.25 * s; y = (r01 + r10) / s; z = (r02 + r20) / s;
  } else if (r11 > r22) {
    const s = 2 * Math.sqrt(1 + r11 - r00 - r22);
    w = (r02 - r20) / s; x = (r01 + r10) / s; y = 0.25 * s; z = (r12 + r21) / s;
  } else {
    const s = 2 * Math.sqrt(1 + r22 - r00 - r11);
    w = (r10 - r01) / s; x = (r02 + r20) / s; y = (r12 + r21) / s; z = 0.25 * s;
  }
  return { position: [m[12], m[13], m[14]], quaternion: [x, y, z, w], scale: [sx, sy, sz] };
}

/** The determinant of the rotation-and-scale part: negative means the matrix mirrors. */
function det3(m: ArrayLike<number>): number {
  return m[0] * (m[5] * m[10] - m[6] * m[9]) - m[4] * (m[1] * m[10] - m[2] * m[9]) + m[8] * (m[1] * m[6] - m[2] * m[5]);
}

/** Where a piece's pivot goes, as fractions of its box per axis (0 min, 0.5 middle, 1 max; null keeps
 *  the modeller's pivot on that axis), or a name:
 *  "bottom" [0.5, 0, 0.5]    stands on the floor, centred: props, posts, furniture.
 *  "back"   [0.5, 0, 0]      against a wall at z = 0, facing +z, foot on the floor: doors, shop fronts.
 *  "wall"   [0.5, null, 0]   against a wall, at the height it was modelled at: a window keeps its sill
 *                            height above the storey's floor, which "back" would drop to the ground.
 *  "corner" [0, 0, 0]        floor tiles on a grid.   "centre" [0.5, 0.5, 0.5].
 *  "origin"                  leaves the pivot where the modeller put it. */
export type KitAnchor = "bottom" | "back" | "wall" | "corner" | "centre" | "origin" | [number | null, number | null, number | null];

export interface KitOptions {
  /** Default "bottom". */
  anchor?: KitAnchor;
  /** By piece name, for the pieces that need another anchor than the rest. */
  anchors?: Record<string, KitAnchor>;
  /** The kit's module in metres. Every piece is measured in whole cells of it, and one off the grid by more than 1% of a cell along x is named in `notes`. */
  grid?: number;
  /** Which nodes are pieces. Default: the named children of the source. A RegExp, a list of names or a test searches the whole tree instead, and a match's own children belong to it. */
  match?: RegExp | string[] | ((name: string) => boolean);
}

export interface KitPiece {
  name: string;
  /** The piece with its pivot on the anchor and its front facing +z. Clone it, or hand it to repeat(). */
  object: any;
  /** Its box once the pivot has moved, in metres. */
  min: V3;
  max: V3;
  size: V3;
  /** Whole grid cells along each axis, when the kit has a grid. */
  cells: V3 | null;
  /** Axes at least one cell long whose size is not a whole number of cells. */
  offGrid: Array<"x" | "y" | "z">;
  /** One plain mesh and nothing else: repeat() draws every copy of it as ONE InstancedMesh. */
  single: boolean;
  triangles: number;
  /** Every mesh, with its column-major matrix in the piece's own frame: what instancing draws. */
  meshes: Array<{ mesh: any; matrix: number[]; instanceable: boolean }>;
}

export interface Kit {
  pieces: Record<string, KitPiece>;
  names: string[];
  grid: number;
  /** The piece. Throws, naming every piece the kit has, when it is not there: a typo should say so. */
  get(name: string): KitPiece;
  has(name: string): boolean;
  /** Plain sentences: pieces off the grid, pieces with nothing to draw, names used twice, nodes with no name. */
  notes: string[];
}

export interface RepeatOptions {
  /** The group's name and the stem of its children's. Default the piece's name. */
  name?: string;
  /** "auto" instances a single-mesh piece and clones anything else; "always" instances each mesh of a multi-mesh piece too; "never" clones. Default "auto". */
  instance?: "auto" | "always" | "never";
  /** Fewer copies than this are cloned, not instanced. Default 2. */
  minInstances?: number;
}

/** How the kit ops touch an engine. Everything else about a kit is the same on both. */
interface KitEngine {
  /** Whether `instance` can draw many copies in one node. PlayCanvas clones, for now. */
  canInstance: boolean;
  group(name: string): any;
  add(parent: any, child: any): void;
  clone(o: any): any;
  /** `dst` takes the WORLD rotation and scale `src` had in its own scene, and sits at the origin. */
  pose(src: any, dst: any): void;
  move(o: any, x: number, y: number, z: number): void;
  /** A column-major 4x4 as a node's local transform. */
  place(o: any, m: number[]): void;
  rename(o: any, name: string): void;
  /** Every visible mesh under `root`: its matrix and box in root's frame, and whether it can be instanced. */
  measure(root: any): KitMeasure;
  /** One node drawing a copy of `mesh` at every matrix. */
  instance(mesh: any, mats: number[][], name: string, mirrored: boolean): any;
}

interface KitMeasure {
  min: V3;
  max: V3;
  meshes: Array<{ mesh: any; matrix: number[]; triangles: number; instanceable: boolean }>;
  /** Everything that draws: meshes, and in three points, lines and sprites too. */
  renderables: number;
}

/** An anchor per axis: a fraction of the box, or null to keep the modeller's pivot on that axis. */
type AnchorAxes = [number | null, number | null, number | null];

const KIT_ANCHORS: Record<string, AnchorAxes | null> = {
  bottom: [0.5, 0, 0.5], back: [0.5, 0, 0], wall: [0.5, null, 0], corner: [0, 0, 0],
  centre: [0.5, 0.5, 0.5], center: [0.5, 0.5, 0.5], origin: null,
};

function kitAnchor(a: KitAnchor | undefined): AnchorAxes | null {
  if (Array.isArray(a) && a.length === 3) {
    const axis = (v: unknown) => (v === null || v === undefined ? null : Number(v) || 0);
    return [axis(a[0]), axis(a[1]), axis(a[2])];
  }
  const k = typeof a === "string" ? a : "bottom";
  return k in KIT_ANCHORS ? KIT_ANCHORS[k] : KIT_ANCHORS.bottom;
}

/** Hang a note on a node where both engines keep one. */
function tagNode(o: any, key: string, value: unknown) {
  (o.userData || (o.userData = {}))[key] = value;
}

/** The (name, node) pairs a kit is made of: a record's entries, a node's named children, or every
 *  node in the tree whose name passes `match` — and not its descendants, which belong to it. */
function kitEntries(source: any, match: KitOptions["match"], notes: string[]): Array<[string, any]> {
  if (!source || typeof source !== "object") return [];
  const proto = Object.getPrototypeOf(source);
  if (proto === Object.prototype || proto === null) {
    return Object.entries(source).filter(([k, v]) => k && v && typeof v === "object") as Array<[string, any]>;
  }
  const test: ((n: string) => boolean) | null = match instanceof RegExp
    // A /g RegExp remembers where it stopped, so without the reset every other name would fail.
    ? ((re: RegExp) => (n: string) => { re.lastIndex = 0; return re.test(n); })(match)
    : Array.isArray(match) ? ((set: Set<string>) => (n: string) => set.has(n))(new Set(match))
    : typeof match === "function" ? match : null;
  const out: Array<[string, any]> = [];
  if (!test) {
    let unnamed = 0;
    for (const c of source.children || []) { if (c?.name) out.push([c.name, c]); else unnamed++; }
    if (unnamed) notes.push(unnamed + " child node(s) have no name, so they are not pieces: a kit finds its pieces by name.");
    return out;
  }
  const walk = (o: any) => {
    for (const c of o?.children || []) { if (c?.name && test(c.name)) out.push([c.name, c]); else walk(c); }
  };
  walk(source);
  return out;
}

/** One piece: cloned out of its scene, turned and scaled as it was there, measured, and moved so its
 *  anchor is the origin. Null when there is nothing under it to draw. */
function pieceWith(E: KitEngine, name: string, node: any, anchor: AnchorAxes | null, grid: number): KitPiece | null {
  const inst = E.clone(node);
  // Where the piece stood in the file means nothing — pack files lay their pieces out side by side —
  // but how it was turned and scaled does: a Z-up exporter's root rotation lives on an ancestor.
  E.pose(node, inst);
  const holder = E.group(name);
  E.add(holder, inst);
  let m = E.measure(holder);
  if (!m.meshes.length) return null;
  if (anchor) {
    const A = [0, 1, 2].map((k) => {
      const f = anchor[k];
      return f === null ? 0 : m.min[k] + f * (m.max[k] - m.min[k]);
    });
    E.move(inst, -A[0], -A[1], -A[2]);
    m = E.measure(holder);
  }
  const size: V3 = [m.max[0] - m.min[0], m.max[1] - m.min[1], m.max[2] - m.min[2]];
  let cells: V3 | null = null;
  const offGrid: Array<"x" | "y" | "z"> = [];
  if (grid > 0) {
    const tol = Math.max(1e-3, grid * 0.01);
    const c: V3 = [Math.round(size[0] / grid), Math.round(size[1] / grid), Math.round(size[2] / grid)];
    cells = c;
    (["x", "y", "z"] as const).forEach((ax, k) => {
      if (size[k] >= grid - tol && Math.abs(size[k] - c[k] * grid) > tol) offGrid.push(ax);
    });
  }
  return {
    name, object: holder, min: m.min, max: m.max, size, cells, offGrid,
    single: m.meshes.length === 1 && m.renderables === 1 && m.meshes[0].instanceable,
    triangles: m.meshes.reduce((a, e) => a + e.triangles, 0),
    meshes: m.meshes.map((e) => ({ mesh: e.mesh, matrix: e.matrix, instanceable: e.instanceable })),
  };
}

/**
 * A registry of named pieces: the parts of a GLB pack (`kit(gltf.scene)`), or pieces built in code
 * (`kit({ wall, window, door })`), each measured and with its pivot on the same anchor, so that
 * placing any of them at a slot means the same thing.
 *
 * That last part is the one that bites. A pack's pieces arrive with pivots wherever their modeller
 * left them — the centre, a corner, the middle of the floor — and a facade built from them has every
 * other window floating or sunk. Normalising once, here, is what lets the layout code stay simple.
 */
function kitWith(E: KitEngine, source: any, opts: KitOptions = {}): Kit {
  const notes: string[] = [];
  const grid = Math.max(0, Number(opts.grid) || 0);
  const pieces: Record<string, KitPiece> = {};
  const names: string[] = [];
  const seen = new Set<string>();
  const twice = new Set<string>();
  for (const [name, node] of kitEntries(source, opts.match, notes)) {
    if (seen.has(name)) { twice.add(name); continue; }
    seen.add(name);
    const p = pieceWith(E, name, node, kitAnchor(opts.anchors?.[name] ?? opts.anchor), grid);
    if (!p) { notes.push(name + " has no mesh under it, so there is nothing to place: skipped."); continue; }
    pieces[name] = p;
    names.push(name);
    if (p.offGrid.includes("x") && p.cells) {
      const err = p.size[0] - p.cells[0] * grid;
      notes.push(name + " is " + p.size[0].toFixed(3) + " wide: " + (err > 0 ? "+" : "") + err.toFixed(3)
        + " off the " + grid + " grid, so a row of them drifts that much per piece.");
    }
  }
  for (const n of twice) notes.push('"' + n + '" names more than one node; the first is the piece and the rest are ignored. Name them apart.');
  if (!names.length) notes.push("No pieces: nothing named in the source has a mesh under it" + (opts.match ? " and passes `match`." : "."));
  const has = (n: string) => Object.prototype.hasOwnProperty.call(pieces, n);
  return {
    pieces, names, grid, notes, has,
    get(n: string) {
      if (has(n)) return pieces[n];
      throw new Error('kit: no piece "' + n + '". The kit has: ' + (names.join(", ") || "nothing"));
    },
  };
}

/** A kit piece as it is, or any node wrapped as one with its own origin as the pivot. */
function asPiece(E: KitEngine, piece: any, name?: string): KitPiece {
  if (piece && Array.isArray(piece.meshes) && piece.object) return piece as KitPiece;
  if (!piece || typeof piece !== "object") {
    throw new Error("repeat: no piece to repeat. kit.get(name) returns one, and kit.names lists them.");
  }
  const p = pieceWith(E, name || piece.name || "piece", piece, null, 0);
  if (!p) throw new Error("repeat: " + (name || piece.name || "the object") + " has no mesh to repeat");
  return p;
}

/**
 * Copies of one piece at every placement, as one group.
 *
 * A piece that is ONE mesh becomes one InstancedMesh: a hundred dentils are one draw call. Anything
 * else is cloned, and clones still share their geometry and materials, so a hundred copies of a
 * multi-part window cost the memory of one. `instance: "always"` instances each mesh of a
 * multi-part piece as well: one draw per part instead of one per copy.
 */
function repeatWith(E: KitEngine, piece: any, at: PlacementLike[], opts: RepeatOptions = {}): any {
  const p = asPiece(E, piece, opts.name);
  const name = opts.name || p.name;
  const mats = (Array.isArray(at) ? at : []).map(placementMatrix);
  const group = E.group(name);
  const mode = opts.instance || "auto";
  const minN = Math.max(1, Math.floor(Number(opts.minInstances ?? 2)) || 1);
  const all = p.meshes.length > 0 && p.meshes.every((e) => e.instanceable);
  const instanced = E.canInstance && mode !== "never" && mats.length >= minN && all && (mode === "always" || p.single);
  let draws = 0;
  if (instanced) {
    // A mirrored copy cannot share a draw with the rest: the engine picks the winding per OBJECT. So
    // the mirrored copies go in a node of their own that is itself mirrored, and each of its copies
    // is mirrored back — net, exactly the placement asked for, with the faces the right way out.
    const keep: number[][] = [];
    const flip: number[][] = [];
    for (const m of mats) (det3(m) < 0 ? flip : keep).push(m);
    p.meshes.forEach((e, k) => {
      const stem = name + (p.meshes.length > 1 ? "_" + (e.mesh?.name || "part" + k) : "_inst");
      for (const [list, mirrored] of [[keep, false], [flip, true]] as Array<[number[][], boolean]>) {
        if (!list.length) continue;
        const world = list.map((m) => (mirrored ? mat4Mul(MIRROR_X, mat4Mul(m, e.matrix)) : mat4Mul(m, e.matrix)));
        E.add(group, E.instance(e.mesh, world, stem + (mirrored ? "_mirrored" : ""), mirrored));
        draws++;
      }
    });
  } else {
    mats.forEach((m, i) => {
      const c = E.clone(p.object);
      E.rename(c, name + "_" + i);
      E.place(c, m);
      E.add(group, c);
    });
    draws = mats.length * p.meshes.length;
  }
  tagNode(group, "repeat", { piece: p.name, count: mats.length, instanced, draws, triangles: mats.length * p.triangles });
  return group;
}

/** A whole wall: facadePlan's placements, each piece drawn by repeat(). */
function facadeWith(E: KitEngine, kit: Kit, opts: FacadeOptions, pick: FacadePicker): any {
  if (!kit || typeof kit.has !== "function") throw new Error("facade: the first argument is a kit — ops.kit(pieces)");
  if (typeof pick !== "function") throw new Error("facade: pick(slot) names the piece for each slot, and there is none");
  const plan = facadePlan(opts, pick, (n) => (kit.has(n) ? kit.pieces[n].size[0] : undefined));
  const group = E.group(opts.name || "facade");
  const prefix = opts.name ? opts.name + "_" : "";
  const placed: Record<string, number> = {};
  for (const [piece, list] of Object.entries(plan.placements)) {
    placed[piece] = list.length;
    E.add(group, repeatWith(E, kit.get(piece), list, { name: prefix + piece, instance: opts.instance }));
  }
  tagNode(group, "facade", {
    floors: plan.layout.floors.map((f) => ({
      floor: f.floor, count: f.count, bay: f.bay, pitch: f.pitch, gap: f.gap, leftover: f.leftover, fits: f.fits,
    })),
    placed, empty: plan.empty, missing: plan.missing, height: plan.layout.height,
  });
  return group;
}

/** The kit ops on three: Group, clone, decompose, InstancedMesh. */
function threeKitEngine(T: any): KitEngine {
  return {
    canInstance: true,
    group(name) { const g = new T.Group(); g.name = name; return g; },
    add(parent, child) { parent.add(child); },
    clone(o) { return o.clone(true); },
    pose(src, dst) {
      src.updateWorldMatrix?.(true, false);
      (src.matrixWorld || src.matrix).decompose(dst.position, dst.quaternion, dst.scale);
      dst.position.set(0, 0, 0);
    },
    move(o, x, y, z) { o.position.set(x, y, z); },
    place(o, m) { o.matrix.fromArray(m); o.matrix.decompose(o.position, o.quaternion, o.scale); },
    rename(o, name) { o.name = name; },
    measure(root) {
      root.updateMatrixWorld(true);
      const min: V3 = [Infinity, Infinity, Infinity];
      const max: V3 = [-Infinity, -Infinity, -Infinity];
      const meshes: KitMeasure["meshes"] = [];
      let renderables = 0;
      root.traverseVisible((o: any) => {
        if (o.isMesh || o.isPoints || o.isLine || o.isSprite) renderables++;
        const g = o.geometry;
        if (!o.isMesh || !g?.attributes?.position) return;
        if (!g.boundingBox) g.computeBoundingBox();
        const bb = g.boundingBox;
        const e = o.matrixWorld.elements;
        for (let i = 0; i < 8; i++) {
          const x = i & 1 ? bb.max.x : bb.min.x, y = i & 2 ? bb.max.y : bb.min.y, z = i & 4 ? bb.max.z : bb.min.z;
          const w: V3 = [e[0] * x + e[4] * y + e[8] * z + e[12], e[1] * x + e[5] * y + e[9] * z + e[13], e[2] * x + e[6] * y + e[10] * z + e[14]];
          for (let k = 0; k < 3; k++) { if (w[k] < min[k]) min[k] = w[k]; if (w[k] > max[k]) max[k] = w[k]; }
        }
        meshes.push({
          mesh: o, matrix: Array.from(e) as number[],
          triangles: (g.index ? g.index.count : g.attributes.position.count) / 3,
          // Instancing has no bones and no morph targets, and an InstancedMesh is already instanced.
          instanceable: !o.isInstancedMesh && !o.isSkinnedMesh && !(o.morphTargetInfluences && o.morphTargetInfluences.length),
        });
      });
      return { min, max, meshes, renderables };
    },
    instance(mesh, mats, name, mirrored) {
      const im = new T.InstancedMesh(mesh.geometry, mesh.material, mats.length);
      im.name = name;
      const M = new T.Matrix4();
      mats.forEach((m, i) => im.setMatrixAt(i, M.fromArray(m)));
      im.instanceMatrix.needsUpdate = true;
      if (mirrored) im.scale.x = -1;
      im.castShadow = !!mesh.castShadow;
      im.receiveShadow = !!mesh.receiveShadow;
      im.renderOrder = mesh.renderOrder || 0;
      // So culling and framing see every copy, not just the one piece standing at the origin.
      im.computeBoundingBox?.();
      im.computeBoundingSphere?.();
      return im;
    },
  };
}

/** The kit ops on PlayCanvas entities. Copies are clones: a render component shares its mesh, so a
 *  hundred cost the memory of one, and the engine's BatchManager can merge the static ones. */
function pcKitEngine(pc: any): KitEngine {
  const setTRS = (o: any, d: ReturnType<typeof mat4Decompose>, at: V3) => {
    o.setLocalPosition(at[0], at[1], at[2]);
    o.setLocalRotation(d.quaternion[0], d.quaternion[1], d.quaternion[2], d.quaternion[3]);
    o.setLocalScale(d.scale[0], d.scale[1], d.scale[2]);
  };
  return {
    canInstance: false,
    group(name) { return new pc.Entity(name); },
    add(parent, child) { parent.addChild(child); },
    clone(o) { return o.clone(); },
    pose(src, dst) { setTRS(dst, mat4Decompose(src.getWorldTransform().data), [0, 0, 0]); },
    move(o, x, y, z) { o.setLocalPosition(x, y, z); },
    place(o, m) { const d = mat4Decompose(m); setTRS(o, d, d.position); },
    rename(o, name) { o.name = name; },
    measure(root) {
      const min: V3 = [Infinity, Infinity, Infinity];
      const max: V3 = [-Infinity, -Infinity, -Infinity];
      const meshes: KitMeasure["meshes"] = [];
      let renderables = 0;
      const inv = mat4Invert(Array.from(root.getWorldTransform().data as ArrayLike<number>))
        || [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
      const visit = (n: any) => {
        if (n.enabled === false) return;
        for (const mi of n.render?.meshInstances || n.model?.meshInstances || []) {
          renderables++;
          const rel = mat4Mul(inv, Array.from((mi.node || n).getWorldTransform().data as ArrayLike<number>));
          const bb = mi.mesh?.aabb;
          if (bb?.center && bb?.halfExtents) {
            const c = bb.center, h = bb.halfExtents;
            for (let i = 0; i < 8; i++) {
              const x = c.x + (i & 1 ? h.x : -h.x), y = c.y + (i & 2 ? h.y : -h.y), z = c.z + (i & 4 ? h.z : -h.z);
              const w: V3 = [rel[0] * x + rel[4] * y + rel[8] * z + rel[12], rel[1] * x + rel[5] * y + rel[9] * z + rel[13], rel[2] * x + rel[6] * y + rel[10] * z + rel[14]];
              for (let k = 0; k < 3; k++) { if (w[k] < min[k]) min[k] = w[k]; if (w[k] > max[k]) max[k] = w[k]; }
            }
          }
          meshes.push({ mesh: mi, matrix: rel, triangles: (mi.mesh?.primitive?.[0]?.count || 0) / 3, instanceable: false });
        }
        for (const c of n.children || []) visit(c);
      };
      visit(root);
      return { min, max, meshes, renderables };
    },
    instance() { throw new Error("repeat: PlayCanvas copies are clones; instancing is three-only for now"); },
  };
}

// ------------------------------------------------------------------ ground
/** The terrain engine: heightfield, nine brushes, four layers, scatter, and the code
 *  emitter that gets ground out of the editor and into a game. Re-exported here so the
 *  one bundle a caller already imports carries it too - `ops.terrain.applyBrush(...)`.
 *  It imports nothing from this file: it has its own rng and noise so the module it
 *  emits can stand alone. */
export * as terrain from "./terrain";
