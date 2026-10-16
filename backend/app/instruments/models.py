import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.assets.models import Asset
from app.db.base import Base


class Instrument(Base):
    """Canonical market reference data; venue mappings belong in a separate domain."""
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("base_asset_id", "quote_asset_id", "market_type", name="uq_instruments_pair_market"),
        CheckConstraint("base_asset_id <> quote_asset_id", name="ck_instruments_distinct_assets"),
        CheckConstraint("canonical_symbol ~ '^[A-Z][A-Z0-9]{0,15}/[A-Z][A-Z0-9]{0,15}(:[A-Z0-9_-]{1,62})?$'", name="ck_instruments_symbol"),
        CheckConstraint("market_type ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_instruments_market_type"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_instruments_status"),
        CheckConstraint("price_precision BETWEEN 0 AND 18", name="ck_instruments_price_precision"),
        CheckConstraint("quantity_precision BETWEEN 0 AND 18", name="ck_instruments_quantity_precision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    base_asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    quote_asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    canonical_symbol: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    # No SPOT-only enum/check: adding a market type does not require replacing a DB enum.
    market_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SPOT", server_default="SPOT")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    price_precision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity_precision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    base_asset: Mapped[Asset] = relationship(Asset, foreign_keys=[base_asset_id], lazy="raise")
    quote_asset: Mapped[Asset] = relationship(Asset, foreign_keys=[quote_asset_id], lazy="raise")