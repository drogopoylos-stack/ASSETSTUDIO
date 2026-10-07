"""OpenAI gpt-image-1 — text-to-image generation and image editing.

Uses the Images API: ``/v1/images/generations`` for prompt-only jobs, or
``/v1/images/edits`` (multipart) when an input image is supplied. gpt-image-1
always returns base64 PNG data (``data[0].b64_json``), which is decoded and saved.
Requires an OpenAI key stored under the ``openai`` keychain id.
"""
from __future__ import annotations

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

_GEN_URL = "https://api.openai.com/v1/images/generations"
_EDIT_URL = "https://api.openai.com/v1/images/edits"


class OpenAIImageProvider(Provider):
    id = "openai-image"
    name = "OpenAI GPT Image 2"
    stage = StageType.image2d
    kind = ProviderKind.api
    requires_key = True
    key_name = "openai"
    description = (
        "OpenAI GPT Image 2 (gpt-image-2) — newest model with reasoning, plus gpt-image-1. "
        "Text-to-image, or pass one or more reference images (transparent backgrounds "
        "supported). High-quality, commercial-friendly output."
    )
    license_note = "You own GPT Image outputs per OpenAI terms; commercial use permitted."
    commercial_ok = True
    cost_hint = "~$0.04–0.17/img"
    homepage = "https://developers.openai.com/api/docs/models/gpt-image-2"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default=""),
        ProviderParam(
            name="model", label="Model", type="select",
            options=["gpt-image-2", "gpt-image-1"],
            default="gpt-image-2",
            description="gpt-image-2 = newest (reasoning, best quality + references); gpt-image-1 = previous gen.",
        ),
        ProviderParam(
            name="size", label="Size", type="select",
            options=["1024x1024", "1536x1024", "1024x1536", "auto"],
            default="1024x1024",
        ),
        ProviderParam(
            name="quality", label="Quality", type="select",
            options=["low", "medium", "high", "auto"], default="high",
        ),
        ProviderParam(
            name="background", label="Background", type="select",
            options=["auto", "transparent", "opaque"], default="auto",
        ),
    ]

    def is_available(self) -> tuple[bool, str]:
        return super().is_available()

    def run(self, ctx: JobContext) -> list:
        import base64
        from pathlib import Path

        import httpx

        from ... import keychain
        from ...util import is_image

        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Add the OpenAI API key in Settings → Providers.")

        prompt = str(ctx.param("prompt", ""))
        model = str(ctx.param("model", "gpt-image-2"))
        size = str(ctx.param("size", "1024x1024"))
        quality = str(ctx.param("quality", "high"))
        background = str(ctx.param("background", "auto"))
        headers = {"Authorization": f"Bearer {key}"}

        imgs = [p for p in ctx.inputs if is_image(p)][:10]
        ctx.progress(0.2, f"calling {model}")
        with httpx.Client(timeout=300) as client:
            if imgs:
                ctx.log(f"editing with {len(imgs)} reference image(s)")
                field = "image[]" if len(imgs) > 1 else "image"
                files = [(field, (Path(p).name, open(p, "rb").read(), "image/png")) for p in imgs]
                data = {"model": model, "prompt": prompt, "size": size, "n": "1"}
                resp = client.post(_EDIT_URL, headers=headers, data=data, files=files)
            else:
                json_body = {
                    "model": model,
                    "prompt": prompt,
                    "size": size,
                    "quality": quality,
                    "background": background,
                    "n": 1,
                }
                resp = client.post(_GEN_URL, headers=headers, json=json_body)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI image API error {resp.status_code}: {resp.text}")
        payload = resp.json()

        ctx.progress(0.7, "decoding image")
        items = payload.get("data") or []
        b64 = items[0].get("b64_json") if items else None
        if not b64:
            raise RuntimeError(f"OpenAI returned no image data: {resp.text}")

        out = ctx.out_path(f"{self.id}-{ctx.job.id}.png")
        out.write_bytes(base64.b64decode(b64))
        ctx.add_cost(0.05)

        ctx.progress(0.95, "saving asset")
        return [
            ctx.make_asset(
                path=out, type=AssetType.image, prompt=prompt,
                meta={"engine": model, "size": size, "quality": quality,
                      "background": background, "edited": bool(imgs), "refs": len(imgs)},
                license=self.license_note, commercial_ok=self.commercial_ok,
            )
        ]
