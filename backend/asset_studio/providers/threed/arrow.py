"""Medieval low-poly arrow generator — colorful, kid-friendly 3D arrows.

Procedurally builds a real arrow (shaft + head + 3 fletchings + nock) from
primitives with per-part colors, exports a GLB, and renders a preview. No GPU,
no model weights — runs anywhere. Great for low-poly stylized game props.
"""
from __future__ import annotations

import numpy as np

from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider, slugify

PALETTE = {
    "red": (226, 58, 58), "blue": (56, 120, 235), "green": (60, 190, 90),
    "yellow": (245, 205, 50), "orange": (245, 140, 40), "purple": (150, 90, 220),
    "pink": (240, 120, 180), "cyan": (60, 200, 210), "white": (235, 238, 245),
    "silver": (200, 205, 215), "gold": (235, 200, 80), "bronze": (190, 120, 70),
    "steel": (150, 160, 175), "wood": (140, 95, 55), "darkwood": (95, 62, 38),
    "tan": (175, 135, 90),
}


def _col(v, default):
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return tuple(int(x) for x in v[:3])
    s = str(v).strip().lower()
    if s in PALETTE:
        return PALETTE[s]
    if s.startswith("#") and len(s) >= 7:
        try:
            return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    return default


def _paint(mesh, color):
    mesh.visual.face_colors = np.array(list(color) + [255], dtype=np.uint8)
    return mesh


def _cyl(r, z0, z1, sections, color):
    import trimesh

    m = trimesh.creation.cylinder(radius=r, height=max(z1 - z0, 1e-4), sections=sections)
    m.apply_translation([0, 0, (z0 + z1) / 2.0])
    return _paint(m, color)


def _cone(r, z0, z1, sections, color, flat=1.0):
    import trimesh

    m = trimesh.creation.cone(radius=r, height=max(z1 - z0, 1e-4), sections=sections)
    if flat != 1.0:
        m.apply_scale([1.0, flat, 1.0])
    m.apply_translation([0, 0, z0])
    return _paint(m, color)


def build_arrow(head_type, shaft_c, head_c, fletch_c, length=1.0, low_poly=True):
    import trimesh

    s = 6 if low_poly else 20
    L = float(length)
    r = 0.012 * L
    parts = []

    shaft_z1 = 0.80 * L
    parts.append(_cyl(r, 0.0, shaft_z1, s, shaft_c))

    if head_type == "bodkin":
        parts.append(_cone(r * 1.7, shaft_z1, 1.00 * L, s, head_c))
    elif head_type == "broadhead":
        parts.append(_cone(r * 4.2, shaft_z1, 0.99 * L, s, head_c, flat=0.28))
    elif head_type == "leaf":
        parts.append(_cone(r * 3.0, shaft_z1, 0.99 * L, s, head_c, flat=0.55))
    elif head_type == "blunt":
        parts.append(_cyl(r * 2.2, shaft_z1, 0.93 * L, s, head_c))
        cap = trimesh.creation.icosphere(subdivisions=0 if low_poly else 1, radius=r * 2.2)
        cap.apply_translation([0, 0, 0.93 * L])
        parts.append(_paint(cap, head_c))
    else:
        parts.append(_cone(r * 2.4, shaft_z1, 0.99 * L, s, head_c))

    # nock ring at the back
    parts.append(_cyl(r * 1.25, -0.012 * L, 0.012 * L, s, shaft_c))

    # 3 fletchings (feathers) at 120°
    fin_z0, fin_z1 = 0.04 * L, 0.22 * L
    for i in range(3):
        ang = i * 2 * np.pi / 3
        fin = trimesh.creation.box(extents=[0.07 * L, 0.006 * L, (fin_z1 - fin_z0)])
        # taper to a feather: scale the front (high-z) end narrower in x
        v = fin.vertices.copy()
        zr = (v[:, 2] - v[:, 2].min()) / max(float(np.ptp(v[:, 2])), 1e-6)  # 0 at back, 1 at front
        v[:, 0] *= (1.0 - 0.65 * zr)
        fin.vertices = v
        fin.apply_translation([r + 0.035 * L, 0, (fin_z0 + fin_z1) / 2.0])
        fin.apply_transform(trimesh.transformations.rotation_matrix(ang, [0, 0, 1]))
        parts.append(_paint(fin, fletch_c))

    arrow = trimesh.util.concatenate(parts)
    # lie horizontally (along X) so turntables read nicely
    arrow.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [0, 1, 0]))
    return arrow


class MedievalArrowProvider(Provider):
    id = "arrow-medieval"
    name = "Medieval Arrow (low-poly)"
    stage = StageType.gen3d
    kind = ProviderKind.local
    requires_key = False
    cost_hint = "free"
    commercial_ok = True
    description = "Procedural colorful low-poly medieval arrows (shaft + head + fletching). Kid-friendly; no GPU."
    license_note = "Generated locally — fully owned, commercial use OK."
    homepage = ""
    params = [
        ProviderParam(name="prompt", label="Name/notes", type="text", default="medieval arrow"),
        ProviderParam(name="head_type", label="Head", type="select",
                      options=["broadhead", "bodkin", "leaf", "blunt"], default="broadhead"),
        ProviderParam(name="shaft_color", label="Shaft color", type="select",
                      options=list(PALETTE.keys()), default="wood"),
        ProviderParam(name="head_color", label="Head color", type="select",
                      options=list(PALETTE.keys()), default="silver"),
        ProviderParam(name="fletch_color", label="Feather color", type="select",
                      options=list(PALETTE.keys()), default="red"),
        ProviderParam(name="length", label="Length", type="float", default=1.0, min=0.3, max=2.0, step=0.1),
        ProviderParam(name="low_poly", label="Low poly", type="bool", default=True),
    ]

    def run(self, ctx: JobContext) -> list:
        ht = ctx.param("head_type", "broadhead")
        sc = _col(ctx.param("shaft_color", "wood"), PALETTE["wood"])
        hc = _col(ctx.param("head_color", "silver"), PALETTE["silver"])
        fc = _col(ctx.param("fletch_color", "red"), PALETTE["red"])
        L = float(ctx.param("length", 1.0))
        lp = bool(ctx.param("low_poly", True))

        ctx.progress(0.3, "building arrow")
        arrow = build_arrow(ht, sc, hc, fc, L, lp)
        try:
            arrow.merge_vertices()
        except Exception:
            pass

        name = slugify(f"arrow-{ht}-{ctx.param('fletch_color', 'red')}")
        out = ctx.out_path(f"{name}.glb")
        arrow.export(out)

        ctx.progress(0.8, "rendering preview")
        preview = None
        try:
            from ...render.software_render import render_views

            frames = render_views(out, n=1, size=384, elevation=20)
            ppath = ctx.out_path(f"{name}.preview.png")
            frames[0].save(ppath, "PNG")
            preview = str(ppath)
        except Exception as e:
            ctx.log(f"preview failed: {e}")

        ctx.progress(0.95, "saved")
        return [
            ctx.make_asset(
                path=out, type=AssetType.model, prompt=ctx.param("prompt", "medieval arrow"),
                preview_path=preview,
                meta={"engine": "procedural-arrow", "head": ht, "style": "low-poly-medieval",
                      "polys": int(len(arrow.faces)),
                      "colors": {"shaft": ctx.param("shaft_color"), "head": ctx.param("head_color"),
                                 "fletch": ctx.param("fletch_color")}},
                license=self.license_note, commercial_ok=True,
                tags=["arrow", "medieval", "low-poly", "kids"],
            )
        ]
