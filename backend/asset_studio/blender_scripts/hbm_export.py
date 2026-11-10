"""Export every asset of Blender Studio's Human Base Meshes bundle (CC0) as a reference.

    blender --background --factory-startup <bundle.blend> --python hbm_export.py -- <out dir>

For each asset: <slug>/model.glb (Y up, metres, modifiers applied), <slug>/front.png and
<slug>/side.png (orthographic clay renders on white, the silhouette a forge aim wants), and
<slug>/meta.json (real size, triangles, licence). Prints one REF| line per asset.
"""
import json
import math
import os
import re
import sys

import bpy
from mathutils import Vector

out = sys.argv[sys.argv.index("--") + 1]
MAX_TRIS = 200_000
os.makedirs(out, exist_ok=True)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def bounds(objs):
    mn, mx = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    for o in objs:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            mn = Vector(map(min, mn, w)); mx = Vector(map(max, mx, w))
    return mn, mx


assets = [("collection", c) for c in bpy.data.collections if c.asset_data] + \
         [("object", o) for o in bpy.data.objects if o.asset_data]

# A clean stage: one scene that only ever holds the asset being exported, a camera and the light.
stage = bpy.data.scenes.new("ref_stage")
stage.render.engine = "BLENDER_WORKBENCH"
stage.display.shading.light = "STUDIO"
stage.display.shading.color_type = "SINGLE"
stage.display.shading.single_color = (0.78, 0.78, 0.78)
stage.render.film_transparent = False
# White must come out white: the default view transform greys it, and a grey body on grey is a
# weak silhouette for the forge's comparison.
stage.view_settings.view_transform = "Standard"
stage.world = bpy.data.worlds.new("ref_world")
stage.world.color = (1, 1, 1)
stage.display.shading.background_type = "WORLD"
stage.render.resolution_x, stage.render.resolution_y = 768, 1024
cam_data = bpy.data.cameras.new("ref_cam")
cam_data.type = "ORTHO"
cam = bpy.data.objects.new("ref_cam", cam_data)
stage.collection.objects.link(cam)
stage.camera = cam
bpy.context.window.scene = stage

for kind, it in assets:
    name = it.name.strip()
    s = slug(name)
    d = os.path.join(out, s)
    os.makedirs(d, exist_ok=True)
    if kind == "collection":
        stage.collection.children.link(it)
        objs = [o for o in it.all_objects]
    else:
        stage.collection.objects.link(it)
        objs = [it]
    # Hidden helpers (a mirror empty, a cage) stay out: only what is shown is the reference.
    shown = [o for o in objs if o.type == "MESH" and not o.hide_render and not o.hide_get()]
    mn, mx = bounds(shown)
    size = mx - mn
    # A reference, not a sculpt: Multires and Subdivision come down a level at a time until the
    # asset is at most MAX_TRIS, so the bench loads it in a moment. (Full level: 1.4 M, 41 MB.)
    dg = bpy.context.evaluated_depsgraph_get()

    def count():
        bpy.context.view_layer.update()
        n = 0
        for o in shown:
            ev = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
            n += sum(len(p.vertices) - 2 for p in ev.data.polygons)
        return n
    while count() > MAX_TRIS:
        mods = [m for o in shown for m in o.modifiers if m.type in ("MULTIRES", "SUBSURF") and m.levels > 0]
        if not mods:
            break
        top = max(mods, key=lambda m: m.levels)
        top.levels -= 1
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in shown:
        o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(d, "model.glb"), export_format="GLB", use_selection=True,
                              export_apply=True, export_yup=True, export_materials="NONE", export_animations=False)
    tris = 0
    dg = bpy.context.evaluated_depsgraph_get()
    for o in shown:
        m = o.evaluated_get(dg).to_mesh()
        tris += sum(len(p.vertices) - 2 for p in m.polygons)
        o.evaluated_get(dg).to_mesh_clear()
    # Two views: from the front (looking along +Y, Blender's front) and from the side (along -X).
    c = (mn + mx) / 2
    span = max(size.x, size.y, size.z) * 1.1
    cam_data.ortho_scale = span
    for view, loc, rot in (("front", (c.x, mn.y - span * 2, c.z), (math.pi / 2, 0, 0)),
                           ("side", (mx.x + span * 2, c.y, c.z), (math.pi / 2, 0, math.pi / 2))):
        cam.location = loc
        cam.rotation_euler = rot
        cam_data.clip_end = span * 10
        stage.render.filepath = os.path.join(d, view + ".png")
        bpy.ops.render.render(write_still=True, scene=stage.name)
    style = next((w for w in ("realistic", "stylized", "primitive", "planar") if w in name.lower()), "")
    part = next((w for w in ("body", "head", "hand", "foot", "eye", "jaw", "skull") if w in name.lower()), "")
    sex = "female" if "female" in name.lower() else "male" if "male" in name.lower() else ""
    meta = {
        "id": "hbm/" + s, "name": name, "source": "Blender Studio Human Base Meshes v1.2",
        "source_url": "https://www.blender.org/download/demo-files/", "license": "CC0-1.0",
        "kind": part, "style": style, "sex": sex,
        # glTF axes: x width, y height, z depth.
        "dimensions_m": [round(size.x, 4), round(size.z, 4), round(size.y, 4)],
        "triangles": tris, "files": {"model": "model.glb", "front": "front.png", "side": "side.png"},
    }
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print("REF|%s|%s|%s|%d" % (s, name, meta["dimensions_m"], tris))
    if kind == "collection":
        stage.collection.children.unlink(it)
    else:
        stage.collection.objects.unlink(it)
print("REF_DONE|%d" % len(assets))
