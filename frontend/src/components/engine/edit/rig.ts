// Rigging for a procedural asset: an armature you can build in the viewport, and two ways to
// make the model follow it.
//
// Which of the two is right depends on how the asset was made, and both cases are common enough
// that picking one would leave half the library unriggable:
//
//   PARTS   The asset is separate named meshes — head, neck, thigh, three toes. A bone owns a
//           set of parts and moves them rigidly. Exact, free, and how a low-poly game asset is
//           animated anyway. Nothing is approximated, so nothing can go subtly wrong.
//   SKIN    The asset is one welded surface. Then vertices have to be weighted to bones, and the
//           weights are envelopes: distance to the bone segment, the nearest four, normalised.
//           That is what Blender's "with envelope weights" does, and it is good enough to pose
//           with while being simple enough to be obviously correct.
//
// Bones are oriented so their own +Y runs head to tail. That is what makes a pose readable: an
// animator rotating an upper arm expects the twist to be about the arm, not about the world.

import type { BoneSpec, V3 } from "./kit";
import { THEME, boneGeometry } from "./overlay";
import { fabrik } from "./ops";
import { heatWeightsArrays } from "./model";

export type BindMode = "parts" | "skin";

export interface Rig {
  /** The root Object3D to add to the scene: the bone hierarchy plus its display. */
  root: any;
  bones: any[];
  byName: Map<string, any>;
  specs: BoneSpec[];
  mode: BindMode;
  /** Meshes turned into SkinnedMesh, so an unbind can put the originals back. */
  swapped: Array<{ from: any; to: any; parent: any }>;
  /** Parts re-parented onto a bone, and where they came from. */
  moved: Array<{ obj: any; parent: any }>;
  /** One octahedral body per bone. They are children of the BONES, not of a display group, so a
   *  pose carries them for free — which also means hiding them is a walk, not one flag. */
  bodies: any[];
  setDisplay(on: boolean, inFront: boolean): void;
  pose(pose: Record<string, V3>): void;
  /** Bone bodies for the raycaster, each carrying userData.bone. */
  pickables(): any[];
  dispose(): void;
}

const Y: V3 = [0, 1, 0];

/** Parents before children, so a world matrix is always ready when a child needs it. */
export function orderSpecs(specs: BoneSpec[]): BoneSpec[] {
  const byName = new Map(specs.map((s) => [s.name, s]));
  const out: BoneSpec[] = [];
  const seen = new Set<string>();
  const visit = (s: BoneSpec, guard: Set<string>) => {
    if (seen.has(s.name) || guard.has(s.name)) return;   // a cycle in a hand-edited sidecar
    guard.add(s.name);
    const p = s.parent ? byName.get(s.parent) : undefined;
    if (p) visit(p, guard);
    if (seen.has(s.name)) return;
    seen.add(s.name);
    out.push(s);
  };
  for (const s of specs) visit(s, new Set());
  return out;
}

function restWorld(T: any, s: BoneSpec): any {
  const head = new T.Vector3(...s.head);
  const dir = new T.Vector3(...s.tail).sub(head);
  const len = dir.length() || 1e-5;
  const q = new T.Quaternion().setFromUnitVectors(new T.Vector3(...Y), dir.clone().divideScalar(len));
  return { mat: new T.Matrix4().compose(head, q, new T.Vector3(1, 1, 1)), len };
}

export function buildRig(T: any, specs: BoneSpec[], mode: BindMode = "parts"): Rig {
  const ordered = orderSpecs(specs);
  const root = new T.Group();
  root.name = "__rig";
  const bones: any[] = [];
  const byName = new Map<string, any>();
  const worlds = new Map<string, any>();

  for (const s of ordered) {
    const { mat, len } = restWorld(T, s);
    const bone = T.Bone ? new T.Bone() : new T.Object3D();
    bone.name = s.name;
    bone.userData.spec = s;
    bone.userData.length = len;
    const parent = s.parent ? byName.get(s.parent) : null;
    const local = mat.clone();
    if (parent) {
      const pw = worlds.get(s.parent!);
      local.premultiply(new T.Matrix4().copy(pw).invert());
    }
    const p = new T.Vector3(), q = new T.Quaternion(), sc = new T.Vector3();
    local.decompose(p, q, sc);
    bone.position.copy(p);
    bone.quaternion.copy(q);
    // The rest transform is kept so a pose is always relative to it and never accumulates.
    bone.userData.restPos = p.clone();
    bone.userData.restQuat = q.clone();
    (parent || root).add(bone);
    worlds.set(s.name, mat);
    byName.set(s.name, bone);
    bones.push(bone);
  }
  root.updateMatrixWorld(true);

  const bodies = buildDisplay(T, bones);

  const rig: Rig = {
    root, bones, byName, specs: ordered, mode,
    swapped: [], moved: [], bodies,
    setDisplay(on: boolean, inFront: boolean) {
      for (const body of bodies) {
        body.visible = on;
        body.renderOrder = inFront ? 900 : 0;
        body.material.depthTest = !inFront;
        const wire = body.children[0];
        if (wire?.material) { wire.material.depthTest = !inFront; wire.renderOrder = inFront ? 901 : 0; }
      }
    },
    pose(pose: Record<string, V3>) { applyPose(T, rig, pose); },
    pickables() { return bodies; },
    dispose() {
      unbind(rig);
      for (const b of bodies) {
        try { b.geometry?.dispose?.(); b.material?.dispose?.(); } catch { /* gone */ }
        for (const c of b.children) { try { c.geometry?.dispose?.(); c.material?.dispose?.(); } catch { /* gone */ } }
      }
      try { root.parent?.remove(root); } catch { /* never added */ }
    },
  };
  return rig;
}

function buildDisplay(T: any, bones: any[]): any[] {
  const out: any[] = [];
  for (const b of bones) {
    const len = b.userData.length || 0.1;
    const mesh = new T.Mesh(boneGeometry(T, len), new T.MeshBasicMaterial({
      color: THEME.bone, transparent: true, opacity: 0.55, depthTest: false,
      wireframe: false, toneMapped: false,
    }));
    mesh.name = "bone:" + b.name;
    mesh.userData.boneName = b.name;
    mesh.renderOrder = 900;
    mesh.frustumCulled = false;
    const wire = new T.Mesh(boneGeometry(T, len), new T.MeshBasicMaterial({
      color: THEME.bone, wireframe: true, transparent: true, opacity: 0.9, depthTest: false, toneMapped: false,
    }));
    wire.renderOrder = 901;
    wire.frustumCulled = false;
    mesh.add(wire);
    b.add(mesh);
    out.push(mesh);
  }
  return out;
}

export function highlightBone(rig: Rig, selected: Set<string>, active: string) {
  for (const body of rig.bodies) {
    const b = { name: body.userData.boneName as string };
    const on = selected.has(b.name);
    const col = b.name === active ? THEME.boneActive : on ? THEME.boneSel : THEME.bone;
    body.material.color.setHex(col);
    body.material.opacity = on ? 0.8 : 0.5;
    const wire = body.children[0];
    if (wire?.material) { wire.material.color.setHex(col); wire.material.opacity = on ? 1 : 0.75; }
  }
}

/**
 * Inverse kinematics on a chain of bones: put the TIP of `tip` at `target`, moving up to `depth`
 * bones above it. FABRIK finds the joint positions; each bone is then swung, root to tip, from
 * the direction it has to the direction it needs, and the swing is written into the pose as the
 * rest-relative euler the rest of the editor already understands. No twist is added, which is
 * what a foot or a hand wants from a drag.
 */
export function solveChain(T: any, rig: Rig, tip: string, target: V3, depth: number, pose: Record<string, V3>,
  iterations = 16): { pose: Record<string, V3>; chain: string[]; reached: boolean } {
  const chain: any[] = [];
  let b = rig.byName.get(tip);
  while (b && chain.length < Math.max(1, depth) + 1) { chain.unshift(b); b = b.parent?.isBone ? b.parent : null; }
  const out = { ...pose };
  if (!chain.length) return { pose: out, chain: [], reached: false };
  rig.root.updateMatrixWorld(true);
  const specLen = (name: string) => {
    const s = rig.specs.find((x) => x.name === name);
    return s ? Math.hypot(s.tail[0] - s.head[0], s.tail[1] - s.head[1], s.tail[2] - s.head[2]) : 1;
  };
  const head = (bone: any): V3 => { const v = bone.getWorldPosition(new T.Vector3()); return [v.x, v.y, v.z]; };
  const joints: V3[] = chain.map(head);
  const last = chain[chain.length - 1];
  const tailV = last.localToWorld(new T.Vector3(0, specLen(last.name), 0));
  joints.push([tailV.x, tailV.y, tailV.z]);
  const solved = fabrik(joints, target, { iterations });
  for (let i = 0; i < chain.length; i++) {
    const bone = chain[i];
    bone.updateWorldMatrix(true, false);
    const from = bone.getWorldPosition(new T.Vector3());
    const cur = bone.localToWorld(new T.Vector3(0, 1, 0)).sub(from).normalize();
    const want = new T.Vector3(...solved.joints[i + 1]).sub(new T.Vector3(...solved.joints[i])).normalize();
    if (cur.lengthSq() < 1e-12 || want.lengthSq() < 1e-12) continue;
    const swing = new T.Quaternion().setFromUnitVectors(cur, want);
    const world = bone.getWorldQuaternion(new T.Quaternion());
    const parentQ = bone.parent ? bone.parent.getWorldQuaternion(new T.Quaternion()) : new T.Quaternion();
    const local = parentQ.invert().multiply(swing.multiply(world));
    bone.quaternion.copy(local);
    bone.updateMatrixWorld(true);
    const e = new T.Euler().setFromQuaternion(bone.userData.restQuat.clone().invert().multiply(local), "XYZ");
    out[bone.name] = [e.x, e.y, e.z];
  }
  return { pose: out, chain: chain.map((c) => c.name as string), reached: solved.reached };
}

/** A pose is a rest-relative euler per bone, so clearing it is deleting the entry. */
export function applyPose(T: any, rig: Rig, pose: Record<string, V3>) {
  for (const b of rig.bones) {
    const e = pose[b.name];
    b.position.copy(b.userData.restPos);
    if (!e) { b.quaternion.copy(b.userData.restQuat); continue; }
    const q = new T.Quaternion().setFromEuler(new T.Euler(e[0], e[1], e[2], "XYZ"));
    b.quaternion.copy(b.userData.restQuat).multiply(q);
  }
  rig.root.updateMatrixWorld(true);
  for (const s of rig.swapped) {
    // A skinned mesh recomputes its bone matrices from the skeleton, which has just moved.
    try { s.to.skeleton?.update?.(); } catch { /* not skinned after all */ }
  }
}

// ------------------------------------------------------------------ binding
/** Attach named parts to the bone that claims them. `attach` keeps the world transform, which is
 *  what makes binding invisible until the first pose. */
export function bindParts(T: any, rig: Rig, assetRoot: any) {
  unbind(rig);
  for (const s of rig.specs) {
    if (!s.parts?.length) continue;
    const bone = rig.byName.get(s.name);
    if (!bone) continue;
    for (const name of s.parts) {
      const obj = assetRoot.getObjectByName(name);
      if (!obj || obj === assetRoot) continue;
      rig.moved.push({ obj, parent: obj.parent });
      bone.attach(obj);
    }
  }
  rig.mode = "parts";
}

/**
 * Replace every mesh under the root with a SkinnedMesh weighted to the armature.
 *
 * The world transform is baked into the geometry and the SkinnedMesh left at the origin. Binding
 * a mesh that carries its own transform means every later matrix has to agree about whether that
 * transform is already in the bind matrix, and it is the single most common way a skinned mesh
 * ends up in the right shape at the wrong place.
 */
export type WeightMethod = "envelope" | "heat";

export function bindSkin(T: any, rig: Rig, assetRoot: any, maxBones = 4, method: WeightMethod = "envelope") {
  unbind(rig);
  if (!T.SkinnedMesh || !T.Skeleton) return;
  const skeleton = new T.Skeleton(rig.bones);
  const targets: any[] = [];
  assetRoot.updateMatrixWorld(true);
  assetRoot.traverse((o: any) => { if (o.isMesh && !o.isSkinnedMesh && o.geometry?.attributes?.position) targets.push(o); });

  for (const mesh of targets) {
    const geo = mesh.geometry.clone();
    geo.applyMatrix4(mesh.matrixWorld);
    // Envelope weights fall off with distance to the bone; heat weights diffuse over the surface
    // from the nearest bone each vertex can see, which is what keeps one leg's skin off the other.
    const { index, weight } = method === "heat" ? heatWeightsOf(geo, rig.specs, maxBones) : envelopeWeights(T, geo, rig.specs, maxBones);
    geo.setAttribute("skinIndex", new T.Uint16BufferAttribute(index, maxBones));
    geo.setAttribute("skinWeight", new T.Float32BufferAttribute(weight, maxBones));
    const sk = new T.SkinnedMesh(geo, mesh.material);
    sk.name = mesh.name;
    sk.userData = { ...mesh.userData, skinnedFrom: mesh.name };
    sk.normalizeSkinWeights();
    // The bones are NOT made children of the mesh. A skeleton reads bone.matrixWorld, and the
    // bones already live under the rig root in the same scene; adopting them here is a habit
    // copied from glTF loaders that would move the armature into whichever mesh bound last.
    sk.bind(skeleton, new T.Matrix4());
    const parent = mesh.parent;
    rig.swapped.push({ from: mesh, to: sk, parent });
    parent.add(sk);
    parent.remove(mesh);
  }
  rig.mode = "skin";
}

export function unbind(rig: Rig) {
  for (const m of rig.moved) { try { m.parent?.attach(m.obj); } catch { /* parent gone */ } }
  rig.moved = [];
  for (const s of rig.swapped) {
    try {
      s.parent.remove(s.to);
      s.parent.add(s.from);
      s.to.geometry?.dispose?.();
    } catch { /* already torn down */ }
  }
  rig.swapped = [];
}

/**
 * Envelope weights: for each vertex, the distance to every bone segment; the nearest few win,
 * with an inverse-power falloff, normalised. `power` decides how tightly a bone holds its own
 * region — four is close to Blender's default feel.
 */
/** Bone heat on a three geometry: positions and index out, skinIndex/skinWeight arrays back. */
export function heatWeightsOf(geo: any, specs: { head: V3; tail: V3 }[], maxBones = 4) {
  const p = geo.attributes.position.array;
  const pos = p instanceof Float32Array ? p : Float32Array.from(p);
  const idx = geo.index ? Uint32Array.from(geo.index.array) : Uint32Array.from({ length: pos.length / 3 }, (_, i) => i);
  return heatWeightsArrays(pos, idx, specs, { maxBones });
}

export function envelopeWeights(T: any, geo: any, specs: { head: V3; tail: V3 }[], maxBones = 4, power = 4) {
  const pos = geo.attributes.position;
  const n = pos.count;
  const nb = specs.length;
  const index = new Uint16Array(n * maxBones);
  const weight = new Float32Array(n * maxBones);
  if (!nb) return { index, weight };

  const heads = specs.map((s) => new T.Vector3(...s.head));
  const tails = specs.map((s) => new T.Vector3(...s.tail));
  const v = new T.Vector3();
  const best: Array<{ i: number; w: number }> = [];

  for (let i = 0; i < n; i++) {
    v.fromBufferAttribute(pos, i);
    best.length = 0;
    for (let b = 0; b < nb; b++) {
      const d = Math.max(1e-4, distToSegment(v, heads[b], tails[b]));
      best.push({ i: b, w: 1 / Math.pow(d, power) });
    }
    best.sort((a, c) => c.w - a.w);
    let sum = 0;
    const take = Math.min(maxBones, best.length);
    for (let k = 0; k < take; k++) sum += best[k].w;
    if (sum <= 0) { index[i * maxBones] = 0; weight[i * maxBones] = 1; continue; }
    for (let k = 0; k < take; k++) {
      index[i * maxBones + k] = best[k].i;
      weight[i * maxBones + k] = best[k].w / sum;
    }
  }
  return { index, weight };
}

function distToSegment(p: any, a: any, b: any): number {
  const abx = b.x - a.x, aby = b.y - a.y, abz = b.z - a.z;
  const apx = p.x - a.x, apy = p.y - a.y, apz = p.z - a.z;
  const len2 = abx * abx + aby * aby + abz * abz;
  let t = len2 > 1e-12 ? (apx * abx + apy * aby + apz * abz) / len2 : 0;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const dx = apx - abx * t, dy = apy - aby * t, dz = apz - abz * t;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

// ------------------------------------------------------------------ making bones
/** A bone spanning the longest axis of a part, already bound to it. The one-click rig for an
 *  asset that is already built out of named pieces. */
export function boneFromObject(T: any, obj: any, name?: string): BoneSpec | null {
  const box = new T.Box3().setFromObject(obj);
  if (box.isEmpty()) return null;
  const size = box.getSize(new T.Vector3());
  const c = box.getCenter(new T.Vector3());
  const axis = size.x >= size.y && size.x >= size.z ? 0 : size.y >= size.z ? 1 : 2;
  const half = [size.x, size.y, size.z][axis] / 2;
  const head: V3 = [c.x, c.y, c.z];
  const tail: V3 = [c.x, c.y, c.z];
  head[axis] -= half;
  tail[axis] += half;
  return { name: name || obj.name || "bone", parent: null, head, tail, parts: [obj.name].filter(Boolean) };
}

/** A unique bone name, because two bones with the same name make a pose ambiguous. */
export function uniqueBoneName(specs: BoneSpec[], want: string): string {
  const taken = new Set(specs.map((s) => s.name));
  if (!taken.has(want)) return want;
  for (let i = 1; i < 999; i++) if (!taken.has(want + "." + String(i).padStart(3, "0"))) return want + "." + String(i).padStart(3, "0");
  return want + "." + Date.now();
}
