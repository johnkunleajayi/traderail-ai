"""Evaluate all applicable switches without hiding simultaneous blockers."""
from app.risk.reasons import RiskReason as R
from app.risk.vocabulary import KillSwitchScope

_ACTIVE = {KillSwitchScope.USER:R.USER_KILL_SWITCH_ACTIVE, KillSwitchScope.PORTFOLIO:R.PORTFOLIO_KILL_SWITCH_ACTIVE,
    KillSwitchScope.STRATEGY:R.STRATEGY_KILL_SWITCH_ACTIVE, KillSwitchScope.MARKET:R.MARKET_KILL_SWITCH_ACTIVE,
    KillSwitchScope.DATA:R.DATA_KILL_SWITCH_ACTIVE, KillSwitchScope.SYSTEM:R.SYSTEM_KILL_SWITCH_ACTIVE}


def evaluate_switches(intent,inventory,policy,at,findings):
    rejected=False
    def reject(code,**row):
        nonlocal rejected
        rejected=True
        findings.add(code,**row)
    if (inventory.intent_identity!=intent.intent_identity or inventory.scope!=intent.scope
            or inventory.market_context_identity!=intent.market_context.context_identity):
        reject(R.SWITCH_EVIDENCE_MISMATCH,kind="INVENTORY",identity=inventory.inventory_identity)
    missing=tuple(sorted(scope.value for scope in KillSwitchScope if scope not in inventory.covered_scopes))
    if missing:
        reject(R.SWITCH_EVIDENCE_UNAVAILABLE,kind="COVERAGE",missing_scopes=missing)
    if inventory.as_of>at:
        reject(R.SWITCH_EVIDENCE_FUTURE,kind="INVENTORY",identity=inventory.inventory_identity)
    elif at>=inventory.valid_until or at-inventory.as_of>policy.max_inventory_age:
        reject(R.SWITCH_EVIDENCE_STALE,kind="INVENTORY",identity=inventory.inventory_identity)
    applicable=[];blocking=[]
    for row in inventory.switches:
        if row.environment is not intent.scope.environment:
            continue
        if row.target.conflicts_with(intent):
            reject(R.SWITCH_EVIDENCE_MISMATCH,kind="TARGET_ATTRIBUTION",switch_identity=str(row.switch_id),identity=row.evidence_identity)
            continue
        if not row.target.matches(intent):
            continue
        applicable.append(row)
        detail=dict(kind="SWITCH",switch_identity=str(row.switch_id),identity=row.evidence_identity)
        if row.as_of>at:
            reject(R.SWITCH_EVIDENCE_FUTURE,**detail)
        elif at>=row.valid_until or at-row.as_of>policy.max_switch_age:
            reject(R.SWITCH_EVIDENCE_STALE,**detail)
        if row.active is None:
            reject(R.SWITCH_EVIDENCE_UNAVAILABLE,**detail)
        if (row.active is True and row.as_of<=at and row.activated_at<=at
                and (row.expires_at is None or at<row.expires_at)):
            blocking.append(row)
            reject(_ACTIVE[row.target.scope],**detail,cause=row.cause.value)
    if not rejected:
        findings.add(R.KILL_SWITCH_CLEAR,applicable_count=len(applicable),inventory_identity=inventory.inventory_identity)
    return tuple(applicable),tuple(blocking),not rejected
