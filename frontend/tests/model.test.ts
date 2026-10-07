// The modelling operations, tested on the numbers that would be wrong if the algorithm were.
//
// Run: npm run test:model

import { checkArrays, type V3 } from "../src/components/engine/edit/ops";
import { bevelArrays, quadsFromTris, subdivideCC } from "../src/components/engine/edit/model";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => { if (cond) { pass++; return; } fails.push(name + (extra ? "  <- " + extra : "")); };
const near = (name: string, got: number, want: number, tol = 1e-4) => ok(name, Math.abs(got - want) <= tol, "got " + got + ", want " + want);
const eq = (name: string, got: unknown, want: unknown) => ok(name, JSON.stringify(got) === JSON.stringify(want), "got " + JSON.stringify(got) + ", want " + JSON.stringify(want));

/** A unit cube as six separate quads (24 vertices, 12 triangles): the shape a procedural box has. */
function cube(size = 1): { pos: Float32Array; idx: Uint32Array } {
  const h = size / 2;
  const P: number[] = [], I: number[] = [];
  const faces: Array<[V3, V3, V3]> = [
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
    [[0, -1, 0], [1, 0, 0], [0, 0, 1]], [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
  ];
  for (const [n, u, v] of faces) {
    const c: V3 = [n[0] * h, n[1] * h, n[2] * h];
    const corner = (su: number, sv: number): V3 => [c[0] + u[0] * su * h + v[0] * sv * h, c[1] + u[1] * su * h + v[1] * sv * h, c[2] + u[2] * su * h + v[2] * sv * h];
    const base = P.length / 3;
    for (const [su, sv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) P.push(...corner(su, sv));
    I.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

/** An open grid in XZ from -1 to 1, n by n quads, facing +Y. */
function plane(n = 2): { pos: Float32Array; idx: Uint32Array } {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) P.push(-1 + (2 * i) / n, 0, -1 + (2 * j) / n);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const a = j * (n + 1) + i, b = a + 1, c = a + n + 1, d = c + 1;
    I.push(a, c, b, b, c, d);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

const extent = (pos: Float32Array) => { let m = 0; for (const v of pos) m = Math.max(m, Math.abs(v)); return m; };

// ---- quads from triangles ------------------------------------------------------------------
{
  const c = cube(1);
  const q = quadsFromTris(c.pos, c.idx);
  eq("a box comes back as six quads", [q.faces.length, q.faces.every((f) => f.length === 4)], [6, true]);
  const p = plane(3);
  eq("a grid comes back as nine quads", quadsFromTris(p.pos, p.idx).faces.length, 9);
}

// ---- Catmull-Clark -------------------------------------------------------------------------
{
  const c = cube(1);
  const s1 = subdivideCC(c.pos, c.idx, 1);
  const r1 = checkArrays(s1.pos, s1.idx);
  eq("one level of a box is 24 quads, 48 triangles", s1.idx.length / 3, 48);
  ok("smooth subdivision rounds the box and keeps it closed", r1.holes === 0 && !r1.inverted && r1.volume > 0.4 && r1.volume < 0.9, JSON.stringify([r1.holes, r1.volume]));
  const s2 = subdivideCC(c.pos, c.idx, 2);
  eq("two levels: 96 quads", s2.idx.length / 3, 192);
  const r2 = checkArrays(s2.pos, s2.idx);
  ok("still closed and outward after two", r2.holes === 0 && !r2.inverted && r2.volume < r1.volume + 1e-6, JSON.stringify([r2.holes, r2.volume]));
  ok("the smooth limit shrinks toward the inside: nothing leaves the box", extent(s2.pos) <= 0.5 + 1e-6);

  // Creases: every box edge is ninety degrees, so with a crease angle below that the box stays a box.
  const k = subdivideCC(c.pos, c.idx, 2, { creaseAngle: 45 });
  const rk = checkArrays(k.pos, k.idx);
  near("a creased box keeps its volume exactly", rk.volume, 1, 1e-6);
  near("and its extent", extent(k.pos), 0.5, 1e-9);
  ok("closed", rk.holes === 0 && !rk.inverted);

  // Sharpness in levels: sharp for one level, then smooth — between the two.
  const half = subdivideCC(c.pos, c.idx, 2, { creaseAngle: 45, sharpness: 1 });
  const rh = checkArrays(half.pos, half.idx);
  ok("a one-level crease lands between smooth and sharp", rh.volume > r2.volume && rh.volume < 1, String(rh.volume));

  // Boundaries: an open sheet keeps its outline and its corners.
  const p = plane(2);
  const sp = subdivideCC(p.pos, p.idx, 2);
  near("an open grid keeps its extent", extent(sp.pos), 1, 1e-9);
  let onEdge = 0, total = 0;
  for (let i = 0; i < sp.pos.length; i += 3) { total++; if (Math.abs(Math.abs(sp.pos[i]) - 1) < 1e-9 || Math.abs(Math.abs(sp.pos[i + 2]) - 1) < 1e-9) onEdge++; }
  ok("its boundary is still a square", onEdge >= 16 && total > onEdge, onEdge + "/" + total);
  ok("and stays flat", sp.pos.every((v, i) => (i % 3 === 1 ? Math.abs(v) < 1e-9 : true)));
}

// ---- bevel -----------------------------------------------------------------------------------
{
  const c = cube(1);
  const b = bevelArrays(c.pos, c.idx, { width: 0.1, segments: 3 });
  const r = checkArrays(b.pos, b.idx);
  ok("a bevelled box is closed and outward", r.holes === 0 && !r.inverted, JSON.stringify([r.holes, r.inverted, r.boundaryEdges]));
  ok("it only loses edge volume", r.volume > 0.93 && r.volume < 0.999, String(r.volume));
  ok("it grows no bigger than the box", extent(b.pos) <= 0.5 + 1e-6, String(extent(b.pos)));
  ok("and has many more faces", b.idx.length / 3 > 100, String(b.idx.length / 3));
  const ch = bevelArrays(c.pos, c.idx, { width: 0.05, segments: 1 });
  const rc = checkArrays(ch.pos, ch.idx);
  ok("a one-segment bevel is a chamfer: closed, a hair smaller", rc.holes === 0 && rc.volume > 0.98 && rc.volume < 1, String(rc.volume));
  const p = plane(3);
  const bp = bevelArrays(p.pos, p.idx, { width: 0.1, segments: 2, angle: 30 });
  eq("a flat sheet has nothing to bevel and comes back as it was", bp.idx.length / 3, 18);
}

// ---- unwrap ----------------------------------------------------------------------------------
import { bakeAO, bakeCurvature, bakeNormalMap, buildBVH, nearestBVH, raycastBVH, unwrapArrays } from "../src/components/engine/edit/model";
{
  const c = cube(1);
  const u = unwrapArrays(c.pos, c.idx, { margin: 0.02 });
  eq("a box unwraps to six islands", u.islands, 6);
  ok("every uv lies inside the square", u.uv.every((v) => v >= -1e-9 && v <= 1 + 1e-9));
  eq("vertices were split per island", u.pos.length / 3, 24);
  // Uniform texel density: six equal faces get six equal uv areas.
  const areas: number[] = [];
  for (let t = 0; t < u.idx.length; t += 3) {
    const a = u.idx[t], b = u.idx[t + 1], cc = u.idx[t + 2];
    areas.push(Math.abs((u.uv[b * 2] - u.uv[a * 2]) * (u.uv[cc * 2 + 1] - u.uv[a * 2 + 1]) - (u.uv[cc * 2] - u.uv[a * 2]) * (u.uv[b * 2 + 1] - u.uv[a * 2 + 1])) / 2);
  }
  const mn = Math.min(...areas), mx = Math.max(...areas);
  ok("every triangle has the same uv area", mx - mn < 1e-6, mn + ".." + mx);
  ok("and the square is well used", areas.reduce((p, q) => p + q, 0) > 0.5, String(areas.reduce((p, q) => p + q, 0)));
  // Islands do not overlap: their boxes are disjoint.
  const boxes: number[][] = [];
  for (let i = 0; i < 6; i++) {
    let x0 = 1, x1 = 0, y0 = 1, y1 = 0;
    for (let v = i * 4; v < i * 4 + 4; v++) { x0 = Math.min(x0, u.uv[v * 2]); x1 = Math.max(x1, u.uv[v * 2]); y0 = Math.min(y0, u.uv[v * 2 + 1]); y1 = Math.max(y1, u.uv[v * 2 + 1]); }
    boxes.push([x0, x1, y0, y1]);
  }
  let overlap = false;
  for (let i = 0; i < 6; i++) for (let j = i + 1; j < 6; j++) {
    const [a, b] = [boxes[i], boxes[j]];
    if (a[0] < b[1] - 1e-9 && b[0] < a[1] - 1e-9 && a[2] < b[3] - 1e-9 && b[2] < a[3] - 1e-9) overlap = true;
  }
  ok("no two islands overlap", !overlap);
  const p = plane(4);
  const up = unwrapArrays(p.pos, p.idx);
  eq("a flat sheet is one island", up.islands, 1);
}

// ---- BVH and bakes -------------------------------------------------------------------------
{
  const c = cube(1);
  const bvh = buildBVH(c.pos, c.idx);
  const hit = raycastBVH(bvh, [0, 0, 5], [0, 0, -1]);
  ok("a ray from outside hits the near face", !!hit && Math.abs(hit.t - 4.5) < 1e-6, JSON.stringify(hit));
  ok("a ray that misses says so", raycastBVH(bvh, [3, 3, 5], [0, 0, -1]) === null);
  const nr = nearestBVH(bvh, [0, 0, 2])!;
  near("the nearest point of a box to a point above it is on the top face", Math.sqrt(nr.d2), 1.5, 1e-6);

  // AO on a convex box: nothing occludes it, so the map is white.
  const u = unwrapArrays(c.pos, c.idx);
  const ao = bakeAO(u.pos, u.idx, u.uv, 32, { rays: 16 });
  let sum = 0, n = 0;
  for (let i = 0; i < ao.data.length; i += 4) if (ao.data[i + 3]) { sum += ao.data[i]; n++; }
  ok("a convex box bakes white", n > 0 && sum / n > 250, String(sum / n));

  // A trench: a floor with two walls; the floor between them is darker than the open floor.
  const floor = plane(8);
  const P: number[] = Array.from(floor.pos), I: number[] = Array.from(floor.idx);
  const wall = (x: number, flip: boolean) => {
    const base = P.length / 3;
    P.push(x, 0, -0.3, x, 0, 0.3, x, 0.8, 0.3, x, 0.8, -0.3);
    if (flip) I.push(base, base + 1, base + 2, base, base + 2, base + 3); else I.push(base, base + 2, base + 1, base, base + 3, base + 2);
  };
  wall(-0.25, true); wall(0.25, false);
  const tp = Float32Array.from(P), ti = Uint32Array.from(I);
  const tu = unwrapArrays(tp, ti);
  const tao = bakeAO(tu.pos, tu.idx, tu.uv, 48, { rays: 24 });
  // Read the AO back at two points of the floor through the uv of the nearest floor vertex.
  const aoAt = (x: number, z: number) => {
    let best = -1, bd = Infinity;
    for (let v = 0; v < tu.pos.length / 3; v++) { if (Math.abs(tu.pos[v * 3 + 1]) > 1e-6) continue; const d = Math.hypot(tu.pos[v * 3] - x, tu.pos[v * 3 + 2] - z); if (d < bd) { bd = d; best = v; } }
    const px = Math.min(47, Math.floor(tu.uv[best * 2] * 48)), py = Math.min(47, Math.floor(tu.uv[best * 2 + 1] * 48));
    return tao.data[(py * 48 + px) * 4];
  };
  const inside = aoAt(0, 0), open = aoAt(0.9, 0.9);
  ok("the floor between two walls is darker than the open floor", inside < open - 20, inside + " vs " + open);

  // Curvature: a box's corners are convex, so the bake is above mid-grey and in range.
  const cv = bakeCurvature(u.pos, u.idx, u.uv, 32);
  let lo = 255, hi = 0;
  for (let i = 0; i < cv.data.length; i += 4) if (cv.data[i + 3]) { lo = Math.min(lo, cv.data[i]); hi = Math.max(hi, cv.data[i]); }
  ok("curvature of a box reads convex", lo >= 128 && hi <= 255 && n > 0, lo + ".." + hi);

  // A normal map: a bumpy high-poly over a flat low-poly plane varies; a flat one over flat is blue.
  const low = plane(1);
  const lu = unwrapArrays(low.pos, low.idx);
  const high = plane(24);
  for (let i = 0; i < high.pos.length; i += 3) high.pos[i + 1] = 0.04 * Math.sin(high.pos[i] * 6) * Math.cos(high.pos[i + 2] * 6);
  const nm = bakeNormalMap({ pos: lu.pos, idx: lu.idx, uv: lu.uv }, high, 32, { distance: 0.2 });
  let rMin = 255, rMax = 0, bSum = 0, cnt = 0;
  for (let i = 0; i < nm.data.length; i += 4) if (nm.data[i + 3]) { rMin = Math.min(rMin, nm.data[i]); rMax = Math.max(rMax, nm.data[i]); bSum += nm.data[i + 2]; cnt++; }
  ok("the bumps show as a varying red channel", rMax - rMin > 20 && cnt > 0, rMin + ".." + rMax);
  ok("and the map points mostly up", bSum / cnt > 230, String(bSum / cnt));
  const flat = bakeNormalMap({ pos: lu.pos, idx: lu.idx, uv: lu.uv }, plane(4), 16, { distance: 0.2 });
  let dev = 0, fc = 0;
  for (let i = 0; i < flat.data.length; i += 4) if (flat.data[i + 3]) { dev = Math.max(dev, Math.abs(flat.data[i] - 128), Math.abs(flat.data[i + 1] - 128), Math.abs(flat.data[i + 2] - 255)); fc++; }
  ok("flat over flat is the flat blue", fc > 0 && dev <= 2, String(dev));
}

// ---- heat weights, remesh, relax -----------------------------------------------------------
import { heatWeightsArrays, relaxArrays, remeshArrays } from "../src/components/engine/edit/model";
/** An open tube along Y from 0 to `h`, `rows` rings of `seg` vertices, welded. */
function tube(h = 2, rows = 20, seg = 12, r = 0.3): { pos: Float32Array; idx: Uint32Array } {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= rows; j++) for (let i = 0; i < seg; i++) { const a = (i / seg) * Math.PI * 2; P.push(Math.cos(a) * r, (j / rows) * h, Math.sin(a) * r); }
  for (let j = 0; j < rows; j++) for (let i = 0; i < seg; i++) {
    const a = j * seg + i, b = j * seg + (i + 1) % seg, c = a + seg, d = b + seg;
    I.push(a, c, b, b, c, d);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}
{
  const t = tube(2, 20, 12);
  const bones = [{ head: [0, 0, 0] as V3, tail: [0, 1, 0] as V3 }, { head: [0, 1, 0] as V3, tail: [0, 2, 0] as V3 }];
  const { index, weight } = heatWeightsArrays(t.pos, t.idx, bones, { maxBones: 4 });
  const nv = t.pos.length / 3;
  let sumOk = true;
  const w0At = (y: number) => {
    // Average weight of bone 0 over the ring nearest y.
    let s = 0, k = 0;
    for (let v = 0; v < nv; v++) {
      if (Math.abs(t.pos[v * 3 + 1] - y) > 1e-6) continue;
      let w = 0; for (let q = 0; q < 4; q++) if (index[v * 4 + q] === 0) w += weight[v * 4 + q];
      s += w; k++;
    }
    return s / Math.max(1, k);
  };
  for (let v = 0; v < nv; v++) { let s = 0; for (let q = 0; q < 4; q++) s += weight[v * 4 + q]; if (Math.abs(s - 1) > 1e-4) sumOk = false; }
  ok("every vertex's weights sum to one", sumOk);
  ok("the bottom ring belongs to the lower bone", w0At(0) > 0.9, String(w0At(0)));
  ok("the top ring belongs to the upper bone", w0At(2) < 0.1, String(w0At(2)));
  const mid = w0At(1);
  ok("the middle is shared", mid > 0.3 && mid < 0.7, String(mid));
  let mono = true, prev = 2;
  for (let j = 0; j <= 20; j++) { const w = w0At((j / 20) * 2); if (w > prev + 1e-6) mono = false; prev = w; }
  ok("and the blend runs smoothly from one to the other", mono);
}
{
  const c = cube(1);
  const r = remeshArrays(c.pos, c.idx, { res: 32 });
  const k = checkArrays(r.pos, r.idx);
  ok("a remeshed box is one closed surface", k.boundaryEdges === 0 && k.nonManifoldEdges === 0 && !k.inverted, JSON.stringify([k.boundaryEdges, k.nonManifoldEdges, k.inverted]));
  ok("with the box's volume, give or take the voxel skin", k.volume > 0.9 && k.volume < 1.12, String(k.volume));
  eq("and no duplicate vertices", k.duplicates, 0);
  ok("its triangles are evenly sized: many more than twelve", r.idx.length / 3 > 500, String(r.idx.length / 3));
}
{
  const p = plane(24);
  for (let i = 0; i < p.pos.length; i += 3) p.pos[i + 1] = 0.05 * Math.sin(p.pos[i] * 40) * Math.cos(p.pos[i + 2] * 40);
  const rough = (pos: Float32Array) => { let s = 0; for (let i = 0; i < pos.length; i += 3) s += Math.abs(pos[i + 1]); return s / (pos.length / 3); };
  const before = rough(p.pos);
  const after = relaxArrays(p.pos, p.idx, { iterations: 8 });
  ok("relaxing a jagged sheet flattens it", rough(after) < before * 0.5, rough(after) + " vs " + before);
  ok("but leaves its edge alone", Math.abs(after[0] - p.pos[0]) < 1e-9 && Math.abs(after[2] - p.pos[2]) < 1e-9);
  eq("and keeps every vertex", after.length, p.pos.length);
}

// ------------------------------------------------------------------
console.log(pass + " checks passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL " + f);
process.exit(fails.length ? 1 : 0);
