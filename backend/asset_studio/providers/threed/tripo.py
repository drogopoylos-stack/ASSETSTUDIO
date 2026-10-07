"""Tripo (image/text → 3D) provider.

Calls the Tripo3D cloud API to turn a text prompt or an input image into a
textured GLB mesh.

Documented flow (Tripo Open Platform API v2,
https://platform.tripo3d.ai/docs):

    * Auth: ``Authorization: Bearer <api-key>`` on every request.
    * Image flow:
        1. ``POST {base}/upload`` (multipart, field ``file``) -> the response
           ``data.image_token`` references the uploaded image.
        2. ``POST {base}/task`` with::

               {"type": "image_to_model",
                "file": {"type": "png", "file_token": <image_token>},
                "texture": true, "pbr": true, "face_limit": 10000}
    * Text flow:
        ``POST {base}/task`` with
        ``{"type": "text_to_model", "prompt": ..., "texture": ..., ...}``.
    * Both return ``data.task_id``. Poll ``GET {base}/task/{task_id}`` until
      ``data.status == "success"`` (``failed``/``cancelled`` => error),
      reporting ``data.progress`` (0..100) as job progress.
    * Download ``data.output.pbr_model`` (preferred) or ``data.output.model``
      (a signed URL) and save it as ``.glb``.
"""
from __future__ import annotations

import time
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify

_API_BASE = "https://api.tripo3d.ai/v2/openapi"


class TripoProvider(Provider):
    id = "tripo"
    name = "Tripo (image/text → 3D)"
    stage = StageType.gen3d
    kind = ProviderKind.api
    requires_key = True
    key_name = "tripo"
    description = "Cloud image/text → textured 3D GLB via the Tripo3D API."
    license_note = "Tripo grants commercial rights to generated models (verify your plan)."
    commercial_ok = True
    cost_hint = "~$0.10–0.30/model"
    homepage = "https://platform.tripo3d.ai/"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="",
                      description="Text prompt (used for text→model, or to guide image→model)."),
        ProviderParam(name="mode", label="Mode", type="select",
                      options=["auto", "text_to_model", "image_to_model"], default="auto",
                      description="'auto' uses an input image when present, else text."),
        ProviderParam(name="texture", label="Texture", type="bool", default=True),
        ProviderParam(name="pbr", label="PBR materials", type="bool", default=True),
        ProviderParam(name="face_limit", label="Face limit", type="int", default=10000,
                      min=1000, max=100000, step=1000),
        ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1),
    ]

    # --- availability ------------------------------------------------------
    def is_available(self) -> tuple[bool, str]:
        ok, reason = super().is_available()
        if not ok:
            return ok, reason
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False, "Install httpx (pip install httpx) to use the Tripo provider."
        return True, ""

    # --- execution ---------------------------------------------------------
    def run(self, ctx: JobContext) -> list:
        import httpx

        from ... import keychain

        key = keychain.get_key(self.key_name)
        if not key:
            raise RuntimeError("Tripo API key is missing — add it in Settings → Providers.")
        headers = {"Authorization": f"Bearer {key}"}

        prompt = str(ctx.param("prompt", "") or "")
        mode = ctx.param("mode", "auto")
        texture = bool(ctx.param("texture", True))
        pbr = bool(ctx.param("pbr", True))
        face_limit = int(ctx.param("face_limit", 10000))
        seed = ctx.param("seed", -1)

        image = ctx.first_image()
        use_image = image is not None and mode in ("auto", "image_to_model")
        if not use_image and mode == "image_to_model":
            raise RuntimeError("Mode 'image_to_model' selected but no input image was provided.")
        if not use_image and not prompt.strip():
            raise RuntimeError("Tripo text→model needs a non-empty prompt (or attach an input image).")

        with httpx.Client(timeout=120.0) as client:
            # --- build the task payload --------------------------------------
            if use_image:
                ctx.progress(0.1, "uploading image")
                img_path = Path(image)
                ext = img_path.suffix.lower().lstrip(".") or "png"
                if ext == "jpeg":
                    ext = "jpg"
                with img_path.open("rb") as fh:
                    up = client.post(
                        f"{_API_BASE}/upload",
                        headers=headers,
                        files={"file": (img_path.name, fh)},
                    )
                _raise_for_api(up, "image upload")
                token = (up.json().get("data") or {}).get("image_token")
                if not token:
                    raise RuntimeError(f"Tripo upload returned no image_token: {up.text}")
                payload = {
                    "type": "image_to_model",
                    "file": {"type": ext, "file_token": token},
                    "texture": texture,
                    "pbr": pbr,
                    "face_limit": face_limit,
                }
            else:
                payload = {
                    "type": "text_to_model",
                    "prompt": prompt,
                    "texture": texture,
                    "pbr": pbr,
                    "face_limit": face_limit,
                }
            if seed not in (None, "", -1, "-1"):
                try:
                    payload["model_seed"] = int(seed)
                except (TypeError, ValueError):
                    pass

            ctx.progress(0.2, "submitting task")
            sub = client.post(f"{_API_BASE}/task", headers=headers, json=payload)
            _raise_for_api(sub, "task submit")
            task_id = (sub.json().get("data") or {}).get("task_id")
            if not task_id:
                raise RuntimeError(f"Tripo did not return a task_id: {sub.text}")
            ctx.log(f"Tripo task {task_id} submitted ({payload['type']}).")

            # --- poll to completion -----------------------------------------
            output: dict = {}
            while True:
                time.sleep(3)
                poll = client.get(f"{_API_BASE}/task/{task_id}", headers=headers)
                _raise_for_api(poll, "task poll")
                data = poll.json().get("data") or {}
                status = data.get("status")
                pct = data.get("progress", 0) or 0
                ctx.progress(0.2 + 0.7 * (float(pct) / 100.0), f"generating ({pct}%)")
                if status == "success":
                    output = data.get("output") or {}
                    break
                if status in ("failed", "cancelled", "banned", "expired", "error"):
                    raise RuntimeError(f"Tripo task {task_id} ended as '{status}': {poll.text}")

            # --- download the model -----------------------------------------
            model_url = output.get("pbr_model") or output.get("model")
            if not model_url:
                raise RuntimeError(f"Tripo task {task_id} produced no model URL: {output}")
            ctx.progress(0.92, "downloading model")
            dl = client.get(model_url, timeout=300.0)
            _raise_for_api(dl, "model download")
            stem = slugify(prompt) or "tripo"
            out = ctx.out_path(f"{stem}-{task_id}.glb")
            out.write_bytes(dl.content)

        ctx.add_cost(0.2)
        ctx.progress(0.97, "saving asset")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                prompt=prompt,
                meta={"engine": "tripo", "task_id": task_id,
                      "mode": payload["type"], "pbr": pbr, "textured": texture},
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]


def _raise_for_api(resp, what: str) -> None:
    """Raise a RuntimeError carrying the API error body on non-2xx responses."""
    if resp.status_code >= 400:
        raise RuntimeError(f"Tripo {what} failed (HTTP {resp.status_code}): {resp.text}")
    # Tripo wraps results as {"code": 0, "data": {...}}; non-zero code is an error.
    try:
        body = resp.json()
    except Exception:
        return
    if isinstance(body, dict) and body.get("code") not in (0, None):
        raise RuntimeError(f"Tripo {what} error (code {body.get('code')}): {body.get('message', resp.text)}")
