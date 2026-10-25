"""Read-only authoritative ownership and instrument-registry adapter."""
from datetime import datetime, timezone
from uuid import UUID
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.instruments.models import Instrument
from app.mandates.domain import MandateError
from app.mandates.service import owned
from .contracts import MandateContext, InstrumentReference
from .policy import POLICY
from .resolver import resolve


def resolution_time() -> datetime:
    return datetime.now(timezone.utc)


def inspect_eligibility(db: Session, user_id: UUID, mandate_id: UUID, *, resolved_at: datetime):
    row = owned(db, user_id, mandate_id)
    try:
        context = MandateContext.model_validate(row)
    except ValidationError:
        raise MandateError("mandate_evidence_invalid", "Saved mandate context is unavailable or invalid") from None
    instrument = db.scalar(select(Instrument).where(Instrument.canonical_symbol == context.instrument))
    try:
        reference = InstrumentReference.model_validate(instrument) if instrument is not None else None
    except ValidationError:
        reference = None
    return resolve(context, POLICY, reference, resolved_at=resolved_at)
