import { useEffect, useRef, useState } from "react";
import { GitBranch, FolderPlus, Loader2, X } from "lucide-react";
import { api } from "../api/client";
import { cls } from "./ui";
import type { WorkspaceRoot } from "../types";

// Create a worktree: another branch of the same repository, in its own folder, with its own
// agent.
//
// Two things worth knowing, because they are what make this safe rather than clever:
//   * git allows one worktree per branch. That single rule is what stops two agents fighting
//     over the same branch, so the dialog shows which branches are already taken instead of
//     letting you pick one and fail.
//   * a worktree is just a folder, and a folder is a workspace here — so the new checkout
//     arrives with its own session, transcript, model and agent picker, automatically.
//
// The destination is shown before you commit to it, because you will go looking for it in
// Explorer and a folder you cannot find is a folder you cannot trust.
export function WorktreeDialog({ project, onClose, onCreated }: {
  project: WorkspaceRoot;
  onClose: () => void;
  onCreated: (path: string, name: string) => void;
}) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [branch, setBranch] = useState("");
  const [base, setBase] = useState("");
  const [dest, setDest] = useState("");
  const [destTouched, setDestTouched] = useState(false);
  const [branches, setBranches] = useState<string[]>([]);
  const [taken, setTaken] = useState<string[]>([]);
  const [current, setCurrent] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    first.current?.focus();
    api.wtBranches(project.path).then((b) => {
      setBranches(b.branches || []);
      setTaken(b.taken || []);
      setCurrent(b.current || "");
      setBase((v) => v || b.current || "");
    }).catch(() => {});
  }, [project.path]);

  // Follow the name until the moment you edit the path yourself, then stop — nothing is more
  // irritating than a field that keeps overwriting what you just typed.
  useEffect(() => {
    if (destTouched) return;
    const t = window.setTimeout(() => {
      api.wtSuggest(project.path, name).then((r) => setDest(r.dest || "")).catch(() => {});
    }, 120);
    return () => window.clearTimeout(t);
  }, [name, destTouched, project.path]);

  const slug = (s: string) => s.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "");
  const effBranch = mode === "existing" ? branch : (branch.trim() || slug(name));
  const clash = mode === "existing" && taken.includes(branch);
  const canGo = !!name.trim() && !!effBranch && !clash && !busy;

  async function create() {
    if (!canGo) return;
    setBusy(true); setErr("");
    const r = await api.wtCreate({
      path: project.path, name: name.trim(), branch: effBranch,
      base: mode === "new" ? base : "", dest: dest.trim(),
      existing_branch: mode === "existing",
    }).catch((e) => ({ ok: false, error: String(e?.message || e) } as any));
    setBusy(false);
    if (!r.ok) { setErr(r.error || "could not create the worktree"); return; }
    if (r.root_error) { setErr(r.root_error); return; }   // it EXISTS — do not imply it failed
    onCreated(r.path || "", r.name || name.trim());
  }

  const label = "text-[11px] font-medium text-muted mb-1 block";
  return (
    <>
      <div className="fixed inset-0 z-[120] bg-black/60" onClick={onClose} />
      <div className="fixed inset-0 z-[121] flex items-center justify-center p-4 pointer-events-none">
        <div className="card w-[30rem] max-w-full p-4 shadow-card pointer-events-auto"
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.stopPropagation(); onClose(); }
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); create(); }
          }}>
          <div className="flex items-center gap-2 mb-1">
            <GitBranch size={15} className="text-brand shrink-0" />
            <span className="text-sm font-semibold">New worktree</span>
            <button className="ml-auto text-muted hover:text-text" onClick={onClose}><X size={15} /></button>
          </div>
          <p className="text-[11px] text-muted leading-snug mb-3">
            Another branch of <b className="text-text/80">{project.name}</b>, in its own folder,
            with its own agent. Both stay open at once and neither can touch the other's files.
          </p>

          <div className="mb-3">
            <label className={label}>Name</label>
            <input ref={first} className="input text-sm" value={name} placeholder="e.g. cli look"
              onChange={(e) => setName(e.target.value)} />
          </div>

          <div className="mb-3">
            <div className="flex items-center gap-1 mb-1.5">
              <button onClick={() => setMode("new")}
                className={cls("px-2 py-1 rounded text-[11px] border",
                  mode === "new" ? "border-brand/60 bg-brand/10 text-text" : "border-line text-muted hover:text-text")}>
                New branch
              </button>
              <button onClick={() => setMode("existing")}
                className={cls("px-2 py-1 rounded text-[11px] border",
                  mode === "existing" ? "border-brand/60 bg-brand/10 text-text" : "border-line text-muted hover:text-text")}>
                Existing branch
              </button>
            </div>
            {mode === "new" ? (
              <div className="flex gap-2">
                <div className="flex-1 min-w-0">
                  <label className={label}>Branch to create</label>
                  <input className="input text-sm font-mono" value={branch} placeholder={slug(name) || "branch"}
                    onChange={(e) => setBranch(e.target.value)} />
                </div>
                <div className="w-40 shrink-0">
                  <label className={label}>From</label>
                  <select className="input text-sm" value={base} onChange={(e) => setBase(e.target.value)}>
                    {!branches.length && <option value="">{current || "HEAD"}</option>}
                    {branches.map((b) => <option key={b} value={b}>{b}</option>)}
                  </select>
                </div>
              </div>
            ) : (
              <div>
                <label className={label}>Branch to check out</label>
                <select className="input text-sm font-mono" value={branch} onChange={(e) => setBranch(e.target.value)}>
                  <option value="">select a branch…</option>
                  {branches.map((b) => (
                    <option key={b} value={b} disabled={taken.includes(b)}>
                      {b}{taken.includes(b) ? "  — already open elsewhere" : ""}
                    </option>
                  ))}
                </select>
                {clash && (
                  <p className="text-[11px] text-warn mt-1 leading-snug">
                    Git allows one worktree per branch, and this one is already checked out.
                    That rule is what keeps two agents off the same files.
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="mb-3">
            <label className={label}>Folder</label>
            <input className="input text-xs font-mono" value={dest}
              onChange={(e) => { setDest(e.target.value); setDestTouched(true); }} />
            <p className="text-[10px] text-muted/70 mt-1">
              Sits beside the project by default, so it is where you would look for it.
            </p>
          </div>

          {err && <div className="text-xs text-danger mb-3 leading-snug">{err}</div>}

          <div className="flex items-center gap-2">
            <p className="text-[10px] text-muted/60 flex-1 leading-snug">
              A fresh worktree has no <span className="font-mono">node_modules</span> or
              <span className="font-mono"> .venv</span> — those are ignored by git. Fine for
              editing; install them there before you build.
            </p>
            <button className="btn text-xs" onClick={onClose}>Cancel</button>
            <button className="btn-primary text-xs" disabled={!canGo} onClick={create}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <FolderPlus size={13} />}
              Create
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
