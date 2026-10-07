// One UV atlas for a whole asset: every part unwrapped, and ALL their islands packed into one
// 0..1 square, so a figure of twenty parts carries one base-colour, one ORM and one normal map
// under one material. This is Blender's "Smart UV Project on all selected objects, scale islands
// per object, Pack Islands" in plain arrays.
//
// Per mesh, the islands are Blender's: a small set of projection directions is chosen so every
// face is within `angle` degrees of one (the largest faces first, each direction refined to the
// area-weighted mean of the faces it takes), each face goes to the direction it faces best, and
// the connected faces sharing a direction form an island, projected flat along it. Every face of
// an island faces its projection direction by less than `angle` (< 90), so an island never folds.
// Each island is turned to its tightest box (rotating calipers on its hull). All islands of all
// meshes are then skyline-packed, bottom-left, with a 90-degree turn allowed, into the smallest
// square that holds them, found by bisection. Projections are one-to-one in world units, so the
// texel density is uniform across the whole asset, times each mesh's `weight`.
//
// Plain arrays in, plain arrays out: no engine, no DOM. The two helpers the bakes share with it
// (weld by position, crease-aware normals) live here too.

// ------------------------------------------------------------------ weld by position
/**
 * Which vertices share a position: `map[i]` is the welded id of vertex i (0..count-1) and `rep[w]`
 * the first vertex that had that position. A 27-cell spatial hash, so a pair that straddles a cell
 * boundary is still found. `tol` defaults to a millionth of the mesh's extent. Nothing is moved:
 * this is for connectivity (islands, curvature, normals across UV seams), not for merging.
 */
export function weldMap(pos: Float32Array, tol = 0): { map: Uint32Array; count: number; rep: Uint32Array } {
  const n = (pos.length / 3) | 0;
  let ext = 0;
  for (let i = 0; i < pos.length; i++) { const a = Math.abs(pos[i]); if (a > ext) ext = a; }
  const t = tol > 0 ? tol : Math.max(1e-9, ext * 1e-6);
  const inv = 1 / t, t2 = t * t;
  let cap = 16; while (cap < n * 2 + 16) cap <<= 1;
  const mask = cap - 1;
  const kx = new Int32Array(cap), ky = new Int32Array(cap), kz = new Int32Array(cap);
  const head = new Int32Array(cap).fill(-1);
  const next = new Int32Array(Math.max(1, n)).fill(-1);
  const map = new Uint32Array(n);
  const rep: number[] = [];
  const slotOf = (x: number, y: number, z: number, create: boolean): number => {
    let h = (Math.imul(x, 73856093) ^ Math.imul(y, 19349663) ^ Math.imul(z, 83492791)) & mask;
    for (;;) {
      if (head[h] === -1) {
        if (!create) return -1;
        kx[h] = x; ky[h] = y; kz[h] = z; head[h] = -2;   // -2: claimed, still empty
        return h;
      }
      if (kx[h] === x && ky[h] === y && kz[h] === z) return h;
      h = (h + 1) & mask;
    }
  };
  for (let i = 0; i < n; i++) {
    const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
    const qx = Math.floor(x * inv), qy = Math.floor(y * inv), qz = Math.floor(z * inv);
    let found = -1;
    for (let dz = -1; dz <= 1 && found < 0; dz++) for (let dy = -1; dy <= 1 && found < 0; dy++) for (let dx = -1; dx <= 1 && found < 0; dx++) {
      const s = slotOf(qx + dx, qy + dy, qz + dz, false);
      if (s < 0) continue;
      for (let j = head[s]; j >= 0; j = next[j]) {
        const ox = pos[j * 3] - x, oy = pos[j * 3 + 1] - y, oz = pos[j * 3 + 2] - z;
        if (ox * ox + oy * oy + oz * oz <= t2) { found = j; break; }
      }
    }
    if (found >= 0) { map[i] = map[found]; continue; }
    const s = slotOf(qx, qy, qz, true);
    next[i] = head[s] >= 0 ? head[s] : -1;
    head[s] = i;
    map[i] = rep.length;
    rep.push(i);
  }
  return { map, count: rep.length, rep: Uint32Array.from(rep) };
}

// ------------------------------------------------------------------ faces
/** Unit face normals and areas. A face with no area gets a zero normal. */
function faceData(pos: Float32Array, idx: Uint32Array): { fn: Float32Array; area: Float32Array } {
  const nt = (idx.length / 3) | 0;
  const fn = new Float32Array(nt * 3), area = new Float32Array(nt);
  for (let t = 0; t < nt; t++) {
    const a = idx[t * 3] * 3, b = idx[t * 3 + 1] * 3, c = idx[t * 3 + 2] * 3;
    const e1x = pos[b] - pos[a], e1y = pos[b + 1] - pos[a + 1], e1z = pos[b + 2] - pos[a + 2];
    const e2x = pos[c] - pos[a], e2y = pos[c + 1] - pos[a + 1], e2z = pos[c + 2] - pos[a + 2];
    const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
    const l = Math.hypot(nx, ny, nz);
    area[t] = l * 0.5;
    if (l > 1e-30) { fn[t * 3] = nx / l; fn[t * 3 + 1] = ny / l; fn[t * 3 + 2] = nz / l; }
  }
  return { fn, area };
}

/** Faces around each welded vertex, as CSR (start has count+1 entries). */
function facesAround(wid: Uint32Array | Int32Array, idx: Uint32Array, count: number): { start: Int32Array; list: Int32Array } {
  const nt = (idx.length / 3) | 0;
  const start = new Int32Array(count + 1);
  for (let k = 0; k < nt * 3; k++) start[wid[idx[k]] + 1]++;
  for (let i = 0; i < count; i++) start[i + 1] += start[i];
  const fill = start.slice(0, count);
  const list = new Int32Array(nt * 3);
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) list[fill[wid[idx[t * 3 + k]]]++] = t;
  return { start, list };
}

/**
 * The normals the bakes assume when a mesh brings none: area-weighted, shared across every copy of
 * a position (so a UV seam never shows as a shading seam), and split where faces meet sharper than
 * `crease` degrees. Per vertex: the faces round its position within `crease` of the vertex's own
 * faces are averaged. Give a mesh these as its `normal` attribute and the baked normal map is exact.
 */
export function smoothNormalsWelded(pos: Float32Array, idx: Uint32Array, crease = 60): Float32Array {
  const nv = (pos.length / 3) | 0, nt = (idx.length / 3) | 0;
  const { map, count } = weldMap(pos);
  const { fn, area } = faceData(pos, idx);
  const { start, list } = facesAround(map, idx, count);
  // Each vertex's own faces, area weighted: the reference a crease is measured against.
  const own = new Float64Array(nv * 3);
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) {
    const v = idx[t * 3 + k];
    own[v * 3] += fn[t * 3] * area[t]; own[v * 3 + 1] += fn[t * 3 + 1] * area[t]; own[v * 3 + 2] += fn[t * 3 + 2] * area[t];
  }
  const cosC = Math.cos((crease * Math.PI) / 180);
  const out = new Float32Array(nv * 3);
  for (let v = 0; v < nv; v++) {
    let rx = own[v * 3], ry = own[v * 3 + 1], rz = own[v * 3 + 2];
    const rl = Math.hypot(rx, ry, rz);
    if (rl < 1e-30) { out[v * 3 + 1] = 1; continue; }
    rx /= rl; ry /= rl; rz /= rl;
    const w = map[v];
    let sx = 0, sy = 0, sz = 0;
    for (let i = start[w]; i < start[w + 1]; i++) {
      const f = list[i];
      const d = fn[f * 3] * rx + fn[f * 3 + 1] * ry + fn[f * 3 + 2] * rz;
      if (d < cosC) continue;
      sx += fn[f * 3] * area[f]; sy += fn[f * 3 + 1] * area[f]; sz += fn[f * 3 + 2] * area[f];
    }
    const l = Math.hypot(sx, sy, sz);
    if (l < 1e-30) { out[v * 3] = rx; out[v * 3 + 1] = ry; out[v * 3 + 2] = rz; }
    else { out[v * 3] = sx / l; out[v * 3 + 1] = sy / l; out[v * 3 + 2] = sz / l; }
  }
  return out;
}

// ------------------------------------------------------------------ islands of one mesh
interface Island {
  mesh: number;
  faces: number[];
  verts: number[];            // input vertex per island vertex
  x: Float64Array; y: Float64Array;   // island-local 2D, world units x weight, min corner at 0
  w: number; h: number;
  // placement
  px: number; py: number; rot: boolean;
}

/** Andrew's monotone chain: the hull of 2D points, counter-clockwise, as indices. */
function hull2(x: Float64Array, y: Float64Array): number[] {
  const n = x.length;
  const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => x[a] - x[b] || y[a] - y[b]);
  const cross = (o: number, a: number, b: number) => (x[a] - x[o]) * (y[b] - y[o]) - (y[a] - y[o]) * (x[b] - x[o]);
  const lower: number[] = [], upper: number[] = [];
  for (const i of order) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], i) <= 0) lower.pop();
    lower.push(i);
  }
  for (let k = order.length - 1; k >= 0; k--) {
    const i = order[k];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], i) <= 0) upper.pop();
    upper.push(i);
  }
  upper.pop(); lower.pop();
  return lower.concat(upper);
}

/** Turn the island's points to their tightest box and move the box's corner to the origin. */
function tighten(isl: Island) {
  const { x, y } = isl;
  const n = x.length;
  if (!n) { isl.w = isl.h = 0; return; }
  const H = n > 2 ? hull2(x, y) : Array.from({ length: n }, (_, i) => i);
  let bestA = Infinity, bc = 1, bs = 0;
  const tryDir = (c: number, s: number) => {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const i of H) {
      const rx = x[i] * c + y[i] * s, ry = -x[i] * s + y[i] * c;
      if (rx < x0) x0 = rx; if (rx > x1) x1 = rx; if (ry < y0) y0 = ry; if (ry > y1) y1 = ry;
    }
    const a = (x1 - x0) * (y1 - y0);
    if (a < bestA - 1e-18) { bestA = a; bc = c; bs = s; }
  };
  tryDir(1, 0);
  for (let k = 0; k < H.length; k++) {
    const i = H[k], j = H[(k + 1) % H.length];
    const dx = x[j] - x[i], dy = y[j] - y[i], l = Math.hypot(dx, dy);
    if (l > 1e-15) tryDir(dx / l, dy / l);
  }
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    const rx = x[i] * bc + y[i] * bs, ry = -x[i] * bs + y[i] * bc;
    x[i] = rx; y[i] = ry;
    if (rx < x0) x0 = rx; if (rx > x1) x1 = rx; if (ry < y0) y0 = ry; if (ry > y1) y1 = ry;
  }
  for (let i = 0; i < n; i++) { x[i] -= x0; y[i] -= y0; }
  isl.w = x1 - x0; isl.h = y1 - y0;
  // Landscape, so the packer's first guess is the flat way round.
  if (isl.h > isl.w) {
    // A quarter turn, (x, y) -> (y, w - x): a rotation, never a mirror.
    for (let i = 0; i < n; i++) { const t = x[i]; x[i] = y[i]; y[i] = isl.w - t; }
    const t = isl.w; isl.w = isl.h; isl.h = t;
  }
}

function islandsOfMesh(pos: Float32Array, idx: Uint32Array, meshIndex: number, weight: number, angle: number): Island[] {
  const nt = (idx.length / 3) | 0;
  if (!nt) return [];
  const { map, count } = weldMap(pos);
  const { fn, area } = faceData(pos, idx);
  // Adjacency across shared (welded) edges; a non-manifold edge links its first two faces only.
  const adj = new Int32Array(nt * 3).fill(-1);
  const edges = new Map<number, number>();
  for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) {
    const a = map[idx[t * 3 + k]], b = map[idx[t * 3 + (k + 1) % 3]];
    if (a === b) continue;
    const key = a < b ? a * count + b : b * count + a;
    const o = edges.get(key);
    if (o === undefined) edges.set(key, t * 3 + k);
    else if (o >= 0) { adj[t * 3 + k] = (o / 3) | 0; adj[o] = t; edges.set(key, -1); }
  }
  // Projection directions, largest faces first.
  const cosA = Math.cos((Math.min(89, Math.max(1, angle)) * Math.PI) / 180);
  const order = Array.from({ length: nt }, (_, i) => i).sort((p, q) => area[q] - area[p]);
  const assigned = new Uint8Array(nt);
  for (let t = 0; t < nt; t++) if (!(area[t] > 1e-30)) assigned[t] = 1;   // no area, no direction
  const dirs: number[] = [];
  let ptr = 0;
  for (;;) {
    while (ptr < nt && assigned[order[ptr]]) ptr++;
    if (ptr >= nt) break;
    const seed = order[ptr];
    let vx = fn[seed * 3], vy = fn[seed * 3 + 1], vz = fn[seed * 3 + 2];
    for (let it = 0; it < 3; it++) {
      let sx = 0, sy = 0, sz = 0;
      for (let f = 0; f < nt; f++) {
        if (assigned[f]) continue;
        const d = fn[f * 3] * vx + fn[f * 3 + 1] * vy + fn[f * 3 + 2] * vz;
        if (d < cosA) continue;
        sx += fn[f * 3] * area[f]; sy += fn[f * 3 + 1] * area[f]; sz += fn[f * 3 + 2] * area[f];
      }
      const l = Math.hypot(sx, sy, sz);
      if (l < 1e-30) break;
      const nx = sx / l, ny = sy / l, nz = sz / l;
      // The refined direction must still take the seed, or the loop would never end.
      if (fn[seed * 3] * nx + fn[seed * 3 + 1] * ny + fn[seed * 3 + 2] * nz < cosA) break;
      vx = nx; vy = ny; vz = nz;
    }
    dirs.push(vx, vy, vz);
    for (let f = 0; f < nt; f++) {
      if (assigned[f]) continue;
      if (fn[f * 3] * vx + fn[f * 3 + 1] * vy + fn[f * 3 + 2] * vz >= cosA) assigned[f] = 1;
    }
    assigned[seed] = 1;
  }
  const nd = dirs.length / 3;
  if (!nd) dirs.push(0, 1, 0);
  // Each face to the direction it faces best.
  const group = new Int32Array(nt);
  for (let f = 0; f < nt; f++) {
    let best = 0, bd = -Infinity;
    for (let d = 0; d < dirs.length / 3; d++) {
      const v = fn[f * 3] * dirs[d * 3] + fn[f * 3 + 1] * dirs[d * 3 + 1] + fn[f * 3 + 2] * dirs[d * 3 + 2];
      if (v > bd) { bd = v; best = d; }
    }
    group[f] = best;
  }
  // Islands: connected faces of one direction.
  const isl = new Int32Array(nt).fill(-1);
  const out: Island[] = [];
  const stamp = new Int32Array((pos.length / 3) | 0).fill(-1);
  const local = new Int32Array((pos.length / 3) | 0);
  const queue = new Int32Array(nt);
  for (let s = 0; s < nt; s++) {
    if (isl[s] >= 0) continue;
    const id = out.length;
    const g = group[s];
    let qh = 0, qt = 0;
    queue[qt++] = s; isl[s] = id;
    const faces: number[] = [];
    while (qh < qt) {
      const t = queue[qh++];
      faces.push(t);
      for (let k = 0; k < 3; k++) {
        const u = adj[t * 3 + k];
        if (u < 0 || isl[u] >= 0 || group[u] !== g) continue;
        isl[u] = id; queue[qt++] = u;
      }
    }
    // Project along the direction: a right-handed (t, b, n) frame.
    const nx = dirs[g * 3], ny = dirs[g * 3 + 1], nz = dirs[g * 3 + 2];
    const hx = Math.abs(ny) < 0.9 ? 0 : 1, hy = Math.abs(ny) < 0.9 ? 1 : 0;
    let tx = hy * nz - 0 * ny, ty = 0 * nx - hx * nz, tz = hx * ny - hy * nx;
    const tl = Math.hypot(tx, ty, tz) || 1; tx /= tl; ty /= tl; tz /= tl;
    const bx = ny * tz - nz * ty, by = nz * tx - nx * tz, bz = nx * ty - ny * tx;
    const verts: number[] = [];
    for (const t of faces) for (let k = 0; k < 3; k++) {
      const v = idx[t * 3 + k];
      if (stamp[v] === id) continue;
      stamp[v] = id; local[v] = verts.length; verts.push(v);
    }
    const x = new Float64Array(verts.length), y = new Float64Array(verts.length);
    for (let i = 0; i < verts.length; i++) {
      const v = verts[i] * 3;
      x[i] = (pos[v] * tx + pos[v + 1] * ty + pos[v + 2] * tz) * weight;
      y[i] = (pos[v] * bx + pos[v + 1] * by + pos[v + 2] * bz) * weight;
    }
    const island: Island = { mesh: meshIndex, faces, verts, x, y, w: 0, h: 0, px: 0, py: 0, rot: false };
    tighten(island);
    out.push(island);
  }
  return out;
}

// ------------------------------------------------------------------ packing
/**
 * Skyline, bottom-left: each box (sorted longest side first) goes where its top ends lowest,
 * either way round; ties go left. Returns the height used, or Infinity when a box is wider than
 * the bin. Boxes arrive already grown by the gap; the bin starts `gap` in from the left and bottom.
 */
function skyline(boxes: Array<{ w: number; h: number; i: number }>, binW: number, place: (i: number, x: number, y: number, rot: boolean) => void): number {
  // Nodes: x start, y top, width.
  let nx: number[] = [0], ny: number[] = [0], nw: number[] = [binW];
  let used = 0;
  for (const b of boxes) {
    let bestY = Infinity, bestX = Infinity, bestI = -1, bestRot = false, bestW = 0, bestH = 0;
    for (let r = 0; r < 2; r++) {
      const w = r ? b.h : b.w, h = r ? b.w : b.h;
      if (w > binW + 1e-12) continue;
      for (let i = 0; i < nx.length; i++) {
        if (nx[i] + w > binW + 1e-12) break;
        // The lowest y the box can sit at from node i: the highest node it spans.
        let y = 0, span = 0, j = i;
        while (span < w - 1e-12 && j < nx.length) { if (ny[j] > y) y = ny[j]; span += nw[j]; j++; }
        if (span < w - 1e-12) continue;
        const top = y + h;
        if (top < bestY - 1e-12 || (Math.abs(top - bestY) <= 1e-12 && nx[i] < bestX)) {
          bestY = top; bestX = nx[i]; bestI = i; bestRot = r === 1; bestW = w; bestH = h;
        }
      }
    }
    if (bestI < 0) return Infinity;
    const x = nx[bestI], y = bestY - bestH;
    place(b.i, x, y, bestRot);
    if (bestY > used) used = bestY;
    // New node over [x, x + bestW) at bestY; trim the nodes it covers.
    const NX: number[] = [], NY: number[] = [], NW: number[] = [];
    for (let i = 0; i < nx.length; i++) {
      const s = nx[i], e = nx[i] + nw[i];
      if (e <= x + 1e-15 || s >= x + bestW - 1e-15) { NX.push(s); NY.push(ny[i]); NW.push(nw[i]); continue; }
      if (s < x) { NX.push(s); NY.push(ny[i]); NW.push(x - s); }
      if (e > x + bestW) { NX.push(x + bestW); NY.push(ny[i]); NW.push(e - (x + bestW)); }
    }
    // Insert the new node in order, then merge equal neighbours.
    let at = 0; while (at < NX.length && NX[at] < x) at++;
    NX.splice(at, 0, x); NY.splice(at, 0, bestY); NW.splice(at, 0, bestW);
    nx = []; ny = []; nw = [];
    for (let i = 0; i < NX.length; i++) {
      const k = nx.length - 1;
      if (k >= 0 && Math.abs(ny[k] - NY[i]) <= 1e-15) nw[k] += NW[i];
      else { nx.push(NX[i]); ny.push(NY[i]); nw.push(NW[i]); }
    }
  }
  return used;
}

/** Pack every island into the smallest square side that holds them all with `margin` gaps. */
function packIslands(islands: Island[], margin: number): number {
  if (!islands.length) return 1;
  let area = 0, big = 0;
  for (const s of islands) { area += (s.w + 1e-12) * (s.h + 1e-12); big = Math.max(big, s.w, s.h); }
  const order = islands.map((s, i) => ({ w: s.w, h: s.h, i })).sort((p, q) => Math.max(q.w, q.h) - Math.max(p.w, p.h) || q.w * q.h - p.w * p.h);
  const tryS = (S: number, commit: boolean): boolean => {
    const g = margin * S;
    const boxes = order.map((b) => ({ w: b.w + g, h: b.h + g, i: b.i }));
    const used = skyline(boxes, S - g, commit ? (i, x, y, rot) => { const s = islands[i]; s.px = x + g; s.py = y + g; s.rot = rot; } : () => {});
    return used + g <= S + 1e-12;
  };
  const m = Math.min(Math.max(margin, 0), 0.2);
  let lo = Math.max(Math.sqrt(area), big) / (1 - 2 * m + 1e-9) * 0.999;
  let hi = lo * 1.25;
  let guard = 0;
  while (!tryS(hi, false) && guard++ < 60) { lo = hi; hi *= 1.25; }
  for (let it = 0; it < 22 && hi - lo > hi * 2e-4; it++) {
    const mid = (lo + hi) / 2;
    if (tryS(mid, false)) hi = mid; else lo = mid;
  }
  tryS(hi, true);
  return hi;
}

// ------------------------------------------------------------------ the atlas
/**
 * Unwrap several meshes and pack ALL their islands into one 0..1 square: one texture set for a
 * whole asset. Islands are Smart-UV-Project islands (every face within `angle` degrees, default 66,
 * of its island's projection direction, so none folds); texel density is uniform across every
 * mesh, times its `weight` (the face x2, a hidden arm x0.5: a linear scale of its islands).
 * `margin` (default 0.004 of the side, ~8 px at 2048) is the gap between islands and to the border.
 *
 * Vertices are split per island, as `unwrapArrays` does: `map[i]` is the INPUT vertex each output
 * vertex came from, so any attribute of the input (colour, skin weights) can be carried over. The
 * extra `normal` is the input's own `normal` carried through `map` when one was given, otherwise
 * `smoothNormalsWelded` of the input: give the geometry this normal and pass the same to the bakes.
 */
export function atlasArrays(meshes: Array<{ pos: Float32Array; idx: Uint32Array; weight?: number; normal?: Float32Array }>,
  opts: { margin?: number; angle?: number } = {}): Array<{ pos: Float32Array; idx: Uint32Array; uv: Float32Array; map: Uint32Array; normal: Float32Array }> {
  const margin = opts.margin ?? 0.004, angle = opts.angle ?? 66;
  const all: Island[] = [];
  const perMesh: Island[][] = meshes.map((m, mi) => {
    const w = typeof m.weight === "number" && m.weight > 0 ? m.weight : 1;
    const list = islandsOfMesh(m.pos, m.idx, mi, w, angle);
    for (const s of list) all.push(s);
    return list;
  });
  const S = packIslands(all, margin);
  return meshes.map((m, mi) => {
    const list = perMesh[mi];
    let nv = 0, ni = 0;
    for (const s of list) { nv += s.verts.length; ni += s.faces.length * 3; }
    const pos = new Float32Array(nv * 3), uv = new Float32Array(nv * 2), map = new Uint32Array(nv), idx = new Uint32Array(ni);
    const src = m.normal && m.normal.length === m.pos.length ? m.normal : smoothNormalsWelded(m.pos, m.idx);
    const normal = new Float32Array(nv * 3);
    const local = new Int32Array((m.pos.length / 3) | 0);
    let vo = 0, io = 0;
    for (const s of list) {
      for (let i = 0; i < s.verts.length; i++) {
        const v = s.verts[i];
        local[v] = vo + i;
        map[vo + i] = v;
        pos[(vo + i) * 3] = m.pos[v * 3]; pos[(vo + i) * 3 + 1] = m.pos[v * 3 + 1]; pos[(vo + i) * 3 + 2] = m.pos[v * 3 + 2];
        normal[(vo + i) * 3] = src[v * 3]; normal[(vo + i) * 3 + 1] = src[v * 3 + 1]; normal[(vo + i) * 3 + 2] = src[v * 3 + 2];
        const lx = s.rot ? s.h - s.y[i] : s.x[i], ly = s.rot ? s.x[i] : s.y[i];
        uv[(vo + i) * 2] = Math.min(1, Math.max(0, (s.px + lx) / S));
        uv[(vo + i) * 2 + 1] = Math.min(1, Math.max(0, (s.py + ly) / S));
      }
      for (const t of s.faces) for (let k = 0; k < 3; k++) idx[io++] = local[m.idx[t * 3 + k]];
      vo += s.verts.length;
    }
    return { pos, idx, uv, map, normal };
  });
}

/** How full an atlas is: the share of the square its islands' triangles cover, and the island count. */
export function atlasStats(parts: Array<{ idx: Uint32Array; uv: Float32Array }>): { fill: number; islands: number } {
  let area = 0, islands = 0;
  for (const p of parts) {
    const nv = (p.uv.length / 2) | 0;
    const parent = new Int32Array(nv); for (let i = 0; i < nv; i++) parent[i] = i;
    const find = (a: number): number => { while (parent[a] !== a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; };
    for (let t = 0; t < p.idx.length; t += 3) {
      const a = p.idx[t], b = p.idx[t + 1], c = p.idx[t + 2];
      area += Math.abs((p.uv[b * 2] - p.uv[a * 2]) * (p.uv[c * 2 + 1] - p.uv[a * 2 + 1]) - (p.uv[c * 2] - p.uv[a * 2]) * (p.uv[b * 2 + 1] - p.uv[a * 2 + 1])) / 2;
      const ra = find(a), rb = find(b), rc = find(c);
      parent[rb] = ra; parent[find(rc)] = ra;
    }
    const used = new Uint8Array(nv);
    for (let t = 0; t < p.idx.length; t++) used[p.idx[t]] = 1;
    for (let i = 0; i < nv; i++) if (used[i] && find(i) === i) islands++;
  }
  return { fill: area, islands };
}
