// The editor: a Blender-shaped window over a procedural asset.
//
// The thing to be clear about, because it decides the whole design: for a procedural asset the
// editable object is NOT the vertices. Drag a vertex and the next run of the code overwrites it.
// Blender's own answer to that problem is Geometry Nodes, which is parametric rather than
// push-and-pull — and for an asset that IS code, the parametric editor is the stronger tool,
// because a slider changes the source rather than a copy of the output.
//
// So three things are editable here, in order of how well they round-trip:
//
//   PARAMETERS  A top-level constant in the file becomes a slider, and moving it rewrites that
//               one number in the source. Exact, reviewable in a diff, no new file format.
//   PARTS       Move, rotate, scale or hide a named part. Kept in a sidecar and re-applied BY
//               NAME after every rebuild, so a parameter change does not throw the edit away.
//   RIG & POSE  An armature, a pose, and keyframes — data, applied the same way.
//
// Vertex editing is deliberately absent. It would fight the medium and lose.

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { X, Image as ImageIcon, Boxes, BoxSelect, Trash2, CopyPlus, Plus,
  AlertTriangle, ArrowDownToLine, Box, Bone as BoneIcon, CircleHelp, Diamond, Eye, EyeOff, FileCode, Focus, Grid3x3, Layers, Link2, Loader2,
  Map as MapIcon, Maximize2, Mountain, Move, MousePointer2, PanelRight, PersonStanding, Play, Radio, Redo2, RefreshCw, RotateCw, Ruler, Save, Scaling,
  Shapes, Sliders, Sparkles, Sprout, Sun, Undo2, Video, FlipHorizontal, Paintbrush,
} from "lucide-react";
import { api } from "../../../api/client";
import { cls, useSticky as useLocal } from "../../ui";
import { buildStudio, loadEngine, runGeneration, type EngineKind, type Studio } from "../studio";
import { placedIdOf, PRIMITIVES, pcSnapshot, threeToPcPlace } from "./ops";
import { dressForGame } from "./gamedress";
import { type PlacedRef, type PlacedItem,
  applyParams, countEdits, emptyEdits, insertKey, keyFrames, loaderThreeRef, normaliseEdits, removeKeysAt,
  scanCallParams, scanParams, sidecarFor, type Clip, type Edits, type Mod, type ParamSpec, type ParamValue,
  type PartOverride, type V3, type WorldOverride,
} from "./kit";
import { dirOf, joinPath, loadFileAsset, mergeSpecs, type FileAsset, type PcEngine } from "./load";
import { boneFromObject, uniqueBoneName } from "./rig";
import { type ElemKind } from "./vertedit";
import { DabPlacer, TOOL_DEFAULTS, clamp, paintDocPath, paintKey, parsePaintDoc, pressureOf, pushRecent,
  type BrushSettings, type Dab, type PaintKeyAction, type PaintPreset, type PaintTool, type Projection } from "./paintCore";
import type { ScreenDab, StrokeOptions } from "./paintGpu";
import { PaintBrush, PaintColour, PaintKeys, PaintLayers, PaintStroke, PaintTexture, PaintTools, TOOL_INFO, toolIcon,
  type LayerActions, type PaintInfo } from "./PaintPanel";
import { DEFAULT_OVERLAYS, EditWorld, type Mode, type Overlays, type PartInfo, type Shading, type SolidColor, type SolidLight, type Stats, type TerrainViewEnv } from "./world";
import { deserialize as terrainRead, emitBuilder, makeTerrain, report as terrainReport, serialize as terrainWrite,
  type Brush, type TerrainData, type TerrainLayer, type TerrainReport } from "./terrain";
import { CHUNK_SAMPLES, TerrainTool, builderName, emitRes, ensureLayers, packTerrain, pickEmitEngine,
  scatterAssetOf, terrainBuilderPath, terrainSidecarPath, unpackTerrain, usedPalette, wantChunks,
  type LiveTab, type ScatterAsset } from "./terrainTool";
import { ENGINE_DEFAULTS, worldOptions, type EnginePrefs } from "../prefs";
import type { Axis, GizmoMode } from "./gizmo";
import {
  BakePanel, CameraPanel, Check, Choice, keymapFor, LightPanel, NavGizmo, NumField, Outliner, Popover, RigPanel, Section,
  MeshPanel, StatsBar, TerrainBrushes, TerrainEmit, TerrainLayers, TerrainScatter, TerrainStart, TerrainStats,
  Timeline, Toolbar, Vec3Field, WorldPanel, type BakeKind, type Tool,
} from "./panels";
import type { Defects } from "./ops";
// The configurator (configurator.ts is the contract): presets, handles, the view / day-night bar.
import { NIGHT_PARAM, type ModularManifest, type ParamHandleCallbacks, type PresetSpec } from "./configurator";
import { handlesFromManifest } from "./handles";
import { matchPreset, presetBuildValues, presetChanges, presetEntries, presetsFromManifest, withParams } from "./presets";
import { declaredHandleCount, hasNightParam, isConfigurator, leavesFrame, panelSpecs } from "./configModel";
import { ConfigBar, ConfigPanel, SideTabs } from "./ConfigPanel";
import { applyStudioLook, captureStudio, type StudioLook, type StudioRefs } from "./configLook";
import { hashString, liveResources, releaseThumb, shootThumb, usePresetThumbs } from "./presetThumbs";
import { makeSignal, samePartsView, statsShape, type Signal } from "./viewSync";

export interface EditorSource {
  /** A file can be written back to; a recorded run cannot; a live game is mirrored through the
   *  live link and its edits are pushed back into it and saved as a sidecar at the project root. */
  /** `asset` is one row of the game's own asset index — a spec in a table, a builder function.
   *  It is a snippet that CALLS the game's code, not the game's code, so it runs the way a
   *  recorded run does and saves a sidecar of its own beside the file it came from. */
  kind: "file" | "generation" | "live" | "asset";
  label: string;
  code: string;
  path: string;
  project: string;
  devUrl: string;
  moduleUrls: string[];
  engine: string;
  /** The asset index row this came from, for `asset` sources: what the sidecar is named after. */
  assetKey?: string;
  /** Which GAME inside the workspace this came from — `rot-rush`. Its own server is the only one
   *  that can serve its files, and a workspace here holds three. */
  assetRoot?: string;
  /** Every place the game's own code can be fetched from, best first. */
  bases?: string[];
}

interface Props {
  source: EditorSource | null;
  notice?: string;
  /** The Studio engine settings: how the editor opens, and the viewport's behaviour. */
  prefs?: EnginePrefs;
  /** The angle a forge call rendered from, e.g. "az=90,el=-5". When set, the viewport opens on
   *  that angle instead of its own default, so a person watching an agent sees what it saw. */
  aim?: string;
}

type Phase = "idle" | "engine" | "build" | "ready" | "error";
const BACKDROP = "#1a1e26";

/** Each paint tool's brush as it was last left, over the defaults: a soft eraser stays soft. */
function loadBrushes(): Record<PaintTool, BrushSettings> {
  const out = { ...TOOL_DEFAULTS } as Record<PaintTool, BrushSettings>;
  try {
    const raw = JSON.parse(localStorage.getItem("paint.brushes") || "{}");
    for (const k of Object.keys(out) as PaintTool[]) if (raw && raw[k]) out[k] = { ...out[k], ...raw[k] };
  } catch { /* first visit, or private mode */ }
  return out;
}
const toScreen = (d: Dab): ScreenDab => ({ x: d.x, y: d.y, size: d.size, alpha: d.alpha, angle: d.angle, seed: d.seed });
const UNDO_MAX = 60;

export default function Editor({ source, notice, prefs, aim }: Props) {
  const P = prefs || ENGINE_DEFAULTS;
  // Held in a ref as well as a prop: the scene loads asynchronously and frames itself when it
  // lands, so the angle has to be re-applied at that moment, not only when the prop changes.
  const aimRef = useRef<string>("");
  // The resolver is defined further down and the build path runs above it; a ref keeps the
  // two in the same order they execute rather than the order they are written.
  const resolveRefRef = useRef<(ref: any) => Promise<any>>(async () => null);
  aimRef.current = aim || "";
  // Callbacks made once (undo, bakes) read the settings through this, so they see today's values.
  const prefsRef = useRef(P);
  prefsRef.current = P;
  const host = useRef<HTMLDivElement>(null);
  const canvasBox = useRef<HTMLDivElement>(null);
  const world = useRef<EditWorld | null>(null);

  // The agent moved its camera; move ours. Only when a scene is already up — a fresh load frames
  // itself and applies the same angle on the way in, so this is the case where the person is
  // watching one asset and the agent renders it again from somewhere else.
  useEffect(() => {
    if (aim && world.current) world.current.aimSpec(aim);
  }, [aim]);
  const raf = useRef(0);
  const rebuildTimer = useRef(0);
  /** The engine URL the file was imported against, kept so a changed constant can be imported again. */
  const engineUrl = useRef("");
  /** Which text-level values the current module was imported with; see `rebuild`. */
  const importedSig = useRef("");

  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  // The first exception the frame loop caught. Kept apart from `error`, because the asset DID
  // build — the outliner and the numbers are true — and only the picture is missing.
  const [renderError, setRenderError] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [parts, setParts] = useState<PartInfo[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [selection, setSelection] = useState<string[]>([]);
  const [active, setActive] = useState("");
  const [activeBoneTick, force] = useState(0);
  const bump = useCallback(() => force((n) => n + 1), []);
  // What follows the camera, the frame rate, the transform readout and the box-select rectangle
  // each re-render alone through a signal, at most once a frame. The editor itself no longer
  // re-renders when the view moves: that was a whole-editor render per mouse move (viewSync.ts).
  const navSig = useMemo(() => makeSignal(), []);
  const statsSig = useMemo(() => makeSignal(), []);
  const liveStats = useRef<{ fps: number; drawCalls: number; drawn: boolean; error: string } | null>(null);

  const [shading, setShading] = useState<Shading>(P.shading);
  const [solidLight, setSolidLight] = useState<SolidLight>(P.solid_light);
  const [solidColor, setSolidColor] = useState<SolidColor>(P.solid_color);
  const [sceneLights, setSceneLights] = useState(true);
  const [sceneWorld, setSceneWorld] = useState(true);
  const [overlays, setOverlays] = useState<Overlays>({
    ...DEFAULT_OVERLAYS, grid: P.grid, axes: P.axes, outline: P.outline, bones: P.bones, extras: P.extras,
  });
  // THE SELECTION OUTLINE: one switch in three places — the toolbar, the Overlays menu and the
  // Settings page — and remembered. Someone who turns it off does not want it back at the next
  // asset, so this writes the Settings page's own "Selection outline" preference. Only the
  // outline: it hides a line, never the selection, and the gizmo stays where it is.
  const setOutline = useCallback((v: boolean) => {
    setOverlays((o) => ({ ...o, outline: v }));
    api.updateSettings({ engine: { outline: v } }).catch(() => { /* this session keeps the choice */ });
  }, []);
  // ONE DESELECT for the places that offer it — the header chip and the empty outliner, beside
  // Esc, Alt+A and a click on empty space. It drops what can be SEEN selected: the corners too,
  // in edit mode, where they are the selection.
  const deselectEverything = () => {
    const w = world.current;
    if (!w) return;
    if (mode === "edit") w.selectElems([], false);
    w.deselectAll();
    bump();
  };
  const [mode, setMode] = useState<Mode>("object");
  // ---- paint mode (paintCore.ts, paintGpu.ts, PaintPanel.tsx) ---------------------------
  const [paintTool, setPaintTool] = useState<PaintTool>("brush");
  const [brushes, setBrushes] = useState<Record<PaintTool, BrushSettings>>(loadBrushes);
  const [paintColor, setPaintColor] = useState(() => { try { return localStorage.getItem("paint.color") || "#c0392b"; } catch { return "#c0392b"; } });
  const [paintColor2, setPaintColor2] = useState(() => { try { return localStorage.getItem("paint.color2") || "#ffffff"; } catch { return "#ffffff"; } });
  const [recent, setRecent] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("paint.recent") || "[]"); } catch { return []; } });
  const [projection, setProjection] = useState<Projection>("view");
  const [frontOnly, setFrontOnly] = useState(true);
  const [mirror, setMirror] = useState<0 | 1 | 2 | 3>(0);
  const [fillIsland, setFillIsland] = useState(false);
  const [paintInfo, setPaintInfo] = useState<PaintInfo | null>(null);
  const [paintDirty, setPaintDirty] = useState(false);
  const [paintNote, setPaintNote] = useState("");
  const [paintLoadTick, setPaintLoadTick] = useState(0);
  // The sidecar has been read (or found missing): the saved paint waits for it, see below.
  const [sidecarReady, setSidecarReady] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState("");
  const placer = useRef<DabPlacer | null>(null);
  const pendingPaint = useRef<any>(null);
  const lastExport = useRef("");
  const cursorRef = useRef<HTMLDivElement>(null);
  // Which element one click selects. Not what gets stored -- the document is always corners.
  const [elem, setElem] = useState<ElemKind>("vert");
  const [gizmoMode, setGizmoMode] = useState<GizmoMode>("move");
  const [space, setSpace] = useState<"global" | "local">(P.space);
  const [filter, setFilter] = useState("");
  const readoutRef = useRef("");
  const readoutSig = useMemo(() => makeSignal(), []);
  const setReadout = useCallback((t: string) => {
    if (readoutRef.current === t) return;
    readoutRef.current = t;
    readoutSig.emit();
  }, [readoutSig]);
  const [toolsOpen, setToolsOpen] = useLocal("edit.tools", true);
  const [sideOpen, setSideOpen] = useLocal("edit.side", true);
  /** The gizmo is off while the select tool is active, the way Blender's Tweak tool works. */
  const [gizmoOn, setGizmoOn] = useState(true);
  /** Inverse kinematics reach in pose mode; 0 is off. */
  const [ikDepth, setIkDepth] = useState(0);
  /** How a skin binding weighs its vertices. */
  const [weights, setWeights] = useState<"envelope" | "heat">(P.weights);
  const [bakeSize, setBakeSize] = useState(P.bake_size);
  const [baking, setBaking] = useState<BakeKind | "">("");
  const [bakePreviews, setBakePreviews] = useState<Record<string, string>>({});
  const [bakeNote, setBakeNote] = useState("");
  const [liveNote, setLiveNote] = useState("");
  /** "Start game" is pressing play in the Studio's copy of the game and taking the scene again. */
  const [starting, setStarting] = useState(false);
  /** The whole level, or only what the game draws right now (see EditWorld.setShowCulled). */
  const [showCulled, setShowCulled] = useState(true);
  /** The last snapshot of a live game, so a modifier change can rebuild without asking again. */
  const liveSnap = useRef<any>(null);
  const livePending = useRef<{ parts: Record<string, PartOverride>; world?: WorldOverride | null } | null>(null);
  const liveTimer = useRef(0);
  /** Which engine the live game runs, and where its app object was found; both from the open. */
  const liveInfo = useRef<{ engine: string; at: string }>({ engine: "three", at: "" });
  /** A PlayCanvas asset is built by PlayCanvas, in a hidden studio, and mirrored into the viewport. */
  const pcStudio = useRef<Studio | null>(null);
  const pcHost = useRef<HTMLDivElement | null>(null);
  const pcMod = useRef<any>(null);
  const pcUrl = useRef("");
  const [report, setReport] = useState<Defects | null>(null);
  /** The Mesh section is open. The defect report is taken only then. */
  const [meshOpen, setMeshOpen] = useState(false);
  const [modErrors, setModErrors] = useState<string[]>([]);

  const [specs, setSpecs] = useState<ParamSpec[]>([]);
  // The module's manifest as it was exported, kept from the moment the asset opened: presets and
  // handles are read from it (presets.ts, handles.ts), and both validate it themselves.
  const [manifest, setManifest] = useState<ModularManifest | null>(null);
  const [edits, setEdits] = useState<Edits>(() => emptyEdits(""));
  const [saved, setSaved] = useState<string>("");
  // ---- the configurator ----------------------------------------------------------------
  /** Which tab the side panel shows for a configurator asset. Others have no tabs at all. */
  const [sideTab, setSideTab] = useState<"options" | "scene">("options");
  /** The studio lighting the DAY / NIGHT bar chose, for an asset with no `night` setting. */
  const [look, setLook] = useState<StudioLook>("day");
  /** Arrows on the model. On by default, remembered, and only offered when the asset has handles. */
  const [handlesOn, setHandlesOn] = useLocal("edit.cfg.handles", true);
  /** The studio's own backdrop and environment, captured the moment each world is made — before
   *  any asset can put a background of its own on the scene. DAY / NIGHT and the thumbnails. */
  const studioRef = useRef<StudioRefs | null>(null);
  const studioEnv = useRef<any>(null);
  /** Rebuilds in flight, and when a value last moved: the thumbnail queue waits on both. */
  const rebuilding = useRef(0);
  const lastValuesAt = useRef(0);
  /** Modules imported for thumbnails whose detected constants differ from the live module's. */
  const thumbModules = useRef(new Map<string, FileAsset>());
  /** The edit document as it was when the current slider / handle / colour gesture began: pushed
   *  onto undo when the gesture ends, so one undo takes back the whole drag. */
  const gesture = useRef<Edits | null>(null);
  /** A sidecar with something in it is on disk for this source — read at open, set by a save. A
   *  save that has nothing left to keep must then empty it, or the old edits come back next open. */
  const sidecarOnDisk = useRef(false);
  /** Bumped each time the sidecar's document arrives; `docBuilt` is the bump the model on screen
   *  was built after. See the effect beside the parameter rebuild for why both exist. */
  const [docTick, setDocTick] = useState(0);
  const docTickRef = useRef(0);
  docTickRef.current = docTick;
  const docBuilt = useRef(-1);
  /** The view the editor itself last framed (camSig). A configurator keeps a growing model in
   *  view only while the camera is still exactly there — never against a view the person chose. */
  const framedCam = useRef("");
  const noteFramed = () => { const w = world.current; if (w) framedCam.current = camSig(w); };

  // A setting changed in the Settings page while the editor is open: apply the fields that
  // changed, and only those, so a choice made in this session is not undone by an unrelated switch.
  const prevPrefs = useRef(P);
  useEffect(() => {
    const a = prevPrefs.current, b = P;
    prevPrefs.current = b;
    if (a === b) return;
    if (a.shading !== b.shading) setShading(b.shading);
    if (a.solid_light !== b.solid_light) setSolidLight(b.solid_light);
    if (a.solid_color !== b.solid_color) setSolidColor(b.solid_color);
    if (a.space !== b.space) setSpace(b.space);
    if (a.weights !== b.weights) setWeights(b.weights);
    if (a.bake_size !== b.bake_size) setBakeSize(b.bake_size);
    const ov: Partial<Overlays> = {};
    for (const k of ["grid", "axes", "outline", "bones", "extras"] as const) if (a[k] !== b[k]) ov[k] = b[k];
    if (Object.keys(ov).length) setOverlays((o) => ({ ...o, ...ov }));
    world.current?.applyOptions(worldOptions(b));
  }, [P]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [saving, setSaving] = useState(false);
  const [saveNote, setSaveNote] = useState("");
  // Read by callbacks made once, so they see this render's document and mode rather than the
  // one that existed when they were created.
  const editsRef = useRef<Edits>(edits);
  editsRef.current = edits;
  const modeRef = useRef<Mode>(mode);
  modeRef.current = mode;

  /**
   * The mesh half of the document, in the one order that is correct.
   *
   * Hand-moved vertices belong to the BASE mesh and the modifier stack evaluates over them, which
   * is Blender's order; the reverse would let a subdivide quietly throw away hand work.
   *
   * And in edit mode the stack is held off completely. You drag the corners the code made, not
   * whatever a remesh left behind — again what Blender does, and the only version where the
   * position you save is a position a rebuild can find again.
   */
  const applyMeshEdits = (w: EditWorld) => {
    w.applyVertEdits(editsRef.current.verts || []);
    return w.applyMods(modeRef.current === "edit" ? [] : editsRef.current.mods);
  };

  const undo = useRef<Edits[]>([]);

  // ---- terrain -------------------------------------------------------------------------
  //
  // The field lives in a REF, not in state. A 513-sample heightfield is a megabyte of
  // Float32Array; in `edits` it would be deep-copied by JSON on every undo push, and in state it
  // would be compared by identity on every render of a component that changes sixty times a
  // second while a brush is down. What React is told is `terrainTick` — that something changed.
  const terrain = useRef<TerrainData | null>(null);
  const tool = useRef<TerrainTool>(new TerrainTool());
  const [terrainTick, setTerrainTick] = useState(0);
  const tbump = useCallback(() => setTerrainTick((n) => n + 1), []);
  const [terrainDirty, setTerrainDirty] = useState(false);
  const [terrainNote, setTerrainNote] = useState("");
  const [tReport, setTReport] = useState<TerrainReport | null>(null);
  const [layerIdx, setLayerIdx] = useState(0);
  const [tSize, setTSize] = useState(256);
  const [tRes, setTRes] = useState(257);
  const [palette, setPalette] = useState<ScatterAsset[]>([]);
  const paletteRef = useRef<ScatterAsset[]>([]);
  paletteRef.current = palette;
  const [scatterQ, setScatterQ] = useState("");
  const [scatterRows, setScatterRows] = useState<any[]>([]);
  const [scatterBusy, setScatterBusy] = useState(false);
  // ---- the emitted builder. See `doEmit`; the whole point of the mode is this one button. ----
  const [emitPath, setEmitPath] = useState("");
  const [emitEngine, setEmitEngine] = useState<"three" | "playcanvas" | "">("");
  const [emitLod, setEmitLod] = useState(1);
  const [emitBusy, setEmitBusy] = useState(false);
  const [emitNote, setEmitNote] = useState("");
  const [emitError, setEmitError] = useState("");
  /** The shared browser's open games. Null until asked, so "unknown" and "none" stay apart. */
  const [liveTabs, setLiveTabs] = useState<LiveTab[] | null>(null);
  /** One built object per scattered asset, cloned for every item standing on the ground. The
   *  Library's builder is an async import; resolving it once and cloning is the difference
   *  between five hundred trees and five hundred module evaluations. */
  const protos = useRef(new Map<string, any>());
  const protoBusy = useRef(new Set<string>());
  const srcRef = useRef(source);
  srcRef.current = source;

  const reportTimer = useRef(0);
  /** Straight away — for the moment the ground is made or loaded, which is exactly when somebody
   *  reads the panel. 13 ms on 513², and it is paid once. */
  const reportNow = useCallback((t: TerrainData | null) => {
    window.clearTimeout(reportTimer.current);
    if (!t) { setTReport(null); return; }
    try { setTReport(terrainReport(t)); } catch { setTReport(null); }
  }, []);
  /** The report walks slope bands and coverage over the WHOLE field, so during a stroke it is
   *  taken when the button comes up rather than on every frame. */
  const scheduleReport = useCallback(() => {
    window.clearTimeout(reportTimer.current);
    reportTimer.current = window.setTimeout(() => {
      const t = terrain.current;
      if (!t) { setTReport(null); return; }
      try { setTReport(terrainReport(t)); } catch { setTReport(null); }
    }, 180);
  }, []);

  const terrainEnv = useRef<TerrainViewEnv>({
    prototype: (asset: string) => {
      const got = protos.current.get(asset);
      if (got) return got;
      if (protoBusy.current.has(asset)) return null;
      const a = paletteRef.current.find((p) => p.id === asset);
      if (!a) return null;
      protoBusy.current.add(asset);
      // The SAME resolver the Add palette uses. A tree planted by the brush and a tree dropped by
      // hand have to be the same object, or the level is dressed with two kinds of the same tree.
      resolveRefRef.current(a.ref)
        .then((obj: any) => {
          if (!obj) throw new Error("built nothing");
          protos.current.set(asset, obj);
          const tv = world.current?.terrain;
          tv?.dropScatterFor(asset);
          tv?.syncScatter();
        })
        .catch((e: any) => setTerrainNote("could not build " + a.name + ": " + String(e?.message || e).slice(0, 90)))
        .finally(() => protoBusy.current.delete(asset));
      return null;
    },
    textureUrl: (p: string) => api.engineModelUrl(srcRef.current?.project || "", p),
    chunkSamples: CHUNK_SAMPLES,
  }).current;

  /**
   * Put the field in the viewport, or take it out. Handing in the same one twice is free.
   *
   * The two round-two settings are read HERE rather than inside the view, because a `TerrainView`
   * decides at construction whether it is chunked and whether its scatter is instanced — one
   * decision, not a branch on every frame. Changing either in Settings therefore has to build the
   * view again, which is what the null in the middle is for.
   */
  useEffect(() => {
    const t = terrain.current;
    const chunks = !!t && wantChunks(P.terrain_chunks, t.spec.res);
    const instances = !!P.terrain_instances;
    if (terrainEnv.chunks !== chunks || terrainEnv.instances !== instances) {
      terrainEnv.chunks = chunks;
      terrainEnv.instances = instances;
      world.current?.showTerrain(null, terrainEnv);
    }
    world.current?.showTerrain(terrain.current, terrainEnv);
  }, [terrainTick, phase, terrainEnv, P.terrain_chunks, P.terrain_instances]);

  const makeGround = useCallback(() => {
    try {
      terrain.current = ensureLayers(makeTerrain({ size: tSize, res: tRes, maxHeight: Math.max(8, Math.round(tSize / 8)) }));
      tool.current.history.clear();
      protos.current.clear();
      setTerrainDirty(true);
      setTerrainNote("");
      tbump();
      reportNow(terrain.current);
    } catch (e: any) {
      setTerrainNote("the ground could not be made: " + String(e?.message || e).slice(0, 140));
    }
  }, [tSize, tRes, tbump, reportNow]);

  /** One undo or redo of a whole stroke. Its own stack, not the document's: an `Edits` step is a
   *  JSON clone and a terrain step is a rectangle of samples. */
  const terrainStep = useCallback((redo: boolean) => {
    const t = terrain.current;
    if (!t) return;
    const r = redo ? tool.current.history.redo(t) : tool.current.history.undo(t);
    if (!r) { setTerrainNote(redo ? "nothing to redo" : "nothing to undo"); return; }
    const tv = world.current?.terrain;
    tv?.update(r);
    tv?.syncScatter();
    setTerrainNote("");
    setTerrainDirty(true);
    scheduleReport();
    tbump();
  }, [scheduleReport, tbump]);

  const pickLayer = useCallback((i: number) => {
    setLayerIdx(i);
    tool.current.brush.layer = i;
    tool.current.setKind("paint");
    tbump();
  }, [tbump]);

  const changeLayer = useCallback((i: number, patch: Partial<TerrainLayer>) => {
    const t = terrain.current;
    if (!t?.layers?.[i]) return;
    t.layers[i] = { ...t.layers[i], ...patch };
    world.current?.terrain?.readLayers(true);
    setTerrainDirty(true);
    tbump();
  }, [tbump]);

  const pickScatter = useCallback((id: string) => {
    tool.current.brush.asset = id;
    tool.current.setKind("scatter");
    tbump();
  }, [tbump]);

  // ---- the emitted builder ---------------------------------------------------------------
  //
  // WHY THIS BUTTON IS THE POINT OF TERRAIN MODE. Everything else here writes `.terrain.json`:
  // the editor's own memory of the field, so that reopening the asset finds it as it was left.
  // No game can read that file, and no game should have to. This writes the other one — a module
  // the game imports, holding the heights, the layers and the scatter as code that imports
  // nothing back. Unity ships a .asset the runtime decodes; Godot ships a resource; this ships
  // the game's own source, which is the whole premise of the Studio and was the one thing terrain
  // could not yet do.
  //
  // The engine is decided rather than assumed: `new THREE.BufferGeometry()` in a PlayCanvas game
  // is a ReferenceError on the first line, so the running game is asked first and believed.

  /** Which engine, and whether the game is up. Recomputed as the browser's tabs come back. */
  const emitPick = useMemo(
    () => pickEmitEngine(source?.project || "", liveTabs, source?.engine || "",
                         !!source && isPcSource(source)),
    [source, liveTabs]);
  const emitFor = emitEngine || emitPick.engine;

  // The file's default name comes off the SIDECAR's, one step along the same chain, so the data
  // and the code cannot end up in different folders.
  useEffect(() => {
    setEmitPath(source ? terrainBuilderPath(sidecarPathOf(source)) : "");
    setEmitEngine("");
    setEmitNote("");
    setEmitError("");
  }, [source]);

  // Asked once when the mode opens: it answers both "which engine" and "is it running".
  useEffect(() => {
    if (mode !== "terrain" || liveTabs) return;
    let dead = false;
    api.liveGameStatus()
      .then((r: any) => { if (!dead) setLiveTabs((r?.tabs || []) as LiveTab[]); })
      .catch(() => { if (!dead) setLiveTabs([]); });
    return () => { dead = true; };
  }, [mode, liveTabs]);

  const doEmit = useCallback(async () => {
    const t = terrain.current;
    if (!t || !source) return;
    const path = emitPath.trim();
    if (!path) { setEmitError("give the file a path"); return; }
    setEmitBusy(true);
    setEmitNote("");
    setEmitError("");
    try {
      const fn = builderName(path);
      const code = emitBuilder(t, fn, emitFor, { lod: emitLod });
      // The PlayCanvas splat material is inside `emitBuilder` now, so this button and the HTTP
      // `code` action write the same file. It used to be appended here and only here.
      // THE SAME WRITE THE SIDECAR USES. There is one path out of this editor to disk and this
      // is it: the workspace endpoint, with the project as its root.
      await api.wsWrite(path, code);
      const kb = Math.max(1, Math.round(code.length / 1024));
      const where = path.replace(/\\/g, "/").split("/").slice(-2).join("/");
      setEmitNote(where + " · " + kb.toLocaleString() + " KB · " + fn
        + (emitPick.running ? " — written to disk; the running game shows it after a reload" : ""));
    } catch (e: any) {
      setEmitError("could not write it: " + String(e?.message || e).slice(0, 140));
    } finally {
      setEmitBusy(false);
    }
  }, [source, emitPath, emitFor, emitLod, emitPick.running]);

  // Only things with a shape: a sprite has nothing to stand on the ground. The rows are the
  // Library's own, unfiltered otherwise — this is the project's list, not a second one.
  useEffect(() => {
    if (mode !== "terrain" || !source?.project) return;
    let dead = false;
    setScatterBusy(true);
    api.engineAssets(source.project, { q: scatterQ, root: source.assetRoot || "" })
      .then((r: any) => {
        if (dead) return;
        setScatterRows((r.items || []).filter((a: any) => a.type === "spec" || a.type === "code" || a.type === "model").slice(0, 80));
      })
      .catch(() => { if (!dead) setScatterRows([]); })
      .finally(() => { if (!dead) setScatterBusy(false); });
    return () => { dead = true; };
  }, [mode, scatterQ, source?.project, source?.assetRoot]);

  const scatterCounts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const s of terrain.current?.scatter || []) out[s.asset] = (out[s.asset] || 0) + 1;
    return out;
  }, [terrainTick, tReport]);   // eslint-disable-line react-hooks/exhaustive-deps


  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [clipIdx, setClipIdx] = useState(0);
  const [showTimeline, setShowTimeline] = useState(false);

  const box = useRef({
    dragging: false, mode: "" as "" | "nav" | "gizmo" | "box" | "click" | "terrain" | "fly" | "paint",
    x0: 0, y0: 0, x1: 0, y1: 0, moved: false, shift: false,
  });
  /** Where the pointer was on the last move of a flight, for a browser that would not lock it. */
  const flyLast = useRef({ x: 0, y: 0 });
  const rectRef = useRef<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const rectSig = useMemo(() => makeSignal(), []);
  const setRect = useCallback((r: { x0: number; y0: number; x1: number; y1: number } | null) => {
    rectRef.current = r;
    rectSig.emit();
  }, [rectSig]);

  const clip: Clip | null = edits.clips[clipIdx] || null;
  const canWrite = ((source?.kind === "file" || source?.kind === "asset") && !!source.path)
    || source?.kind === "live";
  const dirtyParams = useMemo(() => new Set(Object.keys(edits.params)), [edits.params]);
  const dirty = useMemo(() => JSON.stringify(edits) !== saved, [edits, saved]);

  // ---- the edit document ---------------------------------------------------------------
  const push = useCallback((next: Edits | ((e: Edits) => Edits)) => {
    // Any discrete edit ends a parameter gesture that was never committed (a handle drag that
    // was cancelled, say): its snapshot is older than this edit, and pushing it later would make
    // one undo take back both.
    gesture.current = null;
    setEdits((prev) => {
      undo.current.push(JSON.parse(JSON.stringify(prev)));
      if (undo.current.length > Math.max(1, prefsRef.current.undo_max || UNDO_MAX)) undo.current.shift();
      return typeof next === "function" ? next(prev) : next;
    });
  }, []);

  const stepBack = useCallback(() => {
    gesture.current = null;
    const prev = undo.current.pop();
    if (prev) setEdits(prev);
  }, []);

  // Live: every edit also goes into the RUNNING game, through the same applyEdits the game itself
  // would call at startup, imported into its page from the Studio. Coalesced over a short beat,
  // because a drag commits once but a colour picker fires on every pixel.
  const liveApply = useCallback((patch: { parts?: Record<string, PartOverride>; world?: WorldOverride | null }) => {
    if (source?.kind !== "live") return;
    const p = livePending.current || (livePending.current = { parts: {} });
    if (patch.parts) Object.assign(p.parts, patch.parts);
    if (patch.world !== undefined) p.world = patch.world;
    window.clearTimeout(liveTimer.current);
    liveTimer.current = window.setTimeout(() => {
      const body = livePending.current;
      livePending.current = null;
      if (!body) return;
      const li = liveInfo.current;
      const js = li.engine === "playcanvas"
        ? "(async () => { const m = await import(" + JSON.stringify(window.location.origin + "/forge-ops.js") + ");"
          + " const app = " + PC_APP_JS(li.at) + "; if (!app) return { error: 'no PlayCanvas app' };"
          + " return m.pcApply(app.root, " + JSON.stringify(body) + "); })()"
        : "(async () => { const m = await import(" + JSON.stringify(window.location.origin + "/forge-ops.js") + ");"
          + " const s = (window.__live && __live.scenes && __live.scenes()[0]) || null; if (!s) return { error: 'no scene' };"
          + " return m.applyEdits(s, " + JSON.stringify(body) + "); })()";
      api.liveEval(source.project, js)
        .then((r) => {
          const v = r?.value;
          if (!r?.ok || v?.error) setLiveNote("the game did not take the edit: " + (r?.error || v?.error || ""));
          else if (v?.missing?.length) setLiveNote("not in the game: " + v.missing.slice(0, 4).join(", "));
          else setLiveNote("");
        })
        .catch((e) => setLiveNote(String(e?.message || e).slice(0, 160)));
    }, 120);
  }, [source]);

  // ---- build ---------------------------------------------------------------------------
  const fileAsset = useRef<FileAsset | null>(null);

  const pcOpts = (): PcEngine | undefined =>
    pcStudio.current ? { pc: pcMod.current, app: (pcStudio.current as any).app, pcUrl: pcUrl.current } : undefined;

  /**
   * Build a PlayCanvas asset. PlayCanvas runs the code in the hidden studio — a file through its
   * build function, a recorded run through the forge's own wrapper — then the entity tree is
   * snapshotted and mirrored into the three viewport. The mirror is what gets edited; the sidecar
   * it produces is applied to the real entities by `pcApply`, keyed the same way.
   */
  const buildPc = useCallback(async (vals: Record<string, ParamValue>, code?: string): Promise<{ error: string }> => {
    const w = world.current, s: any = pcStudio.current;
    if (!w || !s) return { error: "no PlayCanvas studio to build in" };
    s.clear();
    try {
      if (code !== undefined) {
        const r = await runGeneration(s, code, source?.devUrl || "");
        if (r.error) return { error: r.error };
      } else if (fileAsset.current) {
        const out = await fileAsset.current.build(pcMod.current, vals);
        const c = s.ctx();
        for (const o of Array.isArray(out) ? out : [out]) if (o) c.add(o);
      } else {
        // NOTHING TO RUN IS AN ERROR, NOT AN EMPTY SCENE. Mirroring the studio's bare root here
        // is exactly how "no code reached the builder" looked like a successful build of nothing.
        return { error: "nothing was run: this " + (source?.kind || "source")
          + " reached the PlayCanvas studio with no code and no file to build" };
      }
      s.app?.root?.syncHierarchy?.();
      const snap = pcSnapshot(s.root, { app: s.app });
      return w.loadPcSnapshot(snap);
    } catch (e: any) {
      return { error: String(e?.stack || e?.message || e).slice(0, 2000) };
    }
  }, [source]);

  const rebuildNow = useCallback(async (vals: Record<string, ParamValue>, why: string) => {
    const w = world.current;
    if (!w || !source) return;
    docBuilt.current = docTickRef.current;
    setPhase(why === "params" ? "ready" : "build");
    // A DETECTED value lives in the text, not in `params`: the module was imported once, with
    // the constants it had then. So when one changes, the source is rewritten in memory — the
    // same one-number edit a save makes — and imported again. The signature says which values
    // the current module carries, so putting a slider back also imports back.
    const sig = spanSig(specs, vals);
    let r: { error: string };
    if (source.kind === "live") {
      const snap = liveSnap.current;
      r = !snap ? { error: "no snapshot of the game yet" } : snap.engine === "playcanvas" ? w.loadPcSnapshot(snap) : await w.loadJSON(snap);
    } else if (source.kind === "file") {
      if (sig !== importedSig.current || !fileAsset.current) {
        const code = applyParams(source.code, vals, specs).source;
        try {
          const asset = await loadFileAsset(code, engineUrl.current, relResolver(source), pcOpts());
          fileAsset.current?.dispose();
          fileAsset.current = asset;
          importedSig.current = sig;
        } catch (e: any) {
          setError(String(e?.message || e));
          setPhase("error");
          return;
        }
      }
      r = pcStudio.current ? await buildPc(vals) : await w.runFile(fileAsset.current.build, vals);
    } else {
      const code = sig !== spanSig(specs, {}) ? applyParams(source.code, vals, specs).source : source.code;
      r = pcStudio.current ? await buildPc(vals, code) : await w.run(code, source.devUrl, vals);
    }
    if (r.error) { setError(r.error); setPhase("error"); return; }
    setError("");
    if ("log" in r) setLog((r as { log: string[] }).log.slice(0, 20));
    // Overrides are transforms and go on FIRST, because a merge bakes world transforms and a
    // modifier that runs before them would weld the model in the wrong pose. Then the stack,
    // then the rig, which has to bind to whatever meshes the stack left behind.
    w.applyOverrides(edits);
    setModErrors(applyMeshEdits(w).errors);
    if (edits.bones.length) w.buildRigFrom(edits.bones, w.rig?.mode || "parts", edits.pose);
    // Paint follows the rebuild: the same pixels where the texture layout is unchanged, the strokes
    // painted again where it changed.
    const ps = w.paintScan();
    if (ps && (ps.replayed || ps.relaid)) {
      setPaintNote(ps.replayed ? "the texture layout changed; paint was redrawn from its strokes"
        : "the texture layout changed and this paint has no strokes to redraw from; it may not line up");
    }
    if (why !== "params") {
      // A mirrored game opens through its own camera: what the player sees is the view to edit
      // in, not a frame around the whole island that makes every dino a dot. Orbit out of it, or
      // press Numpad 0, and the orbit continues from where the camera stood.
      if (!(source.kind === "live" && w.lookThroughGameCamera())) {
        if (!(aimRef.current && w.aimSpec(aimRef.current))) w.frameAll();
      }
      noteFramed();
    }
    // The placements go on LAST: they are positioned in the world the code just made, and a
    // clone can only be made once the thing it copies exists.
    if (edits.placed?.length) {
      const put = await w.applyPlacedDoc(edits.placed, resolveRefRef.current);
      if (put.errors.length) setPlaceNote(put.errors[0]);
      // A placement MOVED after it was placed keeps that move in `parts`, which went on above,
      // before the placement existed to take it. Again, now that it does.
      w.applyOverrides(edits);
      if (source.kind === "live") void liveSyncRef.current(edits.placed);
    }
    setParts(w.parts());
    setStats(w.stats());
    setPhase("ready");
    // A configurator's model that just grew out of the view is brought back into it (see
    // `keepInView`), unless the person has moved the camera themselves.
    if (why === "params") keepInViewRef.current();
  }, [source, edits, specs, buildPc]);
  /** The same, counted: the preset thumbnails wait while any rebuild is in flight. */
  const rebuild = useCallback(async (vals: Record<string, ParamValue>, why: string) => {
    rebuilding.current++;
    try { await rebuildNow(vals, why); } finally { rebuilding.current--; }
  }, [rebuildNow]);

  // The parameter values a build should use: what the file says, then what the user changed.
  const values = useMemo(() => {
    const v: Record<string, ParamValue> = {};
    for (const s of specs) v[s.key] = s.value;
    return { ...v, ...edits.params };
  }, [specs, edits.params]);

  // ---- the configurator, derived -------------------------------------------------------
  // Read through refs by the callbacks handed to the world and to the thumbnail queue, which are
  // made once and must still see this render's values.
  const valuesRef = useRef(values);
  valuesRef.current = values;
  const specsRef = useRef(specs);
  specsRef.current = specs;
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const presets = useMemo(() => presetsFromManifest(manifest), [manifest]);
  const declaredHandles = useMemo(() => declaredHandleCount(manifest), [manifest]);
  // Agent C's parser; it promises never to throw, and the editor does not bet the panel on it.
  const handleSpecs = useMemo(() => {
    try { return handlesFromManifest(manifest, specs); } catch { return []; }
  }, [manifest, specs]);
  const handleAxes = useMemo(() => Object.fromEntries(handleSpecs.map((h) => [h.param, h.axis])) as Record<string, "x" | "y" | "z">,
    [handleSpecs]);
  const configurable = useMemo(() => isConfigurator(presets, declaredHandles, specs), [presets, declaredHandles, specs]);
  /** What the Options tab lists: the parameters, less the manifest's own name and flags. */
  const optionSpecs = useMemo(() => panelSpecs(specs, configurable), [specs, configurable]);
  const nightParam = useMemo(() => hasNightParam(specs), [specs]);
  const activePreset = useMemo(() => matchPreset(presets, values, specs), [presets, values, specs]);
  // An asset with its own `night` setting decides the look itself, so the bar and that setting
  // can never disagree; one without it keeps the bar's own choice.
  const lookNow: StudioLook = nightParam ? (values[NIGHT_PARAM] ? "night" : "day") : look;
  useEffect(() => { lastValuesAt.current = performance.now(); }, [JSON.stringify(values)]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- boot ----------------------------------------------------------------------------
  useEffect(() => {
    if (!source) return;
    let dead = false;
    const el = canvasBox.current;
    if (!el) return;
    setPhase("engine");
    setError("");
    setRenderError("");
    setLog([]);

    (async () => {
      // ALWAYS THREE, for the viewport. It was "" for anything not labelled three, so the FIRST
      // module that loaded won — PlayCanvas, for a PlayCanvas asset — and was then refused two
      // lines down. A PlayCanvas asset is built in the hidden PlayCanvas app below; the viewport
      // that shows it is three either way, so three is the only thing worth asking for here.
      const want: EngineKind = "three";
      let mod: any;
      let threeUrl = "";
      try {
        const eng = await loadEngine(source.moduleUrls, want);
        if (eng.kind !== "three") throw new Error("kind:" + eng.kind);
        mod = eng.mod;
        threeUrl = eng.url;
        engineUrl.current = eng.url;
      } catch (e: any) {
        if (dead) return;
        setError(String(e?.message || e).startsWith("kind:")
          ? "The editor is three.js only. This project's engine is " + String(e.message).slice(5)
            + ", which has no scene graph shaped like this one — the read-only Asset tab still shows it."
          : "Could not load the project's own engine. The editor builds with the same three.js the "
            + "asset was made with, and looks for it in node_modules and beside the page.\n\n"
            // The loader's own account, one line per place it looked. Without it this message
            // was true and useless: it named no URL and no reason.
            + String(e?.message || e).slice(0, 1200));
        setPhase("error");
        return;
      }
      if (dead) return;

      // A PlayCanvas asset needs PlayCanvas as well: the viewport stays three, the building
      // happens in a hidden PlayCanvas studio, and the result is mirrored across.
      let pcEng: Awaited<ReturnType<typeof loadEngine>> | null = null;
      if (source.kind !== "live" && isPcSource(source)) {
        try { pcEng = await loadEngine(source.moduleUrls, "playcanvas"); }
        catch (e: any) {
          if (dead) return;
          setError("This asset is PlayCanvas, and no PlayCanvas build was found to run it with. The editor looks in "
            + "the project's node_modules and beside its page, then in any PlayCanvas project the Studio knows.\n\n"
            + String(e?.message || e).slice(0, 800));
          setPhase("error");
          return;
        }
        if (dead) return;
      }

      const canvas = document.createElement("canvas");
      canvas.className = "block w-full h-full";
      el.appendChild(canvas);
      const w = new EditWorld(mod, canvas, BACKDROP, {
        onSelect: (keys, a) => { setSelection(keys); setActive(a); },
        onEdit: (kind, key, value) => {
          if (kind === "part") {
            push((e) => ({ ...e, parts: { ...e.parts, [key]: value } }));
            liveApply({ parts: { [key]: value } });
          } else if (kind === "vert") {
            // The complete list for that mesh replaces the old one. A delta would need an inverse
            // for undo and would drift if the document were ever applied twice; a replacement
            // cannot do either, and push() gives undo for nothing.
            const next = (value || []) as Edits["verts"];
            push((e) => ({
              ...e,
              verts: [...(e.verts || []).filter((v) => v.mesh !== key), ...(next || [])],
            }));
          } else push((e) => ({ ...e, pose: { ...e.pose, [key]: value } }));
        },
        onNav: () => navSig.emit(),
      }, worldOptions(prefsRef.current));
      // Before anything has run in it: the backdrop Color and the environment are still the
      // studio's own. DAY / NIGHT tints that Color in place; the thumbnails light with that map.
      studioRef.current = captureStudio(w.scene);
      studioEnv.current = w.scene.environment || null;
      // A glTF loader that shares this three, for any code in the editor that wants to place
      // a model file. The server binds it to the same engine module the viewport loaded.
      // THE THREE THE VIEWPORT ACTUALLY LOADED, by URL. Not the project's, not the Studio's: the
      // list above can end on another project's build, and a loader bound by any other rule makes
      // materials the renderer here cannot draw — r160 called `material.onBuild` on r183
      // materials, threw every frame, and the viewport was blank under a full outliner.
      w.engineUrl = threeUrl;
      w.loaderUrl = api.engineLoaderUrl(source.project || "", loaderThreeRef(threeUrl, window.location.href));
      w.onRenderError = (msg) => { if (!dead) setRenderError(msg); };
      // WHICH SERVER THE GAME'S CODE COMES FROM. Not the workspace's — the OWNING game's. A
      // workspace here holds rot-haul on 5178 and rot-rush on 5179, and asking 5178 for one of
      // rot-rush's four hundred files returns a 404 that reads exactly like a missing file.
      w.bases = (source.bases && source.bases.length ? source.bases
        : [source.devUrl].filter(Boolean)) as string[];
      w.assetRoot = source.assetRoot || "";
      w.modelUrl = (file: string) => api.engineModelUrl(source.project || "", file);
      world.current = w;
      // The live world, reachable from the page: what lets the review harness and an agent on the
      // live link drive the editor — select a part, look through a camera, read the stats —
      // instead of guessing at pixel coordinates.
      (window as any).__edit = w;
      w.isLive = source.kind === "live";
      pcStudio.current?.dispose();
      pcStudio.current = null;
      if (pcEng && pcHost.current) {
        try {
          pcStudio.current = buildStudio(pcEng, pcHost.current, { background: BACKDROP, ground: false, sky: false });
          pcMod.current = pcEng.mod;
          pcUrl.current = pcEng.url;
        } catch (e: any) {
          setError("PlayCanvas could not start a hidden application here: " + String(e?.message || e).slice(0, 300));
          setPhase("error");
          return;
        }
      }
      const r = el.getBoundingClientRect();
      w.resize(r.width, r.height);
      setPhase("build");

      // A file is a MODULE and is imported; a recorded run is a function body and is inlined.
      // Running a file the second way is a syntax error on its first `export`, which reads as a
      // broken asset rather than as the loader being handed the wrong shape.
      // Constants at the top of the file, then what each placed asset was handed at its call.
      const detected = [...scanParams(source.code), ...scanCallParams(source.code)];
      let found = detected;
      fileAsset.current?.dispose();
      fileAsset.current = null;
      if (source.kind === "file") {
        try {
          const asset = await loadFileAsset(source.code, threeUrl, relResolver(source), pcOpts());
          if (dead) { asset.dispose(); return; }
          fileAsset.current = asset;
          found = mergeSpecs(asset.specs, detected);
        } catch (e: any) {
          if (dead) return;
          setError(String(e?.message || e));
          setPhase("error");
          return;
        }
      }
      setSpecs(found);
      const man = source.kind === "file" ? fileAsset.current?.manifest ?? null : null;
      setManifest(man);
      // A configurator opens on HERO, the view its preset thumbnails are photographed from.
      const hero = isConfigurator(presetsFromManifest(man), declaredHandleCount(man), found);

      const first = Object.fromEntries(found.map((s) => [s.key, s.value]));
      importedSig.current = spanSig(found, first);
      const run = source.kind === "live"
        ? await snapshotLive(w, source.project, liveSnap, liveInfo)
        : pcStudio.current
          // EVERY KIND BUT A FILE CARRIES ITS CODE, the rule `rebuild` already used. This said
          // `kind === "generation"`, so an asset reached `buildPc` with no code, ran nothing,
          // mirrored an empty root and reported the build ready - 0 triangles and no error.
          ? await buildPc(first, source.kind === "file" ? undefined : source.code)
          : fileAsset.current
            ? await w.runFile(fileAsset.current.build, first)
            : await w.run(source.code, source.devUrl, first);
      if (dead) return;
      if (run.error) { setError(run.error); setPhase("error"); }
      else {
        if ("log" in run) setLog((run as { log: string[] }).log.slice(0, 20));
        setModErrors(applyMeshEdits(w).errors);
        w.paintScan();
        if (!(source.kind === "live" && w.lookThroughGameCamera())) {
          if (!(aimRef.current && w.aimSpec(aimRef.current))) {
            if (hero) w.setView("3q");
            w.frameAll();
          }
        }
        noteFramed();
        setParts(w.parts());
        setStats(w.stats());
        setPhase("ready");
      }
    })();

    return () => {
      dead = true;
      // NOT cancelAnimationFrame here. The frame loop is owned by its own effect below and runs
      // for the life of the component; cancelling its pending frame from this cleanup killed it
      // for good the first time the source changed — a black viewport with a working outliner.
      world.current?.dispose();
      if ((window as any).__edit === world.current) delete (window as any).__edit;
      world.current = null;
      pcStudio.current?.dispose();
      pcStudio.current = null;
      while (el.firstChild) el.removeChild(el.firstChild);
    };
    // Only a change of asset rebuilds the world; everything else edits the one already open.
  }, [source?.code, source?.path, source?.moduleUrls?.join("|")]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- the sidecar ---------------------------------------------------------------------
  useEffect(() => {
    sidecarOnDisk.current = false;
    setSidecarReady(false);
    if (!source || source.kind === "generation" || (source.kind === "file" && !source.path)) {
      setSidecarReady(true);
      // A recorded run has no sidecar, but it still starts CLEAN. Leaving `saved` empty made the
      // status bar say "unsaved" the moment the editor opened, before anything had been touched.
      const e = emptyEdits("");
      setEdits(e);
      setSaved(JSON.stringify(e));
      undo.current = [];
      return;
    }
    let dead = false;
    const p = sidecarPathOf(source);
    // A new asset starts on bare ground: the field, the palette and the stack all belong to the
    // thing that was open, and leaving them would sculpt one level into another.
    terrain.current = null;
    protos.current.clear();
    tool.current.history.clear();
    setPalette([]);
    setTReport(null);
    setTerrainDirty(false);
    setTerrainNote("");
    setTerrainTick((n) => n + 1);
    // The paint beside the asset. It goes on once the asset has built (see the effect below the
    // frame loop), because its layers are laid onto textures the build makes.
    pendingPaint.current = null;
    setPaintDirty(false);
    setPaintInfo(null);
    setPaintNote("");
    api.wsFile(paintDocPath(p))
      .then((f) => {
        if (dead) return;
        const doc = parsePaintDoc(f.text || "");
        if (doc && doc.targets.length) { pendingPaint.current = doc; setPaintLoadTick((n) => n + 1); }
      })
      .catch(() => { /* no paint beside this asset, which is the normal case */ });
    if (prefsRef.current.terrain) api.wsFile(terrainSidecarPath(p))
      .then((f) => {
        if (dead) return;
        const doc = unpackTerrain(f.text || "");
        if (!doc) return;
        try {
          terrain.current = ensureLayers(terrainRead(doc.field));
          setPalette(doc.palette);
          setTerrainTick((n) => n + 1);
          reportNow(terrain.current);
        } catch (err: any) {
          setTerrainNote("the saved ground could not be read: " + String(err?.message || err).slice(0, 120));
        }
      })
      .catch(() => { /* no terrain beside this asset, which is the normal case */ });
    api.wsFile(p)
      .then((f) => {
        if (dead) return;
        const e = normaliseEdits(JSON.parse(f.text || "{}"), source.path || source.project);
        sidecarOnDisk.current = countEdits(e) > 0;
        setEdits(e);
        setSaved(JSON.stringify(e));
        setDocTick((n) => n + 1);
        setSidecarReady(true);
        undo.current = [];
        // A live game gets its saved edits back the moment the editor opens on it, so the tab
        // and the sidecar agree from the first frame.
        if (source.kind === "live" && (Object.keys(e.parts).length || e.world)) liveApply({ parts: e.parts, world: e.world });
      })
      .catch(() => {
        if (dead) return;
        const e = emptyEdits(source.path);
        setEdits(e);
        setSaved(JSON.stringify(e));
        setSidecarReady(true);
      });
    return () => { dead = true; };
  }, [source?.path, source?.kind, source?.project]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- paint that was saved beside the asset --------------------------------------------
  useEffect(() => {
    if (phase !== "ready" || !pendingPaint.current || !sidecarReady) return;
    // The saved document goes on first: its slider values decide the texture layout the paint was
    // saved on. Laid on the file's defaults instead, a changed layout was redrawn from strokes, and
    // redrawn again when the document's values arrived — close, but not the texels that were saved.
    if (countEdits(editsRef.current) && docBuilt.current !== docTick) return;
    const w0 = world.current;
    if (!w0) return;
    const doc = pendingPaint.current;
    pendingPaint.current = null;
    const eng = w0.ensurePaint();
    eng.onChange = () => setPaintInfo(eng.info() as PaintInfo);
    eng.scan();
    void eng.loadDoc(doc).then((r) => {
      setPaintInfo(eng.info() as PaintInfo);
      setPaintDirty(false);
      const bits = [r.loaded + " texture" + (r.loaded === 1 ? "" : "s") + " of paint loaded"];
      if (r.replayed) bits.push(r.replayed + " redrawn from strokes (the layout changed)");
      if (r.missing) bits.push(r.missing + " kept aside (their texture is not in this build)");
      setPaintNote(bits.join(" · "));
    }).catch((e: any) => setPaintNote("the saved paint could not be read: " + String(e?.message || e).slice(0, 120)));
  }, [phase, paintLoadTick, sidecarReady, docTick, stats]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- the frame loop ------------------------------------------------------------------
  useEffect(() => {
    const tick = () => {
      raf.current = requestAnimationFrame(tick);
      const w = world.current;
      if (!w) return;
      // Guarded. A throw inside an animation frame reaches nobody: the loop keeps running, the
      // outliner stays full, and the viewport is blank with no sentence anywhere to say why.
      w.renderSafe();
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, []);

  useEffect(() => {
    const el = canvasBox.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      world.current?.resize(r.width, r.height);
      setParts((p) => (world.current ? world.current.parts() : p));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Stats and the on-screen part sizes are read on a slow beat rather than on every frame. Both
  // depend on the CAMERA — a part is 9 px until you lean in — so a value taken once at build time
  // is stale the moment anyone orbits, and the frame rate would have read zero for ever.
  useEffect(() => {
    if (phase !== "ready") return;
    let shape = "";
    const id = window.setInterval(() => {
      const w = world.current;
      if (!w || document.hidden) return;
      // The frame rate and the draw count: read on every beat, drawn by the status bar alone.
      liveStats.current = w.liveStats();
      statsSig.emit();
      // The rest re-renders the editor, so it is set only when what it shows has changed; and the
      // walk over the whole scene waits while a hand is on the view, where it would cost a frame.
      const busy = w.nav.dragging || w.nav.flying || w.gizmo.dragging;
      if (!busy) {
        const s = w.stats();
        const k = statsShape(s);
        if (k !== shape) { shape = k; setStats(s); }
      }
      if (overlays.sizes || (!busy && w.selection.size)) {
        const next = w.parts();
        setParts((prev) => (samePartsView(prev, next, overlays.sizes) ? prev : next));
      }
    }, 500);
    return () => window.clearInterval(id);
  }, [phase, overlays.sizes, statsSig]);

  // The defect report is expensive — it welds a copy of the mesh to count duplicates — so it is
  // taken when the thing being looked at changes, not on a timer, and only while the Mesh section
  // that shows it is open. It used to run on every selection with that section closed: 250 ms
  // frozen for one click on a Dino Smash dino, and two seconds after a box select.
  useEffect(() => {
    const w = world.current;
    if (!w || phase !== "ready" || !meshOpen) { setReport(null); return; }
    const id = window.setTimeout(() => { try { setReport(w.inspect(active)); } catch { setReport(null); } }, 120);
    return () => window.clearTimeout(id);
  }, [phase, active, JSON.stringify(edits.mods), stats?.triangles, meshOpen]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the world's flags in step with the panel.
  useEffect(() => { world.current?.setShading(shading); }, [shading]);
  // Blender's defaults per shading mode, for the same reason: solid is for working, under a rig
  // that keeps every part readable; material shows the scene lit as the code lit it; rendered is
  // the whole world, fog and all. The two checks in the popover override this until the mode changes.
  useEffect(() => {
    setSceneLights(shading === "material" || shading === "rendered");
    setSceneWorld(shading === "rendered");
  }, [shading]);
  useEffect(() => { if (world.current) world.current.solidLight = solidLight; }, [solidLight]);
  useEffect(() => { if (world.current) world.current.solidColor = solidColor; }, [solidColor]);
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    w.sceneLights = sceneLights;
    w.sceneWorld = sceneWorld;
  }, [sceneLights, sceneWorld, phase]);
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    w.setOverlays(overlays);
    w.refreshNormals();
    w.refreshOrigins();
  }, [overlays]);
  useEffect(() => {
    world.current?.setMode(mode);
    // Leaving mid-stroke: drop the open stroke rather than push half of one.
    if (mode !== "terrain") { tool.current.cancel(); setReadout(""); }
    // Paint: the engine tells the panel when anything changes; leaving drops an open stroke.
    const eng = world.current?.paint;
    if (mode === "paint" && eng) {
      eng.onChange = () => setPaintInfo(eng.info() as PaintInfo);
      setPaintInfo(eng.info() as PaintInfo);
      if (shading === "wire") setShading("material");
    } else {
      eng?.cancel();
      placer.current = null;
      if (cursorRef.current) cursorRef.current.style.display = "none";
    }
    bump();
  }, [mode, bump]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { world.current?.setGizmoMode(gizmoMode); }, [gizmoMode]);
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    w.gizmo.enabled = gizmoOn;
    // Never in Terrain mode: the gizmo would sit over the ground offering to move a part that
    // the brush is not aimed at.
    w.gizmo.show(gizmoOn && mode !== "terrain" && (mode === "pose" ? !!w.activeBone : w.selection.size > 0));
  }, [gizmoOn, mode, selection, activeBoneTick]);
  useEffect(() => { world.current?.setGizmoSpace(space); }, [space]);
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    w.ikDepth = ikDepth;
    // Re-place the gizmo: with IK on it sits on the bone's tail, otherwise on its head.
    if (w.activeBone) w.selectBone(w.activeBone);
  }, [ikDepth]);
  useEffect(() => { if (world.current) world.current.worldEdit = edits.world ?? null; }, [edits.world]);

  // A parameter change rebuilds the asset. Debounced while a slider is being dragged, because
  // running the whole asset on every pointer move is what turns a live editor into a slideshow.
  useEffect(() => {
    if (phase !== "ready" && phase !== "error") return;
    if (!Object.keys(edits.params).length && !specs.length) return;
    window.clearTimeout(rebuildTimer.current);
    rebuildTimer.current = window.setTimeout(() => { rebuild(values, "params"); }, 70);
    return () => window.clearTimeout(rebuildTimer.current);
    // On the VALUES and the STACK, not on the whole document. Moving a part must not re-run the
    // code; changing a modifier must, because a modifier is destructive to the live geometry and
    // the only honest way to remove one is to build again from the source and re-apply the rest.
  }, [JSON.stringify(values), JSON.stringify(edits.mods), mode]);   // eslint-disable-line react-hooks/exhaustive-deps

  // THE SAVED DOCUMENT, ONTO THE MODEL, ONCE. The sidecar is fetched alongside the first build and
  // usually lands first, while the engine is still loading: its values then reached the panel but
  // the effect above skipped the rebuild (not ready yet) and never ran again — and the part
  // overrides, bones and placements, which only a rebuild applies, were not applied at all until
  // something else changed. A saved preset reopened showing the file's shape under the preset's
  // numbers. So when the editor is ready and the document it holds has not been built with yet,
  // build once. An asset with no sidecar has nothing in it and pays nothing.
  // Not a "params" rebuild: the saved document can change the model's whole shape, so this one is
  // framed the way the first build was — the camera had been framed around the file's defaults.
  useEffect(() => {
    if (phase !== "ready" && phase !== "error") return;
    if (docBuilt.current === docTick || !countEdits(editsRef.current)) return;
    window.clearTimeout(rebuildTimer.current);
    rebuildTimer.current = window.setTimeout(() => { rebuild(valuesRef.current, "document"); }, 70);
  }, [phase, docTick]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Playback.
  useEffect(() => {
    if (!playing || !clip) return;
    let last = performance.now();
    let f = frame;
    let id = 0;
    const step = () => {
      id = requestAnimationFrame(step);
      const now = performance.now();
      f += ((now - last) / 1000) * clip.fps;
      last = now;
      if (f > clip.end) f = clip.loop ? clip.start + (f - clip.end) : clip.end;
      const rounded = Math.round(f);
      world.current?.setFrame(rounded, edits.pose);
      setFrame(rounded);
      if (!clip.loop && f >= clip.end) setPlaying(false);
    };
    id = requestAnimationFrame(step);
    return () => cancelAnimationFrame(id);
  }, [playing, clipIdx]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- pointer -------------------------------------------------------------------------
  const ndcOf = (e: { clientX: number; clientY: number }) => {
    const el = canvasBox.current!;
    const r = el.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * 2 - 1, y: -(((e.clientY - r.top) / r.height) * 2 - 1), px: e.clientX - r.left, py: e.clientY - r.top };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    const w = world.current;
    if (!w) return;
    host.current?.focus();
    const n = ndcOf(e);
    const el = canvasBox.current!;
    const r = el.getBoundingClientRect();
    try { el.setPointerCapture(e.pointerId); } catch { /* a nicety */ }

    // A modal transform started with G/R/S is confirmed by a click and cancelled by the right.
    if (w.gizmo.dragging && w.gizmo.isModal) {
      w.gizmo.end(e.button === 0);
      setReadout("");
      return;
    }
    // Alt+left orbits — except in Terrain mode with Blender's mouse, where Alt+click is the
    // eyedropper below and the middle button orbits. It used to orbit there too, so the eyedropper
    // could never be reached.
    if (w.nav.down(e.nativeEvent, (mode !== "terrain" && mode !== "paint") || w.nav.scheme === "unity")) { box.current.mode = "nav"; return; }
    // Hold the right button to fly: look with the mouse, W A S D to move — the way Unity, Godot
    // and Unreal all move through a level. The pointer is locked, so a turn never stops at the edge
    // of the screen; a browser that refuses flies on the cursor's own movement instead.
    if (e.button === 2) {
      w.flyStart();
      box.current.mode = "fly";
      flyLast.current = { x: e.clientX, y: e.clientY };
      try {
        const p: any = (el as any).requestPointerLock?.();
        if (p && typeof p.catch === "function") p.catch(() => { /* flies unlocked */ });
      } catch { /* flies unlocked */ }
      return;
    }
    if (e.button !== 0) return;

    // TERRAIN, before the gizmo. There is no selection to transform in this mode, so a handle
    // cannot be under the cursor — and a brush that lost its first stamp to an invisible one
    // would be maddening to use.
    // Alt-click names the thing under the cursor instead of painting: which of the ten thousand
    // scattered items that is, and its asset armed on the brush. Sculpting tools all put the
    // eyedropper on alt, and alt did nothing here before, so nothing a hand already knows moved.
    // It is also the only way to see that instancing kept picking honest — an InstancedMesh is
    // ONE object, and without `ScatterBatch.index` every tree in a forest has the same name.
    if (mode === "terrain" && terrain.current && e.altKey) {
      const hit = w.terrainScatterHit(n);
      if (hit) {
        pickScatter(hit.item.asset);
        const named = paletteRef.current.find((p) => p.id === hit.item.asset);
        setTerrainNote((named?.name || hit.item.asset) + " — item " + (hit.index + 1)
          + " of " + (terrain.current.scatter?.length || 0));
      } else {
        setTerrainNote("nothing scattered under the cursor");
      }
      return;
    }
    if (mode === "terrain" && terrain.current) {
      const at = w.terrainHit(n);
      tool.current.down(terrain.current, at, e.clientX, e.nativeEvent.timeStamp || performance.now(), { shift: e.shiftKey });
      w.terrain?.setCursor(at, tool.current.brush.radius, tool.current.brush.falloff);
      box.current = { dragging: true, mode: "terrain", x0: n.px, y0: n.py, x1: n.px, y1: n.py, moved: false, shift: e.shiftKey };
      setTerrainNote(tool.current.blocked());
      return;
    }

    // PAINT. A press starts a stroke; Alt takes a colour (or, with the clone brush, where to copy
    // from); the picker and the fill act on one click.
    if (mode === "paint") {
      const eng = w.ensurePaint();
      if (!eng.onChange) eng.onChange = () => setPaintInfo(eng.info() as PaintInfo);
      if (e.altKey || paintTool === "picker") {
        const h = eng.hit(n);
        if (!h) { setPaintNote("no painted surface under the cursor"); return; }
        if (e.altKey && paintTool === "clone") { eng.setCloneSource(h); setPaintNote(eng.note); return; }
        const c = eng.pick(h);
        if (c) { chooseColor(c); setPaintNote("took " + c); }
        return;
      }
      if (paintTool === "fill") {
        const h = eng.hit(n);
        if (!h) { setPaintNote("click a textured part to fill it"); return; }
        if (eng.fill(h, strokeOpts(), fillIsland)) { setPaintDirty(true); setRecent((r) => pushRecent(r, paintColor)); }
        return;
      }
      if (!eng.begin(strokeOpts())) { setPaintNote(eng.note); return; }
      placer.current = new DabPlacer(brushes[paintTool], (Date.now() & 0xffff) + 1);
      eng.dabs(placer.current.begin({ x: n.px, y: n.py, pressure: pressureOf(e.nativeEvent) }).map(toScreen));
      box.current = { dragging: true, mode: "paint", x0: n.px, y0: n.py, x1: n.px, y1: n.py, moved: false, shift: e.shiftKey };
      return;
    }

    const axis = w.gizmo.hover(n, w.camera);
    if (axis) {
      w.beginDrag();
      w.gizmo.begin(n, w.camera, { w: r.width, h: r.height });
      // Where the press began, so a press that never moves can be told apart from a drag.
      box.current = { dragging: false, mode: "gizmo", x0: n.px, y0: n.py, x1: n.px, y1: n.py, moved: false, shift: e.shiftKey };
      return;
    }
    box.current = { dragging: true, mode: "click", x0: n.px, y0: n.py, x1: n.px, y1: n.py, moved: false, shift: e.shiftKey };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const w = world.current;
    if (!w) return;
    const n = ndcOf(e);
    const el = canvasBox.current!;
    const r = el.getBoundingClientRect();
    const b = box.current;

    if (b.mode === "fly") {
      const locked = document.pointerLockElement === el;
      const dx = locked ? e.movementX : e.clientX - flyLast.current.x;
      const dy = locked ? e.movementY : e.clientY - flyLast.current.y;
      flyLast.current = { x: e.clientX, y: e.clientY };
      w.flyLook(dx, dy);
      return;
    }

    // The brush circle follows the pointer in paint mode, button down or not, so its size is seen
    // before it touches anything. A held stroke reads every pointer sample the browser coalesced:
    // a pen reports far more often than a frame, and each sample carries its own pressure.
    if (mode === "paint") {
      const c = cursorRef.current;
      if (c) {
        const s = brushes[paintTool].size;
        c.style.display = paintTool === "fill" || paintTool === "picker" ? "none" : "block";
        c.style.width = s + "px"; c.style.height = s + "px";
        c.style.left = (n.px - s / 2) + "px"; c.style.top = (n.py - s / 2) + "px";
      }
      if (b.mode === "paint") {
        const eng = w.paint;
        const pl = placer.current;
        if (eng && pl) {
          const evs: any[] = (e.nativeEvent as any).getCoalescedEvents?.() || [];
          const list = evs.length ? evs : [e.nativeEvent];
          const out: Dab[] = [];
          for (const ce of list) { const q = ndcOf(ce); out.push(...pl.moveTo({ x: q.px, y: q.py, pressure: pressureOf(ce) })); }
          if (out.length) eng.dabs(out.map(toScreen));
        }
        return;
      }
    }

    // The ring follows the cursor whether or not the button is down: seeing the size of the
    // brush BEFORE it takes anything is the whole reason a ring is drawn at all.
    if (mode === "terrain" && (b.mode === "terrain" || !b.dragging)) {
      const at = w.terrainHit(n);
      if (b.mode === "terrain") {
        const kind = tool.current.brush.kind;
        const rect = tool.current.move(terrain.current, at, e.clientX,
                                       e.nativeEvent.timeStamp || performance.now(), { shift: b.shift });
        if (rect) w.terrain?.update(rect);
        if (kind === "scatter" || kind === "erase") w.terrain?.syncScatter();
        setReadout(brushReadout(tool.current.brush, tool.current.doing));
      }
      if (tool.current.doing !== "strength") {
        w.terrain?.setCursor(at, tool.current.brush.radius, tool.current.brush.falloff);
      }
      return;
    }

    if (b.mode === "nav") { w.nav.move(e.nativeEvent, r.height); return; }
    if (b.mode === "gizmo" || (w.gizmo.dragging && w.gizmo.isModal)) {
      if (b.mode === "gizmo" && !b.moved && Math.hypot(n.px - b.x0, n.py - b.y0) > 3) b.moved = true;
      const d = w.gizmo.drag(n, w.camera, { w: r.width, h: r.height }, { snap: e.ctrlKey, fine: e.shiftKey });
      setReadout(d?.text || "");
      return;
    }
    if (b.dragging) {
      b.x1 = n.px; b.y1 = n.py;
      if (!b.moved && Math.hypot(b.x1 - b.x0, b.y1 - b.y0) > 4) { b.moved = true; b.mode = "box"; }
      if (b.mode === "box") setRect({ x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 });
      return;
    }
    w.gizmo.hover(n, w.camera);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const w = world.current;
    if (!w) return;
    const el = canvasBox.current!;
    try { el.releasePointerCapture(e.pointerId); } catch { /* never held */ }
    const b = box.current;

    if (b.mode === "fly") {
      w.flyEnd();
      try { if (document.pointerLockElement) document.exitPointerLock(); } catch { /* never locked */ }
      box.current.mode = "";
      return;
    }
    if (b.mode === "nav") { w.nav.up(); box.current.mode = ""; return; }
    if (b.mode === "paint") {
      if (w.paint?.end(TOOL_INFO[paintTool].label.toLowerCase())) { setPaintDirty(true); setRecent((r) => pushRecent(r, paintColor)); }
      placer.current = null;
      box.current = { ...b, dragging: false, mode: "", moved: false };
      return;
    }
    if (b.mode === "terrain") {
      if (tool.current.up(terrain.current)) { setTerrainDirty(true); scheduleReport(); tbump(); }
      w.terrain?.syncScatter();
      setReadout("");
      box.current = { ...b, dragging: false, mode: "", moved: false };
      return;
    }
    if (b.mode === "gizmo") {
      // A PRESS ON THE HANDLES THAT WENT NOWHERE IS A CLICK ON WHAT IS UNDER THEM. The handles sit
      // on the middle of the selection, so on anything small they cover it: the second click on a
      // sign letter — the one that should take the whole sign — landed on the move handle, moved
      // nothing, and wrote a no-op edit. Now it is let go, and the click goes through.
      if (!b.moved && !w.gizmo.isModal) {
        w.gizmo.end(false);
        setReadout("");
        box.current = { ...b, dragging: false, mode: "", moved: false };
        if (mode === "object") {
          const keys = w.pickMany(ndcOf(e), b.shift);
          if (keys.length) w.select(keys, b.shift);
          // Nothing under the handle: the same click on empty space that deselects everywhere else.
          else if (!b.shift) w.deselectAll();
        }
        return;
      }
      w.gizmo.end(true); setReadout(""); box.current.mode = ""; return;
    }
    if (b.mode === "box") {
      if (mode === "edit") w.boxSelectElems({ x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 }, b.shift);
      else w.boxSelect({ x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 }, b.shift);
      setRect(null);
      setParts(w.parts());
      bump();
    } else if (b.mode === "click") {
      const n = ndcOf(e);
      if (mode === "edit") {
        // Pixels, not normalised coordinates: a corner is a dot a few pixels across, and "the one
        // nearest the cursor" is the only rule that feels the same at every zoom.
        const hit = w.pickElem(n.px, n.py);
        if (hit.length) w.selectElems(hit, b.shift);
        else if (!b.shift) w.selectElems([], false);
        bump();
      } else if (mode === "pose") {
        w.selectBone(w.pickBone(n), b.shift);
        bump();
      } else {
        // One click can mean several keys: every piece of a group, moved together.
        const keys = w.pickMany(n, b.shift);
        if (keys.length) w.select(keys, b.shift);
        else if (!b.shift) w.deselectAll();
      }
    }
    box.current = { ...b, dragging: false, mode: "", moved: false };
  };

  // The wheel zooms; with Shift it pans up and down, with Alt sideways, and a trackpad's
  // sideways scroll pans sideways on its own. Ctrl is left to mean zoom too, because a pinch on
  // a trackpad arrives as Ctrl+wheel and a pinch that panned would feel broken.
  const onWheel = (e: React.WheelEvent) => {
    const w = world.current;
    if (!w) return;
    // Flying, the wheel sets the speed (Unity's rule), whatever else is held.
    if (w.nav.flying) { w.zoomAt(ndcOf(e), e.deltaY); return; }
    const h = canvasBox.current?.getBoundingClientRect().height || 600;
    if (e.shiftKey) w.nav.pan(0, e.deltaY * 0.6, h);
    else if (e.altKey) w.nav.pan(e.deltaY * 0.6, 0, h);
    else {
      if (e.deltaX) w.nav.pan(-e.deltaX * 0.6, 0, h);
      // Toward the point under the cursor, so the wheel reaches the thing you point at instead of
      // flying past it toward the middle of the level (navmath.zoomToward; a setting).
      if (e.deltaY) w.zoomAt(ndcOf(e), e.deltaY);
    }
  };

  // ---- keyboard ------------------------------------------------------------------------
  const onKeyDown = (e: React.KeyboardEvent) => {
    const w = world.current;
    if (!w) return;
    const tag = (e.target as HTMLElement)?.tagName;
    // A dropdown takes letters too (type "h" to jump to "house"): they must not also hide the
    // selection or start a scale in the viewport behind it.
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    const k = e.key;
    const stop = () => { e.preventDefault(); e.stopPropagation(); };

    // Flying: W A S D, Q E and Shift move the camera while the right button is held, and only
    // then — let go, and every Blender key on those letters works again.
    if (w.nav.flying && w.nav.flyKey(e.code, true)) return stop();

    // A live modal transform swallows almost everything: that is the point of a modal.
    if (w.gizmo.dragging) {
      if (k === "Escape") { w.gizmo.end(false); setReadout(""); return stop(); }
      if (k === "Enter") { w.gizmo.end(true); setReadout(""); return stop(); }
      if (/^[xyz]$/i.test(k)) {
        const el = canvasBox.current!;
        const r = el.getBoundingClientRect();
        w.gizmo.reaim(k.toLowerCase() as Axis, lastNdc.current, w.camera, { w: r.width, h: r.height });
        return stop();
      }
      if (w.gizmo.key(k)) return stop();
      return;
    }

    // PAINT'S KEYS COME FIRST in paint mode, for the reason Terrain's do: several of them are
    // letters the object modes have spent (E, R, S, X, G), and none of those means anything here.
    // P opens paint mode from any other mode.
    if (mode === "paint") {
      const a = paintKey(e);
      if (a) { applyPaintKey(a); return stop(); }
    } else if ((k === "p" || k === "P") && !e.ctrlKey && !e.metaKey && !e.altKey) {
      setMode("paint");
      return stop();
    }

    // TERRAIN'S KEYS COME FIRST, because several of them are letters the object modes have
    // already spent — R, S and X. In this mode those three do nothing anyway: two of them
    // transform a selection and one deletes it, and Terrain mode never has one.
    if (mode === "terrain") {
      const wantRedo = e.shiftKey || k === "y" || k === "Y";
      if ((k === "z" || k === "Z" || k === "y" || k === "Y") && (e.ctrlKey || e.metaKey)) {
        terrainStep(wantRedo);
        return stop();
      }
      if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        const did = tool.current.key(k);
        if (did) {
          if (did === "radius") {
            w.terrain?.setCursor(tool.current.cursor, tool.current.brush.radius, tool.current.brush.falloff);
          }
          setTerrainNote(tool.current.blocked());
          tbump();
          return stop();
        }
      }
    }
    // Terrain mode, on and off. Blender puts sculpt on a mode key for the same reason: a mode you
    // can only reach with the mouse is one you use less than you should.
    if (P.terrain && (k === "t" || k === "T") && !e.ctrlKey && !e.metaKey && !e.altKey) {
      setMode((m) => (m === "terrain" ? "object" : "terrain"));
      return stop();
    }

    // Blender's modal transform: press the key, then move the mouse.
    const modal = (m: GizmoMode, axis: Axis) => {
      if (mode === "pose" ? !w.activeBone : !w.selection.size) return;
      const el = canvasBox.current!;
      const r = el.getBoundingClientRect();
      setGizmoMode(m);
      w.setGizmoMode(m);
      w.beginDrag();
      w.gizmo.begin(lastNdc.current, w.camera, { w: r.width, h: r.height }, axis);
      stop();
    };
    // The tool keys Unity and Godot share: Q select, W move, E rotate — and R scale when the
    // mouse is laid out like Unity's. Otherwise R stays Blender's modal rotate.
    if (!e.ctrlKey && !e.metaKey && !e.altKey) {
      if (k === "q" || k === "Q") { pickTool("select"); return stop(); }
      if (k === "w" || k === "W") { pickTool("move"); return stop(); }
      if (k === "e" || k === "E") { pickTool("rotate"); return stop(); }
      if ((k === "r" || k === "R") && w.nav.scheme === "unity") { pickTool("scale"); return stop(); }
    }
    if (k === "g" || k === "G") return modal("move", "view");
    if (k === "r" || k === "R") return modal("rotate", "view");
    if (k === "s" || k === "S") return modal("scale", "all");

    if (k === "Home") { w.frameAll(); noteFramed(); return stop(); }
    // F frames the selection: Unity's and Godot's key, beside Blender's numpad period.
    if ((k === "f" || k === "F") && !e.ctrlKey && !e.metaKey && !e.altKey) { w.frameSelected(); return stop(); }
    if (k === "Delete" || k === "x" || k === "X") { deleteSelected(); return stop(); }
    if (k === "PageDown") { dropSelected(); return stop(); }
    if (e.shiftKey && (k === "d" || k === "D")) { duplicateSelected(); return stop(); }
    if (e.shiftKey && (k === "a" || k === "A")) { setPaletteOpen((v) => !v); return stop(); }
    if (k === "." && e.code === "NumpadDecimal") { w.frameSelected(); return stop(); }
    if (e.code === "Numpad1") { w.setView(e.ctrlKey ? "back" : "front"); return stop(); }
    if (e.code === "Numpad3") { w.setView(e.ctrlKey ? "left" : "right"); return stop(); }
    if (e.code === "Numpad7") { w.setView(e.ctrlKey ? "bottom" : "top"); return stop(); }
    if (e.code === "Numpad9") { w.nav.nudge(Math.PI, 0); return stop(); }
    if (e.code === "Numpad5") { w.nav.ortho = !w.nav.ortho; w.syncCamera(); bump(); return stop(); }
    if (e.code === "Numpad0") { w.lookThrough(); bump(); return stop(); }
    if (e.code === "Numpad4") { w.nav.nudge(0.2618, 0); return stop(); }
    if (e.code === "Numpad6") { w.nav.nudge(-0.2618, 0); return stop(); }
    if (e.code === "Numpad8") { w.nav.nudge(0, -0.2618); return stop(); }
    if (e.code === "Numpad2") { w.nav.nudge(0, 0.2618); return stop(); }
    if (e.code === "NumpadAdd") { w.nav.zoom(-120); return stop(); }
    if (e.code === "NumpadSubtract") { w.nav.zoom(120); return stop(); }

    if ((k === "a" || k === "A") && e.altKey) {
      if (mode === "edit") { w.selectElems([], false); bump(); } else w.deselectAll();
      return stop();
    }
    if (k === "a" || k === "A") {
      // Blender's A toggles: everything, then nothing. Same key, one level down.
      if (mode === "edit") { w.selectAllElems(); bump(); } else { w.selectAll(); setParts(w.parts()); }
      return stop();
    }
    if ((k === "h" || k === "H") && e.altKey) {
      push((ed) => {
        const parts = { ...ed.parts };
        for (const key of Object.keys(parts)) if (parts[key].hidden) { const c = { ...parts[key] }; delete c.hidden; parts[key] = c; }
        return { ...ed, parts };
      });
      for (const p of w.parts()) w.setVisible(p.key, true);
      setParts(w.parts());
      return stop();
    }
    if (k === "h" || k === "H") {
      for (const key of w.selection) { w.setVisible(key, false); }
      push((ed) => {
        const parts = { ...ed.parts };
        for (const key of w.selection) parts[key] = { ...(parts[key] || {}), hidden: true };
        return { ...ed, parts };
      });
      setParts(w.parts());
      return stop();
    }
    // Ctrl+Z BEFORE plain Z. It used to be tested after, so the shading cycle swallowed it and
    // the only undo in the editor was the button in the corner.
    if ((k === "z" || k === "Z") && (e.ctrlKey || e.metaKey)) { stepBack(); return stop(); }
    if ((k === "z" || k === "Z") && e.shiftKey) { setOverlays((o) => ({ ...o, wireframe: !o.wireframe })); return stop(); }
    if (k === "z" || k === "Z") {
      setShading((s) => (s === "solid" ? "material" : s === "material" ? "rendered" : s === "rendered" ? "wire" : "solid"));
      return stop();
    }
    if (k === "i" || k === "I") { insertKeyHere(); return stop(); }
    if (k === " ") { setPlaying((p) => !p); return stop(); }
    if (k === "Tab" && e.ctrlKey) { setMode((m) => (m === "object" ? "pose" : "object")); return stop(); }
    // Blender's Tab. Plain, because this is the mode a modeller is in and out of constantly.
    if (k === "Tab") { setMode((m) => (m === "object" ? "edit" : "object")); return stop(); }
    if (mode === "edit" && (k === "1" || k === "2" || k === "3")) {
      const kind = (k === "1" ? "vert" : k === "2" ? "edge" : "face") as ElemKind;
      setElem(kind); w.setElem(kind); bump();
      return stop();
    }
    // Esc drops what you can SEE selected: in edit mode that is the corners as well.
    if (k === "Escape") {
      if (mode === "edit") w.selectElems([], false);
      w.deselectAll();
      bump();
      return stop();
    }
  };

  const lastNdc = useRef({ x: 0, y: 0 });
  useEffect(() => {
    const el = canvasBox.current;
    if (!el) return;
    const track = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      lastNdc.current = { x: ((e.clientX - r.left) / r.width) * 2 - 1, y: -(((e.clientY - r.top) / r.height) * 2 - 1) };
    };
    el.addEventListener("pointermove", track);
    return () => el.removeEventListener("pointermove", track);
  }, []);

  // A flying key let go. Leaving the window mid-flight lands the camera: the key-up for a held W
  // would never arrive, and the view would fly on by itself.
  const onKeyUp = (e: React.KeyboardEvent) => {
    const w = world.current;
    if (w?.nav.flying && w.nav.flyKey(e.code, false)) e.preventDefault();
  };
  useEffect(() => {
    const land = () => {
      const w = world.current;
      if (!w?.nav.flying) return;
      w.flyEnd();
      if (box.current.mode === "fly") box.current.mode = "";
      try { if (document.pointerLockElement) document.exitPointerLock(); } catch { /* never locked */ }
    };
    // A lock the browser grants after a quick right-click has already let go would hide the cursor
    // with no flight to end it: give it straight back.
    const lockChanged = () => {
      if (document.pointerLockElement && box.current.mode !== "fly") {
        try { document.exitPointerLock(); } catch { /* already gone */ }
      }
    };
    window.addEventListener("blur", land);
    document.addEventListener("pointerlockchange", lockChanged);
    return () => {
      window.removeEventListener("blur", land);
      document.removeEventListener("pointerlockchange", lockChanged);
    };
  }, []);

  // ---- actions -------------------------------------------------------------------------
  // One GESTURE is one step of undo: the document as it was when a slider, a handle or a colour
  // picker was first touched is kept, and pushed when it is let go. This used to push the state
  // AFTER the change, so the first Ctrl+Z after moving a slider took back nothing at all.
  // A value equal to the file's own is dropped rather than stored (presets.ts `withParams`), the
  // rule this always followed — now with colours compared regardless of case.
  const setParam = (key: string, v: ParamValue) => {
    if (!gesture.current) gesture.current = JSON.parse(JSON.stringify(editsRef.current));
    setEdits((e) => ({ ...e, params: withParams(e.params, specsRef.current, { [key]: v }) }));
  };
  const commitParam = () => {
    const before = gesture.current;
    gesture.current = null;
    if (!before) return;
    undo.current.push(before);
    if (undo.current.length > Math.max(1, prefsRef.current.undo_max || UNDO_MAX)) undo.current.shift();
    bump();
    // A handle drag held still at its end has no rebuild after it to look again: this does.
    window.setTimeout(() => keepInViewRef.current(), 250);
  };
  const resetParam = (key: string) => {
    gesture.current = null;
    push((e) => { const p = { ...e.params }; delete p[key]; return { ...e, params: p }; });
  };
  /** Every parameter back to the file's value, as ONE step of undo. */
  const resetAllParams = () => {
    gesture.current = null;
    push((e) => ({ ...e, params: {} }));
  };
  /** A preset is one click and one step of undo. Only what the asset can take is applied; the
   *  values it does not name stay as they are (presets.ts `presetEntries` says why). */
  const applyPresetNow = (p: PresetSpec) => {
    gesture.current = null;
    if (!presetChanges(p, specs, values).length) return;
    const { values: patch } = presetEntries(p, specs);
    push((e) => ({ ...e, params: withParams(e.params, specs, patch) }));
  };
  /** DAY / NIGHT. The studio's lighting always; the asset's own `night` setting too, when it has
   *  one, so a building can light its windows when the sun goes down. */
  const chooseLook = (l: StudioLook) => {
    setLook(l);
    if (nightParam && !!values[NIGHT_PARAM] !== (l === "night")) { setParam(NIGHT_PARAM, l === "night"); commitParam(); }
  };
  const setParamRef = useRef(setParam);
  setParamRef.current = setParam;
  const commitParamRef = useRef(commitParam);
  commitParamRef.current = commitParam;
  /** What a drag handle reads and writes: the same values the sliders write, so the same 70 ms
   *  debounced rebuild runs, and the same commit, so a drag is one step of undo. */
  const handleCb = useMemo<ParamHandleCallbacks>(() => ({
    get: (key: string) => Number(valuesRef.current[key]),
    set: (key: string, v: number) => setParamRef.current(key, v),
    commit: () => commitParamRef.current(),
  }), []);

  /**
   * KEEP A GROWING MODEL IN VIEW — a configurator's only. The camera deliberately does not follow
   * a parameter change (a slider must not throw away a view), so a building given two more floors
   * grew straight out of the top of the viewport, the floors handle with it. When the model has
   * left the safe part of the frame (configModel `leavesFrame`), it is framed again — but only while
   * the camera is still exactly where the editor last framed it: a view the person chose is never
   * taken away from them. And never under a handle that is being dragged, whose drag is measured
   * through this camera; the gesture's end looks again.
   */
  const configurableRef = useRef(configurable);
  configurableRef.current = configurable;
  const keepInView = () => {
    const w = world.current;
    if (!w || !configurableRef.current || modeRef.current !== "object") return;
    if (!framedCam.current || framedCam.current !== camSig(w)) return;
    try { if (w.paramHandleInfo().some((h) => h.dragging)) return; } catch { /* no handle layer */ }
    const T = w.T;
    const box = new T.Box3().setFromObject(w.subject);
    if (box.isEmpty()) return;
    const cam = w.camera;
    cam.updateMatrixWorld(true);
    const ndc: Array<[number, number, number]> = [];
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const v = new T.Vector3(x, y, z).project(cam);
      ndc.push([v.x, v.y, v.z]);
    }
    if (!leavesFrame(ndc)) return;
    w.frameAll();
    noteFramed();
    bump();
  };
  const keepInViewRef = useRef(keepInView);
  keepInViewRef.current = keepInView;

  // A new asset opens on its options, in daylight.
  useEffect(() => { setSideTab("options"); setLook("day"); }, [source?.path, source?.code]);
  // The terrain tools live in the Scene tab: entering Terrain mode shows them.
  useEffect(() => { if (mode === "terrain") setSideTab("scene"); }, [mode]);
  // DAY / NIGHT onto the world — and onto every NEW world, which is born in daylight.
  useEffect(() => {
    const w = world.current;
    if (w) applyStudioLook(w.scene, lookNow, studioRef.current);
  }, [lookNow, phase]);
  // The arrows, when they are on, the asset has any, it built, and a click means "select": in
  // edit, pose and terrain modes a click already means something else.
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    const on = handlesOn && handleSpecs.length > 0 && phase === "ready" && mode === "object";
    try { w.setParamHandles(on ? handleSpecs : null, on ? handleCb : undefined); }
    catch (e) { console.warn("[configurator] setParamHandles failed", e); }
  }, [handlesOn, handleSpecs, phase, mode, handleCb]);
  // A thumbnail module belongs to the source it was imported from.
  useEffect(() => () => {
    for (const m of thumbModules.current.values()) { try { m.dispose(); } catch { /* already gone */ } }
    thumbModules.current.clear();
  }, [source?.path, source?.code]);

  /** One preset's object, for its thumbnail: built by the asset's own function with the viewport's
   *  own THREE, into a holder of its own — the live viewport is never touched. A preset that moves
   *  a DETECTED constant needs the module imported with that constant written in, exactly as
   *  `rebuild` does; those modules are kept, a few at a time, for the next thumbnail. */
  const buildThumb = useCallback(async (vals: Record<string, ParamValue>): Promise<any> => {
    const w = world.current, src = srcRef.current;
    if (!w || !src || !fileAsset.current) throw new Error("the asset is not open");
    const sp = specsRef.current;
    const sig = spanSig(sp, vals);
    let asset: FileAsset = fileAsset.current;
    if (sig !== importedSig.current) {
      let m = thumbModules.current.get(sig);
      if (!m) {
        m = await loadFileAsset(applyParams(src.code, vals, sp).source, engineUrl.current, relResolver(src));
        thumbModules.current.set(sig, m);
        while (thumbModules.current.size > 4) {
          const oldest = thumbModules.current.keys().next().value as string;
          thumbModules.current.get(oldest)?.dispose();
          thumbModules.current.delete(oldest);
        }
      }
      asset = m;
    }
    const T = w.T;
    const holder = new T.Group();
    holder.name = "preset-thumbnail";
    const add = (thing: any) => {
      for (const t of Array.isArray(thing) ? thing : [thing]) if (t?.isObject3D && t !== holder) holder.add(t);
      return thing;
    };
    // The run context every build gets, with everything that would reach the live scene replaced.
    const ctx = { ...w.ctx(vals), root: holder, scene: new T.Scene(), camera: new T.PerspectiveCamera(),
      add, clear: () => holder.clear(), forge: undefined, params: vals };
    const out = await asset.build(T, vals, ctx);
    for (const o of Array.isArray(out) ? out : [out]) if (o?.isObject3D && o !== holder && !o.parent) holder.add(o);
    return holder;
  }, []);

  const thumbJobs = useMemo(() => presets.map((p) => ({ name: p.name, values: presetBuildValues(p, specs) })), [presets, specs]);
  // Hashed once per source, not once per render: the editor renders on every orbit.
  const thumbSig = useMemo(() => (source?.kind === "file" && source.path ? source.path + ":" + hashString(source.code || "") : ""),
    [source?.kind, source?.path, source?.code]);
  const thumbsOff = !presets.length ? ""
    : source?.kind !== "file" ? "No thumbnails: this asset was not opened from its own file."
    : pcStudio.current ? "No thumbnails for a PlayCanvas asset: the viewport can only photograph three.js." : "";
  const thumbs = usePresetThumbs({
    enabled: presets.length > 0 && !!thumbSig && !thumbsOff,
    sig: thumbSig,
    jobs: thumbJobs,
    build: buildThumb,
    shoot: (obj) => {
      const w = world.current;
      if (!w) throw new Error("the viewport is gone");
      return shootThumb(w.T, w.ctx({}).renderer, obj, { env: studioEnv.current, redraw: () => w.renderSafe() });
    },
    release: (obj) => releaseThumb(obj, liveResources(world.current?.subject)),
    // Never while the viewport is busy: a rebuild in flight, a value moved in the last beat, or
    // anything being dragged. The thumbnails are the least urgent thing on the page.
    busy: () => {
      const w = world.current;
      const ph = phaseRef.current;
      return rebuilding.current > 0 || (ph !== "ready" && ph !== "error") || performance.now() - lastValuesAt.current < 700
        || !!w?.gizmo.dragging || !!w?.nav.dragging || !!w?.nav.flying || box.current.dragging;
    },
  });

  const addBone = () => {
    const w = world.current;
    if (!w || !w.active) return;
    const obj = w.objectOf(w.active);
    const spec = boneFromObject(w.T, obj, uniqueBoneName(edits.bones, obj?.name || "bone"));
    if (!spec) return;
    const bones = [...edits.bones, spec];
    push((e) => ({ ...e, bones }));
    w.buildRigFrom(bones, "parts", edits.pose);
    setParts(w.parts());
  };
  const removeBone = (name: string) => {
    const bones = edits.bones.filter((b) => b.name !== name);
    const pose = { ...edits.pose };
    delete pose[name];
    push((e) => ({ ...e, bones, pose }));
    world.current?.buildRigFrom(bones, world.current.rig?.mode || "parts", pose);
  };
  const rebind = (m: "parts" | "skin", method: "envelope" | "heat" = weights) => {
    world.current?.buildRigFrom(edits.bones, m, edits.pose, method);
    setParts(world.current?.parts() || []);
    bump();
  };
  const changeWeights = (wm: "envelope" | "heat") => {
    setWeights(wm);
    if (world.current?.rig?.mode === "skin") rebind("skin", wm);
  };

  // ---- baking ---------------------------------------------------------------------------
  const runBake = (kind: BakeKind, part = active, size = bakeSize) => {
    const w = world.current;
    if (!w || !part) return;
    setBaking(kind);
    setBakeNote("");
    // A beat later, so the button can say "baking…" before the main thread goes quiet.
    window.setTimeout(() => {
      try {
        const rays = prefsRef.current.bake_rays || 24;
        const baked = w.bakePart(part, kind, size, rays);
        if (!baked) { setBakeNote("no mesh under " + part); return; }
        setBakePreviews((p) => ({ ...p, [part + ":" + kind]: bakedToUrl(baked) }));
        push((e) => ({ ...e, bakes: [...(e.bakes || []).filter((b) => !(b.part === part && b.kind === kind)), { part, kind, size, rays }] }));
      } catch (e: any) {
        setBakeNote(String(e?.message || e).slice(0, 160));
      } finally {
        setBaking("");
      }
    }, 30);
  };
  // The recipe, not the pixels, is what the sidecar keeps: after a rebuild the maps are baked
  // again, once the parameter sliders have settled.
  const rebakeTimer = useRef(0);
  useEffect(() => {
    window.clearTimeout(rebakeTimer.current);
    const bakes = edits.bakes;
    if (phase !== "ready" || !bakes?.length) return;
    rebakeTimer.current = window.setTimeout(() => {
      const w = world.current;
      if (!w) return;
      for (const b of bakes) {
        try {
          const baked = w.bakePart(b.part, b.kind, b.size, b.rays || 24);
          if (baked) setBakePreviews((p) => ({ ...p, [b.part + ":" + b.kind]: bakedToUrl(baked) }));
        } catch { /* a manual bake shows the reason */ }
      }
    }, prefsRef.current.rebake_ms);
    return () => window.clearTimeout(rebakeTimer.current);
  }, [phase, JSON.stringify(edits.bakes), stats?.triangles]);   // eslint-disable-line react-hooks/exhaustive-deps
  const clearPose = () => {
    push((e) => ({ ...e, pose: {} }));
    world.current?.buildRigFrom(edits.bones, world.current.rig?.mode || "parts", {});
  };

  const MOD_ARGS: Record<Mod["op"], Record<string, number>> = {
    skin: { tol: 0.001, angle: 40, levels: 0 },
    weld: { tol: 0.001 },
    smooth: { angle: 40 },
    subdivide: { levels: 1 },
    mirror: { axis: 0 },
    solidify: { thickness: 0.05 },
    displace: { amp: 0.05, freq: 3, octaves: 3, seed: 1 },
    simplify: { cell: 0.05 },
    flip: {},
    // Catmull-Clark: `crease` is the angle above which an edge stays sharp (0 = none), `sharp`
    // for how many levels (99 = always). Bevel width is in world units.
    subsurf: { levels: 1, crease: 0, sharp: 99 },
    bevel: { width: 0.05, segments: 3, angle: 30 },
    unwrap: { angle: 66, margin: 0.02 },
    remesh: { res: 48 },
    relax: { iterations: 5 },
  };
  const addMod = (op: Mod["op"]) => {
    // `skin` merges the whole asset by nature, so it never takes a part as its target.
    const target = op === "skin" ? "" : active;
    push((e) => ({ ...e, mods: [...e.mods, { op, target, args: { ...MOD_ARGS[op] } }] }));
  };
  const removeMod = (i: number) => push((e) => ({ ...e, mods: e.mods.filter((_, k) => k !== i) }));
  const toggleMod = (i: number) => push((e) => ({
    ...e, mods: e.mods.map((m, k) => (k === i ? { ...m, off: !m.off } : m)),
  }));
  const setModArg = (i: number, key: string, v: number) => setEdits((e) => ({
    ...e, mods: e.mods.map((m, k) => (k === i ? { ...m, args: { ...m.args, [key]: v } } : m)),
  }));

  const ensureClip = (): Clip => {
    if (clip) return clip;
    const c: Clip = { name: "action", fps: 24, start: 0, end: 48, loop: true, tracks: [] };
    push((e) => ({ ...e, clips: [...e.clips, c] }));
    setShowTimeline(true);
    return c;
  };

  const insertKeyHere = () => {
    const w = world.current;
    if (!w) return;
    const c = ensureClip();
    const next: Clip = JSON.parse(JSON.stringify(c));
    if (mode === "pose" && w.activeBone) {
      const pose = w.posedNow()[w.activeBone] || [0, 0, 0];
      for (const ax of [0, 1, 2] as const) insertKey(next, w.activeBone, "rot", ax, frame, pose[ax]);
    } else {
      for (const key of w.selection) {
        const o = w.objectOf(key);
        if (!o) continue;
        for (const ax of [0, 1, 2] as const) {
          insertKey(next, key, "pos", ax, frame, [o.position.x, o.position.y, o.position.z][ax]);
          insertKey(next, key, "rot", ax, frame, [o.rotation.x, o.rotation.y, o.rotation.z][ax]);
        }
      }
    }
    push((e) => ({ ...e, clips: e.clips.map((x, i) => (i === clipIdx ? next : x)) }));
    if (w) w.clip = next;
    setShowTimeline(true);
  };

  const deleteKeyHere = () => {
    if (!clip) return;
    const next: Clip = JSON.parse(JSON.stringify(clip));
    const target = mode === "pose" ? active || world.current?.activeBone || "" : active;
    removeKeysAt(next, target, frame);
    push((e) => ({ ...e, clips: e.clips.map((x, i) => (i === clipIdx ? next : x)) }));
    if (world.current) world.current.clip = next;
  };

  useEffect(() => { if (world.current) world.current.clip = clip; }, [clip]);

  const gotoFrame = (f: number) => {
    setFrame(f);
    world.current?.setFrame(f, edits.pose);
  };

  // ---- placing, duplicating and removing ------------------------------------------------
  //
  // This is the half of an editor that a viewer is not, and the reason it did not exist before is
  // that a procedural game has nowhere to put the answer: the world is a function, it runs again
  // on the next reload, and anything added to the scene is gone. So a placement lives in the
  // sidecar beside "move this part, hide that one", and is put back after the code has built.
  const [placeNote, setPlaceNote] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);

  /** A project-relative path, as a URL this window can actually fetch: through the game's own dev
   *  server when there is one, and through the Studio's file proxy when there is not. */
  const projUrl = useCallback((rel: string) => {
    if (!source) return "";
    const root = (source.project || "").replace(/\\/g, "/").replace(/\/+$/, "");
    const dev = (source.devUrl || "").replace(/\/+$/, "");
    const clean = String(rel || "").replace(/\\/g, "/").replace(/^\/+/, "");
    return dev ? dev + "/" + clean : api.wsRaw(root + "/" + clean, source.project);
  }, [source]);

  const resolveRef = useCallback(async (ref: PlacedRef): Promise<any> => {
    const w = world.current;
    if (!w) return null;
    if (ref.kind === "code" && ref.file) {
      // The game's OWN builder, called in the editor's scene. Not a copy of it — the module the
      // game loads, so what you place is what will ship. Which server has that module, which
      // entry in it this is, which function builds it and what that function needs handed to it
      // are all one question, answered in one place: see assetOpen.ts.
      const got = await w.openRef({
        type: ref.spec ? "spec" : "code",
        file: ref.file, export: ref.export, key: ref.key || "",
        table: ref.table || "", index: ref.index ?? -1,
        root: ref.root || "", deps: ref.deps || [],
      });
      if (got.object) return got.object;
      throw new Error(got.tried.slice(0, 3).join("; ") || `${ref.export || "default"} built nothing`);
    }
    if (ref.kind === "model" && ref.url) {
      // Through the Studio, not through the game's own server: the file comes back with its
      // compression already taken out, and the loader comes back bound to this three. Neither
      // needs anything of the game, which is the point — a Draco model in a PlayCanvas project
      // used to be unopenable here, and it is a file like any other.
      const obj = await w.loadGLB(api.engineModelUrl(source?.project || "", ref.url));
      // NOTHING TO SEE IS AN ANSWER, NOT A PLACEMENT. A clip file loads as bones and nothing
      // else; placed, it was an empty group that the note called "placed".
      let drawn = 0;
      obj?.traverse?.((c: any) => { if (c.isMesh || c.isPoints || c.isLine || c.isSprite) drawn++; });
      if (obj && !drawn) throw new Error("that file holds no mesh (an animation clip, or an empty scene) — place the model it belongs to");
      if (!obj) return obj;
      // THE GAME'S VERSION, WHEN THE MIRROR HAS ONE: a copy of the one the game draws, or the file
      // painted like the rest of its folder — not a grey figure of every variant at its own size.
      const live = source?.kind === "live" && liveInfo.current.engine === "playcanvas";
      const d = dressForGame(w.T, live ? w.keyRoot() : null, ref.url, obj, ref.nodes || [], ref.parts || []);
      if (d.note) d.object.userData.studioDressNote = d.note;
      return d.object;
    }
    return null;
  }, [projUrl]);

  resolveRefRef.current = resolveRef;

  // ---- placing INTO a running game ------------------------------------------------------
  //
  // In a PlayCanvas game the Studio is editing live, a placement goes into the game as well as the
  // mirror: the model the editor already loaded is sent as arrays (threeToPcPlace) through a file,
  // and the game builds it with its own classes (pcPlace). The same id replaces an earlier copy,
  // so sending one again is harmless — which is what keeps the game in step after a refresh.
  const livePlace = useCallback(async (id: string): Promise<string> => {
    const w = world.current;
    if (!w || source?.kind !== "live" || liveInfo.current.engine !== "playcanvas") return "";
    const obj = w.placedObject(id);
    if (!obj) return "not in the editor";
    const root = source.project.replace(/\\/g, "/").replace(/\/+$/, "");
    const path = root + "/.studio/place-" + id + "-" + Date.now().toString(36) + ".json";
    try {
      const spec = threeToPcPlace(w.T, obj, { id, name: obj.name || id });
      try { await api.wsMkdir(root + "/.studio"); } catch { /* there already */ }
      await api.wsWrite(path, JSON.stringify(spec));
      const url = window.location.origin + api.wsRaw(path);
      const js = "(async () => { const m = await import(" + JSON.stringify(window.location.origin + "/forge-ops.js") + " + '?v=' + Date.now());"
        + " const app = " + PC_APP_JS(liveInfo.current.at) + "; if (!app) return { ok: false, error: 'no PlayCanvas app' };"
        + " const spec = await (await fetch(" + JSON.stringify(url) + ")).json(); return await m.pcPlace(app, spec); })()";
      const r = await api.liveEval(source.project, js);
      const v = r?.value;
      if (!r?.ok || !v?.ok) return (r?.error || v?.error || "no answer from the game");
      return "";
    } catch (e: any) {
      return String(e?.message || e).slice(0, 160);
    } finally {
      api.wsDelete(path).catch(() => { /* a leftover file, nothing worse */ });
    }
  }, [source]);

  const liveUnplace = useCallback((ids: string[]) => {
    if (!ids.length || source?.kind !== "live" || liveInfo.current.engine !== "playcanvas") return;
    const js = "(async () => { const m = await import(" + JSON.stringify(window.location.origin + "/forge-ops.js") + " + '?v=' + Date.now());"
      + " const app = " + PC_APP_JS(liveInfo.current.at) + "; if (!app) return 0;"
      + " let n = 0; for (const id of " + JSON.stringify(ids) + ") n += m.pcUnplace(app, id); return n; })()";
    api.liveEval(source.project, js).catch(() => { /* the game tab went away; the sidecar is right */ });
  }, [source]);

  /** Every placement in the document, into the game. After a refresh the game may have lost them
   *  (a reloaded tab) or still hold them; either way it ends with exactly one of each. */
  const liveSyncPlaced = useCallback(async (placed: PlacedItem[] | undefined) => {
    if (!placed?.length || source?.kind !== "live" || liveInfo.current.engine !== "playcanvas") return;
    let failed = "";
    for (const it of placed) { const err = await livePlace(it.id); if (err && !failed) failed = it.name + ": " + err; }
    if (failed) setLiveNote("a placed model did not reach the game — " + failed);
  }, [source, livePlace]);
  const liveSyncRef = useRef(liveSyncPlaced);
  liveSyncRef.current = liveSyncPlaced;

  const placeRef = useCallback(async (ref: PlacedRef, name: string) => {
    const w = world.current;
    if (!w) return;
    const id = "p" + Math.random().toString(36).slice(2, 9);
    // A name of its own. It is the key the game finds the placement by, so two of one model must
    // not share one — "Labubu_idle", then "Labubu_idle 2".
    const taken = new Set(w.parts().map((p) => p.name));
    let unique = name, n = 2;
    while (taken.has(unique)) unique = name + " " + n++;
    const item: PlacedItem = { id, name: unique, ref, pos: w.dropPoint(), rot: [0, 0, 0], scale: [1, 1, 1] };
    const r = await w.applyPlacedDoc([item], resolveRef);
    if (!r.added) { setPlaceNote(r.errors[0] || "could not place that"); return; }
    setPlaceNote("");
    push((e) => ({ ...e, placed: [...(e.placed || []), item] }));
    setParts(w.parts());
    setStats(w.stats());
    const note: string = w.placedObject(id)?.userData?.studioDressNote || "";
    const how = note ? note[0].toUpperCase() + note.slice(1) + "." : "";
    if (source?.kind === "live" && liveInfo.current.engine === "playcanvas") {
      const err = await livePlace(id);
      setPlaceNote(err ? "placed here, but the game did not take it: " + err
        : "placed — in the editor and in the running game." + (how ? " " + how : ""));
    } else if (how) setPlaceNote("placed. " + how);
  }, [resolveRef, push, source, livePlace]);

  /** Delete means delete for something the editor put there, and HIDE for something the game's
   *  own code built — because the code will build it again next run, and a sidecar that claimed
   *  otherwise would be lying to you every reload. */
  const deleteSelected = useCallback(() => {
    const w = world.current;
    // The viewport's own selection, not the copy React last rendered: a key pressed in the same
    // tick as the click that selected must delete what that click selected.
    const sel: string[] = w ? [...w.selection] : [];
    if (!w || !sel.length) return;
    const gone: string[] = [];
    const hid: string[] = [];
    for (const k of sel) {
      const o = w.objectOf(k);
      const pid = o ? placedIdOf(o) : "";
      const r = w.removeKey(k);
      if (r.removed && pid) gone.push(pid);
      else if (r.hidden) hid.push(k);
    }
    if (!gone.length && !hid.length) return;
    push((e) => {
      const parts = { ...e.parts };
      for (const k of hid) parts[k] = { ...(parts[k] || {}), hidden: true };
      return { ...e, parts, placed: (e.placed || []).filter((x) => !gone.includes(x.id)) };
    });
    setPlaceNote(hid.length ? `${hid.length} hidden — the game builds those, so they cannot be removed, only hidden`
                            : `${gone.length} removed`);
    setParts(w.parts());
    liveUnplace(gone);
  }, [push, liveUnplace]);

  /** Godot's "Snap Object to Floor": each selected object down onto the surface under it. The
   *  answer shows in the readout chip for two seconds, unless a drag has taken the chip since. */
  const dropSelected = useCallback(() => {
    const w = world.current;
    if (!w || !selection.length) return;
    const n = w.dropToFloor();
    const note = n ? (n === 1 ? "put down on the ground" : n + " put down on the ground") : "nothing under it to stand on";
    setReadout(note);
    window.setTimeout(() => { if (readoutRef.current === note) setReadout(""); }, 2000);
    if (n) setParts(w.parts());
  }, [selection, setReadout]);

  const duplicateSelected = useCallback(() => {
    const w = world.current;
    if (!w || !active) return;
    const src = w.objectOf(active);
    const srcId = src ? placedIdOf(src) : "";
    const origin = srcId ? (edits.placed || []).find((x) => x.id === srcId) : null;
    const made = w.duplicateKey(active);
    if (!made) return;
    const o = made.object;
    const item: PlacedItem = {
      id: String(o.userData.studioPlaced), name: o.name || "copy",
      // A copy of something the editor placed repeats its recipe. A copy of something the GAME
      // built cannot — there is no recipe here — so it is stored as "another one of that", and
      // the applier clones it after the game has built the original.
      ref: origin ? origin.ref : { kind: "clone", of: active },
      pos: [o.position.x, o.position.y, o.position.z],
      rot: [o.rotation.x, o.rotation.y, o.rotation.z],
      scale: [o.scale.x, o.scale.y, o.scale.z],
    };
    push((e) => ({ ...e, placed: [...(e.placed || []), item] }));
    setPlaceNote("");
    setParts(w.parts());
    setStats(w.stats());
    if (source?.kind === "live") void livePlace(item.id).then((err) => { if (err) setPlaceNote("copied here, but the game did not take it: " + err); });
  }, [active, edits.placed, push, source, livePlace]);

  // ---- saving --------------------------------------------------------------------------
  // ---- paint actions ---------------------------------------------------------------------
  const strokeOpts = (): StrokeOptions => ({ tool: paintTool, color: paintColor, brush: brushes[paintTool], projection, frontOnly, mirror });
  const chooseColor = (hex: string) => {
    setPaintColor(hex);
    try { localStorage.setItem("paint.color", hex); } catch { /* private mode */ }
  };
  const swapColors = () => {
    const a = paintColor, b2 = paintColor2;
    chooseColor(b2);
    setPaintColor2(a);
    try { localStorage.setItem("paint.color2", a); } catch { /* private mode */ }
  };
  const setBrush = (patch: Partial<BrushSettings>) => {
    setBrushes((all) => {
      const next = { ...all, [paintTool]: { ...all[paintTool], ...patch } };
      try { localStorage.setItem("paint.brushes", JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
  };
  const applyPreset = (pr: PaintPreset) => { setPaintTool(pr.tool); setBrushes((all) => {
    const next = { ...all, [pr.tool]: { ...all[pr.tool], ...pr.brush } };
    try { localStorage.setItem("paint.brushes", JSON.stringify(next)); } catch { /* private mode */ }
    return next;
  }); };
  const paintUndo = () => { const l = world.current?.paint?.undo(); if (l) { setPaintDirty(true); setPaintNote("undid the " + l); } };
  const paintRedo = () => { const l = world.current?.paint?.redo(); if (l) { setPaintDirty(true); setPaintNote("redid the " + l); } };
  const applyPaintKey = (a: PaintKeyAction) => {
    const cur = brushes[paintTool];
    switch (a.kind) {
      case "tool": setPaintTool(a.tool); break;
      case "size": setBrush({ size: clamp(Math.round(cur.size * a.factor) || 1, 1, 400) }); break;
      case "hardness": setBrush({ hardness: clamp(Math.round((cur.hardness + a.delta) * 100) / 100, 0, 1) }); break;
      case "opacity":
        if (paintTool === "smudge" || paintTool === "blur" || paintTool === "clone") setBrush({ strength: a.value });
        else setBrush({ opacity: a.value });
        break;
      case "flow": setBrush({ flow: a.value }); break;
      case "swap": swapColors(); break;
      case "defaults": chooseColor("#000000"); setPaintColor2("#ffffff"); break;
      case "mirror": setMirror((m) => (m ? 0 : 1)); break;
      case "undo": paintUndo(); break;
      case "redo": paintRedo(); break;
      case "leave": setMode("object"); break;
    }
  };
  const layerActions: LayerActions = {
    add: () => { world.current?.ensurePaint().addLayer(); setPaintDirty(true); },
    remove: (id) => { world.current?.paint?.removeLayer(id); setPaintDirty(true); },
    clear: (id) => { world.current?.paint?.clearLayer(id); setPaintDirty(true); },
    merge: (id) => { world.current?.paint?.mergeDown(id); setPaintDirty(true); },
    move: (id, dir) => { world.current?.paint?.moveLayer(id, dir); setPaintDirty(true); },
    set: (id, props) => { world.current?.paint?.setLayer(id, props); setPaintDirty(true); },
    pick: (id) => { world.current?.paint?.setActive(id); },
  };
  const makeTexture = (key: string) => {
    const eng = world.current?.ensurePaint();
    if (!eng) return;
    const why = eng.makeTexture(key);
    setPaintNote(why || "texture made for " + key + " — paint away");
    if (!why) setPaintDirty(true);
  };
  /** The model with its painted textures as a .glb beside the asset, for Godot, Unity, Unreal
   *  or Blender. The asset's own nodes go out (not the editor's holder), with any animation clips. */
  const exportGlb = async () => {
    const w0 = world.current;
    if (!w0 || !source) return;
    setExporting(true);
    setExportNote("");
    try {
      const loader = w0.loaderUrl || api.engineLoaderUrl(source.project || "");
      const url = loader.includes("name=") ? loader.replace(/name=[^&]*/, "name=GLTFExporter.js") : loader + (loader.includes("?") ? "&" : "?") + "name=GLTFExporter.js";
      const mod: any = await import(/* @vite-ignore */ url);
      const Exporter = mod.GLTFExporter || mod.default;
      if (!Exporter) throw new Error("the exporter did not load from " + url);
      const clips: any[] = [];
      w0.subject.traverse((o: any) => { if (Array.isArray(o.animations)) for (const c of o.animations) if (c && !clips.includes(c)) clips.push(c); });
      const restore = w0.paint ? w0.paint.exportSwap() : () => {};
      let buf: ArrayBuffer;
      try {
        const kids = w0.subject.children.slice();
        buf = await new Promise<ArrayBuffer>((res, rej) => new Exporter().parse(kids.length === 1 ? kids[0] : kids, res, rej, { binary: true, animations: clips }));
      } finally {
        restore();
      }
      const dir = source.path ? dirOf(source.path) : (source.project || "");
      const base = (source.path ? String(source.path.split(/[\\/]/).pop()) : (source.label || "asset")).replace(/\.(glb|gltf|m?[jt]sx?)$/i, "");
      const name = base + ".painted.glb";
      if (lastExport.current) await api.wsDelete(lastExport.current).catch(() => { /* already gone */ });
      const fd = new FormData();
      fd.append("dest_dir", dir);
      fd.append("file", new Blob([buf], { type: "model/gltf-binary" }), name);
      const res = await fetch("/api/workspace/upload-file", { method: "POST", body: fd });
      const j = await res.json().catch(() => ({}));
      if (!res.ok || !j?.path) throw new Error(j?.detail || "the file could not be written");
      lastExport.current = j.path;
      setExportNote("exported " + j.path + " (" + Math.round(buf.byteLength / 1024) + " KB" + (clips.length ? ", " + clips.length + " animation clip" + (clips.length === 1 ? "" : "s") : "") + ")");
    } catch (e: any) {
      setExportNote("export failed: " + String(e?.message || e).slice(0, 220));
    } finally {
      setExporting(false);
    }
  };

  const save = async () => {
    if (!source || !canWrite) return;
    setSaving(true);
    setSaveNote("");
    try {
      const notes: string[] = [];
      // Parameters go back into the source as one changed number each; overrides go beside it.
      let written: string[] = [];
      if (Object.keys(edits.params).length) {
        const file = await api.wsFile(source.path);
        const r = applyParams(file.text || "", edits.params, specs);
        if (r.changed.length) {
          await api.wsWrite(source.path, r.source);
          written = r.changed;
          notes.push(r.changed.length + " parameter" + (r.changed.length === 1 ? "" : "s") + " written to the file");
        }
        if (r.skipped.length) {
          notes.push(r.skipped.length + " skipped — the file changed under the editor; reopen it");
        }
      }
      // WHAT THE SOURCE CANNOT TAKE STAYS IN THE SIDECAR. A DECLARED parameter — every setting in
      // a manifest, so every preset and every choice — has no literal in the text to rewrite: it
      // reaches the build as an argument, and the sidecar is its only home. This used to write the
      // sidecar only for edits OTHER than parameters, so a preset applied and saved was silently
      // gone on the next open. A parameter the file did take is left out: it is in the file now.
      const kept: Record<string, ParamValue> = {};
      for (const [k, v] of Object.entries(edits.params)) if (!written.includes(k)) kept[k] = v;
      const assetId = source.kind === "live" ? source.project : source.path;
      const doc: Edits = { ...edits, params: kept, asset: assetId, updated: Date.now() / 1000 };
      const body = countEdits(doc) > 0 || doc.clips.length ? JSON.stringify(doc, null, 2) : "";
      if (body) {
        await api.wsWrite(sidecarPathOf(source), body);
        sidecarOnDisk.current = true;
        notes.push(source.kind === "live"
          ? "saved as studio.edits.json at the game's root — it applies them with applyEdits(scene, edits) from forge-ops.js"
          : "edits saved beside the asset");
      } else if (sidecarOnDisk.current) {
        // Everything was put back. The sidecar is emptied, not left holding what "Reset all" took
        // away — or the next open would bring it all back. (Emptied, never deleted: it is a file
        // the person may keep in version control.)
        await api.wsWrite(sidecarPathOf(source), JSON.stringify({ ...emptyEdits(assetId), updated: Date.now() / 1000 }, null, 2));
        sidecarOnDisk.current = false;
        notes.push("the edits beside the asset were cleared");
      }
      // The ground gets a file of its own beside the edits. See terrainTool for the two numbers
      // that decided that: a 513-sample field base64s to 1.4 MB, and every undo push in this
      // editor deep-copies the edit document with JSON.
      const ground = terrain.current;
      if (ground && terrainDirty) {
        const body = packTerrain(terrainWrite(ground), usedPalette(ground.scatter, paletteRef.current));
        await api.wsWrite(terrainSidecarPath(sidecarPathOf(source)), body);
        setTerrainDirty(false);
        notes.push("terrain saved beside the asset (" + Math.round(body.length / 1024) + " KB)");
      }
      // Paint gets a file of its own too, for the same reason: layers are PNGs, megabytes of them.
      const eng = world.current?.paint;
      if (eng && paintDirty) {
        const doc = await eng.toDoc(assetId);
        const pbody = JSON.stringify(doc);
        await api.wsWrite(paintDocPath(sidecarPathOf(source)), pbody);
        setPaintDirty(false);
        notes.push("paint saved beside the asset (" + Math.round(pbody.length / 1024) + " KB)");
      }
      setSaved(JSON.stringify(edits));
      setSaveNote(notes.join(" · ") || "nothing to write");
    } catch (e: any) {
      setSaveNote("could not save: " + String(e?.message || e).slice(0, 120));
    } finally {
      setSaving(false);
      window.setTimeout(() => setSaveNote(""), 6000);
    }
  };

  // ---- derived -------------------------------------------------------------------------
  const w = world.current;
  const activeObj = w && active ? w.objectOf(active) : null;
  const activeBone = w?.activeBone || "";
  const extra = w && active ? w.extraOf(active) : null;
  const cameraKeys = w ? w.cameras() : [];
  const unityMouse = P.nav_scheme === "unity";
  const selSet = useMemo(() => new Set(selection), [selection]);
  const boneSel = w ? w.boneSelection : new Set<string>();
  const smallParts = parts.filter((p) => p.px > 0 && p.px < 24).length;

  const pickTool = (m: GizmoMode | "select") => {
    if (m === "select") { setGizmoOn(false); return; }
    setGizmoOn(true);
    setGizmoMode(m);
  };
  const tools: Tool[] = [
    { id: "select", icon: <MousePointer2 size={14} />, label: "Select — click a part, drag a box", key: "Q · click",
      active: !gizmoOn, onClick: () => pickTool("select") },
    { id: "move", icon: <Move size={14} />, label: "Move", key: "W · G", active: gizmoOn && gizmoMode === "move", onClick: () => pickTool("move") },
    { id: "rotate", icon: <RotateCw size={14} />, label: "Rotate", key: unityMouse ? "E" : "E · R", active: gizmoOn && gizmoMode === "rotate", onClick: () => pickTool("rotate") },
    { id: "scale", icon: <Scaling size={14} />, label: "Scale", key: unityMouse ? "R · S" : "S", active: gizmoOn && gizmoMode === "scale", onClick: () => pickTool("scale") },
    { id: "add", icon: <Plus size={14} />, label: "Add — a shape, a light, or one of this project's own assets",
      key: "Shift+A", sep: true, active: paletteOpen, onClick: () => setPaletteOpen((v) => !v) },
    { id: "dup", icon: <CopyPlus size={14} />, label: "Duplicate the selection", key: "Shift+D",
      disabled: !active, onClick: duplicateSelected },
    { id: "del", icon: <Trash2 size={14} />, label: "Remove — or hide, when the game's code made it",
      key: "X", disabled: !selection.length, onClick: deleteSelected },
    { id: "frameSel", icon: <Focus size={14} />, label: "Frame the selection", key: "F · numpad .", sep: true,
      disabled: !selection.length, onClick: () => w?.frameSelected() },
    { id: "drop", icon: <ArrowDownToLine size={14} />, label: "Drop onto the ground — the selection comes down onto the surface under it",
      key: "PgDn", disabled: !selection.length, onClick: dropSelected },
    { id: "frameAll", icon: <Maximize2 size={14} />, label: "Frame everything", key: "Home", onClick: () => { w?.frameAll(); noteFramed(); } },
    { id: "xray", icon: overlays.xray ? <EyeOff size={14} /> : <Eye size={14} />, sep: true,
      label: "X-ray — see through the surface", active: overlays.xray,
      onClick: () => setOverlays((o) => ({ ...o, xray: !o.xray })) },
    { id: "outline", icon: <BoxSelect size={14} />,
      label: overlays.outline
        ? "Selection outline is on — click to hide the orange line. The selection stays; Esc drops it."
        : "Selection outline is off — click to show the orange line round what is selected",
      active: overlays.outline, onClick: () => setOutline(!overlays.outline) },
    { id: "wire", icon: <Grid3x3 size={14} />, label: "Wireframe over the surface", key: "Shift+Z",
      active: overlays.wireframe, onClick: () => setOverlays((o) => ({ ...o, wireframe: !o.wireframe })) },
    { id: "faces", icon: <Shapes size={14} />, label: "Face orientation — blue is front, red is back",
      active: overlays.faces, onClick: () => setOverlays((o) => ({ ...o, faces: !o.faces })) },
    { id: "sizes", icon: <Ruler size={14} />, label: "Sizes on screen — what is too small to judge",
      active: overlays.sizes, onClick: () => setOverlays((o) => ({ ...o, sizes: !o.sizes })) },
    { id: "bone", icon: <BoneIcon size={14} />, label: "Bone from the selected part", sep: true,
      disabled: !active, onClick: addBone },
    { id: "pose", icon: <PersonStanding size={14} />, label: "Pose mode", key: "Ctrl+Tab",
      active: mode === "pose", disabled: !edits.bones.length,
      onClick: () => setMode(mode === "pose" ? "object" : "pose") },
    { id: "ik", icon: <Link2 size={14} />, label: "Inverse kinematics — move a bone's tip and the chain above it follows (reach in the Rig panel)",
      active: ikDepth > 0, disabled: mode !== "pose",
      onClick: () => { setIkDepth(ikDepth > 0 ? 0 : 2); setGizmoMode("move"); setGizmoOn(true); } },
    { id: "key", icon: <Diamond size={14} />, label: "Insert a keyframe here", key: "I",
      disabled: !active && !activeBone, onClick: insertKeyHere },
    ...(P.terrain ? [{ id: "terrain", icon: <Mountain size={14} />, sep: true,
      label: "Terrain — sculpt and paint the ground", key: "T", active: mode === "terrain",
      onClick: () => setMode(mode === "terrain" ? "object" : "terrain") }] : []),
  ];
  // In paint mode the toolbar holds the pens instead of the transform tools.
  const paintTools: Tool[] = [
    ...(Object.keys(TOOL_INFO) as PaintTool[]).map((t) => ({
      id: "paint-" + t, icon: toolIcon(t), label: TOOL_INFO[t].hint, key: TOOL_INFO[t].key,
      active: paintTool === t, onClick: () => setPaintTool(t),
    })),
    { id: "paint-mirror", icon: <FlipHorizontal size={14} />, sep: true, key: "M",
      label: mirror ? "Mirror is on: each stroke lands on the other side too" : "Mirror is off: click to paint both sides at once",
      active: !!mirror, onClick: () => setMirror((m) => (m ? 0 : 1)) },
    { id: "frameAll", icon: <Maximize2 size={14} />, label: "Frame everything", key: "Home", sep: true, onClick: () => { w?.frameAll(); noteFramed(); } },
  ];

  if (!source) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted">
        <Box size={26} className="opacity-40" />
        <div className="text-sm">Pick an asset to edit.</div>
        <div className="text-[11px] max-w-md text-center leading-relaxed opacity-80">
          Choose a generation from the strip below, or open a source file — a file can be written
          back to, so its constants become sliders that save.
        </div>
      </div>
    );
  }

  return (
    <div ref={host} tabIndex={0} onKeyDown={onKeyDown} onKeyUp={onKeyUp}
      className="absolute inset-0 flex flex-col outline-none bg-bg">
      {/* ---------------------------------------------------------------- header */}
      <div className="h-9 shrink-0 flex items-center gap-1.5 px-2 border-b border-line bg-panel">
        <Choice value={mode} onChange={(m) => setMode(m)}
          options={[
            { v: "object" as Mode, label: "Object", hint: "Move, rotate and scale the parts" },
            { v: "pose" as Mode, label: "Pose", hint: "Pose the armature (Ctrl+Tab)" },
            { v: "edit" as Mode, label: "Edit", hint: "Move the mesh itself: vertices, edges and faces (Tab)" },
            { v: "paint" as Mode, label: "Paint", hint: "Paint on the model's textures with brushes, layers and an eraser (P)" },
            ...(P.terrain ? [{ v: "terrain" as Mode, label: "Terrain", hint: "Sculpt and paint the ground (T)" }] : []),
          ]} />

        {/* THE ELEMENT, and what is chosen. Only in edit mode, because everywhere else it would
            be a control for something that is not happening. The modifier stack is held off while
            this is open, so the corners here are the ones the code made. */}
        {mode === "edit" && (() => {
          const c = world.current?.elemCounts() || { verts: 0, edges: 0, faces: 0, total: 0 };
          return (
            <>
              <Choice value={elem} onChange={(k) => { setElem(k); world.current?.setElem(k); bump(); }}
                options={[
                  { v: "vert" as ElemKind, label: "Vertex", hint: "One corner  (1)" },
                  { v: "edge" as ElemKind, label: "Edge", hint: "Both ends of an edge  (2)" },
                  { v: "face" as ElemKind, label: "Face", hint: "All three corners of a face  (3)" },
                ]} />
              <span className="text-[10px] text-muted ml-1 tabular-nums" title={
                "A corner, not a vertex: generated geometry duplicates its corners, and moving one "
                + "of three coincident vertices would tear the surface open."}>
                {c.verts} / {c.total} corners
                {c.edges ? " \u00b7 " + c.edges + " edges" : ""}
                {c.faces ? " \u00b7 " + c.faces + " faces" : ""}
              </span>
              {c.verts > 0 && (
                <button className="chip" title={
                  "Put these corners back where the code put them. Blender has no equivalent, "
                  + "because in Blender there is nothing to go back to."}
                  onClick={() => { world.current?.resetElems(); bump(); }}>Reset</button>
              )}
            </>
          );
        })()}

        <div className="flex items-center gap-0.5 ml-1">
          {([["move", Move], ["rotate", RotateCw], ["scale", Scaling]] as const).map(([m, Icon]) => (
            <button key={m} onClick={() => setGizmoMode(m)} title={m + "  (" + m[0].toUpperCase() + ")"}
              className={cls("p-1.5 rounded-md", gizmoMode === m ? "bg-brand/20 text-text" : "text-muted hover:bg-panel2")}>
              <Icon size={13} />
            </button>
          ))}
        </div>
        <button onClick={() => setSpace(space === "global" ? "local" : "global")}
          title="The frame the gizmo uses" className="chip">{space}</button>

        <span className={cls("text-[11px] truncate max-w-[16rem] ml-1", source.kind === "live" ? "text-ok" : "text-muted")} title={source.path || source.label}>
          {source.kind === "file" ? <FileCode size={11} className="inline mr-1 -mt-0.5" />
            : source.kind === "live" ? <Radio size={11} className="inline mr-1 -mt-0.5" />
            : <Sparkles size={11} className="inline mr-1 -mt-0.5" />}
          {source.label}
        </span>
        {/* WHAT IS SELECTED, with the way out beside it. Esc, Alt+A and a click on empty space all
            deselect, but none of them can be seen, and "I cannot deselect it" was the result. */}
        {selection.length > 0 && mode !== "terrain" && (
          <button onClick={deselectEverything} title="Deselect  (Esc · Alt+A · a click on empty space)"
            className="chip flex items-center gap-1 max-w-[12rem]">
            <span className="truncate">
              {selection.length > 1 ? selection.length + " selected" : (parts.find((p) => p.key === active)?.name || active)}
            </span>
            <X size={11} className="shrink-0" />
          </button>
        )}
        {source.kind === "live" && (
          <button onClick={resnap} title="Take the game's scene again. The game keeps running while you edit; this catches up with it."
            className="chip flex items-center gap-1"><RefreshCw size={11} /> refresh</button>
        )}
        {source.kind === "live" && (
          <button onClick={startGame} disabled={starting}
            title={"Press play in the game, wait for it to load what it loads on the first run — rot-rush fetches its brainrot "
              + "models only then — and take the scene again. It happens in the Studio's own copy of the game, not in your browser."}
            className={cls("chip flex items-center gap-1", starting && "opacity-60")}>
            {starting ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} start game
          </button>
        )}
        {source.kind === "live" && (w?.culled.shown || !showCulled) ? (
          <button onClick={() => { const next = !showCulled; setShowCulled(next); world.current?.setShowCulled(next); setParts(world.current?.parts() || []); }}
            title={showCulled
              ? "Showing the whole level. The game switches off what is far from the player — "
                + (w?.culled.shown || 0) + " parts here are off in the game right now. Click to see only what the game draws now."
              : "Showing only what the game draws right now. Click to show the whole level, including the parts the game switched off because they are far away."}
            className={cls("chip flex items-center gap-1", showCulled ? "text-brand border-brand/40 bg-brand/10" : "")}>
            <MapIcon size={11} /> {showCulled ? "whole level" : "as the game shows it"}
          </button>
        ) : null}
        {liveNote && <span className="chip text-warn border-warn/40 max-w-[22rem] truncate" title={liveNote}>{liveNote}</span>}

        <div className="ml-auto flex items-center gap-1.5">
          <Follow signal={readoutSig}>{() => readoutRef.current
            && <span className="chip text-brand border-brand/40 bg-brand/10 font-mono">{readoutRef.current}</span>}</Follow>
          {(dirty || terrainDirty || paintDirty) && (
            <button onClick={save} disabled={saving || !canWrite}
              title={canWrite ? "Write the parameters into the file and the edits beside it"
                : "A recorded run has no file to write to. Open the source to save."}
              className={cls("chip flex items-center gap-1",
                canWrite ? "text-ok border-ok/40 bg-ok/10 hover:bg-ok/20" : "opacity-50")}>
              {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
              save
            </button>
          )}
          {/* In Terrain mode these drive the STROKE stack, which is a different history with a
              different unit: one entry is one held brush stroke, not one edited field. */}
          {mode === "terrain" ? (
            <>
              <button onClick={() => terrainStep(false)} disabled={!tool.current.history.depth}
                title={tool.current.history.depth ? "Undo the " + tool.current.history.topLabel + " stroke  (Ctrl+Z)" : "Nothing to undo"}
                className={cls("chip", tool.current.history.depth ? "" : "opacity-40")}><Undo2 size={11} /></button>
              <button onClick={() => terrainStep(true)} disabled={!tool.current.history.redoDepth}
                title="Redo  (Ctrl+Shift+Z)"
                className={cls("chip", tool.current.history.redoDepth ? "" : "opacity-40")}><Redo2 size={11} /></button>
            </>
          ) : mode === "paint" ? (
            <>
              <button onClick={paintUndo} disabled={!paintInfo?.undo}
                title={paintInfo?.undo ? "Undo the " + paintInfo.undoLabel + "  (Ctrl+Z)" : "Nothing to undo"}
                className={cls("chip", paintInfo?.undo ? "" : "opacity-40")}><Undo2 size={11} /></button>
              <button onClick={paintRedo} disabled={!paintInfo?.redo} title="Redo  (Ctrl+Shift+Z)"
                className={cls("chip", paintInfo?.redo ? "" : "opacity-40")}><Redo2 size={11} /></button>
            </>
          ) : (
            <button onClick={stepBack} disabled={!undo.current.length} title="Undo (Ctrl+Z)"
              className={cls("chip", undo.current.length ? "" : "opacity-40")}><Undo2 size={11} /></button>
          )}
          <button onClick={() => setSideOpen(!sideOpen)} title={sideOpen ? "Hide the side panel" : "Show the side panel"}
            className={cls("chip", sideOpen ? "" : "text-muted/60")}><PanelRight size={11} /></button>

          <Popover label="keys" icon={<CircleHelp size={11} />} width={360}>
            <div className="text-[10px] uppercase tracking-wide text-muted/70 px-1.5 pt-0.5 pb-1">How to move around</div>
            {keymapFor(unityMouse, P.zoom_to_cursor).map(([k, v]) => (
              <div key={k} className="grid grid-cols-[5.2rem_1fr] gap-2 px-1.5 py-[3px] text-[11px] leading-snug">
                <span className="text-text">{k}</span>
                <span className="text-muted">{v}</span>
              </div>
            ))}
          </Popover>

          <Popover label="Overlays" icon={<Grid3x3 size={11} />} active={overlays.wireframe || overlays.faces || overlays.normals}>
            <div className="text-[10px] uppercase tracking-wide text-muted/70 px-1.5 pt-0.5">Guides</div>
            <Check label="Floor grid" on={overlays.grid} onChange={(v) => setOverlays((o) => ({ ...o, grid: v }))} />
            <Check label="Axes" on={overlays.axes} onChange={(v) => setOverlays((o) => ({ ...o, axes: v }))} />
            <Check label="Origins" on={overlays.origins} onChange={(v) => setOverlays((o) => ({ ...o, origins: v }))} />
            <div className="text-[10px] uppercase tracking-wide text-muted/70 px-1.5 pt-1.5">Geometry</div>
            <Check label="Wireframe" on={overlays.wireframe} onChange={(v) => setOverlays((o) => ({ ...o, wireframe: v }))}
              hint="Shift+Z. The edges over the shaded surface." />
            <Check label="Face orientation" on={overlays.faces} onChange={(v) => setOverlays((o) => ({ ...o, faces: v }))}
              hint="Blue is the front of a face, red is the back. Inverted winding shows in no other pass." />
            <Check label="Normals" on={overlays.normals} onChange={(v) => setOverlays((o) => ({ ...o, normals: v }))}
              hint="A short line out of every face." />
            <Check label="X-ray" on={overlays.xray} onChange={(v) => setOverlays((o) => ({ ...o, xray: v }))}
              hint="See through the surface, to reach a bone or check a limb inside a body." />
            <div className="text-[10px] uppercase tracking-wide text-muted/70 px-1.5 pt-1.5">Objects</div>
            <Check label="Outline selected" on={overlays.outline} onChange={setOutline}
              hint="The orange line round what is selected. Off keeps the selection and hides only the line; remembered." />
            <Check label="Bones" on={overlays.bones} onChange={(v) => setOverlays((o) => ({ ...o, bones: v }))} />
            <Check label="Bones in front" on={overlays.bonesInFront} onChange={(v) => setOverlays((o) => ({ ...o, bonesInFront: v }))} />
            <Check label="Sizes on screen" on={overlays.sizes} onChange={(v) => setOverlays((o) => ({ ...o, sizes: v }))}
              hint="The pixel size of each part. Under about 24 px nothing about a part can honestly be judged." />
            <Check label="Lights and cameras" on={overlays.extras} onChange={(v) => setOverlays((o) => ({ ...o, extras: v }))}
              hint="Icons for what has no surface, so it can be seen, picked and moved." />
          </Popover>

          <Popover label={shading} icon={<Eye size={11} />} width={210}>
            <Choice value={shading} onChange={(v) => setShading(v)}
              options={[
                { v: "wire" as Shading, label: "Wire" },
                { v: "solid" as Shading, label: "Solid" },
                { v: "material" as Shading, label: "Material" },
                { v: "rendered" as Shading, label: "Render" },
              ]} />
            {(w?.hasSceneLights || w?.hasSceneWorld) && (
              <>
                <div className="text-[10px] uppercase tracking-wide text-muted/70 px-1.5 pt-1.5">Scene</div>
                {w?.hasSceneLights && (
                  <Check label="Scene lights" on={sceneLights} onChange={setSceneLights}
                    hint="Light it with the lights the code made, instead of the studio rig." />
                )}
                {w?.hasSceneWorld && (
                  <Check label="Scene world" on={sceneWorld} onChange={setSceneWorld}
                    hint="The background, fog and environment the code set." />
                )}
              </>
            )}
            {shading === "solid" && (
              <>
                <div className="text-[10px] uppercase tracking-wide text-muted/70 px-1.5 pt-1.5">Lighting</div>
                <Choice value={solidLight} onChange={(v) => setSolidLight(v)}
                  options={[
                    { v: "studio" as SolidLight, label: "Studio", hint: "The three-point rig the forge photographs with" },
                    { v: "matcap" as SolidLight, label: "Matcap", hint: "Colour and light stop competing with shape" },
                    { v: "flat" as SolidLight, label: "Flat", hint: "No shading at all, so only the colour is left" },
                  ]} />
                {solidLight !== "matcap" && (
                  <>
                    <div className="text-[10px] uppercase tracking-wide text-muted/70 px-1.5 pt-1.5">Colour</div>
                    <Choice value={solidColor} onChange={(v) => setSolidColor(v)}
                      options={[
                        { v: "material" as SolidColor, label: "Material", hint: "Each part's own base colour and colour map" },
                        { v: "single" as SolidColor, label: "Single", hint: "One grey over everything, so only form is left" },
                      ]} />
                  </>
                )}
              </>
            )}
          </Popover>
        </div>
      </div>

      {notice && <div className="px-3 py-1 text-[11px] text-warn bg-warn/10 border-b border-warn/30 shrink-0">{notice}</div>}
      {saveNote && <div className="px-3 py-1 text-[11px] text-ok bg-ok/10 border-b border-ok/30 shrink-0">{saveNote}</div>}

      {/* ---------------------------------------------------------------- body */}
      <div className="flex-1 min-h-0 flex">
        <div className="flex-1 min-w-0 flex flex-col">
          {/* THE CONFIGURATOR BAR: five views, day or night, and the arrows on the model — a strip
              above the viewport, so it can never cover the model (see ConfigBar). Only for an
              asset that uses the configurator contract; every other asset keeps the viewport
              exactly as it was. */}
          {configurable && (
            <Follow signal={navSig}>{() => (
            <ConfigBar view={world.current?.nav.axisView || ""}
              onView={(id) => { const ww = world.current; if (!ww) return; ww.setView(id); ww.frameAll(); noteFramed(); bump(); }}
              look={lookNow} onLook={chooseLook} nightParam={nightParam}
              handles={handlesOn} onHandles={setHandlesOn} declared={declaredHandles}
              usable={[...new Set(handleSpecs.map((h) => specs.find((s) => s.key === h.param)?.label || h.param))]} />
            )}</Follow>
          )}
          <div className="flex-1 min-h-0 relative overflow-hidden">
            <div ref={canvasBox} className="absolute inset-0"
              onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
              onPointerLeave={() => { world.current?.terrain?.setCursor(null); if (cursorRef.current) cursorRef.current.style.display = "none"; }}
              onPointerCancel={onPointerUp} onWheel={onWheel} onContextMenu={(e) => e.preventDefault()} />
            <div ref={cursorRef} aria-hidden className="absolute rounded-full pointer-events-none border border-white/90"
              style={{ display: "none", boxShadow: "0 0 0 1px rgba(0,0,0,0.65)" }} />

            {/* The hidden PlayCanvas studio lives here when the asset is PlayCanvas: a real
                application on a real (invisible) canvas, because a mesh needs a graphics device. */}
            <div ref={pcHost} aria-hidden className="absolute left-0 top-0 w-16 h-16 opacity-0 pointer-events-none overflow-hidden" />

            <Toolbar tools={mode === "paint" ? paintTools : tools} open={toolsOpen} onOpen={setToolsOpen} />

            {paletteOpen && (
              <Palette project={source.project} root={source.assetRoot || ""} onClose={() => setPaletteOpen(false)}
                onPlace={(ref, name) => { void placeRef(ref, name); }} note={placeNote} />
            )}

            {/* box select */}
            <Follow signal={rectSig}>{() => {
              const rect = rectRef.current;
              return rect && (
                <div className="absolute border border-brand/80 bg-brand/10 pointer-events-none"
                  style={{
                    left: Math.min(rect.x0, rect.x1), top: Math.min(rect.y0, rect.y1),
                    width: Math.abs(rect.x1 - rect.x0), height: Math.abs(rect.y1 - rect.y0),
                  }} />
              );
            }}</Follow>

            {/* the pixel size of each part, where the forge would have measured it */}
            {overlays.sizes && parts.filter((p) => !p.at.behind && p.px > 0).map((p) => (
              <div key={p.key} className={cls("absolute -translate-x-1/2 -translate-y-full px-1 rounded text-[9px] tabular-nums pointer-events-none",
                p.px < 24 ? "bg-warn/80 text-bg" : "bg-black/55 text-white/80")}
                style={{ left: p.at.x, top: p.at.y - 2 }}>
                {p.px}px
              </div>
            ))}

            {/* What follows the camera re-renders here, alone, once a frame while the view moves. */}
            <Follow signal={navSig}>{() => {
              const ww = world.current;
              const camFrame = ww?.cameraFrame() || null;
              return (<>
                {/* Blender's passepartout: the camera's frame, and everything outside it dimmed. */}
                {camFrame && (
                  <div className="absolute pointer-events-none border border-white/45"
                    style={{ left: camFrame.x, top: camFrame.y, width: camFrame.w, height: camFrame.h, boxShadow: "0 0 0 4000px rgba(8,10,14,0.58)" }} />
                )}
                <div className={cls("absolute top-2 text-[11px] text-white/75 pointer-events-none select-none", toolsOpen ? "left-12" : "left-10")}
                  style={{ textShadow: "0 1px 2px rgba(0,0,0,.8)" }}>
                  {viewLabelOf(ww, camFrame)}
                </div>

                <div className="absolute top-2 right-2">
                  <NavGizmo theta={ww?.nav.theta ?? 0} phi={ww?.nav.phi ?? 1} view={ww?.nav.axisView || ""}
                    ortho={!!ww?.nav.ortho} hasCamera={cameraKeys.length > 0} cameraOn={!!camFrame}
                    onOrbit={(dx, dy) => ww?.nav.orbit(dx, dy)}
                    onPick={(n) => { ww?.setView(n); bump(); }}
                    onZoom={(dy) => ww?.nav.zoom(dy)}
                    onPan={(dx, dy) => ww?.nav.pan(dx, dy, canvasBox.current?.getBoundingClientRect().height || 600)}
                    onCamera={() => { ww?.lookThrough(); bump(); }}
                    onOrtho={() => { if (ww) { ww.nav.ortho = !ww.nav.ortho; ww.syncCamera(); navSig.emit(); } }} />
                </div>
              </>);
            }}</Follow>

            {(phase === "engine" || phase === "build") && (
              <div className="absolute inset-0 flex items-center justify-center gap-2 text-muted text-xs bg-bg/70">
                <Loader2 size={14} className="animate-spin" />
                {phase === "engine" ? "loading the project's engine…" : "running the asset…"}
              </div>
            )}
            {renderError && phase !== "error" && (
              <div className="absolute left-0 right-0 top-0 z-10 flex items-start gap-2 px-3 py-2 bg-danger/90 text-white text-[11px]">
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <div className="font-medium">The viewport stopped drawing. The outliner and the numbers are still true; the picture is not.</div>
                  <pre className="whitespace-pre-wrap font-mono opacity-90 leading-relaxed">{renderError}</pre>
                </div>
              </div>
            )}
            {phase === "error" && (
              <div className="absolute inset-0 flex items-start justify-center p-6 bg-bg/85 overflow-auto">
                <div className="max-w-2xl">
                  <div className="flex items-center gap-2 text-danger text-sm mb-2">
                    <AlertTriangle size={15} /> the asset did not build
                  </div>
                  <pre className="text-[11px] text-muted whitespace-pre-wrap font-mono leading-relaxed">{error}</pre>
                </div>
              </div>
            )}

            {/* a small honest note when most of the model is too small to judge */}
            {phase === "ready" && smallParts > 0 && !overlays.sizes && (
              <button onClick={() => setOverlays((o) => ({ ...o, sizes: true }))}
                className="absolute bottom-2 left-2 chip text-warn border-warn/40 bg-warn/10">
                {smallParts} part{smallParts === 1 ? "" : "s"} under 24px — too small to judge
              </button>
            )}
            {log.length > 0 && (
              <div className="absolute bottom-2 right-2 max-w-[22rem] text-[10px] text-muted/70 font-mono text-right leading-snug pointer-events-none">
                {log.slice(-3).map((l, i) => <div key={i} className="truncate">{l}</div>)}
              </div>
            )}
          </div>

          {showTimeline && (
            <div className="h-24 shrink-0 border-t border-line">
              <Timeline clip={clip} frame={frame} playing={playing} onFrame={gotoFrame} onPlay={setPlaying}
                onKey={insertKeyHere} onDeleteKey={deleteKeyHere}
                target={mode === "pose" ? activeBone : active}
                onRange={(s, en) => push((e) => ({
                  ...e, clips: e.clips.map((c, i) => (i === clipIdx ? { ...c, start: s, end: en } : c)),
                }))} />
            </div>
          )}
        </div>

        {/* ---------------------------------------------------------------- right */}
        {sideOpen && (
        <aside className="w-[19rem] shrink-0 border-l border-line bg-panel flex flex-col min-h-0">
          {/* A configurator asset gets two tabs: its options, full height, and the scene as it
              always was. Anything else has no tabs and the side panel it always had. */}
          {configurable && (
            <SideTabs tab={sideTab} onTab={setSideTab} options={optionSpecs.length} scene={parts.length} selected={selection.length} />
          )}
          {configurable && sideTab === "options" ? (
            <div className="flex-1 min-h-0">
              <ConfigPanel key={source.path || source.label} variant="tab"
                title={typeof manifest?.name === "string" ? manifest.name : ""}
                specs={optionSpecs} values={values} changed={dirtyParams}
                onChange={setParam} onCommit={commitParam} onReset={resetParam} onResetAll={resetAllParams}
                canSave={canWrite} note={canWrite ? "" : "Opened from a recorded run: parameter changes are live only."}
                presets={presets} activePreset={activePreset} onPreset={applyPresetNow}
                thumbs={thumbs} thumbsOff={thumbsOff}
                handleAxes={handlesOn ? handleAxes : undefined} onUndo={stepBack} />
            </div>
          ) : (<>
          <div className="h-[42%] min-h-0 border-b border-line">
            <Outliner parts={parts} selection={selSet} active={active} filter={filter} onFilter={setFilter}
              onSelect={(k, add) => { w?.select([k], add); }}
              onClear={deselectEverything}
              onFocus={(k) => { w?.select([k]); w?.frameSelected(); }}
              onToggle={(k, on) => {
                w?.setVisible(k, on);
                push((e) => ({ ...e, parts: { ...e.parts, [k]: { ...(e.parts[k] || {}), hidden: !on } } }));
                setParts(w?.parts() || []);
              }} />
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto">
            {mode === "terrain" && (terrain.current ? (
              <>
                <Section title="Brush" defaultOpen right={<Mountain size={11} className="text-muted" />}>
                  <TerrainBrushes brush={tool.current.brush} blocked={tool.current.blocked()} note={terrainNote}
                    onChange={(patch) => {
                      if (patch.kind) tool.current.setKind(patch.kind);
                      Object.assign(tool.current.brush, patch);
                      w?.terrain?.setCursor(tool.current.cursor, tool.current.brush.radius, tool.current.brush.falloff);
                      tbump();
                    }} />
                </Section>
                <Section title="Layers" defaultOpen right={<Layers size={11} className="text-muted" />}>
                  <TerrainLayers layers={terrain.current.layers || []} active={layerIdx}
                    onPick={pickLayer} onChange={changeLayer} />
                </Section>
                <Section title={"Scatter" + (palette.length ? " (" + palette.length + ")" : "")} defaultOpen={false}
                  right={<Sprout size={11} className="text-muted" />}>
                  <TerrainScatter palette={palette} active={tool.current.brush.asset || ""} rows={scatterRows}
                    busy={scatterBusy} query={scatterQ} onQuery={setScatterQ} counts={scatterCounts}
                    onAdd={(id) => {
                      const row = scatterRows.find((r: any) => r.id === id);
                      if (!row) return;
                      const a = scatterAssetOf(row);
                      setPalette((ps) => (ps.some((x) => x.id === a.id) ? ps : [...ps, a]));
                      pickScatter(a.id);
                    }}
                    onPick={pickScatter}
                    onDrop={(id) => {
                      setPalette((ps) => ps.filter((x) => x.id !== id));
                      protos.current.delete(id);
                      if (tool.current.brush.asset === id) { delete tool.current.brush.asset; tbump(); }
                    }} />
                </Section>
                <Section title="Ground" defaultOpen>
                  <TerrainStats report={tReport} drawn={w?.terrain?.drawn} hidden={w?.terrain?.hidden}
                    chunks={w?.terrain?.chunkStats() || null} instanced={!!w?.terrain?.instanced} />
                </Section>
                <Section title="Emit builder" defaultOpen right={<FileCode size={11} className="text-muted" />}>
                  {canWrite ? (
                    <TerrainEmit
                      path={emitPath} name={builderName(emitPath)} engine={emitFor}
                      engineWhy={emitEngine ? "chosen by hand — " + emitPick.why : emitPick.why}
                      lod={emitLod} res={emitRes(terrain.current.spec.res, emitLod)}
                      size={terrain.current.spec.size} scatter={terrain.current.scatter?.length || 0}
                      running={emitPick.running} busy={emitBusy} note={emitNote} error={emitError}
                      onPath={setEmitPath} onEngine={setEmitEngine} onLod={setEmitLod} onEmit={doEmit} />
                  ) : (
                    <div className="text-[11px] text-muted leading-snug">
                      Opened from a recorded run, so there is nowhere beside it to write. Open the
                      asset from the Library and the builder can be emitted next to it.
                    </div>
                  )}
                </Section>
              </>
            ) : (
              <Section title="Terrain" defaultOpen right={<Mountain size={11} className="text-muted" />}>
                <TerrainStart size={tSize} res={tRes} onSize={setTSize} onRes={setTRes} onMake={makeGround} />
                {terrainNote && <div className="text-[10px] text-warn mt-1">{terrainNote}</div>}
              </Section>
            ))}
            {mode === "paint" && (
              <>
                <Section title="Paint" defaultOpen right={<Paintbrush size={11} className="text-muted" />}>
                  <PaintTools tool={paintTool} onTool={setPaintTool} brush={brushes[paintTool]} onPreset={applyPreset} />
                </Section>
                <Section title={TOOL_INFO[paintTool].label} defaultOpen>
                  <PaintBrush tool={paintTool} brush={brushes[paintTool]} onBrush={setBrush} />
                </Section>
                <Section title="Colour" defaultOpen>
                  <PaintColour color={paintColor} color2={paintColor2} recent={recent} onColor={chooseColor} onSwap={swapColors} />
                </Section>
                <Section title="Stroke" defaultOpen>
                  <PaintStroke tool={paintTool} projection={projection} onProjection={setProjection} frontOnly={frontOnly}
                    onFrontOnly={setFrontOnly} mirror={mirror} onMirror={setMirror} fillIsland={fillIsland}
                    onFillIsland={setFillIsland} cloneSet={!!paintInfo?.cloneSource} />
                </Section>
                <Section title="Layers" defaultOpen right={<Layers size={11} className="text-muted" />}>
                  <PaintLayers info={paintInfo} actions={layerActions} />
                </Section>
                <Section title="Texture" defaultOpen>
                  <PaintTexture info={paintInfo} untextured={w?.paint ? w.paint.untextured() : []} onMake={makeTexture}
                    onScale={(id, sc) => { w?.paint?.setScale(id, sc); setPaintDirty(true); }}
                    onExport={exportGlb} exporting={exporting} exportNote={exportNote} canWrite={!!source?.path || !!source?.project} />
                </Section>
                <div className="px-2 py-1.5 space-y-1">
                  <PaintKeys />
                  {(paintNote || paintInfo?.note) && <div className="text-[10px] text-muted">{paintNote || paintInfo?.note}</div>}
                </div>
              </>
            )}
            <Section title="Transform" defaultOpen>
              {activeObj ? (
                <>
                  <Vec3Field label="Location" value={[activeObj.position.x, activeObj.position.y, activeObj.position.z]}
                    onChange={(v) => { activeObj.position.set(v[0], v[1], v[2]); bump(); }}
                    onCommit={() => commitTransform()} />
                  <Vec3Field label="Rotation" unit="rad"
                    value={[activeObj.rotation.x, activeObj.rotation.y, activeObj.rotation.z]}
                    onChange={(v) => { activeObj.rotation.set(v[0], v[1], v[2]); bump(); }}
                    onCommit={() => commitTransform()} />
                  <Vec3Field label="Scale" value={[activeObj.scale.x, activeObj.scale.y, activeObj.scale.z]}
                    onChange={(v) => { activeObj.scale.set(v[0], v[1], v[2]); bump(); }}
                    onCommit={() => commitTransform()} />
                  {edits.parts[active] && (
                    <button onClick={() => { w?.resetPart(active); push((e) => { const p = { ...e.parts }; delete p[active]; return { ...e, parts: p }; }); }}
                      className="w-full h-6 rounded border border-line bg-panel2 text-[11px] text-muted hover:text-text">
                      Back to what the code built
                    </button>
                  )}
                </>
              ) : (
                <div className="text-[11px] text-muted">Nothing selected.</div>
              )}
            </Section>

            {extra?.light && (
              <Section title="Light" defaultOpen right={<Sun size={11} className="text-muted" />}>
                <LightPanel light={extra.light}
                  onChange={(p, v) => { w?.setProp(active, p, v); bump(); }}
                  onCommit={() => commitProps(["color", "intensity", "distance", "decay", "angle", "penumbra", "shadow"])} />
              </Section>
            )}
            {extra?.camera && (
              <Section title="Camera" defaultOpen right={<Video size={11} className="text-muted" />}>
                <Follow signal={navSig}>{() => (
                <CameraPanel camera={extra.camera} looking={world.current?.nav.through === active}
                  onChange={(p, v) => { w?.setProp(active, p, v); bump(); }}
                  onCommit={() => commitProps(["fov", "zoom", "near", "far"])}
                  onLook={() => { w?.lookThrough(active); bump(); }} />
                )}</Follow>
              </Section>
            )}

            <Section title={"World" + (edits.world ? " ·" : "")} defaultOpen={false}>
              <WorldPanel base={w?.assetWorldSpec() || null} world={edits.world}
                onChange={(wd) => {
                  push((e) => { const n = { ...e }; if (wd) n.world = wd; else delete n.world; return n; });
                  if (w) w.worldEdit = wd || null;
                  // The world shows only under the scene's own world, so switch that on rather
                  // than let a change land on nothing visible.
                  setSceneWorld(true);
                  if (wd) liveApply({ world: wd });
                }} />
            </Section>

            {!configurable && (
              <Section title={"Parameters" + (specs.length ? " (" + specs.length + ")" : "")} defaultOpen
                right={<Sliders size={11} className="text-muted" />}>
                <div className="-mx-2 -mb-2.5">
                  <ConfigPanel key={source.path || source.label} variant="inline"
                    specs={specs} values={values} changed={dirtyParams}
                    onChange={setParam} onCommit={commitParam} onReset={resetParam} onResetAll={resetAllParams}
                    canSave={canWrite} note={canWrite ? "" : "Opened from a recorded run: parameter changes are live only."}
                    onUndo={stepBack} />
                </div>
              </Section>
            )}

            <Section title={"Mesh" + (edits.mods.length ? " (" + edits.mods.length + ")" : "")} defaultOpen={false}
              onOpen={setMeshOpen} right={<Shapes size={11} className="text-muted" />}>
              <div className="-mx-2">
                <MeshPanel report={report} mods={edits.mods} target={active} errors={modErrors}
                  onAdd={addMod} onRemove={removeMod} onToggle={toggleMod} onArg={setModArg} />
              </div>
            </Section>

            <Section title={"Bake" + (edits.bakes?.length ? " (" + edits.bakes.length + ")" : "")} defaultOpen={false}>
              <BakePanel part={active} size={bakeSize} onSize={setBakeSize} busy={baking} note={bakeNote}
                previews={{ ao: bakePreviews[active + ":ao"], curvature: bakePreviews[active + ":curvature"], normal: bakePreviews[active + ":normal"] }}
                onBake={(k) => runBake(k)} />
            </Section>

            <Section title="Rig" defaultOpen={false} right={<BoneIcon size={11} className="text-muted" />}>
              <div className="-mx-2">
                <RigPanel bones={edits.bones} selected={boneSel} active={activeBone}
                  mode={w?.rig?.mode || "parts"} canAdd={!!active} hasPose={!!Object.keys(edits.pose).length}
                  onSelect={(n, add) => { w?.selectBone(n, add); bump(); }}
                  onAdd={addBone} onRemove={removeBone} onBind={(m) => rebind(m)}
                  weights={weights} onWeights={changeWeights}
                  onMode={(m) => setMode(m)} onClearPose={clearPose} />
              </div>
              {mode === "pose" && (
                <div className="flex items-center gap-1.5 pt-1.5">
                  <span className="w-[42%] shrink-0 text-[11px] text-muted" title="How many bones above the selected one may move when its tip is dragged. 0 turns inverse kinematics off.">IK reach</span>
                  <div className="flex-1 min-w-0">
                    <NumField value={ikDepth} min={0} max={6} step={1} decimals={0} bar
                      onChange={(v) => setIkDepth(Math.max(0, Math.min(6, Math.round(v))))} />
                  </div>
                </div>
              )}
            </Section>

            <Section title="Animation" defaultOpen={false}>
              <div className="flex items-center gap-1">
                <button onClick={() => { ensureClip(); setShowTimeline(true); }}
                  className="flex-1 h-6 rounded border border-line bg-panel2 text-[11px] hover:border-brand/50">
                  {clip ? "Show timeline" : "New clip"}
                </button>
                {edits.clips.length > 1 && (
                  <select value={clipIdx} onChange={(e) => setClipIdx(Number(e.target.value))}
                    className="h-6 rounded bg-panel2 border border-line text-[11px] px-1">
                    {edits.clips.map((c, i) => <option key={i} value={i}>{c.name}</option>)}
                  </select>
                )}
              </div>
              {clip && (
                <div className="grid grid-cols-2 gap-1">
                  <NumField label="fps" value={clip.fps} step={1} decimals={0}
                    onChange={(v) => push((e) => ({ ...e, clips: e.clips.map((c, i) => (i === clipIdx ? { ...c, fps: Math.max(1, Math.round(v)) } : c)) }))} />
                  <button onClick={() => push((e) => ({ ...e, clips: e.clips.map((c, i) => (i === clipIdx ? { ...c, loop: !c.loop } : c)) }))}
                    className={cls("h-6 rounded border text-[11px]", clip.loop ? "border-brand/50 bg-brand/15 text-text" : "border-line bg-panel2 text-muted")}>
                    {clip.loop ? "loop" : "once"}
                  </button>
                  <div className="col-span-2 text-[10px] text-muted">
                    {keyFrames(clip).length} keyframe{keyFrames(clip).length === 1 ? "" : "s"} on {clip.tracks.length} track{clip.tracks.length === 1 ? "" : "s"}
                  </div>
                </div>
              )}
            </Section>
          </div>
          </>)}
        </aside>
        )}
      </div>

      <Follow signal={statsSig}>{() => {
        const live = liveStats.current;
        return (
          <StatsBar stats={stats && live ? { ...stats, ...live } : stats} gridCell={world.current?.gridCell ?? 1} mode={mode}
            note={dirty ? "unsaved" : ""} hint={mouseHint(unityMouse, P.zoom_to_cursor)} />
        );
      }}</Follow>
    </div>
  );

  /** Take the running game's scene again, and put the edits back on top of it. */
  async function resnap() {
    const w = world.current;
    if (!w || !source || source.kind !== "live") return;
    setPhase("build");
    w.showCulled = showCulled;
    const r = await snapshotLive(w, source.project, liveSnap, liveInfo);
    if (r.error) { setError(r.error); setPhase("error"); return; }
    w.applyOverrides(edits);
    setModErrors(applyMeshEdits(w).errors);
    // The placements are the editor's own and the snapshot leaves them out; they go back on, their
    // moves after them, and the game is brought into step.
    if (edits.placed?.length) {
      const put = await w.applyPlacedDoc(edits.placed, resolveRefRef.current);
      if (put.errors.length) setPlaceNote(put.errors[0]);
      w.applyOverrides(edits);
      void liveSyncRef.current(edits.placed);
    }
    setParts(w.parts());
    setStats(w.stats());
    setPhase("ready");
  }

  /**
   * Press play in the Studio's copy of the game, then take the scene again.
   *
   * A game at its title card has not loaded what it loads for play: rot-rush fetches its brainrot
   * models only after "tap to run" (the portals count every byte fetched before play starts), so
   * a mirror taken at the title has none of them. The press is a click where a play button sits,
   * a tap for a touch layout, and Space and Enter for a keyboard one; then four seconds for the
   * first models to land.
   */
  async function startGame() {
    if (!source || source.kind !== "live" || starting) return;
    setStarting(true);
    setLiveNote("");
    try {
      const r = await api.liveInput(source.project, [
        { type: "click", nx: 0.5, ny: 0.5 },
        { type: "click", nx: 0.5, ny: 0.585 },
        { type: "tap", nx: 0.5, ny: 0.585 },
        { type: "key", key: " ", code: "Space", keyCode: 32 },
        { type: "key", key: "Enter", code: "Enter", keyCode: 13 },
      ]);
      if (!r?.ok) setLiveNote("the game did not take the press: " + (r?.error || "no answer"));
      await sleep(4000);
      await resnap();
    } catch (e: any) {
      setLiveNote("the game did not take the press: " + String(e?.message || e).slice(0, 160));
    } finally {
      setStarting(false);
    }
  }

  /** Record what a light or camera panel just changed, read back off the live object. */
  function commitProps(fields: string[]) {
    const o = activeObj;
    if (!o || !active) return;
    const patch: Record<string, number | string | boolean> = {};
    for (const f of fields) {
      if (f === "color") { if (o.color?.getHexString) patch.color = "#" + o.color.getHexString(); }
      else if (f === "shadow") patch.shadow = !!o.castShadow;
      else if (typeof o[f] === "number") patch[f] = r5(o[f]);
    }
    push((e) => ({ ...e, parts: { ...e.parts, [active]: { ...(e.parts[active] || {}), ...patch } } }));
    liveApply({ parts: { [active]: patch as PartOverride } });
  }

  function commitTransform() {
    const o = activeObj;
    if (!o || !active) return;
    const xf: PartOverride = {
      pos: [r5(o.position.x), r5(o.position.y), r5(o.position.z)] as V3,
      rot: [r5(o.rotation.x), r5(o.rotation.y), r5(o.rotation.z)] as V3,
      scale: [r5(o.scale.x), r5(o.scale.y), r5(o.scale.z)] as V3,
    };
    // A piece of a merged mesh carries what the game needs to find it again (pieces.ts).
    if (o.userData?.piece) xf.piece = { c: o.userData.piece.c, n: o.userData.piece.n };
    push((e) => ({ ...e, parts: { ...e.parts, [active]: { ...(e.parts[active] || {}), ...xf } } }));
    liveApply({ parts: { [active]: xf } });
  }
}

/** The header chip while a brush is down: what it is doing, how wide and how hard. Terrain has
 *  no numeric transform to echo the way a gizmo drag does, and "raise 8.0 @ 0.50" is the nearest
 *  honest equivalent. */
function brushReadout(b: Brush, doing: string): string {
  if (doing === "strength") return "strength " + b.strength.toFixed(2);
  return b.kind + "  r " + (b.radius >= 10 ? b.radius.toFixed(0) : b.radius.toFixed(1))
    + "  \u00b7  " + b.strength.toFixed(2);
}

/** THE PALETTE — what you can put into the scene.
 *
 *  Two shelves, and the order is the point. The primitives first, because blocking a level out
 *  with boxes and getting a light on it is the first thing anyone does and it needs nothing from
 *  the project at all. Then the project's OWN assets: the builders its code exports, the models it
 *  ships, the sprites it draws. That list is the game's, not ours — placing from it puts the real
 *  thing in the scene, built by the same function that will build it at run time.
 */
function Palette({ project, root = "", onPlace, onClose, note }: {
  project: string;
  /** The game inside the workspace whose assets to offer. A workspace with three games in it
   *  offers four hundred things otherwise, most of which the open game cannot even load. */
  root?: string;
  onPlace: (ref: PlacedRef, name: string) => void;
  onClose: () => void;
  note?: string;
}) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"spec" | "code" | "model" | "image">("spec");
  const [rows, setRows] = useState<any[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");

  useEffect(() => {
    if (!project) return;
    let dead = false;
    setBusy(true);
    setHint("");
    api.engineAssets(project, { type: kind, q, root })
      .then((r: any) => { if (!dead) { setRows(r.items || []); setCounts(r.by_type || {}); } })
      .catch(() => { if (!dead) setRows([]); })
      .finally(() => { if (!dead) setBusy(false); });
    return () => { dead = true; };
  }, [project, kind, q, root]);

  const shelf = (t: "spec" | "code" | "model" | "image", label: string) => (
    <button key={t} onClick={() => setKind(t)}
      className={cls("chip", kind === t ? "text-brand border-brand/50 bg-brand/10" : "hover:text-text")}>
      {label}{counts[t] ? ` ${counts[t]}` : ""}
    </button>
  );

  return (
    <div className="absolute left-2 top-12 bottom-2 w-72 z-20 flex flex-col rounded-lg border border-line bg-panel/95 backdrop-blur shadow-xl">
      <div className="h-8 shrink-0 flex items-center gap-1.5 px-2 border-b border-line text-xs">
        <Plus size={12} className="text-brand" />
        <span className="text-text">Add</span>
        <span className="text-muted/70 text-[10px]">click to drop it where you are looking</span>
        <button onClick={onClose} className="ml-auto p-0.5 rounded hover:bg-panel2 text-muted"><X size={12} /></button>
      </div>

      <div className="p-2 border-b border-line">
        <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1">Shapes and lights</div>
        <div className="flex flex-wrap gap-1">
          {PRIMITIVES.map((sh) => (
            <button key={sh} onClick={() => onPlace({ kind: "primitive", shape: sh }, sh)}
              className="chip hover:text-text" title={`Place a ${sh}`}>{sh.replace("Light", " light")}</button>
          ))}
        </div>
      </div>

      <div className="p-2 pb-1 flex items-center gap-1">
        {/* SPECS FIRST. This is where a game actually keeps its content: thirty brainrots in one
            table, fifty-five props in another. A palette that offered only exported functions
            offered the scaffolding and hid the game. */}
        {shelf("spec", "Specs")}
        {shelf("code", "Builders")}
        {shelf("model", "Models")}
        {shelf("image", "Sprites")}
        {busy && <Loader2 size={11} className="animate-spin text-muted ml-auto" />}
      </div>
      <div className="px-2 pb-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search this project…"
          className="w-full bg-panel2 border border-line rounded px-2 py-1 text-xs outline-none focus:border-brand/60" />
      </div>

      <div className="flex-1 min-h-0 overflow-auto px-1 pb-2">
        {!rows.length && !busy && (
          <div className="px-2 py-3 text-[11px] text-muted">
            Nothing of that kind in this project.
          </div>
        )}
        {rows.map((a: any) => {
          // A model file with no mesh in it — a game's clip beside its model, "Labubu_idle.glb" —
          // places an empty group. Said on the row, and refused with the reason on a click.
          const bare = a.type === "model" && a.meshes === 0;
          return (
          <button key={a.id} title={`${a.file}${a.export ? " · " + a.export : ""}${bare ? " · no mesh in this file" : ""}`}
            onClick={() => bare
              ? setHint(`${a.name} holds ${a.anims ? "an animation" : "nothing"} and no mesh, so there is nothing to place. Pick the model it belongs to.`)
              : (setHint(""), onPlace(
              a.model ? { kind: "model", url: a.model, key: a.key || "",
                          ...(a.nodes?.length ? { nodes: a.nodes } : {}), ...(a.parts?.length ? { parts: a.parts } : {}) }
                : a.type === "spec" ? { kind: "code", spec: true, file: a.file, root: a.root || "",
                                        table: a.table || "", key: a.key || "",
                                        index: typeof a.index === "number" ? a.index : -1,
                                        export: a.export, deps: a.deps || [] }
                  : a.type === "code" ? { kind: "code", file: a.file, root: a.root || "",
                                          export: a.export, deps: a.deps || [] }
                    : a.type === "model" ? { kind: "model", url: a.file }
                      : { kind: "image", url: a.file },
              a.name))}
            className={cls("w-full text-left px-2 py-1 rounded hover:bg-panel2 flex items-center gap-2", bare && "opacity-50")}>
            <span className="text-muted">{a.type === "code" || a.type === "spec" ? <Boxes size={12} /> : a.type === "model" ? <Box size={12} /> : <ImageIcon size={12} />}</span>
            <span className="truncate text-xs text-text">{a.name}</span>
            {bare && <span className="shrink-0 text-[9px] uppercase tracking-wide text-muted">{a.anims ? "animation" : "empty"}</span>}
            <span className="ml-auto truncate text-[10px] text-muted/70 max-w-[8rem]">{a.file.split("/").slice(-1)[0]}</span>
          </button>
          );
        })}
      </div>

      {(hint || note) && <div className="px-2 py-1 text-[10px] text-warn border-t border-line">{hint || note}</div>}
    </div>
  );
}

/** Where the sidecar of a source lives. A file's sits beside it. A live game's sits at the root of
 *  the project as studio.edits.json — the name the live shim looks for, and the one to fetch in the
 *  game's own startup: `applyEdits(scene, await (await fetch('./studio.edits.json')).json())`. */
function sidecarPathOf(source: EditorSource): string {
  if (source.kind === "live") return source.project.replace(/\\/g, "/").replace(/\/+$/, "") + "/studio.edits.json";
  return sidecarFor(source.path);
}

// The game serialises its own scene with three's toJSON, which is faithful down to the materials.
// A texture from another origin cannot be read back into a data URL, so on that failure the scene
// is taken again with every image blanked rather than not at all.
//
// TRANSPORT. The live link flattens every answer and clips a string at two thousand characters,
// which is right for an agent reading a value and hopeless for a scene of megabytes. So the page
// does not hand the JSON back through the link at all: it PUTs the text to the Studio's own
// workspace endpoint (CORS is open, the project is a registered root), the editor reads that file
// back whole on its own origin, and deletes it. Three calls, any size.
const SNAP_JS = (path: string, studio: string, at: string) => `(async () => {
  const find = () => {
    try { const s = window.__live && __live.scenes && __live.scenes()[0]; if (s) return s; } catch (e) {}
    try { const r = window.__live && __live.reach && __live.reach(); const p = (r && r.at) || ${JSON.stringify(at)}; if (p) { const v = eval(p); if (v && v.isScene) return v; if (v && v.scene && v.scene.isScene) return v.scene; } } catch (e) {}
    return null;
  };
  const s = find();
  if (!s) return { error: "no three.js scene is reachable in the game yet" };
  // The game's own camera rides along, so the mirror opens on what the player sees instead of a
  // frame around the whole world that makes every creature a dot. Three does not require the
  // camera to be in the scene, so it is hunted: on the engine object, in the scene, on window.
  const findCam = () => {
    try {
      const r = window.__live && __live.reach && __live.reach(); const p = (r && r.at) || ${JSON.stringify(at)};
      if (p) { const v = eval(p); if (v && v.isCamera) return v; for (const k of ["camera", "cam", "activeCamera", "mainCamera", "playerCamera"]) { const c = v && v[k]; if (c && c.isCamera) return c; } }
    } catch (e) {}
    try { let c = null; s.traverse((o) => { if (!c && o.isCamera) c = o; }); if (c) return c; } catch (e) {}
    try { for (const k of Object.keys(window)) { const v = window[k]; if (v && v.isPerspectiveCamera) return v; if (v && typeof v === "object" && v.camera && v.camera.isCamera) return v.camera; } } catch (e) {}
    return null;
  };
  const cam = findCam();
  let inScene = false;
  if (cam) { try { s.traverse((o) => { if (o === cam) inScene = true; }); } catch (e) {} }
  if (cam && inScene) { cam.userData = Object.assign({}, cam.userData || {}, { studioGameCamera: true }); }
  const withCam = (out) => {
    try {
      if (!cam || inScene || !out || !out.object) return out;
      cam.updateMatrixWorld(true);
      const cj = cam.toJSON().object;
      cj.name = cj.name || "Game camera";
      cj.matrix = cam.matrixWorld.toArray();
      cj.userData = Object.assign({}, cj.userData || {}, { studioGameCamera: true });
      delete cj.children;
      out.object.children = (out.object.children || []).concat([cj]);
    } catch (e) {}
    return out;
  };
  let str;
  try { str = JSON.stringify(withCam(s.toJSON())); }
  catch (e) {
    const meta = { geometries: {}, materials: {}, textures: {}, images: {}, shapes: {}, skeletons: {}, animations: {}, nodes: {} };
    s.traverse((o) => { for (const m of [].concat(o.material || [])) if (m) for (const k in m) { const t = m[k]; if (t && t.isTexture && t.image) { if (!t.image.uuid) t.image.uuid = "img-" + Math.random().toString(36).slice(2); meta.images[t.image.uuid] = { uuid: t.image.uuid, url: "" }; } } });
    const out = s.toJSON(meta);
    for (const k of Object.keys(meta)) out[k] = Object.values(meta[k]);
    str = JSON.stringify(withCam(out));
  }
  const res = await fetch(${JSON.stringify(studio + "/api/workspace/file")}, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ path: ${JSON.stringify(path)}, text: str }) });
  return { length: str.length, wrote: res.ok, status: res.status, objects: s.children.length };
})()`;

const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

/** The game's scene as three's own JSON, carried through a file on the Studio, loaded into the world. */
async function snapshotLive(w: EditWorld, project: string, keep: { current: any },
  info: { current: { engine: string; at: string } }): Promise<{ error: string }> {
  const root = project.replace(/\\/g, "/").replace(/\/+$/, "");
  // Open (or re-open) the game in the live tab first: the tab may have moved on since the game
  // was last looked at, and a game builds its scene a moment after its page loads.
  let at = "";
  let engine = "three";
  try {
    const o = await api.liveOpen(project);
    if (o?.error) return { error: o.error };
    engine = String(o?.engine || "three");
    if (engine !== "three" && engine !== "playcanvas") return { error: "live editing mirrors a three.js or PlayCanvas scene; this game is " + engine };
    at = String((o as any)?.found_at || o?.at || "");
    for (let i = 0; i < 8 && !o?.reachable; i++) {
      await sleep(1000);
      const r = await api.liveEval(project, "__live.reach()");
      if (r?.value?.reachable) { at = String(r.value.at || at); engine = String(r.value.engine || engine); break; }
    }
  } catch (e: any) {
    return { error: "the live link did not answer: " + String(e?.message || e).slice(0, 200) };
  }
  info.current = { engine, at };
  // ONE FILE PER SNAPSHOT. The editor can start two at once — the page sets its source twice
  // while the games list arrives — and with one shared name the first one's clean-up deleted the
  // file the second had just written, so it read back a 404 and the level never opened.
  const snapPath = root + "/.studio/live-snapshot-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7) + ".json";
  try { await api.wsMkdir(root + "/.studio"); } catch { /* it is there already, or the write below will say */ }
  let head: Awaited<ReturnType<typeof api.liveEval>>;
  const js = engine === "playcanvas" ? SNAP_JS_PC(snapPath, window.location.origin, at) : SNAP_JS(snapPath, window.location.origin, at);
  try { head = await api.liveEval(project, js); }
  catch (e: any) { return { error: "the live link did not answer: " + String(e?.message || e).slice(0, 200) }; }
  if (!head?.ok) return { error: "the live link refused: " + (head?.error || "no reason given") };
  const v = head.value;
  if (!v || v.error) return { error: v?.error || "the game returned nothing for its scene" };
  if (!v.wrote) return { error: "the game could not hand its scene over (HTTP " + v.status + " writing " + snapPath + ")" };
  let json: any;
  try {
    const res = await fetch(api.wsRaw(snapPath) + "&t=" + Date.now());
    if (!res.ok) return { error: "the snapshot could not be read back (HTTP " + res.status + ")" };
    json = await res.json();
  } catch (e: any) {
    return { error: "the snapshot is not whole: " + String(e?.message || e).slice(0, 160) };
  } finally {
    api.wsDelete(snapPath).catch(() => { /* a leftover file, nothing worse */ });
  }
  keep.current = json;
  return json?.engine === "playcanvas" ? w.loadPcSnapshot(json) : w.loadJSON(json);
}

/** The PlayCanvas app inside the game page: at the path the shim found it, else `pc.app`. */
const PC_APP_JS = (at: string) => "(() => { try { const v = " + (at ? "eval(" + JSON.stringify(at) + ")" : "null")
  + "; if (v && v.root && v.scene) return v; if (v && v.app && v.app.root) return v.app; } catch (e) {}"
  + " return (window.pc && window.pc.app) || null; })()";

// The PlayCanvas twin of SNAP_JS: the page imports the ops library, snapshots its own app's root
// with pcSnapshot, and hands the JSON over through the same file. PACKED: every array as base64
// of a typed array — rot-rush went from 184 MB of decimal JSON with no pictures to 56 MB with all
// 66 of them. The query string defeats a cached copy of the library from before a rebuild.
const SNAP_JS_PC = (path: string, studio: string, at: string) => `(async () => {
  const m = await import(${JSON.stringify(studio + "/forge-ops.js")} + "?v=" + Date.now());
  const app = ${PC_APP_JS(at)};
  if (!app) return { error: "no PlayCanvas application is reachable in the game" };
  const snap = m.pcSnapshot(app.root, { app, pack: true });
  const str = JSON.stringify(snap);
  const res = await fetch(${JSON.stringify(studio + "/api/workspace/file")}, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ path: ${JSON.stringify(path)}, text: str }) });
  return { length: str.length, wrote: res.ok, status: res.status, objects: snap.counts.entities };
})()`;

/** Is this asset PlayCanvas? By the project it lives in, or by what the code plainly calls. */
function isPcSource(source: EditorSource): boolean {
  // A MODEL OPEN IS NEVER PLAYCANVAS. `loadGLB` is the Studio's own name — it appears only in a
  // snippet the Studio itself wrote for a .glb — and what it returns is a three object. Built in
  // the hidden PlayCanvas studio it cannot even be called, so every model in every PlayCanvas
  // game opened to an empty grid. Checked before the engine field, because that field describes
  // the GAME and a file belongs to no engine.
  // THE CODE OUTRANKS THE GAME, for the same reason: a file written against one engine only is
  // built with that engine, and the game's engine decides only when the code names both or
  // neither. The Studio project itself resolves to PlayCanvas, so a pure three.js file in data/ab
  // was built with the PlayCanvas call shapes and died on `new THREE.Group`.
  const code = source.code || "";
  if (/\bloadGLB\s*\(/.test(code)) return false;
  const usesPc = /\bpc\.(create[A-Z]\w*|Entity|StandardMaterial|MeshInstance|Application|Color)\b/.test(code);
  const usesThree = /\bTHREE\.[A-Z]\w*/.test(code);
  if (usesThree && !usesPc) return false;   // written against three: build it with three
  if (usesPc && !usesThree) return true;
  return source.engine === "playcanvas";     // ambiguous: the game decides, as before
}

const r5 = (n: number) => Math.round(n * 1e5) / 1e5;

/** A baked map as a PNG data URL, for the thumbnail. */
function bakedToUrl(b: { width: number; height: number; data: Uint8ClampedArray }): string {
  const c = document.createElement("canvas");
  c.width = b.width; c.height = b.height;
  c.getContext("2d")!.putImageData(new ImageData(new Uint8ClampedArray(b.data), b.width, b.height), 0, 0);
  return c.toDataURL("image/png");
}

const VIEW_LABEL: Record<string, string> = {
  front: "Front", back: "Back", right: "Right", left: "Left", top: "Top", bottom: "Bottom", "3q": "User", low: "User",
};

/** Where the viewport's camera is, as one string: orbit angles, distance, target, projection and
 *  any scene camera looked through. Two equal strings are the same view. */
/** Re-renders what it wraps when `signal` fires, and nothing around it (viewSync.ts). What it
 *  wraps reads the world through refs, so it always draws the current view. */
function Follow({ signal, children }: { signal: Signal; children: () => React.ReactNode }) {
  useSyncExternalStore(signal.subscribe, signal.version);
  return <>{children()}</>;
}

/** Blender's corner text: what you are looking through, and how — and, flying, how fast. */
function viewLabelOf(w: EditWorld | null, frame: { name: string } | null): string {
  if (frame) return "Camera · " + frame.name;
  const n = w?.nav;
  const view = (n?.axisView ? VIEW_LABEL[n.axisView] || n.axisView : "User") + (n?.ortho ? " orthographic" : " perspective");
  if (!n?.flying) return view;
  const v = n.flySpeed >= 10 ? String(Math.round(n.flySpeed)) : n.flySpeed.toFixed(1);
  return view + " · flying " + v + " m/s · the wheel sets the speed";
}

/** The status bar's reminder of the mouse, for the layout chosen in Settings. */
function mouseHint(unity: boolean, toCursor: boolean): string {
  const zoom = "zoom: wheel" + (toCursor ? ", toward the cursor" : "");
  return (unity ? "orbit: Alt+left drag · pan: middle drag" : "orbit: middle drag · pan: Shift+middle")
    + " · " + zoom + " · fly: hold right, W A S D · click again: the next size up · every key under ? keys";
}

function camSig(w: EditWorld): string {
  const n = w.nav;
  return [n.theta, n.phi, n.dist, n.target.x, n.target.y, n.target.z].map((v: number) => v.toFixed(4)).join(",")
    + (n.ortho ? ",ortho" : "") + (n.through ? ",through:" + n.through : "");
}

/** The values that live in the TEXT of the source, as one string, so a change to any of them is
 *  one comparison. Declared parameters are absent on purpose: they reach the build as arguments. */
function spanSig(specs: ParamSpec[], vals: Record<string, ParamValue>): string {
  return JSON.stringify(specs.filter((s) => s.span).map((s) => [s.key, vals[s.key] ?? s.value]));
}

/**
 * Where `./parts.js` beside the asset actually lives, as a URL this window can import.
 *
 * The dev server of the project is preferred whenever the file sits under it, because then a
 * nested import inside THAT file resolves on its own — the workspace endpoint is a query string,
 * so an import chain through it only survives one hop.
 */
function relResolver(source: EditorSource): (rel: string) => string {
  const dir = dirOf(source.path);
  const root = (source.project || "").replace(/\\/g, "/").replace(/\/+$/, "");
  const dev = (source.devUrl || "").replace(/\/+$/, "");
  return (rel: string) => {
    const abs = joinPath(dir, rel);
    if (dev && root && abs.toLowerCase().startsWith(root.toLowerCase() + "/")) {
      return dev + "/" + abs.slice(root.length + 1);
    }
    return api.wsRaw(abs, source.project);
  };
}
