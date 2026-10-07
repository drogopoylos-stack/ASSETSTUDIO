// The editor's pure half: the data model for a manual edit, and the two ways a number inside a
// procedural asset becomes a slider. Nothing here touches an engine, a canvas or the DOM.
//
// This is the part that has to be exactly right, because it writes back into the user's own
// source file. A parametric editor that corrupts the code it edits is worse than no editor, so
// every rewrite here is the narrowest possible edit: one number span replaced, nothing else
// touched, and the file is byte-identical everywhere else.

// A type-only import: erased at build, so this file stays free of ops.ts at runtime and
// forge-ops.js keeps bundling on its own.
import type { VertEdit } from "./ops";

export type { VertEdit };
export type V3 = [number, number, number];
export type ParamValue = number | boolean | string;
// "choice" is a string with a fixed list of values — a dropdown. Declared in a manifest with
// `options: [...]` (see configurator.ts); it has no span, so it is never written into the source.
export type ParamKind = "number" | "bool" | "text" | "color" | "choice";

/** Where a value literally sits in the source text, so it can be replaced without a reformat. */
export interface Span { start: number; end: number }

export interface ParamSpec {
  key: string;
  label: string;
  kind: ParamKind;
  value: ParamValue;
  min?: number;
  max?: number;
  step?: number;
  /** Present only for a DETECTED parameter — a declared one has no home in the text. */
  span?: Span;
  /** How many decimals the source wrote, so a rewrite keeps the style of the file. */
  decimals?: number;
  /** How a colour was spelled. Both spellings hold the same "#rrggbb" value, so without this
   *  the write-back cannot tell 0x7a8b5c from "#7a8b5c" and would corrupt one of them. */
  literal?: "hex" | "quoted";
  from: "declared" | "detected";
  group?: string;
  /** For a parameter read out of a CALL SITE: the name the scene gave that instance, when the
   *  code says so, so the panel can put the sliders beside the part they change. */
  part?: string;
  /** kind "choice": the values the dropdown offers, in order. */
  options?: string[];
  /** Hidden until the panel's "Advanced" switch is on — the settings most people never touch. */
  advanced?: boolean;
}

export interface PartOverride {
  pos?: V3;
  rot?: V3;
  scale?: V3;
  hidden?: boolean;
  /** A material colour on a mesh, the light colour on a light. One key, because a person means
   *  the same thing by "colour" for both and a sidecar should read the way a person thinks. */
  color?: string;
  roughness?: number;
  metalness?: number;
  // A light.
  intensity?: number;
  distance?: number;
  angle?: number;
  penumbra?: number;
  decay?: number;
  shadow?: boolean;
  // A camera.
  fov?: number;
  near?: number;
  far?: number;
  zoom?: number;
  /** A PIECE of a merged mesh (the key reads `mesh~t<first>-<end>`): where its middle was and how
   *  many corners it has, in the mesh's own frame, when it was picked. The applier checks both
   *  before it moves a vertex, so an edit to a mesh the code has since rebuilt differently is
   *  reported instead of landing on some other object. See pieces.ts. */
  piece?: { c: V3; n: number };
}

/** The numeric fields of an override that are not a transform: copied as-is by the applier. */
export const OVERRIDE_NUMBERS = ["roughness", "metalness", "intensity", "distance", "angle", "penumbra", "decay", "fov", "near", "far", "zoom"] as const;

export interface BoneSpec {
  name: string;
  parent: string | null;
  head: V3;
  tail: V3;
  /** Which parts this bone drives, when the rig is bound by name rather than by weight. */
  parts?: string[];
}

export type TrackProp = "pos" | "rot" | "scale";
export interface Key { t: number; v: number; ease?: "linear" | "constant" | "smooth" }
export interface Track { target: string; prop: TrackProp; axis: 0 | 1 | 2; keys: Key[] }
export interface Clip { name: string; fps: number; start: number; end: number; loop: boolean; tracks: Track[] }

/** One entry in the modifier stack: an operation, what it applies to, and its settings.
 *
 *  This is Blender's stack, and it is a stack for the same reason: the operations are re-applied
 *  in order after every rebuild, so changing a parameter does not throw away the weld and the
 *  subdivision that were put on top of it. A baked result could not survive a slider. */
export interface Mod {
  op: "weld" | "smooth" | "subdivide" | "mirror" | "solidify" | "displace" | "simplify" | "flip" | "skin"
    | "subsurf" | "bevel" | "unwrap" | "remesh" | "relax";
  /** A part key, or "" for the whole asset. */
  target: string;
  args: Record<string, number | string | boolean>;
  off?: boolean;
}

/** The scene's world: what Blender keeps in the World tab. Applied to the Scene the code returned
 *  (or the one the game hands to `applyEdits`), never to the studio's own. */
export interface WorldOverride {
  background?: string;
  /** `null` means the code's fog is switched off; absent means untouched. */
  fog?: { type: "linear" | "exp2"; color: string; near?: number; far?: number; density?: number } | null;
}

/** A map baked onto a part's material. Kept as the recipe, not the pixels: a rebuild bakes again. */
export interface Bake { part: string; kind: "ao" | "curvature" | "normal"; size: number; rays?: number }

export interface Edits {
  version: 1;
  asset: string;
  params: Record<string, ParamValue>;
  parts: Record<string, PartOverride>;
  bones: BoneSpec[];
  /** Bone name to a rest-relative euler, in radians. The pose, not the rig. */
  pose: Record<string, V3>;
  clips: Clip[];
  mods: Mod[];
  /** VERTICES THE HAND MOVED. Keyed by where the vertex was, never by its index, because the code
   *  runs again and an index survives nothing. See VertEdit in ops.ts for why. */
  verts?: VertEdit[];
  world?: WorldOverride;
  bakes?: Bake[];
  /** THINGS THE CODE DID NOT MAKE. A procedural game rebuilds its world every run, so an object
   *  you place cannot live in the scene — it lives here, and is put back after the code has
   *  finished building. Each entry says what to make and where to put it, never a live object,
   *  because this document is JSON on disk. */
  placed?: PlacedItem[];
  updated?: number;
}

export interface PlacedRef {
  kind: "primitive" | "code" | "model" | "image" | "clone";
  /** primitive: which shape, and its colour. */
  shape?: string;
  color?: string;
  /** code: a project-relative file and the export in it that builds something. */
  file?: string;
  export?: string;
  args?: any[];
  /** model and image: a project-relative path. */
  url?: string;
  /** clone: the stable key of an object the game itself built. "Another one of those." */
  of?: string;
  /** spec: which game inside the workspace owns the file, which table it came from and which
   *  entry. The entry is found by its own id at build time, because the table it was declared in
   *  is very often a module-private const the game never exports. */
  root?: string;
  table?: string;
  key?: string;
  index?: number;
  deps?: string[];
  /** True when this is one entry of a table rather than a whole exported builder. */
  spec?: boolean;
  /** model: the nodes of the file a table entry names — the first is the one the game draws, the
   *  rest its repaints — and the extra parts it always shows. */
  nodes?: string[];
  parts?: string[];
}

export interface PlacedItem {
  id: string;
  name: string;
  ref: PlacedRef;
  pos: [number, number, number];
  rot: [number, number, number];
  scale: [number, number, number];
}

const MOD_OPS = new Set(["weld", "smooth", "subdivide", "mirror", "solidify", "displace", "simplify", "flip", "skin",
  "subsurf", "bevel", "unwrap", "remesh", "relax"]);

export function emptyEdits(asset = ""): Edits {
  return { version: 1, asset, params: {}, parts: {}, bones: [], pose: {}, clips: [], mods: [] };
}

/** True when there is genuinely nothing to save, so an untouched asset never gets a sidecar. */
export function isEmpty(e: Edits): boolean {
  return !Object.keys(e.params).length && !Object.keys(e.parts).length
    && !e.bones.length && !Object.keys(e.pose).length && !e.clips.length && !e.mods.length
    && !worldCount(e.world) && !(e.bakes?.length) && !(e.placed?.length);
}

export function countEdits(e: Edits): number {
  return (e.placed?.length || 0) + Object.keys(e.params).length + Object.keys(e.parts).length
    + e.bones.length + Object.keys(e.pose).length + e.clips.length + e.mods.length + worldCount(e.world) + (e.bakes?.length || 0);
}

const worldCount = (w?: WorldOverride) => (w ? (w.background !== undefined ? 1 : 0) + (w.fog !== undefined ? 1 : 0) : 0);

function normaliseWorld(raw: any): WorldOverride | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const w: WorldOverride = {};
  if (typeof raw.background === "string" && /^#[0-9a-fA-F]{6}$/.test(raw.background)) w.background = raw.background.toLowerCase();
  if (raw.fog === null) w.fog = null;
  else if (raw.fog && typeof raw.fog === "object" && typeof raw.fog.color === "string") {
    const f = raw.fog;
    const type = f.type === "exp2" ? "exp2" : "linear";
    w.fog = { type, color: f.color };
    for (const k of ["near", "far", "density"] as const) if (typeof f[k] === "number" && isFinite(f[k])) w.fog[k] = f[k];
  }
  return worldCount(w) ? w : undefined;
}

/** Accept anything off disk without trusting it: a half-written sidecar must not break the editor. */
export function normaliseEdits(raw: any, asset = ""): Edits {
  const e = emptyEdits(asset);
  if (!raw || typeof raw !== "object") return e;
  if (typeof raw.asset === "string" && raw.asset) e.asset = raw.asset;
  if (raw.params && typeof raw.params === "object") {
    for (const [k, v] of Object.entries(raw.params)) {
      if (typeof v === "number" || typeof v === "boolean" || typeof v === "string") e.params[k] = v;
    }
  }
  if (raw.parts && typeof raw.parts === "object") {
    for (const [k, v] of Object.entries<any>(raw.parts)) {
      const o: PartOverride = {};
      if (isV3(v?.pos)) o.pos = v.pos.slice(0, 3) as V3;
      if (isV3(v?.rot)) o.rot = v.rot.slice(0, 3) as V3;
      if (isV3(v?.scale)) o.scale = v.scale.slice(0, 3) as V3;
      // Both values: `false` forces on something the game's code hides, and the scene API writes
      // it for exactly that. Dropped here, the Edit tab's next save took the show back out.
      if (typeof v?.hidden === "boolean") o.hidden = v.hidden;
      // A piece of a merged mesh carries where its middle was and how many triangles it had; the
      // runtime checks it before moving the piece, so the editor must not lose it on load.
      if (v?.piece && isV3(v.piece.c) && typeof v.piece.n === "number" && isFinite(v.piece.n)) {
        o.piece = { c: v.piece.c.slice(0, 3) as V3, n: v.piece.n };
      }
      if (typeof v?.color === "string") o.color = v.color;
      if (typeof v?.shadow === "boolean") o.shadow = v.shadow;
      for (const f of OVERRIDE_NUMBERS) if (typeof v?.[f] === "number" && isFinite(v[f])) o[f] = v[f];
      if (Object.keys(o).length) e.parts[k] = o;
    }
  }
  const world = normaliseWorld(raw.world);
  if (world) e.world = world;
  // THE THINGS THE CODE DID NOT MAKE. Validated field by field like everything else here: this
  // document is read from a file a person or an agent may have written by hand, and one bad entry
  // must cost that entry, never the scene.
  if (Array.isArray(raw.placed)) {
    const placed: PlacedItem[] = [];
    for (const it of raw.placed) {
      const ref = it?.ref;
      if (!it || typeof it.id !== "string" || !ref || typeof ref !== "object") continue;
      if (!["primitive", "code", "model", "image", "clone"].includes(ref.kind)) continue;
      placed.push({
        id: it.id,
        name: typeof it.name === "string" ? it.name : it.id,
        ref: {
          kind: ref.kind,
          ...(typeof ref.shape === "string" ? { shape: ref.shape } : {}),
          ...(typeof ref.color === "string" ? { color: ref.color } : {}),
          ...(typeof ref.file === "string" ? { file: ref.file } : {}),
          ...(typeof ref.export === "string" ? { export: ref.export } : {}),
          ...(typeof ref.url === "string" ? { url: ref.url } : {}),
          ...(typeof ref.of === "string" ? { of: ref.of } : {}),
          ...(Array.isArray(ref.args) ? { args: ref.args } : {}),
        },
        pos: isV3(it.pos) ? (it.pos.slice(0, 3) as V3) : [0, 0, 0],
        rot: isV3(it.rot) ? (it.rot.slice(0, 3) as V3) : [0, 0, 0],
        scale: isV3(it.scale) ? (it.scale.slice(0, 3) as V3) : [1, 1, 1],
      });
    }
    if (placed.length) e.placed = placed;
  }
  if (Array.isArray(raw.bakes)) {
    const bakes: Bake[] = [];
    for (const b of raw.bakes) {
      if (!b || typeof b.part !== "string" || !["ao", "curvature", "normal"].includes(b.kind)) continue;
      const size = [128, 256, 512, 1024].includes(b.size) ? b.size : 256;
      const bake: Bake = { part: b.part, kind: b.kind, size };
      if (typeof b.rays === "number" && b.rays > 0) bake.rays = Math.round(b.rays);
      bakes.push(bake);
    }
    if (bakes.length) e.bakes = bakes;
  }
  if (Array.isArray(raw.bones)) {
    for (const b of raw.bones) {
      if (!b || typeof b.name !== "string" || !isV3(b.head) || !isV3(b.tail)) continue;
      e.bones.push({
        name: b.name,
        parent: typeof b.parent === "string" ? b.parent : null,
        head: b.head.slice(0, 3) as V3,
        tail: b.tail.slice(0, 3) as V3,
        parts: Array.isArray(b.parts) ? b.parts.filter((s: any) => typeof s === "string") : undefined,
      });
    }
  }
  if (raw.pose && typeof raw.pose === "object") {
    for (const [k, v] of Object.entries(raw.pose)) if (isV3(v)) e.pose[k] = (v as number[]).slice(0, 3) as V3;
  }
  if (Array.isArray(raw.clips)) for (const c of raw.clips) { const n = normClip(c); if (n) e.clips.push(n); }
  if (Array.isArray(raw.mods)) {
    for (const m of raw.mods) {
      if (!m || !MOD_OPS.has(m.op)) continue;
      const args: Record<string, number | string | boolean> = {};
      if (m.args && typeof m.args === "object") {
        for (const [k, v] of Object.entries(m.args)) {
          if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") args[k] = v;
        }
      }
      e.mods.push({ op: m.op, target: typeof m.target === "string" ? m.target : "", args, off: m.off === true });
    }
  }
  if (typeof raw.updated === "number") e.updated = raw.updated;
  return e;
}

function isV3(v: any): v is V3 {
  return Array.isArray(v) && v.length >= 3 && v.slice(0, 3).every((n: any) => typeof n === "number" && isFinite(n));
}

function normClip(c: any): Clip | null {
  if (!c || typeof c.name !== "string") return null;
  const tracks: Track[] = [];
  if (Array.isArray(c.tracks)) {
    for (const t of c.tracks) {
      if (!t || typeof t.target !== "string") continue;
      if (t.prop !== "pos" && t.prop !== "rot" && t.prop !== "scale") continue;
      const axis = t.axis === 1 ? 1 : t.axis === 2 ? 2 : 0;
      const keys: Key[] = [];
      if (Array.isArray(t.keys)) {
        for (const k of t.keys) {
          if (!k || typeof k.t !== "number" || typeof k.v !== "number") continue;
          keys.push({ t: k.t, v: k.v, ease: k.ease === "constant" || k.ease === "smooth" ? k.ease : "linear" });
        }
      }
      keys.sort((a, b) => a.t - b.t);
      if (keys.length) tracks.push({ target: t.target, prop: t.prop, axis: axis as 0 | 1 | 2, keys });
    }
  }
  return {
    name: c.name,
    fps: typeof c.fps === "number" && c.fps > 0 ? c.fps : 24,
    start: typeof c.start === "number" ? c.start : 0,
    end: typeof c.end === "number" ? c.end : 48,
    loop: c.loop !== false,
    tracks,
  };
}

// ------------------------------------------------------------------ animation
export const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
const smooth = (u: number) => u * u * (3 - 2 * u);

/** Value of one track at a frame. Outside the keys it HOLDS rather than extrapolating: an
 *  extrapolated rotation flies off the model, and a held one is what an animator expects. */
export function sampleTrack(track: Track, t: number): number {
  const k = track.keys;
  if (!k.length) return 0;
  if (t <= k[0].t) return k[0].v;
  if (t >= k[k.length - 1].t) return k[k.length - 1].v;
  let i = 0;
  while (i < k.length - 1 && k[i + 1].t <= t) i++;
  const a = k[i], b = k[i + 1];
  if (a.ease === "constant") return a.v;
  const span = b.t - a.t;
  if (span <= 0) return b.v;
  const u = (t - a.t) / span;
  return a.v + (b.v - a.v) * (a.ease === "smooth" ? smooth(u) : u);
}

export function clipRange(c: Clip): [number, number] {
  let lo = c.start, hi = c.end;
  for (const t of c.tracks) for (const k of t.keys) { if (k.t < lo) lo = k.t; if (k.t > hi) hi = k.t; }
  return [lo, hi];
}

/** Every target the clip touches at frame t, as a sparse transform. A property the clip never
 *  keys is left undefined, so the rest pose shows through instead of being zeroed. */
export function evalClip(c: Clip, t: number): Record<string, PartOverride> {
  const out: Record<string, PartOverride> = {};
  for (const tr of c.tracks) {
    const o = (out[tr.target] ||= {});
    const cur = (o[tr.prop] || (tr.prop === "scale" ? [1, 1, 1] : [0, 0, 0])) as V3;
    cur[tr.axis] = sampleTrack(tr, t);
    o[tr.prop] = cur;
  }
  return out;
}

/** Put a key on the track, replacing one already at that frame. */
export function insertKey(c: Clip, target: string, prop: TrackProp, axis: 0 | 1 | 2, t: number, v: number): Track {
  let tr = c.tracks.find((x) => x.target === target && x.prop === prop && x.axis === axis);
  if (!tr) { tr = { target, prop, axis, keys: [] }; c.tracks.push(tr); }
  const at = tr.keys.findIndex((k) => Math.abs(k.t - t) < 1e-6);
  if (at >= 0) tr.keys[at].v = v; else tr.keys.push({ t, v, ease: "smooth" });
  tr.keys.sort((a, b) => a.t - b.t);
  return tr;
}

export function removeKeysAt(c: Clip, target: string, t: number): number {
  let n = 0;
  for (const tr of c.tracks) {
    if (tr.target !== target) continue;
    const before = tr.keys.length;
    tr.keys = tr.keys.filter((k) => Math.abs(k.t - t) >= 1e-6);
    n += before - tr.keys.length;
  }
  c.tracks = c.tracks.filter((tr) => tr.keys.length > 0);
  return n;
}

/** Frames that hold a key for a target: what the timeline draws as diamonds. */
export function keyFrames(c: Clip, target = ""): number[] {
  const s = new Set<number>();
  for (const tr of c.tracks) {
    if (target && tr.target !== target) continue;
    for (const k of tr.keys) s.add(k.t);
  }
  return [...s].sort((a, b) => a - b);
}

// ------------------------------------------------- reading parameters out of source
//
// A procedural asset is mostly a wall of named constants. `const SAIL_H = 0.9;` is already a
// parameter declaration; it just has no range attached. Reading them out of the file means every
// asset in the project is editable on the day the editor ships, with no rewrite and no manifest,
// and the write-back is the narrowest edit a file can take: one number.
//
// The scan has to know what is code. A number inside a string or a comment is not a parameter,
// and a constant inside a function is a local, not a knob. So the source is masked once for
// strings, templates, comments and brace depth, and only depth-zero code is offered.

interface Masked { code: Uint8Array; depth: Uint16Array }

export function maskSource(src: string): Masked {
  const n = src.length;
  const code = new Uint8Array(n);
  const depth = new Uint16Array(n);
  let d = 0;
  let i = 0;
  while (i < n) {
    const c = src[i], c2 = src[i + 1];
    if (c === "/" && c2 === "/") { while (i < n && src[i] !== "\n") { depth[i] = d; i++; } continue; }
    if (c === "/" && c2 === "*") {
      i += 2;
      while (i < n && !(src[i] === "*" && src[i + 1] === "/")) { depth[i] = d; i++; }
      i = Math.min(n, i + 2);
      continue;
    }
    if (c === '"' || c === "'") {
      const q = c;
      depth[i] = d; i++;
      while (i < n && src[i] !== q) { if (src[i] === "\\") { depth[i] = d; i++; } depth[i] = d; i++; }
      i++;
      continue;
    }
    if (c === "`") {
      depth[i] = d; i++;
      while (i < n) {
        if (src[i] === "\\") { i += 2; continue; }
        if (src[i] === "`") { i++; break; }
        // Inside a template hole it IS code again, but never top level: it belongs to the string.
        if (src[i] === "$" && src[i + 1] === "{") {
          let inner = 1; i += 2;
          while (i < n && inner > 0) {
            if (src[i] === "{") inner++;
            else if (src[i] === "}") inner--;
            i++;
          }
          continue;
        }
        i++;
      }
      continue;
    }
    if (c === "{") { code[i] = 1; depth[i] = d; d++; i++; continue; }
    if (c === "}") { d = d > 0 ? d - 1 : 0; code[i] = 1; depth[i] = d; i++; continue; }
    code[i] = 1;
    depth[i] = d;
    i++;
  }
  return { code, depth };
}

// `const` only, deliberately. A top-level `let` is usually an accumulator that the code mutates
// while it runs — `let tris = 0` — and offering a slider on one offers to rewrite a number that
// was never a setting, and that the next run overwrites anyway. A setting is written `const`.
const DECL = /(?:^|[;\n])[ \t]*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=[ \t]*/g;
const NUM = /^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/;
const HEX = /^0x[0-9a-fA-F]{3,8}/;
const STR = /^(['"])((?:[^\\]|\\.)*?)\1/;
const SKIP_NAME = /^(?:i|j|k|n|t|dt|_|e|el|ev|fn|cb|tmp|self|that)$/;

function decimalsOf(text: string): number {
  const dot = text.indexOf(".");
  if (dot < 0) return 0;
  return text.slice(dot + 1).replace(/[eE].*$/, "").length;
}

/** A workable slider range around a value the author already chose. Never a hard limit: the
 *  field always takes a typed number, so a wrong guess costs a keystroke, not the edit. */
export function rangeFor(v: number): { min: number; max: number; step: number } {
  if (!isFinite(v) || v === 0) return { min: -1, max: 1, step: 0.01 };
  const a = Math.abs(v);
  if (Number.isInteger(v) && a >= 2) return { min: 0, max: Math.ceil(a * 3), step: 1 };
  const mag = Math.floor(Math.log10(a));
  const step = Math.max(1e-5, Math.pow(10, mag - 2));
  const lo = v < 0 ? round(v * 3, 5) : 0;
  const hi = v < 0 ? round(a, 5) : round(v * 3, 5);
  return { min: lo, max: hi, step };
}

export const round = (v: number, dp = 4) => { const m = Math.pow(10, dp); return Math.round(v * m) / m; };

const looksColor = (name: string) => /col|colour|color|tint|hue|rgb/i.test(name);

function specFromLiteral(name: string, src: string, at: number, group?: string): ParamSpec | null {
  const rest = src.slice(at, at + 64);
  let m = HEX.exec(rest);
  if (m) {
    return {
      key: name, label: labelOf(name), kind: "color", from: "detected", group, literal: "hex",
      value: "#" + m[0].slice(2).padStart(6, "0").slice(-6),
      span: { start: at, end: at + m[0].length },
    };
  }
  m = NUM.exec(rest);
  if (m) {
    const v = parseFloat(m[0]);
    if (!isFinite(v)) return null;
    return {
      key: name, label: labelOf(name), kind: "number", from: "detected", group,
      value: v, decimals: decimalsOf(m[0]), ...rangeFor(v),
      span: { start: at, end: at + m[0].length },
    };
  }
  if (/^true\b/.test(rest) || /^false\b/.test(rest)) {
    const t = /^true\b/.test(rest);
    return {
      key: name, label: labelOf(name), kind: "bool", from: "detected", group, value: t,
      span: { start: at, end: at + (t ? 4 : 5) },
    };
  }
  m = STR.exec(rest);
  if (m) {
    const isCol = /^#[0-9a-fA-F]{3,8}$/.test(m[2]) || looksColor(name);
    return {
      key: name, label: labelOf(name), kind: isCol ? "color" : "text", from: "detected", group,
      literal: "quoted", value: m[2], span: { start: at + 1, end: at + 1 + m[2].length },
    };
  }
  return null;
}

/** SAIL_HEIGHT and sailHeight both become "Sail height", which is Blender's own capitalisation. */
export function labelOf(key: string): string {
  const base = key.split(".").pop() || key;
  const words = base
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Every top-level constant in the source that could be a knob, with the exact span of its value.
 *
 * Two shapes are read: a plain `const NAME = <literal>`, and a top-level object of literals, which
 * is how a well-written asset already groups its settings. An object contributes `NAME.key`, so
 * two objects can hold the same key without colliding.
 */
export function scanParams(src: string, limit = 200): ParamSpec[] {
  if (!src) return [];
  const { code, depth } = maskSource(src);
  const out: ParamSpec[] = [];
  const seen = new Set<string>();
  DECL.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = DECL.exec(src)) && out.length < limit) {
    const nameAt = m.index + m[0].indexOf(m[1]);
    if (!code[nameAt] || depth[nameAt] !== 0) continue;
    const name = m[1];
    if (SKIP_NAME.test(name) || name.length < 2 || seen.has(name)) continue;
    const at = m.index + m[0].length;
    if (src[at] === "{") {
      // An object of settings. Only literal members become knobs; a nested object or a call is
      // structure, not a setting, and pretending otherwise would put a slider on a function.
      const close = matchBrace(src, at, depth);
      if (close < 0) continue;
      const body = src.slice(at + 1, close);
      const KEY = /(?:^|[,{\n])[ \t]*(?:\/\/[^\n]*\n[ \t]*)?["']?([A-Za-z_$][\w$]*)["']?\s*:[ \t]*/g;
      let km: RegExpExecArray | null;
      while ((km = KEY.exec(body)) && out.length < limit) {
        const abs = at + 1 + km.index + km[0].length;
        const keyAt = at + 1 + km.index + km[0].indexOf(km[1]);
        if (!code[keyAt] || depth[keyAt] !== 1) continue;
        const spec = specFromLiteral(name + "." + km[1], src, abs, name);
        if (spec && !seen.has(spec.key)) { seen.add(spec.key); out.push(spec); }
      }
      continue;
    }
    const spec = specFromLiteral(name, src, at);
    if (spec) { seen.add(name); out.push(spec); }
  }
  return out;
}

function matchBrace(src: string, open: number, depth: Uint16Array): number {
  const want = depth[open];
  for (let i = open + 1; i < src.length; i++) if (src[i] === "}" && depth[i] === want) return i;
  return -1;
}

// ------------------------------------------------- parameters at a call site
//
// A scene is code that calls other assets: `buildLizard(THREE, { sailHeight: 1.2 })`. The object
// literal in that call is the settings of THAT INSTANCE — what Unreal shows as the exposed
// variables of a placed Blueprint, and Houdini as the parameters of an HDA dropped into a scene.
// Reading them out of the call gives every placed instance its own sliders with no contract at
// all, and the write-back is the same one-number edit a constant gets.
//
// Only calls to something the file IMPORTED are read. A call to a local helper is structure, and
// a slider on `lerp(a, { t: 0.5 })` would be noise.

const IMPORT = /\bimport\s+([^'";]+?)\s+from\s*['"][^'"]+['"]/g;
const DYN_IMPORT = /\b(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*await\s+import\s*\(/g;
const IDENT = /[A-Za-z_$][\w$]*/y;

/** Every local name an import binds: default, named (with `as`), namespace, and destructured
 *  from a dynamic import. */
export function importedNames(src: string): string[] {
  const names = new Set<string>();
  const clause = (c: string) => {
    const braces = c.match(/\{([^}]*)\}/);
    if (braces) {
      for (const part of braces[1].split(",")) {
        const m = part.trim().match(/(?:[\w$]+\s+as\s+)?([A-Za-z_$][\w$]*)$/);
        if (m) names.add(m[1]);
      }
    }
    const rest = c.replace(/\{[^}]*\}/, "");
    for (const m of rest.matchAll(/(?:\*\s+as\s+)?([A-Za-z_$][\w$]*)/g)) if (m[1] !== "as" && m[1] !== "type") names.add(m[1]);
  };
  for (const m of src.matchAll(IMPORT)) clause(m[1]);
  for (const m of src.matchAll(DYN_IMPORT)) clause(m[1]);
  return [...names];
}

/** The argument spans of a call whose `(` is at `open`, or null if it never closes. Masked
 *  characters are skipped whole, so a bracket inside a string cannot unbalance the walk. */
function callArgs(src: string, code: Uint8Array, open: number): Span[] | null {
  const args: Span[] = [];
  let level = 0;
  let start = open + 1;
  for (let i = open; i < src.length; i++) {
    if (!code[i]) continue;
    const ch = src[i];
    if (ch === "(" || ch === "[" || ch === "{") { level++; continue; }
    if (ch === ")" || ch === "]" || ch === "}") {
      level--;
      if (level === 0) {
        if (i > start && src.slice(start, i).trim()) args.push({ start, end: i });
        return args;
      }
      continue;
    }
    if (ch === "," && level === 1) { args.push({ start, end: i }); start = i + 1; }
  }
  return null;
}

/**
 * Every literal inside an object handed to an imported function, with the span of its value.
 *
 * The instance is labelled by the variable the call was assigned to (`const liz = build…`), and
 * when that variable is later given a `.name`, the label is the part the sliders belong to. An
 * unassigned call is numbered, which is stable for as long as the code around it is.
 */
export function scanCallParams(src: string, limit = 200): ParamSpec[] {
  if (!src) return [];
  const names = importedNames(src);
  if (!names.length) return [];
  const { code, depth } = maskSource(src);
  const out: ParamSpec[] = [];
  const seen = new Set<string>();
  const counts = new Map<string, number>();
  const callee = new RegExp("\\b(" + names.map((n) => n.replace(/\$/g, "\\$")).join("|") + ")(?:\\.[A-Za-z_$][\\w$]*)?\\s*\\(", "g");
  let m: RegExpExecArray | null;
  while ((m = callee.exec(src)) && out.length < limit) {
    if (!code[m.index]) continue;
    // `import x from` also matches `x(` nowhere, but `new X(` and `.X(` are not calls to the import.
    const before = src.slice(Math.max(0, m.index - 1), m.index);
    if (before === ".") continue;
    const open = m.index + m[0].length - 1;
    const args = callArgs(src, code, open);
    if (!args) continue;
    const fn = m[0].replace(/\s*\($/, "");
    const n = (counts.get(fn) || 0) + 1;
    counts.set(fn, n);
    // What the call was assigned to, if anything, and what that was then named.
    const lead = src.slice(Math.max(0, m.index - 120), m.index);
    const asg = /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?$/.exec(lead)
      || /(?:^|[;\n{])\s*([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?$/.exec(lead);
    const label = asg ? asg[1] : fn + "#" + n;
    let part: string | undefined;
    if (asg) {
      const named = new RegExp("\\b" + asg[1].replace(/\$/g, "\\$") + "\\.name\\s*=\\s*(['\"])([^'\"]+)\\1").exec(src.slice(open));
      if (named) part = named[2];
    }
    for (const a of args) {
      let s = a.start;
      while (s < a.end && /\s/.test(src[s])) s++;
      if (src[s] !== "{") continue;
      const close = matchBrace(src, s, depth);
      if (close < 0 || close > a.end) continue;
      const body = src.slice(s + 1, close);
      const want = depth[s] + 1;
      const KEY = /(?:^|[,{\n])[ \t]*(?:\/\/[^\n]*\n[ \t]*)?["']?([A-Za-z_$][\w$]*)["']?\s*:[ \t]*/g;
      let km: RegExpExecArray | null;
      while ((km = KEY.exec(body)) && out.length < limit) {
        const abs = s + 1 + km.index + km[0].length;
        // Judge the KEY's position, not the value's: a quoted value is masked as text, and it is
        // still a setting.
        const keyAt = s + 1 + km.index + km[0].indexOf(km[1]);
        if (!code[keyAt] || depth[keyAt] !== want) continue;
        const spec = specFromLiteral(label + "." + km[1], src, abs, label);
        if (!spec || seen.has(spec.key)) continue;
        seen.add(spec.key);
        if (part) spec.part = part;
        out.push(spec);
      }
    }
  }
  void IDENT;
  return out;
}

// ------------------------------------------------------------------ writing back
export function formatValue(spec: ParamSpec, v: ParamValue): string {
  if (spec.kind === "bool") return v ? "true" : "false";
  if (spec.kind === "color") {
    const hex = String(v).replace(/^#/, "").padStart(6, "0").slice(-6);
    // A source that wrote 0xRRGGBB gets 0xRRGGBB back; one that wrote "#rrggbb" keeps its quotes,
    // which the span already excludes, so only the characters between them change.
    return spec.literal === "quoted" ? "#" + hex : "0x" + hex;
  }
  // A choice is a string that happens to come from a list. It is NOT a number, even when one of
  // its options looks like one: "1e3" must be written as "1e3", never as 1000, and "shop" must
  // never fall through to the number branch below and quietly write back the old value.
  if (spec.kind === "text" || spec.kind === "choice") return String(v);
  const n = Number(v);
  if (!isFinite(n)) return String(spec.value);
  // The shortest exact spelling, capped at six places. Two wrong answers were tempting here and
  // both are worse: matching the decimal count of the author caps precision, so a file that says
  // 0.9 would round an edit of 1.45 down to 1.5 and silently discard it; and writing the raw
  // double puts 0.30000000000000004 in the file, which is the same value spelled unreadably.
  return String(round(n, 6));
}

/**
 * The write-back. Only spans whose value actually changed are touched, applied from the end of
 * the file backwards so every earlier offset stays valid, and nothing else in the file moves.
 *
 * A span is written only if the text still reads exactly as the scan found it. The file can
 * change under an open editor — an agent is often editing the same asset — and a stale offset
 * would write a number into the middle of a different statement.
 */
export function applyParams(src: string, values: Record<string, ParamValue>, specs: ParamSpec[]):
  { source: string; changed: string[]; skipped: string[] } {
  const hits = specs
    .filter((s) => s.span && Object.prototype.hasOwnProperty.call(values, s.key))
    .filter((s) => String(values[s.key]) !== String(s.value))
    .sort((a, b) => b.span!.start - a.span!.start);
  let out = src;
  const changed: string[] = [];
  const skipped: string[] = [];
  for (const s of hits) {
    const here = src.slice(s.span!.start, s.span!.end);
    if (!sameLiteral(s, here)) { skipped.push(s.key); continue; }
    out = out.slice(0, s.span!.start) + formatValue(s, values[s.key]) + out.slice(s.span!.end);
    changed.push(s.key);
  }
  return { source: out, changed: changed.reverse(), skipped };
}

/** Does the text at the span still say what the scan read there? Exported for the tests. */
export function sameLiteral(s: ParamSpec, text: string): boolean {
  if (s.kind === "number") return parseFloat(text) === Number(s.value);
  if (s.kind === "bool") return (text === "true") === Boolean(s.value);
  if (s.kind === "color") {
    const a = text.replace(/^0x/, "").replace(/^#/, "").toLowerCase();
    return a === String(s.value).replace(/^#/, "").toLowerCase();
  }
  // "text" and "choice" alike: the characters, exactly. A choice compared as a number would call
  // "1.0" and "1" the same option and let a stale span through.
  if (s.kind === "text" || s.kind === "choice") return text === String(s.value);
  return text === String(s.value);
}

/**
 * The edits as pasteable code, for anyone who would rather inline them than keep a sidecar.
 *
 * Part overrides need nothing but three, so they are written out plain. A modifier stack needs
 * the operations library, so that line imports it from the Studio — right for the dev loop and
 * for an agent, and a game that ships should copy forge-ops.js next to its own code instead.
 */
export function editsAsCode(e: Edits, rootVar = "root"): string {
  const out = [
    "// Manual edits made in the Studio editor. Applied by name, so a rebuild keeps them.",
    "for (const [name, o] of Object.entries(" + JSON.stringify(e.parts) + ")) {",
    "  const t = " + rootVar + ".getObjectByName(name);",
    "  if (!t) continue;",
    "  if (o.pos) t.position.set(o.pos[0], o.pos[1], o.pos[2]);",
    "  if (o.rot) t.rotation.set(o.rot[0], o.rot[1], o.rot[2]);",
    "  if (o.scale) t.scale.set(o.scale[0], o.scale[1], o.scale[2]);",
    "  if (o.hidden) t.visible = false;",
    "  if (o.color) (t.isLight ? t.color : t.material?.color)?.set(o.color);",
    "  for (const k of " + JSON.stringify([...OVERRIDE_NUMBERS]) + ") if (typeof o[k] === 'number' && k in t) t[k] = o[k];",
    "  if (typeof o.shadow === 'boolean') t.castShadow = o.shadow;",
    "  if (t.isCamera) t.updateProjectionMatrix();",
    "}",
  ];
  const w = e.world;
  if (w && (w.background !== undefined || w.fog !== undefined)) {
    out.push("const scene = " + rootVar + ".isScene ? " + rootVar + " : " + rootVar + ".getObjectByProperty('isScene', true);");
    if (w.background !== undefined) out.push("if (scene) scene.background = new THREE.Color(" + JSON.stringify(w.background) + ");");
    if (w.fog === null) out.push("if (scene) scene.fog = null;");
    else if (w.fog) {
      out.push(w.fog.type === "exp2"
        ? "if (scene) scene.fog = new THREE.FogExp2(" + JSON.stringify(w.fog.color) + ", " + (w.fog.density ?? 0.05) + ");"
        : "if (scene) scene.fog = new THREE.Fog(" + JSON.stringify(w.fog.color) + ", " + (w.fog.near ?? 1) + ", " + (w.fog.far ?? 100) + ");");
    }
  }
  const mods = e.mods.filter((m) => !m.off);
  if (mods.length) {
    out.push(
      "const { makeOps, applyEdits } = await import('http://127.0.0.1:8777/forge-ops.js');",
      "applyEdits(" + rootVar + ", { parts: {}, mods: " + JSON.stringify(mods) + " }, makeOps(THREE));",
    );
  }
  return out.join("\n");
}

/** The path the sidecar of an asset takes. Beside the file, so moving the asset moves the edits. */
export function sidecarFor(assetPath: string, key = ""): string {
  const base = assetPath.replace(/\.(m?[jt]sx?)$/i, "");
  // ONE FILE, MANY ASSETS. `proplib.ts` holds fifty-five props; without the key they would all
  // write to the same sidecar and the last one edited would be the only one kept.
  const tag = String(key || "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return base + (tag ? "." + tag : "") + ".edits.json";
}

/**
 * The viewport's three, as a reference the loader endpoint may be handed — or "" if it is not one.
 *
 * The glTF loader has to import the EXACT module the viewport imported: module identity is the URL
 * string, and a second copy of three makes materials the first one's renderer throws on. The
 * server splices this into JavaScript, so it takes three shapes only: a path on this origin, our
 * own absolute URL folded to its path, or a loopback dev server the viewport already imported
 * from. Anything else is refused here, before it is sent.
 */
export function loaderThreeRef(url: string, base: string): string {
  const u = String(url || "").trim();
  if (!u) return "";
  if (u.startsWith("/") && !u.startsWith("//")) return u;
  try {
    const a = new URL(u, base), b = new URL(base);
    if (a.protocol !== "http:" && a.protocol !== "https:") return "";
    if (a.origin === b.origin) return a.pathname + a.search;
    if (["127.0.0.1", "localhost", "[::1]"].includes(a.hostname)) return a.href;
    return "";
  } catch { return ""; }
}
