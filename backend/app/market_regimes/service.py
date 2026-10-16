from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Session
from app.instruments.models import Instrument
from app.instruments.service import reference_query
from app.market_intelligence.service import DatasetInstrumentNotFound, calculate_stored_features
from app.market_intelligence.types import InvalidCandleData
from app.market_regimes.engine import classify_regimes
from app.market_regimes.types import RegimeResult


class StoredRegimesUnavailable(ValueError):
    pass


class IntelligenceUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class StoredRegimes:
    canonical_symbol: str
    base_asset: str
    quote_asset: str
    provider: str
    timeframe: str
    rows: tuple[RegimeResult, ...]


def read_stored_regimes(db: Session, canonical_symbol: str, provider: str, timeframe: str,
                       *, limit: int, as_of: datetime) -> StoredRegimes:
    """Read reference metadata and reuse the exact bounded intelligence/engine pipeline."""
    instrument = db.scalar(reference_query().where(Instrument.canonical_symbol == canonical_symbol)
                           .execution_options(autoflush=False))
    if instrument is None:
        raise DatasetInstrumentNotFound("Canonical instrument not found")
    try:
        features = calculate_stored_features(db, instrument.canonical_symbol, provider, timeframe,
                                            limit=limit, as_of=as_of)
    except (DatasetInstrumentNotFound, InvalidCandleData):
        raise
    except ValueError:
        raise IntelligenceUnavailable("Intelligence calculation unavailable") from None
    if not features:
        raise StoredRegimesUnavailable("No stored candles for this dataset")
    rows = classify_regimes(features, as_of=as_of)
    return StoredRegimes(instrument.canonical_symbol, instrument.base_asset.symbol, instrument.quote_asset.symbol,
                         provider, timeframe, tuple(rows))
