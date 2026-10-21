"""Eligibility of exact attested strategy evidence plus optional explicit metric vetoes."""
from app.risk.reasons import RiskReason as R
from .health import StrategyHealth

_HEALTH_CODES={StrategyHealth.HEALTHY:R.STRATEGY_HEALTH_CONFIRMED,StrategyHealth.DEGRADED:R.STRATEGY_HEALTH_DEGRADED,
    StrategyHealth.UNHEALTHY:R.STRATEGY_HEALTH_UNHEALTHY,StrategyHealth.SUSPENDED:R.STRATEGY_HEALTH_SUSPENDED,
    StrategyHealth.UNKNOWN:R.STRATEGY_HEALTH_UNKNOWN}


def valid_strategy_evidence(intent,row,kind,maximum_age,at,findings):
    valid=True
    if (row.environment is not intent.scope.environment or row.strategy.family!=intent.strategy.family
            or row.strategy.version!=intent.strategy.version
            or row.strategy.configuration_identity!=intent.strategy.configuration_identity):
        findings.add(R.STRATEGY_EVIDENCE_MISMATCH,kind=kind,identity=row.evidence_identity)
        valid=False
    if row.as_of>at:
        findings.add(R.STRATEGY_EVIDENCE_FUTURE,kind=kind,identity=row.evidence_identity)
        valid=False
    elif at>=row.valid_until or at-row.as_of>maximum_age:
        findings.add(R.STRATEGY_EVIDENCE_STALE,kind=kind,identity=row.evidence_identity)
        valid=False
    return valid


def evaluate_lifecycle(intent,row,policy,at,findings):
    if not valid_strategy_evidence(intent,row,"LIFECYCLE",policy.max_lifecycle_age,at,findings):
        return None
    eligible=row.lifecycle.state in policy.lifecycle_by_environment[intent.scope.environment]
    findings.add(R.STRATEGY_LIFECYCLE_ELIGIBLE if eligible else R.STRATEGY_LIFECYCLE_INELIGIBLE,
        state=row.lifecycle.state.value,environment=intent.scope.environment.value)
    return eligible


def evaluate_health(intent,row,policy,at,findings):
    if not valid_strategy_evidence(intent,row,"HEALTH",policy.max_health_age,at,findings):
        return
    if at-row.data_through>policy.max_health_data_age:
        findings.add(R.STRATEGY_EVIDENCE_STALE,kind="HEALTH_DATA",identity=row.evidence_identity)
    findings.add(_HEALTH_CODES[row.state],state=row.state.value,identity=row.evidence_identity)
    if row.state not in policy.permitted_health_states:
        findings.add(R.STRATEGY_HEALTH_NOT_PERMITTED,state=row.state.value)
    metrics=row.metrics
    # Evaluate each required metric independently: no score, ranking or early exit.
    checks=(("completed_trade_count",policy.minimum_completed_trades,"MIN"),
        ("expectancy",policy.minimum_expectancy,"MIN"),("profit_factor",policy.minimum_profit_factor,"MIN"),
        ("drawdown_fraction",policy.maximum_drawdown_fraction,"MAX"),
        ("stress_degradation",policy.maximum_stress_degradation,"MAX"))
    for name,threshold,comparison in checks:
        if threshold is None:
            continue  # an explicitly unconfigured metric gate, never a fabricated observation
        value=getattr(metrics,name) if metrics is not None else None
        if value is None:
            findings.add(R.INSUFFICIENT_HEALTH_EVIDENCE,metric=name,required=threshold)
        elif name=="expectancy" and metrics.expectancy_currency is None:
            findings.add(R.INSUFFICIENT_HEALTH_EVIDENCE,metric="expectancy_currency",required=policy.expectancy_currency)
        elif name=="expectancy" and metrics.expectancy_currency!=policy.expectancy_currency:
            findings.add(R.STRATEGY_EVIDENCE_MISMATCH,kind="HEALTH_EXPECTANCY_CURRENCY",
                         observed=metrics.expectancy_currency,required=policy.expectancy_currency)
        elif (value<threshold if comparison=="MIN" else value>threshold):
            findings.add(R.HEALTH_METRIC_THRESHOLD_FAILED,metric=name,observed=value,required=threshold,comparison=comparison)
    if policy.require_data_sufficient:
        value=metrics.data_sufficient if metrics is not None else None
        if value is None:
            findings.add(R.INSUFFICIENT_HEALTH_EVIDENCE,metric="data_sufficient",required=True)
        elif value is False:
            findings.add(R.HEALTH_METRIC_THRESHOLD_FAILED,metric="data_sufficient",observed=False,required=True)
    if policy.required_validation_verdicts is not None:
        verdict=metrics.validation_verdict if metrics is not None else None
        if verdict is None:
            findings.add(R.INSUFFICIENT_HEALTH_EVIDENCE,metric="validation_verdict",required=tuple(v.value for v in policy.required_validation_verdicts))
        elif verdict not in policy.required_validation_verdicts:
            findings.add(R.HEALTH_METRIC_THRESHOLD_FAILED,metric="validation_verdict",observed=verdict.value,
                         required=tuple(v.value for v in policy.required_validation_verdicts))
