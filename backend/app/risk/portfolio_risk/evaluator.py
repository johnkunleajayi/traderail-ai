"""Portfolio gate only. Does not construct final RiskDecision or execute anything."""
from app.risk.canonical import timestamp
from app.risk.reasons import RiskFinding, RiskReason as R
from app.risk.vocabulary import RiskOutcome
from .models import PortfolioLimitKind as K, PortfolioRiskEvaluation, PortfolioRiskInputError, PortfolioRiskStatus as S
from .projection import project_portfolio
from .validation import require_inputs, validate_context

_REASONS = {
    K.OPEN_RISK: (R.AGGREGATE_OPEN_RISK_WITHIN_LIMIT, R.AGGREGATE_OPEN_RISK_EXCEEDED),
    K.GROSS_EXPOSURE: (R.PORTFOLIO_EXPOSURE_WITHIN_LIMIT, R.PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED),
    K.INSTRUMENT: (R.INSTRUMENT_CONCENTRATION_WITHIN_LIMIT, R.INSTRUMENT_CONCENTRATION_EXCEEDED),
    K.DIRECTION: (R.DIRECTIONAL_EXPOSURE_WITHIN_LIMIT, R.DIRECTIONAL_EXPOSURE_EXCEEDED),
    K.STRATEGY: (R.STRATEGY_CONCENTRATION_WITHIN_LIMIT, R.STRATEGY_CONCENTRATION_EXCEEDED),
    K.CORRELATED_EXPOSURE: (R.CORRELATED_EXPOSURE_WITHIN_LIMIT, R.CORRELATED_EXPOSURE_LIMIT_EXCEEDED),
    K.GROUP: (R.GROUP_CONCENTRATION_WITHIN_LIMIT, R.GROUP_CONCENTRATION_EXCEEDED),
    K.POSITION_COUNT: (R.PORTFOLIO_POSITION_COUNT_WITHIN_LIMIT, R.PORTFOLIO_POSITION_COUNT_EXCEEDED),
    K.CAPITAL_COMMITMENT: (R.PORTFOLIO_CAPITAL_WITHIN_LIMIT, R.PORTFOLIO_CAPITAL_EXCEEDED),
}


def evaluate_portfolio_risk(intent, trade_risk, configuration, snapshot, policy, context, evaluated_at):
    require_inputs(intent, trade_risk, configuration, snapshot, policy, context)
    at = timestamp(evaluated_at)
    common = dict(intent_identity=intent.intent_identity, trade_risk_identity=trade_risk.evaluation_identity,
        configuration_identity=configuration.configuration_identity, snapshot_identity=snapshot.snapshot_identity,
        policy_identity=policy.policy_identity, context_identity=context.context_identity, evaluated_at=at)
    try:
        validate_context(intent, trade_risk, configuration, snapshot, policy, context, at)
        projection = project_portfolio(intent, trade_risk, configuration, snapshot, policy, context)
    except PortfolioRiskInputError as error:
        return PortfolioRiskEvaluation(**common, status=S.PORTFOLIO_RISK_REJECTED, projection=None,
                                       findings=(RiskFinding(error.code, {"detail": str(error)}),))
    except ArithmeticError:
        return PortfolioRiskEvaluation(**common, status=S.PORTFOLIO_RISK_REJECTED, projection=None,
            findings=(RiskFinding(R.PORTFOLIO_NUMERIC_CALCULATION_UNAVAILABLE,
                {"detail": "Finite portfolio-risk arithmetic could not be completed"}),))
    grouped = {}
    for check in projection.checks:
        code = _REASONS[check.kind][0 if check.within_limit else 1]
        grouped.setdefault(code, []).append({"key": check.key, "before": check.before_value,
                                            "observed": check.after_value, "required": check.limit_value})
    findings = tuple(RiskFinding(code, {"checks": rows}) for code, rows in grouped.items())
    rejected = any(f.code.outcome is RiskOutcome.REJECTED for f in findings)
    return PortfolioRiskEvaluation(**common, status=S.PORTFOLIO_RISK_REJECTED if rejected else S.PORTFOLIO_RISK_PASS,
                                   projection=projection, findings=findings)
