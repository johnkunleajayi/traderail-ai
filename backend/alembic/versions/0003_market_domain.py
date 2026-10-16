"""Create venue-neutral assets/instruments and deterministic reference seeds."""
from uuid import UUID, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_market_domain"
down_revision = "0002_paper_portfolios"
branch_labels = None
depends_on = None


def upgrade():
    assets = op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", name="uq_assets_symbol"),
        sa.CheckConstraint("symbol ~ '^[A-Z][A-Z0-9]{0,15}$'", name="ck_assets_symbol"),
        sa.CheckConstraint("char_length(trim(name)) > 0", name="ck_assets_name"),
        sa.CheckConstraint("asset_type ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_assets_type"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_assets_status"),
    )
    instruments = op.create_table(
        "instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_symbol", sa.String(96), nullable=False),
        sa.Column("market_type", sa.String(32), nullable=False, server_default="SPOT"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("price_precision", sa.SmallInteger(), nullable=False),
        sa.Column("quantity_precision", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["base_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quote_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("canonical_symbol", name="uq_instruments_canonical_symbol"),
        sa.UniqueConstraint("base_asset_id", "quote_asset_id", "market_type", name="uq_instruments_pair_market"),
        sa.CheckConstraint("base_asset_id <> quote_asset_id", name="ck_instruments_distinct_assets"),
        sa.CheckConstraint("canonical_symbol ~ '^[A-Z][A-Z0-9]{0,15}/[A-Z][A-Z0-9]{0,15}(:[A-Z0-9_-]{1,62})?$'", name="ck_instruments_symbol"),
        sa.CheckConstraint("market_type ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_instruments_market_type"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_instruments_status"),
        sa.CheckConstraint("price_precision BETWEEN 0 AND 18", name="ck_instruments_price_precision"),
        sa.CheckConstraint("quantity_precision BETWEEN 0 AND 18", name="ck_instruments_quantity_precision"),
    )
    # Frozen migration data: do not import mutable application seed definitions.
    namespace = UUID("52eca53b-1223-481d-a52c-896730352a38")
    ids = {}
    for symbol, name, asset_type in (
        ("BTC", "Bitcoin", "CRYPTO"), ("ETH", "Ethereum", "CRYPTO"), ("USD", "US Dollar", "FIAT"),
    ):
        ids[symbol] = uuid5(namespace, "asset:" + symbol)
        op.execute(postgresql.insert(assets).values(
            id=ids[symbol], symbol=symbol, name=name, asset_type=asset_type, status="active",
        ).on_conflict_do_nothing(index_elements=["symbol"]))
    for base in ("BTC", "ETH"):
        canonical = base + "/USD"
        op.execute(postgresql.insert(instruments).values(
            id=uuid5(namespace, "instrument:SPOT:" + canonical),
            base_asset_id=ids[base], quote_asset_id=ids["USD"], canonical_symbol=canonical,
            market_type="SPOT", status="active", price_precision=2, quantity_precision=8,
        ).on_conflict_do_nothing(index_elements=["canonical_symbol"]))


def downgrade():
    op.drop_table("instruments")
    op.drop_table("assets")