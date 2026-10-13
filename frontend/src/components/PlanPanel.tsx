import { useEffect, useMemo, useState } from "react";
import { CalendarClock, Check, FolderGit2, Gauge, Layers3, Pencil } from "lucide-react";
import { api } from "../api/client";
import type { PlanDashboard, PlanSub } from "../types";
import { Section, Spinner, cls, meterFill, meterTone, pollWhileVisible } from "./ui";

/** WHAT THIS PANEL REPLACED, AND WHY.
 *
 *  The Dashboard used to open with money: a generation cost, an agent spend, a monthly total. On
 *  a fixed subscription not one of those dollars is charged - they are a rate card applied to
 *  work that the plan already paid for. The screen was leading with its least actionable number,
 *  and it read as a bill.
 *
 *  These are the questions a subscription actually raises: when does it renew, how much of the
 *  window is left, which models am I spending it on, and which projects got the work. Every
 *  number here is measured rather than priced - a percentage the provider reports, or a turn
 *  count from `data/turns.jsonl`.
 *
 *  THE ONE DATE NOBODY PUBLISHES. Codex carries its renewal in the id_token on disk, so it is
 *  exact and free to read. Anthropic exposes no billing date on any endpoint Claude Code is
 *  allowed to call, so Claude's renewal is typed once here and kept in `claude_renews_on`. A
 *  guessed date would be worse than an empty one: it would be believed.
 */

function fmtTokens(n?: number | null): string {
  const v = Number(n) || 0;
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(v);
}

/** A countdown a human reads at a glance: "3d 4h", "4h 12m", "7m". */
function fmtSpan(seconds?: number | null): string {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (!s) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${Math.max(1, m)}m`;
}

function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function fmtDay(day: string): string {
  const d = new Date(`${day}T12:00:00`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** A labelled percentage bar. The same four colour bands as every other meter in the app, so a
 *  window that is nearly spent looks the same here as it does in the top bar. */
function Window({ label, percent, resetSeconds }: { label: string; percent: number; resetSeconds?: number }) {
  const pct = Math.max(0, Math.min(100, Number(percent) || 0));
  return (
    <div>
      <div className="flex items-baseline gap-2 text-[11px]">
        <span className="text-muted truncate flex-1">{label}</span>
        <span className={cls("font-mono tabular-nums", meterTone(pct))}>{pct.toFixed(0)}%</span>
        {resetSeconds ? <span className="text-muted/70 font-mono">· {fmtSpan(resetSeconds)}</span> : null}
      </div>
      <div className="mt-1 h-1.5 rounded-full bg-panel2 overflow-hidden">
        <div className={cls("h-full transition-all duration-300", meterFill(pct))} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** The renewal line. For Codex it is a fact; for Claude it is an input, because the date exists
 *  nowhere this app can read it. */
function Renewal({ sub, onSaved }: { sub: PlanSub; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!sub.setting) return;
    setSaving(true);
    try {
      await api.updateSettings({ [sub.setting]: value.trim() });
      setEditing(false);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1.5">
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") setEditing(false);
          }}
          placeholder="day of month, or 2026-11-15"
          className="input text-[11px] py-1 px-2 flex-1 min-w-0"
        />
        <button className="btn-ghost p-1" title="Save" disabled={saving} onClick={save}>
          {saving ? <Spinner size={12} /> : <Check size={13} />}
        </button>
      </div>
    );
  }

  if (sub.renews_at) {
    const days = sub.renews_in_days ?? null;
    return (
      <div className="flex items-center gap-1.5 text-[11px]">
        <CalendarClock size={12} className={cls(days != null && days <= 3 ? "text-warn" : "text-muted")} />
        <span className={cls(days != null && days <= 3 && "text-warn")}>
          Renews {days === 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`}
        </span>
        <span className="text-muted/70">· {fmtDate(sub.renews_at)}</span>
        {sub.renewal_source === "you" && sub.setting && (
          <button className="btn-ghost p-0.5 ml-auto" title="Change the renewal date"
                  onClick={() => { setValue(""); setEditing(true); }}>
            <Pencil size={11} />
          </button>
        )}
      </div>
    );
  }

  if (sub.setting) {
    return (
      <button className="text-[11px] text-brand hover:underline text-left flex items-center gap-1.5"
              onClick={() => setEditing(true)} title={sub.renewal_hint || ""}>
        <CalendarClock size={12} /> Set the renewal date
      </button>
    );
  }
  return <div className="text-[11px] text-muted">Renewal date unknown</div>;
}

function SubCard({ sub, onSaved }: { sub: PlanSub; onSaved: () => void }) {
  return (
    <div className="card p-4 space-y-2.5">
      <div className="flex items-center gap-2">
        <span className="rounded-lg bg-panel2 p-1.5 text-brand"><Gauge size={16} /></span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold truncate">{sub.plan || sub.name}</div>
          <div className="text-[11px] text-muted truncate">{sub.name}</div>
        </div>
        {sub.stale && <span className="chip text-warn border-warn/40 text-[10px]">stale</span>}
      </div>

      <Renewal sub={sub} onSaved={onSaved} />

      {sub.windows.length > 0 ? (
        <div className="space-y-2 pt-0.5">
          {sub.windows.map((w) => (
            <Window key={w.key} label={w.label} percent={w.percent} resetSeconds={w.reset_seconds} />
          ))}
        </div>
      ) : (
        <div className="text-[11px] text-muted">{sub.note || "No limit windows reported."}</div>
      )}

      {sub.windows.length > 0 && sub.note && (
        <div className="text-[11px] text-muted">{sub.note}</div>
      )}

      {sub.extra_usage?.enabled && (
        <div className="text-[11px] text-muted">
          Extra usage on · {sub.extra_usage.used_credits ?? 0}
          {sub.extra_usage.monthly_limit ? ` / ${sub.extra_usage.monthly_limit}` : ""}{" "}
          {sub.extra_usage.currency || ""}
        </div>
      )}
    </div>
  );
}

/** One row of the two "where it went" lists. The bar is the share of turns, not of money. */
function Row({ label, tag, turns, tokens, share }:
             { label: string; tag?: string; turns: number; tokens: number; share: number }) {
  return (
    <div>
      <div className="flex items-baseline gap-2 text-xs">
        <span className="truncate flex-1 font-medium">{label}</span>
        {tag && <span className="chip text-[9px] py-0 px-1 text-muted">{tag}</span>}
        <span className="font-mono tabular-nums text-muted">{turns} turns</span>
        <span className="font-mono tabular-nums text-muted/70 w-12 text-right">{fmtTokens(tokens)}</span>
      </div>
      <div className="mt-1 h-1.5 rounded-full bg-panel2 overflow-hidden">
        <div className="h-full bg-brand-600" style={{ width: `${Math.max(1, share)}%` }} />
      </div>
    </div>
  );
}

export default function PlanPanel({ days = 14 }: { days?: number }) {
  const [data, setData] = useState<PlanDashboard | null>(null);
  const [err, setErr] = useState("");

  // One request for both accounts, and not a fast one: the Codex half can wait on its
  // app-server. Both halves are cached in the backend, so a 30s poll is the cheap end of this.
  const load = (force = false) =>
    api.planDashboard(days, force)
      .then((d) => { setData(d); setErr(""); })
      .catch((e) => setErr(/404/.test(e?.message || "") ? "restart the backend to enable it"
                                                        : e?.message || "not available"));
  useEffect(() => pollWhileVisible(() => load(), 30000), [days]);

  const work = data?.work;
  const busiest = work?.busiest_day;
  const peak = useMemo(
    () => Math.max(1, ...(work?.by_day || []).map((d) => d.turns)),
    [work],
  );

  return (
    <div className="space-y-5">
      <Section
        title="Subscriptions"
        desc="What is paid for, when it renews, and how much of the window is left"
        right={
          err ? <span className="chip text-muted">{err}</span>
              : !data ? <Spinner />
              : <span className="chip">{data.subscriptions.filter((s) => s.available).length}/{data.subscriptions.length} live</span>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {!data
            ? [0, 1].map((i) => (
                <div key={i} className="card p-4 h-32 flex items-center justify-center">
                  {err ? <span className="text-xs text-muted">{err}</span> : <Spinner size={18} />}
                </div>
              ))
            : data.subscriptions.map((s) => (
                <SubCard key={s.id} sub={s} onSaved={() => load(true)} />
              ))}
        </div>
      </Section>

      <Section
        title="Your work"
        desc={`Turns, not dollars — the last ${work?.days ?? days} days from the turn ledger`}
        right={
          work ? (
            <div className="flex items-center gap-2">
              <span className="chip">{work.turns.toLocaleString()} turns</span>
              <span className="chip">{fmtTokens(work.tokens)} tokens</span>
            </div>
          ) : null
        }
      >
        {!work ? (
          <div className="h-24 flex items-center justify-center text-xs text-muted">
            {err || <Spinner size={18} />}
          </div>
        ) : work.turns === 0 ? (
          <div className="text-xs text-muted">No finished turns in this window yet.</div>
        ) : (
          <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div className="space-y-2.5">
                <div className="flex items-center gap-2 text-xs font-semibold">
                  <Layers3 size={13} className="text-brand" /> Models
                  <span className="text-muted font-normal">{work.model_count} used</span>
                </div>
                {work.models.map((m) => (
                  <Row key={m.model} label={m.model} turns={m.turns} tokens={m.tokens} share={m.share} />
                ))}
              </div>
              <div className="space-y-2.5">
                <div className="flex items-center gap-2 text-xs font-semibold">
                  <FolderGit2 size={13} className="text-brand" /> Projects
                  <span className="text-muted font-normal">{work.project_count} touched</span>
                </div>
                {work.projects.map((p) => (
                  <Row key={p.id} label={p.name} tag={p.harness || undefined}
                       turns={p.turns} tokens={p.tokens} share={p.share} />
                ))}
              </div>
            </div>

            {/* Per-day activity. Height is the share of the busiest day, so the shape is the
                point rather than the absolute number. */}
            <div>
              <div className="flex items-center justify-between text-xs font-semibold mb-2">
                <span>Per day</span>
                {busiest && busiest.turns > 0 && (
                  <span className="text-muted font-normal">
                    busiest {fmtDay(busiest.day)} · {busiest.turns} turns
                  </span>
                )}
              </div>
              <div className="flex items-end gap-1 h-16">
                {work.by_day.map((d) => (
                  <div key={d.day} className="flex-1 flex flex-col justify-end h-full group relative"
                       title={`${fmtDay(d.day)} · ${d.turns} turns · ${fmtTokens(d.tokens)} tokens`}>
                    <div className={cls("rounded-t bg-brand-600/80 group-hover:bg-brand transition-colors",
                                        d.turns === 0 && "bg-panel2")}
                         style={{ height: `${Math.max(2, (d.turns / peak) * 100)}%` }} />
                  </div>
                ))}
              </div>
              <div className="flex justify-between text-[10px] text-muted mt-1">
                <span>{fmtDay(work.by_day[0]?.day || "")}</span>
                <span>{fmtDay(work.by_day[work.by_day.length - 1]?.day || "")}</span>
              </div>
            </div>

            {work.efforts.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted">Effort:</span>
                {work.efforts.map((e) => (
                  <span key={e.effort} className="chip">
                    {e.effort} <span className="font-mono">{e.turns}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </Section>
    </div>
  );
}
