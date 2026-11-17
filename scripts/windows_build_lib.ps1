# Shared helpers for the Windows folder build (scripts/build_windows.ps1).
#
# The functions here are pure path/assertion logic with no side effects, so
# they can be exercised directly by the test suite without running a build.
# Dot-source this file; do not execute it.

Set-StrictMode -Version Latest

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

function Get-PackageRuntimePrefix([string]$Path) {
    # Long-form path of the bundled CPython runtime inside the package, or
    # $null when the package has no runtime. The path is taken from
    # Get-ChildItem so it uses the same path form as the items it is compared
    # against; a caller-provided root may use a short 8.3 path.
    $runtime = Get-ChildItem -LiteralPath $Path -Directory -Force |
        Where-Object { $_.Name -eq "python" } |
        Select-Object -First 1
    if ($null -eq $runtime) {
        return $null
    }
    return $runtime.FullName.TrimEnd("\")
}

function Test-IsRuntimePath([string]$RuntimePrefix, [string]$FullName) {
    # True when a path is inside the bundled runtime subtree.
    if (-not $RuntimePrefix) {
        return $false
    }
    return $FullName.StartsWith("$RuntimePrefix\", [StringComparison]::OrdinalIgnoreCase)
}

function Find-ForbiddenArtifacts([string]$Path) {
    # Report artifacts that must never ship. The bundled CPython runtime is
    # scanned for development files and databases, but its stdlib and
    # site-packages __pycache__/.pyc files are legitimate installed bytecode,
    # not export residue, so bytecode checks exclude the python\ subtree.
    $runtimePrefix = Get-PackageRuntimePrefix $Path
    $artifacts = @(
        Get-ChildItem -LiteralPath $Path -Recurse -Directory -Force |
            Where-Object {
                $_.Name -in @(".venv", "node_modules", ".git", ".idea", ".vscode") -or
                ($_.Name -eq "__pycache__" -and
                 -not (Test-IsRuntimePath $runtimePrefix $_.FullName))
            }
    )
    $artifacts += @(
        Get-ChildItem -LiteralPath $Path -Recurse -File -Force |
            Where-Object {
                $_.Extension -in @(".db", ".sqlite", ".sqlite3") -or
                ($_.Extension -in @(".pyc", ".pyo") -and
                 -not (Test-IsRuntimePath $runtimePrefix $_.FullName))
            }
    )
    return $artifacts
}

function Assert-CleanPackage([string]$Path) {
    $forbidden = @(Find-ForbiddenArtifacts $Path)
    Assert-Build ($forbidden.Count -eq 0) (
        "Forbidden artifacts in the package: " +
        (($forbidden | ForEach-Object { $_.FullName }) -join ", "))
}

function Get-RequiredPackageFiles {
    # Files the shipped folder must contain for Opscore to start.
    return @(
        "python\python.exe",
        "python\pythonw.exe",
        "ducklab\data\DSCSPR_alias_2027.xlsx",
        "ducklab\data\store_directory_2025_2026.xlsx",
        "packaging\opscore_desktop.py",
        "packaging\opscore_backend.py",
        "packaging\opscore_tray.py",
        "assets\opscore.ico",
        ".web\build\client\index.html",
        ".web\build\client\404.html",
        "rxconfig.py",
        "Opscore.cmd",
        "RELEASE"
    )
}

function Assert-PackageContents([string]$Path, [string]$ExpectedVersion) {
    Assert-Build (Test-Path -LiteralPath $Path -PathType Container) "Missing package directory."
    Assert-CleanPackage $Path

    foreach ($relative in (Get-RequiredPackageFiles)) {
        Assert-Build (Test-Path -LiteralPath (Join-Path $Path $relative) -PathType Leaf) `
            "Required package file missing: $relative"
    }

    # Reflex writes these into the project root during export; they are not
    # part of the application and must never be packaged.
    Assert-Build (-not (Test-Path -LiteralPath (Join-Path $Path ".gitignore"))) `
        "Reflex-generated .gitignore must not be packaged."
    Assert-Build (-not (Test-Path -LiteralPath (Join-Path $Path "requirements.txt"))) `
        "Reflex-generated requirements.txt must not be packaged."
    Assert-Build (-not (Test-Path -LiteralPath (Join-Path $Path ".venv"))) `
        "A virtual environment must not be packaged."
    Assert-Build (-not (Test-Path -LiteralPath (Join-Path $Path ".web\node_modules"))) `
        "Frontend build tools must not be packaged."

    $releasePath = Join-Path $Path "RELEASE"
    $packagedVersion = (Get-Content -LiteralPath $releasePath -Raw).Trim()
    Assert-Build ($packagedVersion -eq $ExpectedVersion) `
        "RELEASE version '$packagedVersion' does not match requested version '$ExpectedVersion'."
}

# --- candidate build safety ----------------------------------------------------
# A candidate build must never modify, replace or remove the published release
# artifacts in dist\. These helpers snapshot those artifacts and prove that a
# build left them alone.

function Get-NormalizedPath([string]$Path) {
    # Long-form path when the path exists, otherwise the path as given.
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -ne $item) {
        return $item.FullName.TrimEnd("\")
    }
    return $Path.TrimEnd("\")
}

function Test-SamePath([string]$A, [string]$B) {
    return ((Get-NormalizedPath $A) -ieq (Get-NormalizedPath $B))
}

function Get-ProtectedReleasePaths([string]$Dist) {
    # The published release folder and every published installer in $Dist.
    # Candidate installers ("...-candidate.exe") are build outputs rather than
    # release artifacts, so they are not protected.
    $paths = @()
    $releaseFolder = Join-Path $Dist "Opscore"
    if (Test-Path -LiteralPath $releaseFolder) {
        $paths += $releaseFolder
    }
    $installers = @(
        Get-ChildItem -LiteralPath $Dist -File -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -match '^Opscore-Setup-.+\.exe$' -and
                $_.Name -notmatch '-candidate\.exe$'
            }
    )
    foreach ($installer in $installers) {
        $paths += $installer.FullName
    }
    return $paths
}

function Get-ProtectedArtifactSnapshot([string]$Dist) {
    # Record the state of every published release artifact so a build can
    # prove that it did not modify, replace or remove any of them.
    $snapshot = @{}
    foreach ($path in (Get-ProtectedReleasePaths $Dist)) {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            $snapshot[$path] = @{ Exists = $false; Fingerprint = "" }
        }
        elseif ($item.PSIsContainer) {
            $files = @(Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue)
            $sum = ($files | Measure-Object -Property Length -Sum).Sum
            $snapshot[$path] = @{ Exists = $true; Fingerprint = "$($files.Count)/$sum" }
        }
        else {
            $snapshot[$path] = @{ Exists = $true; Fingerprint = "$($item.Length)" }
        }
    }
    return $snapshot
}

function Assert-ProtectedArtifactsUnchanged([string]$Dist, $Snapshot) {
    # Fail when a published release artifact was created, removed or modified
    # while the build was running.
    Assert-Build ($null -ne $Snapshot) "No protected-artifact snapshot was supplied."
    foreach ($path in $Snapshot.Keys) {
        $before = $Snapshot[$path]
        $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            Assert-Build (-not $before.Exists) `
                "The build removed the protected release artifact: $path"
            continue
        }
        Assert-Build $before.Exists `
            "The build created a new protected release artifact: $path"
        if ($item.PSIsContainer) {
            $files = @(Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue)
            $sum = ($files | Measure-Object -Property Length -Sum).Sum
            $after = "$($files.Count)/$sum"
        }
        else {
            $after = "$($item.Length)"
        }
        Assert-Build ($before.Fingerprint -eq $after) `
            "The build modified the protected release artifact: $path"
    }
}

function Assert-SafeOutputFolder([string]$OutputPath, [string]$Dist, [bool]$AllowReleaseOutput) {
    # A candidate build must not replace the published release folder.
    if ($AllowReleaseOutput) {
        return
    }
    $releaseFolder = Join-Path $Dist "Opscore"
    if (-not (Test-Path -LiteralPath $releaseFolder)) {
        return
    }
    if (Test-SamePath $OutputPath $releaseFolder) {
        throw ("Refusing to replace the published release folder '$releaseFolder'. " +
               "Build into a candidate folder, or pass -AllowReleaseOutput for a release build.")
    }
}

function Get-InstallerFileName([string]$Version, [string]$Suffix = "") {
    # Installer file name for a version, e.g. 1.2 + "-candidate" ->
    # "Opscore-Setup-1.2-candidate.exe".
    return "Opscore-Setup-$Version$Suffix.exe"
}

function Assert-InstallerOutputIsSafe([string]$InstallerPath, [bool]$AllowOverwrite) {
    # Never silently replace an existing installer.
    if ($AllowOverwrite) {
        return
    }
    Assert-Build (-not (Test-Path -LiteralPath $InstallerPath)) `
        ("Refusing to overwrite an existing installer: $InstallerPath. " +
         "Remove it first, or pass -AllowOverwrite.")
}
