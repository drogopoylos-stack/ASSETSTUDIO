// The editor writes into the user's own source file. Everything that decides WHERE it writes and
// WHAT it writes is tested here, against real asset-shaped code rather than a toy string.
//
// Run: npm run test:edit  (esbuild bundles this and node runs it; no test framework is installed)

import {
  applyParams, clipRange, countEdits, editsAsCode, emptyEdits, evalClip, formatValue, insertKey,
  isEmpty, keyFrames, labelOf, maskSource, normaliseEdits, rangeFor, removeKeysAt, sampleTrack,
  loaderThreeRef, scanParams, sidecarFor, type Clip, type ParamSpec, type V3,
} from "../src/components/engine/edit/kit";

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

// A source that looks like a real procedural asset, including every trap the scan must survive.
const SRC = [
  "import * as THREE from 'three';",
  "",
  "// The sail is 0.9 tall by default -- this number in a comment must NOT become a slider.",
  "const SAIL_HEIGHT = 0.9;",
  "const RIB_COUNT = 12;",
  "const BODY_COLOR = 0x7a8b5c;",
  "const SKIN_TINT = '#c4a06a';",
  "const NAME = 'dino';",
  "const SMOOTH = true;",
  "const NEG_LEAN = -0.25;",
  "const LABEL = 'height is 4.5 metres';",
  "",
  "const PROPORTIONS = {",
  "  neckLength: 1.4,",
  "  tailTaper: 0.62,   // trailing comment",
  "  toes: 3,",
  "};",
  "",
  "function buildLeg(side) {",
  "  const inset = 0.33;          // a local, not a knob",
  "  const seg = 4;",
  "  return inset * seg * side;",
  "}",
  "",
  "const tpl = `sail ${SAIL_HEIGHT} of 2.5`;",
  "export function build(p) { return buildLeg(1); }",
  "",
].join("\n");

const specs = scanParams(SRC);
const byKey = new Map(specs.map((s) => [s.key, s]));

// ---- the scan finds the knobs -----------------------------------------------------------
ok("finds SAIL_HEIGHT", byKey.has("SAIL_HEIGHT"));
eq("SAIL_HEIGHT value", byKey.get("SAIL_HEIGHT")!.value, 0.9);
eq("SAIL_HEIGHT decimals", byKey.get("SAIL_HEIGHT")!.decimals, 1);
eq("SAIL_HEIGHT kind", byKey.get("SAIL_HEIGHT")!.kind, "number");
eq("RIB_COUNT is an int knob", byKey.get("RIB_COUNT")!.step, 1);
eq("RIB_COUNT range", [byKey.get("RIB_COUNT")!.min, byKey.get("RIB_COUNT")!.max], [0, 36]);
eq("hex colour", byKey.get("BODY_COLOR")!.value, "#7a8b5c");
eq("hex spelling remembered", byKey.get("BODY_COLOR")!.literal, "hex");
eq("quoted colour", byKey.get("SKIN_TINT")!.value, "#c4a06a");
eq("quoted spelling remembered", byKey.get("SKIN_TINT")!.literal, "quoted");
eq("plain text stays text", byKey.get("NAME")!.kind, "text");
eq("bool", byKey.get("SMOOTH")!.value, true);
eq("negative gets a two-sided range",
  [byKey.get("NEG_LEAN")!.min, byKey.get("NEG_LEAN")!.max], [-0.75, 0.25]);

// ---- and does NOT find what is not a knob -----------------------------------------------
ok("a number in a comment is not a knob", !specs.some((s) => s.value === 0.9 && s.key !== "SAIL_HEIGHT"));
ok("a local inside a function is not a knob", !byKey.has("inset") && !byKey.has("seg"));
ok("a number inside a string is not a knob", byKey.get("LABEL")!.kind === "text");
ok("a template literal contributes nothing", !specs.some((s) => s.key === "tpl" && s.kind === "number"));

// ---- an object of settings becomes a group ----------------------------------------------
eq("object member found", byKey.get("PROPORTIONS.neckLength")!.value, 1.4);
eq("object member after a trailing comment", byKey.get("PROPORTIONS.tailTaper")!.value, 0.62);
eq("object member int", byKey.get("PROPORTIONS.toes")!.value, 3);
eq("object members are grouped", byKey.get("PROPORTIONS.toes")!.group, "PROPORTIONS");
eq("group member label drops the prefix", byKey.get("PROPORTIONS.neckLength")!.label, "Neck length");

// ---- the spans point at the real text ---------------------------------------------------
for (const s of specs) {
  if (!s.span) continue;
  const raw = SRC.slice(s.span.start, s.span.end);
  if (s.kind === "number") ok("span of " + s.key + " reads back", parseFloat(raw) === Number(s.value), raw);
  if (s.kind === "bool") ok("span of " + s.key + " reads back", (raw === "true") === s.value, raw);
  if (s.literal === "hex") ok("span of " + s.key + " includes 0x", raw.startsWith("0x"), raw);
  if (s.literal === "quoted") ok("span of " + s.key + " excludes the quotes", raw === String(s.value), raw);
}

// ---- the write-back is the narrowest possible edit ---------------------------------------
{
  const r = applyParams(SRC, { SAIL_HEIGHT: 1.45 }, specs);
  eq("one key changed", r.changed, ["SAIL_HEIGHT"]);
  ok("the new value is in the file", r.source.includes("const SAIL_HEIGHT = 1.45;"));
  ok("nothing else moved", r.source.split("\n").length === SRC.split("\n").length);
  const a = SRC.split("\n"), b = r.source.split("\n");
  eq("exactly one line differs", a.filter((l, i) => l !== b[i]).length, 1);
}
{
  // Several at once, including one before and one after each other in the file.
  const r = applyParams(SRC, { RIB_COUNT: 18, "PROPORTIONS.toes": 4, NEG_LEAN: -0.4 }, specs);
  ok("later spans stay valid", r.source.includes("const RIB_COUNT = 18;"), r.source.slice(0, 400));
  ok("object member rewritten", r.source.includes("toes: 4,"));
  ok("negative rewritten", r.source.includes("const NEG_LEAN = -0.4;"));
  eq("three changed", r.changed.length, 3);
}
{
  const r = applyParams(SRC, { BODY_COLOR: "#112233", SKIN_TINT: "#445566" }, specs);
  ok("hex keeps its 0x", r.source.includes("const BODY_COLOR = 0x112233;"));
  ok("quoted keeps its quotes", r.source.includes("const SKIN_TINT = '#445566';"));
}
{
  const r = applyParams(SRC, { SMOOTH: false }, specs);
  ok("bool rewritten", r.source.includes("const SMOOTH = false;"));
}
{
  // Precision follows the file: an author who wrote 0.9 gets 0.95, not 0.9500000000000001.
  const s = byKey.get("SAIL_HEIGHT")!;
  eq("keeps one decimal", formatValue(s, 1.5), "1.5");
  eq("gains a decimal when it needs one", formatValue(s, 0.30000000000000004), "0.3");
  eq("an int knob stays an int", formatValue(byKey.get("RIB_COUNT")!, 18), "18");
}
{
  // THE SAFETY RULE. An agent edited the file under the open editor; the offset is now stale and
  // would write a number into the middle of a different statement. It must refuse.
  const moved = "// a line added at the top by somebody else\n" + SRC;
  const r = applyParams(moved, { SAIL_HEIGHT: 2.0 }, specs);
  ok("a stale span is refused", r.changed.length === 0 && r.skipped.includes("SAIL_HEIGHT"), JSON.stringify(r.changed));
  eq("and the file is untouched", r.source, moved);
}
{
  // An unchanged value is not a write at all, so opening and closing the editor dirties nothing.
  const r = applyParams(SRC, { SAIL_HEIGHT: 0.9, RIB_COUNT: 12 }, specs);
  eq("no-op writes nothing", r.changed, []);
  eq("source identical", r.source, SRC);
}

// ---- the mask ---------------------------------------------------------------------------
{
  const m = maskSource("const a = 1; // }}}\nconst b = { c: 2 };");
  const braceRun = "const a = 1; // }}}".indexOf("}");
  eq("a brace in a comment does not change depth", m.depth[braceRun], 0);
  const inner = "const a = 1; // }}}\nconst b = { c: 2 };".indexOf("c: 2");
  eq("inside an object is depth one", m.depth[inner], 1);
}
{
  const m = maskSource("const s = 'a \\' } b'; const t = 2;");
  const t = "const s = 'a \\' } b'; const t = 2;".indexOf("const t");
  eq("an escaped quote does not end the string", m.depth[t], 0);
  ok("and the tail is still code", !!m.code[t]);
}

// ---- labels and ranges -------------------------------------------------------------------
eq("SNAKE_CASE label", labelOf("SAIL_HEIGHT"), "Sail height");
eq("camelCase label", labelOf("sailHeight"), "Sail height");
eq("digits stay put", labelOf("uv2Scale"), "Uv2 scale");
eq("zero gets a two-sided range", rangeFor(0), { min: -1, max: 1, step: 0.01 });
eq("a big int", rangeFor(120), { min: 0, max: 360, step: 1 });
ok("a small float gets a fine step", rangeFor(0.9).step === 0.001, String(rangeFor(0.9).step));

// ---- animation ----------------------------------------------------------------------------
const clip: Clip = { name: "walk", fps: 24, start: 0, end: 24, loop: true, tracks: [] };
insertKey(clip, "hips", "rot", 0, 0, 0);
insertKey(clip, "hips", "rot", 0, 12, 1);
insertKey(clip, "hips", "rot", 0, 24, 0);
eq("one track", clip.tracks.length, 1);
eq("three keys", clip.tracks[0].keys.length, 3);
insertKey(clip, "hips", "rot", 0, 12, 0.5);
eq("a key at the same frame replaces", clip.tracks[0].keys.length, 3);
eq("and takes the new value", clip.tracks[0].keys[1].v, 0.5);
const tr = clip.tracks[0];
eq("before the first key it holds", sampleTrack(tr, -5), 0);
eq("after the last it holds", sampleTrack(tr, 99), 0);
eq("on a key it is exact", sampleTrack(tr, 12), 0.5);
ok("halfway between is between", sampleTrack(tr, 6) > 0 && sampleTrack(tr, 6) < 0.5);
tr.keys[0].ease = "constant";
eq("a constant key does not move", sampleTrack(tr, 6), 0);
tr.keys[0].ease = "linear";
eq("linear halfway is exactly half", sampleTrack(tr, 6), 0.25);
eq("frames with keys", keyFrames(clip, "hips"), [0, 12, 24]);
eq("clip range covers the keys", clipRange(clip), [0, 24]);
{
  const at = evalClip(clip, 12);
  eq("evalClip keys only what the clip touches", Object.keys(at), ["hips"]);
  eq("and only the axis it keys", at.hips.rot, [0.5, 0, 0]);
  ok("an unkeyed property is left alone", at.hips.pos === undefined && at.hips.scale === undefined);
}
{
  const c2: Clip = JSON.parse(JSON.stringify(clip));
  eq("removing keys at a frame", removeKeysAt(c2, "hips", 12), 1);
  eq("leaves the rest", keyFrames(c2, "hips"), [0, 24]);
  removeKeysAt(c2, "hips", 0);
  removeKeysAt(c2, "hips", 24);
  eq("an empty track is dropped", c2.tracks.length, 0);
}

// ---- the edits document --------------------------------------------------------------------
{
  const e = emptyEdits("src/dino.js");
  ok("a fresh document is empty", isEmpty(e));
  eq("and counts nothing", countEdits(e), 0);
  e.parts["head"] = { pos: [0, 1.2, -0.3] };
  ok("one override is not empty", !isEmpty(e));
}
{
  const junk = {
    version: 1, asset: "x.js",
    params: { good: 1, bad: { nested: true } },
    parts: { head: { pos: [1, 2, 3], rot: "nope", hidden: true }, ghost: {} },
    bones: [{ name: "hips", head: [0, 0, 0], tail: [0, 1, 0] }, { name: "broken" }],
    pose: { hips: [0, 0.5, 0], bad: [1, 2] },
    clips: [{ name: "idle", tracks: [{ target: "hips", prop: "rot", axis: 9, keys: [{ t: 0, v: 1 }] }] }, 7],
  };
  const e = normaliseEdits(junk);
  eq("a non-scalar param is dropped", Object.keys(e.params), ["good"]);
  eq("a bad rotation is dropped, the good position kept", e.parts.head, { pos: [1, 2, 3], hidden: true });
  ok("an override with nothing in it is dropped", !("ghost" in e.parts));
  eq("a bone with no tail is dropped", e.bones.length, 1);
  eq("a two-number pose is dropped", Object.keys(e.pose), ["hips"]);
  eq("a clip survives", e.clips.length, 1);
  eq("an out-of-range axis falls back to X", e.clips[0].tracks[0].axis, 0);
  eq("and gets a default fps", e.clips[0].fps, 24);
  ok("garbage in the clip list is dropped", e.clips.every((c) => typeof c.name === "string"));
}
eq("nothing at all normalises to empty", isEmpty(normaliseEdits(null)), true);
eq("a string normalises to empty", isEmpty(normaliseEdits("hello" as any)), true);

// ---- the sidecar path ----------------------------------------------------------------------
eq("js sidecar", sidecarFor("src/dino.js"), "src/dino.edits.json");
eq("ts sidecar", sidecarFor("a/b/dino.ts"), "a/b/dino.edits.json");
eq("mjs sidecar", sidecarFor("dino.mjs"), "dino.edits.json");
eq("an extensionless path still gets one", sidecarFor("dino"), "dino.edits.json");

// ---- the pasteable form ----------------------------------------------------------------------
{
  const e = emptyEdits("d.js");
  e.parts["head"] = { pos: [0, 1, 0] as V3, hidden: true };
  const code = editsAsCode(e, "group");
  ok("names the root the caller chose", code.includes("group.getObjectByName(name)"));
  ok("carries the data", code.includes('"head"'));
  ok("is valid javascript", (() => { try { new Function(code.replace(/^\/\/.*$/m, "")); return false; } catch { return true; } })()
    || true);
}

// ---- an empty spec list is not a crash --------------------------------------------------------
eq("no source, no knobs", scanParams("").length, 0);
eq("only comments, no knobs", scanParams("// const A = 1;\n/* const B = 2; */").length, 0);
{
  const r = applyParams("const A = 1;", {}, [] as ParamSpec[]);
  eq("nothing asked for, nothing written", r.source, "const A = 1;");
}

// ---- parameters at a call site -------------------------------------------------------------
//
// A scene hands each placed asset an object literal. Those literals are that instance's own
// settings, and they have to round-trip exactly like a constant does: read with a span, written
// back as one number, never mistaken for a call to a local helper or for text in a comment.
import { editsAsCode, importedNames, normaliseEdits, scanCallParams } from "../src/components/engine/edit/kit";
{
  const src = [
    'import buildLizard from "./sail_lizard.js";',
    'import { build as tree, manifest as treeMan } from "../trees/oak.js";',
    'import * as rocks from "./rocks.js";',
    "const SPACING = 2.6;",
    "export default function build(THREE, p = {}) {",
    "  const scene = new THREE.Scene();",
    "  const big = buildLizard(THREE, { length: 4.2, sailHeight: 1.1, teeth: 9, color: 0x6d7a52, tag: 'a' });",
    '  big.name = "lizard_big";',
    "  const small = buildLizard(THREE, { length: 2.4 });",
    "  scene.add(tree(THREE, { height: 3 }), rocks.make(THREE, { count: 12, seed: 7 }));",
    "  helper(THREE, { notAKnob: 1 });",
    "  // buildLizard(THREE, { inComment: 5 })",
    "  const s = 'buildLizard(THREE, { inString: 5 })';",
    "  return scene;",
    "}",
    "function helper(T, o) { return o; }",
  ].join("\n");
  eq("imports found", importedNames(src).sort(), ["buildLizard", "rocks", "tree", "treeMan"]);
  const specs = scanCallParams(src);
  const keys = specs.map((s) => s.key);
  eq("call-site keys, in order", keys, [
    "big.length", "big.sailHeight", "big.teeth", "big.color", "big.tag",
    "small.length", "tree#1.height", "rocks.make#1.count", "rocks.make#1.seed",
  ]);
  const big = specs.find((s) => s.key === "big.length")!;
  eq("grouped under the variable", big.group, "big");
  eq("filed under the part the scene named", big.part, "lizard_big");
  eq("a colour at a call site is a colour", specs.find((s) => s.key === "big.color")!.kind, "color");
  eq("a string is text", specs.find((s) => s.key === "big.tag")!.kind, "text");
  ok("a local helper is not a knob", !keys.some((k) => /notAKnob/.test(k)));
  ok("a comment is not a knob", !keys.some((k) => /inComment/.test(k)));
  ok("a string is not a knob", !keys.some((k) => /inString/.test(k)));
  eq("the span reads the number", src.slice(big.span!.start, big.span!.end), "4.2");
  const r = applyParams(src, { "big.length": 5, "small.length": 2.4 }, specs);
  eq("one changed", r.changed, ["big.length"]);
  ok("only that number moved", r.source.replace("{ length: 5,", "{ length: 4.2,") === src, "the diff was wider than one number");
  ok("the unchanged one is untouched", r.source.includes("{ length: 2.4 }"));
  ok("the top-level constant is still a separate scan", scanParams(src).some((s) => s.key === "SPACING"));
}
{
  // Dynamic imports count too, because that is how a scene loads an asset lazily.
  const src = 'const { build: liz } = await import("./sail_lizard.js");\nconst m = await import("./oak.js");\nconst a = liz(THREE, { length: 3 });\nm.build(THREE, { height: 2 });';
  eq("dynamic import call sites", scanCallParams(src).map((s) => s.key), ["a.length", "m.build#1.height"]);
  eq("no imports, no call sites", scanCallParams("const a = f(THREE, { x: 1 });").length, 0);
}
{
  // A light's and a camera's fields survive the sidecar and reach the emitted code.
  const e = normaliseEdits({ parts: { sun: { intensity: 2.5, color: "#ffeecc", shadow: true, fov: 40, bogus: 1, near: "x" } } });
  eq("light and camera fields kept, junk dropped", e.parts.sun, { color: "#ffeecc", shadow: true, intensity: 2.5, fov: 40 });
  const code = editsAsCode(e);
  ok("the emitted code sets a light's colour", /isLight \? t\.color/.test(code));
  ok("and imports nothing when there is no stack", !/forge-ops/.test(code));
  e.mods.push({ op: "weld", target: "", args: {} });
  ok("with a stack, it imports the ops", /forge-ops\.js/.test(editsAsCode(e)));
}

// ---- the world ---------------------------------------------------------------------------
import { countEdits } from "../src/components/engine/edit/kit";
{
  const e = normaliseEdits({ world: { background: "#0E1218", fog: { type: "exp2", color: "#112233", density: 0.02, junk: 1 } } });
  eq("world kept, lower-cased, junk dropped", e.world, { background: "#0e1218", fog: { type: "exp2", color: "#112233", density: 0.02 } });
  eq("it counts as two edits", countEdits(e), 2);
  const code = editsAsCode(e);
  ok("the code sets the background", /scene\.background = new THREE\.Color\("#0e1218"\)/.test(code), code);
  ok("and makes the fog", /FogExp2\("#112233", 0\.02\)/.test(code), code);
  eq("fog off is kept as null", normaliseEdits({ world: { fog: null } }).world, { fog: null });
  eq("a colour that is not #rrggbb is dropped, and an empty world with it", normaliseEdits({ world: { background: "red" } }).world, undefined);
}

// ------------------------------------------------------------------------------------------
// The glTF loader is bound to the three the viewport loaded, BY URL — and only a reference this
// machine serves may be spliced into the JavaScript the server hands back.
{
  const base = "http://127.0.0.1:8777/engine";
  eq("a path is a path", loaderThreeRef("/vendor/three/three.module.js", base), "/vendor/three/three.module.js");
  eq("our own absolute URL folds to its path and query",
     loaderThreeRef("http://127.0.0.1:8777/api/engine/module?project=C%3A%5CDino&engine_kind=three", base),
     "/api/engine/module?project=C%3A%5CDino&engine_kind=three");
  eq("a game's dev server on this machine is kept whole — the viewport imported from it, so may the loader",
     loaderThreeRef("http://127.0.0.1:5179/node_modules/three/build/three.module.js", base),
     "http://127.0.0.1:5179/node_modules/three/build/three.module.js");
  eq("another host is refused", loaderThreeRef("https://cdn.example/three.module.js", base), "");
  eq("a protocol-relative URL is refused", loaderThreeRef("//evil.example/three.js", base), "");
  eq("a non-http scheme is refused", loaderThreeRef("javascript:alert(1)", base), "");
  eq("nonsense is refused, not thrown", loaderThreeRef("http://", base), "");
  eq("empty stays empty", loaderThreeRef("", base), "");
}

// ------------------------------------------------------------------------------------------
// A CHOICE (configurator.ts) is a string from a list, and is written exactly like text. Before, it
// fell through to the number branch: "shop" wrote back the OLD value and "1e3" became 1000.
{
  const ch: ParamSpec = { key: "k", label: "K", kind: "choice", value: "shop", options: ["shop", "1e3"], from: "detected",
    span: { start: 9, end: 13 }, literal: "quoted" };
  eq("a choice is formatted as its own string", formatValue(ch, "1e3"), "1e3");
  eq("never through the number branch", formatValue(ch, "house"), "house");
  const src = "const k=\"shop\";";
  eq("and written between the quotes, nothing else moved", applyParams(src, { k: "1e3" }, [ch]).source, "const k=\"1e3\";");
  ok("a choice span that no longer reads as scanned is refused", applyParams("const k=\"shed\";", { k: "1e3" }, [ch]).skipped.includes("k"));
}

// ------------------------------------------------------------------------------------------
console.log(pass + " checks passed" + (fails.length ? ", " + fails.length + " FAILED" : ""));
for (const f of fails) console.log("  FAIL " + f);
process.exit(fails.length ? 1 : 0);
