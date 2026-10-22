"""Execution persistence and single-use approval consumption; no economic execution.

Frozen schema snapshot: intentionally independent of application model imports.
"""
from alembic import op
revision = "0007_execution_artifacts"
down_revision = "0006_market_candles"
branch_labels = None
depends_on = None

from sqlalchemy import (Table, Column, String, Text, DateTime, Numeric, CheckConstraint,
                        UniqueConstraint, ForeignKeyConstraint, Index)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from types import SimpleNamespace
from sqlalchemy import MetaData
Base = SimpleNamespace(metadata=MetaData())


def key(name, **kw): return Column(name, String(71), nullable=False, **kw)
def time(name): return Column(name, DateTime(timezone=True), nullable=False)
def number(name, positive=False, signed=False):
    return (Column(name, Numeric(asdecimal=True), nullable=False),
            CheckConstraint(f"{name} NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)" +
                ("" if signed else f" AND {name} {'>' if positive else '>='} 0"), name=f"ck_{name}"))


requests = Table("execution_requests", Base.metadata,
    key("request_identity", primary_key=True), key("action_identity", unique=True),
    key("intent_identity"), key("decision_identity"), key("client_order_identity", unique=True),
    key("order_identity", unique=True), key("payload_identity"),
    Column("environment", String(5), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False), Column("portfolio_id", UUID(as_uuid=True), nullable=False),
    Column("instrument_id", UUID(as_uuid=True), nullable=False), Column("canonical_symbol", String(100), nullable=False),
    Column("provider", String(64), nullable=False), Column("timeframe", String(4), nullable=False),
    Column("direction", String(5), nullable=False), Column("side", String(4), nullable=False),
    Column("action", String(11), nullable=False),
    Column("venue_identity", String(64), nullable=False), *number("quantity", True),
    time("created_at"), time("approval_expires_at"),
    Column("evidence", JSONB, nullable=False), Column("canonical_payload", Text, nullable=False),
    CheckConstraint("environment = 'PAPER'", name="ck_exec_request_paper"),
    CheckConstraint("(direction='LONG' AND side='BUY' AND action='ENTER_LONG') OR (direction='SHORT' AND side='SELL' AND action='ENTER_SHORT')", name="ck_exec_request_side"),
    CheckConstraint("created_at < approval_expires_at", name="ck_exec_request_times"),
    UniqueConstraint("request_identity", "decision_identity", "intent_identity", "action_identity", "environment", "user_id", "portfolio_id", name="uq_exec_request_binding"),
    UniqueConstraint("request_identity", "order_identity", "client_order_identity", name="uq_exec_order_binding"))
Index("ix_exec_requests_portfolio_time", requests.c.portfolio_id, requests.c.created_at)

consumptions = Table("execution_approval_consumptions", Base.metadata,
    key("consumption_identity", primary_key=True), key("decision_identity", unique=True),
    key("request_identity", unique=True), key("intent_identity"), key("action_identity", unique=True),
    key("payload_identity"), Column("environment", String(5), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False), Column("portfolio_id", UUID(as_uuid=True), nullable=False),
    time("consumed_at"), time("approval_expires_at"), Column("canonical_payload", Text, nullable=False),
    CheckConstraint("environment='PAPER' AND consumed_at < approval_expires_at", name="ck_exec_consumption_valid"),
    ForeignKeyConstraint(["request_identity", "decision_identity", "intent_identity", "action_identity", "environment", "user_id", "portfolio_id"],
        ["execution_requests."+n for n in ("request_identity", "decision_identity", "intent_identity", "action_identity", "environment", "user_id", "portfolio_id")],
        ondelete="RESTRICT", name="fk_exec_consumption_binding"))

orders = Table("execution_orders", Base.metadata,
    key("order_identity", primary_key=True), key("request_identity", unique=True), key("client_order_identity", unique=True),
    key("observation_identity"), key("payload_identity"),
    Column("state", String(20), nullable=False), time("observed_at"),
    Column("accepted_at", DateTime(timezone=True)), Column("venue_order_identity", String(256)),
    Column("evidence", JSONB, nullable=False), Column("canonical_payload", Text, nullable=False),
    CheckConstraint("state IN ('CREATED','ACCEPTED','PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED','UNKNOWN')", name="ck_exec_order_state"),
    CheckConstraint("accepted_at IS NULL OR accepted_at <= observed_at", name="ck_exec_order_times"),
    UniqueConstraint("order_identity", "request_identity", name="uq_exec_fill_binding"),
    ForeignKeyConstraint(["request_identity"], ["execution_approval_consumptions.request_identity"], ondelete="RESTRICT", name="fk_exec_order_consumption"),
    ForeignKeyConstraint(["request_identity", "order_identity", "client_order_identity"],
        ["execution_requests."+n for n in ("request_identity", "order_identity", "client_order_identity")],
        ondelete="RESTRICT", name="fk_exec_order_request"))

fills = Table("execution_fills", Base.metadata,
    key("fill_identity", primary_key=True), key("order_identity"), key("request_identity"),
    key("observation_identity", unique=True), key("calculation_identity", unique=True), key("market_evidence_identity"),
    key("payload_identity"), Column("venue_fill_identity", String(256), nullable=False),
    *number("quantity", True), *number("reference_price", True), *number("execution_price", True),
    *number("spread_impact", signed=True), *number("slippage_impact", signed=True),
    *number("reference_notional", True), *number("gross_notional", True), *number("fee"),
    *number("spread_cost"), *number("slippage_cost"), *number("net_cash_effect", signed=True),
    time("filled_at"), Column("evidence", JSONB, nullable=False), Column("canonical_payload", Text, nullable=False),
    UniqueConstraint("order_identity", "venue_fill_identity", name="uq_exec_venue_fill"),
    ForeignKeyConstraint(["order_identity", "request_identity"],
        ["execution_orders.order_identity", "execution_orders.request_identity"], ondelete="RESTRICT", name="fk_exec_fill_order"))

IMMUTABLE_FUNCTION = """
CREATE FUNCTION traderail_execution_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable execution audit record' USING ERRCODE = '23514';
END; $$
"""
ORDER_FUNCTION = """
CREATE FUNCTION traderail_execution_order_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'execution orders cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['state','observed_at','accepted_at','venue_order_identity','observation_identity','payload_identity','canonical_payload','evidence'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['state','observed_at','accepted_at','venue_order_identity','observation_identity','payload_identity','canonical_payload','evidence']) THEN
        RAISE EXCEPTION 'immutable order attribution' USING ERRCODE='23514';
    END IF;
    IF NEW.observed_at < OLD.observed_at
       OR (OLD.accepted_at IS NOT NULL AND NEW.accepted_at IS DISTINCT FROM OLD.accepted_at)
       OR (OLD.venue_order_identity IS NOT NULL AND NEW.venue_order_identity IS DISTINCT FROM OLD.venue_order_identity) THEN
        RAISE EXCEPTION 'order facts cannot be rewritten' USING ERRCODE='23514';
    END IF;
    IF OLD.state IN ('FILLED','CANCELLED','REJECTED') AND NEW.state IS DISTINCT FROM OLD.state THEN
        RAISE EXCEPTION 'terminal order cannot change' USING ERRCODE='23514';
    END IF;
    IF NEW.state <> OLD.state AND NOT (
        (OLD.state='CREATED' AND NEW.state IN ('ACCEPTED','CANCELLED','REJECTED','UNKNOWN')) OR
        (OLD.state='ACCEPTED' AND NEW.state IN ('PARTIALLY_FILLED','FILLED','CANCELLED','UNKNOWN')) OR
        (OLD.state='PARTIALLY_FILLED' AND NEW.state IN ('FILLED','CANCELLED','UNKNOWN')) OR
        (OLD.state='UNKNOWN' AND NEW.state IN ('ACCEPTED','PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED'))
    ) THEN RAISE EXCEPTION 'invalid execution lifecycle transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END; $$
"""
CONSUMPTION_FUNCTION = """
CREATE FUNCTION traderail_execution_consumption_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r execution_requests%ROWTYPE;
BEGIN
    SELECT * INTO STRICT r FROM execution_requests WHERE request_identity=NEW.request_identity;
    IF NEW.consumed_at < r.created_at OR NEW.approval_expires_at <> r.approval_expires_at THEN
        RAISE EXCEPTION 'approval consumption time/context mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END; $$
"""


def upgrade():
    for table in Base.metadata.sorted_tables:
        table.create(op.get_bind())
    for sql in (IMMUTABLE_FUNCTION, ORDER_FUNCTION, CONSUMPTION_FUNCTION):
        op.execute(sql)
    for name in ('execution_requests','execution_approval_consumptions','execution_fills'):
        op.execute(f"CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION traderail_execution_immutable()")
    op.execute("CREATE TRIGGER order_guard BEFORE UPDATE OR DELETE ON execution_orders FOR EACH ROW EXECUTE FUNCTION traderail_execution_order_guard()")
    op.execute("CREATE TRIGGER consumption_guard BEFORE INSERT ON execution_approval_consumptions FOR EACH ROW EXECUTE FUNCTION traderail_execution_consumption_guard()")


def downgrade():
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(op.get_bind())
    for name in ('traderail_execution_consumption_guard','traderail_execution_order_guard','traderail_execution_immutable'):
        op.execute(f"DROP FUNCTION {name}()")
