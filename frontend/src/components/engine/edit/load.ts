// Opening a real asset FILE, as opposed to a recorded forge run.
//
// The two are not the same shape and cannot be run the same way. A recorded run is the body of a
// function — it calls `add(...)` and has a context handed to it. A file in the project is a
// module: it has imports and exports, and `export function build()` inside an async IIFE is a
// syntax error, not an asset.
//
// So a file is imported as a module. Two things have to be fixed first, and both are the kind of
// failure that looks like a broken asset rather than a broken loader:
//
//   1. `import * as THREE from 'three'` is a BARE specifier. Inside a blob module there is no
//      package resolution at all, so it is repointed at the same engine URL the editor already
//      resolved — which also guarantees the file gets the exact three the rest of the window uses,
//      rather than a second copy whose instanceof checks would all quietly fail.
//   2. `./parts.js` next to the file means the folder of the file, not the origin of the Studio.
//
// What the file has to offer in return is one function that builds something. Rather than impose
// a signature on every asset in the library, three are tried in the order a person would guess.

import type { ParamSpec, ParamValue } from "./kit";
import type { ModularManifest } from "./configurator";
import { labelOf, rangeFor } from "./kit";

export interface FileAsset {
  /** `ctx` is the run context (loadGLB, buildAsset, …) every call shape passes on; optional. */
  build(THREE: any, params: Record<string, ParamValue>, ctx?: any): Promise<any>;
  /** The export that was found, so the panel can say which one it is calling. */
  entry: string;
  /** Declared parameters, if the file exports a manifest. */
  specs: ParamSpec[];
  /** The manifest exactly as the module exported it, or null. Presets and handles are read from
   *  it by presets.ts and handles.ts — untrusted input, so each reader validates. */
  manifest: ModularManifest | null;
  dispose(): void;
}

const BARE_THREE = /(\bfrom\s*|\bimport\s*\(?\s*)(['"])three\2/g;
const BARE_PC = /(\bfrom\s*|\bimport\s*\(?\s*)(['"])playcanvas\2/g;
const RELATIVE = /(\bfrom\s*|\bimport\s*\(?\s*)(['"])(\.{1,2}\/[^'"]+)\2/g;

/** The other engine, when the asset is PlayCanvas: its module, the hidden app to build in, and
 *  the URL a bare `import 'playcanvas'` is pointed at. */
export interface PcEngine { pc: any; app: any; pcUrl: string }

/** The folder part of a path, with separators normalised. Windows paths arrive here too. */
export function dirOf(path: string): string {
  const p = path.replace(/\\/g, "/");
  const cut = p.lastIndexOf("/");
  return cut > 0 ? p.slice(0, cut) : "";
}

/** Join a relative import onto a folder, resolving `..` — no URL class, because a Windows path
 *  is not a URL and `new URL('../a', 'C:/b/c')` does not mean what it looks like it means. */
export function joinPath(dir: string, rel: string): string {
  const parts = dir.replace(/\\/g, "/").split("/").filter(Boolean);
  for (const seg of rel.replace(/\\/g, "/").split("/")) {
    if (!seg || seg === ".") continue;
    if (seg === "..") parts.pop();
    else parts.push(seg);
  }
  const abs = /^[A-Za-z]:/.test(dir) || dir.startsWith("/");
  return (dir.startsWith("/") ? "/" : "") + parts.join("/") + (abs ? "" : "");
}

/** A blob: module has no hierarchical base, so `/api/…` inside one is "invalid relative URL",
 *  not a path on this origin. Every specifier written into the file has to be absolute. */
function absolute(u: string): string {
  try { return new URL(u, window.location.href).href; } catch { return u; }
}

function rewrite(code: string, threeUrl: string, resolveRel: (rel: string) => string, pcUrl = ""): string {
  const three = absolute(threeUrl);
  let out = code.replace(BARE_THREE, (_m, pre: string, q: string) => `${pre}${q}${three}${q}`);
  if (pcUrl) { const pcu = absolute(pcUrl); out = out.replace(BARE_PC, (_m, pre: string, q: string) => `${pre}${q}${pcu}${q}`); }
  out = out.replace(RELATIVE, (_m, pre: string, q: string, path: string) => `${pre}${q}${absolute(resolveRel(path))}${q}`);
  return out;
}

const BUILDER = /^(build|make|create|generate)/i;

/**
 * Is this the body of a forge run rather than a module?
 *
 * The forge saves the exact code it ran beside every generation, and that code is not a module.
 * It has no imports and no exports, and it calls `add()` and `clear()` on names the forge hands
 * it. Opened as a file it dies on the first line — "clear is not defined" — which is a strange
 * thing for the Studio to say about a file the Studio itself wrote.
 *
 * The test is deliberately narrow: anything that exports is a module and is left alone.
 */
export function isForgeScript(code: string): boolean {
  if (/(^|\n)\s*export\s/.test(code)) return false;
  if (/(^|\n)\s*import\s+[^(]/.test(code)) return false;
  return /(^|[^.\w])add\s*\(/.test(code);
}

/**
 * The same script, wrapped in the module the editor knows how to open.
 *
 * It is given the four names the forge gives it, and whatever it adds becomes the asset. The body
 * goes inside an async function, so a script that awaits an import still works.
 */
export function wrapForgeScript(code: string, threeUrl: string): string {
  return [
    "import * as __three from " + JSON.stringify(absolute(threeUrl)) + ";",
    "export const manifest = { forgeScript: true };",
    "export async function build(THREE, params, __c) {",
    // The caller tries several signatures, so THREE may arrive as the params object, as the
    // PlayCanvas module, or not at all. Anything that cannot make a Group is not three.
    "  if (!THREE || typeof THREE.Group !== 'function') THREE = __three;",
    "  const group = new THREE.Group();",
    "  group.name = 'forge';",
    "  const add = (o) => { if (o) group.add(o); return o; };",
    "  const clear = () => { while (group.children.length) group.remove(group.children[0]); };",
    "  params = params || {};",
    // EVERY name the forge destructures into a run, so a script that uses one is not a
    // ReferenceError. The ones with no meaning in the editor are null rather than absent: a
    // script that reads `scene` gets null and can say so, where a missing binding just throws.
    "  const log = (...a) => { try { console.log(...a); } catch (e) { /* nothing to log to */ } };",
    "  const engine = 'three', pc = null, app = null, device = null;",
    "  const renderer = null, scene = null, camera = null, root = group;",
    "  const forge = { add, clear, log, params, group, root, THREE, engine };",
    // THE OTHER FIVE. `world.run` destructures these into every snippet it runs, and the snippet
    // the Studio writes for a model is `await loadGLB(url)` — so leaving them out made the app's
    // own generated code throw `loadGLB is not defined` the moment it came in by this path
    // instead of the other. They arrive on the context the editor passes; outside the editor
    // there is no context, and a name that cannot work says WHICH name and why rather than
    // failing as a ReferenceError that explains nothing.
    "  __c = __c || {};",
    "  const __need = (n) => async () => {",
    "    throw new Error(n + '() needs the editor: it is only available when the Studio runs this');",
    "  };",
    "  const loadGLB = __c.loadGLB || __need('loadGLB');",
    "  const buildAsset = __c.buildAsset || __need('buildAsset');",
    "  const buildFrom = __c.buildFrom || __need('buildFrom');",
    "  const importProject = __c.importProject || ((p) => import(/* @vite-ignore */ p));",
    "  const toObject = __c.toObject || ((v) => ({ object: (v && v.isObject3D) ? v : null }));",
    "  await (async () => {",
    code,
    "  })();",
    "  return group;",
    "}",
  ].join("\n");
}

/**
 * Import the file and find the one function that builds the asset.
 *
 * Nothing here guesses at what a returned value MEANS — the caller checks whether it is an
 * Object3D. Guessing at the call signature is bad enough; guessing at the result as well would
 * turn a wrong answer into a silently empty viewport.
 */
export async function loadFileAsset(code: string, threeUrl: string, resolveRel: (rel: string) => string, pcEngine?: PcEngine): Promise<FileAsset> {
  // A recorded forge run is not a module. Given the names the forge gives it, it becomes one.
  //
  // And when it is one, it is built as a THREE asset whatever else is open: a forge script is
  // written against `new THREE.Mesh(...)`, so calling it with `(pc, app, params)` first — which
  // is what happens when a PlayCanvas studio is up beside it — hands it a module that has no
  // such constructor.
  const forgeRun = isForgeScript(code);
  const pc = forgeRun ? undefined : pcEngine;
  let src = rewrite(code, threeUrl, resolveRel, pc?.pcUrl || "");
  if (forgeRun) src = wrapForgeScript(src, threeUrl);
  const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
  let mod: any;
  try {
    mod = await import(/* @vite-ignore */ url);
  } catch (e: any) {
    URL.revokeObjectURL(url);
    throw new Error("the file did not import: " + String(e?.message || e).slice(0, 400));
  }

  let entry = "";
  let fn: any = null;
  if (typeof mod.default === "function") { fn = mod.default; entry = "default"; }
  else if (typeof mod.build === "function") { fn = mod.build; entry = "build"; }
  else {
    for (const [k, v] of Object.entries(mod)) {
      if (typeof v === "function" && BUILDER.test(k)) { fn = v; entry = k; break; }
    }
  }
  if (!fn) {
    for (const [k, v] of Object.entries(mod)) {
      if (typeof v === "function") { fn = v; entry = k; break; }
    }
  }
  if (!fn) {
    URL.revokeObjectURL(url);
    throw new Error(
      "the file imported, but exports no function to build with. The editor calls the default "
      + "export, or one named build / make / create / generate. Exports found: "
      + (Object.keys(mod).join(", ") || "none"));
  }

  const specs = specsFromManifest(mod.manifest);
  const tried: string[] = [];
  const build = async (THREE: any, params: Record<string, ParamValue>, ctx?: any) => {
    // The three signatures an asset in this library actually uses, in the order a person would
    // guess. The first that returns an object wins; a thrown TypeError is a wrong guess, not a
    // broken asset, so it moves on rather than being reported.
    // A PlayCanvas asset needs the app as well as the module: a mesh cannot exist without a
    // graphics device. So its shapes lead with (pc, app, params), the mirror of (THREE, params).
    //
    // THE RUN CONTEXT GOES ON THE END OF EVERY SHAPE. The wrapper reads `loadGLB`, `buildAsset`
    // and the rest off it, and every shape here was written before there was one to pass — so the
    // Studio's own generated snippet for a model reached the wrapper with `__c` undefined and
    // refused itself. A builder that does not want it is unaffected: an extra argument is free.
    const calls: Array<[string, () => any]> = pc
      ? [
        ["(pc, app, params)", () => fn(pc.pc, pc.app, params, ctx)],
        ["(pc, params)", () => fn(pc.pc, params, ctx)],
        ["(params)", () => fn(params, ctx)],
        ["()", () => fn(undefined, undefined, ctx)],
      ]
      : [
        ["(THREE, params)", () => fn(THREE, params, ctx)],
        ["(params)", () => fn(params, ctx)],
        ["()", () => fn(undefined, undefined, ctx)],
      ];
    let lastErr: any = null;
    for (const [shape, call] of calls) {
      try {
        const out = await call();
        if (out && (out.isObject3D || Array.isArray(out) || typeof out.addChild === "function")) return out;
        tried.push(shape + " returned " + describe(out));
      } catch (e: any) {
        lastErr = e;
        tried.push(shape + " threw " + String(e?.message || e).slice(0, 120));
      }
    }
    throw new Error(
      "`" + entry + "` ran but never returned anything to show.\n" + tried.join("\n")
      + (lastErr ? "\n\n" + String(lastErr.stack || lastErr).slice(0, 800) : ""));
  };

  const manifest = mod.manifest && typeof mod.manifest === "object" ? (mod.manifest as ModularManifest) : null;
  return { build, entry, specs, manifest, dispose: () => URL.revokeObjectURL(url) };
}

function describe(v: any): string {
  if (v === undefined) return "undefined";
  if (v === null) return "null";
  if (typeof v === "object") return (v.type || v.constructor?.name || "an object") + " (not an Object3D)";
  return typeof v;
}

/**
 * Parameters an asset DECLARES, as opposed to the ones read out of its source.
 *
 * A declared parameter is the better contract of the two: it carries a range and a label, and it
 * is stored in the sidecar and handed to the build function, so nothing has to be rewritten in
 * the file at all. Both shapes below are accepted, because both are what people actually write.
 *
 *   export const manifest = { params: { sailHeight: { value: 0.9, min: 0, max: 2, label: '…' } } }
 *   export const manifest = { params: { sailHeight: 0.9 } }
 *
 * Two more fields come from the configurator contract (configurator.ts). `options: [...]` makes a
 * CHOICE — a dropdown of those strings — and `advanced: true` hides the setting until the panel's
 * Advanced switch is on. A `kind` the editor does not know is ignored and the kind is inferred
 * from the value instead: an unknown kind used to reach the panel as-is and draw no control.
 */
const KINDS = new Set<ParamSpec["kind"]>(["number", "bool", "text", "color", "choice"]);

/** The strings a choice offers, in order: non-empty strings only, first of any duplicate kept.
 *  Never trimmed — the build compares against the exact string, and so must the dropdown. */
export function choiceOptions(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const o of raw) {
    if (typeof o !== "string" || !o.trim() || out.includes(o)) continue;
    out.push(o);
    if (out.length >= 200) break;
  }
  return out;
}

export function specsFromManifest(manifest: any): ParamSpec[] {
  const out: ParamSpec[] = [];
  const params = manifest?.params;
  if (!params || typeof params !== "object") return out;
  const entries = Array.isArray(params)
    ? params.map((p: any) => [p?.key, p])
    : Object.entries(params);
  for (const [key, raw] of entries) {
    if (typeof key !== "string" || !key) continue;
    if (raw === null || raw === undefined) continue;
    if (typeof raw === "number" || typeof raw === "boolean" || typeof raw === "string") {
      out.push({
        key, label: labelOf(key), from: "declared", value: raw,
        kind: typeof raw === "number" ? "number" : typeof raw === "boolean" ? "bool"
          : /^#[0-9a-fA-F]{3,8}$/.test(raw) ? "color" : "text",
        ...(typeof raw === "number" ? rangeFor(raw) : {}),
      });
      continue;
    }
    if (typeof raw !== "object") continue;
    const label = typeof raw.label === "string" && raw.label ? raw.label : labelOf(key);
    const group = typeof raw.group === "string" ? raw.group : undefined;
    const advanced = raw.advanced === true ? { advanced: true } : {};
    const given = raw.value ?? raw.default;
    // A CHOICE. Its value stays whatever the manifest said, even when that is not one of the
    // options — the panel shows such a value as unknown rather than silently replacing it, and
    // the build receives exactly what the author wrote. With no value at all, the first option.
    const options = choiceOptions(raw.options);
    if (options.length) {
      const value = typeof given === "string" ? given
        : given === undefined || given === null ? options[0] : String(given);
      out.push({ key, from: "declared", value, kind: "choice", options, label, group, ...advanced });
      continue;
    }
    const v = given ?? 0;
    const inferred: ParamSpec["kind"] = typeof v === "boolean" ? "bool"
      : typeof v === "string" ? (/^#[0-9a-fA-F]{3,8}$/.test(v) ? "color" : "text") : "number";
    // "choice" without options cannot be drawn as a dropdown, so it is inferred like any other.
    const kind: ParamSpec["kind"] = KINDS.has(raw.kind) && raw.kind !== "choice" ? raw.kind : inferred;
    out.push({
      key, from: "declared", value: v, kind,
      label,
      group,
      ...advanced,
      ...(kind === "number"
        ? {
          min: typeof raw.min === "number" ? raw.min : rangeFor(Number(v)).min,
          max: typeof raw.max === "number" ? raw.max : rangeFor(Number(v)).max,
          step: typeof raw.step === "number" ? raw.step : rangeFor(Number(v)).step,
        }
        : {}),
    });
  }
  return out;
}

/** Declared first, then anything detected in the source that a manifest did not already name. */
export function mergeSpecs(declared: ParamSpec[], detected: ParamSpec[]): ParamSpec[] {
  const seen = new Set(declared.map((s) => s.key));
  return [...declared, ...detected.filter((s) => !seen.has(s.key))];
}
