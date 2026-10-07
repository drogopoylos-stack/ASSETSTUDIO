import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Bell,
  BellOff,
  Bot,
  Eye,
  EyeOff,
  FolderOpen,
  GitBranch,
  GitCommitHorizontal,
  Globe,
  GripVertical,
  RefreshCw,
  Radar,
  RotateCcw,
  Rocket,
  Send,
  Server,
  SlidersHorizontal,
  Square,
  Terminal,
} from "lucide-react";
import { AppWindow, Brain, CornerDownRight, HelpCircle, LayoutGrid, ListChecks, Pencil, Search, Wrench } from "lucide-react";
import { api } from "../api/client";
import { UsageLimitsBar } from "../components/UsageBar";
import { useStore } from "../store/useStore";
import type { CCAgent, CCPort, CCProject, FeedDiffLine, FeedEvent, FeedTodo, MissionOverview } from "../types";
import { Empty, Section, Spinner, cls, pollWhileVisible, timeAgo } from "../components/ui";
import { atBottom, nextScrollTop } from "../components/feedScroll";
import { SessionFeed } from "../components/SessionFeed";
import { ContextMeter } from "../components/ContextMeter";
import { ChatComposer } from "../components/ChatComposer";
import { WorkingPulse } from "../components/WorkingPulse";
import { ModelBadge } from "../components/ModelBadge";
import { folderAgent, sendSettings } from "../components/sendPrefs";

const REFRESH_MS = 4000;
const LS_KEY = "asset-studio-mission-layout-v2";
const DEFAULT_W = 400;
const DEFAULT_H = 340;
const GAP = 12;
const MIN_W = 280;
const MIN_H = 180;
const SNAP = 8;
const snap = (v: number) => Math.round(v / SNAP) * SNAP;

/** "c:\Users\Administrator\Downloads\STUDIO" → "…\Downloads\STUDIO" — enough to identify the
 *  folder without spending a whole header row on it (full path stays in the tooltip). */
const shortPath = (p?: string) => {
  if (!p) return "";
  const parts = p.split(/[\\/]/).filter(Boolean);
  return parts.length <= 2 ? p : `…\\${parts.slice(-2).join("\\")}`;
};

// Non-overlapping row-flow: cards keep their own widths and flow left→right, wrapping
// to a new row when they don't fit — so a wide card lands alone on its row and pushes
// the rest down. Order = each card's current visual position (drag to reorder), then
// reflow snaps everything so nothing ever overlaps or hides behind another.
function packLayout(prev: Layout, list: CCProject[], cw: number): Layout {
  const hidden = prev.hidden || [];
  const visible = list.filter((p) => !hidden.includes(p.id) && prev.cards[p.id]);
  const ordered = [...visible].sort((a, b) => {
    const ca = prev.cards[a.id]!, cb = prev.cards[b.id]!;
    const dy = (ca.y ?? 0) - (cb.y ?? 0);
    if (Math.abs(dy) > 30) return dy;           // different rows → top to bottom
    return (ca.x ?? 0) - (cb.x ?? 0);           // same row → left to right
  });
  const cards = { ...prev.cards };
  let x = 0, y = 0, rowH = 0;
  for (const p of ordered) {
    const cur = cards[p.id]!;
    const w = Math.min(Math.max(cur.w || DEFAULT_W, MIN_W), cw);   // never wider than canvas
    const h = cur.collapsed ? 56 : (cur.h || DEFAULT_H);
    if (x > 0 && x + w > cw + 1) { x = 0; y += rowH + GAP; rowH = 0; }  // wrap to next row
    cards[p.id] = { ...cur, x, y, w };
    x += w + GAP;
    rowH = Math.max(rowH, h);
  }
  return { ...prev, cards };
}

type CardState = { x: number; y: number; w: number; h: number; collapsed?: boolean; z?: number };
type Layout = { cards: Record<string, CardState>; tidy?: boolean; hidden?: string[]; cols?: number };

function loadLayout(): Layout {
  try {
    const l = JSON.parse(localStorage.getItem(LS_KEY) || "");
    return { cards: l.cards || {}, tidy: l.tidy, hidden: l.hidden || [] };
  } catch {
    return { cards: {}, hidden: [] };
  }
}
function saveLayout(l: Layout) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(l));
  } catch {
    /* ignore */
  }
}

export default function MissionControl() {
  const [ov, setOv] = useState<MissionOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(() => localStorage.getItem("mc-auto") !== "0");
  const [layout, setLayout] = useState<Layout>(loadLayout);
  const [claudeOk, setClaudeOk] = useState(false);
  const [windowsOpen, setWindowsOpen] = useState(false);
  const [notify, setNotify] = useState(() =>
    localStorage.getItem("mc-notify") === "1" &&
    typeof Notification !== "undefined" && Notification.permission === "granted");
  const toast = useStore((s) => s.toast);
  const openInWorkspace = useStore((s) => s.openInWorkspace);
  const timer = useRef<number | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);

  // Snap every card into N equal columns. Free mode only rounds to an 8px grid, so neighbours can
  // end up 400 and 392 wide and never line up — that ragged edge is what feels clumsy. Giving all
  // cards one computed width makes N-per-row land flush, with no leftover gap on the right.
  const applyColumns = (n: number) => {
    const cw = canvasRef.current?.clientWidth || 1200;
    const w = Math.max(MIN_W, Math.floor((cw - GAP * (n - 1)) / n));
    setLayout((prev) => {
      const hidden = prev.hidden || [];
      const visible = (ov?.projects || []).filter((p) => !hidden.includes(p.id) && prev.cards[p.id]);
      const ordered = [...visible].sort((a, b) => {          // keep the on-screen reading order
        const ca = prev.cards[a.id]!, cb = prev.cards[b.id]!;
        const dy = (ca.y ?? 0) - (cb.y ?? 0);
        return Math.abs(dy) > 30 ? dy : (ca.x ?? 0) - (cb.x ?? 0);
      });
      const cards = { ...prev.cards };
      let rowTop = 0, rowH = 0;
      ordered.forEach((p, i) => {
        const col = i % n;
        const cur = cards[p.id]!;
        if (col === 0 && i > 0) { rowTop += rowH + GAP; rowH = 0; }
        cards[p.id] = { ...cur, x: col * (w + GAP), y: rowTop, w };
        rowH = Math.max(rowH, cur.collapsed ? 56 : (cur.h || DEFAULT_H));
      });
      const next = { ...prev, cards, tidy: false, cols: n };
      saveLayout(next);
      return next;
    });
  };
  const interacting = useRef(false); // pause refresh while dragging/resizing a card
  const prevState = useRef<Record<string, { working: boolean; awaiting: boolean }>>({});
  const firstFlow = useRef(true);    // reflow once on first load to clean any saved overlaps

  // desktop notifications when a session finishes or needs your answer (Cline-style)
  useEffect(() => {
    const projs = ov?.projects || [];
    const fire = (title: string, body: string, proj: CCProject) => {
      if (!notify || typeof Notification === "undefined" || Notification.permission !== "granted") return;
      try {
        const n = new Notification(title, { body, tag: proj.id });
        n.onclick = () => {            // clicking the toast takes you to that session's chat
          window.focus();
          if (proj.exists && proj.path) openInWorkspace(proj.path);
          n.close();
        };
      } catch { /* ignore */ }
    };
    for (const p of projs) {
      const prev = prevState.current[p.id];
      const cur = { working: !!p.working, awaiting: !!p.awaiting_input };
      if (prev) {
        if (!prev.awaiting && cur.awaiting) fire("❓ Needs your answer", `${p.name} is waiting for you`, p);
        else if (prev.working && !cur.working && !cur.awaiting) fire("✅ Turn finished", `${p.name} is done — click to open`, p);
      }
      prevState.current[p.id] = cur;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ov, notify]);

  function toggleNotify() {
    if (!notify && typeof Notification !== "undefined" && Notification.permission !== "granted") {
      Notification.requestPermission().then((perm) => {
        if (perm === "granted") { setNotify(true); localStorage.setItem("mc-notify", "1"); toast("Desktop notifications on", "ok"); }
        else toast("Notifications blocked in browser settings", "warn");
      });
      return;
    }
    const next = !notify; setNotify(next); localStorage.setItem("mc-notify", next ? "1" : "0");
  }

  async function load(force = false) {
    setLoading(true);
    try {
      setOv(await api.mission(force));
    } catch (e: any) {
      toast(`Mission scan failed: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(true);
    api.claudeStatus().then((s) => setClaudeOk(s.available)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    if (auto) timer.current = window.setInterval(() => { if (!interacting.current && !document.hidden) load(false); }, REFRESH_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto]);

  // auto-place any project that doesn't yet have a card, then reflow (no overlap)
  useEffect(() => {
    if (!ov) return;
    const cw = canvasRef.current?.clientWidth || 1200;
    setLayout((prev) => {
      const cards = { ...prev.cards };
      let changed = false;
      ov.projects.forEach((p) => {
        if (!cards[p.id]) {
          cards[p.id] = { x: 1e6, y: 1e6, w: DEFAULT_W, h: DEFAULT_H };  // huge → flow appends to end
          changed = true;
        }
      });
      if (!changed && !firstFlow.current) return prev;   // reflow on new card OR first load
      firstFlow.current = false;
      const next = packLayout({ ...prev, cards }, ov.projects, cw);  // preserves hidden/tidy
      saveLayout(next);
      return next;
    });
  }, [ov]);

  function reflow() {
    const cw = canvasRef.current?.clientWidth || 1200;
    setLayout((prev) => {
      const next = packLayout(prev, ov?.projects || [], cw);
      saveLayout(next);
      return next;
    });
  }
  // keep cards tidy when the window resizes
  useEffect(() => {
    const onR = () => reflow();
    window.addEventListener("resize", onR);
    return () => window.removeEventListener("resize", onR);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ov]);

  function update(id: string, patch: Partial<CardState>) {
    setLayout((prev) => {
      const next = { ...prev, cards: { ...prev.cards, [id]: { ...prev.cards[id], ...patch } } };
      saveLayout(next);
      return next;
    });
  }
  function bringToFront(id: string) {
    setLayout((prev) => {
      const maxZ = Math.max(1, ...Object.values(prev.cards).map((c) => c.z || 1));
      if ((prev.cards[id]?.z || 1) >= maxZ) return prev;
      const next = { ...prev, cards: { ...prev.cards, [id]: { ...prev.cards[id], z: maxZ + 1 } } };
      saveLayout(next);
      return next;
    });
  }
  function resetLayout() {
    setLayout((prev) => {
      const next = { cards: {}, hidden: prev.hidden, tidy: false };
      saveLayout(next);
      return next;
    });
    setOv((o) => (o ? { ...o } : o)); // re-trigger auto-place
  }
  function autoArrange() {
    const cw = canvasRef.current?.clientWidth || 1200;
    setLayout((prev) => {
      const hidden = prev.hidden || [];
      const ordered = (ov?.projects || []).filter((p) => !hidden.includes(p.id)).sort((a, b) => b.last_activity - a.last_activity);
      const cards = { ...prev.cards };
      let x = 0, y = 0, rowH = 0;
      for (const p of ordered) {
        const cur = cards[p.id] || { x: 0, y: 0, w: DEFAULT_W, h: DEFAULT_H };
        const w = Math.min(Math.max(cur.w || DEFAULT_W, MIN_W), cw);
        const h = cur.collapsed ? 56 : (cur.h || DEFAULT_H);
        if (x > 0 && x + w > cw + 1) { x = 0; y += rowH + GAP; rowH = 0; }
        cards[p.id] = { ...cur, x, y, w };
        x += w + GAP;
        rowH = Math.max(rowH, h);
      }
      const next: Layout = { ...prev, cards, tidy: true };
      saveLayout(next);
      return next;
    });
  }
  function reflowIfTidy() {
    const cw = canvasRef.current?.clientWidth || 1200;
    setLayout((prev) => {
      if (!prev.tidy) return prev;
      const next = packLayout(prev, ov?.projects || [], cw);
      saveLayout(next);
      return next;
    });
  }
  function breakTidy() {
    setLayout((prev) => (prev.tidy ? (saveLayout({ ...prev, tidy: false }), { ...prev, tidy: false }) : prev));
  }
  function toggleHidden(id: string) {
    const cw = canvasRef.current?.clientWidth || 1200;
    setLayout((prev) => {
      const hidden = new Set(prev.hidden || []);
      hidden.has(id) ? hidden.delete(id) : hidden.add(id);
      let next: Layout = { ...prev, hidden: [...hidden] };
      next = packLayout(next, ov?.projects || [], cw);   // reflow so the gap closes / card returns in order
      saveLayout(next);
      return next;
    });
  }
  function setAllHidden(hide: boolean) {
    const cw = canvasRef.current?.clientWidth || 1200;
    setLayout((prev) => {
      let next: Layout = { ...prev, hidden: hide ? (ov?.projects || []).map((p) => p.id) : [] };
      next = packLayout(next, ov?.projects || [], cw);
      saveLayout(next);
      return next;
    });
  }

  function jumpToWaiting() {
    // skip hidden cards — jumping to one you deliberately hid just scrolls to nothing
    const hidden = layout.hidden || [];
    const wp = (ov?.projects || []).find((p) => p.awaiting_input && !hidden.includes(p.id));
    if (!wp) return;
    bringToFront(wp.id);
    const s = layout.cards[wp.id];
    const scroller = (canvasRef.current?.closest("main") || canvasRef.current?.parentElement) as HTMLElement | null;
    if (s && scroller && canvasRef.current) {
      scroller.scrollTo({ top: canvasRef.current.offsetTop + s.y - 16, behavior: "smooth" });
    }
  }

  const serverUrls = useMemo(() => {
    const urls = new Set<string>();
    (ov?.projects || []).forEach((p) => p.url && urls.add(p.url));
    (ov?.ports || []).forEach((p) => p.project_id && urls.add(p.url));
    return [...urls];
  }, [ov]);

  function openAll() {
    if (!serverUrls.length) return toast("No running project servers detected.", "warn");
    serverUrls.forEach((u) => window.open(u, "_blank"));
  }
  async function openProject(p: CCProject, target: "vscode" | "folder") {
    if (!p.exists) return toast("Project path not found on disk.", "warn");
    try {
      const r = await api.missionOpen(p.path, target);
      toast(r.ok ? `Opened ${p.name} (${r.opened})` : `Open failed: ${r.error}`, r.ok ? "ok" : "danger");
    } catch (e: any) {
      toast(`Open failed: ${e.message}`, "danger");
    }
  }

  const allProjects = ov?.projects || [];
  const hiddenSet = new Set(layout.hidden || []);
  const projects = allProjects.filter((p) => !hiddenSet.has(p.id));
  const canvasHeight =
    Math.max(
      300,
      ...projects.map((p) => {
        const s = layout.cards[p.id];
        return s ? s.y + (s.collapsed ? 60 : s.h) : 0;
      })
    ) + 80; // cards always flow now — just a little bottom breathing room

  const c = ov?.counts;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Radar size={20} className="text-brand" /> Mission Control
        </h2>
        <span className="text-sm text-muted">Drag ⠿ to move · resize from any edge · or hit Auto-arrange to tidy.</span>
        {/* count only VISIBLE cards: a hidden session shouldn't keep nagging "waiting" when you
            can't see it and the jump target wouldn't be on screen anyway */}
        {(() => {
          const hidden = layout.hidden || [];
          const waiting = (ov?.projects || []).filter((p) => p.awaiting_input && !hidden.includes(p.id));
          return waiting.length > 0 ? (
            <button className="chip text-warn border-warn/50 animate-pulse hover:bg-warn/20"
              onClick={jumpToWaiting} title="Jump to a session waiting for your answer">
              <HelpCircle size={11} /> {waiting.length} waiting
            </button>
          ) : null;
        })()}
        <div className="ml-auto flex items-center gap-2">
          <button className={cls("btn-ghost !px-2 flex items-center gap-1", notify && "text-brand")} onClick={toggleNotify}
            title={notify ? "Desktop notifications on — click to mute" : "Notify me when a session finishes or needs an answer"}>
            {notify ? <Bell size={14} /> : <BellOff size={14} />}
            <span className="text-xs">{notify ? "Notifying" : "Notify"}</span>
          </button>
          <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
            <input type="checkbox" checked={auto} onChange={(e) => { setAuto(e.target.checked); localStorage.setItem("mc-auto", e.target.checked ? "1" : "0"); }} /> auto-refresh
          </label>

          {/* Windows selector */}
          <div className="relative">
            <button className={cls("btn-ghost", windowsOpen && "bg-panel2")} onClick={() => setWindowsOpen((v) => !v)}
              title="Choose which projects stay open">
              <AppWindow size={15} /> Windows
              <span className="text-[10px] text-muted">{projects.length}/{allProjects.length}</span>
            </button>
            {windowsOpen && (
              <div className="absolute right-0 mt-1 z-[1100] w-72 card p-2 shadow-card" onMouseLeave={() => setWindowsOpen(false)}>
                <div className="flex items-center justify-between mb-1.5 px-1">
                  <span className="text-xs font-semibold">Open windows</span>
                  <div className="flex gap-1">
                    <button className="btn-ghost !px-1.5 !py-0.5 text-[11px]" onClick={() => setAllHidden(false)}>all</button>
                    <button className="btn-ghost !px-1.5 !py-0.5 text-[11px]" onClick={() => setAllHidden(true)}>none</button>
                  </div>
                </div>
                <div className="max-h-72 overflow-y-auto space-y-0.5">
                  {[...allProjects].sort((a, b) => b.last_activity - a.last_activity).map((p) => {
                    const open = !hiddenSet.has(p.id);
                    return (
                      <button key={p.id} onClick={() => toggleHidden(p.id)}
                        className={cls("w-full flex items-center gap-2 px-2 py-1 rounded text-xs hover:bg-panel2",
                          open ? "text-text" : "text-muted/60")}>
                        {open ? <Eye size={13} className="text-ok shrink-0" /> : <EyeOff size={13} className="shrink-0" />}
                        <span className={cls("h-1.5 w-1.5 rounded-full shrink-0", p.active ? "bg-ok" : "bg-line")} />
                        <span className="truncate flex-1 text-left">{p.name}</span>
                        {p.awaiting_input && <span className="text-warn text-[10px]">waiting</span>}
                        <span className="text-muted/50 text-[10px]">{p.last_activity ? timeAgo(p.last_activity) : ""}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* exact column snap — 2 / 3 / 4 cards per row, edges flush */}
          <div className="inline-flex rounded-md border border-line overflow-hidden shrink-0" title="Snap all cards into equal columns">
            {[2, 3, 4].map((n) => (
              <button key={n} type="button" onClick={() => applyColumns(n)}
                className={cls("px-2 py-1 text-[11px] transition-colors",
                  layout.cols === n && !layout.tidy ? "bg-brand-600 text-white" : "bg-panel2 text-muted hover:text-text")}
                title={`${n} cards per row`}>{n}&times;</button>
            ))}
          </div>
          <button className={cls("btn-ghost", layout.tidy && "text-brand")} onClick={autoArrange} title="Auto-arrange (tidy columns by last used)">
            <LayoutGrid size={15} /> Auto-arrange
          </button>
          <button className="btn-ghost" onClick={resetLayout} title="Reset card layout"><RotateCcw size={15} /></button>
          <button className="btn-ghost" onClick={() => load(true)} title="Refresh now">
            <RefreshCw size={15} className={cls(loading && "animate-spin")} />
          </button>
          <button className="btn-primary" onClick={openAll}><Rocket size={15} /> Open all servers</button>
        </div>
      </div>

      <UsageLimitsBar className="sticky top-0 -mx-3 border-y z-[1000]" />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat icon={<Radar size={16} />} label="Projects" value={c?.projects ?? "—"} />
        <Stat icon={<Activity size={16} />} label="Active now" value={c?.active_projects ?? "—"} accent="ok" />
        <Stat icon={<Bot size={16} />} label="Agents running" value={c?.active_agents ?? "—"} accent="brand" />
        <Stat icon={<Server size={16} />} label="Live servers" value={c?.running_servers ?? "—"} accent="accent" />
      </div>

      {ov && ov.agents.length > 0 && (
        <Section title="Agents working right now" right={<span className="chip text-brand">{ov.agents.length}</span>}>
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {ov.agents.slice(0, 30).map((a) => <AgentRow key={a.project_id + a.agent_id} a={a} />)}
          </div>
        </Section>
      )}

      {!ov ? (
        <div className="flex justify-center py-16"><Spinner size={28} /></div>
      ) : projects.length === 0 ? (
        <Empty icon={<Radar size={24} />} label="No Claude Code projects found in ~/.claude/projects" />
      ) : (
        <div ref={canvasRef} className="relative w-full" style={{ height: canvasHeight }}>
          {projects.map((p) => {
            const s = layout.cards[p.id];
            if (!s) return null;
            return (
              <ProjectCard
                key={p.id}
                p={p}
                state={s}
                onChange={(patch) => update(p.id, patch)}
                onRaise={() => bringToFront(p.id)}
                onInteract={(v) => (interacting.current = v)}
                onOpen={openProject}
                claudeOk={claudeOk}
                tidy={!!layout.tidy}
                onAfterDrag={reflow}
                onAfterResize={reflow}
              />
            );
          })}
        </div>
      )}

      {ov && ov.ports.length > 0 && (
        <Section title="Detected localhost servers" desc="Mapped to projects by process working directory">
          <div className="space-y-1.5">{ov.ports.map((pt) => <PortRow key={pt.port} pt={pt} />)}</div>
        </Section>
      )}
    </div>
  );
}

function Stat({ icon, label, value, accent }: { icon: React.ReactNode; label: string; value: any; accent?: string }) {
  const col = accent === "ok" ? "text-ok" : accent === "brand" ? "text-brand" : accent === "accent" ? "text-accent" : "text-text";
  return (
    <div className="card p-3 flex items-center gap-3">
      <span className={cls("text-muted", col)}>{icon}</span>
      <div>
        <div className={cls("text-2xl font-semibold leading-none", col)}>{value}</div>
        <div className="text-xs text-muted mt-1">{label}</div>
      </div>
    </div>
  );
}

function AgentRow({ a }: { a: CCAgent }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="relative flex h-2 w-2 shrink-0">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand opacity-60" />
        <span className="relative inline-flex rounded-full h-2 w-2 bg-brand" />
      </span>
      <span className="chip shrink-0">{a.project_name}</span>
      <span className="truncate text-muted flex-1" title={a.label}>{a.label || "(starting…)"}</span>
      <span className="text-[11px] text-muted font-mono shrink-0">{a.age_seconds < 60 ? "now" : `${Math.floor(a.age_seconds / 60)}m`}</span>
    </div>
  );
}

function PortRow({ pt }: { pt: CCPort }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <Globe size={14} className={pt.project_id ? "text-ok" : "text-muted"} />
      <a href={pt.url} target="_blank" rel="noreferrer" className="font-mono text-brand hover:underline">:{pt.port}</a>
      <span className="text-xs text-muted">{pt.process}</span>
      <span className="ml-auto chip">{pt.is_self ? "Asset Studio" : pt.project_name || "unmatched"}</span>
    </div>
  );
}

function IconBtn({ title, onClick, disabled, href, children }: any) {
  const cl = "inline-flex items-center justify-center h-6 px-1.5 rounded border border-line bg-panel2 hover:bg-line text-muted hover:text-text disabled:opacity-30 text-xs gap-1";
  if (href)
    return <a className={cl} href={href} target="_blank" rel="noreferrer" title={title} onClick={(e) => e.stopPropagation()}>{children}</a>;
  return <button className={cl} title={title} disabled={disabled} onClick={(e) => { e.stopPropagation(); onClick?.(); }}>{children}</button>;
}

function ProjectCard({
  p, state, onChange, onRaise, onInteract, onOpen, claudeOk, tidy, onAfterDrag, onAfterResize,
}: {
  p: CCProject;
  state: CardState;
  onChange: (patch: Partial<CardState>) => void;
  onRaise: () => void;
  onInteract: (v: boolean) => void;
  onOpen: (p: CCProject, t: "vscode" | "folder") => void;
  claudeOk: boolean;
  tidy?: boolean;
  onAfterDrag?: () => void;
  onAfterResize?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const collapsed = !!state.collapsed;
  const [sending, setSending] = useState(false);
  const answerPoll = useRef<number | null>(null);
  const cardToast = useStore((s) => s.toast);
  const openInWorkspace = useStore((s) => s.openInWorkspace);
  const containerW = () => ref.current?.parentElement?.clientWidth || 99999;

  // WHICH ENGINE THIS CARD IS SHOWING. `p.id` is the bare Claude project id, but the composer below
  // can be switched to Codex or DeepSeek, whose conversations are filed under "<engine>--<id>"
  // (Workspace.altFeedPrefix, backend mission.project_dir). The feed, the live pulse, the context
  // ring and the conversation picker all key off that id, so the card kept rendering Claude's
  // transcript while another engine was answering — and `quickAnswer` sent to Claude's session
  // because it named no agent at all.
  const [agentTick, setAgentTick] = useState(0);
  useEffect(() => {
    const f = () => setAgentTick((n) => n + 1);
    window.addEventListener("cc-agent", f);
    const iv = window.setInterval(() => { if (!document.hidden) f(); }, 2000);
    return () => { window.removeEventListener("cc-agent", f); window.clearInterval(iv); };
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const cardAgent = useMemo(() => folderAgent(p.id), [p.id, agentTick]);
  const cardFeed = cardAgent && cardAgent !== "claude" ? `${cardAgent}--${p.id}` : p.id;

  // answer a session's question (or quick-reply) using the saved model/mode
  async function quickAnswer(text: string) {
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      // What this card's chat box sends. This read the pre-per-agent keys ("opus", written once long
      // ago) and no effort at all, and a send that differs from the running session restarts it
      // with the difference (sendPrefs.ts).
      // What this card's chat box sends — for the engine the card is SET TO, and the turn is sent
      // to that engine too (`agent`). Naming no agent sent every quick answer to Claude's session
      // even on a card whose composer was set to Codex or DeepSeek.
      const s = sendSettings(p.id, cardAgent);
      const r = await api.sessionSend(p.id, {
        message: text,
        model: s.model,
        permission_mode: s.permission_mode,
        effort: s.effort,
        fork: s.fork,
        agent: cardAgent,
        path: p.path,
      });
      if (!r.ok) {
        cardToast(r.error || "send failed", "danger");
        setSending(false);
        return;
      }
      cardToast(`Answered ${p.name}: ${text.slice(0, 40)}`, "ok");
      answerPoll.current = window.setInterval(async () => {
        const s = await api.sessionSending(cardFeed).catch(() => ({ sending: false }));
        if (!s.sending) {
          setSending(false);
          if (answerPoll.current) window.clearInterval(answerPoll.current);
        }
      }, 1500);
    } catch (e: any) {
      cardToast(e.message, "danger");
      setSending(false);
    }
  }
  useEffect(() => () => { if (answerPoll.current) window.clearInterval(answerPoll.current); }, []);

  // move by the grip handle
  function startDrag(e: React.PointerEvent) {
    e.preventDefault();
    onRaise();
    onInteract(true);
    const el = ref.current!;
    const sx = e.clientX, sy = e.clientY, ox = state.x, oy = state.y, cw = containerW();
    el.style.opacity = "0.92";
    function move(ev: PointerEvent) {
      el.style.left = `${Math.max(0, Math.min(ox + ev.clientX - sx, cw - 60))}px`;
      el.style.top = `${Math.max(0, oy + ev.clientY - sy)}px`;
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      el.style.opacity = "";
      onInteract(false);
      onChange({ x: snap(parseInt(el.style.left) || 0), y: snap(parseInt(el.style.top) || 0) });
      onAfterDrag?.(); // moving a card breaks tidy mode → free positioning
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // resize from any edge/corner — dirX/dirY in {-1,0,1}
  function startResize(e: React.PointerEvent, dirX: number, dirY: number) {
    e.preventDefault();
    e.stopPropagation();
    onRaise();
    onInteract(true);
    const el = ref.current!;
    const sx = e.clientX, sy = e.clientY;
    const ox = state.x, oy = state.y, ow = state.w, oh = state.h, cw = containerW();
    function move(ev: PointerEvent) {
      const dmx = ev.clientX - sx, dmy = ev.clientY - sy;
      let x = ox, y = oy, w = ow, h = oh;
      if (dirX > 0) w = ow + dmx;
      if (dirX < 0) { w = ow - dmx; x = ox + dmx; }
      if (dirY > 0) h = oh + dmy;
      if (dirY < 0) { h = oh - dmy; y = oy + dmy; }
      if (w < MIN_W) { if (dirX < 0) x = ox + (ow - MIN_W); w = MIN_W; }
      if (h < MIN_H) { if (dirY < 0) y = oy + (oh - MIN_H); h = MIN_H; }
      if (x < 0) { w += x; x = 0; }
      if (y < 0) { h += y; y = 0; }
      if (x + w > cw) w = cw - x;
      el.style.left = `${x}px`; el.style.top = `${y}px`;
      el.style.width = `${w}px`; el.style.height = `${h}px`;
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      onInteract(false);
      onChange({
        x: snap(parseInt(el.style.left) || 0), y: snap(parseInt(el.style.top) || 0),
        w: snap(el.offsetWidth), h: snap(el.offsetHeight),
      });
      onAfterResize?.(); // in tidy mode, reflow so cards stay clamped under each other
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  const RH = (dirX: number, dirY: number, css: React.CSSProperties, cursor: string) => (
    <div onPointerDown={(e) => startResize(e, dirX, dirY)}
      style={{ position: "absolute", zIndex: 20, cursor, touchAction: "none", ...css }} />
  );

  return (
    <div
      ref={ref}
      onPointerDownCapture={onRaise}
      style={{
        position: "absolute",
        left: state.x,
        top: state.y,
        width: state.w,
        height: collapsed ? "auto" : state.h,
        maxWidth: "100%",
        minWidth: MIN_W,
        minHeight: collapsed ? 0 : MIN_H,
        overflow: "hidden",
        zIndex: state.z || 1,
      }}
      className={cls("card p-3 flex flex-col gap-2",
        p.awaiting_input ? "border-warn/70 ring-1 ring-warn/40" : p.active && "border-brand/50")}
    >
      {!collapsed && (
        <>
          {RH(0, -1, { top: 0, left: 12, right: 12, height: 7 }, "ns-resize")}
          {RH(0, 1, { bottom: 0, left: 12, right: 12, height: 7 }, "ns-resize")}
          {RH(-1, 0, { left: 0, top: 12, bottom: 12, width: 7 }, "ew-resize")}
          {RH(1, 0, { right: 0, top: 12, bottom: 12, width: 7 }, "ew-resize")}
          {RH(-1, -1, { left: 0, top: 0, width: 13, height: 13 }, "nwse-resize")}
          {RH(1, -1, { right: 0, top: 0, width: 13, height: 13 }, "nesw-resize")}
          {RH(-1, 1, { left: 0, bottom: 0, width: 13, height: 13 }, "nesw-resize")}
          {RH(1, 1, { right: 0, bottom: 0, width: 13, height: 13 }, "nwse-resize")}
          {/* visible corner grip hint */}
          <div className="absolute bottom-0.5 right-0.5 pointer-events-none text-muted/40" style={{ fontSize: 9 }}>⌟</div>
        </>
      )}

      {/* header */}
      <div className="flex items-start gap-1.5">
        <button
          className="text-muted hover:text-text cursor-grab active:cursor-grabbing mt-0.5 shrink-0 touch-none"
          onPointerDown={startDrag}
          title="Drag to move"
        >
          <GripVertical size={14} />
        </button>
        <button
          className="text-muted hover:text-text mt-0.5 shrink-0"
          onClick={() => { onChange({ collapsed: !collapsed }); onAfterResize?.(); }}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? <Eye size={13} /> : <EyeOff size={13} />}
        </button>
        <span className={cls("mt-1 h-2 w-2 rounded-full shrink-0", p.active ? "bg-ok" : "bg-line")} />
        <div className="min-w-0 flex-1">
          <div className="font-semibold truncate flex items-center gap-2">
            <span className={cls("truncate", p.exists && "cursor-pointer hover:text-brand hover:underline decoration-dotted")}
              onDoubleClick={() => p.exists && p.path && openInWorkspace(p.path)}
              title={p.exists ? "Double-click to open in Workspace" : p.name}>{p.name}</span>
            {/* Flat counting made an obedient fan-out look like a runaway: ask for 5, see 16.
                Split it by ORIGIN — top-level spawns are what you asked for, nested ones were
                spawned by those agents themselves. */}
            {(p.agents_active > 0 || (p.agents_nested ?? 0) > 0) && (
              <span className={cls("chip", (p.agents_nested ?? 0) > 0 ? "text-warn" : "text-brand")}
                title={`${p.agents_active} agent${p.agents_active === 1 ? "" : "s"} writing right now.\n`
                  + `This session spawned ${p.agents_top ?? 0} top-level agent${(p.agents_top ?? 0) === 1 ? "" : "s"}`
                  + ((p.agents_nested ?? 0) > 0
                    ? `, and those spawned ${p.agents_nested} more themselves (nested).\n`
                      + `Nested agents are NOT part of a "max N agents" limit — that only binds the top level. `
                      + `Use the Explore agent type (no Agent tool) if you need a hard cap.`
                    : ".")}>
                <Bot size={11} /> {p.agents_active}
                {(p.agents_nested ?? 0) > 0 && (
                  <span className="opacity-70 ml-0.5">{p.agents_top ?? 0}+{p.agents_nested}</span>
                )}
              </span>
            )}
            {p.awaiting_input && (
              <span className="chip text-warn border-warn/50 animate-pulse"><HelpCircle size={11} /> waiting for you</span>
            )}
            <WorkingPulse working={p.working} />
          </div>
        </div>
        {/* top-right: what is actually running here — model, effort and live context use.
            Was a whole row of its own at the bottom of the header; up here it costs no rows. */}
        {!collapsed && (p.model || p.ctx_max) && (
          <div className="flex items-center gap-1.5 shrink-0 max-w-[60%] justify-end overflow-hidden">
            {p.model && <ModelBadge model={p.model} folderId={p.id} />}
            {p.ctx_max ? (
              <ContextMeter className="min-w-0" folderId={p.id}
                data={{ model: p.model, ctx_used: p.ctx_used, ctx_max: p.ctx_max, ctx_pct: p.ctx_pct, ctx_remaining: p.ctx_remaining }} />
            ) : null}
          </div>
        )}
        <span className="text-[11px] text-muted shrink-0">{p.last_activity ? timeAgo(p.last_activity) : ""}</span>
      </div>

      {!collapsed && (
        <>
          {/* compact actions row — next to version */}
          <div className="flex items-center gap-1.5 flex-wrap">
            {/* the folder button now carries the path itself — kills the separate path row while
                keeping the folder one click away; the full path stays on hover */}
            <IconBtn title={p.path ? `Open folder · ${p.path}` : "Open folder"} disabled={!p.exists} onClick={() => onOpen(p, "folder")}>
              <FolderOpen size={12} /> <span className="font-mono truncate max-w-[200px]">{shortPath(p.path) || p.id}</span>
            </IconBtn>
            {p.url ? (
              <IconBtn title={`Open ${p.url}`} href={p.url}><Globe size={12} /> :{p.port}</IconBtn>
            ) : (
              <span className="text-[10px] text-muted/60">no server</span>
            )}
            {p.git_branch && <span className="chip" title={p.git_last_commit}><GitBranch size={11} /> {p.git_branch}</span>}
            {p.git_dirty > 0 && (
              <span className="chip text-warn"
                title={`${p.git_dirty} file${p.git_dirty === 1 ? "" : "s"} changed but not committed yet (git status). Not an error — just uncommitted work.`}>
                {p.git_dirty} uncommitted
              </span>
            )}
            {p.cc_version && <span className="chip text-muted/70" title="Claude Code CLI version">cc {p.cc_version}</span>}
          </div>

          {p.git_last_commit && (
            <div className="flex items-start gap-1.5 text-[11px] text-muted">
              <GitCommitHorizontal size={12} className="mt-0.5 shrink-0" />
              <span className="truncate" title={p.git_last_commit}>{p.git_last_commit}</span>
              <span className="shrink-0">· {p.git_last_commit_rel}</span>
            </div>
          )}
          {p.todo_in_progress && (
            <div className="flex items-center gap-1.5 text-[11px] text-brand">
              <Activity size={12} /> <span className="truncate">{p.todo_in_progress}</span>
              {p.todos_total > 0 && <span className="text-muted">({p.todo_done}/{p.todos_total})</span>}
            </div>
          )}

          <SessionFeed id={cardFeed} rootPath={p.path} active={p.active} fast={sending} onAnswer={quickAnswer} />
          {/* `claudeOk` is Claude's own availability. Gating the whole box on it meant a card was
              dead while Codex or DeepSeek was signed in and ready; the composer already disables
              itself for an engine that is not installed. */}
          <ChatComposer projectId={p.id} rootPath={p.path} rootName={p.name} variant="card"
            disabled={cardAgent === "claude" ? !claudeOk : false} onSendingChange={setSending} />
        </>
      )}
    </div>
  );
}

function ProjectFeed({ id, active, fast, onAnswer }: { id: string; active: boolean; fast?: boolean; onAnswer?: (t: string) => void }) {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [limit, setLimit] = useState(150);
  const font = useStore((s) => s.missionFont);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const restore = useRef<number | null>(null);
  const anchor = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.missionFeed(id, limit).then((r) => alive && setEvents(r.lines)).catch(() => {});
    const stop = pollWhileVisible(load, fast ? 1500 : active ? 4000 : 12000);
    return () => { alive = false; stop(); };
  }, [id, active, fast, limit]);

  // Same three reading positions as the conversation feed, same shared decision — including
  // the third one this page never had: hold your place while you read back, instead of letting
  // the next event slide the view out from under you.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const want = nextScrollTop({
      scrollHeight: el.scrollHeight,
      restore: restore.current,
      stick: stick.current,
      anchor: anchor.current,
    });
    if (want == null) return;
    if (restore.current != null) { anchor.current = restore.current; restore.current = null; }
    el.scrollTop = want;
  }, [events]);

  function loadEarlier() {
    const el = ref.current;
    restore.current = el ? el.scrollHeight - el.scrollTop : 0;
    setLimit((l) => Math.min(l + 300, 2000));
  }
  const mightHaveMore = events.length >= limit && limit < 2000;

  return (
    <div
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = atBottom(el.scrollHeight, el.scrollTop, el.clientHeight, 24);
        // Where to hold, when you are not following. Taken however you scrolled there — wheel,
        // scrollbar or keyboard — because only this event sees all three.
        anchor.current = stick.current ? null : el.scrollHeight - el.scrollTop;
      }}
      style={{ fontSize: font, lineHeight: 1.5, transform: "translateZ(0)", backfaceVisibility: "hidden" }}
      className="flex-1 min-h-0 overflow-y-auto rounded-md bg-bg border border-line p-2"
    >
      {mightHaveMore && (
        <button onClick={loadEarlier}
          className="sticky top-0 z-10 w-full mb-1 py-0.5 rounded bg-panel2 border border-line text-[10px] text-muted hover:text-text">
          ↑ load earlier
        </button>
      )}
      {events.length === 0 ? (
        <div className="text-muted/50 flex items-center gap-1"><Terminal size={12} /> no recent activity</div>
      ) : (
        <div className="relative">
          <div className="absolute left-[4px] top-1 bottom-1 w-px bg-line/70" />
          {events.map((e, i) => <EventRow key={i} e={e} onAnswer={onAnswer} />)}
        </div>
      )}
    </div>
  );
}

const URL_RE = /(https?:\/\/[^\s<>")]+)/g;

// Keep trailing sentence punctuation OUT of the href ("http://127.0.0.1:3888." → the "." breaks it).
function peelUrl(u: string): [string, string] {
  let end = u.length;
  while (end > 0 && ".,;:!?'’…".includes(u[end - 1])) end--;
  for (const [open, close] of [["[", "]"], ["{", "}"]] as const) {
    while (end > 0 && u[end - 1] === close &&
      u.slice(0, end).split(open).length <= u.slice(0, end).split(close).length) end--;
  }
  return [u.slice(0, end), u.slice(end)];
}
function UrlAnchor({ url }: { url: string }) {
  const [href, tail] = peelUrl(url);
  return (
    <>
      <a href={href} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
        className="text-brand underline decoration-brand/40 hover:decoration-brand break-all">{href}</a>
      {tail}
    </>
  );
}
function Linkify({ text }: { text: string }) {
  const parts = (text || "").split(URL_RE);
  return (
    <>
      {parts.map((p, i) =>
        /^https?:\/\//.test(p) ? (
          <UrlAnchor key={i} url={p} />
        ) : (
          <span key={i}>{p}</span>
        )
      )}
    </>
  );
}

// lightweight markdown for feed messages: bullets, numbered lists, headings, **bold**, *italic*, `code`, links
function parseInline(text: string): React.ReactNode[] {
  const re = /(`[^`]+`)|(\*\*[^*]+?\*\*)|(https?:\/\/[^\s<>")]+)|(\*[^*\n]+?\*|_[^_\n]+?_)/g;
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (m[1]) nodes.push(<code key={k++} className="px-1 py-0.5 rounded bg-panel2 text-accent font-mono text-[0.92em]">{tok.slice(1, -1)}</code>);
    else if (m[2]) nodes.push(<strong key={k++} className="font-semibold text-text">{parseInline(tok.slice(2, -2))}</strong>);
    else if (m[3]) nodes.push(<UrlAnchor key={k++} url={tok} />);
    else if (m[4]) nodes.push(<em key={k++}>{tok.slice(1, -1)}</em>);
    last = re.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function Markdown({ text }: { text: string }) {
  const lines = (text || "").split("\n");
  return (
    <div className="space-y-0.5">
      {lines.map((line, i) => {
        const head = line.match(/^(#{1,6})\s+(.*)/);
        const bullet = line.match(/^\s*[-*+]\s+(.*)/);
        const num = line.match(/^\s*(\d+)[.)]\s+(.*)/);
        if (head) return <div key={i} className="font-bold text-text mt-1">{parseInline(head[2])}</div>;
        if (bullet)
          return (
            <div key={i} className="flex gap-1.5">
              <span className="text-accent select-none">•</span>
              <span className="flex-1">{parseInline(bullet[1])}</span>
            </div>
          );
        if (num)
          return (
            <div key={i} className="flex gap-1.5">
              <span className="text-accent select-none font-mono">{num[1]}.</span>
              <span className="flex-1">{parseInline(num[2])}</span>
            </div>
          );
        if (line.trim() === "") return <div key={i} className="h-1" />;
        return <div key={i}>{parseInline(line)}</div>;
      })}
    </div>
  );
}

function Expandable({ text, max = 240, className }: { text: string; max?: number; className?: string }) {
  const [open, setOpen] = useState(false);
  const long = (text || "").length > max;
  const shown = open || !long ? text : text.slice(0, max).trimEnd() + "…";
  return (
    <span className={cls("whitespace-pre-wrap break-words", className)}>
      <Linkify text={shown} />
      {long && (
        <button className="text-[0.82em] text-muted hover:text-text ml-1 align-baseline"
          onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}>{open ? "less" : "more"}</button>
      )}
    </span>
  );
}

function kindColor(e: FeedEvent): string {
  if (e.kind === "question") return "rgb(var(--c-warn))";
  if (e.kind === "thinking") return "rgb(var(--c-accent))";
  if (e.kind === "user") return "rgb(var(--c-text))";
  if (e.kind === "result") return e.ok === false ? "rgb(var(--c-danger))" : "rgb(var(--c-muted))";
  if (e.kind === "tool")
    return e.icon === "edit" ? "rgb(var(--c-ok))" : e.icon === "terminal" ? "rgb(var(--c-warn))" : "rgb(var(--c-accent))";
  return "rgb(var(--c-brand))";
}

function toolIcon(icon?: string) {
  return icon === "edit" ? Pencil : icon === "terminal" ? Terminal : icon === "search" ? Search
    : icon === "check" ? ListChecks : icon === "bot" ? Bot : icon === "globe" ? Globe : Wrench;
}

// keep byte-identical with cc_session.py / SessionFeed.tsx — steering & /btw wrapper prefixes
// are stripped so Control cards show the user's clean note, not the internal framing text
const STEER_PREFIX = "↪ Steering update (sent while you were working)";
const BTW_PREFIX = "↪ Side-note (by the way — sent while you work)";
function stripSteer(t: string): string {
  if (t.startsWith(STEER_PREFIX) || t.startsWith(BTW_PREFIX)) {
    const nl = t.indexOf("\n\n");
    t = nl >= 0 ? t.slice(nl + 2) : t;
  }
  return t.replace(/^\/btw\b[ \t]*/i, "");
}

function EventRow({ e, onAnswer }: { e: FeedEvent; onAnswer?: (t: string) => void }) {
  return (
    <div className="relative pl-5 py-[3px]">
      <span className="absolute left-0 top-[7px] h-2.5 w-2.5 rounded-full ring-2 ring-bg" style={{ background: kindColor(e) }} />
      <Ts ts={e.ts} />
      <EventBody e={e} onAnswer={onAnswer} />
    </div>
  );
}

function Ts({ ts }: { ts: string }) {
  return <span className="text-muted/40 text-[0.82em] mr-1 select-none font-mono">{ts}</span>;
}

function EventBody({ e, onAnswer }: { e: FeedEvent; onAnswer?: (t: string) => void }) {
  if (e.kind === "question")
    return (
      <div className="rounded-md border border-warn/50 bg-warn/10 p-2 my-0.5 space-y-1.5">
        <div className="flex items-center gap-1.5 text-warn font-semibold">
          <HelpCircle size={13} /> Needs your answer
        </div>
        <div className="text-text/90"><Markdown text={e.text || ""} /></div>
        {e.options && e.options.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {e.options.map((o, i) => (
              <button
                key={i}
                title={o.description}
                onClick={() => onAnswer?.(o.label)}
                className="px-2 py-1 rounded-md border border-warn/50 bg-panel2 hover:bg-warn/20 text-text text-xs font-medium"
              >
                {o.label}
              </button>
            ))}
          </div>
        )}
        <div className="text-[10px] text-muted/70">click an option, or type your own reply below ↓</div>
      </div>
    );
  if (e.kind === "thinking")
    return (
      <span className="text-muted italic">
        <Brain size={12} className="inline mr-1 -mt-0.5 text-accent" />
        {e.text ? <Expandable text={e.text} max={200} className="text-muted" /> : <span className="text-muted/70">Thinking…</span>}
      </span>
    );
  if (e.kind === "user")
    return (
      <div className="text-text/90 flex gap-1.5">
        <span className="text-ok font-bold shrink-0">›</span>
        <div className="flex-1 min-w-0"><Markdown text={stripSteer(e.text || "")} /></div>
      </div>
    );
  if (e.kind === "text")
    return <div className="text-text/90"><Markdown text={e.text || ""} /></div>;
  if (e.kind === "result")
    return (
      <span className={cls(e.ok === false ? "text-danger" : "text-muted")}>
        <CornerDownRight size={11} className="inline mr-1 -mt-0.5 opacity-60" /><Expandable text={e.text || ""} max={180} />
      </span>
    );
  return <ToolEvent e={e} />;
}

function ToolEvent({ e }: { e: FeedEvent }) {
  const Icon = toolIcon(e.icon);
  return (
    <div className="inline-block align-top w-full">
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        <Icon size={12} className="text-muted shrink-0" />
        <span className="font-semibold text-text/90">{e.title}</span>
        {e.subtitle && <span className="text-muted font-mono break-all" title={e.subtitle}>{e.subtitle}</span>}
        {e.diff && (e.diff.added > 0 || e.diff.removed > 0) && (
          <span className="font-mono text-[0.82em]">
            {e.diff.added > 0 && <span className="text-ok">+{e.diff.added}</span>}
            {e.diff.removed > 0 && <span className="text-danger ml-1">−{e.diff.removed}</span>}
          </span>
        )}
      </span>
      {e.command && (
        <pre className="mt-1 bg-panel2 border border-line rounded px-2 py-1 overflow-x-auto font-mono text-text/90 whitespace-pre-wrap break-words">
          <span className="text-ok select-none">$ </span><Linkify text={e.command} />
        </pre>
      )}
      {e.diff && e.diff.hunks && e.diff.hunks.length > 0 && (
        <DiffView hunks={e.diff.hunks} total={e.diff.added + e.diff.removed} />
      )}
      {e.todos && e.todos.length > 0 && <TodoView todos={e.todos} />}
    </div>
  );
}

function DiffView({ hunks, total }: { hunks: FeedDiffLine[]; total: number }) {
  return (
    <div className="mt-1 rounded border border-line overflow-hidden font-mono">
      {hunks.map((h, i) => (
        <div key={i} className={cls("px-2 whitespace-pre-wrap break-words", h.t === "add" ? "bg-ok/10 text-ok" : "bg-danger/10 text-danger")}>
          <span className="select-none opacity-50">{h.t === "add" ? "+" : "−"} </span>{h.s || " "}
        </div>
      ))}
      {total > hunks.length && <div className="px-2 py-0.5 text-muted/60 bg-panel2">… {total - hunks.length} more lines</div>}
    </div>
  );
}

function TodoView({ todos }: { todos: FeedTodo[] }) {
  const ic = (s: string) => (s === "completed" ? "✓" : s === "in_progress" ? "◐" : "○");
  const col = (s: string) => (s === "completed" ? "text-ok" : s === "in_progress" ? "text-brand" : "text-muted");
  return (
    <div className="mt-1 space-y-0.5">
      {todos.map((t, i) => (
        <div key={i} className={cls("flex gap-1.5", col(t.status))}>
          <span className="select-none shrink-0">{ic(t.status)}</span>
          <span className={cls(t.status === "completed" && "line-through opacity-70")}>{t.content}</span>
        </div>
      ))}
    </div>
  );
}
