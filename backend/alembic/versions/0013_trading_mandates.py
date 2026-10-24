"""Structured PAPER user intent only; no economic or authorization state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_trading_mandates"
down_revision = "0012_paper_usdt_risk"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("trading_mandates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("environment", sa.String(5), nullable=False, server_default="PAPER"),
        sa.Column("capital_allocation", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("allocation_currency", sa.String(4), nullable=False, server_default="USDT"),
        sa.Column("instrument", sa.String(96), sa.ForeignKey("instruments.canonical_symbol", ondelete="RESTRICT"), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("trading_style", sa.String(16), nullable=False),
        sa.Column("direction_capability", sa.String(16), nullable=False, server_default="LONG_ONLY"),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("environment = 'PAPER'", name="ck_mandate_environment"),
        sa.CheckConstraint("allocation_currency = 'USDT'", name="ck_mandate_currency"),
        sa.CheckConstraint("instrument = 'BTC/USDT'", name="ck_mandate_instrument"),
        sa.CheckConstraint("direction_capability = 'LONG_ONLY'", name="ck_mandate_direction"),
        sa.CheckConstraint("capital_allocation > 0 AND capital_allocation NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)", name="ck_mandate_allocation"),
        sa.CheckConstraint("risk_level IN ('CONSERVATIVE', 'BALANCED', 'GROWTH')", name="ck_mandate_risk_level"),
        sa.CheckConstraint("trading_style IN ('SHORT_TERM', 'MEDIUM_TERM', 'LONGER_TERM')", name="ck_mandate_style"),
        sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'RETIRED')", name="ck_mandate_status"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_mandate_timestamps"),
    )
    op.create_index("ix_trading_mandates_owner_created", "trading_mandates", ["user_id", "created_at", "id"])
    op.execute("""CREATE FUNCTION traderail_mandate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Retire mandates; persisted intent cannot be silently deleted';
        ELSIF TG_OP='INSERT' THEN
            IF NEW.status <> 'DRAFT' THEN RAISE EXCEPTION 'Mandates must begin as DRAFT'; END IF;
            NEW.created_at := statement_timestamp();
            NEW.updated_at := NEW.created_at;
        ELSE
            IF (NEW.id,NEW.user_id,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.user_id,OLD.created_at) THEN
                RAISE EXCEPTION 'Mandate identity and ownership are immutable';
            END IF;
            IF (NEW.environment,NEW.capital_allocation,NEW.allocation_currency,NEW.instrument,
                NEW.risk_level,NEW.trading_style,NEW.direction_capability) IS DISTINCT FROM
               (OLD.environment,OLD.capital_allocation,OLD.allocation_currency,OLD.instrument,
                OLD.risk_level,OLD.trading_style,OLD.direction_capability)
               AND OLD.status NOT IN ('DRAFT','PAUSED') THEN
                RAISE EXCEPTION 'Only DRAFT or PAUSED intent can be edited';
            END IF;
            IF NEW.status <> OLD.status AND NOT (
                (OLD.status='DRAFT' AND NEW.status IN ('ACTIVE','RETIRED')) OR
                (OLD.status='ACTIVE' AND NEW.status IN ('PAUSED','RETIRED')) OR
                (OLD.status='PAUSED' AND NEW.status IN ('ACTIVE','RETIRED'))
            ) THEN RAISE EXCEPTION 'Invalid mandate lifecycle transition'; END IF;
            NEW.updated_at := GREATEST(statement_timestamp(), OLD.updated_at);
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER trading_mandate_guard BEFORE INSERT OR UPDATE OR DELETE
        ON trading_mandates FOR EACH ROW EXECUTE FUNCTION traderail_mandate_guard()""")


def downgrade():
    op.execute("LOCK TABLE trading_mandates IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM trading_mandates) THEN
        RAISE EXCEPTION 'Persisted mandate intent prevents downgrade'; END IF; END; $$""")
    op.drop_table("trading_mandates")
    op.execute("DROP FUNCTION traderail_mandate_guard()")
