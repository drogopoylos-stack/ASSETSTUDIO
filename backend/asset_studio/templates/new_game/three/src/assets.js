// Builders: the things this game is made of, written as code.
//
// Each one takes a single options object (every option has a default) and returns a THREE.Group
// with a name and its feet on y = 0. That shape is the whole contract, and it is what lets the
// Studio use a builder without knowing anything else about the game: the Library lists it, the
// forge builds it alone in a lit studio, review/targets.js mounts it for the visual review, and
// the scene tools place more of it, bottom first, on whatever is below.
//
// To add one: export `buildThing({ ...options } = {})` from this file, give the group a name,
// keep its lowest point at y = 0, and add a target for it in review/targets.js.
import * as THREE from 'three';

const darker = (hex, f = 0.6) => new THREE.Color(hex).multiplyScalar(f);

function shadows(group) {
  group.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
  return group;
}

/** A wooden crate: a box with a darker frame on all twelve edges. `size` is its width in metres. */
export function buildCrate({ size = 1, color = 0xb5793f } = {}) {
  const g = new THREE.Group();
  g.name = 'crate';
  const s = size;
  const wood = new THREE.MeshStandardMaterial({ color, roughness: 0.82 });
  const frame = new THREE.MeshStandardMaterial({ color: darker(color), roughness: 0.9 });

  const box = new THREE.Mesh(new THREE.BoxGeometry(s * 0.9, s * 0.9, s * 0.9), wood);
  box.name = 'crate-box';
  box.position.y = s / 2;
  g.add(box);

  // The frame stands just proud of the box: four uprights, and four beams top and bottom.
  const t = s * 0.12;
  const e = (s - t) / 2;
  const beam = (w, h, d, x, y, z) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), frame);
    m.name = 'crate-beam';
    m.position.set(x, y, z);
    g.add(m);
  };
  for (const x of [-e, e]) for (const z of [-e, e]) beam(t, s, t, x, s / 2, z);
  for (const y of [t / 2, s - t / 2]) {
    for (const z of [-e, e]) beam(s, t, t, 0, y, z);
    for (const x of [-e, e]) beam(t, t, s, x, y, 0);
  }
  return shadows(g);
}

/** A low-poly tree: a tapered trunk under two stacked cones of leaves. `height` in metres. */
export function buildTree({ height = 3, leaves = 0x3f8f4a, trunk = 0x6b4a2f } = {}) {
  const g = new THREE.Group();
  g.name = 'tree';
  const h = height;
  const trunkH = h * 0.38;

  const bark = new THREE.Mesh(
    new THREE.CylinderGeometry(h * 0.05, h * 0.075, trunkH, 8),
    new THREE.MeshStandardMaterial({ color: trunk, roughness: 0.95 }),
  );
  bark.name = 'tree-trunk';
  bark.position.y = trunkH / 2;
  g.add(bark);

  const leaf = new THREE.MeshStandardMaterial({ color: leaves, roughness: 0.8, flatShading: true });
  const low = new THREE.Mesh(new THREE.ConeGeometry(h * 0.32, h * 0.5, 9), leaf);
  low.name = 'tree-leaves';
  low.position.y = trunkH + h * 0.18;
  const top = new THREE.Mesh(new THREE.ConeGeometry(h * 0.23, h * 0.4, 9), leaf);
  top.name = 'tree-top';
  top.position.y = trunkH + h * 0.42;      // its tip lands exactly on `height`
  g.add(low, top);
  return shadows(g);
}
