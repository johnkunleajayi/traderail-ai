"""Persisted PAPER controls. Provisioning is explicit; resolution never inserts.

State is durable until explicitly changed. Freshness is a bounded attestation of
a locked database read, not the age of an unchanged inactive control.
"""
from uuid import UUID, uuid5, NAMESPACE_URL
from sqlalchemy import Table, Column, String, Boolean, DateTime, Text, CheckConstraint, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from app.db.base import Base
from app.portfolio.models import PaperPortfolio
from app.instruments.models import Instrument
from app.risk.canonical import require, timestamp, values
from app.risk.intent import TradeIntent
from app.risk.vocabulary import Environment, KillSwitchScope as Scope
from app.risk.safety_gate import (KillSwitchTarget, StrategyTarget, InstrumentTarget, DataTarget,
    KillSwitchInventory, KillSwitchEvidence, SwitchCause)
from app.strategy_lab.canonical import canonical_json, fingerprint

states = Table("paper_kill_switch_states", Base.metadata,
    Column("target_identity", String(71), primary_key=True),
    Column("environment", String(5), nullable=False),
    Column("scope", String(16), nullable=False),
    Column("target_payload", Text, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("cause", String(32), nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("environment='PAPER'", name="ck_paper_switch_environment"),
    CheckConstraint("scope IN ('USER','PORTFOLIO','STRATEGY','MARKET','DATA','SYSTEM')", name="ck_paper_switch_scope"),
    CheckConstraint("cause IN ('MANUAL_CONTROL','DATA_INTEGRITY','STRATEGY_CONTROL','OPERATIONS','EMERGENCY')", name="ck_paper_switch_cause"))


class PaperSwitchUnavailable(RuntimeError):
    pass


def targets_for(intent):
    require(intent, TradeIntent, "trusted intent")
    if intent.scope.environment is not Environment.PAPER:
        raise PaperSwitchUnavailable("PAPER controls only")
    instrument = InstrumentTarget(intent.instrument.instrument_id, intent.instrument.canonical_symbol)
    return tuple(sorted((KillSwitchTarget(Scope.SYSTEM),
        KillSwitchTarget(Scope.USER, user_id=intent.scope.user_id),
        KillSwitchTarget(Scope.PORTFOLIO, portfolio_id=intent.scope.portfolio_id),
        KillSwitchTarget(Scope.STRATEGY, strategy=StrategyTarget(intent.strategy.family, intent.strategy.version, None)),
        KillSwitchTarget(Scope.MARKET, instrument=instrument),
        KillSwitchTarget(Scope.DATA, data=DataTarget(intent.instrument.provider, instrument, intent.instrument.timeframe, None))),
        key=lambda target: target.scope.value))


def _check(c, intent, user_id):
    require(user_id, UUID, "authenticated owner")
    targets = targets_for(intent)
    if (user_id != intent.scope.user_id or c.dialect.name != "postgresql" or not c.in_transaction()
            or c.connection.dbapi_connection.autocommit is not False
            or c.get_isolation_level() != "READ COMMITTED"):
        raise PaperSwitchUnavailable("Trusted PAPER transaction/owner required")
    owner = c.execute(select(PaperPortfolio.id).where(PaperPortfolio.id == intent.scope.portfolio_id,
        PaperPortfolio.user_id == user_id, PaperPortfolio.environment == "PAPER",
        PaperPortfolio.status == "active", PaperPortfolio.base_currency == intent.scope.base_currency)
        .with_for_update(read=True)).scalar_one_or_none()
    market = c.execute(select(Instrument.id).where(Instrument.id == intent.instrument.instrument_id,
        Instrument.canonical_symbol == intent.instrument.canonical_symbol, Instrument.status == "active")
        .with_for_update(read=True)).scalar_one_or_none()
    if owner is None or market is None:
        raise PaperSwitchUnavailable("Eligible PAPER subjects unavailable")
    return targets


def provision_inactive(c, intent, *, user_id, changed_at):
    """Server-only controlled provisioning; never reset an existing active state.

    Requires the caller's authoritative eligible strategy/market intent. No HTTP
    route exposes this function. Caller owns commit/rollback.
    """
    try:
        targets = _check(c, intent, user_id)
        at = timestamp(changed_at)
        for target in targets:
            c.execute(insert(states).values(target_identity=target.target_identity, environment="PAPER",
                scope=target.scope.value, target_payload=canonical_json(values(target)), active=False,
                cause=SwitchCause.OPERATIONS.value, changed_at=at).on_conflict_do_nothing())
    except SQLAlchemyError:
        raise PaperSwitchUnavailable("PAPER switch storage unavailable") from None


def load_inventory(c, intent, *, user_id, observed_at, valid_until):
    """Resolve six exact persisted subjects in one locked read; never infer absence.

    The explicit lease must also satisfy the existing SafetyGatePolicy at use.
    A transaction snapshot cannot attest future observations.
    """
    try:
        targets = _check(c, intent, user_id)
        at, until = timestamp(observed_at), timestamp(valid_until)
        if until <= at:
            raise PaperSwitchUnavailable("Bounded observation required")
        rows = c.execute(select(states).where(states.c.environment == "PAPER",
            states.c.target_identity.in_([t.target_identity for t in targets]))
            .order_by(states.c.target_identity).with_for_update(read=True)).mappings().all()
        by_id = {row["target_identity"]: row for row in rows}
        if len(by_id) != 6:
            raise PaperSwitchUnavailable("Complete persisted switch coverage unavailable")
        switches = []
        for target in targets:
            row = by_id[target.target_identity]
            if (row["scope"] != target.scope.value or row["target_payload"] != canonical_json(values(target))
                    or row["environment"] != "PAPER" or type(row["active"]) is not bool
                    or timestamp(row["changed_at"]) > at):
                raise PaperSwitchUnavailable("Switch attribution/state unavailable")
            source = fingerprint("paper-switch-state/v1", canonical_json(values(dict(row))))
            switches.append(KillSwitchEvidence(uuid5(NAMESPACE_URL, target.target_identity), target,
                Environment.PAPER, row["active"], SwitchCause(row["cause"]),
                row["changed_at"] if row["active"] else None, None, at, until, source))
        source = fingerprint("paper-switch-read/v1", canonical_json([s.source_identity for s in switches]))
        return KillSwitchInventory(intent.intent_identity, intent.scope, intent.market_context.context_identity,
            tuple(Scope), tuple(switches), at, until, source)
    except (SQLAlchemyError, ValueError, KeyError, TypeError):
        raise PaperSwitchUnavailable("PAPER switch evidence unavailable") from None
