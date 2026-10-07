/* OPENING ONE OF A GAME'S OWN ASSETS — the single convention, used in both places.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS FILE EXISTS AT ALL
 *
 * There were two implementations of "call the game's code and get a thing": one in the editor, in
 * TypeScript, and one in the backend's thumbnail job, as a Python string of JavaScript. They
 * disagreed three separate times, and every disagreement looked like a rendering bug — five blank
 * thumbnails that reported success, a viewport that said "no builder beside NATURE", a preview of
 * the right asset built by the wrong function. A convention held in two places is not a
 * convention. This is the one place, bundled to `dist/asset-open.js` for the page that needs it
 * over HTTP and imported directly by the editor.
 *
 * ---------------------------------------------------------------------------
 * WHAT MAKES IT HARD, IN ORDER
 *
 * 1. WHICH SERVER. A workspace holds three games on three ports. `rot-rush/src/render/proplib.ts`
 *    exists on 5179 and on neither of the others, and asking the wrong one gives a 404 that reads
 *    exactly like a missing file.
 *
 * 2. THE TABLE IS OFTEN NOT EXPORTED. `NATURE`, `FX` and `SKY` are module-private consts that get
 *    spread into one exported `PROPS`. Indexing `m.NATURE[3]` finds nothing, for ever. An entry
 *    knows its own id, so it is looked for BY IDENTITY across everything the module exports.
 *
 * 3. THE BUILDER IS OFTEN THE ENTRY. `PropDef.build(params)` is a method on the spec itself, not
 *    a `buildProp(def)` beside it. Guessing at module-level names found `buildCreature`, which
 *    wants a PlayCanvas device and returns a mesh with no vertices in it.
 *
 * 4. THE ARGUMENT IS NOT OPTIONAL. `build({ seed, s, tint })` needs `s` to be the biome's colour
 *    script or every colour comes out black. See `standIn` — the values are recorded, then the
 *    real object is looked for among the exports of the file's own imports.
 *
 * 5. WHAT COMES BACK IS USUALLY NOT A MESH. This game's props and creatures are `Prop[]` — plain
 *    box descriptors that its renderer bakes into merged buckets. They are the asset. `toObject`
 *    turns them into geometry so they can be looked at and moved.
 */

/** One row of the asset index, as much of it as opening needs. */
export interface AssetRef {
  type?: string;
  name?: string;
  /** Project-relative source file, e.g. `rot-rush/src/render/proplib.ts`. */
  file?: string;
  /** The same file's absolute path on disk, when the index knew it. Tried first, through
   *  Vite's `/@fs/`, which beats guessing where a dev server mounted it. */
  path?: string;
  /** The game folder inside the workspace that owns the file, e.g. `rot-rush`. */
  root?: string;
  /** The export to call (code), or the table the entry came from (spec). */
  export?: string;
  table?: string;
  /** The entry's own id — `nature.tree`. This is what finds it. */
  key?: string;
  index?: number;
  builder?: string;
  /** A model file this entry points at, project-relative — resolved by the index. */
  model?: string;
  /** The Studio's own origin, e.g. `http://127.0.0.1:8777`. When the index knew the absolute
   *  path, this is the LAST import candidate — the only one that can reach a file living outside
   *  every game's dev-server root, which Vite answers 403 to and rightly so. */
  studio?: string;
  /** The file's own imports, project-relative, for finding real argument values. */
  deps?: string[];
}

export interface OpenEnv {
  /** The three namespace the viewport is holding, when the page has one. */
  THREE?: any;
  /** Bases to try in order: the owning game's dev server first. */
  bases?: string[];
  /** PlayCanvas, when the page has it. */
  pc?: any;
  /** Whatever the page calls a graphics device — a pc device, or a three renderer. */
  device?: any;
  /** Cap on how many descriptor parts become meshes. */
  maxParts?: number;
  /** How THIS page loads a .glb. Half the creatures in a real game are files, and no search of
   *  the module's functions will ever find one — but the entry names it. The loader stays with
   *  the caller because it is the one thing that is genuinely per-page: the editor decodes
   *  through the Studio, and a game's own page uses its own engine's loader. */
  loadModel?: (url: string) => Promise<any>;
}

export interface OpenResult {
  object: any;
  /** How it was found, in words, for a person reading a failure. */
  how: string;
  /** Everything that was tried and did not work. */
  tried: string[];
  parts: number;
}

// ---------------------------------------------------------------------------
// 1. Importing from the game that owns the file
// ---------------------------------------------------------------------------

const clean = (s: string) => String(s || "").replace(/\\/g, "/").replace(/^\/+/, "");

/** Every URL worth trying for one project-relative path, best first.
 *
 *  `strip` is the game folder that owns the file. Its own dev server is rooted THERE, so
 *  `rot-rush/src/render/proplib.ts` is `src/render/proplib.ts` to it — and that is the first
 *  thing asked for. The rest of the ladder stays, because a workspace-level server wants the
 *  whole path and nothing in a URL says which kind of server answered. */
export function importCandidates(rel: string, bases: string[], strip = "", abs = "",
                                 studio = ""): string[] {
  const parts = clean(rel).split("/").filter(Boolean);
  const cut = clean(strip).split("/").filter(Boolean);
  let head = 0;
  while (head < cut.length && parts[head] && parts[head].toLowerCase() === cut[head].toLowerCase()) head++;
  const order: number[] = [];
  // ALWAYS the head, 0 included. `if (head)` skipped it for a game at the project root — every new
  // game — and the loop below then skipped index 0 too, so `src/assets.js` was never asked of the
  // game's own server: only `assets.js`, the Vite-only /@fs/ and the Studio were tried.
  order.push(head);
  for (let i = 0; i < parts.length; i++) if (i !== head) order.push(i);
  const out: string[] = [];
  const roots = bases.length ? bases : [""];
  // THE ABSOLUTE PATH FIRST, when the index knew it. Vite mounts every file inside its allowed
  // root under `/@fs/` and transpiles TypeScript on the way out, so there is nothing left to
  // guess. Measured on the running rot-rush server: `/@fs/<abs>/src/render/proplib.ts` answered
  // 200 text/javascript while all four guesses below failed. A file outside the root answers
  // 403, which is why the guesses stay.
  if (abs) {
    const p = String(abs).replace(/\\/g, "/");
    for (const b of roots) {
      const u = String(b || "").replace(/\/+$/, "") + "/@fs/" + encodeURI(p);
      if (!out.includes(u)) out.push(u);
    }
  }
  for (const b of roots) {
    const base = String(b || "").replace(/\/+$/, "");
    for (const i of order) {
      const u = base + "/" + parts.slice(i).join("/");
      if (!out.includes(u)) out.push(u);
    }
  }
  // AND THE STUDIO, LAST. A file that belongs to no game belongs to no dev server: both of this
  // workspace's servers answered `200 text/html` for it — their SPA fallback — and Vite's own
  // `/@fs/` answered 403, because the file is outside its allowed root. Last, so a file a game
  // really owns still comes from that game, with that game's transpiling and its own imports.
  if (studio && abs && /\.(m?js)$/i.test(abs)) {
    const u = String(studio).replace(/\/+$/, "") + "/api/engine/source?path="
      + encodeURIComponent(String(abs).replace(/\\/g, "/"));
    if (!out.includes(u)) out.push(u);
  }
  return out;
}

/**
 * Which engine the Edit tab builds a thing in.
 *
 * A LIBRARY ENTRY IS BUILT BY `openAsset`, AND `openAsset` BUILDS INTO THREE. A model loads through
 * a three loader; a spec or a code row comes back as a three object, box descriptors included,
 * whatever engine the game around it ships with. So every Library entry opens in the editor's own
 * three viewport. Only a plain SOURCE FILE of a PlayCanvas game — code that calls `pc.*` itself —
 * needs the hidden PlayCanvas studio.
 *
 * This decision used to be inline in the Engine page, and it was wrong twice in the same way: a
 * .glb in a PlayCanvas game opened to an empty grid, which was fixed for models only; then every
 * spec and code row of the same games did the same thing, because they were sent to a PlayCanvas
 * studio whose context has no `buildAsset` for the Library's snippet to call.
 */
export function editEngineFor(asset: { type?: string; model?: string } | null | undefined,
                              gameIsPlayCanvas: boolean): "three" | "playcanvas" {
  if (asset) return "three";
  return gameIsPlayCanvas ? "playcanvas" : "three";
}

/** Import one of the game's modules, from whichever of its servers actually has it. */
export async function importAny(rel: string, bases: string[], strip = "",
                                abs = "", studio = ""): Promise<any> {
  const tried: string[] = [];
  let first = "";
  // A PAGE REMEMBERS A FAILED IMPORT FOR AS LONG AS IT LIVES. The same URL rejects instantly the
  // second time, even after the file is unquestionably there — and the thumbnail pass runs many
  // times in one tab. Measured: 60 of the workspace's assets reported "could not import, tried 5
  // URLs" while both of those URLs imported by hand, in that same tab, under a fresh query. The
  // first attempt had failed months of milliseconds earlier, when the tab was still on the
  // neighbouring game's origin, and nothing since could ever succeed.
  //
  // A distinct query is a distinct fetch and a distinct module instance, which for building one
  // asset is exactly what is wanted. The plain URL is tried first so a page with nothing
  // remembered pays nothing.
  const stamp = "open=" + Date.now().toString(36);
  for (const u of importCandidates(rel, bases, strip, abs, studio)) {
    tried.push(u);
    for (const t of [u, u + (u.indexOf("?") < 0 ? "?" : "&") + stamp]) {
      try {
        return await import(/* @vite-ignore */ t);
      } catch (e: any) {
        if (!first) first = String(e?.message || e).slice(0, 160);
      }
    }
  }
  // THE REASON FIRST, THE URLs AFTER. Every consumer truncates this message, and the URL list
  // is long enough that the truncation always ate the one part that says WHY — leaving "tried 5
  // URLs" and nothing to act on. A module served raw by the Studio, for instance, fails on its
  // own bare specifiers, and that is a completely different problem from a 404.
  throw new Error("could not import " + rel + ": " + first + " — tried " + tried.length
    + " URLs (" + tried.slice(0, 4).join(", ") + (tried.length > 4 ? ", …" : "") + ")");
}

// ---------------------------------------------------------------------------
// 2. Finding the entry, by identity
// ---------------------------------------------------------------------------

const ID_KEYS = ["id", "key", "kind", "type", "slug", "name"];
const isObj = (v: any) => !!v && typeof v === "object" && !Array.isArray(v);
const plain = (v: any) => isObj(v) && (v.constructor === Object || !v.constructor);

const idsOf = (o: any): string[] =>
  isObj(o) ? ID_KEYS.map((k) => o[k]).filter((v) => typeof v === "string").map((v) => v.toLowerCase()) : [];

function matches(entry: any, want: string): boolean {
  const w = want.toLowerCase();
  return idsOf(entry).indexOf(w) >= 0;
}

/** The one entry this asset names, wherever the module happens to keep it. */
export function findSpec(m: any, ref: AssetRef): { spec: any; how: string } {
  const want = String(ref.key || ref.name || "");
  const tables: Array<[string, any[]]> = [];
  const named = ref.table && Array.isArray(m?.[ref.table]) ? [String(ref.table), m[ref.table]] as [string, any[]] : null;
  if (named) tables.push(named);
  for (const k of Object.keys(m || {})) {
    const v = (m as any)[k];
    if (Array.isArray(v) && v.length && isObj(v[0]) && (!named || k !== named[0])) tables.push([k, v]);
  }
  // BY IDENTITY FIRST, and across every table. `NATURE` is a private const spread into the
  // exported `PROPS`, so the table the index recorded does not exist at runtime — but the entry
  // still knows it is `nature.tree`, and that is true in whichever array it ended up in.
  if (want) {
    for (const [k, arr] of tables) {
      for (const e of arr) if (matches(e, want)) return { spec: e, how: "found " + want + " in " + k };
    }
    // A registry keyed by id rather than an array.
    for (const k of Object.keys(m || {})) {
      const v = (m as any)[k];
      if (plain(v) && isObj(v[want])) return { spec: v[want], how: "found " + want + " in " + k };
    }
  }
  // Then the recorded position, which is right whenever the table really is exported.
  const idx = Number(ref.index);
  if (named && idx >= 0 && idx < named[1].length) {
    return { spec: named[1][idx], how: named[0] + "[" + idx + "]" };
  }
  if (tables.length === 1 && idx >= 0 && idx < tables[0][1].length) {
    return { spec: tables[0][1][idx], how: tables[0][0] + "[" + idx + "]" };
  }
  return { spec: null, how: "" };
}

// ---------------------------------------------------------------------------
// 3. Finding the builder
// ---------------------------------------------------------------------------

const BUILD_NAME = /^(build|make|create|spawn|gen|generate|construct|assemble|forge|mesh|model|parts|geometry|shape)/i;
const NOT_BUILD = /^(load|preload|dispose|update|tick|animate|render|init|setup|main|start|stop|resize|on[A-Z]|use[A-Z]|is[A-Z]|has[A-Z]|to[A-Z]|parse|format|save|register|search|find|bounds|place|aabb|height|width)/;

export interface Builder { fn: Function; self: any; name: string; wantsSpec: boolean; }

/** Everything in this module that could build this asset, most likely first. */
/** An ES class. Calling one without `new` is a guaranteed TypeError, so it is never a guess worth
 *  making: `class Actors` was the first line of an error about an asset called boxMesh. */
export function isClass(fn: any): boolean {
  try { return typeof fn === "function" && /^class[\s{]/.test(Function.prototype.toString.call(fn)); }
  catch { return false; }
}

export function findBuilders(m: any, ref: AssetRef, spec: any): Builder[] {
  const out: Builder[] = [];
  const push = (fn: any, self: any, name: string, wantsSpec: boolean) => {
    if (typeof fn !== "function" || out.some((b) => b.fn === fn && b.self === self)) return;
    out.push({ fn, self, name, wantsSpec });
  };
  // THE ENTRY'S OWN METHOD, first and by a distance. `PropDef.build(params)` is the asset; a
  // module-level function with a promising name is a guess.
  if (isObj(spec)) {
    for (const k of Object.keys(spec)) {
      if (typeof spec[k] === "function" && BUILD_NAME.test(k)) push(spec[k], spec, "." + k, false);
    }
  }
  const asked = String(ref.builder || "");
  if (asked && typeof m?.[asked] === "function") push(m[asked], m, asked, true);
  // For a plain code asset the index recorded the export to call; it is not a guess.
  if (ref.type !== "spec" && ref.export && typeof m?.[ref.export] === "function") {
    push(m[ref.export], m, ref.export, false);
  }
  if (ref.type !== "spec" && ref.name && typeof m?.[ref.name] === "function") push(m[ref.name], m, ref.name, false);
  if (typeof m?.default === "function") push(m.default, m, "default", ref.type === "spec");
  const fns = Object.keys(m || {}).filter((k) => typeof (m as any)[k] === "function");
  // The GUESSES skip classes: see `isClass`. A class the index named on purpose was pushed above.
  for (const k of fns) if (BUILD_NAME.test(k) && !NOT_BUILD.test(k) && !isClass((m as any)[k])) push((m as any)[k], m, k, true);
  for (const k of fns) if (!NOT_BUILD.test(k) && !isClass((m as any)[k]) && (m as any)[k].length <= 3) push((m as any)[k], m, k, true);
  return out;
}

// ---------------------------------------------------------------------------
// 4. The argument a builder is asking for
// ---------------------------------------------------------------------------

const COLOURY = /colou?r|tint|hue|paint|deck|rim|dome|accent|sky|fog|ambient|shade|glow|emissiv|body|belly|skin|fur|trim|light|dark|pal/i;
const NUMBERY = /scale|size|radius|height|width|depth|length|thick|count|speed|mass|power|amount|level|tier|rate/i;
const WORDY = /name|label|title|text|slug|kind|type|shape|anim|style|variant$/i;
const SEEDY = /seed|rand|noise/i;

let _hash = 0;
const hashOf = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0);
};
/** A pastel, so a stand-in colour reads as a colour rather than as a bug. */
const pastel = (s: string) => {
  const h = hashOf(s);
  const r = 150 + (h & 63), g = 150 + ((h >> 6) & 63), b = 150 + ((h >> 12) & 63);
  return (r << 16) | (g << 8) | b;
};

export interface Recorded { keys: Map<string, Set<string>>; }

/**
 * An object that answers any question, and remembers what was asked.
 *
 * A builder that reads `p.s.deck` cannot be called with `{}` — it gets `undefined`, does bit
 * arithmetic on it, and returns a prop painted pure black, which is a failure that renders. So the
 * first call is made with this, purely to find out what the code wants; then `resolve` looks for
 * the real object among the exports of the file's own imports and the call is made again for
 * real. What the game's own colour script says beats anything invented here.
 */
export function standIn(rec: Recorded, path = ""): any {
  const cache = new Map<string, any>();
  const at = (k: string) => (path ? path + "." + k : k);
  if (!rec.keys.has(path)) rec.keys.set(path, new Set());
  const target: any = {};
  return new Proxy(target, {
    get(_t, prop: any) {
      if (typeof prop === "symbol") {
        if (prop === Symbol.toPrimitive) return (hint: string) => (hint === "string" ? "" : pastel(path));
        if (prop === Symbol.iterator) return undefined;
        return undefined;
      }
      const k = String(prop);
      if (k === "then" || k === "constructor" || k === "toJSON") return undefined;
      rec.keys.get(path)!.add(k);
      if (cache.has(k)) return cache.get(k);
      let v: any;
      if (SEEDY.test(k)) v = 7;
      else if (COLOURY.test(k)) v = pastel(at(k));
      else if (NUMBERY.test(k)) v = 1;
      else if (WORDY.test(k)) v = "";
      else v = standIn(rec, at(k));
      cache.set(k, v);
      return v;
    },
    has() { return true; },
    ownKeys() { return Array.from(rec.keys.get(path) || []); },
    getOwnPropertyDescriptor() { return { enumerable: true, configurable: true, value: undefined }; },
  });
}

/** Every object in these modules that could be a real argument — exports, and one level in. */
function candidates(mods: any[]): Array<{ name: string; value: any }> {
  const out: Array<{ name: string; value: any }> = [];
  for (const m of mods) {
    if (!m) continue;
    for (const k of Object.keys(m)) {
      const v = (m as any)[k];
      if (!plain(v)) continue;
      out.push({ name: k, value: v });
      for (const k2 of Object.keys(v)) {
        if (plain(v[k2])) out.push({ name: k + "." + k2, value: v[k2] });
      }
    }
  }
  return out;
}

/**
 * The argument, rebuilt from what the first call actually read.
 *
 * Nested objects get the real thing when the game has one — `SCRIPT.bubblegum` has every key the
 * prop asked its colour script for, so the candy tree comes out the colour the game paints it.
 * Leaves at the top level are the CALLER's choices and default to nothing, because `?? fallback`
 * is how this kind of code is written and a made-up override would be honoured over the game's.
 */
export function resolveArg(rec: Recorded, mods: any[]): any {
  const cands = candidates(mods);
  const build = (path: string): any => {
    const keys = rec.keys.get(path);
    if (!keys || !keys.size) return {};
    const out: any = {};
    for (const k of keys) {
      const child = path ? path + "." + k : k;
      const childKeys = rec.keys.get(child);
      if (childKeys && childKeys.size) {
        // Full coverage or nothing. A candidate missing one of the keys the code read would
        // hand back `undefined` where a colour belongs, and undefined through bit arithmetic is
        // black — a failure that renders, which is the worst kind. Among the ones that do cover
        // it, the richest wins: a real data record carries a whole biome, a coincidence carries
        // one field.
        let best: any = null;
        let bestSize = -1;
        for (const c of cands) {
          let ok = true;
          for (const want of childKeys) if (c.value[want] === undefined) { ok = false; break; }
          if (!ok) continue;
          const size = Object.keys(c.value).length;
          if (size > bestSize) { bestSize = size; best = c.value; }
        }
        out[k] = best || build(child);
        continue;
      }
      if (SEEDY.test(k)) out[k] = 7;
      else if (path) out[k] = COLOURY.test(k) ? pastel(child) : NUMBERY.test(k) ? 1 : WORDY.test(k) ? "" : null;
      else out[k] = null;
    }
    return out;
  };
  return build("");
}

// ---------------------------------------------------------------------------
// 5. Whatever came back, as something you can look at
// ---------------------------------------------------------------------------

const num = (v: any, d = 0) => (typeof v === "number" && isFinite(v) ? v : d);
const pick = (o: any, ...names: string[]) => {
  for (const n of names) if (typeof o?.[n] === "number") return o[n];
  return undefined;
};
const triple = (v: any): [number, number, number] | null => {
  if (Array.isArray(v) && v.length >= 3) return [num(v[0]), num(v[1]), num(v[2])];
  if (isObj(v) && typeof v.x === "number") return [num(v.x), num(v.y), num(v.z)];
  return null;
};

/** Does this plain object describe a box, a ball or a bar in space? */
export function isPart(o: any): boolean {
  if (!isObj(o) || o.isObject3D) return false;
  const hasPos = typeof o.x === "number" || Array.isArray(o.pos) || Array.isArray(o.position) || Array.isArray(o.p);
  const hasSize = ["hx", "w", "sx", "r", "radius", "size", "scale", "half", "d", "h"].some((k) => o[k] !== undefined);
  return hasPos && hasSize;
}

/** The size, position, turn, colour and shape one descriptor asks for, whatever it calls them.
 *
 *  Half-extents when the object says `hx` (that is what the `h` means), full extents when it says
 *  `w` or `size`. That is the one real ambiguity, and both conventions name themselves clearly
 *  enough to tell apart — which is why this reads the names rather than guessing at the numbers. */
export function readPart(p: any) {
  const pos = triple(p.pos) || triple(p.position) || triple(p.p) || [num(p.x), num(p.y), num(p.z)];
  const half = p.hx !== undefined || p.hy !== undefined || p.hz !== undefined;
  const sz = triple(p.size) || triple(p.half) || triple(p.scale);
  let w: number, h: number, d: number;
  if (half) { w = num(p.hx, 0.1) * 2; h = num(p.hy, 0.1) * 2; d = num(p.hz, 0.1) * 2; }
  else if (sz) { w = sz[0]; h = sz[1]; d = sz[2]; }
  else {
    w = num(pick(p, "w", "sx", "width"), NaN);
    h = num(pick(p, "h", "sy", "height"), NaN);
    d = num(pick(p, "d", "sz", "depth"), NaN);
  }
  const r = num(pick(p, "r", "radius"), NaN);
  const named = String(p.shape || p.geo || p.prim || "").toLowerCase();
  const shape = named.startsWith("sph") || named === "ball" || (!isFinite(w) && isFinite(r)) ? "sphere"
    : named.startsWith("cyl") || named.startsWith("cap") ? "cyl"
      : named.startsWith("con") ? "cone" : "box";
  const rot = triple(p.rotation) || triple(p.rot)
    // A single `rot` is yaw in this codebase, and yaw is what every prop uses.
    || [num(p.rx), num(p.rot ?? p.ry ?? p.yaw), num(p.rz)] as [number, number, number];
  return {
    shape, pos, rot,
    w: Math.abs(num(w, 0.2)) || 0.02, h: Math.abs(num(h, 0.2)) || 0.02, d: Math.abs(num(d, 0.2)) || 0.02,
    r: isFinite(r) ? r : Math.max(0.02, num(w, 0.4) / 2),
    color: num(p.color ?? p.colour ?? p.c ?? p.tint, 0xcccccc) >>> 0,
    emissive: num(p.emissive, 0),
    name: String(p.name || p.id || shape || "part"),
  };
}

/** How to make a thing, in whichever engine the page is holding.
 *
 *  The editor's viewport is three; the thumbnail renderer runs inside the GAME's page, and half
 *  these games are PlayCanvas with no three anywhere. Same descriptors, same reader, two makers —
 *  rather than two copies of the reader, which is how the conventions drifted apart before. */
export interface Maker {
  group(name: string): any;
  mesh(part: ReturnType<typeof readPart>): any;
  fromGeometry(geo: any): any;
  addTo(parent: any, child: any): void;
  isNode(v: any): boolean;
  isGeometry(v: any): boolean;
}

export function threeMaker(T: any): Maker {
  const cache = new Map<number, any>();
  const mat = (c: number, e: number) => {
    const key = c * 2 + (e > 0 ? 1 : 0);
    let m = cache.get(key);
    if (!m) {
      m = new T.MeshStandardMaterial({
        color: c, roughness: e > 0 ? 0.4 : 0.72, metalness: 0.02,
        emissive: e > 0 ? c : 0x000000, emissiveIntensity: e > 0 ? Math.min(1.4, e) : 0,
      });
      cache.set(key, m);
    }
    return m;
  };
  return {
    group: (name) => { const g = new T.Group(); g.name = name; return g; },
    fromGeometry: (geo) => new T.Mesh(geo, mat(0xbfc6cf, 0)),
    isNode: (v) => !!v?.isObject3D,
    isGeometry: (v) => !!v?.isBufferGeometry,
    addTo: (parent, child) => { parent.add(child); },
    mesh(part) {
      const geo = part.shape === "sphere" ? new T.SphereGeometry(part.r, 20, 14)
        : part.shape === "cyl" ? new T.CylinderGeometry(part.r, part.r, part.h, 16)
          : part.shape === "cone" ? new T.ConeGeometry(part.r, part.h, 16)
            : new T.BoxGeometry(part.w, part.h, part.d);
      const m = new T.Mesh(geo, mat(part.color, part.emissive));
      m.position.set(part.pos[0], part.pos[1], part.pos[2]);
      m.rotation.set(part.rot[0], part.rot[1], part.rot[2]);
      m.castShadow = true;
      m.receiveShadow = true;
      m.name = part.name;
      return m;
    },
  };
}

export function pcMaker(pc: any): Maker {
  const cache = new Map<number, any>();
  const mat = (c: number, e: number) => {
    const key = c * 2 + (e > 0 ? 1 : 0);
    let m = cache.get(key);
    if (!m) {
      m = new pc.StandardMaterial();
      const col = new pc.Color(((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255);
      m.diffuse = col;
      if (e > 0) { m.emissive = col; m.emissiveIntensity = Math.min(1.4, e); }
      m.update();
      cache.set(key, m);
    }
    return m;
  };
  const PRIM: Record<string, string> = { box: "box", sphere: "sphere", cyl: "cylinder", cone: "cone" };
  return {
    group: (name) => new pc.Entity(name),
    fromGeometry: () => null,
    isNode: (v) => !!v && (typeof v.addChild === "function") && v.enabled !== undefined,
    isGeometry: () => false,
    addTo: (parent, child) => { parent.addChild(child); },
    mesh(part) {
      const e = new pc.Entity(part.name);
      e.addComponent("render", { type: PRIM[part.shape] || "box" });
      e.render.material = mat(part.color, part.emissive);
      // A PlayCanvas primitive is one unit across, so its scale IS its size.
      if (part.shape === "box") e.setLocalScale(part.w, part.h, part.d);
      else if (part.shape === "sphere") e.setLocalScale(part.r * 2, part.r * 2, part.r * 2);
      else e.setLocalScale(part.r * 2, part.h, part.r * 2);
      e.setLocalPosition(part.pos[0], part.pos[1], part.pos[2]);
      const deg = 180 / Math.PI;
      e.setLocalEulerAngles(part.rot[0] * deg, part.rot[1] * deg, part.rot[2] * deg);
      return e;
    },
  };
}

/** The maker for whatever engine this page actually has. */
export function makerFor(env: { THREE?: any; pc?: any }): Maker | null {
  if (env.THREE) return threeMaker(env.THREE);
  if (env.pc) return pcMaker(env.pc);
  return null;
}

/** Anything a builder can return, as one node — or null when it truly made nothing. */
export function toObject(value: any, engine: any, maxParts = 4000): { object: any; parts: number } {
  // `engine` is a three namespace, a Maker, or `{THREE, pc}`. Called three ways in three places,
  // and the alternative was three call sites that each had to know which.
  const mk: Maker | null = engine && typeof engine.mesh === "function" && typeof engine.group === "function"
    ? engine as Maker
    : engine && (engine.THREE || engine.pc) ? makerFor(engine) : engine ? threeMaker(engine) : null;
  if (!mk) return { object: null, parts: 0 };
  return convert(value, mk, maxParts);
}

function convert(value: any, mk: Maker, maxParts: number): { object: any; parts: number } {
  if (value == null) return { object: null, parts: 0 };
  if (mk.isNode(value)) return { object: value, parts: 1 };
  if (mk.isGeometry(value)) {
    const o = mk.fromGeometry(value);
    return o ? { object: o, parts: 1 } : { object: null, parts: 0 };
  }
  // A wrapper: `{ parts: [...] }`, `{ props: [...] }`, `{ object: mesh }`.
  if (!Array.isArray(value) && isObj(value)) {
    for (const k of ["object", "mesh", "group", "root", "scene", "entity", "parts", "props", "boxes", "pieces", "items"]) {
      if (value[k] != null) {
        const got = convert(value[k], mk, maxParts);
        if (got.object) return got;
      }
    }
  }
  const list: any[] = Array.isArray(value) ? value : [value];
  const group = mk.group("asset");
  let parts = 0;
  for (const item of list) {
    if (item == null) continue;
    if (mk.isNode(item)) { mk.addTo(group, item); parts++; continue; }
    if (mk.isGeometry(item)) {
      const o = mk.fromGeometry(item);
      if (o) { mk.addTo(group, o); parts++; }
      continue;
    }
    if (!isPart(item)) continue;
    if (parts >= maxParts) break;
    mk.addTo(group, mk.mesh(readPart(item)));
    parts++;
  }
  return parts ? { object: group, parts } : { object: null, parts: 0 };
}

// ---------------------------------------------------------------------------
// 6. The whole thing, in one call
// ---------------------------------------------------------------------------

/** Import the file, find the entry, call whatever builds it, and hand back geometry. */
export async function openAsset(ref: AssetRef, env: OpenEnv): Promise<OpenResult> {
  const bases = (env.bases || []).slice();
  const tried: string[] = [];
  const strip = String(ref.root || "");
  const mk = makerFor(env);

  // A FILE IS LOADED, NOT BUILT — and it is checked for first, because an entry that names a .glb
  // usually has no builder at all and searching for one only produces a confident wrong answer.
  const direct = String(ref.model || (ref.type === "model" ? ref.file : "") || "");
  if (direct && env.loadModel) {
    const o = await env.loadModel(direct);
    if (o) return { object: o, how: "loaded " + direct, parts: 1, tried };
    tried.push(direct + " did not load");
  }

  const m = await importAny(String(ref.file || ""), bases, strip, String(ref.path || ""),
                            String(ref.studio || ""));
  const deps: any[] = [];
  for (const d of (ref.deps || []).slice(0, 8)) {
    try { deps.push(await importAny(d, bases, strip)); } catch { tried.push("dep " + d + " did not import"); }
  }
  // An entry that points at a model file, found once the module is in hand.
  if (env.loadModel && mk) {
    const { spec } = findSpec(m, ref);
    const url = modelUrlOf(spec);
    if (url) {
      const o = await env.loadModel(url);
      if (o) return { object: o, how: "loaded " + url, parts: 1, tried };
      tried.push(url + " did not load");
    }
  }
  return buildFrom(m, ref, env, deps, tried);
}

/** The model file one entry points at, whatever field it uses, or "". */
export function modelUrlOf(o: any): string {
  if (!isObj(o)) return "";
  for (const k of ["model", "glb", "gltf", "url", "src", "file", "mesh", "asset"]) {
    const v = (o as any)[k];
    if (typeof v === "string" && /\.(glb|gltf)(\?|$)/i.test(v)) return v;
  }
  return "";
}

/** The half that needs no network: a module already in hand, built into geometry. */
export function buildFrom(m: any, ref: AssetRef, env: OpenEnv, deps: any[] = [], tried: string[] = []): OpenResult {
  const mk = makerFor(env);
  if (!mk) {
    return { object: null, how: "", parts: 0, tried: tried.concat("this page has neither three nor PlayCanvas loaded") };
  }
  const maxParts = env.maxParts || 4000;
  const wantsSpec = ref.type === "spec" || !!ref.key || Number(ref.index) >= 0;
  const { spec, how } = wantsSpec ? findSpec(m, ref) : { spec: null, how: "" };
  if (wantsSpec && !spec && ref.key) tried.push("no entry called " + ref.key + " in " + (ref.file || "the module"));
  // THE REAL REASON FIRST. The index can record a function the module never exports — a private
  // helper like `function boxMesh(device, ...)` — and the search then falls through to whatever
  // else the module offers, so the banner named a different function entirely.
  if (ref.type !== "spec" && ref.export && typeof m?.[ref.export] !== "function") {
    tried.push(ref.export + " is not exported by " + (ref.file || "the module")
      + " - only what a module exports can be built from outside it");
  }
  const builders = findBuilders(m, ref, spec);
  if (!builders.length) {
    return { object: null, how: "", parts: 0, tried: tried.concat("nothing in " + (ref.file || "the module") + " is a function that could build this") };
  }
  const mods = [m, ...deps];
  for (const b of builders.slice(0, 8)) {
    // Two calls: one with a stand-in to learn what the argument has to be, one for real.
    const attempts: Array<{ label: string; call: () => any }> = [];
    const rec: Recorded = { keys: new Map() };
    const probe = standIn(rec);
    if (b.self !== m) {
      // A method on the entry: the entry is `this`, the argument is the parameters.
      attempts.push({ label: b.name + "(params)", call: () => b.fn.call(b.self, probe) });
    } else if (b.wantsSpec && spec) {
      attempts.push({ label: b.name + "(entry)", call: () => b.fn.call(m, spec) });
      attempts.push({ label: b.name + "(device, entry)", call: () => b.fn.call(m, env.device, spec) });
      attempts.push({ label: b.name + "(entry, params)", call: () => b.fn.call(m, spec, probe) });
    } else {
      attempts.push({ label: b.name + "()", call: () => b.fn.call(m) });
      attempts.push({ label: b.name + "(params)", call: () => b.fn.call(m, probe) });
      attempts.push({ label: b.name + "(device, params)", call: () => b.fn.call(m, env.device, probe) });
    }
    for (const a of attempts) {
      let out: any;
      try {
        out = a.call();
      } catch (e: any) {
        tried.push(a.label + " threw: " + String(e?.message || e).slice(0, 120));
        continue;
      }
      if (out && typeof out.then === "function") { tried.push(a.label + " is async — not supported here"); continue; }
      let got = toObject(out, mk, maxParts);
      if (!got.parts) { tried.push(a.label + " returned nothing this can draw"); continue; }
      // It worked with invented values; now do it again with the real ones, and keep that
      // instead when it is at least as good.
      if (rec.keys.size) {
        try {
          const real = resolveArg(rec, mods);
          const redo = b.self !== m ? b.fn.call(b.self, real)
            : b.wantsSpec && spec && a.label.indexOf("params") < 0 ? null
              : a.label.indexOf("device") >= 0 ? b.fn.call(m, env.device, real) : b.fn.call(m, real);
          if (redo != null) {
            const better = toObject(redo, mk, maxParts);
            if (better.parts >= got.parts) got = better;
          }
        } catch { /* the stand-in run is the answer, then */ }
      }
      return {
        object: got.object, parts: got.parts, tried,
        how: (how ? how + ", built by " : "built by ") + a.label,
      };
    }
  }
  return { object: null, how: "", parts: 0, tried };
}
