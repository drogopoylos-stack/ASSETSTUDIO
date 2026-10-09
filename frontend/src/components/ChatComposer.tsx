import { useEffect, useMemo, useRef, useState } from "react";
import {
  AtSign,
  Bot,
  Brain,
  Check,
  ChevronDown,
  Clock,
  Copy,
  CornerDownRight,
  Database,
  ExternalLink,
  FileText,
  Hash,
  Languages,
  Loader2,
  MessageSquarePlus,
  Mic,
  Network,
  Paperclip,
  Send,
  SlidersHorizontal,
  Square,
  SquareTerminal,
  Terminal,
  X,
  Zap, Globe,} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import { notesToPrompt } from "./diffNotes";
import { FEED_DETAILS, detailSummary } from "./feedDetail";
import type { AgentInfo, BoostStatus, ScheduledJob, SearchEntry, SlashCommand, WorkspaceRoot } from "../types";
import { cls, pollWhileVisible } from "./ui";
import { agentKey, folderAgent, modeFallback, pinnedAgent, readEffort, readPref, writeEffort, writePref } from "./sendPrefs";
import { VoiceButton, type VoiceState } from "./VoiceButton";
import { DEEPSEEK_MODELS } from "./modelLabel";
import { CODEX_ACCESS, CodexSettings, CodexSignIn, codexEffortRows, codexModeOf, codexModelRows, refreshCodex, useCodex } from "./CodexPanel";

// Every version we name is pinned by FULL id, never by a bare alias, so a label can't start lying when
// a newer model ships. (`opus`/`sonnet`/`haiku` are *moving* aliases — `opus` resolves to claude-opus-4-8
// today, verified via `claude -p --output-format json`, and will silently move later; that is why the
// version-labelled rows below don't use them.) "Opus (auto)" keeps the alias available on purpose, for
// anyone who wants to ride whatever Anthropic considers current — the header badge always reports the
// model that actually ran, so the auto row is self-documenting. An id your Claude CLI doesn't know is
// simply rejected (harmless); update it with `npm i -g @anthropic-ai/claude-code`.
// Fable 5.1 arrived with CLI 2.1.257 (1M native, June 2026 cutoff, cache reads at a quarter of
// Fable 5's). It is rolling out per account, so a pick can still be refused — the backend records
// that from the turn and the option below says so instead of the pick costing a message.
// Opus 5.5 arrived with CLI 2.1.280, and its catalog entry (read out of the binary, not recalled)
// says: 1M native, June 2026 cutoff, 128k max output where Opus 5 defaults to 64k, every effort
// level, fast mode, and $4/$20 per million with cache reads at $0.20 — against Opus 5's $5/$25 and
// $0.50. Its own default effort is medium, not high. Opus 5 stays on the list beside it.
const MODELS: [string, string][] = [["default", "Default"], ["claude-opus-5-5", "Opus 5.5"], ["claude-opus-5", "Opus 5"], ["claude-opus-4-8", "Opus 4.8"], ["opus", "Opus (auto)"], ["claude-fable-5-1", "Fable 5.1"], ["claude-fable-5", "Fable 5"], ["sonnet", "Sonnet"], ["haiku", "Haiku"]];
// Kimi runs through the same Claude Code engine (Moonshot endpoint) — model rides the spawn env
const KIMI_MODELS: [string, string][] = [["default", "Kimi K3"], ["kimi-k3", "Kimi K3 (pinned)"], ["kimi-k2-turbo-preview", "Kimi K2 Turbo"]];
// Qwen rides the SAME Claude Code engine (DashScope's Anthropic-compatible endpoint), so the model
// travels via ANTHROPIC_MODEL in the spawn env — these are DashScope ids, not Anthropic ones.
// Every id below was verified live against the endpoint before being listed here.
const QWEN_MODELS: [string, string][] = [["default", "Qwen3.8 Max"], ["qwen3.8-max", "Qwen3.8 Max (pinned)"], ["qwen3-max", "Qwen3 Max"], ["qwen3-coder-plus", "Qwen3 Coder Plus"], ["qwen3-max-preview", "Qwen3 Max Preview"]];
// DEEPSEEK HARNESS: ONE MODEL, FOUR IDS, TWO OF WHICH CAN SEE.
//
// `deepseek-flash` IS DeepSeek V4.1 Flash, and that is read off the runtime rather than guessed: the
// adapter's catalogue names that row "DeepSeek-V41-Flash", and the release notice says "Set your
// model to `deepseek-flash`" — V4.1 Flash has no id of its own. The row was here all along, labelled
// "DeepSeek Flash", which is exactly why it read as an older unversioned model and V4.1 looked
// missing from this menu. So the names now say what answers. The three V4-era ids are aliases the
// API still serves and routes to V4.1 Flash (V4-Flash and V4-Flash-Vision-Exp retired 2026-09-10;
// V4-Pro phased out from 2026-09-14) — they are kept because a saved choice may still name one.
//
// WHICH ROW SEES is a different question, and it is the runtime's: only the two rows it catalogues
// with `inputModalities: ["text","image"]` may carry a picture; the rest have it dropped before the
// request. This list once offered ONLY the text-only pair, which is the whole reason the paperclip
// looked broken. `default` (server-side: MODELS[0]) is the vision row, so a screenshot just works.
// The ROWS themselves live in `modelLabel.ts`, beside the names they are built from: this menu was
// wrong once, so it is the one part of this list a plain node test can hold to account.
const EFFORTS: [string, string][] = [["default", "Default"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["xhigh", "X-High"], ["max", "Max"], ["ultracode", "Ultracode"]];
const MODES = ["default", "plan", "acceptEdits", "bypassPermissions"];
// Models where fast mode actually engages. Checked against the CLI: opus-5, opus-5[1m] and
// opus-4-8 report fast_mode_state "on"; the Fable models, sonnet and haiku stay "off" —
// and the catalog agrees, listing `fast_mode` as an Opus-only capability. Opus 5.5 carries it
// too (2.1.280 catalog), and the pattern below already covers every `claude-opus-` id.
const FAST_MODELS = /^(claude-)?opus(-|$)|^claude-opus-|^opus$/i;
// An engine the user added in Settings → Models. It runs the same Claude Code loop, so it gets the
// same effort / mode / thinking controls; only the model list comes from the provider.
const isAltEngine = (a: AgentInfo | null) => !!a && (a.custom || a.id === "kimi" || a.id === "qwen");
// Codex (OpenAI) lists its own models, and each model its own efforts (low … ultra on GPT-6.1 Sol),
// read from the installed CLI through /api/codex/models (CodexPanel.tsx). A fixed list here went
// stale: it offered gpt-5-codex and o3, which Codex no longer has, and none of the GPT-6 models.
// stable empty reference — returning a fresh [] from a zustand selector makes
// useSyncExternalStore loop forever (blank screen). Always fall back to this.
const EMPTY_INFLIGHT: { full: string; images: string[] }[] = [];
const SCHED_PRESETS: [number, string][] = [[5, "5m"], [15, "15m"], [30, "30m"], [60, "1h"], [120, "2h"], [240, "4h"]];
// Monday-first, matching datetime.weekday() on the backend (Mon = 0). Pick no days and a
// scheduled prompt stays the one-shot it has always been.
const DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"];
const FULL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type Trigger = { type: "file" | "slash"; query: string; start: number } | null;

// An unsent message must survive leaving the tab — the composer unmounts with the page, so the
// draft and its undo stack are kept per project instead of in component state.
// The chosen agent is remembered PER WORKSPACE. It used to be one global key, so picking Ox
// Alpha in one project silently switched every other project to it -- and switching back left you
// talking to the wrong model without noticing.
// The keys and the reading rules are in sendPrefs.ts, so that every OTHER sender (edit-and-resend,
// answering a question, /compact, the terminal) reads them exactly the way this box does.
const initialAgent = (pid: string) => folderAgent(pid);
// The global key survives as the default for a workspace nobody has chosen for yet.
// Writing it is also what tells the REST of the app: the Workspace page keeps its own copy of the
// agent (the feed, model badge and context meter are all keyed on it), and it learns about a
// change only from this key plus the "cc-agent" event. Setting agent state without going through
// here leaves the chat on one engine and the feed showing another.
const rememberAgent = (pid: string, a: string) => {
  localStorage.setItem("cc-agent", a);
  if (pid) localStorage.setItem(agentKey(pid), a);
  window.dispatchEvent(new Event("cc-agent"));
};

// Model / effort / approval are bound to the FOLDER as well as the agent, so "Opus at high effort
// in STUDIO" survives a trip to another workspace. The per-agent key stays as the fallback: an
// existing setup carries over, and a workspace you have never configured starts from your usual
// choice rather than from nothing.
// readPref / writePref / modeFallback: sendPrefs.ts.

// Effort is saved per MODEL as well, matching what CLI 2.1.251 changed `/effort` to do. The right
// effort is a property of the model, not of the person: Opus 5 multiplies its cost 1.6x from high
// to max, Fable 1.91x, Sonnet 5.59x — so a level carried over from the last model is the wrong
// level. The plain per-agent key stays as the fallback, so your existing choice is inherited by
// the first model you use it on instead of being reset. Opus 5.5 publishes NO cost index at all
// and asks for medium by default, so its levels are its own; keeping them per model is the point.
// readEffort / writeEffort: sendPrefs.ts.

function readDraft(projectId: string): { msg: string; files: string[]; images: string[]; undo: string[] } {
  const out = { msg: "", files: [] as string[], images: [] as string[], undo: [] as string[] };
  try {
    const d = JSON.parse(localStorage.getItem(`cc-draft-${projectId}`) || "null");
    if (typeof d?.msg === "string") out.msg = d.msg;
    if (Array.isArray(d?.files)) out.files = d.files;
    if (Array.isArray(d?.images)) out.images = d.images;
  } catch { /* corrupt entry — start clean rather than block the composer */ }
  try {
    const u = JSON.parse(localStorage.getItem(`cc-undo-${projectId}`) || "[]");
    if (Array.isArray(u)) out.undo = u.filter((x) => typeof x === "string");
  } catch { /* ignore */ }
  return out;
}

/** The highest `composerInsert` nonce any composer on this page has already taken. */
let takenInsert = 0;

export function ChatComposer({
  projectId, rootPath, rootName, variant = "panel", disabled, onSendingChange,
  session = "", newSession = false, onSent, onClear, cliLook, onTerminalCommand,
  selectedAgent, onAgentChange,
}: {
  projectId: string;
  selectedAgent?: string;
  /** Return `false` to refuse the change (the pane already has that engine elsewhere); the box
   *  then stays on the engine it had. */
  onAgentChange?: (agent: string) => void | boolean;
  rootPath?: string;
  rootName?: string;
  variant?: "card" | "panel";
  disabled?: boolean;
  onSendingChange?: (v: boolean) => void;
  session?: string;
  newSession?: boolean;
  onSent?: () => void;
  onClear?: () => void;
  /** The look of the pane this composer sits in. Passing it is also what puts the per-project
   *  Look toggle in the settings menu — a pane that does not paint the look must not offer it,
   *  or the switch would do nothing you can see. */
  cliLook?: boolean;
  /** Run a command in the real CLI on this session. `/subtask` and `/fork` are drawn by the
   *  interactive interface and the headless stream answers "isn't available in this
   *  environment", so the composer hands them over rather than sending them into a refusal. */
  onTerminalCommand?: (command: string) => void;
}) {
  const initialAgent = (pid: string) => selectedAgent || folderAgent(pid);
  /** False when the owner refused the engine — the caller must then leave the box as it was. */
  const rememberAgent = (pid: string, a: string): boolean => {
    if (onAgentChange) return onAgentChange(a) !== false;
    localStorage.setItem("cc-agent", a);
    localStorage.setItem(agentKey(pid), a);
    window.dispatchEvent(new Event("cc-agent"));
    return true;
  };
  const compact = variant === "card";
  const toast = useStore((s) => s.toast);
  const setTab = useStore((s) => s.setTab);
  // The CLI look moves the prompt marker into the box and puts the mode line under it; every
  // control, key and shortcut below is the one it always was.
  const studioCli = useStore((s) => s.skin) === "cli";
  const cli = cliLook ?? studioCli;
  const projectCli = useStore((s) => !!s.projectCli[projectId]);
  const setProjectCli = useStore((s) => s.setProjectCli);
  const feedDetail = useStore((s) => s.feedDetail);
  const setFeedDetail = useStore((s) => s.setFeedDetail);

  const [msg, setMsg] = useState("");
  const [sending, setSending] = useState(false);
  const [cfg, setCfg] = useState(false);
  const [agentMenu, setAgentMenu] = useState(false);
  const [model, setModel] = useState(() => readPref("model", projectId, initialAgent(projectId), "default"));
  const [effort, setEffort] = useState(() => {
    const a = initialAgent(projectId);
    return readEffort(projectId, a, readPref("model", projectId, a, "default"));
  });
  const [mode, setMode] = useState(() => { const a = initialAgent(projectId); return readPref("mode", projectId, a, modeFallback(a)); });
  const [fork, setFork] = useState(() => localStorage.getItem("cc-fork") === "1");
  const [thinking, setThinking] = useState(() => localStorage.getItem("cc-thinking") === "1");
  const [autolearn, setAutolearn] = useState(() => localStorage.getItem("cc-autolearn") !== "0");
  // BOOST — the ON/OFF button in the row that makes a prompt cheaper by using this PC first.
  // The BACKEND is authoritative here, unlike the note switches below: switching it on also
  // applies a profile of saving switches and remembers what it replaced, so a local copy that
  // disagreed would paint the wrong state. localStorage only draws the first frame.
  const [boost, setBoost] = useState(() => localStorage.getItem("cc-boost") === "1");
  const [boostSt, setBoostSt] = useState<BoostStatus | null>(null);
  const [boostBusy, setBoostBusy] = useState(false);
  const [memory, setMemory] = useState(() => localStorage.getItem("cc-memory") !== "0");
  const [graphify, setGraphify] = useState(() => localStorage.getItem("cc-graphify") === "1");
  const [webTools, setWebTools] = useState(() => localStorage.getItem("cc-web-tools") !== "0");
  const [webStatus, setWebStatus] = useState<{ available: boolean; installing: boolean; error: string } | null>(null);
  // Fast mode — Opus with faster output (NOT a smaller model). Opus-only: the CLI reports it
  // off for Fable/Sonnet/Haiku, so the row says so rather than pretending it applies.
  const [fastMode, setFastMode] = useState(false);
  // Settings -> Planning -> "Force plan mode". The backend applies it, so without this the
  // selector would keep showing acceptEdits while every message ran in plan mode.
  const [forcePlan, setForcePlan] = useState(false);
  // graphify install/health — the Studio installs it itself (self-contained venv), so show the real
  // state (installing… / ready / failed) instead of the old "enable it and hope" with no feedback.
  const [gpyStatus, setGpyStatus] = useState<{ available: boolean; installing: boolean; error: string } | null>(null);
  const [alStatus, setAlStatus] = useState<Awaited<ReturnType<typeof api.autolearnStatus>> | null>(null);
  // output style — a system-prompt overlay that changes HOW Claude writes ("" = off, Claude's own
  // voice). Same ~/.claude/output-styles/*.md files `/output-style` uses, so the list is whatever
  // is installed there; the Studio ships ASD-STE100 (Simplified Technical English).
  const [ostyle, setOstyle] = useState(() => localStorage.getItem("cc-output-style") || "");
  const [ostyles, setOstyles] = useState<Awaited<ReturnType<typeof api.outputStyles>>["styles"]>([]);
  // voice input mode: "translate" = any speech → English (incl. Greek); "transcribe" = keep spoken language
  const [voiceTask, setVoiceTask] = useState(() => localStorage.getItem("cc-voice-task") || "translate");
  // …and what dictation is doing right now. It is reported up by the mic so the box can say
  // "listening" itself: you dictate without looking at the screen, so a tooltip cannot carry it.
  const [voiceSt, setVoiceSt] = useState<VoiceState>({ st: "idle", hint: "", ready: false, engine: "", task: "" });
  const [agentId, setAgentId] = useState(() => initialAgent(projectId));
  // Preferences and transcript ids stay tied to the real folder, while the
  // selected engine and its busy/optimistic state belong to this pane's feed.
  const feedId = agentId === "claude" || agentId.startsWith("term:") ? projectId : `${agentId}--${projectId}`;
  // dual-agent: extra co-agents that run alongside the primary. They see each other's messages
  // and review each other. Options are stored per-agent (cc-<key>-<id>) so each keeps its own.
  const [savedCompanions, setCompanions] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("cc-companions") || "[]"); } catch { return []; } });
  const companions = agentId === "deepseek-harness" ? [] : savedCompanions.filter((id) => id !== "deepseek-harness");
  const [compNonce, setCompNonce] = useState(0);
  const toggleCompanion = (cid: string) => setCompanions((cs) => { const next = cs.includes(cid) ? cs.filter((x) => x !== cid) : [...cs, cid]; try { localStorage.setItem("cc-companions", JSON.stringify(next)); } catch { /* ignore */ } return next; });
  const compGet = (cid: string, key: string, def: string) => localStorage.getItem(`cc-${key}-${cid}`) || def;
  const compSet = (cid: string, key: string, v: string) => { localStorage.setItem(`cc-${key}-${cid}`, v); setCompNonce((n) => n + 1); };
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  // Codex: installed? signed in? its models. Polled only while Codex is the agent or a co-agent.
  const codex = useCodex(agentId === "codex" || companions.includes("codex"));
  const codexReady = !!codex.status?.installed && !!codex.status?.ready;
  const [codexFast, setCodexFast] = useState(false);
  const [codexPlanner, setCodexPlanner] = useState(false);
  // CLIs that draw their own full-screen interface. They are NOT in `agents`: that list is
  // for engines the chat can render, and putting a terminal-only CLI in it would offer the
  // user a send that nothing can receive.
  const [termAgents, setTermAgents] = useState<{ id: string; name: string; color: string; installed: boolean; note: string }[]>([]);
  const [slash, setSlash] = useState<SlashCommand[]>([]);
  const [images, setImages] = useState<string[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [dropping, setDropping] = useState(false);

  // scheduled (delayed) send
  const [schedOpen, setSchedOpen] = useState(false);
  const [schedMin, setSchedMin] = useState(30);
  const [repeatDays, setRepeatDays] = useState<number[]>([]);
  const [repeatTime, setRepeatTime] = useState("09:00");
  const [schedTarget, setSchedTarget] = useState("");   // "" = current workspace
  const [roots, setRoots] = useState<WorkspaceRoot[]>([]);
  const [sched, setSched] = useState<ScheduledJob[]>([]);
  const [schedPaused, setSchedPaused] = useState(false);
  const pendingCount = sched.filter((j) => j.status === "pending").length;

  // autocomplete (@file / /slash)
  const [trigger, setTrigger] = useState<Trigger>(null);
  const [fileHits, setFileHits] = useState<SearchEntry[]>([]);
  const [mi, setMi] = useState(0);

  // messages already fired into the live session, draining as Claude works through them.
  // Kept per-project in the store so they persist across workspace/tab switches.
  const inflight = useStore((s) => s.inflight[feedId] || EMPTY_INFLIGHT);
  const pushInflight = useStore((s) => s.pushInflight);
  const dropInflight = useStore((s) => s.dropInflight);
  const clearInflight = useStore((s) => s.clearInflight);
  const ta = useRef<HTMLTextAreaElement>(null);

  // TEXT THE USER ASSEMBLED BY POINTING: a clicked page element, a batch of diff comments. It
  // arrives in the box rather than being sent, so it can still be read and added to.
  //
  // `takenInsert` is a MODULE variable, not store state, and that is the whole trick. Two panes
  // on the same project have two composers; clearing a store field from inside one effect does
  // not stop the sibling's effect for the same commit, so both would append. A module variable
  // is written synchronously and read by whichever effect runs second.
  const composerInsert = useStore((s) => s.composerInsert);
  const insertIntoComposer = useStore((s) => s.insertIntoComposer);
  const clearDiffNotes = useStore((s) => s.clearDiffNotes);
  const allNotes = useStore((s) => s.diffNotes);
  const myNotes = useMemo(() => allNotes.filter((n) => n.projectId === projectId), [allNotes, projectId]);
  useEffect(() => {
    const ins = composerInsert;
    if (!ins || ins.projectId !== projectId || ins.nonce <= takenInsert) return;
    takenInsert = ins.nonce;
    setMsg((m) => (m.trim() ? m.replace(/\s*$/, "\n\n") : "") + ins.text + "\n\n");
    // The caret goes to the end, so what the user types next follows what was pasted in.
    requestAnimationFrame(() => {
      const el = ta.current;
      if (!el) return;
      el.focus();
      el.selectionStart = el.selectionEnd = el.value.length;
      el.scrollTop = el.scrollHeight;
    });
  }, [composerInsert, projectId]);

  const poll = useRef<number | null>(null);
  const searchTimer = useRef<number | null>(null);
  const lastSend = useRef(0);
  const setLS = (k: string, v: string, f: (v: string) => void) => {
    if (k === "cc-agent") { if (rememberAgent(projectId, v)) f(v); return; }
    f(v);
    localStorage.setItem(k, v);
  };

  const agent = agents.find((a) => a.id === agentId) || null;
  // Codex needs a sign-in as well as the CLI; until the status is in, "installed" is the answer
  const agentAvail = agentId === "codex" && codex.status ? codexReady
    : agent ? agent.available : agentId === "claude" ? !disabled : true;
  // Model list per agent. A user-added provider supplies its own; an id that is not listed can
  // still be typed into the provider and sent, so this is a convenience, not a limit.
  const [customModels, setCustomModels] = useState<Record<string, [string, string][]>>({});
  const modelsFor = (id: string): [string, string][] =>
    customModels[id] || (id === "codex" ? codexModelRows(codex.models, codex.def) : id === "kimi" ? KIMI_MODELS
      : id === "deepseek-harness" ? DEEPSEEK_MODELS
      : id === "qwen" ? QWEN_MODELS : MODELS);

  // Models this account was refused on a real turn — learned by the backend from the CLI's own
  // message, never probed. A model the CLI knows about can still be unreachable here while it
  // rolls out, and picking it silently loses the message; the option carries the reason instead.
  const [noAccess, setNoAccess] = useState<Record<string, { reason: string }>>({});
  const blockedModel = (v: string) => noAccess[v.split("[")[0].toLowerCase()];

  // Switching model brings that model's own saved effort with it (readEffort, sendPrefs.ts).
  const pickModel = (v: string) => {
    writePref("model", projectId, agentId, v);
    setModel(v);
    setEffort(readEffort(projectId, agentId, v));
  };
  const pickEffort = (v: string) => { writeEffort(projectId, agentId, model, v); setEffort(v); };

  useEffect(() => {
    api.settings().then((st) => {
      setFastMode(!!st?.cc_fast_mode);
      setCodexFast(!!st?.codex_fast);
      setCodexPlanner(!!st?.codex_planner);
      setForcePlan(!!st?.cc_force_plan);
      // These four are the BACKEND's settings - that is what the agent gets - and Settings ->
      // Agent notes switches them there without touching this browser's copy, so a switch changed
      // there looked unchanged here. The copy only paints the first frame; the backend wins.
      const own = (k: string, lsKey: string, set: (v: boolean) => void) => {
        if (typeof st?.[k] !== "boolean") return;
        set(st[k]);
        localStorage.setItem(lsKey, st[k] ? "1" : "0");
      };
      own("cc_autolearn", "cc-autolearn", setAutolearn);
      own("cc_memory", "cc-memory", setMemory);
      own("cc_graphify", "cc-graphify", setGraphify);
      own("cc_web_tools", "cc-web-tools", setWebTools);
    }).catch(() => {});
    // BOOST is its own endpoint rather than one of the note switches, because turning it on
    // applies a profile as well. Ask the backend what is really on, and what it has saved.
    api.boostStatus()
      .then((b) => { setBoostSt(b); setBoost(b.on); localStorage.setItem("cc-boost", b.on ? "1" : "0"); })
      .catch(() => {});          // an older backend has no BOOST; the button then just reads "off"
    api.missionAgents().then((r) => setAgents(r.agents)).catch(() => {});
    api.claudeStatus().then((s) => setNoAccess(s.model_access || {})).catch(() => {});
    api.terminalAgents().then((r) => setTermAgents(r.agents || [])).catch(() => {});
    api.chatProviders().then((r) => {
      const m: Record<string, [string, string][]> = {};
      for (const p of r.providers) {
        m[p.id] = [["default", p.default_model || "Provider default"] as [string, string],
          ...p.models.map((x) => [x, x] as [string, string])];
      }
      setCustomModels(m);
    }).catch(() => {});
  }, []);
  useEffect(() => {
    api.missionSlash(projectId).then((r) => setSlash(r.commands)).catch(() => {});
  }, [projectId]);
  useEffect(() => () => { [poll, searchTimer].forEach((r) => r.current && window.clearInterval(r.current)); }, []);
  // Switching workspace switches the agent with it. A stored choice wins. Failing that the
  // transcripts say which engine actually ran in that folder, so a workspace you have used before
  // lands on the right agent the first time rather than after you re-pick in every one.
  useEffect(() => {
    if (!projectId) return;
    if (selectedAgent) { setAgentId(selectedAgent); return; }
    const stored = pinnedAgent(projectId);
    if (stored) { setAgentId(stored); rememberAgent(projectId, stored); return; }
    if (!rootPath) return;
    let alive = true;
    api.lastAgent(rootPath).then((r) => {
      const found = r?.agent;
      if (!alive || !found) return;
      if (agents.length && !agents.some((x) => x.id === found)) return;   // engine since removed
      if (pinnedAgent(projectId)) return;                                 // user picked meanwhile
      setAgentId(found);
      rememberAgent(projectId, found);
    }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, rootPath, agents.length, selectedAgent]);

  // model / effort / approval follow BOTH the agent and the workspace
  useEffect(() => {
    const m = readPref("model", projectId, agentId, "default");
    setModel(m);
    setEffort(readEffort(projectId, agentId, m));
    setMode(readPref("mode", projectId, agentId, modeFallback(agentId)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, projectId]);

  // auto-grow the textarea with its content, up to a user-adjustable cap (drag the grip)
  const minH = compact ? 34 : 56;
  const [maxH, setMaxH] = useState(() =>
    Number(localStorage.getItem(compact ? "cc-maxh-card" : "cc-maxh-panel")) || (compact ? 150 : 220));
  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(minH, Math.min(el.scrollHeight, maxH))}px`;
    el.style.overflowY = el.scrollHeight > maxH ? "auto" : "hidden";
  }, [msg, maxH, minH]);
  function startComposerResize(e: React.PointerEvent) {
    e.preventDefault();
    const startY = e.clientY, start = maxH, cap = Math.round(window.innerHeight * 0.7);
    const move = (ev: PointerEvent) => setMaxH(Math.max(72, Math.min(cap, start + (startY - ev.clientY))));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setMaxH((m) => { localStorage.setItem(compact ? "cc-maxh-card" : "cc-maxh-panel", String(m)); return m; });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // ---- autocomplete trigger detection ----
  function recompute(value: string, caret: number) {
    // slash: a leading "/word" with no space yet
    if (value.startsWith("/") && !value.slice(0, caret).includes(" ")) {
      setTrigger({ type: "slash", query: value.slice(1, caret), start: 0 });
      return;
    }
    // @file: token under caret starting with @
    const upto = value.slice(0, caret);
    const at = upto.lastIndexOf("@");
    if (at >= 0 && !/\s/.test(value.slice(at + 1, caret)) && (at === 0 || /\s/.test(value[at - 1]))) {
      setTrigger({ type: "file", query: value.slice(at + 1, caret), start: at });
      return;
    }
    setTrigger(null);
  }
  function onChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    snapshot(msg);            // remember the text BEFORE this keystroke, so Ctrl+Z returns to it
    setMsg(e.target.value);
    recompute(e.target.value, e.target.selectionStart || e.target.value.length);
    setMi(0);
  }

  // fetch file hits when @-typing
  useEffect(() => {
    if (trigger?.type !== "file" || !rootPath) { setFileHits([]); return; }
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      api.wsSearch(rootPath, trigger.query, 12).then((r) => setFileHits(r.entries)).catch(() => setFileHits([]));
    }, 120) as unknown as number;
  }, [trigger, rootPath]);

  const LOCAL_AGENT_SLASH: SlashCommand[] = [
    { name: "/codex", desc: "Run this prompt with Codex — Claude stays idle, watch it live", scope: "builtin" },
    { name: "/claude", desc: "Run this prompt with Claude", scope: "builtin" },
    { name: "/btw", desc: "Live side-note — Claude sees it while working, keeps the main task going", scope: "builtin" },
    { name: "/steer", desc: "Correct the running Claude or Codex turn at its next step", scope: "builtin" },
  ];
  const slashHits = [...LOCAL_AGENT_SLASH, ...slash].filter((c) => c.name.slice(1).toLowerCase().startsWith((trigger?.query || "").toLowerCase())).slice(0, 10);
  const menuOpen = !!trigger && (trigger.type === "file" ? fileHits.length > 0 : slashHits.length > 0);
  const menuItems: { label: string; sub: string; tag?: string }[] = trigger?.type === "file"
    ? fileHits.map((f) => ({ label: f.name, sub: f.rel }))
    : slashHits.map((c) => ({ label: c.name, sub: c.desc, tag: c.scope }));

  function accept(idx: number) {
    if (!trigger) return;
    if (trigger.type === "slash") {
      const c = slashHits[idx]; if (!c) return;
      setMsg(c.name + " ");
    } else {
      const f = fileHits[idx]; if (!f) return;
      const before = msg.slice(0, trigger.start);
      const after = msg.slice((ta.current?.selectionStart) || msg.length);
      setMsg(`${before}@${f.rel} ${after}`);
      setFiles((xs) => (xs.includes(f.path) ? xs : [...xs, f.path]));
    }
    setTrigger(null);
    setTimeout(() => ta.current?.focus(), 0);
  }

  // ---- undo / redo for the message box ----
  // A controlled <textarea> loses the browser's own undo stack (React re-writes `value`, and
  // clearing it on send wipes what is left), so Ctrl+Z did nothing and a sent message was gone for
  // good. Keep our own stack: snapshots while you type, plus a snapshot of the text at send — so
  // Ctrl+Z right after sending brings the message back to edit or re-send.
  const undoRef = useRef<string[]>([]);
  const redoRef = useRef<string[]>([]);
  const lastSnap = useRef(0);

  // ---- draft survival across tab switches ----
  // Moving to another tab UNMOUNTS this composer (pages are lazy-loaded per tab), so an unsent
  // message died with it — and Ctrl+Z could not bring back something that no longer existed.
  // Both the draft and the undo stack live in localStorage, per project, so leaving and coming
  // back restores exactly what was there. Attachments ride along: losing those is the same bug.
  const draftKey = `cc-draft-${projectId}`;
  const undoKey = `cc-undo-${projectId}`;
  function persistUndo() {
    if (!projectId) return;
    try { localStorage.setItem(undoKey, JSON.stringify(undoRef.current.slice(-30))); } catch { /* quota */ }
  }
  // Restore DURING render, not in an effect. React's sanctioned "adjust state when a prop changes"
  // pattern: it discards this render and re-runs with the restored values, so effects below never
  // observe the previous project's text. An effect here would run AFTER the save effect had already
  // seen the old text paired with the new project's key, and written one project's draft onto another.
  const draftFor = useRef("");
  if (projectId && draftFor.current !== projectId) {
    draftFor.current = projectId;
    const d = readDraft(projectId);
    setMsg(d.msg); setFiles(d.files); setImages(d.images);
    undoRef.current = d.undo;
    redoRef.current = [];
  }
  useEffect(() => {                                  // save, debounced — this fires per keystroke
    if (!projectId || draftFor.current !== projectId) return;
    const t = window.setTimeout(() => {
      try {
        if (msg || files.length || images.length) localStorage.setItem(draftKey, JSON.stringify({ msg, files, images }));
        else localStorage.removeItem(draftKey);
      } catch { /* out of quota — a lost draft beats a broken composer */ }
    }, 250);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [msg, files, images, projectId]);

  function snapshot(text: string, force = false) {
    const stack = undoRef.current;
    if (stack[stack.length - 1] === text) return;
    const now = Date.now();
    // coalesce a burst of typing into one step; a forced snapshot (send) always gets its own
    if (!force && now - lastSnap.current < 500 && stack.length) stack[stack.length - 1] = text;
    else stack.push(text);
    lastSnap.current = now;
    if (stack.length > 100) stack.shift();
    redoRef.current = [];
    persistUndo();
  }
  function undo() {
    const stack = undoRef.current;
    while (stack.length && stack[stack.length - 1] === msg) stack.pop();
    const prev = stack.pop();
    if (prev === undefined) return;
    redoRef.current.push(msg);
    setMsg(prev);
    persistUndo();
  }
  function redo() {
    const next = redoRef.current.pop();
    if (next === undefined) return;
    undoRef.current.push(msg);
    setMsg(next);
    persistUndo();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "Z")) {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === "y" || e.key === "Y")) { e.preventDefault(); redo(); return; }
    if (menuOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); setMi((i) => (i + 1) % menuItems.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setMi((i) => (i - 1 + menuItems.length) % menuItems.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); accept(mi); return; }
      if (e.key === "Escape") { e.preventDefault(); setTrigger(null); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  // ---- attachments ----
  async function uploadBlob(blob: Blob, filename: string) {
    if (!rootPath) { toast("No project folder to attach into.", "warn"); return; }
    try { const r = await api.wsUploadImage(rootPath, blob, filename); setImages((im) => [...im, r.path]); toast("Image attached", "ok"); }
    catch (e: any) { toast(`Attach failed: ${e.message}`, "danger"); }
  }
  async function onPaste(e: React.ClipboardEvent) {
    const img = Array.from(e.clipboardData.items).find((i) => i.type.startsWith("image/"));
    if (img) { e.preventDefault(); const b = img.getAsFile(); if (b) await uploadBlob(b, `paste.${img.type.split("/")[1] || "png"}`); }
  }
  async function onDrop(e: React.DragEvent) {
    e.preventDefault(); setDropping(false);
    let attached = 0;
    for (const f of Array.from(e.dataTransfer.files)) {
      if (f.type.startsWith("image/")) { await uploadBlob(f, f.name); continue; }
      // Any other file (3D, txt, json, glb, …): resolve its real OS path via the Electron bridge
      // (webUtils.getPathForFile — newer Electron dropped File.path) and reference it in place;
      // in browser mode upload the bytes into the project so Claude can read it.
      const p = ((window as unknown as { studioBridge?: { getPathForFile?: (f: File) => string } })
        .studioBridge?.getPathForFile?.(f) || (f as unknown as { path?: string }).path || "");
      if (p) { setFiles((fs) => (fs.includes(p) ? fs : [...fs, p])); attached++; }
      else if (rootPath) {
        try { const r = await api.wsUploadFile(rootPath, f); setFiles((fs) => [...fs, r.path]); attached++; }
        catch (err: any) { toast(`Couldn't attach ${f.name}: ${err.message}`, "danger"); }
      } else { toast("Open a project folder first to attach files.", "warn"); }
    }
    if (attached) toast(`Attached ${attached} file${attached > 1 ? "s" : ""}`, "ok");
  }

  // ---- send (messages stream into the live session right away, like VS Code) ----
  function done() { setSending(false); onSendingChange?.(false); clearInflight(feedId); if (poll.current) { window.clearInterval(poll.current); poll.current = null; } }
  function startPoll() {
    if (poll.current) window.clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      if (document.hidden) return;   // resume on refocus; the next tick settles the chips
      const s = await api.sessionSending(feedId).catch(() => null);
      if (!s) return;
      if (s.sending) { setSending(true); onSendingChange?.(true); }
      else if (Date.now() - lastSend.current > 1500) { done(); }   // settled → idle: clear the chips
    }, 600);
  }
  // Per-agent saved settings — so "/codex …" uses Codex's own model/effort/approval, not Claude's.
  function agentCfg(a: string) {
    return {
      model: readPref("model", projectId, a, "default"),
      effort: readEffort(projectId, a, readPref("model", projectId, a, "default")),
      mode: readPref("mode", projectId, a, modeFallback(a)),
    };
  }
  async function dispatch(payload: { full: string; images: string[] }, ov?: { agent: string; model: string; effort: string; mode: string }, steer = false) {
    lastSend.current = Date.now();
    setSending(true); onSendingChange?.(true);
    startPoll();
    const useAgent = ov?.agent || agentId;
    const useFeed = ov ? (useAgent === "claude" ? projectId : `${useAgent}--${projectId}`) : feedId;
    try {
      const r = await api.sessionSend(projectId, { message: payload.full, model: ov?.model ?? model, permission_mode: ov?.mode ?? mode, fork: steer ? false : fork, effort: ov?.effort ?? effort, thinking, steer, images: payload.images, agent: useAgent, new_session: newSession, session, path: rootPath || "",
        companions: steer ? [] : companions.filter((c) => c !== useAgent).map((cid) => ({ id: cid, model: compGet(cid, "model", "default"), effort: compGet(cid, "effort", "default"), permission_mode: compGet(cid, "mode", cid === "codex" ? "full" : "default") })) });
      if (!r.ok) {
        toast(r.error || "send failed", "danger");
        // Codex's own banner above the box says what is missing and fixes it; the agent menu
        // only has a command to copy
        if (r.agent === "codex" && (r.needs_login || r.needs_install)) refreshCodex(true);
        else if (r.needs_install) setAgentMenu(true);
        dropInflight(useFeed, payload);
        if (steer) { setMsg((m) => m.trim() ? payload.full + "\n\n" + m : payload.full); setImages((im) => [...new Set([...im, ...payload.images])]); }
        return;
      }
      // A RESPAWN THAT COSTS MONEY, SAID OUT LOUD. Changing the model, the effort, the permission
      // mode, the output style, fast mode or any Chat-helpers toggle changes the request prefix, so
      // the next send re-sends the whole conversation as a CACHE MISS — billed at cache-write rather
      // than cache-read, which is the line a long agent spends most of its money on. The backend
      // reports the reason; this is the only place the user can be told, and the fix on their side is
      // one line: change settings between turns, not during one.
      if ((r as any).respawn_reason === "settings") {
        toast("Setting changed mid-conversation — this answer re-sent the whole context as a cache miss, which bills like a fresh start. Change model / effort / mode between turns, not during one.", "warn");
      }
      if (steer || (r as any).steer_mode) {
        if ((r as any).steer_live) dropInflight(useFeed, payload);
        toast((r as any).steer_mode === "live"
          ? `Steer accepted — ${useAgent === "codex" ? "Codex" : "Claude"} picks it up at its next step`
          : "Steer queued — picked up on the next turn", "ok");
      } else if ((r as any).btw_live) {
        // delivered INTO the running turn via the hook (not stdin) — it never echoes back as a
        // user message in the transcript, so drop the optimistic bubble and confirm instead
        dropInflight(useFeed, payload);
        toast("Side-note accepted — Claude sees it at its next step (watch for the acknowledgment)", "ok");
      } else if ((r as any).btw) {
        // queued path (idle chat, or the hook file couldn't be written): it DOES echo back as a
        // bubble, but confirm it landed AS a side-note so a lost one can never be mistaken for a
        // delivered one — that ambiguity is what made /btw feel like it "wasn't noted".
        toast("Side-note queued — Claude picks it up on its next turn", "ok");
      }
      // BOOST says what THIS prompt saved, instead of leaving the claim on a button. Silent when
      // there was nothing to remove: a toast that says "saved 0 tokens" is noise, and the point
      // of the number is that it is never invented.
      const bsave = (r as any).boost;
      if (bsave?.applied && (bsave.saved_tokens > 0 || bsave.prefetch_lookups > 0)) {
        const bits = [];
        if (bsave.saved_tokens > 0) bits.push(`~${bsave.saved_tokens.toLocaleString()} tokens removed from this prompt`);
        if (bsave.prefetch_lookups > 0) bits.push(`${bsave.prefetch_lookups} symbol${bsave.prefetch_lookups === 1 ? "" : "s"} answered from the local code graph before sending`);
        toast("BOOST: " + bits.join(", ") + ".", "ok");
      }
      onSent?.();
    } catch (e: any) {
      toast(e.message, "danger");
      dropInflight(useFeed, payload);
      if (steer) { setMsg((m) => m.trim() ? payload.full + "\n\n" + m : payload.full); setImages((im) => [...new Set([...im, ...payload.images])]); }
    }
  }
  function send(steer = false) {
    let text = msg.trim();
    // "/codex …" or "/claude …" runs THIS prompt on that agent (one-shot). For /codex, Claude
    // stays idle — Codex does the work and you watch it stream live in the feed below.
    let ov: { agent: string; model: string; effort: string; mode: string } | undefined;
    const am = text.match(/^\/(codex|claude)\b[ \t]*/i);
    if (am) {
      const a = am[1].toLowerCase();
      text = text.slice(am[0].length).trim();
      if (a !== agentId) ov = { agent: a, ...agentCfg(a) };
      // bare "/codex" (no prompt) → just switch the active agent
      if (!text && images.length === 0 && files.length === 0) {
        if (rememberAgent(projectId, a)) setAgentId(a);
        setMsg(""); setTrigger(null);
        return;
      }
    }
    if (!text && images.length === 0 && files.length === 0) return;
    if (/^\/steer(?:\s|$)/i.test(text)) {
      steer = true;
      text = text.replace(/^\/steer(?:\s+|$)/i, "");
      if (!text && images.length === 0 && files.length === 0) return;
    }
    const effAgent = ov?.agent || agentId;
    if (effAgent === "deepseek-harness" && agents.find((a) => a.id === effAgent)?.needs_key) {
      toast("Add the DeepSeek Harness key in Settings → API Keys, then send your prompt.", "warn");
      setTab("settings");
      return;
    }
    const effAvail = effAgent === "claude" ? !disabled
      : effAgent === "codex" && codex.status ? codexReady
      : !!agents.find((x) => x.id === effAgent)?.available;
    if (!effAvail) {
      if (effAgent === "codex") {
        toast(codex.status?.installed ? "Sign in to Codex first — the box above the chat does it in one click"
          : "Install Codex first — the box above the chat does it in one click", "warn");
        if (agentId !== "codex" && rememberAgent(projectId, "codex")) setAgentId("codex");
        refreshCodex(true);
        return;
      }
      toast(`${effAgent} isn't ready — set it up in the agent menu first`, "warn");
      setAgentMenu(true);
      return;
    }
    // /clear: start a fresh conversation instead of round-tripping the command.
    // This used to be gated to Claude, so on any other agent the words "/clear" were sent to the
    // model as an ordinary message — the one command you reach for when a chat has grown too big
    // to answer did nothing on exactly the agents most likely to need it.
    // Commands the stream cannot run. Verified against the CLI: sent headlessly, /subtask comes
    // back "/subtask isn't available in this environment." — so sending it would spend a turn to
    // be told no. Hand it to the terminal, which runs the same session and can.
    if (!steer && onTerminalCommand && /^\/(subtask|fork)/.test(text)) {
      // WHICH ENGINE, THOUGH. This used to run for every engine, and the handler switched the pane
      // to `term:claude` — so typing /subtask on a Codex or DeepSeek pane SILENTLY CHANGED the
      // engine the user had chosen, and the answer arrived in a conversation they were not looking
      // at. The handoff is Claude-only for a real reason: an alternate engine's session lives in its
      // own config home, which the terminal refuses to resume, and Codex/DeepSeek have no such
      // command. On any other engine the words stay in the box with a sentence saying why, instead
      // of being sent, switched or thrown away.
      if (agentId !== "claude") {
        toast(`/${text.slice(1).split(/\s+/)[0]} only runs in a Claude Code terminal — this pane is `
              + `set to ${agent?.name || agentId}. Switch the engine beside the prompt, or rephrase it.`, "warn");
        return;
      }
      setMsg(""); setImages([]); setFiles([]); setTrigger(null);
      onTerminalCommand(text);
      return;
    }
    if (!steer && (text === "/clear" || text === "/new") && onClear) {
      setMsg(""); setImages([]); setFiles([]); setTrigger(null);
      onClear();
      return;
    }
    let full = text || "(see attached)";
    if (files.length) full += "\n\nReferenced files — please Read:\n" + files.map((f, i) => `  ${i + 1}. ${f}`).join("\n");
    const payload = { full, images: [...images] };
    snapshot(msg, true);      // Ctrl+Z after sending brings the message back to edit or re-send
    setMsg(""); setImages([]); setFiles([]); setTrigger(null);   // clear instantly — keep typing
    pushInflight(ov ? (ov.agent === "claude" ? projectId : `${ov.agent}--${projectId}`) : feedId, payload);
    dispatch(payload, ov, steer);
  }
  // Is a CLI process alive for this workspace, whoever started it? `sending` only knows about
  // turns THIS tab sent, so after a reload — or when a turn has wedged and stopped reporting
  // itself as working — the Stop button vanished exactly when it was needed. The backend knows.
  const [liveSession, setLiveSession] = useState(false);
  useEffect(() => {
    if (!projectId) { setLiveSession(false); return; }
    let alive = true;
    const tick = () => api.liveStatus()
      .then((r) => { if (alive) setLiveSession(feedId in (r.statuses || {}) && (agentId !== "claude" || r.agents?.[projectId] === "claude")); })
      .catch(() => {});
    const stop = pollWhileVisible(tick, 5000);
    return () => { alive = false; stop(); };
  }, [feedId]);

  async function stop() { await api.sessionCancel(feedId, agentId).catch(() => {}); done(); }

  // ---- scheduled (delayed) send ----
  const loadSched = () => api.scheduledList()
    .then((r) => { setSched(r.jobs || []); setSchedPaused(!!r.paused); }).catch(() => {});
  function toggleSchedPause() {
    api.schedulePause(!schedPaused).then((r) => {
      setSchedPaused(r.paused);
      toast(r.paused ? "Scheduled messages paused. Nothing is sent until you resume."
        : "Scheduled messages resumed. Any that are overdue are sent now.", "info");
    }).catch((e) => toast(`Could not change it: ${e?.message || e}`, "danger"));
  }
  const loadRoots = () => api.wsRoots().then(setRoots).catch(() => {});
  function scheduleSend() {
    const text = msg.trim();
    if (!text) { toast("Write a prompt first, then schedule it.", "warn"); return; }
    let full = text;
    if (files.length) full += "\n\nReferenced files — please Read:\n" + files.map((f, i) => `  ${i + 1}. ${f}`).join("\n");
    const tgt = schedTarget ? roots.find((r) => r.id === schedTarget) : null;
    const send_at = Date.now() / 1000 + Math.max(1, schedMin) * 60;
    api.scheduleCreate({
      project_id: tgt ? tgt.id : projectId, path: tgt ? tgt.path : (rootPath || ""),
      root_name: tgt ? tgt.name : (rootName || "this workspace"),
      message: full, send_at, model, permission_mode: mode, effort, fork, thinking,
      agent: agentId, new_session: newSession, session, images: [...images],
      repeat_weekdays: repeatDays, repeat_time: repeatDays.length ? repeatTime : "",
    }).then(() => {
      const where = tgt ? tgt.name : (rootName || "this workspace");
      toast(repeatDays.length
        ? `Repeating ${repeatDays.map((d) => DAY_LABELS[d]).join("/")} at ${repeatTime} → ${where}`
        : `Scheduled for ${fmtClock(send_at * 1000)} → ${where}`, "ok");
      setMsg(""); setImages([]); setFiles([]); setSchedOpen(false);
      loadSched();
    }).catch((e: any) => toast(e.message || "schedule failed", "danger"));
  }
  function cancelSched(id: string) { api.scheduleCancel(id).then(() => loadSched()).catch(() => {}); }

  // keep the scheduled badge fresh (jobs fire on their own on the backend)
  useEffect(() => { loadSched(); const iv = window.setInterval(() => { if (!document.hidden) loadSched(); }, 30000); return () => window.clearInterval(iv); }, []);
  function toggleAutolearn() {
    const v = !autolearn;
    setAutolearn(v); localStorage.setItem("cc-autolearn", v ? "1" : "0");
    api.updateSettings({ cc_autolearn: v }).catch(() => {});
    toast(v ? "Auto-learn on — mining your sessions for validated learnings (zero tokens, local LLM); new skills land under learned-* in the Skills tab"
            : "Auto-learn off — mining, installs and the local model all stop", v ? "ok" : "info");
  }
  /** The BOOST button. Optimistic, then corrected by what the backend actually did: the profile
   *  it applies touches other switches, and a button that showed "on" while the backend refused
   *  would be worse than no button. The switches it moved are named out loud — a saving mode that
   *  quietly turns something off is not a setting, it is a surprise. */
  function toggleBoost() {
    if (boostBusy) return;
    const v = !boost;
    setBoost(v); localStorage.setItem("cc-boost", v ? "1" : "0");
    setBoostBusy(true);
    api.boostSet(v)
      .then((b) => {
        setBoostSt(b); setBoost(b.on);
        localStorage.setItem("cc-boost", b.on ? "1" : "0");
        if (b.on) {
          const moved = Object.entries(b.changed || {})
            .filter(([, c]) => c.changed).map(([k]) => k).join(", ");
          // THE RECOMMENDATIONS ARE SAID HERE TOO, not only in Settings. This is the one moment the
          // user is thinking about BOOST, and each of these is a saving BOOST refuses to take on
          // their behalf — because the switch rides the system prompt, so flipping it now would
          // re-send the whole conversation as a cache miss. Naming it, with its reason, is the
          // difference between a smaller saving and a user who can fix it in one click between turns.
          const rec = (b.recommend || []).filter((r) => !r.matches);
          toast("BOOST on — each prompt is compressed on this PC before it leaves"
                + (b.prefetch_ready ? ", and the symbols you name are answered from the local code graph" : "")
                + (moved ? `. It moved: ${moved}` : "")
                + (rec.length
                   ? `. Left alone on purpose: ${rec.map((r) => r.why).join("; ")}. BOOST will not flip `
                     + "those — they ride the system prompt, so changing one mid-conversation re-sends "
                     + "the whole context as a cache miss. Change it in Settings between turns."
                   : ""), "ok");
        } else {
          toast("BOOST off — prompts go as written, and every switch it moved is back where it was.", "info");
        }
      })
      .catch((e: any) => {
        setBoost(!v); localStorage.setItem("cc-boost", !v ? "1" : "0");
        toast(`BOOST could not be switched: ${e?.message || e}`, "danger");
      })
      .finally(() => setBoostBusy(false));
  }
  function toggleMemory() {
    const v = !memory;
    setMemory(v); localStorage.setItem("cc-memory", v ? "1" : "0");
    api.updateSettings({ cc_memory: v }).catch(() => {});
    toast(v ? "Memory ON — I'll read & update the project memory" : "Memory OFF — I won't read or update memory.md (takes effect next message)", v ? "ok" : "info");
  }
  function toggleGraphify() {
    const v = !graphify;
    setGraphify(v); localStorage.setItem("cc-graphify", v ? "1" : "0");
    api.updateSettings({ cc_graphify: v }).catch(() => {});
    toast(v ? "Graphify ON — I'll use the code knowledge-graph for search (takes effect next message)" : "Graphify OFF", v ? "ok" : "info");
  }
  function toggleWebTools() {
    const v = !webTools;
    setWebTools(v); localStorage.setItem("cc-web-tools", v ? "1" : "0");
    api.updateSettings({ cc_web_tools: v }).catch(() => {});
    toast(v ? "Web tools ON — I can read pages that block robots, and search without an API key (takes effect next message)"
            : "Web tools OFF — back to the model's own WebFetch, which a protected site refuses",
          v ? "ok" : "info");
  }
  function toggleCodexFast(v: boolean) {
    setCodexFast(v);
    api.updateSettings({ codex_fast: v }).catch(() => {});
    toast(v ? "Codex Fast on: faster answers, more usage. Applies from your next message." : "Codex Fast off", v ? "ok" : "info");
  }
  async function toggleCodexPlanner(v: boolean) {
    setCodexPlanner(v);
    try {
      await api.updateSettings({ codex_planner: v });
      toast(v ? "Codex Planner on: the next turn will explore and propose a plan."
        : "Codex Planner off: the next turn can implement the plan.", v ? "ok" : "info");
    } catch (e: any) {
      setCodexPlanner(!v);
      toast(e?.message || "Could not save Codex Planner", "danger");
    }
  }
  function toggleFastMode() {
    const v = !fastMode;
    setFastMode(v);
    api.updateSettings({ cc_fast_mode: v }).catch(() => {});
    toast(v ? "Fast mode ON — Opus answers faster (same model). Applies from your next message."
            : "Fast mode OFF", v ? "ok" : "info");
  }
  function applyStyle(id: string) {
    setOstyle(id); localStorage.setItem("cc-output-style", id);
    api.updateSettings({ cc_output_style: id }).catch(() => {});
    const nm = ostyles.find((s) => s.id === id)?.name || id;
    toast(id ? `Output style: ${nm} — applies from your next message` : "Output style off — my normal voice", id ? "ok" : "info");
  }
  function toggleOutputStyle() {
    if (ostyle) { applyStyle(""); return; }
    // ON picks the last style you used, else the bundled ASD-STE100, else whatever is installed
    const last = localStorage.getItem("cc-output-style-last") || "";
    const pick = ostyles.find((s) => s.id === last)?.id || ostyles.find((s) => s.id === "asd-ste100")?.id || ostyles[0]?.id;
    if (!pick) { toast("No output styles installed — drop a .md into ~/.claude/output-styles/", "warn"); return; }
    localStorage.setItem("cc-output-style-last", pick);
    applyStyle(pick);
  }
  // Load the installed styles when the settings popover opens, and re-sync from the backend —
  // it owns what actually gets injected, so it wins over a stale localStorage value.
  useEffect(() => {
    if (!cfg) return;
    let alive = true;
    api.settings().then((st) => { if (alive) setCodexPlanner(!!st?.codex_planner); }).catch(() => {});
    api.outputStyles().then((r) => {
      if (!alive) return;
      setOstyles(r.styles || []);
      if ((r.active || "") !== ostyle) { setOstyle(r.active || ""); localStorage.setItem("cc-output-style", r.active || ""); }
      if (r.active) localStorage.setItem("cc-output-style-last", r.active);
    }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg]);
  // Same for the web tools: show installing…/ready/failed rather than a switch with no feedback.
  useEffect(() => {
    if (!webTools) { setWebStatus(null); return; }
    let alive = true; let iv = 0;
    const f = async () => {
      const s = await api.webStatus().catch(() => null);
      if (!alive || !s) return;
      setWebStatus(s);
      if (s.available && !s.installing) window.clearInterval(iv);
    };
    f(); iv = window.setInterval(() => { if (!document.hidden) f(); }, 4000);
    return () => { alive = false; window.clearInterval(iv); };
  }, [webTools]);
  // While Graphify is on, poll its real install/health so the toggle can show installing…/ready/failed.
  // Stops polling once it's settled (available and not installing).
  useEffect(() => {
    if (!graphify) { setGpyStatus(null); return; }
    let alive = true; let iv = 0;
    const f = async () => {
      const s = await api.graphifyStatus().catch(() => null);
      if (!alive || !s) return;
      setGpyStatus(s);
      if (s.available && !s.installing) window.clearInterval(iv);
    };
    f(); iv = window.setInterval(() => { if (!document.hidden) f(); }, 4000);
    return () => { alive = false; window.clearInterval(iv); };
  }, [graphify]);
  // While auto-learn is on, poll the zero-token miner's status (pending candidates, local-LLM
  // install progress, skills written) so the toggle can show what it's doing.
  useEffect(() => {
    if (!autolearn) { setAlStatus(null); return; }
    let alive = true;
    const f = async () => {
      const s = await api.autolearnStatus().catch(() => null);
      if (alive && s) setAlStatus(s);
    };
    f();
    const iv = window.setInterval(() => { if (!document.hidden) f(); }, 8000);
    return () => { alive = false; window.clearInterval(iv); };
  }, [autolearn]);

  // switching projects (the Workspace reuses one composer) — reset & re-derive real state
  useEffect(() => {
    if (poll.current) { window.clearInterval(poll.current); poll.current = null; }
    setSending(false); onSendingChange?.(false);   // keep inflight chips — they live per-project in the store
    let alive = true;
    api.sessionSending(feedId).then((s) => {
      if (!alive) return;
      if (s.sending) { setSending(true); onSendingChange?.(true); startPoll(); }
      else if ((useStore.getState().inflight[feedId] || []).length) startPoll();   // drain leftover chips
    }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedId]);

  const btn = compact
    ? "inline-flex items-center justify-center h-[34px] w-8 rounded-lg border border-line bg-panel2 hover:bg-line text-muted shrink-0"
    : "btn !px-2 shrink-0";
  const sendBtn = compact ? "h-[34px] px-3 rounded-lg shrink-0" : "btn-primary !px-3 shrink-0";

  // The line under the box while dictation is doing something. Setup reports itself here too,
  // because the first click both sets voice up AND starts listening (see VoiceButton).
  const voiceNote = voiceSt.st === "recording" ? "Listening… speak, then click the mic again to write it in"
    : voiceSt.st === "transcribing" ? "Transcribing…"
    : voiceSt.st === "setup" ? (voiceSt.hint || "Setting up voice…")
    : (!voiceSt.ready && voiceSt.hint) ? voiceSt.hint
    : "";

  return (
    <div className={cls("composer-box relative shrink-0", compact ? "" : "border-t border-line bg-panel/70 p-2")}
      onDragOver={(e) => { if (rootPath) { e.preventDefault(); setDropping(true); } }}
      onDragLeave={() => setDropping(false)} onDrop={onDrop}>

      {/* drag to set how tall the input can grow */}
      <div onPointerDown={startComposerResize}
        className="absolute top-0 left-1/2 -translate-x-1/2 -mt-1 z-20 h-3 w-12 flex items-center justify-center cursor-ns-resize touch-none group/grip"
        title="Drag to resize the message box">
        <div className="h-1 w-9 rounded-full bg-line group-hover/grip:bg-brand transition-colors" />
      </div>

      {dropping && (
        <div className="absolute inset-1 z-30 rounded-lg border-2 border-dashed border-brand bg-brand/10 flex items-center justify-center text-brand text-xs pointer-events-none">
          Drop image to attach
        </div>
      )}

      {/* COMMENTS WAITING ON A DIFF. They are written on the lines, up in the feed, and collected
          here — one batch, one message, instead of one interruption per remark. "Send" puts them
          in the box rather than firing them, so a sentence can still be added on top. */}
      {myNotes.length > 0 && (
        <div className="mb-1.5 flex items-center gap-2 rounded border border-brand/40 bg-brand/5 px-2 py-1 text-[11px]">
          <MessageSquarePlus size={12} className="text-brand shrink-0" />
          <span className="text-text/85 tabular-nums shrink-0">
            {myNotes.length} comment{myNotes.length === 1 ? "" : "s"} on the diff
          </span>
          <span className="text-muted truncate min-w-0">{myNotes[myNotes.length - 1].note}</span>
          <button className="ml-auto chip shrink-0 hover:border-brand hover:text-brand"
            onClick={() => { insertIntoComposer(projectId, notesToPrompt(myNotes)); clearDiffNotes(projectId); }}>
            Put in the message
          </button>
          <button className="chip shrink-0 hover:text-text" title="Throw the comments away"
            onClick={() => clearDiffNotes(projectId)}><X size={11} /></button>
        </div>
      )}

      {/* autocomplete menu */}
      {menuOpen && (
        <div className="absolute bottom-12 left-2 right-2 z-50 card p-1 max-h-56 overflow-auto shadow-card">
          <div className="px-2 py-1 text-[10px] text-muted flex items-center gap-1">
            {trigger?.type === "file" ? <><AtSign size={10} /> add file as context</> : <><Hash size={10} /> slash commands</>}
          </div>
          {menuItems.map((it, i) => (
            <button key={it.label + i}
              className={cls("w-full text-left px-2 py-1 rounded flex items-center gap-2 text-xs", i === mi ? "bg-brand/15" : "hover:bg-panel2")}
              onMouseEnter={() => setMi(i)} onClick={() => accept(i)}>
              {trigger?.type === "file" ? <FileText size={12} className="text-muted shrink-0" /> : <Terminal size={12} className="text-brand shrink-0" />}
              <span className="font-mono shrink-0">{it.label}</span>
              <span className="text-muted truncate">{it.sub}</span>
              {it.tag && <span className="chip ml-auto shrink-0 text-[9px]">{it.tag}</span>}
            </button>
          ))}
        </div>
      )}

      {/* settings popover */}
      {cfg && (
        // Anchored to the BOTTOM of the composer, not the top.
        //
        // `bottom-full` put every one of these menus above the whole box — and the box grows
        // upward as you type, so the same menu opened in a different place depending on how much
        // you had written. With a long draft it opened near the top of the window, far from the
        // button that opens it. Measuring from the bottom edge, which does not move, means it
        // opens in the same place every time; a long menu now overlays the draft instead of
        // running away from the cursor.
        //
        // It also grows upward and had already outgrown a 900px-tall window: the Model select was
        // clipped behind the nav with no way to reach it. Cap it and scroll.
        <div className="absolute bottom-12 left-2 z-40 card p-2 w-60 space-y-2 text-xs shadow-card max-h-[70vh] overflow-y-auto">
          <div><label className="label !mb-0.5">Model</label>
            <select className="input !py-1 text-xs" value={model} onChange={(e) => pickModel(e.target.value)}>
              {modelsFor(agentId).map(([v, l]) => {
                const b = blockedModel(v);
                return <option key={v} value={v} title={b?.reason}>{l}{b ? " — no access yet" : ""}</option>;
              })}</select>
            {blockedModel(model) && (
              <p className="text-[10px] text-warn/90 leading-snug mt-1">
                {blockedModel(model)!.reason} The Studio found that out from the last turn and will
                try again by itself once a day.
              </p>)}</div>
          {/* Look — per project, because a workspace is where you read the conversation. It paints
              this pane only: the nav, the status bar and every other project stay as they are.
              When the whole studio is already in the console look there is nothing to switch on,
              so say that instead of showing a control that cannot do anything. */}
          {cliLook !== undefined && (studioCli ? (
            <div className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5">
              <SquareTerminal size={14} className="shrink-0 text-brand" />
              <span className="text-left">
                <span className="block text-xs font-medium">CLI look</span>
                <span className="block text-[10px] text-muted/70 leading-tight">on for the whole studio · Settings &rarr; Look</span>
              </span>
            </div>
          ) : (
            <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2"
              title="Draw this workspace the way Claude Code draws it in a console — same chat, same project, same keys. Safe in the middle of a turn."
              onClick={() => setProjectCli(projectId, !projectCli)}>
              <SquareTerminal size={14} className={cls("shrink-0", projectCli ? "text-brand" : "text-muted/60")} />
              <span className="text-left min-w-0">
                <span className={cls("block text-xs font-medium", !projectCli && "text-muted")}>CLI look</span>
                <span className="block text-[10px] text-muted/70 leading-tight truncate">console look for {rootName || "this project"}</span>
              </span>
              <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", projectCli ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                <span className="h-4 w-4 rounded-full bg-white shadow" />
              </span>
            </button>
          ))}
          {/* HOW MUCH OF A COMMAND'S OUTPUT THIS CHAT SHOWS. Here because this is where the wall
              of output is when it annoys you — Settings is for when you go looking on purpose.
              One store field, so the two controls cannot disagree. */}
          <div className="w-full rounded-md border border-line bg-panel2/40 px-2 py-1.5">
            <div className="flex items-center gap-2">
              <Terminal size={14} className="shrink-0 text-muted/60" />
              <span className="text-xs font-medium">Command output</span>
            </div>
            <div className="mt-1.5 flex rounded-md border border-line overflow-hidden">
              {FEED_DETAILS.map((d) => (
                <button key={d.id} type="button" onClick={() => setFeedDetail(d.id)}
                  title={d.hint}
                  className={cls("flex-1 px-2 py-1 text-[11px] transition-colors",
                    feedDetail === d.id ? "bg-brand-600 text-white" : "text-muted hover:bg-panel2")}>
                  {d.label}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-muted/70 leading-tight mt-1">{detailSummary(feedDetail)}</p>
          </div>
          {/* set-once preferences, moved out of the input row so it stops feeling crowded */}
          <div className="flex items-center justify-between gap-2">
            <label className="label !mb-0">Voice language</label>
            <button className="chip !text-[10px]"
              title={voiceTask === "translate"
                ? "Speech is auto-translated to English (any language, incl. Greek). Click to keep the spoken language."
                : "Speech is kept as spoken. Click to auto-translate it to English."}
              onClick={() => { const v = voiceTask === "translate" ? "transcribe" : "translate"; setVoiceTask(v); localStorage.setItem("cc-voice-task", v); }}>
              {voiceTask === "translate" ? "→ English" : "as spoken"}
            </button>
          </div>
          {agentId === "claude" && (
            <label className="flex items-center gap-1.5 cursor-pointer text-muted"
              title={alStatus
                ? `${alStatus.pending} queued · ${alStatus.skills_written} saved this run · ${alStatus.llm.available ? "local LLM ready" : alStatus.llm.installing ? "local LLM installing" : "local LLM not set up"}`
                : "Mine your sessions for validated learnings and save them as skills (zero tokens: local harvester + local LLM)"}>
              <input type="checkbox" checked={autolearn} onChange={toggleAutolearn} /> auto-learn skills
            </label>
          )}
          {/* BOOST runs on EVERY engine — Claude, Codex and DeepSeek — so it sits outside the
              Claude-only block below. The same switch as the button in the row; this is the one
              that shows the running total. */}
          <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2"
            disabled={boostBusy} onClick={toggleBoost}
            title={"Nothing above the provider's prompt-cache breakpoint is ever rewritten — only the message you are sending, which sits below it. The local code graph is asked only when a graph for this project already exists."
              + (boost && boostSt && !boostSt.prefetch_ready
                ? " The code graph switch is OFF, so that lookup is doing nothing right now: turn on \"Code graph: answer symbols locally\" in Settings → Studio engine to get it — BOOST will not flip that switch behind your back, because changing it mid-conversation re-sends the whole context as a cache miss."
                : "")}>
            <Zap size={14} className={cls("shrink-0", boost ? "text-warn" : "text-muted/60")} />
            <span className="text-left min-w-0">
              <span className={cls("block text-xs font-medium", !boost && "text-muted")}>BOOST — spend less per prompt</span>
              <span className="block text-[10px] text-muted/70 leading-tight truncate">
                {boost
                  ? `${(boostSt?.saved_tokens || 0).toLocaleString()} tokens saved · ${boostSt?.messages || 0} prompt${boostSt?.messages === 1 ? "" : "s"} · ${boostSt?.prefetched || 0} local lookups`
                    + (boostSt && !boostSt.prefetch_ready ? " · code graph is off" : "")
                  : "compress here first · use the code graph if it is on · apply the two free switches"}
              </span>
            </span>
            <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", boost ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
              <span className="h-4 w-4 rounded-full bg-white shadow" />
            </span>
          </button>
          {(agentId === "claude" || isAltEngine(agent)) && <>
            <div><label className="label !mb-0.5 flex items-center gap-1">Effort <span className="text-muted/60 font-normal normal-case">· thinking depth</span></label>
              <select className="input !py-1 text-xs" value={effort} onChange={(e) => pickEffort(e.target.value)}>
                {EFFORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
            {/* extended-thinking toggle — maps to Claude Code's `ultrathink` (max thinking budget per message) */}
            <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2"
              onClick={() => { const v = !thinking; setThinking(v); localStorage.setItem("cc-thinking", v ? "1" : "0"); }}>
              <Brain size={14} className={cls("shrink-0", thinking ? "text-brand" : "text-muted/60")} />
              <span className="text-left">
                <span className={cls("block text-xs font-medium", !thinking && "text-muted")}>Thinking</span>
                <span className="block text-[10px] text-muted/70 leading-tight">force max thinking each message</span>
              </span>
              <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", thinking ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                <span className="h-4 w-4 rounded-full bg-white shadow" />
              </span>
            </button>
            {/* memory.md on/off — when off, I'm told not to read/use/update the persistent memory */}
            <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2" onClick={toggleMemory}>
              <Database size={14} className={cls("shrink-0", memory ? "text-brand" : "text-muted/60")} />
              <span className="text-left">
                <span className={cls("block text-xs font-medium", !memory && "text-muted")}>Memory.md</span>
                <span className="block text-[10px] text-muted/70 leading-tight">read &amp; update persistent memory</span>
              </span>
              <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", memory ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                <span className="h-4 w-4 rounded-full bg-white shadow" />
              </span>
            </button>
            {/* fast mode — Opus, faster output. Opus-only, so the row states which models it covers. */}
            <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2"
              onClick={toggleFastMode}
              title={"Fast mode runs Claude Opus with faster output — it does NOT swap to a smaller model.\n"
                + "Applies to: Opus 5.5, Opus 5, Opus 5 [1m], Opus 4.8.\n"
                + "Fable, Sonnet and Haiku ignore it (verified against the CLI)."}>
              <Zap size={14} className={cls("shrink-0", fastMode ? "text-brand" : "text-muted/60")} />
              <span className="text-left min-w-0">
                <span className={cls("block text-xs font-medium", !fastMode && "text-muted")}>Fast mode</span>
                <span className={cls("block text-[10px] leading-tight",
                  fastMode && !FAST_MODELS.test(model) ? "text-warn/90" : "text-muted/70")}>
                  {fastMode && !FAST_MODELS.test(model)
                    ? `on — but ${model === "default" ? "the default model" : model} ignores it (Opus only)`
                    : "Opus, faster output · same model"}
                </span>
              </span>
              <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", fastMode ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                <span className="h-4 w-4 rounded-full bg-white shadow" />
              </span>
            </button>
            {/* graphify — prefer the code knowledge-graph over broad grep (cheaper context) */}
            <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2" onClick={toggleGraphify}>
              <Network size={14} className={cls("shrink-0", graphify ? "text-brand" : "text-muted/60")} />
              <span className="text-left">
                <span className={cls("block text-xs font-medium", !graphify && "text-muted")}>Graphify</span>
                <span className={cls("block text-[10px] leading-tight",
                  graphify && gpyStatus?.error ? "text-danger/80"
                  : graphify && gpyStatus?.installing ? "text-warn/90" : "text-muted/70")}>
                  {graphify && gpyStatus?.installing ? "installing graphify…"
                    : graphify && gpyStatus?.error ? `install failed — ${gpyStatus.error.slice(0, 42)}`
                    : graphify && gpyStatus?.available ? "ready · code knowledge-graph"
                    : "search via the code knowledge-graph"}
                </span>
              </span>
              <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", graphify ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                <span className="h-4 w-4 rounded-full bg-white shadow" />
              </span>
            </button>
            <button className="w-full flex items-center gap-2 rounded-md border border-line bg-panel2/40 px-2 py-1.5 hover:bg-panel2" onClick={toggleWebTools}>
              <Globe size={14} className={cls("shrink-0", webTools ? "text-brand" : "text-muted/60")} />
              <span className="text-left">
                <span className={cls("block text-xs font-medium", !webTools && "text-muted")}>Web tools</span>
                <span className={cls("block text-[10px] leading-tight",
                  webTools && webStatus?.error ? "text-danger/80"
                  : webTools && webStatus?.installing ? "text-warn/90" : "text-muted/70")}>
                  {webTools && webStatus?.installing ? "installing the fetcher…"
                    : webTools && webStatus?.error ? `install failed — ${webStatus.error.slice(0, 42)}`
                    : webTools && webStatus?.available ? "ready · reads blocked pages, keyless search"
                    : "read pages that block robots"}
                </span>
              </span>
              <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", webTools ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                <span className="h-4 w-4 rounded-full bg-white shadow" />
              </span>
            </button>
            {/* output style — swaps my writing voice for a controlled one (ASD-STE100 &c.) */}
            <div className="rounded-md border border-line bg-panel2/40 overflow-hidden">
              <button className="w-full flex items-center gap-2 px-2 py-1.5 hover:bg-panel2" onClick={toggleOutputStyle}
                title="A system-prompt overlay that changes HOW I write — never what I can do. Same ~/.claude/output-styles/*.md files as /output-style. Costs a few hundred tokens per message while on.">
                <Languages size={14} className={cls("shrink-0", ostyle ? "text-brand" : "text-muted/60")} />
                <span className="text-left min-w-0">
                  <span className={cls("block text-xs font-medium", !ostyle && "text-muted")}>Output style</span>
                  <span className="block text-[10px] text-muted/70 leading-tight truncate">
                    {ostyle ? (ostyles.find((s) => s.id === ostyle)?.name || ostyle) : "my normal voice"}
                  </span>
                </span>
                <span className={cls("ml-auto flex items-center h-5 w-9 rounded-full transition-colors px-0.5 shrink-0", ostyle ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                  <span className="h-4 w-4 rounded-full bg-white shadow" />
                </span>
              </button>
              {/* The style's own description used to print here, in full. It is a paragraph of
                  prose about a setting you have already turned on, and the row above it already
                  names the style — so it said nothing new and pushed everything below it down.
                  The description still lives in the style file, where the CLI reads it. */}
              {ostyle && ostyles.length > 1 && (
                <div className="px-2 pb-1.5">
                  <select className="input !py-1 text-xs" value={ostyle}
                    onChange={(e) => { localStorage.setItem("cc-output-style-last", e.target.value); applyStyle(e.target.value); }}>
                    {ostyles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </div>
              )}
            </div>
            <div><label className="label !mb-0.5">Permission mode</label>
              <select className="input !py-1 text-xs" value={forcePlan ? "plan" : mode} disabled={forcePlan}
                title={forcePlan ? "Settings \u2192 Planning forces plan mode for every message" : ""}
                onChange={(e) => { writePref("mode", projectId, agentId, e.target.value); setMode(e.target.value); }}>
                {MODES.map((m) => <option key={m} value={m}>{m}</option>)}</select>
              {forcePlan && <div className="text-[10px] text-muted/70 mt-0.5">forced in Settings &rarr; Planning</div>}</div>
            <label className="flex items-center gap-1.5 cursor-pointer text-muted">
              <input type="checkbox" checked={fork} onChange={(e) => { setFork(e.target.checked); localStorage.setItem("cc-fork", e.target.checked ? "1" : "0"); }} /> new session (fork)
            </label>
          </>}
          {agentId === "codex" && (
            <CodexSettings status={codex.status} models={codex.models} def={codex.def} model={model}
              effort={effort} mode={mode} fast={codexFast} planner={codexPlanner} rootPath={rootPath}
              onEffort={pickEffort} onFast={toggleCodexFast} onPlanner={toggleCodexPlanner}
              onMode={(v) => { writePref("mode", projectId, agentId, v); setMode(v); }} />
          )}
          {agentId === "deepseek-harness" && <div className="text-[11px] text-muted space-y-1">
            <div>Permissions: <strong className="text-text">Full access</strong></div>
            <p>The native SDK can run commands and edit files with your user permissions.</p>
            <button className="underline text-brand" onClick={() => setTab("settings")}>Settings → API Keys → DeepSeek Harness</button>
          </div>}
          {agentId !== "claude" && agentId !== "codex" && <p className="text-[10px] text-muted/70 leading-snug">{agent?.note}</p>}
          {companions.filter((c) => c !== agentId).length > 0 && (
            <div className="border-t border-line pt-1.5 mt-0.5 space-y-2" key={`comp-${compNonce}`}>
              <div className="text-[10px] text-muted uppercase tracking-wide">Co-agents · run together &amp; review each other</div>
              {companions.filter((c) => c !== agentId).map((cid) => { const ca = agents.find((a) => a.id === cid); return (
                <div key={cid} className="space-y-1">
                  <div className="text-[11px] font-medium text-text flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full shrink-0" style={{ background: ca?.color || "#888" }} /> {ca?.name || cid}
                    {ca && !ca.available && <span className="chip text-[9px] text-muted ml-1">not installed</span>}
                  </div>
                  <select className="input !py-1 text-xs w-full" value={compGet(cid, "model", "default")} onChange={(e) => compSet(cid, "model", e.target.value)}>
                    {modelsFor(cid).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                  {cid === "codex" && (
                    <div className="flex gap-1">
                      <select className="input !py-1 text-xs flex-1" title="reasoning effort" value={compGet(cid, "effort", "default")} onChange={(e) => compSet(cid, "effort", e.target.value)}>
                        {codexEffortRows(codex.models, codex.def, compGet(cid, "model", "default"), compGet(cid, "effort", "default")).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                      <select className="input !py-1 text-xs flex-1" title="access (a co-agent review never edits)" value={codexModeOf(compGet(cid, "mode", "full"))} onChange={(e) => compSet(cid, "mode", e.target.value)}>
                        {CODEX_ACCESS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    </div>
                  )}
                </div>
              ); })}
            </div>
          )}
        </div>
      )}

      {/* agent picker menu */}
      {agentMenu && (
        <div className="absolute bottom-12 left-2 z-40 card p-1.5 w-72 space-y-0.5 text-xs shadow-card">
          <div className="px-1.5 py-1 text-[10px] text-muted">Send this chat with…</div>
          {agents.map((a) => (
            <div key={a.id} className={cls("rounded px-1.5 py-1.5", a.id === agentId && "bg-panel2")}>
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: a.color }} />
                <button className="font-medium hover:text-brand disabled:opacity-50" disabled={!a.available}
                  onClick={() => { setLS("cc-agent", a.id, setAgentId); setAgentMenu(false); }}>{a.name}</button>
                <div className="ml-auto flex items-center gap-1.5 shrink-0">
                  {a.id !== agentId && a.id !== "deepseek-harness" && agentId !== "deepseek-harness" && (
                    <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer"
                      title="Run together with the primary agent — they see & review each other's messages">
                      <input type="checkbox" checked={companions.includes(a.id)} onChange={() => toggleCompanion(a.id)} /> co-run
                    </label>
                  )}
                  {a.available
                    ? <span className="chip text-ok text-[9px]"><Check size={9} /> installed</span>
                    : <span className="chip text-muted text-[9px]">not installed</span>}
                </div>
              </div>
              {!a.available && (
                <div className="mt-1 flex items-center gap-1 pl-4">
                  <code className="flex-1 truncate bg-bg rounded px-1.5 py-1 text-[10px] text-muted font-mono">{a.install_cmd}</code>
                  <button className="p-1 rounded hover:bg-line text-muted" title="Copy install command"
                    onClick={() => { navigator.clipboard?.writeText(a.install_cmd); toast("Install command copied", "ok"); }}><Copy size={12} /></button>
                  <a className="p-1 rounded hover:bg-line text-muted" title="Open docs" href={a.install_url} target="_blank" rel="noreferrer"><ExternalLink size={12} /></a>
                </div>
              )}
            </div>
          ))}
          <p className="px-1.5 pt-1 text-[10px] text-muted/70 leading-snug">Claude and Codex stream live into the feed, each with its own conversation. Others run headless in the project folder; their output shows in the agent console.</p>

          {/* Terminal-only CLIs. Picking one turns this pane INTO that agent: its own
              full-screen interface, no composer. Stored under a "term:" prefix so no code
              path that expects a streaming engine can ever pick it up by accident. */}
          {termAgents.length > 0 && (
            <>
              <div className="px-1.5 pt-2 pb-1 text-[10px] text-muted uppercase tracking-wide border-t border-line mt-1">
                Or open this in a terminal
              </div>
              {termAgents.map((t) => {
                // Claude Code is the one that keeps the conversation. It was hidden here until
                // the backend learned to resume: a bare `claude` opens a BLANK chat, which is
                // the one thing you never want from a button sitting next to your history.
                const mine = t.id === "claude";
                return (
                  <div key={t.id} className="rounded px-1.5 py-1.5 flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: t.color }} />
                    <button className="font-medium hover:text-brand disabled:opacity-50 truncate"
                      disabled={!t.installed}
                      title={mine ? "Open THIS conversation in a real Claude Code terminal — same session, same project. Real slash commands and the real plan dialog. Everything you type there is back in this feed when you return."
                                  : t.note}
                      onClick={() => { setLS("cc-agent", "term:" + t.id, setAgentId); setAgentMenu(false); }}>
                      {t.name}
                    </button>
                    <span className={cls("ml-auto chip text-[9px] shrink-0", mine && t.installed ? "text-brand" : "text-muted")}>
                      {!t.installed ? "not installed" : mine ? "same conversation" : "own terminal"}
                    </span>
                  </div>
                );
              })}
              <p className="px-1.5 pt-1 text-[10px] text-muted/70 leading-snug">
                These replace this chat with their own interface. Claude Code carries this
                conversation across and brings it back; the others start their own. Install any
                missing one in Settings &rarr; Coding agents.
              </p>
            </>
          )}
        </div>
      )}

      {/* schedule popover — set a delay + target workspace; the backend fires it via send() */}
      {schedOpen && (
        <div className="absolute bottom-12 right-2 z-40 card p-2.5 w-72 space-y-2 text-xs shadow-card">
          <div className="flex items-center gap-1.5 font-semibold text-text"><Clock size={13} className="text-brand" /> Schedule this prompt</div>
          <div>
            <label className="label !mb-1">Send in</label>
            <div className="flex flex-wrap gap-1">
              {SCHED_PRESETS.map(([m, l]) => (
                <button key={m} onClick={() => setSchedMin(m)}
                  className={cls("px-2 py-1 rounded-md border text-[11px]", schedMin === m ? "border-brand bg-brand/15 text-brand" : "border-line hover:bg-panel2 text-muted")}>{l}</button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 mt-1.5">
              <input type="number" min={1} value={schedMin}
                onChange={(e) => setSchedMin(Math.max(1, Number(e.target.value) || 1))}
                className="input !py-1 w-16 text-xs" />
              <span className="text-muted">minutes from now</span>
            </div>
            <div className="text-[11px] text-muted mt-1">&rarr; sends at <b className="text-text">{fmtClock(Date.now() + schedMin * 60000)}</b></div>
          </div>
          <div>
            {/* Pick no days and this stays exactly the one-shot it has always been. */}
            <label className="label !mb-1">Repeat on</label>
            <div className="flex gap-1">
              {DAY_LABELS.map((lbl, i) => {
                const on = repeatDays.includes(i);
                return (
                  <button key={i} title={FULL_DAYS[i]}
                    onClick={() => setRepeatDays((d) => on ? d.filter((x) => x !== i) : [...d, i].sort((a, b) => a - b))}
                    className={cls("w-7 py-1 rounded-md border text-[11px]",
                      on ? "border-brand bg-brand/15 text-brand" : "border-line hover:bg-panel2 text-muted")}>
                    {lbl}
                  </button>
                );
              })}
            </div>
            {repeatDays.length > 0 ? (
              <div className="flex items-center gap-1.5 mt-1.5">
                <span className="text-[11px] text-muted">at</span>
                <input type="time" value={repeatTime} onChange={(e) => setRepeatTime(e.target.value)}
                  className="input !py-1 w-24 text-xs" />
                <span className="text-[11px] text-muted">every week</span>
              </div>
            ) : (
              <div className="text-[10px] text-muted/70 mt-1">No days picked — sends once.</div>
            )}
          </div>
          <div>
            <label className="label !mb-1">Workspace</label>
            <select className="input !py-1 text-xs w-full" value={schedTarget} onChange={(e) => setSchedTarget(e.target.value)}>
              <option value="">{rootName || "Current"} (current)</option>
              {roots.filter((r) => r.id !== projectId).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </div>
          <button className="btn-primary w-full !py-1.5 text-xs inline-flex items-center justify-center gap-1" onClick={scheduleSend} disabled={!msg.trim()}>
            <Clock size={13} /> Schedule send
          </button>
          {pendingCount > 0 && (
            <div className="border-t border-line pt-1.5 space-y-1 max-h-44 overflow-auto">
              <div className="flex items-center text-[10px] text-muted uppercase tracking-wide">
                <span className="flex-1">Scheduled ({pendingCount}){schedPaused ? " · paused" : ""}</span>
                <button onClick={toggleSchedPause} className={schedPaused ? "text-warn hover:text-text normal-case" : "hover:text-text normal-case"}
                  title="Pause or resume every scheduled and recurring message">
                  {schedPaused ? "Resume all" : "Pause all"}
                </button>
              </div>
              {sched.filter((j) => j.status === "pending").map((j) => (
                <div key={j.id} className="flex items-center gap-1.5 bg-panel2/50 rounded px-1.5 py-1">
                  <Clock size={10} className="text-brand shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-text/85">{j.message.split("\n")[0]}</div>
                    <div className="text-[10px] text-muted/70 truncate">{j.root_name || "workspace"} &middot; {fmtClock(j.send_at * 1000)}</div>
                  </div>
                  <button onClick={() => cancelSched(j.id)} className="text-muted hover:text-danger shrink-0" title="Cancel"><X size={11} /></button>
                </div>
              ))}
            </div>
          )}
          {!msg.trim() && <p className="text-[10px] text-muted/70">Type a prompt above first, then schedule it.</p>}
        </div>
      )}

      {/* in-flight messages — ALL already delivered into the live session; these drain as Claude answers */}
      {inflight.length > 0 && (
        <div className="flex flex-col gap-1 mb-2">
          {inflight.map((q, i) => (
            <div key={i} className="flex items-center gap-1.5 text-xs bg-panel2/60 border border-line rounded px-2 py-1">
              {i === 0
                ? <Loader2 size={11} className="text-brand shrink-0 animate-spin" />
                : <Check size={11} className="text-ok shrink-0" />}
              <span className="truncate flex-1 text-text/80">{q.full.split("\n")[0]}</span>
              <span className="text-[10px] text-muted/60 shrink-0">{i === 0 ? "working…" : "sent · Claude picks this up next"}</span>
            </div>
          ))}
        </div>
      )}

      {/* attachment chips */}
      {(images.length > 0 || files.length > 0) && (
        <div className="flex gap-1.5 flex-wrap mb-2">
          {images.map((p, i) => (
            <div key={p} className="relative">
              <img src={api.wsRaw(p, rootPath)} className="h-12 w-12 object-cover rounded border border-line" />
              <button className="absolute -top-1 -right-1 bg-panel border border-line rounded-full p-0.5 text-muted hover:text-danger"
                onClick={() => setImages((im) => im.filter((_, j) => j !== i))}><X size={10} /></button>
            </div>
          ))}
          {files.map((p, i) => (
            <span key={p} className="chip gap-1 max-w-[180px]">
              <FileText size={11} className="shrink-0" />
              <span className="truncate">{p.split(/[\\/]/).pop()}</span>
              <button className="hover:text-danger" onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))}><X size={10} /></button>
            </span>
          ))}
        </div>
      )}

      {/* Codex that cannot answer yet says why, and fixes it: install, or sign in */}
      {agentId === "codex" && codex.status && (!codex.status.installed || !codex.status.ready) && (
        <CodexSignIn status={codex.status} />
      )}

      <div className="composer-row flex items-end gap-1.5">
        {/* agent picker button */}
        <button className={cls(btn, "relative")} title={`Agent: ${agent?.name || agentId}${companions.filter((c) => c !== agentId).length ? ` + ${companions.filter((c) => c !== agentId).join(", ")} (co-run)` : ""}`} onClick={() => { setAgentMenu((v) => !v); setCfg(false); }}>
          <Bot size={15} />
          <span className="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border border-panel" style={{ background: agent?.color || "#888" }} />
          {companions.filter((c) => c !== agentId).length > 0 && <span className="absolute -top-1 -right-1 min-w-[14px] h-[14px] px-0.5 rounded-full bg-accent text-[9px] text-white flex items-center justify-center font-semibold">+{companions.filter((c) => c !== agentId).length}</span>}
        </button>
        {/* (the Claude|Codex quick-switch lived here — removed: the agent button above already
            picks the agent, and it cost the input ~90px of width for a duplicate control) */}
        {/* BOOST — the money switch, in the row rather than buried in the popover, because it is
            the one control you flip for a whole session and the number it carries is the point.
            It reports what this PC has ACTUALLY removed so far, never a promise. */}
        <button
          className={cls(btn, "relative gap-1", boost
            ? "!text-warn !border-warn/60 !bg-warn/10"
            : "opacity-65 hover:opacity-100")}
          disabled={boostBusy}
          title={boost
            ? `BOOST on — ${(boostSt?.saved_tokens || 0).toLocaleString()} tokens removed from `
              + `${boostSt?.messages || 0} prompt${boostSt?.messages === 1 ? "" : "s"}, `
              + `${boostSt?.prefetched || 0} local code-graph lookups. Each prompt is compressed on `
              + "this PC before it leaves."
              + (boostSt && !boostSt.prefetch_ready
                ? " The code graph switch is off, so those lookups are doing nothing — BOOST will not "
                  + "flip it for you (it changes the system prompt, which costs a cache re-write), so "
                  + "turn it on in Settings → Studio engine between conversations."
                : "")
              + " Click to switch off."
            : "BOOST off. On: every prompt is compressed on this PC before it is sent, the symbols "
              + "you name are answered from the local code graph when one exists, one constant note "
              + "asks the agent to work that way, and the two switches that are free to move are "
              + "applied — the same answer for fewer credits."}
          onClick={toggleBoost}>
          <Zap size={15} className={cls(boostBusy && "animate-pulse")} />
          {!compact && <span className="text-[10px] font-semibold tracking-wider">BOOST</span>}
        </button>
        {/* settings */}
        <button className={cls(btn, "relative", autolearn && "!text-brand/90")}
          title={`${model}${(agentId === "claude" || isAltEngine(agent)) ? ` · ${effort} · ${mode}` : ""}${autolearn ? " · auto-learn on" : ""}`}
          onClick={() => { setCfg((v) => !v); setAgentMenu(false); }}>
          <SlidersHorizontal size={15} />
          {/* auto-learn moved into this popover; its live "working" pulse rides the settings
              button so the signal survives without costing a permanent slot in the row */}
          {autolearn && (alStatus?.llm?.installing || alStatus?.distilling || alStatus?.scanning) && (
            <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-warn animate-pulse" />
          )}
        </button>
        {/* the CLI draws a "> " inside the prompt box; the wrapper only positions that glyph —
            the textarea keeps its own auto-grow height, so nothing about typing changes */}
        <div className="composer-field relative flex-1 min-w-0 flex">
        {cli && (
          <span className="absolute left-3 top-2 text-brand select-none pointer-events-none leading-[1.45]">&gt;</span>
        )}
        <textarea ref={ta}
          className={cls("input w-full resize-none", cli ? "!pl-7 cli-prompt-box" : "font-sans", compact ? "text-xs !py-2" : "text-sm")}
          style={{ minHeight: minH, maxHeight: maxH }}
          // the full hint wraps to three lines in a Mission-Control card and eats the chat area,
          // so the compact variant gets a short one (the long form stays in the Workspace panel)
          placeholder={sending
            ? (compact ? "working… Enter queues another" : "working… type & Enter to send another (it'll be picked up next)")
            : (compact ? `Message ${rootName || "this session"}…` : menuHint(agentId, agent?.name, rootName, isAltEngine(agent) || agentId === "codex"))}
          value={msg} onPaste={onPaste} onChange={onChange} onKeyDown={onKeyDown}
          onFocus={() => { setCfg(false); setAgentMenu(false); setSchedOpen(false); }} />
        </div>
        {/* VOICE, IN THE BAR, WITH THE LANGUAGE BESIDE IT. The mic used to draw a download arrow
            until the model was resident, which is exactly how a first-time user concluded there
            was no voice input here; and the language choice sat in the settings popover, a menu
            away from the words it decides. Both are now where the prompt is typed. */}
        <VoiceButton className={btn} task={voiceTask} compact={compact} onStatus={setVoiceSt}
          onTask={(v) => { setVoiceTask(v); localStorage.setItem("cc-voice-task", v); }}
          onText={(t) => { setMsg((m) => (m && !/\s$/.test(m) ? m + " " : m) + t); setTimeout(() => ta.current?.focus(), 0); }} />
        <label className={cls(btn, "cursor-pointer", !rootPath && "opacity-40 pointer-events-none")} title="Attach image (or drag & drop / paste)">
          <Paperclip size={15} />
          <input type="file" accept="image/*" multiple className="hidden"
            onChange={(e) => { Array.from(e.target.files || []).forEach((f) => uploadBlob(f, f.name)); e.currentTarget.value = ""; }} />
        </label>
        {(sending || liveSession) && (
          <button className={cls(btn, "text-danger border border-danger/40 bg-danger/10")}
            onClick={stop}
            title={sending ? "Stop the current turn"
                           : "Stop the session running in this workspace"}><Square size={15} /></button>
        )}
        {/* schedule this prompt to send later */}
        {(agentId === "claude" || agentId === "codex") && (
          <button className={cls(btn, "!w-auto !px-2 gap-1 !text-accent border border-accent/40 bg-accent/10")}
            aria-label="Steer" onClick={() => send(true)}
            disabled={!sending || !agentAvail || newSession || (!msg.trim() && images.length === 0 && files.length === 0)}
            title={`Steer ${agent?.name || agentId}: send this correction into the running turn at its next step`}>
            <CornerDownRight size={15} /><span className="text-[10px] font-semibold">Steer</span>
          </button>
        )}
        <button className={cls(btn, "relative", schedOpen && "!text-brand !border-brand/50 !bg-brand/10")} title="Schedule this prompt to send later"
          onClick={() => { setSchedOpen((v) => !v); setCfg(false); setAgentMenu(false); if (!schedOpen) { loadRoots(); loadSched(); } }}>
          <Clock size={15} />
          {pendingCount > 0 && <span className="absolute -top-1 -right-1 min-w-[14px] h-[14px] px-0.5 rounded-full bg-brand text-[9px] text-white flex items-center justify-center font-semibold">{pendingCount}</span>}
        </button>
        <button className={cls(sendBtn, "composer-send")} onClick={() => send()} disabled={!msg.trim() && images.length === 0 && files.length === 0}
          title={!agentAvail ? `Install ${agent?.name} first` : sending ? (agentId === "codex" ? "Send into the running turn (or queue if unavailable)" : "Queue for the next turn; use Steer to correct the running turn") : "Send"}><Send size={15} /></button>
      </div>

      {/* Dictation reports itself here, under the box: you speak without watching the screen, so
          "listening", "transcribing" and first-run setup cannot hide in a tooltip. */}
      {voiceNote && (
        <div className={cls("flex items-center gap-1.5 text-[11px] px-1 pt-1",
          voiceSt.st === "recording" ? "text-danger" : "text-muted")}>
          <Mic size={11} className={voiceSt.st === "recording" ? "animate-pulse" : undefined} />
          <span className="truncate">{voiceNote}</span>
        </div>
      )}

      {/* The console keeps a status line under its prompt box — "⏵⏵ bypass permissions on ·
          Opus 5 · high · STUDIO" — and this used to copy it. In a terminal that line is the only
          place those four facts exist. Here they are all in the pane header a few centimetres
          above it, so the copy was a second answer to a question already answered, taking a row
          from the feed on every pane. The settings button beside the box opens the same popover
          the line used to open. */}

      {agentId === "deepseek-harness" && agent?.needs_key && <div className="text-[10px] text-warn mt-1">
        DeepSeek API key needed · <button className="underline" onClick={() => setTab("settings")}>Add in Settings → API Keys</button>
      </div>}
      {!agentAvail && agentId !== "codex" && (
        <div className="text-[10px] text-warn mt-1 flex items-center gap-1">
          {agent?.name} not installed · <button className="underline" onClick={() => setAgentMenu(true)}>install</button>
          {agentId !== "claude" && <>· <button className="underline" onClick={() => setLS("cc-agent", "claude", setAgentId)}>use Claude</button></>}
          {agentId === "claude" && <>· <button className="underline" onClick={() => setTab("settings")}>configure path</button></>}
        </div>
      )}
    </div>
  );
}

// (cliModeLabel lived here — it spelled the permission mode the way the console does, for the
//  status line under the prompt box. That line is gone, and it had no other caller.)

function fmtClock(ms: number) {
  const d = new Date(ms);
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const sameDay = ms >= start.getTime() && ms < start.getTime() + 86400000;
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function menuHint(agentId: string, name?: string, rootName?: string, alt?: boolean) {
  // An alternate engine works inside the project like Claude does, so the placeholder must say
  // "Message <project>" — naming the engine only when it isn't Claude.
  const inProject = agentId === "claude" || alt;
  const who = inProject
    ? (rootName ? `Message ${rootName}${agentId === "claude" ? "" : ` (${name || agentId})`}` : "Message this session")
    : `Ask ${name || agentId}`;
  return `${who}…  (@ files · / commands · Ctrl+V image · Enter to send)`;
}
