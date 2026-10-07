"""Idle GPU-memory reaper.

The native GPU providers (TripoSR, local Diffusers, upscalers) cache their model
weights in VRAM after a job so the *next* run is instant. That's great on a big
card but wasteful on an 8 GB GPU, where the cached models can sit on ~4-5 GB of
VRAM long after the job finished.

This module drops those caches + empties the CUDA allocator after a short idle
period (``gpu_idle_release_seconds``), and exposes a manual "free now" for the UI.
Idle VRAM falls back to roughly the bare CUDA context (~0.3 GB) instead of holding
whole models hostage.

It is deliberately torch-optional: nothing here imports torch eagerly, so a
CPU-only install pays no cost and never errors.

COMFYUI IS NOT IN THIS PROCESS, so `torch.cuda.empty_cache()` is not enough. The video, image and
3D presets run inside a ComfyUI server of their own; its models sit in ITS allocator and it keeps
them cached between prompts by design. `providers.comfy_common.free_comfy` is the only thing that
can give that memory back, so the idle loop asks it directly as well (a provider's `unload()`
cannot cover a ComfyUI that was left loaded by a *previous* Studio session, because this process
then never marks anything dirty).
"""
from __future__ import annotations

import gc
import sys
import threading
import time

from .config import settings

_LOCK = threading.Lock()
_last_activity = 0.0
_busy = 0           # in-flight GPU jobs; never release while > 0
_dirty = False      # a job ran since the last free -> something may be cached
_started = False
# The ComfyUI probe is throttled separately from the release: it is a couple of localhost GETs
# that answer "nothing loaded" in the common case, and it runs at most once per idle window.
_comfy_probe_at = 0.0
_comfy_probed_at_startup = False
_COMFY_PROBE_EVERY = 30.0


# --- activity tracking (called by the job queue) ---------------------------
def note_busy_start() -> None:
    global _busy
    with _LOCK:
        _busy += 1


def note_busy_end() -> None:
    global _busy, _last_activity, _dirty
    with _LOCK:
        _busy = max(0, _busy - 1)
        _last_activity = time.time()
        _dirty = True


def _idle_seconds() -> float:
    try:
        return float(settings.get("gpu_idle_release_seconds", 90) or 0)
    except Exception:
        return 90.0


def _torch():
    """Return the torch module only if it's already imported (i.e. the GPU was
    actually used). Never imports torch just to check."""
    return sys.modules.get("torch")


def _vram_used():
    t = _torch()
    if t is None:
        return None
    try:
        if t.cuda.is_available():
            free, total = t.cuda.mem_get_info()
            return total - free
    except Exception:
        return None
    return None


def vram_status() -> dict:
    """Device VRAM snapshot (matches nvidia-smi) + this process's torch usage."""
    t = _torch()
    if t is None:
        return {"cuda": False, "torch_loaded": False, "idle_release_seconds": _idle_seconds()}
    try:
        if not t.cuda.is_available():
            return {"cuda": False, "torch_loaded": True, "idle_release_seconds": _idle_seconds()}
        free, total = t.cuda.mem_get_info()
        return {
            "cuda": True,
            "torch_loaded": True,
            "device": t.cuda.get_device_name(0),
            "total_mb": round(total / 1e6),
            "free_mb": round(free / 1e6),
            "used_mb": round((total - free) / 1e6),
            "torch_allocated_mb": round(t.cuda.memory_allocated() / 1e6),
            "torch_reserved_mb": round(t.cuda.memory_reserved() / 1e6),
            "idle_release_seconds": _idle_seconds(),
        }
    except Exception:
        return {"cuda": False, "torch_loaded": True, "idle_release_seconds": _idle_seconds()}


def free_now(reason: str = "manual", force: bool = False) -> dict:
    """Ask every provider to drop cached models, then empty the CUDA allocator.

    ``unload()`` is where the ComfyUI-backed providers hand the memory BACK to their server — it
    lives in another process, so the two lines at the end of this function free nothing of theirs.

    Skips the model-unload step while a job is in flight (unless ``force``), so a
    manual click can't yank a model out from under a running generation.
    """
    global _dirty
    with _LOCK:
        busy = _busy
    before = _vram_used()
    unloaded = False
    if busy == 0 or force:
        try:
            from .providers.registry import all_providers
            for p in all_providers():
                try:
                    p.unload()
                except Exception:
                    pass
        except Exception:
            pass
        with _LOCK:
            _dirty = False
        unloaded = True
    gc.collect()
    t = _torch()
    if t is not None:
        try:
            if t.cuda.is_available():
                t.cuda.empty_cache()
                t.cuda.ipc_collect()
        except Exception:
            pass
    after = _vram_used()
    st = vram_status()
    st.update(
        ok=True,
        reason=reason,
        unloaded=unloaded,
        freed_mb=(round(max(0.0, (before - after)) / 1e6, 1) if (before and after) else None),
    )
    return st


def _idle_pass(now: float) -> None:
    """One tick of the reaper, at time ``now``. Split out from the sleep loop so the POLICY is
    testable without waiting ninety seconds for a thread."""
    global _comfy_probe_at, _comfy_probed_at_startup
    idle = _idle_seconds()
    if idle <= 0:
        return
    with _LOCK:
        busy, dirty, last = _busy, _dirty, _last_activity
    if busy or (now - last) < idle:
        return
    # Ask ComfyUI for its share. Once per idle window, plus once shortly after startup — a ComfyUI
    # left loaded by a PREVIOUS Studio session has this process's `_dirty` still False, so the
    # release below would never fire and its 30 GB would stand all day.
    if (dirty or not _comfy_probed_at_startup) and (now - _comfy_probe_at) >= _COMFY_PROBE_EVERY:
        _comfy_probe_at = now
        _comfy_probed_at_startup = True
        try:
            from .providers.comfy_common import free_comfy

            free_comfy("idle")
        except Exception:
            pass
    if dirty:
        try:
            free_now("idle")
        except Exception:
            pass


def _loop() -> None:
    while True:
        time.sleep(15.0)
        _idle_pass(time.time())


def _ensure_started() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()


_ensure_started()
