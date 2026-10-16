import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaperPortfolio(Base):
    """Virtual-only ledger foundation; future LIVE accounts must use a separate domain."""
    __tablename__ = "paper_portfolios"
    __table_args__ = (
        CheckConstraint("environment = 'PAPER'", name="ck_paper_portfolios_environment"),
        CheckConstraint("base_currency = 'USD'", name="ck_paper_portfolios_currency"),
        CheckConstraint("starting_balance = 10000.00", name="ck_paper_portfolios_starting_balance"),
        CheckConstraint("cash_balance >= 0", name="ck_paper_portfolios_cash_balance"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_paper_portfolios_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    environment: Mapped[str] = mapped_column(String(5), nullable=False, default="PAPER", server_default="PAPER")
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("10000.00"), server_default="10000.00")
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("10000.00"), server_default="10000.00")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())