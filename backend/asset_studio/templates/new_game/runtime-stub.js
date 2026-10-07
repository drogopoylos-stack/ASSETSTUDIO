// studio-runtime.js: A STUB. It has the exports of the Asset Studio's runtime and does nothing.
//
// This game was made before the Studio had built its runtime (frontend/dist/studio-runtime.js),
// so this stand-in keeps the game's imports working. The game runs exactly as its code builds it.
// What it does NOT do yet is apply studio.edits.json: moves, hides and placements saved in the
// Studio show in the Studio's live tab but not in the game on its own. To get the real runtime,
// replace this file with the one the Studio serves:
//
//   curl -o src/studio-runtime.js __GAME_STUDIO_ORIGIN__/studio-runtime.js
//
// Every function below answers the way the real one does when it has nothing to do, and the
// first call says once, in the console, that this is the stub.
export const version = 0;
export const stub = true;

let said = false;
function say() {
  if (said) return;
  said = true;
  console.info('[studio-runtime] this is a stub: studio.edits.json is not applied. Replace '
    + 'src/studio-runtime.js with __GAME_STUDIO_ORIGIN__/studio-runtime.js to apply it.');
}

/** Which engine a target belongs to. Detection only: it touches nothing. */
export function engineOf(target) {
  if (!target) return '';
  if (target.isObject3D) return 'three';
  if (target.root && target.graphicsDevice) return 'playcanvas';
  if (typeof target.addChild === 'function' && target.enabled !== undefined) return 'playcanvas';
  return '';
}

/** The object saved edits are keyed from: a three.js object itself, a PlayCanvas app's root. */
export function rootOf(target) {
  if (target && target.root && target.graphicsDevice) return target.root;
  return target || null;
}

export function studioKeys() {
  say();
  return new Map();
}

export function findByKey() {
  say();
  return null;
}

export async function loadStudioEdits() {
  say();
  return null;
}

export async function applyStudioEdits() {
  say();
  return { parts: 0, placed: 0, pending: 0, missing: [],
           errors: ['studio-runtime.js is a stub: nothing was applied'] };
}

export async function placeStudioItem() {
  say();
  return { ok: false, error: 'studio-runtime.js is a stub: nothing was placed' };
}

export function unplaceStudioItem() {
  say();
  return 0;
}
