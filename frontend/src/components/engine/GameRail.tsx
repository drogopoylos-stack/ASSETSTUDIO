import { useState } from "react";
import { ChevronDown, ChevronRight, Layers } from "lucide-react";
import type { EngineProject } from "../../types";
import { cls } from "../ui";
import { EngineChip } from "./EngineChip";

// The games, as the backend ranks them: games first, then the ones with a tab open, then by name.
// The rest of the pinned workspaces are a click away, not gone — a folder becomes a game the
// moment an engine lands in its node_modules, and hiding it outright would make that invisible.

/** Windows paths arrive with either slash and either case; compare them as one thing. */
export const normPath = (s: string) => (s || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

interface Props {
  projects: EngineProject[];
  selected: string;
  onSelect: (path: string) => void;
  /** Generations per project, keyed by normPath(path). */
  counts: Record<string, number>;
  /** Projects with a live tab right now, by normPath — fresher than the cached project list. */
  openPaths: Set<string>;
  total: number;
  loading: boolean;
}

export function GameRail({ projects, selected, onSelect, counts, openPaths, total, loading }: Props) {
  const [showAll, setShowAll] = useState(false);
  const games = projects.filter((p) => p.game);
  const rest = projects.filter((p) => !p.game);
  const isOpen = (p: EngineProject) => p.open || openPaths.has(normPath(p.path)) || openPaths.has(normPath(p.root));
  const row = (p: EngineProject) => (
    <Row key={p.path} name={p.name} sub={p.sub ? `/${p.sub}` : ""} script={p.dev_script} engine={p.engine}
      open={isOpen(p)} count={counts[normPath(p.path)] || 0}
      active={!!selected && normPath(p.path) === normPath(selected)} onClick={() => onSelect(p.path)}
      title={p.root} />
  );
  return (
    <div className="flex flex-col py-1">
      <div className="px-3 py-1.5 text-[10px] uppercase tracking-wide text-muted flex items-center justify-between">
        <span>Games</span><span className="font-mono">{games.length}</span>
      </div>
      <div className="px-1.5 space-y-0.5">
        <button onClick={() => onSelect("")}
          className={cls("w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2 transition-colors hover:bg-panel2",
            !selected && "bg-panel2 ring-1 ring-line")}>
          <Layers size={13} className="text-muted shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="block text-[13px] truncate">All projects</span>
            <span className="block text-[10px] text-muted">{total} generation{total === 1 ? "" : "s"}</span>
          </span>
        </button>
        {games.map(row)}
        {!games.length && !loading && (
          <div className="px-2.5 py-3 text-xs text-muted leading-relaxed">
            No pinned workspace has a game in it — none has a three.js, PlayCanvas, Babylon, Phaser or Pixi
            build in node_modules, or a dev script.
          </div>
        )}
      </div>
      {rest.length > 0 && (
        <button onClick={() => setShowAll((v) => !v)}
          className="mt-2 mx-1.5 px-2.5 py-1.5 text-[11px] text-muted hover:text-text flex items-center gap-1 rounded-lg hover:bg-panel2">
          {showAll ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {showAll ? "hide the rest" : "show all"} · {rest.length} more
        </button>
      )}
      {showAll && <div className="px-1.5 space-y-0.5 opacity-75">{rest.map(row)}</div>}
    </div>
  );
}

function Row({ name, sub, script, engine, open, count, active, onClick, title }: {
  name: string; sub: string; script: string; engine: string; open: boolean; count: number;
  active: boolean; onClick: () => void; title: string;
}) {
  return (
    <button onClick={onClick} title={title}
      className={cls("w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2 transition-colors hover:bg-panel2",
        active && "bg-panel2 ring-1 ring-line")}>
      <span className="relative flex h-1.5 w-1.5 shrink-0" title={open ? "a live tab is open" : ""}>
        {open && <span className="absolute inline-flex h-full w-full rounded-full bg-ok opacity-70 animate-ping" />}
        <span className={cls("relative inline-flex h-1.5 w-1.5 rounded-full", open ? "bg-ok" : "bg-line")} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] truncate">{name}</span>
        <span className="block text-[10px] text-muted truncate font-mono">
          {sub}{sub && script ? " · " : ""}{script ? `npm run ${script}` : ""}{!sub && !script ? " " : ""}
        </span>
      </span>
      <span className="flex flex-col items-end gap-0.5 shrink-0">
        <EngineChip kind={engine} size="xs" />
        {count > 0 && <span className="text-[10px] font-mono text-muted">{count}</span>}
      </span>
    </button>
  );
}
