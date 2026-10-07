import { useEffect, useState } from "react";
import { Image as ImageIcon, RefreshCw } from "lucide-react";
import { api, assetFileUrl, assetPreviewUrl } from "../api/client";
import type { Asset } from "../types";
import StageRunner from "../components/StageRunner";

// Dedicated Image tab: the image2d generator (free local + API engines) plus a
// gallery of everything you've made. Results auto-save to the Catalog via the
// normal jobs pipeline, so this is a focused front door for image generation.
export default function ImageStudio() {
  const [recent, setRecent] = useState<Asset[]>([]);
  const load = () => api.assets({ stage: "image2d", limit: 24 }).then(setRecent).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <div className="space-y-4">
      <StageRunner
        stage="image2d"
        title="Image"
        subtitle="Generate images — free & local (Qwen / Flux / SDXL via ComfyUI) or API (OpenAI GPT Image 2, Google Nano Banana / Pro). Add one or more reference images (image-only or image+prompt). API keys in Settings → API keys; results auto-save to your Catalog."
        inputType="image"
        multiInput
      />

      <div className="card p-3">
        <div className="flex items-center gap-2 mb-2">
          <ImageIcon size={15} className="text-brand" />
          <span className="font-semibold text-sm">Recent images</span>
          <span className="text-muted text-xs">· {recent.length}</span>
          <button className="ml-auto chip hover:text-text" onClick={load} title="Refresh"><RefreshCw size={12} /></button>
        </div>
        {recent.length === 0 ? (
          <div className="text-muted text-xs py-6 text-center">No images yet — generate one above and it'll show up here.</div>
        ) : (
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
            {recent.map((a) => (
              <a key={a.id} href={assetFileUrl(a.id)} target="_blank" rel="noreferrer"
                className="block rounded-lg overflow-hidden border border-line bg-panel2 hover:border-brand transition-colors"
                title={a.prompt || a.name}>
                <img src={assetPreviewUrl(a.id)} className="w-full aspect-square object-cover" loading="lazy" />
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
