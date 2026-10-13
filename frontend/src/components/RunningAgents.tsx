import { Bot, Loader2, X } from "lucide-react";
import type { RunningAgent } from "../types";
import { SubAgentCard } from "./SubAgentCard";
import { cls } from "./ui";

// Every subagent in flight, across every workspace and every engine, in one list.
//
// The count on the activity bar answers "is anything delegated right now"; this answers the two
// that follow — what, and where.
//
// IT USED TO ASK THE WRONG THING. The panel was handed the projects the live poll had already
// counted, filtered down to the registered workspaces, and then asked each one for its agents. So
// it could not show an agent the poll had missed, and the poll walks only the sessions this
// backend process is streaming: after a restart that is none of them. The panel said "No agents
// running" beside a fan-out the user could watch working, and restarting the Studio — the obvious
// thing to try — made it worse rather than better.
//
// So the rows now come from the backend's own sweep of the disk (/api/mission/agents/running),
// polled once in the page and passed in here. One source for the badge and for this list, which
// is also the only way the two can agree.

export function RunningAgents({ rows, err, onOpen, onClose, nameOf }: {
  /** every working agent, from the sweep — newest first */
  rows: RunningAgent[];
  err?: string;
  /** open one in a pane — the workspace switch is the caller's, since only it knows the roots */
  onOpen: (folderId: string, agentId: string, engine: string) => void;
  onClose: () => void;
  /** a readable name for a folder id */
  nameOf: (folderId: string) => string;
}) {
  // Grouped by the conversation that started them: one fan-out reads as one block, with the
  // engine named, instead of a flat list in which a Codex agent and a Claude agent look alike.
  const groups: { project: string; folder: string; engine: string; engineName: string; agents: RunningAgent[] }[] = [];
  for (const a of rows) {
    const g = groups.find((x) => x.project === a.project);
    if (g) g.agents.push(a);
    else groups.push({ project: a.project, folder: a.folder, engine: a.engine,
                       engineName: a.engine_name, agents: [a] });
  }

  const total = rows.length;
  // Split, because "3 agents" hides the question the user actually has: is anything stuck?
  const moving = rows.filter((x) => x.running).length;

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
            Nothing is delegated right now. This looks in every folder and asks every engine —
            Claude, Codex and DeepSeek — so an agent shows up here whichever one started it, and
            whether or not the Studio was running when it did. A model the agent launched from the
            shell counts as well, which is how /studio draws a comic panel with GPT.
          </div>
        )}

        {groups.map((g) => (
          <div key={g.project} className="border-b border-line/60 last:border-0">
            <div className="px-3 pt-2 pb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted/70">
              <span>{nameOf(g.folder)}</span>
              {/* WHOSE AGENTS. One folder can run all three engines at once, and the cards alone
                  do not say which conversation handed the work out. */}
              <span className="chip !py-0 !px-1 normal-case text-[9px]">{g.engineName}</span>
            </div>
            <div className={cls("px-2 pb-2 space-y-1")}>
              {g.agents.map((a) => (
                <SubAgentCard key={`${g.project}:${a.agent_id}`} agent={a} compact
                  onOpen={(agentId) => { onOpen(g.folder, agentId, g.engine); onClose(); }} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
