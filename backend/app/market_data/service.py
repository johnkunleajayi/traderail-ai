from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.instruments.service import find_instrument
from app.market_data.provider import MarketDataProvider
from app.market_data.schemas import TickerResponse
from app.venues.models import Venue, VenueInstrumentMapping


class InstrumentNotFound(Exception):
    pass


class MappingUnavailable(Exception):
    pass


@dataclass(frozen=True)
class TickerTarget:
    canonical_symbol: str
    quote_currency: str
    venue: str
    venue_symbol: str


def resolve_ticker_target(db: Session, canonical_symbol: str) -> TickerTarget:
    instrument = find_instrument(db, canonical_symbol)
    if instrument is None:
        raise InstrumentNotFound()
    mapping = db.scalar(select(VenueInstrumentMapping).join(Venue).where(
        VenueInstrumentMapping.instrument_id == instrument.id,
        Venue.code == "QUIDAX", Venue.status == "active",
        VenueInstrumentMapping.status == "active",
    ))
    if instrument.status != "active" or mapping is None:
        raise MappingUnavailable()
    return TickerTarget(instrument.canonical_symbol, instrument.quote_asset.symbol,
                        "QUIDAX", mapping.venue_symbol)


def fetch_ticker(target: TickerTarget, provider: MarketDataProvider) -> TickerResponse:
    ticker = provider.fetch_ticker(target.venue_symbol)
    return TickerResponse(**ticker.model_dump(), canonical_symbol=target.canonical_symbol,
                          quote_currency=target.quote_currency, venue=target.venue)
