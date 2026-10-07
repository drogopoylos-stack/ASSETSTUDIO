// The configurator's pure half: what a manifest's `options`, `advanced`, `presets` become, what a
// preset may write into the edit document, which preset the values match, how the panel lays the
// parameters out, where a slider lands, how a thumbnail is framed, and what DAY / NIGHT does to
// the studio rig. Everything here decides what gets WRITTEN or SHOWN, so none of it is left to a
// click to discover.
//
// Run: npm run test:presets  (esbuild bundles this and node runs it; no test framework is installed)

import { applyParams, formatValue, sameLiteral, type ParamSpec, type ParamValue } from "../src/components/engine/edit/kit";
import { choiceOptions, specsFromManifest } from "../src/components/engine/edit/load";
import {
  MAX_PRESETS, applyPreset, filterPresets, longHex, matchPreset, presetBuildValues, presetChanges, presetEntries,
  presetTags, presetsFromManifest, sameValue, withParams,
} from "../src/components/engine/edit/presets";
import {
  GENERAL, SAFE_FRAME, advancedCount, decimalsFor, declaredHandleCount, describePreset, hasNightParam, isConfigurator,
  leavesFrame, matchesWords, panelSpecs, paramSections, prettyOption, showValue, snapValue,
} from "../src/components/engine/edit/configModel";
import { HERO_DIR, fitView, hashString, stableValues, thumbError, thumbKey } from "../src/components/engine/edit/presetThumbs";
import { NIGHT, applyStudioLook, captureStudio } from "../src/components/engine/edit/configLook";
import type { PresetSpec } from "../src/components/engine/edit/configurator";

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
function heading(t: string) { console.log("  " + t); }

console.log("\nThe configurator: choices, presets, the panel, thumbnails and night\n");

// The fixture's manifest, verbatim (data/ab/configurator_fixture.js): every part of the contract.
const FIXTURE = {
  name: "configurator fixture",
  params: {
    width: { value: 6.0, min: 3.6, max: 12, step: 0.1, label: "Width", group: "building" },
    depth: { value: 6.0, min: 3.6, max: 10, step: 0.1, label: "Depth", group: "building" },
    floors: { value: 3, min: 1, max: 6, step: 1, label: "Floors", group: "building" },
    type: { value: "shop", options: ["shop", "house", "service"], label: "Building type", group: "building" },
    rearStair: { value: false, label: "Rear service stair", group: "building" },
    wall: { value: "#d9c9a3", label: "Wall colour", group: "facade" },
    trim: { value: "#6b3f2a", label: "Trim colour", group: "facade", advanced: true },
    bay: { value: 1.2, min: 0.8, max: 2, step: 0.05, label: "Bay width", group: "facade", advanced: true },
    night: { value: false, label: "Night" },
  },
  presets: [
    { name: "Corner shop · tea", tags: ["shop", "corner"], values: { width: 5.8, depth: 6, floors: 3, type: "shop" } },
    { name: "Twin market", tags: ["shop"], values: { width: 9.8, depth: 6, floors: 3, type: "shop" }, note: "two shop fronts" },
    { name: "Narrow house", tags: ["house", "residential"], values: { width: 4.2, depth: 7, floors: 2, type: "house", wall: "#c8d3cf" } },
    { name: "Service block", tags: ["service"], values: { width: 7.2, depth: 8, floors: 4, type: "service", rearStair: true } },
  ],
  handles: [
    { param: "width", axis: "x", side: "max", scale: 2 },
    { param: "depth", axis: "z", side: "max", scale: 2 },
    { param: "floors", axis: "y", side: "max", scale: 1 / 3, snap: 1 },
  ],
};

const specs = specsFromManifest(FIXTURE);
const spec = (k: string) => specs.find((s) => s.key === k)!;
const defaults: Record<string, ParamValue> = Object.fromEntries(specs.map((s) => [s.key, s.value]));

// ------------------------------------------------------------------------------------------
heading("a choice, and an advanced setting, out of a manifest");
{
  eq("every declared parameter is read, in order", specs.map((s) => s.key),
    ["width", "depth", "floors", "type", "rearStair", "wall", "trim", "bay", "night"]);
  eq("options make a choice", spec("type").kind, "choice");
  eq("with its options, in order", spec("type").options, ["shop", "house", "service"]);
  eq("and its value", spec("type").value, "shop");
  eq("the label is the manifest's", spec("type").label, "Building type");
  eq("the group is kept", spec("type").group, "building");
  eq("a choice has no slider range", [spec("type").min, spec("type").max, spec("type").step], [undefined, undefined, undefined]);
  ok("advanced: true is carried", spec("trim").advanced === true && spec("bay").advanced === true);
  ok("and only on the ones that say so", !("advanced" in spec("width")) && !("advanced" in spec("wall")));
  eq("a number keeps its range", [spec("width").min, spec("width").max, spec("width").step], [3.6, 12, 0.1]);
  eq("a colour is still a colour", spec("wall").kind, "color");
  eq("a switch is still a switch", spec("night").kind, "bool");
  eq("an advanced number is still a number", spec("bay").kind, "number");

  const odd = specsFromManifest({ params: {
    a: { value: "loft", options: ["shop", "house"] },          // a value the list does not have
    b: { options: ["x", "y"] },                                  // no value at all
    c: { value: 2, options: ["1", "2", "3"] },                   // a number where a string belongs
    d: { value: "k", options: [3, "", "  ", "k", "k", null, "j"] }, // junk in the list
    e: { value: "p", kind: "choice" },                           // a choice with nothing to choose
    f: { value: "q", options: [] },                              // an empty list
    g: { value: 1, kind: "slider" },                             // a kind nobody knows
    h: { value: "#abcdef", kind: "text" },                       // an explicit kind is obeyed
    i: { value: 1, advanced: "yes" },                            // only `true` is advanced
    j: { value: 1, label: 42 },                                  // a label that is not a string
  } });
  const o = (k: string) => odd.find((s) => s.key === k)!;
  eq("an unknown value is kept as it is, not replaced", [o("a").kind, o("a").value], ["choice", "loft"]);
  eq("no value: the first option", o("b").value, "x");
  eq("a number value becomes its string", o("c").value, "2");
  eq("the list is cleaned: strings, not blank, once each", o("d").options, ["k", "j"]);
  eq("kind choice with no options is inferred instead", o("e").kind, "text");
  eq("an empty options list is no choice", o("f").kind, "text");
  eq("an unknown kind is inferred from the value", o("g").kind, "number");
  ok("and gets the range a number has", typeof o("g").min === "number" && typeof o("g").max === "number");
  eq("an explicit known kind is obeyed", o("h").kind, "text");
  ok("advanced must be exactly true", !("advanced" in o("i")));
  eq("a label that is not a string falls back to the key", o("j").label, "J");

  eq("choiceOptions keeps exact strings, untrimmed", choiceOptions([" a", "a", "b "]), [" a", "a", "b "]);
  eq("choiceOptions of nonsense is empty", [choiceOptions(null), choiceOptions("abc"), choiceOptions({})], [[], [], []]);
  eq("an array of params reads the same", specsFromManifest({ params: [{ key: "t", value: "a", options: ["a", "b"] }] })[0].kind, "choice");
  eq("shorthand params are untouched", specsFromManifest({ params: { n: 0.9, s: "#123456", b: true, t: "x" } }).map((s) => s.kind),
    ["number", "color", "bool", "text"]);
}

heading("a choice can never write a wrong literal");
{
  // `const k="1";` — the 1 is character 9, between the quotes.
  const ch: ParamSpec = { key: "k", label: "K", kind: "choice", value: "1", options: ["1", "1e3", "shop"], from: "detected", span: { start: 9, end: 10 } };
  eq("a choice is written as its string", formatValue(ch, "shop"), "shop");
  eq("one that looks like a number is NOT reformatted", formatValue(ch, "1e3"), "1e3");
  eq("nor is one with trailing zeros", formatValue(ch, "2.50"), "2.50");
  ok("a choice compares as characters, not as a number", sameLiteral(ch, "1") && !sameLiteral(ch, "1.0"));
  const src = 'const k="1";';
  const r = applyParams(src, { k: "1e3" }, [ch]);
  eq("the write-back puts the exact string between the quotes", r.source, 'const k="1e3";');
  const stale = applyParams('const k="2";', { k: "shop" }, [ch]);
  ok("a stale span is refused for a choice too", stale.skipped.includes("k") && stale.source === 'const k="2";', JSON.stringify(stale));
  const text: ParamSpec = { key: "t", label: "T", kind: "text", value: "a", from: "detected" };
  eq("text behaves exactly as before", formatValue(text, "1e3"), "1e3");
}

// ------------------------------------------------------------------------------------------
heading("presets out of a manifest");
const presets = presetsFromManifest(FIXTURE);
{
  eq("the fixture's four presets", presets.map((p) => p.name), ["Corner shop · tea", "Twin market", "Narrow house", "Service block"]);
  eq("tags as written", presets[2].tags, ["house", "residential"]);
  eq("a note is kept", presets[1].note, "two shop fronts");
  ok("no note, no key", !("note" in presets[0]));
  eq("values as written", presets[3].values, { width: 7.2, depth: 8, floors: 4, type: "service", rearStair: true });

  eq("no manifest, no presets", [presetsFromManifest(null), presetsFromManifest(undefined), presetsFromManifest(5), presetsFromManifest("x")], [[], [], [], []]);
  eq("presets that are not a list, none", presetsFromManifest({ presets: { a: 1 } }), []);

  const messy = presetsFromManifest({ presets: [
    null, 7, "preset", [],                                            // not objects
    { name: "  Spaced  ", values: { a: 1 } },                           // trimmed
    { name: "", values: { a: 1 } },                                     // no name
    { name: "   ", values: { a: 1 } },                                  // blank name
    { values: { a: 1 } },                                               // missing name
    { name: 12, values: { a: 1 } },                                     // a name that is not a string
    { name: "Spaced", values: { a: 2 } },                               // a duplicate of the first
    { name: "No values" },                                              // nothing to set
    { name: "Array values", values: [1, 2] },                           // values must be an object
    { name: "Junk values", values: { n: NaN, i: Infinity, z: null, o: {}, l: [1], u: undefined } },
    { name: "Mixed", values: { n: NaN, ok: 3, s: "x", b: false }, tags: ["Shop", " SHOP ", "", 4, "All", "corner"], note: "  a note  " },
    { name: "One tag", values: { a: 1 }, tags: "House" },
    { name: "Proto", values: { __proto__: 1, constructor: 2, prototype: 3, fine: 4 } as any },
    { name: "Note not text", values: { a: 1 }, note: 5 },
  ] });
  eq("only the well-formed survive, first of a name wins", messy.map((p) => p.name), ["Spaced", "Mixed", "One tag", "Proto", "Note not text"]);
  eq("the first 'Spaced' is the one kept", messy[0].values, { a: 1 });
  eq("a bad value costs that value, not the preset", messy[1].values, { ok: 3, s: "x", b: false });
  eq("tags: lower-cased, trimmed, once each, no 'all', strings only", messy[1].tags, ["shop", "corner"]);
  eq("the note is trimmed", messy[1].note, "a note");
  eq("a single tag string is taken as one tag", messy[2].tags, ["house"]);
  eq("keys that would reach a prototype are dropped", Object.keys(messy[3].values), ["fine"]);
  ok("a note that is not text is left out", !("note" in messy[4]));
  ok("no tags at all is an empty list", Array.isArray(messy[3].tags) && messy[3].tags.length === 0);

  const many = presetsFromManifest({ presets: Array.from({ length: MAX_PRESETS + 25 }, (_, i) => ({ name: "p" + i, values: { a: i } })) });
  eq("the count is capped", many.length, MAX_PRESETS);
  eq("and the cap keeps the first ones", many[MAX_PRESETS - 1].name, "p" + (MAX_PRESETS - 1));
  const long = presetsFromManifest({ presets: [{ name: "x".repeat(500), values: { a: 1 }, note: "n".repeat(900), tags: ["t".repeat(99)] }] })[0];
  ok("a very long name, note and tag are cut, not refused", long.name.length === 120 && long.note!.length === 240 && long.tags[0].length === 40);
}

heading("what a preset may write");
{
  const p: PresetSpec = { name: "all kinds", tags: [], values: {
    width: 9.8, floors: "4" as any, type: "loft", rearStair: "yes" as any, wall: "#ABC", night: true, ghost: 1, bay: Infinity as any,
  } };
  const got = presetEntries(p, specs);
  eq("only what each parameter could take from its own control", got.values, { width: 9.8, wall: "#aabbcc", night: true });
  eq("and why each of the rest was refused", got.rejected.map((r) => r.key + ": " + r.why), [
    "floors: needs a number", "type: not one of its options", "rearStair: needs true or false",
    "ghost: not a parameter of this asset", "bay: needs a number",
  ]);
  const bad = presetEntries({ name: "c", tags: [], values: { wall: "red", type: "house" } }, specs);
  eq("a colour must be written as a colour", [bad.values, bad.rejected.map((r) => r.key)], [{ type: "house" }, ["wall"]]);
  const txt = presetEntries({ name: "t", tags: [], values: { label: 3, name: "ok" } },
    [{ key: "label", label: "L", kind: "text", value: "", from: "declared" }, { key: "name", label: "N", kind: "text", value: "", from: "declared" }]);
  eq("text takes only text", txt.values, { name: "ok" });
  const far = presetEntries({ name: "f", tags: [], values: { width: 40 } }, specs);
  eq("a number outside the slider is the author's word, not clamped", far.values, { width: 40 });

  const current = { ...defaults, wall: "#111111" };
  const before = JSON.stringify(current);
  const r = applyPreset(current, presets[1], specs);
  eq("applying sets what the preset names", [r.values.width, r.values.depth, r.values.floors, r.values.type], [9.8, 6, 3, "shop"]);
  eq("and leaves everything else as it was", r.values.wall, "#111111");
  eq("it reports what it applied", r.applied, ["width", "depth", "floors", "type"]);
  ok("the current values are not mutated", JSON.stringify(current) === before);
  eq("what would actually change", presetChanges(presets[1], specs, defaults), ["width"]);
  eq("nothing, once applied", presetChanges(presets[1], specs, r.values), []);

  eq("#abc is the long form, lower-cased", [longHex("#ABC"), longHex("#A1B2C3"), longHex("red"), longHex("#abcd")], ["#aabbcc", "#a1b2c3", "red", "#abcd"]);
}

heading("which preset the values ARE");
{
  eq("the file's own values match none of the four", matchPreset(presets, defaults, specs), null);
  const twin = applyPreset(defaults, presets[1], specs).values;
  eq("after applying Twin market, it is Twin market", matchPreset(presets, twin, specs), "Twin market");
  eq("a slider that lands a hair off still matches", matchPreset(presets, { ...twin, width: 9.800000000000001 }, specs), "Twin market");
  eq("a real change does not", matchPreset(presets, { ...twin, width: 9.9 }, specs), null);
  eq("values a preset does not name do not matter", matchPreset(presets, { ...twin, wall: "#000000", bay: 1.9 }, specs), "Twin market");
  const house = applyPreset(defaults, presets[2], specs).values;
  eq("a colour matches whatever its case", matchPreset(presets, { ...house, wall: "#C8D3CF" }, specs), "Narrow house");
  const general: PresetSpec = { name: "general", tags: [], values: { width: 9.8 } };
  const exact: PresetSpec = { name: "exact", tags: [], values: { width: 9.8, depth: 6, floors: 3 } };
  eq("the most specific match wins", matchPreset([general, exact], twin, specs), "exact");
  eq("whatever the order", matchPreset([exact, general], twin, specs), "exact");
  const twinA: PresetSpec = { name: "A", tags: [], values: { width: 9.8 } };
  const twinB: PresetSpec = { name: "B", tags: [], values: { width: 9.8 } };
  eq("a tie goes to the first", matchPreset([twinA, twinB], twin, specs), "A");
  const empty: PresetSpec = { name: "nothing usable", tags: [], values: { ghost: 1, type: "loft" } };
  eq("a preset with nothing usable matches nothing", matchPreset([empty], defaults, specs), null);
  const partly: PresetSpec = { name: "partly", tags: [], values: { width: 9.8, type: "loft" } };
  eq("the refused part of a preset is not held against it", matchPreset([partly], twin, specs), "partly");

  ok("numbers: a billionth apart is equal", sameValue(spec("width"), 1, 1 + 1e-10) && !sameValue(spec("width"), 1, 1.0001));
  ok("numbers: NaN equals nothing", !sameValue(spec("width"), NaN, NaN));
  ok("colours: case and shorthand do not matter", sameValue(spec("wall"), "#AABBCC", "#abc"));
  ok("text: exact", !sameValue(undefined, "Shop", "shop") && sameValue(undefined, "shop", "shop"));
  ok("different types are different", !sameValue(spec("width"), "3", 3));
}

heading("filtering presets, and the chips");
{
  eq("no filter, all of them", filterPresets(presets).length, 4);
  eq("by name", filterPresets(presets, "twin").map((p) => p.name), ["Twin market"]);
  eq("every word, in any order", filterPresets(presets, "market twin").map((p) => p.name), ["Twin market"]);
  eq("a word can be a tag", filterPresets(presets, "residential").map((p) => p.name), ["Narrow house"]);
  eq("or part of one", filterPresets(presets, "reside").map((p) => p.name), ["Narrow house"]);
  eq("case does not matter", filterPresets(presets, "SERVICE").map((p) => p.name), ["Service block"]);
  eq("a word that matches nothing", filterPresets(presets, "castle"), []);
  eq("a tag", filterPresets(presets, "", "shop").map((p) => p.name), ["Corner shop · tea", "Twin market"]);
  eq("'all' is no tag", filterPresets(presets, "", "all").length, 4);
  eq("text and tag together", filterPresets(presets, "corner", "shop").map((p) => p.name), ["Corner shop · tea"]);
  eq("a tag nobody has", filterPresets(presets, "", "castle"), []);
  eq("the chips: each tag once, in the order it first appears, with its count", presetTags(presets), [
    { tag: "shop", count: 2 }, { tag: "corner", count: 1 }, { tag: "house", count: 1 },
    { tag: "residential", count: 1 }, { tag: "service", count: 1 },
  ]);
  eq("no presets, no chips", presetTags([]), []);
}

heading("the edit document after a preset");
{
  eq("a value equal to the file's is dropped, not stored", withParams({}, specs, { width: 6, depth: 7 }), { depth: 7 });
  eq("one that differs replaces what was there", withParams({ width: 7 }, specs, { width: 9.8 }), { width: 9.8 });
  eq("a key the patch does not name is kept", withParams({ wall: "#000000" }, specs, { width: 9.8 }), { wall: "#000000", width: 9.8 });
  eq("going back to the file's value removes the edit", withParams({ width: 9.8 }, specs, { width: 6 }), {});
  eq("a colour that differs only in case is the file's own", withParams({}, specs, { wall: "#D9C9A3" }), {});
  eq("a key with no parameter is stored as given", withParams({}, specs, { ghost: 1 }), { ghost: 1 });
  eq("a prototype key is never stored", withParams({}, specs, JSON.parse('{"__proto__": 1, "width": 7}')), { width: 7 });
  const tv = presetBuildValues(presets[1], specs);
  eq("a thumbnail builds from the file's values plus the preset", [tv.width, tv.wall, tv.bay, tv.night], [9.8, "#d9c9a3", 1.2, false]);
  eq("never from the person's edits", Object.keys(tv).length, specs.length);
}

// ------------------------------------------------------------------------------------------
heading("the panel's sections");
{
  const off = paramSections(specs, {});
  eq("one section per group, in declaration order, ungrouped in General", off.map((s) => s.title), ["Building", "Facade", GENERAL]);
  eq("advanced settings are hidden by default", off[1].shown.map((s) => s.key), ["wall"]);
  eq("and counted", [off[1].hidden, off[1].total], [2, 3]);
  eq("the Advanced switch shows them", paramSections(specs, { advanced: true })[1].shown.map((s) => s.key), ["wall", "trim", "bay"]);
  eq("how many are advanced at all", advancedCount(specs), 2);
  eq("the filter matches a label", paramSections(specs, { filter: "floor" }).map((s) => [s.title, s.shown.map((x) => x.key)]),
    [["Building", ["floors"]]]);
  eq("or a key", paramSections(specs, { filter: "rearstair" }).flatMap((s) => s.shown.map((x) => x.key)), ["rearStair"]);
  eq("or the section's own title, which shows the whole section", paramSections(specs, { filter: "facade", advanced: true })[0].shown.length, 3);
  const trim = paramSections(specs, { filter: "trim" });
  eq("an advanced match keeps its section, counted as hidden", trim.map((s) => [s.title, s.shown.length, s.hidden]), [["Facade", 0, 1]]);
  eq("a filter that matches nothing leaves nothing", paramSections(specs, { filter: "castle" }), []);
  eq("every word must match", paramSections(specs, { filter: "rear stair" }).flatMap((s) => s.shown.map((x) => x.key)), ["rearStair"]);
  const mixed: ParamSpec[] = [
    { key: "a", label: "A", kind: "number", value: 1, from: "detected" },
    { key: "b", label: "B", kind: "number", value: 1, from: "declared", group: "g" },
    { key: "c", label: "C", kind: "number", value: 1, from: "detected" },
  ];
  eq("General sits where its first member was declared", paramSections(mixed).map((s) => [s.title, s.shown.map((x) => x.key)]),
    [[GENERAL, ["a", "c"]], ["G", ["b"]]]);
  ok("matchesWords: no words match everything", matchesWords([], ["x"]));
  ok("matchesWords: each word somewhere", matchesWords(["a", "b"], ["xa", "yb"]) && !matchesWords(["a", "q"], ["xa", "yb"]));
}

heading("values in words, and where a slider lands");
{
  eq("identifier options read as words", ["shop", "mixed_use", "twoStorey", "semi-detached"].map(prettyOption), ["Shop", "Mixed use", "Two storey", "Semi detached"]);
  eq("anything written for people is left alone", ["Shop / mixed use", "LED", "2 floors", "A"].map(prettyOption), ["Shop / mixed use", "LED", "2 floors", "A"]);
  eq("decimals from a step", [decimalsFor(0.05), decimalsFor(0.1), decimalsFor(1), decimalsFor(0.25), decimalsFor(1e-7), decimalsFor(undefined), decimalsFor(0)],
    [2, 1, 0, 2, 6, 3, 3]);
  eq("snaps to the step, counted from min", snapValue(3.66, 3.6, 12, 0.1), 3.7);
  eq("no float dust", snapValue(0.1 + 0.2, 0, 1, 0.1), 0.3);
  eq("clamped below", snapValue(-5, 3.6, 12, 0.1), 3.6);
  eq("clamped above", snapValue(50, 3.6, 12, 0.1), 12);
  eq("the max is reachable when the step does not divide the range", snapValue(1, 0, 1, 0.4), 1);
  eq("a step that starts off the round numbers keeps its grid", snapValue(3.71, 3.65, 5, 0.1), 3.75);
  eq("whole steps", snapValue(2.4, 1, 6, 1), 2);
  eq("no step: three decimals", snapValue(0.123456, 0, 1), 0.123);
  eq("a swapped range still clamps", snapValue(20, 12, 3.6, 0.1), 12);
  eq("nonsense lands on min", snapValue(NaN, 1, 6, 1), 1);
  eq("a switch in words", [showValue(spec("night"), true), showValue(spec("night"), false)], ["on", "off"]);
  eq("a choice in words", showValue(spec("type"), "service"), "Service");
  eq("a number with its step's decimals", [showValue(spec("width"), 9.8), showValue(spec("floors"), 4)], ["9.8", "4"]);
  eq("but never rounded into a different number", showValue(spec("floors"), 2.5), "2.5");
  eq("text in quotes, colours in lower case", [showValue({ ...spec("wall"), kind: "text" }, "a"), showValue(spec("wall"), "#ABCDEF")], ["“a”", "#abcdef"]);
  const tip = describePreset({ ...presets[1], values: { ...presets[1].values, ghost: 1 } }, specs);
  ok("a preset's tooltip names it, its note and its tags", tip.startsWith("Twin market\ntwo shop fronts\ntags: shop"), tip);
  ok("says what it sets, as a person reads it", tip.includes("Width 9.8") && tip.includes("Building type Shop"), tip);
  ok("and what it ignores, and why", tip.includes("ignores ghost (not a parameter of this asset)"), tip);
}

heading("is this a configurator?");
{
  eq("handles are counted as declared", [declaredHandleCount(FIXTURE), declaredHandleCount({}), declaredHandleCount(null), declaredHandleCount({ handles: "x" })], [3, 0, 0, 0]);
  ok("the fixture is one", isConfigurator(presets, 3, specs));
  ok("presets alone make one", isConfigurator(presets, 0, []));
  ok("handles alone make one", isConfigurator([], 1, []));
  ok("a choice alone makes one", isConfigurator([], 0, [spec("type")]));
  ok("an advanced setting alone makes one", isConfigurator([], 0, [spec("trim")]));
  ok("the night switch alone makes one", isConfigurator([], 0, [spec("night")]) && hasNightParam(specs));
  const plain = specsFromManifest({ params: { spacing: { value: 2.6, min: 1.5, max: 5 }, color: "#123456", lit: true } });
  ok("plain parameters do NOT: that asset keeps its editor as it was", !isConfigurator([], 0, plain));
  ok("a 'night' that is not a switch is not the night switch", !hasNightParam(specsFromManifest({ params: { night: 3 } })));
  const withName: ParamSpec[] = [...specs, { key: "manifest.name", label: "Name", kind: "text", value: "x", from: "detected", group: "manifest" }];
  eq("the configurator panel leaves out the manifest's own name", panelSpecs(withName, true).length, specs.length);
  eq("the ordinary panel keeps it, exactly as before", panelSpecs(withName, false).length, specs.length + 1);
  eq("a DECLARED parameter grouped 'manifest' is still a parameter",
    panelSpecs([{ key: "m", label: "M", kind: "number", value: 1, from: "declared", group: "manifest" }], true).length, 1);

  // Keeping a growing model in view.
  ok("a model well inside the frame stays put", !leavesFrame([[-0.5, -0.6, 0.9], [0.5, 0.7, 0.95]]));
  ok("a roof above the top margin is out, though still on screen", leavesFrame([[0, SAFE_FRAME.top + 0.05, 0.9]]));
  ok("the top keeps more room than the sides", !leavesFrame([[0.9, 0, 0.9]]) && leavesFrame([[0, 0.9, 0.9]]));
  ok("past a side is out", leavesFrame([[0.97, 0, 0.9]]) && leavesFrame([[-0.97, 0, 0.9]]));
  ok("below the bottom is out", leavesFrame([[0, -0.95, 0.9]]));
  ok("a corner behind the camera is out", leavesFrame([[0, 0, 1.3]]) && leavesFrame([[0, 0, -1.2]]));
  ok("nonsense corners are ignored, not panicked over", !leavesFrame([[NaN, 0, 0.5], [0, Infinity, 0.5]]));
  ok("no corners, nothing to keep", !leavesFrame([]));
}

// ------------------------------------------------------------------------------------------
heading("thumbnails: keys and framing");
{
  ok("a hash is stable", hashString("abc") === hashString("abc"));
  ok("and tells sources apart", hashString("const W = 6;") !== hashString("const W = 7;"));
  ok("the empty string hashes too", typeof hashString("") === "string" && hashString("").length > 0);
  eq("values do not depend on the order they were written in", stableValues({ b: 1, a: "x" }), stableValues({ a: "x", b: 1 }));
  ok("a key tells two value sets apart", thumbKey("s", { a: 1 }) !== thumbKey("s", { a: 2 }));
  ok("and two sources apart", thumbKey("s1", { a: 1 }) !== thumbKey("s2", { a: 1 }));

  const f = fitView([-0.5, -0.5, -0.5], [0.5, 0.5, 0.5], [0, 0, 1], 90, 1, 1);
  ok("a unit cube, straight on, at 90 degrees: one unit away", Math.abs(f.distance - 1) < 1e-9, String(f.distance));
  ok("aimed at its centre", f.target.every((v) => Math.abs(v) < 1e-9), JSON.stringify(f.target));
  ok("from the front", Math.abs(f.position[2] - 1) < 1e-9 && Math.abs(f.position[0]) < 1e-9);
  const wide = fitView([-0.5, -0.5, -0.5], [0.5, 0.5, 0.5], [0, 0, 1], 90, 2, 1);
  ok("a wider frame does not need to stand further back for a cube", Math.abs(wide.distance - 1) < 1e-9);
  const slab = fitView([-2, -0.5, -0.5], [2, 0.5, 0.5], [0, 0, 1], 90, 1, 1);
  ok("a wide slab in a square frame: its width decides", Math.abs(slab.distance - 2.5) < 1e-9, String(slab.distance));
  const m = fitView([-0.5, -0.5, -0.5], [0.5, 0.5, 0.5], [0, 0, 1], 90, 1, 1.25);
  ok("margin is breathing room on the distance", Math.abs(m.distance - 1.25) < 1e-9);

  // The real case: a tall building from the HERO direction. Every corner must be inside the
  // frustum and the projected box centred.
  const lo: [number, number, number] = [-3, 0, -3], hi: [number, number, number] = [3, 9.25, 3.5];
  const fov = 30, aspect = 4 / 3;
  const v = fitView(lo, hi, HERO_DIR, fov, aspect, 1.12);
  const d = (() => { const l = Math.hypot(...HERO_DIR); return HERO_DIR.map((x) => x / l); })();
  const fwd = d.map((x) => -x);
  const r0 = [fwd[1] * 0 - fwd[2] * 1, fwd[2] * 0 - fwd[0] * 0, fwd[0] * 1 - fwd[1] * 0];
  const rl = Math.hypot(r0[0], r0[1], r0[2]);
  const right = r0.map((x) => x / rl);
  const up = [right[1] * fwd[2] - right[2] * fwd[1], right[2] * fwd[0] - right[0] * fwd[2], right[0] * fwd[1] - right[1] * fwd[0]];
  const tanV = Math.tan((fov * Math.PI) / 360), tanH = tanV * aspect;
  let inside = true, lx = 9, hx = -9, ly = 9, hy = -9;
  for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) {
    const q = [x - v.target[0], y - v.target[1], z - v.target[2]];
    const depth = v.distance - (q[0] * d[0] + q[1] * d[1] + q[2] * d[2]);
    const nx = (q[0] * right[0] + q[1] * right[1] + q[2] * right[2]) / (depth * tanH);
    const ny = (q[0] * up[0] + q[1] * up[1] + q[2] * up[2]) / (depth * tanV);
    if (Math.abs(nx) > 1 + 1e-6 || Math.abs(ny) > 1 + 1e-6) inside = false;
    lx = Math.min(lx, nx); hx = Math.max(hx, nx); ly = Math.min(ly, ny); hy = Math.max(hy, ny);
  }
  ok("every corner of the building is in the picture", inside);
  ok("the building is centred in the picture", Math.abs((lx + hx) / 2) < 0.01 && Math.abs((ly + hy) / 2) < 0.01,
    [lx, hx, ly, hy].map((n) => n.toFixed(3)).join(" "));
  ok("and fills it: the tighter side is within the margin of the edge", Math.max(hx - lx, (hy - ly)) > 2 / 1.12 - 0.08,
    [(hx - lx).toFixed(3), (hy - ly).toFixed(3)].join(" "));
  const top = fitView([-1, -1, -1], [1, 1, 1], [0, 1, 0], 45, 1, 1.1);
  ok("straight down does not divide by zero", Number.isFinite(top.distance) && top.distance > 0 && top.position[1] > 1);

  // What a failed thumbnail says: the asset's own exception, not the loader's summary above it.
  const loader = new Error("`default` ran but never returned anything to show.\n(THREE, params) threw this preset is broken on purpose\n(params) threw THREE.Group is not a constructor\n\nat build (blob:x)");
  eq("a failed thumbnail says what the asset threw", thumbError(loader), "this preset is broken on purpose");
  eq("a plain error keeps its first line", thumbError(new Error("the build took longer than 10 s\nmore")), "the build took longer than 10 s");
  eq("a thrown string is still a sentence", thumbError("nothing in it to see"), "nothing in it to see");
  eq("and nothing at all is an unknown error", thumbError(undefined), "unknown error");
  ok("it is kept short", thumbError(new Error("x".repeat(900))).length === 240);
}

// ------------------------------------------------------------------------------------------
heading("DAY / NIGHT on the studio rig");
{
  const colour = (hex: number) => ({ isColor: true, hex, getHex() { return this.hex; }, setHex(h: number) { this.hex = h; return this; } });
  const light = (type: string, hex: number, intensity: number, ground?: number) => ({
    isLight: true, type, isHemisphereLight: type === "hemi", intensity, color: colour(hex),
    ...(ground !== undefined ? { groundColor: colour(ground) } : {}), userData: {} as Record<string, any>,
  });
  const rig = [light("key", 0xffffff, 2.6), light("fill", 0xbcd0ff, 0.9), light("rim", 0xffe6c0, 1.6), light("hemi", 0xa8c4ff, 0.7, 0x40332a)];
  const scene: any = {
    background: colour(0x1a1e26), environmentIntensity: 1, userData: {},
    getObjectByName: (n: string) => (n === "__lights" ? { children: [...rig, { isLight: false }] } : null),
  };
  const studio = captureStudio(scene)!;
  const day = JSON.stringify(rig.map((l: any) => [l.color.hex, l.intensity, l.groundColor?.hex]));
  eq("the studio backdrop is captured with its day colour", studio.day, 0x1a1e26);
  eq("day on a world that was never night touches nothing", applyStudioLook(scene, "day", studio), 0);
  ok("not even a note on a light", rig.every((l) => !("__studioDay" in l.userData)) && scene.background.hex === 0x1a1e26);
  eq("night sets every light of the rig", applyStudioLook(scene, "night", studio), 4);
  eq("the key becomes moonlight", [rig[0].color.hex, rig[0].intensity], [NIGHT.directional[0].color, NIGHT.directional[0].intensity]);
  eq("fill and rim follow, in order", [rig[1].intensity, rig[2].intensity], [NIGHT.directional[1].intensity, NIGHT.directional[2].intensity]);
  eq("the hemisphere gets its own night", [rig[3].color.hex, (rig[3] as any).groundColor.hex, rig[3].intensity],
    [NIGHT.hemisphere.color, NIGHT.hemisphere.ground, NIGHT.hemisphere.intensity]);
  eq("the backdrop darkens", scene.background.hex, NIGHT.background);
  eq("the environment turns down", scene.environmentIntensity, NIGHT.environment);
  applyStudioLook(scene, "night", studio);
  applyStudioLook(scene, "day", studio);
  eq("night twice, then day: the forge rig exactly as it was", JSON.stringify(rig.map((l: any) => [l.color.hex, l.intensity, l.groundColor?.hex])), day);
  eq("the backdrop back to its day colour", scene.background.hex, 0x1a1e26);
  eq("the environment back to full", scene.environmentIntensity, 1);
  eq("a world with no rig is left alone", applyStudioLook({ getObjectByName: () => null, userData: {} }, "night", null), 0);
  eq("and no scene at all is no error", applyStudioLook(null, "night", null), 0);
  eq("nothing to capture from a scene with no colour backdrop", captureStudio({ background: null }), null);
}

// ------------------------------------------------------------------------------------------
console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL " + f);
process.exit(fails.length ? 1 : 0);
