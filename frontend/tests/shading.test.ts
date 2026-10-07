// What the viewport shows when it opens, and whether Solid keeps an asset's colour.
//
// The fault these lock down was found by a person, not a test: an asset opened in the Edit tab
// and every part of it was the same grey. Solid shading swapped one MeshStandardMaterial over the
// whole subject, and Solid was what the editor opened on, so a chest built from thirteen
// materials looked like unpainted clay and there was no sign anything was wrong.
//
// Run: npm run test:shading

import { solidColorMaterial } from "../src/components/engine/edit/overlay";
import { ENGINE_DEFAULTS, mergePrefs } from "../src/components/engine/prefs";

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

// ---------------------------------------------------------------- a stand-in three
//
// The real module never arrives in node. Only the constructors this code path touches are
// needed, and each one records exactly what it was handed.

class FakeColor {
  hex: number;
  constructor(hex: number) { this.hex = hex; }
  clone() { return new FakeColor(this.hex); }
}
const mat = (kind: string) => class {
  kind = kind;
  [k: string]: any;
  constructor(o: Record<string, any> = {}) { Object.assign(this, o); this.kind = kind; }
  dispose() { this.disposed = true; }
};
const T: any = {
  Color: FakeColor,
  FrontSide: 0, DoubleSide: 2,
  MeshStandardMaterial: mat("standard"),
  MeshBasicMaterial: mat("basic"),
  LineBasicMaterial: mat("line"),
  PointsMaterial: mat("points"),
};

const src = (o: Record<string, any> = {}) => ({ color: new FakeColor(0x5a54c8), ...o });

// ---------------------------------------------------------------- the colour survives

{
  const m: any = solidColorMaterial(T, src());
  eq("solid keeps the part's own colour", m.color.hex, 0x5a54c8);
  ok("and lights it, so form still reads", m.kind === "standard");
  ok("but drops metalness, so Solid is not a second Render", m.metalness === 0.02);
}

{
  const tex = { isTexture: true };
  const m: any = solidColorMaterial(T, src({ map: tex }));
  ok("a colour map comes across", m.map === tex);
}

{
  const m: any = solidColorMaterial(T, src(), "mesh", true);
  ok("flat lighting is unlit, not grey", m.kind === "basic");
  eq("and still the part's own colour", m.color.hex, 0x5a54c8);
}

// A gold bar at metalness 0.95 has an emissive and an env map that Solid must not carry, or the
// mode stops being the cheap one.
{
  const m: any = solidColorMaterial(T, src({ metalness: 0.95, emissive: new FakeColor(0xff0000), envMap: {} }));
  ok("emissive is dropped", m.emissive === undefined);
  ok("the environment map is dropped", m.envMap === undefined);
}

// ---------------------------------------------------------------- what must not be lost

{
  const m: any = solidColorMaterial(T, src({ transparent: true, opacity: 0.3, depthWrite: false }));
  ok("a see-through part stays see-through", m.transparent === true && m.opacity === 0.3);
  ok("and keeps its depth write off", m.depthWrite === false);
}

{
  const m: any = solidColorMaterial(T, src({ vertexColors: true }));
  ok("per-vertex colour is kept", m.vertexColors === true);
}

{
  const m: any = solidColorMaterial(T, src({ side: T.DoubleSide }));
  eq("a two-sided part stays two-sided", m.side, T.DoubleSide);
}

{
  const m: any = solidColorMaterial(T, src({ alphaTest: 0.5 }));
  eq("a cut-out leaf keeps its alpha test", m.alphaTest, 0.5);
}

// ---------------------------------------------------------------- lines and points
//
// A MeshStandardMaterial on a Line draws nothing. The old code put the one grey mesh material on
// every Line and Points in the subject, which is how a hair card or a debug path vanished in
// Solid and came back in Material.

{
  const m: any = solidColorMaterial(T, src(), "line");
  ok("a line gets a line material", m.kind === "line");
  eq("in its own colour", m.color.hex, 0x5a54c8);
}

{
  const m: any = solidColorMaterial(T, src({ size: 7, sizeAttenuation: true }), "points");
  ok("points get a points material", m.kind === "points");
  eq("at the size the code asked for", m.size, 7);
  eq("and its own attenuation", m.sizeAttenuation, true);
}

// ---------------------------------------------------------------- nothing to copy

{
  const m: any = solidColorMaterial(T, null);
  ok("a part with no material still gets one", m.kind === "standard");
  ok("in the neutral grey", m.color.hex === 0xb9c3d0);
}

// ---------------------------------------------------------------- how the editor opens

eq("the editor opens on material preview", ENGINE_DEFAULTS.shading, "material");
eq("and Solid, when chosen, is coloured", ENGINE_DEFAULTS.solid_color, "material");

{
  const p = mergePrefs({ solid_color: "single" });
  eq("single grey is still available", p.solid_color, "single");
}
{
  const p = mergePrefs({ solid_color: "rainbow" });
  eq("a value nobody knows falls back", p.solid_color, "material");
}
{
  const p = mergePrefs({});
  eq("settings that predate the field get the default", p.solid_color, "material");
  eq("and the coloured shading mode", p.shading, "material");
}

// ---------------------------------------------------------------- report
if (fails.length) {
  console.error("\nFAILED " + fails.length + " of " + (pass + fails.length));
  for (const f of fails) console.error("  x " + f);
  process.exit(1);
}
console.log("shading: " + pass + " checks pass");
