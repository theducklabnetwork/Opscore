"""Shared parsing and validation for client-supplied form values.

Blank-versus-zero decisions belong to the callers: these parsers validate a
value only after the caller has determined that it should be parsed. Money
uses integer cents; decimal inputs never pass through binary floats.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional


# Input bounds: anything larger is a typo and would overflow SQLite INTEGER.
MAX_AMOUNT_RM = Decimal("100000000.00")   # RM 100,000,000.00
MAX_COUNT = 1_000_000


def parse_money_cents(raw: str) -> int:
    """Shared money parser for all data-entry pages: '100000' or
    '100,000.00' -> integer cents. Rejects sub-cent input, inf, nan and
    amounts above RM 100,000,000.00 ("Amount too large").
    Negative amounts are allowed here (refunds are valid money); callers
    that forbid negatives say so themselves. Raises ValueError."""
    cleaned = raw.replace(",", "").replace("RM", "").strip()
    try:
        dec = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {raw!r}")
    if not dec.is_finite():
        raise ValueError(f"Invalid amount: {raw!r}")
    # copy_abs() is exact (no context), so huge exponents cannot overflow
    if dec.copy_abs() > MAX_AMOUNT_RM:
        raise ValueError("Amount too large")
    cents = int(dec * 100)
    if Decimal(cents) != dec * 100:
        raise ValueError(f"Amount has sub-cent precision: {raw!r}")
    return cents


def parse_count(raw: str, label: str) -> int:
    """Whole, non-negative number. Raises ValueError with ``label``."""
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{label} must be a whole number")
    if value < 0:
        raise ValueError(f"{label} cannot be negative")
    if value > MAX_COUNT:
        raise ValueError(f"{label} is too large")
    return value


def parse_decimal(raw: str, label: str, lo: Decimal, hi: Decimal) -> Decimal:
    """Finite decimal within [lo, hi] (factors, percentages). "NaN" and
    "inf" are rejected like any non-number. Raises ValueError."""
    try:
        d = Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError(f"{label} must be a number")
    if not d.is_finite():
        raise ValueError(f"{label} must be a number")
    if not (lo <= d <= hi):
        raise ValueError(f"{label} must be between {lo} and {hi}")
    return d


def parse_client_id(raw, what: str, required: bool = False) -> Optional[int]:
    """Parse an id sent over the socket ('' -> None unless ``required``).
    Anything that is not a positive whole number is rejected with
    ValidationError before any state changes."""
    # Local import avoids a repository/KPI import cycle at module load.
    from ducklab.operations_write_repository import ValidationError

    s = str(raw if raw is not None else "").strip()
    if not s and not required:
        return None
    try:
        value = int(s)
    except ValueError:
        value = 0
    if value <= 0:
        raise ValidationError(f"Unknown {what} — reopen it from the list.")
    return value
