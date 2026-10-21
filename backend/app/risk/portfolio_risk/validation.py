"""Fail-closed reconciliation of trusted input records. No provider or DB access."""
from decimal import Decimal

from app.strategy_lab.numeric import exact_sum as add, exact_product as mul, ratio
from app.risk.configuration import RiskConfiguration
from app.risk.intent import TradeIntent
from app.risk.portfolio import PortfolioRiskSnapshot
from app.risk.reasons import RiskReason
from app.risk.trade_risk import TradeRiskEvaluation, TradeRiskStatus, project_size
from app.risk.vocabulary import Direction, Environment
from .context import PortfolioRiskContext
from .models import PortfolioRiskInputError
from .policy import PortfolioRiskPolicy

ZERO = Decimal("0")


def fail(code, message):
    raise PortfolioRiskInputError(code, message)


def require_inputs(intent, trade, configuration, snapshot, policy, context):
    for value, kind in ((intent, TradeIntent), (trade, TradeRiskEvaluation), (configuration, RiskConfiguration),
                        (snapshot, PortfolioRiskSnapshot), (policy, PortfolioRiskPolicy), (context, PortfolioRiskContext)):
        if type(value) is not kind:
            fail(RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE, "Explicit immutable portfolio-risk contracts are required")


def validate_context(intent, trade, configuration, snapshot, policy, context, at):
    if trade.status is not TradeRiskStatus.TRADE_RISK_PASS:
        fail(RiskReason.UPSTREAM_TRADE_RISK_FAILURE, "A failed trade-risk gate is not eligible for portfolio evaluation")
    if (trade.intent_identity != intent.intent_identity or trade.configuration_identity != configuration.configuration_identity
            or trade.snapshot_identity != snapshot.snapshot_identity or trade.evaluated_at != at
            or context.intent_identity != intent.intent_identity or context.snapshot_identity != snapshot.snapshot_identity
            or context.policy_identity != policy.policy_identity or snapshot.scope != intent.scope
            or configuration.environment != intent.scope.environment):
        fail(RiskReason.RISK_CONTEXT_MISMATCH, "Portfolio inputs must bind the exact intent, configuration, snapshot and evaluation instant")
    if (context.valuation_identity != snapshot.valuation_identity or context.valuation_currency != snapshot.scope.base_currency
            or intent.instrument.canonical_symbol.split(":")[0].split("/")[1] != context.valuation_currency):
        fail(RiskReason.PORTFOLIO_VALUATION_INCOMPATIBLE, "No implicit quote/portfolio currency conversion is permitted")
    if context.as_of < snapshot.as_of or at < max(context.as_of, snapshot.as_of, intent.created_at):
        fail(RiskReason.RISK_CONTEXT_MISMATCH, "Portfolio context must be causal and describe the supplied snapshot")
    if at >= min(context.expires_at, snapshot.expires_at, intent.expires_at, intent.market_context.expires_at):
        fail(RiskReason.CONTEXT_EXPIRED, "Required portfolio context has expired")
    if (trade.sizing is None or trade.evaluated_projection is None
            or trade.sizing.loss_model.invalidation_price != intent.invalidation_price
            or trade.sizing.strategy_reference_price != intent.reference_price
            or project_size(trade.evaluated_projection.quantity, trade.sizing.loss_model) != trade.evaluated_projection):
        fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Upstream trade projection is inconsistent")
    if snapshot.equity <= ZERO:
        fail(RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE, "Positive portfolio equity is required")
    if snapshot.available_cash > snapshot.equity:
        fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Unleveraged available capital cannot exceed equity")
    if intent.direction is Direction.SHORT and intent.scope.environment not in (Environment.LAB, Environment.PAPER):
        fail(RiskReason.SHORT_RESEARCH_ONLY, "Short exposure is research-only in this capital model")
    for row in context.correlations:
        if row.as_of > at or at >= row.expires_at or at - row.as_of > policy.operating_correlation_max_age:
            fail(RiskReason.CORRELATION_EVIDENCE_STALE, "Supplied correlation evidence is future-dated, expired or too old")


def validate_positions(intent, snapshot, context):
    required = (snapshot.open_positions, snapshot.open_risk, snapshot.gross_exposure, snapshot.long_exposure, snapshot.short_exposure)
    if any(value is None for value in required):
        fail(RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE, "Complete positions and known aggregate risk/exposure are required")
    attributions = {row.configuration_identity: row for row in context.strategy_attributions}
    proposing = attributions.get(intent.strategy.configuration_identity)
    if proposing is None:
        fail(RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE, "Proposing strategy attribution is unavailable")
    if (proposing.family, proposing.version) != (intent.strategy.family, intent.strategy.version):
        fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Proposing strategy attribution does not match its definition")
    by_symbol, by_uuid = {intent.instrument.canonical_symbol: intent.instrument.instrument_id}, {intent.instrument.instrument_id: intent.instrument.canonical_symbol}
    for p in snapshot.open_positions:
        if p.exposure_amount is None or p.open_risk_amount is None:
            fail(RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE, "Every position requires known exposure and modeled open risk")
        if p.canonical_symbol.split(":")[0].split("/")[1] != context.valuation_currency:
            fail(RiskReason.PORTFOLIO_VALUATION_INCOMPATIBLE, "Cross-currency position aggregation is unsupported")
        if p.strategy_configuration_identity not in attributions:
            fail(RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE, "Existing position strategy attribution is unavailable")
        if p.exposure_amount <= ZERO or (p.mark_price is not None and p.exposure_amount < mul(p.quantity, p.mark_price)):
            fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Position exposure cannot understate a known positive mark/quantity")
        if (p.canonical_symbol in by_symbol and by_symbol[p.canonical_symbol] != p.instrument_id
                or p.instrument_id in by_uuid and by_uuid[p.instrument_id] != p.canonical_symbol):
            fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Instrument UUID/symbol attribution is inconsistent")
        by_symbol[p.canonical_symbol], by_uuid[p.instrument_id] = p.instrument_id, p.canonical_symbol
        if p.direction is Direction.SHORT and intent.scope.environment not in (Environment.LAB, Environment.PAPER):
            fail(RiskReason.SHORT_RESEARCH_ONLY, "Existing short exposure is incompatible with this environment")
    rows = snapshot.open_positions
    expected = {
        "open_risk": add(p.open_risk_amount for p in rows), "gross_exposure": add(p.exposure_amount for p in rows),
        "long_exposure": add(p.exposure_amount for p in rows if p.direction is Direction.LONG),
        "short_exposure": add(p.exposure_amount for p in rows if p.direction is Direction.SHORT),
    }
    if any(getattr(snapshot, name) != value for name, value in expected.items()):
        fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Portfolio aggregate totals do not reconcile with complete positions")
    return attributions


def verify_optional_concentrations(snapshot, instruments, configurations):
    for provided, amounts in ((snapshot.instrument_concentration, instruments), (snapshot.strategy_concentration, configurations)):
        if provided is None:
            continue  # derive from the required complete inventory, never from missing-value substitution
        for key in sorted(set(provided) | set(amounts)):
            if key in provided and provided[key] is None:
                fail(RiskReason.REQUIRED_PORTFOLIO_EVIDENCE_UNAVAILABLE, "Supplied concentration evidence contains unknown values")
            if provided.get(key) != ratio(amounts.get(key, ZERO), snapshot.equity):
                fail(RiskReason.PORTFOLIO_EVIDENCE_INCONSISTENT, "Supplied concentration ratios disagree with the complete inventory")
