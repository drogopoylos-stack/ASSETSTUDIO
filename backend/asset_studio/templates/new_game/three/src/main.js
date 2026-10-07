// __GAME_TITLE__: a three.js game, made by the Asset Studio.
//
// Everything the Studio reads is wired in from the first line, so no tool has to be bolted on later:
//   window.__game      { renderer, scene, camera, THREE }: where the live link, the forge and the
//                      scene tools look first. A module game has no window.THREE, and without this
//                      handle the scene can only be found by hunting the heap for it.
//   studio.edits.json  what was moved, hidden or placed in the Studio. ./studio-runtime.js applies
//                      it before the first frame, with the same keys the Studio saved it under.
//   window.__review    a named action the visual review can fire (`jump`), and __reviewReady,
//                      which tells the review when the first frame is real instead of guessing.
//
// Names matter: every object the game makes gets a name that is unique in the scene, because a
// saved edit is keyed by name (a repeated name becomes name.000, name.001, ... in scene order).
import * as THREE from 'three';
import { buildCrate, buildTree } from './assets.js';

// The game folder, whatever page loaded this module.
const BASE = new URL('../', import.meta.url).href;

// ------------------------------------------------------------------ renderer, scene, camera
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
// The default shadow filter. three r186 removed PCFSoftShadowMap and warns on every start when a
// game still asks for it, which is noise in the console an agent reads first.
renderer.shadowMap.enabled = true;
renderer.domElement.id = 'game';
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1d2330);
scene.fog = new THREE.Fog(0x1d2330, 28, 60);

const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 200);
camera.name = 'camera';

// ------------------------------------------------------------------ light
const sky = new THREE.HemisphereLight(0xcfe0ff, 0x3b3226, 1.1);
sky.name = 'sky-light';
const sun = new THREE.DirectionalLight(0xfff4e0, 2.4);
sun.name = 'sun';
sun.position.set(8, 14, 6);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -16, right: 16, top: 16, bottom: -16, near: 1, far: 50 });
scene.add(sky, sun);

// ------------------------------------------------------------------ the world
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(40, 40),
  new THREE.MeshStandardMaterial({ color: 0x4f6b45, roughness: 1 }),
);
ground.name = 'ground';
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);

// [builder, options, name, position, turn about y in radians]
const PROPS = [
  [buildCrate, { size: 1 }, 'crate-1', [-3, 0, -1], 0.3],
  [buildCrate, { size: 0.7, color: 0x9c6b3c }, 'crate-2', [-1.9, 0, -1.7], -0.2],
  [buildTree, { height: 3.2 }, 'tree-1', [3.5, 0, -3], 0],
  [buildTree, { height: 2.4, leaves: 0x4c9a52 }, 'tree-2', [5, 0, 0.5], 0.8],
];
for (const [build, options, name, [x, y, z], turn] of PROPS) {
  const thing = build(options);
  thing.name = name;
  thing.position.set(x, y, z);
  thing.rotation.y = turn;
  scene.add(thing);
}

// ------------------------------------------------------------------ the player
const player = new THREE.Group();
player.name = 'player';
const body = new THREE.Mesh(
  new THREE.CapsuleGeometry(0.35, 0.8, 6, 16),
  new THREE.MeshStandardMaterial({ color: 0xffb347, roughness: 0.55 }),
);
body.name = 'player-body';
body.position.y = 0.75;                     // feet on y = 0, like everything else
body.castShadow = true;
player.add(body);
player.position.set(0, 0, 3);
scene.add(player);

// ------------------------------------------------------------------ input
const keys = new Set();
let vy = 0;
function jump() {
  if (player.position.y <= 0) vy = 6.5;
}
window.addEventListener('keydown', (e) => {
  keys.add(e.code);
  if (e.code === 'Space') jump();
});
window.addEventListener('keyup', (e) => keys.delete(e.code));

// ------------------------------------------------------------------ the loop
let last = 0;
let frames = 0;
function frame(now) {
  const dt = last ? Math.min((now - last) / 1000, 0.05) : 1 / 60;
  last = now;
  const ax = (keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0) - (keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0);
  const az = (keys.has('KeyS') || keys.has('ArrowDown') ? 1 : 0) - (keys.has('KeyW') || keys.has('ArrowUp') ? 1 : 0);
  if (ax || az) {
    const len = Math.hypot(ax, az);
    player.position.x += (ax / len) * 5 * dt;
    player.position.z += (az / len) * 5 * dt;
    player.rotation.y = Math.atan2(ax, az);
  }
  vy -= 18 * dt;
  player.position.y = Math.max(0, player.position.y + vy * dt);
  if (player.position.y === 0 && vy < 0) vy = 0;

  camera.position.set(player.position.x, 6.5, player.position.z + 9);
  camera.lookAt(player.position.x, 0.8, player.position.z);
  renderer.render(scene, camera);
  frames += 1;
  requestAnimationFrame(frame);
}

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// ------------------------------------------------------------------ the Studio's handles
window.__game = { renderer, scene, camera, THREE };

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
    const options = { base: BASE };
    // The loader only when a placed model needs one: most games never pay for it.
    if ((edits.placed || []).some((p) => p && p.ref && p.ref.kind === 'model')) {
      options.GLTFLoader = (await import('three/addons/loaders/GLTFLoader.js')).GLTFLoader;
    }
    const report = await runtime.applyStudioEdits(scene, edits, options);
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
requestAnimationFrame(frame);
