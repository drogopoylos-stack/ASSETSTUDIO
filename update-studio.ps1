$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$frontend = Join-Path $root 'frontend'

Write-Host ''
Write-Host '=== Updating Asset Studio ===' -ForegroundColor Cyan
Write-Host ''

# 1) stop the running app (frees locked files + stops the old code)
& (Join-Path $root 'stop-studio.ps1')

# 2) pull latest if this is a git checkout (skipped if you copied the files in by hand)
if (Test-Path (Join-Path $root '.git')) {
  Write-Host 'Pulling latest from GitHub...' -ForegroundColor Cyan
  Push-Location $root
  try { git pull } catch { Write-Host ('  (git pull skipped: ' + $_.Exception.Message + ')') -ForegroundColor Yellow }
  Pop-Location
}

# 3) refresh BACKEND deps too, so any newly-required package gets installed. A backend that
#    won't start after an update ("Backend didn't start") is usually a missing dependency or an
#    incomplete venv -- this covers that. If the venv is missing entirely, run setup.ps1.
$venvPy = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (Test-Path $venvPy) {
  Write-Host 'Refreshing backend dependencies...' -ForegroundColor Cyan
  & $venvPy -m pip install -q --prefer-binary -r (Join-Path $root 'backend\requirements.txt')
} else {
  Write-Host 'Backend venv not found - run setup.ps1 first (it creates the venv + installs deps).' -ForegroundColor Yellow
}

# 4) REBUILD the frontend bundle (dist/). This is the step a plain "git pull" misses: the app
#    shows a PRE-BUILT bundle, so UI changes (e.g. the Workflows + Ask AI tabs) don't appear
#    until it's rebuilt. The backend reloads itself when the app relaunches.
Push-Location $frontend
if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
  Write-Host 'Installing frontend dependencies (first time only)...' -ForegroundColor Cyan
  npm install
}
Write-Host 'Building the frontend (the step that was missing)...' -ForegroundColor Cyan
npm run build
$built = ($LASTEXITCODE -eq 0)
Pop-Location

if (-not $built) {
  Write-Host ''
  Write-Host 'Frontend build FAILED (see errors above). The old UI was kept - nothing broken.' -ForegroundColor Red
  Write-Host 'Make sure Node.js is installed, then run this again.' -ForegroundColor Yellow
  exit 1
}

# 5) relaunch - Electron starts a fresh backend and loads the freshly built UI
Write-Host ''
Write-Host 'Relaunching Asset Studio...' -ForegroundColor Green
Start-Process (Join-Path $root 'Asset Studio.vbs')
Write-Host 'Done - the Workflows + Ask AI tabs will be there now.' -ForegroundColor Green
Write-Host ''
