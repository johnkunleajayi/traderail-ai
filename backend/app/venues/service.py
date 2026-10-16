import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.instruments.models import Instrument
from app.venues.models import Venue, VenueInstrumentMapping


def list_venues(db: Session) -> list[Venue]:
    return list(db.scalars(select(Venue).order_by(Venue.code)).all())


def find_venue(db: Session, code: str) -> Venue | None:
    return db.scalar(select(Venue).where(Venue.code == code))


def list_venue_instruments(db: Session, venue_id: uuid.UUID) -> list[VenueInstrumentMapping]:
    statement = select(VenueInstrumentMapping).where(
        VenueInstrumentMapping.venue_id == venue_id
    ).options(
        joinedload(VenueInstrumentMapping.venue),
        joinedload(VenueInstrumentMapping.instrument).joinedload(Instrument.base_asset),
        joinedload(VenueInstrumentMapping.instrument).joinedload(Instrument.quote_asset),
    ).order_by(VenueInstrumentMapping.venue_symbol)
    return list(db.scalars(statement).all())