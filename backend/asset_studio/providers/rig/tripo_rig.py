"""Tripo Auto-Rig — Tripo3D's hosted humanoid auto-rigging API.

Tripo's rig/animation tasks operate on a model that Tripo already generated: they
take the *task id* of the original model rather than an uploaded file. So this
provider expects the input asset to carry Tripo's task id in its metadata (the
Tripo gen3d provider stores it as ``meta['tripo_task_id']`` / ``meta['task_id']``).
If that id is missing, the base mesh must first be created with the Tripo provider.

Flow:

1. ``POST {base}/task`` with ``{"type": "animate_rig", "original_model_task_id": <id>,
   "out_format": "glb"}`` -> returns a new task id.
2. Poll ``GET {base}/task/{id}`` until ``status`` is ``success`` (or failure).
3. Download the rigged GLB from the result and register it as a model asset.

Base URL ``https://api.tripo3d.ai/v2/openapi``; auth is ``Authorization: Bearer <key>``.
"""
from __future__ import annotations

import time
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

_BASE = "https://api.tripo3d.ai/v2/openapi"


class TripoRigProvider(Provider):
    """Hosted humanoid auto-rig via Tripo3D (needs a Tripo-generated base mesh)."""

    id = "tripo-rig"
    name = "Tripo Auto-Rig"
    stage = StageType.rig
    kind = ProviderKind.api
    requires_key = True
    key_name = "tripo"
    description = (
        "Auto-rig a Tripo-generated model with Tripo3D's hosted humanoid rigging. "
        "Requires the base mesh to have been created with the Tripo provider (it reuses "
        "that model's task id)."
    )
    license_note = "Tripo commercial rights (verify plan)."
    commercial_ok = True
    cost_hint = "~$0.10/model"
    homepage = "https://platform.tripo3d.ai/"
    params = [
        ProviderParam(name="spec", label="Rig spec", type="select",
                      options=["humanoid"], default="humanoid"),
        ProviderParam(name="out_format", label="Output format", type="select",
                      options=["glb"], default="glb"),
    ]

    def run(self, ctx: JobContext) -> list:
        import httpx

        from ... import keychain

        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Add the Tripo API key in Settings → Providers (key_name 'tripo').")

        task_id = _find_tripo_task_id(ctx)
        if not task_id:
            raise RuntimeError(
                "Tripo Auto-Rig needs the original model's Tripo task id, which is missing. "
                "Generate the base mesh with the Tripo (gen3d) provider first, then rig that asset."
            )

        out_format = str(ctx.param("out_format", "glb")).lower() or "glb"
        headers = {"Authorization": f"Bearer {key}"}
        body = {
            "type": "animate_rig",
            "original_model_task_id": task_id,
            "out_format": out_format,
        }

        ctx.progress(0.1, "submitting rig task")
        with httpx.Client(base_url=_BASE, headers=headers, timeout=60.0) as client:
            resp = client.post("/task", json=body)
            if resp.status_code >= 400:
                raise RuntimeError(f"Tripo task submit failed ({resp.status_code}): {resp.text[:500]}")
            data = resp.json().get("data", {})
            rig_task_id = data.get("task_id") or data.get("taskId")
            if not rig_task_id:
                raise RuntimeError(f"Tripo did not return a task id: {resp.text[:500]}")

            # --- poll until done ------------------------------------------
            model_url = None
            deadline = time.time() + 600
            while time.time() < deadline:
                poll = client.get(f"/task/{rig_task_id}")
                if poll.status_code >= 400:
                    raise RuntimeError(
                        f"Tripo poll failed ({poll.status_code}): {poll.text[:500]}"
                    )
                pdata = poll.json().get("data", {})
                status = str(pdata.get("status", "")).lower()
                prog = pdata.get("progress")
                frac = 0.2 + 0.6 * (float(prog) / 100.0) if isinstance(prog, (int, float)) else 0.4
                ctx.progress(min(frac, 0.85), f"rigging ({status or 'running'})")
                if status in ("success", "succeeded", "completed"):
                    model_url = _extract_model_url(pdata)
                    break
                if status in ("failed", "error", "cancelled", "canceled", "banned", "expired"):
                    raise RuntimeError(f"Tripo rig task {status}: {poll.text[:500]}")
                time.sleep(3)

            if not model_url:
                raise RuntimeError("Tripo rig task did not produce a model URL before timeout.")

            ctx.progress(0.9, "downloading rigged model")
            dl = client.get(model_url, timeout=300.0)
            if dl.status_code >= 400:
                raise RuntimeError(f"Tripo model download failed ({dl.status_code}).")
            out = ctx.out_path(f"tripo-rig-{rig_task_id}.{out_format}")
            out.write_bytes(dl.content)

        ctx.add_cost(0.10)
        ctx.progress(0.97, "saving rigged model")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                name=out.name,
                meta={"engine": "tripo", "rigged": True, "spec": ctx.param("spec", "humanoid"),
                      "tripo_task_id": rig_task_id, "source_task_id": task_id},
                parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                license=self.license_note,
                commercial_ok=True,
            )
        ]


def _find_tripo_task_id(ctx: JobContext) -> str | None:
    """Pull the original Tripo model task id from input-asset metadata."""
    for asset in ctx.input_assets:
        meta = asset.meta or {}
        for k in ("tripo_task_id", "task_id", "taskId", "original_model_task_id"):
            v = meta.get(k)
            if v:
                return str(v)
    return None


def _extract_model_url(pdata: dict) -> str | None:
    """Find the rigged-model download URL in a finished Tripo task payload."""
    out = pdata.get("output") or pdata.get("result") or {}
    if isinstance(out, dict):
        for k in ("model", "pbr_model", "rigged_model", "base_model", "model_url"):
            v = out.get(k)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, dict) and v.get("url"):
                return v["url"]
    # some payloads put the url directly on data
    for k in ("model", "model_url"):
        v = pdata.get(k)
        if isinstance(v, str) and v:
            return v
    return None
