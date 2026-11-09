// __GAME_TITLE__: a PixiJS game, made by the Asset Studio.
//
// Everything the Studio reads is wired in from the first line, so no tool has to be bolted on later:
//   window.__game      { app, PIXI }: where the live link and the scene tools look first. A module
//                      game has no global to find, so without this it is found by a heap hunt.
//   studio.edits.json  what was moved, hidden or placed in the Studio. ./studio-runtime.js applies
//                      it before the first frame, with the same keys the Studio saved it under.
//   window.__review    a named action the visual review can fire (`jump`), and __reviewReady,
//                      which tells the review when the first frame is real instead of guessing.
//
// Names matter: every object the game makes gets a unique `label`, because a saved edit is keyed by
// it. An object with no label is keyed by its texture, and by its place in the tree when it has no
// texture, and a place in a tree changes when the code changes.
import * as PIXI from 'pixi.js';
import { buildCrate, buildTree } from './assets.js';

// The game folder, whatever page loaded this module.
const BASE = new URL('../', import.meta.url).href;

const WIDTH = 960;
const HEIGHT = 540;
const GROUND_Y = 450;
const GRAVITY = 1500;
const JUMP_SPEED = 620;
const RUN_SPEED = 300;

const app = new PIXI.Application();
await app.init({ width: WIDTH, height: HEIGHT, background: '#1d2330', antialias: true, autoStart: false });
app.canvas.id = 'game';
document.body.appendChild(app.canvas);

// ------------------------------------------------------------------ the world
const ground = new PIXI.Graphics().rect(0, 0, WIDTH, HEIGHT - GROUND_Y).fill(0x4f6b45);
ground.label = 'ground';
ground.position.set(0, GROUND_Y);
app.stage.addChild(ground);

// Each builder draws to a canvas, which becomes a texture.
const crate = PIXI.Texture.from(buildCrate({ size: 72 }));
const tree = PIXI.Texture.from(buildTree({ height: 170 }));

// [texture, name, x, y, scale]: the anchor is the middle of the feet, so y is the ground.
const PROPS = [
  [crate, 'crate-1', 180, GROUND_Y, 1],
  [crate, 'crate-2', 262, GROUND_Y, 0.7],
  [tree, 'tree-1', 640, GROUND_Y, 1],
  [tree, 'tree-2', 790, GROUND_Y, 0.75],
];
for (const [texture, name, x, y, scale] of PROPS) {
  const thing = new PIXI.Sprite(texture);
  thing.label = name;
  thing.anchor.set(0.5, 1);
  thing.position.set(x, y);
  thing.scale.set(scale);
  app.stage.addChild(thing);
}

// ------------------------------------------------------------------ the player
const player = new PIXI.Graphics().rect(-22, -80, 44, 80).fill(0xffb347);
player.label = 'player';
player.position.set(WIDTH / 2, GROUND_Y);
app.stage.addChild(player);

const hud = new PIXI.Text({
  text: 'A D or arrows to move, Space to jump',
  style: { fontFamily: 'system-ui, sans-serif', fontSize: 14, fill: '#c9d3e6' },
});
hud.label = 'hud';
hud.position.set(12, 12);
app.stage.addChild(hud);

// ------------------------------------------------------------------ input
const keys = new Set();
let vy = 0;
function jump() {
  if (player.y >= GROUND_Y) vy = -JUMP_SPEED;
}
window.addEventListener('keydown', (e) => {
  keys.add(e.code);
  if (e.code === 'Space') jump();
});
window.addEventListener('keyup', (e) => keys.delete(e.code));

// ------------------------------------------------------------------ the loop
let frames = 0;
app.ticker.add((ticker) => {
  const dt = Math.min(ticker.deltaMS / 1000, 0.05);
  const ax = (keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0) - (keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0);
  player.x = Math.max(22, Math.min(WIDTH - 22, player.x + ax * RUN_SPEED * dt));
  vy += GRAVITY * dt;
  player.y = Math.min(GROUND_Y, player.y + vy * dt);
  if (player.y === GROUND_Y && vy > 0) vy = 0;
  frames += 1;
});

// The page fits the window; the game keeps its own size inside it.
function fit() {
  const k = Math.min(window.innerWidth / WIDTH, window.innerHeight / HEIGHT);
  app.canvas.style.width = WIDTH * k + 'px';
  app.canvas.style.height = HEIGHT * k + 'px';
}
window.addEventListener('resize', fit);
fit();

// ------------------------------------------------------------------ the Studio's handles
window.__game = { app, PIXI };

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
