"""Conservative Decimal representation of positive quantity bounds; no lot-size guess."""
from decimal import Context, Decimal, ROUND_FLOOR, localcontext

from app.strategy_lab.numeric import DIVISION_PRECISION, exact_product
from app.risk.canonical import decimal


def floor_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    decimal(numerator, "numerator")
    decimal(denominator, "denominator", positive=True)
    # A recurring quotient cannot be represented exactly as a finite Decimal.
    # Fifty significant digits, always downward, never nearest/binary rounding.
    with localcontext(Context(prec=DIVISION_PRECISION, rounding=ROUND_FLOOR)):
        return numerator / denominator


def floor_increment(quantity: Decimal, increment: Decimal) -> Decimal:
    decimal(quantity, "quantity")
    decimal(increment, "increment", positive=True)
    qn, qd = quantity.as_integer_ratio()
    sn, sd = increment.as_integer_ratio()
    steps = (qn * sd) // (qd * sn)
    return exact_product(Decimal(steps), increment)
