"""Bounded one-position full-close proposal. Risk still evaluates every gate.

Only the first BTC/USDT LONG, with complete canonical close history since entry,
is supported. Missing provenance/history or a breached modeling boundary rejects;
this is not an emergency-liquidation bypass or general portfolio valuation engine.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select
from app.users.models import User
from app.portfolio.models import PaperPortfolio
from app.execution.paper.book import capture_book
from app.execution.paper.models import positions,accounting
from app.execution.paper.contracts import restore_result
from app.execution.registry.models import authorizations
from app.execution.registry.repository import load_authorization
from app.risk.canonical import require,timestamp
from app.risk.context import PortfolioScope
from app.risk.intent import TradeIntent
from app.risk.portfolio import PositionRiskSnapshot,PortfolioRiskSnapshot
from app.risk.vocabulary import Environment,Direction,RiskState
from app.risk.paper_policy import configuration_for_paper_portfolio
from app.risk.trade_risk import SizingAssumptions
from app.risk.trade_risk.calculation import unit_loss
from app.risk.portfolio_risk import PortfolioRiskContext,StrategyAttribution
from app.risk.risk_states import DrawdownEvidence
from app.risk.risk_states.measurement import DrawdownMeasurement,transition_state
from app.risk.integrated.inputs import RiskEvaluationInputs
from app.strategies.inputs import PositionState
from app.strategies.decisions import DecisionType
from app.strategy_lab.numeric import exact_product,exact_sum,difference
from .market import read_candles,analyze,PaperEvidenceUnavailable,digest
from .models import initializations
from .policies import COSTS,COST_VERSION,CONSTRAINTS,PORTFOLIO_POLICY,STATE_POLICY,SAFETY_POLICY,SYMBOL


def history_state(initial,position,entry,book,candles):
    """Reconstruct state only from immutable genesis/receipt and complete marks.

    Peak is a peak of explicitly observed completed-candle portfolio valuations,
    including the authoritative entry reference mark; no intrabar peak is claimed.
    """
    if (initial['previous_state']!='NORMAL' or initial['peak_equity']!=Decimal('10000')
            or initial['book_identity']!=entry.before_state_identity
            or initial['initialized_at']>entry.executed_at
            or entry.action!='ENTER_LONG' or entry.position_id!=position['position_id']
            or entry.request_identity!=position['opening_request_identity']
            or entry.order_identity!=position['opening_order_identity']
            or entry.fill_identity!=position['opening_fill_identity']
            or entry.intent_identity!=position['origin_intent_identity']
            or entry.decision_identity!=position['origin_decision_identity']
            or entry.quantity!=position['quantity'] or position['quantity']!=position['opening_quantity']
            or entry.gross_notional!=position['entry_cost_basis'] or entry.fee!=position['entry_fee']
            or entry.executed_at!=position['opened_at'] or book.updated_at!=entry.executed_at
            or entry.cash_after!=book.cash_balance or entry.cash_before!=initial['peak_equity']
            or book.accounting_identities!=(entry.result_identity,)
            or candles[0].open_time>entry.executed_at):
        raise PaperEvidenceUnavailable('Complete first-position valuation provenance required')
    peak=initial['peak_equity'];peak_at=initial['initialized_at'];state=RiskState.NORMAL
    entry_mark=position['entry_reference_notional']
    opening_equity=exact_sum((book.cash_balance,entry_mark))
    measured=DrawdownMeasurement(peak,opening_equity)
    state,_=transition_state(measured,state,STATE_POLICY)
    if opening_equity>peak:peak,peak_at=opening_equity,entry.executed_at
    for candle in candles[:-1]:
        if candle.close_time<entry.executed_at:continue
        equity=exact_sum((book.cash_balance,exact_product(position['quantity'],candle.close)))
        measured=DrawdownMeasurement(peak,equity)
        state,_=transition_state(measured,state,STATE_POLICY)
        if equity>peak:peak,peak_at=equity,candle.close_time
    return peak,peak_at,state


def assemble_close(c,user_id,position_id,at,*,expected_candle=None):
    from .service import safety_evidence
    require(user_id,UUID,'authenticated owner');require(position_id,UUID,'position id');at=timestamp(at)
    if (c.dialect.name!='postgresql' or not c.in_transaction()
            or c.connection.dbapi_connection.autocommit is not False or c.get_isolation_level()!='READ COMMITTED'):
        raise PaperEvidenceUnavailable('Trusted PostgreSQL transaction required')
    user=c.execute(select(User.id).where(User.id==user_id,User.status=='active').with_for_update(read=True)).scalar_one_or_none()
    pid=c.execute(select(PaperPortfolio.id).where(PaperPortfolio.user_id==user_id).with_for_update()).scalar_one_or_none()
    if user is None or pid is None:raise PaperEvidenceUnavailable('Owned PAPER position unavailable')
    book=capture_book(c,pid,lock=True)
    all_positions=c.execute(select(positions).where(positions.c.portfolio_id==pid)).mappings().all()
    if (book.user_id!=user_id or book.environment!='PAPER' or book.base_currency!='USDT' or book.status!='active'
            or len(book.positions)!=1 or len(all_positions)!=1):
        raise PaperEvidenceUnavailable('Only the first USDT LONG full close is supported')
    pos=book.positions[0]
    if (pos['position_id']!=position_id or pos['user_id']!=user_id or pos['portfolio_id']!=pid
            or pos['direction']!='LONG' or pos['currency']!='USDT' or pos['canonical_symbol']!=SYMBOL or pos['status']!='OPEN'):
        raise PaperEvidenceUnavailable('Exact owned USDT LONG required')
    row=c.execute(select(authorizations.c.registry_identity).where(authorizations.c.decision_identity==pos['origin_decision_identity'],
        authorizations.c.intent_identity==pos['origin_intent_identity'],authorizations.c.user_id==user_id,
        authorizations.c.portfolio_id==pid)).scalar_one_or_none()
    if row is None:raise PaperEvidenceUnavailable('Opening authorization unavailable')
    origin=load_authorization(c,row,user_id=user_id)
    receipts=c.execute(select(accounting).where(accounting.c.portfolio_id==pid)).mappings().all()
    initial=c.execute(select(initializations).where(initializations.c.portfolio_id==pid)).mappings().one_or_none()
    if len(receipts)!=1 or initial is None:raise PaperEvidenceUnavailable('Initial risk and acquisition history required')
    entry=restore_result(receipts[0]['evidence'])
    if entry.result_identity!=receipts[0]['result_identity']:
        raise PaperEvidenceUnavailable('Acquisition receipt mismatch')
    context,candles=read_candles(c,at=at)
    if expected_candle is not None:
        if not candles:
            raise PaperEvidenceUnavailable("Expected completed position candle is unavailable")
        expected_candle.require(context,candles[-1])
    observation,decision,market=analyze(context,candles,at=at,position_state=PositionState.LONG)
    if (context!=origin.authorization.intent.instrument or observation.definition!=origin.authorization.intent.strategy
            or (pos['strategy_family'],pos['strategy_version'],pos['strategy_configuration_identity'])!=(
                observation.definition.family,observation.definition.version,observation.definition.configuration_identity)):
        raise PaperEvidenceUnavailable('Closing strategy/instrument attribution mismatch')
    if decision.decision is not DecisionType.EXIT_LONG:return None,decision
    peak,peak_at,previous=history_state(initial,pos,entry,book,candles)
    scope=PortfolioScope(user_id,pid,Environment.PAPER,'USDT');config=configuration_for_paper_portfolio(scope)
    until=min(market.expires_at,at+config.operating_limits.max_approval_age)
    reference=observation.candle.close
    invalidation=difference(reference,exact_product(Decimal('2'),observation.intelligence.atr_14))
    intent=TradeIntent(scope,context,Direction.LONG,observation.definition,decision.decision_identity,
        observation.input_identity,decision.timestamp,reference,invalidation,
        {'paper_action':'EXIT_LONG','paper_position_identity':pos['position_identity'],
         'opening_authorization':origin.registry_identity,'paper_cost_policy':COST_VERSION,
         'invalidation_rule':'adaptive-trend-atr14-invalidation/v1.0.0'},market,at,until)
    exposure=exact_product(pos['quantity'],reference)
    # Conservative existing-position reserve reuses established loss mathematics;
    # it is never charged as cash/P&L and never means the stop has executed.
    loss=unit_loss(Direction.LONG,reference,origin.authorization.intent.invalidation_price,COSTS)
    open_risk=exact_product(pos['quantity'],loss.total_loss)
    risk_position=PositionRiskSnapshot(position_id,context.instrument_id,context.canonical_symbol,Direction.LONG,
        pos['quantity'],observation.definition.configuration_identity,reference,exposure,open_risk)
    equity=exact_sum((book.cash_balance,exposure));measurement=DrawdownMeasurement(peak,equity)
    snapshot=PortfolioRiskSnapshot(scope,at,until,book.state_identity,equity,book.cash_balance,previous,
        (risk_position,),open_risk,exposure,exposure,Decimal('0'),None,None,None,measurement.drawdown_fraction)
    lifecycle,inventory,health=safety_evidence(c,intent,observation,market,at,until)
    attribution=StrategyAttribution(observation.definition.configuration_identity,observation.definition.family,
        observation.definition.version,lifecycle.evidence_identity)
    portfolio_context=PortfolioRiskContext(intent.intent_identity,snapshot.snapshot_identity,PORTFOLIO_POLICY.policy_identity,
        'USDT',book.state_identity,at,until,book.state_identity,(attribution,),(),())
    drawdown=DrawdownEvidence(scope,snapshot.snapshot_identity,book.state_identity,equity,peak,previous,peak_at,at,until,
        digest('paper-first-close-history/v1',{'entry':entry.result_identity,'market':market.data_identity,'initial':dict(initial)}))
    assumptions=SizingAssumptions(reference,market,config.operating_limits.max_trade_risk_fraction,COSTS,CONSTRAINTS,pos['quantity'])
    return RiskEvaluationInputs(intent,config,snapshot,market,at,assumptions,PORTFOLIO_POLICY,portfolio_context,
        STATE_POLICY,drawdown,SAFETY_POLICY,inventory,lifecycle,health),decision
