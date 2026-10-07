import { useEffect, useState } from "react";
import { Cloud, Cpu, KeyRound } from "lucide-react";
import { api } from "../api/client";
import type { ProviderInfo, StageType } from "../types";
import { cls } from "./ui";

// Dropdown + free⇄API toggle for picking a provider for one stage.
// Calls back with the selected ProviderInfo whenever it changes.
export default function ProviderPicker({
  stage,
  value,
  onChange,
  reloadKey,
}: {
  stage: StageType;
  value?: string;
  onChange: (p: ProviderInfo | undefined) => void;
  reloadKey?: number;
}) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [kind, setKind] = useState<"local" | "api">("local");
  const [sel, setSel] = useState<string | undefined>(value);

  useEffect(() => {
    api.providers(stage).then((ps) => {
      setProviders(ps);
      // Keep the user's current pick across a reloadKey bump (e.g. after starting/stopping
      // its server) so availability refreshes without jumping to another provider.
      const initial =
        ps.find((p) => p.id === sel) ||
        ps.find((p) => p.id === value) ||
        ps.find((p) => p.available) ||
        ps[0];
      if (initial) {
        setSel(initial.id);
        setKind(initial.kind);
        onChange(initial);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, reloadKey]);

  const inKind = providers.filter((p) => p.kind === kind);
  const current = providers.find((p) => p.id === sel);

  function selectProvider(id: string) {
    setSel(id);
    onChange(providers.find((p) => p.id === id));
  }

  function switchKind(k: "local" | "api") {
    setKind(k);
    const first = providers.find((p) => p.kind === k && p.available) || providers.find((p) => p.kind === k);
    if (first) selectProvider(first.id);
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <div className="inline-flex rounded-lg border border-line overflow-hidden text-xs">
          <button
            className={cls("px-3 py-1.5 flex items-center gap-1", kind === "local" ? "bg-brand-600 text-white" : "bg-panel2 text-muted")}
            onClick={() => switchKind("local")}
          >
            <Cpu size={13} /> Free / Local
          </button>
          <button
            className={cls("px-3 py-1.5 flex items-center gap-1", kind === "api" ? "bg-brand-600 text-white" : "bg-panel2 text-muted")}
            onClick={() => switchKind("api")}
          >
            <Cloud size={13} /> API
          </button>
        </div>
      </div>

      {/* Every provider stays selectable — picking a stopped-server one (e.g. ComfyUI 3D)
          reveals its Start control below instead of being a dead, greyed-out row. */}
      <select className="input" value={sel} onChange={(e) => selectProvider(e.target.value)}>
        {inKind.length === 0 && <option>— none —</option>}
        {inKind.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} {p.available ? `· ${p.cost_hint}` : "· unavailable"}
          </option>
        ))}
      </select>

      {current && (
        <div className="mt-2 text-xs text-muted space-y-1">
          <p>{current.description}</p>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="chip">{current.kind}</span>
            <span className="chip">{current.cost_hint}</span>
            {current.requires_key && (
              <span className={cls("chip", current.available ? "text-ok" : "text-warn")}>
                <KeyRound size={11} /> {current.available ? "key set" : "needs key"}
              </span>
            )}
            {current.commercial_ok === true && <span className="chip text-ok">commercial OK</span>}
            {current.commercial_ok === false && <span className="chip text-danger">non-commercial</span>}
          </div>
          {!current.available && current.available_reason && (
            <p className="text-warn">{current.available_reason}</p>
          )}
        </div>
      )}
    </div>
  );
}
