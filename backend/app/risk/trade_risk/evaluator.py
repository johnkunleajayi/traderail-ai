"""One mandatory gate's evidence only. Never constructs a final RiskDecision."""
from datetime import datetime

from app.risk.canonical import timestamp
from app.risk.reasons import RiskFinding, RiskReason
from app.risk.vocabulary import RiskOutcome
from .arithmetic import floor_increment
from .calculation import calculate_trade_size, project_size, require_inputs
from .models import SizingConstraint, TradeRiskCalculationError, TradeRiskEvaluation, TradeRiskStatus


def evaluate_trade_risk(intent, configuration, snapshot, assumptions, evaluated_at: datetime) -> TradeRiskEvaluation:
    require_inputs(intent, configuration, snapshot, assumptions)
    at = timestamp(evaluated_at)
    common = dict(intent_identity=intent.intent_identity, configuration_identity=configuration.configuration_identity,
        snapshot_identity=snapshot.snapshot_identity, assumptions_identity=assumptions.assumptions_identity, evaluated_at=at)
    findings = []
    try:
        if at < max(intent.created_at, snapshot.as_of, assumptions.entry_context.observed_at):
            raise TradeRiskCalculationError(RiskReason.RISK_CONTEXT_MISMATCH,
                                            "Evaluation cannot precede any required input")
        if at >= min(intent.expires_at, intent.market_context.expires_at, snapshot.expires_at, assumptions.entry_context.expires_at):
            raise TradeRiskCalculationError(RiskReason.CONTEXT_EXPIRED, "Trade-risk input context has expired")
        sizing = calculate_trade_size(intent, configuration, snapshot, assumptions)
        quantity = sizing.constrained_max_quantity if assumptions.proposed_quantity is None else assumptions.proposed_quantity
        projection = project_size(quantity, sizing.loss_model)
    except TradeRiskCalculationError as error:
        return TradeRiskEvaluation(**common, status=TradeRiskStatus.TRADE_RISK_REJECTED, sizing=None,
            evaluated_projection=None, findings=(RiskFinding(error.code, {"detail": str(error)}),))
    except ArithmeticError:
        return TradeRiskEvaluation(**common, status=TradeRiskStatus.TRADE_RISK_REJECTED, sizing=None,
            evaluated_projection=None, findings=(RiskFinding(RiskReason.NUMERIC_RISK_CALCULATION_UNAVAILABLE,
                {"detail": "Finite conservative risk arithmetic could not be completed"}),))

    def record(code, **evidence):
        findings.append(RiskFinding(code, evidence))

    if sizing.permitted_monetary_risk > 0:
        record(RiskReason.RISK_BUDGET_AVAILABLE, permitted_monetary_risk=sizing.permitted_monetary_risk)
    else:
        record(RiskReason.RISK_BUDGET_UNAVAILABLE, permitted_monetary_risk=sizing.permitted_monetary_risk)
    if sizing.constrained_max_quantity == 0:
        record(RiskReason.ZERO_SAFE_QUANTITY, safe_base_amount=sizing.constrained_max_quantity)
    if projection.modeled_loss > sizing.permitted_monetary_risk:
        record(RiskReason.TRADE_RISK_LIMIT_EXCEEDED, observed=projection.modeled_loss, required=sizing.permitted_monetary_risk)
    if projection.notional > sizing.notional_limit:
        record(RiskReason.NOTIONAL_LIMIT_EXCEEDED, observed=projection.notional, required=sizing.notional_limit)
    if projection.required_cash > sizing.available_cash_limit:
        record(RiskReason.CASH_LIMIT_EXCEEDED, observed=projection.required_cash, required=sizing.available_cash_limit)
    c = assumptions.constraints
    if (quantity > sizing.constrained_max_quantity
            or (c.minimum_quantity is not None and quantity < c.minimum_quantity)
            or (c.quantity_increment is not None and floor_increment(quantity, c.quantity_increment) != quantity)):
        record(RiskReason.QUANTITY_CONSTRAINT_VIOLATED, proposed_base_amount=quantity,
               conservative_maximum=sizing.constrained_max_quantity)
    for binding, code in ((SizingConstraint.NOTIONAL_LIMIT, RiskReason.NOTIONAL_LIMIT_BINDING),
                          (SizingConstraint.AVAILABLE_CASH, RiskReason.CASH_LIMIT_BINDING),
                          (SizingConstraint.MAXIMUM_QUANTITY, RiskReason.QUANTITY_LIMIT_BINDING),
                          (SizingConstraint.MINIMUM_QUANTITY, RiskReason.QUANTITY_LIMIT_BINDING),
                          (SizingConstraint.QUANTITY_INCREMENT, RiskReason.QUANTITY_INCREMENT_APPLIED)):
        if binding in sizing.binding_constraints and not any(f.code is code for f in findings):
            record(code, constrained_base_amount=sizing.constrained_max_quantity)
    rejected = any(f.code.outcome is RiskOutcome.REJECTED for f in findings)
    if not rejected:
        record(RiskReason.TRADE_RISK_WITHIN_LIMIT, modeled_loss=projection.modeled_loss,
               permitted_monetary_risk=sizing.permitted_monetary_risk)
    return TradeRiskEvaluation(**common, status=(TradeRiskStatus.TRADE_RISK_REJECTED if rejected else TradeRiskStatus.TRADE_RISK_PASS),
        sizing=sizing, evaluated_projection=projection, findings=tuple(findings))
