"""A human at a given real height and build, made with MPFB2 (MakeHuman for Blender; its output is CC0).

    BLENDER_USER_RESOURCES=<studio profile> blender --background --python mpfb_human.py -- '<json>'

json: {"out": dir, "height_m": 1.75, "gender": 0..1 (0 female, 1 male), "age_years": 25,
       "muscle": 0..1, "weight": 0..1, "proportions": 0..1, "rig": ""|"game_engine"|"mixamo"|"default"}

Writes <out>/model.glb (Y up, metres; skinned when a rig was asked for), front.png, side.png and
meta.json. MPFB's height control is 0..1, not metres, so the value that gives the asked height is
found by bisection; a height beyond the human range is reached by scaling and said so.
"""
import json
import math
import os
import sys

import addon_utils
import bpy
from mathutils import Vector

args = json.loads(sys.argv[sys.argv.index("--") + 1])
out = args["out"]
os.makedirs(out, exist_ok=True)
# An empty stage: the start-up file's cube stood in front of the body's legs in every render.
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
addon_utils.enable("bl_ext.user_default.mpfb", default_set=True)
from bl_ext.user_default.mpfb.services.humanservice import HumanService  # noqa: E402
from bl_ext.user_default.mpfb.services.targetservice import TargetService  # noqa: E402

clamp = lambda v, lo=0.0, hi=1.0: max(lo, min(hi, float(v)))  # noqa: E731


def age_macro(years: float) -> float:
    # MakeHuman's age control: 0 = 1 year, 0.1875 = 11, 0.5 = 25, 1 = 90 (linear between).
    y = max(1.0, min(90.0, float(years)))
    if y <= 11:
        return (y - 1) / 10 * 0.1875
    if y <= 25:
        return 0.1875 + (y - 11) / 14 * 0.3125
    return 0.5 + (y - 25) / 65 * 0.5


def macro(h: float) -> dict:
    m = TargetService.get_default_macro_info_dict()
    m.update(gender=clamp(args.get("gender", 0.5)), age=age_macro(args.get("age_years", 25)), muscle=clamp(args.get("muscle", 0.5)),
             weight=clamp(args.get("weight", 0.5)), proportions=clamp(args.get("proportions", 0.5)), height=clamp(h))
    return m


def tall(o) -> float:
    bpy.context.view_layer.update()
    ev = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
    zs = [(ev.matrix_world @ v.co).z for v in ev.data.vertices]
    return max(zs) - min(zs)


def make(h: float):
    o = HumanService.create_human(macro_detail_dict=macro(h))
    return o, tall(o)


want = float(args.get("height_m", 1.75))
lo, hi = 0.0, 1.0
o, t = make(0.5)
note = ""
for _ in range(9):
    if abs(t - want) < 0.004:
        break
    mid = (lo + hi) / 2
    if t < want:
        lo = mid
    else:
        hi = mid
    bpy.data.objects.remove(o, do_unlink=True)
    o, t = make((lo + hi) / 2)
if abs(t - want) > 0.01:
    # Beyond what the body controls reach (a child's age caps it, a giant is past 2.43 m).
    s = want / t
    o.scale = (o.scale.x * s, o.scale.y * s, o.scale.z * s)
    note = "scaled by %.3f: the asked height is outside the body controls' range" % s
    t = tall(o)

rig_name = str(args.get("rig") or "")
armature = HumanService.add_builtin_rig(o, rig_name) if rig_name else None

# The shape is final: bake the body targets into the mesh and cut the helper geometry away, so
# the file holds exactly the body that was measured.
bpy.context.view_layer.objects.active = o
for ob in bpy.context.view_layer.objects:
    ob.select_set(ob == o)
if o.data.shape_keys:
    bpy.ops.object.shape_key_remove(all=True, apply_mix=True)
for m in list(o.modifiers):
    if m.type == "MASK":
        bpy.ops.object.modifier_apply(modifier=m.name)

for ob in bpy.context.view_layer.objects:
    ob.select_set(ob == o or ob == armature)
bpy.ops.export_scene.gltf(filepath=os.path.join(out, "model.glb"), export_format="GLB", use_selection=True,
                          export_apply=False, export_yup=True, export_materials="NONE", export_animations=False,
                          export_skins=bool(armature))

# Renders, as the human base meshes have them.
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "SINGLE"
scene.display.shading.single_color = (0.78, 0.78, 0.78)
scene.view_settings.view_transform = "Standard"
scene.world = scene.world or bpy.data.worlds.new("w")
scene.world.color = (1, 1, 1)
scene.display.shading.background_type = "WORLD"
scene.render.resolution_x, scene.render.resolution_y = 768, 1024
if armature:
    armature.hide_render = True
bpy.context.view_layer.update()
ev = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
pts = [ev.matrix_world @ v.co for v in ev.data.vertices]
mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
size = mx - mn
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
    scene.render.filepath = os.path.join(out, view + ".png")
    bpy.ops.render.render(write_still=True)

meta = {
    "id": "human/" + os.path.basename(out.rstrip("/\\")), "name": args.get("name") or "human",
    "source": "MPFB2 2.0.18 (MakeHuman for Blender)", "source_url": "https://extensions.blender.org/add-ons/mpfb/",
    "license": "CC0-1.0", "kind": "body", "style": "realistic",
    "sex": "male" if clamp(args.get("gender", 0.5)) >= 0.5 else "female",
    "asked": {k: args.get(k) for k in ("height_m", "gender", "age_years", "muscle", "weight", "proportions", "rig")},
    "dimensions_m": [round(size.x, 4), round(size.z, 4), round(size.y, 4)],
    "triangles": sum(len(p.vertices) - 2 for p in ev.data.polygons),
    "rig": rig_name, "bones": len(armature.data.bones) if armature else 0, "note": note,
    "files": {"model": "model.glb", "front": "front.png", "side": "side.png"},
}
with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=1)
print("HUMAN|" + json.dumps(meta))
