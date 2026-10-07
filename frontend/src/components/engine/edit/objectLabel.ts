// A readable name for an object the game never named.
//
// In a procedural game that is almost every object: the code builds meshes and never sets
// `.name`, so the outliner of a mirrored Dino Smash was 578 rows of "(unnamed)" — useless for
// finding the thing you want to move. What an object IS, what shape, and what colour is enough to
// tell a fence post from a palm trunk from a dino, and it is searchable in the outliner's filter
// ("Box", "#6b3f2a", "Group").
//
// Duck-typed on purpose (no three import): it reads the same fields on any three build, and it is
// tested in node.

export function describeObject(o: any): string {
  if (!o) return "(nothing)";
  if (typeof o.name === "string" && o.name.trim()) return o.name;
  const kind = o.isInstancedMesh ? "Instances"
    : o.isSkinnedMesh ? "Skinned mesh"
    : o.isMesh ? "Mesh"
    : o.isLight ? String(o.type || "Light").replace(/Light$/, "") + " light"
    : o.isCamera ? "Camera"
    : o.isBone ? "Bone"
    : o.isSprite ? "Sprite"
    : o.isPoints ? "Points"
    : o.isLine ? "Line"
    : o.isGroup || (o.children && o.children.length) ? "Group"
    : String(o.type || "Object");
  const bits = [kind];
  const g = o.geometry && typeof o.geometry.type === "string"
    ? o.geometry.type.replace(/(Buffer)?Geometry$/, "") : "";
  if (g && g !== "Buffer") bits.push(g);
  const m = Array.isArray(o.material) ? o.material[0] : o.material;
  if (m) {
    if (typeof m.name === "string" && m.name.trim()) bits.push(m.name.trim());
    else if (m.color && typeof m.color.getHexString === "function") bits.push("#" + m.color.getHexString());
  }
  if (o.isInstancedMesh && Number(o.count) > 0) bits.push("×" + Number(o.count));
  if (!o.isMesh && o.children && o.children.length) {
    bits.push(o.children.length + (o.children.length === 1 ? " child" : " children"));
    const inner = innerName(o);
    if (inner) bits.push(inner);
  }
  return bits.join(" · ");
}

/** Names every glTF export carries, which say nothing about what the model is. */
const GENERIC = /^(scene|armature|root|rootnode|gltf_scenerootnode|sketchfab_model|auxscene|object\d*|group\d*|mesh\d*|node\d*)$/i;

/** The first telling name under an unnamed group, breadth first. A loaded model sits four or five
 *  anonymous groups deep ("Group › Group › Scene › Armature › character"), and the name at the
 *  bottom is the only one that says which model it is — so a dino's outer group reads as one. */
function innerName(o: any): string {
  const queue: any[] = [...(o.children || [])];
  for (let seen = 0; queue.length && seen < 64; seen++) {
    const c = queue.shift();
    const n = typeof c?.name === "string" ? c.name.trim() : "";
    if (n && !GENERIC.test(n)) return n;
    if (c?.children?.length) queue.push(...c.children);
  }
  return "";
}
