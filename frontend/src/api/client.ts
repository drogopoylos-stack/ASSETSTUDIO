// Typed REST client. Uses relative URLs so it works via the Vite proxy (dev),
// the FastAPI-served build, and inside Electron (which loads the backend origin).
import { announceSettings } from "../components/engine/prefs";
import type {
  EntryInfo,
  AgentRun,
  Asset,
  BoostStatus,
  ChatProvider,
  ChatProviderInput,
  Job,
  JobRequest,
  KeyInfo,
  MissionOverview,
  PlanInfo,
  PluginInfo,
  ProviderInfo,
  ServiceStatus,
  StageType,
  SystemStats,
  WorkflowRun,
  WorkflowDetail,
  WorkflowAgentEvent,
  SubAgent,
  SubAgentClash,
  SubAgentDetail,
  EngineProject,
  EngineGen,
  EngineStats,
} from "../types";

// EVERY REQUEST GETS A DEADLINE, because `fetch` has no timeout of its own.
//
// A browser opens at most six connections per origin over HTTP/1.1. When one endpoint became
// slow enough that a poll started before the previous one had returned, six of them filled the
// pool — and every other request in the app, the conversation feed included, sat in the
// browser's queue behind them. A queued request never settles and never rejects, so a caller
// holding a "one load at a time" latch waited on a promise that could not resolve: the chat
// stayed empty, in every pane, with an idle CPU and a healthy backend, until Ctrl+R.
//
// A deadline turns that into a visible, recoverable error instead of a permanent freeze.
//
// GET is a read, and no read in this app has any business taking a minute and a half. POST is an
// action and some legitimately run long — installing the CLI allows 900s, cloning a repo takes
// as long as the network does — so those keep no deadline unless the caller sets one.
const GET_TIMEOUT_MS = 90_000;

type Init = RequestInit & { timeoutMs?: number };

// ---------------------------------------------------------------------------
// ONE REQUEST FOR ONE QUESTION, when several parts of the page ask in the same instant.
//
// Four components poll `/api/mission/live-status`, four poll `/api/mission/agents`, three poll
// `/api/engine/state` and three the project's `/context` — each for its own meter, each on its own
// timer. Measured across one workspace switch: 26 requests for the context of a single project and
// 24 for the agent list. Duplicates are not free even when each is fast, because a browser allows
// six connections per origin: they push the file tree and the conversation to the back of a queue
// the person is actually waiting on.
//
// THIS IS A POLL WINDOW, NOT A CACHE. 900ms is shorter than the fastest of those timers, so every
// meter still refreshes exactly as often as before — the window only collapses the pile-up.
// Anything not on this list is untouched: a send, a save and a one-off read all go straight out.
const SHARE_MS = 900;
const SHARE_RE = /^\/api\/(mission\/(live-status|agents)|engine\/state)$|\/context$/;
const _shared = new Map<string, { at: number; p: Promise<unknown> }>();

async function req<T>(path: string, init?: Init): Promise<T> {
  const method = ((init || {}).method || "GET").toUpperCase();
  if (method !== "GET" || !SHARE_RE.test(path)) return send<T>(path, init);
  const now = Date.now();
  const hit = _shared.get(path);
  if (hit && now - hit.at < SHARE_MS) return hit.p as Promise<T>;
  const p = send<T>(path, init);
  _shared.set(path, { at: now, p });
  // A FAILURE IS NOT SHARED past the callers already holding it. Keeping a rejected promise for
  // the rest of the window would make one blip look like a whole second of them.
  p.catch(() => { if (_shared.get(path)?.p === p) _shared.delete(path); });
  if (_shared.size > 32) for (const [k, v] of _shared) if (now - v.at > SHARE_MS) _shared.delete(k);
  return p;
}

async function send<T>(path: string, init?: Init): Promise<T> {
  const { timeoutMs, ...rest } = init || {};
  const method = (rest.method || "GET").toUpperCase();
  const ms = timeoutMs ?? (method === "GET" ? GET_TIMEOUT_MS : 0);
  const ctl = ms > 0 ? new AbortController() : null;
  const timer = ctl ? window.setTimeout(() => ctl.abort(), ms) : 0;
  let res: Response;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...(ctl ? { signal: ctl.signal } : {}),
      ...rest,
    });
  } catch (e: any) {
    // Name the failure. "AbortError" tells the user nothing; the timeout is the finding.
    if (e?.name === "AbortError") throw new Error(`timed out after ${Math.round(ms / 1000)}s`);
    throw e;
  } finally {
    if (timer) window.clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const fileUrl = (path: string) => `/api/file?path=${encodeURIComponent(path)}`;
export const assetFileUrl = (id: string) => `/api/assets/${id}/file`;
export const assetPreviewUrl = (id: string) => `/api/assets/${id}/preview`;

export const api = {
  // system
  systemStats: () => req<SystemStats>("/api/system/stats"),
  systemInfo: () => req<any>("/api/system/info"),
  /** Hand cached GPU memory back NOW — this process's torch caches AND ComfyUI's own models.
   *  ComfyUI runs in a separate process, so its UNET and 32B text encoder are the memory that
   *  stays high after a video even though the job is long finished (the backend's idle reaper
   *  reaches it too; this is the manual version of the same request). */
  freeGpu: () => req<{
    ok: boolean;
    /** VRAM this process's torch allocator gave back, in MB (null when torch is not loaded) */
    freed_mb?: number | null;
    comfyui?: {
      ok: boolean; freed?: boolean; skipped?: string; error?: string;
      before?: { vram_total: number; vram_free: number; ram_free: number };
      after?: { vram_total: number; vram_free: number; ram_free: number };
    };
  }>("/api/system/free-gpu", { method: "POST", body: "{}" }),
  health: () => req<any>("/api/health"),
  sysVersion: () => req<{ version: string; build: string;
    /** the Python on disk has changed since this process imported it */
    code_stale?: boolean; code_now?: string; uptime_s?: number;
    /** when THIS backend process booted — it changing means the process was replaced */
    started_at?: number;
    busy?: { sessions: number; turns: number; agents: number; projects: string[];
      /** background agents a restart would strand — they lose the channel they report through */
      stranded?: { project: string; agent: string }[];
      /** sessions with nobody holding their stdin - a restart really does end these. Only ever
       *  a session started by a backend older than the keeper, so the list empties itself. */
      unkeepered?: string[] } }>("/api/system/version", { timeoutMs: 12_000 }),
  sysRestart: (force = false) => req<{ ok: boolean; restarting: boolean; error?: string;
    /** refused because a restart would strand background agents; call again with force */
    needs_force?: boolean;
    busy?: { turns: number; agents: number } }>("/api/system/restart",
    { method: "POST", body: JSON.stringify({ force }) }),

  // in-app installer
  setupTasks: () => req<{ tasks: import("../types").SetupTask[] }>("/api/setup/tasks"),
  setupInstall: (id: string) => req<{ ok: boolean; started?: boolean; error?: string }>(`/api/setup/install/${encodeURIComponent(id)}`, { method: "POST" }),
  setupStatus: (id: string) => req<{ running: boolean; log: string; done: boolean; ok: boolean; restart: boolean }>(`/api/setup/status/${encodeURIComponent(id)}`),

  // providers
  providers: (stage?: StageType) =>
    req<ProviderInfo[]>(`/api/providers${stage ? `?stage=${stage}` : ""}`),
  provider: (id: string) => req<ProviderInfo>(`/api/providers/${id}`),
  reloadProviders: () => req<any>("/api/providers/reload", { method: "POST" }),
  loadErrors: () => req<Record<string, string>>("/api/providers/errors"),
  comfyCatalog: () => req<{ generators: import("../types").Generator[]; comfyui: boolean }>("/api/providers/comfy-catalog"),
  // `agent` picks WHOSE skill library: Claude's home, the DeepSeek Harness runtime's roots (which
  // the Studio mounts its shared library into), or nothing for Codex, which has no skill mechanism.
  skillsList: (projectId = "", agent = "") =>
    req<{ skills: import("../types").Skill[]; agent: string }>(
      `/api/skills?project_id=${encodeURIComponent(projectId)}&agent=${encodeURIComponent(agent)}`),
  skillToggle: (id: string, enabled: boolean, projectId = "", agent = "") =>
    req<{ ok: boolean; id: string; enabled: boolean; state: string; path?: string }>(
      `/api/skills/${encodeURIComponent(id)}`,
      { method: "POST", body: JSON.stringify({ enabled, project_id: projectId, agent }) }),
  listCustom: () => req<any[]>("/api/providers/custom/list"),
  upsertCustom: (spec: any) =>
    req<any>("/api/providers/custom", { method: "POST", body: JSON.stringify(spec) }),
  deleteCustom: (id: string) =>
    req<any>(`/api/providers/custom/${id}`, { method: "DELETE" }),

  // jobs
  submitJob: (r: JobRequest) =>
    req<Job>("/api/jobs", { method: "POST", body: JSON.stringify(r) }),
  jobs: (status?: string, limit = 100) =>
    req<Job[]>(`/api/jobs?limit=${limit}${status ? `&status=${status}` : ""}`),
  job: (id: string) => req<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => req<any>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  pipeline: (body: any) =>
    req<any>("/api/pipeline", { method: "POST", body: JSON.stringify(body) }),

  // catalog
  assets: (q: Partial<{ stage: string; target_game: string; tag: string; search: string; limit: number }> = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v != null && v !== "" && p.set(k, String(v)));
    return req<Asset[]>(`/api/assets?${p.toString()}`);
  },
  asset: (id: string) => req<Asset>(`/api/assets/${id}`),
  patchAsset: (id: string, patch: any) =>
    req<Asset>(`/api/assets/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteAsset: (id: string, deleteFile = false) =>
    req<any>(`/api/assets/${id}?delete_file=${deleteFile}`, { method: "DELETE" }),
  catalogSummary: () => req<any>("/api/catalog/summary"),

  // settings + keys
  settings: () => req<any>("/api/settings"),
  /** Every note a session is told, with its switch and what it costs per turn. Newer than some
   *  backends: the caller falls back to its own list on a 404. */
  agentNotes: () => req<{ notes: { key: string; label: string; on: boolean; default: boolean; tokens: number; needs?: string }[]; total: number }>("/api/settings/agent-notes"),
  // Saved, then announced: the Engine window is a window of its own, and it follows a change made
  // here without being reopened.
  updateSettings: (patch: any) =>
    req<any>("/api/settings", { method: "PUT", body: JSON.stringify({ patch }) })
      .then((r) => { announceSettings(patch); return r; }),
  // BOOST — the local economy behind the button beside the prompt. Its own endpoint rather than a
  // plain settings key, because switching it on also applies a profile of saving switches and
  // switching it off puts back exactly what that profile replaced; a settings write cannot do
  // either, and a button that only flipped a boolean would be a feature that did nothing.
  boostStatus: () => req<BoostStatus>("/api/boost"),
  boostSet: (on: boolean) =>
    req<BoostStatus>("/api/boost", { method: "PUT", body: JSON.stringify({ on }) })
      .then((r) => { announceSettings({ boost: on }); return r; }),
  /** What BOOST would do to THIS text, before anything is sent — so the rewrite is never
   *  something that happened to the user's words without them being able to see it. */
  boostPreview: (text: string) =>
    req<{ text: string; saved_chars: number; saved_tokens: number; changed: boolean;
          before_chars: number; after_chars: number; reasons: Record<string, number> }>(
      "/api/boost/preview", { method: "POST", body: JSON.stringify({ text }) }),
  boostForget: () => req<BoostStatus>("/api/boost/forget", { method: "POST" }),
  graphifyStatus: () =>
    req<{ available: boolean; installing: boolean; error: string; venv: string }>("/api/graphify/status"),
  webStatus: () =>
    req<{ available: boolean; installing: boolean; error: string; venv: string }>("/api/web/status"),
  graphifyPrebuild: (path: string) =>
    req<{ scheduled: boolean }>("/api/graphify/prebuild", { method: "POST", body: JSON.stringify({ path }) }),
  wsOpenBrowser: (path: string) =>
    req<{ ok: boolean; url: string; browser: string; error?: string; entry?: string;
          live?: boolean; stale?: boolean; dev_script?: string }>("/api/workspace/open-browser",
      { method: "POST", body: JSON.stringify({ path }) }),
  wsPreviewUrl: (path: string) =>
    req<{ url: string }>(`/api/workspace/preview-url?path=${encodeURIComponent(path)}`),
  // every page in a project that could BE the app, best first — the localhost menu
  wsEntries: (path: string) =>
    req<{ entries: EntryInfo[] }>(`/api/workspace/entries?path=${encodeURIComponent(path)}`),
  wsOpenUrl: (url: string) =>
    req<{ ok: boolean; url: string; browser: string; error?: string }>("/api/workspace/open-url",
      { method: "POST", body: JSON.stringify({ url }) }),
  wsStartDev: (path: string) =>
    req<{ ok: boolean; url?: string; script?: string; error?: string; log?: string[] }>(
      "/api/workspace/dev-server", { method: "POST", body: JSON.stringify({ path }) }),
  // output tokens per second: live for the current turn + every finished turn by model/effort
  missionSpeed: (projectId: string, allProjects = false) =>
    req<{
      live: { working: boolean; tps: number; tokens: number; gen_s: number; elapsed: number; model: string };
      by_model: { model: string; effort: string; turns: number; tokens: number; gen_s: number; tps: number; best: number }[];
      recent: { ts: number; model_short: string; effort: string; tokens: number; gen_s: number; wall_s: number; tps: number; wall_tps: number }[];
      turns: number;
    }>(`/api/mission/projects/${encodeURIComponent(projectId)}/speed${allProjects ? "?all_projects=true" : ""}`),
  missionTodos: (projectId: string) =>
    // `finished` = every phase ticked, so the panel can read as a record rather than a plan.
    // `runs` = how many sets have been written, so it knows whether there is history to offer.
    req<{ todos: { content: string; status: string; active_form: string; detail?: string }[]; ts?: string; done: number; total: number; source?: string; earlier?: number; finished?: boolean; runs?: number }>(
      `/api/mission/projects/${encodeURIComponent(projectId)}/todos`),
  lastAgent: (path: string) =>
    req<{ agent: string; at: number }>(`/api/mission/last-agent?path=${encodeURIComponent(path)}`),
  reviewStatus: () =>
    req<{ ok: boolean; error: string; running: boolean; idle_s: number;
          gpu: boolean; quality: string }>("/api/review/status"),
  // ---- the engine window -------------------------------------------------
  // Read-only. The window is a viewer over what the agents are already doing; it never starts a
  // browser or a dev server, because opening a panel must not cost a gigabyte.
  engineState: () =>
    req<{ ok: boolean; enabled: boolean; available: boolean; why: string; open_games: number;
          recent_generations: number; busy: boolean;
          /** How many tabs an agent called in the last five minutes (a newer backend only). */
          in_use?: number;
          /** `idle_s` is seconds since an agent last called the tab; `active` is whether that is
           *  inside the last five minutes (a newer backend only — see components/engine/inUse). */
          tabs: { project: string; url: string; engine: string; device: string; age_s?: number;
                  idle_s?: number; active?: boolean }[];
          /** The resolution the game is judged at — shared with the agents via cc_engine_view. */
          view: { w: number; h: number; label: string };
          latest: { id: string; label: string; project_name: string; engine: string; ts: number;
                    sheet: string; ok: boolean } | null;
          /** Who is working right now. A generation says what was MADE; this says what is being
           *  made — including by an agent that never calls the forge at all, which is why the
           *  window looked idle while one was modelling in Blender. */
          agents?: { agent_id: string; project: string; description: string; model: string;
                     moving: boolean; idle_s: number; tools: number; tokens: number;
                     phase: string; tool: string; detail: string }[] }>("/api/engine/state"),
  engineProjects: () =>
    req<{ ok: boolean; games: number; cached: boolean;
          projects: EngineProject[] }>("/api/engine/projects"),
  engineProject: (path: string) =>
    req<EngineProject & { ok: boolean; dev_url: string; dev_script: string; tab?: unknown }>(
      `/api/engine/project?path=${encodeURIComponent(path)}`),
  engineHistory: (project = "", limit = 80, kind = "", extra: { type?: string; subject?: string; q?: string } = {}) =>
    req<{ ok: boolean; total: number; items: EngineGen[];
          by_project: Record<string, number>; by_day: Record<string, number>;
          by_kind: Record<string, number>; by_type?: Record<string, number>; by_subject?: Record<string, number> }>(
      `/api/engine/history?project=${encodeURIComponent(project)}&limit=${limit}&kind=${kind}`
      + `&type=${encodeURIComponent(extra.type || "")}&subject=${encodeURIComponent(extra.subject || "")}&q=${encodeURIComponent(extra.q || "")}`),
  /** A person's word on what a generation depicts. It sticks; no later guess overwrites it. */
  engineTag: (id: string, subject: string, tags: string[]) =>
    req<{ ok: boolean; error?: string } & Partial<EngineGen>>("/api/engine/tag", { method: "POST", body: JSON.stringify({ id, subject, tags }) }),
  /** What a project already has: builders and spec tables in its code, model files, images. */
  /** EVERY game inside one workspace, each with its own dev server. A workspace here holds
   *  three, and only the game that owns a file can serve it. */
  engineGames: (project: string, fresh = false) =>
    req<{ ok: boolean; error?: string; games: import("../types").EngineGame[] }>(
      `/api/engine/games?project=${encodeURIComponent(project)}${fresh ? "&fresh=1" : ""}`),
  engineAssets: (project: string,
                 extra: { type?: string; subject?: string; q?: string; fresh?: boolean; root?: string } = {}) =>
    req<{ ok: boolean; error?: string; total: number; items: import("../types").LibraryAsset[];
          by_type: Record<string, number>; by_subject: Record<string, number>; files_seen: number;
          truncated: boolean; games?: string[] }>(
      `/api/engine/assets?project=${encodeURIComponent(project)}&type=${encodeURIComponent(extra.type || "")}`
      + `&subject=${encodeURIComponent(extra.subject || "")}&q=${encodeURIComponent(extra.q || "")}`
      + `&root=${encodeURIComponent(extra.root || "")}${extra.fresh ? "&fresh=1" : ""}`),
  engineGeneration: (id: string) =>
    req<{ ok: boolean; error?: string; id: string; code: string; engine: string; project: string;
          label: string; ts: number; sheet: string; views: string[]; stats: EngineStats }>(
      `/api/engine/generation?id=${encodeURIComponent(id)}`),
  // Plain URLs, not fetches: an <img src> and a dynamic import() want a string.
  /** Render a picture of each asset by calling the project's own builders. Batched and cached. */
  engineThumbs: (project: string, ids: string[], size = 288) =>
    req<{ ok: boolean; thumbs: Record<string, string>; rendered: number; error?: string;
          errors?: Record<string, string> }>(
      "/api/engine/thumbs", { method: "POST", body: JSON.stringify({ project, ids, size }) }),
  /** A glTF loader bound to the project's own three, for projects that vendor three as one file. */
  /** Given `three` — the module URL the viewport actually loaded — the loader is bound to THAT,
   *  which is the only rule that cannot disagree with the scene. Without it, to the project's own
   *  three or the Studio's. */
  engineLoaderUrl: (project: string, three = "") =>
    `/api/engine/loader?project=${encodeURIComponent(project)}&name=GLTFLoader.js`
    + (three ? `&three=${encodeURIComponent(three)}` : ""),
  /** A model file with its compression already taken out, so anything can open it. */
  engineModelUrl: (project: string, file: string) =>
    `/api/engine/model?project=${encodeURIComponent(project)}&file=${encodeURIComponent(file)}`,
  /** The same file addressed by its ABSOLUTE path, for the times the project is not known — the
   *  file picker and the `?edit=` deep link both hand over a path and nothing else, and the
   *  window may be showing every project at once. Same decompression, same headers. */
  engineModelPath: (path: string) =>
    `/api/engine/model?project=&path=${encodeURIComponent(path)}`,
  engineSheetUrl: (path: string) => `/api/engine/sheet?path=${encodeURIComponent(path)}`,
  engineModuleUrl: (project: string, engineKind = "") =>
    `/api/engine/module?project=${encodeURIComponent(project)}&engine_kind=${encodeURIComponent(engineKind)}`,
  // NOT `liveStatus` — that name is already taken by the Claude-session live state below, and a
  // duplicate key in this object silently shadows one of them.
  liveGameStatus: () =>
    req<{ ok: boolean; enabled: boolean; available: boolean; why: string;
          tabs: { project: string; url: string; engine: string; device: string;
                  served_by: string; age_s: number; idle_s: number }[] }>("/api/live/status"),
  /** Put the game in the shared headless tab (starting its dev server if need be) and say what
   *  engine was found and whether its scene is reachable. */
  liveOpen: (project: string) =>
    req<{ ok?: boolean; engine: string; reachable: boolean; at?: string; tag?: string; hint?: string; error?: string }>(
      "/api/live/open", { method: "POST", body: JSON.stringify({ project }) }),
  /** What the forge page is holding RIGHT NOW: the code that made the scene currently in it, a
   *  signature for that code, and the camera the agent last looked from.
   *
   *  This is what makes watching an agent work live rather than after the fact. A generation is
   *  a finished run; this is the bench, and it changes while the agent is still on it. */
  liveBench: (project = "") =>
    req<{
      ok: boolean; open: boolean; sig: string; code: string; label?: string; ts?: number;
      project?: string; engine?: string;
      /** The GLB files on the bench since the last clear; `v` is the file's time. */
      models?: { name: string; path: string; v?: number }[];
      at?: { view?: string; az?: number; el?: number; zoom?: number; framed?: string };
      stats?: { triangles?: number; meshes?: number; materials?: number };
      error?: string;
    }>("/api/live/bench?project=" + encodeURIComponent(project)),
  /** One frame of the live page, written to disk. `path` is what `fileUrl` then serves. */
  liveShot: (project: string) =>
    req<{ ok: boolean; path?: string; bytes?: number; error?: string }>(
      "/api/live/shot", { method: "POST", body: JSON.stringify({ project }), timeoutMs: 30_000 }),
  /** Run JavaScript in the live game and bring the value back, flattened. */
  liveEval: (project: string, js: string, depth = 6) =>
    req<{ ok: boolean; value?: any; error?: string; note?: string; form?: string }>(
      "/api/live/eval", { method: "POST", body: JSON.stringify({ project, js, depth }) }),
  /** Keys, clicks, taps and waits into the live game, in order. `nx`/`ny` are 0..1 of the view. */
  liveInput: (project: string, events: Array<Record<string, unknown>>) =>
    req<{ ok: boolean; sent?: number; error?: string }>(
      "/api/live/input", { method: "POST", body: JSON.stringify({ project, events }), timeoutMs: 30_000 }),
  subagents: (projectId: string, files = false) =>
    req<{ agents: SubAgent[]; running: number; open: number; tokens: number; cache_read?: number }>(
      `/api/mission/projects/${encodeURIComponent(projectId)}/subagents${files ? "?files=true" : ""}`),
  subagent: (projectId: string, agentId: string) =>
    req<SubAgentDetail>(
      `/api/mission/projects/${encodeURIComponent(projectId)}/subagents/${encodeURIComponent(agentId)}`),
  subagentClashes: (projectId: string, root: string) =>
    req<{ collisions: SubAgentClash[] }>(
      `/api/mission/projects/${encodeURIComponent(projectId)}/subagents/collisions?root=${encodeURIComponent(root)}`),
  reviewBrowsers: () =>
    req<{
      ours: null | {
        pid: number; port: number; profile: string; gpu: boolean;
        age_s: number; idle_s: number; closes_in_s: number;
        tabs: { session: string; url: string; idle_s: number }[];
        who: null | { project: string; project_id: string; label: string; mode: string;
                      subject: string; url: string; since: number; renders: number };
      };
      strays: { pid: number; name: string; age_s: number; profile: string;
                mb: number; studio: boolean; kind: string }[];
      idle_kill_s: number;
    }>("/api/review/browsers"),
  reviewKill: (pids: number[]) =>
    req<{ ok: boolean; killed: number; profiles_removed: number }>(
      "/api/review/browsers/kill", { method: "POST", body: JSON.stringify({ pids }) }),
  reviewStop: () => req<{ ok: boolean }>("/api/review/stop", { method: "POST" }),
  outputStyles: () =>
    req<{ active: string; styles: { id: string; name: string; description: string; bundled: boolean; words: number }[] }>("/api/output-styles"),
  autolearnStatus: () =>
    req<{ enabled: boolean; pending: number; skills_written: number; scanning: boolean; distilling: boolean;
          llm: { available: boolean; installing: boolean; error: string; model_pct: number; step: string } }>("/api/autolearn/status"),
  autolearnRun: () => req<{ started: boolean }>("/api/autolearn/run", { method: "POST" }),
  wsRenameRoot: (path: string, name: string) =>
    req<{ ok: boolean; id: string; name: string; path: string; old_path: string; migrated_sessions: number }>(
      "/api/workspace/rename-root", { method: "POST", body: JSON.stringify({ path, name }) }),
  claudeLimits: () =>
    req<{ ok: boolean; stale?: boolean; error?: string;
          limits: { kind: string; label: string; model: string; percent: number; resets_at: string }[] }>(
      "/api/auth/claude/limits"),
  // voice input (local Whisper) — status/enable are JSON; transcribe is multipart (raw fetch).
  voiceStatus: () =>
    req<{ installed: boolean; installing: boolean; install_error: string; ready: boolean; loading: boolean; device: string; model: string; worker_error: string; task: string;
      engine?: string; groq_model?: string; groq_key?: boolean }>("/api/voice/status"),
  voiceEnable: () => req<any>("/api/voice/enable", { method: "POST" }),
  voiceTranscribe: async (blob: Blob, task: string) => {
    const fd = new FormData();
    fd.append("audio", blob, "audio.webm");
    fd.append("task", task);
    const res = await fetch("/api/voice/transcribe", { method: "POST", body: fd });
    return res.json() as Promise<{ text?: string; language?: string; error?: string }>;
  },
  keys: () => req<{ backend: string; keys: KeyInfo[] }>("/api/keys"),
  setKey: (name: string, value: string) =>
    req<any>(`/api/keys/${name}`, { method: "PUT", body: JSON.stringify({ value }) }),
  deleteKey: (name: string) => req<any>(`/api/keys/${name}`, { method: "DELETE" }),

  // plugins — every optional tab / background helper, on or off
  plugins: () => req<{ plugins: PluginInfo[] }>("/api/plugins"),
  setPlugins: (patch: Record<string, boolean>) =>
    req<{ plugins: PluginInfo[]; applied: any }>("/api/plugins", { method: "PUT", body: JSON.stringify({ patch }) }),

  // chat providers — any model, in the workspace, like Claude
  chatProviders: () =>
    req<{ providers: ChatProvider[]; presets: ChatProvider[]; protocols: string[] }>("/api/chat-providers"),
  createChatProvider: (p: ChatProviderInput) =>
    req<ChatProvider>("/api/chat-providers", { method: "POST", body: JSON.stringify(p) }),
  updateChatProvider: (id: string, p: ChatProviderInput) =>
    req<ChatProvider>(`/api/chat-providers/${id}`, { method: "PUT", body: JSON.stringify(p) }),
  deleteChatProvider: (id: string) => req<any>(`/api/chat-providers/${id}`, { method: "DELETE" }),
  chatProviderModels: (id: string) =>
    req<{ ok: boolean; models: string[]; error?: string }>(`/api/chat-providers/${id}/models`),
  testChatProvider: (id: string) =>
    req<{ ok: boolean; model?: string; reply?: string; error?: string }>(
      `/api/chat-providers/${id}/test`, { method: "POST" }),

  // plans written in plan mode
  plans: () => req<{ plans: PlanInfo[] }>("/api/plans"),
  planFile: (file: string) => req<{ file: string; text: string }>(`/api/plans/file?file=${encodeURIComponent(file)}`),
  deletePlan: (file: string) => req<any>("/api/plans/delete", { method: "POST", body: JSON.stringify({ file }) }),

  // local servers (model backends the studio can launch)
  services: () => req<ServiceStatus[]>("/api/services"),
  serviceForProvider: (providerId: string) =>
    req<{ service: ServiceStatus | null }>(`/api/services/for-provider/${providerId}`),
  startService: (id: string, wait = true, timeout = 90) =>
    req<ServiceStatus>(`/api/services/${id}/start?wait=${wait}&timeout=${timeout}`,
      { method: "POST", timeoutMs: (timeout + 30) * 1000 }),
  stopService: (id: string) => req<ServiceStatus>(`/api/services/${id}/stop`, { method: "POST" }),
  startAllServices: (wait = true) =>
    req<ServiceStatus[]>(`/api/services/start-all?wait=${wait}`, { method: "POST" }),
  serviceLogs: (id: string) => req<{ id: string; log: string }>(`/api/services/${id}/logs`),
  updateService: (id: string, patch: any) =>
    req<ServiceStatus>(`/api/services/${id}`, { method: "PUT", body: JSON.stringify(patch) }),

  // workspace (file explorer / editor)
  // Earlier phase sets. The agent rewrites its phase file whole, so without this the steps of a
  // finished build vanish the moment the next set is written.
  missionPhaseHistory: (projectId: string) =>
    req<{ runs: { started: number; ended: number; total: number; done: number;
                  phases: { content: string; status: string; detail?: string }[] }[] }>(
      `/api/mission/projects/${encodeURIComponent(projectId)}/phase-history`),

  wsRoots: () => req<import("../types").WorkspaceRoot[]>("/api/workspace/roots"),

  // ---- source control ----------------------------------------------------------------------
  // Status is a GET; everything that changes something is a POST you pressed. Push in particular
  // never rides along with anything else — it leaves the machine.
  gitStatus: (path: string) =>
    req<import("../types").GitStatus>(`/api/git/status?path=${encodeURIComponent(path)}`),
  gitGithub: () =>
    req<{ ok: boolean; installed: boolean; authenticated?: boolean; login?: string; error?: string }>(
      "/api/git/github"),
  gitCommit: (body: { path: string; message: string }) =>
    req<{ ok: boolean; error?: string; hash?: string; files?: number }>(
      "/api/git/commit", { method: "POST", body: JSON.stringify(body) }),
  gitPush: (path: string) =>
    req<{ ok: boolean; error?: string; message?: string; no_remote?: boolean }>(
      "/api/git/push", { method: "POST", body: JSON.stringify({ path }) }),
  gitPull: (path: string) =>
    req<{ ok: boolean; error?: string; message?: string }>(
      "/api/git/pull", { method: "POST", body: JSON.stringify({ path }) }),
  gitCreateRepo: (body: { path: string; name: string; private: boolean; push: boolean }) =>
    req<{ ok: boolean; error?: string; remote?: string; pushed?: boolean }>(
      "/api/git/github/create", { method: "POST", body: JSON.stringify(body) }),

  // ---- worktrees: several branches of one repo, open side by side, one agent each ----------
  // A worktree is a folder, and a folder is a workspace — so each one gets its own session,
  // transcript and agent with no extra plumbing. `map` is what lets the rail nest them.
  wtMap: () => req<{ roots: import("../types").WorkspaceRoot[] }>("/api/worktrees/map"),
  wtList: (path: string) =>
    req<{ ok: boolean; error?: string; worktrees: import("../types").Worktree[] }>(
      `/api/worktrees/list?path=${encodeURIComponent(path)}`),
  wtBranches: (path: string) =>
    req<{ ok: boolean; branches: string[]; taken: string[]; current: string }>(
      `/api/worktrees/branches?path=${encodeURIComponent(path)}`),
  wtSuggest: (path: string, name: string) =>
    req<{ dest: string }>(
      `/api/worktrees/suggest?path=${encodeURIComponent(path)}&name=${encodeURIComponent(name)}`),
  wtCreate: (body: { path: string; name: string; branch?: string; base?: string; dest?: string; existing_branch?: boolean }) =>
    req<{ ok: boolean; error?: string; path?: string; branch?: string; name?: string; root_error?: string }>(
      "/api/worktrees/create", { method: "POST", body: JSON.stringify(body) }),
  wtRemove: (body: { path: string; force?: boolean; delete_branch?: boolean }) =>
    req<{ ok: boolean; error?: string; dirty?: boolean }>(
      "/api/worktrees/remove", { method: "POST", body: JSON.stringify(body) }),

  // Terminals for the other coding agents. Separate from the chat on purpose: these CLIs draw
  // their own full-screen interface, so they get a pty and render themselves.
  terminalAgents: () => req<{ ok: boolean; error: string; agents: any[] }>("/api/terminal/agents"),
  terminalLaunch: (body: { agent: string; cwd: string; cols: number; rows: number; install: boolean; model?: string; effort?: string }) =>
    req<{ ok: boolean; id: string; label: string; error?: string; install?: string }>(
      "/api/terminal/launch", { method: "POST", body: JSON.stringify(body) }),
  // Hand a project's CONVERSATION to a real Claude Code terminal. Not a new chat: the backend
  // stops the streaming session and resumes the same session id, on the same transcript, so
  // closing the terminal hands everything typed there back to the feed.
  terminalClaude: (body: { project_id: string; cols: number; rows: number; model: string; permission_mode: string; effort: string; session: string; fresh: boolean; cwd: string }) =>
    req<{ ok: boolean; id?: string; error?: string; session_id?: string; cwd?: string }>(
      "/api/terminal/claude", { method: "POST", body: JSON.stringify(body) }),
  terminalClose: (tid: string) =>
    req<{ ok: boolean }>(`/api/terminal/${encodeURIComponent(tid)}`, { method: "DELETE" }),
  wsTree: (path: string) =>
    req<{ path: string; entries: import("../types").TreeEntry[] }>(`/api/workspace/tree?path=${encodeURIComponent(path)}`),
  wsFile: (path: string) => req<import("../types").FileResult>(`/api/workspace/file?path=${encodeURIComponent(path)}`),
  wsStat: (path: string) => req<{ path: string; mtime: number; size: number }>(`/api/workspace/stat?path=${encodeURIComponent(path)}`),
  wsChanges: (root: string, since: number) =>
    req<{ version: number; paths: string[] }>(`/api/workspace/changes?root=${encodeURIComponent(root)}&since=${since}`),
  wsSearch: (root: string, q: string, limit = 30) =>
    req<{ root: string; entries: import("../types").SearchEntry[] }>(`/api/workspace/search?root=${encodeURIComponent(root)}&q=${encodeURIComponent(q)}&limit=${limit}`),
  // `root` is the project the path was written in. Pass it whenever the path may be relative or a
  // bare name — otherwise a name that several open projects share resolves to the wrong one.
  wsRaw: (path: string, root = "") =>
    `/api/workspace/raw?path=${encodeURIComponent(path)}${root ? `&root=${encodeURIComponent(root)}` : ""}`,
  // .obj/.stl/.ply converted to GLB server-side so the 3D view can render them
  wsModel: (path: string) => `/api/workspace/model?path=${encodeURIComponent(path)}`,
  wsWrite: (path: string, text: string) =>
    req<{ path: string; size: number; mtime: number; ok: boolean }>("/api/workspace/file", { method: "PUT", body: JSON.stringify({ path, text }) }),
  wsMkdir: (path: string) => req<any>("/api/workspace/mkdir", { method: "POST", body: JSON.stringify({ path }) }),
  wsNewFile: (path: string) => req<any>("/api/workspace/newfile", { method: "POST", body: JSON.stringify({ path }) }),
  wsDelete: (path: string) => req<any>("/api/workspace/delete", { method: "POST", body: JSON.stringify({ path }) }),
  wsNewProject: (parent: string, name: string) =>
    req<import("../types").WorkspaceRoot & { ok: boolean }>("/api/workspace/new-project", { method: "POST", body: JSON.stringify({ parent, name }) }),
  /** Folders on this machine that look like projects and are not open yet; `add` opens them all. */
  wsDiscover: (add = false) =>
    req<{ found: { path: string; name: string }[]; added: number }>("/api/workspace/discover", { method: "POST", body: JSON.stringify({ add }) }),
  wsAddRoot: (path: string) =>
    req<import("../types").WorkspaceRoot & { ok: boolean }>("/api/workspace/add-root", { method: "POST", body: JSON.stringify({ path }) }),
  wsRemoveRoot: (path: string) =>
    req<any>("/api/workspace/remove-root", { method: "POST", body: JSON.stringify({ path }) }),
  wsUploadImage: async (projectPath: string, blob: Blob, filename = "paste.png") => {
    const fd = new FormData();
    fd.append("project_path", projectPath);
    fd.append("file", blob, filename);
    const res = await fetch("/api/workspace/upload-image", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed (${res.status})`);
    return res.json() as Promise<{ path: string; ok: boolean }>;
  },
  wsRename: (path: string, name: string) =>
    req<{ ok: boolean; path: string; name: string }>("/api/workspace/rename", { method: "POST", body: JSON.stringify({ path, name }) }),
  wsMove: (path: string, destDir: string) =>
    req<{ ok: boolean; path: string }>("/api/workspace/move", { method: "POST", body: JSON.stringify({ path, dest_dir: destDir }) }),
  wsCopy: (path: string, destDir: string) =>
    req<{ ok: boolean; path: string; name: string }>("/api/workspace/copy", { method: "POST", body: JSON.stringify({ path, dest_dir: destDir }) }),
  // where a chat link actually points (for "copy full path") — same resolver as the thumbnail
  wsResolve: (path: string, root = "") =>
    req<{ path: string }>(`/api/workspace/resolve?path=${encodeURIComponent(path)}${root ? `&root=${encodeURIComponent(root)}` : ""}`),
  wsReveal: (path: string, root = "") =>
    req<{ ok: boolean }>("/api/workspace/reveal", { method: "POST", body: JSON.stringify({ path, root }) }),
  wsRun: (path: string) =>
    req<{ ok: boolean; action?: string; url?: string; ext?: string }>("/api/workspace/run", { method: "POST", body: JSON.stringify({ path }) }),
  wsOpen: (path: string, root = "") =>
    req<{ ok: boolean; path?: string }>("/api/workspace/open", { method: "POST", body: JSON.stringify({ path, root }) }),
  wsImport: (src: string, destDir: string) =>
    req<{ ok: boolean; path: string; name: string }>("/api/workspace/import", { method: "POST", body: JSON.stringify({ src, dest_dir: destDir }) }),
  wsUploadFile: async (destDir: string, file: File) => {
    const fd = new FormData();
    fd.append("dest_dir", destDir);
    fd.append("file", file, file.name);
    const res = await fetch("/api/workspace/upload-file", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed (${res.status})`);
    return res.json() as Promise<{ ok: boolean; path: string; name: string }>;
  },
  uploadInput: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await fetch("/api/uploads/image", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed (${res.status})`);
    return res.json() as Promise<{ ok: boolean; path: string; name: string }>;
  },

  // mission control (Claude Code projects)
  mission: (force = false) => req<MissionOverview>(`/api/mission/overview${force ? "?force=true" : ""}`),
  missionOpen: (path: string, target: "vscode" | "folder" = "vscode") =>
    req<any>("/api/mission/open", { method: "POST", body: JSON.stringify({ path, target }) }),
  missionFeed: (projectId: string, limit = 50, session = "", kinds = "") =>
    req<import("../types").FeedResponse>(
      `/api/mission/projects/${encodeURIComponent(projectId)}/feed?limit=${limit}${session ? `&session=${encodeURIComponent(session)}` : ""}${kinds ? `&kinds=${encodeURIComponent(kinds)}` : ""}`,
      { timeoutMs: 30_000 },
    ),
  missionSessions: (projectId: string) =>
    req<{ id: string; sessions: import("../types").Session[] }>(`/api/mission/projects/${encodeURIComponent(projectId)}/sessions`),
  missionUsage: (provider = "auto", force = false) =>
    req<import("../types").UsageSummary>(`/api/mission/usage?provider=${encodeURIComponent(provider)}${force ? "&force=true" : ""}`,
      { timeoutMs: 60_000 }),
  usageAccounts: () => req<{ accounts: import("../types").UsageAccount[] }>("/api/mission/usage/accounts", { timeoutMs: 60_000 }),
  missionContext: (projectId: string) =>
    req<import("../types").ContextInfo>(`/api/mission/projects/${encodeURIComponent(projectId)}/context`, { timeoutMs: 12_000 }),
  claudeStatus: () => req<{ available: boolean; path: string; models: string[]; modes: string[]; efforts: string[]; agents: import("../types").AgentInfo[];
    /** models the CLI offers that this account was refused on a real turn, keyed by model id */
    model_access?: Record<string, { reason: string; at: number }> }>("/api/mission/claude"),
  agentSpend: () => req<{ total: number; today: number; month: number; turns: number; projects: number;
    by_model: Record<string, number>; top: { project: string; cost: number; turns: number }[];
    days: Record<string, number>; cap?: { limit: number; spent: number; enabled: boolean; over: boolean };
    budgets?: { key: string; label: string; used: number; limit: number; percent: number }[] }>("/api/mission/spend"),
  // What the money BOUGHT, per tool and per kind of round trip. Prices each request from the
  // transcript's own token counts, so it is arithmetic rather than the CLI's per-process running
  // total — see cost_breakdown.py. Parsing a long transcript takes seconds; poll it slowly.
  spendBreakdown: (project: string) => req<{ ok: boolean; note?: string; round_trips: number; total: number;
    by_tool: { tool: string; calls: number; cost: number; percent: number; per_call: number }[];
    by_kind: { kind: string; messages: number; cost: number; percent: number }[];
    by_model: { model: string; messages: number; cost: number; basis: string }[];
    context: { files?: number; messages: number;
               spread: { label: string; messages: number; percent: number }[] } }>(
    `/api/mission/spend/breakdown?project=${encodeURIComponent(project)}`),
  claudeVersion: () => req<{ path: string; version: string; latest: string; next: string; newest: string;
    update_available: boolean; on_channel: string; checked_at: number; checking: boolean; busy: string[] }>("/api/mission/claude/version"),
  claudeInstall: (target: string) => req<{ ok: boolean; target: string; version: string; output: string }>(
    "/api/mission/claude/install", { method: "POST", body: JSON.stringify({ target }) }),
  missionAgents: () => req<{ agents: import("../types").AgentInfo[] }>("/api/mission/agents"),
  // Codex: each call may wait for the Codex app-server to start (about a second, once)
  codexStatus: (fresh = false) =>
    req<import("../types").CodexStatus>(`/api/codex/status${fresh ? "?fresh=true" : ""}`, { timeoutMs: 60_000 }),
  codexRecheck: () =>
    req<import("../types").CodexStatus>("/api/codex/recheck", { method: "POST", timeoutMs: 60_000 }),
  codexLogin: (kind: "chatgpt" | "device" | "apikey", api_key = "") =>
    req<{ ok: boolean; error?: string; opened?: boolean; login?: import("../types").CodexStatus["login"];
      account?: { signed_in: boolean; auth: string; email: string; plan: string } }>(
      "/api/codex/login", { method: "POST", body: JSON.stringify({ kind, api_key }), timeoutMs: 60_000 }),
  codexLoginCancel: () => req<{ ok: boolean }>("/api/codex/login/cancel", { method: "POST", timeoutMs: 30_000 }),
  codexLogout: () => req<{ ok: boolean; error?: string }>("/api/codex/logout", { method: "POST", timeoutMs: 30_000 }),
  codexModels: (refresh = false) =>
    req<{ models: import("../types").CodexModel[]; default: string; error: string }>(
      `/api/codex/models${refresh ? "?refresh=true" : ""}`, { timeoutMs: 60_000 }),
  codexSandbox: (cwd = "") =>
    req<{ ok: boolean; started?: boolean; error?: string }>(
      "/api/codex/sandbox", { method: "POST", body: JSON.stringify({ cwd }), timeoutMs: 60_000 }),
  codexInstall: (update = true) =>
    req<{ ok: boolean; running?: boolean; error?: string }>(
      "/api/codex/install", { method: "POST", body: JSON.stringify({ update }), timeoutMs: 30_000 }),
  missionSlash: (projectId = "") => req<{ commands: import("../types").SlashCommand[] }>(`/api/mission/slash-commands${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  sessionSend: (projectId: string, body: { message: string; model: string; permission_mode: string; fork: boolean; images?: string[]; effort?: string; thinking?: boolean; agent?: string; new_session?: boolean; session?: string; path?: string; companions?: { id: string; model?: string; effort?: string; permission_mode?: string }[] }) =>
    req<any>(`/api/mission/projects/${encodeURIComponent(projectId)}/send`, { method: "POST", body: JSON.stringify(body) }),
  sessionSending: (projectId: string) =>
    req<{ sending: boolean; pending?: number }>(`/api/mission/projects/${encodeURIComponent(projectId)}/sending`),
  liveStatus: () => req<{ statuses: Record<string, boolean>;
    agents?: Record<string, string>;
    /** subagents in flight per project: writing right now PLUS dispatched and quiet */
    running_agents?: Record<string, number>;
    /** of those, the ones that have not written for 90s. A background agent sits quiet inside a
     *  long tool call, so this is normal — it is only worth saying so the count can be explained
     *  rather than making the user wonder why nothing is moving. */
    quiet_agents?: Record<string, number>;
    /** the honest total — the map above is keyed by both the prefixed and the bare id */
    running_agents_total?: number }>("/api/mission/live-status", { timeoutMs: 12_000 }),

  /** Who is blocked on a question, who is working, how many are quiet — across every project. */
  missionAttention: () =>
    req<{ ok: boolean; off?: boolean; at: number;
      counts: { blocked: number; working: number; idle: number; agents: number };
      rows: { id: string; path: string; name: string; state: "blocked" | "working" | "idle";
              agents: number; since: number; why: string; todo: string }[] }>(
      "/api/mission/attention", { timeoutMs: 12_000 }),

  /** What is under that fraction of the live page: the element, its CSS, and a crop of it. */
  livePick: (body: { project: string; x: number; y: number; pad?: number; shot?: boolean }) =>
    req<{ ok: boolean; error?: string; tag?: string; id?: string; classes?: string[];
      selector?: string; parent?: string; text?: string; children?: number;
      attrs?: Record<string, string>; css?: Record<string, string>; html?: string;
      canvas?: { width: number; height: number; x: number; y: number } | null;
      at?: { x: number; y: number; w: number; h: number };
      box?: { x: number; y: number; w: number; h: number };
      title?: string; url?: string; crop?: string; prompt?: string }>(
      "/api/live/pick", { method: "POST", body: JSON.stringify(body), timeoutMs: 30_000 }),

  // scheduled (delayed) messages
  scheduledList: () => req<{ jobs: import("../types").ScheduledJob[]; paused?: boolean }>("/api/mission/scheduled"),
  /** Pause or resume every scheduled and recurring message at once. */
  schedulePause: (paused: boolean) =>
    req<{ paused: boolean }>("/api/mission/scheduled/paused", { method: "PUT", body: JSON.stringify({ paused }) }),
  scheduleCreate: (body: { project_id: string; path?: string; root_name?: string; message: string; send_at: number; model?: string; permission_mode?: string; effort?: string; fork?: boolean; thinking?: boolean; agent?: string; new_session?: boolean; session?: string; images?: string[]; repeat_weekdays?: number[]; repeat_time?: string }) =>
    req<import("../types").ScheduledJob>("/api/mission/scheduled", { method: "POST", body: JSON.stringify(body) }),
  scheduleCancel: (id: string) => req<{ ok: boolean }>(`/api/mission/scheduled/${encodeURIComponent(id)}`, { method: "DELETE" }),
  liveState: (projectId: string) =>
    req<{ working: boolean; tokens: number; activity: string; elapsed: number; text: string; kind: string; compacting: boolean }>(`/api/mission/projects/${encodeURIComponent(projectId)}/live`, { timeoutMs: 12_000 }),

  // --- Ask AI (OpenRouter multi-model chat) ---
  chatKey: () => req<{ has_key: boolean }>("/api/chat/key"),
  chatSetKey: (key: string) => req<{ ok: boolean; has_key: boolean }>("/api/chat/key", { method: "POST", body: JSON.stringify({ key }) }),
  chatModels: (force = false) =>
    req<{ ok: boolean; has_key: boolean; error?: string; models: import("../types").ChatModel[] }>(`/api/chat/models${force ? "?force=true" : ""}`),
  chatConversations: () => req<{ conversations: import("../types").ChatConvo[] }>("/api/chat/conversations"),
  chatNewConversation: (model: string, title = "") =>
    req<import("../types").ChatConvoFull>("/api/chat/conversations", { method: "POST", body: JSON.stringify({ model, title }) }),
  chatGetConversation: (cid: string) => req<import("../types").ChatConvoFull>(`/api/chat/conversations/${encodeURIComponent(cid)}`),
  chatDeleteConversation: (cid: string) => req<{ ok: boolean }>(`/api/chat/conversations/${encodeURIComponent(cid)}`, { method: "DELETE" }),
  chatRenameConversation: (cid: string, title: string) =>
    req<import("../types").ChatConvoFull>(`/api/chat/conversations/${encodeURIComponent(cid)}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  chatUpload: async (file: File): Promise<import("../types").ChatAttachment> => {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("/api/chat/upload", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "upload failed");
    return r.json();
  },
  chatFileUrl: (name: string) => `/api/chat/file?name=${encodeURIComponent(name)}`,
  sessionAgentLog: (projectId: string, lines = 120) =>
    req<{ id: string; log: string; sending: boolean }>(`/api/mission/projects/${encodeURIComponent(projectId)}/agent-log?lines=${lines}`),
  /** `agent` = "codex" stops only the Codex turn; left out, the Claude session stops (and a Codex
   *  turn in the same folder with it). */
  sessionCancel: (projectId: string, agent = "") =>
    req<any>(`/api/mission/projects/${encodeURIComponent(projectId)}/cancel-send${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`, { method: "POST" }),
  sessionRewind: (projectId: string, body: { uuid: string; message: string; model?: string; permission_mode?: string; fork?: boolean; effort?: string; thinking?: boolean; session?: string; path?: string; restore_files?: boolean }) =>
    req<any>(`/api/mission/projects/${encodeURIComponent(projectId)}/rewind`, { method: "POST", body: JSON.stringify(body) }),

  // checkpoints (review & revert AI edits)
  ckptList: (projectId: string) =>
    req<{ id: string; checkpoints: import("../types").Checkpoint[] }>(`/api/mission/projects/${encodeURIComponent(projectId)}/checkpoints`),
  ckptCreate: (projectId: string, label = "", agent = "") =>
    req<{ ok: boolean; id?: string; file_count?: number; partial?: boolean; error?: string }>(`/api/mission/projects/${encodeURIComponent(projectId)}/checkpoints`, { method: "POST", body: JSON.stringify({ label, agent }) }),
  ckptDiff: (projectId: string, cid: string) =>
    req<{ ok: boolean; id: string; ts: number; label: string; changed: number; changes: import("../types").CheckpointChange[]; error?: string }>(`/api/mission/projects/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(cid)}/diff`),
  /** `busy` comes back when another agent is writing in this folder; send again with `force`
   *  only after the user has said yes. */
  ckptRestore: (projectId: string, cid: string, files?: string[], force = false) =>
    req<{ ok: boolean; restored?: number; files?: string[]; error?: string; busy?: string[] }>(`/api/mission/projects/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(cid)}/restore`, { method: "POST", body: JSON.stringify({ files: files || null, force }) }),

  // agent
  agentRun: (goal: any, background = false) =>
    req<AgentRun>(`/api/agent/run?background=${background}`, {
      method: "POST",
      body: JSON.stringify(goal),
    }),
  agentRunStatus: (id: string) => req<AgentRun>(`/api/agent/runs/${id}`),

  // --- Workflows (the Workflow tool's multi-agent runs) ---
  workflows: (limit = 80) =>
    req<{ workflows: WorkflowRun[]; live: number; total: number }>(`/api/workflows?limit=${limit}`),
  workflow: (projectId: string, runId: string) =>
    req<WorkflowDetail>(`/api/workflows/${encodeURIComponent(projectId)}/${encodeURIComponent(runId)}`),
  workflowAgent: (projectId: string, runId: string, agentId: string) =>
    req<{ events: WorkflowAgentEvent[]; file?: string; error?: string; prompt?: string; tokens?: number; toolCalls?: number;
          toolCounts?: { name: string; count: number }[]; files?: { path: string; count: number }[] }>(
      `/api/workflows/${encodeURIComponent(projectId)}/${encodeURIComponent(runId)}/agent/${encodeURIComponent(agentId)}`),

  // --- Claude login state + in-app re-login (when auth fails with a 401) ---
  claudeAuth: () =>
    req<{ ok: boolean; needs_login: boolean; logged_in: boolean; reason: string; source: string; since?: number | null; plan: string; expires_at?: number | null }>("/api/auth/claude"),
  claudeLogin: () =>
    req<{ ok: boolean; error?: string; command?: string }>("/api/auth/claude/login", { method: "POST" }),
  claudeRecheck: () =>
    req<{ ok: boolean; needs_login: boolean; logged_in: boolean; reason: string }>("/api/auth/claude/recheck", { method: "POST" }),
  compare: (body: any) =>
    req<AgentRun>("/api/agent/compare", { method: "POST", body: JSON.stringify(body) }),

  // --- A new game, Studio-ready from the first minute (backend/asset_studio/game_scaffold.py) ---
  /** Make the game; the engine install runs in the background and `wsNewGameStatus` follows it. */
  wsNewGame: (body: NewGameBody) =>
    req<NewGameResult>("/api/workspace/new-game", { method: "POST", body: JSON.stringify(body), timeoutMs: 60_000 }),
  wsNewGameStatus: (path: string) =>
    req<NewGameStatus>(`/api/workspace/new-game/status?path=${encodeURIComponent(path)}`),
  /** Folder, engine and install switch the dialog starts from, and whether npm is on this PC. */
  wsNewGameDefaults: (beside = "") =>
    req<NewGameDefaults>(`/api/workspace/new-game/defaults?beside=${encodeURIComponent(beside)}`),
};

export type NewGameEngine = "three" | "playcanvas";

export interface NewGameBody {
  name: string;
  parent?: string;
  engine?: NewGameEngine;
  install?: boolean;
  open?: boolean;
  /** The project the caller is in: with no parent, the game is made next to it. */
  beside?: string;
}

export interface NewGameResult {
  ok: boolean;
  path: string;
  name: string;
  title: string;
  engine: NewGameEngine;
  files: string[];
  install: { state: "running" | "skipped" | "failed"; pid?: number; log?: string; error?: string };
  dev: { script: string; command: string; port: number; url: string; note?: string };
  runtime: { file: string; source: "studio" | "stub"; bytes: number; sha1?: string; why?: string };
  root: (import("../types").WorkspaceRoot & { ok?: boolean }) | null;
  root_error?: string;
  next: string[];
}

export interface NewGameStatus {
  ok: boolean;
  installing: boolean;
  done: boolean;
  state: string;
  code: number | null;
  tail: string;
  log: string;
  node_modules: boolean;
  timed_out?: boolean;
  seconds: number | null;
}

export interface NewGameDefaults {
  ok: boolean;
  parent: string;
  parent_from: "setting" | "beside" | "home";
  engine: NewGameEngine;
  install: boolean;
  engines: { id: NewGameEngine; label: string; dependencies: Record<string, string>; handle: string }[];
  runtime: { ready: boolean; path: string; bytes: number; why?: string; missing?: string[] };
  npm: { found: boolean; path: string };
  node: { found: boolean; path: string };
}

// ---------------------------------------------------------------------------
// THE AGENT'S OWN TAB, LIVE.
//
// The Game tab's iframe plays a SECOND copy of the game; the agent's moves happen in the headless
// tab the live link keeps open. These two watch THAT tab: an MJPEG stream an <img> plays, and a
// status the Game tab polls. Read-only by design — nothing here can send the agent's tab a click
// or a key, and the stream never opens a tab: with no agent in the game it answers 404.

export interface LiveStreamStatus {
  ok?: boolean;
  /** An agent's tab for this game exists in the browser right now. */
  live: boolean;
  url: string;
  engine?: string;
  /** A screencast is running — somebody is watching. */
  streaming: boolean;
  viewers: number;
  /** Frames a second that CHANGED, over the last few seconds. ~0 means nothing moved. */
  fps_measured: number;
  /** Frames a second the browser sent, changed or not (paced to the stream's fps). */
  fps_source?: number;
  last_frame_ms_ago: number | null;
  /** An agent call touched the tab in the last 20 s. */
  agent_active: boolean;
  agent_idle_s?: number | null;
  /** The live link's own switch (cc_live). */
  enabled?: boolean;
  /** The folder the agent opened — a sub-folder or a parent when `match` is not "exact". */
  project?: string;
  match?: "exact" | "inside" | "contains" | "";
  title?: string;
  /** What a new stream would use: Settings → Studio engine, clamped. */
  settings?: { fps: number; quality: number; max_w: number };
  stream?: { fps: number; quality: number; max_w: number; size: [number, number] | null;
             attached: boolean; received: number; published: number; acked: number;
             refreshes: number; switches: number; since_s: number; error?: string };
  /** Why the last screencast stopped, while none runs: "idle", "nobody is watching", … */
  stopped?: { why: string; ago_s: number };
  /** The sentence to show when it is not live. */
  why?: string;
}

export interface LiveStreamOpts {
  fps?: number;
  quality?: number;
  max_w?: number;
  /** A new number is a new connection: how a view that ended is started again. */
  n?: number;
}

/** The URL an <img> plays: MJPEG of the agent's tab. Absent or 0 means the Settings default. */
export function liveStreamUrl(project: string, opts: LiveStreamOpts = {}): string {
  const q = new URLSearchParams({ project });
  if (opts.fps) q.set("fps", String(Math.round(opts.fps)));
  if (opts.quality) q.set("quality", String(Math.round(opts.quality)));
  if (opts.max_w) q.set("max_w", String(Math.round(opts.max_w)));
  if (opts.n) q.set("n", String(opts.n));
  return `/api/live/stream?${q.toString()}`;
}

/** The same frames over a WebSocket, one binary message per JPEG — what the Game tab plays. The
 *  MJPEG response never ends, so in this page it held one of the browser's six connections to the
 *  Studio for as long as the view was open; a WebSocket is not in that pool. */
export function liveStreamWsUrl(project: string, opts: LiveStreamOpts = {}): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${liveStreamUrl(project, opts).replace("/api/live/stream?", "/api/live/stream/ws?")}`;
}

/** Is an agent's tab open for this game, and is anybody watching it? Touches no page. */
export function liveStreamStatus(project: string): Promise<LiveStreamStatus> {
  return req<LiveStreamStatus>(`/api/live/stream/status?project=${encodeURIComponent(project)}`,
    { timeoutMs: 10_000 });
}
