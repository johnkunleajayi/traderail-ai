"""10E composition only. No upstream mutation, I/O, promotion or final approval."""
from app.risk.canonical import timestamp
from app.risk.configuration import RiskConfiguration
from app.risk.intent import TradeIntent
from app.risk.reasons import RiskReason as R,RiskOutcome
from .findings import Findings
from .health import StrategyHealthEvidence,StrategyLifecycleEvidence
from .health_evaluation import evaluate_health,evaluate_lifecycle
from .kill_switches import KillSwitchInventory
from .models import KillSwitchStatus as K,SafetyGateResult,SafetyGateStatus as S
from .policy import SafetyGatePolicy
from .switch_evaluation import evaluate_switches


def evaluate_safety_gate(intent,configuration,policy,inventory,lifecycle,health,evaluated_at):
    at=timestamp(evaluated_at)
    findings=Findings()
    inputs=((intent,TradeIntent,"intent_identity"),(configuration,RiskConfiguration,"configuration_identity"),
            (policy,SafetyGatePolicy,"policy_identity"),(inventory,KillSwitchInventory,"inventory_identity"),
            (lifecycle,StrategyLifecycleEvidence,"lifecycle_identity"),(health,StrategyHealthEvidence,"health_identity"))
    common={name:(getattr(value,"evidence_identity" if name in ("lifecycle_identity","health_identity") else name)
                  if type(value) is kind else None) for value,kind,name in inputs}
    for value,kind,name in inputs:
        if type(value) is not kind:
            code=(R.SWITCH_EVIDENCE_UNAVAILABLE if kind is KillSwitchInventory else
                  R.STRATEGY_EVIDENCE_UNAVAILABLE if kind in (StrategyHealthEvidence,StrategyLifecycleEvidence) else R.SAFETY_INPUT_UNAVAILABLE)
            findings.add(code,kind=name)
    applicable=blocking=()
    switch_ok=False;eligible=None
    if type(intent) is TradeIntent:
        if type(configuration) is RiskConfiguration and configuration.environment is not intent.scope.environment:
            findings.add(R.SAFETY_CONTEXT_MISMATCH,kind="CONFIGURATION_ENVIRONMENT")
        if at<intent.created_at:
            findings.add(R.SAFETY_CONTEXT_MISMATCH,kind="FUTURE_INTENT")
        elif at>=min(intent.expires_at,intent.market_context.expires_at):
            findings.add(R.CONTEXT_EXPIRED,kind="INTENT_MARKET_CONTEXT")
        if type(policy) is SafetyGatePolicy:
            if type(inventory) is KillSwitchInventory:
                applicable,blocking,switch_ok=evaluate_switches(intent,inventory,policy,at,findings)
            if type(lifecycle) is StrategyLifecycleEvidence:
                eligible=evaluate_lifecycle(intent,lifecycle,policy,at,findings)
            if type(health) is StrategyHealthEvidence:
                evaluate_health(intent,health,policy,at,findings)
    rows=findings.freeze()
    rejected=any(row.code.outcome is RiskOutcome.REJECTED for row in rows)
    return SafetyGateResult(**common,evaluated_at=at,kill_switch_status=K.KILL_SWITCH_PASS if switch_ok else K.KILL_SWITCH_REJECTED,
        applicable_switches=applicable,active_blocking_switches=blocking,lifecycle_eligible=eligible,
        lifecycle_evidence=lifecycle if type(lifecycle) is StrategyLifecycleEvidence else None,
        health_evidence=health if type(health) is StrategyHealthEvidence else None,
        findings=rows,status=S.SAFETY_GATE_REJECTED if rejected else S.SAFETY_GATE_PASS)
