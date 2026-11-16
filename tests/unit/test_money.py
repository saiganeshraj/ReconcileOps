"""Unit tests for exact money arithmetic."""

from decimal import Decimal

import pytest

from reconcileops.domain.money import (
    MoneyValidationError,
    format_money,
    parse_money,
    validate_currency,
)


def test_parse_money_valid_strings():
    assert parse_money("100") == Decimal("100.00")
    assert parse_money("100.5") == Decimal("100.50")
    assert parse_money("100.55") == Decimal("100.55")
    assert parse_money("0") == Decimal("0.00")
    assert parse_money("0.00") == Decimal("0.00")


def test_parse_money_from_int_and_decimal():
    assert parse_money(100) == Decimal("100.00")
    assert parse_money(Decimal("250.75")) == Decimal("250.75")


def test_parse_money_rejects_floats():
    with pytest.raises(MoneyValidationError, match="must not be a float"):
        parse_money(100.50)  # Float input is forbidden


def test_parse_money_rejects_excessive_precision():
    # Never silently round
    with pytest.raises(MoneyValidationError, match="exceeds supported 2-decimal precision"):
        parse_money("100.555")

    with pytest.raises(MoneyValidationError, match="exceeds supported 2-decimal precision"):
        parse_money("100.0001")


def test_parse_money_rejects_negative_by_default():
    with pytest.raises(MoneyValidationError, match="must be non-negative"):
        parse_money("-10.00")


def test_parse_money_allows_negative_when_specified():
    assert parse_money("-10.00", allow_negative=True) == Decimal("-10.00")


def test_parse_money_rejects_scientific_notation():
    with pytest.raises(MoneyValidationError, match="invalid scientific notation"):
        parse_money("1e4")


def test_validate_currency():
    assert validate_currency("inr") == "INR"
    assert validate_currency("USD") == "USD"
    with pytest.raises(MoneyValidationError, match="Unsupported currency"):
        validate_currency("EUR")


def test_format_money():
    assert format_money(Decimal("123.40")) == "123.40"
    assert format_money(Decimal("123.40"), "INR") == "INR 123.40"
