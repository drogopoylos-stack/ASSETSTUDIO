import { useEffect, useRef } from "react";
import { Boxes, Image as ImageIcon, Loader2, Search, Sparkles, Triangle, X } from "lucide-react";
import { api } from "../../api/client";
import type { EngineGen } from "../../types";
import { cls } from "../ui";
import { EngineChip } from "./EngineChip";

// Everything made so far, newest first, on shelves a person can find things on: what kind of
// thing it is (a 3D asset, a scene, a picture, a review sheet), what it depicts (a character, a
// building, a prop…), which project, and a search. The backend answers every one of those over
// the whole history, so a chip counts everything ever made, not only the page that is loaded.
// Forge runs and review sheets sit on the same shelves because both are previous generations;
// only one of them can be replayed, and the card says which before it is clicked.

/** Kept for older callers; the strip now filters by type and subject. */
export type KindFilter = "" | "forge" | "review";
export type TypeFilter = "" | "asset" | "scene" | "picture" | "sheet";
/** Review sheets have no id; the sheet path is the one thing every item has. */
export const keyOf = (g: EngineGen) => g.id || g.sheet;

export const TYPE_LABEL: Record<Exclude<TypeFilter, "">, string> = {
  asset: "3D assets", scene: "Scenes", picture: "Pictures", sheet: "Review sheets",
};
export const SUBJECT_ORDER = ["character", "creature", "building", "prop", "vehicle", "environment",
  "weapon", "ui", "effect", "material", "test", "other"];

const fmtTris = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}k` : String(n);

function dayLabel(d: Date): string {
  const k = (x: Date) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (k(d) === k(today)) return "Today";
  if (k(d) === k(yesterday)) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function groupByDay(items: EngineGen[]): { key: string; label: string; items: EngineGen[] }[] {
  const out: { key: string; label: string; items: EngineGen[] }[] = [];
  const idx: Record<string, number> = {};
  for (const g of items) {
    const d = new Date((g.ts || 0) * 1000);
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    if (idx[key] === undefined) { idx[key] = out.length; out.push({ key, label: dayLabel(d), items: [] }); }
    out[idx[key]].items.push(g);
  }
  return out;
}

function Chip({ on, label, count, onClick, title }: { on: boolean; label: string; count?: number; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title}
      className={cls("px-2 py-0.5 rounded-full border transition-colors whitespace-nowrap",
        on ? "border-brand/50 bg-brand/10 text-brand" : "border-transparent text-muted hover:text-text")}>
      {label}{count != null && <span className="font-mono opacity-70"> {count}</span>}
    </button>
  );
}

interface Props {
  items: EngineGen[];
  total: number;
  selectedKey: string;
  onSelect: (g: EngineGen) => void;
  type: TypeFilter;
  onType: (t: TypeFilter) => void;
  typeCounts: Record<string, number>;
  subject: string;
  onSubject: (s: string) => void;
  subjectCounts: Record<string, number>;
  query: string;
  onQuery: (q: string) => void;
  projectName: string;
  onClearProject: () => void;
  isJustMade: (g: EngineGen) => boolean;
  canMore: boolean;
  onMore: () => void;
  loading: boolean;
}

export function HistoryStrip({ items, total, selectedKey, onSelect, type, onType, typeCounts, subject, onSubject,
                               subjectCounts, query, onQuery, projectName, onClearProject, isJustMade, canMore,
                               onMore, loading }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const groups = groupByDay(items);
  // A backend older than the shelves sends no counts; "all" then says what the list itself says.
  const allCount = Object.values(typeCounts).reduce((a, b) => a + b, 0) || total;
  const subjects = SUBJECT_ORDER.filter((s) => (subjectCounts[s] || 0) > 0 || s === subject);
  // Anything the classifier knows that the fixed order does not — a future subject — still shows.
  for (const s of Object.keys(subjectCounts)) if (!subjects.includes(s) && subjectCounts[s] > 0) subjects.push(s);

  // A followed generation is selected from outside the strip; bring it into view.
  useEffect(() => {
    if (!selectedKey || !scroller.current) return;
    const el = scroller.current.querySelector<HTMLElement>(`[data-k="${CSS.escape(selectedKey)}"]`);
    el?.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "smooth" });
  }, [selectedKey]);

  return (
    <div className="h-full flex flex-col">
      <div className="shrink-0 border-b border-line text-[11px]">
        <div className="h-8 flex items-center gap-2 px-3">
          <span className="text-[10px] uppercase tracking-wide text-muted">History</span>
          <div className="flex items-center gap-0.5 ml-1">
            <Chip on={type === ""} label="all" count={allCount} onClick={() => onType("")} />
            {(Object.keys(TYPE_LABEL) as Exclude<TypeFilter, "">[]).map((t) => (
              ((typeCounts[t] || 0) > 0 || type === t) &&
                <Chip key={t} on={type === t} label={TYPE_LABEL[t]} count={typeCounts[t] || 0} onClick={() => onType(type === t ? "" : t)}
                  title={t === "asset" ? "Code that builds one thing; it can be replayed and edited"
                       : t === "scene" ? "Code that builds a place: lights, fog, many things"
                       : t === "picture" ? "A forge run kept without its code — a picture only"
                       : "A contact sheet of a running game — a picture, not code"} />
            ))}
          </div>
          {projectName && (
            <button onClick={onClearProject} className="chip hover:text-text" title="Show every project">
              {projectName} <X size={10} />
            </button>
          )}
          <label className="ml-2 flex items-center gap-1 text-muted">
            <Search size={11} />
            <input value={query} onChange={(e) => onQuery(e.target.value)} placeholder="find by name, tag, project…"
              className="bg-transparent outline-none border-b border-transparent focus:border-line text-text placeholder:text-muted/60 w-44" />
            {query && <button onClick={() => onQuery("")} className="hover:text-text" title="Clear"><X size={10} /></button>}
          </label>
          <span className="ml-auto text-muted font-mono">
            {loading && !items.length ? <Loader2 size={11} className="animate-spin inline" /> : `${items.length} of ${total}`}
          </span>
          {canMore && <button onClick={onMore} className="text-muted hover:text-text">load more</button>}
        </div>
        {subjects.length > 0 && (
          <div className="h-7 flex items-center gap-0.5 px-3 overflow-x-auto">
            <span className="text-[10px] uppercase tracking-wide text-muted mr-1">Shows</span>
            <Chip on={subject === ""} label="anything" onClick={() => onSubject("")} />
            {subjects.map((s) => (
              <Chip key={s} on={subject === s} label={s} count={subjectCounts[s] || 0} onClick={() => onSubject(subject === s ? "" : s)} />
            ))}
          </div>
        )}
      </div>

      <div ref={scroller} className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden flex items-stretch gap-4 px-3 py-2"
        onWheel={(e) => {
          // A strip scrolls sideways; the wheel most people have only goes up and down.
          if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) e.currentTarget.scrollLeft += e.deltaY;
        }}>
        {!items.length && !loading && (
          <div className="self-center text-xs text-muted px-2">
            {total || type || subject || query ? "Nothing on this shelf. Clear a chip or the search."
              : "Nothing has been made yet. The first forge run an agent makes will appear here."}
          </div>
        )}
        {groups.map((grp) => (
          <div key={grp.key} className="flex flex-col shrink-0">
            <div className="text-[10px] text-muted mb-1 flex items-center gap-1.5">
              <span className="uppercase tracking-wide">{grp.label}</span>
              <span className="font-mono opacity-70">{grp.items.length}</span>
            </div>
            <div className="flex gap-2">
              {grp.items.map((g) => {
                const k = keyOf(g);
                const active = k === selectedKey;
                const fresh = isJustMade(g);
                const subj = g.subject && g.subject !== "other" ? g.subject : "";
                return (
                  <button key={k} data-k={k} onClick={() => onSelect(g)}
                    title={g.kind === "review" ? `${g.label}\nA review sheet — a picture, not code; it cannot be replayed.`
                           : !g.replayable ? `${g.label}\nRecorded without its code; it cannot be replayed.`
                           : `${g.label}\n${g.project_name}${subj ? `\n${subj}` : ""}${g.tags?.length ? `\n${g.tags.join(", ")}` : ""}`}
                    className={cls("group relative w-32 shrink-0 text-left rounded-lg border bg-panel2 overflow-hidden transition-colors",
                      active ? "border-brand ring-1 ring-brand/50" : "border-line hover:border-muted/60",
                      fresh && !active && "border-accent/60")}>
                    <div className="h-[4.5rem] bg-black/40 overflow-hidden flex items-center justify-center">
                      {g.sheet
                        ? <img src={api.engineSheetUrl(g.sheet)} loading="lazy" decoding="async" alt=""
                            className={cls("w-full h-full object-cover", !g.replayable && "opacity-75")} />
                        : g.replayable
                        ? <span className="flex flex-col items-center gap-0.5 text-muted">
                            <Boxes size={16} />
                            <span className="text-[9px] leading-3">measured</span>
                          </span>
                        : <ImageIcon size={16} className="text-muted" />}
                    </div>
                    {fresh && (
                      <span className="absolute top-1 left-1 inline-flex items-center gap-0.5 rounded-full bg-accent/90 text-white text-[9px] px-1.5 leading-4">
                        <Sparkles size={9} /> just made
                      </span>
                    )}
                    {!g.ok && <span className="absolute top-1.5 right-1.5 h-1.5 w-1.5 rounded-full bg-danger" title="the forge reported an error" />}
                    <div className="px-1.5 py-1">
                      <div className="truncate text-[11px] text-text">{g.label || g.id || "—"}</div>
                      <div className="flex items-center gap-1 text-[10px] text-muted mt-0.5 min-h-[1rem]">
                        {g.kind === "review"
                          ? <span className="inline-flex items-center rounded-full border border-line px-1.5 text-[9px] leading-4">sheet</span>
                          : <EngineChip kind={g.engine} size="xs" />}
                        {g.type === "scene" && <span className="inline-flex items-center rounded-full border border-line px-1.5 text-[9px] leading-4">scene</span>}
                        {subj && <span className="truncate inline-flex items-center rounded-full bg-panel px-1.5 text-[9px] leading-4 text-text/80" title={`shows: ${subj}`}>{subj}</span>}
                        {g.kind === "forge" && !g.replayable && <span className="text-muted/60">no code</span>}
                        {g.triangles != null && (
                          <span className="ml-auto font-mono inline-flex items-center gap-0.5" title={`${g.triangles.toLocaleString()} triangles`}>
                            <Triangle size={8} />{fmtTris(g.triangles)}
                          </span>
                        )}
                        {g.flat && <span className="ml-auto">flat</span>}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
