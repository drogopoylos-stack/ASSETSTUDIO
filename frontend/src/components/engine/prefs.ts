// The Studio engine's settings: one nested object under the `engine` key of the shared settings,
// edited in Settings → "Studio engine", read by the Engine window and the editor.
//
// Why one nested key and not a dozen flat ones: the backend deep-merges patches, so the pane can
// save a single field at a time, and a backend that has never heard of a field keeps it anyway.
// Why a cache in localStorage: the window's first render happens before any request can answer,
// and the tab it opens on has to be decided right then.
//
// This module is deliberately free of the API client, so it can be bundled into a node test.

import type { WorldOptions } from "./edit/world";

export interface ViewSize { w: number; h: number; label: string }

/** The Game tab's sizes. The chosen one is saved as `cc_engine_view`, which the live link reads
 *  to open a game at the same size (and a phone size gives agents a touch layout). */
export const VIEW_PRESETS: ViewSize[] = [
  { w: 1920, h: 1080, label: "1080p" },
  { w: 1600, h: 900, label: "900p" },
  { w: 1366, h: 768, label: "laptop" },
  { w: 1280, h: 720, label: "720p" },
  { w: 1024, h: 768, label: "4:3" },
  { w: 390, h: 844, label: "phone" },
  { w: 820, h: 1180, label: "tablet" },
];

export interface EnginePrefs {
  /** Which tab the window opens on. "last" remembers the one it was closed on. */
  open_tab: "asset" | "edit" | "game" | "library" | "last";

  // ---- the editor, as it opens
  shading: "wire" | "solid" | "material" | "rendered";
  solid_light: "studio" | "matcap" | "flat";
  /** Where Solid takes its colour from. Blender keeps this on a separate axis from the lighting,
   *  and so does this: "material" is each part's own base colour, "single" is one grey. */
  solid_color: "material" | "single";
  space: "global" | "local";
  weights: "envelope" | "heat";
  bake_size: number;
  bake_rays: number;
  rebake_ms: number;
  undo_max: number;
  grid: boolean;
  axes: boolean;
  outline: boolean;
  bones: boolean;
  extras: boolean;
  /** Terrain mode in the Edit tab: the heightfield, the nine brushes, the layer palette
   *  and the scatter. Off, the mode is not offered and no `.terrain.json` is looked for. */
  terrain: boolean;
  /** Chunked ground in the viewport: one mesh per square of the field, each with its own
   *  bounding sphere and its own detail level. "auto" turns it on above the threshold in
   *  `chunkAbove` and leaves smaller fields as one mesh, where chunking is pure overhead. */
  terrain_chunks: "auto" | "on" | "off";
  /** Scattered items drawn as one instanced draw call per asset instead of one clone each. */
  terrain_instances: boolean;

  // ---- the viewport
  /** Device pixels per CSS pixel, at most. 0 means the screen's own ratio with no cap. */
  pixel_ratio: number;
  shadows: boolean;
  fov: number;
  orbit_speed: number;
  zoom_speed: number;
  invert_orbit: boolean;
  /** Which hand the mouse is laid out for. "blender" is Blender's and Godot's (middle drag
   *  orbits, Shift+middle pans); "unity" is Unity's (Alt+left orbits, middle drag pans, Alt+right
   *  dollies). Holding the right button flies with W A S D in both. */
  nav_scheme: "blender" | "unity";
  /** The wheel zooms toward the point under the cursor rather than the centre of the view. */
  zoom_to_cursor: boolean;

  // ---- the live link, the forge and the headless browser (read by the backend)
  live_sidecar: boolean;
  forge_quality: "draft" | "normal" | "high";
  forge_views: string[];
  /** How much empty backdrop a forge panel keeps around the subject. 1.0 is edge to edge;
   *  the old 1.35 spent a third of every panel on nothing, which is a third of the
   *  resolution the subject could have had. */
  forge_margin: number;
  /** Detail review: a character's face, head, hair and torso photographed close up from four
   *  sides beside the same crop of the reference, with an upside-down and mirrored check. */
  forge_detail: boolean;
  /** Colour and size per part, against the reference: what the part's own colour is, what the
   *  reference's is in the same place, and how tall and wide it is as a share of the figure.
   *  This is the swatch strip and the ratio ladder an agent used to write its own scripts for. */
  forge_colours: boolean;
  /** The checks the goblin A/B added: a centred part (a belt, a buckle, a nose) that sits off the
   *  midline, a hole a person can see, a gap where two parts should touch, and a part whose light
   *  and dark are much flatter than the reference's. Read by the backend as `forge_checks`. */
  forge_checks: boolean;
  /** A picture with every scene change: an agent's edit or place answers with before beside after,
   *  framed on the object, so it SEES the move instead of reading numbers about it. */
  scene_look: boolean;
  /** The live view of the agent's own game tab in the Game tab: frames a second, JPEG quality and
   *  width. Only spent while someone is watching. */
  live_watch_fps: number;
  live_watch_quality: number;
  live_watch_width: number;
  browser_idle_min: number;
  /** A game tab no agent has called for this many minutes is closed (a watched one stays). Read by
   *  the backend as `live_tab_idle_min`. */
  live_tab_idle_min: number;
}

export const ENGINE_DEFAULTS: EnginePrefs = {
  open_tab: "asset",
  // Material preview, coloured. The editor used to open on Solid with one grey material, so an
  // asset whose whole design is its colour opened looking like unpainted clay.
  shading: "material", solid_light: "studio", solid_color: "material",
  space: "global", weights: "envelope",
  bake_size: 256, bake_rays: 24, rebake_ms: 600, undo_max: 60,
  grid: true, axes: true, outline: true, bones: true, extras: true,
  terrain: true, terrain_chunks: "auto", terrain_instances: true,
  pixel_ratio: 2, shadows: true, fov: 38, orbit_speed: 1, zoom_speed: 1, invert_orbit: false,
  nav_scheme: "blender", zoom_to_cursor: true,
  live_sidecar: true, forge_quality: "normal", forge_views: ["3q"], forge_margin: 1.06,
  forge_detail: true,
  forge_colours: true,
  forge_checks: true,
  scene_look: true,
  live_watch_fps: 8, live_watch_quality: 70, live_watch_width: 1280,
  browser_idle_min: 10,
  live_tab_idle_min: 10,
};

const num = (v: any, d: number, lo: number, hi: number) => {
  const n = typeof v === "number" ? v : parseFloat(v);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d;
};
const oneOf = <T extends string>(v: any, all: readonly T[], d: T): T => (all.includes(v) ? v : d);
const bool = (v: any, d: boolean) => (typeof v === "boolean" ? v : d);

/** Whatever the settings hold, made whole and sane: unknown values fall back, numbers are clamped. */
export function mergePrefs(raw: any): EnginePrefs {
  const r = raw && typeof raw === "object" ? raw : {};
  const D = ENGINE_DEFAULTS;
  return {
    open_tab: oneOf(r.open_tab, ["asset", "edit", "game", "library", "last"] as const, D.open_tab),
    shading: oneOf(r.shading, ["wire", "solid", "material", "rendered"] as const, D.shading),
    solid_light: oneOf(r.solid_light, ["studio", "matcap", "flat"] as const, D.solid_light),
    solid_color: oneOf(r.solid_color, ["material", "single"] as const, D.solid_color),
    space: oneOf(r.space, ["global", "local"] as const, D.space),
    weights: oneOf(r.weights, ["envelope", "heat"] as const, D.weights),
    bake_size: [128, 256, 512, 1024, 2048].includes(r.bake_size) ? r.bake_size : D.bake_size,
    bake_rays: Math.round(num(r.bake_rays, D.bake_rays, 4, 256)),
    rebake_ms: Math.round(num(r.rebake_ms, D.rebake_ms, 0, 10000)),
    undo_max: Math.round(num(r.undo_max, D.undo_max, 1, 1000)),
    grid: bool(r.grid, D.grid), axes: bool(r.axes, D.axes), outline: bool(r.outline, D.outline),
    bones: bool(r.bones, D.bones), extras: bool(r.extras, D.extras),
    terrain: bool(r.terrain, D.terrain),
    terrain_chunks: oneOf(r.terrain_chunks, ["auto", "on", "off"] as const, D.terrain_chunks),
    terrain_instances: bool(r.terrain_instances, D.terrain_instances),
    pixel_ratio: num(r.pixel_ratio, D.pixel_ratio, 0, 4),
    shadows: bool(r.shadows, D.shadows),
    fov: num(r.fov, D.fov, 10, 120),
    orbit_speed: num(r.orbit_speed, D.orbit_speed, 0.1, 5),
    zoom_speed: num(r.zoom_speed, D.zoom_speed, 0.1, 5),
    invert_orbit: bool(r.invert_orbit, D.invert_orbit),
    nav_scheme: oneOf(r.nav_scheme, ["blender", "unity"] as const, D.nav_scheme),
    zoom_to_cursor: bool(r.zoom_to_cursor, D.zoom_to_cursor),
    live_sidecar: bool(r.live_sidecar, D.live_sidecar),
    forge_quality: oneOf(r.forge_quality, ["draft", "normal", "high"] as const, D.forge_quality),
    forge_views: Array.isArray(r.forge_views) && r.forge_views.length
      ? r.forge_views.filter((v: any) => typeof v === "string") : D.forge_views,
    forge_margin: num(r.forge_margin, D.forge_margin, 1, 2.5),
    forge_detail: bool(r.forge_detail, D.forge_detail),
    forge_colours: bool(r.forge_colours, D.forge_colours),
    forge_checks: bool(r.forge_checks, D.forge_checks),
    scene_look: bool(r.scene_look, D.scene_look),
    live_watch_fps: num(r.live_watch_fps, D.live_watch_fps, 1, 30),
    live_watch_quality: num(r.live_watch_quality, D.live_watch_quality, 30, 95),
    live_watch_width: num(r.live_watch_width, D.live_watch_width, 320, 1920),
    browser_idle_min: num(r.browser_idle_min, D.browser_idle_min, 1, 24 * 60),
    live_tab_idle_min: num(r.live_tab_idle_min, D.live_tab_idle_min, 1, 24 * 60),
  };
}

/** The viewport's share of the settings, in the shape the world takes. */
export function worldOptions(p: EnginePrefs): WorldOptions {
  return {
    pixelRatio: p.pixel_ratio, shadows: p.shadows, fov: p.fov,
    orbitSpeed: p.orbit_speed, zoomSpeed: p.zoom_speed, invertOrbit: p.invert_orbit,
    navScheme: p.nav_scheme, zoomToCursor: p.zoom_to_cursor,
  };
}

const CACHE = "engine.prefs";
export const SETTINGS_CHANNEL = "studio-settings";

/** The last settings this browser saw, for the first render. Defaults on a first visit. */
export function readCachedPrefs(): EnginePrefs {
  try { return mergePrefs(JSON.parse(localStorage.getItem(CACHE) || "null")); } catch { return { ...ENGINE_DEFAULTS }; }
}

export function cachePrefs(p: EnginePrefs) {
  try { localStorage.setItem(CACHE, JSON.stringify(p)); } catch { /* private mode; the next boot asks the API */ }
}

/** Tell every other window of the Studio that settings changed. The client calls this after a save. */
export function announceSettings(patch: any) {
  try {
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel(SETTINGS_CHANNEL);
    ch.postMessage({ patch: patch || {} });
    ch.close();
  } catch { /* nothing listens; nothing lost */ }
}

/** Run `reload` whenever the settings may have changed: another window saved some, or this one
 *  came back into focus. Returns the function that stops listening. */
export function watchPrefs(reload: () => void): () => void {
  let ch: BroadcastChannel | null = null;
  try {
    if (typeof BroadcastChannel !== "undefined") {
      ch = new BroadcastChannel(SETTINGS_CHANNEL);
      ch.onmessage = (e) => { if (!e?.data?.patch || "engine" in e.data.patch || "cc_engine_view" in e.data.patch) reload(); };
    }
  } catch { ch = null; }
  const onFocus = () => reload();
  window.addEventListener("focus", onFocus);
  return () => { window.removeEventListener("focus", onFocus); try { ch?.close(); } catch { /* closed */ } };
}
