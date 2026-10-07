# Asset Studio - launch backend + frontend (Electron dev).
# Backend starts in its own window on :8777; Vite + Electron run here.
param([switch]$NoElectron)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "Backend venv missing - running setup first..." -ForegroundColor Yellow
    & (Join-Path $root "setup.ps1")
}

# Stop any previous instance FIRST, so launching always runs the LATEST code. Closing the
# window leaves the backend + Vite dev server alive in the background; without this they keep
# serving old code on relaunch (and lock files when you update). Matches only THIS install's
# processes (its backend\.venv and frontend\node_modules), so it won't touch VS Code or another
# copy of the Studio. See studio-procs.ps1.
. (Join-Path $root "studio-procs.ps1")
$old = @(Get-StudioProcesses $root)
if ($old.Count -gt 0) {
    Write-Host ("Stopping previous instance (" + $old.Count + " process)...") -ForegroundColor DarkGray
    foreach ($p in $old) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800
}

Write-Host "Starting backend on http://127.0.0.1:8777 ..." -ForegroundColor Cyan
Start-Process -FilePath $venvPy -ArgumentList "-m", "asset_studio.main" -WorkingDirectory $backend

# wait for health
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:8777/api/health" -TimeoutSec 1 | Out-Null; $ok = $true; break }
    catch { Start-Sleep -Milliseconds 500 }
}
if ($ok) { Write-Host "Backend is up." -ForegroundColor Green }
else { Write-Host "Backend not responding yet; continuing anyway." -ForegroundColor Yellow }

Push-Location $frontend
if ($NoElectron) {
    Write-Host "Starting Vite dev server (open http://localhost:5173) ..." -ForegroundColor Cyan
    npm run dev
} else {
    Write-Host "Starting Vite + Electron ..." -ForegroundColor Cyan
    npm run app
}
Pop-Location
