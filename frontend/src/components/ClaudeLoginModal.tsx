import { useEffect, useRef, useState } from "react";
import { Check, Copy, KeyRound, Loader2, LogIn, RefreshCw, ShieldAlert, X } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import { cls } from "./ui";

type Phase = "prompt" | "waiting" | "checking";

// Global re-login popup. Polls Claude's auth state; when the OAuth token fails (a 401), it
// blocks with a clear prompt and opens Claude Code in a terminal so the user can re-authenticate
// in the browser — then a one-click recheck confirms and dismisses it.
export default function ClaudeLoginModal() {
  const toast = useStore((s) => s.toast);
  const [needs, setNeeds] = useState(false);
  const [reason, setReason] = useState("");
  const [loggedIn, setLoggedIn] = useState(true);
  const [phase, setPhase] = useState<Phase>("prompt");
  const [dismissed, setDismissed] = useState(false);
  const [command, setCommand] = useState("");
  const [copied, setCopied] = useState(false);
  const prevSince = useRef<number | null | undefined>(undefined);
  // which failure we've already auto-launched the login terminal for (once per failure)
  const autoRan = useRef<number | null | undefined>(undefined);

  // poll auth state (instant on the backend — reads a flag + the creds file)
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const loop = async () => {
      const s = await api.claudeAuth().catch(() => null);
      if (!alive) return;
      if (s) {
        setNeeds(s.needs_login);
        setReason(s.reason || "");
        setLoggedIn(s.logged_in);
        // a NEW/changed failure re-opens the popup even if a previous one was dismissed
        if (s.needs_login && s.since !== prevSince.current) { setDismissed(false); setPhase("prompt"); }
        if (!s.needs_login) { setPhase("prompt"); setDismissed(false); autoRan.current = undefined; }
        prevSince.current = s.since;
        // The backend only reports needs_login for a REAL sign-out (a stale access token is
        // refreshed silently instead), so it's safe to open the login terminal automatically —
        // once per failure — rather than making the user click through it.
        // FROM THE DESKTOP APP ONLY. The Studio's own headless browser loads this page too (the
        // reviews, the live link), and so does a phone on the LAN; each of those opened a login
        // console on the PC's desktop - found when the installed copy was opened headless on an
        // empty profile. Elsewhere the popup still offers the button.
        if (s.needs_login && autoRan.current !== s.since && (window as any).studioBridge) {
          autoRan.current = s.since;
          login();
        }
      }
      timer = window.setTimeout(loop, s && s.needs_login ? 5000 : 15000);
    };
    loop();
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);

  const open = needs && !dismissed;
  if (!open) return null;

  async function login() {
    const r = await api.claudeLogin().catch(() => null);
    if (r?.command) setCommand(r.command);
    if (r?.ok) { setPhase("waiting"); toast("Opened Claude Code — finish the login in your browser", "info"); }
    else { setPhase("waiting"); toast(r?.error || "Couldn't open the terminal — run the command shown", "danger"); }
  }

  async function recheck() {
    setPhase("checking");
    const r = await api.claudeRecheck().catch(() => null);
    if (r && !r.needs_login) {
      toast("Reconnected to Claude ✓", "ok");
      setNeeds(false);
      setDismissed(true);
    } else {
      setPhase("waiting");
      toast("Still not logged in — finish the login, then recheck", "warn");
    }
  }

  function copy() {
    if (!command) return;
    navigator.clipboard?.writeText(command).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); });
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/55 backdrop-blur-sm"
      style={{ animation: "fadeIn 0.15s ease" }}>
      <div className="w-full max-w-md rounded-2xl border border-warn/40 bg-panel shadow-2xl overflow-hidden"
        style={{ animation: "wf-spawn 0.25s cubic-bezier(0.22,1,0.36,1)" }}>
        {/* header */}
        <div className="relative p-4 pb-3 border-b border-line">
          <div className="absolute inset-0 wf-aurora opacity-30" />
          <div className="relative flex items-start gap-3">
            <div className="h-11 w-11 rounded-xl bg-warn/15 ring-1 ring-warn/30 flex items-center justify-center shrink-0">
              <ShieldAlert size={22} className="text-warn" />
            </div>
            <div className="min-w-0">
              <h2 className="text-[16px] font-bold">{loggedIn ? "Claude login expired" : "Log in to Claude"}</h2>
              <p className="text-[12px] text-muted mt-0.5">{reason || "Your Claude authentication failed (401)."}</p>
            </div>
          </div>
        </div>

        {/* body */}
        <div className="p-4 space-y-3">
          {phase === "prompt" && (
            <p className="text-[13px] text-text/80 leading-relaxed">
              You're signed out of Claude Code, so chats and agents are paused. A sign-in terminal
              opens automatically — finish it in your browser, then click <b className="text-text">I've
              logged in</b>. Your work and conversations are safe.
            </p>
          )}
          {phase !== "prompt" && (
            <div className="rounded-lg border border-line bg-bg/60 p-3 text-[13px] text-text/85 space-y-2">
              <p className="flex items-start gap-2"><LogIn size={15} className="text-brand mt-0.5 shrink-0" />
                A Claude Code terminal opened. If it asks you to log in, complete it in your browser.</p>
              <p className="flex items-start gap-2"><RefreshCw size={15} className="text-brand mt-0.5 shrink-0" />
                Back at the Claude prompt? Click <b className="text-text">I've logged in</b> below to reconnect.</p>
              {command && (
                <div className="flex items-center gap-1.5 pt-1">
                  <code className="flex-1 truncate text-[11px] font-mono bg-panel2 rounded px-2 py-1 text-accent">{command}</code>
                  <button onClick={copy} title="Copy — run this in a terminal if the window didn't open"
                    className="p-1.5 rounded-md border border-line text-muted hover:text-text">
                    {copied ? <Check size={13} className="text-ok" /> : <Copy size={13} />}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* actions */}
          <div className="flex items-center gap-2 pt-1">
            {phase === "prompt" ? (
              <button onClick={login}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white px-3.5 py-2 text-[13px] font-semibold">
                <KeyRound size={15} /> Log in to Claude
              </button>
            ) : (
              <button onClick={recheck} disabled={phase === "checking"}
                className="inline-flex items-center gap-1.5 rounded-lg bg-ok hover:opacity-90 text-bg px-3.5 py-2 text-[13px] font-semibold disabled:opacity-60">
                {phase === "checking" ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                I've logged in
              </button>
            )}
            {phase === "waiting" && (
              <button onClick={login} className="inline-flex items-center gap-1.5 rounded-lg border border-line hover:bg-panel2 text-muted px-3 py-2 text-[13px]">
                <LogIn size={14} /> Open again
              </button>
            )}
            <button onClick={() => setDismissed(true)}
              className="ml-auto inline-flex items-center gap-1 rounded-lg border border-line hover:bg-panel2 text-muted px-3 py-2 text-[13px]" title="Hide for now (reappears if it fails again)">
              <X size={14} /> Not now
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
