import { useEffect, useRef, useState } from "react";
import { ImagePlus, Loader2, Upload, X } from "lucide-react";
import { api, assetPreviewUrl } from "../api/client";
import { useStore } from "../store/useStore";
import type { Asset } from "../types";
import { cls, Empty } from "./ui";

const TYPE_FILTER: Record<string, (a: Asset) => boolean> = {
  image: (a) => ["image", "atlas", "render"].includes(a.type),
  mesh: (a) => a.type === "model",
  any: () => true,
};

// a value item is either a catalog asset id or an absolute file path (uploaded)
const isPath = (s: string) => /[\\/]/.test(s) || /^[A-Za-z]:/.test(s);
const previewFor = (item: string) =>
  isPath(item) ? `/api/file?path=${encodeURIComponent(item)}` : assetPreviewUrl(item);

// Pick stage inputs: choose recent catalog items, OR upload / drag your own reference
// image(s) from disk. Values are asset ids and/or absolute file paths — the job queue
// resolves both, so a provider can work image-only or image+prompt.
export default function InputPicker({
  type = "any",
  multiple = false,
  value,
  onChange,
}: {
  type?: "image" | "mesh" | "any";
  multiple?: boolean;
  value: string[];
  onChange: (ids: string[]) => void;
}) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const recentLen = useStore((s) => s.recentAssets.length);
  const toast = useStore((s) => s.toast);
  const canUpload = type === "image" || type === "any";

  useEffect(() => {
    api.assets({ limit: 60 }).then((a) => setAssets(a.filter(TYPE_FILTER[type]))).catch(() => {});
  }, [type, recentLen]);

  function toggle(id: string) {
    if (value.includes(id)) onChange(value.filter((x) => x !== id));
    else onChange(multiple ? [...value, id] : [id]);
  }
  function remove(item: string) { onChange(value.filter((x) => x !== item)); }

  async function addFiles(files: FileList | File[]) {
    const list = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (!list.length) return;
    setUploading(true);
    const added: string[] = [];
    for (const f of list) {
      try { const r = await api.uploadInput(f); added.push(r.path); }
      catch (e: any) { toast(`Upload failed: ${e.message}`, "danger"); }
    }
    setUploading(false);
    if (added.length) onChange(multiple ? [...value, ...added] : [added[added.length - 1]]);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="label !mb-0">
          Reference {type !== "any" ? `(${type})` : ""} {multiple ? "" : "— one"}
        </span>
        {value.length > 0 && <span className="chip">{value.length} selected</span>}
      </div>

      {/* selected tray (catalog assets + uploaded files) */}
      {value.length > 0 && (
        <div className="flex gap-2 flex-wrap mb-2">
          {value.map((item) => (
            <div key={item} className="relative w-16 h-16 rounded-lg overflow-hidden border border-brand ring-1 ring-brand bg-panel2 group">
              <img src={previewFor(item)} className="w-full h-full object-cover" />
              <button onClick={() => remove(item)} title="Remove"
                className="absolute top-0.5 right-0.5 p-0.5 rounded bg-bg/70 text-muted hover:text-danger opacity-0 group-hover:opacity-100">
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* upload / drag-drop your own reference image(s) */}
      {canUpload && (
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
          className={cls("mb-2 flex items-center justify-center gap-2 py-3 rounded-lg border border-dashed cursor-pointer text-xs",
            dragOver ? "border-brand bg-brand/10 text-brand" : "border-line text-muted hover:border-muted hover:text-text")}>
          {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          {uploading ? "Uploading…" : `Add reference image${multiple ? "(s)" : ""} — click or drop here`}
          <input ref={fileRef} type="file" accept="image/*" multiple={multiple} className="hidden"
            onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        </div>
      )}

      {/* or pick from recent catalog */}
      {assets.length === 0 ? (
        !canUpload && <Empty icon={<ImagePlus size={20} />} label={`No ${type} assets yet — generate one first`} />
      ) : (
        <>
          <div className="text-[10px] text-muted mb-1 uppercase tracking-wide">or pick from your catalog</div>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {assets.map((a) => (
              <button key={a.id} onClick={() => toggle(a.id)}
                className={cls("shrink-0 w-20 h-20 rounded-lg overflow-hidden border bg-panel2",
                  value.includes(a.id) ? "border-brand ring-2 ring-brand" : "border-line hover:border-muted")}
                title={a.name}>
                <img src={a.preview_path ? `/api/file?path=${encodeURIComponent(a.preview_path)}` : assetPreviewUrl(a.id)}
                  className="w-full h-full object-contain" />
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
