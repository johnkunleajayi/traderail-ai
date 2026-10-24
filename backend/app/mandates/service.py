"""Owner-scoped persistence. No strategy, Risk, portfolio or execution calls."""
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.instruments.models import Instrument
from .domain import MandateError, MandateStatus, require_editable, require_transition
from .models import TradingMandate
from .schemas import MandateIntent


def owned(db: Session, user_id: UUID, mandate_id: UUID, *, lock: bool = False) -> TradingMandate:
    query = select(TradingMandate).where(TradingMandate.id == mandate_id, TradingMandate.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise MandateError("mandate_not_found", "Mandate not found", 404)
    return row


def list_owned(db: Session, user_id: UUID) -> list[TradingMandate]:
    return list(db.scalars(select(TradingMandate).where(TradingMandate.user_id == user_id)
                           .order_by(TradingMandate.created_at.desc(), TradingMandate.id.desc())).all())


def require_market(db: Session, intent: MandateIntent) -> None:
    row = db.scalar(select(Instrument).where(Instrument.canonical_symbol == intent.instrument,
                    Instrument.status == "active", Instrument.market_type == "SPOT").with_for_update(read=True))
    if row is None:
        raise MandateError("mandate_market_unavailable", "The canonical mandate market is unavailable")


def create(db: Session, user_id: UUID, intent: MandateIntent) -> TradingMandate:
    require_market(db, intent)
    row = TradingMandate(user_id=user_id, status=MandateStatus.DRAFT, **intent.model_dump())
    db.add(row)
    return row


def update(db: Session, user_id: UUID, mandate_id: UUID, intent: MandateIntent) -> TradingMandate:
    row = owned(db, user_id, mandate_id, lock=True)
    require_editable(row.status)
    require_market(db, intent)
    for key, value in intent.model_dump().items():
        setattr(row, key, value)
    return row


def transition(db: Session, user_id: UUID, mandate_id: UUID, target: MandateStatus) -> TradingMandate:
    row = owned(db, user_id, mandate_id, lock=True)
    require_transition(row.status, target)
    if target == MandateStatus.ACTIVE:
        # Revalidate persisted intent; ACTIVE is structural readiness, not trading permission.
        intent = MandateIntent.model_validate({key: getattr(row, key) for key in MandateIntent.model_fields})
        require_market(db, intent)
    row.status = target
    return row
