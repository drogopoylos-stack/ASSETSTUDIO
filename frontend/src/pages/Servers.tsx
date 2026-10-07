import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Loader2,
  Play,
  PlugZap,
  RefreshCw,
  Rocket,
  Save,
  ScrollText,
  Square,
  XCircle,
} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { ServiceStatus } from "../types";
import { Section, cls, pollWhileVisible } from "../components/ui";

export default function Servers() {
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [autoStart, setAutoStart] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useStore((s) => s.toast);
  const timer = useRef<number | null>(null);

  async function load() {
    try {
      setServices(await api.services());
    } catch (e: any) {
      toast(`Failed to load servers: ${e.message}`, "danger");
    }
  }

  useEffect(() => {
    api.settings().then((s) => setAutoStart(s.auto_start_services !== false)).catch(() => {});
    return pollWhileVisible(load, 3000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function start(id: string) {
    setBusy(id);
    try {
      const st = await api.startService(id, true);
      toast(st.reachable ? `${st.name} is running` : st.last_error || `Could not start ${st.name}`, st.reachable ? "ok" : "danger");
      await load();
    } finally {
      setBusy(null);
    }
  }
  async function stop(id: string) {
    setBusy(id);
    try {
      await api.stopService(id);
      await load();
    } finally {
      setBusy(null);
    }
  }
  async function startAll() {
    setBusy("__all__");
    try {
      const r = await api.startAllServices(true);
      const up = r.filter((s) => s.reachable).length;
      toast(`Started ${up}/${r.length} configured server${r.length === 1 ? "" : "s"}`, up ? "ok" : "warn");
      await load();
    } finally {
      setBusy(null);
    }
  }
  async function toggleAuto(v: boolean) {
    setAutoStart(v);
    try {
      await api.updateSettings({ auto_start_services: v });
    } catch {
      /* ignore */
    }
  }

  const configuredCount = services.filter((s) => s.configured).length;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <PlugZap size={20} className="text-brand" /> Local Servers
        </h2>
        <span className="text-sm text-muted">
          Launch the localhost model backends your free/local providers need.
        </span>
        <div className="ml-auto flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
            <input type="checkbox" checked={autoStart} onChange={(e) => toggleAuto(e.target.checked)} />
            auto-start on Generate
          </label>
          <button className="btn-ghost" onClick={load} title="Refresh">
            <RefreshCw size={15} />
          </button>
          <button className="btn-primary" onClick={startAll} disabled={!configuredCount || busy === "__all__"}>
            {busy === "__all__" ? <Loader2 size={15} className="animate-spin" /> : <Rocket size={15} />}
            Start all
          </button>
        </div>
      </div>

      {services.map((s) => (
        <ServiceCard key={s.id} s={s} busy={busy === s.id} onStart={() => start(s.id)} onStop={() => stop(s.id)} onSaved={load} />
      ))}

      <p className="text-xs text-muted">
        Tip: set each server's launch command + working folder below (installs vary). When "auto-start on Generate" is
        on, the studio starts the right server the moment you run a local provider, waits until it's reachable, then runs.
      </p>
    </div>
  );
}

function ServiceCard({
  s,
  busy,
  onStart,
  onStop,
  onSaved,
}: {
  s: ServiceStatus;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onSaved: () => void;
}) {
  const [command, setCommand] = useState(s.command);
  const [cwd, setCwd] = useState(s.cwd);
  const [autostart, setAutostart] = useState(s.autostart);
  const [dirty, setDirty] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [logs, setLogs] = useState("");
  const toast = useStore((st) => st.toast);

  useEffect(() => {
    if (!dirty) {
      setCommand(s.command);
      setCwd(s.cwd);
      setAutostart(s.autostart);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.command, s.cwd, s.autostart]);

  async function save() {
    try {
      await api.updateService(s.id, { command, cwd, autostart });
      setDirty(false);
      toast(`Saved ${s.name}`, "ok");
      onSaved();
    } catch (e: any) {
      toast(`Save failed: ${e.message}`, "danger");
    }
  }
  async function loadLogs() {
    try {
      const r = await api.serviceLogs(s.id);
      setLogs(r.log || "(no output yet)");
    } catch {
      setLogs("(failed to read logs)");
    }
  }

  const dot = s.reachable ? "bg-ok" : s.state === "starting" ? "bg-warn animate-pulse" : s.state === "unreachable" ? "bg-danger" : "bg-line";

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={cls("h-2.5 w-2.5 rounded-full shrink-0", dot)} />
        <span className="font-semibold">{s.name}</span>
        <span className={cls("text-xs", s.reachable ? "text-ok" : "text-muted")}>{s.reachable ? "running" : s.state}</span>
        {s.port && (
          <a href={`http://localhost:${s.port}`} target="_blank" rel="noreferrer" className="chip text-brand hover:underline">
            :{s.port}
          </a>
        )}
        <div className="flex items-center gap-1 flex-wrap">
          {s.powers.map((p) => <span key={p} className="chip">{p}</span>)}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {s.reachable ? (
            <button className="btn text-xs" onClick={onStop} disabled={busy}>
              <Square size={13} /> Stop
            </button>
          ) : (
            <button className="btn-primary text-xs" onClick={onStart} disabled={busy || !s.configured}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
              {busy ? "Starting…" : "Start"}
            </button>
          )}
        </div>
      </div>

      {s.docs && !s.configured && <p className="text-xs text-muted">{s.docs}</p>}
      {s.last_error && <p className="text-xs text-danger font-mono whitespace-pre-wrap">{s.last_error}</p>}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="label">Launch command</label>
          <input className="input font-mono text-xs" value={command} placeholder="e.g. python main.py --port 8188"
            onChange={(e) => { setCommand(e.target.value); setDirty(true); }} />
        </div>
        <div>
          <label className="label">Working folder (cwd)</label>
          <input className="input font-mono text-xs" value={cwd} placeholder="e.g. C:\\tools\\ComfyUI"
            onChange={(e) => { setCwd(e.target.value); setDirty(true); }} />
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
          <input type="checkbox" checked={autostart} onChange={(e) => { setAutostart(e.target.checked); setDirty(true); }} />
          auto-start this server when needed
        </label>
        <span className="text-[11px] text-muted font-mono">health: {s.health_url}</span>
        <div className="ml-auto flex items-center gap-2">
          <button className="btn-ghost text-xs" onClick={() => { setShowLogs((v) => !v); if (!showLogs) loadLogs(); }}>
            <ScrollText size={13} /> Logs
          </button>
          <button className="btn text-xs" onClick={save} disabled={!dirty}>
            <Save size={13} /> Save
          </button>
        </div>
      </div>

      {showLogs && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="label !mb-0">Server log (tail)</span>
            <button className="btn-ghost text-xs" onClick={loadLogs}><RefreshCw size={12} /> refresh</button>
          </div>
          <pre className="text-[11px] font-mono bg-bg border border-line rounded p-2 max-h-48 overflow-auto whitespace-pre-wrap">
            {logs || "(no output yet)"}
          </pre>
        </div>
      )}
    </div>
  );
}
