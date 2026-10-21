"""Hand-calculated risk oracles; synthetic hypotheses, no venue/live-limit claims."""
import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP, localcontext
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app.market_intelligence.types import DatasetContext
from app.market_regimes.types import Regime
from app.risk import (Direction, Environment, MarketRiskContext, OperatingLimits, PlatformHardLimits,
    PortfolioRiskSnapshot, PortfolioScope, RiskConfiguration, RiskDecision, RiskFinding, RiskOutcome,
    RiskProfile, RiskReason, RiskState, TradeIntent, to_wire)
from app.risk.canonical import values
from app.risk.trade_risk import (SIZING_VERSION, SizingAssumptions, SizingConstraint, TradeRiskCalculationError,
    TradeRiskCosts, TradeRiskStatus, TradeSizeConstraints, calculate_trade_size, evaluate_trade_risk, project_size, unit_loss)
from app.risk.trade_risk.arithmetic import floor_increment, floor_ratio
from app.strategies.configuration import StrategyDefinition
from app.strategy_lab.canonical import canonical_json

D = Decimal
T = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
AT = T + timedelta(seconds=3)
ID = "sha256:" + "c" * 64
OTHER = "sha256:" + "d" * 64
INSTRUMENT = DatasetContext(UUID(int=1), "BTC/USDT", "BINANCE", "1h")
COST_FIELDS = ("entry_fee_rate", "exit_fee_rate", "entry_full_spread_rate", "exit_full_spread_rate",
               "entry_slippage_rate", "exit_slippage_rate")


def market(**changes):
    args = dict(instrument=INSTRUMENT, data_through=T, observed_at=T + timedelta(seconds=1),
        expires_at=T + timedelta(minutes=3), data_identity=ID, intelligence_identity=ID,
        regime_identity=ID, regime=Regime.UNKNOWN)
    args.update(changes)
    return MarketRiskContext(**args)


def scope(environment=Environment.PAPER):
    return PortfolioScope(UUID(int=2), UUID(int=3), environment, "USDT")


def intent(direction=Direction.LONG, environment=Environment.PAPER, **changes):
    args = dict(scope=scope(environment), instrument=INSTRUMENT, direction=direction,
        strategy=StrategyDefinition("TEST_RESEARCH", "1.0.0", {}), strategy_decision_identity=ID,
        strategy_input_identity=ID, decision_at=T, reference_price=D("100"),
        invalidation_price=D("90") if direction is Direction.LONG else D("110"),
        entry_context={"basis": "synthetic completed observation"}, market_context=market(),
        created_at=T + timedelta(seconds=2), expires_at=T + timedelta(minutes=2))
    args.update(changes)
    return TradeIntent(**args)


def snapshot(environment=Environment.PAPER, **changes):
    args = dict(scope=scope(environment), as_of=T, expires_at=T + timedelta(minutes=3),
        valuation_identity=ID, equity=D("10000"), available_cash=D("10000"), risk_state=RiskState.NORMAL)
    args.update(changes)
    return PortfolioRiskSnapshot(**args)


def limits(kind=OperatingLimits, **changes):
    args = dict(max_trade_risk_fraction=D("0.005"), max_open_risk_fraction=D("0.1"),
        max_gross_exposure_fraction=D("1"), max_directional_exposure_fraction=D("1"),
        max_instrument_concentration_fraction=D("1"), max_strategy_concentration_fraction=D("1"),
        max_correlated_exposure_fraction=D("1"), max_drawdown_fraction=D("0.1"), max_positions=2,
        max_approval_age=timedelta(seconds=30))
    args.update(changes)
    return kind(**args)


def config(environment=Environment.PAPER, **changes):
    args = dict(version="synthetic-hypothesis-v1", environment=environment, profile=RiskProfile.CONSERVATIVE,
        operating_limits=limits(), platform_hard_limits=limits(PlatformHardLimits))
    args.update(changes)
    return RiskConfiguration(**args)


def costs(**changes):
    args = {name: D("0") for name in COST_FIELDS}
    args.update(changes)
    return TradeRiskCosts(**args)


def combined_costs():
    return costs(entry_fee_rate=D("0.001"), exit_fee_rate=D("0.001"),
        entry_full_spread_rate=D("0.02"), exit_full_spread_rate=D("0.02"),
        entry_slippage_rate=D("0.005"), exit_slippage_rate=D("0.005"))


def constraints(**changes):
    args = dict(platform_max_notional=None, operating_max_notional=None, platform_max_quantity=None,
        operating_max_quantity=None, minimum_quantity=None, quantity_increment=None)
    args.update(changes)
    return TradeSizeConstraints(**args)


def assumptions(**changes):
    args = dict(entry_reference_price=D("100"), entry_context=market(), requested_risk_fraction=D("0.005"),
        costs=costs(), constraints=constraints())
    args.update(changes)
    return SizingAssumptions(**args)


def run(*, proposal=None, configuration=None, portfolio=None, sizing=None, at=AT):
    return evaluate_trade_risk(proposal or intent(), configuration or config(), portfolio or snapshot(), sizing or assumptions(), at)


def codes(result):
    return {finding.code for finding in result.findings}


@pytest.mark.parametrize("direction", list(Direction))
def test_zero_cost_loss_to_invalidation_oracle(direction):
    result = run(proposal=intent(direction))
    s = result.sizing
    assert result.status is TradeRiskStatus.TRADE_RISK_PASS
    assert s.permitted_monetary_risk == D("50")
    assert s.loss_model.adverse_price_loss == D("10")
    assert s.loss_model.total_loss == D("10")
    assert s.raw_max_quantity == s.constrained_max_quantity == D("5")
    assert s.maximum_projection.notional == D("500")
    assert s.maximum_projection.modeled_loss == D("50")
    assert s.maximum_projection.entry_cost == s.maximum_projection.exit_cost == D("0")
    assert s.maximum_projection.required_cash == D("500")


@pytest.mark.parametrize("direction,expected", [
    (Direction.LONG, ("101.5", "88.65", "1", "0.5", "0.1015", "0.9", "0.45", "0.08865", "1.6015", "1.43865", "13.04015", "26.08030", "203.2030")),
    (Direction.SHORT, ("98.5", "111.65", "1", "0.5", "0.0985", "1.1", "0.55", "0.11165", "1.5985", "1.76165", "13.36015", "26.72030", "203.1970")),
])
def test_independent_combined_cost_oracle(direction, expected):
    # Literal arithmetic oracle, not another call to production cost formulas.
    result = run(proposal=intent(direction), sizing=assumptions(costs=combined_costs(),
        constraints=constraints(platform_max_quantity=D("2"))))
    model = result.sizing.loss_model
    names = ("modeled_adverse_entry_price", "modeled_adverse_exit_price", "entry_spread_cost", "entry_slippage_cost",
        "entry_fee", "exit_spread_cost", "exit_slippage_cost", "exit_fee", "total_entry_cost", "total_exit_cost", "total_loss")
    assert tuple(getattr(model, name) for name in names) == tuple(map(D, expected[:11]))
    assert result.sizing.constrained_max_quantity == D("2")
    assert result.evaluated_projection.modeled_loss == D(expected[11])
    assert result.evaluated_projection.required_cash == D(expected[12])
    assert result.evaluated_projection.reference_notional == D("200")
    assert result.evaluated_projection.notional == (D("203") if direction is Direction.LONG else D("200"))
    if direction is Direction.LONG:
        assert result.evaluated_projection.entry_cost == D("3.2030")
        assert result.evaluated_projection.exit_cost == D("2.87730")
    else:
        assert result.evaluated_projection.entry_cost == D("3.1970")
        assert result.evaluated_projection.exit_cost == D("3.52330")


@pytest.mark.parametrize("field,expected", [
    ("entry_fee_rate", "11"), ("exit_fee_rate", "10.9"),
    ("entry_full_spread_rate", "10.5"), ("exit_full_spread_rate", "10.45"),
    ("entry_slippage_rate", "11"), ("exit_slippage_rate", "10.9")])
def test_each_adverse_cost_reduces_raw_safe_quantity(field, expected):
    result = run(sizing=assumptions(costs=costs(**{field: D("0.01")})))
    assert result.sizing.loss_model.total_loss == D(expected)
    assert result.sizing.raw_max_quantity < D("5")
    assert result.sizing.maximum_projection.modeled_loss <= D("50")


def test_strategy_reference_is_distinct_from_risk_entry_assumption():
    s = run(sizing=assumptions(entry_reference_price=D("105"),
        constraints=constraints(quantity_increment=D("0.1")))).sizing
    assert s.strategy_reference_price == D("100")
    assert s.loss_model.assumed_entry_price == D("105")
    assert s.loss_model.invalidation_price == D("90")
    assert s.loss_model.total_loss == D("15")
    assert s.constrained_max_quantity == D("3.3")
    assert s.maximum_projection.modeled_loss == D("49.5")
    assert s.maximum_projection.notional == D("346.5")


@pytest.mark.parametrize("direction,entry", [(Direction.LONG, "90"), (Direction.LONG, "89.999999999999999999"),
    (Direction.SHORT, "110"), (Direction.SHORT, "110.000000000000000001")])
def test_assumed_entry_can_invalidate_an_otherwise_valid_intent(direction, entry):
    result = run(proposal=intent(direction), sizing=assumptions(entry_reference_price=D(entry)))
    assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert result.sizing is None and result.evaluated_projection is None
    assert codes(result) == {RiskReason.INVALID_INVALIDATION_BOUNDARY}


@pytest.mark.parametrize("direction,boundary", [(Direction.LONG, D("100")), (Direction.LONG, D("101")),
    (Direction.SHORT, D("100")), (Direction.SHORT, D("99")), (Direction.LONG, None)])
def test_invalid_intent_boundary_rejected_by_preserved_10a(direction, boundary):
    with pytest.raises(ValueError):
        intent(direction, invalidation_price=boundary)


def test_effective_budget_uses_strictest_request_operating_and_hard_limits():
    c = config(operating_limits=limits(max_trade_risk_fraction=D("0.004")),
        platform_hard_limits=limits(PlatformHardLimits, max_trade_risk_fraction=D("0.01")))
    s = run(configuration=c, sizing=assumptions(requested_risk_fraction=D("0.02"))).sizing
    assert s.effective_risk_fraction == D("0.004")
    assert s.permitted_monetary_risk == D("40")
    assert s.constrained_max_quantity == D("4")
    assert SizingConstraint.OPERATING_RISK_LIMIT in s.binding_constraints
    capped = run(sizing=assumptions(requested_risk_fraction=D("0.02"))).sizing
    assert capped.permitted_monetary_risk == D("50")
    assert SizingConstraint.PLATFORM_RISK_LIMIT in capped.binding_constraints
    reduced = run(sizing=assumptions(requested_risk_fraction=D("0.001"))).sizing
    assert reduced.permitted_monetary_risk == D("10")
    assert reduced.constrained_max_quantity == D("1")
    with pytest.raises(ValueError, match="hard limits"):
        config(operating_limits=limits(max_trade_risk_fraction=D("0.006")))


@pytest.mark.parametrize("direction", list(Direction))
@pytest.mark.parametrize("rate", ["0", "0.00001", "0.01", "0.25"])
def test_cost_aware_quantity_never_exceeds_exact_budget(direction, rate):
    c = TradeRiskCosts(*(D(rate) for _ in COST_FIELDS))
    result = run(proposal=intent(direction), sizing=assumptions(costs=c))
    assert result.status is TradeRiskStatus.TRADE_RISK_PASS
    assert result.sizing.maximum_projection.modeled_loss <= D("50")
    model = result.sizing.loss_model
    with localcontext() as ctx:
        ctx.prec = 150
        if direction is Direction.LONG:
            fill_based_loss = model.modeled_adverse_entry_price - model.modeled_adverse_exit_price
        else:
            fill_based_loss = model.modeled_adverse_exit_price - model.modeled_adverse_entry_price
        assert fill_based_loss + model.entry_fee + model.exit_fee == model.total_loss
        assert model.adverse_price_loss + model.total_entry_cost + model.total_exit_cost == model.total_loss


def test_additional_notional_and_quantity_caps_never_raise_platform_cap():
    s = run(sizing=assumptions(constraints=constraints(platform_max_notional=D("300"), operating_max_notional=D("200")))).sizing
    assert s.notional_limit == D("200")
    assert s.constrained_max_quantity == D("2")
    assert SizingConstraint.NOTIONAL_LIMIT in s.binding_constraints
    assert RiskReason.NOTIONAL_LIMIT_BINDING in codes(run(sizing=assumptions(constraints=constraints(platform_max_notional=D("200")))) )
    c = config(operating_limits=limits(max_gross_exposure_fraction=D("0.01")))
    s = run(configuration=c, sizing=assumptions(constraints=constraints(platform_max_notional=D("9000")))).sizing
    assert s.notional_limit == D("100") and s.constrained_max_quantity == D("1")
    q = run(sizing=assumptions(constraints=constraints(platform_max_quantity=D("3"), operating_max_quantity=D("2")))).sizing
    assert q.constrained_max_quantity == D("2")
    assert SizingConstraint.MAXIMUM_QUANTITY in q.binding_constraints


def test_cash_cap_includes_entry_costs_hand_oracle():
    result = run(portfolio=snapshot(available_cash=D("50")), sizing=assumptions(costs=combined_costs(),
        constraints=constraints(quantity_increment=D("0.01"))))
    assert result.sizing.constrained_max_quantity == D("0.49")
    assert result.sizing.maximum_projection.required_cash == D("49.784735")
    assert result.sizing.maximum_projection.modeled_loss == D("6.3896735")
    assert SizingConstraint.AVAILABLE_CASH in result.sizing.binding_constraints
    assert RiskReason.CASH_LIMIT_BINDING in codes(result)
    # Raw risk-only quantity would need more cash; costs are not ignored in the cash denominator.
    assert result.sizing.raw_max_quantity > D("3")


def test_short_research_uses_collateral_not_reusable_sale_proceeds():
    result = run(proposal=intent(Direction.SHORT), portfolio=snapshot(available_cash=D("50")))
    assert result.sizing.constrained_max_quantity == D("0.5")
    assert result.sizing.maximum_projection.required_cash == D("50")
    zero = run(proposal=intent(Direction.SHORT), portfolio=snapshot(available_cash=D("0")))
    assert zero.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert RiskReason.ZERO_SAFE_QUANTITY in codes(zero)


@pytest.mark.parametrize("portfolio,assumption", [(snapshot(equity=D("0")), assumptions()),
    (snapshot(), assumptions(requested_risk_fraction=D("0")))])
def test_zero_equity_or_budget_is_explicit_rejection(portfolio, assumption):
    result = run(portfolio=portfolio, sizing=assumption)
    assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert result.sizing.permitted_monetary_risk == D("0")
    assert result.sizing.constrained_max_quantity == D("0")
    assert {RiskReason.RISK_BUDGET_UNAVAILABLE, RiskReason.ZERO_SAFE_QUANTITY} <= codes(result)


@pytest.mark.parametrize("field,bad", [("equity", D("-1")), ("equity", None), ("available_cash", None),
    ("available_cash", D("NaN"))])
def test_required_portfolio_values_cannot_be_fabricated(field, bad):
    with pytest.raises(ValueError):
        snapshot(**{field: bad})


@pytest.mark.parametrize("missing", ["intent", "configuration", "snapshot", "assumptions"])
def test_missing_required_contracts_fail_with_typed_error(missing):
    args = dict(intent=intent(), configuration=config(), snapshot=snapshot(), assumptions=assumptions(), evaluated_at=AT)
    args[missing] = None
    with pytest.raises(TradeRiskCalculationError) as error:
        evaluate_trade_risk(**args)
    assert error.value.code is RiskReason.REQUIRED_RISK_DATA_UNAVAILABLE


def test_no_usd_usdt_substitution_or_unknown_fx_fallback():
    s = replace(scope(), base_currency="USD")
    result = run(proposal=intent(scope=s), portfolio=snapshot(scope=s))
    assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert codes(result) == {RiskReason.RISK_CURRENCY_UNSUPPORTED}
    assert result.sizing is None and result.evaluated_projection is None


@pytest.mark.parametrize("quantity_constraints", [constraints(platform_max_quantity=D("0")),
    constraints(platform_max_notional=D("0")), constraints(quantity_increment=D("10")),
    constraints(minimum_quantity=D("10"))])
def test_zero_safe_size_has_no_implicit_approval(quantity_constraints):
    result = run(sizing=assumptions(constraints=quantity_constraints))
    assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert result.sizing.constrained_max_quantity == D("0")
    assert RiskReason.ZERO_SAFE_QUANTITY in codes(result)


@pytest.mark.parametrize("change", [dict(platform_max_notional=D("1"), operating_max_notional=D("1.00001")),
    dict(platform_max_quantity=D("1"), operating_max_quantity=D("2")),
    dict(platform_max_quantity=D("1"), minimum_quantity=D("2")),
    dict(quantity_increment=D("0")), dict(quantity_increment=D("-1")), dict(minimum_quantity=D("0"))])
def test_impossible_or_invalid_constraints_rejected(change):
    with pytest.raises(ValueError):
        constraints(**change)


def test_increment_is_an_actual_step_not_just_decimal_places():
    result = run(sizing=assumptions(constraints=constraints(quantity_increment=D("0.03"))))
    assert result.sizing.constrained_max_quantity == D("4.98")
    assert result.sizing.maximum_projection.modeled_loss == D("49.8")
    assert RiskReason.QUANTITY_INCREMENT_APPLIED in codes(result)
    assert floor_increment(D("1.099"), D("0.05")) == D("1.05")
    assert floor_increment(D("1.10"), D("0.05")) == D("1.10")
    assert floor_increment(D("0.00000000109"), D("0.00000000003")) == D("0.00000000108")


def test_repeating_quantity_division_is_always_down_and_context_independent():
    a = floor_ratio(D("50"), D("3"))
    assert a == D("16." + "6" * 48)
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_UP
        assert floor_ratio(D("50"), D("3")) == a
        first = run(sizing=assumptions(costs=combined_costs()))
    second = run(sizing=assumptions(costs=combined_costs()))
    assert first.evaluation_identity == second.evaluation_identity
    assert first.sizing.calculation_identity == second.sizing.calculation_identity
    with localcontext() as ctx:
        ctx.prec = 150
        assert a * D("3") <= D("50")


@pytest.mark.parametrize("quantity,expected_code", [(D("5.000000000000000001"), RiskReason.TRADE_RISK_LIMIT_EXCEEDED),
    (D("200"), RiskReason.CASH_LIMIT_EXCEEDED)])
def test_oversized_proposal_is_rejected_never_silently_resized(quantity, expected_code):
    result = run(sizing=assumptions(proposed_quantity=quantity))
    assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert result.evaluated_projection.quantity == quantity
    assert result.sizing.constrained_max_quantity == D("5")
    assert expected_code in codes(result)


def test_proposal_notional_quantity_and_step_are_checked_explicitly():
    result = run(sizing=assumptions(proposed_quantity=D("2"), constraints=constraints(platform_max_notional=D("100"))))
    assert RiskReason.NOTIONAL_LIMIT_EXCEEDED in codes(result)
    result = run(sizing=assumptions(proposed_quantity=D("1.01"), constraints=constraints(platform_max_quantity=D("1"))))
    assert RiskReason.QUANTITY_CONSTRAINT_VIOLATED in codes(result)
    result = run(sizing=assumptions(proposed_quantity=D("5"), constraints=constraints(quantity_increment=D("0.03"))))
    assert RiskReason.QUANTITY_CONSTRAINT_VIOLATED in codes(result)
    assert result.evaluated_projection.modeled_loss == D("50")  # budget alone is insufficient
    good = run(sizing=assumptions(proposed_quantity=D("4.98"), constraints=constraints(quantity_increment=D("0.03"))))
    assert good.status is TradeRiskStatus.TRADE_RISK_PASS
    small = run(sizing=assumptions(proposed_quantity=D("0.09"), constraints=constraints(minimum_quantity=D("0.1"))))
    assert small.status is TradeRiskStatus.TRADE_RISK_REJECTED


@pytest.mark.parametrize("field", COST_FIELDS)
@pytest.mark.parametrize("bad", [D("-0.0001"), D("NaN"), D("Infinity"), D("1.01"), 0.0, 0, None])
def test_costs_must_be_explicit_finite_decimal_rates(field, bad):
    with pytest.raises(ValueError):
        costs(**{field: bad})


@pytest.mark.parametrize("field", ["entry_reference_price", "requested_risk_fraction", "proposed_quantity"])
@pytest.mark.parametrize("bad", [D("-1"), D("NaN"), D("Infinity"), 0.5, None])
def test_invalid_assumption_values_never_become_valid_math(field, bad):
    if field == "proposed_quantity" and bad is None:
        assert assumptions(proposed_quantity=None)  # explicit maximum-sizing mode, not an unknown market value
    else:
        with pytest.raises(ValueError):
            assumptions(**{field: bad})


@pytest.mark.parametrize("field", ["entry_reference_price", "proposed_quantity"])
def test_nonpositive_prices_and_proposed_quantities(field):
    with pytest.raises(ValueError):
        assumptions(**{field: D("0")})


def test_cost_adjustments_must_leave_positive_modeled_prices():
    with pytest.raises(ValueError):
        costs(entry_full_spread_rate=D("1"), entry_slippage_rate=D("0.5"))
    with pytest.raises(ValueError):
        costs(exit_full_spread_rate=D("1"), exit_slippage_rate=D("0.5"))


def test_stale_future_or_mismatched_context_cannot_pass_gate():
    for at, code in ((T, RiskReason.RISK_CONTEXT_MISMATCH), (T + timedelta(minutes=2), RiskReason.CONTEXT_EXPIRED)):
        result = run(at=at)
        assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
        assert codes(result) == {code}
    mismatch = run(sizing=assumptions(entry_context=market(instrument=replace(INSTRUMENT, canonical_symbol="ETH/USDT"))))
    assert codes(mismatch) == {RiskReason.RISK_CONTEXT_MISMATCH}
    assert mismatch.sizing is None
    different_scope = run(portfolio=snapshot(scope=replace(scope(), user_id=UUID(int=99))))
    assert codes(different_scope) == {RiskReason.RISK_CONTEXT_MISMATCH}
    with pytest.raises(ValueError, match="aware"):
        run(at=AT.replace(tzinfo=None))


@pytest.mark.parametrize("environment", list(Environment))
def test_all_environment_bindings_and_long_no_execution_authority(environment):
    result = run(proposal=intent(environment=environment), configuration=config(environment), portfolio=snapshot(environment))
    assert result.status is TradeRiskStatus.TRADE_RISK_PASS
    assert not isinstance(result, RiskDecision)
    assert result.scope == "TRADE_RISK_ONLY"
    assert not hasattr(result, "approval_expires_at")
    if environment is not Environment.PAPER:
        wrong = run(configuration=config(environment))
        assert codes(wrong) == {RiskReason.RISK_CONTEXT_MISMATCH}


@pytest.mark.parametrize("environment", list(Environment))
def test_short_is_research_only_with_no_live_short_capability(environment):
    result = run(proposal=intent(Direction.SHORT, environment), configuration=config(environment), portfolio=snapshot(environment))
    if environment in (Environment.LAB, Environment.PAPER):
        assert result.status is TradeRiskStatus.TRADE_RISK_PASS
    else:
        assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
        assert codes(result) == {RiskReason.SHORT_RESEARCH_ONLY}
        assert result.sizing is None


def test_unknown_other_portfolio_gates_are_not_treated_as_global_approval():
    s = snapshot(open_positions=None, gross_exposure=None, open_risk=None, drawdown_fraction=None)
    result = run(portfolio=s)
    assert result.status is TradeRiskStatus.TRADE_RISK_PASS
    assert to_wire(result)["scope"] == "TRADE_RISK_ONLY"
    assert not isinstance(result, RiskDecision)
    assert s.gross_exposure is None and s.open_positions is None
    assert {status.value for status in TradeRiskStatus} == {"TRADE_RISK_PASS", "TRADE_RISK_REJECTED"}


@pytest.mark.parametrize("factory,field,value", [
    (costs, "entry_fee_rate", D("0.1")), (constraints, "quantity_increment", D("1")),
    (assumptions, "requested_risk_fraction", D("0.1")),
    (lambda: run().sizing, "constrained_max_quantity", D("10")),
    (lambda: run(), "status", TradeRiskStatus.TRADE_RISK_REJECTED),
    (lambda: run().sizing.loss_model, "total_loss", D("1")),
    (lambda: run().evaluated_projection, "quantity", D("100"))])
def test_all_sizing_evidence_is_immutable(factory, field, value):
    with pytest.raises((FrozenInstanceError, AttributeError)):
        setattr(factory(), field, value)


def test_equivalent_decimals_timestamps_and_repeated_calls_preserve_identity():
    a = run(sizing=assumptions(costs=costs(entry_fee_rate=D("0.00100")), proposed_quantity=D("2.00")))
    b = run(sizing=assumptions(entry_reference_price=D("100.00000"), requested_risk_fraction=D("0.00500"),
        costs=costs(entry_fee_rate=D("0.001")), proposed_quantity=D("2")), at=AT.astimezone(timezone(timedelta(hours=1))))
    assert a.evaluation_identity == b.evaluation_identity == run(sizing=assumptions(costs=costs(entry_fee_rate=D("0.00100")), proposed_quantity=D("2.00"))).evaluation_identity
    wire = to_wire(a)
    assert wire["evaluated_projection"]["quantity"] == "2.00"
    assert wire["evaluated_at"].endswith("Z")
    assert json.loads(json.dumps(wire))["sizing"]["permitted_monetary_risk"] == "50.000"
    assert a.version == SIZING_VERSION == "1.0.0"


@pytest.mark.parametrize("change", [
    dict(portfolio=snapshot(equity=D("10001"))), dict(portfolio=snapshot(available_cash=D("9999"))),
    dict(sizing=assumptions(entry_reference_price=D("100.01"))),
    dict(proposal=intent(invalidation_price=D("89.99"))),
    dict(sizing=assumptions(requested_risk_fraction=D("0.004"))),
    dict(sizing=assumptions(costs=costs(exit_fee_rate=D("0.001")))),
    dict(sizing=assumptions(constraints=constraints(platform_max_notional=D("200")))),
    dict(sizing=assumptions(constraints=constraints(platform_max_quantity=D("3")))),
    dict(sizing=assumptions(constraints=constraints(quantity_increment=D("0.01")))),
    dict(configuration=config(platform_hard_limits=limits(PlatformHardLimits, max_trade_risk_fraction=D("0.01")))),
    dict(sizing=assumptions(entry_context=market(data_identity=OTHER))),
    dict(sizing=assumptions(proposed_quantity=D("1"))),
])
def test_material_inputs_change_calculation_identity(change):
    assert run(**change).sizing.calculation_identity != run().sizing.calculation_identity


def test_fingerprint_matches_versioned_canonical_envelope():
    result = run().sizing
    payload = {f.name: values(getattr(result, f.name)) for f in fields(result) if f.init}
    expected = "sha256:" + hashlib.sha256(("risk.trade-sizing-1.0.0/v1\n" + canonical_json(payload)).encode()).hexdigest()
    assert expected == result.calculation_identity


def test_tiny_and_high_precision_source_prices_have_no_binary_float_path():
    proposal = intent(reference_price=D("0.00000000000000000100"), invalidation_price=D("0.00000000000000000090"))
    s = run(proposal=proposal, sizing=assumptions(entry_reference_price=D("0.00000000000000000100"))).sizing
    assert s.loss_model.total_loss == D("0.00000000000000000010")
    assert s.maximum_projection.modeled_loss <= D("50")
    assert s.maximum_projection.notional <= D("10000")
    assert to_wire(s)["loss_model"]["assumed_entry_price"] == "0.00000000000000000100"


def test_cash_never_exceeds_equity_even_if_snapshot_cash_is_greater():
    result = run(portfolio=snapshot(equity=D("100"), available_cash=D("1000")),
        proposal=intent(invalidation_price=D("99.999")), sizing=assumptions(costs=combined_costs()))
    assert result.sizing.available_cash_limit == D("100")
    assert result.sizing.maximum_projection.required_cash <= D("100")


def test_no_db_provider_execution_or_float_calculation_dependencies():
    folder = Path(__file__).parents[1] / "app/risk/trade_risk"
    forbidden = ("sqlalchemy", "psycopg", "httpx", "requests", "fastapi", "app.db", "app.market_data",
                 "app.strategy_lab.execution", "app.risk.decision")
    for path in folder.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith(forbidden) for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden)
            if isinstance(node, ast.Constant):
                assert not isinstance(node.value, float)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"float", "RiskDecision"}
    script = "import sys; before=set(sys.modules); import app.risk.trade_risk; added=set(sys.modules)-before; assert not any(x in added for x in ['app.db.session','app.market_data','httpx','sqlalchemy','fastapi']); print('pure trade risk')"
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "pure trade risk"



def test_adverse_entry_notional_is_capped_without_double_counting_cash_costs():
    result = run(sizing=assumptions(costs=combined_costs(), constraints=constraints(
        platform_max_notional=D("200"), quantity_increment=D("0.01"))))
    assert result.sizing.constrained_max_quantity == D("1.97")
    assert result.evaluated_projection.reference_notional == D("197")
    assert result.evaluated_projection.notional == D("199.955")
    assert result.evaluated_projection.entry_cost == D("3.154955")
    assert result.evaluated_projection.required_cash == D("200.154955")
    too_large = run(sizing=assumptions(costs=combined_costs(), proposed_quantity=D("2"),
        constraints=constraints(platform_max_notional=D("200"))))
    assert too_large.evaluated_projection.reference_notional == D("200")
    assert too_large.evaluated_projection.notional == D("203")
    assert RiskReason.NOTIONAL_LIMIT_EXCEEDED in codes(too_large)


def test_trade_pass_contract_itself_rejects_contradictory_projection():
    result = run()
    too_large = project_size(D("6"), result.sizing.loss_model)
    with pytest.raises(ValueError, match="bounds"):
        replace(result, evaluated_projection=too_large)


@pytest.mark.parametrize("field", ["entry_context", "costs", "constraints"])
def test_missing_assumption_contract_never_means_default_zero(field):
    with pytest.raises(ValueError):
        assumptions(**{field: None})


def test_risk_budget_and_cash_exact_boundary_and_immediately_adjacent_values():
    exact = run(sizing=assumptions(proposed_quantity=D("5")))
    assert exact.status is TradeRiskStatus.TRADE_RISK_PASS
    adjacent = run(sizing=assumptions(proposed_quantity=D("5.000000000000000000000000000001")))
    assert adjacent.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert RiskReason.TRADE_RISK_LIMIT_EXCEEDED in codes(adjacent)
    cash = run(portfolio=snapshot(available_cash=D("100")), sizing=assumptions(proposed_quantity=D("1")))
    assert cash.status is TradeRiskStatus.TRADE_RISK_PASS
    cash_above = run(portfolio=snapshot(available_cash=D("100")),
        sizing=assumptions(proposed_quantity=D("1.000000000000000000000000000001")))
    assert RiskReason.CASH_LIMIT_EXCEEDED in codes(cash_above)


def test_numeric_failure_is_a_controlled_rejection_not_infinity():
    # Valid finite inputs that exceed the established exact-product context range.
    proposal = intent(reference_price=D("9.99e999999"), invalidation_price=D("9e999999"))
    result = run(proposal=proposal, sizing=assumptions(entry_reference_price=D("9.99e999999"), costs=combined_costs()))
    assert result.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert result.sizing is None
    assert RiskReason.NUMERIC_RISK_CALCULATION_UNAVAILABLE in codes(result)



@pytest.mark.parametrize("field,expected", [("entry_fee_rate", "11"), ("exit_fee_rate", "11.1"),
    ("entry_full_spread_rate", "10.5"), ("exit_full_spread_rate", "10.55"),
    ("entry_slippage_rate", "11"), ("exit_slippage_rate", "11.1")])
def test_short_each_cost_side_independent_oracle(field, expected):
    result = run(proposal=intent(Direction.SHORT), sizing=assumptions(costs=costs(**{field: D("0.01")})))
    assert result.sizing.loss_model.total_loss == D(expected)
    assert result.sizing.constrained_max_quantity < D("5")
    assert result.sizing.maximum_projection.modeled_loss <= D("50")


def test_requested_risk_zero_and_quantity_minimum_boundary_are_exact():
    rejected = run(configuration=config(operating_limits=limits(max_trade_risk_fraction=D("0"))))
    assert rejected.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert RiskReason.RISK_BUDGET_UNAVAILABLE in codes(rejected)
    assert run(sizing=assumptions(constraints=constraints(minimum_quantity=D("5")))).status is TradeRiskStatus.TRADE_RISK_PASS
    too_large = run(sizing=assumptions(constraints=constraints(minimum_quantity=D("5.000000000000000001"))))
    assert too_large.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert too_large.sizing.constrained_max_quantity == D("0")
