"""Exact money handling with Decimal, strictly avoiding float conversions."""

from decimal import Decimal, InvalidOperation
from typing import Final

SUPPORTED_CURRENCIES: Final[set[str]] = {"INR", "USD"}
TWO_PLACES: Final[Decimal] = Decimal("0.01")


class MoneyValidationError(ValueError):
    """Raised when an amount cannot be validated according to currency precision rules."""

    pass


def parse_money(
    value: str | Decimal | int,
    *,
    allow_negative: bool = False,
    field_name: str = "amount",
) -> Decimal:
    """Parse and validate money string into an exact Decimal.

    Rules:
    - Never accept float to prevent IEEE 754 precision leakage.
    - Must be a valid decimal representation.
    - Exponent must have at most 2 decimal places (no fractional cents/paise).
    - Must be non-negative unless allow_negative=True.
    """
    if isinstance(value, (float, bool)):
        raise MoneyValidationError(
            f"{field_name} must not be a float to prevent precision corruption; pass str, int or Decimal."
        )

    if isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, Decimal):
        d = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise MoneyValidationError(f"{field_name} cannot be empty.")
        # Reject scientific notation like 1e5
        if "e" in cleaned.lower():
            raise MoneyValidationError(
                f"{field_name} contains invalid scientific notation: {value}"
            )
        try:
            d = Decimal(cleaned)
        except InvalidOperation as err:
            raise MoneyValidationError(
                f"{field_name} is not a valid decimal number: '{value}'"
            ) from err
    else:
        raise MoneyValidationError(
            f"{field_name} must be str, int or Decimal, got {type(value).__name__}"
        )

    if not d.is_finite() or d.copy_abs() >= Decimal("1000000000000"):
        raise MoneyValidationError(f"{field_name} must be finite and fit NUMERIC(14,2).")

    if not allow_negative and d < Decimal(0):
        raise MoneyValidationError(f"{field_name} must be non-negative, got {d}")

    # Check precision: quantization with round=None or checking exponent
    # as_tuple().exponent gives negative decimal count, e.g. -2 for 100.25, -3 for 100.255
    exponent = d.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise MoneyValidationError(
            f"{field_name} exceeds supported 2-decimal precision (got {abs(exponent)} decimals in '{value}'). "
            "Input must not be silently rounded."
        )

    # Normalize to 2 decimal places (e.g. Decimal('100') -> Decimal('100.00'))
    return d.quantize(TWO_PLACES)


def validate_currency(currency: str) -> str:
    """Validate that currency is supported (INR or USD)."""
    normalized = currency.strip().upper()
    if normalized not in SUPPORTED_CURRENCIES:
        raise MoneyValidationError(
            f"Unsupported currency '{currency}'. Version 1 supports only: {', '.join(sorted(SUPPORTED_CURRENCIES))}"
        )
    return normalized


def format_money(value: Decimal, currency: str = "") -> str:
    """Format decimal amount with 2 decimal places."""
    formatted = f"{value:.2f}"
    if currency:
        return f"{currency} {formatted}"
    return formatted
