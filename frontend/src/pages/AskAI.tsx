import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check, ChevronDown, Copy, ExternalLink, FileText, Loader2, MessageSquarePlus, Paperclip, Pin, Search, Send, Sparkles, Star, Trash2, X,
} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { ChatAttachment, ChatConvo, ChatMessage, ChatModel } from "../types";
import { cls } from "../components/ui";

// vendor → friendly name + brand colour (the left bar shows which AI each chat is)
const VENDORS: Record<string, { name: string; color: string }> = {
  openai: { name: "ChatGPT", color: "#10a37f" },
  deepseek: { name: "DeepSeek", color: "#4d6bfe" },
  anthropic: { name: "Claude", color: "#cc785c" },
  google: { name: "Gemini", color: "#1a73e8" },
  "meta-llama": { name: "Llama", color: "#0866ff" },
  mistralai: { name: "Mistral", color: "#fa520f" },
  qwen: { name: "Qwen", color: "#615ced" },
  "x-ai": { name: "Grok", color: "#1a1a1a" },
  cohere: { name: "Cohere", color: "#39594d" },
  perplexity: { name: "Perplexity", color: "#20808d" },
  microsoft: { name: "Phi", color: "#0078d4" },
  nvidia: { name: "Nvidia", color: "#76b900" },
  amazon: { name: "Nova", color: "#ff9900" },
  nousresearch: { name: "Nous", color: "#7c3aed" },
};
function vendorInfo(id: string) {
  const p = ((id || "").split("/")[0] || "").toLowerCase();
  if (VENDORS[p]) return { ...VENDORS[p], initial: VENDORS[p].name[0] };
  const name = p ? p[0].toUpperCase() + p.slice(1) : "AI";
  return { name, color: "#6b7280", initial: name[0] || "A" };
}
function Avatar({ id, size = 22 }: { id: string; size?: number }) {
  const v = vendorInfo(id);
  return (
    <span style={{ background: v.color, width: size, height: size }}
      className="shrink-0 rounded-md inline-flex items-center justify-center text-white font-bold leading-none">
      <span style={{ fontSize: Math.round(size * 0.48) }}>{v.initial}</span>
    </span>
  );
}

// turn bare URLs into clickable links. target="_blank" → Electron's window-open handler
// sends them to the real default browser (Chrome); in a normal browser it opens a tab.
const URL_RE = /(https?:\/\/[^\s<>"'`)\]]+)/g;
function linkify(text: string) {
  return text.split(URL_RE).map((part, i) =>
    /^https?:\/\//.test(part) ? (
      <a key={i} href={part} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
        className="text-brand underline decoration-brand/40 hover:decoration-brand break-all">{part}</a>
    ) : <span key={i}>{part}</span>
  );
}

// minimal markdown: fenced code blocks + wrapped text (with clickable links)
function renderContent(text: string) {
  return (text || "").split(/```/).map((seg, i) =>
    i % 2 === 1 ? (
      <pre key={i} className="my-1.5 bg-bg border border-line rounded-lg px-2.5 py-2 overflow-x-auto text-[12px] font-mono whitespace-pre-wrap break-words">
        {seg.replace(/^[a-zA-Z0-9]*\n/, "")}
      </pre>
    ) : (
      <span key={i} className="whitespace-pre-wrap break-words">{linkify(seg)}</span>
    )
  );
}

// current top models (real, valid, versioned OpenRouter ids). Ordered "great AND affordable
// first" — GPT-5/Gemini/R1 are top-tier and cheap; Opus/Sonnet are the very best but cost more.
const DEFAULT_PINS = [
  "openai/gpt-5",                       // top quality · cheap · works on low credit
  "google/gemini-3.1-pro-preview",      // top quality · 1M context · cheap
  "deepseek/deepseek-r1-0528",          // best value — reasoning, ~free
  "anthropic/claude-opus-4.7",          // best overall (needs more credit)
  "anthropic/claude-sonnet-4.6",        // excellent (needs credit)
  "meta-llama/llama-3.3-70b-instruct:free", // best fully-free
];
const PINS_VERSION = "2026-06c";        // bump to re-seed everyone's pins past the stale/invalid set

export default function AskAI() {
  const toast = useStore((s) => s.toast);
  const [hasKey, setHasKey] = useState<boolean | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [models, setModels] = useState<ChatModel[]>([]);
  const [convos, setConvos] = useState<ChatConvo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [model, setModel] = useState<string>(() => localStorage.getItem("askai-model") || "");  // "" → loadModels picks a free default
  const [pinned, setPinned] = useState<string[]>(() => {
    try {
      if (localStorage.getItem("askai-pins-v") !== PINS_VERSION) {   // one-time re-seed off the stale set
        localStorage.setItem("askai-pins-v", PINS_VERSION);
        localStorage.setItem("askai-pins", JSON.stringify(DEFAULT_PINS));
        return DEFAULT_PINS;
      }
      return JSON.parse(localStorage.getItem("askai-pins") || "null") || DEFAULT_PINS;
    } catch { return DEFAULT_PINS; }
  });
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [mq, setMq] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [copied, setCopied] = useState(-1);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const streamingInto = useRef<string | null>(null);   // guards the reload race for a fresh chat

  const loadConvos = () => api.chatConversations().then((r) => setConvos(r.conversations)).catch(() => {});
  const loadModels = () => api.chatModels().then((r) => {
    setModels(r.models || []);
    setHasKey(r.has_key);
    setModel((m) => (r.models || []).some((x) => x.id === m) ? m : ((r.models || []).find((x) => x.free && /deepseek|llama|qwen/i.test(x.id))?.id || m));
  }).catch(() => {});

  useEffect(() => { api.chatKey().then((r) => setHasKey(r.has_key)); loadModels(); loadConvos(); /* eslint-disable-next-line */ }, []);
  useEffect(() => { localStorage.setItem("askai-model", model); }, [model]);
  useEffect(() => { localStorage.setItem("askai-pins", JSON.stringify(pinned)); }, [pinned]);

  // load messages when switching conversation — but NOT while we're streaming into it (avoids clobbering optimistic state)
  useEffect(() => {
    if (!activeId) { if (streamingInto.current === null) setMessages([]); return; }
    if (activeId === streamingInto.current) return;
    api.chatGetConversation(activeId).then((c) => { setMessages(c.messages || []); if (c.model) setModel(c.model); }).catch(() => {});
  }, [activeId]);

  useEffect(() => { const el = scrollRef.current; if (el) el.scrollTop = el.scrollHeight; }, [messages, streaming]);

  function newChat() { streamingInto.current = null; setActiveId(null); setMessages([]); setTimeout(() => ta.current?.focus(), 0); }
  function togglePin(id: string, e?: React.MouseEvent) { e?.stopPropagation(); setPinned((p) => p.includes(id) ? p.filter((x) => x !== id) : [id, ...p]); }

  async function saveKey() {
    const k = keyInput.trim(); if (!k) return;
    try { const r = await api.chatSetKey(k); setHasKey(r.has_key); setKeyInput(""); toast("OpenRouter key saved", "ok"); loadModels(); }
    catch (e: any) { toast(e.message, "danger"); }
  }
  async function removeChat(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await api.chatDeleteConversation(id).catch(() => {});
    if (activeId === id) { setActiveId(null); setMessages([]); }
    loadConvos();
  }
  function copyMsg(text: string, i: number) { navigator.clipboard?.writeText(text); setCopied(i); setTimeout(() => setCopied(-1), 1200); }
  async function onFiles(files: FileList | null) {
    if (!files || !files.length) return;
    setUploading(true);
    for (const f of Array.from(files)) {
      try { const a = await api.chatUpload(f); setAttachments((x) => [...x, a]); }
      catch (e: any) { toast(e?.message || "upload failed", "danger"); }
    }
    setUploading(false);
  }

  async function send() {
    const text = input.trim();
    if ((!text && attachments.length === 0) || streaming) return;
    if (!hasKey) { toast("Add your OpenRouter API key first", "warn"); return; }
    let cid = activeId;
    if (!cid) {
      const c = await api.chatNewConversation(model).catch(() => null);
      if (!c) { toast("Could not start a chat", "danger"); return; }
      cid = c.id;
      streamingInto.current = cid;   // mark BEFORE setActiveId so the reload effect skips it
      setActiveId(cid);
    }
    const atts = attachments;
    setInput(""); setAttachments([]);
    setMessages((m) => [...m, { role: "user", content: text, attachments: atts }, { role: "assistant", content: "", model }]);
    setStreaming(true);
    try {
      const resp = await fetch(`/api/chat/conversations/${encodeURIComponent(cid)}/send`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, model, attachments: atts }),
      });
      const reader = resp.body?.getReader();
      if (!reader) throw new Error("no stream");
      const dec = new TextDecoder();
      let buf = "";
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() || "";
        for (const ln of lines) {
          if (!ln.trim()) continue;
          let obj: any; try { obj = JSON.parse(ln); } catch { continue; }
          if (obj.delta || obj.error) {
            const add = obj.delta || ("\n\n⚠️ " + obj.error);
            if (obj.error) toast(obj.error, "danger");
            setMessages((m) => {
              if (!m.length) return m;
              const c = [...m]; const last = c[c.length - 1];
              if (!last || last.role !== "assistant") return m;     // defensive: never crash on a bad tail
              c[c.length - 1] = { ...last, content: last.content + add };
              return c;
            });
          } else if (obj.title) {
            loadConvos();
          }
        }
      }
    } catch (e: any) {
      toast("Chat failed: " + (e?.message || e), "danger");
    } finally {
      setStreaming(false);
      streamingInto.current = null;
      loadConvos();
    }
  }

  const curModel = models.find((m) => m.id === model);
  const pinnedModels = useMemo(() => pinned.map((id) => models.find((m) => m.id === id)).filter(Boolean) as ChatModel[], [pinned, models]);
  const filtered = useMemo(() => {
    const q = mq.trim().toLowerCase();
    return models.filter((m) => (!freeOnly || m.free) && (!q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))).slice(0, 250);
  }, [models, mq, freeOnly]);

  // ── key setup gate ────────────────────────────────────────────────────────
  if (hasKey === false) {
    return (
      <div className="h-full flex items-center justify-center bg-bg p-6">
        <div className="card p-6 w-[480px] max-w-full space-y-3">
          <div className="flex items-center gap-2 text-brand font-semibold text-base"><Sparkles size={18} /> Ask AI — connect OpenRouter</div>
          <p className="text-sm text-muted leading-relaxed">
            One key unlocks <b className="text-text">340+ models</b> — DeepSeek, Llama, Qwen, Gemini, GPT, Claude and more — many <span className="text-ok font-medium">free</span>.
            Stored locally on this machine only.
          </p>
          <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer" className="text-xs text-brand inline-flex items-center gap-1 hover:underline">
            Get a free key at openrouter.ai/keys <ExternalLink size={11} />
          </a>
          <div className="flex gap-2">
            <input className="input flex-1 font-mono text-xs" type="password" placeholder="sk-or-v1-…" value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") saveKey(); }} />
            <button className="btn-primary !px-4" onClick={saveKey} disabled={!keyInput.trim()}>Save</button>
          </div>
        </div>
      </div>
    );
  }

  const modelRow = (m: ChatModel, isPinned: boolean) => (
    <button key={m.id} onClick={() => { setModel(m.id); setModelOpen(false); setMq(""); }}
      className={cls("group/m w-full text-left px-2 py-1.5 rounded-lg flex items-center gap-2 hover:bg-panel2 text-xs", m.id === model && "bg-panel2")}>
      <Avatar id={m.id} size={18} />
      <span className="truncate flex-1">{m.name}</span>
      {m.free && <span className="chip text-ok text-[9px] shrink-0">free</span>}
      {!!m.context && <span className="text-muted/40 text-[10px] shrink-0">{Math.round(m.context / 1000)}k</span>}
      <span onClick={(e) => togglePin(m.id, e)} title={isPinned ? "Unpin" : "Pin to top"}
        className={cls("p-0.5 rounded shrink-0", isPinned ? "text-warn" : "opacity-0 group-hover/m:opacity-100 text-muted/50 hover:text-warn")}>
        <Star size={13} className={isPinned ? "fill-warn" : ""} />
      </span>
    </button>
  );

  return (
    <div className="h-full flex bg-bg min-h-0">
      {/* sidebar */}
      <div className="w-64 shrink-0 border-r border-line bg-panel/40 flex flex-col min-h-0">
        <div className="p-2.5">
          <button className="btn-primary w-full justify-center gap-2 !py-2.5 rounded-xl" onClick={newChat}>
            <MessageSquarePlus size={16} /> New chat
          </button>
        </div>
        <div className="px-3 pb-1 text-[10px] uppercase tracking-wide text-muted/50">Chats</div>
        <div className="flex-1 min-h-0 overflow-auto px-1.5 pb-2 space-y-0.5">
          {convos.length === 0 && <div className="text-muted/50 text-xs px-2 py-3">No chats yet — start one above.</div>}
          {convos.map((c) => (
            <div key={c.id} onClick={() => setActiveId(c.id)}
              className={cls("group rounded-lg px-2 py-2 cursor-pointer flex items-center gap-2 transition-colors", activeId === c.id ? "bg-panel2" : "hover:bg-panel2/60")}>
              <Avatar id={c.model} size={24} />
              <div className="flex-1 min-w-0">
                <div className="text-[13px] truncate text-text/90 leading-tight">{c.title}</div>
                <div className="text-[10px] text-muted/60 truncate">{vendorInfo(c.model).name} · {c.messages} msg</div>
              </div>
              <button className="opacity-0 group-hover:opacity-100 text-muted/50 hover:text-danger shrink-0" title="Delete chat" onClick={(e) => removeChat(c.id, e)}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
        <div className="px-3 py-1.5 border-t border-line text-[10px] text-muted/50 flex items-center gap-1">
          <Sparkles size={10} className="text-brand/60" /> OpenRouter · {models.length} models
        </div>
      </div>

      {/* main */}
      <div className="flex-1 min-w-0 flex flex-col min-h-0">
        {/* header: model picker */}
        <div className="h-12 shrink-0 border-b border-line bg-panel/60 flex items-center gap-2 px-4 relative">
          <button className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl border border-line bg-panel2/60 hover:bg-panel2 text-sm transition-colors" onClick={() => setModelOpen((v) => !v)}>
            <Avatar id={model} size={20} />
            <span className="font-medium truncate max-w-[280px]">{curModel?.name || model}</span>
            {curModel?.free && <span className="chip text-ok text-[9px]">free</span>}
            <ChevronDown size={14} className="text-muted" />
          </button>
          {modelOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setModelOpen(false)} />
              <div className="absolute top-full left-4 mt-1 z-50 card p-2 w-[440px] max-w-[92vw] shadow-card">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <div className="flex items-center gap-1.5 flex-1 input !py-1.5">
                    <Search size={13} className="text-muted shrink-0" />
                    <input autoFocus className="bg-transparent outline-none text-xs flex-1" placeholder="Search 340+ models…" value={mq} onChange={(e) => setMq(e.target.value)} />
                  </div>
                  <button className={cls("chip text-[10px] cursor-pointer", freeOnly && "!text-ok !border-ok/50")} onClick={() => setFreeOnly((v) => !v)}>free only</button>
                </div>
                <div className="max-h-[60vh] overflow-auto">
                  {!mq && pinnedModels.length > 0 && (
                    <>
                      <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-warn/70 flex items-center gap-1"><Pin size={9} /> Pinned</div>
                      <div className="space-y-0.5 mb-1.5">{pinnedModels.map((m) => modelRow(m, true))}</div>
                      <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted/50">All models</div>
                    </>
                  )}
                  <div className="space-y-0.5">{filtered.map((m) => modelRow(m, pinned.includes(m.id)))}</div>
                  {filtered.length === 0 && <div className="text-muted/50 text-xs px-2 py-2">no match</div>}
                </div>
              </div>
            </>
          )}
          <span className="ml-auto text-[10px] text-muted/40">general questions · saved on the left</span>
        </div>

        {/* messages */}
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto px-4 py-5">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center gap-3">
              <Avatar id={model} size={48} />
              <div className="text-lg font-medium text-text/80">Ask {vendorInfo(model).name} anything</div>
              <div className="text-xs text-muted/60 max-w-sm">Pick a model up top (★ pin your favorites) · your chats are saved on the left.</div>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto space-y-5">
              {messages.map((m, i) => m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="flex flex-col items-end gap-1.5 max-w-[82%]">
                    {m.attachments && m.attachments.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 justify-end">
                        {m.attachments.map((a, j) => a.kind === "image" ? (
                          <img key={j} src={api.chatFileUrl(a.name)} alt={a.orig} className="max-h-52 rounded-xl border border-line" />
                        ) : (
                          <span key={j} className="chip gap-1"><FileText size={11} /><span className="truncate max-w-[150px]">{a.orig}</span></span>
                        ))}
                      </div>
                    )}
                    {m.content && <div className="bg-brand-600 text-white rounded-2xl rounded-br-md px-3.5 py-2 text-[13.5px] leading-relaxed whitespace-pre-wrap break-words">{m.content}</div>}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex gap-3 items-start group">
                  <Avatar id={m.model || model} size={28} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-[12px] font-medium text-text/70">{vendorInfo(m.model || model).name}</span>
                      {m.content && (
                        <button className="opacity-0 group-hover:opacity-100 text-muted/40 hover:text-text transition-opacity" title="Copy" onClick={() => copyMsg(m.content, i)}>
                          {copied === i ? <Check size={12} className="text-ok" /> : <Copy size={12} />}
                        </button>
                      )}
                    </div>
                    <div className="text-[13.5px] leading-relaxed text-text/90">
                      {m.content ? renderContent(m.content) : null}
                      {streaming && i === messages.length - 1 && <span className="inline-block w-1.5 h-4 ml-0.5 align-text-bottom bg-brand/70 animate-pulse rounded-sm" />}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* composer */}
        <div className="shrink-0 px-4 pb-4 pt-2">
          <div className="max-w-3xl mx-auto">
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2 px-1">
                {attachments.map((a, i) => (
                  <div key={i} className="relative">
                    {a.kind === "image" ? (
                      <img src={api.chatFileUrl(a.name)} className="h-14 w-14 object-cover rounded-lg border border-line" />
                    ) : (
                      <span className="inline-flex items-center gap-1 h-14 px-2.5 rounded-lg border border-line bg-panel2 text-xs max-w-[170px]"><FileText size={14} className="shrink-0 text-muted" /><span className="truncate">{a.orig}</span></span>
                    )}
                    <button className="absolute -top-1.5 -right-1.5 bg-panel border border-line rounded-full p-0.5 text-muted hover:text-danger"
                      onClick={() => setAttachments((x) => x.filter((_, j) => j !== i))}><X size={10} /></button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex items-end gap-1.5 bg-panel border border-line rounded-2xl p-1.5 shadow-card focus-within:border-brand/50 transition-colors">
              <label className="p-2 rounded-xl text-muted hover:text-text hover:bg-panel2 cursor-pointer shrink-0" title="Attach image or code/text file to review">
                {uploading ? <Loader2 size={18} className="animate-spin" /> : <Paperclip size={18} />}
                <input type="file" multiple className="hidden"
                  accept="image/*,.txt,.md,.markdown,.js,.ts,.tsx,.jsx,.cjs,.mjs,.py,.json,.css,.scss,.html,.vue,.svelte,.go,.rs,.java,.kt,.c,.h,.cpp,.cs,.rb,.php,.sh,.ps1,.yml,.yaml,.toml,.xml,.sql,.ini,.csv,.log"
                  onChange={(e) => { onFiles(e.target.files); e.currentTarget.value = ""; }} />
              </label>
              <textarea ref={ta} className="flex-1 resize-none bg-transparent outline-none text-sm px-1 py-2 max-h-[180px]" rows={1}
                placeholder={`Message ${vendorInfo(model).name}…`}
                value={input} onChange={(e) => { setInput(e.target.value); const t = e.target; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
              <button className="btn-primary !rounded-xl !px-3.5 shrink-0 h-[40px]" onClick={send} disabled={streaming || (!input.trim() && attachments.length === 0)} title="Send (Enter)">
                {streaming ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
              </button>
            </div>
            <div className="text-center text-[10px] text-muted/40 mt-1.5">Enter to send · Shift+Enter newline · 📎 attach images & code to review</div>
          </div>
        </div>
      </div>
    </div>
  );
}
