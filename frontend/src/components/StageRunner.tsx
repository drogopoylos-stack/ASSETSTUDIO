import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Play, PlugZap, Sparkles, Square } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { Asset, Job, ProviderInfo, ServiceStatus, StageType } from "../types";
import AssetCard from "./AssetCard";
import InputPicker from "./InputPicker";
import JobProgress from "./JobProgress";
import ModelViewer from "./ModelViewer";
import ParamForm from "./ParamForm";
import ProviderPicker from "./ProviderPicker";
import { Empty, Section, cls } from "./ui";

// The reusable "generate" surface shared by 2D/3D/Texture/Rig/Optimize/QA tabs.
export default function StageRunner({
  stage,
  title,
  subtitle,
  inputType,
  inputRequired,
  multiInput,
}: {
  stage: StageType;
  title: string;
  subtitle?: string;
  inputType?: "image" | "mesh" | "any";
  inputRequired?: boolean;
  multiInput?: boolean;
}) {
  const [provider, setProvider] = useState<ProviderInfo | undefined>();
  const [params, setParams] = useState<Record<string, any>>({});
  const [inputs, setInputs] = useState<string[]>([]);
  const [targetGame, setTargetGame] = useState("");
  const [jobId, setJobId] = useState<string | undefined>();
  const [outputs, setOutputs] = useState<Asset[]>([]);
  const [recent, setRecent] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [service, setService] = useState<ServiceStatus | null>(null);
  const [startingSvc, setStartingSvc] = useState(false);
  const [providerReload, setProviderReload] = useState(0); // bump to re-check provider availability

  const liveJob = useStore((s) => (jobId ? s.jobs[jobId] : undefined)) as Job | undefined;
  const recentLen = useStore((s) => s.recentAssets.length);
  const toast = useStore((s) => s.toast);
  const setTab = useStore((s) => s.setTab);

  useEffect(() => {
    api.assets({ stage, limit: 24 }).then(setRecent).catch(() => {});
  }, [stage, recentLen]);

  // check whether the selected provider needs a local server, and its status
  useEffect(() => {
    setService(null);
    if (!provider || provider.kind !== "local") return;
    let alive = true;
    api.serviceForProvider(provider.id).then((r) => alive && setService(r.service)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [provider?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function startServer() {
    if (!service) return;
    setStartingSvc(true);
    try {
      const st = await api.startService(service.id, true);
      setService(st);
      setProviderReload((n) => n + 1); // provider should flip to available once the server is up
      toast(st.reachable ? `${st.name} is running` : st.last_error || `Could not start ${st.name}`, st.reachable ? "ok" : "danger");
    } catch (e: any) {
      toast(`Start failed: ${e.message}`, "danger");
    } finally {
      setStartingSvc(false);
    }
  }

  async function stopServer() {
    if (!service) return;
    setStartingSvc(true);
    try {
      const st = await api.stopService(service.id);
      setService(st);
      setProviderReload((n) => n + 1); // provider goes unavailable; GPU memory is released
      toast(`${st.name} stopped — GPU memory freed`, "ok");
    } catch (e: any) {
      toast(`Stop failed: ${e.message}`, "danger");
    } finally {
      setStartingSvc(false);
    }
  }

  // when the tracked job finishes, pull its outputs
  useEffect(() => {
    if (liveJob && (liveJob.status === "succeeded" || liveJob.status === "failed")) {
      setBusy(false);
      if (liveJob.status === "succeeded") {
        api.job(liveJob.id).then((j) => setOutputs(j.outputs)).catch(() => {});
      } else if (liveJob.error) {
        toast(`Job failed: ${liveJob.error.split("\n").pop()}`, "danger");
      }
    }
  }, [liveJob?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Allow Generate when the provider is available, OR when it's a local provider whose
  // server is merely stopped-but-configured (the job auto-starts it before running).
  const canRun = useMemo(
    () =>
      (provider?.available || (provider?.kind === "local" && !!service?.configured)) &&
      (!inputRequired || inputs.length > 0) &&
      !busy,
    [provider, inputs, inputRequired, busy, service]
  );

  async function run() {
    if (!provider) return;
    setBusy(true);
    setOutputs([]);
    try {
      const job = await api.submitJob({
        stage,
        provider_id: provider.id,
        params,
        inputs,
        target_game: targetGame,
        label: `${title} · ${provider.name}`,
      });
      useStore.getState().upsertJob(job);
      setJobId(job.id);
    } catch (e: any) {
      toast(`Submit failed: ${e.message}`, "danger");
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-4">
      {/* Controls */}
      <div className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Sparkles size={18} className="text-brand" /> {title}
          </h2>
          {subtitle && <p className="text-sm text-muted mt-0.5">{subtitle}</p>}
        </div>

        <Section title="Provider" desc="Free/local or paid API — switch any time">
          <ProviderPicker stage={stage} value={provider?.id} reloadKey={providerReload} onChange={setProvider} />
        </Section>

        {service && (
          <div
            className={cls(
              "card p-3 flex items-center gap-2 text-sm",
              service.reachable ? "border-ok/40" : "border-warn/40"
            )}
          >
            {service.reachable ? (
              <CheckCircle2 size={16} className="text-ok shrink-0" />
            ) : (
              <AlertTriangle size={16} className="text-warn shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              <div className="font-medium truncate">{service.name}</div>
              <div className="text-xs text-muted">
                {service.reachable
                  ? "server running — ready · stop it to free GPU memory"
                  : service.configured
                  ? "not running · click Start, or just Generate (auto-starts)"
                  : "no launch command set"}
              </div>
            </div>
            {service.reachable ? (
              <button
                className="btn text-xs"
                onClick={stopServer}
                disabled={startingSvc}
                title="Stop this server to free GPU memory (VRAM)"
              >
                {startingSvc ? <Loader2 size={13} className="animate-spin" /> : <Square size={13} />}
                {startingSvc ? "Stopping…" : "Stop · free VRAM"}
              </button>
            ) : service.configured ? (
              <button className="btn text-xs" onClick={startServer} disabled={startingSvc}>
                {startingSvc ? <Loader2 size={13} className="animate-spin" /> : <PlugZap size={13} />}
                {startingSvc ? "Starting…" : "Start now"}
              </button>
            ) : (
              <button className="btn text-xs" onClick={() => setTab("servers")}>
                <PlugZap size={13} /> Configure
              </button>
            )}
          </div>
        )}

        {inputType && (
          <Section title="Inputs">
            <InputPicker type={inputType} multiple={multiInput} value={inputs} onChange={setInputs} />
          </Section>
        )}

        {provider && provider.params.length > 0 && (
          <Section title="Parameters">
            <ParamForm params={provider.params} value={params} onChange={setParams} />
          </Section>
        )}

        <div>
          <label className="label">Target game (optional)</label>
          <input className="input" value={targetGame} onChange={(e) => setTargetGame(e.target.value)}
            placeholder="e.g. Coin Dash" />
        </div>

        <button className="btn-primary w-full justify-center !py-3 text-base" disabled={!canRun} onClick={run}>
          <Play size={18} /> {busy ? "Running…" : "Generate"}
        </button>
        {inputRequired && inputs.length === 0 && (
          <p className="text-xs text-warn text-center">Select an input asset to run this stage.</p>
        )}
      </div>

      {/* Output + history */}
      <div className="space-y-4">
        {liveJob && (liveJob.status === "running" || liveJob.status === "queued") && (
          <JobProgress job={liveJob} onCancel={() => setBusy(false)} />
        )}

        {outputs.length > 0 && (
          <Section title="Result" right={<span className="chip">{outputs.length} asset{outputs.length > 1 ? "s" : ""}</span>}>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {outputs.map((a) =>
                a.type === "model" ? (
                  <div key={a.id} className="card overflow-hidden">
                    <div className="aspect-square">
                      <ModelViewer assetId={a.id} />
                    </div>
                    <div className="p-2 text-xs truncate">{a.name}</div>
                  </div>
                ) : (
                  <AssetCard key={a.id} asset={a} onUse={() => setInputs([a.id])} />
                )
              )}
            </div>
          </Section>
        )}

        <Section
          title={`Recent ${title} outputs`}
          right={<button className="btn-ghost text-xs" onClick={() => setTab("catalog")}>Open Catalog →</button>}
        >
          {recent.length === 0 ? (
            <Empty label="Nothing here yet — generate your first asset" />
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {recent.map((a) => (
                <AssetCard key={a.id} asset={a} compact onUse={() => setInputs([a.id])} />
              ))}
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
