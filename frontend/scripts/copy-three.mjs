// The Studio's OWN three.js, copied into the bundle so a project that has none can still be
// looked at.
//
// The editor's viewport is three. Until now the three it ran came only from the PROJECT — the
// right answer when the project is a three game, because a second copy of three builds meshes the
// first copy does not recognise, and the two could not share a scene. But half the games here are
// PlayCanvas, Phaser or Pixi, and a few have no engine at all. For those, `module_path(project,
// "three")` returns None, `loadEngine` throws, and opening a .glb drew nothing at all: no error a
// person could read, just an empty grid and `0 tris · 0 draws`.
//
// A GLB is a FILE. Loading one into the editor's own viewport does not have to share anything
// with the game, so the project's three is a preference here, not a requirement. This is the
// fallback: served at /vendor/three/, tried last, used only when the project cannot answer.
//
// Copied rather than bundled: three is a split build (`three.module.js` imports `./three.core.js`
// since r163), and copying both keeps that relative import working with no rewriting anywhere.

import { copyFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "node_modules", "three", "build");
const dest = join(here, "..", "dist", "vendor", "three");

const FILES = ["three.module.js", "three.core.js"];

if (!existsSync(join(src, FILES[0]))) {
  console.error("copy-three: no three in node_modules — run `npm install` first");
  process.exit(1);
}
mkdirSync(dest, { recursive: true });
let n = 0;
for (const f of FILES) {
  const from = join(src, f);
  if (!existsSync(from)) continue;     // r162 and earlier are a single file; that is fine
  copyFileSync(from, join(dest, f));
  n++;
}
console.log(`copy-three: ${n} file${n === 1 ? "" : "s"} -> dist/vendor/three`);
