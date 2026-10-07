"""TRELLIS / TRELLIS2 image-to-3D.

Two ways to run it (no 16 GB GPU required for the hosted path):

  * HOSTED (recommended on low-VRAM machines): point at a HuggingFace TRELLIS
    Space and we drive it with ``gradio_client``. Set ``tools.trellis_space`` to
    the Space id (e.g. ``"JeffreyXiang/TRELLIS"``) and add a free HuggingFace token
    in Settings → API Keys as ``huggingface``. Runs on the Space's GPU.

  * LOCAL SERVER: if you run a TRELLIS Gradio server yourself, leave
    ``trellis_space`` blank and set ``tools.trellis_url`` (default
    ``http://127.0.0.1:7860``); we POST to its Gradio predict endpoint.

The hosted flow targets the official TRELLIS Space layout
(start_session → preprocess_image → image_to_3d → extract_glb); forks may expose
slightly different api_names — adjust ``_HOSTED_*`` below if needed.
"""
from __future__ import annotations

from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify

_ENDPOINTS = ("/run/predict", "/api/predict")  # local-server fallbacks


class TrellisProvider(Provider):
    id = "trellis"
    name = "TRELLIS / TRELLIS2 (image→3D)"
    stage = StageType.gen3d
    kind = ProviderKind.local
    requires_key = False
    key_name = "huggingface"
    description = (
        "Single image → textured 3D mesh. Use a hosted HuggingFace TRELLIS Space "
        "(set tools.trellis_space + a huggingface token) — works on 8 GB GPUs since it "
        "runs on the Space's GPU — or your own local TRELLIS server (tools.trellis_url)."
    )
    license_note = "Review the TRELLIS model license for commercial use."
    commercial_ok = None
    cost_hint = "free (hosted) / free (local)"
    homepage = "https://github.com/microsoft/TRELLIS"
    params = [
        ProviderParam(name="seed", label="Seed (-1 = random)", type="seed", default=-1),
        ProviderParam(name="ss_steps", label="Sparse-structure steps", type="int", default=12, min=1, max=50),
        ProviderParam(name="slat_steps", label="Structured-latent steps", type="int", default=12, min=1, max=50),
        ProviderParam(name="mesh_simplify", label="Mesh simplify", type="float", default=0.95, min=0.9, max=0.98, step=0.01),
        ProviderParam(name="texture_size", label="Texture size", type="select", options=["1024", "2048"], default="1024"),
    ]

    # --- config ------------------------------------------------------------
    def _space(self) -> str:
        from ...config import settings
        return str(settings.get("tools", {}).get("trellis_space", "") or "").strip()

    def _base(self, ctx: JobContext) -> str:
        return str(ctx.tool("trellis_url", "http://127.0.0.1:7860")).rstrip("/")

    # --- availability ------------------------------------------------------
    def is_available(self) -> tuple[bool, str]:
        space = self._space()
        if space:
            try:
                import gradio_client  # noqa: F401
            except Exception:
                return False, "pip install gradio_client to use a hosted TRELLIS Space."
            if not self.has_key():
                return False, f"Hosted TRELLIS ready ({space}) — add a free 'huggingface' token in Settings → API Keys."
            return True, ""
        from ...config import settings
        base = str(settings.get("tools", {}).get("trellis_url", "http://127.0.0.1:7860")).rstrip("/")
        reason = (f"Set tools.trellis_space to a HuggingFace TRELLIS Space (recommended for 8 GB GPUs) "
                  f"or start a local TRELLIS server at {base}.")
        try:
            import httpx
            with httpx.Client(timeout=1.5) as client:
                for path in ("", "/config"):
                    try:
                        if client.get(base + path).status_code < 500:
                            return True, ""
                    except httpx.HTTPError:
                        continue
        except Exception:
            return False, reason
        return False, reason

    # --- dispatch ----------------------------------------------------------
    def run(self, ctx: JobContext) -> list:
        if self._space():
            return self._run_hosted(ctx)
        return self._run_local(ctx)

    # --- hosted (HuggingFace Space via gradio_client) ----------------------
    def _run_hosted(self, ctx: JobContext) -> list:
        from gradio_client import Client, handle_file

        from ... import keychain

        image = ctx.first_image()
        if not image:
            raise RuntimeError("TRELLIS is image-conditioned: provide an input image.")
        space = self._space()
        token = keychain.get_key("huggingface")

        ctx.progress(0.08, f"connecting to {space}")
        client = Client(space, hf_token=token or None)
        seed = int(ctx.param("seed", -1))
        seed = 0 if seed < 0 else seed
        ss = int(ctx.param("ss_steps", 12))
        slat = int(ctx.param("slat_steps", 12))
        simplify = float(ctx.param("mesh_simplify", 0.95))
        tex = int(ctx.param("texture_size", 1024))

        try:
            client.predict(api_name="/start_session")
        except Exception:
            pass

        ctx.progress(0.25, "preprocessing image")
        try:
            processed = client.predict(handle_file(image), api_name="/preprocess_image")
        except Exception as e:
            ctx.log(f"preprocess_image unavailable ({e}); passing raw image")
            processed = handle_file(image)

        ctx.progress(0.4, "generating 3D (may queue on the Space)")
        try:
            client.predict(
                processed, [], False, seed, 7.5, ss, 3.0, slat, "stochastic",
                api_name="/image_to_3d",
            )
        except Exception as e:
            # fallback: keyword call for forks with a different signature
            try:
                client.predict(image=processed, seed=seed, api_name="/image_to_3d")
            except Exception:
                raise RuntimeError(
                    f"TRELLIS Space '/image_to_3d' call failed: {e}. The Space's API may differ — "
                    f"check its 'Use via API' panel and adjust _run_hosted()."
                )

        ctx.progress(0.8, "extracting GLB")
        try:
            glb = client.predict(simplify, tex, api_name="/extract_glb")
        except Exception:
            glb = client.predict(api_name="/extract_glb")
        glb_path = self._extract_path(glb)
        if not glb_path or not Path(glb_path).exists():
            raise RuntimeError(f"TRELLIS Space returned no GLB file. Got: {str(glb)[:400]}")

        out = ctx.out_path(f"trellis-{ctx.job.id}.glb")
        out.write_bytes(Path(glb_path).read_bytes())
        ctx.progress(0.95, "saving asset")
        parent = ctx.input_assets[0].id if ctx.input_assets else None
        return [ctx.make_asset(path=out, type=AssetType.model, seed=seed, parent_id=parent,
                               meta={"engine": "trellis", "mode": "hf_space", "space": space},
                               license=self.license_note, commercial_ok=self.commercial_ok)]

    @staticmethod
    def _extract_path(result):
        def walk(n):
            if isinstance(n, str):
                return n if n.lower().endswith((".glb", ".gltf")) else None
            if isinstance(n, dict):
                for k in ("path", "value", "url", "name"):
                    v = n.get(k)
                    if isinstance(v, str) and v.lower().endswith((".glb", ".gltf")):
                        return v
                for v in n.values():
                    h = walk(v)
                    if h:
                        return h
            if isinstance(n, (list, tuple)):
                for v in n:
                    h = walk(v)
                    if h:
                        return h
            return None
        return walk(result)

    # --- local server (Gradio HTTP) ---------------------------------------
    def _run_local(self, ctx: JobContext) -> list:
        import httpx

        image = ctx.first_image()
        if not image:
            raise RuntimeError("TRELLIS is image-conditioned: provide an input image.")
        base = self._base(ctx)
        seed = int(ctx.param("seed", -1))
        ss_steps = int(ctx.param("ss_steps", 12))
        slat_steps = int(ctx.param("slat_steps", 12))

        ctx.progress(0.1, "uploading image to TRELLIS")
        with httpx.Client(timeout=600) as client:
            file_url = self._gradio_upload(client, base, image, ctx)
            file_ref = {"name": Path(image).name, "data": None}
            if file_url:
                file_ref = {"path": file_url, "url": base + "/file=" + file_url,
                            "orig_name": Path(image).name, "meta": {"_type": "gradio.FileData"}}
            payload = {"data": [file_ref, "", seed, ss_steps, slat_steps, True]}
            ctx.progress(0.3, "generating 3D mesh")
            resp, last_err = None, ""
            for path in _ENDPOINTS:
                try:
                    r = client.post(base + path, json=payload)
                except httpx.HTTPError as e:
                    last_err = str(e); continue
                if r.status_code == 404:
                    last_err = f"{path} -> 404"; continue
                resp = r; break
            if resp is None or resp.status_code >= 400:
                raise RuntimeError(f"TRELLIS local server request failed ({last_err or (resp and resp.status_code)}).")
            glb_ref = self._extract_path(resp.json())
            if not glb_ref:
                raise RuntimeError(f"No GLB in TRELLIS response: {resp.text[:400]}")
            ctx.progress(0.8, "downloading GLB")
            out = ctx.out_path(f"trellis-{ctx.job.id}.glb")
            self._download_glb(client, base, glb_ref, out)
        parent = ctx.input_assets[0].id if ctx.input_assets else None
        return [ctx.make_asset(path=out, type=AssetType.model, parent_id=parent,
                               meta={"engine": "trellis", "mode": "local", "server": base},
                               license=self.license_note, commercial_ok=self.commercial_ok)]

    def _gradio_upload(self, client, base, image, ctx):
        import httpx
        try:
            with open(image, "rb") as fh:
                r = client.post(base + "/upload", files={"files": (Path(image).name, fh, "application/octet-stream")})
            if r.status_code < 400:
                data = r.json()
                if isinstance(data, list) and data:
                    return data[0]
        except (httpx.HTTPError, ValueError) as e:
            ctx.log(f"TRELLIS upload route unavailable ({e})")
        return None

    def _download_glb(self, client, base, ref, out):
        if ref.startswith(("http://", "https://")):
            url = ref
        else:
            local = Path(ref)
            if local.exists():
                out.write_bytes(local.read_bytes()); return
            url = base + "/file=" + ref.lstrip("/")
        r = client.get(url)
        if r.status_code >= 400:
            raise RuntimeError(f"Failed to download GLB [{r.status_code}]")
        out.write_bytes(r.content)
