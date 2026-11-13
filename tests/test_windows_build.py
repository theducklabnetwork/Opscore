"""Regression tests for the Windows folder build and installer version wiring.

These tests inspect the build/installer sources and, where PowerShell is
available, execute the pure assertion helpers in scripts/windows_build_lib.ps1
against synthetic trees. They never run the real build, install dependencies,
compile an installer, or touch dist/.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "scripts" / "build_windows.ps1"
LIB = ROOT / "scripts" / "windows_build_lib.ps1"
ISS = ROOT / "packaging" / "windows" / "opscore.iss"

# Keep in sync with Get-RequiredPackageFiles in scripts/windows_build_lib.ps1.
REQUIRED_PACKAGE_FILES = (
    r"python\python.exe",
    r"python\pythonw.exe",
    r"ducklab\data\DSCSPR_alias_2027.xlsx",
    r"packaging\opscore_desktop.py",
    r"packaging\opscore_backend.py",
    r"packaging\opscore_tray.py",
    r"assets\opscore.ico",
    r".web\build\client\index.html",
    r".web\build\client\404.html",
    "rxconfig.py",
    "Opscore.cmd",
    "RELEASE",
)


def powershell_command() -> str | None:
    """A PowerShell executable that can dot-source the build helpers."""
    for name in ("pwsh", "powershell"):
        if shutil.which(name):
            return name
    return None


def run_powershell(script: str) -> subprocess.CompletedProcess:
    # -ExecutionPolicy Bypass is needed to dot-source the library on hosts
    # whose default policy is Restricted. Nothing is installed or persisted.
    return subprocess.run(
        [powershell_command(), "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )


@unittest.skipUnless(powershell_command(), "PowerShell is not available")
class TestPackageAssertions(unittest.TestCase):
    """Exercise the real PowerShell assertion helpers on synthetic trees."""

    def run_assertions(self, setup: str) -> str:
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
$pkg = Join-Path $env:TEMP ('opscore-pkg-test-' + [guid]::NewGuid().ToString('N'))
foreach ($rel in (Get-RequiredPackageFiles)) {{
    $dest = Join-Path $pkg $rel
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Set-Content -LiteralPath $dest -Value 'x' -Encoding Ascii
}}
Set-Content -LiteralPath (Join-Path $pkg 'RELEASE') -Value '2.0' -Encoding Ascii -NoNewline
{setup}
Assert-PackageContents $pkg '2.0'
Remove-Item -LiteralPath $pkg -Recurse -Force
Write-Output 'ASSERTIONS_PASSED'
"""
        return run_powershell(script)

    def assert_clean_tree_passes(self):
        result = self.run_assertions("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ASSERTIONS_PASSED", result.stdout)

    def test_clean_package_passes(self):
        self.assert_clean_tree_passes()

    def test_runtime_bytecode_is_allowed(self):
        result = self.run_assertions(
            "New-Item -ItemType Directory -Path (Join-Path $pkg 'python\\Lib\\__pycache__') -Force | Out-Null\n"
            "Set-Content -LiteralPath (Join-Path $pkg 'python\\Lib\\__pycache__\\x.cpython-314.pyc') -Value 'x'\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ASSERTIONS_PASSED", result.stdout)

    def assert_rejected(self, setup: str, message_part: str):
        result = self.run_assertions(setup)
        self.assertNotEqual(result.returncode, 0, "forbidden artifact was accepted")
        self.assertIn(message_part, result.stdout + result.stderr)

    def test_app_pycache_directory_is_rejected(self):
        self.assert_rejected(
            "New-Item -ItemType Directory -Path (Join-Path $pkg 'ducklab\\__pycache__') -Force | Out-Null",
            "Forbidden artifacts",
        )

    def test_app_pyc_file_is_rejected(self):
        self.assert_rejected(
            "Set-Content -LiteralPath (Join-Path $pkg 'ducklab\\app.pyc') -Value 'x'",
            "Forbidden artifacts",
        )

    def test_pyo_file_is_rejected(self):
        self.assert_rejected(
            "Set-Content -LiteralPath (Join-Path $pkg 'packaging\\x.pyo') -Value 'x'",
            "Forbidden artifacts",
        )

    def test_database_is_rejected(self):
        self.assert_rejected(
            "Set-Content -LiteralPath (Join-Path $pkg 'store_ops.db') -Value 'x'",
            "Forbidden artifacts",
        )

    def test_venv_is_rejected(self):
        self.assert_rejected(
            "New-Item -ItemType Directory -Path (Join-Path $pkg '.venv') -Force | Out-Null",
            "Forbidden artifacts",
        )

    def test_node_modules_is_rejected(self):
        self.assert_rejected(
            "New-Item -ItemType Directory -Path (Join-Path $pkg '.web\\node_modules') -Force | Out-Null",
            "Forbidden artifacts",
        )

    def test_gitignore_is_rejected(self):
        self.assert_rejected(
            "Set-Content -LiteralPath (Join-Path $pkg '.gitignore') -Value 'x'",
            ".gitignore must not be packaged",
        )

    def test_requirements_txt_is_rejected(self):
        self.assert_rejected(
            "Set-Content -LiteralPath (Join-Path $pkg 'requirements.txt') -Value 'x'",
            "requirements.txt must not be packaged",
        )

    def test_missing_required_file_is_rejected(self):
        result = self.run_assertions(
            "Remove-Item -LiteralPath (Join-Path $pkg 'python\\pythonw.exe') -Force"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Required package file missing", result.stdout + result.stderr)

    def test_release_version_mismatch_is_rejected(self):
        result = self.run_assertions(
            "Set-Content -LiteralPath (Join-Path $pkg 'RELEASE') -Value '9.9' -Encoding Ascii -NoNewline"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match requested version", result.stdout + result.stderr)


@unittest.skipUnless(powershell_command(), "PowerShell is not available")
class TestCandidateSafety(unittest.TestCase):
    """A candidate build must not touch the published release artifacts."""

    def run_script(self, script: str) -> subprocess.CompletedProcess:
        return run_powershell(script)

    def make_release_tree(self) -> tuple[str, str]:
        """Create a fake dist\ with a release folder and a published installer."""
        dist = Path(self.tmp.name) / "dist"
        release = dist / "Opscore"
        (release / "python").mkdir(parents=True)
        (release / "RELEASE").write_text("1.1")
        (release / "python" / "python.exe").write_text("runtime")
        installer = dist / "Opscore-Setup-1.1.exe"
        installer.write_text("published installer")
        return str(dist), str(release)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dist, self.release = self.make_release_tree()

    def tearDown(self):
        self.tmp.cleanup()

    def test_candidate_output_is_rejected_for_the_release_folder(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
try {{
    Assert-SafeOutputFolder (Join-Path '{self.dist}' 'Opscore') '{self.dist}' $false
    Write-Output 'NOT_REJECTED'
}} catch {{
    Write-Output ('REJECTED: ' + $_.Exception.Message)
}}
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REJECTED", result.stdout)
        self.assertIn("Refusing to replace the published release folder", result.stdout)

    def test_candidate_output_folder_is_allowed(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
Assert-SafeOutputFolder (Join-Path '{self.dist}' 'Opscore-candidate') '{self.dist}' $false
Write-Output 'ALLOWED'
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ALLOWED", result.stdout)

    def test_release_output_is_allowed_when_explicitly_requested(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
Assert-SafeOutputFolder (Join-Path '{self.dist}' 'Opscore') '{self.dist}' $true
Write-Output 'ALLOWED'
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ALLOWED", result.stdout)

    def test_modified_release_folder_is_detected(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
$snapshot = Get-ProtectedArtifactSnapshot '{self.dist}'
Set-Content -LiteralPath (Join-Path '{self.release}' 'RELEASE') -Value 'tampered'
try {{
    Assert-ProtectedArtifactsUnchanged '{self.dist}' $snapshot
    Write-Output 'NOT_DETECTED'
}} catch {{
    Write-Output ('DETECTED: ' + $_.Exception.Message)
}}
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DETECTED", result.stdout)

    def test_removed_release_folder_is_detected(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
$snapshot = Get-ProtectedArtifactSnapshot '{self.dist}'
Remove-Item -LiteralPath '{self.release}' -Recurse -Force
try {{
    Assert-ProtectedArtifactsUnchanged '{self.dist}' $snapshot
    Write-Output 'NOT_DETECTED'
}} catch {{
    Write-Output ('DETECTED: ' + $_.Exception.Message)
}}
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DETECTED", result.stdout)
        self.assertIn("removed the protected release artifact", result.stdout)

    def test_modified_published_installer_is_detected(self):
        installer = Path(self.dist) / "Opscore-Setup-1.1.exe"
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
$snapshot = Get-ProtectedArtifactSnapshot '{self.dist}'
Set-Content -LiteralPath '{installer}' -Value 'tampered installer'
try {{
    Assert-ProtectedArtifactsUnchanged '{self.dist}' $snapshot
    Write-Output 'NOT_DETECTED'
}} catch {{
    Write-Output ('DETECTED: ' + $_.Exception.Message)
}}
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DETECTED", result.stdout)

    def test_unchanged_release_artifacts_pass(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
$snapshot = Get-ProtectedArtifactSnapshot '{self.dist}'
Assert-ProtectedArtifactsUnchanged '{self.dist}' $snapshot
Write-Output 'UNCHANGED'
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UNCHANGED", result.stdout)

    def test_candidate_installer_is_not_protected(self):
        """A candidate installer is a build output, not a release artifact."""
        candidate = Path(self.dist) / "Opscore-Setup-1.2-candidate.exe"
        candidate.write_text("candidate")
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
$snapshot = Get-ProtectedArtifactSnapshot '{self.dist}'
$protected = @($snapshot.Keys)
Write-Output ('PROTECTED_COUNT=' + $protected.Count)
foreach ($p in $protected) {{ Write-Output ('PROTECTED=' + $p) }}
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PROTECTED_COUNT=2", result.stdout)
        self.assertNotIn("candidate.exe", result.stdout)

    def test_existing_installer_is_not_overwritten(self):
        target = Path(self.dist) / "Opscore-Setup-1.2-candidate.exe"
        target.write_text("existing")
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
try {{
    Assert-InstallerOutputIsSafe '{target}' $false
    Write-Output 'NOT_REJECTED'
}} catch {{
    Write-Output ('REJECTED: ' + $_.Exception.Message)
}}
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REJECTED", result.stdout)
        self.assertIn("Refusing to overwrite an existing installer", result.stdout)

    def test_candidate_installer_file_name(self):
        script = f"""
$ErrorActionPreference = 'Stop'
. {str(LIB)!r}
Write-Output ('CANDIDATE=' + (Get-InstallerFileName '1.2' '-candidate'))
Write-Output ('RELEASE=' + (Get-InstallerFileName '1.2'))
"""
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CANDIDATE=Opscore-Setup-1.2-candidate.exe", result.stdout)
        self.assertIn("RELEASE=Opscore-Setup-1.2.exe", result.stdout)


class TestBuildScriptStructure(unittest.TestCase):
    def setUp(self):
        self.build = BUILD.read_text(encoding="utf-8-sig")
        self.lib = LIB.read_text(encoding="utf-8-sig")

    def test_app_tree_is_rebuilt_from_scratch(self):
        self.assertRegex(self.build, r"Remove-IfPresent \$App\b")
        # The fresh tree is created after removal, before any copying.
        self.assertLess(self.build.index("Remove-IfPresent $App"),
                        self.build.index("Copying application"))

    def test_download_and_runtime_caches_are_preserved(self):
        self.assertIn('$Downloads = Join-Path $Work "downloads"', self.build)
        self.assertIn('$Runtime = Join-Path $Work "python"', self.build)
        self.assertIn("Reusing the downloaded Python archive", self.build)
        self.assertIn("Reusing the extracted Python runtime", self.build)
        # The reusable runtime is cached outside the purged app tree; the copy
        # inside $App\python is expected, so only the cache location is checked.
        self.assertIn('$Python = Join-Path $Runtime "python.exe"', self.build)
        self.assertNotIn('$Python = Join-Path $App "python\\python.exe"', self.build)

    def test_reflex_generated_root_files_are_removed_after_export(self):
        export = self.build.index("reflex export")
        for artifact in ('.gitignore', 'requirements.txt'):
            removal = f'Remove-IfPresent (Join-Path $App "{artifact}")'
            self.assertIn(removal, self.build)
            self.assertGreater(self.build.index(removal), export,
                               f"{artifact} must be removed after reflex export")

    def test_post_export_cleanup_removes_bytecode_outside_runtime(self):
        self.assertIn("Test-IsRuntimePath $RuntimePrefix $_.FullName", self.build)
        self.assertIn('.Extension -in @(".pyc", ".pyo")', self.build)

    def test_forbidden_artifact_assertion_runs_before_publish(self):
        self.assertIn("Assert-PackageContents $App $Version", self.build)
        self.assertLess(self.build.index("Assert-PackageContents $App $Version"),
                        self.build.index("Move-Item -LiteralPath $App -Destination $Output"))

    def test_pip_is_upgraded_before_locked_dependency_install(self):
        upgrade = self.build.index('pip install --no-cache-dir --upgrade pip')
        locked_install = self.build.index('-r (Join-Path $Root "requirements.lock")')
        self.assertLess(upgrade, locked_install,
                        "the bundled pip must be upgraded before locked deps")

    def test_tray_module_and_dependencies_are_packaged(self):
        self.assertIn('packaging\\opscore_tray.py', self.build)
        self.assertIn('"packaging\\opscore_tray.py"', self.lib)
        self.assertIn('import reflex, granian, argon2, openpyxl, webview, pystray, PIL', self.build)

    def test_default_output_is_a_candidate_folder(self):
        self.assertIn('$OutputName = if ($AllowReleaseOutput) { "Opscore" } else { "Opscore-candidate" }',
                      self.build)
        self.assertIn('[switch]$AllowReleaseOutput', self.build)

    def test_release_output_requires_explicit_opt_in(self):
        self.assertIn('if ($AllowReleaseOutput -and $OutputName -ne "Opscore")', self.build)
        self.assertIn('Assert-SafeOutputFolder $Output $Dist ([bool]$AllowReleaseOutput)', self.build)

    def test_build_snapshots_and_rechecks_release_artifacts(self):
        snapshot = self.build.index("$ProtectedArtifacts = Get-ProtectedArtifactSnapshot $Dist")
        publish = self.build.index("Move-Item -LiteralPath $App -Destination $Output")
        recheck = self.build.index("Assert-ProtectedArtifactsUnchanged $Dist $ProtectedArtifacts")
        self.assertLess(snapshot, publish,
                        "the release artifacts must be snapshotted before publishing")
        self.assertGreater(recheck, publish,
                           "the release artifacts must be rechecked after publishing")

    def test_build_does_not_promote_candidate_artifacts(self):
        # No step copies, renames or moves the candidate into the release
        # folder or over an existing installer.
        self.assertNotRegex(self.build, r'Move-Item[^\n]*-Destination \$Output.*\n.*Opscore-Setup')
        self.assertNotIn("Copy-Item", self.build[self.build.index("Remove-IfPresent $Output"):])

    def test_candidate_installer_name_is_reported(self):
        self.assertIn('Get-InstallerFileName $Version "-candidate"', self.build)

    def test_output_name_rejects_path_separators(self):
        self.assertIn("Invalid output folder name", self.build)

    def test_no_phase2_or_dependency_changes(self):
        # The build still uses standalone CPython + Reflex + Inno Setup.
        self.assertIn("python-build-standalone", self.build)
        self.assertIn("reflex export", self.build)
        self.assertNotIn("PyInstaller", self.build)
        self.assertNotIn("Nuitka", self.build)

    def test_stale_frontend_is_not_copied_from_source_checkout(self):
        self.assertNotIn('Join-Path $Root ".web"', self.build,
                         "the source .web tree must not be reused for the package")
        self.assertIn('Join-Path $Root "reflex.lock"', self.build,
                      "the frontend lockfile should still pin dependencies")

    def test_assertions_are_defined_in_shared_library(self):
        for name in ("Assert-PackageContents", "Find-ForbiddenArtifacts",
                     "Assert-CleanPackage", "Get-RequiredPackageFiles"):
            self.assertIn(f"function {name}", self.lib)
            self.assertIn("windows_build_lib.ps1", self.build)

    def test_database_and_dev_directories_are_detected(self):
        self.assertIn('".db", ".sqlite", ".sqlite3"', self.lib)
        for name in ('.venv', 'node_modules', '.git', '.idea', '.vscode'):
            self.assertIn(f'"{name}"', self.lib)


class TestInstallerVersionConsistency(unittest.TestCase):
    def setUp(self):
        self.iss = ISS.read_text(encoding="utf-8-sig")

    def test_version_is_read_from_packaged_release(self):
        self.assertIn('FileOpen(OPSCORE_RELEASE)', self.iss)
        self.assertIn('FileRead(OPSCORE_RELEASE_HANDLE)', self.iss)
        self.assertIn('Trim(', self.iss)
        self.assertIn('AppVersion={#OPSCORE_VERSION}', self.iss)
        self.assertIn('OutputBaseFilename={#OPSCORE_OUTPUT_NAME}', self.iss)

    def test_explicit_version_must_match_release(self):
        self.assertIn('#if OPSCORE_VERSION != OPSCORE_PACKAGE_VERSION', self.iss)
        self.assertIn('does not match the packaged RELEASE file', self.iss)

    def test_missing_release_fails_compile(self):
        self.assertIn('RELEASE file is missing', self.iss)

    def test_candidate_package_produces_candidate_installer(self):
        self.assertIn('OPSCORE_PACKAGE_NAME', self.iss)
        self.assertIn('#if OPSCORE_PACKAGE_NAME == "Opscore"', self.iss)
        self.assertIn('"Opscore-Setup-" + OPSCORE_VERSION + "-candidate"', self.iss)

    def test_existing_installer_is_not_overwritten_by_default(self):
        self.assertIn('#if FileExists(OPSCORE_OUTPUT_PATH)', self.iss)
        self.assertIn('OPSCORE_ALLOW_OVERWRITE', self.iss)
        self.assertIn('The installer output already exists', self.iss)

    def test_version_mismatch_error_is_generic_to_candidate_paths(self):
        # The message must not hard-code the release folder, since candidate
        # builds read a different RELEASE file.
        self.assertNotIn('does not match dist\\Opscore\\RELEASE', self.iss)
        self.assertIn('does not match the packaged RELEASE file', self.iss)

    def test_output_paths_are_derived_from_the_package_folder(self):
        self.assertIn('#define OPSCORE_PACKAGE OPSCORE_DIST + "\\" + OPSCORE_PACKAGE_NAME', self.iss)
        self.assertIn('OutputBaseFilename={#OPSCORE_OUTPUT_NAME}', self.iss)

    def test_app_id_and_architecture_are_unchanged(self):
        self.assertIn("AppId={{7B9D53AA-62C3-4DF3-9F6B-45D89F717F24}", self.iss)
        self.assertIn("PrivilegesRequired=lowest", self.iss)
        self.assertIn("ArchitecturesAllowed=x64compatible", self.iss)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\Opscore", self.iss)

    def test_installer_packages_the_built_folder(self):
        self.assertIn('Source: "{#OPSCORE_PACKAGE}\\*"', self.iss)

    def test_database_choice_page_defaults_to_keep(self):
        self.assertIn('CreateInputOptionPage(', self.iss)
        self.assertIn('Use the existing database (recommended)', self.iss)
        self.assertIn('Create a fresh database (the existing one is kept as a numbered backup)', self.iss)
        self.assertIn('DatabasePage.SelectedValueIndex := 0;', self.iss)

    def test_database_choice_page_only_shows_with_existing_database(self):
        self.assertIn("FileExists(ExistingDatabasePath())", self.iss)
        self.assertIn("(PageID = DatabasePage.ID) and not ShouldShowDatabasePage()", self.iss)
        self.assertIn("{localappdata}\\Opscore\\store_ops.db", self.iss)

    def test_database_choice_is_recorded_for_first_launch(self):
        self.assertIn("{localappdata}\\Opscore\\db-choice.json", self.iss)
        self.assertIn('SaveStringToFile(', self.iss)
        self.assertIn('"{#OPSCORE_VERSION}"', self.iss)
        # The installer never renames or deletes the database itself; the
        # launcher consumes the marker before starting the backend.
        self.assertNotIn("DeleteFile(ExistingDatabasePath())", self.iss)
        self.assertNotIn("RenameFile(ExistingDatabasePath()", self.iss)


class TestReleaseWorkflowConsistency(unittest.TestCase):
    """The published-release CI job must opt into the release output folder."""

    def setUp(self):
        self.workflow = (ROOT / ".github" / "workflows" / "release-windows.yml").read_text(
            encoding="utf-8-sig")

    def test_ci_release_build_opts_into_release_output(self):
        self.assertIn("build_windows.ps1 -Version $version -AllowReleaseOutput", self.workflow)

    def test_ci_installer_uses_the_release_package(self):
        # No candidate package override in the published-release job.
        self.assertNotIn("OPSCORE_PACKAGE_NAME=Opscore-candidate", self.workflow)
        self.assertIn("/DOPSCORE_VERSION=$version", self.workflow)

    def test_ci_does_not_upload_a_candidate_installer(self):
        self.assertNotIn("-candidate.exe", self.workflow)


if __name__ == "__main__":
    unittest.main()
