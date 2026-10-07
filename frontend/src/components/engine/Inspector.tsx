import { useEffect, useMemo, useState } from "react";
import Prism from "prismjs";
import { AlertTriangle, Check, Copy, Image as ImageIcon, Loader2, Sparkles } from "lucide-react";
import { api } from "../../api/client";
import type { EngineGen, EngineStats } from "../../types";
import { cls, timeAgo } from "../ui";
import { EngineChip } from "./EngineChip";
import type { LiveInfo } from "./Viewport";

// What is known about the selected generation: the figures the forge recorded, the figures this
// window measured when it re-ran the code, the code itself and the sheet. Recorded and measured
// are shown side by side on purpose — when they differ, the engine build or the code's inputs
// changed, and that is worth seeing before trusting either picture.

export type GenDetail = Awaited<ReturnType<typeof api.engineGeneration>>;

const FRAMED_TONE: Record<string, string> = {
  fit: "text-ok border-ok/40 bg-ok/10",
  widened: "text-warn border-warn/40 bg-warn/10",
  "still-clipped": "text-danger border-danger/40 bg-danger/10",
  "no-subject": "text-muted border-line bg-panel2",
};

const fmtN = (n?: number | null) => n == null ? "—" : n.toLocaleString();
const fmtV = (v?: number[]) => v && v.length === 3 ? v.map((x) => (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(2))).join(" × ") : "—";
const shortUrl = (u: string) => {
  try {
    const x = new URL(u, location.href);
    if (x.pathname.startsWith("/api/engine/module")) return "backend · " + (x.searchParams.get("project") || "").split(/[\\/]/).filter(Boolean).pop();
    return x.host + x.pathname;
  } catch { return u; }
};

interface Props {
  item: EngineGen | null;
  gen: GenDetail | null;
  live: LiveInfo | null;
  loading: boolean;
  error: string;
  justMade: boolean;
  /** The person corrected what this generation depicts; the list should show it at once. */
  onTagged?: (row: EngineGen) => void;
}

const SUBJECTS = ["character", "creature", "building", "prop", "vehicle", "environment", "weapon", "ui", "effect", "material", "test", "other"];

/** The shelf a generation sits on, and the one place a wrong guess is corrected. A choice made
 *  here is written to the record and never overwritten by a later guess. */
function Retag({ item, onTagged }: { item: EngineGen; onTagged?: (row: EngineGen) => void }) {
  const [subject, setSubject] = useState(item.subject || "other");
  const [tags, setTags] = useState((item.tags || []).join(", "));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const tagKey = (item.tags || []).join(",");
  useEffect(() => { setSubject(item.subject || "other"); setTags((item.tags || []).join(", ")); }, [item.id, item.subject, tagKey]);   // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (s: string, t: string) => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.engineTag(item.id, s, t.split(",").map((x) => x.trim()).filter(Boolean));
      if (!r.ok) throw new Error(r.error || "could not save");
      onTagged?.(r as unknown as EngineGen);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  };
  const by = item.subject_by === "user" ? "your choice" : item.subject_by === "agent" ? "tagged by the agent" : "a guess from the name and the code";
  return (
    <div className="rounded-lg border border-line bg-panel2/50 px-2.5 py-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-muted">Shows</span>
        <select className="input !py-0 h-6 text-[11px]" value={subject} disabled={busy}
          onChange={(e) => { setSubject(e.target.value); save(e.target.value, tags); }}>
          {SUBJECTS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="text-[10px] text-muted">{by}</span>
      </div>
      <input className="input !py-0 h-6 text-[11px] w-full mt-1.5" value={tags} placeholder="tags, comma separated — anything worth finding it by" disabled={busy}
        onChange={(e) => setTags(e.target.value)} onBlur={() => { if (tags !== (item.tags || []).join(", ")) save(subject, tags); }}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
      {err && <div className="text-[10px] text-danger mt-1">{err}</div>}
    </div>
  );
}

export function Inspector({ item, gen, live, loading, error, justMade, onTagged }: Props) {
  if (!item) {
    return (
      <div className="p-4 text-xs text-muted leading-relaxed">
        <div className="text-[10px] uppercase tracking-wide mb-2">Inspector</div>
        Nothing selected. The stats, the code and the sheet of whatever you pick appear here.
      </div>
    );
  }
  const rec: EngineStats = gen?.stats || { triangles: item.triangles ?? undefined, materials: item.materials ?? undefined,
                                           meshes: item.meshes ?? undefined, flat: item.flat, framed: item.framed };
  const ls = live?.stats || null;
  const when = new Date((gen?.ts || item.ts) * 1000);
  const replayable = item.kind === "forge" && item.replayable;
  return (
    <div className="p-3 space-y-3 text-xs">
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] font-semibold text-text break-all">{item.label || gen?.id || item.id || "—"}</span>
          {justMade && <span className="chip text-accent border-accent/40 bg-accent/10"><Sparkles size={11} /> just made</span>}
        </div>
        <div className="mt-1 flex items-center gap-1.5 flex-wrap text-muted">
          <span className="text-brand">{item.project_name || "—"}</span>
          <EngineChip kind={gen?.engine || item.engine} size="xs" />
          <span className="chip !py-0 text-[10px]">{item.kind}</span>
          <span title={when.toLocaleString()}>{timeAgo(gen?.ts || item.ts)}</span>
          {!item.ok && <span className="text-danger flex items-center gap-1"><AlertTriangle size={11} /> the forge reported an error</span>}
        </div>
      </div>

      {item.kind === "forge" && !!item.id && <Retag item={item} onTagged={onTagged} />}

      {item.sheet && (
        <a href={api.engineSheetUrl(item.sheet)} target="_blank" rel="noopener" title="Open the sheet at full size"
          className="block rounded-lg border border-line bg-black/40 overflow-hidden">
          <img src={api.engineSheetUrl(item.sheet)} alt="" className="w-full max-h-52 object-contain" />
        </a>
      )}
      {(gen?.views?.length || item.views?.length) ? (
        <div className="flex items-center gap-1 flex-wrap text-muted">
          <span className="text-[10px] uppercase tracking-wide mr-1">views</span>
          {(gen?.views || item.views).map((v) => <span key={v} className="chip !py-0 text-[10px] font-mono">{v}</span>)}
        </div>
      ) : null}

      {!replayable && (
        <div className="rounded-lg border border-line bg-panel2 px-3 py-2 text-muted leading-relaxed flex gap-2">
          <ImageIcon size={14} className="shrink-0 mt-0.5" />
          <span>
            {item.kind === "review"
              ? "A review is a picture of the running game, not code — it cannot be replayed. The sheet is what there is."
              : "This forge run was recorded without its code, so it cannot be replayed."}
          </span>
        </div>
      )}

      {replayable && (
        <>
          <Block title="Recorded by the forge">
            <Grid>
              <Cell label="triangles" value={fmtN(rec.triangles)} />
              <Cell label="meshes" value={fmtN(rec.meshes)} />
              <Cell label="materials" value={fmtN(rec.materials)} />
              <Cell label="objects" value={fmtN(rec.objects)} />
              {rec.draw_calls != null && <Cell label="draw calls" value={fmtN(rec.draw_calls)} />}
              {rec.radius != null && <Cell label="radius" value={String(rec.radius)} />}
            </Grid>
            <Row label="bbox size" value={fmtV(rec.bbox_size)} />
            <Row label="bbox centre" value={fmtV(rec.bbox_center)} />
            {rec.image && <Row label="image" value={`${rec.image[0]} × ${rec.image[1]} (flat)`} />}
            <Row label="framed" value={<Framed v={rec.framed} />} />
            {!!rec.material_names?.length && (
              <div className="mt-1.5 flex items-center gap-1 flex-wrap">
                {rec.material_names.map((m, i) => <span key={i} className="chip !py-0 text-[10px] font-mono">{m}</span>)}
                {rec.materials != null && rec.materials > rec.material_names.length && (
                  <span className="text-muted">+{rec.materials - rec.material_names.length}</span>
                )}
              </div>
            )}
            {!!rec.log?.length && <Log lines={rec.log} />}
          </Block>

          <Block title="In this viewer" right={live?.phase === "ready" ? <span className="text-ok">live</span>
                                                : live?.phase === "error" ? <span className="text-danger">failed</span>
                                                : <span className="text-muted flex items-center gap-1"><Loader2 size={10} className="animate-spin" /> running</span>}>
            {ls ? (
              <>
                <Grid>
                  <Cell label="triangles" value={fmtN(ls.triangles)}
                    warn={ls.triangles != null && rec.triangles != null && ls.triangles !== rec.triangles ? `recorded ${fmtN(rec.triangles)}` : ""} />
                  <Cell label="meshes" value={fmtN(ls.meshes)} />
                  <Cell label="materials" value={fmtN(ls.materials)} />
                  {ls.draw_calls != null && <Cell label="draw calls" value={fmtN(ls.draw_calls)} />}
                </Grid>
                <Row label="framed" value={<Framed v={ls.framed} />} />
                {live && live.ranMs > 0 && <Row label="ran in" value={`${live.ranMs < 1000 ? live.ranMs.toFixed(0) + " ms" : (live.ranMs / 1000).toFixed(1) + " s"}`} />}
                {live?.engineUrl && <Row label="engine" value={<span className="font-mono">{shortUrl(live.engineUrl)}</span>} />}
                {live?.result && <Row label="returned" value={<span className="font-mono break-all">{live.result}</span>} />}
                {!!ls.log?.length && <Log lines={ls.log} />}
              </>
            ) : live?.phase === "error" ? null : (
              <div className="text-muted">Measured once the code has run.</div>
            )}
            {live?.error && (
              <pre className="mt-1.5 font-mono text-[10px] text-danger/90 whitespace-pre-wrap break-words max-h-32 overflow-auto">{live.error}</pre>
            )}
          </Block>

          {loading && <div className="text-muted flex items-center gap-2"><Loader2 size={12} className="animate-spin" /> reading the generation…</div>}
          {error && <div className="text-danger flex items-center gap-2"><AlertTriangle size={12} /> {error}</div>}
          {gen && <CodeView code={gen.code} />}
        </>
      )}
    </div>
  );
}

function Block({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line p-2.5">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-muted mb-1.5">
        <span>{title}</span>{right}
      </div>
      {children}
    </div>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-3 gap-1">{children}</div>;
}

function Cell({ label, value, warn }: { label: string; value: string; warn?: string }) {
  return (
    <div className="rounded-md bg-panel2 px-2 py-1" title={warn || ""}>
      <div className="text-[10px] text-muted">{label}</div>
      <div className={cls("font-mono text-[12px]", warn ? "text-warn" : "text-text")}>{value}</div>
      {warn && <div className="text-[9px] text-warn/80 truncate">{warn}</div>}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 mt-1">
      <span className="text-[10px] text-muted w-20 shrink-0">{label}</span>
      <span className="text-text/90 font-mono text-[11px] min-w-0">{value}</span>
    </div>
  );
}

function Framed({ v }: { v?: string }) {
  if (!v) return <span className="text-muted">—</span>;
  return (
    <span className={cls("inline-flex items-center rounded-full border px-1.5 text-[10px] font-medium font-sans", FRAMED_TONE[v] || FRAMED_TONE["no-subject"])}>
      {v}
    </span>
  );
}

function Log({ lines }: { lines: string[] }) {
  return (
    <pre className="mt-1.5 font-mono text-[10px] text-muted whitespace-pre-wrap break-words max-h-28 overflow-auto bg-panel2 rounded-md p-1.5">
      {lines.join("\n")}
    </pre>
  );
}

function CodeView({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const html = useMemo(() => {
    try { return Prism.highlight(code, Prism.languages.javascript, "javascript"); } catch { return null; }
  }, [code]);
  const n = code.split("\n").length;
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); window.setTimeout(() => setCopied(false), 1200); }
    catch { /* no clipboard in this context */ }
  };
  return (
    <div className="rounded-lg border border-line overflow-hidden">
      <div className="flex items-center gap-2 px-2 py-1 bg-panel2 border-b border-line text-[10px] uppercase tracking-wide text-muted">
        <span>code · {n} line{n === 1 ? "" : "s"}</span>
        <button onClick={copy} className="ml-auto normal-case tracking-normal flex items-center gap-1 hover:text-text">
          {copied ? <Check size={11} /> : <Copy size={11} />} {copied ? "copied" : "copy"}
        </button>
      </div>
      <div className="flex text-[11px] leading-[1.45] font-mono max-h-[28rem] overflow-auto bg-panel/50">
        <div className="select-none text-right text-muted/40 px-2 py-2 border-r border-line shrink-0" aria-hidden="true">
          {Array.from({ length: n }, (_, i) => <div key={i}>{i + 1}</div>)}
        </div>
        <pre className="flex-1 px-2 py-2 m-0 whitespace-pre">
          {html ? <code dangerouslySetInnerHTML={{ __html: html }} /> : <code>{code}</code>}
        </pre>
      </div>
    </div>
  );
}
