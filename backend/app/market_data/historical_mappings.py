from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy.orm import Session
from app.instruments.service import find_instrument
from app.market_data.service import InstrumentNotFound

# Research-data configuration only. Not Venue rows or execution eligibility.
HISTORICAL_SYMBOLS = MappingProxyType({
    "BINANCE": MappingProxyType({"BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT"}),
})


class HistoricalMappingUnavailable(Exception):
    pass


@dataclass(frozen=True)
class HistoricalTarget:
    canonical_symbol: str
    provider: str
    provider_symbol: str


def resolve_historical_target(db: Session, canonical_symbol: str, provider: str = "BINANCE") -> HistoricalTarget:
    instrument = find_instrument(db, canonical_symbol)
    if instrument is None:
        raise InstrumentNotFound()
    symbol = HISTORICAL_SYMBOLS.get(provider, {}).get(instrument.canonical_symbol)
    if instrument.status != "active" or not symbol:
        raise HistoricalMappingUnavailable()
    return HistoricalTarget(instrument.canonical_symbol, provider, symbol)
