import { useCallback, useEffect, useState } from "react";
import { Bot, Loader2, X } from "lucide-react";
import { api } from "../api/client";
import type { SubAgent } from "../types";
import { SubAgentCard } from "./SubAgentCard";
import { cls, pollWhileVisible } from "./ui";

// Every subagent in flight, across every workspace, in one list.
//
// The count on the activity bar answers "is anything delegated right now"; this answers the two
// that follow — what, and where. Until now a fan-out was only visible from inside the workspace
// that launched it, so nine agents running in a project you were not looking at were invisible
// from the project you were.
//
// It asks only for the workspaces the poll already says are busy, and only while it is open.

export function RunningAgents({ busy, onOpen, onClose, nameOf }: {
  /** project ids the live poll reports subagents in, with the count it reported */
  busy: { id: string; count: number; feed?: string }[];
  /** open one in a pane — the workspace switch is the caller's, since only it knows the roots */
  onOpen: (projectId: string, agentId: string) => void;
  onClose: () => void;
  /** a readable name for a project id */
  nameOf: (projectId: string) => string;
}) {
  const [rows, setRows] = useState<{ pid: string; agents: SubAgent[] }[]>([]);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      // `running` alone is "wrote something in the last 90 seconds", which a background agent
      // stops doing the moment it enters a long tool call. Filtering on it made this panel say
      // "No agents running" while the rail beside it showed 2 — so an agent that has been
      // dispatched and owes a result belongs here too, quiet or not.
      //
      // `feed` is where the agents actually live: an alternate engine files them under its own
      // prefix, and the bare id would read another engine's transcripts and find nothing.
      const out = await Promise.all(busy.map(async (b) => ({
        pid: b.id,
        agents: ((await api.subagents(b.feed || b.id)).agents || []).filter((a) => a.running || a.open),
      })));
      setRows(out.filter((r) => r.agents.length));
      setErr("");
    } catch (e: any) {
      setErr(e?.message || "could not read the agent list");
    }
  }, [busy]);

  useEffect(() => {
    return pollWhileVisible(load, 3000);
  }, [load]);

  const total = rows.reduce((a, r) => a + r.agents.length, 0);
  // Split, because "3 agents" hides the question the user actually has: is anything stuck?
  const moving = rows.reduce((a, r) => a + r.agents.filter((x) => x.running).length, 0);

  return (
    <>
      <div className="fixed inset-0 z-[70]" onClick={onClose} />
      <div className="absolute left-full top-0 ml-1 z-[71] card p-0 w-[26rem] max-h-[70vh] overflow-auto shadow-card text-xs">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-line sticky top-0 bg-panel">
          {total ? <Loader2 size={14} className="text-brand animate-spin" /> : <Bot size={14} className="text-muted" />}
          <span className="font-medium text-text">
            {total ? `${total} agent${total > 1 ? "s" : ""} working` : "No agents running"}
          </span>
          {total > moving && (
            <span className="text-[11px] font-mono text-muted"
              title="Quiet means it has not written for 90 seconds — normal inside a long tool call">
              {moving} writing, {total - moving} quiet
            </span>
          )}
          <button className="ml-auto p-0.5 rounded text-muted hover:text-text" onClick={onClose}><X size={14} /></button>
        </div>

        {err && <div className="px-3 py-2 text-warn bg-warn/10 border-b border-warn/30">{err}</div>}

        {!total && !err && (
          <div className="px-3 py-4 text-muted">
            Nothing is delegated right now. When a turn uses the Task tool, every agent it starts
            shows up here — with what it inherited, what it has spent, and what it has touched.
          </div>
        )}

        {rows.map((r) => (
          <div key={r.pid} className="border-b border-line/60 last:border-0">
            <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-muted/70">
              {nameOf(r.pid)}
            </div>
            <div className={cls("px-2 pb-2 space-y-1")}>
              {r.agents.map((a) => (
                <SubAgentCard key={a.agent_id} agent={a} compact
                  onOpen={(agentId) => { onOpen(r.pid, agentId); onClose(); }} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
