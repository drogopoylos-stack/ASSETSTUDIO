// The viewport's navigation and what re-renders when it moves.
//
//   navmath   zoom toward the cursor, fly (look, move, speed), the clipping planes
//   Nav       the two mouse layouts (Blender/Godot and Unity), zoom and fly on the real class
//   viewSync  the once-a-frame signal, and when a fresh parts list or stats need a re-render
//   labels    a readable name for an object the game never named
//   prefs     the two new settings, their defaults and what reaches the world
//
// Run: npm run test:nav
import * as THREE from "three";
import {
  clipPlanes, defaultFlySpeed, dirOf, eyeOf, FLY_CODES, flyLook, flySpeedAfterWheel, flyStep, MIN_DIST,
  targetFromEye, wheelFactor, zoomToward, type Vec3,
} from "../src/components/engine/edit/navmath";
import { makeSignal, samePartsView, statsShape, type PartView } from "../src/components/engine/edit/viewSync";
import { describeObject } from "../src/components/engine/edit/objectLabel";
import { ENGINE_DEFAULTS, mergePrefs, worldOptions } from "../src/components/engine/prefs";
import { Nav } from "../src/components/engine/edit/world";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
function eq(name: string, got: unknown, want: unknown) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  ok(name, a === b, "got " + a + ", want " + b);
}
function near(name: string, got: number, want: number, tol = 1e-9) {
  ok(name, Number.isFinite(got) && Math.abs(got - want) <= tol, "got " + got + ", want " + want + " (tol " + tol + ")");
}
const section = (t: string) => console.log("\n" + t);

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const len = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (a: Vec3): Vec3 => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const nearV = (name: string, got: Vec3, want: Vec3, tol = 1e-9) =>
  ok(name, len(sub(got, want)) <= tol, "got " + JSON.stringify(got) + ", want " + JSON.stringify(want));

// ------------------------------------------------------------------------------------------------
section("zoom toward the cursor");
{
  near("no wheel, no change", wheelFactor(0), 1);
  ok("wheel up zooms in", wheelFactor(-100) < 1);
  near("in then out is the identity", wheelFactor(-100) * wheelFactor(100), 1, 1e-12);
  near("zoom speed doubles the step", Math.log(wheelFactor(100, 2)), 2 * Math.log(wheelFactor(100)), 1e-12);

  // A whole-level view: the orbit centre on the ground 150 m away, a dino under the cursor 40 m
  // to one side. Zooming must go to the dino, and the dino must stay under the same pixel.
  const T: Vec3 = [0, 0, 0], d = 150, th = 0.7, ph = 1.1;
  const P: Vec3 = [30, 4, 60];
  const E = eyeOf(T, d, th, ph);
  const r = zoomToward(T, d, -120, 1, P);
  const E2 = eyeOf(r.target, r.dist, th, ph);
  near("the pivot stays on the same view ray", dot(unit(sub(P, E2)), unit(sub(P, E))), 1, 1e-12);
  near("the eye closed on the pivot by the wheel's factor", len(sub(P, E2)) / len(sub(P, E)), wheelFactor(-120), 1e-12);
  ok("the orbit centre moved toward the pivot", len(sub(r.target, P)) < len(sub(T, P)));
  // The old wheel: the distance to the DINO barely changed after ten notches, because it dollied
  // toward the ground point instead.
  let t: Vec3 = T, dist = d;
  for (let i = 0; i < 10; i++) { const s = zoomToward(t, dist, -120, 1, P); t = s.target; dist = s.dist; }
  const reach = len(sub(P, eyeOf(t, dist, th, ph)));
  ok("ten notches reach the thing under the cursor", reach < 0.3 * len(sub(P, E)), "left " + reach.toFixed(2) + " m");
  // Retrace: the same number of notches out puts everything back.
  for (let i = 0; i < 10; i++) { const s = zoomToward(t, dist, 120, 1, P); t = s.target; dist = s.dist; }
  nearV("zooming out retraces the way in", t, T, 1e-9);
  near("and the distance comes back", dist, d, 1e-9);

  const none = zoomToward(T, d, -120, 1, null);
  nearV("no pivot: the centre stays", none.target, T);
  near("no pivot: the distance scales", none.dist, d * wheelFactor(-120), 1e-9);

  const clamped = zoomToward([1, 2, 3], MIN_DIST, -500, 1, [9, 9, 9]);
  nearV("at the closest distance a zoom in cannot drag the centre away", clamped.target, [1, 2, 3]);
  near("and the distance holds", clamped.dist, MIN_DIST);
}

// ------------------------------------------------------------------------------------------------
section("fly: look, move, speed");
{
  const T: Vec3 = [5, 1, -2], d = 7, th = 0.4, ph = 1.3;
  const E = eyeOf(T, d, th, ph);
  const a = flyLook(th, ph, 40, -25);
  nearV("mouse-look keeps the eye where it is", eyeOf(targetFromEye(E, d, a.theta, a.phi), d, a.theta, a.phi), E, 1e-9);
  const fwd = (t: number, p: number): Vec3 => { const b = dirOf(t, p); return [-b[0], -b[1], -b[2]]; };
  const right: Vec3 = [Math.cos(th), 0, -Math.sin(th)];
  ok("mouse right turns the view right", dot(fwd(flyLook(th, ph, 10, 0).theta, ph), right) > 0);
  ok("mouse down looks down", fwd(th, flyLook(th, ph, 0, 10).phi)[1] < fwd(th, ph)[1]);
  ok("invert turns the other way", dot(fwd(flyLook(th, ph, 10, 0, 1, true).theta, ph), right) < 0);
  const pole = flyLook(th, 0.02, 0, 10000);
  ok("the view never flips over the pole", pole.phi > 0 && pole.phi < Math.PI, "phi " + pole.phi);

  const keys = (...k: string[]) => new Set(k);
  const f = fwd(th, ph);
  nearV("W flies where the view looks", flyStep(keys("KeyW"), th, ph, 10, 0.05), [f[0] * 0.5, f[1] * 0.5, f[2] * 0.5], 1e-12);
  nearV("S flies back", flyStep(keys("KeyS"), th, ph, 10, 0.05), [-f[0] * 0.5, -f[1] * 0.5, -f[2] * 0.5], 1e-12);
  nearV("D strafes right, level", flyStep(keys("KeyD"), th, ph, 10, 0.1), [right[0], 0, right[2]], 1e-12);
  nearV("A strafes left", flyStep(keys("KeyA"), th, ph, 10, 0.1), [-right[0], 0, -right[2]], 1e-12);
  nearV("E rises straight up", flyStep(keys("KeyE"), th, ph, 10, 0.1), [0, 1, 0], 1e-12);
  nearV("Q sinks straight down", flyStep(keys("KeyQ"), th, ph, 10, 0.1), [0, -1, 0], 1e-12);
  nearV("the arrow keys fly too", flyStep(keys("ArrowUp"), th, ph, 10, 0.05), flyStep(keys("KeyW"), th, ph, 10, 0.05), 1e-12);
  near("Shift flies four times as fast", len(flyStep(keys("KeyW", "ShiftLeft"), th, ph, 10, 0.1)), 4, 1e-12);
  near("a diagonal is no faster than straight", len(flyStep(keys("KeyW", "KeyD"), th, ph, 10, 0.1)), 1, 1e-12);
  near("a stalled frame does not jump", len(flyStep(keys("KeyW"), th, ph, 10, 3)), 1, 1e-12);
  eq("no keys, no move", flyStep(keys(), th, ph, 10, 0.1), [0, 0, 0]);
  eq("Shift alone does not move", flyStep(keys("ShiftRight"), th, ph, 10, 0.1), [0, 0, 0]);
  eq("W and S cancel", flyStep(keys("KeyW", "KeyS"), th, ph, 10, 0.1), [0, 0, 0]);
  ok("the fly keys are taken by code", FLY_CODES.has("KeyW") && FLY_CODES.has("KeyQ") && FLY_CODES.has("ShiftLeft"));
  ok("G, R and S stay Blender's", !FLY_CODES.has("KeyG") && !FLY_CODES.has("KeyR") && !FLY_CODES.has("KeyF"));

  near("an island flies at a pace", defaultFlySpeed(300), 105);
  near("a table top gently", defaultFlySpeed(0.2), 0.5);
  near("a huge scene is capped", defaultFlySpeed(1e6), 400);
  near("an unknown scene gets a sane speed", defaultFlySpeed(NaN), 1.75);
  near("wheel up speeds up", flySpeedAfterWheel(10, -100), 12, 1e-12);
  near("wheel down slows down", flySpeedAfterWheel(12, 100), 10, 1e-12);
  near("never stops dead", flySpeedAfterWheel(0.05, 100), 0.05);
  near("never runs away", flySpeedAfterWheel(5000, -100), 5000);
}

// ------------------------------------------------------------------------------------------------
section("clipping planes");
{
  for (const [dist, radius] of [[150, 300], [5, 300], [0.02, 300], [1e-4, 300], [3, 2], [0.5, 0], [2000, 50]] as const) {
    const c = clipPlanes(dist, radius);
    ok(`near keeps what is at the orbit centre (d ${dist}, r ${radius})`, c.near <= dist * 0.5 + 1e-15, JSON.stringify(c));
    ok(`far reaches past the whole scene (d ${dist}, r ${radius})`, c.far >= radius * 4 && c.far >= dist * 200 + 100, JSON.stringify(c));
    ok(`near is positive (d ${dist}, r ${radius})`, c.near > 0);
  }
  const mid = clipPlanes(5, 300);
  ok("the depth range stays inside what the buffer can hold", mid.far / mid.near <= 50000 + 1e-9, JSON.stringify(mid));
  // What cut a close-up in half: near followed the ORBIT distance, which a flight left 150 m away.
  const close = clipPlanes(0.05, 300);
  ok("two metres from a dino with a short orbit, the dino is not clipped", close.near < 0.05, JSON.stringify(close));
  near("NaN radius is treated as none", clipPlanes(5, NaN).far, 1100);
}

// ------------------------------------------------------------------------------------------------
section("Nav: the two mouse layouts");
{
  const pe = (button: number, mods: { shift?: boolean; ctrl?: boolean; alt?: boolean } = {}, x = 100, y = 100) =>
    ({ button, shiftKey: !!mods.shift, ctrlKey: !!mods.ctrl, altKey: !!mods.alt, clientX: x, clientY: y }) as unknown as PointerEvent;
  let changes = 0;
  const nav = new Nav(THREE, () => { changes++; });
  const snap = () => ({ th: nav.theta, ph: nav.phi, d: nav.dist, t: [nav.target.x, nav.target.y, nav.target.z] });
  const drag = (button: number, mods: { shift?: boolean; ctrl?: boolean; alt?: boolean } = {}, allowLeft = true) => {
    const before = snap();
    const took = nav.down(pe(button, mods), allowLeft);
    if (took) { nav.move(pe(button, mods, 130, 120), 600); nav.up(); }
    const after = snap();
    return {
      took,
      orbit: after.th !== before.th,
      pan: JSON.stringify(after.t) !== JSON.stringify(before.t) && after.th === before.th && after.d === before.d,
      dolly: after.d !== before.d && after.th === before.th,
    };
  };
  eq("default layout is Blender's", nav.scheme, "blender");
  ok("blender: middle drag orbits", drag(1).orbit);
  ok("blender: Shift+middle pans", drag(1, { shift: true }).pan);
  ok("blender: Ctrl+middle dollies", drag(1, { ctrl: true }).dolly);
  ok("blender: Alt+left orbits", drag(0, { alt: true }).orbit);
  ok("blender: Alt+left is refused where left orbit is off", !drag(0, { alt: true }, false).took);
  ok("blender: the right button is left for flying", !drag(2).took);
  ok("blender: a plain left click is not navigation", !drag(0).took);
  nav.scheme = "unity";
  ok("unity: middle drag pans", drag(1).pan);
  ok("unity: Alt+left orbits", drag(0, { alt: true }).orbit);
  ok("unity: Alt+right dollies", drag(2, { alt: true }).dolly);
  ok("unity: a plain right button is left for flying", !drag(2).took);
  ok("unity: a plain left click is not navigation", !drag(0).took);
  nav.scheme = "blender";
  ok("every change told the world", changes > 0);
}

section("Nav: leaving a scene camera starts from where it stood");
{
  // A mirrored game opens looking through its own camera. Every move used to drop that view and
  // jump to wherever the orbit had been parked; now each move asks the world to park it first.
  let parked = 0;
  const nav = new Nav(THREE, () => {});
  nav.leaving = () => { parked++; nav.target.set(7, 0, 7); nav.dist = 3; };
  const moves: Array<[string, () => void]> = [
    ["orbit", () => nav.orbit(5, 0)], ["zoom", () => nav.zoom(-100)], ["zoom toward", () => nav.zoomToward(-100, null)],
    ["pan", () => nav.pan(5, 0, 600)], ["numpad view", () => nav.look([0, 0, 1], "front")], ["nudge", () => nav.nudge(0.1, 0)],
    ["frame", () => nav.frame(new THREE.Vector3(1, 1, 1), 2)],
  ];
  for (const [name, move] of moves) {
    nav.through = "cam";
    const before = parked;
    move();
    ok(name + " parks at the camera first", parked === before + 1);
    ok(name + " leaves the camera", nav.through === "");
  }
  nav.through = "cam";
  nav.zoomToward(-120, new THREE.Vector3(7, 0, 4));
  ok("the zoom starts from the parked pose, not the old orbit", nav.target.distanceTo(new THREE.Vector3(7, 0, 7)) < 3.1);
  const before = parked;
  nav.orbit(3, 0);
  ok("with no camera in use, nothing is parked", parked === before);
  nav.through = "cam";
  nav.leaving = () => { throw new Error("no camera"); };
  nav.orbit(3, 0);
  ok("a failed park still leaves the camera", nav.through === "");
}

section("Nav: zoom toward a point, and fly");
{
  const nav = new Nav(THREE, () => {});
  nav.target.set(0, 0, 0); nav.dist = 150; nav.theta = 0.7; nav.phi = 1.1;
  const P = new THREE.Vector3(30, 4, 60);
  const e0 = nav.position();
  const r0 = P.clone().sub(e0).normalize();
  nav.zoomToward(-120, P);
  const e1 = nav.position();
  near("Nav.zoomToward keeps the point on the same ray", P.clone().sub(e1).normalize().dot(r0), 1, 1e-12);
  ok("and leaves any numpad view", nav.axisView === "");

  const f = new Nav(THREE, () => {});
  f.target.set(1, 2, 3); f.dist = 4; f.theta = 0.3; f.phi = 1.2;
  ok("a fly key is not taken before the flight", !f.flyKey("KeyW", true));
  f.flyStart();
  ok("W is taken while flying", f.flyKey("KeyW", true));
  ok("G is never taken", !f.flyKey("KeyG", true));
  const eye = f.position();
  f.flyLook(25, -10);
  const eye2 = f.position();
  near("Nav.flyLook keeps the eye", eye.distanceTo(eye2), 0, 1e-9);
  f.flySpeed = 10;
  const before = f.position();
  const d0 = f.dist;
  f.flyTick(0.1);
  near("one tenth of a second at 10 m/s is one metre", f.position().distanceTo(before), 1, 1e-9);
  near("the orbit distance rides along", f.dist, d0);
  f.flyEnd();
  ok("landing lets go of the keys", !f.flying && f.flyKeys.size === 0);
  ok("and the keys are Blender's again", !f.flyKey("KeyW", true));
}

// ------------------------------------------------------------------------------------------------
section("signals: once a frame");
{
  const queue: Array<() => void> = [];
  const sig = makeSignal((cb) => { queue.push(cb); return queue.length; });
  let a = 0, b = 0;
  const offA = sig.subscribe(() => { a++; });
  sig.subscribe(() => { b++; });
  for (let i = 0; i < 125; i++) sig.emit();      // a second of a 125 Hz mouse, inside one frame
  eq("125 changes ask for one frame", queue.length, 1);
  eq("nothing is delivered before the frame", [a, b, sig.version()], [0, 0, 0]);
  queue.shift()!();
  eq("the frame delivers once to each listener", [a, b, sig.version()], [1, 1, 1]);
  sig.emit();
  eq("the next change asks for the next frame", queue.length, 1);
  offA();
  queue.shift()!();
  eq("a listener that left hears nothing", [a, b, sig.version()], [1, 2, 2]);
  sig.subscribe(() => { throw new Error("boom"); });
  let c = 0;
  sig.subscribe(() => { c++; });
  sig.emit(); queue.shift()!();
  eq("one listener's throw does not stop the others", c, 1);
}

section("when a fresh parts list needs a re-render");
{
  const part = (key: string, px: number, x = 100, y = 100, extra: Partial<PartView> = {}): PartView => ({
    key, name: key, type: "Mesh", depth: 1, parent: "root", meshes: 1, triangles: 12, visible: true,
    px, at: { x, y, behind: false }, ...extra,
  });
  const A = [part("a", 80), part("b", 30), part("c", 10)];
  ok("the same list", samePartsView(A, A, false));
  ok("sizes moved but none crossed 24 px", samePartsView(A, [part("a", 95), part("b", 26), part("c", 10)], false));
  ok("a part shrank under 24 px", !samePartsView(A, [part("a", 80), part("b", 20), part("c", 10)], false));
  ok("a small part's number changed", !samePartsView(A, [part("a", 80), part("b", 30), part("c", 11)], false));
  ok("hidden", !samePartsView(A, [part("a", 80, 100, 100, { visible: false }), part("b", 30), part("c", 10)], false));
  ok("renamed", !samePartsView(A, [part("a", 80, 100, 100, { name: "door" }), part("b", 30), part("c", 10)], false));
  ok("a part added", !samePartsView(A, [...A, part("d", 50)], false));
  ok("labels on: a size changed", !samePartsView(A, [part("a", 81), part("b", 30), part("c", 10)], true));
  ok("labels on: a label moved under a pixel", samePartsView(A, [part("a", 80, 100.3, 99.8), part("b", 30), part("c", 10)], true));
  ok("labels on: a label moved two pixels", !samePartsView(A, [part("a", 80, 102, 100), part("b", 30), part("c", 10)], true));
  ok("labels off: a label moved two pixels", samePartsView(A, [part("a", 80, 102, 100), part("b", 30), part("c", 10)], false));

  const s = { objects: 578, meshes: 400, triangles: 151000, materials: 60, bbox: [300, 40, 300], error: "", drawn: true };
  eq("the frame rate is not the scene", statsShape({ ...s, fps: 60, drawCalls: 300 } as any), statsShape({ ...s, fps: 144, drawCalls: 290 } as any));
  ok("a triangle count is", statsShape(s) !== statsShape({ ...s, triangles: 151012 }));
  ok("so is a render error", statsShape(s) !== statsShape({ ...s, error: "boom" }));
  eq("no stats", statsShape(null), "");
}

// ------------------------------------------------------------------------------------------------
section("a name for what the game never named");
{
  const box = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ color: 0x6b3f2a }));
  eq("a coloured box", describeObject(box), "Mesh · Box · #6b3f2a");
  box.name = "crate";
  eq("a name the game gave wins", describeObject(box), "crate");
  box.name = "   ";
  eq("a blank name is no name", describeObject(box), "Mesh · Box · #6b3f2a");
  const mat = new THREE.MeshStandardMaterial(); mat.name = "bark";
  eq("a material's name beats its colour", describeObject(new THREE.Mesh(new THREE.CylinderGeometry(), mat)), "Mesh · Cylinder · bark");
  const g = new THREE.Group(); g.add(new THREE.Object3D(), new THREE.Object3D(), new THREE.Object3D());
  eq("a group says how much it holds", describeObject(g), "Group · 3 children");
  // A loaded model: four anonymous groups, then glTF's own Scene and Armature, then the name
  // that says which model it is.
  const chain = new THREE.Group(); let at: THREE.Object3D = chain;
  for (const n of ["", "", "", "Scene", "Armature"]) { const c = new THREE.Group(); c.name = n; at.add(c); at = c; }
  const skin = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); skin.name = "character"; at.add(skin);
  eq("a loaded model's outer group names the model", describeObject(chain), "Group · 1 child · character");
  const bare = new THREE.Group(); const sc = new THREE.Group(); sc.name = "Scene"; bare.add(sc);
  eq("export names alone say nothing", describeObject(bare), "Group · 1 child");
  const inst = new THREE.InstancedMesh(new THREE.SphereGeometry(), new THREE.MeshBasicMaterial({ color: 0x00ff00 }), 12);
  eq("instances say how many", describeObject(inst), "Instances · Sphere · #00ff00 · ×12");
  eq("a light", describeObject(new THREE.PointLight()), "Point light");
  eq("a sun", describeObject(new THREE.DirectionalLight()), "Directional light");
  eq("a camera", describeObject(new THREE.PerspectiveCamera()), "Camera");
  eq("nothing", describeObject(null), "(nothing)");
}

// ------------------------------------------------------------------------------------------------
section("the two settings");
{
  eq("defaults: Blender's mouse, zoom to the cursor", [ENGINE_DEFAULTS.nav_scheme, ENGINE_DEFAULTS.zoom_to_cursor], ["blender", true]);
  eq("Unity is kept", mergePrefs({ nav_scheme: "unity" }).nav_scheme, "unity");
  eq("an unknown layout falls back", mergePrefs({ nav_scheme: "maya" }).nav_scheme, "blender");
  eq("zoom to the centre is kept", mergePrefs({ zoom_to_cursor: false }).zoom_to_cursor, false);
  eq("a junk value falls back", mergePrefs({ zoom_to_cursor: "no" }).zoom_to_cursor, true);
  const o = worldOptions(mergePrefs({ nav_scheme: "unity", zoom_to_cursor: false }));
  eq("both reach the world", [o.navScheme, o.zoomToCursor], ["unity", false]);
}

console.log("\n" + pass + " passed, " + fails.length + " failed");
if (fails.length) {
  for (const f of fails) console.log("  FAIL " + f);
  process.exit(1);
}
