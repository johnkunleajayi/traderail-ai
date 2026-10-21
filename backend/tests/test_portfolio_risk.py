"""Synthetic portfolio oracles, not production thresholds or empirical correlations."""
import ast
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from decimal import Decimal, localcontext
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app.risk import Direction, Environment, PositionRiskSnapshot, RiskDecision, RiskProfile, RiskReason, to_wire
from app.risk.trade_risk import evaluate_trade_risk, TradeRiskStatus
from app.risk.portfolio_risk import (CorrelationEvidence, ExposureGroup, PortfolioLimitKind as K,
    PortfolioRiskContext, PortfolioRiskInputError, PortfolioRiskPolicy, PortfolioRiskStatus as S,
    StrategyAttribution, evaluate_portfolio_risk)
from app.strategies.configuration import StrategyDefinition
from test_trade_risk_sizing import (AT, ID, OTHER, T, assumptions, combined_costs, config, intent, limits,
                                    market, scope, snapshot, PlatformHardLimits)

D = Decimal


def policy(**changes):
    args = dict(version="synthetic-only-v1", platform_correlation_threshold=D("0.8"),
        operating_correlation_threshold=D("0.8"), platform_correlation_max_age=timedelta(hours=2),
        operating_correlation_max_age=timedelta(hours=1))
    args.update(changes)
    return PortfolioRiskPolicy(**args)


def attribution(configuration=None, family="TEST_RESEARCH", version="1.0.0", **changes):
    args = dict(configuration_identity=configuration or intent().strategy.configuration_identity,
                family=family, version=version, source_identity=ID)
    args.update(changes)
    return StrategyAttribution(**args)


def position(symbol="BTC/USDT", exposure="1000", risk="20", direction=Direction.LONG, number=10, **changes):
    ids = {"BTC/USDT": 1, "ETH/USDT": 2, "LTC/USDT": 3, "ETH/USD": 4}
    args = dict(position_id=UUID(int=number), instrument_id=UUID(int=ids[symbol]), canonical_symbol=symbol,
        direction=direction, quantity=D("1"), strategy_configuration_identity=intent().strategy.configuration_identity,
        mark_price=None, exposure_amount=D(exposure), open_risk_amount=D(risk))
    args.update(changes)
    return PositionRiskSnapshot(**args)


def portfolio(positions=(), **changes):
    # Independent fixture aggregation; oracle assertions below use literal expected values.
    with localcontext() as ctx:
        ctx.prec = 200
        gross = sum((p.exposure_amount for p in positions), D("0"))
        args = dict(open_positions=positions, open_risk=sum((p.open_risk_amount for p in positions), D("0")),
            gross_exposure=gross, long_exposure=sum((p.exposure_amount for p in positions if p.direction is Direction.LONG), D("0")),
            short_exposure=sum((p.exposure_amount for p in positions if p.direction is Direction.SHORT), D("0")),
            available_cash=D("10000") - gross)
    args.update(changes)
    return snapshot(**args)


def configuration(**changes):
    args = dict(operating_limits=limits(max_positions=10), platform_hard_limits=limits(PlatformHardLimits, max_positions=10))
    args.update(changes)
    return config(**args)


def correlation(a="BTC/USDT", b="ETH/USDT", coefficient="0.9", **changes):
    args = dict(instrument_a=a, instrument_b=b, coefficient=D(coefficient) if isinstance(coefficient,str) else coefficient, provider="BINANCE", timeframe="1h",
        method="declared-log-return-correlation/v1", observation_count=100, window_start=T-timedelta(days=5),
        window_end=T-timedelta(hours=1), as_of=T, expires_at=T+timedelta(minutes=10), source_identity=ID)
    args.update(changes)
    return CorrelationEvidence(**args)


def group(code="DECLARED_GROUP", members=("BTC/USDT", "ETH/USDT"), **changes):
    args = dict(code=code, members=members, platform_max_fraction=D("0.5"), operating_max_fraction=D("0.5"), source_identity=ID)
    args.update(changes)
    return ExposureGroup(**args)


def context(proposal, snap, p, **changes):
    args = dict(intent_identity=proposal.intent_identity, snapshot_identity=snap.snapshot_identity, policy_identity=p.policy_identity,
        valuation_currency=snap.scope.base_currency, valuation_identity=snap.valuation_identity, as_of=AT,
        expires_at=T+timedelta(minutes=1), source_identity=ID,
        strategy_attributions=(attribution(proposal.strategy.configuration_identity, proposal.strategy.family, proposal.strategy.version),),
        correlations=(), groups=())
    args.update(changes)
    return PortfolioRiskContext(**args)


def inputs(positions=(), *, proposal=None, snap=None, c=None, p=None, sizing=None, context_changes=None):
    proposal = proposal or intent()
    snap = snap or portfolio(positions)
    c, p = c or configuration(), p or policy()
    trade = evaluate_trade_risk(proposal, c, snap, sizing or assumptions(proposed_quantity=D("1")), AT)
    ctx = context(proposal, snap, p, **(context_changes or {}))
    return dict(intent=proposal, trade_risk=trade, configuration=c, snapshot=snap, policy=p, context=ctx, evaluated_at=AT)


def run(positions=(), **kwargs):
    return evaluate_portfolio_risk(**inputs(positions, **kwargs))


def codes(result):
    return {f.code for f in result.findings}


def check(result, kind, key=None):
    rows = [c for c in result.projection.checks if c.kind is kind and (key is None or c.key == key)]
    assert len(rows) == 1
    return rows[0]


def test_empty_known_portfolio_oracle_and_gate_boundary():
    result = run()
    assert result.status is S.PORTFOLIO_RISK_PASS
    p = result.projection
    assert p.before.position_count == 0 and p.after.position_count == 1
    assert p.before.open_risk == p.before.gross_exposure == D("0")
    assert p.proposed_risk == p.after.open_risk == D("10")
    assert p.proposed_exposure == p.after.gross_exposure == D("100")
    assert p.after.available_cash == D("9900")
    assert p.after.long_exposure == D("100") and p.after.short_exposure == D("0")
    assert result.scope == "PORTFOLIO_RISK_ONLY" and not isinstance(result, RiskDecision)
    assert not hasattr(result, "approval_expires_at") and not hasattr(result, "outcome")
    assert {s.value for s in S} == {"PORTFOLIO_RISK_PASS", "PORTFOLIO_RISK_REJECTED"}


def test_exact_before_after_oracle_and_actual_proposal_not_maximum_size():
    args = inputs((position(),))
    assert args["trade_risk"].sizing.constrained_max_quantity == D("5")
    result = evaluate_portfolio_risk(**args)
    assert result.status is S.PORTFOLIO_RISK_PASS
    p = result.projection
    assert (p.before.open_risk, p.proposed_risk, p.after.open_risk) == (D("20"), D("10"), D("30"))
    assert (p.before.gross_exposure, p.proposed_exposure, p.after.gross_exposure) == (D("1000"), D("100"), D("1100"))
    assert (p.before.available_cash, p.after.available_cash) == (D("9000"), D("8900"))
    assert (check(result,K.INSTRUMENT).before_fraction,check(result,K.INSTRUMENT).after_fraction) == (D("0.1"), D("0.11"))
    assert check(result,K.STRATEGY).after_fraction == D("0.11")
    assert check(result,K.CORRELATED_EXPOSURE).after_value == D("1100")
    assert p.existing_positions == args["snapshot"].open_positions


def test_combined_costs_propagate_from_trade_gate_without_double_counting():
    result = run((position(),), sizing=assumptions(proposed_quantity=D("1"), costs=combined_costs()))
    assert result.status is S.PORTFOLIO_RISK_PASS
    p = result.projection
    assert p.proposed_risk == D("13.04015") and p.after.open_risk == D("33.04015")
    assert p.proposed_exposure == D("101.5") and p.after.gross_exposure == D("1101.5")
    assert p.proposed_cash_commitment == D("101.6015") and p.after.available_cash == D("8898.3985")
    assert p.before.equity == p.after.equity == D("10000")  # explicit frozen risk denominator, not accounting


@pytest.mark.parametrize("sizing", [assumptions(requested_risk_fraction=D("0")), assumptions(proposed_quantity=D("6"))])
def test_upstream_trade_failure_can_never_pass(sizing):
    args = inputs(sizing=sizing)
    assert args["trade_risk"].status is TradeRiskStatus.TRADE_RISK_REJECTED
    result = evaluate_portfolio_risk(**args)
    assert result.status is S.PORTFOLIO_RISK_REJECTED and result.projection is None
    assert codes(result) == {RiskReason.UPSTREAM_TRADE_RISK_FAILURE}


@pytest.mark.parametrize("existing,expected", [("20", S.PORTFOLIO_RISK_PASS),
    ("20.000000000000000000000000000001", S.PORTFOLIO_RISK_REJECTED)])
def test_open_risk_exact_boundary_and_adjacent_decimal(existing, expected):
    c = configuration(operating_limits=limits(max_positions=10,max_open_risk_fraction=D("0.003")))
    result = run((position(risk=existing),),c=c)
    assert result.status is expected
    assert check(result,K.OPEN_RISK).limit_value == D("30")
    if expected is S.PORTFOLIO_RISK_REJECTED:
        assert RiskReason.AGGREGATE_OPEN_RISK_EXCEEDED in codes(result)


def test_hard_and_stricter_operating_limits_cannot_be_bypassed():
    hard = limits(PlatformHardLimits,max_positions=10,max_open_risk_fraction=D("0.002"))
    op = limits(max_positions=10,max_open_risk_fraction=D("0.002"))
    result = run((position(),),c=configuration(operating_limits=op,platform_hard_limits=hard))
    assert RiskReason.AGGREGATE_OPEN_RISK_EXCEEDED in codes(result)
    stricter = configuration(operating_limits=limits(max_positions=10,max_open_risk_fraction=D("0.001")))
    assert check(run((position(),),c=stricter),K.OPEN_RISK).limit_value == D("10")
    with pytest.raises(ValueError,match="hard limits"):
        configuration(operating_limits=limits(max_positions=10,max_open_risk_fraction=D("0.003")), platform_hard_limits=hard)


@pytest.mark.parametrize("field,kind,reason", [
    ("max_gross_exposure_fraction",K.GROSS_EXPOSURE,RiskReason.PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED),
    ("max_instrument_concentration_fraction",K.INSTRUMENT,RiskReason.INSTRUMENT_CONCENTRATION_EXCEEDED),
    ("max_directional_exposure_fraction",K.DIRECTION,RiskReason.DIRECTIONAL_EXPOSURE_EXCEEDED),
    ("max_strategy_concentration_fraction",K.STRATEGY,RiskReason.STRATEGY_CONCENTRATION_EXCEEDED),
    ("max_correlated_exposure_fraction",K.CORRELATED_EXPOSURE,RiskReason.CORRELATED_EXPOSURE_LIMIT_EXCEEDED)])
def test_each_aggregate_exposure_limit_is_mandatory(field,kind,reason):
    c = configuration(operating_limits=limits(max_positions=10,**{field:D("0.1")}))
    result = run((position(),),c=c)
    assert result.status is S.PORTFOLIO_RISK_REJECTED and reason in codes(result)
    chosen = check(result,kind,"LONG" if kind is K.DIRECTION else None)
    assert chosen.after_value == D("1100") and chosen.limit_value == D("1000")


def test_multiple_positions_gross_directions_and_correlated_oracle_no_netting():
    positions=(position(),position("ETH/USDT",exposure="2000",risk="40",direction=Direction.SHORT,number=11))
    result=run(positions,context_changes={"correlations":(correlation(coefficient="-0.9"),)})
    assert result.status is S.PORTFOLIO_RISK_PASS
    p=result.projection
    assert (p.before.gross_exposure,p.after.gross_exposure)==(D("3000"),D("3100"))
    assert (p.before.open_risk,p.after.open_risk)==(D("60"),D("70"))
    assert p.after.long_exposure==D("1100") and p.after.short_exposure==D("2000")
    assert check(result,K.CORRELATED_EXPOSURE).after_value==D("3100")
    assert len(p.correlation_contributions)==2
    assert next(c for c in p.correlation_contributions if c.canonical_symbol=="ETH/USDT").direction_adjusted_coefficient==D("0.9")


def test_strategy_allocation_groups_configurations_by_family_and_version():
    alternate=StrategyDefinition("TEST_RESEARCH","1.0.0",{"variant":D("1")})
    p=position(exposure="4800",strategy_configuration_identity=alternate.configuration_identity)
    c=configuration(operating_limits=limits(max_positions=10,max_strategy_concentration_fraction=D("0.48")))
    attrs=(attribution(),attribution(alternate.configuration_identity))
    result=run((p,),c=c,context_changes={"strategy_attributions":attrs})
    assert RiskReason.STRATEGY_CONCENTRATION_EXCEEDED in codes(result)
    assert check(result,K.STRATEGY).before_value==D("4800") and check(result,K.STRATEGY).after_value==D("4900")
    assert len(result.projection.after.strategy_exposure)==1
    assert len(result.projection.after.strategy_configuration_exposure)==2


def test_missing_strategy_attribution_is_not_an_empty_allocation_bucket():
    result=run((position(strategy_configuration_identity=OTHER),))
    assert codes(result)=={RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE} and result.projection is None
    bad=run(context_changes={"strategy_attributions":(attribution(family="DIFFERENT"),)})
    assert RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT in codes(bad)


@pytest.mark.parametrize("coefficient",["0.9","-0.9"])
@pytest.mark.parametrize("existing_direction",list(Direction))
@pytest.mark.parametrize("proposed_direction",list(Direction))
def test_conservative_correlation_has_no_hedging_credit(coefficient,existing_direction,proposed_direction):
    c=configuration(operating_limits=limits(max_positions=10,max_correlated_exposure_fraction=D("0.05")))
    result=run((position("ETH/USDT",exposure="600",direction=existing_direction),),c=c,
        proposal=intent(proposed_direction),context_changes={"correlations":(correlation(coefficient=coefficient),)})
    assert RiskReason.CORRELATED_EXPOSURE_LIMIT_EXCEEDED in codes(result)
    assert check(result,K.CORRELATED_EXPOSURE).before_value==D("600")
    assert check(result,K.CORRELATED_EXPOSURE).after_value==D("700")
    contribution=result.projection.correlation_contributions[0]
    assert contribution.included is True
    expected=D(coefficient) if existing_direction is proposed_direction else D(coefficient).copy_negate()
    assert contribution.direction_adjusted_coefficient==expected


@pytest.mark.parametrize("coefficient,included",[("0.8",True),("-0.8",True),("0.799999999999999999999",False),("0",False)])
def test_absolute_correlation_threshold_boundary(coefficient,included):
    result=run((position("ETH/USDT"),),context_changes={"correlations":(correlation(coefficient=coefficient),)})
    assert result.projection.correlation_contributions[0].included is included
    assert check(result,K.CORRELATED_EXPOSURE).after_value==(D("1100") if included else D("100"))


def test_lower_operating_correlation_threshold_is_stricter_not_bypass():
    p=policy(operating_correlation_threshold=D("0.7"))
    result=run((position("ETH/USDT"),),p=p,context_changes={"correlations":(correlation(coefficient="0.75"),)})
    assert result.projection.correlation_contributions[0].included
    with pytest.raises(ValueError): policy(operating_correlation_threshold=D("0.9"))
    with pytest.raises(ValueError): policy(operating_correlation_max_age=timedelta(hours=3))


def test_missing_correlation_is_not_guessed_from_asset_symbols():
    result=run((position("ETH/USDT"),))
    assert codes(result)=={RiskReason.CORRELATION_EVIDENCE_MISSING}
    assert result.projection is None
    # Identical canonical instrument is an exact identity, not an inferred BTC/ETH relationship.
    self_result=run((position(),))
    contribution=self_result.projection.correlation_contributions[0]
    assert contribution.coefficient==D("1") and contribution.evidence_identity is None


@pytest.mark.parametrize("evidence",[correlation(as_of=T-timedelta(hours=2),window_end=T-timedelta(hours=2)),
    correlation(expires_at=AT),correlation(as_of=T-timedelta(seconds=1))])
def test_old_or_expired_observations_fail_closed(evidence):
    # Context may describe the observation but cannot make an old/expired row fresh.
    if evidence.as_of>AT:
        pytest.fail("fixture unexpectedly beyond context")
    result=run((position("ETH/USDT"),),context_changes={"correlations":(evidence,)},
        p=policy(operating_correlation_max_age=timedelta(seconds=1)))
    assert result.status is S.PORTFOLIO_RISK_REJECTED
    assert RiskReason.CORRELATION_EVIDENCE_STALE in codes(result)


def test_correlation_at_maximum_age_boundary_is_valid_but_one_microsecond_older_is_not():
    p=policy(operating_correlation_max_age=timedelta(seconds=3))
    assert run((position("ETH/USDT"),),p=p,context_changes={"correlations":(correlation(),)}).status is S.PORTFOLIO_RISK_PASS
    older=correlation(as_of=T-timedelta(microseconds=1))
    assert RiskReason.CORRELATION_EVIDENCE_STALE in codes(run((position("ETH/USDT"),),p=p,context_changes={"correlations":(older,)}))


def test_explicit_group_concentration_and_no_inferred_business_membership():
    result=run((position(),),context_changes={"groups":(group(platform_max_fraction=D("0.1"),operating_max_fraction=D("0.1")),)})
    assert RiskReason.GROUP_CONCENTRATION_EXCEEDED in codes(result)
    assert check(result,K.GROUP).before_value==D("1000") and check(result,K.GROUP).after_value==D("1100")
    unrelated=run((position(),),context_changes={"groups":(group(members=("LTC/USDT",)),)})
    assert check(unrelated,K.GROUP).after_value==D("0") and unrelated.status is S.PORTFOLIO_RISK_PASS
    overlap=run((position(),),context_changes={"groups":(group("A",("BTC/USDT",)),group("B",("BTC/USDT","ETH/USDT")))})
    assert check(overlap,K.GROUP,"A").after_value==check(overlap,K.GROUP,"B").after_value==D("1100")


def test_group_and_policy_hard_limits_cannot_be_increased_by_operating_settings():
    with pytest.raises(ValueError): group(platform_max_fraction=D("0.2"),operating_max_fraction=D("0.3"))
    result=run((position(),),context_changes={"groups":(group(platform_max_fraction=D("0.5"),operating_max_fraction=D("0.1")),)})
    assert RiskReason.GROUP_CONCENTRATION_EXCEEDED in codes(result)


def test_position_count_is_conservative_new_slot_without_merging():
    c=configuration(operating_limits=limits(max_positions=1))
    result=run((position(),),c=c)
    assert RiskReason.PORTFOLIO_POSITION_COUNT_EXCEEDED in codes(result)
    assert check(result,K.POSITION_COUNT).after_value==D("2")


@pytest.mark.parametrize("field",["open_positions","open_risk","gross_exposure","long_exposure","short_exposure"])
def test_unknown_required_aggregate_never_becomes_zero(field):
    result=run(snap=replace(portfolio(),**{field:None}))
    assert result.status is S.PORTFOLIO_RISK_REJECTED and result.projection is None
    assert codes(result)=={RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE}


@pytest.mark.parametrize("field",["exposure_amount","open_risk_amount"])
def test_incomplete_position_is_not_discarded(field):
    bad=replace(position(),**{field:None})
    s=portfolio((position(),))
    result=run(snap=replace(s,open_positions=(bad,)))
    assert codes(result)=={RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE}


@pytest.mark.parametrize("field,value",[("open_risk",D("21")),("gross_exposure",D("999")),
    ("long_exposure",D("0")),("short_exposure",D("1000"))])
def test_snapshot_totals_must_reconcile_exactly_with_all_positions(field,value):
    result=run(snap=replace(portfolio((position(),)),**{field:value}))
    assert codes(result)=={RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT}


def test_known_position_mark_cannot_be_understated_and_aliases_cannot_split_concentration():
    result=run((position(exposure="100",mark_price=D("101")),))
    assert RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT in codes(result)
    alias=position(instrument_id=UUID(int=22))
    assert RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT in codes(run((alias,)))


def test_currency_and_valuation_mismatch_never_get_parity_or_conversion_fallback():
    result=run((position("ETH/USD"),),context_changes={"correlations":(correlation(b="ETH/USD"),)})
    assert codes(result)=={RiskReason.PORTFOLIO_VALUATION_INCOMPATIBLE}
    assert result.projection is None
    assert codes(run(context_changes={"valuation_identity":OTHER}))=={RiskReason.PORTFOLIO_VALUATION_INCOMPATIBLE}
    assert codes(run(context_changes={"valuation_currency":"USD"}))=={RiskReason.PORTFOLIO_VALUATION_INCOMPATIBLE}


def test_optional_ratios_are_derived_from_complete_inventory_or_verified_if_supplied():
    s=portfolio((position(),),instrument_concentration={"BTC/USDT":D("0.1")},
        strategy_concentration={intent().strategy.configuration_identity:D("0.1")},correlated_exposure=D("1000"))
    assert run(snap=s).status is S.PORTFOLIO_RISK_PASS
    bad=replace(s,instrument_concentration={"BTC/USDT":None})
    assert RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE in codes(run(snap=bad))
    for bad in (replace(s,instrument_concentration={}),replace(s,correlated_exposure=D("0")),
                replace(s,strategy_concentration={intent().strategy.configuration_identity:D("0.2")})):
        assert RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT in codes(run(snap=bad))


def test_context_and_upstream_reuse_require_exact_bindings_and_same_evaluation_instant():
    args=inputs()
    for change in ({"intent":intent(reference_price=D("101"))},{"configuration":configuration(profile=RiskProfile.GROWTH)},
        {"snapshot":portfolio(available_cash=D("9999"))},{"evaluated_at":AT+timedelta(microseconds=1)},
        {"context":replace(args["context"],policy_identity=OTHER)}):
        assert RiskReason.RISK_CONTEXT_MISMATCH in codes(evaluate_portfolio_risk(**(args|change)))
    assert RiskReason.CONTEXT_EXPIRED in codes(run(context_changes={"as_of":T+timedelta(seconds=2),"expires_at":AT}))
    with pytest.raises(ValueError): evaluate_portfolio_risk(**(args|{"evaluated_at":AT.replace(tzinfo=None)}))


@pytest.mark.parametrize("name",["intent","trade_risk","configuration","snapshot","policy","context"])
def test_missing_contracts_raise_typed_error_without_fake_identities(name):
    with pytest.raises(PortfolioRiskInputError) as error:
        evaluate_portfolio_risk(**(inputs()|{name:None}))
    assert error.value.code is RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE


@pytest.mark.parametrize("value",[D("NaN"),D("Infinity"),D("-Infinity"),D("1.00001"),D("-1.00001"),0.5,None])
def test_invalid_correlation_coefficients_rejected(value):
    with pytest.raises(ValueError): correlation(coefficient=value)


@pytest.mark.parametrize("change",[{"window_start":T},{"window_end":T+timedelta(seconds=1)},
    {"observation_count":True},{"observation_count":1},{"as_of":T.replace(tzinfo=None)},
    {"instrument_a":"BTC/USDT","instrument_b":"BTC/USDT"},{"timeframe":"5m"}])
def test_invalid_correlation_context_rejected(change):
    with pytest.raises(ValueError): correlation(**change)


def test_pair_order_decimal_representation_and_context_order_do_not_change_identity():
    first=correlation(coefficient="0.9000")
    second=correlation("ETH/USDT","BTC/USDT",coefficient="0.9")
    assert first.evidence_identity==second.evidence_identity
    a=inputs((position("ETH/USDT"),),context_changes={"correlations":(first,),"groups":(group("A"),group("B"))})
    b=inputs((position("ETH/USDT"),),context_changes={"correlations":(second,),"groups":(group("B",("ETH/USDT","BTC/USDT")),group("A"))})
    assert evaluate_portfolio_risk(**a).evaluation_identity==evaluate_portfolio_risk(**b).evaluation_identity
    assert evaluate_portfolio_risk(**a)==evaluate_portfolio_risk(**a)
    same=replace(a["snapshot"],equity=D("10000.00"))
    c=inputs(snap=same,context_changes={"correlations":(first,),"groups":(group("A"),group("B"))})
    assert evaluate_portfolio_risk(**a).evaluation_identity==evaluate_portfolio_risk(**c).evaluation_identity


def test_inputs_and_derived_states_are_immutable_and_exact_wire_values_survive():
    args=inputs((position(),))
    before=to_wire(args["snapshot"])
    result=evaluate_portfolio_risk(**args)
    assert to_wire(args["snapshot"])==before
    for obj,name,value in ((args["snapshot"],"equity",D("1")),(args["context"],"source_identity",OTHER),
        (args["policy"],"version","changed"),(result,"status",S.PORTFOLIO_RISK_REJECTED),
        (result.projection.before,"gross_exposure",D("0"))):
        with pytest.raises((FrozenInstanceError,AttributeError)): setattr(obj,name,value)
    with pytest.raises(TypeError): result.projection.after.instrument_exposure["BTC/USDT"]=D("1")
    wire=to_wire(result)
    assert wire["projection"]["after"]["gross_exposure"]=="1100"
    assert wire["evaluated_at"].endswith("Z")
    assert json.loads(json.dumps(wire))["projection"]["proposed_risk"]=="10.0"


def test_mandatory_checks_cannot_be_removed_or_failures_changed_to_pass():
    result=run((position(),))
    with pytest.raises(ValueError,match="Mandatory"):
        replace(result.projection,checks=tuple(c for c in result.projection.checks if c.kind is not K.OPEN_RISK))
    bad=run((position(),),c=configuration(operating_limits=limits(max_positions=10,max_open_risk_fraction=D("0.001"))))
    with pytest.raises(ValueError): replace(bad,status=S.PORTFOLIO_RISK_PASS)
    with pytest.raises(ValueError): replace(result.projection,correlation_contributions=())


@pytest.mark.parametrize("change",[
    {"positions":(position(exposure="1001"),)}, {"positions":(position(risk="21"),)},
    {"positions":(position(number=22),)},
    {"snap":portfolio((position(),),equity=D("10001"))},
    {"sizing":assumptions(proposed_quantity=D("0.5"))},
    {"c":configuration(operating_limits=limits(max_positions=10,max_open_risk_fraction=D("0.09")))},
    {"p":policy(operating_correlation_threshold=D("0.7"))},
    {"context_changes":{"groups":(group(),)}},
    {"context_changes":{"source_identity":OTHER}},
    {"context_changes":{"correlations":(correlation(coefficient="0.5"),)}}])
def test_material_inputs_change_reproducibility_identity(change):
    baseline=run((position(),))
    args={"positions":(position(),)}|change
    assert run(**args).evaluation_identity!=baseline.evaluation_identity


def test_contract_sequences_reject_duplicates_unknowns_or_mixed_windows():
    args=inputs()
    for change in ({"groups":None},{"groups":(group(),group())},
        {"correlations":(correlation(),correlation("ETH/USDT","BTC/USDT"))},
        {"strategy_attributions":(attribution(),attribution())},
        {"correlations":(correlation(),correlation(b="LTC/USDT",timeframe="4h"))}):
        with pytest.raises(ValueError): replace(args["context"],**change)
    with pytest.raises(ValueError): group(members=("BTC/USDT","BTC/USDT"))
    with pytest.raises(ValueError): group(operating_max_fraction=D("NaN"))


def test_no_provider_database_execution_or_float_dependencies():
    folder=Path(__file__).parents[1]/"app/risk/portfolio_risk"
    forbidden=("sqlalchemy","psycopg","httpx","requests","fastapi","app.db","app.market_data","app.strategy_lab.execution","app.risk.decision")
    for path in folder.glob("*.py"):
        source=path.read_text()
        assert "BTC" not in source and "ETH" not in source and "QUIDAX" not in source
        for node in ast.walk(ast.parse(source)):
            if isinstance(node,ast.Import): assert not any(a.name.startswith(forbidden) for a in node.names)
            if isinstance(node,ast.ImportFrom): assert not (node.module or "").startswith(forbidden)
            if isinstance(node,ast.Constant): assert not isinstance(node.value,float)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name): assert node.func.id not in {"float","RiskDecision"}
    script="import sys; before=set(sys.modules); import app.risk.portfolio_risk; added=set(sys.modules)-before; assert not any(x in added for x in ['app.db.session','app.market_data','httpx','sqlalchemy','fastapi']); print('pure portfolio risk')"
    child=subprocess.run([sys.executable,"-B","-c",script],cwd=Path(__file__).parents[1],capture_output=True,text=True,check=True)
    assert child.stdout.strip()=="pure portfolio risk"



def test_gross_exposure_cannot_exceed_equity_even_when_cash_can_fund_the_trade():
    s=portfolio((position(exposure="9950"),),available_cash=D("100"))
    args=inputs(snap=s)
    assert args["trade_risk"].status is TradeRiskStatus.TRADE_RISK_PASS
    result=evaluate_portfolio_risk(**args)
    assert RiskReason.PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED in codes(result)
    assert result.projection.after.gross_exposure==D("10050")
    assert result.projection.after.available_cash==D("0")
    s=portfolio(available_cash=D("10001"))
    assert RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT in codes(run(snap=s))


def test_insufficient_cash_upstream_cannot_be_overridden_by_portfolio_evidence():
    args=inputs(snap=portfolio(available_cash=D("99.99")))
    assert args["trade_risk"].status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert codes(evaluate_portfolio_risk(**args))=={RiskReason.UPSTREAM_TRADE_RISK_FAILURE}


def test_multiple_distinct_correlations_retain_all_positions_and_use_full_unsigned_exposures():
    positions=(position("ETH/USDT",exposure="1500",risk="20"),position("LTC/USDT",exposure="500",risk="10",number=11))
    rows=(correlation(coefficient="0.9"),correlation(b="LTC/USDT",coefficient="0.1"))
    result=run(positions,context_changes={"correlations":rows})
    assert result.status is S.PORTFOLIO_RISK_PASS
    assert len(result.projection.correlation_contributions)==2
    assert check(result,K.CORRELATED_EXPOSURE).before_value==D("1500")
    assert check(result,K.CORRELATED_EXPOSURE).after_value==D("1600")
    assert result.projection.after.gross_exposure==D("2100")
    reordered=run(tuple(reversed(positions)),context_changes={"correlations":tuple(reversed(rows))})
    assert result.evaluation_identity==reordered.evaluation_identity


def test_same_family_new_configuration_is_grouped_but_distinct_versions_remain_explicit():
    other=StrategyDefinition("TEST_RESEARCH","2.0.0",{})
    p=position(strategy_configuration_identity=other.configuration_identity)
    attrs=(attribution(),attribution(other.configuration_identity,version="2.0.0"))
    result=run((p,),context_changes={"strategy_attributions":attrs})
    assert result.status is S.PORTFOLIO_RISK_PASS
    assert len(result.projection.after.strategy_exposure)==2
    reordered=run((p,),context_changes={"strategy_attributions":tuple(reversed(attrs))})
    assert result.evaluation_identity==reordered.evaluation_identity


def test_future_portfolio_or_correlation_evidence_is_rejected_without_lookahead():
    future=AT+timedelta(microseconds=1)
    result=run(context_changes={"as_of":future})
    assert codes(result)=={RiskReason.RISK_CONTEXT_MISMATCH}
    result=run((position("ETH/USDT"),),context_changes={"as_of":future,"correlations":(correlation(as_of=future),)})
    assert codes(result)=={RiskReason.RISK_CONTEXT_MISMATCH}
    with pytest.raises(ValueError):
        replace(inputs()["context"],correlations=(correlation(as_of=future),))


def test_decimal_and_timezone_equivalence_preserve_full_result_identity():
    a=run((position(),))
    args=inputs((position(exposure="1000.0000",risk="20.000"),),p=policy(operating_correlation_threshold=D("0.80000")))
    args["evaluated_at"]=AT.astimezone(timezone(timedelta(hours=1)))
    b=evaluate_portfolio_risk(**args)
    assert a.evaluation_identity==b.evaluation_identity
    with localcontext() as ctx:
        ctx.prec=3
        # Re-evaluate already frozen inputs; financial helpers must ignore ambient context.
        c=evaluate_portfolio_risk(**args)
    assert b.evaluation_identity==c.evaluation_identity


@pytest.mark.parametrize("changed",[correlation(coefficient="0.8"),correlation(source_identity=OTHER),
    correlation(window_start=T-timedelta(days=6)),correlation(as_of=T+timedelta(seconds=1))])
def test_used_correlation_provenance_changes_identity_even_if_exposure_bucket_does_not(changed):
    base=run((position("ETH/USDT"),),context_changes={"correlations":(correlation(),)})
    new=run((position("ETH/USDT"),),context_changes={"correlations":(changed,)})
    assert base.evaluation_identity!=new.evaluation_identity
    assert check(base,K.CORRELATED_EXPOSURE).after_value==check(new,K.CORRELATED_EXPOSURE).after_value


@pytest.mark.parametrize("change",[{"entry_reference_price":D("101")},{"proposed_quantity":D("0.9")}])
def test_proposed_trade_exposure_and_risk_are_identity_bound(change):
    baseline=run((position(),))
    new=run((position(),),sizing=assumptions(proposed_quantity=D("1"),**{k:v for k,v in change.items() if k!="proposed_quantity"})
        if "proposed_quantity" not in change else assumptions(**change))
    assert baseline.evaluation_identity!=new.evaluation_identity
    assert baseline.projection.proposed_exposure!=new.projection.proposed_exposure


@pytest.mark.parametrize("field",["platform_correlation_threshold","operating_correlation_threshold"])
@pytest.mark.parametrize("bad",[D("NaN"),D("Infinity"),D("-0.1"),D("1.1"),0.8,None])
def test_nonfinite_or_invalid_policy_limits_rejected(field,bad):
    with pytest.raises(ValueError): policy(**{field:bad})


def test_group_membership_or_threshold_changes_are_material_without_optimization():
    baseline=run((position(),),context_changes={"groups":(group(),)})
    for g in (group(members=("ETH/USDT",)),group(operating_max_fraction=D("0.4")),group(source_identity=OTHER)):
        assert run((position(),),context_changes={"groups":(g,)}).evaluation_identity!=baseline.evaluation_identity


def test_position_evidence_cannot_inject_live_short_exposure():
    environment=Environment.LIVE
    proposal=intent(environment=environment)
    c=configuration(environment=environment)
    s=portfolio((position(direction=Direction.SHORT),),scope=scope(environment))
    args=inputs(proposal=proposal,c=c,snap=s)
    assert args["trade_risk"].status is TradeRiskStatus.TRADE_RISK_PASS
    assert codes(evaluate_portfolio_risk(**args))=={RiskReason.SHORT_RESEARCH_ONLY}


def test_missing_optional_concentration_maps_are_not_missing_inventory():
    # Maps are derived from complete, known inventory; they are never replaced with zeros.
    result=run((position(),),snap=portfolio((position(),),instrument_concentration=None,strategy_concentration=None))
    assert check(result,K.INSTRUMENT).before_value==D("1000")
    assert check(result,K.STRATEGY).before_value==D("1000")



def test_rejection_identity_is_stable_across_python_hash_seeds_with_mixed_bad_evidence():
    import os
    script = """import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_portfolio_risk import D, portfolio, position, run, codes
s=portfolio((position(),),instrument_concentration={'BTC/USDT':None,'ETH/USDT':D('0.2')})
r=run(snap=s)
valid=run((position(),))
print(json.dumps({'identity':r.evaluation_identity,'codes':sorted(c.value for c in codes(r)), 'valid':valid.evaluation_identity},sort_keys=True))
"""
    results=[]
    for seed in ("1","2","3","4"):
        child=subprocess.run([sys.executable,"-B","-c",script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        results.append(json.loads(child.stdout))
    assert all(r==results[0] for r in results)
    assert results[0]['codes']==['REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE']
