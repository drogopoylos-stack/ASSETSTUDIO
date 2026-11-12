import { useCallback, useEffect, useRef, useState } from "react";
import { Download, ExternalLink, Loader2, Square, X } from "lucide-react";
import { api } from "../api/client";

/**
 * DEEPSEEK CREATOR MODE, OVER THE WHOLE WINDOW.
 *
 * Creator mode is a preset of the official DeepSeek Harness app, not of the SDK runtime the chat
 * box drives (that one has no presets at all — see backend `deepseek_creator`). So this shows the
 * app itself, started by the Studio for this folder: new sessions in Creator mode, this folder as
 * the workspace, the Studio's DeepSeek key. Its plugins are panels of the app's own page, which is
 * why the page is shown whole rather than rebuilt here.
 *
 * Closing the overlay leaves the app running, the way a chat keeps running when you switch panes;
 * Stop ends it.
 */
export function DeepSeekCreator({ folder, name, onClose, onSettings }: {
  folder: string;
  name?: string;
  onClose: () => void;
  /** Settings → API keys, for a Studio with no DeepSeek or RIVER.AI key. */
  onSettings?: () => void;
}) {
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<"checking" | "install" | "installing" | "starting" | "ready" | "error">("checking");
  const [msg, setMsg] = useState("");
  const [needsKey, setNeedsKey] = useState(false);
  const [info, setInfo] = useState<{ version: string; node: string; node_ok: boolean; log: string }>(
    { version: "", node: "", node_ok: true, log: "" });
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const start = useCallback(async () => {
    setPhase("starting");
    setMsg("");
    try {
      const r = await api.deepseekCreatorOpen(folder);
      if (!alive.current) return;
      if (r.ok && r.url) { setUrl(r.url); setPhase("ready"); return; }
      if (r.needs_install) { setPhase("install"); return; }
      setNeedsKey(!!r.needs_key);
      setMsg(r.error || "DeepSeek Harness did not start.");
      setPhase("error");
    } catch (e: any) {
      if (alive.current) { setMsg(String(e?.message || e)); setPhase("error"); }
    }
  }, [folder]);

  const check = useCallback(async () => {
    try {
      const s = await api.deepseekCreatorStatus();
      if (!alive.current) return;
      setInfo({ version: s.version, node: s.node, node_ok: s.node_ok, log: s.install_log || "" });
      if (s.install.running) { setPhase("installing"); return; }
      if (s.ready) { start(); return; }
      if (s.install.ok === false) setMsg(s.install.error || "The install failed.");
      setPhase("install");
    } catch (e: any) {
      if (alive.current) { setMsg(String(e?.message || e)); setPhase("error"); }
    }
  }, [start]);

  useEffect(() => { check(); }, [check]);
  // An install runs in the backend for a minute or two; ask how it is going until it ends.
  useEffect(() => {
    if (phase !== "installing") return;
    const t = window.setInterval(check, 2500);
    return () => window.clearInterval(t);
  }, [phase, check]);

  const install = async () => {
    setMsg("");
    const r = await api.deepseekCreatorInstall().catch((e) => ({ ok: false, error: String(e?.message || e) }));
    if (!r.ok) { setMsg((r as any).error || "The install did not start."); return; }
    setPhase("installing");
  };
  const stop = async () => {
    await api.deepseekCreatorStop(folder).catch(() => {});
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-bg text-text">
      <div className="h-10 shrink-0 flex items-center gap-2 px-3 border-b border-line bg-panel text-xs">
        <span className="h-2.5 w-2.5 rounded-full bg-[#4d6bfe] shrink-0" />
        <span className="font-semibold whitespace-nowrap">DeepSeek Creator mode</span>
        <span className="text-muted truncate" title={folder}>· {name || folder}</span>
        <span className="text-muted/70 truncate hidden md:inline">
          · new sessions build and install DeepSeek Harness plugins for this folder; each install asks you first
        </span>
        <div className="ml-auto flex items-center gap-1.5 shrink-0">
          {url && (
            <button className="chip flex items-center gap-1 hover:text-text" title="Open the same app in your browser"
              onClick={() => window.open(url, "_blank", "noopener")}>
              <ExternalLink size={11} /> browser
            </button>
          )}
          {url && (
            <button className="chip flex items-center gap-1 hover:text-text" title="Stop the app for this folder. Its sessions and plugins stay in its profile."
              onClick={stop}>
              <Square size={11} /> stop
            </button>
          )}
          <button className="p-1 rounded hover:bg-panel2 text-muted hover:text-text"
            title="Close. The app keeps running; open Creator mode again to come back to it." onClick={onClose}>
            <X size={15} />
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0 relative">
        {phase === "ready" && url ? (
          <iframe src={url} title="DeepSeek Harness" className="absolute inset-0 w-full h-full border-0 bg-black"
            allow="clipboard-read; clipboard-write; microphone" />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center p-6">
            <div className="max-w-lg w-full space-y-3 text-sm">
              {(phase === "checking" || phase === "starting") && (
                <div className="flex items-center gap-2 text-muted">
                  <Loader2 size={15} className="animate-spin" />
                  {phase === "checking" ? "Checking DeepSeek Harness…" : "Starting DeepSeek Harness for this folder…"}
                </div>
              )}
              {phase === "install" && (
                <>
                  <div className="font-medium">Creator mode needs the DeepSeek Harness app {info.version}.</div>
                  <p className="text-xs text-muted leading-relaxed">
                    The Studio installs it into its own data folder (about 900 MB, one to two minutes), with its
                    own pnpm. Nothing is installed globally. It needs Node.js 24.9 or newer
                    {info.node ? ` (this PC has ${info.node})` : ""}.
                  </p>
                  <button className="btn flex items-center gap-1.5" disabled={!info.node_ok} onClick={install}>
                    <Download size={13} /> Install DeepSeek Harness
                  </button>
                </>
              )}
              {phase === "installing" && (
                <>
                  <div className="flex items-center gap-2"><Loader2 size={15} className="animate-spin" /> Installing DeepSeek Harness…</div>
                  {info.log && <pre className="text-[10px] text-muted bg-panel2 rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap">{info.log}</pre>}
                </>
              )}
              {phase === "error" && (
                <>
                  <div className="text-warn">{msg}</div>
                  <div className="flex gap-2">
                    {needsKey && onSettings && <button className="btn" onClick={() => { onClose(); onSettings(); }}>Settings → API Keys</button>}
                    <button className="btn" onClick={check}>Try again</button>
                  </div>
                </>
              )}
              {msg && phase === "install" && <div className="text-warn text-xs">{msg}</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
