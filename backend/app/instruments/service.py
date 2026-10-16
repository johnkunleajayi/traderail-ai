from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.instruments.models import Instrument


def reference_query():
    return select(Instrument).options(joinedload(Instrument.base_asset), joinedload(Instrument.quote_asset))


def list_instruments(db: Session) -> list[Instrument]:
    return list(db.scalars(reference_query().order_by(Instrument.canonical_symbol)).all())


def find_instrument(db: Session, canonical_symbol: str) -> Instrument | None:
    return db.scalar(reference_query().where(Instrument.canonical_symbol == canonical_symbol))