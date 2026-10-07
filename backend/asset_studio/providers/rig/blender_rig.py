"""Blender headless auto-rig — turn a static mesh into a rigged model for free.

Drives a local Blender install in ``--background`` mode with a generated Python
script that imports the mesh, builds a simple armature (root + spine bones),
parents the mesh to it with automatic weights, and exports a GLB (or FBX).

Two methods are offered:

* ``auto_weights`` (default, dependable): a hand-built skeleton + Blender's
  ``ARMATURE_AUTO`` automatic weighting. Works on any vanilla Blender.
* ``rigify``: requests Blender's bundled **Rigify** addon for a full humanoid
  metarig. This only works if the Rigify addon is enabled in your Blender
  preferences; the script falls back to ``auto_weights`` if it is not available.

No GPU and no API key required — the output is your own rig, commercial-OK.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

# The bpy script run inside Blender. Paths/options are passed as argv after "--".
_BPY_SCRIPT = r'''
import sys, bpy

argv = sys.argv[sys.argv.index("--") + 1:]
in_path, out_path, fmt, method = argv[0], argv[1], argv[2], argv[3]
ext = in_path.lower().rsplit(".", 1)[-1]

# --- clean slate ----------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)

# --- import the mesh ------------------------------------------------------
if ext in ("glb", "gltf"):
    bpy.ops.import_scene.gltf(filepath=in_path)
elif ext == "obj":
    try:
        bpy.ops.wm.obj_import(filepath=in_path)          # Blender >= 3.3
    except Exception:
        bpy.ops.import_scene.obj(filepath=in_path)       # legacy
elif ext == "fbx":
    bpy.ops.import_scene.fbx(filepath=in_path)
elif ext == "stl":
    bpy.ops.import_mesh.stl(filepath=in_path)
elif ext == "ply":
    try:
        bpy.ops.wm.ply_import(filepath=in_path)
    except Exception:
        bpy.ops.import_mesh.ply(filepath=in_path)
else:
    raise RuntimeError("Unsupported mesh extension: " + ext)

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise RuntimeError("No mesh objects found after import.")

# join all imported meshes into one object for clean parenting
for o in bpy.context.scene.objects:
    o.select_set(False)
for m in meshes:
    m.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
mesh = bpy.context.view_layer.objects.active

# fit the skeleton to the mesh bounds
zs = [(mesh.matrix_world @ v.co).z for v in mesh.data.vertices]
zmin, zmax = (min(zs), max(zs)) if zs else (0.0, 1.0)
height = max(zmax - zmin, 1e-4)


def build_basic_armature():
    bpy.ops.object.armature_add(enter_editmode=True, location=(0, 0, zmin))
    arm = bpy.context.object
    arm.name = "AutoRig"
    eb = arm.data.edit_bones
    eb.remove(eb[0])  # drop the default bone
    root = eb.new("root")
    root.head = (0, 0, 0)
    root.tail = (0, 0, height * 0.15)
    hips = eb.new("spine")
    hips.head = root.tail
    hips.tail = (0, 0, height * 0.55)
    hips.parent = root
    chest = eb.new("chest")
    chest.head = hips.tail
    chest.tail = (0, 0, height * 0.85)
    chest.parent = hips
    head = eb.new("head")
    head.head = chest.tail
    head.tail = (0, 0, height * 1.0)
    head.parent = chest
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


arm = None
if method == "rigify":
    try:
        import addon_utils
        addon_utils.enable("rigify", default_set=True, persistent=True)
        bpy.ops.object.armature_human_metarig_add()
        arm = bpy.context.object
        arm.name = "AutoRig"
        # scale the metarig to roughly match the mesh height
        arm.location = (0, 0, zmin)
        arm.scale = (height, height, height)
        bpy.ops.object.transform_apply(scale=True, location=False, rotation=False)
    except Exception as exc:  # Rigify addon missing/disabled -> dependable fallback
        print("Rigify unavailable (%s); using auto_weights." % exc)
        arm = None
if arm is None:
    arm = build_basic_armature()

# --- parent mesh to armature with automatic weights -----------------------
for o in bpy.context.scene.objects:
    o.select_set(False)
mesh.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type="ARMATURE_AUTO")

# --- export ---------------------------------------------------------------
if fmt == "fbx":
    bpy.ops.export_scene.fbx(filepath=out_path, add_leaf_bones=False)
else:
    bpy.ops.export_scene.gltf(filepath=out_path, export_format="GLB")
print("RIG_EXPORT_OK", out_path)
'''


class BlenderRigProvider(Provider):
    """Headless Blender auto-rigging.

    ``auto_weights`` is the reliable default; ``rigify`` needs Blender's Rigify
    addon enabled (otherwise the bpy script falls back to ``auto_weights``).
    """

    id = "blender-rig"
    name = "Blender Auto-Rig (headless)"
    stage = StageType.rig
    kind = ProviderKind.local
    requires_key = False
    key_name = None
    description = (
        "Rig a mesh locally with headless Blender: builds an armature, binds it with "
        "automatic weights, and exports a GLB/FBX. Free, no GPU, no key."
    )
    license_note = "Your own rig — commercial OK."
    commercial_ok = True
    cost_hint = "free"
    homepage = "https://www.blender.org/"
    params = [
        ProviderParam(
            name="method", label="Method", type="select",
            options=["auto_weights", "rigify"], default="auto_weights",
            description="auto_weights is dependable everywhere; rigify needs the Rigify addon enabled.",
        ),
        ProviderParam(
            name="export", label="Export format", type="select",
            options=["glb", "fbx"], default="glb",
        ),
    ]

    def is_available(self) -> tuple[bool, str]:
        import shutil

        blender = getattr(self, "_blender_path", "blender")
        if shutil.which(blender) or Path(str(blender)).exists():
            return True, ""
        return False, "Set tools.blender_path to your Blender executable."

    def run(self, ctx: JobContext) -> list:
        import shutil

        mesh = ctx.first_mesh()
        if not mesh:
            raise RuntimeError("Blender auto-rig needs an input 3D model (.glb/.gltf/.obj/.fbx/...).")

        blender = ctx.tool("blender_path", "blender")
        if not (shutil.which(blender) or Path(str(blender)).exists()):
            raise RuntimeError(
                "Blender not found. Set tools.blender_path to your Blender executable."
            )

        method = str(ctx.param("method", "auto_weights"))
        fmt = str(ctx.param("export", "glb")).lower()
        if fmt not in ("glb", "fbx"):
            fmt = "glb"

        stem = Path(mesh).stem
        out = ctx.out_path(f"{stem}.rigged.{fmt}")
        script = ctx.out_path("rig_job.py")
        script.write_text(_BPY_SCRIPT, encoding="utf-8")

        cmd = [
            str(blender), "--background", "--python", str(script),
            "--", str(Path(mesh).resolve()), str(out.resolve()), fmt, method,
        ]
        ctx.log(f"Running Blender: {' '.join(cmd)}")
        ctx.progress(0.2, f"rigging with Blender ({method})")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Blender rig timed out after 600s.") from exc

        if proc.returncode != 0 or not out.exists():
            tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
            raise RuntimeError(f"Blender rig failed (exit {proc.returncode}):\n{tail}")

        ctx.progress(0.95, "saving rigged model")
        return [
            ctx.make_asset(
                path=out,
                type=AssetType.model,
                name=out.name,
                meta={"engine": "blender", "method": method, "rigged": True,
                      "of_model": Path(mesh).name},
                parent_id=ctx.input_assets[0].id if ctx.input_assets else None,
                license=self.license_note,
                commercial_ok=True,
            )
        ]
