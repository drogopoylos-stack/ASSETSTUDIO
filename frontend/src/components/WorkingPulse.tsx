import { useEffect, useRef, useState } from "react";
import { Bot, PackageOpen } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import { cls, pollWhileVisible } from "./ui";

const fmtTok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const fmtTime = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s` : `${s.toFixed(0)}s`);

// Live "what Claude is doing right now" pulse for a Claude Code session. With a
// `projectId` it polls /live fast and shows the REAL current activity (Thinking /
// Editing X / Running …), a ticking elapsed timer, and tokens that update as Claude
// generates (from --include-partial-messages) — not a delayed per-block guess.
export function WorkingPulse({ working, projectId, className }: {
  working?: boolean; projectId?: string; className?: string;
}) {
  const [live, setLive] = useState<boolean>(!!working);
  const [tokens, setTokens] = useState(0);
  const [activity, setActivity] = useState("");
  const [compacting, setCompacting] = useState(false);
  const [agents, setAgents] = useState(0);
  const [spawn, setSpawn] = useState(0);
  const [, setTick] = useState(0);           // forces re-render so the elapsed timer ticks
  const prevAgents = useRef(0);
  const baseElapsed = useRef(0);             // backend `elapsed` captured at the last poll
  const baseAt = useRef(0);                  // performance.now() at the last poll (for smoothing)
  // live state pushed over WS as Claude works — renders instantly, and while pushes are
  // fresh the HTTP fallback poll below skips its fetches entirely
  const ccPush = useStore((s) => (projectId ? s.ccLive[projectId] : undefined));
  const wsAt = useRef(0);

  function applyLive(s: any) {
    setLive(s.working);
    setTokens(s.tokens || 0);
    setActivity(s.activity || "");
    setCompacting(!!s.compacting);
    baseElapsed.current = s.elapsed || 0;
    baseAt.current = performance.now();
  }
  const applyRef = useRef(applyLive);
  applyRef.current = applyLive;

  useEffect(() => {
    if (!projectId || !ccPush) return;
    wsAt.current = ccPush.at;
    applyRef.current(ccPush.state);
  }, [ccPush, projectId]);

  // fallback live poll: tokens + activity + elapsed (skipped while WS pushes are fresh
  // or the window is hidden — no HTTP when nobody can see the pulse)
  useEffect(() => {
    if (projectId === undefined) { setLive(!!working); return; }
    let alive = true;
    let timer = 0;
    const loop = async () => {
      if (document.hidden || Date.now() - wsAt.current < 2500) { timer = window.setTimeout(loop, 1500); return; }
      const s = await api.liveState(projectId).catch(() => null);
      if (!alive) return;
      if (s) applyRef.current(s);
      timer = window.setTimeout(loop, s && s.working ? 600 : 1500);  // snappy while working
    };
    loop();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [working, projectId]);

  // separate, slower poll just for the subagent count
  useEffect(() => {
    if (projectId === undefined) return;
    let alive = true;
    const stop = pollWhileVisible(() => api.missionContext(projectId).then((c) => {
      if (!alive) return;
      const n = c.agents_active || 0;
      if (n > prevAgents.current) setSpawn((x) => x + 1);
      prevAgents.current = n;
      setAgents(n);
    }).catch(() => {}), 4000);
    return () => { alive = false; stop(); };
  }, [projectId]);

  // smooth ticking clock while live, so the elapsed timer moves between polls
  useEffect(() => {
    if (!live) return;
    const iv = window.setInterval(() => { if (!document.hidden) setTick((n) => n + 1); }, 250);
    return () => window.clearInterval(iv);
  }, [live]);

  if (!live && agents < 1) return null;
  const hasLive = projectId !== undefined;
  const elapsed = live && baseAt.current ? baseElapsed.current + (performance.now() - baseAt.current) / 1000 : 0;
  // /compact is one long internal op (can take minutes) — show a clear, distinct "not frozen"
  // state with a ticking timer instead of the usual Thinking/Editing pulse.
  if (live && compacting)
    return (
      <span className={cls("inline-flex items-center gap-1.5 text-[11px] whitespace-nowrap text-warn", className)}
        title="Compacting the conversation — summarizing the history to free up the context window. This can take a few minutes; it isn't frozen.">
        <PackageOpen size={12} className="shrink-0 animate-pulse" />
        <span className="font-medium">Compacting the conversation</span><span className="animate-pulse">…</span>
        {elapsed > 0 && <span className="text-warn/70 font-mono">· {fmtTime(elapsed)}</span>}
      </span>
    );
  return (
    <span className={cls("inline-flex items-center gap-2 text-[11px] whitespace-nowrap", className)}
      title="Live — what Claude is doing right now">
      {live && (
        <span className="inline-flex items-center gap-1.5 text-brand">
          {/* the CLI skin prints its own ✻ in front of this line, so the dot is hidden there
              (index.css) — two spinners for one turn read as two things happening */}
          <span data-pulse-dot className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full rounded-full bg-brand opacity-60 animate-ping" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-brand" />
          </span>
          <span className="font-medium">{activity || "Working"}</span><span className="animate-pulse">…</span>
          {hasLive && elapsed > 0 && <span className="text-muted/70 font-mono">· {fmtTime(elapsed)}</span>}
          {hasLive && tokens > 0 && <span className="text-muted font-mono">· {fmtTok(tokens)} tok</span>}
        </span>
      )}
      {agents >= 1 && (
        <span className="relative inline-flex items-center gap-1 text-brand">
          {spawn > 0 && (
            <span key={spawn} className="agent-spawn-fly absolute -left-3 -bottom-3 pointer-events-none text-ok"><Bot size={12} /></span>
          )}
          <Bot size={11} />
          <span key={`c${spawn}`} className="agent-count-pop inline-block font-medium">{agents}</span>
          <span>{agents === 1 ? "agent" : "agents"}</span>
        </span>
      )}
    </span>
  );
}
