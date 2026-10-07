"""MiniMax H3 Max via fal's supported queued API; keys stay on the backend."""
from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ... import keychain
from ...models import ProviderKind, ProviderParam, StageType
from ..base import Provider
from .common import PROMPT_PARAMS, save_video_asset, video_options

QUEUE_BASE = "https://queue.fal.run"
MODEL = "minimax/h3-max"


def queue_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "queue.fal.run":
        raise RuntimeError("fal returned an invalid queue URL.")
    return url


def image_data_uri(path):
    path = Path(path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("The starting image must be smaller than 20 MB.")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


class MiniMaxH3MaxProvider(Provider):
    id = "minimax-h3-max"
    name = "MiniMax H3 Max (fal API)"
    stage = StageType.video
    kind = ProviderKind.api
    requires_key = True
    key_name = "fal"
    description = "Generate video with native audio using fal's H3 Max. Requires a fal API key and paid credits."
    license_note = "fal API and MiniMax H3 Max terms apply."
    cost_hint = "paid fal API; billed by resolution and duration"
    homepage = "https://fal.ai/models/minimax/h3-max/text-to-video"
    params = PROMPT_PARAMS + [
        ProviderParam(name="resolution", label="Resolution", type="select", options=["480P", "768P", "1080P"], default="768P", group="Video"),
        ProviderParam(name="prompt_expansion_mode", label="Prompt expansion", type="select",
                      options=["disabled", "balanced", "quality"], default="disabled", group="Prompt",
                      description="Disabled sends your exact prompt; quality asks fal to expand it."),
    ]

    def run(self, ctx):
        prompt, duration, ratio, seed = video_options(ctx)
        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Add your fal API key in Settings > API keys to use H3 Max.")
        resolution = str(ctx.param("resolution", "768P"))
        expansion = str(ctx.param("prompt_expansion_mode", "disabled"))
        if resolution not in ("480P", "768P", "1080P") or expansion not in ("disabled", "balanced", "quality"):
            raise ValueError("Choose a supported resolution and prompt expansion mode.")
        payload = {"prompt": prompt, "duration": duration, "aspect_ratio": ratio,
                   "resolution": resolution, "seed": seed, "prompt_expansion_mode": expansion}
        image = ctx.first_image()
        mode = "image-to-video" if image else "text-to-video"
        if image:
            payload["image_url"] = image_data_uri(image)
        if ctx.canceled():
            raise RuntimeError("canceled")
        with httpx.Client(timeout=120) as http:
            # Attach credentials only to fal's queue, never to downloaded output files.
            headers = {"Authorization": f"Key {key}"}
            ctx.progress(0.1, "Submitting to H3 Max (paid fal API)")
            response = http.post(f"{QUEUE_BASE}/{MODEL}/{mode}", headers=headers, json=payload)
            response.raise_for_status()
            request = response.json()
            request_id = request["request_id"]
            status_url = queue_url(request["status_url"])
            result_url = queue_url(request["response_url"])
            cancel_url = queue_url(request["cancel_url"])
            ctx.log(f"fal H3 Max request {request_id}")
            deadline = time.monotonic() + 1800
            try:
                while time.monotonic() < deadline:
                    if ctx.canceled():
                        raise RuntimeError("canceled")
                    response = http.get(status_url, headers=headers)
                    response.raise_for_status()
                    state = response.json().get("status")
                    if state == "COMPLETED":
                        break
                    if state not in ("IN_QUEUE", "IN_PROGRESS"):
                        raise RuntimeError(f"H3 Max request ended with status: {state}")
                    ctx.progress(0.3 if state == "IN_QUEUE" else 0.65,
                                 "Waiting in fal queue" if state == "IN_QUEUE" else "H3 Max is generating video")
                    time.sleep(2)
                else:
                    raise RuntimeError("H3 Max timed out after 30 minutes.")
            except Exception:
                try:
                    http.put(cancel_url, headers=headers)
                except Exception:
                    pass
                raise
            result = http.get(result_url, headers=headers)
            result.raise_for_status()
            data = result.json()
            video = data.get("video") or {}
            url = video.get("url", "")
            if urlparse(url).scheme != "https":
                raise RuntimeError("H3 Max returned no downloadable video.")
            if ctx.canceled():
                raise RuntimeError("canceled")
            ctx.progress(0.95, "Downloading video")
            output = http.get(url, follow_redirects=True)
            output.raise_for_status()
        out = ctx.out_path("minimax-h3-max.mp4")
        out.write_bytes(output.content)
        return save_video_asset(ctx, out, prompt, seed, {"engine": "fal", "model": "MiniMax H3 Max",
            "request_id": request_id, "duration": duration, "resolution": resolution,
            "aspect_ratio": ratio, "audio": True, "expanded_prompt": data.get("expanded_prompt")}, self.license_note)
