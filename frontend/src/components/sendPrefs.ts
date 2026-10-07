// THE CHAT BOX'S SEND SETTINGS, READ ONE WAY EVERYWHERE.
//
// The chat box keeps model, effort and approval mode per FOLDER and per agent
// (`cc-model:<folder>:<agent>`), and effort per MODEL as well (`cc-effort:<folder>:<agent>:<model>`).
// The agent-wide key (`cc-model-claude`) is only the default for a folder nobody has set up, and it
// holds whatever was picked last in ANY folder. Every other sender used to read that agent-wide key:
// an edit-and-resend in a folder pinned to Opus 5 went out as Opus 5.5 because Opus 5.5 had been
// picked last in another folder, and the header said "high" above a chat box set to max.
// A wrong value is not a one-off either. A send whose model, effort, mode or fork differs from the
// running session's RESTARTS that session with them (cc_session._send_streaming, `want_sig`), so it
// stays wrong until the next send from the chat box puts it back.
//
// ONE FOLDER, TWO IDS. The Workspace lower-cases the drive letter (workspace._claude_id);
// Mission Control lists ~/.claude/projects the way the CLI named them, often "C--Users-…". Both are
// the same folder, so a reader that finds nothing under one id tries the other before it falls back
// to the agent-wide default. Writes stay on the id the caller has.

type Reader = Pick<Storage, "getItem">;
type Writer = Pick<Storage, "setItem">;

const store = (): Storage | null => (typeof localStorage === "undefined" ? null : localStorage);

export const agentKey = (pid: string) => `cc-agent:${pid}`;
export const paneAgentKey = (pane: string, pid: string) => `cc-pane-agent:workspace:${pane}:${pid.replace(/^([A-Z])--/, (_, d: string) => d.toLowerCase() + "--")}`;
export function paneAgent(pane: string, pid: string, r: Reader | null = store()): string {
  return r?.getItem(paneAgentKey(pane, pid)) || folderAgent(pid, r);
}
export function writePaneAgent(pane: string, pid: string, agent: string, w: Writer | null = store()) {
  w?.setItem(paneAgentKey(pane, pid), agent);
}
export const prefKey = (kind: string, pid: string, a: string) => `cc-${kind}:${pid}:${a}`;
export const effortKey = (pid: string, a: string, m: string) => `cc-effort:${pid}:${a}:${m || "default"}`;
// Codex's "full" is full access with no sandbox: on Windows its sandboxed modes block or ask about
// every command until Codex's own sandbox is set up, so the mode that works on every PC is the default.
export const modeFallback = (a: string) => (a === "codex" || a === "deepseek-harness" ? "full" : "acceptEdits");

/** The ids one folder can be stored under: this one first, then the same id with the drive
 *  letter's case flipped. Anything that is not a drive-letter id has only itself. */
export function folderIds(pid: string): string[] {
  if (!pid) return [];
  const m = /^([A-Za-z])--/.exec(pid);
  if (!m) return [pid];
  const d = m[1];
  return [pid, (d === d.toLowerCase() ? d.toUpperCase() : d.toLowerCase()) + pid.slice(1)];
}

// An empty string counts as unset, as it always has in the chat box (`||`, not `??`).
function first(r: Reader, keys: string[]): string | null {
  for (const k of keys) {
    const v = r.getItem(k);
    if (v) return v;
  }
  return null;
}

/** A per-folder choice: this folder's own pin, else this agent's default everywhere, else `fb`. */
export function readPref(kind: string, pid: string, a: string, fb: string, r: Reader | null = store()): string {
  if (!r) return fb;
  return first(r, folderIds(pid).map((id) => prefKey(kind, id, a))) || r.getItem(`cc-${kind}-${a}`) || fb;
}

/** The effort the chat box sends with model `m`: that model's own level in this folder first.
 *  The right effort belongs to the model (see ChatComposer), so the folder's last effort is only
 *  the fallback for a model that has none of its own yet. */
export function readEffort(pid: string, a: string, m: string, r: Reader | null = store()): string {
  if (!r) return "default";
  return first(r, folderIds(pid).map((id) => effortKey(id, a, m))) || readPref("effort", pid, a, "default", r);
}

/** The agent this folder's own pin names, or "" when the folder has none. */
export function pinnedAgent(pid: string, r: Reader | null = store()): string {
  if (!r) return "";
  return first(r, folderIds(pid).map(agentKey)) || "";
}

/** The agent the chat box in this folder talks to: the folder's pin, else the last one picked. */
export function folderAgent(pid: string, r: Reader | null = store()): string {
  if (!r) return "claude";
  return pinnedAgent(pid, r) || r.getItem("cc-agent") || "claude";
}

export function writePref(kind: string, pid: string, a: string, v: string, w: Writer | null = store()) {
  if (!w) return;
  w.setItem(`cc-${kind}-${a}`, v);              // this agent's default everywhere
  if (pid) w.setItem(prefKey(kind, pid, a), v); // and pinned to this folder
}

export function writeEffort(pid: string, a: string, m: string, v: string, w: Writer | null = store()) {
  if (!w) return;
  writePref("effort", pid, a, v, w);                 // the agent-wide fallback
  if (pid) w.setItem(effortKey(pid, a, m), v);       // and this model's own level
}

export type SendSettings = {
  model: string; effort: string; permission_mode: string; fork: boolean; thinking: boolean;
};

/** What the chat box in this folder would send with `agent`, right now. Every sender that is not
 *  the chat box itself (edit-and-resend, answering a question, /compact, a quick reply, the
 *  terminal) must send exactly this, or it runs the wrong model and restarts the session. */
export function sendSettings(pid: string, agent: string, r: Reader | null = store()): SendSettings {
  const model = readPref("model", pid, agent, "default", r);
  return {
    model,
    effort: readEffort(pid, agent, model, r),
    permission_mode: readPref("mode", pid, agent, modeFallback(agent), r),
    fork: r?.getItem("cc-fork") === "1",
    thinking: r?.getItem("cc-thinking") === "1",
  };
}

/** Whose settings a feed's conversation runs on. An alternate engine's feed is
 *  "<engine>--<folder>" (Workspace altFeedPrefix) and runs on that engine's settings; any other
 *  feed is the folder's Claude Code conversation — also when the chat box is set to Codex or a
 *  terminal, which do not write into that conversation. */
export function feedAgent(feedId: string, folderId: string): string {
  if (folderId && feedId.length > folderId.length + 2 && feedId.endsWith("--" + folderId))
    return feedId.slice(0, feedId.length - folderId.length - 2);
  return "claude";
}
