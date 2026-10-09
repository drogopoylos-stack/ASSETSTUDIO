param([string]$ComfyDir = (Join-Path $PSScriptRoot '../data/vendor/ComfyUI'))
$ErrorActionPreference = 'Stop'
$ComfyDir = (Resolve-Path -LiteralPath $ComfyDir).Path
$studioDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
foreach ($repo in @(
    @{ Name = 'MiniMaxH3-Director-V1.2'; Url = 'https://github.com/muse-collective-26/MiniMaxH3-Director-V1.2' },
    @{ Name = 'ComfyUI-H3-Motion-Context-MultiRef'; Url = 'https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef' },
    @{ Name = 'Comfyui_Minimax_h3_latent_Upscaler'; Url = 'https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler' }
)) {
    $target = Join-Path $ComfyDir ('custom_nodes/' + $repo.Name)
    if (!(Test-Path -LiteralPath $target)) {
        git clone --depth 1 $repo.Url $target
        if ($LASTEXITCODE) { throw "Could not clone $($repo.Name)" }
    }
}
$python = Join-Path $ComfyDir '.venv/Scripts/python.exe'
& $python -m pip install -r (Join-Path $ComfyDir 'custom_nodes/MiniMaxH3-Director-V1.2/requirements.txt') av
if ($LASTEXITCODE) { throw 'Director Python dependencies could not be installed.' }
$bridge = Join-Path $ComfyDir 'custom_nodes/AssetStudioDirector'
New-Item -ItemType Directory -Path $bridge -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $studioDir 'backend/asset_studio/vendor/comfy_director') |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $bridge -Recurse -Force }
$workflows = Join-Path $ComfyDir 'user/default/workflows'
New-Item -ItemType Directory -Path $workflows -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $studioDir 'frontend/public/minimax-director.json') -Destination (Join-Path $workflows 'Asset Studio - MiniMax Director.json') -Force
Write-Host 'Director installed. Restart ComfyUI. Model weights are installed separately.'
