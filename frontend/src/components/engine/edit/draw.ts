// DRAW: the 2D half of the Studio Engine's art tools, for sprites a 2D game uses.
//
// A drawing is a few layers over a few frames, every cel a plain RGBA image the size of the
// canvas (straight alpha, as a canvas ImageData and a PNG keep it). The tools are an art program's
// and a pixel editor's at once: a soft brush that uses Paint's brush (paintCore: the same falloff,
// spacing and blend modes), a pencil that sets whole pixels with no smoothing, an eraser, a flood
// fill, lines, rectangles and ellipses, a mirror, and onion skin for animation. What goes into the
// game is a PNG, or a sprite sheet with the atlas JSON Phaser and PixiJS read.
//
// A STROKE NEVER COVERS MORE THAN ITS OPACITY. Each dab adds to a coverage mask for the stroke,
// and the cel is made again from its state at the stroke's start every time, so a 40% brush that
// crosses itself stays 40% — Paint's rule, kept here.
//
// Plain arrays, no DOM, no three: `npm run test:draw` draws in node.

import { BLEND_MODES, DabPlacer, DEFAULT_BRUSH, clamp, compositeOver, falloff, hexToRgb, type BlendMode, type BrushSettings, type RGB, type StrokeSample } from "./paintCore";

export type DrawTool = "pencil" | "brush" | "eraser" | "fill" | "line" | "rect" | "ellipse" | "picker" | "move";
export const DRAW_TOOLS: DrawTool[] = ["pencil", "brush", "eraser", "fill", "line", "rect", "ellipse", "picker", "move"];

export interface DrawLayer { id: string; name: string; visible: boolean; opacity: number; blend: BlendMode }

export interface DrawDoc {
  w: number;
  h: number;
  /** Pixel art: the pencil is the default tool, the view is never smoothed, shapes have hard edges. */
  pixel: boolean;
  fps: number;
  layers: DrawLayer[];
  /** frames[f][l]: the cel of layer l in frame f, RGBA straight alpha, w * h * 4 bytes. */
  frames: Uint8ClampedArray[][];
}

let nextId = 1;
const layerId = () => "L" + (nextId++).toString(36) + Math.floor(Math.random() * 1e6).toString(36);
const blank = (d: { w: number; h: number }) => new Uint8ClampedArray(d.w * d.h * 4);

export function newDoc(w: number, h: number, opts: { pixel?: boolean; fps?: number } = {}): DrawDoc {
  const W = Math.round(clamp(w, 1, 4096)), H = Math.round(clamp(h, 1, 4096));
  return {
    w: W, h: H, pixel: opts.pixel ?? (W <= 128 && H <= 128), fps: opts.fps ?? 8,
    layers: [{ id: layerId(), name: "Layer 1", visible: true, opacity: 1, blend: "normal" }],
    frames: [[blank({ w: W, h: H })]],
  };
}

export function cloneDoc(d: DrawDoc): DrawDoc {
  return { ...d, layers: d.layers.map((l) => ({ ...l })), frames: d.frames.map((f) => f.map((c) => new Uint8ClampedArray(c))) };
}

// ------------------------------------------------------------------ layers and frames
export function addLayer(d: DrawDoc, at = d.layers.length): number {
  const i = clamp(Math.round(at), 0, d.layers.length);
  d.layers.splice(i, 0, { id: layerId(), name: "Layer " + (d.layers.length + 1), visible: true, opacity: 1, blend: "normal" });
  for (const f of d.frames) f.splice(i, 0, blank(d));
  return i;
}

export function removeLayer(d: DrawDoc, i: number): boolean {
  if (d.layers.length <= 1 || i < 0 || i >= d.layers.length) return false;
  d.layers.splice(i, 1);
  for (const f of d.frames) f.splice(i, 1);
  return true;
}

export function moveLayer(d: DrawDoc, i: number, to: number): number {
  const j = clamp(to, 0, d.layers.length - 1);
  if (i === j || i < 0 || i >= d.layers.length) return i;
  const [l] = d.layers.splice(i, 1);
  d.layers.splice(j, 0, l);
  for (const f of d.frames) { const [c] = f.splice(i, 1); f.splice(j, 0, c); }
  return j;
}

/** Layer i put onto the one below it, with its opacity and blend, in every frame. */
export function mergeDown(d: DrawDoc, i: number): boolean {
  if (i <= 0 || i >= d.layers.length) return false;
  const top = d.layers[i];
  for (const f of d.frames) f[i - 1] = compositeCel(f[i - 1], f[i], top.opacity, top.blend);
  d.layers.splice(i, 1);
  for (const f of d.frames) f.splice(i, 1);
  return true;
}

export function addFrame(d: DrawDoc, after = d.frames.length - 1, copy = true): number {
  const src = d.frames[clamp(after, 0, d.frames.length - 1)];
  const at = clamp(after + 1, 0, d.frames.length);
  d.frames.splice(at, 0, src.map((c) => (copy ? new Uint8ClampedArray(c) : blank(d))));
  return at;
}

export function removeFrame(d: DrawDoc, f: number): boolean {
  if (d.frames.length <= 1 || f < 0 || f >= d.frames.length) return false;
  d.frames.splice(f, 1);
  return true;
}

export function moveFrame(d: DrawDoc, f: number, to: number): number {
  const j = clamp(to, 0, d.frames.length - 1);
  if (f === j || f < 0 || f >= d.frames.length) return f;
  const [x] = d.frames.splice(f, 1);
  d.frames.splice(j, 0, x);
  return j;
}

// ------------------------------------------------------------------ compositing
/** `top` over `bottom` at an opacity and in a blend mode: a new cel. */
export function compositeCel(bottom: Uint8ClampedArray, top: Uint8ClampedArray, opacity = 1, blend: BlendMode = "normal"): Uint8ClampedArray {
  const out = new Uint8ClampedArray(bottom);
  for (let i = 0; i < out.length; i += 4) {
    const as = (top[i + 3] / 255) * opacity;
    if (as <= 0) continue;
    const ab = out[i + 3] / 255;
    const o = compositeOver([out[i] / 255, out[i + 1] / 255, out[i + 2] / 255], ab,
      [top[i] / 255, top[i + 1] / 255, top[i + 2] / 255], as, blend);
    out[i] = o.c[0] * 255; out[i + 1] = o.c[1] * 255; out[i + 2] = o.c[2] * 255; out[i + 3] = o.a * 255;
  }
  return out;
}

/** What frame f looks like: its visible layers, bottom first. With `onion`, the frames before and
 *  after show faintly under it (red before, green after), the way an animator's light table does. */
export function flatten(d: DrawDoc, f: number, opts: { onion?: number; hide?: boolean } = {}): Uint8ClampedArray {
  let out: Uint8ClampedArray = blank(d);
  const ghost = (g: number, tint: RGB) => {
    if (g < 0 || g >= d.frames.length || g === f) return;
    const flat = flatten(d, g);
    const t = new Uint8ClampedArray(flat.length);
    for (let i = 0; i < t.length; i += 4) {
      t[i] = (flat[i] + tint[0] * 255) / 2; t[i + 1] = (flat[i + 1] + tint[1] * 255) / 2; t[i + 2] = (flat[i + 2] + tint[2] * 255) / 2;
      t[i + 3] = flat[i + 3];
    }
    out = compositeCel(out, t, opts.onion!, "normal");
  };
  if (opts.onion && opts.onion > 0) { ghost(f - 1, [1, 0.2, 0.2]); ghost(f + 1, [0.2, 1, 0.3]); }
  const cels = d.frames[f] || [];
  d.layers.forEach((l, i) => { if (l.visible && cels[i]) out = compositeCel(out, cels[i], l.opacity, l.blend); });
  return out;
}

// ------------------------------------------------------------------ strokes
export interface StrokeOpts {
  tool: "pencil" | "brush" | "eraser";
  colour: string;
  /** Diameter in canvas pixels. */
  size: number;
  /** 0..1, the most one stroke puts down. */
  opacity: number;
  hardness?: number;
  spacing?: number;
  blend?: BlendMode;
  /** Draw the same stroke mirrored across the vertical middle. */
  mirrorX?: boolean;
  pressureSize?: boolean;
}

/**
 * One stroke on one cel. `add` takes pointer samples in canvas pixels; the cel is updated in
 * place after each call and `dirty` says which rectangle changed, for a cheap redraw.
 */
export class Stroke {
  private base: Uint8ClampedArray;
  private cov: Float32Array;
  private placer: DabPlacer | null = null;
  private lastPx: [number, number] | null = null;
  private rgb: RGB;
  dirty: [number, number, number, number] | null = null;

  constructor(private cel: Uint8ClampedArray, private w: number, private h: number, private o: StrokeOpts) {
    this.base = new Uint8ClampedArray(cel);
    this.cov = new Float32Array(w * h);
    this.rgb = hexToRgb(o.colour);
    if (o.tool !== "pencil") {
      const b: BrushSettings = { ...DEFAULT_BRUSH, size: Math.max(0.5, o.size), hardness: o.hardness ?? 0.7, spacing: o.spacing ?? 0.12, opacity: 1, flow: 1, pressureSize: o.pressureSize ?? true };
      this.placer = new DabPlacer(b, 1);
    }
  }

  add(samples: StrokeSample[], first = false): void {
    for (const s of samples) {
      if (this.o.tool === "pencil") this.pencil(s, first && s === samples[0]);
      else {
        const dabs = first && s === samples[0] ? this.placer!.begin(s) : this.placer!.moveTo(s);
        for (const d of dabs) {
          this.dab(d.x, d.y, d.size, d.alpha);
          if (this.o.mirrorX) this.dab(this.w - d.x, d.y, d.size, d.alpha);
        }
      }
    }
    this.apply();
  }

  /** Whole pixels along a line: Bresenham, a square nib `size` pixels wide, no smoothing. */
  private pencil(s: StrokeSample, first: boolean) {
    const x = Math.floor(s.x), y = Math.floor(s.y);
    const from = first || !this.lastPx ? [x, y] : this.lastPx;
    pixelLine(from[0], from[1], x, y, (px, py) => {
      this.nib(px, py);
      if (this.o.mirrorX) this.nib(this.w - 1 - px, py);
    });
    this.lastPx = [x, y];
  }

  private nib(cx: number, cy: number) {
    const n = Math.max(1, Math.round(this.o.size)), h0 = Math.floor((n - 1) / 2);
    for (let y = cy - h0; y < cy - h0 + n; y++) for (let x = cx - h0; x < cx - h0 + n; x++) this.cover(x, y, 1);
  }

  private dab(cx: number, cy: number, size: number, alpha: number) {
    const r = size / 2, hard = this.o.hardness ?? 0.7;
    const x0 = Math.floor(cx - r), x1 = Math.ceil(cx + r), y0 = Math.floor(cy - r), y1 = Math.ceil(cy + r);
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      // Distance from the pixel's middle; a dab smaller than a pixel still marks the pixel it is in.
      const d = Math.hypot(x + 0.5 - cx, y + 0.5 - cy) / Math.max(0.5, r);
      const k = r < 0.75 ? (Math.floor(cx) === x && Math.floor(cy) === y ? 1 : 0) : falloff(d, hard);
      if (k > 0) this.cover(x, y, k * alpha);
    }
  }

  private cover(x: number, y: number, k: number) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    const i = y * this.w + x;
    this.cov[i] = this.cov[i] + (1 - this.cov[i]) * clamp(k, 0, 1);
    const d = this.dirty;
    if (!d) this.dirty = [x, y, x, y];
    else { d[0] = Math.min(d[0], x); d[1] = Math.min(d[1], y); d[2] = Math.max(d[2], x); d[3] = Math.max(d[3], y); }
  }

  /** The cel, made again from its state at the stroke's start and the stroke's coverage. */
  private apply() {
    const d = this.dirty;
    if (!d) return;
    const { cel, base, cov, rgb } = this;
    const op = clamp(this.o.opacity, 0, 1), blend = this.o.blend || "normal", erase = this.o.tool === "eraser";
    for (let y = d[1]; y <= d[3]; y++) for (let x = d[0]; x <= d[2]; x++) {
      const p = y * this.w + x, i = p * 4;
      const k = cov[p] * op;
      if (k <= 0) continue;
      if (erase) { cel[i] = base[i]; cel[i + 1] = base[i + 1]; cel[i + 2] = base[i + 2]; cel[i + 3] = base[i + 3] * (1 - k); continue; }
      const o = compositeOver([base[i] / 255, base[i + 1] / 255, base[i + 2] / 255], base[i + 3] / 255, rgb, k, blend);
      cel[i] = o.c[0] * 255; cel[i + 1] = o.c[1] * 255; cel[i + 2] = o.c[2] * 255; cel[i + 3] = o.a * 255;
    }
  }
}

/** The pixels of a line from (x0, y0) to (x1, y1), both ends included. */
export function pixelLine(x0: number, y0: number, x1: number, y1: number, plot: (x: number, y: number) => void): void {
  let x = Math.round(x0), y = Math.round(y0);
  const X = Math.round(x1), Y = Math.round(y1);
  const dx = Math.abs(X - x), dy = -Math.abs(Y - y), sx = x < X ? 1 : -1, sy = y < Y ? 1 : -1;
  let err = dx + dy;
  for (let guard = 0; guard < 1e6; guard++) {
    plot(x, y);
    if (x === X && y === Y) return;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x += sx; }
    if (e2 <= dx) { err += dx; y += sy; }
  }
}

// ------------------------------------------------------------------ fill
/**
 * A flood fill from (x, y): the pixels that match the one clicked, within `tolerance` (0..1 of the
 * largest channel difference, alpha included), joined to it — or every matching pixel in the cel
 * when `contiguous` is off. Returns how many pixels it painted.
 */
export function floodFill(cel: Uint8ClampedArray, w: number, h: number, x: number, y: number, colour: string,
  opts: { tolerance?: number; contiguous?: boolean; opacity?: number; erase?: boolean } = {}): number {
  const sx = Math.floor(x), sy = Math.floor(y);
  if (sx < 0 || sy < 0 || sx >= w || sy >= h) return 0;
  const t = Math.round(clamp(opts.tolerance ?? 0, 0, 1) * 255);
  const s = (sy * w + sx) * 4;
  const ref = [cel[s], cel[s + 1], cel[s + 2], cel[s + 3]];
  const like = (p: number) => {
    const i = p * 4;
    // Two transparent pixels match whatever colour they hold: an empty pixel is empty.
    if (ref[3] === 0 && cel[i + 3] <= t) return true;
    return Math.abs(cel[i] - ref[0]) <= t && Math.abs(cel[i + 1] - ref[1]) <= t && Math.abs(cel[i + 2] - ref[2]) <= t && Math.abs(cel[i + 3] - ref[3]) <= t;
  };
  const hit = new Uint8Array(w * h);
  if (opts.contiguous === false) {
    for (let p = 0; p < w * h; p++) if (like(p)) hit[p] = 1;
  } else {
    const stack = [sy * w + sx];
    hit[stack[0]] = 1;
    while (stack.length) {
      const p = stack.pop()!;
      const px = p % w, py = (p - px) / w;
      for (const q of [px > 0 ? p - 1 : -1, px < w - 1 ? p + 1 : -1, py > 0 ? p - w : -1, py < h - 1 ? p + w : -1]) {
        if (q >= 0 && !hit[q] && like(q)) { hit[q] = 1; stack.push(q); }
      }
    }
  }
  const rgb = hexToRgb(colour), op = clamp(opts.opacity ?? 1, 0, 1);
  let n = 0;
  for (let p = 0; p < w * h; p++) {
    if (!hit[p]) continue;
    const i = p * 4;
    n++;
    if (opts.erase) { cel[i + 3] = cel[i + 3] * (1 - op); continue; }
    const o = compositeOver([cel[i] / 255, cel[i + 1] / 255, cel[i + 2] / 255], cel[i + 3] / 255, rgb, op, "normal");
    cel[i] = o.c[0] * 255; cel[i + 1] = o.c[1] * 255; cel[i + 2] = o.c[2] * 255; cel[i + 3] = o.a * 255;
  }
  return n;
}

// ------------------------------------------------------------------ shapes
export interface ShapeOpts { colour: string; opacity?: number; filled?: boolean; thickness?: number; smooth?: boolean; mirrorX?: boolean; erase?: boolean }

/** A line, rectangle or ellipse from one corner (x0, y0) to the other, drawn onto `cel`. With
 *  `smooth` off (pixel art) every pixel is in or out; with it on, the edge is anti-aliased. */
export function drawShape(cel: Uint8ClampedArray, w: number, h: number, kind: "line" | "rect" | "ellipse",
  x0: number, y0: number, x1: number, y1: number, o: ShapeOpts): void {
  const cov = new Float32Array(w * h);
  const put = (x: number, y: number, k: number) => {
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    const p = y * w + x;
    cov[p] = Math.max(cov[p], clamp(k, 0, 1));
  };
  const th = Math.max(1, o.thickness ?? 1);
  const marks: Array<[number, number, number, number]> = [[x0, y0, x1, y1]];
  if (o.mirrorX) marks.push([w - x0, y0, w - x1, y1]);
  for (const [a, b, c, d] of marks) {
    if (kind === "line") {
      if (!o.smooth) pixelLine(Math.floor(a), Math.floor(b), Math.floor(c), Math.floor(d), (x, y) => square(x, y, th, put));
      else smoothLine(a, b, c, d, th, put, w, h);
      continue;
    }
    const lx = Math.min(a, c), hx = Math.max(a, c), ly = Math.min(b, d), hy = Math.max(b, d);
    if (kind === "rect") rectCov(Math.floor(lx), Math.floor(ly), Math.floor(hx), Math.floor(hy), !!o.filled, Math.round(th), put);
    else ellipseCov(lx, ly, hx, hy, !!o.filled, th, !!o.smooth, put);
  }
  const rgb = hexToRgb(o.colour), op = clamp(o.opacity ?? 1, 0, 1);
  for (let p = 0; p < w * h; p++) {
    const k = cov[p] * op;
    if (k <= 0) continue;
    const i = p * 4;
    if (o.erase) { cel[i + 3] = cel[i + 3] * (1 - k); continue; }
    const r = compositeOver([cel[i] / 255, cel[i + 1] / 255, cel[i + 2] / 255], cel[i + 3] / 255, rgb, k, "normal");
    cel[i] = r.c[0] * 255; cel[i + 1] = r.c[1] * 255; cel[i + 2] = r.c[2] * 255; cel[i + 3] = r.a * 255;
  }
}

function square(cx: number, cy: number, n: number, put: (x: number, y: number, k: number) => void) {
  const s = Math.max(1, Math.round(n)), h0 = Math.floor((s - 1) / 2);
  for (let y = cy - h0; y < cy - h0 + s; y++) for (let x = cx - h0; x < cx - h0 + s; x++) put(x, y, 1);
}

function smoothLine(x0: number, y0: number, x1: number, y1: number, th: number, put: (x: number, y: number, k: number) => void, w: number, h: number) {
  const r = th / 2;
  const lx = Math.max(0, Math.floor(Math.min(x0, x1) - r - 1)), hx = Math.min(w - 1, Math.ceil(Math.max(x0, x1) + r + 1));
  const ly = Math.max(0, Math.floor(Math.min(y0, y1) - r - 1)), hy = Math.min(h - 1, Math.ceil(Math.max(y0, y1) + r + 1));
  const dx = x1 - x0, dy = y1 - y0, L2 = dx * dx + dy * dy;
  for (let y = ly; y <= hy; y++) for (let x = lx; x <= hx; x++) {
    const px = x + 0.5, py = y + 0.5;
    const t = L2 > 0 ? clamp(((px - x0) * dx + (py - y0) * dy) / L2, 0, 1) : 0;
    const dist = Math.hypot(px - (x0 + dx * t), py - (y0 + dy * t));
    put(x, y, clamp(r + 0.5 - dist, 0, 1));
  }
}

function rectCov(x0: number, y0: number, x1: number, y1: number, filled: boolean, th: number, put: (x: number, y: number, k: number) => void) {
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
    const edge = x - x0 < th || x1 - x < th || y - y0 < th || y1 - y < th;
    if (filled || edge) put(x, y, 1);
  }
}

function ellipseCov(x0: number, y0: number, x1: number, y1: number, filled: boolean, th: number, smooth: boolean,
  put: (x: number, y: number, k: number) => void) {
  // Pixel art draws the ellipse through the pixels it was dragged over, both ends included.
  const ax = smooth ? x0 : Math.floor(x0), bx = smooth ? x1 : Math.floor(x1) + 1;
  const ay = smooth ? y0 : Math.floor(y0), by = smooth ? y1 : Math.floor(y1) + 1;
  const cx = (ax + bx) / 2, cy = (ay + by) / 2, rx = Math.max(0.5, (bx - ax) / 2), ry = Math.max(0.5, (by - ay) / 2);
  for (let y = Math.floor(ay); y < Math.ceil(by); y++) for (let x = Math.floor(ax); x < Math.ceil(bx); x++) {
    // How far inside the ellipse the pixel's middle is, in pixels (approximately).
    const nx = (x + 0.5 - cx) / rx, ny = (y + 0.5 - cy) / ry;
    const f = Math.hypot(nx, ny);
    const outside = (f - 1) * Math.min(rx, ry);
    const inside = filled ? -Infinity : -th;
    if (smooth) put(x, y, clamp(0.5 - outside, 0, 1) * clamp(outside - inside + 0.5, 0, 1));
    else if (outside <= 0 && outside > inside) put(x, y, 1);
  }
}

/** The colour under (x, y) in a flattened frame, as #rrggbb, or "" where it is empty. */
export function pickColour(flat: Uint8ClampedArray, w: number, x: number, y: number): string {
  const i = (Math.floor(y) * w + Math.floor(x)) * 4;
  if (i < 0 || i >= flat.length || flat[i + 3] === 0) return "";
  const b = (v: number) => v.toString(16).padStart(2, "0");
  return "#" + b(flat[i]) + b(flat[i + 1]) + b(flat[i + 2]);
}

/** A cel moved by whole pixels; what leaves the canvas is gone. */
export function shiftCel(cel: Uint8ClampedArray, w: number, h: number, dx: number, dy: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(cel.length);
  const ix = Math.round(dx), iy = Math.round(dy);
  for (let y = 0; y < h; y++) {
    const sy = y - iy;
    if (sy < 0 || sy >= h) continue;
    for (let x = 0; x < w; x++) {
      const sx = x - ix;
      if (sx < 0 || sx >= w) continue;
      out.set(cel.subarray((sy * w + sx) * 4, (sy * w + sx) * 4 + 4), (y * w + x) * 4);
    }
  }
  return out;
}

// ------------------------------------------------------------------ undo
type Step = { kind: "cel"; f: number; l: number; data: Uint8ClampedArray } | { kind: "doc"; doc: DrawDoc };

/** Undo and redo. A paint step keeps one cel; a change of layers or frames keeps the drawing. */
export class DrawHistory {
  private undoS: Step[] = [];
  private redoS: Step[] = [];
  constructor(private max = 80) {}
  get canUndo() { return this.undoS.length > 0; }
  get canRedo() { return this.redoS.length > 0; }

  /** Call BEFORE a cel changes. */
  cel(d: DrawDoc, f: number, l: number) { this.push({ kind: "cel", f, l, data: new Uint8ClampedArray(d.frames[f][l]) }); }
  /** Call BEFORE layers or frames change. */
  doc(d: DrawDoc) { this.push({ kind: "doc", doc: cloneDoc(d) }); }

  private push(s: Step) {
    this.undoS.push(s);
    if (this.undoS.length > this.max) this.undoS.shift();
    this.redoS = [];
  }

  /** The drawing after one undo (the same object for a cel step, a new one for a doc step). */
  undo(d: DrawDoc): DrawDoc { return this.swap(d, this.undoS, this.redoS); }
  redo(d: DrawDoc): DrawDoc { return this.swap(d, this.redoS, this.undoS); }

  private swap(d: DrawDoc, from: Step[], to: Step[]): DrawDoc {
    const s = from.pop();
    if (!s) return d;
    if (s.kind === "doc") { to.push({ kind: "doc", doc: cloneDoc(d) }); return s.doc; }
    if (!d.frames[s.f]?.[s.l]) return d;
    to.push({ kind: "cel", f: s.f, l: s.l, data: new Uint8ClampedArray(d.frames[s.f][s.l]) });
    d.frames[s.f][s.l] = s.data;
    return d;
  }
}

// ------------------------------------------------------------------ for the game
export interface SheetFrame { x: number; y: number; w: number; h: number }

/** Every frame flattened into one image, in a grid of `cols` (default: as square as it fits). */
export function spriteSheet(d: DrawDoc, cols = 0, pad = 0): { w: number; h: number; data: Uint8ClampedArray; frames: SheetFrame[] } {
  const n = d.frames.length;
  const c = cols > 0 ? Math.min(cols, n) : Math.ceil(Math.sqrt(n));
  const r = Math.ceil(n / c);
  const W = c * d.w + (c - 1) * pad, H = r * d.h + (r - 1) * pad;
  const data = new Uint8ClampedArray(W * H * 4);
  const frames: SheetFrame[] = [];
  for (let f = 0; f < n; f++) {
    const fx = (f % c) * (d.w + pad), fy = Math.floor(f / c) * (d.h + pad);
    const flat = flatten(d, f);
    for (let y = 0; y < d.h; y++) data.set(flat.subarray(y * d.w * 4, (y + 1) * d.w * 4), ((fy + y) * W + fx) * 4);
    frames.push({ x: fx, y: fy, w: d.w, h: d.h });
  }
  return { w: W, h: H, data, frames };
}

/** The atlas for a sheet: TexturePacker's "JSON Hash", which Phaser's `load.atlas` and PixiJS's
 *  `Assets.load` read, with the animation listed for PixiJS and the frame time for both. */
export function atlasJson(name: string, image: string, sheet: { w: number; h: number; frames: SheetFrame[] }, fps: number): any {
  const frames: Record<string, any> = {};
  const names: string[] = [];
  const ms = Math.round(1000 / Math.max(1, fps));
  sheet.frames.forEach((f, i) => {
    const key = name + "_" + i;
    names.push(key);
    frames[key] = {
      frame: { x: f.x, y: f.y, w: f.w, h: f.h }, rotated: false, trimmed: false,
      spriteSourceSize: { x: 0, y: 0, w: f.w, h: f.h }, sourceSize: { w: f.w, h: f.h }, duration: ms,
    };
  });
  return {
    frames,
    animations: { [name]: names },
    meta: { app: "Asset Studio", version: "1", image, format: "RGBA8888", size: { w: sheet.w, h: sheet.h }, scale: "1",
            frameTags: [{ name, from: 0, to: names.length - 1, direction: "forward" }] },
  };
}

// ------------------------------------------------------------------ the file
/** The drawing as JSON, every cel as a string the caller makes (a PNG data URL in a browser). */
export function docToJson(d: DrawDoc, encode: (cel: Uint8ClampedArray, w: number, h: number) => string): any {
  return {
    format: "asset-studio-draw", version: 1, w: d.w, h: d.h, pixel: d.pixel, fps: d.fps,
    layers: d.layers.map(({ name, visible, opacity, blend }) => ({ name, visible, opacity, blend })),
    frames: d.frames.map((f) => f.map((c) => encode(c, d.w, d.h))),
  };
}

/** A drawing from its JSON, bad parts made blank or default rather than refused. */
export function docFromJson(j: any, decode: (s: string, w: number, h: number) => Uint8ClampedArray | null): DrawDoc | null {
  if (!j || j.format !== "asset-studio-draw" || !(j.w > 0) || !(j.h > 0)) return null;
  const d = newDoc(j.w, j.h, { pixel: !!j.pixel, fps: typeof j.fps === "number" && j.fps > 0 ? j.fps : 8 });
  const layers = Array.isArray(j.layers) && j.layers.length ? j.layers : [{}];
  d.layers = layers.map((l: any, i: number) => ({
    id: layerId(), name: typeof l?.name === "string" ? l.name : "Layer " + (i + 1), visible: l?.visible !== false,
    opacity: typeof l?.opacity === "number" ? clamp(l.opacity, 0, 1) : 1, blend: BLEND_MODES.includes(l?.blend) ? l.blend : "normal",
  }));
  const frames = Array.isArray(j.frames) && j.frames.length ? j.frames : [[]];
  d.frames = frames.map((f: any) => d.layers.map((_, i) => {
    const c = Array.isArray(f) && typeof f[i] === "string" ? decode(f[i], d.w, d.h) : null;
    return c && c.length === d.w * d.h * 4 ? c : blank(d);
  }));
  return d;
}
