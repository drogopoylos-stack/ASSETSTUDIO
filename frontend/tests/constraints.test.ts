// Bone constraints (rig.ts applyConstraints), with real three: each one does what Blender's does.
import * as THREE from "three";
import { applyPose, buildRig } from "../src/components/engine/edit/rig";
import type { BoneSpec } from "../src/components/engine/edit/kit";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number, e = 1e-3) => Math.abs(a - b) < e;
const tail = (rig: any, name: string) => {
  const b = rig.byName.get(name);
  b.updateWorldMatrix(true, false);
  return b.localToWorld(new THREE.Vector3(0, b.userData.length, 0));
};
const dirOf = (rig: any, name: string) => {
  const b = rig.byName.get(name);
  const h = b.getWorldPosition(new THREE.Vector3());
  return b.localToWorld(new THREE.Vector3(0, 1, 0)).sub(h).normalize();
};
const deg = (rig: any, name: string) => {
  const b = rig.byName.get(name);
  const e = new THREE.Euler().setFromQuaternion(b.userData.restQuat.clone().invert().multiply(b.quaternion), "XYZ");
  return [e.x, e.y, e.z].map((v) => (v * 180) / Math.PI);
};

{
  // A two-bone arm reaching for a goal it can reach.
  const specs: BoneSpec[] = [
    { name: "upper", parent: null, head: [0, 0, 0], tail: [0, 1, 0] },
    { name: "lower", parent: "upper", head: [0, 1, 0], tail: [0, 2, 0], constraints: [{ type: "ik", target: "goal", chain: 2 }] },
    { name: "goal", parent: null, head: [1.2, 1, 0], tail: [1.2, 1.2, 0] },
  ];
  const rig = buildRig(THREE, specs, "parts");
  applyPose(THREE, rig, {});
  const t = tail(rig, "lower");
  ok("IK: the chain's tip reaches the target's head", t.distanceTo(new THREE.Vector3(1.2, 1, 0)) < 0.02, t.toArray());
  ok("...and both bones of the chain are marked as moved by a constraint", rig.constrained!.has("upper") && rig.constrained!.has("lower"));
  ok("the pose it was given is kept apart from the result", JSON.stringify(rig.inputPose) === "{}");
}
{
  const specs: BoneSpec[] = [
    { name: "eye", parent: null, head: [0, 0, 0], tail: [0, 0.2, 0], constraints: [{ type: "track", target: "spot" }] },
    { name: "spot", parent: null, head: [3, 0, 0], tail: [3, 0.1, 0] },
  ];
  const rig = buildRig(THREE, specs, "parts");
  applyPose(THREE, rig, {});
  const d = dirOf(rig, "eye");
  ok("track: the bone points at the target", near(d.x, 1) && near(d.y, 0) && near(d.z, 0), d.toArray());
  specs[0].constraints![0].influence = 0.5;
  const rig2 = buildRig(THREE, specs, "parts");
  applyPose(THREE, rig2, {});
  const d2 = dirOf(rig2, "eye");
  ok("...half influence turns it half way (45 degrees)", near(Math.atan2(d2.x, d2.y) * 180 / Math.PI, 45, 0.5), d2.toArray());
}
{
  const specs: BoneSpec[] = [
    { name: "a", parent: null, head: [0, 0, 0], tail: [0, 1, 0] },
    { name: "b", parent: null, head: [1, 0, 0], tail: [1, 1, 0], constraints: [{ type: "copyRotation", target: "a", axes: [false, false, true] }] },
  ];
  const rig = buildRig(THREE, specs, "parts");
  applyPose(THREE, rig, { a: [0.3, 0, 0.5] });
  const db = deg(rig, "b"), da = deg(rig, "a");
  ok("copy rotation: the chosen axis is copied", near(db[2], da[2], 0.01), [db, da]);
  ok("...and the others are not", near(db[0], 0, 0.01), db);
}
{
  const specs: BoneSpec[] = [
    { name: "knee", parent: null, head: [0, 0, 0], tail: [0, -1, 0], constraints: [{ type: "limitRotation", min: [0, -180, -180], max: [30, 180, 180], axes: [true, false, false] }] },
  ];
  const rig = buildRig(THREE, specs, "parts");
  applyPose(THREE, rig, { knee: [Math.PI / 2, 0, 0] });
  ok("limit rotation: 90 degrees is held to 30", near(deg(rig, "knee")[0], 30, 0.01), deg(rig, "knee"));
  applyPose(THREE, rig, { knee: [-0.5, 0, 0] });
  ok("...and a bend the other way to 0", near(deg(rig, "knee")[0], 0, 0.01), deg(rig, "knee"));
  ok("the input pose is what the editor saves for it", near(rig.inputPose!.knee[0], -0.5));
}
{
  const specs: BoneSpec[] = [
    { name: "a", parent: null, head: [0, 0, 0], tail: [0, 1, 0] },
    { name: "b", parent: null, head: [1, 0, 0], tail: [1, 1, 0], constraints: [{ type: "copyRotation", target: "a", off: true }] },
  ];
  const rig = buildRig(THREE, specs, "parts");
  applyPose(THREE, rig, { a: [0, 0, 1] });
  ok("a constraint that is off does nothing", near(deg(rig, "b")[2], 0) && !rig.constrained!.size);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
