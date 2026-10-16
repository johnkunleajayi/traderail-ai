from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.instruments.models import Instrument
from app.venues.models import Venue, VenueInstrumentMapping

SEED_NAMESPACE = UUID("58e451f6-3c0a-4c8b-8f8b-ddfba7c4ce2b")
VENUE_SEEDS = (
    {"id": uuid5(SEED_NAMESPACE, "venue:QUIDAX"), "code": "QUIDAX",
     "name": "Quidax", "venue_type": "CRYPTO_EXCHANGE", "status": "active"},
)
# Exact identifiers verified against the official public catalogue on 2026-10-16.
# USD markets remain unmapped; no currency substitution is permitted.
VERIFIED_MAPPING_SEEDS: tuple[tuple[str, str, str], ...] = (('QUIDAX', 'BTC/USDT', 'btcusdt'), ('QUIDAX', 'ETH/USDT', 'ethusdt'), ('QUIDAX', 'BTC/NGN', 'btcngn'), ('QUIDAX', 'ETH/NGN', 'ethngn'), ('QUIDAX', 'USDT/NGN', 'usdtngn'))


def seed_venues_and_mappings(db: Session) -> None:
    """Caller owns transaction; preserve existing venue and mapping metadata."""
    for values in VENUE_SEEDS:
        db.execute(insert(Venue).values(**values).on_conflict_do_nothing(index_elements=[Venue.code]))
    for code, canonical_symbol, venue_symbol in VERIFIED_MAPPING_SEEDS:
        venue = db.scalar(select(Venue).where(Venue.code == code))
        instrument = db.scalar(select(Instrument).where(Instrument.canonical_symbol == canonical_symbol))
        if venue is None or instrument is None:
            raise ValueError("Verified mapping references missing domain records")
        db.execute(insert(VenueInstrumentMapping).values(
            id=uuid5(SEED_NAMESPACE, "mapping:" + code + ":" + canonical_symbol),
            venue_id=venue.id, instrument_id=instrument.id, venue_symbol=venue_symbol, status="active",
        ).on_conflict_do_nothing(index_elements=[
            VenueInstrumentMapping.venue_id, VenueInstrumentMapping.instrument_id,
        ]))
        # The other unique key intentionally raises on ambiguous venue symbols.


def main():
    from sqlalchemy.exc import SQLAlchemyError
    from app.core.config import get_settings
    from app.db.session import get_engine
    try:
        with Session(get_engine(str(get_settings().database_url))) as db, db.begin():
            seed_venues_and_mappings(db)
    except (SQLAlchemyError, ValueError):
        raise SystemExit("Venue seeding failed; database details suppressed.") from None
    print("Venue reference seeds applied safely; no unverified mappings were added.")


if __name__ == "__main__":
    main()