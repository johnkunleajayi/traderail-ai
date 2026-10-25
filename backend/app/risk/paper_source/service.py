"""First-trade authoritative PAPER assembly and atomic approval registration."""
from datetime import timedelta
from dataclasses import replace
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.users.models import User
from app.portfolio.models import PaperPortfolio
from app.execution.paper.book import capture_book
from app.execution.paper.models import positions,accounting
from app.execution.persistence.models import requests
from app.risk.context import PortfolioScope
from app.risk.vocabulary import Environment,RiskState
from app.risk.portfolio import PortfolioRiskSnapshot
from app.risk.paper_policy import configuration_for_paper_portfolio
from app.risk.adaptive_trend_intent import create_adaptive_trend_intent
from app.risk.paper_switches import provision_inactive,load_inventory
from app.risk.trade_risk import SizingAssumptions
from app.risk.portfolio_risk import PortfolioRiskContext,StrategyAttribution
from app.risk.risk_states import DrawdownEvidence
from app.risk.safety_gate import (StrategyLifecycleEvidence,StrategyHealthEvidence,StrategyHealth,
    KillSwitchStatus,evaluate_safety_gate)
from app.risk.safety_gate.findings import Findings
from app.risk.safety_gate.switch_evaluation import evaluate_switches
from app.risk.integrated.inputs import RiskEvaluationInputs
from app.risk.authorization_service import evaluate_and_register
from app.execution.registry.contracts import ExecutionEvidenceReferences
from app.risk.canonical import require,timestamp
from app.strategy_lab.lifecycle import StrategyLifecycle,StrategyState
from app.strategies.decisions import DecisionType
from .models import initializations,activations
from .market import read_observation,PaperEvidenceUnavailable,digest
from .policies import (COSTS,COST_VERSION,CONSTRAINTS,PORTFOLIO_POLICY,STATE_POLICY,SAFETY_POLICY,ACTIVATION_VERSION)


def owned_untouched_book(c,user_id):
    require(user_id,UUID,"authenticated owner")
    user=c.execute(select(User.id).where(User.id==user_id,User.status=='active').with_for_update(read=True)).scalar_one_or_none()
    pid=c.execute(select(PaperPortfolio.id).where(PaperPortfolio.user_id==user_id).with_for_update()).scalar_one_or_none()
    if user is None or pid is None:
        raise PaperEvidenceUnavailable("Owned PAPER portfolio unavailable")
    book=capture_book(c,pid,lock=True)
    # Complete history checks, not just the OPEN inventory: a previously closed
    # position does not make a portfolio newly initialized again.
    history=any(c.execute(select(table.c.portfolio_id).where(table.c.portfolio_id==pid).limit(1)).first()
        for table in (positions,accounting,requests))
    if (book.user_id!=user_id or book.environment!='PAPER' or book.base_currency!='USDT'
            or book.status!='active' or book.cash_balance!=Decimal('10000') or book.positions
            or book.accounting_identities or history):
        raise PaperEvidenceUnavailable("Only a verified untouched USDT PAPER portfolio is supported")
    start=c.execute(select(PaperPortfolio.starting_balance).where(PaperPortfolio.id==pid)).scalar_one()
    if start!=Decimal('10000'):
        raise PaperEvidenceUnavailable("Initial PAPER capital does not reconcile")
    return book


def assemble(c,user_id,at,*,proposal_context=None,size_constraints=None):
    """Return inputs or genuine NO_TRADE; never use browser financial values."""
    at=timestamp(at)
    if (c.dialect.name!='postgresql' or not c.in_transaction()
            or c.connection.dbapi_connection.autocommit is not False or c.get_isolation_level()!='READ COMMITTED'):
        raise PaperEvidenceUnavailable("Explicit trusted PostgreSQL transaction required")
    book=owned_untouched_book(c,user_id)
    observation,decision,market=read_observation(c,at=at)
    if decision.decision is not DecisionType.ENTER_LONG:
        # NO_ACTION/EXIT/SHORT cannot become a first LONG exposure.
        return None,decision
    scope=PortfolioScope(user_id,book.portfolio_id,Environment.PAPER,'USDT')
    config=configuration_for_paper_portfolio(scope)
    until=min(market.expires_at,at+config.operating_limits.max_approval_age)
    intent=create_adaptive_trend_intent(scope=scope,observation=observation,decision=decision,
        market_context=market,created_at=at,expires_at=until)
    if proposal_context and set(proposal_context).intersection((*intent.entry_context,'paper_cost_policy','paper_activation_policy')):
        raise PaperEvidenceUnavailable('Proposal provenance cannot override canonical analytical evidence')
    intent=replace(intent,entry_context={**intent.entry_context,**(proposal_context or {}),'paper_cost_policy':COST_VERSION,
        'paper_activation_policy':ACTIVATION_VERSION})
    old=c.execute(select(initializations).where(initializations.c.portfolio_id==book.portfolio_id)).mappings().one_or_none()
    if old is None:
        c.execute(insert(initializations).values(portfolio_id=book.portfolio_id,book_identity=book.state_identity,
            peak_equity=book.cash_balance,previous_state='NORMAL',initialized_at=at))
        # Explicit once-only activation. Subsequent missing switch rows fail closed;
        # ordinary resolution never replenishes deleted/incomplete controls.
        provision_inactive(c,intent,user_id=user_id,changed_at=at)
        initialized_at=at
    else:
        if (old['book_identity']!=book.state_identity or old['peak_equity']!=book.cash_balance
                or old['previous_state']!='NORMAL' or old['initialized_at']>at):
            raise PaperEvidenceUnavailable("Initial risk history does not reconcile")
        initialized_at=old['initialized_at']
    lifecycle,inventory,health=safety_evidence(c,intent,observation,market,at,until,activate=True)
    # The existing execution revalidator binds valuation to the exact locked book.
    # Observation time/currency remain bound by the immutable Risk snapshot itself.
    valuation=book.state_identity
    definition=observation.definition
    zero=Decimal('0')
    snapshot=PortfolioRiskSnapshot(scope,at,until,valuation,book.cash_balance,book.cash_balance,RiskState.NORMAL,
        (),zero,zero,zero,zero,None,None,None,zero)
    attribution=StrategyAttribution(definition.configuration_identity,definition.family,definition.version,lifecycle.evidence_identity)
    context=PortfolioRiskContext(intent.intent_identity,snapshot.snapshot_identity,PORTFOLIO_POLICY.policy_identity,
        'USDT',valuation,at,until,book.state_identity,(attribution,),(),())
    drawdown=DrawdownEvidence(scope,snapshot.snapshot_identity,valuation,book.cash_balance,book.cash_balance,
        RiskState.NORMAL,initialized_at,at,until,digest('paper-initial-drawdown/v1',{'book':book.state_identity,'initialized_at':initialized_at}))
    assumptions=SizingAssumptions(intent.reference_price,market,config.operating_limits.max_trade_risk_fraction,
        COSTS,CONSTRAINTS if size_constraints is None else size_constraints,None)
    return RiskEvaluationInputs(intent,config,snapshot,market,at,assumptions,PORTFOLIO_POLICY,context,
        STATE_POLICY,drawdown,SAFETY_POLICY,inventory,lifecycle,health),decision


def safety_evidence(c,intent,observation,market,at,until,*,activate=False):
    definition=observation.definition
    if activate:
        c.execute(insert(activations).values(configuration_identity=definition.configuration_identity,
            family=definition.family,version=definition.version,environment='PAPER',lifecycle='PAPER',
            authority=ACTIVATION_VERSION,activated_at=at).on_conflict_do_nothing())
    activation=c.execute(select(activations).where(activations.c.configuration_identity==definition.configuration_identity)
        .with_for_update(read=True)).mappings().one()
    if (activation['family'],activation['version'],activation['environment'],activation['lifecycle'],activation['authority'])!=(
            definition.family,definition.version,'PAPER','PAPER',ACTIVATION_VERSION) or activation['activated_at']>at:
        raise PaperEvidenceUnavailable("Exact PAPER lifecycle authorization unavailable")
    lifecycle=StrategyLifecycleEvidence(definition,Environment.PAPER,StrategyLifecycle(StrategyState.PAPER),
        at,at+timedelta(hours=24),digest('paper-lifecycle-attestation/v1',dict(activation)))
    inventory=load_inventory(c,intent,user_id=intent.scope.user_id,observed_at=at,valid_until=min(until,at+timedelta(minutes=5)))
    clear=evaluate_switches(intent,inventory,SAFETY_POLICY,at,Findings())[2]
    # Operational eligibility only. No Paper performance/profitability is inferred.
    health=StrategyHealthEvidence(definition,Environment.PAPER,
        StrategyHealth.HEALTHY if clear else StrategyHealth.UNKNOWN,None,observation.candle.open_time,
        observation.candle.close_time,at,min(market.expires_at,at+timedelta(minutes=15)),
        digest('paper-operational-health/v1',{'activation':dict(activation),'input':observation.input_identity,
            'inventory':inventory.inventory_identity,'policy':SAFETY_POLICY.policy_identity}))
    return lifecycle,inventory,health


class ProductionPaperRiskSource:
    def __init__(self,engine): self.engine=engine

    def resolve(self,*,user_id,intent_identity,evaluated_at):
        with self.engine.begin() as c:
            inputs,_=assemble(c,user_id,evaluated_at)
            if inputs is None or inputs.intent.intent_identity!=intent_identity:
                raise PaperEvidenceUnavailable("Exact current PAPER intent unavailable")
            return inputs

    def inspect_registered(self,*,user_id,intent_identity,evaluated_at):
        with self.engine.begin() as c:
            inputs,_=assemble(c,user_id,evaluated_at)
            if inputs is None or inputs.intent.intent_identity!=intent_identity:
                raise PaperEvidenceUnavailable("Exact current PAPER intent unavailable")
            refs=ExecutionEvidenceReferences(inputs.market_context.data_identity,inputs.market_context.context_identity,
                inputs.sizing_assumptions.costs.costs_identity,inputs.snapshot.valuation_identity)
            report,_=evaluate_and_register(c,inputs,user_id=user_id,evidence_references=refs)
            return inputs,report

    def evaluate_latest(self,*,user_id,evaluated_at):
        with self.engine.begin() as c:
            inputs,decision=assemble(c,user_id,evaluated_at)
            return registered_response(c,inputs,decision,user_id)

    def evaluate_close(self,*,user_id,position_id,evaluated_at):
        from .close import assemble_close
        with self.engine.begin() as c:
            inputs,decision=assemble_close(c,user_id,position_id,evaluated_at)
            return registered_response(c,inputs,decision,user_id)


def registered_response(c,inputs,decision,user_id):
    if inputs is None:
        return {'status':'NO_TRADE','strategy_decision':decision.decision.value,
            'decision_identity':decision.decision_identity,'reason_codes':decision.reason_codes}
    refs=ExecutionEvidenceReferences(inputs.market_context.data_identity,inputs.market_context.context_identity,
        inputs.sizing_assumptions.costs.costs_identity,inputs.snapshot.valuation_identity)
    report,package=evaluate_and_register(c,inputs,user_id=user_id,evidence_references=refs)
    from app.risk.canonical import to_wire
    result={'status':report.decision.outcome.value,'intent_identity':inputs.intent.intent_identity,
                'decision_identity':report.decision.decision_identity,'evaluated_at':to_wire(report.decision.evaluated_at),
                'approval_expires_at':to_wire(report.decision.approval_expires_at),
                'registry_identity':package.registry_identity if package else None,
                'approved_quantity':to_wire(package.authorization.quantity) if package else None,
                'reason_codes':tuple(code.value for code in report.decision.reason_codes),
                'gates':tuple({'gate':row.gate.value,'outcome':row.disposition.value,
                    'record_identity':row.record_identity} for row in report.gates)}
    if package:
        from app.execution.request import ExecutionRequest
        # Deterministic request metadata only; no order or consumption is created.
        result['request_identity']=ExecutionRequest(package.authorization,'PAPER_INTERNAL',report.decision.evaluated_at).request_identity
    return result
