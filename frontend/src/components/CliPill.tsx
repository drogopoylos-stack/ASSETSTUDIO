import { useCallback, useEffect, useState } from "react";
import { ArrowUpCircle, Loader2, Terminal, X } from "lucide-react";
import { api } from "../api/client";
import { cls } from "./ui";

// The status pill for the `claude` binary the Studio actually runs.
//
// This exists because of a fault that is invisible by construction. The VSCode extension once
// held 2.1.233 while the installed CLI was already 2.1.250 — seventeen versions behind, for
// weeks — and nothing said so, because `claude --version` in a terminal reports whatever is
// first on PATH, not the copy the Studio resolved. A number on the bar is the whole fix.
//
// It also names the CHANNEL, because the obvious update command can go backwards: on the day
// `next` was 2.1.257, `latest` was still 2.1.252, so `claude install latest --force` would have
// downgraded by five versions. The button therefore installs an exact version, not a tag.

type Info = Awaited<ReturnType<typeof api.claudeVersion>>;

export function CliPill({ compact }: { compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [d, setD] = useState<Info | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try { setD(await api.claudeVersion()); setErr(""); }
    catch (e: any) {
      // Newer than the running backend until it is restarted. Say that, rather than showing
      // nothing and looking like the CLI is missing.
      setErr(/404/.test(e?.message || "")
        ? "Restart the backend to use this — the endpoint is newer than the running process."
        : e?.message || "could not read the CLI version");
    }
  }, []);

  useEffect(() => {
    load();
    const iv = window.setInterval(() => { if (!document.hidden) load(); }, 60000);
    return () => window.clearInterval(iv);
  }, [load]);

  // Nothing known and nothing to say — stay out of the bar rather than showing a blank chip.
  if (!d?.version && !err && !open) return null;

  const upd = !!d?.update_available;
  const busySess = d?.busy || [];

  async function install() {
    setBusy(true); setMsg(""); setErr("");
    try {
      const r = await api.claudeInstall(d?.newest || "");
      setMsg(r.ok ? `Now on ${r.version}. New sessions start on it; anything already running keeps the build it loaded.`
                  : `That did not take: ${r.output || "no output"}`);
      await load();
    } catch (e: any) { setErr(e?.message || "the install failed"); }
    finally { setBusy(false); }
  }

  return (
    <span className="relative">
      <button onClick={() => setOpen((v) => !v)}
        title={upd ? `Claude Code ${d?.version} — ${d?.newest} is published. Click for details.`
                   : `Claude Code ${d?.version || "?"} — the binary this Studio runs. Click for details.`}
        className={cls("flex items-center gap-1.5 rounded-full border transition-colors",
          compact ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-0.5",
          upd ? "bg-warn/10 text-warn border-warn/40 hover:border-warn/70"
              : open ? "bg-panel2 text-text border-line" : "text-muted border-transparent hover:border-line")}>
        {upd ? <ArrowUpCircle size={13} /> : <Terminal size={13} />}
        {compact ? (d?.version || "cli") : `cli ${d?.version || "?"}`}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-[80]" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 mb-2 z-[81] card p-0 w-[27rem] max-h-[70vh] overflow-auto shadow-card text-xs">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-line">
              <Terminal size={14} className="text-brand" />
              <span className="font-medium text-text">Claude Code CLI</span>
              <button className="ml-auto p-0.5 rounded text-muted hover:text-text"
                onClick={() => setOpen(false)}><X size={14} /></button>
            </div>

            {err && <div className="px-3 py-2 text-warn bg-warn/10 border-b border-warn/30">{err}</div>}

            {d && (
              <div className="px-3 py-2.5 space-y-1.5 border-b border-line">
                <Row k="Running" v={d.version || "unknown"} />
                <Row k="Stable channel" v={d.latest || "—"} />
                <Row k="Pre-release channel" v={d.next || "—"} />
                <Row k="You are on" v={d.on_channel === "next" ? "the pre-release channel"
                  : d.on_channel === "latest" ? "the stable channel" : "a pinned version"} />
                <div className="text-muted/60 font-mono truncate pt-0.5">{d.path}</div>
              </div>
            )}

            {d && !upd && !msg && (
              <div className="px-3 py-2.5 text-muted">
                This is the newest published build on either channel.
              </div>
            )}

            {d && upd && (
              <div className="px-3 py-2.5 space-y-2">
                <div className="text-text">
                  <span className="text-warn font-medium">{d.newest}</span> is published
                  {d.newest === d.next && d.next !== d.latest
                    ? " on the pre-release channel — `claude install latest` would install the older "
                      + d.latest + " instead."
                    : "."}
                </div>
                {busySess.length > 0 && (
                  <div className="text-warn/90">
                    {busySess.length === 1 ? "One session is" : `${busySess.length} sessions are`} mid-turn.
                    Installing does not touch a running process, but one that respawns comes back on
                    the new build.
                  </div>
                )}
                <button disabled={busy} onClick={install}
                  className="btn btn-primary w-full justify-center disabled:opacity-60">
                  {busy ? <><Loader2 size={13} className="animate-spin" /> installing {d.newest}…</>
                        : <>Install {d.newest}</>}
                </button>
              </div>
            )}

            {msg && <div className="px-3 py-2.5 text-ok border-t border-line">{msg}</div>}
          </div>
        </>
      )}
    </span>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-muted w-36 shrink-0">{k}</span>
      <span className="text-text font-mono">{v}</span>
    </div>
  );
}
