"""Meshy 7 quality pipeline (image/text → 3D) — the three-call provider.

This is a SECOND Meshy provider that runs alongside the original ``meshy`` one
(``threed/meshy.py``), which is left exactly as it shipped. Pick either from the
gen3d provider list and compare the two outputs on the same reference image.

THE THREE-CALL PIPELINE, AND WHY IT IS NOT ONE CALL
---------------------------------------------------
Meshy's best output does not come from asking ``image-to-3d`` for a textured mesh
in one shot. It comes from the same three steps an artist does by hand:

    1. SCULPT at full quality.   POST /openapi/v1/image-to-3d
       ``should_remesh: false``, ``should_texture: false``.
       The docs are explicit: "For the highest-quality model, we recommend
       setting should_remesh to false." That returns the raw high-detail
       surface — no decimation, no smart-topology retopo eating the detail
       being paid for.

    2. RETOPOLOGISE adaptively.  POST /openapi/v1/remesh
       ``decimation_mode: 3`` (medium) with ``topology: triangle``.
       ADAPTIVE, not a flat target: ``decimation_mode`` OVERRIDES
       ``target_polycount`` entirely and spends triangles where curvature needs
       them. A flat 30k target shaves the horns and the eye ridge — the exact
       silhouette a character is recognised by — to pay for flat belly panels.

    3. TEXTURE last.             POST /openapi/v1/retexture
       ``texture_resolution: 4k``, ``enable_pbr: true``, styled by the SAME
       reference image via ``image_style_url``.
       Texturing last means the UVs being painted belong to the mesh that
       ships, so nothing is resampled afterwards.

WHY THE ORIGINAL PROVIDER'S TOPOLOGY KNOBS DO NOTHING
-----------------------------------------------------
``threed/meshy.py`` sends ``topology`` and ``target_polycount`` on the
image-to-3d call but never sends ``should_remesh``. Both of those parameters
apply ONLY when ``should_remesh`` is true, and Meshy defaults it to false on
meshy-6/7 — so those two dials are silently ignored there. That provider is kept
unchanged on purpose: it is the baseline to compare against.

COST WARNING
------------
This provider makes three billable calls, not one. The Remesh API consumes
credits and answers HTTP 402 when the balance is too low, even though the same
step can be free in the Meshy web UI. Budget for three calls per model.

Auth is ``Authorization: Bearer <api-key>`` on every request. Every create
returns ``{"result": <task-id>}``; poll the matching GET until
``status == "SUCCEEDED"`` and read ``model_urls.glb``.
"""
from __future__ import annotations

import time
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify
from .meshy import _API_BASE, _data_uri, _raise_for_api

# Adaptive decimation levels. These OVERRIDE target_polycount when sent.
_DECIMATION = {"ultra": 1, "high": 2, "medium": 3, "low": 4}


class MeshyPipelineProvider(Provider):
    id = "meshy_pipeline"
    name = "Meshy 7 pipeline (sculpt → remesh → 4K texture)"
    stage = StageType.gen3d
    kind = ProviderKind.api
    requires_key = True
    key_name = "meshy"
    description = (
        "Three Meshy calls instead of one: full-detail sculpt (no remesh) → "
        "adaptive remesh → 4K PBR texture styled by the reference image. Keeps "
        "the silhouette that a flat polycount target destroys. Costs three calls."
    )
    license_note = "Meshy grants commercial use of generated assets (verify your plan)."
    commercial_ok = True
    cost_hint = "~$0.40/model — 3 billable calls (remesh consumes credits; 402 if low)"
    homepage = "https://docs.meshy.ai/"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="",
                      description="Text prompt (used for text→3D, or to guide image→3D)."),
        ProviderParam(name="mode", label="Mode", type="select",
                      options=["auto", "text", "image"], default="auto",
                      description="'auto' uses an input image when present, else text."),
        ProviderParam(name="ai_model", label="Meshy model", type="select",
                      options=["latest", "meshy-7", "meshy-6", "meshy-5"], default="latest",
                      description="'latest' currently maps to Meshy 7."),
        ProviderParam(name="art_style", label="Art style (text→3D only)", type="select",
                      options=["realistic", "sculpture"], default="realistic"),

        # ---- stage 1: the sculpt -----------------------------------------
        ProviderParam(name="should_remesh", label="Remesh during generation", type="bool",
                      default=False,
                      description="OFF = highest quality (Meshy's own recommendation). "
                                  "ON uses smart topology and costs detail."),
        ProviderParam(name="topology", label="Topology", type="select",
                      options=["triangle", "quad"], default="triangle"),
        ProviderParam(name="target_polycount", label="Target polycount", type="int",
                      default=30000, min=100, max=300000, step=1000,
                      description="Ignored whenever an adaptive decimation level is set."),

        # ---- stage 2: the adaptive remesh --------------------------------
        ProviderParam(name="remesh_after", label="Remesh after (adaptive)", type="bool",
                      default=True,
                      description="Second pass: retopologise the full-detail sculpt. "
                                  "This call consumes Meshy credits."),
        ProviderParam(name="decimation_mode", label="Adaptive level", type="select",
                      options=["ultra", "high", "medium", "low"], default="medium",
                      description="Spends triangles where curvature needs them. "
                                  "Overrides target polycount."),

        # ---- stage 3: the texture ----------------------------------------
        ProviderParam(name="should_texture", label="Texture", type="bool", default=True),
        ProviderParam(name="texture_resolution", label="Texture resolution", type="select",
                      options=["2k", "4k", "8k"], default="4k"),
        ProviderParam(name="enable_pbr", label="PBR maps", type="bool", default=True,
                      description="Metallic, roughness and normal alongside base colour."),
        ProviderParam(name="texture_style", label="Texture styled by", type="select",
                      options=["image", "prompt"], default="image",
                      description="'image' reuses the reference photo, which is what "
                                  "keeps the colours faithful to it."),
        ProviderParam(name="texture_prompt", label="Texture prompt", type="text", default="",
                      description="Used when 'Texture styled by' is 'prompt'."),
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
        ai_model = ctx.param("ai_model", "latest")
        art_style = ctx.param("art_style", "realistic")
        should_remesh = bool(ctx.param("should_remesh", False))
        topology = ctx.param("topology", "triangle")
        target_polycount = int(ctx.param("target_polycount", 30000))
        remesh_after = bool(ctx.param("remesh_after", True))
        decimation = str(ctx.param("decimation_mode", "medium"))
        should_texture = bool(ctx.param("should_texture", True))
        texture_resolution = str(ctx.param("texture_resolution", "4k"))
        enable_pbr = bool(ctx.param("enable_pbr", True))
        texture_style = str(ctx.param("texture_style", "image"))
        texture_prompt = str(ctx.param("texture_prompt", "") or "")

        image = ctx.first_image()
        use_image = image is not None and mode in ("auto", "image")
        if not use_image and mode == "image":
            raise RuntimeError("Mode 'image' selected but no input image was provided.")
        if not use_image and not prompt.strip():
            raise RuntimeError("Meshy text→3D needs a non-empty prompt (or attach an input image).")

        image_uri = _data_uri(Path(image)) if use_image else ""
        stages = 1 + (1 if remesh_after else 0) + (1 if should_texture else 0)
        step = 0.9 / stages
        # every task id the run produced, so a comparison run stays traceable
        ids: dict[str, str] = {}

        with httpx.Client(timeout=180.0) as client:
            # ---- stage 1: the sculpt ---------------------------------------
            if use_image:
                kind = "image"
                create_url = f"{_API_BASE}/openapi/v1/image-to-3d"
                payload = {
                    "image_url": image_uri,
                    "ai_model": ai_model,
                    "should_remesh": should_remesh,
                    # TEXTURING IS ALWAYS ITS OWN PASS, never bundled here, so the
                    # UVs being painted belong to the mesh that actually ships.
                    # Bundling it textures the pre-remesh sculpt, then throws it away.
                    "should_texture": False,
                }
            else:
                kind = "text"
                create_url = f"{_API_BASE}/openapi/v2/text-to-3d"
                payload = {
                    "mode": "preview",
                    "prompt": prompt,
                    "ai_model": ai_model,
                    "art_style": art_style,
                    "should_remesh": should_remesh,
                }
            # topology/target_polycount are read by Meshy ONLY when remeshing here
            if should_remesh:
                payload["topology"] = topology
                payload["target_polycount"] = target_polycount

            ctx.progress(0.05, f"sculpting at full detail ({ai_model})")
            task_id, model_urls = _run_task(
                client, headers, ctx, create_url, create_url, payload,
                label="sculpt", lo=0.05, hi=0.05 + step,
            )
            ids["sculpt"] = task_id
            done = 0.05 + step
            ctx.log(f"Meshy {kind}→3D sculpt {task_id} done (should_remesh={should_remesh}).")

            # ---- stage 2: the adaptive remesh ------------------------------
            if remesh_after:
                url = f"{_API_BASE}/openapi/v1/remesh"
                remesh_payload = {
                    "input_task_id": task_id,
                    "target_formats": ["glb"],
                    "topology": topology,
                    # Adaptive. This OVERRIDES target_polycount by design.
                    "decimation_mode": _DECIMATION.get(decimation, 3),
                }
                ctx.progress(done, f"remeshing (adaptive {decimation}, {topology})")
                task_id, model_urls = _run_task(
                    client, headers, ctx, url, url, remesh_payload,
                    label="remesh", lo=done, hi=done + step,
                )
                ids["remesh"] = task_id
                done += step
                ctx.log(f"Meshy remesh {task_id} done (adaptive {decimation}).")

            # ---- stage 3: the texture --------------------------------------
            if should_texture:
                url = f"{_API_BASE}/openapi/v1/retexture"
                tex_payload = {
                    "input_task_id": task_id,
                    "ai_model": ai_model,
                    "enable_pbr": enable_pbr,
                    "texture_resolution": texture_resolution,
                    "target_formats": ["glb"],
                }
                # EXACTLY ONE style input is allowed by the API.
                if texture_style == "image" and image_uri:
                    tex_payload["image_style_url"] = image_uri
                else:
                    style = texture_prompt.strip() or prompt.strip()
                    if not style:
                        raise RuntimeError(
                            "Texturing by prompt needs a texture prompt (or a reference image)."
                        )
                    tex_payload["text_style_prompt"] = style[:600]
                ctx.progress(done, f"texturing ({texture_resolution}, pbr={enable_pbr})")
                task_id, model_urls = _run_task(
                    client, headers, ctx, url, url, tex_payload,
                    label="texture", lo=done, hi=0.93,
                )
                ids["texture"] = task_id
                ctx.log(f"Meshy retexture {task_id} done ({texture_resolution}).")

            # ---- download ---------------------------------------------------
            glb_url = (model_urls or {}).get("glb")
            if not glb_url:
                raise RuntimeError(f"Meshy task {task_id} produced no GLB URL: {model_urls}")
            ctx.progress(0.94, "downloading model")
            dl = client.get(glb_url, timeout=600.0)
            _raise_for_api(dl, "model download")
            # "-pipeline" in the filename so an A/B pair is distinguishable on disk
            stem = f"{slugify(prompt) or 'meshy'}-pipeline"
            out = ctx.out_path(f"{stem}-{task_id}.glb")
            out.write_bytes(dl.content)

        # Sculpt + optional remesh + optional 4K texture — three billable calls.
        ctx.add_cost(0.2 + (0.1 if remesh_after else 0.0) + (0.1 if should_texture else 0.0))
        ctx.progress(0.97, "saving asset")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                prompt=prompt,
                meta={
                    "engine": "meshy", "pipeline": "3-call", "task_id": task_id,
                    "task_ids": ids, "mode": kind,
                    "ai_model": ai_model, "art_style": art_style, "topology": topology,
                    "should_remesh": should_remesh,
                    "remesh_after": remesh_after,
                    "decimation_mode": decimation if remesh_after else None,
                    "texture_resolution": texture_resolution if should_texture else None,
                    "enable_pbr": enable_pbr if should_texture else None,
                },
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]


def _run_task(client, headers, ctx, create_url: str, poll_base: str, payload: dict,
              *, label: str, lo: float, hi: float) -> tuple[str, dict]:
    """Submit one Meshy task and poll it to SUCCEEDED. Returns (task_id, model_urls)."""
    sub = client.post(create_url, headers=headers, json=payload)
    if sub.status_code == 402:
        # the remesh and retexture calls are billable — say so instead of leaking a bare 402
        raise RuntimeError(
            f"Meshy {label} needs credits (HTTP 402). This pipeline makes three billable "
            f"calls; the remesh step consumes credits even when it is free in the web UI. "
            f"Top up, or switch to the single-call 'meshy' provider. Body: {sub.text}"
        )
    _raise_for_api(sub, f"{label} submit")
    task_id = (sub.json() or {}).get("result")
    if not task_id:
        raise RuntimeError(f"Meshy {label} returned no task id: {sub.text}")
    while True:
        time.sleep(3)
        poll = client.get(f"{poll_base}/{task_id}", headers=headers)
        _raise_for_api(poll, f"{label} poll")
        data = poll.json() or {}
        status = data.get("status")
        pct = float(data.get("progress", 0) or 0)
        ctx.progress(lo + (hi - lo) * (pct / 100.0), f"{label} ({pct:.0f}%)")
        if status == "SUCCEEDED":
            return task_id, (data.get("model_urls") or {})
        if status in ("FAILED", "CANCELED", "EXPIRED"):
            err = (data.get("task_error") or {}).get("message") or poll.text
            raise RuntimeError(f"Meshy {label} task {task_id} ended as '{status}': {err}")
