"""Shared video parameters, validation and output metadata."""
from __future__ import annotations

import math
import random
from pathlib import Path

from ...models import AssetType, ProviderParam

RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
PROMPT_PARAMS = [
    ProviderParam(name="prompt", label="Scene / motion / sound prompt", type="text", default="", group="Prompt"),
    ProviderParam(name="duration", label="Duration (seconds)", type="int", default=5, min=5, max=15, group="Video"),
    ProviderParam(name="aspect_ratio", label="Aspect ratio", type="select", options=RATIOS, default="16:9", group="Video"),
    ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1, group="Video"),
]


def video_options(ctx):
    prompt = str(ctx.param("prompt", "")).strip()
    if not prompt:
        raise ValueError("Write a video prompt before clicking Generate.")
    duration = float(ctx.param("duration", 5))
    if not math.isfinite(duration) or not 5 <= duration <= 15:
        raise ValueError("Video duration must be between 5 and 15 seconds.")
    ratio = str(ctx.param("aspect_ratio", "16:9"))
    if ratio not in RATIOS:
        raise ValueError("Choose one of the supported aspect ratios.")
    seed = int(ctx.param("seed", -1))
    if seed == -1:
        seed = random.randrange(2**31)
    if not 0 <= seed < 2**31:
        raise ValueError("Seed must be -1 (random) or a nonnegative 32-bit integer.")
    return prompt, duration, ratio, seed


def dimensions(ratio, resolution):
    edge = {"480P": 480, "768P": 768}.get(resolution)
    if not edge:
        raise ValueError("Local H3 supports 480P and 768P in this workflow.")
    a, b = (int(x) for x in ratio.split(":"))
    width, height = (edge * x / min(a, b) for x in (a, b))
    # H3's native area cap is 1344x768. Preserve wide/portrait framing
    # while keeping every canvas on the 32-pixel grid and below that cap.
    cap = 1344 * 768
    scale = min(1, math.sqrt(cap / (width * height)))
    width, height = (round(x * scale / 32) * 32 for x in (width, height))
    while width * height > cap:
        if width >= height:
            width -= 32
        else:
            height -= 32
    return width, height


def save_video_asset(ctx, path, prompt, seed, meta, license_note, commercial_ok=None):
    return [ctx.make_asset(path=path, type=AssetType.video, prompt=prompt, seed=seed,
                           meta=meta, license=license_note, commercial_ok=commercial_ok)]


def video_refs(entry):
    refs = []
    for output in (entry.get("outputs") or {}).values():
        if not isinstance(output, dict):
            continue
        for items in output.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and Path(str(item.get("filename", ""))).suffix.lower() in (".mp4", ".webm", ".mov"):
                    refs.append({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                 "type": item.get("type", "output")})
    return refs
