import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  GitBranch,
  Layers,
  Play,
  Plus,
  Sparkles,
  Trash2,
  Wand2,
  XCircle,
} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { Asset, Job, ProviderInfo, StageType } from "../types";
import { STAGES } from "../types";
import AssetCard from "../components/AssetCard";
import InputPicker from "../components/InputPicker";
import JobProgress from "../components/JobProgress";
import ModelViewer from "../components/ModelViewer";
import ParamForm from "../components/ParamForm";
import { Empty, Section, Spinner, cls } from "../components/ui";

// One step of the pipeline. `params` is owned by ParamForm for the selected provider.
interface Step {
  stage: StageType;
  provider_id: string;
  params: Record<string, any>;
}

const STAGE_LABEL: Record<StageType, string> = STAGES.reduce(
  (acc, s) => ((acc[s.id] = s.label), acc),
  {} as Record<StageType, string>
);

// Preset templates — filled lazily so we can seed sensible default providers.
interface Preset {
  id: string;
  label: string;
  desc: string;
  // Each entry: stage + optional forced provider id (else use settings default / first available)
  steps: { stage: StageType; provider_id?: string }[];
}

const PRESETS: Preset[] = [
  {
    id: "concept-3d",
    label: "Concept → 3D → Texture → Optimize → QA",
    desc: "Full art pipeline from a text concept to a game-ready, QA'd model.",
    steps: [
      { stage: "image2d" },
      { stage: "gen3d" },
      { stage: "texture" },
      { stage: "optimize" },
      { stage: "qa" },
    ],
  },
  {
    id: "sprite-atlas",
    label: "Sprite → Slice → Atlas → WebP",
    desc: "Generate a sprite sheet, slice it, pack an atlas, then compress to WebP.",
    steps: [
      { stage: "image2d", provider_id: "placeholder-image" },
      { stage: "process2d", provider_id: "slicer" },
      { stage: "process2d", provider_id: "atlas" },
      { stage: "process2d", provider_id: "webp" },
    ],
  },
];

function shortId() {
  return Math.random().toString(36).slice(2, 8);
}

export default function Pipeline() {
  const [steps, setSteps] = useState<Step[]>([]);
  const [inputs, setInputs] = useState<string[]>([]);
  const [targetGame, setTargetGame] = useState("");
  const [running, setRunning] = useState(false);
  const [label, setLabel] = useState<string>("");
  const [finalAssets, setFinalAssets] = useState<Asset[]>([]);
  const [failed, setFailed] = useState<{ step: number; error: string } | null>(null);

  // Provider catalog per stage, fetched on demand and cached. Keyed by stage.
  const [providerCache, setProviderCache] = useState<Record<string, ProviderInfo[]>>({});
  // Settings default providers, loaded once for preset seeding.
  const [defaults, setDefaults] = useState<Record<string, string>>({});

  const jobs = useStore((s) => s.jobs);
  const toast = useStore((s) => s.toast);
  const setTab = useStore((s) => s.setTab);

  // Load settings defaults + warm provider lists for the stages of the presets.
  useEffect(() => {
    api
      .settings()
      .then((s) => setDefaults((s?.default_providers as Record<string, string>) || {}))
      .catch(() => {});
  }, []);

  // Ensure providers for a given stage are loaded; returns cached list if present.
  async function ensureProviders(stage: StageType): Promise<ProviderInfo[]> {
    if (providerCache[stage]) return providerCache[stage];
    try {
      const ps = await api.providers(stage);
      setProviderCache((c) => ({ ...c, [stage]: ps }));
      return ps;
    } catch (e: any) {
      toast(`Could not load ${stage} providers: ${e.message}`, "danger");
      return [];
    }
  }

  // Pick the best default provider id for a stage: settings default (if available) → first available → first.
  function pickProvider(ps: ProviderInfo[], stage: StageType, forced?: string): string {
    if (forced && ps.some((p) => p.id === forced)) return forced;
    const dflt = defaults[stage];
    if (dflt && ps.some((p) => p.id === dflt && p.available)) return dflt;
    const avail = ps.find((p) => p.available);
    return avail?.id || ps[0]?.id || forced || "";
  }

  async function addStep(stage: StageType = "image2d", forced?: string) {
    const ps = await ensureProviders(stage);
    const provider_id = pickProvider(ps, stage, forced);
    setSteps((s) => [...s, { stage, provider_id, params: {} }]);
  }

  async function applyPreset(preset: Preset) {
    // Resolve providers for every step first (in parallel by stage), then commit.
    const next: Step[] = [];
    for (const st of preset.steps) {
      const ps = await ensureProviders(st.stage);
      next.push({
        stage: st.stage,
        provider_id: pickProvider(ps, st.stage, st.provider_id),
        params: {},
      });
    }
    setSteps(next);
    setFinalAssets([]);
    setFailed(null);
    toast(`Loaded preset: ${preset.label}`, "info");
  }

  function removeStep(idx: number) {
    setSteps((s) => s.filter((_, i) => i !== idx));
  }

  function moveStep(idx: number, dir: -1 | 1) {
    setSteps((s) => {
      const j = idx + dir;
      if (j < 0 || j >= s.length) return s;
      const next = [...s];
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  }

  async function changeStage(idx: number, stage: StageType) {
    const ps = await ensureProviders(stage);
    const provider_id = pickProvider(ps, stage);
    setSteps((s) => s.map((st, i) => (i === idx ? { stage, provider_id, params: {} } : st)));
  }

  function changeProvider(idx: number, provider_id: string) {
    // Reset params: ParamForm reseeds defaults from the new provider's param set.
    setSteps((s) => s.map((st, i) => (i === idx ? { ...st, provider_id, params: {} } : st)));
  }

  function setStepParams(idx: number, params: Record<string, any>) {
    setSteps((s) => s.map((st, i) => (i === idx ? { ...st, params } : st)));
  }

  // The provider chosen for a step, resolved from the cache.
  function providerFor(step: Step): ProviderInfo | undefined {
    return (providerCache[step.stage] || []).find((p) => p.id === step.provider_id);
  }

  // Live pipeline jobs: backend labels each step "<label> [i/N] <stage>", so prefix-match.
  const pipelineJobs = useMemo(() => {
    if (!label) return [] as Job[];
    return Object.values(jobs)
      .filter((j) => j.label && j.label.startsWith(label))
      .sort((a, b) => a.created_at - b.created_at);
  }, [jobs, label]);

  const canRun = useMemo(
    () => steps.length > 0 && steps.every((s) => s.provider_id) && !running,
    [steps, running]
  );

  async function runPipeline() {
    if (steps.length === 0) return;
    const lbl = `pipeline-${shortId()}`;
    setLabel(lbl);
    setRunning(true);
    setFinalAssets([]);
    setFailed(null);
    try {
      const result = await api.pipeline({
        steps: steps.map((s) => ({
          stage: s.stage,
          provider_id: s.provider_id,
          params: s.params,
        })),
        inputs,
        target_game: targetGame,
        label: lbl,
      });

      if (result?.ok) {
        const ids: string[] = result.final_assets || [];
        const assets = await Promise.all(
          ids.map((id) => api.asset(id).catch(() => null))
        );
        setFinalAssets(assets.filter(Boolean) as Asset[]);
        toast(`Pipeline complete · ${ids.length} final asset${ids.length === 1 ? "" : "s"}`, "ok");
      } else {
        const failedIdx = typeof result?.failed_step === "number" ? result.failed_step : -1;
        const err = result?.error || "unknown error";
        setFailed({ step: failedIdx, error: err });
        const stageLabel =
          failedIdx >= 0 && steps[failedIdx]
            ? STAGE_LABEL[steps[failedIdx].stage]
            : "a step";
        toast(`Pipeline failed at step ${failedIdx + 1} (${stageLabel}): ${String(err).split("\n").pop()}`, "danger");
      }
    } catch (e: any) {
      setFailed({ step: -1, error: e.message });
      toast(`Pipeline request failed: ${e.message}`, "danger");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <GitBranch size={18} className="text-brand" /> Pipeline
        </h2>
        <p className="text-sm text-muted mt-0.5">
          Chain stages one-click — 2D → 3D → Texture → Rig → Optimize → QA. Each step feeds
          its output into the next.
        </p>
      </div>

      {/* Presets */}
      <Section title="Templates" desc="Start from a common recipe — providers default to your settings.">
        <div className="grid sm:grid-cols-2 gap-3">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              className="card p-3 text-left hover:border-brand transition-colors"
              onClick={() => applyPreset(p)}
            >
              <div className="flex items-center gap-2 font-medium text-sm">
                <Wand2 size={15} className="text-accent" /> {p.label}
              </div>
              <p className="text-xs text-muted mt-1">{p.desc}</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {p.steps.map((s, i) => (
                  <span key={i} className="chip">
                    {STAGE_LABEL[s.stage]}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </Section>

      {/* Starting inputs */}
      <Section
        title="Starting inputs"
        desc="Optional — pick a base asset (e.g. a concept image) to seed the first step."
      >
        <InputPicker type="any" multiple value={inputs} onChange={setInputs} />
      </Section>

      {/* Steps */}
      <Section
        title="Steps"
        desc={steps.length ? `${steps.length} stage${steps.length === 1 ? "" : "s"} chained in order` : "Add stages to build your chain"}
        right={
          <button className="btn-primary !py-1.5 text-xs" onClick={() => addStep()}>
            <Plus size={14} /> Add step
          </button>
        }
      >
        {steps.length === 0 ? (
          <Empty icon={<Layers size={22} />} label="No steps yet — add one or pick a template above" />
        ) : (
          <div className="space-y-3">
            {steps.map((step, idx) => {
              const provider = providerFor(step);
              const stageProviders = providerCache[step.stage] || [];
              return (
                <div key={idx} className="card bg-panel2 p-3">
                  {/* Step header */}
                  <div className="flex items-center gap-2 mb-3">
                    <span className="flex items-center justify-center w-6 h-6 rounded-full bg-brand-600 text-white text-xs font-semibold shrink-0">
                      {idx + 1}
                    </span>
                    <span className="text-sm font-medium flex-1">{STAGE_LABEL[step.stage]}</span>
                    <div className="flex items-center gap-1">
                      <button
                        className="btn-ghost !px-2 !py-1"
                        title="Move up"
                        disabled={idx === 0}
                        onClick={() => moveStep(idx, -1)}
                      >
                        <ArrowUp size={14} />
                      </button>
                      <button
                        className="btn-ghost !px-2 !py-1"
                        title="Move down"
                        disabled={idx === steps.length - 1}
                        onClick={() => moveStep(idx, 1)}
                      >
                        <ArrowDown size={14} />
                      </button>
                      <button
                        className="btn-ghost !px-2 !py-1 text-danger"
                        title="Remove step"
                        onClick={() => removeStep(idx)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>

                  {/* Stage + provider selects */}
                  <div className="grid sm:grid-cols-2 gap-3">
                    <div>
                      <label className="label">Stage</label>
                      <select
                        className="input"
                        value={step.stage}
                        onChange={(e) => changeStage(idx, e.target.value as StageType)}
                      >
                        {STAGES.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="label">Provider</label>
                      <select
                        className="input"
                        value={step.provider_id}
                        onChange={(e) => changeProvider(idx, e.target.value)}
                      >
                        {stageProviders.length === 0 && <option value="">— loading —</option>}
                        {stageProviders.map((p) => (
                          <option key={p.id} value={p.id} disabled={!p.available}>
                            {p.name} {p.available ? `· ${p.cost_hint}` : "· unavailable"}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* Provider note */}
                  {provider && (
                    <div className="mt-2 flex items-center gap-2 flex-wrap text-[11px] text-muted">
                      <span className="chip">{provider.kind}</span>
                      <span className="chip">{provider.cost_hint}</span>
                      {provider.commercial_ok === false && (
                        <span className="chip text-danger">non-commercial</span>
                      )}
                      {!provider.available && provider.available_reason && (
                        <span className="text-warn">{provider.available_reason}</span>
                      )}
                    </div>
                  )}

                  {/* Params */}
                  {provider && provider.params.length > 0 && (
                    <div className="mt-3 border-t border-line pt-3">
                      <div className="label">Parameters</div>
                      <ParamForm
                        key={provider.id}
                        params={provider.params}
                        value={step.params}
                        onChange={(v) => setStepParams(idx, v)}
                      />
                    </div>
                  )}

                  {/* Connector arrow */}
                  {idx < steps.length - 1 && (
                    <div className="flex justify-center mt-1 -mb-1 text-muted">
                      <ArrowDown size={16} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {/* Target game + run */}
      <div className="card p-4 space-y-3">
        <div>
          <label className="label">Target game (optional)</label>
          <input
            className="input"
            value={targetGame}
            onChange={(e) => setTargetGame(e.target.value)}
            placeholder="e.g. Coin Dash"
          />
        </div>
        <button
          className="btn-primary w-full justify-center !py-3 text-base"
          disabled={!canRun}
          onClick={runPipeline}
        >
          {running ? <Spinner size={18} /> : <Play size={18} />}
          {running ? "Running pipeline…" : `Run pipeline${steps.length ? ` · ${steps.length} steps` : ""}`}
        </button>
        {steps.length === 0 && (
          <p className="text-xs text-warn text-center">Add at least one step to run the pipeline.</p>
        )}
      </div>

      {/* Live progress */}
      {(running || pipelineJobs.length > 0) && (
        <Section
          title="Live progress"
          desc="Steps run sequentially; output of each feeds the next."
          right={
            running ? (
              <span className="chip text-brand">
                <Spinner size={11} /> running
              </span>
            ) : undefined
          }
        >
          {pipelineJobs.length === 0 ? (
            <div className="flex items-center gap-2 text-sm text-muted py-2">
              <Spinner size={14} /> Submitting steps…
            </div>
          ) : (
            <div className="space-y-2">
              {pipelineJobs.map((j) => (
                <JobProgress key={j.id} job={j} />
              ))}
            </div>
          )}
        </Section>
      )}

      {/* Failure */}
      {failed && (
        <Section title="Pipeline failed">
          <div className="flex items-start gap-2 text-sm">
            <XCircle size={18} className="text-danger shrink-0 mt-0.5" />
            <div className="min-w-0">
              <div className="font-medium text-danger">
                {failed.step >= 0
                  ? `Step ${failed.step + 1}${
                      steps[failed.step] ? ` · ${STAGE_LABEL[steps[failed.step].stage]}` : ""
                    } failed`
                  : "Request failed"}
              </div>
              <pre className="mt-2 text-[11px] text-danger font-mono bg-danger/10 rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap">
                {failed.error}
              </pre>
            </div>
          </div>
        </Section>
      )}

      {/* Final results */}
      {finalAssets.length > 0 && (
        <Section
          title="Final assets"
          right={
            <button className="btn-ghost text-xs" onClick={() => setTab("catalog")}>
              Open Catalog →
            </button>
          }
        >
          <div className="flex items-center gap-2 text-sm text-ok mb-3">
            <CheckCircle2 size={16} /> Pipeline finished successfully
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {finalAssets.map((a) =>
              a.type === "model" ? (
                <div key={a.id} className="card overflow-hidden">
                  <div className="aspect-square">
                    <ModelViewer assetId={a.id} />
                  </div>
                  <div className="p-2 text-xs truncate" title={a.name}>
                    {a.name}
                  </div>
                </div>
              ) : (
                <AssetCard key={a.id} asset={a} onUse={() => setInputs([a.id])} />
              )
            )}
          </div>
        </Section>
      )}

      {/* Subtle footer hint */}
      <p className={cls("text-center text-[11px] text-muted", running && "animate-pulse")}>
        <Sparkles size={11} className="inline mb-0.5" /> Outputs are saved to your catalog as each
        step completes.
      </p>
    </div>
  );
}
