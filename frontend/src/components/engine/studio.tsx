// The forge's studio, built in the window instead of inside a game.
//
// live_forge.py builds a lit studio beside a running game and photographs an asset in it. A
// photograph cannot be turned around, so this is the same studio built in the Studio's own page:
// the recorded code is run again, in the project's own engine, and the result can be orbited.
// The lights, the environment, the framing passes and the stats are kept deliberately identical
// to the forge's — a generation that framed as "fit" there must frame as "fit" here, or the two
// can never be compared and the window is a different renderer pretending to be the same one.
//
// Neither engine is a dependency of this frontend. The module arrives at runtime by `import()`
// from the project that made the asset, so everything engine-shaped is `any` on purpose.

import type { EngineStats } from "../../types";

export type EngineKind = "three" | "playcanvas";
export type V3 = [number, number, number];

export interface Bounds { c: V3; size: V3; radius: number }
export interface StudioOpts { background: string; ground: boolean; sky: boolean }
export type LiveStats = EngineStats & { gpu_triangles?: number; textures?: number; error?: string };
export interface RunResult { value: unknown; error: string; ms: number }
export interface LoadedEngine { mod: any; kind: EngineKind; url: string }

export const FOV = 38;
export const VIEWS: Record<string, V3> = {
  "3q": [1, 0.62, 1.15], front: [0, 0, 1], back: [0, 0, -1], side: [1, 0, 0],
  left: [-1, 0, 0], top: [0, 1, 0.0001], bottom: [0, -1, 0.0001],
  low: [0.8, -0.32, 1], hero: [0.55, 0.28, 1], back3q: [-1, 0.5, -1],
};
/** Where each engine's ES build lives under node_modules — the same table the backend serves from. */
export const MODULE_FILES: Record<EngineKind, string> = {
  three: "three/build/three.module.js",
  playcanvas: "playcanvas/build/playcanvas.mjs",
};

const r3 = (n: number) => Math.round(n * 1000) / 1000;

// ------------------------------------------------------------------ engine
export function kindOf(m: any): EngineKind | "" {
  try {
    if (m.WebGLRenderer && m.Scene && m.PerspectiveCamera) return "three";
    if (m.Application && m.Entity && m.StandardMaterial) return "playcanvas";
  } catch { /* not a module namespace */ }
  return "";
}

// A module URL whose fetch failed once fails forever in this document: the module map records
// the failure and the network is never asked again. The endpoint 404s until the backend is
// restarted and node_modules appears after an npm install, so a retry has to be a fresh URL.
const failed = new Set<string>();

const shortUrl = (u: string) => {
  try {
    const x = new URL(u, location.href);
    const p = x.searchParams.get("project");
    return p ? p.split(/[\\/]/).filter(Boolean).pop() || p : x.host + x.pathname;
  } catch { return u; }
};

export async function loadEngine(urls: string[], want: EngineKind | ""): Promise<LoadedEngine> {
  const errors: string[] = [];
  for (const base of urls) {
    const url = failed.has(base) ? `${base}${base.includes("?") ? "&" : "?"}r=${Date.now()}` : base;
    try {
      const m = await import(/* @vite-ignore */ url);
      const ns = kindOf(m) ? m : (m && m.default) || {};
      const k = kindOf(ns);
      if (k && (!want || k === want)) return { mod: ns, kind: k, url: base };
      errors.push(`${shortUrl(base)}: loaded, but it is not ${want || "three.js or PlayCanvas"}`);
    } catch (e: any) {
      failed.add(base);
      errors.push(`${shortUrl(base)}: ${String(e?.message || e).slice(0, 160)}`);
    }
  }
  throw new Error(errors.join("\n") || "no engine module to load");
}

// ------------------------------------------------------------------ studio
function hexToRgb(hex: string) {
  let h = String(hex || "#1a1e26").replace("#", "");
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const n = parseInt(h, 16);
  return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255 };
}

// The world. A `metalness: 1` blade under three directional lights and nothing else renders pure
// black — correctly, because a metal takes its colour from what it reflects. The forge found that
// out from a picture, and the same gradient it uses is used here: sky over ground, one warm blob
// for a metal to catch. It lights the scene without ever being drawn.
function envCanvas(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 128;
  const x = c.getContext("2d")!;
  const g = x.createLinearGradient(0, 0, 0, 128);
  g.addColorStop(0.00, "#93b8ec");
  g.addColorStop(0.46, "#e4ecf7");
  g.addColorStop(0.54, "#70757f");
  g.addColorStop(1.00, "#2b2e35");
  x.fillStyle = g;
  x.fillRect(0, 0, 256, 128);
  const s = x.createRadialGradient(72, 32, 2, 72, 32, 50);
  s.addColorStop(0, "rgba(255,247,226,1)");
  s.addColorStop(1, "rgba(255,247,226,0)");
  x.fillStyle = s;
  x.fillRect(0, 0, 256, 128);
  return c;
}

function asFlat(v: any): any {
  try {
    if (!v) return null;
    if (typeof HTMLCanvasElement !== "undefined" && v instanceof HTMLCanvasElement) return v;
    if (typeof HTMLImageElement !== "undefined" && v instanceof HTMLImageElement) return v;
    if (typeof ImageBitmap !== "undefined" && v instanceof ImageBitmap) return v;
    if (typeof ImageData !== "undefined" && v instanceof ImageData) return v;
    const src = v.image || (v.getSource && v.getSource()) || (v._levels && v._levels[0]);
    if (src && (src.width || src.videoWidth)) return src;
  } catch { /* not image-like */ }
  return null;
}

export abstract class Studio {
  abstract readonly kind: EngineKind;
  readonly canvas: HTMLCanvasElement;
  readonly opts: StudioOpts;
  framed = "unknown";
  /** An image the code handed to `add`: judged flat on a checkerboard, never lit. */
  flat: any = null;
  bgPixel: V3 | null = null;
  log: string[] = [];
  /** Of the last framed subject. The orbit scales its clip planes by it. */
  radius = 1;
  protected engine: any; protected app: any; protected device: any; protected renderer: any;
  protected scene: any; protected camera: any; protected root: any; protected ground: any;

  protected constructor(canvas: HTMLCanvasElement, opts: StudioOpts) {
    this.canvas = canvas;
    this.opts = opts;
  }

  abstract clear(): void;
  abstract bounds(): Bounds | null;
  abstract render(dt?: number): void;
  abstract resize(w: number, h: number): void;
  abstract setCamera(pos: V3, target: V3): void;
  abstract setClip(near: number, far: number): void;
  abstract cameraPosition(): V3;
  abstract stats(): LiveStats;
  protected abstract ndc(p: V3): { x: number; y: number; behind: boolean };
  protected abstract placeGround(b: Bounds): void;
  protected abstract addOne(t: any): void;
  protected abstract teardown(): void;

  /** The names the recorded code destructures — the forge's `F.ctx()`, member for member. */
  ctx(): Record<string, any> {
    return {
      pc: this.kind === "playcanvas" ? this.engine : undefined,
      THREE: this.kind === "three" ? this.engine : undefined,
      engine: this.kind,
      app: this.app, device: this.device, renderer: this.renderer,
      scene: this.scene, camera: this.camera, root: this.root,
      forge: this.forge(),
      log: (...a: unknown[]) => { try { this.log.push(a.map(String).join(" ").slice(0, 400)); } catch { /* unprintable */ } },
      clear: () => this.clear(),
      add: (thing: any) => this.add(thing),
    };
  }

  // What the code may reach for on `forge`. `dispose` is a no-op: the window owns the studio.
  private forge() {
    return {
      ensure: async () => ({ ok: true, engine: this.kind, reused: true }),
      clear: () => { this.clear(); return true; },
      dispose: () => true,
      view: (name: string, margin?: number) => { this.frame(name, margin); return this.shot(); },
      shot: () => this.shot(),
      stats: () => this.stats(),
      bounds: () => this.bounds(),
      ready: () => ({ built: true, engine: this.kind }),
      ctx: () => this.ctx(),
    };
  }

  add(thing: any): any {
    if (thing == null) return thing;
    const many = Array.isArray(thing) ? thing : [thing];
    for (const t of many) {
      if (t == null) continue;
      const flat = asFlat(t);
      if (flat) { this.flat = flat; continue; }
      try { this.addOne(t); } catch (err) { this.log.push("add failed: " + String(err).slice(0, 200)); }
    }
    return thing;
  }

  shot(): string {
    try { this.render(); return this.canvas.toDataURL("image/png"); }
    catch (e) { return "error:" + String(e).slice(0, 300); }
  }

  /** Sampled once from the empty studio: the only moment the backdrop is all there is. */
  sampleBackdrop() {
    try {
      this.render();
      const t = document.createElement("canvas");
      t.width = 1; t.height = 1;
      const x = t.getContext("2d")!;
      x.drawImage(this.canvas, 2, 2, 1, 1, 0, 0, 1, 1);
      const px = x.getImageData(0, 0, 1, 1).data;
      this.bgPixel = [px[0], px[1], px[2]];
    } catch { /* the border check falls back to the nominal colour */ }
  }

  // The forge's framing, pass for pass. A bounding sphere and a fixed margin is what crops
  // assets; projecting the eight box corners gives the real answer, and looking at the rendered
  // border afterwards catches what a bounding box under-reports.
  frame(name = "3q", margin = 1.35): Bounds | null {
    const b = this.bounds();
    this.framed = "no-subject";
    if (!b) return null;
    this.radius = b.radius;
    const d = VIEWS[name] || VIEWS["3q"];
    const len = Math.hypot(d[0], d[1], d[2]) || 1;
    const fov = FOV * Math.PI / 180;
    let dist = (b.radius / Math.tan(fov / 2)) * margin;
    const place = () => {
      this.setCamera([b.c[0] + d[0] / len * dist, b.c[1] + d[1] / len * dist, b.c[2] + d[2] / len * dist], b.c);
      this.setClip(Math.max(0.001, dist - b.radius * 4), dist + b.radius * 8);
      this.placeGround(b);
    };
    const SAFE = 0.88;
    place();
    for (let pass = 0; pass < 5; pass++) {
      this.render();
      const w = this.worstNdc(b);
      if (w <= SAFE) break;
      dist *= Math.min(4, w / SAFE) * 1.03;
      place();
    }
    this.framed = "fit";
    for (let back = 0; back < 3; back++) {
      this.render();
      if (!this.touchesBorder()) break;
      dist *= 1.22;
      place();
      this.framed = "widened";
    }
    this.render();
    if (this.touchesBorder()) this.framed = "still-clipped";
    return b;
  }

  private worstNdc(b: Bounds): number {
    let worst = 0;
    for (let i = 0; i < 8; i++) {
      const n = this.ndc([
        b.c[0] + (i & 1 ? 0.5 : -0.5) * b.size[0],
        b.c[1] + (i & 2 ? 0.5 : -0.5) * b.size[1],
        b.c[2] + (i & 4 ? 0.5 : -0.5) * b.size[2],
      ]);
      if (n.behind) return 99;
      worst = Math.max(worst, Math.abs(n.x), Math.abs(n.y));
    }
    return worst;
  }

  private touchesBorder(): boolean {
    try {
      if (this.opts.ground || this.opts.sky) return false;
      const n = 96;
      const t = document.createElement("canvas");
      t.width = n; t.height = n;
      const x = t.getContext("2d")!;
      x.drawImage(this.canvas, 0, 0, n, n);
      const d = x.getImageData(0, 0, n, n).data;
      // The MEASURED backdrop: three encodes its output to sRGB, so the pixel it draws for the
      // nominal colour is not the nominal colour, and comparing against the hex called every
      // border pixel a subject.
      const bg = this.bgPixel || (() => { const c = hexToRgb(this.opts.background); return [c.r * 255, c.g * 255, c.b * 255]; })();
      const hit = (px: number, py: number) => {
        const i = (py * n + px) * 4;
        return Math.abs(d[i] - bg[0]) + Math.abs(d[i + 1] - bg[1]) + Math.abs(d[i + 2] - bg[2]) > 26;
      };
      for (let k = 0; k < n; k++) {
        if (hit(k, 0) || hit(k, n - 1) || hit(0, k) || hit(n - 1, k)) return true;
      }
    } catch { /* a tainted or lost canvas: assume it fits */ }
    return false;
  }

  dispose() {
    try { this.teardown(); } catch { /* a lost context throws on the way out; it is gone either way */ }
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
  }
}

// --------------------------------------------------------------- three.js
function disposeThree(o: any) {
  const kill = (n: any) => {
    try { n.geometry?.dispose?.(); } catch { /* already gone */ }
    const ms = Array.isArray(n.material) ? n.material : n.material ? [n.material] : [];
    for (const m of ms) {
      try {
        for (const k of Object.keys(m)) { const v = m[k]; if (v && v.isTexture) v.dispose?.(); }
        m.dispose?.();
      } catch { /* already gone */ }
    }
  };
  if (o.traverse) o.traverse(kill); else kill(o);
}

class ThreeStudio extends Studio {
  readonly kind = "three" as const;
  private T: any;

  constructor(T: any, canvas: HTMLCanvasElement, opts: StudioOpts, w: number, h: number) {
    super(canvas, opts);
    this.T = T;
    this.engine = T;
    const bg = hexToRgb(opts.background);
    const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(w, h, false);
    if (renderer.outputColorSpace !== undefined && T.SRGBColorSpace) renderer.outputColorSpace = T.SRGBColorSpace;
    const scene = new T.Scene();
    scene.background = new T.Color(bg.r, bg.g, bg.b);
    const camera = new T.PerspectiveCamera(FOV, w / h, 0.01, 10000);
    const key = new T.DirectionalLight(0xffffff, 2.6); key.position.set(3, 5, 4);
    const fill = new T.DirectionalLight(0xbcd0ff, 0.9); fill.position.set(-4, 1.5, 3);
    const rim = new T.DirectionalLight(0xffe6c0, 1.6); rim.position.set(-2, 3, -5);
    const amb = new T.HemisphereLight(0xa8c4ff, 0x40332a, 0.7);
    scene.add(key); scene.add(fill); scene.add(rim); scene.add(amb);
    try {
      const pm = new T.PMREMGenerator(renderer);
      const eq = new T.CanvasTexture(envCanvas());
      eq.mapping = T.EquirectangularReflectionMapping;
      scene.environment = pm.fromEquirectangular(eq).texture;
      eq.dispose();
      pm.dispose();
    } catch { /* an old three without PMREM still gets the three-point rig */ }
    const root = new T.Group();
    root.name = "forge-subject";
    scene.add(root);
    let ground: any = null;
    if (opts.ground) {
      ground = new T.Mesh(new T.PlaneGeometry(1000, 1000), new T.MeshStandardMaterial({ color: 0x2a2f38, roughness: 1 }));
      ground.rotation.x = -Math.PI / 2;
      scene.add(ground);
    }
    this.renderer = renderer; this.scene = scene; this.camera = camera; this.root = root; this.ground = ground;
  }

  clear() {
    this.log = [];
    this.flat = null;
    for (const k of this.root.children.slice()) { this.root.remove(k); disposeThree(k); }
  }

  bounds(): Bounds | null {
    const T = this.T;
    const box = new T.Box3().setFromObject(this.root);
    if (box.isEmpty()) return null;
    const c = box.getCenter(new T.Vector3()), s = box.getSize(new T.Vector3());
    return { c: [c.x, c.y, c.z], size: [s.x, s.y, s.z], radius: Math.max(0.0001, s.length() / 2) };
  }

  render() { this.renderer.render(this.scene, this.camera); }

  resize(w: number, h: number) {
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  setCamera(pos: V3, target: V3) {
    this.camera.position.set(pos[0], pos[1], pos[2]);
    this.camera.lookAt(target[0], target[1], target[2]);
    this.camera.updateMatrixWorld(true);
  }

  setClip(near: number, far: number) {
    this.camera.near = near;
    this.camera.far = far;
    this.camera.updateProjectionMatrix();
  }

  cameraPosition(): V3 { const p = this.camera.position; return [p.x, p.y, p.z]; }

  protected ndc(p: V3) {
    const v = new this.T.Vector3(p[0], p[1], p[2]).project(this.camera);
    return { x: v.x, y: v.y, behind: v.z > 1 };
  }

  protected placeGround(b: Bounds) { if (this.ground) this.ground.position.y = b.c[1] - b.size[1] / 2; }

  protected addOne(t: any) {
    const T = this.T;
    // A bare geometry or material is a common thing to hand over; wrap it so it shows.
    if (t.isBufferGeometry) t = new T.Mesh(t, new T.MeshStandardMaterial({ color: 0xb9c3d0, roughness: 0.55, metalness: 0.05 }));
    else if (t.isMaterial) t = new T.Mesh(new T.SphereGeometry(1, 48, 32), t);
    this.root.add(t);
  }

  stats(): LiveStats {
    const out: LiveStats = { engine: this.kind, flat: !!this.flat, framed: this.framed };
    try {
      const b = this.bounds();
      if (b) { out.bbox_size = b.size.map(r3); out.bbox_center = b.c.map(r3); out.radius = r3(b.radius); }
      if (this.flat) { out.image = [this.flat.width || 0, this.flat.height || 0]; return out; }
      let tris = 0, meshes = 0, objs = 0;
      const mats: any[] = [];
      this.root.traverse((o: any) => {
        objs++;
        if (!o.isMesh && !o.isPoints && !o.isLine) return;
        meshes++;
        const g = o.geometry;
        if (g && g.index) tris += g.index.count / 3;
        else if (g && g.attributes && g.attributes.position) tris += g.attributes.position.count / 3;
        for (const m of Array.isArray(o.material) ? o.material : [o.material]) if (m && !mats.includes(m)) mats.push(m);
      });
      const info = this.renderer.info;
      if (info) { out.draw_calls = info.render.calls; out.gpu_triangles = info.render.triangles; out.textures = info.memory.textures; }
      out.objects = objs - 1;
      out.meshes = meshes;
      out.triangles = Math.round(tris);
      out.materials = mats.length;
      out.material_names = mats.slice(0, 6).map((m) => m.name || "(unnamed)");
    } catch (e) { out.error = String(e).slice(0, 200); }
    if (this.log.length) out.log = this.log.slice(0, 30);
    return out;
  }

  protected teardown() {
    this.clear();
    try { this.scene.environment?.dispose?.(); } catch { /* gone */ }
    this.renderer.dispose();
    // dispose() alone leaves the context alive until the canvas is collected, which is never
    // soon enough: a browser allows about sixteen, and a viewer that leaks one per click is
    // blank within a minute. Forcing the loss gives it back now.
    try { this.renderer.forceContextLoss?.(); } catch { /* already lost */ }
  }
}

// ------------------------------------------------------------- PlayCanvas
class PcStudio extends Studio {
  readonly kind = "playcanvas" as const;
  private pc: any;

  constructor(pc: any, canvas: HTMLCanvasElement, opts: StudioOpts, w: number, h: number) {
    super(canvas, opts);
    this.pc = pc;
    this.engine = pc;
    const bg = hexToRgb(opts.background);
    const app = new pc.Application(canvas, { graphicsDeviceOptions: { antialias: true, alpha: false } });
    app.graphicsDevice.maxPixelRatio = Math.min(2, window.devicePixelRatio || 1);
    app.setCanvasFillMode(pc.FILLMODE_NONE);
    app.setCanvasResolution(pc.RESOLUTION_FIXED, w, h);
    app.scene.ambientLight = new pc.Color(0.22, 0.25, 0.30);
    const cam = new pc.Entity("forge-camera");
    cam.addComponent("camera", { clearColor: new pc.Color(bg.r, bg.g, bg.b), fov: FOV, nearClip: 0.01, farClip: 10000 });
    app.root.addChild(cam);
    const mkLight = (name: string, colour: any, intensity: number, eul: V3) => {
      const e = new pc.Entity(name);
      e.addComponent("light", { type: "directional", color: colour, intensity, castShadows: false });
      e.setEulerAngles(eul[0], eul[1], eul[2]);
      app.root.addChild(e);
    };
    mkLight("key", new pc.Color(1, 0.98, 0.94), 1.9, [-42, 38, 0]);
    mkLight("fill", new pc.Color(0.74, 0.82, 1), 0.7, [-14, -60, 0]);
    mkLight("rim", new pc.Color(1, 0.9, 0.75), 1.4, [-18, 190, 0]);
    try {
      const tex = new pc.Texture(app.graphicsDevice, {
        name: "forge-env", width: 256, height: 128, format: pc.PIXELFORMAT_RGBA8,
        projection: pc.TEXTUREPROJECTION_EQUIRECT, mipmaps: false,
        addressU: pc.ADDRESS_REPEAT, addressV: pc.ADDRESS_CLAMP_TO_EDGE,
      });
      tex.setSource(envCanvas());
      app.scene.envAtlas = pc.EnvLighting.generateAtlas(pc.EnvLighting.generateLightingSource(tex));
      app.scene.skyboxIntensity = 1;
      // envAtlas both lights the scene and draws as the sky. Dropping the skybox layer from this
      // camera keeps the lighting and loses the drawn sky; the forge measured every other way.
      if (!opts.sky && pc.LAYERID_SKYBOX !== undefined) {
        const ll = (app.scene.layers && app.scene.layers.layerList) || [];
        const ids = ll.filter((l: any) => l.id !== pc.LAYERID_SKYBOX).map((l: any) => l.id);
        if (ids.length) cam.camera.layers = ids;
      }
    } catch { /* an engine build without EnvLighting still gets the three-point rig */ }
    const root = new pc.Entity("forge-subject");
    app.root.addChild(root);
    let ground: any = null;
    if (opts.ground) {
      ground = new pc.Entity("forge-ground");
      const gm = new pc.StandardMaterial();
      gm.diffuse = new pc.Color(0.16, 0.18, 0.22);
      gm.update();
      ground.addComponent("render", { type: "plane", material: gm });
      ground.setLocalScale(1000, 1, 1000);
      app.root.addChild(ground);
    }
    this.app = app; this.device = app.graphicsDevice; this.camera = cam; this.root = root; this.ground = ground;
    this.scene = app.scene;
  }

  clear() {
    this.log = [];
    this.flat = null;
    for (const k of this.root.children.slice()) k.destroy();
  }

  bounds(): Bounds | null {
    this.app.root.syncHierarchy();
    const mins = [1e9, 1e9, 1e9], maxs = [-1e9, -1e9, -1e9];
    let any = false;
    const walk = (e: any) => {
      const mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
      for (const m of mi) {
        const a = m.aabb;
        if (!a) continue;
        const lo = [a.center.x - a.halfExtents.x, a.center.y - a.halfExtents.y, a.center.z - a.halfExtents.z];
        const hi = [a.center.x + a.halfExtents.x, a.center.y + a.halfExtents.y, a.center.z + a.halfExtents.z];
        for (let k = 0; k < 3; k++) { if (lo[k] < mins[k]) mins[k] = lo[k]; if (hi[k] > maxs[k]) maxs[k] = hi[k]; }
        any = true;
      }
      for (const kid of e.children || []) walk(kid);
    };
    walk(this.root);
    if (!any) return null;
    const c: V3 = [(mins[0] + maxs[0]) / 2, (mins[1] + maxs[1]) / 2, (mins[2] + maxs[2]) / 2];
    const size: V3 = [maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]];
    return { c, size, radius: Math.max(0.0001, Math.hypot(size[0], size[1], size[2]) / 2) };
  }

  // Stepped by hand, as the forge does, never app.start(): the window owns the frame loop, so an
  // unmounted viewport costs nothing. frameStart/frameEnd are what tick() would have wrapped the
  // render in; without frameStart the backbuffer keeps its old size after a resize.
  render(dt = 1 / 60) {
    const app = this.app;
    app.update(dt);
    if (app.frameStart) app.frameStart();
    app.render();
    if (app.frameEnd) app.frameEnd();
  }

  resize(w: number, h: number) {
    this.device.maxPixelRatio = Math.min(2, window.devicePixelRatio || 1);
    this.device.resizeCanvas(w, h);
  }

  setCamera(pos: V3, target: V3) {
    this.camera.setPosition(pos[0], pos[1], pos[2]);
    this.camera.lookAt(target[0], target[1], target[2]);
  }

  setClip(near: number, far: number) {
    this.camera.camera.nearClip = near;
    this.camera.camera.farClip = far;
  }

  cameraPosition(): V3 { const p = this.camera.getPosition(); return [p.x, p.y, p.z]; }

  // Through the camera's own matrices rather than worldToScreen, which measures in the client
  // rect PlayCanvas last sampled — stale across a resize, and the framing runs before any tick.
  protected ndc(p: V3) {
    const pc = this.pc, cc = this.camera.camera;
    const m = new pc.Mat4().mul2(cc.projectionMatrix, cc.viewMatrix);
    const v = m.transformVec4(new pc.Vec4(p[0], p[1], p[2], 1));
    if (v.w <= 1e-9) return { x: 99, y: 99, behind: true };
    return { x: v.x / v.w, y: v.y / v.w, behind: false };
  }

  protected placeGround(b: Bounds) { if (this.ground) this.ground.setPosition(b.c[0], b.c[1] - b.size[1] / 2, b.c[2]); }

  protected addOne(t: any) {
    const pc = this.pc;
    if (t instanceof pc.Mesh) {
      const mi = new pc.MeshInstance(t, new pc.StandardMaterial());
      const e = new pc.Entity("mesh");
      e.addComponent("render", { meshInstances: [mi] });
      t = e;
    } else if (t instanceof pc.StandardMaterial) {
      const e = new pc.Entity("material");
      e.addComponent("render", { type: "sphere", material: t });
      t = e;
    }
    this.root.addChild(t);
  }

  stats(): LiveStats {
    const out: LiveStats = { engine: this.kind, flat: !!this.flat, framed: this.framed };
    try {
      const b = this.bounds();
      if (b) { out.bbox_size = b.size.map(r3); out.bbox_center = b.c.map(r3); out.radius = r3(b.radius); }
      if (this.flat) { out.image = [this.flat.width || 0, this.flat.height || 0]; return out; }
      let tris = 0, meshes = 0, objs = 0;
      const mats: any[] = [];
      const walk = (e: any) => {
        objs++;
        const mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
        for (const m of mi) {
          meshes++;
          const pr = m.mesh && m.mesh.primitive && m.mesh.primitive[0];
          if (pr && pr.count) tris += pr.count / 3;
          if (m.material && !mats.includes(m.material)) mats.push(m.material);
        }
        for (const kid of e.children || []) walk(kid);
      };
      walk(this.root);
      out.objects = objs - 1;
      out.meshes = meshes;
      out.triangles = Math.round(tris);
      out.materials = mats.length;
      out.material_names = mats.slice(0, 6).map((m) => m.name || "(unnamed)");
    } catch (e) { out.error = String(e).slice(0, 200); }
    if (this.log.length) out.log = this.log.slice(0, 30);
    return out;
  }

  protected teardown() { this.app.destroy(); }
}

// ------------------------------------------------------------------ public
export function buildStudio(eng: LoadedEngine, host: HTMLElement, opts: StudioOpts): Studio {
  const w = Math.max(64, host.clientWidth || 640), h = Math.max(64, host.clientHeight || 480);
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none;outline:none;";
  host.appendChild(canvas);
  const s = eng.kind === "three" ? new ThreeStudio(eng.mod, canvas, opts, w, h) : new PcStudio(eng.mod, canvas, opts, w, h);
  s.sampleBackdrop();
  return s;
}

// In the game page `import('/src/weapons.js')` meant the game's dev server. Inside a blob module
// it means the Studio's own origin, which has no such file, so when the project's dev server is
// known the root- and dot-relative specifiers are pointed back at it.
function retargetImports(code: string, devUrl: string): string {
  const base = devUrl.replace(/\/+$/, "");
  return code.replace(/import\(\s*(['"`])(?:\.\/|\/(?!\/))/g, (_m, q: string) => `import(${q}${base}/`);
}

// The code becomes the body of an async IIFE after the context is destructured — the exact
// shape live.py builds for the forge, so `return`, `await` and `import()` inside it behave as
// they did in the game page. A Blob module rather than `new Function`: a module keeps line
// numbers in its errors, survives a content-security policy that bans string evaluation, and
// gives `import()` inside the code a real module to resolve from.
export async function runGeneration(studio: Studio, code: string, devUrl = ""): Promise<RunResult> {
  const body = devUrl ? retargetImports(code, devUrl) : code;
  const src = "export default async function __run(__c) {\n"
    + "const {pc,THREE,engine,app,device,renderer,scene,camera,root,forge,add,clear,log} = __c;\n"
    + "return await (async () => {\n" + body + "\n})();\n}\n";
  const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
  const t0 = performance.now();
  try {
    const m = await import(/* @vite-ignore */ url);
    const value = await m.default(studio.ctx());
    return { value, error: "", ms: performance.now() - t0 };
  } catch (e: any) {
    return { value: undefined, error: String(e?.stack || e).slice(0, 2000), ms: performance.now() - t0 };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** A flat subject: a checkerboard, then the image at its own aspect, so its alpha can be seen. */
export function drawFlat(img: any, canvas: HTMLCanvasElement) {
  const x = canvas.getContext("2d");
  if (!x) return;
  const W = canvas.width, H = canvas.height, sq = 16;
  for (let yy = 0; yy < H; yy += sq) {
    for (let xx = 0; xx < W; xx += sq) {
      x.fillStyle = ((xx / sq + yy / sq) % 2) ? "#2b3038" : "#232830";
      x.fillRect(xx, yy, sq, sq);
    }
  }
  const iw = img.width || img.videoWidth || 1, ih = img.height || img.videoHeight || 1;
  const s = Math.min(W / iw, H / ih) * 0.92;
  const dw = iw * s, dh = ih * s;
  try {
    if (typeof ImageData !== "undefined" && img instanceof ImageData) {
      const tmp = document.createElement("canvas");
      tmp.width = iw; tmp.height = ih;
      tmp.getContext("2d")!.putImageData(img, 0, 0);
      img = tmp;
    }
    x.imageSmoothingEnabled = s < 2;      /* pixel art must stay crisp when magnified */
    x.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
  } catch (e) {
    x.fillStyle = "#ff5555";
    x.fillText("could not draw: " + String(e).slice(0, 60), 8, 20);
  }
}
