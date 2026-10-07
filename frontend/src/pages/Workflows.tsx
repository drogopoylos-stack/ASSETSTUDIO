import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock,
  Cpu,
  FileText,
  Hash,
  Layers,
  Loader2,
  Network,
  ScrollText,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  Telescope,
  Workflow as WorkflowIcon,
  Wrench,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { api } from "../api/client";
import { cls } from "../components/ui";
import type { WorkflowAgent, WorkflowAgentEvent, WorkflowDetail, WorkflowPhase, WorkflowRun } from "../types";

// ───────── formatting helpers ─────────
const fmtTok = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}k` : String(n || 0);
const fmtDur = (ms: number) => {
  const s = (ms || 0) / 1000;
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = s / 60;
  if (m < 60) return `${Math.floor(m)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(m / 60)}h ${Math.floor(m % 60)}m`;
};
const timeAgo = (ms: number) => {
  if (!ms) return "";
  const s = (Date.now() - ms) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
const modelShort = (m: string) =>
  (m || "")
    .replace(/\[1m\]/, "").replace(/-\d{8}$/, "")
    .replace(/^(anthropic|openai|google|meta-llama|deepseek|qwen)\//, "")
    .replace(/^claude-/, "").replace(/-/g, " ").trim();

// ───────── stalled-agent detection ─────────
// A workflow phase waits for EVERY agent in it, so ONE wedged agent stalls the entire run while its
// siblings keep reporting normally — which reads as "the workflow is just slow" instead of "one agent
// is stuck", and the chat never gets a result even though almost all the work finished. Claude Code's
// Workflow harness owns the agent loop and exposes no per-agent timeout, so the Studio cannot PREVENT
// this; what it can do is make the wedge obvious instead of invisible.
// 5 min matches the backend's agent-activity window, and it separates cleanly in practice: healthy
// agents were observed writing 0–17s apart while a wedged one had been silent for 8m+.
const STALL_MS = 300_000;

/** How long a *running* agent has been silent, or 0 when it's reporting normally / not running. */
function stalledFor(a: WorkflowAgent, now: number): number {
  if (a.state !== "running" || !a.lastProgressAt) return 0;
  const idle = now - a.lastProgressAt;
  return idle >= STALL_MS ? idle : 0;
}

// ───────── state visuals ─────────
// Tailwind purges interpolated class names (`bg-${x}`), so every colour is a COMPLETE static
// string here — that's the only way the JIT compiler emits them (no safelist in this project).
type Tone = "ok" | "warn" | "danger" | "muted" | "brand" | "accent";
const TONE: Record<Tone, { text: string; bar: string; soft: string; ring: string; border: string }> = {
  ok:     { text: "text-ok",     bar: "bg-ok",     soft: "bg-ok/15",     ring: "ring-ok/30",     border: "border-ok/40" },
  warn:   { text: "text-warn",   bar: "bg-warn",   soft: "bg-warn/15",   ring: "ring-warn/30",   border: "border-warn/50" },
  danger: { text: "text-danger", bar: "bg-danger", soft: "bg-danger/15", ring: "ring-danger/30", border: "border-danger/40" },
  muted:  { text: "text-muted",  bar: "bg-muted",  soft: "bg-muted/15",  ring: "ring-muted/30",  border: "border-line" },
  brand:  { text: "text-brand",  bar: "bg-brand",  soft: "bg-brand/15",  ring: "ring-brand/30",  border: "border-brand/50" },
  accent: { text: "text-accent", bar: "bg-accent", soft: "bg-accent/15", ring: "ring-accent/30", border: "border-accent/40" },
};

const AG = {
  running: { c: "warn", label: "running" },
  done: { c: "ok", label: "done" },
  error: { c: "danger", label: "failed" },
  queued: { c: "muted", label: "queued" },
} as const;
type AgState = keyof typeof AG;

function statusMeta(status: string, live: boolean) {
  if (live || status === "running") return { c: "ok", label: "Running", live: true };
  if (status === "completed") return { c: "brand", label: "Completed", live: false };
  if (status === "killed" || status === "canceled" || status === "cancelled") return { c: "muted", label: "Stopped", live: false };
  if (status === "error" || status === "failed") return { c: "danger", label: "Failed", live: false };
  return { c: "muted", label: status || "done", live: false };
}

function phaseIcon(title: string) {
  const t = (title || "").toLowerCase();
  if (/scope|plan|decompose/.test(t)) return Telescope;
  if (/search|find|discover/.test(t)) return Search;
  if (/fetch|read|gather|collect|map|inventor/.test(t)) return Network;
  if (/verif|review|audit|check|vote|test|qa|certif/.test(t)) return ShieldCheck;
  if (/synth|merge|report|final|summar|build|fix/.test(t)) return Sparkles;
  return Layers;
}

// a number that smoothly counts up to its target (for the big header stats)
function useCountUp(value: number, dur = 650) {
  const [disp, setDisp] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = performance.now();
    const a = from.current;
    const b = value;
    if (a === b) { setDisp(b); return; }
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      const v = a + (b - a) * e;
      setDisp(v);
      from.current = v;
      if (p < 1) raf = requestAnimationFrame(tick);
      else { from.current = b; setDisp(b); }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, dur]);
  return disp;
}

// re-render every 250ms so live elapsed timers tick
function useTick(on: boolean) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!on) return;
    const iv = window.setInterval(() => { if (!document.hidden) set((n) => n + 1); }, 250);
    return () => window.clearInterval(iv);
  }, [on]);
}

// ───────── main ─────────
export default function Workflows() {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [liveCount, setLiveCount] = useState(0);
  const [total, setTotal] = useState(0);
  const [sel, setSel] = useState<{ pid: string; rid: string } | null>(null);
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [q, setQ] = useState("");
  const [onlyLive, setOnlyLive] = useState(false);
  const [agent, setAgent] = useState<WorkflowAgent | null>(null);
  const selRef = useRef(sel);
  selRef.current = sel;

  // poll the run list (fast when something is live)
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const loop = async () => {
      if (document.hidden) { timer = window.setTimeout(loop, 2000); return; }
      const r = await api.workflows(120).catch(() => null);
      if (!alive) return;
      if (r) {
        setRuns(r.workflows || []);
        setLiveCount(r.live || 0);
        setTotal(r.total || 0);
        if (!selRef.current && r.workflows?.length)
          setSel({ pid: r.workflows[0].project_id, rid: r.workflows[0].runId });
      }
      timer = window.setTimeout(loop, r && r.live ? 2500 : 6000);
    };
    loop();
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);

  // poll the selected run's detail (fast while it's live, lazy when finished)
  useEffect(() => {
    if (!sel) { setDetail(null); return; }
    let alive = true;
    let timer = 0;
    const loop = async () => {
      if (document.hidden) { timer = window.setTimeout(loop, 2000); return; }
      const d = await api.workflow(sel.pid, sel.rid).catch(() => null);
      if (!alive) return;
      if (d) setDetail(d);
      timer = window.setTimeout(loop, d && d.live ? 1200 : 12000);
    };
    loop();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [sel?.pid, sel?.rid]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return runs.filter((r) =>
      (!onlyLive || r.live) &&
      (!needle || `${r.name} ${r.task} ${r.project_name}`.toLowerCase().includes(needle)));
  }, [runs, q, onlyLive]);

  return (
    <div className="h-full flex bg-bg text-text overflow-hidden">
      {/* ── left: run list ── */}
      <aside className="w-[300px] shrink-0 border-r border-line bg-panel/40 flex flex-col min-h-0">
        <div className="p-3 border-b border-line">
          <div className="flex items-center gap-2">
            <WorkflowIcon size={17} className="text-brand" />
            <h1 className="font-semibold text-[15px]">Workflows</h1>
            {liveCount > 0 && (
              <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-ok font-medium">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-ok opacity-60 animate-ping" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-ok" />
                </span>{liveCount} live
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted mt-1">Multi-agent runs — agents working through phases.</p>
          <div className="mt-2 flex items-center gap-1.5">
            <div className="relative flex-1">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted/60" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${total} runs…`}
                className="input w-full !pl-6 !py-1.5 text-xs" />
            </div>
            <button onClick={() => setOnlyLive((v) => !v)} title="Show only live runs"
              className={cls("px-2 py-1.5 rounded-md text-[11px] border transition-colors",
                onlyLive ? "border-ok/60 bg-ok/10 text-ok" : "border-line text-muted hover:text-text")}>
              live
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0 p-1.5 space-y-1">
          {filtered.length === 0 ? (
            <div className="text-muted/60 text-xs text-center mt-8 px-3">
              {runs.length === 0 ? "No workflow runs yet. Run one (e.g. /code-review ultra or a research workflow) and it shows up here, live." : "No runs match."}
            </div>
          ) : filtered.map((r) => (
            <RunRow key={`${r.project_id}/${r.runId}`} run={r}
              active={sel?.rid === r.runId && sel?.pid === r.project_id}
              onClick={() => { setSel({ pid: r.project_id, rid: r.runId }); setAgent(null); }} />
          ))}
        </div>
      </aside>

      {/* ── right: detail ── */}
      <main className="flex-1 min-w-0 relative overflow-hidden">
        {!detail ? (
          <div className="h-full flex flex-col items-center justify-center text-muted/60 gap-3">
            <Network size={40} className="opacity-40" />
            <p className="text-sm">{runs.length ? "Select a workflow run." : "Workflow runs will appear here."}</p>
          </div>
        ) : (
          <WorkflowView key={`${detail.project_id}/${detail.runId}`} d={detail} onAgent={setAgent} />
        )}
        {agent && detail && (
          <AgentDrawer agent={agent} pid={detail.project_id} rid={detail.runId} onClose={() => setAgent(null)} />
        )}
      </main>
    </div>
  );
}

// ───────── left list row ─────────
function RunRow({ run, active, onClick }: { run: WorkflowRun; active: boolean; onClick: () => void }) {
  const sm = statusMeta(run.status, run.live);
  const pct = run.agentCount ? Math.round((run.done / run.agentCount) * 100) : 0;
  return (
    <button onClick={onClick}
      className={cls("w-full text-left rounded-lg px-2.5 py-2 border transition-all group",
        active ? "border-brand/50 bg-brand/10" : "border-transparent hover:border-line hover:bg-panel2/50")}>
      <div className="flex items-center gap-1.5">
        {sm.live ? (
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full rounded-full bg-ok opacity-60 animate-ping" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-ok" />
          </span>
        ) : <span className={cls("h-2 w-2 rounded-full shrink-0", TONE[sm.c as Tone].bar)} />}
        <span className="font-medium text-[13px] truncate flex-1">{run.name}</span>
        <span className="text-[10px] text-muted/70 shrink-0">{timeAgo(run.updatedMs)}</span>
      </div>
      {run.task && <p className="text-[11px] text-muted mt-0.5 line-clamp-1 pl-3.5">{run.task}</p>}
      <div className="mt-1.5 pl-3.5 flex items-center gap-2 text-[10px] text-muted/80">
        <span className="inline-flex items-center gap-0.5"><Bot size={10} />{run.agentCount}</span>
        <span className="inline-flex items-center gap-0.5"><Hash size={10} />{fmtTok(run.totalTokens)}</span>
        <span className="truncate opacity-70">{run.project_name}</span>
      </div>
      {/* mini phase progress */}
      <div className="mt-1.5 pl-3.5 h-1 rounded-full bg-panel2 overflow-hidden">
        <div className={cls("h-full rounded-full transition-all", sm.live ? "bg-ok" : TONE[sm.c as Tone].bar)} style={{ width: `${pct}%` }} />
      </div>
    </button>
  );
}

// ───────── detail view ─────────
function WorkflowView({ d, onAgent }: { d: WorkflowDetail; onAgent: (a: WorkflowAgent) => void }) {
  useTick(d.live);
  const sm = statusMeta(d.status, d.live);
  const elapsed = d.live && d.startMs ? Date.now() - d.startMs : d.durationMs;
  const tok = useCountUp(d.totalTokens);
  const tools = useCountUp(d.totalToolCalls);
  const agentsDone = useCountUp(d.done);
  // useTick(d.live) above re-renders while the run is live, so this age stays current on its own.
  const stalledCount = d.agents.reduce((n, a) => n + (stalledFor(a, Date.now()) ? 1 : 0), 0);

  return (
    <div className="h-full overflow-y-auto">
      {/* header */}
      <div className="relative overflow-hidden border-b border-line">
        <div className="absolute inset-0 wf-aurora opacity-60" />
        <div className="relative p-4">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 h-10 w-10 rounded-xl bg-panel/70 backdrop-blur flex items-center justify-center ring-1 ring-line shrink-0">
              <WorkflowIcon size={20} className="text-brand" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-xl font-bold tracking-tight bg-gradient-to-r from-text to-text/70 bg-clip-text">{d.name}</h2>
                <StatusBadge status={d.status} live={d.live} />
                <span className="text-[11px] text-muted/80">{d.project_name}</span>
              </div>
              {(d.taskFull || d.task) && (
                <p className="text-[13px] text-text/80 mt-1 line-clamp-2 leading-snug">{d.taskFull || d.task}</p>
              )}
            </div>
          </div>
          {/* stat chips */}
          <div className="flex items-center gap-2 mt-3 flex-wrap">
            <Stat icon={Bot} label="agents" value={`${Math.round(agentsDone)}/${d.agentCount}`} tone="brand" />
            <Stat icon={Zap} label="new tokens" value={fmtTok(Math.round(tok))} tone="accent"
              title={"New tokens: the input each agent had not sent before, and what it wrote. Every API call counted once; the cache reads (the same context sent again on each call) are not in it."} />
            <Stat icon={Wrench} label="tool calls" value={String(Math.round(tools))} tone="ok" />
            <Stat icon={Layers} label="phases" value={String(d.phaseCount)} tone="muted" />
            <Stat icon={Clock} label={d.live ? "elapsed" : "took"} value={fmtDur(elapsed)} tone={d.live ? "ok" : "muted"} />
            {d.defaultModel && <Stat icon={Cpu} label="model" value={modelShort(d.defaultModel)} tone="muted" />}
            {d.running > 0 && (
              <span className="inline-flex items-center gap-1 text-[11px] text-warn font-medium ml-1">
                <Loader2 size={12} className="animate-spin" />{d.running} working now
              </span>
            )}
            {stalledCount > 0 && (
              <span className="inline-flex items-center gap-1 text-[11px] text-danger font-medium ml-1"
                title={`${stalledCount} agent${stalledCount > 1 ? "s have" : " has"} reported no progress for over `
                  + `${Math.round(STALL_MS / 60000)} min. A phase waits for every agent in it, so a wedged agent `
                  + `holds up the whole run — that's why it can look "slow" when it is actually stuck.`}>
                <AlertTriangle size={12} />{stalledCount} stalled
              </span>
            )}
          </div>
        </div>
      </div>

      {/* phase pipeline */}
      {d.phases.length > 0 && <PhasePipeline phases={d.phases} />}

      {/* body: agents + logs */}
      <div className="flex gap-3 p-3 items-start">
        <div className="flex-1 min-w-0">
          <AgentBoard agents={d.agents} phases={d.phases} onAgent={onAgent} />
        </div>
        {d.logs.length > 0 && (
          <div className="w-72 shrink-0 hidden xl:block sticky top-2">
            <LogStream logs={d.logs} live={d.live} />
          </div>
        )}
      </div>

      {/* result (finished runs) */}
      {d.result && (
        <div className="px-3 pb-4">
          <Section title="Result" icon={Sparkles} defaultOpen={!d.live}>
            <pre className="text-[12px] text-text/85 whitespace-pre-wrap break-words font-mono bg-panel/50 rounded-lg p-3 border border-line max-h-[420px] overflow-auto">{d.result}</pre>
          </Section>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status, live }: { status: string; live: boolean }) {
  const sm = statusMeta(status, live);
  return (
    <span className={cls("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ring-1",
      TONE[sm.c as Tone].text, TONE[sm.c as Tone].soft, TONE[sm.c as Tone].ring)}>
      {sm.live
        ? <span className="relative flex h-1.5 w-1.5"><span className="absolute inline-flex h-full w-full rounded-full bg-ok opacity-70 animate-ping" /><span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-ok" /></span>
        : status === "completed" ? <CheckCircle2 size={12} /> : status === "killed" ? <Square size={11} /> : <XCircle size={12} />}
      {sm.label}
    </span>
  );
}

function Stat({ icon: Icon, label, value, tone, title }: { icon: any; label: string; value: string; tone: Tone; title?: string }) {
  return (
    <span title={title} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-panel/60 backdrop-blur ring-1 ring-line">
      <Icon size={13} className={TONE[tone].text} />
      <span className="font-mono font-semibold text-[13px] text-text">{value}</span>
      <span className="text-[10px] text-muted/80 uppercase tracking-wide">{label}</span>
    </span>
  );
}

// ───────── phase pipeline ─────────
function PhasePipeline({ phases }: { phases: WorkflowPhase[] }) {
  return (
    <div className="px-3 py-3 border-b border-line bg-panel/20 overflow-x-auto">
      <div className="flex items-stretch gap-0 min-w-min">
        {phases.map((p, i) => (
          <div key={p.index} className="flex items-stretch">
            <PhaseNode p={p} />
            {i < phases.length - 1 && (
              <div className="flex items-center px-1 self-center" style={{ minWidth: 34 }}>
                <div className="h-[3px] w-full rounded-full bg-line overflow-hidden">
                  {(p.state === "done" || p.state === "running") && <div className="h-full w-full wf-flow" />}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function PhaseNode({ p }: { p: WorkflowPhase }) {
  const Icon = phaseIcon(p.title);
  const tone: Tone = p.state === "done" ? "ok" : p.state === "running" ? "warn" : "muted";
  const pct = p.agentCount ? Math.round((p.done / p.agentCount) * 100) : p.state === "done" ? 100 : 0;
  return (
    <div className={cls("rounded-xl px-3 py-2 border bg-panel/50 min-w-[136px] transition-all",
      p.state === "running" ? "border-warn/50 wf-glow" : p.state === "done" ? "border-ok/40" : "border-line")}>
      <div className="flex items-center gap-1.5">
        <span className={cls("h-6 w-6 rounded-lg flex items-center justify-center shrink-0", TONE[tone].soft)}>
          {p.state === "running" ? <Loader2 size={13} className="text-warn animate-spin" />
            : p.state === "done" ? <CheckCircle2 size={14} className="text-ok" />
            : <Icon size={13} className="text-muted" />}
        </span>
        <div className="min-w-0">
          <div className="text-[12px] font-semibold truncate">{p.title}</div>
          <div className="text-[10px] text-muted">{p.done}/{p.agentCount || 0} agents{p.error ? ` · ${p.error}✗` : ""}</div>
        </div>
      </div>
      <div className="mt-1.5 h-1 rounded-full bg-panel2 overflow-hidden">
        <div className={cls("h-full rounded-full transition-all duration-500", TONE[tone].bar)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ───────── agent board (grouped by phase) ─────────
function AgentBoard({ agents, phases, onAgent }: { agents: WorkflowAgent[]; phases: WorkflowPhase[]; onAgent: (a: WorkflowAgent) => void }) {
  // group agents by phaseIndex, ordered by phase
  const groups = useMemo(() => {
    const byPhase = new Map<number, WorkflowAgent[]>();
    for (const a of agents) {
      const k = a.phaseIndex || 0;
      if (!byPhase.has(k)) byPhase.set(k, []);
      byPhase.get(k)!.push(a);
    }
    const order = phases.length ? phases.map((p) => p.index) : [...byPhase.keys()].sort((a, b) => a - b);
    for (const k of byPhase.keys()) if (!order.includes(k)) order.push(k);
    return order.filter((k) => byPhase.has(k)).map((k) => ({
      index: k,
      title: phases.find((p) => p.index === k)?.title || (k ? `Phase ${k}` : "Agents"),
      agents: byPhase.get(k)!,
    }));
  }, [agents, phases]);

  if (agents.length === 0)
    return <div className="text-muted/60 text-sm text-center py-12"><Loader2 size={18} className="inline animate-spin mr-2" />waiting for the first agent to spawn…</div>;

  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <AgentGroup key={g.index} title={g.title} agents={g.agents} onAgent={onAgent} />
      ))}
    </div>
  );
}

function AgentGroup({ title, agents, onAgent }: { title: string; agents: WorkflowAgent[]; onAgent: (a: WorkflowAgent) => void }) {
  const running = agents.some((a) => a.state === "running");
  const [open, setOpen] = useState(true);
  const done = agents.filter((a) => a.state === "done").length;
  const Icon = phaseIcon(title);
  return (
    <div>
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 w-full text-left mb-1.5 group">
        {open ? <ChevronDown size={13} className="text-muted" /> : <ChevronRight size={13} className="text-muted" />}
        <Icon size={13} className={running ? "text-warn" : "text-brand"} />
        <span className="font-semibold text-[13px]">{title}</span>
        <span className="text-[11px] text-muted">{done}/{agents.length}</span>
        {running && <Loader2 size={11} className="text-warn animate-spin" />}
        <span className="flex-1 h-px bg-line ml-1 group-hover:bg-line/80" />
      </button>
      {open && (
        <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]">
          {agents.map((a, i) => <AgentCard key={a.agentId} a={a} idx={i} onClick={() => onAgent(a)} />)}
        </div>
      )}
    </div>
  );
}

function AgentCard({ a, idx, onClick }: { a: WorkflowAgent; idx: number; onClick: () => void }) {
  const st = AG[a.state as AgState] || AG.queued;
  const running = a.state === "running";
  const stalled = stalledFor(a, Date.now());
  const doing = stalled
    ? `no progress for ${fmtDur(stalled)} — looks wedged`
    : running
      ? (a.lastToolSummary || (a.lastTool ? `${a.lastTool}…` : "working…"))
      : (a.resultPreview || a.lastToolSummary || "");
  return (
    <button onClick={onClick}
      style={{ animationDelay: `${Math.min(idx, 36) * 22}ms` }}
      className={cls("wf-spawn text-left rounded-xl p-2.5 border bg-panel/50 hover:bg-panel2/60 transition-colors relative overflow-hidden group",
        stalled ? "border-danger/60" : running ? "border-warn/50 wf-glow" : a.state === "error" ? "border-danger/40" : "border-line")}>
      <span className={cls("absolute left-0 top-0 bottom-0 w-[3px]", stalled ? TONE.danger.bar : TONE[st.c as Tone].bar)} />
      <div className="flex items-center gap-1.5 pl-1">
        <AgentDot state={a.state} />
        <span className="font-semibold text-[12.5px] truncate flex-1">{a.label || `agent ${a.index}`}</span>
        {stalled > 0 && (
          <span className="text-[9px] text-danger bg-danger/15 px-1 rounded inline-flex items-center gap-0.5 shrink-0"
            title={`No progress for ${fmtDur(stalled)}. This agent looks wedged — its phase cannot finish until it `
              + `returns, so the whole run stays "running". Open it to see where it stopped.`}>
            <AlertTriangle size={9} /> stalled {fmtDur(stalled)}
          </span>
        )}
        {a.attempt > 1 && <span className="text-[9px] text-warn bg-warn/15 px-1 rounded">retry {a.attempt}</span>}
      </div>
      {doing && (
        <p className={cls("text-[11px] mt-1 pl-1 line-clamp-2 leading-snug",
          stalled ? "text-danger/90" : running ? "text-warn/90" : "text-muted")}>
          {running && !stalled && a.lastTool && <span className="font-mono text-[10px] text-warn mr-1">{a.lastTool}</span>}
          {doing}
        </p>
      )}
      <div className="flex items-center gap-2 mt-1.5 pl-1 text-[10px] text-muted/80">
        <span className="inline-flex items-center gap-0.5" title={"New tokens: the input each agent had not sent before, and what it wrote. Every API call counted once; the cache reads (the same context sent again on each call) are not in it."}><Zap size={9} />{fmtTok(a.tokens)}</span>
        <span className="inline-flex items-center gap-0.5"><Wrench size={9} />{a.toolCalls}</span>
        {a.durationMs > 0 && <span className="inline-flex items-center gap-0.5"><Clock size={9} />{fmtDur(a.durationMs)}</span>}
        {a.model && <span className="ml-auto truncate opacity-60 max-w-[80px]">{modelShort(a.model)}</span>}
      </div>
    </button>
  );
}

function AgentDot({ state }: { state: string }) {
  if (state === "running")
    return (
      <span className="relative flex h-3.5 w-3.5 items-center justify-center shrink-0">
        <span className="absolute inset-0 rounded-full" style={{ background: "conic-gradient(rgb(var(--c-warn)), transparent 75%)" }} />
        <span className="wf-spin absolute inset-0 rounded-full" style={{ background: "conic-gradient(transparent, rgb(var(--c-warn)) 80%, transparent)" }} />
        <span className="relative h-1.5 w-1.5 rounded-full bg-warn" />
      </span>
    );
  if (state === "done") return <CheckCircle2 size={14} className="text-ok shrink-0" />;
  if (state === "error") return <XCircle size={14} className="text-danger shrink-0" />;
  return <Circle size={13} className="text-muted shrink-0" />;
}

// ───────── narrator / logs ─────────
function LogStream({ logs, live }: { logs: string[]; live: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [logs.length]);
  return (
    <div className="rounded-xl border border-line bg-panel/40 overflow-hidden">
      <div className="px-2.5 py-1.5 border-b border-line flex items-center gap-1.5 text-[11px] font-semibold text-muted uppercase tracking-wide">
        <ScrollText size={12} /> Narrator
        {live && <Activity size={11} className="text-ok animate-pulse ml-auto" />}
      </div>
      <div ref={ref} className="max-h-[440px] overflow-y-auto p-2 space-y-1 text-[11px] font-mono">
        {logs.map((l, i) => (
          <div key={i} className={cls("wf-rise leading-snug break-words", i === logs.length - 1 ? "text-text" : "text-muted/85")}>
            <span className="text-muted/40 select-none mr-1">›</span>{l}
          </div>
        ))}
      </div>
    </div>
  );
}

// ───────── collapsible section ─────────
function Section({ title, icon: Icon, defaultOpen, children }: { title: string; icon: any; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div className="rounded-xl border border-line bg-panel/30 overflow-hidden">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-1.5 px-3 py-2 text-[13px] font-semibold hover:bg-panel2/40">
        {open ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
        <Icon size={14} className="text-accent" />{title}
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}

// ───────── agent drawer (click an agent) ─────────
function AgentDrawer({ agent, pid, rid, onClose }: { agent: WorkflowAgent; pid: string; rid: string; onClose: () => void }) {
  const [events, setEvents] = useState<WorkflowAgentEvent[] | null>(null);
  // the COMPLETE prompt this agent was given — the card and the journal only carry a preview
  const [fullPrompt, setFullPrompt] = useState("");
  const [showFull, setShowFull] = useState(false);
  const [tools, setTools] = useState<{ name: string; count: number }[]>([]);
  const [files, setFiles] = useState<{ path: string; count: number }[]>([]);
  const [tab, setTab] = useState<"timeline" | "tools" | "prompt">("timeline");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true); setEvents(null); setFullPrompt(""); setShowFull(false);
    setTools([]); setFiles([]); setTab("timeline");
    api.workflowAgent(pid, rid, agent.agentId).then((r) => {
      if (!alive) return;
      setEvents(r.events || []);
      setFullPrompt(r.prompt || "");
      setTools(r.toolCounts || []);
      setFiles(r.files || []);
      setLoading(false);
    }).catch(() => { if (alive) { setEvents([]); setLoading(false); } });
    return () => { alive = false; };
  }, [pid, rid, agent.agentId]);
  const st = AG[agent.state as AgState] || AG.queued;

  return (
    <>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[1px] z-20 animate-[fadeIn_0.15s_ease]" onClick={onClose} />
      <div className="absolute inset-y-0 right-0 w-full max-w-[440px] bg-panel border-l border-line z-30 flex flex-col shadow-2xl"
        style={{ animation: "slideInRight 0.22s cubic-bezier(0.22,1,0.36,1)" }}>
        <div className="p-3 border-b border-line flex items-start gap-2">
          <AgentDot state={agent.state} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <h3 className="font-bold text-[14px] truncate">{agent.label || `agent ${agent.index}`}</h3>
              <span className={cls("text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0", TONE[st.c as Tone].text, TONE[st.c as Tone].soft)}>{st.label}</span>
            </div>
            <div className="text-[11px] text-muted mt-0.5 flex flex-wrap gap-x-2">
              {agent.phaseTitle && <span>{agent.phaseTitle}</span>}
              {agent.model && <span>· {modelShort(agent.model)}</span>}
              <span title={"New tokens: the input each agent had not sent before, and what it wrote. Every API call counted once; the cache reads (the same context sent again on each call) are not in it."}>· {fmtTok(agent.tokens)} new tok</span>
              <span>· {agent.toolCalls} tools</span>
              {agent.durationMs > 0 && <span>· {fmtDur(agent.durationMs)}</span>}
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-text p-0.5"><X size={16} /></button>
        </div>
        <div className="flex items-center gap-1 px-3 pt-2 border-b border-line">
          {([["timeline", "Timeline"], ["tools", "Tools"], ["prompt", "Prompt"]] as const).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
              className={cls("px-2.5 py-1.5 text-[11px] font-medium border-b-2 -mb-px transition-colors",
                tab === id ? "border-brand text-text" : "border-transparent text-muted hover:text-text")}>
              {label}
              {id === "tools" && tools.length > 0 && (
                <span className="ml-1 text-[9px] text-muted/70">{agent.toolCalls}</span>
              )}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {tab === "timeline" && (
            <>
              {loading ? (
                <div className="text-muted/60 text-xs flex items-center gap-2 py-3"><Loader2 size={13} className="animate-spin" /> reading agent transcript…</div>
              ) : events && events.length ? (
                <div className="space-y-1.5">{events.map((e, i) => <AgentEventRow key={i} e={e} />)}</div>
              ) : (
                <div className="text-muted/50 text-xs py-2">No transcript captured for this agent.</div>
              )}
              {agent.resultPreview && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1 flex items-center gap-1"><Sparkles size={11} /> result</div>
                  <pre className="text-[11px] text-text/80 whitespace-pre-wrap break-words font-mono bg-panel2/50 rounded-lg p-2 border border-line max-h-52 overflow-auto">{agent.resultPreview}</pre>
                </div>
              )}
            </>
          )}

          {tab === "tools" && (() => {
            const top = tools[0]?.count || 1;
            return (
              <>
                <div className="grid grid-cols-2 gap-1.5">
                  {[["model", agent.model ? modelShort(agent.model) : "—"],
                    ["new tokens", fmtTok(agent.tokens)],
                    ["tool calls", String(agent.toolCalls)],
                    ["active for", agent.durationMs > 0 ? fmtDur(agent.durationMs) : "—"]].map(([k, v]) => (
                    <div key={k} className="rounded-lg border border-line bg-panel2/40 px-2 py-1.5">
                      <div className="text-[9px] uppercase tracking-wide text-muted/70">{k}</div>
                      <div className="text-[12px] font-mono truncate">{v}</div>
                    </div>
                  ))}
                </div>

                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1 flex items-center gap-1">
                    <Wrench size={11} /> tools used
                  </div>
                  {tools.length ? (
                    <div className="space-y-1">
                      {tools.map((t) => (
                        <div key={t.name} className="flex items-center gap-2">
                          <span className="text-[11px] font-mono w-24 shrink-0 truncate">{t.name}</span>
                          <span className="flex-1 h-1.5 rounded-full bg-panel2 overflow-hidden">
                            <span className="block h-full rounded-full bg-brand-600"
                              style={{ width: `${Math.max(4, (t.count / top) * 100)}%` }} />
                          </span>
                          <span className="text-[11px] font-mono text-muted w-8 text-right">{t.count}</span>
                        </div>
                      ))}
                    </div>
                  ) : <div className="text-muted/50 text-xs">No tool calls recorded.</div>}
                </div>

                {!!files.length && (
                  <div>
                    <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1 flex items-center gap-1">
                      <FileText size={11} /> files it changed
                    </div>
                    <div className="space-y-0.5">
                      {files.map((f) => (
                        <div key={f.path} className="flex items-center gap-2 text-[11px]">
                          <span className="font-mono text-muted w-8 shrink-0 text-right">{f.count}×</span>
                          <span className="font-mono truncate text-text/85" title={f.path}>
                            {f.path.split(/[\\/]/).slice(-2).join("/")}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            );
          })()}

          {tab === "prompt" && (
            fullPrompt || agent.promptPreview ? (() => {
              const text = fullPrompt || agent.promptPreview;
              const long = text.length > 1200;
              const shown = long && !showFull ? text.slice(0, 1200).trimEnd() + "…" : text;
              return (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1 flex items-center gap-1">
                    <Cpu size={11} /> what it was told to do
                    <span className="ml-auto normal-case tracking-normal text-muted/60">{text.length.toLocaleString()} chars</span>
                  </div>
                  <p className="text-[12px] text-text/85 whitespace-pre-wrap break-words bg-panel2/50 rounded-lg p-2 border border-line">{shown}</p>
                  <div className="flex items-center gap-2 mt-1">
                    {long && (
                      <button className="text-[11px] text-brand hover:underline" onClick={() => setShowFull((v) => !v)}>
                        {showFull ? "see less" : "see more"}
                      </button>
                    )}
                    <button className="text-[11px] text-muted hover:text-text"
                      onClick={() => { navigator.clipboard?.writeText(text); }}>copy</button>
                  </div>
                </div>
              );
            })() : <div className="text-muted/50 text-xs py-2">No prompt captured.</div>
          )}
        </div>
      </div>
    </>
  );
}

function AgentEventRow({ e }: { e: WorkflowAgentEvent }) {
  if (e.kind === "thinking")
    return <div className="text-[11px] text-muted/80 italic border-l-2 border-accent/40 pl-2 line-clamp-4">{e.text}</div>;
  if (e.kind === "tool")
    return (
      <div className="text-[11px] flex items-start gap-1.5">
        <Wrench size={11} className="text-brand mt-0.5 shrink-0" />
        <span><span className="font-semibold text-text/90">{e.verb || e.tool}</span>{e.text && <span className="text-muted font-mono ml-1 break-all">{e.text}</span>}</span>
      </div>
    );
  return <div className="text-[12px] text-text/85 whitespace-pre-wrap break-words border-l-2 border-brand/50 pl-2">{e.text}</div>;
}
