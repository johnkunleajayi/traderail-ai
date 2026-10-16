from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.market_data.candle_schemas import CandleResponse, Timeframe
from app.market_data.historical_mappings import HistoricalTarget
from app.market_data.models import MarketCandle
from app.market_data.provider import ProviderUnavailable
from app.market_data.quality import INTERVAL_SECONDS, analyze_quality
from app.market_data.store_schemas import IngestionResult

FINANCIAL_FIELDS = ("open", "high", "low", "close", "volume")


class CandleIntegrityConflict(Exception):
    def __init__(self, open_time):
        self.open_time = open_time


def validated_batch(candles: list[CandleResponse], target: HistoricalTarget,
                    timeframe: Timeframe, limit: int) -> list[CandleResponse]:
    try:
        if not candles or len(candles) > limit:
            raise ValueError("Invalid batch")
        seconds = INTERVAL_SECONDS[timeframe]
        seen, validated = set(), []
        now = datetime.now(timezone.utc)
        for candle in candles:
            if not all(isinstance(getattr(candle, field), Decimal) for field in FINANCIAL_FIELDS):
                raise ValueError("Financial values must remain Decimal")
            item = CandleResponse.model_validate({field: getattr(candle, field) for field in CandleResponse.model_fields})
            if (item.canonical_symbol != target.canonical_symbol or item.provider != target.provider
                    or item.timeframe != timeframe or item.open_time in seen):
                raise ValueError("Candle identity mismatch")
            elapsed = item.open_time - datetime(1970, 1, 1, tzinfo=timezone.utc)
            if ((elapsed.days * 86400 + elapsed.seconds) % seconds
                    or item.open_time.microsecond or item.close_time != item.open_time + timedelta(seconds=seconds) - timedelta(milliseconds=1)
                    or item.close_time > now):
                raise ValueError("Invalid completed interval")
            seen.add(item.open_time)
            validated.append(item)
        return sorted(validated, key=lambda candle: candle.open_time)
    except (AttributeError, TypeError, ValueError, KeyError):
        raise ProviderUnavailable() from None


def persist_candles(db: Session, instrument_id, target: HistoricalTarget, timeframe: Timeframe,
                    candles: list[CandleResponse], limit: int) -> IngestionResult:
    """Caller owns transaction; any conflict requires rollback of the whole batch."""
    batch = validated_batch(candles, target, timeframe, limit)
    inserted = unchanged = 0
    for candle in batch:
        values = {field: getattr(candle, field) for field in (*FINANCIAL_FIELDS, "open_time", "close_time", "retrieved_at")}
        result = db.scalar(insert(MarketCandle).values(instrument_id=instrument_id,
            provider=target.provider, timeframe=timeframe, **values).on_conflict_do_nothing(
                constraint="uq_market_candles_identity").returning(MarketCandle.id))
        if result is not None:
            inserted += 1
            continue
        existing = db.scalar(select(MarketCandle).where(MarketCandle.instrument_id == instrument_id,
            MarketCandle.provider == target.provider, MarketCandle.timeframe == timeframe,
            MarketCandle.open_time == candle.open_time))
        if existing is None or any(getattr(existing, field) != getattr(candle, field)
                                   for field in (*FINANCIAL_FIELDS, "close_time")):
            raise CandleIntegrityConflict(candle.open_time)
        unchanged += 1
    return IngestionResult(canonical_symbol=target.canonical_symbol, provider=target.provider,
                           timeframe=timeframe, fetched=len(batch), inserted=inserted, unchanged=unchanged)


def stored_candles(db: Session, instrument_id, canonical_symbol: str, provider: str, timeframe: Timeframe, limit: int):
    # Latest bounded window, returned in chronological order; quality covers all stored rows.
    rows = list(db.scalars(select(MarketCandle).where(MarketCandle.instrument_id == instrument_id,
        MarketCandle.provider == provider, MarketCandle.timeframe == timeframe)
        .order_by(MarketCandle.open_time.desc()).limit(limit)).all())
    return [CandleResponse(**{field: getattr(row, field) for field in CandleResponse.model_fields
                             if field != "canonical_symbol"}, canonical_symbol=canonical_symbol)
            for row in reversed(rows)]


def dataset_quality(db: Session, instrument_id, canonical_symbol: str, provider: str, timeframe: Timeframe):
    rows = db.execute(select(MarketCandle.open_time, MarketCandle.close_time).where(
        MarketCandle.instrument_id == instrument_id, MarketCandle.provider == provider,
        MarketCandle.timeframe == timeframe).order_by(MarketCandle.open_time)).all()
    return analyze_quality(canonical_symbol, provider, timeframe, rows, datetime.now(timezone.utc))
