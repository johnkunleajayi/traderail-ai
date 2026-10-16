from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.instruments.models import Instrument
from app.market_data.models import MarketCandle
from app.market_intelligence.engine import calculate_features
from app.market_intelligence.config import MAX_LIMIT
from app.market_intelligence.types import Candle, DatasetContext, FeatureRow, InvalidCandleData, TIMEFRAME_SECONDS


class DatasetInstrumentNotFound(ValueError):
    pass


def calculate_stored_features(db: Session, canonical_symbol: str, provider: str, timeframe: str,
                              *, as_of: datetime, limit: int | None = None) -> list[FeatureRow]:
    """Read all rows, or exactly the latest bounded window; never filter out invalid rows."""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT):
        raise ValueError("Invalid dataset limit")
    if timeframe not in TIMEFRAME_SECONDS:
        raise InvalidCandleData("unsupported_timeframe")
    instrument = db.scalar(select(Instrument).where(Instrument.canonical_symbol == canonical_symbol).execution_options(autoflush=False))
    if instrument is None:
        raise DatasetInstrumentNotFound("Canonical instrument not found")
    context = DatasetContext(instrument.id, instrument.canonical_symbol, provider, timeframe)
    query = select(MarketCandle).where(MarketCandle.instrument_id == instrument.id,
        MarketCandle.provider == provider, MarketCandle.timeframe == timeframe)
    if limit is None:
        query = query.order_by(MarketCandle.open_time)
    else:
        query = query.order_by(MarketCandle.open_time.desc()).limit(limit)
    stored = db.scalars(query.execution_options(autoflush=False)).all()
    if limit is not None:
        stored = list(reversed(stored))
    candles = [Candle(**{field: getattr(row, field) for field in Candle.__dataclass_fields__}) for row in stored]
    return calculate_features(context, candles, as_of=as_of)
