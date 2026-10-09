// Mirrors backend/asset_studio/models.py — keep in sync.

export type StageType =
  | "image2d"
  | "video"
  | "process2d"
  | "gen3d"
  | "texture"
  | "rig"
  | "optimize"
  | "qa";

export type ProviderKind = "local" | "api";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";
export type AssetType =
  | "image"
  | "video"
  | "model"
  | "texture"
  | "atlas"
  | "animation"
  | "render"
  | "other";

export interface ProviderParam {
  name: string;
  label: string;
  type: "string" | "text" | "int" | "float" | "bool" | "select" | "seed" | "file";
  default?: unknown;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
  description?: string;
  group?: string;
}

export interface ProviderInfo {
  id: string;
  name: string;
  stage: StageType;
  kind: ProviderKind;
  requires_key: boolean;
  key_name?: string | null;
  available: boolean;
  available_reason: string;
  description: string;
  license_note: string;
  commercial_ok?: boolean | null;
  cost_hint: string;
  homepage: string;
  params: ProviderParam[];
}

export interface Asset {
  id: string;
  name: string;
  stage: StageType;
  type: AssetType;
  path: string;
  preview_path?: string | null;
  size_bytes: number;
  meta: Record<string, any>;
  tags: string[];
  target_game: string;
  provider_id: string;
  prompt: string;
  seed?: number | null;
  license: string;
  commercial_ok?: boolean | null;
  cost: number;
  job_id?: string | null;
  parent_id?: string | null;
  created_at: number;
}

export interface Job {
  id: string;
  stage: StageType;
  provider_id: string;
  status: JobStatus;
  progress: number;
  step: string;
  eta_seconds?: number | null;
  params: Record<string, any>;
  inputs: string[];
  label: string;
  target_game: string;
  tags: string[];
  outputs: Asset[];
  cost: number;
  error: string;
  error_hint?: string;
  logs: string[];
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
}

export interface JobRequest {
  stage: StageType;
  provider_id: string;
  params?: Record<string, any>;
  inputs?: string[];
  label?: string;
  target_game?: string;
  tags?: string[];
}

export interface GPUStat {
  index: number;
  name: string;
  vram_total_mb: number;
  vram_used_mb: number;
  util_percent: number;
  temperature_c?: number | null;
  power_w?: number | null;
}

export interface DiskStat {
  path: string;
  total_gb: number;
  used_gb: number;
  free_gb: number;
  percent: number;
}

export interface SystemStats {
  cpu_percent: number;
  cpu_cores: number;
  ram_total_gb: number;
  ram_used_gb: number;
  ram_percent: number;
  disk?: DiskStat | null;
  gpus: GPUStat[];
  headless_browsers?: number;
  timestamp: number;
}

export interface JudgeResult {
  score: number;
  accept: boolean;
  reasoning: string;
  suggestions: string;
}

export interface AgentIteration {
  index: number;
  provider_id: string;
  job_id?: string | null;
  asset_id?: string | null;
  judge?: JudgeResult | null;
}

export interface AgentRun {
  id: string;
  goal: any;
  status: JobStatus;
  iterations: AgentIteration[];
  best_asset_id?: string | null;
  best_score: number;
  error: string;
  created_at: number;
  finished_at?: number | null;
}

export type WSEventType =
  | "hello"
  | "job_update"
  | "job_log"
  | "asset_created"
  | "stats"
  | "agent_update"
  | "cc_live";

export interface WSEvent {
  type: WSEventType;
  job_id?: string | null;
  status?: JobStatus | null;
  progress?: number | null;
  step?: string | null;
  eta_seconds?: number | null;
  message?: string | null;
  asset?: Asset | null;
  job?: Job | null;
  data?: Record<string, any> | null;
}

export interface KeyInfo {
  name: string;
  label: string;
  present: boolean;
}

export interface ServiceStatus {
  id: string;
  name: string;
  state: "stopped" | "starting" | "running" | "unreachable";
  reachable: boolean;
  managed: boolean;
  pid?: number | null;
  port?: number | null;
  health_url: string;
  command: string;
  cwd: string;
  autostart: boolean;
  configured: boolean;
  powers: string[];
  last_error: string;
  docs: string;
}

// --- Mission Control (Claude Code projects) --------------------------------
export interface CCProject {
  id: string;
  path: string;
  name: string;
  exists: boolean;
  last_activity: number;
  active: boolean;
  sessions: number;
  size_bytes: number;
  cc_version: string;
  git_branch: string;
  git_last_commit: string;
  git_last_commit_rel: string;
  git_dirty: number;
  last_summary: string;
  awaiting_input?: boolean;
  working?: boolean;
  model?: string;
  ctx_used?: number;
  ctx_max?: number;
  ctx_pct?: number;
  ctx_remaining?: number;
  todos_total: number;
  todo_done: number;
  todo_in_progress: string;
  agents_active: number;
  /** spawns made by the MAIN session — i.e. what you actually asked for */
  agents_top?: number;
  /** spawns made BY those agents (children spawning their own children) */
  agents_nested?: number;
  port?: number | null;
  url?: string | null;
}

export interface CCAgent {
  project_id: string;
  project_name: string;
  session: string;
  agent_id: string;
  last_update: number;
  age_seconds: number;
  label: string;
}

export interface CCPort {
  port: number;
  pid: number;
  process: string;
  cwd?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  is_self: boolean;
  url: string;
}

export interface WorkspaceRoot {
  id: string;
  name: string;
  path: string;
  opened?: boolean;
  // git facts, filled in by /api/worktrees/map. Absent until that lands, so the rail can draw
  // the flat list immediately and nest itself a moment later rather than waiting on git.
  is_repo?: boolean;
  /** the shared git dir — every worktree of one repository reports the same one, so this is
   *  the grouping key that nests folders under their repo however they were created */
  repo?: string;
  branch?: string;
  primary?: boolean;
  detached?: boolean;
  dirty?: boolean;
  /** "primary" = the repo's own checkout · "worktree" = another branch, its own folder ·
   *  "subfolder" = one corner of a checkout, opened on its own to keep an agent's reading small */
  kind?: "primary" | "worktree" | "subfolder" | "plain";
  /** for a subfolder: where it sits inside the checkout */
  rel?: string;
  top?: string;
}

export interface GitStatus {
  ok: boolean;
  error?: string;
  is_repo?: boolean;
  branch?: string;
  detached?: boolean;
  /** a repository with no commits yet — push, pull and "what changed" all mean something else */
  unborn?: boolean;
  upstream?: string;
  ahead?: number;
  behind?: number;
  remote?: string;
  host?: string;
  files?: { path: string; state: string; staged: boolean }[];
  truncated?: number;
  staged?: number;
  unstaged?: number;
  untracked?: number;
  conflicts?: number;
  clean?: boolean;
  last_commit?: { hash: string; subject: string; when: string; author: string } | null;
  identity?: { name: string; email: string; ok: boolean };
}

export interface Worktree {
  path: string;
  name: string;
  branch: string;
  head?: string;
  primary?: boolean;
  locked?: boolean;
  detached?: boolean;
}
export interface TreeEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  skip?: boolean;
}
export interface FileResult {
  path: string;
  /** "model" = .glb/.gltf, rendered by <model-viewer> instead of the code editor */
  kind: "text" | "image" | "binary" | "toobig" | "model";
  text?: string;
  size: number;
  mtime?: number;
  ext?: string;
  /** the editor can offer a live Preview toggle for this file (HTML today) */
  previewable?: boolean;
  /** mesh that isn't glTF (.obj/.stl/.ply) — fetch it via /api/workspace/model, which converts */
  convert?: boolean;
}

export interface FeedDiffLine {
  /** "ctx" = unchanged context around a change; "gap" = elided unchanged lines */
  t: "add" | "del" | "ctx" | "gap";
  s?: string;
  n?: number;   // line number in the OLD file (del / ctx)
  m?: number;   // line number in the NEW file (add / ctx)
}
export interface FeedTodo {
  content: string;
  status: string;
}
export interface FeedEvent {
  kind: "thinking" | "text" | "tool" | "result" | "user" | "question" | "graph" | "agent" | "turn";
  /** "turn": what the finished answer above took. Only the fields the backend actually KNEW are
   *  sent — a missing one means "not known", never zero. The `agent_*` figures are a SLICE of
   *  `tokens` and `cost`, not an addition to them: the CLI prices the whole turn on its own rate
   *  card, and a Task the turn spawned is part of that turn. */
  at?: number;
  tools?: number;
  files?: number;
  added?: number;
  removed?: number;
  wall_s?: number;
  wall_from?: "cli" | "feed";
  gen_s?: number;
  cost?: number;
  effort?: string;
  compacting?: boolean;
  agents?: number;
  agent_tokens?: number;
  agent_tools?: number;
  agent_s?: number;
  ts: string;
  id?: string;   // transcript uuid (user messages) — used to rewind the conversation here
  /** a /btw side-note delivered mid-turn by the hook (recovered from the attachment entry) */
  btw?: boolean;
  /** an update incorporated into a running turn */
  steer?: boolean;
  /** kind "graph": the symbol the code graph was asked about */
  symbol?: string;
  /** this shell command was NOT waited for — it runs on past this card */
  bg?: boolean;
  /** minutes this shell command was allowed to take, when that is a long time */
  slow?: number;
  text?: string;
  tool?: string;
  title?: string;
  subtitle?: string;
  command?: string;
  icon?: string;
  ok?: boolean;
  tokens?: number;
  /** the model stopped at its output limit (stop_reason: max_tokens) — the answer really is cut */
  cut?: boolean;
  diff?: { added: number; removed: number; hunks: FeedDiffLine[] };
  todos?: FeedTodo[];
  options?: { label: string; description?: string }[];
  /** kind "agent": a subagent, with everything the CLI recorded about it */
  agent?: SubAgent;
}

/** A subagent — the Task/Agent tool's own record, joined with its transcript.
 *
 *  The counts come from two places: a foreground agent reports its own `toolStats`, and a
 *  background one reports nothing at all, so those are computed from its transcript instead.
 *  Either way every field here is real; none of it is estimated. */
export interface SubAgent {
  agent_id: string;
  description?: string;
  agent_type?: string;
  model?: string;
  status?: string;
  background?: boolean;
  running?: boolean;
  /** Dispatched and never reported an outcome. A background agent goes quiet for minutes inside
   *  one long turn; `running` alone made it vanish from the UI and read as dead. */
  open?: boolean;
  /** Seconds since this agent last wrote anything — the figure that says whether to worry. */
  idle_s?: number;
  /** What it is doing right now, from the end of its own transcript. `phase` is the half that
   *  was missing: "tool" means that tool is still running, "generating" means it finished and the
   *  agent is writing. Showing a finished tool as current is what made a working agent read as
   *  hung for thirteen minutes. */
  activity?: { tool: string; detail: string; phase?: "tool" | "generating" };
  prompt?: string;
  result?: string;
  output_file?: string;
  session?: string;
  /** wall clock, ms */
  ms?: number;
  /** NEW tokens: input + cache writes + output, every API call counted once. The same context
   *  read again from the cache on every call is `usage.cache_read`, apart - counted in, it made a
   *  747k agent read as 33.6M (and adding transcript lines counted each call two or three times). */
  tokens?: number;
  /** new tokens and cache reads added up: everything that crossed the wire */
  wire?: number;
  /** API calls, each counted once */
  turns?: number;
  tools?: number;
  updated?: number;
  /** the context its FIRST request carried — what the parent handed it, plus prompt and tools.
   *  A Task agent lands near 13-17k; a /subtask, given the whole conversation, lands far higher. */
  inherited?: number;
  /** the biggest single request it made — how full its own window got */
  peak?: number;
  /** the window that model runs with, so `peak` can be read as a fraction */
  ctx_max?: number;
  /** API list price of this agent's own tokens. A Max plan is not charged this. */
  cost?: number;
  usage?: { input: number; output: number; cache_read: number; cache_write: number; cache_write_1h?: number };
  stats?: {
    read: number; search: number; bash: number; edits: number;
    added: number; removed: number; other: number;
  };
  touched?: { edited: { path: string; times: number }[]; read: { path: string; times: number }[]; commands: number };
}

export interface SubAgentDetail {
  ok: boolean;
  error?: string;
  agent?: SubAgent;
  lines: FeedEvent[];
  touched?: SubAgent["touched"];
}

export interface SubAgentClash { path: string; agents: string[]; symbols: string[]; }

export interface FeedResponse {
  id: string;
  lines: FeedEvent[];
  /** a /btw written but not yet delivered — shown at once, before the hook consumes it */
  btw_pending?: string;
  working?: boolean;
  tokens?: number;
  agents_active?: number;
  agent?: string;
  agent_log?: string;
  companions?: Record<string, { text: string; ts: number; running: boolean }>;
}

export interface UsageWindow {
  key: string;
  label: string;
  percent: number;
  reset_seconds: number;
  resets_at?: string | null;
}
/** One limit bucket exactly as the account reports it. `model` is set only on a per-model
 *  bucket ("Fable"), which is what separates a scoped allowance from the shared ones. */
export interface UsageLimit {
  kind: string;          // session | weekly_all | weekly_scoped
  label: string;
  model: string;
  percent: number;
  resets_at?: string;
}

/** One row of the Plan usage account picker (GET /api/mission/usage/accounts). */
export interface UsageAccount {
  id: string;
  name: string;
  kind: "plan" | "balance";
  /** how to connect it: the Claude login, the Codex sign-in, or an API key in `key` */
  connect: "claude" | "codex" | "key";
  key?: string;
  key_url?: string;
  hint?: string;
  connected: boolean;
}

export interface UsageSummary {
  available: boolean;
  provider?: string;
  plan?: string;
  windows: UsageWindow[];
  /** the canonical per-bucket list, including the per-model ones `windows` does not carry */
  limits?: UsageLimit[];
  extra_usage?: { enabled: boolean; used_credits?: number | null; monthly_limit?: number | null; currency?: string | null } | null;
  /** a pay-as-you-go API account (DeepSeek, Kimi, OpenRouter): the money left, not a window */
  balance?: { amount: number; currency: string; limit?: number | null; used?: number | null; detail?: string; ok?: boolean };
  /** the key has no limit, so only `balance.used` is a real number */
  balance_unknown?: boolean;
  /** the account is not signed in / has no key: the bar offers to connect it */
  needs_connect?: boolean;
  note?: string;
  error?: string;
  stale?: boolean;
  source?: string;
  generated_at?: number;
}

export interface ContextInfo {
  model?: string;
  ctx_used?: number;
  ctx_max?: number;
  ctx_pct?: number;
  ctx_remaining?: number;
  just_compacted?: boolean;
  compacting?: boolean;     // a /compact is running right now (can take minutes)
  /** "auto" when the window filled up, "manual" when you asked. From the compaction hook, so it
   *  also covers a session the Studio did not spawn — one running in the terminal pane. */
  compact_trigger?: string;
  awaiting_input?: boolean;
  working?: boolean;
  agents_active?: number;
  tokens?: number;
  // prompt-cache split of the last turn's billed input — the figures CLI 2.1.251 added to /cost
  cache_read?: number;
  cache_write?: number;
  fresh_in?: number;
  cache_pct?: number;
  /** the CLAUDE.md / memory files this session loaded, from the InstructionsLoaded hook.
   *  Billed on every request of the session and shown nowhere else. */
  instructions?: {
    count: number;
    tokens: number;
    files: { path: string; name: string; kind: string; reason: string; parent: string; tokens: number }[];
  };
}

export interface AgentInfo {
  needs_key?: boolean;
  id: string;
  name: string;
  color: string;
  available: boolean;
  path: string;
  streams: boolean;
  note: string;
  install_cmd: string;
  install_url: string;
  custom?: boolean;      // added by the user in Settings → Models
  protocol?: string;     // "anthropic" | "openai"
}

/** Codex in the chat box (GET /api/codex/status): installed, which version, signed in and how,
 *  a sign-in in progress with the page or code it needs, an install or update running. */
export interface CodexStatus {
  installed: boolean;
  path: string;
  exe: string;
  version: string;
  /** the newest @openai/codex on npm, "" until it has been looked up */
  latest: string;
  outdated: boolean;
  /** npm is on this PC, so the Studio can install or update Codex itself */
  npm: boolean;
  running: boolean;
  error: string;
  signed_in: boolean;
  requires_auth: boolean;
  /** signed in, or a provider that needs no OpenAI sign-in */
  ready?: boolean;
  /** "chatgpt" | "apiKey" | "" */
  auth: string;
  email: string;
  plan: string;
  /** Windows: Codex's own sandbox, "ready" | "notConfigured" | "updateRequired" */
  sandbox: string;
  login: null | {
    kind: string; pending: boolean; success: boolean | null; error: string;
    auth_url: string; verification_url: string; user_code: string; at: number; opened?: boolean;
  };
  install: { running: boolean; ok: boolean | null; error: string; what: string; log: string[] };
}

/** One model the installed Codex offers the signed-in account (GET /api/codex/models). */
export interface CodexModel {
  id: string;
  name: string;
  description: string;
  efforts: { id: string; description: string }[];
  default_effort: string;
  images: boolean;
  is_default: boolean;
  /** the "priority" service tier: faster, more usage */
  fast: boolean;
  fast_note: string;
}

// --- plugins: every optional part of the studio, on or off -----------------
export interface PluginInfo {
  id: string;
  kind: "tab" | "service";
  group: string;
  label: string;
  desc: string;
  core: boolean;         // always on — turning it off would lock you out
  enabled: boolean;
  setting: string;       // the settings key a service toggle writes
}

// --- chat providers: any model, in the workspace, like Claude --------------
export interface ChatProvider {
  id: string;
  name: string;
  vendor: string;
  protocol: string;      // "anthropic" = spoken directly | "openai" = through the bridge
  base_url: string;
  models: string[];
  default_model: string;
  context_window: number;   // 0 = unknown, so the agent keeps its safe 200k assumption
  color: string;
  note: string;
  key_url: string;
  preset?: string;
  force_stream?: boolean;   // stream to the provider even when the caller wants one whole answer
  session_with?: string;             // id of the provider whose chat history this one shares
  fallback_models?: string[];        // tried, in order, when the first cannot serve the request
  provider_routing?: Record<string, unknown>;   // the gateway's own `provider` object, verbatim
  limits?: { limit?: number; remaining?: number; reset?: number; at?: number };
  has_key?: boolean;
  agent_id?: string;
}

export type ChatProviderInput = Omit<ChatProvider, "has_key" | "agent_id"> & { api_key?: string };

// --- localhost: which page in a project IS the app --------------------------
export interface EntryInfo {
  path: string;
  rel: string;           // relative to the project root, e.g. "rot-rush/dist/index.html"
  project: string;       // the folder that owns it, stepped up out of any build dir
  project_name: string;
  score: number;
  mtime: number;
  built: boolean;        // lives in dist/ build/ — runnable statically, but a snapshot
  stale: boolean;        // ...and its source has changed since it was built
  source_mtime: number;
  dev: { url?: string; script?: string };   // a live dev server for this project, if any
}

// --- plans written in plan mode --------------------------------------------
export interface PlanInfo {
  id: string;
  engine: string;        // which agent wrote it (claude / kimi / a custom provider)
  file: string;
  name: string;
  title: string;
  phases: string[];
  mtime: number;
  size: number;
  root: string;          // the workspace it is about, matched by the paths it names
  words: number;
}

export interface SlashCommand {
  name: string;
  desc: string;
  scope: "builtin" | "personal" | "project";
}

export interface SearchEntry {
  name: string;
  path: string;
  rel: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  scope: string;
  enabled: boolean;
  state: string;
  category?: string;
  created?: string;     // display string (frontmatter `created` or file creation time)
  created_ts?: number;  // epoch seconds — used for "newest first" ordering
  updated?: string;
}

export interface SetupTask {
  id: string;
  name: string;
  kind: string;
  desc: string;
  installed: boolean;
  restart: boolean;
  running?: boolean;
  needs_restart?: boolean;
  requires?: string;
  link: string;
}

export interface Generator {
  id: string;
  name: string;
  kind: "2d" | "3d" | "splat";
  license: string;
  vram: string;
  node: string;
  homepage: string;
  notes: string;
  installed?: boolean;
  status_label?: string;
  detail?: string;
}

export interface Session {
  id: string;
  ts: number;
  size: number;
  title: string;
  active: boolean;
}

export interface Checkpoint {
  id: string;
  ts: number;
  label: string;
  kind: string;
  /** Which engine was about to write when this was taken ("" for older snapshots and for a
   *  manual one made before the picker knew). Every engine snapshots through the same door now,
   *  so the panel can say whose turn a checkpoint frames instead of assuming Claude. */
  agent?: string;
  file_count: number;
  partial: boolean;
}

export interface CheckpointChange {
  path: string;
  status: "modified" | "added" | "deleted";
  added: number;
  removed: number;
  hunks: FeedDiffLine[];
}

export interface MissionOverview {
  projects: CCProject[];
  agents: CCAgent[];
  ports: CCPort[];
  counts: {
    projects: number;
    active_projects: number;
    active_agents: number;
    running_servers: number;
  };
  generated_at?: number;
}

export const STAGES: { id: StageType; label: string }[] = [
  { id: "image2d", label: "2D Image" },
  { id: "video", label: "Video" },
  { id: "process2d", label: "2D Process" },
  { id: "gen3d", label: "3D Generate" },
  { id: "texture", label: "Texture" },
  { id: "rig", label: "Rig & Animate" },
  { id: "optimize", label: "Optimize" },
  { id: "qa", label: "QA" },
];

// --- Ask AI (OpenRouter multi-model chat) ---
export interface ChatModel { id: string; name: string; free: boolean; context: number; in_price?: string; out_price?: string; }
export interface ChatConvo { id: string; title: string; model: string; updated: number; messages: number; }
export interface ChatAttachment { name: string; orig: string; kind: string; url: string; }
export interface ChatMessage { role: "user" | "assistant" | "system"; content: string; ts?: number; model?: string; attachments?: ChatAttachment[]; }
export interface ChatConvoFull { id: string; title: string; model: string; created: number; updated: number; messages: ChatMessage[]; }

// --- Workflows (the Workflow tool's multi-agent runs) ---
export type WfState = "running" | "done" | "error" | "queued";
export interface WorkflowPhase {
  index: number; title: string; detail: string;
  agentCount: number; done: number; error: number; running: number;
  state: "pending" | "running" | "done";
}
export interface WorkflowAgent {
  index: number; agentId: string; label: string; phaseIndex: number; phaseTitle: string;
  model: string; state: WfState; rawState: string;
  tokens: number; toolCalls: number; durationMs: number;
  lastTool: string; lastToolSummary: string; promptPreview: string; resultPreview: string;
  startedAt?: number; queuedAt?: number; lastProgressAt?: number; attempt: number;
}
export interface WorkflowRun {
  runId: string; project_id: string; project_name: string; name: string; task: string;
  description: string; status: string; live: boolean; agentCount: number;
  done: number; error: number; running: number; phaseCount: number; curPhase: number;
  totalTokens: number; totalToolCalls: number; durationMs: number; startMs: number; updatedMs: number; errorMsg: string;
}
export interface WorkflowDetail extends WorkflowRun {
  phases: WorkflowPhase[]; agents: WorkflowAgent[]; logs: string[];
  defaultModel: string; scriptName: string; taskFull: string; summary: string; result: string;
}
export interface WorkflowAgentEvent { kind: "thinking" | "text" | "tool"; text: string; tool?: string; verb?: string; }

// --- Scheduled (delayed) messages to Claude Code ---
export interface ScheduledJob {
  id: string; project_id: string; path: string; root_name: string; message: string;
  send_at: number; created_at: number; model: string; permission_mode: string; effort: string;
  fork: boolean; thinking: boolean; agent: string; new_session: boolean; session: string;
  images: string[]; status: "pending" | "sending" | "sent" | "failed"; error: string; sent_at?: number | null;
  /** Monday is 0. Empty = fires once — the shape every job had before recurrence existed. */
  repeat_weekdays?: number[];
  /** Local "HH:MM"; only meaningful alongside a non-empty repeat_weekdays. */
  repeat_time?: string;
  last_run_at?: number | null;
  run_count?: number;
}

// --- The engine window ---
// One project as the engine window sees it. `root` is where the game actually lives: it is often
// a subfolder, because a repo commonly holds the game beside its tools (arena/, rot-haul/).
export interface EngineProject {
  path: string; root: string; sub: string; name: string;
  /** What a live tab reports, else what is installed in node_modules. */
  engine: string;
  /** What is installed, whatever a tab says. */
  module: string;
  /** Only three.js and PlayCanvas can have an asset built in them. */
  forgeable: boolean;
  game: boolean; open: boolean; dev_script: string;
  dev_url?: string;
}

export interface EngineStats {
  engine?: string; flat?: boolean; framed?: string;
  triangles?: number; meshes?: number; materials?: number; objects?: number;
  material_names?: string[]; bbox_size?: number[]; bbox_center?: number[]; radius?: number;
  image?: number[]; draw_calls?: number; log?: string[];
}

/** One past generation. `replayable` means the code was kept, so it can be re-run and orbited. */
export interface EngineGen {
  id: string; kind: "forge" | "review"; ts: number;
  project: string; project_name: string; label: string; engine: string;
  sheet: string; ok: boolean; views: string[]; replayable: boolean;
  triangles?: number | null; materials?: number | null; meshes?: number | null;
  flat?: boolean; framed?: string;
  /** The shelves: what kind of thing it is, what it depicts, who decided that, and any tags. */
  type?: "asset" | "scene" | "picture" | "sheet";
  subject?: string;
  subject_by?: "user" | "agent" | "auto";
  tags?: string[];
}

/** One thing a project already has, found by scanning it: a builder in code, one entry of a
 *  spec table, a model file or an image. */
export interface LibraryAsset {
  id: string;
  /** A texture is a picture a material samples; an image is one drawn as itself. Nothing in
   *  a file says which, so the index reads the folder and the suffix conventions. */
  type: "code" | "spec" | "model" | "texture" | "audio" | "image";
  /** A table entry that names a .glb has nothing to build — it has something to show. The
   *  index resolves the variable the entry references to the file on disk. */
  model?: string;
  name: string;
  /** Project-relative, forward slashes. */
  file: string;
  /** Absolute, for opening and revealing. */
  path: string;
  line: number;
  export: string;
  table: string;
  key: string;
  index: number;
  engine: string;
  subject: string;
  tags: string[];
  size: number;
  mtime: number;
  /** Which GAME inside the workspace owns it — `rot-rush`. Only that game's server has the file. */
  root?: string;
  /** The file's own local imports, so opening it can read the game's real palette and tables. */
  deps?: string[];
}

/** One game inside a workspace. A research workspace holds several, each on its own port. */
export interface EngineGame {
  /** Absolute path to the game's own folder. */
  root: string;
  /** Where it sits inside the workspace — `rot-rush`, or "" for the workspace itself. */
  sub: string;
  name: string;
  engine: string;
  dev_url: string;
  dev_script: string;
  servers: Array<{ url: string; port: number; proc: string; title: string }>;
}

/** What the BOOST button shows: whether the local economy is on, what it has saved, and what
 *  switching it on moved. `changed` is what the profile did to the switches it touched, so the
 *  UI can say it out loud rather than silently editing the user's settings. */
/** A switch BOOST deliberately will NOT flip: it changes the text a session is given, so flipping
 *  it mid-conversation re-sends the whole context as a cache miss. The user flips it between
 *  conversations, where the change costs nothing. */
export interface BoostRecommend {
  key: string;
  /** What BOOST would set it to if it were willing to. */
  want: boolean;
  now: boolean;
  matches: boolean;
  /** What the switch buys, in plain words. */
  why: string;
  /** What changing it costs — the reason it is not automatic. */
  cost: string;
}

export interface BoostStatus {
  on: boolean;
  /** Tokens the one constant system-prompt note costs. It rides the prompt, so it is paid once. */
  directive_tokens: number;
  compress: boolean;
  prefetch: boolean;
  /** The prefetch can only answer from a code graph that exists. False means BOOST is on and its
   *  lookups are doing nothing until `cc_graphify` is switched on — see `recommend`. */
  prefetch_ready: boolean;
  level: string;
  profile: Record<string, boolean>;
  /** What the profile did to the switches it touched, measured against the values it REPLACED —
   *  `changed` is true for a switch BOOST moved, so the UI can name it instead of silently
   *  editing the user's settings. */
  changed: Record<string, { on: boolean; applied: boolean; changed: boolean; from: boolean }>;
  /** The prompt-riding switches BOOST recommends but refuses to move by itself, each with what it
   *  buys and what changing it costs. Shown, never applied behind the user's back. */
  recommend: BoostRecommend[];
  /** Tokens actually removed from messages. A prefetch is NOT counted here — it costs tokens. */
  saved_tokens: number;
  saved_chars: number;
  messages: number;
  prefetched: number;
  since: number;
}
