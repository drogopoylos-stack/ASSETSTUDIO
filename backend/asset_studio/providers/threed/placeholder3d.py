"""Procedural 3D generator — the zero-dependency default for the 3D stage.

Builds a deterministic low-poly GLB from primitives (no GPU, no model weights) so
the 3D → texture → rig → optimize → QA pipeline and the agent loop run anywhere.
Swap to TRELLIS / Hunyuan3D / Tripo / Meshy from the dropdown for real meshes.
"""
from __future__ import annotations

import hashlib
import random

import numpy as np

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify


def _seed(prompt, seed) -> int:
    if seed not in (None, "", -1, "-1"):
        try:
            return int(seed)
        except (TypeError, ValueError):
            pass
    return int(hashlib.sha256(str(prompt).encode()).hexdigest(), 16) % (2**31)


class Placeholder3DProvider(Provider):
    id = "placeholder-3d"
    name = "Procedural Mesh (no-key)"
    stage = StageType.gen3d
    kind = ProviderKind.local
    requires_key = False
    description = "Deterministic low-poly GLB from primitives. Zero deps; always available; ideal for pipeline/agent tests."
    license_note = "Generated locally — fully owned, commercial use OK."
    commercial_ok = True
    cost_hint = "free"
    params = [
        ProviderParam(name="prompt", label="Prompt", type="text", default="prop"),
        ProviderParam(name="seed", label="Seed (-1 = from prompt)", type="seed", default=-1),
        ProviderParam(name="complexity", label="Parts", type="int", default=5, min=1, max=14),
    ]

    def run(self, ctx: JobContext) -> list:
        import trimesh

        prompt = str(ctx.param("prompt", "prop"))
        seed = _seed(prompt, ctx.param("seed", -1))
        rng = random.Random(seed)
        parts = int(ctx.param("complexity", 5))

        ctx.progress(0.25, "assembling primitives")
        meshes = []
        for i in range(parts):
            kind = rng.choice(["box", "sphere", "cyl", "ico"])
            if kind == "box":
                m = trimesh.creation.box(extents=[rng.uniform(0.3, 1.0) for _ in range(3)])
            elif kind == "sphere":
                m = trimesh.creation.icosphere(subdivisions=1, radius=rng.uniform(0.2, 0.6))
            elif kind == "cyl":
                m = trimesh.creation.cylinder(radius=rng.uniform(0.1, 0.4), height=rng.uniform(0.4, 1.2), sections=12)
            else:
                m = trimesh.creation.icosphere(subdivisions=0, radius=rng.uniform(0.25, 0.6))
            m.apply_translation([rng.uniform(-0.8, 0.8) for _ in range(3)])
            r, g, b = (rng.randint(40, 230) for _ in range(3))
            m.visual.face_colors = np.array([r, g, b, 255], dtype=np.uint8)
            meshes.append(m)

        ctx.progress(0.6, "merging + welding")
        combined = trimesh.util.concatenate(meshes)
        try:
            combined.merge_vertices()
        except Exception:
            pass  # scipy-optional; concatenated mesh is still valid

        out = ctx.out_path(f"{slugify(prompt)}-{seed}.glb")
        combined.export(out)

        # preview render via the built-in software renderer
        ctx.progress(0.85, "rendering preview")
        preview = None
        try:
            from ...render.software_render import render_views

            frames = render_views(out, n=1, size=384)
            ppath = ctx.out_path(f"{slugify(prompt)}-{seed}.preview.png")
            frames[0].save(ppath, "PNG")
            preview = str(ppath)
        except Exception as e:
            ctx.log(f"preview render failed: {e}")

        return [
            ctx.make_asset(
                path=out, type=AssetType.model, prompt=prompt, seed=seed,
                preview_path=preview,
                meta={"engine": "procedural", "parts": parts,
                      "polys": int(len(combined.faces)), "vertices": int(len(combined.vertices))},
                license=self.license_note, commercial_ok=True,
            )
        ]
