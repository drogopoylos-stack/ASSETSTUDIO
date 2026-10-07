# The processes of THIS copy of Asset Studio - never another copy's, never another app's.
#
# The venv's python.exe is a redirector that starts the real interpreter as its child, so one
# backend is two processes: the one under backend\.venv, and its child. The scripts used to stop
# every process on the PC with "asset_studio" in its command line - so a second copy of the Studio
# (a development copy, an older install) lost its backend, and the agents running in it, whenever
# this copy was stopped, restarted or updated.
#
#   . (Join-Path $PSScriptRoot "studio-procs.ps1")
#   $procs = Get-StudioProcesses $PSScriptRoot               # everything of this copy: backend,
#                                                            # session keepers, Electron/Vite
#   $procs = Get-StudioProcesses $PSScriptRoot -BackendOnly  # asset_studio.main alone
#
# -BackendOnly leaves the session keepers alone ON PURPOSE: a keeper holds a running Claude
# session's stdin open so the session outlives a backend restart - stopping it ends the chat.
function Get-StudioProcesses([string]$Root, [switch]$BackendOnly) {
    $venvDir = Join-Path $Root "backend\.venv"
    $feMods = Join-Path $Root "frontend\node_modules"
    $needle = "asset_studio"
    if ($BackendOnly) { $needle = "asset_studio.main" }
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $mine = @($all | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($needle) -and $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($venvDir, [System.StringComparison]::OrdinalIgnoreCase) })
    $ids = @($mine | ForEach-Object { $_.ProcessId })
    $out = @($mine)
    $out += @($all | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($needle) -and ($ids -contains $_.ParentProcessId) })
    if (-not $BackendOnly) {
        $out += @($all | Where-Object {
            ($_.Name -eq "node.exe" -or $_.Name -eq "electron.exe") -and $_.CommandLine -and
            $_.CommandLine.IndexOf($feMods, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 })
    }
    return $out
}
