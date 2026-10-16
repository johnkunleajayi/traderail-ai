"""Create virtual-only Paper Portfolios and backfill existing users."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_paper_portfolios"
down_revision = "0001_users_and_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_portfolios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("environment", sa.String(5), nullable=False, server_default="PAPER"),
        sa.Column("base_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("starting_balance", sa.Numeric(20, 2), nullable=False, server_default="10000.00"),
        sa.Column("cash_balance", sa.Numeric(20, 2), nullable=False, server_default="10000.00"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_paper_portfolios_user_id"),
        sa.CheckConstraint("environment = 'PAPER'", name="ck_paper_portfolios_environment"),
        sa.CheckConstraint("base_currency = 'USD'", name="ck_paper_portfolios_currency"),
        sa.CheckConstraint("starting_balance = 10000.00", name="ck_paper_portfolios_starting_balance"),
        sa.CheckConstraint("cash_balance >= 0", name="ck_paper_portfolios_cash_balance"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_paper_portfolios_status"),
    )
    # Existing users receive their system-created portfolio in the same migration.
    op.execute(sa.text("""
        INSERT INTO paper_portfolios (id, user_id)
        SELECT gen_random_uuid(), id FROM users
        ON CONFLICT (user_id) DO NOTHING
    """))


def downgrade():
    op.drop_table("paper_portfolios")