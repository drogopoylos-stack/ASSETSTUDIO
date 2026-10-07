# Asset Studio - one setup that fixes everything (idempotent + self-healing).
# Stops THIS copy if it is running, (re)builds a working Python venv, installs + VERIFIES the backend,
# installs the UI (or uses a prebuilt one), graphify, the Scrapling web tools and a browser for the
# Studio Engine, verifying each step with a clear message on failure. Safe to re-run anytime.
#
#   -Yes           no questions: skip the optional-AI-pack prompt and the final pause
#   -PythonExe     use this interpreter (the installer .exe passes its bundled one)
#   -Wheelhouse    install Python packages from this folder of wheels first, with no internet
#   -SkipUiBuild   the UI is already built and node_modules shipped: no npm, no Node needed
#   -InstallNode   install Node.js LTS with winget when missing, even with -SkipUiBuild
#   -NoClaude      do not install Claude Code          -NoCodex   do not install the Codex CLI
#
# A folder called runtime\ beside this script (the installer .exe puts it there) supplies the
# first two by itself: runtime\python\python.exe and runtime\wheelhouse.
param([switch]$Yes, [string]$PythonExe = "", [string]$Wheelhouse = "", [switch]$SkipUiBuild,
      [switch]$InstallNode, [switch]$NoClaude, [switch]$NoCodex)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venvDir = Join-Path $backend ".venv"
$venvPy = Join-Path $venvDir "Scripts\python.exe"
$feMods = Join-Path $frontend "node_modules"
$electronExe = Join-Path $feMods "electron\dist\electron.exe"
$distIndex = Join-Path $frontend "dist\index.html"

# The bundled runtime, when this is an installed copy.
$bundledPy = Join-Path $root "runtime\python\python.exe"
$bundledWh = Join-Path $root "runtime\wheelhouse"
if (-not $PythonExe -and (Test-Path $bundledPy)) { $PythonExe = $bundledPy }
if (-not $Wheelhouse -and (Test-Path $bundledWh)) { $Wheelhouse = $bundledWh }
if ($Wheelhouse -and -not (Test-Path $Wheelhouse)) { $Wheelhouse = "" }
# The backend's own installers (graphify, the web tools) read the same wheels.
if ($Wheelhouse) { $env:ASSET_STUDIO_WHEELHOUSE = $Wheelhouse }

function Fail($msg) { Write-Host ""; Write-Host ("[X] " + $msg) -ForegroundColor Red; Write-Host ""; try { Stop-Transcript | Out-Null } catch {}; if (-not $Yes) { Read-Host "Press Enter to close" }; exit 1 }
function Step($msg) { Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Good($msg) { Write-Host ("[ok] " + $msg) -ForegroundColor Green }
function Warn($msg) { Write-Host ("[!] " + $msg) -ForegroundColor Yellow }

# A log of every run, so a problem on somebody else's PC can be read afterwards.
try {
    $logDir = Join-Path $root "data\logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Transcript -Path (Join-Path $logDir ("setup-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")) -Force | Out-Null
} catch {}

try {
    Write-Host ""
    Write-Host "===== Asset Studio - setup / repair =====" -ForegroundColor Cyan
    Write-Host ("    folder: " + $root)

    # 0) stop THIS copy if it is running, so nothing is file-locked and its port is free.
    #    Only this copy: a second Studio on the same PC (a development copy, an older install) can
    #    have agents running, and stopping every asset_studio.main on the machine stopped those too.
    #    The venv's python.exe is a redirector that starts the real interpreter as its child, so
    #    the child is found through its parent.
    Step "Stopping this copy of Asset Studio if it is running..."
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $mine = @($all | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains("asset_studio.main") -and $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($venvDir, [System.StringComparison]::OrdinalIgnoreCase) })
    $mineIds = @($mine | ForEach-Object { $_.ProcessId })
    $stop = @($mine)
    $stop += @($all | Where-Object { $_.CommandLine -and $_.CommandLine.Contains("asset_studio.main") -and ($mineIds -contains $_.ParentProcessId) })
    $stop += @($all | Where-Object { ($_.Name -eq "node.exe" -or $_.Name -eq "electron.exe") -and $_.CommandLine -and
        $_.CommandLine.IndexOf($feMods, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 })
    foreach ($p in $stop) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    if ($stop.Count -gt 0) { Start-Sleep -Milliseconds 1000; Good ("Stopped " + $stop.Count + " process(es) of this copy.") }

    # 1) prerequisites: Python 3.10+, Node.js and Claude Code. Installed with winget / the official
    #    installer when missing, so a fresh PC needs nothing done by hand first.
    Step "Checking Python..."
    function Refresh-Path {
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $lb = Join-Path $env:USERPROFILE ".local\bin"          # where Claude Code's own installer puts claude.exe
        if ((Test-Path $lb) -and ($env:Path -notlike ("*" + $lb + "*"))) { $env:Path = $lb + ";" + $env:Path }
    }
    function Add-UserPath($dir) {
        # Onto the user PATH for good, as the REGISTRY holds it: the raw value, %VARIABLES% not
        # expanded, and still REG_EXPAND_SZ. [Environment]::SetEnvironmentVariable would write the
        # expanded text back as a plain string - a new account's %USERPROFILE%\...\WindowsApps entry
        # turned into a fixed path. Returns $true when it added the folder.
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
        if (-not $key) { return $false }
        try {
            $raw = [string]$key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $parts = @($raw -split ";" | Where-Object { $_ })
            $have = @($parts | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd("\") })
            if ($have -contains $dir.TrimEnd("\")) { return $false }
            $key.SetValue("Path", (($parts + $dir) -join ";"), [Microsoft.Win32.RegistryValueKind]::ExpandString)
        } finally { $key.Close() }
        # Tell Explorer, so programs started from now on see it (setting a variable that does not
        # exist to nothing is the documented way to broadcast WM_SETTINGCHANGE from PowerShell).
        [Environment]::SetEnvironmentVariable("ASSET_STUDIO_PATH_REFRESH", $null, "User")
        if (($env:Path -split ";") -notcontains $dir) { $env:Path = $env:Path + ";" + $dir }
        return $true
    }
    function Try-Winget($id, $what) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
        Step ("Installing " + $what + " with winget (one time, a few minutes)...")
        $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Host
        $ErrorActionPreference = $eap
        Refresh-Path
        return $true
    }
    function Test-Py($c, $isPy) {
        # A python that really RUNS and is 3.10+. The Store's python.exe stub and a stale PATH entry both fail here.
        if (-not $c -or -not (Test-Path $c)) { return $null }
        $v = $null
        try {
            if ($isPy) { $v = (& $c -3 -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null | Select-Object -First 1) }
            else { $v = (& $c -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null | Select-Object -First 1) }
        } catch { $v = $null }
        if ($v -and ("$v" -match '^(\d+)\.(\d+)$')) {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 10)) { return @{ exe = $c; ver = "$v"; py3 = $isPy } }
        }
        return $null
    }
    function Find-Python {
        $cands = @()
        foreach ($c in @("py", "python", "python3")) { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $cands += $g.Source } }
        foreach ($d in @((Join-Path $env:LOCALAPPDATA "Programs\Python"), "C:\Program Files", "C:\")) {
            if (Test-Path $d) {
                Get-ChildItem $d -Directory -Filter "Python3*" -ErrorAction SilentlyContinue | Sort-Object Name -Descending |
                    ForEach-Object { $cands += (Join-Path $_.FullName "python.exe") }
            }
        }
        foreach ($c in $cands) {
            $r = Test-Py $c ((Split-Path $c -Leaf) -ieq "py.exe")
            if ($r) { return $r }
        }
        return $null
    }
    $found = $null
    if ($PythonExe) {
        $found = Test-Py $PythonExe $false
        if (-not $found) { Fail ("The Python given (" + $PythonExe + ") does not run or is older than 3.10.") }
    }
    if (-not $found) { $found = Find-Python }
    if (-not $found) {
        if (Try-Winget "Python.Python.3.12" "Python 3.12") { $found = Find-Python }
    }
    if (-not $found) { Fail "Python 3.10+ not found and winget could not install it. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again." }
    $pyExe = $found.exe; $pyVer = $found.ver
    $pyArgs = @(); if ($found.py3) { $pyArgs = @("-3") }
    Good ("Python " + $pyVer + " (" + $pyExe + ")")

    # Node.js: needed to BUILD the UI. An installed copy ships the UI built, so there Node is only
    # for running web games, gltf-transform (compressed models) and the optional Codex CLI.
    Step "Checking Node.js..."
    $needNode = -not $SkipUiBuild
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        if ($needNode -or $InstallNode) { Try-Winget "OpenJS.NodeJS.LTS" "Node.js LTS" | Out-Null }
    }
    if (Get-Command npm -ErrorAction SilentlyContinue) { Good ("Node " + (node --version)) }
    elseif ($needNode) { Fail "Node.js not found and winget could not install it. Install the LTS from https://nodejs.org/, then run this again." }
    else { Warn "Node.js is not installed. The Studio runs without it; install the LTS from https://nodejs.org/ to run web games and open compressed models." }

    # Claude Code itself, so a new PC can send its first message. Logging in stays a one-time step.
    # The official installer first (no Node needed, and it is the copy that updates itself), npm second.
    if ($NoClaude) { Step "Claude Code: skipped (-NoClaude)." }
    else {
        Refresh-Path
        if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
            Step "Installing Claude Code (the official installer from claude.ai)..."
            $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            try { & powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex" 2>&1 | Out-Host } catch {}
            $ErrorActionPreference = $eap
            Refresh-Path
            if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
                Step "Trying npm instead (npm install -g @anthropic-ai/claude-code)..."
                $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                npm install -g @anthropic-ai/claude-code --no-fund --no-audit 2>&1 | Out-Host
                $ErrorActionPreference = $eap
                Refresh-Path
            }
        }
        # Claude's own installer puts claude.exe in ~\.local\bin and then asks you to put that folder
        # on PATH BY HAND ("Native installation exists but ...\.local\bin is not in your PATH").
        # Until it is, a new terminal - and the Studio, started from Explorer - cannot find claude.
        $lb = Join-Path $env:USERPROFILE ".local\bin"
        if (Test-Path (Join-Path $lb "claude.exe")) {
            if (Add-UserPath $lb) { Good ("Put " + $lb + " on your PATH, as Claude Code's installer asks.") }
        }
        $cl = Get-Command claude -ErrorAction SilentlyContinue
        if ($cl) { Good ("Claude Code: " + $cl.Source + ". Log in once: the app asks, or run 'claude' in a terminal.") }
        else { Warn "Claude Code did not install; Settings in the app can install it later, or run: irm https://claude.ai/install.ps1 | iex" }
    }

    # 2) Python venv - create it, or REBUILD it if the existing one is broken/incomplete
    $venvGood = $false
    if (Test-Path $venvPy) {
        try { & $venvPy -c "import ensurepip" 2>$null; if ($LASTEXITCODE -eq 0) { $venvGood = $true } } catch {}
    }
    if (-not $venvGood) {
        if (Test-Path $venvDir) {
            Step "The Python environment is missing pieces - rebuilding it..."
            try { Remove-Item $venvDir -Recurse -Force }
            catch { Fail "Could not replace the old environment - is Asset Studio still open? Close it (tray icon -> Quit) and run this again." }
        }
        else { Step "Creating the Python environment..." }
        & $pyExe @pyArgs -m venv $venvDir
        if (-not (Test-Path $venvPy)) { Fail "Could not create the Python environment. Reinstall Python 3.10+ and run this again." }
    }
    Good "Python environment ready."

    # 3) backend dependencies: the bundled wheels first (works with no internet), then PyPI with
    #    prebuilt wheels preferred so nothing has to compile from source.
    Step "Installing backend dependencies (can take a few minutes the first time)..."
    $req = Join-Path $backend "requirements.txt"
    $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $pipOk = $false
    if ($Wheelhouse) {
        & $venvPy -m pip install --no-index --find-links $Wheelhouse --upgrade pip --quiet 2>&1 | Out-Null
        & $venvPy -m pip install --no-index --find-links $Wheelhouse -r $req
        if ($LASTEXITCODE -eq 0) { $pipOk = $true; Good "Installed from the bundled wheels (no download)." }
        else { Warn "The bundled wheels did not cover everything - trying the internet for the rest." }
    }
    if (-not $pipOk) {
        & $venvPy -m pip install --upgrade pip --quiet
        if ($Wheelhouse) { & $venvPy -m pip install --prefer-binary --find-links $Wheelhouse -r $req }
        else { & $venvPy -m pip install --prefer-binary -r $req }
        if ($LASTEXITCODE -eq 0) { $pipOk = $true }
    }
    $ErrorActionPreference = $eap
    if (-not $pipOk) {
        Fail ("Installing backend dependencies failed (see the pip messages above). " +
              "If it mentions a COMPILER or 'building from source' (e.g. for numpy/scipy), your Python " +
              "(" + $pyVer + ") is too new for a prebuilt download - install Python 3.12 from " +
              "https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), delete backend\.venv, " +
              "and run this again. Otherwise check your internet connection and re-run.")
    }
    Good "Backend dependencies installed."

    # 4) VERIFY the backend actually imports - this is exactly what 'Backend didn't start' means.
    #    It also checks the FEATURE endpoints are mounted: importing proves the code parses, but a
    #    router that failed to register leaves a whole tab dead at runtime with no error anywhere.
    Step "Verifying the backend loads..."
    $verify = @'
import sys
sys.path.insert(0, sys.argv[1])      # run as a FILE, so the backend dir is not on sys.path by default
import asset_studio.main as m
def paths(routes):
    out = []
    for r in routes:
        p = getattr(r, "path", None)
        if p:
            out.append(p)
        sub = getattr(r, "original_router", None)     # FastAPI keeps included routers nested
        if sub is not None:
            out += paths(getattr(sub, "routes", []) or [])
    return out
have = set(paths(m.app.routes))
need = ["/api/health", "/api/settings", "/api/plugins", "/api/chat-providers", "/api/plans",
        "/api/bridge/{pid}/v1/messages", "/api/mission/agents", "/api/workspace/raw",
        "/api/jobs", "/api/providers",
        # The Studio engine, the forge and what every agent is told about. A router that fails to
        # register leaves a whole window dead at runtime with no error anywhere, so name them here
        # and let setup refuse rather than let the user find out by opening the tab.
        "/api/engine/projects", "/api/engine/module", "/api/engine/loader",
        "/api/live/forge", "/api/live/look", "/api/live/bench",
        "/api/graphify/query", "/api/web/fetch", "/api/web/search", "/api/review/render"]
missing = [n for n in need if n not in have]
print("MISSING_ROUTES " + ", ".join(missing) if missing else "IMPORT_OK %d routes" % len(have))
'@
    # Run it from a FILE, not `python -c`: passing a multi-line script as one native-command
    # argument makes Windows strip the embedded double quotes, and the probe dies on a
    # SyntaxError that has nothing to do with the app.
    $verifyFile = Join-Path ([System.IO.Path]::GetTempPath()) "asset-studio-verify.py"
    # UTF-8 with NO byte-order mark: Set-Content -Encoding utf8 adds one on Windows PowerShell 5.1.
    [System.IO.File]::WriteAllText($verifyFile, $verify, (New-Object System.Text.UTF8Encoding($false)))
    Push-Location $backend
    $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $probe = (& $venvPy $verifyFile $backend 2>&1 | Out-String)
    $probeCode = $LASTEXITCODE
    $ErrorActionPreference = $eap
    Pop-Location
    Remove-Item $verifyFile -ErrorAction SilentlyContinue
    if ($probeCode -ne 0 -or $probe -notmatch "IMPORT_OK") {
        Write-Host ""
        Write-Host $probe.Trim() -ForegroundColor Red
        Fail "The backend could not load (error above). Run this setup again; if it still fails, send me that red text."
    }
    # Only the probe's own line: Python warnings on stderr arrive in $probe too.
    $routes = ""
    if ($probe -match 'IMPORT_OK (\d+) routes') { $routes = $Matches[1] }
    Good ("Backend loads cleanly - " + $routes + " routes served.")

    # 5) the UI: built here, or shipped built (the installer .exe) - then there is nothing to do.
    if ($SkipUiBuild) {
        if (-not (Test-Path $distIndex) -or -not (Test-Path $electronExe)) {
            Fail "This copy has no prebuilt UI (frontend\dist and node_modules\electron). Run setup again without -SkipUiBuild."
        }
        Good "UI: prebuilt, nothing to build."
    }
    else {
        Push-Location $frontend
        Step "Installing / updating the UI dependencies..."
        npm install --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install failed (see output above). Check Node.js / your internet, then run this again." }
        Step "Building the UI..."
        # the app is one large bundle; on a low-RAM fresh PC the tsc+vite build can OOM
        # ("JavaScript heap out of memory") — give Node headroom so setup doesn't fail there.
        $env:NODE_OPTIONS = "--max-old-space-size=4096"
        npm run build
        $buildCode = $LASTEXITCODE
        Remove-Item Env:\NODE_OPTIONS -ErrorAction SilentlyContinue
        Pop-Location
        if ($buildCode -ne 0 -or -not (Test-Path $distIndex)) { Fail "Building the UI failed (see output above)." }
        Good "UI built."
    }

    # 6) bundled Claude skills -> the Claude folder's skills (disabled by default; enable in the
    #    Skills tab). Claude Code's folder is CLAUDE_CONFIG_DIR when that is set, else ~\.claude.
    $bundled = Join-Path $root "skills"
    if (Test-Path $bundled) {
        if ($env:CLAUDE_CONFIG_DIR) { $skillsDest = Join-Path $env:CLAUDE_CONFIG_DIR "skills" }
        else { $skillsDest = Join-Path $env:USERPROFILE ".claude\skills" }
        New-Item -ItemType Directory -Force -Path $skillsDest | Out-Null
        $added = 0
        foreach ($s in Get-ChildItem $bundled -Directory) {
            $d = Join-Path $skillsDest $s.Name
            if (-not (Test-Path $d)) { Copy-Item $s.FullName $d -Recurse; $added++ }
        }
        Good ("Bundled skills: " + $added + " new installed in " + $skillsDest + " (disabled by default).")
    }

    # Every backend installer below prints one line: READY, or FAILED with the reason.
    function Run-Backend($code) {
        $f = Join-Path ([System.IO.Path]::GetTempPath()) ("asset-studio-step-" + [guid]::NewGuid().ToString("N") + ".py")
        [System.IO.File]::WriteAllText($f, $code, (New-Object System.Text.UTF8Encoding($false)))
        Push-Location $backend
        $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $out = ""
        try { $out = (& $venvPy $f $backend 2>&1 | Out-String).Trim() } catch { $out = "FAILED: " + $_.Exception.Message }
        $ErrorActionPreference = $eap
        Pop-Location
        Remove-Item $f -ErrorAction SilentlyContinue
        return $out
    }

    # 6b) graphify - the code knowledge-graph behind the Graphify toggle, in the backend's OWN
    #     venv under data\ (no pipx): the exact path the app uses at runtime. Three pieces, and a
    #     new PC needs all three: the package, the /graphify Claude skill, and a `graphify`
    #     command on PATH - the skill runs that command in the terminal.
    Step "Installing graphify (the code graph agents search first)..."
    $gOut = Run-Backend @'
import sys; sys.path.insert(0, sys.argv[1])
from asset_studio import graphify_index as g
ok = g.ensure_installed(block=True, timeout=900)
if ok:
    print("READY")
    print("EXE=" + str(g.graphify_exe() or ""))
    print("SKILL=" + ("1" if g.ensure_skill() else "0"))
else:
    print("FAILED: " + str(g.install_status().get("error") or "unknown"))
'@
    if ($gOut -match 'READY') {
        Good "graphify ready - the Graphify toggle builds a graph in EVERY project."
        if ($gOut -match 'SKILL=1') { Good "The /graphify skill is installed for Claude Code." }
        else { Warn "The /graphify skill could not be written; the Studio tries again when it starts." }
        # The command itself. When the PC has none, the Studio's copy goes into its own bin folder
        # (that folder holds nothing else, so no other python or pip is put on PATH).
        $gExe = ""
        if ($gOut -match '(?m)^EXE=(.+)$') { $gExe = $Matches[1].Trim() }
        if (-not (Get-Command graphify -ErrorAction SilentlyContinue) -and $gExe -and (Test-Path $gExe)) {
            $binDir = Join-Path $root "bin"
            New-Item -ItemType Directory -Force -Path $binDir | Out-Null
            Copy-Item $gExe (Join-Path $binDir "graphify.exe") -Force
            Add-UserPath $binDir | Out-Null
            Good ("graphify command: " + (Join-Path $binDir "graphify.exe") + " (its folder is now on your PATH)")
        }
    }
    else {
        Warn ("graphify install problem -> " + $gOut)
        Write-Host "  (Not fatal: the Studio retries this same install the first time it starts.)" -ForegroundColor Yellow
    }

    # 6c) Scrapling - the web fetch that gets past blocked pages, and the key-free web search.
    #     Its own venv under data\tools, and a Chromium where its worker looks for one.
    Step "Installing the web tools (Scrapling + its browser; a few minutes the first time)..."
    $wOut = Run-Backend @'
import sys; sys.path.insert(0, sys.argv[1])
from asset_studio import web_tools as w
s = w.ensure_installed(block=True, timeout=2400)
if s.get("available") and s.get("browsers"):
    print("READY")
elif s.get("available"):
    print("PARTIAL: the packages are in, the browser is not: " + str(s.get("error") or "no error recorded"))
else:
    print("FAILED: " + str(s.get("error") or "unknown"))
'@
    if ($wOut -match '^READY') { Good "Web tools ready - fetch and search work, blocked pages included." }
    else {
        Warn ("web tools: " + $wOut)
        Write-Host "  (Not fatal: the Studio finishes this itself the next time it starts with internet.)" -ForegroundColor Yellow
    }

    # 6d) a browser for the Studio Engine (the forge, the reviews, the live game link). Chrome or
    #     Edge when the PC has one - every Windows PC has Edge - else a headless Chrome is fetched.
    Step "Checking the browser the Studio Engine renders with..."
    $bOut = Run-Backend @'
import sys; sys.path.insert(0, sys.argv[1])
from asset_studio import workspace, browser_install
exe = workspace._find_chrome()
if not exe:
    st = browser_install.ensure_installed(block=True, timeout=1800)
    exe = st.get("exe") or ""
    if not exe:
        print("FAILED: " + str(st.get("error") or "no browser"))
        raise SystemExit(0)
print("READY " + exe)
'@
    if ($bOut -match '^READY (.+)$') { Good ("Studio Engine browser: " + $Matches[1]) }
    else {
        Warn ("browser: " + $bOut)
        Write-Host "  (Not fatal: the Studio downloads one itself the first time the forge is used.)" -ForegroundColor Yellow
    }

    # 6e) gltf-transform - a game's models are usually compressed (Draco, meshopt), and the Studio
    #     Engine opens them by decoding them with this; agents shrink GLBs with it too. A Node CLI.
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Step "Checking gltf-transform (opens compressed game models in the Studio Engine)..."
        if (-not (Get-Command gltf-transform -ErrorAction SilentlyContinue)) {
            $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            try { & npm install -g "@gltf-transform/cli" --no-fund --no-audit 2>&1 | Out-Null } catch {}
            $ErrorActionPreference = $eap
            Refresh-Path
        }
        # npm's global folder is on PATH when Node's own installer made it; when Node came some
        # other way it may not be, and then neither the Studio nor an agent finds the command.
        if (-not (Get-Command gltf-transform -ErrorAction SilentlyContinue)) {
            $npmPrefix = ""
            try { $npmPrefix = ("" + (& npm config get prefix 2>$null | Select-Object -First 1)).Trim() } catch {}
            if ($npmPrefix -and (Test-Path (Join-Path $npmPrefix "gltf-transform.cmd"))) {
                if (Add-UserPath $npmPrefix) { Good ("Put " + $npmPrefix + " (npm's global commands) on your PATH.") }
            }
        }
        if (Get-Command gltf-transform -ErrorAction SilentlyContinue) { Good "gltf-transform ready - compressed models open in the Studio Engine." }
        else { Warn "gltf-transform did not install - compressed models will not open. Later: npm install -g @gltf-transform/cli" }
    }
    else { Warn "gltf-transform needs Node.js: until Node is installed, compressed (Draco) models do not open in the Studio Engine." }

    # 7) OpenAI Codex CLI - the Studio's second chat agent (and the co-run reviewer). Needs npm.
    #    Signing in stays manual (it opens a browser). Non-fatal either way.
    if (-not $NoCodex -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        Step "Checking the Codex agent CLI..."
        if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
            try { $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"; & npm install -g "@openai/codex" --no-fund --no-audit 2>&1 | Out-Null; $ErrorActionPreference = $eap } catch {}
        }
        if (Get-Command codex -ErrorAction SilentlyContinue) {
            $cv = ""
            try { $cv = (& codex --version 2>&1 | Select-Object -First 1) } catch {}
            Good ("Codex CLI ready. " + $cv)
        }
        else { Warn "Codex CLI unavailable (optional) - install later: npm install -g @openai/codex" }
    }

    # 8) optional local AI pack
    if (-not $Yes) {
        Write-Host ""
        $ai = Read-Host "Also install the optional local AI pack (background removal + GPU image/3D)? [y/N]"
        if ($ai -match '^(y|yes)$') {
            $aipack = Join-Path $root "Install AI Pack.ps1"
            if (Test-Path $aipack) { & $aipack -NoPause } else { Warn "Install AI Pack.ps1 not found - skipping." }
        }
    }

    Write-Host ""
    Good "Setup complete - Asset Studio is ready."
    Write-Host "    Launch it with 'Asset Studio.vbs' (or the desktop shortcut)." -ForegroundColor Green
    if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and -not $NoClaude) {
        Warn "Claude Code is not installed yet - the app offers to install it, or run: irm https://claude.ai/install.ps1 | iex"
    }
    Write-Host ""
    try { Stop-Transcript | Out-Null } catch {}
    if (-not $Yes) { Read-Host "Press Enter to close" }
}
catch {
    Fail ("Setup hit an error: " + $_.Exception.Message)
}
