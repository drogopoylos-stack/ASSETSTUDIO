import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, Sparkles, TriangleAlert } from "lucide-react";
import { api } from "../api/client";
import { cls } from "./ui";

// Two ways this app can be out of date, watched by one poller.
//
// The UI half: the served build id changes when the frontend is rebuilt, and a reload picks it up.
//
// The BACKEND half was the one nobody could see. Python is imported once, at boot, so a backend
// change does nothing at all until the process restarts — and there was no signal anywhere that
// this had happened. A forgotten restart looks exactly like a feature that does not work, which
// is the failure it actually caused: four separate rounds of changes this week ended with a
// human being told, by hand, to go and restart. `/api/system/version` now fingerprints every
// .py on disk and compares it with the fingerprint taken at import, so the app can say so itself.
//
// AND THE RELOAD IS AUTOMATIC, whoever did the restarting.
//
// Pressing the button here already reloaded afterwards. But a backend restarted any OTHER way —
// the desktop shortcut, `Restart Studio Backend.bat`, the supervisor after a crash — left this
// page talking to a process it had never met, still holding the old code's assumptions, until
// someone pressed Ctrl+R. `started_at` is the backend's own boot time: when it changes, the
// process was replaced, and the page reloads itself. It says so for a moment first, so a window
// reloading on its own is explained rather than mysterious.
//
// Reloading is safe: the composer persists its draft per project (`cc-draft-<id>`), so nothing
// you were typing is lost.
//
// WHAT A RESTART REALLY COSTS — the card has now been wrong about this in both directions.
//
// It first claimed a restart would kill your agents. That was wrong: a session is spawned
// CREATE_BREAKAWAY_FROM_JOB in its own process group, with its stdout on a log file rather than a
// pipe, precisely so it outlives the backend, and the next start-up adopts it.
//
// Then it claimed a restart costs nothing. That was wrong too, and cost real work. The PROCESS
// survives; its ability to be TALKED TO does not. Windows will not hand a new parent the stdin of
// a child it did not create, so an inherited session can be watched but not written to (see
// `cc_session._Adopted`). A conversation does not mind — the next message respawns it from the
// transcript. A BACKGROUND AGENT does: it reports through that channel, and without it the CLI
// eventually gives up on the absent client and interrupts its work, recording no result at all,
// because an interrupt leaves no transcript marker.
//
// So: a turn in flight is INFORMATION and the button stays available; background agents are a
// COST, named one by one, and the restart asks first.

type Ver = Awaited<ReturnType<typeof api.sysVersion>>;

/** What the desktop launcher exposes. Undefined in a plain browser tab. */
type Bridge = { restartBackend?: () => Promise<{ ok: boolean; error?: string }> };

// Survives the reload it is guarding against, which is the whole point of sessionStorage here.
const RELOAD_KEY = "studio-auto-reload-at";
const RELOAD_GUARD_MS = 30000;

export default function UpdateBanner() {
  const [uiStale, setUiStale] = useState(false);
  const [ver, setVer] = useState<Ver | null>(null);
  const [dismissed, setDismissed] = useState("");   // the code id that was waved away
  const [phase, setPhase] = useState<"" | "restarting" | "reloading" | "failed">("");
  const [err, setErr] = useState("");
  const [waited, setWaited] = useState(0);          // seconds spent waiting, so it never looks stuck
  const [asking, setAsking] = useState(false);      // showing what a restart would cost
  const ticker = useRef<number | null>(null);
  const initial = useRef<string | null>(null);
  const boot = useRef<number | null>(null);         // the backend boot time we started talking to
  const reloading = useRef(false);                  // fire the reload once, never twice

  const check = useCallback(() => api.sysVersion().then((v) => {
    setVer(v);
    // A DIFFERENT backend process is answering than the one this page loaded against.
    const at = typeof v.started_at === "number" ? v.started_at : null;
    if (at !== null) {
      if (boot.current === null) boot.current = at;
      else if (at !== boot.current && !reloading.current) {
        // ONE AUTOMATIC RELOAD, NOT A STORM.
        //
        // A backend that flaps changes started_at on every pass, and reloading each time is far
        // worse than a stale page: the window jumps, the reason never stays on screen long
        // enough to read, and each reload costs a fresh start-up. After the first, the new
        // process is accepted quietly and the card offers the reload instead — the user chooses.
        let last = 0;
        try { last = Number(sessionStorage.getItem(RELOAD_KEY) || 0); } catch { /* private mode */ }
        if (Date.now() - last < RELOAD_GUARD_MS) {
          boot.current = at;
          setUiStale(true);            // shows "An update is ready" with a Reload button
          return;
        }
        try { sessionStorage.setItem(RELOAD_KEY, String(Date.now())); } catch { /* ignore */ }
        reloading.current = true;
        setPhase("reloading");
        window.setTimeout(() => location.reload(), 900);   // let the notice paint first
        return;
      }
    }
    if (!v.build) return;
    if (initial.current === null) initial.current = v.build;
    else if (v.build !== initial.current) setUiStale(true);
  }).catch(() => {}), []);

  useEffect(() => {
    let alive = true;
    const run = () => { if (alive && !document.hidden) check(); };
    run();
    const iv = window.setInterval(run, 15000);
    return () => {
      alive = false;
      window.clearInterval(iv);
      if (ticker.current) window.clearInterval(ticker.current);
    };
  }, [check]);

  const codeStale = !!ver?.code_stale && dismissed !== (ver?.code_now || "");
  const busy = ver?.busy;
  const inFlight = (busy?.turns || 0) + (busy?.agents || 0);
  const stranded = busy?.stranded || [];
  // Sessions started before the keeper existed. A restart ends those, agents or not - and it
  // is worth saying once, because after that restart every session survives the next one.
  const unkeepered = busy?.unkeepered || [];

  // A spinner with nothing moving beside it reads as frozen, and that is exactly how this
  // looked when it stalled. The seconds tick so the wait is visibly a wait.
  function startTicker() {
    setWaited(0);
    if (ticker.current) window.clearInterval(ticker.current);
    const t0 = Date.now();
    ticker.current = window.setInterval(() => setWaited(Math.round((Date.now() - t0) / 1000)), 500);
  }
  function stopTicker() {
    if (ticker.current) { window.clearInterval(ticker.current); ticker.current = null; }
  }

  async function restart(confirmed = false) {
    // ASK FIRST WHEN A RESTART WOULD COST SOMETHING.
    //
    // A session whose stdin a keeper holds is untouched by a restart, so there is nothing to
    // ask about. A session WITHOUT one exits a second after this backend does — a stream-json
    // process ends when its stdin closes — and any background agent inside it goes with it, with
    // no result recorded, because an interrupt leaves no transcript marker. Two agents were lost
    // exactly that way, and nothing said so until a new session started four hours later.
    //
    // In practice only a session started by a backend older than the keeper is bare, so this
    // asks once and then stops asking.
    //
    // The check lives here, not only in the endpoint, because the desktop path goes through the
    // launcher and never touches /api/system/restart.
    const stranded = ver?.busy?.stranded || [];
    const bare = ver?.busy?.unkeepered || [];
    if ((stranded.length || bare.length) && !confirmed) { setAsking(true); return; }
    setAsking(false);
    setErr("");
    setPhase("restarting");
    startTicker();

    // THE LAUNCHER DOES IT, when there is one.
    //
    // The backend cannot restart itself here without fighting its own supervisor: ending the
    // serving process makes the launcher respawn a competing backend, and its health watchdog
    // can add a third, so several race for the port and this card waits on a `started_at` that
    // never settles. The launcher instead suppresses its recovery for the duration, and reloads
    // the window itself when the new backend answers.
    const bridge = (window as unknown as { studioBridge?: Bridge }).studioBridge;
    if (bridge?.restartBackend) {
      const r = await Promise.race([
        bridge.restartBackend().catch(() => ({ ok: false, error: "the launcher did not answer" })),
        new Promise<{ ok: boolean; error?: string }>((res) =>
          window.setTimeout(() => res({ ok: false, error: "the launcher did not answer in 60s" }), 60000)),
      ]);
      if (r?.ok) { stopTicker(); setPhase("reloading"); return; }   // it reloads us in a moment
      stopTicker();
      setPhase("failed");
      setErr(r?.error || "The backend did not come back.");
      return;
    }

    // Plain browser: no supervisor to race, so the backend spawns its own replacement.
    try {
      const r = await api.sysRestart(true);
      if (!r.ok) {
        // Defensive only — the backend no longer refuses, because a restart does not stop the
        // agents. Kept so a future refusal would be shown rather than swallowed.
        stopTicker();
        setErr(r.error || "The backend would not restart.");
        setPhase("failed");
        return;
      }
    } catch {
      // The connection dropping IS the restart happening — the process exits mid-response.
    }
    // Poll until the NEW process answers. `check` compares started_at and reloads the moment it
    // changes, so the wait is proof the new code is serving — not merely that a port is open.
    const t0 = Date.now();
    const poll = window.setInterval(() => {
      if (reloading.current) { window.clearInterval(poll); stopTicker(); return; }
      if (Date.now() - t0 > 60000) {
        window.clearInterval(poll);
        stopTicker();
        setPhase("failed");
        setErr("The backend did not answer within 60s. Start it from the desktop shortcut.");
        return;
      }
      check();
    }, 1000);
  }

  // The reload notice must show even when nothing was stale — an external restart is exactly the
  // case where this card would otherwise be hidden, and the window would reload with no reason.
  // A restart in progress, or one that failed, must stay on screen for the same reason: the user
  // pressed a button and is owed the outcome, even if the staleness that prompted it has gone.
  const busyPhase = phase === "reloading" || phase === "restarting" || phase === "failed";
  if (!uiStale && !codeStale && !busyPhase) return null;

  if (phase === "reloading") {
    return (
      <div className="fixed bottom-5 right-5 z-[200] card p-3 shadow-card animate-in border-brand/60 flex items-center gap-3">
        <Loader2 size={16} className="text-brand shrink-0 animate-spin" />
        <span className="text-sm">The backend restarted — reloading…</span>
      </div>
    );
  }

  return (
    <div className={cls("fixed bottom-5 right-5 z-[200] card p-3 shadow-card animate-in max-w-[26rem]",
      codeStale || busyPhase ? "border-warn/60" : "border-brand/60")}>
      {codeStale || busyPhase ? (
        <div className="space-y-2">
          <div className="flex items-start gap-2.5">
            <TriangleAlert size={16} className="text-warn shrink-0 mt-0.5" />
            <div className="min-w-0">
              <div className="text-sm text-text">
                {codeStale ? "The backend is running older code." : "Restarting the backend."}
              </div>
              <div className="text-xs text-muted mt-0.5">
                {codeStale
                  ? <>Python is read once at start-up, so changes on disk do nothing until it restarts.
                      {typeof ver?.uptime_s === "number" && ver.uptime_s > 60 && ` Up ${fmtUptime(ver.uptime_s)}.`}</>
                  : "The window reloads by itself when the new process answers."}
              </div>
            </div>
            <button className="ml-auto shrink-0 text-muted hover:text-text text-xs"
              onClick={() => setDismissed(ver?.code_now || "x")}
              title="Hide until the code changes again">✕</button>
          </div>

          {asking ? (
            // The one real cost, named. A session survives a restart; its ability to be TALKED TO
            // does not, and a background agent reports through exactly that channel.
            <div className="text-xs bg-panel2/60 border border-warn/50 rounded px-2 py-1.5 space-y-1">
              {stranded.length > 0 && (<>
                <div className="text-warn">
                  {stranded.length} background agent{stranded.length > 1 ? "s" : ""} would be
                  interrupted with no result recorded:
                </div>
                <ul className="text-muted space-y-0.5">
                  {stranded.slice(0, 4).map((s, i) => (
                    <li key={i} className="truncate">· {s.agent || s.project}</li>
                  ))}
                  {stranded.length > 4 && <li>· and {stranded.length - 4} more</li>}
                </ul>
              </>)}
              {stranded.length === 0 && unkeepered.length > 0 && (
                <div className="text-warn">
                  {unkeepered.length} chat session{unkeepered.length > 1 ? "s" : ""} would end
                  (nothing is in flight in {unkeepered.length > 1 ? "them" : "it"}).
                </div>
              )}
              <div className="text-muted/80">
                {unkeepered.length > 0
                  ? "These sessions were started before the fix, so this backend still holds their input pipe and they end with it. The next message starts them again — and every session after this restart survives the one after that."
                  : "Wait for them, or restart anyway."}
              </div>
            </div>
          ) : inFlight > 0 && (
            <div className="text-xs text-muted bg-panel2/60 border border-line rounded px-2 py-1.5">
              {busy!.turns > 0 && <>{busy!.turns} turn{busy!.turns > 1 ? "s" : ""}</>}
              {busy!.turns > 0 && busy!.agents > 0 && " and "}
              {busy!.agents > 0 && <>{busy!.agents} subagent{busy!.agents > 1 ? "s" : ""}</>}
              {" "}running — the conversation keeps going and is picked back up.
            </div>
          )}

          {err && <div className="text-xs text-danger">{err}</div>}

          <div className="flex items-center gap-2">
            {phase === "restarting" ? (
              <span className="flex items-center gap-2 text-xs text-muted tabular-nums">
                <Loader2 size={13} className="animate-spin" />
                restarting, then reloading… {waited}s
              </span>
            ) : asking ? (
              <>
                <button className="btn-primary !py-1 !px-3 text-sm inline-flex items-center gap-1.5 !bg-warn/80 hover:!bg-warn"
                  onClick={() => restart(true)}>
                  <RefreshCw size={13} /> Restart anyway
                </button>
                <button className="text-xs text-muted hover:text-text"
                  onClick={() => setAsking(false)}>Wait for them</button>
              </>
            ) : (
              <button className="btn-primary !py-1 !px-3 text-sm inline-flex items-center gap-1.5"
                onClick={() => restart()}>
                <RefreshCw size={13} /> Restart backend
              </button>
            )}
            {!asking && <span className="text-[11px] text-muted/70">the window reloads itself</span>}
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3">
          <Sparkles size={16} className="text-brand shrink-0" />
          <span className="text-sm">An update is ready.</span>
          <button className="btn-primary !py-1 !px-3 text-sm" onClick={() => location.reload()}>Reload</button>
          <button className="text-muted hover:text-text text-xs" onClick={() => setUiStale(false)} title="dismiss">✕</button>
        </div>
      )}
    </div>
  );
}

function fmtUptime(s: number): string {
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.round((s % 86400) / 3600)}h`;
}
