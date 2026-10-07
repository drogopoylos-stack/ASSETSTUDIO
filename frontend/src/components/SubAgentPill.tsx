import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Bot, FileEdit, Loader2, X } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { SubAgent, SubAgentClash } from "../types";
import { fmtMs, fmtTok } from "./SubAgentCard";
import { cls, pollWhileVisible } from "./ui";

// The bottom-bar read-out for delegated work.
//
// A subagent can outspend the conversation that sent it, and until now none of that was visible
// anywhere. (The "15.5 million tokens" once quoted here was the old line sum - every API call
// counted two or three times, cache reads included; see agent_usage.py.) This
// says how many are working, what they have cost in total, and the one thing that actually goes
// wrong with parallel agents: two of them writing the same file.

const baseName = (p: string) => (p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

export function SubAgentPill({ compact }: { compact?: boolean }) {
  const projectId = useStore((s) => s.activeProject);
  const rootPath = useStore((s) => s.workspaceTarget) || "";
  const [open, setOpen] = useState(false);
  const [agents, setAgents] = useState<SubAgent[]>([]);
  const [running, setRunning] = useState(0);
  // Dispatched but quiet. Counted apart from `running` so a background agent that is mid-turn
  // stays on screen instead of vanishing and reading as dead.
  const [openCount, setOpenCount] = useState(0);
  const [total, setTotal] = useState(0);
  const [cacheRead, setCacheRead] = useState(0);
  const [clashes, setClashes] = useState<SubAgentClash[]>([]);
  const [err, setErr] = useState("");

  const live = running + openCount;

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      const r = await api.subagents(projectId, true);
      setAgents(r.agents || []);
      setRunning(r.running || 0);
      setOpenCount(r.open || 0);
      setTotal(r.tokens || 0);
      setCacheRead(r.cache_read || 0);
      setErr("");
      if ((r.running || 0) > 1) {
        const c = await api.subagentClashes(projectId, rootPath).catch(() => ({ collisions: [] }));
        setClashes(c.collisions || []);
      } else setClashes([]);
    } catch (e: any) {
      setErr(/404/.test(e?.message || "")
        ? "Restart the backend to use this — the endpoint is newer than the running process."
        : e?.message || "could not read the agents");
    }
  }, [projectId, rootPath]);

  // A cheap poll all the time (the count is what the bar is for), and a faster one while open.
  useEffect(() => {
    return pollWhileVisible(load, open ? 2500 : 8000);
  }, [load, open]);

  if (!projectId || (!agents.length && !open)) return null;
  const danger = clashes.length > 0;

  return (
    <span className="relative">
      <button onClick={() => setOpen((v) => !v)}
        title={danger ? "Two agents are writing the same file — click to see which"
                      : "Delegated work: how many are running and what they have cost"}
        className={cls("flex items-center gap-1.5 rounded-full border transition-colors",
          compact ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-0.5",
          danger ? "bg-danger/15 text-danger border-danger/50"
          : live ? "bg-brand/10 text-brand border-brand/40"
          : open ? "bg-panel2 text-text border-line" : "bg-panel2/60 text-muted border-line hover:text-text")}>
        {danger ? <AlertTriangle size={13} />
          : live ? <Loader2 size={13} className="animate-spin" />
          : <Bot size={13} />}
        {compact
          ? (live || agents.length)
          : live ? `${live} agent${live > 1 ? "s" : ""}` : `${agents.length} agents`}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-[80]" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 mb-2 z-[81] card p-0 w-[30rem] max-h-[70vh] overflow-auto shadow-card text-xs font-sans">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-line">
              <Bot size={14} className="text-accent" />
              <span className="font-medium text-text">Subagents</span>
              <span className="text-muted font-mono"
                title={"New tokens: the input each agent had not sent before, and what it wrote — "
                  + "every API call counted once."
                  + (cacheRead ? ` The ${cacheRead.toLocaleString()} cache reads are the same context `
                    + "sent again on every call, at a small part of the price." : "")}>
                {agents.length} · {fmtTok(total)} new tokens
                {!!cacheRead && <span className="text-muted/60"> · {fmtTok(cacheRead)} from cache</span>}
              </span>
              <button className="ml-auto p-0.5 rounded text-muted hover:text-text"
                onClick={() => setOpen(false)}><X size={14} /></button>
            </div>

            {err && <div className="px-3 py-2 text-warn bg-warn/10 border-b border-warn/30">{err}</div>}

            {/* The warning worth having. Both sides come from the agents' own tool calls, so
                this means they really did both write that file — not merely that they were
                running at the same time. */}
            {clashes.map((c) => (
              <div key={c.path} className="px-3 py-2 border-b border-danger/30 bg-danger/10">
                <div className="flex items-center gap-1.5 text-danger font-medium">
                  <AlertTriangle size={13} />
                  {c.agents.length} running agents have both edited this file
                </div>
                <div className="font-mono text-[11px] text-text/85 mt-1 truncate" title={c.path}>
                  {baseName(c.path)}
                </div>
                {!!c.symbols.length && (
                  <div className="text-[11px] text-muted mt-0.5">
                    touches {c.symbols.slice(0, 6).join(", ")}
                    {c.symbols.length > 6 && ` and ${c.symbols.length - 6} more`}
                  </div>
                )}
              </div>
            ))}

            <div className="divide-y divide-line/60">
              {agents.map((a) => {
                const st = a.stats || { edits: 0, added: 0, removed: 0 } as SubAgent["stats"];
                return (
                  <div key={a.agent_id} className="px-3 py-2">
                    <div className="flex items-center gap-2 min-w-0">
                      {a.running
                        ? <Loader2 size={12} className="animate-spin text-brand shrink-0" />
                        : <Bot size={12} className="text-muted/50 shrink-0" />}
                      <span className="truncate flex-1 min-w-0 text-text/90">
                        {a.description || a.agent_type || a.agent_id.slice(0, 10)}
                      </span>
                      <span className="shrink-0 font-mono text-[11px] text-muted tabular-nums">
                        {!!a.ms && fmtMs(a.ms)}{!!a.tokens && ` · ${fmtTok(a.tokens)}`}
                      </span>
                    </div>
                    {/* "1 agent" says something is delegated and nothing else. This is the
                        question actually being asked: what is it doing right now. */}
                    {(a.running || a.open) && a.activity && (
                      a.activity.phase === "generating" ? (
                        // The tool is DONE. A turn writes nothing until the whole message is
                        // finished, so a long think plus a big file is many quiet minutes with
                        // nothing wrong -- and naming the finished command as if it were still
                        // running is what made that look like a hang.
                        <div className="mt-1 flex items-baseline gap-1.5 text-[11px] font-mono min-w-0">
                          <span className="text-brand shrink-0">writing</span>
                          <span className="truncate text-muted/60">
                            {a.activity.tool ? `after ${a.activity.tool}` : "the answer"}
                          </span>
                        </div>
                      ) : a.activity.tool ? (
                        <div className="mt-1 flex items-baseline gap-1.5 text-[11px] font-mono min-w-0">
                          <span className="text-brand shrink-0">{a.activity.tool}</span>
                          <span className="truncate text-muted/80">{a.activity.detail}</span>
                        </div>
                      ) : null
                    )}
                    <div className="flex items-center gap-3 mt-0.5 text-[11px] text-muted/70 font-mono">
                      {!!a.model && <span className="truncate max-w-[11rem]">{a.model}</span>}
                      {/* Quiet is not stopped. Say how quiet, and let the user judge. */}
                      {a.open && !a.running && (
                        <span className={a.activity?.phase === "generating" ? "text-muted/70" : "text-warn/80"}
                          title={a.activity?.phase === "generating"
                            ? "Generating. Nothing is written until the message completes, so a long answer is silent while it is being made."
                            : "It has not written for a while and the last tool it asked for has not come back."}>
                          {a.activity?.phase === "generating" ? "thinking" : "quiet"} {fmtMs((a.idle_s || 0) * 1000)}
                        </span>
                      )}
                      {!!a.turns && <span title="API calls, each counted once">{a.turns} calls</span>}
                      {!!a.tools && <span>{a.tools} tools</span>}
                      {!!st?.edits && (
                        <span className="flex items-center gap-1 text-warn/80">
                          <FileEdit size={10} />{st.edits}
                          <span className="text-ok">+{st.added}</span>
                          <span className="text-danger">−{st.removed}</span>
                        </span>
                      )}
                    </div>
                    {!!a.touched?.edited?.length && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {a.touched.edited.slice(0, 6).map((f) => (
                          <span key={f.path} title={f.path}
                            className="px-1 rounded bg-panel2 text-[10px] font-mono text-muted/80">
                            {baseName(f.path)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
              {!agents.length && !err && (
                <div className="px-3 py-4 text-muted">No subagent has run in this project yet.</div>
              )}
            </div>
          </div>
        </>
      )}
    </span>
  );
}
