// Preset thumbnails: every preset of a modular asset, built and photographed small, one at a time,
// in the time the viewport is not using.
//
// THREE DECISIONS, each forced by something in this codebase:
//
//   1. The SAME three and the SAME build. The object is made by the asset's own build function
//      with the viewport's own THREE (`EditWorld.T`) — a second copy of three makes objects the
//      renderer throws on (see the long note in world.ts `loadGLB`), so none is ever imported here.
//
//   2. The SAME renderer, not a second one. A browser allows about sixteen WebGL contexts and
//      drops the OLDEST when a new one goes past that — which would be the editor's own viewport.
//      So a thumbnail is drawn by the viewport's renderer into a corner of its canvas, copied out
//      in the same task, and the frame is redrawn at once; the corner is never composited. It also
//      means the thumbnail is lit by the studio's own environment map, colour-managed the same way,
//      and antialiased by the same multisampling: it looks like the viewport because it IS it.
//
//   3. IDLE TIME ONLY. A build is on the main thread whatever we do, so the queue only moves when
//      the editor says it is not busy — no rebuild running, no drag in progress, nothing changed
//      in the last beat — and then one thumbnail per idle slot. A cache keyed by the source and
//      the exact values means a preset is photographed once per session, not once per open.

import { useEffect, useMemo, useRef, useState } from "react";
import type { ParamValue, V3 } from "./kit";

// ------------------------------------------------------------------ pure: keys and framing

/** FNV-1a, 32 bit, as base 36. Not cryptographic: it only has to tell two sources apart. */
export function hashString(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(36);
}

/** The values as one string that does not depend on the order the keys were written in. */
export function stableValues(values: Record<string, ParamValue>): string {
  return JSON.stringify(Object.keys(values).sort().map((k) => [k, values[k]]));
}

/** One thumbnail's cache key: which source, and exactly what it was built with. */
export function thumbKey(sig: string, values: Record<string, ParamValue>): string {
  return sig + "\u0001" + stableValues(values);
}

const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: V3): V3 => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

/** The viewport's HERO direction — the same vector world.ts calls "3q". */
export const HERO_DIR: V3 = [1.1, 0.75, 1.35];

/**
 * Where a camera looking back along `dir` has to stand to fit a box, and what it should aim at.
 *
 * Fitted to the box's eight CORNERS, not to its bounding sphere: a sphere leaves a wide building
 * floating in empty space, and a thumbnail is too small to waste a third of. The aim is then moved
 * until the projected box is centred on screen, because a three-quarter view of a tall box puts
 * its centre below the middle of the frame. `margin` is breathing room: 1.1 leaves about a tenth.
 */
export function fitView(min: V3, max: V3, dir: V3, fovDeg: number, aspect: number, margin = 1.1):
  { target: V3; distance: number; position: V3 } {
  const d = unit(dir);
  const f: V3 = [-d[0], -d[1], -d[2]];
  const up: V3 = Math.abs(f[1]) > 0.999 ? [0, 0, -1] : [0, 1, 0];
  const right = unit(cross(f, up));
  const camUp = cross(right, f);
  const tanV = Math.tan((Math.max(1, Math.min(170, fovDeg)) * Math.PI) / 360);
  const tanH = tanV * Math.max(1e-3, aspect);
  const corners: V3[] = [];
  for (const x of [min[0], max[0]]) for (const y of [min[1], max[1]]) for (const z of [min[2], max[2]]) corners.push([x, y, z]);
  let target: V3 = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  let distance = 0;
  for (let iter = 0; iter < 4; iter++) {
    distance = 0;
    for (const p of corners) {
      const q = sub(p, target);
      const x = dot(q, right), y = dot(q, camUp), z = dot(q, d);
      distance = Math.max(distance, z + Math.abs(x) / tanH, z + Math.abs(y) / tanV);
    }
    distance = Math.max(1e-4, distance * margin);
    let lx = Infinity, hx = -Infinity, ly = Infinity, hy = -Infinity;
    for (const p of corners) {
      const q = sub(p, target);
      const depth = Math.max(1e-6, distance - dot(q, d));
      const nx = dot(q, right) / (depth * tanH), ny = dot(q, camUp) / (depth * tanV);
      lx = Math.min(lx, nx); hx = Math.max(hx, nx); ly = Math.min(ly, ny); hy = Math.max(hy, ny);
    }
    const ox = (lx + hx) / 2, oy = (ly + hy) / 2;
    if ((Math.abs(ox) < 1e-4 && Math.abs(oy) < 1e-4) || iter === 3) break;
    target = add(target, add(mul(right, ox * distance * tanH), mul(camUp, oy * distance * tanV)));
  }
  return { target, distance, position: add(target, mul(d, distance)) };
}

/**
 * The sentence a failed thumbnail shows. The loader's own message leads with a summary — "`build`
 * ran but never returned anything to show" — and puts what the asset actually THREW on the lines
 * after it, one per call shape it tried. The first of those is the one a person needs.
 */
export function thumbError(err: unknown): string {
  const text = String((err as any)?.message ?? err ?? "unknown error");
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const threw = lines.find((l) => /^\([^)]*\) threw /.test(l));
  const why = threw ? threw.replace(/^\([^)]*\) threw /, "") : lines[0] || "unknown error";
  return why.slice(0, 240);
}

// ------------------------------------------------------------------ the cache

interface Shot { url?: string; error?: string }
const CACHE = new Map<string, Shot>();
const CACHE_MAX = 300;

function cacheGet(k: string): Shot | undefined {
  const v = CACHE.get(k);
  if (v) { CACHE.delete(k); CACHE.set(k, v); }   // least recently used goes first
  return v;
}
function cacheSet(k: string, v: Shot) {
  CACHE.set(k, v);
  while (CACHE.size > CACHE_MAX) CACHE.delete(CACHE.keys().next().value as string);
}

// ------------------------------------------------------------------ the photograph

/** How big a thumbnail is, in CSS pixels. The canvas holds it at the viewport's pixel ratio. */
export const THUMB_W = 160;
export const THUMB_H = 120;
const THUMB_BG = 0x1d222b;
const THUMB_FOV = 30;

/**
 * Photograph one built object with the viewport's own renderer.
 *
 * The object is lit by a copy of the forge rig (world.ts, member for member) and the studio's own
 * environment map, framed from the HERO direction, drawn into the bottom-left corner of the
 * viewport's canvas, and copied out as a WebP data URL in the same task. Every renderer setting it
 * touches is put back in a `finally`, and `redraw` repaints the viewport before the task ends, so
 * the corner is never on screen. Throws when there is nothing to photograph.
 */
export function shootThumb(T: any, renderer: any, obj: any, opts: { env?: any; redraw?: () => void } = {}): string {
  if (!T || !renderer || !obj?.isObject3D) throw new Error("nothing to photograph");
  obj.updateMatrixWorld(true);
  const box = new T.Box3().setFromObject(obj);
  if (box.isEmpty() || ![box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z].every(Number.isFinite)) {
    throw new Error("it built, but there is nothing in it to see");
  }
  const scene = new T.Scene();
  scene.background = new T.Color(THUMB_BG);
  if (opts.env) scene.environment = opts.env;
  const key = new T.DirectionalLight(0xffffff, 2.6); key.position.set(3, 5, 4);
  const fill = new T.DirectionalLight(0xbcd0ff, 0.9); fill.position.set(-4, 1.5, 3);
  const rim = new T.DirectionalLight(0xffe6c0, 1.6); rim.position.set(-2, 3, -5);
  const amb = new T.HemisphereLight(0xa8c4ff, 0x40332a, 0.7);
  scene.add(key, fill, rim, amb);

  // A soft contact shadow under the footprint, so the object stands on something rather than
  // floating in the tile. Made per shot and thrown away with it: 64 x 64 pixels.
  const size = box.getSize(new T.Vector3());
  const centre = box.getCenter(new T.Vector3());
  const blob = document.createElement("canvas");
  blob.width = blob.height = 64;
  const bg = blob.getContext("2d");
  let shadow: any = null;
  if (bg) {
    const grad = bg.createRadialGradient(32, 32, 2, 32, 32, 32);
    grad.addColorStop(0, "rgba(0,0,0,0.55)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    bg.fillStyle = grad;
    bg.fillRect(0, 0, 64, 64);
    const tex = new T.CanvasTexture(blob);
    shadow = new T.Mesh(new T.PlaneGeometry(1, 1), new T.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2;
    shadow.scale.set(Math.max(size.x, 1e-3) * 1.45, Math.max(size.z, 1e-3) * 1.45, 1);
    shadow.position.set(centre.x, box.min.y + Math.max(size.y, 1e-3) * 0.002, centre.z);
    shadow.renderOrder = -1;
    scene.add(shadow);
  }
  scene.add(obj);

  const cssW = THUMB_W, cssH = THUMB_H;
  const fit = fitView([box.min.x, box.min.y, box.min.z], [box.max.x, box.max.y, box.max.z], HERO_DIR, THUMB_FOV, cssW / cssH, 1.12);
  const radius = Math.max(1e-4, size.length() / 2);
  const cam = new T.PerspectiveCamera(THUMB_FOV, cssW / cssH, Math.max(1e-4, fit.distance - radius * 2), fit.distance + radius * 4);
  cam.position.set(fit.position[0], fit.position[1], fit.position[2]);
  cam.lookAt(fit.target[0], fit.target[1], fit.target[2]);
  cam.updateMatrixWorld(true);

  const canvas: HTMLCanvasElement = renderer.domElement;
  const pr: number = renderer.getPixelRatio();
  const view = renderer.getSize(new T.Vector2());
  const w = Math.min(cssW, Math.floor(view.x)), h = Math.min(cssH, Math.floor(view.y));
  if (w < 16 || h < 16) throw new Error("the viewport is too small to photograph in");

  const prevViewport = renderer.getViewport(new T.Vector4());
  const prevScissor = renderer.getScissor(new T.Vector4());
  const prevScissorTest = renderer.getScissorTest();
  const prevTarget = renderer.getRenderTarget();
  const prevAuto = renderer.autoClear;
  try {
    renderer.setRenderTarget(null);
    renderer.setViewport(0, 0, w, h);
    renderer.setScissor(0, 0, w, h);
    renderer.setScissorTest(true);
    renderer.autoClear = true;
    renderer.render(scene, cam);
    const out = document.createElement("canvas");
    out.width = Math.round(w * pr);
    out.height = Math.round(h * pr);
    const g = out.getContext("2d");
    if (!g) throw new Error("no 2d canvas to copy the picture into");
    // GL's origin is the bottom-left; the canvas image's is the top-left.
    g.drawImage(canvas, 0, canvas.height - out.height, out.width, out.height, 0, 0, out.width, out.height);
    const url = out.toDataURL("image/webp", 0.9);
    return url.startsWith("data:image/") ? url : out.toDataURL("image/png");
  } finally {
    renderer.setScissorTest(prevScissorTest);
    renderer.setScissor(prevScissor);
    renderer.setViewport(prevViewport);
    renderer.setRenderTarget(prevTarget);
    renderer.autoClear = prevAuto;
    scene.remove(obj);
    if (shadow) { shadow.geometry.dispose(); shadow.material.map?.dispose(); shadow.material.dispose(); }
    try { opts.redraw?.(); } catch { /* the viewport's own frame will try again */ }
  }
}

/** Every geometry, material and texture under a root: what must NOT be disposed with a
 *  thumbnail, because an asset may share one module-level material between every build. */
export function liveResources(root: any): Set<any> {
  const keep = new Set<any>();
  root?.traverse?.((o: any) => {
    if (o.geometry) keep.add(o.geometry);
    for (const m of Array.isArray(o.material) ? o.material : o.material ? [o.material] : []) {
      keep.add(m);
      for (const v of Object.values(m)) if ((v as any)?.isTexture) keep.add(v);
    }
  });
  return keep;
}

/** Give a thumbnail's GPU memory back — everything it made that the live asset is not using. */
export function releaseThumb(obj: any, keep: Set<any>) {
  obj?.traverse?.((o: any) => {
    if (o.geometry && !keep.has(o.geometry)) o.geometry.dispose?.();
    for (const m of Array.isArray(o.material) ? o.material : o.material ? [o.material] : []) {
      if (keep.has(m)) continue;
      for (const v of Object.values(m)) if ((v as any)?.isTexture && !keep.has(v)) (v as any).dispose?.();
      m.dispose?.();
    }
  });
}

// ------------------------------------------------------------------ the queue

export interface ThumbState {
  status: "pending" | "ready" | "failed";
  url?: string;
  error?: string;
}

export interface ThumbJob { name: string; values: Record<string, ParamValue> }

/** How long one build may take before its thumbnail is marked failed instead of holding the queue. */
const BUILD_TIMEOUT_MS = 10000;

/**
 * The thumbnails of a list of presets, rendered in idle time, as a map from preset name to state.
 *
 * `build` makes the object (never touching the live viewport), `shoot` photographs it, `release`
 * frees it, `busy` says when to wait. The queue restarts from the cache whenever the source or the
 * list changes, so a thumbnail already taken this session comes back instantly.
 */
export function usePresetThumbs(o: {
  enabled: boolean;
  sig: string;
  jobs: ThumbJob[];
  build(values: Record<string, ParamValue>): Promise<any>;
  shoot(obj: any): string;
  release(obj: any): void;
  busy(): boolean;
}): Record<string, ThumbState> {
  const [states, setStates] = useState<Record<string, ThumbState>>({});
  const fns = useRef(o);
  fns.current = o;
  const plan = useMemo(
    () => (o.enabled && o.sig ? o.sig + "\u0002" + JSON.stringify(o.jobs.map((j) => [j.name, stableValues(j.values)])) : ""),
    [o.enabled, o.sig, o.jobs]);

  useEffect(() => {
    if (!plan) { setStates({}); return; }
    const { sig, jobs } = fns.current;
    let dead = false;
    let timer = 0;
    let idle = 0;
    const initial: Record<string, ThumbState> = {};
    const queue: Array<ThumbJob & { key: string }> = [];
    for (const j of jobs) {
      const key = thumbKey(sig, j.values);
      const hit = cacheGet(key);
      if (hit) initial[j.name] = hit.url ? { status: "ready", url: hit.url } : { status: "failed", error: hit.error };
      else { initial[j.name] = { status: "pending" }; queue.push({ ...j, key }); }
    }
    setStates(initial);

    const later = (ms: number) => { timer = window.setTimeout(pump, ms); };
    async function one() {
      idle = 0;
      if (dead) return;
      if (fns.current.busy()) { later(300); return; }
      const job = queue.shift();
      if (!job) return;
      let obj: any = null;
      let shot: Shot;
      try {
        obj = await Promise.race([
          fns.current.build(job.values),
          new Promise((_, rej) => window.setTimeout(() => rej(new Error("the build took longer than " + BUILD_TIMEOUT_MS / 1000 + " s")), BUILD_TIMEOUT_MS)),
        ]);
        if (dead) return;
        // The build awaited; the person may have grabbed a slider meanwhile. Photograph later.
        if (fns.current.busy()) { queue.unshift(job); later(300); return; }
        shot = { url: fns.current.shoot(obj) };
      } catch (e: any) {
        shot = { error: thumbError(e) };
      } finally {
        if (obj) { try { fns.current.release(obj); } catch { /* freed or never allocated */ } }
      }
      if (dead) return;
      cacheSet(job.key, shot);
      setStates((s) => ({ ...s, [job.name]: shot.url ? { status: "ready", url: shot.url } : { status: "failed", error: shot.error } }));
      if (queue.length) later(40);
    }
    function pump() {
      timer = 0;
      if (dead || !queue.length) return;
      const ric = (window as any).requestIdleCallback as undefined | ((cb: () => void, o?: { timeout: number }) => number);
      if (ric) idle = ric(() => { void one(); }, { timeout: 1500 });
      else timer = window.setTimeout(() => { timer = 0; void one(); }, 60);
    }
    // A beat first: the asset has just been built, and the viewport's first frames matter more.
    if (queue.length) later(400);
    return () => {
      dead = true;
      if (timer) window.clearTimeout(timer);
      const cic = (window as any).cancelIdleCallback as undefined | ((id: number) => void);
      if (idle && cic) cic(idle);
    };
  }, [plan]);

  return states;
}
