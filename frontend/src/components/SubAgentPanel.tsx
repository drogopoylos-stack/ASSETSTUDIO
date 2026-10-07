import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, CornerDownRight, Loader2, RefreshCw, Send, X } from "lucide-react";
import { api } from "../api/client";
import type { SubAgentDetail } from "../types";
import { FeedLines } from "./SessionFeed";
import { SubAgentCard } from "./SubAgentCard";
import { cls, pollWhileVisible } from "./ui";

// One subagent, watched in a pane of its own.
//
// The parent conversation only ever shows you the paragraph a subagent came back with. Its own
// transcript holds the whole thing — every search, every command, every edit and every thought —
// and it is on disk the entire time the agent is working. So this is not a replay: point a pane
// at a running agent and you watch it work beside the conversation that sent it.

// The statuses the parent records for a finished agent. Anything else means it is
// still working, however long it has been quiet.
const TERMINAL = new Set(["completed", "stopped", "failed", "error", "cancelled",
                          "canceled", "killed", "timed_out", "timeout"]);

export function SubAgentPanel({ projectId, agentId, cli, onClose, onContinue }: {
  projectId: string;
  agentId: string;
  cli?: boolean;
  onClose?: () => void;
  /** Ask the PARENT to send this agent more work. Only the parent can — see the note below. */
  onContinue?: (agentId: string, text: string) => void;
}) {
  const [data, setData] = useState<SubAgentDetail | null>(null);
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const load = useCallback(async () => {
    if (!projectId || !agentId) return;
    try {
      setData(await api.subagent(projectId, agentId));
      setErr("");
    } catch (e: any) {
      setErr(/404/.test(e?.message || "")
        ? "Restart the backend to use this — the endpoint is newer than the running process."
        : e?.message || "could not read that agent");
    }
  }, [projectId, agentId]);

  // Poll until the agent's OUTCOME is recorded, not until it goes quiet. `running` only means
  // "wrote its transcript in the last 90 seconds", and an agent thinking through one long turn
  // writes nothing for ten minutes. Stopping on quiet froze the panel on the agent's first step
  // and never restarted it, so a working agent looked dead until the window was reloaded by hand.
  const done = TERMINAL.has(String(data?.agent?.status || "").toLowerCase());
  useEffect(() => {
    return pollWhileVisible(() => (done ? undefined : load()), 2500);
  }, [load, done]);

  // Follow the tail while it runs, unless you have scrolled up to read something.
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [data?.lines?.length]);

  const a = data?.agent;
  const lines = data?.lines || [];

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-bg text-text" data-skin={cli ? "cli" : undefined}>
      <div className="pane-head h-8 shrink-0 bg-panel border-b border-line flex items-center px-2 gap-2 text-xs">
        <span className="flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
          {a?.running ? <Loader2 size={12} className="animate-spin text-brand shrink-0" />
                      : <Bot size={12} className="text-accent shrink-0" />}
          <span className="text-muted shrink-0">subagent</span>
          <span className="truncate font-medium">
            {a?.description || a?.agent_type || agentId.slice(0, 10)}
          </span>
        </span>
        <button onClick={load} className="shrink-0 p-0.5 rounded text-muted/60 hover:text-brand"
          title="Read it again now"><RefreshCw size={12} /></button>
        {onClose && (
          <button onClick={onClose} className="shrink-0 p-0.5 rounded text-muted/60 hover:text-danger"
            title="Close this view"><X size={13} /></button>
        )}
      </div>

      {err && <div className="shrink-0 px-3 py-2 text-xs text-warn bg-warn/10 border-b border-warn/30">{err}</div>}

      {a && (
        <div className="shrink-0 px-2 pt-2">
          {/* `??` and not a plain override: an engine that computes the file list itself puts it
              on the agent, and the detail response's empty `{}` would otherwise erase it. */}
          <SubAgentCard agent={{ ...a, touched: data?.touched ?? a.touched }} cli={cli} compact />
        </div>
      )}

      <div ref={box} className="flex-1 min-h-0 overflow-auto"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}>
        {!data && !err && (
          <div className="p-4 text-xs text-muted flex items-center gap-2">
            <Loader2 size={13} className="animate-spin" /> reading its transcript…
          </div>
        )}
        {data && <FeedLines lines={lines} cli={cli} />}
      </div>

      {onContinue && (
        <div className="shrink-0 border-t border-line p-2">
          {/* Only the parent can talk to a subagent — the id goes to SendMessage, and SendMessage
              is a tool the parent holds. So this does not send to the agent directly; it asks the
              conversation that owns it to pass the message on. Saying so is better than a box
              that looks like a chat and quietly is not one. */}
          <div className="flex items-start gap-1.5">
            <CornerDownRight size={13} className="text-muted/60 mt-2 shrink-0" />
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && draft.trim()) {
                  e.preventDefault();
                  setBusy(true);
                  onContinue(agentId, draft.trim());
                  setDraft("");
                  window.setTimeout(() => setBusy(false), 800);
                }
              }}
              rows={2} placeholder="More work for this agent — sent through the conversation that owns it"
              className="flex-1 min-w-0 resize-none rounded border border-line bg-panel2/50 px-2 py-1.5 text-xs
                         placeholder:text-muted/50 focus:outline-none focus:border-brand/50" />
            <button disabled={!draft.trim() || busy}
              onClick={() => { setBusy(true); onContinue(agentId, draft.trim()); setDraft(""); }}
              className={cls("mt-1 p-1.5 rounded", draft.trim()
                ? "text-brand hover:bg-brand/10" : "text-muted/30")}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
