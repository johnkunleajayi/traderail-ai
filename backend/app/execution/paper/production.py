"""PAPER-only command reconstruction inside the economic transaction.

No strategy evaluation or sizing occurs here. Risk fixes quantity and evidence;
canonical rows are re-read only to verify that exact approved market snapshot.
"""
from datetime import timedelta
from sqlalchemy import select
from app.risk.canonical import timestamp
from app.risk.vocabulary import Environment,Direction
from app.risk.paper_source.market import read_candles,digest
from app.risk.paper_source.policies import (COSTS,COST_VERSION,MARKET_MAX_AGE,MARKET_POLICY_VERSION,
    SYMBOL,PROVIDER,TIMEFRAME,HISTORY)
from app.market_intelligence.validation import validate_dataset
from app.execution.registry.models import authorizations,commands
from app.execution.registry.repository import (load_authorization,load_command,persist_command,
    RegistryConflict,AuthorizationUnavailable)
from app.execution.paper.book import capture_book
from app.execution.paper import PaperCommand
from app.execution.request import ExecutionRequest
from app.execution.paper_fill import PaperMarketEvidence,PaperFillAssumptions
from app.strategy_lab.costs import ResearchCostModel


class ProductionPaperCommandSource:
    def resolve_in_transaction(self,c,*,user_id,intent_identity,decision_identity,request_identity,evaluated_at):
        at=timestamp(evaluated_at)
        # Immutable successful retries recover the original execution instant and
        # economic command, even after approval expiry. 11D verifies the receipt.
        existing=c.execute(select(commands.c.request_identity).where(commands.c.request_identity==request_identity,
            commands.c.user_id==user_id)).scalar_one_or_none()
        if existing is not None:
            command=load_command(c,request_identity,user_id=user_id)
            if command.request.intent_identity!=intent_identity or command.request.risk_decision_identity!=decision_identity:
                raise RegistryConflict('Conflicting Paper retry')
            return command
        registry=c.execute(select(authorizations.c.registry_identity).where(authorizations.c.intent_identity==intent_identity,
            authorizations.c.decision_identity==decision_identity,authorizations.c.user_id==user_id)).scalar_one_or_none()
        if registry is None:raise AuthorizationUnavailable('Owned approved authorization unavailable')
        package=load_authorization(c,registry,user_id=user_id)
        a=package.authorization;i=a.intent
        if (i.scope.user_id!=user_id or i.scope.environment is not Environment.PAPER or i.scope.base_currency!='USDT'
                or i.direction is not Direction.LONG or (i.instrument.canonical_symbol,i.instrument.provider,i.instrument.timeframe)!=(SYMBOL,PROVIDER,TIMEFRAME)):
            raise AuthorizationUnavailable('Only owned USDT PAPER BTC LONG authorization is supported')
        a.validate_at(at)
        request=ExecutionRequest(a,'PAPER_INTERNAL',a.decision.evaluated_at)
        if request.request_identity!=request_identity:raise RegistryConflict('Conflicting request identity')
        book=capture_book(c,i.scope.portfolio_id,lock=True)
        # A duplicate may have waited for the first writer's portfolio lock.
        # Re-read after acquiring it under READ COMMITTED before comparing book
        # state, because that writer may now have committed the exact receipt.
        if c.execute(select(commands.c.request_identity).where(commands.c.request_identity==request_identity,
                commands.c.user_id==user_id)).scalar_one_or_none() is not None:
            command=load_command(c,request_identity,user_id=user_id)
            if command.request.intent_identity!=intent_identity or command.request.risk_decision_identity!=decision_identity:
                raise RegistryConflict('Conflicting Paper retry')
            return command
        if (book.user_id!=user_id or book.environment!='PAPER' or book.base_currency!='USDT'
                or book.status!='active' or book.state_identity!=package.inputs.snapshot.valuation_identity):
            raise RegistryConflict('Authoritative Paper portfolio changed since approval')
        if (package.inputs.sizing_assumptions.costs!=COSTS
                or i.entry_context.get('paper_cost_policy')!=COST_VERSION):
            raise RegistryConflict('Approved Paper cost policy is unsupported')
        context,candles=read_candles(c,at=a.decision.evaluated_at)
        validate_dataset(context,candles,a.decision.evaluated_at)
        data_identity=digest('paper-market-data/v1',{'policy':MARKET_POLICY_VERSION,'history':candles})
        if (len(candles)!=HISTORY or context!=i.instrument or data_identity!=i.market_context.data_identity
                or data_identity!=package.evidence_references.market_source_identity
                or candles[-1].close_time!=i.market_context.data_through
                or candles[-1].close!=package.inputs.sizing_assumptions.entry_reference_price
                or any(row.retrieved_at>a.decision.evaluated_at for row in candles)):
            raise RegistryConflict('Approved canonical market evidence changed or is unavailable')
        valid_until=min(a.decision.approval_expires_at,i.market_context.expires_at,
            candles[-1].open_time+timedelta(hours=1)+MARKET_MAX_AGE[TIMEFRAME])
        if not at<valid_until:raise ValueError('Market evidence is stale or expired')
        market=PaperMarketEvidence(request.request_identity,context,Environment.PAPER,candles[-1].close,
            candles[-1].close_time,at,valid_until,data_identity,True)
        assumptions=PaperFillAssumptions(ResearchCostModel(COSTS.entry_fee_rate,COSTS.entry_full_spread_rate,
            COSTS.entry_slippage_rate),MARKET_MAX_AGE[TIMEFRAME])
        command=PaperCommand(request,market,assumptions,at)
        return persist_command(c,registry,command,user_id=user_id,as_of=at)
