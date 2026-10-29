"""Durable candle work metadata; no trading or accounting changes."""
from alembic import op
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, CheckConstraint, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
revision = "0014_paper_mandate_work"
down_revision = "0013_trading_mandates"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("paper_mandate_work",
        Column("work_id", UUID(as_uuid=True), primary_key=True),
        Column("mandate_id", UUID(as_uuid=True), ForeignKey("trading_mandates.id", ondelete="RESTRICT"), nullable=False),
        Column("candle_id", UUID(as_uuid=True), ForeignKey("market_candles.id", ondelete="RESTRICT"), nullable=False),
        Column("runner_version", String(64), nullable=False),
        Column("state", String(32), nullable=False, server_default="PENDING"),
        Column("attempts", Integer, nullable=False, server_default="0"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("last_attempt_at", DateTime(timezone=True)),
        Column("retry_at", DateTime(timezone=True)),
        Column("fresh_until", DateTime(timezone=True), nullable=False),
        Column("outcome_code", String(64)),
        Column("reason_code", String(64)),
        Column("eligibility_identity", String(71)),
        Column("market_evidence_identity", String(71)),
        Column("registry_identity", String(71), ForeignKey("paper_authorizations.registry_identity", ondelete="RESTRICT")),
        Column("request_identity", String(71), ForeignKey("execution_requests.request_identity", ondelete="RESTRICT")),
        Column("position_id", UUID(as_uuid=True), ForeignKey("paper_positions.position_id", ondelete="RESTRICT")),
        UniqueConstraint("mandate_id", "candle_id", name="uq_paper_work_mandate_candle"),
        CheckConstraint("state IN ('PENDING','RETRY_WAIT','COMPLETED','SKIPPED','RECONCILIATION_REQUIRED')", name="ck_paper_work_state"),
        CheckConstraint("attempts >= 0 AND ((attempts = 0 AND last_attempt_at IS NULL) OR (attempts > 0 AND last_attempt_at IS NOT NULL))", name="ck_paper_work_attempts"),
        CheckConstraint("updated_at >= created_at AND fresh_until > created_at AND (last_attempt_at IS NULL OR (last_attempt_at >= created_at AND last_attempt_at <= updated_at))", name="ck_paper_work_times"),
        CheckConstraint("(state = 'RETRY_WAIT' AND retry_at IS NOT NULL AND retry_at >= updated_at AND retry_at < fresh_until) OR (state <> 'RETRY_WAIT' AND retry_at IS NULL)", name="ck_paper_work_retry"),
        CheckConstraint("state <> 'COMPLETED' OR attempts > 0", name="ck_paper_work_completed_attempt"),
        CheckConstraint("state NOT IN ('COMPLETED','SKIPPED','RECONCILIATION_REQUIRED') OR outcome_code IS NOT NULL", name="ck_paper_work_outcome"),
        CheckConstraint("runner_version ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$'", name="ck_paper_work_version"),
        *(CheckConstraint(f"{field} IS NULL OR {field} ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="ck_paper_work_"+field) for field in ("outcome_code", "reason_code")),
    )

    op.create_index("ix_paper_work_due", "paper_mandate_work", ["state", "retry_at", "fresh_until"])
    op.execute("""CREATE FUNCTION traderail_paper_work_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Durable work cannot be deleted'; END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.state <> 'PENDING' OR NEW.attempts <> 0 OR NEW.last_attempt_at IS NOT NULL THEN
                RAISE EXCEPTION 'Work must begin pending and unattempted';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM market_candles c JOIN trading_mandates m
                ON m.id=NEW.mandate_id JOIN instruments i ON i.id=c.instrument_id
                WHERE c.id=NEW.candle_id AND c.provider='BINANCE' AND c.timeframe='1h'
                AND i.canonical_symbol=m.instrument AND m.environment='PAPER'
                AND c.close_time < NEW.created_at AND NEW.created_at <= statement_timestamp()
                AND NEW.fresh_until <= c.open_time + interval '1 hour 10 minutes') THEN
                RAISE EXCEPTION 'Matching completed PAPER candle and bounded freshness required';
            END IF;
        ELSE
            IF OLD.state IN ('COMPLETED','SKIPPED','RECONCILIATION_REQUIRED') THEN
                RAISE EXCEPTION 'Terminal work is immutable';
            END IF;
            IF (NEW.work_id,NEW.mandate_id,NEW.candle_id,NEW.runner_version,NEW.created_at,NEW.fresh_until)
                IS DISTINCT FROM (OLD.work_id,OLD.mandate_id,OLD.candle_id,OLD.runner_version,OLD.created_at,OLD.fresh_until)
                OR NEW.updated_at < OLD.updated_at OR NEW.attempts < OLD.attempts
                OR NEW.attempts > OLD.attempts+1 THEN
                RAISE EXCEPTION 'Work identity, deadline and attempt history are protected';
            END IF;
            IF (NEW.attempts = OLD.attempts AND NEW.last_attempt_at IS DISTINCT FROM OLD.last_attempt_at)
                OR (NEW.attempts = OLD.attempts+1 AND (
                    NEW.state <> 'PENDING' OR NEW.last_attempt_at IS NULL
                    OR NEW.last_attempt_at IS DISTINCT FROM NEW.updated_at
                    OR NEW.updated_at >= OLD.fresh_until
                    OR (OLD.retry_at IS NOT NULL AND NEW.updated_at < OLD.retry_at))) THEN
                RAISE EXCEPTION 'Attempt must be due, fresh and recorded exactly once';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER paper_work_guard BEFORE INSERT OR UPDATE OR DELETE
        ON paper_mandate_work FOR EACH ROW EXECUTE FUNCTION traderail_paper_work_guard()""")

def downgrade():
    op.execute("LOCK TABLE paper_mandate_work IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM paper_mandate_work) THEN
        RAISE EXCEPTION 'Durable runner history prevents downgrade'; END IF; END; $$""")
    op.drop_table("paper_mandate_work")
    op.execute("DROP FUNCTION traderail_paper_work_guard()")
