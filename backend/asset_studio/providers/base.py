"""Provider contract.

Every generation/processing backend is a subclass of :class:`Provider`. The job
queue builds a :class:`JobContext`, calls ``provider.run(ctx)`` (in a worker
thread, since most providers block on subprocess/HTTP/PIL), and persists whatever
:class:`Asset` objects are returned.

Implementing a new provider is intentionally small:

    class MyProvider(Provider):
        id = "my-thing"
        name = "My Thing"
        stage = StageType.image2d
        kind = ProviderKind.api
        requires_key = True
        key_name = "mything"
        params = [ProviderParam(name="prompt", label="Prompt", type="text")]

        def is_available(self):
            return (self.has_key(), "Set the MyThing API key in Settings")

        def run(self, ctx):
            ctx.progress(0.1, "calling API")
            ...
            return [ctx.make_asset(path=out, type=AssetType.image, prompt=...)]

Then register it in ``providers/registry.py``.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

from .. import keychain
from ..models import (
    Asset,
    AssetType,
    Job,
    ProgressEvent,
    ProviderInfo,
    ProviderKind,
    ProviderParam,
    StageType,
    WSEventType,
)
from ..util import file_size, image_dims, is_image, is_mesh, make_thumbnail, mesh_info, slugify


class JobContext:
    """Everything a provider needs to do its work and report progress."""

    def __init__(
        self,
        job: Job,
        inputs: list[str],
        input_assets: list[Asset],
        workdir: Path,
        settings: dict[str, Any],
        emit: Callable[[ProgressEvent], None],
        is_canceled: Optional[Callable[[], bool]] = None,
    ):
        self.job = job
        self.params: dict[str, Any] = job.params
        self.inputs = inputs               # resolved absolute file paths
        self.input_assets = input_assets   # resolved Asset rows (subset of inputs)
        self.workdir = workdir
        self.settings = settings
        self._emit = emit
        self._is_canceled = is_canceled or (lambda: False)
        self._cost = 0.0
        self._assets: list[Asset] = []
        self._t0 = time.time()

    def canceled(self) -> bool:
        """True once the user cancels this job. Long-running provider loops (e.g.
        the ComfyUI poll) should check this and bail out promptly so Stop works."""
        try:
            return bool(self._is_canceled())
        except Exception:
            return False

    # --- input helpers -----------------------------------------------------
    def first_input(self) -> Optional[str]:
        return self.inputs[0] if self.inputs else None

    def first_image(self) -> Optional[str]:
        return next((p for p in self.inputs if is_image(p)), None)

    def first_mesh(self) -> Optional[str]:
        return next((p for p in self.inputs if is_mesh(p)), None)

    def param(self, name: str, default: Any = None) -> Any:
        v = self.params.get(name, default)
        return default if v is None else v

    def tool(self, name: str, default: Any = None) -> Any:
        return self.settings.get("tools", {}).get(name, default)

    # --- progress / logging ------------------------------------------------
    def progress(
        self,
        p: float,
        step: Optional[str] = None,
        eta: Optional[float] = None,
        message: Optional[str] = None,
    ) -> None:
        self.job.progress = max(0.0, min(1.0, float(p)))
        if step is not None:
            self.job.step = step
        # auto-estimate ETA from elapsed/progress when not provided
        if eta is None and 0.02 < self.job.progress < 0.99:
            elapsed = time.time() - self._t0
            eta = elapsed * (1 - self.job.progress) / max(self.job.progress, 1e-3)
        self.job.eta_seconds = eta
        self._emit(
            ProgressEvent(
                type=WSEventType.job_update,
                job_id=self.job.id,
                status=self.job.status,
                progress=self.job.progress,
                step=self.job.step,
                eta_seconds=eta,
                message=message,
            )
        )

    def log(self, message: str) -> None:
        self.job.logs.append(message)
        self._emit(
            ProgressEvent(type=WSEventType.job_log, job_id=self.job.id, message=message)
        )

    # --- cost --------------------------------------------------------------
    def add_cost(self, amount: float) -> None:
        self._cost += float(amount)
        self.job.cost = round(self._cost, 6)

    @property
    def cost(self) -> float:
        return round(self._cost, 6)

    # --- outputs -----------------------------------------------------------
    def out_path(self, filename: str) -> Path:
        self.workdir.mkdir(parents=True, exist_ok=True)
        return self.workdir / filename

    def make_asset(
        self,
        path: str | Path,
        type: AssetType,
        name: Optional[str] = None,
        prompt: str = "",
        seed: Optional[int] = None,
        meta: Optional[dict[str, Any]] = None,
        license: str = "",
        commercial_ok: Optional[bool] = None,
        preview_path: Optional[str] = None,
        parent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Asset:
        path = str(Path(path).resolve())
        meta = dict(meta or {})
        # auto-enrich metadata
        if is_image(path):
            w, h = image_dims(path)
            if w:
                meta.setdefault("width", w)
                meta.setdefault("height", h)
            if preview_path is None:
                thumb = self.out_path(f"{Path(path).stem}.thumb.png")
                preview_path = make_thumbnail(path, thumb) or path
        elif is_mesh(path):
            meta.update({k: v for k, v in mesh_info(path).items() if k not in meta})

        asset = Asset(
            name=name or Path(path).name,
            stage=self.job.stage,
            type=type,
            path=path,
            preview_path=preview_path,
            size_bytes=file_size(path),
            meta=meta,
            tags=tags if tags is not None else list(self.job.tags),
            target_game=self.job.target_game,
            provider_id=self.job.provider_id,
            prompt=prompt,
            seed=seed,
            license=license,
            commercial_ok=commercial_ok,
            cost=self.cost,
            job_id=self.job.id,
            parent_id=parent_id,
        )
        self._assets.append(asset)
        return asset


class Provider(ABC):
    """Base class for all stages' providers."""

    id: str = "unnamed"
    name: str = "Unnamed"
    stage: StageType = StageType.image2d
    kind: ProviderKind = ProviderKind.local
    requires_key: bool = False
    key_name: Optional[str] = None
    description: str = ""
    license_note: str = ""
    commercial_ok: Optional[bool] = None
    cost_hint: str = "free"
    homepage: str = ""
    params: list[ProviderParam] = []

    # --- availability ------------------------------------------------------
    def has_key(self) -> bool:
        return bool(self.key_name) and keychain.has_key(self.key_name)

    def is_available(self) -> tuple[bool, str]:
        """Return (available, reason_if_not). Override for tool/GPU detection."""
        if self.requires_key and not self.has_key():
            return False, f"Add the {self.name} API key in Settings → Providers."
        return True, ""

    def cached_is_available(self, ttl: float = 15.0) -> tuple[bool, str]:
        """is_available() with a short TTL cache so repeated provider-list calls
        don't re-run (potentially slow, network-pinging) availability checks."""
        cache = getattr(self, "_avail_cache", None)
        if cache and (time.time() - cache[0]) < ttl:
            return cache[1], cache[2]
        try:
            ok, reason = self.is_available()
        except Exception as e:  # noqa: BLE001
            ok, reason = False, f"availability check failed: {e}"
        self._avail_cache = (time.time(), ok, reason)
        return ok, reason

    def info(self) -> ProviderInfo:
        avail, reason = self.cached_is_available()
        return ProviderInfo(
            id=self.id,
            name=self.name,
            stage=self.stage,
            kind=self.kind,
            requires_key=self.requires_key,
            key_name=self.key_name,
            available=avail,
            available_reason=reason,
            description=self.description,
            license_note=self.license_note,
            commercial_ok=self.commercial_ok,
            cost_hint=self.cost_hint,
            homepage=self.homepage,
            params=self.params,
        )

    # --- execution ---------------------------------------------------------
    @abstractmethod
    def run(self, ctx: JobContext) -> list[Asset]:
        """Do the work; return produced assets. Use ctx.progress()/ctx.log()."""
        raise NotImplementedError

    def unload(self) -> None:
        """Release any cached model / GPU memory held between jobs. The idle
        GPU-memory reaper (:mod:`asset_studio.gpu_memory`) calls this after a
        period of inactivity. Default: nothing cached, nothing to do."""
        return None


# Re-exported for adapter convenience
__all__ = [
    "Provider",
    "JobContext",
    "ProviderParam",
    "ProviderKind",
    "StageType",
    "AssetType",
    "Asset",
    "slugify",
]
