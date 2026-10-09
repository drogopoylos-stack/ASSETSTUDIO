import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ContextInfo } from "../types";
import { cls, Meter, meterTone, pollWhileVisible } from "./ui";
import { feedAgent, folderAgent, readPref } from "./sendPrefs";

const k = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n));

// Context-window fill for a Claude Code session: % used + headroom until
// auto-compact. Pass `data` (from the mission overview) or `projectId` to self-fetch.
export function ContextMeter({ data, projectId, folderId, className, onCompact, refreshSignal, cli }: {
  data?: ContextInfo; projectId?: string; className?: string; onCompact?: () => void;
  /** The folder the chat box keeps its settings under (the pane's project id). `projectId` is the
   *  feed's id, which carries an "<engine>--" prefix for an alternate engine. */
  folderId?: string;
  // bump to force an immediate refetch (e.g. right after firing /compact)
  refreshSignal?: number;
  /** draw it the way a console would — characters, not a shaded bar */
  cli?: boolean;
}) {
  const [ctx, setCtx] = useState<ContextInfo | null>(data || null);
  useEffect(() => {
    if (data) { setCtx(data); return; }
    if (!projectId) return;
    let alive = true;
    const stop = pollWhileVisible(() => api.missionContext(projectId).then((c) => alive && setCtx(c)).catch(() => {}), 6000);
    return () => { alive = false; stop(); };
  }, [data, projectId]);

  // instant refresh + a short follow-up burst so the bar catches the drop the
  // moment a /compact lands (the summary takes a few seconds to be written).
  useEffect(() => {
    if (!refreshSignal || data || !projectId) return;
    let alive = true;
    const f = () => api.missionContext(projectId).then((c) => alive && setCtx(c)).catch(() => {});
    const ts = [200, 2500, 6000, 11000, 18000].map((d) => window.setTimeout(f, d));
    return () => { alive = false; ts.forEach(window.clearTimeout); };
  }, [refreshSignal, data, projectId]);

  // Which agent the chat is sending to. Codex's top-right indicator differs from Claude's — it
  // runs each message statelessly here, so there's no growing session/context window to compact.
  // THIS folder's agent, not the last one picked anywhere: with Codex in one pane, the pane beside
  // it said "stateless" too.
  const folder = folderId || projectId || "";
  const [agentSel, setAgentSel] = useState(() => folderAgent(folder));
  useEffect(() => {
    const read = () => setAgentSel(folderAgent(folder));
    read();
    const onEvt = () => read();
    window.addEventListener("cc-agent", onEvt);
    const iv = window.setInterval(() => { if (!document.hidden) read(); }, 2000);
    return () => { window.removeEventListener("cc-agent", onEvt); window.clearInterval(iv); };
  }, [folder]);

  // Kimi streams through the same engine with its own sessions → it gets the REAL context
  // meter (the parent passes the kimi-- prefixed project id); only one-shot agents are stateless.
  // An alternate engine's feed ("qwen--<folder>") is a real session with a real window.
  if (agentSel && agentSel !== "claude" && agentSel !== "kimi" && feedAgent(projectId || folder, folder) === "claude") {
    const model = readPref("model", folder, agentSel, "default");
    const name = agentSel.charAt(0).toUpperCase() + agentSel.slice(1);
    return (
      <div className={cls("flex items-center gap-1.5 text-[11px] whitespace-nowrap", className)}
        title={`You're sending to ${name}. It runs each message statelessly here — there's no growing session to auto-compact like Claude's, so no context-fill % to show.`}>
        <span className="text-muted">agent</span>
        <span className="font-medium text-text/85">{name}</span>
        {model !== "default" && <span className="font-mono text-muted/70">· {model}</span>}
        <span className="text-muted/50">· stateless</span>
      </div>
    );
  }

  if (ctx?.compacting && (!ctx.ctx_max || !ctx.ctx_used)) return (
    <span className={cls("inline-flex items-center gap-1.5 text-[11px] whitespace-nowrap", className)}>
      <span className="text-muted">ctx</span><span className="text-warn">· Compacting…</span>
    </span>
  );
  if (!ctx || !ctx.ctx_max || !ctx.ctx_used) return null;
  const pct = Math.min(100, ctx.ctx_pct || 0);
  const shown = Math.round(pct);
  // Rounded: the backend hands this back as a float, and "19.6% — compact now" in a monospace
  // row reads as a measurement when it is a rough headroom figure.
  const rem = Math.round(ctx.ctx_remaining ?? 0);
  const est = !!ctx.just_compacted; // value is an estimate until the next real turn
  const col = est ? "bg-ok" : rem < 8 ? "bg-danger" : rem < 25 ? "bg-warn" : "bg-brand-600";
  const txt = est ? "text-ok" : rem < 8 ? "text-danger" : rem < 25 ? "text-warn" : "text-muted";
  // The prompt-cache split of the last turn, the same figures the CLI puts in `/cost`. It answers
  // the question the % alone cannot: a big context is cheap while it is being READ from the cache,
  // and expensive the moment something in the prefix starts moving and every turn re-writes it.
  const cache = typeof ctx.cache_pct === "number" && (ctx.cache_read || ctx.cache_write)
    ? `\nPrompt cache: ${ctx.cache_pct}% of the last turn's input was read from cache `
      + `(${k(ctx.cache_read || 0)} read, ${k(ctx.cache_write || 0)} written, ${k(ctx.fresh_in || 0)} fresh)`
    : "";
  // The standing instructions, and what they weigh. This is the part of a window nobody chose:
  // a CLAUDE.md imports another, a plugin adds a third, and every one of them is re-sent on
  // every request for the life of the session. The biggest three are named because that is
  // what you would act on; the rest are a count.
  const ins = ctx.instructions;
  const loaded = ins?.count
    ? `\nInstructions loaded: ${ins.count} file${ins.count > 1 ? "s" : ""}, ${k(ins.tokens)} tokens on every request`
      + ins.files.slice(0, 3).map((f) => `\n  · ${f.name} — ${k(f.tokens)}${f.parent ? ` (imported by ${f.parent})` : ""}`).join("")
      + (ins.files.length > 3 ? `\n  · and ${ins.files.length - 3} more` : "")
    : "";
  const tip = `Context: ${k(ctx.ctx_used)} / ${k(ctx.ctx_max)} tokens (${ctx.ctx_pct}% used) · ${rem}% left until auto-compact${ctx.model ? ` · ${ctx.model}` : ""}${est ? ` · just ${ctx.compact_trigger === "auto" ? "auto-" : ""}compacted (estimate until the next message)` : ""}${cache}${loaded}`;

  // The console rendering. Same numbers, same click, drawn in characters — which is what a
  // terminal has instead of a progress bar, and it lines up with the rest of the row.
  // Narrow-pane behaviour. The [data-opt] marks below are read by the container query in
  // index.css, and ONLY inside a pane header — everywhere else this renders in full.
  //
  // The prose goes, the number stays, and the button stays clickable. Hiding the whole tail
  // would be easier and would take the only way to compact on demand with it, which is the
  // exact fault this read-out had before: a session that could not be compacted at all.
  if (cli) {
    return (
      <span className={cls("inline-flex items-center gap-1.5 text-[11px] font-mono whitespace-nowrap", className)} title={tip}>
        <span className="text-muted">ctx</span>
        <span data-opt="5"><Meter pct={pct} tone={est ? "text-ok" : meterTone(pct)} /></span>
        <span className={txt}>{est ? "~" : ""}{shown}%</span>
        {ctx.compacting ? (
          <span className="text-warn" title={`Compacting the conversation now — this can take a few minutes.${ctx.compact_trigger === "auto" ? " The window filled up, so the CLI started it." : ""}`}>· compacting…</span>
        ) : est ? (
          <span className="text-ok/70">· {ctx.compact_trigger === "auto" ? "auto-" : ""}compacted</span>
        ) : onCompact ? (
          <button onClick={onCompact} disabled={ctx.working} className="text-muted/70 hover:text-brand disabled:opacity-50 disabled:cursor-wait"
            title={ctx.working ? "Wait for the current turn to finish before compacting" : "Run /compact now to summarize the conversation and free up the context window"}>
            · {rem}%<span data-opt="2">{rem < 25 ? " — compact now" : " to compact"}</span>
          </button>
        ) : <span className="text-muted/60">· {rem}%<span data-opt="2"> to compact</span></span>}
      </span>
    );
  }

  return (
    <div className={cls("flex items-center gap-1.5 text-[11px] whitespace-nowrap", className)} title={tip}>
      <span className="text-muted">ctx</span>
      <div data-opt="5" className="w-16 h-1.5 rounded bg-panel2 overflow-hidden shrink-0">
        <div className={cls("h-full transition-all", col)} style={{ width: `${pct}%` }} />
      </div>
      <span className={cls("font-mono", txt)}>{est ? "~" : ""}{shown}%</span>
      {ctx.compacting ? (
        <span className="text-warn font-medium" title="Compacting the conversation now — this can take a few minutes.">· Compacting…</span>
      ) : est ? (
        <span className="text-ok/70">· {ctx.compact_trigger === "auto" ? "auto-" : ""}compacted</span>
      ) : onCompact ? (
        // Always clickable; loud only when it matters.
        //
        // This used to be `onCompact && rem < 25`, so below the warn band the read-out was plain
        // text and there was NO way to compact on demand at all — a session sitting at 40% simply
        // could not be compacted, at any time, active or idle. The original reasoning still holds
        // (a permanent "compact now" reads as "you must clear", which is the pressure a 1M window
        // exists to remove), so the wording and the underline still only arrive in the warn band.
        // The click is always there.
        <button onClick={onCompact} disabled={ctx.working}
          title={ctx.working ? "Wait for the current turn to finish before compacting" : "Run /compact now to summarize the conversation and free up the context window"}
          className={cls("text-muted/60 hover:text-brand underline decoration-dotted disabled:opacity-50 disabled:cursor-wait",
            rem < 25 ? "decoration-muted/40" : "decoration-transparent hover:decoration-muted/40")}>
          · {rem}%<span data-opt="2">{rem < 25 ? " — compact now" : " to compact"}</span>
        </button>
      ) : (
        <span className="text-muted/60">· {rem}%<span data-opt="2"> to compact</span></span>
      )}
    </div>
  );
}
