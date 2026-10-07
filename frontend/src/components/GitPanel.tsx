import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, GitBranch, Github, Loader2, RefreshCw, X } from "lucide-react";
import { api } from "../api/client";
import { cls } from "./ui";
import type { GitStatus } from "../types";

// Source control for one workspace or worktree: what changed, write it down, send it up.
//
// Deliberately four verbs and no more. Rebase, reset, force-push and anything that rewrites
// history stay out — those are the operations where a wrong click costs work, and they belong in
// a terminal where you can see exactly what you are agreeing to. The Studio has one of those.
//
// Nothing here happens on a poll. Every button is something you pressed, and push is on its own
// because it is the one that leaves the machine.
const STATE_COLOUR: Record<string, string> = {
  added: "text-ok", untracked: "text-ok",
  modified: "text-warn", changed: "text-warn", renamed: "text-warn", copied: "text-warn",
  deleted: "text-danger", conflict: "text-danger",
};
const STATE_MARK: Record<string, string> = {
  added: "A", untracked: "?", modified: "M", changed: "M",
  renamed: "R", copied: "C", deleted: "D", conflict: "!",
};

export function GitPanel({ path, name, onClose }: {
  path: string; name: string; onClose: () => void;
}) {
  const [st, setSt] = useState<GitStatus | null>(null);
  const [gh, setGh] = useState<Awaited<ReturnType<typeof api.gitGithub>> | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [repoName, setRepoName] = useState(name);
  const [priv, setPriv] = useState(true);

  const load = useCallback(() => {
    api.gitStatus(path).then(setSt).catch((e) => setErr(String(e?.message || e)));
  }, [path]);
  useEffect(() => { load(); api.gitGithub().then(setGh).catch(() => {}); }, [load]);

  async function run(kind: string, fn: () => Promise<{ ok: boolean; error?: string; message?: string }>) {
    setBusy(kind); setErr(""); setNote("");
    const r: { ok: boolean; error?: string; message?: string } =
      await fn().catch((e) => ({ ok: false, error: String(e?.message || e) }));
    setBusy("");
    if (!r.ok) { setErr(r.error || `${kind} failed`); return false; }
    setNote(r.message || `${kind} done`);
    load();
    return true;
  }

  const files = st?.files || [];
  const changed = (st?.staged || 0) + (st?.unstaged || 0) + (st?.untracked || 0);
  const label = "text-[11px] font-medium text-muted mb-1 block";

  return (
    <>
      <div className="fixed inset-0 z-[120] bg-black/60" onClick={onClose} />
      <div className="fixed inset-0 z-[121] flex items-center justify-center p-4 pointer-events-none">
        <div className="card w-[34rem] max-w-full max-h-[85vh] flex flex-col p-4 shadow-card pointer-events-auto"
          onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>

          <div className="flex items-center gap-2 mb-2 shrink-0">
            <GitBranch size={15} className="text-brand shrink-0" />
            <span className="text-sm font-semibold truncate">{name}</span>
            {st?.branch && <span className="font-mono text-[11px] text-muted truncate">{st.branch}</span>}
            <button className="ml-auto text-muted hover:text-text p-1" title="Refresh"
              onClick={load}><RefreshCw size={13} /></button>
            <button className="text-muted hover:text-text" onClick={onClose}><X size={15} /></button>
          </div>

          {!st ? <div className="text-xs text-muted py-6">reading the repository…</div>
            : !st.is_repo ? (
              <div className="text-xs text-muted py-6 leading-snug">
                {st.error || "This folder is not a git repository."}
                <div className="mt-1 text-muted/70">Run <span className="font-mono">git init</span> in it first.</div>
              </div>
            ) : (
              <>
                {/* where this branch stands against its remote */}
                <div className="flex items-center gap-3 text-[11px] mb-3 shrink-0 flex-wrap">
                  {st.upstream ? (
                    <>
                      <span className={cls("flex items-center gap-1", st.ahead ? "text-brand" : "text-muted/60")}
                        title={`${st.ahead} commit(s) here that the remote does not have`}>
                        <ArrowUp size={11} />{st.ahead}
                      </span>
                      <span className={cls("flex items-center gap-1", st.behind ? "text-warn" : "text-muted/60")}
                        title={`${st.behind} commit(s) on the remote that you do not have`}>
                        <ArrowDown size={11} />{st.behind}
                      </span>
                      <span className="text-muted/60 font-mono truncate">{st.upstream}</span>
                    </>
                  ) : (
                    <span className="text-muted/70">{st.remote ? "not published yet" : "no remote"}</span>
                  )}
                  {st.host && <span className="text-muted/50 ml-auto truncate">{st.host}</span>}
                </div>

                {st.unborn && (
                  <div className="text-[11px] text-muted mb-2 shrink-0">
                    No commits yet — this will be the first.
                  </div>
                )}
                {st.identity && !st.identity.ok && (
                  <div className="text-[11px] text-warn mb-2 shrink-0 leading-snug">
                    git does not know who you are yet, so it cannot record a commit. Set it once:
                    <div className="font-mono text-[10px] text-muted mt-1">
                      git config --global user.name "Your Name"<br />
                      git config --global user.email "you@example.com"
                    </div>
                  </div>
                )}
                {!!st.conflicts && (
                  <div className="text-[11px] text-danger mb-2 shrink-0">
                    {st.conflicts} file(s) have merge conflicts — resolve them before committing.
                  </div>
                )}

                {/* what changed */}
                <div className="flex-1 min-h-0 overflow-auto rounded border border-line/70 mb-3">
                  {st.clean ? (
                    <div className="text-xs text-muted p-3">
                      Nothing has changed since the last commit.
                      {st.last_commit && (
                        <div className="text-muted/70 mt-1">
                          Last: <span className="font-mono">{st.last_commit.hash}</span>{" "}
                          {st.last_commit.subject} · {st.last_commit.when}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="divide-y divide-line/40">
                      {files.map((f) => (
                        <div key={f.path} className="flex items-center gap-2 px-2 py-1 text-[11px]">
                          <span className={cls("font-mono w-3 shrink-0 text-center", STATE_COLOUR[f.state] || "text-muted")}
                            title={f.state}>{STATE_MARK[f.state] || "•"}</span>
                          <span className="font-mono truncate text-text/80" title={f.path}>{f.path}</span>
                          {f.staged && <span className="ml-auto text-[9px] text-ok/70 shrink-0">staged</span>}
                        </div>
                      ))}
                      {!!st.truncated && (
                        <div className="px-2 py-1 text-[10px] text-muted/70">…and {st.truncated} more</div>
                      )}
                    </div>
                  )}
                </div>

                {/* record it */}
                <div className="shrink-0">
                  <label className={label}>
                    Commit message
                    {changed > 0 && <span className="text-muted/60 font-normal"> · {changed} file{changed === 1 ? "" : "s"} will be included</span>}
                  </label>
                  <textarea className="input text-xs resize-none h-14" value={msg}
                    placeholder="what changed, and why"
                    onChange={(e) => setMsg(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && msg.trim()) {
                        e.preventDefault();
                        run("commit", () => api.gitCommit({ path, message: msg })).then((okd) => okd && setMsg(""));
                      }
                    }} />
                </div>

                {err && <div className="text-[11px] text-danger mt-2 whitespace-pre-wrap leading-snug shrink-0">{err}</div>}
                {note && !err && <div className="text-[11px] text-ok mt-2 whitespace-pre-wrap leading-snug shrink-0">{note}</div>}

                <div className="flex items-center gap-2 mt-3 shrink-0 flex-wrap">
                  {gh?.authenticated && (
                    <span className="text-[10px] text-muted/60 flex items-center gap-1 mr-auto" title="the account gh is signed in as">
                      <Github size={11} /> {gh.login}
                    </span>
                  )}
                  <button className="btn text-xs" disabled={!!busy || st.clean || !msg.trim()}
                    onClick={() => run("commit", () => api.gitCommit({ path, message: msg })).then((okd) => okd && setMsg(""))}>
                    {busy === "commit" ? <Loader2 size={13} className="animate-spin" /> : null}
                    Commit
                  </button>
                  <button className="btn text-xs" disabled={!!busy || !st.upstream || !!st.behind === false && false}
                    title={st.upstream ? "Fast-forward only, and only with a clean tree" : "Push it first"}
                    onClick={() => run("pull", () => api.gitPull(path))}>
                    {busy === "pull" ? <Loader2 size={13} className="animate-spin" /> : <ArrowDown size={13} />}
                    Pull{st.behind ? ` (${st.behind})` : ""}
                  </button>
                  <button className="btn-primary text-xs" disabled={!!busy || !st.remote}
                    title={st.remote ? "Send this branch to its remote — never forced" : "There is no remote to push to yet"}
                    onClick={() => run("push", () => api.gitPush(path))}>
                    {busy === "push" ? <Loader2 size={13} className="animate-spin" /> : <ArrowUp size={13} />}
                    Push{st.ahead ? ` (${st.ahead})` : ""}
                  </button>
                </div>

                {/* nowhere to push yet */}
                {!st.remote && (
                  <div className="mt-3 pt-3 border-t border-line shrink-0">
                    {!gh?.authenticated ? (
                      <div className="text-[11px] text-muted leading-snug">
                        {gh?.installed
                          ? "The GitHub CLI is installed but not signed in. Run `gh auth login` once."
                          : "Install the GitHub CLI (gh) to create a repository from here."}
                      </div>
                    ) : (
                      <>
                        <label className={label}>Create it on GitHub</label>
                        <div className="flex items-center gap-2">
                          <input className="input text-xs font-mono flex-1" value={repoName}
                            onChange={(e) => setRepoName(e.target.value)} />
                          <label className="flex items-center gap-1 text-[11px] text-muted cursor-pointer shrink-0">
                            <input type="checkbox" checked={priv} onChange={(e) => setPriv(e.target.checked)} /> private
                          </label>
                          <button className="btn text-xs shrink-0" disabled={!!busy || !repoName.trim()}
                            onClick={() => run("create", () => api.gitCreateRepo({
                              path, name: repoName.trim(), private: priv, push: false }))}>
                            {busy === "create" ? <Loader2 size={13} className="animate-spin" /> : <Github size={13} />}
                            Create
                          </button>
                        </div>
                        <p className="text-[10px] text-muted/60 mt-1 leading-snug">
                          Creates the repository and wires it up as <span className="font-mono">origin</span>.
                          It does not push — that stays a separate press.
                        </p>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
        </div>
      </div>
    </>
  );
}
