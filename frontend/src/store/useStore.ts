import { create } from "zustand";
import type { Asset, Job, SystemStats, WSEvent } from "../types";
import {
  applySkin, applyTheme, getStoredProjectCli, getStoredSkin, getStoredTheme,
  SkinId, storeProjectCli, ThemeId,
} from "../theme";
import { getStoredDetail, storeDetail, type FeedDetail } from "../components/feedDetail";

export type TabId =
  | "dashboard"
  | "mission"
  | "workspace"
  | "workflows"
  | "chat"
  | "plans"
  | "image"
  | "video"
  | "studio2d"
  | "studio3d"
  | "texture"
  | "rig"
  | "pipeline"
  | "catalog"
  | "jobs"
  | "compare"
  | "servers"
  | "settings";

export interface Toast {
  id: number;
  kind: "info" | "ok" | "warn" | "danger";
  message: string;
}

/** One comment, on one line of one diff.
 *
 *  `code` is the line's own text and it is not optional: a feed diff of an Edit shows the diff of
 *  two STRINGS, so its line numbers count from the start of the edit, not from the start of the
 *  file. Quoting the line is the only reference that is true in both cases, which is why `line`
 *  is only ever sent when `lineIsFile` says the number means what it looks like. */
export interface DiffNote {
  id: number;
  projectId: string;
  file: string;
  code: string;
  side: "add" | "del" | "ctx";
  line?: number;
  lineIsFile?: boolean;
  note: string;
}

/** Every item the bottom bar can show, in the order it is offered. The bar's own list is a
 *  subset of these ids, so a build that adds an item does not disturb a saved arrangement — a
 *  new id is simply not in the saved list, and stays off until it is turned on. */
export const STATUS_ITEMS: { id: string; label: string }[] = [
  { id: "link", label: "Connection" },
  { id: "cpu", label: "CPU" },
  { id: "ram", label: "RAM" },
  { id: "gpuName", label: "GPU name" },
  { id: "gpu", label: "GPU load" },
  { id: "vram", label: "VRAM" },
  { id: "temp", label: "GPU temperature" },
  { id: "disk", label: "Disk" },
  { id: "needs", label: "Who needs you" },
  { id: "cli", label: "Claude Code version" },
  { id: "engine", label: "Studio engine" },
  { id: "browser", label: "Headless browser" },
  { id: "subagents", label: "Subagents" },
  { id: "jobs", label: "Running jobs" },
];
export const STATUS_DEFAULT = STATUS_ITEMS.map((i) => i.id);

// A saved bar never gained a new reading. The list is stored verbatim, so an item added to the
// Studio later was simply absent from every customised bar — invisible, with nothing to say it
// existed. The saved list alone cannot tell "switched off" from "not invented yet", so what the
// user has actually been OFFERED is recorded next to it, and only genuinely new ids are appended.
const KNOWN_KEY = "status-items-known";
// The ids that existed before that record was kept. A save from before then is compared against
// this, so an item missing from it was switched off on purpose and stays off.
const STATUS_LEGACY = ["link", "cpu", "ram", "gpuName", "gpu", "vram", "temp", "disk",
  "browser", "subagents", "jobs"];

interface AppState {
  tab: TabId;
  setTab: (t: TabId) => void;

  // when set, the Workspace tab should select this project path on mount
  workspaceTarget: string | null;
  // a file clicked in a chat feed → the Workspace opens it in an editor tab on the right
  fileTarget: { path: string; projectId?: string; nonce: number } | null;
  openFileInWorkspace: (path: string, projectId?: string) => void;
  clearFileTarget: () => void;
  openInWorkspace: (path: string) => void;
  clearWorkspaceTarget: () => void;

  // cross-pane signal: jump the Claude feed to a specific prompt (Prompt History → feed)
  feedJump: { id: string; key: string; nonce: number } | null;
  jumpToPrompt: (id: string, key: string) => void;

  theme: ThemeId;
  setTheme: (t: ThemeId) => void;

  // How the studio is drawn: the Studio's own look, or the Claude Code console look.
  // Presentation only — it never touches the session, the transcript or the send path.
  skin: SkinId;
  setSkin: (s: SkinId) => void;

  // HOW MUCH OF A COMMAND'S OUTPUT THE CHAT SHOWS. Not a look — the look decides the shape of
  // a row, this decides how many rows there are. One choice for the whole studio, because it is
  // about how you read rather than about which project you are in.
  feedDetail: FeedDetail;
  setFeedDetail: (d: FeedDetail) => void;

  // Workspaces that have switched themselves to the console look, whatever `skin` says.
  // A project can turn the look ON for itself; it cannot turn it off for a studio that is
  // already in it, because there is no sane way to draw a studio pane inside a console window.
  projectCli: Record<string, boolean>;
  setProjectCli: (projectId: string, on: boolean) => void;

  missionFont: number; // px size for the Mission Control feed text only
  setMissionFont: (n: number) => void;

  wsFont: number; // px size for the Workspace Claude feed text only
  setWsFont: (n: number) => void;

  showStatusBar: boolean; // bottom CPU/GPU/RAM bar visibility
  setShowStatusBar: (b: boolean) => void;

  // What the bottom bar shows, in the order it shows it, and how big. The array is both the
  // order AND the on/off set: an item that is not in it is not drawn. One list rather than a
  // list plus a set of flags, because two of those can disagree and one cannot.
  statusItems: string[];
  statusSize: "compact" | "normal" | "large";
  /** The project the workspace is showing, so window-wide chrome can ask about it. */
  activeProject: string;
  setActiveProject: (id: string) => void;
  setStatusItems: (ids: string[]) => void;
  setStatusSize: (s: "compact" | "normal" | "large") => void;

  wsConnected: boolean;
  setWsConnected: (b: boolean) => void;

  stats: SystemStats | null;
  jobs: Record<string, Job>;        // live/known jobs by id
  recentAssets: Asset[];            // newest asset_created first
  agentRuns: Record<string, any>;
  // live Claude-session state pushed over WS per project (SessionFeed renders from this
  // the instant it arrives; `at` lets consumers detect a stale/broken push and fall back)
  ccLive: Record<string, { state: any; at: number }>;

  toasts: Toast[];
  toast: (message: string, kind?: Toast["kind"]) => void;
  dismissToast: (id: number) => void;

  // TEXT ON ITS WAY INTO A COMPOSER. Two features hand the agent something the user assembled
  // by pointing rather than typing — a clicked page element, a batch of diff comments — and
  // both must land in the box the user can still edit before sending. One channel for both, so
  // there is one place to get the "which composer" rule right.
  //
  // The rule: the first composer of that project to see a NEW nonce takes it, tracked in a
  // module variable rather than in the store, because clearing a store field from inside an
  // effect does not stop a sibling's effect that is already queued for the same commit.
  composerInsert: { projectId: string; text: string; nonce: number } | null;
  insertIntoComposer: (projectId: string, text: string) => void;

  // A comment written on one line of a diff, waiting to be sent. Kept in the store rather than
  // in the diff component, because the diff is inside a feed that re-renders and unmounts as it
  // scrolls, and a comment must survive that — it is the user's writing, not a view state.
  diffNotes: DiffNote[];
  addDiffNote: (n: Omit<DiffNote, "id">) => void;
  dropDiffNote: (id: number) => void;
  clearDiffNotes: (projectId: string) => void;

  handleEvent: (ev: WSEvent) => void;
  upsertJob: (job: Job) => void;

  // per-project in-flight chat messages (persist across workspace/tab switches)
  inflight: Record<string, { full: string; images: string[] }[]>;
  pushInflight: (pid: string, m: { full: string; images: string[] }) => void;
  dropInflight: (pid: string, m: { full: string; images: string[] }) => void;
  clearInflight: (pid: string) => void;

  // Settings → Plugins. null = not fetched yet, which must read as "show everything":
  // hiding tabs for the first moment of every launch would flash the whole nav bar.
  enabledTabs: TabId[] | null;
  setEnabledTabs: (t: TabId[]) => void;
  loadPlugins: () => Promise<void>;
}

let toastSeq = 1;
let noteSeq = 1;

const TAB_SLUGS: TabId[] = [
  "dashboard", "mission", "workspace", "workflows", "chat", "plans", "image", "video", "studio2d", "studio3d", "texture", "rig",
  "pipeline", "catalog", "jobs", "compare", "servers", "settings",
];
// Last known plugin state, so a reload paints the right tabs immediately instead of
// showing a disabled tab until the fetch lands.
const TABS_LS = "studio-enabled-tabs";
function cachedTabs(): TabId[] | null {
  try {
    const raw = localStorage.getItem(TABS_LS);
    if (!raw) return null;
    const ids = JSON.parse(raw) as string[];
    return Array.isArray(ids) && ids.length ? (ids.filter((i) => TAB_SLUGS.includes(i as TabId)) as TabId[]) : null;
  } catch { return null; }
}
export function tabFromPath(): TabId {
  if (typeof location === "undefined") return "dashboard";
  const p = location.pathname.replace(/^\/+/, "").split("/")[0];
  return (TAB_SLUGS.includes(p as TabId) ? p : "dashboard") as TabId;
}

export const useStore = create<AppState>((set, get) => ({
  tab: tabFromPath(),
  setTab: (t) => {
    set({ tab: t });
    const path = t === "dashboard" ? "/" : "/" + t;
    if (typeof history !== "undefined" && location.pathname !== path) {
      history.pushState({}, "", path);
    }
  },

  workspaceTarget: null,
  openInWorkspace: (path) => {
    try { localStorage.setItem("ws-root", path); } catch { /* ignore */ }
    set({ workspaceTarget: path });
    get().setTab("workspace");
  },
  clearWorkspaceTarget: () => set({ workspaceTarget: null }),

  fileTarget: null,
  openFileInWorkspace: (path, projectId) => {
    set((s) => ({ fileTarget: { path, projectId, nonce: (s.fileTarget?.nonce || 0) + 1 } }));
    get().setTab("workspace");
  },
  clearFileTarget: () => set({ fileTarget: null }),

  feedJump: null,
  jumpToPrompt: (id, key) => set((s) => ({ feedJump: { id, key, nonce: (s.feedJump?.nonce || 0) + 1 } })),

  enabledTabs: cachedTabs(),
  setEnabledTabs: (t) => {
    try { localStorage.setItem(TABS_LS, JSON.stringify(t)); } catch { /* ignore */ }
    set({ enabledTabs: t });
    // Standing on a tab that was just turned off would leave a blank window.
    const cur = get().tab;
    if (!t.includes(cur)) get().setTab("workspace");
  },
  loadPlugins: async () => {
    try {
      const { api } = await import("../api/client");
      const r = await api.plugins();
      get().setEnabledTabs(r.plugins.filter((p) => p.kind === "tab" && p.enabled).map((p) => p.id as TabId));
    } catch { /* backend not up yet — the cached list stands */ }
  },

  theme: getStoredTheme(),
  setTheme: (t) => {
    applyTheme(t);
    set({ theme: t });
  },

  skin: getStoredSkin(),
  setSkin: (s) => {
    applySkin(s);
    set({ skin: s });
  },

  feedDetail: getStoredDetail(),
  setFeedDetail: (d) => {
    storeDetail(d);
    set({ feedDetail: d });
  },

  projectCli: getStoredProjectCli(),
  setProjectCli: (projectId, on) => {
    const next = { ...get().projectCli };
    if (on) next[projectId] = true;
    else delete next[projectId];        // absent = follow the studio-wide setting
    storeProjectCli(next);
    set({ projectCli: next });
  },

  missionFont: Math.min(26, Math.max(9, Number(localStorage.getItem("mc-font")) || 12)),
  setMissionFont: (n) => {
    const v = Math.min(26, Math.max(9, Math.round(n)));
    try { localStorage.setItem("mc-font", String(v)); } catch { /* ignore */ }
    set({ missionFont: v });
  },

  wsFont: Math.min(26, Math.max(9, Number(localStorage.getItem("ws-font")) || 13)),
  setWsFont: (n) => {
    const v = Math.min(26, Math.max(9, Math.round(n)));
    try { localStorage.setItem("ws-font", String(v)); } catch { /* ignore */ }
    set({ wsFont: v });
  },

  showStatusBar: localStorage.getItem("show-statusbar") !== "0",
  setShowStatusBar: (b) => {
    try { localStorage.setItem("show-statusbar", b ? "1" : "0"); } catch { /* ignore */ }
    set({ showStatusBar: b });
  },

  activeProject: "",
  setActiveProject: (id) => set((s) => (s.activeProject === id ? s : { activeProject: id })),

  statusItems: (() => {
    try {
      const v = JSON.parse(localStorage.getItem("status-items") || "null");
      if (Array.isArray(v) && v.every((x) => typeof x === "string")) {
        let known: string[] = STATUS_LEGACY;
        try {
          const k = JSON.parse(localStorage.getItem(KNOWN_KEY) || "null");
          if (Array.isArray(k) && k.every((x) => typeof x === "string")) known = k;
        } catch { /* an unreadable record means "assume the legacy set" */ }
        const fresh = STATUS_DEFAULT.filter((id) => !known.includes(id) && !v.includes(id));
        if (!fresh.length) return v;
        // Record what has now been offered, or the same ids would be re-appended on every
        // load and switching one off again would never stick.
        try {
          localStorage.setItem(KNOWN_KEY, JSON.stringify(STATUS_DEFAULT));
          localStorage.setItem("status-items", JSON.stringify([...v, ...fresh]));
        } catch { /* ignore */ }
        return [...v, ...fresh];
      }
    } catch { /* fall through to the default set */ }
    return STATUS_DEFAULT.slice();
  })(),
  statusSize: (["compact", "normal", "large"] as const)
    .find((x) => x === localStorage.getItem("status-size")) || "normal",
  setStatusItems: (ids) => {
    try {
      localStorage.setItem("status-items", JSON.stringify(ids));
      // Everything currently on offer has now been seen, so anything left out was left out
      // deliberately and must not be re-added by the merge above.
      localStorage.setItem(KNOWN_KEY, JSON.stringify(STATUS_DEFAULT));
    } catch { /* ignore */ }
    set({ statusItems: ids });
  },
  setStatusSize: (v) => {
    try { localStorage.setItem("status-size", v); } catch { /* ignore */ }
    set({ statusSize: v });
  },

  wsConnected: false,
  setWsConnected: (b) => set({ wsConnected: b }),

  stats: null,
  jobs: {},
  recentAssets: [],
  agentRuns: {},
  ccLive: {},

  toasts: [],
  toast: (message, kind = "info") => {
    const id = toastSeq++;
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }));
    setTimeout(() => get().dismissToast(id), 4200);
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  composerInsert: null,
  insertIntoComposer: (projectId, text) =>
    set((s) => ({ composerInsert: { projectId, text, nonce: (s.composerInsert?.nonce || 0) + 1 } })),

  diffNotes: [],
  addDiffNote: (n) => set((s) => ({ diffNotes: [...s.diffNotes, { ...n, id: noteSeq++ }] })),
  dropDiffNote: (id) => set((s) => ({ diffNotes: s.diffNotes.filter((n) => n.id !== id) })),
  clearDiffNotes: (projectId) =>
    set((s) => ({ diffNotes: s.diffNotes.filter((n) => n.projectId !== projectId) })),

  upsertJob: (job) => set((s) => ({ jobs: { ...s.jobs, [job.id]: job } })),

  inflight: {},
  pushInflight: (pid, m) => set((s) => ({ inflight: { ...s.inflight, [pid]: [...(s.inflight[pid] || []), m] } })),
  dropInflight: (pid, m) => set((s) => ({ inflight: { ...s.inflight, [pid]: (s.inflight[pid] || []).filter((x) => x !== m) } })),
  clearInflight: (pid) => set((s) => ({ inflight: { ...s.inflight, [pid]: [] } })),

  handleEvent: (ev) => {
    const s = get();
    switch (ev.type) {
      case "stats":
        if (ev.data) set({ stats: ev.data as SystemStats });
        break;
      case "job_update": {
        if (!ev.job_id) break;
        const prev = s.jobs[ev.job_id];
        const merged: Job = {
          ...(ev.job || prev || ({} as Job)),
          ...(prev || {}),
          ...(ev.job || {}),
          id: ev.job_id,
          status: (ev.status as any) ?? ev.job?.status ?? prev?.status ?? "running",
          progress: ev.progress ?? ev.job?.progress ?? prev?.progress ?? 0,
          step: ev.step ?? ev.job?.step ?? prev?.step ?? "",
          eta_seconds: ev.eta_seconds ?? ev.job?.eta_seconds ?? prev?.eta_seconds ?? null,
        };
        set({ jobs: { ...s.jobs, [ev.job_id]: merged } });
        break;
      }
      case "job_log": {
        if (!ev.job_id) break;
        const prev = s.jobs[ev.job_id];
        if (prev)
          set({
            jobs: {
              ...s.jobs,
              [ev.job_id]: { ...prev, logs: [...(prev.logs || []), ev.message || ""] },
            },
          });
        break;
      }
      case "asset_created": {
        if (ev.asset) {
          set({ recentAssets: [ev.asset, ...s.recentAssets].slice(0, 60) });
          get().toast(`New ${ev.asset.type}: ${ev.asset.name}`, "ok");
        }
        break;
      }
      case "agent_update": {
        const d = ev.data || {};
        if (d.run_id)
          set({ agentRuns: { ...s.agentRuns, [d.run_id]: { ...s.agentRuns[d.run_id], ...d } } });
        break;
      }
      case "cc_live": {
        const d = ev.data || {};
        if (d.project_id)
          set({ ccLive: { ...s.ccLive, [d.project_id]: { state: d, at: Date.now() } } });
        break;
      }
      default:
        break;
    }
  },
}));

/** Is this workspace drawn in the Claude Code console look? The studio-wide setting wins when it
 *  is on; otherwise the project decides for itself. Reactive, so the toggle in the chat box
 *  redraws the feed beside it in the same frame. */
export const useCliLook = (projectId?: string): boolean =>
  useStore((s) => s.skin === "cli" || (!!projectId && !!s.projectCli[projectId]));

// selector helpers
export const activeJobs = (jobs: Record<string, Job>) =>
  Object.values(jobs)
    .filter((j) => j.status === "queued" || j.status === "running")
    .sort((a, b) => b.created_at - a.created_at);
