"""Native MiniMax H3 text/image-to-video through the user's ComfyUI."""
from __future__ import annotations

import time

import httpx

from ...models import ProviderKind, ProviderParam, StageType
from ..base import Provider
from ..comfy_common import (
    ComfyUnloadMixin, comfy_available, comfy_base, fetch, new_client_id, submit, upload_image,
)
from .common import PROMPT_PARAMS, dimensions, save_video_asset, video_options, video_refs

MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_int8_convrot.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
TURBO_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"


def frame_count(duration):
    frames = max(5, round(duration * 24))
    return frames + (5 - frames % 17) % 17


def build_graph(prompt, duration, ratio, resolution, seed, steps=20, turbo=False, image=None):
    width, height = dimensions(ratio, resolution)
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": ENCODER, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "5": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0],
               "prompt": prompt, "width": width, "height": height, "length": frame_count(duration)}},
        "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "8": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "9": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},
        "10": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["6", 0], "guider": ["9", 0],
                "sampler": ["7", 0], "sigmas": ["8", 0], "latent_image": ["5", 1]}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
        "13": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "audio": ["12", 0], "fps": 24}},
        "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": "AssetStudio/MiniMaxH3",
                "format": "mp4", "format.codec": "h264", "format.codec.encoding": "auto"}},
    }
    if image:
        graph["15"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        graph["5"]["inputs"]["first_frame"] = ["15", 0]
    if turbo:
        graph["16"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": TURBO_LORA, "strength_model": 1.0}}
        graph["8"]["inputs"].update(model=["16", 0], steps=8)
        graph["9"]["inputs"]["model"] = ["16", 0]
    return graph


def wait_video(http, base, prompt_id, ctx, timeout=7200):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ctx.canceled():
            # Delete only our queued request; interrupt only if our request owns the sampler.
            http.post(f"{base}/queue", json={"delete": [prompt_id]})
            queue = http.get(f"{base}/queue").json()
            if any(len(row) > 1 and row[1] == prompt_id for row in queue.get("queue_running", [])):
                http.post(f"{base}/interrupt")
            raise RuntimeError("canceled")
        response = http.get(f"{base}/history/{prompt_id}")
        response.raise_for_status()
        entry = response.json().get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                messages = status.get("messages") or []
                detail = next((m[1].get("exception_message", "") for m in messages
                               if len(m) > 1 and m[0] == "execution_error" and isinstance(m[1], dict)), "")
                raise RuntimeError(f"ComfyUI H3 generation failed: {detail or 'check the ComfyUI log'}")
            if video_refs(entry):
                return entry
            if status.get("completed"):
                raise RuntimeError("ComfyUI completed without a video output.")
        ctx.progress(0.3, "ComfyUI is generating video and audio")
        time.sleep(2)
    raise RuntimeError("H3 generation timed out after two hours. Check the ComfyUI log.")


class MiniMaxH3Provider(ComfyUnloadMixin, Provider):
    # The mixin is the fix for the report: H3's UNET and its 32B Qwen3-VL encoder live inside
    # ComfyUI's process, so `unload()` has to ask ComfyUI to drop them (see comfy_common).
    id = "minimax-h3"
    name = "MiniMax H3 (local ComfyUI)"
    stage = StageType.video
    kind = ProviderKind.local
    description = "Text or a starting image to video with native audio. Runs in your local ComfyUI."
    license_note = "MiniMax H3 Community License; commercial local outputs require a MiniMax commercial license."
    cost_hint = "local GPU; no API charge"
    homepage = "https://docs.comfy.org/tutorials/video/minimax/minimax-h3"
    params = PROMPT_PARAMS + [
        ProviderParam(name="resolution", label="Resolution", type="select", options=["480P", "768P"], default="480P", group="Video"),
        ProviderParam(name="steps", label="Quality steps", type="int", default=20, min=10, max=40, group="Quality"),
        ProviderParam(name="turbo", label="Turbo (8 steps; lower quality)", type="bool", default=False, group="Quality"),
    ]

    def is_available(self):
        ok, reason = comfy_available(comfy_base())
        if not ok:
            return ok, reason
        with httpx.Client(timeout=15) as http:
            info = http.get(f"{comfy_base()}/object_info").json()
        if "MiniMaxH3ImageToVideo" not in info:
            return False, "Update ComfyUI to 0.30.0 or later for native H3 support."
        for node, field, model in [("UNETLoader", "unet_name", MODEL), ("CLIPLoader", "clip_name", ENCODER),
                                   ("VAELoader", "vae_name", VIDEO_VAE), ("VAELoader", "vae_name", AUDIO_VAE)]:
            choices = (info.get(node, {}).get("input", {}).get("required", {}).get(field) or [[]])[0]
            if model not in choices:
                return False, f"Missing H3 model file: {model}"
        return True, ""

    def run(self, ctx):
        prompt, duration, ratio, seed = video_options(ctx)
        steps = int(ctx.param("steps", 20))
        if not 10 <= steps <= 40:
            raise ValueError("Quality steps must be between 10 and 40.")
        base = comfy_base(ctx)
        resolution = str(ctx.param("resolution", "480P"))
        turbo = bool(ctx.param("turbo", False))
        with httpx.Client(timeout=120) as http:
            image_path = ctx.first_image()
            image = upload_image(http, base, image_path) if image_path else None
            graph = build_graph(prompt, duration, ratio, resolution, seed, steps, turbo, image)
            ctx.progress(0.1, "Submitting MiniMax H3 to ComfyUI")
            prompt_id = submit(http, base, graph, new_client_id())
            ctx.log(f"ComfyUI H3 request {prompt_id}; seed={seed}")
            entry = wait_video(http, base, prompt_id, ctx)
            ref = video_refs(entry)[-1]
            ctx.progress(0.95, "Saving video")
            data = fetch(http, base, ref)
        out = ctx.out_path("minimax-h3.mp4")
        out.write_bytes(data)
        width, height = dimensions(ratio, resolution)
        return save_video_asset(ctx, out, prompt, seed, {"engine": "comfyui", "model": "MiniMax H3",
            "duration": frame_count(duration) / 24, "fps": 24, "width": width, "height": height,
            "audio": True, "turbo": turbo}, self.license_note)
