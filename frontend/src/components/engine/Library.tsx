import { useCallback, useEffect, useMemo, useState } from "react";
import { Camera, Music, Box, Code2, ExternalLink, FolderOpen, Image as ImageIcon, Loader2, RefreshCw, Search, Table2, X } from "lucide-react";
import { api } from "../../api/client";
import type { EngineProject, LibraryAsset } from "../../types";
import { cls } from "../ui";
import { EngineChip } from "./EngineChip";
import ModelViewer from "../ModelViewer";
import { SUBJECT_ORDER } from "./HistoryStrip";

// What a game already HAS — its dinos, its brainrots, its hammers, its GLBs, its textures — on
// the same shelves the history uses. The history is what the forge made; this is what is in the
// project, whether or not the forge ever drew it. Four kinds: a builder in code, one entry of a
// spec table (where a game keeps its fifteen species), a model file, an image.

const TYPE_LABEL: Record<string, string> = { spec: "Specs", code: "Builders", model: "Models",
  texture: "Textures", audio: "Audio", image: "Images" };
const TYPE_HINT: Record<string, string> = {
  spec: "One entry of a spec table in the code — a species, a hammer, an enemy. The builder next to it turns the entry into a thing.",
  code: "An exported function or class that builds something visual.",
  model: "A model file: glb, gltf, fbx, obj, stl, ply.",
  image: "An image or texture, outside screenshot folders.",
};
const fmtSize = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n >= 1e3 ? `${Math.round(n / 1e3)} kB` : `${n} B`;

function Chip({ on, label, count, onClick, title }: { on: boolean; label: string; count?: number; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title}
      className={cls("px-2 py-0.5 rounded-full border transition-colors whitespace-nowrap text-[11px]",
        on ? "border-brand/50 bg-brand/10 text-brand" : "border-transparent text-muted hover:text-text")}>
      {label}{count != null && <span className="font-mono opacity-70"> {count}</span>}
    </button>
  );
}

function Icon({ a, size = 14 }: { a: LibraryAsset; size?: number }) {
  if (a.type === "image" || a.type === "texture") return <ImageIcon size={size} />;
  if (a.type === "audio") return <Music size={size} />;
  if (a.type === "model") return <Box size={size} />;
  if (a.type === "spec") return <Table2 size={size} />;
  return <Code2 size={size} />;
}

interface Props {
  project: EngineProject | null;
  /** Which GAME inside the workspace to show — `rot-rush`, or "" for all of them. */
  game?: string;
  /** Open a source file in the Edit tab. */
  onOpenFile: (path: string) => void;
  /** Build THIS entry and look at it, rather than opening the file that declares it. */
  onOpenAsset?: (asset: LibraryAsset) => void;
}

/** A project-relative path made absolute, with the slashes the file API expects. */
function joinUnder(root: string, rel: string): string {
  const r = String(root || "").replace(/[\\/]+$/, "");
  return r + "/" + String(rel || "").replace(/^[\\/]+/, "");
}

/**
 * Can the Edit tab open this row itself?
 *
 * A model can: the Studio serves it decompressed and the editor loads it with a loader bound to
 * its own three. It was left out of this test, so a .glb went to `onOpenFile` instead — which
 * asked for the TEXT of a binary file, got none, and opened the editor on an empty string.
 */
function openable(a: LibraryAsset): boolean {
  return a.type === "code" || a.type === "spec" || a.type === "model" || !!a.model;
}

export function Library({ project, game = "", onOpenFile, onOpenAsset }: Props) {
  const [items, setItems] = useState<LibraryAsset[]>([]);
  const [byType, setByType] = useState<Record<string, number>>({});
  const [bySubject, setBySubject] = useState<Record<string, number>>({});
  const [type, setType] = useState("");
  const [subject, setSubject] = useState("");
  const [query, setQuery] = useState("");
  const [dq, setDq] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [meta, setMeta] = useState<{ files: number; truncated: boolean; totals: Record<string, number>; capped: Record<string, number> }>(
    { files: 0, truncated: false, totals: {}, capped: {} });
  // How many pictures exist beyond the per-folder cap. Shown, not hidden: "60" printed over a
  // folder of 800 is exactly the impression that the window cannot see a game's assets.
  const hidden = Object.values(meta.capped).reduce((a, b) => a + b, 0);
  // A PICTURE OF EACH ONE, made by the game's own builders.
  //
  // A shelf of thirty creatures that all show the same table icon is a list, not a library. So
  // the Studio renders them: it opens the project's page once, calls whatever in that file draws
  // the entry, and keeps the frame. Rendering is opt-in per shelf and batched, because it costs a
  // browser and a dev server — about three seconds for six, and nothing at all the second time.
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [thumbErr, setThumbErr] = useState<Record<string, string>>({});
  const [rendering, setRendering] = useState(false);
  const [renderNote, setRenderNote] = useState("");
  useEffect(() => { setThumbs({}); setThumbErr({}); setRenderNote(""); }, [project?.path]);

  // Cheapest first, so the shelf fills while the slow half is still going: an image IS its
  // picture and needs no browser at all, a model is one load, and a builder is a page visit.
  const COST: Record<string, number> = { image: 0, texture: 0, model: 1, spec: 2, code: 2 };

  const renderThumbs = async () => {
    if (!project?.path || rendering) return;
    // ONE CLICK DOES THE WHOLE SHELF, OF EVERY KIND. It used to ask for forty of the two kinds
    // it knew about; on a workspace of 816 that left 616 images and models never offered to the
    // renderer at all, and made the other 200 twenty clicks with no sign of how far it had got.
    const done: Record<string, string> = { ...thumbs };
    const bad: Record<string, string> = { ...thumbErr };
    const left = () => items.filter((a) => !done[a.id] && !bad[a.id])
      .sort((x, y) => (COST[x.type] ?? 3) - (COST[y.type] ?? 3));
    if (!left().length) { setRenderNote("everything on this shelf has a picture"); return; }
    setRendering(true);
    try {
      for (let round = 0; round < 60; round++) {
        const want = left().slice(0, 60);
        if (!want.length) break;
        setRenderNote(`${Object.keys(done).length} of ${items.length}…`);
        const r = await api.engineThumbs(project.path, want.map((a) => a.id));
        if (!r.ok) { setRenderNote(r.error || "could not render"); break; }
        Object.assign(done, r.thumbs || {});
        Object.assign(bad, r.errors || {});
        // Anything the server answered nothing about is not asked again in this pass, or the
        // loop never ends.
        for (const a of want) if (!done[a.id] && !bad[a.id]) bad[a.id] = "no answer";
        setThumbs({ ...done });
        setThumbErr({ ...bad });
      }
      const missed = items.filter((a) => bad[a.id]).length;
      setRenderNote(`${items.filter((a) => done[a.id]).length} of ${items.length} have a picture`
        + (missed ? ` · ${missed} could not be drawn` : ""));
    } catch (e: any) {
      setRenderNote(e?.message || "could not render");
    } finally {
      setRendering(false);
    }
  };

  const [sel, setSel] = useState<LibraryAsset | null>(null);
  useEffect(() => { const t = window.setTimeout(() => setDq(query), 250); return () => window.clearTimeout(t); }, [query]);

  const load = useCallback(async (fresh = false) => {
    if (!project) { setItems([]); setByType({}); setBySubject({}); return; }
    setLoading(true);
    setErr("");
    try {
      const r = await api.engineAssets(project.path, { type, subject, q: dq, fresh, root: game });
      if (!r.ok) throw new Error(r.error || "could not scan the project");
      setItems(r.items || []);
      setByType(r.by_type || {});
      setBySubject(r.by_subject || {});
      setMeta({ files: r.files_seen || 0, truncated: !!r.truncated,
                totals: (r as any).totals || {}, capped: (r as any).capped || {} });
    } catch (e: any) {
      setErr(/\b404\b/.test(String(e?.message)) ? "The backend is older than the Library; restart it to scan projects." : String(e?.message || e));
    } finally { setLoading(false); }
  }, [project, type, subject, dq, game]);
  useEffect(() => { load(); }, [load]);
  // A different project: the selection and the search belong to the old one.
  useEffect(() => { setSel(null); setQuery(""); setType(""); setSubject(""); }, [project?.path]);

  const subjects = useMemo(() => {
    const s = SUBJECT_ORDER.filter((k) => (bySubject[k] || 0) > 0 || k === subject);
    for (const k of Object.keys(bySubject)) if (!s.includes(k) && bySubject[k] > 0) s.push(k);
    return s;
  }, [bySubject, subject]);
  const all = Object.values(byType).reduce((a, b) => a + b, 0);
  const groups = useMemo(() => {
    // Shelved by folder, then by table: "src/economy.js · SPECIES" reads as the drawer it is.
    const out: { key: string; label: string; items: LibraryAsset[] }[] = [];
    const idx: Record<string, number> = {};
    for (const a of items) {
      const key = a.type === "spec" ? `${a.file} · ${a.table}` : a.type === "code" ? a.file : a.file.split("/").slice(0, -1).join("/") || "(root)";
      // The file path already begins with the game folder, so the shelf reads as the drawer it is.
      if (idx[key] === undefined) { idx[key] = out.length; out.push({ key, label: key, items: [] }); }
      out[idx[key]].items.push(a);
    }
    return out;
  }, [items]);

  if (!project) {
    return (
      <div className="h-full flex items-center justify-center text-center text-sm text-muted px-8">
        <div>
          <div className="text-text">Pick a game in the rail.</div>
          <div className="mt-1 max-w-md">The Library lists what that project already has: the species and props in its code,
            its model files and its images, on the same shelves as the history.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex min-w-0">
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="shrink-0 border-b border-line">
          <div className="h-9 flex items-center gap-2 px-3">
            <span className="text-[10px] uppercase tracking-wide text-muted">Library</span>
            <span className="text-xs text-brand truncate max-w-[14rem]" title={project.path}>{project.name}</span>
            <div className="flex items-center gap-0.5 ml-1">
              <Chip on={type === ""} label="all" count={all} onClick={() => setType("")} />
              {(["spec", "code", "model", "texture", "audio", "image"] as const).map((t) => ((byType[t] || 0) > 0 || type === t) && (
                <Chip key={t} on={type === t} label={TYPE_LABEL[t]} count={byType[t] || 0} title={TYPE_HINT[t]}
                  onClick={() => setType(type === t ? "" : t)} />
              ))}
            </div>
            <label className="ml-2 flex items-center gap-1 text-muted text-[11px]">
              <Search size={11} />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="find by name, table, file…"
                className="bg-transparent outline-none border-b border-transparent focus:border-line text-text placeholder:text-muted/60 w-48" />
              {query && <button onClick={() => setQuery("")} className="hover:text-text" title="Clear"><X size={10} /></button>}
            </label>
            <button onClick={renderThumbs} disabled={rendering}
              title="Give every asset on this shelf a picture: an image is its own, a model is loaded by the game's engine, and a builder or a table is called in the project's own code. Cached after the first time."
              className={cls("chip flex items-center gap-1", rendering ? "opacity-60" : "hover:text-text")}>
              {rendering ? <Loader2 size={11} className="animate-spin" /> : <Camera size={11} />}
              {rendering ? "rendering" : "render previews"}
            </button>
            {renderNote && <span className="text-[10px] text-muted max-w-[16rem] truncate" title={renderNote}>{renderNote}</span>}
            <span className="ml-auto text-[11px] text-muted font-mono"
              title={[`${meta.files} files looked at`,
                      Object.entries(meta.totals).map(([k, v]) => `${v} ${k}`).join(", "),
                      Object.keys(meta.capped).length
                        ? "listed 60 per folder, so these folders show only part of themselves:\n  "
                          + Object.entries(meta.capped).map(([k, v]) => `${k} (+${v} more)`).join("\n  ")
                        : "",
                      meta.truncated ? "stopped early: a very large project" : ""].filter(Boolean).join("\n")}>
              {loading ? <Loader2 size={11} className="animate-spin inline" />
                : `${items.length}${hidden > 0 ? ` of ${items.length + hidden}` : ""}${meta.truncated ? "+" : ""}`}
            </span>
            <button onClick={() => load(true)} className="text-muted hover:text-text" title="Scan the project again"><RefreshCw size={12} /></button>
          </div>
          {subjects.length > 0 && (
            <div className="h-7 flex items-center gap-0.5 px-3 overflow-x-auto">
              <span className="text-[10px] uppercase tracking-wide text-muted mr-1">Shows</span>
              <Chip on={subject === ""} label="anything" onClick={() => setSubject("")} />
              {subjects.map((s) => <Chip key={s} on={subject === s} label={s} count={bySubject[s] || 0} onClick={() => setSubject(subject === s ? "" : s)} />)}
            </div>
          )}
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
          {err && <div className="text-xs text-danger">{err}</div>}
          {!err && !loading && !items.length && (
            <div className="text-xs text-muted">
              {all ? "Nothing on this shelf. Clear a chip or the search." : "Nothing found in this project: no builder functions, spec tables, model files or images outside build and screenshot folders."}
            </div>
          )}
          {groups.map((g) => (
            <div key={g.key}>
              <div className="text-[10px] text-muted mb-1.5 flex items-center gap-1.5">
                <span className="font-mono truncate">{g.label}</span>
                <span className="opacity-70">{g.items.length}</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {g.items.map((a) => {
                  const active = sel?.id === a.id;
                  return (
                    <button key={a.id} onClick={() => setSel(a)} onDoubleClick={() => (onOpenAsset && openable(a) ? onOpenAsset(a) : onOpenFile(a.path))}
                      title={`${a.name}\n${a.file}${a.line ? ":" + a.line : ""}\n${a.subject}`}
                      className={cls("w-36 text-left rounded-lg border bg-panel2 overflow-hidden transition-colors",
                        active ? "border-brand ring-1 ring-brand/50" : "border-line hover:border-muted/60")}>
                      <div className="h-20 bg-black/40 flex items-center justify-center text-muted overflow-hidden">
                        {thumbs[a.id]
                          ? <img src={api.engineSheetUrl(thumbs[a.id])} loading="lazy" decoding="async" alt=""
                              className="w-full h-full object-contain" />
                          : a.model || a.type === "model"
                          ? <ModelViewer src={api.engineModelUrl(project.path, a.model || a.file)}
                              className="h-full w-full" />
                          : a.type === "image" || a.type === "texture"
                          ? <img src={api.wsRaw(a.path)} loading="lazy" decoding="async" alt="" className="w-full h-full object-contain" />
                          : <Icon a={a} size={22} />}
                      </div>
                      <div className="px-1.5 py-1">
                        <div className="truncate text-[11px] text-text">{a.name}</div>
                        <div className="flex items-center gap-1 text-[10px] text-muted mt-0.5 min-h-[1rem]">
                          {a.subject !== "other" && <span className="truncate rounded-full bg-panel px-1.5 text-[9px] leading-4 text-text/80">{a.subject}</span>}
                          {a.engine && <EngineChip kind={a.engine} size="xs" />}
                          <span className="ml-auto font-mono opacity-70">{a.type === "spec" ? a.key || `#${a.index + 1}` : a.type === "code" ? a.tags[0] : fmtSize(a.size)}</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {sel && (
        <aside className="w-[22rem] shrink-0 border-l border-line bg-panel overflow-y-auto p-3 text-xs space-y-3">
          <div className="flex items-start gap-2">
            <span className="text-muted mt-0.5"><Icon a={sel} /></span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold text-text break-words">{sel.name}</div>
              <div className="text-muted mt-0.5 flex items-center gap-1.5 flex-wrap">
                <span className="chip !py-0 text-[10px]">{TYPE_LABEL[sel.type] || sel.type}</span>
                {sel.subject !== "other" && <span className="chip !py-0 text-[10px]">{sel.subject}</span>}
                {sel.engine && <EngineChip kind={sel.engine} size="xs" />}
              </div>
            </div>
            <button onClick={() => setSel(null)} className="text-muted hover:text-text" title="Close"><X size={12} /></button>
          </div>

          {(sel.type === "spec" && sel.model) && (
            <div className="h-48 rounded-lg overflow-hidden border border-line bg-black/40">
              <ModelViewer src={api.engineModelUrl(project.path, sel.model)} className="h-full w-full" />
            </div>
          )}
          {(sel.type === "image" || sel.type === "texture") && (
            <a href={api.wsRaw(sel.path)} target="_blank" rel="noopener" className="block rounded-lg border border-line bg-black/40 overflow-hidden" title="Open at full size">
              <img src={api.wsRaw(sel.path)} alt="" className="w-full max-h-64 object-contain" />
            </a>
          )}
          {sel.type === "model" && (
            <div className="rounded-lg border border-line bg-black/40 overflow-hidden h-56">
              <ModelViewer src={sel.model ? api.engineModelUrl(project.path, sel.model)
                : /\.(glb|gltf)$/i.test(sel.file) ? api.engineModelUrl(project.path, sel.file)
                : api.wsModel(sel.path)} className="h-full w-full" />
            </div>
          )}

          <div className="space-y-1">
            <div className="flex justify-between gap-2"><span className="text-muted">file</span><span className="font-mono text-right break-all">{sel.file}{sel.line ? `:${sel.line}` : ""}</span></div>
            {sel.table && <div className="flex justify-between gap-2"><span className="text-muted">table</span><span className="font-mono">{sel.table}{sel.key ? ` · ${sel.key}` : ` · #${sel.index + 1}`}</span></div>}
            {sel.export && !sel.table && <div className="flex justify-between gap-2"><span className="text-muted">export</span><span className="font-mono">{sel.export}</span></div>}
            {sel.size > 0 && (sel.type === "image" || sel.type === "model") && <div className="flex justify-between gap-2"><span className="text-muted">size</span><span className="font-mono">{fmtSize(sel.size)}</span></div>}
          </div>

          <div className="flex flex-wrap gap-2">
            {openable(sel) && (
              <>
                {onOpenAsset && (
                  <button onClick={() => onOpenAsset(sel)} className="btn btn-sm inline-flex items-center gap-1.5"
                    title={sel.type === "model"
                      ? "Open it in the Edit tab: orbit it, move it, and put things beside it"
                      : "Build THIS one in the Edit tab and look at it — not the file that declares it"}>
                    <Box size={12} /> {sel.type === "model" ? "Open it" : "Build it"}
                  </button>
                )}
                {sel.type !== "model" && (
                  <button onClick={() => onOpenFile(sel.path)} className="btn btn-sm inline-flex items-center gap-1.5" title="Open the file it is declared in">
                    <ExternalLink size={12} /> Open the file
                  </button>
                )}
              </>
            )}
            <button onClick={() => api.wsReveal(sel.path).catch(() => {})} className="btn btn-sm inline-flex items-center gap-1.5" title="Show the file in its folder">
              <FolderOpen size={12} /> Reveal
            </button>
          </div>
          {sel.type === "spec" && (
            <div className="text-[11px] text-muted leading-relaxed">
              One entry of <span className="font-mono">{sel.table}</span>. Open in Edit shows the file; the builder beside the table
              (a <span className="font-mono">build…</span> function) is what turns this entry into a thing.
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
