"""Counterfactual additive exposure only; no ledger mutation, offsets or execution."""
from decimal import Decimal

from app.strategy_lab.numeric import difference, exact_sum as add, exact_product as mul, ratio
from app.risk.reasons import RiskReason
from app.risk.vocabulary import Direction
from .models import (CorrelationContribution, PortfolioLimitCheck, PortfolioLimitKind as K,
                     PortfolioProjection, PortfolioState)
from .validation import fail, validate_positions, verify_optional_concentrations

ZERO = Decimal("0")
ONE = Decimal("1")


def amounts(positions, key):
    keys = {key(p) for p in positions}
    return {k: add(p.exposure_amount for p in positions if key(p) == k) for k in sorted(keys)}


def increment(mapping, key, value):
    result = dict(mapping)
    result[key] = add((result.get(key, ZERO), value))
    return result


def project_portfolio(intent, trade, configuration, snapshot, policy, context):
    attributions = validate_positions(intent, snapshot, context)
    positions = snapshot.open_positions
    instruments = amounts(positions, lambda p: p.canonical_symbol)
    configurations = amounts(positions, lambda p: p.strategy_configuration_identity)
    strategies = amounts(positions, lambda p: attributions[p.strategy_configuration_identity].strategy_identity)
    verify_optional_concentrations(snapshot, instruments, configurations)
    p = trade.evaluated_projection
    strategy = attributions[intent.strategy.configuration_identity].strategy_identity
    before = PortfolioState(snapshot.equity, snapshot.available_cash, snapshot.open_risk, snapshot.gross_exposure,
        snapshot.long_exposure, snapshot.short_exposure, len(positions), instruments, strategies, configurations)
    after = PortfolioState(snapshot.equity, difference(snapshot.available_cash, p.required_cash),
        add((snapshot.open_risk, p.modeled_loss)), add((snapshot.gross_exposure, p.notional)),
        add((snapshot.long_exposure, p.notional if intent.direction is Direction.LONG else ZERO)),
        add((snapshot.short_exposure, p.notional if intent.direction is Direction.SHORT else ZERO)), len(positions) + 1,
        increment(instruments, intent.instrument.canonical_symbol, p.notional), increment(strategies, strategy, p.notional),
        increment(configurations, intent.strategy.configuration_identity, p.notional))
    checks = []
    operating, hard = configuration.operating_limits, configuration.platform_hard_limits

    def check(kind, key, old, new, fraction=None, limit=None):
        if fraction is not None:
            limit = mul(snapshot.equity, fraction)
        checks.append(PortfolioLimitCheck(kind, key, old, difference(new, old), new, limit,
            ratio(old, snapshot.equity) if fraction is not None else None,
            ratio(new, snapshot.equity) if fraction is not None else None, fraction))

    def cap(name):
        return min(getattr(operating, name), getattr(hard, name))

    check(K.OPEN_RISK, "TOTAL", before.open_risk, after.open_risk, cap("max_open_risk_fraction"))
    check(K.GROSS_EXPOSURE, "TOTAL", before.gross_exposure, after.gross_exposure, cap("max_gross_exposure_fraction"))
    check(K.POSITION_COUNT, "TOTAL", Decimal(before.position_count), Decimal(after.position_count),
          limit=Decimal(min(operating.max_positions, hard.max_positions)))
    check(K.CAPITAL_COMMITMENT, "NEW_RESERVE", ZERO, p.required_cash, limit=snapshot.available_cash)
    for direction in Direction:
        name = "long_exposure" if direction is Direction.LONG else "short_exposure"
        check(K.DIRECTION, direction.value, getattr(before, name), getattr(after, name), cap("max_directional_exposure_fraction"))
    for kind, old, new, field in ((K.INSTRUMENT, before.instrument_exposure, after.instrument_exposure, "max_instrument_concentration_fraction"),
                                  (K.STRATEGY, before.strategy_exposure, after.strategy_exposure, "max_strategy_concentration_fraction")):
        for key in sorted(set(old) | set(new)):
            check(kind, key, old.get(key, ZERO), new.get(key, ZERO), cap(field))

    pairs = {row.pair: row for row in context.correlations}
    contributions = []
    anchor = intent.instrument.canonical_symbol
    for position in positions:
        if position.canonical_symbol == anchor:
            coefficient, included, evidence_id = ONE, True, None  # exact identity, no inferred asset relationship
        else:
            pair = tuple(sorted((anchor, position.canonical_symbol)))
            evidence = pairs.get(pair)
            if evidence is None:
                fail(RiskReason.CORRELATION_EVIDENCE_MISSING, "A proposed/existing instrument pair lacks trusted correlation evidence")
            coefficient, evidence_id = evidence.coefficient, evidence.evidence_identity
            included = coefficient.copy_abs() >= policy.operating_correlation_threshold
        adjusted = coefficient if position.direction is intent.direction else coefficient.copy_negate()
        contributions.append(CorrelationContribution(position.position_identity, position.canonical_symbol,
            position.direction, coefficient, adjusted, included, position.exposure_amount, evidence_id))
    old_correlated = add(c.exposure_amount for c in contributions if c.included)
    if snapshot.correlated_exposure is not None and snapshot.correlated_exposure != old_correlated:
        fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Supplied correlated aggregate disagrees with this proposal/policy cluster")
    check(K.CORRELATED_EXPOSURE, anchor, old_correlated, add((old_correlated, p.notional)), cap("max_correlated_exposure_fraction"))
    for group in context.groups:
        old = add(instruments.get(key, ZERO) for key in group.members)
        new = add(after.instrument_exposure.get(key, ZERO) for key in group.members)
        check(K.GROUP, group.code, old, new, min(group.operating_max_fraction, group.platform_max_fraction))
    return PortfolioProjection(before=before, after=after, canonical_symbol=anchor, direction=intent.direction,
        strategy_identity=strategy, strategy_configuration_identity=intent.strategy.configuration_identity,
        group_codes=tuple(group.code for group in context.groups), proposed_risk=p.modeled_loss,
        proposed_exposure=p.notional, proposed_cash_commitment=p.required_cash, proposed_base_quantity=p.quantity,
        existing_positions=positions, checks=tuple(checks), correlation_contributions=tuple(contributions))
