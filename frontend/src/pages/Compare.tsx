import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Cloud,
  Cpu,
  Crown,
  GitCompareArrows,
  KeyRound,
  Play,
  Swords,
} from "lucide-react";
import { api, assetFileUrl, assetPreviewUrl } from "../api/client";
import { useStore } from "../store/useStore";
import InputPicker from "../components/InputPicker";
import ModelViewer from "../components/ModelViewer";
import { cls, Empty, Section, Spinner } from "../components/ui";
import { STAGES } from "../types";
import type { AgentRun, Asset, ProviderInfo, StageType } from "../types";

// Stages that take an image as input (so we surface the InputPicker for them).
const IMAGE_INPUT_STAGES: StageType[] = ["process2d", "gen3d", "texture", "video"];

type Side = "a" | "b";

export default function Compare() {
  const [stage, setStage] = useState<StageType>("image2d");
  const [prompt, setPrompt] = useState("");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providerA, setProviderA] = useState<string>("");
  const [providerB, setProviderB] = useState<string>("");
  const [inputs, setInputs] = useState<string[]>([]);

  const [busy, setBusy] = useState(false);
  const [runId, setRunId] = useState<string | undefined>();
  const [run, setRun] = useState<AgentRun | undefined>();
  // resolved assets for each iteration's asset_id
  const [assets, setAssets] = useState<Record<string, Asset>>({});

  const toast = useStore((s) => s.toast);
  const setTab = useStore((s) => s.setTab);
  // live agent progress message streamed in via WebSocket
  const liveRun = useStore((s) => (runId ? s.agentRuns[runId] : undefined));

  const showInputs = IMAGE_INPUT_STAGES.includes(stage);

  // (Re)load providers whenever the stage changes; reset the picks to sensible defaults.
  useEffect(() => {
    let alive = true;
    api
      .providers(stage)
      .then((ps) => {
        if (!alive) return;
        setProviders(ps);
        const avail = ps.filter((p) => p.available);
        const pool = avail.length ? avail : ps;
        setProviderA(pool[0]?.id ?? "");
        setProviderB((pool[1] ?? pool[0])?.id ?? "");
      })
      .catch((e: any) => toast(`Failed to load providers: ${e.message}`, "danger"));
    // clear any previous comparison when we change stage
    setRun(undefined);
    setRunId(undefined);
    setAssets({});
    setInputs([]);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  const provById = useMemo(() => {
    const m: Record<string, ProviderInfo> = {};
    providers.forEach((p) => (m[p.id] = p));
    return m;
  }, [providers]);

  const canRun =
    !busy &&
    prompt.trim().length > 0 &&
    !!providerA &&
    !!providerB &&
    providerA !== providerB &&
    !!provById[providerA]?.available &&
    !!provById[providerB]?.available;

  async function runCompare() {
    if (!canRun) return;
    setBusy(true);
    setRun(undefined);
    setAssets({});
    try {
      const result = await api.compare({
        stage,
        prompt: prompt.trim(),
        provider_a: providerA,
        provider_b: providerB,
        inputs,
      });
      setRun(result);
      setRunId(result.id);
    } catch (e: any) {
      toast(`Compare failed: ${e.message}`, "danger");
    } finally {
      setBusy(false);
    }
  }

  // capture the run id early (it arrives via WS too) so live messages show while awaiting
  useEffect(() => {
    if (!runId && liveRun?.run_id) setRunId(liveRun.run_id);
  }, [liveRun, runId]);

  // resolve the asset for each iteration once the run lands
  useEffect(() => {
    if (!run) return;
    const ids = run.iterations.map((it) => it.asset_id).filter(Boolean) as string[];
    ids.forEach((id) => {
      if (assets[id]) return;
      api
        .asset(id)
        .then((a) => setAssets((prev) => ({ ...prev, [id]: a })))
        .catch(() => {});
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run]);

  // map the two chosen providers onto the run's iterations for side-by-side layout
  const sides: { side: Side; providerId: string }[] = [
    { side: "a", providerId: providerA },
    { side: "b", providerId: providerB },
  ];

  function iterationFor(providerId: string, sideIndex: number) {
    if (!run) return undefined;
    // prefer matching by provider id; fall back to positional so duplicates still render
    const byProvider = run.iterations.filter((it) => it.provider_id === providerId);
    if (byProvider.length) {
      // if both providers are identical, disambiguate by index
      return byProvider[Math.min(sideIndex, byProvider.length - 1)] ?? byProvider[0];
    }
    return run.iterations[sideIndex];
  }

  const liveMessage: string | undefined = liveRun?.message;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-4">
      {/* ───────────── Controls ───────────── */}
      <div className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <GitCompareArrows size={18} className="text-brand" /> Compare A/B
          </h2>
          <p className="text-sm text-muted mt-0.5">
            Run one prompt through two providers, then let the judge score both and crown a winner.
          </p>
        </div>

        <Section title="Stage" desc="What kind of asset to generate">
          <select
            className="input"
            value={stage}
            onChange={(e) => setStage(e.target.value as StageType)}
          >
            {STAGES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </Section>

        <Section title="Prompt" desc="Same prompt sent to both providers">
          <textarea
            className="input min-h-[96px] resize-y"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. a glowing health potion, top-down game icon, clean edges"
          />
        </Section>

        <Section title="Providers" desc="Pick two contenders">
          <div className="space-y-3">
            <ProviderSelect
              label="Provider A"
              accent="text-brand"
              providers={providers}
              value={providerA}
              onChange={setProviderA}
            />
            <ProviderSelect
              label="Provider B"
              accent="text-accent"
              providers={providers}
              value={providerB}
              onChange={setProviderB}
            />
            {providerA && providerA === providerB && (
              <p className="text-xs text-warn">Pick two different providers for a meaningful comparison.</p>
            )}
          </div>
        </Section>

        {showInputs && (
          <Section title="Input image" desc="Optional — conditions both providers">
            <InputPicker type="image" value={inputs} onChange={setInputs} />
          </Section>
        )}

        <button
          className="btn-primary w-full justify-center !py-3 text-base"
          disabled={!canRun}
          onClick={runCompare}
        >
          {busy ? <Spinner size={18} /> : <Play size={18} />}
          {busy ? "Comparing…" : "Run compare"}
        </button>
        {!prompt.trim() && <p className="text-xs text-muted text-center">Enter a prompt to start.</p>}
      </div>

      {/* ───────────── Results ───────────── */}
      <div className="space-y-4">
        {busy && (
          <Section
            title="Running comparison"
            desc="Both providers are generating — this can take a while."
            right={<Spinner size={18} />}
          >
            <div className="grid grid-cols-2 gap-4">
              {sides.map(({ side, providerId }) => (
                <WorkingCard
                  key={side}
                  side={side}
                  provider={provById[providerId]}
                />
              ))}
            </div>
            {liveMessage && (
              <div className="mt-3 flex items-center gap-2 text-xs text-muted font-mono bg-panel2 rounded-lg px-3 py-2">
                <Swords size={13} className="text-brand shrink-0" />
                <span className="truncate">{liveMessage}</span>
              </div>
            )}
          </Section>
        )}

        {run && !busy && (
          <>
            <Section
              title="Verdict"
              desc={`Judged ${run.iterations.length} result${run.iterations.length === 1 ? "" : "s"}`}
              right={
                <span className="chip">
                  best score {Number(run.best_score ?? 0).toFixed(2)}
                </span>
              }
            >
              {run.error ? (
                <div className="text-sm text-danger font-mono bg-danger/10 rounded-lg p-3">
                  {run.error}
                </div>
              ) : run.iterations.length === 0 ? (
                <Empty label="No results were produced." />
              ) : (
                <div className="grid grid-cols-2 gap-4">
                  {sides.map(({ side, providerId }, i) => {
                    const it = iterationFor(providerId, i);
                    const assetId = it?.asset_id ?? undefined;
                    const asset = assetId ? assets[assetId] : undefined;
                    const provider = provById[it?.provider_id ?? providerId];
                    const isWinner =
                      !!run.best_asset_id && !!assetId && run.best_asset_id === assetId;
                    return (
                      <ResultCard
                        key={side}
                        side={side}
                        provider={provider}
                        providerId={it?.provider_id ?? providerId}
                        asset={asset}
                        hasAssetId={!!assetId}
                        score={it?.judge?.score}
                        reasoning={it?.judge?.reasoning}
                        suggestions={it?.judge?.suggestions}
                        isWinner={isWinner}
                        onUseWinner={() => {
                          toast("Opening winner in Catalog", "ok");
                          setTab("catalog");
                        }}
                      />
                    );
                  })}
                </div>
              )}
            </Section>
          </>
        )}

        {!run && !busy && (
          <Section title="Results">
            <Empty
              icon={<GitCompareArrows size={22} />}
              label="Set up a prompt and two providers, then run a comparison."
            />
          </Section>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────── sub-components ───────────────────────── */

function ProviderSelect({
  label,
  accent,
  providers,
  value,
  onChange,
}: {
  label: string;
  accent: string;
  providers: ProviderInfo[];
  value: string;
  onChange: (id: string) => void;
}) {
  const current = providers.find((p) => p.id === value);
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className={cls("label !mb-0", accent)}>{label}</span>
        {current &&
          (current.kind === "api" ? (
            <span className="chip">
              <Cloud size={11} /> API
            </span>
          ) : (
            <span className="chip">
              <Cpu size={11} /> Local
            </span>
          ))}
      </div>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {providers.length === 0 && <option value="">— none —</option>}
        {providers.map((p) => (
          <option key={p.id} value={p.id} disabled={!p.available}>
            {p.name} · {p.kind} {p.available ? `· ${p.cost_hint}` : "· unavailable"}
          </option>
        ))}
      </select>
      {current && (
        <div className="mt-1 flex items-center gap-2 flex-wrap text-[11px] text-muted">
          <span className="truncate">{current.cost_hint}</span>
          {current.requires_key && (
            <span className={cls("chip", current.available ? "text-ok" : "text-warn")}>
              <KeyRound size={10} /> {current.available ? "key set" : "needs key"}
            </span>
          )}
          {!current.available && current.available_reason && (
            <span className="text-warn truncate">{current.available_reason}</span>
          )}
        </div>
      )}
    </div>
  );
}

function WorkingCard({
  side,
  provider,
}: {
  side: Side;
  provider?: ProviderInfo;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="aspect-square bg-panel2 flex flex-col items-center justify-center gap-3 text-muted">
        <Spinner size={26} />
        <span className="text-xs">working…</span>
      </div>
      <div className="p-3">
        <div className="flex items-center gap-2">
          <span className={cls("chip", side === "a" ? "text-brand" : "text-accent")}>
            {side.toUpperCase()}
          </span>
          <span className="text-sm font-medium truncate">
            {provider?.name ?? provider?.id ?? "—"}
          </span>
        </div>
      </div>
    </div>
  );
}

function ResultCard({
  side,
  provider,
  providerId,
  asset,
  hasAssetId,
  score,
  reasoning,
  suggestions,
  isWinner,
  onUseWinner,
}: {
  side: Side;
  provider?: ProviderInfo;
  providerId: string;
  asset?: Asset;
  hasAssetId: boolean;
  score?: number;
  reasoning?: string;
  suggestions?: string;
  isWinner: boolean;
  onUseWinner: () => void;
}) {
  const isModel = asset?.type === "model";
  const hasScore = typeof score === "number";
  const scoreColor = hasScore && score! >= 0.7 ? "text-ok" : "text-warn";

  return (
    <div
      className={cls(
        "card overflow-hidden flex flex-col transition-all",
        isWinner && "ring-2 ring-brand border-brand"
      )}
    >
      {/* preview */}
      <div className="aspect-square bg-panel2 relative">
        {asset ? (
            asset.type === "video" ? (
              <video src={assetFileUrl(asset.id)} controls preload="metadata" className="w-full h-full object-contain" />
            ) : isModel ? (
            asset.preview_path ? (
              <img
                src={`/api/file?path=${encodeURIComponent(asset.preview_path)}`}
                className="w-full h-full object-contain"
              />
            ) : (
              <div className="w-full h-full">
                <ModelViewer assetId={asset.id} />
              </div>
            )
          ) : (
            <img
              src={assetPreviewUrl(asset.id)}
              className="w-full h-full object-contain"
              loading="lazy"
              style={{
                backgroundImage:
                  "repeating-conic-gradient(#1a1e2b 0% 25%, #13161f 0% 50%)",
                backgroundSize: "20px 20px",
              }}
            />
          )
        ) : hasAssetId ? (
          <div className="w-full h-full flex items-center justify-center text-muted">
            <Spinner size={22} />
          </div>
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-muted">
            <Box size={22} />
            <span className="text-xs">no asset produced</span>
          </div>
        )}

        <span
          className={cls(
            "absolute top-2 left-2 chip bg-black/55 backdrop-blur",
            side === "a" ? "text-brand" : "text-accent"
          )}
        >
          {side.toUpperCase()}
        </span>
        {isWinner && (
          <span className="absolute top-2 right-2 chip bg-brand-600 text-white border-brand-700">
            <Crown size={12} /> Winner
          </span>
        )}
      </div>

      {/* body */}
      <div className="p-3 flex flex-col gap-2 flex-1">
        <div className="text-sm font-medium truncate" title={provider?.name ?? providerId}>
          {provider?.name ?? providerId}
        </div>

        <div className="flex items-baseline gap-2">
          <span className={cls("text-3xl font-bold tabular-nums", hasScore ? scoreColor : "text-muted")}>
            {hasScore ? score!.toFixed(2) : "—"}
          </span>
          <span className="text-xs text-muted">judge score</span>
        </div>

        {reasoning && (
          <p className="text-xs text-muted leading-relaxed">{reasoning}</p>
        )}
        {suggestions && (
          <p className="text-[11px] text-muted/80 leading-relaxed">
            <span className="text-accent">Suggestions: </span>
            {suggestions}
          </p>
        )}

        {isWinner && (
          <button
            className="btn-primary w-full justify-center mt-auto"
            onClick={onUseWinner}
          >
            <Crown size={15} /> Use winner in Catalog
          </button>
        )}
      </div>
    </div>
  );
}
