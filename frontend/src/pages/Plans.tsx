/**
 * Plans written in plan mode.
 *
 * In plan mode the agent may read but not write, and it saves what it worked out to
 * ~/.claude/plans/<name>.md before asking for approval. Those files are the build
 * phases — they just had nowhere to be seen. This lists them newest first, shows the
 * phases at a glance, and sends one straight to the workspace it is about so the
 * agent can build it.
 */
import { useEffect, useMemo, useState } from "react";
import {
  ClipboardList, FileText, FolderOpen, Hammer, Loader2, RefreshCw, Search, Trash2,
} from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { PlanInfo } from "../types";
import { Empty, cls, timeAgo } from "../components/ui";

const ENGINE_COLOR: Record<string, string> = {
  claude: "#d97757", kimi: "#6a5cff", qwen: "#615ced",
};

/** Minimal markdown for a plan: headings, bullets, numbered steps, code and bold. */
function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => {
    const out: { kind: string; text: string }[] = [];
    let fence: string[] | null = null;
    for (const raw of text.split("\n")) {
      if (raw.trim().startsWith("```")) {
        if (fence) { out.push({ kind: "code", text: fence.join("\n") }); fence = null; }
        else fence = [];
        continue;
      }
      if (fence) { fence.push(raw); continue; }
      const line = raw.replace(/\s+$/, "");
      if (!line.trim()) { out.push({ kind: "gap", text: "" }); continue; }
      const h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) { out.push({ kind: `h${h[1].length}`, text: h[2] }); continue; }
      const li = /^\s*(?:[-*]|\d+[.)])\s+(.*)$/.exec(line);
      out.push(li ? { kind: "li", text: li[1] } : { kind: "p", text: line });
    }
    if (fence) out.push({ kind: "code", text: fence.join("\n") });
    return out;
  }, [text]);

  const inline = (s: string) => {
    const parts = s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
    return parts.map((p, i) => {
      if (p.startsWith("`") && p.endsWith("`"))
        return <code key={i} className="px-1 py-px rounded bg-panel2 text-brand font-mono text-[0.9em]">{p.slice(1, -1)}</code>;
      if (p.startsWith("**") && p.endsWith("**"))
        return <strong key={i} className="text-text">{p.slice(2, -2)}</strong>;
      return <span key={i}>{p}</span>;
    });
  };

  return (
    <div className="text-sm leading-relaxed text-muted">
      {blocks.map((b, i) => {
        if (b.kind === "gap") return <div key={i} className="h-2" />;
        if (b.kind === "code")
          return <pre key={i} className="my-2 p-2.5 rounded-lg bg-panel2 border border-line overflow-x-auto text-xs font-mono text-text">{b.text}</pre>;
        if (b.kind === "h1") return <h2 key={i} className="text-base font-semibold text-text mt-4 mb-1.5">{inline(b.text)}</h2>;
        if (b.kind === "h2") return <h3 key={i} className="text-sm font-semibold text-text mt-4 mb-1 flex items-center gap-2"><span className="w-1 h-3.5 rounded-full bg-brand" />{inline(b.text)}</h3>;
        if (b.kind === "h3" || b.kind === "h4") return <h4 key={i} className="text-[13px] font-semibold text-text/90 mt-3 mb-1">{inline(b.text)}</h4>;
        if (b.kind === "li") return <div key={i} className="flex gap-2 pl-1 my-0.5"><span className="text-brand shrink-0">·</span><span>{inline(b.text)}</span></div>;
        return <p key={i} className="my-1">{inline(b.text)}</p>;
      })}
    </div>
  );
}

export default function Plans() {
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [sel, setSel] = useState<PlanInfo | null>(null);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const openInWorkspace = useStore((s) => s.openInWorkspace);
  const toast = useStore((s) => s.toast);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.plans();
      setPlans(r.plans);
      setSel((cur) => r.plans.find((p) => p.id === cur?.id) || r.plans[0] || null);
    } catch { /* backend down */ } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (!sel) { setText(""); return; }
    let dead = false;
    api.planFile(sel.file).then((r) => { if (!dead) setText(r.text); }).catch(() => setText(""));
    return () => { dead = true; };
  }, [sel?.file]);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return plans;
    return plans.filter((p) => (p.title + " " + p.phases.join(" ") + " " + p.root).toLowerCase().includes(s));
  }, [plans, q]);

  async function build() {
    if (!sel) return;
    if (!sel.root) {
      toast("This plan does not name a folder inside an open workspace — open the project first.", "warn");
      return;
    }
    try { await navigator.clipboard.writeText(`Build this plan:\n\n${text}`); } catch { /* ignore */ }
    openInWorkspace(sel.root);
    toast("Plan copied — paste it into the chat and switch the mode off plan to build.", "ok");
  }

  async function remove(p: PlanInfo) {
    if (!confirm(`Delete "${p.title}"?`)) return;
    await api.deletePlan(p.file).catch(() => {});
    load();
  }

  return (
    <div className="h-full flex min-h-0">
      {/* list */}
      <div className="w-[340px] shrink-0 border-r border-line flex flex-col min-h-0">
        <div className="p-2.5 border-b border-line flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
            <input className="input !pl-7 !py-1.5 text-sm" placeholder="Search plans" value={q}
              onChange={(e) => setQ(e.target.value)} />
          </div>
          <button className="btn !px-2 !py-1.5" title="Refresh" onClick={load}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {!shown.length && !loading && (
            <Empty icon={<ClipboardList size={26} />}
              label={plans.length ? "No plan matches that." : "No plans yet — set the chat's mode to “plan” and ask for one."} />
          )}
          {shown.map((p) => (
            <button key={p.id} onClick={() => setSel(p)}
              className={cls("w-full text-left px-3 py-2.5 border-b border-line/60 hover:bg-panel2/60 transition-colors",
                sel?.id === p.id && "bg-panel2")}>
              <div className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ background: ENGINE_COLOR[p.engine] || "#8b8b8b" }} title={p.engine} />
                <span className="text-sm font-medium truncate flex-1">{p.title}</span>
                <span className="text-[10px] text-muted shrink-0">{timeAgo(p.mtime)}</span>
              </div>
              {!!p.phases.length && (
                <div className="text-[11px] text-muted mt-1 truncate">
                  {p.phases.length} phase{p.phases.length > 1 ? "s" : ""} · {p.phases.slice(0, 3).join(" · ")}
                </div>
              )}
              {p.root && (
                <div className="text-[10px] text-muted/70 mt-0.5 truncate font-mono">
                  {p.root.split(/[\\/]/).pop()}
                </div>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* detail */}
      <div className="flex-1 min-w-0 flex flex-col min-h-0">
        {!sel ? (
          <Empty icon={<FileText size={28} />} label="Pick a plan" />
        ) : (
          <>
            <div className="px-4 py-3 border-b border-line flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="font-semibold truncate">{sel.title}</div>
                <div className="text-[11px] text-muted mt-0.5 font-mono truncate">{sel.file}</div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {sel.root && (
                  <button className="btn !py-1.5" onClick={() => openInWorkspace(sel.root)} title={sel.root}>
                    <FolderOpen size={13} /> Open project
                  </button>
                )}
                <button className="btn-primary !py-1.5" onClick={build}>
                  <Hammer size={13} /> Send to build
                </button>
                <button className="btn !px-2 !py-1.5 text-danger" title="Delete" onClick={() => remove(sel)}>
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
            {!!sel.phases.length && (
              <div className="px-4 py-2.5 border-b border-line flex flex-wrap gap-1.5">
                {sel.phases.map((ph, i) => (
                  <span key={i} className="text-[11px] px-2 py-1 rounded-lg bg-panel2 border border-line flex items-center gap-1.5">
                    <span className="w-4 h-4 rounded-full bg-brand/15 text-brand text-[9px] flex items-center justify-center font-semibold">
                      {i + 1}
                    </span>
                    {ph}
                  </span>
                ))}
              </div>
            )}
            <div className="flex-1 overflow-y-auto px-4 py-3">
              {text ? <Markdown text={text} /> : <Loader2 size={16} className="animate-spin text-muted" />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
