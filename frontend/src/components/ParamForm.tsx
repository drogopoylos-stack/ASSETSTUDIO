import { useEffect, useState } from "react";
import { Dices } from "lucide-react";
import type { ProviderParam } from "../types";

// Renders an auto-form for a provider's declared params and reports the values.
export default function ParamForm({
  params,
  value,
  onChange,
}: {
  params: ProviderParam[];
  value: Record<string, any>;
  onChange: (v: Record<string, any>) => void;
}) {
  const [vals, setVals] = useState<Record<string, any>>(value || {});

  useEffect(() => {
    // seed defaults whenever the param set changes
    const next: Record<string, any> = { ...value };
    for (const p of params) if (next[p.name] === undefined) next[p.name] = p.default;
    setVals(next);
    onChange(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  function set(name: string, v: any) {
    const next = { ...vals, [name]: v };
    setVals(next);
    onChange(next);
  }

  const groups = [...new Set(params.map((p) => p.group || "General"))];

  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <div key={g}>
          {groups.length > 1 && <div className="text-xs font-semibold text-muted mb-2">{g}</div>}
          <div className="space-y-3">
            {params
              .filter((p) => (p.group || "General") === g)
              .map((p) => (
                <Field key={p.name} p={p} value={vals[p.name]} onChange={(v) => set(p.name, v)} />
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Field({ p, value, onChange }: { p: ProviderParam; value: any; onChange: (v: any) => void }) {
  const common = (
    <div className="flex items-center justify-between">
      <label className="label !mb-0">{p.label}</label>
      {p.description && <span className="text-[10px] text-muted">{p.description}</span>}
    </div>
  );

  if (p.type === "bool")
    return (
      <label className="flex items-center justify-between cursor-pointer">
        <span className="label !mb-0">{p.label}</span>
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
      </label>
    );

  if (p.type === "select")
    return (
      <div>
        {common}
        <select className="input mt-1" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
          {(p.options || []).map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </div>
    );

  if (p.type === "text")
    return (
      <div>
        {common}
        <textarea
          className="input mt-1 min-h-[72px] resize-y"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={p.label}
        />
      </div>
    );

  if (p.type === "seed")
    return (
      <div>
        {common}
        <div className="flex gap-2 mt-1">
          <input
            className="input"
            type="number"
            value={value ?? -1}
            onChange={(e) => onChange(parseInt(e.target.value))}
          />
          <button
            className="btn"
            title="Randomize"
            onClick={() => onChange(Math.floor(Math.random() * 2 ** 31))}
          >
            <Dices size={15} />
          </button>
        </div>
      </div>
    );

  if (p.type === "int" || p.type === "float") {
    const step = p.step ?? (p.type === "int" ? 1 : 0.1);
    return (
      <div>
        {common}
        <div className="flex items-center gap-2 mt-1">
          {p.min != null && p.max != null && (
            <input
              type="range"
              min={p.min}
              max={p.max}
              step={step}
              value={value ?? p.default ?? p.min}
              onChange={(e) => onChange(p.type === "int" ? parseInt(e.target.value) : parseFloat(e.target.value))}
              className="flex-1 accent-brand"
            />
          )}
          <input
            className="input w-24"
            type="number"
            min={p.min}
            max={p.max}
            step={step}
            value={value ?? p.default ?? 0}
            onChange={(e) => onChange(p.type === "int" ? parseInt(e.target.value) : parseFloat(e.target.value))}
          />
        </div>
      </div>
    );
  }

  return (
    <div>
      {common}
      <input className="input mt-1" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
