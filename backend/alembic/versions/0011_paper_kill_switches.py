"""Explicit server-owned PAPER controls; absence never means inactive."""
from alembic import op
revision = "0011_paper_kill_switches"
down_revision = "0010_paper_authorizations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE paper_kill_switch_states (
        target_identity VARCHAR(71) PRIMARY KEY,
        environment VARCHAR(5) NOT NULL CONSTRAINT ck_paper_switch_environment CHECK (environment='PAPER'),
        scope VARCHAR(16) NOT NULL CONSTRAINT ck_paper_switch_scope CHECK (scope IN ('USER','PORTFOLIO','STRATEGY','MARKET','DATA','SYSTEM')),
        target_payload TEXT NOT NULL,
        active BOOLEAN NOT NULL,
        cause VARCHAR(32) NOT NULL CONSTRAINT ck_paper_switch_cause CHECK (cause IN ('MANUAL_CONTROL','DATA_INTEGRITY','STRATEGY_CONTROL','OPERATIONS','EMERGENCY')),
        changed_at TIMESTAMPTZ NOT NULL
    )""")
    op.execute("""CREATE FUNCTION traderail_paper_switch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'PAPER safety controls cannot be silently removed'; END IF;
        IF (NEW.target_identity,NEW.environment,NEW.scope,NEW.target_payload)
            IS DISTINCT FROM (OLD.target_identity,OLD.environment,OLD.scope,OLD.target_payload)
            OR NEW.changed_at < OLD.changed_at
            OR ((NEW.active,NEW.cause) IS DISTINCT FROM (OLD.active,OLD.cause) AND NEW.changed_at <= OLD.changed_at)
        THEN RAISE EXCEPTION 'PAPER switch attribution is immutable; changes require advancing timestamps'; END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER paper_switch_guard BEFORE UPDATE OR DELETE ON paper_kill_switch_states
        FOR EACH ROW EXECUTE FUNCTION traderail_paper_switch_guard()""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM paper_kill_switch_states) THEN
        RAISE EXCEPTION 'Persisted safety controls require explicit operational reconciliation';
        END IF; END; $$""")
    op.drop_table("paper_kill_switch_states")
    op.execute("DROP FUNCTION traderail_paper_switch_guard()")
