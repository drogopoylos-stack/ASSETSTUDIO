// Hands as one signed-distance field, and the grip placement, tested on the numbers that would be
// wrong if the geometry were: an empty channel along the handle, fingers wrapped round it, a thumb
// where the frame says, L the exact mirror of R, one closed surface, and a placement that lands
// the channel on a world handle.
//
// Run (from frontend/):
//   esbuild tests/hands.test.ts --bundle --format=esm --platform=node --outfile=../data/tmp/hands.test.mjs --log-level=warning && node ../data/tmp/hands.test.mjs

import { readFileSync } from "node:fs";
import { handField, gripPlacement, type Hand, type HandOpts, type HandPose } from "../src/components/engine/edit/hands";
import { isosurfaceArrays, checkArrays } from "../src/components/engine/edit/ops";

type V3 = [number, number, number];
let pass = 0;
const fails: string[] = [];
const notes: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => { if (cond) { pass++; return; } fails.push(name + (extra ? "  <- " + extra : "")); };

const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: V3): V3 => mul(a, 1 / (len(a) || 1));
const deg = (a: V3, b: V3) => (Math.acos(Math.max(-1, Math.min(1, dot(norm(a), norm(b))))) * 180) / Math.PI;
/** Deterministic points: a small LCG, so the test itself never uses Math.random. */
function lcg(seed: number) { let s = seed >>> 0; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; }; }

/** Rotate v by a column-major 4x4 (no translation). */
const rotM = (m: number[], v: V3): V3 => [m[0] * v[0] + m[4] * v[1] + m[8] * v[2], m[1] * v[0] + m[5] * v[1] + m[9] * v[2], m[2] * v[0] + m[6] * v[1] + m[10] * v[2]];
const xformM = (m: number[], v: V3): V3 => add(rotM(m, v), [m[12], m[13], m[14]]);
/** Rotate v by quaternion [x, y, z, w]. */
function rotQ(q: number[], v: V3): V3 {
  const [x, y, z, w] = q;
  const u: V3 = [x, y, z];
  const t = mul(cross(u, v), 2);
  return add(add(v, mul(t, w)), cross(u, t));
}

/** Connected pieces of a triangle mesh (vertices joined by triangles). */
function components(idx: Uint32Array, nv: number): number {
  const p = new Int32Array(nv).map((_, i) => i);
  const find = (a: number): number => { while (p[a] !== a) { p[a] = p[p[a]]; a = p[a]; } return a; };
  const used = new Uint8Array(nv);
  for (let t = 0; t < idx.length; t += 3) {
    const a = find(idx[t]), b = find(idx[t + 1]), c = find(idx[t + 2]);
    used[idx[t]] = used[idx[t + 1]] = used[idx[t + 2]] = 1;
    p[b] = a; p[find(c)] = a;
  }
  const roots = new Set<number>();
  for (let i = 0; i < nv; i++) if (used[i]) roots.add(find(i));
  return roots.size;
}

/** Degrees of a ring (radius r round the handle axis, in the plane x = at) that lie inside the hand. */
function wrapDegrees(h: Hand, at: number, r: number, samples = 720): number {
  const c = h.frame.gripCentre;
  let inside = 0;
  for (let i = 0; i < samples; i++) {
    const a = (i / samples) * Math.PI * 2;
    if (h.field(at, c[1] + Math.sin(a) * r, c[2] + Math.cos(a) * r) < 0) inside++;
  }
  return (inside / samples) * 360;
}

// ---- the grip channel ----------------------------------------------------------------------
for (const side of ["R", "L"] as const) for (const fingers of [4, 3] as const) {
  const W = 0.05, g = 0.0065;
  const h = handField({ side, pose: "grip", size: W, gripRadius: g, fingers });
  const tag = `grip ${side}${fingers}`;
  const { gripCentre: c, gripAxis: ax } = h.frame;
  ok(tag + ": the frame reports the channel's radius", Math.abs(h.frame.gripRadius - g) < 1e-12, String(h.frame.gripRadius));
  ok(tag + ": gripAxis is the unit x axis, toward the thumb side", Math.abs(ax[0] - (side === "R" ? 1 : -1)) < 1e-12 && Math.abs(ax[1]) + Math.abs(ax[2]) < 1e-12, JSON.stringify(ax));
  // The handle's axis, right through the box and out the other side, never enters the hand.
  let minAxis = Infinity;
  const span = (h.max[0] - h.min[0]) * 0.7;
  for (let s = -1; s <= 1.0001; s += 1 / 200) minAxis = Math.min(minAxis, h.field(c[0] + ax[0] * span * s, c[1], c[2]));
  ok(tag + ": the handle's axis lies in an empty channel (field > 0 all along it)", minAxis > 0, "min " + minAxis);
  // ...and a whole handle of gripRadius fits (a hair of press, 2%, is allowed: flesh on wood).
  let minBar = Infinity;
  for (let s = -1; s <= 1.0001; s += 1 / 60) for (let a = 0; a < 360; a += 10) {
    const r = g * 0.97, t = (a * Math.PI) / 180;
    minBar = Math.min(minBar, h.field(c[0] + ax[0] * span * s, c[1] + Math.sin(t) * r, c[2] + Math.cos(t) * r));
  }
  ok(tag + ": a handle of gripRadius fits the channel", minBar > 0, "min " + minBar);
  // The fingers wrap it: a ring just outside the handle, in each finger's plane.
  const covers = h.joints.fingers.map((f) => wrapDegrees(h, f[0][0], g * 1.04));
  const mid = covers[1];
  ok(tag + ": the hand closes round the handle (>= 270 degrees, middle finger's plane)", mid >= 270, covers.map((v) => v.toFixed(0)).join("/"));
  ok(tag + ": every finger's plane is wrapped >= 250 degrees", Math.min(...covers) >= 250, covers.map((v) => v.toFixed(0)).join("/"));
  if (side === "R" && fingers === 4) notes.push("wrap degrees per finger plane (grip R4, ring at 1.04 x gripRadius): " + covers.map((v) => v.toFixed(0)).join(", "));
  // Every finger's pad touches the handle: some point of each finger's middle phalanx is within
  // a finger radius of the handle surface.
  for (let i = 0; i < h.joints.fingers.length; i++) {
    const [, pip, dip] = h.joints.fingers[i];
    const m = mul(add(pip, dip), 0.5);
    const d = Math.hypot(m[1] - c[1], m[2] - c[2]) - g;
    ok(`${tag}: finger ${i}'s middle phalanx rests on the handle`, d < h.joints.fingerRadii[i][1] * 1.05 && d > 0, (d * 1000).toFixed(2) + " mm");
  }
}

// A bigger and a smaller handle still get a channel of their size and a closed hand.
for (const g of [0.004, 0.009]) {
  const h = handField({ side: "R", pose: "grip", size: 0.05, gripRadius: g });
  const c = h.frame.gripCentre;
  let minAxis = Infinity;
  for (let s = -1; s <= 1; s += 1 / 100) minAxis = Math.min(minAxis, h.field(c[0] + s * 0.06, c[1], c[2]));
  ok(`grip r=${g * 1000}mm: empty channel`, minAxis > 0, String(minAxis));
  const cov = wrapDegrees(h, h.joints.fingers[1][0][0], g * 1.04);
  ok(`grip r=${g * 1000}mm: wrapped >= 250 degrees`, cov >= 250, cov.toFixed(0));
  notes.push(`grip r=${(g * 1000).toFixed(1)} mm (size 50 mm): middle-finger wrap ${cov.toFixed(0)} degrees, flexion ${JSON.stringify(h.joints.flexion[1])}`);
}

// ---- the thumb is where the frame says -----------------------------------------------------
const POSES: HandPose[] = ["grip", "fist", "open", "relaxed", "point"];
for (const pose of POSES) for (const side of ["R", "L"] as const) {
  const h = handField({ side, pose, size: 0.05 });
  const t = h.frame.thumbTip;
  const tr = h.joints.thumbRadii[3];
  ok(`${pose} ${side}: the thumb tip is deep inside the surface`, h.field(t[0], t[1], t[2]) < -0.5 * tr, String(h.field(t[0], t[1], t[2])));
  const part = h.partAt(t[0], t[1], t[2]);
  ok(`${pose} ${side}: partAt(thumbTip) is the thumb`, part === "thumb" || part === "nail", part);
  // The thumb's joints are inside, and the thumb's base is on the thumb side (+x for R).
  const inside = h.joints.thumb.slice(0, 4).every((p) => h.field(p[0], p[1], p[2]) < 0);
  ok(`${pose} ${side}: every thumb joint is inside the surface`, inside);
  const sgn = side === "R" ? 1 : -1;
  ok(`${pose} ${side}: the thumb grows from the thumb side`, h.joints.thumb[1][0] * sgn > 0, String(h.joints.thumb[1][0]));
  // A right hand is a right hand: the thumb is on the side back x fingers points to (the left
  // hand the other side), whatever the pose.
  const thumbSide = dot(sub(h.joints.thumb[1], h.frame.palmCentre), cross(h.frame.back, h.frame.fingers));
  ok(`${pose} ${side}: chirality (the thumb on the back x fingers side for R, the other side for L)`, side === "R" ? thumbSide > 0 : thumbSide < 0, String(thumbSide));
  ok(`${pose} ${side}: gripAxis points to the thumb side`, dot(sub(h.joints.thumb[1], h.frame.palmCentre), h.frame.gripAxis) > 0);
  // Every finger tip is inside too.
  ok(`${pose} ${side}: every finger tip is inside`, h.frame.tips.every((p) => h.field(p[0], p[1], p[2]) < 0));
}

// ---- L is exactly R mirrored ---------------------------------------------------------------
for (const pose of POSES) {
  const R = handField({ side: "R", pose, size: 0.05 });
  const L = handField({ side: "L", pose, size: 0.05 });
  const rnd = lcg(7);
  let worst = 0;
  for (let i = 0; i < 3000; i++) {
    const p: V3 = [R.min[0] + rnd() * (R.max[0] - R.min[0]), R.min[1] + rnd() * (R.max[1] - R.min[1]), R.min[2] + rnd() * (R.max[2] - R.min[2])];
    worst = Math.max(worst, Math.abs(R.field(p[0], p[1], p[2]) - L.field(-p[0], p[1], p[2])));
  }
  ok(`${pose}: L(-x, y, z) === R(x, y, z) exactly`, worst === 0, String(worst));
  ok(`${pose}: L's box is R's mirrored`, L.min[0] === -R.max[0] && L.max[0] === -R.min[0] && L.min[1] === R.min[1] && L.max[2] === R.max[2]);
  const mir = (v: V3): V3 => [-v[0], v[1], v[2]];
  const fr = R.frame, fl = L.frame;
  const same = (a: V3, b: V3) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) < 1e-15;
  ok(`${pose}: L's frame is R's mirrored`, same(fl.gripCentre, mir(fr.gripCentre)) && same(fl.gripAxis, mir(fr.gripAxis)) && same(fl.knuckles, mir(fr.knuckles))
    && same(fl.thumbTip, mir(fr.thumbTip)) && same(fl.palm, mir(fr.palm)) && fl.tips.every((t, i) => same(t, mir(fr.tips[i]))));
  ok(`${pose}: partAt mirrors too`, L.partAt(-fr.thumbTip[0], fr.thumbTip[1], fr.thumbTip[2]) === R.partAt(fr.thumbTip[0], fr.thumbTip[1], fr.thumbTip[2]));
}
{
  // The polygonised meshes agree: same count, same volume, mirrored bounds.
  const R = handField({ side: "R", pose: "grip", size: 0.05 }), L = handField({ side: "L", pose: "grip", size: 0.05 });
  const mr = isosurfaceArrays(R.field, R.min, R.max, 48), ml = isosurfaceArrays(L.field, L.min, L.max, 48);
  const cr = checkArrays(mr.pos, mr.idx), cl = checkArrays(ml.pos, ml.idx);
  ok("grip: L's mesh has R's triangle count", ml.idx.length === mr.idx.length, ml.idx.length + " vs " + mr.idx.length);
  ok("grip: L's mesh has R's volume", Math.abs(cl.volume - cr.volume) <= 1e-6 * Math.abs(cr.volume) + 1e-12, cl.volume + " vs " + cr.volume);
  ok("grip: L's mesh bounds are R's mirrored", Math.abs(cl.bounds.min[0] + cr.bounds.max[0]) < 1e-6 && Math.abs(cl.bounds.max[0] + cr.bounds.min[0]) < 1e-6);
}

// ---- one closed surface, every pose and variant ---------------------------------------------
{
  const variants: HandOpts[] = [];
  for (const pose of POSES) for (const fingers of [4, 3] as const) for (const chunk of [0, 0.5, 1]) variants.push({ side: "R", pose, fingers, chunk, size: 0.05 });
  variants.push({ side: "L", pose: "fist", size: 0.08, thumb: "up" });
  variants.push({ side: "R", pose: "grip", size: 0.05, thumb: "along" });
  variants.push({ side: "R", pose: "grip", size: 0.05, thumb: "up" });
  variants.push({ side: "R", pose: "fist", size: 0.05, thumb: "along" });
  variants.push({ side: "R", pose: "open", size: 0.05, thumb: "wrap" });
  variants.push({ side: "R", pose: "open", size: 0.05, thumb: "along" });
  variants.push({ side: "R", pose: "relaxed", size: 0.05, curl: [0, 1, 0.5, 1] });
  variants.push({ side: "R", pose: "relaxed", size: 0.05, nails: false, knuckles: 1, fingerLength: 1.1 });
  variants.push({ side: "R", pose: "open", size: 0.05, wrist: 0 });
  variants.push({ side: "R", pose: "open", size: 0.05, curl: [1, 1, 1, 1] });
  variants.push({ side: "R", pose: "open", size: 0.05, curl: [1, 0, 1, 0] });
  variants.push({ side: "L", pose: "grip", size: 0.05, curl: [0] });
  variants.push({ side: "R", pose: "grip", size: 0.05, gripRadius: 0.02 });
  let nm = 0, worst = "";
  for (const o of variants) {
    const h = handField(o);
    const m = isosurfaceArrays(h.field, h.min, h.max, h.grid(64));
    const c = checkArrays(m.pos, m.idx);
    const k = components(m.idx, m.pos.length / 3);
    const tag = JSON.stringify(o);
    ok(tag + ": polygonises with no open edges", c.holes === 0 && c.boundaryEdges === 0, "holes " + c.holes);
    ok(tag + ": as ONE connected surface", k === 1, k + " pieces");
    ok(tag + ": wound outward (positive volume)", c.volume > 0 && !c.inverted, String(c.volume));
    ok(tag + ": the surface lies inside the box it came with", c.bounds.min.every((v, i) => v > h.min[i]) && c.bounds.max.every((v, i) => v < h.max[i]));
    if (c.nonManifoldEdges > nm) { nm = c.nonManifoldEdges; worst = tag; }
  }
  notes.push(`${variants.length} variants closed; most non-manifold edges in one: ${nm}${worst ? " (" + worst + ")" : ""}`);
  const t = handField({ side: "R", pose: "fist", size: 0.05, nails: false });
  const r = lcg(3);
  let nailSeen = false;
  for (let i = 0; i < 20000 && !nailSeen; i++) nailSeen = t.partAt(t.min[0] + r() * (t.max[0] - t.min[0]), t.min[1] + r() * (t.max[1] - t.min[1]), t.min[2] + r() * (t.max[2] - t.min[2])) === "nail";
  ok("nails: false leaves no nail anywhere", !nailSeen);
  // Walk up from the middle of the middle finger's last phalanx to its back surface: just under
  // the surface there is nail.
  for (const pose of ["open", "fist", "grip"] as HandPose[]) {
    const wn = handField({ side: "R", pose, size: 0.05 });
    const f = wn.joints.fingers[1];
    const c = mul(add(f[2], f[3]), 0.5);
    // the back of the last phalanx: its direction d turned a quarter toward the back of the hand
    // in the finger's (y, z) plane — at d = +z the back is +y, at d = -y it is +z
    const d = norm(sub(f[3], f[2]));
    const back = norm([0, d[2], -d[1]]);
    let s = 0;
    while (s < 0.02 && wn.field(c[0] + back[0] * s, c[1] + back[1] * s, c[2] + back[2] * s) < 0) s += 0.00005;
    const q = add(c, mul(back, s - 0.0002));
    const part = wn.partAt(q[0], q[1], q[2]);
    ok(`nails: a nail sits on the back of the middle finger's tip (${pose})`, part === "nail", part + " at " + (s * 1000).toFixed(2) + " mm");
  }
}

// ---- options do what they say ---------------------------------------------------------------
{
  const v = (o: HandOpts) => { const h = handField(o); const m = isosurfaceArrays(h.field, h.min, h.max, h.grid(48)); return checkArrays(m.pos, m.idx).volume; };
  const thin = v({ side: "R", pose: "open", chunk: 0 }), toy = v({ side: "R", pose: "open", chunk: 1 });
  ok("chunk: a toy hand is fatter than a thin one", toy > thin * 1.15, thin + " vs " + toy);
  const s = handField({ side: "R", pose: "open", size: 0.05 }), b = handField({ side: "R", pose: "open", size: 0.1 });
  ok("size: twice the palm width, twice every length", Math.abs(b.frame.tips[1][2] / s.frame.tips[1][2] - 2) < 1e-9);
  const f3 = handField({ side: "R", pose: "fist", fingers: 3 });
  ok("fingers: 3 gives three fingers", f3.joints.fingers.length === 3 && f3.frame.tips.length === 3 && f3.fingers === 3);
  const long = handField({ side: "R", pose: "open", fingerLength: 1.2 }), short = handField({ side: "R", pose: "open", fingerLength: 0.7 });
  ok("fingerLength: longer fingers reach further", long.frame.tips[1][2] > short.frame.tips[1][2] + 0.005);
  const curled = handField({ side: "R", pose: "open", curl: [1, 0, 0, 0] });
  ok("curl: one finger curled, the others open", curled.joints.flexion[0][1] > 80 && curled.joints.flexion[1][1] < 10);
  const point = handField({ side: "R", pose: "point" });
  ok("point: index straight, the rest curled", point.joints.flexion[0][1] < 10 && point.joints.flexion.slice(1).every((f) => f[1] > 80));
  const open = handField({ side: "R", pose: "open" });
  ok("open: the fingers point along +z", open.frame.tips.every((t) => t[2] > open.frame.knuckleRow[2]));
  ok("open: the palm faces -y, the back +y", open.frame.palm[1] === -1 && open.frame.back[1] === 1);
  const fist = handField({ side: "R", pose: "fist" });
  ok("fist: the finger tips come back under the palm", fist.frame.tips.every((t) => t[2] < fist.frame.knuckleRow[2] && t[1] < 0));
  ok("fist: the knuckles face between +y and +z", fist.frame.knuckles[1] > 0.2 && fist.frame.knuckles[2] > 0.2 && Math.abs(fist.frame.knuckles[0]) < 1e-12);
  const wrist = handField({ side: "R", pose: "open", size: 0.05 });
  ok("wrist: the stub ends in a closed cap past the wrist (no open cuff)", wrist.field(0, 0, -0.02) < 0 && wrist.field(0, 0, -0.035) > 0);
  ok("frame: the wrist joint is the origin and the forearm runs -z", JSON.stringify(wrist.frame.wrist) === "[0,0,0]" && wrist.frame.forearm[2] === -1);
  // determinism
  const a = handField({ side: "R", pose: "grip", size: 0.05 }), b2 = handField({ side: "R", pose: "grip", size: 0.05 });
  const r = lcg(11);
  let same = true;
  for (let i = 0; i < 500; i++) { const x = r() * 0.1 - 0.05, y = r() * 0.1 - 0.05, z = r() * 0.1 - 0.03; if (a.field(x, y, z) !== b2.field(x, y, z)) same = false; }
  ok("deterministic: the same options give the same field", same);
  const src = readFileSync(new URL("../../frontend/src/components/engine/edit/hands.ts", import.meta.url), "utf8");
  ok("hands.ts uses no randomness, no three, no DOM, and does not import ops", !/Math\.random|from\s+["']three["']|document\.|window\.|from\s+["']\.\/ops["']/.test(src));
  ok("every export has a JSDoc comment", src.split("\n").every((l, i, all) => !/^export /.test(l) || /\*\/\s*$/.test(all[i - 1] || "")));
}

// ---- gripPlacement ---------------------------------------------------------------------------
{
  const rnd = lcg(42);
  const unit = (): V3 => { for (;;) { const v: V3 = [rnd() * 2 - 1, rnd() * 2 - 1, rnd() * 2 - 1]; const l = len(v); if (l > 0.2 && l <= 1) return mul(v, 1 / l); } };
  let worstAxis = 0, worstPos = 0, worstFace = 0, worstQ = 0;
  for (const side of ["R", "L"] as const) for (const pose of ["grip", "fist", "open"] as HandPose[]) {
    const h = handField({ side, pose, size: 0.05 });
    for (let i = 0; i < 200; i++) {
      const a: V3 = [rnd() - 0.5, rnd() + 0.2, rnd() - 0.5];
      const b = add(a, mul(unit(), 0.05 + rnd() * 0.3));
      const t = rnd();
      const face = i % 25 === 0 ? mul(sub(b, a), -2) : unit();          // every 25th: face parallel to the handle
      const p = gripPlacement({ a, b, t, face, hand: h });
      const H = norm(sub(b, a));
      const axisW = rotM(p.matrix, h.frame.gripAxis);
      worstAxis = Math.max(worstAxis, deg(axisW, H));
      const gc = xformM(p.matrix, h.frame.gripCentre);
      const want = add(a, mul(sub(b, a), t));
      worstPos = Math.max(worstPos, len(sub(gc, want)));
      if (!p.fallback) {
        const fperp = sub(face, mul(H, dot(face, H)));
        worstFace = Math.max(worstFace, deg(rotM(p.matrix, h.frame.knuckles), fperp));
      } else {
        ok("fallback only when face is parallel to the handle", len(sub(face, mul(H, dot(face, H)))) < 1e-3 * len(face));
        const k = rotM(p.matrix, h.frame.knuckles);
        ok("fallback still turns the knuckles off the handle", Math.abs(dot(k, H)) < 1e-9);
      }
      // quaternion and matrix are the same rotation; the wrist is the placed wrist
      for (const v of [[1, 0, 0], [0, 1, 0], [0, 0, 1]] as V3[]) worstQ = Math.max(worstQ, len(sub(rotQ(p.quaternion, v), rotM(p.matrix, v))));
      ok("the wrist is the placed wrist joint", len(sub(p.wrist, xformM(p.matrix, h.frame.wrist))) < 1e-12);
      ok("position is the matrix's translation", len(sub(p.position, [p.matrix[12], p.matrix[13], p.matrix[14]])) < 1e-15);
    }
  }
  ok("gripPlacement: the grip axis lies along the handle within 1 degree", worstAxis < 1, worstAxis.toFixed(6));
  ok("gripPlacement: the grip centre lies on the handle within 1 mm", worstPos < 0.001, worstPos.toExponential(2));
  ok("gripPlacement: the knuckles within 5 degrees of face", worstFace < 5, worstFace.toFixed(6));
  ok("gripPlacement: quaternion == matrix", worstQ < 1e-9, String(worstQ));
  notes.push(`gripPlacement over 1200 random handles, both sides: axis error ${worstAxis.toExponential(1)} deg, centre ${worstPos.toExponential(1)} m, knuckles ${worstFace.toExponential(1)} deg`);

  // End to end: a world handle through the placed hand's channel is empty, and the hand's
  // fingers are round it — sampled in world space through the inverse placement.
  for (const side of ["R", "L"] as const) {
    const h = handField({ side, pose: "grip", size: 0.05, gripRadius: 0.006 });
    const a: V3 = [0.2, 0.9, -0.1], b: V3 = [0.25, 0.7, 0.15];
    const p = gripPlacement({ a, b, t: 0.4, face: [0, 0, 1], hand: h });
    const m = p.matrix;
    const toLocal = (w: V3): V3 => { const d = sub(w, [m[12], m[13], m[14]]); return [m[0] * d[0] + m[1] * d[1] + m[2] * d[2], m[4] * d[0] + m[5] * d[1] + m[6] * d[2], m[8] * d[0] + m[9] * d[1] + m[10] * d[2]]; };
    let minF = Infinity;
    for (let s = 0; s <= 1.0001; s += 0.01) { const l = toLocal(add(a, mul(sub(b, a), s))); minF = Math.min(minF, h.field(l[0], l[1], l[2])); }
    ok(`end to end ${side}: the world handle runs through the placed hand's empty channel`, minF > 0, String(minF));
    const H = norm(sub(b, a)), P = add(a, mul(sub(b, a), 0.4));
    const u = norm(cross(H, [0, 1, 0])), v = cross(H, u);
    let inside = 0;
    for (let i = 0; i < 360; i++) {
      const t = (i * Math.PI) / 180, r = 0.006 * 1.04;
      const w = add(P, add(mul(u, Math.cos(t) * r), mul(v, Math.sin(t) * r)));
      const l = toLocal(add(w, mul(H, h.joints.fingers[1][0][0] * (side === "R" ? 1 : -1))));
      if (h.field(l[0], l[1], l[2]) < 0) inside++;
    }
    ok(`end to end ${side}: the placed fingers close round the world handle (>= 270 degrees)`, inside >= 270, String(inside));
    ok(`end to end ${side}: the knuckles face +z`, rotM(m, h.frame.knuckles)[2] > 0.9 * len(sub([0, 0, 1], mul(H, H[2]))));
    // The thumb side of the fist faces b (a sword's blade leaves on the thumb side).
    ok(`end to end ${side}: the thumb side faces b`, dot(rotM(m, h.frame.gripAxis), H) > 0.999);
  }
}

// ---- speed -----------------------------------------------------------------------------------
{
  const times: string[] = [];
  let worst = 0;
  for (const pose of ["grip", "fist", "open"] as HandPose[]) {
    const h = handField({ side: "R", pose, size: 0.05 });
    const t0 = performance.now();
    const m = isosurfaceArrays(h.field, h.min, h.max, 64);
    const dt = performance.now() - t0;
    worst = Math.max(worst, dt);
    times.push(`${pose} ${dt.toFixed(0)} ms (${m.idx.length / 3} tris)`);
  }
  ok("a 64^3 isosurface of one hand takes under 1.5 s", worst < 1500, worst.toFixed(0) + " ms");
  const t0 = performance.now();
  for (let i = 0; i < 20; i++) handField({ side: i % 2 ? "L" : "R", pose: POSES[i % 5], size: 0.05 });
  notes.push("64^3 isosurface: " + times.join(", ") + `; handField build ${((performance.now() - t0) / 20).toFixed(2)} ms`);
}

for (const n of notes) console.log("  " + n);
if (fails.length) {
  console.log(`hands: ${pass} passed, ${fails.length} FAILED`);
  for (const f of fails.slice(0, 60)) console.log("  FAIL " + f);
  process.exit(1);
}
console.log(`hands: all ${pass} passed`);
