"""Decimal-only arithmetic: exact finite sums/products; deterministic division."""
from collections.abc import Iterable
from datetime import timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

DIVISION_PRECISION = 50
ZERO = Decimal("0")
ONE = Decimal("1")


def require_decimal(value: Decimal, name: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if nonnegative and value < ZERO:
        raise ValueError(f"{name} must be nonnegative")
    return value


def exact_sum(values: Iterable[Decimal]) -> Decimal:
    rows = tuple(require_decimal(value, "summand") for value in values)
    if not rows:
        return ZERO
    # Align all finite decimal coefficients and reserve digits for carries.
    exponent = min(value.as_tuple().exponent for value in rows)
    width = max(value.adjusted() for value in rows) - exponent + 1
    precision = max(DIVISION_PRECISION, width + len(str(len(rows))))
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return sum(rows, ZERO)


def difference(left: Decimal, right: Decimal) -> Decimal:
    return exact_sum((left, right.copy_negate()))


def exact_product(left: Decimal, right: Decimal) -> Decimal:
    require_decimal(left, "factor")
    require_decimal(right, "factor")
    precision = max(DIVISION_PRECISION, len(left.as_tuple().digits) + len(right.as_tuple().digits))
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return left * right


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    require_decimal(numerator, "numerator")
    require_decimal(denominator, "denominator")
    if denominator == ZERO:
        return None
    with localcontext(Context(prec=DIVISION_PRECISION, rounding=ROUND_HALF_EVEN)):
        return numerator / denominator


def duration_microseconds(value: timedelta, name: str) -> int:
    if not isinstance(value, timedelta) or value < timedelta(0):
        raise ValueError(f"{name} must be a nonnegative timedelta")
    return (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
