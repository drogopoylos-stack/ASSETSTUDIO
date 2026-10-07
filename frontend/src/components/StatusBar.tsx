import { useEffect, useState } from "react";
import {
  Activity, ChevronDown, ChevronUp, Cpu, HardDrive, MemoryStick, RotateCcw,
  Settings2, Wifi, WifiOff, Zap,
} from "lucide-react";
import { api } from "../api/client";
import { STATUS_DEFAULT, STATUS_ITEMS, activeJobs, useStore } from "../store/useStore";
import { BrowserPill } from "./BrowserPill";
import { EnginePill } from "./EnginePill";
import { CliPill } from "./CliPill";
import { SubAgentPill } from "./SubAgentPill";
import { NeedsYouPill } from "./NeedsYouPill";
import { cls } from "./ui";

// The persistent bottom bar: GPU/VRAM, CPU, RAM, disk, the link, the headless browser, jobs.
//
// What it shows, in what order and at what size is the user's choice — it is a strip of numbers
// on every screen, and which numbers matter depends entirely on what you are doing. The choice
// lives in one ordered list of ids: an item that is not in the list is not drawn, so the order
// and the on/off state cannot disagree with each other.

/** Three sizes, each a complete set. Mixing a large font with small icons looks like a mistake
 *  rather than a setting, so every measurement is named here and nothing is derived by guesswork. */
const SIZES = {
  compact: { bar: "h-8", text: "text-[11px]", gap: "gap-3", icon: 13, meter: "w-10 h-1.5", pad: "px-3" },
  normal: { bar: "h-12", text: "text-[13px]", gap: "gap-5", icon: 16, meter: "w-14 h-2", pad: "px-4" },
  large: { bar: "h-14", text: "text-[15px]", gap: "gap-6", icon: 19, meter: "w-20 h-2.5", pad: "px-5" },
} as const;

export default function StatusBar() {
  const { stats, wsConnected, jobs, setShowStatusBar, statusItems, statusSize } = useStore();
  const toast = useStore((s) => s.toast);
  const running = activeJobs(jobs).length;
  const [editing, setEditing] = useState(false);
  const [freeing, setFreeing] = useState(false);
  const z = SIZES[statusSize] || SIZES.normal;

  /** Hand cached model memory back, from the number that made you look.
   *
   *  The model weights live in two places and only one of them is this app: the Studio's own
   *  providers cache torch tensors in-process, while every ComfyUI-backed generator (video, 2D,
   *  3D, texture) leaves its UNET/encoder/VAEs inside the ComfyUI server — the reason VRAM and RAM
   *  both stay high after a video. The backend frees both; this refreshes the bar so the drop is
   *  visible immediately instead of on the next 2-second tick. */
  async function freeNow() {
    if (freeing) return;
    setFreeing(true);
    try {
      const r = await api.freeGpu();
      const c = r.comfyui;
      // ComfyUI reports both sides, and they do not have to move together: H3's 32B text encoder
      // can be offloaded to HOST RAM while the card looks free. Report whichever side actually
      // moved — never both, or the same gigabytes would be counted twice.
      const delta = (k: "vram_free" | "ram_free") =>
        c?.before && c?.after ? Math.max(0, c.after[k] - c.before[k]) / 1e9 : 0;
      const vramGb = delta("vram_free");
      const comfyGb = vramGb > 0.05 ? vramGb : delta("ram_free");
      const parts = [
        r.freed_mb ? `${(r.freed_mb / 1024).toFixed(1)} GB VRAM` : "",
        comfyGb > 0.05 ? `${comfyGb.toFixed(1)} GB ${vramGb > 0.05 ? "VRAM" : "RAM"} from ComfyUI` : "",
      ].filter(Boolean);
      toast(parts.length ? `Released ${parts.join(" + ")}` : "Nothing cached to release",
        parts.length ? "ok" : "warn");
      useStore.setState({ stats: await api.systemStats() });
    } catch (e: any) {
      toast(e.message, "danger");
    } finally {
      setFreeing(false);
    }
  }

  // fallback polling if WS stats are not arriving
  useEffect(() => {
    const id = setInterval(async () => {
      if (document.hidden) return;
      if (!useStore.getState().stats) {
        try {
          useStore.setState({ stats: await api.systemStats() });
        } catch {
          /* ignore */
        }
      }
    }, 3000);
    return () => clearInterval(id);
  }, []);

  const gpu = stats?.gpus?.[0];

  function item(id: string) {
    switch (id) {
      case "link":
        return (
          <span key={id} className={cls("flex items-center gap-1", wsConnected ? "text-ok" : "text-danger")}>
            {wsConnected ? <Wifi size={z.icon} /> : <WifiOff size={z.icon} />}
            {wsConnected ? "live" : "offline"}
          </span>
        );
      case "cpu":
        return <Metric key={id} z={z} icon={<Cpu size={z.icon} />} label="CPU"
          value={stats ? `${stats.cpu_percent.toFixed(0)}%` : "—"} pct={stats?.cpu_percent} />;
      case "ram":
        return <Metric key={id} z={z} icon={<MemoryStick size={z.icon} />} label="RAM"
          value={stats ? `${stats.ram_used_gb.toFixed(1)}/${stats.ram_total_gb.toFixed(0)}G` : "—"}
          pct={stats?.ram_percent} onClick={freeNow} busy={freeing}
          title="RAM held by cached models — a finished video's 32B text encoder sits here. Click to release it." />;
      case "gpuName":
        return gpu ? (
          <span key={id} className="flex items-center gap-1 text-text/80" title={gpu.name}>
            <Zap size={z.icon} className="text-accent" />
            {gpu.name.replace("NVIDIA GeForce ", "")}
          </span>
        ) : <span key={id} className="text-muted/60">no NVIDIA GPU</span>;
      case "gpu":
        return gpu ? <Metric key={id} z={z} icon={<Activity size={z.icon} />} label="GPU"
          value={`${gpu.util_percent.toFixed(0)}%`} pct={gpu.util_percent} /> : null;
      case "vram":
        return gpu ? <Metric key={id} z={z} label="VRAM"
          value={`${(gpu.vram_used_mb / 1024).toFixed(1)}/${(gpu.vram_total_mb / 1024).toFixed(0)}G`}
          pct={(gpu.vram_used_mb / Math.max(gpu.vram_total_mb, 1)) * 100}
          onClick={freeNow} busy={freeing}
          title="Cached model memory — ComfyUI's video/3D models live in its own process. Click to release." /> : null;
      case "temp":
        return gpu && gpu.temperature_c != null
          ? <span key={id}>{gpu.temperature_c.toFixed(0)}°C</span> : null;
      case "disk":
        return stats?.disk ? <Metric key={id} z={z} icon={<HardDrive size={z.icon} />} label="Disk"
          value={`${stats.disk.free_gb.toFixed(0)}G free`} pct={stats.disk.percent} /> : null;
      default:
        return null;
    }
  }

  // These belong at the far end: they report on work in progress rather than on the machine.
  const showNeeds = statusItems.includes("needs");
  const showBrowser = statusItems.includes("browser");
  const showCli = statusItems.includes("cli");
  const showAgents = statusItems.includes("subagents");
  const showJobs = statusItems.includes("jobs");
  // Left end, unlike the pills above: the engine is where the work being watched happens,
  // not a resource being consumed, and the user asked for it in that corner.
  const showEngine = statusItems.includes("engine");

  return (
    <div className={cls("shrink-0 border-t border-line bg-panel flex items-center select-none font-mono text-muted",
      z.bar, z.pad, z.gap, z.text)}>
      {showEngine && <EnginePill compact={statusSize === "compact"} />}
      {statusItems.filter((id) => id !== "browser" && id !== "cli" && id !== "jobs"
        && id !== "subagents" && id !== "engine" && id !== "needs").map(item)}

      <span className="ml-auto flex items-center gap-2">
        {/* First of the right-hand group: a project waiting on an answer outranks every other
            reading here, and it draws nothing at all while they are all quiet. */}
        {showNeeds && <NeedsYouPill compact={statusSize === "compact"} />}
        {showCli && <CliPill compact={statusSize === "compact"} />}
        {showAgents && <SubAgentPill compact={statusSize === "compact"} />}
        {showBrowser && <BrowserPill count={stats?.headless_browsers ?? 0}
          compact={statusSize === "compact"} />}
        {showJobs && running > 0 && (
          <span className="text-brand">{running} job{running > 1 ? "s" : ""} running</span>
        )}
        <span className="relative flex items-center">
          <button onClick={() => setEditing((v) => !v)}
            className={cls("p-0.5 rounded hover:bg-panel2 hover:text-text", editing && "text-brand bg-panel2")}
            title="Choose what this bar shows, and how big it is">
            <Settings2 size={z.icon - 1} />
          </button>
          {editing && <BarEditor onClose={() => setEditing(false)} />}
        </span>
        <button onClick={() => setShowStatusBar(false)}
          className="p-0.5 rounded hover:bg-panel2 hover:text-text" title="Hide the status bar">
          <ChevronDown size={z.icon - 1} />
        </button>
      </span>
    </div>
  );
}

/** The same controls in a popover, opened from the bar itself — which is where you are standing
 *  when you decide that a number is in your way. */
export function BarEditor({ onClose }: { onClose?: () => void }) {
  return (
    <>
      <div className="fixed inset-0 z-[80]" onClick={onClose} />
      <div className="absolute bottom-full right-0 mb-2 z-[81] card p-2 w-64 shadow-card text-xs font-sans">
        <BarEditorBody />
      </div>
    </>
  );
}

/** Pick the items, order them, set the size. */
export function BarEditorBody() {
  const { statusItems, statusSize, setStatusItems, setStatusSize } = useStore();
  const on = (id: string) => statusItems.includes(id);

  const toggle = (id: string) => setStatusItems(
    on(id) ? statusItems.filter((x) => x !== id)
           // put it back where it belongs in the canonical order, not on the end
           : STATUS_DEFAULT.filter((x) => x === id || statusItems.includes(x)));

  const move = (id: string, by: number) => {
    const i = statusItems.indexOf(id);
    const j = i + by;
    if (i < 0 || j < 0 || j >= statusItems.length) return;
    const next = statusItems.slice();
    [next[i], next[j]] = [next[j], next[i]];
    setStatusItems(next);
  };

  // Shown in the user's own order first, so the list reads like the bar it is editing; anything
  // switched off follows, greyed, ready to be switched back on.
  const rows = [...statusItems.filter((id) => STATUS_DEFAULT.includes(id)),
                ...STATUS_DEFAULT.filter((id) => !statusItems.includes(id))];

  return (
    <div className="text-xs">
        <div className="text-[10px] uppercase tracking-wide text-muted px-1 pb-1">Size</div>
        <div className="flex gap-1 px-1 pb-2">
          {(["compact", "normal", "large"] as const).map((s) => (
            <button key={s} onClick={() => setStatusSize(s)}
              className={cls("flex-1 py-1 rounded border capitalize",
                statusSize === s ? "border-brand/60 bg-brand/10 text-brand"
                                 : "border-line text-muted hover:text-text hover:bg-panel2")}>
              {s}
            </button>
          ))}
        </div>
        <div className="text-[10px] uppercase tracking-wide text-muted px-1 pb-1 border-t border-line pt-2">
          Items
        </div>
        <div className="max-h-64 overflow-auto">
          {rows.map((id) => {
            const meta = STATUS_ITEMS.find((x) => x.id === id)!;
            const shown = on(id);
            return (
              <div key={id} className="flex items-center gap-1 px-1 py-0.5 rounded hover:bg-panel2">
                <button onClick={() => toggle(id)}
                  className={cls("flex-1 text-left truncate", shown ? "text-text" : "text-muted/50 line-through")}>
                  <span className={cls("inline-block w-3", shown ? "text-brand" : "text-transparent")}>✓</span>
                  {meta.label}
                </button>
                <button disabled={!shown} onClick={() => move(id, -1)}
                  className="p-0.5 rounded text-muted hover:text-text disabled:opacity-20" title="Move left">
                  <ChevronUp size={11} />
                </button>
                <button disabled={!shown} onClick={() => move(id, 1)}
                  className="p-0.5 rounded text-muted hover:text-text disabled:opacity-20" title="Move right">
                  <ChevronDown size={11} />
                </button>
              </div>
            );
          })}
        </div>
        <button onClick={() => { setStatusItems(STATUS_DEFAULT.slice()); setStatusSize("normal"); }}
          className="mt-1.5 w-full py-1 rounded border border-line text-muted hover:text-text hover:bg-panel2 flex items-center justify-center gap-1.5">
          <RotateCcw size={11} /> Back to the default
        </button>
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
  pct,
  z,
  onClick,
  busy,
  title,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string;
  pct?: number | null;
  z: (typeof SIZES)[keyof typeof SIZES];
  /** Given a handler the reading becomes a button — see `freeNow`. */
  onClick?: () => void;
  busy?: boolean;
  title?: string;
}) {
  const color = pct == null ? "" : pct > 85 ? "text-danger" : pct > 60 ? "text-warn" : "text-text/80";
  const body = (
    <>
      {icon}
      <span className="text-muted">{label}</span>
      <span className={color}>{value}</span>
      {pct != null && (
        <span className={cls("rounded bg-panel2 overflow-hidden inline-block align-middle", z.meter)}>
          <span className={cls("block h-full", pct > 85 ? "bg-danger" : pct > 60 ? "bg-warn" : "bg-brand-600")}
            style={{ width: `${Math.min(100, pct)}%` }} />
        </span>
      )}
    </>
  );
  if (!onClick) return <span className="flex items-center gap-1" title={title || `${label} ${value}`}>{body}</span>;
  return (
    <button type="button" onClick={onClick} disabled={busy}
      className="flex items-center gap-1 -mx-1 px-1 rounded hover:bg-panel2 hover:text-text disabled:opacity-60"
      title={title || `${label} ${value}`}>
      {body}
    </button>
  );
}
