"""Read-only reconciliation of existing registry/receipt/position relationships.

Never evaluates Risk/strategy, prices a fill, consumes approval, or writes a row.
"""
from collections.abc import Mapping
from uuid import UUID
from sqlalchemy import select
from app.execution.registry.models import authorizations, commands
from app.execution.registry.repository import load_authorization, load_command
from app.execution.paper.models import positions, accounting
from app.execution.paper.contracts import restore_result
from app.execution.persistence import models as artifacts
from app.execution.persistence.records import request_row, consumption_row, order_row
from app.execution.request import ExecutionRequest
from app.execution.fills import Fill
from app.execution.orders import Order
from app.execution.vocabulary import OrderState
from app.risk.canonical import to_wire, decimal
from app.strategy_lab.canonical import canonical_json
from app.strategy_lab.numeric import exact_product, difference
from .contracts import Leg, Trade, ReportingUnavailable


def require(condition):
    if not condition:
        raise ReportingUnavailable('ATTRIBUTION_RECONCILIATION_REQUIRED')


def row(c, table, condition):
    return c.execute(select(table).where(condition)).mappings().one_or_none()


def leg(c, package, user_id, position):
    a=package.authorization
    request=ExecutionRequest(a,'PAPER_INTERNAL',a.decision.evaluated_at)
    rid=request.request_identity
    receipt=row(c,accounting,accounting.c.request_identity==rid)
    command=row(c,commands,commands.c.request_identity==rid)
    fact=row(c,artifacts.requests,artifacts.requests.c.request_identity==rid)
    consumed=row(c,artifacts.consumptions,artifacts.consumptions.c.request_identity==rid)
    order=row(c,artifacts.orders,artifacts.orders.c.request_identity==rid)
    fills=c.execute(select(artifacts.fills).where(artifacts.fills.c.request_identity==rid)).mappings().all()
    if not any((receipt,command,fact,consumed,order,fills)):
        return None
    require(receipt is not None and command is not None and fact is not None and consumed is not None
        and order is not None and len(fills)==1 and position is not None)
    original=load_command(c,rid,user_id=user_id)
    require(original.request==request and original.market.request_identity==rid
        and original.market.instrument==a.intent.instrument
        and original.market.source_identity==package.evidence_references.market_source_identity
        and original.market.reference_price==package.inputs.sizing_assumptions.entry_reference_price)
    result=restore_result(receipt['evidence'])
    require(all(receipt[k]==v for k,v in vars_result(result).items())
        and receipt['canonical_payload']==canonical_json(to_wire(result))
        and result.command_identity==original.command_identity and result.position_id==position['position_id']
        and result.before_state_identity==package.inputs.snapshot.valuation_identity
        and result.quantity==a.quantity and result.action==request.action.value
        and receipt['user_id']==user_id and receipt['portfolio_id']==a.intent.scope.portfolio_id
        and receipt['environment']=='PAPER' and receipt['currency']=='USDT'
        and result.request_identity==rid and result.intent_identity==request.intent_identity
        and result.decision_identity==request.risk_decision_identity and result.order_identity==request.order_identity)
    require(all(fact[k]==v for k,v in request_row(request).items()))
    require(all(consumed[k]==v for k,v in consumption_row(request,consumed['consumed_at']).items()))
    f=fills[0]
    fill=Fill(request,f['venue_fill_identity'],f['quantity'],f['execution_price'],f['filled_at'])
    require(f['fill_identity']==result.fill_identity==fill.fill_identity and f['order_identity']==request.order_identity
        and f['observation_identity']==fill.observation_identity and f['quantity']==result.quantity
        and f['execution_price']==result.execution_price and f['gross_notional']==result.gross_notional
        and f['reference_notional']==exact_product(f['quantity'],f['reference_price'])
        and f['gross_notional']==exact_product(f['quantity'],f['execution_price'])
        and f['net_cash_effect']==difference(result.cash_after,result.cash_before)
        and f['fee']==result.fee and f['filled_at']==result.executed_at==original.executed_at
        and f['reference_price']==original.market.reference_price
        and f['market_evidence_identity']==original.market.evidence_identity
        and f['evidence']['fill']==to_wire(fill))
    observation=Order(request,OrderState.FILLED,order['observed_at'],order['accepted_at'],order['venue_order_identity'],(fill,))
    require(all(order[k]==v for k,v in order_row(observation).items()))
    reasons=a.intent.entry_context.get('reason_codes')
    require(reasons is None or (isinstance(reasons,(tuple,list)) and all(isinstance(x,str) for x in reasons)))
    return (Leg(intent_identity=request.intent_identity,decision_identity=request.risk_decision_identity,
        strategy_decision_identity=a.intent.strategy_decision_identity,strategy_input_identity=a.intent.strategy_input_identity,
        strategy_reason_codes=None if reasons is None else tuple(reasons),registry_identity=package.registry_identity,
        authorization_identity=a.authorization_identity,request_identity=rid,client_order_identity=request.client_order_identity,
        order_identity=request.order_identity,fill_identity=fill.fill_identity,accounting_identity=result.result_identity,
        market_evidence_identity=f['market_evidence_identity'],executed_at=result.executed_at,quantity=result.quantity,
        reference_price=f['reference_price'],execution_price=result.execution_price,reference_notional=f['reference_notional'],gross_notional=result.gross_notional,fee=result.fee),result)


def vars_result(result):
    from dataclasses import fields
    return {f.name:getattr(result,f.name) for f in fields(result) if f.name in accounting.c}


def load_trades(c, mandate, user_id):
    ids=c.execute(select(authorizations.c.registry_identity).where(authorizations.c.user_id==user_id)
        .order_by(authorizations.c.registry_identity)).scalars().all()
    packages=tuple(load_authorization(c,key,user_id=user_id) for key in ids)
    origins={(p.authorization.intent.intent_identity,p.authorization.decision.decision_identity):p for p in packages}
    require(len(origins)==len(packages))
    inventory=c.execute(select(positions).where(positions.c.user_id==user_id).order_by(positions.c.position_identity)).mappings().all()
    attributed={}
    for position in inventory:
        origin=origins.get((position['origin_intent_identity'],position['origin_decision_identity']))
        require(origin is not None)
        provenance=origin.authorization.intent.entry_context.get('mandate_provenance')
        if provenance is None:
            continue  # Explicit non-mandate origin, not inferred ownership.
        require(isinstance(provenance,Mapping))
        require(str(UUID(provenance['mandate_identity']))==provenance['mandate_identity'])
        if provenance['mandate_identity']==str(mandate.id):
            key=origin.registry_identity
            require(key not in attributed)
            attributed[key]=position
    trades=[]
    for origin in packages:
        i=origin.authorization.intent
        provenance=i.entry_context.get('mandate_provenance')
        if provenance is None:
            continue
        require(isinstance(provenance,Mapping))
        if provenance.get('mandate_identity')!=str(mandate.id):
            continue
        require(i.scope.user_id==user_id and i.scope.environment.value=='PAPER' and i.scope.base_currency=='USDT'
            and i.direction.value=='LONG' and i.instrument.canonical_symbol==mandate.instrument
            and provenance['allocation_currency']=='USDT')
        allocation=decimal(provenance['capital_allocation'],'allocation at entry',positive=True)
        position=attributed.get(origin.registry_identity)
        entry=leg(c,origin,user_id,position)
        if entry is None:
            require(position is None)
            continue  # Approved but unused is not a trade or reserved capital.
        opening,entry_receipt=entry
        require(entry_receipt.action=='ENTER_LONG')
        require(position['opening_request_identity']==opening.request_identity
            and position['opening_order_identity']==opening.order_identity and position['opening_fill_identity']==opening.fill_identity
            and position['environment']=='PAPER' and position['currency']=='USDT' and position['direction']=='LONG'
            and position['portfolio_id']==i.scope.portfolio_id and position['instrument_id']==i.instrument.instrument_id
            and position['canonical_symbol']==i.instrument.canonical_symbol
            and (position['strategy_family'],position['strategy_version'],position['strategy_configuration_identity'])
                ==(i.strategy.family,i.strategy.version,i.strategy.configuration_identity)
            and position['opening_quantity']==opening.quantity and position['opened_at']==opening.executed_at
            and position['average_entry_price']==opening.execution_price and position['entry_cost_basis']==opening.gross_notional
            and position['entry_fee']==opening.fee and position['entry_reference_notional']==opening.reference_notional)
        closed=[]
        for candidate in packages:
            ci=candidate.authorization.intent
            if ci.entry_context.get('opening_authorization')!=origin.registry_identity:
                continue
            require(ci.scope==i.scope and ci.instrument==i.instrument and ci.strategy==i.strategy
                and ci.entry_context.get('paper_action')=='EXIT_LONG'
                and ci.entry_context.get('paper_position_identity')==position['position_identity'])
            closing=leg(c,candidate,user_id,position)
            if closing is not None:
                require(closing[0].quantity==opening.quantity)
                closed.append(closing)
        require(len(closed)<=1)
        exit_leg,exit_receipt=closed[0] if closed else (None,None)
        if position['status']=='OPEN':
            require(not closed and position['quantity']==opening.quantity and position['closed_at'] is None
                and position['closing_request_identity'] is None)
        else:
            require(position['status']=='CLOSED' and exit_leg is not None and position['quantity']==0
                and position['closing_request_identity']==exit_leg.request_identity
                and position['closed_at']==exit_leg.executed_at>=opening.executed_at
                and exit_receipt.entry_cost_basis==opening.gross_notional and exit_receipt.entry_fee_attributable==opening.fee)
        trades.append(Trade(position_id=position['position_id'],position_identity=position['position_identity'],
            instrument=i.instrument.canonical_symbol,instrument_id=i.instrument.instrument_id,strategy_family=i.strategy.family,strategy_version=i.strategy.version,
            strategy_configuration_identity=i.strategy.configuration_identity,status=position['status'],quantity=position['quantity'],
            allocation_at_entry=allocation,eligibility_identity=provenance['eligibility_identity'],
            strategy_observation_identity=provenance['strategy_observation_identity'],entry=opening,exit=exit_leg,
            gross_realized_pnl=exit_receipt.gross_realized_pnl if exit_receipt else None,
            net_realized_pnl=exit_receipt.net_realized_pnl if exit_receipt else None))
    return tuple(sorted(trades,key=lambda t:(t.entry.executed_at,str(t.position_id))))
