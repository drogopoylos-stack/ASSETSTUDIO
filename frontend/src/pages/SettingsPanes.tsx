/**
 * The two panes that make the Studio configurable without touching code:
 *
 *  - Plugins — every optional tab and background helper, on or off. A tab that is off
 *    is never mounted, so its code is never downloaded.
 *  - Models  — add any Anthropic- or OpenAI-compatible endpoint as a chat agent. A saved
 *    provider gets its own sessions and context in the Workspace, exactly like Claude.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, Check, ChevronDown, Cpu, Loader2, Lock, Pencil, Plus, X, Zap,
} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { AgentInfo, ChatProvider, PluginInfo } from "../types";
import type { TabId } from "../store/useStore";
import { Section, cls } from "../components/ui";

const toast = (m: string, k: "info" | "ok" | "warn" | "danger" = "info") =>
  useStore.getState().toast(m, k);

// ---------------------------------------------------------------------------
// shared bits
// ---------------------------------------------------------------------------
function Toggle({ on, disabled, onChange }: { on: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} disabled={disabled}
      onClick={() => !disabled && onChange(!on)}
      className={cls("shrink-0 w-9 h-5 rounded-full relative transition-colors",
        disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer",
        on ? "bg-brand-600" : "bg-line")}>
      <span className={cls("absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all",
        on ? "left-[18px]" : "left-0.5")} />
    </button>
  );
}

function Dot({ on, title }: { on: boolean; title?: string }) {
  return <span title={title} className={cls("inline-block w-2 h-2 rounded-full shrink-0",
    on ? "bg-ok" : "bg-muted/40")} />;
}

// ---------------------------------------------------------------------------
// Plugins
// ---------------------------------------------------------------------------
export function PluginsSection() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [busy, setBusy] = useState("");
  const setEnabledTabs = useStore((s) => s.setEnabledTabs);

  const load = () => api.plugins().then((r) => setPlugins(r.plugins)).catch(() => {});
  useEffect(() => { load(); }, []);

  const publish = (list: PluginInfo[]) =>
    setEnabledTabs(list.filter((p) => p.kind === "tab" && p.enabled).map((p) => p.id as TabId));

  async function flip(p: PluginInfo, v: boolean) {
    setBusy(p.id);
    try {
      const r = await api.setPlugins({ [p.id]: v });
      setPlugins(r.plugins);
      publish(r.plugins);
      toast(`${p.label} ${v ? "on" : "off"}`, v ? "ok" : "info");
    } catch (e: any) {
      toast(e?.message || "Could not save", "danger");
    } finally {
      setBusy("");
    }
  }

  const groups = useMemo(() => {
    const g = new Map<string, PluginInfo[]>();
    for (const p of plugins) g.set(p.group, [...(g.get(p.group) || []), p]);
    return [...g.entries()];
  }, [plugins]);

  const offCount = plugins.filter((p) => !p.enabled).length;

  return (
    <Section title="Plugins"
      desc="Turn off what you do not use. A tab that is off is not loaded at all, so its code is never downloaded."
      right={<span className="text-xs text-muted">{offCount ? `${offCount} off` : "all on"}</span>}>
      <div className="space-y-5">
        {groups.map(([group, items]) => (
          <div key={group}>
            <div className="text-[11px] uppercase tracking-wide text-muted mb-1.5">{group}</div>
            <div className="rounded-xl border border-line divide-y divide-line overflow-hidden">
              {items.map((p) => (
                <div key={p.id} className="flex items-start gap-3 px-3 py-2.5 bg-panel/40">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium flex items-center gap-1.5">
                      {p.label}
                      {p.core && <Lock size={11} className="text-muted" />}
                      {p.kind === "service" && (
                        <span className="text-[10px] px-1.5 py-px rounded bg-panel2 text-muted border border-line">
                          background
                        </span>
                      )}
                    </div>
                    {p.desc && <div className="text-xs text-muted mt-0.5 leading-relaxed">{p.desc}</div>}
                  </div>
                  {busy === p.id
                    ? <Loader2 size={16} className="animate-spin text-muted mt-0.5" />
                    : <div className="mt-0.5">
                        <Toggle on={p.enabled} disabled={p.core}
                          onChange={(v) => flip(p, v)} />
                      </div>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------
const EMPTY: ChatProvider & { api_key: string } = {
  id: "", name: "", vendor: "", protocol: "anthropic", base_url: "", models: [],
  default_model: "", context_window: 0, color: "#8b8b8b", note: "", key_url: "",
  preset: "", force_stream: false, session_with: "", fallback_models: [], provider_routing: {},
  api_key: "",
};

// What the gateway last told us about this key's quota. A daily cap is invisible until it bites,
// and the agent's error arrives hours into a build; showing the count here makes it something the
// user can see coming.
function Quota({ l }: { l: NonNullable<ChatProvider["limits"]> }) {
  if (typeof l.remaining !== "number" || typeof l.limit !== "number" || l.limit <= 0) return null;
  const left = l.remaining;
  const frac = left / l.limit;
  const when = l.reset ? new Date(l.reset > 1e11 ? l.reset : l.reset * 1000) : null;
  const hrs = when ? (when.getTime() - Date.now()) / 3.6e6 : 0;
  return (
    <span className={cls("text-[11px] font-mono shrink-0 hidden lg:inline",
      left === 0 ? "text-danger" : frac < 0.15 ? "text-warn" : "text-muted")}
      title={when ? `Resets ${when.toLocaleString()}` : undefined}>
      {left}/{l.limit} left
      {when && hrs > 0 && ` · resets ${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
    </span>
  );
}

const PROTOCOL_LABEL: Record<string, string> = {
  anthropic: "anthropic-messages",
  openai: "openai-completions",
};

function ProviderForm({ preset, editing, others = [], onDone, onCancel }: {
  preset?: ChatProvider | null;
  editing?: ChatProvider | null;
  others?: ChatProvider[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [f, setF] = useState<ChatProvider & { api_key: string }>(() => ({
    ...EMPTY, ...(preset || {}), ...(editing || {}), api_key: "",
    models: [...((editing || preset)?.models || [])],
  }));
  const [newModel, setNewModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [fetching, setFetching] = useState(false);
  const set = (patch: Partial<typeof f>) => setF((v) => ({ ...v, ...patch }));
  const isEdit = !!editing;

  async function fetchModels() {
    if (!isEdit) { toast("Save the provider first, then fetch its model list.", "info"); return; }
    setFetching(true);
    try {
      const r = await api.chatProviderModels(f.id);
      if (r.ok && r.models.length) {
        set({ models: r.models, default_model: f.default_model || r.models[0] });
        toast(`Found ${r.models.length} models`, "ok");
      } else {
        toast(r.error || "That provider does not list its models — type the id instead.", "warn");
      }
    } finally { setFetching(false); }
  }

  async function save() {
    setSaving(true);
    try {
      const body = { ...f, api_key: f.api_key ? f.api_key : undefined } as any;
      if (isEdit) await api.updateChatProvider(editing!.id, body);
      else await api.createChatProvider(body);
      toast(`${f.name || f.id} saved — pick it in the chat's agent menu.`, "ok");
      onDone();
    } catch (e: any) {
      toast(e?.message || "Could not save the provider", "danger");
    } finally { setSaving(false); }
  }

  const local = /^https?:\/\/(127\.0\.0\.1|localhost|0\.0\.0\.0)/.test(f.base_url);

  return (
    <div className="rounded-xl border border-line bg-panel2/50 p-4 space-y-3">
      <div className="font-medium text-sm">{isEdit ? `Edit ${editing!.name}` : "Custom provider"}</div>

      <div className="grid sm:grid-cols-2 gap-3">
        <div>
          <label className="label">Provider ID</label>
          <input className="input font-mono" placeholder="acme-gateway" value={f.id}
            onChange={(e) => set({ id: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} />
          <p className="text-[11px] text-muted mt-1">
            Lowercase, starts with a letter. Names the provider in the agent menu and stores its key.
          </p>
        </div>
        <div>
          <label className="label">Display name</label>
          <input className="input" placeholder="Display name" value={f.name}
            onChange={(e) => set({ name: e.target.value })} />
        </div>
      </div>

      <div>
        <label className="label">Base URL</label>
        <input className="input font-mono text-xs" placeholder="https://gateway.example/v1" value={f.base_url}
          onChange={(e) => set({ base_url: e.target.value.trim() })} />
      </div>

      <div className="grid sm:grid-cols-2 gap-3">
        <div>
          <label className="label">API protocol</label>
          <select className="input" value={f.protocol} onChange={(e) => set({ protocol: e.target.value })}>
            <option value="anthropic">anthropic-messages</option>
            <option value="openai">openai-completions</option>
          </select>
          <p className="text-[11px] text-muted mt-1">
            {f.protocol === "openai"
              ? "Served through the Studio's local translation bridge. Text, images, tool calls and reasoning are translated both ways."
              : "Spoken directly by the agent engine. Nothing is translated."}
          </p>
        </div>
        <div>
          <label className="label">API key</label>
          <textarea className="input font-mono text-xs" autoComplete="off" rows={2}
            placeholder={isEdit ? "Leave blank to keep the stored key(s)" : local ? "Not needed for a local server" : "Enter your API key — one per line for several"}
            value={f.api_key} onChange={(e) => set({ api_key: e.target.value })} />
          <p className="text-[11px] text-muted mt-1">
            One key per line. With more than one, the Studio uses them in turn — worth it only
            where the provider limits you per key. Measured on OpenRouter and OpenCode Zen: it
            makes no difference on either, so one key is enough for both.
          </p>
          {f.key_url && (
            <a className="text-[11px] text-brand hover:underline mt-1 inline-block" href={f.key_url}
              target="_blank" rel="noreferrer">Get a key →</a>
          )}
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between">
          <label className="label !mb-0">Models</label>
          <button className="text-[11px] text-brand hover:underline disabled:opacity-50 inline-flex items-center gap-1"
            onClick={fetchModels} disabled={fetching || !f.base_url}>
            {fetching && <Loader2 size={11} className="animate-spin" />} Fetch available models
          </button>
        </div>
        <div className="mt-1.5 rounded-lg border border-line bg-panel/50 p-2 space-y-1 max-h-52 overflow-auto">
          {!f.models.length && (
            <div className="text-xs text-muted px-1 py-1.5">
              No models will be shown in the picker. An unlisted id can still be sent.
            </div>
          )}
          {f.models.map((m) => (
            <div key={m} className="flex items-center gap-2 text-xs font-mono px-1 py-0.5 group">
              <button title="Use as the default model"
                onClick={() => set({ default_model: m })}
                className={cls("w-3.5 h-3.5 rounded-full border shrink-0",
                  f.default_model === m ? "bg-brand border-brand" : "border-line hover:border-brand")} />
              <span className="truncate flex-1">{m}</span>
              {f.default_model === m && <span className="text-[10px] text-brand shrink-0">default</span>}
              <button className="opacity-0 group-hover:opacity-100 text-muted hover:text-danger shrink-0"
                onClick={() => set({ models: f.models.filter((x) => x !== m) })}><X size={12} /></button>
            </div>
          ))}
        </div>
        <div className="flex gap-2 mt-2">
          <input className="input flex-1 font-mono text-xs" placeholder="model-id" value={newModel}
            onChange={(e) => setNewModel(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && newModel.trim()) { set({ models: [...f.models, newModel.trim()], default_model: f.default_model || newModel.trim() }); setNewModel(""); } }} />
          <button className="btn" disabled={!newModel.trim()}
            onClick={() => { set({ models: [...f.models, newModel.trim()], default_model: f.default_model || newModel.trim() }); setNewModel(""); }}>
            <Plus size={13} /> Add model
          </button>
        </div>
      </div>

      <div className="max-w-xs">
        <label className="label">Context window (tokens)</label>
        <input className="input font-mono" inputMode="numeric" placeholder="0"
          value={f.context_window || ""}
          onChange={(e) => set({ context_window: parseInt(e.target.value.replace(/\D/g, ""), 10) || 0 })} />
        <p className="text-[11px] text-muted mt-1">
          The agent does not know a third-party model's window, so it assumes 200,000 tokens and
          compacts there. Enter the real number to use the whole window. Leave 0 if you are unsure.
        </p>
      </div>

      <div className="max-w-xs">
        <label className="label">Chat history</label>
        <select className="input" value={f.session_with || ""}
          onChange={(e) => set({ session_with: e.target.value })}>
          <option value="">Its own</option>
          {others.map((o) => (
            <option key={o.id} value={o.id}>Shared with {o.name}</option>
          ))}
        </select>
        <p className="text-[11px] text-muted mt-1">
          Two routes to the same model can keep one conversation. Share the history and switching
          between them carries the work over instead of opening an empty chat beside it. Only one
          of them runs at a time, so switching mid-answer ends that answer.
        </p>
      </div>

      <div className="max-w-xl">
        <label className="label">Fallback models</label>
        <textarea className="input font-mono text-xs h-[64px]"
          placeholder={"one model id per line\nnvidia/nemotron-3-ultra-550b-a55b:free"}
          value={(f.fallback_models || []).join("\n")}
          onChange={(e) => set({
            fallback_models: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean),
          })} />
        <p className="text-[11px] text-muted mt-1">
          If the chosen model cannot serve a request — it is rate limited, down, or filtered — the
          gateway tries these instead, inside the same call, and the agent never sees the failure.
          The reply says which model answered.
          <b className="text-text"> Leave this empty and the model you picked is the only one that
          may answer</b> — a refusal reaches you as a refusal, and a reply from any other model is
          discarded rather than shown.
        </p>
      </div>

      {f.protocol === "anthropic" && (
        <label className="flex items-start gap-2 cursor-pointer select-none">
          <input type="checkbox" className="mt-0.5" checked={!!f.force_stream}
            onChange={(e) => set({ force_stream: e.target.checked })} />
          <span>
            <span className="text-sm">Stream and retry through the Studio</span>
            <p className="text-[11px] text-muted mt-0.5">
              Some gateways drop a request that has not produced a token yet — OpenRouter calls it
              "Upstream idle timeout exceeded" — and the agent's turn dies with it. Turn this on
              and the Studio carries the request instead: it streams to the provider, and if a try
              fails before anything reached the agent, it sends the request again. Once real text
              is on its way, an error is passed straight through. Turn it on if agents on this
              provider stop mid-turn.
            </p>
          </span>
        </label>
      )}

      <div className="flex items-center justify-end gap-2 pt-1">
        <button className="btn" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" onClick={save} disabled={saving || !f.id || !f.base_url}>
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          {isEdit ? "Save changes" : "Create provider"}
        </button>
      </div>
    </div>
  );
}

export function ChatModelsSection() {
  const [providers, setProviders] = useState<ChatProvider[]>([]);
  const [presets, setPresets] = useState<ChatProvider[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [adding, setAdding] = useState<ChatProvider | null | undefined>(undefined); // undefined = closed
  const [editing, setEditing] = useState<ChatProvider | null>(null);
  const [menu, setMenu] = useState(false);
  const [testing, setTesting] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  const load = () => {
    api.chatProviders().then((r) => { setProviders(r.providers); setPresets(r.presets); }).catch(() => {});
    api.missionAgents().then((r) => setAgents(r.agents)).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  useEffect(() => {
    const off = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, []);

  const builtin = agents.filter((a) => !a.custom);
  const unused = presets.filter((p) => !providers.some((x) => x.id === p.id));

  async function remove(p: ChatProvider) {
    if (!confirm(`Remove ${p.name}?\n\nIts API key is deleted too. The chats it already wrote stay on disk.`)) return;
    await api.deleteChatProvider(p.id).catch(() => {});
    toast(`${p.name} removed`, "info");
    load();
  }

  async function test(p: ChatProvider) {
    setTesting(p.id);
    try {
      const r = await api.testChatProvider(p.id);
      if (r.ok) toast(`${p.name} answered on ${r.model} ✓`, "ok");
      else toast(`${p.name}: ${r.error}`, "danger");
    } finally { setTesting(""); }
  }

  return (
    <Section title="Models"
      desc="Enter an API key to use models from these providers. A provider you add here becomes a chat agent in the Workspace — its own sessions and context, the same effort and permission controls Claude has.">
      {/* built-in engines, for context — these are not editable */}
      <div className="space-y-1.5 mb-3">
        {builtin.map((a) => (
          <div key={a.id} className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl border border-line bg-panel/40">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: a.color }} />
            <span className="text-sm font-medium">{a.name}</span>
            <span className="text-[10px] px-1.5 py-px rounded bg-panel2 text-muted border border-line">built-in</span>
            <Dot on={a.available} title={a.available ? "Ready" : "Not set up"} />
            {!a.available && (
              <span className="text-[11px] text-muted truncate">{a.install_cmd}</span>
            )}
          </div>
        ))}
      </div>

      {/* user-added providers */}
      <div className="space-y-1.5">
        {providers.map((p) => (
          <div key={p.id}>
            <div className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl border border-line bg-panel/40">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.color }} />
              <span className="text-sm font-medium">{p.name}</span>
              <span className="text-[10px] px-1.5 py-px rounded bg-panel2 text-muted border border-line">
                {PROTOCOL_LABEL[p.protocol] || p.protocol}
              </span>
              <Dot on={!!p.has_key || /127\.0\.0\.1|localhost/.test(p.base_url)}
                title={p.has_key ? "Key stored" : "No key"} />
              <span className="text-[11px] text-muted font-mono truncate hidden md:inline">{p.default_model}</span>
              {p.limits && <Quota l={p.limits} />}
              <div className="ml-auto flex items-center gap-1 shrink-0">
                <button className="btn !px-2 !py-1 text-xs" onClick={() => test(p)} disabled={testing === p.id}>
                  {testing === p.id ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />} Test
                </button>
                <button className="btn !px-2 !py-1 text-xs"
                  onClick={() => { setEditing(editing?.id === p.id ? null : p); setAdding(undefined); }}>
                  <Pencil size={12} /> Edit
                </button>
                <button className="text-xs text-danger hover:underline px-1.5" onClick={() => remove(p)}>
                  Delete
                </button>
              </div>
            </div>
            {editing?.id === p.id && (
              <div className="mt-2">
                <ProviderForm editing={p} others={providers.filter((x) => x.id !== p.id)}
                  onCancel={() => setEditing(null)}
                  onDone={() => { setEditing(null); load(); }} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* add */}
      {adding === undefined ? (
        <div className="relative mt-3" ref={menuRef}>
          <button className="btn" onClick={() => setMenu((v) => !v)}>
            <Plus size={14} /> Add provider <ChevronDown size={13} />
          </button>
          {menu && (
            <div className="absolute z-20 mt-1 w-80 card p-1 max-h-96 overflow-auto shadow-card">
              {unused.map((p) => (
                <button key={p.id} className="w-full text-left px-2 py-2 rounded hover:bg-panel2 flex items-start gap-2"
                  onClick={() => { setAdding(p); setMenu(false); setEditing(null); }}>
                  <span className="w-2 h-2 rounded-full mt-1.5 shrink-0" style={{ background: p.color }} />
                  <span className="min-w-0">
                    <span className="text-sm block">{p.name}</span>
                    <span className="text-[11px] text-muted block truncate font-mono">{p.base_url}</span>
                  </span>
                  <span className="text-[10px] text-muted ml-auto shrink-0 mt-1">
                    {p.protocol === "openai" ? "bridge" : "direct"}
                  </span>
                </button>
              ))}
              {!!unused.length && <div className="h-px bg-line my-1" />}
              <button className="w-full text-left px-2 py-2 rounded hover:bg-panel2 text-sm flex items-center gap-2"
                onClick={() => { setAdding(null); setMenu(false); setEditing(null); }}>
                <Cpu size={13} className="text-muted" /> Custom provider…
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="mt-3">
          <ProviderForm preset={adding} onCancel={() => setAdding(undefined)}
            onDone={() => { setAdding(undefined); load(); }} />
        </div>
      )}

      <p className="text-[11px] text-muted mt-3 flex items-start gap-1.5">
        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
        A model drives the same agent loop Claude does, which sends a large system prompt and many tools.
        A small local model can answer well but may not hold a long tool-using session together.
      </p>
    </Section>
  );
}
