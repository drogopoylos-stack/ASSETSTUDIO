"""Provider registry — the single place every backend is wired in.

Imports are *guarded*: if an adapter module fails to import (missing optional
dependency, syntax error in a freshly-added file, etc.) it is skipped and the
reason recorded in :data:`LOAD_ERRORS` instead of crashing the whole app. This
keeps the studio usable even when only some backends are installed.

To add a new AI: drop a ``Provider`` subclass in the appropriate sub-package. That
is enough — :func:`_discover` finds it. ``_SPECS`` below is the DISPLAY ORDER, not
the gate: list a provider there to place it, leave it out and it is appended.
(The UI also lets you register simple HTTP providers at runtime — see
routers/providers.py.)
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Optional

from .base import Provider

# sub-packages scanned for provider modules (one per pipeline stage)
_STAGE_DIRS = ("image", "video", "process", "threed", "texture", "rig", "optimize", "qa")

# (module path relative to this package, class name)
_SPECS: list[tuple[str, str]] = [
    ("video.minimax_h3", "MiniMaxH3Provider"),
    ("video.minimax_h3_max", "MiniMaxH3MaxProvider"),
    # --- 2D image ---------------------------------------------------------
    ("image.placeholder", "PlaceholderImageProvider"),
    ("image.comfyui", "ComfyUIImageProvider"),
    ("image.comfy_workflow", "ComfyWorkflowImageProvider"),
    ("image.local_diffusers", "DiffusersImageProvider"),
    ("image.nanobanana", "NanoBananaProvider"),
    ("image.openai_image", "OpenAIImageProvider"),
    # --- 2D processing ----------------------------------------------------
    ("process.rembg_provider", "RembgProvider"),
    ("process.sam2_cutout", "Sam2CutoutProvider"),
    ("process.upscale", "RealESRGANProvider"),
    ("process.slicer", "SpriteSlicerProvider"),
    ("process.atlas", "AtlasPackProvider"),
    ("process.webp", "WebpProvider"),
    # --- 3D generation ----------------------------------------------------
    ("threed.placeholder3d", "Placeholder3DProvider"),
    ("threed.arrow", "MedievalArrowProvider"),
    ("threed.comfy_workflow", "ComfyWorkflow3DProvider"),
    ("threed.triposr", "TripoSRProvider"),
    ("threed.trellis", "TrellisProvider"),
    ("threed.hunyuan3d", "Hunyuan3DProvider"),
    ("threed.tripo", "TripoProvider"),
    ("threed.meshy", "MeshyProvider"),
    # same key, second recipe: three calls (sculpt → adaptive remesh → 4K texture).
    # Kept beside the original so the two can be compared on the same reference image.
    ("threed.meshy_pipeline", "MeshyPipelineProvider"),
    # --- texturing --------------------------------------------------------
    ("texture.trellis_texture", "TrellisTextureProvider"),
    ("texture.hunyuan_paint", "HunyuanPaintProvider"),
    ("texture.tripo_texture", "TripoTextureProvider"),
    ("texture.meshy_texture", "MeshyTextureProvider"),
    # --- rigging ----------------------------------------------------------
    ("rig.blender_rig", "BlenderRigProvider"),
    ("rig.unirig", "UniRigProvider"),
    ("rig.tripo_rig", "TripoRigProvider"),
    ("rig.mixamo", "MixamoProvider"),
    # --- optimize ---------------------------------------------------------
    ("optimize.gltf_transform", "GltfTransformProvider"),
    # --- QA ---------------------------------------------------------------
    ("qa.turntable", "TurntableProvider"),
]

_REGISTRY: dict[str, Provider] = {}
LOAD_ERRORS: dict[str, str] = {}


def _load() -> None:
    _REGISTRY.clear()
    LOAD_ERRORS.clear()
    for mod, cls in _SPECS:
        full = f"{__package__}.{mod}"
        try:
            module = importlib.import_module(full)
            provider_cls = getattr(module, cls)
            inst: Provider = provider_cls()
            if inst.id in _REGISTRY:
                LOAD_ERRORS[f"{mod}.{cls}"] = f"duplicate id '{inst.id}'"
                continue
            _REGISTRY[inst.id] = inst
        except Exception as e:  # noqa: BLE001 - guarded on purpose
            LOAD_ERRORS[f"{mod}.{cls}"] = f"{type(e).__name__}: {e}"
    # anything on disk that _SPECS does not mention
    _discover()
    # runtime, user-defined providers persisted in settings
    _load_custom()


def _discover() -> None:
    """Register provider modules that are NOT in ``_SPECS``.

    Adding a provider used to mean editing this file, which meant a rebuild and a restart of a
    packaged app for what is otherwise a drop-in file. Now the stage folders are scanned and any
    ``Provider`` subclass DEFINED there (``__module__`` must match, so a class merely imported from
    a sibling is not registered twice) is instantiated. Same guard as ``_load``: one bad file
    records a reason and never breaks the rest."""
    here = Path(__file__).resolve().parent
    listed = {mod for mod, _cls in _SPECS}
    for sub in _STAGE_DIRS:
        d = here / sub
        if not d.is_dir():
            continue
        for found in pkgutil.iter_modules([str(d)]):
            rel = f"{sub}.{found.name}"
            if rel in listed or found.name.startswith("_"):
                continue
            full = f"{__package__}.{rel}"
            try:
                module = importlib.import_module(full)
                for _name, obj in inspect.getmembers(module, inspect.isclass):
                    if (issubclass(obj, Provider) and obj is not Provider
                            and obj.__module__ == full and getattr(obj, "id", "")):
                        inst: Provider = obj()
                        if inst.id not in _REGISTRY:
                            _REGISTRY[inst.id] = inst
            except Exception as e:  # noqa: BLE001 - guarded on purpose
                LOAD_ERRORS[rel] = f"{type(e).__name__}: {e}"


def _load_custom() -> None:
    try:
        from .custom import build_custom_providers

        for inst in build_custom_providers():
            _REGISTRY[inst.id] = inst
    except Exception as e:  # noqa: BLE001
        LOAD_ERRORS["custom"] = f"{type(e).__name__}: {e}"


def reload() -> None:
    _load()


def warm_availability(ttl: float = 15.0, per_check_timeout: float = 4.0) -> None:
    """Compute every provider's availability concurrently (network pings to local
    model servers can each take seconds) and cache the results, so /api/providers
    stays fast. Safe to call repeatedly — fresh cache entries are reused."""
    import time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

    provs = list(_REGISTRY.values())
    with ThreadPoolExecutor(max_workers=min(16, len(provs) or 1)) as ex:
        futs = {ex.submit(p.cached_is_available, ttl): p for p in provs}
        for fut, p in futs.items():
            try:
                fut.result(timeout=per_check_timeout)
            except (FTimeout, Exception):  # noqa: BLE001
                p._avail_cache = (time.time(), False, "availability check timed out")


def all_providers() -> list[Provider]:
    return list(_REGISTRY.values())


def providers_for(stage: str) -> list[Provider]:
    return [p for p in _REGISTRY.values() if p.stage.value == stage]


def get_provider(provider_id: str) -> Optional[Provider]:
    return _REGISTRY.get(provider_id)


_load()
