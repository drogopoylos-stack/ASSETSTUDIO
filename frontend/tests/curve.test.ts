// Curves (curve.ts): a placed recipe of control points swept into a tube, with real three for the mesh.
import * as THREE from "three";
import { cleanCurve, curveArrays, defaultCurve, extendCurve } from "../src/components/engine/edit/curve";
import { applyPlaced, curveGeometry } from "../src/components/engine/edit/ops";
import { normaliseEdits } from "../src/components/engine/edit/kit";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number, e = 1e-3) => Math.abs(a - b) < e;

/** Distance of each vertex from the x axis, at the two ends of a straight curve along x. */
function radiusAt(a: { pos: Float32Array }, x: number): number {
  let r = 0;
  for (let i = 0; i < a.pos.length; i += 3) if (near(a.pos[i], x, 1e-4)) r = Math.max(r, Math.hypot(a.pos[i + 1], a.pos[i + 2]));
  return r;
}
const straight = { points: [[0, 0, 0], [1, 0, 0], [2, 0, 0]] as [number, number, number][], smooth: 0 };

{
  const c = cleanCurve({ points: [[0, 0, 0], [1, "x", 0], [1, 1], [2, 0, 0]], radius: -4, sides: 999, profile: "star", smooth: 2.6 });
  ok("a file's curve: bad points dropped, numbers held in range, an unknown profile is round",
    !!c && c.points.length === 2 && c.radius! > 0 && c.sides === 64 && c.profile === "round" && c.smooth === 3, c);
  ok("one point is no curve", cleanCurve({ points: [[0, 0, 0]] }) === null);
  ok("a closed curve needs three", cleanCurve({ points: [[0, 0, 0], [1, 0, 0]], closed: true }) === null);
}
{
  const a = curveArrays({ ...straight, radius: 0.1 });
  ok("a round tube has the radius it was given", near(radiusAt(a, 1), 0.1, 1e-3), radiusAt(a, 1));
  const t = curveArrays({ ...straight, radius: 0.1, taper: [1, 0.25] });
  ok("a taper narrows it toward the end", near(radiusAt(t, 0), 0.1, 2e-3) && near(radiusAt(t, 2), 0.025, 2e-3), [radiusAt(t, 0), radiusAt(t, 2)]);
  const sq = curveArrays({ ...straight, radius: 0.1, profile: "square" });
  ok("a square profile reaches its corners at radius x sqrt 2", near(radiusAt(sq, 1), 0.1 * Math.SQRT2, 2e-3), radiusAt(sq, 1));
  const flat = curveArrays({ ...straight, radius: 0.1, profile: "flat" });
  let thin = Infinity;
  for (let i = 0; i < flat.pos.length; i += 3) if (near(flat.pos[i], 1, 1e-4)) thin = Math.min(thin, Math.max(Math.abs(flat.pos[i + 1]), Math.abs(flat.pos[i + 2])));
  ok("a strap is wide and thin", near(radiusAt(flat, 1), Math.hypot(0.1, 0.02), 2e-3), radiusAt(flat, 1));
}
{
  const open = curveArrays(defaultCurve());
  const loop = curveArrays({ ...defaultCurve(), closed: true });
  ok("indices stay inside the vertices", Array.from(open.idx).every((i) => i < open.pos.length / 3) && Array.from(loop.idx).every((i) => i < loop.pos.length / 3));
  const smooth = curveArrays({ ...defaultCurve(), smooth: 8 }), sharp = curveArrays({ ...defaultCurve(), smooth: 0 });
  ok("smooth adds rings between the points", smooth.pos.length > sharp.pos.length * 4, [smooth.pos.length, sharp.pos.length]);
}
{
  const c = { points: [[0, 0, 0], [1, 0, 0]] as [number, number, number][] };
  const end = extendCurve(c);
  ok("a new point at the end goes on in the same direction", end.length === 3 && end[2][0] === 2);
  const mid = extendCurve(c, 0);
  ok("a new point after a middle one goes half way to the next", mid.length === 3 && mid[1][0] === 0.5 && mid[2][0] === 1);
}
{
  const e = normaliseEdits({ placed: [
    { id: "c1", name: "rail", ref: { kind: "curve", curve: { points: [[0, 0, 0], [1, 1, 0]], radius: 0.2 } } },
    { id: "c2", name: "bad", ref: { kind: "curve", curve: { points: [[0, 0, 0]] } } },
  ] });
  ok("the sidecar keeps a curve and drops one that cannot be made", e.placed?.length === 1 && e.placed[0].ref.curve?.radius === 0.2, e.placed);
}
{
  const root = new THREE.Group();
  const r = await applyPlaced(root, [{ id: "c1", name: "rail", ref: { kind: "curve", curve: { points: [[0, 0, 0], [2, 0, 0]], radius: 0.05 }, color: "#ff0000" }, pos: [0, 1, 0], rot: [0, 0, 0], scale: [1, 1, 1] }] as any,
    THREE, async () => null);
  const m = root.children[0] as THREE.Mesh;
  ok("the editor places a curve: a mesh marked as a curve, at its place, in its colour",
    r.added === 1 && m?.isMesh && m.userData.studioCurve && m.position.y === 1 && (m.material as any).color.getHexString() === "ff0000", r.errors);
  m.geometry.computeBoundingBox();
  ok("...two metres long", near(m.geometry.boundingBox!.max.x - m.geometry.boundingBox!.min.x, 2, 1e-3), m.geometry.boundingBox);
  const g = curveGeometry(THREE, { points: [[0, 0, 0], [0, 1, 0], [1, 1, 0]], closed: true });
  ok("a closed curve makes a geometry with normals and UVs", !!g.attributes.normal && !!g.attributes.uv && g.index!.count > 0);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
