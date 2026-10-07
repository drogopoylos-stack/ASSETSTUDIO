"""Local model-server management — start/stop/health/logs for the localhost
servers that power the free/local providers (ComfyUI, TRELLIS, Hunyuan3D, …)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import manager

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("")
def list_services():
    return manager.all_status()


@router.get("/for-provider/{provider_id}")
def service_for_provider(provider_id: str):
    spec = manager.service_for_provider(provider_id)
    if not spec:
        return {"service": None}
    return {"service": manager.status(spec.id)}


@router.post("/{sid}/start")
def start_service(sid: str, wait: bool = False, timeout: float = 90.0):
    if manager.spec(sid) is None:
        raise HTTPException(404, f"unknown service '{sid}'")
    if wait:
        ok, reason = manager.ensure(sid, timeout=timeout)
        st = manager.status(sid)
        st.last_error = "" if ok else reason
        return st
    return manager.start(sid)


@router.post("/{sid}/stop")
def stop_service(sid: str):
    if manager.spec(sid) is None:
        raise HTTPException(404, f"unknown service '{sid}'")
    return manager.stop(sid)


@router.post("/start-all")
def start_all(wait: bool = True, timeout: float = 90.0):
    out = []
    for spec in manager.specs():
        if not spec.command.strip():
            continue
        if wait:
            ok, reason = manager.ensure(spec.id, timeout=timeout)
            st = manager.status(spec.id)
            st.last_error = "" if ok else reason
        else:
            st = manager.start(spec.id)
        out.append(st)
    return out


@router.get("/{sid}/logs")
def service_logs(sid: str, lines: int = 120):
    if manager.spec(sid) is None:
        raise HTTPException(404, f"unknown service '{sid}'")
    return {"id": sid, "log": manager.log_tail(sid, lines)}


class ServicePatch(BaseModel):
    command: Optional[str] = None
    cwd: Optional[str] = None
    autostart: Optional[bool] = None
    health_url: Optional[str] = None
    port: Optional[int] = None
    name: Optional[str] = None


@router.put("/{sid}")
def update_service(sid: str, patch: ServicePatch):
    return manager.update(sid, patch.model_dump(exclude_none=True))
