// Presets: a modular asset's saved looks, read from `manifest.presets` (see configurator.ts).
//
// Everything here is PURE — no engine, no DOM, no React — because every rule in it decides what
// gets written into the edit document, and a rule that can only be checked by clicking is a rule
// nobody checks. The thumbnails are elsewhere (presetThumbs.ts); the panel is ConfigPanel.tsx.
//
// The manifest is untrusted input: an asset file may have been written by hand or by an agent, and
// it outlives the parameters it was saved against. So nothing here throws, and nothing here lets a
// value through that the parameter could not have taken from its own control — a number field
// never receives a string, a dropdown never receives a value that is not in its list.

import type { PresetSpec } from "./configurator";
import type { ParamSpec, ParamValue } from "./kit";

/** More than any real kit ships (the demo this is modelled on has 45), few enough that every
 *  thumbnail can still be rendered in idle time. */
export const MAX_PRESETS = 100;
const MAX_TAGS = 12;
const MAX_NAME = 120;
const MAX_TAG = 40;
const MAX_NOTE = 240;
/** Keys that would reach an object's prototype if a value document were ever merged naively. */
const BAD_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** Read `manifest.presets` into clean PresetSpecs. Never throws: a malformed entry is skipped.
 *
 *  An entry is kept when it has a name (non-empty after trimming, and not already taken — the
 *  first of two with one name wins) and at least one usable value: a finite number, a boolean or
 *  a string. Tags are lower-cased, trimmed and de-duplicated; "all" is left out, because that word
 *  is the chip that clears the filter. A note is optional. */
export function presetsFromManifest(manifest: unknown): PresetSpec[] {
  const list = manifest && typeof manifest === "object" ? (manifest as { presets?: unknown }).presets : undefined;
  if (!Array.isArray(list)) return [];
  const out: PresetSpec[] = [];
  const names = new Set<string>();
  for (const raw of list) {
    if (out.length >= MAX_PRESETS) break;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const r = raw as Record<string, unknown>;
    const name = typeof r.name === "string" ? r.name.trim().slice(0, MAX_NAME) : "";
    if (!name || names.has(name)) continue;
    const vals = r.values;
    if (!vals || typeof vals !== "object" || Array.isArray(vals)) continue;
    const values: Record<string, ParamValue> = {};
    for (const [k, v] of Object.entries(vals as Record<string, unknown>)) {
      if (!k || BAD_KEYS.has(k)) continue;
      if ((typeof v === "number" && Number.isFinite(v)) || typeof v === "boolean" || typeof v === "string") values[k] = v;
    }
    if (!Object.keys(values).length) continue;
    names.add(name);
    const note = typeof r.note === "string" && r.note.trim() ? r.note.trim().slice(0, MAX_NOTE) : "";
    const p: PresetSpec = { name, tags: cleanTags(r.tags), values };
    if (note) p.note = note;
    out.push(p);
  }
  return out;
}

function cleanTags(raw: unknown): string[] {
  const list = typeof raw === "string" ? [raw] : Array.isArray(raw) ? raw : [];
  const out: string[] = [];
  for (const t of list) {
    if (typeof t !== "string") continue;
    const s = t.trim().toLowerCase().slice(0, MAX_TAG);
    if (!s || s === "all" || out.includes(s)) continue;
    out.push(s);
    if (out.length >= MAX_TAGS) break;
  }
  return out;
}

/** Why a preset value was refused, for the tooltip — a preset that silently does less than its
 *  name says is worse than one that says what it skipped. */
export interface Refusal { key: string; why: string }

/** The part of a preset THIS asset can take, and what it refused.
 *
 *  A key the asset has no parameter for is ignored (presets outlive their parameters, see
 *  configurator.ts). A number parameter takes only a finite number, a switch only a boolean, a
 *  choice only one of its own options, a colour only #rgb or #rrggbb (normalised to lower-case
 *  #rrggbb, the one spelling the write-back and the colour picker both understand), text only a
 *  string. Numbers are NOT clamped to the slider: the range of a slider is a convenience, the
 *  preset is the author's word. */
export function presetEntries(preset: PresetSpec, specs: ParamSpec[]): { values: Record<string, ParamValue>; rejected: Refusal[] } {
  const byKey = new Map(specs.map((s) => [s.key, s]));
  const values: Record<string, ParamValue> = {};
  const rejected: Refusal[] = [];
  for (const [key, v] of Object.entries(preset?.values || {})) {
    const s = byKey.get(key);
    if (!s) { rejected.push({ key, why: "not a parameter of this asset" }); continue; }
    const why = refusal(s, v);
    if (why) { rejected.push({ key, why }); continue; }
    values[key] = s.kind === "color" ? longHex(String(v)) : v;
  }
  return { values, rejected };
}

function refusal(s: ParamSpec, v: unknown): string {
  switch (s.kind) {
    case "number": return typeof v === "number" && Number.isFinite(v) ? "" : "needs a number";
    case "bool": return typeof v === "boolean" ? "" : "needs true or false";
    case "choice": return typeof v === "string" && (s.options || []).includes(v) ? "" : "not one of its options";
    case "color": return typeof v === "string" && HEX.test(v) ? "" : "needs a colour written #rrggbb";
    case "text": return typeof v === "string" ? "" : "needs text";
    default: return "a kind of parameter the editor does not know";
  }
}

/** #abc -> #aabbcc, and lower-case either way. Anything else comes back as it went in. */
export function longHex(v: string): string {
  if (!HEX.test(v)) return v;
  const h = v.slice(1).toLowerCase();
  return "#" + (h.length === 3 ? h.split("").map((c) => c + c).join("") : h);
}

/** A preset laid over the current values: the keys it may set are replaced, every other value is
 *  left exactly as it was. `applied` lists what changed hands, `rejected` what was refused. */
export function applyPreset(current: Record<string, ParamValue>, preset: PresetSpec, specs: ParamSpec[]):
  { values: Record<string, ParamValue>; applied: string[]; rejected: Refusal[] } {
  const { values, rejected } = presetEntries(preset, specs);
  return { values: { ...current, ...values }, applied: Object.keys(values), rejected };
}

/** Two values of one parameter are the same value. Numbers within a billionth (relative) are —
 *  a slider can land on 5.800000000000001, and that is still the preset's 5.8. Colours compare
 *  without regard to case or to the #abc shorthand. Everything else must be identical. */
export function sameValue(spec: ParamSpec | undefined, a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") {
    if (a === b) return true;
    if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
    return Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
  }
  if (spec?.kind === "color" && typeof a === "string" && typeof b === "string") return longHex(a) === longHex(b);
  return a === b;
}

/** The keys applying this preset would actually change, in the preset's own order — what the
 *  panel means by "2 settings changed", and nothing it would set to the value already there. */
export function presetChanges(preset: PresetSpec, specs: ParamSpec[], current: Record<string, ParamValue>): string[] {
  const byKey = new Map(specs.map((s) => [s.key, s]));
  const { values } = presetEntries(preset, specs);
  return Object.keys(values).filter((k) => !sameValue(byKey.get(k), current[k], values[k]));
}

/** Which preset the current values ARE, or null.
 *
 *  A preset matches when every value it can set equals the current one; values it does not name
 *  do not matter, because applying it would not touch them either. When more than one matches,
 *  the one that pins down the most parameters wins (the more specific look), and then the first.
 *  A preset with nothing usable in it matches nothing — otherwise it would match everything. */
export function matchPreset(presets: PresetSpec[], current: Record<string, ParamValue>, specs: ParamSpec[]): string | null {
  const byKey = new Map(specs.map((s) => [s.key, s]));
  let best: string | null = null;
  let most = 0;
  for (const p of presets) {
    const { values } = presetEntries(p, specs);
    const keys = Object.keys(values);
    if (!keys.length || keys.length <= most) continue;
    if (keys.every((k) => sameValue(byKey.get(k), current[k], values[k]))) { best = p.name; most = keys.length; }
  }
  return best;
}

/** The presets a filter box and a tag chip leave showing, in their own order.
 *
 *  The text is split into words and every word has to be found — in the name or in a tag — so
 *  "market twin" finds "Twin market". An empty tag, or "all", is no tag filter. */
export function filterPresets(presets: PresetSpec[], text = "", tag = ""): PresetSpec[] {
  const words = String(text || "").toLowerCase().split(/\s+/).filter(Boolean);
  const t = String(tag || "").trim().toLowerCase();
  return presets.filter((p) => {
    if (t && t !== "all" && !p.tags.includes(t)) return false;
    if (!words.length) return true;
    const name = p.name.toLowerCase();
    return words.every((w) => name.includes(w) || p.tags.some((x) => x.includes(w)));
  });
}

/** The chips: every tag any preset carries, with how many carry it, in the order they first
 *  appear — the author's order, which is usually the order they think of the kit in. */
export function presetTags(presets: PresetSpec[]): Array<{ tag: string; count: number }> {
  const m = new Map<string, number>();
  for (const p of presets) for (const t of p.tags) m.set(t, (m.get(t) || 0) + 1);
  return [...m].map(([tag, count]) => ({ tag, count }));
}

/** The edit document's `params` after setting several values at once — the rule one slider has
 *  always followed, for a whole preset. A value equal to the file's own is DROPPED rather than
 *  stored, so a preset that happens to match the file leaves nothing to save; a key the patch
 *  does not name is kept exactly as it was. */
export function withParams(params: Record<string, ParamValue>, specs: ParamSpec[], patch: Record<string, ParamValue>):
  Record<string, ParamValue> {
  const byKey = new Map(specs.map((s) => [s.key, s]));
  const next: Record<string, ParamValue> = { ...params };
  for (const [k, v] of Object.entries(patch)) {
    if (BAD_KEYS.has(k)) continue;
    const s = byKey.get(k);
    if (s && sameValue(s, s.value, v)) delete next[k];
    else next[k] = v;
  }
  return next;
}

/** The values a preset's THUMBNAIL is built with: the file's own values, then the preset.
 *  Deliberately not the person's current edits — a thumbnail is a picture of the preset, and a
 *  picture that changed every time a slider moved could never be cached. */
export function presetBuildValues(preset: PresetSpec, specs: ParamSpec[]): Record<string, ParamValue> {
  const base: Record<string, ParamValue> = {};
  for (const s of specs) base[s.key] = s.value;
  return { ...base, ...presetEntries(preset, specs).values };
}
