import { useCallback, useEffect, useState } from "react";
import { Globe, Loader2, Square, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { cls, pollWhileVisible } from "./ui";

// The status pill for the headless review browser.
//
// It used to be a label and nothing else: "headless browser active". That tells you a browser
// exists and gives you no way to find out whose it is, what it is doing, whether it is stuck, or
// how to be rid of it. Click it now and it says which project asked for it, what the review was
// called, how long it has been up, when it will close itself — and it can be closed on the spot.
//
// It also lists browsers the Studio did NOT start, because those are the ones that pile up: a
// backend that is restarted abandons its Chrome, and nothing ever comes back for it. Four of them
// holding 2.3 GB is what that looked like in practice.

type Browsers = Awaited<ReturnType<typeof api.reviewBrowsers>>;

/** "2m 13s", "41s", "1h 20m" — a duration you can read at a glance in a status bar. */
function dur(s: number): string {
  const n = Math.max(0, Math.round(s));
  if (n < 60) return `${n}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m ${n % 60}s`;
  return `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
}

const baseName = (p: string) => (p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

export function BrowserPill({ count, compact }: { count: number; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Browsers | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await api.reviewBrowsers());
      setErr("");
    } catch (e: any) {
      // The endpoint is newer than the running backend until it is restarted. Say that, rather
      // than showing an empty panel that looks like "nothing is running" when something is.
      setErr(/404/.test(e?.message || "")
        ? "Restart the backend to use this — the endpoint is newer than the running process."
        : e?.message || "could not read the browser list");
    }
  }, []);

  // Only while the panel is open: the figures are a countdown, and nobody is watching it closed.
  useEffect(() => {
    if (!open) return;
    return pollWhileVisible(load, 2000);
  }, [open, load]);

  if (count <= 0 && !open) return null;

  const ours = data?.ours || null;
  const strays = data?.strays || [];
  const strayMb = strays.reduce((a, b) => a + b.mb, 0);

  async function act(what: string, fn: () => Promise<unknown>) {
    setBusy(what);
    try { await fn(); await load(); } catch (e: any) { setErr(e?.message || "that did not work"); }
    finally { setBusy(""); }
  }

  return (
    <span className="relative">
      <button onClick={() => setOpen((v) => !v)}
        title="A browser is rendering a page for an AI review. Click to see which project, and to close it."
        className={cls("flex items-center gap-1.5 rounded-full border transition-colors",
          compact ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-0.5",
          open ? "bg-accent/20 text-accent border-accent/60"
               : "bg-accent/10 text-accent border-accent/30 hover:border-accent/60")}>
        <Globe size={13} className={open ? "" : "animate-pulse"} />
        {compact ? count : "headless browser active"}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-[80]" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 mb-2 z-[81] card p-0 w-[27rem] max-h-[70vh] overflow-auto shadow-card text-xs">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-line">
              <Globe size={14} className="text-accent" />
              <span className="font-medium text-text">Headless browser</span>
              <button className="ml-auto p-0.5 rounded text-muted hover:text-text"
                onClick={() => setOpen(false)}><X size={14} /></button>
            </div>

            {err && <div className="px-3 py-2 text-warn bg-warn/10 border-b border-warn/30">{err}</div>}

            {!data && !err && (
              <div className="px-3 py-4 text-muted flex items-center gap-2">
                <Loader2 size={13} className="animate-spin" /> reading…
              </div>
            )}

            {ours && (
              <div className="px-3 py-2.5 border-b border-line">
                <div className="text-[10px] uppercase tracking-wide text-muted mb-1.5">This Studio</div>
                {ours.who ? (
                  <>
                    <div className="text-text">
                      Reviewing <span className="text-brand">{baseName(ours.who.project) || "a project"}</span>
                      {ours.who.label && <> — “{ours.who.label}”</>}
                    </div>
                    <div className="text-muted mt-0.5">
                      {ours.who.mode === "isolate" ? "one asset on a clean backdrop" : "the running page"}
                      {ours.who.subject && <> · {ours.who.subject}</>}
                      {ours.who.renders > 1 && <> · {ours.who.renders} renders</>}
                    </div>
                    {ours.who.url && (
                      <div className="text-muted/60 font-mono truncate mt-0.5">{ours.who.url}</div>
                    )}
                  </>
                ) : (
                  <div className="text-muted">Open and warm, with no review running. It was started ready
                    for the next one.</div>
                )}
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-muted/80 font-mono text-[11px]">
                  <span>up {dur(ours.age_s)}</span>
                  <span>idle {dur(ours.idle_s)}</span>
                  {/* the reaper is not a mystery: say exactly when it will act */}
                  <span className={ours.closes_in_s < 60 ? "text-warn" : ""}>
                    closes itself in {dur(ours.closes_in_s)}
                  </span>
                  <span>port {ours.port}</span>
                  {ours.tabs.length > 0 && <span>{ours.tabs.length} warm tab{ours.tabs.length > 1 ? "s" : ""}</span>}
                </div>
                <button disabled={!!busy}
                  onClick={() => act("stop", () => api.reviewStop())}
                  className="mt-2 px-2 py-1 rounded border border-line hover:border-danger/60 hover:text-danger flex items-center gap-1.5 disabled:opacity-50">
                  {busy === "stop" ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
                  Close it
                </button>
                <div className="text-muted/50 mt-1">It opens again by itself the next time an agent asks
                  for a review.</div>
              </div>
            )}

            {data && !ours && !strays.length && !err && (
              <div className="px-3 py-4 text-muted">Nothing is running now.</div>
            )}

            {strays.length > 0 && (
              <div className="px-3 py-2.5">
                <div className="text-[10px] uppercase tracking-wide text-muted mb-1.5">
                  Not started by this Studio — {strays.length} browser{strays.length > 1 ? "s" : ""},
                  {" "}{(strayMb / 1024).toFixed(2)} GB
                </div>
                <div className="space-y-1">
                  {strays.map((b) => (
                    <div key={b.pid} className="flex items-center gap-2 font-mono text-[11px]">
                      <span className="text-muted/60 w-14 shrink-0">{b.pid}</span>
                      <span className="flex-1 min-w-0 truncate text-text/80">{b.kind}</span>
                      <span className="text-muted shrink-0">{dur(b.age_s)}</span>
                      <span className="text-muted shrink-0 w-16 text-right">{b.mb.toFixed(0)} MB</span>
                      <button disabled={!!busy} title={`Kill ${b.pid}`}
                        onClick={() => act(`k${b.pid}`, () => api.reviewKill([b.pid]))}
                        className="p-0.5 rounded text-muted hover:text-danger disabled:opacity-50">
                        {busy === `k${b.pid}` ? <Loader2 size={11} className="animate-spin" /> : <X size={12} />}
                      </button>
                    </div>
                  ))}
                </div>
                <button disabled={!!busy}
                  onClick={() => act("all", () => api.reviewKill(strays.map((b) => b.pid)))}
                  className="mt-2 px-2 py-1 rounded border border-line hover:border-danger/60 hover:text-danger flex items-center gap-1.5 disabled:opacity-50">
                  {busy === "all" ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                  Kill all {strays.length}
                </button>
                <div className="text-muted/50 mt-1">
                  A backend that is restarted leaves its review browser behind. These are swept at
                  startup as well, so the list should normally be empty.
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </span>
  );
}
