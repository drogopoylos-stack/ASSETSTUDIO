/* Opening a game's own assets — the convention, against a real game and against a fake one.
 *
 * The fake game is the part that has to keep working on any machine: it reproduces, in thirty
 * lines, the three things that actually broke here — a table that is never exported, a builder
 * that is a method on the entry, and a return value that is box descriptors rather than meshes.
 *
 * The real game is run too when it happens to be on this machine, because a convention proved
 * only against a fixture written by the same person is not proved.
 */
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const FRONTEND = path.resolve(HERE, "..");
const TMP = path.resolve(FRONTEND, "..", "data", "tmp");
mkdirSync(TMP, { recursive: true });
const ESBUILD = path.join(FRONTEND, "node_modules", ".bin", "esbuild.cmd");
const ESBUILD_NIX = path.join(FRONTEND, "node_modules", ".bin", "esbuild");
const exe = existsSync(ESBUILD) ? ESBUILD : ESBUILD_NIX;

function bundle(src, out, alias) {
  const args = [`"${src}"`, "--bundle", "--format=esm", "--platform=neutral",
    `"--outfile=${out}"`, "--log-level=error"];
  if (alias) args.push(`"--alias:playcanvas=${alias}"`);
  execFileSync(exe, args, { shell: true, stdio: "inherit" });
  return import(pathToFileURL(out).href + "?t=" + Date.now());
}

let pass = 0, fail = 0;
const check = (name, ok, note = "") => {
  if (ok) pass++; else fail++;
  console.log((ok ? "  PASS  " : "  FAIL  ") + name + (note ? "  \u2014 " + note : ""));
};

// --------------------------------------------------------------- the smallest three that holds a shape
const v3 = () => ({ x: 0, y: 0, z: 0, set(a, b, c) { this.x = a; this.y = b; this.z = c; return this; } });
class Obj {
  constructor() {
    this.isObject3D = true; this.children = []; this.userData = {}; this.name = "";
    this.position = v3(); this.rotation = v3(); this.scale = v3();
  }
  add(o) { this.children.push(o); return this; }
}
class Group extends Obj {}
class Mesh extends Obj { constructor(g, m) { super(); this.isMesh = true; this.geometry = g; this.material = m; } }
const geoOf = (kind) => function (...a) { return { isBufferGeometry: true, kind, args: a }; };
const THREE = {
  Group, Mesh,
  BoxGeometry: geoOf("box"), SphereGeometry: geoOf("sphere"),
  CylinderGeometry: geoOf("cyl"), ConeGeometry: geoOf("cone"),
  MeshStandardMaterial: function (o) { return { ...o, isMaterial: true }; },
};

const A = await bundle(path.join(FRONTEND, "src/components/engine/edit/assetOpen.ts"),
  path.join(TMP, "assetopen.mjs"));

// =========================================================================== the fake game
console.log("\nA library shaped like the ones that broke");
const NATURE = [
  { id: "fake.tree", name: "Tree", size: [1, 2, 1],
    build(p) {
      return [
        { x: 0, y: 0.4, z: 0, hx: 0.12, hy: 0.4, hz: 0.12, color: p.s.bark, rot: 0 },
        { x: 0, y: 1.1, z: 0, hx: 0.5, hy: 0.4, hz: 0.5, color: p.s.leaf, rot: 0 },
      ];
    } },
  { id: "fake.rock", name: "Rock", size: [1, 1, 1],
    build(p) { return [{ x: 0, y: 0.3, z: 0, hx: 0.4, hy: 0.3, hz: 0.4, color: p.s.stone, rot: 0.4 }]; } },
];
const FX = [{ id: "fake.spark", name: "Spark", size: [1, 1, 1],
  build(p) { return [{ x: 0, y: 1, z: 0, hx: 0.1, hy: 0.1, hz: 0.1, color: p.s.glow, emissive: 1 }]; } }];
// The exported surface: one flat list. The two tables above are private, exactly as in the game.
const fake = {
  PROPS: [...NATURE, ...FX],
  findProp: (id) => fake.PROPS.find((d) => d.id === id),
  searchProps: () => [],
};
const fakePalette = { SCRIPT: { forest: { bark: 0x7a5230, leaf: 0x4fbf6a, stone: 0x9aa3ad, glow: 0xffe08a, label: "forest" } } };

check("the table the index recorded is not exported at runtime", fake.NATURE === undefined);
const ref = { type: "spec", name: "Tree", file: "game/src/props.ts", root: "game",
  table: "NATURE", key: "fake.tree", index: 0, deps: ["game/src/palette.ts"] };
const spec = A.findSpec(fake, ref);
check("...and the entry is found anyway, by its own id", spec.spec?.id === "fake.tree", spec.how);
check("its own build() is the first builder tried", A.findBuilders(fake, ref, spec.spec)[0]?.name === ".build");

const built = A.buildFrom(fake, ref, { THREE }, [fakePalette]);
check("it draws", built.parts === 2, built.how);
check("...as real meshes under one group", built.object.children.length === 2 && built.object.children[0].isMesh);
const colours = built.object.children.map((c) => c.material.color);
check("...in the colours the game's own script gives", colours.includes(0x7a5230) && colours.includes(0x4fbf6a),
  colours.map((c) => "#" + c.toString(16)).join(" "));
const box = built.object.children[0].geometry;
check("...at the size the descriptor asked for, half-extents doubled", box.kind === "box" && box.args[0] === 0.24,
  JSON.stringify(box.args));
check("...and in the place it asked for", built.object.children[0].position.y === 0.4);

const noPalette = A.buildFrom(fake, ref, { THREE });
check("with no palette to read it still draws, in stand-in colours",
  noPalette.parts === 2 && !noPalette.object.children.map((c) => c.material.color).includes(0x7a5230));

const emissive = A.buildFrom(fake, { ...ref, key: "fake.spark" }, { THREE }, [fakePalette]);
check("an emissive descriptor comes back emissive", emissive.object.children[0].material.emissive === 0xffe08a);

console.log("\nWhen it cannot be done, it says which part failed");
const empty = A.buildFrom({ PROPS: [] }, { ...ref, key: "nope" }, { THREE });
check("a missing entry is named", empty.tried.some((t) => t.includes("no entry called nope")), empty.tried[0] || "");
const nothing = A.buildFrom({ notes: "hello" }, { type: "code", export: "x" }, { THREE });
check("a module with nothing to call is named", nothing.tried.some((t) => /is a function/.test(t)), nothing.tried[0] || "");

console.log("\nWhich server a file is asked for");
const cands = A.importCandidates("rot-rush/src/render/proplib.ts", ["http://127.0.0.1:5179"], "rot-rush");
check("the owning game's server is asked first, with its own folder taken off",
  cands[0] === "http://127.0.0.1:5179/src/render/proplib.ts", cands[0]);
check("...and the whole path is still tried, for a server rooted at the workspace",
  cands.includes("http://127.0.0.1:5179/rot-rush/src/render/proplib.ts"));
// WHICH ENGINE THE EDIT TAB BUILDS IN. Wrong twice the same way: a .glb of a PlayCanvas game
// opened to an empty grid (fixed for models only), then every spec and code row of those games did
// too, sent to a PlayCanvas studio with no `buildAsset`. Reproduced on the running Studio as
// "Candy tree": 0 triangles, one mirrored empty root, no error on screen.
check("every Library entry opens in three, whatever the game ships with",
  A.editEngineFor({ type: "spec" }, true) === "three"
  && A.editEngineFor({ type: "code" }, true) === "three"
  && A.editEngineFor({ type: "model" }, true) === "three");
check("...and a plain source file of a PlayCanvas game still gets the PlayCanvas studio",
  A.editEngineFor(null, true) === "playcanvas" && A.editEngineFor(undefined, false) === "three");

// A CLASS IS NEVER GUESSED, AND A MISSING EXPORT IS SAID FIRST. Opening rot-haul's `boxMesh` - a
// private helper, never exported - showed "Actors() threw: Class constructor Actors cannot be
// invoked without 'new'", naming a class the asset has nothing to do with.
{
  class Actors { constructor() { this.x = 1; } }
  function helper() { return null; }
  const mod = { Actors, helper };
  check("a class is never guessed as a builder", A.isClass(Actors) && !A.isClass(helper)
    && !A.findBuilders(mod, { type: "code", export: "boxMesh", file: "a.ts" }, null).some((b) => b.name === "Actors"));
  const r = A.buildFrom(mod, { type: "code", export: "boxMesh", file: "actors.ts" },
    { THREE: { Group: function () {}, Mesh: function () {}, BoxGeometry: function () {},
               MeshStandardMaterial: function () {}, Color: function () {} } });
  check("...and an export the module does not have is the first reason given",
    (r.tried[0] || "").indexOf("boxMesh is not exported by actors.ts") === 0, r.tried[0]);
}

check("two servers give two ladders",
  A.importCandidates("a/b.ts", ["http://x", "http://y"], "a").length === 4);

// A FILE NO DEV SERVER OWNS. Measured on the real workspace: `character-ab/studio/builder.js` is
// a sibling of the games, not inside one, so both dev servers answered `200 text/html` for it —
// their SPA fallback — and Vite's own `/@fs/` answered 403, which is Vite being correct. Ten
// assets could never draw. The Studio serves it, LAST, so a file a game really owns still comes
// from that game with that game's transpiling.
const out = A.importCandidates("character-ab/studio/builder.js", ["http://127.0.0.1:5179"], "",
  "c:/w/character-ab/studio/builder.js", "http://127.0.0.1:8777");
check("the Studio is the last candidate, never the first",
  out[out.length - 1].startsWith("http://127.0.0.1:8777/api/engine/source?path="), out[out.length - 1]);
check("...and the game's own server is still asked before it",
  out[0].startsWith("http://127.0.0.1:5179"), out[0]);
check("...it is not offered for TypeScript, which it cannot transpile",
  !A.importCandidates("a/b.ts", [""], "", "c:/w/a/b.ts", "http://s")
    .some((u) => u.includes("/api/engine/source")));
check("...nor when the index never knew the absolute path",
  !A.importCandidates("a/b.js", [""], "", "", "http://s")
    .some((u) => u.includes("/api/engine/source")));

// A GAME AT THE PROJECT ROOT — every game the new-game scaffold makes. With no folder to strip,
// `head` was 0, `if (head)` pushed nothing, and the loop skipped index 0 as well: the full path was
// never asked of the game's own server.
const rootGame = A.importCandidates("src/assets.js", ["http://127.0.0.1:5438"], "");
check("a game at the project root asks its own server for the full path first",
  rootGame[0] === "http://127.0.0.1:5438/src/assets.js", rootGame);
check("...and still tries the shorter ones after it",
  rootGame.includes("http://127.0.0.1:5438/assets.js"), rootGame);

// =========================================================================== the real game
const GAME = process.env.STUDIO_TEST_GAME
  || "C:/Users/Administrator/Desktop/brainrot 3d game research crazygames/rot-rush";
if (existsSync(path.join(GAME, "src/render/proplib.ts"))) {
  console.log("\nThe real library in " + path.basename(GAME));
  const stub = path.join(TMP, "pc-stub.mjs");
  const { writeFileSync } = await import("node:fs");
  writeFileSync(stub, "export class Mesh{constructor(d){this.device=d;}}\nexport class GraphicsDevice{}\n"
    + "export class Vec3{constructor(x,y,z){this.x=x;this.y=y;this.z=z;}}\nexport class Entity{}\n");
  const proplib = await bundle(path.join(GAME, "src/render/proplib.ts"), path.join(TMP, "proplib.mjs"), stub);
  const palette = await bundle(path.join(GAME, "src/sim/palette.ts"), path.join(TMP, "palette.mjs"), stub);
  const creatures = await bundle(path.join(GAME, "src/render/creatures.ts"), path.join(TMP, "creatures.mjs"), stub);

  check("its NATURE table is private too", proplib.NATURE === undefined);
  const treeRef = { type: "spec", name: "Candy tree", file: "rot-rush/src/render/proplib.ts",
    root: "rot-rush", table: "NATURE", key: "nature.tree", index: 0 };
  const tree = A.buildFrom(proplib, treeRef, { THREE }, [palette]);
  check("the candy tree draws", tree.parts > 0, tree.how + " \u00b7 " + tree.parts + " parts");

  const def = proplib.PROPS.find((d) => d.id === "nature.tree");
  const want = def.build({ seed: 7, s: palette.SCRIPT.bubblegum, tint: null });
  const ours = tree.object.children.map((c) => c.material.color);
  check("in exactly the colours the game's own colour script produces",
    want.length === ours.length && want.every((p, i) => p.color === ours[i]),
    ours.map((c) => "#" + c.toString(16)).join(" "));

  let blank = [];
  for (const d of proplib.PROPS) {
    const out = A.buildFrom(proplib, { type: "spec", key: d.id, table: "", index: -1 }, { THREE }, [palette]);
    if (!out.parts) blank.push(d.id);
  }
  check("all " + proplib.PROPS.length + " props draw", blank.length === 0, blank.slice(0, 8).join(", "));

  const c0 = { type: "spec", name: creatures.SPECIES[0].name, table: "SPECIES", index: 0, key: "" };
  const cr = A.buildFrom(creatures, c0, { THREE }, [palette]);
  check("a creature draws, built from parts and not from the PlayCanvas mesh",
    cr.parts > 0 && /creatureParts/.test(cr.how), cr.how + " \u00b7 " + cr.parts + " parts");
  let cblank = 0;
  for (let i = 0; i < creatures.SPECIES.length; i++) {
    if (!A.buildFrom(creatures, { type: "spec", table: "SPECIES", index: i, key: "" }, { THREE }, [palette]).parts) cblank++;
  }
  check("all " + creatures.SPECIES.length + " species draw", cblank === 0, cblank + " blank");
} else {
  console.log("\n(the real game is not on this machine \u2014 the fake one covers the convention)");
}

console.log("\n  " + pass + " passed, " + fail + " failed\n");
process.exit(fail ? 1 : 0);
