import { useEffect, useRef, useState } from "react";
import { ExternalLink, Film, Loader2, Plus, RefreshCw } from "lucide-react";
import { api } from "../api/client";

export default function VideoStudio() {
  const frame = useRef<HTMLIFrameElement>(null);
  const [url, setUrl] = useState("");
  const [running, setRunning] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState<string[]>([]);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setError("");
    setRunning(false);
    setReady(false);
    api.serviceForProvider("minimax-h3").then(async ({ service }) => {
      if (!service) throw new Error("Configure ComfyUI in Settings → Servers.");
      const base = new URL(service.health_url);
      base.pathname = "/";
      base.search = "";
      base.hash = "";
      if (alive) setUrl(base.href);
      const status = service.reachable ? service : await api.startService(service.id, true, 120);
      if (!status.reachable) throw new Error(status.last_error || "ComfyUI could not start. Check Settings → Servers.");
      if (alive) setRunning(true);
    }).catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [attempt]);

  useEffect(() => {
    if (!url) return;
    const origin = new URL(url).origin;
    let alive = true;
    const onMessage = async (event: MessageEvent) => {
      if (event.origin !== origin || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === "asset-studio-director:ready") {
        try {
          const response = await fetch("/minimax-director.json");
          if (!response.ok) throw new Error("The Director starter workflow is missing.");
          const workflow = await response.json();
          if (alive) frame.current?.contentWindow?.postMessage({ type: "asset-studio-director:open", workflow }, origin);
        } catch (e: any) { if (alive) setError(e.message); }
      }
      if (event.data?.type === "asset-studio-director:loaded") {
        setReady(true);
        setError("");
        setMissing(event.data.missing || []);
      }
      if (event.data?.type === "asset-studio-director:error") setError(event.data.message);
    };
    window.addEventListener("message", onMessage);
    return () => { alive = false; window.removeEventListener("message", onMessage); };
  }, [url]);

  useEffect(() => {
    if (!running || ready) return;
    const timer = window.setTimeout(() => setError("Director did not connect. Check that the Asset Studio Director bridge is installed, then restart ComfyUI in Settings → Servers."), 45000);
    return () => window.clearTimeout(timer);
  }, [running, ready, attempt]);

  async function newTimeline() {
    if (!url || !window.confirm("Start a new timeline? Save your current workflow in ComfyUI first if you want to keep it.")) return;
    try {
      const response = await fetch("/minimax-director.json");
      if (!response.ok) throw new Error("The Director starter workflow is missing.");
      frame.current?.contentWindow?.postMessage({ type: "asset-studio-director:new", workflow: await response.json() }, new URL(url).origin);
    } catch (e: any) { setError(e.message); }
  }

  const src = url ? `${url}?asset-studio-director=1&studio-origin=${encodeURIComponent(location.origin)}` : "";
  return <div className="h-full flex flex-col min-h-0">
    <div className="shrink-0 border-b border-line bg-panel px-4 py-2 flex items-center gap-3">
      <Film size={18} className="text-brand shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold">MiniMax H3 Director</div>
        <div className="text-xs text-muted">Timeline, CUTs, character / video / audio references · local ComfyUI</div>
      </div>
      <span className="text-xs text-muted inline-flex items-center gap-1.5">
        {!ready && !error && <Loader2 size={13} className="animate-spin" />}
        {ready ? "Connected" : error ? "Needs attention" : running ? "Opening Director…" : "Starting ComfyUI…"}
      </span>
      <button className="btn text-xs" onClick={newTimeline} disabled={!ready}><Plus size={13} /> New timeline</button>
      {url && <a className="btn text-xs" href={url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> ComfyUI</a>}
    </div>
    {missing.length > 0 && <div className="shrink-0 px-4 py-2 text-xs text-warn border-b border-line">
      Models still needed: {missing.join(", ")}. Generation becomes available when the downloads finish; refresh ComfyUI to update the model list.
    </div>}
    {error && <div className="shrink-0 px-4 py-3 text-sm text-danger flex items-center gap-3">
      <span className="flex-1">{error}</span>
      <button className="btn text-xs" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={13} /> Retry</button>
    </div>}
    {running ? <iframe ref={frame} key={attempt} src={src} title="MiniMax H3 Director timeline"
      className="w-full flex-1 min-h-0 border-0 bg-bg" allow="clipboard-read; clipboard-write; fullscreen" />
      : !error && <div className="flex-1 flex items-center justify-center text-muted"><Loader2 size={24} className="animate-spin" /></div>}
  </div>;
}
