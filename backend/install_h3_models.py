"""Download the official Comfy-Org H3 weights with resume and SHA-256 verification.

Usage: python install_h3_models.py PATH_TO_COMFYUI
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

FILES = [
    "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "vae/minimax_h3_video_vae_int8_convrot.safetensors",
    "vae/minimax_h3_audio_vae_fp32.safetensors",
    "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
]


def install(root, model_root=None, files=None):
    root = Path(root).resolve()
    if not (root / "main.py").is_file():
        raise SystemExit("The target must be an installed ComfyUI folder.")
    with urllib.request.urlopen("https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/tree/main?recursive=true&expand=false") as response:
        tree = {row["path"]: row for row in json.load(response)}

    model_root = Path(model_root).resolve() if model_root else root / "models"
    files = files or FILES
    if any(name not in FILES for name in files):
        raise ValueError("Only official H3 model filenames are allowed.")

    def download(name):
        expected = tree[name]
        target = model_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(target.suffix + ".part")
        if not target.exists():
            print(f"Downloading {name} ({expected['size'] / 1e9:.2f} GB)", flush=True)
            # Start a fresh curl process for every retry so the resume offset is
            # recomputed from disk. curl's internal retries can reset partial files.
            for attempt in range(12):
                result = subprocess.run(["curl.exe", "--fail", "--location", "--silent", "--show-error",
                    "--connect-timeout", "30", "--speed-limit", "1024", "--speed-time", "90",
                    "--continue-at", "-", "--output", str(part),
                    f"https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/{name}?download=true"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                if result.returncode == 0:
                    break
                print(f"Retry {attempt + 1}: {name}: {result.stderr.strip()[-180:]}", flush=True)
                time.sleep(5)
            else:
                raise RuntimeError(f"Download failed; rerun to resume: {name}")
            if part.stat().st_size != expected["size"]:
                raise RuntimeError(f"Size mismatch: {name}")
            with part.open("r+b") as stream:
                os.fsync(stream.fileno())
            source = part
        else:
            source = target
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected["lfs"]["oid"]:
            raise RuntimeError(f"SHA-256 mismatch: {name}")
        if source == part:
            part.replace(target)
        print(f"Verified {name}", flush=True)
        return name

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, files))
    print("All requested H3 model files installed and verified.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comfy_dir")
    parser.add_argument("--model-root")
    parser.add_argument("--only", choices=FILES, action="append")
    args = parser.parse_args()
    install(args.comfy_dir, args.model_root, args.only)
