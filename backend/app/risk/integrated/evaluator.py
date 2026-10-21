"""Fixed mandatory-gate orchestration. Terminates at the existing RiskDecision."""
from collections import defaultdict

from app.risk.canonical import require,values
from app.risk.decision import RiskDecision
from app.risk.reasons import RiskFinding,RiskReason as R
from app.risk.vocabulary import RiskOutcome
from app.risk.trade_risk import evaluate_trade_risk
from app.risk.portfolio_risk import evaluate_portfolio_risk
from app.risk.risk_states import NewRiskCapacity,evaluate_risk_state
from app.risk.safety_gate import evaluate_safety_gate
from app.strategy_lab.canonical import canonical_json
from .inputs import RiskEvaluationInputs
from .models import (INTEGRATED_RISK_VERSION,MANDATORY_GATES,RESULT_TYPES,PASS_STATUSES,
                     GateName as G,GateDisposition as D,GateFinding,GateRecord)
from .reconciliation import root_mismatches,result_mismatches
from .expiry import derive_approval_validity


def _capacity(i,trade):
    # Bind the actual proposal, not its maximum size or any caller-supplied smaller loss.
    return NewRiskCapacity(i.intent.scope,i.snapshot.snapshot_identity,i.configuration.configuration_identity,
        i.intent.intent_identity,trade.evaluation_identity,trade.sizing.permitted_monetary_risk,
        trade.evaluated_projection.modeled_loss)


def _blocked(gate,code,**detail):
    reason=RiskFinding(code,values(detail))
    return GateRecord(gate,D.BLOCKED,None,(GateFinding(gate,D.BLOCKED,reason,None),))


def _record(i,gate,result,trade=None,capacity=None):
    if type(result) is not RESULT_TYPES[gate]:
        return _blocked(gate,R.INTEGRATED_EVIDENCE_UNAVAILABLE,kind="UNEXPECTED_GATE_RESULT")
    mismatches=result_mismatches(i,gate,result,trade,capacity)
    reasons=list(result.findings)
    if mismatches:
        reasons.append(RiskFinding(R.INTEGRATED_CONTEXT_MISMATCH,{"checks":mismatches}))
    disposition=(D.BLOCKED if mismatches else D.PASS if result.status is PASS_STATUSES[gate] else D.REJECTED)
    return GateRecord(gate,disposition,result,tuple(GateFinding(gate,disposition,r,result.evaluation_identity) for r in reasons))


def _invoke(i,gate,call,trade=None,capacity=None):
    try:
        return _record(i,gate,call(),trade,capacity)
    except Exception as error:
        # A gate defect/unavailable calculation cannot become permission. Never expose error text.
        return _blocked(gate,R.INTEGRATED_GATE_BLOCKED,kind="EVALUATION_UNAVAILABLE",error_type=type(error).__name__)


def _collect(i):
    if i.sizing_assumptions is None:
        trade=_blocked(G.TRADE_RISK,R.INTEGRATED_EVIDENCE_UNAVAILABLE,missing=("sizing_assumptions",))
    else:
        trade=_invoke(i,G.TRADE_RISK,lambda:evaluate_trade_risk(i.intent,i.configuration,i.snapshot,i.sizing_assumptions,i.evaluated_at))
    if trade.disposition is not D.PASS:
        portfolio=_blocked(G.PORTFOLIO_RISK,R.INTEGRATED_GATE_BLOCKED,prerequisite=G.TRADE_RISK.value)
        state=_blocked(G.RISK_STATE,R.INTEGRATED_GATE_BLOCKED,prerequisite=G.TRADE_RISK.value)
    else:
        source=trade.result
        missing=tuple(n for n in ("portfolio_policy","portfolio_context") if getattr(i,n) is None)
        if missing:
            portfolio=_blocked(G.PORTFOLIO_RISK,R.INTEGRATED_EVIDENCE_UNAVAILABLE,missing=missing)
        else:
            portfolio=_invoke(i,G.PORTFOLIO_RISK,lambda:evaluate_portfolio_risk(i.intent,source,i.configuration,
                i.snapshot,i.portfolio_policy,i.portfolio_context,i.evaluated_at),source)
        try:
            capacity=_capacity(i,source)
            state=_invoke(i,G.RISK_STATE,lambda:evaluate_risk_state(i.snapshot,i.configuration,i.state_policy,
                i.drawdown_evidence,capacity,i.evaluated_at),source,capacity)
        except Exception as error:
            state=_blocked(G.RISK_STATE,R.INTEGRATED_GATE_BLOCKED,kind="CAPACITY_UNAVAILABLE",error_type=type(error).__name__)
    # Independent safety vetoes remain inspectable even if financial prerequisites failed.
    safety=_invoke(i,G.SAFETY_GATE,lambda:evaluate_safety_gate(i.intent,i.configuration,i.safety_policy,
        i.switch_inventory,i.lifecycle_evidence,i.health_evidence,i.evaluated_at))
    return (trade,portfolio,state,safety)


def _finalize(i,records):
    # Internal finalizer is defensive as well: there is no public precomputed-result input.
    records=tuple(sorted(records,key=lambda r:(r.gate.value,r.record_identity)))
    root=[]
    checks=root_mismatches(i)
    if checks: root.append(RiskFinding(R.INTEGRATED_CONTEXT_MISMATCH,{"checks":checks}))
    coverage=tuple(r.gate for r in records)
    if len(coverage)!=len(MANDATORY_GATES) or set(coverage)!=set(MANDATORY_GATES):
        root.append(RiskFinding(R.INTEGRATED_GATE_BLOCKED,{"kind":"MANDATORY_GATE_COVERAGE",
            "required":tuple(g.value for g in MANDATORY_GATES),"observed":tuple(g.value for g in coverage)}))
    by_gate={r.gate:r for r in records}
    trade=by_gate.get(G.TRADE_RISK)
    trade_source=trade.result if trade is not None and trade.disposition is D.PASS else None
    capacity=_capacity(i,trade_source) if trade_source is not None else None
    # Recheck supplied records against this exact boundary, even if each passes in isolation.
    for row in records:
        if row.disposition is not D.PASS: continue
        try:
            mismatches=result_mismatches(i,row.gate,row.result,trade_source,capacity)
        except Exception:
            mismatches=({"context":"UNRECONCILABLE_GATE_INPUT"},)
        if mismatches: root.append(RiskFinding(R.INTEGRATED_CONTEXT_MISMATCH,{"gate":row.gate.value,"checks":mismatches}))
    all_pass=(len(coverage)==len(MANDATORY_GATES) and set(coverage)==set(MANDATORY_GATES)
              and all(row.disposition is D.PASS for row in records) and not root)
    validity=None
    if all_pass:
        try:
            validity=derive_approval_validity(i,by_gate[G.SAFETY_GATE].result)
            root.append(RiskFinding(R.INTEGRATED_APPROVAL_EXPIRY_DERIVED,values({"validity_identity":validity.validity_identity,
                "expires_at":validity.expires_at,"boundaries":[{"name":b.name,"valid_until":b.valid_until,
                "evidence_identity":b.evidence_identity} for b in validity.boundaries]})))
        except Exception as error:
            root.append(RiskFinding(R.INTEGRATED_APPROVAL_EXPIRY_UNAVAILABLE,{"error_type":type(error).__name__}))
    if all_pass and validity is not None:
        root.append(RiskFinding(R.INTEGRATED_GATE_COVERAGE_CONFIRMED,{"mandatory_gates":tuple(g.value for g in MANDATORY_GATES)}))
    manifest={"version":INTEGRATED_RISK_VERSION,"inputs_identity":i.inputs_identity,
        "policies":{"portfolio":i.portfolio_policy.policy_identity if i.portfolio_policy is not None else None,
                    "risk_state":i.state_policy.policy_identity if i.state_policy is not None else None,
                    "safety":i.safety_policy.policy_identity if i.safety_policy is not None else None},
        "gates":[{"gate":row.gate.value,"disposition":row.disposition.value,"record_identity":row.record_identity,
            "source_result_identity":row.result.evaluation_identity if row.result is not None else None,
            "source_outcome":row.result.status.value if row.result is not None else None,
            "source_scope":row.result.scope if row.result is not None else None} for row in records]}
    root.append(RiskFinding(R.INTEGRATED_EVALUATION_RECORDED,manifest))
    grouped=defaultdict(list)
    for row in records:
        for f in row.findings:
            grouped[f.reason.code].append({"gate":f.gate.value,"gate_outcome":f.gate_disposition.value,
                "blocking":f.blocking,"source_result_identity":f.source_result_identity,
                "source_finding_identity":f.reason.finding_identity,"gate_finding_identity":f.finding_identity,
                "source_evidence":f.reason.evidence})
    for finding in root:
        grouped[finding.code].append({"gate":"INTEGRATION","blocking":finding.code.outcome is RiskOutcome.REJECTED,
                                     "source_finding_identity":finding.finding_identity,"source_evidence":finding.evidence})
    findings=tuple(RiskFinding(code,{"gate_findings":tuple(sorted(grouped[code],key=canonical_json))})
                   for code in sorted(grouped,key=lambda code:code.value))
    rejected=any(f.code.outcome is RiskOutcome.REJECTED for f in findings)
    return RiskDecision(i.intent,i.configuration,i.snapshot,i.market_context,i.evaluated_at,
        RiskOutcome.REJECTED if rejected else RiskOutcome.APPROVED,findings,
        None if rejected else validity.expires_at)


def evaluate_risk(inputs):
    """Return the existing exact-intent RiskDecision; never execute or persist it."""
    require(inputs,RiskEvaluationInputs,"integrated inputs")
    return _finalize(inputs,_collect(inputs))
