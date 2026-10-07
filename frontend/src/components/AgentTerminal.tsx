// Launch another coding agent in a REAL terminal.
//
// Claude Code streams into our chat because we parse its stream-json. Every other CLI draws its
// own full-screen interface with escape codes — boxes, spinners, colour. Re-rendering that in
// our message list would mean reimplementing each one and breaking on their next release. So
// they keep their own face: a pty on the backend, xterm.js here, and nothing in between that
// could misread them.
//
// Two shapes, one component:
//   <AgentTerminal />                                  the picker in Settings
//   <AgentTerminal embedded agentId="opencode" ... />   the Workspace chat area, become an agent
//
// Nothing on this screen touches cc_session, the chat transcript, or the send path. That
// isolation is the point — a new agent that misbehaves misbehaves inside this box.
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, Loader2, RotateCw, SquareTerminal, X } from "lucide-react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { api } from "../api/client";
import { cls } from "./ui";
import { sendSettings } from "./sendPrefs";
import type { WorkspaceRoot } from "../types";

type AgentCli = {
  id: string; name: string; color: string; install: string; note: string;
  installed: boolean; path: string;
};

export type AgentTerminalProps = {
  /** Text typed into the terminal once it is up — how a slash command the headless stream
   *  refuses (`/subtask`, `/fork`) reaches the one place it works. */
  typeOnReady?: string;
  onTyped?: () => void;
  /** Workspace mode: no picker, auto-launch, fill the pane. */
  embedded?: boolean;
  /** Which CLI to run when embedded. */
  agentId?: string;
  /** Folder to run it in when embedded. */
  cwd?: string;
  /** "Back to chat" in embedded mode. */
  onExit?: () => void;
  /** Claude Code only: resume THIS project's conversation instead of starting a fresh one.
   *  The backend stops the streaming session and reopens the same session id in the terminal,
   *  so the chat you were reading is the chat that appears here — and what you type here is
   *  in the feed when you come back. */
  resumeProjectId?: string;
  /** The chat's conversation picker, mirrored — otherwise "new conversation" then "open the
   *  terminal" quietly hands back the OLD chat, which is both wrong and, on a long history,
   *  a replay big enough to leave the pane blank. */
  resumeSession?: string;
  resumeFresh?: boolean;
  /** The folder's project id, so a Codex terminal opens on the chat box's model and effort. */
  projectId?: string;
};

function wsUrl(tid: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/api/terminal/ws/${encodeURIComponent(tid)}`;
}

export default function AgentTerminal({ embedded, agentId, cwd: fixedCwd, onExit, resumeProjectId,
  resumeSession = "", resumeFresh = false, typeOnReady = "", onTyped, projectId }: AgentTerminalProps) {
  const [agents, setAgents] = useState<AgentCli[]>([]);
  const [ptyError, setPtyError] = useState("");
  const [roots, setRoots] = useState<WorkspaceRoot[]>([]);
  const [pickedCwd, setPickedCwd] = useState("");
  const [tid, setTid] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const cwd = embedded ? (fixedCwd || "") : pickedCwd;
  const host = useRef<HTMLDivElement | null>(null);
  const term = useRef<XTerm | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const sock = useRef<WebSocket | null>(null);

  const loadAgents = useCallback(() => {
    api.terminalAgents()
      .then((r) => { setAgents(r.agents || []); setPtyError(r.ok ? "" : r.error || ""); })
      .catch((e) => setPtyError(String(e?.message || e)));
  }, []);

  useEffect(() => {
    loadAgents();
    if (embedded) return;
    api.wsRoots().then((r) => {
      setRoots(r);
      setPickedCwd((c) => c || r[0]?.path || "");
    }).catch(() => {});
  }, [loadAgents, embedded]);

  // One xterm for the life of the pane; sessions swap underneath it.
  useEffect(() => {
    if (!host.current || term.current) return;
    const t = new XTerm({
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: 12.5, cursorBlink: true, convertEol: false, scrollback: 5000,
      theme: { background: "#0b0e14", foreground: "#c9d1d9", cursor: "#7aa2f7" },
    });
    const f = new FitAddon();
    t.loadAddon(f);
    t.open(host.current);
    try { f.fit(); } catch { /* not laid out yet */ }
    term.current = t; fit.current = f;
    const onResize = () => {
      try { f.fit(); } catch { /* hidden */ }
      const s = sock.current;
      if (s && s.readyState === WebSocket.OPEN)
        s.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }));
    };
    window.addEventListener("resize", onResize);
    // The pane is resizable by dragging, which fires no window resize — watch the box itself.
    const ro = new ResizeObserver(onResize);
    ro.observe(host.current);
    return () => {
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      try { t.dispose(); } catch { /* already gone */ }
      term.current = null; fit.current = null;
    };
  }, []);

  // Wire the socket whenever the session changes.
  useEffect(() => {
    const t = term.current;
    if (!tid || !t) return;
    t.reset();
    const s = new WebSocket(wsUrl(tid));
    sock.current = s;
    const typed = t.onData((d) => {
      if (s.readyState === WebSocket.OPEN) s.send(JSON.stringify({ type: "in", data: d }));
    });
    s.onopen = () => {
      try { fit.current?.fit(); } catch { /* ignore */ }
      s.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }));
      t.focus();
      // A command handed over from the chat box. It goes in as keystrokes because that is the
      // only way in — the interactive CLI has no other input, which is exactly why /subtask
      // could not be run from the chat in the first place. The delay lets the TUI finish
      // drawing its prompt; typed into a half-painted screen, the first characters are lost.
      if (typeOnReady) {
        window.setTimeout(() => {
          if (s.readyState !== WebSocket.OPEN) return;
          s.send(JSON.stringify({ type: "in", data: typeOnReady }));
          onTyped?.();
        }, 1200);
      }
    };
    s.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data as string);
        if (m.type === "out") t.write(m.data);
        else if (m.type === "closed") t.write("\r\n\x1b[90m[terminal closed]\x1b[0m\r\n");
        else if (m.type === "error") t.write(`\r\n\x1b[31m${m.error}\x1b[0m\r\n`);
      } catch { /* not ours */ }
    };
    s.onerror = () => t.write("\r\n\x1b[31m[connection lost]\x1b[0m\r\n");
    return () => { typed.dispose(); try { s.close(); } catch { /* ignore */ } sock.current = null; };
  }, [tid]);

  const start = useCallback(async (a: AgentCli, install: boolean) => {
    setErr(""); setBusy(a.id + (install ? ":i" : ""));
    try {
      const t = term.current;
      const cols = t?.cols || 100, rows = t?.rows || 30;
      // Claude Code with a project: resume THAT conversation rather than open a blank one.
      // This is the whole point — the backend refuses mid-turn rather than lose an answer,
      // so an error here is information, not a failure to retry.
      if (resumeProjectId && a.id === "claude" && !install) {
        // The chat's own model, effort and permission mode for this folder: a mismatch would quietly
        // reopen the same conversation on a different model. This read the folder key alone, so a
        // folder with no pin of its own opened on the CLI default, and the effort was not the level
        // the chat box keeps for that model (sendPrefs.ts).
        const s = sendSettings(resumeProjectId, "claude");
        const r = await api.terminalClaude({
          project_id: resumeProjectId, cols, rows, cwd,
          model: s.model,
          permission_mode: s.permission_mode,
          effort: s.effort,
          session: resumeSession, fresh: resumeFresh,
        });
        if (!r.ok || !r.id) { setErr(r.error || "could not open the conversation here"); return; }
        setLabel(r.session_id ? "Claude Code · this conversation" : "Claude Code");
        setTid(r.id);
        return;
      }
      // Codex opens on the chat box's model and effort for this folder, like the Claude terminal.
      const cs = a.id === "codex" && projectId && !install ? sendSettings(projectId, "codex") : null;
      const r = await api.terminalLaunch({ agent: a.id, cwd, install, cols, rows,
        ...(cs ? { model: cs.model, effort: cs.effort } : {}) });
      if (!r.ok) { setErr(r.error || "could not start"); return; }
      setLabel(install ? `install ${a.name}` : a.name);
      setTid(r.id);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy("");
    }
  }, [cwd, resumeProjectId, resumeSession, resumeFresh, projectId]);

  // Embedded: the pane IS the agent, so start it as soon as we know which one.
  const chosen = embedded ? agents.find((a) => a.id === agentId) || null : null;
  useEffect(() => {
    if (!embedded || tid || busy) return;
    // Every one of these used to `return` in silence, leaving a black pane and no reason for it.
    // `ptyError` in particular is only rendered by the SETTINGS view, so a machine with no pty
    // showed the picker a clear message and showed the workspace nothing at all.
    if (ptyError) { setErr(ptyError); return; }
    if (!agents.length) return;                       // the list has not arrived yet — not an error
    if (!chosen) { setErr(`"${agentId}" is not an agent this Studio knows about.`); return; }
    if (!chosen.installed) { setErr(`${chosen.name} is not installed`); return; }
    start(chosen, false);
  }, [embedded, chosen, tid, busy, start, agents.length, ptyError, agentId]);

  async function close(back: boolean) {
    if (tid) { try { await api.terminalClose(tid); } catch { /* already gone */ } }
    setTid(""); setLabel(""); setErr("");
    loadAgents();                 // an install that just finished should flip to "installed"
    term.current?.reset();
    if (back) onExit?.();
  }

  // ---------------------------------------------------------------- embedded
  if (embedded) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="flex items-center gap-2 px-2 py-1 border-b border-line text-[11px]">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: chosen?.color || "#7c8aa5" }} />
          <span className="text-text truncate">{chosen?.name || agentId}</span>
          {/* Say which of the two this is. "Same conversation" is the whole feature, and a
              terminal that merely LOOKS like the chat would be the worst possible surprise. */}
          {resumeProjectId && agentId === "claude" ? (
            <span className="text-brand truncate"
              title={resumeFresh
                ? "A new conversation in this project. It lands in the feed when you go back."
                : "This is the chat you were reading, resumed. What you type here is in the feed when you go back."}>
              {resumeFresh ? "· new conversation" : "· this conversation, resumed"}
            </span>
          ) : (
            <span className="text-muted truncate">in {cwd || "no folder"}</span>
          )}
          <div className="ml-auto flex items-center gap-1 shrink-0">
            {tid && (
              <button className="chip hover:text-text" title="Restart this agent"
                onClick={() => { void close(false); }}>
                <RotateCw size={11} /> <span className="ml-1">Restart</span>
              </button>
            )}
            <button className="chip hover:text-text" title="Back to the Claude Code chat"
              onClick={() => { void close(true); }}>
              <ArrowLeft size={11} /> <span className="ml-1">Back to chat</span>
            </button>
          </div>
        </div>
        {err && (
          <div className="px-2 py-1.5 text-xs text-amber-400 border-b border-line flex items-center gap-2">
            <span className="flex-1">{err}</span>
            {chosen && !chosen.installed && (
              <button className="chip hover:text-text" onClick={() => start(chosen, true)}>
                <Download size={11} /> <span className="ml-1">Install it here</span>
              </button>
            )}
          </div>
        )}
        <div ref={host} className="flex-1 min-h-0 w-full" style={{ background: "#0b0e14" }} />
      </div>
    );
  }

  // ---------------------------------------------------------------- settings
  return (
    <div className="space-y-3">
      <div>
        <div className="text-sm font-medium text-text">Coding agents</div>
        <div className="text-xs text-muted mt-0.5">
          Launch a coding agent — it runs in a real terminal here, with its own interface. You can
          also pick one in the Workspace chat, and that pane becomes the agent.
        </div>
      </div>

      {ptyError && (
        <div className="card p-2 text-xs text-amber-400">Terminals are unavailable: {ptyError}</div>
      )}

      <div className="flex items-center gap-2 text-xs">
        <span className="text-muted shrink-0">Folder</span>
        <select className="input py-1 text-xs max-w-[420px]" value={pickedCwd}
          onChange={(e) => setPickedCwd(e.target.value)}>
          {roots.length === 0 && <option value="">(no folder opened yet)</option>}
          {roots.map((r) => <option key={r.path} value={r.path}>{r.path}</option>)}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {agents.map((a) => (
          <div key={a.id} className="card p-2 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: a.color }} />
            <div className="min-w-0 flex-1">
              <div className="text-xs text-text truncate">{a.name}</div>
              <div className="text-[10px] text-muted truncate" title={a.note}>{a.note}</div>
            </div>
            {a.installed ? (
              <button className="chip shrink-0 hover:text-text" disabled={!!busy}
                onClick={() => start(a, false)} title={a.path}>
                {busy === a.id ? <Loader2 size={11} className="animate-spin" /> : <SquareTerminal size={11} />}
                <span className="ml-1">Launch</span>
              </button>
            ) : (
              <button className="chip shrink-0 hover:text-text" disabled={!!busy}
                onClick={() => start(a, true)} title={a.install}>
                {busy === a.id + ":i" ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />}
                <span className="ml-1">Install</span>
              </button>
            )}
          </div>
        ))}
      </div>

      {err && <div className="text-xs text-danger">{err}</div>}

      <div className={cls("card overflow-hidden", !tid && "opacity-60")}>
        <div className="flex items-center justify-between px-2 py-1 border-b border-line">
          <div className="text-[11px] text-muted truncate">
            {tid ? <>Running <span className="text-text">{label}</span> in {cwd || "the current folder"}</>
                 : "Pick an agent above — it opens here."}
          </div>
          {tid && (
            <button className="chip hover:text-text shrink-0" onClick={() => close(false)} title="Close this terminal">
              <X size={11} /> <span className="ml-1">Close</span>
            </button>
          )}
        </div>
        <div ref={host} className="h-[420px] w-full" style={{ background: "#0b0e14" }} />
      </div>
    </div>
  );
}
