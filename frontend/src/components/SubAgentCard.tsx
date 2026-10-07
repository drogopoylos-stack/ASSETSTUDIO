import { useState } from "react";
import {
  Bot, ChevronDown, ChevronRight, Clock, Coins, FileEdit, Gauge, Inbox, Loader2,
  Maximize2, Search, Terminal, Wrench,
} from "lucide-react";
import type { SubAgent } from "../types";
import { cls } from "./ui";

// A subagent, drawn as something you can actually audit.
//
// Everywhere else a delegated task is one line out and one paragraph back: you cannot see what
// it cost, what it read, what it changed, or whether it is still going. All of that is written
// down by the CLI — a foreground agent reports its own toolStats, and a background one reports
// nothing at all, so those figures are computed from its transcript instead. Either way nothing
// on this card is an estimate.

export const fmtTok = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
  : n >= 1000 ? `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1)}k`
  : String(n || 0);

/** "$8.53", "$0.05", "<$0.01" — never "$0.00", which reads as free rather than as small. */
export const fmtUsd = (n: number): string =>
  !n ? "" : n < 0.01 ? "<$0.01" : n < 10 ? `$${n.toFixed(2)}` : `$${Math.round(n)}`;

export const fmtMs = (ms: number): string => {
  const s = Math.round((ms || 0) / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

const baseName = (p: string) => (p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

/** One number with its label, monospaced so a column of them lines up. */
function Stat({ icon, value, label, tone, title }: {
  icon?: React.ReactNode; value: string; label: string; tone?: string; title?: string;
}) {
  return (
    <span className="flex items-center gap-1 whitespace-nowrap" title={title || label}>
      {icon}
      <span className={cls("font-mono tabular-nums", tone || "text-text/85")}>{value}</span>
      <span className="text-muted/70">{label}</span>
    </span>
  );
}

export function SubAgentCard({ agent, cli, onOpen, compact }: {
  agent: SubAgent;
  cli?: boolean;
  /** show it in a pane of its own */
  onOpen?: (agentId: string) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const a = agent || ({} as SubAgent);
  const st = a.stats || { read: 0, search: 0, bash: 0, edits: 0, added: 0, removed: 0, other: 0 };
  const u = a.usage || { input: 0, output: 0, cache_read: 0, cache_write: 0 };
  const label = a.description || a.agent_type || "subagent";
  const edited = a.touched?.edited || [];

  return (
    <div className={cls("min-w-0", cli ? "font-mono text-[13px]" : "")}>
      <div className={cls("rounded border overflow-hidden",
        a.running ? "border-brand/50 bg-brand/[0.06]" : "border-line bg-panel/40")}>
        {/* ---- the line you read at a glance ---- */}
        <div className="flex items-center gap-2 px-2.5 py-1.5 min-w-0">
          <span className="shrink-0">
            {a.running
              ? <Loader2 size={13} className="animate-spin text-brand" />
              : <Bot size={13} className="text-accent" />}
          </span>
          <button onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1 min-w-0 flex-1 text-left hover:text-brand">
            {open ? <ChevronDown size={12} className="shrink-0 text-muted" />
                  : <ChevronRight size={12} className="shrink-0 text-muted" />}
            <span className="truncate font-medium">{label}</span>
          </button>

          {/* Cost and effort, always visible. This is the number nobody could see before: a
              research agent can spend more than the conversation that sent it. */}
          <span className="hidden sm:flex items-center gap-3 text-[11px] shrink-0">
            {!!a.ms && <Stat icon={<Clock size={11} className="text-muted/60" />}
              value={fmtMs(a.ms)} label="" title="wall clock" />}
            {!!a.tokens && <Stat icon={<Coins size={11} className="text-muted/60" />}
              value={fmtTok(a.tokens)} label="tok"
              title={`${a.tokens.toLocaleString()} new tokens: ${(u.input + u.cache_write).toLocaleString()} `
                + `input and ${u.output.toLocaleString()} output, each API call counted once.`
                + (u.cache_read ? ` Another ${u.cache_read.toLocaleString()} were the same context read `
                  + "again from the cache, at a small part of the price — open the card to see them." : "")} />}
            {!!a.cost && <Stat value={fmtUsd(a.cost)} label="" tone="text-warn/90"
              title={"API list price of this agent's own tokens, at the CLI's own rate card. "
                + "Your Max plan is not charged this — it is here so two agents, two models or "
                + "two effort levels can be compared."} />}
            {!!a.tools && <Stat icon={<Wrench size={11} className="text-muted/60" />}
              value={String(a.tools)} label="" title="tool calls" />}
            {(st.added > 0 || st.removed > 0) && (
              <span className="font-mono tabular-nums whitespace-nowrap" title="lines written">
                <span className="text-ok">+{st.added}</span>
                <span className="text-muted/40">/</span>
                <span className="text-danger">−{st.removed}</span>
              </span>
            )}
          </span>

          {a.background && !a.running && (
            <span className="shrink-0 text-[10px] px-1 rounded bg-panel2 text-muted/70">bg</span>
          )}
          {onOpen && a.agent_id && (
            <button onClick={() => onOpen(a.agent_id)} title="Open this agent in its own pane"
              className="shrink-0 p-0.5 rounded text-muted/60 hover:text-brand">
              <Maximize2 size={12} />
            </button>
          )}
        </div>

        {/* WHAT IT IS DOING RIGHT NOW, without opening anything.
            The pill has shown this since it was added, but the card — which is what you actually
            look at, in the feed and in the running-agents list — was a spinner and a name. That
            is the whole difference between knowing an agent exists and knowing what it is up to.
            `phase` keeps a finished tool from reading as a running one: when a tool comes back
            the agent is writing, and those are different things to wait on. */}
        {(a.running || a.open) && a.activity && (a.activity.tool || a.activity.phase) && (
          <div className="px-2.5 pb-1.5 flex items-baseline gap-1.5 text-[11px] font-mono min-w-0">
            {a.activity.phase === "generating" ? (
              <>
                <span className="text-brand shrink-0">writing</span>
                <span className="truncate text-muted/60">
                  {a.activity.tool ? `after ${a.activity.tool}` : "the answer"}
                </span>
              </>
            ) : (
              <>
                <span className="text-brand shrink-0">{a.activity.tool || "working"}</span>
                <span className="truncate text-muted/80">{a.activity.detail}</span>
              </>
            )}
            {!a.running && a.idle_s ? (
              <span className="ml-auto shrink-0 text-warn/80"
                title="Dispatched and not yet reported back. Silence inside one long turn is normal — this is how long it has been.">
                quiet {fmtMs((a.idle_s || 0) * 1000)}
              </span>
            ) : null}
          </div>
        )}

        {/* ---- everything else ---- */}
        {open && (
          <div className="border-t border-line/70 px-2.5 py-2 space-y-2 text-[11px]">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted">
              {a.model && <Stat value={a.model} label="" title="the model actually served" />}
              {!!a.agent_type && <Stat value={a.agent_type} label="agent" />}
              {!!a.turns && <Stat value={String(a.turns)} label="calls" title="API calls, each counted once" />}
              {!!a.status && <Stat value={a.status} label=""
                tone={a.status === "completed" ? "text-ok" : "text-warn"} />}
            </div>

            {/* What it STARTED with, and how full it got.
                Neither is in the token total, and the total cannot be made to yield them: every
                turn re-sends the same growing prefix, so summing usage counts that prefix once
                per turn. `inherited` is the first request; `peak` is the largest. Together they
                say whether an agent was handed the conversation or just a paragraph, and whether
                it ran out of room. */}
            {!!a.peak && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <Stat icon={<Inbox size={11} className="text-muted/60" />}
                  value={fmtTok(a.inherited || 0)} label="inherited"
                  title={`Its first request carried ${(a.inherited || 0).toLocaleString()} tokens — `
                    + "the system prompt, the tool schemas and whatever the parent handed it. "
                    + "A plain Task agent starts around 13-17k; a /subtask is given the whole "
                    + "conversation, so it starts far higher."} />
                <Stat icon={<Gauge size={11} className="text-muted/60" />}
                  value={fmtTok(a.peak)} label="peak context"
                  tone={a.ctx_max && a.peak / a.ctx_max > 0.8 ? "text-danger"
                    : a.ctx_max && a.peak / a.ctx_max > 0.5 ? "text-warn" : undefined}
                  title={`Its biggest single request was ${a.peak.toLocaleString()} tokens`
                    + (a.ctx_max ? ` of a ${fmtTok(a.ctx_max)} window` : "")
                    + " — how close it came to filling its own context."} />
                {!!a.ctx_max && (
                  <span className="flex items-center gap-1.5 min-w-0" title={`${fmtTok(a.ctx_max)} window`}>
                    <span className="w-14 h-1.5 rounded bg-panel2 overflow-hidden shrink-0">
                      <span className={cls("block h-full",
                        a.peak / a.ctx_max > 0.8 ? "bg-danger"
                          : a.peak / a.ctx_max > 0.5 ? "bg-warn" : "bg-brand-600")}
                        style={{ width: `${Math.min(100, (100 * a.peak) / a.ctx_max)}%` }} />
                    </span>
                    <span className="font-mono tabular-nums text-muted/70">
                      {Math.round((100 * a.peak) / a.ctx_max)}%
                    </span>
                  </span>
                )}
              </div>
            )}

            {/* The bill, in two lines. The first is what `tokens` above adds up: what the agent
                wrote and the input it had not sent before. The second is the same context sent
                again on every call, served by the cache at a small part of the price - far the
                largest number, and the reason it is not the headline. */}
            {!!a.tokens && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <Stat value={fmtTok(u.output)} label="written" tone="text-brand"
                  title={`${u.output.toLocaleString()} output tokens`} />
                <Stat value={fmtTok(u.input + u.cache_write)} label="new input"
                  title={`${u.cache_write.toLocaleString()} written to the cache for the next calls, `
                    + `${u.input.toLocaleString()} not cached`} />
              </div>
            )}
            {!!u.cache_read && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <Stat value={fmtTok(u.cache_read)} label="read again from the cache" tone="text-ok"
                  title={`${u.cache_read.toLocaleString()} tokens: every call sends the whole context again, `
                    + "and the cache serves it at a small part of the input price. Not in the tokens above."} />
                {!!a.wire && <Stat value={fmtTok(a.wire)} label="all together"
                  title={`${a.wire.toLocaleString()} — new tokens and cache reads added up: everything that crossed the wire`} />}
              </div>
            )}

            {/* What it actually did with its time. */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <Stat icon={<Search size={11} className="text-muted/60" />} value={String(st.read)} label="read" />
              <Stat icon={<Search size={11} className="text-muted/60" />} value={String(st.search)} label="searched" />
              <Stat icon={<Terminal size={11} className="text-muted/60" />} value={String(st.bash)} label="commands" />
              <Stat icon={<FileEdit size={11} className="text-muted/60" />} value={String(st.edits)} label="edits"
                tone={st.edits ? "text-warn" : undefined} />
              {!!st.other && <Stat value={String(st.other)} label="other" />}
            </div>

            {/* The files it changed. The summary counts edits and names no file, so these come
                from the agent's own tool calls — the only place the paths exist. */}
            {edited.length > 0 && (
              <div>
                <div className="text-[10px] uppercase tracking-wide text-muted mb-1">Files it changed</div>
                <div className="space-y-0.5 font-mono">
                  {edited.slice(0, 10).map((f) => (
                    <div key={f.path} className="flex items-center gap-2 min-w-0" title={f.path}>
                      <FileEdit size={10} className="text-warn shrink-0" />
                      <span className="truncate text-text/80">{baseName(f.path)}</span>
                      {f.times > 1 && <span className="text-muted/60 shrink-0">×{f.times}</span>}
                    </div>
                  ))}
                  {edited.length > 10 && (
                    <div className="text-muted/60">…and {edited.length - 10} more</div>
                  )}
                </div>
              </div>
            )}

            {!!a.prompt && !compact && (
              <div>
                <div className="text-[10px] uppercase tracking-wide text-muted mb-1">What it was asked</div>
                <div className="max-h-40 overflow-auto rounded bg-bg/60 border border-line/60 p-2 whitespace-pre-wrap text-muted/90">
                  {a.prompt}
                </div>
              </div>
            )}
            {!!a.result && (
              <div>
                <div className="text-[10px] uppercase tracking-wide text-muted mb-1">What it reported</div>
                <div className="max-h-56 overflow-auto rounded bg-bg/60 border border-line/60 p-2 whitespace-pre-wrap text-text/85">
                  {a.result}
                </div>
              </div>
            )}
            {a.running && (
              <div className="text-brand/80">
                Still working. Its own timeline is in the pane view, live.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
