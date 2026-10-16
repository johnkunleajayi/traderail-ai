"""Create venue references and separate canonical-to-venue mappings."""
from uuid import UUID, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_venues_and_mappings"
down_revision = "0003_market_domain"
branch_labels = None
depends_on = None


def upgrade():
    venues = op.create_table(
        "venues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("venue_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_venues_code"),
        sa.CheckConstraint("code ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_venues_code"),
        sa.CheckConstraint("char_length(trim(name)) > 0", name="ck_venues_name"),
        sa.CheckConstraint("venue_type ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_venues_type"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_venues_status"),
    )
    op.create_table(
        "venue_instrument_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("venue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("venue_symbol", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("venue_id", "instrument_id", name="uq_venue_mappings_instrument"),
        sa.UniqueConstraint("venue_id", "venue_symbol", name="uq_venue_mappings_symbol"),
        sa.CheckConstraint("char_length(venue_symbol) > 0 AND venue_symbol = trim(venue_symbol)", name="ck_venue_mappings_symbol"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_venue_mappings_status"),
    )
    # Frozen reference seed only. Verified catalogue has no BTC/USD or ETH/USD.
    op.execute(postgresql.insert(venues).values(
        id=uuid5(UUID("58e451f6-3c0a-4c8b-8f8b-ddfba7c4ce2b"), "venue:QUIDAX"),
        code="QUIDAX", name="Quidax", venue_type="CRYPTO_EXCHANGE", status="active",
    ).on_conflict_do_nothing(index_elements=["code"]))
    # Deliberately seed zero mappings. Never substitute USD with USDT or NGN.


def downgrade():
    op.drop_table("venue_instrument_mappings")
    op.drop_table("venues")