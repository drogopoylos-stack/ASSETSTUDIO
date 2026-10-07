"""nanobanana — Google Gemini 2.5 Flash Image (text-to-image + image editing).

Calls the Gemini ``generateContent`` endpoint with ``responseModalities`` set to
``["IMAGE","TEXT"]`` so the model returns an inline PNG. When an input image is
provided it is attached as inline base64 data, turning the call into an image
edit ("nano-banana" style). Requires a Google AI Studio key stored under the
``gemini`` keychain id.
"""
from __future__ import annotations

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class NanoBananaProvider(Provider):
    id = "nanobanana"
    name = "Nano Banana (Google Gemini Image)"
    stage = StageType.image2d
    kind = ProviderKind.api
    requires_key = True
    key_name = "gemini"
    description = (
        "Google Gemini 2.5 Flash Image generation/editing. Text-to-image, or "
        "edit an input image by passing one as the job input."
    )
    license_note = "Google Gemini image output — review Google's usage terms before monetizing."
    commercial_ok = None
    cost_hint = "~$0.04/img"
    homepage = "https://ai.google.dev/gemini-api/docs/image-generation"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default=""),
        ProviderParam(
            name="model", label="Model", type="select",
            options=[
                "gemini-2.5-flash-image-preview",
                "gemini-2.5-flash-image",
                "gemini-3-pro-image-preview",
                "gemini-2.0-flash-preview-image-generation",
            ],
            default="gemini-2.5-flash-image-preview",
            description="gemini-3-pro-image-preview = Nano Banana Pro · "
                        "gemini-2.5-flash-image = Nano Banana (GA). If a model id 404s, "
                        "pick another — Google rotates these as they graduate from preview.",
        ),
    ]

    def is_available(self) -> tuple[bool, str]:
        return super().is_available()

    def run(self, ctx: JobContext) -> list:
        import base64

        import httpx

        from ... import keychain

        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Add the Gemini API key in Settings → Providers.")

        prompt = str(ctx.param("prompt", ""))
        model = str(ctx.param("model", "gemini-2.5-flash-image-preview"))

        from ...util import is_image
        parts: list[dict] = [{"text": prompt}]
        imgs = [p for p in ctx.inputs if is_image(p)][:6]
        for src in imgs:
            ctx.log(f"reference image: {src}")
            img_b64 = base64.b64encode(open(src, "rb").read()).decode()
            parts.append({"inline_data": {"mime_type": "image/png", "data": img_b64}})

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
        }

        ctx.progress(0.2, "calling Gemini")
        url = _ENDPOINT.format(model=model)
        with httpx.Client(timeout=180) as client:
            resp = client.post(url, params={"key": key}, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text}")
        payload = resp.json()

        ctx.progress(0.7, "decoding image")
        candidates = payload.get("candidates") or []
        resp_parts = (candidates[0].get("content", {}).get("parts", []) if candidates else [])
        img_part = next(
            (p for p in resp_parts if (p.get("inline_data") or p.get("inlineData"))),
            None,
        )
        if img_part is None:
            raise RuntimeError(f"Gemini returned no image: {resp.text}")
        data = (img_part.get("inline_data") or img_part.get("inlineData"))["data"]

        out = ctx.out_path(f"{self.id}-{ctx.job.id}.png")
        out.write_bytes(base64.b64decode(data))
        ctx.add_cost(0.04)

        ctx.progress(0.95, "saving asset")
        return [
            ctx.make_asset(
                path=out, type=AssetType.image, prompt=prompt,
                meta={"engine": "gemini", "model": model, "edited": bool(imgs), "refs": len(imgs)},
                license=self.license_note, commercial_ok=self.commercial_ok,
            )
        ]
