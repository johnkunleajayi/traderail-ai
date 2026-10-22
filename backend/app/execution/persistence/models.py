"""PostgreSQL execution audit storage. No generated clocks or financial coercion."""
from sqlalchemy import (Table, Column, String, Text, DateTime, Numeric, CheckConstraint,
                        UniqueConstraint, ForeignKeyConstraint, Index)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db.base import Base


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
    CheckConstraint("(direction='LONG' AND side='BUY' AND action='ENTER_LONG') OR (direction='LONG' AND side='SELL' AND action='EXIT_LONG') OR (direction='SHORT' AND side='SELL' AND action='ENTER_SHORT')", name="ck_exec_request_side"),
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
