from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from app.market_intelligence.types import FeatureRow, TIMEFRAME_SECONDS
from app.market_regimes.config import measurement_first_indices


def aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def validate_row(row, index: int, previous: FeatureRow | None, context: tuple | None, as_of: datetime) -> str | None:
    if not isinstance(row, FeatureRow):
        return "MALFORMED_ROW"
    if (not isinstance(row.canonical_symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,15}/[A-Z][A-Z0-9]{0,15}(?::[A-Z0-9_-]{1,62})?", row.canonical_symbol)
        or not isinstance(row.provider, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", row.provider)
        or not isinstance(row.timeframe, str) or row.timeframe not in TIMEFRAME_SECONDS):
        return "INVALID_CONTEXT"
    if context is not None and (row.canonical_symbol, row.provider, row.timeframe) != context:
        return "MIXED_CONTEXT"
    if not all(aware(value) for value in (row.open_time, row.close_time)):
        return "INVALID_TIMESTAMP"
    start = row.open_time.astimezone(timezone.utc)
    end = row.close_time.astimezone(timezone.utc)
    duration = timedelta(seconds=TIMEFRAME_SECONDS[row.timeframe])
    if (start <= datetime(1970, 1, 1, tzinfo=timezone.utc)
        or (start - datetime(1970, 1, 1, tzinfo=timezone.utc)) % duration
        or end != start + duration - timedelta(milliseconds=1)):
        return "INVALID_INTERVAL"
    if start + duration > as_of:
        return "INCOMPLETE_CANDLE"
    if previous is not None:
        previous_start = previous.open_time.astimezone(timezone.utc)
        if start == previous_start:
            return "DUPLICATE_TIMESTAMP"
        if start < previous_start:
            return "NON_CHRONOLOGICAL"
        if start != previous_start + duration:
            return "INTERNAL_GAP"
    first = measurement_first_indices()
    for name in ("close", *first):
        value = getattr(row, name)
        if value is None:
            if name == "close" or index >= first[name]:
                return "MISSING_REQUIRED_MEASUREMENT"
            continue
        if not isinstance(value, Decimal) or not value.is_finite():
            return "INVALID_NUMERIC_VALUE"
        if name in ("close", "ema_20", "ema_50") and value <= 0:
            return "NON_POSITIVE_PRICE"
        if name in ("atr_14", "realized_volatility") and value < 0:
            return "NEGATIVE_VOLATILITY"
    if row.rsi_14 is not None and not 0 <= row.rsi_14 <= 100:
        return "INVALID_RSI"
    if row.simple_return is not None and row.simple_return <= -1:
        return "INVALID_RETURN"
    if all(value is not None for value in (row.macd, row.macd_signal, row.macd_histogram)) and row.macd - row.macd_signal != row.macd_histogram:
        return "INCONSISTENT_MACD"
    return None
