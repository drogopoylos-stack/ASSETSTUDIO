"""PNG/JPG → WebP conversion with quality + size-budget reporting."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider


class WebpProvider(Provider):
    id = "webp"
    name = "PNG → WebP"
    stage = StageType.process2d
    kind = ProviderKind.local
    description = "Convert images to WebP for smaller web payloads. Lossless or quality-controlled."
    license_note = "Output inherits the source image's license."
    commercial_ok = True
    cost_hint = "free"
    params = [
        ProviderParam(name="lossless", label="Lossless", type="bool", default=False),
        ProviderParam(name="quality", label="Quality", type="int", default=85, min=1, max=100),
        ProviderParam(name="method", label="Effort", type="int", default=6, min=0, max=6),
    ]

    def run(self, ctx: JobContext) -> list:
        srcs = [p for p in ctx.inputs] or []
        if not srcs:
            raise RuntimeError("WebP conversion needs at least one input image.")
        out_assets = []
        n = len(srcs)
        for i, src in enumerate(srcs):
            ctx.progress((i + 0.5) / n, f"converting {Path(src).name}")
            im = Image.open(src).convert("RGBA")
            out = ctx.out_path(f"{Path(src).stem}.webp")
            im.save(
                out, "WEBP",
                lossless=bool(ctx.param("lossless", False)),
                quality=int(ctx.param("quality", 85)),
                method=int(ctx.param("method", 6)),
            )
            before = Path(src).stat().st_size
            after = out.stat().st_size
            saved = round(100 * (1 - after / max(before, 1)), 1)
            out_assets.append(
                ctx.make_asset(
                    path=out, type=AssetType.image, name=out.name,
                    meta={"format": "webp", "saved_percent": saved, "src_bytes": before},
                    parent_id=ctx.input_assets[i].id if i < len(ctx.input_assets) else None,
                )
            )
        ctx.progress(1.0, "done")
        return out_assets
