"""Controlled USDT PAPER initialization; refuse all active USD conversions."""
from alembic import op
revision = "0012_paper_usdt_risk"
down_revision = "0011_paper_kill_switches"
branch_labels = None
depends_on = None


def upgrade():
    # Lock before examining history. This is replacement of untouched virtual
    # initialization, never a USD/USDT exchange or an economic balance conversion.
    op.execute("LOCK TABLE paper_portfolios IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM paper_portfolios p WHERE p.base_currency='USD' AND (
        p.cash_balance<>10000 OR p.starting_balance<>10000
        OR EXISTS(SELECT 1 FROM paper_positions x WHERE x.portfolio_id=p.id)
        OR EXISTS(SELECT 1 FROM paper_accounting_entries x WHERE x.portfolio_id=p.id)
        OR EXISTS(SELECT 1 FROM paper_authorizations x WHERE x.portfolio_id=p.id)
        OR EXISTS(SELECT 1 FROM execution_requests x WHERE x.portfolio_id=p.id)))
        THEN RAISE EXCEPTION 'Active USD Paper history cannot be redenominated'; END IF; END; $$""")
    op.execute("ALTER TABLE paper_portfolios DROP CONSTRAINT ck_paper_portfolios_currency")
    op.execute("ALTER TABLE paper_portfolios ALTER COLUMN base_currency TYPE VARCHAR(4)")
    op.execute("ALTER TABLE paper_portfolios ALTER COLUMN base_currency SET DEFAULT 'USDT'")
    op.execute("UPDATE paper_portfolios SET base_currency='USDT' WHERE base_currency='USD'")
    op.execute("ALTER TABLE paper_portfolios ADD CONSTRAINT ck_paper_portfolios_currency CHECK (base_currency='USDT')")
    # Preserve existing USD facts if present in a future mixed-history deployment;
    # runtime reconciliation still requires exact portfolio/quote equality.
    op.execute("ALTER TABLE paper_positions DROP CONSTRAINT ck_paper_position_scope")
    op.execute("ALTER TABLE paper_positions ADD CONSTRAINT ck_paper_position_scope CHECK (environment='PAPER' AND direction='LONG' AND currency IN ('USD','USDT'))")
    op.execute("ALTER TABLE paper_accounting_entries DROP CONSTRAINT ck_paper_accounting_scope")
    op.execute("ALTER TABLE paper_accounting_entries ADD CONSTRAINT ck_paper_accounting_scope CHECK (environment='PAPER' AND currency IN ('USD','USDT') AND action IN ('ENTER_LONG','EXIT_LONG'))")
    op.execute("""CREATE TABLE paper_risk_initializations (
        portfolio_id UUID PRIMARY KEY REFERENCES paper_portfolios(id) ON DELETE RESTRICT,
        book_identity VARCHAR(71) NOT NULL,peak_equity NUMERIC NOT NULL,
        previous_state VARCHAR(16) NOT NULL,initialized_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT ck_paper_initial_risk CHECK (peak_equity=10000 AND previous_state='NORMAL'))""")
    op.execute("""CREATE TABLE paper_strategy_activations (
        configuration_identity VARCHAR(71) PRIMARY KEY,family VARCHAR(64) NOT NULL,version VARCHAR(64) NOT NULL,
        environment VARCHAR(5) NOT NULL,lifecycle VARCHAR(16) NOT NULL,authority VARCHAR(64) NOT NULL,
        activated_at TIMESTAMPTZ NOT NULL,CONSTRAINT ck_paper_activation_scope
        CHECK (environment='PAPER' AND lifecycle='PAPER' AND family='ADAPTIVE_TREND' AND version='1.0.0'))""")
    for table in ("paper_risk_initializations","paper_strategy_activations"):
        op.execute("CREATE TRIGGER immutable_paper_activation BEFORE UPDATE OR DELETE ON "+table+
            " FOR EACH ROW EXECUTE FUNCTION traderail_execution_immutable()")


def downgrade():
    op.execute("LOCK TABLE paper_portfolios IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM paper_risk_initializations)
        OR EXISTS(SELECT 1 FROM paper_strategy_activations) OR EXISTS(SELECT 1 FROM paper_authorizations)
        OR EXISTS(SELECT 1 FROM execution_requests) OR EXISTS(SELECT 1 FROM paper_positions)
        OR EXISTS(SELECT 1 FROM paper_accounting_entries)
        OR EXISTS(SELECT 1 FROM paper_portfolios WHERE cash_balance<>10000 OR starting_balance<>10000)
        THEN RAISE EXCEPTION 'Paper activation/economic history prevents downgrade'; END IF; END; $$""")
    op.drop_table("paper_strategy_activations");op.drop_table("paper_risk_initializations")
    op.execute("ALTER TABLE paper_portfolios DROP CONSTRAINT ck_paper_portfolios_currency")
    op.execute("UPDATE paper_portfolios SET base_currency='USD'")
    op.execute("ALTER TABLE paper_portfolios ALTER COLUMN base_currency TYPE VARCHAR(3)")
    op.execute("ALTER TABLE paper_portfolios ALTER COLUMN base_currency SET DEFAULT 'USD'")
    op.execute("ALTER TABLE paper_portfolios ADD CONSTRAINT ck_paper_portfolios_currency CHECK (base_currency='USD')")
    op.execute("ALTER TABLE paper_positions DROP CONSTRAINT ck_paper_position_scope")
    op.execute("ALTER TABLE paper_positions ADD CONSTRAINT ck_paper_position_scope CHECK (environment='PAPER' AND direction='LONG' AND currency='USD')")
    op.execute("ALTER TABLE paper_accounting_entries DROP CONSTRAINT ck_paper_accounting_scope")
    op.execute("ALTER TABLE paper_accounting_entries ADD CONSTRAINT ck_paper_accounting_scope CHECK (environment='PAPER' AND currency='USD' AND action IN ('ENTER_LONG','EXIT_LONG'))")
