"""Local Diffusers text-to-image provider — run Stable Diffusion / SDXL on a GPU.

Uses Hugging Face :mod:`diffusers` (``AutoPipelineForText2Image``) so any
text-to-image model id from the Hub works — ``stabilityai/sd-turbo`` (default,
fast 1-8 step) , ``stabilityai/stable-diffusion-xl-base-1.0``, etc. Weights are
downloaded and cached by ``diffusers`` on first use.

Heavy deps (torch, diffusers, accelerate) are imported lazily inside the
methods, so this module always imports cleanly even when they are absent. CUDA
is strongly recommended; CPU works but is very slow.
"""
from __future__ import annotations

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify


class DiffusersImageProvider(Provider):
    id = "diffusers"
    name = "Local Diffusers (SD/SDXL, GPU)"
    stage = StageType.image2d
    kind = ProviderKind.local
    requires_key = False
    key_name = None
    description = (
        "Run Stable Diffusion / SDXL locally via Hugging Face diffusers. Any "
        "text-to-image Hub model id works; GPU strongly recommended. Pipelines "
        "are cached in-process per model_id to avoid reloading."
    )
    license_note = "Depends on the model weights' license."
    commercial_ok = None
    cost_hint = "free"
    homepage = "https://github.com/huggingface/diffusers"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="game asset"),
        ProviderParam(name="negative", label="Negative prompt", type="text", default=""),
        ProviderParam(name="model_id", label="Model id", type="string",
                      default="stabilityai/sd-turbo", description="HF model id"),
        ProviderParam(name="width", label="Width", type="int", default=768,
                      min=256, max=2048, step=64),
        ProviderParam(name="height", label="Height", type="int", default=768,
                      min=256, max=2048, step=64),
        ProviderParam(name="steps", label="Steps", type="int", default=8, min=1, max=60),
        ProviderParam(name="guidance", label="Guidance scale", type="float",
                      default=1.0, min=0, max=15),
        ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1),
    ]

    # one cached pipeline per model_id, keyed on the instance
    _pipelines: dict[str, object]

    def __init__(self) -> None:
        self._pipelines = {}

    # --- availability ------------------------------------------------------
    def is_available(self) -> tuple[bool, str]:
        try:
            import torch
        except Exception:
            return False, "pip install torch diffusers accelerate (heavy, needs CUDA)"
        try:
            if not torch.cuda.is_available():
                # runnable on CPU, but warn that it will be slow
                return True, "No CUDA GPU detected — generation will run on CPU and be very slow."
        except Exception:
            return True, ""
        return True, ""

    # --- pipeline cache ----------------------------------------------------
    def _get_pipeline(self, model_id: str):
        """Load (or reuse) an AutoPipelineForText2Image for ``model_id``."""
        cached = self._pipelines.get(model_id)
        if cached is not None:
            return cached

        import torch
        from diffusers import AutoPipelineForText2Image

        use_cuda = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
        dtype = torch.float16 if use_cuda else torch.float32
        pipe = AutoPipelineForText2Image.from_pretrained(model_id, torch_dtype=dtype)

        # Prefer CPU offload (fits ~8GB VRAM); fall back to a plain device move.
        offloaded = False
        if use_cuda and hasattr(pipe, "enable_model_cpu_offload"):
            try:
                pipe.enable_model_cpu_offload()
                offloaded = True
            except Exception:
                offloaded = False
        if not offloaded:
            pipe = pipe.to("cuda" if use_cuda else "cpu")

        # quiet, deterministic-ish progress; we report our own progress bar
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass

        self._pipelines[model_id] = pipe
        return pipe

    # --- run ---------------------------------------------------------------
    def run(self, ctx: JobContext) -> list:
        import torch

        prompt = str(ctx.param("prompt", "game asset"))
        negative = str(ctx.param("negative", "")) or None
        model_id = str(ctx.param("model_id", "stabilityai/sd-turbo"))
        width = int(ctx.param("width", 768))
        height = int(ctx.param("height", 768))
        steps = int(ctx.param("steps", 8))
        guidance = float(ctx.param("guidance", 1.0))

        raw_seed = ctx.param("seed", -1)
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError):
            seed = -1

        use_cuda = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
        device = "cuda" if use_cuda else "cpu"
        if not use_cuda:
            ctx.log("No CUDA GPU — running on CPU (slow).")

        ctx.progress(0.1, f"loading {model_id}")
        try:
            pipe = self._get_pipeline(model_id)
        except Exception as e:
            raise RuntimeError(f"Failed to load diffusers model '{model_id}': {e}") from e

        generator = None
        if seed >= 0:
            generator = torch.Generator(device=device).manual_seed(seed)

        def _on_step(_pipe, step_index, _timestep, callback_kwargs):
            frac = 0.2 + (step_index + 1) / max(steps, 1) * 0.7
            ctx.progress(min(0.9, frac), f"sampling {step_index + 1}/{steps}")
            return callback_kwargs

        ctx.progress(0.2, "sampling")
        kwargs = {
            "prompt": prompt,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "width": width,
            "height": height,
            "generator": generator,
        }
        if negative:
            kwargs["negative_prompt"] = negative
        # step callback API differs across diffusers versions — best-effort
        try:
            result = pipe(callback_on_step_end=_on_step, **kwargs)
        except TypeError:
            result = pipe(**kwargs)
        except Exception as e:
            raise RuntimeError(f"diffusers generation failed: {e}") from e

        try:
            image = result.images[0]
        except (AttributeError, IndexError) as e:
            raise RuntimeError("diffusers returned no image.") from e

        ctx.progress(0.95, "saving")
        out = ctx.out_path(f"{slugify(prompt) or 'image'}-{seed if seed >= 0 else 'rand'}.png")
        image.save(out, "PNG")

        return [
            ctx.make_asset(
                path=out,
                type=AssetType.image,
                prompt=prompt,
                seed=seed if seed >= 0 else None,
                meta={
                    "engine": "diffusers",
                    "model_id": model_id,
                    "steps": steps,
                    "guidance": guidance,
                    "device": device,
                    "negative": negative or "",
                },
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]
