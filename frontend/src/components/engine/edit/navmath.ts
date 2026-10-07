// The viewport's navigation maths, kept free of three.js and the DOM so it can be tested in node.
//
// Three complaints from a person editing a live game (Dino Smash: a 300 m island, 151k triangles)
// put every function here:
//
//   "I cannot zoom in closer" — the wheel dollied toward the ORBIT CENTRE. In a whole-level scene
//     that centre is some point on the ground a hundred metres away, so no amount of scrolling ever
//     reached the dino under the cursor; you flew past it toward a spot you were not looking at.
//     `zoomToward` moves toward the point under the cursor instead (Blender's "Zoom to Mouse
//     Position", Godot's and Unity's default feel), and the orbit centre travels with it, so the
//     next orbit turns round what you zoomed into.
//   "the controls are not perfect" — a Unity or Godot hand expects to hold the right button and
//     fly: look with the mouse, W A S D to move, Q E down and up, Shift to go faster, the wheel to
//     change the speed. `flyLook` and `flyStep` are that.
//   "I cannot see it up close" — the near clipping plane followed the orbit distance only, so a
//     camera flown close to something while its orbit centre stayed far away cut the thing in half.
//     `clipPlanes` keeps the near plane small when close and the far plane beyond the whole scene.

export type Vec3 = [number, number, number];

const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a: Vec3, k: number): Vec3 => [a[0] * k, a[1] * k, a[2] * k];
const len = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: Vec3): Vec3 => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

/** Smallest and largest orbit distance, and the elevation margin that keeps the view off the poles. */
export const MIN_DIST = 1e-5;
export const MAX_DIST = 1e7;
const POLE = 0.015;

/** The unit vector from the orbit centre to the eye, for an azimuth theta and a polar angle phi. */
export function dirOf(theta: number, phi: number): Vec3 {
  const s = Math.sin(phi);
  return [s * Math.sin(theta), Math.cos(phi), s * Math.cos(theta)];
}

/** Where the eye is: the orbit centre plus `dist` along the view's back direction. */
export function eyeOf(target: Vec3, dist: number, theta: number, phi: number): Vec3 {
  return add(target, scale(dirOf(theta, phi), dist));
}

/** The factor one wheel event changes the distance by. Multiplicative, so a step is the same
 *  fraction of the distance at any scale — reaching a claw takes three steps, not thirty. */
export function wheelFactor(deltaY: number, zoomSpeed = 1): number {
  return Math.exp(deltaY * 0.0012 * zoomSpeed);
}

/**
 * Zoom by one wheel event toward `pivot`, the point under the cursor (or toward the centre when
 * there is none). The eye slides along the line from itself to the pivot and the orbit centre
 * moves by the same fraction, so the view keeps its direction and the pivot stays under the
 * cursor. Zooming out retraces the same line.
 */
export function zoomToward(target: Vec3, dist: number, deltaY: number, zoomSpeed: number,
                           pivot: Vec3 | null): { target: Vec3; dist: number } {
  const next = Math.max(MIN_DIST, Math.min(MAX_DIST, dist * wheelFactor(deltaY, zoomSpeed)));
  const g = next / dist;                       // what was actually applied, after the clamp
  if (!pivot) return { target: [...target] as Vec3, dist: next };
  return { target: add(target, scale(sub(pivot, target), 1 - g)), dist: next };
}

/** Mouse-look while flying: the angles turn, the EYE stays where it is. Mouse right turns the view
 *  right, mouse up looks up — the same signs as the orbit, which already felt right. */
export function flyLook(theta: number, phi: number, dx: number, dy: number, speed = 1, invert = false):
  { theta: number; phi: number } {
  const k = 0.0035 * speed * (invert ? -1 : 1);
  return { theta: theta - dx * k, phi: Math.min(Math.PI - POLE, Math.max(POLE, phi - dy * k)) };
}

/** The orbit centre that puts the eye at `eye` looking along (theta, phi) from `dist` away. */
export function targetFromEye(eye: Vec3, dist: number, theta: number, phi: number): Vec3 {
  return sub(eye, scale(dirOf(theta, phi), dist));
}

/** The keys that move a flying camera, by `KeyboardEvent.code`, so a French or German keyboard
 *  moves with the keys in the same PLACE as W A S D. */
export const FLY_CODES = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE",
  "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "ShiftLeft", "ShiftRight"]);

/** How far the eye moves in one frame of flying. Forward is where the view looks, right is level
 *  (never tilted with the view), up is the world's up — the way Unity and Godot fly. */
export function flyStep(keys: Set<string>, theta: number, phi: number, speed: number, dt: number): Vec3 {
  const back = dirOf(theta, phi);
  const fwd: Vec3 = [-back[0], -back[1], -back[2]];
  const right: Vec3 = norm([Math.cos(theta), 0, -Math.sin(theta)]);
  let v: Vec3 = [0, 0, 0];
  if (keys.has("KeyW") || keys.has("ArrowUp")) v = add(v, fwd);
  if (keys.has("KeyS") || keys.has("ArrowDown")) v = sub(v, fwd);
  if (keys.has("KeyD") || keys.has("ArrowRight")) v = add(v, right);
  if (keys.has("KeyA") || keys.has("ArrowLeft")) v = sub(v, right);
  if (keys.has("KeyE")) v = add(v, [0, 1, 0]);
  if (keys.has("KeyQ")) v = sub(v, [0, 1, 0]);
  if (len(v) < 1e-9) return [0, 0, 0];
  const boost = keys.has("ShiftLeft") || keys.has("ShiftRight") ? 4 : 1;
  return scale(norm(v), speed * boost * Math.max(0, Math.min(0.1, dt)));
}

/** A fly speed that suits the scene: a whole island in a few seconds, a table top gently. */
export function defaultFlySpeed(sceneRadius: number): number {
  const r = Number.isFinite(sceneRadius) && sceneRadius > 0 ? sceneRadius : 5;
  return Math.max(0.5, Math.min(400, r * 0.35));
}

/** One wheel notch while flying changes the speed, not the distance (Unity's rule). */
export function flySpeedAfterWheel(speed: number, deltaY: number): number {
  const s = speed * (deltaY < 0 ? 1.2 : deltaY > 0 ? 1 / 1.2 : 1);
  return Math.max(0.05, Math.min(5000, s));
}

/**
 * The perspective camera's clipping planes.
 *
 * Near follows the orbit distance so it stays tiny when you are close — and is never allowed so
 * small against far that the depth buffer runs out (a far/near ratio past ~50,000 makes distant
 * ground shimmer). Far always reaches past the whole scene, whatever the orbit distance is, so a
 * camera flown to one end of an island still sees the other end.
 */
export function clipPlanes(dist: number, sceneRadius: number): { near: number; far: number } {
  const far = Math.max(dist * 200 + 100, (Number.isFinite(sceneRadius) ? sceneRadius : 0) * 4);
  const near = Math.max(1e-4, dist * 0.002, far / 50000);
  return { near: Math.min(near, dist * 0.5 || near), far };
}
