$ErrorActionPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$feMods = Join-Path (Join-Path $root 'frontend') 'node_modules'

Write-Host ''
Write-Host 'Stopping Asset Studio (backend + frontend) so the files unlock...' -ForegroundColor Cyan
Write-Host ''

# the backend (python -m asset_studio.main) + this install's Vite/Electron (node/electron under
# frontend\node_modules). Only THIS install — not another copy of the Studio, not VS Code or
# other Node/Electron apps. See studio-procs.ps1.
. (Join-Path $root 'studio-procs.ps1')
$procs = @(Get-StudioProcesses $root)

foreach ($p in $procs) { Write-Host ('  stopping ' + $p.Name + '  PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force }
Start-Sleep -Seconds 1

Write-Host ''
if ($procs.Count -gt 0) {
  Write-Host ('Stopped ' + $procs.Count + ' process(es). The folder is free now — you can delete/replace it, or relaunch Asset Studio.') -ForegroundColor Green
} else {
  Write-Host 'No running Asset Studio processes found (already stopped).' -ForegroundColor Yellow
}
Write-Host ''
