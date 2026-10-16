from datetime import datetime, timedelta, timezone
from app.market_data.candle_schemas import Timeframe
from app.market_data.store_schemas import CandleGap, DatasetQuality

# Canonical timeframe durations for dataset quality, independent of provider intervals.
INTERVAL_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def analyze_quality(canonical_symbol: str, provider: str, timeframe: Timeframe,
                    intervals: list[tuple[datetime, datetime]], now: datetime) -> DatasetQuality:
    if now.tzinfo is None:
        raise ValueError("Quality evaluation requires an aware timestamp")
    now = now.astimezone(timezone.utc)
    seconds = INTERVAL_SECONDS[timeframe]
    ordered = sorted((start.astimezone(timezone.utc), end.astimezone(timezone.utc)) for start, end in intervals)
    starts = sorted({start for start, _ in ordered})
    gaps = []
    for previous, current in zip(starts, starts[1:]):
        missing = (current - previous) // timedelta(seconds=seconds) - 1
        if missing > 0:
            gaps.append(CandleGap(first_missing_open_time=previous + timedelta(seconds=seconds),
                                  last_missing_open_time=current - timedelta(seconds=seconds), missing_count=missing))
    elapsed = now - EPOCH
    elapsed_seconds = elapsed.days * 86400 + elapsed.seconds
    boundary = EPOCH + timedelta(seconds=(elapsed_seconds // seconds) * seconds)
    expected_close = boundary - timedelta(milliseconds=1)
    latest_close = max((end for _, end in ordered), default=None)
    return DatasetQuality(canonical_symbol=canonical_symbol, provider=provider, timeframe=timeframe,
        candle_count=len(ordered), earliest_open_time=starts[0] if starts else None,
        latest_close_time=latest_close, expected_interval_seconds=seconds,
        duplicate_count=len(ordered) - len(starts), gaps=gaps,
        missing_interval_count=sum(gap.missing_count for gap in gaps),
        freshness="empty" if not ordered else "fresh" if latest_close >= expected_close else "stale",
        expected_latest_close_time=expected_close, evaluated_at=now)
