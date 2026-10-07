"""TRELLIS2 texture projection (local).

Projects a texture onto an input mesh using a local TRELLIS2 backend. Two
transports are supported, tried in order:

  * a ComfyUI instance (``tools.comfyui_url``) running the **TRELLIS2 texture
    projection** custom nodes, exposed as a workflow at ``/trellis2/texture``;
  * a standalone TRELLIS server (``tools.trellis_url``) exposing ``/texture``.

This is best-effort glue around community endpoints: it uploads the mesh (and an
optional reference image), waits for the textured GLB, and downloads it. It
requires the TRELLIS2 ComfyUI nodes (or a TRELLIS server) to be installed and
running; if neither endpoint accepts the request, a clear RuntimeError explains
how to set it up.

``httpx`` is imported lazily so the module always imports cleanly.
"""
from __future__ import annotations

from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider
from ..comfy_common import ComfyUnloadMixin


class TrellisTextureProvider(ComfyUnloadMixin, Provider):
    id = "trellis-texture"
    name = "TRELLIS2 Texture Projection (ComfyUI)"
    stage = StageType.texture
    kind = ProviderKind.local
    requires_key = False
    description = (
        "Project a texture onto a mesh with a local TRELLIS2 backend "
        "(ComfyUI TRELLIS2 nodes or a TRELLIS server)."
    )
    license_note = "Review TRELLIS license."
    commercial_ok = None
    cost_hint = "free"
    homepage = "https://github.com/microsoft/TRELLIS"
    params = [
        ProviderParam(name="prompt", label="Texture prompt", type="text", default=""),
        ProviderParam(name="reference_image", label="Reference image", type="file", default="",
                      description="Optional image to guide the projected texture."),
        ProviderParam(name="resolution", label="Texture resolution", type="select",
                      options=["1024", "2048"], default="1024"),
    ]

    def _endpoints(self, ctx: JobContext) -> list[tuple[str, str]]:
        """Return candidate (base_url, label) transports, in preference order."""
        out: list[tuple[str, str]] = []
        comfy = ctx.tool("comfyui_url")
        trellis = ctx.tool("trellis_url")
        if comfy:
            out.append((str(comfy).rstrip("/"), "comfyui"))
        if trellis:
            out.append((str(trellis).rstrip("/"), "trellis"))
        return out

    def is_available(self) -> tuple[bool, str]:
        import httpx

        from ...config import settings as _settings

        tools = (_settings.get("tools", {}) or {})
        comfy = tools.get("comfyui_url")
        trellis = tools.get("trellis_url")
        for base, probe in ((comfy, "/system_stats"), (trellis, "/")):
            if not base:
                continue
            try:
                r = httpx.get(str(base).rstrip("/") + probe, timeout=2.5)
                if r.status_code < 500:
                    return True, ""
            except Exception:
                continue
        return False, "Start ComfyUI with TRELLIS2 nodes, or a TRELLIS server."

    def run(self, ctx: JobContext) -> list:
        import httpx

        mesh = ctx.first_mesh()
        if not mesh:
            raise RuntimeError("TRELLIS2 texture projection needs an input 3D model (.glb/.gltf/...).")

        endpoints = self._endpoints(ctx)
        if not endpoints:
            raise RuntimeError(
                "No TRELLIS backend configured. Set tools.comfyui_url (with the "
                "TRELLIS2 texture-projection nodes) or tools.trellis_url in Settings."
            )

        prompt = str(ctx.param("prompt", "")).strip()
        resolution = str(ctx.param("resolution", "1024"))
        ref = ctx.param("reference_image", "") or ctx.first_image()

        # transport-specific texture-projection path
        paths = {"comfyui": "/trellis2/texture", "trellis": "/texture"}

        ctx.progress(0.15, "submitting texture projection")
        last_err = ""
        with httpx.Client(timeout=600.0) as client:
            for base, kind in endpoints:
                url = base + paths[kind]
                files = {"mesh": (Path(mesh).name, Path(mesh).read_bytes(), "model/gltf-binary")}
                if ref and Path(str(ref)).exists():
                    files["reference_image"] = (
                        Path(str(ref)).name, Path(str(ref)).read_bytes(), "application/octet-stream")
                data = {"prompt": prompt, "resolution": resolution}
                try:
                    resp = client.post(url, files=files, data=data)
                except Exception as e:
                    last_err = f"{kind} unreachable: {e}"
                    ctx.log(last_err)
                    continue
                if resp.status_code == 404:
                    last_err = (f"{kind} has no texture-projection endpoint at {url} "
                                "(TRELLIS2 nodes not installed?)")
                    ctx.log(last_err)
                    continue
                if resp.status_code >= 400:
                    last_err = f"{kind} texture failed ({resp.status_code}): {resp.text[:300]}"
                    ctx.log(last_err)
                    continue

                ctx.progress(0.85, "downloading textured GLB")
                out = ctx.out_path(f"trellis-textured-{Path(mesh).stem}.glb")
                out.write_bytes(resp.content)
                ctx.progress(0.97, "saving asset")
                return [
                    ctx.make_asset(
                        path=out,
                        type=AssetType.model,
                        name=out.name,
                        prompt=prompt,
                        meta={"engine": "trellis2", "transport": kind, "resolution": resolution},
                        parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                        license=self.license_note,
                        commercial_ok=self.commercial_ok,
                    )
                ]

        raise RuntimeError(
            "TRELLIS2 texture projection failed: no working endpoint. "
            "Install the TRELLIS2 texture-projection ComfyUI nodes (or run a "
            f"TRELLIS server) and retry. Last error: {last_err or 'no endpoint reachable'}"
        )
