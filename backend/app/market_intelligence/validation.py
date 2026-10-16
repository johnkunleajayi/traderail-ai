from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from uuid import UUID
from app.market_intelligence.types import Candle, DatasetContext, InvalidCandleData, TIMEFRAME_SECONDS

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def validate_dataset(context: DatasetContext, candles: Sequence[Candle], as_of: datetime) -> tuple[Candle, ...]:
    if not isinstance(context, DatasetContext):
        raise InvalidCandleData("invalid_context")
    if not aware(as_of):
        raise InvalidCandleData("invalid_as_of")
    if not isinstance(context.timeframe, str) or context.timeframe not in TIMEFRAME_SECONDS:
        raise InvalidCandleData("unsupported_timeframe")
    if (not isinstance(context.instrument_id, UUID) or not isinstance(context.canonical_symbol, str)
        or not re.fullmatch(r"[A-Z][A-Z0-9]{0,15}/[A-Z][A-Z0-9]{0,15}(?::[A-Z0-9_-]{1,62})?", context.canonical_symbol)
        or not isinstance(context.provider, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", context.provider)):
        raise InvalidCandleData("invalid_context")
    duration = timedelta(seconds=TIMEFRAME_SECONDS[context.timeframe])
    previous = None
    rows = tuple(candles)
    for index, candle in enumerate(rows):
        if not isinstance(candle, Candle):
            raise InvalidCandleData("malformed_candle", index)
        if (candle.instrument_id, candle.provider, candle.timeframe) != (context.instrument_id, context.provider, context.timeframe):
            raise InvalidCandleData("mixed_context", index)
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if (any(not isinstance(value, Decimal) or not value.is_finite() for value in values)
                or any(value <= 0 for value in values[:4]) or candle.volume < 0):
            raise InvalidCandleData("invalid_financial_value", index)
        if not (candle.low <= candle.open <= candle.high and candle.low <= candle.close <= candle.high):
            raise InvalidCandleData("impossible_ohlc", index)
        if not all(aware(value) for value in (candle.open_time, candle.close_time, candle.retrieved_at)):
            raise InvalidCandleData("invalid_timestamp", index)
        if (candle.open_time <= EPOCH or (candle.open_time - EPOCH) % duration
                or candle.close_time != candle.open_time + duration - timedelta(milliseconds=1)
                or candle.retrieved_at < candle.close_time):
            raise InvalidCandleData("invalid_interval", index)
        if candle.open_time + duration > as_of:
            raise InvalidCandleData("incomplete_candle", index)
        if previous is not None:
            if candle.open_time == previous:
                raise InvalidCandleData("duplicate_timestamp", index)
            if candle.open_time < previous:
                raise InvalidCandleData("non_chronological", index)
            if candle.open_time != previous + duration:
                raise InvalidCandleData("internal_gap", index)
        previous = candle.open_time
    return rows
