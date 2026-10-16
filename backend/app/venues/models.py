import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.instruments.models import Instrument


class Venue(Base):
    """Reference metadata only; not a user account connection."""
    __tablename__ = "venues"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_venues_code"),
        CheckConstraint("char_length(trim(name)) > 0", name="ck_venues_name"),
        CheckConstraint("venue_type ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_venues_type"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_venues_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    venue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class VenueInstrumentMapping(Base):
    """Exact venue spelling lives here, never on the canonical Instrument."""
    __tablename__ = "venue_instrument_mappings"
    __table_args__ = (
        UniqueConstraint("venue_id", "instrument_id", name="uq_venue_mappings_instrument"),
        UniqueConstraint("venue_id", "venue_symbol", name="uq_venue_mappings_symbol"),
        CheckConstraint("char_length(venue_symbol) > 0 AND venue_symbol = trim(venue_symbol)", name="ck_venue_mappings_symbol"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_venue_mappings_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("venues.id", ondelete="CASCADE"), nullable=False)
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False)
    # Opaque, case-sensitive venue identifier; no shared provider-format assumptions.
    venue_symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    venue: Mapped[Venue] = relationship(Venue, lazy="raise")
    instrument: Mapped[Instrument] = relationship(Instrument, lazy="raise")