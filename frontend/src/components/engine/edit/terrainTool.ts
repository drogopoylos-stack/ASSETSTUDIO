// TERRAIN, UNDER THE HAND: what a pointer does to the ground, and what one undo takes back.
//
// Split out of the React file for one reason that is worth the extra module: a held stroke is
// hundreds of `applyBrush` calls and exactly ONE undo, and the merging that makes that true is
// the part most likely to be subtly wrong. Here it runs in node with no browser, no three and no
// canvas, so `npm run test:terraintool` can hold a button down for a simulated second and check
// that the ground comes back exactly.
//
// Nothing here knows about the DOM. The editor hands in a world position and a timestamp; the
// tool hands back the sample rectangle that changed, so the viewport can rebuild that patch of
// mesh instead of the whole field.
//
// WHAT A FRAME OF A HELD STROKE COSTS, measured on a 513-sample field (263,169 samples) with a
// 24-unit brush, by `npm run test:terraintool`:
//
//     brush step            0.21 - 0.25 ms
//     region mesh rebuild   0.24 - 0.27 ms   (about 2,700 samples touched)
//     ------------------------------------
//     one frame             0.46 - 0.52 ms   — 3% of a 60 fps budget
//
//     closing the stroke    0.33 ms          (once, on the button coming up)
//     the whole field       18 - 20 ms       — 72 to 82x the region, and over budget alone
//
// That last line is why `applyBrush` returns a rectangle and why `terrainMesh` takes a region.
// Without them the tool is not slow, it is unusable.

import {
  applyBrush as realApply, capture as realCapture, undoPatch as realUndo, heightAt as realHeight,
  type Brush, type BrushKind, type Patch, type ScatterItem, type TerrainData, type TerrainLayer,
} from "./terrain";
import type { PlacedRef } from "./kit";

/** A rectangle in SAMPLE space — the same coordinates a `Patch` uses, and the same ones
 *  `terrainMesh({ region })` takes, so a stroke's answer goes straight to the renderer. */
export interface Rect { x0: number; z0: number; w: number; h: number }

/**
 * The slice of terrain.ts this file uses, as a parameter rather than a hard import.
 *
 * Not indirection for its own sake. Two things needed it: the history has to be testable with no
 * engine at all (it is the piece that loses a person's work when it is wrong), and the same
 * stack will one day drive a field that lives in the running game's page, where the terrain
 * module loaded THERE is a different module object from the one loaded here — module identity is
 * the URL, and a second copy is a second set of functions.
 */
export interface TerrainEngine {
  applyBrush(t: TerrainData, b: Brush, x: number, z: number, dt: number): Patch;
  capture(t: TerrainData, x0: number, z0: number, w: number, h: number): Patch;
  undoPatch(t: TerrainData, p: Patch): void;
  heightAt(t: TerrainData, x: number, z: number): number;
}

export const REAL_ENGINE: TerrainEngine = {
  applyBrush: realApply, capture: realCapture, undoPatch: realUndo, heightAt: realHeight,
};

// ------------------------------------------------------------------ the nine brushes

export interface BrushDef {
  kind: BrushKind;
  /** The single key that arms it. See the note under BRUSHES for how these were chosen. */
  key: string;
  label: string;
  /** What it does, in the tooltip. */
  hint: string;
  /** Needs a layer chosen in the palette before it can do anything. */
  wantsLayer?: boolean;
  /** Needs an asset chosen in the scatter palette. */
  wantsAsset?: boolean;
  /** Moves the ground rather than painting it — these are the ones `flatten`'s target reads. */
  sculpts?: boolean;
}

/**
 * THE KEYS. The rule is the trade's: one letter, the brush's own first letter wherever that
 * letter is not already doing something in this editor.
 *
 * Seven land on their own initial. R and S look taken — they are the modal rotate and scale —
 * but both of those need a selection and Terrain mode never has one, so they are free HERE and
 * nowhere else. Taking them was still a decision: the finger reaching for raise reaches for R,
 * and raise/lower/smooth are used more than the other six together.
 *
 * The two that could not have their initial:
 *   scatter  S went to smooth, so sCatter.
 *   erase    E went to erode, so X — which is already what removes things everywhere else in
 *            this editor, and reads better than any second letter of "erase" would have.
 *
 * Z, A, H, I and G are left alone on purpose: shading, select-all, hide, keyframe and move all
 * still do something useful while you are sculpting.
 */
export const BRUSHES: BrushDef[] = [
  { kind: "raise", key: "r", label: "Raise", sculpts: true,
    hint: "Pull the ground up under the cursor." },
  { kind: "lower", key: "l", label: "Lower", sculpts: true,
    hint: "Push the ground down under the cursor." },
  { kind: "smooth", key: "s", label: "Smooth", sculpts: true,
    hint: "Average the ground with its neighbours — the brush that rescues everything the others overdid." },
  { kind: "flatten", key: "f", label: "Flatten", sculpts: true,
    hint: "Level to one height. Takes the height under the cursor when the stroke starts, so a plateau is one drag." },
  { kind: "noise", key: "n", label: "Noise", sculpts: true,
    hint: "Break a surface up. A hillside that came out of raise and smooth reads as plastic until this has been over it." },
  { kind: "erode", key: "e", label: "Erode", sculpts: true,
    hint: "Move material downhill, so ridges sharpen and valleys silt up. The slowest brush and the only one that invents drainage." },
  { kind: "paint", key: "p", label: "Paint", wantsLayer: true,
    hint: "Paint the chosen layer onto the surface." },
  { kind: "scatter", key: "c", label: "Scatter", wantsAsset: true,
    hint: "Place the chosen asset, as many as the density asks for, standing on the ground." },
  { kind: "erase", key: "x", label: "Erase", hint: "Take scattered items back off the ground." },
];

export const BRUSH_BY_KIND: Record<string, BrushDef> =
  Object.fromEntries(BRUSHES.map((b) => [b.kind, b]));

/** The brush a fresh Terrain mode is holding. Radius 8 over a 256-unit field is about a
 *  three-hundredth of it — big enough to see move, small enough to aim. */
export const DEFAULT_BRUSH: Brush = {
  kind: "raise", radius: 8, strength: 0.5, falloff: 0.7, layer: 0, density: 0.05, jitter: 0.12,
};

/** Four layers, because four is what one splat byte each buys and what the shader blends.
 *
 *  These are terrain.ts's OWN palette, name for name and hex for hex. It keeps its copy private
 *  and it is right to; but a second set of names here would mean the panel said "Grass" and the
 *  emitted builder said "ground", and the person who renamed one would find the other unchanged. */
export const DEFAULT_LAYERS: TerrainLayer[] = [
  { name: "ground", colour: "#7d8a6a", tiling: 8 },
  { name: "dirt", colour: "#8f7a5c", tiling: 8 },
  { name: "rock", colour: "#8d8f93", tiling: 12 },
  { name: "sand", colour: "#d9d2bd", tiling: 6 },
];

/**
 * Four layers, always.
 *
 * `makeTerrain` ships ONE, and the engine grows the array on demand as a brush paints into a
 * higher slot. That is right for the engine and wrong for a palette: the whole point of the
 * palette is that you can SEE the four and click one, and a slot that only exists after you have
 * already painted into it cannot be clicked first.
 */
export function ensureLayers(t: TerrainData): TerrainData {
  if (!Array.isArray(t.layers)) t.layers = [];
  while (t.layers.length < 4) t.layers.push({ ...DEFAULT_LAYERS[t.layers.length] });
  return t;
}

/** `[` and `]` step by a RATIO, not by a fixed amount: the same two keys have to feel right at
 *  radius 0.5 and at radius 200, and +1 unit a press does not. Twelve presses doubles it. */
export const RADIUS_STEP = 1.22;
export const RADIUS_MIN = 0.25;
export const RADIUS_MAX = 256;

/** How far apart brush stamps are laid along a drag, as a share of the radius. Below about a
 *  third the stamps pile up and a fast flick digs a trench; above it the stroke is dotted. */
const SPACING = 0.28;

/** A pointer move that arrives after a stall — a tab switch, a rebuild — must not deposit the
 *  whole gap in one stamp. 100 ms is the cap: a stroke that pauses resumes, it does not gouge. */
const MAX_DT = 0.1;

/** Stamps per pointer move. A 4000 px flick at radius 0.25 would otherwise ask for 57,000. */
const MAX_STEPS = 64;

// ------------------------------------------------------------------ the scatter palette

/**
 * One thing the scatter brush can place, taken from the project's OWN asset index.
 *
 * `ScatterItem.asset` in the contract is a bare string, so the field alone knows that a tree
 * stands at (12, 0, 40) and not WHICH tree. This is the other half: the string is the Library's
 * own id, and this carries the recipe that builds it — the same `PlacedRef` the Add palette
 * hands to `placeRef`, so a scattered tree is built by the game's own builder and not by a
 * lookalike the editor invented.
 */
export interface ScatterAsset {
  id: string;
  name: string;
  ref: PlacedRef;
}

/** A row of the Library asset index, as much of it as scattering needs. Kept structural rather
 *  than importing `LibraryAsset`, so this module stays free of the API types and bundles into a
 *  node test. */
export interface AssetRow {
  id: string;
  type: string;
  name: string;
  file: string;
  export?: string;
  table?: string;
  key?: string;
  index?: number;
  root?: string;
  deps?: string[];
  model?: string;
}

/**
 * A Library row turned into something placeable.
 *
 * Byte for byte the mapping the Add palette already uses, on purpose: a tree scattered by the
 * brush and a tree dropped by hand must be the same object, or the level has two kinds of tree
 * in it that only differ by how they got there.
 */
export function scatterAssetOf(a: AssetRow): ScatterAsset {
  const ref: PlacedRef = a.model
    ? { kind: "model", url: a.model }
    : a.type === "spec"
      ? { kind: "code", spec: true, file: a.file, root: a.root || "", table: a.table || "",
          key: a.key || "", index: typeof a.index === "number" ? a.index : -1,
          export: a.export, deps: a.deps || [] }
      : a.type === "code"
        ? { kind: "code", file: a.file, root: a.root || "", export: a.export, deps: a.deps || [] }
        : a.type === "model"
          ? { kind: "model", url: a.file }
          : { kind: "image", url: a.file };
  return { id: a.id, name: a.name || a.id, ref };
}

// ------------------------------------------------------------------ the undo stack

/** One entry of the stack: enough to put the ground back exactly as it was before a stroke. */
interface Step {
  patch: Patch;
  /**
   * The WHOLE scatter list as it stood, for the two brushes that change it.
   *
   * An index-by-index inverse was tried first and is not safe: one stroke removes item 5, the
   * item that was 6 slides into 5, the same stroke removes that one too, and both are recorded
   * as "was at 5". Putting them back in order gives the right count and the wrong list. The list
   * itself cannot be wrong, and at roughly 60 bytes an item a five-thousand-tree field costs
   * 300 KB a step — only for the strokes that actually touched scatter.
   */
  scatter?: ScatterItem[];
  label: string;
}

/** The rectangle a stroke has touched so far, with the FIRST value seen at every sample. */
class Merged {
  x0 = 0; z0 = 0; w = 0; h = 0;
  height: Float32Array | null = null;
  splat: Uint8Array | null = null;
  /** Bit 1: this sample's height is recorded. Bit 2: its splat is. */
  seen: Uint8Array = new Uint8Array(0);
  scatterTouched = false;
  any = false;

  private grow(x0: number, z0: number, w: number, h: number) {
    if (this.w === 0) {
      this.x0 = x0; this.z0 = z0; this.w = w; this.h = h;
      this.seen = new Uint8Array(w * h);
      return;
    }
    const nx0 = Math.min(this.x0, x0), nz0 = Math.min(this.z0, z0);
    const nx1 = Math.max(this.x0 + this.w, x0 + w), nz1 = Math.max(this.z0 + this.h, z0 + h);
    const nw = nx1 - nx0, nh = nz1 - nz0;
    if (nw === this.w && nh === this.h && nx0 === this.x0 && nz0 === this.z0) return;
    const seen = new Uint8Array(nw * nh);
    const height = this.height ? new Float32Array(nw * nh) : null;
    const splat = this.splat ? new Uint8Array(nw * nh * 4) : null;
    for (let j = 0; j < this.h; j++) {
      const src = j * this.w;
      const dst = (this.z0 - nz0 + j) * nw + (this.x0 - nx0);
      seen.set(this.seen.subarray(src, src + this.w), dst);
      if (height && this.height) height.set(this.height.subarray(src, src + this.w), dst);
      if (splat && this.splat) splat.set(this.splat.subarray(src * 4, (src + this.w) * 4), dst * 4);
    }
    this.x0 = nx0; this.z0 = nz0; this.w = nw; this.h = nh;
    this.seen = seen; this.height = height; this.splat = splat;
  }

  /** Fold one `applyBrush` answer in. Earliest wins: a sample already recorded is left alone,
   *  because the value wanted is the one from before the STROKE, not before this stamp. */
  take(p: Patch) {
    if (!p) return;
    if (p.scatterAdded?.length || p.scatterRemoved?.length) { this.scatterTouched = true; this.any = true; }
    if (!p.height && !p.splat) return;
    if (p.w <= 0 || p.h <= 0) return;
    this.grow(p.x0, p.z0, p.w, p.h);
    if (p.height && !this.height) this.height = new Float32Array(this.w * this.h);
    if (p.splat && !this.splat) this.splat = new Uint8Array(this.w * this.h * 4);
    this.any = true;
    for (let j = 0; j < p.h; j++) {
      for (let i = 0; i < p.w; i++) {
        const s = j * p.w + i;
        const d = (p.z0 - this.z0 + j) * this.w + (p.x0 - this.x0 + i);
        const was = this.seen[d];
        if (p.height && this.height && !(was & 1)) { this.height[d] = p.height[s]; this.seen[d] = was | 1; }
        if (p.splat && this.splat && !(this.seen[d] & 2)) {
          this.splat[d * 4] = p.splat[s * 4];
          this.splat[d * 4 + 1] = p.splat[s * 4 + 1];
          this.splat[d * 4 + 2] = p.splat[s * 4 + 2];
          this.splat[d * 4 + 3] = p.splat[s * 4 + 3];
          this.seen[d] = this.seen[d] | 2;
        }
      }
    }
  }

  /**
   * The one patch for the whole stroke.
   *
   * Samples inside the union rectangle that no stamp ever touched are filled from the field AS
   * IT IS NOW — which is what they were before, since nothing moved them. That is why the union
   * rectangle can be ragged and the patch still exact.
   */
  finish(t: TerrainData, eng: TerrainEngine): Patch | null {
    if (!this.any) return null;
    const p: Patch = { x0: this.x0, z0: this.z0, w: this.w, h: this.h };
    if (this.height || this.splat) {
      const now = eng.capture(t, this.x0, this.z0, this.w, this.h);
      if (this.height && now.height) {
        const out = Float32Array.from(now.height);
        for (let i = 0; i < out.length; i++) if (this.seen[i] & 1) out[i] = this.height[i];
        p.height = out;
      }
      if (this.splat && now.splat) {
        const out = Uint8Array.from(now.splat);
        for (let i = 0; i < this.seen.length; i++) {
          if (!(this.seen[i] & 2)) continue;
          out[i * 4] = this.splat[i * 4];
          out[i * 4 + 1] = this.splat[i * 4 + 1];
          out[i * 4 + 2] = this.splat[i * 4 + 2];
          out[i * 4 + 3] = this.splat[i * 4 + 3];
        }
        p.splat = out;
      }
    }
    return p;
  }

  rect(): Rect { return { x0: this.x0, z0: this.z0, w: this.w, h: this.h }; }
}

/**
 * The stroke-shaped undo stack.
 *
 * Depth is 50 by default, which is the floor the brief set and comfortably more than the
 * eight-or-so a person actually walks back. Redo is symmetric: undoing captures what it is about
 * to destroy, so a redo is just another patch — no replay of the brush, and no drift between
 * "what happens if I redo" and "what happened the first time".
 */
export class TerrainHistory {
  private past: Step[] = [];
  private future: Step[] = [];
  private open: Merged | null = null;
  private openLabel = "";

  constructor(private eng: TerrainEngine = REAL_ENGINE, public max = 64) {}

  get depth() { return this.past.length; }
  get redoDepth() { return this.future.length; }
  get striking() { return this.open !== null; }
  /** What Ctrl+Z would undo, for the button's tooltip. */
  get topLabel() { return this.past.length ? this.past[this.past.length - 1].label : ""; }

  clear() { this.past = []; this.future = []; this.open = null; }

  /** The button went down. Everything until `end` is one entry. */
  begin(label = "stroke") {
    this.open = new Merged();
    this.openLabel = label;
  }

  add(p: Patch) { this.open?.take(p); }

  /** The button came up. Returns true when something was actually pushed — a click that changed
   *  nothing must not consume an undo slot, or Ctrl+Z stops meaning anything. */
  end(t: TerrainData): boolean {
    const m = this.open;
    this.open = null;
    if (!m || !m.any) return false;
    const patch = m.finish(t, this.eng);
    if (!patch) return false;
    const step: Step = { patch, label: this.openLabel };
    // No snapshot means no scatter entry, never an empty one: an empty list would read as "there
    // was nothing on the ground" and undo would sweep the whole field clear.
    if (m.scatterTouched && this.scatterBefore) step.scatter = this.scatterBefore.slice();
    this.scatterBefore = null;
    this.past.push(step);
    if (this.past.length > this.max) this.past.shift();
    // A new stroke ends the branch the redo stack belonged to; keeping it would let redo paste a
    // patch over ground that no longer looks like what the patch was cut from.
    this.future = [];
    return true;
  }

  /** Drop the open stroke without pushing it. For a pointer cancel, not for undo. */
  abandon() { this.open = null; this.scatterBefore = null; }

  /** Taken at `begin` by the tool, because only the caller knows the list before the first stamp. */
  private scatterBefore: ScatterItem[] | null = null;
  noteScatter(list: ScatterItem[]) { if (!this.scatterBefore) this.scatterBefore = list.slice(); }

  undo(t: TerrainData): Rect | null {
    const s = this.past.pop();
    if (!s) return null;
    this.future.push(this.inverse(t, s));
    this.apply(t, s);
    return { x0: s.patch.x0, z0: s.patch.z0, w: s.patch.w, h: s.patch.h };
  }

  redo(t: TerrainData): Rect | null {
    const s = this.future.pop();
    if (!s) return null;
    this.past.push(this.inverse(t, s));
    this.apply(t, s);
    return { x0: s.patch.x0, z0: s.patch.z0, w: s.patch.w, h: s.patch.h };
  }

  /** What the field holds right now, over the same rectangle — the step that puts it back. */
  private inverse(t: TerrainData, s: Step): Step {
    const patch: Patch = { x0: s.patch.x0, z0: s.patch.z0, w: s.patch.w, h: s.patch.h };
    // A scatter-only stroke has an empty rectangle, and `capture` of nothing is not something
    // every engine is obliged to survive.
    const now = s.patch.w > 0 && s.patch.h > 0
      ? this.eng.capture(t, s.patch.x0, s.patch.z0, s.patch.w, s.patch.h) : null;
    if (s.patch.height && now?.height) patch.height = Float32Array.from(now.height);
    if (s.patch.splat && now?.splat) patch.splat = Uint8Array.from(now.splat);
    const out: Step = { patch, label: s.label };
    if (s.scatter) out.scatter = t.scatter.slice();
    return out;
  }

  private apply(t: TerrainData, s: Step) {
    this.eng.undoPatch(t, s.patch);
    // In place, not a reassignment: the viewport and the placement cache both hold this array.
    if (s.scatter) { t.scatter.length = 0; for (const it of s.scatter) t.scatter.push(it); }
  }
}

// ------------------------------------------------------------------ the pointer

export interface Mods { shift?: boolean; ctrl?: boolean; alt?: boolean }

/** Where the cursor's ray met the ground, in world units. Null when it missed — off the edge of
 *  the field, or looking at the sky. */
export interface Ground { x: number; z: number }

/** What the tool is doing between a down and an up. */
export type Doing = "" | "stroke" | "strength";

export class TerrainTool {
  brush: Brush = { ...DEFAULT_BRUSH };
  history: TerrainHistory;
  /** Where the ring is drawn. Kept even when the button is up, because the ring is the whole
   *  point: a brush whose size you cannot see is a brush you undo. */
  cursor: Ground | null = null;
  doing: Doing = "";
  /** The last thing the tool did that is worth putting in the status bar. */
  note = "";

  private lastAt: Ground | null = null;
  private lastT = 0;
  private dragX = 0;
  private strength0 = 0;
  private carry = 0;

  constructor(private eng: TerrainEngine = REAL_ENGINE, max = 64) {
    this.history = new TerrainHistory(eng, max);
  }

  /** The brush wants something the palette has not given it yet. "" when it is ready. */
  blocked(): string {
    const d = BRUSH_BY_KIND[this.brush.kind];
    if (d?.wantsLayer && this.brush.layer === undefined) return "choose a layer to paint";
    if (d?.wantsAsset && !this.brush.asset) return "choose an asset to scatter";
    return "";
  }

  setKind(k: BrushKind) {
    this.brush = { ...this.brush, kind: k };
    // Flatten's target is re-read at the start of every stroke; a stale one from the last hill
    // would level this one to the wrong height.
    if (k === "flatten") delete this.brush.target;
  }

  /** `[` and `]`. Returns the new radius, or 0 when the key was not one of ours. */
  resize(dir: -1 | 1): number {
    const r = dir > 0 ? this.brush.radius * RADIUS_STEP : this.brush.radius / RADIUS_STEP;
    this.brush.radius = Math.min(RADIUS_MAX, Math.max(RADIUS_MIN, Math.round(r * 1000) / 1000));
    return this.brush.radius;
  }

  /**
   * A key press in Terrain mode. Returns what it changed, so the editor can re-render the panel
   * and swallow the event — and "" when the key was none of ours, so everything else still works.
   */
  key(k: string): "" | "radius" | BrushKind {
    if (k === "[") { this.resize(-1); return "radius"; }
    if (k === "]") { this.resize(1); return "radius"; }
    const low = k.length === 1 ? k.toLowerCase() : k;
    const b = BRUSHES.find((d) => d.key === low);
    if (!b) return "";
    this.setKind(b.kind);
    return b.kind;
  }

  /**
   * The button went down.
   *
   * With Shift it is not a stroke at all: horizontal movement sets strength, which is how every
   * sculpting tool in the trade does it and the only way to change strength without leaving the
   * ground and losing your place.
   */
  down(t: TerrainData | null, at: Ground | null, px: number, nowMs: number, mods: Mods = {}) {
    this.cursor = at;
    if (mods.shift) {
      this.doing = "strength";
      this.dragX = px;
      this.strength0 = this.brush.strength;
      return;
    }
    if (!t || !at || this.blocked()) { this.doing = ""; return; }
    this.doing = "stroke";
    this.lastAt = at;
    this.lastT = nowMs;
    this.carry = 0;
    // Flatten levels to the ground the stroke STARTED on. Reading it per stamp would make the
    // brush chase its own output and flatten nothing.
    if (this.brush.kind === "flatten") this.brush.target = this.eng.heightAt(t, at.x, at.z);
    this.history.begin(BRUSH_BY_KIND[this.brush.kind]?.label.toLowerCase() || this.brush.kind);
    if (this.brush.kind === "scatter" || this.brush.kind === "erase") this.history.noteScatter(t.scatter);
    // A tap with no movement must still leave a mark, so the first stamp is laid here.
    this.stamp(t, at, 1 / 60);
  }

  /**
   * The pointer moved. Returns the sample rectangle that changed, for a partial mesh rebuild,
   * or null when nothing did.
   */
  move(t: TerrainData | null, at: Ground | null, px: number, nowMs: number, mods: Mods = {}): Rect | null {
    this.cursor = at;
    if (this.doing === "strength") {
      // 300 px for the full 0..1, the same travel the panel's number fields use, so the hand
      // learns one sensitivity rather than two.
      const d = (px - this.dragX) / 300;
      this.brush.strength = Math.min(1, Math.max(0, this.strength0 + d));
      this.note = "strength " + this.brush.strength.toFixed(2);
      return null;
    }
    if (this.doing !== "stroke" || !t || !at) return null;
    const dt = Math.min(MAX_DT, Math.max(0, (nowMs - this.lastT) / 1000));
    this.lastT = nowMs;
    const from = this.lastAt || at;
    const dist = Math.hypot(at.x - from.x, at.z - from.z);
    const spacing = Math.max(0.02, this.brush.radius * SPACING);

    let acc: Rect | null = null;
    if (dist < spacing * 0.5) {
      // Holding still. One stamp with the whole slice of time: a held brush must keep digging,
      // which is what `dt` in the contract is for.
      acc = this.union(acc, this.stamp(t, at, dt));
    } else {
      const want = Math.min(MAX_STEPS, Math.max(1, Math.ceil(dist / spacing)));
      const slice = dt / want;
      for (let i = 1; i <= want; i++) {
        const f = i / want;
        acc = this.union(acc, this.stamp(t, { x: from.x + (at.x - from.x) * f, z: from.z + (at.z - from.z) * f }, slice));
      }
    }
    this.lastAt = at;
    return acc;
  }

  /** The button came up. True when a stroke was pushed onto the stack. */
  up(t: TerrainData | null): boolean {
    const was = this.doing;
    this.doing = "";
    this.lastAt = null;
    if (was === "strength") return false;
    if (!t) { this.history.abandon(); return false; }
    return this.history.end(t);
  }

  /** A pointer cancel, or leaving the mode mid-stroke. */
  cancel() { this.doing = ""; this.lastAt = null; this.history.abandon(); }

  private stamp(t: TerrainData, at: Ground, dt: number): Rect | null {
    if (dt <= 0) return null;
    const p = this.eng.applyBrush(t, this.brush, at.x, at.z, dt);
    if (!p) return null;
    this.history.add(p);
    return p.w > 0 && p.h > 0 ? { x0: p.x0, z0: p.z0, w: p.w, h: p.h } : null;
  }

  private union(a: Rect | null, b: Rect | null): Rect | null {
    if (!a) return b;
    if (!b) return a;
    const x0 = Math.min(a.x0, b.x0), z0 = Math.min(a.z0, b.z0);
    const x1 = Math.max(a.x0 + a.w, b.x0 + b.w), z1 = Math.max(a.z0 + a.h, b.z0 + b.h);
    return { x0, z0, w: x1 - x0, h: z1 - z0 };
  }
}

// ------------------------------------------------------------------ the sidecar

/**
 * WHY TERRAIN GETS A FILE OF ITS OWN, beside the asset, rather than a field in `.edits.json`.
 *
 * Two numbers decided it. A 513² field base64s to about 1.4 MB, and the editor's edit document is
 * deep-copied with JSON.parse(JSON.stringify(…)) on EVERY undo push — a slider nudge would have
 * cloned a megabyte and a half. And `normaliseEdits` rebuilds the document field by field from
 * `emptyEdits`, so an unknown key does not survive a reload at all.
 *
 * Same folder, same stem, same "reopen the asset and it is still there": `foo.terrain.json`
 * beside `foo.edits.json`.
 */
export const TERRAIN_DOC_VERSION = 1;

export interface TerrainDoc {
  version: number;
  /** Whatever `terrain.serialize` produced: the field, the splat, the layers and the scatter. */
  field: string;
  /** The recipes for the scattered assets. Without these a reload knows a tree stands at
   *  (12, 0, 40) and not which tree. */
  palette: ScatterAsset[];
  updated?: number;
}

/** `…/foo.edits.json` → `…/foo.terrain.json`. Derived from the editor's own sidecar path so the
 *  two can never end up in different folders. */
export function terrainSidecarPath(editsPath: string): string {
  const p = String(editsPath || "");
  return /\.edits\.json$/i.test(p) ? p.replace(/\.edits\.json$/i, ".terrain.json")
    : p.replace(/\.json$/i, "") + ".terrain.json";
}

export function packTerrain(field: string, palette: ScatterAsset[]): string {
  const doc: TerrainDoc = {
    version: TERRAIN_DOC_VERSION, field, palette: palette.slice(), updated: Date.now() / 1000,
  };
  return JSON.stringify(doc, null, 2);
}

/**
 * Read one back. Null rather than a throw for anything unreadable: this file can be edited by
 * hand or written by an agent, and one bad character must cost the terrain, never the editor.
 */
export function unpackTerrain(text: string): { field: string; palette: ScatterAsset[] } | null {
  let raw: any;
  try { raw = JSON.parse(text || ""); } catch { return null; }
  if (!raw || typeof raw !== "object" || typeof raw.field !== "string" || !raw.field) return null;
  const palette: ScatterAsset[] = [];
  if (Array.isArray(raw.palette)) {
    for (const a of raw.palette) {
      if (!a || typeof a.id !== "string" || !a.ref || typeof a.ref !== "object") continue;
      if (!["primitive", "code", "model", "image", "clone"].includes(a.ref.kind)) continue;
      palette.push({ id: a.id, name: typeof a.name === "string" ? a.name : a.id, ref: a.ref });
    }
  }
  return { field: raw.field, palette };
}

/** Only the assets the field actually stands on. A palette that grew to forty over a session
 *  would otherwise be written out whole, most of it recipes for nothing. */
export function usedPalette(scatter: ScatterItem[], palette: ScatterAsset[]): ScatterAsset[] {
  const used = new Set(scatter.map((s) => s.asset));
  return palette.filter((a) => used.has(a.id));
}

// ------------------------------------------------------------------ the emitted builder
//
// THE SIDECAR IS THE EDITOR'S MEMORY; THE BUILDER IS THE GAME'S GROUND.
//
// `.terrain.json` exists so that reopening the asset finds the field exactly as it was left,
// undo stack and palette and all. It is a Studio file: nothing but this editor knows how to read
// it. Shipping it as the game's terrain would mean the game carrying a base64 heightfield reader
// it did not write, for a format it cannot see.
//
// `emitBuilder` writes the other thing: a module the game imports, that imports nothing back.
// That is the whole premise of this Studio — an asset leaves as CODE — and terrain was the one
// asset still leaving as data. Everything below is the path that file takes, kept here rather
// than in the React file so it can be checked with no browser.

/**
 * `…/foo.edits.json` → `…/foo.terrain.js`.
 *
 * Derived from the SIDECAR path rather than from the asset's, one step further along the same
 * chain, so the ground's data and the ground's code cannot end up in different folders. A person
 * who moves one and not the other has moved both.
 */
export function terrainBuilderPath(editsPath: string, ext = ".js"): string {
  return terrainSidecarPath(editsPath).replace(/\.json$/i, ext);
}

/** Names that mean "this is the terrain file", so the export does not come out `buildTerrainTerrain`. */
const PLAIN_STEM = /^(terrain|ground|land|level|studio|edits|scene|world)$/i;

/**
 * The export name a builder file gets, from the file's own name.
 *
 * One field to fill in, not two. `hills.terrain.js` exports `buildHillsTerrain`, which is what a
 * game with three grounds in it needs; `studio.terrain.js` and `terrain.js` export plain
 * `buildTerrain`, because a name that repeats itself reads like a mistake.
 */
export function builderName(path: string): string {
  const base = String(path || "").split(/[\\/]/).pop() || "";
  const stem = base.replace(/\.(m|c)?[jt]sx?$/i, "").replace(/\.terrain$/i, "");
  const words = stem.split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (!words.length) return "buildTerrain";
  const pascal = words.map((w) => w[0].toUpperCase() + w.slice(1)).join("");
  if (PLAIN_STEM.test(pascal)) return "buildTerrain";
  return "build" + (/^[0-9]/.test(pascal) ? "_" : "") + pascal + "Terrain";
}

/** A tab of the shared headless browser, as much of one as the emitter needs. */
export interface LiveTab { project: string; engine: string }

export interface EmitEngine {
  engine: "three" | "playcanvas";
  /** Plain words for the panel: how it knows. A guess says so. */
  why: string;
  /** The game is up in the shared browser right now — so a written file is on disk and NOT in
   *  the running page until it is imported again. */
  running: boolean;
}

const samePath = (a: string, b: string) =>
  String(a || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
  === String(b || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

/**
 * Which engine this ground has to be emitted for.
 *
 * The running game is asked FIRST and believed. Everything else is inference: the project's
 * declared engine, then what the asset's own code plainly calls. Emitting three for a PlayCanvas
 * game is not a cosmetic mistake — `new THREE.BufferGeometry()` in a game with no THREE is a
 * ReferenceError on the first line, which is why this is a decision and not a default.
 */
export function pickEmitEngine(project: string, tabs: LiveTab[] | null | undefined,
                               sourceEngine: string, pcCode: boolean): EmitEngine {
  const tab = (tabs || []).find((t) => samePath(t.project, project));
  if (tab && (tab.engine === "three" || tab.engine === "playcanvas")) {
    return { engine: tab.engine, why: "the running game is " + tab.engine, running: true };
  }
  const running = !!tab;
  if (sourceEngine === "playcanvas") return { engine: "playcanvas", why: "this project is PlayCanvas", running };
  if (sourceEngine === "three") return { engine: "three", why: "this project is three.js", running };
  if (pcCode) return { engine: "playcanvas", why: "this asset's code calls pc.*", running };
  return { engine: "three", why: "no engine declared — three.js assumed", running };
}

// ------------------------------------------------------------------ chunks and instances

/**
 * Samples per side of one chunk of ground.
 *
 * 64 is 8,192 triangles a chunk: small enough that a chunk off screen is worth rejecting, big
 * enough that a 1025² field is 256 of them rather than four thousand. Below about 32 the draw
 * calls cost more than the culling saves; above 128 a chunk is most of a screen and the frustum
 * never rejects one.
 */
export const CHUNK_SAMPLES = 64;

/**
 * Above this many samples per side, `auto` chunks the field.
 *
 * A 257² field is 131,072 triangles in ONE draw call, and a modern GPU does not notice it. Cut
 * into sixteen it is sixteen draw calls, sixteen bounding-sphere tests and sixteen geometries to
 * keep in step — for a field that is nearly always entirely on screen anyway. 513² is 524,288
 * triangles, which is where a whole-field draw starts to show, and where the cull starts to have
 * something to reject.
 */
export const CHUNK_ABOVE = 257;

/** What Settings says, turned into what the viewport does. `auto` is the threshold above. */
export function wantChunks(mode: "auto" | "on" | "off", res: number): boolean {
  if (mode === "off") return false;
  if (mode === "on") return true;
  return res > CHUNK_ABOVE;
}

/** The emit's own settings, kept whole so the panel and the writer cannot disagree. */
export interface EmitPlan {
  path: string;
  name: string;
  engine: "three" | "playcanvas";
  /** 1 = every sample. Higher writes a coarser field: the same ground, fewer bytes. */
  lod: number;
}

/** The samples per side an emit at this detail actually writes. `emitBuilder` refuses a `lod`
 *  that does not divide the field, and silently keeps every sample; this says the same thing
 *  before the button is pressed. */
export function emitRes(res: number, lod: number): number {
  const step = Math.max(1, Math.floor(lod));
  return step > 1 && (res - 1) % step === 0 ? (res - 1) / step + 1 : res;
}
