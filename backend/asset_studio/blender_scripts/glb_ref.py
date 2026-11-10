"""Any GLB as a reference: its true size and two clay renders, as the human references have them.

    blender --background --factory-startup --python glb_ref.py -- '{"glb": path, "out": dir}'

Prints REF|{"dimensions_m": [x, y, z], "triangles": n, "animations": [...], "bones": n} (glTF axes:
x width, y height, z depth). Models from a library come at any scale; this is what they measure.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

a = json.loads(sys.argv[sys.argv.index("--") + 1])
os.makedirs(a["out"], exist_ok=True)
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
bpy.ops.import_scene.gltf(filepath=a["glb"])
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
mn, mx, tris = Vector((1e9,) * 3), Vector((-1e9,) * 3), 0
for o in bpy.context.scene.objects:
    if o.type != "MESH":
        continue
    ev = o.evaluated_get(dg)
    m = ev.to_mesh()
    for v in m.vertices:
        w = ev.matrix_world @ v.co
        mn = Vector(map(min, mn, w)); mx = Vector(map(max, mx, w))
    tris += sum(len(p.vertices) - 2 for p in m.polygons)
    ev.to_mesh_clear()
if tris == 0:
    print("REF_ERR|the file holds no mesh")
    sys.exit(0)
size = mx - mn
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "SINGLE"
scene.display.shading.single_color = (0.78, 0.78, 0.78)
scene.view_settings.view_transform = "Standard"
scene.world = scene.world or bpy.data.worlds.new("w")
scene.world.color = (1, 1, 1)
scene.display.shading.background_type = "WORLD"
scene.render.resolution_x, scene.render.resolution_y = 1024, 1024
for o in scene.objects:
    if o.type == "ARMATURE":
        o.hide_render = True
cd = bpy.data.cameras.new("c")
cd.type = "ORTHO"
cam = bpy.data.objects.new("c", cd)
scene.collection.objects.link(cam)
scene.camera = cam
span = max(size.x, size.y, size.z) * 1.1
cd.ortho_scale = span
cd.clip_end = span * 10
c = (mn + mx) / 2
for view, loc, rot in (("front", (c.x, mn.y - span * 2, c.z), (math.pi / 2, 0, 0)),
                       ("side", (mx.x + span * 2, c.y, c.z), (math.pi / 2, 0, math.pi / 2))):
    cam.location = loc
    cam.rotation_euler = rot
    scene.render.filepath = os.path.join(a["out"], view + ".png")
    bpy.ops.render.render(write_still=True)
arm = [o for o in scene.objects if o.type == "ARMATURE"]
print("REF|" + json.dumps({
    "dimensions_m": [round(size.x, 4), round(size.z, 4), round(size.y, 4)], "triangles": tris,
    # The importer names each clip twice ("Armature|Walk" and "Walk"): one name per clip.
    "animations": sorted({act.name.split("|")[-1] for act in bpy.data.actions}), "bones": sum(len(o.data.bones) for o in arm)}))
