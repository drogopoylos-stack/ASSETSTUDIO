import { useEffect, useRef } from "react";
import { api } from "../api/client";

/**
 * Native OS notification when a workspace finishes a turn.
 *
 * A run can take ten minutes, and the whole point of the app is that you go and do something
 * else while it works. `Toasts.tsx` only reaches you while you are already looking at the
 * window — exactly when you did not need telling. This lives in App, so it keeps watching
 * whichever tab you are on, and it watches EVERY workspace: a finish in one notifies you
 * while you are working in another. That was the actual complaint.
 *
 * Three properties of `live-status` shape the logic, and getting any of them wrong produces
 * notifications that are wrong rather than merely absent:
 *
 *  1. Each project is reported TWICE — under its prefixed id (`zen--c--foo`) and under the
 *     bare folder id (`c--foo`). Walking `statuses` would notify twice for one turn. `agents`
 *     is keyed by the bare id only, one entry per workspace, so that is the identity used.
 *  2. A project appears only while a CLI process is alive. When the 15-minute reaper collects
 *     an idle session its key simply vanishes — long after the turn ended. So a key that
 *     DISAPPEARS must never notify; only an explicit busy -> idle flip on a key present in
 *     both snapshots counts. (The unread-badge code in Workspace.tsx treats a vanished key as
 *     "done", which is harmless for a dot and would be a false alarm here.)
 *  3. That same rule makes a backend restart silent: every key vanishes at once, and vanishing
 *     is not finishing.
 */

const POLL_MS = 4000;
const SETTINGS_EVERY = 4; // re-read the on/off switch every 4th tick (~16s)

/** `c--Users-Administrator-Desktop-brainrot-3d-game-research` -> `brainrot 3d game research`. */
function fallbackName(projectId: string): string {
  const withoutDrive = projectId.replace(/^[a-z]--/i, "");
  const tail = withoutDrive.split("-").filter(Boolean).slice(-6).join(" ");
  return tail || projectId;
}

type Bridge = { notify?: (title: string, body: string) => Promise<boolean> };

export function useTurnEndNotifier() {
  const wasBusy = useRef<Record<string, boolean> | null>(null);
  const names = useRef<Record<string, string>>({});
  const namesFetchedAt = useRef(0);
  const enabled = useRef(true);
  const ticks = useRef(0);

  useEffect(() => {
    let alive = true;

    async function resolveName(projectId: string): Promise<string> {
      if (names.current[projectId]) return names.current[projectId];
      // The overview is much heavier than live-status, so only reach for it when an id is
      // genuinely unknown, and at most once a minute — otherwise a workspace that never
      // appears in the list would cost a fetch on every finish.
      if (Date.now() - namesFetchedAt.current > 60000) {
        namesFetchedAt.current = Date.now();
        try {
          const ov = await api.mission();
          for (const p of ov.projects || []) {
            if (p && p.id) names.current[p.id] = p.name || fallbackName(p.id);
          }
        } catch {
          /* fall through to the derived name */
        }
      }
      return names.current[projectId] || fallbackName(projectId);
    }

    async function tick() {
      // Re-read the switch periodically so turning it off in Settings actually takes effect
      // without a reload. Cheap: the backend serves this from an in-memory dict.
      if (ticks.current % SETTINGS_EVERY === 0) {
        try {
          const st = await api.settings();
          enabled.current = st?.notify_turn_end !== false;
        } catch {
          /* keep the last known value */
        }
      }
      ticks.current += 1;

      let statuses: Record<string, boolean>;
      let agents: Record<string, string>;
      try {
        const r = await api.liveStatus();
        statuses = r.statuses || {};
        agents = r.agents || {};
      } catch {
        // Backend down or restarting. Drop the baseline — comparing against a snapshot from
        // before an outage would fire for every workspace the moment it returns.
        wasBusy.current = null;
        return;
      }
      if (!alive) return;

      // One entry per workspace, keyed by the bare id (note 1).
      const ids = Object.keys(agents).length ? Object.keys(agents) : Object.keys(statuses);
      const now: Record<string, boolean> = {};
      for (const id of ids) now[id] = !!statuses[id];

      const before = wasBusy.current;
      wasBusy.current = now;
      if (!before) return; // the first poll only establishes a baseline

      for (const [id, busyBefore] of Object.entries(before)) {
        if (!busyBefore) continue;
        if (!(id in now)) continue; // vanished, not finished (notes 2 and 3)
        if (now[id]) continue; // still working
        if (!enabled.current) continue; // switch is checked here so the baseline stays fresh while off
        // Don't interrupt someone already watching this exact workspace. Workspace.tsx
        // publishes which root is open; anything else (another tab, another workspace,
        // another window entirely) should still be told.
        let openRoot = "";
        try {
          openRoot = window.localStorage.getItem("ws-active") || "";
        } catch {
          /* private mode / storage disabled */
        }
        if (typeof document !== "undefined" && document.hasFocus() && openRoot === id) continue;
        const bridge = (window as unknown as { studioBridge?: Bridge }).studioBridge;
        if (!bridge?.notify) continue; // plain browser tab: no Electron, nothing to show
        const name = await resolveName(id);
        const who = agents[id] || "claude";
        try {
          await bridge.notify("Asset Studio — turn finished", `${who} finished in ${name}`);
        } catch {
          /* a failed toast must never disturb the poll loop */
        }
      }
    }

    // `tick` awaits several requests, so a plain interval can start a second run before the
    // first is done and hold two connections where it is entitled to one. A browser allows six
    // per origin, and starving that pool is what left the conversation feed empty.
    let busy = false;
    const guarded = () => {
      if (busy) return;
      busy = true;
      tick().finally(() => { busy = false; });
    };
    guarded();
    const h = window.setInterval(guarded, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(h);
    };
    // Set up once; live values are read through refs so the interval never goes stale.
  }, []);
}
