"""Persisted user intent. No portfolio ledger or authorization references."""
import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates
from app.db.base import Base

class TradingMandate(Base):
    __tablename__ = "trading_mandates"
    __table_args__ = (
        CheckConstraint("environment = 'PAPER'", name="ck_mandate_environment"),
        CheckConstraint("allocation_currency = 'USDT'", name="ck_mandate_currency"),
        CheckConstraint("instrument = 'BTC/USDT'", name="ck_mandate_instrument"),
        CheckConstraint("direction_capability = 'LONG_ONLY'", name="ck_mandate_direction"),
        CheckConstraint("capital_allocation > 0 AND capital_allocation NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)", name="ck_mandate_allocation"),
        CheckConstraint("risk_level IN ('CONSERVATIVE', 'BALANCED', 'GROWTH')", name="ck_mandate_risk_level"),
        CheckConstraint("trading_style IN ('SHORT_TERM', 'MEDIUM_TERM', 'LONGER_TERM')", name="ck_mandate_style"),
        CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'RETIRED')", name="ck_mandate_status"),
        CheckConstraint("updated_at >= created_at", name="ck_mandate_timestamps"),
        Index("ix_trading_mandates_owner_created", "user_id", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    environment: Mapped[str] = mapped_column(String(5), nullable=False, default="PAPER", server_default="PAPER")
    capital_allocation: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    allocation_currency: Mapped[str] = mapped_column(String(4), nullable=False, default="USDT", server_default="USDT")
    instrument: Mapped[str] = mapped_column(String(96), ForeignKey("instruments.canonical_symbol", ondelete="RESTRICT"), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    trading_style: Mapped[str] = mapped_column(String(16), nullable=False)
    direction_capability: Mapped[str] = mapped_column(String(16), nullable=False, default="LONG_ONLY", server_default="LONG_ONLY")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @validates("user_id")
    def immutable_owner(self, key, value):
        if key in self.__dict__ and self.__dict__[key] != value:
            raise ValueError("Mandate ownership is immutable")
        return value
