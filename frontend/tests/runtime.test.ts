// The Studio runtime (studio-runtime.js), tested on the promises a shipped game relies on.
//
// Real three in node for the three.js half, and a small PlayCanvas stand-in with the surface the
// runtime touches (entities, a render component, a mesh, a material, an asset registry) for the
// other. What is checked is what would be wrong without anyone noticing: a key that means one
// object in the editor and another in the game, a rotation read as degrees, a placement that turns
// into two after a second apply, a clone that is never made because the game spawned its source
// late, a primitive that is not the shape the editor placed.
//
// Run: npm run test:runtime

import * as THREE from "three";
import {
  applyStudioEdits, engineOf, findByKey, loadStudioEdits, placeStudioItem, rootOf, shapeArrays, studioKeys,
  unplaceStudioItem, version, type Report,
} from "../src/components/engine/edit/runtime";
import { quatFromEulerXYZ, stableKeys } from "../src/components/engine/edit/ops";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
};
const near = (name: string, got: number, want: number, tol = 1e-6) =>
  ok(name, Math.abs(got - want) <= tol, "got " + got + ", want " + want);
const eq = (name: string, got: unknown, want: unknown) =>
  ok(name, JSON.stringify(got) === JSON.stringify(want), "got " + JSON.stringify(got) + ", want " + JSON.stringify(want));
const section = (s: string) => console.log("- " + s);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const settled = (): { opts: { onSettle: (r: Report) => void }; done: Promise<Report> } => {
  let fire: (r: Report) => void = () => {};
  const done = new Promise<Report>((r) => { fire = r; });
  return { opts: { onSettle: (r: Report) => fire(r) }, done };
};
const within = <T>(p: Promise<T>, ms: number): Promise<T | "timeout"> => Promise.race([p, sleep(ms).then(() => "timeout" as const)]);

const { mkdirSync, writeFileSync, readFileSync } = await import("node:fs");
const { pathToFileURL, fileURLToPath } = await import("node:url");
const { dirname, join } = await import("node:path");

// ------------------------------------------------------------------ a scene like the proof game
/** The courtyard of the proof game: a floor, four pillars (groups of three meshes with repeated
 *  child names), two lanterns with point lights, five crystals, a sun, a camera. */
function courtyard() {
  const T = THREE;
  const scene = new T.Scene();
  scene.background = new T.Color(0x0d0f1a);
  const stone = new T.MeshStandardMaterial({ color: 0xc9c2b2, roughness: 0.9 });
  const floor = new T.Mesh(new T.PlaneGeometry(16, 16), new T.MeshStandardMaterial({ roughness: 0.95 }));
  floor.name = "floor";
  scene.add(floor);
  const pillars: any[] = [];
  [[-5, -5], [5, -5], [-5, 5], [5, 5]].forEach(([x, z], i) => {
    const g = new T.Group();
    g.name = "pillar-" + (i + 1);
    const plinth = new T.Mesh(new T.BoxGeometry(0.9, 0.3, 0.9), stone); plinth.name = "pillar-plinth"; plinth.position.y = 0.15;
    const shaft = new T.Mesh(new T.CylinderGeometry(0.3, 0.34, 2.4, 12), stone); shaft.name = "pillar-shaft"; shaft.position.y = 1.5;
    const cap = new T.Mesh(new T.BoxGeometry(0.8, 0.3, 0.8), stone); cap.name = "pillar-cap"; cap.position.y = 2.85;
    g.add(plinth, shaft, cap);
    g.position.set(x, 0, z);
    scene.add(g);
    pillars.push(g);
  });
  [[-2, 6], [2, 6]].forEach(([x, z], i) => {
    const g = new T.Group(); g.name = "lantern-" + (i + 1);
    const light = new T.PointLight(0xffc36b, 6, 7, 2); light.name = "lantern-light"; light.position.y = 1.7;
    g.add(light); g.position.set(x, 0, z); scene.add(g);
  });
  for (let i = 0; i < 5; i++) {
    const g = new T.Group(); g.name = "crystal-" + (i + 1);
    const body = new T.Mesh(new T.OctahedronGeometry(0.35), new T.MeshStandardMaterial({ color: 0x6ae3ff })); body.name = "crystal-body";
    g.add(body); scene.add(g);
  }
  const sun = new T.DirectionalLight(0xffffff, 1.4); sun.name = "sun"; scene.add(sun);
  const cam = new T.PerspectiveCamera(55, 1.5, 0.1, 100); cam.name = "camera"; scene.add(cam);
  const anon = new T.Mesh(new T.BoxGeometry(1, 1, 1), stone); scene.add(anon);           // no name: a path key
  return { scene, pillars, floor, sun, cam, anon };
}

// ------------------------------------------------------------------ engine, root and keys
section("engine, root and keys");
{
  const { scene, pillars, anon } = courtyard();
  eq("version is a number", typeof version, "number");
  eq("a three scene is three", engineOf(scene), "three");
  eq("a three mesh is three", engineOf(pillars[0].children[0]), "three");
  ok("a three object is its own root", rootOf(scene) === scene);
  eq("anything else is nothing", [engineOf(null), engineOf({}), engineOf(42), engineOf({ children: [] })], ["", "", "", ""]);
  const a = studioKeys(scene);
  eq("a unique name is its key", a.get(pillars[1]), "pillar-2");
  eq("a repeated name is numbered in traversal order", [a.get(pillars[0].children[0]), a.get(pillars[3].children[0])], ["pillar-plinth.000", "pillar-plinth.003"]);
  ok("an unnamed object is keyed by its path", /^\/\d+$/.test(a.get(anon) || ""), a.get(anon));
  ok("findByKey finds each of them", ["pillar-2", "pillar-cap.002", "sun", a.get(anon)!].every((k) => !!findByKey(scene, k) && a.get(findByKey(scene, k)) === k));
  ok("findByKey of nothing is null", findByKey(scene, "no-such-thing") === null && findByKey(scene, "") === null);
  // The bundle runs from data/tmp; the source is two folders up from there.
  const at = dirname(fileURLToPath(import.meta.url));
  const file = ["../../frontend/src/components/engine/edit/runtime.ts", "../src/components/engine/edit/runtime.ts"]
    .map((p) => join(at, p)).find((p) => { try { readFileSync(p); return true; } catch { return false; } }) || "";
  const src = readFileSync(file, "utf8").replace(/\/\/[^\n]*|\/\*[\s\S]*?\*\//g, "");
  ok("the runtime's code never converts to degrees", !/180|toDegrees|RAD2DEG|DEG2RAD/.test(src));
  ok("the runtime imports no engine", !/from\s+["'](three|playcanvas)["']/.test(src));

  // THE BUILT FILE, not the source: the bundle also carries ops.ts, gamedress.ts, pieces.ts,
  // model.ts and terrain.ts, and one `import * as THREE` in any of them would put a second copy of
  // three inside every game while a check on runtime.ts alone still passed.
  const dist = ["../../frontend/dist/studio-runtime.js", "../dist/studio-runtime.js"]
    .map((p) => join(at, p)).find((p) => { try { readFileSync(p); return true; } catch { return false; } }) || "";
  ok("the runtime is built (npm run build:runtime)", !!dist, "no dist/studio-runtime.js");
  if (dist) {
    const js = readFileSync(dist, "utf8");
    ok("the bundle imports nothing: no second engine can ride in", !/(^|[;\s])import\s*[\w*{\s,}]*\s*from\s*["']|^\s*import\s*["']/m.test(js));
    ok("the bundle stays under 40 KB", js.length < 40 * 1024, js.length + " bytes");
    const exported = new Set<string>();
    const m = /export\s*\{([^}]*)\}\s*;?\s*$/m.exec(js);
    for (const part of (m ? m[1] : "").split(",")) {
      const name = part.trim().split(/\s+as\s+/).pop()!.trim();
      if (name) exported.add(name);
    }
    const want = ["version", "engineOf", "rootOf", "studioKeys", "findByKey", "loadStudioEdits",
                  "applyStudioEdits", "placeStudioItem", "unplaceStudioItem"];
    ok("it exports the contract's API", want.every((w) => exported.has(w)), [...exported].join(", "));
  }
}

// ------------------------------------------------------------------ a placement keeps the game's keys
section("a placement never renumbers the game's keys");
{
  // Two of the game's own "a", a "b", and a placed copy that carries an "a" inside it. Counted
  // together, the copy's inner "a" made the game's keys a.000 / a.001 / a.002 — and an edit made
  // then was saved under a key that meant another object once the copy was gone.
  const T = THREE;
  const root = new T.Scene();
  const mk = (name: string) => { const o = new T.Object3D(); o.name = name; return o; };
  const a1 = mk("a"), a2 = mk("a"), b = mk("b");
  root.add(a1, a2, b);
  const before = studioKeys(root);
  const copy = mk("a"); copy.userData.studioPlaced = "p1";
  const inner = mk("a"), c = mk("c");
  copy.add(inner, c);
  root.add(copy);
  const crate = mk("crate-6"); crate.userData.studioPlaced = "p2";
  root.add(crate);
  const after = studioKeys(root);
  eq("the game's keys are the same with the copy in the scene", [after.get(a1), after.get(a2), after.get(b)],
     [before.get(a1), before.get(a2), before.get(b)]);
  eq("...which are the game's own numbering", [after.get(a1), after.get(a2), after.get(b)], ["a.000", "a.001", "b"]);
  eq("a placement named like a game object is keyed name#id", after.get(copy), "a#p1");
  eq("what is inside it is keyed under it", [after.get(inner), after.get(c)], ["a#p1/a", "a#p1/c"]);
  eq("a placement with a name of its own keeps it", after.get(crate), "crate-6");
  ok("findByKey finds a placement and its insides", findByKey(root, "a#p1") === copy && findByKey(root, "a#p1/c") === c);
  ok("no two objects share a key", new Set(after.values()).size === after.size);
  const pc = { name: "Root", children: [] as any[] };
  const pa = { name: "a", children: [] as any[] }, pb = { name: "a", children: [] as any[] };
  const pp: any = { name: "a", __studioPlaced: "q1", children: [{ name: "a", children: [] }] };
  pc.children.push(pa, pb, pp);
  const pk = studioKeys(pc);
  eq("PlayCanvas: the same rule, read from __studioPlaced", [pk.get(pa), pk.get(pb), pk.get(pp), pk.get(pp.children[0])],
     ["a.000", "a.001", "a#q1", "a#q1/a"]);
}

// ------------------------------------------------------------------ primitives: three's own shapes
section("primitives are three's own shapes, number for number");
{
  const T = THREE;
  const refs: Record<string, any> = {
    box: new T.BoxGeometry(1, 1, 1),
    sphere: new T.SphereGeometry(0.5, 32, 20),
    cylinder: new T.CylinderGeometry(0.5, 0.5, 1, 28),
    cone: new T.ConeGeometry(0.5, 1, 28),
    plane: new T.PlaneGeometry(1, 1),
    torus: new T.TorusGeometry(0.4, 0.16, 16, 40),
  };
  for (const [shape, g] of Object.entries(refs)) {
    const a = shapeArrays(shape);
    const same = (x: ArrayLike<number>, y: ArrayLike<number>) => x.length === y.length && Array.from(x).every((v, i) => Math.abs(v - y[i]) < 1e-6);
    ok(shape + ": positions", same(a.pos, g.attributes.position.array), a.pos.length + " vs " + g.attributes.position.array.length);
    ok(shape + ": normals", same(a.nor, g.attributes.normal.array));
    ok(shape + ": uvs", same(a.uv, g.attributes.uv.array));
    ok(shape + ": triangles", same(a.idx, g.index.array), a.idx.length + " vs " + g.index.count);
  }
  eq("an unknown shape is a box", shapeArrays("blob").pos.length, 72);
}

// ------------------------------------------------------------------ three: parts and world
section("three: parts, world, and applying twice");
{
  const { scene, pillars, sun, cam, anon } = courtyard();
  const shaft = pillars[1].children[1];
  const r1 = await applyStudioEdits(scene, {
    parts: {
      "pillar-2": { pos: [4, 0, -4], rot: [0, 0.5, 0], scale: [1, 1.5, 1] },
      "pillar-shaft.001": { color: "#ff0000", roughness: 0.25 },
      "crystal-3": { hidden: true },
      sun: { intensity: 3, color: "#00ff00" },
      camera: { fov: 40 },
      [studioKeys(scene).get(anon)!]: { pos: [9, 9, 9] },
      ghost: { pos: [1, 1, 1] },
      "bad-one": { pos: [1, "x", 2], rot: "nope" },
    },
    world: { background: "#102030", fog: { type: "linear", color: "#223344", near: 5, far: 40 } },
  }, { retryMs: 0 });
  eq("six parts applied, the ghost named missing", [r1.parts, r1.missing], [6, ["ghost"]]);
  eq("a malformed override costs that override only", r1.errors, []);
  eq("position, local, as saved", pillars[1].position.toArray(), [4, 0, -4]);
  ok("rotation in radians, exactly as saved", pillars[1].rotation.y === 0.5 && pillars[1].rotation.x === 0);
  eq("scale", pillars[1].scale.toArray(), [1, 1.5, 1]);
  eq("colour and roughness on the part's material", [shaft.material.color.getHexString(), shaft.material.roughness], ["ff0000", 0.25]);
  ok("hidden", scene.getObjectByName("crystal-3")!.visible === false);
  eq("a light's colour and intensity", [sun.color.getHexString(), sun.intensity], ["00ff00", 3]);
  ok("a camera's fov, and its projection rebuilt", cam.fov === 40 && Math.abs(cam.projectionMatrix.elements[5] - 1 / Math.tan((40 * Math.PI) / 360)) < 1e-9);
  eq("an unnamed object by its path key", anon.position.toArray(), [9, 9, 9]);
  eq("background", (scene.background as any).getHexString(), "102030");
  ok("a fog where the game had none: three's renderer reads isFog, color, near, far", !!scene.fog && (scene.fog as any).isFog === true
    && (scene.fog as any).color.getHexString() === "223344" && (scene.fog as any).near === 5 && (scene.fog as any).far === 40);
  ok("and a Scene can still copy and serialise it", !!(scene.fog as any).clone().isFog && (scene.toJSON() as any).object.fog.type === "Fog");
  const r2 = await applyStudioEdits(scene, { parts: { "pillar-2": { pos: [4, 0, -4], rot: [0, 0.5, 0] } }, world: { fog: { type: "linear", color: "#000000", near: 1, far: 9 } } }, { retryMs: 0 });
  eq("applying again changes nothing twice", [pillars[1].position.toArray(), pillars[1].rotation.y, r2.parts], [[4, 0, -4], 0.5, 1]);
  eq("an existing fog is changed in place", [(scene.fog as any).near, (scene.fog as any).far, (scene.fog as any).color.getHexString()], [1, 9, "000000"]);
  const r3 = await applyStudioEdits(scene, { parts: {}, mods: [{ op: "weld", target: "", args: {} }, { op: "smooth", off: true }] }, { retryMs: 0 });
  ok("a modifier stack is refused in words, not dropped silently", r3.errors.length === 1 && /1 modifier is not applied/.test(r3.errors[0]) && /forge-ops/.test(r3.errors[0]), r3.errors.join("|"));
  const r4 = await applyStudioEdits({ nope: true }, { parts: {} });
  ok("not an engine object is said plainly", r4.errors.length === 1 && /not a three\.js object/.test(r4.errors[0]));
  const r5 = await applyStudioEdits(scene, null, { retryMs: 0 });
  eq("no document is nothing to do", [r5.parts, r5.placed, r5.errors], [0, 0, []]);
}

// ------------------------------------------------------------------ three: a piece of a merged mesh
section("three: a piece of a merged mesh moves from its rest corners");
{
  const T = THREE;
  // Three boxes merged into one mesh, the way a game merges its scenery.
  const pos: number[] = [], idx: number[] = [];
  for (const cx of [0, 3, 6]) {
    const b = new T.BoxGeometry(1, 1, 1); b.translate(cx, 0, 0);
    const base = pos.length / 3;
    pos.push(...Array.from(b.attributes.position.array as Float32Array));
    for (const i of Array.from(b.index!.array as Uint16Array)) idx.push(i + base);
  }
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  const scene = new T.Scene();
  const props = new T.Mesh(g, new T.MeshStandardMaterial()); props.name = "base-props";
  scene.add(props);
  // Box two is triangles 12..24, its middle at x = 3, 24 corners.
  const edit = { parts: { "base-props~t12-24": { pos: [3, 2, 0], piece: { c: [3, 0, 0], n: 24 } } } };
  const r = await applyStudioEdits(scene, edit, { retryMs: 0 });
  const ys = (t0: number) => { const a = g.attributes.position.array as Float32Array; let lo = Infinity; for (let t = t0; t < t0 + 12; t++) for (let k = 0; k < 3; k++) lo = Math.min(lo, a[idx[t * 3 + k] * 3 + 1]); return lo; };
  eq("the piece applied", [r.parts, r.errors], [1, []]);
  near("its corners went up by 2 m", ys(12), 1.5);
  near("the pieces either side did not move", ys(0), -0.5);
  near("", ys(24), -0.5);
  await applyStudioEdits(scene, edit, { retryMs: 0 });
  near("applied twice, it is still 2 m up, not 4", ys(12), 1.5);
  const bad = await applyStudioEdits(scene, { parts: { "base-props~t12-24": { pos: [3, 9, 0], piece: { c: [3, 0, 0], n: 30 } } } }, { retryMs: 0 });
  ok("a piece whose corner count changed is refused, and says why", bad.parts === 0 && /corners now/.test(bad.errors[0] || ""), bad.errors.join("|"));
  near("and nothing moved", ys(12), 1.5);
}

// ------------------------------------------------------------------ three: every kind of placement
section("three: placements");
const here = dirname(fileURLToPath(import.meta.url));
const tmp = join(here, "runtime-fixtures");
mkdirSync(join(tmp, "src"), { recursive: true });
writeFileSync(join(tmp, "src", "assets.mjs"), [
  "// A builder the way a game writes one: named group, feet on y = 0. Three comes from the test.",
  "export function buildTower({ height = 2, color = '#88aa44' } = {}) {",
  "  const T = globalThis.__T;",
  "  const g = new T.Group(); g.name = 'tower';",
  "  const m = new T.Mesh(new T.BoxGeometry(1, height, 1), new T.MeshStandardMaterial({ color }));",
  "  m.name = 'tower-body'; m.position.y = height / 2; g.add(m);",
  "  return g;",
  "}",
  "export class Crate { constructor(size = 1) { const T = globalThis.__T; this.object = new T.Mesh(new T.BoxGeometry(size, size, size), new T.MeshStandardMaterial()); } }",
  "export async function buildLater() { await new Promise((r) => setTimeout(r, 5)); return { group: new globalThis.__T.Group() }; }",
  "export function buildNothing() { return 42; }",
  "export function buildPc(app) { return new app.root.constructor('pc-thing', app); }",
].join("\n"));
const base = pathToFileURL(tmp + "/").href;
(globalThis as any).__T = THREE;
{
  const { scene, pillars } = courtyard();
  const Mesh = pillars[0].children[0].constructor;
  const item = (id: string, ref: any, pos: number[] = [0, 0, 0], extra: any = {}) => ({ id, name: extra.name || id, ref, pos, rot: extra.rot || [0, 0, 0], scale: extra.scale || [1, 1, 1] });

  const box = await placeStudioItem(scene, item("p-box", { kind: "primitive", shape: "box", color: "#ff8800" }, [1, 0.5, 2], { name: "orange-box", rot: [0, 0.3, 0], scale: [2, 1, 1] }));
  ok("a primitive is placed", box.ok && !!box.object, box.error);
  const o = box.object;
  ok("made with the GAME's three: its Mesh class, a standard material", o instanceof THREE.Mesh && o.constructor === Mesh && o.material instanceof THREE.MeshStandardMaterial);
  eq("named, marked, parented to the root, keyed by its name", [o.name, o.userData.studioPlaced, o.parent === scene, box.key], ["orange-box", "p-box", true, "orange-box"]);
  eq("its transform as given, rotation in radians", [o.position.toArray(), o.rotation.y, o.scale.toArray()], [[1, 0.5, 2], 0.3, [2, 1, 1]]);
  eq("its colour and the editor's roughness and metalness", [o.material.color.getHexString(), o.material.roughness, o.material.metalness], ["ff8800", 0.7, 0.05]);
  o.geometry.computeBoundingBox();
  const size = o.geometry.boundingBox.getSize(new THREE.Vector3()).multiply(o.scale);
  const bb = new THREE.Box3().setFromObject(o);
  ok("and it is the editor's unit box: 2 x 1 x 1 at that scale, its bottom on y = 0", Math.abs(size.x - 2) < 1e-6 && Math.abs(size.y - 1) < 1e-6
    && Math.abs(size.z - 1) < 1e-6 && Math.abs(bb.min.y) < 1e-6, JSON.stringify(size));

  let freed = 0;
  o.geometry.addEventListener("dispose", () => freed++);
  o.material.addEventListener("dispose", () => freed++);
  const again = await placeStudioItem(scene, item("p-box", { kind: "primitive", shape: "sphere", color: "#0000ff" }, [5, 0, 5], { name: "orange-box" }));
  let marked = 0; scene.traverse((n: any) => { if (n.userData.studioPlaced === "p-box") marked++; });
  ok("the same id twice leaves one object, the new one", again.ok && marked === 1 && again.object.geometry.attributes.position.count === 693 && !o.parent);
  eq("and the one it replaced gave its geometry and material back", freed, 2);

  const plane = await placeStudioItem(scene, item("p-plane", { kind: "primitive", shape: "plane", color: "#ffffff" }, [0, 0, 0], { rot: [-Math.PI / 2, 0, 0] }));
  ok("a plane is double-sided, laid flat by its own rotation", plane.object.material.side === THREE.DoubleSide && Math.abs(plane.object.rotation.x + Math.PI / 2) < 1e-12);
  const lamp = await placeStudioItem(scene, item("p-lamp", { kind: "primitive", shape: "pointLight", color: "#ffeeaa" }, [0, 3, 0]));
  ok("a light primitive uses the game's own PointLight", lamp.ok && lamp.object instanceof THREE.PointLight && lamp.object.castShadow === true);
  const dir = await placeStudioItem(new THREE.Scene().add(new THREE.Mesh()), item("p-dir", { kind: "primitive", shape: "dirLight" }));
  ok("a light the game has none of is refused in words", !dir.ok && /no dirLight/.test(dir.error || ""), dir.error);

  const copy = await placeStudioItem(scene, item("p-copy", { kind: "clone", of: "pillar-3" }, [0, 0, -8], { name: "pillar-5" }));
  const p3 = scene.getObjectByName("pillar-3")!;
  ok("a clone copies the game's object and shares its geometry", copy.ok && copy.object !== p3 && copy.object.children[0].geometry === p3.children[0].geometry);
  eq("the original is untouched; the copy is where it was put", [p3.position.toArray(), copy.object.position.toArray(), copy.key], [[-5, 0, 5], [0, 0, -8], "pillar-5"]);
  const k = studioKeys(scene);
  eq("the game's keys are unchanged by a uniquely named placement", [k.get(pillars[0]), k.get(pillars[2].children[2])], ["pillar-1", "pillar-cap.002"]);

  const code = await placeStudioItem(scene, item("p-tower", { kind: "code", file: "src/assets.mjs", export: "buildTower", args: [{ height: 3, color: "#123456" }] }, [2, 0, 2], { name: "tower-1" }), { base });
  ok("a code placement calls the game's own builder with its args", code.ok && code.object.children[0].geometry.parameters.height === 3
    && code.object.children[0].material.color.getHexString() === "123456", code.error);
  eq("and says how", code.how, "built by buildTower()");
  const cls = await placeStudioItem(scene, item("p-crate", { kind: "code", file: "src/assets.mjs", export: "Crate", args: [2] }), { base });
  ok("a class is constructed, and the object it holds is placed", cls.ok && cls.object.geometry.parameters.width === 2, cls.error);
  const late = await placeStudioItem(scene, item("p-later", { kind: "code", file: "src/assets.mjs", export: "buildLater" }), { base });
  ok("an async builder is awaited, and a wrapper unwrapped", late.ok && late.object.isGroup, late.error);
  const deep = await placeStudioItem(scene, item("p-deep", { kind: "code", file: "my-game/src/assets.mjs", export: "buildTower" }), { base });
  ok("a workspace-relative path is found one folder shorter", deep.ok, deep.error);
  const none = await placeStudioItem(scene, item("p-none", { kind: "code", file: "src/assets.mjs", export: "buildNothing" }), { base });
  ok("a builder that returns nothing placeable is refused in words", !none.ok && /nothing that can be placed/.test(none.error || ""), none.error);
  const missing = await placeStudioItem(scene, item("p-miss", { kind: "code", file: "src/assets.mjs", export: "buildGhost" }), { base });
  ok("a missing export is named", !missing.ok && /exports no function called buildGhost/.test(missing.error || ""), missing.error);

  // A model: the loader is the game's (here a stand-in that "loads" a two-variant file).
  const urls: string[] = [];
  class FakeGLTF { async loadAsync(url: string) {
    urls.push(url);
    const s = new THREE.Group(); s.name = "Scene";
    for (const n of ["Hero", "Hero_Gold"]) { const m = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), new THREE.MeshStandardMaterial()); m.name = n; s.add(m); }
    return { scene: s };
  } }
  const model = await placeStudioItem(scene, item("p-model", { kind: "model", url: "assets/hero.glb", nodes: ["Hero"] }, [0, 0, 3], { name: "hero-1" }),
    { GLTFLoader: FakeGLTF, url: (p, kind) => (kind === "model" ? "http://studio/api/engine/model?file=" + encodeURIComponent(p) : "") });
  ok("a model is loaded with the game's loader, through the URL it was told", model.ok && urls[0] === "http://studio/api/engine/model?file=assets%2Fhero.glb", model.error || urls[0]);
  eq("only the node the table names is shown", model.object.children.map((c: any) => c.visible), [true, false]);
  const noLoader = await placeStudioItem(scene, item("p-model2", { kind: "model", url: "a.glb" }));
  ok("a model with no loader says what it needs", !noLoader.ok && /GLTFLoader/.test(noLoader.error || ""), noLoader.error);

  eq("unplace takes it out and says how many", [unplaceStudioItem(scene, "p-tower"), scene.getObjectByName("tower-1") ?? null], [1, null]);
  eq("unplacing what is not there is zero", unplaceStudioItem(scene, "p-tower"), 0);
}

// ------------------------------------------------------------------ three: the whole document, twice
section("three: the placed list, applied, reapplied and pruned");
{
  const { scene } = courtyard();
  const doc = {
    parts: { "pillar-1": { pos: [-4, 0, -5] }, "box-a": { color: "#00ff00" } },
    placed: [
      { id: "a", name: "box-a", ref: { kind: "primitive", shape: "box", color: "#ff0000" }, pos: [0, 0.5, 0], rot: [0, 0, 0], scale: [1, 1, 1] },
      { id: "b", name: "crystal-6", ref: { kind: "clone", of: "crystal-2" }, pos: [3, 0, 3], rot: [0, 1, 0], scale: [1, 1, 1] },
      { id: "bad" },
    ],
  };
  const r1 = await applyStudioEdits(scene, doc, { retryMs: 0 });
  eq("two placed, the malformed entry dropped, parts applied", [r1.placed, r1.parts, r1.errors, r1.missing], [2, 2, [], []]);
  ok("a part keyed to a placement lands on it", (scene.getObjectByName("box-a") as any).material.color.getHexString() === "00ff00");
  await applyStudioEdits(scene, doc, { retryMs: 0 });
  const count = (id: string) => { let n = 0; scene.traverse((o: any) => { if (o.userData.studioPlaced === id) n++; }); return n; };
  eq("applied twice: one of each, never two", [count("a"), count("b")], [1, 1]);
  await applyStudioEdits(scene, { ...doc, placed: [doc.placed[1]] }, { retryMs: 0 });
  eq("a placement the file no longer lists is taken out", [count("a"), count("b")], [0, 1]);
  await applyStudioEdits(scene, { parts: {} }, { retryMs: 0, placed: false });
  eq("placed:false leaves placements alone", count("b"), 1);
}

// ------------------------------------------------------------------ late things
section("late things: a clone of what the game spawns later, a part built later");
{
  const { scene } = courtyard();
  const s = settled();
  const rep = await applyStudioEdits(scene, {
    parts: { "late-part": { pos: [0, 5, 0] } },
    placed: [{ id: "c1", name: "rock-copy", ref: { kind: "clone", of: "late-rock" }, pos: [1, 0, 1], rot: [0, 0, 0], scale: [2, 2, 2] }],
  }, { retryMs: 4000, ...s.opts });
  eq("at first: the clone is pending and the part missing", [rep.pending, rep.placed, rep.missing], [1, 0, ["late-part"]]);
  await sleep(150);
  const rock = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial()); rock.name = "late-rock";
  const part = new THREE.Group(); part.name = "late-part";
  scene.add(rock, part);
  const fin = await within(s.done, 3000);
  ok("it settles once both exist", fin !== "timeout");
  ok("the report it returned is the same object, now true", fin === rep);
  eq("the clone was made, the part moved", [rep.pending, rep.placed, rep.missing, part.position.y], [0, 1, [], 5]);
  const copy = scene.getObjectByName("rock-copy") as any;
  ok("the late clone is a copy of the late rock, where the file puts it", !!copy && copy.geometry === rock.geometry && copy.scale.x === 2 && copy.position.x === 1);

  const s2 = settled();
  const r2 = await applyStudioEdits(scene, { placed: [{ id: "c2", name: "never", ref: { kind: "clone", of: "never-built" }, pos: [0, 0, 0] }] }, { retryMs: 700, ...s2.opts });
  eq("a clone of something that never comes is pending", r2.pending, 1);
  const f2 = await within(s2.done, 3000);
  ok("and is given up on in words when its time is up", f2 !== "timeout" && r2.pending === 0 && /never-built/.test(r2.errors.join(" ")) && /gave up/.test(r2.errors.join(" ")), r2.errors.join("|"));

  const r3 = await applyStudioEdits(scene, { placed: [{ id: "c3", name: "n3", ref: { kind: "clone", of: "nope" } }] }, { retryMs: 0 });
  ok("with retrying off, a missing source is an error at once", r3.pending === 0 && r3.errors.length === 1, r3.errors.join("|"));

  const s4 = settled();
  const p4 = await placeStudioItem(scene, { id: "c4", name: "late-copy", ref: { kind: "clone", of: "later-still" }, pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] }, { retryMs: 3000, ...s4.opts });
  ok("placeStudioItem says pending too", !p4.ok && p4.pending === true);
  const ls = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)); ls.name = "later-still"; scene.add(ls);
  await within(s4.done, 3000);
  ok("and makes it when its source appears", !!scene.getObjectByName("late-copy"));

  // An agent's live move (parts only) in the middle of a wait must not cancel the wait: the old
  // runtime replaced one job with the next, and the clone was never made.
  const s6 = settled();
  const r6 = await applyStudioEdits(scene, { placed: [{ id: "c6", name: "late-copy-2", ref: { kind: "clone", of: "spawns-after-a-move" }, pos: [0, 0, 0] }] }, { retryMs: 3000, ...s6.opts });
  const r6b = await applyStudioEdits(scene, { parts: { "late-rock": { pos: [7, 0, 7] } } }, { placed: false, retryMs: 0 });
  eq("a parts-only apply leaves the waiting clone alone", [r6.pending, r6b.parts, r6b.pending], [1, 1, 0]);
  const sp = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)); sp.name = "spawns-after-a-move"; scene.add(sp);
  ok("and the clone is made when its source spawns", (await within(s6.done, 3000)) === r6 && r6.placed === 1 && !!scene.getObjectByName("late-copy-2"));
  // A full apply owns the placement set: what an older one was waiting for is dropped, in words.
  const s7 = settled();
  const r7 = await applyStudioEdits(scene, { placed: [{ id: "c7", name: "never-2", ref: { kind: "clone", of: "not-yet" }, pos: [0, 0, 0] }] }, { retryMs: 3000, ...s7.opts });
  await applyStudioEdits(scene, { placed: [] }, { retryMs: 0 });
  const f7 = await within(s7.done, 1000);
  ok("a newer full apply drops an older wait and says so", f7 === r7 && r7.pending === 0 && /replaced by a newer apply/.test(r7.errors.join(" ")), r7.errors.join("|"));

  const empty = new THREE.Scene();
  const s5 = settled();
  const r5 = await applyStudioEdits(empty, { placed: [{ id: "e1", name: "first-box", ref: { kind: "primitive", shape: "box" }, pos: [0, 0, 0] }] }, { retryMs: 3000, ...s5.opts });
  eq("a primitive in a scene with no mesh yet waits for one", r5.pending, 1);
  empty.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial()));
  await within(s5.done, 3000);
  ok("and is made once the game has built something", !!empty.getObjectByName("first-box") && r5.placed === 1);
}

// ------------------------------------------------------------------ loadStudioEdits
section("loading the document");
{
  const real = globalThis.fetch;
  const answer = (status: number, body: any) => async () => ({ ok: status < 400, status, json: async () => (typeof body === "string" ? JSON.parse(body) : body) }) as any;
  const warn = console.warn; console.warn = () => {};
  try {
    globalThis.fetch = answer(404, {});
    eq("a 404 is no edits, not an error", await loadStudioEdits("x.json"), null);
    globalThis.fetch = answer(200, { parts: { a: { pos: [1, 2, 3] } } });
    eq("a document is returned", await loadStudioEdits("x.json"), { parts: { a: { pos: [1, 2, 3] } } });
    globalThis.fetch = answer(200, { path: "C:/x/studio.edits.json", text: '{"placed":[]}' });
    eq("the Studio's {text} answer is unwrapped", await loadStudioEdits("x.json"), { placed: [] });
    globalThis.fetch = answer(200, "{not json");
    eq("a broken file is no edits, with a warning", await loadStudioEdits("x.json"), null);
    globalThis.fetch = answer(500, {});
    eq("a server error is no edits", await loadStudioEdits("x.json"), null);
    globalThis.fetch = (async () => { throw new Error("offline"); }) as any;
    eq("no network is no edits", await loadStudioEdits("x.json"), null);
  } finally { globalThis.fetch = real; console.warn = warn; }
}

// ------------------------------------------------------------------ a PlayCanvas stand-in
// The surface the runtime and pcApply touch, and nothing more: entities with local transforms,
// a world transform (position and scale are enough here), components, clone(); a render
// component whose mesh instances have a world box; Mesh, MeshInstance, a standard material; an
// asset registry that "loads" a container. Built like PlayCanvas: `new Entity(name, app)`.
let CURRENT: any = null;
class V { constructor(public x = 0, public y = 0, public z = 0) {} }
class Q { constructor(public x = 0, public y = 0, public z = 0, public w = 1) {} }
class Col { constructor(public r = 1, public g = 1, public b = 1, public a = 1) {} set(r: number, g: number, b: number, a = 1) { this.r = r; this.g = g; this.b = b; this.a = a; return this; } }
class FMesh {
  pos: number[] = []; nor: number[] = []; uv: number[] = []; idx: number[] = []; updated = 0;
  constructor(public device?: any, box?: [number, number, number]) {
    if (box) { const g = new THREE.BoxGeometry(...box); this.pos = Array.from(g.attributes.position.array as Float32Array); this.idx = Array.from(g.index!.array as Uint16Array); }
  }
  setPositions(a: ArrayLike<number>) { this.pos = Array.from(a); }
  setNormals(a: ArrayLike<number>) { this.nor = Array.from(a); }
  setUvs(_c: number, a: ArrayLike<number>) { this.uv = Array.from(a); }
  setIndices(a: ArrayLike<number>) { this.idx = Array.from(a); }
  getPositions(out: number[]) { out.length = 0; out.push(...this.pos); return this.pos.length / 3; }
  update() { this.updated++; }
}
class FMaterial { diffuse = new Col(); emissive = new Col(0, 0, 0); diffuseMap: any = null; emissiveMap: any = null; gloss = 0.5; metalness = 0; useMetalness = false; cull = 1; updates = 0; update() { this.updates++; } }
class FMI {
  node: any = null;
  constructor(public mesh: any, public material: any) {}
  get aabb() {
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    const p = this.mesh.pos;
    for (let i = 0; i < p.length; i += 3) {
      const w = this.node.toWorld([p[i], p[i + 1], p[i + 2]]);
      for (let a = 0; a < 3; a++) { lo[a] = Math.min(lo[a], w[a]); hi[a] = Math.max(hi[a], w[a]); }
    }
    return { getMin: () => new V(lo[0], lo[1], lo[2]), getMax: () => new V(hi[0], hi[1], hi[2]) };
  }
}
class FEntity {
  name: string; children: FEntity[] = []; parent: FEntity | null = null; enabled = true; _app: any;
  _p = new V(); _r = new Q(); _s = new V(1, 1, 1); destroyed = false;
  [k: string]: any;
  constructor(name = "Untitled", app: any = CURRENT) { this.name = name; this._app = app; }
  addChild(c: FEntity) { c.parent?.removeChild(c); this.children.push(c); c.parent = this; }
  removeChild(c: FEntity) { this.children = this.children.filter((x) => x !== c); c.parent = null; }
  destroy() { this.parent?.removeChild(this); this.destroyed = true; }
  setLocalPosition(x: number, y: number, z: number) { this._p = new V(x, y, z); }
  setLocalRotation(x: number, y: number, z: number, w: number) { this._r = new Q(x, y, z, w); }
  setLocalScale(x: number, y: number, z: number) { this._s = new V(x, y, z); }
  getLocalPosition() { return this._p; }
  getLocalRotation() { return this._r; }
  getLocalScale() { return this._s; }
  getRotation() { return new Q(); }
  worldScale(): V { const p = this.parent ? this.parent.worldScale() : new V(1, 1, 1); return new V(p.x * this._s.x, p.y * this._s.y, p.z * this._s.z); }
  getWorldTransform() { const s = this.worldScale(); return { getScale: () => s }; }
  toWorld(v: number[]): number[] {           // no rotation in these tests: scale, then move, then the parent
    const l = [v[0] * this._s.x + this._p.x, v[1] * this._s.y + this._p.y, v[2] * this._s.z + this._p.z];
    return this.parent ? this.parent.toWorld(l) : l;
  }
  addComponent(type: string, data: any = {}) {
    if (type === "render") {
      const mis = data.meshInstances || (data.type ? [new FMI(new FMesh(null, [1, 1, 1]), this._app.defaultMaterial)] : []);
      for (const mi of mis) mi.node = this;
      this.render = { meshInstances: mis, type: data.type || "asset", asset: data.asset };
    } else this[type] = { ...data };
    return this[type];
  }
  removeComponent(type: string) { delete this[type]; }
  clone(): FEntity {
    const e = new FEntity(this.name, this._app);
    e.enabled = this.enabled; e._p = { ...this._p } as V; e._r = { ...this._r } as Q; e._s = { ...this._s } as V;
    for (const k of ["script", "rigidbody", "collision", "camera", "light", "sound"]) if (this[k]) e[k] = { ...this[k] };
    if (this.render) {
      const mis = this.render.meshInstances.map((mi: FMI) => { const n = new FMI(mi.mesh, mi.material); n.node = e; return n; });
      e.render = { ...this.render, meshInstances: mis };
    }
    for (const c of this.children) e.addChild(c.clone());
    return e;
  }
}
function pcApp(opts: { withMesh?: boolean } = {}) {
  const assets: any[] = [];
  const loads: any[] = [];
  const app: any = {
    graphicsDevice: { id: "device" },
    defaultMaterial: new FMaterial(),
    scene: { ambientLight: new Col(0.2, 0.2, 0.2), fog: "none", fogColor: new Col(), fogStart: 1, fogEnd: 100, fogDensity: 0 },
    assets: {
      list: assets, loads,
      get: (id: number) => assets.find((a) => a.id === id) || null,
      find: (name: string, type: string) => assets.find((a) => a.name === name && a.type === type) || null,
      loadFromUrlAndFilename(url: string, file: string, type: string, cb: any) {
        loads.push([url, file, type]);
        const a = { id: 900 + loads.length, name: file, type, file: { url }, resource: {
          instantiateRenderEntity: () => {
            const root = new FEntity("Scene", app);
            const body = new FEntity("Body", app);
            body.addComponent("render", { meshInstances: [new FMI(new FMesh(null, [1, 2, 1]), new FMaterial())] });
            root.addChild(body);
            return root;
          },
        } };
        setTimeout(() => cb(null, a), 1);
      },
    },
  };
  CURRENT = app;
  app.root = new FEntity("Root", app);
  if (opts.withMesh !== false) {
    const ground = new FEntity("ground", app);
    ground.addComponent("render", { meshInstances: [new FMI(new FMesh(null, [20, 0.2, 20]), new FMaterial())] });
    app.root.addChild(ground);
  }
  return app;
}

section("PlayCanvas: keys, parts, and radians");
{
  const app = pcApp();
  const a = new FEntity("sign", app); a.addComponent("render", { meshInstances: [new FMI(new FMesh(null, [1, 1, 0.1]), new FMaterial())] });
  const b = new FEntity("sign", app); b.addComponent("render", { meshInstances: [new FMI(new FMesh(null, [1, 1, 0.1]), new FMaterial())] });
  const lamp = new FEntity("lamp", app); lamp.addComponent("light", { type: "omni", color: new Col(1, 1, 1), intensity: 1 });
  app.root.addChild(a); app.root.addChild(b); app.root.addChild(lamp);
  eq("an app is PlayCanvas, keyed from its root", [engineOf(app), rootOf(app) === app.root, engineOf(a), rootOf(a) === a], ["playcanvas", true, "playcanvas", true]);
  eq("keys are three's rule on entities", [...studioKeys(app).values()], ["ground", "sign.000", "sign.001", "lamp"]);
  ok("studioKeys of the app is stableKeys of its root", JSON.stringify([...studioKeys(app).values()]) === JSON.stringify([...stableKeys(app.root).values()]));
  const r = await applyStudioEdits(app, {
    parts: { "sign.001": { pos: [1, 2, 3], rot: [0.1, 0.2, 0.3], scale: [2, 2, 2], color: "#ff0000" }, lamp: { hidden: true, intensity: 4 } },
  }, { retryMs: 0 });
  eq("parts applied through the editor's own pcApply", [r.parts, r.missing, r.errors], [2, [], []]);
  eq("local position", [b._p.x, b._p.y, b._p.z], [1, 2, 3]);
  const q = quatFromEulerXYZ(0.1, 0.2, 0.3);
  ok("the rotation is the XYZ radians as a quaternion", Math.abs(b._r.x - q[0]) < 1e-12 && Math.abs(b._r.y - q[1]) < 1e-12 && Math.abs(b._r.z - q[2]) < 1e-12 && Math.abs(b._r.w - q[3]) < 1e-12);
  const back = new THREE.Euler().setFromQuaternion(new THREE.Quaternion(b._r.x, b._r.y, b._r.z, b._r.w), "XYZ");
  ok("and read back through three it is the same radians", Math.abs(back.x - 0.1) < 1e-9 && Math.abs(back.y - 0.2) < 1e-9 && Math.abs(back.z - 0.3) < 1e-9);
  eq("colour on the material, the other sign untouched", [b.render.meshInstances[0].material.diffuse.g, a.render.meshInstances[0].material.diffuse.g], [0, 1]);
  ok("hidden is disabled; a light's intensity", lamp.enabled === false && lamp.light.intensity === 4);
}

section("PlayCanvas: placements");
{
  const app = pcApp();
  const item = (id: string, ref: any, pos: number[] = [0, 0, 0], extra: any = {}) => ({ id, name: extra.name || id, ref, pos, rot: extra.rot || [0, 0, 0], scale: extra.scale || [1, 1, 1] });
  const box = await placeStudioItem(app, item("q-box", { kind: "primitive", shape: "box", color: "#ff8800" }, [1, 0.5, 2], { name: "crate-1", rot: [0, Math.PI / 2, 0] }));
  const e = box.object;
  ok("a primitive is an entity of the app's own class", box.ok && e instanceof FEntity && e._app === app, box.error);
  eq("named, marked, under the root, keyed", [e.name, e.__studioPlaced, e.parent === app.root, box.key], ["crate-1", "q-box", true, "crate-1"]);
  const mi = e.render.meshInstances[0];
  ok("built from the same arrays as three's box, with the game's Mesh and material classes", mi instanceof FMI && mi.mesh instanceof FMesh && mi.material instanceof FMaterial
    && JSON.stringify(mi.mesh.pos) === JSON.stringify(Array.from(shapeArrays("box").pos)) && mi.mesh.updated === 1);
  eq("its colour as sRGB, gloss from the editor's roughness", [mi.material.diffuse.r, mi.material.diffuse.g.toFixed(4), mi.material.gloss, mi.material.metalness, mi.material.updates], [1, (0x88 / 255).toFixed(4), 0.3, 0.05, 1]);
  const q = quatFromEulerXYZ(0, Math.PI / 2, 0);
  eq("its transform: position, and the rotation as the same quaternion", [[e._p.x, e._p.y, e._p.z], [e._r.y.toFixed(9), e._r.w.toFixed(9)]], [[1, 0.5, 2], [q[1].toFixed(9), q[3].toFixed(9)]]);
  await placeStudioItem(app, item("q-box", { kind: "primitive", shape: "cone" }, [0, 0, 0], { name: "crate-1" }));
  let n = 0; for (const c of app.root.children) if (c.__studioPlaced === "q-box") n++;
  ok("the same id twice leaves one entity, and the old one is destroyed", n === 1 && e.destroyed);

  const empty = pcApp({ withMesh: false });
  const first = await placeStudioItem(empty, item("q-first", { kind: "primitive", shape: "sphere" }));
  ok("an app that has drawn nothing yet still gets its classes (from a scratch box)", first.ok && first.object.render.meshInstances[0].material instanceof FMaterial, first.error);
  ok("and the scratch box is gone again", empty.root.children.length === 1);
  const light = await placeStudioItem(app, item("q-light", { kind: "primitive", shape: "spotLight", color: "#00ff00" }));
  ok("a light primitive is a light component of the right type", light.ok && light.object.light.type === "spot" && light.object.light.color.g === 1);

  // A clone is a copy of what it looks like: no scripts, no body, no second camera.
  const npc = new FEntity("npc", app);
  npc.addComponent("render", { meshInstances: [new FMI(new FMesh(null, [1, 1, 1]), new FMaterial())] });
  npc.addComponent("script", { scripts: ["wander"] }); npc.addComponent("rigidbody", { type: "dynamic" }); npc.addComponent("collision", {});
  const eye = new FEntity("eye", app); eye.addComponent("camera", { fov: 50 }); npc.addChild(eye);
  app.root.addChild(npc);
  const copy = await placeStudioItem(app, item("q-copy", { kind: "clone", of: "npc" }, [4, 0, 4], { name: "npc-2" }));
  ok("a clone is placed", copy.ok && copy.object !== npc, copy.error);
  ok("with its render, and without its scripts, body, collision or camera", !!copy.object.render && !copy.object.script && !copy.object.rigidbody && !copy.object.collision && !copy.object.children[0].camera);
  ok("and the original keeps all of them", !!npc.script && !!npc.rigidbody && !!npc.children[0].camera);

  // A model file: through the registry, by URL AND file name, since a Studio URL has no extension.
  const model = await placeStudioItem(app, item("q-model", { kind: "model", url: "public/models/rock.glb" }, [2, 0, 0], { name: "rock-1" }),
    { url: (p, kind) => (kind === "model" ? "http://studio/api/engine/model?file=" + p : "") });
  ok("a model file is loaded through the app's own registry", model.ok && JSON.stringify(app.assets.loads[0]) === JSON.stringify(["http://studio/api/engine/model?file=public/models/rock.glb", "rock.glb", "container"]), model.error);
  eq("instantiated, and says it is the file", [model.object.name, model.how], ["rock-1", "the file public/models/rock.glb"]);

  // The game's own copy of a file: rot-rush's brainrots are unpainted and in centimetres, so the
  // one the game drew (painted, fitted to 1.55 m) is what gets copied.
  app.assets.list.push({ id: 11, name: "brainrot.glb/render/0", type: "render" });
  app.assets.list.push({ id: 12, name: "brainrot.glb", type: "container", file: { url: "/assets/brainrots/brainrot.glb?v=3" },
    resource: { data: { nodes: [{ name: "Body" }, { name: "Armature" }], scenes: [{ name: "Scene" }] } } });
  const holder = new FEntity("brainrot-7", app); holder.setLocalPosition(10, 2, 0); holder.setLocalScale(0.01, 0.01, 0.01);
  const sceneNode = new FEntity("Scene", app);
  const body = new FEntity("Body", app);
  body.addComponent("render", { asset: 11, meshInstances: [new FMI(new FMesh(null, [100, 155, 100]), new FMaterial())] });
  sceneNode.addChild(body); holder.addChild(sceneNode); app.root.addChild(holder);
  const got = await placeStudioItem(app, item("q-rot", { kind: "model", url: "assets/brainrots/brainrot.glb" }, [0, 0, 5], { name: "brainrot-copy" }));
  ok("a file the game has drawn is placed as a copy of the game's own", got.ok && /copy of the one the game draws/.test(got.how || ""), got.error || got.how);
  eq("nothing was loaded for it", app.assets.loads.length, 1);
  const inner = got.object.children[0];
  eq("the copy carries the game's size (its holder's 0.01)", [inner._s.x, inner._s.y], [0.01, 0.01]);
  const bb = inner.render ? null : inner.children[0].render.meshInstances[0].aabb;
  ok("and stands with its feet on the placement's origin, 1.55 m tall", !!bb && Math.abs(bb.getMin().y - 0) < 1e-9 && Math.abs(bb.getMax().y - 1.55) < 1e-9
    && Math.abs(bb.getMin().x + 0.5) < 1e-9 && Math.abs(bb.getMin().z - 4.5) < 1e-9, bb ? JSON.stringify([bb.getMin(), bb.getMax()]) : "no box");

  // Code: a PlayCanvas builder that wants the app gets it.
  const code = await placeStudioItem(app, item("q-code", { kind: "code", file: "src/assets.mjs", export: "buildPc" }), { base });
  ok("a PlayCanvas builder that takes the app is given it", code.ok && code.object instanceof FEntity && code.object.name === "q-code", code.error);

  // The whole document, and a late spawn.
  const s = settled();
  const rep = await applyStudioEdits(app, {
    placed: [
      { id: "d1", name: "box-d1", ref: { kind: "primitive", shape: "box" }, pos: [0, 0, 0] },
      { id: "d2", name: "spawn-copy", ref: { kind: "clone", of: "spawned-later" }, pos: [1, 1, 1] },
    ],
  }, { retryMs: 3000, ...s.opts });
  eq("the primitive now, the clone pending", [rep.placed, rep.pending], [1, 1]);
  ok("and every placement the file does not list is gone", !app.root.children.some((c: any) => ["q-copy", "q-model", "q-rot", "q-code", "q-box"].includes(c.__studioPlaced)));
  const sp = new FEntity("spawned-later", app); sp.addComponent("render", { meshInstances: [new FMI(new FMesh(null, [1, 1, 1]), new FMaterial())] });
  app.root.addChild(sp);
  await within(s.done, 3000);
  ok("the late clone is made when its source spawns", rep.placed === 2 && rep.pending === 0 && app.root.children.some((c: any) => c.__studioPlaced === "d2"));
  await applyStudioEdits(app, { placed: [{ id: "d1", name: "box-d1", ref: { kind: "primitive", shape: "box" }, pos: [0, 0, 0] }, { id: "d2", name: "spawn-copy", ref: { kind: "clone", of: "spawned-later" }, pos: [1, 1, 1] }] }, { retryMs: 0 });
  eq("applied again: one of each", app.root.children.filter((c: any) => c.__studioPlaced).map((c: any) => c.__studioPlaced).sort(), ["d1", "d2"]);
  eq("unplace on an app", [unplaceStudioItem(app, "d1"), app.root.children.some((c: any) => c.__studioPlaced === "d1")], [1, false]);
}

console.log(pass + " checks passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL " + f);
if (fails.length) process.exitCode = 1;
