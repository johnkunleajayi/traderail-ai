"""Mandate provenance adapter for the existing first-position close lifecycle.

Strategy and Risk run before execution, in the caller's one transaction. Closed
receipts recover without a new observation, approval, or economic action.
"""
from contextlib import contextmanager
from sqlalchemy import select
from app.mandate_eligibility.service import inspect_eligibility
from app.mandate_eligibility.contracts import EligibilityStatus
from app.execution.paper.book import capture_book
from app.execution.paper.models import positions, accounting
from app.execution.persistence import models as artifacts
from app.execution.registry.models import authorizations, commands
from app.execution.registry.repository import load_authorization
from app.execution.request import ExecutionRequest
from app.execution.api.schemas import PaperExecutionRequest
from app.execution.paper.production import ProductionPaperCommandSource
from app.risk.canonical import timestamp
from app.risk.paper_source.close import assemble_close
from app.risk.paper_source.service import registered_response
from app.risk.paper_source.market import PaperEvidenceUnavailable
from app.strategies.decisions import DecisionType
from .contracts import PositionRunResult, PaperRunError, EntryExecution


def reconcile(message="Position history requires reconciliation"):
    raise PaperRunError("paper_run_reconciliation_required", message, 409)


def close_request(package, origin, position):
    a=package.authorization; i=a.intent; opening=origin.authorization.intent
    if (i.scope != opening.scope or i.instrument != opening.instrument or i.strategy != opening.strategy
        or i.direction != opening.direction or i.entry_context.get('paper_action') != 'EXIT_LONG'
        or i.entry_context.get('opening_authorization') != origin.registry_identity
        or i.entry_context.get('paper_position_identity') != position['position_identity']
        or a.quantity != position['opening_quantity'] or i.intent_identity == opening.intent_identity
        or a.decision.decision_identity == origin.authorization.decision.decision_identity):
        reconcile("Exit authorization does not match the acquired position")
    request=ExecutionRequest(a,'PAPER_INTERNAL',a.decision.evaluated_at)
    if request.action is not DecisionType.EXIT_LONG:
        reconcile("Only a fresh EXIT_LONG authorization may close this position")
    return request


def completed_close(c, origin, position, user_id):
    ids=c.execute(select(authorizations.c.registry_identity).where(authorizations.c.user_id==user_id)
        .order_by(authorizations.c.registry_identity)).scalars().all()
    completed=[]
    for key in ids:
        package=load_authorization(c,key,user_id=user_id)
        context=package.inputs.intent.entry_context
        if context.get('opening_authorization') != origin.registry_identity:
            continue
        request=close_request(package,origin,position)
        command=c.execute(select(commands.c.request_identity).where(commands.c.request_identity==request.request_identity,
            commands.c.user_id==user_id)).scalar_one_or_none()
        receipt=c.execute(select(accounting).where(accounting.c.request_identity==request.request_identity,
            accounting.c.user_id==user_id)).mappings().one_or_none()
        fact=c.execute(select(artifacts.requests.c.request_identity).where(artifacts.requests.c.decision_identity==request.risk_decision_identity,
            artifacts.requests.c.user_id==user_id)).scalar_one_or_none()
        consumption=c.execute(select(artifacts.consumptions.c.consumption_identity).where(
            artifacts.consumptions.c.decision_identity==request.risk_decision_identity,
            artifacts.consumptions.c.user_id==user_id)).scalar_one_or_none()
        state=c.execute(select(artifacts.orders.c.state).where(artifacts.orders.c.request_identity==request.request_identity)).scalar_one_or_none()
        fills=c.execute(select(artifacts.fills.c.fill_identity).where(artifacts.fills.c.request_identity==request.request_identity)).scalars().all()
        if not any((command,receipt,fact,consumption,state,fills)):
            continue
        if not (command and receipt and fact==request.request_identity and consumption and state=='FILLED'
            and len(fills)==1 and fills[0]==receipt['fill_identity'] and receipt['order_identity']==request.order_identity
            and receipt['position_id']==position['position_id'] and receipt['action']=='EXIT_LONG'
            and position['status']=='CLOSED' and position['closing_request_identity']==request.request_identity):
            reconcile()
        completed.append(package)
    if len(completed)>1 or (position['status']=='CLOSED' and len(completed)!=1):
        reconcile()
    return completed[0] if completed else None


def result(origin, position, mandate_id, decision, *, status, evaluated_at, **extra):
    i=origin.authorization.intent
    return PositionRunResult(status=status,mandate_identity=mandate_id,position_id=position['position_id'],
        position_identity=position['position_identity'],opening_registry_identity=origin.registry_identity,
        opening_eligibility_identity=i.entry_context['mandate_provenance']['eligibility_identity'],
        evaluated_at=timestamp(evaluated_at),strategy_timestamp=decision.timestamp,
        opening_intent_identity=i.intent_identity,opening_decision_identity=origin.authorization.decision.decision_identity,
        capital_allocation=i.entry_context['mandate_provenance']['capital_allocation'],
        strategy_decision=decision.decision,strategy_decision_identity=decision.decision_identity,
        strategy_input_identity=decision.input_identity,**extra)


def execute_close(c, origin, position, package, user_id, mandate_id, at, *, reused):
    # Reuse the canonical execution adapter and service, including immutable retry
    # recovery. No financial math or strategy evaluation belongs in this phase.
    from .service import run_paper_execution
    request=close_request(package,origin,position)
    @contextmanager
    def same_transaction():
        yield c
    response=run_paper_execution(ProductionPaperCommandSource(),same_transaction,user_id,
        PaperExecutionRequest(intent_identity=request.intent_identity,decision_identity=request.risk_decision_identity,
            request_identity=request.request_identity),timestamp(at))
    i=package.authorization.intent
    from types import SimpleNamespace
    decision=SimpleNamespace(decision=DecisionType.EXIT_LONG,decision_identity=i.strategy_decision_identity,
        input_identity=i.strategy_input_identity,timestamp=i.decision_at)
    return result(origin,position,mandate_id,decision,status='POSITION_CLOSED',evaluated_at=package.authorization.decision.evaluated_at,risk_outcome='APPROVED',
        intent_identity=request.intent_identity,decision_identity=request.risk_decision_identity,
        registry_identity=package.registry_identity,authorization_identity=package.authorization.authorization_identity,
        approved_quantity=package.authorization.quantity,execution=EntryExecution.model_validate(response.model_dump()),reused=reused)


def manage(db,c,mandate,origin,user_id,mandate_id,*,evaluated_at,clock):
    from .service import bound_request, execute_entry
    opening=bound_request(origin,user_id,mandate_id)
    book=capture_book(c,origin.authorization.intent.scope.portfolio_id,lock=True)
    position=c.execute(select(positions).where(positions.c.opening_request_identity==opening.request_identity)
        .with_for_update()).mappings().one_or_none()
    acquisition=c.execute(select(accounting).where(accounting.c.request_identity==opening.request_identity,
        accounting.c.user_id==user_id)).mappings().one_or_none()
    i=origin.authorization.intent
    if (position is None or acquisition is None or position['user_id']!=user_id or position['portfolio_id']!=book.portfolio_id
        or book.user_id!=user_id or book.environment!='PAPER' or book.base_currency!='USDT'
        or position['environment']!='PAPER' or position['currency']!='USDT' or position['direction']!='LONG'
        or position['origin_intent_identity']!=opening.intent_identity or position['origin_decision_identity']!=opening.risk_decision_identity
        or position['opening_order_identity']!=opening.order_identity
        or position['opening_fill_identity']!=acquisition['fill_identity']
        or position['opening_quantity']!=opening.quantity or position['opened_at']!=acquisition['executed_at']
        or position['instrument_id']!=i.instrument.instrument_id or position['canonical_symbol']!=i.instrument.canonical_symbol
        or (position['strategy_family'],position['strategy_version'],position['strategy_configuration_identity'])
            !=(i.strategy.family,i.strategy.version,i.strategy.configuration_identity)):
        reconcile()
    prior=completed_close(c,origin,position,user_id)
    if prior:
        return execute_close(c,origin,position,prior,user_id,mandate_id,clock(),reused=True)
    if position['status']!='OPEN' or position['quantity']!=position['opening_quantity']:
        reconcile()
    if mandate.status!='ACTIVE':
        # Historical entry receipt recovery only; paused/retired cannot initiate a
        # new market evaluation or close. Preserves the existing retry contract.
        return execute_entry(c,origin,user_id,mandate_id,at=timestamp(clock()),reused=True)
    eligibility=inspect_eligibility(db,user_id,mandate_id,resolved_at=evaluated_at)
    if eligibility.status is not EligibilityStatus.ELIGIBLE:
        raise PaperRunError('mandate_ineligible','The mandate is not eligible for position management',409)
    try:
        inputs,decision=assemble_close(c,user_id,position['position_id'],evaluated_at)
        if inputs is None:
            if decision.decision is not DecisionType.NO_ACTION:
                reconcile('Unexpected held-position strategy decision')
            return result(origin,position,mandate_id,decision,status='POSITION_HELD',evaluated_at=evaluated_at,reason_codes=decision.reason_codes)
        if decision.decision is not DecisionType.EXIT_LONG:
            reconcile('A genuine strategy EXIT_LONG is required')
        response=registered_response(c,inputs,decision,user_id)
    except PaperEvidenceUnavailable:
        raise PaperRunError('mandate_position_evidence_unavailable','Fresh authoritative position evidence is unavailable',409) from None
    if response['status']=='REJECTED':
        return result(origin,position,mandate_id,decision,status='EXIT_RISK_REJECTED',evaluated_at=evaluated_at,risk_outcome='REJECTED',
            intent_identity=inputs.intent.intent_identity,decision_identity=response['decision_identity'],reason_codes=response['reason_codes'])
    if response['status']!='APPROVED' or not response.get('registry_identity'):
        reconcile('Complete close Risk authorization is required')
    package=load_authorization(c,response['registry_identity'],user_id=user_id)
    if (package.inputs.inputs_identity!=inputs.inputs_identity
        or package.authorization.intent.strategy_decision_identity!=decision.decision_identity
        or package.authorization.decision.decision_identity!=response['decision_identity']):
        reconcile('Close Risk response does not match its authorization')
    return execute_close(c,origin,position,package,user_id,mandate_id,clock(),reused=False)
