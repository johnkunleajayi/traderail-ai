"""Synthetic contract tests only: no database, market provider or clock dependency."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app.market_intelligence.types import DatasetContext
from app.market_regimes.types import Regime
from app.strategies.configuration import StrategyDefinition
from app.strategy_lab.canonical import canonical_json
from app.risk import (Direction, Environment, KillSwitchScope, MarketRiskContext, OperatingLimits,
    PlatformHardLimits, PortfolioRiskSnapshot, PortfolioScope, PositionRiskSnapshot, RiskCategory,
    RiskConfiguration, RiskDecision, RiskFinding, RiskOutcome, RiskProfile, RiskReason, RiskState,
    TradeIntent, to_wire)
from app.risk.canonical import values

D = Decimal
T = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
ID = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
INSTRUMENT = DatasetContext(UUID(int=1), "BTC/USDT", "BINANCE", "1h")
FRACTIONS = ("max_trade_risk_fraction", "max_open_risk_fraction", "max_gross_exposure_fraction",
    "max_directional_exposure_fraction", "max_instrument_concentration_fraction",
    "max_strategy_concentration_fraction", "max_correlated_exposure_fraction", "max_drawdown_fraction")


def limits(kind=OperatingLimits, **changes):
    # Synthetic contract bounds only; not recommended live settings.
    args = {name: D("0.1") for name in FRACTIONS}
    args.update(max_positions=2, max_approval_age=timedelta(seconds=30))
    args.update(changes)
    return kind(**args)


def scope(environment=Environment.PAPER):
    return PortfolioScope(UUID(int=2), UUID(int=3), environment, "USD")


def market(**changes):
    args = dict(instrument=INSTRUMENT, data_through=T, observed_at=T + timedelta(seconds=1),
        expires_at=T + timedelta(minutes=5), data_identity=ID, intelligence_identity=ID,
        regime_identity=ID, regime=Regime.UNKNOWN)
    args.update(changes)
    return MarketRiskContext(**args)


def intent(**changes):
    args = dict(scope=scope(), instrument=INSTRUMENT, direction=Direction.LONG,
        strategy=StrategyDefinition("EXAMPLE_RESEARCH", "1.0.0", {"threshold": D("0.01")}),
        strategy_decision_identity=ID, strategy_input_identity=ID, decision_at=T,
        reference_price=D("83415.32000000"), invalidation_price=D("82000.00000000"),
        entry_context={"basis": "completed analytical observation", "measurements": [D("0.00100")]},
        market_context=market(), created_at=T + timedelta(seconds=2), expires_at=T + timedelta(minutes=2))
    args.update(changes)
    return TradeIntent(**args)


def snapshot(**changes):
    args = dict(scope=scope(), as_of=T, expires_at=T + timedelta(minutes=3), valuation_identity=ID,
        equity=D("10000.00000000"), available_cash=D("10000.00"), risk_state=RiskState.NORMAL)
    args.update(changes)
    return PortfolioRiskSnapshot(**args)


def config(**changes):
    args = dict(version="test-only-v1", environment=Environment.PAPER, profile=RiskProfile.CONSERVATIVE,
        operating_limits=limits(), platform_hard_limits=limits(PlatformHardLimits))
    args.update(changes)
    return RiskConfiguration(**args)


def decision(**changes):
    args = dict(intent=intent(), configuration=config(), portfolio_snapshot=snapshot(), market_context=market(),
        evaluated_at=T + timedelta(seconds=3), outcome=RiskOutcome.APPROVED,
        findings=(RiskFinding(RiskReason.CONTEXT_CURRENT, {"observed": True}),),
        approval_expires_at=T + timedelta(seconds=25))
    args.update(changes)
    return RiskDecision(**args)


def position(**changes):
    args = dict(position_id=UUID(int=4), instrument_id=INSTRUMENT.instrument_id,
        canonical_symbol="BTC/USDT", direction=Direction.LONG, quantity=D("0.00100"),
        strategy_configuration_identity=ID, mark_price=D("83415.32000000"),
        exposure_amount=None, open_risk_amount=None)
    args.update(changes)
    return PositionRiskSnapshot(**args)


@pytest.mark.parametrize("factory,attribute,value", [
    (intent, "reference_price", D("1")), (decision, "outcome", RiskOutcome.REJECTED),
    (config, "profile", RiskProfile.GROWTH), (limits, "max_positions", 9),
    (snapshot, "equity", D("1")), (market, "regime", Regime.RANGE), (position, "quantity", D("9")),
    (scope, "base_currency", "NGN")])
def test_contracts_are_frozen(factory, attribute, value):
    with pytest.raises((FrozenInstanceError, AttributeError)):
        setattr(factory(), attribute, value)


def test_nested_evidence_is_detached_and_immutable():
    source = {"nested": {"values": [D("1.000")]}}
    row = intent(entry_context=source)
    source["nested"]["values"].append(D("2"))
    assert row.entry_context["nested"]["values"] == (D("1"),)
    with pytest.raises(TypeError):
        row.entry_context["nested"]["new"] = 1
    with pytest.raises(TypeError):
        row.strategy.parameters["threshold"] = D("2")
    detached = to_wire(row)
    detached["entry_context"]["nested"]["values"].append("changed")
    assert len(row.entry_context["nested"]["values"]) == 1


def test_intent_canonical_identity_matches_independent_hash_envelope():
    row = intent()
    payload = {f.name: values(getattr(row, f.name)) for f in fields(row) if f.init}
    expected = "sha256:" + hashlib.sha256(("risk.trade-intent/v1\n" + canonical_json(payload)).encode()).hexdigest()
    assert row.intent_identity == expected == intent().intent_identity


def test_equivalent_decimal_order_and_utc_offset_are_semantically_identical():
    a = intent(entry_context={"a": D("0.00"), "b": [D("1.00")]})
    b = intent(entry_context={"b": [D("1")], "a": D("-0")}, reference_price=D("83415.32"),
        invalidation_price=D("82000"), decision_at=T.astimezone(timezone(timedelta(hours=1))))
    assert a.intent_identity == b.intent_identity
    assert b.decision_at.tzinfo is timezone.utc
    assert config(operating_limits=limits(max_trade_risk_fraction=D("0.1000"))).configuration_identity == config().configuration_identity
    with localcontext() as ctx:
        ctx.prec = 2
        assert intent().intent_identity == intent(reference_price=D("83415.32000000")).intent_identity


@pytest.mark.parametrize("change", [
    {"reference_price": D("83415.32000001")}, {"invalidation_price": D("81999")},
    {"direction": Direction.SHORT, "invalidation_price": D("90000")},
    {"strategy_decision_identity": OTHER}, {"strategy_input_identity": OTHER},
    {"strategy": StrategyDefinition("EXAMPLE_RESEARCH", "1.0.1", {})},
    {"strategy": StrategyDefinition("EXAMPLE_RESEARCH", "1.0.0", {"threshold": D("0.02")})},
    {"scope": replace(scope(), user_id=UUID(int=99))},
    {"scope": replace(scope(), portfolio_id=UUID(int=99))},
    {"scope": scope(Environment.LAB)}, {"scope": replace(scope(), base_currency="NGN")},
    {"entry_context": {"basis": "different evidence"}},
    {"created_at": T + timedelta(seconds=4)}, {"expires_at": T + timedelta(minutes=1)},
    {"market_context": market(data_identity=OTHER)},
])
def test_material_intent_changes_change_identity(change):
    assert intent(**change).intent_identity != intent().intent_identity


@pytest.mark.parametrize("field,value", [("canonical_symbol", "ETH/USDT"), ("instrument_id", UUID(int=77)),
    ("provider", "OTHER"), ("timeframe", "4h")])
def test_canonical_context_is_bound(field, value):
    ctx = replace(INSTRUMENT, **{field: value})
    changed = intent(instrument=ctx, market_context=market(instrument=ctx))
    assert changed.intent_identity != intent().intent_identity
    with pytest.raises(ValueError, match="context"):
        intent(instrument=ctx)


@pytest.mark.parametrize("field", FRACTIONS)
def test_operating_hard_limit_exact_boundary(field):
    assert config().operating_limits == limits()
    with pytest.raises(ValueError, match="hard limits"):
        config(operating_limits=limits(**{field: D("0.100000000000000000000000001")}))
    assert config(operating_limits=limits(**{field: D("0")}))


@pytest.mark.parametrize("bad", [0, 1.0, True, "0.1", D("NaN"), D("Infinity"), D("-0.01"), D("1.00001")])
def test_invalid_limit_financial_values(bad):
    with pytest.raises(ValueError):
        limits(max_trade_risk_fraction=bad)


@pytest.mark.parametrize("change", [{"max_positions": -1}, {"max_positions": True},
    {"max_approval_age": timedelta(0)}, {"max_approval_age": timedelta(seconds=-1)}, {"max_approval_age": 30}])
def test_invalid_limit_counts_and_ages(change):
    with pytest.raises(ValueError):
        limits(**change)


def test_limits_are_distinct_types_and_caps_cannot_be_bypassed():
    with pytest.raises(ValueError):
        config(platform_hard_limits=limits())
    with pytest.raises(ValueError):
        config(operating_limits=limits(PlatformHardLimits))
    for change in ({"max_positions": 3}, {"max_approval_age": timedelta(seconds=31)}):
        with pytest.raises(ValueError, match="hard limits"):
            config(operating_limits=limits(**change))
    assert limits().limits_identity != limits(PlatformHardLimits).limits_identity
    assert config(profile=RiskProfile.BALANCED).configuration_identity != config().configuration_identity
    assert set(RiskProfile) == {RiskProfile.CONSERVATIVE, RiskProfile.BALANCED, RiskProfile.GROWTH}


@pytest.mark.parametrize("environment", list(Environment))
def test_every_environment_has_same_binding_and_expiry_rules(environment):
    s = scope(environment)
    result = decision(intent=intent(scope=s), portfolio_snapshot=snapshot(scope=s),
        configuration=config(environment=environment))
    assert result.intent.scope.environment is environment
    with pytest.raises(ValueError):
        replace(result, approval_expires_at=None)
    with pytest.raises(ValueError):
        replace(result, configuration=config(environment=next(e for e in Environment if e is not environment)))


def test_vocabulary_is_exact_and_no_live_enablement():
    assert {e.value for e in Environment} == {"LAB", "PAPER", "SHADOW", "LIVE"}
    assert {s.value for s in RiskState} == {"NORMAL", "CAUTION", "DEFENSIVE", "SUSPENDED"}
    assert {s.value for s in KillSwitchScope} == {"USER", "PORTFOLIO", "STRATEGY", "MARKET", "DATA", "SYSTEM"}
    assert {s.value for s in RiskOutcome} == {"APPROVED", "REJECTED"}
    for value in ("PENDING", "CONDITIONAL", "APPROVED_FOR_LIVE"):
        with pytest.raises(ValueError):
            RiskOutcome(value)


def test_typed_reason_registry_covers_both_results_for_every_future_gate():
    assert len(RiskCategory) == 14
    for category in RiskCategory:
        assert {r.outcome for r in RiskReason if r.category is category} == set(RiskOutcome)
    with pytest.raises(ValueError):
        RiskFinding("CONTEXT_CURRENT", {})
    finding = RiskFinding(RiskReason.DATA_UNHEALTHY, {"observed": False, "required": True})
    assert finding.category is RiskCategory.DATA_HEALTH
    assert to_wire(finding)["code"] == "DATA_UNHEALTHY"


def test_decision_exact_bindings_and_deterministic_identity():
    a = decision()
    assert a.decision_identity == decision().decision_identity
    assert a.intent_identity == a.intent.intent_identity
    assert a.configuration_identity == a.configuration.configuration_identity
    assert a.snapshot_identity == a.portfolio_snapshot.snapshot_identity
    assert a.market_context_identity == a.market_context.context_identity
    assert a.reason_codes == (RiskReason.CONTEXT_CURRENT,)
    for change in ({"evaluated_at": T + timedelta(seconds=4)},
        {"approval_expires_at": T + timedelta(seconds=24)},
        {"portfolio_snapshot": snapshot(equity=D("9999"))},
        {"market_context": market(data_identity=OTHER)},
        {"configuration": config(version="test-only-v2")},
        {"intent": intent(reference_price=D("83415.33"))},
        {"findings": (RiskFinding(RiskReason.CONTEXT_CURRENT, {"observed": "different"}),)}):
        assert decision(**change).decision_identity != a.decision_identity


def test_reason_order_is_not_material_and_rejection_can_include_successful_gates():
    findings = (RiskFinding(RiskReason.DATA_UNHEALTHY, {"observed": "gap"}),
        RiskFinding(RiskReason.ENVIRONMENT_PERMITTED, {}))
    a = decision(outcome=RiskOutcome.REJECTED, findings=findings, approval_expires_at=None)
    b = replace(a, findings=tuple(reversed(findings)))
    assert a.decision_identity == b.decision_identity
    with pytest.raises(ValueError):
        replace(a, findings=(findings[0], findings[0]))
    with pytest.raises(ValueError):
        replace(a, outcome=RiskOutcome.APPROVED, approval_expires_at=T + timedelta(seconds=20))
    with pytest.raises(ValueError):
        decision(outcome=RiskOutcome.REJECTED, approval_expires_at=None)


@pytest.mark.parametrize("bad", [None, "APPROVED", True])
def test_outcome_requires_typed_enum(bad):
    with pytest.raises(ValueError):
        decision(outcome=bad)


def test_approval_expiry_is_bounded_and_never_indefinite():
    assert decision(approval_expires_at=T + timedelta(seconds=33))
    for expiry in (None, T + timedelta(seconds=3), T + timedelta(seconds=33, microseconds=1)):
        with pytest.raises(ValueError):
            decision(approval_expires_at=expiry)
    for change in ({"intent": intent(expires_at=T + timedelta(seconds=24))},
        {"market_context": market(expires_at=T + timedelta(seconds=24))},
        {"portfolio_snapshot": snapshot(expires_at=T + timedelta(seconds=24))},
        {"intent": intent(market_context=market(expires_at=T + timedelta(seconds=24)))}):
        with pytest.raises(ValueError):
            decision(**change)
    # A stale context must be representable for rejection, but can never be approved.
    args = dict(evaluated_at=T + timedelta(minutes=10), approval_expires_at=None,
        outcome=RiskOutcome.REJECTED, findings=(RiskFinding(RiskReason.CONTEXT_EXPIRED, {}),))
    assert decision(**args).outcome is RiskOutcome.REJECTED
    with pytest.raises(ValueError):
        decision(evaluated_at=args["evaluated_at"], approval_expires_at=T + timedelta(minutes=11))


@pytest.mark.parametrize("factory,field", [(intent, "created_at"), (intent, "expires_at"), (intent, "decision_at"),
    (market, "data_through"), (market, "observed_at"), (market, "expires_at"),
    (snapshot, "as_of"), (snapshot, "expires_at"), (decision, "evaluated_at"), (decision, "approval_expires_at")])
def test_naive_timestamps_rejected(factory, field):
    with pytest.raises(ValueError, match="aware"):
        factory(**{field: T.replace(tzinfo=None)})


@pytest.mark.parametrize("factory,change", [
    (intent, {"created_at": T}), (intent, {"expires_at": T}), (intent, {"decision_at": T + timedelta(hours=1)}),
    (market, {"observed_at": T - timedelta(seconds=1)}), (market, {"expires_at": T}),
    (snapshot, {"expires_at": T}), (decision, {"evaluated_at": T}),
    (decision, {"portfolio_snapshot": snapshot(as_of=T + timedelta(seconds=4))}),
    (decision, {"market_context": market(observed_at=T + timedelta(seconds=4))}),
    (decision, {"portfolio_snapshot": snapshot(scope=replace(scope(), user_id=UUID(int=90)))}),
    (decision, {"market_context": market(instrument=replace(INSTRUMENT, canonical_symbol="ETH/USDT"))})])
def test_invalid_causality_and_scope_rejected(factory, change):
    with pytest.raises(ValueError):
        factory(**change)


@pytest.mark.parametrize("value", [D("0"), D("-1"), D("NaN"), D("Infinity"), 1.0, 1, "1"])
def test_reference_prices_require_exact_positive_decimals(value):
    with pytest.raises(ValueError):
        intent(reference_price=value)


@pytest.mark.parametrize("direction,boundary", [(Direction.LONG, D("83415.32")), (Direction.LONG, D("90000")),
    (Direction.SHORT, D("83415.32")), (Direction.SHORT, D("82000"))])
def test_directional_invalidation_geometry(direction, boundary):
    with pytest.raises(ValueError):
        intent(direction=direction, invalidation_price=boundary)


def test_very_small_prices_and_exact_wire_preservation():
    row = intent(reference_price=D("0.0000000000000000000001234500"), invalidation_price=D("0.00000000000000000000012"))
    wire = to_wire(decision(intent=row))
    assert wire["intent"]["reference_price"] == "0.0000000000000000000001234500"
    assert wire["portfolio_snapshot"]["equity"] == "10000.00000000"
    assert wire["evaluated_at"].endswith("Z")
    assert wire["intent"]["market_context"]["regime"] == "UNKNOWN"
    assert json.loads(json.dumps(wire))["intent"]["invalidation_price"] == "0.00000000000000000000012"


def test_unknown_snapshot_is_not_known_zero_or_flat():
    unknown = snapshot()
    known = snapshot(open_positions=(), open_risk=D("0"), gross_exposure=D("0"),
        instrument_concentration={}, strategy_concentration={}, drawdown_fraction=D("0"))
    assert unknown.snapshot_identity != known.snapshot_identity
    assert to_wire(unknown)["open_positions"] is None
    assert to_wire(known)["open_positions"] == []
    assert to_wire(known)["open_risk"] == "0"


def test_snapshot_sequences_and_maps_are_canonical_detached_inputs():
    a, b = position(), position(position_id=UUID(int=5), direction=Direction.SHORT)
    rows = [a, b]
    concentrations = {"BTC/USDT": D("0.1"), "ETH/USDT": None}
    first = snapshot(open_positions=rows, instrument_concentration=concentrations, strategy_concentration={ID: D("0.2")})
    second = snapshot(open_positions=[b, a], instrument_concentration={"ETH/USDT": None, "BTC/USDT": D("0.100")}, strategy_concentration={ID: D("0.20")})
    assert first.snapshot_identity == second.snapshot_identity
    rows.clear()
    concentrations.clear()
    assert len(first.open_positions) == 2
    with pytest.raises(TypeError):
        first.instrument_concentration["BTC/USDT"] = D("0")
    with pytest.raises(ValueError):
        snapshot(open_positions=(a, a))


@pytest.mark.parametrize("factory,change", [(snapshot, {"equity": None}), (snapshot, {"available_cash": D("-1")}),
    (snapshot, {"gross_exposure": D("NaN")}), (snapshot, {"drawdown_fraction": D("1.01")}),
    (snapshot, {"instrument_concentration": {"bad": D("0.1")}}),
    (snapshot, {"strategy_concentration": {"not an identity": D("0.1")}}),
    (snapshot, {"risk_state": "NORMAL"}), (position, {"quantity": D("0")}),
    (position, {"mark_price": D("0")}), (position, {"direction": "LONG"})])
def test_invalid_snapshot_input(factory, change):
    with pytest.raises(ValueError):
        factory(**change)


@pytest.mark.parametrize("ctx", [replace(INSTRUMENT, canonical_symbol="BTC/BTC"),
    replace(INSTRUMENT, canonical_symbol="btc_usdt"), replace(INSTRUMENT, instrument_id="1"),
    replace(INSTRUMENT, provider=""), replace(INSTRUMENT, timeframe="5m")])
def test_invalid_canonical_context(ctx):
    with pytest.raises(ValueError):
        market(instrument=ctx)


@pytest.mark.parametrize("key", ["orders", "order_id", "execution_authority", "api_key", "quantity", "leverage"])
def test_evidence_cannot_smuggle_execution_or_credentials(key):
    with pytest.raises(ValueError):
        intent(entry_context={"nested": {key: "not allowed"}})
    with pytest.raises(ValueError):
        RiskFinding(RiskReason.CONTEXT_CURRENT, {key: True})


@pytest.mark.parametrize("bad", [1.0, D("NaN"), object(), lambda: None])
def test_evidence_rejects_noncanonical_values(bad):
    with pytest.raises(ValueError):
        intent(entry_context={"nested": [bad]})


def test_no_order_sizing_or_execution_fields_in_proposals_or_decisions():
    forbidden = {"order", "order_id", "quantity", "notional", "venue", "credentials", "execution_authority", "leverage"}
    for kind in (TradeIntent, RiskDecision):
        assert not ({item.name for item in fields(kind)} & forbidden)
        with pytest.raises(TypeError):
            kind(order_id="not permitted")


def test_domain_imports_have_no_database_network_or_runtime_integrations():
    directory = Path(__file__).parents[1] / "app/risk"
    forbidden = ("sqlalchemy", "psycopg", "httpx", "requests", "fastapi", "app.core.database",
        "app.market_data", "app.db", "app.strategy_lab.execution", "app.portfolio.models")
    for path in directory.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            # This explicitly persisted server adapter is not a pure gate/domain
            # contract. Permit only its declared storage imports, never network,
            # HTTP, provider or execution integrations. All other root modules
            # and the public app.risk import retain the original purity checks.
            storage_imports = {"sqlalchemy", "sqlalchemy.dialects.postgresql", "sqlalchemy.exc",
                               "app.db.base", "app.portfolio.models"}
            for name in imports:
                if path.name == "paper_switches.py" and name in storage_imports:
                    continue
                assert not name.startswith(forbidden), (path.name, name)
    script = "import sys; before=set(sys.modules); import app.risk; added=set(sys.modules)-before; assert not any(x in added for x in ['sqlalchemy', 'fastapi', 'httpx', 'app.market_data', 'app.core.database', 'app.db.session']); print('pure risk domain')"
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "pure risk domain"
