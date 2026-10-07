import { useMemo } from "react";
import { Check, CornerDownRight, Folder, FolderOpen, GitBranch, Loader2 } from "lucide-react";
import type { WorkspaceRoot } from "../types";
import { cls } from "./ui";

// The workspace list, drawn as one tree.
//
// A repository is not one folder. It is a checkout, the worktrees branched off it, and any
// subfolder opened on its own so an agent reads less — and the rail used to list all of them
// flat, side by side, with nothing saying they were the same repository. Six rows, one project.
//
// Here the folder icon of a primary checkout is its own button. Click it and the rest of that
// repository opens underneath, indented against a guide line, each child saying which branch or
// which subfolder it is. Only a primary can open: a worktree IS a child, and a twisty on it
// would imply a level that does not exist.
//
// The same component draws the pinned strip and the Workspaces panel, so the two can never
// disagree about what belongs to what.

export interface TreeRow { r: WorkspaceRoot; kids: WorkspaceRoot[] }

/** Group roots into heads and their repository siblings, keeping the caller's order. */
export function buildTree(all: WorkspaceRoot[], shown?: WorkspaceRoot[]): TreeRow[] {
  const heads = shown || all;
  return heads.map((r) => ({
    r,
    kids: r.kind === "primary" && r.repo
      ? all
          .filter((x) => x.repo === r.repo && x.path !== r.path)
          .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name)
            : a.kind === "worktree" ? -1 : 1))
      : [],
  }));
}

export interface WorkspaceTreeProps {
  rows: TreeRow[];
  activePath?: string;
  /** which repositories are showing their children, keyed by git dir */
  open: Record<string, boolean>;
  onToggle: (key: string) => void;
  onSelect: (r: WorkspaceRoot) => void;
  onContext?: (r: WorkspaceRoot, x: number, y: number) => void;
  /** "working" | "done" | "idle" for a workspace id */
  statusOf: (id: string) => "working" | "done" | "idle";
  /** subagents in flight in that workspace */
  fanoutOf: (id: string) => number;
  /** of those, how many are quiet rather than writing - for the tooltip only */
  quietOf?: (id: string) => number;
  /** which engine is working there, and the colour it was given in Settings → Models */
  agentOf?: (id: string) => string;
  colourOf?: (id: string) => string;
  /** drag support — only the pinned strip reorders, so this is optional */
  drag?: {
    path: string | null;
    onStart: (r: WorkspaceRoot, e: React.DragEvent) => void;
    onDropRow: (fromPath: string, toPath: string) => void;
    onEnd: () => void;
  };
  compact?: boolean;
}

export function WorkspaceTree(p: WorkspaceTreeProps) {
  return (
    <div className={cls("space-y-0.5", p.compact && "text-[11px]")}>
      {p.rows.map(({ r, kids }) => {
        const openKids = kids.length > 0 && !!p.open[r.repo || r.path];
        return (
          <div key={r.path}>
            <Row p={p} x={r} kids={kids} openKids={openKids} child={false} />
            {/* One continuous guide line for the whole group, not a segment per row. Drawn on
                the container because `space-y` between rows breaks a per-row border into
                dashes — which reads as a dotted style choice rather than as a tree. The 14px
                offset puts it under the centre of the folder icon above it. */}
            {openKids && (
              <div className="ml-[14px] border-l border-line/70">
                {kids.map((kid) => (
                  <Row key={kid.path} p={p} x={kid} kids={[]} openKids={false} child />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Row({ p, x, kids, openKids, child }: {
  p: WorkspaceTreeProps; x: WorkspaceRoot; kids: WorkspaceRoot[]; openKids: boolean; child: boolean;
}) {
  const st = p.statusOf(x.id);
  const n = p.fanoutOf(x.id);
  const q = p.quietOf ? p.quietOf(x.id) : 0;
  const colour = p.colourOf ? p.colourOf(x.id) : "";
  const d = p.drag;
  return (
    <div role="button" tabIndex={0}
      onClick={() => p.onSelect(x)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); p.onSelect(x); } }}
      title={`${x.path}${x.branch ? `\non branch ${x.branch}` : ""}${d ? "\ndrag into a pane to open it there" : ""}`}
      draggable={!!d && !child}
      onDragStart={d && !child ? (e) => d.onStart(x, e) : undefined}
      /* A ROW being dragged is this row's business; anything else is not. Claiming every drag
         here is what made a folder from Windows unusable over the pinned strip: dragover said
         yes, the cursor said yes, and the drop handler - which only knows how to reorder - had
         no row to move and did nothing at all. Left unclaimed, the drag passes through to the
         panel or the explorer column behind, which knows what a folder means. */
      onDragOver={d?.path ? (e) => e.preventDefault() : undefined}
      onDrop={d?.path ? (e) => { e.preventDefault(); if (d.path !== x.path) d.onDropRow(d.path!, x.path); d.onEnd(); } : undefined}
      onDragEnd={d ? () => d.onEnd() : undefined}
      onContextMenu={p.onContext ? (e) => { e.preventDefault(); e.stopPropagation(); p.onContext!(x, e.clientX, e.clientY); } : undefined}
      className={cls("group w-full flex items-center gap-1.5 rounded text-xs text-left cursor-pointer",
        child ? "pl-2 pr-2 py-1" : "px-2 py-1",
        p.activePath === x.path ? "bg-brand/15 text-text" : "text-muted hover:bg-panel2 hover:text-text",
        d?.path === x.path && "opacity-50")}>
      {child ? (
        x.kind === "worktree"
          ? <GitBranch size={12} className="text-brand shrink-0" />
          : <CornerDownRight size={12} className="text-accent shrink-0" />
      ) : kids.length > 0 ? (
        <button className="p-0 -m-0.5 rounded shrink-0 text-warn hover:text-brand"
          title={openKids
            ? "Hide this repository's other folders"
            : `Show this repository's ${kids.length} other folder${kids.length > 1 ? "s" : ""} — worktrees and scoped folders`}
          onClick={(e) => { e.stopPropagation(); p.onToggle(x.repo || x.path); }}>
          {openKids ? <FolderOpen size={13} /> : <Folder size={13} />}
        </button>
      ) : <Folder size={13} className="text-warn shrink-0" />}

      <span className="truncate flex-1 min-w-0">{x.name}</span>

      {/* a worktree's whole point is which branch it is on; a scoped folder's is which folder */}
      {child && x.kind === "worktree" && x.branch && (
        <span className="shrink-0 text-[10px] font-mono text-brand/70 truncate max-w-[6rem]">{x.branch}</span>
      )}
      {child && x.kind === "subfolder" && x.rel && (
        <span className="shrink-0 text-[10px] font-mono text-muted/50 truncate max-w-[6rem]">./{x.rel}</span>
      )}
      {x.dirty && <span className="shrink-0 h-1.5 w-1.5 rounded-full bg-warn" title="uncommitted changes" />}

      {/* shut, the head still says how many folders it is hiding */}
      {!child && kids.length > 0 && !openKids && (
        <span className="shrink-0 text-[10px] font-mono text-muted/45 tabular-nums"
          title={`${kids.length} more folder${kids.length > 1 ? "s" : ""} in this repository`}>{kids.length}</span>
      )}

      {/* Subagents in flight, immediately left of the status dot. "Working" alone cannot tell one
          agent from a fan-out of nine, and those are the difference between a minute and an hour. */}
      {n > 0 && (
        <span className="shrink-0 text-[10px] font-mono tabular-nums px-1 rounded bg-brand/15 text-brand"
          title={q ? `${n} subagent${n > 1 ? "s" : ""} here: ${n - q} writing, ${q} quiet inside a long step`
                   : `${n} subagent${n > 1 ? "s" : ""} running here`}>{n}</span>
      )}
      {st === "working" ? (
        <span className="shrink-0 flex" title={`${(p.agentOf ? p.agentOf(x.id) : "") || "claude"} is working here`}>
          <Loader2 size={12} style={colour ? { color: colour } : undefined}
            className={cls("animate-spin", colour ? "" : "text-brand")} />
        </span>
      ) : st === "done" ? <Check size={12} className="text-ok shrink-0" />
        : <span className="w-1.5 h-1.5 rounded-full bg-muted/30 shrink-0" title="idle" />}
    </div>
  );
}

/** Convenience for the caller that only has the flat list. */
export function useWorkspaceTree(all: WorkspaceRoot[], shown?: WorkspaceRoot[]): TreeRow[] {
  return useMemo(() => buildTree(all, shown), [all, shown]);
}
