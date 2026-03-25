[CmdletBinding()]
param(
    [Alias("Torch")]
    [ValidateSet("auto", "cpu", "cuda")]
    [string]$Device = "auto",

    [ValidateSet("auto", "cu130", "cu129", "cu128", "cu126", "cu125", "cu124", "cu123", "cu122", "cu121", "cu120", "cu118", "cu117", "cu116", "cu115", "cu114", "cu113", "cu112", "cu111", "cu110", "cu102", "cu101", "cu100", "cu92", "cu91", "cu90", "cu80")]
    [string]$CudaBackend = "auto",

    [switch]$SkipPrewarm,

    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

$MarkerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$CacheRoot = Join-Path $MarkerRoot ".cache"
$UvCacheDir = Join-Path $CacheRoot "uv"
$LocalToolsDir = Join-Path $MarkerRoot ".tools"
$LocalUvDir = Join-Path $LocalToolsDir "uv"
$LocalUvExe = Join-Path $LocalUvDir "uv.exe"
$LocalEnvPath = Join-Path $MarkerRoot "local.env"
$VenvPython = Join-Path $MarkerRoot ".venv\Scripts\python.exe"
$PrewarmScript = Join-Path $MarkerRoot "prewarm.py"
$VerifyScript = Join-Path $MarkerRoot "verify.py"

function Write-Step {
    param([string]$Message)
    Write-Host "[install-marker] $Message" -ForegroundColor Cyan
}

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
}

function Resolve-Uv {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) {
        return $uvCommand.Source
    }

    if (Test-Path $LocalUvExe) {
        return $LocalUvExe
    }

    Ensure-Directory $LocalUvDir
    Write-Step "uv not found; installing a local copy into $LocalUvDir"

    $env:UV_UNMANAGED_INSTALL = $LocalUvDir
    try {
        Invoke-RestMethod "https://astral.sh/uv/install.ps1" | Invoke-Expression
    }
    finally {
        Remove-Item Env:UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $LocalUvExe)) {
        throw "uv installation failed. Install uv manually and rerun this script."
    }

    return $LocalUvExe
}

function Test-NvidiaSmi {
    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $nvidiaSmi) {
        return $false
    }

    try {
        & $nvidiaSmi.Source "--query-gpu=name" "--format=csv,noheader" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Should-PromptForCuda {
    if ($PSBoundParameters.ContainsKey("Device")) {
        return $false
    }

    if ($env:CI) {
        return $false
    }

    if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
        return $false
    }

    return Test-NvidiaSmi
}

function Resolve-RequestedDevice {
    if (Should-PromptForCuda) {
        $answer = Read-Host "Detected NVIDIA GPU. Install CUDA-enabled PyTorch for Marker? [y/N]"
        if ($answer -match "^(?i:y|yes)$") {
            return "cuda"
        }
    }

    return $Device
}

function Write-LocalEnv {
    param(
        [string]$PythonRelativePath,
        [string]$TorchDevice
    )

    $lines = @("MARKER_PYTHON=$PythonRelativePath")
    if ($TorchDevice) {
        $lines += "TORCH_DEVICE=$TorchDevice"
    }

    Set-Content -Path $LocalEnvPath -Value $lines -Encoding ASCII
}

function Sync-Environment {
    param([string]$UvExe)

    Write-Step "Syncing marker-worker environment"
    & $UvExe sync --project $MarkerRoot --link-mode copy
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed."
    }
}

function Install-CudaTorch {
    param(
        [string]$UvExe,
        [string]$PythonExec
    )

    Write-Step "Installing CUDA-enabled PyTorch backend: $CudaBackend"
    & $UvExe pip install --python $PythonExec --link-mode copy --reinstall-package torch --torch-backend $CudaBackend torch
    if ($LASTEXITCODE -ne 0) {
        throw "PyTorch installation failed for backend $CudaBackend"
    }
}

function Invoke-Validation {
    param(
        [string]$PythonExec,
        [bool]$SkipModelPrewarm
    )

    if (-not $SkipModelPrewarm) {
        Write-Step "Prewarming Marker models into $CacheRoot"
        & $PythonExec $PrewarmScript
        if ($LASTEXITCODE -ne 0) {
            throw "Marker prewarm failed."
        }
    }
    else {
        Write-Step "Skipping prewarm. Models will download on first parse."
    }

    Write-Step "Verifying Marker worker handshake"
    & $PythonExec $VerifyScript
    if ($LASTEXITCODE -ne 0) {
        throw "Marker verification failed."
    }
}

Ensure-Directory $CacheRoot
Ensure-Directory $UvCacheDir

$env:UV_CACHE_DIR = $UvCacheDir
$env:UV_LINK_MODE = "copy"

$uvExe = Resolve-Uv
Write-Step "Using uv: $uvExe"

if ($RecreateVenv -and (Test-Path (Join-Path $MarkerRoot ".venv"))) {
    Write-Step "Removing existing marker virtualenv"
    Remove-Item -Recurse -Force (Join-Path $MarkerRoot ".venv")
}

Sync-Environment -UvExe $uvExe

if (-not (Test-Path $VenvPython)) {
    throw "Marker virtualenv was not created at $VenvPython"
}

$requestedDevice = Resolve-RequestedDevice
$torchDevice = switch ($requestedDevice) {
    "cpu" { "cpu" }
    "cuda" { "cuda" }
    default { "" }
}

Write-LocalEnv -PythonRelativePath ".venv/Scripts/python.exe" -TorchDevice $torchDevice

if ($requestedDevice -eq "cuda") {
    try {
        Install-CudaTorch -UvExe $uvExe -PythonExec $VenvPython
    }
    catch {
        Write-Warning "$($_.Exception.Message) Falling back to CPU compatibility mode."
        $torchDevice = "cpu"
        Write-LocalEnv -PythonRelativePath ".venv/Scripts/python.exe" -TorchDevice $torchDevice
    }
}

try {
    Invoke-Validation -PythonExec $VenvPython -SkipModelPrewarm $SkipPrewarm.IsPresent
}
catch {
    if ($torchDevice -ne "cpu") {
        $modeLabel = if ($torchDevice) { $torchDevice } else { "auto" }
        Write-Warning "Marker validation failed in '$modeLabel' mode. Retrying with CPU compatibility mode."
        $torchDevice = "cpu"
        Write-LocalEnv -PythonRelativePath ".venv/Scripts/python.exe" -TorchDevice $torchDevice
        Invoke-Validation -PythonExec $VenvPython -SkipModelPrewarm $SkipPrewarm.IsPresent
    }
    else {
        throw
    }
}

Write-Step "Done. Marker env: $VenvPython"
Write-Step "Marker metadata: $LocalEnvPath"
Write-Step "Model cache: $(Join-Path $CacheRoot 'models')"
