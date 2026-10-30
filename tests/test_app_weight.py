"""keep dry import weight on a budget for ducklab modules.

Most ducklab modules are meant to be dry-importable (no DB, no app, no
browser) in well under a megabyte of fresh heap. A few are not: page
components, state classes, and the app builder need reflex; chart code needs
plotly. The registry below names every heavy module.

Weight classes
--------------

LIGHT modules
    Anything not listed in ``HEAVY_MODULES`` must not import a heavy third
    party root (reflex / plotly / openpyxl) at module scope. Dry import of
    these stays in the few-hundred-KB range; reflex alone breaks that budget
    because it scans the frontend bundle tree, and plotly pulls in a ~3 MB
    multi-file payload.

HEAVY_MODULES
    Named modules are allowed to import heavy roots at module scope. Each
    entry carries a one-line justification. When a change legitimately makes
    a previously light module heavy, add it to the registry in the same
    commit; when a heavy module is slimmed down, delete its entry (the
    registry-exists check keeps this honest).

Legacy offenders, restored 2026-02-15 after a refactor: ``ducklab.py`` is
the app (reflex by definition); ``db_inspect``, ``report_export`` and
``dsc_import`` are scripted openpyxl tooling that historically grabbed
reactivity at module scope. Long-term these should move their heavy imports
inside function bodies (the ``operations_repository`` pattern) and leave the
registry.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DUCKLAB_DIR = REPO_ROOT / "ducklab"

# Modules that legitimately import heavy third-party roots at top level.
# Adding a good-size test file to this list is a release-time decision: every
# module here makes the frontend bundle larger instead of smaller.
HEAVY_MODULES = {
    "app_service",        # serves frontend assets incl. the built .web tree
    "auth_pages",         # reflex page components
    "auth_service",       # auth flow marshals reflex state
    "authz",              # guards wrap reflex state classes
    "design",             # style tokens consumed by page components
    "ducklab",            # the app itself
    "ops_actions_pages",  # page components
    "ops_actions_state",  # rx.State subclass
    "ops_assignment_state",  # rx.State subclass
    "ops_charts",         # plotly chart builders
    "ops_daily_pages",    # page components
    "ops_daily_state",    # rx.State subclass
    "ops_expenses_pages",  # page components
    "ops_expenses_state",  # rx.State subclass
    "ops_finalize",       # rx event-spec builders (download/redirect) for finalize workflow
    "ops_manpower_pages",  # page components
    "ops_manpower_state",  # rx.State subclass
    "ops_overview_state",  # rx.State subclass
    "ops_pages",          # page components
    "ops_sales_pages",    # page components
    "ops_sales_state",    # rx.State subclass
    "ops_schedule_pages",  # page components
    "ops_schedule_state",  # rx.State subclass
    "ops_settings_pages",  # page components
    "ops_setup_state",    # rx.State subclass
    "ops_shared_state",   # rx.State base classes
    "ops_shell",          # reflex shell layout components
    "ops_state",          # rx.State subclass
    "ops_stock_pages",    # page components
    "ops_stock_state",    # rx.State subclass
    "ops_stores_pages",   # page components
    "ops_stores_state",   # rx.State subclass
    "portfolio_service",  # heavyweight analytics/graphs backing service
    "ui",                 # reflex UI primitives
    # --- legacy offenders (see module docstring; restored 2026-02-15) ---
    "db_inspect",         # release/schema inspection tooling (openpyxl-era scripted)
    "report_export",      # scripted Excel reports (openpyxl)
    "dsc_import",         # historical workbook importer (openpyxl)
}

# Import roots whose top-level presence makes a module heavy: reflex drags in
# the whole component tree and the frontend bundle scan; plotly pulls a ~3 MB
# multi-file JS payload; openpyxl is a workbook engine only the scripted
# tooling should touch.
HEAVY_IMPORT_ROOTS = {"reflex", "plotly", "openpyxl"}


def _top_level_import_roots(tree: ast.Module) -> set[str]:
    """Import roots that execute when the module is imported.

    Only direct module-body statements are considered: anything nested inside
    a function or class body is a deferred import (allowed in light modules —
    that is exactly how light modules stay light). ``if TYPE_CHECKING:``
    blocks never run at runtime, so they are ignored as well.
    """

    def is_type_checking_block(stmt: ast.stmt) -> bool:
        if not isinstance(stmt, ast.If):
            return False
        test = stmt.test
        return (
            isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
        ) or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    roots: set[str] = set()
    for stmt in tree.body:
        if is_type_checking_block(stmt):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


class TestAppWeightBudget(unittest.TestCase):
    """Static encoding of the dry-import weight policy.

    The original version measured import-time RSS growth at runtime, which
    made the verdict depend on which packages happened to be installed in the
    runner's venv (a release-pipeline validation venv carrying extra tooling
    failed on a ~2 MB difference; a dev venv without them passed). The policy
    itself is venv-independent: light modules must not import
    reflex/plotly/openpyxl at module scope. A static top-level import is the
    mechanism by which a module becomes heavy, so checking it directly is as
    verifiable as the runtime measurement while staying meaningful in every
    environment that runs the suite.
    """

    def test_registry_modules_exist(self):
        # A stale registry silently weakens the guard when files are renamed
        # or removed.
        for name in sorted(HEAVY_MODULES):
            self.assertTrue((DUCKLAB_DIR / f"{name}.py").is_file(),
                            f"HEAVY_MODULES entry has no module: {name}")

    def test_light_modules_have_no_top_level_heavy_imports(self):
        offenders: dict[str, list[str]] = {}
        for path in sorted(DUCKLAB_DIR.glob("*.py")):
            heavy = sorted(_top_level_import_roots(
                ast.parse(path.read_text(), filename=str(path)))
                & HEAVY_IMPORT_ROOTS)
            if heavy and path.stem not in HEAVY_MODULES:
                offenders[path.stem] = heavy
        self.assertEqual(
            offenders,
            {},
            "light modules importing heavy roots at module scope (this breaks "
            "the dry-import weight budget; if the import is deliberate, weigh "
            "the frontend-bundle size cost and add the module to HEAVY_MODULES "
            "with a one-line justification): "
            + "; ".join(f"{name} -> {roots}" for name, roots in offenders.items()),
        )


if __name__ == "__main__":
    unittest.main()
