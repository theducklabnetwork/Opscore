"""Tests for the health endpoint used by desktop startup and build checks."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestHealthEndpoint(unittest.TestCase):
    def test_health_endpoint_registered(self):
        text = (Path(__file__).resolve().parent.parent / "ducklab"
                / "session_transport.py").read_text()
        self.assertIn("/api/health", text)
        self.assertIn("DUCKLAB_RELEASE_ID", text)

    def test_health_endpoint_serves(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {
                    "DUCKLAB_DB_PATH": str(Path(td) / "test-health.db"),
                    "DUCKLAB_RELEASE_ID": "test-release"}):
                from ducklab import session_transport
                from starlette.applications import Starlette
                from starlette.testclient import TestClient

                app = session_transport.asgi_transformer(Starlette())
                client = TestClient(app)
                response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["release"], "test-release")


if __name__ == "__main__":
    unittest.main()
