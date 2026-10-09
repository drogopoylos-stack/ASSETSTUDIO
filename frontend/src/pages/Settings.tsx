import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  Check,
  Cloud,
  Cpu,
  DollarSign,
  Download,
  Gamepad2,
  KeyRound,
  ListChecks,
  Palette,
  Plus,
  Puzzle,
  RefreshCw,
  RotateCcw,
  Save,
  Server,
  Settings as SettingsIcon,
  SlidersHorizontal,
  Sparkles,
  SquareTerminal,
  Terminal,
  Trash2,
  Wand2,
  Wrench,
} from "lucide-react";
import { api } from "../api/client";
import { EnginePane } from "./SettingsEngine";
import { settingSlug } from "../components/ui";
import { FEED_DETAILS, detailSummary } from "../components/feedDetail";
import AgentTerminal from "../components/AgentTerminal";
import { CodexAccountCard } from "../components/CodexPanel";
import { LookSwitcher } from "../components/LookSwitcher";
import { BarEditorBody } from "../components/StatusBar";
import { useStore } from "../store/useStore";
import { STAGES } from "../types";
import type { Generator, KeyInfo, ProviderInfo, SetupTask, StageType } from "../types";
import { Empty, Section, Spinner, cls, fmtCost } from "../components/ui";
import { THEMES } from "../theme";
import { ChatModelsSection, PluginsSection } from "./SettingsPanes";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
const toast = (m: string, k: "info" | "ok" | "warn" | "danger" = "info") =>
  useStore.getState().toast(m, k);

const TOOL_FIELDS: { key: string; label: string; placeholder: string; hint: string }[] = [
  { key: "comfyui_url", label: "ComfyUI URL", placeholder: "http://127.0.0.1:8188", hint: "Local Stable Diffusion / Flux server" },
  { key: "blender_path", label: "Blender path", placeholder: "blender", hint: "On PATH, or absolute path to blender.exe" },
  { key: "gltf_transform_cmd", label: "gltf-transform command", placeholder: "gltf-transform", hint: "npm i -g @gltf-transform/cli" },
  { key: "trellis_url", label: "TRELLIS URL", placeholder: "http://127.0.0.1:7860", hint: "Gradio image→3D server" },
  { key: "hunyuan_url", label: "Hunyuan3D URL", placeholder: "http://127.0.0.1:8080", hint: "Hunyuan3D inference server" },
  { key: "unirig_path", label: "UniRig path", placeholder: "", hint: "Path to UniRig checkout (auto-rigging)" },
];

const RESULT_EXT: Record<string, string> = { image: ".png", model: ".glb", texture: ".png" };

const OPENAI_TEMPLATE = {
  id: "openai-images",
  name: "OpenAI Images (gpt-image-1)",
  stage: "image2d" as StageType,
  kind: "api" as const,
  endpoint: "https://api.openai.com/v1/images/generations",
  method: "POST" as const,
  requires_key: true,
  key_name: "openai",
  headers: {
    Authorization: "Bearer {key}",
    "Content-Type": "application/json",
  },
  body: {
    model: "gpt-image-1",
    prompt: "{prompt}",
    size: "{size}",
    n: 1,
  },
  output: { mode: "b64_in_json", json_path: "data.0.b64_json", ext: ".png" },
  result_type: "image",
  cost_per_call: 0.04,
  license_note: "Subject to OpenAI usage policies — verify commercial rights.",
  commercial_ok: true,
  params: [
    { name: "prompt", label: "Prompt", type: "text", default: "" },
    { name: "size", label: "Size", type: "select", options: ["1024x1024", "1024x1536", "1536x1024"], default: "1024x1024" },
  ],
};

// ---------------------------------------------------------------------------
// 1) API keys
// ---------------------------------------------------------------------------
function ApiKeysSection() {
  const [backend, setBackend] = useState("");
  const [keys, setKeys] = useState<KeyInfo[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const r = await api.keys();
      setBackend(r.backend);
      setKeys(r.keys);
    } catch (e: any) {
      toast(`Failed to load keys: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function save(name: string) {
    const value = drafts[name] ?? "";
    if (!value) {
      toast("Enter a value first", "warn");
      return;
    }
    setBusy(name);
    try {
      await api.setKey(name, value);
      setDrafts((d) => ({ ...d, [name]: "" }));
      toast(`Saved ${name}`, "ok");
      await load();
    } catch (e: any) {
      toast(`Save failed: ${e.message}`, "danger");
    } finally {
      setBusy(null);
    }
  }

  async function remove(name: string) {
    setBusy(name);
    try {
      await api.deleteKey(name);
      toast(`Removed ${name}`, "ok");
      await load();
    } catch (e: any) {
      toast(`Remove failed: ${e.message}`, "danger");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Section
      title="API keys"
      desc="Secrets for paid providers. Stored locally via the keychain backend."
      right={
        <span className="chip">
          <Server size={12} /> {backend || "—"}
        </span>
      }
    >
      {loading ? (
        <div className="py-8 flex justify-center">
          <Spinner />
        </div>
      ) : keys.length === 0 ? (
        <Empty icon={<KeyRound size={24} />} label="No key slots advertised by providers" />
      ) : (
        <div className="space-y-3">
          {keys.map((k) => (
            <div key={k.name} className="flex flex-col sm:flex-row sm:items-end gap-2">
              <div className="flex-1 min-w-0">
                <label className="label flex items-center gap-1.5">
                  <KeyRound size={12} />
                  {k.label}
                  {k.present ? (
                    <span className="chip text-ok ml-1">
                      <Check size={11} /> set
                    </span>
                  ) : (
                    <span className="chip text-warn ml-1">not set</span>
                  )}
                  <span className="text-[10px] text-muted ml-auto">{k.name}</span>
                </label>
                <input
                  className="input font-mono"
                  type="password"
                  autoComplete="off"
                  placeholder={k.present ? "•••• set — type to replace" : "paste secret…"}
                  value={drafts[k.name] ?? ""}
                  onChange={(e) => setDrafts((d) => ({ ...d, [k.name]: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && save(k.name)}
                />
              </div>
              <div className="flex gap-2">
                <button className="btn-primary" disabled={busy === k.name} onClick={() => save(k.name)}>
                  {busy === k.name ? <Spinner size={14} /> : <Save size={15} />} Save
                </button>
                <button
                  className="btn text-danger"
                  disabled={busy === k.name || !k.present}
                  onClick={() => remove(k.name)}
                  title="Remove key"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 2) Default providers
// ---------------------------------------------------------------------------
function DefaultProvidersSection() {
  const [defaults, setDefaults] = useState<Record<string, string>>({});
  const [byStage, setByStage] = useState<Record<string, ProviderInfo[]>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const [s, all] = await Promise.all([api.settings(), api.providers()]);
      setDefaults({ ...(s?.default_providers || {}) });
      const grouped: Record<string, ProviderInfo[]> = {};
      for (const p of all) (grouped[p.stage] ||= []).push(p);
      setByStage(grouped);
    } catch (e: any) {
      toast(`Failed to load defaults: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function change(stage: StageType, id: string) {
    const next = { ...defaults, [stage]: id };
    setDefaults(next);
    setSaving(stage);
    try {
      await api.updateSettings({ default_providers: next });
      toast(`Default for ${stage} → ${id}`, "ok");
    } catch (e: any) {
      toast(`Update failed: ${e.message}`, "danger");
    } finally {
      setSaving(null);
    }
  }

  return (
    <Section title="Default providers" desc="The pre-selected engine for each pipeline stage.">
      {loading ? (
        <div className="py-8 flex justify-center">
          <Spinner />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {STAGES.map((st) => {
            const opts = byStage[st.id] || [];
            return (
              <div key={st.id}>
                <label className="label flex items-center gap-1.5">
                  {st.label}
                  {saving === st.id && <Spinner size={11} />}
                </label>
                <select
                  className="input"
                  value={defaults[st.id] ?? ""}
                  onChange={(e) => change(st.id, e.target.value)}
                >
                  {!defaults[st.id] && <option value="">— pick —</option>}
                  {opts.map((p) => (
                    <option key={p.id} value={p.id} disabled={!p.available}>
                      {p.kind === "api" ? "☁ " : "▣ "}
                      {p.name}
                      {p.available ? "" : " · unavailable"}
                    </option>
                  ))}
                  {opts.length === 0 && <option value={defaults[st.id] || ""}>{defaults[st.id] || "none"}</option>}
                </select>
              </div>
            );
          })}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 3) Local tools & endpoints
// ---------------------------------------------------------------------------
function ToolsSection() {
  const [tools, setTools] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .settings()
      .then((s) => setTools({ ...(s?.tools || {}) }))
      .catch((e: any) => toast(`Failed to load tools: ${e.message}`, "danger"))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setSaving(true);
    try {
      await api.updateSettings({ tools: { ...tools } });
      toast("Tool paths saved", "ok");
    } catch (e: any) {
      toast(`Save failed: ${e.message}`, "danger");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Section
      title="Local tools & endpoints"
      desc="Point the studio at your installed binaries and inference servers."
      right={
        <button className="btn-primary" disabled={saving || loading} onClick={save}>
          {saving ? <Spinner size={14} /> : <Save size={15} />} Save tools
        </button>
      }
    >
      {loading ? (
        <div className="py-8 flex justify-center">
          <Spinner />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {TOOL_FIELDS.map((f) => (
            <div key={f.key}>
              <label className="label flex items-center gap-1.5">
                <Wrench size={12} /> {f.label}
              </label>
              <input
                className="input font-mono"
                value={tools[f.key] ?? ""}
                placeholder={f.placeholder}
                onChange={(e) => setTools((t) => ({ ...t, [f.key]: e.target.value }))}
              />
              <p className="text-[10px] text-muted mt-1">{f.hint}</p>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 4) Add a new AI (custom provider)
// ---------------------------------------------------------------------------
interface CustomForm {
  id: string;
  name: string;
  stage: StageType;
  endpoint: string;
  method: "GET" | "POST";
  requires_key: boolean;
  key_name: string;
  headers: string;
  body: string;
  outputMode: "binary" | "url_in_json" | "b64_in_json";
  json_path: string;
  ext: string;
  result_type: "image" | "model" | "texture";
  cost_per_call: number;
  license_note: string;
  commercial_ok: boolean;
  params: string;
}

const EMPTY_FORM: CustomForm = {
  id: "",
  name: "",
  stage: "image2d",
  endpoint: "",
  method: "POST",
  requires_key: true,
  key_name: "",
  headers: '{\n  "Content-Type": "application/json"\n}',
  body: '{\n  "prompt": "{prompt}"\n}',
  outputMode: "url_in_json",
  json_path: "data.0.url",
  ext: ".png",
  result_type: "image",
  cost_per_call: 0,
  license_note: "",
  commercial_ok: true,
  params: '[\n  { "name": "prompt", "label": "Prompt", "type": "text" }\n]',
};

function CustomProvidersSection() {
  const [form, setForm] = useState<CustomForm>(EMPTY_FORM);
  const [existing, setExisting] = useState<any[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);

  function up<K extends keyof CustomForm>(k: K, v: CustomForm[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function loadList() {
    try {
      setExisting(await api.listCustom());
    } catch (e: any) {
      toast(`Failed to list custom AIs: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    loadList();
  }, []);

  function useTemplate() {
    const t = OPENAI_TEMPLATE;
    setForm({
      id: t.id,
      name: t.name,
      stage: t.stage,
      endpoint: t.endpoint,
      method: t.method,
      requires_key: t.requires_key,
      key_name: t.key_name,
      headers: JSON.stringify(t.headers, null, 2),
      body: JSON.stringify(t.body, null, 2),
      outputMode: t.output.mode as CustomForm["outputMode"],
      json_path: t.output.json_path,
      ext: t.output.ext,
      result_type: t.result_type as CustomForm["result_type"],
      cost_per_call: t.cost_per_call,
      license_note: t.license_note,
      commercial_ok: t.commercial_ok,
      params: JSON.stringify(t.params, null, 2),
    });
    toast("Template loaded — tweak then save", "info");
  }

  function parseJSON(label: string, raw: string, fallback: any): any | undefined {
    const trimmed = raw.trim();
    if (!trimmed) return fallback;
    try {
      return JSON.parse(trimmed);
    } catch (e: any) {
      toast(`Bad JSON in ${label}: ${e.message}`, "danger");
      return undefined;
    }
  }

  async function submit() {
    if (!form.id.trim() || !form.name.trim() || !form.endpoint.trim()) {
      toast("id, name and endpoint are required", "warn");
      return;
    }
    const headers = parseJSON("Headers", form.headers, {});
    if (headers === undefined) return;
    const body = parseJSON("Body", form.body, {});
    if (body === undefined) return;
    const params = parseJSON("Params", form.params, []);
    if (params === undefined) return;
    if (!Array.isArray(params)) {
      toast("Params must be a JSON array", "danger");
      return;
    }

    const spec = {
      id: form.id.trim(),
      name: form.name.trim(),
      stage: form.stage,
      kind: "api",
      endpoint: form.endpoint.trim(),
      method: form.method,
      requires_key: form.requires_key,
      key_name: form.key_name.trim() || form.id.trim(),
      headers,
      body,
      output: { mode: form.outputMode, json_path: form.json_path.trim(), ext: form.ext.trim() || RESULT_EXT[form.result_type] },
      result_type: form.result_type,
      cost_per_call: Number(form.cost_per_call) || 0,
      license_note: form.license_note.trim(),
      commercial_ok: form.commercial_ok,
      params,
    };

    setSubmitting(true);
    try {
      await api.upsertCustom(spec);
      toast(`Saved custom AI "${spec.name}"`, "ok");
      setForm(EMPTY_FORM);
      await loadList();
      await api.reloadProviders().catch(() => {});
    } catch (e: any) {
      toast(`Save failed: ${e.message}`, "danger");
    } finally {
      setSubmitting(false);
    }
  }

  async function del(id: string) {
    try {
      await api.deleteCustom(id);
      toast(`Deleted ${id}`, "ok");
      await loadList();
      await api.reloadProviders().catch(() => {});
    } catch (e: any) {
      toast(`Delete failed: ${e.message}`, "danger");
    }
  }

  function edit(spec: any) {
    setForm({
      id: spec.id || "",
      name: spec.name || "",
      stage: (spec.stage as StageType) || "image2d",
      endpoint: spec.endpoint || "",
      method: spec.method === "GET" ? "GET" : "POST",
      requires_key: !!spec.requires_key,
      key_name: spec.key_name || "",
      headers: JSON.stringify(spec.headers || {}, null, 2),
      body: JSON.stringify(spec.body || {}, null, 2),
      outputMode: (spec.output?.mode as CustomForm["outputMode"]) || "url_in_json",
      json_path: spec.output?.json_path || "",
      ext: spec.output?.ext || ".png",
      result_type: (spec.result_type as CustomForm["result_type"]) || "image",
      cost_per_call: Number(spec.cost_per_call) || 0,
      license_note: spec.license_note || "",
      commercial_ok: spec.commercial_ok !== false,
      params: JSON.stringify(spec.params || [], null, 2),
    });
    toast(`Editing "${spec.name || spec.id}"`, "info");
    if (typeof window !== "undefined") window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }

  return (
    <Section
      title="Add a new AI"
      desc="Wire any image / 3D REST API into the studio — no code, just a JSON spec."
      right={
        <button className="btn" onClick={useTemplate}>
          <Sparkles size={15} /> Use template
        </button>
      }
    >
      {/* existing list */}
      <div className="mb-4">
        <div className="label">Your custom providers</div>
        {loading ? (
          <Spinner />
        ) : existing.length === 0 ? (
          <p className="text-xs text-muted">None yet. Build one below or start from the template.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {existing.map((c) => (
              <div key={c.id} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-panel2 border border-line">
                <Cloud size={14} className="text-accent shrink-0" />
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{c.name || c.id}</div>
                  <div className="text-[11px] text-muted truncate">
                    {c.stage} · {c.method || "POST"} · {c.endpoint}
                  </div>
                </div>
                <div className="ml-auto flex items-center gap-2 shrink-0">
                  {c.commercial_ok === false && <span className="chip text-danger">non-commercial</span>}
                  <button className="btn-ghost !px-2 !py-1" title="Edit" onClick={() => edit(c)}>
                    <SlidersHorizontal size={14} />
                  </button>
                  <button className="btn-ghost !px-2 !py-1 text-danger" title="Delete" onClick={() => del(c.id)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* form */}
      <div className="border-t border-line pt-4 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="label">ID (slug)</label>
            <input className="input font-mono" value={form.id} placeholder="my-api" onChange={(e) => up("id", e.target.value)} />
          </div>
          <div>
            <label className="label">Display name</label>
            <input className="input" value={form.name} placeholder="My API" onChange={(e) => up("name", e.target.value)} />
          </div>
          <div>
            <label className="label">Stage</label>
            <select className="input" value={form.stage} onChange={(e) => up("stage", e.target.value as StageType)}>
              {STAGES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">HTTP method</label>
            <select className="input" value={form.method} onChange={(e) => up("method", e.target.value as "GET" | "POST")}>
              <option value="POST">POST</option>
              <option value="GET">GET</option>
            </select>
          </div>
        </div>

        <div>
          <label className="label">Endpoint URL</label>
          <input
            className="input font-mono"
            value={form.endpoint}
            placeholder="https://api.example.com/v1/images"
            onChange={(e) => up("endpoint", e.target.value)}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-end">
          <label className="flex items-center gap-2 cursor-pointer select-none py-2">
            <input type="checkbox" checked={form.requires_key} onChange={(e) => up("requires_key", e.target.checked)} />
            <span className="text-sm">Requires an API key</span>
          </label>
          <div>
            <label className="label">Key name (slot)</label>
            <input
              className="input font-mono"
              value={form.key_name}
              placeholder="defaults to ID"
              disabled={!form.requires_key}
              onChange={(e) => up("key_name", e.target.value)}
            />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="label">Headers (JSON) — use {"{key}"} for the secret</label>
            <textarea
              className="input font-mono min-h-[110px] resize-y"
              value={form.headers}
              spellCheck={false}
              onChange={(e) => up("headers", e.target.value)}
            />
          </div>
          <div>
            <label className="label">Body (JSON) — {"{prompt}"}, {"{size}"}, … from params</label>
            <textarea
              className="input font-mono min-h-[110px] resize-y"
              value={form.body}
              spellCheck={false}
              onChange={(e) => up("body", e.target.value)}
            />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="label">Output mode</label>
            <select
              className="input"
              value={form.outputMode}
              onChange={(e) => up("outputMode", e.target.value as CustomForm["outputMode"])}
            >
              <option value="binary">binary (raw bytes)</option>
              <option value="url_in_json">url_in_json</option>
              <option value="b64_in_json">b64_in_json</option>
            </select>
          </div>
          <div>
            <label className="label">JSON path</label>
            <input
              className="input font-mono"
              value={form.json_path}
              placeholder="data.0.url"
              disabled={form.outputMode === "binary"}
              onChange={(e) => up("json_path", e.target.value)}
            />
          </div>
          <div>
            <label className="label">File extension</label>
            <input className="input font-mono" value={form.ext} placeholder=".png" onChange={(e) => up("ext", e.target.value)} />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="label">Result type</label>
            <select
              className="input"
              value={form.result_type}
              onChange={(e) => {
                const rt = e.target.value as CustomForm["result_type"];
                up("result_type", rt);
                if (!form.ext || form.ext === ".png" || form.ext === ".glb") up("ext", RESULT_EXT[rt]);
              }}
            >
              <option value="image">image</option>
              <option value="model">model</option>
              <option value="texture">texture</option>
            </select>
          </div>
          <div>
            <label className="label">Cost per call ($)</label>
            <input
              className="input"
              type="number"
              min={0}
              step={0.001}
              value={form.cost_per_call}
              onChange={(e) => up("cost_per_call", parseFloat(e.target.value) || 0)}
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer select-none self-end py-2">
            <input type="checkbox" checked={form.commercial_ok} onChange={(e) => up("commercial_ok", e.target.checked)} />
            <span className="text-sm">Commercial use OK</span>
          </label>
        </div>

        <div>
          <label className="label">License note</label>
          <input
            className="input"
            value={form.license_note}
            placeholder="Check vendor terms"
            onChange={(e) => up("license_note", e.target.value)}
          />
        </div>

        <div>
          <label className="label">Params (JSON array of {"{ name, label, type, default, options? }"})</label>
          <textarea
            className="input font-mono min-h-[110px] resize-y"
            value={form.params}
            spellCheck={false}
            onChange={(e) => up("params", e.target.value)}
          />
        </div>

        <div className="flex gap-2">
          <button className="btn-primary" disabled={submitting} onClick={submit}>
            {submitting ? <Spinner size={14} /> : <Plus size={15} />} Save custom AI
          </button>
          <button className="btn" disabled={submitting} onClick={() => setForm(EMPTY_FORM)}>
            Reset
          </button>
        </div>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 5) Providers overview
// ---------------------------------------------------------------------------
function ProvidersOverviewSection() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);

  async function load() {
    try {
      const [ps, errs] = await Promise.all([api.providers(), api.loadErrors().catch(() => ({}))]);
      setProviders(ps);
      setErrors(errs || {});
    } catch (e: any) {
      toast(`Failed to load providers: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function reload() {
    setReloading(true);
    try {
      const r = await api.reloadProviders();
      const errs = (r && (r.errors || r.load_errors)) || {};
      setErrors(errs);
      const n = Object.keys(errs).length;
      toast(n ? `Reloaded with ${n} error(s)` : "Providers reloaded", n ? "warn" : "ok");
      await load();
    } catch (e: any) {
      toast(`Reload failed: ${e.message}`, "danger");
    } finally {
      setReloading(false);
    }
  }

  const grouped = useMemo(() => {
    const g: Record<string, ProviderInfo[]> = {};
    for (const p of providers) (g[p.stage] ||= []).push(p);
    return g;
  }, [providers]);

  const errorEntries = Object.entries(errors);

  return (
    <Section
      title="Providers overview"
      desc="Every engine the studio discovered, grouped by stage."
      right={
        <button className="btn" disabled={reloading} onClick={reload}>
          <RefreshCw size={15} className={cls(reloading && "animate-spin")} /> Reload providers
        </button>
      }
    >
      {errorEntries.length > 0 && (
        <div className="mb-3 rounded-lg border border-danger/40 bg-danger/10 p-3">
          <div className="flex items-center gap-2 text-danger text-sm font-medium mb-1">
            <AlertTriangle size={14} /> Load errors ({errorEntries.length})
          </div>
          <ul className="text-xs text-muted space-y-0.5">
            {errorEntries.map(([k, v]) => (
              <li key={k} className="font-mono">
                <span className="text-warn">{k}</span>: {v}
              </li>
            ))}
          </ul>
        </div>
      )}

      {loading ? (
        <div className="py-8 flex justify-center">
          <Spinner />
        </div>
      ) : providers.length === 0 ? (
        <Empty icon={<Boxes size={24} />} label="No providers found" />
      ) : (
        <div className="space-y-4">
          {STAGES.filter((s) => grouped[s.id]?.length).map((s) => (
            <div key={s.id}>
              <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">{s.label}</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {grouped[s.id].map((p) => (
                  <div key={p.id} className="rounded-lg border border-line bg-panel2 p-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm truncate">{p.name}</span>
                      <span className="chip ml-auto shrink-0">
                        {p.kind === "api" ? <Cloud size={11} /> : <Cpu size={11} />}
                        {p.kind}
                      </span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2 flex-wrap text-[11px]">
                      <span className={cls("flex items-center gap-1", p.available ? "text-ok" : "text-warn")}>
                        <span className={cls("w-1.5 h-1.5 rounded-full", p.available ? "bg-ok" : "bg-warn")} />
                        {p.available ? "available" : "unavailable"}
                      </span>
                      <span className="text-muted">· {p.cost_hint}</span>
                      {p.commercial_ok === true && <span className="text-ok">· commercial OK</span>}
                      {p.commercial_ok === false && <span className="text-danger">· non-commercial</span>}
                    </div>
                    {!p.available && p.available_reason && (
                      <p className="mt-1 text-[11px] text-warn">{p.available_reason}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 6) Cost tracking & budget
// ---------------------------------------------------------------------------
function CostSection() {
  const [summary, setSummary] = useState<any>(null);
  const [budget, setBudget] = useState<number>(5000);
  const [savingBudget, setSavingBudget] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const [sum, s] = await Promise.all([api.catalogSummary(), api.settings()]);
      setSummary(sum);
      setBudget(Number(s?.web_size_budget_kb ?? 5000));
    } catch (e: any) {
      toast(`Failed to load cost data: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function saveBudget() {
    setSavingBudget(true);
    try {
      await api.updateSettings({ web_size_budget_kb: Number(budget) || 0 });
      toast("Web size budget saved", "ok");
    } catch (e: any) {
      toast(`Save failed: ${e.message}`, "danger");
    } finally {
      setSavingBudget(false);
    }
  }

  const byProvider: [string, number][] = useMemo(() => {
    const m = (summary?.by_provider_cost || {}) as Record<string, number>;
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  }, [summary]);

  return (
    <Section
      title="Cost tracking & budget"
      desc="Spend across paid providers, plus your per-asset web size budget."
      right={
        <span className="chip text-accent">
          <DollarSign size={12} /> total {fmtCost(summary?.total_cost)}
        </span>
      }
    >
      {loading ? (
        <div className="py-8 flex justify-center">
          <Spinner />
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat label="Total spend" value={fmtCost(summary?.total_cost)} accent />
            <Stat label="Assets" value={String(summary?.total_assets ?? 0)} />
            <Stat label="Jobs running" value={String(summary?.jobs_running ?? 0)} />
            <Stat label="Non-commercial" value={String(summary?.non_commercial_assets ?? 0)} />
          </div>

          <div>
            <div className="label">Cost by provider</div>
            {byProvider.length === 0 ? (
              <p className="text-xs text-muted">No spend recorded yet — local providers are free.</p>
            ) : (
              <div className="rounded-lg border border-line overflow-hidden">
                {byProvider.map(([pid, cost], i) => (
                  <div
                    key={pid}
                    className={cls(
                      "flex items-center justify-between px-3 py-2 text-sm",
                      i % 2 === 0 ? "bg-panel2" : "bg-transparent"
                    )}
                  >
                    <span className="font-mono text-muted truncate">{pid}</span>
                    <span className={cls("font-medium", cost > 0 ? "text-accent" : "text-muted")}>{fmtCost(cost)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-line pt-4">
            <label className="label">Web size budget (KB per optimized asset)</label>
            <div className="flex gap-2 items-center">
              <input
                className="input max-w-[180px]"
                type="number"
                min={0}
                step={100}
                value={budget}
                onChange={(e) => setBudget(parseInt(e.target.value) || 0)}
              />
              <button className="btn-primary" disabled={savingBudget} onClick={saveBudget}>
                {savingBudget ? <Spinner size={14} /> : <Save size={15} />} Save budget
              </button>
            </div>
            <p className="text-[10px] text-muted mt-1">
              The Optimize stage and QA target this ceiling when compressing meshes & textures.
            </p>
          </div>
        </div>
      )}
    </Section>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-panel2 p-3">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={cls("text-lg font-semibold mt-0.5", accent ? "text-accent" : "text-text")}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------
function SetupInstallSection() {
  const [tasks, setTasks] = useState<SetupTask[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [needsRestart, setNeedsRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const poll = useRef<number | null>(null);
  const load = () => api.setupTasks().then((r) => setTasks(r.tasks)).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  async function install(t: SetupTask) {
    if (activeId) return;
    setLog(""); setActiveId(t.id);
    const r = await api.setupInstall(t.id).catch(() => ({ ok: false, error: "failed to start" }));
    if (!r.ok) { toast(r.error || "install failed", "danger"); setActiveId(null); return; }
    poll.current = window.setInterval(async () => {
      const s = await api.setupStatus(t.id).catch(() => null);
      if (!s) return;
      setLog(s.log || "");
      if (s.done) {
        if (poll.current) window.clearInterval(poll.current);
        setActiveId(null);
        toast(s.ok ? `${t.name} installed` : `${t.name} failed — see the log`, s.ok ? "ok" : "danger");
        if (s.ok && s.restart) setNeedsRestart(true);
        load();
      }
    }, 1000);
  }
  async function restart() {
    setRestarting(true);
    toast("Restarting the studio backend…", "info");
    await api.sysRestart().catch(() => {});
    const wait = () => api.health().then(() => location.reload()).catch(() => window.setTimeout(wait, 800));
    window.setTimeout(wait, 1500);
  }

  return (
    <Section title="Install engines (one-click)"
      desc="Set up the optional local tools the generators need. Installs run here with a live log; some need a quick restart after (button below)."
      right={
        <div className="flex items-center gap-2">
          <button className="btn-ghost !px-2" onClick={load} title="Re-check what's installed"><RefreshCw size={14} /></button>
          <button className="btn !px-2 text-xs" onClick={restart} disabled={restarting} title="Restart the studio backend"><RotateCcw size={13} /> Restart</button>
        </div>
      }>
      {needsRestart && (
        <div className="mb-3 rounded-lg border border-warn/50 bg-warn/10 p-2.5 flex items-center gap-2 text-xs">
          <AlertTriangle size={14} className="text-warn shrink-0" />
          <span className="flex-1">An install needs a restart to take effect.</span>
          <button className="btn-primary !py-1 !px-3" onClick={restart} disabled={restarting}>{restarting ? "Restarting…" : "Restart now"}</button>
        </div>
      )}
      <div className="space-y-2">
        {tasks.map((t) => (
          <div key={t.id} className={cls("rounded-lg border p-3", t.installed ? "border-ok/40 bg-ok/[0.04]" : "border-line bg-panel2/40")}>
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm">{t.name}</span>
              {t.link && <a href={t.link} target="_blank" rel="noreferrer" className="text-[11px] text-brand hover:underline">docs ↗</a>}
              <div className="ml-auto">
                {t.installed ? (
                  <span className="chip text-ok border-ok/50"><Check size={11} /> Installed</span>
                ) : activeId === t.id ? (
                  <span className="chip inline-flex items-center gap-1"><Spinner size={11} /> Installing…</span>
                ) : (
                  <button className="btn-primary !py-1 !px-3 text-xs" disabled={!!activeId} onClick={() => install(t)}>
                    <Download size={13} /> Install
                  </button>
                )}
              </div>
            </div>
            <div className="text-[11px] text-muted mt-1 leading-snug">{t.desc}</div>
            {activeId === t.id && (
              <pre className="mt-2 max-h-48 overflow-auto rounded bg-bg border border-line p-2 text-[11px] font-mono whitespace-pre-wrap text-muted">{log || "starting…"}</pre>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}

function genBadge(g: Generator) {
  const label = g.status_label || (g.installed ? "Installed" : "Not installed");
  const tone = g.installed ? "text-ok border-ok/50"
    : /saved|up |available/i.test(label) ? "text-warn border-warn/50"
    : "text-muted/70";
  return (
    <span className={cls("chip shrink-0 ml-auto", tone)} title={g.detail || ""}>
      {g.installed && <Check size={10} />} {label}
    </span>
  );
}

function GeneratorsCatalogSection() {
  const [gens, setGens] = useState<Generator[]>([]);
  const [comfy, setComfy] = useState(false);
  const [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true);
    api.comfyCatalog().then((r) => { setGens(r.generators); setComfy(!!r.comfyui); }).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(load, []);
  const groups: [string, ("2d" | "3d" | "splat")[]][] = [
    ["3D models", ["3d", "splat"]],
    ["2D image models", ["2d"]],
  ];
  return (
    <Section title="Local generators (ComfyUI)"
      desc="Free 2D/3D models you run through your own ComfyUI. Native engines (e.g. TripoSR) show Installed automatically; ComfyUI models are Installed once their workflow is saved as data/comfy_workflows/<id>.json and ComfyUI is running."
      right={
        <div className="flex items-center gap-2">
          <span className={cls("chip", comfy ? "text-ok border-ok/50" : "text-muted")}>ComfyUI {comfy ? "running" : "off"}</span>
          <button className="btn-ghost !px-2" onClick={load} title="Re-check what's installed"><RefreshCw size={14} /></button>
        </div>
      }>
      {loading ? (
        <Spinner />
      ) : gens.length === 0 ? (
        <Empty icon={<Boxes size={22} />} label="catalog unavailable" />
      ) : (
        groups.map(([label, kinds]) => (
          <div key={label} className="mb-4 last:mb-0">
            <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">{label}</div>
            <div className="grid sm:grid-cols-2 gap-2">
              {gens.filter((g) => kinds.includes(g.kind)).map((g) => (
                <div key={g.id} className={cls("rounded-lg border p-3", g.installed ? "border-ok/40 bg-ok/[0.04]" : "border-line bg-panel2/40")}>
                  <div className="flex items-center gap-2">
                    <Boxes size={14} className={cls("shrink-0", g.installed ? "text-ok" : "text-brand")} />
                    <span className="font-medium text-sm truncate" title={g.name}>{g.name}</span>
                    {genBadge(g)}
                  </div>
                  <div className="text-[11px] text-muted mt-1 leading-snug">{g.notes}</div>
                  <div className="flex flex-wrap items-center gap-1.5 mt-2">
                    <span className="chip" title="VRAM fit">{g.vram}</span>
                    <span className="chip" title="license">{g.license}</span>
                    {!g.installed && <span className="chip font-mono text-[10px]" title="save your workflow under this filename">{g.id}.json</span>}
                    <a href={g.homepage} target="_blank" rel="noreferrer"
                      className="chip hover:border-brand hover:text-brand ml-auto">install / docs ↗</a>
                  </div>
                  <div className="text-[10px] text-muted/70 mt-1.5 truncate" title={g.node}>ComfyUI node: {g.node}</div>
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </Section>
  );
}

// Voice input — which engine turns your speech into text.
//
// Local is the default and stays that way: private, offline, no key. Groq exists for the case
// that actually bites, which is dictating while the GPU is busy generating assets — local Whisper
// wants ~3GB of VRAM on an 8GB card and stutters exactly then.
function VoiceEngineSection() {
  const [engine, setEngine] = useState("local");
  const [hasKey, setHasKey] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [task, setTask] = useState("translate");
  const [saved, setSaved] = useState("");
  const note = (m: string) => { setSaved(m); window.setTimeout(() => setSaved(""), 3000); };
  function refresh() {
    api.voiceStatus().then((s) => {
      setEngine((s.engine || "local").toLowerCase());
      setHasKey(!!s.groq_key);
      setTask(s.task || "translate");
    }).catch(() => {});
  }
  useEffect(refresh, []);
  return (
    <Section title="Voice input"
      desc="Speech to text for the chat box. Local runs on this PC; Groq runs in the cloud and costs the GPU nothing.">
      <div className="max-w-xs">
        <label className="label">Engine</label>
        <select className="input" value={engine}
          onChange={(e) => {
            const v = e.target.value;
            setEngine(v);
            api.updateSettings({ voice_engine: v }).then(() => {
              note(v === "groq"
                ? "Groq — dictation no longer touches your GPU."
                : "Local — private and offline, uses ~3 GB of VRAM while it listens.");
              refresh();
            }).catch(() => {});
          }}>
          <option value="local">Local — faster-whisper large-v3 (private, offline)</option>
            <option value="groq">Groq — whisper-large-v3 (free tier, no GPU cost)</option>
        </select>
      </div>
      {engine === "groq" && (
        <div className="mt-3 max-w-md">
          <label className="label">Groq API key {hasKey && <span className="text-ok">· saved</span>}</label>
          <div className="flex gap-1.5">
            <input className="input flex-1" type="password" placeholder={hasKey ? "•••••••• (stored)" : "gsk_…"}
              value={keyInput} onChange={(e) => setKeyInput(e.target.value)} />
            <button className="btn-primary !px-3 !py-1 text-xs" disabled={!keyInput.trim()}
              onClick={() => {
                api.setKey("voice:groq", keyInput.trim()).then(() => {
                  setKeyInput(""); note("Key saved."); refresh();
                }).catch(() => note("Could not save the key."));
              }}>Save</button>
          </div>
          <p className="text-[11px] text-muted mt-1.5">
            Free at <b className="text-text">console.groq.com</b> — no credit card. The free tier is
            8 hours of audio a day, which you will not reach. Stored in the OS keychain, never in a file.
          </p>
          {task === "translate" && (
            <p className="text-[11px] text-muted mt-1.5">
              Your task is <b className="text-text">translate</b> — you speak any language and get
              English. That needs <code>whisper-large-v3</code>; the faster <code>turbo</code> model
              cannot translate at all, so the Studio substitutes the right one automatically.
            </p>
          )}
          {!hasKey && <p className="text-[11px] text-danger mt-1.5">No key yet — dictation will fail until one is saved.</p>}
        </div>
      )}
      {saved && <p className="text-[11px] text-ok mt-1">{saved}</p>}
    </Section>
  );
}

// blender-kiln — a 3D asset pipeline skill that pilots Blender over MCP. Off by default, and the
// copy below says why: without the addon running, Claude would plan around tools that never
// resolve. Telling it about a skill it cannot drive is worse than saying nothing.
function BlenderKilnSection() {
  const [on, setOn] = useState(false);
  const [saved, setSaved] = useState("");
  useEffect(() => {
    api.settings().then((st) => setOn(!!st?.cc_blender_kiln)).catch(() => {});
  }, []);
  return (
    <Section title="Blender-Kiln (3D asset pipeline)"
      desc="A Claude skill that drives Blender over MCP: brief → source → import → cleanup → texture → optimize → export GLB.">
      <label className="flex items-start gap-2 cursor-pointer select-none">
        <input type="checkbox" className="mt-0.5" checked={on}
          onChange={(e) => {
            const v = e.target.checked;
            setOn(v);
            api.updateSettings({ cc_blender_kiln: v }).then(() => {
              setSaved(v ? "On — Claude is now told the kiln skill exists and what is installed locally."
                         : "Off — Claude is not told about it at all.");
              window.setTimeout(() => setSaved(""), 3200);
            }).catch(() => {});
          }} />
        <span>
          <span className="text-sm">Let Claude use Blender-Kiln</span>
          <span className="block text-[11px] text-muted">
            When on, every chat is told the skill is installed and — importantly — that Hunyuan3D
            and gltf-transform are already on this PC, so it stops reaching for a HuggingFace Space
            and re-downloading weights you have. When off, it is never mentioned.
          </span>
        </span>
      </label>
      {saved && <p className="text-[11px] text-ok mt-1">{saved}</p>}
      <p className="text-[11px] text-muted mt-2">
        <b className="text-text">Before this can actually run:</b> the Blender half needs
        Blender open with the MCP addon listening on port 9876, <i>and</i> a <code>blender</code> MCP
        server registered with the CLI. Neither is set up yet — you have Blender 5.2 and
        gltf-transform, but no MCP server. Until then the skill can still plan, and optimize GLBs
        through gltf-transform, but it cannot drive Blender.
      </p>
    </Section>
  );
}

function AppearanceSection() {
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const skin = useStore((s) => s.skin);
  const feedDetail = useStore((s) => s.feedDetail);
  const setFeedDetail = useStore((s) => s.setFeedDetail);
  const showStatusBar = useStore((s) => s.showStatusBar);
  const setShowStatusBar = useStore((s) => s.setShowStatusBar);
  const [notifyDone, setNotifyDone] = useState(true);
  const [notifySaved, setNotifySaved] = useState("");
  useEffect(() => {
    api.settings().then((st) => setNotifyDone(st?.notify_turn_end !== false)).catch(() => {});
  }, []);
  return (
    <Section title="Appearance" desc="Theme and chrome for the whole studio.">
      <LookSwitcher />
      <p className="text-[11px] text-muted mt-1.5 mb-3 leading-snug max-w-md">
        The CLI look draws your chat the way Claude Code draws it in a console window — the
        same conversation, the same project, the same keys. Switching is safe in the middle
        of a turn. This sets the <b className="text-text/80">whole studio</b>; a single workspace
        can switch itself on from the chat box's settings menu (the ⚙ next to the model). The
        switcher is also at the bottom of the list on the left.
      </p>
      {/* THE SAME STORE FIELD AS THE COMPOSER'S MENU. A write is untouched by all three: an
          edit's diff is the work, a command's output is a note about the work. */}
      <div className="max-w-md mb-4" data-setting={settingSlug("Command output")}>
        <label className="label flex items-center gap-1.5"><Terminal size={12} /> Command output</label>
        <div className="flex rounded-md border border-line overflow-hidden">
          {FEED_DETAILS.map((d) => (
            <button key={d.id} type="button" onClick={() => setFeedDetail(d.id)} title={d.hint}
              className={cls("flex-1 px-3 py-1.5 text-xs transition-colors",
                feedDetail === d.id ? "bg-brand-600 text-white" : "text-muted hover:bg-panel2")}>
              {d.label}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-muted mt-1.5 leading-snug">
          {detailSummary(feedDetail)} A file write is the same in all three — an edit's diff is the
          work, and only the note about the work is folded.
        </p>
      </div>
      <div className="max-w-xs">
        <label className="label flex items-center gap-1.5"><Palette size={12} /> Theme</label>
        <select className="input" value={theme} onChange={(e) => setTheme(e.target.value as any)}>
          {THEMES.map((t) => (<option key={t.id} value={t.id}>{t.label}</option>))}
        </select>
        {/* Say so rather than let the picker look broken: the console look brings its own palette. */}
        {skin === "cli" && (
          <p className="text-[11px] text-muted mt-1 leading-snug">
            The CLI look uses the console palette, so this is on hold. It applies again when you
            go back to the Studio look.
          </p>
        )}
      </div>
      <label className="flex items-center gap-2 cursor-pointer select-none mt-3">
        <input type="checkbox" checked={showStatusBar} onChange={(e) => setShowStatusBar(e.target.checked)} />
        <span className="text-sm">Show the bottom status bar (CPU / GPU / RAM / disk)</span>
      </label>
      {showStatusBar && (
        <div className="mt-2 ml-6 max-w-xs rounded border border-line bg-panel/60 p-2">
          <BarEditorBody />
          <p className="text-[11px] text-muted/70 mt-2 px-1">
            The same controls sit behind the ⚙ on the bar itself.
          </p>
        </div>
      )}
      <label className="flex items-start gap-2 cursor-pointer select-none mt-3">
        <input type="checkbox" className="mt-0.5" checked={notifyDone}
          onChange={(e) => {
            const v = e.target.checked;
            setNotifyDone(v);
            api.updateSettings({ notify_turn_end: v }).then(() => {
              setNotifySaved(v ? "On — you'll be told when a workspace finishes."
                               : "Off — no desktop notifications.");
              window.setTimeout(() => setNotifySaved(""), 2600);
            }).catch(() => {});
          }} />
        <span>
          <span className="text-sm">Tell me when a turn finishes</span>
          <span className="block text-[11px] text-muted">
            A desktop notification and a taskbar flash when any workspace finishes — not just the
            one on screen. Stays quiet while you're watching that workspace. Takes effect within
            about fifteen seconds; no restart needed.
          </span>
        </span>
      </label>
      {notifySaved && <p className="text-[11px] text-ok mt-1">{notifySaved}</p>}
    </Section>
  );
}

// Planning — how a job gets broken up before it is built.
//
// Neither switch is a house rule. Plan mode costs a turn and is the wrong answer to a one-line
// edit, so it is off until you ask for it. The phase tools are worth switching off if you never
// open the panel. Both live here rather than in the composer because you set them once.
// ---------------------------------------------------------------- what agents are told
//
// Every capability the Studio gives an agent arrives as a paragraph in its system prompt, and a
// paragraph is paid for on every turn whether the turn uses it or not. This is the one place that
// lists all of them, each with its own switch and the tokens it costs, so the prompt is a budget
// the person sets rather than a pile that grows with every feature.

interface NoteRow { key: string; label: string; why: string; default: boolean; tokens: number; needs?: string; on?: boolean; group: "studio" | "forge" }

/** Two groups, so it is plain what was always sent and what the forge added — and what to switch off. */
const NOTE_GROUPS: { id: "studio" | "forge"; title: string; hint: string }[] = [
  { id: "studio", title: "The Studio's own notes", hint: "What agents were told before the forge existed." },
  { id: "forge", title: "The forge, new", hint: "Off means an agent is never told these exist. The forge endpoint refuses too." },
];

const NOTE_CATALOG: NoteRow[] = [
  { key: "studio_tools_prompt", label: "Studio generators", group: "studio", default: false, tokens: 606, needs: "a generator tab switched on",
    why: "The 2D and 3D generators behind the tabs, callable by curl." },
  { key: "cc_web_tools", label: "Blocked web pages and search", group: "studio", default: true, tokens: 138,
    why: "A stealth fetch for pages that refuse robots, and a keyless search." },
  { key: "cc_graphify", label: "Code graph", group: "studio", default: true, tokens: 544,
    why: "Where a symbol is and what calls it, from the live graph instead of grep." },
  { key: "cc_phases", label: "Build phases panel", group: "studio", default: true, tokens: 213,
    why: "The phase list beside the chat, kept current by the agent." },
  { key: "cc_review", label: "Visual review", group: "studio", default: false, tokens: 687, needs: "a page a browser can open",
    why: "Contact sheets of the running game at the size the player sees." },
  { key: "cc_live", label: "Live game link", group: "studio", default: false, tokens: 438, needs: "a page a browser can open",
    why: "Question and change the running game: the scene tree, console, perf, input." },
  { key: "cc_autolearn", label: "Auto-learn skills", group: "studio", default: false, tokens: 1926,
    why: "Bank reusable build patterns as skills while the agent works." },
  { key: "cc_blender_kiln", label: "Blender kiln", group: "studio", default: false, tokens: 215,
    why: "The Blender MCP pipeline: source, generate, clean, texture, export." },
  { key: "cc_1m", label: "1M context", group: "studio", default: true, tokens: 97,
    why: "Allows 1M context. Off limits new Claude sessions to 200k, including native 1M models." },
  { key: "cc_fable_efficient", label: "Fable token efficiency", group: "studio", default: true, tokens: 163,
    why: "Act on what is known, do not re-derive, lead with the outcome. Fable models only." },
  { key: "cc_force_plan", label: "Force plan mode", group: "studio", default: false, tokens: 174,
    why: "Starts every conversation in plan mode. The plan note is sent while a session is in plan mode." },
  { key: "cc_memory", label: "Memory", group: "studio", default: true, tokens: 43,
    why: "Off adds a short note that memory is not available; on costs nothing here." },
  { key: "cc_forge", label: "The forge", group: "forge", default: true, tokens: 1194, needs: "a browser, and a page to build for",
    why: "Render asset code in a lit studio with numbers and a reference, batch several calls into one request, and ask the library what it exports. Its own note: off, and the agent is never told it exists; the endpoint refuses too." },
  { key: "cc_ops", label: "Mesh tools", group: "forge", default: true, tokens: 208,
    why: "Optional. The library: skin, subsurf, bevel, unwrap, bakes, heat weights, remesh, booleans, check(); Blender's chain in code (sculpt, decimate, smart materials in one texture set), whole hands on a grip, sweeps and rims — and applying a saved document in node with no browser open. Off: agents build in plain code." },
  { key: "cc_vertedit", label: "Edit mode: the vertices", group: "forge", default: false, tokens: 322, needs: "a project with a mesh in it",
    why: "Move vertices, edges and faces by hand, and keep the move when the code runs again. Blender cannot do this: its mesh is stored, so it has nothing to rebuild against." },
  { key: "cc_animate", label: "Animation review", group: "forge", default: false, tokens: 274, needs: "a page a browser can open",
    why: "Run a cycle and photograph every frame on one fixed camera, with the feet measured against a ground that does not move. Says which foot floats or slides while planted, and when — a gait bug is invisible in any single frame." },
  { key: "cc_navigate", label: "Navigate the running game", group: "forge", default: false, tokens: 386, needs: "a game a browser can open",
    why: "Go to a place in the running game and look at it: a camera the Studio owns, the named things in view with distances, and a screenshot you pasted turned into a camera pose. Blender, Unity and Godot have a free camera; none can be told 'go where this picture was taken'." },
  { key: "cc_scene_edit", label: "Scene edit for agents", group: "forge", default: true, tokens: 715, needs: "the live link, and a game a browser can open",
    why: "The running game's objects as an agent's own tools: each one's key, world size and the code line that named it; move, turn, scale, hide and place the game's own assets, undo, and save a change to studio.edits.json. The answer says whether the thing now floats, sinks or overlaps." },
  { key: "cc_new_game", label: "New game template", group: "forge", default: true, tokens: 189, needs: "a folder with no game in it yet",
    why: "In a folder with no game yet, the agent is told it can start one that every Studio tool works on from the first minute, instead of wiring its own screenshots and test pages." },
  { key: "cc_debugger", label: "Debugger", group: "forge", default: false, tokens: 163, needs: "a page a browser can open",
    why: "Stop asset code where it threw and read every local variable, with real line numbers. Godot's is the only other one and it needs the Godot editor open; Blender's and Unity's cannot have one at all." },
  { key: "cc_code_tools", label: "Code tools and tests", group: "studio", default: false, tokens: 241,
    why: "A file's sha without reading it, a typecheck, a narrow edit that refuses when the file moved under you, and the test suites as a job with pass and fail counts." },
];

function AgentNotesSection({ sync, refresh }: { sync: Record<string, (v: boolean) => void>; refresh: string }) {
  const [rows, setRows] = useState<NoteRow[]>(NOTE_CATALOG);
  const [measured, setMeasured] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  useEffect(() => {
    let dead = false;
    api.settings().then((st) => {
      if (dead) return;
      setRows((rs) => rs.map((r) => ({ ...r, on: st?.[r.key] === undefined ? r.default : !!st[r.key] })));
    }).catch(() => {});
    // The backend measures the real sizes; an older backend has no such route and the list above stands.
    api.agentNotes().then((d) => {
      if (dead || !d?.notes?.length) return;
      setRows((rs) => rs.map((r) => { const m = d.notes.find((n) => n.key === r.key); return m ? { ...r, tokens: m.tokens, on: m.on } : r; }));
      setMeasured(true);
    }).catch(() => {});
    return () => { dead = true; };
  }, [refresh]);
  const flip = (r: NoteRow, v: boolean) => {
    setBusy(r.key);
    setRows((rs) => rs.map((x) => (x.key === r.key ? { ...x, on: v } : x)));
    api.updateSettings({ [r.key]: v })
      .then(() => { setNote((v ? "On: " : "Off: ") + r.label + ". Takes effect on the next message."); sync[r.key]?.(v); })
      .catch((e) => setNote("could not save: " + String(e?.message || e)))
      .finally(() => setBusy(""));
  };
  // Memory is the one inverted row: its note is sent when the switch is OFF.
  const total = rows.reduce((s, r) => s + ((r.key === "cc_memory" ? !r.on : r.on) ? r.tokens : 0), 0) + 207;
  return (
    <div className="mt-6 pt-4 border-t border-line">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="text-sm">What every agent is told</div>
          <div className="text-xs text-muted mt-0.5 max-w-xl">
            Each capability is one paragraph in the agent's system prompt, paid on every turn. Switch
            off what a project does not need; the agent simply will not know that tool exists.
            {measured ? " Sizes are measured from the notes as they are today." : " Sizes are the last measured values."}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs text-muted">per turn, about</div>
          <div className="text-sm tabular-nums">{total.toLocaleString()} tokens</div>
        </div>
      </div>
      {NOTE_GROUPS.map((g) => (
        <div key={g.id} className="mt-3">
          <div className="text-[11px] uppercase tracking-wide text-muted">{g.title}</div>
          <div className="text-[11px] text-muted/80 mb-1.5">{g.hint}</div>
          <div className="space-y-1.5">
            {rows.filter((r) => r.group === g.id).map((r) => (
              <label key={r.key} className="flex items-start gap-2 cursor-pointer select-none">
                <input type="checkbox" className="mt-0.5" checked={!!r.on} disabled={busy === r.key}
                  onChange={(e) => flip(r, e.target.checked)} />
                <span className="flex-1 min-w-0">
                  <span className="text-xs">
                    {r.label}
                    <span className="text-muted/70 tabular-nums"> · ~{r.tokens} tokens</span>
                    {r.needs && <span className="text-muted/60"> · also needs {r.needs}</span>}
                  </span>
                  <span className="block text-[11px] text-muted max-w-xl">{r.why}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
      ))}
      <div className="mt-3 flex items-start gap-2 select-none opacity-70">
        <input type="checkbox" className="mt-0.5" checked disabled />
        <span className="flex-1 min-w-0">
          <span className="text-xs">Browser rule<span className="text-muted/70 tabular-nums"> · ~207 tokens · always</span></span>
          <span className="block text-[11px] text-muted max-w-xl">Never kill every browser on the machine. A safety rule, so it has no switch.</span>
        </span>
      </div>
      {!!note && <div className="text-xs text-ok mt-2">{note}</div>}
    </div>
  );
}

function PlanningPane() {
  const [phases, setPhases] = useState(true);
  const [forcePlan, setForcePlan] = useState(false);
  const [reviewOn, setReviewOn] = useState(true);
  const [reviewWhy, setReviewWhy] = useState("");
  const [reviewQuality, setReviewQuality] = useState("normal");
  const [reviewGpu, setReviewGpu] = useState(true);
  const [reviewSubs, setReviewSubs] = useState(false);
  const [phaseArchive, setPhaseArchive] = useState(true);
  const [engineSubs, setEngineSubs] = useState(false);
  const [liveOn, setLiveOn] = useState(true);
  const [liveWhy, setLiveWhy] = useState("");
  const [liveTabs, setLiveTabs] = useState<{ project: string; engine: string; url: string }[]>([]);
  const [saved, setSaved] = useState("");
  useEffect(() => {
    api.settings().then((st) => {
      setPhases(st?.cc_phases !== false);
      setForcePlan(!!st?.cc_force_plan);
      setLiveOn(st?.cc_live !== false);
      // `!== false` would read a MISSING key as on. Visual review is off by default now, so
      // it has to be read as "explicitly true" or a fresh install shows a switch that lies.
      setReviewOn(!!st?.cc_review);
      setReviewSubs(!!st?.cc_review_subagents);
      // On unless explicitly off; the engine notes for subagents only when explicitly on.
      setPhaseArchive(st?.cc_phase_archive !== false);
      setEngineSubs(!!st?.cc_engine_subagents);
      setReviewQuality(st?.cc_review_quality || "normal");
      setReviewGpu(st?.cc_review_gpu !== false);
    }).catch(() => {});
    // The switch is honest about the machine: with no Chrome there is nothing to turn on.
    api.reviewStatus().then((r) => setReviewWhy(r?.ok ? "" : r?.error || "")).catch(() => {});
    // Same browser, so the same answer — plus which games are open right now, which is the one
    // thing about this feature you cannot see anywhere else in the app.
    const readLive = () => api.liveGameStatus().then((r) => {
      setLiveWhy(r?.available ? "" : r?.why || "");
      setLiveTabs(r?.tabs || []);
    }).catch(() => {});
    readLive();
    const t = window.setInterval(readLive, 8000);
    return () => window.clearInterval(t);
  }, []);
  function save(patch: any, note: string) {
    api.updateSettings(patch).then(() => {
      setSaved(note);
      window.setTimeout(() => setSaved(""), 2600);
    }).catch(() => {});
  }
  return (
    <Section title="Planning & review"
      desc="Whether Claude plans before it builds, writes the phases down, and how it looks at what it made.">
      <label className="flex items-start gap-2 cursor-pointer select-none">
        <input type="checkbox" className="mt-0.5" checked={forcePlan}
          onChange={(e) => { setForcePlan(e.target.checked); save({ cc_force_plan: e.target.checked },
            e.target.checked ? "Plan mode forced — every chat now researches and proposes first."
                             : "Plan mode released — the composer's mode is back in charge."); }} />
        <span>
          <span className="text-sm">Force plan mode</span>
          <span className="block text-xs text-muted mt-0.5 max-w-xl">
            Every message starts Claude in plan mode, on any model, whatever the composer's
            permission-mode selector says. Claude researches and proposes, and changes no file
            until you approve. Off = the composer decides, message by message.
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 cursor-pointer select-none mt-4">
        <input type="checkbox" className="mt-0.5" checked={phases}
          onChange={(e) => { setPhases(e.target.checked); save({ cc_phases: e.target.checked },
            e.target.checked ? "Build phases on — the Phases panel fills on any model."
                             : "Build phases off — only a model that ships the phase tools will fill the panel."); }} />
        <span>
          <span className="text-sm">Build phases</span>
          <span className="block text-xs text-muted mt-0.5 max-w-xl">
            Claude Code ships its phase tools to Haiku only — Opus and Sonnet sessions get none, so
            the Phases panel stays empty on them. This hands the tools to every model and asks the
            session to keep the list current. Off = no phase tools and no note; the panel then only
            shows phases a model happened to write by itself.
          </span>
        </span>
      </label>

      {phases && (
        <label className="flex items-start gap-2 cursor-pointer select-none ml-6 mt-2">
          <input type="checkbox" className="mt-0.5" checked={phaseArchive}
            onChange={(e) => { setPhaseArchive(e.target.checked); save({ cc_phase_archive: e.target.checked },
              e.target.checked ? "A finished phase list now leaves the session's task list after half an hour."
                               : "The session's task list keeps every finished phase again."); }} />
          <span>
            <span className="text-sm">…and keep the phase list short</span>
            <span className="block text-xs text-muted mt-0.5 max-w-xl">
              Claude Code repeats the <b className="text-text/80">whole</b> task list in a reminder
              every few tool calls, and in a long session that list only grows: one session here had
              554 finished phases, about 10,000 tokens each time. This moves a finished list out of it
              half an hour after its last change. The Phases panel still shows it with the earlier
              sets, and nothing is deleted. On by default.
            </span>
          </span>
        </label>
      )}

      <label className="flex items-start gap-2 cursor-pointer select-none mt-4">
        <input type="checkbox" className="mt-0.5" checked={reviewOn} disabled={!!reviewWhy}
          onChange={(e) => { setReviewOn(e.target.checked); save({ cc_review: e.target.checked },
            e.target.checked ? "Visual review on — agents judge art from contact sheets."
                             : "Visual review off — agents fall back to ad-hoc screenshots."); }} />
        <span>
          <span className="text-sm">Visual review</span>
          <span className="block text-xs text-muted mt-0.5 max-w-xl">
            A screenshot of a running game is the wrong frame nearly every time — an effect peaks
            for about 200ms, and two runs never match. This renders a deterministic contact sheet
            instead: the exact frames asked for, identical every run, the same row again at
            gameplay size, and numbers. Claude is told to use it rather than take its own
            screenshots. Off by default: it costs about 700 tokens a session, and it is only
            sent to a workspace that actually has a page a browser could open — a backend or a
            research folder never pays for it.
          </span>
          {!!reviewWhy && <span className="block text-xs text-warn mt-1">{reviewWhy}</span>}
        </span>
      </label>

      {reviewOn && !reviewWhy && (
        <label className="flex items-start gap-2 cursor-pointer select-none ml-6 mt-2">
          <input type="checkbox" className="mt-0.5" checked={reviewSubs}
            onChange={(e) => { setReviewSubs(e.target.checked); save({ cc_review_subagents: e.target.checked },
              e.target.checked ? "Subagents are told about visual review too."
                               : "Subagents no longer carry the visual-review note."); }} />
          <span>
            <span className="text-sm">…and tell subagents as well</span>
            <span className="block text-xs text-muted mt-0.5 max-w-xl">
              A subagent's prompt is not shared with the main session's cache, so this note costs
              about <b className="text-text/80">700 tokens for every agent</b> you fan out — a run
              of ten pays roughly 7,000. Worth it when the agents you send out judge art; pure
              waste when they are grepping for a symbol. Off by default.
            </span>
          </span>
        </label>
      )}

      <label className="flex items-start gap-2 cursor-pointer select-none mt-4">
        <input type="checkbox" className="mt-0.5" checked={engineSubs}
          onChange={(e) => { setEngineSubs(e.target.checked); save({ cc_engine_subagents: e.target.checked },
            e.target.checked ? "Subagents get the full engine notes again."
                             : "Subagents get one line about the engine and read its notes when they need them."); }} />
        <span>
          <span className="text-sm">…and give subagents the engine notes</span>
          <span className="block text-xs text-muted mt-0.5 max-w-xl">
            The live game link, forge, navigation and scene-edit notes are about{" "}
            <b className="text-text/80">2,700 tokens for every subagent</b>, because a subagent's
            prompt is not shared with the main session's cache. Most subagents search and read code.
            Off: a subagent gets one line that says where the notes are, and the agent that needs the
            engine reads them itself. On: every subagent gets them in full. Off by default.
          </span>
        </span>
      </label>

      {reviewOn && !reviewWhy && (
        <div className="ml-6 mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
          <label className="flex items-center gap-1.5">
            <span className="text-muted">Sheet detail</span>
            <select className="input !py-1 text-xs" value={reviewQuality}
              onChange={(e) => { setReviewQuality(e.target.value);
                save({ cc_review_quality: e.target.value }, `Sheet detail set to ${e.target.value}.`); }}>
              <option value="draft">draft — small, ~800 image tokens</option>
              <option value="normal">normal — ~2,200</option>
              <option value="high">high — judge craft, ~4,300</option>
            </select>
          </label>
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <input type="checkbox" checked={reviewGpu}
              onChange={(e) => { setReviewGpu(e.target.checked);
                save({ cc_review_gpu: e.target.checked }, e.target.checked
                  ? "Review renders on the GPU."
                  : "Review renders in software — slower, but the GPU stays free."); }} />
            <span>Render on the GPU</span>
          </label>
          <span className="text-muted/70 max-w-md">
            Measured here: reading a frame back takes 9ms on the GPU and 98ms in software. Turn it
            off only to leave the GPU free while a local model is loaded.
          </span>
        </div>
      )}

      <label className="flex items-start gap-2 cursor-pointer select-none mt-4">
        <input type="checkbox" className="mt-0.5" checked={liveOn} disabled={!!liveWhy}
          onChange={(e) => { setLiveOn(e.target.checked); save({ cc_live: e.target.checked },
            e.target.checked ? "Live game link on — agents can question and change the running game."
                             : "Live game link off — agents are back to editing and re-screenshotting."); }} />
        <span>
          <span className="text-sm">Live game link</span>
          <span className="block text-xs text-muted mt-0.5 max-w-xl">
            A contact sheet shows what the game looks like. It cannot say <i>why</i> — a picture
            has no way to report that a material is black or that a model never loaded. This keeps
            one tab of the game running on the real clock and lets Claude ask it questions and
            change it on the spot: read the scene tree and its materials, read the console and the
            failed asset loads, measure the frame rate and the draw calls, send keys, mouse, taps
            and swipes, and set a value live to see the result before writing it to a file.
          </span>
          <span className="block text-xs text-muted mt-1 max-w-xl">
            It also carries the <b className="text-text/80">forge</b>. For a project whose assets
            are written rather than modelled, Claude can run asset code in a lit studio — three
            lights, a world so that metals do not render black, and a camera framed on the
            subject's bounding box and then checked against the rendered pixels, so an asset is
            never cropped — and get the picture back with triangle and material counts. It builds
            with your engine and <code>import()</code> works inside it, so it previews your real{" "}
            <code>buildSword()</code> instead of a copy. three.js and PlayCanvas.
          </span>
          <span className="block text-xs text-muted mt-1 max-w-xl">
            Everything an agent forges is kept with its code, so the{" "}
            <b className="text-text/80">Engine</b> window — the pill at the left of this bar —
            can re-run any past generation and let you turn it around, list your game projects,
            and run the game itself. Switch this off and the pill goes with it.
          </span>
          <span className="block text-xs text-muted mt-1 max-w-xl">
            Reads the scene of PlayCanvas, three.js, Babylon, Phaser, PixiJS and Cocos, and
            measures any HTML5 game including plain canvas 2D — without changing your project,
            even when the engine is bundled and nothing is on <code>window</code>. Unity, Godot
            and Defold keep their scene inside WebAssembly, which nothing can read from
            JavaScript; those are named honestly, and everything except the scene tree still
            works on them.
          </span>
          <span className="block text-xs text-muted/70 mt-1 max-w-xl">
            It rides the same headless Chrome the visual review uses — one browser, one more tab —
            and opens nothing until an agent asks. On by default: the note costs about 440 tokens — the
            visual review note is 700 — and only a workspace with a page a browser could open is
            told about it.
          </span>
          {!!liveWhy && <span className="block text-xs text-warn mt-1">{liveWhy}</span>}
          {liveOn && !liveWhy && liveTabs.length > 0 && (
            <span className="block text-xs text-ok mt-1">
              {liveTabs.length === 1 ? "1 game open" : `${liveTabs.length} games open`}:{" "}
              {liveTabs.map((t) => `${t.project.split(/[\\/]/).pop()}${t.engine ? ` (${t.engine})` : ""}`).join(", ")}
            </span>
          )}
        </span>
      </label>

      {!!saved && <div className="text-xs text-ok mt-3">{saved} Takes effect on your next message.</div>}

      <AgentNotesSection refresh={saved}
        sync={{ cc_live: setLiveOn, cc_review: setReviewOn, cc_phases: setPhases, cc_force_plan: setForcePlan }} />
    </Section>
  );
}

// A left rail instead of one long scroll: the settings page had nine stacked cards and
// finding anything meant scrolling past all of them.
// ---------------------------------------------------------------- skills
//
// A skill is a folder under ~/.claude/skills with a SKILL.md in it: instructions Claude loads
// only when it needs them. Two things about the cost, because they are different and both
// matter:
//
//   * The DESCRIPTION of every installed skill is in context on every turn, whether the skill is
//     used or not. It is the only thing the model reads when deciding to load one, so it is the
//     price of the skill EXISTING.
//   * The BODY costs nothing until the switch below is on. That is what the switch does: it
//     writes `disable-model-invocation` into the file, so a skill that is off cannot be loaded.
//
// A new skill therefore arrives OFF. Nothing installs itself into your prompt.

function SkillsSection() {
  const [skills, setSkills] = useState<import("../types").Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");

  const load = () => api.skillsList()
    .then((r) => { setSkills(r.skills || []); setErr(""); })
    .catch((e) => setErr(String(e?.message || e)))
    .finally(() => setLoading(false));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const toggle = async (sk: import("../types").Skill) => {
    setBusy(sk.id);
    try {
      const r = await api.skillToggle(sk.id, !sk.enabled);
      setSkills((ss) => ss.map((x) => (x.id === sk.id ? { ...x, enabled: r.enabled, state: r.state } : x)));
    } catch (e: any) { setErr(String(e?.message || e)); } finally { setBusy(""); }
  };

  const needle = q.trim().toLowerCase();
  const shown = needle
    ? skills.filter((s) => (s.name + " " + s.description + " " + (s.category || "")).toLowerCase().includes(needle))
    : skills;
  // On first, because "what is switched on" is the question this page answers.
  const sorted = [...shown].sort((a, b) => (Number(b.enabled) - Number(a.enabled)) || a.name.localeCompare(b.name));
  const on = skills.filter((s) => s.enabled).length;

  return (
    <Section title="Skills"
      desc="Instructions Claude loads only when it needs them. A skill that is off cannot be loaded at all, so it costs nothing but the one line of description below its name."
      right={<div className="text-xs text-muted text-right">
        <div><span className="text-text tabular-nums">{on}</span> of {skills.length} on</div>
        <button className="text-brand hover:underline inline-flex items-center gap-1"
          onClick={() => { setLoading(true); load(); }}><RefreshCw size={11} /> refresh</button>
      </div>}>
      <input className="input w-full text-sm mb-3" value={q} placeholder="filter by name, description or category"
        onChange={(e) => setQ(e.target.value)} />
      {err && <div className="text-xs text-danger mb-2">{err}</div>}
      {loading ? (
        <div className="text-xs text-muted py-3">loading…</div>
      ) : sorted.length === 0 ? (
        <div className="text-xs text-muted/70 py-3">
          {skills.length === 0
            ? "No skills installed. They live in ~/.claude/skills/<name>/SKILL.md, and setup.ps1 puts the bundled ones there."
            : "Nothing matches that."}
        </div>
      ) : (
        <div className="-mx-1">
          {sorted.map((sk) => (
            <button key={sk.id} type="button" disabled={busy === sk.id} onClick={() => toggle(sk)}
              title={sk.enabled ? "Switch off" : "Switch on"}
              className={cls("w-full text-left flex items-start gap-3 px-1 py-2 rounded-lg transition-colors",
                busy === sk.id ? "opacity-50" : "hover:bg-panel2/60 cursor-pointer")}>
              <Wand2 size={13} className={cls("mt-1 shrink-0", sk.enabled ? "text-brand" : "text-muted/50")} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 flex-wrap">
                  <span className={cls("text-sm", !sk.enabled && "text-muted")}>{sk.name}</span>
                  <span className={cls("text-[10px] uppercase tracking-wide",
                    sk.enabled ? "text-ok" : "text-muted/60")}>{sk.enabled ? "on" : "off"}</span>
                  {sk.category && <span className="chip text-[9px]">{sk.category}</span>}
                  <span className="chip text-[9px]">{sk.scope}</span>
                </span>
                <span className="block text-xs text-muted mt-0.5 max-w-2xl">{sk.description}</span>
              </span>
              <span className={cls("shrink-0 mt-0.5 flex items-center h-5 w-9 rounded-full px-0.5 transition-colors",
                sk.enabled ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
                <span className="h-4 w-4 rounded-full bg-white shadow" />
              </span>
            </button>
          ))}
        </div>
      )}
      <p className="text-[11px] text-muted/70 mt-3 leading-relaxed">
        A new skill arrives <span className="text-muted">off</span> — nothing adds itself to your prompt.
        A change applies to your next message, in every project. The same list is beside your code
        in the Workspace, behind the wand.
      </p>
    </Section>
  );
}

// ---------------------------------------------------------------- find a setting
//
// TEN PANES, AND NO WAY IN. Nothing about the words "sweep leaked browser windows" says "Studio
// engine", so the only way to find it was to open all ten panes and read them. This is the map:
// what a setting is called, what it does, and which pane holds it.
//
// It is a MAP, NOT A SECOND COPY. The control stays the one place it always was; this only says
// where to go. `also` is the words somebody would actually type — "fps", "size", "chrome" — which
// are rarely the words on the label.
//
// settingsfind.test.ts reads the switchboard and the note catalogue out of the source and demands
// this list cover both, so a switch nobody can find fails the build instead of being discovered
// three weeks later.
interface Find { label: string; pane: string; what: string; also?: string }

const FIND: Find[] = [
  // What is on — the six capabilities
  { label: "Live game link", pane: "engine", also: "running game console scene inspect playcanvas three",
    what: "Agents question and change the running game, and the Edit tab mirrors it." },
  { label: "The forge", pane: "engine", also: "asset 3d model render studio numbers cc_forge",
    what: "Build an asset in code, then look at it and measure it in a lit studio." },
  { label: "Studio tools over MCP", pane: "engine",
    also: "mcp model context protocol tools server stdio codex cursor gemini desktop tool call picture cc_mcp",
    what: "Give every agent session the Studio engine as MCP tools, beside the curl calls in its notes." },
  { label: "Edit mode: the vertices", pane: "engine",
    also: "vertex vertices edge face corner drag gizmo tab cc_vertedit mesh edit sculpt point",
    what: "Move the mesh itself by hand, and keep the move when the code runs again." },
  { label: "Animation review", pane: "engine",
    also: "animation animate cycle walk run gait foot feet slide skate float plant contact frames strip timeline cc_animate",
    what: "Photograph a cycle on one fixed camera and measure the feet against the ground." },
  { label: "Detail review of characters", pane: "engine",
    also: "detail close up closeup face head hair torso pants shoes hands zoom angles upside down flipped mirrored covered reference crop compare character colour color size measure swatch palette orthographic ortho light exposure rig glb blender forge_detail forge_colours",
    what: "Photograph a character's face, head, hair and torso close up beside the same crop of the reference, and flag an upside-down part." },
  { label: "Colour and size of every part", pane: "engine",
    also: "colour color size measure swatch palette hex ratio proportion shirt longer shorter darker brighter exposure levels lamp forge_colours",
    what: "Each part's own colour beside the reference's, and how tall and wide it is as a share of the figure." },
  { label: "Symmetry, holes, gaps and contrast", pane: "engine",
    also: "symmetry symmetric centre center middle midline off-centre belt buckle twin mirror hole open gap floating float touch grip contrast dark light flat value range forge_checks",
    what: "Say when a centred part sits to one side, a hole shows, a part floats free, or the darks and lights are flatter than the reference's." },
  { label: "Navigate the running game", pane: "engine",
    also: "navigate goto go to where locate camera fly look at place area platform position screenshot find location move around in view cc_navigate",
    what: "Put a Studio camera anywhere in the running game, list what is in view, and find where a screenshot was taken." },
  { label: "A picture with every scene change", pane: "engine",
    also: "picture screenshot look before after see change live view viewport frame scene edit place scene_look",
    what: "An agent's move or placement answers with a picture of it, before beside after." },
  { label: "Frames a second", pane: "engine",
    also: "live view watch agent stream fps frame rate game tab live_watch_fps",
    what: "How smooth the live view of the agent's game tab is." },
  { label: "Picture quality", pane: "engine",
    also: "live view watch agent stream jpeg quality live_watch_quality",
    what: "The JPEG quality of the live view of the agent's game tab." },
  { label: "Live view width", pane: "engine",
    also: "live view watch agent stream width resolution pixels live_watch_width",
    what: "The widest frame of the live view of the agent's game tab." },
  { label: "Scene edit for agents", pane: "engine",
    also: "scene edit move place objects object key pick drop ground float sink overlap undo save studio.edits.json saved edits sidecar runtime placement asset sheet cc_scene_edit",
    what: "Agents list the running game's objects, move and place them, and keep the change." },
  { label: "BOOST: spend less per prompt", pane: "engine",
    also: "boost credits cost save saving token tokens cheap compress compression cache money bill budget spend local pc",
    what: "Spend fewer tokens for the same answer by using this PC first: the message is compressed before it leaves, the code graph answers the symbols you named when that switch is on, and the two switches that are free to move are applied. The two that ride the system prompt are recommended to you, never flipped behind your back." },
  { label: "New game template", pane: "engine",
    also: "new game scaffold template start create project three playcanvas serve.mjs cc_new_game",
    what: "In an empty folder, agents can start a game that every Studio tool works on." },
  { label: "Engine for new games", pane: "engine",
    also: "new game engine three three.js playcanvas default new_game_engine",
    what: "The engine a new game starts on when nobody names one." },
  { label: "Where new games go", pane: "engine",
    also: "new game folder parent location desktop new_game_parent",
    what: "The folder a new game is made in." },
  { label: "Install packages at once", pane: "engine",
    also: "new game npm install packages node_modules background new_game_install",
    what: "Run npm install in the background as a new game is made." },
  { label: "Debugger", pane: "engine",
    also: "breakpoint break pause step stack locals variables nan why threw exception cc_debugger debug",
    what: "Stop asset code where it went wrong and read the variables that were in scope." },
  { label: "Code tools and tests", pane: "engine",
    also: "sha hash checksum validate typecheck tsc edit test tests suite run job cc_code_tools",
    what: "Confirm a file is unchanged, typecheck it, edit it narrowly, and run the test suites." },
  { label: "Mesh tools", pane: "engine", also: "skin subsurf bevel unwrap bake remesh boolean weld cc_ops modelling",
    what: "The modelling library agents import over HTTP." },
  { label: "Visual review", pane: "engine", also: "screenshot contact sheet frames clock cc_review",
    what: "Contact sheets of the running game on a stepped clock, with findings and metrics." },
  { label: "Apply saved edits when a game opens", pane: "engine", also: "sidecar studio.edits.json placements",
    what: "A project's studio.edits.json is applied by name to the running game as it opens." },
  { label: "Sweep leaked browser windows", pane: "engine", also: "chrome memory orphan boot leak",
    what: "A review browser that outlived its backend is closed at boot." },
  { label: "Instant window", pane: "engine",
    also: "speed fast slow lag responsive cache caching latency workspace switch instant performance perf_fast stale",
    what: "The backend answers from memory while the files behind an answer have not changed. Off: every request reads from disk." },
  { label: "Who needs you", pane: "engine",
    also: "attention blocked waiting idle working bottom bar status needs_you question across projects inbox pill",
    what: "A reading in the bottom bar: how many projects stopped to ask you a question, and how many are working. Nothing is drawn while they are all quiet." },
  { label: "Send a page element", pane: "engine",
    also: "inspect click element css html screenshot crop design mode live_pick devtools picker",
    what: "Click an element in the running page and send what it is — HTML, the CSS that applies, a crop — to the agent." },
  { label: "Comment on a diff", pane: "engine",
    also: "review annotate diff_notes line comment feedback checkpoint changes remark",
    what: "Write a remark on a line of a diff, collect them, and hand the batch to the agent with the code quoted." },

  // Studio engine — how it behaves
  { label: "Open on", pane: "engine", also: "tab default asset edit game", what: "Which tab the Engine window shows when it opens." },
  { label: "Game view size", pane: "engine", also: "resolution 720p 900p phone touch", what: "The frame the Game tab shows a game in." },
  { label: "Terrain", pane: "engine",
    also: "ground heightfield height map sculpt brush raise lower smooth flatten noise erode paint splat layer scatter trees grass rock sand dirt landscape hills level world unity godot",
    what: "Sculpt, paint and scatter ground in the Edit tab, with the project's own assets." },
  { label: "Shading", pane: "engine", also: "material solid wireframe preview", what: "How the editor draws an asset when it opens." },
  { label: "Solid lighting", pane: "engine", also: "studio matcap flat three point", what: "The light rig behind solid shading." },
  { label: "Solid colour", pane: "engine", also: "grey material single blender default", what: "Whether solid shading keeps each part's own colour." },
  { label: "Transform space", pane: "engine", also: "global local gizmo axes", what: "Whether the gizmo moves along the world's axes or the part's." },
  { label: "Skin weights", pane: "engine", also: "envelope bone heat rig armature", what: "How a mesh is bound to a skeleton." },
  { label: "Overlays at open", pane: "engine", also: "grid axes outline bones icons", what: "Which overlays the editor starts with." },
  { label: "Bake size", pane: "engine", also: "texture resolution ao normal curvature", what: "The texture size of an ambient occlusion, curvature or normal bake." },
  { label: "Occlusion rays", pane: "engine", also: "ao samples quality bake", what: "More rays give a smoother shade and a slower bake." },
  { label: "Re-bake delay", pane: "engine", also: "slider settle debounce ms", what: "How long after a slider settles the maps are baked again." },
  { label: "Undo steps", pane: "engine", also: "history ctrl z", what: "How many steps the editor remembers." },
  { label: "Pixel ratio", pane: "engine", also: "dpi retina sharpness performance", what: "How many device pixels the viewport draws per CSS pixel." },
  { label: "Shadows", pane: "engine", also: "shadow map viewport", what: "Whether the editor's viewport casts shadows." },
  { label: "Field of view", pane: "engine", also: "fov lens camera perspective", what: "The editor camera's lens." },
  { label: "Orbit speed", pane: "engine", also: "mouse drag rotate sensitivity", what: "How fast dragging turns the view." },
  { label: "Zoom speed", pane: "engine", also: "wheel scroll sensitivity", what: "How fast the wheel zooms." },
  { label: "Invert orbit", pane: "engine", also: "reverse mouse direction", what: "Turn the view the other way when you drag." },
  { label: "Mouse", pane: "engine", also: "controls keymap unity godot blender navigation fly wasd pan orbit", what: "Lay the viewport's mouse out like Blender and Godot, or like Unity." },
  { label: "Wheel zooms toward", pane: "engine", also: "zoom to cursor mouse position scroll close", what: "Zoom toward what the cursor points at, or the centre of the view." },
  { label: "Default quality", pane: "engine", also: "forge draft normal high render", what: "The render quality a forge call uses when it names none." },
  { label: "Framing", pane: "engine", also: "margin zoom fill panel forge", what: "Empty space kept around the subject in a forge render." },
  { label: "Default views", pane: "engine", also: "camera angle three-quarter front back side top forge", what: "The cameras a forge call renders when it names none." },
  { label: "Review quality", pane: "engine", also: "draft normal high contact sheet", what: "The render quality of a visual review." },
  { label: "Use the GPU", pane: "engine", also: "software rendering driver headless chrome", what: "Off renders in software: slower, but it works with no graphics driver." },
  { label: "Close the browser after", pane: "engine", also: "idle minutes memory chrome reap", what: "A browser nobody has used for this long is closed to free memory." },
  { label: "Close a game tab after", pane: "engine", also: "live tab idle minutes in use old agents engine reap", what: "A game tab no agent has called for this long is closed, so a finished agent's game stops running and no longer shows as in use." },
  { label: "Tell subagents too", pane: "engine", also: "fan out review note tokens", what: "Whether every subagent also carries the visual-review note." },

  { label: "Command output", pane: "general", also: "chat feed minimal normal full shell bash lines truncate show more verbose quiet",
    what: "How much of what a command printed the chat shows: minimal, normal or full." },

  // Skills
  { label: "Skills", pane: "skills", also: "skill claude enable disable forge-director web-build-weight wand",
    what: "Instructions Claude loads only when it needs them. Each with its own switch." },

  // Planning & review — the agent notes
  { label: "Studio generators", pane: "planning", also: "2d 3d curl tools prompt", what: "The generators behind the tabs, callable by curl." },
  { label: "Blocked web pages and search", pane: "planning", also: "403 cloudflare stealth fetch scrapling", what: "A stealth fetch for pages that refuse robots, and a keyless search." },
  { label: "Code graph", pane: "planning", also: "graphify symbol callers grep", what: "Where a symbol is and what calls it, from the live graph instead of grep." },
  { label: "Build phases panel", pane: "planning", also: "tasks steps progress", what: "The phase list beside the chat, kept current by the agent." },
  { label: "Keep the phase list short", pane: "planning", also: "tasks reminder archive tokens bloat finished phases", what: "Move a finished phase list out of the session's task list, which Claude Code repeats every few tool calls." },
  { label: "Engine notes for subagents", pane: "planning", also: "subagent fan-out tokens forge live scene navigate bloat", what: "Full engine notes for every subagent, or one line that says where they are." },
  { label: "Auto-learn skills", pane: "planning", also: "learned mining sessions qwen local", what: "Mine finished sessions for validated learnings, with no tokens." },
  { label: "Blender kiln", pane: "planning", also: "mcp 3d pipeline export", what: "The Blender MCP asset pipeline." },
  { label: "Fable token efficiency", pane: "planning", also: "cheap model routing", what: "How Fable is told to spend." },
  { label: "Force plan mode", pane: "planning", also: "planning first ask", what: "Every turn starts in plan mode." },
  { label: "Memory", pane: "planning", also: "remember recall facts", what: "The file-backed memory an agent writes to." },

  // The other panes, at pane level
  { label: "Appearance", pane: "general", also: "theme dark light skin look colour", what: "Theme and the look of the Studio." },
  { label: "Voice", pane: "general", also: "speech dictation groq whisper microphone", what: "Which engine turns your speech into text." },
  { label: "Tools", pane: "general", also: "comfyui blender path gltf-transform trellis hunyuan unirig", what: "Where the outside programs live on this machine." },
  { label: "Chat models", pane: "models", also: "opus sonnet fable haiku effort default", what: "Which model each new conversation starts on." },
  { label: "API keys", pane: "models", also: "token secret openai gemini anthropic key", what: "Keys for the services the Studio can call." },
  { label: "Plugins", pane: "plugins", also: "marketplace install claude plugin", what: "Claude Code plugins this machine has installed." },
  { label: "Default engines", pane: "generation", also: "provider image 3d rig texture", what: "Which generator each stage uses by default." },
  { label: "Custom engines", pane: "providers", also: "openrouter endpoint base url provider", what: "Providers you added yourself." },
  { label: "Cost", pane: "cost", also: "spend money usage dollars budget", what: "What the Studio has spent, and on what." },
  // The machine guards live in the same Limits tab as the money cap, so they are indexed beside it:
  // somebody whose PC froze mid-generation searches for "freeze", not for "min_free_ram_gb".
  { label: "Machine guards", pane: "cost", also: "limits safety min_free ram disk freeze page file memory floor local job", what: "What stops a local generation before it freezes the PC." },
  { label: "Free RAM floor", pane: "cost", also: "memory freeze page file out of memory oom local min_free_ram_gb", what: "A local job is refused when free system memory is below this." },
  { label: "Free disk floor", pane: "cost", also: "space full min_free_gb storage", what: "Every generation is refused when free disk space is below this." },
  { label: "One local job at a time", pane: "cost", also: "serialize gpu queue concurrency offload comfyui vram", what: "Local jobs run one at a time so two do not share the one card." },
  { label: "Coding agents", pane: "agents", also: "codex gemini cursor terminal cli", what: "Somebody else's CLI, in a box of its own." },
];

/**
 * Go to a setting: open its pane, then light the row up.
 *
 * The pane renders on the next frame, so the row is not there when the click is handled. Rather
 * than guess one delay, it tries a few times and gives up quietly — a search that scrolls
 * nowhere is better than one that throws.
 */
function landOn(label: string) {
  const id = settingSlug(label);
  let tries = 0;
  const go = () => {
    const el = document.querySelector('[data-setting="' + id + '"]') as HTMLElement | null;
    if (!el) {
      if (++tries < 8) window.setTimeout(go, 60);
      return;
    }
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("ring-2", "ring-brand", "rounded-lg");
    window.setTimeout(() => el.classList.remove("ring-2", "ring-brand", "rounded-lg"), 2200);
  };
  window.setTimeout(go, 40);
}

const PANES: { id: string; label: string; icon: React.ReactNode; render: () => React.ReactNode }[] = [
  {
    id: "general", label: "General", icon: <SlidersHorizontal size={14} />,
    render: () => (<><AppearanceSection /><VoiceEngineSection /><ToolsSection /></>),
  },
  {
    id: "models", label: "Models", icon: <Boxes size={14} />,
    render: () => (<><ChatModelsSection /><ApiKeysSection /></>),
  },
  {
    id: "plugins", label: "Plugins", icon: <Puzzle size={14} />,
    render: () => <PluginsSection />,
  },
  // Beside Plugins, because they are the same kind of thing to a person: something extra that
  // Claude can use, that you switch on and off.
  {
    id: "skills", label: "Skills", icon: <Wand2 size={14} />,
    render: () => <SkillsSection />,
  },
  {
    id: "generation", label: "Asset engines", icon: <Sparkles size={14} />,
    render: () => (<><DefaultProvidersSection /><BlenderKilnSection /><SetupInstallSection /><GeneratorsCatalogSection /></>),
  },
  {
    id: "providers", label: "Custom engines", icon: <Server size={14} />,
    render: () => (<><CustomProvidersSection /><ProvidersOverviewSection /></>),
  },
  {
    id: "cost", label: "Cost", icon: <DollarSign size={14} />,
    render: () => <CostSection />,
  },
  {
    id: "planning", label: "Planning & review", icon: <ListChecks size={14} />,
    render: () => <PlanningPane />,
  },
  {
    id: "engine", label: "Studio engine", icon: <Gamepad2 size={14} />,
    render: () => <EnginePane />,
  },
  // Last on purpose: the bottom of the left rail. Everything above configures the Studio;
  // this one launches somebody else's program, in their interface, in a box of its own.
  {
    id: "agents", label: "Coding agents", icon: <SquareTerminal size={14} />,
    render: () => <><CodexAccountCard /><AgentTerminal /></>,
  },
];

export default function Settings() {
  const [pane, setPane] = useState(() => {
    try { return localStorage.getItem("settings-pane") || "general"; } catch { return "general"; }
  });
  const [q, setQ] = useState("");
  const cur = PANES.find((p) => p.id === pane) || PANES[0];
  const pick = (id: string) => {
    setPane(id);
    try { localStorage.setItem("settings-pane", id); } catch { /* ignore */ }
  };
  const paneLabel = (id: string) => PANES.find((p) => p.id === id)?.label || id;

  // Match the label, what it does, AND the words somebody would actually type. Every word has to
  // hit something, so "browser idle" narrows rather than widening.
  const hits = useMemo(() => {
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return FIND.filter((f) => {
      const hay = (f.label + " " + f.what + " " + (f.also || "") + " " + paneLabel(f.pane)).toLowerCase();
      return words.every((w) => hay.includes(w));
    }).slice(0, 20);
  }, [q]);

  const goTo = (f: Find) => { pick(f.pane); setQ(""); landOn(f.label); };

  return (
    <div className="max-w-6xl mx-auto pb-12">
      <div className="mb-4 flex items-center gap-4 flex-wrap">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <SettingsIcon size={18} className="text-brand" /> Settings
        </h2>
        {/* THE WAY IN. Ten panes and no way to guess which one holds a setting; this is that way. */}
        <div className="relative flex-1 min-w-[16rem] max-w-md">
          <input className="input w-full text-sm" value={q} autoComplete="off"
            placeholder="Find a setting — try browser, forge, tokens, fps"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") setQ(""); if (e.key === "Enter" && hits[0]) goTo(hits[0]); }} />
          {!!q.trim() && (
            <div className="absolute z-30 mt-1 w-full max-h-[26rem] overflow-auto rounded-xl border border-line bg-panel shadow-xl">
              {hits.length === 0 ? (
                <div className="px-3 py-3 text-xs text-muted">Nothing matches that.</div>
              ) : hits.map((f) => (
                <button key={f.pane + "/" + f.label} type="button" onClick={() => goTo(f)}
                  className="w-full text-left px-3 py-2 hover:bg-panel2/70 border-b border-line/40 last:border-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm">{f.label}</span>
                    <span className="chip text-[9px] ml-auto shrink-0">{paneLabel(f.pane)}</span>
                  </div>
                  <div className="text-[11px] text-muted mt-0.5">{f.what}</div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="flex gap-5 items-start">
        <nav className="w-44 shrink-0 sticky top-4 space-y-0.5">
          {PANES.map((p) => (
            <button key={p.id} onClick={() => pick(p.id)}
              className={cls("w-full text-left px-3 py-2 rounded-lg text-sm flex items-center gap-2 transition-colors",
                p.id === cur.id ? "bg-panel2 text-text font-medium" : "text-muted hover:text-text hover:bg-panel2/50")}>
              <span className={p.id === cur.id ? "text-brand" : ""}>{p.icon}</span>
              {p.label}
            </button>
          ))}
          {/* Bottom of the rail, under everything that configures the studio: how the studio
              is DRAWN. It sits here rather than inside a pane because it is one click and it
              changes the whole window — you want it reachable from any pane. */}
          <LookSwitcher rail />
        </nav>
        <div className="flex-1 min-w-0 space-y-5">{cur.render()}</div>
      </div>
    </div>
  );
}
