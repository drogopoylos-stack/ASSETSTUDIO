"""Dependency-light software renderer (numpy + trimesh + Pillow).

Renders a mesh to PNG frames with a z-buffer and simple diffuse shading — no GPU,
no OpenGL, no display required. This guarantees the QA turntable works everywhere
so the agent can always *see* a 3D result and judge it. If a real GL renderer
(pyrender) or Blender is available, those paths produce nicer frames; this is the
universal fallback.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

MAX_FACES = 120_000


def load_mesh(path: str | Path):
    """Return (vertices Nx3 float, faces Mx3 int, face_colors Mx3 uint8|None)."""
    import trimesh

    mesh = trimesh.load(str(path), force="mesh")
    if not hasattr(mesh, "vertices") or len(getattr(mesh, "faces", [])) == 0:
        raise RuntimeError("mesh has no faces")
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)

    colors = None
    try:
        vis = mesh.visual
        if hasattr(vis, "face_colors") and vis.face_colors is not None and len(vis.face_colors):
            colors = np.asarray(vis.face_colors)[:, :3].astype(np.uint8)
        elif hasattr(vis, "vertex_colors") and vis.vertex_colors is not None and len(vis.vertex_colors):
            vc = np.asarray(vis.vertex_colors)[:, :3]
            colors = vc[faces].mean(axis=1).astype(np.uint8)
        elif hasattr(vis, "to_color"):
            cv = vis.to_color()
            if getattr(cv, "vertex_colors", None) is not None:
                vc = np.asarray(cv.vertex_colors)[:, :3]
                colors = vc[faces].mean(axis=1).astype(np.uint8)
    except Exception:
        colors = None

    if len(faces) > MAX_FACES:  # subsample for the preview only
        idx = np.linspace(0, len(faces) - 1, MAX_FACES).astype(np.int64)
        faces = faces[idx]
        if colors is not None:
            colors = colors[idx]
    return verts, faces, colors


def _normalize(verts: np.ndarray):
    center = (verts.max(0) + verts.min(0)) / 2
    v = verts - center
    radius = np.linalg.norm(v, axis=1).max() or 1.0
    return v / radius


def _look_at(eye, target, up):
    f = target - eye
    f /= np.linalg.norm(f) or 1.0
    s = np.cross(f, up)
    s /= np.linalg.norm(s) or 1.0
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def _render_one(verts, faces, colors, size, az_deg, el_deg, bg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    dist = 2.6
    eye = np.array([dist * math.cos(el) * math.sin(az),
                    dist * math.sin(el),
                    dist * math.cos(el) * math.cos(az)])
    view = _look_at(eye, np.zeros(3), np.array([0.0, 1.0, 0.0]))
    vh = np.c_[verts, np.ones(len(verts))]
    cam = (view @ vh.T).T[:, :3]  # camera space

    fov = math.radians(40)
    fpx = (size / 2) / math.tan(fov / 2)
    z = cam[:, 2]
    z_safe = np.where(z >= -1e-4, -1e-4, z)
    sx = size / 2 + fpx * cam[:, 0] / -z_safe
    sy = size / 2 - fpx * cam[:, 1] / -z_safe
    screen = np.c_[sx, sy]

    # lighting in camera space
    light = np.array([0.4, 0.7, 1.0])
    light /= np.linalg.norm(light)

    img = np.zeros((size, size, 3), dtype=np.float32)
    img[:] = np.array(bg, dtype=np.float32) / 255.0
    zbuf = np.full((size, size), np.inf)

    tri_v = cam[faces]
    n = np.cross(tri_v[:, 1] - tri_v[:, 0], tri_v[:, 2] - tri_v[:, 0])
    nlen = np.linalg.norm(n, axis=1)
    nlen[nlen == 0] = 1
    n = n / nlen[:, None]
    shade = np.clip(np.abs(n @ light), 0.15, 1.0)

    base = (colors.astype(np.float32) / 255.0) if colors is not None else None
    default = np.array([0.62, 0.66, 0.74])

    order = np.argsort(-tri_v[:, :, 2].mean(axis=1))  # far-to-near helps ties
    for fi in order:
        tri = screen[faces[fi]]
        depth = cam[faces[fi], 2]
        minx = max(int(np.floor(tri[:, 0].min())), 0)
        maxx = min(int(np.ceil(tri[:, 0].max())), size - 1)
        miny = max(int(np.floor(tri[:, 1].min())), 0)
        maxy = min(int(np.ceil(tri[:, 1].max())), size - 1)
        if minx > maxx or miny > maxy:
            continue
        xs = np.arange(minx, maxx + 1)
        ys = np.arange(miny, maxy + 1)
        px, py = np.meshgrid(xs, ys)
        ax, ay = tri[0]; bx, by = tri[1]; cx, cy = tri[2]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            continue
        wa = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
        wb = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
        wc = 1 - wa - wb
        inside = (wa >= 0) & (wb >= 0) & (wc >= 0)
        if not inside.any():
            continue
        zinterp = wa * depth[0] + wb * depth[1] + wc * depth[2]
        col = (base[fi] if base is not None else default) * shade[fi]
        ys_i, xs_i = py[inside], px[inside]
        zz = zinterp[inside]
        cur = zbuf[ys_i, xs_i]
        closer = zz < cur
        yy, xx = ys_i[closer], xs_i[closer]
        zbuf[yy, xx] = zz[closer]
        img[yy, xx] = col
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGB")


def render_views(path, n=8, size=512, elevation=18.0, bg=(28, 30, 42)):
    verts, faces, colors = load_mesh(path)
    verts = _normalize(verts)
    return [
        _render_one(verts, faces, colors, size, az_deg=i * (360.0 / n), el_deg=elevation, bg=bg)
        for i in range(n)
    ]


def montage(images, cols=4):
    if not images:
        raise RuntimeError("no frames")
    cols = min(cols, len(images))
    rows = math.ceil(len(images) / cols)
    w, h = images[0].size
    sheet = Image.new("RGB", (cols * w, rows * h), (16, 16, 22))
    for i, im in enumerate(images):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    return sheet


def save_gif(images, path, duration=110):
    if not images:
        return None
    images[0].save(path, save_all=True, append_images=images[1:], loop=0, duration=duration, disposal=2)
    return str(path)
