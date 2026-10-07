# Asset Studio - AI pack installer (optional heavy local generators).
# Installs into the studio's existing venv. Safe to re-run. Two tiers:
#   1) light : background removal (rembg/onnxruntime) + MCP - CPU, ~few hundred MB
#   2) GPU   : PyTorch (+diffusers/transformers/accelerate) - ~2.5 GB, for local
#              image generation and upscaling (prompted; skipped by default)
# ComfyUI models (TripoSplat/Hunyuan3D/TRELLIS/TripoSG/SF3D) run through ComfyUI -
# this script only checks/points you to it (they are installed inside ComfyUI).
param([switch]$NoPause)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"

function Done($code = 0) { if (-not $NoPause) { Write-Host ""; Read-Host "Press Enter to close" } ; exit $code }

if (-not (Test-Path $venvPy)) {
    Write-Host "No virtual environment found. Run setup.ps1 first." -ForegroundColor Red
    Done 1
}

Write-Host "== Asset Studio - AI pack ==" -ForegroundColor Cyan

# --- tier 1: light optional deps (always) ---------------------------------
Write-Host "Installing background-removal + MCP deps (rembg, onnxruntime, mcp)..." -ForegroundColor Yellow
& $venvPy -m pip install -r (Join-Path $backend "requirements-optional.txt")
Write-Host "Light AI deps installed (background removal now works)." -ForegroundColor Green

# --- detect GPU ------------------------------------------------------------
$gpu = $false
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    try { nvidia-smi | Out-Null; if ($LASTEXITCODE -eq 0) { $gpu = $true } } catch {}
}
$gpuColor = "DarkGray"; if ($gpu) { $gpuColor = "Green" }
Write-Host ("NVIDIA GPU detected: " + $gpu) -ForegroundColor $gpuColor

# --- tier 2: GPU stack (prompted) -----------------------------------------
Write-Host ""
$ans = Read-Host "Install the GPU stack for LOCAL image generation + upscaling? (PyTorch, ~2.5 GB) [y/N]"
if ($ans -match '^(y|yes)$') {
    if ($gpu) {
        Write-Host "Installing PyTorch (CUDA 12.1) - this is the big download..." -ForegroundColor Yellow
        & $venvPy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
    } else {
        Write-Host "No NVIDIA GPU found - installing PyTorch (CPU build)..." -ForegroundColor Yellow
        & $venvPy -m pip install torch torchvision
    }
    Write-Host "Installing diffusers / transformers / accelerate..." -ForegroundColor Yellow
    & $venvPy -m pip install "diffusers==0.32.1" transformers accelerate
    Write-Host "GPU stack installed (local Diffusers image generation + Real-ESRGAN base)." -ForegroundColor Green
    Write-Host "  (Native TripoSR 3D also needs extra setup - easiest path is the ComfyUI models below.)" -ForegroundColor DarkGray
} else {
    Write-Host "Skipped the GPU stack. Run this script again anytime to add it." -ForegroundColor DarkGray
}

# --- ComfyUI check (for TripoSplat / Hunyuan3D / TRELLIS / TripoSG / SF3D) --
Write-Host ""
$comfy = "http://127.0.0.1:8188"
try {
    Invoke-RestMethod "$comfy/system_stats" -TimeoutSec 2 | Out-Null
    Write-Host "ComfyUI is running at $comfy - your ComfyUI generators are ready to drive." -ForegroundColor Green
} catch {
    Write-Host "ComfyUI not detected at $comfy." -ForegroundColor Yellow
    Write-Host "  For TripoSplat / Hunyuan3D / TRELLIS.2 / TripoSG / SF3D and the GGUF 2D models:" -ForegroundColor Yellow
    Write-Host "    1) Install ComfyUI (v0.23+):  https://www.comfy.org" -ForegroundColor Yellow
    Write-Host "    2) In the studio Settings, set tools.comfyui_url (default $comfy)." -ForegroundColor Yellow
    Write-Host "    3) Install each model node + weights, save its workflow as data\comfy_workflows\<id>.json" -ForegroundColor Yellow
    Write-Host "       (Settings -> 'Local generators' lists every model + filename)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "AI pack finished." -ForegroundColor Cyan
Done 0
