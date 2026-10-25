"""Reuse the existing stored-only causal pipeline; apply capability, never sizing."""
from datetime import datetime
from app.mandate_eligibility.contracts import EligibilityResult, EligibilityStatus, canonical_identity
from app.mandate_eligibility.policy import POLICY_V2, ADAPTIVE_TREND_REFERENCE
from app.risk.paper_source.market import read_observation
from app.risk.paper_source.policies import HISTORY, MARKET_POLICY_VERSION
from app.market_regimes.types import Regime
from app.strategies.decisions import DecisionType
from app.strategies.inputs import PositionState
from .contracts import (MandateEvaluationError, MandateObservationResult, MarketEvidence, RegimeEvidence,
    StrategyDecisionEvidence, ObservationOutcome, ObservationReason)


def require_eligible(eligibility: EligibilityResult):
    if eligibility.status is not EligibilityStatus.ELIGIBLE:
        raise MandateEvaluationError("mandate_ineligible", "This mandate is not technically eligible", tuple(code.value for code in eligibility.reason_codes))
    if (eligibility.policy_version != POLICY_V2.version or eligibility.policy_identity != POLICY_V2.policy_identity
        or eligibility.technical_eligibility.eligible_configurations != (ADAPTIVE_TREND_REFERENCE,)
        or eligibility.technical_eligibility.risk_configuration != POLICY_V2.risk_references[0]):
        raise MandateEvaluationError("technical_configuration_unavailable", "The exact approved mandate configuration is unavailable")


def filter_capability(decision: DecisionType):
    if decision is DecisionType.ENTER_LONG:
        return ObservationOutcome.ENTER_LONG, (ObservationReason.LONG_PROPOSAL_ONLY,)
    if decision is DecisionType.NO_ACTION:
        return ObservationOutcome.NO_TRADE, (ObservationReason.STRATEGY_NO_ACTION,)
    if decision in (DecisionType.ENTER_SHORT, DecisionType.EXIT_SHORT):
        return ObservationOutcome.NO_TRADE, (ObservationReason.UNSUPPORTED_DIRECTION,)
    return ObservationOutcome.NO_TRADE, (ObservationReason.NO_HELD_LONG_CONTEXT,)


def build_result(eligibility, book, observation, decision, market, *, at):
    require_eligible(eligibility)
    if (book.user_id != eligibility.user_intent.user_id or book.environment != eligibility.user_intent.environment
        or book.base_currency != eligibility.user_intent.allocation_currency or book.status != "active" or book.positions):
        raise MandateEvaluationError("position_context_unavailable", "The Paper book does not prove this mandate's CASH context")
    ref = ADAPTIVE_TREND_REFERENCE
    if (observation.definition.family != ref.strategy_family or observation.definition.version != ref.strategy_version
        or observation.definition.configuration_identity != ref.configuration_identity
        or observation.context.canonical_symbol != eligibility.user_intent.instrument
        or observation.context.provider != "BINANCE" or observation.context.timeframe != ref.timeframe
        or observation.position_state is not PositionState.CASH
        or decision.input_identity != observation.input_identity or decision.configuration_identity != ref.configuration_identity
        or decision.strategy_family != ref.strategy_family or decision.strategy_version != ref.strategy_version
        or decision.timestamp != observation.candle.close_time or market.instrument != observation.context
        or market.data_through != observation.candle.close_time or market.observed_at != observation.as_of
        or observation.as_of != at or market.regime != observation.regime.regime):
        raise MandateEvaluationError("analytical_context_mismatch", "Analytical evidence does not match the eligible mandate context")
    if observation.regime.regime in (Regime.UNSAFE, Regime.UNKNOWN):
        raise MandateEvaluationError("unsafe_market_evidence", "Canonical regime evidence is unsafe or incomplete")
    outcome, reasons = filter_capability(decision.decision)
    evidence = MarketEvidence(instrument_id=observation.context.instrument_id, canonical_symbol=observation.context.canonical_symbol,
        provider=observation.context.provider, timeframe=observation.context.timeframe, history_count=HISTORY,
        market_policy_version=MARKET_POLICY_VERSION, market_evidence_identity=market.data_identity,
        intelligence_identity=market.intelligence_identity, regime_identity=market.regime_identity,
        candle_open_time=observation.candle.open_time, candle_close_time=observation.candle.close_time,
        retrieved_at=observation.candle.retrieved_at, valid_until=market.expires_at)
    regime = RegimeEvidence(regime=observation.regime.regime, candidate=observation.regime.evidence.candidate,
        confirmation_count=observation.regime.evidence.confirmation_count,
        confirmation_required=observation.regime.evidence.confirmation_required,
        retained_previous=observation.regime.evidence.retained_previous, reason_codes=observation.regime.evidence.reason_codes)
    genuine = StrategyDecisionEvidence(**{field: getattr(decision, field) for field in StrategyDecisionEvidence.model_fields})
    payload = dict(mandate_identity=eligibility.mandate_identity, user_intent=eligibility.user_intent,
        eligibility_identity=eligibility.eligibility_identity, eligibility_policy_version=eligibility.policy_version,
        eligibility_policy_identity=eligibility.policy_identity, technical_configuration=ref,
        portfolio_identity=book.portfolio_id, book_identity=book.state_identity, position_context="CASH",
        market=evidence, regime=regime, strategy_decision=genuine, outcome=outcome, reason_codes=reasons)
    return MandateObservationResult(evaluation_identity=canonical_identity("strategy-observation", {"version":"mandate-strategy-v1", **payload}),
        **payload, evaluated_at=at)


def observe(connection, eligibility, book, *, at: datetime):
    require_eligible(eligibility)
    observation, decision, market = read_observation(connection, at=at, position_state=PositionState.CASH)
    return build_result(eligibility, book, observation, decision, market, at=at)
