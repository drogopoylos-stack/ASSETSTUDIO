import { GUTTER, MIN_TRACK, PRESETS, even, normalise, placement, preset, resize, swap, tracks, valid } from "./src/components/paneLayout";

let ok = 0, fail = 0;
const check = (l: string, c: boolean, e?: unknown) => {
  if (c) { ok++; console.log("  PASS  " + l); }
  else { fail++; console.log("  FAIL  " + l + "  " + JSON.stringify(e)); }
};
const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);
const near = (a: number, b: number) => Math.abs(a - b) < 1e-9;

// --- fractions always sum to one -----------------------------------------
check("normalise sums to 1", near(sum(normalise([2, 3, 5])), 1));
check("normalise handles rubbish", near(sum(normalise([NaN, -4, 0])), 1), normalise([NaN, -4, 0]));
check("even(3) sums to 1", near(sum(even(3)), 1));

// --- resizing -------------------------------------------------------------
let f = [0.5, 0.5];
check("a drag moves the seam", near(resize(f, 0, 0.2)[0], 0.7));
check("...and the pair keeps its total", near(sum(resize(f, 0, 0.2)), 1));
check("a huge drag cannot collapse a pane", resize(f, 0, 5)[1] >= MIN_TRACK, resize(f, 0, 5));
check("...in either direction", resize(f, 0, -5)[0] >= MIN_TRACK, resize(f, 0, -5));
check("dragging the last seam is a no-op", resize(f, 1, 0.2) === f);
check("dragging a seam that is not there is a no-op", resize(f, -1, 0.2) === f);
f = [0.2, 0.6, 0.2];
const r = resize(f, 1, 0.1);
check("only the two adjacent tracks move",
  near(r[0], f[0]) && near(r[1], 0.7) && near(r[2], 0.1) && near(r[0] + r[1] + r[2], 1), r);
check("...the far track is untouched", near(r[0], f[0]), [f, r]);

// --- track strings --------------------------------------------------------
check("tracks interleave real gutters", tracks([0.6, 0.4]) === `0.6fr ${GUTTER}px 0.4fr`, tracks([0.6, 0.4]));
check("one track has no gutter", tracks([1]) === "1fr", tracks([1]));

// --- placement lands on the right lines -----------------------------------
const p0 = placement({ id: "a", kind: "chat", col: 0, row: 0, colSpan: 1, rowSpan: 1 });
check("first pane starts on line 1", p0.gridColumn === "1 / span 1", p0);
const p1 = placement({ id: "b", kind: "chat", col: 1, row: 0, colSpan: 1, rowSpan: 1 });
check("second pane skips the gutter", p1.gridColumn === "3 / span 1", p1);
const p2 = placement({ id: "c", kind: "chat", col: 0, row: 0, colSpan: 2, rowSpan: 1 });
check("a span covers its gutter too", p2.gridColumn === "1 / span 3", p2);
const p3 = placement({ id: "d", kind: "chat", col: 0, row: 0, colSpan: 1, rowSpan: 2 });
check("row spans work the same", p3.gridRow === "1 / span 3", p3);

// --- every preset is well formed -----------------------------------------
for (const ps of PRESETS) {
  const l = ps.build([]);
  check(`preset "${ps.label}" sums to 1`, near(sum(l.cols), 1) && near(sum(l.rows), 1), l);
  check(`preset "${ps.label}" is valid`, valid(l), l);
  const cells = l.panes.reduce((a, p) => a + p.colSpan * p.rowSpan, 0);
  check(`preset "${ps.label}" fills the grid exactly (${cells} of ${l.cols.length * l.rows.length})`,
    cells === l.cols.length * l.rows.length, l.panes);
  // no two panes may overlap
  const taken = new Set<string>();
  let clash = false;
  for (const p of l.panes)
    for (let c = p.col; c < p.col + p.colSpan; c++)
      for (let rr = p.row; rr < p.row + p.rowSpan; rr++) {
        if (taken.has(`${c},${rr}`)) clash = true;
        taken.add(`${c},${rr}`);
      }
  check(`preset "${ps.label}" has no overlapping panes`, !clash);
}
check("Main + 2 really is one tall pane beside two", (() => {
  const l = preset("mainSide").build([]);
  return l.panes[0].rowSpan === 2 && l.panes[1].rowSpan === 1 && l.panes[2].row === 1;
})());
check("Six is three across and two down", (() => {
  const l = preset("six").build([]);
  return l.cols.length === 3 && l.rows.length === 2 && l.panes.length === 6;
})());

// --- switching preset keeps what still fits -------------------------------
const two = preset("twoCols").build([]);
two.panes[0].kind = "terminal"; two.panes[0].ref = "codex";
const four = preset("quad").build(two.panes);
check("a kept pane keeps its id", four.panes[0].id === two.panes[0].id);
check("...and what it was showing", four.panes[0].kind === "terminal" && four.panes[0].ref === "codex", four.panes[0]);
check("...and the new panes are fresh", four.panes[3].id !== two.panes[0].id);

// --- the loader refuses a layout that does not fit ------------------------
check("valid() rejects a pane outside the grid",
  !valid({ cols: [1], rows: [1], panes: [{ id: "x", kind: "chat", col: 1, row: 0, colSpan: 1, rowSpan: 1 }] }));
check("valid() rejects an oversized span",
  !valid({ cols: [0.5, 0.5], rows: [1], panes: [{ id: "x", kind: "chat", col: 1, row: 0, colSpan: 2, rowSpan: 1 }] }));
check("valid() rejects rubbish", !valid(null) && !valid({}) && !valid({ cols: [], rows: [], panes: [] }));
check("valid() accepts a real preset", valid(preset("six").build([])));


// --- swapping two panes ---------------------------------------------------
{
  const l = preset("twoCols").build([]);
  const a = l.panes[0].id, b = l.panes[1].id;
  const s = swap(l, a, b);
  check("swap moves the geometry", s.panes[0].col === 1 && s.panes[1].col === 0);
  check("...and keeps both ids in place", s.panes[0].id === a && s.panes[1].id === b);
  check("...so React keeps both subtrees", s.panes.map((p) => p.id).join() === l.panes.map((p) => p.id).join());
  check("swap of a pane with itself is a no-op", swap(l, a, a) === l);
  check("swap of an unknown id is a no-op", swap(l, a, "nope") === l);
  check("a swapped layout is still valid", valid(s));
  // and the swap must survive a change of shape
  const q = preset("quad").build(s.panes);
  check("a preset change keeps the swapped pane on the right", q.panes[0].id === b,
    q.panes.map((p) => `${p.id}@${p.col},${p.row}`));
}
{
  // reading order, not array order: three panes swapped end-to-end and re-shaped
  const l = preset("threeCols").build([]);
  const ids = l.panes.map((p) => p.id);
  const s = swap(l, ids[0], ids[2]);
  const back = preset("threeCols").build(s.panes);
  check("reading order survives a rebuild", back.panes.map((p) => p.id).join() === [ids[2], ids[1], ids[0]].join(),
    back.panes.map((p) => p.id));
}
console.log(`\n  ${ok} passed, ${fail} failed  (with swap)`);
process.exit(fail ? 1 : 0);
