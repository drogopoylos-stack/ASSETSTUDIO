// The graph editor: Blender's, for the clip's channels of the selected part or bone.
//
// Each channel (pos X, rot Z, ...) is drawn as the curve that actually plays (graph.ts), with its
// keys on it. Drag a key to move it in time and value (whole frames; Alt for between), drag a
// handle to shape the curve (Shift breaks it), pick an interpolation for the chosen keys, or click
// the background to go to that frame. A drag shows in the viewport while it lasts and is ONE step
// of undo when it ends.

import { useEffect, useMemo, useRef, useState } from "react";
import { cls } from "../../ui";
import type { Clip, Key, Track } from "./kit";
import { EASES, curvePoints, dragHandle, graphBounds, handlePoints, moveKey, type Ease } from "./graph";

const AXIS_COLOR = ["#e5484d", "#46a758", "#3e63dd"];
const PROP_DASH: Record<string, string> = { pos: "", rot: "5 3", scale: "1 3" };
const AXIS = ["X", "Y", "Z"];

type Drag =
  | { kind: "key"; x0: number; y0: number; orig: Map<Key, [number, number]>; moved: boolean }
  | { kind: "handle"; track: Track; key: Key; side: "in" | "out"; moved: boolean }
  | { kind: "scrub" }
  | null;

export function GraphEditor({ clip, target, frame, onFrame, onPreview, onCommit }: {
  clip: Clip; target: string; frame: number;
  onFrame(f: number): void; onPreview(c: Clip): void; onCommit(c: Clip): void;
}) {
  // The copy a drag works on; between drags it is the clip as given.
  const [work, setWork] = useState<Clip>(() => structuredClone(clip));
  const drag = useRef<Drag>(null);
  // Only a clip that differs is copied again (an undo, another clip): the one this panel just saved
  // is the same, and copying it would drop the chosen keys.
  useEffect(() => {
    if (!drag.current && JSON.stringify(clip) !== JSON.stringify(work)) setWork(structuredClone(clip));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip]);

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [sel, setSel] = useState<Set<Key>>(new Set());
  useEffect(() => { setSel(new Set()); }, [target]);

  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 600, h: 180 });
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: Math.max(100, el.clientWidth), h: Math.max(60, el.clientHeight) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const tracks = work.tracks.filter((t) => t.target === target);
  const chan = (t: Track) => t.prop + t.axis;
  const shown = tracks.filter((t) => !hidden.has(chan(t)));

  // The view fits every shown key and the curve between them; a drag freezes it so the key under
  // the pointer does not run away from it.
  const fit = useMemo(() => graphBounds(shown.map((t) => t.keys), work.start, work.end),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(shown.map((t) => t.keys)), work.start, work.end]);
  const frozen = useRef(fit);
  const view = drag.current && drag.current.kind !== "scrub" ? frozen.current : fit;
  if (!drag.current) frozen.current = fit;

  const pad = 8;
  const X = (t: number) => pad + ((t - view.t0) / Math.max(1e-9, view.t1 - view.t0)) * (size.w - 2 * pad);
  const Y = (v: number) => pad + (1 - (v - view.v0) / Math.max(1e-9, view.v1 - view.v0)) * (size.h - 2 * pad);
  const T = (x: number) => view.t0 + ((x - pad) / Math.max(1, size.w - 2 * pad)) * (view.t1 - view.t0);
  const V = (y: number) => view.v0 + (1 - (y - pad) / Math.max(1, size.h - 2 * pad)) * (view.v1 - view.v0);

  const local = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const publish = (c: Clip) => { setWork({ ...c, tracks: [...c.tracks] }); onPreview(c); };

  const downKey = (e: React.PointerEvent, k: Key) => {
    e.stopPropagation();
    const next = new Set(e.shiftKey ? sel : []);
    if (e.shiftKey && sel.has(k)) next.delete(k); else next.add(k);
    setSel(next);
    const p = local(e);
    const orig = new Map<Key, [number, number]>();
    for (const s of next) orig.set(s, [s.t, s.v]);
    drag.current = { kind: "key", x0: p.x, y0: p.y, orig, moved: false };
    capture(e);
  };
  // The panel holds the pointer, not the dot under it: a dot re-drawn mid-drag lost the capture,
  // the release never arrived, and the panel kept a drag open that blocked every later update.
  const capture = (e: React.PointerEvent) => { try { box.current?.setPointerCapture(e.pointerId); } catch { /* gone */ } };
  const downHandle = (e: React.PointerEvent, track: Track, k: Key, side: "in" | "out") => {
    e.stopPropagation();
    drag.current = { kind: "handle", track, key: k, side, moved: false };
    capture(e);
  };
  const downBack = (e: React.PointerEvent) => {
    if (!e.shiftKey) setSel(new Set());
    drag.current = { kind: "scrub" };
    onFrame(Math.round(T(local(e).x)));
    capture(e);
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = local(e);
    if (d.kind === "scrub") { onFrame(Math.round(T(p.x))); return; }
    if (d.kind === "key") {
      let dt = T(p.x) - T(d.x0);
      if (!e.altKey) dt = Math.round(dt);
      const dv = V(p.y) - V(d.y0);
      if (!d.moved && Math.hypot(p.x - d.x0, p.y - d.y0) < 3) return;
      d.moved = true;
      for (const tr of work.tracks) {
        for (const k of [...tr.keys]) {
          const o = d.orig.get(k);
          if (o) moveKey(tr.keys, tr.keys.indexOf(k), Math.round((o[0] + dt) * 100) / 100, o[1] + dv);
        }
      }
      publish(work);
      return;
    }
    const keys = d.track.keys;
    const i = keys.indexOf(d.key);
    if (i < 0) return;
    const s = dragHandle(keys, i, d.side, [T(p.x), V(p.y)], e.shiftKey);
    d.key.ti = s.ti;
    d.key.to = s.to;
    if (d.key.ease !== "bezier") d.key.ease = "bezier";
    d.moved = true;
    publish(work);
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (d && d.kind !== "scrub" && d.moved) onCommit(structuredClone(work));
  };
  // ...and a release anywhere ends a drag, so one can never stay open.
  const upRef = useRef(up);
  upRef.current = up;
  useEffect(() => {
    const end = () => { if (drag.current) upRef.current(); };
    window.addEventListener("pointerup", end);
    window.addEventListener("blur", end);
    return () => { window.removeEventListener("pointerup", end); window.removeEventListener("blur", end); };
  }, []);

  /** A change to the chosen keys, saved at once. */
  const edit = (fn: (k: Key) => void, drop = false) => {
    const c = structuredClone(work);
    // The copy has new key objects: find the chosen ones by place in the same order.
    const pick = new Set<string>();
    work.tracks.forEach((tr, ti) => tr.keys.forEach((k, ki) => { if (sel.has(k)) pick.add(ti + ":" + ki); }));
    c.tracks.forEach((tr, ti) => { tr.keys = tr.keys.filter((k, ki) => { if (!pick.has(ti + ":" + ki)) return true; fn(k); return !drop; }); });
    c.tracks = c.tracks.filter((tr) => tr.keys.length);
    setSel(new Set());
    onCommit(c);
  };

  const ease0 = [...sel][0]?.ease || "";
  const ticks = useMemo(() => {
    const span = view.v1 - view.v0;
    const step = Math.pow(10, Math.floor(Math.log10(span / 3)));
    const m = span / step > 15 ? 5 : span / step > 7 ? 2 : 1;
    const out: number[] = [];
    for (let v = Math.ceil(view.v0 / (step * m)) * step * m; v <= view.v1; v += step * m) out.push(+v.toFixed(6));
    return out;
  }, [view.v0, view.v1]);

  return (
    <div className="h-full flex min-h-0">
      <div className="w-28 shrink-0 border-r border-line overflow-y-auto py-1 text-[11px]">
        {!tracks.length && <div className="px-2 text-muted">{target ? "no keys on " + target : "select a part or a bone"}</div>}
        {tracks.map((t) => (
          <button key={chan(t)} onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(chan(t))) n.delete(chan(t)); else n.add(chan(t)); return n; })}
            className={cls("w-full flex items-center gap-1.5 px-2 py-0.5 text-left hover:bg-panel2", hidden.has(chan(t)) && "opacity-40")}>
            <span className="w-2.5 h-0.5 shrink-0" style={{ background: AXIS_COLOR[t.axis] }} />
            {t.prop} {AXIS[t.axis]}
            <span className="ml-auto text-muted/60">{t.keys.length}</span>
          </button>
        ))}
      </div>
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="h-7 shrink-0 flex items-center gap-1 px-2 border-b border-line text-[11px]">
          <span className="text-muted">{sel.size ? sel.size + " key" + (sel.size === 1 ? "" : "s") : "click a key · Shift adds · drag the background to scrub"}</span>
          <select value={sel.size ? ease0 : ""} disabled={!sel.size} title="Interpolation from the chosen keys to the next"
            onChange={(e) => edit((k) => { k.ease = e.target.value as Ease; })}
            className="ml-auto h-5 rounded bg-panel2 border border-line text-[11px] px-1 disabled:opacity-40">
            {!sel.size && <option value="">interpolation</option>}
            {EASES.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
          <button disabled={!sel.size} onClick={() => edit((k) => { delete k.ti; delete k.to; })} title="Automatic handles (auto clamped)"
            className="chip disabled:opacity-40">auto handles</button>
          <button disabled={!sel.size} onClick={() => edit(() => {}, true)} title="Delete the chosen keys"
            className="chip hover:text-danger disabled:opacity-40">delete</button>
        </div>
        <div ref={box} className="relative flex-1 min-h-0 bg-bg select-none overflow-hidden"
          onPointerDown={downBack} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          <svg width={size.w} height={size.h} className="absolute inset-0">
            {ticks.map((v) => (
              <g key={"v" + v}>
                <line x1={0} x2={size.w} y1={Y(v)} y2={Y(v)} stroke="currentColor" className="text-line" strokeWidth={v === 0 ? 1 : 0.5} />
                <text x={3} y={Y(v) - 2} className="fill-muted" fontSize={9}>{v}</text>
              </g>
            ))}
            <rect x={X(work.start)} y={0} width={Math.max(0, X(work.end) - X(work.start))} height={size.h} className="fill-panel2/30" />
            {shown.map((t) => (
              <polyline key={"c" + chan(t)} fill="none" stroke={AXIS_COLOR[t.axis]} strokeWidth={1.5} strokeDasharray={PROP_DASH[t.prop]}
                points={curvePoints(t.keys, view.t0, view.t1, Math.max(1, Math.min(8, 600 / Math.max(1, view.t1 - view.t0)))).map(([a, b]) => X(a) + "," + Y(b)).join(" ")} />
            ))}
            {shown.map((t) => t.keys.map((k, i) => {
              if (!sel.has(k)) return null;
              const h = handlePoints(t.keys, i);
              return (
                <g key={"h" + chan(t) + i}>
                  {h.in && <line x1={X(k.t)} y1={Y(k.v)} x2={X(h.in[0])} y2={Y(h.in[1])} stroke="#cfd6e2" strokeWidth={1} />}
                  {h.out && <line x1={X(k.t)} y1={Y(k.v)} x2={X(h.out[0])} y2={Y(h.out[1])} stroke="#cfd6e2" strokeWidth={1} />}
                  {h.in && <rect x={X(h.in[0]) - 3.5} y={Y(h.in[1]) - 3.5} width={7} height={7} fill="#cfd6e2" className="cursor-move"
                    onPointerDown={(e) => downHandle(e, t, k, "in")} />}
                  {h.out && <rect x={X(h.out[0]) - 3.5} y={Y(h.out[1]) - 3.5} width={7} height={7} fill="#cfd6e2" className="cursor-move"
                    onPointerDown={(e) => downHandle(e, t, k, "out")} />}
                </g>
              );
            }))}
            {shown.map((t) => t.keys.map((k, i) => (
              <circle key={"k" + chan(t) + i} cx={X(k.t)} cy={Y(k.v)} r={sel.has(k) ? 5 : 4}
                fill={sel.has(k) ? "#ff8c1a" : "#0d1117"} stroke={AXIS_COLOR[t.axis]} strokeWidth={1.5} className="cursor-pointer"
                onPointerDown={(e) => downKey(e, k)}>
                <title>{`${t.prop} ${AXIS[t.axis]} · frame ${k.t} · ${+k.v.toFixed(4)} · ${k.ease || "linear"}`}</title>
              </circle>
            )))}
            <line x1={X(frame)} x2={X(frame)} y1={0} y2={size.h} stroke="#3b82f6" strokeWidth={1} pointerEvents="none" />
          </svg>
        </div>
      </div>
    </div>
  );
}
