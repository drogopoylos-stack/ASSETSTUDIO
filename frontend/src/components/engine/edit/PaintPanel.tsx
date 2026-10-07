// Paint mode's side panel: the pen, its settings, the colour, the layers, and the texture.
//
// Presentation only. Everything it shows comes from the editor's state and the paint engine's
// `info()`; everything it does is a callback. The order is the order a person works in: pick a
// pen, size it, pick a colour, then the layers — and the texture last, which is visited to make or
// export one rather than while painting.

import { ArrowDown, ArrowLeftRight, ArrowUp, Brush, Download, Droplets, Eraser, Eye, EyeOff, Fingerprint, Loader2,
  Merge, PaintBucket, Pipette, Plus, Stamp, Trash2 } from "lucide-react";
import { cls } from "../../ui";
import { Check, Choice, NumField, Section } from "./panels";
import {
  BLEND_MODES, DEFAULT_SWATCHES, PRESETS, TIP_KINDS,
  type BlendMode, type BrushSettings, type PaintPreset, type PaintTool, type Projection, type TipKind,
} from "./paintCore";

export const TOOL_INFO: Record<PaintTool, { label: string; key: string; hint: string }> = {
  brush: { label: "Brush", key: "B", hint: "Paint with the pen and colour you choose" },
  eraser: { label: "Eraser", key: "E", hint: "Take your paint away; the original texture shows again" },
  smudge: { label: "Smudge", key: "R", hint: "Drag the colours along with the stroke" },
  blur: { label: "Blur", key: "L", hint: "Soften the paint under the brush" },
  clone: { label: "Clone", key: "S", hint: "Alt+click where to copy from, then paint to copy it here" },
  fill: { label: "Fill", key: "G", hint: "Fill a whole part, or one texture island of it, with the colour" },
  picker: { label: "Pick", key: "I", hint: "Take the colour under the cursor (Alt+click does it with any tool)" },
};

export const toolIcon = (t: PaintTool, size = 14) => {
  switch (t) {
    case "brush": return <Brush size={size} />;
    case "eraser": return <Eraser size={size} />;
    case "smudge": return <Fingerprint size={size} />;
    case "blur": return <Droplets size={size} />;
    case "clone": return <Stamp size={size} />;
    case "fill": return <PaintBucket size={size} />;
    default: return <Pipette size={size} />;
  }
};

const TIP_LABEL: Record<TipKind, string> = { round: "Round", square: "Square", chalk: "Chalk", noise: "Grunge", splatter: "Splatter", bristle: "Bristle" };
const BLEND_LABEL: Record<BlendMode, string> = { normal: "Normal", multiply: "Multiply", screen: "Screen", overlay: "Overlay", add: "Add", darken: "Darken", lighten: "Lighten" };

export interface PaintInfo {
  targets: Array<{ id: string; name: string; w: number; h: number; scale: number; live: boolean; sharedUV: boolean; made: boolean; strokes: number; unreplayable: number; layers: string[] }>;
  layers: Array<{ id: string; name: string; visible: boolean; opacity: number; blend: BlendMode }>;
  active: string;
  undo: number;
  redo: number;
  undoLabel: string;
  redoLabel: string;
  cloneSource: boolean;
  orphans: number;
  note: string;
}

export interface LayerActions {
  add(): void;
  remove(id: string): void;
  clear(id: string): void;
  merge(id: string): void;
  move(id: string, dir: -1 | 1): void;
  set(id: string, props: Partial<{ name: string; visible: boolean; opacity: number; blend: BlendMode }>): void;
  pick(id: string): void;
}

function Sel<T extends string>({ value, options, onChange, label }: { value: T; options: Array<{ v: T; label: string }>; onChange(v: T): void; label: string }) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] text-muted">
      <span className="shrink-0">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value as T)}
        className="flex-1 min-w-0 h-6 px-1 rounded bg-panel2 border border-line text-text text-[11px] outline-none focus:border-brand/60">
        {options.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function PaintTools({ tool, onTool, onPreset, brush }: {
  tool: PaintTool; onTool(t: PaintTool): void; onPreset(p: PaintPreset): void; brush: BrushSettings;
}) {
  const presets = PRESETS.filter((p) => p.tool === tool || (tool === "brush" && p.tool === "brush"));
  return (
    <div className="space-y-1.5">
      <div className="grid grid-cols-4 gap-1">
        {(Object.keys(TOOL_INFO) as PaintTool[]).map((t) => (
          <button key={t} onClick={() => onTool(t)} title={TOOL_INFO[t].hint + "   (" + TOOL_INFO[t].key + ")"}
            className={cls("h-8 rounded border text-[10px] flex flex-col items-center justify-center gap-0.5 transition-colors",
              tool === t ? "border-brand/60 bg-brand/20 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
            {toolIcon(t, 13)}
            <span className="leading-none">{TOOL_INFO[t].label}</span>
          </button>
        ))}
      </div>
      {presets.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {presets.map((p) => {
            const on = (Object.keys(p.brush) as Array<keyof BrushSettings>).every((k) => brush[k] === p.brush[k]);
            return (
              <button key={p.id} onClick={() => onPreset(p)} title={p.hint}
                className={cls("px-1.5 h-6 rounded border text-[10px] transition-colors",
                  on ? "border-brand/60 bg-brand/20 text-text" : "border-line bg-panel2 text-muted hover:text-text")}>
                {p.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function PaintBrush({ tool, brush, onBrush }: { tool: PaintTool; brush: BrushSettings; onBrush(patch: Partial<BrushSettings>): void }) {
  if (tool === "picker" || tool === "fill") {
    return (
      <div className="space-y-1.5">
        {tool === "fill" && <NumField label="Opacity" value={brush.opacity} min={0} max={1} step={0.01} decimals={2} bar onChange={(v) => onBrush({ opacity: v })} />}
        {tool === "fill" && (
          <Sel label="Mode" value={brush.blend} options={BLEND_MODES.map((m) => ({ v: m, label: BLEND_LABEL[m] }))} onChange={(v) => onBrush({ blend: v })} />
        )}
        {tool === "picker" && <div className="text-[10px] text-muted">Click the model to take its colour. Alt+click does the same with every tool.</div>}
      </div>
    );
  }
  const pixel = tool === "smudge" || tool === "blur" || tool === "clone";
  return (
    <div className="space-y-1.5">
      <NumField label="Size" value={brush.size} min={1} max={400} step={1} decimals={0} bar onChange={(v) => onBrush({ size: v })} />
      {brush.tip === "round" && (
        <NumField label="Hardness" value={brush.hardness} min={0} max={1} step={0.01} decimals={2} bar onChange={(v) => onBrush({ hardness: v })} />
      )}
      {pixel
        ? <NumField label="Strength" value={brush.strength} min={0} max={1} step={0.01} decimals={2} bar onChange={(v) => onBrush({ strength: v })} />
        : <>
          <NumField label="Opacity" value={brush.opacity} min={0} max={1} step={0.01} decimals={2} bar onChange={(v) => onBrush({ opacity: v })} />
          <NumField label="Flow" value={brush.flow} min={0.01} max={1} step={0.01} decimals={2} bar onChange={(v) => onBrush({ flow: v })} />
        </>}
      <NumField label="Spacing" value={brush.spacing} min={0.02} max={2} step={0.01} decimals={2} bar onChange={(v) => onBrush({ spacing: v })} />
      <Sel label="Tip" value={brush.tip} options={TIP_KINDS.map((k) => ({ v: k, label: TIP_LABEL[k] }))} onChange={(v) => onBrush({ tip: v })} />
      {tool === "brush" && (
        <Sel label="Mode" value={brush.blend} options={BLEND_MODES.map((m) => ({ v: m, label: BLEND_LABEL[m] }))} onChange={(v) => onBrush({ blend: v })} />
      )}
      <div className="grid grid-cols-2 gap-1">
        <NumField label="Angle" value={brush.angle} min={-180} max={180} step={1} decimals={0} onChange={(v) => onBrush({ angle: v })} />
        <NumField label="Turn ±" value={brush.angleJitter} min={0} max={180} step={1} decimals={0} onChange={(v) => onBrush({ angleJitter: v })} />
        <NumField label="Size ±" value={brush.sizeJitter} min={0} max={1} step={0.01} decimals={2} onChange={(v) => onBrush({ sizeJitter: v })} />
        <NumField label="Scatter" value={brush.scatter} min={0} max={2} step={0.01} decimals={2} onChange={(v) => onBrush({ scatter: v })} />
      </div>
      <Check label="Tip follows the stroke" on={brush.followStroke} onChange={(v) => onBrush({ followStroke: v })}
        hint="The stamp turns with the direction you paint in — for bristles and flat brushes." />
      <Check label="Pen pressure changes the size" on={brush.pressureSize} onChange={(v) => onBrush({ pressureSize: v })} />
      <Check label="Pen pressure changes the opacity" on={brush.pressureOpacity} onChange={(v) => onBrush({ pressureOpacity: v })} />
    </div>
  );
}

export function PaintColour({ color, color2, recent, onColor, onSwap }: {
  color: string; color2: string; recent: string[]; onColor(hex: string): void; onSwap(): void;
}) {
  const swatch = (hex: string, i: number) => (
    <button key={hex + i} onClick={() => onColor(hex)} title={hex}
      className={cls("w-5 h-5 rounded-sm border", hex.toLowerCase() === color.toLowerCase() ? "border-brand ring-1 ring-brand/60" : "border-line")}
      style={{ background: hex }} />
  );
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <div className="relative w-12 h-10 shrink-0">
          <span className="absolute right-0 bottom-0 w-7 h-7 rounded border border-line" style={{ background: color2 }} title={"Second colour " + color2 + " (X swaps)"} />
          <label className="absolute left-0 top-0 w-7 h-7 rounded border-2 border-white/80 shadow cursor-pointer" style={{ background: color }} title="Paint colour — click to choose">
            <input type="color" value={color} onChange={(e) => onColor(e.target.value)} className="opacity-0 w-0 h-0" />
          </label>
        </div>
        <input value={color} onChange={(e) => { if (/^#[0-9a-fA-F]{6}$/.test(e.target.value)) onColor(e.target.value); }}
          onKeyDown={(e) => e.stopPropagation()}
          className="w-20 h-6 px-1.5 rounded bg-panel2 border border-line text-[11px] font-mono outline-none focus:border-brand/60" />
        <button onClick={onSwap} title="Swap the two colours (X)" className="chip"><ArrowLeftRight size={11} /></button>
      </div>
      <div className="flex flex-wrap gap-1">{DEFAULT_SWATCHES.map(swatch)}</div>
      {recent.length > 0 && (
        <>
          <div className="text-[10px] uppercase tracking-wide text-muted/70">Recent</div>
          <div className="flex flex-wrap gap-1">{recent.map(swatch)}</div>
        </>
      )}
    </div>
  );
}

export function PaintStroke({ tool, projection, onProjection, frontOnly, onFrontOnly, mirror, onMirror, fillIsland, onFillIsland, cloneSet }: {
  tool: PaintTool; projection: Projection; onProjection(p: Projection): void; frontOnly: boolean; onFrontOnly(v: boolean): void;
  mirror: 0 | 1 | 2 | 3; onMirror(v: 0 | 1 | 2 | 3): void; fillIsland: boolean; onFillIsland(v: boolean): void; cloneSet: boolean;
}) {
  return (
    <div className="space-y-1.5">
      {tool === "fill" ? (
        <Choice value={fillIsland ? "island" : "part"} onChange={(v) => onFillIsland(v === "island")}
          options={[
            { v: "part", label: "Whole part", hint: "Fill every face of the part you click" },
            { v: "island", label: "UV island", hint: "Fill only the connected piece of texture under the cursor" },
          ]} />
      ) : (
        <Choice value={projection} onChange={onProjection}
          options={[
            { v: "view" as Projection, label: "What you see", hint: "Paint lands where the brush is on the screen, and never behind something" },
            { v: "sphere" as Projection, label: "3D ball", hint: "Paint everything within the brush's radius in 3D, seen or not — round a thin part in one stroke" },
          ]} />
      )}
      <Choice value={String(mirror) as "0" | "1" | "2" | "3"} onChange={(v) => onMirror(Number(v) as 0 | 1 | 2 | 3)}
        options={[
          { v: "0", label: "No mirror" },
          { v: "1", label: "Mirror X", hint: "Paint the other side too, across the asset's own left-right axis (M)" },
          { v: "2", label: "Y" },
          { v: "3", label: "Z" },
        ]} />
      {tool !== "fill" && (
        <Check label="Front faces only" on={frontOnly} onChange={onFrontOnly}
          hint="Leave faces turned away from you alone, and fade the paint where a surface turns edge-on." />
      )}
      {tool === "clone" && (
        <div className={cls("text-[10px]", cloneSet ? "text-ok" : "text-warn")}>
          {cloneSet ? "Source set. Alt+click again to move it." : "Alt+click the model where the clone should copy from."}
        </div>
      )}
    </div>
  );
}

export function PaintLayers({ info, actions }: { info: PaintInfo | null; actions: LayerActions }) {
  const layers = info ? [...info.layers].reverse() : [];
  const active = info?.layers.find((l) => l.id === info.active) || null;
  return (
    <div className="space-y-1.5">
      <div className="rounded border border-line bg-panel2/40 max-h-44 overflow-y-auto">
        {!layers.length && <div className="px-2 py-2 text-[10px] text-muted">No layers yet. The first stroke makes one.</div>}
        {layers.map((l) => (
          <div key={l.id} onClick={() => actions.pick(l.id)}
            className={cls("flex items-center gap-1.5 px-1.5 h-6 text-[11px] cursor-default",
              l.id === info?.active ? "bg-brand/15 text-text" : "text-muted hover:bg-panel2")}>
            <button onClick={(e) => { e.stopPropagation(); actions.set(l.id, { visible: !l.visible }); }}
              title={l.visible ? "Hide" : "Show"} className="shrink-0 text-muted hover:text-text">
              {l.visible ? <Eye size={11} /> : <EyeOff size={11} />}
            </button>
            <span className={cls("truncate flex-1", !l.visible && "opacity-50")}
              onDoubleClick={(e) => { e.stopPropagation(); const n = window.prompt("Layer name", l.name); if (n && n.trim()) actions.set(l.id, { name: n.trim() }); }}
              title="Double-click to rename">{l.name}</span>
            <span className="text-[9px] text-muted/60 tabular-nums">{Math.round(l.opacity * 100)}%</span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        <button className="chip flex items-center gap-1" onClick={actions.add} title="A new layer above the chosen one"><Plus size={11} /> layer</button>
        {active && <button className="chip" onClick={() => actions.move(active.id, 1)} title="Move the layer up"><ArrowUp size={11} /></button>}
        {active && <button className="chip" onClick={() => actions.move(active.id, -1)} title="Move the layer down"><ArrowDown size={11} /></button>}
        {active && <button className="chip flex items-center gap-1" onClick={() => actions.merge(active.id)} title="Merge into the layer below"><Merge size={11} /> merge</button>}
        {active && <button className="chip" onClick={() => actions.clear(active.id)} title="Empty the layer (undo brings it back)">clear</button>}
        {active && <button className="chip text-warn" onClick={() => actions.remove(active.id)} title="Delete the layer (undo brings it back)"><Trash2 size={11} /></button>}
      </div>
      {active && (
        <div className="space-y-1">
          <NumField label="Layer opacity" value={active.opacity} min={0} max={1} step={0.01} decimals={2} bar
            onChange={(v) => actions.set(active.id, { opacity: v })} />
          <Sel label="Blend" value={active.blend} options={BLEND_MODES.map((m) => ({ v: m, label: BLEND_LABEL[m] }))}
            onChange={(v) => actions.set(active.id, { blend: v })} />
        </div>
      )}
    </div>
  );
}

export function PaintTexture({ info, untextured, onMake, onScale, onExport, exporting, exportNote, canWrite }: {
  info: PaintInfo | null;
  untextured: Array<{ key: string; name: string; uv: boolean }>;
  onMake(key: string): void;
  onScale(id: string, scale: number): void;
  onExport(): void;
  exporting: boolean;
  exportNote: string;
  canWrite: boolean;
}) {
  const live = info?.targets.filter((t) => t.live) || [];
  return (
    <div className="space-y-1.5">
      {!live.length && <div className="text-[10px] text-warn">Nothing on this asset has a texture yet. Make one below.</div>}
      {live.map((t) => (
        <div key={t.id} className="rounded border border-line px-1.5 py-1 space-y-1">
          <div className="flex items-center gap-1.5 text-[11px]">
            <span className="truncate flex-1 text-text" title={t.id}>{t.name}</span>
            <span className="text-[10px] text-muted tabular-nums">{t.w} × {t.h}</span>
          </div>
          {!t.made && (
            <Choice value={String(t.scale)} onChange={(v) => onScale(t.id, Number(v))}
              options={[
                { v: "1", label: "1×", hint: "Paint at the texture's own size" },
                { v: "2", label: "2×", hint: "Twice as many pixels each way: finer lines, a bigger file" },
                { v: "4", label: "4×", hint: "Four times each way, up to 4096" },
              ]} />
          )}
          {t.sharedUV && (
            <div className="text-[10px] text-warn leading-snug">
              Some parts share texture space here: paint on one of them shows on the others too (for example, spikes that all use one swatch).
            </div>
          )}
          {t.unreplayable > 0 && (
            <div className="text-[10px] text-muted leading-snug">
              {t.unreplayable} smudge, blur or clone stroke{t.unreplayable === 1 ? "" : "s"} cannot follow a change of the texture layout.
            </div>
          )}
        </div>
      ))}
      {untextured.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-muted/70">Parts with no texture</div>
          {untextured.slice(0, 12).map((u) => (
            <div key={u.key} className="flex items-center gap-1.5 text-[11px]">
              <span className="truncate flex-1 text-muted" title={u.key}>{u.name}</span>
              <button className="chip" onClick={() => onMake(u.key)} disabled={!u.uv}
                title={u.uv ? "Make a texture from how this part looks now, so it can be painted" : "This part has no texture layout: add the Unwrap step in the modifier stack first"}>
                make texture
              </button>
            </div>
          ))}
        </div>
      )}
      <button onClick={onExport} disabled={exporting || !canWrite}
        title={canWrite ? "Write the model with its painted textures as a .glb beside the asset — for Godot, Unity, Unreal or Blender"
          : "A recorded run has no folder to write to. Open the source file to export."}
        className={cls("w-full h-7 rounded border text-[11px] flex items-center justify-center gap-1.5",
          canWrite ? "border-brand/50 bg-brand/15 text-text hover:bg-brand/25" : "border-line text-muted opacity-60")}>
        {exporting ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />} Export GLB
      </button>
      {exportNote && <div className="text-[10px] text-muted break-all">{exportNote}</div>}
    </div>
  );
}

export function PaintKeys() {
  return (
    <div className="text-[10px] text-muted/80 leading-snug">
      <span className="text-text">B</span> brush · <span className="text-text">E</span> eraser · <span className="text-text">R</span> smudge ·{" "}
      <span className="text-text">L</span> blur · <span className="text-text">S</span> clone · <span className="text-text">G</span> fill ·{" "}
      <span className="text-text">I</span> pick · <span className="text-text">[ ]</span> size · <span className="text-text">Shift+[ ]</span> hardness ·{" "}
      <span className="text-text">1–0</span> opacity · <span className="text-text">X</span> swap colours · <span className="text-text">M</span> mirror ·{" "}
      <span className="text-text">Alt+click</span> pick colour or clone source · <span className="text-text">Ctrl+Z</span> undo · <span className="text-text">P</span> leave paint
    </div>
  );
}

export { Section };
