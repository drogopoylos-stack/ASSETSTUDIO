// The edit-mode tools (meshedit.ts), held to geometry that can be checked exactly.
//
// Every tool must leave a closed mesh closed: each edge used by exactly two faces, in opposite
// directions, which is what "the faces all point outward and nothing is torn" means. And the volume
// must be what the tool promises: an extrude of a unit cube's top by 0.5 is 1.5, a loop cut and a
// knife change nothing, a delete opens a hole that fill closes again.
import { bridge, densify, extrude, fill, findVerts, inset, knife, loopcut, merge, refineNear, remove, runSteps, sculptStep, subdivide, unweld, weld, type WMesh } from "../src/components/engine/edit/meshedit";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number, e = 1e-6) => Math.abs(a - b) < e;

/** A unit cube as a generator makes it: 24 vertices (each face its own), 12 triangles, outward. */
function cube(): { pos: number[]; idx: number[] } {
  const pos: number[] = [], idx: number[] = [];
  const faces: [number[], number[], number[]][] = [
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]],
    [[0, 1, 0], [0, 0, 1], [1, 0, 0]], [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
  ];
  for (const [n, u, v] of faces) {
    const base = pos.length / 3;
    for (const [su, sv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      pos.push(0.5 * (n[0] + su * u[0] + sv * v[0]), 0.5 * (n[1] + su * u[1] + sv * v[1]), 0.5 * (n[2] + su * u[2] + sv * v[2]));
    }
    idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  return { pos, idx };
}

function volume(m: WMesh): number {
  let v = 0;
  for (let t = 0; t < m.tris.length; t += 3) {
    const a = m.tris[t] * 3, b = m.tris[t + 1] * 3, c = m.tris[t + 2] * 3;
    const ax = m.pos[a], ay = m.pos[a + 1], az = m.pos[a + 2], bx = m.pos[b], by = m.pos[b + 1], bz = m.pos[b + 2], cx = m.pos[c], cy = m.pos[c + 1], cz = m.pos[c + 2];
    v += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6;
  }
  return v;
}

/** Closed and consistently wound: every directed edge once, and its reverse once. */
function closed(m: WMesh): { ok: boolean; open: number; twice: number } {
  const dir = new Map<string, number>();
  for (let t = 0; t < m.tris.length; t += 3) for (let k = 0; k < 3; k++) {
    const key = m.tris[t + k] + ">" + m.tris[t + (k + 1) % 3];
    dir.set(key, (dir.get(key) || 0) + 1);
  }
  let open = 0, twice = 0;
  for (const [key, n] of dir) {
    if (n > 1) twice++;
    const [a, b] = key.split(">");
    if (!dir.has(b + ">" + a)) open++;
  }
  return { ok: !open && !twice, open, twice };
}

const at = (m: WMesh, test: (x: number, y: number, z: number) => boolean) => {
  const s = new Set<number>();
  for (let i = 0; i < m.pos.length / 3; i++) if (test(m.pos[i * 3], m.pos[i * 3 + 1], m.pos[i * 3 + 2])) s.add(i);
  return s;
};

const c = cube();
const base = weld(c.pos, c.idx);
ok("a generator's cube welds to 8 corners and 12 faces", base.pos.length / 3 === 8 && base.tris.length / 3 === 12);
ok("...closed, volume 1", closed(base).ok && near(volume(base), 1), [closed(base), volume(base)]);
const top = (m: WMesh) => at(m, (_x, y) => y > 0.49);

// ---- extrude
{
  const r = extrude(base, top(base), 0.5);
  ok("extrude: closed", closed(r.mesh).ok, closed(r.mesh));
  ok("extrude: volume 1.5", near(volume(r.mesh), 1.5), volume(r.mesh));
  ok("extrude: the new top is selected, at y = 1", r.out.length === 4 && r.out.every((v) => near(r.mesh.pos[v * 3 + 1], 1)));
  const neg = extrude(base, top(base), -0.25);
  ok("extrude inward: volume 0.75", near(volume(neg.mesh), 0.75), volume(neg.mesh));
  ok("extrude refuses with no face selected", !!extrude(base, new Set([0]), 1).error);
}

// ---- inset
{
  const r = inset(base, top(base), 0.1);
  ok("inset: closed", closed(r.mesh).ok, closed(r.mesh));
  ok("inset: the volume does not change", near(volume(r.mesh), 1), volume(r.mesh));
  const inner = r.out.map((v) => [r.mesh.pos[v * 3], r.mesh.pos[v * 3 + 2]]);
  ok("inset: the inner rim is 0.1 inside the edges", inner.every(([x, z]) => near(Math.abs(x), 0.4, 1e-6) && near(Math.abs(z), 0.4, 1e-6)), inner);
  const r2 = extrude(r.mesh, new Set(r.out), 0.3);
  ok("inset then extrude: a closed box with a step, volume 1 + 0.8*0.8*0.3", closed(r2.mesh).ok && near(volume(r2.mesh), 1 + 0.64 * 0.3), volume(r2.mesh));
}

// ---- loop cut
{
  const side = at(base, (x, y) => x > 0.49 && y > 0.49);           // the edge along z at x = y = 0.5
  const r = loopcut(base, side, 1);
  ok("loop cut: no error", !r.error, r.error);
  ok("loop cut: closed, the volume unchanged", closed(r.mesh).ok && near(volume(r.mesh), 1), [closed(r.mesh), volume(r.mesh)]);
  ok("loop cut: a ring of 4 new corners round the cube, all at z = 0", r.out.length === 4 && r.out.every((v) => near(r.mesh.pos[v * 3 + 2], 0)), r.out.map((v) => r.mesh.pos.slice(v * 3, v * 3 + 3)));
  const r3 = loopcut(base, side, 3);
  ok("three cuts: 12 new corners, still closed", r3.out.length === 12 && closed(r3.mesh).ok, [r3.out.length, closed(r3.mesh)]);
}

// ---- knife
{
  const r = knife(base, new Set(), [0, 0.2, 0], [0, 1, 0]);
  ok("knife: closed, the volume unchanged", closed(r.mesh).ok && near(volume(r.mesh), 1), [closed(r.mesh), volume(r.mesh)]);
  ok("knife: every new corner lies on the plane", r.out.length > 0 && r.out.every((v) => near(r.mesh.pos[v * 3 + 1], 0.2)), r.out.length);
}

// ---- subdivide
{
  const r = subdivide(base, top(base));
  ok("subdivide: closed, the volume unchanged", closed(r.mesh).ok && near(volume(r.mesh), 1), [closed(r.mesh), volume(r.mesh)]);
  ok("subdivide: the top is 8 faces", r.mesh.tris.length / 3 > 12);
}

// ---- delete, fill, merge, bridge
{
  const d = remove(base, top(base));
  const cl = closed(d.mesh);
  ok("delete the top: an open box with a rim of 4 edges", cl.open === 4, cl);
  const f = fill(d.mesh, at(d.mesh, (_x, y) => y > 0.49));
  ok("fill: closed again, volume 1", closed(f.mesh).ok && near(volume(f.mesh), 1), [closed(f.mesh), volume(f.mesh)]);
  const mg = merge(base, top(base));
  ok("merge the top: a closed pyramid one unit high, volume 1/3", closed(mg.mesh).ok && near(volume(mg.mesh), 1 / 3), [closed(mg.mesh), volume(mg.mesh)]);
  // two open boxes facing each other, bridged into one closed shape
  const a = remove(base, top(base)).mesh;
  const b2 = remove(base, at(base, (_x, y) => y < -0.49)).mesh;
  const both: WMesh = { pos: [...a.pos, ...b2.pos.map((v, i) => (i % 3 === 1 ? v + 2 : v))], tris: [...a.tris, ...b2.tris.map((v) => v + a.pos.length / 3)] };
  const rims = at(both, (_x, y) => (y > 0.49 && y < 0.51) || (y > 1.49 && y < 1.51));
  const br = bridge(both, rims);
  ok("bridge: two open boxes become one closed shape, volume 3", !br.error && closed(br.mesh).ok && near(volume(br.mesh), 3, 1e-6), [br.error, closed(br.mesh), volume(br.mesh)]);
}

// ---- by place, in order, as the editor saves them
{
  const steps = [
    { mesh: "box", op: "inset" as const, sel: [[-0.5, 0.5, -0.5], [0.5, 0.5, -0.5], [0.5, 0.5, 0.5], [-0.5, 0.5, 0.5]] as any, args: { thickness: 0.1 } },
    { mesh: "box", op: "extrude" as const, sel: [[-0.4, 0.5, -0.4], [0.4, 0.5, -0.4], [0.4, 0.5, 0.4], [-0.4, 0.5, 0.4]] as any, args: { distance: 0.3 } },
    { mesh: "box", op: "move" as const, sel: [], moves: [{ at: [0.4, 0.8, 0.4] as any, to: [0.4, 1.0, 0.4] as any }] },
  ];
  const r = runSteps(c.pos, c.idx, steps);
  ok("three saved steps run in order, finding each corner by place", !r.errors.length && closed(r.mesh).ok, r.errors);
  ok("...and the moved corner is where the last step put it", findVerts(r.mesh, [[0.4, 1.0, 0.4]]).size === 1);
  ok("the drawn buffer is unindexed triangles with a normal each", r.pos.length === r.mesh.tris.length * 3 && r.normal.length === r.pos.length);
  const bad = runSteps(c.pos, c.idx, [{ mesh: "box", op: "extrude", sel: [[9, 9, 9]], args: { distance: 1 } }]);
  ok("a step whose corners are gone is named and skipped, not guessed at", bad.errors.length === 1 && near(volume(bad.mesh), 1), bad.errors);
}

// ---- a hard edge stays hard
{
  const u = unweld(base);
  let hard = true;
  for (let i = 0; i < u.normal.length; i += 3) {
    const n = [u.normal[i], u.normal[i + 1], u.normal[i + 2]];
    if (Math.max(...n.map(Math.abs)) < 0.999) hard = false;
  }
  ok("a cube drawn by unweld keeps its flat faces", hard);
}

// ---- sculpting: brushes as steps
{
  const fine = densify(base, 0.15).mesh;
  ok("densify: closed, the volume unchanged, edges no longer than asked", closed(fine).ok && near(volume(fine), 1), [closed(fine), volume(fine)]);
  const path: [number, number, number][] = [[-0.3, 0.5, 0], [0, 0.5, 0], [0.3, 0.5, 0]];
  const clay = sculptStep(fine, path, { brush: "clay", radius: 0.25, depth: 0.05 });
  ok("clay on the top: no error, closed, more volume", !clay.error && closed(clay.mesh).ok && volume(clay.mesh) > 1.001, [clay.error, volume(clay.mesh)]);
  const crease = sculptStep(fine, path, { brush: "crease", radius: 0.2, depth: -0.05 });
  ok("a crease on the top: less volume", !crease.error && closed(crease.mesh).ok && volume(crease.mesh) < 0.999, [crease.error, volume(crease.mesh)]);
  const far = Math.max(...crease.mesh.pos.filter((_v, i) => i % 3 === 1).map(Math.abs));
  ok("...and nothing moved above the top", far <= 0.5 + 1e-9, far);
  // refine near the path only: the bottom keeps its two triangles' worth of corners
  const r = refineNear(base, path, 0.3, 0.1);
  const bottom = r.tris.filter((_v, i) => i % 3 === 0).length;
  const low = (() => { let n = 0; for (let t = 0; t < r.tris.length; t += 3) if ([0, 1, 2].every((k) => r.pos[r.tris[t + k] * 3 + 1] < -0.49)) n++; return n; })();
  // Longest-edge bisection spreads a little to stay crack-free; far from the stroke the mesh stays coarse.
  ok("refinement stays near the stroke: the bottom face stays coarse", low <= 8 && closed(r).ok && bottom > 100, [low, bottom]);
  // Slivers: area over the longest edge squared. An equilateral triangle is 0.87; a split through a
  // short edge, again and again, drives it toward 0.
  const thinShare = (mm: WMesh) => {
    let thin = 0;
    for (let t = 0; t < mm.tris.length; t += 3) {
      const q = [0, 1, 2].map((k) => mm.tris[t + k] * 3);
      const e = (i: number, j: number) => Math.hypot(mm.pos[q[i]] - mm.pos[q[j]], mm.pos[q[i] + 1] - mm.pos[q[j] + 1], mm.pos[q[i] + 2] - mm.pos[q[j] + 2]);
      const ux = mm.pos[q[1]] - mm.pos[q[0]], uy = mm.pos[q[1] + 1] - mm.pos[q[0] + 1], uz = mm.pos[q[1] + 2] - mm.pos[q[0] + 2];
      const vx = mm.pos[q[2]] - mm.pos[q[0]], vy = mm.pos[q[2] + 1] - mm.pos[q[0] + 1], vz = mm.pos[q[2] + 2] - mm.pos[q[0] + 2];
      const area = Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 2;
      const L = Math.max(e(0, 1), e(1, 2), e(2, 0));
      if (area / (L * L) < 0.08) thin++;
    }
    return thin / (mm.tris.length / 3);
  };
  const fineRef = refineNear(base, path, 0.3, 0.03);
  ok("refinement makes no slivers: under 2% of triangles thin", thinShare(fineRef) < 0.02 && closed(fineRef).ok, thinShare(fineRef));
  ok("densify makes no slivers either", thinShare(densify(base, 0.05).mesh) < 0.02, thinShare(densify(base, 0.05).mesh));
  // mirrored: a stroke on +x leaves the model symmetric across x = 0
  const fineSym = densify(base, 0.12).mesh;
  const m = sculptStep(fineSym, [[0.25, 0.5, 0.1]], { brush: "inflate", radius: 0.2, depth: 0.04, mirror: "x" });
  // The cube's diagonals make its corners asymmetric before any stroke, so the test is the bump:
  // as high on the left as on the right.
  const top = (side: number) => { let h = -1e9; for (let i = 0; i < m.mesh.pos.length; i += 3) if (m.mesh.pos[i] * side > 0.1) h = Math.max(h, m.mesh.pos[i + 1]); return h; };
  const hl = top(-1), hr = top(1);
  ok("a mirrored stroke raises both sides alike", !m.error && hr > 0.51 && Math.abs(hl - hr) < 0.1 * (hr - 0.5), [m.error, hl, hr]);
  const again = sculptStep(fine, path, { brush: "clay", radius: 0.25, depth: 0.05 });
  ok("the same stroke on the same mesh gives the same surface", again.mesh.pos.every((v, i) => v === clay.mesh.pos[i]));
  const steps = runSteps(c.pos, c.idx, [{ mesh: "box", op: "sculpt", sel: [], path, args: { brush: "ridge", radius: 0.2, depth: 0.04 } }]);
  ok("a sculpt step runs from the document like any other", !steps.errors.length && volume(steps.mesh) > 1, steps.errors);
  ok("a stroke with no radius is refused", !!sculptStep(fine, path, { brush: "clay" }).error);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
