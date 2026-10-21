"""Release-pipeline artifact tests: validate deploy/release tooling and the
versioned-release systemd unit by inspecting files and running safe commands.

These tests never install anything, never touch live infrastructure, and
never use a live database.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


class TestReleaseArtifactsExist(unittest.TestCase):
    def test_all_release_artifacts_present(self):
        for name in ("release.sh", "release_lib.sh", "simulate.sh",
                     "OPERATOR-RELEASE.md", "RELEASES.md", "MIGRATIONS.md",
                     "requirements.lock"):
            p = DEPLOY / name if name != "requirements.lock" else ROOT / name
            self.assertTrue(p.is_file(), f"missing {name}")

    def test_scripts_are_executable(self):
        for name in ("release.sh", "simulate.sh", "operator-install.sh",
                     "ducklab-backup.sh"):
            self.assertTrue((DEPLOY / name).stat().st_mode & 0o111,
                            f"{name} not executable")

    def test_shell_syntax_valid(self):
        for name in ("release.sh", "release_lib.sh", "simulate.sh",
                     "operator-install.sh", "ducklab-backup.sh"):
            r = subprocess.run(["bash", "-n", str(DEPLOY / name)],
                               capture_output=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr.decode()}")


class TestVenvDefectFix(unittest.TestCase):
    """The copied-venv defect must never recur."""

    def test_installer_never_copies_venv(self):
        text = (DEPLOY / "operator-install.sh").read_text()
        # No rsync of the development .venv into production.
        self.assertNotRegex(text, r'rsync[^\n]*"\$SRC/\.venv"')
        # Venv must be created in place.
        self.assertIn("-m venv \"$DEPLOY_DIR/.venv\"", text)

    def test_installer_verifies_shebangs_and_imports(self):
        text = (DEPLOY / "operator-install.sh").read_text()
        self.assertIn("#!/home/", text)          # foreign-path shebang guard
        self.assertIn("pip\" check", text.replace(".venv/bin/pip\" check",
                                                  "pip\" check"))
        for mod in ("reflex", "granian", "argon2", "starlette"):
            self.assertIn(mod, text)

    def test_release_lib_has_venv_integrity_check(self):
        text = (DEPLOY / "release_lib.sh").read_text()
        self.assertIn("release_verify_venv", text)
        self.assertIn("bad shebang", text)
        self.assertIn("release_create_venv", text)

    def test_venv_integrity_detects_foreign_shebang(self):
        """Functional test: a relocated venv must be rejected.

        Uses the caller's private temp space (no shared /tmp/opencode) and
        stubs the sanity check so the test exercises the SHEBANG rule under
        any identity (including the nologin ducklab service account).
        """
        with tempfile.TemporaryDirectory() as td:
            rel = Path(td) / "rel"
            (rel / ".venv" / "bin").mkdir(parents=True)
            fake = rel / ".venv" / "bin" / "python"
            fake.write_text(f"#!{rel}/.venv/bin/python\n")
            fake.chmod(0o755)
            script = rel / ".venv" / "bin" / "reflex"
            script.write_text("#!/home/duck/other/.venv/bin/python3\n")
            script.chmod(0o755)
            r = subprocess.run(
                ["bash", "-c",
                 f'. "{DEPLOY}/release_lib.sh"; release_verify_venv "{rel}"'],
                capture_output=True)
            self.assertNotEqual(r.returncode, 0,
                                "foreign shebang was not detected")
            self.assertIn(b"shebang", r.stderr)


class TestRequirementsLock(unittest.TestCase):
    def test_lock_covers_direct_requirements(self):
        lock_lines = [l for l in (ROOT / "requirements.lock").read_text()
                      .lower().splitlines() if l and not l.startswith("#")]
        lock_names = {l.split("==")[0] for l in lock_lines}
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = line.split("==")[0].lower()
            self.assertIn(name, lock_names,
                          f"{name} missing from requirements.lock")

    def test_lock_has_no_dev_packages(self):
        lock = (ROOT / "requirements.lock").read_text().lower()
        for dev in ("pytest", "playwright"):
            self.assertNotRegex(lock, rf"^{dev}==")

    def test_lock_pins_everything(self):
        for line in (ROOT / "requirements.lock").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            self.assertIn("==", line, f"unpinned: {line}")

    def test_argon2_present(self):
        self.assertIn("argon2-cffi==", (ROOT / "requirements.lock").read_text())


class TestVersionedSystemdUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (DEPLOY / "ducklab.service").read_text()

    def test_uses_current_symlink(self):
        self.assertIn("WorkingDirectory=/opt/ducklab-current", self.text)
        self.assertIn("ExecStart=/opt/ducklab-current/.venv/bin/reflex",
                      self.text)

    def test_hardening_preserved(self):
        for directive in ("NoNewPrivileges=true", "PrivateTmp=true",
                          "ProtectSystem=full", "ProtectHome=true",
                          "Restart=on-failure", "User=ducklab",
                          "EnvironmentFile=/etc/ducklab/ducklab.env"):
            self.assertIn(directive, self.text)
        # Writable paths cover the release tree, not the old layout.
        self.assertIn("ReadWritePaths=/var/lib/ducklab /opt/ducklab-releases",
                      self.text)
        self.assertNotIn("ReadWritePaths=/var/lib/ducklab /opt/ducklab\n",
                         self.text)

    def test_loopback_only(self):
        self.assertIn("--backend-host 127.0.0.1", self.text)

    def test_release_identity_env(self):
        self.assertIn("ducklab-release.env", self.text)


class TestHealthEndpoint(unittest.TestCase):
    def test_health_endpoint_registered(self):
        text = (ROOT / "ducklab" / "session_transport.py").read_text()
        self.assertIn("/api/health", text)
        self.assertIn("DUCKLAB_RELEASE_ID", text)

    def test_health_endpoint_serves(self):
        """Functional: the ASGI transformer exposes /api/health."""
        import os
        # Per-invocation private temp DB (works as any identity; no shared
        # fixed filename, auto-cleaned).
        with tempfile.TemporaryDirectory() as td:
            os.environ["DUCKLAB_DB_PATH"] = str(Path(td) / "test-health.db")
            os.environ["DUCKLAB_RELEASE_ID"] = "test-rel"
            from ducklab import session_transport
            from starlette.applications import Starlette
            from starlette.testclient import TestClient
            app = session_transport.asgi_transformer(Starlette())
            client = TestClient(app)
            r = client.get("/api/health")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "ok")
            self.assertEqual(r.json()["release"], "test-rel")


class TestDbInspect(unittest.TestCase):
    def test_inspect_and_compat_on_disposable_db(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "test-dbinspect.db")
            from ducklab.store_ops_db import init_db, SCHEMA_VERSION
            from ducklab import db_inspect
            init_db(path)
            info = db_inspect.inspect(path)
            self.assertTrue(info.integrity_ok)
            self.assertEqual(info.user_version, SCHEMA_VERSION)
            ok, msg = db_inspect.check_compatibility(path)
            self.assertTrue(ok, msg)
            # Newer schema than code must block.
            conn = sqlite3.connect(path)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            conn.commit(); conn.close()
            ok, msg = db_inspect.check_compatibility(path)
            self.assertFalse(ok)
            self.assertIn("NEWER", msg)

    def test_cli_prints_version(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "test-dbinspect-cli.db")
            from ducklab.store_ops_db import init_db, SCHEMA_VERSION
            init_db(path)
            r = subprocess.run(
                [sys.executable, "-m", "ducklab.db_inspect", path],
                capture_output=True, cwd=ROOT, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), str(SCHEMA_VERSION))


class TestReleaseScriptGuards(unittest.TestCase):
    def test_staging_refuses_port_3000(self):
        text = (DEPLOY / "release.sh").read_text()
        self.assertIn('refusing to bind staging to production port 3000',
                      text)

    def test_activate_requires_verified(self):
        text = (DEPLOY / "release.sh").read_text()
        self.assertIn('"status": "verified"', text)

    def test_rollback_never_touches_db(self):
        text = (DEPLOY / "release.sh").read_text()
        cmd = text.split("cmd_rollback()")[1].split("\n}\n")[0].lower()
        # No file-copy or DB-restore operations inside the rollback body.
        self.assertNotIn("cp ", cmd)
        self.assertNotIn(".backup", cmd)
        self.assertNotIn("gunzip", cmd)
        self.assertNotRegex(cmd, r"sqlite3\s+\S*\.db\s*<")
        self.assertIn("database was not restored", text.lower())

    def test_no_rsync_delete_on_persistent_paths(self):
        for name in ("release.sh", "release_lib.sh", "simulate.sh"):
            text = (DEPLOY / name).read_text()
            for line in text.splitlines():
                if "rsync" in line or "--delete" in line:
                    self.assertNotIn("/var/lib/ducklab", line,
                                     f"{name}: {line}")


if __name__ == "__main__":
    unittest.main()


class TestTrustedCliCopy(unittest.TestCase):
    def test_cli_copy_in_sync(self):
        """deploy/cli/ is the operator-installed trusted copy; it must match
        the working scripts exactly."""
        for name in ("release.sh", "release_lib.sh"):
            self.assertEqual((DEPLOY / name).read_bytes(),
                             (DEPLOY / "cli" / name).read_bytes(),
                             f"deploy/cli/{name} out of sync with deploy/{name}")
