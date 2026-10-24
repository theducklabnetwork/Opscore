#!/usr/bin/env python3
"""One-time import of the Daily Sales Contribution (DSC) workbook.

Thin CLI wrapper around ``ducklab.dsc_import`` — parsing, validation,
error messages and the all-or-nothing replacement live there and are
shared with the Settings > Reference Data page. See that module for the
workbook layout rules.

Usage:
    DUCKLAB_DB_PATH=/path/to/store_ops.db .venv/bin/python \\
        scripts/import_dsc.py <workbook.xlsx>

Environment:
    DUCKLAB_DB_PATH   The operations database (required; never defaulted).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ducklab import dsc_import  # noqa: E402

DscImportError = dsc_import.DscImportError  # re-export for callers/tests
import_workbook = dsc_import.import_workbook


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: import_dsc.py <workbook.xlsx>", file=sys.stderr)
        return 2
    db = os.environ.get("DUCKLAB_DB_PATH")
    if not db:
        print("DUCKLAB_DB_PATH is not set; refusing to guess a database.",
              file=sys.stderr)
        return 2
    try:
        result = import_workbook(Path(argv[1]), Path(db))
    except (DscImportError, OSError, KeyError,
            ElementTree.ParseError) as exc:
        print(f"Import failed; nothing changed.\n{exc}", file=sys.stderr)
        return 1
    print(f"Database: {db}\n"
          f"Imported {result['shares']} day shares "
          f"({result['pairs']} State/Concept pairs) and "
          f"{result['benchmarks']} SPR/IPR benchmarks "
          f"({result['benchmark_months']} Concept/State months).")
    for w in result["warnings"]:
        print(f"Warning: {w} (stores cannot match it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
