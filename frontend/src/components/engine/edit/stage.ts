// THE 2D STAGE: a game's own 2D objects, read and changed by key. Phaser, PixiJS and HTML pages.
//
// The 3D runtime walks a three.js or PlayCanvas tree. A 2D game has a different tree in each engine,
// and most of the 2D games on this machine have no engine at all: they are React pages. This file
// gives all three one shape, so the Edit tab, the agents' scene tools and a shipped game read and
// write one document, the same `studio.edits.json` a 3D game uses:
//
//   parts[key] = { pos: [x, y, 0], rot: [0, 0, radians], scale: [sx, sy, 1], alpha, hidden }
//   placed[]   = a PlacedItem whose ref is { kind: "image", url }
//
// The rules of the runtime hold here too. No engine is imported: every class comes from an object
// the game already has, by duck type. A key is a name when the name is used once, `name.000` when it
// is repeated, and an index path when there is no name at all.
//
// WHAT `pos` MEANS. Phaser and PixiJS: the object's own x and y, relative to its parent, as the code
// sets them. HTML: an offset from where the page's layout put the element, because the layout owns
// the position and the page can lay it out again at any moment.
//
// HTML EDITS ARE ONE STYLE SHEET, NOT INLINE STYLES. React writes `style` on each render and replaces
// elements when a screen changes. A rule in a sheet, found by selector, outlives both. The rule uses
// the separate `translate`, `rotate` and `scale` properties, so it adds to a `transform` the game
// animates instead of fighting it.

export type StageEngine = "phaser" | "pixi" | "dom";
export type Box = [number, number, number, number];

export interface StageVals {
  pos?: number[];
  rot?: number[];
  scale?: number[];
  alpha?: number;
  hidden?: boolean;
}

export interface StageRow {
  key: string;
  name: string;
  type: string;
  parent: string;
  /** Where it is on the page: x, y, width, height in CSS pixels of the viewport. */
  box: Box;
  /** It has no measurable size (a Phaser Graphics): the box is a marker around its position. */
  point?: boolean;
  pos: [number, number];
  rot: number;
  scale: [number, number];
  alpha: number;
  visible: boolean;
  scene?: string;
  texture?: string;
  text?: string;
  placed?: string;
}

interface Found { obj: any; key: string; name: string; type: string; parent: string; scene?: string; placed?: string }

interface Adapter {
  engine: StageEngine;
  view(): [number, number];
  walk(): Found[];
  row(f: Found): StageRow | null;
  write(obj: any, v: StageVals, key: string): void;
  /** A move of dx, dy page pixels as a move in the object's own coordinates. */
  delta(obj: any, dx: number, dy: number): [number, number];
  /** The object's own coordinates under a page point: where a new placement goes. */
  at(x: number, y: number): [number, number];
  alive(obj: any): boolean;
  place(item: any, url: string): Promise<any>;
  remove(obj: any): void;
  paused(): boolean;
  pause(on: boolean): void;
  redraw(): void;
  read(obj: any, key: string): StageVals;
}

const r3 = (n: any): number => (typeof n === "number" && isFinite(n) ? Math.round(n * 1000) / 1000 : 0);
const num = (n: any, or: number): number => (typeof n === "number" && isFinite(n) ? n : or);
const MAX_ROWS = 5000;
const BOX_POINT = 16;

const isPhaser = (o: any): boolean => { try { return !!(o && o.scene && o.scene.scenes && o.canvas && o.renderer); } catch { return false; } };
const isPixi = (o: any): boolean => { try { return !!(o && o.stage && o.stage.children && o.renderer && (o.ticker || o._ticker)); } catch { return false; } };
const isDom = (o: any): boolean => !!(o && o.nodeType === 9);

export function stageEngineOf(target: any): StageEngine | "" {
  if (!target || typeof target !== "object") return "";
  if (isPhaser(target)) return "phaser";
  if (isPixi(target)) return "pixi";
  if (isDom(target)) return "dom";
  return "";
}

/** `stableKeys` for one scope: a name used once is the key, a repeated name reads name.000. */
function keyed(rows: Found[], scope: string, nameless: (i: number) => string): void {
  const counts = new Map<string, number>();
  for (const f of rows) if (f.name && !f.placed) counts.set(f.name, (counts.get(f.name) || 0) + 1);
  const seen = new Map<string, number>();
  rows.forEach((f, i) => {
    if (f.placed) { f.key = "placed/" + f.placed; return; }
    if (!f.name) { f.key = scope + nameless(i); return; }
    if ((counts.get(f.name) || 0) < 2) { f.key = scope + f.name; return; }
    const n = seen.get(f.name) || 0;
    seen.set(f.name, n + 1);
    f.key = scope + f.name + "." + String(n).padStart(3, "0");
  });
}

const loadImage = (url: string): Promise<any> => new Promise((done, fail) => {
  const im = new Image();
  im.crossOrigin = "anonymous";
  im.onload = () => done(im);
  im.onerror = () => fail(new Error("the image could not be loaded: " + url));
  im.src = url;
});

const baseName = (s: string): string => s.split(/[\\/]/).pop()!.split("?")[0].replace(/\.[a-z0-9]+$/i, "");

// ------------------------------------------------------------------ Phaser

function phaserAdapter(game: any): Adapter {
  const active = (): any[] => (game.scene.scenes || []).filter((s: any) => {
    // 5 is running and 6 is paused: a paused scene is still on the screen, and `active` is false then.
    try { return s.sys ? s.sys.settings.visible !== false && (s.sys.settings.status === 5 || s.sys.settings.status === 6) : true; }
    catch { return false; }
  });
  const canvasScale = () => {
    const r = game.canvas.getBoundingClientRect();
    const w = num(game.scale?.width, 0) || num(game.config?.width, 0) || r.width || 1;
    const h = num(game.scale?.height, 0) || num(game.config?.height, 0) || r.height || 1;
    return { r, kx: r.width ? r.width / w : 1, ky: r.height ? r.height / h : 1 };
  };
  const sceneOf = (o: any): any => o.scene || active()[0];
  const camOf = (o: any): any => sceneOf(o)?.cameras?.main;
  const toPage = (cam: any, x: number, y: number, sfx: number, sfy: number): [number, number] => {
    const { r, kx, ky } = canvasScale();
    if (!cam) return [r.left + x * kx, r.top + y * ky];
    const ox = cam.width / 2, oy = cam.height / 2;
    const sx = cam.x + ox + (x - cam.scrollX * sfx - ox) * cam.zoom;
    const sy = cam.y + oy + (y - cam.scrollY * sfy - oy) * cam.zoom;
    return [r.left + sx * kx, r.top + sy * ky];
  };
  const kidsOf = (o: any): any[] => {
    if (Array.isArray(o.list)) return o.list;
    return o.type === "Layer" && typeof o.getChildren === "function" ? o.getChildren() : [];
  };
  const nameOf = (o: any): string => {
    if (o.name) return String(o.name);
    const t = o.texture?.key;
    return t && t !== "__DEFAULT" && t !== "__MISSING" && t !== "__NORMAL" ? String(t) : "";
  };
  return {
    engine: "phaser",
    view: () => [window.innerWidth, window.innerHeight],
    walk() {
      const out: Found[] = [];
      for (const s of active()) {
        const scope = String(s.scene?.key || "scene") + "/";
        const rows: Found[] = [];
        const paths = new Map<Found, string>();
        const go = (list: any[], parent: Found | null, path: string) => {
          list.forEach((o, i) => {
            if (rows.length >= MAX_ROWS || !o) return;
            const f: Found = { obj: o, key: "", name: nameOf(o), type: String(o.type || "Object"), parent: "", scene: String(s.scene?.key || "") };
            const placed = o.__studioPlaced;
            if (placed) f.placed = String(placed);
            const p = path ? path + "/" + i : String(i);
            paths.set(f, p);
            rows.push(f);
            (f as any).up = parent;
            const kids = kidsOf(o);
            if (kids.length) go(kids, f, p);
          });
        };
        go(s.children?.list || [], null, "");
        keyed(rows, scope, (i) => paths.get(rows[i]) || String(i));
        for (const f of rows) { f.parent = (f as any).up?.key || ""; delete (f as any).up; }
        out.push(...rows);
      }
      return out;
    },
    row(f) {
      const o = f.obj;
      const cam = camOf(o);
      const sfx = num(o.scrollFactorX, 1), sfy = num(o.scrollFactorY, 1);
      let box: Box;
      let point = false;
      let b: any = null;
      try { b = typeof o.getBounds === "function" ? o.getBounds() : null; } catch { b = null; }
      if (b && b.width > 0 && b.height > 0) {
        const [x1, y1] = toPage(cam, b.x, b.y, sfx, sfy);
        const [x2, y2] = toPage(cam, b.x + b.width, b.y + b.height, sfx, sfy);
        box = [r3(x1), r3(y1), r3(x2 - x1), r3(y2 - y1)];
      } else {
        const [x, y] = toPage(cam, num(o.x, 0), num(o.y, 0), sfx, sfy);
        box = [r3(x - BOX_POINT / 2), r3(y - BOX_POINT / 2), BOX_POINT, BOX_POINT];
        point = true;
      }
      const row: StageRow = {
        key: f.key, name: f.name || f.type, type: f.type, parent: f.parent, box,
        pos: [r3(o.x), r3(o.y)], rot: r3(o.rotation), scale: [r3(num(o.scaleX, 1)), r3(num(o.scaleY, 1))],
        alpha: r3(num(o.alpha, 1)), visible: o.visible !== false, scene: f.scene,
      };
      if (point) row.point = true;
      if (o.texture?.key) row.texture = String(o.texture.key);
      if (typeof o.text === "string") row.text = o.text.slice(0, 60);
      if (f.placed) row.placed = f.placed;
      return row;
    },
    read: (o) => ({ pos: [num(o.x, 0), num(o.y, 0)], rot: [0, 0, num(o.rotation, 0)], scale: [num(o.scaleX, 1), num(o.scaleY, 1)], alpha: num(o.alpha, 1), hidden: o.visible === false }),
    write(o, v) {
      if (v.pos) {
        const x = num(v.pos[0], o.x), y = num(v.pos[1], o.y);
        if (typeof o.setPosition === "function") o.setPosition(x, y);
        else { o.x = x; o.y = y; }
        // The physics body keeps its own position and writes it back onto the sprite each step.
        try { if (o.body && typeof o.body.reset === "function") o.body.reset(x, y); } catch { /* not this physics */ }
      }
      if (v.rot) o.rotation = num(v.rot[2], o.rotation);
      if (v.scale) {
        if (typeof o.setScale === "function") o.setScale(num(v.scale[0], o.scaleX), num(v.scale[1], o.scaleY));
        else { o.scaleX = num(v.scale[0], o.scaleX); o.scaleY = num(v.scale[1], o.scaleY); }
      }
      if (typeof v.alpha === "number") { if (typeof o.setAlpha === "function") o.setAlpha(v.alpha); else o.alpha = v.alpha; }
      if (typeof v.hidden === "boolean") { if (typeof o.setVisible === "function") o.setVisible(!v.hidden); else o.visible = !v.hidden; }
    },
    delta(o, dx, dy) {
      const { kx, ky } = canvasScale();
      const zoom = camOf(o)?.zoom || 1;
      const wx = dx / kx / zoom, wy = dy / ky / zoom;
      const pc = o.parentContainer;
      if (pc && typeof pc.getWorldTransformMatrix === "function") {
        const m = pc.getWorldTransformMatrix();
        const det = m.a * m.d - m.b * m.c;
        if (det) return [(m.d * wx - m.c * wy) / det, (m.a * wy - m.b * wx) / det];
      }
      return [wx, wy];
    },
    at(x, y) {
      const { r, kx, ky } = canvasScale();
      const cam = active()[0]?.cameras?.main;
      const sx = (x - r.left) / kx, sy = (y - r.top) / ky;
      if (!cam) return [sx, sy];
      const ox = cam.width / 2, oy = cam.height / 2;
      return [(sx - cam.x - ox) / cam.zoom + ox + cam.scrollX, (sy - cam.y - oy) / cam.zoom + oy + cam.scrollY];
    },
    alive: (o) => !!(o && o.scene),
    async place(item, url) {
      const list = active();
      const s = list[list.length - 1];
      if (!s?.add?.image) throw new Error("this game has no running scene to place into");
      const key = "__studio_" + item.id;
      if (!game.textures.exists(key)) game.textures.addImage(key, await loadImage(url));
      const o = s.add.image(0, 0, key);
      o.name = String(item.name || "");
      o.setDepth?.(1000);
      return o;
    },
    remove: (o) => { try { o.destroy(); } catch { /* already gone */ } },
    paused: () => { const a = active(); return a.length > 0 && a.every((s: any) => s.sys?.isPaused?.()); },
    pause(on) {
      for (const s of on ? active() : (game.scene.scenes || [])) {
        try { if (on) s.scene.pause(); else if (s.sys?.isPaused?.()) s.scene.resume(); } catch { /* this scene is changing */ }
      }
    },
    redraw: () => { /* a paused Phaser scene still draws */ },
  };
}

// ------------------------------------------------------------------ PixiJS

function pixiAdapter(app: any): Adapter {
  const canvas = (): any => app.canvas || app.view || app.renderer?.canvas || app.renderer?.view;
  const canvasScale = () => {
    const c = canvas();
    const r = c.getBoundingClientRect();
    const scr = app.renderer?.screen || app.screen || { width: r.width, height: r.height };
    return { r, kx: scr.width ? r.width / scr.width : 1, ky: scr.height ? r.height / scr.height : 1 };
  };
  const ticker = (): any => app.ticker || app._ticker;
  const textureName = (o: any): string => {
    const t = o.texture;
    if (!t) return "";
    const n = t.label || t.textureCacheIds?.[0] || t.baseTexture?.resource?.url || t.source?.label || "";
    return n && n !== "WHITE" && n !== "EMPTY" ? String(n) : "";
  };
  const kindOf = (o: any): string => {
    if (typeof o.renderPipeId === "string" && o.renderPipeId) return o.renderPipeId;
    if (typeof o.text === "string") return "text";
    if (o.texture) return "sprite";
    if (o.geometry || typeof o.clear === "function") return "graphics";
    return "container";
  };
  const nameOf = (o: any): string => String(o.label || o.name || "") || baseName(textureName(o));
  return {
    engine: "pixi",
    view: () => [window.innerWidth, window.innerHeight],
    walk() {
      const rows: Found[] = [];
      const paths = new Map<Found, string>();
      const go = (list: any[], parent: Found | null, path: string) => {
        list.forEach((o, i) => {
          if (rows.length >= MAX_ROWS || !o) return;
          const f: Found = { obj: o, key: "", name: nameOf(o), type: kindOf(o), parent: "" };
          if (o.__studioPlaced) f.placed = String(o.__studioPlaced);
          const p = path ? path + "/" + i : String(i);
          paths.set(f, p);
          rows.push(f);
          (f as any).up = parent;
          if (o.children?.length) go(o.children, f, p);
        });
      };
      go(app.stage.children || [], null, "");
      keyed(rows, "", (i) => paths.get(rows[i]) || String(i));
      for (const f of rows) { f.parent = (f as any).up?.key || ""; delete (f as any).up; }
      return rows;
    },
    row(f) {
      const o = f.obj;
      const { r, kx, ky } = canvasScale();
      let box: Box;
      let point = false;
      let b: any = null;
      try { b = o.getBounds(); } catch { b = null; }
      if (b && b.width > 0 && b.height > 0) box = [r3(r.left + b.x * kx), r3(r.top + b.y * ky), r3(b.width * kx), r3(b.height * ky)];
      else {
        const g = o.parent?.toGlobal ? o.parent.toGlobal({ x: num(o.x, 0), y: num(o.y, 0) }) : { x: num(o.x, 0), y: num(o.y, 0) };
        box = [r3(r.left + g.x * kx - BOX_POINT / 2), r3(r.top + g.y * ky - BOX_POINT / 2), BOX_POINT, BOX_POINT];
        point = true;
      }
      const row: StageRow = {
        key: f.key, name: f.name || f.type, type: f.type, parent: f.parent, box,
        pos: [r3(o.x), r3(o.y)], rot: r3(o.rotation), scale: [r3(num(o.scale?.x, 1)), r3(num(o.scale?.y, 1))],
        alpha: r3(num(o.alpha, 1)), visible: o.visible !== false,
      };
      if (point) row.point = true;
      const tn = textureName(o);
      if (tn) row.texture = tn;
      if (typeof o.text === "string") row.text = o.text.slice(0, 60);
      if (f.placed) row.placed = f.placed;
      return row;
    },
    read: (o) => ({ pos: [num(o.x, 0), num(o.y, 0)], rot: [0, 0, num(o.rotation, 0)], scale: [num(o.scale?.x, 1), num(o.scale?.y, 1)], alpha: num(o.alpha, 1), hidden: o.visible === false }),
    write(o, v) {
      if (v.pos) { o.x = num(v.pos[0], o.x); o.y = num(v.pos[1], o.y); }
      if (v.rot) o.rotation = num(v.rot[2], o.rotation);
      if (v.scale && o.scale) o.scale.set(num(v.scale[0], o.scale.x), num(v.scale[1], o.scale.y));
      if (typeof v.alpha === "number") o.alpha = v.alpha;
      if (typeof v.hidden === "boolean") o.visible = !v.hidden;
    },
    delta(o, dx, dy) {
      const { kx, ky } = canvasScale();
      const wx = dx / kx, wy = dy / ky;
      const m = o.parent?.worldTransform;
      if (m) {
        const det = m.a * m.d - m.b * m.c;
        if (det) return [(m.d * wx - m.c * wy) / det, (m.a * wy - m.b * wx) / det];
      }
      return [wx, wy];
    },
    at(x, y) {
      const { r, kx, ky } = canvasScale();
      return [(x - r.left) / kx, (y - r.top) / ky];
    },
    alive: (o) => !!(o && !o.destroyed && o.parent),
    async place(item, url) {
      // No PIXI global in a bundled game: the classes come from a sprite the game made.
      let sample: any = null;
      const look = (l: any[]) => { for (const o of l || []) { if (sample) return; if (o.texture && typeof o.text !== "string" && kindOf(o) === "sprite") sample = o; else look(o.children); } };
      look(app.stage.children);
      const G: any = (window as any).PIXI;
      const SpriteC = sample?.constructor || G?.Sprite;
      const TextureC = sample?.texture?.constructor || G?.Texture;
      if (!SpriteC || typeof TextureC?.from !== "function") {
        throw new Error("this PixiJS game has no sprite to copy the classes from, and no PIXI global");
      }
      const o = new SpriteC(TextureC.from(await loadImage(url)));
      o.anchor?.set?.(0.5);
      o.label = o.name = String(item.name || "");
      app.stage.addChild(o);
      return o;
    },
    remove: (o) => { try { o.parent?.removeChild(o); o.destroy?.(); } catch { /* already gone */ } },
    paused: () => { const t = ticker(); return !!t && t.started === false; },
    pause(on) { const t = ticker(); if (t) { if (on) t.stop(); else t.start(); } },
    // A stopped ticker draws nothing, so a change made while paused has to ask for its frame.
    redraw: () => { try { const t = ticker(); if (t && t.started === false) app.render?.(); } catch { /* it draws next tick */ } },
  };
}

// ------------------------------------------------------------------ HTML

const DOM_STYLE = "__studio_stage_style";
const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "LINK", "META", "HEAD", "TITLE", "NOSCRIPT", "TEMPLATE", "BR"]);
const SHOWN_TAGS = new Set(["IMG", "SVG", "CANVAS", "VIDEO", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "A", "H1", "H2", "H3", "H4", "LABEL"]);

// THE STATE LIVES ON THE TARGET, NOT IN THIS MODULE. One page can hold two copies of this file: the
// game's own src/studio-runtime.js, and the Studio's, imported under a URL of its own. State kept in
// the module made two jobs that each placed the same image, and a copy that did not know which HTML
// rules the other had written.
function stateOf(target: any): { job?: Job; over?: Map<string, StageVals> } {
  if (!target.__studioStage) {
    Object.defineProperty(target, "__studioStage", { value: {}, enumerable: false, configurable: true });
  }
  return target.__studioStage;
}

function cssEscape(s: string): string {
  const C: any = (window as any).CSS;
  return C?.escape ? C.escape(s) : s.replace(/[^\w-]/g, "\\$&");
}

/** The element's address. An id or a test id when it is the only one with that value, else its
 *  place under the nearest ancestor that has one. */
export function domKey(el: any, doc: any): string {
  const parts: string[] = [];
  let e = el;
  while (e && e.nodeType === 1) {
    const tag = String(e.tagName).toLowerCase();
    if (tag === "body" || tag === "html") { parts.unshift(tag); break; }
    if (e.id && doc.querySelectorAll("#" + cssEscape(e.id)).length === 1) { parts.unshift("#" + cssEscape(e.id)); break; }
    const tid = e.getAttribute?.("data-testid");
    if (tid && doc.querySelectorAll('[data-testid="' + tid.replace(/"/g, '\\"') + '"]').length === 1) {
      parts.unshift('[data-testid="' + tid.replace(/"/g, '\\"') + '"]');
      break;
    }
    const sibs = e.parentElement ? Array.from(e.parentElement.children) : [e];
    parts.unshift(tag + ":nth-child(" + (sibs.indexOf(e) + 1) + ")");
    e = e.parentElement;
  }
  return parts.join(" > ");
}

/** The rule for one element, or "" when nothing is changed. */
export function domRule(key: string, v: StageVals): string {
  const d: string[] = [];
  const x = num(v.pos?.[0], 0), y = num(v.pos?.[1], 0);
  if (x || y) d.push("translate:" + x + "px " + y + "px !important");
  const rot = num(v.rot?.[2], 0);
  if (rot) d.push("rotate:" + rot + "rad !important");
  const sx = num(v.scale?.[0], 1), sy = num(v.scale?.[1], 1);
  if (sx !== 1 || sy !== 1) d.push("scale:" + sx + " " + sy + " !important");
  if (typeof v.alpha === "number" && v.alpha < 1) d.push("opacity:" + v.alpha + " !important");
  if (v.hidden) d.push("visibility:hidden !important");
  return d.length ? key + "{" + d.join(";") + "}" : "";
}

function domAdapter(doc: any): Adapter {
  const state = stateOf(doc);
  if (!state.over) state.over = new Map();
  const over = state.over;
  const flush = () => {
    let el = doc.getElementById(DOM_STYLE);
    if (!el) { el = doc.createElement("style"); el.id = DOM_STYLE; (doc.head || doc.documentElement).appendChild(el); }
    el.textContent = Array.from(over.entries()).map(([k, v]) => domRule(k, v)).filter(Boolean).join("\n");
  };
  const worthShowing = (el: any, style: any): boolean => {
    if (SHOWN_TAGS.has(String(el.tagName).toUpperCase())) return true;
    if (el.id || el.getAttribute?.("data-testid") || el.getAttribute?.("role") === "button") return true;
    if (style.backgroundImage && style.backgroundImage !== "none") return true;
    for (const n of Array.from(el.childNodes as any[])) if (n.nodeType === 3 && String(n.nodeValue || "").trim()) return true;
    return false;
  };
  // The PARENT's scale. `translate` applies outside the element's own `transform`, so a button drawn
  // at 1.1 of its size still moves one pixel per pixel unless something around it is scaled.
  const scaleOf = (el: any): [number, number] => {
    const up = el.parentElement;
    if (!up) return [1, 1];
    const r = up.getBoundingClientRect();
    // One number from the longer side: a rounded offsetHeight of 17 for 16 px is six percent wrong.
    const k = up.offsetWidth >= up.offsetHeight ? (up.offsetWidth && r.width ? r.width / up.offsetWidth : 1) : (r.height / up.offsetHeight);
    return [k, k];
  };
  const placedHost = (): any => doc.body || doc.documentElement;
  return {
    engine: "dom",
    view: () => [window.innerWidth, window.innerHeight],
    walk() {
      const rows: Found[] = [];
      const go = (el: any, parent: string) => {
        if (rows.length >= MAX_ROWS) return;
        for (const c of Array.from(el.children as any[])) {
          if (rows.length >= MAX_ROWS) return;
          if (SKIP_TAGS.has(String(c.tagName).toUpperCase()) || c.id === DOM_STYLE) continue;
          const key = domKey(c, doc);
          const saved = over.has(key);
          const style = window.getComputedStyle(c);
          const r = c.getBoundingClientRect();
          const shown = r.width >= 2 && r.height >= 2 && style.display !== "none"
            && (saved || (style.visibility !== "hidden" && Number(style.opacity) > 0))
            && r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight;
          const placed = c.getAttribute?.("data-studio-placed") || "";
          const listed = shown && (saved || !!placed || worthShowing(c, style));
          if (listed) {
            const text = Array.from(c.childNodes as any[]).filter((n: any) => n.nodeType === 3).map((n: any) => String(n.nodeValue || "").trim()).join(" ").trim();
            const name = c.getAttribute?.("aria-label") || c.getAttribute?.("alt") || text.slice(0, 40) || c.id || String(c.tagName).toLowerCase();
            rows.push({ obj: c, key: placed ? "placed/" + placed : key, name, type: String(c.tagName).toLowerCase(), parent, ...(placed ? { placed } : {}) });
          }
          if (String(c.tagName).toUpperCase() !== "SVG") go(c, listed ? (placed ? "placed/" + placed : key) : parent);
        }
      };
      if (doc.body) go(doc.body, "");
      return rows;
    },
    row(f) {
      const el = f.obj;
      const r = el.getBoundingClientRect();
      const v = f.placed ? placedVals(el) : over.get(f.key) || {};
      const text = el.tagName && typeof el.innerText === "string" ? el.innerText.trim().slice(0, 60) : "";
      const src = el.getAttribute?.("src") || "";
      const row: StageRow = {
        key: f.key, name: f.name, type: f.type, parent: f.parent, box: [r3(r.left), r3(r.top), r3(r.width), r3(r.height)],
        pos: [r3(num(v.pos?.[0], 0)), r3(num(v.pos?.[1], 0))], rot: r3(num(v.rot?.[2], 0)),
        scale: [r3(num(v.scale?.[0], 1)), r3(num(v.scale?.[1], 1))], alpha: r3(num(v.alpha, 1)), visible: !v.hidden,
      };
      if (text) row.text = text;
      if (src) row.texture = baseName(src);
      if (f.placed) row.placed = f.placed;
      return row;
    },
    read(el, key) {
      if (el?.getAttribute?.("data-studio-placed")) return placedVals(el);
      const v = over.get(key) || {};
      return { pos: [num(v.pos?.[0], 0), num(v.pos?.[1], 0)], rot: [0, 0, num(v.rot?.[2], 0)], scale: [num(v.scale?.[0], 1), num(v.scale?.[1], 1)], alpha: num(v.alpha, 1), hidden: !!v.hidden };
    },
    write(el, v, key) {
      if (el?.getAttribute?.("data-studio-placed")) { placedWrite(el, v); return; }
      const cur = { ...(over.get(key) || {}) };
      for (const f of ["pos", "rot", "scale", "alpha", "hidden"] as const) if (v[f] !== undefined) (cur as any)[f] = v[f];
      over.set(key, cur);
      flush();
    },
    delta(el, dx, dy) {
      const [kx, ky] = scaleOf(el);
      return [dx / (kx || 1), dy / (ky || 1)];
    },
    at: (x, y) => [x, y],
    alive: (el) => !!el?.isConnected,
    async place(item, url) {
      await loadImage(url);
      const el = doc.createElement("img");
      el.src = url;
      el.alt = String(item.name || "");
      el.draggable = false;
      el.style.cssText = "position:fixed;left:0;top:0;z-index:2147483000;pointer-events:none";
      placedHost().appendChild(el);
      return el;
    },
    remove: (el) => { try { el.remove(); } catch { /* already gone */ } },
    paused: () => false,
    pause: () => { /* a page has no game loop to stop */ },
    redraw: () => { /* the browser draws it */ },
  };
}

function placedVals(el: any): StageVals {
  try { return JSON.parse(el.getAttribute("data-studio-vals") || "{}"); } catch { return {}; }
}

function placedWrite(el: any, v: StageVals): void {
  const cur = { ...placedVals(el) };
  for (const f of ["pos", "rot", "scale", "alpha", "hidden"] as const) if (v[f] !== undefined) (cur as any)[f] = v[f];
  el.setAttribute("data-studio-vals", JSON.stringify(cur));
  el.style.translate = "calc(" + num(cur.pos?.[0], 0) + "px - 50%) calc(" + num(cur.pos?.[1], 0) + "px - 50%)";
  el.style.rotate = num(cur.rot?.[2], 0) + "rad";
  el.style.scale = num(cur.scale?.[0], 1) + " " + num(cur.scale?.[1], 1);
  el.style.opacity = String(num(cur.alpha, 1));
  el.style.visibility = cur.hidden ? "hidden" : "visible";
}

// ------------------------------------------------------------------ the adapter of a target

const adapters = new WeakMap<any, Adapter>();

function adapterOf(target: any): Adapter | null {
  const hit = adapters.get(target);
  if (hit) return hit;
  const e = stageEngineOf(target);
  const a = e === "phaser" ? phaserAdapter(target) : e === "pixi" ? pixiAdapter(target) : e === "dom" ? domAdapter(target) : null;
  if (a) adapters.set(target, a);
  return a;
}

function vector(v: any, n: number): boolean {
  return Array.isArray(v) && v.length >= n && v.slice(0, n).every((x) => typeof x === "number" && isFinite(x));
}

/** Only the fields this layer understands, and only numbers: the file can be edited by hand. */
export function cleanVals(raw: any): StageVals {
  const v: StageVals = {};
  if (!raw || typeof raw !== "object") return v;
  if (vector(raw.pos, 2)) v.pos = raw.pos;
  if (vector(raw.rot, 3)) v.rot = raw.rot;
  if (vector(raw.scale, 2)) v.scale = raw.scale;
  if (typeof raw.alpha === "number" && isFinite(raw.alpha)) v.alpha = Math.max(0, Math.min(1, raw.alpha));
  if (typeof raw.hidden === "boolean") v.hidden = raw.hidden;
  return v;
}

// ------------------------------------------------------------------ the saved document

export interface StageReport { parts: number; placed: number; pending: number; missing: string[]; errors: string[] }
export interface StageOpts {
  base?: string;
  url?: (path: string, kind: "model" | "code" | "image") => string | undefined | null;
  retryMs?: number;
  placed?: boolean;
  onSettle?: (report: StageReport) => void;
}

interface Job {
  parts: Record<string, StageVals>;
  items: Map<string, any>;
  objects: Map<string, any>;
  done: WeakSet<any>;
  loading: Set<string>;
  failed: Map<string, string>;
  opts: StageOpts;
  timer: any;
  rep: StageReport;
  until: number;
  told: boolean;
}

const WATCH_MS = 1000;

function urlOf(it: any, opts: StageOpts): string {
  const raw = String(it?.ref?.url || "");
  const via = opts.url?.(raw, "image");
  if (via) return via;
  try { return new URL(raw, opts.base || document.baseURI).href; } catch { return raw; }
}

function itemVals(it: any): StageVals {
  return cleanVals({ pos: it.pos, rot: it.rot, scale: it.scale, alpha: it.alpha, hidden: it.hidden });
}

function pass(a: Adapter, j: Job): void {
  const found = new Map<string, any>();
  for (const f of a.walk()) if (!f.placed) found.set(f.key, f.obj);
  const missing: string[] = [];
  let parts = 0;
  for (const [key, vals] of Object.entries(j.parts)) {
    const o = found.get(key);
    if (!o) {
      // An HTML rule does not wait for its element: the sheet applies it on the screen it belongs to.
      if (a.engine === "dom" && !j.done.has(vals)) { a.write(null, vals, key); j.done.add(vals); }
      missing.push(key);
      continue;
    }
    parts++;
    // Once per OBJECT. The game may move it afterwards, and a Phaser scene that restarts makes new
    // objects, which is why this looks again instead of finishing.
    if (j.done.has(o)) continue;
    a.write(o, vals, key);
    j.done.add(o);
  }
  j.rep.parts = parts;
  j.rep.missing = missing;

  for (const [id, it] of j.items) {
    const o = j.objects.get(id);
    if (o && a.alive(o)) continue;
    if (j.loading.has(id) || j.failed.has(id)) continue;
    j.loading.add(id);
    a.place(it, urlOf(it, j.opts)).then((obj) => {
      if (!j.items.has(id)) { a.remove(obj); return; }
      obj.__studioPlaced = id;
      if (a.engine === "dom") obj.setAttribute("data-studio-placed", id);
      a.write(obj, itemVals(j.items.get(id)), "placed/" + id);
      j.objects.set(id, obj);
      a.redraw();
    }).catch((e) => { j.failed.set(id, String(e?.message || e)); }).then(() => { j.loading.delete(id); });
  }
  j.rep.placed = Array.from(j.objects.values()).filter((o) => a.alive(o)).length;
  j.rep.pending = j.loading.size;
  j.rep.errors = Array.from(j.failed.values());
  if (!j.told && !j.loading.size && (!missing.length || Date.now() > j.until)) {
    j.told = true;
    try { j.opts.onSettle?.(j.rep); } catch { /* not ours */ }
  }
  a.redraw();
}

function watch(a: Adapter, target: any, j: Job): void {
  if (j.timer || typeof setInterval !== "function") return;
  j.timer = setInterval(() => {
    if (stateOf(target).job !== j) { clearInterval(j.timer); j.timer = 0; return; }
    try { pass(a, j); } catch { /* a scene is changing: the next look finds it */ }
  }, WATCH_MS);
  j.timer?.unref?.();
}

function jobFor(a: Adapter, target: any, opts: StageOpts): Job {
  const state = stateOf(target);
  let j = state.job;
  if (!j) {
    j = { parts: {}, items: new Map(), objects: new Map(), done: new WeakSet(), loading: new Set(), failed: new Map(), opts, timer: 0,
      rep: { parts: 0, placed: 0, pending: 0, missing: [], errors: [] }, until: 0, told: false };
    state.job = j;
    watch(a, target, j);
  }
  return j;
}

/** The saved document onto a 2D game: what `applyStudioEdits` does when its target is 2D. It keeps
 *  watching, because a game makes objects late and a scene can make them again. */
export async function applyStage(target: any, doc: any, opts: StageOpts = {}): Promise<StageReport> {
  const a = adapterOf(target);
  if (!a) {
    const rep = { parts: 0, placed: 0, pending: 0, missing: [], errors: ["not a Phaser game, a PixiJS app or a document"] };
    try { opts.onSettle?.(rep); } catch { /* not ours */ }
    return rep;
  }
  const j = jobFor(a, target, opts);
  j.opts = opts;
  j.parts = {};
  for (const [k, v] of Object.entries(doc?.parts || {})) j.parts[k] = cleanVals(v);
  j.done = new WeakSet();
  j.told = false;
  j.until = Date.now() + (typeof opts.retryMs === "number" ? opts.retryMs : 60000);
  j.failed.clear();

  const keep = new Set<string>();
  j.items.clear();
  if (opts.placed !== false) {
    for (const it of Array.isArray(doc?.placed) ? doc.placed : []) {
      if (!it?.id) continue;
      if (it?.ref?.kind !== "image" || !it.ref.url) {
        j.failed.set(String(it.id), String(it.name || it.id) + ": a 2D game places images only");
        continue;
      }
      j.items.set(String(it.id), it);
      keep.add(String(it.id));
    }
  }
  for (const [id, o] of [...j.objects]) {
    if (!keep.has(id)) { a.remove(o); j.objects.delete(id); }
    else {
      a.write(o, itemVals(j.items.get(id)), "placed/" + id);
    }
  }
  pass(a, j);
  const wait = Array.from(j.loading).length;
  if (wait) {
    await new Promise<void>((done) => {
      const t0 = Date.now();
      const look = () => (!j.loading.size || Date.now() - t0 > 8000 ? done() : setTimeout(look, 40));
      look();
    });
    pass(a, j);
  }
  return j.rep;
}

// ------------------------------------------------------------------ the Studio's calls

export interface StageResult {
  ok: boolean; key?: string; name?: string; placed?: string; error?: string;
  before?: StageVals; after?: StageVals; save?: StageVals; undo?: StageVals;
}

function resolveTarget(rows: Found[], target: string): { hit: Found | null; error?: string } {
  const exact = rows.find((f) => f.key === target);
  if (exact) return { hit: exact };
  const same = rows.filter((f) => f.name === target);
  if (same.length === 1) return { hit: same[0] };
  if (same.length > 1) return { hit: null, error: target + " names " + same.length + " objects: use a key (" + same.slice(0, 4).map((f) => f.key).join(", ") + ")" };
  return { hit: null, error: "nothing is called " + target + " on this screen" };
}

function roundVals(v: StageVals): StageVals {
  const o: StageVals = {};
  if (v.pos) o.pos = [r3(v.pos[0]), r3(v.pos[1]), 0];
  if (v.rot) o.rot = [0, 0, Math.round(num(v.rot[2], 0) * 1e6) / 1e6];
  if (v.scale) o.scale = [r3(v.scale[0]), r3(v.scale[1]), 1];
  if (typeof v.alpha === "number") o.alpha = r3(v.alpha);
  if (typeof v.hidden === "boolean") o.hidden = v.hidden;
  return o;
}

/** One change, in the terms an agent and the Edit tab use: `pos` is absolute, `move` is page pixels
 *  on screen, `rotate` adds to `rot`, and both rotations are RADIANS here (the backend converts). */
function editOne(a: Adapter, rows: Found[], job: Job | undefined, sp: any): StageResult {
  const { hit, error } = resolveTarget(rows, String(sp?.target || ""));
  if (!hit) return { ok: false, error };
  const o = hit.obj;
  const before = a.read(o, hit.key);
  const change: StageVals = {};
  if (vector(sp.pos, 2)) change.pos = [sp.pos[0], sp.pos[1]];
  if (vector(sp.move, 2)) {
    const [dx, dy] = a.delta(o, sp.move[0], sp.move[1]);
    const base = change.pos || before.pos || [0, 0];
    change.pos = [base[0] + dx, base[1] + dy];
  }
  if (typeof sp.rot === "number") change.rot = [0, 0, sp.rot];
  if (typeof sp.rotate === "number") change.rot = [0, 0, num(change.rot?.[2], num(before.rot?.[2], 0)) + sp.rotate];
  if (typeof sp.scale === "number") change.scale = [sp.scale, sp.scale];
  else if (vector(sp.scale, 2)) change.scale = [sp.scale[0], sp.scale[1]];
  if (typeof sp.alpha === "number") change.alpha = Math.max(0, Math.min(1, sp.alpha));
  if (typeof sp.visible === "boolean") change.hidden = !sp.visible;
  if (!Object.keys(change).length) return { ok: false, key: hit.key, error: hit.key + ": nothing to change" };
  a.write(o, change, hit.key);
  a.redraw();
  const after = a.read(o, hit.key);
  if (job && sp.save) {
    if (hit.placed) {
      const it = job.items.get(hit.placed);
      if (it) Object.assign(it, roundVals(change));
    } else {
      job.parts[hit.key] = { ...(job.parts[hit.key] || {}), ...change };
      job.done.add(o);
    }
  }
  const undo: StageVals = {};
  for (const f of Object.keys(change) as (keyof StageVals)[]) (undo as any)[f] = before[f];
  return {
    ok: true, key: hit.key, name: hit.name, ...(hit.placed ? { placed: hit.placed } : {}),
    before: roundVals(before), after: roundVals(after), save: roundVals(change), undo,
  };
}

/** The one entry the Studio calls in the page. `null` target: this page has a 3D engine. */
export async function stageCall(target: any, op: string, args: any, opts: StageOpts = {}): Promise<any> {
  const a = adapterOf(target);
  if (!a) return { ok: false, error: "not a Phaser game, a PixiJS app or a document" };
  const job = stateOf(target).job;
  if (op === "list") {
    const rows = a.walk();
    const out: StageRow[] = [];
    for (const f of rows) {
      try { const r = a.row(f); if (r) out.push(r); } catch { /* this object is being destroyed */ }
    }
    const saved = job ? Object.keys(job.parts) : [];
    return { ok: true, engine: a.engine, view: a.view(), rows: out, total: out.length, paused: a.paused(), saved };
  }
  const rows = a.walk();
  if (op === "edit") {
    const j = (args?.specs || []).some((s: any) => s?.save) ? jobFor(a, target, opts) : job;
    const results = (args?.specs || []).map((sp: any) => editOne(a, rows, j, sp));
    return { ok: true, engine: a.engine, results };
  }
  if (op === "restore") {
    const done: string[] = [];
    const missing: string[] = [];
    for (const it of args?.items || []) {
      const f = rows.find((x) => x.key === it.key);
      if (!f) { missing.push(it.key); continue; }
      const vals = cleanVals(it.vals);
      a.write(f.obj, vals, f.key);
      // The watcher too. A page that makes its elements again would otherwise get the undone value
      // back on the next look.
      if (job?.parts[f.key]) job.parts[f.key] = { ...job.parts[f.key], ...vals };
      done.push(it.key);
    }
    a.redraw();
    return { ok: true, restored: done, missing };
  }
  if (op === "at") {
    const [w, h] = a.view();
    const [x, y] = a.at(num(args?.x, w / 2), num(args?.y, h / 2));
    return { ok: true, pos: [r3(x), r3(y)] };
  }
  if (op === "place") {
    const j = jobFor(a, target, opts);
    const it = args?.item;
    if (!it?.id || it?.ref?.kind !== "image" || !it.ref.url) return { ok: false, error: "a 2D game places images only" };
    const id = String(it.id);
    const old = j.objects.get(id);
    if (old) { a.remove(old); j.objects.delete(id); }
    j.failed.delete(id);
    j.items.set(id, it);
    j.loading.add(id);
    try {
      const o = await a.place(it, urlOf(it, j.opts));
      o.__studioPlaced = id;
      if (a.engine === "dom") o.setAttribute("data-studio-placed", id);
      a.write(o, itemVals(it), "placed/" + id);
      j.objects.set(id, o);
      a.redraw();
      return { ok: true, id, key: "placed/" + id, name: it.name };
    } catch (e: any) {
      j.items.delete(id);
      return { ok: false, error: String(e?.message || e) };
    } finally { j.loading.delete(id); }
  }
  if (op === "unplace") {
    const removed: string[] = [];
    for (const id of args?.ids || []) {
      job?.items.delete(String(id));
      const o = job?.objects.get(String(id));
      if (o) { a.remove(o); job?.objects.delete(String(id)); removed.push(String(id)); }
    }
    a.redraw();
    return { ok: true, removed };
  }
  if (op === "pause") {
    a.pause(!!args?.on);
    // Phaser queues a pause for its next step, so the answer waits for the game to take it.
    const t0 = Date.now();
    while (a.paused() !== !!args?.on && Date.now() - t0 < 500) await new Promise((done) => setTimeout(done, 20));
    return { ok: true, paused: a.paused() };
  }
  return { ok: false, error: "unknown stage call: " + op };
}
