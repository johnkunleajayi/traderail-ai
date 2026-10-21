"""Identity/equality reconciliation only; no duplicated risk mathematics."""
from app.risk.canonical import values
from .models import GateName as G


def root_mismatches(i):
    checks=[]
    def equal(name,actual,expected):
        if actual!=expected: checks.append({"context":name,"observed":values(actual),"required":values(expected)})
    equal("PORTFOLIO_SCOPE",i.snapshot.scope,i.intent.scope)
    equal("CONFIGURATION_ENVIRONMENT",i.configuration.environment,i.intent.scope.environment)
    equal("MARKET_CONTEXT",i.market_context.context_identity,i.intent.market_context.context_identity)
    if i.evaluated_at<max(i.intent.created_at,i.snapshot.as_of,i.market_context.observed_at):
        checks.append({"context":"FUTURE_ROOT_INPUT","evaluation_instant":values(i.evaluated_at)})
    if i.sizing_assumptions is not None:
        equal("ENTRY_MARKET_CONTEXT",i.sizing_assumptions.entry_context.context_identity,i.market_context.context_identity)
    if i.portfolio_context is not None:
        equal("PORTFOLIO_INTENT",i.portfolio_context.intent_identity,i.intent.intent_identity)
        equal("PORTFOLIO_SNAPSHOT",i.portfolio_context.snapshot_identity,i.snapshot.snapshot_identity)
        if i.portfolio_policy is not None:
            equal("PORTFOLIO_POLICY",i.portfolio_context.policy_identity,i.portfolio_policy.policy_identity)
    if i.drawdown_evidence is not None:
        equal("DRAWDOWN_SCOPE",i.drawdown_evidence.scope,i.snapshot.scope)
        equal("DRAWDOWN_SNAPSHOT",i.drawdown_evidence.snapshot_identity,i.snapshot.snapshot_identity)
        equal("DRAWDOWN_EQUITY",i.drawdown_evidence.current_equity,i.snapshot.equity)
        equal("DRAWDOWN_PREVIOUS_STATE",i.drawdown_evidence.previous_state,i.snapshot.risk_state)
    if i.switch_inventory is not None:
        equal("SWITCH_INTENT",i.switch_inventory.intent_identity,i.intent.intent_identity)
        equal("SWITCH_SCOPE",i.switch_inventory.scope,i.intent.scope)
        equal("SWITCH_MARKET",i.switch_inventory.market_context_identity,i.market_context.context_identity)
    for name,row in (("HEALTH",i.health_evidence),("LIFECYCLE",i.lifecycle_evidence)):
        if row is None: continue
        equal(name+"_ENVIRONMENT",row.environment,i.intent.scope.environment)
        equal(name+"_FAMILY",row.strategy.family,i.intent.strategy.family)
        equal(name+"_VERSION",row.strategy.version,i.intent.strategy.version)
        equal(name+"_CONFIGURATION",row.strategy.configuration_identity,i.intent.strategy.configuration_identity)
    return tuple(sorted(checks,key=lambda row:row["context"]))


def result_mismatches(i,gate,result,trade=None,capacity=None):
    checks=[]
    def equal(name,actual,expected):
        if actual!=expected: checks.append({"context":name,"observed":values(actual),"required":values(expected)})
    equal("EVALUATION_INSTANT",result.evaluated_at,i.evaluated_at)
    equal("CONFIGURATION_IDENTITY",result.configuration_identity,i.configuration.configuration_identity)
    if gate in (G.TRADE_RISK,G.PORTFOLIO_RISK,G.SAFETY_GATE):
        equal("INTENT_IDENTITY",result.intent_identity,i.intent.intent_identity)
    if gate in (G.TRADE_RISK,G.PORTFOLIO_RISK,G.RISK_STATE):
        equal("SNAPSHOT_IDENTITY",result.snapshot_identity,i.snapshot.snapshot_identity)
    if gate is G.TRADE_RISK:
        equal("ASSUMPTIONS_IDENTITY",result.assumptions_identity,i.sizing_assumptions.assumptions_identity)
        if result.sizing is not None:
            equal("STRATEGY_REFERENCE",result.sizing.strategy_reference_price,i.intent.reference_price)
            equal("INVALIDATION",result.sizing.loss_model.invalidation_price,i.intent.invalidation_price)
        if result.evaluated_projection is not None and result.sizing is not None:
            quantity=i.sizing_assumptions.proposed_quantity
            equal("PROPOSED_BASE_AMOUNT",result.evaluated_projection.quantity,
                  result.sizing.constrained_max_quantity if quantity is None else quantity)
    elif gate is G.PORTFOLIO_RISK:
        equal("TRADE_RESULT_IDENTITY",result.trade_risk_identity,trade.evaluation_identity)
        equal("PORTFOLIO_POLICY",result.policy_identity,i.portfolio_policy.policy_identity)
        equal("PORTFOLIO_CONTEXT",result.context_identity,i.portfolio_context.context_identity)
        if result.projection is not None:
            equal("PROPOSED_RISK",result.projection.proposed_risk,trade.evaluated_projection.modeled_loss)
            equal("PROPOSED_EXPOSURE",result.projection.proposed_exposure,trade.evaluated_projection.notional)
            equal("PROPOSED_CASH",result.projection.proposed_cash_commitment,trade.evaluated_projection.required_cash)
            equal("PROPOSED_BASE_AMOUNT",result.projection.proposed_base_quantity,trade.evaluated_projection.quantity)
            equal("INSTRUMENT",result.projection.canonical_symbol,i.intent.instrument.canonical_symbol)
            equal("DIRECTION",result.projection.direction,i.intent.direction)
            equal("STRATEGY_CONFIGURATION",result.projection.strategy_configuration_identity,i.intent.strategy.configuration_identity)
    elif gate is G.RISK_STATE:
        equal("STATE_POLICY",result.policy_identity,i.state_policy.policy_identity if i.state_policy is not None else None)
        equal("DRAWDOWN_EVIDENCE",result.evidence_identity,i.drawdown_evidence.evidence_identity if i.drawdown_evidence is not None else None)
        equal("CAPACITY_IDENTITY",result.capacity_identity,capacity.capacity_identity)
        if result.projection is not None:
            equal("PROPOSED_RISK",result.projection.proposed_modeled_risk,trade.evaluated_projection.modeled_loss)
            equal("BASE_CAPACITY",result.projection.base_permitted_risk,trade.sizing.permitted_monetary_risk)
            equal("PREVIOUS_STATE",result.projection.previous_state,i.snapshot.risk_state)
            equal("CURRENT_EQUITY",result.projection.measurement.current_equity,i.snapshot.equity)
            if i.drawdown_evidence is not None:
                equal("HISTORICAL_PEAK",result.projection.measurement.historical_peak,i.drawdown_evidence.historical_peak)
    elif gate is G.SAFETY_GATE:
        for label,actual,row,attribute in (("SAFETY_POLICY",result.policy_identity,i.safety_policy,"policy_identity"),
            ("SWITCH_INVENTORY",result.inventory_identity,i.switch_inventory,"inventory_identity"),
            ("LIFECYCLE_EVIDENCE",result.lifecycle_identity,i.lifecycle_evidence,"evidence_identity"),
            ("HEALTH_EVIDENCE",result.health_identity,i.health_evidence,"evidence_identity")):
            equal(label,actual,getattr(row,attribute) if row is not None else None)
    return tuple(sorted(checks,key=lambda row:row["context"]))
