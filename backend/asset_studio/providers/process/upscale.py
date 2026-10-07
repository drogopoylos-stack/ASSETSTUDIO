"""Image upscaling. Uses Real-ESRGAN (basicsr) when installed for AI super-
resolution; otherwise falls back to a high-quality PIL Lanczos resize so the
feature always works on any machine.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

# Real-ESRGAN model weight URLs (downloaded on first use into the model cache).
_MODEL_URLS = {
    "RealESRGAN_x4plus": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.1.0/RealESRGAN_x4plus.pth"
    ),
    "RealESRGAN_x4plus_anime_6B": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
    ),
}


class RealESRGANProvider(Provider):
    id = "realesrgan"
    name = "Upscale (Real-ESRGAN)"
    stage = StageType.process2d
    kind = ProviderKind.local
    requires_key = False
    description = (
        "Upscale images 2x/4x. Uses Real-ESRGAN for AI super-resolution when "
        "installed, else a Lanczos resize fallback so it always runs."
    )
    license_note = "Output inherits the source image license."
    commercial_ok = True
    cost_hint = "free"
    homepage = "https://github.com/xinntao/Real-ESRGAN"
    params = [
        ProviderParam(name="scale", label="Scale", type="select",
                      options=["2", "4"], default="4"),
        ProviderParam(name="model", label="Model", type="select",
                      options=["RealESRGAN_x4plus", "RealESRGAN_x4plus_anime_6B"],
                      default="RealESRGAN_x4plus"),
        ProviderParam(name="tile", label="Tile", type="int", default=0, min=0, max=1024,
                      step=64, description="0 = no tiling"),
    ]

    def is_available(self) -> tuple[bool, str]:
        return True, ""  # always usable thanks to the Lanczos fallback

    def run(self, ctx: JobContext) -> list:
        src = ctx.first_image()
        if not src:
            raise RuntimeError("Upscale needs an input image.")

        scale = int(ctx.param("scale", "4"))
        model_name = str(ctx.param("model", "RealESRGAN_x4plus"))
        tile = int(ctx.param("tile", 0))

        ctx.progress(0.15, "loading image")
        img = Image.open(src).convert("RGB")
        src_size = list(img.size)  # [w, h]

        method = "lanczos-fallback"
        out_img: Image.Image
        try:
            out_img = self._realesrgan(ctx, src, scale, model_name, tile)
            method = "realesrgan"
        except Exception as e:  # noqa: BLE001 - any failure → graceful fallback
            ctx.log(f"Real-ESRGAN unavailable ({e}); using Lanczos fallback.")
            ctx.progress(0.5, "lanczos resize")
            out_img = img.resize(
                (src_size[0] * scale, src_size[1] * scale), Image.LANCZOS
            )

        ctx.progress(0.9, "saving")
        out = ctx.out_path(f"{Path(src).stem}-x{scale}.png")
        out_img.save(out, "PNG")
        out_size = list(out_img.size)

        ctx.progress(1.0, "done")
        parent = ctx.input_assets[0] if ctx.input_assets else None
        return [
            ctx.make_asset(
                path=out, type=AssetType.image, name=out.name,
                meta={
                    "method": method,
                    "scale": scale,
                    "src_size": src_size,
                    "out_size": out_size,
                    "model": model_name if method == "realesrgan" else None,
                },
                license=self.license_note,
                commercial_ok=parent.commercial_ok if parent else self.commercial_ok,
                parent_id=parent.id if parent else None,
            )
        ]

    def _realesrgan(
        self,
        ctx: JobContext,
        src: str,
        scale: int,
        model_name: str,
        tile: int,
    ) -> Image.Image:
        """Run Real-ESRGAN; raises on any missing dep/weight so run() can fall back."""
        import numpy as np  # lazy: only needed on the AI path
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer

        url = _MODEL_URLS.get(model_name)
        if not url:
            raise RuntimeError(f"Unknown Real-ESRGAN model '{model_name}'.")

        ctx.progress(0.3, f"loading {model_name}")
        # anime_6B uses 6 RRDB blocks; the standard x4plus uses 23.
        num_block = 6 if model_name.endswith("anime_6B") else 23
        arch = RRDBNet(
            num_in_ch=3, num_out_ch=3, num_feat=64,
            num_block=num_block, num_grow_ch=32, scale=4,
        )
        upsampler = RealESRGANer(
            scale=4,
            model_path=url,  # RealESRGANer downloads weights from a URL on first use
            model=arch,
            tile=tile,
            tile_pad=10,
            pre_pad=0,
            half=False,
        )

        ctx.progress(0.5, f"upscaling x{scale}")
        bgr = np.asarray(Image.open(src).convert("RGB"))[:, :, ::-1]  # RGB→BGR for cv2
        output, _ = upsampler.enhance(bgr, outscale=scale)
        rgb = np.ascontiguousarray(output[:, :, ::-1])  # BGR→RGB
        return Image.fromarray(rgb)
