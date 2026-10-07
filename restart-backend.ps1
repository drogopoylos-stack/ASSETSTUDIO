$ErrorActionPreference = 'SilentlyContinue'
Write-Host ''
Write-Host 'Restarting the Asset Studio backend (loading the new Workflows + login features)...' -ForegroundColor Cyan
Write-Host ''

# 1) stop THIS copy's running backend (not another copy's - see studio-procs.ps1)
. (Join-Path $PSScriptRoot 'studio-procs.ps1')
$procs = @(Get-StudioProcesses $PSScriptRoot -BackendOnly)
if ($procs) { foreach ($p in $procs) { Write-Host ('  stopping backend PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force } }
else { Write-Host '  (no running backend found)' }
Start-Sleep -Seconds 2

# 2) start a fresh backend from the project venv (relative to this script's folder)
$backend = Join-Path $PSScriptRoot 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
Write-Host '  starting fresh backend...' -ForegroundColor Cyan
Start-Process -FilePath $py -ArgumentList '-m', 'asset_studio.main' -WorkingDirectory $backend -WindowStyle Hidden

# 3) wait for it to answer
Write-Host '  waiting for it to come up...'
$ok = $false
for ($i = 0; $i -lt 15; $i++) {
  Start-Sleep -Seconds 1
  try { $h = Invoke-RestMethod 'http://127.0.0.1:8777/api/health' -TimeoutSec 3; if ($h.ok) { $ok = $true; break } } catch {}
}

Write-Host ''
if ($ok) {
  $wf = 'no'
  try { Invoke-RestMethod 'http://127.0.0.1:8777/api/workflows?limit=1' -TimeoutSec 3 | Out-Null; $wf = 'yes' } catch {}
  Write-Host 'Backend restarted and healthy.' -ForegroundColor Green
  Write-Host ('Workflows tab ready: ' + $wf) -ForegroundColor Green
  Write-Host ''
  Write-Host 'Now: switch to Asset Studio, press Ctrl+R to reload, then open the Workflows tab.' -ForegroundColor Green
} else {
  Write-Host 'Backend did not answer yet - give it a few more seconds, then check Asset Studio.' -ForegroundColor Yellow
}
Write-Host ''
