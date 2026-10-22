"""Lossless rows built solely from validated 11A/11B artifacts; no pricing here."""
from app.risk.canonical import to_wire, values
from app.strategy_lab.canonical import canonical_json
from ..canonical import execution_identity
from ..request import ApprovalConsumptionBinding


def audit_fields(kind, value):
    return dict(payload_identity=execution_identity("storage-"+kind+"-1.0.0", value),
                canonical_payload=canonical_json(values(value)))


def request_row(r):
    i=r.authorization.intent
    return dict(request_identity=r.request_identity, action_identity=r.action_identity,
        intent_identity=r.intent_identity, decision_identity=r.risk_decision_identity,
        client_order_identity=r.client_order_identity, order_identity=r.order_identity,
        environment=r.environment.value, user_id=i.scope.user_id, portfolio_id=i.scope.portfolio_id,
        instrument_id=i.instrument.instrument_id, canonical_symbol=i.instrument.canonical_symbol,
        provider=i.instrument.provider, timeframe=i.instrument.timeframe, direction=i.direction.value,
        side=r.side.value, action=r.action.value, venue_identity=r.venue_identity,
        quantity=r.quantity, created_at=r.created_at, approval_expires_at=r.authorization.decision.approval_expires_at,
        evidence=to_wire(r), **audit_fields("request",r))


def consumption_row(r, at):
    b=ApprovalConsumptionBinding(r); i=r.authorization.intent
    payload=dict(binding=b,consumed_at=at)
    return dict(consumption_identity=b.consumption_key, decision_identity=r.risk_decision_identity,
        request_identity=r.request_identity, intent_identity=r.intent_identity, action_identity=r.action_identity,
        environment=r.environment.value, user_id=i.scope.user_id, portfolio_id=i.scope.portfolio_id,
        consumed_at=at, approval_expires_at=r.authorization.decision.approval_expires_at,
        **audit_fields("consumption",payload))


def order_row(o):
    return dict(order_identity=o.order_identity, request_identity=o.request.request_identity,
        client_order_identity=o.request.client_order_identity, observation_identity=o.observation_identity,
        state=o.state.value, observed_at=o.observed_at, accepted_at=o.accepted_at,
        venue_order_identity=o.venue_order_identity, evidence=to_wire(o), **audit_fields("order",o))


def fill_row(a):
    f,c=a.fill,a.calculation; leg=c.leg
    return dict(fill_identity=f.fill_identity, order_identity=f.order_identity, request_identity=f.request_identity,
        observation_identity=f.observation_identity, calculation_identity=c.calculation_identity,
        market_evidence_identity=c.market.evidence_identity, venue_fill_identity=f.venue_fill_identity,
        quantity=f.quantity, reference_price=leg.reference_price, execution_price=f.price,
        spread_impact=leg.spread_impact_per_unit, slippage_impact=leg.slippage_impact_per_unit,
        reference_notional=leg.reference_notional, gross_notional=leg.gross_notional, fee=leg.fee,
        spread_cost=leg.spread_cost, slippage_cost=leg.slippage_cost, net_cash_effect=leg.net_cash_effect,
        filled_at=f.filled_at, evidence=to_wire(a), **audit_fields("fill",a))
