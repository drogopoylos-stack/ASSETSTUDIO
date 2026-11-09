// The edit-mode tools (meshedit.ts), held to geometry that can be checked exactly.
//
// Every tool must leave a closed mesh closed: each edge used by exactly two faces, in opposite
// directions, which is what "the faces all point outward and nothing is torn" means. And the volume
// must be what the tool promises: an extrude of a unit cube's top by 0.5 is 1.5, a loop cut and a
// knife change nothing, a delete opens a hole that fill closes again.
import { bridge, extrude, fill, findVerts, inset, knife, loopcut, merge, remove, runSteps, subdivide, unweld, weld, type WMesh } from "../src/components/engine/edit/meshedit";

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

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
