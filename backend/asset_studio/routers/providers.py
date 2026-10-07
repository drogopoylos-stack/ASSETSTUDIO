"""Provider discovery + runtime custom-provider management ("add a new AI")."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..providers import registry

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
def list_providers(stage: Optional[str] = None):
    registry.warm_availability()  # parallel + cached; fast when already warm
    provs = registry.providers_for(stage) if stage else registry.all_providers()
    return [p.info() for p in provs]


@router.get("/errors")
def load_errors():
    return registry.LOAD_ERRORS


@router.post("/reload")
def reload_providers():
    registry.reload()
    return {"ok": True, "count": len(registry.all_providers()), "errors": registry.LOAD_ERRORS}


@router.get("/comfy-catalog")
def comfy_catalog():
    """Curated free local 2D/3D generators with LIVE install status: native studio
    providers (e.g. TripoSR) are detected via availability; ComfyUI models are
    'installed' once their workflow is saved and ComfyUI is running."""
    from ..providers.comfy_common import CATALOG, WORKFLOWS_DIR, comfy_available
    comfy_ok = comfy_available()[0]
    native = {"triposr": "triposr"}  # catalog id -> a native studio provider id
    out = []
    for c in CATALOG:
        item = dict(c)
        installed = False
        label = "Not installed"
        detail = "Install its ComfyUI node, then save the workflow to enable."
        nid = native.get(c["id"])
        if nid:
            p = registry.get_provider(nid)
            if p:
                avail, reason = p.cached_is_available()
                if avail:
                    installed, label, detail = True, "Installed", "Native studio provider - ready to use."
                else:
                    label, detail = "Available (not set up)", reason
        wf = WORKFLOWS_DIR / f"{c['id']}.json"
        if wf.exists():
            if comfy_ok:
                installed, label, detail = True, "Installed", "Workflow saved and ComfyUI is running."
            else:
                label, detail = "Workflow saved", "Workflow is saved - start ComfyUI to use it."
        elif not installed and not nid and comfy_ok:
            label, detail = "ComfyUI up - add workflow", "ComfyUI is running; save this model's workflow JSON to enable."
        item.update({"installed": installed, "status_label": label, "detail": detail})
        out.append(item)
    return {"generators": out, "comfyui": comfy_ok}


@router.get("/{provider_id}")
def get_provider(provider_id: str):
    p = registry.get_provider(provider_id)
    if not p:
        raise HTTPException(404, "provider not found")
    return p.info()


# --- custom providers ------------------------------------------------------
class CustomSpec(BaseModel):
    id: str
    name: str
    stage: str = "image2d"
    kind: str = "api"
    endpoint: str
    method: str = "POST"
    headers: dict[str, Any] = {}
    body: dict[str, Any] = {}
    query: dict[str, Any] = {}
    output: dict[str, Any] = {"mode": "binary"}
    result_type: str = "image"
    requires_key: bool = True
    key_name: Optional[str] = None
    cost_per_call: float = 0.0
    license_note: str = ""
    commercial_ok: Optional[bool] = None
    homepage: str = ""
    description: str = ""
    params: list[dict[str, Any]] = []


@router.get("/custom/list")
def list_custom():
    return settings.get("custom_providers", []) or []


@router.post("/custom")
def upsert_custom(spec: CustomSpec):
    specs = list(settings.get("custom_providers", []) or [])
    specs = [s for s in specs if s.get("id") != spec.id]
    specs.append(spec.model_dump())
    settings.update({"custom_providers": specs})
    registry.reload()
    p = registry.get_provider(spec.id)
    return {"ok": True, "provider": p.info() if p else None, "errors": registry.LOAD_ERRORS}


@router.delete("/custom/{provider_id}")
def delete_custom(provider_id: str):
    specs = [s for s in (settings.get("custom_providers", []) or []) if s.get("id") != provider_id]
    settings.update({"custom_providers": specs})
    registry.reload()
    return {"ok": True}
