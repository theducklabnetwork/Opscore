"""Deployment artifact tests: validate the prepared production configuration.

These tests inspect the *files* in deploy/ and the security/env semantics —
they never install anything or touch live infrastructure. No live DB is used.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


class TestDeployArtifactsExist(unittest.TestCase):
    def test_all_artifacts_present(self):
        for name in ("ducklab.service", "ducklab.env.template",
                     "Caddyfile.ducklab", "Caddyfile.gated",
                     "operator-install.sh",
                     "ducklab-backup.sh", "ducklab-backup.service",
                     "ducklab-backup.timer", "DEPLOYMENT.md", "OPERATOR.md",
                     "ROLLBACK.md", "RECOVERY.md", "VERIFICATION.md"):
            self.assertTrue((DEPLOY / name).is_file(), f"missing {name}")


class TestSystemdService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.unit = (DEPLOY / "ducklab.service").read_text()

    def test_non_root_user(self):
        self.assertIn("User=ducklab", self.unit)
        self.assertNotIn("User=root", self.unit)

    def test_uses_opt_deployment_copy(self):
        # service runs the controlled versioned release via the atomic
        # /opt/ducklab-current symlink, never the source tree
        self.assertIn("WorkingDirectory=/opt/ducklab-current", self.unit)
        self.assertIn("/opt/ducklab-current/.venv/bin/reflex", self.unit)
        self.assertNotIn("/home/duck/project/ducklab/.venv", self.unit)

    def test_production_single_port_loopback(self):
        self.assertIn("--env prod", self.unit)
        self.assertIn("--single-port", self.unit)
        self.assertIn("--backend-host 127.0.0.1", self.unit)

    def test_restart_and_autostart(self):
        self.assertIn("Restart=on-failure", self.unit)
        self.assertIn("WantedBy=multi-user.target", self.unit)

    def test_env_file_and_no_hardcoded_secrets(self):
        self.assertIn("EnvironmentFile=/etc/ducklab/ducklab.env", self.unit)
        # no inline secrets
        self.assertNotRegex(self.unit, r"(?i)(password|secret|token)=\S+")

    def test_graceful_stop_and_startup_timeout(self):
        self.assertIn("TimeoutStopSec=30", self.unit)
        self.assertIn("TimeoutStartSec=300", self.unit)
        self.assertIn("KillSignal=SIGTERM", self.unit)

    def test_persistent_db_writable(self):
        self.assertIn("/var/lib/ducklab", self.unit)

    def test_protects_home(self):
        # deployment copy lives in /opt, so /home need not be exposed
        self.assertIn("ProtectHome=true", self.unit)


class TestBackupService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.unit = (DEPLOY / "ducklab-backup.service").read_text()

    def test_runs_as_ducklab_not_root(self):
        self.assertIn("User=ducklab", self.unit)
        self.assertNotIn("User=root", self.unit)

    def test_invokes_backup_script(self):
        self.assertIn("/usr/local/bin/ducklab-backup", self.unit)


class TestOperatorInstaller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sh = (DEPLOY / "operator-install.sh").read_text()

    def test_gate_activated_before_app_start(self):
        # Caddy reload (gate activation) must come before any instruction to
        # start the ducklab service.
        reload_idx = self.sh.find("systemctl reload caddy")
        start_idx = self.sh.find("systemctl enable --now ducklab")
        self.assertNotEqual(reload_idx, -1)
        self.assertNotEqual(start_idx, -1)
        self.assertLess(reload_idx, start_idx,
                        "gate must be reloaded before app start")

    def test_deploys_opt_copy_preserves_source(self):
        self.assertIn("/opt/ducklab", self.sh)
        # never recursively chmod/chown the /home/duck source tree
        self.assertNotIn("chmod -R g+rX /home/duck", self.sh)
        self.assertNotIn("usermod -aG duck", self.sh)

    def test_preserves_existing_env(self):
        # only installs the template when ducklab.env is absent
        self.assertIn('if [[ -f "$ETC_DIR/ducklab.env" ]]', self.sh)
        self.assertIn("preserving it", self.sh)

    def test_gate_password_not_on_argv(self):
        self.assertIn('read -rsp "    Gate password: "', self.sh)
        self.assertIn("caddy hash-password --plaintext", self.sh)
        self.assertIn("unset GATE_PW", self.sh)

    def test_operator_ip_prompted_and_validated(self):
        self.assertIn("SSH_CONNECTION", self.sh)
        self.assertIn("Operator public IP to allowlist", self.sh)

    def test_preflight_checks(self):
        self.assertIn("ss -tln", self.sh)               # port free
        self.assertIn("is-active --quiet ducklab", self.sh)  # existing svc
        self.assertIn("already exists — it will be preserved", self.sh)  # DB

    def test_atomic_caddy_install_and_backup(self):
        self.assertIn("mktemp", self.sh)
        self.assertIn("mv -f", self.sh)                 # atomic rename
        self.assertIn('.bak.$STAMP', self.sh)           # backup preserved
        # mktemp output isn't named "Caddyfile", so validation must pass the
        # adapter explicitly or Caddy defaults to JSON and fails.
        self.assertIn("caddy validate --adapter caddyfile", self.sh)

    def test_backup_dir_owned_by_ducklab(self):
        self.assertIn('install -d -o "$SVC_USER" -g "$SVC_USER" -m 700 '
                      '"$BACKUP_DIR"', self.sh)


class TestEnvTemplate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = (DEPLOY / "ducklab.env.template").read_text()

    def test_secure_cookies_and_proxy(self):
        self.assertIn("DUCKLAB_SECURE_COOKIES=1", self.env)
        self.assertIn("DUCKLAB_TRUST_PROXY=1", self.env)

    def test_persistent_db_path(self):
        self.assertIn("DUCKLAB_DB_PATH=/var/lib/ducklab/store_ops.db", self.env)

    def test_dev_escape_hatches_off(self):
        # FRONTEND_ORIGINS and EXPOSE_PROTOTYPE must be commented/unset
        for line in self.env.splitlines():
            if "DUCKLAB_FRONTEND_ORIGINS" in line or "DUCKLAB_EXPOSE_PROTOTYPE" in line:
                self.assertTrue(line.strip().startswith("#"),
                                f"escape hatch must be commented: {line}")

    def test_no_secrets(self):
        self.assertNotRegex(self.env, r"(?i)(password|secret|apikey|api_key)=\S+")


class TestSecurityEnvSemantics(unittest.TestCase):
    """Confirm the env vars the template sets actually change behavior."""

    def test_secure_cookies_flag(self):
        os.environ["DUCKLAB_SECURE_COOKIES"] = "1"
        try:
            from ducklab import security
            cfg = security.SecurityConfig.from_env()
            self.assertTrue(cfg.secure_cookies)
        finally:
            os.environ.pop("DUCKLAB_SECURE_COOKIES", None)

    def test_trust_proxy_flag(self):
        os.environ["DUCKLAB_TRUST_PROXY"] = "1"
        try:
            from ducklab import security
            self.assertTrue(security.SecurityConfig.from_env().trust_proxy)
        finally:
            os.environ.pop("DUCKLAB_TRUST_PROXY", None)

    def test_frontend_origins_empty_by_default(self):
        os.environ.pop("DUCKLAB_FRONTEND_ORIGINS", None)
        from ducklab import security
        self.assertEqual(security.SecurityConfig.from_env()
                         .allowed_frontend_origins, ())


class TestCaddyProposal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.caddy = (DEPLOY / "Caddyfile.ducklab").read_text()

    def test_domain_and_upstream(self):
        self.assertIn("app.theducklab.com", self.caddy)
        self.assertIn("reverse_proxy 127.0.0.1:3000", self.caddy)

    def test_no_path_splitting_needed(self):
        # single-port: /api/* and /_event ride the same upstream
        self.assertNotIn("@api", self.caddy)


class TestGatedCaddy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.caddy = (DEPLOY / "Caddyfile.gated").read_text()

    def test_gate_present(self):
        self.assertIn("@blocked not remote_ip", self.caddy)
        self.assertIn("basic_auth @blocked", self.caddy)

    def test_single_port_upstream(self):
        self.assertIn("reverse_proxy 127.0.0.1:3000", self.caddy)

    def test_placeholders_fail_closed(self):
        # placeholders must be present (installer substitutes them) and would
        # be invalid if installed unsubstituted
        self.assertIn("__OPERATOR_IP_CIDR__", self.caddy)
        self.assertIn("__OPERATOR_BCRYPT_HASH__", self.caddy)

    def test_documents_401_not_403(self):
        self.assertIn("401", self.caddy)


class TestBackupScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sh = (DEPLOY / "ducklab-backup.sh").read_text()

    def test_uses_sqlite_backup_api(self):
        # consistent online backup, not naive file copy
        self.assertIn(".backup(", self.sh)
        self.assertNotRegex(self.sh, r"\bcp\b.*\.db")

    def test_restricted_permissions(self):
        self.assertIn("chmod 600", self.sh)
        self.assertIn("chmod 700", self.sh)

    def test_retention(self):
        self.assertIn("DUCKLAB_BACKUP_KEEP", self.sh)

    def test_executable(self):
        self.assertTrue(os.access(DEPLOY / "ducklab-backup.sh", os.X_OK),
                        "backup script must be executable")


if __name__ == "__main__":
    unittest.main()
