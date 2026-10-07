/**
 * Output tokens per second for this workspace — live while the model writes, and a comparison
 * of every model and effort level once turns have finished.
 *
 * The number is deliberately GENERATION speed, not wall-clock: a turn that waits two minutes on a
 * test suite would otherwise make the model look slow, which is useless for "is Fable faster than
 * Opus at max effort". The backend clocks only the intervals where tokens were being produced.
 */
import { useEffect, useRef, useState } from "react";
import { Gauge, Loader2 } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import { cls, pollWhileVisible } from "./ui";

type Summary = Awaited<ReturnType<typeof api.missionSpeed>>;

const short = (m: string) =>
  (m || "").split("[")[0].replace(/^claude-/, "").replace(/-2025\d{4}$/, "") || "—";
const fmtTps = (n: number) => (n >= 100 ? Math.round(n) : n.toFixed(1));
const fmtTok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

export function SpeedMeter({ projectId, className, cli }: {
  projectId: string; className?: string;
  /** the console look: no button chrome, just the number — the panel still opens on click */
  cli?: boolean;
}) {
  const [tps, setTps] = useState(0);
  const [working, setWorking] = useState(false);
  const [model, setModel] = useState("");
  const [sum, setSum] = useState<Summary | null>(null);
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  // the same WS push the feed uses, so the number moves with the tokens instead of a poll behind
  const push = useStore((s) => s.ccLive[projectId]);
  const wsAt = useRef(0);

  useEffect(() => {
    const s: any = push?.state;
    if (!s) return;
    wsAt.current = performance.now();
    setWorking(!!s.working);
    setTps(s.working ? (s.tps || 0) : 0);
    if (s.model) setModel(s.model);
  }, [push]);

  // history (and a fallback for the live number if WS pushes are not arriving)
  useEffect(() => {
    if (!projectId) return;
    const load = () => api.missionSpeed(projectId, all).then((r) => {
      setSum(r);
      if (performance.now() - wsAt.current > 2500) {
        setWorking(r.live.working);
        setTps(r.live.working ? r.live.tps : 0);
        if (r.live.model) setModel(r.live.model);
      }
    }).catch(() => {});
    load();
    return pollWhileVisible(load, open ? 3000 : 15000);
  }, [projectId, open, all]);

  // when idle, show what this workspace's current model averages — still useful at a glance
  const best = sum?.by_model?.[0];
  const idleTps = best?.tps || 0;
  const shown = working ? tps : idleTps;
  if (!shown && !sum?.turns) return null;

  // A terminal has no chips. In the CLI look the border and fill come off and it is text on the
  // row like everything else — still a button, so the comparison panel is one click away.
  const tone = cli
    ? (working ? "text-brand border-transparent" : "text-muted border-transparent")
    : working
      ? "text-brand border-brand/40 bg-brand/10"
      : "text-muted border-line bg-panel2/60";

  return (
    <div className={cls("relative shrink-0", className)}>
      <button
        className={cls("flex items-center gap-1 px-1.5 h-5 rounded border text-[10px] font-mono select-none hover:border-brand/50", tone)}
        title={working
          ? `${fmtTps(tps)} output tokens/second right now${model ? ` · ${short(model)}` : ""}\nGeneration time only — waiting on a tool does not count.`
          : `Average output speed per model and effort for this workspace.\nClick to compare.`}
        onClick={() => setOpen((v) => !v)}>
        {working ? <Loader2 size={10} className="animate-spin" /> : <Gauge size={10} />}
        {fmtTps(shown)} <span className="opacity-60">tok/s</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-[60]" onClick={() => setOpen(false)} />
          <div className="absolute top-full right-0 mt-1 z-[61] card p-2 w-[30rem] max-h-[26rem] overflow-auto shadow-card">
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-[11px] uppercase tracking-wide text-muted">Model speed</div>
              <button className="text-[10px] text-muted hover:text-text"
                onClick={() => setAll((v) => !v)}>
                {all ? "this workspace" : "all workspaces"} ▾
              </button>
            </div>

            {!sum?.by_model?.length && (
              <div className="text-xs text-muted px-1 py-2">
                No finished turns measured yet. Send a message and the speed appears here.
              </div>
            )}

            {!!sum?.by_model?.length && (
              <table className="w-full text-[11px]">
                <thead className="text-muted">
                  <tr className="text-left">
                    <th className="font-normal py-1">model</th>
                    <th className="font-normal">effort</th>
                    <th className="font-normal text-right">tok/s</th>
                    <th className="font-normal text-right">best</th>
                    <th className="font-normal text-right">turns</th>
                    <th className="font-normal text-right">tokens</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {sum.by_model.map((g, i) => (
                    <tr key={`${g.model}-${g.effort}`} className="border-t border-line/60">
                      <td className="py-1 pr-2">{g.model}</td>
                      <td className="pr-2 text-muted">{g.effort}</td>
                      <td className={cls("text-right font-semibold", i === 0 && "text-ok")}>{fmtTps(g.tps)}</td>
                      <td className="text-right text-muted">{fmtTps(g.best)}</td>
                      <td className="text-right text-muted">{g.turns}</td>
                      <td className="text-right text-muted">{fmtTok(g.tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {!!sum?.recent?.length && (
              <>
                <div className="text-[11px] uppercase tracking-wide text-muted mt-3 mb-1">Recent turns</div>
                <div className="space-y-0.5 font-mono text-[10px]">
                  {sum.recent.slice(0, 10).map((r, i) => (
                    <div key={i} className="flex items-center gap-2 text-muted">
                      <span className="w-24 truncate text-text/80">{r.model_short}</span>
                      <span className="w-12 truncate">{r.effort}</span>
                      <span className="w-14 text-right text-text/90">{fmtTps(r.tps)} t/s</span>
                      <span className="w-14 text-right">{fmtTok(r.tokens)} tok</span>
                      <span className="w-12 text-right">{r.gen_s}s gen</span>
                      <span className="w-14 text-right opacity-60">{r.wall_s}s wall</span>
                    </div>
                  ))}
                </div>
              </>
            )}

            <p className="text-[10px] text-muted/80 mt-2 leading-relaxed">
              Generation time only — seconds spent waiting on a tool are not counted, so this
              measures the model rather than your test suite. “wall” is the whole turn for comparison.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
