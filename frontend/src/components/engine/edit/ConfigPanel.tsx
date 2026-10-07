// The configurator panel: a modular asset's presets as a grid of thumbnails, its parameters in
// sections, and the bar over the viewport that picks a view and the lighting.
//
// It replaces the flat parameter list the editor had, for every asset — the same controls got
// better for everyone — and for an asset that uses the configurator contract (configurator.ts) it
// also becomes the side panel's first tab. What each control IS was decided by one rule: the
// control must make a wrong value impossible to enter, not merely unlikely. A choice is a real
// dropdown of its own options, a switch is a switch, a colour can only become a colour, and a
// number has both a slider to explore with and a field to type the exact value into.
//
// Every rule that is not layout lives in configModel.ts and presets.ts, where it is tested.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, Box, Check, ChevronDown, ChevronRight, Layers, Loader2, Moon, Move, MoveHorizontal, MoveVertical,
  RotateCcw, Search, SlidersHorizontal, Sun, Undo2, X,
} from "lucide-react";
import { cls, useSticky } from "../../ui";
import type { PresetSpec } from "./configurator";
import type { ParamSpec, ParamValue } from "./kit";
import { labelOf } from "./kit";
import { NumField } from "./panels";
import { advancedCount, decimalsFor, describePreset, paramSections, prettyOption, showValue, snapValue } from "./configModel";
import { filterPresets, longHex, presetChanges, presetTags } from "./presets";
import type { ThumbState } from "./presetThumbs";

export interface ConfigPanelProps {
  /** "tab": the whole side panel, with its own header and scroll. "inline": inside the
   *  Parameters section of the Scene panel, for an asset that is not a configurator. */
  variant: "tab" | "inline";
  /** The asset's own name, from its manifest. */
  title?: string;
  specs: ParamSpec[];
  values: Record<string, ParamValue>;
  /** Keys whose value differs from the file's. */
  changed: Set<string>;
  onChange(key: string, v: ParamValue): void;
  /** The end of one gesture — a drag let go, a switch flipped — which is one step of undo. */
  onCommit(): void;
  onReset(key: string): void;
  onResetAll(): void;
  canSave: boolean;
  note?: string;
  presets?: PresetSpec[];
  activePreset?: string | null;
  onPreset?(p: PresetSpec): void;
  thumbs?: Record<string, ThumbState>;
  /** Non-empty when there will be no thumbnails, and why. */
  thumbsOff?: string;
  /** Parameters an arrow on the model drives, and along which axis — shown beside the label. */
  handleAxes?: Record<string, "x" | "y" | "z">;
  onUndo?(): void;
}

export function ConfigPanel(p: ConfigPanelProps) {
  const [filter, setFilter] = useState("");
  const [tag, setTag] = useState("");
  const [advanced, setAdvanced] = useSticky("edit.cfg.advanced", false);
  const [shut, setShut] = useState<Record<string, boolean>>({});
  const [applied, setApplied] = useState<{ name: string; n: number; at: number } | null>(null);
  const presets = p.presets || [];

  const sections = useMemo(() => paramSections(p.specs, { filter, advanced }), [p.specs, filter, advanced]);
  const advCount = useMemo(() => advancedCount(p.specs), [p.specs]);
  const tags = useMemo(() => presetTags(presets), [presets]);
  // The text decides whether the Presets section is there at all; the tag only narrows the grid.
  // (Were the tag allowed to hide the section, it would hide its own chips with it.)
  const textPresets = useMemo(() => filterPresets(presets, filter, ""), [presets, filter]);
  const shownPresets = useMemo(() => filterPresets(textPresets, "", tag), [textPresets, tag]);
  const known = useMemo(() => new Set(p.specs.map((s) => s.key)), [p.specs]);
  const changedCount = [...p.changed].filter((k) => known.has(k)).length;
  const filtering = !!filter.trim();
  // Hidden matches a section already explains on its own line ("2 advanced settings — show") are
  // not repeated at the bottom; only those inside sections that also show something are.
  const hiddenMatches = advanced ? 0 : sections.filter((s) => s.shown.length > 0).reduce((n, s) => n + s.hidden, 0);
  const nothing = filtering && !sections.length && !textPresets.length;

  // The "applied" note goes away on its own; undo stays on the toolbar and Ctrl+Z for ever.
  useEffect(() => {
    if (!applied) return;
    const t = window.setTimeout(() => setApplied(null), 5000);
    return () => window.clearTimeout(t);
  }, [applied]);

  const isOpen = (id: string) => filtering || !shut[id];
  const toggle = (id: string) => setShut((s) => ({ ...s, [id]: !s[id] }));

  if (!p.specs.length && !presets.length) {
    return (
      <div className="px-2.5 py-3 text-[11px] text-muted leading-relaxed">
        No parameters found. The editor reads the top-level constants of the source file, so
        <span className="text-text font-mono"> const SAIL_HEIGHT = 0.9;</span> becomes a slider on its own,
        and in a scene it reads what each placed asset was handed, so
        <span className="text-text font-mono"> buildLizard(THREE, {"{ sailHeight: 1.2 }"})</span> gives that
        lizard its own. Nothing in this file is written either way yet.
      </div>
    );
  }

  const apply = (preset: PresetSpec) => {
    const n = presetChanges(preset, p.specs, p.values).length;
    p.onPreset?.(preset);
    setApplied({ name: preset.name, n, at: Date.now() });
  };

  // The filter gets a row of its own: squeezed beside two buttons in a panel this narrow, even
  // its placeholder was cut in half. Under it, what the panel is about and the two switches.
  const search = (
    <div className="relative">
      <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted/60 pointer-events-none" />
      <input value={filter} onChange={(e) => setFilter(e.target.value)} spellCheck={false}
        onKeyDown={(e) => { if (e.key === "Escape") setFilter(""); e.stopPropagation(); }}
        placeholder={presets.length ? "Filter options and presets…" : "Filter options…"}
        className="w-full h-7 pl-6 pr-6 rounded-md bg-panel2 border border-line text-[11px] text-text outline-none placeholder:text-muted/60 focus:border-brand/60" />
      {filter && (
        <button onClick={() => setFilter("")} title="Clear the filter  (Esc)"
          className="absolute right-1 top-1/2 -translate-y-1/2 p-0.5 rounded text-muted hover:text-text"><X size={11} /></button>
      )}
    </div>
  );
  const switches = (
    <>
      {advCount > 0 && (
        <label className="shrink-0 h-6 inline-flex items-center gap-1.5 cursor-pointer select-none"
          title={advanced ? "Hide the " + advCount + " advanced setting" + (advCount === 1 ? "" : "s")
            : advCount + " advanced setting" + (advCount === 1 ? " is" : "s are") + " hidden — the ones most people never touch"}>
          <Toggle on={advanced} onChange={setAdvanced} small />
          <span className={cls("text-[11px]", advanced ? "text-text" : "text-muted")}>Advanced</span>
        </label>
      )}
      <button onClick={p.onResetAll} disabled={!changedCount}
        title={changedCount ? "Put all " + changedCount + " changed setting" + (changedCount === 1 ? "" : "s") + " back to the file's values  (undo brings them back)" : "Nothing has been changed"}
        className={cls("shrink-0 h-6 px-2 rounded-md border text-[11px] inline-flex items-center gap-1 transition-colors",
          changedCount ? "border-line bg-panel2/60 text-text hover:border-brand/50" : "border-transparent text-muted/40 cursor-default")}>
        <RotateCcw size={11} /> Reset all
      </button>
    </>
  );
  const status = (
    <span className="min-w-0 flex-1 truncate text-[10px]">
      {changedCount > 0
        ? <span className="text-brand tabular-nums" title="Settings that differ from the file">{changedCount} changed</span>
        : <span className="text-muted/60">{p.title || (p.specs.length + " setting" + (p.specs.length === 1 ? "" : "s"))}</span>}
    </span>
  );

  const header = p.variant === "tab" ? (
    <div className="shrink-0 px-2.5 pt-2 pb-1.5 space-y-1.5 border-b border-line bg-panel">
      {search}
      <div className="flex items-center gap-2 min-w-0">{status}{switches}</div>
    </div>
  ) : (
    // The same height whether or not anything has changed yet: a row that appeared with the first
    // change would jump the whole list down under a slider that is being dragged.
    <div className="px-2.5 pt-2 pb-1 space-y-1.5">
      {p.specs.length > 5 && search}
      {p.specs.length > 0 && <div className="flex items-center gap-2 min-w-0">{status}{switches}</div>}
    </div>
  );

  const body = (
    <>
      {p.note && <div className="px-2.5 pt-2 text-[10px] text-warn leading-snug">{p.note}</div>}

      {presets.length > 0 && (!filtering || textPresets.length > 0) && (
        <CfgSection title="Presets" open={isOpen("__presets")} onToggle={() => toggle("__presets")}
          count={filtering || tag ? shownPresets.length + "/" + presets.length : String(presets.length)}>
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1 pb-2">
              {[{ tag: "", label: "All", count: presets.length }, ...tags.map((t) => ({ tag: t.tag, label: labelOf(t.tag), count: t.count }))].map((c) => (
                <button key={c.tag || "__all"} onClick={() => setTag(c.tag)} aria-pressed={tag === c.tag}
                  className={cls("h-[22px] px-2 rounded-full border text-[10px] inline-flex items-center gap-1 transition-colors",
                    tag === c.tag ? "bg-brand/20 border-brand/60 text-text" : "bg-panel2/60 border-line text-muted hover:text-text hover:border-muted/50")}>
                  {c.label}
                  <span className="text-[9px] opacity-60 tabular-nums">{c.count}</span>
                </button>
              ))}
            </div>
          )}
          {shownPresets.length ? (
            <div className="grid grid-cols-3 gap-1.5 max-h-[284px] overflow-y-auto -mr-1 pr-1 pb-0.5">
              {shownPresets.map((preset) => (
                <PresetTile key={preset.name} preset={preset} state={p.thumbs?.[preset.name]} off={!!p.thumbsOff}
                  active={p.activePreset === preset.name} onApply={() => apply(preset)}
                  title={describePreset(preset, p.specs) + (p.thumbs?.[preset.name]?.status === "failed"
                    ? "\n\nthe thumbnail could not be made: " + (p.thumbs?.[preset.name]?.error || "unknown error") : "")} />
              ))}
            </div>
          ) : (
            <div className="py-2 text-[11px] text-muted">
              No preset tagged {"“" + labelOf(tag) + "”"}{filtering ? " matches the filter" : ""}.{" "}
              <button onClick={() => setTag("")} className="text-brand hover:underline">Show all</button>
            </div>
          )}
          {p.thumbsOff && <div className="pt-1.5 text-[10px] text-muted/70 leading-snug">{p.thumbsOff}</div>}
        </CfgSection>
      )}

      {sections.map((s) => {
        const inSection = p.specs.filter((x) => (x.group || "") === s.id);
        const changedHere = inSection.filter((x) => p.changed.has(x.key)).length;
        return (
          <CfgSection key={"g:" + s.id} title={s.title} open={isOpen("g:" + s.id)} onToggle={() => toggle("g:" + s.id)}
            count={String(s.shown.length)} dot={changedHere > 0 ? changedHere + " changed in " + s.title : ""}
            hidden={s.hidden} onShowHidden={() => setAdvanced(true)} sub={p.variant === "inline"}>
            {s.shown.map((spec) => (
              <ParamRow key={spec.key} spec={spec} value={p.values[spec.key] ?? spec.value} changed={p.changed.has(spec.key)}
                axis={p.handleAxes?.[spec.key]}
                onChange={(v) => p.onChange(spec.key, v)} onCommit={p.onCommit} onReset={() => p.onReset(spec.key)} />
            ))}
            {!s.shown.length && s.hidden > 0 && (
              <button onClick={() => setAdvanced(true)} className="text-[10px] text-muted hover:text-brand py-1">
                {s.hidden} advanced setting{s.hidden === 1 ? "" : "s"} — show
              </button>
            )}
          </CfgSection>
        );
      })}

      {filtering && hiddenMatches > 0 && (
        <button onClick={() => setAdvanced(true)} className="w-full px-2.5 py-2 text-left text-[10px] text-muted hover:text-brand">
          {hiddenMatches} more match{hiddenMatches === 1 ? "" : "es"} among the advanced settings — show {hiddenMatches === 1 ? "it" : "them"}
        </button>
      )}
      {nothing && (
        <div className="px-2.5 py-4 text-[11px] text-muted">
          Nothing matches <span className="text-text">{"“" + filter.trim() + "”"}</span>.{" "}
          <button onClick={() => setFilter("")} className="text-brand hover:underline">Clear the filter</button>
        </div>
      )}
      {!p.canSave && changedCount > 0 && (
        <div className="px-2.5 py-2 text-[10px] text-muted leading-snug">
          These changes are live only. Open the asset from its source file to write them back.
        </div>
      )}
    </>
  );

  const toast = applied && (
    <div className="shrink-0 mx-2 mb-2 mt-1 px-2.5 py-1.5 rounded-md border border-brand/40 bg-brand/[0.12] flex items-center gap-2 text-[11px]">
      <Check size={12} className="text-brand shrink-0" />
      <span className="min-w-0 truncate text-text">
        {applied.name}
        <span className="text-muted">{applied.n ? " — " + applied.n + " setting" + (applied.n === 1 ? "" : "s") + " changed" : " — already set"}</span>
      </span>
      {applied.n > 0 && p.onUndo && (
        <button onClick={() => { p.onUndo?.(); setApplied(null); }} title="Undo  (Ctrl+Z)"
          className="ml-auto shrink-0 inline-flex items-center gap-1 text-brand hover:text-text">
          <Undo2 size={11} /> Undo
        </button>
      )}
    </div>
  );

  return p.variant === "tab" ? (
    <div className="flex flex-col h-full min-h-0">
      {header}
      <div className="flex-1 min-h-0 overflow-y-auto">{body}</div>
      {toast}
    </div>
  ) : (
    <div>
      {header}
      {body}
      {toast}
    </div>
  );
}

// ------------------------------------------------------------------ section

function CfgSection({ title, count, open, onToggle, dot, hidden = 0, onShowHidden, sub, children }: {
  title: string; count: string; open: boolean; onToggle(): void; dot?: string;
  hidden?: number; onShowHidden?(): void;
  /** Inside the Scene panel's own Parameters section: a sub-heading, quieter than its parent. */
  sub?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-line/60 last:border-b-0">
      <div role="button" tabIndex={0} onClick={onToggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onToggle(); } }}
        className={cls("w-full px-2.5 flex items-center gap-1.5 cursor-pointer select-none hover:bg-panel2/50 outline-none focus-visible:bg-panel2/60",
          sub ? "h-7" : "h-8")}>
        <ChevronRight size={12} className={cls("shrink-0 text-muted transition-transform", open && "rotate-90")} />
        <span className={cls("text-[10px] font-semibold uppercase tracking-[0.14em] truncate", sub ? "text-muted" : "text-text/85")}>{title}</span>
        {hidden > 0 && (
          <button onClick={(e) => { e.stopPropagation(); onShowHidden?.(); }}
            title={hidden + " advanced setting" + (hidden === 1 ? "" : "s") + " hidden here — click to show"}
            className="shrink-0 text-[10px] text-muted/70 hover:text-brand tabular-nums">+{hidden}</button>
        )}
        {dot && <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-brand" title={dot} />}
        {count !== "0" && (
          <span className="ml-auto shrink-0 min-w-[20px] h-4 px-1.5 rounded-full bg-panel2 border border-line text-[9px] text-muted tabular-nums inline-flex items-center justify-center">
            {count}
          </span>
        )}
      </div>
      {open && <div className="px-2.5 pb-2.5 pt-0.5">{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ one parameter

function ParamRow({ spec, value, changed, axis, onChange, onCommit, onReset }: {
  spec: ParamSpec; value: ParamValue; changed: boolean; axis?: "x" | "y" | "z";
  onChange(v: ParamValue): void; onCommit(): void; onReset(): void;
}) {
  const hint = [
    spec.label + "  ·  " + spec.key,
    spec.kind === "number" && spec.min !== undefined && spec.max !== undefined ? "range " + spec.min + " to " + spec.max + (spec.step ? ", steps of " + spec.step : "") : "",
    spec.kind === "choice" ? (spec.options || []).length + " options" : "",
    spec.advanced ? "an advanced setting" : "",
    changed ? "changed — the file says " + showValue(spec, spec.value) : "",
    axis ? "an arrow on the model drives this: drag it along " + axis.toUpperCase() : "",
  ].filter(Boolean).join("\n");
  const Arrow = axis === "y" ? MoveVertical : MoveHorizontal;
  return (
    <div className="flex items-center gap-2 min-h-[30px]">
      <div className="w-[40%] shrink-0 min-w-0 flex items-center gap-1.5">
        <span className={cls("w-1.5 h-1.5 rounded-full shrink-0 transition-colors", changed ? "bg-brand" : "bg-line/80")} />
        <span title={hint} className={cls("text-[11px] truncate", changed ? "text-text" : "text-muted")}>{spec.label}</span>
        {axis && <Arrow size={10} className="shrink-0 text-brand/70" aria-label="driven by a handle" />}
        {changed && (
          <button onClick={onReset} title={"Back to the value in the file: " + showValue(spec, spec.value)}
            className="shrink-0 p-0.5 -m-0.5 rounded text-muted/70 hover:text-text"><RotateCcw size={10} /></button>
        )}
      </div>
      <div className="flex-1 min-w-0">
        {spec.kind === "number" && <NumberControl spec={spec} value={Number(value)} onChange={onChange} onCommit={onCommit} />}
        {spec.kind === "bool" && (
          <div className="h-6 flex items-center">
            <Toggle on={!!value} onChange={(v) => { onChange(v); onCommit(); }} title={spec.label + ": " + (value ? "on" : "off")} />
          </div>
        )}
        {spec.kind === "choice" && <ChoiceSelect spec={spec} value={String(value)} onChange={(v) => { onChange(v); onCommit(); }} />}
        {spec.kind === "color" && <ColorField value={String(value)} onChange={onChange} onCommit={onCommit} />}
        {spec.kind === "text" && (
          <input value={String(value)} onChange={(e) => onChange(e.target.value)} onBlur={onCommit} spellCheck={false}
            className="w-full h-7 px-2 rounded-md bg-panel2 border border-line text-[11px] font-mono text-text outline-none focus:border-brand/60" />
        )}
      </div>
    </div>
  );
}

/** A slider to explore with and a field to type the exact number into, side by side. A declared
 *  parameter is held to the author's range; a detected one is not, because its range is only the
 *  editor's guess (kit.ts `rangeFor`) and a wrong guess must cost a keystroke, not the edit. */
function NumberControl({ spec, value, onChange, onCommit }: {
  spec: ParamSpec; value: number; onChange(v: number): void; onCommit(): void;
}) {
  const min = spec.min, max = spec.max;
  const ranged = min !== undefined && max !== undefined && Number.isFinite(min) && Number.isFinite(max) && max > min;
  const hard = spec.from === "declared";
  const d = decimalsFor(spec.step);
  const field = (
    <NumField value={value} step={spec.step} decimals={d} label={undefined}
      min={hard ? min : undefined} max={hard ? max : undefined}
      onChange={onChange} onCommit={() => onCommit()} />
  );
  if (!ranged) return field;
  return (
    <div className="flex items-center gap-2">
      <Slider value={value} min={min!} max={max!} step={spec.step} label={spec.label} onChange={onChange} onCommit={onCommit} />
      <div className="w-[3.4rem] shrink-0">{field}</div>
    </div>
  );
}

function Slider({ value, min, max, step, label, onChange, onCommit }: {
  value: number; min: number; max: number; step?: number; label: string;
  onChange(v: number): void; onCommit(): void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef(false);
  const [held, setHeld] = useState(false);
  const span = max - min;
  const pct = span > 0 && Number.isFinite(value) ? Math.min(1, Math.max(0, (value - min) / span)) : 0;
  const at = (x: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return value;
    const u = Math.min(1, Math.max(0, (x - r.left) / Math.max(1, r.width)));
    return snapValue(min + u * span, min, max, step);
  };
  const set = (v: number) => { if (v !== value) onChange(v); };
  const end = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = false;
    setHeld(false);
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* never held */ }
    onCommit();
  };
  return (
    <div ref={ref} role="slider" tabIndex={0} aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value}
      title={label + " — drag, or use the arrow keys (Shift for ten steps)"}
      className="group/sl relative flex-1 min-w-0 h-6 cursor-pointer touch-none select-none outline-none"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        drag.current = true;
        setHeld(true);
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* a nicety */ }
        set(at(e.clientX));
      }}
      onPointerMove={(e) => { if (drag.current) set(at(e.clientX)); }}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={(e) => {
        const st = step && step > 0 ? step : span / 100;
        const k = e.shiftKey ? 10 : 1;
        let v: number | null = null;
        if (e.key === "ArrowRight" || e.key === "ArrowUp") v = value + st * k;
        else if (e.key === "ArrowLeft" || e.key === "ArrowDown") v = value - st * k;
        else if (e.key === "PageUp") v = value + st * 10;
        else if (e.key === "PageDown") v = value - st * 10;
        else if (e.key === "Home") v = min;
        else if (e.key === "End") v = max;
        if (v === null) return;
        e.preventDefault();
        e.stopPropagation();
        set(snapValue(v, min, max, step));
        onCommit();
      }}>
      <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-[3px] rounded-full bg-line" />
      <div className="absolute left-0 top-1/2 -translate-y-1/2 h-[3px] rounded-full bg-brand/75" style={{ width: (pct * 100).toFixed(2) + "%" }} />
      <div className={cls("absolute top-1/2 w-3 h-3 -mt-1.5 -ml-1.5 rounded-full bg-text border-2 border-brand shadow-[0_1px_3px_rgba(0,0,0,0.55)] transition-transform",
        held ? "scale-125" : "group-hover/sl:scale-110 group-focus-visible/sl:ring-2 group-focus-visible/sl:ring-brand/50")}
        style={{ left: (pct * 100).toFixed(2) + "%" }} />
    </div>
  );
}

/** A real switch. `role="switch"`, so it is announced as one and Space flips it. */
export function Toggle({ on, onChange, title, small }: { on: boolean; onChange(v: boolean): void; title?: string; small?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} title={title}
      onClick={(e) => { e.preventDefault(); onChange(!on); }}
      className={cls("relative inline-flex shrink-0 items-center rounded-full border transition-colors",
        small ? "w-7 h-4" : "w-8 h-[18px]",
        on ? "bg-brand/85 border-brand" : "bg-panel2 border-line hover:border-muted/60")}>
      <span className={cls("absolute top-1/2 -translate-y-1/2 rounded-full shadow transition-all",
        small ? "w-3 h-3" : "w-3.5 h-3.5",
        on ? (small ? "left-[13px] bg-white" : "left-[15px] bg-white") : "left-[1px] bg-muted")} />
    </button>
  );
}

/** A dropdown of the options, in their order. A value that is not one of them — a preset or a
 *  sidecar written against an older list — is shown as what it is, marked, instead of being
 *  swapped for the first option behind the person's back. */
function ChoiceSelect({ spec, value, onChange }: { spec: ParamSpec; value: string; onChange(v: string): void }) {
  const options = spec.options || [];
  const unknown = !options.includes(value);
  return (
    <div className="relative">
      <select value={value} onChange={(e) => onChange(e.target.value)}
        title={unknown ? "“" + value + "” is not one of this setting's options — pick one" : spec.label}
        className={cls("w-full h-7 pl-2 pr-7 rounded-md bg-panel2 border text-[11px] text-text appearance-none cursor-pointer outline-none transition-colors focus:border-brand/60",
          unknown ? "border-warn/60" : "border-line hover:border-brand/40")}>
        {unknown && <option value={value} disabled>{value} — not an option</option>}
        {options.map((o) => <option key={o} value={o}>{prettyOption(o)}</option>)}
      </select>
      <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-muted" />
    </div>
  );
}

/** A swatch that opens the picker, and the hex beside it to type into. Only a whole #rrggbb is
 *  ever handed on — a half-typed colour is not a colour. */
function ColorField({ value, onChange, onCommit }: { value: string; onChange(v: string): void; onCommit(): void }) {
  const hex = longHex(value);
  const valid = /^#[0-9a-f]{6}$/.test(hex);
  const [draft, setDraft] = useState<string | null>(null);
  const cancel = useRef(false);
  const finish = () => {
    const t = (draft ?? "").trim();
    setDraft(null);
    if (cancel.current) { cancel.current = false; return; }
    const h = longHex(t.startsWith("#") ? t : "#" + t);
    if (/^#[0-9a-f]{6}$/.test(h) && h !== hex) { onChange(h); onCommit(); }
  };
  return (
    <div className="flex items-center gap-1.5">
      <label title="Pick a colour"
        className="relative w-8 h-7 shrink-0 rounded-md border border-line overflow-hidden cursor-pointer hover:border-brand/50 shadow-inner"
        style={{ background: valid ? hex : "repeating-conic-gradient(#3a3f4d 0% 25%, #2a2e39 0% 50%) 50% / 8px 8px" }}>
        <input type="color" value={valid ? hex : "#ffffff"} onChange={(e) => onChange(e.target.value)} onBlur={onCommit}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" />
      </label>
      <input value={draft ?? value} spellCheck={false}
        onFocus={() => setDraft(value)} onChange={(e) => setDraft(e.target.value)} onBlur={finish}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") { cancel.current = true; (e.target as HTMLInputElement).blur(); }
        }}
        className={cls("flex-1 min-w-0 h-7 px-2 rounded-md bg-panel2 border text-[11px] font-mono text-text outline-none focus:border-brand/60",
          valid ? "border-line" : "border-warn/60")} />
    </div>
  );
}

// ------------------------------------------------------------------ presets

function PresetTile({ preset, state, active, off, title, onApply }: {
  preset: PresetSpec; state?: ThumbState; active: boolean; off: boolean; title: string; onApply(): void;
}) {
  const status = off ? "off" : state?.status || "pending";
  return (
    <button type="button" onClick={onApply} title={title} aria-pressed={active}
      className={cls("group/tile relative flex flex-col rounded-md border overflow-hidden text-left transition-colors",
        active ? "border-brand ring-1 ring-brand/70 bg-brand/[0.08]" : "border-line bg-panel2/40 hover:border-brand/50 hover:bg-panel2")}>
      <div className="relative w-full aspect-[4/3] overflow-hidden bg-[#1d222b]">
        {status === "ready" && state?.url && (
          <img src={state.url} alt="" draggable={false}
            className="absolute inset-0 w-full h-full object-cover transition-transform duration-300 group-hover/tile:scale-[1.05]" />
        )}
        {status === "pending" && (
          <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-b from-panel2/40 to-transparent">
            <div className="absolute inset-0 animate-pulse bg-panel2/40" />
            <Loader2 size={12} className="relative animate-spin text-muted/60" />
          </div>
        )}
        {status === "failed" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-0.5 text-warn bg-warn/[0.07]">
            <AlertTriangle size={14} />
            <span className="text-[9px] font-medium">did not build</span>
          </div>
        )}
        {status === "off" && (
          <div className="absolute inset-0 flex items-center justify-center text-muted/35"><Box size={18} /></div>
        )}
        {active && (
          <span className="absolute top-1 right-1 w-4 h-4 rounded-full bg-brand text-bg flex items-center justify-center shadow">
            <Check size={10} strokeWidth={3} />
          </span>
        )}
      </div>
      <div className={cls("px-1.5 pt-1 pb-1.5 text-[10px] leading-[1.25] line-clamp-2 min-h-[31px]",
        active ? "text-text" : "text-muted group-hover/tile:text-text")}>
        {preset.name}
      </div>
    </button>
  );
}

// ------------------------------------------------------------------ over the viewport

/** The five views, as the viewport names them (world.ts VIEWS). HERO is the three-quarter view
 *  the preset thumbnails are photographed from, so what the grid shows is one click away. */
export const VIEW_BUTTONS: Array<{ id: string; label: string; hint: string }> = [
  { id: "3q", label: "Hero", hint: "Hero — the three-quarter view the preset thumbnails are taken from" },
  { id: "front", label: "Front", hint: "Front  (numpad 1)" },
  { id: "right", label: "Right", hint: "Right  (numpad 3)" },
  { id: "back", label: "Rear", hint: "Rear  (Ctrl + numpad 1)" },
  { id: "top", label: "Top", hint: "Top  (numpad 7)" },
];

export function ConfigBar({ view, onView, look, onLook, nightParam, handles, onHandles, declared, usable }: {
  view: string; onView(id: string): void;
  look: "day" | "night"; onLook(l: "day" | "night"): void;
  /** The asset has a `night` setting that the switch sets too. */
  nightParam: boolean;
  handles: boolean; onHandles(on: boolean): void;
  /** Handles the manifest declares, and how many of them bind to a number parameter. */
  declared: number; usable: string[];
}) {
  const group = "flex items-center gap-0.5 p-[2px] rounded-md bg-panel2/70 border border-line";
  const btn = (on: boolean) => cls("h-6 px-2.5 rounded text-[10px] font-semibold uppercase tracking-[0.12em] inline-flex items-center gap-1.5 transition-colors",
    on ? "bg-brand/25 text-text ring-1 ring-inset ring-brand/45" : "text-muted hover:text-text hover:bg-panel2");
  return (
    // A STRIP ABOVE THE VIEWPORT, not a bar floating over it. It floated at first, like the demo
    // it answers — and the moment the building grew two floors its roof and the floors handle rose
    // under the bar, because the camera (rightly) does not re-frame when a parameter moves. Any
    // overlay across the top band has that fault for some model; a strip has it for none.
    <div className="h-9 shrink-0 px-3 flex items-center justify-center gap-2 border-b border-line bg-panel select-none overflow-x-auto no-scrollbar">
      <div className={group} role="group" aria-label="View">
        {VIEW_BUTTONS.map((v) => (
          <button key={v.id} onClick={() => onView(v.id)} title={v.hint} aria-pressed={view === v.id} className={btn(view === v.id)}>
            {v.label}
          </button>
        ))}
      </div>
      <div className={group} role="group" aria-label="Lighting">
        <button onClick={() => onLook("day")} aria-pressed={look === "day"} className={btn(look === "day")}
          title="Day — the studio rig the forge photographs with">
          <Sun size={11} /> Day
        </button>
        <button onClick={() => onLook("night")} aria-pressed={look === "night"} className={btn(look === "night")}
          title={"Night — the studio dimmed to moonlight" + (nightParam ? ", and this asset's own Night setting switched on, so it can light itself" : "")}>
          <Moon size={11} /> Night
        </button>
      </div>
      {declared > 0 && (
        <div className={group}>
          <button onClick={() => onHandles(!handles)} aria-pressed={handles} className={btn(handles)}
            title={usable.length
              ? (handles ? "Hide the arrows on the model" : "Show arrows on the model") + " — drag one to change " + usable.join(", ")
              : "This asset declares " + declared + " handle" + (declared === 1 ? "" : "s") + ", and none of them drives a number parameter yet"}>
            <Move size={11} /> Handles
          </button>
        </div>
      )}
    </div>
  );
}

/** The side panel's two tabs, for a configurator asset: its options, and the scene as before. */
export function SideTabs({ tab, onTab, options, scene, selected }: {
  tab: "options" | "scene"; onTab(t: "options" | "scene"): void; options: number; scene: number; selected: number;
}) {
  const item = (id: "options" | "scene", icon: React.ReactNode, label: string, count: number, extra?: React.ReactNode) => (
    <button onClick={() => onTab(id)} aria-pressed={tab === id}
      className={cls("flex-1 h-full inline-flex items-center justify-center gap-1.5 border-b-2 -mb-px text-[11px] font-semibold uppercase tracking-[0.1em] transition-colors",
        tab === id ? "text-text border-brand" : "text-muted border-transparent hover:text-text")}>
      {icon}
      {label}
      <span className="text-[10px] font-normal normal-case tracking-normal tabular-nums text-muted/70">{count}</span>
      {extra}
    </button>
  );
  return (
    <div className="h-8 shrink-0 flex items-stretch border-b border-line bg-panel">
      {item("options", <SlidersHorizontal size={12} />, "Options", options)}
      {item("scene", <Layers size={12} />, "Scene", scene,
        selected > 0 ? <span className="px-1 rounded bg-brand/20 text-brand text-[9px] font-medium normal-case tracking-normal">{selected} selected</span> : null)}
    </div>
  );
}
