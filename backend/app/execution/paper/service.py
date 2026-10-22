"""Atomic PAPER LONG/CASH orchestration only. Never commits or calls a venue."""
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select,insert,update,or_
from sqlalchemy.exc import DBAPIError
from app.users.models import User
from app.portfolio.models import PaperPortfolio
from app.instruments.models import Instrument
from app.assets.models import Asset
from app.risk.canonical import require,timestamp,to_wire
from app.risk.vocabulary import Direction,Environment
from app.strategy_lab.canonical import canonical_json
from ..canonical import execution_identity
from ..orders import Order
from ..fills import Fill
from ..vocabulary import OrderState
from ..paper_fill import calculate_paper_fill
from ..persistence import ExecutionArtifacts,FillAudit,persist_artifacts
from ..persistence import models as execution
from .contracts import PaperCommand,PaperEconomicResult,PaperExecutionError,restore_result
from .book import capture_book
from .validation import reconcile
from .accounting import cash_after,full_close
from .models import positions,accounting


def execute_paper(connection,command:PaperCommand,*,as_of):
    require(command,PaperCommand,'Paper command');at=timestamp(as_of)
    if connection.dialect.name!='postgresql' or not connection.in_transaction():raise PaperExecutionError('Explicit PostgreSQL transaction required')
    if connection.connection.dbapi_connection.autocommit is not False or connection.get_isolation_level()!='READ COMMITTED':raise PaperExecutionError('Non-autocommit READ COMMITTED transaction required')
    try:
        with connection.begin_nested():
            return _execute_locked(connection,command,at)
    except ArithmeticError:
        raise PaperExecutionError('Finite exact Paper accounting arithmetic unavailable') from None
    except DBAPIError:
        raise PaperExecutionError('Paper execution transaction failed; no partial economic action may commit') from None


def _execute_locked(c,command,at):
    r=command.request;i=r.authorization.intent
    if r.venue_identity!='PAPER_INTERNAL':raise PaperExecutionError('Internal PAPER venue attribution required')
    if r.environment is not Environment.PAPER or i.direction is not Direction.LONG:raise PaperExecutionError('Only PAPER LONG/CASH economic execution is enabled')
    user=c.execute(select(User.__table__).where(User.id==i.scope.user_id).with_for_update(read=True)).mappings().one_or_none()
    if user is None:raise PaperExecutionError('Authoritative user unavailable')
    book=capture_book(c,i.scope.portfolio_id,lock=True)
    if book.user_id!=i.scope.user_id or book.environment!='PAPER':raise PaperExecutionError('Paper ownership/environment mismatch')
    prior=c.execute(select(accounting).where(accounting.c.request_identity==r.request_identity)).mappings().one_or_none()
    if prior is not None:
        if prior['command_identity']!=command.command_identity or prior['portfolio_id']!=book.portfolio_id or prior['user_id']!=book.user_id:raise PaperExecutionError('Conflicting economic retry')
        if at<command.executed_at:raise PaperExecutionError('Retry cannot precede original execution')
        result=restore_result(prior['evidence'])
        if (result.result_identity!=prior['result_identity'] or result.command_identity!=command.command_identity
            or result.request_identity!=r.request_identity or result.intent_identity!=r.intent_identity
            or result.decision_identity!=r.risk_decision_identity or result.quantity!=r.quantity
            or result.action!=r.action.value or result.position_id!=prior['position_id']
            or prior['canonical_payload']!=canonical_json(to_wire(result))):raise PaperExecutionError('Stored result identity/context mismatch')
        if c.execute(select(positions.c.position_id).where(positions.c.position_id==result.position_id,positions.c.portfolio_id==book.portfolio_id,positions.c.user_id==book.user_id)).scalar_one_or_none() is None:
            raise PaperExecutionError('Stored result position ownership mismatch')
        return result
    if user['status']!='active':raise PaperExecutionError('User inactive')
    if c.execute(select(execution.consumptions.c.consumption_identity).where(or_(execution.consumptions.c.decision_identity==r.risk_decision_identity,execution.consumptions.c.action_identity==r.action_identity))).first():
        raise PaperExecutionError('Approval/action already reserved without an economic receipt; explicit recovery required')
    reconcile(command,book,at)
    instrument=c.execute(select(Instrument.__table__).where(Instrument.id==i.instrument.instrument_id).with_for_update(read=True)).mappings().one_or_none()
    if instrument is None or instrument['canonical_symbol']!=i.instrument.canonical_symbol or instrument['status']!='active' or instrument['market_type']!='SPOT':raise PaperExecutionError('Authoritative SPOT instrument unavailable or mismatched')
    quote=c.execute(select(Asset.__table__).where(Asset.id==instrument['quote_asset_id']).with_for_update(read=True)).mappings().one()
    base=c.execute(select(Asset.__table__).where(Asset.id==instrument['base_asset_id']).with_for_update(read=True)).mappings().one()
    if instrument['canonical_symbol'].split(':')[0]!=base['symbol']+'/'+quote['symbol']:raise PaperExecutionError('Canonical instrument/asset attribution inconsistent')
    if quote['symbol']!=book.base_currency or base['status']!='active' or quote['status']!='active':raise PaperExecutionError('Currency mismatch or inactive asset; no implicit FX or USD/USDT parity')
    calc=calculate_paper_fill(r,command.market,command.assumptions,r.action,executed_at=at,evaluated_at=at)
    candidates=[p for p in book.positions if p['instrument_id']==i.instrument.instrument_id]
    close=r.action.value=='EXIT_LONG'
    if close:
        if len(candidates)!=1:raise PaperExecutionError('No exact open LONG position to close')
        pos=dict(candidates[0])
        if pos['position_identity']!=i.entry_context.get('paper_position_identity'):raise PaperExecutionError('Close target position mismatch')
        if (pos['strategy_family'],pos['strategy_version'],pos['strategy_configuration_identity'])!=(i.strategy.family,i.strategy.version,i.strategy.configuration_identity):raise PaperExecutionError('Closing strategy attribution mismatch')
        if at<pos['opened_at']:raise PaperExecutionError('Close predates position')
        gross,net=full_close(pos,calc.leg);pid=pos['position_id']
        basis,entry_fee=pos['entry_cost_basis'],pos['entry_fee']
    else:
        if candidates:raise PaperExecutionError('One open position per portfolio/instrument; scale-in unsupported')
        pid=UUID(hex=r.action_identity.split(':')[1][:32]);gross=net=basis=entry_fee=None
    cash=cash_after(book.cash_balance,calc.leg)
    observed=Fill(r,'PAPER_'+r.action_identity.split(':')[1],r.quantity,calc.leg.modeled_execution_price,at)
    lifecycle=(Order(r,OrderState.CREATED,at,None,None,()),Order(r,OrderState.ACCEPTED,at,at,None,()),Order(r,OrderState.FILLED,at,at,None,(observed,)))
    persist_artifacts(c,ExecutionArtifacts(r,at,lifecycle,(FillAudit(observed,calc),)))
    result=PaperEconomicResult(r.request_identity,command.command_identity,r.intent_identity,r.risk_decision_identity,r.order_identity,observed.fill_identity,book.state_identity,pid,r.action.value,r.quantity,calc.leg.modeled_execution_price,calc.leg.gross_notional,calc.leg.fee,book.cash_balance,cash,basis,entry_fee,gross,net,at)
    if close:
        c.execute(update(positions).where(positions.c.position_id==pid).values(quantity=Decimal('0'),status='CLOSED',closed_at=at,closing_request_identity=r.request_identity))
    else:
        c.execute(insert(positions).values(position_id=pid,position_identity=execution_identity('paper-position-1.0.0',{'opening_request':r.request_identity}),user_id=book.user_id,portfolio_id=book.portfolio_id,instrument_id=instrument['id'],canonical_symbol=instrument['canonical_symbol'],environment='PAPER',direction='LONG',currency=book.base_currency,strategy_family=i.strategy.family,strategy_version=i.strategy.version,strategy_configuration_identity=i.strategy.configuration_identity,origin_intent_identity=r.intent_identity,origin_decision_identity=r.risk_decision_identity,opening_request_identity=r.request_identity,opening_order_identity=r.order_identity,opening_fill_identity=observed.fill_identity,opening_quantity=r.quantity,quantity=r.quantity,average_entry_price=calc.leg.modeled_execution_price,entry_reference_notional=calc.leg.reference_notional,entry_cost_basis=calc.leg.gross_notional,entry_fee=calc.leg.fee,entry_spread_cost=calc.leg.spread_cost,entry_slippage_cost=calc.leg.slippage_cost,status='OPEN',opened_at=at,closed_at=None,closing_request_identity=None))
    c.execute(update(PaperPortfolio.__table__).where(PaperPortfolio.id==book.portfolio_id).values(cash_balance=cash,updated_at=at))
    c.execute(insert(accounting).values(request_identity=r.request_identity,result_identity=result.result_identity,command_identity=command.command_identity,before_state_identity=book.state_identity,position_id=pid,portfolio_id=book.portfolio_id,user_id=book.user_id,order_identity=r.order_identity,fill_identity=observed.fill_identity,decision_identity=r.risk_decision_identity,intent_identity=r.intent_identity,action=r.action.value,environment='PAPER',currency=book.base_currency,quantity=r.quantity,cash_before=book.cash_balance,cash_after=cash,gross_notional=calc.leg.gross_notional,fee=calc.leg.fee,entry_cost_basis=basis,entry_fee_attributable=entry_fee,gross_realized_pnl=gross,net_realized_pnl=net,executed_at=at,evidence=to_wire(result),canonical_payload=canonical_json(to_wire(result))))
    return result
