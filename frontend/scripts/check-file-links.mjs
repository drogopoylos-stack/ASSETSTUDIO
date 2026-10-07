// Extracts the real regex block out of SessionFeed.tsx and exercises it, so the test can never
// drift from the source. Adversarial cases matter as much as the happy path: a matcher that is
// too greedy turns a shell command into one giant fake link, which is worse than no link.
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../src/components/SessionFeed.tsx", import.meta.url), "utf8");
const start = src.indexOf("const FILE_EXTS");
const end = src.indexOf("\n", src.indexOf("const FILE_ONE"));
const block = src.slice(start, end);
const { FILE_RE, FILE_ONE } = new Function(block + "\nreturn { FILE_RE, FILE_ONE };")();

const B = "\\";                                  // one backslash, kept out of the literals below
const cases = [
  // the reported bug: a folder name with spaces
  [`C:${B}Users${B}Administrator${B}Desktop${B}brainrot 3d game research crazygames${B}dino_after.png`,
   [`C:${B}Users${B}Administrator${B}Desktop${B}brainrot 3d game research crazygames${B}dino_after.png`]],
  ["C:/Users/Administrator/Desktop/brainrot 3d game research crazygames/dino_after.png",
   ["C:/Users/Administrator/Desktop/brainrot 3d game research crazygames/dino_after.png"]],
  [`C:${B}Users${B}me${B}WOLT - EFOOD automatic upload${B}web${B}favicon.ico`,
   [`C:${B}Users${B}me${B}WOLT - EFOOD automatic upload${B}web${B}favicon.ico`]],
  // must stop at the FIRST extension, not swallow the rest of the sentence
  [`saved to C:${B}a${B}b file.png and then more.png here`, [`C:${B}a${B}b file.png`, "more.png"]],
  [`See C:${B}a${B}b.png, C:${B}c${B}d.png`, [`C:${B}a${B}b.png`, `C:${B}c${B}d.png`]],
  [`ends a sentence C:${B}a${B}my report.md.`, [`C:${B}a${B}my report.md`]],
  [`C:${B}a${B}my v1.2 file.png`, [`C:${B}a${B}my v1.2 file.png`]],
  // everything that already worked must keep working
  [".studio-uploads/paste-123.png", [".studio-uploads/paste-123.png"]],
  ["final.png", ["final.png"]],
  ["src/render/world.ts", ["src/render/world.ts"]],
  ["frontend/src/pages/Workspace.tsx", ["frontend/src/pages/Workspace.tsx"]],
  // must NOT become one giant link
  ["npm run build && cp a.js b.js", ["a.js", "b.js"]],
  [`cd "C:/x/y" && sed -n 1,5p src/render/world.ts`, ["src/render/world.ts"]],
  ["I updated the config.json file and it works", ["config.json"]],
  ["see the report.md for details", ["report.md"]],
];

let pass = 0, fail = 0;
for (const [text, want] of cases) {
  FILE_RE.lastIndex = 0;
  const got = text.match(FILE_RE) || [];
  const ok = JSON.stringify(got) === JSON.stringify(want);
  ok ? pass++ : fail++;
  console.log((ok ? "PASS  " : "FAIL  ") + JSON.stringify(text));
  if (!ok) {
    console.log("        want " + JSON.stringify(want));
    console.log("        got  " + JSON.stringify(got));
  }
}

console.log("");
// FILE_ONE decides whether a backticked span / tool subtitle becomes a link
const ones = [
  [`C:${B}Users${B}Administrator${B}Desktop${B}brainrot 3d game research crazygames${B}dino_after.png`, true],
  [".studio-uploads/x.png", true],
  ["final.png", true],
  ["src/render/world.ts", true],
  ["npm run build && cp a.js b.js", false],
  [`cd "C:/x" && sed -n 1,5p src/render/world.ts`, false],
  ["some words about a thing.md", false],
];
for (const [s, want] of ones) {
  const got = FILE_ONE.test(s);
  const ok = got === want;
  ok ? pass++ : fail++;
  console.log((ok ? "PASS  " : "FAIL  ") + `FILE_ONE(${JSON.stringify(s)}) = ${got} (want ${want})`);
}

// a pathological string must not hang the feed
const t0 = Date.now();
const evil = "C:" + B + "a b ".repeat(4000) + "no_extension_here";
FILE_RE.lastIndex = 0;
evil.match(FILE_RE);
const ms = Date.now() - t0;
const fastEnough = ms < 250;
fastEnough ? pass++ : fail++;
console.log((fastEnough ? "PASS  " : "FAIL  ") + `no catastrophic backtracking on a 16k-char near-miss (${ms} ms)`);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
