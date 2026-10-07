"""Hunyuan3D-2 image-to-3D, served by a local API server.

Tencent's Hunyuan3D-2 turns a single reference image into a textured 3D mesh.
This adapter targets the project's ``api_server.py``, which exposes::

    POST {base}/generate
        json: {"image": "<base64>", "texture": bool, "seed": int,
               "octree_resolution": int}
        -> a GLB returned either as raw binary (Content-Type model/gltf-binary
           or application/octet-stream) or as base64 in a JSON field.

Configure the server base URL with the ``hunyuan_url`` tool setting
(default ``http://127.0.0.1:8080``). No API key required.
"""
from __future__ import annotations

import base64
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify


class Hunyuan3DProvider(Provider):
    """Image -> 3D via a locally-hosted Hunyuan3D-2 ``api_server.py``."""

    id = "hunyuan3d"
    name = "Hunyuan3D-2 (local image→3D)"
    stage = StageType.gen3d
    kind = ProviderKind.local
    requires_key = False
    key_name = None
    description = (
        "Image-conditioned 3D mesh generation served by a local Hunyuan3D-2 API "
        "server (api_server.py). Configure the server URL in Settings → Tools "
        "(hunyuan_url)."
    )
    license_note = "Review the Hunyuan3D license for commercial use."
    commercial_ok = None
    cost_hint = "free"
    homepage = "https://github.com/Tencent/Hunyuan3D-2"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="",
                      description="optional text guidance"),
        ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1),
        ProviderParam(name="texture", label="Bake texture", type="bool", default=True),
        ProviderParam(name="octree_resolution", label="Octree resolution", type="int",
                      default=256, min=64, max=512, step=64),
    ]

    # --- availability ------------------------------------------------------
    def _base(self, ctx: JobContext) -> str:
        return str(ctx.tool("hunyuan_url", "http://127.0.0.1:8080")).rstrip("/")

    def is_available(self) -> tuple[bool, str]:
        from ...config import settings

        base = str(settings.get("tools", {}).get("hunyuan_url",
                   "http://127.0.0.1:8080")).rstrip("/")
        reason = f"Start the Hunyuan3D-2 API server at {base}"
        try:
            import httpx

            with httpx.Client(timeout=1.5) as client:
                for path in ("/health", ""):
                    try:
                        r = client.get(base + path)
                        if r.status_code < 500:
                            return True, ""
                    except httpx.HTTPError:
                        continue
        except Exception:
            return False, reason
        return False, reason

    # --- execution ---------------------------------------------------------
    def run(self, ctx: JobContext) -> list:
        """POST the base64 image to ``{base}/generate`` and save the GLB.

        Handles both response shapes Hunyuan3D-2's ``api_server.py`` may use:
        a raw binary GLB body, or a JSON object carrying base64 GLB bytes.
        Raises ``RuntimeError`` (with the server response) on failure. Returns
        a single model asset.
        """
        import httpx

        image = ctx.first_image()
        if not image:
            raise RuntimeError(
                "Hunyuan3D-2 is image-conditioned: provide an input image (.png/.jpg)."
            )

        base = self._base(ctx)
        prompt = str(ctx.param("prompt", ""))
        seed = int(ctx.param("seed", -1))
        texture = bool(ctx.param("texture", True))
        octree = int(ctx.param("octree_resolution", 256))

        ctx.progress(0.15, "encoding image")
        img_b64 = base64.b64encode(Path(image).read_bytes()).decode("ascii")
        payload = {
            "image": img_b64,
            "texture": texture,
            "seed": seed,
            "octree_resolution": octree,
        }
        if prompt:
            payload["prompt"] = prompt

        ctx.progress(0.3, "generating 3D mesh")
        with httpx.Client(timeout=600) as client:
            try:
                resp = client.post(base + "/generate", json=payload)
            except httpx.HTTPError as e:
                raise RuntimeError(
                    f"Could not reach Hunyuan3D-2 at {base}/generate: {e}"
                )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Hunyuan3D-2 request failed [{resp.status_code}]: {resp.text[:800]}"
                )

            out = ctx.out_path(f"{slugify(prompt or Path(image).stem)}-{ctx.job.id}.glb")
            data = self._extract_glb(resp)
            if not data:
                raise RuntimeError(
                    "Hunyuan3D-2 returned no GLB data — check the api_server.py "
                    f"response format. Response: {resp.text[:800]}"
                )
            ctx.progress(0.85, "saving GLB")
            out.write_bytes(data)

        ctx.progress(0.95, "saving asset")
        parent = ctx.input_assets[0].id if ctx.input_assets else None
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                prompt=prompt,
                seed=seed if seed != -1 else None,
                parent_id=parent,
                meta={"engine": "hunyuan3d", "server": base,
                      "textured": texture, "octree_resolution": octree},
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]

    # --- response helpers --------------------------------------------------
    def _extract_glb(self, resp) -> bytes:
        """Return raw GLB bytes from a binary body or a base64 JSON field."""
        ctype = resp.headers.get("content-type", "").lower()
        if "json" not in ctype:
            # Binary GLB (model/gltf-binary, application/octet-stream, ...).
            return resp.content
        try:
            body = resp.json()
        except ValueError:
            return resp.content
        if isinstance(body, str):
            return self._b64_to_bytes(body)
        if isinstance(body, dict):
            for key in ("glb", "model", "mesh", "data", "result", "file"):
                val = body.get(key)
                if isinstance(val, str) and val:
                    return self._b64_to_bytes(val)
        return b""

    @staticmethod
    def _b64_to_bytes(val: str) -> bytes:
        if "," in val and val.lower().startswith("data:"):
            val = val.split(",", 1)[1]
        try:
            return base64.b64decode(val)
        except (ValueError, base64.binascii.Error):
            return b""
