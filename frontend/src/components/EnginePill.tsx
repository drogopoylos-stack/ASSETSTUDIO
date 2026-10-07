import { useCallback, useEffect, useState } from "react";
import { Boxes } from "lucide-react";
import { api } from "../api/client";
import { cls, pollWhileVisible } from "./ui";
import { engineInUse, tabInUse } from "./engine/inUse";

// The status pill for the Studio's own engine, at the left end of the bar.
//
// It answers one question at a glance — is an agent building something right now — and opens the
// engine window when clicked. The headless-browser and subagent pills at the right end do the
// same job for their own machinery; this one sits on the left because the engine is where the
// work being watched actually happens, not a resource being consumed.
//
// It never starts anything. Polling a viewer must not cost a browser, so the endpoint behind this
// only reads: which games are open, how many generations were made recently, and what was last.

type State = Awaited<ReturnType<typeof api.engineState>>;

const ago = (ts: number): string => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
};

/** Open the engine as a real window. Electron turns a same-origin `window.open` into a native
 *  one (see `setWindowOpenHandler` in electron/main.cjs), so this is a program, not a tab. */
export function openEngineWindow() {
  try {
    window.open(`${location.origin}/engine`, "studio-engine",
                "width=1480,height=940,menubar=no,toolbar=no");
  } catch {
    location.href = "/engine";
  }
}

export function EnginePill({ compact }: { compact?: boolean }) {
  const [st, setSt] = useState<State | null>(null);
  const [stale, setStale] = useState(false);

  const load = useCallback(async () => {
    try {
      setSt(await api.engineState());
      setStale(false);
    } catch (e: any) {
      // The endpoint is newer than the running backend until it is restarted. Say so in the
      // tooltip rather than showing a dead pill that looks like "nothing is happening".
      setStale(/404/.test(e?.message || ""));
    }
  }, []);

  useEffect(() => pollWhileVisible(load, 4000), [load]);

  if (!st && !stale) return null;
  if (st && !st.enabled) return null;          // the live link is switched off; so is this

  // IN USE, not merely open: a game tab outlives its agent's last call, and every open one used to
  // count, so an agent that had finished a day earlier still lit the pill. See engine/inUse.ts.
  const busy = engineInUse(st);
  const games = (st?.tabs || []).filter(tabInUse).length;
  const open = st?.tabs?.length ?? st?.open_games ?? 0;
  const made = st?.recent_generations ?? 0;
  const latest = st?.latest;

  const title = stale
    ? "Studio engine — restart the backend to use this; the endpoint is newer than the running process."
    : [
        busy ? "An agent is using the engine now." : "The engine is idle.",
        games ? `${games} game${games > 1 ? "s" : ""} in use` : "",
        open > games ? `${open - games} game tab${open - games > 1 ? "s" : ""} open with no agent calling (closed after a few idle minutes)` : "",
        made ? `${made} made in the last 5 minutes` : "",
        latest ? `Last: ${latest.label || "untitled"} (${latest.project_name}) ${ago(latest.ts)}` : "",
        "Click to open the engine window.",
      ].filter(Boolean).join("\n");

  return (
    <button onClick={openEngineWindow} title={title}
      className={cls("flex items-center gap-1.5 px-1.5 py-0.5 rounded hover:bg-panel2 hover:text-text",
        busy && "text-brand")}>
      <Boxes size={13} className={busy ? "animate-pulse" : undefined} />
      {!compact && <span>Engine</span>}
      {/* A count only when there is one: a row of zeroes is noise in a status bar. */}
      {games > 0 && <span className="text-brand">{games}</span>}
      {busy && made > 0 && <span className="text-muted/70">·{made}</span>}
      {stale && <span className="text-warn">!</span>}
    </button>
  );
}

export default EnginePill;
