import { Box, Download, Image as ImageIcon, Layers, Trash2, Video } from "lucide-react";
import { assetFileUrl, assetPreviewUrl } from "../api/client";
import type { Asset } from "../types";
import ModelViewer from "./ModelViewer";
import { cls, fmtCost, humanBytes } from "./ui";

export default function AssetCard({
  asset,
  onClick,
  onDelete,
  onUse,
  selected,
  compact,
}: {
  asset: Asset;
  onClick?: (a: Asset) => void;
  onDelete?: (a: Asset) => void;
  onUse?: (a: Asset) => void;
  selected?: boolean;
  compact?: boolean;
}) {
  const isModel = asset.type === "model";
  const polys = asset.meta?.polys ?? asset.meta?.vertices;

  return (
    <div
      className={cls(
        "card overflow-hidden group relative transition-all",
        onClick && "cursor-pointer hover:border-brand",
        selected && "ring-2 ring-brand border-brand"
      )}
      onClick={() => onClick?.(asset)}
    >
      <div className="aspect-square bg-panel2 relative">
        {asset.type === "video" ? (
          <video src={assetFileUrl(asset.id)} controls preload="metadata"
            className="w-full h-full object-contain" onClick={(e) => e.stopPropagation()} />
        ) : isModel ? (
          asset.preview_path ? (
            <img src={`/api/file?path=${encodeURIComponent(asset.preview_path)}`} className="w-full h-full object-contain" />
          ) : (
            <div className="w-full h-full">
              <ModelViewer assetId={asset.id} />
            </div>
          )
        ) : (
          <img
            src={assetPreviewUrl(asset.id)}
            className="w-full h-full object-contain"
            loading="lazy"
            style={{ backgroundImage: "repeating-conic-gradient(#1a1e2b 0% 25%, #13161f 0% 50%)", backgroundSize: "20px 20px" }}
          />
        )}
        <span className="absolute top-2 left-2 chip bg-black/50 backdrop-blur">
          {asset.type === "video" ? <Video size={11} /> : isModel ? <Box size={11} /> : asset.type === "atlas" ? <Layers size={11} /> : <ImageIcon size={11} />}
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
        {!compact && (
          <div className="mt-1 flex items-center gap-2 flex-wrap text-[11px] text-muted">
            <span>{humanBytes(asset.size_bytes)}</span>
            {asset.meta?.width && <span>{asset.meta.width}×{asset.meta.height}</span>}
            {polys ? <span>{polys.toLocaleString()} tris</span> : null}
            <span className="ml-auto">{fmtCost(asset.cost)}</span>
          </div>
        )}
        {!compact && asset.provider_id && (
          <div className="mt-1 text-[11px] text-muted truncate">via {asset.provider_id}</div>
        )}
      </div>

      <div className="absolute bottom-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {onUse && (
          <button className="btn-ghost !px-2 !py-1 bg-black/50 backdrop-blur" title="Use as input"
            onClick={(e) => { e.stopPropagation(); onUse(asset); }}>
            <Layers size={14} />
          </button>
        )}
        <a className="btn-ghost !px-2 !py-1 bg-black/50 backdrop-blur" href={assetFileUrl(asset.id)} download
          title="Download" onClick={(e) => e.stopPropagation()}>
          <Download size={14} />
        </a>
        {onDelete && (
          <button className="btn-ghost !px-2 !py-1 bg-black/50 backdrop-blur text-danger" title="Delete"
            onClick={(e) => { e.stopPropagation(); onDelete(asset); }}>
            <Trash2 size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
