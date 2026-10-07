"""Shared ComfyUI plumbing + a catalog of the current free local generators.

ComfyUI is the universal runner for the new free local models (TripoSplat,
Hunyuan3D, TRELLIS.2, TripoSG, SF3D for 3D; FLUX, Qwen-Image, SD3.5, SDXL for 2D).
We don't bundle the model weights — you install the relevant ComfyUI custom node +
weights once, then the studio drives your ComfyUI over its HTTP API. A *generic
workflow* provider runs ANY exported ComfyUI workflow (API format), so new models
are future-proof: drop the model into ComfyUI and the workflow into the studio.

This module holds: the ComfyUI HTTP client, a placeholder-injection helper (so the
studio can feed the prompt / seed / input image into your workflow), and CATALOG —
the curated list of generators surfaced as presets in the UI.
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..config import DATA_DIR, settings

# Where users drop exported ComfyUI workflows (API format), named "<preset-id>.json".
WORKFLOWS_DIR = (DATA_DIR / "comfy_workflows")
WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MESH_EXTS = (".glb", ".gltf", ".obj", ".ply", ".stl", ".fbx")


# ---------------------------------------------------------------------------
# Catalog of free local generators (2026). `kind`: "2d" | "3d" | "splat".
# These are surfaced as presets; availability still depends on the user having
# installed the matching ComfyUI custom node + weights.
# ---------------------------------------------------------------------------
CATALOG: list[dict] = [
    # ---- 3D ----
    {"id": "triposplat", "name": "TripoSplat — 3D Gaussian splats", "kind": "splat",
     "license": "MIT", "vram": "~8 GB", "node": "native in ComfyUI v0.23+ (Template Library)",
     "homepage": "https://github.com/VAST-AI-Research/TripoSplat",
     "notes": "Single image → 3D Gaussian splats. Great for stylized characters/props; very 8GB-friendly."},
    {"id": "triposr", "name": "TripoSR — fast mesh", "kind": "3d",
     "license": "MIT", "vram": "6–8 GB", "node": "ComfyUI-Flowty-TripoSR (or built-in studio provider)",
     "homepage": "https://github.com/VAST-AI-Research/TripoSR",
     "notes": "Single-image feed-forward mesh in ~seconds. The studio also has a native TripoSR provider."},
    {"id": "triposg", "name": "TripoSG — clean SDF mesh", "kind": "3d",
     "license": "MIT", "vram": "~8–10 GB", "node": "ComfyUI-3D-Pack",
     "homepage": "https://github.com/VAST-AI-Research/TripoSG",
     "notes": "High-quality watertight meshes from one image."},
    {"id": "hunyuan3d-21", "name": "Hunyuan3D 2.1 — best textures", "kind": "3d",
     "license": "Tencent (check commercial terms)", "vram": "~10–16 GB (mini lower)",
     "node": "ComfyUI-Hunyuan3DWrapper / ComfyUI-3D-Pack",
     "homepage": "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1",
     "notes": "Highest-fidelity PBR textures; heavier. A '2mini' variant fits smaller GPUs."},
    {"id": "trellis2", "name": "TRELLIS.2 — production PBR mesh", "kind": "3d",
     "license": "MIT", "vram": "~10–16 GB", "node": "ComfyUI-TRELLIS2",
     "homepage": "https://github.com/microsoft/TRELLIS",
     "notes": "Production-grade PBR GLB. The studio also has a native TRELLIS provider (hosted/local)."},
    {"id": "sf3d", "name": "Stable Fast 3D — fastest", "kind": "3d",
     "license": "Stability Community", "vram": "~6–7 GB", "node": "ComfyUI-3D-Pack",
     "homepage": "https://github.com/Stability-AI/stable-fast-3d",
     "notes": "Mesh in under a second; lower fidelity but unbeatable speed."},
    {"id": "hi3dgen", "name": "Hi3DGen — best geometry", "kind": "3d",
     "license": "MIT", "vram": "~10–16 GB", "node": "ComfyUI-3D-Pack",
     "homepage": "https://github.com/Stable-X/Hi3DGen",
     "notes": "Strongest geometric detail via normal-bridged generation."},
    # ---- 2D ----
    {"id": "sdxl-lightning", "name": "SDXL + Lightning (best 8GB baseline)", "kind": "2d",
     "license": "varies (checkpoint-dependent)", "vram": "6–8 GB", "node": "native",
     "homepage": "https://comfyui.org",
     "notes": "Add a Lightning LoRA for 4-step speed. The studio's built-in default workflow uses SDXL."},
    {"id": "flux", "name": "FLUX.1 (GGUF for 8GB)", "kind": "2d",
     "license": "schnell = Apache-2.0 · dev = non-commercial", "vram": "~8 GB with GGUF",
     "node": "native + ComfyUI-GGUF loader",
     "homepage": "https://github.com/black-forest-labs/flux",
     "notes": "Top-tier quality and prompt-following; use a GGUF/quantized build to fit 8GB."},
    {"id": "qwen-image", "name": "Qwen-Image (GGUF)", "kind": "2d",
     "license": "Apache-2.0", "vram": "~8 GB with GGUF", "node": "native",
     "homepage": "https://github.com/QwenLM/Qwen-Image",
     "notes": "Excellent text rendering inside images; GGUF build fits 8GB."},
    {"id": "sd35", "name": "Stable Diffusion 3.5", "kind": "2d",
     "license": "Stability Community", "vram": "~8–12 GB", "node": "native",
     "homepage": "https://stability.ai/news/introducing-stable-diffusion-3-5",
     "notes": "Strong general model; medium variant is friendlier to 8GB."},
    {"id": "pixart-sigma", "name": "PixArt-Sigma (tiny, <8GB)", "kind": "2d",
     "license": "open", "vram": "<8 GB", "node": "native / ComfyUI_ExtraModels",
     "homepage": "https://github.com/PixArt-alpha/PixArt-sigma",
     "notes": "Only 0.6B params — surprisingly good and very light."},
]

CATALOG_BY_ID = {c["id"]: c for c in CATALOG}


def catalog_for(kinds: tuple[str, ...]) -> list[dict]:
    return [c for c in CATALOG if c["kind"] in kinds]


# ---------------------------------------------------------------------------
# ComfyUI HTTP client
# ---------------------------------------------------------------------------
def comfy_base(ctx: Any = None) -> str:
    if ctx is not None:
        return str(ctx.tool("comfyui_url", "http://127.0.0.1:8188")).rstrip("/")
    return str((settings.get("tools") or {}).get("comfyui_url", "http://127.0.0.1:8188")).rstrip("/")


def comfy_available(base: Optional[str] = None) -> tuple[bool, str]:
    base = base or comfy_base()
    try:
        import httpx
        r = httpx.get(f"{base}/system_stats", timeout=1.5)
        if r.status_code == 200:
            return True, ""
    except Exception:
        pass
    return False, f"Start ComfyUI (set tools.comfyui_url in Settings — default {base})."


def load_preset_workflow(preset_id: str) -> Optional[dict]:
    """Load a workflow the user exported from ComfyUI (Save → API Format) and saved
    as <preset_id>.json under DATA_DIR/comfy_workflows/."""
    f = WORKFLOWS_DIR / f"{preset_id}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # accept either the bare API graph or a {"prompt": graph} wrapper
    if isinstance(data, dict) and "prompt" in data and isinstance(data["prompt"], dict):
        return data["prompt"]
    return data if isinstance(data, dict) else None


def inject(graph: dict, mapping: dict[str, Any]) -> dict:
    """Replace %token% placeholders anywhere in a workflow graph. Exact-match tokens
    are replaced with the typed value (e.g. an int seed); embedded tokens do string
    substitution. Lets the studio feed prompt/seed/image/size into any workflow."""
    g = copy.deepcopy(graph)

    def conv(v: Any) -> Any:
        if isinstance(v, str):
            if v in mapping:
                return mapping[v]
            s = v
            for k, rep in mapping.items():
                if k in s:
                    s = s.replace(k, str(rep))
            return s
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {kk: conv(vv) for kk, vv in v.items()}
        return v

    return conv(g)


def upload_image(http: Any, base: str, path: str) -> str:
    """Upload an input image to ComfyUI; return the name a LoadImage node should use."""
    p = Path(path)
    files = {"image": (p.name, p.read_bytes(), "image/png")}
    r = http.post(f"{base}/upload/image", files=files, data={"overwrite": "true"})
    r.raise_for_status()
    j = r.json()
    name = j.get("name") or p.name
    sub = j.get("subfolder", "")
    return f"{sub}/{name}" if sub else name


def submit(http: Any, base: str, graph: dict, client_id: str) -> str:
    r = http.post(f"{base}/prompt", json={"prompt": graph, "client_id": client_id})
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI /prompt {r.status_code}: {r.text[:400]}")
    j = r.json()
    if j.get("node_errors"):
        raise RuntimeError(f"ComfyUI workflow error: {json.dumps(j['node_errors'])[:400]}")
    pid = j.get("prompt_id")
    if not pid:
        raise RuntimeError("ComfyUI did not return a prompt_id.")
    return pid


def interrupt(http: Any, base: str) -> None:
    """Ask ComfyUI to abort whatever it's running (best-effort)."""
    try:
        http.post(f"{base}/interrupt", timeout=5.0)
    except Exception:
        pass


def wait(http: Any, base: str, prompt_id: str, ctx: Any, timeout: float = 900.0) -> dict:
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
        # Stop button / job cancel: tell ComfyUI to abort and bail out of the poll.
        if getattr(ctx, "canceled", None) and ctx.canceled():
            interrupt(http, base)
            raise RuntimeError("canceled")
        h = http.get(f"{base}/history/{prompt_id}")
        if h.status_code == 200:
            j = h.json()
            if prompt_id in j:
                entry = j[prompt_id]
                st = entry.get("status") or {}
                if st.get("status_str") == "error":
                    raise RuntimeError("ComfyUI reported an error — check the ComfyUI console.")
                if entry.get("outputs"):
                    return entry
        frac = (time.time() - start) / max(1.0, timeout)
        ctx.progress(min(0.9, 0.2 + frac * 0.7), "rendering in ComfyUI")
        time.sleep(1.0)
    raise RuntimeError("ComfyUI timed out.")


def collect_outputs(entry: dict) -> tuple[list[dict], list[dict]]:
    """Return (image_refs, file_refs) found anywhere in a history entry's outputs."""
    imgs: list[dict] = []
    files: list[dict] = []
    for _node_id, out in (entry.get("outputs") or {}).items():
        if not isinstance(out, dict):
            continue
        for _key, items in out.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict) or not it.get("filename"):
                    continue
                ref = {"filename": it["filename"], "subfolder": it.get("subfolder", ""),
                       "type": it.get("type", "output")}
                ext = Path(it["filename"]).suffix.lower()
                if ext in IMG_EXTS:
                    imgs.append(ref)
                elif ext in MESH_EXTS:
                    files.append(ref)
    return imgs, files


def fetch(http: Any, base: str, ref: dict) -> bytes:
    r = http.get(f"{base}/view", params={"filename": ref["filename"],
                 "subfolder": ref.get("subfolder", ""), "type": ref.get("type", "output")})
    r.raise_for_status()
    return r.content


def new_client_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Giving the memory back
# ---------------------------------------------------------------------------
#
# COMFYUI IS A SEPARATE PROCESS, SO ITS MODELS ARE NOT OURS TO FREE.
#
# Everything the Studio runs in-process (TripoSR, local Diffusers, the upscalers) caches its
# weights in THIS process's torch allocator, and `gpu_memory.free_now()` reaches them with
# `torch.cuda.empty_cache()`. ComfyUI keeps its UNET, text encoder and VAEs in ITS allocator, in
# another process, so that call cannot touch one byte of it — and until this section existed
# nothing else ever asked. Measured on this PC, 2026-10-05, right after one MiniMax H3 video, with
# ComfyUI's queue EMPTY:
#
#     before /free:  13.8 GB of 16 GB VRAM in use  ·  ComfyUI private 30.8 GB  ·  5.8 GB free RAM
#     after  /free:   0.9 GB of 16 GB VRAM in use  ·  ComfyUI private  4.2 GB  · 21.0 GB free RAM
#
# That is the whole report: the video finished, ComfyUI kept its model cache warm on purpose (it is
# what makes the NEXT render fast), and the Studio's idle reaper had nothing to unload — so a 32B
# text encoder sat in host RAM and the video model in VRAM with nothing coming to collect them.
# The 32B encoder is why RAM, not just VRAM, is the number that stays high after a video.
#
# `/free` is ComfyUI's own "Unload models" + "free memory" — the two checkboxes behind its
# free-model-cache button — so this is its supported request, not a shortcut through its guts.

def comfy_memory(base: Optional[str] = None) -> Optional[dict]:
    """ComfyUI's own view of the machine — device VRAM and host RAM. None when it is down."""
    try:
        import httpx

        r = httpx.get(f"{(base or comfy_base()).rstrip('/')}/system_stats", timeout=3.0)
        if r.status_code != 200:
            return None
        j = r.json() or {}
        dev = (j.get("devices") or [{}])[0]
        host = j.get("system") or {}
        return {"vram_total": float(dev.get("vram_total") or 0),
                "vram_free": float(dev.get("vram_free") or 0),
                "ram_free": float(host.get("ram_free") or 0)}
    except Exception:
        return None


def free_comfy(base: Optional[str] = None, *, force: bool = False) -> dict:
    """Ask ComfyUI to unload its models and empty its caches. Never raises.

    Two guards, both deliberate:

    * a RUNNING prompt is never disturbed, not even by ``force`` — unloading the model under a
      render that is mid-sampler breaks it, and the right answer there is to wait. (The reaper
      already withholds this whole call while one of the Studio's own jobs is in flight; this
      covers prompts somebody started in ComfyUI's own window.)
    * when the card already reports itself empty (>= 85% free) the POST is skipped. VRAM and host
      RAM are released together — after the measurement above the card read 91% free and ComfyUI's
      private memory was back at its 4.2 GB baseline — so one number answers both, and this keeps
      the reaper's periodic call to two cheap GETs. ``force`` bypasses only THIS check.

    Returns ``{"ok": True, "freed": True, before, after}``, ``{"ok": True, "skipped": ...}`` or
    ``{"ok": False, "error": ...}``.
    """
    base = (base or comfy_base()).rstrip("/")
    before = comfy_memory(base)
    if before is None:
        return {"ok": False, "error": "ComfyUI is not reachable"}
    try:
        import httpx

        with httpx.Client(timeout=15.0) as http:
            running = (http.get(f"{base}/queue").json() or {}).get("queue_running") or []
            if running:
                return {"ok": True, "skipped": "ComfyUI is running a prompt", "before": before}
            total = before.get("vram_total") or 0
            if not force and total and before.get("vram_free", 0) >= 0.85 * total:
                return {"ok": True, "skipped": "ComfyUI holds nothing", "before": before}
            r = http.post(f"{base}/free", json={"unload_models": True, "free_memory": True})
            if r.status_code != 200:
                return {"ok": False, "before": before,
                        "error": f"ComfyUI /free {r.status_code}: {r.text[:200]}"}
    except Exception as e:  # noqa: BLE001 - a release attempt must never break a job or the reaper
        return {"ok": False, "before": before, "error": f"ComfyUI /free failed: {e}"}
    return {"ok": True, "freed": True, "before": before, "after": comfy_memory(base)}


class ComfyUnloadMixin:
    """A real ``unload()`` for every provider whose model lives inside ComfyUI's process.

    ``gpu_memory.free_now()`` walks the provider registry and calls this after an idle period and
    from the UI's *free now*. Without it that walk did nothing at all for the ComfyUI family — only
    TripoSR implemented ``unload`` — which is why a finished video left 13.8 GB of VRAM and 30.8 GB
    of private memory standing until ComfyUI was restarted.

    Several ComfyUI-backed providers share one server, so the reaper may call this a few times in a
    row; the next call finds the card empty and returns without a POST.
    """

    def unload(self) -> None:
        try:
            free_comfy()
        except Exception:  # noqa: BLE001 - a failed release is never worth failing a job for
            pass
