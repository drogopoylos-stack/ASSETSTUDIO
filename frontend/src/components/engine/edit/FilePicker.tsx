// Choosing which asset to edit.
//
// A recorded run is what the Engine window already has, and it opens with one click — but it has
// no file, so nothing it changes can be saved. Opening the SOURCE is the path that round-trips,
// so it is offered beside the run rather than buried.

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, FileCode, Loader2, Search } from "lucide-react";
import { api } from "../../../api/client";
import { cls } from "../../ui";

const CODE = /\.(m?[jt]s)$/i;
const NOISE = /(^|\/)(node_modules|dist|build|\.git|coverage|__pycache__)(\/|$)/i;
/** Files that are almost never an asset, ranked down rather than hidden — a project may well
 *  keep its dinosaur in main.js, and refusing to show it would be worse than a bad order. */
const DULL = /(^|\/)(vite|rollup|webpack|eslint|tailwind|postcss|jest|vitest)\.config\.|\.d\.ts$|\.test\.|\.spec\./i;
const LIKELY = /(asset|model|mesh|char|creature|prop|weapon|build|make|forge|shape|geo)/i;

export interface Picked { path: string; code: string }

export function FilePicker({ project, current, onPick, disabled }:
  { project: string; current: string; onPick(p: Picked | null): void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [files, setFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [open]);

  const load = useCallback(async () => {
    if (!project) { setFiles([]); setErr("Pick a project in the rail first."); return; }
    setLoading(true);
    setErr("");
    try {
      // Two passes so both spellings are covered without asking for the whole tree: the search
      // matches on name, and an asset is as likely to be called `dino.js` as `assets.js`.
      const seen = new Set<string>();
      for (const term of [".js", ".ts"]) {
        const r = await api.wsSearch(project, term, 200);
        for (const e of r.entries || []) {
          const p = e.path || "";
          if (!CODE.test(p) || NOISE.test(p.replace(/\\/g, "/"))) continue;
          seen.add(p);
        }
      }
      const list = [...seen].sort((a, b) => rank(b) - rank(a) || a.length - b.length || a.localeCompare(b));
      setFiles(list);
      if (!list.length) setErr("No JavaScript or TypeScript files found in this project.");
    } catch (e: any) {
      setErr(String(e?.message || e).slice(0, 160));
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => { if (open) load(); }, [open, load]);

  const choose = async (path: string) => {
    setBusy(path);
    try {
      const f = await api.wsFile(path);
      if (f.kind !== "text" || typeof f.text !== "string") throw new Error("that file is not text");
      onPick({ path, code: f.text });
      setOpen(false);
    } catch (e: any) {
      setErr(String(e?.message || e).slice(0, 160));
    } finally {
      setBusy("");
    }
  };

  const shown = q
    ? files.filter((f) => f.toLowerCase().includes(q.toLowerCase()))
    : files.slice(0, 300);
  const name = current ? current.replace(/\\/g, "/").split("/").pop() : "";

  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen((v) => !v)} disabled={disabled}
        title={current || "Open an asset source file — the only way an edit can be saved"}
        className={cls("chip flex items-center gap-1 max-w-[15rem]",
          current && "text-ok border-ok/40 bg-ok/10", disabled && "opacity-50")}>
        <FileCode size={11} className="shrink-0" />
        <span className="truncate">{name || "open file"}</span>
        <ChevronDown size={10} className="opacity-60 shrink-0" />
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-1 z-40 w-[26rem] rounded-lg border border-line bg-panel shadow-xl">
          <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-line">
            <Search size={12} className="text-muted shrink-0" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter by path"
              className="flex-1 h-6 px-1 bg-transparent text-[11px] outline-none" />
            {loading && <Loader2 size={12} className="animate-spin text-muted" />}
            <span className="text-[10px] text-muted tabular-nums">{shown.length}</span>
          </div>
          {err && <div className="px-2.5 py-2 text-[11px] text-warn">{err}</div>}
          <div className="max-h-80 overflow-y-auto py-0.5">
            {current && (
              <button onClick={() => { onPick(null); setOpen(false); }}
                className="w-full text-left px-2.5 py-1 text-[11px] text-muted hover:bg-panel2 hover:text-text">
                ← back to the last recorded run
              </button>
            )}
            {shown.map((f) => (
              <button key={f} onClick={() => choose(f)}
                className={cls("w-full text-left px-2.5 py-1 text-[11px] hover:bg-panel2 flex items-center gap-1.5",
                  f === current ? "text-ok" : "text-muted hover:text-text")}>
                {busy === f ? <Loader2 size={10} className="animate-spin shrink-0" /> : <FileCode size={10} className="shrink-0 opacity-50" />}
                <span className="truncate font-mono">{short(f, project)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function rank(p: string): number {
  let n = 0;
  if (LIKELY.test(p)) n += 3;
  if (DULL.test(p)) n -= 5;
  if (/(^|[\\/])src[\\/]/i.test(p)) n += 1;
  return n;
}

function short(p: string, root: string): string {
  const a = p.replace(/\\/g, "/");
  const b = (root || "").replace(/\\/g, "/").replace(/\/+$/, "");
  return b && a.toLowerCase().startsWith(b.toLowerCase() + "/") ? a.slice(b.length + 1) : a;
}
