"""Human-facing integer point counts; does not modify accounting values."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def format_point_count(value):
    try:
        number = Decimal(str(value if value is not None else 0))
        if not number.is_finite():
            return "0"
        return str(int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    except (InvalidOperation, TypeError, ValueError):
        return "0"


def format_amount(value, *, places=None):
    """Remove display-only trailing zeros while preserving monetary values."""
    try:
        number = Decimal(str(value if value is not None else 0))
        if not number.is_finite():
            return "0"
        if places is not None:
            number = number.quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
        text = format(number, ",f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    except (InvalidOperation, TypeError, ValueError):
        return "0"
