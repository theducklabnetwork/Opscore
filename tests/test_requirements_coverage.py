"""Packaging guard: every third-party top-level import under ducklab/ must be
declared as a direct dependency in requirements.txt.

Stdlib modules and local ducklab modules are ignored. Import names that
differ from their PyPI distribution names are mapped via an explicit table
(for example ``argon2`` -> ``argon2-cffi``).
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "ducklab"
REQ = ROOT / "requirements.txt"

# Importable top-level name -> distribution name, for packages whose import
# root differs from the name used in requirements files.
IMPORT_TO_PACKAGE = {
    "argon2": "argon2-cffi",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "multipart": "python-multipart",
    "websocket": "websocket-client",
    "OpenSSL": "pyOpenSSL",
    "Crypto": "pycryptodome",
    "serial": "pyserial",
}

# Repo-local import roots that must never be treated as third-party.
LOCAL_ROOTS = {"ducklab", "rxconfig"}

_STDLIB = set(getattr(sys, "stdlib_module_names", ()))


def _norm(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_packages() -> set[str]:
    declared = set()
    for raw in REQ.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        if name:
            declared.add(_norm(name))
    return declared


def imported_roots() -> dict[str, list[str]]:
    """Map each imported top-level name to the files importing it."""
    users: dict[str, list[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # skip relative imports (from . / from .mod import ...)
                mods = [node.module] if (node.level == 0 and node.module) else []
            else:
                continue
            for mod in mods:
                root = mod.split(".")[0]
                ref = f"{path.relative_to(ROOT)}:{node.lineno}"
                users.setdefault(root, []).append(ref)
    return users


class TestThirdPartyImportsDeclared(unittest.TestCase):
    def test_all_third_party_imports_declared(self):
        declared = declared_packages()
        missing: dict[str, list[str]] = {}
        for root, users in imported_roots().items():
            if root in _STDLIB or root in LOCAL_ROOTS:
                continue
            package = _norm(IMPORT_TO_PACKAGE.get(root, root))
            if package not in declared:
                missing[root] = sorted(set(users))
        details = "; ".join(f"{name} imported by {refs[:3]}"
                            for name, refs in sorted(missing.items()))
        self.assertEqual(missing, {},
                         "third-party imports missing from requirements.txt: "
                         + details)


class TestRequirementsLock(unittest.TestCase):
    def setUp(self):
        self.lock_lines = [
            line.strip() for line in (ROOT / "requirements.lock")
            .read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def test_lock_covers_direct_requirements(self):
        lock_names = {
            re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].lower()
            for line in self.lock_lines
        }
        for line in REQ.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].lower()
            self.assertIn(name, lock_names,
                          f"{name} missing from requirements.lock")

    def test_lock_has_no_dev_packages(self):
        lock_names = {
            re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].lower()
            for line in self.lock_lines
        }
        self.assertNotIn("pytest", lock_names)
        self.assertNotIn("playwright", lock_names)

    def test_lock_pins_everything(self):
        for line in self.lock_lines:
            self.assertIn("==", line, f"unpinned: {line}")

    def test_argon2_present(self):
        self.assertTrue(any(line.startswith("argon2-cffi==")
                            for line in self.lock_lines))


if __name__ == "__main__":
    unittest.main()
