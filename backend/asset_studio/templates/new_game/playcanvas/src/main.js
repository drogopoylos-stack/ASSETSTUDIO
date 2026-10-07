// __GAME_TITLE__: a PlayCanvas game, made by the Asset Studio.
//
// Everything the Studio reads is wired in from the first line, so no tool has to be bolted on later:
//   window.__game      { app, pc }: where the live link, the forge and the scene tools look first.
//                      A module game has no window.pc, and without this handle the app can only
//                      be found by hunting the heap for it.
//   studio.edits.json  what was moved, hidden or placed in the Studio. ./studio-runtime.js applies
//                      it before the first frame, with the same keys the Studio saved it under.
//   window.__review    a named action the visual review can fire (`jump`), and __reviewReady,
//                      which tells the review when the first frame is real instead of guessing.
//
// Names matter: every entity the game makes gets a name that is unique under app.root, because a
// saved edit is keyed by name (a repeated name becomes name.000, name.001, ... in tree order).
// And every entity is made with `app` passed in: see the note at the top of assets.js.
import * as pc from 'playcanvas';
import { buildCrate, buildTree } from './assets.js';

// The game folder, whatever page loaded this module.
const BASE = new URL('../', import.meta.url).href;

// ------------------------------------------------------------------ the app
const canvas = document.createElement('canvas');
// PlayCanvas files every app under its canvas id, and the Studio's forge makes a second app in
// this page: an id of its own keeps the game's app from being filed over.
canvas.id = 'game';
document.body.appendChild(canvas);
const app = new pc.Application(canvas, {
  mouse: new pc.Mouse(canvas),
  keyboard: new pc.Keyboard(window),
  graphicsDeviceOptions: { antialias: true },
});
app.setCanvasFillMode(pc.FILLMODE_FILL_WINDOW);
app.setCanvasResolution(pc.RESOLUTION_AUTO);
window.addEventListener('resize', () => app.resizeCanvas());
app.scene.ambientLight = new pc.Color(0.32, 0.36, 0.44);

const material = (r, g, b) => {
  const m = new pc.StandardMaterial();
  m.diffuse = new pc.Color(r, g, b);
  m.useMetalness = true;
  m.metalness = 0;
  m.gloss = 0.25;
  m.update();
  return m;
};

// ------------------------------------------------------------------ camera and light
const camera = new pc.Entity('camera', app);
camera.addComponent('camera', {
  clearColor: new pc.Color(0.114, 0.137, 0.188),
  fov: 55,
  nearClip: 0.1,
  farClip: 200,
});
app.root.addChild(camera);

const sun = new pc.Entity('sun', app);
sun.addComponent('light', {
  type: 'directional',
  color: new pc.Color(1, 0.96, 0.88),
  intensity: 1.6,
  castShadows: true,
  shadowDistance: 40,
  shadowResolution: 2048,
  shadowBias: 0.2,
  normalOffsetBias: 0.05,
});
sun.setEulerAngles(50, 35, 0);
app.root.addChild(sun);

// ------------------------------------------------------------------ the world
const ground = new pc.Entity('ground', app);
ground.addComponent('render', { type: 'plane', material: material(0.31, 0.42, 0.27), castShadows: false, receiveShadows: true });
ground.setLocalScale(40, 1, 40);
app.root.addChild(ground);

// [builder, options, name, position, turn about y in degrees]
const PROPS = [
  [buildCrate, { size: 1 }, 'crate-1', [-3, 0, -1], 17],
  [buildCrate, { size: 0.7, color: 0x9c6b3c }, 'crate-2', [-1.9, 0, -1.7], -11],
  [buildTree, { height: 3.2 }, 'tree-1', [3.5, 0, -3], 0],
  [buildTree, { height: 2.4, leaves: 0x4c9a52 }, 'tree-2', [5, 0, 0.5], 46],
];
for (const [build, options, name, [x, y, z], turn] of PROPS) {
  const thing = build({ app, ...options });
  thing.name = name;
  thing.setLocalPosition(x, y, z);
  thing.setLocalEulerAngles(0, turn, 0);
  app.root.addChild(thing);
}

// ------------------------------------------------------------------ the player
const player = new pc.Entity('player', app);
const body = new pc.Entity('player-body', app);
body.addComponent('render', { type: 'capsule', material: material(1, 0.7, 0.28), castShadows: true, receiveShadows: true });
// The capsule primitive is 1 wide and 2 tall: this makes it 0.7 by 1.5, feet on y = 0.
body.setLocalScale(0.7, 0.75, 0.7);
body.setLocalPosition(0, 0.75, 0);
player.addChild(body);
player.setLocalPosition(0, 0, 3);
app.root.addChild(player);

// ------------------------------------------------------------------ input and the loop
let vy = 0;
let frames = 0;
function jump() {
  if (player.getLocalPosition().y <= 0) vy = 6.5;
}
const held = (...codes) => codes.some((k) => app.keyboard.isPressed(k));
app.on('update', (dt) => {
  dt = Math.min(dt, 0.05);
  if (app.keyboard.wasPressed(pc.KEY_SPACE)) jump();
  const ax = (held(pc.KEY_D, pc.KEY_RIGHT) ? 1 : 0) - (held(pc.KEY_A, pc.KEY_LEFT) ? 1 : 0);
  const az = (held(pc.KEY_S, pc.KEY_DOWN) ? 1 : 0) - (held(pc.KEY_W, pc.KEY_UP) ? 1 : 0);
  const p = player.getLocalPosition().clone();
  if (ax || az) {
    const len = Math.hypot(ax, az);
    p.x += (ax / len) * 5 * dt;
    p.z += (az / len) * 5 * dt;
    player.setLocalEulerAngles(0, (Math.atan2(ax, az) * 180) / Math.PI, 0);
  }
  vy -= 18 * dt;
  p.y = Math.max(0, p.y + vy * dt);
  if (p.y === 0 && vy < 0) vy = 0;
  player.setLocalPosition(p);

  camera.setPosition(p.x, 6.5, p.z + 9);
  camera.lookAt(p.x, 0.8, p.z);
  frames += 1;
});

// ------------------------------------------------------------------ the Studio's handles
window.__game = { app, pc };

// MERGED, never assigned. During a review the Studio's harness owns window.__review (its clock,
// its capture) and reads only `actions` from it; replacing the object would remove the harness.
const review = (window.__review = window.__review || {});
review.actions = Object.assign(review.actions || {}, {
  jump: { note: 'the player jumps once', run: () => jump() },
});
window.__reviewReady = () => frames > 1;

// ------------------------------------------------------------------ saved edits, then go
async function applySavedEdits() {
  try {
    const runtime = await import('./studio-runtime.js');
    const edits = await runtime.loadStudioEdits(new URL('studio.edits.json', BASE).href);
    if (!edits) return null;
    const report = await runtime.applyStudioEdits(app, edits, { base: BASE });
    if (report && ((report.errors || []).length || (report.missing || []).length)) {
      console.warn('[studio] saved edits:', report);
    }
    return report;
  } catch (e) {
    // A game never fails to start because of its saved edits: it starts as its code built it.
    console.warn('[studio] saved edits were not applied:', e);
    return null;
  }
}

const within = (ms, work) => Promise.race([work, new Promise((done) => setTimeout(() => done(null), ms))]);
window.__game.edits = await within(4000, applySavedEdits());
app.start();
