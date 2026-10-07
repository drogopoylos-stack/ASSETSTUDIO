// Opening a .glb in the editor.
//
// This is held as a test because the failure was silent and the symptom named the wrong thing.
// `/api/workspace/file` answers a model with `{"kind": "model", "size": 144652}` and NO `text`
// field, because a model is not text. The Engine window wrote `f.text || ""`, so the editor
// opened on an empty source: the header named the file, the viewport was empty, and the one
// message on screen was "the asset did not build" — true, and about the wrong thing entirely.
// Underneath it the loader, the model endpoint and the three build were all healthy.
//
// The rules below are what stop it coming back by another door. Two of them are about the shape
// of code rather than its behaviour, because the fault was a MISSING branch: there is nothing to
// call, so there is nothing to assert on except that the branch is written.
//
// Run: npm run test:openmodel

import { readFileSync } from "node:fs";
import { join } from "node:path";

// Run from `frontend/`, the way every other test in this folder is. The bundle lands in
// data/tmp, so a path relative to the FILE would point somewhere else entirely.
const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

const engine = src("pages/Engine.tsx");
const library = src("components/engine/Library.tsx");
const client = src("api/client.ts");

// ---------------------------------------------------------------- the fault itself

console.log("A model is not text");

ok("nothing defaults a file's text to an empty string",
   !/code:\s*f\.text\s*\|\|\s*""/.test(engine),
   "the empty-string default is back in Engine.tsx — that is the whole fault");

ok("the opener asks what the file IS before it asks for its text",
   engine.indexOf('f.kind === "model"') > 0
   && engine.indexOf('f.kind === "model"') < engine.indexOf("setEditFile({ path: p, code: f.text })"),
   "the model branch must come first");

ok("a file with no text says so rather than opening blank",
   /typeof f\.text !== "string"/.test(engine) && /has nothing to edit/.test(engine));

// ---------------------------------------------------------------- one writer

console.log("\nThe snippet that opens a model is written once");

ok("there is a modelCode()", /function modelCode\(/.test(engine));
ok("...that the Library's route uses", /code: modelCode\(a\.name, modelFile, url\)/.test(engine));
ok("...and the file route uses too", /code: modelCode\(name, name,/.test(engine));
ok("nobody writes the loadGLB line by hand any more",
   (engine.match(/await loadGLB\(/g) || []).length === 1,
   "found " + (engine.match(/await loadGLB\(/g) || []).length);

// ---------------------------------------------------------------- every way in

console.log("\nEvery way a path becomes an editor source");

ok("the ?edit= deep link goes through the same opener",
   /openRef\.current\(p\)/.test(engine) && /openRef\.current = openInEditor/.test(engine));

ok("a model addressed only by its absolute path can still be fetched",
   /engineModelPath:/.test(client) && /project=&path=\$\{encodeURIComponent\(path\)\}/.test(client),
   "the file picker and the deep link have a path and no project");

ok("a mesh that is not glTF goes to the endpoint that converts it",
   /f\.convert \? api\.wsModel\(p\)/.test(engine),
   ".obj and .stl need trimesh, not the glTF path");

// ---------------------------------------------------------------- the Library

console.log("\nThe Library offers a model the same doors as a spec");

ok("openable() is named once", /function openable\(a: LibraryAsset\): boolean/.test(library));
ok("...and a model is openable", /a\.type === "model"/.test(library) && /!!a\.model/.test(library));
ok("a double-click asks openable(), not a hardcoded pair",
   /onDoubleClick=\{\(\) => \(onOpenAsset && openable\(a\)/.test(library));
ok("the inspector's buttons ask openable() too",
   /\{openable\(sel\) && \(/.test(library));
// Three: the declaration and the two decisions. A fourth place deciding this for itself is how
// the model was left out the first time.
ok("openable() is the ONLY thing that decides it",
   (library.match(/openable\(/g) || []).length === 3,
   "found " + (library.match(/openable\(/g) || []).length + " mentions, want 3");

// ---------------------------------------------------------------- the engine field

console.log("\nA file belongs to no engine");

// The rule moved into assetOpen.ts as `editEngineFor`, and grew: it covered models only, and
// every spec and code row of a PlayCanvas game then opened to the same empty grid, sent to a
// PlayCanvas studio with no `buildAsset`. Its behaviour is pinned in openasset.test.mjs; here the
// page is held to USING it, so a second inline decision cannot creep back in.
const assetOpen = src("components/engine/edit/assetOpen.ts");
ok("a model opens as three whatever the game is built with",
   /engine: editEngineFor\(asset, /.test(engine)
   && /export function editEngineFor/.test(assetOpen)
   && /if \(asset\) return "three";/.test(assetOpen),
   "a .glb built in a hidden PlayCanvas studio cannot even call loadGLB");
ok("...and so does every other Library entry, which is the half that was missed",
   !/pcProject \? "playcanvas" : "three"/.test(engine),
   "a spec or code row of a PlayCanvas game went to a studio with no buildAsset");

// ---------------------------------------------------------------- one three, by URL
//
// The Ballerina GLB: the viewport ran Dino Smash's three (r160, the first URL that loaded) and
// the loader was bound to the Studio's (r183). The r160 renderer called `material.onBuild` on
// r183 materials and threw inside requestAnimationFrame: outliner full, 10,770 triangles counted,
// viewport blank, no message anywhere. Two rules for "which three" can disagree; one cannot.

console.log("\nThe loader is bound to the three the viewport loaded");
const editor = src("components/engine/edit/Editor.tsx");
const world = src("components/engine/edit/world.ts");

ok("the loader URL carries the viewport's three",
   /engineLoaderUrl\(source\.project \|\| "", loaderThreeRef\(threeUrl/.test(editor),
   "a loader bound by any other rule can disagree with the scene");
ok("...and the client appends it", /&three=\$\{encodeURIComponent\(three\)\}/.test(client));
ok("the world remembers which three it runs", /w\.engineUrl = threeUrl/.test(editor));
ok("a model from a foreign three is refused, naming both modules",
   /instanceof this\.T\.Object3D/.test(world) && /different copy of three/.test(world) && /viewport: /.test(world));

console.log("\nA throw in the frame loop is a message, not a blank");
ok("the frame loop calls the guarded render", /w\.renderSafe\(\)/.test(editor));
ok("...which keeps the first error and tells the editor once",
   /renderSafe\(\)/.test(world) && /this\.lastError = msg/.test(world) && /onRenderError\?\.\(msg\)/.test(world));
ok("the editor shows it without hiding the outliner", /renderError && phase !== "error"/.test(editor));
ok("draw calls are counted over the whole frame, not the last pass",
   /info\.autoReset = false;\s*\n\s*info\.reset\(\);/.test(world) && /this\.lastCalls = info\.render\.calls/.test(world));
ok("stats() carries the error and whether anything was drawn",
   /error: this\.lastError/.test(world) && /drawn: this\.lastCalls > 0/.test(world));

// ---------------------------------------------------------------- PlayCanvas runs open
//
// Every PlayCanvas bench and every PlayCanvas run read "the asset did not build ... loaded, but
// it is not three" in the Edit tab. Three lines together did it: the module list dropped the
// Studio's three for PlayCanvas, the bench was labelled three, and the viewport asked for "" so
// the PlayCanvas module won and was refused. Found twice on the same day, by two sessions.

console.log("\nA PlayCanvas asset opens in the Edit tab");
ok("the Studio's three is on every module list, PlayCanvas included",
   /return withStudioThree\(urls\);\s*\n\s*\}, \[gen, projects, genDev\]\);/.test(engine)
   && !/k === "playcanvas" \? urls : withStudioThree\(urls\)/.test(engine));
ok("the bench carries the engine that built it", /engine: benchEngine/.test(engine) && /moduleUrls: benchUrls/.test(engine)
   && !/moduleUrls, engine: "three",/.test(engine));
ok("...read from the backend, with the code as the fallback", /b\.engine \|\| ""/.test(engine) && /PC_CODE\.test\(bench\.code\)/.test(engine));
ok("...and its own project, not the rail's empty one", /project: b\.project \|\| proj/.test(engine));
ok("the viewport always asks for three", /const want: EngineKind = "three";/.test(editor));
ok("the bench type has the field", /project\?: string; engine\?: string;/.test(client));

// ----------------------------------------------------------------

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);
