// __GAME_TITLE__: a Phaser game, made by the Asset Studio.
//
// Everything the Studio reads is wired in from the first line, so no tool has to be bolted on later:
//   window.__game      { game, Phaser }: where the live link and the scene tools look first. A module
//                      game has no global to find, so without this it is found by a heap hunt.
//   studio.edits.json  what was moved, hidden or placed in the Studio. ./studio-runtime.js applies
//                      it before the first update, with the same keys the Studio saved it under.
//   window.__review    a named action the visual review can fire (`jump`), and __reviewReady,
//                      which tells the review when the first frame is real instead of guessing.
//
// Names matter: every object the game makes gets a unique name, because a saved edit is keyed by
// `<scene key>/<name>`. An object with no name is keyed by its texture, and by its place in the
// list when it has no texture, and a place in a list changes when the code changes.
import * as Phaser from 'phaser';
import { buildCrate, buildTree } from './assets.js';

// The game folder, whatever page loaded this module.
const BASE = new URL('../', import.meta.url).href;

const WIDTH = 960;
const HEIGHT = 540;
const GROUND_Y = 450;
const GRAVITY = 1500;
const JUMP_SPEED = 620;
const RUN_SPEED = 300;

let started = false;
let jumpAsked = false;
let frames = 0;

function jump() {
  jumpAsked = true;
}

class Main extends Phaser.Scene {
  constructor() {
    super('Main');
  }

  create() {
    // Each builder draws to a canvas; the scene uses it as a texture. [builder, options, texture]
    const TEXTURES = [
      [buildCrate, { size: 72 }, 'crate'],
      [buildTree, { height: 170 }, 'tree'],
    ];
    for (const [build, options, key] of TEXTURES) this.textures.addCanvas(key, build(options));

    const ground = this.add.rectangle(WIDTH / 2, GROUND_Y + 45, WIDTH, 90, 0x4f6b45);
    ground.name = 'ground';

    // [texture, name, x, y, scale]: y is the middle of the picture, so the feet stand on GROUND_Y.
    const PROPS = [
      ['crate', 'crate-1', 180, GROUND_Y - 36, 1],
      ['crate', 'crate-2', 262, GROUND_Y - 25, 0.7],
      ['tree', 'tree-1', 640, GROUND_Y - 85, 1],
      ['tree', 'tree-2', 790, GROUND_Y - 64, 0.75],
    ];
    for (const [texture, name, x, y, scale] of PROPS) {
      const thing = this.add.image(x, y, texture);
      thing.name = name;
      thing.setScale(scale);
    }

    this.player = this.add.rectangle(WIDTH / 2, GROUND_Y - 40, 44, 80, 0xffb347);
    this.player.name = 'player';
    this.vy = 0;

    const hud = this.add.text(12, 12, 'A D or arrows to move, Space to jump', {
      fontFamily: 'system-ui, sans-serif', fontSize: '14px', color: '#c9d3e6',
    });
    hud.name = 'hud';

    this.keys = this.input.keyboard.addKeys('LEFT,RIGHT,A,D,SPACE');
    sceneReady(this);
  }

  update(_time, delta) {
    if (!started) return;
    frames += 1;
    const dt = Math.min(delta / 1000, 0.05);
    const p = this.player;
    const k = this.keys;
    const ax = (k.RIGHT.isDown || k.D.isDown ? 1 : 0) - (k.LEFT.isDown || k.A.isDown ? 1 : 0);
    p.x = Phaser.Math.Clamp(p.x + ax * RUN_SPEED * dt, 22, WIDTH - 22);
    const onGround = p.y >= GROUND_Y - 40;
    if (onGround && (jumpAsked || Phaser.Input.Keyboard.JustDown(k.SPACE))) this.vy = -JUMP_SPEED;
    jumpAsked = false;
    this.vy += GRAVITY * dt;
    p.y = Math.min(GROUND_Y - 40, p.y + this.vy * dt);
    if (p.y === GROUND_Y - 40 && this.vy > 0) this.vy = 0;
  }
}

let sceneReady = () => {};
const created = new Promise((done) => { sceneReady = done; });

const game = new Phaser.Game({
  type: Phaser.AUTO,
  width: WIDTH,
  height: HEIGHT,
  backgroundColor: '#1d2330',
  parent: document.body,
  scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
  scene: [Main],
});

// ------------------------------------------------------------------ the Studio's handles
window.__game = { game, Phaser };

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
    const report = await runtime.applyStudioEdits(game, edits, { base: BASE });
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
await created;
window.__game.edits = await within(4000, applySavedEdits());
started = true;
