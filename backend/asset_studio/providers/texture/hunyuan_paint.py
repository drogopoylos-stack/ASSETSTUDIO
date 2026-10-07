"""Hunyuan3D-2 Paint (local).

Texture-paints an input mesh with a locally hosted Hunyuan3D-2 server (the
``/texture`` paint endpoint). Point ``tools.hunyuan_url`` at your running
Hunyuan3D-2 API. The mesh (and an optional reference image) are uploaded; the
textured GLB is downloaded back.

``httpx`` is imported lazily so the module always imports cleanly even when no
server is running.
"""
from __future__ import annotations

from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider


class HunyuanPaintProvider(Provider):
    id = "hunyuan-paint"
    name = "Hunyuan3D Paint"
    stage = StageType.texture
    kind = ProviderKind.local
    requires_key = False
    description = "Texture-paint a mesh with a local Hunyuan3D-2 server (tools.hunyuan_url)."
    license_note = "Review Hunyuan3D license."
    commercial_ok = None
    cost_hint = "free"
    homepage = "https://github.com/Tencent/Hunyuan3D-2"
    params = [
        ProviderParam(name="prompt", label="Texture prompt", type="text", default=""),
        ProviderParam(name="reference_image", label="Reference image", type="file", default="",
                      description="Optional image to guide the painted texture."),
        ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1),
    ]

    def _base(self, ctx: JobContext) -> str | None:
        url = ctx.tool("hunyuan_url")
        return str(url).rstrip("/") if url else None

    def is_available(self) -> tuple[bool, str]:
        import httpx

        from ...config import settings as _settings

        base = (_settings.get("tools", {}) or {}).get("hunyuan_url")
        if not base:
            return False, "Set tools.hunyuan_url to your running Hunyuan3D-2 server."
        try:
            r = httpx.get(str(base).rstrip("/") + "/", timeout=2.5)
            if r.status_code < 500:
                return True, ""
        except Exception:
            pass
        return False, f"Hunyuan3D-2 server not reachable at {base}. Start it and retry."

    def run(self, ctx: JobContext) -> list:
        import httpx

        mesh = ctx.first_mesh()
        if not mesh:
            raise RuntimeError("Hunyuan3D Paint needs an input 3D model (.glb/.gltf/...).")

        base = self._base(ctx)
        if not base:
            raise RuntimeError(
                "No Hunyuan3D-2 server configured. Set tools.hunyuan_url in Settings."
            )

        prompt = str(ctx.param("prompt", "")).strip()
        seed = int(ctx.param("seed", -1))
        ref = ctx.param("reference_image", "") or ctx.first_image()

        files = {"mesh": (Path(mesh).name, Path(mesh).read_bytes(), "model/gltf-binary")}
        if ref and Path(str(ref)).exists():
            files["image"] = (Path(str(ref)).name, Path(str(ref)).read_bytes(), "application/octet-stream")
        data = {"prompt": prompt, "seed": str(seed)}

        ctx.progress(0.2, "painting texture")
        with httpx.Client(timeout=900.0) as client:
            try:
                resp = client.post(f"{base}/texture", files=files, data=data)
            except Exception as e:
                raise RuntimeError(
                    f"Hunyuan3D-2 server unreachable at {base}: {e}. Start the "
                    "server (its /texture paint endpoint) and retry."
                ) from e
            if resp.status_code == 404:
                raise RuntimeError(
                    f"Hunyuan3D-2 server at {base} has no /texture endpoint. "
                    "Ensure you are running the Hunyuan3D-2 paint API."
                )
            if resp.status_code >= 400:
                raise RuntimeError(f"Hunyuan3D Paint failed ({resp.status_code}): {resp.text[:300]}")

            ctx.progress(0.85, "downloading textured GLB")
            out = ctx.out_path(f"hunyuan-painted-{Path(mesh).stem}.glb")
            out.write_bytes(resp.content)

        ctx.progress(0.97, "saving asset")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                name=out.name,
                prompt=prompt,
                seed=seed if seed >= 0 else None,
                meta={"engine": "hunyuan3d-2", "task": "paint"},
                parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]
