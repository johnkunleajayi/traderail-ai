"""Synthetic analytical evidence; no market/network/database access."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError
from app.risk.adaptive_trend_intent import create_adaptive_trend_intent
from app.risk.api.schemas import EvaluationRequest
from app.risk.context import MarketRiskContext, PortfolioScope
from app.risk.vocabulary import Environment
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.contract import evaluate_strategy
from test_adaptive_trend import observation


def build(direction="long", atr="1", **overrides):
    obs = observation(direction, feature_changes={"atr_14": Decimal(atr) if atr is not None else None})
    market = MarketRiskContext(obs.context, obs.candle.close_time, obs.as_of,
        obs.as_of + timedelta(minutes=5), *("sha256:" + c * 64 for c in "abc"), obs.regime.regime)
    args = dict(scope=PortfolioScope(UUID(int=2), UUID(int=3), Environment.PAPER, "USDT"),
        observation=obs, decision=evaluate_strategy(AdaptiveTrendStrategy(), obs),
        market_context=market, created_at=obs.as_of, expires_at=market.expires_at)
    args.update(overrides)
    return create_adaptive_trend_intent(**args)


@pytest.mark.parametrize("direction,expected", [("long", "98"), ("short", "102")])
def test_exact_invalidation_and_authoritative_intent(direction, expected):
    intent = build(direction)
    assert intent.invalidation_price == Decimal(expected)
    assert intent.reference_price == Decimal("100")
    assert intent.entry_context["atr_multiplier"] == Decimal("2")
    assert build(direction) == intent
    assert build(direction, "1.000").intent_identity == intent.intent_identity


def test_exact_under_low_ambient_precision():
    with localcontext() as ctx:
        ctx.prec = 2
        assert build(atr="1.1234567890123456789").invalidation_price == Decimal("97.7530864219753086422")


@pytest.mark.parametrize("atr", [None, "0", "-1", "NaN", "Infinity", "-Infinity", "50", "51"])
def test_unavailable_invalid_or_nonpositive_boundary_fails_closed(atr):
    with pytest.raises(ValueError):
        build(atr=atr)


def test_no_price_override_in_builder_or_http():
    with pytest.raises(TypeError):
        build(invalidation_price=Decimal("99"))
    with pytest.raises(ValidationError):
        EvaluationRequest(intent_identity="sha256:" + "a" * 64, invalidation_price="99")


def test_other_observation_decision_rejected():
    decision = evaluate_strategy(AdaptiveTrendStrategy(), observation("short"))
    with pytest.raises(ValueError, match="exact authoritative"):
        build(decision=decision)


def test_no_action_is_not_an_entry():
    with pytest.raises(ValueError, match="entry decision"):
        build("range")


def test_context_mismatch_rejected():
    obs = observation()
    market = MarketRiskContext(obs.context, obs.candle.close_time,
        obs.as_of + timedelta(seconds=1), obs.as_of + timedelta(minutes=5),
        *("sha256:" + c * 64 for c in "abc"), obs.regime.regime)
    with pytest.raises(ValueError, match="Market context"):
        build(market_context=market)
