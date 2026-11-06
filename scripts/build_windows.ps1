# Build a self-contained Windows x64 Opscore folder (no installer).
[CmdletBinding()]
param(
    [string]$Version = $env:OPSCORE_VERSION,
    [string]$PythonVersion = "3.14.10",
    [string]$PythonBuildStandaloneTag = "20261026"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "This build must run on 64-bit Windows."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "This build targets Windows x64."
}
if (-not $Version) {
    $Version = [DateTime]::UtcNow.ToString("yyyy.MM.dd", [Globalization.CultureInfo]::InvariantCulture)
}
if ($Version -notmatch '^[0-9][A-Za-z0-9.+~:-]*$') {
    throw "Invalid Opscore version: $Version"
}
if ($PythonVersion -notmatch '^3\.14\.\d+$') {
    throw "PythonVersion must identify a CPython 3.14 patch release."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dist = Join-Path $Root "dist"
$Work = Join-Path $Dist "build-windows"
$App = Join-Path $Work "app"
$Output = Join-Path $Dist "Opscore"
$Downloads = Join-Path $Work "downloads"

function Assert-Build([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Remove-IfPresent([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $Dist -Force | Out-Null
# Keep the work directory so an interrupted build can reuse its runtime and downloads.
New-Item -ItemType Directory -Path $App, $Downloads -Force | Out-Null

Write-Host "==> Downloading standalone CPython $PythonVersion (install_only)"
$AssetName = "cpython-$PythonVersion+$PythonBuildStandaloneTag-x86_64-pc-windows-msvc-install_only.tar.gz"
$ReleaseApi = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/$PythonBuildStandaloneTag"
$Release = Invoke-RestMethod -Uri $ReleaseApi -Headers @{ "User-Agent" = "Opscore-build" }
$Asset = $Release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
Assert-Build ($null -ne $Asset) "python-build-standalone asset not found: $AssetName"
Assert-Build ($Asset.digest -match '^sha256:[0-9a-f]{64}$') "GitHub did not provide a SHA-256 digest for $AssetName"
$Archive = Join-Path $Downloads $AssetName
$ExpectedHash = $Asset.digest.Substring(7)
if (Test-Path -LiteralPath $Archive) {
    $CachedHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($CachedHash -eq $ExpectedHash) {
        Write-Host "    Reusing the downloaded Python archive"
    }
    else {
        Remove-Item -LiteralPath $Archive -Force
    }
}
if (-not (Test-Path -LiteralPath $Archive)) {
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Archive
}
$ActualHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-Build ($ActualHash -eq $ExpectedHash) "Checksum mismatch for $AssetName"

$Python = Join-Path $App "python\python.exe"
$PythonW = Join-Path $App "python\pythonw.exe"
$DependenciesStamp = Join-Path $Work "dependencies-installed.txt"
if (-not (Test-Path -LiteralPath $Python)) {
    $Tar = Get-Command tar -ErrorAction SilentlyContinue
    Assert-Build ($null -ne $Tar) "tar.exe is required (included with current Windows 10/11)."
    Remove-IfPresent (Join-Path $Work "python")
    & $Tar.Source -xzf $Archive -C $Work
    if ($LASTEXITCODE -ne 0) {
        throw "Could not extract the standalone Python archive."
    }
    $ExtractedPython = Join-Path $Work "python"
    Assert-Build (Test-Path -LiteralPath (Join-Path $ExtractedPython "python.exe")) "The archive did not contain python\python.exe."
    Move-Item -LiteralPath $ExtractedPython -Destination (Join-Path $App "python")
    Remove-IfPresent $DependenciesStamp
}
else {
    Write-Host "    Reusing the extracted Python runtime"
}

# python --version avoids Windows PowerShell 5.1's native-argument quote removal.
$BundledVersionOutput = (& $Python --version) -join " "
if ($LASTEXITCODE -ne 0 -or $BundledVersionOutput -notmatch '^Python 3\.14\.\d+') {
    throw "Expected bundled Python 3.14, got $BundledVersionOutput."
}

$RequirementsHash = @(
    (Get-FileHash -LiteralPath (Join-Path $Root "requirements.lock") -Algorithm SHA256).Hash
    (Get-FileHash -LiteralPath (Join-Path $Root "requirements-windows.txt") -Algorithm SHA256).Hash
    $AssetName
) -join "`n"
$DependenciesInstalled = (Test-Path -LiteralPath $DependenciesStamp) -and
    ((Get-Content -LiteralPath $DependenciesStamp -Raw) -eq $RequirementsHash)
if (-not $DependenciesInstalled) {
    Write-Host "==> Installing locked Windows dependencies"
    & $Python -I -m pip install --no-cache-dir -r (Join-Path $Root "requirements.lock") -r (Join-Path $Root "requirements-windows.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
    Set-Content -LiteralPath $DependenciesStamp -Value $RequirementsHash -Encoding Ascii -NoNewline
}
else {
    Write-Host "==> Reusing installed Windows dependencies"
}

Write-Host "==> Copying application (without databases or development data)"
New-Item -ItemType Directory -Path (Join-Path $App "ducklab"), (Join-Path $App "assets"), (Join-Path $App "packaging") -Force | Out-Null
Copy-Item -Path (Join-Path $Root "ducklab\*.py") -Destination (Join-Path $App "ducklab")
Get-ChildItem -LiteralPath (Join-Path $Root "assets") -File | Copy-Item -Destination (Join-Path $App "assets")
Copy-Item -LiteralPath (Join-Path $Root "rxconfig.py") -Destination $App
Copy-Item -LiteralPath (Join-Path $Root "packaging\opscore_desktop.py"), (Join-Path $Root "packaging\opscore_backend.py") -Destination (Join-Path $App "packaging")
Set-Content -LiteralPath (Join-Path $App "RELEASE") -Value $Version -Encoding Ascii -NoNewline
Assert-Build (Test-Path -LiteralPath (Join-Path $App "assets\opscore.ico")) "The Opscore icon was not copied."
Assert-Build (Test-Path -LiteralPath $PythonW) "The windowed Python launcher is missing."

Write-Host "==> Building the production frontend once"
# Reflex installs its own Bun and locked frontend dependencies in a clean tree.
# Those build tools and node_modules are removed before the folder is published.
Remove-IfPresent (Join-Path $App ".web")
Remove-IfPresent (Join-Path $App "reflex.lock")
if (Test-Path -LiteralPath (Join-Path $Root ".web")) {
    Copy-Item -LiteralPath (Join-Path $Root ".web") -Destination (Join-Path $App ".web") -Recurse
    Remove-IfPresent (Join-Path $App ".web\env.json")
}
if (Test-Path -LiteralPath (Join-Path $Root "reflex.lock")) {
    Copy-Item -LiteralPath (Join-Path $Root "reflex.lock") -Destination (Join-Path $App "reflex.lock") -Recurse
}
$BuildDatabase = Join-Path $Work "build.db"
Push-Location $App
try {
    $env:OPSCORE_DESKTOP = "1"
    $env:DUCKLAB_DB_PATH = $BuildDatabase
    $env:DUCKLAB_RELEASE_ID = $Version
    $env:REFLEX_ENV_MODE = "prod"
    $env:REFLEX_CHECK_LATEST_VERSION = "false"
    & $Python -m reflex export --env prod --frontend-only --no-zip
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend export failed."
    }
}
finally {
    Pop-Location
}
Assert-Build (Test-Path -LiteralPath (Join-Path $App ".web\build\client\index.html")) "Frontend export missing."
Assert-Build (Test-Path -LiteralPath (Join-Path $App ".web\build\client\login.html")) "Login frontend export missing."

$VerifyEndpoints = @'
import json
import pathlib
import sys

raw = pathlib.Path(sys.argv[1]).read_text()
endpoints = json.loads(raw)
assert endpoints["EVENT"] == "ws://127.0.0.1:47811/_event", endpoints
assert all(
    "127.0.0.1:47811" in url
    for url in endpoints.values()
    if isinstance(url, str) and url.startswith(("http://", "ws://"))
), endpoints
assert "8000" not in raw, endpoints
'@
$VerifyEndpointsScript = Join-Path $Work "verify_endpoints.py"
Set-Content -LiteralPath $VerifyEndpointsScript -Value $VerifyEndpoints -Encoding Ascii
& $Python $VerifyEndpointsScript (Join-Path $App ".web\env.json")
if ($LASTEXITCODE -ne 0) {
    throw "Frontend was not exported for the fixed desktop port 47811."
}

# Runtime serves .web/build directly. Do not ship the frontend source/toolchain.
Get-ChildItem -LiteralPath (Join-Path $App ".web") -Force |
    Where-Object { $_.Name -ne "build" } |
    Remove-Item -Recurse -Force
Remove-IfPresent (Join-Path $App "reflex.lock")
Remove-IfPresent (Join-Path $App ".states")
Remove-IfPresent (Join-Path $App "uploaded_files")
Get-ChildItem -LiteralPath $App -Recurse -Directory -Filter "__pycache__" |
    Sort-Object { $_.FullName.Length } -Descending |
    Remove-Item -Recurse -Force

Write-Host "==> Creating the windowed launcher"
$Launcher = @'
@echo off
setlocal
set "OPSCORE_ROOT=%~dp0"
if not defined LOCALAPPDATA (
    echo Opscore requires %%LOCALAPPDATA%% to store its data.
    pause
    exit /b 1
)
start "Opscore" /D "%OPSCORE_ROOT%" "%OPSCORE_ROOT%python\pythonw.exe" "%OPSCORE_ROOT%packaging\opscore_desktop.py"
if errorlevel 1 (
    echo Opscore could not start. Check %%LOCALAPPDATA%%\Opscore\opscore.log
    pause
    exit /b 1
)
'@
# cmd.exe needs CRLF and no byte-order mark before @echo off.
$Launcher = $Launcher -replace "`r?`n", "`r`n"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $App "Opscore.cmd"), $Launcher, $Utf8NoBom)

Write-Host "==> Validating the self-contained folder"
$ValidateRuntime = @'
import sys

assert sys.version_info[:2] == (3, 14)
import reflex, granian, argon2, openpyxl, webview
'@
$ValidateRuntimeScript = Join-Path $Work "validate_runtime.py"
Set-Content -LiteralPath $ValidateRuntimeScript -Value $ValidateRuntime -Encoding Ascii
& $Python -I $ValidateRuntimeScript
if ($LASTEXITCODE -ne 0) {
    throw "Bundled runtime validation failed."
}
Assert-Build (-not (Test-Path -LiteralPath (Join-Path $App ".venv"))) "A virtual environment must not be packaged."
Assert-Build (-not (Test-Path -LiteralPath (Join-Path $App ".web\node_modules"))) "Frontend build tools must not be packaged."
$Database = Get-ChildItem -LiteralPath $App -Recurse -File -Filter "*.db" | Select-Object -First 1
Assert-Build ($null -eq $Database) "Database files must not be included in the package."

Remove-IfPresent $Output
Move-Item -LiteralPath $App -Destination $Output
$Size = (Get-ChildItem -LiteralPath $Output -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("Built {0} ({1:N0} bytes)" -f $Output, $Size)
Write-Host "Copy the whole Opscore folder and double-click Opscore.cmd. No installer is created."
