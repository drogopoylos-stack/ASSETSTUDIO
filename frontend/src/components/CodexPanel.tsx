// CODEX IN THE CHAT BOX: SIGN-IN, INSTALL, AND ITS OWN SETTINGS.
//
// A new PC could pick Codex and then get nothing back: the CLI was not signed in, and nothing in
// the Studio could sign it in. The backend now drives Codex's own app-server (codex_app.py), and
// this is the part you see: a banner that says what is missing - Codex itself, or a sign-in - and
// fixes it with one click, and the settings block with the models and efforts Codex itself lists.
//
// One poller for the whole window, however many chat boxes show Codex: the state lives in this
// module and every mounted user is told when it changes.
import { useEffect, useState } from "react";
import { Check, ClipboardList, Copy, Download, ExternalLink, KeyRound, Loader2, LogIn, LogOut, RefreshCw, Shield, X, Zap } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { CodexModel, CodexStatus } from "../types";
import { cls } from "./ui";

/** The Studio's access modes for Codex: [value, label, what it does]. */
export const CODEX_ACCESS: [string, string, string][] = [
  ["full", "Full access", "No sandbox and no questions: Codex runs commands and edits files itself. Works on every PC."],
  ["ask", "Ask me first", "Sandboxed. A command or an edit outside the sandbox waits for Allow or Deny in the chat."],
  ["read-only", "Read only", "Codex asks before it runs or changes anything."],
];

/** Any stored mode (also the old "full-auto" / "yolo") as one of CODEX_ACCESS. */
export function codexModeOf(m: string): string {
  const v = (m || "").toLowerCase();
  if (v === "ask" || v === "on-request" || v === "untrusted" || v === "workspace-write") return "ask";
  if (v === "read-only" || v === "readonly" || v === "plan" || v === "suggest") return "read-only";
  return "full";
}

const EFFORT_NAMES: Record<string, string> = {
  none: "None", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "X-High", max: "Max", ultra: "Ultra",
};
export const codexEffortLabel = (e: string) => EFFORT_NAMES[e] || e;

/** The model rows for the Model picker: the default first, named after the model it is today. */
export function codexModelRows(models: CodexModel[], def: string): [string, string][] {
  const d = models.find((m) => m.id === def);
  return [["default", d ? `Default (${d.name})` : "Default"], ...models.map((m) => [m.id, m.name] as [string, string])];
}

/** The efforts the chosen model lists, as picker rows. A saved level the model does not list is
 *  still shown (marked), so the picker never silently shows something other than what is sent. */
export function codexEffortRows(models: CodexModel[], def: string, model: string, current: string): [string, string][] {
  const m = models.find((x) => x.id === (model === "default" ? def : model));
  const rows: [string, string][] = [["default", m?.default_effort ? `Default (${codexEffortLabel(m.default_effort)})` : "Default"]];
  for (const e of m?.efforts || []) rows.push([e.id, codexEffortLabel(e.id)]);
  if (current && current !== "default" && !rows.some(([v]) => v === current))
    rows.push([current, `${codexEffortLabel(current)} (the model steps it down)`]);
  return rows;
}

// ---- the shared state --------------------------------------------------------------------
type State = { status: CodexStatus | null; models: CodexModel[]; def: string; modelsAt: number; err: string };
const st: State = { status: null, models: [], def: "", modelsAt: 0, err: "" };
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((f) => f());
let users = 0;
let timer = 0;
let running = false;

async function refreshModels(force = false) {
  try {
    const r = await api.codexModels(force);
    st.models = r.models || [];
    st.def = r.default || "";
    st.modelsAt = Date.now();
    notify();
  } catch { /* the next status poll tries again */ }
}

export async function refreshCodex(fresh = false) {
  try {
    const s = await api.codexStatus(fresh);
    const was = !!st.status?.ready;
    st.status = s;
    st.err = "";
    notify();
    if (s.ready && (!was || !st.models.length || Date.now() - st.modelsAt > 300_000)) await refreshModels(!was);
  } catch (e: any) {
    st.err = e?.message || "the backend did not answer";
    notify();
  }
}

async function loop() {
  if (running) return;
  running = true;
  try { await refreshCodex(); } finally { running = false; }
  if (users <= 0) return;
  const s = st.status;
  // quick while something is in motion (a sign-in in the browser, an install); slow once settled
  const hot = !!(s?.login?.pending || s?.install?.running || (s?.installed && !s?.ready));
  timer = window.setTimeout(loop, document.hidden ? 20_000 : hot ? 2_500 : 60_000);
}

/** Codex's status and models, kept fresh while `active`. */
export function useCodex(active: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const f = () => setTick((n) => n + 1);
    listeners.add(f);
    return () => { listeners.delete(f); };
  }, []);
  useEffect(() => {
    if (!active) return;
    users++;
    if (users === 1) { window.clearTimeout(timer); loop(); }
    return () => { users--; if (users <= 0) { users = 0; window.clearTimeout(timer); } };
  }, [active]);
  return { status: st.status, models: st.models, def: st.def, error: st.err, refresh: refreshCodex, refreshModels };
}

function kick() { window.clearTimeout(timer); if (users > 0) timer = window.setTimeout(loop, 400); }

// ---- the banner above the chat box ----------------------------------------------------------
/** What stands between you and a Codex answer, and the button that removes it. Nothing is drawn
 *  once Codex is installed and signed in. */
export function CodexSignIn({ status }: { status: CodexStatus }) {
  const toast = useStore((s) => s.toast);
  const [busy, setBusy] = useState("");
  const [keyOpen, setKeyOpen] = useState(false);
  const [key, setKey] = useState("");
  const box = "mb-2 rounded-lg border border-[#10a37f]/50 bg-[#10a37f]/10 px-2.5 py-2 text-xs space-y-1.5";
  const btn = "inline-flex items-center gap-1 rounded-md border border-line bg-panel2 px-2 py-1 hover:bg-line disabled:opacity-50";
  const primary = "inline-flex items-center gap-1 rounded-md bg-[#10a37f] text-white px-2 py-1 hover:brightness-110 disabled:opacity-50";

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    try { await fn(); } catch (e: any) { toast(e?.message || "Codex did not answer", "danger"); }
    setBusy("");
    kick();
  }

  if (!status.installed) {
    const ins = status.install;
    return (
      <div className={box}>
        <div className="font-medium text-text">Codex is not installed on this PC.</div>
        {ins?.running ? (
          <div className="flex items-center gap-1.5 text-muted">
            <Loader2 size={12} className="animate-spin shrink-0" />
            <span className="truncate">Installing Codex… {ins.log?.[ins.log.length - 1] || ""}</span>
          </div>
        ) : status.npm ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <button className={primary} disabled={!!busy}
              onClick={() => act("install", async () => {
                const r = await api.codexInstall(false);
                if (!r.ok) toast(r.error || "install failed", "danger");
              })}>
              {busy === "install" ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />} Install Codex
            </button>
            <span className="text-muted">One click: runs <code>npm install -g @openai/codex</code>.</span>
          </div>
        ) : (
          <div className="text-muted">
            Codex installs with Node.js, which is not on this PC. Install Node.js LTS from{" "}
            <a className="underline text-text" href="https://nodejs.org" target="_blank" rel="noreferrer">nodejs.org</a>,
            restart the Studio, then press Install here.
          </div>
        )}
        {ins?.ok === false && ins.error && <div className="text-danger">The install failed: {ins.error}</div>}
      </div>
    );
  }

  if (status.ready) return null;
  const lg = status.login;
  if (lg?.pending) {
    const device = lg.kind === "device";
    const url = device ? lg.verification_url : lg.auth_url;
    return (
      <div className={box}>
        {device ? (
          <>
            <div className="font-medium text-text">Open the page, sign in, and type this code:</div>
            <div className="flex items-center gap-2">
              <code className="text-base font-mono tracking-widest bg-bg rounded px-2 py-0.5 text-text select-all">{lg.user_code}</code>
              <button className={btn} onClick={() => { navigator.clipboard?.writeText(lg.user_code); toast("Code copied", "ok"); }}>
                <Copy size={11} /> Copy</button>
            </div>
          </>
        ) : (
          <div className="font-medium text-text">Finish signing in to ChatGPT in your browser.</div>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          {url && <a className={btn} href={url} target="_blank" rel="noreferrer"><ExternalLink size={11} /> {lg.opened ? "Open the page again" : "Open the sign-in page"}</a>}
          <button className={btn} disabled={!!busy} onClick={() => act("cancel", () => api.codexLoginCancel())}><X size={11} /> Cancel</button>
          <span className="text-muted flex items-center gap-1"><Loader2 size={11} className="animate-spin" /> waiting for the sign-in</span>
        </div>
      </div>
    );
  }

  return (
    <div className={box}>
      <div className="font-medium text-text">Sign in to Codex to chat with it.</div>
      <div className="text-muted leading-snug">
        Once is enough: the Codex CLI on this PC uses the same sign-in. Already signed in with{" "}
        <code>codex login</code>? Press Check again.
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <button className={primary} disabled={!!busy}
          onClick={() => act("chatgpt", async () => {
            const r = await api.codexLogin("chatgpt");
            if (!r.ok) toast(r.error || "sign-in failed", "danger");
          })}>
          {busy === "chatgpt" ? <Loader2 size={12} className="animate-spin" /> : <LogIn size={12} />} Sign in with ChatGPT
        </button>
        <button className={btn} disabled={!!busy} title="For a PC where the browser cannot reach this one: a code you type on a web page"
          onClick={() => act("device", async () => {
            const r = await api.codexLogin("device");
            if (!r.ok) toast(r.error || "sign-in failed", "danger");
          })}>
          {busy === "device" ? <Loader2 size={11} className="animate-spin" /> : null} Use a code
        </button>
        <button className={btn} disabled={!!busy} onClick={() => setKeyOpen((v) => !v)}><KeyRound size={11} /> Use an API key</button>
        <button className={btn} disabled={!!busy} onClick={() => act("check", () => api.codexRecheck().then(() => refreshCodex()))}>
          {busy === "check" ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} Check again
        </button>
      </div>
      {keyOpen && (
        <div className="flex items-center gap-1.5">
          <input type="password" autoFocus value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-…  (an OpenAI API key)"
            className="input !py-1 text-xs flex-1 min-w-0"
            onKeyDown={(e) => { if (e.key === "Enter" && key.trim()) (e.currentTarget.nextSibling as HTMLButtonElement | null)?.click(); }} />
          <button className={primary} disabled={!key.trim() || !!busy}
            onClick={() => act("apikey", async () => {
              const r = await api.codexLogin("apikey", key.trim());
              if (!r.ok) { toast(r.error || "the key was refused", "danger"); return; }
              setKey(""); setKeyOpen(false);
              toast("Codex is signed in with your API key", "ok");
            })}>
            {busy === "apikey" ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} Save
          </button>
        </div>
      )}
      {lg && lg.success === false && lg.error && <div className="text-danger">The last sign-in did not finish: {lg.error}</div>}
      {status.error && <div className="text-danger">{status.error}</div>}
      <div className="text-[10px] text-muted/70">
        You can also open Codex in a terminal (agent menu, then Codex under "Or open this in a terminal") and sign in there.
      </div>
    </div>
  );
}

/** "signed in as a@b.com · plus", in words. */
export function codexWho(status: CodexStatus | null): string {
  if (!status) return "…";
  if (!status.installed) return "not installed";
  if (!status.signed_in) return status.ready ? "no sign-in needed (custom provider)" : "not signed in";
  if (status.auth === "apiKey") return "signed in with an API key";
  return `signed in${status.email ? ` as ${status.email}` : ""}${status.plan ? ` · ${status.plan}` : ""}`;
}

/** Settings -> Coding agents: Codex's install and sign-in, outside any chat. */
export function CodexAccountCard() {
  const codex = useCodex(true);
  const toast = useStore((s) => s.toast);
  const [busy, setBusy] = useState("");
  const s = codex.status;
  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    try { await fn(); } catch (e: any) { toast(e?.message || "Codex did not answer", "danger"); }
    setBusy("");
    kick();
  }
  return (
    <div className="card p-3 mb-3 space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: "#10a37f" }} />
        <span className="font-semibold">OpenAI Codex in the chat</span>
        <span className="ml-auto text-xs text-muted">
          {s ? (s.installed ? `Codex ${s.version || "?"}${s.outdated ? ` · ${s.latest} is out` : ""}` : "not installed") : "…"}
        </span>
      </div>
      {s && (!s.installed || !s.ready) ? <CodexSignIn status={s} /> : s ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Shield size={13} className="text-ok shrink-0" />
          <span>{codexWho(s)}</span>
          {s.signed_in && (
            <button className="inline-flex items-center gap-1 rounded-md border border-line bg-panel2 px-2 py-1 hover:bg-line" disabled={!!busy}
              onClick={() => act("logout", async () => { await api.codexLogout(); toast("Codex signed out", "info"); })}>
              <LogOut size={11} /> Sign out</button>)}
          {s.install?.running ? <span className="inline-flex items-center gap-1 text-muted"><Loader2 size={11} className="animate-spin" /> updating…</span>
            : s.outdated && s.npm ? (
              <button className="inline-flex items-center gap-1 rounded-md border border-line bg-panel2 px-2 py-1 hover:bg-line" disabled={!!busy}
                title="npm install -g @openai/codex@latest. The newest models need the newest Codex."
                onClick={() => act("update", async () => { const r = await api.codexInstall(true); if (!r.ok) toast(r.error || "update failed", "danger"); })}>
                <Download size={11} /> Update to {s.latest}</button>) : null}
        </div>
      ) : null}
      {codex.error && <div className="text-xs text-danger">Could not ask the backend about Codex: {codex.error}</div>}
      <p className="text-[11px] text-muted leading-snug">
        Pick OpenAI Codex in a chat box's agent menu. It keeps its own conversation for each folder and streams it into
        the chat: its answers, commands, file edits, plans, subagents and pictures, with the models and efforts your
        Codex offers. The Codex CLI on this PC shares this sign-in.
      </p>
    </div>
  );
}

// ---- the settings block (inside the chat box's settings menu) ----------------------------------
export function CodexSettings({
  status, models, def, model, effort, mode, fast, planner, onEffort, onMode, onFast, onPlanner, rootPath,
}: {
  status: CodexStatus | null; models: CodexModel[]; def: string;
  model: string; effort: string; mode: string; fast: boolean; planner: boolean;
  onEffort: (v: string) => void; onMode: (v: string) => void; onFast: (v: boolean) => void;
  onPlanner: (v: boolean) => void;
  rootPath?: string;
}) {
  const toast = useStore((s) => s.toast);
  const [busy, setBusy] = useState("");
  const m = models.find((x) => x.id === (model === "default" ? def : model));
  const access = codexModeOf(mode);
  const accessRow = CODEX_ACCESS.find(([v]) => v === access);
  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    try { await fn(); } catch (e: any) { toast(e?.message || "Codex did not answer", "danger"); }
    setBusy("");
    kick();
  }
  const who = codexWho(status);
  return (
    <>
      <div><label className="label !mb-0.5 flex items-center gap-1">Effort <span className="text-muted/60 font-normal normal-case">· reasoning</span></label>
        <select className="input !py-1 text-xs" value={effort} onChange={(e) => onEffort(e.target.value)}>
          {codexEffortRows(models, def, model, effort).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        {m && effort !== "default" && m.efforts.find((e) => e.id === effort)?.description && (
          <p className="text-[10px] text-muted/70 leading-snug mt-0.5">{m.efforts.find((e) => e.id === effort)!.description}</p>)}</div>
      <div><label className="label !mb-0.5">Access</label>
        <select className="input !py-1 text-xs" value={access} onChange={(e) => onMode(e.target.value)}>
          {CODEX_ACCESS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        {accessRow && <p className="text-[10px] text-muted/70 leading-snug mt-0.5">{accessRow[2]}</p>}
        {access !== "full" && status?.sandbox === "notConfigured" && (
          <div className="mt-1 text-[10px] text-warn/90 leading-snug">
            Codex's Windows sandbox is not set up, so Codex asks before each command.{" "}
            <button className="underline" disabled={!!busy}
              onClick={() => act("sandbox", async () => {
                const r = await api.codexSandbox(rootPath || "");
                toast(r.ok ? "Codex is setting up its sandbox (no admin needed)" : (r.error || "setup failed"), r.ok ? "ok" : "danger");
              })}>Set it up (no admin)</button>
          </div>)}</div>
      {m?.fast && (
        <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2"
          title={m.fast_note || "The priority tier: faster answers, more usage"} onClick={() => onFast(!fast)}>
          <Zap size={14} className={cls("shrink-0", fast ? "text-brand" : "text-muted/60")} />
          <span className="text-left min-w-0">
            <span className={cls("block text-xs font-medium", !fast && "text-muted")}>Fast</span>
            <span className="block text-[10px] text-muted/70 leading-tight truncate">{m.fast_note || "faster answers, more usage"}</span>
          </span>
          <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", fast ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
            <span className="h-4 w-4 rounded-full bg-white shadow" />
          </span>
        </button>)}
      <button type="button" role="switch" aria-checked={planner} aria-label="Codex Planner"
        className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2"
        onClick={() => onPlanner(!planner)}>
        <ClipboardList size={14} className={cls("shrink-0", planner ? "text-brand" : "text-muted/60")} />
        <span className="text-left min-w-0">
          <span className={cls("block text-xs font-medium", !planner && "text-muted")}>Planner</span>
          <span className="block text-[10px] text-muted/70 leading-tight">Plan mode: explore, ask questions and propose a plan. Applies to the next turn.</span>
        </span>
        <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", planner ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
          <span className="h-4 w-4 rounded-full bg-white shadow" />
        </span>
      </button>
      <div className="rounded-md border border-line bg-panel2/40 px-2 py-1.5 space-y-1">
        <div className="flex items-center gap-1.5">
          <Shield size={12} className={cls("shrink-0", status?.ready ? "text-ok" : "text-muted/60")} />
          <span className="text-[11px] truncate" title={who}>{who}</span>
          {status?.signed_in && (
            <button className="ml-auto text-[10px] text-muted hover:text-danger inline-flex items-center gap-0.5 shrink-0" disabled={!!busy}
              onClick={() => act("logout", async () => { await api.codexLogout(); toast("Codex signed out", "info"); })}>
              <LogOut size={10} /> Sign out</button>)}
        </div>
        {status?.installed && (
          <div className="flex items-center gap-1.5 text-[10px] text-muted">
            <span className="truncate">Codex {status.version || "?"}{status.outdated ? ` · ${status.latest} is out` : status.latest ? " · up to date" : ""}</span>
            {status.install?.running ? <span className="ml-auto inline-flex items-center gap-1"><Loader2 size={10} className="animate-spin" /> updating…</span>
              : status.outdated && status.npm ? (
                <button className="ml-auto underline hover:text-text shrink-0" disabled={!!busy}
                  title="npm install -g @openai/codex@latest. The newest models need the newest Codex."
                  onClick={() => act("update", async () => { const r = await api.codexInstall(true); if (!r.ok) toast(r.error || "update failed", "danger"); })}>
                  Update</button>) : null}
          </div>)}
        {status?.install?.ok === false && status.install.error && <div className="text-[10px] text-danger">{status.install.error}</div>}
      </div>
      <p className="text-[10px] text-muted/70 leading-snug">
        Codex keeps its own conversation for this folder and streams it here: its commands, edits,
        plans, subagents and pictures. Attached images go to Codex as images.
      </p>
    </>
  );
}
