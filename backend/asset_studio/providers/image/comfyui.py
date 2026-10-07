"""ComfyUI text-to-image provider — drive a local ComfyUI server over its HTTP API.

ComfyUI ships an embedded local web server (default ``http://127.0.0.1:8188``)
that exposes a graph-execution API: you POST a node graph to ``/prompt``, poll
``/history/{prompt_id}`` for completion, then ``/view`` the produced PNGs.

This adapter builds a standard txt2img graph
(``CheckpointLoaderSimple -> CLIPTextEncode x2 -> EmptyLatentImage -> KSampler
-> VAEDecode -> SaveImage``) so it works with any checkpoint you have placed in
``ComfyUI/models/checkpoints``. SDXL, Flux and Qwen-Image all use this same
graph shape — they only differ by the checkpoint file, so select the matching
``model`` preset and set ``checkpoint`` to the on-disk filename of those weights.

Everything stays local; nothing is uploaded and no key is required.
"""
from __future__ import annotations

import json
import time
import uuid

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify
from ..comfy_common import ComfyUnloadMixin

_DEFAULT_URL = "http://127.0.0.1:8188"


class ComfyUIImageProvider(ComfyUnloadMixin, Provider):
    id = "comfyui"
    name = "ComfyUI (SDXL / Flux / Qwen)"
    stage = StageType.image2d
    kind = ProviderKind.local
    requires_key = False
    key_name = None
    description = (
        "Generate images on a local ComfyUI server via its HTTP API. Runs a "
        "standard SDXL-style txt2img graph against any checkpoint in "
        "ComfyUI/models/checkpoints. Flux and Qwen-Image use the same graph — "
        "just point 'checkpoint' at the matching weights file."
    )
    license_note = "Commercial use depends on the local checkpoint's license — verify per model."
    commercial_ok = None
    cost_hint = "free"
    homepage = "https://github.com/comfyanonymous/ComfyUI"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="game asset"),
        ProviderParam(name="negative", label="Negative prompt", type="text",
                      default="low quality, blurry"),
        ProviderParam(name="model", label="Model family", type="select",
                      options=["sdxl", "flux", "qwen"], default="sdxl",
                      description="Preset; set 'checkpoint' to the matching weights file."),
        ProviderParam(name="checkpoint", label="Checkpoint", type="string",
                      default="sd_xl_base_1.0.safetensors",
                      description="checkpoint filename in ComfyUI/models/checkpoints"),
        ProviderParam(name="width", label="Width", type="int", default=1024,
                      min=256, max=2048, step=64),
        ProviderParam(name="height", label="Height", type="int", default=1024,
                      min=256, max=2048, step=64),
        ProviderParam(name="steps", label="Steps", type="int", default=25, min=1, max=80),
        ProviderParam(name="cfg", label="CFG scale", type="float", default=7, min=1, max=20),
        ProviderParam(name="sampler", label="Sampler", type="select",
                      options=["euler", "dpmpp_2m", "dpmpp_sde"], default="euler"),
        ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1),
    ]

    # --- availability ------------------------------------------------------
    def _base_url(self, ctx: JobContext | None = None) -> str:
        if ctx is not None:
            return str(ctx.tool("comfyui_url", _DEFAULT_URL)).rstrip("/")
        from ...config import settings
        return str(settings.get("tools", {}).get("comfyui_url", _DEFAULT_URL)).rstrip("/")

    def is_available(self) -> tuple[bool, str]:
        base = self._base_url()
        try:
            import httpx

            resp = httpx.get(f"{base}/system_stats", timeout=1.5)
            if resp.status_code == 200:
                return True, ""
        except Exception:
            pass
        return False, f"Start ComfyUI (embedded local server) at {base}"

    # --- graph -------------------------------------------------------------
    def _build_graph(
        self,
        *,
        checkpoint: str,
        positive: str,
        negative: str,
        width: int,
        height: int,
        steps: int,
        cfg: float,
        sampler: str,
        seed: int,
        filename_prefix: str,
    ) -> dict:
        """A standard ComfyUI txt2img graph keyed by string node ids."""
        return {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positive, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["4", 1]},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": sampler,
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]},
            },
        }

    # --- run ---------------------------------------------------------------
    def run(self, ctx: JobContext) -> list:
        import httpx

        base = self._base_url(ctx)
        prompt = str(ctx.param("prompt", "game asset"))
        negative = str(ctx.param("negative", "low quality, blurry"))
        checkpoint = str(ctx.param("checkpoint", "sd_xl_base_1.0.safetensors"))
        model = str(ctx.param("model", "sdxl"))
        width = int(ctx.param("width", 1024))
        height = int(ctx.param("height", 1024))
        steps = int(ctx.param("steps", 25))
        cfg = float(ctx.param("cfg", 7))
        sampler = str(ctx.param("sampler", "euler"))

        raw_seed = ctx.param("seed", -1)
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError):
            seed = -1
        if seed < 0:
            seed = uuid.uuid4().int % (2**32)

        client_id = uuid.uuid4().hex
        graph = self._build_graph(
            checkpoint=checkpoint, positive=prompt, negative=negative,
            width=width, height=height, steps=steps, cfg=cfg,
            sampler=sampler, seed=seed, filename_prefix=f"assetstudio/{slugify(prompt) or 'image'}",
        )

        ctx.log(f"ComfyUI {model} '{checkpoint}' @ {base} (seed={seed})")
        ctx.progress(0.1, "submitting graph")

        with httpx.Client(timeout=120.0) as http:
            try:
                resp = http.post(f"{base}/prompt", json={"prompt": graph, "client_id": client_id})
            except Exception as e:  # connection refused / DNS / timeout
                raise RuntimeError(f"ComfyUI request failed ({base}): {e}") from e
            if resp.status_code != 200:
                raise RuntimeError(f"ComfyUI /prompt error {resp.status_code}: {resp.text[:500]}")
            try:
                prompt_id = resp.json()["prompt_id"]
            except (json.JSONDecodeError, KeyError) as e:
                raise RuntimeError(f"ComfyUI /prompt gave no prompt_id: {resp.text[:500]}") from e

            # poll history until this prompt's entry materialises
            ctx.progress(0.2, "rendering")
            deadline = time.time() + 600  # 10 min hard cap for slow first loads
            entry = None
            while time.time() < deadline:
                hist = http.get(f"{base}/history/{prompt_id}")
                if hist.status_code == 200:
                    data = hist.json()
                    if prompt_id in data:
                        entry = data[prompt_id]
                        break
                # creep progress toward 0.9 while we wait
                frac = min(0.9, 0.2 + (time.time() - (deadline - 600)) / 600 * 0.7)
                ctx.progress(frac, "rendering")
                time.sleep(1.0)

            if entry is None:
                raise RuntimeError("ComfyUI render timed out (no history entry after 10 min).")

            status = (entry.get("status") or {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI execution error: {json.dumps(status)[:500]}")

            # collect the first produced image from any SaveImage node
            images = []
            for node_out in (entry.get("outputs") or {}).values():
                for img in node_out.get("images", []):
                    if img.get("type") != "temp":
                        images.append(img)
            if not images:
                raise RuntimeError("ComfyUI finished but produced no images.")

            img = images[0]
            ctx.progress(0.92, "downloading image")
            view = http.get(
                f"{base}/view",
                params={
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                },
            )
            if view.status_code != 200:
                raise RuntimeError(f"ComfyUI /view error {view.status_code}: {view.text[:300]}")
            png_bytes = view.content

        out = ctx.out_path(f"{slugify(prompt) or 'image'}-{seed}.png")
        out.write_bytes(png_bytes)
        ctx.progress(0.97, "saved")

        return [
            ctx.make_asset(
                path=out,
                type=AssetType.image,
                prompt=prompt,
                seed=seed,
                meta={
                    "engine": "comfyui",
                    "model": model,
                    "checkpoint": checkpoint,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler": sampler,
                    "negative": negative,
                    "server": base,
                },
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]
