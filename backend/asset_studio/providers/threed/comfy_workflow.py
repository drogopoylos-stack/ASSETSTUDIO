"""Generic ComfyUI 3D provider — runs the new free local image→3D models.

Drives TripoSplat (3D Gaussian splats), Hunyuan3D 2.1, TRELLIS.2, TripoSG, SF3D and
friends through your ComfyUI. The studio doesn't bundle the weights — install the
model's ComfyUI custom node + weights once, export its workflow (Save → API Format),
save it under data/comfy_workflows/<preset>.json, and run it here. The input image,
%seed% and %prompt% are injected wherever you place those tokens.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider
from ..comfy_common import (
    CATALOG_BY_ID, ComfyUnloadMixin, catalog_for, collect_outputs, comfy_available, comfy_base,
    fetch, inject, load_preset_workflow, new_client_id, submit, upload_image, wait,
    WORKFLOWS_DIR,
)

_PRESETS = [c["id"] for c in catalog_for(("3d", "splat"))] + ["custom (paste workflow)"]


class ComfyWorkflow3DProvider(ComfyUnloadMixin, Provider):
    id = "comfyui-3d"
    name = "ComfyUI 3D (TripoSplat / Hunyuan3D / TRELLIS / TripoSG…)"
    stage = StageType.gen3d
    kind = ProviderKind.local
    requires_key = False
    description = ("Image→3D via your ComfyUI: TripoSplat (Gaussian splats), Hunyuan3D 2.1, TRELLIS.2, "
                   "TripoSG, Stable Fast 3D and more. Install the model's ComfyUI node, save its workflow "
                   "(API format) as data/comfy_workflows/<preset>.json, and run it here.")
    license_note = "Depends on the model you run (see the preset's homepage)."
    cost_hint = "free (local GPU)"
    homepage = "https://github.com/MrForExample/ComfyUI-3D-Pack"
    params = [
        ProviderParam(name="preset", label="3D model / preset", type="select", options=_PRESETS,
                      default=_PRESETS[0], group="Model",
                      description="Each needs its ComfyUI node + a saved workflow. TripoSplat & TripoSR are the most 8GB-friendly."),
        ProviderParam(name="prompt", label="Prompt (if your workflow uses one)", type="text", default="", group="Prompt"),
        ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1, group="Sampler"),
        ProviderParam(name="workflow", label="Workflow JSON (custom / preset override)", type="text",
                      default="", group="Advanced",
                      description="Paste a ComfyUI workflow exported as API Format. Overrides the preset."),
    ]

    def is_available(self) -> tuple[bool, str]:
        return comfy_available(comfy_base())

    def _resolve_workflow(self, ctx: JobContext, preset: str) -> dict:
        raw = str(ctx.param("workflow", "")).strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Workflow JSON is invalid: {e}")
            return data.get("prompt", data) if isinstance(data, dict) else data
        if preset == "custom (paste workflow)":
            raise RuntimeError("Paste an exported ComfyUI workflow (Save → API Format) in the Workflow JSON field.")
        wf = load_preset_workflow(preset)
        if wf:
            return wf
        cat = CATALOG_BY_ID.get(preset, {})
        raise RuntimeError(
            f"No saved workflow for '{cat.get('name', preset)}'. In ComfyUI install its node "
            f"({cat.get('node', '?')}) + weights, build the workflow, Save → API Format, and put it at "
            f"{WORKFLOWS_DIR / (preset + '.json')}. Homepage: {cat.get('homepage', '')}")

    def run(self, ctx: JobContext) -> list:
        import httpx
        base = comfy_base(ctx)
        preset = str(ctx.param("preset", _PRESETS[0]))
        graph = self._resolve_workflow(ctx, preset)
        seed = int(ctx.param("seed", -1))
        if seed < 0:
            seed = random.randint(0, 2**31 - 1)
        mapping = {"%seed%": seed, "%prompt%": str(ctx.param("prompt", ""))}
        ctx.progress(0.05, "preparing workflow")
        with httpx.Client(timeout=180.0) as http:
            img = ctx.first_image()
            if img:
                try:
                    mapping["%image%"] = upload_image(http, base, img)
                except Exception as e:
                    ctx.log(f"image upload failed: {e}")
            g = inject(graph, mapping)
            ctx.progress(0.15, "submitting to ComfyUI")
            pid = submit(http, base, g, new_client_id())
            entry = wait(http, base, pid, ctx, timeout=1200)  # 3D can take a while
            imgs, files = collect_outputs(entry)
            if files:
                ref = files[-1]
                ext = Path(ref["filename"]).suffix.lower() or ".glb"
                data = fetch(http, base, ref)
                out = ctx.out_path(f"comfyui3d-{ctx.job.id}{ext}")
                out.write_bytes(data)
            elif imgs:
                # workflow only produced a render — save it but flag the missing mesh export
                ctx.log("No mesh/splat file in outputs — add a Save GLB/PLY node to export the 3D asset.")
                data = fetch(http, base, imgs[-1])
                out = ctx.out_path(f"comfyui3d-{ctx.job.id}.png")
                out.write_bytes(data)
                return [ctx.make_asset(path=out, type=AssetType.image, prompt=str(ctx.param("prompt", "")),
                                       seed=seed, meta={"engine": "comfyui", "preset": preset, "note": "render only (no mesh export)"})]
            else:
                raise RuntimeError("ComfyUI produced no output. Check your workflow's Save node.")

        # render a quick preview for real 3D files (best-effort; splats may not rasterize)
        ctx.progress(0.95, "rendering preview")
        preview = None
        try:
            from ...render.software_render import render_views
            frames = render_views(out, n=1, size=384)
            if frames:
                ppath = ctx.out_path(f"comfyui3d-{ctx.job.id}.preview.png")
                frames[0].save(ppath, "PNG")
                preview = str(ppath)
        except Exception as e:
            ctx.log(f"preview render skipped: {e}")

        return [ctx.make_asset(path=out, type=AssetType.model, seed=seed, preview_path=preview,
                               meta={"engine": "comfyui", "preset": preset, "server": base})]
