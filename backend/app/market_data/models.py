import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("instrument_id", "provider", "timeframe", "open_time", name="uq_market_candles_identity"),
        CheckConstraint("provider ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_market_candles_provider"),
        CheckConstraint("timeframe IN ('15m','1h','4h','1d')", name="ck_market_candles_timeframe"),
        CheckConstraint('"low" <= "open" AND "low" <= "close" AND "high" >= "open" AND "high" >= "close" AND "high" >= "low"', name="ck_market_candles_ohlc"),
        CheckConstraint("open_time > '1970-01-01 UTC'::timestamptz AND close_time > open_time AND close_time <= retrieved_at", name="ck_market_candles_times"),
        CheckConstraint("EXTRACT(EPOCH FROM open_time) % (CASE timeframe WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END) = 0 AND EXTRACT(EPOCH FROM (close_time - open_time)) * 1000 = (CASE timeframe WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END) * 1000 - 1", name="ck_market_candles_interval"),
        *(CheckConstraint(f'''"{field}" {">=" if field == "volume" else ">"} 0 AND "{field}" NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)''', name="ck_market_candles_" + field)
          for field in ("open", "high", "low", "close", "volume")),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Unconstrained PostgreSQL NUMERIC: no scale coercion or precision loss.
    open: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
