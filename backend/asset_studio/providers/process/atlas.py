"""Texture-atlas / sprite-sheet packer.

Packs all input images into a single power-of-two PNG using a simple shelf/skyline
packer and writes a JSON sidecar (TexturePacker-ish "frames" map) so the atlas is
usable directly in HTML5 engines (Phaser, PixiJS, etc.).
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


class AtlasPackProvider(Provider):
    id = "atlas"
    name = "Atlas Pack"
    stage = StageType.process2d
    kind = ProviderKind.local
    description = "Pack many sprites into one atlas PNG + JSON frame map (Phaser/Pixi compatible)."
    license_note = "Atlas inherits the most-restrictive license of its inputs."
    commercial_ok = True
    cost_hint = "free"
    params = [
        ProviderParam(name="padding", label="Padding (px)", type="int", default=2, min=0, max=32),
        ProviderParam(name="max_size", label="Max atlas size", type="int", default=2048, min=256, max=8192),
        ProviderParam(name="pow2", label="Power-of-two", type="bool", default=True),
    ]

    def run(self, ctx: JobContext) -> list:
        srcs = [p for p in ctx.inputs]
        if len(srcs) < 1:
            raise RuntimeError("Atlas packing needs at least one input image.")
        pad = int(ctx.param("padding", 2))
        max_size = int(ctx.param("max_size", 2048))

        ctx.progress(0.15, "loading sprites")
        imgs = [(Path(s).name, Image.open(s).convert("RGBA")) for s in srcs]
        imgs.sort(key=lambda t: t[1].height, reverse=True)

        # skyline / shelf packing
        atlas_w = min(max_size, _next_pow2(max(im.width for _, im in imgs) + pad * 2))
        x = y = shelf_h = 0
        frames = {}
        placements = []
        for name, im in imgs:
            w, h = im.width + pad, im.height + pad
            if x + w > atlas_w:
                x = 0
                y += shelf_h
                shelf_h = 0
            placements.append((name, im, x, y))
            frames[name] = {
                "frame": {"x": x, "y": y, "w": im.width, "h": im.height},
                "rotated": False, "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": im.width, "h": im.height},
                "sourceSize": {"w": im.width, "h": im.height},
            }
            x += w
            shelf_h = max(shelf_h, h)
        atlas_h = y + shelf_h
        if ctx.param("pow2", True):
            atlas_h = _next_pow2(atlas_h)

        ctx.progress(0.6, f"compositing {atlas_w}x{atlas_h}")
        atlas = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))
        for name, im, px, py in placements:
            atlas.paste(im, (px, py), im)

        stem = ctx.param("name", "atlas")
        png = ctx.out_path(f"{stem}.png")
        atlas.save(png, "PNG")
        meta = {
            "frames": frames,
            "meta": {"image": png.name, "size": {"w": atlas_w, "h": atlas_h}, "scale": 1, "format": "RGBA8888"},
        }
        jpath = ctx.out_path(f"{stem}.json")
        jpath.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        ctx.progress(0.95, "saved")
        commercial = all(
            (a.commercial_ok is not False) for a in ctx.input_assets
        ) if ctx.input_assets else None
        return [
            ctx.make_asset(
                path=png, type=AssetType.atlas, name=png.name,
                meta={"count": len(imgs), "json": str(jpath), "width": atlas_w, "height": atlas_h},
                commercial_ok=commercial,
            )
        ]
