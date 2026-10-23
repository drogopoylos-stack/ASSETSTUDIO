import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Box,
  Download,
  FolderOpen,
  Library,
  RotateCw,
  Save,
  Search,
  ShieldCheck,
  ShieldAlert,
  ChevronDown,
  Tag,
  Trash2,
  X,
} from "lucide-react";
import { api, assetFileUrl, assetPreviewUrl } from "../api/client";
import { useStore } from "../store/useStore";
import type { Asset } from "../types";
import { STAGES } from "../types";
import ModelViewer from "../components/ModelViewer";
import {
  cls,
  Empty,
  fmtCost,
  humanBytes,
  Section,
  Spinner,
  timeAgo,
} from "../components/ui";

type CommercialChoice = "yes" | "no" | "unknown";

interface Summary {
  total_assets: number;
  total_cost: number;
  non_commercial_assets: number;
}

// A library shelf (backend library.py) and how many assets sit on it.
interface Shelf {
  id: string;
  label: string;
  count: number;
}

// The Catalog opens on the newest few; "Load more" pages back through the rest.
const PAGE = 24;

const TYPES: { id: string; label: string }[] = [
  { id: "model", label: "3D models" },
  { id: "image", label: "Images" },
  { id: "texture", label: "Textures" },
  { id: "video", label: "Video" },
  { id: "animation", label: "Animations" },
  { id: "render", label: "Renders" },
  { id: "atlas", label: "Atlases" },
  { id: "other", label: "Other" },
];

interface EditState {
  name: string;
  category: string;
  tags: string;
  target_game: string;
  license: string;
  commercial_ok: CommercialChoice;
}

function commercialToChoice(v?: boolean | null): CommercialChoice {
  if (v === true) return "yes";
  if (v === false) return "no";
  return "unknown";
}

function choiceToCommercial(c: CommercialChoice): boolean | null {
  if (c === "yes") return true;
  if (c === "no") return false;
  return null;
}

function editFrom(a: Asset): EditState {
  return {
    name: a.name,
    category: a.category || "",
    tags: (a.tags || []).join(", "),
    target_game: a.target_game || "",
    license: a.license || "",
    commercial_ok: commercialToChoice(a.commercial_ok),
  };
}

export default function Catalog() {
  const toast = useStore((s) => s.toast);
  const recentLen = useStore((s) => s.recentAssets.length);

  // filters
  const [stage, setStage] = useState<string>("all");
  const [category, setCategory] = useState<string>("all"); // "all" | a shelf id | "unsorted"
  const [type, setType] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [targetGame, setTargetGame] = useState("");
  const [tag, setTag] = useState("");
  const [commercialSafeOnly, setCommercialSafeOnly] = useState(false);

  // data
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [shelves, setShelves] = useState<Shelf[]>([]);
  const [shelfTotal, setShelfTotal] = useState(0);

  // selection / drawer
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [edit, setEdit] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);
  const [qaBusy, setQaBusy] = useState(false);

  // Debounce the search input → committed `search`.
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  async function loadSummary() {
    try {
      const s = await api.catalogSummary();
      setSummary({
        total_assets: s?.total_assets ?? 0,
        total_cost: s?.total_cost ?? 0,
        non_commercial_assets: s?.non_commercial_assets ?? 0,
      });
    } catch (e: any) {
      // Non-fatal: the grid still works without the summary banner.
    }
  }

  async function loadShelves() {
    try {
      const r = await api.catalogCategories(type === "all" ? "" : type);
      setShelves(r.categories);
      setShelfTotal(r.total);
    } catch (e: any) {
      // Non-fatal: the grid still works without the shelf chips.
    }
  }

  function query() {
    return {
      stage: stage === "all" ? "" : stage,
      category: category === "all" ? "" : category,
      type: type === "all" ? "" : type,
      target_game: targetGame.trim(),
      tag: tag.trim(),
      search,
    };
  }

  // `keep` reloads as many as are on screen, so a new asset arriving does not drop the pages
  // already loaded with "Load more".
  async function loadAssets(keep = PAGE) {
    setLoading(true);
    const limit = Math.max(PAGE, keep);
    try {
      const list = await api.assets({ ...query(), limit, offset: 0 });
      setAssets(list);
      setHasMore(list.length === limit);
    } catch (e: any) {
      useStore.getState().toast(`Failed to load catalog: ${e.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    setLoadingMore(true);
    try {
      const list = await api.assets({ ...query(), limit: PAGE, offset: assets.length });
      setAssets((prev) => [...prev, ...list.filter((a) => !prev.some((p) => p.id === a.id))]);
      setHasMore(list.length === PAGE);
    } catch (e: any) {
      toast(`Failed to load more: ${e.message}`, "danger");
    } finally {
      setLoadingMore(false);
    }
  }

  // A filter change starts again from the newest page.
  useEffect(() => {
    loadAssets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, category, type, search, targetGame, tag]);

  // A new asset streamed in: refresh what is on screen, keeping its length.
  const shown = useRef(0);
  shown.current = assets.length;
  useEffect(() => {
    if (recentLen) loadAssets(shown.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentLen]);

  useEffect(() => {
    loadSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentLen]);

  useEffect(() => {
    loadShelves();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, recentLen]);

  const shelfLabel = useMemo(() => {
    const m: Record<string, string> = {};
    shelves.forEach((s) => (m[s.id] = s.label));
    return (id?: string) => (id ? m[id] || id : "");
  }, [shelves]);

  async function revealFolder(body: { asset_id?: string; category?: string }) {
    try {
      await api.revealLibrary(body);
    } catch (e: any) {
      toast(`Could not open the folder: ${e.message}`, "danger");
    }
  }

  // Client-side commercial-safe filter (hides commercial_ok === false).
  const visible = useMemo(
    () =>
      commercialSafeOnly
        ? assets.filter((a) => a.commercial_ok !== false)
        : assets,
    [assets, commercialSafeOnly]
  );

  const selected = useMemo(
    () => visible.find((a) => a.id === selectedId) ?? assets.find((a) => a.id === selectedId) ?? null,
    [visible, assets, selectedId]
  );

  // Keep the edit form in sync with whichever asset is selected.
  useEffect(() => {
    setEdit(selected ? editFrom(selected) : null);
  }, [selected]);

  function openAsset(a: Asset) {
    setSelectedId(a.id);
  }

  function closeDrawer() {
    setSelectedId(null);
  }

  async function saveEdit() {
    if (!selected || !edit) return;
    setSaving(true);
    try {
      const patch = {
        name: edit.name.trim() || selected.name,
        category: edit.category,
        tags: edit.tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        target_game: edit.target_game.trim(),
        license: edit.license.trim(),
        commercial_ok: choiceToCommercial(edit.commercial_ok),
      };
      const updated = await api.patchAsset(selected.id, patch);
      setAssets((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      toast("Asset saved", "ok");
      loadSummary();
      loadShelves();
    } catch (e: any) {
      toast(`Save failed: ${e.message}`, "danger");
    } finally {
      setSaving(false);
    }
  }

  async function deleteAsset(a: Asset) {
    if (!window.confirm(`Delete "${a.name}" and its file from disk? This cannot be undone.`))
      return;
    try {
      await api.deleteAsset(a.id, true);
      setAssets((prev) => prev.filter((x) => x.id !== a.id));
      if (selectedId === a.id) setSelectedId(null);
      toast("Asset deleted", "ok");
      loadSummary();
      loadShelves();
    } catch (e: any) {
      toast(`Delete failed: ${e.message}`, "danger");
    }
  }

  async function runTurntable(a: Asset) {
    setQaBusy(true);
    try {
      const job = await api.submitJob({
        stage: "qa",
        provider_id: "turntable",
        inputs: [a.id],
        label: `QA turntable · ${a.name}`,
      });
      useStore.getState().upsertJob(job);
      toast("QA turntable job queued", "ok");
    } catch (e: any) {
      toast(`QA submit failed: ${e.message}`, "danger");
    } finally {
      setQaBusy(false);
    }
  }

  const dirty = useMemo(() => {
    if (!selected || !edit) return false;
    const base = editFrom(selected);
    return (
      base.name !== edit.name ||
      base.category !== edit.category ||
      base.tags !== edit.tags ||
      base.target_game !== edit.target_game ||
      base.license !== edit.license ||
      base.commercial_ok !== edit.commercial_ok
    );
  }, [selected, edit]);

  const activeFilters =
    (stage !== "all" ? 1 : 0) +
    (category !== "all" ? 1 : 0) +
    (type !== "all" ? 1 : 0) +
    (search ? 1 : 0) +
    (targetGame.trim() ? 1 : 0) +
    (tag.trim() ? 1 : 0) +
    (commercialSafeOnly ? 1 : 0);

  function clearFilters() {
    setStage("all");
    setCategory("all");
    setType("all");
    setSearchInput("");
    setSearch("");
    setTargetGame("");
    setTag("");
    setCommercialSafeOnly(false);
  }

  return (
    <div className="space-y-4">
      {/* Header / summary */}
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Library size={18} className="text-brand" /> Asset Catalog
          </h2>
          <p className="text-sm text-muted mt-0.5">
            Your full asset library — review licensing before shipping a monetized game.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="card px-4 py-2.5 flex items-center gap-5">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted">Assets</div>
              <div className="text-base font-semibold tabular-nums">
                {summary ? summary.total_assets.toLocaleString() : "—"}
              </div>
            </div>
            <div className="border-l border-line pl-5">
              <div className="text-[11px] uppercase tracking-wide text-muted">Total cost</div>
              <div className="text-base font-semibold tabular-nums">
                {summary ? fmtCost(summary.total_cost) : "—"}
              </div>
            </div>
            <div className="border-l border-line pl-5">
              <div className="text-[11px] uppercase tracking-wide text-muted">Non-commercial</div>
              <div
                className={cls(
                  "text-base font-semibold tabular-nums",
                  summary && summary.non_commercial_assets > 0 ? "text-warn" : "text-muted"
                )}
              >
                {summary ? summary.non_commercial_assets.toLocaleString() : "—"}
              </div>
            </div>
          </div>
          <button
            className="btn-ghost"
            onClick={() => revealFolder({ category: category === "all" ? "" : category })}
            title="Open the model library folder (data/library/models) in Explorer"
          >
            <FolderOpen size={15} /> Open folder
          </button>
          <button
            className="btn-ghost"
            onClick={() => {
              loadAssets(assets.length);
              loadSummary();
              loadShelves();
            }}
            title="Refresh"
          >
            <RotateCw size={15} /> Refresh
          </button>
        </div>
      </div>

      {summary && summary.non_commercial_assets > 0 && (
        <div className="card p-3 flex items-center gap-2 text-sm border-warn/40">
          <ShieldAlert size={16} className="text-warn shrink-0" />
          <span className="text-warn">
            {summary.non_commercial_assets} asset
            {summary.non_commercial_assets === 1 ? " is" : "s are"} flagged non-commercial.
          </span>
          <span className="text-muted">
            Do not ship these in a monetized game without clearing their license.
          </span>
          {!commercialSafeOnly && (
            <button
              className="btn-ghost text-xs ml-auto"
              onClick={() => setCommercialSafeOnly(true)}
            >
              Hide them
            </button>
          )}
        </div>
      )}

      {/* Library shelves */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          className={cls("chip cursor-pointer", category === "all" ? "bg-brand/20 text-brand border-brand" : "hover:border-brand")}
          onClick={() => setCategory("all")}
        >
          All <span className="text-muted tabular-nums">{shelfTotal}</span>
        </button>
        {shelves
          .filter((s) => s.count > 0 || s.id !== "unsorted")
          .map((s) => (
            <button
              key={s.id}
              className={cls(
                "chip cursor-pointer",
                category === s.id ? "bg-brand/20 text-brand border-brand" : "hover:border-brand",
                s.count === 0 && category !== s.id && "opacity-50"
              )}
              onClick={() => setCategory(category === s.id ? "all" : s.id)}
              title={`${s.label}: ${s.count}`}
            >
              {s.label} <span className="text-muted tabular-nums">{s.count}</span>
            </button>
          ))}
      </div>

      {/* Filters */}
      <Section
        title="Filters"
        desc="Narrow the library by type, stage, search, game, or tag."
        right={
          activeFilters > 0 ? (
            <button className="btn-ghost text-xs" onClick={clearFilters}>
              <X size={13} /> Clear ({activeFilters})
            </button>
          ) : undefined
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3">
          <div>
            <label className="label">Type</label>
            <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="all">All types</option>
              {TYPES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label">Stage</label>
            <select className="input" value={stage} onChange={(e) => setStage(e.target.value)}>
              <option value="all">All stages</option>
              {STAGES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label">Search</label>
            <div className="relative">
              <Search
                size={14}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
              />
              <input
                className="input pl-8"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setSearch(searchInput.trim());
                }}
                placeholder="name or prompt…"
              />
            </div>
          </div>

          <div>
            <label className="label">Target game</label>
            <input
              className="input"
              value={targetGame}
              onChange={(e) => setTargetGame(e.target.value)}
              placeholder="e.g. Coin Dash"
            />
          </div>

          <div>
            <label className="label">Tag</label>
            <div className="relative">
              <Tag
                size={13}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
              />
              <input
                className="input pl-8"
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="e.g. enemy"
              />
            </div>
          </div>
        </div>

        <div className="mt-3 flex items-center justify-between flex-wrap gap-2">
          <label className="inline-flex items-center gap-2 text-sm cursor-pointer select-none">
            <input
              type="checkbox"
              className="accent-brand-600 h-4 w-4"
              checked={commercialSafeOnly}
              onChange={(e) => setCommercialSafeOnly(e.target.checked)}
            />
            <ShieldCheck size={15} className="text-ok" />
            Commercial-safe only
            <span className="text-xs text-muted">(hide non-commercial assets)</span>
          </label>
          <span className="text-xs text-muted">
            {loading ? (
              <span className="inline-flex items-center gap-2">
                <Spinner size={12} /> loading…
              </span>
            ) : (
              `${visible.length} shown, newest first`
            )}
          </span>
        </div>
      </Section>

      {/* Grid */}
      {loading && assets.length === 0 ? (
        <div className="flex justify-center py-16">
          <Spinner size={26} />
        </div>
      ) : visible.length === 0 ? (
        <Empty
          icon={<FolderOpen size={26} />}
          label={
            assets.length === 0
              ? "No assets match these filters."
              : "All matching assets are non-commercial and hidden."
          }
        />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3">
          {visible.map((a) => (
            <AssetGridCard
              key={a.id}
              asset={a}
              selected={a.id === selectedId}
              onClick={openAsset}
              onDelete={deleteAsset}
              shelf={shelfLabel(a.category)}
            />
          ))}
        </div>
      )}

      {hasMore && visible.length > 0 && (
        <div className="flex justify-center">
          <button className="btn" disabled={loadingMore} onClick={loadMore}>
            {loadingMore ? <Spinner size={15} /> : <ChevronDown size={15} />} Load more
          </button>
        </div>
      )}

      {/* Detail drawer */}
      {selected && edit && (
        <>
          <div
            className="fixed inset-0 bg-black/50 z-40"
            onClick={closeDrawer}
            aria-hidden
          />
          <aside className="fixed top-0 right-0 h-full w-full max-w-md bg-panel border-l border-line z-50 flex flex-col shadow-card">
            <div className="flex items-center justify-between px-4 py-3 border-b border-line">
              <div className="min-w-0">
                <h3 className="font-semibold truncate" title={selected.name}>
                  {selected.name}
                </h3>
                <div className="text-xs text-muted">
                  {selected.type} · {selected.stage}
                </div>
              </div>
              <button className="btn-ghost !px-2 !py-2" onClick={closeDrawer} title="Close">
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* Preview */}
              <div className="card overflow-hidden">
                <div className="aspect-square bg-panel2">
                {selected.type === "video" ? (
                  <video src={assetFileUrl(selected.id)} controls preload="metadata" className="w-full h-full object-contain" />
                ) : selected.type === "model" ? (
                    <ModelViewer assetId={selected.id} />
                  ) : (
                    <img
                      src={assetPreviewUrl(selected.id)}
                      className="w-full h-full object-contain"
                      style={{
                        backgroundImage:
                          "repeating-conic-gradient(#1a1e2b 0% 25%, #13161f 0% 50%)",
                        backgroundSize: "20px 20px",
                      }}
                    />
                  )}
                </div>
              </div>

              {/* Licensing flag */}
              {selected.commercial_ok === false && (
                <div className="card p-3 flex items-start gap-2 border-danger/40">
                  <ShieldAlert size={15} className="text-danger shrink-0 mt-0.5" />
                  <span className="text-sm text-danger">
                    Marked non-commercial — unsafe to ship in a monetized game.
                  </span>
                </div>
              )}

              {/* Read-only metadata */}
              <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                <Meta label="Provider" value={selected.provider_id || "—"} mono />
                <Meta label="Cost" value={fmtCost(selected.cost)} />
                <Meta label="Size" value={humanBytes(selected.size_bytes)} />
                <Meta
                  label={selected.type === "model" ? "Polys" : "Dimensions"}
                  value={
                    selected.type === "model"
                      ? selected.meta?.polys != null
                        ? `${Number(selected.meta.polys).toLocaleString()} tris`
                        : "—"
                      : selected.meta?.width
                        ? `${selected.meta.width}×${selected.meta.height}`
                        : "—"
                  }
                />
                <Meta label="Seed" value={selected.seed != null ? String(selected.seed) : "—"} mono />
                <Meta label="Created" value={timeAgo(selected.created_at)} />
              </div>

              <Field label="Prompt">
                <p className="text-sm text-text whitespace-pre-wrap break-words bg-panel2 border border-line rounded-lg px-3 py-2 max-h-32 overflow-y-auto">
                  {selected.prompt || <span className="text-muted">— none —</span>}
                </p>
              </Field>

              <Field label="Path">
                <p
                  className="text-xs font-mono text-muted break-all bg-panel2 border border-line rounded-lg px-3 py-2"
                  title={selected.path}
                >
                  {selected.path}
                </p>
              </Field>

              {/* Editable fields */}
              <div className="border-t border-line pt-4 space-y-3">
                <div>
                  <label className="label">Name</label>
                  <input
                    className="input"
                    value={edit.name}
                    onChange={(e) => setEdit({ ...edit, name: e.target.value })}
                  />
                </div>
                <div>
                  <label className="label">Category</label>
                  <select
                    className="input"
                    value={edit.category}
                    onChange={(e) => setEdit({ ...edit, category: e.target.value })}
                  >
                    <option value="">Unsorted</option>
                    {shelves
                      .filter((s) => s.id !== "unsorted")
                      .map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.label}
                        </option>
                      ))}
                  </select>
                  {selected.type === "model" && (
                    <p className="text-[11px] text-muted mt-1">
                      Saving moves the model file to this category's folder.
                    </p>
                  )}
                </div>
                <div>
                  <label className="label">Tags (comma-separated)</label>
                  <input
                    className="input"
                    value={edit.tags}
                    onChange={(e) => setEdit({ ...edit, tags: e.target.value })}
                    placeholder="enemy, sprite, blue"
                  />
                </div>
                <div>
                  <label className="label">Target game</label>
                  <input
                    className="input"
                    value={edit.target_game}
                    onChange={(e) => setEdit({ ...edit, target_game: e.target.value })}
                    placeholder="e.g. Coin Dash"
                  />
                </div>
                <div>
                  <label className="label">License</label>
                  <input
                    className="input"
                    value={edit.license}
                    onChange={(e) => setEdit({ ...edit, license: e.target.value })}
                    placeholder="e.g. CC0, custom, vendor terms…"
                  />
                </div>
                <div>
                  <label className="label">Commercial use</label>
                  <select
                    className="input"
                    value={edit.commercial_ok}
                    onChange={(e) =>
                      setEdit({ ...edit, commercial_ok: e.target.value as CommercialChoice })
                    }
                  >
                    <option value="yes">Yes — safe to monetize</option>
                    <option value="no">No — non-commercial only</option>
                    <option value="unknown">Unknown</option>
                  </select>
                </div>
              </div>
            </div>

            {/* Drawer actions */}
            <div className="border-t border-line p-3 space-y-2">
              <div className="flex gap-2">
                <button
                  className="btn-primary flex-1 justify-center"
                  disabled={saving || !dirty}
                  onClick={saveEdit}
                >
                  {saving ? <Spinner size={15} /> : <Save size={15} />}
                  {dirty ? "Save changes" : "Saved"}
                </button>
                <a className="btn" href={assetFileUrl(selected.id)} download title="Download original">
                  <Download size={15} /> Download
                </a>
                <button
                  className="btn !px-2.5"
                  onClick={() => revealFolder({ asset_id: selected.id })}
                  title="Show the file in Explorer"
                >
                  <FolderOpen size={15} />
                </button>
              </div>
              <div className="flex gap-2">
                {selected.type === "model" && (
                  <button
                    className="btn flex-1 justify-center"
                    disabled={qaBusy}
                    onClick={() => runTurntable(selected)}
                    title="Render a QA turntable for this model"
                  >
                    {qaBusy ? <Spinner size={15} /> : <Box size={15} />} QA turntable
                  </button>
                )}
                <button
                  className="btn text-danger flex-1 justify-center"
                  onClick={() => deleteAsset(selected)}
                >
                  <Trash2 size={15} /> Delete
                </button>
              </div>
            </div>
          </aside>
        </>
      )}
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={cls("truncate", mono && "font-mono text-xs")} title={value}>
        {value}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="label">{label}</label>
      {children}
    </div>
  );
}

// Small wrapper around the visual card: opens the drawer on click and exposes a
// quick delete. Mirrors AssetCard's look but tuned for the catalog grid.
function AssetGridCard({
  asset,
  selected,
  onClick,
  onDelete,
  shelf,
}: {
  asset: Asset;
  selected: boolean;
  onClick: (a: Asset) => void;
  onDelete: (a: Asset) => void;
  shelf?: string;
}) {
  const isModel = asset.type === "model";
  const polys = asset.meta?.polys ?? asset.meta?.vertices;

  return (
    <div
      className={cls(
        "card overflow-hidden group relative transition-all cursor-pointer hover:border-brand",
        selected && "ring-2 ring-brand border-brand"
      )}
      onClick={() => onClick(asset)}
    >
      <div className="aspect-square bg-panel2 relative">
        {asset.type === "video" ? (
          <video src={assetFileUrl(asset.id)} controls preload="metadata" className="w-full h-full object-contain" onClick={(event) => event.stopPropagation()} />
        ) : isModel && asset.preview_path ? (
          <img
            src={`/api/file?path=${encodeURIComponent(asset.preview_path)}`}
            className="w-full h-full object-contain"
            loading="lazy"
          />
        ) : isModel ? (
          <div className="w-full h-full flex items-center justify-center text-muted">
            <Box size={28} />
          </div>
        ) : (
          <img
            src={assetPreviewUrl(asset.id)}
            className="w-full h-full object-contain"
            loading="lazy"
            style={{
              backgroundImage: "repeating-conic-gradient(#1a1e2b 0% 25%, #13161f 0% 50%)",
              backgroundSize: "20px 20px",
            }}
          />
        )}
        <span className="absolute top-2 left-2 chip bg-black/50 backdrop-blur">
          {asset.type}
        </span>
        {asset.commercial_ok === false && (
          <span className="absolute top-2 right-2 chip bg-black/60 text-danger">non-commercial</span>
        )}
      </div>

      <div className="p-2.5">
        <div className="text-sm font-medium truncate" title={asset.name}>
          {asset.name}
        </div>
        <div className="mt-1 flex items-center gap-2 flex-wrap text-[11px] text-muted">
          <span>{humanBytes(asset.size_bytes)}</span>
          {asset.meta?.width && (
            <span>
              {asset.meta.width}×{asset.meta.height}
            </span>
          )}
          {polys ? <span>{Number(polys).toLocaleString()} tris</span> : null}
          <span className="ml-auto">{fmtCost(asset.cost)}</span>
        </div>
        {(shelf || asset.target_game) && (
          <div className="mt-1 text-[11px] text-muted truncate">
            {[shelf, asset.target_game].filter(Boolean).join(" · ")}
          </div>
        )}
      </div>

      <div className="absolute bottom-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <a
          className="btn-ghost !px-2 !py-1 bg-black/50 backdrop-blur"
          href={assetFileUrl(asset.id)}
          download
          title="Download"
          onClick={(e) => e.stopPropagation()}
        >
          <Download size={14} />
        </a>
        <button
          className="btn-ghost !px-2 !py-1 bg-black/50 backdrop-blur text-danger"
          title="Delete"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(asset);
          }}
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
