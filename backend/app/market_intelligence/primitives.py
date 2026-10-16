from collections.abc import Sequence
from decimal import Decimal
from app.market_intelligence.numeric import deterministic
from app.market_intelligence.config import FEATURE_CONFIG


def validate_period(period: int, minimum: int = 1):
    if isinstance(period, bool) or not isinstance(period, int) or period < minimum:
        raise ValueError("Invalid measurement period")


def validate_values(values: Sequence[Decimal], positive: bool = False):
    if any(not isinstance(value, Decimal) or not value.is_finite() or (positive and value <= 0) for value in values):
        raise ValueError("Finite Decimal values required")


@deterministic
def ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    validate_period(period)
    validate_values(values)
    result = [None] * len(values)
    if len(values) < period:
        return result
    average = sum(values[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = average
    alpha = Decimal(2) / Decimal(period + 1)
    for index in range(period, len(values)):
        average = alpha * values[index] + (Decimal(1) - alpha) * average
        result[index] = average
    return result


def rsi_value(gain: Decimal, loss: Decimal) -> Decimal:
    if not gain and not loss:
        return Decimal(50)  # Explicit neutral measurement convention for an entirely flat window.
    if not loss:
        return Decimal(100)
    if not gain:
        return Decimal(0)
    return Decimal(100) - Decimal(100) / (Decimal(1) + gain / loss)


@deterministic
def rsi(closes: Sequence[Decimal], period: int = FEATURE_CONFIG.rsi_period) -> list[Decimal | None]:
    validate_period(period)
    validate_values(closes, positive=True)
    result = [None] * len(closes)
    if len(closes) <= period:
        return result
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, Decimal(0)) for change in changes]
    losses = [max(-change, Decimal(0)) for change in changes]
    gain = sum(gains[:period], Decimal(0)) / Decimal(period)
    loss = sum(losses[:period], Decimal(0)) / Decimal(period)
    result[period] = rsi_value(gain, loss)
    for index in range(period + 1, len(closes)):
        gain = (gain * Decimal(period - 1) + gains[index - 1]) / Decimal(period)
        loss = (loss * Decimal(period - 1) + losses[index - 1]) / Decimal(period)
        result[index] = rsi_value(gain, loss)
    return result


@deterministic
def true_ranges(highs: Sequence[Decimal], lows: Sequence[Decimal], closes: Sequence[Decimal]) -> list[Decimal]:
    if len(highs) != len(lows) or len(lows) != len(closes):
        raise ValueError("OHLC lengths differ")
    for values in (highs, lows, closes):
        validate_values(values, positive=True)
    if any(not low <= close <= high for high, low, close in zip(highs, lows, closes)):
        raise ValueError("Impossible OHLC relationships")
    return [high - low if index == 0 else max(high - low, abs(high - closes[index - 1]), abs(low - closes[index - 1]))
            for index, (high, low) in enumerate(zip(highs, lows))]


@deterministic
def atr(highs: Sequence[Decimal], lows: Sequence[Decimal], closes: Sequence[Decimal], period: int = FEATURE_CONFIG.atr_period) -> list[Decimal | None]:
    validate_period(period)
    ranges = true_ranges(highs, lows, closes)
    result = [None] * len(closes)
    if len(closes) < period:
        return result
    value = sum(ranges[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = value
    for index in range(period, len(closes)):
        value = (value * Decimal(period - 1) + ranges[index]) / Decimal(period)
        result[index] = value
    return result


@deterministic
def macd(closes: Sequence[Decimal]) -> tuple[list[Decimal | None], list[Decimal | None], list[Decimal | None]]:
    validate_values(closes, positive=True)
    fast, slow = ema(closes, FEATURE_CONFIG.macd_fast_period), ema(closes, FEATURE_CONFIG.macd_slow_period)
    line = [None if left is None or right is None else left - right for left, right in zip(fast, slow)]
    # Compact only the leading warm-up; no internal holes or market gaps are accepted by the engine.
    available = [value for value in line if value is not None]
    signal = [None] * (len(line) - len(available)) + ema(available, FEATURE_CONFIG.macd_signal_period)
    histogram = [None if left is None or right is None else left - right for left, right in zip(line, signal)]
    return line, signal, histogram


@deterministic
def returns(closes: Sequence[Decimal]) -> tuple[list[Decimal | None], list[Decimal | None]]:
    validate_values(closes, positive=True)
    if not closes:
        return [], []
    simple, logarithmic = [None], [None]
    for previous, current in zip(closes, closes[1:]):
        ratio = current / previous
        simple.append(ratio - Decimal(1))
        logarithmic.append(ratio.ln())
    return simple, logarithmic


@deterministic
def realized_volatility(closes: Sequence[Decimal], window: int = FEATURE_CONFIG.realized_volatility_window) -> list[Decimal | None]:
    validate_period(window, minimum=2)
    _, logarithmic = returns(closes)
    result = [None] * len(closes)
    for index in range(window, len(closes)):
        values = logarithmic[index - window + 1:index + 1]
        offsets = [value - values[0] for value in values]
        mean_offset = sum(offsets, Decimal(0)) / Decimal(window)
        variance = sum(((value - mean_offset) ** 2 for value in offsets), Decimal(0)) / Decimal(window)
        result[index] = variance.sqrt()
    return result
