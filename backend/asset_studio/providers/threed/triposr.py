"""TripoSR — fast local single-image → 3D mesh (Stability AI / VAST).

Runs on this machine's RTX 2070 SUPER (~6 GB VRAM) — far lighter than TRELLIS.
First run downloads ~1.7 GB of weights from HuggingFace.

TripoSR normally needs ``torchmcubes`` (a CUDA/C++ extension that won't build here
without MSVC). We transparently shim it with the pure-Python ``PyMCubes`` package,
so no compiler is required.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np

from ...config import BACKEND_DIR
from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

_REPO = BACKEND_DIR / "third_party" / "TripoSR"
_STATE: dict = {"tsr": None, "rembg": None}


def _install_torchmcubes_shim():
    """Provide a `torchmcubes` module backed by PyMCubes (no compilation)."""
    if "torchmcubes" in sys.modules:
        return
    import mcubes
    import torch

    mod = types.ModuleType("torchmcubes")

    def marching_cubes(vol, thresh=0.0):
        arr = vol.detach().cpu().numpy().astype(np.float32)
        v, f = mcubes.marching_cubes(arr, float(thresh))
        return (torch.from_numpy(np.ascontiguousarray(v)).float(),
                torch.from_numpy(np.ascontiguousarray(f.astype(np.int64))))

    mod.marching_cubes = marching_cubes
    sys.modules["torchmcubes"] = mod


def _load_model():
    if _STATE["tsr"] is not None:
        return _STATE["tsr"]
    _install_torchmcubes_shim()
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    import torch
    from tsr.system import TSR

    model = TSR.from_pretrained(
        "stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt"
    )
    model.renderer.set_chunk_size(8192)
    model.to("cuda" if torch.cuda.is_available() else "cpu")
    _STATE["tsr"] = model
    return model


class TripoSRProvider(Provider):
    id = "triposr"
    name = "TripoSR (local image→3D, 8 GB-friendly)"
    stage = StageType.gen3d
    kind = ProviderKind.local
    requires_key = False
    cost_hint = "free"
    commercial_ok = True
    description = ("Fast local single-image → 3D mesh on your GPU (Stability TripoSR). "
                   "First run downloads ~1.7 GB weights. Best with a clean single-object image.")
    license_note = "TripoSR model is MIT-licensed; verify commercial terms for shipped assets."
    homepage = "https://github.com/VAST-AI-Research/TripoSR"
    params = [
        ProviderParam(name="remove_bg", label="Remove background", type="bool", default=True),
        ProviderParam(name="foreground_ratio", label="Foreground ratio", type="float",
                      default=0.85, min=0.5, max=1.0, step=0.05),
        ProviderParam(name="mc_resolution", label="Mesh resolution", type="select",
                      options=["128", "256", "320"], default="256"),
    ]

    def is_available(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401
        except Exception:
            return False, "Local 3D needs PyTorch (pip install torch --index-url …/cu124)."
        if not (_REPO / "tsr").exists():
            return False, "TripoSR not installed (clone into backend/third_party/TripoSR)."
        try:
            import mcubes  # noqa: F401
        except Exception:
            return False, "pip install PyMCubes (mesh extraction without a compiler)."
        return True, ""

    def unload(self) -> None:
        """Drop the cached TripoSR model + rembg session so idle VRAM returns to ~0."""
        had = _STATE.get("tsr") is not None or _STATE.get("rembg") is not None
        _STATE["tsr"] = None
        _STATE["rembg"] = None
        if had:
            import gc
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    def run(self, ctx: JobContext) -> list:
        import torch
        from PIL import Image

        image_path = ctx.first_image()
        if not image_path:
            raise RuntimeError("TripoSR needs an input image (a clean single object works best).")

        ctx.progress(0.1, "loading TripoSR (first run downloads ~1.7GB)")
        model = _load_model()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        from tsr.utils import remove_background, resize_foreground

        img = Image.open(image_path)
        ctx.progress(0.3, "preprocessing image")
        if bool(ctx.param("remove_bg", True)):
            try:
                import rembg

                if _STATE["rembg"] is None:
                    _STATE["rembg"] = rembg.new_session()
                img = remove_background(img.convert("RGB"), _STATE["rembg"])
            except Exception as e:
                ctx.log(f"rembg unavailable ({e}); using image as-is")
                img = img.convert("RGBA")
        else:
            img = img.convert("RGBA")

        img = resize_foreground(img, float(ctx.param("foreground_ratio", 0.85)))
        arr = np.array(img).astype(np.float32) / 255.0
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
        img = Image.fromarray((arr * 255).astype(np.uint8))

        ctx.progress(0.45, f"running TripoSR on {device}")
        with torch.no_grad():
            scene_codes = model([img], device=device)
        ctx.progress(0.75, "extracting mesh")
        res = int(ctx.param("mc_resolution", 256))
        meshes = model.extract_mesh(scene_codes, has_vertex_color=True, resolution=res)
        mesh = meshes[0]

        out = ctx.out_path(f"triposr-{ctx.job.id}.glb")
        mesh.export(str(out))

        ctx.progress(0.9, "rendering preview")
        preview = None
        try:
            from ...render.software_render import render_views

            frames = render_views(out, n=1, size=384)
            ppath = ctx.out_path(f"triposr-{ctx.job.id}.preview.png")
            frames[0].save(ppath, "PNG")
            preview = str(ppath)
        except Exception as e:
            ctx.log(f"preview failed: {e}")

        parent = ctx.input_assets[0].id if ctx.input_assets else None
        return [
            ctx.make_asset(
                path=out, type=AssetType.model, preview_path=preview, parent_id=parent,
                meta={"engine": "triposr", "mc_resolution": res, "polys": int(len(mesh.faces))},
                license=self.license_note, commercial_ok=True, tags=["triposr", "image-to-3d"],
            )
        ]
