"""Import of the Daily Sales Contribution (DSC) workbook.

Shared between ``scripts/import_dsc.py`` (CLI) and the Settings >
Reference Data page; both call :func:`import_workbook` so validation,
error messages and the all-or-nothing replacement are identical.

Columns are read by position, not header text:

  DSC  col A State (header may be blank), B Concept, C Day (Mon..Sun or
       PH), D..O = November..October (month names in row 4); data from
       row 5. Each value is one day's share of that calendar month.
  SPR  row 2: "Average SPR" above col C, "Average IPR" above col P;
       row 3: Concept, State, month names; C..N = average SPR
       November..October, O blank, P..AA = average IPR
       November..October; data from row 4.

State / Concept cells left blank under a merged group take the value
above them; DSC rows without a Day (headings, totals) are skipped.

Both tables are replaced in one transaction: either the whole workbook is
imported or nothing changes. Stdlib only (zipfile + XML), so the
production venv can run it.
"""

from __future__ import annotations

import posixpath
import re
import sqlite3
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

from ducklab import kpi, store_ops_db
from ducklab.operations_write_repository import CONCEPTS, STATES

# Columns are fixed by position (1-based; A = 1). Header text is not used
# to find them: the State header may be blank and the SPR Concept header
# misspelled. Only the month names are checked, to catch a shifted layout.
DSC_SHEET, DSC_HEADER_ROW = "DSC", 4
DSC_STATE, DSC_CONCEPT, DSC_DAY, DSC_FIRST_MONTH = 1, 2, 3, 4    # A B C D..O
SPR_SHEET, SPR_GROUP_ROW, SPR_HEADER_ROW = "SPR", 2, 3
SPR_CONCEPT, SPR_STATE = 1, 2                                    # A B
SPR_FIRST_SPR, SPR_FIRST_IPR = 3, 16                    # C..N, (O), P..AA
MONTHS = {name: n for n, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"), start=1)}
FISCAL_MONTHS = (11, 12, *range(1, 11))                # November..October

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


def _max_row(cells) -> int:
    return max((r for r, _ in cells), default=0)


def _col_letters(col: int) -> str:
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _month_number(value) -> int | None:
    """'September' / 'Sep' / 'sept' -> 9; anything else -> None."""
    text = _label(value).lower()
    if len(text) < 3:
        return None
    return next((n for name, n in MONTHS.items()
                 if name.lower().startswith(text)), None)


def _check_months(cells, sheet: str, row: int, first_col: int) -> dict:
    """{calendar month: column} for November..October in row `row` from
    `first_col` on. The columns are fixed; the names only confirm them."""
    cols, errors = {}, []
    for i, month in enumerate(FISCAL_MONTHS):
        col = first_col + i
        found = cells.get((row, col))
        if _month_number(found) != month:
            expected = next(n for n, m in MONTHS.items() if m == month)
            errors.append(f"{_col_letters(col)}{row} is "
                          f"{_label(found)!r}, expected {expected}")
        cols[month] = col
    if errors:
        raise DscImportError(f"{sheet} row {row}: month headers must be "
                             "November..October: " + "; ".join(errors))
    return cols


def parse_dsc(cells) -> list[tuple[str, str, str, int, float]]:
    """(state, concept, day, month, share) rows from sheet DSC."""
    month_cols = _check_months(cells, "DSC", DSC_HEADER_ROW, DSC_FIRST_MONTH)
    out, errors, seen = [], [], set()
    state = concept = ""
    for r in range(DSC_HEADER_ROW + 1, _max_row(cells) + 1):
        row = {c: v for (rr, c), v in cells.items() if rr == r}
        if not row:
            continue
        # merged State / Concept groups: blank cells repeat the one above
        state = _label(row.get(DSC_STATE)) or state
        concept = _label(row.get(DSC_CONCEPT)) or concept
        if not _label(row.get(DSC_DAY)):
            continue  # group heading / total row: no Day, no share
        try:
            day = _day_key(row.get(DSC_DAY))
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


def parse_spr(cells) -> list[tuple[str, str, int, float | None,
                                   float | None]]:
    """(concept, state, month, avg SPR, avg IPR) rows from sheet SPR."""
    errors = []
    for col, key in ((SPR_FIRST_SPR, "spr"), (SPR_FIRST_IPR, "ipr")):
        found = _label(cells.get((SPR_GROUP_ROW, col)))
        if key not in found.lower():
            errors.append(f"{_col_letters(col)}{SPR_GROUP_ROW} is "
                          f"{found!r}, expected Average {key.upper()}")
    if errors:
        raise DscImportError(f"SPR row {SPR_GROUP_ROW}: "
                             + "; ".join(errors))
    spr_cols = _check_months(cells, "SPR", SPR_HEADER_ROW, SPR_FIRST_SPR)
    ipr_cols = _check_months(cells, "SPR", SPR_HEADER_ROW, SPR_FIRST_IPR)
    out, seen = [], set()
    state = concept = ""
    for r in range(SPR_HEADER_ROW + 1, _max_row(cells) + 1):
        row = {c: v for (rr, c), v in cells.items() if rr == r}
        if not row:
            continue
        concept = _label(row.get(SPR_CONCEPT)) or concept
        state = _label(row.get(SPR_STATE)) or state
        if not state or not concept:
            errors.append(f"SPR row {r}: Concept and State are required")
            continue
        if (concept, state) in seen:
            errors.append(f"SPR row {r}: {concept} / {state} appears twice")
            continue
        seen.add((concept, state))
        for month in FISCAL_MONTHS:
            values = []
            for col in (spr_cols[month], ipr_cols[month]):
                v = row.get(col)
                try:
                    values.append(None if v is None else float(_number(v)))
                except ValueError as exc:
                    errors.append(f"SPR {_col_letters(col)}{r}: "
                                  f"{str(exc)!r} is not a number")
                    values.append(None)
            if values != [None, None]:
                out.append((concept, state, month, *values))
    if errors:
        raise DscImportError("\n".join(errors))
    return out


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
def import_workbook(xlsx: Path, db_path: Path) -> dict:
    """Parse everything first, then replace both tables in one
    transaction. Returns counts and warnings. The import timestamp is
    recorded (reference_data_info, schema v5) inside the same
    transaction; a failed import changes nothing, including the
    timestamp."""
    if not db_path.is_file():
        raise DscImportError(f"Database not found: {db_path}")
    shares = parse_dsc(read_sheet(xlsx, DSC_SHEET))
    benchmarks = parse_spr(read_sheet(xlsx, SPR_SHEET))
    conn = store_ops_db.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 5:
            raise DscImportError(
                f"Database schema is v{version}; start this release once "
                "so it migrates to v5, then import")
        with conn:
            conn.execute("DELETE FROM contribution_shares")
            conn.execute("DELETE FROM basket_benchmarks")
            conn.executemany(
                "INSERT INTO contribution_shares (state, concept, day, "
                "month, share) VALUES (?,?,?,?,?)", shares)
            conn.executemany(
                "INSERT INTO basket_benchmarks (concept, state, month, "
                "avg_spr, avg_ipr) VALUES (?,?,?,?,?)", benchmarks)
            conn.execute(
                "INSERT INTO reference_data_info (id, imported_at) "
                "VALUES (1, datetime('now')) "
                "ON CONFLICT (id) DO UPDATE SET imported_at = "
                "datetime('now')")
    finally:
        conn.close()
    pairs = {(s, c) for s, c, *_ in shares} | {(s, c)
                                              for c, s, *_ in benchmarks}
    warnings = sorted(
        {f"State {s!r} is not a store State option" for s, _ in pairs
         if s not in STATES}
        | {f"Concept {c!r} is not a store Concept option" for _, c in pairs
           if c not in CONCEPTS})
    return {"shares": len(shares),
            "benchmarks": len({(c, s) for c, s, *_ in benchmarks}),
            "benchmark_months": len(benchmarks),
            "pairs": len({(s, c) for s, c, *_ in shares}),
            "warnings": warnings}


def reference_data_info(db_path: Path) -> dict:
    """What's loaded now: State/Concept pairs, benchmark months and the
    last import date (None when never imported)."""
    conn = store_ops_db.connect(db_path)
    try:
        pairs = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT state, concept "
            "FROM contribution_shares)").fetchone()[0]
        months = conn.execute(
            "SELECT COUNT(*) FROM basket_benchmarks").fetchone()[0]
        try:
            row = conn.execute(
                "SELECT imported_at FROM reference_data_info "
                "WHERE id = 1").fetchone()
        except sqlite3.OperationalError:  # pre-v5 database
            row = None
    finally:
        conn.close()
    return {"pairs": pairs, "benchmark_months": months,
            "imported_at": row[0] if row else None}
