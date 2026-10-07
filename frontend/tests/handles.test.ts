// Parameter handles: an arrow on the model, bound to one number parameter, that you drag.
//
// Three things are tested here, in the order the code trusts them:
//   1. the manifest reader — every way a handle entry can be malformed is refused, and what comes
//      back is complete (defaults, the parameter's range, its label);
//   2. the maths — the face a handle sits on, the pointer ray against the handle's line, and the
//      value a drag reaches — pure functions, checked against numbers worked out by hand;
//   3. the layer itself — the class the viewport runs — on the REAL fixture building, with a real
//      three.js camera and fake pointer events: the press is taken before the editor sees it, the
//      value follows the pointer one to one, the handle is found again on the rebuilt face, Escape
//      puts the value back, and `dispose` leaves nothing listening.
//
// Run: npm run test:handles  (esbuild bundles this and node runs it; no test framework is installed)

import * as THREE from "three";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  arrowPose, closestOnAxis, decimalsFor, dragValue, endOnFade, faceAnchor, handleKey, handleText,
  handlesFromManifest, labelPlace, ndcOf, orthoPixelSize, outwardOf, ParamHandleLayer, pickHandle, pixelSize,
  readHandle, resizeCursor, slotsOf, snapValue, tangentOf, type HandleHost, type Vec3,
} from "../src/components/engine/edit/handles";
import type { ParamSpec } from "../src/components/engine/edit/kit";
import type { HandleSpec, ParamHandleCallbacks } from "../src/components/engine/edit/configurator";
// The orchestrator's fixture: the building the configurator is proved on. Its build function makes
// the model the layer is tested against, and its manifest is the one the editor will read.
// @ts-ignore -- a plain .js module with no types; esbuild bundles it
import buildFixture, { manifest as fixtureManifest } from "../../data/ab/configurator_fixture.js";

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
function near(name: string, got: number | null | undefined, want: number, tol = 1e-9) {
  ok(name, typeof got === "number" && Math.abs(got - want) <= tol, "got " + got + ", want " + want + " (tol " + tol + ")");
}
const section = (t: string) => console.log("\n" + t);

// A parameter list shaped like the fixture's, written out by hand so this file does not depend on
// the panel code another agent is changing: three numbers, and one of every other kind.
const P = (key: string, kind: ParamSpec["kind"], value: ParamSpec["value"], extra: Partial<ParamSpec> = {}): ParamSpec =>
  ({ key, label: key[0].toUpperCase() + key.slice(1), kind, value, from: "declared", ...extra });
const SPECS: ParamSpec[] = [
  P("width", "number", 6, { min: 3.6, max: 12, step: 0.1, label: "Width" }),
  P("depth", "number", 6, { min: 3.6, max: 10, step: 0.1, label: "Depth" }),
  P("floors", "number", 3, { min: 1, max: 6, step: 1, label: "Floors" }),
  P("type", "choice", "shop", { options: ["shop", "house", "service"], label: "Building type" }),
  P("rearStair", "bool", false, { label: "Rear service stair" }),
  P("wall", "color", "#d9c9a3", { label: "Wall colour" }),
  P("title", "text", "corner shop"),
  P("bay", "number", 1.2, { min: 0.8, max: 2, step: 0.05, label: "Bay width", advanced: true }),
];
const one = (h: unknown, specs = SPECS) => handlesFromManifest({ handles: [h] }, specs);

// =========================================================================================
section("The manifest: what a handle entry must be");

eq("the fixture's three handles, complete: defaults, the parameter's range, step and label",
  handlesFromManifest(fixtureManifest, SPECS), [
    { param: "width", axis: "x", side: "max", scale: 2, snap: 0.1, min: 3.6, max: 12, label: "Width" },
    { param: "depth", axis: "z", side: "max", scale: 2, snap: 0.1, min: 3.6, max: 10, label: "Depth" },
    { param: "floors", axis: "y", side: "max", scale: 1 / 3, snap: 1, min: 1, max: 6, label: "Floors" },
  ]);

for (const [name, m] of [["null", null], ["undefined", undefined], ["a number", 7], ["a string", "handles"],
  ["an array", [{ param: "width", axis: "x" }]], ["no handles key", { params: {} }],
  ["handles not a list", { handles: { param: "width", axis: "x" } }], ["handles null", { handles: null }]] as const) {
  eq("a manifest that is " + name + " has no handles", handlesFromManifest(m, SPECS), []);
}
eq("an empty list is an empty list", handlesFromManifest({ handles: [] }, SPECS), []);
eq("no parameters at all: nothing can be bound", handlesFromManifest(fixtureManifest, []), []);
eq("a parameter list that is not a list does not throw", handlesFromManifest(fixtureManifest, null as any), []);

for (const [name, raw] of [["null", null], ["a number", 3], ["a string", "width"], ["a list", ["width", "x"]],
  ["true", true]] as const) {
  eq("an entry that is " + name + " is skipped", one(raw), []);
}

// param
eq("no param", one({ axis: "x" }), []);
eq("param not a string", one({ param: 7, axis: "x" }), []);
eq("param empty", one({ param: "", axis: "x" }), []);
eq("param names nothing declared", one({ param: "height", axis: "x" }), []);
eq("param is matched exactly, not trimmed", one({ param: " width", axis: "x" }), []);
for (const k of ["type", "rearStair", "wall", "title"]) {
  eq("a " + SPECS.find((s) => s.key === k)!.kind + " parameter cannot have a handle", one({ param: k, axis: "x" }), []);
}
eq("an advanced number parameter can", one({ param: "bay", axis: "x" }).length, 1);

// axis
for (const a of [undefined, null, "X", "w", "", 0, "xy"]) {
  eq("axis " + JSON.stringify(a) + " is refused", one({ param: "width", axis: a }), []);
}
for (const a of ["x", "y", "z"]) eq("axis " + a + " is kept", one({ param: "width", axis: a })[0]?.axis, a);

// side
eq("side defaults to max", one({ param: "width", axis: "x" })[0].side, "max");
eq("a null side is an absent side", one({ param: "width", axis: "x", side: null })[0].side, "max");
eq("side min is kept", one({ param: "width", axis: "x", side: "min" })[0].side, "min");
for (const s of ["top", "MAX", "", 1, true]) eq("side " + JSON.stringify(s) + " is refused", one({ param: "width", axis: "x", side: s }), []);

// scale
eq("scale defaults to 1", one({ param: "width", axis: "x" })[0].scale, 1);
eq("a null scale is an absent scale", one({ param: "width", axis: "x", scale: null })[0].scale, 1);
eq("a negative scale is allowed — it is finite and not zero", one({ param: "width", axis: "x", scale: -2 })[0].scale, -2);
eq("a fraction is kept exactly", one({ param: "floors", axis: "y", scale: 1 / 3 })[0].scale, 1 / 3);
for (const s of [0, -0, NaN, Infinity, -Infinity, "2", true, {}]) {
  eq("scale " + String(s) + " is refused", one({ param: "width", axis: "x", scale: s }), []);
}

// snap
// An unstated snap rounds like the parameter's own slider, so a drag never writes 10.0928 into
// the file; an explicit 0 still means "no rounding" — the author said so.
eq("an unstated snap takes the parameter's step", one({ param: "width", axis: "x" })[0].snap, 0.1);
eq("an explicit snap 0 is kept, and means no rounding", one({ param: "width", axis: "x", snap: 0 })[0].snap, 0);
eq("a parameter with no step gives an unstated snap of 0",
  one({ param: "width", axis: "x" }, SPECS.map((sp) => (sp.key === "width" ? { ...sp, step: undefined } : sp)))[0].snap, 0);
eq("snap 0.5 is kept", one({ param: "width", axis: "x", snap: 0.5 })[0].snap, 0.5);
for (const s of [-1, -0.001, NaN, Infinity, "1", false]) {
  eq("snap " + String(s) + " is refused", one({ param: "width", axis: "x", snap: s }), []);
}

// min / max
{
  const h = one({ param: "width", axis: "x" })[0];
  eq("an unstated clamp comes from the parameter", [h.min, h.max], [3.6, 12]);
  const g = one({ param: "width", axis: "x", min: 4, max: 9 })[0];
  eq("a stated clamp is the handle's own", [g.min, g.max], [4, 9]);
  const lo = one({ param: "width", axis: "x", min: 5 })[0];
  eq("a stated min keeps the parameter's max", [lo.min, lo.max], [5, 12]);
  eq("min equal to max is a pinned value, not an error", one({ param: "width", axis: "x", min: 6, max: 6 })[0].max, 6);
  eq("min above max is refused", one({ param: "width", axis: "x", min: 9, max: 4 }), []);
  for (const v of [NaN, Infinity, "3", {}]) {
    eq("min " + String(v) + " is refused", one({ param: "width", axis: "x", min: v }), []);
    eq("max " + String(v) + " is refused", one({ param: "width", axis: "x", max: v }), []);
  }
  const over = one({ param: "width", axis: "x", min: 15 })[0];
  eq("a stated min above the parameter's max drops that max, never the stated bound", [over.min, over.max], [15, undefined]);
  const under = one({ param: "width", axis: "x", max: 2 })[0];
  eq("a stated max under the parameter's min drops that min", [under.min, under.max], [undefined, 2]);
  const bare = handlesFromManifest({ handles: [{ param: "n", axis: "x" }] }, [P("n", "number", 1)]);
  eq("a parameter with no range leaves the handle unclamped", [bare[0].min, bare[0].max], [undefined, undefined]);
  const bad = handlesFromManifest({ handles: [{ param: "n", axis: "x" }] }, [P("n", "number", 1, { min: 9, max: 2 })]);
  eq("a parameter whose own range is inverted clamps nothing", [bad[0].min, bad[0].max], [undefined, undefined]);
}

// part / label
eq("part is kept exactly", one({ param: "width", axis: "x", part: "roof" })[0].part, "roof");
eq("part is not trimmed: names are matched exactly", one({ param: "width", axis: "x", part: " roof" })[0].part, " roof");
eq("an empty part means no part", "part" in one({ param: "width", axis: "x", part: "  " })[0], false);
eq("a part that is not a string is refused", one({ param: "width", axis: "x", part: 3 }), []);
eq("label is kept, trimmed", one({ param: "width", axis: "x", label: "  Frontage " })[0].label, "Frontage");
eq("no label: the parameter's", one({ param: "width", axis: "x" })[0].label, "Width");
eq("a blank label: the parameter's", one({ param: "width", axis: "x", label: " " })[0].label, "Width");
eq("a label that is not a string is refused", one({ param: "width", axis: "x", label: 5 }), []);
eq("a parameter with no label: its key",
  handlesFromManifest({ handles: [{ param: "n", axis: "x" }] }, [{ ...P("n", "number", 1), label: "" }])[0].label, "n");

// dedupe
{
  const got = handlesFromManifest({
    handles: [
      { param: "width", axis: "x", side: "max", scale: 2 },
      { param: "width", axis: "x", scale: 5 },                  // same param, axis, side (default max)
      { param: "width", axis: "x", side: "min", scale: 2 },    // another face
      { param: "width", axis: "z" },                            // another axis
    ],
  }, SPECS);
  eq("dedupe by param + axis + side keeps the first", got.map((h) => [h.axis, h.side, h.scale]),
    [["x", "max", 2], ["x", "min", 2], ["z", "max", 1]]);
  const late = handlesFromManifest({
    handles: [{ param: "width", axis: "x", scale: 0 }, { param: "width", axis: "x", scale: 3 }],
  }, SPECS);
  eq("a malformed entry does not shadow a good one after it", late.map((h) => h.scale), [3]);
  const unbound = handlesFromManifest({
    handles: [{ param: "nope", axis: "x" }, { param: "width", axis: "x", scale: 4 }],
  }, SPECS);
  eq("an unbound entry does not count toward the dedupe", unbound.map((h) => h.scale), [4]);
}
{
  const trap = { get param() { throw new Error("no"); }, axis: "x" };
  eq("an entry whose getter throws is skipped, not thrown", one(trap), []);
  const dup = handlesFromManifest({ handles: [{ param: "w", axis: "x" }] },
    [P("w", "number", 1, { min: 0, max: 5, label: "First" }), P("w", "number", 1, { min: 0, max: 50, label: "Second" })]);
  eq("of two specs with one key, the first is the one bound", [dup[0].max, dup[0].label], [5, "First"]);
  eq("extra keys in an entry are ignored", Object.keys(one({ param: "width", axis: "x", colour: "red" })[0]).includes("colour"), false);
}

// readHandle: the same rules, for a list handed to the viewport directly
eq("readHandle fills the defaults", readHandle({ param: "width", axis: "x" }),
  { param: "width", axis: "x", side: "max", scale: 1, snap: 0 });
eq("readHandle knows nothing about parameters, so it adds no range and no label",
  readHandle({ param: "anything", axis: "y", side: "min", scale: 2, snap: 1, min: 0, max: 3, part: "p", label: "L" }),
  { param: "anything", axis: "y", side: "min", scale: 2, snap: 1, min: 0, max: 3, part: "p", label: "L" });
eq("readHandle refuses what the manifest reader refuses", readHandle({ param: "width", axis: "q" }), null);
eq("handleKey is param|axis|side", handleKey({ param: "width", axis: "x", side: "min" }), "width|x|min");

// =========================================================================================
section("The maths: the face, the line, the value");

eq("outward from the max face is +axis", outwardOf("x", "max"), [1, 0, 0]);
eq("outward from the min face is -axis", outwardOf("x", "min"), [-1, 0, 0]);
eq("y max", outwardOf("y", "max"), [0, 1, 0]);
eq("z min", outwardOf("z", "min"), [0, 0, -1]);
eq("two handles on a wall spread up it", tangentOf("x"), [0, 1, 0]);
eq("...on the front too", tangentOf("z"), [0, 1, 0]);
eq("...and across a roof", tangentOf("y"), [1, 0, 0]);

{
  const min: Vec3 = [-3, 0, -3], max: Vec3 = [3, 9, 3];
  eq("the middle of the right-hand face", faceAnchor(min, max, "x", "max"), [3, 4.5, 0]);
  eq("the left-hand face, half a metre out", faceAnchor(min, max, "x", "min", 0.5), [-3.5, 4.5, 0]);
  eq("the roof", faceAnchor(min, max, "y", "max"), [0, 9, 0]);
  eq("the floor, pushed down", faceAnchor(min, max, "y", "min", 0.25), [0, -0.25, 0]);
  eq("the back, a metre out", faceAnchor(min, max, "z", "min", 1), [0, 4.5, -4]);
  eq("an off-centre box keeps its own middle", faceAnchor([1, 2, 3], [5, 4, 11], "z", "max", 0.1), [3, 3, 11.1]);
}

eq("handles on three faces get a slot each to themselves",
  slotsOf([{ axis: "x", side: "max" }, { axis: "z", side: "max" }, { axis: "y", side: "max" }]),
  [{ slot: 0, of: 1 }, { slot: 0, of: 1 }, { slot: 0, of: 1 }]);
eq("two on one face share it", slotsOf([{ axis: "x", side: "max" }, { axis: "y", side: "max" }, { axis: "x", side: "max" }]),
  [{ slot: 0, of: 2 }, { slot: 0, of: 1 }, { slot: 1, of: 2 }]);
eq("the same face of different parts is not shared",
  slotsOf([{ axis: "x", side: "max", part: "roof" }, { axis: "x", side: "max" }]), [{ slot: 0, of: 1 }, { slot: 0, of: 1 }]);
eq("nor the opposite face", slotsOf([{ axis: "x", side: "max" }, { axis: "x", side: "min" }]), [{ slot: 0, of: 1 }, { slot: 0, of: 1 }]);

// closest point between the handle's line and the pointer ray
near("a ray straight down onto the x axis meets it at x = 2", closestOnAxis([0, 0, 0], [1, 0, 0], [2, 0, 5], [0, 0, -1]), 2);
near("metres, whatever the length of the direction", closestOnAxis([0, 0, 0], [2, 0, 0], [2, 0, 5], [0, 0, -1]), 2);
near("measured from the line's own origin", closestOnAxis([5, 0, 0], [1, 0, 0], [2, 0, 5], [0, 0, -1]), -3);
near("a skew pair: the y line through (1,0,0), a ray along -z at y = 3", closestOnAxis([1, 0, 0], [0, 1, 0], [0, 3, 10], [0, 0, -1]), 3);
near("a ray that is not unit length", closestOnAxis([0, 0, 0], [1, 0, 0], [2, 0, 5], [0, 0, -7]), 2);
eq("parallel: the pointer says nothing about the line", closestOnAxis([0, 0, 0], [1, 0, 0], [0, 1, 5], [1, 0, 0]), null);
eq("anti-parallel likewise", closestOnAxis([0, 0, 0], [1, 0, 0], [0, 1, 5], [-1, 0, 0]), null);
{
  const t = 0.01;   // sin of about 0.57 degrees: inside the default 0.02
  const d = [Math.cos(t), Math.sin(t), 0] as Vec3;
  eq("nearly parallel is refused too", closestOnAxis([0, 0, 0], [1, 0, 0], [0, 0, 3], d), null);
  ok("but a threshold of 0.005 takes it", closestOnAxis([0, 0, 0], [1, 0, 0], [0, 0, 3], d, 0.005) !== null);
  const u = 0.05;
  ok("three degrees off is well defined", closestOnAxis([0, 0, 0], [1, 0, 0], [0, 0, 3], [Math.cos(u), 0, -Math.sin(u)]) !== null);
}
eq("the nearest point behind the eye is refused", closestOnAxis([0, 0, 0], [1, 0, 0], [2, 0, 5], [0, 0, 1]), null);
eq("a zero direction is refused", closestOnAxis([0, 0, 0], [0, 0, 0], [2, 0, 5], [0, 0, -1]), null);
eq("a zero ray is refused", closestOnAxis([0, 0, 0], [1, 0, 0], [2, 0, 5], [0, 0, 0]), null);
{
  // A property, not an example: for any line and any ray that are not parallel, the segment
  // between the two closest points is perpendicular to the line. Seeded, so a failure repeats.
  let seed = 12345;
  const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648 * 2 - 1; };
  let worst = 0, checked = 0;
  for (let n = 0; n < 400; n++) {
    const o: Vec3 = [rnd() * 10, rnd() * 10, rnd() * 10], d: Vec3 = [rnd(), rnd(), rnd()];
    const ro: Vec3 = [rnd() * 20, rnd() * 20, rnd() * 20], rd: Vec3 = [rnd(), rnd(), rnd()];
    const s = closestOnAxis(o, d, ro, rd);
    if (s === null) continue;
    const L = Math.hypot(d[0], d[1], d[2]);
    const P3: Vec3 = [o[0] + (d[0] / L) * s, o[1] + (d[1] / L) * s, o[2] + (d[2] / L) * s];
    const rr = rd[0] * rd[0] + rd[1] * rd[1] + rd[2] * rd[2];
    const t = ((P3[0] - ro[0]) * rd[0] + (P3[1] - ro[1]) * rd[1] + (P3[2] - ro[2]) * rd[2]) / rr;
    const Q: Vec3 = [ro[0] + rd[0] * t, ro[1] + rd[1] * t, ro[2] + rd[2] * t];
    const gap = [P3[0] - Q[0], P3[1] - Q[1], P3[2] - Q[2]];
    const len = Math.hypot(gap[0], gap[1], gap[2]) || 1;
    worst = Math.max(worst, Math.abs((gap[0] * d[0] + gap[1] * d[1] + gap[2] * d[2]) / L / len));
    ok("the ray point for a returned answer is in front of the eye (" + n + ")", t >= -1e-9);
    checked++;
  }
  ok("400 random pairs: the joining segment is perpendicular to the line", worst < 1e-6, "worst cosine " + worst);
  ok("...and most of them were answerable", checked > 150, checked + " checked");
}

// snapping
near("snap 0 leaves the value alone", snapValue(6.37, 0), 6.37);
eq("snap 1 rounds up past the half", snapValue(2.6, 1), 3);
eq("...and down before it", snapValue(2.4, 1), 2);
eq("three tenths is three tenths, not 0.30000000000000004", snapValue(0.1 * 3, 0.1), 0.3);
eq("a half step", snapValue(7.25, 0.5), 7.5);
eq("never -0", Object.is(snapValue(-0.4, 1), 0), true);
eq("a negative snap is no snap", snapValue(6.37, -1), 6.37);
ok("NaN stays NaN rather than becoming a number", Number.isNaN(snapValue(NaN, 1)));

// the value
near("width: 6 m, dragged 0.2 m out at scale 2", dragValue(6, 0.2, { scale: 2, snap: 0 }), 6.4);
near("dragged back in", dragValue(6, -0.5, { scale: 2, snap: 0 }), 5);
eq("floors: 3, dragged 3.2 m up at a third of a floor a metre, whole floors", dragValue(3, 3.2, { scale: 1 / 3, snap: 1 }), 4);
eq("1.4 m is not yet half a floor", dragValue(3, 1.4, { scale: 1 / 3, snap: 1 }), 3);
eq("1.6 m is", dragValue(3, 1.6, { scale: 1 / 3, snap: 1 }), 4);
eq("the handle's own max clamps", dragValue(11, 1, { scale: 2, snap: 0, max: 12 }), 12);
eq("the handle's own min clamps", dragValue(4, -2, { scale: 2, snap: 0, min: 3.6 }), 3.6);
eq("with no max of its own, the parameter's", dragValue(9, 1, { scale: 2, snap: 0 }, { max: 10 }), 10);
eq("the handle's bound wins over the parameter's", dragValue(9, 1, { scale: 2, snap: 0, max: 8 }, { max: 10 }), 8);
near("no bounds anywhere: unclamped", dragValue(9, 100, { scale: 2, snap: 0 }), 209);
near("an inverted pair clamps nothing rather than pinning to one end", dragValue(5, 0, { scale: 1, snap: 0, min: 9, max: 2 }), 5);
eq("snap THEN clamp: 12.6 snaps to 15, and the clamp brings it back to 12", dragValue(12.6, 0, { scale: 1, snap: 5, max: 12 }), 12);
eq("...so a clamp is never undone by rounding after it", dragValue(11.9, 0, { scale: 1, snap: 5, max: 12 }), 10);
near("a scale of 0 that slipped past the reader counts as 1", dragValue(6, 0.5, { scale: 0, snap: 0 }), 6.5);
eq("never -0", Object.is(dragValue(0.4, -0.4, { scale: 1, snap: 0 }), 0), true);
near("a negative scale drives the value down as the handle comes out", dragValue(2, 0.5, { scale: -2, snap: 0 }), 1);

// screen size
near("perspective: 10 m away at 90 degrees, 1000 px tall", pixelSize(10, 90, 1000), 0.02, 1e-12);
near("the editor's 38 degrees at 5 m in 900 px", pixelSize(5, 38, 900), (2 * 5 * Math.tan((19 * Math.PI) / 180)) / 900, 1e-12);
eq("behind the eye is nothing", pixelSize(-3, 38, 900), 0);
near("a zero-height viewport divides by one, not zero", pixelSize(1, 90, 0), 2, 1e-12);
near("orthographic: a 10 m frustum in 1000 px", orthoPixelSize(5, -5, 1, 1000), 0.01, 1e-12);
near("zoomed in two times", orthoPixelSize(5, -5, 2, 1000), 0.005, 1e-12);
near("a zoom of 0 is a zoom of 1", orthoPixelSize(5, -5, 0, 1000), 0.01, 1e-12);

// end-on
eq("across the line of sight: fully drawn", endOnFade(0), 1);
eq("20 degrees off it: still fully drawn", endOnFade(0.94), 1);
eq("8 degrees off it: gone", endOnFade(0.99), 0);
eq("straight down it: gone", endOnFade(1), 0);
eq("either way along it", endOnFade(-1), 0);
near("half way through the fade", endOnFade(0.965), 0.5, 1e-9);

// the cursor
eq("sideways", resizeCursor(10, 0), "ew-resize");
eq("sideways, pointing left", resizeCursor(-10, 0), "ew-resize");
eq("up and down", resizeCursor(0, 10), "ns-resize");
eq("up and down, pointing up", resizeCursor(0, -10), "ns-resize");
eq("right and down: top-left to bottom-right", resizeCursor(10, 10), "nwse-resize");
eq("left and up is the same diagonal", resizeCursor(-10, -10), "nwse-resize");
eq("right and up: the other diagonal", resizeCursor(10, -10), "nesw-resize");
eq("left and down likewise", resizeCursor(-10, 10), "nesw-resize");
eq("20 degrees is still sideways", resizeCursor(Math.cos(0.349), Math.sin(0.349)), "ew-resize");
eq("25 degrees is the diagonal", resizeCursor(Math.cos(0.436), Math.sin(0.436)), "nwse-resize");
eq("no direction at all", resizeCursor(0, 0), "move");

// the flat arrow in the pill
{
  const a = arrowPose(10, 0, 10);
  near("an axis running right on screen: no turn", a.angle, 0, 1e-12);
  near("...full length", a.stretch, 1, 1e-12);
  near("running up the screen (y down in pixels): a quarter turn", arrowPose(0, -10, 10).angle, Math.PI / 2, 1e-12);
  near("down and left: three eighths the other way", arrowPose(-7, 7, 10).angle, -3 * Math.PI / 4, 1e-12);
  near("an axis half hidden by perspective looks shorter", arrowPose(3, 4, 10).stretch, 0.55 + 0.45 * 0.5, 1e-12);
  near("but never shorter than 0.55", arrowPose(0, 0, 10).stretch, 0.55, 1e-12);
  near("nor longer than itself", arrowPose(25, 0, 10).stretch, 1, 1e-12);
  eq("no direction at all: no turn", arrowPose(0, 0, 10).angle, 0);
}

// picking
{
  const pts = [{ x: 100, y: 100, ok: true }, { x: 130, y: 100, ok: true }, { x: 112, y: 100, ok: false }];
  eq("the nearest within reach", pickHandle(pts, 112, 100, 16), 0);
  eq("the other one when it is nearer", pickHandle(pts, 119, 100, 16), 1);
  eq("an unusable handle is never picked, however near", pickHandle([pts[2]], 112, 100, 16), -1);
  eq("out of reach of everything", pickHandle(pts, 200, 200, 16), -1);
  eq("exactly at the radius counts", pickHandle(pts, 100, 116, 16), 0);
  eq("nothing to pick", pickHandle([], 0, 0, 16), -1);
}

// text
eq("free drag: two decimals", decimalsFor(0), 2);
eq("whole steps: none", decimalsFor(1), 0);
eq("halves: one", decimalsFor(0.5), 1);
eq("quarters: two", decimalsFor(0.25), 2);
eq("five hundredths: two", decimalsFor(0.05), 2);
eq("thousandths: three", decimalsFor(0.001), 3);
eq("a third has no exact decimals: two", decimalsFor(1 / 3), 2);
eq("steps of two: none", decimalsFor(2), 0);
eq("the label while dragging width", handleText("Width", 6.4, 0), "Width 6.40");
eq("...and floors", handleText("Floors", 3, 1), "Floors 3");
eq("no label: just the number", handleText("", 2, 0), "2.00");
eq("a value that is not a number says so", handleText("Width", NaN, 0), "Width —");

eq("the label sits above a handle in the middle of the view", labelPlace(400, 300, 23, 80, 800), { x: 400, y: 277, below: false });
eq("...and below one that has risen to the top, where the view bar is", labelPlace(400, 60, 23, 80, 800), { x: 400, y: 83, below: true });
eq("the switch is at 64 px of room", [labelPlace(400, 87, 23, 80, 800).below, labelPlace(400, 86.9, 23, 80, 800).below], [false, true]);
eq("pulled in from the right edge so it stays whole", labelPlace(790, 300, 23, 80, 800).x, 756);
eq("...and from the left", labelPlace(3, 300, 23, 80, 800).x, 44);
eq("a viewport narrower than the label leaves it where it is", labelPlace(30, 300, 23, 80, 60).x, 30);
eq("the top-left corner", ndcOf(0, 0, 200, 100), { x: -1, y: 1 });
eq("the bottom-right corner", ndcOf(200, 100, 200, 100), { x: 1, y: -1 });
eq("the middle", ndcOf(100, 50, 200, 100), { x: 0, y: 0 });

// The whole chain, by hand: a camera, the ray through the point 0.3 m outside the right-hand face,
// and the value that ray makes. The face moves half of a width change, so it lands under the ray.
{
  const eye: Vec3 = [8, 6, 10];
  const anchor: Vec3 = [3.15, 4.625, 0.38];
  const through = (p: Vec3) => [p[0] - eye[0], p[1] - eye[1], p[2] - eye[2]] as Vec3;
  const dir = outwardOf("x", "max");
  const s0 = closestOnAxis(anchor, dir, eye, through(anchor))!;
  const s1 = closestOnAxis(anchor, dir, eye, through([anchor[0] + 0.3, anchor[1], anchor[2]]))!;
  near("the press on the handle is at zero along its line", s0, 0, 1e-9);
  near("the ray through a point 0.3 m out is 0.3 m along", s1 - s0, 0.3, 1e-9);
  near("so width 6 becomes 6.6, and the face at +3.3 is under the pointer", dragValue(6, s1 - s0, { scale: 2, snap: 0 }), 6.6, 1e-9);
  const minA: Vec3 = [-3.15, 4.625, 0.38];
  const m = closestOnAxis(minA, outwardOf("x", "min"), eye, through([minA[0] - 0.4, minA[1], minA[2]]))!
    - closestOnAxis(minA, outwardOf("x", "min"), eye, through(minA))!;
  near("the MIN face pulled 0.4 m outward is +0.4, not -0.4: pulling out grows the value", m, 0.4, 1e-9);
  const top: Vec3 = [0, 9.25, 0.38];
  const up = closestOnAxis(top, outwardOf("y", "max"), eye, through([0, 12.45, 0.38]))!
    - closestOnAxis(top, outwardOf("y", "max"), eye, through(top))!;
  eq("the roof pulled 3.2 m up is one more floor", dragValue(3, up, { scale: 1 / 3, snap: 1, min: 1, max: 6 }), 4);
  // An orthographic camera: every ray is parallel, and only the pointer's position matters.
  near("orthographic rays are measured the same way",
    closestOnAxis(anchor, dir, [3.65, 4.625, 100], [0, 0, -1])! - closestOnAxis(anchor, dir, [3.15, 4.625, 100], [0, 0, -1])!, 0.5, 1e-9);
}

// =========================================================================================
section("The layer, on the real fixture, with a real camera and fake pointer events");

// ---- a browser, near enough: a canvas that keeps its listeners, a window that keeps its keys
type Fn = (e: any) => void;
const winKeys: Array<{ type: string; fn: Fn; capture: boolean }> = [];
(globalThis as any).window = {
  addEventListener: (type: string, fn: Fn, capture?: any) => winKeys.push({ type, fn, capture: capture === true || !!capture?.capture }),
  removeEventListener: (type: string, fn: Fn) => {
    const i = winKeys.findIndex((l) => l.type === type && l.fn === fn);
    if (i >= 0) winKeys.splice(i, 1);
  },
};
const made: any[] = [];
(globalThis as any).document = {
  createElement: (tag: string) => {
    const el: any = { tag, style: {}, dataset: {}, textContent: "", removed: false, remove() { this.removed = true; } };
    made.push(el);
    return el;
  },
};
const RECT = { left: 10, top: 20, width: 800, height: 600 };
function fakeCanvas() {
  const listeners = new Map<string, Set<Fn>>();
  const kids: any[] = [];
  const c: any = {
    style: { cursor: "" }, listeners, kids, focused: 0, captured: new Set<number>(),
    parentElement: { appendChild: (el: any) => { kids.push(el); return el; } },
    addEventListener(type: string, fn: Fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type)!.add(fn); },
    removeEventListener(type: string, fn: Fn) { listeners.get(type)?.delete(fn); },
    getBoundingClientRect: () => ({ ...RECT }),
    closest: (sel: string) => (sel === "[tabindex]" ? { focus: () => { c.focused++; } } : null),
    setPointerCapture: (id: number) => { c.captured.add(id); },
    hasPointerCapture: (id: number) => c.captured.has(id),
    releasePointerCapture: (id: number) => { c.captured.delete(id); },
  };
  return c;
}
const listening = (c: any) => [...c.listeners.values()].reduce((n: number, s: Set<Fn>) => n + s.size, 0);
function pev(type: string, at: { x: number; y: number }, o: Record<string, unknown> = {}) {
  return {
    type, clientX: at.x, clientY: at.y, button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 1,
    ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, stopped: false, prevented: false,
    stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.stopped = true; },
    preventDefault() { this.prevented = true; }, ...o,
  } as any;
}
function fire(c: any, ev: any) { for (const fn of [...(c.listeners.get(ev.type) || [])]) fn(ev); return ev; }
function press(k: string) {
  const ev: any = {
    key: k, stopped: false, prevented: false,
    stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.stopped = true; },
    preventDefault() { this.prevented = true; },
  };
  for (const l of [...winKeys]) if (l.type === "keydown") l.fn(ev);
  return ev;
}

// ---- the scene: the fixture building, a three-quarter camera, the editor's 38 degrees
const W = 800, H = 600;
const subject = new THREE.Group();
const params: Record<string, any> = { width: 6, depth: 6, floors: 3, type: "shop" };
function rebuild() {
  for (const c of subject.children.slice()) subject.remove(c);
  subject.add(buildFixture(THREE, params));
  subject.updateMatrixWorld(true);
}
rebuild();
const persp = new THREE.PerspectiveCamera(38, W / H, 0.05, 2000);
function aim(cam: any, pos: Vec3, at: Vec3 = [0, 4.6, 0]) {
  cam.position.set(pos[0], pos[1], pos[2]);
  cam.up.set(0, 1, 0);
  cam.lookAt(at[0], at[1], at[2]);
  cam.updateMatrixWorld(true);
}
aim(persp, [14, 10, 18]);
let cam: any = persp;
let mode = "object", busy = false;
const gizmoScene = new THREE.Scene();
const canvas = fakeCanvas();
const host: HandleHost = {
  canvas, scene: gizmoScene,
  camera: () => cam, size: () => ({ w: W, h: H }), root: () => subject,
  find: (name: string) => subject.getObjectByName(name) || null,
  live: () => mode === "object", busy: () => busy,
};
const sets: Array<[string, number]> = [];
const commits: string[] = [];
const cbFor = (tag: string, log = sets): ParamHandleCallbacks => ({
  get: (k) => Number(params[k]),
  set: (k, v) => { log.push([k, v]); params[k] = v; void tag; },
  commit: (k) => { commits.push(k); },
});
const handles = handlesFromManifest(fixtureManifest, SPECS);
const clientOf = (p: Vec3) => {
  const v = new THREE.Vector3(p[0], p[1], p[2]).project(cam);
  return { x: RECT.left + ((v.x + 1) / 2) * W, y: RECT.top + ((1 - v.y) / 2) * H };
};
const worldBox = () => new THREE.Box3().setFromObject(subject);

ok("before any handles: nothing listens on the canvas", listening(canvas) === 0);
const layer = new ParamHandleLayer(THREE, host);
ok("a layer with no handles set listens to nothing yet", listening(canvas) === 0);
ok("...and draws nothing", layer.info().length === 0);
layer.set(handles, cbFor("a"));
ok("with handles: six pointer listeners on the canvas, none anywhere else", listening(canvas) === 6, String(listening(canvas)));
eq("which ones", [...canvas.listeners.keys()].sort(),
  ["lostpointercapture", "pointercancel", "pointerdown", "pointerleave", "pointermove", "pointerup"]);
ok("no key listener until a drag starts", winKeys.length === 0);
ok("the handles live in the gizmo's scene", gizmoScene.children.some((o: any) => o.name === "__paramHandles"));
ok("they are placed the moment they are set, before any frame", layer.info().length === 3 && layer.info().every((h) => h.visible));
layer.update();
const items = () => (layer as any).items as any[];
{
  const info = layer.info();
  eq("three handles, in manifest order", info.map((h) => h.param), ["width", "depth", "floors"]);
  ok("all three drawn and takeable from a three-quarter view", info.every((h) => h.visible), JSON.stringify(info.map((h) => h.visible)));
  const b = worldBox();
  const wa = items()[0].at as Vec3, da = items()[1].at as Vec3, fa = items()[2].at as Vec3;
  near("width sits at the middle of the right-hand face, in height", wa[1], (b.min.y + b.max.y) / 2, 1e-9);
  near("...and in depth", wa[2], (b.min.z + b.max.z) / 2, 1e-9);
  ok("...a little outside it", wa[0] > b.max.x && wa[0] - b.max.x < 0.25, "x " + wa[0] + " face " + b.max.x);
  ok("depth sits just outside the front face", da[2] > b.max.z && da[2] - b.max.z < 0.25, "z " + da[2] + " face " + b.max.z);
  ok("floors sits just above the roof", fa[1] > b.max.y && fa[1] - b.max.y < 0.25, "y " + fa[1] + " roof " + b.max.y);
  near("floors is in the middle of the roof", fa[0], (b.min.x + b.max.x) / 2, 1e-9);
  const s = clientOf(wa);
  near("info() gives the page position the harness clicks", layer.info()[0].clientX, s.x, 0.01);
  near("...both ways", layer.info()[0].clientY, s.y, 0.01);
}

// ---- the arrow points the way the handle drags, on screen
{
  for (const [i, name] of [[0, "width"], [1, "depth"], [2, "floors"]] as const) {
    const it = items()[i];
    const p = it.group.position.clone();
    const along = new THREE.Vector3(1, 0, 0).applyQuaternion(it.arrow.quaternion).multiplyScalar(it.group.scale.x * 8);
    const s0 = p.clone().project(cam), s1 = p.clone().add(along).project(cam);
    const gx = ((s1.x - s0.x) / 2) * W, gy = -((s1.y - s0.y) / 2) * H;       // the arrow, in pixels, y down
    const cross = (gx * it.screen.dy - gy * it.screen.dx) / (Math.hypot(gx, gy) * Math.hypot(it.screen.dx, it.screen.dy));
    ok("the " + name + " arrow lies along its axis on screen", Math.abs(cross) < 1e-3, "sin " + cross.toFixed(5));
    ok("...and in the pill's plane, facing the eye",
      Math.abs(new THREE.Vector3(0, 0, 1).applyQuaternion(it.arrow.quaternion).dot(new THREE.Vector3(0, 0, 1).applyQuaternion(cam.quaternion)) - 1) < 1e-9);
  }
}

// ---- constant size on screen
function discPixels(it: any) {
  const p = it.group.position.clone();
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(cam.quaternion);
  const q = p.clone().add(right.multiplyScalar(12 * it.group.scale.x));
  const a = p.clone().project(cam), c = q.clone().project(cam);
  return Math.hypot(((c.x - a.x) / 2) * W, ((c.y - a.y) / 2) * H);
}
{
  const r0 = discPixels(items()[0]);
  near("the pill is 12 px in radius on screen", r0, 12, 0.35);
  aim(persp, [28, 20, 36]);           // twice as far away
  layer.update();
  near("still 12 px twice as far away", discPixels(items()[0]), 12, 0.35);
  aim(persp, [5.6, 6.6, 7.2]);          // leaning in
  layer.update();
  near("still 12 px leaning in", discPixels(items()[0]), 12, 0.35);
  aim(persp, [14, 10, 18]);
  layer.update();
}

// ---- hover
{
  const w = layer.info()[0];
  const move = fire(canvas, pev("pointermove", { x: w.clientX + 3, y: w.clientY - 2 }, { buttons: 0 }));
  ok("hovering a handle does not keep the move from the editor", !move.stopped);
  ok("it lights the handle", layer.info()[0].hovered);
  ok("and shows a resize cursor", /resize$/.test(canvas.style.cursor), canvas.style.cursor);
  const label = canvas.kids.find((k: any) => k.dataset && "paramHandleLabel" in k.dataset);
  layer.update();
  // Decimals follow the snap (0.1 here), so the label reads the way the slider does.
  ok("the tooltip names the parameter and its value", label && label.textContent === "Width 6.0", label?.textContent);
  ok("the tooltip never takes the pointer", label && /pointer-events:none/.test(label.style.cssText));
  fire(canvas, pev("pointermove", { x: w.clientX + 200, y: w.clientY + 200 }, { buttons: 0 }));
  ok("moving off lets go of it", !layer.info()[0].hovered && canvas.style.cursor === "");
  ok("and hides the tooltip", label.style.display === "none");
  fire(canvas, pev("pointermove", { x: w.clientX, y: w.clientY }, { buttons: 1 }));
  ok("a move with a button held is someone else's drag: no hover", !layer.info()[0].hovered);
  fire(canvas, pev("pointermove", { x: w.clientX, y: w.clientY }, { buttons: 0 }));
  fire(canvas, { type: "pointerleave" });
  ok("leaving the canvas lets go too", !layer.info()[0].hovered && canvas.style.cursor === "");
}

// ---- a press that is not on a handle belongs to the editor
{
  const away = fire(canvas, pev("pointerdown", { x: 30, y: 40 }));
  ok("a press on empty space is not taken", !away.stopped && !layer.info().some((h) => h.dragging));
  fire(canvas, pev("pointerup", { x: 30, y: 40 }));
  const w = layer.info()[0];
  for (const [name, o] of [["the middle button", { button: 1, buttons: 4 }], ["the right button", { button: 2, buttons: 2 }],
    ["Ctrl", { ctrlKey: true }], ["Cmd", { metaKey: true }]] as const) {
    const e = fire(canvas, pev("pointerdown", { x: w.clientX, y: w.clientY }, o));
    ok(name + " on a handle is left to the editor", !e.stopped && !layer.info()[0].dragging);
  }
  busy = true;
  const b = fire(canvas, pev("pointerdown", { x: w.clientX, y: w.clientY }));
  ok("a G / R / S transform in flight keeps its click", !b.stopped && !layer.info()[0].dragging);
  busy = false;
  ok("none of that called the editor back", sets.length === 0 && commits.length === 0);
}

// ---- THE DRAG: width, 0.4 m out along x
{
  const it = items()[0];
  const a0 = it.at.slice() as Vec3;
  const down = fire(canvas, pev("pointerdown", clientOf(a0), { altKey: true }));
  ok("a press on a handle is taken before the editor sees it — even with Alt, which would orbit", down.stopped);
  ok("default is not prevented, so focus and the workspace's mousedown still happen", !down.prevented);
  ok("the drag has begun", layer.info()[0].dragging);
  ok("the pointer is captured, so the drag survives leaving the canvas", canvas.captured.has(1));
  ok("the viewport is focused, as for any press in it", canvas.focused === 1);
  ok("the keyboard is held for the drag, in the capture phase", winKeys.length === 1 && winKeys[0].capture);
  ok("the cursor says which way", /resize$/.test(canvas.style.cursor));
  ok("pressing is not an edit", sets.length === 0);

  const to = clientOf([a0[0] + 0.4, a0[1], a0[2]]);
  const mv = fire(canvas, pev("pointermove", to));
  ok("every move of the drag is kept from the editor", mv.stopped);
  eq("one set per move", sets.length, 1);
  eq("...on width", sets[0][0], "width");
  near("0.4 m out at scale 2 is 6.8", sets[0][1], 6.8, 1e-6);
  const label = canvas.kids.find((k: any) => k.dataset && "paramHandleLabel" in k.dataset);
  ok("the label says what the value is", label.textContent === "Width 6.8", label.textContent);
  fire(canvas, pev("pointermove", to));
  eq("the same place again sets nothing new", sets.length, 1);

  // The editor rebuilds (it debounces 70 ms); the next frame finds the new face.
  rebuild();
  layer.update();
  const a1 = items()[0].at as Vec3;
  near("the handle followed the model: 0.4 m further out", a1[0] - a0[0], 0.4, 0.01);
  const s1 = layer.info()[0];
  ok("and it is still under the pointer, within a pixel", Math.hypot(s1.clientX - to.x, s1.clientY - to.y) < 1.5,
    Math.hypot(s1.clientX - to.x, s1.clientY - to.y).toFixed(3) + " px");
  near("the line the drag measures on did not move with it", closestOnAxis((layer as any).drag.origin, [1, 0, 0],
    [persp.position.x, persp.position.y, persp.position.z], [0, 0, 0].map((_, k) => a0[k] - persp.position.getComponent(k)) as Vec3), 0, 1e-9);

  const further = clientOf([a0[0] + 0.55, a0[1], a0[2]]);
  fire(canvas, pev("pointermove", further));
  near("a further move measures from the press, not from the rebuilt face: 0.55 m is 7.1", sets[sets.length - 1][1], 7.1, 1e-6);
  const up = fire(canvas, pev("pointerup", further));
  ok("the release is kept from the editor too", up.stopped);
  eq("release commits once, on width", commits, ["width"]);
  ok("the pointer is let go", !canvas.captured.has(1));
  ok("so is the keyboard", winKeys.length === 0);
  ok("the drag is over", !layer.info()[0].dragging);
  rebuild();
  layer.update();
}

// ---- Escape puts it back
{
  sets.length = 0; commits.length = 0;
  const f = items()[2];
  const a0 = f.at.slice() as Vec3;
  fire(canvas, pev("pointerdown", clientOf(a0)));
  ok("floors: the press is taken", layer.info()[2].dragging);
  fire(canvas, pev("pointermove", clientOf([a0[0], a0[1] + 3.2, a0[2]])));
  eq("3.2 m up is one more floor, whole", sets, [["floors", 4]]);
  fire(canvas, pev("pointermove", clientOf([a0[0], a0[1] + 3.4, a0[2]])));
  eq("a little further is still 4: nothing new is set", sets.length, 1);
  const shift = press("Shift");
  ok("a modifier key is not swallowed", !shift.stopped);
  const tab = press("Tab");
  ok("every other key is held while the drag is live (Tab would change mode under the hand)", tab.stopped && tab.prevented);
  const esc = press("Escape");
  ok("Escape is taken, so the editor does not also deselect", esc.stopped && esc.prevented);
  eq("and the start value is put back", sets, [["floors", 4], ["floors", 3]]);
  eq("nothing is committed", commits, []);
  ok("the drag is over", !layer.info()[2].dragging && winKeys.length === 0 && !canvas.captured.has(1));
  const late = fire(canvas, pev("pointerup", clientOf(a0)));
  ok("the release after Escape is an ordinary release again", !late.stopped);
  eq("...and commits nothing", commits, []);
}

// ---- a click on a handle with no movement is not an edit; the other button cancels
{
  sets.length = 0; commits.length = 0;
  const w = layer.info()[0];
  fire(canvas, pev("pointerdown", { x: w.clientX, y: w.clientY }));
  fire(canvas, pev("pointerup", { x: w.clientX, y: w.clientY }));
  eq("a click is not an edit: no set, no commit", [sets.length, commits.length], [0, 0]);
  const b0 = items()[0], a = b0.at.slice() as Vec3, w0 = params.width;
  fire(canvas, pev("pointerdown", clientOf(a)));
  fire(canvas, pev("pointermove", clientOf([a[0] + 0.3, a[1], a[2]])));
  fire(canvas, pev("pointermove", clientOf(a)));
  fire(canvas, pev("pointerup", clientOf(a)));
  eq("out and back: out 0.3 m, then home to the start value",
    sets.map((s) => Math.round(s[1] * 1e6) / 1e6), [Math.round((w0 + 0.6) * 1e6) / 1e6, Math.round(w0 * 1e6) / 1e6]);
  eq("...is still ONE commit: the editor opened an undo gesture on the first set, and only commit closes it",
    commits, ["width"]);
  sets.length = 0; commits.length = 0;
  const it = items()[1];
  fire(canvas, pev("pointerdown", clientOf(it.at)));
  fire(canvas, pev("pointermove", clientOf([it.at[0], it.at[1], it.at[2] + 0.5])));
  near("depth: 0.5 m out at scale 2 is 7", sets[0]?.[1], 7, 1e-6);
  fire(canvas, pev("pointermove", clientOf([it.at[0], it.at[1], it.at[2] + 0.5]), { buttons: 3 }));
  eq("pressing the other button mid-drag cancels it, Blender's way", sets[sets.length - 1], ["depth", 6]);
  eq("...uncommitted", commits, []);
  fire(canvas, pev("pointerup", clientOf(it.at)));
  const c0 = items()[0];
  fire(canvas, pev("pointerdown", clientOf(c0.at)));
  fire(canvas, pev("pointermove", clientOf([c0.at[0] + 0.3, c0.at[1], c0.at[2]])));
  fire(canvas, { type: "pointercancel", pointerId: 1 });
  eq("a pointercancel puts the value back", sets[sets.length - 1], ["width", params.width]);
  near("...which is where it started", params.width, 7.1, 1e-6);
  eq("...and commits nothing", commits, []);

  // Let go where the canvas could not hear it. Chrome says so two ways: it takes the capture away
  // (lostpointercapture, no pointerup), and the next move arrives with no button held.
  sets.length = 0;
  fire(canvas, pev("pointerdown", clientOf(c0.at)));
  fire(canvas, pev("pointermove", clientOf([c0.at[0] + 0.2, c0.at[1], c0.at[2]])));
  fire(canvas, { type: "lostpointercapture", pointerId: 1 });
  eq("capture lost mid-drag is a release: the value is kept and committed", commits, ["width"]);
  near("...at what the drag reached", params.width, 7.1 + 0.4, 1e-6);
  ok("...and the drag is over", !layer.info()[0].dragging && winKeys.length === 0);
  commits.length = 0; sets.length = 0;
  const c1 = items()[0];
  fire(canvas, pev("pointerdown", clientOf(c1.at)));
  fire(canvas, pev("pointermove", clientOf([c1.at[0] - 0.2, c1.at[1], c1.at[2]])));
  const loose = fire(canvas, pev("pointermove", clientOf([c1.at[0] - 0.9, c1.at[1], c1.at[2]]), { buttons: 0 }));
  eq("a move with no button held is a release too — and it does not move the value", sets.length, 1);
  eq("...committed", commits, ["width"]);
  ok("...and kept from the editor, since it was still the drag's", loose.stopped);
  near("the value is what the last held move made it", params.width, 7.1, 1e-6);
  commits.length = 0; sets.length = 0;

  // Nothing heard at all: no pointerup, no lost capture, no buttonless move. The next press by the
  // same pointer closes the old drag as a release; a second finger is refused.
  const c2 = items()[0];
  fire(canvas, pev("pointerdown", clientOf(c2.at)));
  fire(canvas, pev("pointermove", clientOf([c2.at[0] + 0.1, c2.at[1], c2.at[2]])));
  const finger = fire(canvas, pev("pointerdown", { x: 30, y: 40 }, { pointerId: 2 }));
  ok("a second finger mid-drag is kept from the editor and does not end the drag", finger.stopped && layer.info()[0].dragging);
  const again = fire(canvas, pev("pointerdown", { x: 30, y: 40 }));
  eq("the same pointer pressing again commits the drag it never heard end", commits, ["width"]);
  ok("...and its new press, on empty space, goes to the editor", !again.stopped && !layer.info().some((h) => h.dragging));
  fire(canvas, pev("pointerup", { x: 30, y: 40 }));
  near("the value is where the unheard drag left it", params.width, 7.1 + 0.2, 1e-6);
  params.width = 7.1;
  commits.length = 0; sets.length = 0;
}

// ---- a re-render that hands the same list and fresh callbacks does not break the drag
{
  sets.length = 0; commits.length = 0;
  const other: Array<[string, number]> = [];
  const it = items()[0];
  const a0 = it.at.slice() as Vec3;
  fire(canvas, pev("pointerdown", clientOf(a0)));
  fire(canvas, pev("pointermove", clientOf([a0[0] + 0.1, a0[1], a0[2]])));
  const before = items()[0].group;
  layer.set(handlesFromManifest(fixtureManifest, SPECS), cbFor("b", other));
  ok("the same handles again: nothing is rebuilt", items()[0].group === before);
  ok("and the drag goes on", layer.info()[0].dragging);
  fire(canvas, pev("pointermove", clientOf([a0[0] + 0.2, a0[1], a0[2]])));
  near("through the NEW callbacks", other[0]?.[1], 7.1 + 0.4, 1e-6);
  eq("and not the old ones", sets.length, 1);
  layer.set([handles[0], handles[2]], cbFor("c", other));
  ok("a different list that still has the dragged handle keeps the drag", layer.info()[0].dragging && layer.info().length === 2);
  layer.set([handles[1]], cbFor("d", other));
  ok("a list without it drops the drag, quietly", !layer.info().some((h) => h.dragging) && winKeys.length === 0);
  eq("with nothing restored and nothing committed", [commits.length, other.length], [0, 1]);
  params.width = 7.1;
  rebuild();
  layer.set(handles, cbFor("a"));
  layer.update();
}

// ---- only in Object mode
{
  mode = "edit";
  layer.update();
  ok("in Edit mode the handles are not drawn", !(layer as any).root.visible);
  const w = items()[0];
  const e = fire(canvas, pev("pointerdown", clientOf(w.at)));
  ok("and a press goes to the vertex picker", !e.stopped);
  mode = "terrain";
  layer.update();
  ok("nor in Terrain mode", !(layer as any).root.visible && !fire(canvas, pev("pointerdown", clientOf(w.at))).stopped);
  mode = "object";
  layer.update();
  ok("back in Object mode they are", (layer as any).root.visible && layer.info().every((h) => h.visible));
}

// ---- end-on, and the far side
{
  aim(persp, [0, 4.6, 40]);                    // straight at the front
  layer.update();
  const info = layer.info();
  ok("from the front, the depth handle points at the eye and is not drawn", !info[1].visible);
  ok("width and floors are", info[0].visible && info[2].visible);
  ok("at full strength, though both faces are seen edge-on — they are on the outline, not behind it",
    items()[0].mats.disc.opacity > 0.9 && items()[2].mats.disc.opacity > 0.9,
    items()[0].mats.disc.opacity + " / " + items()[2].mats.disc.opacity);
  const e = fire(canvas, pev("pointerdown", { x: info[1].clientX, y: info[1].clientY }));
  ok("and an end-on handle cannot be taken", !e.stopped || !layer.info()[1].dragging);
  if (e.stopped) fire(canvas, pev("pointerup", { x: info[1].clientX, y: info[1].clientY }));
  aim(persp, [0, 60, 0.001]);                   // straight down
  layer.update();
  ok("from the top, floors is not drawn", !layer.info()[2].visible && layer.info()[0].visible);
  aim(persp, [-14, 10, -18]);                   // from behind
  layer.update();
  ok("from behind, a handle on the far face is still drawn", layer.info()[0].visible);
  ok("but lighter", items()[0].mats.disc.opacity < 0.7 && !items()[0].facing, String(items()[0].mats.disc.opacity));
  aim(persp, [14, 10, 18]);
  layer.update();
  ok("from the front three-quarter, full strength", items()[0].mats.disc.opacity > 0.9);
}

// ---- an orthographic camera
{
  const ortho = new THREE.OrthographicCamera(-8 * (W / H), 8 * (W / H), 8, -8, -1000, 1000);
  aim(ortho, [20, 4.6, 30]);
  cam = ortho;
  layer.update();
  const it = items()[0];
  ok("the handles lay out under an orthographic camera", layer.info()[0].visible);
  near("where a pixel is (16 m in 600 px) wherever the handle is", it.group.scale.x, 16 / H, 1e-9);
  sets.length = 0; commits.length = 0;
  const a0 = it.at.slice() as Vec3;
  fire(canvas, pev("pointerdown", clientOf(a0)));
  fire(canvas, pev("pointermove", clientOf([a0[0] + 0.25, a0[1], a0[2]])));
  fire(canvas, pev("pointerup", clientOf([a0[0] + 0.25, a0[1], a0[2]])));
  near("and drag the same: 0.25 m out is +0.5", sets[0]?.[1], 7.1 + 0.5, 1e-6);
  eq("committed", commits, ["width"]);
  cam = persp;
  params.width = 6;
  rebuild();
  layer.update();
}

// ---- a part's box, and two handles on one face
{
  layer.set([{ param: "width", axis: "x", side: "max", scale: 2, snap: 0, part: "roof" }], cbFor("e"));
  layer.update();
  const roof = new THREE.Box3().setFromObject(subject.getObjectByName("roof")!);
  const at = items()[0].at as Vec3;
  near("a handle with `part` sits on that part's face, not the building's", at[1], (roof.min.y + roof.max.y) / 2, 1e-9);
  layer.set([{ param: "width", axis: "x", side: "max", scale: 2, snap: 0, part: "no such part" }], cbFor("e"));
  layer.update();
  near("a part that is not there falls back to the whole asset", (items()[0].at as Vec3)[1],
    (worldBox().min.y + worldBox().max.y) / 2, 1e-9);
  layer.set([
    { param: "width", axis: "x", side: "max", scale: 2, snap: 0 },
    { param: "bay", axis: "x", side: "max", scale: 1, snap: 0 },
  ], cbFor("e"));
  layer.update();
  const [p, q] = layer.info();
  ok("two handles on one face are spread apart, so both can be reached",
    Math.hypot(p.clientX - q.clientX, p.clientY - q.clientY) > 25, Math.hypot(p.clientX - q.clientX, p.clientY - q.clientY).toFixed(1) + " px");
  const hidden = subject.getObjectByName("building")!;
  hidden.visible = false;
  (layer as any).held.clear();
  layer.update();
  ok("with nothing drawn there is nothing to sit on", !layer.info()[0].visible);
  hidden.visible = true;
  layer.update();
  ok("drawn again, they come back", layer.info()[0].visible);
  layer.set(handles, cbFor("a"));
  layer.update();
}

// ---- a fault in the frame never takes the frame down
{
  const warns: unknown[] = [];
  const realWarn = console.warn;
  console.warn = (...a: unknown[]) => { warns.push(a); };
  const realCamera = host.camera;
  host.camera = () => { throw new Error("no camera"); };
  let threw = false;
  try { layer.update(); layer.update(); } catch { threw = true; }
  host.camera = realCamera;
  console.warn = realWarn;
  ok("a throw inside the layout is caught", !threw);
  ok("...hides the handles", !(layer as any).root.visible);
  eq("...and says so once, not every frame", warns.length, 1);
  layer.update();
  ok("the next good frame draws them again", (layer as any).root.visible);
}

// ---- null: nothing drawn, nothing listening
{
  const w = items()[0];
  fire(canvas, pev("pointerdown", clientOf(w.at)));
  ok("a drag is live when the layer goes", layer.info()[0].dragging && winKeys.length === 1);
  sets.length = 0; commits.length = 0;
  layer.dispose();
  ok("dispose removes every canvas listener", listening(canvas) === 0, String(listening(canvas)));
  ok("and the key listener", winKeys.length === 0);
  ok("and lets the pointer go", !canvas.captured.has(1));
  ok("and takes the handles out of the gizmo's scene", !gizmoScene.children.some((o: any) => o.name === "__paramHandles"));
  ok("and the label out of the page", canvas.kids.every((k: any) => k.removed));
  ok("and puts the cursor back", canvas.style.cursor === "");
  eq("and calls nobody on the way out", [sets.length, commits.length], [0, 0]);
  eq("nothing is left to report", layer.info(), []);
}

// =========================================================================================
section("Wired into the viewport");
{
  const world = readFileSync(join(process.cwd(), "src", "components", "engine", "edit", "world.ts"), "utf8");
  const body = world.slice(world.indexOf("setParamHandles(handles"), world.indexOf("paramHandleInfo()"));
  ok("setParamHandles makes a layer in the gizmo's scene", /new ParamHandleLayer\(this\.T/.test(body) && /scene: this\.gizmoScene/.test(body));
  ok("null, an empty list or no callbacks dispose it", /if \(!handles \|\| !handles\.length \|\| !cb\)/.test(body) && /this\.handleLayer\?\.dispose\(\);\s*this\.handleLayer = null;/.test(body));
  ok("the same list again goes through set(), which keeps a drag", /this\.handleLayer\.set\(handles, cb\)/.test(body));
  ok("only in Object mode, and never over a transform in flight",
    /live: \(\) => this\.mode === "object"/.test(body) && /busy: \(\) => this\.gizmo\.dragging/.test(body));
  const render = world.slice(world.indexOf("  render() {"), world.indexOf("  renderSafe() {"));
  const u = render.indexOf("this.handleLayer?.update()"), g = render.indexOf("r.render(this.gizmoScene, cam)"), m = render.indexOf("r.render(this.scene, cam)");
  ok("placed every frame after the main pass and before the gizmo pass draws them", m > 0 && u > m && g > u);
  const after = world.slice(world.indexOf("  private afterBuild(out: any) {"), world.indexOf("  get hasSceneLights()"));
  ok("and re-placed where every build path ends, so the positions are right before the next frame",
    /this\.handleLayer\?\.update\(\);\s*\}\s*\/\*\* The asset brought its own lights/.test(after + "/** The asset brought its own lights"));
  for (const path of ["loadPcSnapshot(snap", "async run(code", "async runFile(build", "async loadJSON(json"]) {
    const at = world.indexOf(path);
    const next = world.indexOf("\n  }\n", at);
    ok(path.replace(/\(.*/, "") + " finishes in afterBuild", at > 0 && world.slice(at, next).includes("this.afterBuild("));
  }
  const dispose = world.slice(world.indexOf("  dispose() {\n    this.handleLayer"), world.indexOf("// ------------------------------------------------------------------ terrain\n//\n// THE GROUND"));
  ok("the world's dispose takes the layer with it", /this\.handleLayer\?\.dispose\(\)/.test(dispose));
  ok("and the stub signature is unchanged", /setParamHandles\(handles: HandleSpec\[\] \| null, cb\?: ParamHandleCallbacks\): void/.test(world));
}

// ------------------------------------------------------------------------------------------
console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL " + f);
process.exit(fails.length ? 1 : 0);
