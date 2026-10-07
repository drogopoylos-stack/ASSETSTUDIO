import { assetFileUrl } from "../api/client";

// Interactive GLB/GLTF preview via Google's <model-viewer> web component.
export default function ModelViewer({
  assetId,
  src,
  poster,
  className,
}: {
  assetId?: string;
  src?: string;
  poster?: string;
  className?: string;
}) {
  const url = src || (assetId ? assetFileUrl(assetId) : undefined);
  if (!url) return null;
  return (
    <model-viewer
      src={url}
      poster={poster}
      camera-controls
      auto-rotate
      shadow-intensity="1"
      exposure="1.1"
      reveal="auto"
      className={className}
      style={{ width: "100%", height: "100%", background: "#0e1118", borderRadius: 12 }}
    />
  );
}
