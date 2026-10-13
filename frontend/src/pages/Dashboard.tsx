import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  ChevronDown,
  ChevronRight,
  Bot,
  Coins,
  Cpu,
  GitCompareArrows,
  Image as ImageIcon,
  Images,
  Layers,
  Loader2,
  MemoryStick,
  Monitor,
  Package,
  RefreshCw,
  Settings as SettingsIcon,
  Sparkles,
  Workflow,
  Zap,
} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { TabId } from "../store/useStore";
import type { Asset, ProviderInfo, StageType } from "../types";
import { STAGES } from "../types";
import AssetCard from "../components/AssetCard";
import { Bar, Empty, Section, Spinner, cls, fmtCost, pollWhileVisible, useSticky } from "../components/ui";
import CostPanel from "../components/CostPanel";
import PlanPanel from "../components/PlanPanel";

interface Summary {
  total_assets: number;
  total_cost: number;
  non_commercial_assets: number;
  jobs_running: number;
  by_stage?: Record<string, number>;
}

interface SysInfo {
  version?: string;
  keychain_backend?: string;
  provider_load_errors?: Record<string, string>;
}

type IconType = typeof Cpu;

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  tone,
  alert,
}: {
  icon: IconType;
  label: string;
  value: React.ReactNode;
  sub?: string;
  tone?: string;
  alert?: boolean;
}) {
  return (
    <div className={cls("card p-4 flex items-start gap-3", alert && "border-warn/50")}>
      <div className={cls("rounded-lg bg-panel2 p-2", tone || "text-brand")}>
        <Icon size={20} />
      </div>
      <div className="min-w-0">
        <div className="text-xs text-muted">{label}</div>
        <div className={cls("text-2xl font-semibold leading-tight tabular-nums", tone)}>{value}</div>
        {sub && <div className="text-[11px] text-muted mt-0.5 truncate">{sub}</div>}
      </div>
    </div>
  );
}

function MachineCard({
  icon: Icon,
  title,
  primary,
  value,
  detail,
}: {
  icon: IconType;
  title: string;
  primary: string;
  value: number; // 0..1
  detail?: string;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={16} className="text-brand" />
        <span className="text-sm font-medium truncate flex-1">{title}</span>
        <span className="text-sm font-mono tabular-nums text-muted">{primary}</span>
      </div>
      <Bar value={value} />
      {detail && <div className="mt-1.5 text-[11px] text-muted truncate">{detail}</div>}
    </div>
  );
}

const QUICK_ACTIONS: { tab: TabId; label: string; desc: string; icon: IconType }[] = [
  { tab: "studio2d", label: "2D Studio", desc: "Text → image", icon: ImageIcon },
  { tab: "studio3d", label: "3D Studio", desc: "Image → mesh", icon: Boxes },
  { tab: "pipeline", label: "Pipeline", desc: "Chain stages", icon: Workflow },
  { tab: "compare", label: "Compare", desc: "Provider A/B", icon: GitCompareArrows },
  { tab: "catalog", label: "Catalog", desc: "Browse assets", icon: Layers },
  { tab: "settings", label: "Settings", desc: "Keys & config", icon: SettingsIcon },
];

export default function Dashboard() {
  const setTab = useStore((s) => s.setTab);
  const stats = useStore((s) => s.stats);
  const recentLen = useStore((s) => s.recentAssets.length);
  const toast = useStore((s) => s.toast);

  const [summary, setSummary] = useState<Summary | null>(null);
  // Agent spend and live fan-out. Polled beside the catalog summary rather than in it: the two
  // live in different stores and one must never wait on the other.
  const [spend, setSpend] = useState<Awaited<ReturnType<typeof api.agentSpend>> | null>(null);
  const [agentsRunning, setAgentsRunning] = useState(0);
  // A spinner that never stops reads as broken, not as pending. The endpoint is newer than a
  // backend that has not been restarted yet, so that case gets a dash and the reason.
  const [spendErr, setSpendErr] = useState("");
  useEffect(() => {
    // Both requests are awaited together so the next tick waits for the slower of the two —
    // a poll must never leave more connections open than it has ticks.
    const tick = () => Promise.all([
      api.agentSpend().then((d) => { setSpend(d); setSpendErr(""); })
        .catch((e) => setSpendErr(/404/.test(e?.message || "") ? "restart the backend to record it"
                                                              : "not available")),
      api.liveStatus().then((r) => setAgentsRunning(r.running_agents_total || 0)).catch(() => {}),
    ]);
    return pollWhileVisible(tick, 10000);
  }, []);
  const [info, setInfo] = useState<SysInfo | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [recent, setRecent] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorsOpen, setErrorsOpen] = useState(true);
  // Closed by default: those dollars are an estimate of a bill nobody sends.
  const [costOpen, setCostOpen] = useSticky("dash.cost", false);

  // System info + providers load once.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [inf, provs] = await Promise.all([api.systemInfo(), api.providers()]);
        if (!alive) return;
        setInfo(inf);
        setProviders(provs);
      } catch (e: any) {
        useStore.getState().toast(`Failed to load system info: ${e.message}`, "danger");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Summary + recent assets refetch whenever a new asset streams in.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [sum, assets] = await Promise.all([
          api.catalogSummary(),
          api.assets({ limit: 12 }),
        ]);
        if (!alive) return;
        setSummary(sum);
        setRecent(assets);
      } catch (e: any) {
        if (alive) toast(`Failed to load overview: ${e.message}`, "danger");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [recentLen, toast]);

  const gpu = stats?.gpus?.[0];

  const providersByStage = useMemo(() => {
    const map = new Map<StageType, { total: number; available: number }>();
    for (const { id } of STAGES) map.set(id, { total: 0, available: 0 });
    for (const p of providers || []) {
      const e = map.get(p.stage) || { total: 0, available: 0 };
      e.total += 1;
      if (p.available) e.available += 1;
      map.set(p.stage, e);
    }
    return map;
  }, [providers]);

  const loadErrors = info?.provider_load_errors || {};
  const loadErrorKeys = Object.keys(loadErrors);
  const nonCommercial = summary?.non_commercial_assets ?? 0;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Sparkles size={22} className="text-brand" /> Asset Studio
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Local-first game-asset generator · overview
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {info?.version ? (
            <span className="chip">v{info.version}</span>
          ) : (
            <span className="chip text-muted">v—</span>
          )}
          {info?.keychain_backend && (
            <span className="chip">keychain: {info.keychain_backend}</span>
          )}
        </div>
      </div>

      {/* Headline stats.

          THE MONEY USED TO BE HERE, AND IT WAS THE WRONG HEADLINE. Two of the five cards were
          dollars: the asset catalog's generation cost, and a list price for every agent turn.
          The second one is not billed at all on a Claude Max or a ChatGPT Plus plan, so the
          screen opened with a large number that nobody pays. What a subscription actually raises
          is when it renews and how much of the window is left — the panel below. The dollars are
          still computed and still exact; they now sit in a section that says what they are. */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        <StatCard
          icon={Package}
          label="Total assets"
          value={loading && !summary ? <Spinner size={20} /> : (summary?.total_assets ?? 0).toLocaleString()}
          sub="images & meshes in the catalog"
        />
        <StatCard
          icon={Loader2}
          label={agentsRunning ? "Agents running" : "Jobs running"}
          value={agentsRunning || (summary?.jobs_running ?? 0)}
          tone={(agentsRunning || (summary?.jobs_running ?? 0)) > 0 ? "text-brand" : undefined}
          sub={agentsRunning ? "delegated right now" : "nothing queued"}
        />
        <StatCard
          icon={AlertTriangle}
          label="Non-commercial assets"
          value={
            <span className="inline-flex items-center gap-1.5">
              {nonCommercial > 0 && <AlertTriangle size={18} />}
              {nonCommercial.toLocaleString()}
            </span>
          }
          tone={nonCommercial > 0 ? "text-warn" : "text-muted"}
          alert={nonCommercial > 0}
          sub={nonCommercial > 0 ? "review before shipping" : "all clear to ship"}
        />
      </div>

      {/* The two subscriptions, and what the turns actually went on. The real headline. */}
      <PlanPanel days={14} />

      {/* Cost estimate. Kept, collapsed, and labelled for what it is.

          Every figure in here is a rate card applied to measured tokens. On a subscription none
          of it is charged: it is what the same work would have cost through the API, which is
          useful for comparing one model against another and misleading as a bill. It opens
          closed for that reason, and the cap inside it still works — a user on API keys wants
          it, and a user on a plan should not be shown a total they do not owe. See CostPanel.tsx
          for what the detail covers. */}
      <div className="card p-0 overflow-hidden">
        <button className="w-full flex items-center gap-2 p-4 text-left" onClick={() => setCostOpen(!costOpen)}>
          {costOpen ? <ChevronDown size={16} className="text-muted" /> : <ChevronRight size={16} className="text-muted" />}
          <Coins size={16} className="text-muted" />
          <span className="font-semibold">Cost estimate</span>
          <span className="text-xs text-muted hidden sm:inline">
            list price of the work · not billed on a subscription
          </span>
          <span className="ml-auto flex items-center gap-2 text-xs">
            {summary?.total_cost ? <span className="chip">assets {fmtCost(summary.total_cost)}</span> : null}
            {spend ? <span className="chip">agents {fmtCost(spend.total)}</span>
                   : spendErr ? <span className="chip text-muted">{spendErr}</span> : null}
            {spend?.cap?.over ? <span className="chip text-warn border-warn/40">over cap</span> : null}
          </span>
        </button>
        {costOpen && (
          <div className="px-4 pb-4">
            <CostPanel spend={spend} />
          </div>
        )}
      </div>

      {/* Machine */}
      <Section
        title="Machine"
        desc="Live resource usage"
        right={
          stats ? (
            <span className="chip">{stats.cpu_cores} cores</span>
          ) : (
            <span className="chip text-muted">no telemetry</span>
          )
        }
      >
        {!stats ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {["CPU", "Memory", "GPU"].map((t) => (
              <div key={t} className="card p-4">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium">{t}</span>
                  <span className="text-muted">—</span>
                </div>
                <div className="mt-2">
                  <Bar value={0} />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <MachineCard
              icon={Cpu}
              title="CPU"
              primary={`${Math.round(stats.cpu_percent)}%`}
              value={stats.cpu_percent / 100}
              detail={`${stats.cpu_cores} cores`}
            />
            <MachineCard
              icon={MemoryStick}
              title="Memory"
              primary={`${Math.round(stats.ram_percent)}%`}
              value={stats.ram_percent / 100}
              detail={`${stats.ram_used_gb.toFixed(1)} / ${stats.ram_total_gb.toFixed(1)} GB`}
            />
            {gpu ? (
              <MachineCard
                icon={Monitor}
                title={gpu.name}
                primary={`${Math.round(gpu.util_percent)}%`}
                value={gpu.util_percent / 100}
                detail={`VRAM ${(gpu.vram_used_mb / 1024).toFixed(1)} / ${(gpu.vram_total_mb / 1024).toFixed(1)} GB${
                  gpu.temperature_c != null ? ` · ${Math.round(gpu.temperature_c)}°C` : ""
                }`}
              />
            ) : (
              <div className="card p-4">
                <div className="flex items-center gap-2 text-sm">
                  <Monitor size={16} className="text-muted" />
                  <span className="font-medium">GPU</span>
                  <span className="ml-auto text-muted">none detected</span>
                </div>
                <div className="mt-2">
                  <Bar value={0} />
                </div>
                <div className="mt-1.5 text-[11px] text-muted">Running on CPU only</div>
              </div>
            )}
          </div>
        )}
      </Section>

      {/* Quick actions */}
      <Section title="Quick actions" desc="Jump into a workflow">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {QUICK_ACTIONS.map(({ tab, label, desc, icon: Icon }) => (
            <button
              key={tab}
              onClick={() => setTab(tab)}
              className="card p-3 text-left hover:border-brand transition-colors group flex flex-col gap-2"
            >
              <span className="rounded-lg bg-panel2 p-2 w-fit text-brand group-hover:text-accent transition-colors">
                <Icon size={18} />
              </span>
              <span className="text-sm font-medium">{label}</span>
              <span className="text-[11px] text-muted">{desc}</span>
            </button>
          ))}
        </div>
      </Section>

      {/* Provider readiness */}
      <Section
        title="Provider readiness"
        desc="Available adapters per stage"
        right={
          providers ? (
            <span className="chip">
              <Zap size={11} /> {providers.filter((p) => p.available).length}/{providers.length} ready
            </span>
          ) : (
            <Spinner />
          )
        }
      >
        {!providers ? (
          <Empty icon={<Spinner size={20} />} label="Loading providers…" />
        ) : (
          <div className="flex flex-wrap gap-2">
            {STAGES.map((s) => {
              const e = providersByStage.get(s.id) || { total: 0, available: 0 };
              const ready = e.available > 0;
              const partial = ready && e.available < e.total;
              return (
                <span
                  key={s.id}
                  className={cls(
                    "chip",
                    e.total === 0 && "opacity-50",
                    ready && !partial && "text-ok border-ok/40",
                    partial && "text-warn border-warn/40",
                    e.total > 0 && !ready && "text-danger border-danger/40"
                  )}
                  title={`${s.label}: ${e.available} of ${e.total} adapters available`}
                >
                  {s.label}
                  <span className="font-mono">
                    {e.available}/{e.total} ready
                  </span>
                </span>
              );
            })}
          </div>
        )}
      </Section>

      {/* Adapter load errors */}
      {loadErrorKeys.length > 0 && (
        <div className="card border-warn/50 bg-warn/5 p-0 overflow-hidden">
          <button
            className="w-full flex items-center gap-2 p-4 text-left"
            onClick={() => setErrorsOpen((o) => !o)}
          >
            {errorsOpen ? (
              <ChevronDown size={16} className="text-warn" />
            ) : (
              <ChevronRight size={16} className="text-warn" />
            )}
            <AlertTriangle size={16} className="text-warn" />
            <span className="font-semibold text-warn">
              {loadErrorKeys.length} adapter{loadErrorKeys.length > 1 ? "s" : ""} with load errors
            </span>
            <span className="ml-auto text-xs text-muted">these providers are unavailable</span>
          </button>
          {errorsOpen && (
            <div className="px-4 pb-4 space-y-2">
              {loadErrorKeys.map((k) => (
                <div key={k} className="rounded-lg bg-panel2 border border-line p-2.5">
                  <div className="text-xs font-mono font-medium text-warn">{k}</div>
                  <div className="text-[11px] text-muted font-mono mt-1 break-words whitespace-pre-wrap">
                    {loadErrors[k]}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Recent assets */}
      <Section
        title="Recent assets"
        desc="Newest generations across all stages"
        right={
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost text-xs"
              title="Refresh"
              onClick={() => {
                setLoading(true);
                Promise.all([api.catalogSummary(), api.assets({ limit: 12 })])
                  .then(([sum, assets]) => {
                    setSummary(sum);
                    setRecent(assets);
                  })
                  .catch((e) => toast(`Refresh failed: ${e.message}`, "danger"))
                  .finally(() => setLoading(false));
              }}
            >
              <RefreshCw size={13} /> Refresh
            </button>
            <button className="btn-ghost text-xs" onClick={() => setTab("catalog")}>
              Open Catalog →
            </button>
          </div>
        }
      >
        {recent.length === 0 ? (
          <Empty
            icon={<Images size={22} />}
            label={loading ? "Loading…" : "No assets yet — generate your first one from the 2D Studio"}
          />
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {recent.map((a) => (
              <AssetCard key={a.id} asset={a} compact onClick={() => setTab("catalog")} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
