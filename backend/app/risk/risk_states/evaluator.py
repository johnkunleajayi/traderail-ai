"""Pure new-exposure gate; no wall clock, database, providers or position actions."""
from app.risk.canonical import require, timestamp
from app.risk.configuration import RiskConfiguration
from app.risk.portfolio import PortfolioRiskSnapshot
from app.risk.reasons import RiskFinding, RiskReason as R
from app.risk.vocabulary import RiskState
from app.strategy_lab.numeric import exact_product
from .context import DrawdownEvidence, NewRiskCapacity
from .measurement import DrawdownMeasurement, transition_state
from .models import RiskStateEvaluation, RiskStateProjection, RiskStateStatus as S
from .policy import RiskStatePolicy


class _Rejected(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def _reject(code, detail):
    raise _Rejected(code, detail)


def _validate(snapshot, configuration, policy, evidence, capacity, at):
    for value, kind in ((snapshot, PortfolioRiskSnapshot), (configuration, RiskConfiguration),
                        (policy, RiskStatePolicy), (evidence, DrawdownEvidence), (capacity, NewRiskCapacity)):
        if type(value) is not kind:
            _reject(R.DRAWDOWN_EVIDENCE_UNAVAILABLE, "Complete immutable state-gate inputs are required")
    if (evidence.scope != snapshot.scope or capacity.scope != snapshot.scope
            or configuration.environment is not snapshot.scope.environment
            or evidence.snapshot_identity != snapshot.snapshot_identity
            or capacity.snapshot_identity != snapshot.snapshot_identity
            or capacity.configuration_identity != configuration.configuration_identity
            or evidence.valuation_identity != snapshot.valuation_identity
            or evidence.previous_state is not snapshot.risk_state or evidence.as_of != snapshot.as_of):
        _reject(R.DRAWDOWN_CONTEXT_MISMATCH, "Portfolio, environment, valuation, previous state and snapshot must match exactly")
    if at < evidence.as_of or at < snapshot.as_of:
        _reject(R.DRAWDOWN_EVIDENCE_FUTURE, "Evidence cannot be observed after the explicit evaluation instant")
    if at >= min(evidence.expires_at, snapshot.expires_at) or at - evidence.as_of > policy.max_evidence_age:
        _reject(R.DRAWDOWN_EVIDENCE_STALE, "Drawdown evidence is expired or exceeds the declared maximum age")
    if evidence.current_equity != snapshot.equity:
        _reject(R.DRAWDOWN_EQUITY_MISMATCH, "Sizing equity and drawdown equity must reconcile exactly")
    if evidence.historical_peak == 0:
        _reject(R.INVALID_PEAK_EQUITY, "A positive trusted historical peak is required; zero peak is not zero drawdown")
    ceiling = min(configuration.operating_limits.max_drawdown_fraction, configuration.platform_hard_limits.max_drawdown_fraction)
    if policy.suspended_entry > ceiling:
        _reject(R.INVALID_STATE_POLICY, "State suspension cannot exceed the operating/platform drawdown ceiling")
    base_ceiling = exact_product(snapshot.equity, min(configuration.operating_limits.max_trade_risk_fraction,
                                                   configuration.platform_hard_limits.max_trade_risk_fraction))
    if capacity.base_permitted_risk > base_ceiling:
        _reject(R.STATE_BASE_CAPACITY_INVALID, "Base capacity cannot exceed the configured trade-risk ceiling")


def evaluate_risk_state(snapshot, configuration, policy, evidence, capacity, evaluated_at):
    at = timestamp(evaluated_at)  # Explicit aware instant is mandatory, even for rejected inputs.
    inputs = ((snapshot, PortfolioRiskSnapshot, "snapshot_identity"),
              (configuration, RiskConfiguration, "configuration_identity"), (policy, RiskStatePolicy, "policy_identity"),
              (evidence, DrawdownEvidence, "evidence_identity"), (capacity, NewRiskCapacity, "capacity_identity"))
    common = {name: getattr(value, name) if type(value) is kind else None for value, kind, name in inputs}
    common["evaluated_at"] = at
    try:
        _validate(snapshot, configuration, policy, evidence, capacity, at)
        measurement = DrawdownMeasurement(evidence.historical_peak, evidence.current_equity)
        if snapshot.drawdown_fraction is not None and snapshot.drawdown_fraction != measurement.drawdown_fraction:
            _reject(R.DRAWDOWN_EQUITY_MISMATCH, "Supplied snapshot drawdown disagrees with its trusted peak/equity")
        state, transition = transition_state(measurement, evidence.previous_state, policy)
        multiplier = policy.multiplier(state)
        projection = RiskStateProjection(measurement, policy, evidence.previous_state, state, transition, multiplier,
            capacity.base_permitted_risk, exact_product(capacity.base_permitted_risk, multiplier), capacity.proposed_modeled_risk)
    except _Rejected as error:
        return RiskStateEvaluation(**common, status=S.RISK_STATE_REJECTED, projection=None,
                                   findings=(RiskFinding(error.code, {"detail": str(error)}),))
    except ArithmeticError:
        return RiskStateEvaluation(**common, status=S.RISK_STATE_REJECTED, projection=None,
            findings=(RiskFinding(R.STATE_NUMERIC_UNAVAILABLE, {"detail": "Finite state-risk arithmetic could not be completed"}),))
    active_code = {RiskState.NORMAL: R.DRAWDOWN_HEALTHY, RiskState.CAUTION: R.CAUTION_STATE_ACTIVE,
                   RiskState.DEFENSIVE: R.DEFENSIVE_STATE_ACTIVE, RiskState.SUSPENDED: R.SUSPENDED_STATE_ACTIVE}[state]
    findings = [RiskFinding(active_code, {"state": state.value, "previous_state": evidence.previous_state.value,
        "transition": transition.value, "drawdown_fraction": measurement.drawdown_fraction})]
    if multiplier < 1:
        findings.append(RiskFinding(R.NEW_RISK_REDUCED_BY_STATE, {"multiplier": multiplier}))
    if projection.proposed_modeled_risk == 0 or projection.adjusted_permitted_risk == 0:
        code = R.STATE_NEW_RISK_UNAVAILABLE
    else:
        code = R.PROPOSED_RISK_WITHIN_STATE_CAPACITY if projection.permits_new_risk else R.PROPOSED_RISK_EXCEEDS_STATE_CAPACITY
    findings.append(RiskFinding(code, {"base": projection.base_permitted_risk,
        "adjusted": projection.adjusted_permitted_risk, "proposed": projection.proposed_modeled_risk}))
    return RiskStateEvaluation(**common, status=S.RISK_STATE_PASS if projection.permits_new_risk else S.RISK_STATE_REJECTED,
                               projection=projection, findings=tuple(findings))
