// The chrome around the viewport: the controls, the outliner, the properties, the timeline.
//
// The one control that matters most is the number field. Blender, Houdini, Maya and Unity all
// converged on the same widget — drag it to scrub, click it to type — because a slider alone
// cannot hit an exact value and a text box alone cannot be explored. A parametric editor is
// mostly this control repeated, so it is worth getting right before anything else.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bone, Box, ChevronDown, ChevronLeft, ChevronRight, Eye, EyeOff, FileCode, Folder, Grid3x3, Hand, Layers,
  Loader2, Lock, Mountain, Search, Sprout, Sun, Trash2, Video, ZoomIn,
} from "lucide-react";
import { cls } from "../../ui";
import type { BoneSpec, Clip, Mod, WorldOverride } from "./kit";
import { keyFrames } from "./kit";
import type { Defects } from "./ops";
import type { PartInfo, Stats } from "./world";
import type { Brush, BrushKind, TerrainLayer, TerrainReport } from "./terrain";
import { BRUSHES, type ScatterAsset } from "./terrainTool";

// ------------------------------------------------------------------ number field
interface NumProps {
  value: number;
  onChange(v: number): void;
  onCommit?(v: number): void;
  min?: number;
  max?: number;
  step?: number;
  label?: string;
  /** Draw the value as a filled bar. Blender's "slider button": only when a range is known. */
  bar?: boolean;
  decimals?: number;
  disabled?: boolean;
  tint?: string;
}

const dec = (v: number, d = 3) => {
  if (!isFinite(v)) return "0";
  const s = v.toFixed(d);
  return s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
};

export function NumField({ value, onChange, onCommit, min, max, step, label, bar, decimals = 3, disabled, tint }: NumProps) {
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const st = useRef({ x: 0, v: 0, moved: false });

  const span = (max ?? 1) - (min ?? 0);
  const unit = step && step > 0 ? step : span > 0 ? span / 200 : 0.01;
  const pct = bar && span > 0 ? Math.min(1, Math.max(0, (value - (min ?? 0)) / span)) : 0;

  const clampv = useCallback((v: number) => {
    let out = v;
    if (min !== undefined && out < min) out = min;
    if (max !== undefined && out > max) out = max;
    return out;
  }, [min, max]);

  const down = (e: React.PointerEvent) => {
    if (disabled || e.button !== 0) return;
    st.current = { x: e.clientX, v: value, moved: false };
    setDrag(true);
    try { (e.target as HTMLElement).setPointerCapture(e.pointerId); } catch { /* a nicety */ }
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const dx = e.clientX - st.current.x;
    if (!st.current.moved && Math.abs(dx) < 3) return;
    st.current.moved = true;
    // Shift refines and Ctrl snaps, the same two modifiers the viewport uses, so the hand does
    // not have to learn a second set of rules for the panel.
    const k = e.shiftKey ? 0.1 : 1;
    let v = st.current.v + dx * unit * k;
    if (e.ctrlKey && step) v = Math.round(v / step) * step;
    onChange(clampv(v));
  };
  const up = (e: React.PointerEvent) => {
    if (!drag) return;
    setDrag(false);
    try { (e.target as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* never held */ }
    if (!st.current.moved) { setText(dec(value, decimals)); setTyping(true); return; }
    onCommit?.(value);
  };

  const commitText = () => {
    setTyping(false);
    const v = parseFloat(text);
    if (isFinite(v)) { onChange(clampv(v)); onCommit?.(clampv(v)); }
  };

  if (typing) {
    return (
      <input
        autoFocus
        className="w-full h-6 px-1.5 rounded bg-bg border border-brand text-[11px] font-mono text-text outline-none"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commitText}
        onKeyDown={(e) => {
          if (e.key === "Enter") commitText();
          if (e.key === "Escape") setTyping(false);
          e.stopPropagation();
        }}
      />
    );
  }

  return (
    <div
      ref={box}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      title={label ? label + " — drag to change, click to type" : "Drag to change, click to type"}
      className={cls(
        "relative h-6 rounded border border-line bg-panel2 overflow-hidden select-none",
        disabled ? "opacity-50" : "cursor-ew-resize hover:border-brand/50",
      )}
    >
      {bar && span > 0 && (
        <div className="absolute inset-y-0 left-0 pointer-events-none"
          style={{ width: (pct * 100).toFixed(2) + "%", background: tint || "rgba(96,140,255,0.28)" }} />
      )}
      <div className="relative flex items-center justify-between h-full px-1.5 text-[11px] font-mono">
        {label && <span className="text-muted truncate mr-1.5 pointer-events-none">{label}</span>}
        <span className="text-text tabular-nums pointer-events-none ml-auto">{dec(value, decimals)}</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ toolbar
//
// Blender's T-panel: the tools that do something, down the left of the viewport, one click away.
// Everything here is also a keystroke — the shortcut is in the tooltip, because the toolbar is
// how a tool is FOUND and the keystroke is how it is used once you know it exists.

export interface Tool {
  id: string;
  icon: React.ReactNode;
  label: string;
  key?: string;
  active?: boolean;
  disabled?: boolean;
  onClick(): void;
  /** A rule above this tool, to group it away from the one before. */
  sep?: boolean;
}

export function Toolbar({ tools, open, onOpen }:
  { tools: Tool[]; open: boolean; onOpen(v: boolean): void }) {
  if (!open) {
    return (
      <button onClick={() => onOpen(true)} title="Show the tools"
        className="absolute left-2 top-2 w-6 h-6 rounded-md bg-panel/85 border border-line text-muted hover:text-text flex items-center justify-center">
        <ChevronRight size={12} />
      </button>
    );
  }
  return (
    <div className="absolute left-2 top-2 flex flex-col gap-0.5 p-1 rounded-lg bg-panel/85 border border-line backdrop-blur-sm">
      {tools.map((t) => (
        <div key={t.id} className={cls(t.sep && "mt-1 pt-1 border-t border-line/70")}>
          <button onClick={t.onClick} disabled={t.disabled}
            title={t.label + (t.key ? "   (" + t.key + ")" : "")}
            className={cls("w-7 h-7 rounded-md flex items-center justify-center transition-colors",
              t.active ? "bg-brand/25 text-text ring-1 ring-brand/40"
                : t.disabled ? "text-muted/35" : "text-muted hover:text-text hover:bg-panel2")}>
            {t.icon}
          </button>
        </div>
      ))}
      <button onClick={() => onOpen(false)} title="Hide the tools"
        className="mt-1 pt-1 border-t border-line/70 w-7 h-6 rounded-md text-muted/60 hover:text-text flex items-center justify-center">
        <ChevronLeft size={11} />
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ sections
export function Section({ title, children, right, defaultOpen = true, dense, onOpen }:
  { title: string; children: React.ReactNode; right?: React.ReactNode; defaultOpen?: boolean; dense?: boolean;
    /** Told whether the section is open, so work only it shows can wait until it is. */
    onOpen?: (open: boolean) => void }) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => { onOpen?.(open); }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="border-b border-line/70">
      <button onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1 px-2 py-1.5 text-[11px] font-semibold text-muted hover:text-text hover:bg-panel2/60">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="uppercase tracking-wide">{title}</span>
        <span className="ml-auto flex items-center gap-1" onClick={(e) => e.stopPropagation()}>{right}</span>
      </button>
      {open && <div className={cls(dense ? "px-2 pb-2" : "px-2 pb-2.5 space-y-1.5")}>{children}</div>}
    </div>
  );
}

export function Vec3Field({ label, value, onChange, onCommit, step = 0.01, decimals = 3, unit }:
  { label: string; value: [number, number, number]; onChange(v: [number, number, number]): void;
    onCommit?(): void; step?: number; decimals?: number; unit?: string }) {
  const AX = ["X", "Y", "Z"];
  const TINT = ["rgba(241,66,79,0.22)", "rgba(139,212,74,0.22)", "rgba(59,127,224,0.22)"];
  return (
    <div>
      <div className="flex items-center justify-between text-[10px] text-muted mb-0.5">
        <span>{label}</span>
        {unit && <span className="opacity-60">{unit}</span>}
      </div>
      <div className="grid grid-cols-3 gap-1">
        {[0, 1, 2].map((i) => (
          <NumField key={i} label={AX[i]} value={value[i]} step={step} decimals={decimals} tint={TINT[i]} bar={false}
            onChange={(v) => { const n = [...value] as [number, number, number]; n[i] = v; onChange(n); }}
            onCommit={() => onCommit?.()} />
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ outliner
export function Outliner({ parts, selection, active, onSelect, onToggle, onFocus, filter, onFilter, onClear }:
  { parts: PartInfo[]; selection: Set<string>; active: string;
    onSelect(key: string, additive: boolean): void; onToggle(key: string, on: boolean): void;
    onFocus(key: string): void; filter: string; onFilter(v: string): void;
    /** A click on the empty list below the rows: deselect everything, as Blender's and Unity's
     *  outliners both do. Optional, so a caller that never passes it keeps the old list. */
    onClear?(): void }) {
  const shown = filter
    ? parts.filter((p) => p.name.toLowerCase().includes(filter.toLowerCase()) || p.key.toLowerCase().includes(filter.toLowerCase()))
    : parts;
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="shrink-0 px-2 py-1.5 border-b border-line flex items-center gap-1.5">
        <Layers size={12} className="text-muted shrink-0" />
        <input value={filter} onChange={(e) => onFilter(e.target.value)} placeholder="filter"
          className="flex-1 min-w-0 h-6 px-1.5 rounded bg-panel2 border border-line text-[11px] outline-none focus:border-brand/60" />
        <span className="text-[10px] text-muted tabular-nums shrink-0">{shown.length}</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto py-0.5"
        onClick={(e) => { if (onClear && e.target === e.currentTarget && selection.size) onClear(); }}>
        {!shown.length && <div className="px-3 py-4 text-[11px] text-muted">Nothing here yet.</div>}
        {shown.map((p) => (
          <div key={p.key}
            onClick={(e) => onSelect(p.key, e.shiftKey || e.ctrlKey)}
            onDoubleClick={() => onFocus(p.key)}
            title={`${p.key}\n${p.triangles.toLocaleString()} triangles · ${p.px}px on screen`}
            className={cls(
              "group flex items-center gap-1.5 pr-1.5 h-[22px] text-[11px] cursor-default",
              selection.has(p.key) ? "bg-brand/15" : "hover:bg-panel2/70",
              p.key === active && "ring-1 ring-inset ring-brand/50",
            )}
            style={{ paddingLeft: 8 + p.depth * 11 }}>
            <TypeIcon type={p.type} />
            <span className={cls("truncate", selection.has(p.key) ? "text-text" : "text-muted",
              !p.visible && "line-through opacity-50")}>{p.name}</span>
            {/* The forge's own warning, live: under about 24 px nothing about a part can be
                judged, so saying so beside the name saves a whole round of looking. */}
            {p.px > 0 && p.px < 24 && (
              <span className="shrink-0 text-[9px] px-1 rounded bg-warn/15 text-warn tabular-nums" title="too small on screen to judge">
                {p.px}px
              </span>
            )}
            {p.revealed && (
              <span className="shrink-0 text-[9px] px-1 rounded bg-panel2 text-muted"
                title="The game has switched this off right now, because it is far from the player. It is shown so the whole level can be edited; an edit reaches the game all the same.">
                far
              </span>
            )}
            {p.piece && (
              <span className="shrink-0 text-[9px] px-1 rounded bg-brand/10 text-brand"
                title="One object inside a mesh the game merged for speed. It moves on its own here, and the game gets the same move.">
                piece
              </span>
            )}
            <span className="ml-auto shrink-0 text-[9px] text-muted/50 tabular-nums group-hover:hidden">
              {p.triangles ? (p.triangles > 999 ? (p.triangles / 1000).toFixed(1) + "k" : p.triangles) : ""}
            </span>
            <button onClick={(e) => { e.stopPropagation(); onToggle(p.key, !p.visible); }}
              className="shrink-0 hidden group-hover:block text-muted hover:text-text" title={p.visible ? "Hide" : "Show"}>
              {p.visible ? <Eye size={11} /> : <EyeOff size={11} />}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** What kind of thing a row is, at a glance: Blender's outliner does this and it is the fastest
 *  way to find the one light among forty meshes. */
function TypeIcon({ type }: { type: string }) {
  const c = "shrink-0 opacity-60";
  if (type === "light") return <Sun size={10} className={cls(c, "text-warn")} />;
  if (type === "camera") return <Video size={10} className={c} />;
  if (type === "bone") return <Bone size={10} className={c} />;
  if (type === "group") return <Folder size={10} className={c} />;
  return <Box size={10} className={c} />;
}

// ------------------------------------------------------------------ parameters
// The parameter panel lives in ConfigPanel.tsx now: sections that fold, a filter, the Advanced
// switch, Reset all, and a real control per kind (slider and field, dropdown, switch, swatch),
// plus a configurator asset's presets. It still uses NumField from this file.

// ------------------------------------------------------------------ rig
export function RigPanel({ bones, selected, active, mode, onSelect, onAdd, onRemove, onBind, onMode, onClearPose, hasPose, canAdd, weights, onWeights }:
  { bones: BoneSpec[]; selected: Set<string>; active: string; mode: "parts" | "skin";
    onSelect(name: string, additive: boolean): void; onAdd(): void; onRemove(name: string): void;
    onBind(mode: "parts" | "skin"): void; onMode(m: "object" | "pose"): void;
    onClearPose(): void; hasPose: boolean; canAdd: boolean;
    weights?: "envelope" | "heat"; onWeights?(w: "envelope" | "heat"): void }) {
  return (
    <div className="space-y-2 px-2 py-2">
      <div className="flex items-center gap-1">
        <button onClick={onAdd} disabled={!canAdd}
          title={canAdd ? "Make a bone spanning the selected part, bound to it" : "Select a part first"}
          className={cls("flex-1 h-6 rounded border text-[11px]",
            canAdd ? "border-line bg-panel2 hover:border-brand/50 text-text" : "border-line/50 text-muted/50")}>
          Bone from part
        </button>
        <button onClick={() => onBind(mode === "parts" ? "skin" : "parts")}
          title={mode === "parts"
            ? "Rigid: a bone moves whole named parts. Exact, and right for an asset built from pieces."
            : "Skin: vertices weighted to the nearest bones. Right for one welded surface."}
          className="h-6 px-2 rounded border border-line bg-panel2 text-[11px] text-muted hover:text-text">
          {mode === "parts" ? "rigid" : "skin"}
        </button>
        {mode === "skin" && onWeights && (
          <button onClick={() => onWeights(weights === "heat" ? "envelope" : "heat")}
            title={weights === "heat"
              ? "Heat weights: diffused over the surface from the nearest bone each vertex can see — Blender's automatic weights. Click for envelope."
              : "Envelope weights: fall off with distance to the bone. Click for heat, which keeps one limb's skin off the other."}
            className={cls("h-6 px-2 rounded border text-[11px]", weights === "heat" ? "border-brand/50 bg-brand/15 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
            {weights === "heat" ? "heat" : "envelope"}
          </button>
        )}
      </div>
      {!bones.length && (
        <div className="text-[11px] text-muted leading-relaxed">
          No armature yet. Select a part and press <span className="text-text">Bone from part</span>: it makes a
          bone along the longest axis of that piece and binds it.
        </div>
      )}
      {bones.length > 0 && (
        <>
          <div className="max-h-44 overflow-y-auto -mx-2">
            {bones.map((b) => (
              <div key={b.name} onClick={(e) => onSelect(b.name, e.shiftKey || e.ctrlKey)}
                className={cls("group flex items-center gap-1.5 px-2 h-[22px] text-[11px] cursor-default",
                  selected.has(b.name) ? "bg-brand/15 text-text" : "text-muted hover:bg-panel2/70",
                  b.name === active && "ring-1 ring-inset ring-brand/50")}
                style={{ paddingLeft: 8 + (b.parent ? 12 : 0) }}>
                <span className="truncate">{b.name}</span>
                {!!b.parts?.length && <span className="text-[9px] text-muted/50 truncate">{b.parts.join(", ")}</span>}
                <button onClick={(e) => { e.stopPropagation(); onRemove(b.name); }}
                  className="ml-auto shrink-0 hidden group-hover:block text-muted hover:text-danger" title="Remove">
                  <Trash2 size={10} />
                </button>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => onMode("pose")}
              className="flex-1 h-6 rounded border border-line bg-panel2 text-[11px] hover:border-brand/50">
              Pose mode
            </button>
            <button onClick={onClearPose} disabled={!hasPose}
              className={cls("h-6 px-2 rounded border border-line bg-panel2 text-[11px]",
                hasPose ? "hover:text-text text-muted" : "text-muted/40")}>
              Clear pose
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ mesh
//
// Blender's modifier stack and its mesh statistics, in one panel — plus the thing Blender has no
// equivalent of, because Blender assumes a person is looking: a written report of what is wrong
// with the geometry. Unwelded seams and inverted winding are invisible in a render and fatal in
// a game, and this is where they get said out loud.

const MOD_LABEL: Record<string, string> = {
  skin: "One skin", weld: "Weld", smooth: "Smooth", subdivide: "Subdivide", mirror: "Mirror",
  solidify: "Solidify", displace: "Displace", simplify: "Simplify", flip: "Flip",
  subsurf: "Subdivision surface", bevel: "Bevel", unwrap: "UV unwrap", remesh: "Remesh", relax: "Relax",
};

/** What each modifier is for, in the tooltip: the tool is found by its purpose, not its name. */
const MOD_HINT: Record<string, string> = {
  skin: "Merge everything, weld the seams and smooth by angle — a pile of primitives becomes one surface",
  weld: "Join vertices that sit on top of each other, so the surface is connected",
  smooth: "Smooth shading by angle: soft where faces meet gently, a hard edge where they turn sharply",
  subdivide: "Loop subdivision on triangles: rounder, four times the faces",
  subsurf: "Catmull-Clark: the smooth surface a modeller means. Set a crease angle to keep hard edges hard",
  bevel: "Round the sharp edges so they catch light. Width in world units, segments for how round",
  unwrap: "Lay the surface out flat for textures: islands by facing, packed into the square",
  remesh: "Rebuild as one even surface from the shape alone, seams and all gone. Sculptor's reset",
  relax: "Take the jaggedness out without shrinking",
  mirror: "Mirror across an axis and weld the seam",
  solidify: "Give an open surface a thickness",
  displace: "Push the surface along its normals with noise",
  simplify: "Fewer triangles, on a grid of the given cell",
  flip: "Turn the faces inside out — for a mesh that check() says is inverted",
};

export function MeshPanel({ report, mods, target, onAdd, onRemove, onToggle, onArg, errors }:
  { report: Defects | null; mods: Mod[]; target: string;
    onAdd(op: Mod["op"]): void; onRemove(i: number): void; onToggle(i: number): void;
    onArg(i: number, key: string, v: number): void; errors: string[] }) {
  return (
    <div className="px-2 py-2 space-y-2">
      {report && (
        <div className="space-y-1">
          <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px] text-muted tabular-nums">
            <span>triangles <span className="text-text">{report.triangles.toLocaleString()}</span></span>
            <span>vertices <span className="text-text">{report.vertices.toLocaleString()}</span></span>
            <span>open edges <span className="text-text">{report.boundaryEdges}</span></span>
            {/* Only `holes` is a fault. Open edges on a mesh made of loose parts are closed by a
                weld, and colouring them red would send an agent chasing a problem it has not got. */}
            <span>holes <span className={report.holes ? "text-danger" : "text-text"}>{report.holes}</span></span>
            <span>non-manifold <span className={report.nonManifoldEdges ? "text-warn" : "text-text"}>{report.nonManifoldEdges}</span></span>
            <span>volume <span className={report.inverted ? "text-danger" : "text-text"}>{report.volume}</span></span>
          </div>
          <ul className="space-y-0.5">
            {report.notes.map((n, i) => (
              <li key={i} className={cls("text-[10px] leading-snug",
                /INSIDE OUT/.test(n) ? "text-danger" : /Nothing wrong/.test(n) ? "text-ok" : "text-warn")}>
                {n}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-1 pt-1 border-t border-line/60">
        {(["skin", "subsurf", "bevel", "unwrap", "remesh", "relax", "weld", "smooth", "subdivide", "mirror", "solidify", "displace", "simplify", "flip"] as const).map((op) => (
          <button key={op} onClick={() => onAdd(op)}
            title={MOD_HINT[op] + (target ? " — on " + target : " — on the whole asset")}
            className="px-1.5 h-5 rounded border border-line bg-panel2 text-[10px] text-muted hover:text-text hover:border-brand/50">
            + {MOD_LABEL[op]}
          </button>
        ))}
      </div>

      {!mods.length && (
        <div className="text-[10px] text-muted/80 leading-snug">
          The stack is empty. A modifier here is re-applied after every rebuild, so a weld survives
          moving a slider — which a one-off edit could not.
        </div>
      )}
      {mods.map((m, i) => (
        <div key={i} className="rounded border border-line bg-panel2/50 px-1.5 py-1">
          <div className="flex items-center gap-1.5">
            <button onClick={() => onToggle(i)} title={m.off ? "Switch on" : "Switch off"}
              className={cls("w-2 h-2 rounded-full shrink-0", m.off ? "bg-muted/40" : "bg-ok")} />
            <span className={cls("text-[11px]", m.off ? "text-muted/60 line-through" : "text-text")}>
              {MOD_LABEL[m.op] || m.op}
            </span>
            <span className="text-[9px] text-muted/60 truncate">{m.target || "whole asset"}</span>
            <button onClick={() => onRemove(i)} className="ml-auto text-muted hover:text-danger shrink-0">
              <Trash2 size={10} />
            </button>
          </div>
          <div className="grid grid-cols-2 gap-1 mt-1">
            {Object.entries(m.args).filter(([, v]) => typeof v === "number").map(([k, v]) => (
              <NumField key={k} label={k} value={v as number} decimals={k === "levels" || k === "axis" ? 0 : 3}
                step={k === "levels" || k === "axis" ? 1 : k === "angle" ? 1 : 0.001}
                onChange={(n) => onArg(i, k, n)} />
            ))}
          </div>
        </div>
      ))}
      {errors.map((e, i) => <div key={i} className="text-[10px] text-danger leading-snug">{e}</div>)}
    </div>
  );
}

// ------------------------------------------------------------------ timeline
export function Timeline({ clip, frame, playing, onFrame, onPlay, onKey, onDeleteKey, target, onRange }:
  { clip: Clip | null; frame: number; playing: boolean; onFrame(f: number): void; onPlay(v: boolean): void;
    onKey(): void; onDeleteKey(): void; target: string; onRange(start: number, end: number): void }) {
  const bar = useRef<HTMLDivElement>(null);
  const start = clip?.start ?? 0;
  const end = clip?.end ?? 48;
  const span = Math.max(1, end - start);
  const keys = clip ? keyFrames(clip, target) : [];
  const allKeys = clip ? keyFrames(clip) : [];

  const seek = (e: React.PointerEvent) => {
    const el = bar.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const u = Math.min(1, Math.max(0, (e.clientX - r.left) / Math.max(1, r.width)));
    onFrame(Math.round(start + u * span));
  };

  const pos = (f: number) => ((f - start) / span) * 100;

  return (
    <div className="h-full flex flex-col">
      <div className="h-8 shrink-0 flex items-center gap-1 px-2 border-b border-line bg-panel">
        <button onClick={() => onFrame(start)} className="chip px-1.5" title="First frame">⏮</button>
        <button onClick={() => onFrame(Math.max(start, frame - 1))} className="chip px-1.5" title="Back one frame">◀</button>
        <button onClick={() => onPlay(!playing)}
          className={cls("chip px-2", playing && "text-ok border-ok/40 bg-ok/10")} title="Play (Space)">
          {playing ? "❚❚" : "▶"}
        </button>
        <button onClick={() => onFrame(Math.min(end, frame + 1))} className="chip px-1.5" title="Forward one frame">▶</button>
        <button onClick={() => onFrame(end)} className="chip px-1.5" title="Last frame">⏭</button>

        <div className="w-14 ml-1"><NumField value={frame} step={1} decimals={0} onChange={(v) => onFrame(Math.round(v))} label="F" /></div>
        <div className="w-14"><NumField value={start} step={1} decimals={0} onChange={(v) => onRange(Math.round(v), end)} label="S" /></div>
        <div className="w-14"><NumField value={end} step={1} decimals={0} onChange={(v) => onRange(start, Math.round(v))} label="E" /></div>

        <button onClick={onKey} disabled={!target}
          className={cls("chip ml-2", target ? "hover:text-text" : "opacity-40")}
          title={target ? "Insert a keyframe here for the selection (I)" : "Select something to key"}>
          ◆ key
        </button>
        <button onClick={onDeleteKey} disabled={!keys.includes(frame)}
          className={cls("chip", keys.includes(frame) ? "hover:text-danger" : "opacity-40")} title="Remove the key here">
          ◇ clear
        </button>
        <span className="ml-auto text-[10px] text-muted">
          {clip ? `${clip.name} · ${allKeys.length} key${allKeys.length === 1 ? "" : "s"} · ${clip.fps} fps` : "no clip"}
        </span>
      </div>

      <div ref={bar} onPointerDown={(e) => { seek(e); (e.target as HTMLElement).setPointerCapture(e.pointerId); }}
        onPointerMove={(e) => { if (e.buttons & 1) seek(e); }}
        className="relative flex-1 min-h-0 bg-bg cursor-ew-resize select-none overflow-hidden">
        {/* frame ruler */}
        {Array.from({ length: Math.min(41, span + 1) }).map((_, i) => {
          const f = Math.round(start + (i * span) / Math.min(40, span));
          return (
            <div key={i} className="absolute top-0 bottom-0 border-l border-line/40" style={{ left: pos(f) + "%" }}>
              <span className="absolute top-0.5 left-1 text-[9px] text-muted/60 tabular-nums">{f}</span>
            </div>
          );
        })}
        {/* every key in the clip, dim; the selection's own keys, bright */}
        {allKeys.map((f) => (
          <div key={"a" + f} className="absolute w-1.5 h-1.5 rotate-45 bg-muted/40"
            style={{ left: `calc(${pos(f)}% - 3px)`, bottom: 6 }} />
        ))}
        {keys.map((f) => (
          <div key={"k" + f} className="absolute w-2 h-2 rotate-45 bg-warn"
            style={{ left: `calc(${pos(f)}% - 4px)`, bottom: 14 }} title={"key at " + f} />
        ))}
        <div className="absolute top-0 bottom-0 w-px bg-brand pointer-events-none" style={{ left: pos(frame) + "%" }}>
          <div className="absolute -top-0 -left-4 w-8 text-center text-[9px] text-bg bg-brand rounded-sm tabular-nums">{frame}</div>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ navigation gizmo
/**
 * Blender's navigation gizmo, behaviour for behaviour, because it is the one control a person
 * reaches for when they do not know the keys: DRAG anywhere on it to orbit; CLICK an axis to look
 * straight down it; click the same axis again to look from the other side. Under it sit the four
 * buttons Blender puts there — zoom and pan by dragging, the camera view, the projection — and
 * they matter more here than there, because a trackpad has no middle button and this column is
 * how those users navigate at all.
 */
const OPPOSITE: Record<string, string> = { right: "left", left: "right", top: "bottom", bottom: "top", front: "back", back: "front" };

export function NavGizmo({ theta, phi, view, ortho, hasCamera, cameraOn, onOrbit, onPick, onZoom, onPan, onCamera, onOrtho, size = 80 }: {
  theta: number; phi: number; view: string; ortho: boolean; hasCamera: boolean; cameraOn: boolean;
  onOrbit(dx: number, dy: number): void; onPick(name: string): void;
  onZoom(dy: number): void; onPan(dx: number, dy: number): void; onCamera(): void; onOrtho(): void;
  size?: number;
}) {
  const [hover, setHover] = useState(false);
  const drag = useRef<{ x: number; y: number; ball: string; moved: boolean } | null>(null);
  const r = size / 2;
  const arm = r - 12;
  // The camera basis, from the same spherical angles the navigation uses.
  const sp = Math.sin(phi), cp = Math.cos(phi);
  const dir: [number, number, number] = [sp * Math.sin(theta), cp, sp * Math.cos(theta)];
  const f: [number, number, number] = [-dir[0], -dir[1], -dir[2]];
  const right = norm(cross(f, [0, 1, 0]));
  const up = cross(right, f);
  const proj = (a: [number, number, number]) => ({ x: r + dot(a, right) * arm, y: r - dot(a, up) * arm, z: dot(a, f) });
  const balls = [
    { n: "right", a: [1, 0, 0] as [number, number, number], c: "#f1424f", t: "X" },
    { n: "left", a: [-1, 0, 0] as [number, number, number], c: "#f1424f", t: "" },
    { n: "top", a: [0, 1, 0] as [number, number, number], c: "#8bd44a", t: "Y" },
    { n: "bottom", a: [0, -1, 0] as [number, number, number], c: "#8bd44a", t: "" },
    { n: "front", a: [0, 0, 1] as [number, number, number], c: "#3b7fe0", t: "Z" },
    { n: "back", a: [0, 0, -1] as [number, number, number], c: "#3b7fe0", t: "" },
  ].map((b) => ({ ...b, p: proj(b.a) })).sort((a, b) => a.p.z - b.p.z);

  const down = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const ball = (e.target as Element).closest?.("[data-ball]")?.getAttribute("data-ball") || "";
    drag.current = { x: e.clientX, y: e.clientY, ball, moved: false };
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* fine */ }
    e.preventDefault();
    e.stopPropagation();
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    // A few pixels of slop, so a click on a ball with a shaky hand is still a click.
    if (!d.moved && Math.hypot(dx, dy) < 3) return;
    d.moved = true;
    d.x = e.clientX; d.y = e.clientY;
    onOrbit(dx * 1.6, dy * 1.6);
  };
  const upOrCancel = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    try { (e.currentTarget as Element).releasePointerCapture(e.pointerId); } catch { /* never held */ }
    if (d && !d.moved && d.ball && e.type === "pointerup") onPick(d.ball === view ? OPPOSITE[d.ball] : d.ball);
  };
  const btn = "w-7 h-7 rounded-full flex items-center justify-center border transition-colors select-none";
  const idle = "bg-panel/80 border-line text-muted hover:text-text hover:bg-panel2";
  const on = "bg-brand/25 border-brand/40 text-text";

  return (
    <div className="flex flex-col items-center gap-1 pointer-events-auto" onContextMenu={(e) => e.preventDefault()}>
      <svg width={size} height={size} className={cls("touch-none", drag.current ? "cursor-grabbing" : "cursor-grab")}
        onPointerDown={down} onPointerMove={move} onPointerUp={upOrCancel} onPointerCancel={upOrCancel}
        onPointerEnter={() => setHover(true)} onPointerLeave={() => setHover(false)}>
        <title>Drag to orbit. Click an axis to look down it; click it again for the other side.</title>
        {/* The whole disc is the drag target. It only shows itself on hover, as Blender's does. */}
        <circle cx={r} cy={r} r={r - 1} fill={hover || drag.current ? "rgba(255,255,255,0.09)" : "rgba(0,0,0,0.001)"} />
        {balls.map((b) => (
          <line key={"l" + b.n} x1={r} y1={r} x2={b.p.x} y2={b.p.y}
            stroke={b.c} strokeWidth={b.t ? 1.7 : 1} opacity={b.t ? 0.85 : 0.3} />
        ))}
        {balls.map((b) => {
          const aligned = view === b.n;
          return (
            <g key={b.n} data-ball={b.n} className="cursor-pointer">
              <circle cx={b.p.x} cy={b.p.y} r={aligned ? 9.5 : 8.5} fill={b.t ? b.c : "#20242c"} stroke={aligned ? "#ffffff" : b.c}
                strokeWidth={aligned ? 1.8 : 1.2} opacity={b.t ? 0.95 : 0.8} />
              {b.t
                ? <text x={b.p.x} y={b.p.y + 3.2} textAnchor="middle" fontSize="9" fill="#12141a" fontWeight="700" className="pointer-events-none">{b.t}</text>
                : hover && <text x={b.p.x} y={b.p.y + 3} textAnchor="middle" fontSize="8" fill={b.c} className="pointer-events-none">-{OPPOSITE[b.n][0].toUpperCase() === "R" ? "X" : OPPOSITE[b.n] === "top" ? "Y" : "Z"}</text>}
            </g>
          );
        })}
      </svg>
      <DragButton title="Zoom — drag up and down  (wheel)" className={cls(btn, idle)} onDrag={(_dx, dy) => onZoom(dy * 4)}>
        <ZoomIn size={13} />
      </DragButton>
      <DragButton title="Pan — drag  (Shift + middle drag)" className={cls(btn, idle)} onDrag={(dx, dy) => onPan(dx, dy)}>
        <Hand size={13} />
      </DragButton>
      <button onClick={onCamera} disabled={!hasCamera}
        title={hasCamera ? (cameraOn ? "Leave the camera view  (numpad 0)" : "Look through the scene's camera  (numpad 0)")
          : "No camera in this file. Add one — new THREE.PerspectiveCamera(...), with a name — and it appears here."}
        className={cls(btn, cameraOn ? on : idle, !hasCamera && "opacity-40 cursor-default hover:bg-panel/80 hover:text-muted")}>
        <Video size={13} />
      </button>
      <button onClick={onOrtho} title={(ortho ? "Orthographic" : "Perspective") + " — click to switch  (numpad 5)"}
        className={cls(btn, ortho ? on : idle)}>
        <Grid3x3 size={13} />
      </button>
    </div>
  );
}

/** A button that does its work while dragged — Blender's zoom and pan widgets. */
function DragButton({ title, className, onDrag, children }:
  { title: string; className: string; onDrag(dx: number, dy: number): void; children: React.ReactNode }) {
  const st = useRef<{ x: number; y: number } | null>(null);
  return (
    <button title={title} className={cls(className, "touch-none cursor-ns-resize")}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        st.current = { x: e.clientX, y: e.clientY };
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* fine */ }
        e.preventDefault();
      }}
      onPointerMove={(e) => {
        const s = st.current;
        if (!s) return;
        const dx = e.clientX - s.x, dy = e.clientY - s.y;
        s.x = e.clientX; s.y = e.clientY;
        if (dx || dy) onDrag(dx, dy);
      }}
      onPointerUp={(e) => { st.current = null; try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* never held */ } }}
      onPointerCancel={() => { st.current = null; }}>
      {children}
    </button>
  );
}

/** The keys, in one place, for the help popover: Blender's where Blender has one, Unity's and
 *  Godot's beside them, and the mouse as it is laid out in Settings. */
export function keymapFor(unity: boolean, toCursor = true): Array<[string, string]> {
  return [
    ["Orbit", unity ? "Alt + left drag · drag the axes" : "middle drag · Alt + left drag · drag the axes"],
    ["Pan", (unity ? "middle drag" : "Shift + middle drag") + " · Shift + wheel (up/down) · Alt + wheel (sideways)"],
    ["Zoom", "wheel" + (toCursor ? ", toward the cursor" : "") + (unity ? " · Alt + right drag" : " · Ctrl + middle drag")
      + " · drag the zoom button"],
    ["Fly", "hold right · W A S D move · Q E down and up · Shift faster · the wheel sets the speed"],
    ["Views", "numpad 1 front · 3 right · 7 top · Ctrl for the opposite · 5 ortho · 0 camera"],
    ["Frame", "F or numpad . the selection · Home everything · double-click a row in the outliner"],
    ["Select", "click · drag a box · Shift adds · A all · Alt+A, Esc or a click on empty space: none"],
    ["Click again", "in a game: the next size up — one piece, the whole mesh, its group — then round again to the smallest"],
    ["Tools", unity ? "Q select · W move · E rotate · R scale" : "Q select · W move · E rotate"],
    ["Transform", (unity ? "G move · S scale" : "G move · R rotate · S scale")
      + ", then X/Y/Z for an axis · Ctrl snaps · Shift is fine"],
    ["Ground", "Page Down puts the selection down on the surface under it"],
    ["Hide", "H hide · Alt+H show all"],
    ["Shading", "Z cycles · Shift+Z wireframe"],
    ["Pose", "Ctrl+Tab pose mode · I keyframe · Space play"],
    ["Undo", "Ctrl+Z"],
  ];
}

// ------------------------------------------------------------------ lights and cameras
export function LightPanel({ light, onChange, onCommit }:
  { light: any; onChange(prop: string, v: number | string | boolean): void; onCommit(): void }) {
  const kind = String(light.type || "Light").replace(/Light$/, "") || "Light";
  const i = Number(light.intensity) || 0;
  return (
    <div className="space-y-1">
      <div className="text-[10px] text-muted">{kind} light</div>
      <div className="flex items-center gap-1.5">
        <span className="w-[42%] shrink-0 text-[11px] text-muted">Colour</span>
        <input type="color" value={"#" + light.color.getHexString()}
          onChange={(e) => onChange("color", e.target.value)} onBlur={onCommit}
          className="h-6 flex-1 min-w-0 rounded bg-panel2 border border-line cursor-pointer" />
      </div>
      <NumField label="Intensity" value={i} min={0} max={Math.max(5, i * 3)} step={0.05} bar
        onChange={(v) => onChange("intensity", Math.max(0, v))} onCommit={onCommit} />
      {typeof light.distance === "number" && (
        <NumField label="Distance" value={light.distance} min={0} max={Math.max(20, light.distance * 3)} step={0.1} bar
          onChange={(v) => onChange("distance", Math.max(0, v))} onCommit={onCommit} />
      )}
      {typeof light.decay === "number" && (
        <NumField label="Decay" value={light.decay} min={0} max={4} step={0.05} bar
          onChange={(v) => onChange("decay", Math.max(0, v))} onCommit={onCommit} />
      )}
      {light.isSpotLight && (
        <>
          <NumField label="Angle" value={light.angle} min={0.01} max={Math.PI / 2} step={0.01} bar
            onChange={(v) => onChange("angle", Math.min(Math.PI / 2, Math.max(0.01, v)))} onCommit={onCommit} />
          <NumField label="Penumbra" value={light.penumbra} min={0} max={1} step={0.01} bar
            onChange={(v) => onChange("penumbra", Math.min(1, Math.max(0, v)))} onCommit={onCommit} />
        </>
      )}
      {(light.isDirectionalLight || light.isSpotLight || light.isPointLight) && (
        <Check label="Cast shadows" on={!!light.castShadow}
          onChange={(v) => { onChange("shadow", v); onCommit(); }}
          hint="Needs a floor that receives them: receiveShadow on the mesh." />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ bake
/** The maps a low-poly game mesh carries its detail in. Each bake goes onto the selected part's
 *  material at once and is remembered in the sidecar, so a rebuild bakes it again. */
export type BakeKind = "ao" | "curvature" | "normal";
export const BAKE_LABEL: Record<BakeKind, string> = { ao: "Ambient occlusion", curvature: "Curvature", normal: "Normal from smooth" };
export const BAKE_HINT: Record<BakeKind, string> = {
  ao: "How much sky each point sees. Darkens the creases and the undersides; goes on as aoMap.",
  curvature: "Convex bright, concave dark: the mask a wear or dirt layer wants. Kept on the material for a shader to read.",
  normal: "The part subdivided twice, baked back onto the part as a normal map: the smooth look at the low-poly price.",
};

export function BakePanel({ part, size, onSize, previews, busy, onBake, note }:
  { part: string; size: number; onSize(n: number): void; previews: Partial<Record<BakeKind, string>>; busy: BakeKind | "";
    onBake(kind: BakeKind): void; note: string }) {
  return (
    <div className="space-y-1.5">
      {!part && <div className="text-[11px] text-muted">Select a part to bake its maps.</div>}
      <div className="flex items-center gap-1.5">
        <span className="w-[42%] shrink-0 text-[11px] text-muted">Size</span>
        <div className="flex-1 min-w-0">
          <Choice value={String(size) as "128" | "256" | "512"} onChange={(v) => onSize(Number(v))}
            options={[{ v: "128" as const, label: "128" }, { v: "256" as const, label: "256" }, { v: "512" as const, label: "512" }]} />
        </div>
      </div>
      {(["ao", "curvature", "normal"] as BakeKind[]).map((k) => (
        <div key={k} className="flex items-center gap-1.5">
          <button onClick={() => onBake(k)} disabled={!part || !!busy} title={BAKE_HINT[k]}
            className={cls("flex-1 h-6 rounded border text-[11px] text-left px-2",
              !part || busy ? "border-line/50 text-muted/50" : "border-line bg-panel2 text-text hover:border-brand/50")}>
            {busy === k ? "baking…" : BAKE_LABEL[k]}
          </button>
          {previews[k] && <img src={previews[k]} alt={k} width={26} height={26} className="rounded border border-line shrink-0" style={{ imageRendering: "pixelated" }} />}
        </div>
      ))}
      {note && <div className="text-[10px] text-warn leading-snug">{note}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ world
/** What the code set as background and fog, as plain values, so the panel can start from it. */
export interface WorldBase {
  background?: string;
  fog?: { type: "linear" | "exp2"; color: string; near?: number; far?: number; density?: number } | null;
}

/** Blender's World tab, the two things of it that a scene made of code has: the background and
 *  the fog. An edit here is saved beside the file and applied to the Scene the code returns. */
export function WorldPanel({ base, world, onChange }:
  { base: WorldBase | null; world: WorldOverride | undefined; onChange(w: WorldOverride | undefined): void }) {
  const bg = world?.background ?? base?.background ?? "#1a1e26";
  const fog = world?.fog === undefined ? (base?.fog ?? null) : world.fog;
  const kind: "none" | "linear" | "exp2" = fog ? fog.type : "none";
  const set = (patch: Partial<WorldOverride>) => {
    const next: WorldOverride = { ...(world || {}), ...patch };
    onChange(next.background !== undefined || next.fog !== undefined ? next : undefined);
  };
  const setFog = (patch: Partial<NonNullable<WorldOverride["fog"]>>) => {
    const type = patch.type ?? (kind === "none" ? "linear" : kind);
    set({ fog: { color: fog?.color ?? bg, near: fog?.near ?? 1, far: fog?.far ?? 50, density: fog?.density ?? 0.02, ...patch, type } });
  };
  return (
    <div className="space-y-1">
      {!base && <div className="text-[10px] text-muted leading-snug">The code set no world of its own. These go onto the Scene it returns, and show in the Render shading.</div>}
      <div className="flex items-center gap-1.5">
        <span className="w-[42%] shrink-0 text-[11px] text-muted">Background</span>
        <input type="color" value={bg} onChange={(e) => set({ background: e.target.value })}
          className="h-6 flex-1 min-w-0 rounded bg-panel2 border border-line cursor-pointer" />
      </div>
      <div className="flex items-center gap-1.5">
        <span className="w-[42%] shrink-0 text-[11px] text-muted">Fog</span>
        <div className="flex-1 min-w-0">
          <Choice value={kind} onChange={(k) => { if (k === "none") set({ fog: null }); else setFog({ type: k }); }}
            options={[
              { v: "none" as const, label: "None" },
              { v: "linear" as const, label: "Linear", hint: "Fades between a near and a far distance" },
              { v: "exp2" as const, label: "Exp²", hint: "Thickens with distance, one density" },
            ]} />
        </div>
      </div>
      {fog && (
        <>
          <div className="flex items-center gap-1.5">
            <span className="w-[42%] shrink-0 text-[11px] text-muted">Fog colour</span>
            <input type="color" value={fog.color} onChange={(e) => setFog({ color: e.target.value })}
              className="h-6 flex-1 min-w-0 rounded bg-panel2 border border-line cursor-pointer" />
          </div>
          {fog.type === "linear" ? (
            <>
              <NumField label="Near" value={fog.near ?? 1} min={0} max={Math.max(50, (fog.far ?? 50))} step={0.1} bar decimals={1}
                onChange={(v) => setFog({ near: Math.max(0, v) })} />
              <NumField label="Far" value={fog.far ?? 50} min={0} max={Math.max(200, (fog.far ?? 50) * 2)} step={0.5} bar decimals={1}
                onChange={(v) => setFog({ far: Math.max(0, v) })} />
            </>
          ) : (
            <NumField label="Density" value={fog.density ?? 0.02} min={0} max={0.5} step={0.001} bar decimals={3}
              onChange={(v) => setFog({ density: Math.max(0, v) })} />
          )}
        </>
      )}
      {world && (
        <button onClick={() => onChange(undefined)}
          className="w-full h-6 rounded border border-line bg-panel2 text-[11px] text-muted hover:text-text">
          Back to the world the code set
        </button>
      )}
    </div>
  );
}

export function CameraPanel({ camera, looking, onChange, onCommit, onLook }:
  { camera: any; looking: boolean; onChange(prop: string, v: number): void; onCommit(): void; onLook(): void }) {
  const persp = !!camera.isPerspectiveCamera;
  return (
    <div className="space-y-1">
      <div className="text-[10px] text-muted">{persp ? "Perspective" : "Orthographic"} camera · aspect {persp ? Number(camera.aspect || 1).toFixed(3) : ((camera.right - camera.left) / ((camera.top - camera.bottom) || 1)).toFixed(3)}</div>
      {persp && (
        <NumField label="Field of view" value={camera.fov} min={1} max={150} step={0.5} bar decimals={1}
          onChange={(v) => onChange("fov", Math.min(179, Math.max(1, v)))} onCommit={onCommit} />
      )}
      <NumField label="Zoom" value={camera.zoom ?? 1} min={0.1} max={5} step={0.01} bar
        onChange={(v) => onChange("zoom", Math.max(0.01, v))} onCommit={onCommit} />
      <NumField label="Near" value={camera.near} min={0.001} max={Math.max(1, camera.near * 4)} step={0.01}
        onChange={(v) => onChange("near", Math.max(1e-4, v))} onCommit={onCommit} />
      <NumField label="Far" value={camera.far} min={1} max={Math.max(100, camera.far * 2)} step={1} decimals={0}
        onChange={(v) => onChange("far", Math.max(0.01, v))} onCommit={onCommit} />
      <button onClick={onLook}
        className={cls("w-full h-6 rounded border text-[11px]", looking ? "border-brand/50 bg-brand/15 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
        {looking ? "Leave the camera view" : "Look through it"}  <span className="opacity-60">(numpad 0)</span>
      </button>
    </div>
  );
}

const cross = (a: number[], b: number[]): [number, number, number] =>
  [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm = (v: [number, number, number]): [number, number, number] => {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
};

// ------------------------------------------------------------------ status
export function StatsBar({ stats, gridCell, mode, note, hint }:
  { stats: Stats | null; gridCell: number; mode: string; note: string; hint?: string }) {
  if (!stats) return null;
  return (
    <div className="h-6 shrink-0 flex items-center gap-3 px-2 border-t border-line bg-panel text-[10px] text-muted tabular-nums">
      <span className="capitalize text-text">{mode}</span>
      <span>{stats.triangles.toLocaleString()} tris</span>
      <span>{stats.objects} objects</span>
      <span>{stats.materials} materials</span>
      {/* Red when there are triangles and no draws: the one combination that means the picture is
          lying. It used to read 0 whenever nothing was selected, so nobody could trust it. */}
      <span className={stats.triangles > 0 && !stats.drawn ? "text-danger font-medium" : ""}>{stats.drawCalls} draws</span>
      {stats.error
        ? <span className="text-danger truncate" title={stats.error}>the viewport stopped drawing: {stats.error.split("\n")[0]}</span>
        : stats.triangles > 0 && !stats.drawn && <span className="text-danger">nothing is drawn</span>}
      {stats.bbox && <span>{stats.bbox.map((n) => n.toFixed(2)).join(" × ")}</span>}
      <span>grid {gridCell >= 1 ? gridCell : gridCell.toFixed(gridCell >= 0.1 ? 1 : 3)}</span>
      {note && <span className="text-warn truncate">{note}</span>}
      {/* Blender puts the mouse hints in its status bar for the same reason: the person who does
          not know how to orbit is looking here, not in a manual. */}
      {hint && <span className="ml-auto truncate text-muted/60 hidden lg:inline">{hint}</span>}
      <span className={hint ? "" : "ml-auto"}>{stats.fps} fps</span>
    </div>
  );
}

// ------------------------------------------------------------------ popover
export function Popover({ label, icon, children, width = 232, active }:
  { label: string; icon?: React.ReactNode; children: React.ReactNode; width?: number; active?: boolean }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [open]);
  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen((v) => !v)}
        className={cls("chip flex items-center gap-1", (open || active) && "text-text border-brand/40 bg-brand/10")}>
        {icon}
        <span>{label}</span>
        <ChevronDown size={10} className="opacity-60" />
      </button>
      {open && (
        <div style={{ width }}
          className="absolute right-0 top-full mt-1 z-40 rounded-lg border border-line bg-panel shadow-xl p-2 space-y-1">
          {children}
        </div>
      )}
    </div>
  );
}

export function Check({ label, on, onChange, hint }:
  { label: string; on: boolean; onChange(v: boolean): void; hint?: string }) {
  return (
    <button onClick={() => onChange(!on)} title={hint}
      className="w-full flex items-center gap-2 px-1.5 py-1 rounded text-[11px] hover:bg-panel2 text-left">
      <span className={cls("w-3 h-3 rounded-sm border shrink-0 flex items-center justify-center",
        on ? "bg-brand border-brand" : "border-line")}>
        {on && <span className="w-1.5 h-1.5 rounded-[1px] bg-bg" />}
      </span>
      <span className={on ? "text-text" : "text-muted"}>{label}</span>
    </button>
  );
}

export function Choice<T extends string>({ value, options, onChange }:
  { value: T; options: Array<{ v: T; label: string; hint?: string }>; onChange(v: T): void }) {
  return (
    <div className="flex items-center gap-0.5 p-0.5 rounded-md bg-panel2 border border-line">
      {options.map((o) => (
        <button key={o.v} onClick={() => onChange(o.v)} title={o.hint}
          className={cls("flex-1 px-1.5 py-0.5 rounded text-[11px] transition-colors",
            value === o.v ? "bg-brand/25 text-text" : "text-muted hover:text-text")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ terrain
//
// Four panels, in the order a person works: choose a brush, size it, choose what it paints, and
// then read what the ground has actually become.
//
// The last of those is the one the other tools do not have. Unity shows you a heightmap and
// leaves you to squint at it; Blender has no terrain at all. `report()` gives slope bands and
// per-layer coverage as numbers, so "is this walkable" and "did I paint over all the grass" are
// answered rather than eyeballed — and an agent driving the same field reads exactly the same
// figures over HTTP.

export function TerrainBrushes({ brush, onChange, onCommit, blocked, note }: {
  brush: Brush;
  onChange(patch: Partial<Brush>): void;
  onCommit?(): void;
  /** Non-empty when the brush needs something the palette has not given it yet. */
  blocked?: string;
  note?: string;
}) {
  return (
    <div className="space-y-1.5">
      {/* Three by three. The sculpting six first, then the three that put things ON the ground —
          which is the order a piece of terrain is actually made in. */}
      <div className="grid grid-cols-3 gap-1">
        {BRUSHES.map((b) => (
          <button key={b.kind} onClick={() => onChange({ kind: b.kind as BrushKind })}
            title={b.hint + "   (" + b.key.toUpperCase() + ")"}
            className={cls("h-7 rounded border text-[11px] flex items-center justify-center gap-1 transition-colors",
              brush.kind === b.kind ? "border-brand/60 bg-brand/20 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
            {b.label}
            <span className="text-[9px] opacity-50 uppercase">{b.key}</span>
          </button>
        ))}
      </div>

      <NumField label="Radius" value={brush.radius} min={0.25} max={256} step={0.25} decimals={2} bar
        onChange={(v) => onChange({ radius: v })} onCommit={onCommit} />
      <NumField label="Strength" value={brush.strength} min={0} max={1} step={0.01} decimals={2} bar
        onChange={(v) => onChange({ strength: v })} onCommit={onCommit} />
      <NumField label="Falloff" value={brush.falloff} min={0} max={1} step={0.01} decimals={2} bar
        onChange={(v) => onChange({ falloff: v })} onCommit={onCommit} />

      {brush.kind === "scatter" && (
        <>
          <NumField label="Density" value={brush.density ?? 0.05} min={0.001} max={2} step={0.005} decimals={3} bar
            onChange={(v) => onChange({ density: v })} onCommit={onCommit} />
          <NumField label="Lean" value={brush.jitter ?? 0} min={0} max={0.6} step={0.01} decimals={2} bar
            onChange={(v) => onChange({ jitter: v })} onCommit={onCommit} />
        </>
      )}

      <div className="text-[10px] text-muted/70 leading-snug pt-0.5">
        <span className="text-text">[</span> and <span className="text-text">]</span> resize ·
        shift-drag sets strength · a letter picks a brush
      </div>
      {blocked && <div className="text-[10px] text-warn">{blocked}</div>}
      {note && <div className="text-[10px] text-muted">{note}</div>}
    </div>
  );
}

/** The four paintable surfaces. Clicking one arms the paint brush with it — one click rather than
 *  "choose paint, then choose a layer", because choosing the layer IS choosing to paint. */
export function TerrainLayers({ layers, active, onPick, onChange }: {
  layers: TerrainLayer[];
  active: number;
  onPick(i: number): void;
  onChange(i: number, patch: Partial<TerrainLayer>): void;
}) {
  const [open, setOpen] = useState(-1);
  return (
    <div className="space-y-1">
      {layers.slice(0, 4).map((l, i) => (
        <div key={i} className={cls("rounded border", active === i ? "border-brand/60 bg-brand/10" : "border-line")}>
          <div className="flex items-center gap-1.5 px-1 py-1">
            <button onClick={() => onPick(i)} title={"Paint with " + l.name}
              className="flex items-center gap-1.5 min-w-0 flex-1 text-left">
              <span className="w-4 h-4 rounded-sm border border-black/30 shrink-0" style={{ background: l.colour }} />
              <span className={cls("truncate text-[11px]", active === i ? "text-text" : "text-muted")}>{l.name}</span>
            </button>
            <span className="text-[9px] text-muted/60 tabular-nums shrink-0">{l.tiling}m</span>
            <button onClick={() => setOpen(open === i ? -1 : i)} title="Rename, recolour, retile"
              className="p-0.5 rounded text-muted hover:text-text shrink-0">
              {open === i ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            </button>
          </div>
          {open === i && (
            <div className="px-1 pb-1.5 space-y-1">
              <input value={l.name} onChange={(e) => onChange(i, { name: e.target.value })}
                className="w-full h-6 px-1.5 rounded bg-panel2 border border-line text-[11px] text-text outline-none focus:border-brand/60"
                placeholder="name" />
              <div className="flex items-center gap-1">
                <input type="color" value={/^#[0-9a-f]{6}$/i.test(l.colour) ? l.colour : "#808080"}
                  onChange={(e) => onChange(i, { colour: e.target.value })}
                  className="h-6 w-8 rounded bg-panel2 border border-line cursor-pointer" />
                <div className="flex-1">
                  <NumField label="Tiling" value={l.tiling} min={0.5} max={128} step={0.5} decimals={1}
                    onChange={(v) => onChange(i, { tiling: v })} />
                </div>
              </div>
              <input value={l.texture || ""} onChange={(e) => onChange(i, { texture: e.target.value })}
                className="w-full h-6 px-1.5 rounded bg-panel2 border border-line text-[10px] font-mono text-text outline-none focus:border-brand/60"
                placeholder="texture — a path in this project, or leave it flat" />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * What the scatter brush plants, taken from the project's own asset index.
 *
 * The rows are the Library's rows. There is no second list of trees anywhere in the Studio, and
 * there must not be: a tree planted here has to be built by the same function the game calls, or
 * the level is dressed with lookalikes that vanish the moment it runs for real.
 */
export function TerrainScatter({ palette, active, rows, busy, query, onQuery, onAdd, onPick, onDrop, counts }: {
  palette: ScatterAsset[];
  active: string;
  /** Rows straight from /api/engine/assets, already filtered by the search box. */
  rows: Array<{ id: string; name: string; type: string; file: string }>;
  busy?: boolean;
  query: string;
  onQuery(q: string): void;
  onAdd(id: string): void;
  onPick(id: string): void;
  onDrop(id: string): void;
  /** How many of each are standing on the ground right now. */
  counts: Record<string, number>;
}) {
  return (
    <div className="space-y-1.5">
      {palette.length > 0 && (
        <div className="space-y-0.5">
          {palette.map((a) => (
            <div key={a.id} className={cls("flex items-center gap-1.5 px-1 py-1 rounded border",
              active === a.id ? "border-brand/60 bg-brand/10" : "border-line")}>
              <button onClick={() => onPick(a.id)} title={"Scatter " + a.name}
                className="flex items-center gap-1.5 min-w-0 flex-1 text-left">
                <Sprout size={12} className={active === a.id ? "text-brand" : "text-muted"} />
                <span className={cls("truncate text-[11px]", active === a.id ? "text-text" : "text-muted")}>{a.name}</span>
              </button>
              <span className="text-[9px] text-muted/60 tabular-nums shrink-0">{counts[a.id] || 0}</span>
              <button onClick={() => onDrop(a.id)} title="Take it off the palette"
                className="p-0.5 rounded text-muted/60 hover:text-danger shrink-0"><Trash2 size={10} /></button>
            </div>
          ))}
        </div>
      )}

      <div className="relative">
        <Search size={11} className="absolute left-1.5 top-1.5 text-muted/60 pointer-events-none" />
        <input value={query} onChange={(e) => onQuery(e.target.value)}
          placeholder="this project's own assets…"
          className="w-full h-6 pl-6 pr-1.5 rounded bg-panel2 border border-line text-[11px] text-text outline-none focus:border-brand/60" />
      </div>

      <div className="max-h-40 overflow-auto -mx-0.5">
        {/* A first scan of a project with 1,490 assets in it takes seconds, and an empty list with
            nothing to explain it reads as "this project has none". */}
        {busy && (
          <div className="px-1.5 py-2 text-[10px] text-muted flex items-center gap-1.5">
            <Loader2 size={11} className="animate-spin" /> Scanning this project…
          </div>
        )}
        {!rows.length && !busy && <div className="px-1.5 py-2 text-[10px] text-muted">Nothing of that name in this project.</div>}
        {rows.map((r) => (
          <button key={r.id} onClick={() => onAdd(r.id)} title={r.file}
            className="w-full text-left px-1.5 py-1 rounded hover:bg-panel2 flex items-center gap-1.5">
            <Box size={11} className="text-muted shrink-0" />
            <span className="truncate text-[11px] text-text">{r.name}</span>
            <span className="ml-auto truncate text-[9px] text-muted/60 max-w-[6rem]">{r.file.split("/").slice(-1)[0]}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * What the ground IS, as numbers.
 *
 * Slope bands and per-layer coverage rather than a picture, because "is this walkable" and "how
 * much of it is still grass" cannot be answered by looking, and because the same numbers go back
 * over HTTP to an agent that has no eyes at all.
 */
export function TerrainStats({ report, drawn, hidden, chunks, instanced }: {
  report: TerrainReport | null;
  drawn?: number;
  hidden?: number;
  /** What the chunked viewport is actually drawing, or null when the field is one mesh. */
  chunks?: { chunks: number; triangles: number; lods: number[]; note?: string } | null;
  /** The scatter is being drawn as instances rather than as clones. */
  instanced?: boolean;
}) {
  if (!report) return <div className="text-[11px] text-muted">No ground yet.</div>;
  const BANDS = ["0-5°", "5-15°", "15-30°", "30-45°", "45°+"];
  const pct = (n: number) => (n * 100).toFixed(n >= 0.1 ? 0 : 1) + "%";
  return (
    <div className="space-y-1.5 text-[11px]">
      <div className="flex items-center justify-between text-muted">
        <span>Height</span>
        <span className="text-text tabular-nums">{report.lo.toFixed(1)} … {report.hi.toFixed(1)}</span>
      </div>
      <div className="flex items-center justify-between text-muted">
        <span title="Share of the field a character could stand on">Walkable</span>
        <span className={cls("tabular-nums", report.walkable < 0.3 ? "text-warn" : "text-text")}>{pct(report.walkable)}</span>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-0.5">Slope</div>
        <div className="flex h-2 rounded overflow-hidden border border-line">
          {(report.slopes || []).map((s, i) => (
            <div key={i} title={BANDS[i] + " — " + pct(s)} style={{ width: (s * 100) + "%" }}
              className={["bg-ok/70", "bg-ok/40", "bg-warn/50", "bg-warn/80", "bg-danger/70"][i]} />
          ))}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-0.5">Coverage</div>
        {(report.coverage || []).map((c) => (
          <div key={c.layer} className="flex items-center justify-between text-muted">
            <span className="truncate">{c.layer}</span>
            <span className="text-text tabular-nums ml-2">{pct(c.share)}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between text-muted pt-0.5 border-t border-line/60">
        <span>Scattered</span>
        <span className="text-text tabular-nums">{report.scatter}</span>
      </div>
      {!!hidden && (
        <div className="text-[10px] text-warn leading-snug">
          {drawn} of {report.scatter} drawn — the rest are in the field and in the saved file, just
          not in this preview.
        </div>
      )}
      {instanced && !hidden && !!report.scatter && (
        <div className="text-[9px] text-muted/60 leading-snug">
          drawn as instances — one draw call per asset, not one node per item
        </div>
      )}
      <div className="flex items-center justify-between text-muted">
        <span>Triangles</span>
        <span className="text-text tabular-nums">{report.triangles.toLocaleString()}</span>
      </div>
      {chunks?.note && (
        <div className="text-[10px] text-warn leading-snug">
          chunking stopped and the field went back to one mesh — {chunks.note}
        </div>
      )}
      {chunks && chunks.chunks > 0 && (
        <>
          <div className="flex items-center justify-between text-muted">
            <span title="One mesh per square of the field, each culled and detailed on its own">Chunks</span>
            <span className="text-text tabular-nums">
              {chunks.chunks} · lod {chunks.lods.join(", ")}
            </span>
          </div>
          <div className="flex items-center justify-between text-muted">
            <span title="What the viewport is holding after the distant chunks were coarsened">In the viewport</span>
            <span className={cls("tabular-nums", chunks.triangles < report.triangles ? "text-ok" : "text-text")}>
              {chunks.triangles.toLocaleString()}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/** No ground yet: one button, and the two numbers that decide everything after it. */
export function TerrainStart({ size, res, onSize, onRes, onMake, busy }: {
  size: number; res: number;
  onSize(v: number): void; onRes(v: number): void;
  onMake(): void; busy?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] text-muted leading-snug">
        A flat field to sculpt. It is saved beside the asset and leaves the editor as a builder the
        game calls — not as a file only the Studio can read.
      </div>
      <NumField label="Metres across" value={size} min={16} max={4096} step={16} decimals={0}
        onChange={(v) => onSize(Math.round(v))} />
      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-0.5">Samples per side</div>
        <div className="grid grid-cols-4 gap-1">
          {[129, 257, 513, 1025].map((n) => (
            <button key={n} onClick={() => onRes(n)}
              title={n + " by " + n + " — " + ((n - 1) * (n - 1) * 2).toLocaleString() + " triangles at full detail"}
              className={cls("h-6 rounded border text-[10px] tabular-nums",
                res === n ? "border-brand/60 bg-brand/20 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
              {n}
            </button>
          ))}
        </div>
        <div className="text-[9px] text-muted/60 mt-0.5 tabular-nums">
          {(size / (res - 1)).toFixed(2)} m a sample · {((res - 1) * (res - 1) * 2).toLocaleString()} triangles
        </div>
      </div>
      <button onClick={onMake} disabled={busy}
        className="w-full h-7 rounded border border-brand/50 bg-brand/15 text-[11px] text-text hover:bg-brand/25 flex items-center justify-center gap-1.5">
        <Mountain size={12} /> Make ground
      </button>
    </div>
  );
}

/**
 * THE BUTTON THIS WHOLE THING IS FOR.
 *
 * Everything else in Terrain mode makes a `.terrain.json` — the editor's own memory of the field,
 * which the game cannot read and should not have to. This writes the OTHER file: a module the
 * game imports, that imports nothing back, carrying the heights, the layers and the scatter as
 * code. It is the one thing this Studio does that Unity and Godot do not, and until now nothing
 * in the editor called it.
 *
 * Four things are decided here and each has a default that is right nearly always, so the common
 * case is one click: where it goes, what the export is called, which engine it is for, and how
 * finely the field is written.
 */
export function TerrainEmit({ path, name, engine, engineWhy, lod, res, size, scatter, running,
                             busy, note, error, onPath, onEngine, onLod, onEmit }: {
  path: string;
  name: string;
  engine: "three" | "playcanvas";
  /** How the engine was decided, in plain words. */
  engineWhy: string;
  lod: number;
  /** Samples per side the emit will actually write, after `lod`. */
  res: number;
  /** World units across, for the "a sample every N metres" line. */
  size: number;
  scatter: number;
  /** The game is up in the shared browser right now. */
  running?: boolean;
  busy?: boolean;
  note?: string;
  error?: string;
  onPath(v: string): void;
  onEngine(v: "three" | "playcanvas"): void;
  onLod(v: number): void;
  onEmit(): void;
}) {
  const call = engine === "three" ? name + "(THREE)" : name + "Splat(pc, app)";
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] text-muted leading-snug">
        The ground as the game's own code — heights, layers and everything standing on them, in one
        module that imports nothing.
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-0.5">File</div>
        <input value={path} onChange={(e) => onPath(e.target.value)} spellCheck={false}
          className="w-full h-6 px-1.5 rounded border border-line bg-panel2 text-[10px] text-text font-mono" />
        <div className="text-[9px] text-muted/60 mt-0.5 truncate" title={"export function " + name}>
          exports <span className="text-text font-mono">{name}</span>
        </div>
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-0.5">For</div>
        <div className="grid grid-cols-2 gap-1">
          {(["three", "playcanvas"] as const).map((k) => (
            <button key={k} onClick={() => onEngine(k)}
              className={cls("h-6 rounded border text-[10px]",
                engine === k ? "border-brand/60 bg-brand/20 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
              {k === "three" ? "three.js" : "PlayCanvas"}
            </button>
          ))}
        </div>
        {engineWhy && <div className="text-[9px] text-muted/60 mt-0.5">{engineWhy}</div>}
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-0.5">Detail</div>
        <div className="grid grid-cols-3 gap-1">
          {[1, 2, 4].map((n) => (
            <button key={n} onClick={() => onLod(n)}
              title={n === 1 ? "Every sample" : "Every " + n + (n === 2 ? "nd" : "th") + " sample — a quarter of the bytes at 2, a sixteenth at 4"}
              className={cls("h-6 rounded border text-[10px] tabular-nums",
                lod === n ? "border-brand/60 bg-brand/20 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
              {n === 1 ? "full" : "1/" + n}
            </button>
          ))}
        </div>
        <div className="text-[9px] text-muted/60 mt-0.5 tabular-nums">
          {res}² samples · {(size / Math.max(1, res - 1)).toFixed(2)} m a sample
          {scatter > 0 && " · " + scatter.toLocaleString() + " scattered"}
        </div>
      </div>

      <button onClick={onEmit} disabled={busy}
        className="w-full h-7 rounded border border-brand/50 bg-brand/15 text-[11px] text-text hover:bg-brand/25 flex items-center justify-center gap-1.5 disabled:opacity-50">
        {busy ? <Loader2 size={12} className="animate-spin" /> : <FileCode size={12} />}
        {busy ? "Writing…" : "Emit builder"}
      </button>

      <div className="text-[9px] text-muted/60 leading-snug font-mono break-all">{call}</div>
      {running && (
        <div className="text-[9px] text-muted/70 leading-snug">
          The game is running. A written file is on disk — the page has to load it again to show it.
        </div>
      )}
      {note && <div className="text-[10px] text-ok leading-snug break-all">{note}</div>}
      {error && <div className="text-[10px] text-warn leading-snug break-all">{error}</div>}
    </div>
  );
}

export { Lock };
