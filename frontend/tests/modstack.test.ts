// The modifiers the Mesh panel gained (ops.applyMod): array, deform, decimate, boolean, with real three.
import * as THREE from "three";
import { applyEdits, makeOps, setMapResolver } from "../src/components/engine/edit/ops";
import { editsAsCode, normaliseEdits } from "../src/components/engine/edit/kit";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number, e = 1e-3) => Math.abs(a - b) < e;
const ops = makeOps(THREE);

function volume(g: any): number {
  const p = g.attributes.position.array, idx = g.index ? g.index.array : null;
  const n = idx ? idx.length : p.length / 3;
  let v = 0;
  for (let t = 0; t < n; t += 3) {
    const [a, b, c] = [0, 1, 2].map((k) => (idx ? idx[t + k] : t + k) * 3);
    v += (p[a] * (p[b + 1] * p[c + 2] - p[b + 2] * p[c + 1]) - p[a + 1] * (p[b] * p[c + 2] - p[b + 2] * p[c]) + p[a + 2] * (p[b] * p[c + 1] - p[b + 1] * p[c])) / 6;
  }
  return v;
}
const tris = (g: any) => (g.index ? g.index.count : g.attributes.position.count) / 3;
function scene() {
  const root = new THREE.Group();
  const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
  box.name = "box";
  const cutter = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
  cutter.name = "cutter";
  cutter.position.set(0.5, 0.5, 0);
  root.add(box, cutter);
  root.updateMatrixWorld(true);
  return { root, box, cutter };
}

{
  const { root, box } = scene();
  const r = applyEdits(root, { parts: {}, mods: [{ op: "array", target: "box", args: { count: 3, ox: 1, oy: 0, oz: 0, relative: 1, weld: 0 } }] } as any, ops);
  box.geometry.computeBoundingBox();
  const b = box.geometry.boundingBox!;
  ok("array: three copies one width apart make it three wide", !r.errors.length && near(b.max.x - b.min.x, 3), [r.errors, b.max.x - b.min.x]);
  ok("...with three times the volume", near(volume(box.geometry), 3, 1e-2), volume(box.geometry));
}
{
  const { root, box, cutter } = scene();
  const r = applyEdits(root, { parts: {}, mods: [{ op: "boolean", target: "box", args: { op: 0, hide: 1, with: "cutter" } }] } as any, ops);
  ok("boolean subtract: the overlap (a quarter) is cut away", !r.errors.length && near(volume(box.geometry), 0.75, 1e-3), [r.errors, volume(box.geometry)]);
  ok("...and the cutter is hidden", cutter.visible === false);
}
{
  const { root, box } = scene();
  applyEdits(root, { parts: {}, mods: [{ op: "boolean", target: "box", args: { op: 2, hide: 0, with: "cutter" } }] } as any, ops);
  ok("boolean intersect: only the overlap is kept", near(volume(box.geometry), 0.25, 1e-3), volume(box.geometry));
}
{
  const { root, box } = scene();
  const before = volume(box.geometry);
  const r = applyEdits(root, { parts: {}, mods: [{ op: "boolean", target: "box", args: { op: 0, hide: 1 } }] } as any, ops);
  ok("a boolean with no cutter chosen leaves the mesh alone", near(volume(box.geometry), before), r.errors);
}
{
  const root = new THREE.Group();
  const ball = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32), new THREE.MeshStandardMaterial());
  ball.name = "ball";
  root.add(ball);
  const t0 = tris(ball.geometry);
  applyEdits(root, { parts: {}, mods: [{ op: "decimate", target: "ball", args: { ratio: 0.5 } }] } as any, ops);
  const t1 = tris(ball.geometry);
  ok("decimate 0.5: about half the triangles", t1 <= t0 * 0.55 && t1 >= t0 * 0.4, [t0, t1]);
  ball.geometry.computeBoundingSphere();
  ok("...and the shape keeps its size", near(ball.geometry.boundingSphere!.radius, 1, 0.05), ball.geometry.boundingSphere!.radius);
}
{
  const root = new THREE.Group();
  const bar = new THREE.Mesh(new THREE.BoxGeometry(0.4, 2, 0.4, 2, 10, 2), new THREE.MeshStandardMaterial());
  bar.name = "bar";
  root.add(bar);
  const p0 = Float32Array.from(bar.geometry.attributes.position.array as Float32Array);
  applyEdits(root, { parts: {}, mods: [{ op: "deform", target: "bar", args: { kind: 1, amount: 1, axis: 1 } }] } as any, ops);
  const p1 = bar.geometry.attributes.position.array as Float32Array;
  let moved = 0;
  for (let i = 0; i < Math.min(p0.length, p1.length); i++) if (Math.abs(p0[i] - p1[i]) > 1e-4) moved++;
  ok("deform twist: the bar is turned along its height", moved > 0, moved);
}
{
  // A texture set on a part: maps made from the project paths, tiled, and UVs where the part had none.
  const root = new THREE.Group();
  const g = new THREE.BoxGeometry(2, 1, 1);
  g.deleteAttribute("uv");
  const wall = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0x884422 }));
  wall.name = "wall";
  root.add(wall);
  const seen: string[] = [];
  setMapResolver((p) => { seen.push(p); return "/raw?path=" + p; });
  const edits = normaliseEdits({ parts: { wall: { maps: { baseColor: "public/a/rock_diff.jpg", normal: "public/a/rock_nor.jpg", arm: "public/a/rock_arm.jpg", tile: 2, bogus: 5 } } } });
  ok("the file keeps the maps and drops what is not a map", JSON.stringify(Object.keys(edits.parts.wall.maps!).sort()) === '["arm","baseColor","normal","tile"]', edits.parts.wall.maps);
  const r = applyEdits(root, edits, ops);
  const m = wall.material as THREE.MeshStandardMaterial;
  ok("maps: colour, normal and ARM become textures", !r.errors.length && !!m.map?.isTexture && !!m.normalMap?.isTexture && m.roughnessMap === m.metalnessMap && m.aoMap === m.roughnessMap, r.errors);
  ok("...the colour map is sRGB, the others are not", m.map!.colorSpace === THREE.SRGBColorSpace && m.normalMap!.colorSpace !== THREE.SRGBColorSpace);
  ok("...tiled and repeating", m.map!.repeat.x === 2 && m.map!.wrapS === THREE.RepeatWrapping);
  ok("...the colour goes white so the texture shows true", m.color.getHex() === 0xffffff);
  ok("...a part with no UVs gets box UVs", !!wall.geometry.attributes.uv && wall.geometry.attributes.uv.count === wall.geometry.attributes.position.count);
  ok("...the paths go through the resolver", seen.includes("public/a/rock_diff.jpg"), seen);
  const code = editsAsCode(edits);
  ok("the code export applies the maps too", code.includes('applyEdits(root, { parts: {"wall":{"maps"'), code);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
