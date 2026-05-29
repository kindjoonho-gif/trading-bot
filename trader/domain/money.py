from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

ZERO = Decimal("0")


def to_decimal(value: str | int | float | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def round_toward_zero(value: Decimal) -> Decimal:
    """Floor magnitude, preserve sign. Used for share quantities."""
    if value >= 0:
        return value.to_integral_value(rounding=ROUND_DOWN)
    return -((-value).to_integral_value(rounding=ROUND_DOWN))


def format_krw(value: Decimal) -> str:
    return f"₩{value:,.0f}"
