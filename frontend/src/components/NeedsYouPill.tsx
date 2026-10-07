import { useEffect, useRef, useState } from "react";
import { Loader2, MessageCircleQuestion } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import { worthShowing } from "./attention";
import { cls, pollWhileVisible, timeAgo } from "./ui";

// WHO NEEDS YOU — in the bottom bar, beside the CPU and the browser, and gone when there is
// nothing to say.
//
// Running five agents at once, the thing that costs time is not any one of them: it is that a
// session which stopped to ask a question is invisible until you happen to click on it. The rail
// shows a dot per project and you have to be looking at the right dot.
//
// IT WAS A LINE AT THE TOP FIRST, AND THAT WAS WRONG. A strip that is always on screen stops
// being read, and most of the time it had no news — a row of chrome reporting that there was
// nothing to report. So it moved here, where the user already chooses what is worth a permanent
// place (the gear in this bar), and it draws NOTHING while every project is quiet. The pill
// appearing IS the signal.
//
// Two switches, and they mean different things. The bar's own item list decides whether this is
// one of the readings you want on screen; Settings → Studio engine → "Who needs you" decides
// whether the feature exists at all, and turning it off stops the poll as well.

type Attention = Awaited<ReturnType<typeof api.missionAttention>>;
type Row = Attention["rows"][number];

const POLL_MS = 6000;
// Switched off, the endpoint answers a constant without touching anything, so this costs nothing
// — and asking once a minute is what brings the pill BACK on its own after the switch is turned
// on again, rather than needing a reload.
const OFF_POLL_MS = 60000;

export function NeedsYouPill({ compact }: { compact?: boolean }) {
  const [data, setData] = useState<Attention | null>(null);
  const [open, setOpen] = useState(false);
  // An older backend has no such endpoint. Say nothing and stop asking, rather than drawing an
  // error over a Studio that is working perfectly well.
  const [gone, setGone] = useState(false);
  const goneRef = useRef(false);
  const off = !!data?.off;
  const openInWorkspace = useStore((s) => s.openInWorkspace);

  useEffect(() => {
    if (gone) return;
    return pollWhileVisible(async () => {
      if (goneRef.current) return;
      try {
        setData(await api.missionAttention());
      } catch (e: any) {
        if (/404/.test(e?.message || "")) { goneRef.current = true; setGone(true); }
      }
    }, off ? OFF_POLL_MS : POLL_MS);
  }, [gone, off]);

  const rows = data?.rows || [];
  if (gone || off || !worthShowing(rows)) return null;

  const blocked = rows.filter((r) => r.state === "blocked");
  const working = rows.filter((r) => r.state === "working");
  const c = data?.counts || { blocked: 0, working: 0, idle: 0, agents: 0 };
  const n = blocked.length || working.length;
  const word = blocked.length ? (blocked.length === 1 ? "needs you" : "need you") : "working";

  return (
    <span className="relative flex items-center">
      <button onClick={() => setOpen((v) => !v)}
        title={`${blocked.length} waiting on you, ${working.length} working, ${c.idle} quiet`
               + " · click for the list · turn it off with the gear in this bar"}
        className={cls("flex items-center gap-1 rounded px-1 hover:bg-panel2",
          blocked.length ? "text-warn" : "text-brand", open && "bg-panel2")}>
        {blocked.length
          ? <MessageCircleQuestion size={compact ? 12 : 14} />
          : <Loader2 size={compact ? 12 : 14} className="animate-spin" />}
        <span className="tabular-nums font-semibold">{n}</span>
        {!compact && <span className="font-normal">{word}</span>}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-[80]" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 mb-2 z-[81] card p-2 w-80 shadow-card text-xs font-sans">
            <div className="text-[10px] uppercase tracking-wide text-muted px-1 pb-1">
              Across every project
            </div>
            {[...blocked, ...working].map((r) => (
              <Line key={r.path} row={r}
                onGo={() => { setOpen(false); openInWorkspace(r.path); }} />
            ))}
            <div className="mt-1.5 pt-1.5 border-t border-line px-1 text-[10px] text-muted/70 flex gap-2">
              <span className="tabular-nums">{c.idle} quiet</span>
              {c.agents > 0 && <span className="tabular-nums">{c.agents} subagent{c.agents === 1 ? "" : "s"}</span>}
              <span className="ml-auto">Settings → Studio engine → Who needs you</span>
            </div>
          </div>
        </>
      )}
    </span>
  );
}

function Line({ row, onGo }: { row: Row; onGo: () => void }) {
  const blocked = row.state === "blocked";
  return (
    <button onClick={onGo}
      className="w-full text-left px-1 py-1 rounded hover:bg-panel2 flex items-start gap-2">
      <span className={cls("mt-1.5 h-1.5 w-1.5 rounded-full shrink-0",
        blocked ? "bg-warn" : "bg-brand animate-pulse")} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className={cls("truncate", blocked ? "text-warn" : "text-text/90")}>{row.name || row.path}</span>
          {row.agents > 0 && <span className="text-muted/60 tabular-nums shrink-0">+{row.agents}</span>}
          <span className="ml-auto text-muted/60 shrink-0 text-[10px]">
            {timeAgo(Date.now() / 1000 - row.since)}
          </span>
        </span>
        {(row.why || row.todo) && (
          <span className="block text-[11px] text-muted truncate">{row.why || row.todo}</span>
        )}
      </span>
    </button>
  );
}
