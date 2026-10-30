"""Focused contract tests for shared client-input parsers."""

from decimal import Decimal

import pytest

from ducklab.input_validation import (
    parse_client_id, parse_count, parse_decimal, parse_money_cents,
)
from ducklab.operations_write_repository import ValidationError


@pytest.mark.parametrize("raw, expected", [
    ("100000", 10_000_000),
    ("10,000.00", 1_000_000),
    ("RM 12.34", 1234),
    ("0", 0),
    ("-0.01", -1),
])
def test_money_accepts_supported_values(raw, expected):
    assert parse_money_cents(raw) == expected


@pytest.mark.parametrize("raw, message", [
    ("", "Invalid amount: ''"),
    ("   ", "Invalid amount: '   '"),
    ("not money", "Invalid amount: 'not money'"),
    ("1.005", "Amount has sub-cent precision: '1.005'"),
    ("100000000.01", "Amount too large"),
    ("-100000000.01", "Amount too large"),
])
def test_money_rejects_invalid_values_exactly(raw, message):
    with pytest.raises(ValueError) as exc_info:
        parse_money_cents(raw)
    assert str(exc_info.value) == message


@pytest.mark.parametrize("raw", ["NaN", "sNaN", "inf", "-Infinity"])
def test_money_rejects_non_finite_decimals(raw):
    with pytest.raises(ValueError) as exc_info:
        parse_money_cents(raw)
    assert str(exc_info.value) == f"Invalid amount: {raw!r}"


def test_count_preserves_zero_and_rejects_blank_negative_and_malformed():
    assert parse_count("0", "Headcount") == 0
    assert parse_count("12", "Headcount") == 12
    for raw, message in [
        ("", "Headcount must be a whole number"),
        ("1.5", "Headcount must be a whole number"),
        ("-1", "Headcount cannot be negative"),
    ]:
        with pytest.raises(ValueError) as exc_info:
            parse_count(raw, "Headcount")
        assert str(exc_info.value) == message


def test_decimal_preserves_precision_and_boundaries():
    assert parse_decimal(" 0.123456789 ", "Factor", Decimal(0), Decimal(2)) == \
        Decimal("0.123456789")
    assert parse_decimal("0", "Factor", Decimal(0), Decimal(2)) == Decimal(0)
    assert parse_decimal("2", "Factor", Decimal(0), Decimal(2)) == Decimal(2)
    for raw, message in [
        ("", "Factor must be a number"),
        ("bad", "Factor must be a number"),
        ("-0.01", "Factor must be between 0 and 2"),
        ("2.01", "Factor must be between 0 and 2"),
    ]:
        with pytest.raises(ValueError) as exc_info:
            parse_decimal(raw, "Factor", Decimal(0), Decimal(2))
        assert str(exc_info.value) == message


@pytest.mark.parametrize("raw", ["NaN", "sNaN", "inf", "-Infinity"])
def test_decimal_rejects_non_finite_values(raw):
    with pytest.raises(ValueError) as exc_info:
        parse_decimal(raw, "Factor", Decimal(0), Decimal(2))
    assert str(exc_info.value) == "Factor must be a number"


def test_client_id_blank_zero_negative_malformed_and_required():
    assert parse_client_id(None, "store") is None
    assert parse_client_id("  ", "store") is None
    assert parse_client_id(" 12 ", "store") == 12
    for raw in ("", "0", "-3", "1.5", "1e3", "bad"):
        with pytest.raises(ValidationError) as exc_info:
            parse_client_id(raw, "store", required=True)
        assert str(exc_info.value) == "Unknown store — reopen it from the list."
