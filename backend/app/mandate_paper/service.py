"""One explicit owner command joins existing Risk and PAPER transactions.
Completed entry facts identify the canonical managed position; unknown facts never resubmit.
"""
from contextlib import contextmanager
from collections.abc import Mapping
from decimal import Decimal
from sqlalchemy import select
from app.mandates.service import owned
from app.mandate_eligibility.policy import ADAPTIVE_TREND_REFERENCE
from app.mandate_eligibility.service import resolution_time
from app.mandate_risk.service import evaluate as evaluate_risk
from app.mandate_risk.contracts import MandateRiskStatus
from app.mandate_strategy.contracts import ObservationOutcome
from app.risk.canonical import identity,timestamp
from app.risk.vocabulary import Environment,Direction,RiskOutcome
from app.risk.paper_policy import configuration_for_paper_portfolio
from app.risk.paper_source.policies import COSTS,COST_VERSION
from app.risk.integrated.models import GateName
from app.strategies.decisions import DecisionType
from app.execution.registry.models import authorizations,commands
from app.execution.registry.repository import load_authorization,RegistryUnavailable,RegistryConflict,AuthorizationUnavailable
from app.execution.persistence import models as artifacts
from app.execution.paper.models import accounting
from app.execution.paper.production import ProductionPaperCommandSource
from app.execution.request import ExecutionRequest
from app.execution.api.schemas import PaperExecutionRequest
from app.execution.api.service import run_paper_execution,PaperAPIError
from .contracts import PaperRunResult,PaperRunStatus,PaperRunError,EntryExecution


def execution_clock():
    # Reuse the application UTC clock, injected explicitly into orchestration.
    return resolution_time


def bound_request(package,user_id,mandate_id):
    i=package.inputs.intent; p=i.entry_context.get("mandate_provenance",{})
    if not isinstance(p,Mapping):
        raise PaperRunError("mandate_authorization_mismatch","Mandate provenance is unavailable",409)
    ref=ADAPTIVE_TREND_REFERENCE
    if (i.scope.user_id!=user_id or i.scope.environment is not Environment.PAPER or i.scope.base_currency!="USDT"
        or i.direction is not Direction.LONG or (i.instrument.canonical_symbol,i.instrument.provider,i.instrument.timeframe)!=("BTC/USDT","BINANCE","1h")
        or (i.strategy.family,i.strategy.version,i.strategy.configuration_identity)!=(ref.strategy_family,ref.strategy_version,ref.configuration_identity)
        or p.get("mandate_identity")!=str(mandate_id) or p.get("eligibility_policy_version")!="mandate-eligibility-v2"
        or p.get("technical_configuration_identity")!=ref.configuration_identity or p.get("allocation_currency")!="USDT"
        or p.get("allocation_rule")!="entry-cash-including-fee-v1"
        or type(p.get("capital_allocation")) is not Decimal or not p["capital_allocation"].is_finite() or p["capital_allocation"]<=0
        or package.inputs.sizing_assumptions.constraints.max_entry_cash_commitment!=p["capital_allocation"]
        or package.inputs.configuration!=configuration_for_paper_portfolio(i.scope)
        or package.inputs.sizing_assumptions.costs!=COSTS or i.entry_context.get("paper_cost_policy")!=COST_VERSION):
        raise PaperRunError("mandate_authorization_mismatch","The authorization is not the exact supported mandate entry",409)
    try:
        for key in ("eligibility_identity","eligibility_policy_identity","strategy_observation_identity"):
            identity(p.get(key),key)
        identity(i.entry_context.get("mandate_risk_proposal_identity"),"mandate proposal")
    except ValueError:
        raise PaperRunError("mandate_authorization_mismatch","Mandate provenance identities are unavailable",409) from None
    request=ExecutionRequest(package.authorization,"PAPER_INTERNAL",package.authorization.decision.evaluated_at)
    trade=next(g.result for g in package.authorization.report.gates if g.gate is GateName.TRADE_RISK)
    if request.action is not DecisionType.ENTER_LONG or trade.evaluated_projection.required_cash>p["capital_allocation"]:
        raise PaperRunError("mandate_authorization_mismatch","The authorization does not permit this entry",409)
    return request


def completed_entry(c,user_id,mandate_id):
    ids=c.execute(select(authorizations.c.registry_identity).where(authorizations.c.user_id==user_id)
        .order_by(authorizations.c.registry_identity)).scalars().all()
    completed=[]
    for key in ids:
        package=load_authorization(c,key,user_id=user_id)
        provenance=package.inputs.intent.entry_context.get("mandate_provenance",{})
        if not isinstance(provenance,Mapping):
            raise PaperRunError("mandate_authorization_mismatch","Stored mandate provenance cannot be verified",409)
        if provenance.get("mandate_identity")!=str(mandate_id):
            continue
        r=bound_request(package,user_id,mandate_id)
        command=c.execute(select(commands.c.request_identity).where(commands.c.request_identity==r.request_identity,
            commands.c.user_id==user_id)).scalar_one_or_none()
        receipt=c.execute(select(accounting).where(accounting.c.request_identity==r.request_identity,
            accounting.c.user_id==user_id)).mappings().one_or_none()
        request=c.execute(select(artifacts.requests.c.request_identity).where(artifacts.requests.c.decision_identity==r.risk_decision_identity,
            artifacts.requests.c.user_id==user_id)).scalar_one_or_none()
        consumed=c.execute(select(artifacts.consumptions.c.consumption_identity).where(artifacts.consumptions.c.decision_identity==r.risk_decision_identity,
            artifacts.consumptions.c.user_id==user_id)).scalar_one_or_none()
        order=c.execute(select(artifacts.orders.c.state).where(artifacts.orders.c.request_identity==r.request_identity)).scalar_one_or_none()
        fills=c.execute(select(artifacts.fills.c.fill_identity).where(artifacts.fills.c.request_identity==r.request_identity)).scalars().all()
        if not any((command,receipt,request,consumed,order,fills)):
            continue
        if not (command and receipt and request==r.request_identity and consumed and order=="FILLED"
            and len(fills)==1 and fills[0]==receipt['fill_identity'] and receipt['order_identity']==r.order_identity):
            raise PaperRunError("paper_run_reconciliation_required","Incomplete or unknown execution state requires explicit reconciliation",409)
        completed.append(package)
    if len(completed)>1:
        raise PaperRunError("paper_run_reconciliation_required","Multiple entry receipts require explicit reconciliation",409)
    return completed[0] if completed else None


def execute_entry(c,package,user_id,mandate_id,*,at,reused):
    request=bound_request(package,user_id,mandate_id)
    payload=PaperExecutionRequest(intent_identity=request.intent_identity,decision_identity=request.risk_decision_identity,
        request_identity=request.request_identity)
    @contextmanager
    def same_transaction():
        yield c
    response=run_paper_execution(ProductionPaperCommandSource(),same_transaction,user_id,payload,at)
    p=package.inputs.intent.entry_context["mandate_provenance"]
    return PaperRunResult(status=PaperRunStatus.PAPER_EXECUTED,mandate_identity=mandate_id,
        eligibility_identity=p['eligibility_identity'],strategy_observation_identity=p['strategy_observation_identity'],
        strategy_decision_identity=package.inputs.intent.strategy_decision_identity,strategy_decision=DecisionType.ENTER_LONG,
        strategy_outcome=ObservationOutcome.ENTER_LONG,capital_allocation=p['capital_allocation'],risk_outcome=RiskOutcome.APPROVED,
        intent_identity=request.intent_identity,decision_identity=request.risk_decision_identity,
        registry_identity=package.registry_identity,authorization_identity=package.authorization.authorization_identity,
        approved_quantity=package.authorization.quantity,execution=EntryExecution.model_validate(response.model_dump()),reused=reused)


def stopped(result):
    s=result.strategy
    return PaperRunResult(status=PaperRunStatus.NO_TRADE if result.status is MandateRiskStatus.NO_TRADE else PaperRunStatus.RISK_REJECTED,
        mandate_identity=s.mandate_identity,eligibility_identity=s.eligibility_identity,
        strategy_observation_identity=s.evaluation_identity,strategy_decision_identity=s.strategy_decision.decision_identity,
        strategy_decision=s.strategy_decision.decision,strategy_outcome=s.outcome,capital_allocation=s.user_intent.capital_allocation,
        risk_outcome=result.risk.outcome if result.risk else None,intent_identity=result.intent_identity,
        decision_identity=result.risk.decision_identity if result.risk else None,
        reason_codes=tuple(code.value for code in (result.risk.blocking_reason_codes if result.risk else s.reason_codes)))


def run(db,user_id,mandate_id,*,evaluated_at,clock,expected_candle=None):
    mandate=owned(db,user_id,mandate_id,lock=True)
    c=db.connection()
    try:
        # An acquired mandate position is managed, never entered a second time.
        # Completed close receipts recover before fresh strategy/Risk evaluation.
        prior=completed_entry(c,user_id,mandate_id)
        if prior is not None:
            from .positions import manage
            return manage(db,c,mandate,prior,user_id,mandate_id,evaluated_at=evaluated_at,clock=clock,
                **({"expected_candle":expected_candle} if expected_candle is not None else {}))
        result=evaluate_risk(db,user_id,mandate_id,evaluated_at=evaluated_at)
        if expected_candle is not None:
            expected_candle.require_market(result.strategy.market)
        if result.status is not MandateRiskStatus.APPROVED:
            return stopped(result)
        package=load_authorization(c,result.authorization.registry_identity,user_id=user_id)
        i=package.inputs.intent
        if (i.intent_identity!=result.intent_identity or package.authorization.decision.decision_identity!=result.risk.decision_identity
            or package.authorization.authorization_identity!=result.authorization.authorization_identity
            or package.authorization.quantity!=result.authorization.quantity
            or i.entry_context.get("mandate_provenance",{}).get("strategy_observation_identity")!=result.strategy.evaluation_identity):
            raise PaperRunError("mandate_authorization_mismatch","The returned authorization does not match the Risk result",409)
        return execute_entry(c,package,user_id,mandate_id,at=timestamp(clock()),reused=False)
    except PaperAPIError as error:
        raise PaperRunError(error.code,error.message,error.status) from None
    except (RegistryConflict,AuthorizationUnavailable):
        raise PaperRunError("mandate_authorization_mismatch","The owned authorization cannot be reconciled",409) from None
    except RegistryUnavailable:
        raise PaperRunError("mandate_registry_unavailable","Authorization registry is unavailable",503) from None
