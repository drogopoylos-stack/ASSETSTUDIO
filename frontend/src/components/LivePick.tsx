import { useCallback, useEffect, useRef, useState } from "react";
import { Crosshair, ExternalLink, Loader2, RefreshCw, Send, X } from "lucide-react";
import { api, fileUrl } from "../api/client";
import { useStore } from "../store/useStore";
import { cls } from "./ui";

// CLICK A THING IN THE RUNNING PAGE, AND TELL THE AGENT ABOUT IT.
//
// Describing a visual fault in words is three round trips: the agent has to find the component,
// guess which element it renders, and guess which rule wins. The page knows all three, so the
// click asks the page — the element under that point, the computed style that actually applies,
// and a cropped picture of it — and drops the lot into the composer, where it can still be
// edited before it is sent.
//
// WHY A PICTURE AND NOT AN IFRAME. A dev server is another origin, so nothing of ours can run
// inside an iframe of it: `contentDocument` is null and no listener reaches the DOM. The live
// link already drives a real Chrome over CDP and that Chrome can read anything, so what is shown
// here is a frame from that Chrome and the click is sent back through CDP as a FRACTION of the
// frame. Fractions, not pixels: the frame is captured at the emulated device's ratio and drawn
// at whatever width this pane happens to be, and a pixel would carry both scale factors.
//
// The image is sized `max-w-full max-h-full w-auto h-auto` on purpose. With `object-contain` the
// element's box includes the letterboxing, so a click near an edge would map to a point the user
// never aimed at; sized this way the element's box IS the picture.

type Pick = Awaited<ReturnType<typeof api.livePick>>;

export function LivePick({ projectId, rootPath, rootName }: {
  projectId: string;
  rootPath: string;
  rootName?: string;
}) {
  const [shot, setShot] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [got, setGot] = useState<Pick | null>(null);
  const [mark, setMark] = useState<{ x: number; y: number } | null>(null);
  const insert = useStore((s) => s.insertIntoComposer);
  const toast = useStore((s) => s.toast);
  // Set true on the way IN as well as false on the way out. React's StrictMode mounts a
  // component, unmounts it and mounts it again; a ref only cleared on unmount would still read
  // false for the second, real mount, and every answer after an await would be thrown away — a
  // tab that stays blank in development and works in a build.
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const frame = useCallback(async (open: boolean) => {
    setErr("");
    setBusy(open ? "Opening the page…" : "Taking a frame…");
    try {
      if (open) {
        const o = await api.liveOpen(rootPath);
        if (o?.error) { setErr(o.error); return; }
      }
      const s = await api.liveShot(rootPath);
      if (!alive.current) return;
      if (!s.ok || !s.path) { setErr(s.error || "the browser returned no frame"); return; }
      // The path does not change between frames on a fast machine, so the cache would show the
      // old picture. The query is what makes the reload real.
      setShot(fileUrl(s.path) + "&t=" + Date.now());
      setGot(null);
      setMark(null);
    } catch (e: any) {
      setErr(/404/.test(e?.message || "")
        ? "Restart the backend to use this — the endpoint is newer than the running process."
        : e?.message || "could not reach the page");
    } finally {
      if (alive.current) setBusy("");
    }
  }, [rootPath]);

  // Open on mount: the tab was opened deliberately, so asking for a second click to see anything
  // would be a step for nothing.
  useEffect(() => { frame(true); }, [frame]);

  async function onPick(ev: React.MouseEvent<HTMLImageElement>) {
    const r = ev.currentTarget.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const fx = (ev.clientX - r.left) / r.width;
    const fy = (ev.clientY - r.top) / r.height;
    setMark({ x: fx, y: fy });
    setBusy("Reading the element…");
    setErr("");
    try {
      const p = await api.livePick({ project: rootPath, x: fx, y: fy });
      if (!alive.current) return;
      if (!p.ok) { setErr(p.error || "nothing is drawn there"); setGot(null); return; }
      setGot(p);
    } catch (e: any) {
      setErr(e?.message || "the pick failed");
    } finally {
      if (alive.current) setBusy("");
    }
  }

  function send() {
    if (!got?.prompt) return;
    insert(projectId, got.prompt);
    toast("Sent to the composer — add a sentence and press Enter", "ok");
  }

  return (
    <div className="h-full flex flex-col bg-bg min-h-0">
      <div className="shrink-0 flex items-center gap-2 px-2 py-1.5 border-b border-line text-xs bg-panel">
        <Crosshair size={13} className="text-brand" />
        <span className="font-semibold">Inspect</span>
        <span className="text-muted truncate">· {rootName || rootPath}</span>
        {busy && <span className="flex items-center gap-1 text-muted"><Loader2 size={11} className="animate-spin" /> {busy}</span>}
        <button className="ml-auto chip hover:text-text disabled:opacity-50" disabled={!!busy}
          onClick={() => frame(false)} title="Take the frame again"><RefreshCw size={11} /> Frame</button>
        <button className="chip hover:text-text disabled:opacity-50" disabled={!!busy}
          onClick={() => frame(true)} title="Open (or re-open) the project's page in the shared browser">
          <ExternalLink size={11} /> Open page
        </button>
      </div>

      {err && (
        <div className="shrink-0 px-2 py-1.5 text-[11px] text-danger bg-danger/10 border-b border-danger/30">
          {err}
        </div>
      )}

      <div className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0 flex items-center justify-center p-2 overflow-auto relative">
          {shot ? (
            <div className="relative">
              <img src={shot} alt="the running page" onClick={onPick}
                className="max-w-full max-h-full w-auto h-auto cursor-crosshair rounded border border-line" />
              {mark && (
                <span className="pointer-events-none absolute h-3 w-3 -ml-1.5 -mt-1.5 rounded-full border-2 border-brand bg-brand/30"
                  style={{ left: `${mark.x * 100}%`, top: `${mark.y * 100}%` }} />
              )}
            </div>
          ) : (
            <div className="text-muted/60 text-xs leading-relaxed max-w-sm text-center">
              {busy ? "…" : "No frame yet. The live game link must be on, and the project needs a page a browser can open."}
            </div>
          )}
        </div>

        <div className="w-80 shrink-0 border-l border-line bg-panel/40 flex flex-col min-h-0">
          {!got ? (
            <div className="p-3 text-[11px] text-muted/70 leading-relaxed">
              Click anything in the picture. The page answers with what is under that point — the
              element, the computed style that applies to it, and a crop of it — and the whole lot
              goes into the composer for the agent.
            </div>
          ) : (
            <>
              <div className="flex-1 min-h-0 overflow-auto p-2 space-y-2 text-[11px]">
                <div className="font-mono text-text break-all">
                  &lt;{got.tag}
                  {got.id && <span className="text-brand"> #{got.id}</span>}
                  {(got.classes || []).slice(0, 4).map((c) => <span key={c} className="text-ok"> .{c}</span>)}
                  &gt;
                </div>
                {got.selector && <div className="font-mono text-muted break-all">{got.selector}</div>}
                {got.box && (
                  <div className="text-muted tabular-nums">
                    {got.box.w}×{got.box.h} at ({got.box.x}, {got.box.y})
                  </div>
                )}
                {got.canvas && (
                  <div className="text-warn leading-snug">
                    This page draws into one canvas — the point is ({got.canvas.x}, {got.canvas.y})
                    of a {got.canvas.width}×{got.canvas.height} backing store.
                  </div>
                )}
                {got.text && <div className="text-text/80 break-words">“{got.text}”</div>}
                {got.crop && (
                  <img src={fileUrl(got.crop) + "&t=" + Date.now()} alt="the element"
                    className="max-w-full rounded border border-line bg-panel2" />
                )}
                {got.css && Object.keys(got.css).length > 0 && (
                  <div className="rounded border border-line overflow-hidden">
                    {Object.entries(got.css).map(([k, v]) => (
                      <div key={k} className="flex gap-2 px-1.5 py-0.5 font-mono odd:bg-panel2/40">
                        <span className="text-muted shrink-0">{k}</span>
                        <span className="text-text/85 break-all">{v}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="shrink-0 p-2 border-t border-line flex gap-2">
                <button className="btn-primary !py-1 !px-2 text-xs flex-1" onClick={send}>
                  <Send size={12} /> Send to the agent
                </button>
                <button className={cls("chip hover:text-text")} title="Forget this pick"
                  onClick={() => { setGot(null); setMark(null); }}><X size={12} /></button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
