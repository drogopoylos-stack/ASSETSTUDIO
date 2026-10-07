import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Coins, Gauge, Loader2, RefreshCw, Wrench } from "lucide-react";
import { api } from "../api/client";
import { cls, pollWhileVisible } from "./ui";

// THE COST & POLICY PANEL.
//
// The Dashboard used to carry one number for agent spend and nothing behind it, and that number
// was 2.34x too high: the CLI reports a RUNNING TOTAL for the whole streaming process on every
// `result`, and the Studio summed those. `cc_session` takes the delta now and `ledger_repair`
// rebuilt the history, so the figure here is right — but a total still cannot tell you WHERE the
// money went, which is the only question that changes behaviour.
//
// So this panel answers three, in the order they are useful:
//
//   1. WHAT IT COST — month, total, today, per model, per project, the last fortnight.
//   2. WHAT IT BOUGHT — cost per TOOL, per kind of round trip, and where the context sat. On this
//      machine one session's 1,552 round trips came to $316: Bash alone was 455 of them and $101
//      (32%), deliberation before an action was $88 (28%), and the phases panel's bookkeeping was
//      104 calls and $15.65. None of that is visible from a project total.
//   3. WHAT STOPS IT — the monthly cap, which until now was checked ONLY against paid
//      asset-generation jobs and could not be set from the UI at all, and the token budgets, which
//      were declared in settings and read by nothing.
//
// A model with no rate in the Studio's rate card is shown as "not priced" rather than $0.00. The
// tokens are real; the dollars are unmeasured. Reporting unmeasured as zero is exactly the lie
// that hid Codex and DeepSeek from this screen in the first place.

type Spend = {
  total: number; today: number; month?: number; turns: number; projects: number;
  by_model: Record<string, number>;
  top: { project: string; cost: number; turns: number }[];
  days: Record<string, number>;
  cap?: { limit: number; spent: number; enabled: boolean; over: boolean };
  budgets?: { key: string; label: string; used: number; limit: number; percent: number }[];
};

type Breakdown = {
  ok: boolean; note?: string; round_trips: number; total: number;
  by_tool: { tool: string; calls: number; cost: number; percent: number; per_call: number }[];
  by_kind: { kind: string; messages: number; cost: number; percent: number }[];
  by_model: { model: string; messages: number; cost: number; basis: string }[];
  context: { files?: number; messages: number; spread: { label: string; messages: number; percent: number }[] };
};

/** Dollars, always with cents — `fmtCost` renders a falsy value as the word "free", which is a
 *  different claim from "$0.00" and the wrong one for a spend figure. */
const usd = (n: number | undefined) => `$${(Number(n) || 0).toFixed(2)}`;
const short = (p: string) => (p || "").replace(/\\/g, "/").split("/").filter(Boolean).pop() || p;

function Bar({ value, max, tone = "bg-brand" }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? Math.max(1, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-1.5 rounded-full bg-panel2 overflow-hidden">
      <div className={cls("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function CostPanel({ spend, onChanged }: { spend: Spend | null; onChanged?: () => void }) {
  const [tab, setTab] = useState<"cost" | "tools" | "limits">("cost");
  const [project, setProject] = useState("");
  const [bd, setBd] = useState<Breakdown | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [cap, setCap] = useState("");
  const [budgets, setBudgets] = useState<Record<string, string>>({});
  // THE MACHINE GUARDS, in the same tab as the money one, because "what stops a job" is one
  // question with two answers: a remote job is stopped by money, a LOCAL job by this PC's RAM and
  // disk. They were enforced by the backend before they were visible here — which is the same as
  // not being settable, since the refusal message told people to change a setting they could not
  // find. The numbers are not a nicety: one MiniMax H3 clip holds ~30.8 GB of private working set,
  // and the second heavyweight job on top of it pushes the rest of the system into the page file.
  const [diskFloor, setDiskFloor] = useState("");
  const [ramFloor, setRamFloor] = useState("");
  const [serialize, setSerialize] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState("");

  // The cap and the budgets live in settings, not in localStorage: they must hold for every pane
  // and every engine, and the backend is what enforces them.
  useEffect(() => {
    api.settings().then((s: any) => {
      setCap(s?.monthly_spend_cap_usd ? String(s.monthly_spend_cap_usd) : "");
      setDiskFloor(s?.min_free_gb != null ? String(s.min_free_gb) : "");
      setRamFloor(s?.min_free_ram_gb != null ? String(s.min_free_ram_gb) : "");
      setSerialize(s?.serialize_local_jobs !== false);
      const b = s?.usage_budgets || {};
      setBudgets({ session: b.session ? String(b.session) : "", daily: b.daily ? String(b.daily) : "",
                   weekly: b.weekly ? String(b.weekly) : "" });
    }).catch(() => {});
  }, []);

  // Default the breakdown to the biggest spender — that is the one worth looking at.
  useEffect(() => {
    if (!project && spend?.top?.length) setProject(spend.top[0].project);
  }, [spend, project]);

  const loadBd = useCallback(() => {
    if (!project) return;
    setLoading(true); setErr("");
    api.spendBreakdown(project)
      .then((r) => setBd(r as Breakdown))
      .catch((e: any) => setErr(e?.message || "could not read that project's transcript"))
      .finally(() => setLoading(false));
  }, [project]);

  // A transcript is re-read when the tab is open, and only while it is visible. Parsing one is
  // seconds of work on a long session, so it polls slowly and never in a hidden window.
  useEffect(() => { if (tab === "tools") return pollWhileVisible(loadBd, 30000); }, [tab, loadBd]);

  const maxModel = useMemo(() => Math.max(1, ...Object.values(spend?.by_model || {})), [spend]);
  const maxDay = useMemo(() => Math.max(1, ...Object.values(spend?.days || {})), [spend]);
  const maxTool = useMemo(() => Math.max(1, ...(bd?.by_tool || []).map((t) => t.cost)), [bd]);

  async function saveLimits() {
    setSaving(true); setSaved("");
    try {
      const patch: any = {
        monthly_spend_cap_usd: Number(cap) || 0,
        min_free_gb: Number(diskFloor) || 0,
        min_free_ram_gb: Number(ramFloor) || 0,
        serialize_local_jobs: serialize,
        usage_budgets: Object.fromEntries(
          Object.entries(budgets).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v)])),
      };
      await api.updateSettings(patch);
      setSaved("saved");
      onChanged?.();
    } catch (e: any) {
      setErr(e?.message || "could not save");
    } finally { setSaving(false); }
  }

  const capState = spend?.cap;
  const days = Object.entries(spend?.days || {});

  return (
    <div className="card p-4 mt-4">
      <div className="flex items-center gap-2 mb-3">
        <Coins size={16} className="text-brand shrink-0" />
        <span className="text-sm font-semibold">Cost &amp; policy</span>
        {capState?.enabled && (
          <span className={cls("chip !text-[10px]", capState.over ? "text-danger border-danger/40" : "text-muted")}>
            cap {usd(capState.limit)} · {capState.over ? "reached — sends are blocked" : `${usd(capState.spent)} used`}
          </span>
        )}
        <div className="ml-auto flex rounded-md border border-line overflow-hidden">
          {([["cost", "What it cost"], ["tools", "What it bought"], ["limits", "Limits"]] as const).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
              className={cls("px-2.5 py-1 text-[11px] transition-colors",
                tab === id ? "bg-brand-600 text-white" : "text-muted hover:bg-panel2")}>{label}</button>
          ))}
        </div>
      </div>

      {tab === "cost" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[["This month", spend?.month ?? spend?.total], ["All time", spend?.total],
              ["Today", spend?.today], ["Turns banked", spend?.turns]].map(([label, v], i) => (
              <div key={String(label)} className="rounded-lg border border-line bg-panel2/40 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-muted">{String(label)}</div>
                <div className={cls("text-lg font-semibold", i === 3 && "text-text")}>
                  {i === 3 ? (Number(v) || 0).toLocaleString() : usd(v as number)}
                </div>
              </div>
            ))}
          </div>

          <p className="text-[10px] text-muted/70 leading-snug">
            The CLI's list price for the same work, priced per turn from each process's running
            total — so a respawn or a retry cannot double it. On a subscription this is not what
            you pay; it is what makes two models, two efforts or two ways of asking comparable.
            {" "}A model with no rate in the card is listed as <em>not priced</em> rather than $0.
          </p>

          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <div className="text-[11px] font-medium text-muted mb-1.5">By model</div>
              {Object.keys(spend?.by_model || {}).length === 0 && <div className="text-xs text-muted/60">nothing banked yet</div>}
              <div className="space-y-1.5">
                {Object.entries(spend?.by_model || {}).map(([m, v]) => (
                  <div key={m}>
                    <div className="flex justify-between text-[11px] gap-2">
                      <span className="truncate">{m}</span><span className="tabular-nums text-muted shrink-0">{usd(v)}</span>
                    </div>
                    <Bar value={v} max={maxModel} />
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="text-[11px] font-medium text-muted mb-1.5">Biggest projects</div>
              <div className="space-y-1.5">
                {(spend?.top || []).slice(0, 6).map((p) => (
                  <div key={p.project} className="flex justify-between text-[11px] gap-2">
                    <span className="truncate" title={p.project}>{short(p.project)}</span>
                    <span className="tabular-nums text-muted shrink-0">{usd(p.cost)} · {p.turns}t</span>
                  </div>
                ))}
                {(spend?.top || []).length === 0 && <div className="text-xs text-muted/60">nothing banked yet</div>}
              </div>
            </div>
          </div>

          {days.length > 0 && (
            <div>
              <div className="text-[11px] font-medium text-muted mb-1.5">Last two weeks</div>
              <div className="flex items-end gap-1 h-16">
                {days.map(([d, v]) => (
                  <div key={d} className="flex-1 group relative" title={`${d}: ${usd(v)}`}>
                    <div className="bg-brand/70 rounded-t" style={{ height: `${Math.max(2, (v / maxDay) * 60)}px` }} />
                  </div>
                ))}
              </div>
              <div className="flex justify-between text-[9px] text-muted/60 mt-0.5">
                <span>{days[0]?.[0]}</span><span>{days[days.length - 1]?.[0]}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "tools" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <select className="input !py-1 text-xs flex-1 max-w-sm" value={project} onChange={(e) => setProject(e.target.value)}>
              <option value="">pick a project…</option>
              {(spend?.top || []).map((p) => (
                <option key={p.project} value={p.project}>{short(p.project)} — {usd(p.cost)}</option>
              ))}
            </select>
            <button className="btn !py-1 !px-2" onClick={loadBd} disabled={!project || loading} title="Read the transcript again">
              {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            </button>
          </div>

          {err && <div className="text-xs text-danger">{err}</div>}
          {bd?.note && <div className="text-xs text-muted">{bd.note}</div>}

          {bd && !bd.note && (
            <>
              <div className="text-[10px] text-muted/70 leading-snug">
                Every round trip re-sends the whole conversation, so the price of calling a tool is
                the price of the message that called it. Summed by tool, over {bd.round_trips.toLocaleString()} round
                trips = <span className="text-text">{usd(bd.total)}</span>.
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                <div>
                  <div className="text-[11px] font-medium text-muted mb-1.5 flex items-center gap-1"><Wrench size={11} /> By tool</div>
                  <table className="w-full text-[11px]">
                    <tbody>
                      {bd.by_tool.slice(0, 12).map((t) => (
                        <tr key={t.tool}>
                          <td className="py-0.5 pr-2 w-[38%] align-middle">
                            <div className="truncate">{t.tool}</div>
                            <Bar value={t.cost} max={maxTool} tone="bg-accent" />
                          </td>
                          <td className="tabular-nums text-muted text-right whitespace-nowrap">{t.calls.toLocaleString()}×</td>
                          <td className="tabular-nums text-right whitespace-nowrap">{usd(t.cost)}</td>
                          <td className="tabular-nums text-muted text-right whitespace-nowrap w-12">{t.percent}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="space-y-3">
                  <div>
                    <div className="text-[11px] font-medium text-muted mb-1.5">By kind of round trip</div>
                    {bd.by_kind.map((k) => (
                      <div key={k.kind} className="mb-1.5">
                        <div className="flex justify-between text-[11px]">
                          <span className="capitalize">{k.kind}</span>
                          <span className="tabular-nums text-muted">{usd(k.cost)} · {k.percent}%</span>
                        </div>
                        <Bar value={k.cost} max={Math.max(1, ...bd.by_kind.map((x) => x.cost))} tone="bg-warn" />
                      </div>
                    ))}
                    <p className="text-[10px] text-muted/60 leading-snug mt-1">
                      "Deliberation" is a message that neither called a tool nor ended the turn — the
                      thinking immediately before an action. It is pure overhead on a large context.
                    </p>
                  </div>
                  <div>
                    <div className="text-[11px] font-medium text-muted mb-1.5 flex items-center gap-1"><Gauge size={11} /> Where the context sat</div>
                    {bd.context.spread.map((b) => (
                      <div key={b.label} className="flex items-center gap-2 text-[10px] mb-0.5">
                        <span className="w-16 text-muted shrink-0">{b.label}</span>
                        <div className="flex-1"><Bar value={b.messages} max={Math.max(1, ...bd.context.spread.map((s) => s.messages))} tone="bg-danger/70" /></div>
                        <span className="tabular-nums text-muted w-10 text-right shrink-0">{b.percent}%</span>
                      </div>
                    ))}
                    <p className="text-[10px] text-muted/60 leading-snug mt-1">
                      Cache reads are billed per message, so the upper bands are where the money is.
                    </p>
                  </div>
                  {bd.by_model.some((m) => m.basis === "unknown" || m.basis === "none") && (
                    <div className="text-[10px] text-warn/90 flex gap-1.5">
                      <AlertTriangle size={11} className="shrink-0 mt-0.5" />
                      <span>
                        {bd.by_model.filter((m) => m.basis === "unknown" || m.basis === "none")
                          .map((m) => m.model).join(", ")}{" "}
                        has no rate in the Studio's card, so its cost reads $0 while its tokens are
                        real. Add a rate under <span className="font-mono">pricing_overrides</span> to price it.
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "limits" && (
        <div className="space-y-4 max-w-2xl">
          <p className="text-[11px] text-muted leading-snug">
            Agent turns had <strong className="text-text">no cost guard at all</strong>: the app's only
            cap was checked against paid asset-generation jobs, and agent spend — the larger number by
            far — was covered by nothing. This is that guard, and it covers every engine the Studio
            drives, because they all reach the session layer through one door.
          </p>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="label">Monthly cap (USD)</label>
              <input className="input !py-1.5 text-sm" inputMode="decimal" placeholder="0 = off"
                value={cap} onChange={(e) => setCap(e.target.value)} />
              <p className="text-[10px] text-muted/70 mt-1">
                Blocks a send once this month's banked spend reaches it. 0 disables the cap.
              </p>
            </div>
            <div>
              <label className="label">Token budgets</label>
              {(["session", "daily", "weekly"] as const).map((k) => (
                <div key={k} className="flex items-center gap-2 mb-1">
                  <span className="text-[11px] text-muted w-14 capitalize">{k}</span>
                  <input className="input !py-1 text-xs flex-1" inputMode="numeric" placeholder="off"
                    value={budgets[k] || ""} onChange={(e) => setBudgets((b) => ({ ...b, [k]: e.target.value }))} />
                </div>
              ))}
              <p className="text-[10px] text-muted/70 mt-1">
                Output tokens over a rolling window, shown as a gauge. Informational — the money cap
                is the one that blocks.
              </p>
            </div>
          </div>

          {(spend?.budgets || []).length > 0 && (
            <div className="space-y-1.5">
              {(spend?.budgets || []).map((b) => (
                <div key={b.key}>
                  <div className="flex justify-between text-[11px]">
                    <span>{b.label}</span>
                    <span className="tabular-nums text-muted">
                      {b.used.toLocaleString()} / {b.limit.toLocaleString()} ({b.percent}%)
                    </span>
                  </div>
                  <Bar value={b.percent} max={100} tone={b.percent >= 90 ? "bg-danger" : b.percent >= 70 ? "bg-warn" : "bg-brand"} />
                </div>
              ))}
            </div>
          )}

          {/* WHAT KEEPS THE PC USABLE. The queue enforced all three of these before they were
              visible anywhere, so its refusal sentence — "lower min_free_ram_gb in Settings" —
              pointed at a setting that did not exist in the UI. A guard nobody can find is a
              guard nobody can tune. */}
          <div className="border-t border-line pt-3 space-y-3">
            <div>
              <div className="text-sm font-medium">Machine guards</div>
              <p className="text-[10px] text-muted/70 leading-snug mt-0.5">
                What stops a <strong className="text-text">local</strong> job. A local generator runs
                on this PC's own card, so the failure it prevents is not a bill — it is the whole
                desktop going unresponsive, because a second heavyweight job pushes everything else
                into the page file. 0 turns a floor off.
              </p>
            </div>
            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <label className="label">Free RAM floor (GB)</label>
                <input className="input !py-1.5 text-sm" inputMode="decimal" placeholder="0 = off"
                  value={ramFloor} onChange={(e) => setRamFloor(e.target.value)} />
                <p className="text-[10px] text-muted/70 mt-1">
                  A local job is refused below this much free memory. One H3 video clip holds roughly
                  30 GB of private working set on its own.
                </p>
              </div>
              <div>
                <label className="label">Free disk floor (GB)</label>
                <input className="input !py-1.5 text-sm" inputMode="decimal" placeholder="0 = off"
                  value={diskFloor} onChange={(e) => setDiskFloor(e.target.value)} />
                <p className="text-[10px] text-muted/70 mt-1">
                  Every generation is refused below this much free space, local or remote.
                </p>
              </div>
            </div>
            <label className="flex items-start gap-2 cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={serialize}
                onChange={(e) => setSerialize(e.target.checked)} />
              <span className="text-[11px] leading-snug">
                <span className="text-text">One local job at a time</span>
                <span className="block text-[10px] text-muted/70">
                  Two local jobs share one card, and the second makes ComfyUI offload the first model
                  into host RAM — slower, and exactly what the RAM floor above guards against.
                  Remote jobs still overlap. Off is for someone splitting two GPUs by hand.
                </span>
              </span>
            </label>
          </div>

          <div className="flex items-center gap-2">
            <button className="btn-primary !py-1.5 !px-3 text-sm" onClick={saveLimits} disabled={saving}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : null} Save limits
            </button>
            {saved && <span className="text-xs text-ok">{saved}</span>}
          </div>

          <div className="text-[10px] text-muted/70 border-t border-line pt-2 leading-snug">
            <strong className="text-muted">Policy, not yet enforced:</strong> per-task routing — send
            routine work (reading, grepping, summarising, test triage) to a local model and reserve a
            front-line model for architecture — needs a local engine configured under
            Settings → Models (the Ollama and LM Studio presets are already there) and a rule set this
            panel does not yet write. It is listed here so the switch has an obvious home.
          </div>
        </div>
      )}
    </div>
  );
}
