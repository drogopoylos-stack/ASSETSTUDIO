// Parameter handles: arrows on the model that drive a number parameter when dragged, read from
// `manifest.handles` (see configurator.ts).
//
// The configurator's pattern: a building says its Width is dragged along x from its right-hand
// face, and the editor puts an arrow there. Pulling the arrow is the SAME edit as moving the Width
// slider — the value goes through the editor's own `set`, the model rebuilds with more window bays,
// and the arrow is found again on the NEW face a frame later, still under the hand.
//
// Three layers, in the order they can be trusted:
//   1. the manifest reader — the only door untrusted data comes in by, so it refuses anything it
//      cannot read exactly rather than guessing what a malformed entry meant;
//   2. the maths — a face, a line, a value — pure, and tested without a browser
//      (frontend/tests/handles.test.ts);
//   3. ParamHandleLayer — the drawing and the pointer, which is only glue between the two. The
//      world owns one while an asset declares handles and drops it, listeners and all, on `null`.

import type { HandleSpec, ParamHandleCallbacks } from "./configurator";
import type { ParamSpec } from "./kit";

export type HandleAxis = HandleSpec["axis"];
export type HandleSide = HandleSpec["side"];
export type Vec3 = [number, number, number];

// ================================================================== 1. the manifest

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
/** Absent means not written at all — `undefined`, or the `null` a JSON manifest uses for it. */
const absent = (v: unknown) => v === undefined || v === null;
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** One handle is one parameter, dragged along one axis, from one face. */
export function handleKey(h: Pick<HandleSpec, "param" | "axis" | "side">): string {
  return h.param + "|" + h.axis + "|" + h.side;
}

/**
 * One raw entry, read strictly: every field that is PRESENT must be valid, or the whole entry is
 * refused. A handle with a typo in its `side` that silently became "max" would sit on the wrong
 * face and drag the wrong way — worse than no handle, because it looks like it works.
 *
 * Knows nothing about parameters: `handlesFromManifest` checks the binding, and the viewport uses
 * this alone for a list handed to it directly.
 */
export function readHandle(raw: unknown): HandleSpec | null {
  if (!isRecord(raw)) return null;
  const param = raw.param, axis = raw.axis;
  if (typeof param !== "string" || !param) return null;
  if (axis !== "x" && axis !== "y" && axis !== "z") return null;
  const side = absent(raw.side) ? "max" : raw.side;
  if (side !== "min" && side !== "max") return null;
  const scale = absent(raw.scale) ? 1 : raw.scale;
  if (!isNum(scale) || scale === 0) return null;
  const snap = absent(raw.snap) ? 0 : raw.snap;
  if (!isNum(snap) || snap < 0) return null;
  const h: HandleSpec = { param, axis, side, scale, snap };
  if (!absent(raw.min)) { if (!isNum(raw.min)) return null; h.min = raw.min; }
  if (!absent(raw.max)) { if (!isNum(raw.max)) return null; h.max = raw.max; }
  if (h.min !== undefined && h.max !== undefined && h.min > h.max) return null;
  if (!absent(raw.part)) {
    if (typeof raw.part !== "string") return null;
    // A name is matched exactly, so it is kept exactly; only an empty one means "no part".
    if (raw.part.trim()) h.part = raw.part;
  }
  if (!absent(raw.label)) {
    if (typeof raw.label !== "string") return null;
    if (raw.label.trim()) h.label = raw.label.trim();
  }
  return h;
}

/** Read `manifest.handles`, keeping only handles bound to a declared NUMBER parameter. Never
 *  throws: a malformed entry, or one bound to a parameter that does not exist, is skipped.
 *
 *  What comes back is complete enough for the viewport to work without the parameter list: a
 *  clamp the handle did not state is filled from the parameter's own min / max, and the label from
 *  the parameter's label — `setParamHandles` is handed handles, not specs. */
export function handlesFromManifest(manifest: unknown, specs: ParamSpec[]): HandleSpec[] {
  if (!isRecord(manifest) || !Array.isArray(manifest.handles)) return [];
  const numbers = new Map<string, ParamSpec>();
  for (const s of Array.isArray(specs) ? specs : []) {
    if (s && s.kind === "number" && typeof s.key === "string" && !numbers.has(s.key)) numbers.set(s.key, s);
  }
  const out: HandleSpec[] = [];
  const seen = new Set<string>();
  for (const raw of manifest.handles) {
    let h: HandleSpec | null = null;
    try { h = readHandle(raw); } catch { h = null; }   // a getter that throws is malformed too
    if (!h) continue;
    const spec = numbers.get(h.param);
    if (!spec) continue;
    const key = handleKey(h);
    if (seen.has(key)) continue;                        // the first one written wins
    seen.add(key);
    // The parameter's range fills what the handle left out, and never overrules what it said:
    // a stated bound that contradicts the parameter's other bound keeps its own side only.
    let min = h.min ?? (isNum(spec.min) ? spec.min : undefined);
    let max = h.max ?? (isNum(spec.max) ? spec.max : undefined);
    if (min !== undefined && max !== undefined && min > max) {
      if (h.min === undefined) min = undefined;
      if (h.max === undefined) max = undefined;
    }
    // A handle that did not state `snap` rounds like the parameter's own slider does. Without it a
    // drag wrote values such as 10.0928 into the file — the same edit as the slider, spelled
    // unreadably. An explicit `snap: 0` still means "no rounding at all".
    const snapStated = isRecord(raw) && !absent(raw.snap);
    const snap = snapStated ? h.snap : (isNum(spec.step) && spec.step > 0 ? spec.step : 0);
    const full: HandleSpec = { param: h.param, axis: h.axis, side: h.side, scale: h.scale, snap };
    if (min !== undefined) full.min = min;
    if (max !== undefined) full.max = max;
    if (h.part) full.part = h.part;
    full.label = h.label || spec.label || h.param;
    out.push(full);
  }
  return out;
}

// ================================================================== 2. the maths

const AXIS_INDEX: Record<HandleAxis, number> = { x: 0, y: 1, z: 2 };

/** The unit vector a handle pulls along: +axis from the max face, −axis from the min face.
 *
 *  A drag is measured OUTWARD from the face the handle sits on, so pulling any handle away from
 *  the model grows its parameter (for a positive `scale`), whichever side it is on. A pair of
 *  handles on the min and max faces of a centred model then both widen it, the way a person pulling
 *  either edge expects. */
export function outwardOf(axis: HandleAxis, side: HandleSide): Vec3 {
  const v: Vec3 = [0, 0, 0];
  v[AXIS_INDEX[axis]] = side === "min" ? -1 : 1;
  return v;
}

/** The direction two handles on one face are spread along: up a wall, across a roof. */
export function tangentOf(axis: HandleAxis): Vec3 {
  return axis === "y" ? [1, 0, 0] : [0, 1, 0];
}

/** The middle of one face of a box, pushed `offset` outward. The box must not be empty. */
export function faceAnchor(min: Vec3, max: Vec3, axis: HandleAxis, side: HandleSide, offset = 0): Vec3 {
  const p: Vec3 = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  const i = AXIS_INDEX[axis];
  p[i] = side === "min" ? min[i] - offset : max[i] + offset;
  return p;
}

/** Handles that share a face (same part, axis and side) get a slot each, so no two sit on the same
 *  pixel with only one of them reachable. `slot` counts from 0 in the order given. */
export function slotsOf(handles: Array<Pick<HandleSpec, "axis" | "side" | "part">>): Array<{ slot: number; of: number }> {
  const faceOf = (h: Pick<HandleSpec, "axis" | "side" | "part">) => (h.part || "") + "|" + h.axis + "|" + h.side;
  const count = new Map<string, number>();
  for (const h of handles) count.set(faceOf(h), (count.get(faceOf(h)) || 0) + 1);
  const used = new Map<string, number>();
  return handles.map((h) => {
    const f = faceOf(h);
    const slot = used.get(f) || 0;
    used.set(f, slot + 1);
    return { slot, of: count.get(f) || 1 };
  });
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/**
 * Where the pointer ray passes closest to a handle's line, as metres along the line from `o`
 * (positive in the direction of `d`, whatever the length of `d`).
 *
 * The closest points of two lines, not a projection of the pointer onto the screen: a screen-space
 * drag feels right until the camera leans toward the axis, and then it inverts. Null — "the pointer
 * says nothing about this line" — when the two are within `minSin` (as the sine of the angle, about
 * 1.1 degrees by default) of parallel, or when the nearest point of the ray is behind the eye; the
 * caller keeps the value it had rather than let a division by almost nothing throw the model
 * across the room.
 */
export function closestOnAxis(o: Vec3, d: Vec3, ro: Vec3, rd: Vec3, minSin = 0.02): number | null {
  const a = dot(d, d), b = dot(d, rd), c = dot(rd, rd);
  if (!(a > 0) || !(c > 0)) return null;
  const w: Vec3 = [o[0] - ro[0], o[1] - ro[1], o[2] - ro[2]];
  const dw = dot(d, w), ew = dot(rd, w);
  const den = a * c - b * b;                    // |d|^2 |rd|^2 sin^2 of the angle between them
  if (!(den > minSin * minSin * a * c)) return null;
  const s = (b * ew - c * dw) / den;            // along the line, in lengths of d
  const t = (a * ew - b * dw) / den;            // along the ray
  if (t < 0) return null;
  const m = s * Math.sqrt(a);
  return Number.isFinite(m) ? m : null;
}

/** Round to the nearest multiple of `snap`, without the float dust (0.30000000000000004) that
 *  would otherwise end up in a sidecar. 0, or anything not positive, leaves the value alone. */
export function snapValue(v: number, snap: number): number {
  if (!(snap > 0) || !Number.isFinite(v)) return v;
  const r = Math.round(Math.round(v / snap) * snap * 1e9) / 1e9;
  return r === 0 ? 0 : r;                        // never -0
}

/**
 * The value a drag has reached: the start value plus the metres dragged times `scale`, then
 * rounded to `snap`, then clamped — to the handle's own min / max, or where it states none, the
 * parameter's. In that order, so a clamp is never undone by the rounding after it.
 */
export function dragValue(
  start: number, metres: number,
  h: Pick<HandleSpec, "scale" | "snap" | "min" | "max">,
  spec?: { min?: number; max?: number },
): number {
  let v = start + metres * (isNum(h.scale) && h.scale !== 0 ? h.scale : 1);
  v = snapValue(v, h.snap);
  const lo = isNum(h.min) ? h.min : isNum(spec?.min) ? spec!.min! : -Infinity;
  const hi = isNum(h.max) ? h.max : isNum(spec?.max) ? spec!.max! : Infinity;
  if (lo <= hi) v = Math.min(hi, Math.max(lo, v));
  return v === 0 ? 0 : v;
}

/** World units per CSS pixel at `depth` in front of a perspective camera: what keeps a handle the
 *  same size on screen at any zoom. */
export function pixelSize(depth: number, fovDeg: number, heightPx: number): number {
  return (2 * Math.max(0, depth) * Math.tan(((fovDeg || 45) * Math.PI) / 360)) / Math.max(1, heightPx);
}

/** The same for an orthographic camera, where depth does not matter and the frustum does. */
export function orthoPixelSize(top: number, bottom: number, zoom: number, heightPx: number): number {
  return Math.abs(top - bottom) / (zoom || 1) / Math.max(1, heightPx);
}

/** How much of a handle to show when its axis points at the eye: `along` is the cosine between the
 *  axis and the line of sight. Fully drawn up to about 20 degrees off it, gone by 8 — end-on, a
 *  handle is a dot that drags in no direction the hand can read, which is why the gizmo fades too. */
export function endOnFade(along: number): number {
  const a = Math.abs(along);
  const lo = 0.94, hi = 0.99;
  if (!(a > lo)) return 1;
  if (a >= hi) return 0;
  return (hi - a) / (hi - lo);
}

/** The resize cursor that matches a direction on screen (pixels, y down): ↔ ↕ ⤡ ⤢. */
export function resizeCursor(dx: number, dy: number): string {
  if (!(Math.abs(dx) + Math.abs(dy) > 1e-9)) return "move";
  const deg = ((((Math.atan2(dy, dx) * 180) / Math.PI) % 180) + 180) % 180;
  if (deg < 22.5 || deg >= 157.5) return "ew-resize";
  if (deg < 67.5) return "nwse-resize";          // right and down: top-left to bottom-right
  if (deg < 112.5) return "ns-resize";
  return "nesw-resize";
}

/** How to lay the flat arrow in the pill: turned (radians, counter-clockwise on screen) to the
 *  direction the axis runs on screen (pixels, y down), and stretched to between 0.55 and 1 of its
 *  length by how much of the axis the screen shows — `full` is the length a drag of the same
 *  distance would cover across the line of sight. A handle that drags partly into the screen looks
 *  shorter, the way the model's own edges do. */
export function arrowPose(dx: number, dy: number, full: number): { angle: number; stretch: number } {
  const len = Math.hypot(dx, dy);
  const angle = len > 1e-9 ? Math.atan2(-dy, dx) : 0;
  const k = full > 0 ? Math.min(1, Math.max(0, len / full)) : 1;
  return { angle, stretch: 0.55 + 0.45 * k };
}

/** The handle under the pointer: the nearest usable one within `radius` pixels, or -1. */
export function pickHandle(points: Array<{ x: number; y: number; ok: boolean }>, x: number, y: number, radius: number): number {
  let best = -1, bestD = radius * radius;
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    if (!p.ok) continue;
    const d = (p.x - x) * (p.x - x) + (p.y - y) * (p.y - y);
    if (d <= bestD) { best = i; bestD = d; }
  }
  return best;
}

/** How many decimals a value dragged in steps of `snap` needs: none for whole floors, two for a
 *  free drag. */
export function decimalsFor(snap: number): number {
  if (!(snap > 0)) return 2;
  for (let d = 0; d <= 6; d++) {
    const m = snap * Math.pow(10, d);
    if (Math.abs(m - Math.round(m)) < 1e-9 * Math.max(1, m)) return d;
  }
  return 2;
}

/** What the label beside a dragged handle says: "Width 6.40", "Floors 3". */
export function handleText(label: string, value: number, snap: number): string {
  return (label ? label + " " : "") + (Number.isFinite(value) ? value.toFixed(decimalsFor(snap)) : "—");
}

/** Where the value label goes, in canvas pixels: centred `lift` above the handle at (x, y), or
 *  below it when its bottom edge would land within 64 px of the top of the viewport (the view bar
 *  lives there); pulled in from the sides so a label `width` wide stays whole inside `viewW`.
 *  `y` is the label's bottom edge when above, its top edge when below. */
export function labelPlace(x: number, y: number, lift: number, width: number, viewW: number): { x: number; y: number; below: boolean } {
  const below = y - lift < 64;
  const half = Math.max(0, width) / 2 + 4;
  const cx = viewW > 2 * half ? Math.min(viewW - half, Math.max(half, x)) : x;
  return { x: cx, y: below ? y + lift : y - lift, below };
}

/** CSS pixels inside the canvas to normalised device coordinates, y up. */
export function ndcOf(x: number, y: number, w: number, h: number): { x: number; y: number } {
  return { x: (x / Math.max(1, w)) * 2 - 1, y: -((y / Math.max(1, h)) * 2 - 1) };
}

// ================================================================== 3. drawing and the pointer

/** What the layer needs from the viewport. Everything is read when it is needed, never cached:
 *  the camera changes on every orbit and the asset on every rebuild. */
export interface HandleHost {
  /** The canvas the pointer arrives on. The editor's own handlers sit on its parent, so a press
   *  this layer takes never reaches them. */
  canvas: HTMLCanvasElement;
  /** A scene drawn last, onto a cleared depth buffer — the gizmo's — so no surface can hide a handle. */
  scene: any;
  camera(): any;
  /** The viewport in CSS pixels. */
  size(): { w: number; h: number };
  /** The asset: handles sit on the box of what is drawn under it. */
  root(): any;
  /** A part by the editor's key, for a handle with `part`. A search by name follows if it misses. */
  find(name: string): any;
  /** Handles are an Object-mode tool; false hides them and stops them taking the pointer. */
  live(): boolean;
  /** Something else owns the pointer (a G / R / S transform in flight): do not start a drag. */
  busy(): boolean;
}

/** One handle's screen state, for the harness and for an agent on the live link. */
export interface HandleInfo {
  param: string;
  axis: HandleAxis;
  side: HandleSide;
  label: string;
  /** Where its centre is drawn, in CSS pixels inside the canvas, and on the page. */
  x: number;
  y: number;
  clientX: number;
  clientY: number;
  /** Drawn and takeable. False end-on, behind the eye, or with no model to sit on. */
  visible: boolean;
  hovered: boolean;
  dragging: boolean;
  /** Its anchor in world space, or null. */
  at: Vec3 | null;
}

// Sizes in CSS pixels. The geometry is built in pixels and the group scaled by world-units-per-
// pixel every frame, which is how a handle stays one size on screen whatever the zoom.
const PX = {
  disc: 12,        // radius of the pill the arrow sits in
  rim: 1.6,        // its dark edge, so it separates from a pale wall
  pick: 16,        // how near the pointer has to be to take a handle
  offset: 5,       // how far outside the face the centre sits
  spread: 32,      // two handles sharing a face sit this far apart
  label: 20,       // the value label floats this far above the centre
  tip: 9.5,        // the arrow: tip to tip 19 px, heads 8.4 px wide, shaft 2.6 px
  head: 5.6,
  headHalf: 4.2,
  shaftHalf: 1.3,
};
const COLOR = { disc: 0xf4f5f7, rim: 0x0f1216, arrow: 0x151920, hot: 0xffd08a, hotRim: 0x5a3d12 };
/** Above the gizmo's 999: a handle takes the pointer before the gizmo does, so it is drawn over it. */
const ORDER = 1000;
/** How long a handle holds its place while the model under it is between two builds. */
const HOLD_MS = 400;

interface Item {
  spec: HandleSpec;
  group: any;
  disc: any;
  rim: any;
  arrow: any;
  mats: { disc: any; rim: any; arrow: any };
  slot: number;
  of: number;
  at: Vec3 | null;
  screen: { x: number; y: number; dx: number; dy: number; ok: boolean };
  fade: number;
  facing: boolean;
}

interface Drag {
  index: number;
  pointerId: number;
  start: number;
  value: number;
  /** How many times `set` was called. A drag that set anything is one edit even if it ended where
   *  it began — the editor opened an undo gesture on the first set, and only commit closes it. */
  sets: number;
  /** The line the drag measures along, frozen at the press: the model rebuilds under the hand, and
   *  a line that moved with it would feed the drag its own output. */
  origin: Vec3;
  dir: Vec3;
  s0: number;
}

type Box = { min: Vec3; max: Vec3 };

const MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock"]);

/**
 * The handles, drawn and taking the pointer. Created by `EditWorld.setParamHandles` when an asset
 * declares handles, disposed on `null` — which removes every object and every listener it added.
 *
 * Placement is recomputed every frame from the box of what is drawn, rather than pushed from each
 * place that can change the model: a rebuild, a transform override, a modifier, the gizmo, a frame
 * of animation and a hidden part all move that box, and a handle that listened to four of them
 * would sit in the wrong place after the fifth. For an asset of a few hundred parts that is well
 * under a millisecond; nothing is computed when no asset declares handles, because then there is
 * no layer at all.
 */
export class ParamHandleLayer {
  private readonly T: any;
  private readonly host: HandleHost;
  private readonly root: any;
  private readonly geo: { disc: any; rim: any; arrow: any };
  private readonly ray: any;
  private readonly box: any;
  private readonly tmp: any;
  private items: Item[] = [];
  private cb: ParamHandleCallbacks | null = null;
  private sig = "";
  private hover = -1;
  private drag: Drag | null = null;
  private label: HTMLDivElement | null = null;
  private listening = false;
  private cursor = "";
  private held = new Map<string, { box: Box; at: number }>();
  private warned = false;

  constructor(T: any, host: HandleHost) {
    this.T = T;
    this.host = host;
    this.ray = new T.Raycaster();
    this.box = new T.Box3();
    this.tmp = new T.Box3();
    this.root = new T.Group();
    this.root.name = "__paramHandles";
    this.root.visible = false;
    // THE ARROW IS FLAT. It was a shaft and two cones along the real axis, and in a three-quarter
    // view — the one the editor opens on — a cone that leans toward the eye shows its round base,
    // so the side handles read as two blobs, not an arrow. Flat in the pill's plane and turned to
    // the axis's direction ON SCREEN, it reads as ↔ from every angle, which is what the hand needs.
    const s = new T.Shape();
    const { tip, head, headHalf: hh, shaftHalf: sh } = PX;
    s.moveTo(tip, 0);
    s.lineTo(tip - head, hh); s.lineTo(tip - head, sh); s.lineTo(-(tip - head), sh); s.lineTo(-(tip - head), hh);
    s.lineTo(-tip, 0);
    s.lineTo(-(tip - head), -hh); s.lineTo(-(tip - head), -sh); s.lineTo(tip - head, -sh); s.lineTo(tip - head, -hh);
    s.closePath();
    this.geo = {
      disc: new T.CircleGeometry(PX.disc, 40),
      rim: new T.RingGeometry(PX.disc, PX.disc + PX.rim, 40),
      arrow: new T.ShapeGeometry(s),
    };
    host.scene.add(this.root);
  }

  /** Replace the handles. The same list again only swaps the callbacks, so an editor that hands
   *  over a fresh callback object on every render does not interrupt a drag in progress. */
  set(handles: HandleSpec[], cb: ParamHandleCallbacks) {
    this.cb = cb;
    const list: HandleSpec[] = [];
    const seen = new Set<string>();
    for (const raw of Array.isArray(handles) ? handles : []) {
      let h: HandleSpec | null = null;
      try { h = readHandle(raw); } catch { h = null; }
      if (!h || seen.has(handleKey(h))) continue;
      seen.add(handleKey(h));
      list.push(h);
    }
    const sig = JSON.stringify(list);
    if (sig === this.sig) return;
    this.sig = sig;
    const dragKey = this.drag ? handleKey(this.items[this.drag.index].spec) : "";
    for (const it of this.items) this.dropItem(it);
    const slots = slotsOf(list);
    this.items = list.map((h, i) => this.makeItem(h, slots[i].slot, slots[i].of));
    this.hover = -1;
    if (this.drag) {
      const i = this.items.findIndex((it) => handleKey(it.spec) === dragKey);
      if (i >= 0) this.drag.index = i;
      else this.endDrag(null);               // its handle is gone: nothing left to commit or restore
    }
    this.setCursor("");
    if (this.items.length) this.attach(); else this.detach();
    this.update();                           // placed now, so info() is right before the next frame
  }

  /** Once a frame, after the main pass has put every world matrix where it belongs. A fault in
   *  here hides the handles and says so once; it never takes the frame down with it. */
  update() {
    try {
      this.layout();
    } catch (e: any) {
      this.root.visible = false;
      this.hideLabel();
      if (!this.warned) { this.warned = true; console.warn("parameter handles:", e?.message || e); }
    }
  }

  /** Where each handle is on screen now, for the review harness and the live link. */
  info(): HandleInfo[] {
    const r = this.host.canvas.getBoundingClientRect();
    const size = this.host.size();
    const kx = size.w ? r.width / size.w : 1, ky = size.h ? r.height / size.h : 1;
    return this.items.map((it, i) => ({
      param: it.spec.param, axis: it.spec.axis, side: it.spec.side,
      label: it.spec.label || it.spec.param,
      x: round2(it.screen.x), y: round2(it.screen.y),
      clientX: round2(r.left + it.screen.x * kx), clientY: round2(r.top + it.screen.y * ky),
      visible: this.usable(it),
      hovered: this.hover === i, dragging: this.drag?.index === i,
      at: it.at ? [round4(it.at[0]), round4(it.at[1]), round4(it.at[2])] : null,
    }));
  }

  dispose() {
    this.endDrag(null);
    this.detach();
    for (const it of this.items) this.dropItem(it);
    this.items = [];
    try { this.host.scene.remove(this.root); } catch { /* never added */ }
    for (const g of Object.values(this.geo)) { try { g.dispose(); } catch { /* gone */ } }
    try { this.label?.remove(); } catch { /* gone with the page */ }
    this.label = null;
    this.setCursor("");
    this.cb = null;
    this.sig = "";
  }

  // ---------------------------------------------------------------- building
  private makeItem(spec: HandleSpec, slot: number, of: number): Item {
    const T = this.T;
    const mat = (color: number, opacity: number) => new T.MeshBasicMaterial({
      color, transparent: true, opacity, depthTest: false, depthWrite: false, toneMapped: false,
      side: T.DoubleSide,
    });
    const mats = { disc: mat(COLOR.disc, 1), rim: mat(COLOR.rim, 0.75), arrow: mat(COLOR.arrow, 1) };
    const group = new T.Group();
    group.name = "__handle:" + spec.param;
    group.visible = false;
    // The pill and the arrow both face the camera; the arrow is turned, every frame, to the way
    // this handle drags on screen — sideways from the front, diagonal in a three-quarter view.
    const disc = new T.Mesh(this.geo.disc, mats.disc);
    const rim = new T.Mesh(this.geo.rim, mats.rim);
    const arrow = new T.Mesh(this.geo.arrow, mats.arrow);
    group.add(disc, rim, arrow);
    for (const m of [disc, rim, arrow]) m.frustumCulled = false;
    this.root.add(group);
    return {
      spec, group, disc, rim, arrow, mats, slot, of, at: null,
      screen: { x: 0, y: 0, dx: 0, dy: 0, ok: false }, fade: 0, facing: true,
    };
  }

  private dropItem(it: Item) {
    try { this.root.remove(it.group); } catch { /* not ours */ }
    for (const m of Object.values(it.mats)) { try { m.dispose(); } catch { /* gone */ } }
  }

  // ---------------------------------------------------------------- placing
  private usable(it: Item): boolean { return it.screen.ok && it.fade > 0.35; }

  private layout() {
    const T = this.T, host = this.host;
    const show = !!this.cb && this.items.length > 0 && host.live();
    this.root.visible = show;
    if (!show) {
      if (this.drag) this.endDrag("cancel");
      if (this.hover >= 0) { this.hover = -1; this.setCursor(""); }
      for (const it of this.items) it.screen.ok = false;
      this.hideLabel();
      return;
    }
    const cam = host.camera();
    const size = host.size();
    cam.updateMatrixWorld();
    const eye = cam.getWorldPosition(new T.Vector3());
    const fwd = cam.getWorldDirection(new T.Vector3());
    const camQ = cam.getWorldQuaternion(new T.Quaternion());
    const ortho = !!cam.isOrthographicCamera;
    const now = performance.now();
    const boxes = new Map<string, Box | null>();
    const ranked: Array<{ it: Item; depth: number }> = [];
    const spin = new T.Quaternion(), zAxis = new T.Vector3(0, 0, 1);

    this.items.forEach((it, i) => {
      const part = it.spec.part || "";
      let b = boxes.get(part);
      if (b === undefined) { b = this.boxFor(part, now); boxes.set(part, b); }
      it.screen.ok = false;
      if (!b) { it.group.visible = false; it.at = null; return; }
      const face = faceAnchor(b.min, b.max, it.spec.axis, it.spec.side, 0);
      const depth = (face[0] - eye.x) * fwd.x + (face[1] - eye.y) * fwd.y + (face[2] - eye.z) * fwd.z;
      if (!ortho && !(depth > (cam.near || 1e-4))) { it.group.visible = false; it.at = null; return; }
      const px = ortho ? orthoPixelSize(cam.top, cam.bottom, cam.zoom, size.h) : pixelSize(depth, cam.fov, size.h);
      const out = outwardOf(it.spec.axis, it.spec.side);
      const tan = tangentOf(it.spec.axis);
      const spread = (it.slot - (it.of - 1) / 2) * PX.spread * px;
      const at: Vec3 = [0, 1, 2].map((k) => face[k] + out[k] * PX.offset * px + tan[k] * spread) as Vec3;
      it.at = at;

      const p = new T.Vector3(at[0], at[1], at[2]);
      const s = toScreen(p, cam, size);
      const q = toScreen(p.clone().add(new T.Vector3(out[0], out[1], out[2]).multiplyScalar(10 * px)), cam, size);
      const view = ortho ? fwd.clone() : p.clone().sub(eye).normalize();
      const along = out[0] * view.x + out[1] * view.y + out[2] * view.z;
      it.fade = endOnFade(along);
      // Lighter only for a face turned clearly AWAY — seen through the model. A face seen edge-on
      // (a side wall from the front, the roof from eye height) is on the outline, and perspective
      // tips it a hair past square; dimming it made three good handles look disabled.
      it.facing = along < 0.25;
      it.screen = { x: s.x, y: s.y, dx: q.x - s.x, dy: q.y - s.y, ok: !s.behind && it.fade > 0 };
      it.group.visible = it.screen.ok;
      if (!it.screen.ok) return;

      const hot = this.drag ? this.drag.index === i : this.hover === i;
      it.group.position.copy(p);
      it.group.scale.setScalar(px * (hot ? 1.15 : 1));
      it.disc.quaternion.copy(camQ);
      it.rim.quaternion.copy(camQ);
      const pose = arrowPose(it.screen.dx, it.screen.dy, 10);
      it.arrow.quaternion.copy(camQ).multiply(spin.setFromAxisAngle(zAxis, pose.angle));
      it.arrow.scale.set(pose.stretch, 1, 1);
      // A handle on the far side of the model is still drawn and still takes the pointer, but
      // lighter, so it reads as behind rather than as floating in front.
      const dim = it.fade * (this.drag && !hot ? 0.4 : 1) * (it.facing ? 1 : 0.55);
      // Opaque at full strength: a window behind a see-through pill tinted half of it.
      paint(it.mats.disc, hot ? COLOR.hot : COLOR.disc, dim);
      paint(it.mats.rim, hot ? COLOR.hotRim : COLOR.rim, 0.75 * dim);
      paint(it.mats.arrow, COLOR.arrow, dim);
      ranked.push({ it, depth });
    });

    // Farthest first, so where two handles overlap the nearer one is drawn whole on top.
    ranked.sort((a, b) => b.depth - a.depth);
    ranked.forEach(({ it }, r) => {
      const o = ORDER + r * 3;
      it.disc.renderOrder = o;
      it.rim.renderOrder = o + 1;
      it.arrow.renderOrder = o + 2;
    });
    if (this.hover >= 0 && !this.usable(this.items[this.hover])) this.setHover(-1);
    this.placeLabel();
  }

  /** The box a handle sits on: the named part's, else the whole asset's — of what is DRAWN, so a
   *  hidden part does not hold a handle out in empty air. */
  private boxFor(part: string, now: number): Box | null {
    const root = this.host.root();
    const target = (part && root ? this.resolve(part, root) : null) || root;
    const b = target ? visibleBox(target, this.box, this.tmp) : null;
    if (b) { this.held.set(part, { box: b, at: now }); return b; }
    // Nothing drawn under it: either the asset is empty, or it is between two builds — the old
    // parts gone, the new ones not in yet. Hold the last box for a beat, and for the whole of a
    // drag, so the handle under the hand does not blink out and back on every rebuild.
    const held = this.held.get(part);
    return held && (this.drag || now - held.at < HOLD_MS) ? held.box : null;
  }

  private resolve(part: string, root: any): any {
    let o: any = null;
    try { o = this.host.find(part); } catch { o = null; }
    // The editor's index is rebuilt after a build; an object from the previous build is detached.
    if (o && isUnder(o, root)) return o;
    return typeof root.getObjectByName === "function" ? root.getObjectByName(part) || null : null;
  }

  private placeLabel() {
    const i = this.drag ? this.drag.index : this.hover;
    const it = i >= 0 ? this.items[i] : null;
    if (!it || !this.usable(it) || !this.cb) { this.hideLabel(); return; }
    const value = this.drag ? this.drag.value : Number(this.cb.get(it.spec.param));
    const text = handleText(it.spec.label || it.spec.param, value, it.spec.snap);
    const el = this.ensureLabel();
    if (!el) return;
    if (el.textContent !== text) el.textContent = text;
    el.style.display = "block";
    // Above the handle, unless that would put it against the top of the viewport — where the view
    // bar floats, and where a roof handle goes once the building has grown a floor — then below.
    // And never past a side: a label cut in half is a label nobody can read.
    const at = labelPlace(it.screen.x, it.screen.y, PX.label * 1.15, el.offsetWidth || 0, this.host.size().w);
    el.style.left = Math.round(at.x) + "px";
    el.style.top = Math.round(at.y) + "px";
    el.style.transform = at.below ? "translate(-50%,0)" : "translate(-50%,-100%)";
  }

  private ensureLabel(): HTMLDivElement | null {
    if (this.label) return this.label;
    const parent = this.host.canvas.parentElement;
    if (!parent) return null;
    const el = document.createElement("div");
    el.dataset.paramHandleLabel = "";
    el.style.cssText = [
      "position:absolute", "display:none", "transform:translate(-50%,-100%)", "pointer-events:none",
      "z-index:4", "white-space:nowrap", "padding:2px 7px", "border-radius:5px",
      "font:500 11px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
      "color:#ffe7c2", "background:rgba(13,16,21,0.9)", "border:1px solid rgba(255,208,138,0.55)",
      "box-shadow:0 2px 8px rgba(0,0,0,0.45)", "font-variant-numeric:tabular-nums",
    ].join(";");
    parent.appendChild(el);
    this.label = el;
    return el;
  }

  private hideLabel() { if (this.label && this.label.style.display !== "none") this.label.style.display = "none"; }

  // ---------------------------------------------------------------- the pointer
  private attach() {
    if (this.listening) return;
    const c = this.host.canvas;
    c.addEventListener("pointerdown", this.onDown);
    c.addEventListener("pointermove", this.onMove);
    c.addEventListener("pointerup", this.onUp);
    c.addEventListener("pointercancel", this.onCancel);
    c.addEventListener("lostpointercapture", this.onLost);
    c.addEventListener("pointerleave", this.onLeave);
    this.listening = true;
  }

  private detach() {
    window.removeEventListener("keydown", this.onKey, true);
    if (!this.listening) return;
    const c = this.host.canvas;
    c.removeEventListener("pointerdown", this.onDown);
    c.removeEventListener("pointermove", this.onMove);
    c.removeEventListener("pointerup", this.onUp);
    c.removeEventListener("pointercancel", this.onCancel);
    c.removeEventListener("lostpointercapture", this.onLost);
    c.removeEventListener("pointerleave", this.onLeave);
    this.listening = false;
  }

  /** Pointer pixels inside the canvas, in the units the viewport projects to. */
  private local(e: PointerEvent): { x: number; y: number } {
    const r = this.host.canvas.getBoundingClientRect();
    const size = this.host.size();
    return {
      x: ((e.clientX - r.left) * size.w) / Math.max(1, r.width),
      y: ((e.clientY - r.top) * size.h) / Math.max(1, r.height),
    };
  }

  private pickAt(e: PointerEvent): number {
    const p = this.local(e);
    return pickHandle(this.items.map((it) => ({ x: it.screen.x, y: it.screen.y, ok: this.usable(it) })), p.x, p.y, PX.pick);
  }

  private rayAt(e: PointerEvent): { o: Vec3; d: Vec3 } {
    const p = this.local(e);
    const size = this.host.size();
    this.ray.setFromCamera(ndcOf(p.x, p.y, size.w, size.h), this.host.camera());
    const r = this.ray.ray;
    return { o: [r.origin.x, r.origin.y, r.origin.z], d: [r.direction.x, r.direction.y, r.direction.z] };
  }

  private onDown = (e: PointerEvent) => {
    if (this.drag) {
      // A second finger mid-drag is not a new press. The SAME pointer pressing again is proof its
      // last release was never heard: close that drag as a release, then treat this press afresh.
      if (e.pointerId !== this.drag.pointerId) { e.stopPropagation(); return; }
      this.endDrag("commit");
    }
    if (e.button !== 0 || e.ctrlKey || e.metaKey) return;
    if (!this.cb || !this.host.live() || this.host.busy()) return;
    let i = -1;
    try { this.layout(); i = this.pickAt(e); } catch { return; }
    if (i < 0) return;
    const it = this.items[i];
    let start = NaN;
    try { start = Number(this.cb.get(it.spec.param)); } catch { start = NaN; }
    if (!Number.isFinite(start) || !it.at) return;       // no number to start from: the press is not ours
    const ray = this.rayAt(e);
    const dir = outwardOf(it.spec.axis, it.spec.side);
    const s0 = closestOnAxis(it.at, dir, ray.o, ray.d);
    if (s0 === null) return;
    // THE PRESS IS OURS. Stopped here, at the canvas, it never reaches the editor's handlers on the
    // canvas's parent: no orbit, no click-select, no gizmo. Default not prevented, so focus and
    // the workspace's own mousedown bookkeeping happen exactly as for any press in the viewport.
    e.stopPropagation();
    e.stopImmediatePropagation();
    this.drag = { index: i, pointerId: e.pointerId, start, value: start, sets: 0, origin: it.at.slice() as Vec3, dir, s0 };
    try { this.host.canvas.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer */ }
    try { (this.host.canvas.closest("[tabindex]") as HTMLElement | null)?.focus({ preventScroll: true }); } catch { /* nothing to focus */ }
    window.addEventListener("keydown", this.onKey, true);
    this.setCursor(resizeCursor(it.screen.dx, it.screen.dy));
    this.placeLabel();
  };

  private onMove = (e: PointerEvent) => {
    const d = this.drag;
    if (d) {
      if (e.pointerId !== d.pointerId) return;
      e.stopPropagation();
      e.stopImmediatePropagation();
      // Blender's cancel: the other button, pressed while this one is held.
      if (e.buttons & 2) { this.endDrag("cancel"); return; }
      // No button held any more: it was let go somewhere this canvas never heard. That is a
      // release, and the drag keeps what it reached — throwing it away would punish the hand.
      if (e.buttons === 0) { this.endDrag("commit"); return; }
      this.dragTo(e);
      return;
    }
    // Hover. Never stopped: the editor needs every move for its own hover, brush and box.
    if (!this.cb || !this.host.live() || this.host.busy() || e.buttons !== 0) { this.setHover(-1); return; }
    let i = -1;
    try { i = this.pickAt(e); } catch { i = -1; }
    this.setHover(i);
  };

  private onUp = (e: PointerEvent) => {
    const d = this.drag;
    if (!d || e.pointerId !== d.pointerId) return;
    e.stopPropagation();
    e.stopImmediatePropagation();
    this.dragTo(e);
    this.endDrag("commit");
  };

  /** The browser gave up on the gesture (a touch taken over by scrolling): nothing happened. */
  private onCancel = (e: PointerEvent) => {
    const d = this.drag;
    if (d && e.pointerId === d.pointerId) this.endDrag("cancel");
  };

  /** Capture lost with no pointerup — Chrome does this when a move arrives with no button held,
   *  i.e. the button came up where we could not hear it. A release, not a cancel. (Our own
   *  release in endDrag clears the drag first, so it never lands here.) */
  private onLost = (e: PointerEvent) => {
    const d = this.drag;
    if (d && e.pointerId === d.pointerId) this.endDrag("commit");
  };

  private onLeave = () => { if (!this.drag) this.setHover(-1); };

  /** While a drag is live it owns the keyboard, the way the gizmo's modal does: Tab or G pressed
   *  mid-drag would change what is being edited under the hand. Escape puts the value back. */
  private onKey = (e: KeyboardEvent) => {
    if (!this.drag || MODIFIERS.has(e.key)) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    if (e.key === "Escape") this.endDrag("cancel");
  };

  private dragTo(e: PointerEvent) {
    const d = this.drag;
    if (!d || !this.cb) return;
    const it = this.items[d.index];
    const ray = this.rayAt(e);
    const s = closestOnAxis(d.origin, d.dir, ray.o, ray.d);
    if (s === null) return;
    const v = dragValue(d.start, s - d.s0, it.spec);
    if (v === d.value) return;
    d.value = v;
    d.sets++;
    this.cb.set(it.spec.param, v);
    this.placeLabel();
  }

  /** "commit": the drag becomes one edit — if it set anything; a click that moved nothing is not an
   *  edit. "cancel": the start value goes back and nothing is committed. null: the layer is going
   *  away, and says nothing to anyone. */
  private endDrag(how: "commit" | "cancel" | null) {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    window.removeEventListener("keydown", this.onKey, true);
    try {
      if (this.host.canvas.hasPointerCapture?.(d.pointerId)) this.host.canvas.releasePointerCapture(d.pointerId);
    } catch { /* already released */ }
    const it = this.items[d.index];
    try {
      if (it && this.cb) {
        if (how === "commit" && d.sets > 0) this.cb.commit(it.spec.param);
        else if (how === "cancel" && d.value !== d.start) this.cb.set(it.spec.param, d.start);
      }
    } finally {
      const h = this.hover;
      this.setCursor(h >= 0 && this.items[h] ? resizeCursor(this.items[h].screen.dx, this.items[h].screen.dy) : "");
      if (how) this.placeLabel(); else this.hideLabel();
    }
  }

  private setHover(i: number) {
    if (i === this.hover) return;
    this.hover = i;
    if (this.drag) return;
    const it = i >= 0 ? this.items[i] : null;
    this.setCursor(it ? resizeCursor(it.screen.dx, it.screen.dy) : "");
    this.placeLabel();
  }

  private setCursor(c: string) {
    if (c === this.cursor) return;
    this.cursor = c;
    try { this.host.canvas.style.cursor = c; } catch { /* no style to set */ }
  }
}

// ---------------------------------------------------------------- small helpers
const round2 = (n: number) => Math.round(n * 100) / 100;
const round4 = (n: number) => Math.round(n * 10000) / 10000;

function paint(m: any, color: number, opacity: number) {
  if (m.color.getHex() !== color) m.color.setHex(color);
  if (m.opacity !== opacity) m.opacity = opacity;
}

function toScreen(p: any, cam: any, size: { w: number; h: number }): { x: number; y: number; behind: boolean } {
  const v = p.clone().project(cam);
  return { x: ((v.x + 1) / 2) * size.w, y: ((1 - v.y) / 2) * size.h, behind: v.z > 1 || v.z < -1 };
}

function isUnder(o: any, root: any): boolean {
  for (let p = o; p; p = p.parent) if (p === root) return true;
  return false;
}

/** The world box of what is drawn under `root`: visible meshes, lines and points, each by its own
 *  cached bounding box — three's `expandByObject` without the hidden parts. */
function visibleBox(root: any, box: any, tmp: any): Box | null {
  box.makeEmpty();
  root.traverseVisible((o: any) => {
    const g = o.geometry;
    if (!g || !(o.isMesh || o.isLine || o.isPoints) || o.userData?.__shell) return;
    if (o.boundingBox !== undefined) {                 // instanced, batched and skinned meshes
      if (o.boundingBox === null) o.computeBoundingBox?.();
      if (!o.boundingBox) return;
      tmp.copy(o.boundingBox);
    } else {
      if (!g.boundingBox) g.computeBoundingBox?.();
      if (!g.boundingBox) return;
      tmp.copy(g.boundingBox);
    }
    if (tmp.isEmpty()) return;
    tmp.applyMatrix4(o.matrixWorld);
    box.union(tmp);
  });
  if (box.isEmpty()) return null;
  const { min, max } = box;
  if (![min.x, min.y, min.z, max.x, max.y, max.z].every(Number.isFinite)) return null;
  return { min: [min.x, min.y, min.z], max: [max.x, max.y, max.z] };
}
