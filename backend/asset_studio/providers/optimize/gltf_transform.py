"""Web-optimize a GLB/glTF model with the gltf-transform CLI.

``gltf-transform`` (https://gltf-transform.dev/) is the de-facto tool for shrinking
glTF assets for the web: Draco mesh compression, KTX2/Basis texture compression,
welding, and pruning of unused data. We shell out to its ``optimize`` command when
the CLI is installed (``npm i -g @gltf-transform/cli``).

So the optimize stage is *always* runnable, this provider degrades gracefully: if
the CLI is missing it falls back to a trimesh re-export (a GLB pass-through with no
compression), which at least normalizes the container. The chosen path is recorded
in ``meta['method']`` and the output size is compared against the web budget.
"""
from __future__ import annotations

from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider


class GltfTransformProvider(Provider):
    id = "gltf-transform"
    name = "Optimize (gltf-transform: Draco + KTX2)"
    stage = StageType.optimize
    kind = ProviderKind.local
    requires_key = False
    key_name = None
    description = (
        "Web-optimize a GLB/glTF: Draco mesh compression + KTX2 textures via the "
        "gltf-transform CLI. Falls back to a trimesh GLB re-export when the CLI is absent."
    )
    license_note = "Optimization only; license unchanged."
    commercial_ok = True
    cost_hint = "free"
    homepage = "https://gltf-transform.dev/"
    params = [
        ProviderParam(name="draco", label="Draco mesh compression", type="bool", default=True),
        ProviderParam(name="ktx2", label="KTX2 textures", type="bool", default=True,
                      description="KTX2 texture compression"),
        ProviderParam(name="weld", label="Weld vertices", type="bool", default=True),
        ProviderParam(name="prune", label="Prune unused data", type="bool", default=True),
        ProviderParam(name="texture_size", label="Max texture size", type="int", default=1024,
                      min=256, max=4096, step=256),
        ProviderParam(name="budget_kb", label="Web size budget (KB)", type="int", default=5000,
                      description="web size budget; reported, not enforced"),
    ]

    def is_available(self) -> tuple[bool, str]:
        """Always available — a trimesh re-export covers the no-CLI case.

        We still surface a hint when the CLI is missing so the user knows how to
        unlock the Draco/KTX2 path.
        """
        import shutil

        from ...config import settings

        cmd = settings.get("tools", {}).get("gltf_transform_cmd", "gltf-transform")
        if not shutil.which(cmd):
            return True, "gltf-transform CLI not found; will re-export without compression (npm i -g @gltf-transform/cli)."
        return True, ""

    def run(self, ctx: JobContext) -> list:
        import shutil

        mesh = ctx.first_mesh()
        if not mesh:
            raise RuntimeError("gltf-transform needs an input 3D model (.glb/.gltf).")
        if Path(mesh).suffix.lower() not in (".glb", ".gltf"):
            raise RuntimeError(
                f"gltf-transform only optimizes .glb/.gltf; got '{Path(mesh).suffix}'."
            )

        cmd = ctx.tool("gltf_transform_cmd", "gltf-transform")
        budget_kb = int(ctx.param("budget_kb", 5000))
        out = ctx.out_path(f"{Path(mesh).stem}.opt.glb")

        draco = bool(ctx.param("draco", True))
        ktx2 = bool(ctx.param("ktx2", True))
        weld = bool(ctx.param("weld", True))
        prune = bool(ctx.param("prune", True))
        tex_size = int(ctx.param("texture_size", 1024))

        parent_id = ctx.input_assets[0].id if ctx.input_assets else None
        method: str

        if shutil.which(cmd):
            method = self._run_cli(
                ctx, cmd, mesh, out,
                draco=draco, ktx2=ktx2, weld=weld, prune=prune, tex_size=tex_size,
            )
        else:
            ctx.log("gltf-transform CLI not found — npm i -g @gltf-transform/cli for Draco/KTX2.")
            method = self._run_trimesh(ctx, mesh, out)

        out_kb = round((out.stat().st_size if out.exists() else 0) / 1024.0, 1)
        within_budget = out_kb <= budget_kb
        ctx.log(
            f"Optimized via {method}: {out_kb} KB "
            f"({'within' if within_budget else 'over'} {budget_kb} KB budget)."
        )

        ctx.progress(0.97, "saving asset")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                name=out.name,
                meta={
                    "method": method,
                    "within_budget": within_budget,
                    "budget_kb": budget_kb,
                    "out_kb": out_kb,
                    "draco": draco,
                    "ktx2": ktx2,
                    "texture_size": tex_size,
                },
                license=self.license_note,
                commercial_ok=True,
                parent_id=parent_id,
            )
        ]

    # --- backends ----------------------------------------------------------
    def _run_cli(
        self,
        ctx: JobContext,
        cmd: str,
        mesh: str,
        out: Path,
        *,
        draco: bool,
        ktx2: bool,
        weld: bool,
        prune: bool,
        tex_size: int,
    ) -> str:
        """Run ``gltf-transform optimize`` with the requested flags.

        Falls back to a plain ``draco`` pass if the ``optimize`` subcommand fails
        (e.g. older CLI without it). Returns the method string for metadata.
        """
        import subprocess

        argv = [cmd, "optimize", str(mesh), str(out)]
        if draco:
            argv += ["--compress", "draco"]
        else:
            argv += ["--compress", "false"]
        if ktx2:
            argv += ["--texture-compress", "ktx2"]
        if not weld:
            argv += ["--weld", "false"]
        if not prune:
            argv += ["--prune", "false"]
        argv += ["--texture-size", str(tex_size)]

        ctx.progress(0.25, "gltf-transform optimize")
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0 and out.exists():
            ctx.progress(0.9, "optimized")
            return "gltf-transform"

        ctx.log(
            "gltf-transform optimize failed "
            f"(exit {proc.returncode}); retrying with the draco subcommand."
        )
        ctx.progress(0.45, "gltf-transform draco (fallback)")
        draco_argv = [cmd, "draco", str(mesh), str(out)]
        proc2 = subprocess.run(draco_argv, capture_output=True, text=True, timeout=600)
        if proc2.returncode == 0 and out.exists():
            ctx.progress(0.9, "compressed")
            return "gltf-transform"

        stderr = (proc2.stderr or proc.stderr or "").strip() or "no stderr output"
        raise RuntimeError(f"gltf-transform failed: {stderr}")

    def _run_trimesh(self, ctx: JobContext, mesh: str, out: Path) -> str:
        """Re-export the model to GLB via trimesh (no compression)."""
        import trimesh

        ctx.progress(0.3, "loading mesh (trimesh fallback)")
        scene = trimesh.load(str(mesh), force="scene")
        ctx.progress(0.7, "re-exporting GLB")
        scene.export(str(out))
        if not out.exists():
            raise RuntimeError("trimesh re-export produced no output file.")
        ctx.progress(0.9, "re-exported")
        return "trimesh-passthrough"
