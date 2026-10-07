// Opening a recorded forge run as a file.
//
// The forge saves the exact code it ran beside every generation. That code has no imports and no
// exports and calls `add()` on a name the forge injects, so opening it in the editor failed with
// "the file did not import: clear is not defined" — the Studio refusing to read a file the Studio
// wrote. Photographed in the editor before this existed.
//
// Run: npm run test:forgescript

import { isForgeScript, wrapForgeScript } from "../src/components/engine/edit/load";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

// ---------------------------------------------------------------- what is a forge script

const RUN = [
  "const CORE = 0x14122b;",
  "const body = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),",
  "  new THREE.MeshStandardMaterial({ color: CORE }));",
  "body.name = 'body';",
  "add(body);",
].join("\n");

const MODULE = [
  "import * as THREE from 'three';",
  "export function buildChest(THREE, params) {",
  "  return new THREE.Group();",
  "}",
].join("\n");

ok("a recorded run is recognised", isForgeScript(RUN));
ok("a module is not", !isForgeScript(MODULE));
ok("...not even one that calls add() inside itself",
   !isForgeScript("export function build(T){const g=new T.Group();g.add(new T.Mesh());return g;}"));
ok("an import alone means a module", !isForgeScript("import { x } from './y.js';\nadd(x);"));
ok("but a dynamic import inside a run does not",
   isForgeScript("const m = await import('./parts.js');\nadd(m.leg());"));
ok("a file that adds nothing is not a run", !isForgeScript("const a = 1;\nconsole.log(a);"));
ok("a method call named add is not the forge's add",
   !isForgeScript("const g = new THREE.Group();\ng.add(mesh);"));

// ---------------------------------------------------------------- what the wrapper produces

const wrapped = wrapForgeScript(RUN, "https://example.test/three.module.js");

ok("it becomes a module with a build function", /export async function build\(/.test(wrapped));
ok("...that the editor's first signature still fits", /build\(THREE, params(, __c)?\)/.test(wrapped));

// ONE VOCABULARY, OR THE APP'S OWN GENERATED CODE THROWS. `world.run` destructures these five
// into every snippet it runs, and the snippet the Studio writes for a model is
// `const obj = await loadGLB(url); add(obj);` — so a wrapper without them turned the Studio's own
// output into `ReferenceError: loadGLB is not defined`, reported to the person as the unhelpful
// and true "the asset did not build". The list is checked as a list so the next name added to one
// wrapper cannot be forgotten in the other.
for (const name of ["loadGLB", "buildAsset", "buildFrom", "importProject", "toObject"]) {
  ok("the wrapper offers " + name, new RegExp("const " + name + " =").test(wrapped), name);
}
ok("...and takes the context they come from", /build\(THREE, params, __c\)/.test(wrapped));
ok("a name that cannot work outside the editor SAYS SO, rather than not existing",
   /needs the editor/.test(wrapped));
// EVERY name the forge destructures into a run, not just the obvious ones. The first version of
// this wrapper supplied four, and the chest died on `log is not defined` — in the editor, in a
// photograph, after it had already got past the first two faults.
ok("every name the forge injects is there",
   ["const add =", "const clear =", "const log =", "params = params ||", "const forge =",
    "const engine =", "pc = null", "app = null", "device = null",
    "renderer = null", "scene = null", "camera = null", "root = group"]
     .every((k) => wrapped.includes(k)));
ok("a THREE that cannot make a Group is not three, and is replaced",
   wrapped.includes("typeof THREE.Group !== 'function'"));
ok("the body is kept exactly, not reindented or rewritten", wrapped.includes(RUN));
ok("three is imported absolutely, because a blob module has no base",
   wrapped.includes('import * as __three from "https://example.test/three.module.js"'));
ok("the body runs inside an async function, so it may await",
   /await \(async \(\) => \{/.test(wrapped));
ok("and what it added is what comes back", wrapped.includes("return group;"));
ok("it says what it is, so the panel can tell", wrapped.includes("forgeScript: true"));

// ---------------------------------------------------------------- it actually runs
//
// A stand-in three, so the wrapper can be executed here rather than only pattern-matched.

class Group {
  isObject3D = true;
  name = "";
  children: any[] = [];
  add(o: any) { this.children.push(o); return this; }
  remove(o: any) { this.children = this.children.filter((c) => c !== o); return this; }
}
const T: any = {
  Group,
  Mesh: class { isObject3D = true; name = ""; constructor(public g: any, public material: any) {} },
  BoxGeometry: class { constructor(public x: number, public y: number, public z: number) {} },
  MeshStandardMaterial: class { constructor(public o: any) {} },
};

// The import line is the one thing node cannot follow here, so it is dropped: the wrapper falls
// back to the THREE it is handed, which is the path the editor always takes anyway.
const runnable = wrapped
  .split("\n").filter((l) => !l.startsWith("import * as __three")).join("\n")
  .replace("if (!THREE || typeof THREE.Group !== 'function') THREE = __three;", "")
  .replace("export const manifest", "const manifest")
  .replace("export async function build", "return async function build");

(async () => {
  // eslint-disable-next-line no-new-func
  const build = new Function(runnable)();
  const out = await build(T, {});
  ok("the run builds an object", !!out && out.isObject3D);
  ok("...holding what the script added", out.children.length === 1);
  ok("...with the name the script gave it", out.children[0].name === "body");
  ok("...and the colour the script chose", out.children[0].material.o.color === 0x14122b);

  // A script that uses the forge's other names must not throw. `log` is the one that actually
  // bit; the rest are here so the next one cannot.
  const uses = [
    "log('building');",
    "if (engine !== 'three') throw new Error('wrong engine');",
    "const _ = [pc, app, device, renderer, scene, camera];",
    "root.add(new THREE.Mesh(new THREE.BoxGeometry(1,1,1), new THREE.MeshStandardMaterial({})));",
    "add(new THREE.Mesh(new THREE.BoxGeometry(1,1,1), new THREE.MeshStandardMaterial({})));",
  ].join("\n");
  const w2 = wrapForgeScript(uses, "https://example.test/three.module.js")
    .split("\n").filter((l) => !l.startsWith("import * as __three")).join("\n")
    .replace("if (!THREE || typeof THREE.Group !== 'function') THREE = __three;", "")
    .replace("export const manifest", "const manifest")
    .replace("export async function build", "return async function build");
  try {
    // eslint-disable-next-line no-new-func
    const out2 = await new Function(w2)()(T, {});
    ok("a script that logs, and reads every injected name, runs", out2.children.length === 2);
  } catch (e: any) {
    ok("a script that logs, and reads every injected name, runs", false, String(e?.message || e));
  }

  if (fails.length) {
    console.error("\nFAILED " + fails.length + " of " + (pass + fails.length));
    for (const f of fails) console.error("  x " + f);
    process.exit(1);
  }
  console.log("forge script: " + pass + " checks pass");
})();
