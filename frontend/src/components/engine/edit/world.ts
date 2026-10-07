// The editable viewport: a lit studio you can fly around, select in, transform in, rig and pose.
//
// It is deliberately the SAME studio the forge photographs — the same three-point rig, the same
// environment, the same field of view and the same framing pass. If the window lit the asset even
// slightly differently from the sheet, then every judgement made here would have to be re-made
// there, and the editor would be a second opinion rather than the same one held closer.
//
// Three.js only, and it says so rather than half-working. PlayCanvas has no scene graph shaped
// like this one and no material model that survives the overlay passes; a second implementation
// would be a different editor wearing the same buttons.

import { buildFrom, importAny, openAsset, toObject, type AssetRef, type OpenResult } from "./assetOpen";
import { evalClip, type Clip, type Edits, type Mod, type PartOverride, type V3, type WorldOverride } from "./kit";
import { applyEdits, applyPlaced, applyVerts, makeOps, placedIdOf, restBuffer, sceneReport, stableKeys, type Defects, type Ops, type PcSnapshot, type SceneReport } from "./ops";
import { buildPcMirror, hideRevealed, revealPlan } from "./pcmirror";
import { groupOf, parsePieceKey, pieceAt, pieceKey, pieceVertices, piecesOf, type PieceMap } from "./pieces";
import { cameraOutline, extraIcon, extraKind, lightOutline, type ExtraKind } from "./overlay";
import {
  THEME, disposeTree, drop, facesMaterial, makeGrid, matcapMaterial, normalLines, normalsMaterial,
  outlineMaterial, silhouetteMaterial, solidColorMaterial, wireMaterial, type Grid,
} from "./overlay";
import { Gizmo, project, type Axis, type DragDelta, type GizmoMode } from "./gizmo";
import type { HandleSpec, ParamHandleCallbacks } from "./configurator";
import { clipPlanes, defaultFlySpeed, FLY_CODES, flyLook, flySpeedAfterWheel, flyStep, targetFromEye, zoomToward } from "./navmath";
import { describeObject } from "./objectLabel";
import { PaintEngine } from "./paintGpu";
import { ParamHandleLayer, type HandleInfo } from "./handles";
import {
  buildTopology, elemGroups, groupAt, groupRest, groupsInBox, nearestEdge, nearestGroup,
  selectionCounts, vertEditsOf, type EditTopology, type ElemKind, type Screen,
} from "./vertedit";
import { applyPose, bindParts, bindSkin, buildRig, highlightBone, solveChain, unbind, type Rig, type WeightMethod } from "./rig";
import { chunkGrid, chunksFor, heightAt, pickLod, scatterBatches, terrainChunk, terrainMesh,
  type ChunkRef, type MeshArrays, type ScatterBatch, type ScatterItem, type TerrainData } from "./terrain";

export type Shading = "wire" | "solid" | "material" | "rendered";
export type SolidLight = "studio" | "matcap" | "flat";
/** Where Solid gets its colour, which in Blender is a separate axis from the lighting.
 *  "material" keeps each part's own base colour and colour map; "single" is the one grey. */
export type SolidColor = "material" | "single";
/** Object moves a part, pose moves a bone, edit moves the mesh itself. Blender's three — and
 *  terrain, which is the one Blender does not have and Unity keeps in a component inspector. */
export type Mode = "object" | "pose" | "edit" | "terrain" | "paint";

export interface Overlays {
  grid: boolean;
  axes: boolean;
  outline: boolean;
  wireframe: boolean;
  faces: boolean;
  normals: boolean;
  origins: boolean;
  bones: boolean;
  bonesInFront: boolean;
  sizes: boolean;
  xray: boolean;
  /** Lights and cameras drawn as icons, so a thing with no surface can still be seen and picked. */
  extras: boolean;
}

/** The viewport's share of the Studio engine settings. Every field is optional; the defaults are
 *  what the editor did before there were settings. */
export interface WorldOptions {
  /** Device pixels per CSS pixel, at most. 0 or missing: the old cap of 2. */
  pixelRatio?: number;
  shadows?: boolean;
  fov?: number;
  orbitSpeed?: number;
  zoomSpeed?: number;
  invertOrbit?: boolean;
  /** "blender" (Blender and Godot: middle drag orbits) or "unity" (Alt+left orbits, middle pans). */
  navScheme?: "blender" | "unity";
  /** The wheel zooms toward the point under the cursor. False: toward the centre of the view. */
  zoomToCursor?: boolean;
}

export const DEFAULT_OVERLAYS: Overlays = {
  grid: true, axes: true, outline: true, wireframe: false, faces: false,
  normals: false, origins: false, bones: true, bonesInFront: true, sizes: false, xray: false, extras: true,
};

export interface PartInfo {
  key: string;
  name: string;
  type: string;
  depth: number;
  parent: string;
  meshes: number;
  triangles: number;
  visible: boolean;
  /** Switched off by the game right now, and shown here because it is part of the level the game
   *  culled by distance (see `revealPlan`). */
  revealed?: boolean;
  /** A piece split out of a merged mesh (see pieces.ts), not an object of the game's own. */
  piece?: boolean;
  /** Longest on-screen edge of the bounding box, in real pixels. The forge's "too small to
   *  judge" number, live: under about 24 px nothing about a part can honestly be assessed. */
  px: number;
  /** Where to hang a label, in CSS pixels. */
  at: { x: number; y: number; behind: boolean };
}

export interface Stats {
  objects: number;
  meshes: number;
  triangles: number;
  materials: number;
  drawCalls: number;
  bbox: V3 | null;
  fps: number;
  /** The first exception the frame loop caught, or "". An agent reading numbers sees it too. */
  error: string;
  /** Did the last whole frame draw anything at all? False with triangles present is the alarm. */
  drawn: boolean;
}

const FOV = 38;
const VIEWS: Record<string, V3> = {
  front: [0, 0, 1], back: [0, 0, -1], right: [1, 0, 0], left: [-1, 0, 0],
  top: [0, 1, 0], bottom: [0, -1, 0], "3q": [1.1, 0.75, 1.35], low: [0.9, 0.18, 1.3],
};

// ------------------------------------------------------------------ navigation
//
// Blender's keymap, because that is the one the user asked for and the one their hands already
// know: middle drag orbits, shift-middle pans, the wheel dollies. Alt+left is offered beside it
// so a trackpad or a two-button mouse is not locked out.

export class Nav {
  target: any;
  theta = 0.85;
  phi = 1.12;
  dist = 5;
  ortho = false;
  /** Set while a numpad view is being held, so the axis widget can show which one. */
  axisView = "";
  /** The key of a scene camera the viewport is looking through, or "". Any navigation leaves
   *  it, which is Blender's behaviour: orbit out of the camera and you are back in your own view. */
  through = "";
  /** Field of view and pointer speeds, from the Studio engine settings. These are the defaults. */
  fov = FOV;
  orbitSpeed = 1;
  zoomSpeed = 1;
  invertOrbit = false;
  /** Which hand the mouse is laid out for. "blender" is Blender's and Godot's: middle drag
   *  orbits, Shift+middle pans, Ctrl+middle dollies. "unity" is Unity's: Alt+left orbits, middle
   *  drag pans, Alt+right dollies. The right button flies in both; the editor handles that. */
  scheme: "blender" | "unity" = "blender";
  private drag: { mode: "orbit" | "pan" | "dolly"; x: number; y: number } | null = null;

  constructor(private T: any, private onChange: () => void) {
    this.target = new T.Vector3();
  }

  down(e: PointerEvent, allowLeftOrbit: boolean): boolean {
    let mode: "orbit" | "pan" | "dolly" | null = null;
    if (this.scheme === "unity") {
      if (e.button === 1) mode = e.ctrlKey ? "dolly" : "pan";
      else if (e.button === 0 && e.altKey && allowLeftOrbit) mode = "orbit";
      else if (e.button === 2 && e.altKey) mode = "dolly";
    } else if (e.button === 1) {
      mode = e.shiftKey ? "pan" : e.ctrlKey ? "dolly" : "orbit";
    } else if (allowLeftOrbit && e.button === 0 && e.altKey) {
      mode = "orbit";
    }
    if (!mode) return false;
    this.drag = { mode, x: e.clientX, y: e.clientY };
    return true;
  }

  move(e: PointerEvent, height: number): boolean {
    if (!this.drag) return false;
    const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
    this.drag.x = e.clientX; this.drag.y = e.clientY;
    if (this.drag.mode === "orbit") this.orbit(dx, dy);
    else if (this.drag.mode === "pan") this.pan(dx, dy, height);
    else this.zoom(dy * 4);
    return true;
  }

  up() { this.drag = null; }
  get dragging() { return !!this.drag; }

  /** Turntable orbit, in pixels of pointer travel. The viewport and the navigation gizmo both
   *  call this, so a drag on either turns the world the same way. */
  orbit(dx: number, dy: number) {
    this.leave();
    const k = 0.008 * this.orbitSpeed * (this.invertOrbit ? -1 : 1);
    this.theta -= dx * k;
    this.phi = Math.min(Math.PI - 0.015, Math.max(0.015, this.phi - dy * k));
    this.axisView = "";
    this.onChange();
  }

  /** Set by the world: called when a move is about to leave a scene camera, to park the orbit
   *  where that camera stands. Every move used to just drop the camera, and the view jumped to
   *  wherever the orbit had been left — a mirrored game opens through its own camera, so the first
   *  wheel notch or drag of every session threw the view somewhere else. */
  leaving: (() => void) | null = null;

  private leave() {
    if (!this.through) return;
    try { this.leaving?.(); } catch { /* the move still happens; only the start point is lost */ }
    this.through = "";
  }

  zoom(deltaY: number) {
    this.leave();
    // Multiplicative, so one wheel step is the same fraction of the distance at any scale — the
    // difference between reaching a claw in three steps and in thirty.
    this.dist = Math.max(1e-5, Math.min(1e7, this.dist * Math.exp(deltaY * 0.0012 * this.zoomSpeed)));
    this.onChange();
  }

  /** One wheel event toward `pivot` — the point under the cursor — or toward the centre when there
   *  is none. The wheel used to dolly toward the orbit centre only, and in a whole-level scene that
   *  centre is a spot on the ground a hundred metres off: no amount of scrolling reached the dino
   *  under the cursor. The maths is navmath.zoomToward, tested there. */
  zoomToward(deltaY: number, pivot: any | null) {
    this.leave();
    const r = zoomToward([this.target.x, this.target.y, this.target.z], this.dist, deltaY, this.zoomSpeed,
      pivot ? [pivot.x, pivot.y, pivot.z] : null);
    this.target.set(r.target[0], r.target[1], r.target[2]);
    this.dist = r.dist;
    this.axisView = "";
    this.onChange();
  }

  // ---- fly: hold the right button, look with the mouse, W A S D / Q E to move ------------------
  //
  // The way Unity, Godot and Unreal all move through a level, and what a hand trained on them
  // reaches for first. The keys only mean "move" while the button is held, so every Blender
  // shortcut on those letters (S scale, A select all, G grab) still works the moment it is let go.
  flying = false;
  /** Metres per second; 0 until the world picks one from the scene's size. The wheel changes it
   *  while flying, and it is kept for the next flight. */
  flySpeed = 0;
  readonly flyKeys = new Set<string>();

  flyStart() {
    this.flying = true;
    this.flyKeys.clear();
  }

  /** Mouse-look: the view turns, the eye stays put. */
  flyLook(dx: number, dy: number) {
    const eye = this.position();
    const a = flyLook(this.theta, this.phi, dx, dy, this.orbitSpeed, this.invertOrbit);
    this.theta = a.theta;
    this.phi = a.phi;
    const t = targetFromEye([eye.x, eye.y, eye.z], this.dist, this.theta, this.phi);
    this.target.set(t[0], t[1], t[2]);
    this.onChange();
  }

  /** A key went down or up. True when it was a flying key and has been taken. */
  flyKey(code: string, down: boolean): boolean {
    if (!this.flying || !FLY_CODES.has(code)) return false;
    if (down) this.flyKeys.add(code);
    else this.flyKeys.delete(code);
    return true;
  }

  /** One frame of flight. The world calls this every frame; it does nothing unless a key is held. */
  flyTick(dt: number): boolean {
    if (!this.flying || !this.flyKeys.size) return false;
    const d = flyStep(this.flyKeys, this.theta, this.phi, this.flySpeed || 5, dt);
    if (!d[0] && !d[1] && !d[2]) return false;
    this.target.x += d[0];
    this.target.y += d[1];
    this.target.z += d[2];
    this.onChange();
    return true;
  }

  flyEnd() {
    this.flying = false;
    this.flyKeys.clear();
  }

  pan(dx: number, dy: number, height: number) {
    this.leave();
    const T = this.T;
    const per = (2 * this.dist * Math.tan((this.fov * Math.PI) / 360)) / Math.max(1, height);
    const p = this.position();
    const f = this.target.clone().sub(p).normalize();
    const r = f.clone().cross(new T.Vector3(0, 1, 0)).normalize();
    const u = r.clone().cross(f).normalize();
    this.target.add(r.multiplyScalar(-dx * per)).add(u.multiplyScalar(dy * per));
    this.onChange();
  }

  position(): any {
    const s = Math.sin(this.phi);
    return new this.T.Vector3(
      this.target.x + this.dist * s * Math.sin(this.theta),
      this.target.y + this.dist * Math.cos(this.phi),
      this.target.z + this.dist * s * Math.cos(this.theta),
    );
  }

  look(dir: V3, name = "") {
    this.leave();
    const len = Math.hypot(dir[0], dir[1], dir[2]) || 1;
    const d = [dir[0] / len, dir[1] / len, dir[2] / len];
    this.phi = Math.acos(Math.min(1, Math.max(-1, d[1])));
    // Straight down or up has no azimuth; keep the one we had so the view does not spin.
    if (Math.abs(d[1]) < 0.999) this.theta = Math.atan2(d[0], d[2]);
    this.axisView = name;
    this.onChange();
  }

  /** Blender's numpad 4/6/8/2: fifteen degrees at a time. */
  nudge(dTheta: number, dPhi: number) {
    this.leave();
    this.theta += dTheta;
    this.phi = Math.min(Math.PI - 0.015, Math.max(0.015, this.phi + dPhi));
    this.axisView = "";
    this.onChange();
  }

  /** Park the orbit where a camera is, so leaving its view continues from its viewpoint rather
   *  than snapping back to wherever the orbit was before. */
  adopt(position: any, forward: any) {
    const f = forward.clone().normalize();
    this.target.copy(position).add(f.multiplyScalar(this.dist));
    this.phi = Math.acos(Math.min(1, Math.max(-1, -f.y / (f.length() || 1))));
    const d = position.clone().sub(this.target);
    if (Math.hypot(d.x, d.z) > 1e-6) this.theta = Math.atan2(d.x, d.z);
    this.phi = Math.acos(Math.min(1, Math.max(-1, d.y / (d.length() || 1))));
  }

  frame(center: any, radius: number, margin = 1.4) {
    // Home and F used to change nothing on screen while looking through a camera: the orbit moved
    // behind a view that ignored it. Leaving first frames from where the camera was looking.
    this.leave();
    this.target.copy(center);
    this.dist = Math.max(1e-4, (radius / Math.tan((this.fov * Math.PI) / 360)) * margin);
    this.onChange();
  }
}

// ------------------------------------------------------------------ the world
export interface WorldEvents {
  onSelect(keys: string[], active: string): void;
  /** A vert edit hands back the COMPLETE list for that mesh, not a delta, so the document is
   *  replaced wholesale and applying it twice cannot drift. */
  onEdit(kind: "part" | "pose" | "vert", key: string, value: any): void;
  onNav(): void;
  /** The scene gained or lost an object. Not a transform — those already arrive as edits — but a
   *  change to WHAT IS THERE, which the sidecar and the outliner both have to hear about. */
  onScene?(): void;
}

const r4 = (n: number) => Math.round(n * 10000) / 10000;

export class EditWorld {
  readonly T: any;
  readonly canvas: HTMLCanvasElement;
  readonly nav: Nav;
  readonly gizmo: Gizmo;
  /** The same operations an agent gets in its own code, so the editor and the asset agree. */
  readonly ops: Ops;

  scene: any;
  subject: any;
  gizmoScene: any;
  rig: Rig | null = null;

  // Material preview, not Blender's Solid. Blender opens on Solid because a production scene is
  // too heavy to shade while you work; these assets are small, and their colour IS the design, so
  // opening grey hides most of what there is to look at.
  shading: Shading = "material";
  solidLight: SolidLight = "studio";
  solidColor: SolidColor = "material";
  /** One Solid stand-in per real material, kept so a subject of 86 meshes over 13 materials
   *  builds 13 of them once instead of 86 every frame. Cleared when the subject is replaced. */
  private solidMats = new Map<any, { lit?: any; flat?: any; line?: any; points?: any }>();
  overlays: Overlays = { ...DEFAULT_OVERLAYS };
  mode: Mode = "object";

  // ---- edit mode. `editKey` is the MESH being edited, not the named part above it: a position
  // only means one thing inside the geometry that holds it. `vsel` holds group ordinals, which
  // are corners rather than vertices, for the reason set out in vertedit.ts.
  elem: ElemKind = "vert";
  editKey = "";
  vsel = new Set<number>();
  private topo: EditTopology | null = null;
  private editObj: any = null;
  private editGroup: any = null;
  private editDots: any = null;
  private editLines: any = null;
  private editFaces: any = null;
  private vert0: Map<number, V3> | null = null;
  private vertPivot0: V3 | null = null;
  /** How near the cursor has to be, in pixels. A vertex is a dot; the hand means "that one". */
  pickRadius = 12;
  /** The ground, when there is one. Never inside the subject: a level is ground AND the props
   *  standing on it, and hiding one to work on the other is how a tree ends up buried. */
  terrain: TerrainView | null = null;
  selection = new Set<string>();
  active = "";
  boneSelection = new Set<string>();
  activeBone = "";
  /** Frame the animation is parked on, so a rebuild lands back where the user was. */
  frame = 0;
  clip: Clip | null = null;

  /** Use the asset's own lights and world instead of the studio's, when it has them. Blender's
   *  "Scene Lights" and "Scene World" toggles, and on by default for the same reason: a scene
   *  photographed under a rig it never asked for is not the scene. */
  sceneLights = true;
  sceneWorld = true;

  private renderer: any;
  private persp: any;
  private orthoCam: any;
  private lights: any;
  private grid: Grid;
  /** Icons for lights and cameras, in the scene but outside the subject so no op ever merges them. */
  private extras: any;
  private extraItems: Array<{ key: string; obj: any; sprite: any; lines: any | null }> = [];
  private assetLights: any[] = [];
  private assetWorld: { background: any; fog: any; environment: any } | null = null;
  private studioWorld: { background: any; fog: any; environment: any } | null = null;
  private viewCam: any = null;
  private frameRect: { x: number; y: number; w: number; h: number } | null = null;
  /** The saved world edit, previewed on top of the asset's own. */
  worldEdit: WorldOverride | null = null;
  /** Inverse kinematics reach in pose mode: how many bones above the selected one may follow a
   *  drag of its tip. 0 is off, and the move gizmo then does nothing to a bone, as before. */
  ikDepth = 0;
  private worldCache = { bgHex: "", bg: null as any, fogKey: "", fog: null as any };
  private ikChain: string[] = [];
  private ikTail0: any = null;
  private poseStart: Record<string, V3> | null = null;
  /** A mirror of a running game. Keys are then taken from the game's own Scene, not from the
   *  editor's holder around it, so a key written here is the key `applyEdits` finds in the game. */
  isLive = false;
  /** Paint on the model's textures (paintGpu.ts). Made the first time paint mode opens, or when a
   *  saved paint file is loaded, and kept for the asset's life: the painted look is part of the
   *  asset in every mode, not only while the brush is out. */
  paint: PaintEngine | null = null;

  /** The object whose children own the keys: the game's own root when live (a three Scene, or
   *  the mirror of a PlayCanvas root), else the holder. */
  keyRoot(): any {
    const kids = this.subject.children;
    return this.isLive && kids.length === 1 ? kids[0] : this.subject;
  }

  /** The paint engine, made on first use and bound to this viewport. */
  ensurePaint(): PaintEngine {
    if (this.paint) return this.paint;
    this.paint = new PaintEngine({
      T: this.T,
      renderer: this.renderer,
      camera: () => this.camera,
      viewport: () => this.size,
      subject: () => this.subject,
      mirrorRoot: () => this.keyRoot(),
      keyOf: (o: any) => this.keys.get(o) || "",
      objOf: (k: string) => this.objs.get(k) || null,
      materialsChanged: () => this.dropSolidMats(),
    });
    return this.paint;
  }

  /** Find the painted textures again: after a rebuild AND its modifier stack, because a step such
   *  as Unwrap changes the very UVs the paint sits on. Nothing happens while nobody has painted. */
  paintScan(): ReturnType<PaintEngine["scan"]> | null {
    if (!this.paint) return null;
    try { return this.paint.scan(); } catch { return null; }
  }

  /**
   * A PlayCanvas asset or game, mirrored: the snapshot comes from `pcSnapshot` — run in the
   * Studio's own hidden app for a file, or inside the game's page for a live game — and becomes
   * a three tree here. Everything the editor does then works on the mirror, and the sidecar it
   * writes is applied to the real entities by `pcApply`, keyed the same way.
   */
  loadPcSnapshot(snap: PcSnapshot): { error: string } {
    for (const c of this.subject.children.slice()) drop(this.subject, c);
    this.clearRig();
    this.beforeBuild();
    try {
      const m = buildPcMirror(this.T, snap, { aspect: this.size.w / Math.max(1, this.size.h) });
      this.subject.add(m.root);
      this.afterBuild(m.root);
      if (m.world) this.assetWorld = m.world;
      return { error: "" };
    } catch (e: any) {
      this.afterBuild(null);
      return { error: String(e?.message || e).slice(0, 800) };
    }
  }

  /** The world position of a bone's tail: where inverse kinematics takes hold. */
  private boneTail(b: any): any {
    const T = this.T;
    const spec = this.rig?.specs.find((s) => s.name === b.name);
    const len = spec ? Math.hypot(spec.tail[0] - spec.head[0], spec.tail[1] - spec.head[1], spec.tail[2] - spec.head[2]) : 1;
    b.updateWorldMatrix(true, false);
    return b.localToWorld(new T.Vector3(0, len, 0));
  }
  private normalsObj: any = null;
  private originsObj: any = null;
  private mats: Record<string, any> = {};
  private keys = new Map<any, string>();
  private objs = new Map<string, any>();
  private baseXf = new Map<string, { pos: V3; rot: V3; scale: V3 }>();
  private ray: any;
  private size = { w: 1, h: 1 };
  private ev: WorldEvents;
  private frameTimes: number[] = [];
  private lastRender = 0;
  private drag0: Map<string, { pos: V3; rot: V3; scale: V3 }> | null = null;
  private pose0: Record<string, V3> | null = null;

  private opts: WorldOptions = {};
  /** The parameter handles, while an asset declares some (handles.ts). Null means none are drawn
   *  and none are listening. */
  private handleLayer: ParamHandleLayer | null = null;

  constructor(T: any, canvas: HTMLCanvasElement, background: string, ev: WorldEvents, opts: WorldOptions = {}) {
    this.T = T;
    this.canvas = canvas;
    this.ev = ev;
    this.opts = { ...opts };
    this.ops = makeOps(T);
    this.ray = new T.Raycaster();

    const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: false, stencil: false });
    renderer.setPixelRatio(this.pixelRatio());
    if (renderer.outputColorSpace !== undefined && T.SRGBColorSpace) renderer.outputColorSpace = T.SRGBColorSpace;
    // Shadows are on (unless the settings say otherwise) so a scene that asks for them gets them.
    // The studio rig casts none, so an asset that never mentions shadows renders exactly as it did.
    if (renderer.shadowMap) { renderer.shadowMap.enabled = opts.shadows !== false; if (T.PCFSoftShadowMap !== undefined) renderer.shadowMap.type = T.PCFSoftShadowMap; }
    this.renderer = renderer;

    const scene = new T.Scene();
    scene.background = new T.Color(background);
    this.scene = scene;

    this.persp = new T.PerspectiveCamera(opts.fov || FOV, 1, 0.01, 20000);
    this.orthoCam = new T.OrthographicCamera(-1, 1, 1, -1, -10000, 20000);

    // The forge rig, member for member. Changing a light here would silently invalidate every
    // comparison between this window and a contact sheet.
    const lights = new T.Group();
    lights.name = "__lights";
    const key = new T.DirectionalLight(0xffffff, 2.6); key.position.set(3, 5, 4);
    const fill = new T.DirectionalLight(0xbcd0ff, 0.9); fill.position.set(-4, 1.5, 3);
    const rim = new T.DirectionalLight(0xffe6c0, 1.6); rim.position.set(-2, 3, -5);
    const amb = new T.HemisphereLight(0xa8c4ff, 0x40332a, 0.7);
    lights.add(key, fill, rim, amb);
    scene.add(lights);
    this.lights = lights;
    try {
      const pm = new T.PMREMGenerator(renderer);
      const eq = new T.CanvasTexture(envCanvas());
      eq.mapping = T.EquirectangularReflectionMapping;
      scene.environment = pm.fromEquirectangular(eq).texture;
      eq.dispose();
      pm.dispose();
    } catch { /* an engine without PMREM still gets the three-point rig */ }

    this.subject = new T.Group();
    this.subject.name = "forge-subject";
    scene.add(this.subject);
    this.studioWorld = { background: scene.background, fog: scene.fog, environment: scene.environment };

    this.extras = new T.Group();
    this.extras.name = "__extras";
    scene.add(this.extras);

    this.grid = makeGrid(T);
    scene.add(this.grid.mesh);

    this.mats = {
      wire: wireMaterial(T),
      wireOverlay: wireMaterial(T, 0x0c1016, 0.28),
      faces: transparentFaces(T),
      normals: normalsMaterial(T),
      matcap: matcapMaterial(T),
      flat: silhouetteMaterial(T, 0x9aa6b8),
      solid: new T.MeshStandardMaterial({ color: 0xb9c3d0, roughness: 0.62, metalness: 0.02 }),
      outline: outlineMaterial(T, THEME.select),
      outlineActive: outlineMaterial(T, THEME.active),
    };

    this.nav = new Nav(T, () => { this.syncCamera(); this.ev.onNav(); });
    this.nav.leaving = () => this.parkAtCamera();
    this.tuneNav(opts);
    this.gizmoScene = new T.Scene();
    this.gizmo = new Gizmo(T, {
      onChange: (d) => this.previewDelta(d),
      onCommit: (d) => this.commitDelta(d),
      onCancel: () => this.revertDelta(),
      snapStep: () => this.grid.cell / 10,
    });
    this.gizmoScene.add(this.gizmo.root);
    this.syncCamera();
  }

  // ------------------------------------------------------------------ camera
  get camera(): any { return this.nav.through && this.viewCam ? this.viewCam : this.nav.ortho ? this.orthoCam : this.persp; }

  /** The scene's own cameras, by key, in the order the code made them. */
  cameras(): string[] {
    const out: string[] = [];
    for (const [k, o] of this.objs) if (o.isCamera) out.push(k);
    return out;
  }

  /** Look through a scene camera (numpad 0). With no key: the selected camera, else the first.
   *  A second call on the same camera leaves it, which is how the key toggles. */
  lookThrough(key = ""): boolean {
    let k = key;
    if (!k) {
      const cams = this.cameras();
      if (!cams.length) return false;
      k = cams.find((c) => this.selection.has(c)) || cams[0];
    }
    if (this.nav.through === k) { this.leaveCamera(); return false; }
    const cam = this.objs.get(k);
    if (!cam?.isCamera) return false;
    this.nav.through = k;
    this.syncCamera();
    this.ev.onNav();
    return true;
  }

  /** Look through the camera a mirrored game brought with it (marked by the snapshot), if any.
   *  Stays put when already looking through it, so a refresh does not toggle out. */
  lookThroughGameCamera(): boolean {
    for (const [k, o] of this.objs) {
      if (o?.isCamera && o.userData?.studioGameCamera) {
        if (this.nav.through === k) return true;
        return this.lookThrough(k);
      }
    }
    return false;
  }

  leaveCamera() {
    // Continue from where the camera was, not from where the orbit was parked before.
    if (this.nav.through) this.parkAtCamera();
    this.nav.through = "";
    this.syncCamera();
    this.ev.onNav();
  }

  /** Leave any scene camera (parked where it stood), then take the editor's own lens back. Home and
   *  F call this first; in the other order the parking would put the camera's lens back on. */
  private ownLens() {
    if (this.nav.through) { this.parkAtCamera(); this.nav.through = ""; }
    this.nav.fov = this.opts.fov || FOV;
  }

  /** Put the orbit exactly where the scene camera being looked through stands, turning round what
   *  it looks at: the surface in the middle of its view, else a tenth of the scene ahead. Read while
   *  the view is still the camera's, so the ray is the camera's own. */
  private parkAtCamera() {
    const cam = this.nav.through ? this.objs.get(this.nav.through) : null;
    if (!cam) return;
    const T = this.T;
    const hit = this.firstHit({ x: 0, y: 0 }, true);
    this.nav.dist = hit && hit.distance > 1e-3 ? hit.distance : Math.max(1, this.radiusNow() * 0.1);
    // Its lens too, while perspective: a view that changed lens as it left the camera would still
    // jump, 17% on Dino Smash (a 43.8° view against the editor's 38°). Home and F give the editor
    // its own lens back, since they reframe the view anyway.
    if (!cam.isOrthographicCamera && this.persp.fov > 1) this.nav.fov = this.persp.fov;
    cam.updateWorldMatrix(true, false);
    this.nav.adopt(cam.getWorldPosition(new T.Vector3()), cam.getWorldDirection(new T.Vector3()));
  }

  /** Where the camera's frame sits in the viewport while looking through it, in CSS pixels, so
   *  the shell can darken everything outside it — Blender's passepartout. Null otherwise. */
  cameraFrame(): { x: number; y: number; w: number; h: number; name: string } | null {
    if (!this.nav.through || !this.frameRect) return null;
    const cam = this.objs.get(this.nav.through);
    return { ...this.frameRect, name: cam?.name || this.nav.through };
  }
  /** One square of the floor, in world units. It changes as you zoom, so the readout has to
   *  come from the grid rather than be assumed. */
  get gridCell(): number { return this.grid.cell; }

  resize(w: number, h: number) {
    this.size = { w: Math.max(1, w), h: Math.max(1, h) };
    this.renderer.setPixelRatio(this.pixelRatio());
    this.renderer.setSize(this.size.w, this.size.h, false);
    this.syncCamera();
  }

  /** Drag handles bound to parameters (configurator.ts). `null` removes every handle.
   *
   *  The drawing and the pointer live in handles.ts. A handle is drawn in the gizmo's scene, over
   *  everything; it sits on the drawn box of the model, re-placed every frame, so after every
   *  rebuild it is on the new box; and a press on it is taken before orbit, selection and the
   *  gizmo. Handing the same list again only swaps the callbacks, so a drag survives a re-render.
   *  With none set there is no layer at all: nothing drawn, nothing listening. */
  setParamHandles(handles: HandleSpec[] | null, cb?: ParamHandleCallbacks): void {
    if (!handles || !handles.length || !cb) {
      this.handleLayer?.dispose();
      this.handleLayer = null;
      return;
    }
    if (!this.handleLayer) {
      this.handleLayer = new ParamHandleLayer(this.T, {
        canvas: this.canvas,
        scene: this.gizmoScene,
        camera: () => this.camera,
        size: () => this.size,
        root: () => this.subject,
        find: (name) => this.objs.get(name) || null,
        live: () => this.mode === "object",
        busy: () => this.gizmo.dragging,
      });
    }
    this.handleLayer.set(handles, cb);
  }

  /** Where each parameter handle is on screen, in canvas and page pixels — what the review harness
   *  and an agent on the live link aim a drag with. Empty when no handles are set. */
  paramHandleInfo(): HandleInfo[] { return this.handleLayer ? this.handleLayer.info() : []; }

  /** The Studio engine settings applied to a running world: pixel ratio, shadows, field of view
   *  and pointer speeds. Called when the person changes a setting while the editor is open. */
  applyOptions(o: WorldOptions) {
    this.opts = { ...this.opts, ...o };
    this.tuneNav(this.opts);
    this.persp.fov = this.opts.fov || FOV;
    this.persp.updateProjectionMatrix();
    if (this.renderer.shadowMap) {
      const on = this.opts.shadows !== false;
      if (this.renderer.shadowMap.enabled !== on) {
        this.renderer.shadowMap.enabled = on;
        // A material compiled without shadows stays that way until it is told otherwise.
        this.scene.traverse((m: any) => {
          const mats = m.material ? (Array.isArray(m.material) ? m.material : [m.material]) : [];
          for (const mt of mats) mt.needsUpdate = true;
        });
      }
    }
    this.resize(this.size.w, this.size.h);
  }

  private tuneNav(o: WorldOptions) {
    this.nav.fov = o.fov || FOV;
    this.nav.orbitSpeed = o.orbitSpeed || 1;
    this.nav.zoomSpeed = o.zoomSpeed || 1;
    this.nav.invertOrbit = !!o.invertOrbit;
    this.nav.scheme = o.navScheme === "unity" ? "unity" : "blender";
  }

  private pixelRatio(): number {
    const device = window.devicePixelRatio || 1;
    const cap = this.opts.pixelRatio === 0 ? device : (this.opts.pixelRatio || 2);
    return Math.min(cap, device);
  }

  syncCamera() {
    const T = this.T;
    if (this.nav.through) {
      if (this.syncThrough()) return;
      this.nav.through = "";
    }
    this.frameRect = null;
    this.viewCam = null;
    const pos = this.nav.position();
    const aspect = this.size.w / this.size.h;
    for (const cam of [this.persp, this.orthoCam]) {
      cam.position.copy(pos);
      cam.up.set(0, 1, 0);
      cam.lookAt(this.nav.target);
    }
    this.persp.aspect = aspect;
    // The editor's own lens: a view through a scene camera set the camera's, and leaving it kept it.
    this.persp.fov = this.nav.fov;
    // Near follows the orbit distance so it stays tiny up close; far always reaches past the whole
    // scene, so a camera flown to one end of an island still sees the other (navmath.clipPlanes).
    const clip = clipPlanes(this.nav.dist, this.sceneRadius);
    this.persp.near = clip.near;
    this.persp.far = clip.far;
    this.persp.updateProjectionMatrix();
    // The orthographic frustum is derived from the perspective one at the target, so numpad 5
    // changes the projection without changing what fills the frame.
    const halfH = this.nav.dist * Math.tan((this.nav.fov * Math.PI) / 360);
    this.orthoCam.left = -halfH * aspect;
    this.orthoCam.right = halfH * aspect;
    this.orthoCam.top = halfH;
    this.orthoCam.bottom = -halfH;
    this.orthoCam.near = -this.nav.dist * 100 - 100;
    this.orthoCam.far = this.nav.dist * 200 + 100;
    this.orthoCam.updateProjectionMatrix();
    this.camera.updateMatrixWorld(true);
    void T;
  }

  /**
   * The viewport as the scene camera sees it.
   *
   * Not by rendering with that camera: its aspect is the game's, not the viewport's, and picking,
   * labels and the gizmo all project through `this.camera`. Instead the view camera copies its
   * pose, near and far, and takes a field of view widened so that the camera's own frame — fitted
   * inside the viewport, letterboxed — spans exactly the camera's field of view. Everything then
   * projects correctly, and the shell darkens what lies outside the frame.
   */
  private syncThrough(): boolean {
    const T = this.T;
    const cam = this.objs.get(this.nav.through);
    if (!cam?.isCamera) return false;
    cam.updateWorldMatrix(true, false);
    const W = this.size.w, H = this.size.h;
    const camAspect = cam.isOrthographicCamera
      ? (cam.right - cam.left) / ((cam.top - cam.bottom) || 1)
      : (cam.aspect || W / H);
    const fw = Math.min(W, H * camAspect), fh = fw / camAspect;
    this.frameRect = { x: (W - fw) / 2, y: (H - fh) / 2, w: fw, h: fh };
    const pos = new T.Vector3(), quat = new T.Quaternion(), scl = new T.Vector3();
    cam.matrixWorld.decompose(pos, quat, scl);
    const grow = H / fh;   // the viewport shows this much more than the frame, vertically
    if (cam.isOrthographicCamera) {
      const v = this.orthoCam;
      const zoom = cam.zoom || 1;
      const halfH = ((cam.top - cam.bottom) / 2 / zoom) * grow;
      v.position.copy(pos); v.quaternion.copy(quat);
      v.top = halfH; v.bottom = -halfH; v.left = -halfH * (W / H); v.right = halfH * (W / H);
      v.near = cam.near; v.far = cam.far;
      v.updateProjectionMatrix();
      v.updateMatrixWorld(true);
      this.viewCam = v;
    } else {
      const v = this.persp;
      const half = Math.tan(((cam.fov || 50) * Math.PI) / 360) / (cam.zoom || 1);
      v.position.copy(pos); v.quaternion.copy(quat);
      v.fov = (Math.atan(half * grow) * 360) / Math.PI;
      v.aspect = W / H;
      v.near = Math.max(1e-4, cam.near || 0.1); v.far = cam.far || 2000;
      v.updateProjectionMatrix();
      v.updateMatrixWorld(true);
      this.viewCam = v;
    }
    return true;
  }

  setView(name: string) {
    const d = VIEWS[name];
    if (d) this.nav.look(d, name);
  }

  /** Take the angle a forge call rendered from: `az=35,el=12` or `az=35,el=12,zoom=2`.
   *
   *  The forge aims with a direction vector — sin(az)cos(el), sin(el), cos(az)cos(el) — and this
   *  viewport orbits with a polar pair, so the two are the same camera written two ways:
   *  theta IS the azimuth, and phi is a quarter turn minus the elevation. Converting rather than
   *  approximating matters, because the whole point is to stand exactly where the agent stood and
   *  see what it saw before it decided something. */
  aimSpec(spec: string): boolean {
    const t = String(spec || "");
    if (!t.includes("=")) return false;
    let az = NaN, el = 0, zoom = 1;
    for (const kv of t.split(/[,;\s]+/)) {
      const [k, v] = kv.split("=");
      const n = parseFloat(v);
      if (!Number.isFinite(n)) continue;
      const key = (k || "").trim().toLowerCase();
      if (key === "az" || key === "azimuth") az = n;
      else if (key === "el" || key === "elev" || key === "elevation") el = n;
      else if (key === "zoom" || key === "z") zoom = n;
    }
    if (!Number.isFinite(az)) return false;
    const D = Math.PI / 180;
    this.nav.theta = az * D;
    this.nav.phi = Math.min(Math.PI - 0.015, Math.max(0.015, Math.PI / 2 - el * D));
    this.frameAll(1.4 / Math.max(0.2, Math.min(20, zoom)));
    return true;
  }

  frameAll(margin = 1.4) {
    const b = this.boundsOf(this.subject);
    if (!b) return;
    this.sceneRadius = b.radius;
    this.ownLens();
    this.nav.frame(b.center, b.radius, margin);
  }

  /** The radius of the whole scene, sky shells left out — measured by the last frame-all. Read by
   *  the clipping planes and the flying speed, so neither is guessed from the orbit distance. */
  sceneRadius = 0;

  private radiusNow(): number {
    if (!this.sceneRadius) {
      const b = this.boundsOf(this.subject);
      this.sceneRadius = b ? b.radius : 5;
    }
    return this.sceneRadius;
  }

  /** The last wheel's pivot. A burst of wheel events keeps one pivot while the cursor stays put:
   *  zooming toward a point leaves that point under the same pixel, so asking again would only pay
   *  for the raycast twenty times to get the same answer. */
  private wheelPivot: { at: number; px: number; py: number; point: any | null } | null = null;

  /** A wheel event over the viewport: zoom toward what is under the cursor. While flying, the
   *  wheel changes the flying speed instead (Unity's rule). */
  zoomAt(ndc: { x: number; y: number; px: number; py: number }, deltaY: number) {
    if (this.nav.flying) {
      this.nav.flySpeed = flySpeedAfterWheel(this.nav.flySpeed || defaultFlySpeed(this.radiusNow()), deltaY);
      this.ev.onNav();
      return;
    }
    if (this.opts.zoomToCursor === false) { this.nav.zoom(deltaY); return; }
    const now = performance.now();
    const c = this.wheelPivot;
    const reuse = !!c && now - c.at < 350 && Math.hypot(c.px - ndc.px, c.py - ndc.py) < 4;
    const point = reuse ? c!.point : this.cursorPoint(ndc);
    this.wheelPivot = { at: now, px: ndc.px, py: ndc.py, point };
    this.nav.zoomToward(deltaY, point);
  }

  /** What a wheel zooms toward: the nearest visible surface under the cursor, sky shells skipped;
   *  over empty sky, the point at the orbit centre's depth, so the zoom still goes where the cursor
   *  points instead of to the middle of the screen. */
  private cursorPoint(ndc: { x: number; y: number }): any | null {
    const hit = this.firstHit(ndc, true);
    if (hit) return hit.point;
    const T = this.T;
    this.ray.setFromCamera(ndc as any, this.camera);
    const normal = this.camera.getWorldDirection(new T.Vector3());
    const plane = new T.Plane().setFromNormalAndCoplanarPoint(normal, this.nav.target);
    const p = new T.Vector3();
    return this.ray.ray.intersectPlane(plane, p) ? p : null;
  }

  /**
   * The nearest visible surface under a viewport point — fast.
   *
   * The plain raycast tests every triangle of every mesh the ray passes near and then keeps only
   * the first hit: 21.5 ms per click on Dino Smash (151k triangles). Here the meshes are tried
   * NEAREST FIRST by where the ray enters their bounding sphere, and the search stops as soon as
   * no sphere left can hold a nearer hit — usually after one or two meshes. Skinned meshes and
   * instanced ones whose sphere is unknown are always tried, because their geometry's sphere does
   * not describe where they are drawn.
   */
  firstHit(ndc: { x: number; y: number }, skipShells = false): { point: any; object: any; distance: number; face: number } | null {
    const T = this.T;
    this.ray.setFromCamera(ndc as any, this.camera);
    const ray = this.ray.ray;
    const sphere = new T.Sphere();
    const toC = new T.Vector3();
    const cands: { o: any; d: number }[] = [];
    this.subject.traverseVisible((o: any) => {
      if (!o.isMesh || !o.geometry) return;
      if (skipShells && (this.shells.has(o) || o.userData?.__shell)) return;
      let bs: any = null;
      if (o.isInstancedMesh) {
        if (!o.boundingSphere && typeof o.computeBoundingSphere === "function") {
          try { o.computeBoundingSphere(); } catch { /* an older three */ }
        }
        bs = o.boundingSphere || null;
      } else if (!o.isSkinnedMesh) {
        if (!o.geometry.boundingSphere) o.geometry.computeBoundingSphere();
        bs = o.geometry.boundingSphere;
      }
      if (!bs) { cands.push({ o, d: 0 }); return; }
      sphere.copy(bs).applyMatrix4(o.matrixWorld);
      toC.copy(sphere.center).sub(ray.origin);
      const along = toC.dot(ray.direction);
      const miss2 = toC.lengthSq() - along * along;
      const r2 = sphere.radius * sphere.radius;
      if (miss2 > r2) return;
      const half = Math.sqrt(r2 - miss2);
      if (along + half < 0) return;                       // wholly behind the eye
      cands.push({ o, d: Math.max(0, along - half) });
    });
    cands.sort((a, b) => a.d - b.d);
    let best: any = null;
    const hits: any[] = [];
    for (const c of cands) {
      if (best && c.d > best.distance) break;
      hits.length = 0;
      try { c.o.raycast(this.ray, hits); } catch { continue; }
      for (const h of hits) if (!best || h.distance < best.distance) best = h;
    }
    return best ? { point: best.point.clone(), object: best.object, distance: best.distance, face: typeof best.faceIndex === "number" ? best.faceIndex : -1 } : null;
  }

  /** Right button down: arm a flight. Nothing about the view changes until the mouse or a key
   *  really moves it, so a right-click that goes nowhere leaves the orbit centre where it was. */
  flyStart() {
    const nav = this.nav;
    if (!nav.flySpeed) nav.flySpeed = defaultFlySpeed(this.radiusNow());
    this.flyAirborne = false;
    nav.flyStart();
    this.ev.onNav();
  }

  private flyAirborne = false;

  /** The first real move of a flight. Looking through a game camera, the flight starts from that
   *  camera's own pose. The orbit centre is pulled in close in front of the eye, because the near
   *  clipping plane follows the orbit distance: left a hundred metres off, it cut in half whatever
   *  the flight came close to. */
  private flyTakeOff() {
    if (this.flyAirborne) return;
    this.flyAirborne = true;
    const nav = this.nav;
    if (nav.through) {
      this.parkAtCamera();
      nav.through = "";
    }
    nav.axisView = "";
    const eye = nav.position();
    const look = Math.min(nav.dist, Math.max(0.5, this.radiusNow() * 0.05));
    nav.dist = look;
    const t = targetFromEye([eye.x, eye.y, eye.z], look, nav.theta, nav.phi);
    nav.target.set(t[0], t[1], t[2]);
  }

  /** Mouse-look while the right button is held: the view turns, the eye stays where it is. */
  flyLook(dx: number, dy: number) {
    if (!this.nav.flying || (!dx && !dy)) return;
    this.flyTakeOff();
    this.nav.flyLook(dx, dy);
  }

  flyEnd() {
    if (!this.nav.flying) return;
    this.nav.flyEnd();
    this.flyAirborne = false;
    this.ev.onNav();
  }

  /**
   * Put each selected object down on the surface under it — Godot's "Snap Object to Floor". The
   * ray starts at the object's middle, so one sunk into the ground comes up onto it and one
   * floating above comes down. What is selected is never the floor, nor is a hidden object or a
   * sky shell. Each move is an edit like a gizmo drag: undone by Ctrl+Z, saved, and sent to a live
   * game. Returns how many objects moved.
   */
  dropToFloor(): number {
    const T = this.T;
    const picked = [...this.selection].map((k) => ({ k, o: this.objs.get(k) })).filter((x) => !!x.o);
    if (!picked.length) return 0;
    const own = new Set<any>();
    for (const { o } of picked) o.traverse((c: any) => own.add(c));
    const shown = (o: any) => { for (let p = o; p; p = p.parent) if (p.visible === false) return false; return true; };
    const ray = new T.Raycaster();
    // A sprite's raycast reads the camera and throws without one — a mirrored game has sprites.
    ray.camera = this.camera;
    let moved = 0;
    for (const { k, o } of picked) {
      const box = new T.Box3().expandByObject(o);
      if (box.isEmpty()) continue;
      ray.set(box.getCenter(new T.Vector3()), new T.Vector3(0, -1, 0));
      const floor = ray.intersectObject(this.subject, true)
        .find((h: any) => !own.has(h.object) && !this.shells.has(h.object) && shown(h.object));
      if (!floor) continue;
      const dy = floor.point.y - box.min.y;
      if (Math.abs(dy) < 1e-5) continue;
      const at = o.getWorldPosition(new T.Vector3());
      at.y += dy;
      if (o.parent) { o.parent.updateWorldMatrix(true, false); o.parent.worldToLocal(at); }
      o.position.copy(at);
      o.updateMatrixWorld(true);
      this.ev.onEdit("part", k, this.xfOf(o));
      moved++;
    }
    if (moved) this.placeGizmo();
    return moved;
  }

  frameSelected(margin = 1.5) {
    const objs = [...this.selection].map((k) => this.objs.get(k)).filter(Boolean);
    if (!objs.length) return this.frameAll(margin);
    this.ownLens();
    const T = this.T;
    const box = new T.Box3();
    for (const o of objs) box.expandByObject(o);
    if (box.isEmpty()) return this.frameAll(margin);
    const c = box.getCenter(new T.Vector3());
    const r = Math.max(1e-4, box.getSize(new T.Vector3()).length() / 2);
    this.nav.frame(c, r, margin);
  }

  /** Meshes that wrap the whole scene — a skydome, a fog sphere, a giant backdrop. */
  private shells = new Set<any>();

  /** Show the parts of a mirrored level the game switched off by distance. On by default: a game
   *  that culls draws three platforms of thirty, and a level editor that shows three is lying
   *  about the level. */
  showCulled = true;
  /** How many switched-off parts are shown, and how many are left off (twins, pools, effects). */
  culled = { shown: 0, kept: 0 };

  private revealCulled() {
    if (!this.showCulled) { this.culled = { shown: 0, kept: 0 }; return; }
    try {
      const plan = revealPlan(this.T, this.keyRoot());
      this.culled = { shown: plan.reveal.length, kept: plan.keep.length };
    } catch { this.culled = { shown: 0, kept: 0 }; }
  }

  /** The "whole level / as the game shows it now" switch. Nothing is rebuilt: the flags flip. */
  setShowCulled(on: boolean) {
    if (on === this.showCulled) return;
    this.showCulled = on;
    if (on) this.revealCulled();
    else { hideRevealed(this.keyRoot()); this.culled = { shown: 0, kept: 0 }; }
    // A selection that just went out of sight is not a selection a person can see or work on.
    for (const k of [...this.selection]) {
      const o = this.objs.get(k);
      let shown = true;
      for (let p = o; p; p = p.parent) if (p.visible === false) { shown = false; break; }
      if (!shown) this.selection.delete(k);
    }
    if (!this.selection.has(this.active)) this.active = [...this.selection].pop() || "";
    this.placeGizmo();
    this.ev.onSelect([...this.selection], this.active);
    this.ev.onScene?.();
  }

  // ------------------------------------------------------------------ pieces of a merged mesh
  //
  // A game that merges its scenery has no object for a treadmill; the treadmill is triangles
  // 1200-1440 of `base-props`. A click there splits those triangles out into a mesh of their own
  // — a child of the merged mesh, keyed `base-props~t1200-1440`, drawn instead of the originals —
  // and from then on it is an object like any other: selected, moved, rotated, scaled, hidden,
  // saved. The game gets the same move as corners (see `movePiecePc` in ops.ts).

  private pieceMaps = new WeakMap<any, PieceMap>();

  /** The pieces of a mesh, worked out once, on the triangles the code built. */
  private piecesFor(mesh: any): PieceMap | null {
    if (!mesh?.isMesh || mesh.isSkinnedMesh || mesh.isInstancedMesh || mesh.userData?.piece || mesh.userData?.pcSkinned) return null;
    const g = mesh.geometry;
    const pa = g?.attributes?.position;
    if (!pa || !g.index) return null;
    let map = this.pieceMaps.get(mesh);
    if (!map) {
      map = piecesOf(pa.array, mesh.userData.restIndex || g.index.array);
      this.pieceMaps.set(mesh, map);
    }
    return map;
  }

  /** Split one piece out of a merged mesh, or find the split already made. */
  splitPiece(mesh: any, meshKey: string, t0: number, t1: number, reindex = true): any | null {
    const T = this.T;
    for (const c of mesh.children) if (c.userData?.piece && c.userData.piece.t0 === t0 && c.userData.piece.t1 === t1) return c;
    const g = mesh.geometry;
    const ia = g?.index, pa = g?.attributes?.position;
    if (!ia || !pa) return null;
    if (!mesh.userData.restIndex) mesh.userData.restIndex = (ia.array as Uint16Array | Uint32Array).slice();
    const rest = mesh.userData.restIndex as ArrayLike<number>;
    if (t1 * 3 > rest.length || t0 < 0 || t1 <= t0) return null;
    const verts = pieceVertices(rest, t0, t1);
    if (!verts.length) return null;
    const P = pa.array as ArrayLike<number>;
    let cx = 0, cy = 0, cz = 0;
    for (const v of verts) { cx += P[v * 3]; cy += P[v * 3 + 1]; cz += P[v * 3 + 2]; }
    cx /= verts.length; cy /= verts.length; cz /= verts.length;
    const remap = new Map<number, number>();
    verts.forEach((v, i) => remap.set(v, i));
    const pg = new T.BufferGeometry();
    for (const [name, attr] of Object.entries<any>(g.attributes)) {
      const size = attr.itemSize;
      const src = attr.array as ArrayLike<number>;
      const Ctor = (src as any).constructor || Float32Array;
      const dst = new Ctor(verts.length * size);
      verts.forEach((v, i) => { for (let k = 0; k < size; k++) dst[i * size + k] = src[v * size + k]; });
      if (name === "position") for (let i = 0; i < verts.length; i++) { dst[i * 3] -= cx; dst[i * 3 + 1] -= cy; dst[i * 3 + 2] -= cz; }
      pg.setAttribute(name, new T.BufferAttribute(dst, size, attr.normalized));
    }
    const idx = new (verts.length > 65535 ? Uint32Array : Uint16Array)((t1 - t0) * 3);
    for (let t = t0, j = 0; t < t1; t++) for (let k = 0; k < 3; k++, j++) idx[j] = remap.get(rest[t * 3 + k])!;
    pg.setIndex(new T.BufferAttribute(idx, 1));
    pg.computeBoundingSphere();
    pg.computeBoundingBox();
    const piece = new T.Mesh(pg, mesh.material);
    piece.position.set(cx, cy, cz);
    piece.castShadow = mesh.castShadow;
    piece.receiveShadow = mesh.receiveShadow;
    const map = this.piecesFor(mesh);
    const n = map ? map.pieces.findIndex((p) => p.t0 === t0) : -1;
    piece.name = (mesh.name || "mesh") + " piece " + (n >= 0 ? n + 1 : t0);
    piece.userData.piece = { mesh: meshKey, t0, t1, c: [r5(cx), r5(cy), r5(cz)], n: verts.length };
    piece.userData.pieceKey = pieceKey(meshKey, { t0, t1 });
    // The originals collapse to nothing where they were: a triangle of three equal corners is not
    // drawn and is not hit, and every other triangle keeps its number, so the ranges still mean
    // the same triangles for the next piece.
    const live = ia.array as Uint16Array | Uint32Array;
    for (let i = t0 * 3; i < t1 * 3; i++) live[i] = 0;
    ia.needsUpdate = true;
    mesh.add(piece);
    mesh.updateMatrixWorld(true);
    // Once per click, not once per piece: a group of twelve letters is twelve splits.
    if (reindex) this.index();
    return piece;
  }

  /** The piece of a merged mesh under a hit triangle, and the group it sits in, without splitting
   *  either yet. */
  private pieceUnder(mesh: any, face: number): { piece: { t0: number; t1: number }; group: Array<{ t0: number; t1: number }> } | null {
    if (face < 0) return null;
    const map = this.piecesFor(mesh);
    if (!map || !map.merged) return null;
    const p = pieceAt(map, face);
    if (!p) return null;
    return { piece: { t0: p.t0, t1: p.t1 }, group: groupOf(map, p).map((q) => ({ t0: q.t0, t1: q.t1 })) };
  }

  /** A part's transform as a sidecar entry, with what a piece needs to be found again. */
  private xfOf(o: any): PartOverride {
    const xf: PartOverride = {
      pos: [r5(o.position.x), r5(o.position.y), r5(o.position.z)],
      rot: [r5(o.rotation.x), r5(o.rotation.y), r5(o.rotation.z)],
      scale: [r5(o.scale.x), r5(o.scale.y), r5(o.scale.z)],
    };
    const p = o.userData?.piece;
    if (p) xf.piece = { c: p.c, n: p.n };
    return xf;
  }

  /** Find the shells of a mirrored game and get them out of the way.
   *
   *  Dino Smash live opened on one white sphere with "82 parts under 24px": the sky dome. Framed
   *  AROUND the dome, the game inside is a dot, and a dome whose sky shader did not survive the
   *  snapshot is drawn as a white ball in front of everything. A shell is kept — listed, pickable,
   *  editable — but drawn from the inside, see-through, and left out of the framing, so what you
   *  see and move is the game. A mesh is a shell when it is far bigger than the typical part and its
   *  box holds the centres of most of the others. */
  tameShells() {
    const T = this.T;
    this.shells.clear();
    const info: { m: any; r: number; box: any }[] = [];
    this.subject.traverse((o: any) => {
      if (!o.isMesh || !o.geometry) return;
      const box = new T.Box3().setFromObject(o);
      if (box.isEmpty()) return;
      const r = box.getSize(new T.Vector3()).length() / 2;
      if (isFinite(r) && r > 0) info.push({ m: o, r, box });
    });
    if (info.length < 4) return;
    const radii = info.map((x) => x.r).sort((a, b) => a - b);
    const median = radii[Math.floor(radii.length / 2)];
    const centres = info.map((x) => x.box.getCenter(new T.Vector3()));
    for (let i = 0; i < info.length; i++) {
      const x = info[i];
      if (x.r < median * 8) continue;
      // A shell wraps in every direction. A flat island the whole game stands on is huge too,
      // and holds every centre, but it is the ground, not the sky: leave it alone.
      const size = x.box.getSize(new T.Vector3());
      const lo = Math.min(size.x, size.y, size.z), hi = Math.max(size.x, size.y, size.z);
      if (hi <= 0 || lo / hi < 0.3) continue;
      let inside = 0;
      for (let j = 0; j < info.length; j++) if (j !== i && x.box.containsPoint(centres[j])) inside++;
      if (inside >= (info.length - 1) * 0.6) this.shells.add(x.m);
    }
    for (const m of this.shells) {
      const mats = Array.isArray(m.material) ? m.material : [m.material];
      for (const mt of mats) {
        if (!mt) continue;
        mt.side = T.BackSide;
        mt.transparent = true;
        mt.opacity = Math.min(mt.opacity ?? 1, 0.35);
        mt.depthWrite = false;
        mt.needsUpdate = true;
      }
      m.userData.__shell = true;
    }
  }

  /** Is this object one of the shells? (The outliner and the stats can say so.) */
  isShell(o: any): boolean { return this.shells.has(o); }

  private boundsOf(o: any): { center: any; radius: number; size: any } | null {
    const T = this.T;
    const box = new T.Box3();
    if (this.shells.size && o === this.subject) {
      // The game, not its sky: every mesh that is not a shell.
      o.traverse((c: any) => { if (c.isMesh && !this.shells.has(c)) box.expandByObject(c); });
      if (box.isEmpty()) box.setFromObject(o);
    } else {
      box.setFromObject(o);
    }
    if (box.isEmpty()) return null;
    const size = box.getSize(new T.Vector3());
    return { center: box.getCenter(new T.Vector3()), radius: Math.max(1e-4, size.length() / 2), size };
  }

  // ------------------------------------------------------------------ running code
  /** The names the recorded code destructures, member for member with the forge's own context.
   *  An asset that runs in a contact sheet has to run here unchanged, or the editor is showing
   *  something the sheet never photographed. */
  /** Where to fetch a glTF loader that shares THIS three. Set by the editor per project. */
  loaderUrl = "";
  /** The module URL the viewport's three came from. The loader is bound to THIS, by string. */
  engineUrl = "";
  /** The first exception the frame loop caught, or "". Read by stats(), shown by the editor. */
  lastError = "";
  /** Frames that threw since the error was recorded. */
  renderErrors = 0;
  /** Draw calls of the last WHOLE frame, every pass counted. */
  lastCalls = 0;
  onRenderError: ((msg: string) => void) | null = null;

  /** Where the game's own code can be fetched from, best first — its dev server, then the
   *  workspace's. Set by the editor from the games list, because a workspace with three games has
   *  three servers and only one of them has any given file. */
  bases: string[] = [];
  /** The game folder inside the workspace that owns what is open, e.g. `rot-rush`. */
  assetRoot = "";

  /** One of the game's own assets, in the viewport, as ordinary editable objects. */
  async openRef(ref: AssetRef): Promise<OpenResult> {
    return openAsset({ ...ref, root: ref.root || this.assetRoot }, {
      THREE: this.T, bases: this.bases, device: this.renderer,
      // Through the Studio, which takes the compression out first — so a Draco creature from a
      // PlayCanvas game opens here with no decoder configured anywhere.
      loadModel: (url: string) => this.loadGLB(this.modelUrl ? this.modelUrl(url) : url),
    });
  }

  /** How to turn a project-relative model path into a URL this page can fetch. Set by the editor. */
  modelUrl: ((file: string) => string) | null = null;

  /** A model file, in the viewport, as ordinary editable objects.
   *
   *  The loader is fetched rather than bundled, and bound to the project's own three by the
   *  server, because a second copy of three produces meshes this scene would not recognise —
   *  they would load, and then not render, and nothing would say why. */
  async loadGLB(url: string): Promise<any> {
    const mod: any = await import(/* @vite-ignore */ (this.loaderUrl || "/api/engine/loader"));
    const L = mod.GLTFLoader || mod.default;
    if (!L) throw new Error("the loader did not export GLTFLoader");
    const gltf: any = await new Promise((res, rej) => new L().load(url, res, undefined, rej));
    const scene = gltf?.scene || (gltf?.scenes || [])[0] || null;
    // BUILT BY THIS THREE, OR REFUSED WITH THE REASON. A mesh from a second copy of three is not
    // an error to three: it loads, it counts, it fills the outliner — and the renderer throws on
    // the first frame, off in an animation frame where nobody is listening. One sentence here,
    // naming both modules, instead of a blank viewport with healthy numbers under it.
    if (scene && this.T?.Object3D && !(scene instanceof this.T.Object3D)) {
      throw new Error("this model was built by a different copy of three than the viewport runs. loader: "
        + (this.loaderUrl || "/api/engine/loader") + " · viewport: " + (this.engineUrl || "?")
        + ". The two must import the same module URL.");
    }
    return scene;
  }

  ctx(params: Record<string, any>): Record<string, any> {
    const log: string[] = [];
    return {
      pc: undefined,
      THREE: this.T,
      engine: "three",
      app: null,
      device: this.renderer,
      renderer: this.renderer,
      scene: this.scene,
      camera: this.camera,
      root: this.subject,
      params,
      log: (...a: unknown[]) => { try { log.push(a.map(String).join(" ").slice(0, 400)); } catch { /* unprintable */ } },
      // A model file, in scope, for code that wants to place one. Compression is already gone by
      // the time the URL is fetched — the Studio takes it out — so this needs no decoder.
      loadGLB: (url: string) => this.loadGLB(url),
      // The game's own modules, from whichever of its servers has them. A blob module has no
      // origin of its own, so a bare `import('/src/x.ts')` asks the STUDIO for the game's file
      // and gets the Studio's own index.html back.
      importProject: (rel: string) => importAny(rel, this.bases, this.assetRoot),
      // One row of the asset index, built. Everything hard about that lives in assetOpen.ts.
      buildAsset: async (ref: AssetRef) => {
        const r = await this.openRef(ref);
        if (!r.object) {
          throw new Error("nothing was drawn. " + (r.tried.slice(0, 3).join("; ") || "no builder was found"));
        }
        return r.object;
      },
      /** Box descriptors — the shape half this codebase's assets are — as meshes. */
      toObject: (v: any) => toObject(v, this.T).object,
      buildFrom: (m: any, ref: AssetRef, deps: any[] = []) =>
        buildFrom(m, ref, { THREE: this.T, device: this.renderer }, deps),
      clear: () => { for (const c of this.subject.children.slice()) drop(this.subject, c); },
      add: (thing: any) => {
        const many = Array.isArray(thing) ? thing : [thing];
        const loose: any[] = [];
        for (const t of many) {
          if (t == null) continue;
          let o = t;
          if (o.isBufferGeometry) o = new this.T.Mesh(o, this.mats.solid.clone());
          else if (o.isMaterial) o = new this.T.Mesh(new this.T.SphereGeometry(1, 48, 32), o);
          if (o.isObject3D) this.subject.add(o);
          else loose.push(t);
        }
        // WHAT THIS GAME'S ASSETS ACTUALLY ARE. Its props and its creatures are `Prop[]` — plain
        // box descriptors its renderer bakes into merged buckets. Dropping them because they are
        // not meshes threw the asset away and reported success.
        if (loose.length) {
          const got = toObject(loose, this.T);
          if (got.object) this.subject.add(got.object);
        }
        return thing;
      },
      forge: {
        ensure: async () => ({ ok: true, engine: "three", reused: true }),
        clear: () => { for (const c of this.subject.children.slice()) drop(this.subject, c); return true; },
        dispose: () => true,
        view: (name: string) => { this.setView(name); this.frameAll(); return ""; },
        shot: () => this.shot(),
        stats: () => this.stats(),
        ready: () => ({ built: true, engine: "three" }),
      },
      __log: log,
    };
  }

  /** Run an asset. The wrapper is the one the forge builds, so `return`, `await` and `import()`
   *  behave exactly as they did in the page that recorded it. */
  async run(code: string, devUrl: string, params: Record<string, any>):
    Promise<{ value: unknown; error: string; ms: number; log: string[] }> {
    for (const c of this.subject.children.slice()) drop(this.subject, c);
    const body = devUrl ? retargetImports(code, devUrl) : code;
    const src = "export default async function __run(__c) {\n"
      + "const {pc,THREE,engine,app,device,renderer,scene,camera,root,forge,add,clear,log,params,"
      + "loadGLB,importProject,buildAsset,toObject,buildFrom} = __c;\n"
      + "return await (async () => {\n" + body + "\n})();\n}\n";
    const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
    const c = this.ctx(params);
    const t0 = performance.now();
    this.beforeBuild();
    try {
      const m = await import(/* @vite-ignore */ url);
      const value = await m.default(c);
      // Some assets build and return rather than calling add(); take whatever came back — and
      // that is as likely to be descriptors as it is to be a mesh.
      if (value && (value as any).isObject3D && !this.subject.children.includes(value)) this.subject.add(value);
      else if (value && !this.subject.children.length) {
        const got = toObject(value, this.T);
        if (got.object) this.subject.add(got.object);
      }
      this.afterBuild(value);
      return { value, error: "", ms: performance.now() - t0, log: c.__log };
    } catch (e: any) {
      this.afterBuild(null);
      return { value: undefined, error: String(e?.stack || e).slice(0, 2000), ms: performance.now() - t0, log: c.__log };
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  /** Build from a module the project owns, rather than from a recorded snippet. */
  async runFile(build: (T: any, p: Record<string, any>, ctx?: any) => Promise<any>, params: Record<string, any>):
    Promise<{ error: string; ms: number }> {
    for (const c of this.subject.children.slice()) drop(this.subject, c);
    const t0 = performance.now();
    this.beforeBuild();
    try {
      // The SAME context the blob wrapper destructures, so a script says the same things
      // whichever way the editor happens to run it.
      const out = await build(this.T, params, this.ctx(params));
      for (const o of Array.isArray(out) ? out : [out]) if (o?.isObject3D) this.subject.add(o);
      this.afterBuild(out);
      return { error: "", ms: performance.now() - t0 };
    } catch (e: any) {
      this.afterBuild(null);
      return { error: String(e?.stack || e?.message || e).slice(0, 2000), ms: performance.now() - t0 };
    }
  }

  /** The studio's world goes back on before every build, so what the code sets is what it set. */
  private beforeBuild() {
    const s = this.studioWorld;
    if (!s) return;
    this.scene.background = s.background;
    this.scene.fog = s.fog;
    this.scene.environment = s.environment;
  }

  /**
   * After the code ran: index, and lift out what makes it a SCENE rather than an asset.
   *
   * A world is taken from two places, because code sets it in two: a returned THREE.Scene carries
   * its own background, fog and environment; and code handed `scene` in its context may set them
   * straight onto it. Either way the studio's own world goes back on and the asset's is kept
   * aside, so the Scene World toggle decides which one is drawn rather than the last writer.
   */
  private afterBuild(out: any) {
    this.subject.updateMatrixWorld(true);
    // A mirrored game brings its sky with it; an asset built from a file does not have one. The
    // parts of the level it culled by distance come back first, so the sky test sees the level.
    if (this.isLive) { this.revealCulled(); this.tameShells(); } else { this.shells.clear(); this.culled = { shown: 0, kept: 0 }; }
    const s = this.studioWorld;
    let world: { background: any; fog: any; environment: any } | null = null;
    const returned = Array.isArray(out) ? out.find((o: any) => o?.isScene) : out?.isScene ? out : null;
    if (returned && (returned.background || returned.fog || returned.environment)) {
      world = { background: returned.background, fog: returned.fog, environment: returned.environment };
    }
    if (s && (this.scene.background !== s.background || this.scene.fog !== s.fog || this.scene.environment !== s.environment)) {
      world = {
        background: this.scene.background !== s.background ? this.scene.background : world?.background,
        fog: this.scene.fog !== s.fog ? this.scene.fog : world?.fog,
        environment: this.scene.environment !== s.environment ? this.scene.environment : world?.environment,
      };
      this.scene.background = s.background;
      this.scene.fog = s.fog;
      this.scene.environment = s.environment;
    }
    this.assetWorld = world;
    this.assetLights = [];
    this.subject.traverse((o: any) => { if (o.isLight) this.assetLights.push(o); });
    this.index();
    this.selection = new Set([...this.selection].filter((k) => this.objs.has(k)));
    if (!this.objs.has(this.active)) this.active = "";
    if (this.nav.through && !this.objs.get(this.nav.through)?.isCamera) this.nav.through = "";
    this.placeGizmo();
    this.refreshNormals();
    this.refreshOrigins();
    this.syncCamera();
    // Every build path ends here, so the handles are on the new box at once — for anyone reading
    // `paramHandleInfo()` before the next frame — not only after it. The frame re-places them too.
    this.handleLayer?.update();
  }

  /** The asset brought its own lights. */
  get hasSceneLights(): boolean { return this.assetLights.length > 0; }
  /** The asset set a background, a fog or an environment of its own. */
  get hasSceneWorld(): boolean { return !!this.assetWorld; }

  // ------------------------------------------------------------------ the asset
  setAsset(group: any) {
    for (const c of this.subject.children.slice()) drop(this.subject, c);
    this.clearRig();
    this.normalsObj = null;
    this.originsObj = null;
    if (group) this.subject.add(group);
    this.subject.updateMatrixWorld(true);
    this.index();
    // A selection made before a rebuild is kept if the parts are still there, which is what makes
    // dragging a slider feel like editing one thing rather than reloading a document.
    this.selection = new Set([...this.selection].filter((k) => this.objs.has(k)));
    if (!this.objs.has(this.active)) this.active = "";
  }

  /** Put the sidecar's placements back into a scene the code has just rebuilt.
   *
   *  This runs AFTER the build and after the transform overrides, in that order and for the same
   *  reason a level editor loads its objects after the terrain: a placement is positioned in the
   *  world the code made, and a clone can only be made once the thing it copies exists. */
  async applyPlacedDoc(placed: any[] | undefined,
                       resolve: (ref: any) => Promise<any>): Promise<{ added: number; errors: string[] }> {
    const out = await applyPlaced(this.placeParent(), placed as any, this.T, resolve);
    if (out.added) {
      this.subject.updateMatrixWorld(true);
      this.index();
      this.ev.onScene?.();
    }
    return out;
  }

  /** WHERE A NEW THING GOES WHEN YOU DO NOT SAY.
   *
   *  Under the camera's own target, dropped to the ground plane, which is where a person is
   *  looking and therefore where they mean. Not the world origin: on a big level the origin is
   *  usually off screen, and an object placed somewhere you are not looking reads as an object
   *  that failed to appear. */
  dropPoint(): [number, number, number] {
    const t = this.nav.target;
    const r2 = (v: number) => Math.round(v * 100) / 100;
    // A LEVEL IS NOT FLAT AT NOUGHT. In a mirrored game the platform you are looking at stands
    // metres up, and a brainrot dropped at y=0 under it was a brainrot nobody could see. So in a
    // live level it lands on the first floor-like surface in the middle of the view.
    if (this.isLive) {
      const T = this.T;
      const ray = new T.Raycaster();
      ray.camera = this.camera;
      ray.setFromCamera(new T.Vector2(0, 0), this.camera);
      const shown = (o: any) => { for (let p = o; p; p = p.parent) if (p.visible === false) return false; return true; };
      const n = new T.Vector3();
      for (const h of ray.intersectObject(this.subject, true)) {
        if (!h.object?.isMesh || this.shells.has(h.object) || h.object.userData?.__shell || !shown(h.object)) continue;
        if (!h.face) continue;
        n.copy(h.face.normal).transformDirection(h.object.matrixWorld);
        if (n.y < 0.5) continue;
        return [r2(h.point.x), r2(h.point.y), r2(h.point.z)];
      }
    }
    return [r2(t.x), 0, r2(t.z)];
  }

  /** Where the editor's own placements hang. In a mirrored game that is the game's root, not the
   *  holder around it: a placement there has the same key in the mirror as in the game it is
   *  sent to, and the holder keeps its one child — two made every key in the level change. */
  private placeParent(): any { return this.isLive ? this.keyRoot() : this.subject; }

  /** One of the editor's own placements, by its id. */
  placedObject(id: string): any | null {
    for (const c of this.placeParent().children) if (c.userData?.studioPlaced === id) return c;
    return null;
  }

  /** Put an object into the subject and select it. `placedId` marks it as the editor's own, which
   *  is what later tells "remove it" apart from "hide what the game built". */
  addObject(obj: any, name = "", placedId = ""): string {
    if (!obj) return "";
    if (name) obj.name = name;
    if (placedId) obj.userData.studioPlaced = placedId;
    this.placeParent().add(obj);
    this.subject.updateMatrixWorld(true);
    this.index();
    const key = this.keys.get(obj) || "";
    if (key) this.select([key]);
    this.ev.onScene?.();
    return key;
  }

  /** Take one out. Only an object the editor placed can really go: a game builds its own world
   *  from code every run, so removing one of ITS objects here would last until the next reload
   *  and no longer. Hiding it is the honest operation, and it is what the sidecar can replay. */
  removeKey(key: string): { removed: boolean; hidden: boolean } {
    const o = this.objs.get(key);
    if (!o) return { removed: false, hidden: false };
    if (placedIdOf(o)) {
      const parent = o.parent;
      if (parent) drop(parent, o);
      this.selection.delete(key);
      if (this.active === key) this.active = "";
      this.index();
      this.placeGizmo();
      this.ev.onSelect([...this.selection], this.active);
      this.ev.onScene?.();
      return { removed: true, hidden: false };
    }
    o.visible = false;
    this.ev.onEdit("part", key, { hidden: true });
    this.ev.onScene?.();
    return { removed: false, hidden: true };
  }

  /** A copy, offset by a metre so it is not hiding inside the original. Geometry and materials are
   *  shared by `clone()`, which is what you want: a hundred copies of one rock cost one rock. */
  duplicateKey(key: string, offset = 1): { key: string; object: any } | null {
    const o = this.objs.get(key);
    if (!o || o === this.subject) return null;
    const copy = o.clone(true);
    copy.position.x += offset;
    copy.name = (o.name || "object") + " copy";
    const id = "p" + Math.random().toString(36).slice(2, 9);
    copy.userData = { ...copy.userData, studioPlaced: id };
    const k = this.addObject(copy, copy.name, id);
    return k ? { key: k, object: copy } : null;
  }

  /** Every object the editor placed, with the transform it has now — the half of the sidecar that
   *  describes things the game's own code knows nothing about. */
  placedNow(): { id: string; name: string; pos: number[]; rot: number[]; scale: number[] }[] {
    const out: any[] = [];
    for (const c of this.placeParent().children) {
      const id = c.userData?.studioPlaced;
      if (!id) continue;
      out.push({
        id: String(id), name: c.name || String(id),
        pos: [r4(c.position.x), r4(c.position.y), r4(c.position.z)],
        rot: [r4(c.rotation.x), r4(c.rotation.y), r4(c.rotation.z)],
        scale: [r4(c.scale.x), r4(c.scale.y), r4(c.scale.z)],
      });
    }
    return out;
  }

  /** Stable keys, from the same function the runtime applier uses, so a key written here is
   *  always the key a game finds. Then the icons for anything with no surface. */
  private index() {
    this.keys = stableKeys(this.keyRoot());
    this.dropSolidMats();
    this.objs.clear();
    this.baseXf.clear();
    for (const [o, key] of this.keys) {
      this.objs.set(key, o);
      this.baseXf.set(key, {
        pos: [o.position.x, o.position.y, o.position.z],
        rot: [o.rotation.x, o.rotation.y, o.rotation.z],
        scale: [o.scale.x, o.scale.y, o.scale.z],
      });
    }
    this.rebuildExtras();
  }

  private rebuildExtras() {
    const T = this.T;
    for (const c of this.extras.children.slice()) drop(this.extras, c);
    this.extraItems = [];
    const b = this.boundsOf(this.subject);
    const reach = Math.min(2.5, Math.max(0.08, (b?.radius || 1) * 0.3));
    for (const [o, key] of this.keys) {
      const kind = extraKind(o);
      if (!kind) continue;
      const hex = "#" + (o.isLight && o.color ? o.color.getHexString() : "cfd6e2");
      const sprite = new T.Sprite(new T.SpriteMaterial({
        map: extraIcon(T, kind, hex), sizeAttenuation: false, depthTest: true, depthWrite: false, transparent: true,
      }));
      sprite.userData.key = key;
      sprite.renderOrder = 880;
      let lines: any = null;
      if (o.isCamera) lines = cameraOutline(T, o, reach, 0xcfd6e2);
      else if (o.isSpotLight || o.isDirectionalLight) lines = lightOutline(T, o, reach * (o.isSpotLight ? 1.6 : 1), o.color?.getHex?.() ?? 0xffffff);
      if (lines) { lines.matrixAutoUpdate = false; this.extras.add(lines); }
      this.extras.add(sprite);
      this.extraItems.push({ key, obj: o, sprite, lines });
    }
  }

  /** Icons follow their objects every frame: a light moved by the gizmo, or by the code. */
  private updateExtras(cam: any) {
    const T = this.T;
    const show = this.overlays.extras;
    this.extras.visible = show;
    if (!show) return;
    // The same pixel size at any distance, like the gizmo.
    const px = 22;
    const scale = cam.isOrthographicCamera
      ? (px * (cam.top - cam.bottom)) / Math.max(1, this.size.h)
      : (px * 2 * Math.tan((cam.fov * Math.PI) / 360)) / Math.max(1, this.size.h);
    const p = new T.Vector3();
    for (const it of this.extraItems) {
      const o = it.obj;
      const vis = o.visible && o.parent !== null;
      it.sprite.visible = vis && this.nav.through !== it.key;
      if (it.lines) it.lines.visible = it.sprite.visible;
      if (!vis) continue;
      o.updateWorldMatrix(true, false);
      it.sprite.position.copy(o.getWorldPosition(p));
      it.sprite.scale.set(scale, scale, 1);
      const sel = this.selection.has(it.key);
      const tint = it.key === this.active ? THEME.active : sel ? THEME.select : 0xffffff;
      if (it.sprite.material.color.getHex() !== tint) it.sprite.material.color.setHex(tint);
      if (it.lines) {
        // A directional light points at its target; the outline is authored down -Z, so aim it.
        if (o.isDirectionalLight || o.isSpotLight) {
          const from = o.getWorldPosition(new T.Vector3());
          const to = o.target?.getWorldPosition ? o.target.getWorldPosition(new T.Vector3()) : new T.Vector3();
          const m = new T.Matrix4().lookAt(from, to, new T.Vector3(0, 1, 0));
          m.setPosition(from);
          it.lines.matrix.copy(m);
        } else {
          it.lines.matrix.copy(o.matrixWorld);
        }
        it.lines.matrixWorld.copy(it.lines.matrix);
        if (it.lines.material.color.getHex() !== tint && (sel || it.key === this.active)) it.lines.material.color.setHex(tint);
      }
    }
  }

  /** What a light or a camera answers to, live, for the panels. */
  extraOf(key: string): { kind: string; light?: any; camera?: any } | null {
    const o = this.objs.get(key);
    const kind = extraKind(o);
    if (!kind) return null;
    return { kind, light: o.isLight ? o : undefined, camera: o.isCamera ? o : undefined };
  }

  /** Set one property on a light or a camera by key; the caller records it as an override. */
  setProp(key: string, prop: string, value: number | string | boolean) {
    const o = this.objs.get(key);
    if (!o) return;
    if (prop === "color") { o.color?.set?.(value); const it = this.extraItems.find((i) => i.key === key); if (it && o.isLight) { it.sprite.material.map = extraIcon(this.T, extraKind(o) as ExtraKind, "#" + o.color.getHexString()); it.sprite.material.needsUpdate = true; if (it.lines) it.lines.material.color.copy(o.color); } return; }
    if (prop === "shadow") { o.castShadow = !!value; return; }
    if (prop in o) o[prop] = value;
    if (o.isCamera) { o.updateProjectionMatrix?.(); if (this.nav.through === key) this.syncCamera(); }
  }

  /** The scene report from the operations library, on what is built right now. */
  sceneInfo(): SceneReport { return sceneReport(this.subject); }

  /** The first mesh a part holds — the part itself when it is one. */
  meshOf(key: string): any {
    const o = this.objs.get(key);
    if (!o) return null;
    if (o.isMesh) return o;
    let found: any = null;
    o.traverse((c: any) => { if (!found && c.isMesh && c.geometry?.attributes?.position) found = c; });
    return found;
  }

  /**
   * Bake a map onto a part and put it on the part's material. The part is unwrapped first when it
   * has no UVs, and its material is made its own, so a bake for this part cannot land on another
   * part that shared the material. Returns the pixels for a preview.
   */
  bakePart(key: string, kind: "ao" | "curvature" | "normal", size = 256, rays = 24): { width: number; height: number; data: Uint8ClampedArray } | null {
    const mesh = this.meshOf(key);
    if (!mesh) return null;
    const T = this.T, ops = this.ops;
    let g = mesh.geometry;
    if (!g.attributes.uv) { g = ops.unwrap(g); mesh.geometry = g; }
    else if (!g.attributes.uv1) g.setAttribute("uv1", g.attributes.uv.clone());
    const b = this.boundsOf(mesh);
    const radius = b?.radius || 1;
    let baked: { width: number; height: number; data: Uint8ClampedArray };
    if (kind === "ao") baked = ops.bakeAO(g, size, { rays, distance: radius * 2 });
    else if (kind === "curvature") baked = ops.bakeCurvature(g, size, {});
    else baked = ops.bakeNormalMap(g, ops.subsurf(g, 2, { creaseAngle: 0 }), size, { distance: radius * 0.05 });
    const tex = ops.texture(baked, { srgb: false });
    if (Array.isArray(mesh.material)) return baked;
    if (!mesh.material.userData?.__own) { mesh.material = mesh.material.clone(); mesh.material.userData.__own = true; }
    const m = mesh.material;
    if (kind === "ao") { m.aoMap = tex; m.aoMapIntensity = 1; }
    else if (kind === "normal") { m.normalMap = tex; if (m.normalScale?.set) m.normalScale.set(1, 1); }
    else { m.userData.curvatureMap = tex; }
    m.needsUpdate = true;
    void T;
    return baked;
  }

  keyOf(o: any): string { return this.keys.get(o) || ""; }
  objectOf(key: string): any { return this.objs.get(key); }

  parts(): PartInfo[] {
    const out: PartInfo[] = [];
    const T = this.T;
    const box = new T.Box3();
    this.subject.updateMatrixWorld(true);
    const walk = (o: any, depth: number, parent: string) => {
      const key = this.keys.get(o);
      if (key) {
        let meshes = 0, tris = 0;
        o.traverse((c: any) => {
          if (!c.isMesh && !c.isPoints && !c.isLine) return;
          meshes++;
          const g = c.geometry;
          if (g?.index) tris += g.index.count / 3;
          else if (g?.attributes?.position) tris += g.attributes.position.count / 3;
        });
        box.makeEmpty();
        box.expandByObject(o);
        let px = 0;
        let at = { x: 0, y: 0, behind: true };
        if (!box.isEmpty()) {
          const s = this.screenExtent(box);
          px = s.px;
          at = s.at;
        }
        const info: PartInfo = {
          key, name: o.name || describeObject(o),
          type: o.isMesh ? "mesh" : o.isLight ? "light" : o.isCamera ? "camera" : o.isBone ? "bone" : o.children?.length ? "group" : o.type || "object",
          depth, parent, meshes, triangles: Math.round(tris), visible: o.visible, px, at,
        };
        if (o.userData?.revealed) info.revealed = true;
        if (o.userData?.piece) info.piece = true;
        out.push(info);
      }
      for (const c of o.children) walk(c, key ? depth + 1 : depth, key || parent);
    };
    walk(this.subject, 0, "");
    return out;
  }

  /** Longest on-screen edge of a world box, in real pixels, and where to put its label. */
  private screenExtent(box: any): { px: number; at: { x: number; y: number; behind: boolean } } {
    const T = this.T;
    const c = box.getCenter(new T.Vector3());
    const s = box.getSize(new T.Vector3());
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9, behind = false;
    const p = new T.Vector3();
    for (let i = 0; i < 8; i++) {
      p.set(c.x + (i & 1 ? 0.5 : -0.5) * s.x, c.y + (i & 2 ? 0.5 : -0.5) * s.y, c.z + (i & 4 ? 0.5 : -0.5) * s.z);
      const q = project(T, p, this.camera, this.size);
      if (q.behind) behind = true;
      minX = Math.min(minX, q.x); maxX = Math.max(maxX, q.x);
      minY = Math.min(minY, q.y); maxY = Math.max(maxY, q.y);
    }
    const top = project(T, new T.Vector3(c.x, c.y + s.y / 2, c.z), this.camera, this.size);
    return {
      px: behind ? 0 : Math.round(Math.max(maxX - minX, maxY - minY)),
      at: { x: top.x, y: top.y, behind: behind || top.behind },
    };
  }

  /** The numbers that change every frame — frame rate and draw calls — without the walk over the
   *  whole scene that `stats()` does. The status bar reads these twice a second. */
  liveStats(): { fps: number; drawCalls: number; drawn: boolean; error: string } {
    const n = this.frameTimes.length;
    const avg = n ? this.frameTimes.reduce((a, c) => a + c, 0) / n : 0;
    return { fps: avg > 0 ? Math.round(1000 / avg) : 0, drawCalls: this.lastCalls, drawn: this.lastCalls > 0, error: this.lastError };
  }

  stats(): Stats {
    let objects = 0, meshes = 0, tris = 0;
    const mats = new Set<any>();
    this.subject.traverse((o: any) => {
      objects++;
      if (!o.isMesh && !o.isPoints && !o.isLine) return;
      meshes++;
      const g = o.geometry;
      if (g?.index) tris += g.index.count / 3;
      else if (g?.attributes?.position) tris += g.attributes.position.count / 3;
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) if (m) mats.add(m);
    });
    const b = this.boundsOf(this.subject);
    const n = this.frameTimes.length;
    const avg = n ? this.frameTimes.reduce((a, c) => a + c, 0) / n : 0;
    return {
      objects: Math.max(0, objects - 1), meshes, triangles: Math.round(tris), materials: mats.size,
      // The whole frame's count, kept by render(). `renderer.info` read here was reset by the LAST
      // pass — the gizmo's — so it said "0 draws" whenever nothing was selected.
      drawCalls: this.lastCalls,
      bbox: b ? [round3(b.size.x), round3(b.size.y), round3(b.size.z)] : null,
      fps: avg > 0 ? Math.round(1000 / avg) : 0,
      error: this.lastError,
      drawn: this.lastCalls > 0,
    };
  }

  // ------------------------------------------------------------------ edit mode
  //
  // The mesh below the object. Three things make this different from Blender's edit mode, and all
  // three come from the same fact: our geometry is a program's output rather than a document.

  /**
   * The buffer as the CODE built it, kept beside the live one.
   *
   * A vertex is keyed by where the code put it. Key off a buffer that already carries yesterday's
   * edits and you write a key that no future rebuild can match, so the edit silently evaporates on
   * the next slider move. Set once per build: a fresh rebuild makes fresh objects with no userData,
   * so "only if absent" is exactly the right rule and nothing has to be cleared.
   */
  private restOf(o: any): Float32Array | null {
    // The same function the game's applier uses, so the editor and the runtime can never disagree
    // about which buffer a key was written against.
    return restBuffer(o);
  }

  /** Saved vertex moves onto the tree, with the rest snapshot taken first. */
  applyVertEdits(verts: any[]): { moved: number; errors: string[] } {
    const root = this.keyRoot();
    root.traverse?.((o: any) => { if (o.isMesh) this.restOf(o); });
    const out = { parts: 0, mods: 0, verts: 0, missing: [] as string[], errors: [] as string[] };
    const moved = applyVerts(root, verts, out);
    if (this.topo && this.editObj) this.rebindEdit();
    return { moved, errors: [...out.errors, ...out.missing.map((k) => "no mesh called " + k)] };
  }

  /** Every mesh in the asset, by key, for the picker that chooses what to edit. */
  editableMeshes(): Array<{ key: string; name: string; verts: number }> {
    const out: Array<{ key: string; name: string; verts: number }> = [];
    for (const [o, k] of stableKeys(this.keyRoot())) {
      const n = o?.geometry?.attributes?.position?.count;
      if (o.isMesh && n) out.push({ key: k, name: o.name || k, verts: n });
    }
    return out;
  }

  /**
   * Go into edit mode on one mesh.
   *
   * Without a key it takes the selected part's first mesh, then the biggest mesh in the asset, so
   * pressing Tab with nothing chosen does the obvious thing instead of nothing.
   */
  enterEdit(meshKey = ""): { key: string; error: string } {
    const byKey = new Map<string, any>();
    for (const [o, k] of stableKeys(this.keyRoot())) byKey.set(k, o);

    let key = meshKey;
    let obj = key ? byKey.get(key) : null;
    if (!obj && this.active) {
      const sel = this.objs.get(this.active);
      sel?.traverse?.((c: any) => { if (!obj && c.isMesh && c.geometry?.attributes?.position) { obj = c; } });
      if (obj) key = [...byKey].find(([, o]) => o === obj)?.[0] || "";
    }
    if (!obj) {
      const all = this.editableMeshes();
      all.sort((a, b) => b.verts - a.verts);
      key = all[0]?.key || "";
      obj = key ? byKey.get(key) : null;
    }
    if (!obj?.geometry?.attributes?.position) {
      return { key: "", error: "nothing here holds a mesh to edit" };
    }

    const rest = this.restOf(obj);
    if (!rest) return { key: "", error: "that mesh has no vertices" };
    const idx = obj.geometry.index ? (obj.geometry.index.array as ArrayLike<number>) : null;
    this.topo = buildTopology(rest, idx);
    this.editObj = obj;
    this.editKey = key;
    this.vsel.clear();
    this.mode = "edit";
    this.refreshEdit();
    this.placeGizmo();
    return { key, error: "" };
  }

  /** Rebuild the corner graph against the geometry as it stands now. Called after a rebuild, so a
   *  parameter move does not leave the selection pointing at corners that no longer exist. */
  rebindEdit() {
    if (!this.editKey) return;
    const byKey = new Map<string, any>();
    for (const [o, k] of stableKeys(this.keyRoot())) byKey.set(k, o);
    const obj = byKey.get(this.editKey);
    const rest = obj ? this.restOf(obj) : null;
    if (!obj || !rest) { this.leaveEdit(); return; }
    const idx = obj.geometry.index ? (obj.geometry.index.array as ArrayLike<number>) : null;
    this.editObj = obj;
    this.topo = buildTopology(rest, idx);
    // A selection made before the rebuild is kept where the corner is still there, the same
    // promise object mode already makes for parts.
    this.vsel = new Set([...this.vsel].filter((g) => g < (this.topo?.groups.length || 0)));
    this.refreshEdit();
    this.placeGizmo();
  }

  leaveEdit() {
    this.mode = "object";
    this.editKey = "";
    this.editObj = null;
    this.topo = null;
    this.vsel.clear();
    this.disposeEditOverlay();
    this.placeGizmo();
  }

  /** Every corner projected to pixels, with its depth, for the screen-space pickers. */
  private projectGroups(): Screen[] {
    const T = this.T;
    const out: Screen[] = [];
    if (!this.topo || !this.editObj) return out;
    const pos = this.editObj.geometry.attributes.position.array as Float32Array;
    this.editObj.updateMatrixWorld(true);
    const m = this.editObj.matrixWorld;
    const camPos = this.camera.getWorldPosition(new T.Vector3());
    const v = new T.Vector3();
    for (let g = 0; g < this.topo.groups.length; g++) {
      const a = groupAt(pos, this.topo, g);
      v.set(a[0], a[1], a[2]).applyMatrix4(m);
      const p = project(T, v, this.camera, this.size);
      out.push({ x: p.x, y: p.y, z: v.distanceTo(camPos), behind: p.behind });
    }
    return out;
  }

  /** What one click at these pixels means, as the corners it selects. */
  pickElem(x: number, y: number): number[] {
    if (!this.topo || !this.editObj) return [];
    if (this.elem === "face") {
      // A face is the one place a raycast beats screen distance: three.js hands back the exact
      // triangle under the cursor, including which side of a thin wall you are on.
      const ndc = { x: (x / this.size.w) * 2 - 1, y: -(y / this.size.h) * 2 + 1 };
      this.ray.setFromCamera(ndc as any, this.camera);
      const hit = this.ray.intersectObject(this.editObj, false)[0];
      const f = hit?.face;
      if (!f) return [];
      const go = this.topo.groupOf;
      return [...new Set([go[f.a], go[f.b], go[f.c]])].filter((g) => g >= 0);
    }
    const pts = this.projectGroups();
    if (this.elem === "vert") {
      const g = nearestGroup(pts, x, y, this.pickRadius);
      return elemGroups("vert", this.topo, g);
    }
    const e = nearestEdge(pts, this.topo.edges, x, y, this.pickRadius);
    return elemGroups("edge", this.topo, e);
  }

  selectElems(groups: number[], additive = false) {
    if (!additive) this.vsel.clear();
    for (const g of groups) {
      if (additive && this.vsel.has(g)) this.vsel.delete(g);
      else this.vsel.add(g);
    }
    this.refreshEdit();
    this.placeGizmo();
  }

  selectAllElems() {
    if (!this.topo) return;
    if (this.vsel.size === this.topo.groups.length) this.vsel.clear();
    else for (let g = 0; g < this.topo.groups.length; g++) this.vsel.add(g);
    this.refreshEdit();
    this.placeGizmo();
  }

  boxSelectElems(r: { x0: number; y0: number; x1: number; y1: number }, additive: boolean) {
    if (!this.topo) return;
    this.selectElems(groupsInBox(this.projectGroups(), r), additive);
  }

  /** What the header shows: corners, and the edges and faces they imply. */
  elemCounts(): { verts: number; edges: number; faces: number; total: number } {
    return this.counts;
  }

  /** Walking every edge and triangle is cheap once and ruinous per frame, and the header reads it
   *  on every render. Recomputed where it can actually change: in refreshEdit. */
  private counts = { verts: 0, edges: 0, faces: 0, total: 0 };

  /** The document for the mesh being edited: only the corners that have actually moved. */
  vertEditsNow(): any[] {
    if (!this.topo || !this.editObj) return [];
    const pos = this.editObj.geometry.attributes.position.array as Float32Array;
    return vertEditsOf(this.editKey, pos, this.topo);
  }

  /** Put every selected corner back where the code put it. Blender has no equivalent, because in
   *  Blender there is nothing to go back TO. */
  resetElems(): number {
    if (!this.topo || !this.editObj || !this.vsel.size) return 0;
    const pos = this.editObj.geometry.attributes.position.array as Float32Array;
    let n = 0;
    for (const g of this.vsel) {
      const a = groupRest(this.topo, g);
      for (const i of this.topo.groups[g]) { pos[i * 3] = a[0]; pos[i * 3 + 1] = a[1]; pos[i * 3 + 2] = a[2]; }
      n++;
    }
    this.afterVertChange();
    this.ev.onEdit("vert", this.editKey, this.vertEditsNow());
    return n;
  }

  private afterVertChange() {
    if (!this.editObj) return;
    const geo = this.editObj.geometry;
    geo.attributes.position.needsUpdate = true;
    // A surface that moved but kept its old normals lights as though nothing happened, and the
    // edit looks like it did not take.
    geo.computeVertexNormals?.();
    geo.computeBoundingSphere?.();
    this.refreshEdit();
    this.placeGizmo();
  }

  // ---- the overlay: dots, edges and the faces you chose
  private disposeEditOverlay() {
    if (this.editGroup) { drop(this.scene, this.editGroup); this.editGroup = null; }
    this.editDots = this.editLines = this.editFaces = null;
  }

  /**
   * Redraw the corner overlay from the live buffer and the current selection.
   *
   * The overlay lives in the SCENE rather than under the subject, with the mesh's world matrix
   * copied onto it. Parented to the mesh it would be swept up by the material swap that draws the
   * shading modes, and edit mode would turn into whatever pass ran last.
   */
  private refreshEdit() {
    const T = this.T;
    this.disposeEditOverlay();
    if (this.mode !== "edit" || !this.topo || !this.editObj) {
      this.counts = { verts: 0, edges: 0, faces: 0, total: 0 };
      return;
    }
    this.counts = { ...selectionCounts(this.topo, this.vsel), total: this.topo.groups.length };
    const pos = this.editObj.geometry.attributes.position.array as Float32Array;
    const topo = this.topo;

    this.editGroup = new T.Group();
    this.editGroup.matrixAutoUpdate = false;
    this.editObj.updateMatrixWorld(true);
    this.editGroup.matrix.copy(this.editObj.matrixWorld);
    this.editGroup.renderOrder = 900;

    // Dots. A very dense mesh gets its edges and faces but no dots: 200,000 sprites costs more
    // than it tells you, and the count in the header still says what is selected.
    const MAX_DOTS = 60000;
    if (topo.groups.length <= MAX_DOTS) {
      const p: number[] = [], c: number[] = [];
      const selCol = [1.0, 0.62, 0.16], offCol = [0.13, 0.15, 0.19], movedCol = [0.35, 0.78, 1.0];
      for (let g = 0; g < topo.groups.length; g++) {
        const a = groupAt(pos, topo, g);
        p.push(a[0], a[1], a[2]);
        // Blue for a corner the hand has already moved, so the work done so far is visible
        // without opening the sidecar.
        const col = this.vsel.has(g) ? selCol
          : (Math.abs(a[0] - topo.rest[topo.groups[g][0] * 3]) > 1e-6
            || Math.abs(a[1] - topo.rest[topo.groups[g][0] * 3 + 1]) > 1e-6
            || Math.abs(a[2] - topo.rest[topo.groups[g][0] * 3 + 2]) > 1e-6) ? movedCol : offCol;
        c.push(col[0], col[1], col[2]);
      }
      const g0 = new T.BufferGeometry();
      g0.setAttribute("position", new T.Float32BufferAttribute(p, 3));
      g0.setAttribute("color", new T.Float32BufferAttribute(c, 3));
      this.editDots = new T.Points(g0, new T.PointsMaterial({
        size: 6, sizeAttenuation: false, vertexColors: true, depthTest: !this.overlays.xray,
      }));
      this.editDots.frustumCulled = false;
      this.editGroup.add(this.editDots);
    }

    // Edges: all of them, so the cage reads, with the chosen ones lit.
    const ep: number[] = [], ec: number[] = [];
    for (const e of topo.edges) {
      const a = groupAt(pos, topo, e[0]), b = groupAt(pos, topo, e[1]);
      ep.push(a[0], a[1], a[2], b[0], b[1], b[2]);
      const on = this.vsel.has(e[0]) && this.vsel.has(e[1]);
      for (let k = 0; k < 2; k++) ec.push(on ? 1.0 : 0.18, on ? 0.62 : 0.2, on ? 0.16 : 0.26);
    }
    if (ep.length) {
      const g1 = new T.BufferGeometry();
      g1.setAttribute("position", new T.Float32BufferAttribute(ep, 3));
      g1.setAttribute("color", new T.Float32BufferAttribute(ec, 3));
      this.editLines = new T.LineSegments(g1, new T.LineBasicMaterial({
        vertexColors: true, transparent: true, opacity: 0.9, depthTest: !this.overlays.xray,
      }));
      this.editLines.frustumCulled = false;
      this.editGroup.add(this.editLines);
    }

    // Only the chosen faces are filled. Filling all of them would just be the model again.
    const fp: number[] = [];
    for (const t of topo.tris) {
      if (!(this.vsel.has(t[0]) && this.vsel.has(t[1]) && this.vsel.has(t[2]))) continue;
      for (const g of t) { const a = groupAt(pos, topo, g); fp.push(a[0], a[1], a[2]); }
    }
    if (fp.length) {
      const g2 = new T.BufferGeometry();
      g2.setAttribute("position", new T.Float32BufferAttribute(fp, 3));
      this.editFaces = new T.Mesh(g2, new T.MeshBasicMaterial({
        color: 0xff9e2c, transparent: true, opacity: 0.28, side: T.DoubleSide, depthWrite: false,
      }));
      this.editFaces.frustumCulled = false;
      this.editGroup.add(this.editFaces);
    }

    this.scene.add(this.editGroup);
  }

  // ------------------------------------------------------------------ selection
  /** What a click selects: one key, the first of `pickMany`. */
  pick(ndc: { x: number; y: number }, additive = false): string {
    return this.pickMany(ndc, additive)[0] || "";
  }

  /** What a click selects, as every key it means: one object, or every piece of a group. */
  pickMany(ndc: { x: number; y: number }, additive = false): string[] {
    this.ray.setFromCamera(ndc as any, this.camera);
    // Icons first: a light is a sprite with nothing behind it to hit, and it is drawn on top.
    if (this.overlays.extras && this.extraItems.length) {
      const sprites = this.extraItems.filter((i) => i.sprite.visible).map((i) => i.sprite);
      const sh = this.ray.intersectObjects(sprites, false);
      if (sh.length) return [sh[0].object.userData.key as string];
    }
    // The nearest surface first, found without testing every triangle under the ray (firstHit).
    const first = this.firstHit(ndc);
    if (first) {
      const k = this.pickFrom(first.object, additive, first.face);
      if (k.length) return k;
    }
    // A nearest hit with no keyed owner is rare; fall back to the full list, as it always was.
    this.ray.setFromCamera(ndc as any, this.camera);
    const hits = this.ray.intersectObject(this.subject, true);
    for (const h of hits) {
      if (!h.object.visible) continue;
      const k = this.pickFrom(h.object, additive, typeof h.faceIndex === "number" ? h.faceIndex : -1);
      if (k.length) return k;
    }
    return [];
  }

  /**
   * Which object a click on `hit` selects.
   *
   * An asset keeps the plain rule: its parts are what its code built, each one a thing to move, so
   * a click takes the nearest keyed owner of the triangle hit.
   *
   * A mirrored GAME has two traps, and the first rule fell into both. Every node has a key, down
   * to the skinned mesh inside a glTF armature, so "nearest owner" selected `character.003` and
   * moving it tore the skin off its bones. And "outermost owner that is not the whole level",
   * which replaced it, selected `base` in rot-rush — the whole plaza, 29 meshes, lit orange — for a
   * click on one treadmill. So a click now means a list of LEVELS, smallest first:
   *   1. the PIECE of a merged mesh under the cursor, when the mesh is one (see pieces.ts);
   *   2. the OBJECT: from the mesh hit, upward while the parent is part of the same thing — about
   *      its size, and not a folder of smaller things (`climbs`);
   *   3. each group above that which is still not the level itself;
   *   4. the parts inside the object, outside in, down to the mesh under the cursor.
   * The first click takes the first level; a click on what is already selected takes the next one,
   * and past the last it starts again — Unity's prefab-then-child, extended both ways. Shift adds
   * or removes the first level.
   */
  private pickFrom(hit: any, additive: boolean, face = -1): string[] {
    const chain: Array<{ o: any; k: string }> = [];
    for (let o = hit; o && o !== this.subject; o = o.parent) {
      const k = this.keys.get(o);
      if (k) chain.push({ o, k });
    }
    if (!chain.length) return [];
    if (!this.isLive) return [chain[0].k];
    const levels = this.levelsOf(chain, face);
    if (!levels.length) return [chain[0].k];
    let pick = levels[0];
    if (!additive) {
      // The level the selection IS, exactly — so a group, which is several pieces, is found too.
      const at = levels.findIndex((l) => l.keys.length === this.selection.size && l.keys.every((k) => this.selection.has(k)));
      if (at >= 0) pick = levels[(at + 1) % levels.length];
    }
    if (pick.split) {
      const made = pick.split.ranges.map((r) => this.splitPiece(pick.split!.mesh, pick.split!.meshKey, r.t0, r.t1, false)).filter(Boolean);
      this.index();
      if (!made.length) return levels.find((l) => !l.split)?.keys || [chain[0].k];
    }
    return pick.keys;
  }

  /** What one click can mean, smallest first — see `pickFrom`. A level is one key, or every piece
   *  of a group, which is selected as several pieces moving together. */
  private levelsOf(chain: Array<{ o: any; k: string }>, face: number):
    Array<{ keys: string[]; split?: { mesh: any; meshKey: string; ranges: Array<{ t0: number; t1: number }> } }> {
    const out: Array<{ keys: string[]; split?: { mesh: any; meshKey: string; ranges: Array<{ t0: number; t1: number }> } }> = [];
    let total = 0;
    this.subject.traverse((m: any) => { if (m.isMesh) total++; });
    const span = 2 * this.radiusNow();
    const leaf = chain[0];
    // A piece already split out is hit as itself; its levels are still the piece, its group, and
    // the mesh it came from.
    const own = leaf.o.userData?.piece;
    if (own && chain.length > 1 && chain[1].o.isMesh) {
      const host = chain[1];
      const map = this.piecesFor(host.o);
      const p = map?.pieces.find((q) => q.t0 === own.t0 && q.t1 === own.t1);
      out.push({ keys: [leaf.k] });
      const group = p && map ? groupOf(map, p) : [];
      if (group.length > 1) out.push({ keys: group.map((r) => pieceKey(host.k, r)), split: { mesh: host.o, meshKey: host.k, ranges: group } });
      for (let i = 1; i < chain.length; i++) {
        if (i > 1 && this.isLevel(chain[i].o, total, span)) break;
        out.push({ keys: [chain[i].k] });
      }
      return out;
    }
    const pc = this.pieceUnder(leaf.o, face);
    if (pc) {
      out.push({ keys: [pieceKey(leaf.k, pc.piece)], split: { mesh: leaf.o, meshKey: leaf.k, ranges: [pc.piece] } });
      if (pc.group.length > 1) out.push({ keys: pc.group.map((r) => pieceKey(leaf.k, r)), split: { mesh: leaf.o, meshKey: leaf.k, ranges: pc.group } });
      for (let i = 0; i < chain.length; i++) {
        if (i > 0 && this.isLevel(chain[i].o, total, span)) break;
        out.push({ keys: [chain[i].k] });
      }
      return out;
    }
    let obj = 0;
    while (obj + 1 < chain.length && this.climbs(chain[obj].o, chain[obj + 1].o, total, span)) obj++;
    for (let i = obj; i < chain.length; i++) {
      if (i > obj && this.isLevel(chain[i].o, total, span)) break;
      out.push({ keys: [chain[i].k] });
    }
    for (let i = obj - 1; i >= 0; i--) out.push({ keys: [chain[i].k] });
    return out;
  }

  /** A node that is the level rather than a thing in it: the root, or one holding most of the
   *  meshes, or spanning half the scene. */
  private isLevel(o: any, total: number, span: number): boolean {
    if (!o || o === this.subject || o === this.keyRoot()) return true;
    let meshes = 0;
    o.traverse((m: any) => { if (m.isMesh) meshes++; });
    if (meshes > Math.max(1, total * 0.5)) return true;
    return this.diagOf(o) > span * 0.5;
  }

  /**
   * Is `p` part of the same thing as its child `c`? About the same size — no more than three
   * times it, or a small thing of a couple of metres anyway — and not a folder of smaller things:
   * a group whose size is four times its typical child is a place things are put, not an object.
   * rot-rush: head → runner climbs (0.5 m in a 2 m character); treadmill sign → platform does
   * not (2 m in 50 m); a brainrot's mesh → its pickup climbs, the pickup → `actors` does not.
   */
  private climbs(c: any, p: any, total: number, span: number): boolean {
    if (this.isLevel(p, total, span)) return false;
    const cd = this.diagOf(c), pd = this.diagOf(p);
    if (!(pd > 0)) return true;
    const small = Math.max(2.5, span * 0.002);
    if (pd > Math.max(small, 3 * cd)) return false;
    if (pd > small) {
      const sizes = (p.children || []).map((k: any) => this.diagOf(k)).filter((d: number) => d > 0).sort((a: number, b: number) => a - b);
      const med = sizes.length ? sizes[sizes.length >> 1] : 0;
      if (med > 0 && pd > 4 * med) return false;
    }
    return true;
  }

  private diagOf(o: any): number {
    const T = this.T;
    const b = new T.Box3().setFromObject(o);
    return b.isEmpty() ? 0 : b.getSize(new T.Vector3()).length();
  }

  pickBone(ndc: { x: number; y: number }): string {
    if (!this.rig) return "";
    this.ray.setFromCamera(ndc as any, this.camera);
    const hits = this.ray.intersectObjects(this.rig.pickables(), false);
    return hits.length ? (hits[0].object.userData.boneName as string) : "";
  }

  select(keys: string[], additive = false) {
    if (!additive) this.selection.clear();
    for (const k of keys) { if (this.selection.has(k) && additive) this.selection.delete(k); else this.selection.add(k); }
    this.active = keys.length ? keys[keys.length - 1] : [...this.selection].pop() || "";
    if (!this.selection.has(this.active)) this.active = [...this.selection].pop() || "";
    this.placeGizmo();
    this.ev.onSelect([...this.selection], this.active);
  }

  selectAll() { const root = this.keyRoot(); this.select([...this.objs.keys()].filter((k) => this.objs.get(k)?.parent === root)); }
  deselectAll() { this.selection.clear(); this.active = ""; this.placeGizmo(); this.ev.onSelect([], ""); }

  selectBone(name: string, additive = false) {
    if (!additive) this.boneSelection.clear();
    if (name) { if (this.boneSelection.has(name) && additive) this.boneSelection.delete(name); else this.boneSelection.add(name); }
    this.activeBone = name;
    if (this.rig) highlightBone(this.rig, this.boneSelection, this.activeBone);
    this.placeGizmo();
  }

  /** Everything whose projected centre lands inside the rectangle. Blender's B, near enough. */
  boxSelect(r: { x0: number; y0: number; x1: number; y1: number }, additive: boolean) {
    const T = this.T;
    const x0 = Math.min(r.x0, r.x1), x1 = Math.max(r.x0, r.x1);
    const y0 = Math.min(r.y0, r.y1), y1 = Math.max(r.y0, r.y1);
    const hit: string[] = [];
    const box = new T.Box3();
    for (const [key, o] of this.objs) {
      if (!o.visible || o.parent !== this.keyRoot()) continue;
      box.makeEmpty();
      box.expandByObject(o);
      // A light or a camera has no box; it is where it is.
      const c = box.isEmpty() ? (o.isLight || o.isCamera ? o.getWorldPosition(new T.Vector3()) : null) : box.getCenter(new T.Vector3());
      if (!c) continue;
      const p = project(T, c, this.camera, this.size);
      if (!p.behind && p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1) hit.push(key);
    }
    this.select(hit, additive);
  }

  private placeGizmo() {
    const T = this.T;
    // A brush in the hand: the move arrows would sit in the middle of what is being painted.
    if (this.mode === "paint") { this.gizmo.show(false); return; }
    if (this.mode === "edit") {
      // On the middle of what is chosen, in world space, exactly as a multi-part selection does.
      if (!this.topo || !this.editObj || !this.vsel.size) { this.gizmo.show(false); return; }
      const pos = this.editObj.geometry.attributes.position.array as Float32Array;
      const c = new T.Vector3();
      for (const g of this.vsel) { const a = groupAt(pos, this.topo, g); c.add(new T.Vector3(a[0], a[1], a[2])); }
      c.divideScalar(this.vsel.size);
      this.editObj.updateMatrixWorld(true);
      c.applyMatrix4(this.editObj.matrixWorld);
      this.gizmo.place([c.x, c.y, c.z], this.editObj.getWorldQuaternion(new T.Quaternion()));
      this.gizmo.show(true);
      return;
    }
    if (this.mode === "pose") {
      const b = this.activeBone && this.rig?.byName.get(this.activeBone);
      if (!b) { this.gizmo.show(false); return; }
      b.updateMatrixWorld(true);
      // With IK on, the move gizmo sits on the TAIL — the end that gets dragged to a target.
      const ik = this.ikDepth > 0 && this.gizmo.mode === "move";
      const p = ik ? this.boneTail(b) : b.getWorldPosition(new T.Vector3());
      this.gizmo.place([p.x, p.y, p.z], b.getWorldQuaternion(new T.Quaternion()));
      this.gizmo.show(true);
      return;
    }
    const objs = [...this.selection].map((k) => this.objs.get(k)).filter(Boolean);
    if (!objs.length) { this.gizmo.show(false); return; }
    const box = new T.Box3();
    for (const o of objs) box.expandByObject(o);
    const c = box.isEmpty() ? objs[0].getWorldPosition(new T.Vector3()) : box.getCenter(new T.Vector3());
    const a = this.active && this.objs.get(this.active);
    this.gizmo.place([c.x, c.y, c.z], a ? a.getWorldQuaternion(new T.Quaternion()) : undefined);
    this.gizmo.show(true);
  }

  // ------------------------------------------------------------------ transforms
  beginDrag() {
    if (this.mode === "edit") {
      this.vert0 = new Map();
      if (!this.topo || !this.editObj) return;
      const pos = this.editObj.geometry.attributes.position.array as Float32Array;
      const c: V3 = [0, 0, 0];
      for (const g of this.vsel) {
        const a = groupAt(pos, this.topo, g);
        this.vert0.set(g, [a[0], a[1], a[2]]);
        c[0] += a[0]; c[1] += a[1]; c[2] += a[2];
      }
      const n = Math.max(1, this.vsel.size);
      // The pivot is kept in LOCAL space: a rotate or a scale of a vertex selection turns about
      // the middle of the selection, and doing that in world space would need the matrix twice.
      this.vertPivot0 = [c[0] / n, c[1] / n, c[2] / n];
      return;
    }
    if (this.mode === "pose") {
      this.pose0 = {};
      this.poseStart = this.posedNow();
      this.ikChain = [];
      const tipBone = this.activeBone && this.rig?.byName.get(this.activeBone);
      this.ikTail0 = tipBone ? this.boneTail(tipBone) : null;
      for (const n of this.boneSelection) {
        const b = this.rig?.byName.get(n);
        if (!b) continue;
        const e = new this.T.Euler().setFromQuaternion(
          b.userData.restQuat.clone().invert().multiply(b.quaternion), "XYZ");
        this.pose0[n] = [e.x, e.y, e.z];
      }
      return;
    }
    this.drag0 = new Map();
    for (const k of this.selection) {
      const o = this.objs.get(k);
      if (!o) continue;
      this.drag0.set(k, {
        pos: [o.position.x, o.position.y, o.position.z],
        rot: [o.rotation.x, o.rotation.y, o.rotation.z],
        scale: [o.scale.x, o.scale.y, o.scale.z],
      });
    }
  }

  private previewDelta(d: DragDelta) {
    const T = this.T;
    if (this.mode === "edit") {
      if (!this.vert0 || !this.topo || !this.editObj || !this.vertPivot0) return;
      const pos = this.editObj.geometry.attributes.position.array as Float32Array;
      // The gizmo speaks world; a vertex lives in the geometry's own frame. The delta comes home
      // through the mesh's OWN world transform, not its parent's, which is the one difference
      // from the object branch below.
      const wq = this.editObj.getWorldQuaternion(new T.Quaternion());
      const ws = this.editObj.getWorldScale(new T.Vector3());
      const inv = wq.clone().invert();
      const piv = new T.Vector3(...this.vertPivot0);
      let move: any = null;
      if (d.move) {
        move = new T.Vector3(...d.move).applyQuaternion(inv)
          .divide(new T.Vector3(ws.x || 1, ws.y || 1, ws.z || 1));
      }
      const axis = d.rotate ? new T.Vector3(...d.rotate.axis).applyQuaternion(inv).normalize() : null;
      const p = new T.Vector3();
      for (const [g, s] of this.vert0) {
        p.set(s[0], s[1], s[2]);
        if (move) p.add(move);
        if (axis && d.rotate) p.sub(piv).applyAxisAngle(axis, d.rotate.angle).add(piv);
        if (d.scale) {
          p.sub(piv);
          p.set(p.x * d.scale[0], p.y * d.scale[1], p.z * d.scale[2]);
          p.add(piv);
        }
        for (const i of this.topo.groups[g]) { pos[i * 3] = p.x; pos[i * 3 + 1] = p.y; pos[i * 3 + 2] = p.z; }
      }
      this.afterVertChange();
      return;
    }
    if (this.mode === "pose") {
      if (!this.pose0 || !this.rig) return;
      // Inverse kinematics: a MOVE of the active bone drags its tail, and the chain above it
      // follows. The solve starts from the pose at the start of the drag every time, so a long
      // drag does not accumulate its own history.
      if (d.move && this.ikDepth > 0 && this.activeBone && this.ikTail0 && this.poseStart) {
        applyPose(T, this.rig, this.poseStart);
        const target: V3 = [this.ikTail0.x + d.move[0], this.ikTail0.y + d.move[1], this.ikTail0.z + d.move[2]];
        const r = solveChain(T, this.rig, this.activeBone, target, this.ikDepth, this.poseStart);
        this.ikChain = r.chain;
        applyPose(T, this.rig, r.pose);
        this.placeGizmo();
        return;
      }
      const pose: Record<string, V3> = { ...this.posedNow(), };
      for (const [name, e0] of Object.entries(this.pose0)) {
        if (d.mode !== "rotate" || !d.rotate) continue;
        const b = this.rig.byName.get(name);
        if (!b) continue;
        const parentQ = new T.Quaternion();
        b.parent?.getWorldQuaternion(parentQ);
        // The gizmo hands back a WORLD axis; a bone pose is stored in its own rest frame, so the
        // axis has to come home through the parent and the rest rotation before it is applied.
        const local = new T.Vector3(...d.rotate.axis).applyQuaternion(parentQ.clone().invert());
        const q0 = new T.Quaternion().setFromEuler(new T.Euler(e0[0], e0[1], e0[2], "XYZ"));
        const dq = new T.Quaternion().setFromAxisAngle(local.normalize(), d.rotate.angle);
        const e = new T.Euler().setFromQuaternion(
          b.userData.restQuat.clone().invert().multiply(dq).multiply(b.userData.restQuat).multiply(q0), "XYZ");
        pose[name] = [e.x, e.y, e.z];
      }
      applyPose(T, this.rig, pose);
      this.placeGizmo();
      return;
    }
    if (!this.drag0) return;
    for (const [k, s] of this.drag0) {
      const o = this.objs.get(k);
      if (!o) continue;
      if (d.move) {
        // The delta is world; the object lives in its parent, so it arrives through the inverse.
        const v = new T.Vector3(...d.move);
        const pq = new T.Quaternion();
        o.parent?.getWorldQuaternion(pq);
        const ps = new T.Vector3(1, 1, 1);
        o.parent?.getWorldScale(ps);
        v.applyQuaternion(pq.invert());
        v.divide(new T.Vector3(ps.x || 1, ps.y || 1, ps.z || 1));
        o.position.set(s.pos[0] + v.x, s.pos[1] + v.y, s.pos[2] + v.z);
      }
      if (d.rotate) {
        const pq = new T.Quaternion();
        o.parent?.getWorldQuaternion(pq);
        const axis = new T.Vector3(...d.rotate.axis).applyQuaternion(pq.invert()).normalize();
        const q0 = new T.Quaternion().setFromEuler(new T.Euler(s.rot[0], s.rot[1], s.rot[2], o.rotation.order));
        const dq = new T.Quaternion().setFromAxisAngle(axis, d.rotate.angle);
        o.quaternion.copy(dq.multiply(q0));
      }
      if (d.scale) {
        o.scale.set(s.scale[0] * d.scale[0], s.scale[1] * d.scale[1], s.scale[2] * d.scale[2]);
      }
    }
    this.subject.updateMatrixWorld(true);
    this.placeGizmo();
  }

  private commitDelta(d: DragDelta) {
    this.previewDelta(d);
    if (this.mode === "edit") {
      // The whole list for this mesh, not a delta: replacing it wholesale means applying the
      // document twice can never drift, and undo is the previous list rather than an inverse.
      this.ev.onEdit("vert", this.editKey, this.vertEditsNow());
      this.vert0 = null;
      this.vertPivot0 = null;
      return;
    }
    if (this.mode === "pose") {
      const pose = this.posedNow();
      const touched = new Set<string>([...this.boneSelection, ...this.ikChain]);
      for (const n of touched) if (pose[n]) this.ev.onEdit("pose", n, pose[n]);
      this.pose0 = null;
      this.poseStart = null;
      this.ikChain = [];
      return;
    }
    for (const k of this.selection) {
      const o = this.objs.get(k);
      if (!o) continue;
      this.ev.onEdit("part", k, this.xfOf(o));
    }
    this.drag0 = null;
  }

  private revertDelta() {
    if (this.mode === "edit") {
      if (this.vert0 && this.topo && this.editObj) {
        const pos = this.editObj.geometry.attributes.position.array as Float32Array;
        for (const [g, s] of this.vert0) {
          for (const i of this.topo.groups[g]) { pos[i * 3] = s[0]; pos[i * 3 + 1] = s[1]; pos[i * 3 + 2] = s[2]; }
        }
        this.afterVertChange();
      }
      this.vert0 = null;
      this.vertPivot0 = null;
      return;
    }
    if (this.mode === "pose") {
      if (this.pose0 && this.rig) applyPose(this.T, this.rig, { ...this.posedNow(), ...this.pose0 });
      this.pose0 = null;
      this.placeGizmo();
      return;
    }
    if (this.drag0) {
      for (const [k, s] of this.drag0) {
        const o = this.objs.get(k);
        if (!o) continue;
        o.position.set(...s.pos);
        o.rotation.set(...s.rot);
        o.scale.set(...s.scale);
      }
      this.subject.updateMatrixWorld(true);
    }
    this.drag0 = null;
    this.placeGizmo();
  }

  posedNow(): Record<string, V3> {
    const out: Record<string, V3> = {};
    if (!this.rig) return out;
    for (const b of this.rig.bones) {
      const e = new this.T.Euler().setFromQuaternion(
        b.userData.restQuat.clone().invert().multiply(b.quaternion), "XYZ");
      if (Math.abs(e.x) + Math.abs(e.y) + Math.abs(e.z) > 1e-6) out[b.name] = [r5(e.x), r5(e.y), r5(e.z)];
    }
    return out;
  }

  setGizmoMode(m: GizmoMode) { this.gizmo.setMode(m); }
  setGizmoSpace(s: "global" | "local") { this.gizmo.space = s; this.placeGizmo(); }

  // ------------------------------------------------------------------ overrides
  /** Put the saved edits back on top of a freshly built asset. By name, so a rebuild with new
   *  parameters keeps them — which is the whole reason overrides exist rather than a saved mesh. */
  applyOverrides(edits: Edits) {
    // A piece with an edit is split out first, so the edit lands on an object the editor can
    // select again — the game gets the same edit as corners.
    let split = 0;
    for (const key of Object.keys(edits.parts || {})) {
      const pk = parsePieceKey(key);
      if (!pk || this.objs.has(key)) continue;
      const host = this.objs.get(pk.mesh);
      if (host?.isMesh && this.splitPiece(host, pk.mesh, pk.t0, pk.t1, false)) split++;
    }
    if (split) this.index();
    // The same function a game calls on its own copy of the sidecar. One implementation, so the
    // editor can never show an edit the game would apply differently.
    applyEdits(this.keyRoot(), { parts: edits.parts, mods: [] });
    for (const it of this.extraItems) if (it.obj.isLight && edits.parts[it.key]?.color) this.setProp(it.key, "color", edits.parts[it.key].color!);
    this.subject.updateMatrixWorld(true);
    this.placeGizmo();
  }

  /**
   * Run the modifier stack over what the code just built.
   *
   * In order, and after every rebuild — that is the whole difference between a modifier and a
   * one-shot edit. A weld baked once would be thrown away the next time a slider moved; a weld in
   * the stack is re-applied to the new geometry, so the model stays welded while it changes shape.
   *
   * `skin` and any whole-asset op replace the subject with a single merged mesh, which is exactly
   * what it is for: thirty primitives in, one surface out.
   */
  applyMods(mods: Mod[]): { applied: number; errors: string[] } {
    const live = mods.filter((m) => !m.off);
    if (!live.length) return { applied: 0, errors: [] };
    const r = applyEdits(this.keyRoot(), { parts: {}, mods: live }, this.ops);
    const errors = [...r.errors, ...r.missing.map((k) => "no part called " + k)];
    const applied = r.mods;
    if (applied) {
      this.subject.updateMatrixWorld(true);
      this.index();
      this.selection = new Set([...this.selection].filter((k) => this.objs.has(k)));
      if (!this.objs.has(this.active)) this.active = "";
      this.placeGizmo();
    }
    return { applied, errors };
  }

  /** The defect report for whatever is selected, or the whole asset. */
  inspect(key = ""): Defects {
    const target = key ? this.objs.get(key) : this.subject;
    return this.ops.check(target || this.subject);
  }

  resetPart(key: string) {
    const o = this.objs.get(key);
    const b = this.baseXf.get(key);
    if (!o || !b) return;
    o.position.set(...b.pos);
    o.rotation.set(...b.rot);
    o.scale.set(...b.scale);
    o.visible = true;
    this.subject.updateMatrixWorld(true);
    this.placeGizmo();
  }

  setVisible(key: string, on: boolean) {
    const o = this.objs.get(key);
    if (o) o.visible = on;
  }

  // ------------------------------------------------------------------ rig
  /** How a skin binding weighs its vertices; heat is Blender's automatic weights. */
  weightMethod: WeightMethod = "envelope";

  buildRigFrom(specs: Edits["bones"], mode: "parts" | "skin", pose: Record<string, V3>, weights?: WeightMethod) {
    this.clearRig();
    if (!specs.length) return;
    if (weights) this.weightMethod = weights;
    this.rig = buildRig(this.T, specs, mode);
    this.scene.add(this.rig.root);
    if (mode === "skin") bindSkin(this.T, this.rig, this.subject, 4, this.weightMethod);
    else bindParts(this.T, this.rig, this.subject);
    applyPose(this.T, this.rig, pose);
    this.rig.setDisplay(this.overlays.bones, this.overlays.bonesInFront);
    highlightBone(this.rig, this.boneSelection, this.activeBone);
    // Binding swaps meshes for skinned meshes, so the key index no longer points at what is drawn.
    this.index();
  }

  clearRig() {
    if (!this.rig) return;
    unbind(this.rig);
    try { this.scene.remove(this.rig.root); } catch { /* never added */ }
    this.rig.dispose();
    this.rig = null;
    this.boneSelection.clear();
    this.activeBone = "";
    if (this.mode === "pose") this.mode = "object";
  }

  setMode(m: Mode) {
    const want = m === "pose" && !this.rig ? "object" : m;
    // Entering and leaving edit mode is not a flag change: a corner graph has to be built against
    // the mesh, and torn down after, or the overlay outlives the geometry it describes.
    if (want === "edit") { if (this.mode !== "edit") this.enterEdit(); return; }
    if (this.mode === "edit") this.leaveEdit();
    this.mode = want;
    // The ring belongs to Terrain mode alone. Left on, it hangs over the object viewport
    // circling ground nobody is sculpting.
    if (want !== "terrain") this.terrain?.setCursor(null);
    if (want === "paint") { try { this.ensurePaint().scan(); } catch { /* the panel says what failed */ } }
    this.placeGizmo();
  }

  /** Vertices, edges or faces. Only how many corners one click adds — never what is stored. */
  setElem(k: ElemKind) {
    this.elem = k;
    this.refreshEdit();
  }

  // ------------------------------------------------------------------ animation
  /** Park the scene on a frame. Everything the clip does not key is left exactly as it was, so
   *  scrubbing a clip that only animates a tail does not reset the rest of the pose. */
  setFrame(f: number, basePose: Record<string, V3>) {
    this.frame = f;
    if (!this.clip) return;
    const at = evalClip(this.clip, f);
    if (this.rig) {
      const pose: Record<string, V3> = { ...basePose };
      for (const [name, o] of Object.entries(at)) if (o.rot) pose[name] = o.rot;
      applyPose(this.T, this.rig, pose);
    }
    for (const [key, o] of Object.entries(at)) {
      const t = this.objs.get(key);
      if (!t) continue;
      if (o.pos) t.position.set(o.pos[0], o.pos[1], o.pos[2]);
      if (o.rot && !this.rig?.byName.has(key)) t.rotation.set(o.rot[0], o.rot[1], o.rot[2]);
      if (o.scale) t.scale.set(o.scale[0], o.scale[1], o.scale[2]);
    }
    this.subject.updateMatrixWorld(true);
    this.placeGizmo();
  }

  // ------------------------------------------------------------------ terrain

  /** Put a heightfield in the viewport, or take it out. Handing in the same field twice is free,
   *  so the editor can call this on every render without rebuilding half a million triangles. */
  showTerrain(data: TerrainData | null, env: TerrainViewEnv): TerrainView | null {
    if (this.terrain && this.terrain.data === data) { this.terrain.env = env; return this.terrain; }
    if (this.terrain) { this.terrain.dispose(); this.terrain = null; }
    if (!data) return null;
    try {
      this.terrain = new TerrainView(this.T, data, env);
      this.scene.add(this.terrain.group);
    } catch (e: any) {
      // The ground failing to build must not take the asset with it: the outliner, the numbers
      // and the props the code made are all still true, and the message belongs on screen.
      this.terrain = null;
      this.lastError = this.lastError || ("terrain: " + String(e?.message || e)).slice(0, 300);
      try { this.onRenderError?.(this.lastError); } catch { /* the listener's problem */ }
    }
    return this.terrain;
  }

  /** Where the cursor meets the ground, in world units, or null off the edge of the field. */
  terrainHit(ndc: { x: number; y: number }): { x: number; z: number } | null {
    if (!this.terrain) return null;
    this.ray.setFromCamera(ndc as any, this.camera);
    return this.terrain.hit(this.ray.ray);
  }

  /** Which scattered item is under the cursor, drawn as a clone or as one of ten thousand
   *  instances. Null when the ray met the ground, the sky, or a tree the field does not know. */
  terrainScatterHit(ndc: { x: number; y: number }): { index: number; item: ScatterItem } | null {
    if (!this.terrain) return null;
    this.ray.setFromCamera(ndc as any, this.camera);
    try { return this.terrain.pickScatter(this.ray); } catch { return null; }
  }

  // ------------------------------------------------------------------ rendering
  setShading(s: Shading) { this.shading = s; }
  setOverlays(o: Partial<Overlays>) {
    this.overlays = { ...this.overlays, ...o };
    this.grid.mesh.visible = this.overlays.grid;
    this.grid.setAxes(this.overlays.axes);
    if (this.rig) this.rig.setDisplay(this.overlays.bones, this.overlays.bonesInFront);
    if (!this.overlays.normals && this.normalsObj) { drop(this.scene, this.normalsObj); this.normalsObj = null; }
    if (!this.overlays.origins && this.originsObj) { drop(this.scene, this.originsObj); this.originsObj = null; }
  }

  /** The normals overlay is rebuilt rather than kept: it is a snapshot of geometry that a
   *  parameter change replaces wholesale, and a stale one points the wrong way. */
  refreshNormals() {
    if (this.normalsObj) { drop(this.scene, this.normalsObj); this.normalsObj = null; }
    if (!this.overlays.normals) return;
    const b = this.boundsOf(this.subject);
    if (!b) return;
    this.normalsObj = normalLines(this.T, this.subject, b.radius * 0.05);
    this.scene.add(this.normalsObj);
  }

  refreshOrigins() {
    if (this.originsObj) { drop(this.scene, this.originsObj); this.originsObj = null; }
    if (!this.overlays.origins) return;
    const T = this.T;
    const pts: number[] = [];
    const p = new T.Vector3();
    for (const [, o] of this.objs) { o.getWorldPosition(p); pts.push(p.x, p.y, p.z); }
    if (!pts.length) return;
    const g = new T.BufferGeometry();
    g.setAttribute("position", new T.Float32BufferAttribute(pts, 3));
    const m = new T.PointsMaterial({ color: THEME.active, size: 7, sizeAttenuation: false, depthTest: false });
    this.originsObj = new T.Points(g, m);
    this.originsObj.renderOrder = 890;
    this.originsObj.frustumCulled = false;
    this.scene.add(this.originsObj);
  }

  /** Every Solid stand-in returned to the driver. Called whenever the subject is re-indexed,
   *  because the materials the cache is keyed by are gone by then. */
  private dropSolidMats() {
    for (const set of this.solidMats.values()) {
      for (const m of [set.lit, set.flat, set.line, set.points]) m?.dispose?.();
    }
    this.solidMats.clear();
  }

  /** The Solid stand-in for one real material, made once and remembered. */
  private solidStandIn(src: any, kind: "mesh" | "line" | "points", flat: boolean): any {
    const slot = kind === "mesh" ? (flat ? "flat" : "lit") : kind;
    let have = this.solidMats.get(src);
    if (!have) { have = {}; this.solidMats.set(src, have); }
    if (!have[slot]) have[slot] = solidColorMaterial(this.T, src, kind, flat);
    return have[slot];
  }

  /** The same, for whatever a mesh actually carries — one material or a list of them. */
  private solidFor(src: any, o: any, flat: boolean): any {
    const kind = o.isLine ? "line" : o.isPoints ? "points" : "mesh";
    if (Array.isArray(src)) return src.map((s) => this.solidStandIn(s, kind, flat));
    return this.solidStandIn(src, kind, flat);
  }

  /**
   * The one material every surface is drawn with this frame, or `null` to leave each part with
   * its own, or a function to derive one PER PART from the one it already has.
   *
   * The third case is Solid with "Colour: Material": the part keeps its base colour and its
   * colour map, and loses everything else. Solid used to be a single grey, which meant the
   * viewport the editor opens on could not show that an asset had any colour at all.
   */
  private subjectMaterialFor(): any | null | ((src: any, o: any) => any) {
    if (this.shading === "material" || this.shading === "rendered") return null;
    if (this.shading === "wire") return this.mats.wire;
    if (this.solidLight === "matcap") return this.mats.matcap;
    if (this.solidColor === "single") return this.solidLight === "flat" ? this.mats.flat : this.mats.solid;
    const flat = this.solidLight === "flat";
    return (src: any, o: any) => this.solidFor(src, o, flat);
  }

  private swap(mat: any | null | ((src: any, o: any) => any), only?: any[]) {
    const list = only ?? [this.subject];
    const derive = typeof mat === "function" ? mat : null;
    for (const root of list) {
      root.traverse((o: any) => {
        if (!o.isMesh && !o.isPoints && !o.isLine) return;
        if (mat) {
          if (o.userData.__mat === undefined) o.userData.__mat = o.material;
          // Always derived from the part's OWN material, never from whatever a previous pass
          // happened to leave on it.
          o.material = derive ? derive(o.userData.__mat, o) : mat;
        } else if (o.userData.__mat !== undefined) {
          o.material = o.userData.__mat;
          delete o.userData.__mat;
        }
      });
    }
  }

  render() {
    // EVERY PASS COUNTED. three resets `info` at each render() call, so a count read after the
    // last pass — the gizmo's — said "0 draws" whenever nothing was selected, whatever the subject
    // was doing. One manual reset per frame, and the number means the frame.
    const info = this.renderer.info;
    info.autoReset = false;
    info.reset();
    const now = performance.now();
    if (this.lastRender) {
      this.frameTimes.push(now - this.lastRender);
      if (this.frameTimes.length > 40) this.frameTimes.shift();
      // Flying moves by TIME, not by frame, so a slow frame does not slow the flight down.
      if (this.nav.flying && this.nav.flyKeys.size) {
        let moving = false;
        for (const k of this.nav.flyKeys) if (k !== "ShiftLeft" && k !== "ShiftRight") { moving = true; break; }
        if (moving) { this.flyTakeOff(); this.nav.flyTick((now - this.lastRender) / 1000); }
      }
    }
    this.lastRender = now;

    const r = this.renderer;
    // The camera object itself may have been moved this frame, by the gizmo or by the code.
    if (this.nav.through) this.syncCamera();
    const cam = this.camera;
    const eye = cam.getWorldPosition(new this.T.Vector3());
    this.grid.update(eye, this.nav.dist);
    // The chunked ground picks its detail from where the camera is. It does nothing at all until
    // the eye has moved half a chunk radius — see `TerrainView.frame` — so this costs one
    // distance test on the frames between.
    if (this.terrain) { try { this.terrain.frame(eye); } catch { /* the ground is not the frame */ } }
    this.gizmo.update(cam, this.size.h);
    // The overlay is not parented to the mesh, so it has to be told where the mesh went.
    if (this.editGroup && this.editObj) {
      this.editObj.updateMatrixWorld(true);
      this.editGroup.matrix.copy(this.editObj.matrixWorld);
    }
    this.applyWorld();
    this.updateExtras(cam);

    // X-ray: Blender's see-through. Every surface goes translucent, which is the only way to
    // pick a bone inside a body or check that a limb is where it looks like it is.
    const xray = this.overlays.xray;
    if (xray) this.subject.traverse((o: any) => {
      if (!o.isMesh) return;
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        if (!m) continue;
        if (m.userData.__xray === undefined) { m.userData.__xray = { t: m.transparent, o: m.opacity, d: m.depthWrite }; }
        m.transparent = true; m.opacity = 0.34; m.depthWrite = false;
      }
    });

    // The painted textures are brought up to date before anything samples them this frame. A
    // failure here costs the paint, never the frame.
    if (this.paint) { try { this.paint.frame(); } catch (e) { this.paint.note = "paint could not draw: " + String((e as any)?.message || e).slice(0, 160); } }
    const base = this.subjectMaterialFor();
    this.swap(base);
    r.autoClear = true;
    r.render(this.scene, cam);
    r.autoClear = false;

    // The passes that answer a question the lit render cannot, each drawn over the top and only
    // over the subject: rendering the whole scene again would redraw the floor four times.
    // NOT IN EDIT MODE, which is Blender's rule too: there the corners and edges ARE the selection,
    // and an object outline drawn over them hid the dots — while a click on empty space cleared
    // only the corners, so the orange stayed and the selection looked impossible to drop.
    if (this.overlays.outline && this.selection.size && this.shading !== "wire" && this.mode !== "edit" && this.mode !== "paint") {
      const sel = [...this.selection].map((k) => this.objs.get(k)).filter(Boolean);
      const activeObj = this.objs.get(this.active);
      for (const o of sel) {
        this.swap(o === activeObj ? this.mats.outlineActive : this.mats.outline, [o]);
        r.render(o, cam);
        this.swap(null, [o]);
      }
      this.swap(base);
    }
    if (this.overlays.wireframe && this.shading !== "wire") {
      this.swap(this.mats.wireOverlay);
      r.render(this.subject, cam);
    }
    if (this.overlays.faces) {
      this.swap(this.mats.faces);
      r.render(this.subject, cam);
    }
    this.swap(null);

    if (xray) this.subject.traverse((o: any) => {
      if (!o.isMesh) return;
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        const s = m?.userData?.__xray;
        if (!s) continue;
        m.transparent = s.t; m.opacity = s.o; m.depthWrite = s.d;
        delete m.userData.__xray;
      }
    });

    // The parameter handles sit on the box the main pass has just placed, and live in the gizmo's
    // scene, so the one pass below draws them over everything too.
    this.handleLayer?.update();
    // The gizmo lives in its own scene and is drawn last onto a cleared depth buffer, so no
    // overlay pass can ever cover the handle the pointer is on.
    r.clearDepth();
    r.render(this.gizmoScene, cam);
    r.autoClear = true;
    this.lastCalls = info.render.calls;
  }

  /**
   * The frame loop's entry: render, and turn a throw into a message instead of a blank.
   *
   * The failure this exists for was real: a model built by a second copy of three loaded, filled
   * the outliner, counted 10,770 triangles — and the renderer threw `material.onBuild is not a
   * function` on every frame, inside requestAnimationFrame, where no catch and no UI ever saw it.
   * The first error is kept and handed to the editor once; the count says whether it is still
   * happening; the draw count goes to zero so the status bar cannot claim otherwise.
   */
  renderSafe() {
    try {
      this.render();
    } catch (e: any) {
      this.lastCalls = 0;
      this.renderErrors++;
      if (!this.lastError) {
        const msg = String(e?.stack || e?.message || e).split("\n").slice(0, 3).join("\n").slice(0, 600);
        this.lastError = msg;
        try { this.onRenderError?.(msg); } catch { /* the listener's problem, not the frame's */ }
      }
      // A frame that dies half-way leaves the renderer's own flags wherever it got to.
      try { this.renderer.autoClear = true; } catch { /* lost context */ }
    }
  }

  /**
   * Which lights and which world are drawn this frame.
   *
   * The studio rig hides when the asset's own lights are in use. The asset's lights are switched
   * by LAYER rather than by `visible`, because `visible` is the user's — it is what the outliner
   * eye and the hidden override mean — and a light hidden by the user must stay dark under either
   * rig. A light on no layer lights nothing, and nothing else in the editor reads layers.
   */
  private applyWorld() {
    const useLights = this.sceneLights && this.assetLights.length > 0;
    this.lights.visible = !useLights;
    for (const l of this.assetLights) l.layers.mask = useLights ? 1 : 0;
    const s = this.studioWorld, a = this.assetWorld, e = this.worldEdit;
    if (!s) return;
    const useWorld = this.sceneWorld && (!!a || !!e);
    let bg = a?.background ?? null, fog = a?.fog ?? null;
    const env = a?.environment ?? null;
    // The edit goes on top of what the code set. Colour and fog objects are made once per value,
    // not once per frame.
    if (e) {
      const T = this.T, c = this.worldCache;
      if (e.background) {
        if (c.bgHex !== e.background) { c.bg = new T.Color(e.background); c.bgHex = e.background; }
        bg = c.bg;
      }
      if (e.fog === null) fog = null;
      else if (e.fog) {
        const key = JSON.stringify(e.fog);
        if (c.fogKey !== key) {
          c.fog = e.fog.type === "exp2"
            ? new T.FogExp2(e.fog.color, e.fog.density ?? 0.02)
            : new T.Fog(e.fog.color, e.fog.near ?? 1, e.fog.far ?? 50);
          c.fogKey = key;
        }
        fog = c.fog;
      }
    }
    this.scene.background = useWorld && bg ? bg : s.background;
    this.scene.fog = useWorld ? fog : s.fog;
    this.scene.environment = useWorld && env ? env : s.environment;
  }

  /** The code's own world as plain values, for the World panel to start from. Null when the
   *  code set none — the panel then edits a world that only the Scene it returns will carry. */
  assetWorldSpec(): { background?: string; fog?: { type: "linear" | "exp2"; color: string; near?: number; far?: number; density?: number } | null } | null {
    const a = this.assetWorld;
    if (!a) return null;
    const out: ReturnType<EditWorld["assetWorldSpec"]> = {};
    if (a.background?.isColor) out!.background = "#" + a.background.getHexString();
    const f = a.fog;
    out!.fog = !f ? null : f.isFogExp2
      ? { type: "exp2", color: "#" + f.color.getHexString(), density: f.density }
      : { type: "linear", color: "#" + f.color.getHexString(), near: f.near, far: f.far };
    return out;
  }

  /**
   * A scene handed over as three's own JSON — what a running game gives through the live link.
   * The ObjectLoader is core three, so the copy is faithful down to the materials; textures
   * arrive as data URLs and fill in as they decode.
   */
  async loadJSON(json: any): Promise<{ error: string }> {
    for (const c of this.subject.children.slice()) drop(this.subject, c);
    this.clearRig();
    this.beforeBuild();
    try {
      const loader = new this.T.ObjectLoader();
      const obj = await new Promise<any>((res, rej) => {
        try { loader.parse(json, (o: any) => res(o)); } catch (e) { rej(e); }
      });
      // What the game had switched off, marked the way the PlayCanvas mirror marks it, so the same
      // rule decides which of it is level the game culled and which is off for a reason.
      if (obj?.isObject3D) obj.traverse((o: any) => { if (o !== obj && o.visible === false) o.userData.gameHidden = true; });
      if (obj?.isObject3D) this.subject.add(obj);
      this.afterBuild(obj);
      return { error: obj?.isObject3D ? "" : "the snapshot held no object" };
    } catch (e: any) {
      this.afterBuild(null);
      return { error: String(e?.message || e).slice(0, 800) };
    }
  }

  shot(): string {
    try { this.render(); return this.canvas.toDataURL("image/png"); }
    catch (e) { return "error:" + String(e).slice(0, 200); }
  }

  dispose() {
    this.handleLayer?.dispose();
    this.handleLayer = null;
    try { this.paint?.dispose(); } catch { /* the context may be gone already */ }
    this.paint = null;
    this.clearRig();
    this.terrain?.dispose();
    this.terrain = null;
    for (const c of this.subject.children.slice()) drop(this.subject, c);
    if (this.normalsObj) drop(this.scene, this.normalsObj);
    if (this.originsObj) drop(this.scene, this.originsObj);
    drop(this.scene, this.extras);
    this.grid.dispose();
    this.gizmo.dispose();
    this.dropSolidMats();
    for (const m of Object.values(this.mats)) { try { m.dispose?.(); } catch { /* gone */ } }
    try { this.scene.environment?.dispose?.(); } catch { /* gone */ }
    this.renderer.dispose();
    // A viewer that keeps a context per open is blank within a minute: a browser allows about
    // sixteen, and dispose() alone leaves this one alive until the canvas is collected.
    try { this.renderer.forceContextLoss?.(); } catch { /* already lost */ }
    void this.lights;
  }
}

// ------------------------------------------------------------------ terrain
//
// THE GROUND, DRAWN — and the two things the drawing has to get right for the brush above it to
// be usable at all.
//
// ONLY WHAT CHANGED. A stroke is sixty pointer moves a second and each one moves a few thousand
// samples of a field that may hold 263,169. Measured on 513 by 513 with a 24-unit brush: the
// region a stroke touches rebuilds in about 0.25 ms, and the whole field in 18 to 20 ms — some
// 75 times more, and over a 60 fps budget on its own before a single triangle is drawn. So
// `update(region)` writes the changed band straight into the live attribute buffers and uploads
// only those rows; a whole frame of a held stroke, brush included, is under 0.52 ms.
//
// AND THE RAY IS NOT A RAYCAST. three's Raycaster walks every triangle it is given; at 513 by 513
// that is 524,288 of them per pointer move. A heightfield does not need it — march the ray one
// cell at a time in XZ and refine where it first dips under the surface, which is a few hundred
// `heightAt` calls and no allocation at all.

export interface TerrainViewEnv {
  /** A three object to CLONE for one scattered asset, or null while it is still being built.
   *  The editor owns the resolving — building the game's own tree means importing the game's own
   *  module — and this only ever clones what it is handed. */
  prototype(asset: string): any | null;
  /** A layer's texture path turned into a URL this page may fetch. */
  textureUrl?(path: string): string;
  /** ADDED, round two. One mesh per square of the field instead of one for the whole thing —
   *  see the note above `rebuildChunks`. Left out, the field draws as it always did. */
  chunks?: boolean;
  /** Samples per side of a chunk. Left out, `CHUNK_SAMPLES`. */
  chunkSamples?: number;
  /** ADDED, round two. Scattered items as one instanced draw call per asset. */
  instances?: boolean;
}

const RING_SEGS = 96;

/**
 * How many scattered items the preview will draw.
 *
 * A clone of a game builder is a whole sub-tree, not an instance: three thousand oaks is a
 * three-thousand-matrix traversal every frame and a viewport at 12 fps. Past this the FIELD still
 * holds them, the sidecar still saves them and the emitted builder still writes them — only the
 * preview thins out, and the panel says so rather than letting a person think the brush stopped.
 */
export const SCATTER_DRAW_MAX = 3000;

/**
 * How many scattered items the INSTANCED preview will draw.
 *
 * Two orders of magnitude more, because an instance is 64 bytes of matrix and no scene-graph
 * node at all: the cost per item stops being a traversal and becomes a memcpy. The cap is still
 * a cap — 100,000 instances is 6.4 MB of matrices per leaf mesh, and a forest that large wants
 * the emitted builder and the game's own culling, not a preview.
 */
export const SCATTER_INSTANCE_MAX = 100000;

/** Chunk rebuilds allowed in one frame when the camera's move changed some detail levels. Four
 *  chunks is about 1 ms; the rest wait for the next frame, which is invisible while flying and
 *  the difference between a smooth orbit and a hitch when the whole grid steps at once. */
const LOD_PER_FRAME = 4;

/** One chunk of ground, as the viewport holds it. */
interface ChunkMesh {
  ref: ChunkRef;
  mesh: any;
  geo: any;
  lod: number;
  /** Vertices the geometry was built with. A rebuild at the same lod writes into the buffers it
   *  already has; a different lod is a different count and has to start again. */
  verts: number;
}

/** One asset's instanced draw, one per leaf mesh of its prototype. */
interface InstBatch {
  asset: string;
  /** Where each instance sits in `t.scatter` — `ScatterBatch.index`, kept so a click on
   *  instance 7 can name the item it is. */
  index: Int32Array;
  count: number;
  capacity: number;
  meshes: any[];
  /** The leaf's own transform inside the prototype, or null when it is the identity — the case
   *  worth the branch, because then the batch's matrices go into the buffer as a memcpy. */
  locals: (any | null)[];
}

export class TerrainView {
  readonly group: any;
  readonly mesh: any;
  data: TerrainData;
  env: TerrainViewEnv;
  /** How many scattered items are on screen, and how many were left off. */
  drawn = 0;
  hidden = 0;

  private T: any;
  private geo: any;
  private mat: any;
  private uni: any;
  private ring: any;
  private inner: any;
  private scatterGroup: any;
  private placed = new Map<ScatterItem, any>();
  private texCache = new Map<string, any>();
  private blank: any;

  // ---- chunks. Everything here is inert when `chunked` is false. ----
  /** Whether the field is being drawn as chunks RIGHT NOW. Starts as the setting asked and goes
   *  false for good the first time the engine cannot do it, so a half-built terrain.ts costs one
   *  caught throw and not a throw a frame. */
  chunked = false;
  /** Why chunking is off when it was asked for. Empty when nothing went wrong. */
  chunkNote = "";
  private chunkN = 64;
  private chunkGroup: any;
  private chunks = new Map<string, ChunkMesh>();
  /** Where the camera was when the detail levels were last chosen, and how far it may move
   *  before the answers are worth asking for again. */
  private lodEye: [number, number, number] | null = null;
  private lodStep = 1e9;
  private lodQueue: ChunkRef[] = [];

  // ---- instanced scatter ----
  /** Whether the scatter is being drawn as instances right now. */
  instanced = false;
  private instGroup: any;
  private batches = new Map<string, InstBatch>();
  private tmpM: any;
  private tmpM2: any;

  constructor(T: any, data: TerrainData, env: TerrainViewEnv) {
    this.T = T;
    this.data = data;
    this.env = env;
    this.group = new T.Group();
    this.group.name = "__terrain";
    this.blank = whiteTexture(T);
    this.mat = this.makeMaterial();
    this.geo = new T.BufferGeometry();
    this.mesh = new T.Mesh(this.geo, this.mat);
    this.mesh.name = "terrain";
    this.mesh.receiveShadow = true;
    this.mesh.userData.terrain = true;
    this.group.add(this.mesh);

    this.chunkGroup = new T.Group();
    this.chunkGroup.name = "__chunks";
    this.group.add(this.chunkGroup);
    this.chunked = !!env.chunks;
    this.chunkN = Math.max(8, Math.floor(env.chunkSamples || 64));

    this.scatterGroup = new T.Group();
    this.scatterGroup.name = "__scatter";
    this.group.add(this.scatterGroup);
    this.instGroup = new T.Group();
    this.instGroup.name = "__scatterInstances";
    this.group.add(this.instGroup);
    this.instanced = !!env.instances;
    this.tmpM = new T.Matrix4();
    this.tmpM2 = new T.Matrix4();

    // Drawn with the depth test off, the way every sculpting cursor in the trade is: a ring that
    // a hill can hide is a ring you stop trusting the moment the ground is not flat.
    const ringMat = new T.LineBasicMaterial({ color: 0xffd08a, transparent: true, opacity: 0.95, depthTest: false });
    const innerMat = new T.LineBasicMaterial({ color: 0xffd08a, transparent: true, opacity: 0.36, depthTest: false });
    this.ring = new T.LineLoop(ringGeometry(T), ringMat);
    this.inner = new T.LineLoop(ringGeometry(T), innerMat);
    this.ring.renderOrder = 998;
    this.inner.renderOrder = 998;
    this.ring.frustumCulled = false;
    this.inner.frustumCulled = false;
    this.ring.visible = false;
    this.inner.visible = false;
    this.group.add(this.ring, this.inner);

    this.readLayers();
    this.rebuild();
  }

  // ---- the mesh ------------------------------------------------------------------------

  /** Everything. On open, on a resolution change, and whenever a region update finds the engine
   *  laid its answer out in a shape this one cannot splice. */
  rebuild() {
    if (this.chunked && this.rebuildChunks()) {
      // The whole-field mesh stays in the tree with nothing in it, so every reference the editor
      // already holds — `mesh.userData.terrain`, the shading swap — is still a live object.
      this.mesh.visible = false;
      this.syncScatter();
      return;
    }
    this.mesh.visible = true;
    this.chunkGroup.visible = false;
    const T = this.T;
    const m = terrainMesh(this.data);
    const n = m.positions.length / 3;
    this.geo.setAttribute("position", new T.BufferAttribute(m.positions, 3));
    this.geo.setAttribute("normal", new T.BufferAttribute(m.normals, 3));
    this.geo.setAttribute("uv", new T.BufferAttribute(m.uvs, 2));
    this.geo.setAttribute("aSplat", new T.BufferAttribute(weightsOf(m, n), 4));
    const col = new Float32Array(n * 3);
    rgbOf(m.colors, col, n, 0);
    this.geo.setAttribute("color", new T.BufferAttribute(col, 3));
    this.geo.setIndex(new T.BufferAttribute(m.indices, 1));
    this.geo.computeBoundingSphere();
    this.geo.computeBoundingBox();
    this.syncScatter();
  }

  /**
   * Only the band a stroke touched.
   *
   * The region is grown by one sample on every side before it is asked for: a vertex normal is
   * the average of the faces around it, so the row just outside a stroke has a stale normal and
   * the seam reads as a crease under a raking light.
   */
  update(region?: { x0: number; z0: number; w: number; h: number } | null) {
    if (!region || region.w <= 0 || region.h <= 0) return;
    if (this.chunked) {
      // ONLY THE CHUNKS THE STROKE TOUCHED, and the neighbours across a seam — which is what
      // `chunksFor` is for and why it is not just a division. A stroke on a 1025 field dirties
      // one or four of 256 chunks; rebuilding the field instead is 2.1 M triangles a frame.
      try {
        const refs = chunksFor(this.data, region.x0, region.z0, region.w, region.h, this.chunkN);
        for (const ref of refs) {
          const held = this.chunks.get(keyOf(ref));
          this.putChunk(ref, held ? held.lod : this.lodFor(ref));
        }
        return;
      } catch (e: any) {
        this.chunkFailed(e);
        // Fall through: the single-mesh path below rebuilds everything, which is slow and right.
      }
    }
    const res = this.data.spec.res;
    const pos = this.geo.getAttribute("position");
    if (!pos || pos.count !== res * res) { this.rebuild(); return; }
    const x0 = Math.max(0, region.x0 - 1), z0 = Math.max(0, region.z0 - 1);
    const x1 = Math.min(res, region.x0 + region.w + 1), z1 = Math.min(res, region.z0 + region.h + 1);
    const w = x1 - x0, h = z1 - z0;
    if (w <= 0 || h <= 0) return;

    let m: any;
    try { m = terrainMesh(this.data, { region: { x0, z0, w, h } }); }
    catch { this.rebuild(); return; }
    // An engine that returns a region mesh laid out any way other than one vertex per sample,
    // row-major, cannot be spliced into a whole-field buffer. Rebuilding is slow and correct.
    if (!m || m.positions.length !== w * h * 3) { this.rebuild(); return; }

    const nrm = this.geo.getAttribute("normal");
    const spl = this.geo.getAttribute("aSplat");
    const col = this.geo.getAttribute("color");
    for (let j = 0; j < h; j++) {
      const src = j * w;
      const dst = (z0 + j) * res + x0;
      (pos.array as Float32Array).set(m.positions.subarray(src * 3, (src + w) * 3), dst * 3);
      (nrm.array as Float32Array).set(m.normals.subarray(src * 3, (src + w) * 3), dst * 3);
      (spl.array as Float32Array).set(weightsOf(m, h * w).subarray(src * 4, (src + w) * 4), dst * 4);
      rgbOf(m.colors.subarray(src * 4, (src + w) * 4) as Float32Array, col.array as Float32Array, w, dst);
    }
    // One contiguous band of whole rows rather than h separate ranges: a few hundred vertices
    // either side of the region are uploaded needlessly, and it is one call instead of h.
    const from = z0 * res, count = h * res;
    mark(pos, from * 3, count * 3);
    mark(nrm, from * 3, count * 3);
    mark(spl, from * 4, count * 4);
    mark(col, from * 3, count * 3);
    this.geo.boundingSphere = null;
    this.geo.boundingBox = null;
  }

  // ---- chunks ----------------------------------------------------------------------------
  //
  // ONE MESH FOR THE WHOLE FIELD CANNOT BE CULLED AND CANNOT HAVE TWO DETAILS. A 1025-sample
  // field is 2,097,152 triangles in a single draw call whose bounding sphere covers the world:
  // stand in one corner looking away and every last triangle is still submitted, transformed and
  // depth-tested. Cut into 64-sample squares it is 256 draws, each with a bounding sphere around
  // its OWN heights, and three quarters of them are rejected before the vertex shader runs.
  //
  // The second half is detail. A chunk 400 metres away does not need a vertex a metre; `pickLod`
  // answers in radii rather than world units, so the same rule holds for a 64-unit field and a
  // 4096, and `terrainChunk` hangs a skirt off the rim so the crack where a coarse chunk meets a
  // fine one has ground behind it instead of sky.
  //
  // THE DEGRADE MATTERS AS MUCH AS THE FEATURE. Every entry point here is wrapped: the first
  // throw out of terrain.ts turns chunking off for the life of this view and hands the field back
  // to the single-mesh path, so an editor opened against a half-built engine draws ground rather
  // than a red frame.

  private lodFor(ref: ChunkRef): number {
    if (!this.lodEye) return 1;
    try { return Math.max(1, Math.floor(pickLod(ref, this.lodEye))); } catch { return 1; }
  }

  /**
   * Chunking is off from here on, and why. Called from a catch, so it must not throw.
   *
   * IT DOES NOT PUT THE GROUND BACK, and that caught me: the whole-field mesh is left in the tree
   * with an EMPTY geometry while chunking is on, so making it visible again shows nothing at all.
   * A photograph found it — every test was green and the field had simply gone. Every caller has
   * to follow this with a rebuild, and the two that cannot are the ones already inside one.
   */
  private chunkFailed(e: any) {
    this.chunked = false;
    this.chunkNote = String(e?.message || e).slice(0, 140);
    this.clearChunks();
    this.mesh.visible = true;
  }

  private clearChunks() {
    for (const [, c] of this.chunks) {
      this.chunkGroup.remove(c.mesh);
      try { c.geo.dispose(); } catch { /* gone */ }
    }
    this.chunks.clear();
    this.lodQueue = [];
    this.chunkGroup.visible = false;
  }

  /** Every chunk, at the detail the camera asks for. False when the engine cannot do it yet. */
  private rebuildChunks(): boolean {
    let refs: ChunkRef[];
    try { refs = chunkGrid(this.data, this.chunkN); } catch (e: any) { this.chunkFailed(e); return false; }
    if (!refs?.length) { this.chunkFailed(new Error("the field has no chunks")); return false; }
    // A chunk grid that shrank — a smaller field opened into the same view — leaves meshes for
    // squares that are no longer there, and they would sit in the scene as floating ground.
    const want = new Set(refs.map(keyOf));
    for (const [k, c] of [...this.chunks]) {
      if (want.has(k)) continue;
      this.chunkGroup.remove(c.mesh);
      try { c.geo.dispose(); } catch { /* gone */ }
      this.chunks.delete(k);
    }
    let smallest = Infinity;
    for (const ref of refs) {
      if (!this.putChunk(ref, this.lodFor(ref))) return false;
      if (ref.radius > 0 && ref.radius < smallest) smallest = ref.radius;
    }
    // Half the smallest chunk's radius. `pickLod`'s answer steps at whole multiples of a radius,
    // so a camera that has moved less than that cannot have changed one.
    this.lodStep = Number.isFinite(smallest) ? Math.max(0.5, smallest * 0.5) : 1e9;
    this.chunkGroup.visible = true;
    this.chunkNote = "";
    return true;
  }

  /** One chunk built or rebuilt. False means the engine threw and chunking is now off. */
  private putChunk(ref: ChunkRef, lod: number): boolean {
    const T = this.T;
    let m: MeshArrays;
    let skirt = 0;
    try {
      const c = terrainChunk(this.data, ref.cx, ref.cz, { chunk: this.chunkN, lod });
      m = c.mesh;
      skirt = c.skirt || 0;
    } catch (e: any) { this.chunkFailed(e); return false; }
    if (!m?.positions?.length) { this.chunkFailed(new Error("chunk " + ref.cx + "," + ref.cz + " came back empty")); return false; }

    const verts = m.positions.length / 3;
    const key = keyOf(ref);
    let held = this.chunks.get(key);
    if (held && held.verts !== verts) {
      this.chunkGroup.remove(held.mesh);
      try { held.geo.dispose(); } catch { /* gone */ }
      this.chunks.delete(key);
      held = undefined;
    }
    const col = new Float32Array(verts * 3);
    rgbOf(m.colors, col, verts, 0);
    if (!held) {
      const geo = new T.BufferGeometry();
      geo.setAttribute("position", new T.BufferAttribute(m.positions, 3));
      geo.setAttribute("normal", new T.BufferAttribute(m.normals, 3));
      geo.setAttribute("uv", new T.BufferAttribute(m.uvs, 2));
      geo.setAttribute("aSplat", new T.BufferAttribute(weightsOf(m, verts), 4));
      geo.setAttribute("color", new T.BufferAttribute(col, 3));
      geo.setIndex(new T.BufferAttribute(m.indices, 1));
      const mesh = new T.Mesh(geo, this.mat);
      mesh.name = "terrain." + ref.cx + "." + ref.cz;
      mesh.receiveShadow = true;
      mesh.userData.terrain = true;
      mesh.userData.chunk = [ref.cx, ref.cz];
      this.chunkGroup.add(mesh);
      held = { ref, mesh, geo, lod, verts };
      this.chunks.set(key, held);
    } else {
      const g = held.geo;
      (g.getAttribute("position").array as Float32Array).set(m.positions);
      (g.getAttribute("normal").array as Float32Array).set(m.normals);
      (g.getAttribute("uv").array as Float32Array).set(m.uvs);
      (g.getAttribute("aSplat").array as Float32Array).set(weightsOf(m, verts));
      (g.getAttribute("color").array as Float32Array).set(col);
      for (const k of ["position", "normal", "uv", "aSplat", "color"]) g.getAttribute(k).needsUpdate = true;
      held.lod = lod;
      held.ref = ref;
    }
    // THE BOUNDING SPHERE IS THE WHOLE POINT, so it is set from the chunk's own heights rather
    // than computed from the buffer: `computeBoundingSphere` would walk every vertex on every
    // stroke, and the skirt hanging below would make each sphere taller than the ground it is
    // around. The engine already measured the region; the skirt is added to it.
    held.geo.boundingSphere = new T.Sphere(
      new T.Vector3(ref.centre[0], ref.centre[1] - skirt / 2, ref.centre[2]), ref.radius + skirt);
    held.geo.boundingBox = new T.Box3(
      new T.Vector3(ref.min[0], ref.min[1] - skirt, ref.min[2]),
      new T.Vector3(ref.max[0], ref.max[1], ref.max[2]));
    return true;
  }

  /**
   * A frame went by. Nothing here runs unless the camera has moved far enough to change an answer.
   *
   * Re-picking the detail of 256 chunks every frame is 256 distance tests and, worse, a rebuild
   * of whatever changed IN that frame — which is the hitch this is meant to remove. So the eye is
   * remembered, the whole pass is skipped until it has moved half a chunk radius, and the chunks
   * whose level actually changed are rebuilt a few per frame.
   */
  frame(eye: { x: number; y: number; z: number }) {
    if (!this.chunked) return;
    if (this.lodQueue.length) {
      for (let n = 0; n < LOD_PER_FRAME && this.lodQueue.length; n++) {
        const ref = this.lodQueue.shift()!;
        const held = this.chunks.get(keyOf(ref));
        if (!held) continue;
        const want = this.lodFor(ref);
        // A chunk that threw mid-flight has just taken every other chunk out of the scene with
        // it, and the whole-field mesh it fell back to is empty. Build it.
        if (want !== held.lod && !this.putChunk(ref, want)) { this.rebuild(); return; }
      }
      return;
    }
    const at: [number, number, number] = [eye.x, eye.y, eye.z];
    if (this.lodEye) {
      const d = Math.hypot(at[0] - this.lodEye[0], at[1] - this.lodEye[1], at[2] - this.lodEye[2]);
      if (d < this.lodStep) return;
    }
    this.lodEye = at;
    const queue: ChunkRef[] = [];
    for (const [, c] of this.chunks) if (this.lodFor(c.ref) !== c.lod) queue.push(c.ref);
    this.lodQueue = queue;
  }

  /** What is actually being drawn, for the panel: chunks in the tree, and the triangles in them. */
  chunkStats(): { chunks: number; triangles: number; lods: number[]; note?: string } | null {
    if (!this.chunked || !this.chunks.size) return this.chunkNote ? { chunks: 0, triangles: 0, lods: [], note: this.chunkNote } : null;
    let triangles = 0;
    const lods = new Set<number>();
    for (const [, c] of this.chunks) {
      triangles += (c.geo.getIndex()?.count || 0) / 3;
      lods.add(c.lod);
    }
    return { chunks: this.chunks.size, triangles: Math.round(triangles), lods: [...lods].sort((a, b) => a - b),
             note: this.chunkNote || undefined };
  }

  // ---- the layers ----------------------------------------------------------------------

  /** Re-read the palette: colours, tiling and textures. Cheap enough to call on every keystroke
   *  in the name field, which is what the panel does. */
  readLayers(rebuildColours = false) {
    const T = this.T;
    const AXIS = ["x", "y", "z", "w"];
    for (let i = 0; i < 4; i++) {
      const l = this.data.layers[i];
      const c = new T.Color(l?.colour || "#808080");
      this.uni["uCol" + i].value.copy(c);
      (this.uni.uTile.value as any)[AXIS[i]] = l?.tiling && l.tiling > 0 ? l.tiling : 8;
      const path = l?.texture || "";
      if (!path) {
        this.uni["uTex" + i].value = this.blank;
        (this.uni.uHas.value as any)[AXIS[i]] = 0;
        continue;
      }
      let tex = this.texCache.get(path);
      if (!tex) {
        const url = this.env.textureUrl ? this.env.textureUrl(path) : path;
        tex = new T.TextureLoader().load(url);
        tex.wrapS = T.RepeatWrapping;
        tex.wrapT = T.RepeatWrapping;
        if (T.SRGBColorSpace) tex.colorSpace = T.SRGBColorSpace;
        this.texCache.set(path, tex);
      }
      this.uni["uTex" + i].value = tex;
      (this.uni.uHas.value as any)[AXIS[i]] = 1;
    }
    // A recoloured layer changes every vertex the engine already blended, and the engine is the
    // one holding the blend. Cheaper to ask it again than to keep a second copy of the maths that
    // could disagree with it: 20 ms on 513², and only when somebody moves a colour picker.
    if (rebuildColours && this.geo?.getAttribute("color")) this.rebuild();
  }

  private makeMaterial(): any {
    const T = this.T;
    this.uni = {
      uCol0: { value: new T.Color(0xffffff) }, uCol1: { value: new T.Color(0xffffff) },
      uCol2: { value: new T.Color(0xffffff) }, uCol3: { value: new T.Color(0xffffff) },
      uTex0: { value: this.blank }, uTex1: { value: this.blank },
      uTex2: { value: this.blank }, uTex3: { value: this.blank },
      uHas: { value: new T.Vector4(0, 0, 0, 0) },
      uTile: { value: new T.Vector4(8, 8, 8, 8) },
    };
    // vertexColors, and the vertex colour is the ENGINE'S OWN blend of the four layers. That way
    // the viewport shows exactly what `terrainMesh` says the ground is — the same array a GLB
    // export, the PlayCanvas mirror and the emitted builder all carry — and the shader below is
    // left with one job, textures, instead of being a second opinion about colour.
    //
    // envMapIntensity is dropped to a third. The studio environment is a bright sky probe aimed
    // at a subject that fills the frame; a 256-unit ground plane facing straight up caught all of
    // it and rendered a mid olive as near-white.
    const m = new T.MeshStandardMaterial({
      color: 0xffffff, roughness: 0.96, metalness: 0, vertexColors: true, envMapIntensity: 0.35,
    });
    const U = this.uni;
    m.onBeforeCompile = (sh: any) => {
      for (const k of Object.keys(U)) sh.uniforms[k] = U[k];
      sh.vertexShader = "attribute vec4 aSplat;\nvarying vec4 vSplat;\nvarying vec2 vGroundXZ;\n" + sh.vertexShader;
      sh.vertexShader = sh.vertexShader.replace("#include <begin_vertex>",
        "#include <begin_vertex>\n  vSplat = aSplat;\n  vGroundXZ = (modelMatrix * vec4(transformed, 1.0)).xz;");
      sh.fragmentShader =
        "uniform vec3 uCol0;\nuniform vec3 uCol1;\nuniform vec3 uCol2;\nuniform vec3 uCol3;\n"
        + "uniform sampler2D uTex0;\nuniform sampler2D uTex1;\nuniform sampler2D uTex2;\nuniform sampler2D uTex3;\n"
        + "uniform vec4 uHas;\nuniform vec4 uTile;\nvarying vec4 vSplat;\nvarying vec2 vGroundXZ;\n"
        + sh.fragmentShader;
      // The layer colours are already in diffuseColor by way of the vertex colour, so the
      // textures only have to modulate it. A texture that averages mid grey, doubled, leaves the
      // colour where it was — switching one on adds grain without also changing how light the
      // ground is — and a layer with no texture contributes a flat 1.0, so a field with no
      // textures at all comes out byte for byte what `terrainMesh` said it was.
      sh.fragmentShader = sh.fragmentShader.replace("#include <color_fragment>", [
        "#include <color_fragment>",
        "  if (uHas.x + uHas.y + uHas.z + uHas.w > 0.5) {",
        "    float wsum = max(1e-4, vSplat.r + vSplat.g + vSplat.b + vSplat.a);",
        "    vec4 sw = vSplat / wsum;",
        "    vec3 grain = vec3(0.0);",
        "    grain += (uHas.x > 0.5 ? texture2D(uTex0, vGroundXZ / max(0.001, uTile.x)).rgb * 2.0 : vec3(1.0)) * sw.r;",
        "    grain += (uHas.y > 0.5 ? texture2D(uTex1, vGroundXZ / max(0.001, uTile.y)).rgb * 2.0 : vec3(1.0)) * sw.g;",
        "    grain += (uHas.z > 0.5 ? texture2D(uTex2, vGroundXZ / max(0.001, uTile.z)).rgb * 2.0 : vec3(1.0)) * sw.b;",
        "    grain += (uHas.w > 0.5 ? texture2D(uTex3, vGroundXZ / max(0.001, uTile.w)).rgb * 2.0 : vec3(1.0)) * sw.a;",
        "    diffuseColor.rgb *= grain;",
        "  }",
      ].join("\n"));
    };
    // WITHOUT THIS, EVERY OTHER STANDARD MATERIAL IN THE SCENE TURNS INTO GROUND. three keys its
    // compiled programs by the material's own parameters, and onBeforeCompile is not one of them,
    // so the first standard material compiled hands its program to all the rest.
    m.customProgramCacheKey = () => "studio-terrain-splat";
    return m;
  }

  // ---- the cursor ----------------------------------------------------------------------

  /**
   * The ring, sized in world units and sitting on the surface.
   *
   * Two of them. The outer one is the radius; the inner one is where the falloff has taken half
   * the strength away. One ring says how WIDE the brush is; two say how SOFT it is, which is the
   * question a person actually has when a hill comes out looking like a thumbprint.
   */
  setCursor(at: { x: number; z: number } | null, radius = 1, falloff = 0.5) {
    if (!at) { this.ring.visible = false; this.inner.visible = false; return; }
    this.ring.visible = true;
    this.inner.visible = true;
    // Lifted by a 250th of the radius: enough to clear the surface it is measured against, small
    // enough that it still reads as lying ON the ground rather than floating over it.
    const lift = radius * 0.004 + 0.005;
    this.layRing(this.ring, at, radius, lift);
    this.layRing(this.inner, at, radius * Math.max(0.05, 1 - falloff * 0.5), lift);
  }

  private layRing(line: any, at: { x: number; z: number }, radius: number, lift: number) {
    const attr = line.geometry.getAttribute("position");
    const arr = attr.array as Float32Array;
    for (let i = 0; i < RING_SEGS; i++) {
      const a = (i / RING_SEGS) * Math.PI * 2;
      const x = at.x + Math.cos(a) * radius, z = at.z + Math.sin(a) * radius;
      arr[i * 3] = x;
      arr[i * 3 + 1] = this.groundY(x, z) + lift;
      arr[i * 3 + 2] = z;
    }
    attr.needsUpdate = true;
    line.geometry.computeBoundingSphere();
  }

  private groundY(x: number, z: number): number {
    try { return heightAt(this.data, x, z); } catch { return this.data.spec.origin[1]; }
  }

  // ---- the ray -------------------------------------------------------------------------

  /** Where a ray meets the ground. See the note above the class for why this is not a raycast. */
  hit(ray: { origin: any; direction: any }): { x: number; z: number } | null {
    try { return this.march(ray); } catch { return null; }
  }

  private march(ray: { origin: any; direction: any }): { x: number; z: number } | null {
    const s = this.data.spec;
    const O = [ray.origin.x, ray.origin.y, ray.origin.z];
    const D = [ray.direction.x, ray.direction.y, ray.direction.z];
    const lo = [s.origin[0], s.origin[1] - 1e-3, s.origin[2]];
    const hi = [s.origin[0] + s.size, s.origin[1] + s.maxHeight + 1e-3, s.origin[2] + s.size];
    let t0 = 0, t1 = 1e9;
    for (let i = 0; i < 3; i++) {
      if (Math.abs(D[i]) < 1e-9) { if (O[i] < lo[i] || O[i] > hi[i]) return null; continue; }
      let a = (lo[i] - O[i]) / D[i], b = (hi[i] - O[i]) / D[i];
      if (a > b) { const t = a; a = b; b = t; }
      if (a > t0) t0 = a;
      if (b < t1) t1 = b;
    }
    if (t1 < t0) return null;

    const cell = s.size / Math.max(1, s.res - 1);
    const dxz = Math.hypot(D[0], D[2]);
    const step = dxz > 1e-6 ? cell / dxz : Math.max(1e-4, (t1 - t0) / 256);
    const at = (t: number) => ({ x: O[0] + D[0] * t, y: O[1] + D[1] * t, z: O[2] + D[2] * t });

    let p = at(t0);
    if (p.y <= this.groundY(p.x, p.z)) return { x: p.x, z: p.z };    // the camera is underground
    let prev = t0;
    for (let n = 0, t = t0 + step; n < 4096 && t <= t1; n++, t += step) {
      p = at(t);
      if (p.y <= this.groundY(p.x, p.z)) {
        // Fourteen halvings of one cell is well under a millimetre on any field this editor opens.
        let a = prev, b = t;
        for (let k = 0; k < 14; k++) {
          const mid = (a + b) / 2;
          const q = at(mid);
          if (q.y <= this.groundY(q.x, q.z)) b = mid; else a = mid;
        }
        const q = at(b);
        return { x: q.x, z: q.z };
      }
      prev = t;
    }
    return null;
  }

  // ---- the scatter -----------------------------------------------------------------------

  /**
   * The things standing on the ground, kept in step with the field's own list.
   *
   * Keyed by the ScatterItem OBJECT and not by its index: a stroke splices the middle of that
   * array, and every index after the splice would then point at the wrong tree.
   */
  syncScatter() {
    if (this.instanced && this.syncInstanced()) { this.clearClones(); return; }
    this.clearBatches();
    const want = this.data.scatter || [];
    const seen = new Set<ScatterItem>();
    let drawn = 0, hidden = 0;
    for (const it of want) {
      if (drawn >= SCATTER_DRAW_MAX) { hidden++; continue; }
      let o = this.placed.get(it);
      if (!o) {
        const proto = this.env.prototype(it.asset);
        // Still importing. The editor calls again when it lands, so this is a frame of absence
        // rather than a tree that never appears.
        if (!proto) { hidden++; continue; }
        o = proto.clone(true);
        o.userData.scatter = true;
        this.scatterGroup.add(o);
        this.placed.set(it, o);
      }
      seen.add(it);
      o.position.set(it.at[0], it.at[1], it.at[2]);
      o.rotation.set(0, it.rot || 0, 0);
      o.scale.setScalar(it.scale || 1);
      drawn++;
    }
    for (const [it, o] of [...this.placed]) {
      if (seen.has(it)) continue;
      this.scatterGroup.remove(o);
      disposeTree(o);
      this.placed.delete(it);
    }
    this.drawn = drawn;
    this.hidden = hidden;
  }

  /** Throw away the clones of one asset, so the next sync makes them again — for the moment a
   *  prototype finishes importing and the items that had none can finally be drawn. */
  dropScatterFor(asset: string) {
    for (const [it, o] of [...this.placed]) {
      if (it.asset !== asset) continue;
      this.scatterGroup.remove(o);
      disposeTree(o);
      this.placed.delete(it);
    }
    this.dropBatch(asset);
  }

  // ---- the scatter, as instances -----------------------------------------------------------
  //
  // A CLONE IS A SUB-TREE; AN INSTANCE IS A MATRIX. `proto.clone(true)` of a game's own tree is a
  // Group with meshes under it, and three walks every node of every one of them once a frame to
  // update its world matrix. Three thousand oaks with four parts each is twelve thousand nodes,
  // and that — not the triangles — is what put the preview at 12 fps and forced SCATTER_DRAW_MAX.
  //
  // `scatterBatches` hands back, per asset, the instance matrices as one Float32Array already in
  // the column-major order `InstancedMesh.instanceMatrix` wants, and an Int32Array saying where
  // each instance came from in `t.scatter`. So the common case — a prototype that is one mesh at
  // the origin — is a memcpy into the instance buffer and one draw call for the whole forest.
  // The index array is what keeps a click meaningful: see `pickScatter`.

  private syncInstanced(): boolean {
    let batches: ScatterBatch[];
    try { batches = scatterBatches(this.data); } catch { this.instanced = false; return false; }
    if (!Array.isArray(batches)) { this.instanced = false; return false; }
    const seen = new Set<string>();
    let drawn = 0, hidden = 0;
    for (const b of batches) {
      if (!b || b.count <= 0) continue;
      const proto = this.env.prototype(b.asset);
      // Still importing. The editor calls again when it lands, so this is a frame of absence
      // rather than a forest that never appears.
      if (!proto) { hidden += b.count; continue; }
      const take = Math.min(b.count, Math.max(0, SCATTER_INSTANCE_MAX - drawn));
      if (take < b.count) hidden += b.count - take;
      if (take <= 0) continue;
      if (!this.putBatch(b, proto, take)) { this.instanced = false; this.clearBatches(); return false; }
      seen.add(b.asset);
      drawn += take;
    }
    for (const [asset] of [...this.batches]) if (!seen.has(asset)) this.dropBatch(asset);
    this.drawn = drawn;
    this.hidden = hidden;
    return true;
  }

  private putBatch(b: ScatterBatch, proto: any, take: number): boolean {
    const T = this.T;
    const leaves = leavesOf(T, proto);
    // A prototype an instance cannot carry: a skinned mesh has a skeleton per copy, and an
    // InstancedMesh of one draws every tree in the same pose or in none. Refusing the whole
    // batch hands the scatter back to the clone path, which is slower and correct.
    if (!leaves) return false;
    if (!leaves.length) return true;                 // a prototype with no geometry draws nothing
    let batch = this.batches.get(b.asset);
    // A capacity that never shrinks and grows in steps: a held scatter brush adds items every
    // frame, and rebuilding the buffers on each one is the cost this was meant to remove.
    if (batch && (batch.meshes.length !== leaves.length || batch.capacity < take)) {
      this.dropBatch(b.asset);
      batch = undefined;
    }
    if (!batch) {
      const capacity = Math.max(64, Math.ceil(take * 1.5));
      const meshes: any[] = [];
      for (const leaf of leaves) {
        const im = new T.InstancedMesh(leaf.geometry, leaf.material, capacity);
        im.name = "scatter." + b.asset;
        im.castShadow = leaf.castShadow;
        im.receiveShadow = true;
        im.userData.scatter = true;
        im.userData.scatterAsset = b.asset;
        // three computes an InstancedMesh's own sphere from the matrices. An engine old enough
        // not to have the field would cull the whole forest by ONE instance's geometry, so it
        // is safer there to draw it always.
        if (!("boundingSphere" in im)) im.frustumCulled = false;
        this.instGroup.add(im);
        meshes.push(im);
      }
      batch = { asset: b.asset, index: b.index, count: 0, capacity, meshes, locals: leaves.map((l) => l.local) };
      this.batches.set(b.asset, batch);
    }
    batch.index = b.index;
    batch.count = take;
    for (let k = 0; k < batch.meshes.length; k++) {
      const im = batch.meshes[k];
      const local = batch.locals[k];
      const arr = im.instanceMatrix.array as Float32Array;
      if (!local) {
        // The leaf sits at the prototype's own origin, which is nearly every asset: the batch's
        // matrices ARE the instance matrices, and this is one copy of take*16 floats.
        arr.set(b.matrices.subarray(0, take * 16));
      } else {
        for (let i = 0; i < take; i++) {
          this.tmpM.fromArray(b.matrices, i * 16);
          this.tmpM2.multiplyMatrices(this.tmpM, local);
          this.tmpM2.toArray(arr, i * 16);
        }
      }
      im.count = take;
      im.instanceMatrix.needsUpdate = true;
      im.boundingSphere = null;
      im.boundingBox = null;
    }
    return true;
  }

  private dropBatch(asset: string) {
    const batch = this.batches.get(asset);
    if (!batch) return;
    for (const im of batch.meshes) {
      this.instGroup.remove(im);
      try { im.dispose(); } catch { /* gone */ }
    }
    this.batches.delete(asset);
  }

  private clearBatches() {
    for (const [asset] of [...this.batches]) this.dropBatch(asset);
  }

  /** Take the clones out, and do NOT dispose them: `proto.clone(true)` shares the prototype's
   *  geometry and material by reference, and the instanced meshes about to be drawn are holding
   *  the very same two objects. Disposing here frees the buffer under them. */
  private clearClones() {
    if (!this.placed.size) return;
    for (const [, o] of this.placed) this.scatterGroup.remove(o);
    this.placed.clear();
  }

  /**
   * Which scattered item is under this ray.
   *
   * The instanced path is the one that had to be proved: a click lands on instance 7 of a single
   * InstancedMesh holding four thousand oaks, and `ScatterBatch.index` is the only thing that
   * knows which of `t.scatter` that is. Without it a forest is one object and every tree in it
   * has the same name.
   */
  pickScatter(ray: any): { index: number; item: ScatterItem } | null {
    const list = this.data.scatter || [];
    if (this.instanced && this.batches.size) {
      const meshes: any[] = [];
      for (const [, b] of this.batches) for (const im of b.meshes) meshes.push(im);
      const hits = ray.intersectObjects(meshes, false);
      for (const h of hits) {
        const asset = h.object?.userData?.scatterAsset;
        const batch = asset ? this.batches.get(asset) : null;
        if (!batch || typeof h.instanceId !== "number") continue;
        const idx = batch.index?.[h.instanceId];
        if (typeof idx !== "number" || idx < 0 || idx >= list.length) continue;
        return { index: idx, item: list[idx] };
      }
      return null;
    }
    const hits = ray.intersectObjects([this.scatterGroup], true);
    for (const h of hits) {
      let o = h.object;
      while (o && !this.placedItemOf(o)) o = o.parent;
      const it = o ? this.placedItemOf(o) : null;
      if (!it) continue;
      const idx = list.indexOf(it);
      if (idx >= 0) return { index: idx, item: it };
    }
    return null;
  }

  private placedItemOf(o: any): ScatterItem | null {
    for (const [it, node] of this.placed) if (node === o) return it;
    return null;
  }

  dispose() {
    this.clearChunks();
    this.clearBatches();
    for (const [, o] of this.placed) disposeTree(o);
    this.placed.clear();
    for (const [, t] of this.texCache) { try { t.dispose?.(); } catch { /* gone */ } }
    this.texCache.clear();
    try { this.blank?.dispose?.(); } catch { /* gone */ }
    try { this.ring.geometry.dispose(); this.ring.material.dispose(); } catch { /* gone */ }
    try { this.inner.geometry.dispose(); this.inner.material.dispose(); } catch { /* gone */ }
    this.group.parent?.remove(this.group);
    try { this.geo.dispose(); } catch { /* gone */ }
    try { this.mat.dispose(); } catch { /* gone */ }
  }
}

/**
 * The raw layer weights, RGBA per vertex.
 *
 * `MeshArrays.colors` is NOT the weights, whatever the comment on it used to say: terrain.ts
 * blends the four layer colours into it and hands the weights back separately. Feeding a COLOUR
 * to a shader that reads it as four weights is not a subtle error — the alpha channel is a
 * constant 1.0, so every vertex came out 63% layer 3, and a field of olive ground rendered
 * sand-cream from edge to edge.
 *
 * An engine that does not hand back `weights` gets a field that is all layer 0, which is what a
 * fresh one is; the vertex colour still carries the real blend, so nothing looks wrong, only the
 * per-layer TEXTURES stop being per-layer.
 */
function weightsOf(m: any, n: number): Float32Array {
  const w = m?.weights;
  if (w instanceof Float32Array && w.length >= n * 4) return w;
  const out = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) out[i * 4] = 1;
  return out;
}

/** RGB out of an RGBA array, for the `color` attribute three wants at three floats a vertex. */
function rgbOf(rgba: Float32Array, out: Float32Array, n: number, dst: number) {
  for (let i = 0; i < n; i++) {
    const o = (dst + i) * 3;
    out[o] = rgba[i * 4];
    out[o + 1] = rgba[i * 4 + 1];
    out[o + 2] = rgba[i * 4 + 2];
  }
}

/** A chunk's place in the grid, as a Map key. */
function keyOf(ref: { cx: number; cz: number }): string { return ref.cx + ":" + ref.cz; }

/**
 * Every drawable mesh of a prototype, with its transform relative to the prototype's own root.
 *
 * The root's own position, rotation and scale are DROPPED, exactly as the clone path drops them:
 * `syncScatter` writes the item's own transform straight onto the clone's root, so whatever the
 * builder left there was never used. A leaf at the root with nothing between it and the root gets
 * `local: null`, which is the flag that lets a batch be one memcpy instead of N multiplies — and
 * that is most assets, because most builders put the mesh at the origin and move the group.
 */
function leavesOf(T: any, proto: any): Array<{ geometry: any; material: any; local: any | null; castShadow: boolean }> | null {
  const out: Array<{ geometry: any; material: any; local: any | null; castShadow: boolean }> = [];
  try { proto.updateMatrixWorld(true); } catch { /* a bare mesh with no parent */ }
  const inv = new T.Matrix4();
  try { inv.copy(proto.matrixWorld).invert(); } catch { inv.identity(); }
  let refuse = false;
  proto.traverse((o: any) => {
    // A skeleton belongs to one mesh, and an InstancedMesh has one geometry: every copy would
    // share a pose. An InstancedMesh inside a prototype cannot be instanced again either.
    if (o.isSkinnedMesh || o.isInstancedMesh) { refuse = true; return; }
    if (!o.isMesh || !o.geometry || !o.material) return;
    if (o.visible === false) return;
    const m = new T.Matrix4().multiplyMatrices(inv, o.matrixWorld);
    const identity = m.elements.every((v: number, i: number) => Math.abs(v - IDENTITY16[i]) < 1e-9);
    out.push({ geometry: o.geometry, material: o.material, local: identity ? null : m, castShadow: !!o.castShadow });
  });
  return refuse ? null : out;
}

const IDENTITY16 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

function ringGeometry(T: any): any {
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.BufferAttribute(new Float32Array(RING_SEGS * 3), 3));
  return g;
}

/** A 1x1 white pixel, so a layer with no texture still has something to bind. A sampler2D left
 *  null renders black on some drivers and warns on every frame on the rest. */
function whiteTexture(T: any): any {
  const t = new T.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
  t.needsUpdate = true;
  return t;
}

/** Upload a slice of an attribute rather than the whole buffer. three renamed this between r152
 *  and r160 and the old field is gone by r183, so both spellings are tried: the editor opens
 *  against whichever three the project it opened is built on, not against ours. */
function mark(attr: any, offset: number, count: number) {
  try {
    if (typeof attr.clearUpdateRanges === "function" && typeof attr.addUpdateRange === "function") {
      attr.clearUpdateRanges();
      attr.addUpdateRange(offset, count);
    } else if (attr.updateRange) {
      attr.updateRange.offset = offset;
      attr.updateRange.count = count;
    }
  } catch { /* a whole-buffer upload is slower, never wrong */ }
  attr.needsUpdate = true;
}

// ------------------------------------------------------------------ helpers
const r5 = (n: number) => Math.round(n * 1e5) / 1e5;
const round3 = (n: number) => Math.round(n * 1000) / 1000;

function transparentFaces(T: any): any {
  const m = facesMaterial(T);
  m.transparent = true;
  m.depthWrite = false;
  m.opacity = 0.55;
  return m;
}

// In a game page `import('/src/parts.js')` meant that game's dev server. Inside a blob module it
// means the origin of the Studio, which has no such file, so when the dev server of the project
// is known the root- and dot-relative specifiers are pointed back at it.
function retargetImports(code: string, devUrl: string): string {
  const base = devUrl.replace(/\/+$/, "");
  return code.replace(/import\(\s*(['"`])(?:\.\/|\/(?!\/))/g, (_m, q: string) => `import(${q}${base}/`);
}

/** The forge's environment: sky over ground with one warm blob, so a metal has something to
 *  reflect. It lights the scene without ever being drawn. */
function envCanvas(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createLinearGradient(0, 0, 0, 128);
  grad.addColorStop(0, "#dce8ff");
  grad.addColorStop(0.5, "#8fa2bd");
  grad.addColorStop(1, "#2a2a30");
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 128);
  const blob = g.createRadialGradient(70, 40, 2, 70, 40, 44);
  blob.addColorStop(0, "rgba(255,240,210,1)");
  blob.addColorStop(1, "rgba(255,240,210,0)");
  g.fillStyle = blob;
  g.fillRect(0, 0, 256, 128);
  return c;
}

export { VIEWS, FOV };
export type { Axis, DragDelta, GizmoMode };
