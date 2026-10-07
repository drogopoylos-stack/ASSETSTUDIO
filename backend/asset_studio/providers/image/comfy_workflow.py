"""Generic ComfyUI 2D-image provider — runs ANY ComfyUI image workflow.

Out of the box it runs a built-in SDXL text-to-image graph. Pick a preset (FLUX,
Qwen-Image, SD3.5…) for guidance, or paste an exported workflow (ComfyUI → Save →
API Format) to run literally anything. The studio injects %prompt%, %negative%,
%seed%, %image%, %width%, %height% wherever you put those tokens in the workflow.
"""
from __future__ import annotations

import json
import random

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ...util import slugify
from ..base import JobContext, Provider
from ..comfy_common import (
    CATALOG_BY_ID, ComfyUnloadMixin, catalog_for, collect_outputs, comfy_available, comfy_base,
    fetch, inject, load_preset_workflow, new_client_id, submit, upload_image, wait,
    WORKFLOWS_DIR,
)

_PRESETS = ["built-in SDXL"] + [c["id"] for c in catalog_for(("2d",))] + ["custom (paste workflow)"]


def _default_sdxl_graph() -> dict:
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "%checkpoint%"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "%prompt%", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "%negative%", "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "%width%", "height": "%height%", "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": "%seed%", "steps": "%steps%", "cfg": "%cfg%", "sampler_name": "%sampler%",
            "scheduler": "normal", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "assetstudio", "images": ["8", 0]}},
    }


class ComfyWorkflowImageProvider(ComfyUnloadMixin, Provider):
    id = "comfyui-workflow"
    name = "ComfyUI Workflow (any 2D model)"
    stage = StageType.image2d
    kind = ProviderKind.local
    requires_key = False
    description = ("Run any ComfyUI image workflow — FLUX, Qwen-Image, SD3.5, SDXL… "
                   "Built-in SDXL works out of the box; pick a preset for setup guidance, or paste "
                   "an exported (API-format) workflow. Future-proof: new models = drop the workflow in.")
    license_note = "Depends on the model/checkpoint you run."
    cost_hint = "free (local GPU)"
    homepage = "https://www.comfy.org"
    params = [
        ProviderParam(name="preset", label="Model / preset", type="select", options=_PRESETS,
                      default="built-in SDXL", group="Model",
                      description="Built-in SDXL runs immediately. Others need their ComfyUI node + a saved workflow."),
        ProviderParam(name="prompt", label="Prompt", type="text", default="", group="Prompt"),
        ProviderParam(name="negative", label="Negative", type="text", default="lowres, blurry, watermark, text", group="Prompt"),
        ProviderParam(name="checkpoint", label="SDXL checkpoint (built-in preset)", type="string",
                      default="sd_xl_base_1.0.safetensors", group="Model"),
        ProviderParam(name="width", label="Width", type="int", default=1024, min=256, max=2048, step=64, group="Size"),
        ProviderParam(name="height", label="Height", type="int", default=1024, min=256, max=2048, step=64, group="Size"),
        ProviderParam(name="steps", label="Steps", type="int", default=25, min=1, max=80, group="Sampler"),
        ProviderParam(name="cfg", label="CFG", type="float", default=6.5, min=1, max=20, step=0.5, group="Sampler"),
        ProviderParam(name="sampler", label="Sampler", type="select",
                      options=["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde", "dpmpp_2m_sde"],
                      default="dpmpp_2m", group="Sampler"),
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
        if preset and preset != "built-in SDXL":
            wf = load_preset_workflow(preset)
            if wf:
                return wf
            cat = CATALOG_BY_ID.get(preset, {})
            raise RuntimeError(
                f"No saved workflow for '{preset}'. In ComfyUI install its node ({cat.get('node', '?')}), "
                f"build the workflow, Save → API Format, and put it at {WORKFLOWS_DIR / (preset + '.json')}.")
        return _default_sdxl_graph()

    def run(self, ctx: JobContext) -> list:
        import httpx
        base = comfy_base(ctx)
        preset = str(ctx.param("preset", "built-in SDXL"))
        prompt = str(ctx.param("prompt", ""))
        graph = self._resolve_workflow(ctx, preset)
        seed = int(ctx.param("seed", -1))
        if seed < 0:
            seed = random.randint(0, 2**31 - 1)
        mapping = {
            "%prompt%": prompt, "%negative%": str(ctx.param("negative", "")),
            "%checkpoint%": str(ctx.param("checkpoint", "")),
            "%width%": int(ctx.param("width", 1024)), "%height%": int(ctx.param("height", 1024)),
            "%steps%": int(ctx.param("steps", 25)), "%cfg%": float(ctx.param("cfg", 6.5)),
            "%sampler%": str(ctx.param("sampler", "dpmpp_2m")), "%seed%": seed,
        }
        ctx.progress(0.05, "preparing workflow")
        with httpx.Client(timeout=120.0) as http:
            img = ctx.first_image()
            if img:
                try:
                    mapping["%image%"] = upload_image(http, base, img)
                except Exception as e:
                    ctx.log(f"image upload failed: {e}")
            g = inject(graph, mapping)
            ctx.progress(0.15, "submitting to ComfyUI")
            pid = submit(http, base, g, new_client_id())
            entry = wait(http, base, pid, ctx, timeout=600)
            imgs, _files = collect_outputs(entry)
            if not imgs:
                raise RuntimeError("Workflow produced no image — make sure it ends in a SaveImage node.")
            ctx.progress(0.95, "downloading")
            data = fetch(http, base, imgs[-1])
        out = ctx.out_path(f"{slugify(prompt) or 'image'}.png")
        out.write_bytes(data)
        return [ctx.make_asset(path=out, type=AssetType.image, prompt=prompt, seed=seed,
                               meta={"engine": "comfyui", "preset": preset, "server": base})]
