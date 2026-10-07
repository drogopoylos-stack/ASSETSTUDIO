import { Ban, CheckCircle2, Lightbulb, Loader2, XCircle } from "lucide-react";
import { api } from "../api/client";
import type { Job } from "../types";
import { Bar, STATUS_COLOR, cls, fmtCost, fmtEta } from "./ui";

export default function JobProgress({ job, onCancel }: { job: Job; onCancel?: () => void }) {
  const running = job.status === "running" || job.status === "queued";
  const Icon =
    job.status === "succeeded" ? CheckCircle2 : job.status === "failed" ? XCircle : job.status === "canceled" ? Ban : Loader2;

  return (
    <div className="card p-3">
      <div className="flex items-center gap-2">
        <Icon size={16} className={cls(STATUS_COLOR[job.status], running && "animate-spin")} />
        <span className="text-sm font-medium truncate flex-1">{job.label || `${job.stage} · ${job.provider_id}`}</span>
        <span className={cls("text-xs font-mono", STATUS_COLOR[job.status])}>{job.status}</span>
        {running && onCancel && (
          <button className="btn-ghost !px-2 !py-1 text-danger" title="Cancel"
            onClick={async () => { try { await api.cancelJob(job.id); } catch {} onCancel(); }}>
            <Ban size={14} />
          </button>
        )}
      </div>

      <div className="mt-2 relative overflow-hidden rounded-full">
        <Bar value={job.progress} />
        {running && <div className="shimmer absolute inset-0 rounded-full pointer-events-none" />}
      </div>

      <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted">
        <span className="truncate">{job.step || "…"}</span>
        <span className="font-mono">
          {(job.progress * 100).toFixed(0)}%
          {running && job.eta_seconds ? ` · ETA ${fmtEta(job.eta_seconds)}` : ""}
          {job.cost ? ` · ${fmtCost(job.cost)}` : ""}
        </span>
      </div>

      {job.status === "failed" && (job.error_hint || job.error) && (
        <div className="mt-2 space-y-1.5">
          {job.error_hint && (
            <div className="text-xs text-warn bg-warn/10 border border-warn/30 rounded p-2 flex gap-2">
              <Lightbulb size={14} className="mt-0.5 shrink-0" /> {job.error_hint}
            </div>
          )}
          {job.error && (
            <details className="text-[11px] text-danger font-mono bg-danger/10 rounded p-2">
              <summary className="cursor-pointer text-muted select-none">Error details</summary>
              <div className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap">
                {job.error.split("\n").slice(-5).join("\n")}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
