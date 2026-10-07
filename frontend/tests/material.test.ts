// Procedural materials and the one-atlas bake, tested on the numbers that would be wrong if the
// algorithm were.
//
// Run: esbuild tests/material.test.ts --bundle --format=esm --platform=node --outfile=../data/tmp/material.test.mjs && node ../data/tmp/material.test.mjs
// MATERIAL_PERF=0 skips the timing section (the contract's two targets, on real sizes).

import * as THREE from "three";
import { atlasArrays, atlasStats, smoothNormalsWelded } from "../src/components/engine/edit/atlas";
import { applyMaps, bakeAtlas, bakeFunction, bakeMaterial, MATERIALS, type SmartMaterial } from "../src/components/engine/edit/material";
import type { V3 } from "../src/components/engine/edit/ops";
import { bakeAO, bakeCurvature, bakeNormalMap, unwrapArrays } from "../src/components/engine/edit/model";
import { createHash } from "node:crypto";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => { if (cond) { pass++; return; } fails.push(name + (extra ? "  <- " + extra : "")); };
const near = (name: string, got: number, want: number, tol: number) => ok(name, Math.abs(got - want) <= tol, "got " + got + ", want " + want + " +- " + tol);

type M = { pos: Float32Array; idx: Uint32Array };

// ------------------------------------------------------------------ meshes
/** An open grid in XZ, n by n quads, facing +Y, centred at the origin, `size` across. */
function plane(n: number, size: number, height?: (x: number, z: number) => number): M {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) {
    const x = -size / 2 + (size * i) / n, z = -size / 2 + (size * j) / n;
    P.push(x, height ? height(x, z) : 0, z);
  }
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const a = j * (n + 1) + i, b = a + 1, c = a + n + 1, d = c + 1;
    I.push(a, c, b, b, c, d);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

/** An L-shaped block: the profile (0,0)(1,0)(2,0)(2,1)(1,1)(1,2)(0,2)(0,1) times `u`, extruded +-h in z.
 *  Convex edges at (2,1) and (1,2), a concave (inner) edge at (1,1). No T-junctions. */
function lBlock(u: number, h: number): M {
  const prof: Array<[number, number]> = [[0, 0], [1, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 1]];
  const P: number[] = [], I: number[] = [];
  const v = (x: number, y: number, z: number) => { P.push(x * u, y * u, z); return P.length / 3 - 1; };
  for (let i = 0; i < prof.length; i++) {
    const [ax, ay] = prof[i], [bx, by] = prof[(i + 1) % prof.length];
    const a0 = v(ax, ay, -h), b0 = v(bx, by, -h), b1 = v(bx, by, h), a1 = v(ax, ay, h);
    I.push(a0, b0, b1, a0, b1, a1);
  }
  // Caps: three unit squares, +z facing out at z = h, -z at z = -h.
  for (const [x0, y0] of [[0, 0], [1, 0], [0, 1]]) {
    const a = v(x0, y0, h), b = v(x0 + 1, y0, h), c = v(x0 + 1, y0 + 1, h), d = v(x0, y0 + 1, h);
    I.push(a, b, c, a, c, d);
    const e = v(x0, y0, -h), f = v(x0 + 1, y0, -h), g = v(x0 + 1, y0 + 1, -h), k = v(x0, y0 + 1, -h);
    I.push(e, g, f, e, k, g);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

/** A UV sphere; `thetaMax` < PI makes an open cap (PI / 2: a hemisphere, open at the bottom). */
function sphere(c: V3, r: V3, su: number, sv: number, bump?: (x: number, y: number, z: number) => number, thetaMax = Math.PI): M {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= sv; j++) for (let i = 0; i <= su; i++) {
    const th = (thetaMax * j) / sv, ph = (2 * Math.PI * i) / su;
    const x = Math.sin(th) * Math.cos(ph), y = Math.cos(th), z = Math.sin(th) * Math.sin(ph);
    const s = bump ? 1 + bump(x, y, z) : 1;
    P.push(c[0] + r[0] * x * s, c[1] + r[1] * y * s, c[2] + r[2] * z * s);
  }
  for (let j = 0; j < sv; j++) for (let i = 0; i < su; i++) {
    const a = j * (su + 1) + i, b = a + 1, d = a + su + 1, e = d + 1;
    if (j > 0) I.push(a, b, d);
    if (j < sv - 1 || thetaMax < Math.PI) I.push(b, e, d);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

function torus(c: V3, R: number, r: number, s1: number, s2: number): M {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= s2; j++) for (let i = 0; i <= s1; i++) {
    const u = (2 * Math.PI * i) / s1, v = (2 * Math.PI * j) / s2;
    P.push(c[0] + (R + r * Math.cos(v)) * Math.cos(u), c[1] + r * Math.sin(v), c[2] + (R + r * Math.cos(v)) * Math.sin(u));
  }
  for (let j = 0; j < s2; j++) for (let i = 0; i < s1; i++) {
    const a = j * (s1 + 1) + i, b = a + 1, d = a + s1 + 1, e = d + 1;
    I.push(a, d, b, b, d, e);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

/** A capped cylinder along y. */
function cylinder(c: V3, r: number, h: number, seg: number, rings: number): M {
  const P: number[] = [], I: number[] = [];
  for (let j = 0; j <= rings; j++) for (let i = 0; i <= seg; i++) {
    const a = (2 * Math.PI * i) / seg;
    P.push(c[0] + r * Math.cos(a), c[1] - h / 2 + (h * j) / rings, c[2] + r * Math.sin(a));
  }
  for (let j = 0; j < rings; j++) for (let i = 0; i < seg; i++) {
    const a = j * (seg + 1) + i, b = a + 1, d = a + seg + 1, e = d + 1;
    I.push(a, d, b, b, d, e);
  }
  for (const [y, up] of [[c[1] + h / 2, 1], [c[1] - h / 2, -1]]) {
    const centre = P.length / 3; P.push(c[0], y, c[2]);
    const base = P.length / 3;
    for (let i = 0; i <= seg; i++) { const a = (2 * Math.PI * i) / seg; P.push(c[0] + r * Math.cos(a), y, c[2] + r * Math.sin(a)); }
    for (let i = 0; i < seg; i++) { if (up > 0) I.push(centre, base + i + 1, base + i); else I.push(centre, base + i, base + i + 1); }
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

function box(c: V3, s: V3): M {
  const P: number[] = [], I: number[] = [];
  const faces: Array<[V3, V3, V3]> = [
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
    [[0, -1, 0], [1, 0, 0], [0, 0, 1]], [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
  ];
  for (const [n, u, v] of faces) {
    const base = P.length / 3;
    for (const [su, sv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      P.push(c[0] + (n[0] + u[0] * su + v[0] * sv) * s[0] / 2, c[1] + (n[1] + u[1] * su + v[1] * sv) * s[1] / 2, c[2] + (n[2] + u[2] * su + v[2] * sv) * s[2] / 2);
    }
    I.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
}

// ------------------------------------------------------------------ helpers
const at = (p: ArrayLike<number>, i: number): V3 => [p[i * 3], p[i * 3 + 1], p[i * 3 + 2]];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a: V3): V3 => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

/** Barycentrics of a point in a triangle's plane (not clamped). */
function bary(p: V3, A: V3, B: V3, C: V3): V3 {
  const v0 = sub(B, A), v1 = sub(C, A), v2 = sub(p, A);
  const d00 = dot(v0, v0), d01 = dot(v0, v1), d11 = dot(v1, v1), d20 = dot(v2, v0), d21 = dot(v2, v1);
  const den = d00 * d11 - d01 * d01;
  const v = (d11 * d20 - d01 * d21) / den, w = (d00 * d21 - d01 * d20) / den;
  return [1 - v - w, v, w];
}

/** The triangle of a UV mesh that holds a 3D point, and the point's UV. */
function locate(m: { pos: Float32Array; idx: Uint32Array; uv: Float32Array }, p: V3): { tri: number; uv: [number, number] } | null {
  let best: { tri: number; uv: [number, number] } | null = null, bestD = Infinity;
  for (let t = 0; t < m.idx.length / 3; t++) {
    const a = m.idx[t * 3], b = m.idx[t * 3 + 1], c = m.idx[t * 3 + 2];
    const A = at(m.pos, a), B = at(m.pos, b), C = at(m.pos, c);
    const n = norm(cross(sub(B, A), sub(C, A)));
    const d = Math.abs(dot(sub(p, A), n));
    if (d > 1e-5) continue;
    const l = bary(p, A, B, C);
    const out = Math.max(-l[0], -l[1], -l[2]);
    if (out > 1e-6 || d >= bestD) continue;
    bestD = d;
    best = { tri: t, uv: [l[0] * m.uv[a * 2] + l[1] * m.uv[b * 2] + l[2] * m.uv[c * 2], l[0] * m.uv[a * 2 + 1] + l[1] * m.uv[b * 2 + 1] + l[2] * m.uv[c * 2 + 1]] };
  }
  return best;
}

/** The UV of the point on the mesh nearest to p (for points given only roughly on a curved surface). */
function locateNear(m: { pos: Float32Array; idx: Uint32Array; uv: Float32Array }, p: V3): [number, number] {
  let best: [number, number] = [0, 0], bd = Infinity;
  for (let t = 0; t < m.idx.length / 3; t++) {
    const a = m.idx[t * 3], b = m.idx[t * 3 + 1], c = m.idx[t * 3 + 2];
    const A = at(m.pos, a), B = at(m.pos, b), C = at(m.pos, c);
    const n = norm(cross(sub(B, A), sub(C, A)));
    const q = sub(p, mul(n, dot(sub(p, A), n)));             // onto the triangle's plane
    let l = bary(q, A, B, C);
    l = [Math.max(0, l[0]), Math.max(0, l[1]), Math.max(0, l[2])];
    const s = l[0] + l[1] + l[2]; l = [l[0] / s, l[1] / s, l[2] / s];
    const on = add(add(mul(A, l[0]), mul(B, l[1])), mul(C, l[2]));
    const d = dot(sub(on, p), sub(on, p));
    if (d < bd) { bd = d; best = [l[0] * m.uv[a * 2] + l[1] * m.uv[b * 2] + l[2] * m.uv[c * 2], l[0] * m.uv[a * 2 + 1] + l[1] * m.uv[b * 2 + 1] + l[2] * m.uv[c * 2 + 1]]; }
  }
  return best;
}

const texel = (img: { width: number; height: number; data: Uint8ClampedArray }, uv: [number, number]): number[] => {
  const x = Math.min(img.width - 1, Math.floor(uv[0] * img.width)), y = Math.min(img.height - 1, Math.floor(uv[1] * img.height));
  const o = (y * img.width + x) * 4;
  return [img.data[o], img.data[o + 1], img.data[o + 2], img.data[o + 3]];
};
const texelCentreUv = (img: { width: number; height: number }, uv: [number, number]): [number, number] => {
  const x = Math.min(img.width - 1, Math.floor(uv[0] * img.width)), y = Math.min(img.height - 1, Math.floor(uv[1] * img.height));
  return [(x + 0.5) / img.width, (y + 0.5) / img.height];
};

/**
 * three r183's perturbNormal2Arb + normal_fragment_maps, ported line for line: how three draws a
 * tangent-space normal map on a mesh WITHOUT a tangent attribute. The screen derivatives come
 * from an orthographic camera looking at the triangle face-on (x right, y up), and the UV
 * derivatives from moving the point by one "pixel" and re-reading its barycentric UV: nothing
 * here shares a formula with material.ts.
 */
function threeDecode(m: { pos: Float32Array; idx: Uint32Array; uv: Float32Array }, normals: Float32Array, tri: number, p: V3,
  rgb: number[], normalScale: [number, number]): V3 {
  const a = m.idx[tri * 3], b = m.idx[tri * 3 + 1], c = m.idx[tri * 3 + 2];
  const A = at(m.pos, a), B = at(m.pos, b), C = at(m.pos, c);
  const fnrm = norm(cross(sub(B, A), sub(C, A)));
  const l = bary(p, A, B, C);
  let N = norm(add(add(mul(at(normals, a), l[0]), mul(at(normals, b), l[1])), mul(at(normals, c), l[2])));
  if (dot(N, fnrm) < 0) N = mul(N, -1);
  // Screen axes in the face plane with right x up = toward the camera (the face normal).
  const helper: V3 = Math.abs(fnrm[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
  const right = norm(cross(helper, fnrm)), up = cross(fnrm, right);
  const px = 1e-4;
  const q0 = mul(right, px), q1 = mul(up, px);
  const uvAt = (x: V3) => { const k = bary(x, A, B, C); return [k[0] * m.uv[a * 2] + k[1] * m.uv[b * 2] + k[2] * m.uv[c * 2], k[0] * m.uv[a * 2 + 1] + k[1] * m.uv[b * 2 + 1] + k[2] * m.uv[c * 2 + 1]]; };
  const uv0 = uvAt(p), uvx = uvAt(add(p, q0)), uvy = uvAt(add(p, q1));
  const st0 = [uvx[0] - uv0[0], uvx[1] - uv0[1]], st1 = [uvy[0] - uv0[0], uvy[1] - uv0[1]];
  // vec3 q1perp = cross( q1, N ); vec3 q0perp = cross( N, q0 );
  const q1perp = cross(q1, N), q0perp = cross(N, q0);
  // vec3 T = q1perp * st0.x + q0perp * st1.x;  vec3 B = q1perp * st0.y + q0perp * st1.y;
  const T = add(mul(q1perp, st0[0]), mul(q0perp, st1[0]));
  const Bt = add(mul(q1perp, st0[1]), mul(q0perp, st1[1]));
  const det = Math.max(dot(T, T), dot(Bt, Bt));
  const scale = det === 0 ? 0 : 1 / Math.sqrt(det);
  // vec3 mapN = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;  mapN.xy *= normalScale;
  const mapN: V3 = [(rgb[0] / 255) * 2 - 1, (rgb[1] / 255) * 2 - 1, (rgb[2] / 255) * 2 - 1];
  mapN[0] *= normalScale[0]; mapN[1] *= normalScale[1];
  // normal = normalize( tbn * mapN );
  return norm(add(add(mul(T, scale * mapN[0]), mul(Bt, scale * mapN[1])), mul(N, mapN[2])));
}

/** Islands of an atlas output: triangles joined through shared vertices, and through copies of a
 *  vertex with the same position and UV (a sphere's seam: one island, two index components). */
function islandFind(o: { pos: Float32Array; idx: Uint32Array; uv: Float32Array }): (a: number) => number {
  const nv = o.uv.length / 2, parent = new Int32Array(nv);
  for (let i = 0; i < nv; i++) parent[i] = i;
  const find = (a: number): number => { while (parent[a] !== a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; };
  const join = (a: number, b: number) => { const ra = find(a), rb = find(b); if (ra !== rb) parent[rb] = ra; };
  for (let t = 0; t < o.idx.length; t += 3) { join(o.idx[t], o.idx[t + 1]); join(o.idx[t], o.idx[t + 2]); }
  const seen = new Map<string, number>();
  for (let i = 0; i < nv; i++) {
    // Rounded: a seam copy made from sin(2 pi) sits 1e-17 away, which Float32 keeps.
    const k = [o.pos[i * 3], o.pos[i * 3 + 1], o.pos[i * 3 + 2], o.uv[i * 2], o.uv[i * 2 + 1]].map((v) => String(Math.round(v * 1e6))).join(",");
    const j = seen.get(k);
    if (j === undefined) seen.set(k, i); else join(j, i);
  }
  return find;
}

const white: SmartMaterial = { base: { color: "#ffffff", roughness: 0.5, metal: 0 }, layers: [] };

// ---- 0. model.ts's own bakes are untouched --------------------------------------------------
// Nothing that works today may change: these hashes are of unwrapArrays / bakeAO / bakeCurvature /
// bakeNormalMap on a fixed mesh, taken from the git HEAD blob of model.ts (421a00c4) before this
// builder's work began (data/tmp/model_head/proof.ts runs HEAD against the working copy).
{
  const lumpy = (su: number, sv: number, amp: number) => {
    const P: number[] = [], I: number[] = [];
    for (let j = 0; j <= sv; j++) for (let i = 0; i <= su; i++) {
      const th = (Math.PI * j) / sv, ph = (2 * Math.PI * i) / su;
      const x = Math.sin(th) * Math.cos(ph), y = Math.cos(th), z = Math.sin(th) * Math.sin(ph);
      const s = 0.2 * (1 + amp * Math.sin(5 * x + 2 * y) * Math.cos(4 * z - y));
      P.push(x * s, y * s, z * s);
    }
    for (let j = 0; j < sv; j++) for (let i = 0; i < su; i++) {
      const a = j * (su + 1) + i, b = a + 1, d = a + su + 1, e = d + 1;
      if (j > 0) I.push(a, b, d);
      if (j < sv - 1) I.push(b, e, d);
    }
    return { pos: Float32Array.from(P), idx: Uint32Array.from(I) };
  };
  const h = (...arrs: ArrayBufferView[]) => { const c = createHash("sha256"); for (const a of arrs) c.update(new Uint8Array(a.buffer, a.byteOffset, a.byteLength)); return c.digest("hex").slice(0, 16); };
  const low = lumpy(32, 24, 0.08), high = lumpy(128, 96, 0.08);
  const u = unwrapArrays(low.pos, low.idx);
  const got = {
    unwrapArrays: h(u.pos, u.idx, u.uv),
    bakeAO: h(bakeAO(u.pos, u.idx, u.uv, 64, { rays: 16 }).data),
    bakeCurvature: h(bakeCurvature(u.pos, u.idx, u.uv, 64).data),
    bakeNormalMap: h(bakeNormalMap({ pos: u.pos, idx: u.idx, uv: u.uv }, high, 64, { distance: 0.05 }).data),
  };
  const want = { unwrapArrays: "ea4d43fd27243d1c", bakeAO: "c695cd266acd0362", bakeCurvature: "b3b85334fa967ab7", bakeNormalMap: "e861b3515f9b8abe" };
  for (const k of Object.keys(want) as Array<keyof typeof want>) ok("model.ts " + k + " output is byte-identical to before", got[k] === want[k], got[k] + " vs " + want[k]);
}

// ---- 1. a convex edge gets the wear layer, a cavity the dirt layer ----------------------------
{
  const u = 0.1, L = lBlock(u, 0.1);
  const [A] = atlasArrays([L]);
  const mat: SmartMaterial = {
    base: { color: "#ffffff", roughness: 0.5, metal: 0 },
    layers: [
      { name: "wear", color: "#ff0000", mask: { curvature: { lo: 0.3, hi: 0.6 } } },
      { name: "dirt", color: "#0000ff", mask: { curvature: { lo: -0.3, hi: -0.6 } } },
    ],
  };
  const maps = bakeMaterial(A, mat, 512, { ao: false });
  const read = (p: V3) => { const l = locate(A, p); return l ? texel(maps.baseColor, l.uv) : [-1, -1, -1]; };
  const convex = read([1.97 * u, 1 * u, 0]);        // 3 mm from the convex edge at (2, 1)
  const concave = read([1.03 * u, 1 * u, 0]);       // 3 mm from the inner edge at (1, 1)
  const flat = read([1.5 * u, 1 * u, 0.02]);        // the middle of that face
  ok("a convex edge gets the wear layer", convex[0] > 200 && convex[1] < 60 && convex[2] < 60, JSON.stringify(convex));
  ok("a cavity gets the dirt layer", concave[2] > 200 && concave[0] < 60 && concave[1] < 60, JSON.stringify(concave));
  ok("and a flat face neither", flat[0] > 245 && flat[1] > 245 && flat[2] > 245, JSON.stringify(flat));
  // The band is as wide as asked, whatever the tessellation: 2 cm from the edge is outside it.
  const far = read([1.8 * u, 1 * u, 0]);
  ok("the wear band ends at the curvature radius (1 cm)", far[1] > 245, JSON.stringify(far));
  // Occlusion: the inner corner sees less sky than the open top.
  const tex = bakeFunction(A, 256, (t) => [t.ao, t.ao, t.ao], { ao: { rays: 64, distance: 0.1 } });
  const aoIn = texel(tex, locate(A, [1.03 * u, 1 * u, 0])!.uv)[0], aoTop = texel(tex, locate(A, [1.5 * u, 1 * u, 0])!.uv)[0];
  ok("the inner corner is occluded, the open face is not", aoIn < aoTop - 50 && aoTop > 200, aoIn + " vs " + aoTop);
  // (3 cm rays: the open sample is 5 cm from the 10 cm wall, which longer rays rightly see.)
  const aoMaps = bakeMaterial(A, { base: { color: "#ffffff", roughness: 0.5 }, layers: [{ color: "#000000", mask: { ao: { lo: 0.6, hi: 0.9 } } }] }, 256, { ao: { rays: 64, distance: 0.03 }, aoInColor: 0 });
  const dIn = texel(aoMaps.baseColor, locate(A, [1.02 * u, 1 * u, 0])!.uv)[0], dTop = texel(aoMaps.baseColor, locate(A, [1.5 * u, 1 * u, 0])!.uv)[0];
  ok("an ao mask puts dirt in the corner and not on top", dIn < 90 && dTop > 230, dIn + " vs " + dTop);
  // A coarse box: every vertex is a corner, and still only a band along the edges is worn.
  const B = box([0, 0, 0], [0.2, 0.2, 0.2]);
  const [BA] = atlasArrays([B]);
  const bm = bakeMaterial(BA, mat, 256, { ao: false });
  const mid = texel(bm.baseColor, locate(BA, [0, 0.1, 0])!.uv), edge = texel(bm.baseColor, locate(BA, [0.097, 0.1, 0])!.uv);
  ok("a 12-triangle box is worn only along its edges", mid[1] > 245 && edge[0] > 200 && edge[1] < 60, JSON.stringify([mid, edge]));
}

// ---- 2. noise is deterministic for a seed and differs for another -----------------------------
{
  const P = plane(8, 0.3);
  const [A] = atlasArrays([P]);
  const noisy = (seed: number): SmartMaterial => ({
    base: { color: "#ffffff", roughness: 0.5 },
    layers: [
      { color: "#000000", mask: { noise: { freq: 20, lo: 0.4, hi: 0.6, seed, warp: 0.5 } } },
      { color: "#ff0000", mask: { voronoi: { cells: 40, mode: "spots", seed } }, opacity: 0.5 },
      { color: "#00ff00", mask: { streaks: { dir: [1, 0, 0], freq: 30, stretch: 6, seed } }, opacity: 0.5 },
    ],
  });
  const a1 = bakeMaterial(A, noisy(1), 128, { ao: false }).baseColor.data;
  const a2 = bakeMaterial(A, noisy(1), 128, { ao: false }).baseColor.data;
  const b = bakeMaterial(A, noisy(2), 128, { ao: false }).baseColor.data;
  let same = true, diff = 0;
  for (let i = 0; i < a1.length; i++) { if (a1[i] !== a2[i]) same = false; if (a1[i] !== b[i]) diff++; }
  ok("the same seed bakes the same bytes", same);
  ok("another seed bakes another pattern", diff > a1.length * 0.2, diff + " of " + a1.length);
  // The remapped noise really is spread evenly: lo 0.75 hi 0.75+ covers about a quarter.
  const cover = bakeMaterial(A, { base: { color: "#000000", roughness: 0.5 }, layers: [{ color: "#ffffff", mask: { noise: { freq: 25, lo: 0.749, hi: 0.751, seed: 5 } } }] }, 256, { ao: false }).baseColor.data;
  let lit = 0, n = 0;
  for (let i = 0; i < cover.length; i += 4) { n++; if (cover[i] > 127) lit++; }
  near("noise lo/hi are area fractions (0.75 leaves a quarter)", lit / n, 0.25, 0.06);
  // Every preset builds and bakes.
  for (const name of Object.keys(MATERIALS)) {
    const m = MATERIALS[name]({ seed: 3 });
    const img = bakeMaterial(A, m, 64, { ao: false });
    let bad = false;
    for (const v of img.baseColor.data) if (!(v >= 0 && v <= 255)) bad = true;
    ok("preset " + name + " bakes", !bad && m.layers.length > 0);
  }
}

// ---- 3. a groove only on the high mesh reaches the low mesh -----------------------------------
{
  const size = 0.2, depth = 0.002, w = 0.005;
  const groove = (_x: number, z: number) => (Math.abs(z) < w ? -depth * Math.cos((Math.PI * z) / (2 * w)) ** 2 : 0);
  const low = plane(4, size);
  const high = plane(200, size, groove);
  const [A] = atlasArrays([low]);
  const mat: SmartMaterial = { base: { color: "#d0d0d0", roughness: 0.5 }, layers: [{ name: "dirt", color: "#202020", mask: { curvature: { lo: -0.1, hi: -0.5 } } }] };
  const withHigh = bakeMaterial(A, mat, 256, { high, ao: { rays: 32 } });
  const without = bakeMaterial(A, mat, 256, { ao: { rays: 32 } });
  const inG = locate(A, [0.013, 0, 0])!, away = locate(A, [0.013, 0, 0.05])!;
  const g1 = texel(withHigh.baseColor, inG.uv)[0], g0 = texel(without.baseColor, inG.uv)[0], f1 = texel(withHigh.baseColor, away.uv)[0];
  ok("the high groove darkens the low mesh's colour", g1 < f1 - 60 && g1 < g0 - 60, JSON.stringify({ groove: g1, groove_without_high: g0, away: f1 }));
  // The normal map bends at the groove's walls, and decodes (the three way) to the high normal.
  const normals = smoothNormalsWelded(A.pos, A.idx);
  let worst = 0, bent = 0;
  for (const zw of [-0.004, -0.0025, -0.0015, 0.0015, 0.0025, 0.004]) {
    const L = locate(A, [0.013, 0, zw])!;
    const c = texelCentreUv(withHigh.normal, L.uv);
    // The texel's own centre, back in 3D (the plane is flat: the UV map is affine).
    const rgb = texel(withHigh.normal, L.uv);
    // Where is that texel centre in 3D? Invert the triangle's affine UV map.
    const a = A.idx[L.tri * 3], b = A.idx[L.tri * 3 + 1], cc = A.idx[L.tri * 3 + 2];
    const du1 = A.uv[b * 2] - A.uv[a * 2], dv1 = A.uv[b * 2 + 1] - A.uv[a * 2 + 1], du2 = A.uv[cc * 2] - A.uv[a * 2], dv2 = A.uv[cc * 2 + 1] - A.uv[a * 2 + 1];
    const det = du1 * dv2 - du2 * dv1, eu = c[0] - A.uv[a * 2], ev = c[1] - A.uv[a * 2 + 1];
    const lb = (eu * dv2 - du2 * ev) / det, lc = (du1 * ev - eu * dv1) / det;
    const P3 = add(add(at(A.pos, a), mul(sub(at(A.pos, b), at(A.pos, a)), lb)), mul(sub(at(A.pos, cc), at(A.pos, a)), lc));
    const got = threeDecode(A, normals, L.tri, P3, rgb, [1, -1]);
    const zz = P3[2];
    const slope = Math.abs(zz) < w ? depth * (Math.PI / (2 * w)) * Math.sin((Math.PI * zz) / w) : 0;   // dy/dz of the groove
    const want = norm([0, 1, -slope]);
    const ang = (Math.acos(Math.max(-1, Math.min(1, dot(got, want)))) * 180) / Math.PI;
    worst = Math.max(worst, ang);
    if (Math.abs(rgb[0] - 128) + Math.abs(rgb[1] - 128) > 20) bent++;
  }
  ok("the baked normal bends at the groove's walls", bent >= 4, String(bent));
  ok("and decodes, three's way with normalScale (1,-1), to the high normal within 6 degrees", worst < 6, worst.toFixed(2) + " deg");
  // The sign is not symmetric: a dimple's walls face every way, so whichever way the atlas turned
  // the island, some wall's tilt lies along V, and (1,1) points it the wrong way.
  {
    const c: V3 = [0.03, 0, -0.03], rad = 0.008;
    const dimple = (x: number, z: number) => { const r = Math.hypot(x - c[0], z - c[2]); return r < rad ? -0.002 * Math.cos((Math.PI * r) / (2 * rad)) ** 2 : 0; };
    const hi2 = plane(240, size, dimple);
    const dm = bakeMaterial(A, mat, 512, { high: hi2, ao: false });
    let good = 0, bad = 0;
    for (const [dx, dz] of [[0.004, 0], [-0.004, 0], [0, 0.004], [0, -0.004]]) {
      const p: V3 = [c[0] + dx, 0, c[2] + dz];
      const L = locate(A, p)!;
      const rgb = texel(dm.normal, L.uv);
      // The analytic normal: the wall rises away from the centre, so it leans toward the centre.
      const r = Math.hypot(dx, dz), sl = 0.002 * (Math.PI / (2 * rad)) * Math.sin((Math.PI * r) / rad);
      const want = norm([-(dx / r) * sl, 1, -(dz / r) * sl]);
      const angle = (n: V3) => (Math.acos(Math.max(-1, Math.min(1, dot(n, want)))) * 180) / Math.PI;
      good = Math.max(good, angle(threeDecode(A, normals, L.tri, p, rgb, [1, -1])));
      bad = Math.max(bad, angle(threeDecode(A, normals, L.tri, p, rgb, [1, 1])));
    }
    ok("a dimple decodes right all round with normalScale (1,-1)", good < 7, good.toFixed(2) + " deg");
    ok("and wrong with (1,1): the convention is not symmetric", bad > 30, bad.toFixed(2) + " deg");
  }
}

// ---- 4. flat is (128,128,255); a height ramp tilts the right way --------------------------------
{
  const P = plane(6, 0.3);
  const [A] = atlasArrays([P]);
  const maps = bakeMaterial(A, white, 128, { ao: false });
  let allFlat = true;
  for (let i = 0; i < maps.normal.data.length; i += 4) {
    if (maps.normal.data[i] !== 128 || maps.normal.data[i + 1] !== 128 || maps.normal.data[i + 2] !== 255) { allFlat = false; break; }
  }
  ok("the normal map of a flat plane is (128,128,255), margins included", allFlat);
  // ORM and colour of a plain material.
  const o = texel(maps.orm, [0.5, 0.5]);
  ok("orm: occlusion 1 without ao, roughness and metal as given", o[0] === 255 && o[1] === 128 && o[2] === 0, JSON.stringify(o));
  // A smooth step in height along +x: the surface rises toward +x, so the normal leans toward -x.
  const a = 0.01, bump = 0.001;
  const ramp: SmartMaterial = { base: { color: "#ffffff", roughness: 0.5 }, bump,
    layers: [{ height: 1, mask: { region: (p) => { const t = Math.max(0, Math.min(1, (p[0] + a) / (2 * a))); return t * t * (3 - 2 * t); } } }] };
  const rm = bakeMaterial(A, ramp, 512, { ao: false });
  const normals = smoothNormalsWelded(A.pos, A.idx);
  const L0 = locate(A, [0, 0, 0.05])!, L1 = locate(A, [0.1, 0, 0.05])!;
  const n0 = threeDecode(A, normals, L0.tri, [0, 0, 0.05], texel(rm.normal, L0.uv), [1, -1]);
  const n1 = threeDecode(A, normals, L1.tri, [0.1, 0, 0.05], texel(rm.normal, L1.uv), [1, -1]);
  const slope = (0.75 / a) * bump;    // d/dx of the smoothstep at its middle, times the relief
  near("a height ramp tilts the normal toward -x by its slope (decoded three's way)", n0[0], -slope / Math.hypot(1, slope), 0.015);
  ok("and not at all where the ramp is flat", Math.abs(n1[0]) < 0.01 && Math.abs(n1[2]) < 0.01, JSON.stringify(n1));
}

// ---- 5. the atlas: islands never overlap and all lie in 0..1 ----------------------------------
{
  const meshes = [
    { ...sphere([0, 1, 0], [0.15, 0.18, 0.15], 32, 20), weight: 2 },
    { ...torus([0.5, 0.5, 0], 0.2, 0.05, 40, 16) },
    { ...lBlock(0.1, 0.1) },
    { ...box([-0.5, 0, 0], [0.3, 0.1, 0.2]), weight: 0.5 },
    { ...cylinder([0, 0, 0.5], 0.08, 0.5, 24, 6) },
  ];
  const out = atlasArrays(meshes);
  let inRange = true;
  const rects: Array<[number, number, number, number, number]> = [];   // x0 y0 x1 y1 owner
  out.forEach((o, mi) => {
    for (const v of o.uv) if (!(v >= 0 && v <= 1)) inRange = false;
    const nv = o.uv.length / 2, find = islandFind(o);
    const box2 = new Map<number, [number, number, number, number]>();
    for (let i = 0; i < nv; i++) {
      const r = find(i), b = box2.get(r) || [Infinity, Infinity, -Infinity, -Infinity];
      b[0] = Math.min(b[0], o.uv[i * 2]); b[1] = Math.min(b[1], o.uv[i * 2 + 1]); b[2] = Math.max(b[2], o.uv[i * 2]); b[3] = Math.max(b[3], o.uv[i * 2 + 1]);
      box2.set(r, b);
    }
    for (const b of box2.values()) rects.push([b[0], b[1], b[2], b[3], mi]);
    ok("mesh " + mi + " keeps its triangles and maps every vertex back", o.idx.length === meshes[mi].idx.length && o.map.length === o.pos.length / 3 &&
      Array.from(o.map).every((v, i) => o.pos[i * 3] === meshes[mi].pos[v * 3] && o.pos[i * 3 + 2] === meshes[mi].pos[v * 3 + 2]));
  });
  ok("all UVs lie in 0..1", inRange);
  let overlaps = 0;
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i], b = rects[j];
    if (a[0] < b[2] - 1e-9 && b[0] < a[2] - 1e-9 && a[1] < b[3] - 1e-9 && b[1] < a[3] - 1e-9) overlaps++;
  }
  ok("no two islands overlap (" + rects.length + " islands from 5 meshes)", overlaps === 0, overlaps + " overlapping pairs");
  // Texel-exact: rasterise every triangle's interior at 1024 and count texels two islands claim.
  const R = 1024, claim = new Int32Array(R * R).fill(-1);
  let clash = 0, islandId = 0;
  out.forEach((o) => {
    const find = islandFind(o);
    const ids = new Map<number, number>();
    for (let t = 0; t < o.idx.length; t += 3) {
      const r = find(o.idx[t]);
      if (!ids.has(r)) ids.set(r, islandId++);
      const id = ids.get(r)!;
      const P2 = [0, 1, 2].map((k) => [o.uv[o.idx[t + k] * 2] * R, o.uv[o.idx[t + k] * 2 + 1] * R]);
      const x0 = Math.max(0, Math.floor(Math.min(P2[0][0], P2[1][0], P2[2][0]))), x1 = Math.min(R - 1, Math.ceil(Math.max(P2[0][0], P2[1][0], P2[2][0])));
      const y0 = Math.max(0, Math.floor(Math.min(P2[0][1], P2[1][1], P2[2][1]))), y1 = Math.min(R - 1, Math.ceil(Math.max(P2[0][1], P2[1][1], P2[2][1])));
      const e = (a: number[], b: number[], x: number, y: number) => (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);
      const area = e(P2[0], P2[1], P2[2][0], P2[2][1]);
      if (Math.abs(area) < 1e-12) continue;
      for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
        const w0 = e(P2[1], P2[2], x + 0.5, y + 0.5) / area, w1 = e(P2[2], P2[0], x + 0.5, y + 0.5) / area, w2 = e(P2[0], P2[1], x + 0.5, y + 0.5) / area;
        if (w0 < 0 || w1 < 0 || w2 < 0) continue;
        const k = y * R + x;
        if (claim[k] >= 0 && claim[k] !== id) clash++;
        claim[k] = id;
      }
    }
  });
  ok("no texel at 1024 is claimed by two islands", clash === 0, clash + " texels");
  // Texel density: the weight-2 sphere gets about 4x the UV area per square metre of the weight-1 torus.
  const density = (o: { pos: Float32Array; idx: Uint32Array; uv: Float32Array }) => {
    let a3 = 0, a2 = 0;
    for (let t = 0; t < o.idx.length; t += 3) {
      const A = at(o.pos, o.idx[t]), B = at(o.pos, o.idx[t + 1]), C = at(o.pos, o.idx[t + 2]);
      const n = cross(sub(B, A), sub(C, A));
      a3 += Math.hypot(n[0], n[1], n[2]) / 2;
      const u = o.uv, i = o.idx[t] * 2, j = o.idx[t + 1] * 2, k = o.idx[t + 2] * 2;
      a2 += Math.abs((u[j] - u[i]) * (u[k + 1] - u[i + 1]) - (u[k] - u[i]) * (u[j + 1] - u[i + 1])) / 2;
    }
    return a2 / a3;
  };
  const ratio = density(out[0]) / density(out[1]);
  ok("weight 2 is about 4x the texel area of weight 1", ratio > 3 && ratio < 5.2, ratio.toFixed(2));
  const st = atlasStats(out);
  ok("the pack is reasonably full (" + (st.fill * 100).toFixed(0) + "% of the square)", st.fill > 0.4, String(st.fill));
  // Same input, same atlas.
  const again = atlasArrays(meshes);
  ok("the atlas is deterministic", again.every((o, i) => o.uv.length === out[i].uv.length && o.uv.every((v, k) => v === out[i].uv[k])));
}

// ---- 6. applyMaps: the sign and the colour spaces ---------------------------------------------
{
  const P = plane(2, 0.2);
  const [A] = atlasArrays([P]);
  const maps = bakeMaterial(A, white, 32, { ao: false });
  const mat = new THREE.MeshStandardMaterial();
  const back = applyMaps(THREE, mat, maps);
  ok("applyMaps returns the material", back === mat);
  ok("normalScale.y is negative for the flipY:false map, x positive", mat.normalScale.y < 0 && mat.normalScale.x > 0 && mat.normalScale.x === -mat.normalScale.y, JSON.stringify(mat.normalScale));
  ok("every map is flipY:false", !mat.map!.flipY && !mat.normalMap!.flipY && !mat.roughnessMap!.flipY);
  ok("base colour is sRGB", mat.map!.colorSpace === THREE.SRGBColorSpace);
  ok("normal and ORM are linear (no colour space)", mat.normalMap!.colorSpace === THREE.NoColorSpace && mat.roughnessMap!.colorSpace === THREE.NoColorSpace, mat.normalMap!.colorSpace + " / " + mat.roughnessMap!.colorSpace);
  ok("one ORM texture feeds ao, roughness and metalness (as glTF packs it)", mat.aoMap === mat.roughnessMap && mat.roughnessMap === mat.metalnessMap);
  ok("roughness and metalness factors are 1, so the map rules", mat.roughness === 1 && mat.metalness === 1);
  ok("the colour factor is white", mat.color.getHex() === 0xffffff);
  ok("textures filter linearly with mipmaps", mat.map!.minFilter === THREE.LinearMipmapLinearFilter && mat.map!.magFilter === THREE.LinearFilter && mat.map!.generateMipmaps === true);
  const m2 = applyMaps(THREE, new THREE.MeshStandardMaterial(), { normal: maps.normal }, { normalScale: 0.6 });
  ok("a strength keeps the sign rule", Math.abs(m2.normalScale.x - 0.6) < 1e-9 && Math.abs(m2.normalScale.y + 0.6) < 1e-9);
}

// ---- 7. bakeFunction: the texel's point is on the surface, the uv is the texel centre ----------
{
  const P = plane(3, 0.4);
  const [A] = atlasArrays([P]);
  let worst = 0, uvWorst = 0, calls = 0;
  const img = bakeFunction(A, [96, 64], (t) => {
    calls++;
    const L = locate(A, t.p);
    if (L) uvWorst = Math.max(uvWorst, Math.abs(L.uv[0] - t.uv[0]) * 96, Math.abs(L.uv[1] - t.uv[1]) * 64);
    worst = Math.max(worst, Math.abs(t.p[1]), Math.abs(t.n[1] - 1));
    return [t.p[0] + 0.5, 0.5, t.p[2] + 0.5];
  }, { ao: false });
  ok("a non-square size works", img.width === 96 && img.height === 64 && img.data.length === 96 * 64 * 4);
  ok("fn sees points on the surface with its normal", worst < 1e-5, String(worst));
  ok("and the uv of the texel it paints (within a texel at island borders)", uvWorst <= 0.76, String(uvWorst));
  let empty = 0;
  for (let i = 3; i < img.data.length; i += 4) if (img.data[i] === 0) empty++;
  ok("dilation leaves no empty texel", empty === 0, String(empty));
  ok("fn ran once per covered texel", calls > 96 * 64 * 0.3 && calls <= 96 * 64, String(calls));
}

// ---- 8. the atlas bake: parts darken each other; one part is bakeMaterial ------------------------
{
  // A plate lying on a floor: alone the floor sees open sky; in one atlas the plate shades it.
  const floor = plane(6, 0.4);
  const plate = box([0, 0.012, 0], [0.16, 0.02, 0.16]);
  const [F, Pl] = atlasArrays([floor, plate]);
  const grey: SmartMaterial = { base: { color: "#c0c0c0", roughness: 0.6 }, layers: [] };
  const alone = bakeMaterial(F, grey, 256, { ao: { rays: 32, distance: 0.1 }, aoInColor: 1 });
  const both = bakeAtlas([{ ...F, mat: grey }, { ...Pl, mat: grey }], 256, { ao: { rays: 32, distance: 0.1 }, aoInColor: 1 });
  const at1 = locate(F, [0.085, 0, 0])!;     // 5 mm from the plate's edge
  const a0 = texel(alone.baseColor, at1.uv)[0], a1 = texel(both.baseColor, at1.uv)[0];
  ok("in one atlas the plate darkens the floor beside it (cross-part occlusion)", a1 < a0 - 40, a0 + " alone vs " + a1 + " together");
  const under = texel(both.orm, locate(F, [0, 0, 0])!.uv)[0];
  ok("and the floor under the plate is black in the occlusion channel", under < 20, String(under));
  // One part through bakeAtlas is exactly bakeMaterial at the same size.
  const [S1] = atlasArrays([sphere([0, 0, 0], [0.1, 0.1, 0.1], 24, 16)]);
  const m1 = bakeMaterial(S1, MATERIALS.leather(), 128, { ao: { rays: 8 } });
  const m2 = bakeAtlas([{ ...S1, mat: MATERIALS.leather() }], 128, { ao: { rays: 8 } });
  ok("bakeAtlas of one part equals bakeMaterial", ["baseColor", "orm", "normal"].every((k) => (m1 as any)[k].data.every((v: number, i: number) => v === (m2 as any)[k].data[i])));
  let threw = "";
  try { bakeMaterial({ pos: plate.pos, idx: plate.idx, uv: new Float32Array(0) }, grey, 32); } catch (e) { threw = String((e as Error).message); }
  ok("a part with no uv is refused with a reason", /no uv/.test(threw), threw);
}

// ---- 9. the graders' words, as numbers ---------------------------------------------------------
// "orange rust on grey steel" (the Blender goblin) against "beige steel that looks like ceramic"
// and "little ambient occlusion" (the forge goblin).
{
  const [S2] = atlasArrays([sphere([0, 0, 0], [0.15, 0.15, 0.15], 48, 32)]);
  const rs = bakeMaterial(S2, MATERIALS.rustySteel(), 256, { ao: false }).baseColor.data;
  let rust = 0, steel = 0, n = 0, sr = 0, sg = 0, sb = 0;
  for (let i = 0; i < rs.length; i += 4) {
    const r = rs[i], g = rs[i + 1], b = rs[i + 2];
    n++;
    if (r > g * 1.2 && g > b * 1.05 && r > 90) rust++;
    else if (Math.abs(r - g) < 14 && Math.abs(g - b) < 14 && r > 110) { steel++; sr += r; sg += g; sb += b; }
  }
  ok("rustySteel is orange rust on grey steel: some rust", rust / n > 0.04 && rust / n < 0.5, (rust / n).toFixed(3));
  ok("and mostly steel", steel / n > 0.3, (steel / n).toFixed(3));
  ok("and the steel is grey, not beige (blue within 25 levels of red; beige is ~60)", steel > 0 && sr / steel - sb / steel < 25, [sr / steel, sg / steel, sb / steel].map((v) => v.toFixed(0)).join(","));
  // Dark under the rim: a head under a helmet, one atlas, the skin preset with default settings.
  const head = sphere([0, 1.0, 0], [0.13, 0.14, 0.13], 48, 36), helm = sphere([0, 1.03, 0], [0.155, 0.16, 0.155], 48, 18, undefined, Math.PI / 2);
  const rim = torus([0, 1.03, 0], 0.155, 0.013, 48, 10);
  const un = atlasArrays([head, helm, rim]);
  const skin = MATERIALS.skin({ color: "#9a8851" }), rs2 = MATERIALS.rustySteel();
  const set = bakeAtlas(un.map((u, i) => ({ ...u, mat: i ? rs2 : skin })), 512, { ao: { rays: 32 } });
  const lum = (p: V3) => { const t = texel(set.baseColor, locateNear(un[0], p)); return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2]; };
  const under = lum([0, 1.022, 0.125]), open = lum([0, 0.94, 0.12]);
  ok("the skin just under the helmet rim is dark (the forge goblin's 'little ambient occlusion')", under < open - 45, under.toFixed(0) + " under the rim vs " + open.toFixed(0) + " on the open face");
}

// ---- 10. timings, on real sizes -----------------------------------------------------------------
const perf = typeof process !== "undefined" && process.env && process.env.MATERIAL_PERF !== "0";
if (perf) {
  const ms = (t0: number) => Math.round(performance.now() - t0);
  // A 7k-triangle organic part (a lumpy head-sized sphere), unwrapped.
  const head = sphere([0, 1.5, 0], [0.12, 0.15, 0.13], 60, 60, (x, y, z) => 0.06 * Math.sin(5 * x + 2 * y) * Math.cos(4 * z - y) + 0.03 * Math.sin(13 * y + 7 * x));
  const [H] = atlasArrays([head]);
  let t0 = performance.now();
  const hm = bakeMaterial(H, MATERIALS.skin({ color: "#9a8851" }), 1024, { ao: { rays: 16 } });
  const tSkin = ms(t0);
  t0 = performance.now();
  bakeMaterial(H, MATERIALS.rustySteel(), 1024, { ao: { rays: 16 } });
  const tRust = ms(t0);
  console.log("  bakeMaterial " + head.idx.length / 3 + " tris @1024, 16 rays: skin " + tSkin + " ms, rustySteel " + tRust + " ms (target < 6000)");
  ok("bakeMaterial 7k tris at 1024 with 16 rays < 6 s", Math.max(tSkin, tRust) < 6000, tSkin + " / " + tRust + " ms");
  ok("the head bake is not empty", hm.baseColor.data.some((v, i) => i % 4 === 0 && v > 60));
  // The Blender recipe: a 260k sculpt baked onto that 7k head (wrinkles as fine ridges).
  const sculpt = sphere([0, 1.5, 0], [0.12, 0.15, 0.13], 360, 362, (x, y, z) => 0.06 * Math.sin(5 * x + 2 * y) * Math.cos(4 * z - y) + 0.03 * Math.sin(13 * y + 7 * x) - 0.006 * Math.abs(Math.sin(60 * y + 9 * x)));
  t0 = performance.now();
  bakeMaterial(H, MATERIALS.skin({ color: "#9a8851" }), 1024, { high: sculpt, ao: { rays: 16 } });
  const tHigh = ms(t0);
  console.log("  bakeMaterial with a " + sculpt.idx.length / 3 + "-tri high mesh @1024, 16 rays: " + tHigh + " ms");

  // Twenty parts of a figure, ~60k triangles, one 2048 atlas.
  const parts: Array<M & { weight?: number; mat: SmartMaterial }> = [];
  const add2 = (m: M, mat: SmartMaterial, weight?: number) => parts.push({ ...m, mat, weight });
  const skin = MATERIALS.skin({ color: "#9a8851" }), rust = MATERIALS.rustySteel(), steel = MATERIALS.steel(), leather = MATERIALS.leather(), cloth = MATERIALS.cloth(), wood = MATERIALS.wood(), gold = MATERIALS.gold(), bone = MATERIALS.bone(), paint = MATERIALS.paintedSteel(), stone = MATERIALS.stone();
  add2(head, skin, 2);
  add2(sphere([0, 1.62, 0], [0.16, 0.14, 0.16], 96, 40), rust);                 // helmet
  add2(sphere([0, 1.1, 0], [0.22, 0.3, 0.16], 80, 48), paint);                  // breastplate
  add2(sphere([-0.26, 1.3, 0], [0.1, 0.07, 0.1], 64, 24), rust);                // pauldrons
  add2(sphere([0.26, 1.3, 0], [0.1, 0.07, 0.1], 64, 24), rust);
  add2(cylinder([-0.3, 1.0, 0], 0.05, 0.45, 48, 40), skin, 0.5);                // arms
  add2(cylinder([0.3, 1.0, 0], 0.05, 0.45, 48, 40), skin, 0.5);
  add2(torus([-0.3, 0.85, 0], 0.055, 0.02, 64, 24), leather);                   // bracers
  add2(torus([0.3, 0.85, 0], 0.055, 0.02, 64, 24), leather);
  add2(torus([0, 0.82, 0], 0.2, 0.03, 96, 24), leather);                        // belt
  add2(cylinder([0, 0.65, 0], 0.22, 0.3, 96, 32), cloth);                       // skirt
  add2(cylinder([-0.1, 0.3, 0], 0.07, 0.5, 48, 40), cloth, 0.5);                // legs
  add2(cylinder([0.1, 0.3, 0], 0.07, 0.5, 48, 40), cloth, 0.5);
  add2(box([-0.1, 0.04, 0.05], [0.1, 0.08, 0.2]), leather);                      // boots
  add2(box([0.1, 0.04, 0.05], [0.1, 0.08, 0.2]), leather);
  add2(cylinder([-0.45, 1.0, 0.1], 0.25, 0.03, 64, 4), wood);                   // shield
  add2(box([0.45, 1.1, 0.1], [0.05, 0.9, 0.01]), steel);                         // blade
  add2(box([0.45, 0.62, 0.1], [0.2, 0.03, 0.03]), gold);                         // crossguard
  add2(sphere([0.45, 0.5, 0.1], [0.03, 0.03, 0.03], 24, 12), bone);             // pommel
  add2(sphere([0.2, 0.05, 0.3], [0.06, 0.05, 0.06], 24, 12), stone);            // a rock by the feet
  const tris = parts.reduce((s, p) => s + p.idx.length / 3, 0);
  t0 = performance.now();
  const unwrapped = atlasArrays(parts);
  const tAtlas = ms(t0);
  t0 = performance.now();
  const set = bakeAtlas(unwrapped.map((u, i) => ({ ...u, mat: parts[i].mat })), 2048, { ao: { rays: 16 } });
  const tBake = ms(t0);
  const st = atlasStats(unwrapped);
  console.log("  atlasArrays " + parts.length + " parts, " + tris + " tris: " + tAtlas + " ms, " + st.islands + " islands, " + (st.fill * 100).toFixed(0) + "% full");
  console.log("  bakeAtlas " + parts.length + " parts @2048, 16 rays: " + tBake + " ms (target < 25000)");
  ok("bakeAtlas 20 parts at 2048 < 25 s", tBake < 25000, tBake + " ms");
  ok("the atlas bake fills all three maps", set.baseColor.width === 2048 && set.orm.data.length === 2048 * 2048 * 4 && set.normal.data.length === 2048 * 2048 * 4);
}

// ------------------------------------------------------------------
console.log(pass + " checks passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL " + f);
process.exit(fails.length ? 1 : 0);
