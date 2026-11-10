import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Circle, Copy, Eraser, Eye, EyeOff, FlipHorizontal, FolderOpen, Grid3x3, Layers, Loader2, Minus, Move, Paintbrush,
  PaintBucket, Pause, Pencil, Pipette, Play, Plus, Redo2, Save, Slash, Square, Trash2, Undo2, X, ZoomIn, ZoomOut,
} from "lucide-react";
import { api } from "../../api/client";
import type { LibraryAsset } from "../../types";
import { cls } from "../ui";
import {
  DrawHistory, Stroke, addFrame, addLayer, atlasJson, docFromJson, docToJson, drawShape, flatten, floodFill, mergeDown,
  moveFrame, moveLayer, newDoc, pickColour, removeFrame, removeLayer, shiftCel, spriteSheet, type DrawDoc, type DrawTool,
} from "./edit/draw";
import { DEFAULT_SWATCHES, pushRecent } from "./edit/paintCore";
import { joinPath } from "./edit/load";

// DRAW: a sprite editor inside the 2D Edit tab. Pixel art and painted art, layers and animation
// frames, saved into the game as a PNG (and a sprite sheet with its atlas when there is more than
// one frame), with the editable drawing beside it as <name>.draw.json. The drawing tools live in
// draw.ts; this file is the canvas, the panels and the files.

const TOOL_KEYS: Record<string, DrawTool> = { p: "pencil", b: "brush", e: "eraser", g: "fill", l: "line", r: "rect", o: "ellipse", i: "picker", v: "move" };
const TOOL_ICON: Record<DrawTool, JSX.Element> = {
  pencil: <Pencil size={13} />, brush: <Paintbrush size={13} />, eraser: <Eraser size={13} />, fill: <PaintBucket size={13} />,
  line: <Slash size={13} />, rect: <Square size={13} />, ellipse: <Circle size={13} />, picker: <Pipette size={13} />, move: <Move size={13} />,
};
const TOOL_KEY: Record<DrawTool, string> = { pencil: "P", brush: "B", eraser: "E", fill: "G", line: "L", rect: "R", ellipse: "O", picker: "I", move: "V" };
const SIZES = [16, 32, 48, 64, 128, 256, 512];

// ------------------------------------------------------------------ pixels <-> files
function toCanvas(data: Uint8ClampedArray, w: number, h: number): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d")!.putImageData(new ImageData(new Uint8ClampedArray(data), w, h), 0, 0);
  return c;
}
const toPng = (data: Uint8ClampedArray, w: number, h: number) =>
  new Promise<Blob>((res, rej) => toCanvas(data, w, h).toBlob((b) => (b ? res(b) : rej(new Error("the PNG could not be made"))), "image/png"));
function loadPixels(url: string): Promise<{ w: number; h: number; data: Uint8ClampedArray }> {
  return new Promise((res, rej) => {
    const im = new Image();
    im.onload = () => {
      const c = document.createElement("canvas");
      c.width = im.naturalWidth; c.height = im.naturalHeight;
      const g = c.getContext("2d")!;
      g.drawImage(im, 0, 0);
      res({ w: c.width, h: c.height, data: g.getImageData(0, 0, c.width, c.height).data });
    };
    im.onerror = () => rej(new Error("could not read " + url.slice(0, 80)));
    im.src = url;
  });
}

export function Draw2D({ project, onClose, onPlaced }: { project: string; onClose: () => void; onPlaced?: (note: string) => void }) {
  const [doc, setDoc] = useState<DrawDoc | null>(null);
  const [name, setName] = useState("sprite");
  const [frame, setFrame] = useState(0);
  const [layer, setLayer] = useState(0);
  const [tool, setTool] = useState<DrawTool>("pencil");
  const [colour, setColour] = useState("#d63a2a");
  const [recent, setRecent] = useState<string[]>([]);
  const [size, setSize] = useState(1);
  const [opacity, setOpacity] = useState(1);
  const [hardness, setHardness] = useState(0.8);
  const [filled, setFilled] = useState(false);
  const [mirror, setMirror] = useState(false);
  const [onion, setOnion] = useState(false);
  const [grid, setGrid] = useState(true);
  const [zoom, setZoom] = useState(8);
  const [playing, setPlaying] = useState(false);
  const [tick, setTick] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [newW, setNewW] = useState(32);
  const [newH, setNewH] = useState(32);
  const [newPixel, setNewPixel] = useState(true);

  const hist = useRef(new DrawHistory());
  const view = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const op = useRef<{ kind: DrawTool; stroke?: Stroke; base?: Uint8ClampedArray; x0: number; y0: number } | null>(null);
  const redraw = () => setTick((t) => t + 1);

  const start = (d: DrawDoc) => {
    hist.current = new DrawHistory();
    setDoc(d);
    setFrame(0);
    setLayer(Math.max(0, d.layers.length - 1));
    setTool(d.pixel ? "pencil" : "brush");
    setSize(d.pixel ? 1 : Math.max(2, Math.round(Math.min(d.w, d.h) / 24)));
    const box = wrap.current;
    const fit = box ? Math.floor(Math.min((box.clientWidth - 40) / d.w, (box.clientHeight - 40) / d.h)) : 8;
    setZoom(Math.max(1, Math.min(32, fit || 1)));
  };

  // ---- the view
  const flat = useMemo(() => (doc ? flatten(doc, frame, { onion: onion && !playing ? 0.3 : 0 }) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [doc, frame, onion, playing, tick]);
  useEffect(() => {
    const c = view.current, box = wrap.current;
    if (!c || !box || !doc || !flat) return;
    const W = box.clientWidth, H = box.clientHeight;
    c.width = W; c.height = H;
    const g = c.getContext("2d")!;
    g.fillStyle = "#0b0d12";
    g.fillRect(0, 0, W, H);
    const dw = doc.w * zoom, dh = doc.h * zoom, ox = Math.round((W - dw) / 2), oy = Math.round((H - dh) / 2);
    // A checkerboard shows what is see-through.
    const cell = Math.max(4, zoom * (doc.pixel ? 1 : 4));
    for (let y = 0; y < dh; y += cell) for (let x = 0; x < dw; x += cell) {
      g.fillStyle = ((x / cell + y / cell) & 1) ? "#2b2f38" : "#3a3f4a";
      g.fillRect(ox + x, oy + y, Math.min(cell, dw - x), Math.min(cell, dh - y));
    }
    g.imageSmoothingEnabled = !doc.pixel && zoom < 4;
    g.drawImage(toCanvas(flat, doc.w, doc.h), ox, oy, dw, dh);
    if (grid && zoom >= 6) {
      g.strokeStyle = "rgba(255,255,255,0.08)";
      g.lineWidth = 1;
      g.beginPath();
      for (let x = 0; x <= doc.w; x++) { g.moveTo(ox + x * zoom + 0.5, oy); g.lineTo(ox + x * zoom + 0.5, oy + dh); }
      for (let y = 0; y <= doc.h; y++) { g.moveTo(ox, oy + y * zoom + 0.5); g.lineTo(ox + dw, oy + y * zoom + 0.5); }
      g.stroke();
    }
    if (mirror) {
      g.strokeStyle = "rgba(255,140,26,0.6)";
      g.beginPath(); g.moveTo(ox + dw / 2 + 0.5, oy); g.lineTo(ox + dw / 2 + 0.5, oy + dh); g.stroke();
    }
    g.strokeStyle = "rgba(255,255,255,0.25)";
    g.strokeRect(ox - 0.5, oy - 0.5, dw + 1, dh + 1);
  }, [doc, flat, zoom, grid, mirror]);
  useEffect(() => {
    const box = wrap.current;
    if (!box) return;
    const ro = new ResizeObserver(() => redraw());
    ro.observe(box);
    return () => ro.disconnect();
  }, []);

  // ---- playback
  useEffect(() => {
    if (!playing || !doc) return;
    const id = window.setInterval(() => setFrame((f) => (f + 1) % doc.frames.length), 1000 / Math.max(1, doc.fps));
    return () => window.clearInterval(id);
  }, [playing, doc]);

  // ---- pointer
  const toPx = (e: React.PointerEvent): [number, number] => {
    const c = view.current!, r = c.getBoundingClientRect();
    const dw = doc!.w * zoom, dh = doc!.h * zoom, ox = Math.round((c.width - dw) / 2), oy = Math.round((c.height - dh) / 2);
    return [(e.clientX - r.left - ox) / zoom, (e.clientY - r.top - oy) / zoom];
  };
  const cel = () => doc!.frames[frame][layer];
  const pressure = (e: React.PointerEvent) => (e.pointerType === "pen" ? (e.pressure > 0 ? e.pressure : 0.5) : 1);

  const down = (e: React.PointerEvent) => {
    if (!doc || e.button !== 0 || playing) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const [x, y] = toPx(e);
    if (tool === "picker") {
      const c = pickColour(flatten(doc, frame), doc.w, x, y);
      if (c) { setColour(c); setRecent((r) => pushRecent(r, c)); }
      return;
    }
    if (!doc.layers[layer].visible) { setNote("this layer is hidden: show it to draw on it"); return; }
    hist.current.cel(doc, frame, layer);
    if (tool === "fill") {
      floodFill(cel(), doc.w, doc.h, x, y, colour, { opacity, tolerance: doc.pixel ? 0 : 0.1 });
      setRecent((r) => pushRecent(r, colour));
      redraw();
      return;
    }
    if (tool === "pencil" || tool === "brush" || tool === "eraser") {
      const stroke = new Stroke(cel(), doc.w, doc.h, {
        tool: tool === "eraser" && doc.pixel ? "pencil" : tool, colour, size, opacity, hardness, mirrorX: mirror,
      });
      if (tool === "eraser" && doc.pixel) {
        // The pixel eraser: a pencil that clears. Made as a pencil stroke on a copy, then cleared where it marked.
        const mark = new Uint8ClampedArray(cel().length);
        const s2 = new Stroke(mark, doc.w, doc.h, { tool: "pencil", colour: "#ffffff", size, opacity: 1, mirrorX: mirror });
        op.current = { kind: "eraser", stroke: s2, base: mark, x0: x, y0: y };
        s2.add([{ x, y, pressure: 1 }], true);
        clearMarked(mark);
      } else {
        op.current = { kind: tool, stroke, x0: x, y0: y };
        stroke.add([{ x, y, pressure: pressure(e) }], true);
      }
      setRecent((r) => (tool === "eraser" ? r : pushRecent(r, colour)));
      redraw();
      return;
    }
    op.current = { kind: tool, base: new Uint8ClampedArray(cel()), x0: x, y0: y };
  };
  const clearMarked = (mark: Uint8ClampedArray) => {
    const c = cel();
    for (let i = 3; i < mark.length; i += 4) if (mark[i]) c[i] = Math.round(c[i] * (1 - opacity));
  };
  const move = (e: React.PointerEvent) => {
    const o = op.current;
    if (!o || !doc) return;
    const [x, y] = toPx(e);
    if (o.stroke) {
      o.stroke.add([{ x, y, pressure: pressure(e) }]);
      if (o.kind === "eraser" && doc.pixel && o.base) clearMarked(o.base);
      redraw();
      return;
    }
    if (!o.base) return;
    if (o.kind === "move") {
      doc.frames[frame][layer] = shiftCel(o.base, doc.w, doc.h, x - o.x0, y - o.y0);
      redraw();
      return;
    }
    const c = cel();
    c.set(o.base);
    let x1 = x, y1 = y;
    if (e.shiftKey) {
      // Shift: a square or a circle, or a line at 45-degree steps.
      if (o.kind === "line") {
        const a = Math.round(Math.atan2(y - o.y0, x - o.x0) / (Math.PI / 4)) * (Math.PI / 4), len = Math.hypot(x - o.x0, y - o.y0);
        x1 = o.x0 + Math.cos(a) * len; y1 = o.y0 + Math.sin(a) * len;
      } else {
        const s = Math.max(Math.abs(x - o.x0), Math.abs(y - o.y0));
        x1 = o.x0 + Math.sign(x - o.x0 || 1) * s; y1 = o.y0 + Math.sign(y - o.y0 || 1) * s;
      }
    }
    drawShape(c, doc.w, doc.h, o.kind as "line" | "rect" | "ellipse", o.x0, o.y0, x1, y1,
      { colour, opacity, filled, thickness: size, smooth: !doc.pixel, mirrorX: mirror });
    redraw();
  };
  const up = () => {
    if (op.current && op.current.kind !== "move") setRecent((r) => pushRecent(r, colour));
    op.current = null;
  };

  // ---- structure: one undo step each
  const change = (fn: (d: DrawDoc) => void) => {
    if (!doc) return;
    hist.current.doc(doc);
    fn(doc);
    setDoc({ ...doc });
  };
  const undo = () => { if (doc) { const d = hist.current.undo(doc); setDoc({ ...d }); setLayer((l) => Math.min(l, d.layers.length - 1)); setFrame((f) => Math.min(f, d.frames.length - 1)); } };
  const redo = () => { if (doc) { const d = hist.current.redo(doc); setDoc({ ...d }); } };

  // ---- keys: the tools' letters, and nothing reaches the editor behind
  const onKey = (e: React.KeyboardEvent) => {
    const tag = (e.target as HTMLElement)?.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    e.stopPropagation();
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); redo(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (TOOL_KEYS[k]) { setTool(TOOL_KEYS[k]); return; }
    if (k === "[") setSize((s) => Math.max(1, s - 1));
    else if (k === "]") setSize((s) => Math.min(64, s + 1));
    else if (k === "m") setMirror((m) => !m);
    else if (k === "x") { const sw = recent[1]; if (sw) setColour(sw); }
    else if (e.key === ",") setFrame((f) => Math.max(0, f - 1));
    else if (e.key === ".") setFrame((f) => Math.min((doc?.frames.length || 1) - 1, f + 1));
    else if (e.key === "Escape") onClose();
  };

  // ---- files
  const gameDir = useCallback(async () => {
    const pub = joinPath(project, "public");
    const hasPublic = await api.wsStat(pub).then(() => true).catch(() => false);
    return { dir: joinPath(project, hasPublic ? "public/assets/drawn" : "assets/drawn"), servedFrom: hasPublic ? "public/" : "" };
  }, [project]);
  const writeFile = async (dir: string, file: string, blob: Blob) => {
    await api.wsDelete(joinPath(dir, file)).catch(() => { /* a first save */ });
    const fd = new FormData();
    fd.append("dest_dir", dir);
    fd.append("file", blob, file);
    const res = await fetch("/api/workspace/upload-file", { method: "POST", body: fd });
    const j = await res.json().catch(() => ({}));
    if (!res.ok || !j?.path) throw new Error(j?.detail || "could not write " + file);
    return String(j.path).replace(/\\/g, "/");
  };
  const safeName = (s: string) => s.trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "sprite";

  const save = async (place: boolean) => {
    if (!doc) return;
    setBusy(true);
    setNote("");
    try {
      const n = safeName(name);
      setName(n);
      const { dir } = await gameDir();
      await api.wsMkdir(dir).catch(() => { /* there already */ });
      const png = await writeFile(dir, n + ".png", await toPng(flatten(doc, 0), doc.w, doc.h));
      const json = docToJson(doc, (c, w, h) => toCanvas(c, w, h).toDataURL("image/png"));
      await writeFile(dir, n + ".draw.json", new Blob([JSON.stringify(json)], { type: "application/json" }));
      let sheetNote = "";
      if (doc.frames.length > 1) {
        const sh = spriteSheet(doc, 0, 0);
        await writeFile(dir, n + ".sheet.png", await toPng(sh.data, sh.w, sh.h));
        await writeFile(dir, n + ".sheet.json", new Blob([JSON.stringify(atlasJson(n, n + ".sheet.png", sh, doc.fps), null, 1)], { type: "application/json" }));
        sheetNote = ` and ${n}.sheet.png + ${n}.sheet.json (${doc.frames.length} frames at ${doc.fps} fps)`;
      }
      const root = project.replace(/\\/g, "/").replace(/\/+$/, "");
      const rel = png.toLowerCase().startsWith(root.toLowerCase() + "/") ? png.slice(root.length + 1) : png;
      let msg = `Saved ${rel}${sheetNote}.`;
      if (place) {
        const r = await api.livePlace(project, { asset: rel, save: true });
        msg = r.ok === false ? msg + " It was not placed: " + String(r.error || "the game refused it") : `Placed ${r.name || n} in the game. ` + msg;
        if (r.ok !== false) onPlaced?.(msg);
      }
      setNote(msg);
    } catch (e: any) {
      setNote("save failed: " + String(e?.message || e).slice(0, 200));
    } finally {
      setBusy(false);
    }
  };

  const openFile = async (file: string) => {
    setOpening(false);
    setBusy(true);
    try {
      const abs = joinPath(project, file);
      const base = file.replace(/\.(png|webp|jpe?g|gif)$/i, "");
      // A drawing made here comes back with its layers and frames; any other picture as one layer.
      // Raw, not through the text reader: a drawing with many frames is larger than it shows.
      const j = await fetch(api.wsRaw(joinPath(project, base + ".draw.json"), project))
        .then((r) => (r.ok ? r.json() : null)).catch(() => null);
      if (j) {
        const urls: string[] = (j.frames || []).flat().filter((s: any) => typeof s === "string");
        const got = new Map<string, Uint8ClampedArray>();
        for (const u of urls) if (!got.has(u)) got.set(u, (await loadPixels(u)).data);
        const d = docFromJson(j, (s) => got.get(s) || null);
        if (d) { start(d); setName(base.split("/").pop() || "sprite"); setNote("opened " + base + ".draw.json"); return; }
      }
      const px = await loadPixels(api.wsRaw(abs, project));
      const d = newDoc(px.w, px.h);
      d.frames[0][0] = px.data;
      start(d);
      setName((base.split("/").pop() || "sprite"));
      setNote("opened " + file + " as one layer");
    } catch (e: any) {
      setNote("could not open: " + String(e?.message || e).slice(0, 200));
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------------ the panel
  if (!doc) {
    return (
      <div className="absolute inset-0 z-30 bg-bg flex items-center justify-center text-xs" onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Escape") onClose(); }} tabIndex={-1}>
        <div className="w-96 rounded-lg border border-line bg-panel p-4 space-y-3">
          <div className="flex items-center"><span className="font-medium text-sm">New drawing</span>
            <button onClick={onClose} className="ml-auto text-muted hover:text-text"><X size={14} /></button></div>
          <div className="flex flex-wrap gap-1">
            {SIZES.map((s) => (
              <button key={s} onClick={() => { setNewW(s); setNewH(s); setNewPixel(s <= 128); }}
                className={cls("chip", newW === s && newH === s && "text-brand border-brand/50 bg-brand/10")}>{s}×{s}</button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1">W <input type="number" min={1} max={4096} value={newW} onChange={(e) => setNewW(+e.target.value || 1)} className="input !py-0.5 w-20" /></label>
            <label className="flex items-center gap-1">H <input type="number" min={1} max={4096} value={newH} onChange={(e) => setNewH(+e.target.value || 1)} className="input !py-0.5 w-20" /></label>
            <label className="flex items-center gap-1 ml-auto"><input type="checkbox" checked={newPixel} onChange={(e) => setNewPixel(e.target.checked)} /> pixel art</label>
          </div>
          <div className="flex items-center gap-1.5">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="name" className="input !py-1 flex-1" />
            <button onClick={() => start(newDoc(newW, newH, { pixel: newPixel }))} className="btn-primary !py-1">Start</button>
          </div>
          <OpenList project={project} onPick={openFile} label="…or open a picture of this game" />
          {busy && <div className="text-muted flex items-center gap-2"><Loader2 size={12} className="animate-spin" /> opening…</div>}
          {note && <div className="text-warn">{note}</div>}
        </div>
      </div>
    );
  }

  const shapeTool = tool === "line" || tool === "rect" || tool === "ellipse";
  const swatches = [...recent, ...DEFAULT_SWATCHES.filter((s) => !recent.includes(s))].slice(0, 24);
  return (
    <div className="absolute inset-0 z-30 bg-bg flex flex-col text-xs outline-none" tabIndex={0} onKeyDown={onKey}>
      <div className="h-9 shrink-0 flex items-center gap-1 px-2 border-b border-line bg-panel">
        <input value={name} onChange={(e) => setName(e.target.value)} className="input !py-0.5 w-28" title="The file name in the game" />
        <span className="text-muted mr-1">{doc.w}×{doc.h}{doc.pixel ? " · pixel" : ""}</span>
        {(Object.keys(TOOL_ICON) as DrawTool[]).map((t) => (
          <button key={t} onClick={() => setTool(t)} title={`${t} (${TOOL_KEY[t]})`}
            className={cls("p-1.5 rounded", tool === t ? "bg-brand/15 text-brand" : "text-muted hover:text-text hover:bg-panel2")}>{TOOL_ICON[t]}</button>
        ))}
        <span className="w-px h-4 bg-line mx-1" />
        <label className="flex items-center gap-1" title="Size in pixels ([ and ])">size
          <input type="number" min={1} max={64} value={size} onChange={(e) => setSize(Math.max(1, Math.min(64, +e.target.value || 1)))} className="input !py-0 w-12" /></label>
        <label className="flex items-center gap-1" title="The most one stroke puts down">opacity
          <input type="range" min={0.05} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(+e.target.value)} className="w-16" /></label>
        {tool === "brush" && <label className="flex items-center gap-1">hard
          <input type="range" min={0} max={1} step={0.05} value={hardness} onChange={(e) => setHardness(+e.target.value)} className="w-14" /></label>}
        {shapeTool && <label className="flex items-center gap-1"><input type="checkbox" checked={filled} onChange={(e) => setFilled(e.target.checked)} /> filled</label>}
        <button onClick={() => setMirror((m) => !m)} title="Mirror X (M)" className={cls("p-1.5 rounded", mirror ? "bg-brand/15 text-brand" : "text-muted hover:text-text")}><FlipHorizontal size={13} /></button>
        <button onClick={() => setGrid((g) => !g)} title="Pixel grid" className={cls("p-1.5 rounded", grid ? "bg-brand/15 text-brand" : "text-muted hover:text-text")}><Grid3x3 size={13} /></button>
        <span className="w-px h-4 bg-line mx-1" />
        <button onClick={() => setZoom((z) => Math.max(1, z > 4 ? z - 2 : z - 1))} className="p-1 text-muted hover:text-text" title="Zoom out"><ZoomOut size={13} /></button>
        <span className="tabular-nums w-8 text-center">{zoom}×</span>
        <button onClick={() => setZoom((z) => Math.min(64, z >= 4 ? z + 2 : z + 1))} className="p-1 text-muted hover:text-text" title="Zoom in"><ZoomIn size={13} /></button>
        <button onClick={undo} disabled={!hist.current.canUndo} title="Undo (Ctrl+Z)" className="p-1 text-muted hover:text-text disabled:opacity-30"><Undo2 size={13} /></button>
        <button onClick={redo} disabled={!hist.current.canRedo} title="Redo (Ctrl+Y)" className="p-1 text-muted hover:text-text disabled:opacity-30"><Redo2 size={13} /></button>
        <span className="ml-auto" />
        <button onClick={() => setOpening((o) => !o)} className="btn-ghost !py-0.5" title="Open a picture or a drawing of this game"><FolderOpen size={12} /> Open</button>
        <button onClick={() => void save(false)} disabled={busy} className="btn-ghost !py-0.5" title="Save into the game's assets/drawn/"><Save size={12} /> Save</button>
        <button onClick={() => void save(true)} disabled={busy} className="btn-primary !py-0.5" title="Save, then put it into the running game">Save & place</button>
        <button onClick={onClose} className="p-1 text-muted hover:text-text" title="Close (Esc)"><X size={14} /></button>
      </div>
      {(note || busy) && <div className="shrink-0 px-3 py-1 text-[11px] text-muted border-b border-line bg-panel2/40 flex items-center gap-2">
        {busy && <Loader2 size={11} className="animate-spin" />}{note}</div>}

      <div className="flex-1 min-h-0 flex">
        <div ref={wrap} className="relative flex-1 min-w-0">
          <canvas ref={view} className={cls("absolute inset-0 touch-none", tool === "move" ? "cursor-move" : "cursor-crosshair")}
            onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
            onWheel={(e) => setZoom((z) => Math.max(1, Math.min(64, z + (e.deltaY < 0 ? (z >= 4 ? 2 : 1) : -(z > 4 ? 2 : 1)))))} />
          {opening && (
            <div className="absolute right-2 top-2 w-72 max-h-[70%] flex flex-col rounded border border-line bg-panel shadow-xl">
              <OpenList project={project} onPick={openFile} label="Pictures of this game" />
            </div>
          )}
        </div>

        <aside className="w-60 shrink-0 border-l border-line bg-panel flex flex-col min-h-0">
          <div className="p-2 border-b border-line space-y-1.5">
            <div className="flex items-center gap-1.5">
              <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} className="h-7 w-10 rounded border border-line bg-panel2 cursor-pointer" />
              <span className="font-mono">{colour}</span>
            </div>
            <div className="grid grid-cols-8 gap-0.5">
              {swatches.map((s) => (
                <button key={s} onClick={() => setColour(s)} title={s}
                  className={cls("h-5 rounded-sm border", s.toLowerCase() === colour.toLowerCase() ? "border-brand" : "border-line")} style={{ background: s }} />
              ))}
            </div>
          </div>

          <div className="p-2 border-b border-line">
            <div className="flex items-center gap-1 mb-1"><Layers size={12} className="text-muted" /><span className="font-medium">Layers</span>
              <button onClick={() => change((d) => setLayer(addLayer(d, layer + 1)))} className="ml-auto p-0.5 text-muted hover:text-text" title="New layer"><Plus size={12} /></button>
              <button onClick={() => change((d) => { if (removeLayer(d, layer)) setLayer(Math.max(0, layer - 1)); })} disabled={doc.layers.length <= 1} className="p-0.5 text-muted hover:text-danger disabled:opacity-30" title="Delete layer"><Trash2 size={12} /></button>
              <button onClick={() => change((d) => { if (mergeDown(d, layer)) setLayer(layer - 1); })} disabled={layer <= 0} className="p-0.5 text-muted hover:text-text disabled:opacity-30 text-[10px]" title="Merge down">merge</button>
            </div>
            <div className="space-y-0.5 max-h-40 overflow-y-auto">
              {doc.layers.map((l, i) => i).reverse().map((i) => {
                const l = doc.layers[i];
                return (
                  <div key={l.id} onClick={() => setLayer(i)}
                    className={cls("flex items-center gap-1 px-1 py-0.5 rounded cursor-pointer", i === layer ? "bg-brand/10 text-text" : "hover:bg-panel2 text-muted")}>
                    <button onClick={(e) => { e.stopPropagation(); l.visible = !l.visible; setDoc({ ...doc }); }} className="p-0.5">{l.visible ? <Eye size={11} /> : <EyeOff size={11} />}</button>
                    <input value={l.name} onChange={(e) => { l.name = e.target.value; setDoc({ ...doc }); }} onClick={(e) => e.stopPropagation()}
                      className="flex-1 min-w-0 bg-transparent outline-none" />
                    <input type="range" min={0} max={1} step={0.05} value={l.opacity} title="Layer opacity" onClick={(e) => e.stopPropagation()}
                      onChange={(e) => { l.opacity = +e.target.value; setDoc({ ...doc }); }} className="w-12" />
                    <button onClick={(e) => { e.stopPropagation(); change((d) => setLayer(moveLayer(d, i, i + 1))); }} className="p-0.5 text-muted hover:text-text" title="Up">▲</button>
                    <button onClick={(e) => { e.stopPropagation(); change((d) => setLayer(moveLayer(d, i, i - 1))); }} className="p-0.5 text-muted hover:text-text" title="Down">▼</button>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="p-2 space-y-1.5">
            <div className="flex items-center gap-1"><span className="font-medium">Frames</span>
              <span className="text-muted">{frame + 1}/{doc.frames.length}</span>
              <button onClick={() => change((d) => setFrame(addFrame(d, frame, false)))} className="ml-auto p-0.5 text-muted hover:text-text" title="New empty frame"><Plus size={12} /></button>
              <button onClick={() => change((d) => setFrame(addFrame(d, frame, true)))} className="p-0.5 text-muted hover:text-text" title="Duplicate this frame"><Copy size={12} /></button>
              <button onClick={() => change((d) => { if (removeFrame(d, frame)) setFrame(Math.max(0, frame - 1)); })} disabled={doc.frames.length <= 1} className="p-0.5 text-muted hover:text-danger disabled:opacity-30" title="Delete frame"><Trash2 size={12} /></button>
            </div>
            <div className="flex flex-wrap gap-0.5">
              {doc.frames.map((_, f) => (
                <button key={f} onClick={() => { setPlaying(false); setFrame(f); }}
                  className={cls("w-7 h-6 rounded border text-[10px] tabular-nums", f === frame ? "border-brand text-brand bg-brand/10" : "border-line text-muted hover:text-text")}>{f + 1}</button>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <button onClick={() => setPlaying((p) => !p)} disabled={doc.frames.length < 2} className="btn-ghost !py-0.5 disabled:opacity-30">{playing ? <Pause size={12} /> : <Play size={12} />}</button>
              <button onClick={() => change((d) => setFrame(moveFrame(d, frame, frame - 1)))} disabled={frame <= 0} className="p-0.5 text-muted hover:text-text disabled:opacity-30" title="Move earlier"><Minus size={11} /></button>
              <label className="flex items-center gap-1 ml-1">fps <input type="number" min={1} max={60} value={doc.fps} onChange={(e) => { doc.fps = Math.max(1, Math.min(60, +e.target.value || 1)); setDoc({ ...doc }); }} className="input !py-0 w-12" /></label>
              <label className="flex items-center gap-1 ml-auto" title="Show the frames before (red) and after (green)"><input type="checkbox" checked={onion} onChange={(e) => setOnion(e.target.checked)} /> onion</label>
            </div>
            <div className="text-[10px] text-muted/80 leading-snug">
              , and . step frames. Shift keeps a square, a circle or 45° lines. Saved as a PNG; with frames, also a sprite sheet and its atlas JSON for Phaser and PixiJS.
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

/** Pictures of the game to open: what /api/engine/assets lists as images and textures. */
function OpenList({ project, onPick, label }: { project: string; onPick: (file: string) => void; label: string }) {
  const [items, setItems] = useState<LibraryAsset[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    let dead = false;
    Promise.all([api.engineAssets(project, { type: "image" }), api.engineAssets(project, { type: "texture" })])
      .then(([a, b]) => { if (!dead) setItems([...(a.items || []), ...(b.items || [])].filter((x) => !/\.sheet\.png$/i.test(x.file))); })
      .catch(() => { if (!dead) setItems([]); });
    return () => { dead = true; };
  }, [project]);
  const low = q.trim().toLowerCase();
  const rows = (items || []).filter((x) => !low || x.file.toLowerCase().includes(low));
  return (
    <div className="flex flex-col min-h-0 text-xs">
      <div className="flex items-center gap-1.5 p-1.5 border-b border-line">
        <span className="text-muted shrink-0">{label}</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.stopPropagation()} placeholder="filter" className="flex-1 min-w-0 bg-transparent outline-none" />
      </div>
      <div className="overflow-y-auto max-h-48">
        {!items && <div className="p-2 text-muted flex items-center gap-2"><Loader2 size={12} className="animate-spin" /> reading…</div>}
        {rows.slice(0, 200).map((x) => (
          <button key={x.id} onClick={() => onPick(x.file)} title={x.file} className="w-full text-left truncate px-2 py-0.5 hover:bg-panel2">{x.file}</button>
        ))}
        {items && !rows.length && <div className="p-2 text-muted">No picture in this game yet.</div>}
      </div>
    </div>
  );
}
