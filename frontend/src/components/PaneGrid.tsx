import { useCallback, useEffect, useRef, useState } from "react";
import { cls } from "./ui";
import {
  GUTTER, type Layout, type Pane, PRESETS, type PresetId,
  even, placement, preset, resize, swap, tracks, valid,
} from "./paneLayout";

/** What a pane's own header sets on a drag, so a drop knows it is a pane and not a file. */
export const PANE_DND = "application/x-studio-pane";

// Several panes in the main area, always aligned, freely resizable.
//
// The alignment is structural rather than maintained: the panes sit on one CSS grid whose tracks
// are fractions, and the GUTTERS ARE TRACKS TOO — not bars floating over the seams. A pane is
// placed on grid lines, so two panes in the same column share a line because it is the same line.
// Nothing here positions anything in pixels, so nothing here can leave a one-pixel step.
//
// Dragging a gutter moves only the pair it sits between, and their combined size never changes,
// so pulling one seam cannot shuffle the whole row. Double-click a gutter to even that axis out.
//
// Nothing in here ever unmounts a pane. Not on a resize, not on a preset change, and not on
// maximise — that is the whole reason the chat can be blown up to the full window and put back
// with its scroll, its running turn and its half-written message still there.
export function PaneGrid({ layout, onLayout, maxed, onMaxed, render, className }: {
  layout: Layout;
  onLayout: (l: Layout) => void;
  /** Which pane fills the window, if any. Held by the CALLER, not here: asking for a project
   *  that lives in a hidden pane has to be able to bring that pane to the front, and it cannot
   *  do that if the only thing that knows what is maximised is this component. */
  maxed: string | null;
  onMaxed: (id: string | null) => void;
  render: (pane: Pane, maxed: boolean, toggleMax: () => void) => React.ReactNode;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ axis: "col" | "row"; index: number } | null>(null);

  // A maximised pane that then disappears (preset change) must not leave a blank grid.
  useEffect(() => {
    if (maxed && !layout.panes.some((p) => p.id === maxed)) onMaxed(null);
  }, [layout.panes, maxed, onMaxed]);

  // Dragging a seam writes the grid template STRAIGHT TO THE DOM, once per animation frame, and
  // commits a single React update on release. Calling onLayout on every pointermove instead
  // re-rendered the entire workspace — chat feed, Monaco, a 3D canvas — for every mouse pixel,
  // which is exactly the lag the old file divider had before it was fixed the same way.
  // Pointer capture keeps the events coming while the cursor is over a preview iframe or a
  // canvas; those swallow window-level pointer events and made the seam feel stuck half way.
  const startDrag = useCallback((axis: "col" | "row", index: number) => (e: React.PointerEvent) => {
    e.preventDefault();
    const el = box.current;
    if (!el) return;
    const bar = e.currentTarget as HTMLElement;
    try { bar.setPointerCapture(e.pointerId); } catch { /* older engines */ }
    setDrag({ axis, index });
    const rect = el.getBoundingClientRect();
    const span = axis === "col" ? rect.width : rect.height;
    const start = axis === "col" ? e.clientX : e.clientY;
    const base = axis === "col" ? layout.cols : layout.rows;
    let next = base;
    let raf = 0;
    const paint = () => {
      raf = 0;
      if (axis === "col") el.style.gridTemplateColumns = tracks(next);
      else el.style.gridTemplateRows = tracks(next);
    };
    const move = (ev: PointerEvent) => {
      const now = axis === "col" ? ev.clientX : ev.clientY;
      next = resize(base, index, span > 0 ? (now - start) / span : 0);
      if (!raf) raf = requestAnimationFrame(paint);
    };
    const up = (ev: PointerEvent) => {
      try { bar.releasePointerCapture(ev.pointerId); } catch { /* ignore */ }
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      bar.removeEventListener("lostpointercapture", up);
      if (raf) cancelAnimationFrame(raf);
      setDrag(null);
      onLayout(axis === "col" ? { ...layout, cols: next } : { ...layout, rows: next });
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
    bar.addEventListener("lostpointercapture", up);
  }, [layout, onLayout]);

  const evenOut = (axis: "col" | "row") => () => onLayout(
    axis === "col" ? { ...layout, cols: even(layout.cols.length) }
                   : { ...layout, rows: even(layout.rows.length) });

  return (
    <div ref={box} className={cls("flex-1 min-h-0 min-w-0", className)}
      style={maxed
        ? { display: "grid", gridTemplateColumns: "1fr", gridTemplateRows: "1fr" }
        : { display: "grid", gridTemplateColumns: tracks(layout.cols), gridTemplateRows: tracks(layout.rows) }}>
      {layout.panes.map((p) => {
        const isMax = maxed === p.id;
        // Maximised, the chosen pane takes the single cell and the others go to display:none.
        // They stay in the tree — an iframe keeps its document, a terminal keeps its process,
        // a feed keeps its scroll — so coming back out of full screen shows what you left.
        const style: React.CSSProperties = !maxed ? placement(p)
          : isMax ? { gridArea: "1 / 1 / -1 / -1" }
          : { display: "none" };
        return (
          // No overflow-hidden here. A pane holds headers with dropdown menus that hang BELOW
          // an h-8 bar, and clipping at the pane cut every one of them in half. Each pane's own
          // content already scrolls itself; nothing needs the cell to clip as well.
          <div key={p.id} style={style} className="min-h-0 min-w-0 flex flex-col">
            {render(p, isMax, () => onMaxed(isMax ? null : p.id))}
          </div>
        );
      })}
      {/* gutters: one per seam, spanning the whole opposite axis so the grab area is the entire
          seam rather than only the part between two particular panes. Hidden while maximised —
          there is nothing to resize against. */}
      {!maxed && layout.cols.slice(0, -1).map((_, i) => (
        <div key={`c${i}`} onPointerDown={startDrag("col", i)} onDoubleClick={evenOut("col")}
          title="Drag to resize · double-click to even out"
          style={{ gridColumn: `${2 * i + 2}`, gridRow: `1 / -1` }}
          className={cls("cursor-col-resize flex justify-center group touch-none",
            drag?.axis === "col" && drag.index === i && "bg-brand/30")}>
          <div className="w-px h-full bg-line group-hover:bg-brand transition-colors" />
        </div>
      ))}
      {!maxed && layout.rows.slice(0, -1).map((_, i) => (
        <div key={`r${i}`} onPointerDown={startDrag("row", i)} onDoubleClick={evenOut("row")}
          title="Drag to resize · double-click to even out"
          style={{ gridRow: `${2 * i + 2}`, gridColumn: `1 / -1` }}
          className={cls("cursor-row-resize flex items-center group touch-none",
            drag?.axis === "row" && drag.index === i && "bg-brand/30")}>
          <div className="h-px w-full bg-line group-hover:bg-brand transition-colors" />
        </div>
      ))}
      {/* While dragging, an iframe or a canvas would otherwise swallow the pointer and the seam
          would stop following the cursor half way across. */}
      {drag && <div className="fixed inset-0 z-[999] select-none"
        style={{ cursor: drag.axis === "col" ? "col-resize" : "row-resize" }} />}
    </div>
  );
}

/** A preset drawn as its own shape — a new preset gets an icon without anyone drawing one. */
function Glyph({ rows, tone }: { rows: number[][]; tone: string }) {
  return (
    <span className="flex flex-col gap-[1px] w-3.5 h-3">
      {rows.map((row, ri) => (
        <span key={ri} className="flex gap-[1px] flex-1">
          {row.map((on, ci) => (
            <span key={ci} className={cls("flex-1 rounded-[1px]", on ? tone : "bg-transparent")} />
          ))}
        </span>
      ))}
    </span>
  );
}

/** The row of shape buttons. */
export function PresetPicker({ current, onPick, className }: {
  current: PresetId; onPick: (id: PresetId) => void; className?: string;
}) {
  return (
    <div className={cls("flex items-center gap-0.5", className)}>
      {PRESETS.map((p) => (
        <button key={p.id} onClick={() => onPick(p.id)} title={`${p.label} — ${p.hint}`}
          className={cls("p-1 rounded border transition-colors",
            current === p.id ? "border-brand/60 bg-brand/10" : "border-transparent hover:bg-panel2")}>
          <Glyph rows={p.glyph} tone={current === p.id ? "bg-brand" : "bg-muted/70"} />
        </button>
      ))}
    </div>
  );
}

/** One button showing the shape you are in, opening the seven you can pick.
 *
 *  A row of seven buttons is the obvious design and the wrong one here: the header it would sit
 *  in already carries the model, the branch, the speed and the context, and seven more pushed
 *  them off the end at a normal window width. */
export function PresetButton({ current, onPick, className }: {
  current: PresetId; onPick: (id: PresetId) => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const p = preset(current);
  return (
    <span className={cls("relative shrink-0 flex", className)}>
      <button onClick={() => setOpen((v) => !v)} title={`Layout — ${p.label} (${p.hint})`}
        className={cls("p-1 rounded border flex", open
          ? "border-brand/60 bg-brand/10 text-brand"
          : "border-line bg-panel2/60 text-muted hover:text-brand hover:border-brand/50")}>
        <Glyph rows={p.glyph} tone={open ? "bg-brand" : "bg-muted/70"} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-[60]" onClick={() => setOpen(false)} />
          <div className="absolute top-full right-0 mt-1 z-[61] card p-1.5 shadow-card">
            <div className="text-[10px] uppercase tracking-wide text-muted px-1 pb-1">Layout</div>
            <PresetPicker current={current} onPick={(id) => { onPick(id); setOpen(false); }} />
          </div>
        </>
      )}
    </span>
  );
}

/** The remembered arrangement for one scope.
 *
 *  The scope is the WINDOW, not a project: a pane can name a project of its own, so "which
 *  project's layout is this" stopped having an answer the moment two agents could sit side by
 *  side. A layout written by an older build that no longer describes a grid its panes fit
 *  inside is discarded rather than rendered — `valid` is the gate. */
export function useLayout(scope: string) {
  const key = `ws-layout:${scope}`;
  const projectId = scope;
  const [layout, setLayout] = useState<Layout>(() => preset("single").build([]));
  const [presetId, setPresetId] = useState<PresetId>("single");

  useEffect(() => {
    if (!projectId) return;
    try {
      const raw = JSON.parse(localStorage.getItem(key) || "null");
      if (raw && valid(raw.layout)) {
        setLayout(raw.layout);
        setPresetId(raw.preset || "single");
        return;
      }
    } catch { /* unreadable — fall through to the default */ }
    setLayout(preset("single").build([]));
    setPresetId("single");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const save = useCallback((l: Layout, p?: PresetId) => {
    setLayout(l);
    if (p) setPresetId(p);
    if (!projectId) return;
    try { localStorage.setItem(key, JSON.stringify({ layout: l, preset: p ?? presetId })); }
    catch { /* storage full or blocked — the layout still works for this session */ }
  }, [key, projectId, presetId]);

  const pick = useCallback((id: PresetId) => {
    setLayout((cur) => {
      const next = preset(id).build(cur.panes);
      if (projectId) {
        try { localStorage.setItem(key, JSON.stringify({ layout: next, preset: id })); }
        catch { /* ignore */ }
      }
      return next;
    });
    setPresetId(id);
  }, [key, projectId]);

  const trade = useCallback((a: string, b: string) => {
    setLayout((cur) => {
      const next = swap(cur, a, b);
      if (projectId) {
        try { localStorage.setItem(key, JSON.stringify({ layout: next, preset: presetId })); }
        catch { /* ignore */ }
      }
      return next;
    });
  }, [key, projectId, presetId]);

  return { layout, presetId, setLayout: save, pick, trade };
}

export { GUTTER };
