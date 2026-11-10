import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Brush, Eye, EyeOff, Gamepad2, ImagePlus, Loader2, MousePointer2, Pause, Play, RefreshCw, RotateCcw, Search, Undo2, X } from "lucide-react";
import { api, liveStreamStatus, liveStreamWsUrl, type LiveStreamStatus, type StageRow, type StageView } from "../../api/client";
import type { LibraryAsset } from "../../types";
import { cls, pollWhileVisible } from "../ui";
import { StreamImg } from "./GameFrame";
import { Draw2D } from "./Draw2D";

// The Edit tab for a 2D game: Phaser, PixiJS, or a page of HTML.
//
// The 3D editor mirrors a game's scene into a viewport of its own. A 2D game has nothing to mirror:
// what it draws IS the picture. So this shows the live tab's own frames, the same stream as the Game
// tab's "Agent's view", with the game's objects drawn on top as boxes from GET /api/live/stage.
//
// A CHANGE IS SENT WHEN THE POINTER IS LET GO, NOT WHILE IT MOVES. Each call is a round trip to the
// page; a drag moves the box here at once and sends one edit at the end. Every change is saved, as
// in a document: Undo takes it back, live and on disk.

type Tool = "select" | "play";
type Handle = "move" | "scale" | "rotate";

interface Drag {
  handle: Handle;
  key: string;
  x0: number;
  y0: number;
  x: number;
  y: number;
  box: [number, number, number, number];
}

const POLL_MS = 500;
const ROTATE_GAP = 22;
const HANDLE = 9;
const ENGINE_LABEL: Record<string, string> = { phaser: "Phaser", pixi: "PixiJS", dom: "HTML page" };

const deg = (rad: number) => Math.round((rad * 180 / Math.PI) * 100) / 100;

/** The last row on the list is drawn last, so it is the one on top. */
export function hitRow(rows: StageRow[], x: number, y: number): StageRow | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (!r.visible) continue;
    const [bx, by, bw, bh] = r.box;
    if (x >= bx && x <= bx + bw && y >= by && y <= by + bh) return r;
  }
  return null;
}

/** What a finished drag asks the page to do. */
export function dragEdit(d: Drag, row: StageRow): Record<string, unknown> | null {
  const dx = d.x - d.x0, dy = d.y - d.y0;
  if (d.handle === "move") return dx || dy ? { move: [dx, dy] } : null;
  const [bx, by, bw, bh] = d.box;
  if (d.handle === "scale") {
    if (!bw || !bh || (!dx && !dy)) return null;
    const kx = Math.max(0.02, (bw + dx) / bw), ky = Math.max(0.02, (bh + dy) / bh);
    return { scale: [row.scale[0] * kx, row.scale[1] * ky] };
  }
  const cx = bx + bw / 2, cy = by + bh / 2;
  const a0 = Math.atan2(d.y0 - cy, d.x0 - cx), a1 = Math.atan2(d.y - cy, d.x - cx);
  const turn = (a1 - a0) * 180 / Math.PI;
  return Math.abs(turn) > 0.01 ? { rotate: [0, 0, turn] } : null;
}

export function Stage2D({ project, name, onClose }: { project: string; name: string; onClose: () => void }) {
  const [view, setView] = useState<StageView | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [sel, setSel] = useState("");
  const [tool, setTool] = useState<Tool>("select");
  const [drag, setDrag] = useState<Drag | null>(null);
  const [filter, setFilter] = useState("");
  const [adding, setAdding] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [st, setSt] = useState<LiveStreamStatus | null>(null);
  const [attempt, setAttempt] = useState(1);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const boxRef = useRef<HTMLDivElement | null>(null);
  const dragging = useRef(false);
  dragging.current = !!drag;

  const load = useCallback(async () => {
    if (dragging.current) return;
    try {
      const v = await api.liveStage(project);
      if (v.ok) { setView(v); setErr(""); }
      else setErr(v.error || "the game did not answer");
    } catch (e: any) {
      setErr(String(e?.message || e).slice(0, 240));
    }
  }, [project]);

  useEffect(() => pollWhileVisible(load, POLL_MS), [load]);

  // The stream of the live tab. Status every three seconds, as the Game tab does. A person is here
  // looking at it, so a stream that the server ended for being idle is simply started again.
  useEffect(() => {
    let dead = false;
    const stop = pollWhileVisible(() => liveStreamStatus(project).then((s) => {
      if (dead) return;
      setSt(s);
      if (s.live && !s.streaming) setAttempt((a) => a + 1);
    }).catch(() => { /* the next poll asks again */ }), 3000);
    return () => { dead = true; stop(); };
  }, [project]);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const rows = view?.rows || [];
  const [W, H] = view?.view || [0, 0];
  const k = W && H && size.w && size.h ? Math.min(size.w / W, size.h / H) : 1;
  const ox = (size.w - W * k) / 2, oy = (size.h - H * k) / 2;
  const toPage = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = boxRef.current!.getBoundingClientRect();
    return [(e.clientX - r.left - ox) / k, (e.clientY - r.top - oy) / k];
  };
  const picked = rows.find((r) => r.key === sel) || null;

  const send = useCallback(async (body: Record<string, unknown>, say = "") => {
    try {
      const r = await api.liveEdit(project, body);
      if (r.ok === false) setNote(String(r.error || "the change was refused"));
      else setNote(say);
      await load();
      return r;
    } catch (e: any) {
      setNote(String(e?.message || e).slice(0, 240));
      return null;
    }
  }, [project, load]);

  const change = useCallback((key: string, what: Record<string, unknown>) => send({ target: key, save: true, ...what }), [send]);

  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || !view) return;
    const [x, y] = toPage(e);
    if (tool === "play") {
      void api.liveInput(project, [{ type: "click", nx: x / (W || 1), ny: y / (H || 1) }]).then(load);
      return;
    }
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    let hit: StageRow | null = picked;
    const inside = hit && x >= hit.box[0] && x <= hit.box[0] + hit.box[2] && y >= hit.box[1] && y <= hit.box[1] + hit.box[3];
    // A click inside the selection drags it, also when something small sits on top of it. A second
    // click without a move goes to the object under the pointer: see onUp.
    if (!inside) {
      hit = hitRow(rows, x, y);
      setSel(hit?.key || "");
    }
    if (hit) setDrag({ handle: "move", key: hit.key, x0: x, y0: y, x, y, box: hit.box });
  };

  const onHandle = (e: React.PointerEvent, handle: Handle) => {
    e.stopPropagation();
    if (!picked || e.button !== 0) return;
    (boxRef.current as HTMLElement).setPointerCapture(e.pointerId);
    const [x, y] = toPage(e);
    setDrag({ handle, key: picked.key, x0: x, y0: y, x, y, box: picked.box });
  };

  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const [x, y] = toPage(e);
    setDrag({ ...drag, x, y });
  };

  const onUp = (e: React.PointerEvent) => {
    if (!drag) return;
    const d = drag;
    setDrag(null);
    const row = rows.find((r) => r.key === d.key);
    const what = row ? dragEdit(d, row) : null;
    if (what) { void change(d.key, what); return; }
    if (d.handle === "move") {
      const [x, y] = toPage(e);
      const hit = hitRow(rows, x, y);
      if (hit) setSel(hit.key);
    }
  };

  // Keys on the viewport: the arrows nudge, H hides, Escape lets go, Ctrl+Z undoes.
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); void send({ undo: true }, "Undone."); return; }
    if (tool === "play") {
      if (e.key.length === 1 || e.key.startsWith("Arrow") || e.key === "Enter" || e.key === "Escape") {
        e.preventDefault();
        const code = e.code;
        void api.liveInput(project, [{ type: "press", key: e.key, code, keyCode: e.keyCode }]);
      }
      return;
    }
    if (e.key === "Escape") { setSel(""); return; }
    if (!picked) return;
    const step = e.shiftKey ? 10 : 1;
    const arrows: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (arrows[e.key]) { e.preventDefault(); void change(picked.key, { move: arrows[e.key] }); return; }
    if (e.key.toLowerCase() === "h") void change(picked.key, { visible: !picked.visible });
  };

  // The drawn selection, with the drag in progress on it. The real object moves on release.
  const ghost = useMemo(() => {
    if (!picked) return null;
    let [x, y, w, h] = picked.box;
    let turn = 0;
    if (drag && drag.key === picked.key) {
      const dx = drag.x - drag.x0, dy = drag.y - drag.y0;
      if (drag.handle === "move") { x += dx; y += dy; }
      else if (drag.handle === "scale") { w = Math.max(1, w + dx); h = Math.max(1, h + dy); }
      else {
        const cx = x + w / 2, cy = y + h / 2;
        turn = (Math.atan2(drag.y - cy, drag.x - cx) - Math.atan2(drag.y0 - cy, drag.x0 - cx)) * 180 / Math.PI;
      }
    }
    return { x, y, w, h, turn };
  }, [picked, drag]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? rows.filter((r) => [r.key, r.name, r.text, r.texture].some((s) => String(s || "").toLowerCase().includes(q))) : rows;
  }, [rows, filter]);

  const pause = async () => {
    const r = await api.liveStagePause(project, !view?.paused);
    if (r.ok === false) setNote(String(r.error || "the game could not be paused"));
    await load();
  };

  const reload = async () => {
    setNote("reloading the game…");
    try {
      await api.liveReload(project);
      setNote("");
      setAttempt((a) => a + 1);
      await load();
    } catch (e: any) { setNote(String(e?.message || e).slice(0, 240)); }
  };

  const engine = view?.engine || "";
  const onlyCanvas = engine === "dom" && rows.length > 0 && rows.every((r) => r.type === "canvas" || r.type === "body");
  const src = st?.live ? liveStreamWsUrl(project, { n: attempt }) : "";

  return (
    <div className="absolute inset-0 flex flex-col">
      {drawing && <Draw2D project={project} onClose={() => setDrawing(false)} onPlaced={(n) => { setNote(n); void load(); }} />}
      <div className="h-9 shrink-0 flex items-center gap-1 px-2 border-b border-line bg-panel text-[11px] min-w-0">
        <span className="font-medium truncate max-w-[12rem]" title={project}>{name}</span>
        <span className="chip shrink-0">{ENGINE_LABEL[engine] || "2D"}</span>
        <span className="w-px h-4 bg-line mx-1 shrink-0" />
        <ToolButton on={tool === "select"} onClick={() => setTool("select")} title="Select objects, drag to move them. Arrow keys nudge, H hides.">
          <MousePointer2 size={12} /> Select
        </ToolButton>
        <ToolButton on={tool === "play"} onClick={() => setTool("play")} title="Clicks and keys go to the game, so you can reach the screen you want to edit.">
          <Gamepad2 size={12} /> Play
        </ToolButton>
        <span className="w-px h-4 bg-line mx-1 shrink-0" />
        {engine && engine !== "dom" && (
          <ToolButton onClick={pause} title="Hold the game still, so a moving object can be selected.">
            {view?.paused ? <><Play size={12} /> Resume</> : <><Pause size={12} /> Pause</>}
          </ToolButton>
        )}
        <ToolButton onClick={() => setAdding((a) => !a)} on={adding} title="Put an image of this project into the game.">
          <ImagePlus size={12} /> Add image
        </ToolButton>
        <ToolButton onClick={() => setDrawing(true)} on={drawing} title="Draw a sprite: pixel art or painted, with layers and animation frames, saved into the game.">
          <Brush size={12} /> Draw
        </ToolButton>
        <ToolButton onClick={() => void send({ undo: true }, "Undone.")} title="Take the last change back, in the game and in the file (Ctrl+Z).">
          <Undo2 size={12} /> Undo
        </ToolButton>
        <ToolButton onClick={reload} title="Reload the game. The saved changes are applied again.">
          <RefreshCw size={12} /> Reload
        </ToolButton>
        <span className="ml-auto text-muted truncate min-w-0" title={note || "Every change is saved in studio.edits.json"}>
          {note || (view?.saved?.length ? `${view.saved.length} saved in studio.edits.json` : "changes are saved in studio.edits.json")}
        </span>
        <button onClick={onClose} className="p-1 rounded text-muted hover:text-text shrink-0" title="Close the 2D editor"><X size={13} /></button>
      </div>

      {err && <div className="shrink-0 px-3 py-1.5 text-xs text-warn bg-warn/10 border-b border-warn/30">{err}</div>}
      {onlyCanvas && (
        <div className="shrink-0 px-3 py-1.5 text-xs text-muted bg-panel2/50 border-b border-line">
          This game draws its objects on a canvas with its own code, so the Studio can move the canvas and the HTML on the page, but not what the canvas draws.
        </div>
      )}

      <div className="flex-1 min-h-0 flex">
        <div ref={boxRef} tabIndex={0} onKeyDown={onKey} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
          className={cls("relative flex-1 min-w-0 bg-black outline-none touch-none select-none overflow-hidden", tool === "play" ? "cursor-pointer" : "cursor-default")}>
          {src && <StreamImg key={src} url={src} onLoad={() => {}} title={`${name}, live`} />}
          {!st?.live && (
            <div className="absolute inset-0 grid place-items-center text-muted text-xs pointer-events-none">
              <span className="flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> opening the game…</span>
            </div>
          )}
          {tool === "select" && W > 0 && (
            <svg className="absolute inset-0 w-full h-full pointer-events-none">
              {rows.map((r) => r.visible && r.key !== sel && (
                <rect key={r.key} x={ox + r.box[0] * k} y={oy + r.box[1] * k} width={r.box[2] * k} height={r.box[3] * k}
                  fill="none" stroke="rgb(var(--c-accent))" strokeOpacity={0.28} strokeWidth={1} />
              ))}
            </svg>
          )}
          {tool === "select" && ghost && (
            <div className="absolute pointer-events-none border border-brand"
              style={{ left: ox + ghost.x * k, top: oy + ghost.y * k, width: ghost.w * k, height: ghost.h * k, transform: ghost.turn ? `rotate(${ghost.turn}deg)` : undefined }}>
              {!drag && (
                <>
                  <span className="absolute left-1/2 -translate-x-1/2 w-px bg-brand" style={{ top: -ROTATE_GAP, height: ROTATE_GAP }} />
                  <span onPointerDown={(e) => onHandle(e, "rotate")} title="Drag to turn"
                    className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand pointer-events-auto cursor-grab"
                    style={{ top: -ROTATE_GAP, width: HANDLE + 2, height: HANDLE + 2 }} />
                  <span onPointerDown={(e) => onHandle(e, "scale")} title="Drag to resize"
                    className="absolute bg-brand pointer-events-auto cursor-nwse-resize"
                    style={{ right: -HANDLE / 2, bottom: -HANDLE / 2, width: HANDLE, height: HANDLE }} />
                </>
              )}
            </div>
          )}
        </div>

        <aside className="w-72 shrink-0 border-l border-line bg-panel flex flex-col min-h-0 text-xs">
          {adding ? (
            <AddImage project={project} onClose={() => setAdding(false)}
              onPick={async (file) => {
                const r = await api.livePlace(project, { asset: file, save: true });
                if (r.ok === false) setNote(String(r.error || "the image could not be placed"));
                else { setNote(`Placed ${r.name || file}.`); setAdding(false); if (r.key) setSel(String(r.key)); }
                await load();
              }} />
          ) : (
            <>
              <div className="shrink-0 p-2 border-b border-line flex items-center gap-1.5">
                <Search size={12} className="text-muted shrink-0" />
                <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`${rows.length} objects`}
                  className="flex-1 min-w-0 bg-transparent outline-none placeholder:text-muted/60" />
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto py-1">
                {[...shown].reverse().map((r) => (
                  <div key={r.key} onClick={() => setSel(r.key)} title={r.key}
                    className={cls("flex items-center gap-1.5 px-2 py-0.5 cursor-pointer", r.key === sel ? "bg-brand/15 text-brand" : "hover:bg-panel2")}>
                    <span className="truncate flex-1 min-w-0">{r.name}</span>
                    {r.placed && <span className="text-[10px] text-accent shrink-0">placed</span>}
                    <span className="text-[10px] text-muted shrink-0">{r.type}</span>
                    <button onClick={(e) => { e.stopPropagation(); void change(r.key, { visible: !r.visible }); }}
                      className="text-muted hover:text-text shrink-0" title={r.visible ? "Hide" : "Show"}>
                      {r.visible ? <Eye size={12} /> : <EyeOff size={12} />}
                    </button>
                  </div>
                ))}
                {view && !shown.length && <div className="p-3 text-muted">Nothing on this screen has a name or a box to show.</div>}
              </div>
              {picked && (
                <Inspector key={picked.key + ":" + picked.pos.join() + ":" + picked.rot + ":" + picked.scale.join() + ":" + picked.alpha}
                  row={picked} engine={engine} onChange={(what) => void change(picked.key, what)}
                  onReset={() => void send({ clear: [picked.placed || picked.key] }, "Taken out of the file.").then(reload)} />
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function ToolButton({ on, onClick, title, children }: { on?: boolean; onClick: () => void; title: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} title={title}
      className={cls("inline-flex items-center gap-1 px-2 py-0.5 rounded shrink-0", on ? "bg-brand/15 text-brand" : "text-muted hover:text-text hover:bg-panel2")}>
      {children}
    </button>
  );
}

function Field({ label, value, onCommit, step = 1 }: { label: string; value: number; onCommit: (v: number) => void; step?: number }) {
  const [text, setText] = useState(String(value));
  const commit = () => {
    const v = Number(text);
    if (text.trim() !== "" && isFinite(v) && v !== value) onCommit(v);
    else setText(String(value));
  };
  return (
    <label className="flex items-center gap-1 min-w-0">
      <span className="text-muted w-9 shrink-0">{label}</span>
      <input type="number" step={step} value={text} onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); e.stopPropagation(); }}
        className="flex-1 min-w-0 rounded border border-line bg-panel2/50 px-1 py-0.5 font-mono text-[11px] outline-none focus:border-brand/50" />
    </label>
  );
}

function Inspector({ row, engine, onChange, onReset }: { row: StageRow; engine: string; onChange: (what: Record<string, unknown>) => void; onReset: () => void }) {
  return (
    <div className="shrink-0 border-t border-line p-2 space-y-1.5">
      <div className="font-medium truncate" title={row.key}>{row.name}</div>
      <div className="text-[10px] font-mono text-muted truncate" title={row.key}>{row.key}</div>
      {(row.texture || row.text) && <div className="text-[10px] text-muted truncate">{row.texture || row.text}</div>}
      <div className="grid grid-cols-2 gap-1.5">
        <Field label={engine === "dom" ? "Off X" : "X"} value={row.pos[0]} onCommit={(v) => onChange({ pos: [v, row.pos[1]] })} />
        <Field label={engine === "dom" ? "Off Y" : "Y"} value={row.pos[1]} onCommit={(v) => onChange({ pos: [row.pos[0], v] })} />
        <Field label="Scale X" value={row.scale[0]} step={0.05} onCommit={(v) => onChange({ scale: [v, row.scale[1]] })} />
        <Field label="Scale Y" value={row.scale[1]} step={0.05} onCommit={(v) => onChange({ scale: [row.scale[0], v] })} />
        <Field label="Turn °" value={deg(row.rot)} onCommit={(v) => onChange({ rot: [0, 0, v] })} />
        <Field label="Alpha" value={row.alpha} step={0.05} onCommit={(v) => onChange({ alpha: Math.max(0, Math.min(1, v)) })} />
      </div>
      <div className="flex items-center gap-1.5">
        <button className="btn-ghost !px-1.5 !py-0.5 text-[11px]" onClick={() => onChange({ visible: !row.visible })}>
          {row.visible ? <><EyeOff size={12} /> Hide</> : <><Eye size={12} /> Show</>}
        </button>
        <button className="btn-ghost !px-1.5 !py-0.5 text-[11px]" onClick={onReset} title="Take this object's saved change out of the file and reload the game.">
          <RotateCcw size={12} /> Reset
        </button>
      </div>
    </div>
  );
}

function AddImage({ project, onPick, onClose }: { project: string; onPick: (file: string) => void; onClose: () => void }) {
  const [items, setItems] = useState<LibraryAsset[] | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    let dead = false;
    Promise.all([api.engineAssets(project, { type: "image" }), api.engineAssets(project, { type: "texture" })]).then(([a, b]) => {
      if (dead) return;
      setItems([...(a.items || []), ...(b.items || [])]);
    }).catch((e) => { if (!dead) setErr(String(e?.message || e).slice(0, 200)); });
    return () => { dead = true; };
  }, [project]);
  const low = q.trim().toLowerCase();
  const rows = (items || []).filter((x) => !low || x.file.toLowerCase().includes(low));
  return (
    <>
      <div className="shrink-0 p-2 border-b border-line flex items-center gap-1.5">
        <Search size={12} className="text-muted shrink-0" />
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Images of this project"
          onKeyDown={(e) => e.stopPropagation()}
          className="flex-1 min-w-0 bg-transparent outline-none placeholder:text-muted/60" />
        <button onClick={onClose} className="text-muted hover:text-text"><X size={12} /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto py-1">
        {!items && !err && <div className="p-3 text-muted flex items-center gap-2"><Loader2 size={13} className="animate-spin" /> reading the project…</div>}
        {err && <div className="p-3 text-warn">{err}</div>}
        {rows.slice(0, 300).map((x) => (
          <button key={x.id} onClick={() => onPick(x.file)} title={x.file}
            className="w-full text-left truncate px-2 py-0.5 hover:bg-panel2">{x.file}</button>
        ))}
        {items && !rows.length && <div className="p-3 text-muted">No image was found in this project.</div>}
      </div>
    </>
  );
}
