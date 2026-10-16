"""Expand canonical reference markets and add officially verified Quidax mappings."""
from uuid import UUID, uuid5
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

revision = "0005_expanded_market_mappings"
down_revision = "0004_venues_and_mappings"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    assets = sa.table("assets", sa.column("id", sa.Uuid()), sa.column("symbol", sa.String()),
                      sa.column("name", sa.String()), sa.column("asset_type", sa.String()), sa.column("status", sa.String()))
    instruments = sa.table("instruments", sa.column("id", sa.Uuid()), sa.column("canonical_symbol", sa.String()),
        sa.column("base_asset_id", sa.Uuid()), sa.column("quote_asset_id", sa.Uuid()),
        sa.column("market_type", sa.String()), sa.column("status", sa.String()),
        sa.column("price_precision", sa.SmallInteger()), sa.column("quantity_precision", sa.SmallInteger()))
    venues = sa.table("venues", sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    mappings = sa.table("venue_instrument_mappings", sa.column("id", sa.Uuid()),
        sa.column("venue_id", sa.Uuid()), sa.column("instrument_id", sa.Uuid()),
        sa.column("venue_symbol", sa.String()), sa.column("status", sa.String()))
    namespace = UUID("52eca53b-1223-481d-a52c-896730352a38")
    mapping_namespace = UUID("58e451f6-3c0a-4c8b-8f8b-ddfba7c4ce2b")
    for symbol, name, kind in (("USDT", "Tether", "STABLECOIN"), ("NGN", "Nigerian Naira", "FIAT")):
        bind.execute(insert(assets).values(id=uuid5(namespace,"asset:"+symbol), symbol=symbol,
            name=name, asset_type=kind, status="active").on_conflict_do_nothing(index_elements=["symbol"]))
    asset_ids = dict(bind.execute(sa.select(assets.c.symbol, assets.c.id)).all())
    # Frozen exact catalogue identifiers; no mutable application seed imports.
    for canonical, symbol in (('BTC/USDT', 'btcusdt'), ('ETH/USDT', 'ethusdt'), ('BTC/NGN', 'btcngn'), ('ETH/NGN', 'ethngn'), ('USDT/NGN', 'usdtngn')):
        base, quote = canonical.split("/")
        bind.execute(insert(instruments).values(id=uuid5(namespace,"instrument:SPOT:"+canonical),
            canonical_symbol=canonical, base_asset_id=asset_ids[base], quote_asset_id=asset_ids[quote],
            market_type="SPOT", status="active", price_precision=2, quantity_precision=8
        ).on_conflict_do_nothing(index_elements=["canonical_symbol"]))
        instrument_id = bind.scalar(sa.select(instruments.c.id).where(instruments.c.canonical_symbol==canonical))
        venue_id = bind.scalar(sa.select(venues.c.id).where(venues.c.code=="QUIDAX"))
        bind.execute(insert(mappings).values(id=uuid5(mapping_namespace,"mapping:QUIDAX:"+canonical),
            venue_id=venue_id, instrument_id=instrument_id, venue_symbol=symbol, status="active"
        ).on_conflict_do_nothing(index_elements=["venue_id","instrument_id"]))


def downgrade():
    # Preserve canonical references and mappings: later records may depend on them.
    # An explicit data-removal plan is required instead of silently deleting reference data.
    raise RuntimeError("Reference data downgrade requires an explicit reviewed removal plan")
