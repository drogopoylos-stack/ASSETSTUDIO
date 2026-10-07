"""Meshy texturing — text-to-texture an existing Meshy model.

Meshy's text-to-texture task needs a *model URL* it can fetch, not a local file
upload, so this adapter re-textures a mesh that Meshy already produced: the base
Meshy gen3d provider stores the model id/URL in the asset ``meta`` and we pass
that through. We submit the task, poll it, then download the textured GLB.

``httpx`` is imported lazily so the module always imports cleanly.
"""
from __future__ import annotations

import time
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

_BASE = "https://api.meshy.ai"


def _model_url_from_meta(meta: dict) -> str | None:
    """Recover a fetchable Meshy model URL (or id) chained through asset meta."""
    for key in ("meshy_model_url", "model_url", "modelUrl", "glb_url"):
        val = meta.get(key)
        if val:
            return str(val)
    # An id alone is not directly fetchable, but Meshy accepts its own URLs;
    # surface the id so the caller error is precise if no URL is present.
    return None


class MeshyTextureProvider(Provider):
    id = "meshy-texture"
    name = "Meshy Texturing"
    stage = StageType.texture
    kind = ProviderKind.api
    requires_key = True
    key_name = "meshy"
    description = (
        "Text-to-texture a Meshy-generated mesh. Needs the base mesh's Meshy "
        "model URL (generate the base mesh with the Meshy provider first)."
    )
    license_note = "Meshy grants commercial use (verify plan)."
    commercial_ok = True
    cost_hint = "~$0.10/model"
    homepage = "https://www.meshy.ai/"
    params = [
        ProviderParam(name="prompt", label="Texture prompt", type="text", default="",
                      description="Describe the desired surface/material look."),
        ProviderParam(name="art_style", label="Art style", type="select",
                      options=["realistic", "cartoon", "sculpture"], default="realistic"),
    ]

    def is_available(self) -> tuple[bool, str]:
        ok, reason = super().is_available()
        return ok, reason

    def run(self, ctx: JobContext) -> list:
        import httpx

        from ... import keychain

        mesh = ctx.first_mesh()
        if not mesh:
            raise RuntimeError("Meshy texturing needs an input 3D model (.glb/.gltf/...).")

        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Add the Meshy API key in Settings → Providers.")

        meta = ctx.input_assets[0].meta if ctx.input_assets else {}
        model_url = _model_url_from_meta(meta)
        if not model_url:
            raise RuntimeError(
                "Meshy text-to-texture needs a public model URL, which a local "
                "mesh file cannot provide. Generate the base mesh with the Meshy "
                "(gen3d) provider first so its model URL is carried in the asset "
                "metadata, then re-run texturing."
            )

        prompt = str(ctx.param("prompt", "")).strip() or "high quality texture"
        art_style = str(ctx.param("art_style", "realistic"))

        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {
            "model_url": model_url,
            "text_style_prompt": prompt,
            "art_style": art_style,
            "enable_pbr": True,
        }

        ctx.progress(0.1, "submitting texture task")
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(f"{_BASE}/openapi/v1/text-to-texture", headers=headers, json=body)
            if resp.status_code >= 400:
                raise RuntimeError(f"Meshy task submit failed ({resp.status_code}): {resp.text}")
            data = resp.json()
            task_id = data.get("result") or data.get("id") or data.get("task_id")
            if not task_id:
                raise RuntimeError(f"Meshy response missing task id: {data}")

            glb_url = self._poll(client, headers, task_id, ctx)

            ctx.progress(0.9, "downloading textured GLB")
            out = ctx.out_path(f"meshy-textured-{task_id}.glb")
            out.write_bytes(client.get(glb_url, timeout=300.0).content)

        ctx.add_cost(0.1)
        ctx.progress(0.97, "saving asset")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                name=out.name,
                prompt=prompt,
                meta={"engine": "meshy", "meshy_task_id": task_id, "art_style": art_style},
                parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]

    def _poll(self, client, headers, task_id, ctx, timeout: float = 600.0) -> str:
        """Poll a Meshy task until success; return the textured GLB URL."""
        deadline = time.time() + timeout
        url = f"{_BASE}/openapi/v1/text-to-texture/{task_id}"
        while time.time() < deadline:
            r = client.get(url, headers=headers, timeout=60.0)
            if r.status_code >= 400:
                raise RuntimeError(f"Meshy poll failed ({r.status_code}): {r.text}")
            d = r.json()
            status = (d.get("status") or "").upper()
            prog = float(d.get("progress", 0) or 0) / 100.0
            ctx.progress(0.15 + 0.7 * max(0.0, min(1.0, prog)), f"texturing ({status or '...'})")
            if status in ("SUCCEEDED", "COMPLETED"):
                urls = d.get("model_urls") or {}
                glb = urls.get("glb") or d.get("model_url")
                if not glb:
                    raise RuntimeError(f"Meshy task done but no GLB URL: {d}")
                return glb
            if status in ("FAILED", "CANCELED", "EXPIRED"):
                raise RuntimeError(f"Meshy texture task {status}: {d.get('task_error') or d}")
            time.sleep(3.0)
        raise RuntimeError("Meshy texture task timed out.")
