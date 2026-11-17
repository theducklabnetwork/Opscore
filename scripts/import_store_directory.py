#!/usr/bin/env python3
"""Import the monthly-sales workbook into the store directory.

Thin CLI wrapper around ``ducklab.store_directory_import`` — parsing,
validation and the all-or-nothing replacement live there.

The directory is reference data only: NO stores are created and no daily
records are written. New Store uses it to pre-fill Code/Name/State/Zone,
and Monthly Setup can copy Last Month / Last Year from it.

Usage:
    DUCKLAB_DB_PATH=/path/to/store_ops.db .venv/bin/python \\
        scripts/import_store_directory.py "MONTHLY SALES (2025-2026).xlsx"

Environment:
    DUCKLAB_DB_PATH   The operations database (required; never defaulted).

The database must already be at schema v16 (start this release once).
Close the app before importing into its database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ducklab import store_directory_import  # noqa: E402

DirectoryImportError = store_directory_import.DirectoryImportError
import_workbook = store_directory_import.import_workbook


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: import_store_directory.py <workbook.xlsx>", file=sys.stderr)
        return 2
    db = os.environ.get("DUCKLAB_DB_PATH")
    if not db:
        print("DUCKLAB_DB_PATH is not set; refusing to guess a database.",
              file=sys.stderr)
        return 2
    try:
        result = import_workbook(Path(argv[1]), Path(db))
    except (DirectoryImportError, OSError, KeyError,
            ElementTree.ParseError) as exc:
        print(f"Import failed; nothing changed.\n{exc}", file=sys.stderr)
        return 1
    print(f"Database: {db}\n"
          f"Directory: {result['stores']} stores, {result['history']} monthly "
          f"figures ({result['first_month']}..{result['last_month']}, "
          f"{result['months']} months).\n"
          "No stores or daily records were created.")
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
