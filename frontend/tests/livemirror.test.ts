// The live mirror of a running game, tested on the faults that sent it back.
//
// Each block stands for something the user saw in a real game (rot-rush) and reported: the signs
// drawn black because their picture was on the mesh instance and not on the material; a level of
// thirty platforms mirrored as three; a merged scenery mesh that no click could take apart; a
// placed brainrot that came out grey, with every variant on top of each other, at the file's own
// size. None of these shows in a type check, and all of them rendered without an error.
//
// Run: npm run test:livemirror

import * as THREE from "three";
import { packArr, pcPlace, pcSnapshot, pcUnplace, threeToPcPlace, unpackArr } from "../src/components/engine/edit/ops";
import { hideRevealed, revealPlan } from "../src/components/engine/edit/pcmirror";
import { groupOf, movePiece, parsePieceKey, pieceAt, pieceKey, piecesOf } from "../src/components/engine/edit/pieces";
import {
  copyAsPlacement, dressForGame, folderPaint, gameCopiesOf, keepNodes, modelFolder, modelStem, nodeName,
  pickCopy, sameModel, shapeOf, unpainted,
} from "../src/components/engine/edit/gamedress";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
};
const near = (name: string, got: number, want: number, tol = 1e-4) =>
  ok(name, Math.abs(got - want) <= tol, "got " + got + ", want " + want);
const eq = (name: string, got: unknown, want: unknown) =>
  ok(name, JSON.stringify(got) === JSON.stringify(want), "got " + JSON.stringify(got) + ", want " + JSON.stringify(want));
const section = (s: string) => console.log("- " + s);

// ------------------------------------------------------------------ fixtures

/** A box as positions and indices, centred on `c`. */
function boxArrays(c: [number, number, number], s = 1): { pos: number[]; idx: number[] } {
  const g = new THREE.BoxGeometry(s, s, s);
  g.translate(c[0], c[1], c[2]);
  return { pos: Array.from(g.attributes.position.array as Float32Array), idx: Array.from(g.index!.array as Uint16Array) };
}

/** Several boxes appended one after another into one mesh, the way a game merges its scenery. */
function merged(centres: [number, number, number][]): { pos: number[]; idx: number[] } {
  const pos: number[] = [], idx: number[] = [];
  for (const c of centres) {
    const b = boxArrays(c);
    const base = pos.length / 3;
    pos.push(...b.pos);
    for (const i of b.idx) idx.push(i + base);
  }
  return { pos, idx };
}

const mesh = (name: string, mat?: any, size = 1) => {
  const m = new THREE.Mesh(new THREE.BoxGeometry(size, size, size), mat || new THREE.MeshStandardMaterial());
  m.name = name;
  return m;
};

// ------------------------------------------------------------------ packing numbers for the wire
section("packing numbers for the wire");
{
  const f = [0.5, -1.25, 3.75, 1e-3];
  const back = Array.from(unpackArr(packArr(f, "f32")) as Float32Array);
  ok("floats come back exact", back.every((v, i) => Math.abs(v - f[i]) < 1e-6), JSON.stringify(back));
  const idx = [0, 1, 2, 65535];
  const bi = unpackArr(packArr(idx, "u16"), true)!;
  ok("an index stays an index", bi instanceof Uint16Array && Array.from(bi).join() === idx.join());
  const nor = [0, 1, -1, 0.5];
  const bn = Array.from(unpackArr(packArr(nor, "i8n")) as Float32Array);
  ok("a normal survives a byte, near enough", bn.every((v, i) => Math.abs(v - nor[i]) < 0.01), JSON.stringify(bn));
  const col = [0, 0.5, 1];
  const bc = Array.from(unpackArr(packArr(col, "u8n")) as Float32Array);
  ok("a colour survives a byte, near enough", bc.every((v, i) => Math.abs(v - col[i]) < 0.005), JSON.stringify(bc));
  ok("a plain array still reads", (unpackArr([1, 2, 3]) as Float32Array).length === 3);
  ok("nothing is nothing", unpackArr(null) === null && unpackArr([]) === null);
}

// ------------------------------------------------------------------ pieces of a merged mesh
section("pieces of a merged mesh");
{
  // Eight crates in a row, 6 m apart, merged into one mesh: the base-props case in miniature.
  const centres: [number, number, number][] = [];
  for (let i = 0; i < 8; i++) centres.push([i * 6, 0.5, 0]);
  const m = merged(centres);
  const map = piecesOf(m.pos, m.idx);
  eq("one piece per crate", map.pieces.length, 8);
  ok("a row of crates is a merged mesh", map.merged);
  eq("each crate owns its 24 corners", map.pieces.map((p) => p.n), new Array(8).fill(24));
  near("the third crate sits where it was put", map.pieces[2].c[0], 12);
  eq("the triangle ranges follow the buffer", map.pieces.map((p) => [p.t0, p.t1])[1], [12, 24]);
  const p = pieceAt(map, 30)!;
  eq("triangle 30 is in the third crate", [p.t0, p.t1], [24, 36]);
  const k = pieceKey("base-props", p);
  eq("a piece key names the mesh and the range", k, "base-props~t24-36");
  eq("and reads back", parsePieceKey(k), { mesh: "base-props", t0: 24, t1: 36 });
  ok("an ordinary key is not a piece", parsePieceKey("base-props") === null);
  ok("a piece is in its group", groupOf(map, p).includes(p));

  // A single object is not merged: a click takes all of it, as before.
  const one = boxArrays([0, 0, 0]);
  ok("one box is not a merged mesh", !piecesOf(one.pos, one.idx).merged);

  // Moving a piece: from rest, so twice is once, and only its own corners.
  const rest = { pos: Float32Array.from(m.pos), idx: m.idx, nor: null };
  const live = { pos: Float32Array.from(m.pos) };
  const piece = map.pieces[2];
  const r1 = movePiece(rest, live, piece.t0, piece.t1, { pos: [12, 3, 0], piece: { c: piece.c, n: piece.n } });
  eq("it moves its 24 corners", r1.moved, 24);
  movePiece(rest, live, piece.t0, piece.t1, { pos: [12, 3, 0], piece: { c: piece.c, n: piece.n } });
  const ys: number[] = [];
  for (let v = 48; v < 72; v++) ys.push(live.pos[v * 3 + 1]);
  near("applied twice, it is lifted once", Math.min(...ys), 2.5);
  near("the crate beside it did not move", live.pos[24 * 3 + 1] - rest.pos[24 * 3 + 1], 0);
  const r2 = movePiece(rest, live, piece.t0, piece.t1, { pos: [0, 0, 0], piece: { c: piece.c, n: 99 } });
  ok("a rebuilt mesh is refused, with the reason", r2.moved === 0 && /rebuilt/.test(r2.error), r2.error);
}

// ------------------------------------------------------------------ the parts the game switched off
section("the parts the game switched off");
{
  const level = new THREE.Group();
  const plat = (name: string, x: number, on: boolean) => {
    const g = new THREE.Group();
    g.name = name;
    g.position.set(x, 0, 0);
    g.add(mesh(name + "-deck", undefined, 4), mesh(name + "-rail", undefined, 1));
    g.visible = on;
    if (!on) g.userData.gameHidden = true;
    level.add(g);
    return g;
  };
  plat("platform-1", 30, true);
  const far = plat("platform-7", 60, false);
  // A lean twin of platform-1, switched off where the full one draws.
  const twin = plat("platform-1-lod", 30, false);
  // One spark in use, and three spares of its pool parked on one spot.
  const inUse = mesh("spark-0", undefined, 0.2);
  inUse.position.set(0, 8, 0);
  level.add(inUse);
  const sparks = [1, 2, 3].map((i) => {
    const s = mesh("spark-" + i, undefined, 0.2);
    s.position.set(5, 5, 5);
    s.visible = false;
    s.userData.gameHidden = true;
    level.add(s);
    return s;
  });
  const plan = revealPlan(THREE, level);
  ok("a platform culled for distance is shown", plan.reveal.includes(far) && far.visible);
  ok("a twin of a drawn platform stays off", plan.keep.includes(twin) && !twin.visible);
  ok("a pool of spares stays off", sparks.every((s) => plan.keep.includes(s) && !s.visible));
  eq("hiding them again puts back what was shown", hideRevealed(level), 1);
  ok("and the platform is off again", !far.visible && !far.userData.revealed);
}

// ------------------------------------------------------------------ the snapshot of a PlayCanvas game
section("the snapshot of a PlayCanvas game");
{
  // Just enough of a browser for readTexture: a canvas that remembers what was put on it.
  const g: any = globalThis;
  g.ImageData = class { data: any; width: number; height: number; constructor(d: any, w: number, h: number) { this.data = d; this.width = w; this.height = h; } };
  g.document = {
    createElement: () => ({
      width: 0, height: 0,
      getContext: () => ({ putImageData() {}, drawImage() {}, translate() {}, scale() {}, imageSmoothingEnabled: true }),
      toDataURL: () => "data:image/png;base64," + "A".repeat(64),
    }),
  };

  const identity = () => ({ data: Float32Array.from([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]) });
  const translated = (x: number, y: number, z: number) => ({ data: Float32Array.from([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]) });
  const tri = { pos: [0, 0, 0, 1, 0, 0, 0, 1, 0], idx: [0, 1, 2] };
  const fakeMesh = (skin = false) => ({
    primitive: [{ type: 4 }],
    getPositions(o: number[]) { o.push(...tri.pos); return 3; },
    getNormals(o: number[]) { o.push(0, 0, 1, 0, 0, 1, 0, 0, 1); return 3; },
    getUvs(ch: number, o: number[]) { if (ch === 0) o.push(0, 0, 1, 0, 0, 1); return ch === 0 ? 3 : 0; },
    getColors() { return 0; },
    getIndices(o: number[]) { o.push(...tri.idx); return 3; },
    getVertexStream(sem: string, o: number[]) {
      if (!skin) return 0;
      if (sem === "BLENDINDICES") o.push(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
      if (sem === "BLENDWEIGHT") o.push(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0);
      return 3;
    },
    vertexBuffer: { format: { elements: [] } },
  });
  // One sign material shared by every sign; the picture is on the MESH INSTANCE.
  const signMaterial = { name: "sign", diffuse: { r: 0, g: 0, b: 0 }, useLighting: false, blendType: 3, cull: 1 };
  const picture = { width: 2, height: 2, format: 7, getSource: () => new Uint8Array(16).fill(200), magFilter: 1 };
  const sign = {
    name: "sign-boost", enabled: true, children: [],
    getLocalPosition: () => ({ x: 1, y: 2, z: 3 }), getLocalRotation: () => ({ x: 0, y: 0, z: 0, w: 1 }), getLocalScale: () => ({ x: 1, y: 1, z: 1 }),
    getWorldTransform: identity,
    render: {
      asset: null,
      meshInstances: [{
        mesh: fakeMesh(), material: signMaterial, node: null,
        getParameter: (n: string) => (n === "texture_emissiveMap" ? { data: picture } : n === "material_emissive" ? { data: [1, 1, 1] } : undefined),
      }],
    },
  };
  // A brainrot built from a file: its render asset names the container it came from, and its skin
  // is posed by one bone lifted a metre.
  const bone = { getWorldTransform: () => translated(0, 1, 0) };
  const brainrot = {
    name: "Labubu_Default", enabled: true, children: [],
    getLocalPosition: () => ({ x: 0, y: 0, z: 0 }), getLocalRotation: () => ({ x: 0, y: 0, z: 0, w: 1 }), getLocalScale: () => ({ x: 1, y: 1, z: 1 }),
    getWorldTransform: identity,
    render: {
      asset: 42,
      meshInstances: [{
        mesh: fakeMesh(true), material: { diffuse: { r: 1, g: 1, b: 1 } }, node: null,
        skinInstance: { bones: [bone], skin: { inverseBindPose: [identity()] } },
      }],
    },
  };
  const culled = { ...brainrot, name: "platform-9", enabled: false, render: { asset: null, meshInstances: [] } };
  const app: any = {
    assets: {
      get: (id: number) => (id === 42 ? { name: "labubu_littleones_and_shit.glb/render/0" } : null),
      find: (name: string, type: string) => (type === "container" && name === "labubu_littleones_and_shit.glb"
        ? { file: { url: "/src/assets/brainrots/labubu_littleones_and_shit.glb?t=17" } } : null),
    },
  };
  const root = {
    name: "Root", enabled: true, children: [sign, brainrot, culled], _app: app,
    getLocalPosition: () => ({ x: 0, y: 0, z: 0 }), getLocalRotation: () => ({ x: 0, y: 0, z: 0, w: 1 }), getLocalScale: () => ({ x: 1, y: 1, z: 1 }),
  };
  const snap = pcSnapshot(root, { app });
  const s = snap.root.children[0];
  const m = s.meshes![0].material;
  ok("the sign's picture is taken from its mesh instance", !!m.emissiveMap && snap.textures![m.emissiveMap!.tex].url.startsWith("data:image/png"));
  ok("and its glow colour, marked linear", m.emissiveLinear === true && JSON.stringify(m.emissive) === "[1,1,1]");
  ok("the sign is unlit", m.unlit === true);
  const b = snap.root.children[1];
  eq("a model knows the file it came from", b.src, "/src/assets/brainrots/labubu_littleones_and_shit.glb");
  ok("an entity not built from a file says nothing", s.src === undefined);
  const g0 = snap.geoms![b.meshes![0].geo!];
  const ys = [1, 4, 7].map((i) => (g0.positions as number[])[i]);
  ok("a skinned mesh is baked in the pose of its bones", b.meshes![0].skinned === true && ys.every((y, i) => Math.abs(y - [1, 1, 2][i]) < 1e-6), JSON.stringify(ys));
  eq("a part the game switched off is still taken, marked", [snap.root.children[2].enabled, snap.counts.hidden], [false, 1]);
  delete g.document;
  delete g.ImageData;
}

// ------------------------------------------------------------------ placing into the running game
section("placing into the running game");
{
  const holder = new THREE.Group();
  holder.position.set(4, 2, 0);
  const body = mesh("body", new THREE.MeshBasicMaterial({ color: 0xff0000 }));
  body.position.set(0, 0.5, 0);
  const hidden = mesh("variant");
  hidden.visible = false;
  holder.add(body, hidden);
  const spec = threeToPcPlace(THREE, holder, { id: "p1", name: "crate" }, true);
  eq("only what is shown is sent", spec.meshes.length, 1);
  eq("where it stands comes with it", spec.pos, [4, 2, 0]);
  ok("an unlit material stays unlit", spec.meshes[0].unlit === true);
  near("its colour is sent as sRGB", spec.meshes[0].color[0], 1, 1e-3);

  // The game's own classes, learned from an object it already has.
  class Ent {
    name: string; children: any[] = []; parent: any = null; render: any = null; __studioPlaced?: string;
    p = [0, 0, 0]; s = [1, 1, 1];
    constructor(n = "") { this.name = n; }
    setLocalPosition(x: number, y: number, z: number) { this.p = [x, y, z]; }
    setLocalRotation() {}
    setLocalScale(x: number, y: number, z: number) { this.s = [x, y, z]; }
    addComponent(_t: string, d: any) { this.render = d; }
    addChild(c: any) { c.parent = this; this.children.push(c); }
    destroy() { if (this.parent) this.parent.children = this.parent.children.filter((x: any) => x !== this); }
  }
  class Mesh { v = 0; constructor(_d: any) {} setPositions(p: Float32Array) { this.v = p.length / 3; } setNormals() {} setUvs() {} setColors() {} setIndices() {} update() {} }
  class MeshInstance { mesh: any; material: any; constructor(m: any, mat: any) { this.mesh = m; this.material = mat; } }
  class Material { diffuse = { set() {} }; diffuseMap: any = null; emissiveMap: any = null; update() {} }
  const game = new Ent("Root");
  const already = new Ent("tree");
  already.render = { meshInstances: [new MeshInstance(new Mesh(null), new Material())] };
  game.addChild(already);
  const app = { root: game, graphicsDevice: {} };
  pcPlace(app, spec).then((r) => {
    const placed = game.children.find((c: any) => c.__studioPlaced === "p1");
    ok("the game builds it with its own classes", r.ok && !!placed && placed.children.length === 1, JSON.stringify(r));
    return pcPlace(app, spec).then(() => {
      eq("sending it again replaces it, never doubles it", game.children.filter((c: any) => c.__studioPlaced === "p1").length, 1);
      eq("and it can be taken out again", pcUnplace(app, "p1"), 1);
      ok("leaving the game's own objects", game.children.length === 1 && game.children[0] === already);
      finish();
    });
  }).catch((e) => { fails.push("pcPlace threw: " + e); finish(); });
}

// ------------------------------------------------------------------ the game's own version of a file
section("the game's own version of a model file");
{
  eq("a file's name, whatever the path and query", modelStem("rot-rush/src/assets/brainrots/Labubu_Littleones.glb?t=3"), "labubu_littleones");
  eq("its folder", modelFolder("/src/assets/brainrots/labubu.glb"), "brainrots");
  ok("a bundler's hash is the same file", sameModel("/assets/labubu-Dk3a9fQ1.glb", "rot-rush/src/assets/brainrots/labubu.glb"));
  ok("a longer name is another file", !sameModel("/assets/labubu_golden.glb", "labubu.glb"));
  eq("a node name as three keeps it", nodeName("FrigoCamelo_Common.001"), "FrigoCamelo_Common001");

  // The FILE, as three's loader gives it: one root node over an armature and two variants.
  const file = () => {
    const scene = new THREE.Group();
    const rootNode = new THREE.Object3D(); rootNode.name = "RootNode";
    const arm = new THREE.Object3D(); arm.name = "Armature";
    const bone = new THREE.Object3D(); bone.name = "Root";
    arm.add(bone);
    const a = mesh("Labubu_Default", new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 1, roughness: 1 }));
    const b = mesh("Labubu_Golden", new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 1, roughness: 1 }));
    b.position.x = 5;
    rootNode.add(arm, a, b);
    scene.add(rootNode);
    return scene;
  };
  const shape = shapeOf(file());
  eq("a node knows how far under the file's top it sits", shape.depth.get("Labubu_Default"), 1);

  // The MIRROR of the game: a pickup holder the game happens to call "Root" — a bone's name —
  // holding the copy it built, with the golden variant switched off and the palette painted on.
  const palette = new THREE.MeshStandardMaterial({ map: new THREE.Texture() });
  const mirrorCopy = (x: number, culledByDistance = false) => {
    const pickup = new THREE.Group(); pickup.name = "Root";
    pickup.position.set(x, 3, 0);
    pickup.scale.setScalar(0.5);
    const top = new THREE.Group(); top.name = "RootNode";
    const a = mesh("Labubu_Default", palette); a.userData.pcSrc = "/src/assets/brainrots/labubu_littleones_and_shit.glb";
    const b = mesh("Labubu_Golden", palette); b.userData.pcSrc = a.userData.pcSrc; b.visible = false; b.userData.gameHidden = true;
    b.position.x = 5;
    top.add(a, b);
    pickup.add(top);
    if (culledByDistance) pickup.userData.revealed = true;
    return { pickup, top };
  };
  const level = new THREE.Group();
  const near1 = mirrorCopy(10, true), near2 = mirrorCopy(20, false);
  level.add(near1.pickup, near2.pickup);
  const copies = gameCopiesOf(level, "rot-rush/src/assets/brainrots/labubu_littleones_and_shit.glb", shape);
  ok("a copy's top is the file's root, not the game's holder of the same name", copies.length === 2 && copies.every((c) => c.name === "RootNode"),
     copies.map((c) => c.name).join());
  eq("one the game draws now beats one it culled", pickCopy(copies, ["Labubu_Default"]), near2.top);
  eq("a table that names another character takes none of these", pickCopy(copies, ["FrigoToToTo"]), null);

  const placed = copyAsPlacement(THREE, near2.top);
  const names: string[] = [];
  placed.traverse((o: any) => names.push(o.name));
  ok("the variant the game switched off is left out", names.includes("Labubu_Default") && !names.includes("Labubu_Golden"), names.join());
  placed.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(placed);
  near("it stands on its origin", box.min.y, 0, 1e-6);
  near("centred", (box.min.x + box.max.x) / 2, 0, 1e-6);
  near("at the size the game drew it", box.max.y - box.min.y, 0.5, 1e-6);
  ok("it keeps the game's paint", (placed.children[0].children[0] as any).material === palette);

  // The raw file, no copy in the game: only the node the table names is shown.
  const raw = file();
  eq("a table's first node is the one shown", keepNodes(raw, ["Labubu_Golden", "Labubu_Default"]), 1);
  ok("the other variant is hidden", raw.getObjectByName("Labubu_Default")!.visible === false);
  const raw2 = file();
  eq("names the file does not have change nothing", keepNodes(raw2, ["Nope"]), -1);
  ok("so every mesh stays shown", raw2.getObjectByName("Labubu_Golden")!.visible === true);

  ok("three's default for 'no material' is recognised", unpainted(new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 1, roughness: 1 })));
  ok("a named material is the file's own", !unpainted(Object.assign(new THREE.MeshStandardMaterial({ metalness: 1, roughness: 1 }), { name: "skin" })));

  // The folder's paint: two OTHER files of brainrots/ wear one palette sheet — each on a material
  // of its own, the way the mirror makes them, and one of them tinted for its rarity.
  const shop = new THREE.Group();
  for (const [i, f] of ["a", "b"].entries()) {
    const own = new THREE.MeshStandardMaterial({ map: palette.map, color: i ? 0xffaa00 : 0xffffff });
    const m = mesh("body-" + f, own, 1.5);
    m.userData.pcSrc = "/src/assets/brainrots/" + f + ".glb";
    m.position.x = i * 4;
    shop.add(m);
  }
  const paint = folderPaint(THREE, shop, "rot-rush/src/assets/brainrots/c.glb");
  ok("the sheet two files share is the folder's paint", !!paint && paint.material.map === palette.map && paint.files === 2);
  ok("taken from the untinted wearer", !!paint && paint.material.color.getHex() === 0xffffff);
  near("with their height", paint ? paint.height : 0, 1.5, 1e-6);
  const lone = new THREE.Group();
  const only = mesh("body-a", palette); only.userData.pcSrc = "/src/assets/brainrots/a.glb"; lone.add(only);
  ok("one file's paint is not a folder's", folderPaint(THREE, lone, "brainrots/c.glb") === null);

  const d1 = dressForGame(THREE, level, "rot-rush/src/assets/brainrots/labubu_littleones_and_shit.glb", file(), ["Labubu_Default"]);
  eq("a file the game drew is placed as the game's copy", d1.how, "copy");
  const d2 = dressForGame(THREE, shop, "rot-rush/src/assets/brainrots/c.glb", file(), ["Labubu_Default"]);
  eq("a file it has not drawn is painted like its folder", d2.how, "painted");
  let wore = 0;
  d2.object.traverse((o: any) => { if (o.isMesh && o.visible && o.material?.map === palette.map) wore++; });
  eq("the shown variant wears the palette", wore, 1);
  d2.object.updateMatrixWorld(true);
  const b2 = new THREE.Box3();
  d2.object.traverse((o: any) => { if (o.isMesh && o.visible) b2.expandByObject(o); });
  near("and is sized like the rest of the folder", b2.max.y - b2.min.y, 1.5, 1e-6);
  const d3 = dressForGame(THREE, null, "x/c.glb", file(), []);
  ok("outside a live game a file is itself, and says nothing", d3.how === "file" && d3.note === "");
}

let finished = false;
function finish() {
  if (finished) return;
  finished = true;
  console.log("\n" + pass + " passed, " + fails.length + " failed");
  if (fails.length) {
    for (const f of fails) console.log("  FAIL " + f);
    process.exit(1);
  }
}
