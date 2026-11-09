import { useEffect, useState } from "react";
import { Check, Download, ExternalLink, Loader2, Search, X } from "lucide-react";
import { api, type PolyHavenRow } from "../../api/client";
import { cls } from "../ui";

// Poly Haven's free (CC0) models, textures and HDRIs, brought into the project open in the Library.
//
// A model comes with two reference renders (textured, and clay: the shape only) and its real size,
// so the forge can score a build against it and check its proportions. Every download keeps its
// LICENSE.txt beside the files. The backend does the fetching (backend/asset_studio/polyhaven.py).

type Kind = "models" | "textures" | "hdris";
const KIND_LABEL: Record<Kind, string> = { models: "Models", textures: "Textures", hdris: "HDRIs" };

export function PolyHaven({ project, onDownloaded, onClose }: { project: string; onDownloaded: () => void; onClose: () => void }) {
  const [kind, setKind] = useState<Kind>("models");
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [category, setCategory] = useState("");
  const [rows, setRows] = useState<PolyHavenRow[]>([]);
  const [cats, setCats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [res, setRes] = useState("1k");
  const [getting, setGetting] = useState<Record<string, "busy" | "done" | string>>({});
  const [have, setHave] = useState<Set<string>>(new Set());

  useEffect(() => { const t = window.setTimeout(() => setDq(q), 250); return () => window.clearTimeout(t); }, [q]);

  useEffect(() => {
    let dead = false;
    setBusy(true);
    api.polyHavenSearch(kind, dq, category).then((r) => {
      if (dead) return;
      if (!r.ok) { setErr(r.error || "Poly Haven did not answer"); setRows([]); return; }
      setErr(""); setRows(r.rows || []); setTotal(r.total || 0); setCats(r.categories || {});
    }).catch((e) => { if (!dead) setErr(String(e?.message || e).slice(0, 200)); })
      .finally(() => { if (!dead) setBusy(false); });
    return () => { dead = true; };
  }, [kind, dq, category]);

  useEffect(() => {
    api.polyHavenDownloaded(project).then((r) => { if (r.ok) setHave(new Set((r.rows || []).map((x) => x.type + "/" + x.id))); }).catch(() => {});
  }, [project]);

  const get = async (row: PolyHavenRow) => {
    const k = kind + "/" + row.id;
    setGetting((g) => ({ ...g, [k]: "busy" }));
    try {
      const r = await api.polyHavenDownload(project, row.id, kind, res);
      if (!r.ok) { setGetting((g) => ({ ...g, [k]: r.error || "the download failed" })); return; }
      setGetting((g) => ({ ...g, [k]: "done" }));
      setHave((h) => new Set(h).add(k));
      onDownloaded();
    } catch (e: any) {
      setGetting((g) => ({ ...g, [k]: String(e?.message || e).slice(0, 200) }));
    }
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="shrink-0 h-9 flex items-center gap-2 px-3 border-b border-line text-[11px]">
        <span className="text-[10px] uppercase tracking-wide text-muted">Poly Haven</span>
        <span className="chip !py-0 text-[10px]" title="Creative Commons Zero: any use, commercial included, no credit needed. A LICENSE.txt is kept with every download.">CC0</span>
        {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
          <button key={k} onClick={() => { setKind(k); setCategory(""); }}
            className={cls("px-2 py-0.5 rounded", kind === k ? "bg-brand/15 text-brand" : "text-muted hover:text-text")}>{KIND_LABEL[k]}</button>
        ))}
        <label className="ml-2 flex items-center gap-1 text-muted">
          <Search size={11} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="axe, barrel, rock, studio…"
            className="bg-transparent outline-none border-b border-transparent focus:border-line text-text placeholder:text-muted/60 w-44" />
        </label>
        <select value={category} onChange={(e) => setCategory(e.target.value)} className="bg-panel2 border border-line rounded px-1 py-0.5 text-[11px]">
          <option value="">every category</option>
          {Object.entries(cats).map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
        </select>
        <select value={res} onChange={(e) => setRes(e.target.value)} title="Texture resolution of the download"
          className="bg-panel2 border border-line rounded px-1 py-0.5 text-[11px]">
          {["1k", "2k", "4k"].map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <span className="text-muted">{busy ? <Loader2 size={11} className="animate-spin" /> : `${total} found`}</span>
        <button onClick={onClose} className="ml-auto text-muted hover:text-text" title="Back to the project's own assets"><X size={13} /></button>
      </div>
      {err && <div className="shrink-0 px-3 py-1.5 text-xs text-warn bg-warn/10 border-b border-warn/30">{err}</div>}
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        <div className="flex flex-wrap gap-2">
          {rows.map((r) => {
            const k = kind + "/" + r.id;
            const st = getting[k];
            const owned = have.has(k);
            return (
              <div key={r.id} className="w-40 rounded-lg border border-line bg-panel2 overflow-hidden text-[11px]" title={r.tags.join(", ")}>
                <img src={r.thumb} alt="" loading="lazy" decoding="async" className="w-full h-28 object-cover bg-black/40" />
                <div className="px-1.5 py-1 space-y-1">
                  <div className="truncate text-text">{r.name}</div>
                  <div className="text-[10px] text-muted truncate">
                    {r.dimensions_m ? `${Math.max(...r.dimensions_m).toFixed(2)} m` : r.categories.slice(0, 2).join(", ")}
                    {r.polycount ? ` · ${r.polycount.toLocaleString()} tris` : ""}
                  </div>
                  <div className="flex items-center gap-1">
                    <button disabled={st === "busy"} onClick={() => get(r)}
                      title={owned ? "Already in this project. Download it again at the chosen resolution." : `Download into this project at ${res}`}
                      className={cls("chip !py-0 flex items-center gap-1", owned || st === "done" ? "text-ok border-ok/40" : "hover:text-text")}>
                      {st === "busy" ? <Loader2 size={10} className="animate-spin" /> : owned || st === "done" ? <Check size={10} /> : <Download size={10} />}
                      {st === "busy" ? "getting" : owned || st === "done" ? "in project" : "get"}
                    </button>
                    <a href={r.page} target="_blank" rel="noreferrer" className="ml-auto text-muted hover:text-text" title="Open its Poly Haven page"><ExternalLink size={11} /></a>
                  </div>
                  {st && st !== "busy" && st !== "done" && <div className="text-[10px] text-warn break-words">{st}</div>}
                </div>
              </div>
            );
          })}
        </div>
        {!busy && !rows.length && !err && <div className="text-muted text-xs">Nothing matches.</div>}
      </div>
    </div>
  );
}
