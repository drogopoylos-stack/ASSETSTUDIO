"""Meshy (image/text → 3D) provider.

Calls the Meshy cloud API to turn a text prompt or an input image into a
textured GLB mesh.

Documented flow (Meshy API, https://docs.meshy.ai):

    * Auth: ``Authorization: Bearer <api-key>`` on every request.
    * Image flow (``POST {base}/openapi/v1/image-to-3d``)::

          {"image_url": <data-uri-or-url>, "ai_model": ..., "topology": ...,
           "target_polycount": ..., "should_texture": true}

      ``image_url`` must be a public URL or a base64 data URI
      (``data:image/png;base64,...``); we encode the local input image inline.
    * Text flow (``POST {base}/openapi/v2/text-to-3d``)::

          {"mode": "preview", "prompt": ..., "art_style": "realistic",
           "topology": "triangle", "target_polycount": 30000}

    * Both return ``{"result": <task-id>}``. Poll
      ``GET {base}/openapi/v2/text-to-3d/{id}`` (text) or
      ``GET {base}/openapi/v1/image-to-3d/{id}`` (image) until
      ``status == "SUCCEEDED"`` (``FAILED``/``CANCELED`` => error), reporting
      ``progress`` (0..100) as job progress.
    * Download ``model_urls.glb`` and save it as ``.glb``.
"""
from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify

_API_BASE = "https://api.meshy.ai"


class MeshyProvider(Provider):
    id = "meshy"
    name = "Meshy (image/text → 3D)"
    stage = StageType.gen3d
    kind = ProviderKind.api
    requires_key = True
    key_name = "meshy"
    description = "Cloud image/text → textured 3D GLB via the Meshy API."
    license_note = "Meshy grants commercial use of generated assets (verify your plan)."
    commercial_ok = True
    cost_hint = "~$0.20/model"
    homepage = "https://www.meshy.ai/"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="",
                      description="Text prompt (used for text→3D, or to guide image→3D)."),
        ProviderParam(name="mode", label="Mode", type="select",
                      options=["auto", "text", "image"], default="auto",
                      description="'auto' uses an input image when present, else text."),
        ProviderParam(name="art_style", label="Art style", type="select",
                      options=["realistic", "sculpture"], default="realistic"),
        ProviderParam(name="topology", label="Topology", type="select",
                      options=["triangle", "quad"], default="triangle"),
        ProviderParam(name="target_polycount", label="Target polycount", type="int",
                      default=30000, min=1000, max=300000, step=1000),
    ]

    # --- availability ------------------------------------------------------
    def is_available(self) -> tuple[bool, str]:
        ok, reason = super().is_available()
        if not ok:
            return ok, reason
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False, "Install httpx (pip install httpx) to use the Meshy provider."
        return True, ""

    # --- execution ---------------------------------------------------------
    def run(self, ctx: JobContext) -> list:
        import httpx

        from ... import keychain

        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Meshy API key is missing — add it in Settings → Providers.")
        headers = {"Authorization": f"Bearer {key}"}

        prompt = str(ctx.param("prompt", "") or "")
        mode = ctx.param("mode", "auto")
        art_style = ctx.param("art_style", "realistic")
        topology = ctx.param("topology", "triangle")
        target_polycount = int(ctx.param("target_polycount", 30000))

        image = ctx.first_image()
        use_image = image is not None and mode in ("auto", "image")
        if not use_image and mode == "image":
            raise RuntimeError("Mode 'image' selected but no input image was provided.")
        if not use_image and not prompt.strip():
            raise RuntimeError("Meshy text→3D needs a non-empty prompt (or attach an input image).")

        with httpx.Client(timeout=120.0) as client:
            # --- submit the task --------------------------------------------
            if use_image:
                kind = "image"
                create_url = f"{_API_BASE}/openapi/v1/image-to-3d"
                poll_base = f"{_API_BASE}/openapi/v1/image-to-3d"
                payload = {
                    "image_url": _data_uri(Path(image)),
                    "topology": topology,
                    "target_polycount": target_polycount,
                    "should_texture": True,
                }
                ctx.progress(0.15, "submitting image→3D task")
            else:
                kind = "text"
                create_url = f"{_API_BASE}/openapi/v2/text-to-3d"
                poll_base = f"{_API_BASE}/openapi/v2/text-to-3d"
                payload = {
                    "mode": "preview",
                    "prompt": prompt,
                    "art_style": art_style,
                    "topology": topology,
                    "target_polycount": target_polycount,
                }
                ctx.progress(0.15, "submitting text→3D task")

            sub = client.post(create_url, headers=headers, json=payload)
            _raise_for_api(sub, "task submit")
            task_id = (sub.json() or {}).get("result")
            if not task_id:
                raise RuntimeError(f"Meshy did not return a task id: {sub.text}")
            ctx.log(f"Meshy {kind}→3D task {task_id} submitted.")

            # --- poll to completion -----------------------------------------
            model_urls: dict = {}
            while True:
                time.sleep(3)
                poll = client.get(f"{poll_base}/{task_id}", headers=headers)
                _raise_for_api(poll, "task poll")
                data = poll.json() or {}
                status = data.get("status")
                pct = data.get("progress", 0) or 0
                ctx.progress(0.15 + 0.75 * (float(pct) / 100.0), f"generating ({pct}%)")
                if status == "SUCCEEDED":
                    model_urls = data.get("model_urls") or {}
                    break
                if status in ("FAILED", "CANCELED", "EXPIRED"):
                    err = (data.get("task_error") or {}).get("message") or poll.text
                    raise RuntimeError(f"Meshy task {task_id} ended as '{status}': {err}")

            # --- download the model -----------------------------------------
            glb_url = model_urls.get("glb")
            if not glb_url:
                raise RuntimeError(f"Meshy task {task_id} produced no GLB URL: {model_urls}")
            ctx.progress(0.92, "downloading model")
            dl = client.get(glb_url, timeout=300.0)
            _raise_for_api(dl, "model download")
            stem = slugify(prompt) or "meshy"
            out = ctx.out_path(f"{stem}-{task_id}.glb")
            out.write_bytes(dl.content)

        ctx.add_cost(0.2)
        ctx.progress(0.97, "saving asset")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                prompt=prompt,
                meta={"engine": "meshy", "task_id": task_id, "mode": kind,
                      "art_style": art_style, "topology": topology},
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]


def _data_uri(path: Path) -> str:
    """Encode a local image file as a ``data:<mime>;base64,...`` URI for Meshy."""
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _raise_for_api(resp, what: str) -> None:
    """Raise a RuntimeError carrying the API error body on non-2xx responses."""
    if resp.status_code >= 400:
        raise RuntimeError(f"Meshy {what} failed (HTTP {resp.status_code}): {resp.text}")
