// Builders: the things this game is made of, written as code.
//
// Each one takes a single options object (every option has a default) and returns a pc.Entity with
// a name and its feet on y = 0. That shape is the whole contract, and it is what lets the Studio
// use a builder without knowing anything else about the game: the Library lists it, the forge
// builds it alone in a lit studio, review/targets.js mounts it for the visual review, and the
// scene tools place more of it, bottom first, on whatever is below.
//
// PASS `app`. An entity belongs to an application, and without one it takes whichever app ran a
// frame or was created last. The Studio's forge creates a second app beside the game's, so a
// builder called with no app after that would make its entities for the forge. The game always
// passes its own; `app` is optional only so a quick call from a console still works.
//
// To add one: export `buildThing({ app, ...options } = {})` from this file, name the entity, keep
// its lowest point at y = 0, and add a target for it in review/targets.js.
import * as pc from 'playcanvas';

const rgb = (hex) => new pc.Color(((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255);
const darker = (hex, f = 0.6) => {
  const c = (shift) => Math.round(((hex >> shift) & 255) * f);
  return (c(16) << 16) | (c(8) << 8) | c(0);
};

function paint(hex, gloss = 0.2) {
  const m = new pc.StandardMaterial();
  m.diffuse = rgb(hex);
  m.useMetalness = true;
  m.metalness = 0;
  m.gloss = gloss;
  m.update();
  return m;
}

// One primitive part. PlayCanvas primitives are one unit across and centred, so the scale IS the
// size: a box is w x h x d, a cylinder or cone is (2r, h, 2r).
function part(app, name, type, material, pos, size) {
  const e = new pc.Entity(name, app);
  e.addComponent('render', { type, material, castShadows: true, receiveShadows: true });
  e.setLocalPosition(pos[0], pos[1], pos[2]);
  e.setLocalScale(size[0], size[1], size[2]);
  return e;
}

/** A wooden crate: a box with a darker frame on all twelve edges. `size` is its width in metres. */
export function buildCrate({ app, size = 1, color = 0xb5793f } = {}) {
  const root = new pc.Entity('crate', app);
  const s = size;
  const wood = paint(color, 0.25);
  const frame = paint(darker(color), 0.15);
  root.addChild(part(app, 'crate-box', 'box', wood, [0, s / 2, 0], [s * 0.9, s * 0.9, s * 0.9]));

  // The frame stands just proud of the box: four uprights, and four beams top and bottom.
  const t = s * 0.12;
  const e = (s - t) / 2;
  for (const x of [-e, e]) for (const z of [-e, e]) root.addChild(part(app, 'crate-beam', 'box', frame, [x, s / 2, z], [t, s, t]));
  for (const y of [t / 2, s - t / 2]) {
    for (const z of [-e, e]) root.addChild(part(app, 'crate-beam', 'box', frame, [0, y, z], [s, t, t]));
    for (const x of [-e, e]) root.addChild(part(app, 'crate-beam', 'box', frame, [x, y, 0], [t, t, s]));
  }
  return root;
}

/** A low-poly tree: a trunk under two stacked cones of leaves. `height` in metres. */
export function buildTree({ app, height = 3, leaves = 0x3f8f4a, trunk = 0x6b4a2f } = {}) {
  const root = new pc.Entity('tree', app);
  const h = height;
  const trunkH = h * 0.38;
  root.addChild(part(app, 'tree-trunk', 'cylinder', paint(trunk, 0.1), [0, trunkH / 2, 0], [h * 0.13, trunkH, h * 0.13]));
  const leaf = paint(leaves, 0.3);
  root.addChild(part(app, 'tree-leaves', 'cone', leaf, [0, trunkH + h * 0.18, 0], [h * 0.64, h * 0.5, h * 0.64]));
  // Its tip lands exactly on `height`.
  root.addChild(part(app, 'tree-top', 'cone', leaf, [0, trunkH + h * 0.42, 0], [h * 0.46, h * 0.4, h * 0.46]));
  return root;
}
