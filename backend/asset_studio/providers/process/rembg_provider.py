"""Background removal. Uses ``rembg`` (U2-Net) when installed; otherwise falls
back to a fast border-flood heuristic so the feature always works.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider


def _flood_remove(img: Image.Image, tol: int = 28) -> Image.Image:
    """Remove background by flood-filling transparent from the 4 borders."""
    rgb = img.convert("RGB")
    arr = np.asarray(rgb).astype(np.int16)
    h, w, _ = arr.shape
    visited = np.zeros((h, w), dtype=bool)
    out_alpha = np.full((h, w), 255, dtype=np.uint8)

    corners = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
    stack = list(corners)
    seeds = [arr[y, x] for y, x in corners]
    ref = np.mean(seeds, axis=0)

    # simple iterative flood with color tolerance
    from collections import deque

    dq = deque(corners)
    while dq:
        y, x = dq.popleft()
        if y < 0 or y >= h or x < 0 or x >= w or visited[y, x]:
            continue
        if np.abs(arr[y, x] - ref).sum() > tol * 3:
            continue
        visited[y, x] = True
        out_alpha[y, x] = 0
        dq.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])

    rgba = rgb.convert("RGBA")
    a = Image.fromarray(out_alpha, "L")
    rgba.putalpha(a)
    return rgba


class RembgProvider(Provider):
    id = "rembg"
    name = "Background Removal (rembg)"
    stage = StageType.process2d
    kind = ProviderKind.local
    requires_key = False
    description = "Remove background to transparent PNG. Uses rembg/U2-Net if installed, else a border-flood fallback."
    license_note = "Output inherits the source image's license."
    commercial_ok = True
    cost_hint = "free"
    homepage = "https://github.com/danielgatis/rembg"
    params = [
        ProviderParam(name="model", label="rembg model", type="select",
                      options=["u2net", "isnet-general-use", "u2netp", "silueta"], default="u2net"),
        ProviderParam(name="tolerance", label="Fallback tolerance", type="int", default=28, min=4, max=120,
                      description="Only used when rembg is not installed."),
    ]

    def is_available(self) -> tuple[bool, str]:
        return True, ""  # always usable thanks to the fallback

    def run(self, ctx: JobContext) -> list:
        src = ctx.first_image()
        if not src:
            raise RuntimeError("Background removal needs an input image.")
        ctx.progress(0.2, "loading image")
        img = Image.open(src).convert("RGBA")

        used = "flood-fallback"
        try:
            from rembg import new_session, remove  # type: ignore

            ctx.progress(0.4, "running rembg")
            session = new_session(ctx.param("model", "u2net"))
            out_img = remove(img, session=session)
            used = f"rembg:{ctx.param('model', 'u2net')}"
        except Exception as e:
            ctx.log(f"rembg unavailable ({e}); using border-flood fallback")
            ctx.progress(0.4, "border-flood fallback")
            out_img = _flood_remove(img, int(ctx.param("tolerance", 28)))

        ctx.progress(0.85, "saving")
        out = ctx.out_path(f"{Path(src).stem}-nobg.png")
        out_img.save(out, "PNG")
        return [
            ctx.make_asset(
                path=out, type=AssetType.image, name=out.name,
                meta={"method": used, "alpha": True},
                parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                commercial_ok=ctx.input_assets[0].commercial_ok if ctx.input_assets else None,
            )
        ]
