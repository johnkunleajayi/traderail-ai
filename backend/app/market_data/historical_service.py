from decimal import Decimal
from sqlalchemy.orm import Session
from app.instruments.service import find_instrument
from app.market_data.candle_store import persist_candles
from app.market_data.candle_schemas import CandleResponse, ProviderCandle, Timeframe
from app.market_data.store_schemas import IngestionResult
from app.market_data.service import InstrumentNotFound
from app.market_data.historical_mappings import HistoricalTarget
from app.market_data.provider import HistoricalMarketDataProvider, ProviderUnavailable


def fetch_historical_candles(target: HistoricalTarget, timeframe: Timeframe, limit: int,
                             provider: HistoricalMarketDataProvider) -> list[CandleResponse]:
    candles = provider.fetch_candles(target.provider_symbol, timeframe, limit)
    return [CandleResponse(**candle.model_dump(), canonical_symbol=target.canonical_symbol,
                           provider=target.provider, timeframe=timeframe) for candle in candles]


def ingest_historical_candles(db: Session, target: HistoricalTarget, timeframe: Timeframe, limit: int,
                              provider: HistoricalMarketDataProvider) -> IngestionResult:
    instrument = find_instrument(db, target.canonical_symbol)
    if instrument is None:
        raise InstrumentNotFound()
    try:
        raw = provider.fetch_candles(target.provider_symbol, timeframe, limit)
        if not isinstance(raw, list) or not raw or len(raw) > limit:
            raise ValueError("Invalid provider batch")
        candles = []
        for candle in raw:
            values = {field: getattr(candle, field) for field in ProviderCandle.model_fields}
            if not all(isinstance(values[field], Decimal) for field in ("open", "high", "low", "close", "volume")):
                raise ValueError("Provider financial values must remain Decimal")
            candles.append(CandleResponse(**values, canonical_symbol=target.canonical_symbol,
                                          provider=target.provider, timeframe=timeframe))
    except (AttributeError, TypeError, ValueError):
        raise ProviderUnavailable() from None
    return persist_candles(db, instrument.id, target, timeframe, candles, limit)
