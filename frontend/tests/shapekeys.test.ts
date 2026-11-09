// Shape keys (shapekeys.ts, ops.applyShapes), with real three: a key becomes a named relative morph
// target at its value, finds every copy of a corner by place, and is baked in when a modifier runs.
import * as THREE from "three";
import { applyEdits, makeOps } from "../src/components/engine/edit/ops";
import { cleanShapes, shapeDeltas, shapeMoves } from "../src/components/engine/edit/shapekeys";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number, e = 1e-6) => Math.abs(a - b) < e;

function scene() {
  const root = new THREE.Group();
  const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
  box.name = "box";
  root.add(box);
  return { root, box };
}

// The corner (0.5, 0.5, 0.5) has three copies in a BoxGeometry, one per face.
const corner: [number, number, number] = [0.5, 0.5, 0.5];
const smile = { mesh: "box", name: "smile", value: 0.5, moves: [{ at: corner, d: [0, 0.2, 0] as [number, number, number] }] };

{
  const { box } = scene();
  const r = shapeDeltas(box.geometry.attributes.position.array, smile.moves);
  let copies = 0;
  for (let i = 0; i < r.delta.length; i += 3) if (near(r.delta[i + 1], 0.2)) copies++;
  ok("every copy of a corner gets the move: three faces, three copies", copies === 3 && r.found === 1, [copies, r.found]);
}

{
  const { root, box } = scene();
  const out = applyEdits(root, { parts: {}, shapes: [smile] } as any);
  ok("a key becomes a named relative morph target", box.morphTargetDictionary?.smile === 0 && box.geometry.morphTargetsRelative === true, box.morphTargetDictionary);
  ok("...at its value", near(box.morphTargetInfluences![0], 0.5));
  ok("no errors", !out.errors.length, out.errors);
  const p = new THREE.Vector3();
  let moved = 0;
  for (let i = 0; i < box.geometry.attributes.position.count; i++) {
    box.getVertexPosition(i, p);
    if (near(p.x, 0.5) && near(p.z, 0.5) && near(p.y, 0.6)) moved++;
  }
  ok("the drawn corner moves by value x move: 0.5 x 0.2", moved === 3, moved);
  const again = applyEdits(root, { parts: {}, shapes: [{ ...smile, value: 1 }] } as any);
  ok("applying again replaces the key, it does not add a second", box.geometry.morphAttributes.position.length === 1 && !again.errors.length);
}

{
  const { root, box } = scene();
  const out = applyEdits(root, { parts: {}, shapes: [smile], mods: [{ op: "flip", target: "", args: {} }] } as any, makeOps(THREE));
  let top = -Infinity;
  const a = box.geometry.attributes.position.array as Float32Array;
  for (let i = 1; i < a.length; i += 3) top = Math.max(top, a[i]);
  ok("a modifier bakes the keys in at their values first", !out.errors.length && near(top, 0.6, 1e-4) && !(box.geometry.morphAttributes.position || []).length, [out.errors, top]);
}

{
  const { root } = scene();
  const out = applyEdits(root, { parts: {}, shapes: [{ ...smile, moves: [{ at: [9, 9, 9], d: [0, 1, 0] }] }] } as any);
  ok("a key whose corners are gone says so", out.errors.some((e) => /finds none/.test(e)), out.errors);
}

{
  const base = new Float32Array([0, 0, 0, 1, 0, 0, 0, 0, 0]);
  const shaped = new Float32Array([0, 0.1, 0, 1, 0, 0, 0, 0.1, 0]);
  const mv = shapeMoves(base, shaped);
  ok("moves from a shaped copy: one per corner, its copies counted once", mv.length === 1 && near(mv[0].d[1], 0.1), mv);
  const back = shapeDeltas(base, mv).delta;
  ok("...and back: the same shape", back.every((v, i) => near(base[i] + v, shaped[i])));
}

{
  const c = cleanShapes([smile, { ...smile }, { mesh: "box", name: "", value: 1, moves: [] }, { mesh: "box", name: "x", value: 5, moves: [{ at: [0, 0], d: [0, 0, 0] }] }]);
  ok("a file's keys are cleaned: duplicate names, no name, values out of range, short vectors", c.length === 2 && c[1].value === 1 && c[1].moves.length === 0, c);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
