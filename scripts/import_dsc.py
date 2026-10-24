#!/usr/bin/env python3
"""One-time import of the Daily Sales Contribution (DSC) workbook.

Reads sheet "DSC" (header row 4: State, Concept, Day, then the month names
November..October; data from row 5; Day is Mon..Sun or PH; each value is
one day's share of that calendar month) and sheet "SPR" (header row 3:
Concept, State, 12-month average SPR, a blank column, 12-month average
IPR; data from row 4). State / Concept cells left blank under a merged
group take the value above them; rows without a Day (headings, totals)
are skipped.

Both tables are replaced in one transaction: either the whole workbook is
imported or nothing changes. Stdlib only (zipfile + XML), so the
production venv can run it.

Usage:
    DUCKLAB_DB_PATH=/path/to/store_ops.db .venv/bin/python \\
        scripts/import_dsc.py <workbook.xlsx>

Environment:
    DUCKLAB_DB_PATH   The operations database (required; never defaulted).
"""

from __future__ import annotations

import os
import posixpath
import re
import sqlite3
import sys
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ducklab import kpi, store_ops_db  # noqa: E402
from ducklab.operations_write_repository import CONCEPTS, STATES  # noqa: E402

DSC_SHEET, DSC_HEADER_ROW = "DSC", 4
SPR_SHEET, SPR_HEADER_ROW = "SPR", 3
# November..October -> calendar month number
MONTHS = {name: n for n, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"), start=1)}

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships"}
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class DscImportError(Exception):
    """The workbook cannot be imported (nothing is written)."""


# --------------------------------------------------------------------------
# Minimal .xlsx reader: cell values by (row, column), 1-based
# --------------------------------------------------------------------------
def _col_number(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - ord("A") + 1
    return n


def _text(el) -> str:
    """All <t> text under a string item (plain or rich text runs)."""
    return "".join(t.text or "" for t in el.iter(f"{{{_NS['m']}}}t"))


def read_sheet(path: Path, name: str) -> dict[tuple[int, int], object]:
    """{(row, col): value} for one sheet. Numbers -> float, text -> str."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DscImportError(f"Not a readable .xlsx file: {exc}") from exc
    with zf:
        book = ElementTree.fromstring(zf.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target")
                   for r in rels.iter(f"{{{_REL_NS}}}Relationship")}
        sheet = next((s for s in book.iter(f"{{{_NS['m']}}}sheet")
                      if (s.get("name") or "").strip() == name), None)
        if sheet is None:
            raise DscImportError(f"Sheet {name!r} not found")
        target = targets[sheet.get(f"{{{_NS['r']}}}id")]
        target = (target.lstrip("/") if target.startswith("/")
                  else posixpath.normpath(posixpath.join("xl", target)))
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            sst = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in sst.findall("m:si", _NS)]
        root = ElementTree.fromstring(zf.read(target))
    cells: dict[tuple[int, int], object] = {}
    for r_i, row in enumerate(root.iter(f"{{{_NS['m']}}}row"), start=1):
        r = int(row.get("r") or r_i)
        for c_i, c in enumerate(row.findall("m:c", _NS), start=1):
            ref = c.get("r")
            col = (_col_number(re.match(r"[A-Z]+", ref).group())
                   if ref else c_i)
            kind = c.get("t", "n")
            v = c.find("m:v", _NS)
            if kind == "inlineStr":
                is_ = c.find("m:is", _NS)
                value = _text(is_) if is_ is not None else None
            elif v is None or v.text is None:
                value = None
            elif kind == "s":
                value = shared[int(v.text)]
            elif kind in ("str", "e"):
                value = v.text
            elif kind == "b":
                value = v.text == "1"
            else:
                value = float(v.text)
            if value is not None and value != "":
                cells[(r, col)] = value
    return cells


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _label(value) -> str:
    return str(value).strip() if value is not None else ""


def _day_key(value) -> str:
    """'Mon'..'Sun' / 'PH' (case-insensitive; full weekday names too)."""
    text = _label(value)
    if text.upper() == kpi.DSC_PH:
        return kpi.DSC_PH
    for key in kpi.DSC_WEEKDAYS:
        full = {"Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
                "Sat": "Saturday"}.get(key, key + "day")
        if len(text) >= 3 and full.lower().startswith(text.lower()):
            return key
    raise ValueError(text)


def _number(value) -> Decimal:
    """Cell value -> Decimal ('3%' text -> 0.03)."""
    if isinstance(value, float):
        return Decimal(repr(value))
    text = _label(value).replace(",", "")
    scale = Decimal(1)
    if text.endswith("%"):
        text, scale = text[:-1].strip(), Decimal("0.01")
    try:
        d = Decimal(text) * scale
    except InvalidOperation:
        raise ValueError(text)
    if not d.is_finite():
        raise ValueError(text)
    return d


def _header(cells, row: int) -> dict[str, int]:
    """Lower-case header text -> column."""
    return {_label(v).lower(): c for (r, c), v in cells.items()
            if r == row and _label(v)}


def _max_row(cells) -> int:
    return max((r for r, _ in cells), default=0)


def parse_dsc(cells) -> list[tuple[str, str, str, int, float]]:
    """(state, concept, day, month, share) rows from sheet DSC."""
    head = _header(cells, DSC_HEADER_ROW)
    missing = [h for h in ("state", "concept", "day") if h not in head]
    month_cols = {MONTHS[name]: head[name.lower()] for name in MONTHS
                  if name.lower() in head}
    missing += [n for n in MONTHS if MONTHS[n] not in month_cols]
    if missing:
        raise DscImportError(f"DSC row {DSC_HEADER_ROW}: missing header(s) "
                           + ", ".join(missing))
    out, errors, seen = [], [], set()
    state = concept = ""
    for r in range(DSC_HEADER_ROW + 1, _max_row(cells) + 1):
        row = {c: v for (rr, c), v in cells.items() if rr == r}
        if not row:
            continue
        # merged State / Concept groups: blank cells repeat the one above
        state = _label(row.get(head["state"])) or state
        concept = _label(row.get(head["concept"])) or concept
        if not _label(row.get(head["day"])):
            continue  # group heading / total row: no Day, no share
        try:
            day = _day_key(row.get(head["day"]))
        except ValueError as exc:
            errors.append(f"DSC row {r}: Day {str(exc)!r} is not Mon–Sun "
                          "or PH")
            continue
        if not state or not concept:
            errors.append(f"DSC row {r}: State and Concept are required")
            continue
        if (state, concept, day) in seen:
            errors.append(f"DSC row {r}: {state} / {concept} / {day} "
                          "appears twice")
            continue
        seen.add((state, concept, day))
        for month, col in month_cols.items():
            if row.get(col) is None:
                continue  # no share for that month (e.g. no PH share)
            try:
                share = _number(row[col])
            except ValueError as exc:
                errors.append(f"DSC row {r}: {str(exc)!r} is not a number")
                continue
            if share < 0:
                errors.append(f"DSC row {r}: share cannot be negative")
                continue
            out.append((state, concept, day, month, float(share)))
    if errors:
        raise DscImportError("\n".join(errors))
    if not out:
        raise DscImportError("DSC: no data rows found")
    return out


def parse_spr(cells) -> list[tuple[str, str, float | None, float | None]]:
    """(concept, state, avg SPR, avg IPR) rows from sheet SPR."""
    head = _header(cells, SPR_HEADER_ROW)
    cols = {"concept": head.get("concept"), "state": head.get("state"),
            "spr": next((c for h, c in head.items() if "spr" in h), None),
            "ipr": next((c for h, c in head.items() if "ipr" in h), None)}
    missing = [k for k, c in cols.items() if c is None]
    if missing:
        raise DscImportError(f"SPR row {SPR_HEADER_ROW}: missing header(s) "
                           + ", ".join(missing))
    out, errors, seen = [], [], set()
    state = concept = ""
    for r in range(SPR_HEADER_ROW + 1, _max_row(cells) + 1):
        row = {c: v for (rr, c), v in cells.items() if rr == r}
        if not row:
            continue
        concept = _label(row.get(cols["concept"])) or concept
        state = _label(row.get(cols["state"])) or state
        if not state or not concept:
            errors.append(f"SPR row {r}: Concept and State are required")
            continue
        if (concept, state) in seen:
            errors.append(f"SPR row {r}: {concept} / {state} appears twice")
            continue
        seen.add((concept, state))
        values = []
        for key in ("spr", "ipr"):
            v = row.get(cols[key])
            try:
                values.append(None if v is None else float(_number(v)))
            except ValueError as exc:
                errors.append(f"SPR row {r}: {str(exc)!r} is not a number")
                values.append(None)
        out.append((concept, state, *values))
    if errors:
        raise DscImportError("\n".join(errors))
    return out


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
def import_workbook(xlsx: Path, db_path: Path) -> dict:
    """Parse everything first, then replace both tables in one
    transaction. Returns counts and warnings."""
    if not db_path.is_file():
        raise DscImportError(f"Database not found: {db_path}")
    shares = parse_dsc(read_sheet(xlsx, DSC_SHEET))
    benchmarks = parse_spr(read_sheet(xlsx, SPR_SHEET))
    conn = store_ops_db.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 3:
            raise DscImportError(
                f"Database schema is v{version}; start this release once "
                "so it migrates to v3, then import")
        with conn:
            conn.execute("DELETE FROM contribution_shares")
            conn.execute("DELETE FROM basket_benchmarks")
            conn.executemany(
                "INSERT INTO contribution_shares (state, concept, day, "
                "month, share) VALUES (?,?,?,?,?)", shares)
            conn.executemany(
                "INSERT INTO basket_benchmarks (concept, state, avg_spr, "
                "avg_ipr) VALUES (?,?,?,?)", benchmarks)
    finally:
        conn.close()
    pairs = {(s, c) for s, c, *_ in shares} | {(s, c)
                                              for c, s, *_ in benchmarks}
    warnings = sorted(
        {f"State {s!r} is not a store State option" for s, _ in pairs
         if s not in STATES}
        | {f"Concept {c!r} is not a store Concept option" for _, c in pairs
           if c not in CONCEPTS})
    return {"shares": len(shares), "benchmarks": len(benchmarks),
            "pairs": len({(s, c) for s, c, *_ in shares}),
            "warnings": warnings}


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
          f"{result['benchmarks']} SPR/IPR benchmarks.")
    for w in result["warnings"]:
        print(f"Warning: {w} (stores cannot match it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
