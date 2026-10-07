// THE STUDIO RUNTIME: the saved edits, applied by the game itself.
//
// What the Studio's Edit tab and its agents save lives beside the game, in `studio.edits.json`:
// "move this part, hide that one, put a crystal here". Until this file, the only thing that could
// apply that document was `forge-ops.js`, 285 KB of modelling tools, and the live link applied it
// to three.js scenes only: never to a PlayCanvas app, never the `placed` list, and never in a
// game that had shipped. An edit made in the Studio existed in the Studio.
//
// This is the part a game ships. One ESM file with no imports of its own, built to
// `dist/studio-runtime.js` and served by the Studio at `/studio-runtime.js`:
//
//   import { loadStudioEdits, applyStudioEdits } from './studio-runtime.js';
//   await applyStudioEdits(scene, await loadStudioEdits());      // three.js
//   await applyStudioEdits(app, await loadStudioEdits());        // PlayCanvas
//
// The live link runs the same module inside a page it opens (live_shim.py), so a game with no
// line of this in it still shows its saved edits, and a game that has the line shows the same.
//
// Three rules the whole file keeps:
//
//  * THE SAME KEYS AND THE SAME APPLIER AS THE EDITOR. Keys are `stableKeys` and parts are applied
//    by `applyEdits` / `pcApply`, imported from ops.ts rather than written again: a sidecar that one
//    copy reads one way and another copy reads another is two documents with one name.
//  * NO SECOND ENGINE. Nothing here imports three or PlayCanvas. Every class it needs is taken
//    from an object the game already has, by duck type, the way `pcClassesOf` does: a second copy
//    of three makes materials the game's renderer throws on, and a bundled game has no `pc` global
//    to ask in the first place.
//  * RADIANS IN, RADIANS OUT. The file stores XYZ eulers in radians (three's convention) and this
//    applies them as they are; PlayCanvas gets the same rotation through `quatFromEulerXYZ`. No
//    degree ever passes through here.

import type { PlacedItem } from "./kit";
import { nodeName, sameModel } from "./gamedress";
import { applyEdits, hexToRgb01, pcApply, quatFromEulerXYZ, stableKeys, walkTree } from "./ops";

export const version = 1;

export interface RuntimeOpts {
  /** The URL the game's files resolve against. Default: the page's own directory. */
  base?: string;
  /** three's glTF loader, bound to the game's own three: the class or a configured instance.
   *  Needed only for a `model` placement in a three.js game. */
  GLTFLoader?: any;
  /** Put the `placed` list in as well. Default true. */
  placed?: boolean;
  /** How long a placement whose source does not exist yet, or a part whose object has not been
   *  built yet, keeps being retried. Default 60 000 ms; 0 turns retrying off. */
  retryMs?: number;
  /** A project-relative path to a URL, when `base` is not enough: the live link sends models
   *  through the Studio, which hands them back with their compression already taken out. */
  url?: (path: string, kind: "model" | "code" | "image") => string | undefined | null;
  /** Called once, when nothing is left pending: every late clone placed or given up on. */
  onSettle?: (report: Report) => void;
}

/** What happened. LIVE: a clone that resolves after `applyStudioEdits` returned moves one from
 *  `pending` to `placed` in this same object, so a caller holding it always reads the truth. */
export interface Report { parts: number; placed: number; pending: number; missing: string[]; errors: string[] }

export interface PlaceResult { ok: boolean; object?: any; key?: string; how?: string; pending?: boolean; error?: string }

// ------------------------------------------------------------------ the engine, by duck type

const isPcApp = (o: any): boolean => !!(o && o.root && o.graphicsDevice && typeof o.root.addChild === "function");
const isPcEntity = (o: any): boolean => !!(o && !o.isObject3D && typeof o.addChild === "function"
  && typeof o.setLocalPosition === "function" && Array.isArray(o.children));

/** Which engine an object belongs to: a three Object3D, or a PlayCanvas app or entity. */
export function engineOf(target: any): "three" | "playcanvas" | "" {
  if (!target || typeof target !== "object") return "";
  if (target.isObject3D) return "three";
  if (isPcApp(target) || isPcEntity(target)) return "playcanvas";
  return "";
}

/** The object keys are counted from: a three object is its own root; a PlayCanvas app's root is
 *  `app.root`, and an entity is its own. */
export function rootOf(target: any): any {
  const e = engineOf(target);
  if (!e) return null;
  return e === "playcanvas" && isPcApp(target) ? target.root : target;
}

interface Ctx { engine: "three" | "playcanvas"; root: any; app: any }

function ctx(target: any): Ctx | null {
  const engine = engineOf(target);
  const root = rootOf(target);
  if (!engine || !root) return null;
  let app: any = null;
  if (engine === "playcanvas") {
    app = isPcApp(target) ? target : root._app || (typeof root.getApplication === "function" ? root.getApplication() : null);
  }
  return { engine, root, app };
}

/** `ops.stableKeys`, the one keying rule of the editor, the page and the game. An app is keyed
 *  from its root, which is what the editor mirrors. */
export function studioKeys(root: any): Map<any, string> {
  return stableKeys(isPcApp(root) ? root.root : root);
}

export function findByKey(root: any, key: string): any | null {
  if (!key) return null;
  for (const [o, k] of studioKeys(root)) if (k === key) return o;
  return null;
}

const keyIn = (root: any, obj: any): string => studioKeys(root).get(obj) || "";

// ------------------------------------------------------------------ the document

/**
 * The saved edits, or null when there are none.
 *
 * A 404 is the ordinary case, not an error: most games have no edits yet. The Studio's own file
 * endpoint answers `{text}` rather than the document, so both are taken. `no-cache` because a
 * browser that heuristically caches this file shows yesterday's edits after today's save.
 */
export async function loadStudioEdits(url = "studio.edits.json"): Promise<any | null> {
  let r: Response;
  try { r = await fetch(url, { cache: "no-cache" }); } catch { return null; }
  if (!r.ok) {
    if (r.status !== 404) console.warn("[studio-runtime] " + url + " answered " + r.status + "; no edits applied");
    return null;
  }
  try {
    let raw: any = await r.json();
    if (raw && typeof raw.text === "string") raw = raw.text.trim() ? JSON.parse(raw.text) : null;
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : null;
  } catch (e: any) {
    console.warn("[studio-runtime] " + url + " is not JSON (" + String(e?.message || e) + "); no edits applied");
    return null;
  }
}

const OVERRIDE_NUMBERS = ["roughness", "metalness", "intensity", "distance", "angle", "penumbra", "decay", "fov", "near", "far", "zoom"];
const KINDS = ["primitive", "code", "model", "image", "clone"];
const fin = (n: any): boolean => typeof n === "number" && isFinite(n);
const v3 = (v: any): boolean => Array.isArray(v) && v.length >= 3 && fin(v[0]) && fin(v[1]) && fin(v[2]);
const take3 = (v: any, d: [number, number, number]): [number, number, number] => (v3(v) ? [v[0], v[1], v[2]] : d);

interface Doc { parts: Record<string, any>; world?: any; verts?: any[]; placed: PlacedItem[]; mods: number }

/**
 * The document with everything malformed dropped, field by field. It is read from a file a person
 * or an agent may have written by hand, and one bad entry must cost that entry, never the scene.
 * `piece` is kept: without it a merged-mesh edit would move whatever now sits in those triangles.
 */
function cleanDoc(raw: any): Doc {
  const doc: Doc = { parts: {}, placed: [], mods: 0 };
  if (!raw || typeof raw !== "object") return doc;
  for (const [k, v] of Object.entries<any>(raw.parts && typeof raw.parts === "object" ? raw.parts : {})) {
    if (!v || typeof v !== "object") continue;
    const o: any = {};
    for (const f of ["pos", "rot", "scale"]) if (v3(v[f])) o[f] = [v[f][0], v[f][1], v[f][2]];
    if (typeof v.hidden === "boolean") o.hidden = v.hidden;
    if (typeof v.color === "string") o.color = v.color;
    if (typeof v.shadow === "boolean") o.shadow = v.shadow;
    for (const f of OVERRIDE_NUMBERS) if (fin(v[f])) o[f] = v[f];
    if (v.piece && v3(v.piece.c) && fin(v.piece.n)) o.piece = { c: [v.piece.c[0], v.piece.c[1], v.piece.c[2]], n: v.piece.n };
    if (Object.keys(o).length) doc.parts[k] = o;
  }
  const w = raw.world;
  if (w && typeof w === "object") {
    const o: any = {};
    if (typeof w.background === "string" && /^#[0-9a-f]{6}$/i.test(w.background)) o.background = w.background;
    if (w.fog === null) o.fog = null;
    else if (w.fog && typeof w.fog === "object" && typeof w.fog.color === "string") {
      o.fog = { type: w.fog.type === "exp2" ? "exp2" : "linear", color: w.fog.color };
      for (const f of ["near", "far", "density"]) if (fin(w.fog[f])) o.fog[f] = w.fog[f];
    }
    if (Object.keys(o).length) doc.world = o;
  }
  if (Array.isArray(raw.verts) && raw.verts.length) doc.verts = raw.verts;
  if (Array.isArray(raw.placed)) for (const it of raw.placed) { const c = cleanItem(it); if (c) doc.placed.push(c); }
  if (Array.isArray(raw.mods)) doc.mods = raw.mods.filter((m: any) => m && m.op && !m.off).length;
  return doc;
}

function cleanItem(it: any): PlacedItem | null {
  const ref = it?.ref;
  if (!it || typeof it.id !== "string" || !it.id || !ref || typeof ref !== "object" || !KINDS.includes(ref.kind)) return null;
  return {
    id: it.id,
    name: typeof it.name === "string" && it.name ? it.name : it.id,
    ref: { ...ref },
    pos: take3(it.pos, [0, 0, 0]),
    rot: take3(it.rot, [0, 0, 0]),
    scale: take3(it.scale, [1, 1, 1]),
  };
}

// ------------------------------------------------------------------ parts, world, vertices

/** The editor's own appliers, minus the modifier stack: that needs the whole modelling library,
 *  which is exactly what this file exists not to carry. */
function applyParts(c: Ctx, doc: { parts: Record<string, any>; world?: any; verts?: any[] }): { parts: number; missing: string[]; errors: string[] } {
  const d = { parts: doc.parts, world: doc.world, verts: doc.verts, mods: [] };
  try {
    const r = c.engine === "three" ? applyEdits(c.root, d, undefined, doc.world ? worldKit(c.root) : undefined) : pcApply(c.root, d);
    return { parts: r.parts, missing: r.missing, errors: r.errors };
  } catch (e: any) {
    return { parts: 0, missing: [], errors: ["parts: " + msgOf(e)] };
  }
}

/**
 * Background and fog need a Color, and a fog where the game made none needs a Fog. The Color is
 * taken from anything that has one. A Fog cannot be: a game with no fog has no Fog anywhere. So a
 * missing one is made by shape, and the shape is everything three's renderer reads (`isFog` or
 * `isFogExp2`, `color`, `near`/`far` or `density`) plus the two methods a Scene calls on it.
 */
function worldKit(root: any): any {
  let sc: any = root.isScene ? root : null;
  let Color: any = sc?.background?.isColor ? sc.background.constructor : null;
  walkTree(root, (o) => {
    if (!sc && o.isScene) sc = o;
    if (!Color && o.color?.isColor) Color = o.color.constructor;
    if (!Color) for (const m of matsOf(o)) if (m?.color?.isColor) { Color = m.color.constructor; break; }
  });
  if (!Color) return undefined;
  const f = sc?.fog;
  return { Color, Fog: f?.isFog ? f.constructor : duckFog(Color, false), FogExp2: f?.isFogExp2 ? f.constructor : duckFog(Color, true) };
}

function duckFog(Color: any, exp2: boolean): any {
  return class {
    isFog = !exp2;
    isFogExp2 = exp2;
    name = "";
    color: any;
    near = 1;
    far = 1000;
    density = 0.00025;
    constructor(color: any, a?: number, b?: number) {
      this.color = new Color(color);
      if (exp2) { if (a !== undefined) this.density = a; }
      else { if (a !== undefined) this.near = a; if (b !== undefined) this.far = b; }
    }
    clone(): any { const C: any = this.constructor; return exp2 ? new C(this.color, this.density) : new C(this.color, this.near, this.far); }
    toJSON(): any {
      return exp2 ? { type: "FogExp2", name: this.name, color: this.color.getHex(), density: this.density }
        : { type: "Fog", name: this.name, color: this.color.getHex(), near: this.near, far: this.far };
    }
  };
}

const matsOf = (o: any): any[] => (Array.isArray(o?.material) ? o.material : o?.material ? [o.material] : []);
const msgOf = (e: any): string => String(e?.message || e).slice(0, 200);

// ------------------------------------------------------------------ applying the whole document

const EVERY = 500;
const RETRY_MS = 60000;
const retryOf = (o: RuntimeOpts): number => (fin(o.retryMs) ? Math.max(0, o.retryMs as number) : RETRY_MS);
const emptyReport = (): Report => ({ parts: 0, placed: 0, pending: 0, missing: [], errors: [] });

/** One call's report, and whether its caller has been told it is done. */
interface Owner { rep: Report; opts: RuntimeOpts; told: boolean }
interface Waiting { it: PlacedItem; own: Owner; until: number; gen: number }
interface Missing { o: any; own: Owner; until: number }
/**
 * What is still outstanding on one root: placements waiting for something, and parts whose object
 * the game has not built yet. ONE PER ROOT, SHARED BY EVERY CALL, and not replaced by the next
 * call: a parts-only apply (an agent's live move) used to cancel the job before it, and with it a
 * clone still waiting for the game to spawn its source. A full apply owns the placement set, so it
 * alone bumps `gen` and drops what an older one was waiting for.
 */
interface Job { c: Ctx; waiting: Map<string, Waiting>; missing: Map<string, Missing>; gen: number; timer: any; busy: boolean }
const JOBS = new WeakMap<object, Job>();

function jobOf(c: Ctx): Job {
  let j = JOBS.get(c.root);
  if (!j) { j = { c, waiting: new Map(), missing: new Map(), gen: 0, timer: 0, busy: false }; JOBS.set(c.root, j); }
  return j;
}

/** Tell a caller its report is final, once nothing it asked for is outstanding. */
function tell(j: Job, own: Owner) {
  if (own.told) return;
  own.rep.pending = 0;
  for (const w of j.waiting.values()) if (w.own === own) own.rep.pending++;
  if (own.rep.pending) return;
  for (const m of j.missing.values()) if (m.own === own) return;
  own.told = true;
  try { own.opts.onSettle?.(own.rep); } catch { /* the caller's callback is not ours to fail on */ }
}

function schedule(j: Job) {
  if (!j.waiting.size && !j.missing.size) { if (j.timer) clearTimeout(j.timer); j.timer = 0; return; }
  if (!j.timer) j.timer = setTimeout(() => { j.timer = 0; void tick(j); }, EVERY);
}

/** Parts that had no object: try again, and take what is found out of every report that missed it. */
function retryParts(j: Job, owners: Set<Owner>) {
  if (!j.missing.size) return;
  const sub: Record<string, any> = {};
  for (const [k, m] of j.missing) sub[k] = m.o;
  const r = applyParts(j.c, { parts: sub });
  const still = new Set(r.missing);
  const now = Date.now();
  for (const [k, m] of [...j.missing]) {
    if (still.has(k)) { if (now >= m.until) { j.missing.delete(k); owners.add(m.own); } continue; }
    j.missing.delete(k);
    m.own.rep.missing = m.own.rep.missing.filter((x) => x !== k);
    if (r.errors.some((e) => e.startsWith(k + ": "))) {
      for (const e of r.errors) if (e.startsWith(k + ": ") && !m.own.rep.errors.includes(e)) m.own.rep.errors.push(e);
    } else m.own.rep.parts++;
    owners.add(m.own);
  }
}

async function tick(j: Job) {
  if (j.busy) { schedule(j); return; }
  j.busy = true;
  const owners = new Set<Owner>();
  try {
    const now = Date.now();
    for (const [id, w] of [...j.waiting]) {
      if (j.waiting.get(id) !== w) continue;
      if (!ready(j.c, w.it)) {
        if (now >= w.until) {
          j.waiting.delete(id);
          w.own.rep.errors.push(w.it.name + ": " + waitingFor(w.it) + " — gave up after " + Math.round(retryOf(w.own.opts) / 1000) + " s");
          owners.add(w.own);
        }
        continue;
      }
      j.waiting.delete(id);
      const r = await placeNow(j.c, w.it, w.own.opts, () => w.gen === j.gen && !j.waiting.has(id));
      owners.add(w.own);
      if (r.ok) { w.own.rep.placed++; retryParts(j, owners); }
      else if (r.pending && w.gen === j.gen && !j.waiting.has(id)) j.waiting.set(id, w);
      else if (r.error) w.own.rep.errors.push(r.error);
    }
    retryParts(j, owners);
  } finally { j.busy = false; }
  for (const own of owners) tell(j, own);
  schedule(j);
}

/**
 * The saved edits onto a running game: the world, every part override (transform, visibility,
 * colour, material numbers, light and camera settings, pieces of merged meshes, hand-moved
 * vertices), then every placement.
 *
 * Safe to call again. Overrides are absolute and pieces move from the code's REST arrays, so a
 * second application changes nothing twice; a placement replaces the object with its id, and one
 * the document no longer lists is taken out. Parts whose object the game has not built yet, and
 * clones of something it has not spawned yet, are retried for `retryMs`: games build late.
 */
export async function applyStudioEdits(target: any, edits: any, opts: RuntimeOpts = {}): Promise<Report> {
  const rep = emptyReport();
  const c = ctx(target);
  if (!c) {
    rep.errors.push("not a three.js object or a PlayCanvas app or entity");
    try { opts.onSettle?.(rep); } catch { /* not ours */ }
    return rep;
  }
  const doc = cleanDoc(edits);
  const retry = retryOf(opts);
  const own: Owner = { rep, opts, told: false };
  const j = jobOf(c);
  const owners = new Set<Owner>([own]);

  const r = applyParts(c, doc);
  rep.parts = r.parts;
  rep.errors.push(...r.errors);
  rep.missing = r.missing.slice();
  // A newer value for a key replaces what an older call was still waiting to apply.
  for (const k of Object.keys(doc.parts)) {
    const old = j.missing.get(k);
    if (old && old.own !== own) { j.missing.delete(k); owners.add(old.own); }
  }
  // Registered even with retrying off: a part keyed to something this same call places still gets
  // its one retry after the placements, and is dropped right after it.
  for (const k of r.missing) if (doc.parts[k]) j.missing.set(k, { o: doc.parts[k], own, until: Date.now() + retry });
  if (doc.mods) {
    rep.errors.push(doc.mods + " modifier" + (doc.mods === 1 ? " is" : "s are") + " not applied: the runtime carries no modelling"
      + " library — bake them into the code, or apply them with forge-ops.js applyEdits");
  }

  if (opts.placed !== false) {
    // THE FILE OWNS THE PLACEMENTS. What an older apply was still waiting for is dropped, and what
    // the file no longer lists goes: without that, a placement deleted in the Studio stayed in the
    // running game until the tab was reloaded, and the file and the scene disagreed.
    const gen = ++j.gen;
    for (const w of j.waiting.values()) {
      if (w.own !== own) { w.own.rep.errors.push(w.it.name + ": replaced by a newer apply before it was placed"); owners.add(w.own); }
    }
    j.waiting.clear();
    const keep = new Set(doc.placed.map((it) => it.id));
    for (const id of placedIds(c)) if (!keep.has(id)) unplace(c, id);
    for (const it of doc.placed) {
      if (j.gen !== gen) break;
      const got = await placeNow(c, it, opts, () => j.gen === gen && !j.waiting.has(it.id));
      if (got.ok) rep.placed++;
      else if (got.pending && retry > 0 && j.gen === gen) j.waiting.set(it.id, { it, own, until: Date.now() + retry, gen });
      else if (got.error) rep.errors.push(got.error);
    }
    // A part keyed to something just placed: the editor keys placements like anything else.
    if (rep.placed) retryParts(j, owners);
  }
  if (retry <= 0) for (const [k, m] of [...j.missing]) if (m.own === own) j.missing.delete(k);
  for (const o of owners) tell(j, o);
  schedule(j);
  return rep;
}

/**
 * One placement into a running game, now. What `applyStudioEdits` does for each entry of `placed`,
 * and what an agent's "put one here" calls directly. An earlier object with the same id is
 * replaced, so sending the same item twice leaves one object.
 */
export async function placeStudioItem(target: any, item: PlacedItem, opts: RuntimeOpts = {}): Promise<PlaceResult> {
  const c = ctx(target);
  if (!c) return { ok: false, error: "not a three.js object or a PlayCanvas app or entity" };
  const it = cleanItem(item);
  if (!it) return { ok: false, error: "not a placement: it needs an id, and a ref whose kind is one of " + KINDS.join(", ") };
  const j = jobOf(c);
  j.waiting.delete(it.id);                              // this call owns that id now
  const gen = j.gen;
  const got = await placeNow(c, it, opts, () => j.gen === gen && !j.waiting.has(it.id));
  const retry = retryOf(opts);
  if (got.pending && retry > 0 && j.gen === gen) {
    const own: Owner = { rep: emptyReport(), opts, told: false };
    j.waiting.set(it.id, { it, own, until: Date.now() + retry, gen });
    schedule(j);
  }
  return got;
}

/** Take a placement back out. Returns how many objects carried that id. */
export function unplaceStudioItem(target: any, id: string): number {
  const c = ctx(target);
  if (!c) return 0;
  const j = JOBS.get(c.root);
  const w = j?.waiting.get(String(id));
  if (j && w) { j.waiting.delete(String(id)); tell(j, w.own); schedule(j); }
  return unplace(c, String(id));
}

// ------------------------------------------------------------------ placing

const markOf = (c: Ctx, o: any): string => String((c.engine === "three" ? o?.userData?.studioPlaced : o?.__studioPlaced) || "");

function placedIds(c: Ctx): Set<string> {
  const out = new Set<string>();
  walkTree(c.root, (o) => { const m = o !== c.root ? markOf(c, o) : ""; if (m) out.add(m); });
  return out;
}

/** Every object carrying the id, wherever the game has since moved it. Nested copies go with the
 *  outer one; what the runtime itself made (a primitive, a loaded file) gives its GPU memory back. */
function unplace(c: Ctx, id: string, keep: any = null): number {
  const gone: any[] = [];
  walkTree(c.root, (o) => { if (o !== c.root && o !== keep && markOf(c, o) === id) gone.push(o); });
  const outer = gone.filter((o) => { for (let p = o.parent; p; p = p.parent) if (gone.includes(p)) return false; return true; });
  for (const o of outer) drop(c, o);
  return outer.length;
}

function drop(c: Ctx, o: any) {
  try {
    if (c.engine === "three") {
      o.parent?.remove(o);
      if (o.userData?.studioOwned) {
        o.traverse?.((n: any) => {
          n.geometry?.dispose?.();
          for (const m of matsOf(n)) { m.map?.dispose?.(); m.dispose?.(); }
        });
      }
    } else o.destroy?.();
  } catch { /* already gone */ }
}

/** The mark, the name, the parent and the transform: the same four things for every kind. */
function put(c: Ctx, it: PlacedItem, obj: any) {
  unplace(c, it.id, obj);
  obj.name = it.name;
  const [px, py, pz] = it.pos, [rx, ry, rz] = it.rot, [sx, sy, sz] = it.scale;
  if (c.engine === "three") {
    obj.userData = obj.userData || {};
    obj.userData.studioPlaced = it.id;
    obj.position.set(px, py, pz);
    obj.rotation.set(rx, ry, rz);
    obj.scale.set(sx, sy, sz);
    c.root.add(obj);
    obj.updateMatrixWorld?.(true);
  } else {
    obj.__studioPlaced = it.id;
    if (obj.parent !== c.root) { obj.parent?.removeChild?.(obj); c.root.addChild(obj); }
    obj.setLocalPosition(px, py, pz);
    const q = quatFromEulerXYZ(rx, ry, rz);
    obj.setLocalRotation(q[0], q[1], q[2], q[3]);
    obj.setLocalScale(sx, sy, sz);
  }
}

type Placed = PlaceResult;
const WAIT = { wait: true };

function waitingFor(it: PlacedItem): string {
  return it.ref.kind === "clone" ? "the object it copies (" + (it.ref.of || "?") + ") does not exist yet"
    : "the scene has no mesh yet to take the engine's classes from";
}

/** Can this placement be made now? A clone needs its source; a three.js primitive needs one mesh
 *  in the scene to learn the classes from. Everything else can always try. */
function ready(c: Ctx, it: PlacedItem): boolean {
  if (it.ref.kind === "clone") return !!findByKey(c.root, it.ref.of || "");
  if (c.engine === "three" && (it.ref.kind === "primitive" || it.ref.kind === "image")) return !!threeKit(c.root);
  return true;
}

async function placeNow(c: Ctx, it: PlacedItem, opts: RuntimeOpts, live: () => boolean): Promise<Placed> {
  let made: any;
  try { made = await build(c, it, opts); } catch (e: any) { return { ok: false, error: it.name + ": " + msgOf(e) }; }
  if (made === WAIT) return { ok: false, pending: true, error: it.name + ": " + waitingFor(it) };
  if (!made?.object) return { ok: false, error: it.name + ": nothing came back" };
  if (!live()) { drop(c, made.object); return { ok: false, error: it.name + ": replaced by a newer apply before it finished" }; }
  put(c, it, made.object);
  return { ok: true, object: made.object, key: keyIn(c.root, made.object), how: made.how };
}

async function build(c: Ctx, it: PlacedItem, opts: RuntimeOpts): Promise<any> {
  const ref = it.ref;
  if (ref.kind === "clone") {
    if (!ref.of) throw new Error("a clone names no object to copy (ref.of)");
    const src = findByKey(c.root, ref.of);
    if (!src) return WAIT;
    return { object: c.engine === "three" ? threeCopy(src) : pcCopy(src), how: "a copy of " + ref.of };
  }
  if (ref.kind === "primitive") {
    const shape = String(ref.shape || "box"), color = String(ref.color || "#9aa7b8");
    if (c.engine === "three") {
      const k = threeKit(c.root);
      if (!k) return WAIT;
      return { object: threeShape(k, shape, color), how: "a " + shape };
    }
    return { object: pcShape(pcKit(c), shape, color, it.name), how: "a " + shape };
  }
  if (ref.kind === "model") return c.engine === "three" ? threeModel(it, opts) : pcModel(c, it, opts);
  if (ref.kind === "code") return codeBuild(c, it, opts);
  if (ref.kind === "image") return c.engine === "three" ? threeImage(c, it, opts) : pcImage(c, it, opts);
  throw new Error("unknown kind " + ref.kind);
}

// ------------------------------------------------------------------ files the game ships

function pageDir(): string {
  try {
    if (typeof document !== "undefined" && document.baseURI) return new URL(".", document.baseURI).href;
    if (typeof location !== "undefined") return new URL(".", location.href).href;
  } catch { /* no page: node, a worker without a location */ }
  return "";
}

function resolve(path: string, kind: "model" | "code" | "image", opts: RuntimeOpts): string {
  const p = String(path || "").replace(/\\/g, "/");
  if (opts.url) { try { const u = opts.url(p, kind); if (u) return u; } catch { /* fall through to the base */ } }
  if (/^[a-z][a-z0-9+.-]*:/i.test(p)) return p;
  try { return new URL(p.replace(/^\/+/, ""), opts.base || pageDir()).href; } catch { return p; }
}

const baseName = (p: string) => String(p || "").split(/[?#]/)[0].split("/").pop() || "model.glb";

/**
 * The module that builds a `code` placement. Resolved against the page first; when that is not
 * found, the path is tried again one folder shorter, twice. A workspace names a file from its own
 * root (`rot-rush/src/props.ts`) while the game's server serves that game's folder at `/`, so the
 * first try is a 404 and the second is the file.
 */
async function importCode(file: string, opts: RuntimeOpts): Promise<any> {
  const segs = String(file).replace(/\\/g, "/").replace(/^\/+/, "").split("/");
  const tried: string[] = [];
  for (let cut = 0; cut < Math.min(3, segs.length); cut++) {
    const url = resolve(segs.slice(cut).join("/"), "code", opts);
    try { return await import(/* @vite-ignore */ url); }
    catch (e: any) {
      const m = msgOf(e);
      tried.push(url + " (" + m + ")");
      if (!/fetch|find|found|load|import|404|ERR_MODULE/i.test(m)) break;
    }
  }
  throw new Error("could not import " + file + ": " + tried.join("; "));
}

const isClass = (fn: any): boolean => {
  try { return /^class[\s{]/.test(Function.prototype.toString.call(fn)); } catch { return false; }
};

/** A builder's answer as something that can go in the scene: the object itself, or the object a
 *  wrapper holds under one of the names builders use for it. */
function placeable(c: Ctx, v: any): any {
  const ok = (o: any) => !!o && (c.engine === "three" ? !!o.isObject3D : isPcEntity(o));
  if (ok(v)) return v;
  if (v && typeof v === "object") for (const k of ["object", "root", "group", "mesh", "scene", "entity", "model"]) if (ok(v[k])) return v[k];
  return null;
}

/** The game's own builder, called: `ref.export` of `ref.file` with `ref.args`. A PlayCanvas builder
 *  that takes nothing it was given is tried once with the app and once with the graphics device,
 *  the two things such builders ask for. A class is constructed; a promise is awaited. */
async function codeBuild(c: Ctx, it: PlacedItem, opts: RuntimeOpts): Promise<any> {
  const ref = it.ref;
  if (!ref.file) throw new Error("a code placement names no file (ref.file)");
  const m = await importCode(ref.file, opts);
  const name = ref.export || "default";
  const fn = m?.[name];
  if (typeof fn !== "function") throw new Error(ref.file + " exports no function called " + name);
  const args = Array.isArray(ref.args) ? ref.args : [];
  const tries: any[][] = [args];
  if (c.engine === "playcanvas" && !args.length && c.app) tries.push([c.app], [c.app.graphicsDevice]);
  let why = "";
  for (const a of tries) {
    let out: any;
    try { out = isClass(fn) ? new fn(...a) : fn(...a); out = await out; }
    catch (e: any) { why = name + " threw: " + msgOf(e); continue; }
    const obj = placeable(c, out);
    if (obj) return { object: obj, how: "built by " + name + "()" };
    why = name + " returned nothing that can be placed";
  }
  throw new Error(why);
}

/** Show only what a table entry names: its first node and its extra parts. The same rule as the
 *  editor's `keepNodes`, on either engine. Nothing changes when none of the names is in the file. */
function keepNodes(model: any, nodes: string[] | undefined, parts: string[] | undefined) {
  const first = (nodes || []).map(nodeName).filter(Boolean)[0];
  if (!first) return;
  const keep = new Set([first, ...(parts || []).map(nodeName).filter(Boolean)]);
  const drawn: any[] = [];
  walkTree(model, (o) => { if (o.isMesh || o.render) drawn.push(o); });
  const has = (o: any) => { for (let p = o; p && p !== model; p = p.parent) if (keep.has(nodeName(p.name))) return true; return false; };
  if (!drawn.some(has)) return;
  for (const o of drawn) { if (o.isMesh) o.visible = has(o); else o.enabled = has(o); }
}

// ------------------------------------------------------------------ three.js

interface ThreeKit { Mesh: any; Geometry: any; Attr: any; Std: any; Basic: any; Color: any; Texture: any; lights: Record<string, any> }

/** The class right below the one that owns `method` in an object's prototype chain. From a
 *  SkinnedMesh, `direct(o, "traverse")` is Mesh; from a MeshPhysicalMaterial, `direct(m,
 *  "setValues")` is MeshStandardMaterial. By method, not by class name: a minified build renames
 *  every class and keeps every method. */
function direct(obj: any, method: string): any {
  for (let p = Object.getPrototypeOf(obj); p; ) {
    const up = Object.getPrototypeOf(p);
    if (!up) return null;
    if (Object.prototype.hasOwnProperty.call(up, method)) return p.constructor;
    p = up;
  }
  return null;
}

const KITS = new WeakMap<object, ThreeKit>();

/**
 * three's classes, from the scene the game built. One mesh is enough: its class makes an empty
 * Mesh, which comes with an empty BufferGeometry and a MeshBasicMaterial, and `setFromPoints` on
 * that geometry makes the attribute class. A lit material is taken from the scene when it has one,
 * so a placed box is shaded like the game's own boxes.
 */
function threeKit(root: any): ThreeKit | null {
  const hit = KITS.get(root);
  if (hit) return hit;
  let mesh: any = null, std: any = null, lit: any = null, tex: any = null;
  const lights: Record<string, any> = {};
  walkTree(root, (o) => {
    if (!mesh && o.isMesh && o.geometry && o.material) mesh = o;
    for (const m of matsOf(o)) {
      if (!std && m.isMeshStandardMaterial) std = m;
      else if (!lit && (m.isMeshLambertMaterial || m.isMeshPhongMaterial)) lit = m;
      if (!tex && m.map?.isTexture) tex = m.map;
    }
    if (o.isPointLight && !lights.pointLight) lights.pointLight = o.constructor;
    if (o.isSpotLight && !lights.spotLight) lights.spotLight = o.constructor;
    if (o.isDirectionalLight && !lights.dirLight) lights.dirLight = o.constructor;
  });
  if (!mesh) return null;
  const Mesh = direct(mesh, "traverse");
  if (!Mesh) return null;
  const probe = new Mesh();
  const Geometry = probe.geometry.constructor;
  const Attr = new Geometry().setFromPoints([{ x: 0, y: 0, z: 0 }]).getAttribute("position").constructor;
  const Basic = probe.material.constructor;
  const k: ThreeKit = {
    Mesh, Geometry, Attr, Basic, lights,
    Std: std ? direct(std, "setValues") : lit ? direct(lit, "setValues") : Basic,
    Color: probe.material.color.constructor,
    Texture: tex ? direct(tex, "addEventListener") : null,
  };
  // Remembered only once it is as good as it gets; an early scene may gain a lit material later.
  if (std) KITS.set(root, k);
  return k;
}

function threeShape(k: ThreeKit, shape: string, color: string): any {
  if (shape === "pointLight" || shape === "spotLight" || shape === "dirLight") {
    const L = k.lights[shape];
    if (!L) throw new Error("the game has no " + shape + " to take three's class from — add one in code");
    const l = shape === "pointLight" ? new L(color, 2, 0, 2) : shape === "spotLight" ? new L(color, 4, 0, Math.PI / 6, 0.4, 2) : new L(color, 1.4);
    l.castShadow = true;
    return l;
  }
  const a = shapeArrays(shape);
  const g = new k.Geometry();
  g.setAttribute("position", new k.Attr(a.pos, 3));
  g.setAttribute("normal", new k.Attr(a.nor, 3));
  g.setAttribute("uv", new k.Attr(a.uv, 2));
  g.setIndex(a.idx);
  g.computeBoundingSphere?.();
  const m = new k.Std();
  m.color?.set?.(color);
  const plane = shape === "plane";
  if ("roughness" in m) m.roughness = plane ? 0.9 : 0.7;
  if ("metalness" in m) m.metalness = plane ? 0 : 0.05;
  if (plane) m.side = 2;                                // DoubleSide, in every three
  const mesh = new k.Mesh(g, m);
  // Shadows like the game's own things: a placed box beside the game's crates was the only object
  // on the floor with none (seen on a new game's review sheet). A plane is ground: it only receives.
  mesh.castShadow = !plane;
  mesh.receiveShadow = true;
  mesh.userData.studioOwned = true;
  return mesh;
}

function threeCopy(src: any): any {
  const o = src.clone(true);
  // A copy is not the thing it copies: its marks go, or removing the copy would remove the
  // original's placement as well, and disposing it would free the original's geometry.
  o.traverse?.((n: any) => { if (n.userData) { delete n.userData.studioPlaced; delete n.userData.studioOwned; } });
  o.visible = true;
  return o;
}

async function threeModel(it: PlacedItem, opts: RuntimeOpts): Promise<any> {
  const L = opts.GLTFLoader;
  if (!L) throw new Error("a model needs opts.GLTFLoader — three's glTF loader, from the game's own three");
  const loader = typeof L === "function" ? new L() : L;
  const url = resolve(it.ref.url || "", "model", opts);
  const gltf: any = typeof loader.loadAsync === "function" ? await loader.loadAsync(url)
    : await new Promise((res, rej) => loader.load(url, res, undefined, rej));
  const obj = gltf?.scene || gltf?.scenes?.[0];
  let drawn = 0;
  obj?.traverse?.((c: any) => { if (c.isMesh || c.isPoints || c.isLine || c.isSprite) drawn++; });
  if (!drawn) throw new Error("that file holds no mesh (an animation clip, or an empty scene) — place the model it belongs to");
  keepNodes(obj, it.ref.nodes, it.ref.parts);
  obj.userData.studioOwned = true;
  return { object: obj, how: "the file " + it.ref.url };
}

/** A picture standing up in the world, alpha kept: the editor's `makeSprite`. The Texture class
 *  comes from a picture the game already shows; a game with none cannot be given one here. */
async function threeImage(c: Ctx, it: PlacedItem, opts: RuntimeOpts): Promise<any> {
  const k = threeKit(c.root);
  if (!k) return WAIT;
  if (!k.Texture) throw new Error("the game shows no picture to take three's Texture class from");
  if (typeof Image === "undefined") throw new Error("pictures need a browser");
  const url = resolve(it.ref.url || "", "image", opts);
  const img = await new Promise((res, rej) => {
    const i = new Image();
    i.crossOrigin = "anonymous";
    i.onload = () => res(i);
    i.onerror = () => rej(new Error("could not load " + url));
    i.src = url;
  });
  const t = new k.Texture(img);
  t.colorSpace = "srgb";
  t.needsUpdate = true;
  const mesh = threeShape(k, "plane", "#ffffff");
  mesh.material.dispose?.();
  mesh.material = new k.Basic({ map: t, transparent: true, alphaTest: 0.02, side: 2, toneMapped: false });
  mesh.userData.studioSprite = it.ref.url;
  return { object: mesh, how: "the picture " + it.ref.url };
}

// ------------------------------------------------------------------ PlayCanvas

interface PcKit { app: any; Entity: any; Mesh: any; MeshInstance: any; Material: any; Color: any }
const PCKITS = new WeakMap<object, PcKit>();

const misOf = (e: any): any[] => (e?.render && e.render.meshInstances) || (e?.model && e.model.meshInstances) || [];

/**
 * PlayCanvas's classes, from the app. Entity is the root's own class. Mesh, MeshInstance and the
 * standard material come from any render the game has; an app that has drawn nothing yet gets them
 * from a scratch box made through its own render system and thrown away.
 */
function pcKit(c: Ctx): PcKit {
  const app = c.app;
  if (!app?.graphicsDevice) throw new Error("no PlayCanvas app behind this entity");
  const hit = PCKITS.get(app);
  if (hit) return hit;
  const Entity = (app.root || c.root).constructor;
  let Mesh: any = null, MeshInstance: any = null, Material: any = null;
  walkTree(app.root || c.root, (e) => {
    for (const mi of misOf(e)) {
      if (!MeshInstance && mi?.mesh) { MeshInstance = mi.constructor; Mesh = mi.mesh.constructor; }
      const m = mi?.material;
      if (!Material && m && "diffuseMap" in m && "emissiveMap" in m && typeof m.update === "function") Material = m.constructor;
    }
  });
  if (!MeshInstance || !Material) {
    const s = new Entity("studio-probe", app);
    try {
      s.addComponent("render", { type: "box" });
      const mi = s.render?.meshInstances?.[0];
      if (mi) { MeshInstance = MeshInstance || mi.constructor; Mesh = Mesh || mi.mesh.constructor; Material = Material || mi.material.constructor; }
    } finally { s.destroy?.(); }
  }
  if (!MeshInstance || !Material) throw new Error("the app has no render system to take its mesh classes from");
  const k: PcKit = { app, Entity, Mesh, MeshInstance, Material, Color: app.scene?.ambientLight?.constructor || null };
  PCKITS.set(app, k);
  return k;
}

const PC_LIGHTS: Record<string, string> = { pointLight: "omni", spotLight: "spot", dirLight: "directional" };

/** A primitive as a PlayCanvas mesh built from the SAME arrays three's geometry has, so a box
 *  placed in either engine is the same box: the render component's own shapes differ from three's
 *  (its torus is 0.5 across, three's 1.12). */
function pcShape(k: PcKit, shape: string, color: string, name: string): any {
  const e = new k.Entity(name, k.app);
  const rgb = hexToRgb01(color);
  if (PC_LIGHTS[shape]) {
    const data: any = { type: PC_LIGHTS[shape], intensity: 1, range: 10, castShadows: true };
    if (k.Color) data.color = new k.Color(rgb[0], rgb[1], rgb[2]);
    e.addComponent("light", data);
    return e;
  }
  const a = shapeArrays(shape);
  const mesh = new k.Mesh(k.app.graphicsDevice);
  mesh.setPositions(a.pos);
  mesh.setNormals(a.nor);
  mesh.setUvs(0, a.uv);
  mesh.setIndices(new Uint16Array(a.idx));
  mesh.update(4, true);                                 // PRIMITIVE_TRIANGLES, and the bounds
  const mat = new k.Material();
  try { mat.diffuse.set(rgb[0], rgb[1], rgb[2]); } catch { /* a material with no diffuse */ }
  const plane = shape === "plane";
  if ("gloss" in mat) mat.gloss = plane ? 0.1 : 0.3;
  if ("metalness" in mat) mat.metalness = plane ? 0 : 0.05;
  if ("useMetalness" in mat) mat.useMetalness = true;
  if (plane) mat.cull = 0;                              // CULLFACE_NONE: seen from both sides, like three's
  mat.update?.();
  e.addComponent("render", { meshInstances: [new k.MeshInstance(mesh, mat)],
                             castShadows: !plane, receiveShadows: true });
  return e;
}

// A copy is a copy of what it LOOKS like. Its scripts would play the game a second time (a
// spawner spawning, a coin being collected), its body would fall, its camera would draw the whole
// scene again on top: three's clone() carries none of those either, so neither does this.
const PC_BEHAVIOUR = ["script", "rigidbody", "collision", "camera", "sound", "audiolistener"];

function pcCopy(src: any): any {
  const e = src.clone();
  walkTree(e, (n) => {
    for (const t of PC_BEHAVIOUR) if (n[t] && typeof n.removeComponent === "function") { try { n.removeComponent(t); } catch { /* not removable */ } }
    if (n.__studioPlaced) delete n.__studioPlaced;
  });
  e.enabled = true;
  return e;
}

/** The file a render came from: a container names every render it makes "<file>/render/<n>". */
function pcSource(app: any, e: any): { url: string; box: any } | null {
  const id = e?.render?.asset;
  if (typeof id !== "number" || !app?.assets?.get) return null;
  const name = String(app.assets.get(id)?.name || "");
  const cut = name.lastIndexOf("/render/");
  if (cut <= 0) return null;
  const file = name.slice(0, cut);
  const box = typeof app.assets.find === "function" ? app.assets.find(file, "container") : null;
  return { url: String(box?.file?.url || file).split(/[?#]/)[0], box };
}

const placedAbove = (e: any): boolean => { for (let p = e; p; p = p.parent) if (p.__studioPlaced) return true; return false; };
const shownPc = (e: any): boolean => { for (let p = e; p; p = p.parent) if (p.enabled === false) return false; return true; };

/**
 * THE GAME'S OWN VERSION OF A MODEL FILE, when it has drawn one. rot-rush ships its brainrots with
 * no paint in them and in centimetres: the game paints every one with a shared palette and fits it
 * to 1.55 m in code. The file placed as it is came out a grey figure 386 m tall. A copy of one the
 * game drew has the game's paint and the game's size, so it is taken first (the editor's rule, see
 * gamedress.ts): the copy's top is climbed to by the file's own node names, its world turn and size
 * kept, and it stands with its feet on the placement's origin.
 */
function pcGameCopy(k: PcKit, root: any, file: string, nodes: string[] | undefined): any | null {
  const want = (nodes || []).map(nodeName).filter(Boolean);
  const tops: any[] = [];
  walkTree(root, (e) => {
    if (!e.render || placedAbove(e)) return;
    const src = pcSource(k.app, e);
    if (!src || !sameModel(src.url, file)) return;
    const d = src.box?.resource?.data;
    const names = new Set<string>(["Untitled"]);
    for (const n of [...(d?.nodes || []), ...(d?.scenes || [])]) if (n?.name) names.add(n.name);
    let top = e;
    while (top.parent && top.parent !== root && names.has(top.parent.name)) top = top.parent;
    if (!tops.includes(top)) tops.push(top);
  });
  const live = tops.filter((t) => shownPc(t) && !tops.some((u) => u !== t && isInside(t, u)));
  const named = want.length ? live.filter((t) => { let hit = false; walkTree(t, (n) => { if (n.render && shownPc(n) && want.includes(nodeName(n.name))) hit = true; }); return hit; }) : live;
  const top = named[0];
  if (!top) return null;
  const copy = pcCopy(top);
  const r = top.getRotation(), s = top.getWorldTransform().getScale();
  copy.setLocalPosition(0, 0, 0);
  copy.setLocalRotation(r.x, r.y, r.z, r.w);
  copy.setLocalScale(s.x, s.y, s.z);
  const holder = new k.Entity("studio-copy", k.app);
  holder.addChild(copy);
  const box = pcBounds(holder);
  if (box) copy.setLocalPosition(-(box.lo[0] + box.hi[0]) / 2, -box.lo[1], -(box.lo[2] + box.hi[2]) / 2);
  return holder;
}

const isInside = (o: any, top: any): boolean => { for (let p = o.parent; p; p = p.parent) if (p === top) return true; return false; };

/** The world box of what an entity draws, from its mesh instances. */
function pcBounds(e: any): { lo: number[]; hi: number[] } | null {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  walkTree(e, (n) => {
    if (n.enabled === false) return;
    for (const mi of misOf(n)) {
      const b = mi?.aabb;
      if (!b?.getMin) continue;
      const mn = b.getMin(), mx = b.getMax();
      lo[0] = Math.min(lo[0], mn.x); lo[1] = Math.min(lo[1], mn.y); lo[2] = Math.min(lo[2], mn.z);
      hi[0] = Math.max(hi[0], mx.x); hi[1] = Math.max(hi[1], mx.y); hi[2] = Math.max(hi[2], mx.z);
    }
  });
  return isFinite(lo[0]) ? { lo, hi } : null;
}

async function pcModel(c: Ctx, it: PlacedItem, opts: RuntimeOpts): Promise<any> {
  const k = pcKit(c);
  const file = it.ref.url || "";
  if (!file) throw new Error("a model placement names no file (ref.url)");
  const copy = pcGameCopy(k, k.app.root || c.root, file, it.ref.nodes);
  if (copy) return { object: copy, how: "a copy of the one the game draws, with its paint and size" };
  const asset = await pcLoad(k, resolve(file, "model", opts), file, "container");
  const e = asset?.resource?.instantiateRenderEntity?.();
  if (!e) throw new Error("that file made no entity");
  let drawn = 0;
  walkTree(e, (n) => { if (misOf(n).length) drawn++; });
  if (!drawn) { e.destroy?.(); throw new Error("that file holds no mesh (an animation clip, or an empty scene) — place the model it belongs to"); }
  keepNodes(e, it.ref.nodes, it.ref.parts);
  return { object: e, how: "the file " + file };
}

/** A picture on an upright quad, unlit: loaded through the app's own asset registry, so no
 *  Texture class is needed. */
async function pcImage(c: Ctx, it: PlacedItem, opts: RuntimeOpts): Promise<any> {
  const k = pcKit(c);
  const tex = (await pcLoad(k, resolve(it.ref.url || "", "image", opts), it.ref.url || "image.png", "texture"))?.resource;
  if (!tex) throw new Error("that picture did not load");
  const e = pcShape(k, "plane", "#ffffff", it.name);
  const mat = misOf(e)[0]?.material;
  if (mat) {
    mat.diffuseMap = mat.emissiveMap = mat.opacityMap = tex;
    mat.emissive?.set?.(1, 1, 1);
    mat.opacityMapChannel = "a";
    mat.alphaTest = 0.02;
    mat.useLighting = false;
    mat.blendType = 2;                                 // BLEND_NORMAL
    mat.update?.();
  }
  return { object: e, how: "the picture " + it.ref.url };
}

/** A file through the app's own asset registry, which already keeps one asset per URL. By file
 *  name as well as URL: the live link hands over a Studio URL with no extension in its path, and
 *  PlayCanvas picks a parser by the extension. */
function pcLoad(k: PcKit, url: string, file: string, type: string): Promise<any> {
  const reg = k.app.assets;
  return new Promise((res, rej) => {
    const done = (err: any, a: any) => (err ? rej(new Error(String(err))) : res(a));
    if (typeof reg.loadFromUrlAndFilename === "function") reg.loadFromUrlAndFilename(url, baseName(file), type, done);
    else reg.loadFromUrl(url, type, done);
  });
}

// ------------------------------------------------------------------ the primitives, as arrays
//
// The same vertices, normals, uvs and triangles as three's own BoxGeometry(1, 1, 1),
// SphereGeometry(0.5, 32, 20), CylinderGeometry(0.5, 0.5, 1, 28), ConeGeometry(0.5, 1, 28),
// PlaneGeometry(1, 1) and TorusGeometry(0.4, 0.16, 16, 40): the shapes the editor's
// `makePrimitive` places. Written out so that neither engine is needed to make one, and checked
// number for number against three in the tests.

interface Arrays { pos: Float32Array; nor: Float32Array; uv: Float32Array; idx: number[] }

export function shapeArrays(shape: string): Arrays {
  const P: number[] = [], N: number[] = [], U: number[] = [], I: number[] = [];
  const vtx = (x: number, y: number, z: number, nx: number, ny: number, nz: number, u: number, v: number) => {
    P.push(x, y, z); N.push(nx, ny, nz); U.push(u, v);
    return P.length / 3 - 1;
  };
  const TAU = Math.PI * 2;
  const quads = (grid: number[][], skipFirst: boolean, skipLast: boolean) => {
    for (let y = 0; y < grid.length - 1; y++) for (let x = 0; x < grid[y].length - 1; x++) {
      const a = grid[y][x + 1], b = grid[y][x], c = grid[y + 1][x], d = grid[y + 1][x + 1];
      if (!(skipFirst && y === 0)) I.push(a, b, d);
      if (!(skipLast && y === grid.length - 2)) I.push(b, c, d);
    }
  };
  if (shape === "sphere") {
    const r = 0.5, ws = 32, hs = 20, grid: number[][] = [];
    for (let iy = 0; iy <= hs; iy++) {
      const v = iy / hs, uo = iy === 0 ? 0.5 / ws : iy === hs ? -0.5 / ws : 0, row: number[] = [];
      for (let ix = 0; ix <= ws; ix++) {
        const u = ix / ws;
        const x = -r * Math.cos(u * TAU) * Math.sin(v * Math.PI), y = r * Math.cos(v * Math.PI), z = r * Math.sin(u * TAU) * Math.sin(v * Math.PI);
        const l = Math.hypot(x, y, z) || 1;
        row.push(vtx(x, y, z, x / l, y / l, z / l, u + uo, 1 - v));
      }
      grid.push(row);
    }
    quads(grid, true, true);
  } else if (shape === "cylinder" || shape === "cone") {
    const rt = shape === "cone" ? 0 : 0.5, rb = 0.5, h = 1, rs = 28, hh = h / 2, slope = (rb - rt) / h;
    const grid: number[][] = [];
    for (let y = 0; y <= 1; y++) {
      const radius = y * (rb - rt) + rt, row: number[] = [];
      for (let x = 0; x <= rs; x++) {
        const u = x / rs, s = Math.sin(u * TAU), c = Math.cos(u * TAU), l = Math.hypot(s, slope, c);
        row.push(vtx(radius * s, -y * h + hh, radius * c, s / l, slope / l, c / l, u, 1 - y));
      }
      grid.push(row);
    }
    // Not the sphere's quads: three's cylinder names its corners from [y][x], not [y][x + 1].
    for (let x = 0; x < rs; x++) {
      const a = grid[0][x], b = grid[1][x], c = grid[1][x + 1], d = grid[0][x + 1];
      if (rt > 0) I.push(a, b, d);
      if (rb > 0) I.push(b, c, d);
    }
    for (const top of [true, false]) {
      const radius = top ? rt : rb, sign = top ? 1 : -1;
      if (!(radius > 0)) continue;
      const c0 = P.length / 3;
      for (let x = 1; x <= rs; x++) vtx(0, hh * sign, 0, 0, sign, 0, 0.5, 0.5);
      const e0 = P.length / 3;
      for (let x = 0; x <= rs; x++) {
        const t = (x / rs) * TAU, cs = Math.cos(t), sn = Math.sin(t);
        vtx(radius * sn, hh * sign, radius * cs, 0, sign, 0, cs * 0.5 + 0.5, sn * 0.5 * sign + 0.5);
      }
      for (let x = 0; x < rs; x++) {
        const cc = c0 + x, i = e0 + x;
        if (top) I.push(i, i + 1, cc); else I.push(i + 1, i, cc);
      }
    }
  } else if (shape === "torus") {
    const R = 0.4, t = 0.16, rad = 16, tub = 40;
    for (let j = 0; j <= rad; j++) {
      const v = (j / rad) * TAU;
      for (let i = 0; i <= tub; i++) {
        const u = (i / tub) * TAU;
        const x = (R + t * Math.cos(v)) * Math.cos(u), y = (R + t * Math.cos(v)) * Math.sin(u), z = t * Math.sin(v);
        const nx = x - R * Math.cos(u), ny = y - R * Math.sin(u), l = Math.hypot(nx, ny, z) || 1;
        vtx(x, y, z, nx / l, ny / l, z / l, i / tub, j / rad);
      }
    }
    for (let j = 1; j <= rad; j++) for (let i = 1; i <= tub; i++) {
      const a = (tub + 1) * j + i - 1, b = (tub + 1) * (j - 1) + i - 1, c = (tub + 1) * (j - 1) + i, d = (tub + 1) * j + i;
      I.push(a, b, d, b, c, d);
    }
  } else if (shape === "plane") {
    // Upright and facing +z, as three builds it: the editor's plane is only laid flat by the
    // rotation its placement carries.
    for (let iy = 0; iy <= 1; iy++) for (let ix = 0; ix <= 1; ix++) vtx(ix - 0.5, -(iy - 0.5), 0, 0, 0, 1, ix, 1 - iy);
    I.push(0, 2, 1, 2, 3, 1);
  } else {
    // three's box: six faces in its order (+x, -x, +y, -y, +z, -z), four corners each.
    const face = (u: number, v: number, w: number, udir: number, vdir: number, depth: number) => {
      const base = P.length / 3;
      for (let iy = 0; iy <= 1; iy++) for (let ix = 0; ix <= 1; ix++) {
        const p = [0, 0, 0], n = [0, 0, 0];
        p[u] = (ix - 0.5) * udir; p[v] = (iy - 0.5) * vdir; p[w] = depth / 2;
        n[w] = depth > 0 ? 1 : -1;
        vtx(p[0], p[1], p[2], n[0], n[1], n[2], ix, 1 - iy);
      }
      I.push(base, base + 2, base + 1, base + 2, base + 3, base + 1);
    };
    face(2, 1, 0, -1, -1, 1);
    face(2, 1, 0, 1, -1, -1);
    face(0, 2, 1, 1, 1, 1);
    face(0, 2, 1, 1, -1, -1);
    face(0, 1, 2, 1, -1, 1);
    face(0, 1, 2, -1, -1, -1);
  }
  return { pos: new Float32Array(P), nor: new Float32Array(N), uv: new Float32Array(U), idx: I };
}
