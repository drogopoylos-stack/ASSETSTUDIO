// PAINT ON THE MODEL: the half with no GPU in it.
//
// Brushes, the stamps they print with, where a stroke puts its dabs, how two colours combine, what
// one undo takes back, and the file a painted asset keeps beside it. Everything here runs in node,
// so `npm run test:paint` can hold a pen down for a simulated second and check the numbers. The
// GPU half (paintGpu.ts) only draws what this half decides, with the same formulas written again
// in GLSL — `blendChannel`, `falloff` and `bakeStroke` here are the reference the shaders follow.
//
// Colour values in this file are the ones a person picks and a PNG stores: sRGB-encoded, 0..1.
// Paint mixes in that space, as Photoshop does by default, so a 50% grey brush at 50% over black
// gives the grey an artist expects, not the lighter one a linear mix gives.

export type PaintTool = "brush" | "eraser" | "smudge" | "blur" | "clone" | "fill" | "picker";
export const PAINT_TOOLS: PaintTool[] = ["brush", "eraser", "smudge", "blur", "clone", "fill", "picker"];

export type BlendMode = "normal" | "multiply" | "screen" | "overlay" | "add" | "darken" | "lighten";
export const BLEND_MODES: BlendMode[] = ["normal", "multiply", "screen", "overlay", "add", "darken", "lighten"];
/** The index each mode has in the shaders' `if` chain. Kept here so the two halves cannot drift. */
export const BLEND_INDEX: Record<BlendMode, number> = {
  normal: 0, multiply: 1, screen: 2, overlay: 3, add: 4, darken: 5, lighten: 6,
};

/** The stamp a dab prints. "round" is drawn by the shader from hardness alone; the others are
 *  small alpha images made by `tipMask`, so a brush with texture needs no image file. */
export type TipKind = "round" | "square" | "chalk" | "noise" | "splatter" | "bristle";
export const TIP_KINDS: TipKind[] = ["round", "square", "chalk", "noise", "splatter", "bristle"];

/** "view": what is under the brush on the screen is what gets paint — the way Substance and
 *  Blender project. "sphere": everything within the brush's radius in 3D, seen or not — what the
 *  mirror side and a replayed stroke use, because neither has a camera looking at it. */
export type Projection = "view" | "sphere";

export interface BrushSettings {
  /** Diameter on the screen, in CSS pixels. */
  size: number;
  /** 0 = soft all the way from the middle, 1 = a hard edge. Round tips only, as in Photoshop. */
  hardness: number;
  /** The most one stroke can put down. A stroke never covers more than this, however often it
   *  crosses itself — the rule that makes a 50% brush usable. */
  opacity: number;
  /** How much each dab adds. Low flow builds up the way an airbrush does. */
  flow: number;
  /** Distance between dabs as a fraction of the diameter. */
  spacing: number;
  tip: TipKind;
  /** Degrees. */
  angle: number;
  angleJitter: number;
  /** 0..1: each dab's size varies by up to this fraction. */
  sizeJitter: number;
  /** 0..2: each dab moves off the line by up to this many diameters. */
  scatter: number;
  /** The tip turns with the direction of the stroke — a flat brush or bristles. */
  followStroke: boolean;
  pressureSize: boolean;
  pressureOpacity: boolean;
  /** How the brush's colour meets the layer it paints on. */
  blend: BlendMode;
  /** Smudge, blur and clone: how strongly each dab acts. */
  strength: number;
}

export const DEFAULT_BRUSH: BrushSettings = {
  size: 24, hardness: 0.8, opacity: 1, flow: 1, spacing: 0.12, tip: "round",
  angle: 0, angleJitter: 0, sizeJitter: 0, scatter: 0, followStroke: false,
  pressureSize: true, pressureOpacity: false, blend: "normal", strength: 0.6,
};

/** Each tool keeps its own brush, the way Photoshop does: a soft eraser does not make the next
 *  brush stroke soft. */
export const TOOL_DEFAULTS: Record<PaintTool, BrushSettings> = {
  brush: { ...DEFAULT_BRUSH },
  eraser: { ...DEFAULT_BRUSH, size: 30, hardness: 0.7, pressureSize: false, pressureOpacity: true },
  smudge: { ...DEFAULT_BRUSH, size: 30, hardness: 0.3, spacing: 0.08, strength: 0.7, pressureSize: false },
  blur: { ...DEFAULT_BRUSH, size: 40, hardness: 0.2, spacing: 0.1, strength: 0.5, pressureSize: false },
  clone: { ...DEFAULT_BRUSH, size: 30, hardness: 0.6, spacing: 0.08, strength: 1, pressureSize: false },
  fill: { ...DEFAULT_BRUSH, opacity: 1 },
  picker: { ...DEFAULT_BRUSH },
};

export interface PaintPreset {
  id: string;
  label: string;
  tool: PaintTool;
  hint: string;
  brush: Partial<BrushSettings>;
}

/** The pens. A preset sets a whole brush; the sliders change it from there. */
export const PRESETS: PaintPreset[] = [
  { id: "ink", label: "Ink pen", tool: "brush", hint: "Thin and hard; thicker with pen pressure",
    brush: { size: 5, hardness: 0.95, opacity: 1, flow: 1, spacing: 0.06, tip: "round", pressureSize: true, pressureOpacity: false, angleJitter: 0, sizeJitter: 0, scatter: 0, followStroke: false } },
  { id: "hard", label: "Hard round", tool: "brush", hint: "A plain brush with a firm edge",
    brush: { size: 22, hardness: 0.85, opacity: 1, flow: 1, spacing: 0.1, tip: "round", pressureSize: true, pressureOpacity: false, angleJitter: 0, sizeJitter: 0, scatter: 0, followStroke: false } },
  { id: "soft", label: "Soft airbrush", tool: "brush", hint: "Builds up slowly, with a soft edge",
    brush: { size: 60, hardness: 0, opacity: 0.8, flow: 0.08, spacing: 0.05, tip: "round", pressureSize: false, pressureOpacity: true, angleJitter: 0, sizeJitter: 0, scatter: 0, followStroke: false } },
  { id: "marker", label: "Marker", tool: "brush", hint: "Flat, angled and see-through; strokes do not build up",
    brush: { size: 16, hardness: 0.9, opacity: 0.5, flow: 1, spacing: 0.08, tip: "square", angle: 35, pressureSize: false, pressureOpacity: false, angleJitter: 0, sizeJitter: 0, scatter: 0, followStroke: false } },
  { id: "chalk", label: "Chalk", tool: "brush", hint: "A grainy edge and small gaps",
    brush: { size: 30, hardness: 0.7, opacity: 0.9, flow: 0.85, spacing: 0.22, tip: "chalk", angleJitter: 180, sizeJitter: 0.1, pressureSize: true, pressureOpacity: false, scatter: 0, followStroke: false } },
  { id: "grunge", label: "Grunge", tool: "brush", hint: "Rough noise, for dirt, wear and rust",
    brush: { size: 50, hardness: 0.5, opacity: 0.7, flow: 0.5, spacing: 0.3, tip: "noise", angleJitter: 180, sizeJitter: 0.3, pressureSize: false, pressureOpacity: true, scatter: 0.2, followStroke: false } },
  { id: "splatter", label: "Splatter", tool: "brush", hint: "Loose drops, for blood, mud or paint",
    brush: { size: 44, hardness: 0.9, opacity: 1, flow: 1, spacing: 0.7, tip: "splatter", angleJitter: 180, sizeJitter: 0.5, scatter: 0.6, pressureSize: false, pressureOpacity: false, followStroke: false } },
  { id: "bristle", label: "Bristle", tool: "brush", hint: "Streaks of a dry brush, along the stroke",
    brush: { size: 34, hardness: 0.8, opacity: 0.9, flow: 0.7, spacing: 0.04, tip: "bristle", followStroke: true, angle: 90, angleJitter: 4, sizeJitter: 0, scatter: 0, pressureSize: false, pressureOpacity: true } },
  { id: "eraser-hard", label: "Hard eraser", tool: "eraser", hint: "Takes your paint away; the original shows again",
    brush: { size: 24, hardness: 0.85, opacity: 1, flow: 1, spacing: 0.1, tip: "round", pressureSize: false, pressureOpacity: false } },
  { id: "eraser-soft", label: "Soft eraser", tool: "eraser", hint: "Fades your paint gradually",
    brush: { size: 60, hardness: 0, opacity: 1, flow: 0.15, spacing: 0.06, tip: "round", pressureSize: false, pressureOpacity: true } },
];

// ------------------------------------------------------------------ numbers
export const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** A tiny seeded generator: a jittered stroke comes out the same when it is replayed. */
export function mulberry32(seed: number): () => number {
  let a = (seed >>> 0) || 0x9e3779b9;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** How much of a round dab reaches `r` (0 at the middle, 1 at the rim). Solid out to `hardness`,
 *  then a smooth fall to nothing at the rim. The shader's `falloff` is this, line for line. */
export function falloff(r: number, hardness: number): number {
  if (r >= 1) return 0;
  const h = clamp(hardness, 0, 0.999);
  const t = clamp((r - h) / (1 - h), 0, 1);
  return 1 - t * t * (3 - 2 * t);
}

/** A pointer's pressure as the brush should read it. A mouse reports 0.5 while held — which would
 *  paint every mouse stroke at half size — so a mouse counts as full pressure. A pen that reports
 *  0 while down (a driver without pressure) counts as half. */
export function pressureOf(e: { pressure?: number; pointerType?: string }): number {
  const p = typeof e.pressure === "number" ? e.pressure : 1;
  if (e.pointerType === "pen") return p > 0 ? clamp(p, 0, 1) : 0.5;
  if (e.pointerType === "touch") return p > 0 ? clamp(p, 0, 1) : 1;
  return 1;
}

// ------------------------------------------------------------------ colour
export type RGB = [number, number, number];
export type RGBA = [number, number, number, number];

export function hexToRgb(hex: string): RGB {
  let h = String(hex || "").trim().replace(/^#/, "");
  if (/^[0-9a-f]{3}$/i.test(h)) h = h.split("").map((c) => c + c).join("");
  if (!/^[0-9a-f]{6}$/i.test(h)) return [0, 0, 0];
  return [parseInt(h.slice(0, 2), 16) / 255, parseInt(h.slice(2, 4), 16) / 255, parseInt(h.slice(4, 6), 16) / 255];
}

export function rgbToHex(c: RGB | RGBA | number[]): string {
  const b = (v: number) => Math.round(clamp(v, 0, 1) * 255).toString(16).padStart(2, "0");
  return "#" + b(c[0]) + b(c[1]) + b(c[2]);
}

export const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
export const linearToSrgb = (c: number) => (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);

/** The swatches a new palette starts with: the neutrals, then a painter's primaries and the
 *  earth colours game art uses most. */
export const DEFAULT_SWATCHES = [
  "#000000", "#3a3a3a", "#7a7a7a", "#bdbdbd", "#ffffff",
  "#8f1d1d", "#d63a2a", "#f08a24", "#f5cf3a", "#5f9e2f",
  "#2f6d3a", "#2a8fb8", "#2b4a9e", "#6b3fa0", "#5a3a22", "#a8744a",
];

/** The recent colours, newest first, with no duplicates and at most `max` of them. */
export function pushRecent(list: string[], hex: string, max = 12): string[] {
  const h = rgbToHex(hexToRgb(hex));
  return [h, ...list.filter((x) => x.toLowerCase() !== h)].slice(0, max);
}

// ------------------------------------------------------------------ blending
/** One channel of a blend mode: `b` is what is below, `s` the paint. The shaders' `blendC`. */
export function blendChannel(mode: BlendMode, b: number, s: number): number {
  switch (mode) {
    case "multiply": return b * s;
    case "screen": return 1 - (1 - b) * (1 - s);
    case "overlay": return b < 0.5 ? 2 * b * s : 1 - 2 * (1 - b) * (1 - s);
    case "add": return Math.min(1, b + s);
    case "darken": return Math.min(b, s);
    case "lighten": return Math.max(b, s);
    default: return s;
  }
}

/**
 * Paint of colour `cs` and alpha `as` put over a backdrop `cb`, `ab`, in a blend mode — the W3C
 * compositing formula, which is also Photoshop's. On a transparent backdrop every mode behaves as
 * Normal, which is why a Multiply brush on an empty layer still paints.
 */
export function compositeOver(cb: RGB, ab: number, cs: RGB, as: number, mode: BlendMode): { c: RGB; a: number } {
  const ao = as + ab * (1 - as);
  if (ao <= 1e-6) return { c: [0, 0, 0], a: 0 };
  const out: RGB = [0, 0, 0];
  for (let i = 0; i < 3; i++) {
    const mixed = (1 - ab) * cs[i] + ab * blendChannel(mode, cb[i], cs[i]);
    out[i] = (as * mixed + ab * cb[i] * (1 - as)) / ao;
  }
  return { c: out, a: ao };
}

/**
 * A finished stroke baked into a layer. Layers are stored PREMULTIPLIED (colour times alpha), the
 * form GPU blending wants; `k` is the stroke's coverage at this texel times its opacity.
 * An eraser scales the layer down; a brush composites its colour over it.
 */
export function bakeStroke(layer: RGBA, colour: RGB, k: number, mode: BlendMode, erase: boolean): RGBA {
  const kk = clamp(k, 0, 1);
  if (erase) return [layer[0] * (1 - kk), layer[1] * (1 - kk), layer[2] * (1 - kk), layer[3] * (1 - kk)];
  const ab = layer[3];
  const cb: RGB = ab > 1e-6 ? [layer[0] / ab, layer[1] / ab, layer[2] / ab] : [0, 0, 0];
  const o = compositeOver(cb, ab, colour, kk, mode);
  return [o.c[0] * o.a, o.c[1] * o.a, o.c[2] * o.a, o.a];
}

// ------------------------------------------------------------------ where the dabs go
export interface StrokeSample { x: number; y: number; pressure: number }

export interface Dab {
  x: number;
  y: number;
  pressure: number;
  /** Diameter in CSS pixels, after pressure and jitter. */
  size: number;
  /** Flow times pressure: what this one dab adds. */
  alpha: number;
  /** Radians. */
  angle: number;
  /** For the stamp's own randomness (splatter), so a replay prints the same drops. */
  seed: number;
  index: number;
}

/**
 * The dabs of one stroke, spaced by distance and not by time — a slow stroke and a fast one leave
 * the same line. The distance carried past the last dab is kept between pointer events, so a
 * stroke made of many short moves is spaced exactly like one long move.
 */
export class DabPlacer {
  private last: StrokeSample | null = null;
  private carry = 0;
  private n = 0;
  private dir = 0;
  private rng: () => number;

  constructor(private brush: BrushSettings, seed = 1) {
    this.rng = mulberry32(seed);
  }

  /** The diameter at a pressure, before jitter. */
  sizeAt(pressure: number): number {
    const b = this.brush;
    return Math.max(0.5, b.size * (b.pressureSize ? 0.15 + 0.85 * clamp(pressure, 0, 1) : 1));
  }

  begin(p: StrokeSample): Dab[] {
    this.last = { ...p };
    this.carry = 0;
    return [this.make(p)];
  }

  moveTo(p: StrokeSample): Dab[] {
    const last = this.last;
    if (!last) return this.begin(p);
    const dx = p.x - last.x, dy = p.y - last.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 1e-6) { last.pressure = p.pressure; return []; }
    this.dir = Math.atan2(dy, dx);
    const out: Dab[] = [];
    let travelled = 0;
    // A cap on dabs per move: a jump across the screen with a 1 px brush at 1% spacing would ask
    // for tens of thousands, and the frame would stall on something nobody can see.
    for (let guard = 0; guard < 4096; guard++) {
      const t0 = travelled / dist;
      const step = Math.max(0.5, clamp(this.brush.spacing, 0.01, 4) * this.sizeAt(lerp(last.pressure, p.pressure, t0)));
      const need = step - this.carry;
      if (travelled + need > dist) { this.carry += dist - travelled; break; }
      travelled += need;
      this.carry = 0;
      const t = travelled / dist;
      out.push(this.make({ x: last.x + dx * t, y: last.y + dy * t, pressure: lerp(last.pressure, p.pressure, t) }));
    }
    this.last = { ...p };
    return out;
  }

  private make(p: StrokeSample): Dab {
    const b = this.brush;
    const r = this.rng;
    const base = this.sizeAt(p.pressure);
    const size = Math.max(0.5, base * (1 + clamp(b.sizeJitter, 0, 1) * (r() * 2 - 1)));
    const deg = Math.PI / 180;
    const angle = b.angle * deg + (b.followStroke ? this.dir : 0) + b.angleJitter * deg * (r() * 2 - 1);
    let x = p.x, y = p.y;
    if (b.scatter > 0) {
      const along = (r() * 2 - 1) * b.scatter * base * 0.5;
      const across = (r() * 2 - 1) * b.scatter * base;
      x += Math.cos(this.dir) * along - Math.sin(this.dir) * across;
      y += Math.sin(this.dir) * along + Math.cos(this.dir) * across;
    }
    const alpha = clamp(b.flow, 0, 1) * (b.pressureOpacity ? clamp(p.pressure, 0, 1) : 1);
    return { x, y, pressure: p.pressure, size, alpha, angle, seed: Math.floor(r() * 1e9), index: this.n++ };
  }
}

// ------------------------------------------------------------------ stamps
function hash2(x: number, y: number, seed: number): number {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(seed | 0, 2246822519)) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

function valueNoise(x: number, y: number, seed: number): number {
  const xi = Math.floor(x), yi = Math.floor(y);
  const fx = x - xi, fy = y - yi;
  const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
  const a = hash2(xi, yi, seed), b = hash2(xi + 1, yi, seed);
  const c = hash2(xi, yi + 1, seed), d = hash2(xi + 1, yi + 1, seed);
  return lerp(lerp(a, b, sx), lerp(c, d, sx), sy);
}

/**
 * The stamp of a tip as an n x n alpha image, 0..255, row-major, y down. The same seed makes the
 * same stamp on every machine, which is what lets a painted asset be saved as strokes.
 */
export function tipMask(kind: TipKind, n = 128, seed = 7): Uint8Array {
  const out = new Uint8Array(n * n);
  const px = 2 / n;                       // one pixel in the -1..1 square
  const edge = (d: number) => clamp(d / px + 0.5, 0, 1);   // antialiased step at d = 0
  const drops: Array<[number, number, number]> = [];
  if (kind === "splatter") {
    const r = mulberry32(seed);
    const count = 14 + Math.floor(r() * 8);
    for (let i = 0; i < count; i++) {
      const ang = r() * Math.PI * 2, rad = Math.sqrt(r()) * 0.78;
      drops.push([Math.cos(ang) * rad, Math.sin(ang) * rad, 0.04 + r() * r() * 0.2]);
    }
  }
  const hairs: Array<[number, number, number]> = [];
  if (kind === "bristle") {
    const r = mulberry32(seed);
    for (let i = 0; i < 26; i++) hairs.push([-0.9 + 1.8 * r(), 0.025 + r() * 0.035, 0.45 + 0.55 * r()]);
  }
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const x = (i + 0.5) * px - 1, y = (j + 0.5) * px - 1;
      const r = Math.hypot(x, y);
      let a = 0;
      switch (kind) {
        case "round": a = edge(1 - r); break;
        case "square": a = edge(0.86 - Math.max(Math.abs(x), Math.abs(y))); break;
        case "chalk": {
          // A rim that wanders with the angle, and grain that leaves small gaps inside it.
          const rim = 0.86 + 0.12 * (valueNoise(Math.atan2(y, x) * 3 + 10, 0.5, seed) - 0.5) * 2;
          const grain = valueNoise(x * 14 + 50, y * 14 + 50, seed + 1) * 0.65 + valueNoise(x * 40, y * 40, seed + 2) * 0.35;
          a = edge(rim - r) * clamp((grain - 0.28) * 3.2, 0, 1);
          break;
        }
        case "noise": {
          let f = 0, amp = 0.5, fr = 3;
          for (let o = 0; o < 4; o++) { f += amp * valueNoise(x * fr + 20, y * fr + 20, seed + o); amp *= 0.5; fr *= 2.1; }
          const soft = 1 - clamp((r - 0.55) / 0.45, 0, 1);
          a = clamp((f - 0.32) * 2.4, 0, 1) * soft * soft;
          break;
        }
        case "splatter": {
          for (const [cx, cy, cr] of drops) a = Math.max(a, edge(cr - Math.hypot(x - cx, y - cy)));
          break;
        }
        case "bristle": {
          // Hair tips in a band across the stamp: dragged along a stroke, each one draws a streak.
          if (Math.abs(y) < 0.2) {
            for (const [hx, hw, ha] of hairs) a = Math.max(a, edge(hw - Math.abs(x - hx)) * ha * (1 - Math.abs(y) / 0.2));
          }
          break;
        }
      }
      out[j * n + i] = Math.round(clamp(a, 0, 1) * 255);
    }
  }
  return out;
}

// ------------------------------------------------------------------ undo
/** One changed rectangle of a layer, before and after, as the GPU stores it (RGBA8, rows bottom
 *  first — the order readPixels hands back and texSubImage takes). */
export interface TileData { x: number; y: number; w: number; h: number; before: Uint8Array; after: Uint8Array }

export interface PaintRecord {
  label: string;
  target: string;
  layer: string;
  /** A stroke: only the tiles it touched. */
  tiles?: TileData[];
  /** A layer operation. The engine that made it knows how to apply it both ways. */
  op?: { kind: string; [k: string]: any };
  /** Called when the record falls off the end of the history, so GPU memory it holds is freed. */
  free?: () => void;
}

export function recordBytes(r: PaintRecord): number {
  let n = 64;
  for (const t of r.tiles || []) n += t.before.byteLength + t.after.byteLength;
  if (r.op && typeof r.op.bytes === "number") n += r.op.bytes;
  return n;
}

/**
 * Undo and redo for paint, one stroke per step. Bounded by MEMORY rather than by a count: a stroke
 * across a whole 4K texture and a dot on an eye differ by four orders of magnitude, and a count
 * that is safe for the first throws away hours of the second.
 */
export class PaintHistory {
  private undoList: PaintRecord[] = [];
  private redoList: PaintRecord[] = [];
  private used = 0;

  constructor(public capBytes = 128 * 1024 * 1024, public minKeep = 8) {}

  push(r: PaintRecord) {
    this.undoList.push(r);
    this.used += recordBytes(r);
    for (const x of this.redoList) { this.used -= recordBytes(x); x.free?.(); }
    this.redoList = [];
    this.trim();
  }

  private trim() {
    while (this.used > this.capBytes && this.undoList.length > this.minKeep) {
      const old = this.undoList.shift()!;
      this.used -= recordBytes(old);
      old.free?.();
    }
  }

  takeUndo(): PaintRecord | null {
    const r = this.undoList.pop() || null;
    if (r) this.redoList.push(r);
    return r;
  }

  takeRedo(): PaintRecord | null {
    const r = this.redoList.pop() || null;
    if (r) this.undoList.push(r);
    return r;
  }

  clear() {
    for (const x of [...this.undoList, ...this.redoList]) x.free?.();
    this.undoList = [];
    this.redoList = [];
    this.used = 0;
  }

  get depth() { return this.undoList.length; }
  get redoDepth() { return this.redoList.length; }
  get bytes() { return this.used; }
  get topLabel() { return this.undoList.length ? this.undoList[this.undoList.length - 1].label : ""; }
  get redoLabel() { return this.redoList.length ? this.redoList[this.redoList.length - 1].label : ""; }
}

/**
 * The tiles a stroke touched, from the GPU's reduction of its coverage: one RGBA texel per tile,
 * non-zero where any texel of the tile was painted. Grown by one ring, because the seam fill that
 * follows a stroke reaches a few texels past what the brush covered.
 */
export function dirtyTiles(reduced: Uint8Array, tilesX: number, tilesY: number, ring = 1): number[] {
  const hit = new Uint8Array(tilesX * tilesY);
  for (let i = 0; i < tilesX * tilesY; i++) {
    if (reduced[i * 4] || reduced[i * 4 + 1] || reduced[i * 4 + 2] || reduced[i * 4 + 3]) hit[i] = 1;
  }
  const out = new Set<number>();
  for (let ty = 0; ty < tilesY; ty++) {
    for (let tx = 0; tx < tilesX; tx++) {
      if (!hit[ty * tilesX + tx]) continue;
      for (let dy = -ring; dy <= ring; dy++) {
        for (let dx = -ring; dx <= ring; dx++) {
          const x = tx + dx, y = ty + dy;
          if (x >= 0 && y >= 0 && x < tilesX && y < tilesY) out.add(y * tilesX + x);
        }
      }
    }
  }
  return [...out].sort((a, b) => a - b);
}

/** Tiles merged into horizontal runs, so reading them back is one call per run, not per tile. */
export function tileRuns(tiles: number[], tilesX: number, tile: number, w: number, h: number):
  Array<{ x: number; y: number; w: number; h: number }> {
  const set = new Set(tiles);
  const out: Array<{ x: number; y: number; w: number; h: number }> = [];
  const seen = new Set<number>();
  for (const t of tiles) {
    if (seen.has(t)) continue;
    const ty = Math.floor(t / tilesX);
    let tx = t % tilesX;
    let end = tx;
    while (set.has(ty * tilesX + end + 1) && end + 1 < tilesX) end++;
    for (let k = tx; k <= end; k++) seen.add(ty * tilesX + k);
    const x = tx * tile, y = ty * tile;
    out.push({ x, y, w: Math.min(w, (end + 1) * tile) - x, h: Math.min(h, (ty + 1) * tile) - y });
    tx = end + 1;
  }
  return out;
}

// ------------------------------------------------------------------ keys
export type PaintKeyAction =
  | { kind: "tool"; tool: PaintTool }
  | { kind: "size"; factor: number }
  | { kind: "hardness"; delta: number }
  | { kind: "opacity"; value: number }
  | { kind: "flow"; value: number }
  | { kind: "swap" }
  | { kind: "defaults" }
  | { kind: "mirror" }
  | { kind: "undo" }
  | { kind: "redo" }
  | { kind: "leave" };

/**
 * Photoshop's keys, where this editor has not already spent the letter on something that a
 * painter would miss. B brush, E eraser, S clone stamp, G fill, I eyedropper, X swap the two
 * colours, D black and white, [ and ] size, Shift+[ ] hardness, 1..0 opacity, Shift+1..0 flow.
 * R is smudge and L is blur (Photoshop keeps both behind R); M mirrors; P leaves paint mode.
 */
export function paintKey(e: { key: string; code?: string; shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean }): PaintKeyAction | null {
  const k = e.key;
  const ctrl = !!(e.ctrlKey || e.metaKey);
  if (ctrl) {
    if ((k === "z" || k === "Z") && e.shiftKey) return { kind: "redo" };
    if (k === "z" || k === "Z") return { kind: "undo" };
    if (k === "y" || k === "Y") return { kind: "redo" };
    return null;
  }
  if (e.altKey) return null;
  const digit = /^Digit([0-9])$/.exec(e.code || "");
  if (digit) {
    const d = Number(digit[1]);
    const v = d === 0 ? 1 : d / 10;
    return e.shiftKey ? { kind: "flow", value: v } : { kind: "opacity", value: v };
  }
  if (k === "[" || k === "{") return e.shiftKey || k === "{" ? { kind: "hardness", delta: -0.1 } : { kind: "size", factor: 1 / 1.25 };
  if (k === "]" || k === "}") return e.shiftKey || k === "}" ? { kind: "hardness", delta: 0.1 } : { kind: "size", factor: 1.25 };
  switch (k.toLowerCase()) {
    case "b": return { kind: "tool", tool: "brush" };
    case "e": return { kind: "tool", tool: "eraser" };
    case "r": return { kind: "tool", tool: "smudge" };
    case "l": return { kind: "tool", tool: "blur" };
    case "s": return { kind: "tool", tool: "clone" };
    case "g": return { kind: "tool", tool: "fill" };
    case "i": return { kind: "tool", tool: "picker" };
    case "x": return { kind: "swap" };
    case "d": return { kind: "defaults" };
    case "m": return { kind: "mirror" };
    case "p": return { kind: "leave" };
  }
  return null;
}

// ------------------------------------------------------------------ the file beside the asset
export const PAINT_DOC_VERSION = 1;

export interface PaintLayerDoc {
  id: string;
  name: string;
  visible: boolean;
  opacity: number;
  blend: BlendMode;
  /** The layer as a PNG, base64, without the data: prefix. */
  png: string;
  /** 1: the PNG holds the layer's PREMULTIPLIED bytes exactly as the GPU keeps them. That is what
   *  the Studio writes, because premultiplying and back again in 8 bits is not exact at a soft
   *  edge — a reload moved 20 of 67,295 painted texels by a step. Missing: a straight-alpha PNG
   *  from anywhere else, premultiplied on the way in. */
  pm?: number;
}

/** One dab of a stroke, kept on the SURFACE rather than in the texture: the mesh by its key, the
 *  point and normal in that mesh's own frame, the radius in that frame's units. A stroke kept this
 *  way can be painted again onto a mesh whose texture layout has changed. */
export interface StrokeDab {
  k: string; p: [number, number, number]; n: [number, number, number]; r: number; a: number; g: number; s: number;
  /** The same point and normal in the ASSET's own frame: the second way back when a rebuild
   *  gives the mesh another key (a slider that splits one merged mesh into its parts). */
  w?: [number, number, number]; wn?: [number, number, number]; wr?: number;
  /** The texture point under the dab. While the layout stays the same it names the same bit of
   *  surface whatever a slider did to the shape: how the dab is found again after a rebuild. */
  u?: [number, number];
  /** A dab painted through the screen: the direction it was painted from, in the dab's own surface
   *  frame (x and y along the surface, z along the normal). A redraw draws the same footprint — a
   *  disc seen along that direction, which is an ellipse where the surface was seen at a slant. */
  vt?: [number, number, number];
}

export interface StrokeRecord {
  tool: "brush" | "eraser" | "fill";
  layer: string;
  color: string;
  opacity: number;
  hardness: number;
  tip: TipKind;
  blend: BlendMode;
  dabs: StrokeDab[];
  /** A fill: the whole mesh, or one island of it (by the index of a triangle in that island). The
   *  point it was clicked at, on the mesh (p) and in the asset (w), finds the island again after a
   *  rebuild that renumbered the triangles or gave the mesh another key. */
  fill?: { k: string; tri: number; island: boolean; p?: [number, number, number]; w?: [number, number, number]; u?: [number, number] } | null;
}

export interface PaintTargetDoc {
  key: string;
  name: string;
  size: [number, number];
  /** Paint pixels per base texture pixel: 1, or 2 to paint finer than the texture it covers. */
  scale: number;
  /** The texture layout the pixels were painted on. When it no longer matches, the strokes are
   *  painted again instead of the pixels being laid onto a different layout. */
  uvSig: string;
  /** A texture the Studio made for a part that had none: what it was made from. */
  made: { from: "color" | "vertex"; color: string } | null;
  active: string;
  layers: PaintLayerDoc[];
  strokes: StrokeRecord[];
  /** Strokes that could not be kept for replay (smudge, blur and clone move pixels, not paint). */
  unreplayable: number;
}

/** A layer as the whole asset sees it. Layers belong to the ASSET, not to one texture: "Scars" is
 *  one layer whether the brush crosses the body's texture or the weapon's. Each texture keeps its
 *  own pixels for it (PaintTargetDoc.layers[].png). */
export interface PaintLayerMeta { id: string; name: string; visible: boolean; opacity: number; blend: BlendMode }

export interface PaintDoc {
  version: number;
  asset: string;
  updated: number;
  /** The asset's layer stack, bottom first. Missing in a file that has one texture: the first
   *  target's layers are the stack then. */
  layers?: PaintLayerMeta[];
  targets: PaintTargetDoc[];
}

/** Where the paint lives: beside the edits, as terrain does, so the edit document — which every
 *  undo step deep-copies — never carries megabytes of pixels. */
export function paintDocPath(sidecarPath: string): string {
  return sidecarPath.replace(/\.edits\.json$/i, "") + ".paint.json";
}

const num = (v: any, lo: number, hi: number, d: number) => (typeof v === "number" && isFinite(v) ? clamp(v, lo, hi) : d);
const vec3 = (v: any): [number, number, number] | null =>
  Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number" && isFinite(x)) ? [v[0], v[1], v[2]] : null;
const vec2 = (v: any): [number, number] | null =>
  Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number" && isFinite(x)) ? [v[0], v[1]] : null;

/** Read a paint file, keeping what is sound and dropping what is not. Never throws. */
export function parsePaintDoc(text: string): PaintDoc | null {
  let raw: any;
  try { raw = JSON.parse(text || ""); } catch { return null; }
  if (!raw || typeof raw !== "object" || !Array.isArray(raw.targets)) return null;
  const targets: PaintTargetDoc[] = [];
  for (const t of raw.targets) {
    if (!t || typeof t.key !== "string" || !Array.isArray(t.layers)) continue;
    // A size out of range is a broken target, not one to clamp into shape: a 1 x 10 layer read
    // from a file that said 0 x 10 would paint onto nothing sensible.
    const sw = Array.isArray(t.size) ? Math.round(Number(t.size[0])) : NaN;
    const sh = Array.isArray(t.size) ? Math.round(Number(t.size[1])) : NaN;
    if (!(sw >= 1 && sh >= 1 && sw <= 8192 && sh <= 8192)) continue;
    const size: [number, number] = [sw, sh];
    const layers: PaintLayerDoc[] = [];
    for (const l of t.layers) {
      if (!l || typeof l.id !== "string" || typeof l.png !== "string") continue;
      layers.push({
        id: l.id, name: typeof l.name === "string" ? l.name : "Layer",
        visible: l.visible !== false, opacity: num(l.opacity, 0, 1, 1),
        blend: BLEND_MODES.includes(l.blend) ? l.blend : "normal",
        png: l.png.replace(/^data:image\/png;base64,/, ""),
        ...(l.pm === 1 ? { pm: 1 } : {}),
      });
    }
    const strokes: StrokeRecord[] = [];
    for (const s of Array.isArray(t.strokes) ? t.strokes : []) {
      if (!s || !["brush", "eraser", "fill"].includes(s.tool) || typeof s.layer !== "string") continue;
      const dabs: StrokeDab[] = [];
      for (const d of Array.isArray(s.dabs) ? s.dabs : []) {
        const p = vec3(d?.p), n = vec3(d?.n);
        if (!p || !n || typeof d.k !== "string") continue;
        const dab: StrokeDab = { k: d.k, p, n, r: num(d.r, 0, 1e6, 0), a: num(d.a, 0, 1, 1), g: num(d.g, -1e3, 1e3, 0), s: Math.floor(num(d.s, 0, 1e10, 0)) };
        const w = vec3(d.w), wn = vec3(d.wn);
        if (w && wn) { dab.w = w; dab.wn = wn; if (typeof d.wr === "number" && isFinite(d.wr)) dab.wr = clamp(d.wr, 0, 1e6); }
        const u = vec2(d.u), vt = vec3(d.vt);
        if (u) dab.u = u;
        if (vt) dab.vt = vt;
        dabs.push(dab);
      }
      let fill: StrokeRecord["fill"] = null;
      if (s.fill && typeof s.fill.k === "string") {
        fill = { k: s.fill.k, tri: Math.floor(num(s.fill.tri, -1, 1e9, -1)), island: !!s.fill.island };
        const fp = vec3(s.fill.p), fw = vec3(s.fill.w), fu = vec2(s.fill.u);
        if (fp) fill.p = fp;
        if (fw) fill.w = fw;
        if (fu) fill.u = fu;
      }
      if (!dabs.length && !fill) continue;
      strokes.push({
        tool: s.tool, layer: s.layer, color: rgbToHex(hexToRgb(s.color)), opacity: num(s.opacity, 0, 1, 1),
        hardness: num(s.hardness, 0, 1, 0.8), tip: TIP_KINDS.includes(s.tip) ? s.tip : "round",
        blend: BLEND_MODES.includes(s.blend) ? s.blend : "normal", dabs, fill,
      });
    }
    targets.push({
      key: t.key, name: typeof t.name === "string" ? t.name : t.key, size,
      scale: num(t.scale, 0.25, 4, 1), uvSig: typeof t.uvSig === "string" ? t.uvSig : "",
      made: t.made && (t.made.from === "color" || t.made.from === "vertex")
        ? { from: t.made.from, color: rgbToHex(hexToRgb(t.made.color || "#ffffff")) } : null,
      active: typeof t.active === "string" ? t.active : (layers[0]?.id || ""),
      layers, strokes, unreplayable: Math.floor(num(t.unreplayable, 0, 1e9, 0)),
    });
  }
  const metas: PaintLayerMeta[] = [];
  const seenIds = new Set<string>();
  const src = Array.isArray(raw.layers) ? raw.layers : (targets[0]?.layers || []);
  for (const l of src) {
    if (!l || typeof l.id !== "string" || seenIds.has(l.id)) continue;
    seenIds.add(l.id);
    metas.push({
      id: l.id, name: typeof l.name === "string" ? l.name : "Layer", visible: l.visible !== false,
      opacity: num(l.opacity, 0, 1, 1), blend: BLEND_MODES.includes(l.blend) ? l.blend : "normal",
    });
  }
  return {
    version: num(raw.version, 1, 99, 1), asset: typeof raw.asset === "string" ? raw.asset : "",
    updated: num(raw.updated, 0, 1e12, 0), layers: metas, targets,
  };
}

// ------------------------------------------------------------------ islands (the fill tool)
/**
 * Which UV island each triangle belongs to: triangles that share a corner in the texture are one
 * island. An indexed mesh already shares corners by index — a seam is where the index is split —
 * so the index alone decides. A mesh with no index has three corners per triangle, and corners
 * are joined when they match in position AND in UV, to 1e-5.
 */
export function triangleIslands(index: ArrayLike<number> | null, uv: ArrayLike<number>, pos: ArrayLike<number>, vertexCount: number): Int32Array {
  const triCount = index ? Math.floor(index.length / 3) : Math.floor(vertexCount / 3);
  const parent = new Int32Array(vertexCount);
  for (let i = 0; i < vertexCount; i++) parent[i] = i;
  const find = (x: number): number => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  const unite = (a: number, b: number) => { const ra = find(a), rb = find(b); if (ra !== rb) parent[ra] = rb; };
  const corner = (t: number, k: number) => (index ? index[t * 3 + k] : t * 3 + k);
  if (!index) {
    const q = (v: number) => Math.round(v * 100000);
    const seen = new Map<string, number>();
    for (let v = 0; v < vertexCount; v++) {
      const key = q(pos[v * 3]) + "," + q(pos[v * 3 + 1]) + "," + q(pos[v * 3 + 2]) + "|" + q(uv[v * 2]) + "," + q(uv[v * 2 + 1]);
      const first = seen.get(key);
      if (first === undefined) seen.set(key, v); else unite(v, first);
    }
  }
  for (let t = 0; t < triCount; t++) {
    const a = corner(t, 0), b = corner(t, 1), c = corner(t, 2);
    if (a >= vertexCount || b >= vertexCount || c >= vertexCount) continue;
    unite(a, b); unite(b, c);
  }
  const ids = new Map<number, number>();
  const out = new Int32Array(triCount);
  for (let t = 0; t < triCount; t++) {
    const a = corner(t, 0);
    const r = a < vertexCount ? find(a) : -1;
    let id = ids.get(r);
    if (id === undefined) { id = ids.size; ids.set(r, id); }
    out[t] = id;
  }
  return out;
}

// ------------------------------------------------------------------ PNG, exactly
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes: Uint8Array, start = 0, end = bytes.length, seed = 0xffffffff): number {
  let c = seed >>> 0;
  for (let i = start; i < end; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return c >>> 0;
}

/**
 * RGBA pixels (straight alpha, rows top first) to PNG bytes, with nothing in between that could
 * change a value. A canvas would do it in one call, but a canvas keeps its pixels premultiplied
 * and the faint edge of a soft brush comes back from it in steps; this writes every byte as given.
 * `deflate` is the zlib compressor: the browser's CompressionStream, or node's zlib in the tests.
 */
export async function encodePNG(rgba: Uint8Array, w: number, h: number,
                                deflate?: (raw: Uint8Array) => Promise<Uint8Array>): Promise<Uint8Array> {
  const raw = new Uint8Array((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 4 + 1)] = 0;                        // filter: none
    raw.set(rgba.subarray(y * w * 4, (y + 1) * w * 4), y * (w * 4 + 1) + 1);
  }
  const z = deflate ? await deflate(raw) : await zlibDeflate(raw);
  const chunk = (type: string, data: Uint8Array) => {
    const out = new Uint8Array(12 + data.length);
    const dv = new DataView(out.buffer);
    dv.setUint32(0, data.length);
    for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
    out.set(data, 8);
    dv.setUint32(8 + data.length, crc32(out, 4, 8 + data.length) ^ 0xffffffff);
    return out;
  };
  const ihdr = new Uint8Array(13);
  const hv = new DataView(ihdr.buffer);
  hv.setUint32(0, w); hv.setUint32(4, h);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;   // 8-bit RGBA, no interlace
  const parts = [new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", z), chunk("IEND", new Uint8Array(0))];
  const total = parts.reduce((n, p) => n + p.length, 0);
  const png = new Uint8Array(total);
  let o = 0;
  for (const p of parts) { png.set(p, o); o += p.length; }
  return png;
}

async function zlibDeflate(raw: Uint8Array): Promise<Uint8Array> {
  const CS: any = (globalThis as any).CompressionStream;
  if (!CS) throw new Error("no CompressionStream in this browser");
  const stream = new Blob([raw as unknown as BlobPart]).stream().pipeThrough(new CS("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export function bytesToBase64(bytes: Uint8Array): string {
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CH)));
  return btoa(s);
}

export function base64ToBytes(b64: string): Uint8Array {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

/** Straight alpha to premultiplied and back, on RGBA8 bytes. */
export function premultiply(px: Uint8Array): Uint8Array {
  const out = new Uint8Array(px.length);
  for (let i = 0; i < px.length; i += 4) {
    const a = px[i + 3];
    out[i] = Math.round(px[i] * a / 255); out[i + 1] = Math.round(px[i + 1] * a / 255);
    out[i + 2] = Math.round(px[i + 2] * a / 255); out[i + 3] = a;
  }
  return out;
}

export function unpremultiply(px: Uint8Array): Uint8Array {
  const out = new Uint8Array(px.length);
  for (let i = 0; i < px.length; i += 4) {
    const a = px[i + 3];
    if (a === 0) continue;
    out[i] = Math.min(255, Math.round(px[i] * 255 / a)); out[i + 1] = Math.min(255, Math.round(px[i + 1] * 255 / a));
    out[i + 2] = Math.min(255, Math.round(px[i + 2] * 255 / a)); out[i + 3] = a;
  }
  return out;
}

/**
 * A texture layout as one short string: the UVs (to 1e-5) and the index order. Positions are left
 * out on purpose: a slider that moves a part's surface but keeps its layout keeps its paint on the
 * same part, and that is the case where the pixels stay exactly where they were.
 */
export function uvSignature(uv: ArrayLike<number> | null, index: ArrayLike<number> | null, extra = ""): string {
  let h = 0x811c9dc5 >>> 0;
  const mix = (v: number) => {
    h ^= v & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
    h ^= (v >>> 8) & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
    h ^= (v >>> 16) & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
    h ^= (v >>> 24) & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
  };
  const n = uv ? uv.length : 0;
  mix(n);
  for (let i = 0; i < n; i++) mix(Math.round((uv as ArrayLike<number>)[i] * 100000) | 0);
  const m = index ? index.length : 0;
  mix(m);
  for (let i = 0; i < m; i++) mix((index as ArrayLike<number>)[i] | 0);
  for (let i = 0; i < extra.length; i++) mix(extra.charCodeAt(i));
  return h.toString(16).padStart(8, "0") + ":" + n + ":" + m;
}

/**
 * The texture triangles of a geometry, one number each, into a set: the three UV corners rounded
 * to 1e-5 and put in order. The number does not depend on what the mesh is called, how the parts
 * are grouped into meshes, or the order of the triangles — so merging parts into one mesh, or
 * splitting them again, is the same layout. `start`/`count` pick one material group (in indices,
 * or in vertices for a geometry with no index).
 */
export function addUvTriangles(into: Set<number>, uv: ArrayLike<number>, index: ArrayLike<number> | null,
  vertexCount: number, start = 0, count = Infinity): void {
  const total = index ? index.length : vertexCount;
  const end = Math.min(total, start + count);
  const c = [0, 0, 0, 0, 0, 0];
  for (let i = Math.max(0, start); i + 3 <= end; i += 3) {
    let bad = false;
    for (let k = 0; k < 3; k++) {
      const v = index ? index[i + k] : i + k;
      if (v >= vertexCount) { bad = true; break; }
      c[k * 2] = Math.round(uv[v * 2] * 100000) | 0;
      c[k * 2 + 1] = Math.round(uv[v * 2 + 1] * 100000) | 0;
    }
    if (bad) continue;
    // The corners in order (by u, then v): the same triangle whichever corner comes first.
    for (let a = 0; a < 2; a++) for (let q = 0; q < 2 - a; q++) {
      const x = q * 2, y = x + 2;
      if (c[x] > c[y] || (c[x] === c[y] && c[x + 1] > c[y + 1])) {
        const t0 = c[x], t1 = c[x + 1];
        c[x] = c[y]; c[x + 1] = c[y + 1]; c[y] = t0; c[y + 1] = t1;
      }
    }
    let h = 0x811c9dc5 >>> 0;
    for (let k = 0; k < 6; k++) {
      const v = c[k];
      h ^= v & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
      h ^= (v >>> 8) & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
      h ^= (v >>> 16) & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
      h ^= (v >>> 24) & 0xff; h = Math.imul(h, 0x01000193) >>> 0;
    }
    into.add(h);
  }
}

/** One string for a set of texture triangles: equal sets, equal strings, in any order. */
export function uvTriangleSetSignature(set: Set<number>): string {
  let a = 0, b = 0, c = 0;
  for (const h of set) {
    a = (a + h) >>> 0;
    b = (b ^ Math.imul(h, 0x9e3779b1)) >>> 0;
    const m = Math.imul(h ^ (h >>> 15), 0x85ebca6b) >>> 0;
    c = (c + (m ^ (m >>> 13))) >>> 0;
  }
  return "t1:" + set.size + ":" + a.toString(36) + ":" + b.toString(36) + ":" + c.toString(36);
}

// ------------------------------------------------------------------ a point on a rebuilt surface
//
// A stroke is kept as points on the surface. When a slider rebuilds the mesh, each point is found
// again: by its texture point while the layout is the same (it names the same bit of surface,
// whatever the slider did to the shape), and by the nearest surface point when the layout changed
// (a texture point means nothing on another layout). Two grids keep both from looping over every
// triangle for every point.

/** The vertex at corner k of triangle tri. */
export function triVertex(index: ArrayLike<number> | null, tri: number, k: number): number {
  return index ? index[tri * 3 + k] : tri * 3 + k;
}

/** A per-vertex value (`size` numbers) at a point of a triangle, from its barycentric weights. */
export function lerpTri(arr: ArrayLike<number>, size: number, index: ArrayLike<number> | null, tri: number, b: ArrayLike<number>): number[] {
  const out = new Array<number>(size).fill(0);
  for (let k = 0; k < 3; k++) {
    const v = triVertex(index, tri, k);
    for (let q = 0; q < size; q++) out[q] += arr[v * size + q] * b[k];
  }
  return out;
}

/** The point of triangle abc nearest p, with its barycentric weights (Ericson, Real-Time Collision
 *  Detection 5.1.5). A triangle with no area gives its nearest corner or edge point. */
export function closestOnTriangle(p: ArrayLike<number>, a: ArrayLike<number>, b: ArrayLike<number>, c: ArrayLike<number>):
  { point: [number, number, number]; b: [number, number, number] } {
  const at = (u: number, v: number, w: number) => ({
    point: [a[0] * u + b[0] * v + c[0] * w, a[1] * u + b[1] * v + c[1] * w, a[2] * u + b[2] * v + c[2] * w] as [number, number, number],
    b: [u, v, w] as [number, number, number],
  });
  const abx = b[0] - a[0], aby = b[1] - a[1], abz = b[2] - a[2];
  const acx = c[0] - a[0], acy = c[1] - a[1], acz = c[2] - a[2];
  // No area (two corners on one point, or all three on a line): the nearest point of its three
  // edges. The regions below assume a real triangle and would give a wrong corner.
  const nx = aby * acz - abz * acy, ny = abz * acx - abx * acz, nz = abx * acy - aby * acx;
  const area2 = nx * nx + ny * ny + nz * nz;
  if (area2 <= 1e-12 * (abx * abx + aby * aby + abz * abz) * (acx * acx + acy * acy + acz * acz) || area2 < 1e-30) {
    const seg = (P: ArrayLike<number>, Q: ArrayLike<number>) => {
      const qx = Q[0] - P[0], qy = Q[1] - P[1], qz = Q[2] - P[2];
      const len2 = qx * qx + qy * qy + qz * qz;
      const s = len2 > 1e-30 ? clamp(((p[0] - P[0]) * qx + (p[1] - P[1]) * qy + (p[2] - P[2]) * qz) / len2, 0, 1) : 0;
      const x = P[0] + qx * s, y = P[1] + qy * s, z = P[2] + qz * s;
      return { s, d: (x - p[0]) ** 2 + (y - p[1]) ** 2 + (z - p[2]) ** 2 };
    };
    const e0 = seg(a, b), e1 = seg(b, c), e2 = seg(c, a);
    if (e0.d <= e1.d && e0.d <= e2.d) return at(1 - e0.s, e0.s, 0);
    if (e1.d <= e2.d) return at(0, 1 - e1.s, e1.s);
    return at(e2.s, 0, 1 - e2.s);
  }
  const apx = p[0] - a[0], apy = p[1] - a[1], apz = p[2] - a[2];
  const d1 = abx * apx + aby * apy + abz * apz, d2 = acx * apx + acy * apy + acz * apz;
  if (d1 <= 0 && d2 <= 0) return at(1, 0, 0);
  const bpx = p[0] - b[0], bpy = p[1] - b[1], bpz = p[2] - b[2];
  const d3 = abx * bpx + aby * bpy + abz * bpz, d4 = acx * bpx + acy * bpy + acz * bpz;
  if (d3 >= 0 && d4 <= d3) return at(0, 1, 0);
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) { const den = d1 - d3; const v = den > 1e-30 ? d1 / den : 0; return at(1 - v, v, 0); }
  const cpx = p[0] - c[0], cpy = p[1] - c[1], cpz = p[2] - c[2];
  const d5 = abx * cpx + aby * cpy + abz * cpz, d6 = acx * cpx + acy * cpy + acz * cpz;
  if (d6 >= 0 && d5 <= d6) return at(0, 0, 1);
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) { const den = d2 - d6; const w = den > 1e-30 ? d2 / den : 0; return at(1 - w, 0, w); }
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) {
    const den = (d4 - d3) + (d5 - d6);
    const w = den > 1e-30 ? (d4 - d3) / den : 0;
    return at(0, 1 - w, w);
  }
  const sum = va + vb + vc;
  if (!(Math.abs(sum) > 1e-30)) {
    const da = apx * apx + apy * apy + apz * apz, db = bpx * bpx + bpy * bpy + bpz * bpz, dc = cpx * cpx + cpy * cpy + cpz * cpz;
    return da <= db && da <= dc ? at(1, 0, 0) : db <= dc ? at(0, 1, 0) : at(0, 0, 1);
  }
  const v = vb / sum, w = vc / sum;
  return at(1 - v - w, v, w);
}

/** A mesh's triangles in a grid of boxes, for "which surface point is nearest here". */
export interface PosGrid {
  n: [number, number, number];
  min: [number, number, number];
  cs: [number, number, number];
  cells: Map<number, number[]>;
  pos: ArrayLike<number>;
  index: ArrayLike<number> | null;
  vertexCount: number;
  tris: number;
}

export function buildPosGrid(pos: ArrayLike<number>, index: ArrayLike<number> | null, vertexCount: number): PosGrid {
  const tris = index ? Math.floor(index.length / 3) : Math.floor(vertexCount / 3);
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let v = 0; v < vertexCount; v++) {
    for (let k = 0; k < 3; k++) {
      const x = pos[v * 3 + k];
      if (x < lo[k]) lo[k] = x;
      if (x > hi[k]) hi[k] = x;
    }
  }
  if (!(lo[0] <= hi[0] && lo[1] <= hi[1] && lo[2] <= hi[2])) { lo.fill(0); hi.fill(0); }
  const ext = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  const big = Math.max(ext[0], ext[1], ext[2], 1e-9);
  // About two triangles a box; a flat or thin mesh is measured by its longest side.
  const vol = Math.max(ext[0], big * 1e-3) * Math.max(ext[1], big * 1e-3) * Math.max(ext[2], big * 1e-3);
  const h = Math.max(Math.cbrt(vol / clamp(tris / 2, 1, 200000)), big / 64);
  const n: [number, number, number] = [1, 1, 1], cs: [number, number, number] = [h, h, h];
  for (let k = 0; k < 3; k++) {
    n[k] = clamp(Math.ceil(ext[k] / h), 1, 64);
    cs[k] = ext[k] > 0 ? ext[k] / n[k] : h;
  }
  const cells = new Map<number, number[]>();
  const cellOf = (k: number, x: number) => clamp(Math.floor((x - lo[k]) / cs[k]), 0, n[k] - 1);
  for (let t = 0; t < tris; t++) {
    const bl = [Infinity, Infinity, Infinity], bh = [-Infinity, -Infinity, -Infinity];
    let bad = false;
    for (let q = 0; q < 3; q++) {
      const v = triVertex(index, t, q);
      if (v >= vertexCount) { bad = true; break; }
      for (let k = 0; k < 3; k++) {
        const x = pos[v * 3 + k];
        if (x < bl[k]) bl[k] = x;
        if (x > bh[k]) bh[k] = x;
      }
    }
    if (bad) continue;
    const x0 = cellOf(0, bl[0]), x1 = cellOf(0, bh[0]), y0 = cellOf(1, bl[1]), y1 = cellOf(1, bh[1]), z0 = cellOf(2, bl[2]), z1 = cellOf(2, bh[2]);
    for (let x = x0; x <= x1; x++) {
      for (let y = y0; y <= y1; y++) {
        for (let z = z0; z <= z1; z++) {
          const key = (x * n[1] + y) * n[2] + z;
          let list = cells.get(key);
          if (!list) { list = []; cells.set(key, list); }
          list.push(t);
        }
      }
    }
  }
  return { n, min: [lo[0], lo[1], lo[2]], cs, cells, pos, index, vertexCount, tris };
}

/** The surface point of the grid's mesh nearest p, no further than maxDist; null when there is none. */
export function nearestOnGrid(g: PosGrid, p: ArrayLike<number>, maxDist = Infinity):
  { tri: number; point: [number, number, number]; b: [number, number, number]; dist: number } | null {
  if (!g.tris) return null;
  let out2 = 0;
  for (let k = 0; k < 3; k++) {
    const lo = g.min[k], hi = g.min[k] + g.n[k] * g.cs[k];
    const d = p[k] < lo ? lo - p[k] : p[k] > hi ? p[k] - hi : 0;
    out2 += d * d;
  }
  if (Math.sqrt(out2) > maxDist) return null;
  const c = [0, 1, 2].map((k) => clamp(Math.floor((p[k] - g.min[k]) / g.cs[k]), 0, g.n[k] - 1));
  // A box k rings out is at least (k - 1) boxes away along an axis that has more than one box.
  let minCs = Infinity;
  for (let k = 0; k < 3; k++) if (g.n[k] > 1) minCs = Math.min(minCs, g.cs[k]);
  const rmax = Math.max(g.n[0], g.n[1], g.n[2]);
  const A = [0, 0, 0], B = [0, 0, 0], C = [0, 0, 0];
  let best: { tri: number; point: [number, number, number]; b: [number, number, number]; dist: number } | null = null;
  let bestD = maxDist;
  const seen = new Set<number>();
  for (let r = 0; r <= rmax; r++) {
    if (r > 0 && !isFinite(minCs)) break;
    if (r > 0 && (r - 1) * minCs > bestD) break;
    for (let dx = -r; dx <= r; dx++) {
      const x = c[0] + dx;
      if (x < 0 || x >= g.n[0]) continue;
      for (let dy = -r; dy <= r; dy++) {
        const y = c[1] + dy;
        if (y < 0 || y >= g.n[1]) continue;
        const rim = Math.abs(dx) === r || Math.abs(dy) === r;
        for (let dz = -r; dz <= r; dz += rim || r === 0 ? 1 : 2 * r) {
          const z = c[2] + dz;
          if (z < 0 || z >= g.n[2]) continue;
          const list = g.cells.get((x * g.n[1] + y) * g.n[2] + z);
          if (!list) continue;
          for (const t of list) {
            if (seen.has(t)) continue;
            seen.add(t);
            for (let k = 0; k < 3; k++) {
              A[k] = g.pos[triVertex(g.index, t, 0) * 3 + k];
              B[k] = g.pos[triVertex(g.index, t, 1) * 3 + k];
              C[k] = g.pos[triVertex(g.index, t, 2) * 3 + k];
            }
            const q = closestOnTriangle(p, A, B, C);
            const d = Math.hypot(q.point[0] - p[0], q.point[1] - p[1], q.point[2] - p[2]);
            if (d <= bestD) { bestD = d; best = { tri: t, point: q.point, b: q.b, dist: d }; }
          }
        }
      }
    }
  }
  return best;
}

/** A mesh's triangles in a grid over texture space, for "which triangle holds this texture point". */
export interface UvGrid {
  n: number;
  min: [number, number];
  cs: [number, number];
  cells: Map<number, number[]>;
  /** Six numbers a triangle: its corners in texture space (NaN for a triangle with a bad index). */
  tuv: Float64Array;
  tris: number;
}

/** `xf` is a texture transform as three's Matrix3.elements (column-major), or null for none. */
export function buildUvGrid(uv: ArrayLike<number>, index: ArrayLike<number> | null, vertexCount: number, xf: ArrayLike<number> | null = null): UvGrid {
  const tris = index ? Math.floor(index.length / 3) : Math.floor(vertexCount / 3);
  const tuv = new Float64Array(tris * 6).fill(NaN);
  let u0 = Infinity, v0 = Infinity, u1 = -Infinity, v1 = -Infinity;
  for (let t = 0; t < tris; t++) {
    let bad = false;
    for (let k = 0; k < 3; k++) if (triVertex(index, t, k) >= vertexCount) bad = true;
    if (bad) continue;
    for (let k = 0; k < 3; k++) {
      const v = triVertex(index, t, k);
      let x = uv[v * 2], y = uv[v * 2 + 1];
      if (xf) { const X = xf[0] * x + xf[3] * y + xf[6], Y = xf[1] * x + xf[4] * y + xf[7]; x = X; y = Y; }
      tuv[t * 6 + k * 2] = x;
      tuv[t * 6 + k * 2 + 1] = y;
      if (x < u0) u0 = x; if (x > u1) u1 = x;
      if (y < v0) v0 = y; if (y > v1) v1 = y;
    }
  }
  if (!(u0 <= u1 && v0 <= v1)) { u0 = 0; v0 = 0; u1 = 1; v1 = 1; }
  const n = clamp(Math.ceil(Math.sqrt(tris / 2)), 1, 256);
  const cs: [number, number] = [Math.max((u1 - u0) / n, 1e-12), Math.max((v1 - v0) / n, 1e-12)];
  const cells = new Map<number, number[]>();
  for (let t = 0; t < tris; t++) {
    const o = t * 6;
    if (isNaN(tuv[o])) continue;
    const bu0 = Math.min(tuv[o], tuv[o + 2], tuv[o + 4]), bu1 = Math.max(tuv[o], tuv[o + 2], tuv[o + 4]);
    const bv0 = Math.min(tuv[o + 1], tuv[o + 3], tuv[o + 5]), bv1 = Math.max(tuv[o + 1], tuv[o + 3], tuv[o + 5]);
    const x0 = clamp(Math.floor((bu0 - u0) / cs[0]), 0, n - 1), x1 = clamp(Math.floor((bu1 - u0) / cs[0]), 0, n - 1);
    const y0 = clamp(Math.floor((bv0 - v0) / cs[1]), 0, n - 1), y1 = clamp(Math.floor((bv1 - v0) / cs[1]), 0, n - 1);
    for (let x = x0; x <= x1; x++) {
      for (let y = y0; y <= y1; y++) {
        const key = x * n + y;
        let list = cells.get(key);
        if (!list) { list = []; cells.set(key, list); }
        list.push(t);
      }
    }
  }
  return { n, min: [u0, v0], cs, cells, tuv, tris };
}

/**
 * Every triangle that holds the texture point (u, v), nearest first, with its barycentric weights.
 * A point within `tol` of a triangle counts: a texture point is kept rounded to 1e-5, so one that
 * was on an edge may now sit a hair outside it.
 */
export function uvLocate(g: UvGrid, u: number, v: number, tol = 3e-5): Array<{ tri: number; b: [number, number, number]; d: number }> {
  const out: Array<{ tri: number; b: [number, number, number]; d: number }> = [];
  const x = Math.floor((u - g.min[0]) / g.cs[0]), y = Math.floor((v - g.min[1]) / g.cs[1]);
  const seen = new Set<number>();
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      const cx = x + dx, cy = y + dy;
      if (cx < 0 || cy < 0 || cx >= g.n || cy >= g.n) continue;
      if (dx || dy) {
        // A neighbouring box only when the point is within tol of it.
        const bx0 = g.min[0] + cx * g.cs[0], by0 = g.min[1] + cy * g.cs[1];
        const ex = u < bx0 ? bx0 - u : u > bx0 + g.cs[0] ? u - bx0 - g.cs[0] : 0;
        const ey = v < by0 ? by0 - v : v > by0 + g.cs[1] ? v - by0 - g.cs[1] : 0;
        if (Math.hypot(ex, ey) > tol) continue;
      }
      const list = g.cells.get(cx * g.n + cy);
      if (!list) continue;
      for (const t of list) {
        if (seen.has(t)) continue;
        seen.add(t);
        const o = t * 6;
        const x0 = g.tuv[o], y0 = g.tuv[o + 1], x1 = g.tuv[o + 2], y1 = g.tuv[o + 3], x2 = g.tuv[o + 4], y2 = g.tuv[o + 5];
        const den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
        if (Math.abs(den) > 1e-20) {
          const b0 = ((y1 - y2) * (u - x2) + (x2 - x1) * (v - y2)) / den;
          const b1 = ((y2 - y0) * (u - x2) + (x0 - x2) * (v - y2)) / den;
          const b2 = 1 - b0 - b1;
          if (b0 >= 0 && b1 >= 0 && b2 >= 0) { out.push({ tri: t, b: [b0, b1, b2], d: 0 }); continue; }
        }
        const q = closestOnTriangle([u, v, 0], [x0, y0, 0], [x1, y1, 0], [x2, y2, 0]);
        const d = Math.hypot(q.point[0] - u, q.point[1] - v);
        if (d <= tol) out.push({ tri: t, b: q.b, d });
      }
    }
  }
  out.sort((a, b) => a.d - b.d);
  return out;
}

/** Strokes kept for replay are bounded: past this many dabs the oldest strokes are dropped, and
 *  the count of dropped ones is kept so the panel can say how many would not follow a change. */
export const MAX_REPLAY_DABS = 60000;

export function capStrokes(strokes: StrokeRecord[], max = MAX_REPLAY_DABS): { kept: StrokeRecord[]; dropped: number } {
  let total = 0;
  for (const s of strokes) total += Math.max(1, s.dabs.length);
  let i = 0;
  while (total > max && i < strokes.length) { total -= Math.max(1, strokes[i].dabs.length); i++; }
  return { kept: strokes.slice(i), dropped: i };
}

/** A new id for a layer: short, unique in the document, stable once made. */
export function layerId(existing: string[]): string {
  for (let i = 1; i < 10000; i++) { const id = "L" + i; if (!existing.includes(id)) return id; }
  return "L" + Date.now();
}
