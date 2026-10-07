import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  AppWindow,
  Box,
  Check,
  ChevronDown,
  ChevronRight,
  GitBranch,
  CornerDownRight,
  ClipboardPaste,
  Code2,
  Copy,
  Crosshair,
  ExternalLink,
  Eye,
  File as FileIcon,
  FilePlus,
  Bot,
  Folder,
  FolderOpen,
  FolderPlus,
  FolderTree,
  Globe,
  GripVertical,
  History,
  Image as ImageIcon,
  ListChecks,
  Loader2,
  Maximize2,
  Minimize2,
  MessageSquare,
  Pencil,
  Pin,
  Play,
  Plus,
  RefreshCw,
  Search,
  RotateCcw,
  Save,
  Scissors,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { Checkpoint, CheckpointChange, EntryInfo, FileResult, Session, Skill, TreeEntry, WorkspaceRoot } from "../types";
import { cls, pollWhileVisible, timeAgo, useSetting } from "../components/ui";
import { folderAgent, paneAgent, writePaneAgent, sendSettings } from "../components/sendPrefs";
import { WorktreeDialog } from "../components/WorktreeDialog";
import { NewGameButton } from "../components/NewGameDialog";

// Loaded on demand, not with the page.
//
// These three were static imports, which put Monaco (~3.2 MB), xterm and the 3D viewer inside the
// Workspace chunk — 3.74 MB that had to download AND PARSE before the workspace list could paint,
// on a page where most visits never open a file at all. Split out, the chat and the project list
// arrive first and the editor loads the moment you actually open something.
const CodeEditor = lazy(() => import("../components/CodeEditor").then((m) => ({ default: m.CodeEditor })));

// The last file listing per folder, so switching back to a project draws its explorer at once and
// then replaces it with the fresh listing. Paths are absolute, so two projects cannot collide.
const lastTree = new Map<string, TreeEntry[]>();
const LAST_TREE_KEEP = 8;
const ModelViewer = lazy(() => import("../components/ModelViewer"));
const AgentTerminal = lazy(() => import("../components/AgentTerminal"));

/** A quiet placeholder while one of those arrives — a spinner here would read as "something is
 *  wrong", when it is simply a large editor being fetched once. */
function PaneLoading({ label }: { label: string }) {
  return <div className="flex-1 min-h-0 flex items-center justify-center text-xs text-muted/60">{label}</div>;
}
import { GitPanel } from "../components/GitPanel";
import { SubAgentPanel } from "../components/SubAgentPanel";
import { buildTree, WorkspaceTree } from "../components/WorkspaceTree";
import { RunningAgents } from "../components/RunningAgents";
import { PANE_DND, PaneGrid, PresetButton, useLayout } from "../components/PaneGrid";
import { preset, splitChatPane, type Pane, type PresetId } from "../components/paneLayout";
import { carriesFiles, openedMessage, planOpen, type Dropped } from "../components/dropFolder";

/** What a tab sets while it is being dragged, so a pane can tell a file tab from a pane. */
const TAB_DND = "application/x-studio-tab";
/** ...and what a project row from the explorer sets. Drag one into a pane to open it there. */
const ROOT_DND = "application/x-studio-root";
/** The Enter keystroke, as the terminal receives it. Written this way rather than as an
 *  escape because it IS a key press being replayed, not a line break in a string. */
const ENTER = String.fromCharCode(13);

type SessMode = { type: "live" } | { type: "session"; id: string } | { type: "new" };

import { DiffView, SessionFeed } from "../components/SessionFeed";
import { LivePick } from "../components/LivePick";
import { UsageLimitsBar } from "../components/UsageBar";
import { ContextMeter } from "../components/ContextMeter";
import { ChatComposer } from "../components/ChatComposer";
import { WorkingPulse } from "../components/WorkingPulse";
import { ModelBadge } from "../components/ModelBadge";
import { SpeedMeter } from "../components/SpeedMeter";



interface OpenTab { path: string; name: string; kind: FileResult["kind"] | "prompts" | "checkpoints" | "skills" | "todos" | "inspect"; text: string; dirty: boolean; mtime: number; diskChanged?: boolean; previewable?: boolean; convert?: boolean; }

/** The next shape up. Used when a file needs somewhere to go and every pane on screen is an
 *  agent: grow the room rather than close somebody's conversation to make space. */
const GROW: Record<PresetId, PresetId> = {
  single: "twoCols", twoCols: "threeCols", twoRows: "threeCols",
  mainSide: "quad", threeCols: "quad", quad: "six", six: "six",
};
/** ...and the smallest shape that still holds N agents, indexed by N-1. Where closing the last
 *  file goes back to. */
const FIT: PresetId[] = ["single", "twoCols", "threeCols", "quad", "six", "six"];
const paneCount = (id: PresetId) => preset(id).build([]).panes.length;

const PROMPTS_TAB = "__prompts__";
const CKPT_TAB = "__checkpoints__";
const SKILLS_TAB = "__skills__";
const TODOS_TAB = "__todos__";
const INSPECT_TAB = "__inspect__";

// apply the user's saved workspace order + hidden list (drag-to-reorder / removed projects)
const wsNorm = (s: string) => (s || "").replace(/\\/g, "/").toLowerCase();
function applyRootPrefs(list: WorkspaceRoot[]): WorkspaceRoot[] {
  let hidden: string[] = [], order: string[] = [];
  try { hidden = JSON.parse(localStorage.getItem("ws-hidden") || "[]"); } catch { /* ignore */ }
  try { order = JSON.parse(localStorage.getItem("ws-order") || "[]"); } catch { /* ignore */ }
  const visible = list.filter((r) => !hidden.includes(wsNorm(r.path)));
  const idx = (p: string) => { const i = order.indexOf(wsNorm(p)); return i < 0 ? 1e9 : i; };
  return visible.slice().sort((a, b) => idx(a.path) - idx(b.path));
}

// Chat agents that are the SAME Claude Code engine pointed at another vendor's endpoint. Each gets
// its own session universe, addressed by an "<id>--" project-id prefix (mission.alt_homes() on the
// backend). Built-ins are listed here; providers added in Settings → Models are learned from
// /api/mission/agents, so a new one works the moment it is saved — no code change.
// Codex keeps its conversation in its own app-server, filed under "codex--<folder>" the same way
// (backend codex_app.py), so the feed, live stream, context meter and phases look it up there.
const BUILTIN_ALT_AGENTS = ["kimi", "qwen", "codex", "deepseek-harness"];

function altFeedPrefix(agentId: string, customIds: string[]): string {
  return (BUILTIN_ALT_AGENTS.includes(agentId) || customIds.includes(agentId)) ? `${agentId}--` : "";
}

export default function Workspace() {
  const [roots, setRoots] = useState<WorkspaceRoot[]>([]);
  const [activeRoot, setActiveRoot] = useState<WorkspaceRoot | null>(null);
  // The look belongs to the workspace you are reading, not to the window. This paints THIS pane
  // and nothing above it, so switching project cannot repaint the nav, the status bar or the
  // tab you were on. Toggled per project from the chat box's settings menu.
  // The console look is chosen per project, so with two projects on screen it has to be applied
  // per pane: one agent can wear the CLI skin while the agent beside it stays in the Studio look.
  // Read from the store directly rather than through the hook, because a hook cannot be called
  // once per pane inside a render function.
  const cliAll = useStore((s) => s.skin === "cli");
  const cliByProject = useStore((s) => s.projectCli);
  const cliOf = (pid: string) => cliAll || !!(pid && cliByProject[pid]);
  // Nesting is the trap here. A pane that is NOT in the console look, sitting inside a window
  // that IS, would inherit the console palette — there is no rule that puts the theme back, and
  // writing one would mean repeating every colour of every theme.
  //
  // So the window chrome only ever wears the console look when it cannot conflict: either the
  // look is on globally (in which case every pane wears it too), or there is a single agent on
  // screen and the chrome simply follows it, exactly as it did before panes existed.
  const cliLook = cliOf(activeRoot?.id || "");
  const [rootEntries, setRootEntries] = useState<TreeEntry[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [wsDrag, setWsDrag] = useState(false);   // highlight the explorer while dragging files/folders over it
  // ...and which workspaces target a folder is being dragged over, so that one lights up and the
  // other three do not. A folder dropped here OPENS as a workspace; the same folder dropped on
  // the file tree is IMPORTED into the open project. One gesture, two meanings, decided by where
  // it lands - so exactly one place may claim it at a time.
  const [folderDrop, setFolderDrop] = useState<"" | "panel" | "rail" | "explorer">("");
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  // Which pane each open file lives in, and which of a pane's files it is showing. Keyed by
  // path and by pane id rather than held on the tab itself, so a pane that goes away (a
  // smaller preset) simply stops matching and its files fall back to the first pane —
  // no cleanup pass, and nothing can end up in a pane that is not on screen.
  const [tabPane, setTabPane] = useState<Record<string, string>>({});
  const [paneFocus, setPaneFocus] = useState<Record<string, string>>({});
  const [focusPane, setFocusPane] = useState("");   // where the next opened file lands
  // Which pane is choosing a project, and where its menu hangs. Window coordinates, because
  // the header it opens from clips so that a pane can never paint over its neighbour.
  const [projMenu, setProjMenu] = useState<{ left: number; top: number; pane: string } | null>(null);
  // A project being dragged out of the explorer, and the pane under the cursor. The project is
  // held in state rather than read from the drag payload because `getData` is unreadable during
  // a dragover — and without the name there is nothing useful to write on the pane you are over.
  const [rootDrag, setRootDrag] = useState<WorkspaceRoot | null>(null);
  const [dropHint, setDropHint] = useState("");
  // Which pane fills the window. Held here rather than inside the grid so that asking for a
  // project which lives in a hidden pane can bring that pane to the front.
  const [maxPane, setMaxPane] = useState<string | null>(null);
  // A one-second ring on a pane, to answer "where did it go" when the click changed nothing
  // visible because the project was already on screen.
  const [flashPane, setFlashPane] = useState("");
  const flashAt = useRef(0);
  useEffect(() => () => window.clearTimeout(flashAt.current), []);
  function flash(paneId: string) {
    window.clearTimeout(flashAt.current);
    setFlashPane(paneId);
    flashAt.current = window.setTimeout(() => setFlashPane(""), 1100);
  }
  // A drag abandoned outside any drop target fires dragend and nothing else, so this is the only
  // place that can clear the highlight when you let go over the desktop.
  useEffect(() => {
    const end = () => { setRootDrag(null); setDropHint(""); setFolderDrop(""); };
    window.addEventListener("dragend", end);
    // A DROP THAT LANDS ON NOTHING MUST DO NOTHING. Without this, Electron treats an unclaimed
    // file drop as a navigation and replaces the whole Studio with a file:// listing of whatever
    // was dragged - every unsaved tab and every running turn gone, from a slip of the mouse.
    // preventDefault on the window is the only thing that covers the gaps BETWEEN the targets.
    const swallow = (e: DragEvent) => {
      if (!Array.from(e.dataTransfer?.types || []).includes("Files")) return;
      // A TARGET THAT WANTED IT HAS ALREADY SAID SO. React calls preventDefault on the native
      // event, and this listener is last in the bubble chain, so `defaultPrevented` is how the
      // window can tell "somebody claimed this" from "nobody did" - and not clobber the cursor
      // the claimant set.
      if (e.defaultPrevented) return;
      e.preventDefault();                      // ...which is what stops Electron navigating
      // Nobody wants it, so say so. Without this every square inch of the app shows a copy
      // cursor and then does nothing, which reads as a broken feature rather than as a
      // gesture that does not apply here. "none" also means no drop event fires at all.
      if (e.type === "dragover" && e.dataTransfer) e.dataTransfer.dropEffect = "none";
    };
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);
    return () => {
      window.removeEventListener("dragend", end);
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", swallow);
    };
  }, []);
  // The shape of the main area. One arrangement for the window — a pane names its own project,
  // so the arrangement can hold two of them and cannot belong to either.
  const { layout, presetId, setLayout, pick, trade } = useLayout("workspace");
  // "3/9" for the Phases button — the counter was already parsed backend-side but had no home in
  // the Workspace, so a build plan was only visible for the one moment it scrolled past in the feed.
  const [todoCount, setTodoCount] = useState<{ done: number; total: number } | null>(null);
  // which open HTML tabs are showing the rendered page instead of the source (per path, so two
  // open files keep their own choice), and a nonce to force the iframe to re-fetch after a save.
  const [previewOn, setPreviewOn] = useState<Record<string, boolean>>({});
  const [previewNonce, setPreviewNonce] = useState(0);
  // the http URL the workspace is being served on, once the browser button has been used
  // Keyed by project id, not one value for the window: two projects on screen have two
  // addresses, two page lists and two dev servers, and sharing one showed the first
  // project's address under the second project's agent.
  const [servedUrls, setServedUrls] = useState<Record<string, string>>({});
  const [activePath, setActivePath] = useState<string | null>(null);
  const [showExplorer, setShowExplorer] = useState(() => localStorage.getItem("ws-explorer") !== "0");
  const toggleExplorer = () => setShowExplorer((v) => { const n = !v; try { localStorage.setItem("ws-explorer", n ? "1" : "0"); } catch { /* ignore */ } return n; });
  const [pinned, setPinned] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("ws-pinned") || "[]"); } catch { return []; } });
  const [pinDrag, setPinDrag] = useState<string | null>(null);   // quick-list drag-reorder source
  // Which engine the chat is set to (claude / kimi / …). Kimi is a full parallel Claude-Code
  // universe — its feed/live/context use a "kimi--" prefixed project id.
  //
  // Resolved PER PROJECT, in the same order the composer already uses: the folder's own pin
  // first, the global key second. One value for the window was fine while the window held one
  // project; with two agents side by side it would put the second pane's feed in the first
  // pane's universe. The tick is what makes a change elsewhere reach this component — the
  // value itself lives in localStorage, which React cannot subscribe to.
  const [agentTick, setAgentTick] = useState(0);
  useEffect(() => {
    const f = () => setAgentTick((n) => n + 1);
    window.addEventListener("cc-agent", f);
    const iv = window.setInterval(() => { if (!document.hidden) f(); }, 2000);
    return () => { window.removeEventListener("cc-agent", f); window.clearInterval(iv); };
  }, []);
  const agentOf = useMemo(() => (pid: string, paneId = "") => (
    paneId ? paneAgent(paneId, pid) : folderAgent(pid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ), [agentTick]);
  // Alternate engines live in their own Claude-Code session universe behind an "<engine>--" id
  // prefix, so the feed/context/live lookups must use the prefixed id — that's what makes their
  // messages render here exactly like Claude's.
  const [customAgents, setCustomAgents] = useState<string[]>([]);
  useEffect(() => {
    api.missionAgents().then((r) => setCustomAgents(r.agents.filter((a) => a.custom).map((a) => a.id)))
      .catch(() => {});
    // agentTick, not one agent name: the list has to be right for EVERY pane, and any pane
    // changing its engine is a reason to have it.
  }, [agentTick]);
  // A CLI that draws its own full-screen interface is stored as "term:<id>". The prefix is
  // deliberate: those ids are not in AGENTS, so without it every lookup keyed on the agent
  // name would quietly return null and the chat would try to send to something that cannot
  // receive. With it, the branch below is the only place that has to know.
  const termOf = (pid: string, paneId = "") => { const a = agentOf(pid, paneId); return a.startsWith("term:") ? a.slice(5) : ""; };
  const feedOf = (root: WorkspaceRoot | null, paneId = "") =>
    root ? altFeedPrefix(agentOf(root.id, paneId), customAgents) + root.id : "";
  const backToChat = (root: WorkspaceRoot | null, paneId: string) => {
    if (root?.id) writePaneAgent(paneId, root.id, "claude");
    window.dispatchEvent(new Event("cc-agent"));
    setAgentTick((n) => n + 1);
  };
  // What the activity bar, the phases badge and the file tree are looking at: the focused pane's
  // project, which is what `activeRoot` now tracks.
  // The pane whose project the rail's buttons act on: the focused one, else the first chat pane.
  const focusedChatPane = focusPane || layout.panes.find((p) => p.kind === "chat")?.id || "";
  const feedId = feedOf(activeRoot, focusedChatPane);
  // The Inspect button is hidden rather than left to fail: the endpoint behind it refuses when
  // the switch is off, and a button whose only answer is a refusal is worse than no button.
  const pickOn = useSetting("live_pick", true);
  // The bottom bar is global and the subagents are not, so tell it which project is in front.
  const setActiveProject = useStore((st) => st.setActiveProject);
  useEffect(() => { setActiveProject(feedId); }, [feedId, setActiveProject]);
  // keep the Phases badge live while the agent works (same visibility-gated poll the context meter
  // uses). `feedId` carries the engine, and the backend reads a phase list from whichever engine's
  // transcript it names — Claude's TodoWrite/TaskCreate, DeepSeek's `todo_write`, Codex's plan.
  useEffect(() => {
    if (!feedId) { setTodoCount(null); return; }
    let alive = true;
    const stop = pollWhileVisible(() => api.missionTodos(feedId)
      .then((r) => { if (alive) setTodoCount(r.total ? { done: r.done, total: r.total } : null); })
      .catch(() => {}), 8000);
    return () => { alive = false; stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedId]);
  const togglePin = (r: WorkspaceRoot) => setPinned((ps) => { const k = wsNorm(r.path); const next = ps.includes(k) ? ps.filter((x) => x !== k) : [...ps, k]; try { localStorage.setItem("ws-pinned", JSON.stringify(next)); } catch { /* ignore */ } return next; });
  const [showTree, setShowTree] = useState(() => localStorage.getItem("ws-showtree") !== "0");
  const toggleTree = () => setShowTree((v) => { const n = !v; try { localStorage.setItem("ws-showtree", n ? "1" : "0"); } catch { /* ignore */ } return n; });
  // per-workspace status: working (spinner) / done-unseen (green tick) / idle (grey)
  const [busyMap, setBusyMap] = useState<Record<string, boolean>>({});
  // Which agent is working in each folder, and the colour it was given in Settings → Models.
  // Claude keeps the blue it has always had; every other engine shows its own, so a glance at
  // the list says both THAT something is running and WHICH agent is running it.
  const [busyAgent, setBusyAgent] = useState<Record<string, string>>({});
  // Subagents in flight per workspace. It rides on the same 1.5s poll as the spinner, because
  // "working" alone cannot tell one agent from a fan-out of nine, and those are the difference
  // between waiting a minute and waiting an hour.
  const [fanout, setFanout] = useState<Record<string, number>>({});
  // Of that fan-out, how many are quiet rather than typing. The badge counts every agent
  // still owed a result; this only feeds the tooltip that explains the number.
  const [quiet, setQuiet] = useState<Record<string, number>>({});
  // The panels the activity bar opens. Both are popovers rather than tabs: they are a glance,
  // not a place you work, and a tab for either would push a pane off the grid.
  const [agentsPanel, setAgentsPanel] = useState(false);
  const [wsPanel, setWsPanel] = useState(false);
  const [wsQuery, setWsQuery] = useState("");
  const [agentColour, setAgentColour] = useState<Record<string, string>>({});
  // What to call each engine in a panel's own words ("Claude Code", "Codex", "DeepSeek Harness").
  // The panels used to say "Claude" in their copy whatever was selected, which on a DeepSeek pane
  // is not a cosmetic slip: it is the panel telling you it is showing somebody else's work.
  const [agentName, setAgentName] = useState<Record<string, string>>({});
  useEffect(() => {
    api.missionAgents()
      .then((r) => {
        setAgentColour(Object.fromEntries(r.agents.map((a) => [a.id, a.color])));
        setAgentName(Object.fromEntries(r.agents.map((a) => [a.id, a.name])));
      })
      .catch(() => {});
  }, []);
  const nameOfAgent = (id: string) =>
    agentName[id || ""] || (id === "claude" || !id ? "Claude Code" : id);
  // WHICH ENGINE THE ACTIVITY BAR IS TALKING ABOUT. The rail is global while the engine is per
  // pane, so it follows the FOCUSED pane — the same pane whose buttons open these tabs. The
  // tooltips name it, because they used to say "Claude" whatever was selected, which on a DeepSeek
  // pane reads as the panel showing somebody else's work.
  const activeAgent = activeRoot ? agentOf(activeRoot.id, focusedChatPane) : "claude";
  const activeAgentName = nameOfAgent(activeAgent);
  const workingColour = (wid: string) => {
    const a = busyAgent[wid] || "claude";
    return a === "claude" ? "" : (agentColour[a] || "#8b8b8b");
  };
  // Publish which workspace is on screen so the turn-end notifier (App -> useTurnEndNotifier)
  // can stay quiet about the one you are already watching, while still telling you about the
  // other 38. It lives in localStorage rather than the store because that hook runs on every
  // tab, including the ones where this page is unmounted.
  useEffect(() => {
    try { localStorage.setItem("ws-active", activeRoot?.id || ""); } catch { /* ignore */ }
  }, [activeRoot?.id]);
  const [unseen, setUnseen] = useState<Set<string>>(() => new Set());
  const prevBusy = useRef<Record<string, boolean>>({});
  const wsStatus = (wid: string): "working" | "done" | "idle" => (busyMap[wid] ? "working" : unseen.has(wid) ? "done" : "idle");
  useEffect(() => {
    let alive = true;
    const tick = () => api.liveStatus().then((r) => {
      if (!alive) return;
      const next = r.statuses || {};
      setUnseen((u) => {
        let nu: Set<string> | null = null;
        for (const pid of Object.keys(prevBusy.current)) {
          if (prevBusy.current[pid] && !next[pid] && pid !== activeRoot?.id) { if (!nu) nu = new Set(u); nu.add(pid); }
        }
        return nu || u;
      });
      prevBusy.current = next;
      setBusyMap(next);
      setBusyAgent(r.agents || {});
      setFanout(r.running_agents || {});
      setQuiet(r.quiet_agents || {});
    }).catch(() => {});
    // THE POLL THAT EMPTIED THE CHAT. Every 1.5s, hand-rolled, with no idea whether the last
    // one had come back. When live-status got slow this opened a new connection each tick until
    // the browser's six-per-origin pool held nothing but copies of it, and the feed's own
    // request could not get a socket. pollWhileVisible skips a tick while one is in flight.
    const stop = pollWhileVisible(tick, 1500);
    return () => { alive = false; stop(); };
  }, [activeRoot?.id]);
  // opening a workspace clears its "unseen" → back to idle
  useEffect(() => {
    const aid = activeRoot?.id;
    if (!aid) return;
    setUnseen((u) => { if (!u.has(aid)) return u; const nu = new Set(u); nu.delete(aid); return nu; });
  }, [activeRoot?.id]);
  const restored = useRef(false);
  const toast = useStore((s) => s.toast);
  const wsFont = useStore((s) => s.wsFont);
  const workspaceTarget = useStore((s) => s.workspaceTarget);
  const clearWorkspaceTarget = useStore((s) => s.clearWorkspaceTarget);
  // One conversation mode per pane: two agents on screen are two conversations, and sending
  // one of them back to "live" must not drag the other with it.
  const [sessModes, setSessModes] = useState<Record<string, SessMode>>({});
  const sessOf = (paneId: string): SessMode => sessModes[paneId] || { type: "live" };
  const setSess = (paneId: string, m: SessMode) => setSessModes((x) => ({ ...x, [paneId]: m }));
  // in-app prompt (window.prompt() is unsupported in Electron, so it did nothing)
  const [ask, setAsk] = useState<{ title: string; placeholder?: string; initial: string; resolve: (v: string | null) => void } | null>(null);
  const askInput = (title: string, opts?: { placeholder?: string; initial?: string }) =>
    new Promise<string | null>((resolve) => setAsk({ title, placeholder: opts?.placeholder, initial: opts?.initial || "", resolve }));
  // right-click menu target + copy/cut clipboard for file operations
  const [menu, setMenu] = useState<{ entry: TreeEntry; x: number; y: number; isRoot?: boolean } | null>(null);
  const [clip, setClip] = useState<{ path: string; name: string; op: "copy" | "cut" } | null>(null);

  useEffect(() => {
    const norm = (s: string) => (s || "").replace(/\\/g, "/").toLowerCase();
    const qsRoot = new URLSearchParams(location.search).get("root");  // detached window preselects this
    api.wsRoots().then((r) => {
      const list = applyRootPrefs(r);
      setRoots(list);
      const saved = norm(qsRoot || localStorage.getItem("ws-root") || "");
      setActiveRoot((cur) => cur || list.find((x) => norm(x.path) === saved) || list[0] || null);
    }).catch((e) => toast(`Workspace: ${e.message}`, "danger"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Git facts arrive SEPARATELY from the folder list. Forty-six roots is forty-six subprocesses,
  // and the list must never wait on git to appear — so the rail paints flat immediately and
  // nests itself a moment later. Merged at render time, so the drag order and the pinned list
  // never have to know any of this happened.
  const [gitInfo, setGitInfo] = useState<Record<string, Partial<WorkspaceRoot>>>({});
  const loadGitMap = () => {
    api.wtMap().then((m) => {
      const by: Record<string, Partial<WorkspaceRoot>> = {};
      for (const r of m.roots || []) {
        const { id: _i, name: _n, path, opened: _o, ...git } = r as any;
        by[wsNorm(path)] = git;
      }
      setGitInfo(by);
    }).catch(() => { /* git missing or backend older — the rail simply stays flat */ });
  };
  const gitMapRef = useRef(loadGitMap);
  gitMapRef.current = loadGitMap;
  // Deliberately NOT on mount. This endpoint builds the same project overview the workspace list
  // is built from, so firing both at once had two callers racing the one thing you are waiting
  // for — and this one also spawns a git process per folder. It waits until the list is in hand;
  // nesting is a decoration on a list that already exists.
  useEffect(() => {
    if (!roots.length) return;
    return pollWhileVisible(() => gitMapRef.current(), 30000);
  }, [roots.length]);
  const gitRoots = useMemo(
    () => roots.map((r) => ({ ...r, ...(gitInfo[wsNorm(r.path)] || {}) })),
    [roots, gitInfo]);

  // Agents in flight, and where. The map is keyed by BOTH the prefixed and the bare project id
  // (an alternate engine files its session under `<engine>--`), so summing the values would
  // count an alternate-engine fan-out twice — only ids the workspace list actually knows are
  // counted, which is also what makes each row clickable.
  //
  // `feed` is the id the AGENTS live under, which is not the id the workspace list knows. A
  // Codex conversation keeps its agents behind "codex--<folder>", and asking for them by the bare
  // folder id reads Claude's transcripts instead — the panel would then say "no agents" beside a
  // badge that says two. The bare id stays, because that is what names the row and finds the root
  // to switch to.
  const agentBusy = useMemo(() => {
    const known = new Set(roots.map((r) => r.id));
    return Object.entries(fanout)
      .filter(([id, n]) => n > 0 && known.has(id))
      .map(([id, count]) => ({ id, count, feed: altFeedPrefix(busyAgent[id] || "", customAgents) + id }));
  }, [fanout, roots, busyAgent, customAgents]);
  const agentCount = agentBusy.reduce((a, b) => a + b.count, 0);

  // Every workspace, with the folders of its repository under it. A root is only a HEAD when no
  // primary checkout of the same repository exists — otherwise it is one of that head's children
  // and must not also be listed at the top level, which is the flat list this replaces.
  const wsRows = useMemo(() => {
    const led = new Set(gitRoots.filter((r) => r.kind === "primary" && r.repo).map((r) => r.repo));
    const heads = gitRoots.filter((r) => !(r.repo && led.has(r.repo) && r.kind !== "primary"));
    return buildTree(gitRoots, heads);
  }, [gitRoots]);

  // Filtering forty-five workspaces.
  //
  // The list was the full set in the order they were added, and finding one meant reading it.
  // Name, branch and path all match, because those are the three things you would remember: the
  // folder, the branch you left a worktree on, or where on disk it is.
  //
  // A match on a CHILD keeps its repository visible and drops the siblings that did not match —
  // otherwise searching for a branch name would either hide the row that carries it or drag in
  // every other folder of that repo alongside it.
  const wsFiltered = useMemo(() => {
    const q = wsQuery.trim().toLowerCase();
    if (!q) return wsRows;
    const hit = (r: WorkspaceRoot) =>
      r.name.toLowerCase().includes(q) || (r.branch || "").toLowerCase().includes(q)
      || r.path.toLowerCase().replace(/\\/g, "/").includes(q);
    const out: typeof wsRows = [];
    for (const row of wsRows) {
      const kids = row.kids.filter(hit);
      if (hit(row.r)) out.push({ r: row.r, kids: kids.length ? kids : row.kids });
      else if (kids.length) out.push({ r: row.r, kids });
    }
    return out;
  }, [wsRows, wsQuery]);

  // Which pinned repositories are showing their other folders. Keyed by the git dir, not the
  // path, so the twisty state follows the REPOSITORY — unpin the primary and pin it again and
  // it opens the same way, because it is the same repo.
  const [pinOpen, setPinOpen] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem("ws-pin-open") || "{}"); } catch { return {}; }
  });
  const togglePinOpen = (key: string) => {
    setPinOpen((o) => {
      const n = { ...o, [key]: !o[key] };
      try { localStorage.setItem("ws-pin-open", JSON.stringify(n)); } catch { /* ignore */ }
      return n;
    });
    // Re-read git as it opens. A worktree made in the terminal pane, or by `claude --worktree`,
    // is otherwise missing until something else happens to refresh the map. The CLI does have
    // WorktreeCreate/WorktreeRemove hooks, and they are deliberately NOT used: the CLI reads the
    // hook's stdout as the path of the worktree to use and refuses the whole operation without
    // one, so registering a recorder on them would break every `claude --worktree`. One git call
    // on a click cannot break anything.
    gitMapRef.current();
  };

  // A pinned workspace and the folders belonging to the same repository, so the rail reads as
  // one tree. Only a primary checkout heads a group: a worktree pinned on its own stays a plain
  // row, because it IS a child and a twisty on it would imply a level that does not exist.
  // While filtering, a repository whose CHILD matched is opened, or the match stays hidden
  // behind a shut twisty and the search looks like it found nothing.
  const wsOpen = useMemo(() => {
    if (!wsQuery.trim()) return pinOpen;
    const o = { ...pinOpen };
    for (const row of wsFiltered) if (row.kids.length) o[row.r.repo || row.r.path] = true;
    return o;
  }, [pinOpen, wsFiltered, wsQuery]);

  const pinnedRows = useMemo(() => {
    const shown = gitRoots.filter((r) => pinned.includes(wsNorm(r.path)));
    return shown.map((r) => ({
      r,
      kids: r.kind === "primary" && r.repo
        ? gitRoots
            .filter((x) => x.repo === r.repo && x.path !== r.path)
            .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name)
              : a.kind === "worktree" ? -1 : 1))
        : [],
    }));
  }, [gitRoots, pinned]);
  const [wtFor, setWtFor] = useState<WorkspaceRoot | null>(null);
  const [gitFor, setGitFor] = useState<WorkspaceRoot | null>(null);
  const activeGit = activeRoot ? gitInfo[wsNorm(activeRoot.path)] : undefined;
  useEffect(() => {
    if (!activeRoot) return;
    localStorage.setItem("ws-root", activeRoot.path);
    // The folder you already looked at is drawn at once, then replaced by the fresh listing.
    // Measured: the tree took 760 ms to arrive on a workspace switch, so the explorer sat empty
    // for most of a second every time. A listing is only ever REPLACED, never merged, and a
    // failed refresh keeps what was there rather than emptying the panel.
    const cached = lastTree.get(activeRoot.path);
    if (cached) setRootEntries(cached);
    api.wsTree(activeRoot.path).then((t) => {
      setRootEntries(t.entries);
      lastTree.delete(activeRoot.path);
      lastTree.set(activeRoot.path, t.entries);
      while (lastTree.size > LAST_TREE_KEEP) lastTree.delete(lastTree.keys().next().value as string);
    }).catch(() => { if (!cached) setRootEntries([]); });
  }, [activeRoot, reloadKey]);

  // THE CODE EDITOR, WARMED. It is a 3.7 MB chunk that loads the first time you open a file:
  // measured at 186 ms of blocked screen, exactly when you are waiting to read something. Pulled
  // in once the window has been idle for a moment, so the first file opens like the second (4 ms).
  useEffect(() => {
    const warm = () => { import("../components/CodeEditor").catch(() => {}); };
    const ric = (window as any).requestIdleCallback as undefined | ((cb: () => void, o?: any) => number);
    const h = ric ? ric(warm, { timeout: 4000 }) : window.setTimeout(warm, 2500);
    return () => {
      const cic = (window as any).cancelIdleCallback as undefined | ((id: number) => void);
      if (ric && cic) cic(h); else window.clearTimeout(h as number);
    };
  }, []);

  // a file clicked in a chat feed (e.g. `.studio-uploads/x.png` in Claude's answer) →
  // open it in an editor tab on the right; switches project first when the click
  // came from another project's feed. Relative paths resolve against the project root.
  const fileTarget = useStore((s) => s.fileTarget);
  const clearFileTarget = useStore((s) => s.clearFileTarget);
  useEffect(() => {
    if (!fileTarget || roots.length === 0) return;
    if (fileTarget.projectId && activeRoot?.id !== fileTarget.projectId) {
      const r = roots.find((x) => x.id === fileTarget.projectId);
      if (r) { setActiveRoot(r); return; }   // effect re-runs with the right root; keep the target
    }
    clearFileTarget();
    const p = fileTarget.path.replace(/\\/g, "/");
    const isAbs = /^([A-Za-z]:\/|\/)/.test(p);
    if (!isAbs && !activeRoot) return;
    const full = isAbs ? p : `${activeRoot!.path.replace(/\\/g, "/")}/${p.replace(/^\.\//, "")}`;
    (async () => {
      // open the exact path when it exists; otherwise find the file BY NAME in the project
      // (chat paths are often written relative to a subfolder, shortened, or slightly off)
      let target = (await api.wsStat(full).catch(() => null)) ? full : "";
      const base = p.split("/").pop() || p;
      if (!target && activeRoot) {
        const s = await api.wsSearch(activeRoot.path, base, 8).catch(() => null);
        const hit = s?.entries?.find((en) => en.name.toLowerCase() === base.toLowerCase()) || s?.entries?.[0];
        if (hit) target = hit.path;
      }
      if (!target) {
        toast(`File not found in ${activeRoot?.name || "the project"}: ${p} (it may be an example name, or was deleted)`, "danger");
        return;
      }
      const name = target.replace(/\\/g, "/").split("/").pop() || target;
      openFile({ path: target, name, is_dir: false, size: 0 });
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileTarget, roots, activeRoot?.id]);

  // jumped here from Mission Control (double-click a card title) → select that project
  useEffect(() => {
    if (!workspaceTarget || roots.length === 0) return;
    const norm = (s: string) => s.replace(/\\/g, "/").toLowerCase();
    const r = roots.find((x) => norm(x.path) === norm(workspaceTarget));
    if (r) { openInPane(r); toast(`Opened ${r.name} in Workspace`, "ok"); }
    clearWorkspaceTarget();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceTarget, roots]);

  // restore previously-open files across refresh
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    let paths: string[] = [];
    try { paths = JSON.parse(localStorage.getItem("ws-tabs") || "[]"); } catch { /* ignore */ }
    try {
      const homes = JSON.parse(localStorage.getItem("ws-tab-panes") || "{}");
      if (homes && typeof homes === "object") setTabPane(homes);
    } catch { /* a pane id that no longer exists simply falls back to the first pane */ }
    if (!paths.length) return;
    const act = localStorage.getItem("ws-active");
    Promise.all(paths.map((p) =>
      p === PROMPTS_TAB
        ? Promise.resolve<OpenTab>({ path: PROMPTS_TAB, name: "Prompts", kind: "prompts", text: "", dirty: false, mtime: 0 })
        : p === CKPT_TAB
        ? Promise.resolve<OpenTab>({ path: CKPT_TAB, name: "Checkpoints", kind: "checkpoints", text: "", dirty: false, mtime: 0 })
        : p === SKILLS_TAB
        ? Promise.resolve<OpenTab>({ path: SKILLS_TAB, name: "Skills", kind: "skills", text: "", dirty: false, mtime: 0 })
        : p === TODOS_TAB
        ? Promise.resolve<OpenTab>({ path: TODOS_TAB, name: "Phases", kind: "todos", text: "", dirty: false, mtime: 0 })
        : api.wsFile(p).then((f) => ({ path: p, name: p.split(/[\\/]/).pop() || p, kind: f.kind, text: f.text || "", dirty: false, mtime: f.mtime || 0, previewable: f.previewable, convert: f.convert }))
          .catch(() => null)))
      .then((loaded) => {
        const valid = loaded.filter(Boolean) as OpenTab[];
        if (!valid.length) return;
        const want = act && valid.some((t) => t.path === act) ? act : valid[valid.length - 1].path;
        setTabs(valid);
        setActivePath(want);
        // ...and show it in the pane it belongs to, rather than that pane's last tab
        try {
          const homes = JSON.parse(localStorage.getItem("ws-tab-panes") || "{}");
          if (homes?.[want]) setPaneFocus((m) => ({ ...m, [homes[want]]: want }));
        } catch { /* the pane simply opens on its last tab */ }
      });
  }, []);
  useEffect(() => {
    localStorage.setItem("ws-tabs", JSON.stringify(tabs.map((t) => t.path)));
    // which pane each one was in, or a refresh piles every open file back into the first pane
    localStorage.setItem("ws-tab-panes", JSON.stringify(tabPane));
    if (activePath) localStorage.setItem("ws-active", activePath); else localStorage.removeItem("ws-active");
  }, [tabs, activePath, tabPane]);

  async function openFile(entry: TreeEntry) {
    // Six agents and no room left. Say so instead of adding a tab that nothing on screen shows.
    if (!editorIds.length && layout.panes.length >= 6) {
      toast("Every pane is an agent — close one to make room for files.", "warn");
      return;
    }
    setActivePath(entry.path);
    landIn(entry.path);
    if (tabs.some((t) => t.path === entry.path)) return;
    try {
      const f = await api.wsFile(entry.path);
      // carry previewable/convert — without previewable the Preview button never appears, which
      // is why a big generated page could only ever say "File too large to open here"
      setTabs((ts) => [...ts, { path: entry.path, name: entry.name, kind: f.kind, text: f.text || "",
                                dirty: false, mtime: f.mtime || 0, previewable: f.previewable, convert: f.convert }]);
      // nothing else can be shown for an over-cap page, so open it rendered straight away
      if (f.kind === "toobig" && f.previewable) setPreviewOn((p) => ({ ...p, [entry.path]: true }));
    } catch (e: any) {
      toast(`Open failed: ${/404/.test(e.message || "") ? `file not found — ${entry.path}` : e.message}`, "danger");
    }
  }
  function closeTab(path: string) {
    const remaining = tabs.filter((t) => t.path !== path);
    setTabs(remaining);
    setTabPane((m) => { const n = { ...m }; delete n[path]; return n; });
    if (activePath === path) setActivePath(remaining.length ? remaining[remaining.length - 1].path : null);
    if (!remaining.length) shrinkToAgents();
  }
  /** Closing the last file gives the room back to the agents — and remembers the shape you were
   *  in, so opening the next file puts you straight back into it. */
  const lastShape = useRef<PresetId | null>(null);
  function shrinkToAgents() {
    const want = FIT[Math.min(Math.max(1, chatPanes.length), 6) - 1];
    if (want === presetId) return;
    lastShape.current = presetId;
    pick(want);
  }
  /** Close every file in ONE pane. Emptying the whole window from a button in a corner of it was
   *  never what "Close all" meant once there is more than one corner. */
  function closeAllIn(paneId: string) {
    const mine = new Set(tabsIn(paneId).map((t) => t.path));
    if (!mine.size) return;
    const remaining = tabs.filter((t) => !mine.has(t.path));
    setTabs(remaining);
    setTabPane((m) => { const n = { ...m }; mine.forEach((k) => delete n[k]); return n; });
    if (activePath && mine.has(activePath)) setActivePath(null);
    if (!remaining.length) shrinkToAgents();
  }

  // ---- file-tree operations (right-click menu + drag-drop) ----------------
  function refreshTree() { setReloadKey((k) => k + 1); }
  async function ctxReveal(p: string) {
    try { await api.wsReveal(p); } catch (e: any) { toast(e.message, "danger"); }
  }
  async function ctxRename(entry: TreeEntry) {
    const name = await askInput("Rename", { initial: entry.name });
    if (!name || name === entry.name) return;
    try {
      const r = await api.wsRename(entry.path, name);
      setTabs((ts) => ts.map((t) => (t.path === entry.path ? { ...t, path: r.path, name } : t)));
      if (activePath === entry.path) setActivePath(r.path);
      refreshTree(); toast(`Renamed to ${name}`, "ok");
    } catch (e: any) { toast(e.message, "danger"); }
  }
  async function ctxDelete(entry: TreeEntry) {
    if (!window.confirm(`Delete "${entry.name}"?${entry.is_dir ? "\n\nThis removes the folder and everything inside it." : ""}`)) return;
    try {
      await api.wsDelete(entry.path);
      setTabs((ts) => ts.filter((t) => !(t.path === entry.path || t.path.startsWith(entry.path + "\\") || t.path.startsWith(entry.path + "/"))));
      refreshTree(); toast(`Deleted ${entry.name}`, "ok");
    } catch (e: any) { toast(e.message, "danger"); }
  }
  function ctxCopy(entry: TreeEntry) { setClip({ path: entry.path, name: entry.name, op: "copy" }); toast(`Copied "${entry.name}" — paste into a folder`, "info"); }
  function ctxCut(entry: TreeEntry) { setClip({ path: entry.path, name: entry.name, op: "cut" }); toast(`Cut "${entry.name}" — paste into a folder`, "info"); }
  async function ctxPaste(destDir: string) {
    if (!clip) return;
    try {
      if (clip.op === "cut") { await api.wsMove(clip.path, destDir); setClip(null); }
      else { await api.wsCopy(clip.path, destDir); }
      refreshTree(); toast(`Pasted "${clip.name}"`, "ok");
    } catch (e: any) { toast(e.message, "danger"); }
  }
  async function newFileIn(dir: string) {
    const name = await askInput("New file", { placeholder: "file name (e.g. index.ts)" }); if (!name) return;
    try { await api.wsNewFile(`${dir}/${name}`); refreshTree(); } catch (e: any) { toast(e.message, "danger"); }
  }
  async function newFolderIn(dir: string) {
    const name = await askInput("New folder", { placeholder: "folder name" }); if (!name) return;
    try { await api.wsMkdir(`${dir}/${name}`); refreshTree(); } catch (e: any) { toast(e.message, "danger"); }
  }
  // drop files/folders from Windows → import into a folder. Electron exposes the dragged
  // item's real path (server-side copy, works for folders too); browser falls back to byte upload.
  // Browser / no-bridge fallback: recursively upload a dropped directory entry (handles folders
  // without needing the Electron real-path bridge).
  async function uploadEntry(entry: any, destDir: string): Promise<number> {
    if (!entry) return 0;
    if (entry.isFile) {
      try {
        const file: File = await new Promise((res, rej) => entry.file(res, rej));
        await api.wsUploadFile(destDir, file);
        return 1;
      } catch { return 0; }
    }
    if (entry.isDirectory) {
      let sub = `${destDir}/${entry.name}`;
      try { const r = await api.wsMkdir(sub); sub = (r as any)?.path || sub; } catch { /* may already exist */ }
      const reader = entry.createReader();
      let n = 0;
      for (;;) {  // readEntries returns in batches; loop until an empty batch
        const batch: any[] = await new Promise((res) => reader.readEntries((e: any[]) => res(e || []), () => res([])));
        if (!batch.length) break;
        for (const e of batch) n += await uploadEntry(e, sub);
      }
      return n;
    }
    return 0;
  }
  async function importDrop(destDir: string, dt: DataTransfer) {
    const files = Array.from(dt.files || []);
    const bridge = (window as any).studioBridge?.getPathForFile;
    const hasPath = !!bridge || files.some((f) => (f as any).path);
    // Capture directory entries SYNCHRONOUSLY — the DataTransferItemList is invalid after an await.
    const entries = hasPath ? [] : Array.from(dt.items || [])
      .filter((i) => i.kind === "file").map((i) => (i as any).webkitGetAsEntry?.()).filter(Boolean);
    let ok = 0, fail = 0;
    if (hasPath) {
      // Electron: copy files AND folders server-side by their real OS path (fast, exact).
      for (const f of files) {
        const srcPath = (bridge?.(f) || (f as any).path || "") as string;
        try {
          if (srcPath) await api.wsImport(srcPath, destDir);
          else await api.wsUploadFile(destDir, f);
          ok++;
        } catch { fail++; }
      }
    } else if (entries.length) {
      for (const en of entries) { const n = await uploadEntry(en, destDir); if (n) ok += n; else fail++; }
    } else {
      for (const f of files) { try { await api.wsUploadFile(destDir, f); ok++; } catch { fail++; } }
    }
    refreshTree();
    if (ok || fail) toast(`Imported ${ok} item${ok !== 1 ? "s" : ""}${fail ? `, ${fail} failed` : ""}`, fail ? "warn" : "ok");
    else toast("Couldn't read the dropped item — try again, or use the workspace's import button.", "warn");
  }
  /** A folder dragged from Windows onto the Workspaces list: open each one as a workspace.
   *
   *  This is the same drag `importDrop` handles over the file tree, and deliberately not the same
   *  outcome - over an open project a folder is COPIED IN, over the workspaces list it is OPENED.
   *  The target decides, which is the only way one gesture can honestly mean two things.
   *
   *  Both halves of a dropped item are read SYNCHRONOUSLY. `DataTransferItemList` is invalidated
   *  by the first await, so `webkitGetAsEntry` has to happen before anything is asked of the
   *  backend - the same trap `importDrop` already documents.
   */
  async function openDroppedFolders(dt: DataTransfer) {
    const bridge = (window as any).studioBridge?.getPathForFile;
    const files = Array.from(dt.files || []);
    const entries = Array.from(dt.items || [])
      .filter((i) => i.kind === "file")
      .map((i) => (i as any).webkitGetAsEntry?.() || null);
    const items: Dropped[] = files.map((f, i) => ({
      name: f.name,
      path: (bridge?.(f) || (f as any).path || "") as string,
      isDir: entries[i] ? !!entries[i].isDirectory : null,
    }));
    const plan = planOpen(items);
    if (!plan.ok) { toast(plan.why, "warn"); return; }

    const opened: WorkspaceRoot[] = [], already: string[] = [], failed: string[] = [];
    const known = new Set(roots.map((r) => wsNorm(r.path)));
    for (const path of plan.paths) {
      try {
        const r = await api.wsAddRoot(path);
        if (known.has(wsNorm(r.path))) already.push(r.name);
        else opened.push(r);
      } catch (e: any) {
        failed.push(`${path.split(/[\\/]/).filter(Boolean).pop() || path}: ${e.message}`);
      }
    }
    // The list is re-read rather than patched: a folder brings its worktrees and its git branch
    // with it, and a hand-built row would be missing both until the next poll.
    try { setRoots(applyRootPrefs(await api.wsRoots())); } catch { /* the toast still lands */ }
    // LAND IN IT. Opening a folder and being left where you were is a step you would then have to
    // take by hand every single time - the same reason the worktree dialog opens what it made.
    if (opened[0]) { openInPane(opened[0]); setWsPanel(false); }
    const said = openedMessage(opened.map((r) => r.name), already, failed, plan.skipped);
    toast(said.text, said.tone);
  }

  /** The three callbacks every workspaces drop target shares. `where` is which one lit up. */
  function folderTarget(where: "panel" | "rail" | "explorer") {
    const mine = (e: React.DragEvent) => carriesFiles(e.dataTransfer.types, [ROOT_DND, TAB_DND, PANE_DND]);
    return {
      onDragOver: (e: React.DragEvent) => {
        if (!mine(e)) return;
        e.preventDefault();
        // "link", not "copy": the cursor is the only warning a person gets about which of the two
        // meanings they are about to trigger, and this one references the folder rather than
        // duplicating it. The file tree keeps the copy cursor, because it really does copy.
        e.dataTransfer.dropEffect = "link";
        setFolderDrop(where);
      },
      onDragLeave: (e: React.DragEvent) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setFolderDrop("");
      },
      onDrop: (e: React.DragEvent) => {
        if (!mine(e)) return;
        e.preventDefault();
        setFolderDrop("");
        openDroppedFolders(e.dataTransfer);
      },
    };
  }

  // open this workspace in its own window (Electron native window after the main.cjs change; browser tab otherwise)
  function detachWorkspace(rootPath?: string) {
    const u = new URL("/workspace", location.origin);
    if (rootPath) u.searchParams.set("root", rootPath);
    window.open(u.toString(), "_blank");
  }
  // right-click the root folder itself (header button / dropdown row / empty tree area)
  function openRootMenu(root: WorkspaceRoot | null, x: number, y: number) {
    if (!root) return;
    setMenu({ entry: { name: root.name, path: root.path, is_dir: true, size: 0 }, x, y, isRoot: true });
  }
  function setTabText(path: string, text: string) {
    setTabs((ts) => ts.map((t) => (t.path === path ? { ...t, text, dirty: true } : t)));
  }
  async function saveTab(path: string) {
    const t = tabs.find((x) => x.path === path);
    if (!t) return;
    try {
      const res = await api.wsWrite(path, t.text);
      setTabs((ts) => ts.map((x) => (x.path === path ? { ...x, dirty: false, mtime: res.mtime, diskChanged: false } : x)));
      toast(`Saved ${t.name}`, "ok");
    } catch (e: any) { toast(`Save failed: ${e.message}`, "danger"); }
  }
  async function reloadFromDisk(path: string) {
    try {
      const f = await api.wsFile(path);
      setTabs((ts) => ts.map((x) => (x.path === path ? { ...x, text: f.text ?? x.text, kind: f.kind, mtime: f.mtime || 0, dirty: false, diskChanged: false } : x)));
    } catch (e: any) { toast(`Reload failed: ${e.message}`, "danger"); }
  }

  // live-sync: detect external edits (VS Code etc.) and reload non-dirty tabs
  useEffect(() => {
    if (tabs.length === 0) return;
    let alive = true;
    const iv = window.setInterval(async () => {
      if (document.hidden) return;   // nothing visible to live-sync — skip the stat round
      for (const t of tabs) {
        if (t.kind !== "text") continue;
        try {
          const s = await api.wsStat(t.path);
          if (!alive || s.mtime <= (t.mtime || 0) + 0.001) continue;
          if (t.dirty) {
            setTabs((ts) => ts.map((x) => (x.path === t.path ? { ...x, diskChanged: true } : x)));
          } else {
            const f = await api.wsFile(t.path);
            if (!alive) return;
            setTabs((ts) => ts.map((x) => (x.path === t.path && !x.dirty ? { ...x, text: f.text ?? x.text, mtime: f.mtime || s.mtime } : x)));
          }
        } catch { /* deleted/locked — ignore */ }
      }
    }, 2500);
    return () => { alive = false; window.clearInterval(iv); };
  }, [tabs]);

  // live filesystem sync: a backend watcher bumps a version whenever files change on
  // disk (Claude writing/creating files, builds, external editors). Poll it cheaply and,
  // when it moves, refresh the file tree AND reload affected open editors right away —
  // so new/edited files appear instantly instead of waiting on the slow per-file poll.
  const fsVer = useRef(-1);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  useEffect(() => {
    if (!activeRoot) return;
    fsVer.current = -1;                       // prime baseline on the new root's first poll
    let alive = true;
    const norm = (s: string) => s.replace(/\\/g, "/").toLowerCase();
    const tick = async () => {
      try {
        const r = await api.wsChanges(activeRoot.path, fsVer.current);
        if (!alive) return;
        const primed = fsVer.current >= 0;
        fsVer.current = r.version;
        if (!primed || r.paths.length === 0) return;   // first poll only sets the baseline
        setReloadKey((k) => k + 1);                     // refresh the file tree (root + expanded)
        const changed = new Set(r.paths.map(norm));
        for (const t of tabsRef.current) {              // instantly resync any open editors that changed
          if (t.kind !== "text" || !changed.has(norm(t.path))) continue;
          if (t.dirty) { setTabs((ts) => ts.map((x) => (x.path === t.path ? { ...x, diskChanged: true } : x))); continue; }
          try {
            const f = await api.wsFile(t.path);
            if (!alive) return;
            setTabs((ts) => ts.map((x) => (x.path === t.path && !x.dirty ? { ...x, text: f.text ?? x.text, kind: f.kind, mtime: f.mtime || 0 } : x)));
          } catch { /* deleted/locked — ignore */ }
        }
      } catch { /* backend busy — retry next tick */ }
    };
    const iv = window.setInterval(() => { if (!document.hidden) tick(); }, 1000);
    const onVis = () => { if (!document.hidden) tick(); };   // catch up instantly on refocus
    document.addEventListener("visibilitychange", onVis);
    return () => { alive = false; window.clearInterval(iv); document.removeEventListener("visibilitychange", onVis); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRoot]);

  async function newFolder() {
    if (!activeRoot) return;
    const name = await askInput("New folder", { placeholder: "folder name" }); if (!name) return;
    try { await api.wsMkdir(`${activeRoot.path}/${name}`); setReloadKey((k) => k + 1); } catch (e: any) { toast(e.message, "danger"); }
  }
  async function newFile() {
    if (!activeRoot) return;
    const name = await askInput("New file", { placeholder: "file name (e.g. index.ts)" }); if (!name) return;
    try { await api.wsNewFile(`${activeRoot.path}/${name}`); setReloadKey((k) => k + 1); } catch (e: any) { toast(e.message, "danger"); }
  }
  function openPrompts() {
    setActivePath(PROMPTS_TAB);
    setTabs((ts) => ts.some((t) => t.path === PROMPTS_TAB) ? ts
      : [...ts, { path: PROMPTS_TAB, name: "Prompts", kind: "prompts", text: "", dirty: false, mtime: 0 }]);
  }
  function openCheckpoints() {
    setActivePath(CKPT_TAB);
    setTabs((ts) => ts.some((t) => t.path === CKPT_TAB) ? ts
      : [...ts, { path: CKPT_TAB, name: "Checkpoints", kind: "checkpoints", text: "", dirty: false, mtime: 0 }]);
  }
  function openInspect() {
    setActivePath(INSPECT_TAB);
    setTabs((ts) => ts.some((t) => t.path === INSPECT_TAB) ? ts
      : [...ts, { path: INSPECT_TAB, name: "Inspect", kind: "inspect", text: "", dirty: false, mtime: 0 }]);
  }
  function openSkills() {
    setActivePath(SKILLS_TAB);
    setTabs((ts) => ts.some((t) => t.path === SKILLS_TAB) ? ts
      : [...ts, { path: SKILLS_TAB, name: "Skills", kind: "skills", text: "", dirty: false, mtime: 0 }]);
  }
  // "see it for real": serve over http (never file://, which breaks ES modules) and open Chrome.
  // Targets the open HTML file when there is one, else the whole workspace folder.
  async function openInBrowser(root: WorkspaceRoot | null) {
    const tab = tabs.find((t) => t.path === activePath);
    const target = tab?.previewable ? tab.path : (root?.path || "");
    if (!target) { toast("Open a workspace first.", "warn"); return; }
    try {
      const r = await api.wsOpenBrowser(target);
      if (root) setServedUrls((m) => ({ ...m, [root.id]: r.url }));
      // say WHICH page opened — the folder's entry is detected, so this is how you notice
      // if a project with several games picked a different one than you expected
      const how = r.live ? "live dev server" : r.entry || "";
      if (r.ok) {
        toast(`Opened ${how ? `${how} — ` : ""}${r.url}${r.browser !== "default" ? " in Chrome" : ""}`, "ok");
        // a build older than its own source is a snapshot of the past; say so rather than
        // letting an old version of the game look like the current one
        if (r.stale && !r.live) {
          toast(r.dev_script
            ? `That build is older than your code — pick “Start dev server” in the localhost menu for the live version.`
            : `That build is older than your code — rebuild it to see your latest changes.`, "warn");
        }
      } else toast(r.error || `Serving at ${r.url}, but the browser did not launch`, "warn");
    } catch (e: any) {
      toast(e?.message || "could not open the browser", "danger");
    }
  }

  // ── the localhost menu: every page in this project that could BE the app ──────────────
  const [entriesFor, setEntriesFor] = useState<Record<string, EntryInfo[]>>({});
  // The pages menu is anchored in WINDOW coordinates, not inside its header. The header clips —
  // that is how a pane keeps its chrome inside its own cell — and an absolutely-positioned menu
  // in a clipped box would be sliced off at the height of an h-8 bar.
  const [entryMenu, setEntryMenu] = useState<{ left: number; top: number; pane: string } | null>(null);
  const [devBusyFor, setDevBusyFor] = useState<Record<string, boolean>>({});
  // A served URL belongs to ONE project, so it is stored against one — nothing to clear on a
  // switch, and nothing that can leak from one pane into the next.
  useEffect(() => {
    const root = entryMenu ? rootOfId(entryMenu.pane) : null;
    if (!root?.path) return;
    api.wsEntries(root.path)
      .then((r) => setEntriesFor((m) => ({ ...m, [root.id]: r.entries })))
      .catch(() => setEntriesFor((m) => ({ ...m, [root.id]: [] })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entryMenu]);

  async function openEntry(root: WorkspaceRoot | null, e: EntryInfo) {
    setEntryMenu(null);
    try {
      const r = e.dev?.url ? await api.wsOpenUrl(e.dev.url) : await api.wsOpenBrowser(e.path);
      if (root) setServedUrls((m) => ({ ...m, [root.id]: r.url }));
      toast(`Opened ${e.dev?.url ? "live dev server" : e.rel} — ${r.url}`, "ok");
    } catch (err: any) { toast(err?.message || "could not open it", "danger"); }
  }

  async function startDev(root: WorkspaceRoot | null, e: EntryInfo) {
    setEntryMenu(null);
    const rid = root?.id || "";
    setDevBusyFor((m) => ({ ...m, [rid]: true }));
    toast(`Starting \`npm run ${e.dev?.script}\` in ${e.project_name}…`, "info");
    try {
      const r = await api.wsStartDev(e.project);
      if (r.ok && r.url) {
        const o = await api.wsOpenUrl(r.url).catch(() => null);
        setServedUrls((m) => ({ ...m, [rid]: o?.url || r.url || "" }));
        toast(`Dev server up — ${r.url}`, "ok");
      } else {
        toast(r.error || "the dev server did not start", "danger");
      }
    } catch (err: any) { toast(err?.message || "could not start the dev server", "danger"); }
    finally { setDevBusyFor((m) => ({ ...m, [rid]: false })); }
  }
  function openTodos() {
    setActivePath(TODOS_TAB);
    setTabs((ts) => ts.some((t) => t.path === TODOS_TAB) ? ts
      : [...ts, { path: TODOS_TAB, name: "Phases", kind: "todos", text: "", dirty: false, mtime: 0 }]);
  }

  // Pre-build this workspace's code graph the moment it's opened, so graphify is ready BEFORE your
  // first message (backend no-ops if the toggle is off; installs graphify if missing; rate-limited).
  useEffect(() => { if (activeRoot?.path) api.graphifyPrebuild(activeRoot.path).catch(() => {}); }, [activeRoot?.path]);
  const ccDefaults = (pid: string, paneId = focusPane) => {
    const chatAgent = agentOf(pid, paneId);
    // agent-aware: when the composer is set to an alternate engine, sends (compact / answering a
    // question) go to THAT engine with ITS saved model/effort/mode — not Claude's. This used to
    // name Kimi only, so a Qwen or custom-provider chat quietly sent its follow-ups to Claude.
    const alt = !!altFeedPrefix(chatAgent, customAgents);
    const agent = alt ? chatAgent : "claude";
    // Exactly what the chat box sends, effort per model and fork included: a send that differs
    // from the running session restarts it with the difference (sendPrefs.ts). Not `thinking`:
    // that is appended to the message text, and "/compact ultrathink" is a different command.
    const s = sendSettings(pid, agent);
    return { model: s.model, permission_mode: s.permission_mode, effort: s.effort, fork: s.fork, agent };
  };
  const [ctxNonce, setCtxNonce] = useState(0);
  async function compactNow(root: WorkspaceRoot | null, paneId: string) {
    if (!root?.id) return;
    try {
      // `/compact` means different things to different engines and the backend is where that is
      // decided: Claude's CLI compacts its own session, Codex maps it to thread/compact/start, and
      // the DeepSeek adapter closes its live runtime so the next message rebuilds from the bounded
      // tail (see `deepseek_session._compact`). This call used to be assumed to be Claude's, which
      // is why the button on a DeepSeek pane asked the model what "/compact" meant.
      const r: any = await api.sessionSend(root.id, { message: "/compact", ...ccDefaults(root.id, paneId), path: root.path });
      if (r.ok) {
        toast(r.compacted
          ? `DeepSeek context compacted — the next message carries ~${Math.max(1, Math.round((r.rebuilt_chars || 0) / 4 / 1000))}k tokens instead of the whole session`
          : "Sent /compact — compacting this session…", "ok");
        setCtxNonce((n) => n + 1);
      } else toast(r.error || "compact failed", "danger");
    } catch (e: any) { toast(e.message, "danger"); }
  }
  async function answerQuestion(paneId: string, root: WorkspaceRoot | null, text: string) {
    if (!root?.id || !text.trim()) return;
    const mode = sessOf(paneId);
    try {
      // `path`: a folder that has only talked to Codex has no Claude transcript for the backend to
      // find the folder in, so an answer sent without it could not be delivered
      const r = await api.sessionSend(root.id, { message: text, ...ccDefaults(root.id, paneId), path: root.path,
        session: mode.type === "session" ? mode.id : "", new_session: mode.type === "new" });
      if (r.ok) setSess(paneId, { type: "live" }); else toast(r.error || "send failed", "danger");
    } catch (e: any) { toast(e.message, "danger"); }
  }
  function selectRoot(r: WorkspaceRoot) {
    setRoots((rs) => rs.some((x) => x.path === r.path) ? rs : [...rs, r]);
    openInPane(r);
  }
  async function newProject() {
    if (!activeRoot) { openFolder(); return; }
    const name = await askInput("New project", { placeholder: "folder name (created next to the current project)" }); if (!name) return;
    const parent = activeRoot.path.replace(/[\\/][^\\/]*$/, "");
    try { const r = await api.wsNewProject(parent, name); selectRoot(r); toast(`Created & opened ${r.name}`, "ok"); }
    catch (e: any) { toast(e.message, "danger"); }
  }
  async function openFolder() {
    const path = await askInput("Open folder", { placeholder: "full path inside home / Downloads / Desktop / Documents" });
    if (!path) return;
    try { const r = await api.wsAddRoot(path.trim()); selectRoot(r); toast(`Opened ${r.name}`, "ok"); }
    catch (e: any) { toast(e.message, "danger"); }
  }
  // The folders on this PC that look like projects, opened in one go: Claude Code's own registry
  // first, then the Desktop, Documents, Downloads and the usual dev folders. The first run on a
  // new PC does this by itself when nothing is open; this is the same thing on demand.
  async function findProjects() {
    try {
      const r = await api.wsDiscover(true);
      if (r.added) {
        const list = applyRootPrefs(await api.wsRoots());
        setRoots(list);
        toast(`Opened ${r.added} project folder${r.added === 1 ? "" : "s"}`, "ok");
      } else {
        toast(r.found.length ? "Every project folder found is already open" : "No project folders found in the usual places", "ok");
      }
    } catch (e: any) { toast(e.message, "danger"); }
  }
  async function removeRoot(r: WorkspaceRoot) {
    try {
      if (r.opened) {
        await api.wsRemoveRoot(r.path);                 // opened folder → drop from workspace_roots
      } else {                                          // detected project → hide locally
        try {
          const h: string[] = JSON.parse(localStorage.getItem("ws-hidden") || "[]");
          if (!h.includes(wsNorm(r.path))) { h.push(wsNorm(r.path)); localStorage.setItem("ws-hidden", JSON.stringify(h)); }
        } catch { /* ignore */ }
      }
      setRoots((rs) => rs.filter((x) => x.path !== r.path));
      setTabs((ts) => ts.filter((t) => !t.path.startsWith(r.path)));
      if (activeRoot?.path === r.path) setActiveRoot(roots.find((x) => x.path !== r.path) || null);
      toast(`Removed ${r.name}`, "ok");
    } catch (e: any) { toast(e.message, "danger"); }
  }
  async function renameRoot(root: WorkspaceRoot) {
    const name = await askInput(`Rename project "${root.name}"`, { initial: root.name, placeholder: "new folder name" });
    if (!name || name.trim() === root.name) return;
    try {
      const r = await api.wsRenameRoot(root.path, name.trim());
      const oldN = wsNorm(root.path), newN = wsNorm(r.path);
      // every path-keyed preference follows the rename (order, hidden, pins, last-open)
      for (const key of ["ws-order", "ws-hidden", "ws-pinned"]) {
        try {
          const a: string[] = JSON.parse(localStorage.getItem(key) || "[]");
          localStorage.setItem(key, JSON.stringify(a.map((x) => (x === oldN ? newN : x))));
        } catch { /* ignore */ }
      }
      if (wsNorm(localStorage.getItem("ws-root") || "") === oldN) localStorage.setItem("ws-root", r.path);
      setPinned((ps) => ps.map((x) => (x === oldN ? newN : x)));
      // open editor tabs keep working inside the renamed folder
      const oldP = root.path.replace(/\\/g, "/").toLowerCase();
      const remap = (p: string) => (p.replace(/\\/g, "/").toLowerCase().startsWith(oldP) ? r.path + p.slice(root.path.length) : p);
      setTabs((ts) => ts.map((t) => ({ ...t, path: remap(t.path) })));
      setActivePath((p) => (p ? remap(p) : p));
      const list = applyRootPrefs(await api.wsRoots());
      setRoots(list);
      if (activeRoot && wsNorm(activeRoot.path) === oldN)
        setActiveRoot(list.find((x) => wsNorm(x.path) === newN) || null);
      toast(`Renamed to ${r.name} — Claude's context moved with it (${r.migrated_sessions} session folder${r.migrated_sessions === 1 ? "" : "s"} migrated)`, "ok");
    } catch (e: any) { toast(`Rename failed: ${e.message}`, "danger"); }
  }
  function reorderRoots(fromPath: string, toPath: string) {
    setRoots((rs) => {
      const from = rs.findIndex((x) => x.path === fromPath);
      const to = rs.findIndex((x) => x.path === toPath);
      if (from < 0 || to < 0 || from === to) return rs;
      const next = rs.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      try { localStorage.setItem("ws-order", JSON.stringify(next.map((x) => wsNorm(x.path)))); } catch { /* ignore */ }
      return next;
    });
  }

  const activeTab = tabs.find((t) => t.path === activePath) || null;
  const fileOpen = tabs.length > 0;

  // ---- panes ---------------------------------------------------------------
  // A pane is either an agent or a set of files. A file remembers a pane id, and a pane id that
  // is no longer on the grid simply does not match, so the file shows up in the first pane that
  // is — no cleanup pass, and nothing can end up in a pane that is not on screen.
  const editorIds = layout.panes.filter((p) => p.kind !== "chat").map((p) => p.id);
  const chatPanes = layout.panes.filter((p) => p.kind === "chat");
  const chromeCli = cliAll || (chatPanes.length <= 1 && cliLook);

  // Which project an agent pane holds.
  //
  // A pane with no `ref` FOLLOWS the rail — that is the single-agent workspace, unchanged: pick a
  // project on the left and the one agent goes there. Naming a project pins the pane. The moment
  // a second agent appears every pane is pinned (see `pinAll`), because two panes both following
  // the rail would move together and there would be no way to hold two projects at once.
  const rootOf = (pane: Pane): WorkspaceRoot | null =>
    pane.ref ? (roots.find((r) => r.id === pane.ref) || null) : activeRoot;
  const rootOfId = (paneId: string): WorkspaceRoot | null => {
    const pane = layout.panes.find((p) => p.id === paneId);
    return pane ? rootOf(pane) : null;
  };
  /** Every agent pane says which project it holds, so none of them is following the rail. */
  const pinAll = (panes: Pane[]) => panes.map((p) =>
    p.kind === "chat" && !p.ref ? { ...p, ref: rootOf(p)?.id || "" } : p);

  // ---- two agents, one project -------------------------------------------------------------
  //
  // The backend keeps ONE session per project and engine. Two panes on one folder with the same
  // engine are therefore not two agents: they are one conversation in two windows, and "new chat"
  // in one of them used to kill the other's turn. So a pane may not take an engine that another
  // pane already runs on the same project; the second agent gets another engine, or — the safe
  // way — a worktree of its own, where it has its own folder, files and session.
  /** The backend session an agent choice lands in, or "" when it holds none of ours. A terminal
   *  for Claude resumes the Claude conversation, so it counts as Claude; other CLIs run alone. */
  const engineKey = (a: string) => (a === "term:claude" ? "claude" : a.startsWith("term:") ? "" : a);
  const ENGINE_LABEL: Record<string, string> = { claude: "Claude", codex: "Codex", "deepseek-harness": "DeepSeek" };
  const engineLabel = (a: string) => ENGINE_LABEL[engineKey(a)] || engineKey(a) || a;
  /** Another chat pane that already runs `agent` on this project, if any. */
  function clashPane(paneId: string, rootId: string, agent: string, panes: Pane[] = layout.panes): Pane | null {
    const k = engineKey(agent);
    if (!k || !rootId) return null;
    return panes.find((p) => p.kind === "chat" && p.id !== paneId
      && (p.ref || activeRoot?.id || "") === rootId && engineKey(agentOf(rootId, p.id)) === k) || null;
  }
  /** An engine no other pane runs on this project: the other of Claude/Codex first, then DeepSeek. */
  function freeEngine(paneId: string, rootId: string, prefer: string, panes: Pane[] = layout.panes): string {
    const order = [prefer, prefer === "codex" ? "claude" : "codex", "claude", "codex", "deepseek-harness"];
    return order.find((a) => !clashPane(paneId, rootId, a, panes)) || prefer;
  }
  const clashMessage = (agent: string) =>
    `${engineLabel(agent)} already runs on this project in another pane. Two panes on one engine `
    + "share one session and stop each other's turns. Pick another engine here, or open the second "
    + "agent in a worktree (the + button).";
  /** After a pane moves to a project, keep it off an engine another pane already holds there. */
  function keepEngineFree(paneId: string, rootId: string, panes: Pane[]) {
    const mine = agentOf(rootId, paneId);
    if (!clashPane(paneId, rootId, mine, panes)) return;
    const free = freeEngine(paneId, rootId, mine, panes);
    writePaneAgent(paneId, rootId, free);
    setAgentTick((n) => n + 1);
    toast(`${engineLabel(mine)} already runs on this project in another pane, so this pane uses ${engineLabel(free)}.`, "info");
  }

  /** Point one agent pane at a project. */
  function retarget(paneId: string, root: WorkspaceRoot) {
    const panes = pinAll(layout.panes).map((p) => p.id === paneId ? { ...p, ref: root.id } : p);
    setLayout({ ...layout, panes });
    keepEngineFree(paneId, root.id, panes);
    setSess(paneId, { type: "live" });
    setFocusPane(paneId);
    setActiveRoot(root);
  }

  /** "+" on an agent pane: where the second agent goes. */
  const [secondMenu, setSecondMenu] = useState<{ left: number; top: number; pane: string } | null>(null);
  const [secondBusy, setSecondBusy] = useState(false);
  /** A second agent in the SAME folder, on an engine the first one is not using. Both edit the
   *  same files, so the toast says so once. */
  function secondAgentHere(paneId: string, root: WorkspaceRoot) {
    const next = splitChatPane({ ...layout, panes: pinAll(layout.panes) }, paneId, root.id);
    const added = next.panes.find((p) => !layout.panes.some((old) => old.id === p.id));
    const mine = agentOf(root.id, paneId);
    writePaneAgent(paneId, root.id, mine);
    if (added) writePaneAgent(added.id, root.id, freeEngine(added.id, root.id, mine === "codex" ? "claude" : "codex", next.panes));
    setLayout(next); setMaxPane(null); setAgentTick((n) => n + 1);
    if (added) setFocusPane(added.id);
    toast("Both agents now edit the same files. Give them different files to work on.", "info");
  }
  /** A second agent in a NEW WORKTREE of this repository: its own folder, branch and session, so
   *  nothing the two agents do can collide. Made in one click; merge it back from Source control. */
  async function secondAgentWorktree(paneId: string, root: WorkspaceRoot) {
    const git = gitInfo[wsNorm(root.path)];
    const stamp = new Date().toISOString().slice(5, 16).replace(/[-:T]/g, "");
    const name = `${root.name}-agent-${stamp}`;
    if (git?.dirty && !window.confirm(
      `${root.name} has uncommitted changes. A worktree starts from the last commit, so the second `
      + "agent will NOT see them.\n\nCommit first if it needs them. Create the worktree anyway?")) return;
    setSecondBusy(true);
    try {
      const r = await api.wtCreate({ path: root.path, name, branch: "", base: "", dest: "", existing_branch: false });
      if (!r.ok) { toast(r.error || "could not create the worktree", "danger"); return; }
      if (r.root_error) { toast(r.root_error, "warn"); return; }
      const list = applyRootPrefs(await api.wsRoots());
      setRoots(list);
      loadGitMap();
      const made = list.find((x) => wsNorm(x.path) === wsNorm(r.path || ""));
      if (!made) { toast(`Worktree created at ${r.path}, but it is not in the project list yet.`, "warn"); return; }
      const next = splitChatPane({ ...layout, panes: pinAll(layout.panes) }, paneId, root.id);
      const added = next.panes.find((p) => !layout.panes.some((old) => old.id === p.id));
      if (added) {
        next.panes = next.panes.map((p) => p.id === added.id ? { ...p, ref: made.id } : p);
        writePaneAgent(added.id, made.id, agentOf(root.id, paneId));
      }
      setLayout(next); setMaxPane(null); setAgentTick((n) => n + 1);
      if (added) { setFocusPane(added.id); setSess(added.id, { type: "live" }); }
      toast(`Second agent in worktree "${made.name}" on branch ${r.branch}. It has its own files and `
        + "session. Merge the branch back from Source control when it is done.", "ok");
    } catch (e: any) {
      toast(`Worktree failed: ${e?.message || e}`, "danger");
    } finally {
      setSecondBusy(false);
    }
  }
  /** Watch ONE subagent in a pane of its own.
   *
   *  It takes the first pane that is not busy: a file pane with nothing in it, else the pane you
   *  were last in, else a new pane made by growing the shape. Never an agent's pane — that is
   *  somebody's running conversation. */
  const openAgents = () => setAgentsPanel((v) => !v);

  function openAgentPane(agentId: string, rootId = "", engine = "") {
    const spare = layout.panes.find((p) => p.kind === "editor" && !tabsIn(p.id).length)
      || layout.panes.find((p) => p.kind === "editor" && p.id === focusPane)
      || layout.panes.find((p) => p.kind === "editor");
    if (!spare) {
      // Every pane is taken. Grow rather than evict, then land in the pane that appears.
      pick(GROW[presetId]);
      setPendingAgent({ id: agentId, root: rootId, engine });
      return;
    }
    engineOnPane(spare.id, rootId, engine);
    setLayout({ ...layout, panes: layout.panes.map((p) =>
      p.id === spare.id ? { ...p, kind: "agent" as const, ref: agentId } : p) });
    setFocusPane(spare.id);
    flash(spare.id);
  }
  /** WHICH ENGINE'S AGENT LOG THIS PANE READS.
   *
   *  An agent pane is addressed by the parent's project id, and for an alternate engine that id
   *  carries the engine (`codex--<folder>` — see `feedOf`). It is resolved from the pane's stored
   *  agent, which an EDITOR pane does not have, so it fell through to `folderAgent`: the folder's
   *  pin, else whatever engine was last picked ANYWHERE. In a folder whose chat box is Claude —
   *  the default — clicking a Codex agent in the rail's subagents list therefore pointed the pane
   *  at Claude's transcript layout, and Claude has never heard of that agent. The agent's own
   *  engine is known at the call site, so it is written down instead of guessed. */
  function engineOnPane(paneId: string, rootId: string, engine: string) {
    if (engine && rootId) { writePaneAgent(paneId, rootId, engine); setAgentTick((n) => n + 1); }
  }
  /** An agent asked for while the grid was still growing — with the engine it belongs to, or it
      would arrive in the new pane as somebody else's. */
  const [pendingAgent, setPendingAgent] = useState<{ id: string; root: string; engine: string } | null>(null);
  // A command handed over from the chat box, waiting for the terminal to come up and type it.
  const [handOff, setHandOff] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!pendingAgent) return;
    const spare = layout.panes.find((p) => p.kind === "editor" && !tabsIn(p.id).length);
    if (!spare) return;
    engineOnPane(spare.id, pendingAgent.root, pendingAgent.engine);
    setLayout({ ...layout, panes: layout.panes.map((p) =>
      p.id === spare.id ? { ...p, kind: "agent" as const, ref: pendingAgent.id } : p) });
    setFocusPane(spare.id);
    setPendingAgent(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAgent, layout.panes.length]);

  /** Send a subagent more work.
   *
   *  Through the parent, not to the agent: the id is only meaningful to SendMessage, and
   *  SendMessage is a tool the conversation holds, not an endpoint the Studio can call. */
  function continueAgent(root: WorkspaceRoot | null, aid: string, text: string) {
    if (!root?.id || !text.trim()) return;
    const msg = `Use SendMessage with to: '${aid}' to continue that agent. Message:

${text.trim()}`;
    api.sessionSend(root.id, { message: msg, ...ccDefaults(root.id) })
      .then((r) => { if (!r.ok) toast(r.error || "send failed", "danger"); })
      .catch((e: any) => toast(e.message, "danger"));
  }

  /** Turn a spare pane into a second agent. */
  function makeAgent(paneId: string) {
    const target = activeRoot;
    const panes = pinAll(layout.panes).map((p) =>
      p.id === paneId ? { ...p, kind: "chat" as const, ref: target?.id || "" } : p);
    setLayout({ ...layout, panes });
    if (target?.id) keepEngineFree(paneId, target.id, panes);
    setSess(paneId, { type: "live" });
    setFocusPane(paneId);
  }
  /** ...and back into a place for files. Never the last one — a workspace with no agent in it
   *  has no way to get one back. */
  function closeAgent(paneId: string) {
    if (chatPanes.length < 2) return;
    setLayout({ ...layout, panes: layout.panes.map((p) =>
      p.id === paneId ? { ...p, kind: "editor" as const, ref: undefined } : p) });
  }
  /** Picking a project in the rail.
   *
   *  If it is ALREADY in a pane, this is "show me that one" — go to it. Filling the focused pane
   *  instead would put the same project on screen twice and quietly take away whatever that pane
   *  was holding, which is not what clicking a thing you can already see should ever mean.
   *
   *  Otherwise it fills the pane you were last in — which, with one agent on screen, is the only
   *  pane there is, so this is exactly what it always did.
   *
   *  Dragging is left alone: that gesture names a pane out loud, so it is obeyed even when it
   *  means having one project open twice. */
  function openInPane(r: WorkspaceRoot) {
    const already = chatPanes.find((p) => rootOf(p)?.id === r.id);
    if (already) {
      setFocusPane(already.id);
      setActiveRoot(r);
      // Full screen on some other pane: stay full screen, on this one.
      if (maxPane && maxPane !== already.id) setMaxPane(already.id);
      flash(already.id);
      return;
    }
    const target = chatPanes.find((p) => p.id === focusPane) || chatPanes[0];
    if (target) retarget(target.id, r);
    else setActiveRoot(r);
  }
  const homeOf = (path: string) => {
    const want = tabPane[path];
    return want && editorIds.includes(want) ? want : (editorIds[0] || "");
  };
  const tabsIn = (paneId: string) => tabs.filter((t) => homeOf(t.path) === paneId);
  const activeIn = (paneId: string) => {
    const mine = tabsIn(paneId);
    return mine.find((t) => t.path === paneFocus[paneId]) || mine[mine.length - 1] || null;
  };
  /** Send a newly opened file to the pane you were last working in. */
  function landIn(path: string) {
    const target = focusPane && editorIds.includes(focusPane) ? focusPane : editorIds[0];
    if (target) setTabPane((m) => ({ ...m, [path]: target }));
  }
  function focusTab(paneId: string, path: string) {
    setPaneFocus((m) => ({ ...m, [paneId]: path }));
    setActivePath(path);
    setFocusPane(paneId);
  }
  /** Drag a tab into another pane — the whole point of the grid: an image or a rendered page,
   *  in the middle, at a size you can actually judge, with the chat still beside it. */
  function moveTab(path: string, paneId: string) {
    if (!paneId || homeOf(path) === paneId) return;
    setTabPane((m) => ({ ...m, [path]: paneId }));
    focusTab(paneId, path);
  }

  // The shape on screen is the shape you picked. It used to be derived — an empty file pane was
  // hidden and its room given to the agent — and that quietly made the second agent unreachable:
  // choosing "Two" with no file open still drew one pane, so the "open a project here" button
  // inside the empty one could never be clicked. Closing the last file is now an ACTION that
  // shrinks the shape (below), not a rule that hides a pane you asked for.
  //
  // Opening a file with nowhere to put it grows the shape instead of taking a pane, because a
  // pane that is an agent is somebody's running conversation. `lastShape` carries the room you
  // had back across the round trip.
  useEffect(() => {
    if (!fileOpen || editorIds.length) return;
    const back = lastShape.current;
    lastShape.current = null;
    pick(back && paneCount(back) > paneCount(presetId) ? back : GROW[presetId]);
  }, [fileOpen, editorIds.length, presetId, pick]);

  /** The grip and the full-screen button every pane carries, plus the drop that trades two
   *  panes' places. Only their grid lines are exchanged, so both keep everything they hold. */
  function paneChrome(pane: Pane, isMax: boolean, toggleMax: () => void) {
    return (
      <span className="flex items-center gap-0.5 shrink-0 pl-1 pr-1">
        <span draggable
          onDragStart={(e) => { e.dataTransfer.setData(PANE_DND, pane.id); e.dataTransfer.effectAllowed = "move"; }}
          className="p-0.5 rounded text-muted/50 hover:text-brand cursor-grab active:cursor-grabbing"
          title="Drag onto another pane to trade places">
          <GripVertical size={12} />
        </span>
        <button onClick={toggleMax} className="p-0.5 rounded text-muted/50 hover:text-brand"
          title={isMax ? "Back to the layout" : "Fill the window with this pane"}>
          {isMax ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
        </button>
      </span>
    );
  }
  // `getData` is unreadable during a dragover, but `types` is — which is how a pane knows to
  // light up for a project or a file tab without being told which one until the drop.
  const paneOver = (paneId: string, takesTabs: boolean) => (e: React.DragEvent) => {
    const t = e.dataTransfer.types;
    if (!(t.includes(PANE_DND) || t.includes(ROOT_DND) || (takesTabs && t.includes(TAB_DND)))) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    // dragover fires many times a second; only ever set state when it actually changed.
    setDropHint((h) => (h === paneId ? h : paneId));
  };
  const paneLeave = (e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropHint("");
  };
  const paneDrop = (paneId: string, takesTabs: boolean) => (e: React.DragEvent) => {
    setDropHint("");
    const path = e.dataTransfer.getData(ROOT_DND);
    if (path) {
      e.preventDefault(); e.stopPropagation();
      const r = roots.find((x) => wsNorm(x.path) === wsNorm(path));
      if (r) dropProjectInto(paneId, r);
      setRootDrag(null);
      return;
    }
    const tab = takesTabs ? e.dataTransfer.getData(TAB_DND) : "";
    if (tab) { e.preventDefault(); e.stopPropagation(); moveTab(tab, paneId); return; }
    const other = e.dataTransfer.getData(PANE_DND);
    if (other) { e.preventDefault(); e.stopPropagation(); trade(other, paneId); }
  };

  /** Drop a project into a pane: that pane becomes its agent.
   *
   *  One write, not "make it an agent" followed by "point it at the project" — two calls in a row
   *  would both read the same stale layout and the second would undo the first. A pane that was
   *  holding files loses them here, and they reappear the moment the grow rule gives them a pane
   *  of their own, so nothing is dropped on the floor. */
  function dropProjectInto(paneId: string, root: WorkspaceRoot) {
    const panes = pinAll(layout.panes).map((p) =>
      p.id === paneId ? { ...p, kind: "chat" as const, ref: root.id } : p);
    setLayout({ ...layout, panes });
    keepEngineFree(paneId, root.id, panes);
    setSess(paneId, { type: "live" });
    setFocusPane(paneId);
    setActiveRoot(root);
  }

  /** The wash over a pane: "drop it here" while dragging, and a fading ring when a click sent
   *  you to a pane you already had. Both pane kinds wear it. */
  function dropVeil(paneId: string) {
    if (dropHint !== paneId) {
      if (flashPane !== paneId) return null;
      return <div className="pane-flash absolute inset-0 z-30 pointer-events-none ring-2 ring-inset ring-brand" />;
    }
    return (
      <div className="absolute inset-0 z-30 pointer-events-none flex items-center justify-center
        ring-2 ring-inset ring-brand/70 bg-brand/10">
        {rootDrag && (
          <span className="px-3 py-1.5 rounded-md bg-panel border border-brand/50 text-brand text-xs font-medium shadow-card flex items-center gap-1.5">
            <Folder size={13} /> open {rootDrag.name} here
          </span>
        )}
      </div>
    );
  }

  /** One project's agent: its conversation, or — when a CLI has been opened for that project —
   *  that terminal. A pane like any other, which is what lets two projects sit side by side. */
  function chatPane(pane: Pane, isMax: boolean, toggleMax: () => void) {
    const root = rootOf(pane);
    const rid = root?.id || "";
    const feed = feedOf(root, pane.id);
    const term = termOf(rid, pane.id);
    const mode = sessOf(pane.id);
    const git = root ? gitInfo[wsNorm(root.path)] : undefined;
    const served = servedUrls[rid] || "";
    const pages = entriesFor[rid] || [];
    const focused = focusPane === pane.id && chatPanes.length > 1;
    const cli = cliOf(rid);
    return (
      <div className="relative flex-1 min-h-0 flex flex-col bg-bg text-text"
        data-skin={cli ? "cli" : undefined}
        onMouseDownCapture={() => {
          setFocusPane(pane.id);
          if (root && root.id !== activeRoot?.id) setActiveRoot(root);
        }}
        // The whole pane is the target for a project, not only its header — you are aiming at
        // the agent you want replaced, and that is the big rectangle, not the 32px bar on top.
        onDragOver={paneOver(pane.id, false)} onDragLeave={paneLeave} onDrop={paneDrop(pane.id, false)}>
        {dropVeil(pane.id)}
        <div className="pane-head h-8 shrink-0 bg-panel border-b border-line flex items-center px-2 text-xs">
          {/* Two groups, not one flat row. The left one is squeezed and clipped; the right one
              never is. Flat, the row grew straight past the pane and painted the localhost chip
              on top of the file beside it — a pane has to end where its cell ends.
              `pane-head` is a CSS container: the [data-opt] items below drop out in order as the
              pane gets narrow, so six panes across still leaves a readable header in each. */}
          <div className="pane-head-left flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
            {/* Which project this agent is in, and the way to change it. It replaces the words
                "Claude Code", which said nothing the model badge beside it does not say better —
                and with two agents on screen the project is the one thing you cannot work
                without. It lights up when it is the pane the explorer is following. */}
            <button
              onClick={(ev) => {
                const r = ev.currentTarget.getBoundingClientRect();
                setProjMenu((v) => v && v.pane === pane.id ? null : {
                  left: Math.max(8, Math.min(r.left, window.innerWidth - 272)),
                  top: r.bottom + 4, pane: pane.id,
                });
              }}
              title={root ? `${root.path}\nClick to hold a different project in this pane`
                          : "Pick a project for this pane"}
              className={cls("shrink-0 flex items-center gap-1 px-1.5 h-5 rounded border font-medium",
                focused ? "border-brand/60 bg-brand/10 text-brand"
                        : "border-line bg-panel2/60 text-text/80 hover:text-brand hover:border-brand/50")}>
              <Folder size={11} className={cls("shrink-0", focused ? "text-brand" : "text-warn")} />
              <span className="truncate max-w-[8rem]">{root?.name || "pick a project"}</span>
              <ChevronDown size={10} className="shrink-0 opacity-60" />
            </button>
            {rid && <ModelBadge projectId={feed} folderId={rid} cli={cli} />}
            {/* Goes before the branch does. Which branch an agent is on is the thing you cannot
                afford to be wrong about with two of them side by side; which saved conversation
                it is on, you rarely change. */}
            {root && (
              <span data-opt="3" className="min-w-0">
                <ConversationControl root={root} feedId={feed} mode={mode} setMode={(m) => setSess(pane.id, m)} />
              </span>
            )}
            {/* feed (not root.id) so the working indicator tracks the SAME session the feed
                renders — in Kimi mode they are different universes. First to go when narrow:
                the feed shows the same running row at its foot. */}
            {rid && <span data-opt="1"><WorkingPulse projectId={feed} className="ml-0.5" /></span>}
            {root && (
              <div data-opt="2" className="relative shrink-0 flex items-center">
                <button className="pl-1.5 pr-1 h-5 rounded-l border border-r-0 border-line bg-panel2/60 text-[10px] font-mono text-muted hover:text-brand hover:border-brand/50 flex items-center gap-1"
                  title={`Open this project's app in Chrome. A running dev server is preferred, otherwise the newest built page.${served ? `\nLast: ${served}` : ""}`}
                  onClick={() => openInBrowser(root)}>
                  {devBusyFor[rid] ? <Loader2 size={11} className="animate-spin" /> : <Globe size={11} />}
                  {served ? served.replace(/^https?:\/\//, "").replace(/\/$/, "") : "localhost"}
                </button>
                <button className="px-1 h-5 rounded-r border border-line bg-panel2/60 text-muted hover:text-brand hover:border-brand/50"
                  title="Other pages in this project"
                  onClick={(ev) => {
                    const r = (ev.currentTarget.parentElement as HTMLElement).getBoundingClientRect();
                    setEntryMenu((v) => v && v.pane === pane.id ? null : {
                      left: Math.max(8, Math.min(r.left, window.innerWidth - 424)),
                      top: r.bottom + 4, pane: pane.id,
                    });
                  }}>
                  <ChevronDown size={10} />
                </button>
                {entryMenu?.pane === pane.id && (
                  <>
                    <div className="fixed inset-0 z-[60]" onClick={() => setEntryMenu(null)} />
                    <div className="fixed z-[61] card p-1 w-[26rem] max-h-80 overflow-auto shadow-card"
                      style={{ left: entryMenu.left, top: entryMenu.top }}>
                      <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted">
                        Pages in {root.name}
                      </div>
                      {!pages.length && <div className="px-2 py-2 text-xs text-muted">No page found in this project.</div>}
                      {pages.map((e, i) => (
                        <button key={e.path} onClick={() => openEntry(root, e)}
                          className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2">
                          <span className={cls("w-1.5 h-1.5 rounded-full shrink-0",
                            e.dev?.url ? "bg-ok" : e.stale ? "bg-warn" : "bg-muted/40")} />
                          <span className="min-w-0 flex-1">
                            <span className="text-xs font-mono truncate block">{e.rel}</span>
                            <span className="text-[10px] text-muted">
                              {e.dev?.url ? `live dev server · ${e.dev.url.replace(/^https?:\/\//, "")}`
                                : `${e.built ? "built" : "page"} · ${timeAgo(e.mtime)}${e.stale ? " · older than your code" : ""}`}
                            </span>
                          </span>
                          {i === 0 && <span className="text-[9px] text-brand shrink-0">default</span>}
                        </button>
                      ))}
                      {pages.some((e) => e.dev?.script && !e.dev?.url) && <div className="h-px bg-line my-1" />}
                      {pages.filter((e) => e.dev?.script && !e.dev?.url).map((e) => (
                        <button key={`dev-${e.project}`} onClick={() => startDev(root, e)}
                          className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2 text-xs">
                          <Play size={11} className="text-ok shrink-0" />
                          Start dev server — <span className="font-mono">npm run {e.dev.script}</span> in {e.project_name}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
            {/* Source control for whatever this pane holds — a project, a worktree or a scoped
                subfolder. It shows the branch because that is the thing you most need to be sure
                of before you commit, and a dot when there is uncommitted work. */}
            {root && git?.is_repo && (
              <button data-opt="4" className={cls("shrink-0 flex items-center gap-1 px-1.5 h-5 rounded border text-[10px] font-mono",
                cli ? "border-transparent text-muted hover:text-brand"
                        : "border-line bg-panel2/60 text-muted hover:text-brand hover:border-brand/50")}
                title={`Source control · on branch ${git.branch || "?"}${git.dirty ? " · uncommitted changes" : ""}`}
                onClick={() => setGitFor(root)}>
                <GitBranch size={11} />
                <span className="truncate max-w-[7rem]">{git.branch || "git"}</span>
                {git.dirty && <span className="h-1.5 w-1.5 rounded-full bg-warn shrink-0" />}
              </button>
            )}
          </div>
          <div className="flex items-center shrink-0 pl-1">
            {rid && !term && <span data-opt="1"><SpeedMeter projectId={feed} cli={cli} /></span>}
            {rid && !term && <ContextMeter projectId={feed} folderId={rid} className="ml-1" onCompact={() => compactNow(root, pane.id)} refreshSignal={ctxNonce} cli={cli} />}
            {root && <button className="chip ml-1" title="Open a second agent on this project" aria-label="Open second agent"
              disabled={secondBusy}
              onClick={(ev) => {
                const r = ev.currentTarget.getBoundingClientRect();
                setSecondMenu((v) => v && v.pane === pane.id ? null : {
                  left: Math.max(8, Math.min(r.right - 288, window.innerWidth - 296)),
                  top: r.bottom + 4, pane: pane.id,
                });
              }}>{secondBusy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}</button>}
            <PresetButton current={presetId} onPick={pick} className="ml-1" />
            {paneChrome(pane, isMax, toggleMax)}
          </div>
        </div>
        {!root ? (
          // A pane that names a project which is no longer open. Offer the list rather than an
          // apology — the fix is one click and it is the same click the chip above would give.
          <div className="flex-1 min-h-0 overflow-auto p-3">
            <div className="text-xs text-muted mb-2">Pick a project for this pane:</div>
            <div className="space-y-0.5">
              {roots.map((r) => (
                <button key={r.path} onClick={() => retarget(pane.id, r)}
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2 text-xs">
                  <Folder size={13} className="text-warn shrink-0" />
                  <span className="truncate flex-1">{r.name}</span>
                </button>
              ))}
              <button onClick={openFolder}
                className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2 text-xs text-brand">
                <FolderOpen size={13} /> Open a folder…
              </button>
              <button onClick={findProjects} title="Open every folder on this PC that looks like a project"
                className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2 text-xs text-brand">
                <Search size={13} /> Find my projects…
              </button>
            </div>
          </div>
        ) : term ? (
          // The pane BECOMES the agent: its own terminal, its own interface, no composer.
          // Nothing in there can reach the transcript or the send path, which is why a new
          // CLI can be added without any risk at all to the Claude chat.
          // Claude Code gets the project id as well: that turns "open a terminal" into
          // "open THIS conversation in a terminal". Every other CLI has no session of ours
          // to resume, so they keep starting fresh.
          <Suspense fallback={<PaneLoading label="starting the terminal…" />}>
          <AgentTerminal embedded agentId={term} cwd={root.path} onExit={() => backToChat(root, pane.id)}
            typeOnReady={handOff[rid] || ""}
            onTyped={() => setHandOff((h) => { const n = { ...h }; delete n[rid]; return n; })}
            resumeProjectId={term === "claude" ? root.id : undefined}
            projectId={root.id}
            resumeSession={mode.type === "session" ? mode.id : ""}
            resumeFresh={mode.type === "new"} />
          </Suspense>
        ) : (
          <>
            <SessionFeed id={feed} rootPath={root.path} active fast font={wsFont} cliLook={cli}
              notesProjectId={root.id} folderId={root.id}
              onAnswer={(t) => answerQuestion(pane.id, root, t)}
              onOpenAgent={(aid) => openAgentPane(aid, rid, agentOf(rid, pane.id))}
              session={mode.type === "session" ? mode.id : ""} fresh={mode.type === "new"} />
            <ChatComposer projectId={root.id} rootPath={root.path} rootName={root.name} variant="panel"
              selectedAgent={agentOf(rid, pane.id)} onAgentChange={(agent) => {
                if (clashPane(pane.id, rid, agent)) { toast(clashMessage(agent), "warn"); return false; }
                writePaneAgent(pane.id, rid, agent); setSess(pane.id, { type: "live" });
                setAgentTick((n) => n + 1);
              }}
              cliLook={cli}
              session={mode.type === "session" ? mode.id : ""} newSession={mode.type === "new"}
              onSent={() => setSess(pane.id, { type: "live" })}
              onClear={() => setSess(pane.id, { type: "new" })}
              onTerminalCommand={(cmd) => {
                // Same conversation, real interface. The terminal handoff already moves the
                // session across; all this adds is the command, typed on arrival.
                //
                // CLAUDE ONLY, as a second lock on the same door the composer now guards: the
                // handoff resumes the CLAUDE conversation for this folder, so firing it from a pane
                // the user had set to Codex or DeepSeek would answer in a different engine's chat.
                if (agentOf(rid, pane.id) !== "claude") return;
                setHandOff((h) => ({ ...h, [rid]: cmd + ENTER }));
                writePaneAgent(pane.id, rid, "term:claude");
                window.dispatchEvent(new Event("cc-agent"));
                setAgentTick((n) => n + 1);
                toast("Opening the real CLI — /subtask does not run in the chat stream.", "info");
              }} />
          </>
        )}
      </div>
    );
  }

  /** One subagent, live. */
  function agentPane(pane: Pane, isMax: boolean, toggleMax: () => void) {
    const root = activeRoot;
    return (
      <div className="relative flex-1 min-h-0 flex flex-col"
        onMouseDownCapture={() => setFocusPane(pane.id)}
        onDragOver={paneOver(pane.id, false)} onDragLeave={paneLeave} onDrop={paneDrop(pane.id, false)}>
        {dropVeil(pane.id)}
        <div className="absolute top-1 right-1 z-20 flex items-center bg-panel/80 rounded">
          {paneChrome(pane, isMax, toggleMax)}
        </div>
        {/* THIS PANE'S engine, not the folder's. `feedOf(root)` with no pane id resolves the
            FOLDER's agent (Workspace.agentOf -> folderAgent), so with two panes on one folder set
            to different engines the agent pane read the other one's subagents. */}
        <SubAgentPanel projectId={feedOf(root, pane.id)} agentId={pane.ref || ""} cli={cliOf(root?.id || "")}
          onClose={() => setLayout({ ...layout, panes: layout.panes.map((p) =>
            p.id === pane.id ? { ...p, kind: "editor" as const, ref: undefined } : p) })}
          onContinue={(aid, text) => continueAgent(root, aid, text)} />
      </div>
    );
  }

  /** One pane's worth of open files: its own tabs, its own toolbar, its own content. */
  function filePane(pane: Pane, isMax: boolean, toggleMax: () => void) {
    const mine = tabsIn(pane.id);
    const tab = activeIn(pane.id);
    return (
      <div className="relative flex-1 min-h-0 flex flex-col"
        onMouseDownCapture={() => setFocusPane(pane.id)}
        onDragOver={paneOver(pane.id, true)} onDragLeave={paneLeave} onDrop={paneDrop(pane.id, true)}>
        {dropVeil(pane.id)}
        <div className="h-8 shrink-0 bg-panel border-b border-line flex items-stretch">
          {/* only the TABS scroll — Save / Close all stay pinned on the right, so they
              never scroll out of reach when many files are open */}
          <div className="flex items-stretch overflow-x-auto no-scrollbar flex-1 min-w-0">
            {mine.map((t) => (
              <div key={t.path} draggable
                onDragStart={(e) => { e.dataTransfer.setData(TAB_DND, t.path); e.dataTransfer.effectAllowed = "move"; }}
                title={`${t.path}\ndrag onto another pane to open it there`}
                /* The rail on the left says which file this pane is showing. Bright on the one
                   you are reading, dim on the rest — the same two-tone rail the feed uses for a
                   prompt against an answer, so "this is the live one" reads the same way twice. */
                className={cls("group flex items-center gap-1.5 px-3 border-r border-line border-l-2 cursor-pointer text-xs whitespace-nowrap",
                  tab?.path === t.path
                    ? "bg-bg text-text border-l-brand"
                    : "bg-panel text-muted hover:text-text border-l-brand/25")}
                onClick={() => focusTab(pane.id, t.path)}>
                {t.kind === "image" ? <ImageIcon size={12} /> : t.kind === "model" ? <Box size={12} /> : t.kind === "todos" ? <ListChecks size={12} /> : t.kind === "prompts" ? <History size={12} /> : t.kind === "checkpoints" ? <RotateCcw size={12} /> : t.kind === "inspect" ? <Crosshair size={12} /> : t.kind === "skills" ? <Wand2 size={12} /> : <FileIcon size={12} />}
                {t.name}{t.dirty && <span className="text-warn">●</span>}
                {t.diskChanged && <span className="text-accent" title="changed on disk">↻</span>}
                <button className="opacity-0 group-hover:opacity-100 hover:text-danger" onClick={(e) => { e.stopPropagation(); closeTab(t.path); }}><X size={12} /></button>
              </div>
            ))}
            {!mine.length && (
              <span className="self-center pl-2 flex items-center gap-2 text-[11px] text-muted/60 whitespace-nowrap">
                empty
                <button onClick={() => makeAgent(pane.id)}
                  title="Run a second agent here, on a project of its own"
                  className="px-1.5 py-0.5 rounded border border-line text-muted hover:text-brand hover:border-brand/50 flex items-center gap-1">
                  <Sparkles size={10} /> open a project here
                </button>
              </span>
            )}
          </div>
          <div className="flex items-center shrink-0 border-l border-line bg-panel">
            {tab?.previewable && (
              <>
                <button className={cls("px-2.5 text-xs flex items-center gap-1", previewOn[tab.path] ? "text-brand" : "text-muted hover:text-text")}
                  title={previewOn[tab.path] ? "Show the source" : "Render this page (served over http, so ES modules and relative files work)"}
                  onClick={() => setPreviewOn((m) => ({ ...m, [tab.path]: !m[tab.path] }))}>
                  {previewOn[tab.path] ? <><Code2 size={13} /> Code</> : <><Eye size={13} /> Preview</>}
                </button>
                {previewOn[tab.path] && (
                  <button className="px-2 text-xs text-muted hover:text-text border-l border-line" title="Reload the preview"
                    onClick={() => setPreviewNonce((n) => n + 1)}><RefreshCw size={13} /></button>
                )}
              </>
            )}
            {tab?.kind === "text" && (
              <button className="px-3 text-xs text-muted hover:text-text flex items-center gap-1"
                onClick={() => saveTab(tab.path)} title="Save (Ctrl+S)"><Save size={13} /> Save</button>
            )}
            {mine.length > 1 && (
              <button className="px-2.5 text-xs text-muted hover:text-danger flex items-center gap-1 border-l border-line"
                onClick={() => closeAllIn(pane.id)} title="Close every file in this pane"><X size={13} /> Close all</button>
            )}
            {paneChrome(pane, isMax, toggleMax)}
          </div>
        </div>
        <div className="flex-1 min-h-0 bg-bg flex flex-col">
          {tab?.diskChanged && (
            <div className="shrink-0 bg-warn/15 border-b border-warn/40 text-xs px-3 py-1 flex items-center gap-2">
              <span className="text-warn">Changed on disk (e.g. saved in VS Code).</span>
              <button className="underline hover:text-text" onClick={() => reloadFromDisk(tab.path)}>Reload from disk</button>
              <span className="text-muted/60">· or keep editing to overwrite on save</span>
            </div>
          )}
          <div className="flex-1 min-h-0">{paneBody(tab, pane.id)}</div>
        </div>
      </div>
    );
  }

  /** What one open file looks like. */
  function paneBody(tab: OpenTab | null, paneId: string) {
    if (!tab) {
      return (
        <div className="h-full flex flex-col items-center justify-center gap-2 text-center text-xs text-muted/50 px-4">
          <span>Nothing open here.<br />Drag a file tab in, or open a file from the explorer.</span>
          <button onClick={() => makeAgent(paneId)}
            className="mt-1 px-2 py-1 rounded border border-line text-muted hover:text-brand hover:border-brand/50 flex items-center gap-1.5">
            <Sparkles size={12} /> or run a second agent here
          </button>
        </div>
      );
    }
    /* preview wins over every kind — a too-large HTML has no editor to fall back to, and
       serving it over http never needed to read the file in the first place */
    if (tab.previewable && previewOn[tab.path]) return <HtmlPreview path={tab.path} nonce={previewNonce} />;
    // WHICH ENGINE'S PANEL THIS IS. These three read per-engine data — a prompt list, a phase list,
    // an edit history — so they are given the FEED id (agent-prefixed), not `root.id`. The prompt
    // panel used to take `root.id`: on a Codex or DeepSeek pane that reads the Claude conversation
    // in the same folder, so the button listed the wrong chat's prompts, or none at all. Checkpoints
    // are per-PROJECT (the same files, whichever engine wrote them) so they keep the bare id — but
    // they are told which engine is selected, to label the snapshot and to speak its name.
    const paneAgent = activeRoot ? agentOf(activeRoot.id, paneId) : "";
    if (tab.kind === "prompts") return activeRoot
      ? <PromptHistory feedId={feedOf(activeRoot, paneId)} agentName={nameOfAgent(paneAgent)} /> : null;
    if (tab.kind === "checkpoints") return activeRoot
      ? <CheckpointsPanel root={activeRoot} agent={paneAgent} agentName={nameOfAgent(paneAgent)}
          resolveAgentName={nameOfAgent} /> : null;
    if (tab.kind === "skills") return activeRoot
      ? <SkillsPanel feedId={feedOf(activeRoot, paneId)} agent={paneAgent} agentName={nameOfAgent(paneAgent)} /> : null;
    // `activeRoot.id`, NOT `feedId`: the composer is given `root.id`, and the feed id can carry
    // an agent prefix ("kimi--…"). The insert is matched on the composer's own key, so a
    // mismatch here would put the text nowhere and look like a button that does nothing.
    if (tab.kind === "inspect") return activeRoot
      ? <LivePick projectId={activeRoot.id} rootPath={activeRoot.path} rootName={activeRoot.name} /> : null;
    if (tab.kind === "todos") return <TodosPanel projectId={feedId} />;
    if (tab.kind === "model") {
      return (
        <div className="h-full p-3">
          {/* .obj/.stl/.ply aren't glTF — serve them through the converter endpoint */}
          <Suspense fallback={<PaneLoading label="loading the 3D view…" />}>
            <ModelViewer src={tab.convert ? api.wsModel(tab.path) : api.wsRaw(tab.path)} className="h-full w-full" />
          </Suspense>
        </div>
      );
    }
    if (tab.kind === "image") {
      return (
        <div className="h-full overflow-auto flex items-center justify-center p-4"
          style={{ backgroundImage: "repeating-conic-gradient(#1a1e2b 0% 25%, #13161f 0% 50%)", backgroundSize: "24px 24px" }}>
          <img src={api.wsRaw(tab.path)} className="max-w-full max-h-full object-contain" />
        </div>
      );
    }
    if (tab.kind === "text") {
      return (
        <Suspense fallback={<PaneLoading label="loading the editor…" />}>
          <CodeEditor value={tab.text}
            ext={tab.name.includes(".") ? tab.name.split(".").pop() : ""}
            onChange={(v) => setTabText(tab.path, v)}
            onSave={() => saveTab(tab.path)} />
        </Suspense>
      );
    }
    return (
      <div className="h-full flex items-center justify-center text-muted text-sm">
        {tab.kind === "toobig" ? "File too large to open here." : "Binary file — not shown."}
      </div>
    );
  }

  return (
    // data-skin here, not on <html>: the console palette and face cascade down this pane only.
    <div data-skin={chromeCli ? "cli" : undefined} className="h-full flex flex-col bg-bg text-text text-sm">
      {gitFor && (
        <GitPanel path={gitFor.path} name={gitFor.name}
          onClose={() => { setGitFor(null); loadGitMap(); }} />
      )}
      {wtFor && (
        <WorktreeDialog project={wtFor} onClose={() => setWtFor(null)}
          onCreated={async (path, name) => {
            setWtFor(null);
            try {
              const list = applyRootPrefs(await api.wsRoots());
              setRoots(list);
              loadGitMap();
              // Land in it. Making a worktree and being left in the old one is a step you would
              // then have to take by hand every single time.
              const made = list.find((x) => wsNorm(x.path) === wsNorm(path));
              if (made) openInPane(made);
              toast(`Worktree "${name}" created — it has its own session`, "ok");
            } catch (e: any) {
              toast(`Created at ${path}, but the list did not refresh: ${e.message}`, "warn");
            }
          }} />
      )}
      {ask && <InputModal {...ask} onClose={() => setAsk(null)} />}
      {/* Which project an agent pane holds. Rendered here rather than inside the pane so it can
          hang below a header that clips, and so only one is ever open. */}
      {secondMenu && (() => {
        const pane = layout.panes.find((p) => p.id === secondMenu.pane);
        const root = pane ? rootOf(pane) : null;
        if (!pane || !root) return null;
        const git = gitInfo[wsNorm(root.path)];
        const other = engineLabel(freeEngine("", root.id,
          agentOf(root.id, pane.id) === "codex" ? "claude" : "codex"));
        const row = "w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-start gap-2";
        return (
          <>
            <div className="fixed inset-0 z-[88]" onClick={() => setSecondMenu(null)}
              onContextMenu={(e) => { e.preventDefault(); setSecondMenu(null); }} />
            <div className="fixed z-[89] card p-1 w-72 shadow-card text-xs"
              style={{ left: secondMenu.left, top: secondMenu.top }}>
              <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted">Second agent</div>
              {git?.is_repo ? (
                <button className={row} onClick={() => { setSecondMenu(null); secondAgentWorktree(pane.id, root); }}>
                  <GitBranch size={13} className="text-brand shrink-0 mt-0.5" />
                  <span>
                    <span className="block font-medium">In a new worktree (recommended)</span>
                    <span className="block text-muted">Own folder, branch and session. The agents cannot
                      overwrite each other. Starts from the last commit.</span>
                  </span>
                </button>
              ) : (
                <div className="px-2 py-1.5 text-muted flex items-start gap-2">
                  <GitBranch size={13} className="shrink-0 mt-0.5 opacity-50" />
                  <span>This folder is not a git repository, so a worktree is not possible.</span>
                </div>
              )}
              <button className={row} onClick={() => { setSecondMenu(null); secondAgentHere(pane.id, root); }}>
                <Plus size={13} className="text-warn shrink-0 mt-0.5" />
                <span>
                  <span className="block font-medium">In this folder, with {other}</span>
                  <span className="block text-muted">Both agents edit the same files. Give them different files.</span>
                </span>
              </button>
            </div>
          </>
        );
      })()}
      {projMenu && (() => {
        const pane = layout.panes.find((p) => p.id === projMenu.pane);
        const cur = pane ? rootOf(pane) : null;
        return (
          <>
            <div className="fixed inset-0 z-[88]" onClick={() => setProjMenu(null)}
              onContextMenu={(e) => { e.preventDefault(); setProjMenu(null); }} />
            <div className="fixed z-[89] card p-1 w-64 max-h-[70vh] overflow-auto shadow-card text-xs"
              style={{ left: projMenu.left, top: projMenu.top }}>
              <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted">
                Project in this pane
              </div>
              {roots.map((r) => {
                const st = wsStatus(r.id);
                return (
                  <button key={r.path} title={r.path}
                    onClick={() => { retarget(projMenu.pane, r); setProjMenu(null); }}
                    className={cls("w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2",
                      cur?.id === r.id && "bg-panel2 ring-1 ring-brand/40")}>
                    <Folder size={12} className="text-warn shrink-0" />
                    <span className="truncate flex-1">{r.name}</span>
                    {st === "working"
                      ? <Loader2 size={11} className="animate-spin text-brand shrink-0" />
                      : st === "done" ? <Check size={11} className="text-ok shrink-0" /> : null}
                  </button>
                );
              })}
              <div className="h-px bg-line my-1" />
              <button onClick={() => { setProjMenu(null); openFolder(); }}
                className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2 text-brand">
                <FolderOpen size={12} /> Open a folder…
              </button>
              <button onClick={() => { setProjMenu(null); findProjects(); }} title="Open every folder on this PC that looks like a project"
                className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2 text-brand">
                <Search size={12} /> Find my projects…
              </button>
              {chatPanes.length > 1 && (
                <button onClick={() => { closeAgent(projMenu.pane); setProjMenu(null); }}
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-danger/10 flex items-center gap-2 text-danger">
                  <X size={12} /> Close this agent
                </button>
              )}
              <div className="px-2 pt-1 pb-0.5 text-[10px] text-muted/60">
                Each pane runs its own agent, in its own project.
              </div>
            </div>
          </>
        );
      })()}
      {menu && (() => {
        const e = menu.entry;
        const isRoot = !!menu.isRoot;
        const destDir = e.is_dir ? e.path : e.path.replace(/[\\/][^\\/]*$/, "");
        const items: { label: string; icon: any; on: () => void; danger?: boolean }[] = [
          { label: "Open in File Explorer", icon: ExternalLink, on: () => ctxReveal(e.path) },
          ...(e.is_dir ? [
            { label: "New File…", icon: FilePlus, on: () => newFileIn(e.path) },
            { label: "New Folder…", icon: FolderPlus, on: () => newFolderIn(e.path) },
          ] : []),
          ...(isRoot ? [] : [
            { label: "Copy", icon: Copy, on: () => ctxCopy(e) },
            { label: "Cut", icon: Scissors, on: () => ctxCut(e) },
          ]),
          ...(clip ? [{ label: `Paste "${clip.name}"`, icon: ClipboardPaste, on: () => ctxPaste(destDir) }] : []),
          { label: "Copy Path", icon: Copy, on: () => { navigator.clipboard?.writeText(e.path); toast("Path copied", "info"); } },
          // A folder INSIDE a project, opened as its own workspace: same files, same branch, but
          // an agent sent there reads that folder instead of the whole repo. That is the cheap
          // half of isolation — no checkout, no branch, just a smaller world to read.
          ...(e.is_dir && !isRoot ? [{
            label: "Open as its own workspace", icon: FolderOpen,
            on: async () => {
              try {
                const r = await api.wsAddRoot(e.path);
                setRoots((rs) => rs.some((x) => wsNorm(x.path) === wsNorm(r.path)) ? rs : [...rs, r]);
                loadGitMap();
                openInPane(r);
                toast(`"${r.name}" opened as a workspace — an agent here reads only this folder`, "ok");
              } catch (err: any) { toast(`Could not open it: ${err.message}`, "danger"); }
            },
          }] : []),
          ...(isRoot ? (() => {
            const root = gitRoots.find((r) => wsNorm(r.path) === wsNorm(e.path));
            return [
              ...(root?.is_repo ? [{
                label: "New worktree…", icon: GitBranch, on: () => root && setWtFor(root),
              }] : []),
              ...(root?.kind === "worktree" ? [{
                label: `Delete this worktree (${root.branch})`, icon: Trash2, danger: true,
                on: async () => {
                  const r = await api.wtRemove({ path: root.path }).catch((x) => ({ ok: false, error: String(x?.message || x) } as any));
                  if (r.ok) {
                    setRoots((rs) => rs.filter((x) => wsNorm(x.path) !== wsNorm(root.path)));
                    if (activeRoot && wsNorm(activeRoot.path) === wsNorm(root.path)) setActiveRoot(null);
                    loadGitMap();
                    toast(`Worktree removed · branch "${root.branch}" kept`, "ok");
                  } else if (r.dirty) {
                    // Never delete uncommitted work on a single click.
                    setAsk({
                      title: `"${root.name}" has uncommitted changes`,
                      placeholder: 'type DELETE to remove it anyway',
                      initial: "",
                      resolve: async (v: string | null) => {
                        if ((v || "").trim().toUpperCase() !== "DELETE") { toast("Left alone.", "info"); return; }
                        const f = await api.wtRemove({ path: root.path, force: true }).catch(() => ({ ok: false } as any));
                        if (f.ok) {
                          setRoots((rs) => rs.filter((x) => wsNorm(x.path) !== wsNorm(root.path)));
                          loadGitMap(); toast("Worktree removed.", "ok");
                        } else toast("Could not remove it.", "danger");
                      },
                    });
                  } else toast(r.error || "Could not remove it.", "danger");
                },
              }] : []),
              { label: "Open in new window", icon: AppWindow, on: () => detachWorkspace(e.path) },
              ...(root ? [{ label: pinned.includes(wsNorm(root.path)) ? "Unpin from quick list" : "Pin to quick list", icon: Pin, on: () => togglePin(root) }] : []),
              ...(root ? [{ label: "Rename project…", icon: Pencil, on: () => renameRoot(root) }] : []),
              ...(root ? [{ label: "Remove from workspace", icon: X, on: () => removeRoot(root), danger: true }] : []),
            ];
          })() : []),
          ...(isRoot ? [] : [
            { label: "Rename…", icon: Pencil, on: () => ctxRename(e) },
            { label: "Delete", icon: Trash2, on: () => ctxDelete(e), danger: true },
          ]),
        ];
        const left = Math.min(menu.x, window.innerWidth - 224);
        const top = Math.min(menu.y, window.innerHeight - (items.length * 34 + 16));
        return (
          <>
            <div className="fixed inset-0 z-[90]" onClick={() => setMenu(null)} onContextMenu={(ev) => { ev.preventDefault(); setMenu(null); }} />
            <div className="fixed z-[91] card p-1 w-52 shadow-card text-sm" style={{ left, top }}>
              {items.map((it, i) => {
                const Icon = it.icon;
                return (
                  <button key={i} className={cls("w-full text-left px-2 py-1.5 rounded flex items-center gap-2 hover:bg-panel2", it.danger && "text-danger hover:bg-danger/10")}
                    onClick={() => { setMenu(null); it.on(); }}>
                    <Icon size={14} /> {it.label}
                  </button>
                );
              })}
            </div>
          </>
        );
      })()}
      {/* "Who needs you" used to be a line here. It moved to the bottom bar, beside the CPU:
          a strip that is on screen with nothing to report stops being read, and the bar already
          lets the user choose which readings deserve a permanent place. */}
      <UsageLimitsBar cli={chromeCli} />
      <div className="flex-1 flex min-h-0">
        {/* activity bar */}
        <div className="w-11 shrink-0 bg-panel border-r border-line flex flex-col items-center py-2 gap-1 select-none relative">
          <button className={cls("p-2 rounded", showExplorer ? "text-brand bg-panel2" : "text-muted hover:text-text")} title="Toggle the file explorer" onClick={toggleExplorer}><FolderTree size={20} /></button>
          {/* Phases — the TodoWrite plan behind the "3/9" counter. The badge shows it without opening. */}
          <button className={cls("p-2 rounded relative", todoCount ? "text-brand hover:text-brand-600" : "text-muted hover:text-text")}
            title={todoCount ? `Build phases — ${todoCount.done}/${todoCount.total} done. Click to read them.` : `Build phases — the plan ${activeAgentName} is working through`}
            onClick={openTodos}>
            <ListChecks size={20} />
            {todoCount && (
              <span className="absolute -bottom-0.5 -right-0.5 px-1 rounded bg-brand-600 text-white text-[9px] leading-[13px] font-mono tabular-nums">
                {todoCount.done}/{todoCount.total}
              </span>
            )}
          </button>
          <button className="p-2 rounded text-muted hover:text-text"
            title={`Prompt history — every prompt you sent to ${activeAgentName} in this folder (click to jump the feed)`} onClick={openPrompts}><History size={20} /></button>
          <button className="p-2 rounded text-muted hover:text-text"
            title={`Checkpoints — review & undo what ${activeAgentName} changed (snapshots are keyed to the folder, so every engine shares one list)`} onClick={openCheckpoints}><RotateCcw size={20} /></button>
          {/* Inspect — click a thing in the running page and hand the agent what it is: the
              element, the CSS that applies, a crop of it. The live link's own switch decides
              whether the page can be opened at all; this only asks for a frame. */}
          {pickOn && (
            <button className="p-2 rounded text-muted hover:text-text" title="Inspect — click an element in the running page and send it to the agent" onClick={openInspect}><Crosshair size={20} /></button>
          )}
          <button className="p-2 rounded text-muted hover:text-text"
            title={activeAgent === "codex"
              ? "Skills — Codex has no skill mechanism; it takes standing instructions from AGENTS.md"
              : `Skills — enable/disable what ${activeAgentName} loads (game-dev, UI/UX…). Claude and DeepSeek share one library.`}
            onClick={openSkills}><Wand2 size={20} /></button>
          {/* Agents. The bottom bar has carried this count for a while, and the bottom bar is
              the one strip people hide. It belongs here, beside the phases badge, in the same
              shape: the ring turns while anything is running, and the number sits to its left. */}
          <button className={cls("p-2 rounded relative", agentCount ? "text-brand hover:text-brand-600" : "text-muted hover:text-text")}
            title={agentCount
              ? `${agentCount} subagent${agentCount > 1 ? "s" : ""} running across every workspace. Click to read them.`
              : "Subagents — what the Task tool delegated, what it cost, and what it touched"}
            onClick={openAgents}>
            {agentCount ? <Loader2 size={20} className="animate-spin" /> : <Bot size={20} />}
            {agentCount > 0 && (
              <span className="absolute -bottom-0.5 -right-0.5 px-1 rounded bg-brand-600 text-white text-[9px] leading-[13px] font-mono tabular-nums">
                {agentCount}
              </span>
            )}
          </button>
          {/* Workspaces. This used to be an OS folder picker and nothing else, so the only way to
              see a repository's worktrees was to open the dropdown inside the explorer — which is
              hidden whenever the explorer is. Now it opens the list itself, worktrees nested under
              the checkout they came from, and the folder picker is one of the actions at the foot
              of it rather than the whole button. */}
          <button className={cls("p-2 rounded", wsPanel ? "text-brand bg-panel2" : "text-muted hover:text-text",
                                 folderDrop === "rail" && "ring-2 ring-brand/70 bg-brand/10 text-brand")}
            {...folderTarget("rail")}
            title="Workspaces — every project, with its worktrees under it. Drop a folder here to open it."
            onClick={() => { setWsPanel((v) => !v); setWsQuery(""); }}><FolderOpen size={20} /></button>
          <button className="p-2 rounded text-muted hover:text-text" title="New project (created & opened here)" onClick={newProject}><FolderPlus size={20} /></button>
          <NewGameButton variant="rail" current={activeRoot} onCreated={selectRoot} />

          {agentsPanel && (
            <RunningAgents busy={agentBusy} onClose={() => setAgentsPanel(false)}
              nameOf={(pid) => roots.find((r) => r.id === pid)?.name || pid}
              onOpen={(pid, agentId) => {
                // Switch to the workspace that owns it first, or the pane would open an agent
                // whose transcript belongs to a project this window is not looking at.
                const r = roots.find((x) => x.id === pid);
                if (r && r.path !== activeRoot?.path) openInPane(r);
                // AND CARRY THE ENGINE. `pid` is the bare folder id; `busyAgent` is the engine the
                // live poll says is working there, which is who the agent belongs to. Without it a
                // Codex agent opened in a pane that read Claude's transcripts and drew nothing —
                // the count said 2, the list said 2, and the pane said "no record".
                openAgentPane(agentId, r?.id || pid || activeRoot?.id || "", busyAgent[pid] || "");
              }} />
          )}

          {wsPanel && (
            <>
              <div className="fixed inset-0 z-[70]" onClick={() => setWsPanel(false)} />
              {/* Drop a folder anywhere on this card and it becomes a workspace. */}
              <div className={cls("absolute left-full top-0 ml-1 z-[71] card p-0 w-72 max-h-[70vh] overflow-auto shadow-card",
                                  folderDrop === "panel" && "ring-2 ring-inset ring-brand/70 bg-brand/5")}
                {...folderTarget("panel")}>
                <div className="sticky top-0 bg-panel border-b border-line">
                  <div className="flex items-center gap-2 px-3 py-2">
                    <FolderOpen size={14} className="text-warn" />
                    <span className="font-medium text-text text-xs">Workspaces</span>
                    <span className="ml-auto text-[10px] font-mono text-muted/60 tabular-nums">
                      {wsQuery.trim() ? `${wsFiltered.length}/${wsRows.length}` : wsRows.length}
                    </span>
                  </div>
                  <div className="px-2 pb-2 relative">
                    <Search size={12} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted/50 pointer-events-none" />
                    <input autoFocus value={wsQuery} onChange={(e) => setWsQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") { e.stopPropagation(); wsQuery ? setWsQuery("") : setWsPanel(false); }
                        // Enter opens the only thing left, which is what a filter is for.
                        if (e.key === "Enter" && wsFiltered.length === 1) {
                          const only = wsFiltered[0].kids.length === 1 && !wsFiltered[0].r.name.toLowerCase().includes(wsQuery.trim().toLowerCase())
                            ? wsFiltered[0].kids[0] : wsFiltered[0].r;
                          openInPane(only); setWsPanel(false);
                        }
                      }}
                      placeholder="Filter by name, branch or path…"
                      className="input !py-1 !pl-6 !pr-6 text-xs w-full" />
                    {wsQuery && (
                      <button onClick={() => setWsQuery("")} title="Clear"
                        className="absolute right-4 top-1/2 -translate-y-1/2 text-muted/60 hover:text-text"><X size={12} /></button>
                    )}
                  </div>
                </div>
                <div className="p-1">
                  {folderDrop === "panel" && (
                    <div className="px-2 py-2 mb-1 rounded border border-dashed border-brand/60 text-brand text-xs text-center">
                      drop to open as a workspace
                    </div>
                  )}
                  {wsRows.length === 0 && (
                    <div className="px-2 py-3 text-muted/60 text-xs">no workspaces yet — drop a folder here, or open one below</div>
                  )}
                  {wsRows.length > 0 && wsFiltered.length === 0 && (
                    <div className="px-2 py-3 text-muted/60 text-xs">nothing matches “{wsQuery}”</div>
                  )}
                  <WorkspaceTree
                    rows={wsFiltered} activePath={activeRoot?.path} open={wsOpen} onToggle={togglePinOpen}
                    onSelect={(r) => { openInPane(r); setWsPanel(false); }}
                    onContext={(r, x, y) => { setWsPanel(false); openRootMenu(r, x, y); }}
                    statusOf={wsStatus} fanoutOf={(id) => fanout[id] || 0}
                    quietOf={(id) => quiet[id] || 0}
                    agentOf={(id) => busyAgent[id] || ""} colourOf={workingColour}
                    // Dragging a project into a pane worked from the old picker, so it has to work
                    // from here. Reordering does not: that is the pinned strip's job, and the
                    // panel is sorted by the workspace list's own order.
                    drag={{
                      path: null,
                      onStart: (r, e) => {
                        e.dataTransfer.setData(ROOT_DND, r.path);
                        e.dataTransfer.effectAllowed = "move";
                        setRootDrag(r);
                        setWsPanel(false);
                      },
                      onDropRow: () => {},
                      onEnd: () => { setRootDrag(null); setDropHint(""); },
                    }} />
                </div>
                {/* The folder picker this button used to BE, kept as an action rather than as the
                    whole of it. */}
                <div className="border-t border-line p-1 flex items-center gap-1 sticky bottom-0 bg-panel">
                  <button className="flex-1 flex items-center gap-1.5 px-2 py-1.5 rounded text-xs text-muted hover:bg-panel2 hover:text-text"
                    onClick={() => { setWsPanel(false); openFolder(); }}>
                    <FolderOpen size={13} className="text-warn shrink-0" /> Open a folder…
                  </button>
                  <button className="flex-1 flex items-center gap-1.5 px-2 py-1.5 rounded text-xs text-muted hover:bg-panel2 hover:text-text"
                    onClick={() => { setWsPanel(false); newProject(); }}>
                    <FolderPlus size={13} className="text-warn shrink-0" /> New project
                  </button>
                  <NewGameButton variant="row" current={activeRoot} onCreated={selectRoot} onOpen={() => setWsPanel(false)} />
                </div>
              </div>
            </>
          )}
        </div>

        {/* explorer */}
        {showExplorer && (
        /* THE WHOLE COLUMN TAKES A FOLDER. The pinned strip, the header, the workspace button
           and the gaps between them all mean the same thing - open it - so one target covers
           them instead of four that each have to be found. The file tree inside has its own,
           narrower meaning (import into the open project) and stops the event there. */
        <div className={cls("w-60 shrink-0 bg-panel/60 border-r border-line flex flex-col min-h-0 relative",
                            folderDrop === "explorer" && "ring-2 ring-inset ring-brand/60 bg-brand/5")}
          {...folderTarget("explorer")}>
          {folderDrop === "explorer" && (
            <div className="pointer-events-none absolute inset-x-0 bottom-1 z-20 text-center text-[10px] text-brand font-medium">
              drop to open as a workspace
            </div>
          )}
          <div className="px-2 py-1.5 flex items-center justify-between">
            <span className="text-[11px] font-semibold tracking-wide text-muted">EXPLORER</span>
            <div className="flex items-center gap-0.5">
              <button className="p-1 rounded hover:bg-panel2 text-muted hover:text-text" title="New file" onClick={newFile}><FilePlus size={14} /></button>
              <button className="p-1 rounded hover:bg-panel2 text-muted hover:text-text" title="New folder" onClick={newFolder}><FolderPlus size={14} /></button>
              <button className="p-1 rounded hover:bg-panel2 text-muted hover:text-text" title="Refresh" onClick={() => setReloadKey((k) => k + 1)}><RefreshCw size={14} /></button>
              <button className="p-1 rounded hover:bg-panel2 text-muted hover:text-text disabled:opacity-40" title="Open this workspace in a new window"
                onClick={() => activeRoot && detachWorkspace(activeRoot.path)} disabled={!activeRoot}><AppWindow size={14} /></button>
            </div>
          </div>
          {/* One line, not a second tree: which workspace this explorer is showing, and the way
              into every other one. The list itself lives in the Workspaces panel on the activity
              bar — drawing it here as well put the same tree twice on the same screen, one above
              the other, and pushed the file explorer down the column. Right-click still opens the
              workspace menu, exactly as it did on the picker this replaces. */}
          <div className="px-2 mb-1">
            <button className="input !py-1 text-xs flex items-center gap-1.5 w-full text-left"
              onClick={() => { setWsPanel(true); setWsQuery(""); }}
              onContextMenu={(e) => { e.preventDefault(); if (activeRoot) openRootMenu(activeRoot, e.clientX, e.clientY); }}
              title={activeRoot ? `${activeRoot.path}
click to switch workspace` : "choose a workspace"}>
              <Folder size={13} className="text-warn shrink-0" />
              <span className="truncate flex-1 min-w-0">{activeRoot?.name || "pick a project"}</span>
              {activeRoot?.branch && (
                <span className="shrink-0 text-[10px] font-mono text-muted/60 truncate max-w-[6rem]">{activeRoot.branch}</span>
              )}
              <ChevronRight size={13} className="text-muted shrink-0" />
            </button>
          </div>
          {/* Pinned workspaces — one click to switch, no dropdown (★ a workspace in the
              picker to add it). Drag a row above/below another to reorder (ws-order). The tree
              itself is WorkspaceTree, shared with the Workspaces panel on the activity bar. */}
          {pinnedRows.length > 0 && (
            <div className="px-1 mb-1 shrink-0">
              <WorkspaceTree
                rows={pinnedRows} activePath={activeRoot?.path} open={pinOpen} onToggle={togglePinOpen}
                onSelect={openInPane} onContext={(r, x, y) => openRootMenu(r, x, y)}
                statusOf={wsStatus} fanoutOf={(id) => fanout[id] || 0}
                quietOf={(id) => quiet[id] || 0}
                agentOf={(id) => busyAgent[id] || ""} colourOf={workingColour}
                drag={{
                  path: pinDrag,
                  onStart: (r, e) => {
                    setPinDrag(r.path);
                    // Two meanings for one gesture: dropped on another row it reorders the list,
                    // dropped on a pane it opens the project there. The row drop reads component
                    // state, the pane drop reads this payload, so neither sees the other's.
                    e.dataTransfer.setData(ROOT_DND, r.path);
                    e.dataTransfer.effectAllowed = "move";
                    setRootDrag(r);
                  },
                  onDropRow: (from, to) => { reorderRoots(from, to); setPinDrag(null); },
                  onEnd: () => { setPinDrag(null); setRootDrag(null); setDropHint(""); },
                }} />
            </div>
          )}
          <button onClick={toggleTree} title="Show / hide the file tree"
            className="px-2 py-1 flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted/70 hover:text-text shrink-0">
            {showTree ? <ChevronDown size={11} /> : <ChevronRight size={11} />} Files
          </button>
          {showTree && (
          <div className={cls("flex-1 min-h-0 overflow-auto px-1 pb-2 relative", wsDrag && "ring-2 ring-inset ring-brand/60 bg-brand/5")}
            onContextMenu={(e) => { if (e.target === e.currentTarget && activeRoot) { e.preventDefault(); openRootMenu(activeRoot, e.clientX, e.clientY); } }}
            onDragOver={(e) => {
              if (e.dataTransfer.types.includes(ROOT_DND)) return;   // that one is for a pane
              e.preventDefault();
              // STOPPED HERE. The column behind would otherwise light up as well and, on the
              // drop, open the folder as a workspace at the same time as this imported it -
              // one gesture doing both of the two things it is allowed to mean.
              e.stopPropagation();
              e.dataTransfer.dropEffect = activeRoot ? "copy" : "link";
              if (!wsDrag) setWsDrag(true);
            }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setWsDrag(false); }}
            /* WITH A PROJECT OPEN a dropped folder is imported into it. WITH NONE there is
               nothing to import into, and this used to be wired to `undefined` - so the one
               moment a person most wants to open a folder was the one moment the drop did
               nothing at all. Same gesture, and the only outcome that makes sense here. */
            onDrop={(e) => {
              e.preventDefault(); e.stopPropagation(); setWsDrag(false);
              if (activeRoot) importDrop(activeRoot.path, e.dataTransfer);
              else openDroppedFolders(e.dataTransfer);
            }}>
            {wsDrag && (
              <div className="pointer-events-none absolute inset-x-0 bottom-1 text-center text-[10px] text-brand font-medium z-10">
                {activeRoot ? `drop to import into ${activeRoot.name}` : "drop a folder to open it as a workspace"}
              </div>
            )}
            {rootEntries.map((e) => <TreeNode key={e.path} entry={e} depth={0} onOpen={openFile} activePath={activePath}
              onContext={(entry, x, y) => setMenu({ entry, x, y })} onDropImport={importDrop} nonce={reloadKey} />)}
            {rootEntries.length === 0 && <div className="text-xs text-muted px-2 py-3">empty</div>}
          </div>
          )}
        </div>
        )}

        {/* The main area. Every pane is a cell on one grid, so nothing can fall out of line —
            the seams ARE the grid tracks, not bars laid over them. The chat is a pane like any
            other: drag it across, blow it up to the whole window, put it back, and it still has
            its scroll, its running turn and whatever you had half typed. */}
        <PaneGrid layout={layout} onLayout={setLayout} maxed={maxPane} onMaxed={setMaxPane}
          render={(pane, isMax, toggleMax) => pane.kind === "chat"
            ? chatPane(pane, isMax, toggleMax)
            : pane.kind === "agent"
            ? agentPane(pane, isMax, toggleMax)
            : filePane(pane, isMax, toggleMax)} />
      </div>
    </div>
  );
}

// The standing build plan. The feed renders each phase as it happens and then it scrolls away, so
// a long job's "3/9" had nothing behind it. This is the copy you can come back to.
//
// The backend reads three sources, because Claude Code moved this feature twice and then withdrew
// it from most models: the Task tools' on-disk store, TaskCreate/TaskUpdate (or the older
// TodoWrite) in the transcript, and the file the session writes when it is given no phase tool at
// all. Polls only while the tab is visible.
function TodosPanel({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.missionTodos>> | null>(null);
  // Earlier sets are fetched only when asked for — most of the time nobody looks, and the panel
  // polls every 5s.
  const [showPast, setShowPast] = useState(false);
  const [past, setPast] = useState<Awaited<ReturnType<typeof api.missionPhaseHistory>>["runs"] | null>(null);
  useEffect(() => { setShowPast(false); setPast(null); }, [projectId]);
  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    const stop = pollWhileVisible(() => api.missionTodos(projectId)
      .then((r) => { if (alive) setData(r); }).catch(() => {}), 5000);
    return () => { alive = false; stop(); };
  }, [projectId]);

  if (!data) return <div className="p-4 text-xs text-muted">Reading the session…</div>;
  if (!data.todos.length) return (
    <div className="p-6 text-sm text-muted max-w-lg">
      <div className="font-medium text-text mb-1">No build phases yet</div>
      Claude writes a phase list when a job needs more than a few steps. It appears here the moment
      it does, and each phase ticks off as it finishes. Short jobs get none — that is normal.
      <div className="mt-2 text-xs text-muted/70">
        Ask for one at any time: “list the phases first, then build”.
      </div>
    </div>
  );
  const pct = data.total ? Math.round((100 * data.done) / data.total) : 0;
  const secs = data.ts ? Date.parse(data.ts) / 1000 : NaN;
  const ic = (s: string) => (s === "completed" ? "✓" : s === "in_progress" ? "◐" : "○");
  const col = (s: string) => (s === "completed" ? "text-ok" : s === "in_progress" ? "text-brand" : "text-muted");
  return (
    <div className="h-full overflow-auto p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm font-semibold">Build phases</span>
        <span className={cls("font-mono text-xs tabular-nums", data.finished ? "text-ok" : "text-muted")}>
          {data.done}/{data.total}
        </span>
        <div className="h-1.5 w-40 rounded bg-panel2 overflow-hidden shrink-0">
          <div className={cls("h-full transition-all", data.finished ? "bg-ok" : "bg-brand-600")}
            style={{ width: `${pct}%` }} />
        </div>
        {/* A set that is entirely ticked is a RECORD, not a plan — say so, or it sits there
            reading as work still in hand until the next set happens to replace it. */}
        {data.finished && (
          <span className="text-[11px] text-ok font-medium">all done — this set is finished</span>
        )}
        {Number.isFinite(secs) && <span className="text-[10px] text-muted/70 ml-auto">updated {timeAgo(secs)}</span>}
      </div>
      <ol className="space-y-1.5">
        {data.todos.map((t, i) => (
          <li key={i} className={cls("flex gap-2 text-sm rounded px-2 py-1.5",
            t.status === "in_progress" ? "bg-brand/10 border border-brand/30" : "border border-transparent")}>
            <span className={cls("select-none shrink-0 font-mono", col(t.status))}>{ic(t.status)}</span>
            <span className="text-[10px] text-muted/60 font-mono shrink-0 pt-0.5 tabular-nums">{i + 1}</span>
            <span className="min-w-0">
              <span className={cls(col(t.status), t.status === "completed" && "line-through opacity-70")}>
                {t.status === "in_progress" && t.active_form ? t.active_form : t.content}
              </span>
              {!!t.detail && (
                <span className="block text-[11px] text-muted/70 mt-0.5 leading-snug">{t.detail}</span>
              )}
            </span>
          </li>
        ))}
      </ol>
      {/* Only when there is nothing better to offer. Once the earlier SETS are listed below, a
          raw count of hidden phases says less than the groups do and reads like a warning. */}
      {!!data.earlier && (data.runs || 0) <= 1 && (
        <div className="mt-3 text-[10px] text-muted/60">
          {data.earlier} earlier {data.earlier === 1 ? "phase" : "phases"} from this session are not
          shown — this is the current build.
        </div>
      )}
      {(data.runs || 0) > 1 && (
        <div className="mt-4 pt-3 border-t border-line">
          <button className="text-xs text-muted hover:text-text flex items-center gap-1.5"
            onClick={() => {
              const next = !showPast;
              setShowPast(next);
              if (next && !past) api.missionPhaseHistory(projectId).then((h) => setPast(h.runs || [])).catch(() => setPast([]));
            }}>
            {showPast ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {(data.runs || 1) - 1} earlier {(data.runs || 1) - 1 === 1 ? "set" : "sets"} of phases
          </button>
          {showPast && (
            <div className="mt-2 space-y-3">
              {past === null && <div className="text-xs text-muted">reading…</div>}
              {/* index 0 is the set on screen above — the earlier ones are what this is for */}
              {(past || []).slice(1).map((run, ri) => (
                <div key={ri} className="rounded border border-line/70 p-2">
                  <div className="flex items-center gap-2 text-[11px] mb-1.5">
                    <span className={cls("font-mono tabular-nums", run.done === run.total ? "text-ok" : "text-muted")}>
                      {run.done}/{run.total}
                    </span>
                    <span className="text-muted/70">
                      {run.ended ? timeAgo(run.ended) : ""}
                    </span>
                  </div>
                  <ol className="space-y-0.5">
                    {run.phases.map((t, i) => (
                      <li key={i} className="flex gap-2 text-xs">
                        <span className={cls("select-none shrink-0 font-mono", col(t.status))}>{ic(t.status)}</span>
                        <span className={cls("min-w-0 text-muted", t.status === "completed" && "line-through opacity-60")}>
                          {t.content}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
              {past && past.length <= 1 && <div className="text-xs text-muted">nothing earlier yet.</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Rendered HTML beside the source. Served over http (never file://) so import maps, ES modules and
// relative assets resolve exactly as they will when the page ships.
function HtmlPreview({ path, nonce }: { path: string; nonce: number }) {
  const [url, setUrl] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    setErr("");
    api.wsPreviewUrl(path)
      .then((r) => { if (alive) setUrl(`${r.url}${r.url.includes("?") ? "&" : "?"}_=${nonce}`); })
      .catch((e: any) => { if (alive) setErr(e?.message || "could not serve this folder"); });
    return () => { alive = false; };
  }, [path, nonce]);
  if (err) return <div className="p-4 text-sm text-danger">Preview failed: {err}</div>;
  if (!url) return <div className="p-4 text-xs text-muted">Starting the preview server…</div>;
  return <iframe src={url} title="preview" className="w-full h-full border-0 bg-white" />;
}

function TreeNode({ entry, depth, onOpen, activePath, onContext, onDropImport, nonce }: {
  entry: TreeEntry; depth: number; onOpen: (e: TreeEntry) => void; activePath: string | null;
  onContext: (entry: TreeEntry, x: number, y: number) => void;
  onDropImport: (destDir: string, dt: DataTransfer) => void;
  nonce: number;
}) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<TreeEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  async function loadChildren() {
    setLoading(true);
    try { setChildren((await api.wsTree(entry.path)).entries); } catch { setChildren([]); } finally { setLoading(false); }
  }
  async function toggle() {
    if (!entry.is_dir) { onOpen(entry); return; }
    const next = !open; setOpen(next);
    if (next && children === null) await loadChildren();
  }
  // re-fetch an open folder's children when the tree changes (rename/delete/paste/drop)
  useEffect(() => {
    if (open && entry.is_dir && children !== null) loadChildren();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);
  return (
    <div>
      <div className={cls("flex items-center gap-1 py-[2px] rounded cursor-pointer hover:bg-panel2",
          activePath === entry.path && "bg-panel2", entry.skip && "opacity-60",
          dragOver && "ring-1 ring-brand bg-brand/10")}
        style={{ paddingLeft: depth * 12 + 4 }} onClick={toggle} title={entry.name}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onContext(entry, e.clientX, e.clientY); }}
        onDragOver={entry.is_dir ? (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true); } : undefined}
        onDragLeave={entry.is_dir ? () => setDragOver(false) : undefined}
        onDrop={entry.is_dir ? (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(false); onDropImport(entry.path, e.dataTransfer); } : undefined}>
        {entry.is_dir ? (open ? <ChevronDown size={13} className="shrink-0 text-muted" /> : <ChevronRight size={13} className="shrink-0 text-muted" />) : <span className="w-[13px] shrink-0" />}
        {entry.is_dir ? (open ? <FolderOpen size={14} className="shrink-0 text-warn" /> : <Folder size={14} className="shrink-0 text-warn" />) : <FileIcon size={14} className="shrink-0 text-muted" />}
        <span className="truncate text-[13px]">{entry.name}</span>
      </div>
      {open && entry.is_dir && (
        <div>
          {loading && <div className="text-[11px] text-muted" style={{ paddingLeft: (depth + 1) * 12 + 18 }}>…</div>}
          {children?.map((c) => <TreeNode key={c.path} entry={c} depth={depth + 1} onOpen={onOpen} activePath={activePath}
            onContext={onContext} onDropImport={onDropImport} nonce={nonce} />)}
        </div>
      )}
    </div>
  );
}

// Keep only genuine user prompts — drop tool results, slash-command wrappers, and
// system/harness injections (task-notifications, reminders, caveats) that also land
// in the transcript as "user" rows.
function isRealPrompt(text: string): boolean {
  const s = (text || "").trim();
  if (!s) return false;
  if (/^<(task-notification|system-reminder|local-command|command-name|command-message|command-args)/i.test(s)) return false;
  if (s.includes("<task-notification>") || s.includes("tool-use-id") || s.includes("</command-")) return false;
  if (s.startsWith("Caveat:") || /^\[SYSTEM/i.test(s)) return false;
  return true;
}

// Table-of-contents of every prompt you sent in this conversation (summarized).
// Click one to scroll the feed (middle pane) to that exchange + its result.
//
// `feedId` and not `root.id`: the id carries the engine ("codex--<folder>", "deepseek-harness--<…>"),
// and the feed this list jumps INTO is keyed the same way (SessionFeed matches `feedJump.id === id`).
// Handed the bare folder id, a non-Claude pane listed the Claude conversation in the same folder.
interface PromptItem { key: string; text: string; ts: string; n: number; answer: string; btw?: boolean }

function PromptHistory({ feedId, agentName }: { feedId: string; agentName: string }) {
  const [prompts, setPrompts] = useState<PromptItem[]>([]);
  // a /btw already sent but not yet picked up by the hook — listed at once, so a side-note is
  // never invisible between the moment you send it and the moment Claude reaches a tool boundary
  const [btwPending, setBtwPending] = useState("");
  const [showAnswers, setShowAnswers] = useState(() => localStorage.getItem("ph-answers") === "1");
  const jumpToPrompt = useStore((s) => s.jumpToPrompt);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!feedId) { setPrompts([]); return; }
    let alive = true;
    const load = () => api.missionFeed(feedId, 2000, "", "user,text").then((r) => {
      if (!alive) return;
      setBtwPending((r.btw_pending || "").trim());
      const lines = r.lines || [];
      const items: PromptItem[] = [];
      let n = 0;
      for (let i = 0; i < lines.length; i++) {
        const e = lines[i];
        if (e.kind !== "user" || !isRealPrompt(e.text || "")) continue;
        if (e.btw) { items.push({ key: `btw-${i}`, text: e.text || "", ts: e.ts, n, answer: "", btw: true }); continue; }
        n++;
        let answer = "";
        for (let j = i + 1; j < lines.length; j++) {        // my reply = the LAST assistant text
          const a = lines[j];                               // before the next prompt (the final summary)
          if (a.kind === "user" && isRealPrompt(a.text || "")) break;
          if (a.kind === "text" && (a.text || "").trim()) answer = a.text || "";
        }
        items.push({ key: `p-${i}`, text: e.text || "", ts: e.ts, n, answer });
      }
      setPrompts(items);
    }).catch(() => {});
    load();
    const iv = window.setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => { alive = false; window.clearInterval(iv); };
  }, [feedId]);

  function toggleAnswers() {
    setShowAnswers((v) => { const n = !v; localStorage.setItem("ph-answers", n ? "1" : "0"); return n; });
  }
  const summarize = (t: string, max = 170) => {
    const s = t.replace(/\s+/g, " ").trim();
    return s.length > max ? s.slice(0, max) + "…" : s;
  };
  const latest = prompts[prompts.length - 1];

  return (
    <div className="h-full flex flex-col bg-bg">
      <div className="shrink-0 flex items-center gap-2 px-2 py-1.5 border-b border-line text-xs bg-panel">
        <History size={13} className="text-brand" />
        <span className="font-semibold">Your prompts</span>
        <span className="text-muted">· {prompts.length}</span>
        {/* WHOSE PROMPTS. The panel is per-engine now, so it says which engine — otherwise the same
            panel on a Codex pane and a DeepSeek pane is indistinguishable at a glance. */}
        <span className="chip !py-0 text-[9px] text-muted" title={`This list is ${agentName}'s conversation in this folder`}>{agentName}</span>
        <button className={cls("ml-auto chip hover:text-text", showAnswers && "border-brand text-brand")}
          onClick={toggleAnswers} title="Show a summary of my answer under each prompt">
          {showAnswers ? "Answers: on" : "Show answers"}
        </button>
        {latest && (
          <button className="chip hover:border-brand hover:text-brand"
            onClick={() => jumpToPrompt(feedId, "u:" + latest.text.slice(0, 120))} title="Jump to your latest prompt">
            ↓ latest
          </button>
        )}
      </div>
      <div ref={listRef} className="flex-1 min-h-0 overflow-auto p-2 space-y-1">
        {btwPending && (
          <div className="rounded-md border border-warn/50 bg-warn/10 px-2 py-1.5">
            <div className="flex items-center gap-2 text-[10px] text-warn">
              <span>↪ side-note</span>
              <span className="ml-auto">waiting — goes in at the next tool step</span>
            </div>
            <div className="text-text/85 text-[12px] mt-0.5 leading-snug whitespace-pre-wrap">{btwPending}</div>
          </div>
        )}
        {prompts.length === 0 && !btwPending && (
          <div className="text-muted/60 text-xs px-1 py-3 leading-relaxed">
            No prompts in this conversation yet.
            <br />
            This lists <span className="text-text">{agentName}</span>'s conversation in this folder — switch the
            chat's engine (the model button beside the prompt) and this follows it.
          </div>
        )}
        {prompts.map((p) => p.btw ? (
          // a side-note: not a prompt of its own, so it carries no number and no answer
          <div key={p.key} className="rounded-md border border-accent/40 bg-accent/5 px-2 py-1.5">
            <div className="flex items-center gap-2 text-[10px] text-accent/90">
              <span>↪ side-note</span><span className="font-mono text-muted">{p.ts}</span>
              <span className="ml-auto text-muted/70">delivered</span>
            </div>
            <div className="text-text/85 text-[12px] mt-0.5 leading-snug">{summarize(p.text, 200)}</div>
          </div>
        ) : (
          <div key={p.key} className="group rounded-md border border-line bg-panel/40 hover:border-warn/50 transition-colors overflow-hidden">
            {/* your prompt — click to jump to it (gold) */}
            <button onClick={() => jumpToPrompt(feedId, "u:" + p.text.slice(0, 120))}
              className="w-full text-left px-2 py-1.5 hover:bg-panel2">
              <div className="flex items-center gap-2 text-[10px] text-muted">
                <span className="font-mono text-warn">#{p.n}</span>
                <span className="font-mono">{p.ts}</span>
                <span className="ml-auto opacity-0 group-hover:opacity-100 text-warn">→ your prompt</span>
              </div>
              <div className="text-text/90 text-[13px] mt-0.5 leading-snug">{summarize(p.text)}</div>
            </button>
            {/* my answer — click to jump to it (blue line) */}
            {showAnswers && (p.answer ? (
              <button onClick={() => jumpToPrompt(feedId, "a:" + p.answer.slice(0, 120))}
                className="w-full text-left px-2 py-1.5 border-t border-line border-l-2 border-l-brand/60 hover:bg-panel2">
                <div className="flex items-center gap-2 text-[10px] text-muted">
                  <span className="text-brand font-medium">↳ my answer</span>
                  <span className="ml-auto opacity-0 group-hover:opacity-100 text-brand">→ jump</span>
                </div>
                <div className="text-muted text-[12px] mt-0.5 leading-snug">{summarize(p.answer, 200)}</div>
              </button>
            ) : (
              <div className="px-2 py-1.5 border-t border-line border-l-2 border-l-brand/30 text-[12px] text-muted/60 italic">↳ no text reply captured</div>
            ))}
          </div>
        ))}
      </div>
      <div className="shrink-0 px-2 py-1 border-t border-line text-[10px] text-muted/70">
        Click a prompt to scroll the Claude feed to that point.
      </div>
    </div>
  );
}
// RootPicker used to live here: a dropdown at the top of the explorer listing every workspace
// with its worktrees nested underneath. The Workspaces panel on the activity bar now draws that
// same tree — from the same WorkspaceTree component — with a filter on top of it, so keeping
// both meant two lists that could disagree and a second copy of the whole tree sitting above the
// file explorer. Everything it could do is still reachable: the tree and the footer actions are
// in the panel, and pin / detach / rename / remove / new-worktree are on the right-click menu.


function InputModal({ title, placeholder, initial, resolve, onClose }: {
  title: string; placeholder?: string; initial: string; resolve: (v: string | null) => void; onClose: () => void;
}) {
  const [v, setV] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);
  function done(val: string | null) { resolve(val); onClose(); }
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50" onMouseDown={() => done(null)}>
      <div className="card p-4 w-[440px] max-w-[92vw]" onMouseDown={(e) => e.stopPropagation()}>
        <div className="font-semibold mb-2">{title}</div>
        <input ref={ref} className="input" placeholder={placeholder} value={v}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); done(v.trim() || null); }
            else if (e.key === "Escape") { e.preventDefault(); done(null); }
          }} />
        <div className="flex justify-end gap-2 mt-3">
          <button className="btn" onMouseDown={(e) => { e.preventDefault(); done(null); }}>Cancel</button>
          <button className="btn-primary" onMouseDown={(e) => { e.preventDefault(); done(v.trim() || null); }}>OK</button>
        </div>
      </div>
    </div>
  );
}

// Switch conversations: start a fresh one (Clear) or reopen a previous one. Past
// conversations stay saved on disk — selecting one shows it and continues it.
//
// The list used to load only once you opened the menu, and the chip read "Latest", so a project
// holding two dozen saved conversations looked exactly like one holding none. That cost a real
// session: the back mountains of PowderPeaks were upgraded in one conversation, the chat was
// cleared to start on the front slope, and the earlier conversation sat on disk unread the whole
// time — reopening it was one click away and nothing on screen said so. The chip now carries the
// name of the conversation you are in and how many there are, so the choice is visible before
// you need it instead of after.
function ConversationControl({ root, feedId, mode, setMode }: {
  root: WorkspaceRoot; feedId: string; mode: SessMode; setMode: (m: SessMode) => void;
}) {
  // Window coordinates, not the header's: the pane header clips so that it can never paint over
  // the pane beside it, and an absolutely-positioned menu inside a clipped box is a menu cut off
  // at the height of the bar it hangs from.
  const [open, setOpen] = useState<{ left: number; top: number } | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  // on mount as well as on open — the LABEL needs the list, not only the menu. ~35ms for 24
  // transcripts, so there is nothing to save by waiting for a click.
  useEffect(() => {
    if (!root.id) return;
    let gone = false;
    setSessions([]);
    api.missionSessions(feedId).then((r) => { if (!gone) setSessions(r.sessions); }).catch(() => {});
    return () => { gone = true; };
  }, [open, feedId]);
  const isCurrent = (s: Session) => mode.type === "session" ? s.id === mode.id : s.active;
  const current = sessions.find(isCurrent);
  const label = mode.type === "new" ? "New conversation"
    : current?.title || (mode.type === "session" ? "Past conversation" : root.name || "Latest");
  return (
    <div className="relative min-w-0">
      {/* Narrow on purpose. A conversation title runs to a full sentence, and at 240px it ate
          the room the model badge, the branch and the context figure needed — in a pane that
          is now half a window wide. The full title is in the menu, one click away. */}
      <button className="chip hover:text-text inline-flex items-center gap-1 max-w-[7.5rem]"
        onClick={(ev) => {
          const r = ev.currentTarget.getBoundingClientRect();
          setOpen((v) => v ? null : { left: Math.max(8, Math.min(r.left, window.innerWidth - 328)), top: r.bottom + 4 });
        }}
        title={sessions.length
          ? `${sessions.length} saved conversation${sessions.length === 1 ? "" : "s"} in this project — reopen one, or clear to start fresh`
          : "Conversations — clear to start fresh, or reopen a previous one"}>
        <MessageSquare size={11} className="shrink-0" /> <span className="truncate">{label}</span>
        {sessions.length > 1 && <span className="shrink-0 text-muted/70 tabular-nums">{sessions.length}</span>}
        <ChevronDown size={10} className="shrink-0" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(null)} />
          <div className="fixed z-50 card p-1 w-80 max-h-80 overflow-auto shadow-card text-xs"
            style={{ left: open.left, top: open.top }}>
            <button className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2 text-brand font-medium"
              onClick={() => { setMode({ type: "new" }); setOpen(null); }}>
              <Plus size={13} /> New conversation <span className="text-muted/60 font-normal">(clear &amp; start fresh)</span>
            </button>
            <button className="w-full text-left px-2 py-1.5 rounded hover:bg-panel2 flex items-center gap-2"
              onClick={() => { setMode({ type: "live" }); setOpen(null); }}>
              <Sparkles size={12} className="text-brand" /> Latest (live)
            </button>
            <div className="px-2 py-1 mt-0.5 text-[10px] text-muted uppercase tracking-wide border-t border-line">Saved conversations</div>
            {sessions.map((s) => (
              <button key={s.id} className={cls("w-full text-left px-2 py-1.5 rounded hover:bg-panel2", mode.type !== "new" && isCurrent(s) && "bg-panel2 ring-1 ring-brand/40")}
                onClick={() => { setMode({ type: "session", id: s.id }); setOpen(null); }}>
                <div className="truncate text-text/90">{s.title}</div>
                <div className="text-[10px] text-muted">{timeAgo(s.ts)}{s.active ? " · current" : ""}</div>
              </button>
            ))}
            {sessions.length === 0 && <div className="px-2 py-2 text-muted/60">no saved conversations yet</div>}
          </div>
        </>
      )}
    </div>
  );
}

// Enable/disable the skills the selected engine will load (game-dev, UI/UX, …).
//
// NOT "Claude Code skills" any more. The DeepSeek Harness runtime discovers `SKILL.md` from its own
// roots — project `.dsh/skills` and `.agents/skills`, plus a user root, plus whatever
// `customSkillDirs` adds — and the Studio mounts its own `~/.claude/skills` library into that
// runtime as a custom root (see `deepseek_session._skill_dirs`). Its on/off switch is the same
// `disable-model-invocation` frontmatter key, so this one panel drives both engines: same files,
// same switch. Codex has no per-task skill mechanism and the panel says so instead of showing
// another engine's list.
// The `feedId` is the ENGINE-PREFIXED id, not the folder: each engine keeps its skills in its own
// home (Kimi and Qwen run the same CLI with their own CLAUDE_CONFIG_DIR), and the backend resolves
// "whose library" from that prefix. Handed the bare folder id, a Kimi or Qwen pane listed Claude's.
function SkillsPanel({ feedId, agent, agentName }: { feedId: string; agent: string; agentName: string }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useStore((s) => s.toast);
  const load = () => api.skillsList(feedId, agent).then((r) => setSkills(r.skills)).catch(() => {}).finally(() => setLoading(false));
  useEffect(() => { setLoading(true); load(); /* eslint-disable-next-line */ }, [feedId, agent]);

  async function toggle(sk: Skill) {
    setBusy(sk.id);
    try {
      const r = await api.skillToggle(sk.id, !sk.enabled, feedId, agent);
      setSkills((ss) => ss.map((x) => (x.id === sk.id ? { ...x, enabled: r.enabled, state: r.state } : x)));
      toast(`${sk.name} ${r.enabled ? "enabled" : "disabled"}`, "ok");
    } catch (e: any) { toast(e.message, "danger"); } finally { setBusy(null); }
  }
  const summarize = (t: string) => { const s = (t || "").replace(/\s+/g, " ").trim(); return s.length > 200 ? s.slice(0, 200) + "…" : s; };
  const codex = agent === "codex";

  return (
    <div className="h-full flex flex-col bg-bg">
      <div className="shrink-0 flex items-center gap-2 px-2 py-1.5 border-b border-line text-xs bg-panel">
        <Wand2 size={13} className="text-brand" />
        <span className="font-semibold">Skills</span>
        <span className="text-muted">· {skills.length}</span>
        <span className="chip !py-0 text-[9px] text-muted" title={`The library ${agentName} loads from`}>{agentName}</span>
        <button className="ml-auto chip hover:text-text" onClick={() => { setLoading(true); load(); }} title="Refresh"><RefreshCw size={11} /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-2 space-y-1.5">
        {loading ? (
          <div className="text-muted text-xs px-1 py-3">loading…</div>
        ) : codex ? (
          <div className="text-muted/60 text-xs px-1 py-3 leading-relaxed">
            <span className="text-text">Codex has no skill mechanism</span> — it takes standing
            instructions from <span className="font-mono text-text">AGENTS.md</span> in the project
            rather than selecting a skill per task. Switch this pane to Claude or DeepSeek to use
            skills; the same library is on both.
          </div>
        ) : skills.length === 0 ? (
          <div className="text-muted/60 text-xs px-1 py-3 leading-relaxed">
            No skills installed. Add them under{" "}
            <span className="font-mono text-text">~/.claude/skills/&lt;name&gt;/SKILL.md</span>
            {" "}and they'll appear here{agent === "deepseek-harness"
              ? ", and load in DeepSeek too — the Studio mounts that same folder into the DeepSeek runtime."
              : ", usable in every project."}
          </div>
        ) : (
          skills.map((sk) => (
            <div key={sk.id} className={cls("rounded-md border p-2.5", sk.enabled ? "border-line bg-panel/40" : "border-line bg-panel2/30")}>
              <div className="flex items-center gap-2">
                <Wand2 size={13} className={cls("shrink-0", sk.enabled ? "text-brand" : "text-muted/60")} />
                <span className={cls("font-medium text-[13px] truncate", !sk.enabled && "text-muted")}>{sk.name}</span>
                {sk.category && <span className="chip text-[9px] shrink-0">{sk.category}</span>}
                <span className="chip text-[9px] shrink-0">{sk.scope}</span>
                <button className="ml-auto shrink-0 disabled:opacity-50" disabled={busy === sk.id}
                  onClick={() => toggle(sk)} title={sk.enabled ? "Disable" : "Enable"}>
                  <span className={cls("flex items-center h-5 w-9 rounded-full transition-colors px-0.5", sk.enabled ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                    <span className="h-4 w-4 rounded-full bg-white shadow" />
                  </span>
                </button>
              </div>
              <div className={cls("text-[11px] mt-1 leading-snug", sk.enabled ? "text-muted" : "text-muted/50")}>{summarize(sk.description)}</div>
              {(sk.created || sk.updated) && (
                <div className="text-[10px] text-muted/50 mt-1 flex items-center gap-2 flex-wrap">
                  {sk.created && <span title="When this skill was created">added {sk.created}</span>}
                  {sk.updated && sk.updated !== (sk.created || "").slice(0, 10) && <span title="Last edited">· updated {sk.updated}</span>}
                </div>
              )}
            </div>
          ))
        )}
      </div>
      <div className="shrink-0 px-2 py-1 border-t border-line text-[10px] text-muted/70">
        New skills arrive <span className="text-muted">off</span> — enable the ones you want. Applies to your next message, every project. Newest added first.
      </div>
    </div>
  );
}

// Review & revert AI edits — snapshots are taken before each turn you send from the Studio (and on
// demand), WHICHEVER ENGINE SENT IT. Expand one to see what changed; Restore rolls files back.
//
// Keyed by the folder, not by the engine: a checkpoint frames the project's files, so a Codex turn
// and a Claude turn in the same folder share one list. `agent` is only what the panel says and
// labels a manual snapshot with — and it is why this panel works on a DeepSeek pane at all, since
// the DeepSeek adapter used to return from `send()` before the line that took the snapshot.
function CheckpointsPanel({ root, agent, agentName, resolveAgentName }: {
  root: WorkspaceRoot; agent: string; agentName: string; resolveAgentName: (id: string) => string;
}) {
  const [list, setList] = useState<Checkpoint[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState("");        // "<checkpoint id><path>" of the open file
  const [diffs, setDiffs] = useState<Record<string, CheckpointChange[]>>({});
  const [busy, setBusy] = useState(false);
  const toast = useStore((s) => s.toast);

  const load = () => api.ckptList(root.id).then((r) => setList(r.checkpoints)).catch(() => {});
  useEffect(() => { if (root.id) load(); /* eslint-disable-next-line */ }, [root.id]);

  async function snapshot() {
    setBusy(true);
    try {
      const r = await api.ckptCreate(root.id, "manual snapshot", agent);
      if (r.ok) { toast(`Checkpoint saved · ${r.file_count} files`, "ok"); load(); }
      else toast(r.error || "snapshot failed", "danger");
    } catch (e: any) { toast(e.message, "danger"); } finally { setBusy(false); }
  }
  async function toggle(cid: string) {
    if (openId === cid) { setOpenId(null); return; }
    setOpenId(cid);
    if (!diffs[cid]) {
      const r = await api.ckptDiff(root.id, cid).catch(() => null);
      if (r?.ok) setDiffs((d) => ({ ...d, [cid]: r.changes }));
    }
  }
  async function restore(cid: string) {
    if (!window.confirm("Restore project files to this checkpoint?\n\nFiles changed since will be overwritten back to this snapshot. Files created since are kept (not deleted).")) return;
    let r = await api.ckptRestore(root.id, cid).catch(() => null);
    // Another agent is writing in this folder: a restore would put its files back under it.
    if (r && !r.ok && r.busy?.length) {
      if (!window.confirm(`${r.error}

Restore anyway? That agent's edits since the checkpoint will be overwritten.`)) return;
      r = await api.ckptRestore(root.id, cid, undefined, true).catch(() => null);
    }
    if (r?.ok) { toast(`Restored ${r.restored} file(s)`, "ok"); setDiffs((d) => { const n = { ...d }; delete n[cid]; return n; }); if (openId === cid) toggle(cid); }
    else toast(r?.error || "restore failed", "danger");
  }

  const stColor = (s: string) => s === "added" ? "text-ok" : s === "deleted" ? "text-danger" : "text-warn";
  const stTag = (s: string) => s === "added" ? "A" : s === "deleted" ? "D" : "M";

  return (
    <div className="h-full flex flex-col bg-bg">
      <div className="shrink-0 flex items-center gap-2 px-2 py-1.5 border-b border-line text-xs bg-panel">
        <RotateCcw size={13} className="text-brand" />
        <span className="font-semibold">Checkpoints</span>
        <span className="text-muted">· {list.length}</span>
        {/* One list per FOLDER, every engine — so the header says what is about to be snapshotted
            rather than implying the list belongs to one engine. */}
        <span className="chip !py-0 text-[9px] text-muted"
          title="A checkpoint covers the whole project folder, so Claude, Codex and DeepSeek share one list">
          this folder · next turn: {agentName}
        </span>
        <button className="ml-auto chip hover:border-brand hover:text-brand disabled:opacity-50" disabled={busy}
          onClick={snapshot} title="Snapshot the project now (before editing)"><Plus size={11} /> Checkpoint now</button>
        <button className="chip hover:text-text" onClick={load} title="Refresh"><RefreshCw size={11} /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-2 space-y-1.5">
        {list.length === 0 && (
          <div className="text-muted/60 text-xs px-1 py-3 leading-relaxed">
            No checkpoints for this folder yet. One is taken automatically before each turn you send
            from the Studio — from <span className="text-text">any</span> engine, Claude, Codex or
            DeepSeek — or hit <span className="text-text">Checkpoint now</span> to snapshot before
            working in VS Code.
          </div>
        )}
        {list.map((c) => (
          <div key={c.id} className="rounded-md border border-line bg-panel/40 overflow-hidden">
            <button className="w-full text-left px-2 py-1.5 hover:bg-panel2 flex items-center gap-2"
              onClick={() => toggle(c.id)}>
              <RotateCcw size={12} className={openId === c.id ? "text-brand" : "text-muted"} />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] text-text/90 truncate">{c.label || (c.kind === "turn" ? "before a turn" : "manual snapshot")}</div>
                <div className="text-[10px] text-muted flex items-center gap-2">
                  <span>{timeAgo(c.ts)}</span><span>· {c.file_count} files</span>
                  {c.kind === "turn" && <span className="chip !py-0 text-[9px]">auto</span>}
                  {/* WHOSE TURN this frames. Older rows predate the field and simply say nothing. */}
                  {c.agent && <span className="chip !py-0 text-[9px]" title="The engine that was about to run">{resolveAgentName(c.agent)}</span>}
                  {c.partial && <span className="text-warn">· partial</span>}
                </div>
              </div>
            </button>
            {openId === c.id && (
              <div className="border-t border-line px-2 py-1.5 space-y-1">
                {!diffs[c.id] ? (
                  <div className="text-[11px] text-muted">comparing…</div>
                ) : diffs[c.id].length === 0 ? (
                  <div className="text-[11px] text-muted">No changes since this checkpoint.</div>
                ) : (
                  <>
                    <div className="text-[10px] text-muted uppercase tracking-wide">{diffs[c.id].length} changed</div>
                    {diffs[c.id].map((ch) => (
                      <div key={ch.path}>
                        <button className="w-full text-left flex items-center gap-2 text-[12px] hover:bg-panel2 rounded px-0.5"
                          onClick={() => setOpenFile((f) => f === c.id + ch.path ? "" : c.id + ch.path)}>
                          <span className={cls("font-mono font-bold w-3 shrink-0", stColor(ch.status))}>{stTag(ch.status)}</span>
                          <span className="font-mono truncate flex-1" title={ch.path}>{ch.path}</span>
                          {ch.added > 0 && <span className="text-ok font-mono text-[11px]">+{ch.added}</span>}
                          {ch.removed > 0 && <span className="text-danger font-mono text-[11px]">−{ch.removed}</span>}
                        </button>
                        {/* THE LINES, and they can be written on. `lineIsFile` is true here and
                            only here: a checkpoint diff compares whole files, so its numbers are
                            the file's own — unlike the feed's Edit cards, which diff two strings. */}
                        {openFile === c.id + ch.path && ch.hunks && ch.hunks.length > 0 && (
                          <div className="text-[11px] pl-1">
                            <DiffView hunks={ch.hunks} total={ch.added + ch.removed}
                              file={ch.path} projectId={root.id} lineIsFile />
                          </div>
                        )}
                      </div>
                    ))}
                    <button className="mt-1 btn-primary !py-1 !px-2 text-xs w-full" onClick={() => restore(c.id)}>
                      <RotateCcw size={12} /> Restore project to this checkpoint
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="shrink-0 px-2 py-1 border-t border-line text-[10px] text-muted/70">
        Restore overwrites changed files back to the snapshot · new files are kept.
      </div>
    </div>
  );
}
