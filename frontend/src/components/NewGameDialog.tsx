import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, FolderPlus, Gamepad2, Loader2, X } from "lucide-react";
import { api, type NewGameDefaults, type NewGameEngine, type NewGameResult, type NewGameStatus } from "../api/client";
import { useStore } from "../store/useStore";
import { cls } from "./ui";
import type { WorkspaceRoot } from "../types";

// A NEW GAME, Studio-ready from the first minute.
//
// "New project" makes an empty folder, and the last game started from one grew sixty ad-hoc
// screenshots and test pages in its root: its agent spent its first hours building tools the Studio
// already has, because every one of them depends on a few lines the game has to carry. This makes
// the game from a template that carries them (window.__game, saved edits applied at start, review
// targets, builders with their feet on y = 0, a dev server with no dependencies), opens it as a
// workspace, and installs the engine in the background while you look at the files.
//
// ONE DIALOG, WHICHEVER ENTRY OPENED IT. The Workspaces panel closes itself as soon as you pick
// anything in it, and a dialog owned by a button inside that panel would close with it. So the
// rail's button hosts the dialog, and the panel's button only asks it to open.

type Host = (current: WorkspaceRoot | null) => void;
const hosts: Host[] = [];

/** Open the dialog from anywhere. False when nothing that can show it is mounted. */
export function openNewGame(current: WorkspaceRoot | null): boolean {
  const host = hosts[0];
  if (!host) return false;
  host(current);
  return true;
}

// THE INSTALL IS FOLLOWED OUTSIDE THE DIALOG. `npm install` takes 10 to 30 seconds and nobody
// should have to keep a dialog open to be told it finished, so the poll lives here, reports to
// whichever dialog is showing, and ends with a toast either way.
const watching = new Map<string, Set<(s: NewGameStatus) => void>>();

function watchInstall(path: string, title: string) {
  if (watching.has(path)) return;
  watching.set(path, new Set());
  let misses = 0;
  const tick = async () => {
    let s: NewGameStatus;
    try {
      s = await api.wsNewGameStatus(path);
      misses = 0;
    } catch {
      if (++misses > 20) { watching.delete(path); return; }
      window.setTimeout(tick, 2500);
      return;
    }
    watching.get(path)?.forEach((f) => f(s));
    if (s.installing) { window.setTimeout(tick, 1200); return; }
    watching.delete(path);
    const toast = useStore.getState().toast;
    if (s.ok) toast(`${title}: the engine is installed${s.seconds ? ` (${s.seconds}s)` : ""}. It is ready to run.`, "ok");
    else toast(`${title}: npm install ${s.timed_out ? "timed out" : "failed"}. The log is in .studio/install.log.`, "danger");
  };
  tick();
}

function useInstall(path: string | null): NewGameStatus | null {
  const [s, setS] = useState<NewGameStatus | null>(null);
  useEffect(() => {
    if (!path) return;
    const set = watching.get(path);
    if (!set) return;
    const f = (x: NewGameStatus) => setS(x);
    set.add(f);
    return () => { set.delete(f); };
  }, [path]);
  return s;
}

// The folder the backend will make, previewed from the same rule (game_scaffold.folder_of).
const titleOf = (name: string) => name.replace(/[\u0000-\u001f\u007f]+/g, " ").split(/\s+/).filter(Boolean).join(" ").slice(0, 64).trim();
const folderOf = (title: string) => title.replace(/[^\p{L}\p{N} ._\-()&]/gu, "").replace(/^[ .]+|[ .]+$/g, "");

export function NewGameDialog({ current, onClose, onCreated }: {
  current: WorkspaceRoot | null;
  onClose: () => void;
  onCreated: (root: WorkspaceRoot) => void;
}) {
  const toast = useStore((s) => s.toast);
  const [defs, setDefs] = useState<NewGameDefaults | null>(null);
  const [name, setName] = useState("");
  const [engine, setEngine] = useState<NewGameEngine>("three");
  const [parent, setParent] = useState("");
  const [install, setInstall] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [made, setMade] = useState<NewGameResult | null>(null);
  const touched = useRef({ engine: false, parent: false, install: false });
  const first = useRef<HTMLInputElement>(null);
  const status = useInstall(made && made.install.state === "running" ? made.path : null);

  useEffect(() => {
    first.current?.focus();
    // Settings first (new_game_parent / _engine / _install), else beside the project you are in.
    api.wsNewGameDefaults(current?.path || "").then((d) => {
      setDefs(d);
      if (!touched.current.parent) setParent(d.parent);
      if (!touched.current.engine) setEngine(d.engine);
      if (!touched.current.install) setInstall(d.install && d.npm.found);
    }).catch((e) => setErr(`could not read the defaults: ${e?.message || e}`));
  }, [current?.path]);

  const title = titleOf(name);
  const folder = folderOf(title);
  const sep = parent.includes("\\") ? "\\" : "/";
  const dest = parent && folder ? parent.replace(/[\\/]+$/, "") + sep + folder : "";
  const label = defs?.engines.find((e) => e.id === engine)?.label || (engine === "three" ? "three.js" : "PlayCanvas");
  const canGo = !!folder && /[\p{L}\p{N}]/u.test(title) && !!parent.trim() && !busy && !made;

  async function create() {
    if (!canGo) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.wsNewGame({ name: title, engine, parent: parent.trim(), install,
                                      open: true, beside: current?.path || "" });
      setMade(r);
      if (r.install.state === "running") watchInstall(r.path, r.title);
      // Land in it. Making a game and being left in the old project is a step you would then
      // take by hand every single time.
      if (r.root) onCreated(r.root);
      toast(`${r.title}: a ${label} game, open in the workspace` +
            (r.install.state === "running" ? ". The engine is installing." : "."), "ok");
      if (r.root_error) setErr(`Made, but not opened as a workspace: ${r.root_error}`);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  const lbl = "text-[11px] font-medium text-muted mb-1 block";
  const installing = made?.install.state === "running" && (!status || status.installing);
  return (
    <>
      <div className="fixed inset-0 z-[120] bg-black/60" onClick={onClose} />
      <div className="fixed inset-0 z-[121] flex items-center justify-center p-4 pointer-events-none">
        <div className="card w-[32rem] max-w-full p-4 shadow-card pointer-events-auto"
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.stopPropagation(); onClose(); }
            if (e.key === "Enter" && !made && (e.ctrlKey || e.metaKey || (e.target as HTMLElement).tagName === "INPUT")) {
              e.preventDefault();
              create();
            }
          }}>
          <div className="flex items-center gap-2 mb-1">
            <Gamepad2 size={15} className="text-brand shrink-0" />
            <span className="text-sm font-semibold">New game</span>
            <button className="ml-auto text-muted hover:text-text" onClick={onClose} title="Close"><X size={15} /></button>
          </div>

          {!made ? (
            <>
              <p className="text-[11px] text-muted leading-snug mb-3">
                A small playable game the Studio can already see: the live link, the forge, the
                visual review and saved edits work on it from the first minute.
              </p>

              <div className="mb-3">
                <label className={lbl}>Name</label>
                <input ref={first} className="input text-sm" value={name} placeholder="e.g. Crate Jumper"
                  onChange={(e) => setName(e.target.value)} />
              </div>

              <div className="mb-3">
                <label className={lbl}>Engine</label>
                <div className="flex items-center gap-1">
                  {(defs?.engines || [{ id: "three" as const, label: "three.js", dependencies: {}, handle: "" },
                                       { id: "playcanvas" as const, label: "PlayCanvas", dependencies: {}, handle: "" }]).map((e) => (
                    <button key={e.id}
                      onClick={() => { touched.current.engine = true; setEngine(e.id); }}
                      className={cls("px-2.5 py-1 rounded text-[11px] border",
                        engine === e.id ? "border-brand/60 bg-brand/10 text-text" : "border-line text-muted hover:text-text")}>
                      {e.label}
                    </button>
                  ))}
                  <span className="ml-2 text-[10px] font-mono text-muted/70 truncate">
                    {Object.entries(defs?.engines.find((x) => x.id === engine)?.dependencies || {})
                      .map(([k, v]) => `${k}@${v}`).join(" ")}
                  </span>
                </div>
              </div>

              <div className="mb-3">
                <label className={lbl}>Folder it goes in</label>
                <input className="input text-xs font-mono" value={parent}
                  onChange={(e) => { touched.current.parent = true; setParent(e.target.value); }} />
                <p className="text-[10px] text-muted/70 mt-1 break-all">
                  {dest ? <>Creates <span className="font-mono text-text/80">{dest}</span></>
                        : "Pick a name to see where it goes."}
                  {defs && !touched.current.parent && defs.parent_from === "beside" && " (beside the project you are in)"}
                  {defs && !touched.current.parent && defs.parent_from === "setting" && " (from Settings)"}
                </p>
              </div>

              <label className="flex items-start gap-2 mb-2 text-xs cursor-pointer select-none">
                <input type="checkbox" className="mt-0.5" checked={install} disabled={defs ? !defs.npm.found : false}
                  onChange={(e) => { touched.current.install = true; setInstall(e.target.checked); }} />
                <span>
                  Install the engine now <span className="text-muted">(npm install, in the background)</span>
                </span>
              </label>
              {defs && !defs.npm.found && (
                <p className="text-[11px] text-warn leading-snug mb-2 flex gap-1.5">
                  <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                  npm was not found on this PC. The game is made either way; install Node.js, then
                  run npm install in its folder.
                </p>
              )}
              {defs && !defs.runtime.ready && (
                <p className="text-[11px] text-muted leading-snug mb-2">
                  The Studio's runtime is not built yet, so the game gets a stand-in with the same
                  exports: it runs, and saved edits apply in the Studio's live tab, but not in the
                  game on its own until the stand-in is replaced.
                </p>
              )}

              {err && <div className="text-xs text-danger mb-3 leading-snug break-words">{err}</div>}

              <div className="flex items-center gap-2 mt-3">
                <p className="text-[10px] text-muted/60 flex-1 leading-snug">
                  It opens as a workspace of its own, with its own agent.
                </p>
                <button className="btn text-xs" onClick={onClose}>Cancel</button>
                <button className="btn-primary text-xs" disabled={!canGo} onClick={create}>
                  {busy ? <Loader2 size={13} className="animate-spin" /> : <FolderPlus size={13} />}
                  Create
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-start gap-2 mt-2 mb-3">
                <Check size={15} className="text-ok shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <div className="text-sm"><b>{made.title}</b> <span className="text-muted">· {label}</span></div>
                  <div className="text-[11px] font-mono text-muted break-all">{made.path}</div>
                  <div className="text-[11px] text-muted mt-0.5">
                    {made.root ? "Open in the workspace. " : ""}{made.files.length} files
                    {made.runtime.source === "stub" ? " · runtime: the stand-in" : " · runtime: copied from the Studio"}
                  </div>
                </div>
              </div>

              <div className="mb-3">
                <div className="flex items-center gap-1.5 text-xs mb-1">
                  {made.install.state === "skipped" && <span className="text-muted">Engine not installed: run npm install in the folder before playing.</span>}
                  {made.install.state === "failed" && (
                    <span className="text-danger flex items-center gap-1"><AlertTriangle size={12} /> {made.install.error || "npm install could not start"}</span>
                  )}
                  {made.install.state === "running" && installing && (
                    <span className="text-muted flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" />
                      Installing {label}{status?.seconds ? `… ${Math.round(status.seconds)}s` : "…"}</span>
                  )}
                  {made.install.state === "running" && status && !status.installing && (
                    status.ok
                      ? <span className="text-ok flex items-center gap-1"><Check size={12} /> Engine installed{status.seconds ? ` in ${status.seconds}s` : ""}. Ready to run.</span>
                      : <span className="text-danger flex items-center gap-1"><AlertTriangle size={12} /> npm install {status.timed_out ? "timed out" : `failed (exit ${status.code ?? "?"})`}</span>
                  )}
                </div>
                {made.install.state === "running" && status?.tail && (
                  <pre className="text-[10px] leading-snug font-mono bg-panel2 border border-line rounded p-2 max-h-28 overflow-auto whitespace-pre-wrap text-muted">
                    {status.tail}
                  </pre>
                )}
              </div>

              <ul className="text-[11px] text-muted leading-snug mb-3 list-disc pl-4 space-y-0.5">
                <li>Play it with the localhost button: it runs <span className="font-mono">npm run dev</span>.</li>
                <li>Its builders are in <span className="font-mono">src/assets.js</span>, and the Engine window's Library lists them.</li>
                <li>Moves saved in the Studio go to <span className="font-mono">studio.edits.json</span>, applied when the game starts.</li>
              </ul>

              {err && <div className="text-xs text-warn mb-3 leading-snug break-words">{err}</div>}

              <div className="flex justify-end">
                <button className="btn text-xs" onClick={onClose}>Close</button>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/** The New game entry: an icon on the rail (which hosts the dialog) or a row in the Workspaces
 *  panel (which asks the rail's to open, then lets the panel close). */
export function NewGameButton({ variant, current, onCreated, onOpen }: {
  variant: "rail" | "row";
  current: WorkspaceRoot | null;
  onCreated: (root: WorkspaceRoot) => void;
  onOpen?: () => void;
}) {
  const [openFor, setOpenFor] = useState<{ current: WorkspaceRoot | null } | null>(null);
  const created = useRef(onCreated);
  created.current = onCreated;
  const hosting = variant === "rail";
  useEffect(() => {
    if (!hosting) return;
    const host: Host = (c) => setOpenFor({ current: c });
    hosts.push(host);
    return () => {
      const i = hosts.indexOf(host);
      if (i >= 0) hosts.splice(i, 1);
    };
  }, [hosting]);
  const click = () => {
    if (!hosting && openNewGame(current)) { onOpen?.(); return; }
    setOpenFor({ current });
  };
  return (
    <>
      {variant === "rail" ? (
        <button className="p-2 rounded text-muted hover:text-text" onClick={click}
          title="New game: a three.js or PlayCanvas starter the Studio can already see (live link, forge, review, saved edits)">
          <Gamepad2 size={20} />
        </button>
      ) : (
        <button className="flex-1 flex items-center gap-1.5 px-2 py-1.5 rounded text-xs text-muted hover:bg-panel2 hover:text-text"
          onClick={click}>
          <Gamepad2 size={13} className="text-warn shrink-0" /> New game
        </button>
      )}
      {openFor && (
        <NewGameDialog current={openFor.current} onClose={() => setOpenFor(null)}
          onCreated={(r) => created.current(r)} />
      )}
    </>
  );
}
