import { useEffect, useRef, useState } from "react";
import { MoreHorizontal, Sparkles, Square } from "lucide-react";
import { api } from "../api/client";
import { activeJobs, TabId, useStore } from "../store/useStore";
import { cls, pollWhileVisible } from "./ui";



// The plan-usage meter that used to sit here has moved into UsageLimitsBar, on the left, beside
// the 5h and weekly bars it belongs with.
//
// It was meant to be the FABLE meter. It was not one: it took whichever bucket was fullest, so
// with the session at 9% and the Fable allowance at 0% it read "5h 9%" — a meter named after one
// thing showing another, alone in the opposite corner from the two meters that answer the same
// question. UsageLimitsBar now draws every per-model bucket the account reports, Fable included,
// in the same row and the same shape as the rest.

// Top buttons are text-only (no icons) with short labels; the full name shows on hover.
const ALL_TABS: { id: TabId; label: string; short?: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "mission", label: "Mission Control", short: "Control" },
  { id: "workspace", label: "Workspace" },
  { id: "workflows", label: "Workflows" },
  { id: "chat", label: "Ask AI" },
  { id: "plans", label: "Plans" },
  { id: "image", label: "Image" },
  { id: "video", label: "Video" },
  { id: "studio2d", label: "2D Studio", short: "2D" },
  { id: "studio3d", label: "3D Studio", short: "3D" },
  { id: "texture", label: "Texture" },
  { id: "rig", label: "Rig & Animate", short: "Rig&A" },
  { id: "pipeline", label: "Pipeline" },
  { id: "catalog", label: "Catalog" },
  { id: "jobs", label: "Jobs" },
  { id: "compare", label: "Compare" },
  { id: "servers", label: "Servers" },
  { id: "settings", label: "Settings" },
];

export default function TopNav() {
  const { tab, setTab, jobs, missionFont, setMissionFont, wsFont, setWsFont } = useStore();
  const enabledTabs = useStore((s) => s.enabledTabs);
  // Settings → Plugins decides what's here. Before the list has loaded (null) show
  // everything, so the bar never flashes half-empty on launch.
  const TABS = enabledTabs ? ALL_TABS.filter((t) => enabledTabs.includes(t.id)) : ALL_TABS;
  const running = activeJobs(jobs).length;
  // feed text-size stepper: shows on Mission Control + Workspace; each has its own size
  const showFont = tab === "mission" || tab === "workspace";
  const fontVal = tab === "workspace" ? wsFont : missionFont;
  const setFont = tab === "workspace" ? setWsFont : setMissionFont;

  // overflow → a "more" menu appears when the tabs can't all fit (narrow window)
  const scrollRef = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const check = () => setOverflow(el.scrollWidth > el.clientWidth + 4);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    window.addEventListener("resize", check);
    return () => { ro.disconnect(); window.removeEventListener("resize", check); };
  }, []);

  async function stopAll() {
    const active = activeJobs(useStore.getState().jobs);
    if (!active.length) return;
    useStore.getState().toast(`Stopping ${active.length} job${active.length > 1 ? "s" : ""}…`, "warn");
    await Promise.all(active.map((j) => api.cancelJob(j.id).catch(() => {})));
  }

  return (
    <div className="h-11 shrink-0 border-b border-line bg-panel flex items-center px-2 gap-1">
      <Sparkles size={18} className="text-brand shrink-0 mx-1" />

      <div ref={scrollRef} className="flex items-center gap-1 overflow-x-auto flex-1 no-scrollbar">
        {TABS.map((t) => {
          const active = tab === t.id;
          return (
            <button key={t.id} className={cls("tab inline-flex items-center gap-1", active && "tab-active")}
              onClick={() => setTab(t.id)} title={t.label}>
              {t.short || t.label}
              {t.id === "jobs" && running > 0 && (
                <span className="text-[10px] bg-brand-600 text-white rounded-full px-1.5">{running}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* overflow menu — every tab in a dropdown when the bar is too narrow to show them all */}
      {overflow && (
        <div className="relative shrink-0">
          <button className="tab inline-flex items-center !px-2" title="All tabs" onClick={() => setMoreOpen((v) => !v)}>
            <MoreHorizontal size={16} />
          </button>
          {moreOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setMoreOpen(false)} />
              <div className="absolute top-full right-0 mt-1 z-50 card p-1 w-52 max-h-[70vh] overflow-auto shadow-card">
                {TABS.map((t) => (
                  <button key={t.id}
                    className={cls("w-full text-left px-2 py-1.5 rounded text-sm flex items-center gap-2 hover:bg-panel2",
                      tab === t.id && "bg-panel2 text-text")}
                    onClick={() => { setTab(t.id); setMoreOpen(false); }}>
                    {t.label}
                    {t.id === "jobs" && running > 0 && (
                      <span className="ml-auto text-[10px] bg-brand-600 text-white rounded-full px-1.5">{running}</span>
                    )}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Claude usage-limit meter — the binding /usage bucket; hides at 100% until reset */}

      {/* feed text-size stepper — Mission Control + Workspace (each keeps its own size) */}
      {showFont && (
        <div className="shrink-0 ml-1 flex items-center gap-0.5 px-1 py-0.5 rounded-lg border border-line bg-panel2"
          title={tab === "workspace" ? "Workspace Claude feed text size" : "Mission Control feed text size"}>
          <button className="w-5 h-6 rounded hover:bg-line text-muted hover:text-text leading-none"
            onClick={() => setFont(fontVal - 1)} title="Smaller">−</button>
          <span className="text-[11px] text-muted font-mono w-4 text-center select-none">{fontVal}</span>
          <button className="w-5 h-6 rounded hover:bg-line text-muted hover:text-text leading-none"
            onClick={() => setFont(fontVal + 1)} title="Larger">+</button>
        </div>
      )}

      {/* global stop — auto-appears only while something is running (keeps the bar clean otherwise).
          Theme picker moved to Settings → Appearance for space. */}
      {running > 0 && (
        <button
          className="shrink-0 ml-1 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[13px] font-medium border bg-danger/15 border-danger/40 text-danger hover:bg-danger/25 transition-colors"
          onClick={stopAll} title="Stop all running generations">
          <Square size={13} className="fill-danger" /> Stop ({running})
        </button>
      )}
    </div>
  );
}
