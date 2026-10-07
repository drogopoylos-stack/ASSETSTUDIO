// The geometry behind the pane grid. No React in here, so it can be reasoned about — and
// tested — as plain arithmetic.
//
// Alignment is not something this maintains; it is something it cannot break. Panes live on a
// CSS grid whose tracks are fractions, with the gutters as REAL tracks between them rather than
// bars floating on top. A pane is placed on track lines, so two panes in the same column share
// the same line by construction. There is no code path that can leave a one-pixel seam, because
// no code path positions anything by pixels.

/** "agent" is one subagent, watched in a pane of its own while its parent keeps working. */
export type PaneKind = "chat" | "editor" | "terminal" | "preview" | "files" | "agent";

export interface Pane {
  id: string;
  kind: PaneKind;
  col: number;          // zero-based, in PANE coordinates (gutters are not counted)
  row: number;
  colSpan: number;
  rowSpan: number;
  /** what this pane is showing — a file path, an agent id, whatever the kind needs */
  ref?: string;
}

export interface Layout {
  cols: number[];       // fractions; always sum to 1
  rows: number[];
  panes: Pane[];
}

/** Gutter thickness in px. A real track, so it takes space instead of covering a pane's edge. */
export const GUTTER = 6;
/** No track may be dragged smaller than this fraction — a pane you cannot see is a pane you
 *  cannot get back without resetting the whole layout. */
export const MIN_TRACK = 0.08;

/** `0.6fr 6px 0.4fr` — panes on the odd lines, gutters on the even ones. */
export function tracks(fractions: number[]): string {
  return fractions.map((f) => `${f}fr`).join(` ${GUTTER}px `);
}

/** CSS grid placement for a pane, converting pane coordinates to track lines.
 *  Pane i starts at line 2i+1 and each extra pane spanned adds its gutter, hence 2*span-1. */
export function placement(p: Pane): { gridColumn: string; gridRow: string } {
  return {
    gridColumn: `${2 * p.col + 1} / span ${2 * p.colSpan - 1}`,
    gridRow: `${2 * p.row + 1} / span ${2 * p.rowSpan - 1}`,
  };
}

/** Normalise fractions so they always sum to exactly 1, however they were edited. */
export function normalise(fs: number[]): number[] {
  const clean = fs.map((f) => (Number.isFinite(f) && f > 0 ? f : MIN_TRACK));
  const sum = clean.reduce((a, b) => a + b, 0);
  return sum > 0 ? clean.map((f) => f / sum) : clean.map(() => 1 / clean.length);
}

/** Move the boundary between track `i` and `i+1` by `delta` (a fraction of the whole axis).
 *  The pair always keeps its combined size, so nothing outside the two tracks ever moves — which
 *  is what stops a drag on one seam from nudging every pane on the row. */
export function resize(fs: number[], i: number, delta: number): number[] {
  if (i < 0 || i + 1 >= fs.length) return fs;
  const out = [...fs];
  const pair = out[i] + out[i + 1];
  const want = out[i] + delta;
  // When the clamp binds, set BOTH sides explicitly. Writing `pair - (pair - MIN_TRACK)` and
  // trusting it to equal MIN_TRACK is a floating-point mistake: it came out 0.07999999999999996
  // against a 0.08 minimum, so the guarantee this function exists to make was quietly false.
  if (want <= MIN_TRACK) {
    out[i] = MIN_TRACK;
    out[i + 1] = pair - MIN_TRACK;
  } else if (want >= pair - MIN_TRACK) {
    out[i] = pair - MIN_TRACK;
    out[i + 1] = MIN_TRACK;
  } else {
    out[i] = want;
    out[i + 1] = pair - want;
  }
  return out;
}

/** Equal tracks — what a double-click on a gutter goes back to. */
export function even(n: number): number[] {
  return Array.from({ length: n }, () => 1 / n);
}

// ---- presets -------------------------------------------------------------
// Named for what you SEE, not for the grid that produces it.

export type PresetId = "single" | "twoCols" | "twoRows" | "mainSide" | "threeCols" | "quad" | "six";

export interface Preset {
  id: PresetId;
  label: string;
  hint: string;
  /** the shape, drawn as rows of pane counts, for the little icon in the picker */
  glyph: number[][];
  build: (existing: Pane[]) => Layout;
}

let seq = 0;
const pid = () => `p${Date.now().toString(36)}${(seq++).toString(36)}`;

/** Split just this pane, retaining all other panes and their mounted contents. */
export function splitChatPane(l: Layout, id: string, ref: string): Layout {
  const target = l.panes.find((p) => p.id === id);
  if (!target) return l;
  const nextId = pid();
  if (target.colSpan > 1) {
    const left = Math.ceil(target.colSpan / 2);
    return { ...l, panes: [...l.panes.map((p) => p.id === id ? { ...p, colSpan: left, ref } : p),
      { ...target, id: nextId, kind: "chat", ref, col: target.col + left, colSpan: target.colSpan - left }] };
  }
  const col = target.col;
  const cols = [...l.cols];
  cols.splice(col, 1, l.cols[col] / 2, l.cols[col] / 2);
  return { ...l, cols, panes: [...l.panes.map((p) => {
    if (p.id === id) return { ...p, ref };
    if (p.col > col) return { ...p, col: p.col + 1 };
    if (p.col + p.colSpan > col) return { ...p, colSpan: p.colSpan + 1 };
    return p;
  }), { ...target, id: nextId, kind: "chat", ref, col: col + 1 }] };
}

/** Keep the panes you already had wherever they still fit, so switching preset rearranges the
 *  room rather than emptying it. Anything that no longer fits becomes a fresh empty pane.
 *
 *  Matching is by READING ORDER, not by position in the array: a preset lays its slots out
 *  top-left first, so the pane that is currently top-left has to be the one that lands there.
 *  Matching by array index instead silently undid a swap — you dragged the chat to the right,
 *  picked a different shape, and it jumped back to the left. */
function fill(cols: number[], rows: number[], spec: Omit<Pane, "id" | "kind" | "ref">[],
              existing: Pane[]): Layout {
  const ordered = [...existing].sort((a, b) => a.row - b.row || a.col - b.col);
  const panes = spec.map((s, i) => {
    const keep = ordered[i];
    return {
      id: keep?.id || pid(),
      kind: keep?.kind || (i === 0 ? "chat" : "editor"),
      ref: keep?.ref,
      ...s,
    } as Pane;
  });
  return { cols: normalise(cols), rows: normalise(rows), panes };
}

const one = { col: 0, row: 0, colSpan: 1, rowSpan: 1 };

export const PRESETS: Preset[] = [
  {
    id: "single", label: "One", hint: "a single pane", glyph: [[1]],
    build: (e) => fill([1], [1], [one], e),
  },
  {
    id: "twoCols", label: "Two", hint: "side by side", glyph: [[1, 1]],
    build: (e) => fill([0.55, 0.45], [1],
      [one, { col: 1, row: 0, colSpan: 1, rowSpan: 1 }], e),
  },
  {
    id: "twoRows", label: "Stacked", hint: "one above the other", glyph: [[1], [1]],
    build: (e) => fill([1], [0.55, 0.45],
      [one, { col: 0, row: 1, colSpan: 1, rowSpan: 1 }], e),
  },
  {
    id: "mainSide", label: "Main + 2", hint: "one big, two stacked beside it", glyph: [[1, 1], [0, 1]],
    build: (e) => fill([0.6, 0.4], [0.5, 0.5], [
      { col: 0, row: 0, colSpan: 1, rowSpan: 2 },
      { col: 1, row: 0, colSpan: 1, rowSpan: 1 },
      { col: 1, row: 1, colSpan: 1, rowSpan: 1 },
    ], e),
  },
  {
    id: "threeCols", label: "Three", hint: "three columns", glyph: [[1, 1, 1]],
    build: (e) => fill([1 / 3, 1 / 3, 1 / 3], [1], [
      one,
      { col: 1, row: 0, colSpan: 1, rowSpan: 1 },
      { col: 2, row: 0, colSpan: 1, rowSpan: 1 },
    ], e),
  },
  {
    id: "quad", label: "Four", hint: "two by two", glyph: [[1, 1], [1, 1]],
    build: (e) => fill([0.5, 0.5], [0.5, 0.5], [
      one,
      { col: 1, row: 0, colSpan: 1, rowSpan: 1 },
      { col: 0, row: 1, colSpan: 1, rowSpan: 1 },
      { col: 1, row: 1, colSpan: 1, rowSpan: 1 },
    ], e),
  },
  {
    id: "six", label: "Six", hint: "three across, two down", glyph: [[1, 1, 1], [1, 1, 1]],
    build: (e) => fill([1 / 3, 1 / 3, 1 / 3], [0.5, 0.5], [
      one,
      { col: 1, row: 0, colSpan: 1, rowSpan: 1 },
      { col: 2, row: 0, colSpan: 1, rowSpan: 1 },
      { col: 0, row: 1, colSpan: 1, rowSpan: 1 },
      { col: 1, row: 1, colSpan: 1, rowSpan: 1 },
      { col: 2, row: 1, colSpan: 1, rowSpan: 1 },
    ], e),
  },
];

export const preset = (id: PresetId) => PRESETS.find((p) => p.id === id) || PRESETS[0];

/** Trade the places of two panes — what a drag of one pane's header onto another does.
 *
 *  Only the GEOMETRY moves. The pane ids stay where they are in the array, so React keeps both
 *  subtrees mounted and only their grid lines change: a chat dragged across the window keeps its
 *  scroll, its running turn and its half-typed message. Swapping the contents instead would look
 *  identical and throw all three away. */
export function swap(l: Layout, a: string, b: string): Layout {
  if (a === b) return l;
  const A = l.panes.find((p) => p.id === a);
  const B = l.panes.find((p) => p.id === b);
  if (!A || !B) return l;
  const at = { col: A.col, row: A.row, colSpan: A.colSpan, rowSpan: A.rowSpan };
  const bt = { col: B.col, row: B.row, colSpan: B.colSpan, rowSpan: B.rowSpan };
  return {
    ...l,
    panes: l.panes.map((p) => (p.id === a ? { ...p, ...bt } : p.id === b ? { ...p, ...at } : p)),
  };
}

/** Does this layout describe a grid every pane actually fits inside? Used by the loader, because
 *  a layout read back from storage was written by an older build and may not. */
export function valid(l: unknown): l is Layout {
  const x = l as Layout;
  if (!x || !Array.isArray(x.cols) || !Array.isArray(x.rows) || !Array.isArray(x.panes)) return false;
  if (!x.cols.length || !x.rows.length || !x.panes.length) return false;
  if (!x.cols.every((f) => Number.isFinite(f) && f > 0)) return false;
  if (!x.rows.every((f) => Number.isFinite(f) && f > 0)) return false;
  return x.panes.every((p) =>
    p && typeof p.id === "string"
    && p.col >= 0 && p.row >= 0 && p.colSpan >= 1 && p.rowSpan >= 1
    && p.col + p.colSpan <= x.cols.length
    && p.row + p.rowSpan <= x.rows.length);
}
