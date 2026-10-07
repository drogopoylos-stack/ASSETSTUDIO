import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Focus, Loader2, RotateCcw, Sparkles } from "lucide-react";
import { cls } from "../ui";
import { EngineChip } from "./EngineChip";
import { buildStudio, drawFlat, loadEngine, runGeneration, type EngineKind, type LiveStats, type Studio } from "./studio";
import { Orbit } from "./orbit";

// The viewport: one recorded generation, re-run in the project's own engine and orbited by hand.
//
// Every generation gets a fresh studio and the old one is torn down first — context loss forced,
// canvas removed — because a viewer that keeps a renderer per click is blank within a minute of
// use. The frame loop belongs to this component, not the engine, so an unmounted viewport costs
// nothing at all.

export interface Replay { id: string; label: string; code: string; engine: string; project: string }
export type Phase = "idle" | "engine" | "studio" | "run" | "frame" | "ready" | "error";
export interface LiveInfo {
  phase: Phase; stats: LiveStats | null; error: string; failedAt: "" | "engine" | "run";
  engineUrl: string; ranMs: number; result: string;
}

const PHASE_TEXT: Record<Phase, string> = {
  idle: "", engine: "loading the project's engine…", studio: "building the studio…",
  run: "running the code…", frame: "framing…", ready: "", error: "",
};
const PRESETS = ["3q", "front", "side", "top", "low"];
const BACKDROP = "#1a1e26";

const FRAMED_TONE: Record<string, string> = {
  fit: "text-ok border-ok/40 bg-ok/10",
  widened: "text-warn border-warn/40 bg-warn/10",
  "still-clipped": "text-danger border-danger/40 bg-danger/10",
  "no-subject": "text-muted border-line bg-panel2",
};

function fmtValue(v: unknown): string {
  if (v === undefined || v === null) return "";
  try { return (typeof v === "string" ? v : JSON.stringify(v)).slice(0, 300); } catch { return String(v).slice(0, 300); }
}

// The two failures a user can do something about, named. Everything else is shown verbatim.
function hintFor(err: string, failedAt: "" | "engine" | "run", engine: string, devUrl: string): string {
  if (failedAt === "engine") {
    // It used to name the r163 split build here whenever the engine was three. That cause was
    // real once and is fixed (the module endpoint rewrites relative imports back to itself), so
    // the message became a confident wrong answer: a harness that keeps three.module.js beside
    // its index.html failed for a completely different reason and was told about three.core.js.
    // Say what was looked for instead of guessing why it was not there.
    return "The window builds with the project's OWN engine and could not find one. It looks for "
      + "the package in node_modules, and for a build kept beside the page "
      + "(three.module.js, playcanvas.mjs, and the like)"
      + (devUrl ? ". The project's dev server is running, so it will try there too." : ".");
  }
  if (/Failed to resolve module specifier|Failed to fetch dynamically imported module/i.test(err)) {
    return devUrl
      ? "The code imports a file the project's dev server did not serve."
      : "The code imports from the game's own dev server, which is not running. This window never "
        + "starts one — start it, and run again.";
  }
  return "";
}

interface Props {
  replay: Replay | null;
  /** Ordered module URLs to try; the first that loads the right engine wins. */
  moduleUrls: string[];
  devUrl: string;
  justMade: boolean;
  /** Something to say while there is no replay yet — reading the record, or an error reading it. */
  notice?: { text: string; error?: boolean } | null;
  onLive: (info: LiveInfo) => void;
}

export function AssetViewport({ replay, moduleUrls, devUrl, justMade, notice, onLive }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const flatRef = useRef<HTMLCanvasElement>(null);
  const studioRef = useRef<Studio | null>(null);
  const orbitRef = useRef<Orbit | null>(null);
  const rafRef = useRef(0);
  const lastT = useRef(0);
  const dirty = useRef(false);
  const token = useRef(0);
  const engUrl = useRef("");
  const ranMs = useRef(0);
  const liveRef = useRef(onLive);
  liveRef.current = onLive;

  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [failedAt, setFailedAt] = useState<"" | "engine" | "run">("");
  const [framed, setFramed] = useState("");
  const [flat, setFlat] = useState<{ w: number; h: number } | null>(null);
  const [runN, setRunN] = useState(0);

  const moduleKey = moduleUrls.join("|");

  const emit = useCallback((p: Phase, err = "", at: "" | "engine" | "run" = "", result = "") => {
    const s = studioRef.current;
    let stats: LiveStats | null = null;
    try { stats = s ? s.stats() : null; } catch { stats = null; }
    liveRef.current({ phase: p, stats, error: err, failedAt: at, engineUrl: engUrl.current, ranMs: ranMs.current, result });
  }, []);

  const applyOrbit = useCallback(() => {
    const s = studioRef.current, o = orbitRef.current;
    if (!s || !o) return;
    s.setCamera(o.position(), o.target);
    // Clip planes follow the distance, floored at a fraction of the subject so a close dolly
    // never z-fights and a far one never clips.
    const r = s.radius, d = o.dist;
    s.setClip(Math.max(r * 1e-3, d - r * 4), d + r * 8);
  }, []);

  const reframe = useCallback((view = "3q") => {
    const s = studioRef.current, o = orbitRef.current;
    if (!s || !o || s.flat) return;
    const b = s.frame(view, 1.35);
    if (b) o.setFrom(s.cameraPosition(), b.c);
    dirty.current = true;
    setFramed(s.framed);
    emit("ready");
  }, [emit]);

  const paintFlat = useCallback(() => {
    const s = studioRef.current, c = flatRef.current, host = hostRef.current;
    if (!s || !s.flat || !c || !host) return;
    c.width = Math.max(1, host.clientWidth);
    c.height = Math.max(1, host.clientHeight);
    drawFlat(s.flat, c);
  }, []);

  const teardown = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = 0;
    orbitRef.current?.dispose();
    orbitRef.current = null;
    studioRef.current?.dispose();
    studioRef.current = null;
  }, []);

  useEffect(() => {
    teardown();
    setFlat(null); setFramed(""); setError(""); setFailedAt("");
    engUrl.current = ""; ranMs.current = 0;
    if (!replay) { setPhase("idle"); return; }
    const my = ++token.current;
    const alive = () => my === token.current;
    (async () => {
      let studio: Studio | null = null;
      try {
        setPhase("engine");
        const want: EngineKind | "" = replay.engine === "three" || replay.engine === "playcanvas" ? replay.engine : "";
        let eng;
        try { eng = await loadEngine(moduleUrls, want); }
        catch (e: any) {
          if (!alive()) return;
          setFailedAt("engine"); setPhase("error"); setError(String(e?.message || e));
          emit("error", String(e?.message || e), "engine");
          return;
        }
        if (!alive()) return;
        engUrl.current = eng.url;
        setPhase("studio");
        studio = buildStudio(eng, hostRef.current!, { background: BACKDROP, ground: false, sky: false });
        studioRef.current = studio;
        orbitRef.current = new Orbit(studio.canvas, () => { dirty.current = true; }, () => reframe("3q"));
        setPhase("run");
        const res = await runGeneration(studio, replay.code, devUrl);
        if (!alive()) return;
        ranMs.current = res.ms;
        if (studio.flat) {
          setFlat({ w: studio.flat.width || 0, h: studio.flat.height || 0 });
          paintFlat();
        } else {
          setPhase("frame");
          const b = studio.frame("3q", 1.35);
          if (b) orbitRef.current.setFrom(studio.cameraPosition(), b.c);
          else orbitRef.current.setFrom([3, 2, 4], [0, 0, 0]);
          dirty.current = true;
          setFramed(studio.framed);
          lastT.current = performance.now();
          const loop = (t: number) => {
            rafRef.current = requestAnimationFrame(loop);
            const s = studioRef.current;
            if (!s) return;
            const dt = Math.min(0.1, Math.max(0, (t - lastT.current) / 1000)) || 1 / 60;
            lastT.current = t;
            if (dirty.current) { applyOrbit(); dirty.current = false; }
            s.render(dt);
          };
          rafRef.current = requestAnimationFrame(loop);
        }
        // A code error still shows whatever was added before it threw: the partial result is
        // often the whole diagnosis.
        if (res.error) { setFailedAt("run"); setPhase("error"); setError(res.error); emit("error", res.error, "run"); }
        else { setPhase("ready"); emit("ready", "", "", fmtValue(res.value)); }
      } catch (e: any) {
        if (!alive()) return;
        const msg = String(e?.message || e);
        setFailedAt(studio ? "run" : "engine"); setPhase("error"); setError(msg);
        emit("error", msg, studio ? "run" : "engine");
      }
    })();
    return () => { token.current++; teardown(); };
    // The replay is identified by id and code; the URLs and dev server decide how it is loaded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replay?.id, replay?.code, moduleKey, devUrl, runN]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const ro = new ResizeObserver(() => {
      const s = studioRef.current;
      if (!s) return;
      if (s.flat) { paintFlat(); return; }
      s.resize(Math.max(1, host.clientWidth), Math.max(1, host.clientHeight));
      dirty.current = true;
    });
    ro.observe(host);
    return () => ro.disconnect();
  }, [paintFlat]);

  const loading = phase === "engine" || phase === "studio" || phase === "run" || phase === "frame";
  const hasScene = !!studioRef.current && (phase === "ready" || (phase === "error" && failedAt === "run"));
  const hint = phase === "error" ? hintFor(error, failedAt, replay?.engine || "", devUrl) : "";

  return (
    <div className="absolute inset-0 select-none">
      <div ref={hostRef} className="absolute inset-0 overflow-hidden" style={{ background: BACKDROP }} />
      <canvas ref={flatRef} className={cls("absolute inset-0 w-full h-full", !flat && "hidden")} />

      {replay && (
        <div className="absolute top-2 left-2 flex items-center gap-2 pointer-events-none max-w-[60%]">
          <span className="px-2 py-1 rounded-lg bg-panel/80 backdrop-blur text-[12px] text-text ring-1 ring-line truncate">
            {replay.label || replay.id}
          </span>
          <EngineChip kind={replay.engine} />
          {justMade && (
            <span className="chip text-accent border-accent/40 bg-accent/10"><Sparkles size={11} /> just made</span>
          )}
        </div>
      )}

      {hasScene && !flat && (
        <div className="absolute top-2 right-2 flex items-center gap-1">
          {PRESETS.map((v) => (
            <button key={v} onClick={() => reframe(v)} title={`Frame from the ${v} view`}
              className="px-2 py-1 rounded-md bg-panel/80 backdrop-blur ring-1 ring-line text-[11px] text-muted hover:text-text">
              {v}
            </button>
          ))}
          <button onClick={() => reframe("3q")} title="Frame the subject again (double-click does the same)"
            className="p-1.5 rounded-md bg-panel/80 backdrop-blur ring-1 ring-line text-muted hover:text-text">
            <Focus size={13} />
          </button>
          <button onClick={() => setRunN((n) => n + 1)} title="Run the code again in a fresh studio"
            className="p-1.5 rounded-md bg-panel/80 backdrop-blur ring-1 ring-line text-muted hover:text-text">
            <RotateCcw size={13} />
          </button>
        </div>
      )}

      {hasScene && (
        <div className="absolute bottom-2 left-2 right-2 flex items-end justify-between gap-2 pointer-events-none text-[10px]">
          <span className="text-muted/70">
            {flat ? `flat image ${flat.w}×${flat.h} on a checkerboard — a texture is judged unlit`
                  : "drag orbits · wheel zooms · right or shift-drag pans · double-click frames"}
          </span>
          {framed && !flat && (
            <span className={cls("inline-flex items-center rounded-full border px-2 py-0.5 font-medium", FRAMED_TONE[framed] || FRAMED_TONE["no-subject"])}>
              framed: {framed}
            </span>
          )}
        </div>
      )}

      {!replay && phase === "idle" && (
        <Centre>
          {notice ? (
            <span className={cls("flex items-center gap-2", notice.error ? "text-danger" : "text-muted")}>
              {notice.error ? <AlertTriangle size={14} /> : <Loader2 size={14} className="animate-spin" />}
              {notice.text}
            </span>
          ) : (
            <>
              <div className="text-text/80">Pick a generation in the strip below.</div>
              <div className="text-muted text-xs max-w-sm text-center">
                Anything an agent forged is re-run here in the project's own engine, lit and framed as the
                forge did it, and can be turned around.
              </div>
            </>
          )}
        </Centre>
      )}

      {loading && (
        <Centre>
          <span className="flex items-center gap-2 text-muted"><Loader2 size={14} className="animate-spin" /> {PHASE_TEXT[phase]}</span>
        </Centre>
      )}

      {phase === "error" && (
        <div className={cls("absolute left-1/2 -translate-x-1/2 max-w-[min(40rem,92%)] card p-3 text-xs shadow-card",
          hasScene ? "bottom-8" : "top-1/2 -translate-y-1/2")}>
          <div className="flex items-center gap-2 text-danger font-medium">
            <AlertTriangle size={14} />
            {failedAt === "engine" ? "The engine could not be loaded" : "The code threw"}
          </div>
          <pre className="mt-1.5 font-mono text-[11px] text-text/85 whitespace-pre-wrap break-words max-h-40 overflow-auto">{error}</pre>
          {hint && <div className="mt-1.5 text-muted">{hint}</div>}
        </div>
      )}
    </div>
  );
}

function Centre({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm pointer-events-none">
      {children}
    </div>
  );
}
