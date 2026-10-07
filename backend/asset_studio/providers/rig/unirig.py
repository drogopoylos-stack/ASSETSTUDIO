"""UniRig — VAST-AI's open-source automatic-rigging model, run as a local CLI.

UniRig predicts a skeleton + skinning weights for an arbitrary mesh. It is not a
pip-installable library you can call in-process here; instead the studio shells
out to your local UniRig checkout. Point ``tools.unirig_path`` at either:

* the repo's launcher / CLI script (e.g. ``.../UniRig/launch/inference.sh`` or a
  ``run.py``), or
* a wrapper executable that accepts ``--input <mesh> --output <glb>``.

Expected invocation (matches UniRig's inference entry point)::

    <unirig_path> --input <mesh.glb> --output <out.glb>

If the tool emits a different file, the newest *.glb/*.fbx written under the job
workdir is picked up. Configure ``tools.unirig_path`` in Settings → Tools first.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider


class UniRigProvider(Provider):
    """Auto-rig a mesh with a local UniRig install (CLI handoff)."""

    id = "unirig"
    name = "UniRig (auto-rigging)"
    stage = StageType.rig
    kind = ProviderKind.local
    requires_key = False
    key_name = None
    description = (
        "Run VAST-AI's UniRig auto-rigging model on a mesh via your local UniRig CLI. "
        "Predicts a skeleton and skinning weights, exporting a rigged model."
    )
    license_note = "Review UniRig license."
    commercial_ok = True
    cost_hint = "free"
    homepage = "https://github.com/VAST-AI-Research/UniRig"
    params = [
        ProviderParam(
            name="export", label="Export format", type="select",
            options=["glb", "fbx"], default="glb",
        ),
    ]

    def is_available(self) -> tuple[bool, str]:
        path = getattr(self, "_unirig_path", "")
        if path and Path(str(path)).exists():
            return True, ""
        return False, "Set tools.unirig_path to your UniRig install/CLI."

    def run(self, ctx: JobContext) -> list:
        mesh = ctx.first_mesh()
        if not mesh:
            raise RuntimeError("UniRig needs an input 3D model (.glb/.gltf/.obj/.fbx/...).")

        path = ctx.tool("unirig_path", "")
        if not path or not Path(str(path)).exists():
            raise RuntimeError(
                "UniRig is not configured. Clone https://github.com/VAST-AI-Research/UniRig "
                "and set tools.unirig_path to its inference launcher/CLI."
            )

        fmt = str(ctx.param("export", "glb")).lower()
        if fmt not in ("glb", "fbx"):
            fmt = "glb"
        stem = Path(mesh).stem
        out = ctx.out_path(f"{stem}.unirig.{fmt}")

        cmd = [str(path), "--input", str(Path(mesh).resolve()), "--output", str(out.resolve())]
        ctx.log(f"Running UniRig: {' '.join(cmd)}")
        ctx.progress(0.2, "auto-rigging with UniRig")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Could not launch UniRig at '{path}'. Check tools.unirig_path."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("UniRig timed out after 600s.") from exc

        # Prefer the requested output; otherwise grab the newest rig the tool wrote.
        result = out if out.exists() else _newest_rig(ctx.workdir)
        if proc.returncode != 0 or result is None:
            tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
            raise RuntimeError(f"UniRig failed (exit {proc.returncode}):\n{tail}")

        ctx.progress(0.95, "saving rigged model")
        return [
            ctx.make_asset(
                path=result,
                type=AssetType.model,
                name=result.name,
                meta={"engine": "unirig", "rigged": True, "of_model": Path(mesh).name},
                parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                license=self.license_note,
                commercial_ok=True,
            )
        ]


def _newest_rig(workdir: Path) -> Path | None:
    """Newest .glb/.fbx written under the job workdir (UniRig output fallback)."""
    cands = [p for ext in ("*.glb", "*.fbx") for p in workdir.glob(ext)]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)
