// The configurator panel's pure half: which sections the parameters fall into, what the filter
// and the Advanced switch leave showing, how a value reads in a sentence, and where a slider
// lands. No React and no engine here, so every one of these rules is tested in presets.test.ts
// instead of being discovered by clicking.

import type { PresetSpec } from "./configurator";
import { NIGHT_PARAM } from "./configurator";
import type { ParamSpec, ParamValue } from "./kit";
import { labelOf } from "./kit";
import { presetEntries } from "./presets";

/** The title of the section that holds the parameters no group claimed. */
export const GENERAL = "General";

export interface ParamSection {
  /** The group exactly as the manifest wrote it; "" for the ungrouped parameters. */
  id: string;
  /** What the header says: the group as a label, or "General". */
  title: string;
  /** What the panel draws, in declaration order. */
  shown: ParamSpec[];
  /** Advanced settings in this section that match the filter but the switch is hiding. */
  hidden: number;
  /** Every parameter in the section, filter or no filter. */
  total: number;
}

function words(text: string | undefined): string[] {
  return String(text || "").toLowerCase().split(/\s+/).filter(Boolean);
}

/** Every word is found in at least one of the fields. */
export function matchesWords(ws: string[], fields: Array<string | undefined>): boolean {
  if (!ws.length) return true;
  const hay = fields.filter(Boolean).map((f) => String(f).toLowerCase());
  return ws.every((w) => hay.some((h) => h.includes(w)));
}

/**
 * The parameters as the panel lays them out: one section per group, in the order the groups are
 * first declared, the ungrouped ones in "General" wherever the first of them was declared.
 *
 * The filter matches a parameter's label, its key, or its section's title — so typing "facade"
 * shows the whole Facade section. The Advanced switch hides advanced settings; a section keeps its
 * header while it has any match at all, so the "+2" of what is hidden is still there to click. A
 * section with no match disappears while the filter is on.
 */
export function paramSections(specs: ParamSpec[], opts: { filter?: string; advanced?: boolean } = {}): ParamSection[] {
  const ws = words(opts.filter);
  const order: string[] = [];
  const by = new Map<string, ParamSpec[]>();
  for (const s of specs) {
    const g = s.group || "";
    if (!by.has(g)) { by.set(g, []); order.push(g); }
    by.get(g)!.push(s);
  }
  const out: ParamSection[] = [];
  for (const id of order) {
    const all = by.get(id)!;
    const title = id ? labelOf(id) : GENERAL;
    const matching = all.filter((s) => matchesWords(ws, [s.label, s.key, title, id]));
    const shown = matching.filter((s) => opts.advanced || !s.advanced);
    const hidden = matching.length - shown.length;
    if (!shown.length && !hidden) continue;
    out.push({ id, title, shown, hidden, total: all.length });
  }
  return out;
}

/** How many advanced settings the asset has — the Advanced switch is only drawn when this is > 0. */
export function advancedCount(specs: ParamSpec[]): number {
  return specs.filter((s) => s.advanced).length;
}

/** An option as a person reads it. An identifier-shaped option — "shop", "mixed_use",
 *  "twoStorey" — becomes "Shop", "Mixed use", "Two storey"; anything already written for people
 *  ("Shop / mixed use", "LED") is shown exactly as written. */
export function prettyOption(opt: string): string {
  const s = String(opt);
  if (/^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$/.test(s) || /^[a-z]+[A-Z][A-Za-z0-9]*$/.test(s)) return labelOf(s);
  return s;
}

/** How many decimals a step implies: 0.05 -> 2, 0.1 -> 1, 1 -> 0, 1e-7 -> 6 (the cap). */
export function decimalsFor(step?: number): number {
  if (!step || !Number.isFinite(step) || step <= 0) return 3;
  const t = String(step);
  const e = /e-(\d+)$/.exec(t);
  if (e) return Math.min(6, parseInt(e[1], 10));
  const dot = t.indexOf(".");
  return dot < 0 ? 0 : Math.min(6, t.length - dot - 1);
}

/** Where a slider lands for a raw position: clamped to the range, snapped to the step counted
 *  from `min` (so a range of 3.6..12 in steps of 0.1 never offers 3.65), and rounded to the step's
 *  own decimals so 0.1 + 0.2 never shows up as 0.30000000000000004. The author's `max` is always
 *  reachable, even when the step does not divide the range: past the last step it is the max. */
export function snapValue(v: number, min: number, max: number, step?: number): number {
  if (!Number.isFinite(v)) return Math.min(min, max);
  const lo = Math.min(min, max), hi = Math.max(min, max);
  let out = Math.min(hi, Math.max(lo, v));
  if (step && step > 0) out = Math.min(hi, lo + Math.round((out - lo) / step) * step);
  const m = Math.pow(10, Math.max(decimalsFor(step), decimalsOfNumber(lo), out === hi ? decimalsOfNumber(hi) : 0));
  return Math.round(out * m) / m;
}

/** Decimals a number is written with, capped at 6: 3.6 -> 1, 12 -> 0. */
function decimalsOfNumber(n: number): number {
  if (!Number.isFinite(n)) return 0;
  const t = String(n);
  const e = /e-(\d+)$/.exec(t);
  if (e) return Math.min(6, parseInt(e[1], 10));
  const dot = t.indexOf(".");
  return dot < 0 ? 0 : Math.min(6, t.length - dot - 1);
}

/** A value as the panel writes it in a sentence — the tooltip of a preset, the "changed from". */
export function showValue(spec: ParamSpec, v: ParamValue | undefined): string {
  if (v === undefined) return "—";
  if (spec.kind === "bool") return v ? "on" : "off";
  if (spec.kind === "choice") return prettyOption(String(v));
  if (spec.kind === "color") return String(v).toLowerCase();
  if (spec.kind === "text") return "“" + String(v) + "”";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  // The step's own decimals, unless that would round the value into a different one — a preset
  // may say 2.5 floors, and the tooltip must say 2.5, not 3.
  const m = Math.pow(10, decimalsFor(spec.step));
  const r = Math.round(n * m) / m;
  return String(Math.abs(r - n) < 1e-9 ? r : Math.round(n * 1e4) / 1e4);
}

/** What a preset will do, for its tooltip: the settings it sets, and the ones it cannot. */
export function describePreset(preset: PresetSpec, specs: ParamSpec[], max = 8): string {
  const byKey = new Map(specs.map((s) => [s.key, s]));
  const { values, rejected } = presetEntries(preset, specs);
  const sets = Object.entries(values).map(([k, v]) => byKey.get(k)!.label + " " + showValue(byKey.get(k)!, v));
  const lines = [preset.name];
  if (preset.note) lines.push(preset.note);
  if (preset.tags.length) lines.push("tags: " + preset.tags.join(", "));
  if (sets.length) lines.push("sets " + sets.slice(0, max).join(" · ") + (sets.length > max ? " · +" + (sets.length - max) + " more" : ""));
  if (rejected.length) lines.push("ignores " + rejected.map((r) => r.key + " (" + r.why + ")").join(", "));
  return lines.join("\n");
}

/** How many handles the manifest DECLARES — before anyone has checked them. The HANDLES switch is
 *  offered when this is > 0; how many survive validation is handles.ts's answer. */
export function declaredHandleCount(manifest: unknown): number {
  const h = manifest && typeof manifest === "object" ? (manifest as { handles?: unknown }).handles : undefined;
  return Array.isArray(h) ? h.length : 0;
}

/** Does this asset get the configurator layout — the Options tab and the view / day-night bar?
 *
 *  Only when it asks for it by using the contract: presets, handles, a choice, an advanced
 *  setting, or the night parameter. An asset with plain parameters keeps the editor exactly as it
 *  was, with its parameters in the side panel where they have always been. */
export function isConfigurator(presets: PresetSpec[], handles: number, specs: ParamSpec[]): boolean {
  return presets.length > 0 || handles > 0
    || specs.some((s) => s.kind === "choice" || s.advanced || (s.key === NIGHT_PARAM && s.kind === "bool"));
}

/** The parameters the configurator panel offers.
 *
 *  The source scan (kit.ts `scanParams`) reads every top-level object of literals as settings —
 *  including `export const manifest = { name: "corner shop", … }`, whose `name` then turns up as a
 *  "parameter" called Manifest › Name. For an asset that declares its settings in that manifest,
 *  the manifest is where the settings come FROM, not one of them, so its own literals are left
 *  out. Only in the configurator layout: every other asset keeps its panel exactly as it was. */
export function panelSpecs(specs: ParamSpec[], configurator: boolean): ParamSpec[] {
  if (!configurator) return specs;
  return specs.filter((s) => !(s.from === "detected" && s.group === "manifest"));
}

/** Where a configurator keeps its model on screen, in normalised device coordinates (-1..1). The
 *  top keeps the most room: the arrow of a height handle sits ON the roof, above the box. */
export const SAFE_FRAME = { side: 0.92, top: 0.8, bottom: 0.92 };

/**
 * Has the model, as its box's corners projected to normalised device coordinates, grown out of
 * the part of the viewport it should stay inside? A corner behind the camera or past the far
 * plane (|z| > 1) counts as out: the camera is then inside or beyond the model.
 *
 * This is what lets a building that gains two floors stay in view: the camera deliberately does
 * not re-frame on a parameter change, so without this the roof, and the floors handle on it, rose
 * off the top of the viewport.
 */
export function leavesFrame(ndc: Array<[number, number, number]>, safe = SAFE_FRAME): boolean {
  for (const [x, y, z] of ndc) {
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
    if (z < -1 || z > 1) return true;
    if (Math.abs(x) > safe.side || y > safe.top || y < -safe.bottom) return true;
  }
  return false;
}

/** The asset has a night switch of its own for the DAY / NIGHT bar to drive. */
export function hasNightParam(specs: ParamSpec[]): boolean {
  return specs.some((s) => s.key === NIGHT_PARAM && s.kind === "bool");
}
