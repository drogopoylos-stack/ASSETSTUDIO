import { useEffect, useMemo, useState } from "react";
import {
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  ListChecks,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { api } from "../api/client";
import { activeJobs, useStore } from "../store/useStore";
import type { Job, JobStatus } from "../types";
import { Empty, Section, Spinner, STATUS_COLOR, cls, fmtCost, timeAgo } from "../components/ui";
import JobProgress from "../components/JobProgress";

type Filter = "all" | JobStatus;

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "succeeded", label: "Succeeded" },
  { id: "failed", label: "Failed" },
  { id: "canceled", label: "Canceled" },
];

function StatusIcon({ status }: { status: JobStatus }) {
  const cl = cls("shrink-0", STATUS_COLOR[status]);
  if (status === "succeeded") return <CheckCircle2 size={15} className={cl} />;
  if (status === "failed") return <XCircle size={15} className={cl} />;
  if (status === "canceled") return <Ban size={15} className={cl} />;
  if (status === "running") return <Loader2 size={15} className={cls(cl, "animate-spin")} />;
  return <Clock size={15} className={cl} />;
}

function HistoryRow({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  const setTab = useStore((s) => s.setTab);
  const outputs = job.outputs || [];

  return (
    <div className="border-b border-line last:border-b-0">
      <button
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-panel2 transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-muted shrink-0">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <StatusIcon status={job.status} />
        <span className={cls("text-xs font-mono w-20 shrink-0", STATUS_COLOR[job.status])}>
          {job.status}
        </span>
        <span className="chip shrink-0 hidden sm:inline-flex">{job.stage}</span>
        <span className="text-sm truncate flex-1 min-w-0">
          {job.label || `${job.stage} · ${job.provider_id}`}
        </span>
        <span className="text-[11px] text-muted font-mono truncate hidden md:block w-40 text-right">
          {job.provider_id}
        </span>
        <span className="text-[11px] text-muted font-mono w-14 text-right shrink-0">
          {fmtCost(job.cost)}
        </span>
        <span className="text-[11px] text-muted w-20 text-right shrink-0">
          {timeAgo(job.created_at)}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 space-y-3 bg-panel2/40">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px]">
            <div>
              <div className="text-muted">Provider</div>
              <div className="font-mono truncate" title={job.provider_id}>{job.provider_id}</div>
            </div>
            <div>
              <div className="text-muted">Stage</div>
              <div>{job.stage}</div>
            </div>
            <div>
              <div className="text-muted">Step</div>
              <div className="truncate" title={job.step}>{job.step || "—"}</div>
            </div>
            <div>
              <div className="text-muted">Progress</div>
              <div className="font-mono">{(job.progress * 100).toFixed(0)}%</div>
            </div>
          </div>

          {job.status === "failed" && job.error && (
            <div>
              <div className="text-[11px] text-muted mb-1">Error</div>
              <pre className="text-[11px] text-danger font-mono bg-danger/10 rounded-lg p-2 max-h-40 overflow-auto whitespace-pre-wrap">
                {job.error}
              </pre>
            </div>
          )}

          {job.logs && job.logs.length > 0 && (
            <div>
              <div className="text-[11px] text-muted mb-1">Logs</div>
              <pre className="text-[11px] text-muted font-mono bg-bg/60 border border-line rounded-lg p-2 max-h-48 overflow-auto whitespace-pre-wrap">
                {job.logs.join("\n")}
              </pre>
            </div>
          )}

          {outputs.length > 0 && (
            <div>
              <div className="text-[11px] text-muted mb-1">
                Output{outputs.length > 1 ? "s" : ""} ({outputs.length})
              </div>
              <div className="flex flex-wrap gap-1.5">
                {outputs.map((a) => (
                  <button
                    key={a.id}
                    className="chip hover:border-brand hover:text-text transition-colors"
                    title={`Open ${a.name} in Catalog`}
                    onClick={() => setTab("catalog")}
                  >
                    {a.type}: <span className="truncate max-w-[160px]">{a.name}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {outputs.length === 0 && job.status === "succeeded" && (
            <div className="text-[11px] text-muted">No output assets recorded.</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Jobs() {
  const jobs = useStore((s) => s.jobs);
  const [history, setHistory] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");

  const active = useMemo(() => activeJobs(jobs), [jobs]);
  // A signature that changes whenever an active job's identity/status flips,
  // so history refreshes when something transitions out of queued/running.
  const activeSig = useMemo(
    () => active.map((j) => `${j.id}:${j.status}`).sort().join("|"),
    [active]
  );

  async function loadHistory() {
    setLoading(true);
    try {
      const list = await api.jobs(undefined, 100);
      setHistory(list);
    } catch (e: any) {
      useStore.getState().toast(`Failed to load job history: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSig]);

  // Merge server history with any live jobs in the store (live wins — fresher
  // logs/outputs/status), so finished jobs appear immediately without a refetch race.
  const merged = useMemo(() => {
    const byId = new Map<string, Job>();
    for (const j of history) byId.set(j.id, j);
    for (const j of Object.values(jobs)) byId.set(j.id, j);
    return Array.from(byId.values()).sort((a, b) => b.created_at - a.created_at);
  }, [history, jobs]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const j of merged) c[j.status] = (c[j.status] || 0) + 1;
    return c;
  }, [merged]);

  const filtered = useMemo(
    () => (filter === "all" ? merged : merged.filter((j) => j.status === filter)),
    [merged, filter]
  );

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <ListChecks size={18} className="text-brand" /> Jobs
          </h2>
          <p className="text-sm text-muted mt-0.5">
            Live queue and full run history. Cancel active jobs or inspect logs and outputs.
          </p>
        </div>
        <button className="btn-ghost text-xs" onClick={loadHistory} disabled={loading}>
          <RefreshCw size={14} className={cls(loading && "animate-spin")} /> Refresh
        </button>
      </div>

      <Section
        title="Active"
        desc="Queued and running jobs update in real time"
        right={<span className="chip">{active.length} active</span>}
      >
        {active.length === 0 ? (
          <Empty
            icon={<Loader2 size={28} className="text-muted/50" />}
            label="No active jobs — nothing in the queue"
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {active.map((job) => (
              <JobProgress key={job.id} job={job} onCancel={() => {}} />
            ))}
          </div>
        )}
      </Section>

      <Section
        title="History"
        desc="Most recent 100 jobs"
        right={
          <div className="flex items-center gap-2">
            {loading && <Spinner size={14} />}
            <select
              className="input !w-auto !py-1.5 text-xs"
              value={filter}
              onChange={(e) => setFilter(e.target.value as Filter)}
            >
              {FILTERS.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                  {f.id !== "all" && counts[f.id] ? ` (${counts[f.id]})` : ""}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {merged.length === 0 ? (
          loading ? (
            <div className="flex items-center justify-center py-12">
              <Spinner size={22} />
            </div>
          ) : (
            <Empty
              icon={<ListChecks size={28} className="text-muted/50" />}
              label="No jobs yet — generate an asset to get started"
            />
          )
        ) : filtered.length === 0 ? (
          <Empty label={`No ${filter} jobs`} />
        ) : (
          <div className="-mx-1 rounded-lg border border-line overflow-hidden bg-panel">
            {filtered.map((job) => (
              <HistoryRow key={job.id} job={job} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
