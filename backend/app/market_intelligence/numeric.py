from decimal import Context, DivisionByZero, DecimalException, FloatOperation, InvalidOperation, Overflow, Underflow, ROUND_HALF_EVEN, localcontext
from functools import wraps

from app.market_intelligence.config import FEATURE_CONFIG

PRECISION = FEATURE_CONFIG.decimal_precision


def calculation_context():
    return localcontext(Context(prec=PRECISION, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                                traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, FloatOperation]))


def deterministic(function):
    @wraps(function)
    def calculate(*args, **kwargs):
        try:
            with calculation_context():
                return function(*args, **kwargs)
        except DecimalException:
            raise ValueError("Measurement exceeds the supported Decimal numeric range") from None
    return calculate
