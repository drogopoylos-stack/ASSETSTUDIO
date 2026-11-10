import { useEffect, useState } from "react";
import { Check, Download, ExternalLink, Loader2, Search, UserPlus, X } from "lucide-react";
import { api, type PolyPizzaRow, type RefRow } from "../../api/client";
import { cls } from "../ui";

// BASE MODELS: what Poly Haven does not have, brought into the project open in the Library.
//
//  - Humans: Blender Studio's Human Base Meshes (CC0) at real size, and a human made to order by
//    MPFB2 (MakeHuman for Blender, CC0 output) — a height in metres, a build, an optional game or
//    Mixamo rig. Both made by Blender in the background (backend refs.py).
//  - Poly Pizza: low-poly game models, many rigged and animated (Quaternius's animals, characters
//    and monsters), CC0 or CC-BY; a CC-BY one keeps its credit in LICENSE.txt (backend polypizza.py).
//
// "get" copies the model into the game (assets/references/ or assets/polypizza/), where the Library
// lists it and the editor and the forge can open it.

type Tab = "humans" | "polypizza";

export function BaseModels({ project, onDownloaded, onClose }: { project: string; onDownloaded: () => void; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("humans");
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="shrink-0 h-9 flex items-center gap-2 px-3 border-b border-line text-[11px]">
        <span className="text-[10px] uppercase tracking-wide text-muted">Base models</span>
        {(["humans", "polypizza"] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cls("px-2 py-0.5 rounded", tab === t ? "bg-brand/15 text-brand" : "text-muted hover:text-text")}>
            {t === "humans" ? "Humans (Blender)" : "Creatures & game models (Poly Pizza)"}
          </button>
        ))}
        <button onClick={onClose} className="ml-auto text-muted hover:text-text" title="Back to the project's own assets"><X size={13} /></button>
      </div>
      {tab === "humans" ? <Humans project={project} onDownloaded={onDownloaded} /> : <PolyPizza project={project} onDownloaded={onDownloaded} />}
    </div>
  );
}

function Humans({ project, onDownloaded }: { project: string; onDownloaded: () => void }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<RefRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ready, setReady] = useState<boolean | null>(null);
  const [state, setState] = useState<Record<string, string>>({});
  const [tick, setTick] = useState(0);
  const [h, setH] = useState({ height_m: 1.75, gender: 1, age_years: 25, muscle: 0.5, weight: 0.5, proportions: 0.5, rig: "game_engine", name: "human" });

  useEffect(() => { api.refsStatus().then((s) => setReady(s.humans && s.mpfb)).catch(() => setReady(false)); }, [tick]);
  useEffect(() => {
    let dead = false;
    const t = window.setTimeout(() => {
      setBusy(true);
      api.refsSearch(q).then((r) => { if (!dead) { setRows(r.rows || []); setErr(r.ok ? "" : r.error || ""); } })
        .catch((e) => { if (!dead) setErr(String(e?.message || e).slice(0, 200)); })
        .finally(() => { if (!dead) setBusy(false); });
    }, 200);
    return () => { dead = true; window.clearTimeout(t); };
  }, [q, tick]);

  const prepare = async () => {
    setState((s) => ({ ...s, _prepare: "busy" }));
    const r = await api.refsPrepare().catch((e) => ({ ok: false, error: String(e?.message || e) }));
    setState((s) => ({ ...s, _prepare: r.ok ? "done" : (r as any).error || "failed" }));
    setTick((t) => t + 1);
  };
  const get = async (row: RefRow) => {
    setState((s) => ({ ...s, [row.id]: "busy" }));
    const r = await api.refsGet(row.id, project).catch((e) => ({ ok: false, error: String(e?.message || e) }) as any);
    setState((s) => ({ ...s, [row.id]: r.ok ? "done" : r.error || "failed" }));
    if (r.ok) onDownloaded();
  };
  const make = async () => {
    setState((s) => ({ ...s, _make: "busy" }));
    const r = await api.refsHuman({ ...h, project }).catch((e) => ({ ok: false, error: String(e?.message || e) }) as any);
    setState((s) => ({ ...s, _make: r.ok ? `made ${r.dimensions_m?.[1]} m tall${r.bones ? `, ${r.bones} bones` : ""} → ${r.project_model}` : r.error || "failed" }));
    if (r.ok) { onDownloaded(); setTick((t) => t + 1); }
  };
  const num = (k: keyof typeof h, label: string, min: number, max: number, step: number, hint = "") => (
    <label className="flex items-center gap-1" title={hint}>
      <span className="text-muted">{label}</span>
      <input type="number" min={min} max={max} step={step} value={h[k] as number}
        onChange={(e) => setH({ ...h, [k]: +e.target.value })} className="input !py-0 w-16" />
    </label>
  );

  return (
    <>
      <div className="shrink-0 flex flex-wrap items-center gap-2 px-3 py-1.5 border-b border-line text-[11px]">
        <UserPlus size={12} className="text-brand" />
        <span className="text-text">Make a human</span>
        {num("height_m", "height m", 0.5, 3, 0.01)}
        <label className="flex items-center gap-1"><span className="text-muted">body</span>
          <select value={h.gender} onChange={(e) => setH({ ...h, gender: +e.target.value })} className="bg-panel2 border border-line rounded px-1 py-0.5">
            <option value={1}>male</option><option value={0}>female</option><option value={0.5}>between</option>
          </select></label>
        {num("age_years", "age", 1, 90, 1)}
        {num("muscle", "muscle", 0, 1, 0.05, "0 to 1")}
        {num("weight", "weight", 0, 1, 0.05, "0 to 1")}
        <label className="flex items-center gap-1"><span className="text-muted">rig</span>
          <select value={h.rig} onChange={(e) => setH({ ...h, rig: e.target.value })} className="bg-panel2 border border-line rounded px-1 py-0.5"
            title="game_engine: Unreal-style bones. mixamo: Mixamo's bone names, so Mixamo animations drive it.">
            <option value="">none</option><option value="game_engine">game</option><option value="mixamo">Mixamo</option>
          </select></label>
        <input value={h.name} onChange={(e) => setH({ ...h, name: e.target.value })} className="input !py-0 w-24" title="name" />
        <button onClick={make} disabled={state._make === "busy" || ready === false} className="btn-primary !py-0.5">
          {state._make === "busy" ? <Loader2 size={11} className="animate-spin" /> : "make (≈5 s)"}</button>
        {state._make && state._make !== "busy" && <span className="text-muted break-all">{state._make}</span>}
      </div>
      <div className="shrink-0 flex items-center gap-2 px-3 py-1 border-b border-line text-[11px]">
        <Search size={11} className="text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="male body, hand, stylized head…"
          className="bg-transparent outline-none text-text placeholder:text-muted/60 w-56" />
        <span className="chip !py-0 text-[10px]" title="Creative Commons Zero: any use, no credit needed">CC0</span>
        <span className="text-muted">{busy ? <Loader2 size={11} className="animate-spin" /> : `${rows.length} found`}</span>
        {ready === false && (
          <button onClick={prepare} disabled={state._prepare === "busy"} className="btn-ghost !py-0.5 ml-auto"
            title="Download Blender's Human Base Meshes and MPFB2 (about 100 MB) and prepare them with Blender, once">
            {state._prepare === "busy" ? <><Loader2 size={11} className="animate-spin" /> preparing (minutes)…</> : "prepare the library"}</button>
        )}
      </div>
      {err && <div className="shrink-0 px-3 py-1.5 text-xs text-warn bg-warn/10 border-b border-warn/30">{err}</div>}
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        <div className="flex flex-wrap gap-2">
          {rows.map((r) => {
            const st = state[r.id];
            return (
              <div key={r.id} className="w-40 rounded-lg border border-line bg-panel2 overflow-hidden text-[11px]" title={r.source}>
                <img src={api.engineSheetUrl(r.front_path)} alt="" loading="lazy" className="w-full h-36 object-contain bg-white" />
                <div className="px-1.5 py-1 space-y-1">
                  <div className="truncate text-text">{r.name}</div>
                  <div className="text-[10px] text-muted truncate">{r.dimensions_m?.[1]?.toFixed(2)} m tall · {r.triangles?.toLocaleString()} tris{r.bones ? ` · ${r.bones} bones` : ""}</div>
                  <button disabled={st === "busy"} onClick={() => get(r)} title="Copy into this game's assets/references/"
                    className={cls("chip !py-0 flex items-center gap-1", st === "done" ? "text-ok border-ok/40" : "hover:text-text")}>
                    {st === "busy" ? <Loader2 size={10} className="animate-spin" /> : st === "done" ? <Check size={10} /> : <Download size={10} />}
                    {st === "busy" ? "getting" : st === "done" ? "in project" : "get"}
                  </button>
                  {st && st !== "busy" && st !== "done" && <div className="text-[10px] text-warn break-words">{st}</div>}
                </div>
              </div>
            );
          })}
        </div>
        {!busy && !rows.length && <div className="text-muted text-xs">{ready === false ? "The library is not prepared on this computer yet." : "Nothing matches."}</div>}
      </div>
    </>
  );
}

function PolyPizza({ project, onDownloaded }: { project: string; onDownloaded: () => void }) {
  const [q, setQ] = useState("wolf");
  const [animated, setAnimated] = useState(true);
  const [cc0, setCc0] = useState(true);
  const [rows, setRows] = useState<PolyPizzaRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [key, setKey] = useState<boolean | null>(null);
  const [state, setState] = useState<Record<string, string>>({});

  useEffect(() => { api.polyPizzaStatus().then((s) => setKey(s.key)).catch(() => setKey(false)); }, []);
  useEffect(() => {
    if (!q.trim() || key === false) { setRows([]); return; }
    let dead = false;
    const t = window.setTimeout(() => {
      setBusy(true);
      api.polyPizzaSearch(q, animated, cc0 ? "CC0" : "").then((r) => {
        if (dead) return;
        setRows(r.rows || []); setErr(r.ok ? "" : r.error || "Poly Pizza did not answer");
      }).catch((e) => { if (!dead) setErr(String(e?.message || e).slice(0, 200)); })
        .finally(() => { if (!dead) setBusy(false); });
    }, 300);
    return () => { dead = true; window.clearTimeout(t); };
  }, [q, animated, cc0, key]);

  const get = async (row: PolyPizzaRow) => {
    setState((s) => ({ ...s, [row.id]: "busy" }));
    const r = await api.polyPizzaDownload(project, row.id).catch((e) => ({ ok: false, error: String(e?.message || e) }) as any);
    setState((s) => ({ ...s, [row.id]: r.ok ? "done" : r.error || "failed" }));
    if (r.ok) onDownloaded();
  };

  return (
    <>
      <div className="shrink-0 flex items-center gap-2 px-3 py-1 border-b border-line text-[11px]">
        <Search size={11} className="text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="wolf, knight, dragon, tree…"
          className="bg-transparent outline-none text-text placeholder:text-muted/60 w-56" />
        <label className="flex items-center gap-1"><input type="checkbox" checked={animated} onChange={(e) => setAnimated(e.target.checked)} /> animated</label>
        <label className="flex items-center gap-1" title="CC0 needs no credit; CC-BY needs the creator's credit in the game">
          <input type="checkbox" checked={cc0} onChange={(e) => setCc0(e.target.checked)} /> CC0 only</label>
        <span className="text-muted">{busy ? <Loader2 size={11} className="animate-spin" /> : `${rows.length} found`}</span>
        <span className="ml-auto text-[10px] text-muted">sizes are the file's: scale to the real size</span>
      </div>
      {key === false && <div className="shrink-0 px-3 py-1.5 text-xs text-warn bg-warn/10 border-b border-warn/30">
        No Poly Pizza key on this computer. Store one in the keychain as "polypizza".</div>}
      {err && <div className="shrink-0 px-3 py-1.5 text-xs text-warn bg-warn/10 border-b border-warn/30">{err}</div>}
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        <div className="flex flex-wrap gap-2">
          {rows.map((r) => {
            const st = state[r.id];
            return (
              <div key={r.id} className="w-40 rounded-lg border border-line bg-panel2 overflow-hidden text-[11px]" title={r.attribution}>
                <img src={r.thumb} alt="" loading="lazy" className="w-full h-28 object-cover bg-black/40" />
                <div className="px-1.5 py-1 space-y-1">
                  <div className="truncate text-text">{r.title}</div>
                  <div className="text-[10px] text-muted truncate">{r.creator} · {r.cc0 ? "CC0" : "CC-BY"}{r.animated ? " · animated" : ""} · {r.triangles} tris</div>
                  <div className="flex items-center gap-1">
                    <button disabled={st === "busy"} onClick={() => get(r)} title="Download into this game's assets/polypizza/ with its license and renders"
                      className={cls("chip !py-0 flex items-center gap-1", st === "done" ? "text-ok border-ok/40" : "hover:text-text")}>
                      {st === "busy" ? <Loader2 size={10} className="animate-spin" /> : st === "done" ? <Check size={10} /> : <Download size={10} />}
                      {st === "busy" ? "getting" : st === "done" ? "in project" : "get"}
                    </button>
                    <a href={"https://poly.pizza/m/" + r.id} target="_blank" rel="noreferrer" className="ml-auto text-muted hover:text-text" title="Its Poly Pizza page"><ExternalLink size={11} /></a>
                  </div>
                  {st && st !== "busy" && st !== "done" && <div className="text-[10px] text-warn break-words">{st}</div>}
                </div>
              </div>
            );
          })}
        </div>
        {!busy && !rows.length && key !== false && <div className="text-muted text-xs">Nothing matches.</div>}
      </div>
    </>
  );
}
