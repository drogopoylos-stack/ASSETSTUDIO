"""SAM2 (Segment Anything 2.1) click-to-cutout — point at an object, get a transparent PNG.

Local + free. Runs ultralytics' SAM2 in a DEDICATED venv (data/tools/sam2-venv with
torch-cpu) so the backend stays lean — same self-contained pattern as voice/graphify.
First job auto-installs the venv (~700 MB one-time) and downloads the checkpoint
(sam2.1 base-plus ~155 MB) with progress; later jobs run in seconds. CPU is plenty
for single images, and CPU-only torch means it never touches the GPU/VRAM.

Modes: points (click x,y — the "one click → perfect mask" flow), box, or auto
(segment EVERY object it finds → one PNG each). Chains naturally with Real-ESRGAN
upscale for small cuts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from ...config import DATA_DIR
from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

_VENV_DIR = DATA_DIR / "tools" / "sam2-venv"
_WEIGHTS_DIR = DATA_DIR / "models" / "sam2"
_WORKER = Path(__file__).resolve().parent / "sam2_worker.py"
_TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
_NF = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_INSTALL_LOCK = threading.Lock()


def _venv_py() -> Path:
    return _VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _installed() -> bool:
    return _venv_py().exists()


def _parse_points(s: str) -> list[list[float]]:
    """'120,340; 200,80' -> [[120,340],[200,80]] (tolerant about separators)."""
    out = []
    for chunk in (s or "").replace("|", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        xy = [t for t in chunk.replace(",", " ").split() if t]
        if len(xy) >= 2:
            out.append([float(xy[0]), float(xy[1])])
    return out


def _install(ctx: JobContext) -> None:
    """Create the sam2 venv (torch-cpu + ultralytics). Serialized; safe to call always."""
    with _INSTALL_LOCK:
        if _installed():
            return
        ctx.progress(0.02, "one-time setup: creating SAM2 env")
        _VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(_VENV_DIR)],
                       timeout=300, creationflags=_NF, capture_output=True)
        py = _venv_py()
        if not py.exists():
            raise RuntimeError("could not create the SAM2 venv")
        ctx.progress(0.05, "one-time setup: installing torch (CPU) — a few minutes")
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
                       timeout=300, creationflags=_NF, capture_output=True)
        r1 = subprocess.run([str(py), "-m", "pip", "install", "--prefer-binary", "--quiet",
                             "torch", "torchvision", "--index-url", _TORCH_CPU_INDEX],
                            timeout=1800, creationflags=_NF, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        ctx.progress(0.12, "one-time setup: installing ultralytics (SAM2)")
        r2 = subprocess.run([str(py), "-m", "pip", "install", "--prefer-binary", "--quiet",
                             "ultralytics"],
                            timeout=1200, creationflags=_NF, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        chk = subprocess.run([str(py), "-c", "import torch, ultralytics"],
                             timeout=180, creationflags=_NF, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if chk.returncode != 0:
            raise RuntimeError("SAM2 install failed: "
                               + ((r1.stderr or "") + (r2.stderr or "") + (chk.stderr or "")).strip()[-400:])


class Sam2CutoutProvider(Provider):
    id = "sam2-cutout"
    name = "SAM2 Cutout (click-to-segment)"
    stage = StageType.process2d
    kind = ProviderKind.local
    requires_key = False
    description = ("Segment Anything 2.1: click a point (or box, or auto-find every object) and get "
                   "clean transparent-PNG cutouts. Local + free, CPU-only (no VRAM). First run does a "
                   "one-time setup (~700 MB env + ~155 MB checkpoint).")
    license_note = "SAM 2 weights: Apache-2.0. Output inherits the source image's license."
    commercial_ok = True
    cost_hint = "free"
    homepage = "https://github.com/facebookresearch/sam2"
    params = [
        ProviderParam(name="mode", label="Mode", type="select",
                      options=["points", "box", "auto"], default="points",
                      description="points: click(s) → the object; box: x1,y1,x2,y2; auto: every object → one PNG each"),
        ProviderParam(name="points", label="Click point(s) x,y", type="string", default="",
                      description="pixel coords in the source image, e.g. 120,340 — several: 120,340; 200,80"),
        ProviderParam(name="neg_points", label="Exclude point(s)", type="string", default="",
                      description="optional clicks on parts that must NOT be in the mask"),
        ProviderParam(name="box", label="Box x1,y1,x2,y2", type="string", default=""),
        ProviderParam(name="model", label="Checkpoint", type="select",
                      options=["sam2.1_b.pt", "sam2.1_s.pt", "sam2.1_t.pt"], default="sam2.1_b.pt",
                      description="base-plus (best), small, tiny (fastest / least disk)"),
        ProviderParam(name="max_masks", label="Max cutouts (auto mode)", type="int", default=20, min=1, max=100),
        ProviderParam(name="min_area_pct", label="Min mask area %", type="float", default=0.05, min=0.0, max=50.0,
                      description="drop specks smaller than this % of the image (auto mode)"),
        ProviderParam(name="crop", label="Crop to object", type="bool", default=True),
        ProviderParam(name="pad", label="Crop padding px", type="int", default=4, min=0, max=64),
    ]

    def is_available(self) -> tuple[bool, str]:
        if _installed():
            return True, ""
        return True, "installs itself on the first run (~700 MB one-time, CPU-only)"

    def run(self, ctx: JobContext) -> list:
        src = ctx.first_image()
        if not src:
            raise RuntimeError("SAM2 cutout needs an input image.")
        if not _installed():
            _install(ctx)

        mode = str(ctx.param("mode", "points"))
        spec = {
            "image": src,
            "out_dir": str(ctx.workdir),
            "weights_dir": str(_WEIGHTS_DIR),
            "model": str(ctx.param("model", "sam2.1_b.pt")),
            "mode": mode,
            "points": _parse_points(str(ctx.param("points", ""))),
            "neg_points": _parse_points(str(ctx.param("neg_points", ""))),
            "box": ([float(t) for t in str(ctx.param("box", "")).replace(",", " ").split()][:4]
                    if str(ctx.param("box", "")).strip() else None),
            "max_masks": int(ctx.param("max_masks", 20)),
            "min_area_pct": float(ctx.param("min_area_pct", 0.05)),
            "crop": bool(ctx.param("crop", True)),
            "pad": int(ctx.param("pad", 4)),
        }
        spec_file = ctx.workdir / "sam2_spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")

        ctx.progress(0.2, "starting SAM2")
        proc = subprocess.Popen([str(_venv_py()), str(_WORKER), str(spec_file)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=_NF, text=True, encoding="utf-8",
                                errors="replace", bufsize=1)
        done: dict = {}
        for line in proc.stdout:               # stream worker progress into the job
            line = line.strip()
            if line.startswith("P "):
                _, v, msg = line.split(" ", 2)
                # map worker 0..1 into the job's 0.2..0.9 band
                ctx.progress(0.2 + float(v) * 0.7, msg)
            elif line.startswith("LOG "):
                ctx.log(line[4:])
            elif line.startswith("DONE "):
                done = json.loads(line[5:])
            if ctx.canceled():
                proc.kill()
                raise RuntimeError("canceled")
        proc.wait(timeout=60)
        if proc.returncode != 0 or not done.get("files"):
            err = (proc.stderr.read() or "").strip()[-400:] if proc.stderr else ""
            raise RuntimeError(f"SAM2 failed: {err or 'no masks produced'}")

        ctx.progress(0.95, "saving assets")
        assets = []
        parent = ctx.input_assets[0] if ctx.input_assets else None
        for f in done["files"]:
            out = Path(f["path"])
            assets.append(ctx.make_asset(
                path=out, type=AssetType.image, name=out.name,
                meta={"method": f"sam2:{spec['model']}", "mode": mode, "alpha": True,
                      "area_pct": f.get("area_pct"), "bbox": f.get("bbox")},
                parent_id=parent.id if parent else None,
                commercial_ok=parent.commercial_ok if parent else None,
            ))
        return assets
