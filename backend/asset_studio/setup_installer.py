"""In-app installer for the optional tools the local providers need.

Runs the REAL install commands (pip / npm / git) in a background thread, streaming
the log so the UI can show progress, reports whether a studio restart is needed,
and (for ComfyUI) wires up the launch command afterwards. Heavy installs (ComfyUI,
PyTorch) are best-effort — they download GBs and depend on your machine — but this
removes the manual copy-paste.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .config import BACKEND_DIR, DATA_DIR, settings

VENDOR_DIR = (DATA_DIR / "vendor")
_RUNS: dict[str, dict] = {}
_LOCK = threading.Lock()
_NF = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_CACHE: dict = {"ts": 0.0, "tasks": None}


def _venv_python() -> str:
    base = BACKEND_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    return str(base / ("python.exe" if os.name == "nt" else "python"))


def _has(name: str) -> bool:
    return shutil.which(name) is not None


def _has_nvidia() -> bool:
    if not _has("nvidia-smi"):
        return False
    try:
        return subprocess.run(["nvidia-smi"], capture_output=True, timeout=8, creationflags=_NF).returncode == 0
    except Exception:
        return False


def _pip_has(module: str) -> bool:
    try:
        r = subprocess.run([_venv_python(), "-c", f"import {module}"], capture_output=True, timeout=25, creationflags=_NF)
        return r.returncode == 0
    except Exception:
        return False


def _comfy_dir() -> Optional[Path]:
    svc = (settings.get("services") or {}).get("comfyui") or {}
    cwd = svc.get("cwd")
    if cwd and (Path(cwd) / "main.py").exists():
        return Path(cwd)
    cand = VENDOR_DIR / "ComfyUI"
    return cand if (cand / "main.py").exists() else None


def _comfy_python() -> str:
    d = _comfy_dir()
    if d:
        p = d / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
        if p.exists():
            return str(p)
    return _venv_python()


# ---------------------------------------------------------------------------
def tasks(force: bool = False) -> list[dict]:
    if not force and _CACHE["tasks"] and (time.time() - _CACHE["ts"]) < 4.0:
        out = _CACHE["tasks"]
    else:
        comfy = _comfy_dir()
        has_comfy = comfy is not None
        out = [
            {"id": "ai-pack", "name": "AI pack — background removal", "kind": "python",
             "desc": "rembg + onnxruntime so the bg-removal / 2D processing engines work.",
             "installed": _pip_has("rembg") and _pip_has("onnxruntime"), "restart": True, "link": ""},
            {"id": "gpu-stack", "name": "PyTorch GPU stack", "kind": "python",
             "desc": "PyTorch + diffusers/transformers for LOCAL image generation & upscaling (~2.5 GB).",
             "installed": _pip_has("torch"), "restart": True, "link": "https://pytorch.org"},
            {"id": "gltf-transform", "name": "glTF-Transform CLI", "kind": "node",
             "desc": "Web-optimizes GLB/GLTF (the Optimize stage). Installs globally via npm.",
             "installed": _has("gltf-transform"), "restart": False, "link": "https://gltf-transform.dev"},
            {"id": "comfyui", "name": "ComfyUI — local 2D/3D engine", "kind": "app",
             "desc": "Clones ComfyUI, sets up its env, and registers its launch command. Big download.",
             "installed": has_comfy, "restart": False, "link": "https://www.comfy.org"},
            {"id": "comfyui-3d-pack", "name": "ComfyUI-3D-Pack (Hunyuan3D / TripoSG / SF3D nodes)", "kind": "node",
             "desc": "Adds the 3D nodes to ComfyUI. Restart ComfyUI after. Needs ComfyUI installed first.",
             "installed": bool(has_comfy and comfy and (comfy / "custom_nodes" / "ComfyUI-3D-Pack").exists()),
             "restart": False, "requires": "comfyui", "link": "https://github.com/MrForExample/ComfyUI-3D-Pack"},
            {"id": "trellis2", "name": "ComfyUI-TRELLIS2 node", "kind": "node",
             "desc": "Adds TRELLIS.2 image-to-3D to ComfyUI. Needs ComfyUI installed first.",
             "installed": bool(has_comfy and comfy and (comfy / "custom_nodes" / "ComfyUI-TRELLIS2").exists()),
             "restart": False, "requires": "comfyui", "link": "https://github.com/PozzettiAndrea/ComfyUI-TRELLIS2"},
        ]
        _CACHE.update(ts=time.time(), tasks=out)
    # attach live run state (not cached)
    for t in out:
        st = _RUNS.get(t["id"]) or {}
        t["running"] = bool(st.get("running"))
        t["needs_restart"] = bool(st.get("restart"))
    return out


def _steps(task_id: str) -> list[tuple[str, object, Optional[str]]]:
    py = _venv_python()
    if task_id == "ai-pack":
        return [("pip install background-removal deps",
                 [py, "-m", "pip", "install", "-r", str(BACKEND_DIR / "requirements-optional.txt")], None)]
    if task_id == "gpu-stack":
        idx = ["--index-url", "https://download.pytorch.org/whl/cu121"] if _has_nvidia() else []
        return [
            ("pip install torch torchvision", [py, "-m", "pip", "install", "torch", "torchvision", *idx], None),
            ("pip install diffusers/transformers/accelerate", [py, "-m", "pip", "install", "diffusers==0.32.1", "transformers", "accelerate"], None),
        ]
    if task_id == "gltf-transform":
        return [("npm install -g @gltf-transform/cli", "npm install -g @gltf-transform/cli", None)]
    if task_id == "comfyui":
        git = shutil.which("git") or "git"
        comfy = VENDOR_DIR / "ComfyUI"
        cvenv = comfy / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
        idx = ["--index-url", "https://download.pytorch.org/whl/cu121"] if _has_nvidia() else []
        VENDOR_DIR.mkdir(parents=True, exist_ok=True)
        return [
            ("clone ComfyUI", f'"{git}" clone --depth 1 https://github.com/comfyanonymous/ComfyUI "{comfy}"', None),
            ("create ComfyUI venv", [sys.executable, "-m", "venv", str(comfy / ".venv")], None),
            ("install PyTorch (this is the big one)", [str(cvenv), "-m", "pip", "install", "torch", "torchvision", *idx], None),
            ("install ComfyUI requirements", [str(cvenv), "-m", "pip", "install", "-r", str(comfy / "requirements.txt")], None),
        ]
    if task_id in ("comfyui-3d-pack", "trellis2"):
        comfy = _comfy_dir()
        if not comfy:
            raise RuntimeError("Install ComfyUI first, then install this node.")
        repo = {"comfyui-3d-pack": "https://github.com/MrForExample/ComfyUI-3D-Pack",
                "trellis2": "https://github.com/PozzettiAndrea/ComfyUI-TRELLIS2"}[task_id]
        name = repo.rsplit("/", 1)[1]
        dest = comfy / "custom_nodes" / name
        git = shutil.which("git") or "git"
        cpy = _comfy_python()
        return [
            ("clone node", f'"{git}" clone --depth 1 {repo} "{dest}"', None),
            (f"install {name} requirements", f'"{cpy}" -m pip install -r "{dest / "requirements.txt"}"', None),
        ]
    raise RuntimeError(f"unknown install task {task_id!r}")


def install(task_id: str) -> dict:
    with _LOCK:
        if (_RUNS.get(task_id) or {}).get("running"):
            return {"ok": False, "error": "already running"}
        _RUNS[task_id] = {"running": True, "log": "", "ok": False, "done": False, "restart": False}
    try:
        steps = _steps(task_id)
    except Exception as e:
        with _LOCK:
            _RUNS[task_id].update(running=False, done=True, ok=False, log=str(e))
        return {"ok": False, "error": str(e)}
    threading.Thread(target=_worker, args=(task_id, steps), daemon=True).start()
    return {"ok": True, "started": True}


def _append(task_id: str, s: str) -> None:
    with _LOCK:
        _RUNS[task_id]["log"] = (_RUNS[task_id]["log"] + s)[-24000:]


def _worker(task_id: str, steps: list) -> None:
    ok = True
    for label, cmd, cwd in steps:
        _append(task_id, f"\n$ {label}\n")
        try:
            p = subprocess.Popen(cmd, cwd=cwd, shell=isinstance(cmd, str), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=_NF)
            assert p.stdout is not None
            for line in p.stdout:
                _append(task_id, line)
            p.wait()
            if p.returncode != 0:
                ok = False
                _append(task_id, f"\n[failed: exit {p.returncode}]\n")
                break
        except Exception as e:
            ok = False
            _append(task_id, f"\n[error] {e}\n")
            break
    restart = _on_done(task_id) if ok else False
    _CACHE["tasks"] = None  # force a fresh status next poll
    with _LOCK:
        _RUNS[task_id].update(running=False, done=True, ok=ok, restart=restart)


def _on_done(task_id: str) -> bool:
    """Post-install wiring; returns whether a studio restart is needed."""
    if task_id == "comfyui":
        comfy = VENDOR_DIR / "ComfyUI"
        cvenv = comfy / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
        services = dict(settings.get("services") or {})
        svc = dict(services.get("comfyui") or {})
        svc["command"] = f'"{cvenv}" main.py --port 8188'
        svc["cwd"] = str(comfy)
        services["comfyui"] = svc
        settings.update({"services": services})
        return False
    return task_id in ("ai-pack", "gpu-stack")  # new Python modules need a backend restart


def status(task_id: str) -> dict:
    with _LOCK:
        return dict(_RUNS.get(task_id) or {"running": False, "log": "", "done": False, "ok": False, "restart": False})
