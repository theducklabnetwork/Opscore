"""Monthly report snapshots: preview, finalization, versioning, corrections.

Backend-only. Integrates the read repository, operations_service (live KPI
assembly) and the existing monthly_snapshots table. No KPI formulas here.

Schema notes / limitations (verified against store_ops_db.py):
- UNIQUE(store_id, month, version) prevents version reuse/overwrite.
- superseded_by references monthly_snapshots(id); ownership checks are
  enforced at application level (the FK alone cannot prevent a cross-store
  link — tested below).
- No correction_reason / operator / approved_by columns exist. Correction
  metadata is limited to created_at + the supersession chain. This is a
  documented limitation, not silently migrated.
- Snapshots preserve KPI OUTPUTS. The DB has no source-record event history,
  so point-in-time reconstruction of original inputs is NOT possible.

Serialization: JSON, payload format "ducklab.snapshot.v1". Money stays
integer cents; Decimals serialize as strings; None stays null.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from ducklab import store_ops_db
from ducklab.operations_repository import (
    InvalidMonthError, OperationsRepository, RepositoryError,
)
from ducklab.kpi import MonthConfig
from ducklab.operations_repository import Store
from ducklab.operations_service import (
    ActionsSection, ExpenseCategoryRow, ExpensesSection, ManpowerSection,
    MonthlyOperationsReport, SalesSection, StockSection, assemble_month,
)

PAYLOAD_FORMAT = "ducklab.snapshot.v1"


class SnapshotError(RepositoryError):
    """Base snapshot failure."""


class SnapshotNotFoundError(SnapshotError):
    """No finalized snapshot for the requested store/month/version."""


# --------------------------------------------------------------------------
# Serialization (lossless for cents / Decimal / None)
# --------------------------------------------------------------------------

def _encode(obj: Any) -> Any:
    if isinstance(obj, MonthlyOperationsReport):
        # Live Dashboard source rows are not report outputs. Keep the v1
        # snapshot payload unchanged when sharing assembled daily records.
        return {k: _encode(v) for k, v in asdict(obj).items() if k != "days"}
    if isinstance(obj, Decimal):
        return {"__decimal__": str(obj)}
    if isinstance(obj, date):
        return {"__date__": obj.isoformat()}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _encode(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj  # int / str / bool / None pass through exactly


def _decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj) == {"__decimal__"}:
            return Decimal(obj["__decimal__"])
        if set(obj) == {"__date__"}:
            return date.fromisoformat(obj["__date__"])
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def serialize_report(report: MonthlyOperationsReport) -> str:
    """Stable JSON payload with format tag. Integer cents pass through
    exactly; Decimals become strings — never binary floats."""
    return json.dumps(
        {"format": PAYLOAD_FORMAT, "report": _encode(report)},
        sort_keys=True,
    )


def deserialize_report(payload: str) -> dict:
    data = json.loads(payload)
    if data.get("format") != PAYLOAD_FORMAT:
        raise SnapshotError(f"Unknown snapshot payload format: {data.get('format')!r}")
    return _decode(data["report"])


def snapshot_to_report(snapshot: dict) -> MonthlyOperationsReport:
    """Rebuild the typed report from an already-authenticated snapshot.

    Only the stored payload is used: no repository reads or KPI recalculation.
    """
    data = snapshot["report"]
    sections = {
        "sales": SalesSection,
        "manpower": ManpowerSection,
        "stock": StockSection,
        "actions": ActionsSection,
        "config": MonthConfig,
        "store": Store,
    }
    values = {
        name: cls(**data[name])
        for name, cls in sections.items()
    }
    expenses = dict(data["expenses"])
    expenses["categories"] = [
        ExpenseCategoryRow(**row) for row in expenses.get("categories", [])
    ]
    values["expenses"] = ExpensesSection(**expenses)
    return MonthlyOperationsReport(
        month=data["month"], is_snapshot=True, **values,
    )


# --------------------------------------------------------------------------
# Finalization warnings (operational completeness, not approval rules)
# --------------------------------------------------------------------------

def finalization_warnings(report: MonthlyOperationsReport) -> list[str]:
    """Non-blocking operational warnings surfaced at finalization time.

    No HQ approval policy is invented; these mirror the workbook's own
    'awaiting / incomplete' semantics.
    """
    w: list[str] = []
    s, m, e = report.sales, report.manpower, report.expenses
    if s.days_entered < s.days_in_month:
        w.append(f"Only {s.days_entered} of {s.days_in_month} days have sales entered")
    if m.total_salary_cents is None:
        w.append("Payroll inputs missing (awaiting payroll)")
    for status, count in e.validation_counts.items():
        if status != "OK":
            w.append(f"{count} expense record(s) with status '{status}'")
    if report.config.level1_base_cents is None:
        w.append("No Level 1 sales target configured")
    return w


# --------------------------------------------------------------------------
# Snapshot service
# --------------------------------------------------------------------------

class SnapshotService:
    def __init__(self, db_path: Path | str = store_ops_db.DB_PATH):
        self._db_path = db_path
        self.repo = OperationsRepository(db_path)

    # -- preview ---------------------------------------------------------
    def preview_month(self, store_id: int, month: str,
                      as_of: Optional[date] = None) -> MonthlyOperationsReport:
        """LIVE recalculated report. Never writes anything."""
        return assemble_month(self.repo, store_id, month, as_of=as_of)

    # -- finalization ------------------------------------------------------
    def finalize_month(
        self, store_id: int, month: str, as_of: Optional[date] = None
    ) -> dict:
        """Create the next finalized snapshot version (v1 or a correction).

        Atomic: report assembly happens on committed data, then version
        allocation + insert + supersession update run in one IMMEDIATE
        transaction (write lock taken up front — no read-then-write race).
        Rollback on any failure leaves no partial snapshot or broken chain.
        """
        # Recalculate from committed records (before taking the write lock).
        report = assemble_month(self.repo, store_id, month, as_of=as_of)
        payload = serialize_report(report)
        warnings = finalization_warnings(report)

        conn = store_ops_db.connect(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_chain(conn, store_id, month)  # pre-check existing chain
            row = conn.execute(
                "SELECT id, version FROM monthly_snapshots "
                "WHERE store_id = ? AND month = ? ORDER BY version DESC LIMIT 1",
                (store_id, month),
            ).fetchone()
            next_version = (row[1] + 1) if row else 1
            cur = conn.execute(
                "INSERT INTO monthly_snapshots (store_id, month, version, "
                "kpi_json, finalized) VALUES (?,?,?,?,1)",
                (store_id, month, next_version, payload),
            )
            new_id = cur.lastrowid
            if row is not None:
                # Supersession: verify ownership before linking (FK alone
                # cannot prevent cross-store/month links).
                prev = conn.execute(
                    "SELECT store_id, month FROM monthly_snapshots WHERE id = ?",
                    (row[0],),
                ).fetchone()
                if prev[0] != store_id or prev[1] != month:
                    raise SnapshotError("Refusing cross-store/month supersession")
                conn.execute(
                    "UPDATE monthly_snapshots SET superseded_by = ? WHERE id = ?",
                    (new_id, row[0]),
                )
            self._validate_chain(conn, store_id, month)
            conn.commit()
            return {"snapshot_id": new_id, "version": next_version,
                    "superseded_id": row[0] if row else None,
                    "warnings": warnings}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def correct_month(self, store_id: int, month: str,
                      as_of: Optional[date] = None) -> dict:
        """Explicit correction: requires an existing finalized version."""
        latest = self.repo.get_snapshot(store_id, month)
        if latest is None:
            raise SnapshotNotFoundError(
                f"No finalized snapshot to correct for store {store_id} in {month}")
        return self.finalize_month(store_id, month, as_of=as_of)

    def _validate_chain(self, conn: sqlite3.Connection, store_id: int,
                        month: str) -> None:
        """No cycles, no dangling/cross-scope links, one active head."""
        rows = conn.execute(
            "SELECT id, superseded_by FROM monthly_snapshots "
            "WHERE store_id = ? AND month = ? ORDER BY version",
            (store_id, month),
        ).fetchall()
        ids = {r[0] for r in rows}
        if not ids:
            return  # nothing finalized yet: valid
        for r in rows:
            if r[1] is not None:
                if r[1] not in ids:
                    raise SnapshotError("Dangling or cross-scope supersession link")
                if r[1] == r[0]:
                    raise SnapshotError("Self-referential supersession")
        heads = [r for r in rows if r[1] is None]
        if len(heads) != 1:
            raise SnapshotError(f"Expected exactly one active head, got {len(heads)}")

    # -- retrieval ---------------------------------------------------------
    def get_finalized(self, store_id: int, month: str,
                      version: Optional[int] = None) -> dict:
        """Latest (or specific) finalized snapshot. Never falls back to a
        live report — a missing snapshot is a clear not-found result."""
        snap = self.repo.get_snapshot(store_id, month, version)
        if snap is None or not snap["finalized"]:
            raise SnapshotNotFoundError(
                f"No finalized snapshot for store {store_id} in {month}"
                + (f" version {version}" if version else ""))
        return {**snap, "report": deserialize_report(snap["kpi_json"])}

    def list_versions(self, store_id: int, month: str) -> list[dict]:
        """Finalized versions in version order, with metadata (no payloads)."""
        conn = store_ops_db.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT id, version, finalized, superseded_by, created_at "
                "FROM monthly_snapshots WHERE store_id = ? AND month = ? "
                "AND finalized = 1 ORDER BY version",
                (store_id, month),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def diff_with_live(self, store_id: int, month: str,
                       as_of: Optional[date] = None) -> dict:
        """Where practical, show that live results differ from the latest
        finalized snapshot (e.g. after historical edits). Scalars compared
        field-by-field at the top level of each section."""
        finalized = self.get_finalized(store_id, month)["report"]
        live = _encode(assemble_month(self.repo, store_id, month, as_of=as_of))
        live = _decode(json.loads(json.dumps(live)))  # normalize shapes
        diffs = []
        for section in ("sales", "expenses", "manpower", "stock", "actions"):
            f, l = finalized.get(section, {}), live.get(section, {})
            for key in sorted(set(f) | set(l)):
                if key == "categories":
                    continue
                if f.get(key) != l.get(key):
                    diffs.append({"section": section, "field": key,
                                  "finalized": f.get(key), "live": l.get(key)})
        return {"month": month, "differs": bool(diffs), "differences": diffs}
