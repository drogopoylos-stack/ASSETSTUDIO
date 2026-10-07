"""Tripo texturing — paint/PBR-texture an existing Tripo-generated mesh.

Tripo's texturing task operates on a *prior* Tripo model task (it chains by
``original_model_task_id``), not on an arbitrary uploaded GLB. So this adapter
requires the base mesh to have been produced by the Tripo gen3d provider, which
stashes its ``task_id`` in the asset ``meta``. We resubmit a ``texture_model``
task, poll it, then download the textured GLB.

Heavy/optional libs (``httpx``) are imported lazily so the module always imports
cleanly even when the dependency is absent.
"""
from __future__ import annotations

import time
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

_BASE = "https://api.tripo3d.ai/v2/openapi"


def _task_id_from_meta(meta: dict) -> str | None:
    """Find the upstream Tripo task id chained through an asset's metadata."""
    for key in ("tripo_task_id", "task_id", "taskId", "original_model_task_id"):
        val = meta.get(key)
        if val:
            return str(val)
    return None


class TripoTextureProvider(Provider):
    id = "tripo-texture"
    name = "Tripo Texturing"
    stage = StageType.texture
    kind = ProviderKind.api
    requires_key = True
    key_name = "tripo"
    description = (
        "PBR-texture a Tripo-generated mesh. Chains off the base mesh's Tripo "
        "task id (set the base provider to Tripo first)."
    )
    license_note = "Tripo grants commercial rights (verify plan)."
    commercial_ok = True
    cost_hint = "~$0.10/model"
    homepage = "https://platform.tripo3d.ai/"
    params = [
        ProviderParam(name="prompt", label="Texture prompt", type="text", default="",
                      description="Optional guidance for the texture look."),
        ProviderParam(name="pbr", label="PBR materials", type="bool", default=True),
        ProviderParam(name="texture_quality", label="Texture quality", type="select",
                      options=["standard", "detailed"], default="standard"),
    ]

    def is_available(self) -> tuple[bool, str]:
        ok, reason = super().is_available()
        return ok, reason

    def run(self, ctx: JobContext) -> list:
        import httpx

        from ... import keychain

        mesh = ctx.first_mesh()
        if not mesh:
            raise RuntimeError("Tripo texturing needs an input 3D model (.glb/.gltf/...).")

        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Add the Tripo API key in Settings → Providers.")

        meta = ctx.input_assets[0].meta if ctx.input_assets else {}
        base_task_id = _task_id_from_meta(meta)
        if not base_task_id:
            raise RuntimeError(
                "Tripo texturing chains off a Tripo task id, which uploading a "
                "local mesh cannot provide. Generate the base mesh with the Tripo "
                "(gen3d) provider first so its task_id is carried in the asset "
                "metadata, then re-run texturing."
            )

        prompt = str(ctx.param("prompt", "")).strip()
        pbr = bool(ctx.param("pbr", True))
        quality = str(ctx.param("texture_quality", "standard"))

        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body: dict = {
            "type": "texture_model",
            "original_model_task_id": base_task_id,
            "texture": True,
            "pbr": pbr,
            "texture_quality": quality,
        }
        if prompt:
            body["text"] = prompt

        ctx.progress(0.1, "submitting texture task")
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(f"{_BASE}/task", headers=headers, json=body)
            if resp.status_code >= 400:
                raise RuntimeError(f"Tripo task submit failed ({resp.status_code}): {resp.text}")
            data = resp.json()
            task_id = (data.get("data") or {}).get("task_id") or data.get("task_id")
            if not task_id:
                raise RuntimeError(f"Tripo response missing task_id: {data}")

            model_url = self._poll(client, headers, task_id, ctx)

            ctx.progress(0.9, "downloading textured GLB")
            out = ctx.out_path(f"tripo-textured-{task_id}.glb")
            out.write_bytes(client.get(model_url, timeout=300.0).content)

        ctx.add_cost(0.1)
        ctx.progress(0.97, "saving asset")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                name=out.name,
                prompt=prompt,
                meta={"engine": "tripo", "tripo_task_id": task_id,
                      "pbr": pbr, "texture_quality": quality},
                parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]

    def _poll(self, client, headers, task_id, ctx, timeout: float = 600.0) -> str:
        """Poll a Tripo task until success; return the textured model URL."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = client.get(f"{_BASE}/task/{task_id}", headers=headers, timeout=60.0)
            if r.status_code >= 400:
                raise RuntimeError(f"Tripo poll failed ({r.status_code}): {r.text}")
            d = (r.json().get("data") or {})
            status = d.get("status")
            prog = float(d.get("progress", 0) or 0) / 100.0
            ctx.progress(0.15 + 0.7 * max(0.0, min(1.0, prog)), f"texturing ({status})")
            if status in ("success", "completed"):
                output = d.get("output") or {}
                url = (output.get("pbr_model") or output.get("model")
                       or output.get("base_model"))
                if not url:
                    raise RuntimeError(f"Tripo task done but no model URL: {output}")
                return url
            if status in ("failed", "cancelled", "banned", "expired"):
                raise RuntimeError(f"Tripo texture task {status}: {d}")
            time.sleep(3.0)
        raise RuntimeError("Tripo texture task timed out.")
