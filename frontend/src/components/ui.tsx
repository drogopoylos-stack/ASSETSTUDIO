import React from "react";

export function cls(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

/** A panel someone folded away stays folded next time. Nobody wants to close the same bar twice. */
export function useSticky(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [v, setV] = React.useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : raw === "1";
    } catch { return initial; }
  });
  const set = React.useCallback((next: boolean) => {
    setV(next);
    try { localStorage.setItem(key, next ? "1" : "0"); } catch { /* private mode */ }
  }, [key]);
  return [v, set];
}

// The chat box's send settings (model, effort, mode, agent) are read through sendPrefs.ts and
// nowhere else. They are kept per FOLDER and per model; the agent-wide key this file used to read
// holds the last choice made in any folder, which is how a resend in a folder pinned to Opus 5
// went out on Opus 5.5.

/** A meter drawn in characters, for the CLI look.
 *
 *  A console has no <div> to shade, so a gauge there is block characters — they inherit the
 *  monospace face and the palette for free, and line up with everything else on the row.
 *
 *  The FILLED and EMPTY halves must be coloured separately. Painting the whole string one colour
 *  put a bright dither pattern where the empty track should be, and `░` at full strength reads as
 *  noise — or worse, as a rendering fault. The empty track is a faint grey rule; only the filled
 *  part carries the status colour.
 */
export function Meter({ pct, tone = "text-brand", width = 8, className }: {
  pct: number; tone?: string; width?: number; className?: string;
}) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  const on = Math.round((p / 100) * width);
  return (
    <span className={cls("select-none tracking-tighter", className)} aria-hidden="true">
      <span className={tone}>{"█".repeat(on)}</span>
      <span className="text-muted/40">{"░".repeat(Math.max(0, width - on))}</span>
    </span>
  );
}

/** Green with room, then the theme's blue, then orange, then red.
 *
 *  It used to be GREY below 60%, which is where a healthy meter spends nearly all of its life —
 *  so the bars read as colourless furniture and a change of state was easy to miss. Four bands
 *  instead of three also puts a step at 75%, the point where "fine" turns into "keep an eye on
 *  it", and that step is worth seeing before the bar is nearly full.
 *
 *  Deliberately NOT the console's bright yellow: #F9F1A5 is near-white, and as a solid fill it
 *  glares and fights every other thing on the row. It is a fine colour for a word, not for a bar. */
export function meterTone(pct: number): string {
  return pct > 90 ? "text-danger" : pct > 75 ? "text-warn" : pct > 50 ? "text-brand" : "text-ok";
}

/** The same four bands as a background, for the panel look's shaded bars. */
export function meterFill(pct: number): string {
  return pct > 90 ? "bg-danger" : pct > 75 ? "bg-warn" : pct > 50 ? "bg-brand-600" : "bg-ok";
}

// Poll only while the window is actually visible. Every UI poll must go through this (or guard
// itself with document.hidden): when the app is minimized / in the tray, a dozen interval loops
// otherwise keep hammering the backend forever — pure waste, felt as CPU while gaming. Skipped
// ticks don't queue; on refocus it fires once immediately so the UI catches up instantly.
// Returns a cleanup fn, so it drops into useEffect as `return pollWhileVisible(load, 5000)`.
// A TICK NEVER OVERLAPS ITS OWN PREVIOUS RUN.
//
// A poll on a fixed interval quietly assumes the request comes back inside that interval. When
// one endpoint got slower than its poll — a rail poll every 1.5s against a request taking eight
// seconds — each tick opened another connection, and a browser only allows six per origin. The
// pool filled with copies of the same request and every OTHER request in the app queued behind
// them, so the conversation feed never loaded and the meters froze on their last value. The
// machine looked idle throughout, because nothing was computing: everything was waiting.
//
// If `fn` returns a promise, the next tick is skipped until it settles. Requests carry deadlines
// (see the api client), so `busy` cannot stick.
export function pollWhileVisible(fn: () => void | Promise<unknown>, ms: number, opts?: { immediate?: boolean }): () => void {
  let busy = false;
  const run = () => {
    if (busy) return;
    let r: any;
    try { r = fn(); } catch { return; }
    if (r && typeof r.then === "function") {
      busy = true;
      const done = () => { busy = false; };
      r.then(done, done);
    }
  };
  if (opts?.immediate !== false) run();
  const iv = window.setInterval(() => { if (!document.hidden) run(); }, ms);
  const onVis = () => { if (!document.hidden) run(); };
  document.addEventListener("visibilitychange", onVis);
  return () => { window.clearInterval(iv); document.removeEventListener("visibilitychange", onVis); };
}

export function humanBytes(n?: number | null): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let f = n,
    i = 0;
  while (f >= 1024 && i < u.length - 1) {
    f /= 1024;
    i++;
  }
  return `${i === 0 ? f.toFixed(0) : f.toFixed(1)} ${u[i]}`;
}

export function fmtCost(c?: number | null): string {
  if (!c) return "free";
  return `$${c.toFixed(c < 0.01 ? 4 : 2)}`;
}

export function fmtEta(s?: number | null): string {
  if (s == null || s <= 0) return "—";
  if (s < 60) return `${Math.ceil(s)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.ceil(s % 60)}s`;
}

export function timeAgo(ts: number): string {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export const STATUS_COLOR: Record<string, string> = {
  queued: "text-muted",
  running: "text-brand",
  succeeded: "text-ok",
  failed: "text-danger",
  canceled: "text-warn",
};

/**
 * A setting's name, as a string a URL or a query selector can hold.
 *
 * Derived from the label rather than written beside it, so the two cannot disagree: rename the
 * row and its anchor renames with it. Shared, because Settings searches for these and the panes
 * are what carry them.
 */
export const settingSlug = (label: string) =>
  label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export function Section({
  title,
  desc,
  children,
  right,
}: {
  title: string;
  desc?: string;
  children?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="card p-4" data-setting={settingSlug(title)}>
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-semibold">{title}</h3>
          {desc && <p className="text-xs text-muted mt-0.5">{desc}</p>}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="inline-block animate-spin rounded-full border-2 border-line border-t-brand"
      style={{ width: size, height: size }}
    />
  );
}

export function Bar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cls("h-2 rounded-full bg-panel2 overflow-hidden", className)}>
      <div
        className="h-full bg-brand-600 transition-all duration-300"
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

export function Empty({ icon, label }: { icon?: React.ReactNode; label: string }) {
  return (
    <div className="flex flex-col items-center justify-center text-muted py-12 gap-2">
      {icon}
      <span className="text-sm">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------------- one setting

/** The whole settings object, fetched at most once every 30 s for the entire page. */
let _settings: { at: number; data: Record<string, unknown> } | null = null;
let _settingsInflight: Promise<Record<string, unknown>> | null = null;

async function readSettings(): Promise<Record<string, unknown>> {
  if (_settings && Date.now() - _settings.at < 30_000) return _settings.data;
  if (!_settingsInflight) {
    const { api } = await import("../api/client");
    _settingsInflight = api.settings()
      .then((d: Record<string, unknown>) => { _settings = { at: Date.now(), data: d || {} }; return _settings.data; })
      .catch(() => _settings?.data || {})
      .finally(() => { _settingsInflight = null; });
  }
  return _settingsInflight;
}

/** One switch out of Settings, shared across every component that asks for it.
 *
 *  Written because the first consumer is a DIFF LINE: a feed can hold hundreds of them, and the
 *  house pattern of `api.settings().then(...)` per component would be hundreds of requests for
 *  one boolean. One fetch is shared by the page and re-read when the window regains focus, so a
 *  switch changed in Settings takes effect on coming back rather than needing a reload.
 */
export function useSetting<T>(key: string, fallback: T): T {
  const [v, setV] = React.useState<T>(() => {
    const cached = _settings?.data;
    return cached && key in cached ? (cached[key] as T) : fallback;
  });
  React.useEffect(() => {
    let alive = true;
    const read = () => readSettings().then((d) => {
      if (!alive) return;
      setV(key in d ? (d[key] as T) : fallback);
    });
    read();
    const again = () => { _settings = null; read(); };
    window.addEventListener("focus", again);
    return () => { alive = false; window.removeEventListener("focus", again); };
    // `fallback` is a literal at every call site; listing it would re-run this on each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return v;
}
