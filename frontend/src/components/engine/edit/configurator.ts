// THE MODULAR-ASSET CONTRACT — the one shape shared by the configurator panel (ConfigPanel.tsx),
// the presets (presets.ts), the drag handles (handles.ts) and the kit helpers in ops.ts.
//
// It was written before any of those existed, so that three agents building them at once could
// not drift apart, and it stays the single place the manifest is defined. It holds TYPES and one
// constant, nothing that runs. A new field is added here first, then taught to its reader.
//
// What it describes: an asset that is a KIT plus RULES, not one mesh — the pattern behind every
// "configurator" demo (a building whose width adds window bays instead of stretching them). The
// asset declares its settings, its saved looks and its drag handles in its manifest; the editor
// turns them into a panel, a thumbnail grid and arrows on the model.
//
//   export const manifest = {
//     name: "corner shop",
//     params: {
//       width:     { value: 6.4, min: 3.6, max: 12, step: 0.1, label: "Width",  group: "building" },
//       floors:    { value: 3,   min: 1,   max: 6,  step: 1,   label: "Floors", group: "building" },
//       type:      { value: "shop", options: ["shop", "house", "service"], label: "Building type", group: "building" },
//       rearStair: { value: false, label: "Rear service stair", group: "building" },
//       trim:      { value: "#6b3f2a", label: "Trim colour", group: "facade", advanced: true },
//       night:     { value: false, label: "Night" },     // the DAY/NIGHT switch drives this when declared
//     },
//     presets: [
//       { name: "Corner shop · tea", tags: ["shop", "corner"], values: { width: 5.8, floors: 3, type: "shop" } },
//       { name: "Twin market",       tags: ["shop"],           values: { width: 9.8, floors: 3, type: "shop" }, note: "two shop fronts" },
//     ],
//     handles: [
//       { param: "width",  axis: "x", side: "max", scale: 2 },              // centred on x: the face moves half the width change
//       { param: "floors", axis: "y", side: "max", scale: 1 / 3, snap: 1 }, // 3 m a floor, whole floors only
//     ],
//   };
//
// Every key is optional. An asset with only `params` works exactly as it did before this file.

import type { ParamValue } from "./kit";

/** A saved set of parameter values: one thumbnail in the grid, applied with one click. */
export interface PresetSpec {
  /** Unique within the asset. Shown under the thumbnail. */
  name: string;
  /** Filter chips. Lower-case words; the panel offers the union of every preset's tags. */
  tags: string[];
  /** Parameter key -> value. A key the asset does not declare is IGNORED, never an error:
   *  presets outlive the parameters they were saved against. */
  values: Record<string, ParamValue>;
  /** One line for the tooltip. */
  note?: string;
}

/** An arrow drawn on the model, bound to ONE number parameter. Dragging it changes the value. */
export interface HandleSpec {
  /** The parameter key it drives. Only a `number` parameter can have a handle. */
  param: string;
  /** The world axis the arrow points along and moves along. */
  axis: "x" | "y" | "z";
  /** Which face of the bounding box it sits on: the far side (`max`) or the near side (`min`). */
  side: "min" | "max";
  /** Parameter units per metre of drag. 1 = the value follows the pointer one to one; 2 for a
   *  model centred on that axis (its face moves half of any width change). Default 1.
   *  A drag is measured OUTWARD from the face the handle sits on: pulling a `min`-side handle
   *  toward −axis grows the value too, so a pair on opposite faces both widen the model. */
  scale: number;
  /** Round the value to a multiple of this — 1 for a floor count. 0 = no rounding. Default: the
   *  parameter's own `step`, so a drag rounds exactly like the slider it moves. */
  snap: number;
  /** Clamp. Falls back to the parameter's own min / max when absent. */
  min?: number;
  max?: number;
  /** Sit on one named part's box instead of the whole asset's. */
  part?: string;
  /** The tooltip. Defaults to the parameter's label. */
  label?: string;
}

/** What the editor hands the world, so a drag can read and write the parameter it is bound to. */
export interface ParamHandleCallbacks {
  /** The parameter's current value — what the panel shows, at most one debounced rebuild (70 ms)
   *  ahead of what the model was last built with. */
  get(key: string): number;
  /** During the drag, on every move. The editor already debounces the rebuild (70 ms). */
  set(key: string, value: number): void;
  /** On release: the drag is one edit, so this is where it becomes dirty / undoable. */
  commit(key: string): void;
}

/** The manifest as it arrives from the module: untrusted, so every reader validates. */
export interface ModularManifest {
  name?: string;
  params?: Record<string, unknown> | unknown[];
  presets?: unknown[];
  handles?: unknown[];
}

/** The parameter the DAY / NIGHT switch sets, when an asset declares it, so the asset can light
 *  its own windows. The switch changes the studio's lighting either way. */
export const NIGHT_PARAM = "night";
