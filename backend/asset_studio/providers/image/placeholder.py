"""Procedural, deterministic image generator — the zero-dependency default.

Needs no API key and no GPU, so the full pipeline (and the agent loop) works out
of the box on any machine. It renders a tasteful gradient + glyph composition
seeded by the prompt, so identical prompt+seed give identical output. Swap to
ComfyUI / Flux / SDXL / nanobanana / gpt-image-1 from the dropdown for real art.
"""
from __future__ import annotations

import colorsys
import hashlib
import math
import random

from PIL import Image, ImageDraw, ImageFont

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify


def _seed_from(prompt: str, seed) -> int:
    if seed not in (None, "", -1, "-1"):
        try:
            return int(seed)
        except (TypeError, ValueError):
            pass
    return int(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), 16) % (2**31)


def _hsl(rng, base_h):
    h = (base_h + rng.uniform(-0.08, 0.08)) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, rng.uniform(0.45, 0.65), rng.uniform(0.55, 0.9))
    return int(r * 255), int(g * 255), int(b * 255)


class PlaceholderImageProvider(Provider):
    id = "placeholder-image"
    name = "Procedural (no-key)"
    stage = StageType.image2d
    kind = ProviderKind.local
    requires_key = False
    description = "Deterministic procedural art. Zero deps; always available. Great for wiring/agent tests."
    license_note = "Generated locally — fully owned, commercial use OK."
    commercial_ok = True
    cost_hint = "free"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="game asset"),
        ProviderParam(name="width", label="Width", type="int", default=1024, min=64, max=2048, step=64),
        ProviderParam(name="height", label="Height", type="int", default=1024, min=64, max=2048, step=64),
        ProviderParam(name="seed", label="Seed (-1 = from prompt)", type="seed", default=-1),
        ProviderParam(
            name="style", label="Style", type="select",
            options=["gradient", "emblem", "sprite"], default="emblem",
        ),
    ]

    def run(self, ctx: JobContext) -> list:
        prompt = str(ctx.param("prompt", "game asset"))
        w = int(ctx.param("width", 1024))
        h = int(ctx.param("height", 1024))
        style = ctx.param("style", "emblem")
        seed = _seed_from(prompt, ctx.param("seed", -1))
        rng = random.Random(seed)
        base_h = rng.random()

        ctx.progress(0.2, "composing background")
        img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img, "RGBA")
        top, bottom = _hsl(rng, base_h), _hsl(rng, (base_h + 0.5) % 1.0)
        for y in range(h):
            t = y / max(h - 1, 1)
            draw.line(
                [(0, y), (w, y)],
                fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,),
            )

        ctx.progress(0.5, f"rendering {style}")
        cx, cy = w / 2, h / 2
        if style in ("emblem", "sprite"):
            for i in range(rng.randint(5, 9)):
                rad = min(w, h) * (0.42 - i * 0.04)
                col = _hsl(rng, (base_h + i * 0.12) % 1.0)
                a = 160 if style == "emblem" else 220
                sides = rng.choice([0, 3, 4, 5, 6])
                if sides == 0:
                    draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=col + (a,), width=max(2, int(rad * 0.06)))
                else:
                    pts = [
                        (cx + rad * math.cos(2 * math.pi * k / sides + i),
                         cy + rad * math.sin(2 * math.pi * k / sides + i))
                        for k in range(sides)
                    ]
                    draw.polygon(pts, outline=col + (a,))
        else:
            for _ in range(rng.randint(30, 60)):
                x0, y0 = rng.uniform(0, w), rng.uniform(0, h)
                r = rng.uniform(10, min(w, h) * 0.18)
                draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=_hsl(rng, rng.random()) + (90,))

        # caption
        ctx.progress(0.75, "labeling")
        label = prompt[:42]
        try:
            font = ImageFont.truetype("arial.ttf", max(14, w // 28))
        except Exception:
            font = ImageFont.load_default()
        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.rectangle([0, h - th - 24, w, h], fill=(0, 0, 0, 140))
        draw.text(((w - tw) / 2, h - th - 16), label, font=font, fill=(255, 255, 255, 235))

        out = ctx.out_path(f"{slugify(prompt)}-{seed}.png")
        img.save(out, "PNG")
        ctx.progress(0.95, "saved")
        return [
            ctx.make_asset(
                path=out, type=AssetType.image, prompt=prompt, seed=seed,
                meta={"engine": "procedural", "style": style},
                license=self.license_note, commercial_ok=True,
            )
        ]
