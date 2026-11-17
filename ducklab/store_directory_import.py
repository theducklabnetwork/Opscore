"""Import of the monthly-sales workbook into the store directory.

Used by ``scripts/import_store_directory.py``. The workbook (one sheet,
e.g. "2025-2026") lists one store per row:

  A No., B Store Code, C Store Name, D Display Area, E Concept, F State,
  G Zone, H BD Target, then one column per month whose header is a real
  Excel date (Jan 2025 onwards).

Only Code, Name, Display Area, State, Zone and the monthly sales are
imported. Concept and BD Target are deliberately NOT imported: Concept is
an Opscore dropdown tied to the DSC reference data, and BD Target is a
manual Monthly Setup input.

The directory is reference data, like the DSC tables. No stores are
created and no daily records are written. A blank month cell is skipped;
a 0 cell is skipped too (the workbook uses 0 for "not open yet"), so the
directory never records a zero-sales month. Amounts are RM with cents and
are stored as integer cents.

Everything is parsed and validated before anything is written, then both
directory tables are replaced in one transaction: either the whole
workbook is imported or nothing changes. Stdlib only (zipfile + XML) via
:mod:`ducklab.dsc_import`, so the production venv can run it.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from ducklab import dsc_import, store_ops_db

HEADER_ROW = 1
COL_CODE, COL_NAME, COL_AREA = 2, 3, 4          # B C D
COL_BD_TARGET = 8                               # H
COL_STATE, COL_ZONE = 6, 7                      # F G
FIRST_MONTH_COL = 9                             # I onwards
MAX_MONTH_COL = 200                             # safety bound, not a limit

# The workbook carries one BD Target column. The business states that it
# covers October 2026; the file itself does not say, so the month is
# declared here rather than inferred from the sales columns.
BD_TARGET_MONTH = "2026-10"

# Text used in the workbook where a store has no BD Target.
BD_TARGET_NO_VALUE = {"NIL", "N/A", "-", "--"}

# Excel's 1900 date system serial for 1899-12-30 (the usual epoch, with the
# historical leap-year bug already accounted for by this offset).
_EXCEL_EPOCH = date(1899, 12, 30)


class DirectoryImportError(Exception):
    """The workbook cannot be imported (nothing is written)."""


def _label(value) -> str:
    # A numeric 0 in a text column (State/Zone for the event counters in the
    # source workbook) means "not applicable", not the text "0.0".
    if value is None or (isinstance(value, (int, float))
                         and not isinstance(value, bool) and value == 0):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _month_key(value) -> str | None:
    """A month header cell -> 'YYYY-MM', or None when it is not a date.

    The stdlib reader returns numbers as floats, so a date header arrives
    as an Excel serial. Text dates are accepted too. A non-date header
    (e.g. a totals column) returns None rather than failing.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Only real business months: 1990-01-01 (serial 32874) to
        # 9999-12-31 (serial 2958465). A smaller number is a plain figure
        # in a month column, not a date.
        if not (32874 <= value <= 2958465):
            return None
        day = _EXCEL_EPOCH + timedelta(days=int(value))
        return f"{day.year:04d}-{day.month:02d}"
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}"
    text = _label(value)
    if len(text) >= 10:
        try:
            day = date.fromisoformat(text[:10])
        except ValueError:
            return None
        return f"{day.year:04d}-{day.month:02d}"
    return None


def _cents(value) -> int:
    """RM cell -> integer cents, rounded half-up. Never a float amount."""
    if isinstance(value, bool):
        raise ValueError(str(value))
    if isinstance(value, (int, float)):
        amount = Decimal(repr(value))
    else:
        try:
            amount = Decimal(_label(value).replace(",", ""))
        except InvalidOperation:
            raise ValueError(_label(value))
    if not amount.is_finite():
        raise ValueError(str(value))
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _bd_target(value, code: str, warnings: list[str]) -> int | None:
    """The workbook's BD Target cell -> integer cents, or None.

    A blank cell, the workbook's "NIL" text, or a numeric 0 means the store
    has no BD Target: the result is None, never RM 0. A numeric 0 is the
    workbook's own "not applicable" marker, the same convention its State and
    Zone columns use for the event counters. A value that is not a number is
    reported as a warning and ignored, so one bad cell never fails the
    whole import.
    """
    if value is None:
        return None
    label = _label(value)
    if not label or label.upper() in BD_TARGET_NO_VALUE:
        return None
    try:
        cents = _cents(value)
    except ValueError:
        warnings.append(
            f"{code}: BD Target {label!r} is not a number; ignored")
        return None
    if cents < 0:
        warnings.append(f"{code}: BD Target cannot be negative; ignored")
        return None
    if cents == 0:
        return None                   # 0 = not applicable, not a target
    return cents


def parse_workbook(cells) -> tuple[list[dict], list[dict], list[str]]:
    """Cells -> (stores, history rows, warnings). Raises on any error.

    stores:   {code, name, state, zone, display_area, bd_target_cents}
    history:  {code, month, sales_cents}   (zero/blank months omitted)
    """
    max_row = max((r for r, _ in cells), default=0)
    if max_row <= HEADER_ROW:
        raise DirectoryImportError("No data rows found under the header")

    # Month columns come from the header row only; anything without a date
    # header (totals, notes) is ignored.
    month_cols: dict[int, str] = {}
    for (r, c), v in cells.items():
        if r != HEADER_ROW or c < FIRST_MONTH_COL or c > MAX_MONTH_COL:
            continue
        key = _month_key(v)
        if key is not None:
            month_cols[c] = key
    if not month_cols:
        raise DirectoryImportError(
            "No month columns found: row 1 must hold real dates from "
            f"column {dsc_import._col_letters(FIRST_MONTH_COL)} onwards")

    warnings: list[str] = []
    for col, key in sorted(month_cols.items()):
        raw = cells.get((HEADER_ROW, col))
        day = None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            day = (_EXCEL_EPOCH + timedelta(days=int(raw))).day
        if day not in (None, 1):
            warnings.append(
                f"{dsc_import._col_letters(col)}{HEADER_ROW} is a mid-month "
                f"date; imported as {key}")

    stores, history, errors, seen = [], [], [], set()
    for r in range(HEADER_ROW + 1, max_row + 1):
        row = {c: v for (rr, c), v in cells.items() if rr == r}
        if not row:
            continue
        code = _label(row.get(COL_CODE))
        name = _label(row.get(COL_NAME))
        if not code and not name:
            continue                            # spacer row
        if not code or not name:
            errors.append(f"Row {r}: Store Code and Store Name are required")
            continue
        if code in seen:
            errors.append(f"Row {r}: Store Code {code!r} appears twice")
            continue
        seen.add(code)
        state, zone = _label(row.get(COL_STATE)), _label(row.get(COL_ZONE))
        if not state or not zone:
            warnings.append(
                f"{code}: State/Zone blank in the workbook; left blank")
        area = row.get(COL_AREA)
        try:
            area_value = (None if not _label(area) else int(float(area)))
        except (ValueError, TypeError):
            area_value = None
            warnings.append(f"{code}: Display Area is not a number; ignored")
        stores.append({"code": code, "name": name,
                       "state": state or None, "zone": zone or None,
                       "display_area": area_value,
                       "bd_target_cents": _bd_target(row.get(COL_BD_TARGET),
                                                    code, warnings)})
        for col, month in sorted(month_cols.items()):
            value = row.get(col)
            if value is None or not _label(value):
                continue                        # not reported
            try:
                cents = _cents(value)
            except ValueError:
                errors.append(
                    f"Row {r} {dsc_import._col_letters(col)}{r}: sales "
                    f"{_label(value)!r} is not a number")
                continue
            if cents < 0:
                errors.append(
                    f"Row {r} {dsc_import._col_letters(col)}{r}: sales "
                    "cannot be negative")
                continue
            if cents == 0:
                continue                        # 0 = not open / not reported
            history.append({"code": code, "month": month,
                            "sales_cents": cents})
    if errors:
        raise DirectoryImportError("\n".join(errors[:50]))
    if not stores:
        raise DirectoryImportError("No store rows found")
    return stores, history, warnings


def import_workbook(xlsx: Path, db_path: Path, *, sheet: str | None = None,
                    bundled: bool = False) -> dict:
    """Replace the store directory from the workbook, all-or-nothing.

    Returns counts and warnings. Existing stores, monthly configs and daily
    records are never touched. ``bundled`` records whether this is the
    shipped workbook, so startup may refresh it later without ever replacing
    a directory the user uploaded.
    """
    if not db_path.is_file():
        raise DirectoryImportError(f"Database not found: {db_path}")
    if sheet is None:
        sheet = _first_sheet_name(xlsx)
    stores, history, warnings = parse_workbook(
        dsc_import.read_sheet(Path(xlsx), sheet))
    try:
        conn = store_ops_db.connect(db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 16:
                raise DirectoryImportError(
                    f"Database schema is v{version}; start this release once "
                    "so it migrates to v16, then import")
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(store_directory_info)")}
            info_sql, info_params = (
                ("INSERT INTO store_directory_info (id, imported_at, "
                 "source_name, bundled) VALUES (1, datetime('now'), ?, ?) "
                 "ON CONFLICT (id) DO UPDATE SET imported_at = "
                 "datetime('now'), source_name = excluded.source_name, "
                 "bundled = excluded.bundled",
                 (Path(xlsx).name, int(bundled)))
                if "bundled" in columns else
                ("INSERT INTO store_directory_info (id, imported_at, "
                 "source_name) VALUES (1, datetime('now'), ?) "
                 "ON CONFLICT (id) DO UPDATE SET imported_at = "
                 "datetime('now'), source_name = excluded.source_name",
                 (Path(xlsx).name,)))
            # bd_target_cents arrived in v18; a v16/v17 database still
            # imports, without the BD Target column.
            store_columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(store_directory)")}
            with_bd = "bd_target_cents" in store_columns
            store_sql = (
                "INSERT INTO store_directory (code, name, state, zone, "
                "display_area, bd_target_cents) VALUES (:code, :name, "
                ":state, :zone, :display_area, :bd_target_cents)"
                if with_bd else
                "INSERT INTO store_directory (code, name, state, zone, "
                "display_area) VALUES (:code, :name, :state, :zone, "
                ":display_area)")
            rows = stores if with_bd else [
                {k: v for k, v in s.items() if k != "bd_target_cents"}
                for s in stores]
            with conn:
                conn.execute("DELETE FROM store_directory_history")
                conn.execute("DELETE FROM store_directory")
                conn.executemany(store_sql, rows)
                conn.executemany(
                    "INSERT INTO store_directory_history (code, month, "
                    "sales_cents) VALUES (:code, :month, :sales_cents)",
                    history)
                conn.execute(info_sql, info_params)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise DirectoryImportError(f"Database error during import: {exc}") from exc
    months = {h["month"] for h in history}
    return {"stores": len(stores), "history": len(history),
            "months": len(months),
            "first_month": min(months) if months else None,
            "last_month": max(months) if months else None,
            "warnings": warnings}


def _first_sheet_name(xlsx: Path) -> str:
    """The workbook's first sheet name (the monthly-sales file has one)."""
    import zipfile
    from xml.etree import ElementTree
    with zipfile.ZipFile(xlsx) as zf:
        book = ElementTree.fromstring(zf.read("xl/workbook.xml"))
    for el in book.iter(f"{{{dsc_import._NS['m']}}}sheet"):
        name = (el.get("name") or "").strip()
        if name:
            return name
    raise DirectoryImportError("Workbook has no sheets")


def directory_info(db_path: Path) -> dict:
    """What is loaded now: store/month counts and the last import details."""
    conn = store_ops_db.connect(db_path)
    try:
        stores = conn.execute("SELECT COUNT(*) FROM store_directory").fetchone()[0]
        history = conn.execute(
            "SELECT COUNT(*), MIN(month), MAX(month) "
            "FROM store_directory_history").fetchone()
        row = conn.execute(
            "SELECT imported_at, source_name FROM store_directory_info "
            "WHERE id = 1").fetchone()
        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(store_directory_info)")}
        bundled = bool(conn.execute(
            "SELECT bundled FROM store_directory_info WHERE id = 1"
        ).fetchone()[0]) if row and "bundled" in columns else False
    finally:
        conn.close()
    return {"stores": stores, "history": history[0],
            "first_month": history[1], "last_month": history[2],
            "imported_at": row[0] if row else None,
            "source_name": row[1] if row else None,
            "bundled": bundled}


def lookup(db_path: Path, code: str = "", name: str = "") -> dict | None:
    """Exact code OR exact name match (case-insensitive) for the New Store
    form. Never a partial match: typing must not auto-fill prematurely."""
    code, name = code.strip(), name.strip()
    if not code and not name:
        return None
    conn = store_ops_db.connect(db_path)
    try:
        row = None
        if code:
            row = conn.execute(
                "SELECT code, name, state, zone FROM store_directory "
                "WHERE code = ? COLLATE NOCASE", (code,)).fetchone()
        if row is None and name:
            row = conn.execute(
                "SELECT code, name, state, zone FROM store_directory "
                "WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"code": row[0], "name": row[1], "state": row[2] or "",
            "zone": row[3] or ""}


def history_for(db_path: Path, code: str) -> dict[str, int]:
    """{'YYYY-MM': cents} for one store code, all imported months."""
    conn = store_ops_db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT month, sales_cents FROM store_directory_history "
            "WHERE code = ? ORDER BY month", (code,)).fetchall()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def bd_target_for(db_path: Path, code: str) -> int | None:
    """The imported BD Target (cents) for one store code, or None.

    The figure belongs to BD_TARGET_MONTH (October 2026). It is reference
    data only: it feeds no KPI, target level, budget or achievement
    number. ``monthly_configs.bd_target_cents`` holds the per-store,
    per-month value the user types in Monthly Setup.
    """
    conn = store_ops_db.connect(db_path)
    try:
        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(store_directory)")}
        if "bd_target_cents" not in columns:
            return None
        row = conn.execute(
            "SELECT bd_target_cents FROM store_directory WHERE code = ?",
            (code,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def comparison_figures(db_path: Path, code: str, month: str) -> dict:
    """Sales Last Month / Last Year (cents) for Monthly Setup.

    A month absent from the directory (zero or blank in the workbook)
    yields None, which the form shows as blank — never as RM 0.
    """
    year, mon = int(month[:4]), int(month[5:7])
    previous = f"{year - (mon == 1):04d}-{12 if mon == 1 else mon - 1:02d}"
    last_year = f"{year - 1:04d}-{mon:02d}"
    history = history_for(db_path, code)
    return {"sales_last_month_cents": history.get(previous),
            "sales_last_year_cents": history.get(last_year)}
