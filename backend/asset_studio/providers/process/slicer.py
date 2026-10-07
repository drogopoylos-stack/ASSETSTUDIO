"""Sprite slicer — cut, clean, orient.

Two modes:
  * ``grid``  — fixed columns × rows sheet → one PNG per cell.
  * ``auto``  — connected-component detection on the alpha channel → one PNG per
                discrete sprite, auto-trimmed to its tight bounding box.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider


def _components(alpha: np.ndarray, min_area: int):
    h, w = alpha.shape
    mask = alpha > 16
    seen = np.zeros_like(mask, dtype=bool)
    boxes = []
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            x0 = x1 = sx
            y0 = y1 = sy
            area = 0
            dq = deque([(sy, sx)])
            seen[sy, sx] = True
            while dq:
                y, x = dq.popleft()
                area += 1
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
                for ny, nx in ((y+1, x), (y-1, x), (y, x+1), (y, x-1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        dq.append((ny, nx))
            if area >= min_area:
                boxes.append((x0, y0, x1 + 1, y1 + 1))
    return boxes


class SpriteSlicerProvider(Provider):
    id = "slicer"
    name = "Sprite Slicer"
    stage = StageType.process2d
    kind = ProviderKind.local
    description = "Cut a sprite sheet into frames (fixed grid or auto component detection), trim + orient."
    license_note = "Frames inherit the source sheet's license."
    commercial_ok = True
    cost_hint = "free"
    params = [
        ProviderParam(name="mode", label="Mode", type="select", options=["auto", "grid"], default="auto"),
        ProviderParam(name="cols", label="Grid cols", type="int", default=4, min=1, max=64, group="Grid"),
        ProviderParam(name="rows", label="Grid rows", type="int", default=4, min=1, max=64, group="Grid"),
        ProviderParam(name="min_area", label="Min sprite area (px)", type="int", default=64, min=1, max=100000, group="Auto"),
        ProviderParam(name="trim", label="Trim transparent border", type="bool", default=True),
    ]

    def run(self, ctx: JobContext) -> list:
        src = ctx.first_image()
        if not src:
            raise RuntimeError("Slicer needs an input image.")
        img = Image.open(src).convert("RGBA")
        arr = np.asarray(img)
        alpha = arr[:, :, 3]
        mode = ctx.param("mode", "auto")
        trim = bool(ctx.param("trim", True))

        ctx.progress(0.2, f"slicing ({mode})")
        if mode == "grid":
            cols, rows = int(ctx.param("cols", 4)), int(ctx.param("rows", 4))
            cw, ch = img.width // cols, img.height // rows
            boxes = [(c * cw, r * ch, (c + 1) * cw, (r + 1) * ch) for r in range(rows) for c in range(cols)]
        else:
            boxes = _components(alpha, int(ctx.param("min_area", 64)))

        if not boxes:
            raise RuntimeError("No sprites detected. Try grid mode or lower min area.")

        out_assets = []
        stem = Path(src).stem
        n = len(boxes)
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            ctx.progress(0.2 + 0.7 * (i / n), f"frame {i+1}/{n}")
            cell = img.crop((x0, y0, x1, y1))
            if trim:
                bbox = cell.getbbox()
                if bbox:
                    cell = cell.crop(bbox)
            out = ctx.out_path(f"{stem}_{i:03d}.png")
            cell.save(out, "PNG")
            out_assets.append(
                ctx.make_asset(
                    path=out, type=AssetType.image, name=out.name,
                    meta={"index": i, "src_box": [x0, y0, x1, y1]},
                    parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                )
            )
        ctx.progress(1.0, f"{n} frames")
        return out_assets
