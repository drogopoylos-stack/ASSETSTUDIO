import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ExternalLink, Eye, Gamepad2, Loader2, Monitor, Radio, RefreshCw, RotateCw } from "lucide-react";
import { api, liveStreamStatus, liveStreamWsUrl, type LiveStreamStatus } from "../../api/client";
import { cls, pollWhileVisible } from "../ui";
import type { EngineProject } from "../../types";
import { VIEW_PRESETS, type ViewSize } from "./prefs";
import { normPath } from "./GameRail";

// The game itself, in an iframe of its dev server. This never starts one: opening a viewer must
// not cost a build, so a project without a server running says so and stays put.
//
// THE IFRAME IS SIZED AT THE TARGET RESOLUTION AND THEN SCALED DOWN TO FIT.
//
// It used to be `flex-1 w-full`, so the game's viewport was whatever the pane happened to be —
// about 900px in this window. That is not the game. A layout built for 1920x1080 reflows at 900:
// the HUD stacks, text that fits stops fitting, and an agent judging that frame is judging a
// screen no player will ever see, while a real fault at the shipping size never appears. Setting
// the iframe's own width and height is what the game reads as `innerWidth`; the CSS transform
// only changes what WE see. So the game renders 1920 wide and is displayed at 38%.
//
// The chosen size is written back to `cc_engine_view`, which `live._device_of` reads — so an
// agent that names no size opens the game at the resolution this window is set to. One number,
// both sides.
//
// TWO VIEWS OF ONE GAME. The iframe is YOUR copy: you play it, and the agent's live tries (a move,
// a place, an eval) never reach it. It follows the game's files, as its dev server serves them:
// when the code changes it reloads, the way a Vite game does and a new game's serve.mjs does. The
// agent works in a different place — the headless tab the live link keeps open — so "Agent's
// view · live" streams THAT tab (/api/live/stream/ws: its frames over a WebSocket, drawn in an
// <img>). It is read-only: no click or key goes from here to the agent's tab, and the stream never
// opens a tab, so a person watching can never change the game. Play stays the default; the choice
// is remembered per game.

export type ProjectDetail = Awaited<ReturnType<typeof api.engineProject>>;

interface Props {
  project: EngineProject | null;
  detail: ProjectDetail | null;
  loading: boolean;
  onRefresh: () => void;
}

// The sizes live with the engine settings, so Settings → Studio engine offers the same list.
type Size = ViewSize;
const PRESETS: Size[] = VIEW_PRESETS;

type View = "play" | "agent";
const viewKey = (path: string) => "engine.gameView." + normPath(path);

function readView(path: string): View | null {
  try {
    const v = path ? localStorage.getItem(viewKey(path)) : null;
    return v === "agent" || v === "play" ? v : null;
  } catch { return null; /* private mode */ }
}

export function GameFrame(props: Props) {
  const { project } = props;
  const path = project?.path || "";
  // The choice belongs to the game. Unchosen means Play: an agent opening a game must not take the
  // window away from somebody who is playing it — it gets a chip instead.
  //
  // READ BEFORE THE FIRST RENDER, and again in the same render when the game changes. An effect
  // ran after the first commit, so for somebody who had chosen Agent's view the Play iframe was
  // made — and started loading the whole game — before it was torn down again.
  const [viewFor, setViewFor] = useState(path);
  const [view, setView] = useState<View>(() => readView(path) || "play");
  const [chosen, setChosen] = useState(() => readView(path) !== null);
  const [st, setSt] = useState<LiveStreamStatus | null>(null);
  if (viewFor !== path) {
    const v = readView(path);
    setViewFor(path);
    setView(v || "play");
    setChosen(v !== null);
    setSt(null);
  }

  const choose = useCallback((v: View) => {
    setView(v);
    setChosen(true);
    try { if (path) localStorage.setItem(viewKey(path), v); } catch { /* private mode */ }
  }, [path]);

  // Is an agent's tab open for this game? Every three seconds while the window is visible, in both
  // views — Play needs it for the chip. Cheap on the backend: no page is touched.
  useEffect(() => {
    if (!path) return;
    let dead = false;
    let fails = 0;
    const stop = pollWhileVisible(() => liveStreamStatus(path)
      .then((s) => { if (!dead) { fails = 0; setSt(s); } })
      .catch(() => {
        // ONE FAILED POLL IS NOT "NO AGENT". A busy backend times a poll out now and then, and
        // reading that as gone tore down a healthy stream and said nobody had the game open. Three
        // in a row is gone — or an older backend with no such route.
        if (!dead && ++fails >= 3) setSt(null);
      }), 3000);
    return () => { dead = true; stop(); };
  }, [path]);

  if (!project) return <PlayView {...props} />;
  const live = !!st?.live;

  return (
    <div className="absolute inset-0 flex flex-col">
      <div className="h-8 shrink-0 flex items-center gap-1 px-2 border-b border-line bg-panel text-[11px]">
        <ViewButton on={view === "play"} onClick={() => choose("play")}
          title="Your own copy of the game, in this window. The agent's live tries never reach it; it reloads when the game's code changes, as its dev server does.">
          <Gamepad2 size={12} /> Play (your copy)
        </ViewButton>
        <ViewButton on={view === "agent"} onClick={() => choose("agent")}
          title="The agent's own tab, streamed live: every move it makes, as it makes it. View only.">
          <Radio size={12} /> Agent's view · live
          {live && (
            <span className={cls("h-1.5 w-1.5 rounded-full shrink-0",
              st?.agent_active ? "bg-ok animate-pulse" : "bg-ok/50")} />
          )}
        </ViewButton>
        {view === "play" && live && !chosen && (
          <button onClick={() => choose("agent")}
            className="ml-auto chip shrink-0 text-ok border-ok/40 bg-ok/10 hover:bg-ok/20 transition-colors"
            title="Switch to the agent's tab. Your choice is remembered for this game.">
            <Eye size={11} /> An agent is working in this game — watch
          </button>
        )}
      </div>
      <div className="relative flex-1 min-h-0">
        {view === "play" ? <PlayView {...props} /> : <AgentView project={project} st={st} />}
      </div>
    </div>
  );
}

function ViewButton({ on, onClick, title, children }: {
  on: boolean; onClick: () => void; title: string; children: React.ReactNode;
}) {
  return (
    <button onClick={onClick} title={title}
      className={cls("inline-flex items-center gap-1.5 px-2 py-0.5 rounded shrink-0",
        on ? "bg-brand/15 text-brand" : "text-muted hover:text-text hover:bg-panel2")}>
      {children}
    </button>
  );
}

function ago(s: number | null | undefined): string {
  if (s == null) return "";
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

function baseName(p: string): string {
  const parts = (p || "").replace(/\\/g, "/").replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || p;
}

function AgentView({ project, st }: { project: EngineProject; st: LiveStreamStatus | null }) {
  const [hidden, setHidden] = useState(() => typeof document !== "undefined" && document.hidden);
  const [attempt, setAttempt] = useState(1);
  const [paused, setPaused] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const openedAt = useRef(0);

  // A hidden window closes the stream, and a visible one opens it again: nobody watches a
  // minimised window, and a screencast nobody sees still costs the agent's browser.
  useEffect(() => {
    const on = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);

  const live = !!st?.live;
  // Paused keeps the element: its connection is already closed by the server and it goes on
  // showing the last frame, which is the truth about a game nobody has touched since.
  const src = live && !hidden ? liveStreamWsUrl(project.path, { n: attempt }) : "";

  useEffect(() => {
    if (!src) return;
    openedAt.current = Date.now();
    setLoaded(false);
  }, [src]);

  // THE STREAM ENDS ON THE SERVER'S SIDE for real reasons: the agent closed its tab, or ten minutes
  // passed with no agent. The <img> cannot tell — it just keeps the last frame. So the status
  // decides. Ours is still running while a screencast runs that is at least as old as our
  // connection (a younger one belongs to another window that reconnected first). Otherwise connect
  // again — unless it ended for idle: then wait for an agent to come back, or for a click.
  useEffect(() => {
    if (!st || !st.live || hidden) return;
    if (paused) {
      if (st.agent_active) { setPaused(false); setAttempt((a) => a + 1); }
      return;
    }
    const mine = (Date.now() - openedAt.current) / 1000;
    if (mine < 6) return;
    if (st.streaming && (st.stream?.since_s ?? 0) + 2 >= mine) return;
    if (!st.streaming && st.stopped?.why === "idle" && !st.agent_active) { setPaused(true); return; }
    setAttempt((a) => a + 1);
  }, [st, hidden, paused]);

  if (!live) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm">
        <Radio size={26} className="text-muted" />
        <div className="text-text/80">
          {st && st.enabled === false
            ? "The live game link is off, so no agent can open this game."
            : "No agent has this game open. The view starts when one opens it."}
        </div>
        <div className="text-muted text-xs max-w-md text-center leading-relaxed">
          This shows the agent's own tab — every move it makes, as it makes it. It never opens the
          game itself, and nothing you do here reaches it.
        </div>
      </div>
    );
  }

  const fps = Math.round(st?.fps_measured || 0);
  const still = (st?.last_frame_ms_ago ?? 0) > 3000;
  return (
    <div className="absolute inset-0 flex flex-col">
      <div className="h-7 shrink-0 flex items-center gap-2 px-2 border-b border-line bg-panel/60 text-[11px] min-w-0">
        <span className={cls("h-1.5 w-1.5 rounded-full shrink-0",
          paused ? "bg-muted" : st?.agent_active ? "bg-ok animate-pulse" : "bg-ok/60")} />
        {/* A still scene sends no new frames, so its rate is 0 — "still" says that without
            reading as a broken stream. */}
        <span className="text-text/80 shrink-0">
          {paused ? "paused" : "live"} · the agent's tab · {fps > 0 ? `${fps} fps` : "still"}
        </span>
        <span className="text-muted shrink-0">
          · {st?.agent_active ? "agent working" : `agent quiet ${ago(st?.agent_idle_s)}`}
          {!paused && still && st?.last_frame_ms_ago != null ? ` · nothing has moved for ${ago(st.last_frame_ms_ago / 1000)}` : ""}
          {st?.match && st.match !== "exact" && st.project ? ` · opened as ${baseName(st.project)}` : ""}
        </span>
        <span className="ml-auto font-mono text-muted truncate min-w-0" title={st?.url || ""}>{st?.url}</span>
        <span className="text-muted/70 shrink-0" title="Clicks and keys here never reach the agent's tab">view only</span>
      </div>
      <div className="flex-1 min-h-0 relative bg-black">
        {src && (
          <StreamImg key={src} url={src} onLoad={() => setLoaded(true)}
            title={`${project.name} — the agent's tab, live (view only)`} />
        )}
        {!paused && !loaded && !(st?.streaming && st.last_frame_ms_ago != null) && (
          <div className="absolute inset-0 grid place-items-center text-muted text-xs pointer-events-none">
            <span className="flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> connecting to the agent's tab…</span>
          </div>
        )}
        {paused && (
          <div className="absolute left-1/2 top-3 -translate-x-1/2 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-panel/90 border border-line text-xs shadow-card">
            <span className="text-text/80">Paused — no agent has touched this game for 10 minutes.</span>
            <button className="btn-ghost !px-1.5 !py-0.5 text-[11px]"
              onClick={() => { setPaused(false); setAttempt((a) => a + 1); }}>
              <Eye size={12} /> watch anyway
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// THE FRAMES COME OVER A WEBSOCKET, one JPEG per message, each shown as a blob URL. The MJPEG
// route in an <img src> held one of the browser's six connections to the Studio for as long as the
// view was open, and every poll in the app queued on the rest; a WebSocket is not in that pool.
function StreamImg({ url, onLoad, title }: { url: string; onLoad: () => void; title: string }) {
  const ref = useRef<HTMLImageElement | null>(null);
  const loadRef = useRef(onLoad);
  loadRef.current = onLoad;              // a status poll re-renders the parent; that must not reconnect
  // Keyed by url, so each connection has its own element and its own socket.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let shown = "";
    let first = true;
    const ws = new WebSocket(url);
    ws.binaryType = "blob";
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") return;         // a refusal; the status poll says why
      const next = URL.createObjectURL(ev.data as Blob);
      el.src = next;
      // The element keeps its decoded picture until the next one is ready, so the old URL can go.
      if (shown) URL.revokeObjectURL(shown);
      shown = next;
    };
    const loaded = () => { if (first) { first = false; loadRef.current(); } };
    el.addEventListener("load", loaded);
    return () => {
      ws.onmessage = null;
      try { ws.close(); } catch { /* already closed */ }
      el.removeEventListener("load", loaded);
      if (shown) URL.revokeObjectURL(shown);
    };
  }, [url]);
  return (
    <img ref={ref} alt="The agent's tab, live" title={title} draggable={false}
      className="absolute inset-0 w-full h-full object-contain select-none pointer-events-none" />
  );
}

function PlayView({ project, detail, loading, onRefresh }: Props) {
  const [n, setN] = useState(0);
  const [size, setSize] = useState<Size>({ w: 1280, h: 720, label: "720p" });
  const [fit, setFit] = useState(true);
  const [pane, setPane] = useState({ w: 0, h: 0 });
  const boxRef = useRef<HTMLDivElement | null>(null);

  // Start from the shared setting, so the window opens on whatever was last agreed with agents.
  useEffect(() => {
    api.engineState()
      .then((s) => { if (s?.view?.w && s?.view?.h) setSize({ w: s.view.w, h: s.view.h, label: s.view.label || "" }); })
      .catch(() => { /* the endpoint is newer than the backend; the default stands */ });
  }, []);

  const choose = useCallback((s: Size) => {
    setSize(s);
    // Written, not just held: an agent reads this to open the game at the same resolution.
    api.updateSettings({ cc_engine_view: { w: s.w, h: s.h, label: s.label } }).catch(() => {});
  }, []);

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setPane({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setPane({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, [project?.path, detail?.dev_url]);

  if (!project) {
    return (
      <Centre>
        <Gamepad2 size={28} className="text-muted" />
        <div className="text-text/80">Pick a game on the left to see it running.</div>
      </Centre>
    );
  }
  if (!project.game) {
    return (
      <Centre>
        <div className="text-text/80"><span className="text-brand">{project.name}</span> is not a game.</div>
        <div className="text-muted text-xs max-w-md text-center">
          No three.js, PlayCanvas, Babylon, Phaser or Pixi build in its node_modules, and no dev script to serve it.
        </div>
      </Centre>
    );
  }
  const url = detail?.dev_url || "";
  if (!url) {
    return (
      <Centre>
        {loading && !detail ? (
          <span className="flex items-center gap-2 text-muted"><Loader2 size={14} className="animate-spin" /> looking for its dev server…</span>
        ) : (
          <>
            <Gamepad2 size={28} className="text-muted" />
            <div className="text-text/80">No dev server is running for <span className="text-brand">{project.name}</span>.</div>
            <div className="text-muted text-xs max-w-md text-center leading-relaxed">
              This window never starts one. Run{" "}
              {project.dev_script
                ? <code className="font-mono text-text/80">npm run {project.dev_script}</code>
                : "the dev server"}
              {" "}in <code className="font-mono text-text/80 break-all">{project.root}</code> and it appears here.
            </div>
            <button className="btn mt-2 pointer-events-auto" onClick={onRefresh} disabled={loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Check again
            </button>
          </>
        )}
      </Centre>
    );
  }

  // Never scale UP: a 390px phone layout blown up to fill the pane is a lie about legibility.
  const k = fit && pane.w > 0
    ? Math.min(1, pane.w / size.w, pane.h / size.h)
    : 1;
  const pct = Math.round(k * 100);

  return (
    <div className="absolute inset-0 flex flex-col">
      <div className="h-8 shrink-0 flex items-center gap-2 px-2 border-b border-line bg-panel text-[11px]">
        <span className="h-1.5 w-1.5 rounded-full bg-ok shrink-0" />
        <span className="font-mono text-muted truncate">{url}</span>
        <button className="ml-auto btn-ghost !px-1.5 !py-0.5 text-[11px] shrink-0" onClick={() => setN((v) => v + 1)} title="Reload the game">
          <RefreshCw size={12} /> reload
        </button>
        <button className="btn-ghost !px-1.5 !py-0.5 text-[11px] shrink-0" onClick={() => window.open(url, "_blank", "noopener")} title="Open in a browser">
          <ExternalLink size={12} /> open
        </button>
      </div>

      {/* The resolution bar. The figure on the right is the honest one: what the game renders at,
          and how much of that you are actually seeing. */}
      <div className="h-8 shrink-0 flex items-center gap-1 px-2 border-b border-line bg-panel/60 text-[11px] overflow-x-auto">
        <Monitor size={12} className="text-muted shrink-0" />
        {PRESETS.map((p) => (
          <button key={p.label} onClick={() => choose(p)}
            title={`${p.w} x ${p.h}`}
            className={cls("px-1.5 py-0.5 rounded shrink-0 font-mono",
              size.w === p.w && size.h === p.h ? "bg-brand/15 text-brand" : "text-muted hover:text-text hover:bg-panel2")}>
            {p.label}
          </button>
        ))}
        <button onClick={() => choose({ w: size.h, h: size.w, label: size.label })}
          title="Swap orientation" className="px-1 py-0.5 rounded text-muted hover:text-text hover:bg-panel2 shrink-0">
          <RotateCw size={12} />
        </button>
        <span className="mx-1 h-3 w-px bg-line shrink-0" />
        <input type="number" value={size.w} min={200} max={3840} aria-label="width"
          onChange={(e) => choose({ ...size, w: Math.max(200, Math.min(3840, +e.target.value || 0)), label: "custom" })}
          className="input !py-0 !px-1 w-16 text-[11px] font-mono shrink-0" />
        <span className="text-muted/60 shrink-0">×</span>
        <input type="number" value={size.h} min={200} max={2160} aria-label="height"
          onChange={(e) => choose({ ...size, h: Math.max(200, Math.min(2160, +e.target.value || 0)), label: "custom" })}
          className="input !py-0 !px-1 w-16 text-[11px] font-mono shrink-0" />
        <button onClick={() => setFit((v) => !v)}
          title={fit ? "Showing it scaled to fit — click for 1:1" : "Showing it 1:1 — click to fit"}
          className={cls("ml-auto px-1.5 py-0.5 rounded shrink-0 font-mono",
            fit ? "text-muted hover:text-text hover:bg-panel2" : "bg-brand/15 text-brand")}>
          {fit ? `fit ${pct}%` : "1:1"}
        </button>
        <span className="font-mono text-muted/70 shrink-0 tabular-nums">
          {size.w}×{size.h}
        </span>
      </div>

      <div ref={boxRef} className="flex-1 min-h-0 relative overflow-auto bg-black/40 grid place-items-center">
        {/* The wrapper carries the SCALED footprint so the scrollbars and centring are right; the
            iframe inside keeps its true pixel size, which is what the game reads. */}
        <div style={{ width: size.w * k, height: size.h * k }} className="shrink-0">
          <iframe key={`${n}-${size.w}x${size.h}`} src={url} title={project.name}
            width={size.w} height={size.h}
            style={{ width: size.w, height: size.h, transform: `scale(${k})`, transformOrigin: "top left" }}
            className="bg-black border-0 block"
            allow="fullscreen; pointer-lock; gamepad; autoplay; xr-spatial-tracking" />
        </div>
      </div>
    </div>
  );
}

function Centre({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm">
      {children}
    </div>
  );
}
