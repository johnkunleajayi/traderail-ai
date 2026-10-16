"""Create immutable canonical market candle storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0006_market_candles"
down_revision = "0005_expanded_market_mappings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("market_candles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        *(sa.Column(name, sa.DateTime(timezone=True), nullable=False) for name in ("open_time", "close_time", "retrieved_at")),
        *(sa.Column(name, sa.Numeric(asdecimal=True), nullable=False) for name in ("open", "high", "low", "close", "volume")),
        *(sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()) for name in ("created_at", "updated_at")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("instrument_id", "provider", "timeframe", "open_time", name="uq_market_candles_identity"),
        sa.CheckConstraint("provider ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_market_candles_provider"),
        sa.CheckConstraint("timeframe IN ('15m','1h','4h','1d')", name="ck_market_candles_timeframe"),
        sa.CheckConstraint('"low" <= "open" AND "low" <= "close" AND "high" >= "open" AND "high" >= "close" AND "high" >= "low"', name="ck_market_candles_ohlc"),
        sa.CheckConstraint("open_time > '1970-01-01 UTC'::timestamptz AND close_time > open_time AND close_time <= retrieved_at", name="ck_market_candles_times"),
        sa.CheckConstraint("EXTRACT(EPOCH FROM open_time) % (CASE timeframe WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END) = 0 AND EXTRACT(EPOCH FROM (close_time - open_time)) * 1000 = (CASE timeframe WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END) * 1000 - 1", name="ck_market_candles_interval"),
        *(sa.CheckConstraint(f'''"{name}" {">=" if name == "volume" else ">"} 0 AND "{name}" NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)''', name="ck_market_candles_" + name)
          for name in ("open", "high", "low", "close", "volume")),
    )


def downgrade():
    op.drop_table("market_candles")
