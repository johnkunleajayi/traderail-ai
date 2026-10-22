"""11D pure contracts/accounting oracles. Real SQL tests are isolated separately."""
from dataclasses import replace,FrozenInstanceError
from datetime import timedelta,timezone
from decimal import Decimal,localcontext,ROUND_UP
from uuid import UUID
from types import SimpleNamespace
import json,subprocess,sys,os
from pathlib import Path
import pytest
from app.execution import ExecutionRequest,ExecutionAuthorization
from app.execution.actions import intent_action
from app.execution.paper import *
from app.execution.paper.accounting import cash_after,full_close
from app.execution.paper.contracts import restore_result
from app.execution.paper.validation import reconcile
from app.risk import PortfolioScope,Environment,Direction,PositionRiskSnapshot,RiskState,to_wire
from app.market_intelligence.types import DatasetContext
from app.risk.integrated.inspection import inspect_risk
from app.risk.trade_risk import TradeRiskCosts
from app.strategy_lab.numeric import exact_product,exact_sum
from test_integrated_risk import inputs,AT,T,ID
from test_trade_risk_sizing import intent,market as risk_market,assumptions
from test_portfolio_risk import portfolio
from test_safety_gate import policy as safety_policy,health,metrics
from test_paper_fill import costs,market as fill_market
from app.execution.paper_fill import calculate_paper_fill
D=Decimal


def empty_book(**changes):
    args=dict(portfolio_id=UUID(int=3),user_id=UUID(int=2),environment='PAPER',base_currency='USD',cash_balance=D('10000'),status='active',updated_at=T-timedelta(days=1),positions=(),accounting_identities=())
    args.update(changes);return PaperBookState(**args)


def command_for(book,*,price='100',stop='90',quantity='1',close=None,at=AT,nonce=ID,cost=None,direction=Direction.LONG,instrument=None):
    cost=cost or costs()
    scope=PortfolioScope(book.user_id,book.portfolio_id,Environment.PAPER,book.base_currency)
    instrument=instrument or DatasetContext(UUID(int=1),'BTC/USD','BINANCE','1h')
    if book.positions:instrument=replace(instrument,instrument_id=book.positions[0]['instrument_id'])
    context=risk_market(instrument=instrument)
    entry={'basis':'declared synthetic PAPER oracle'}
    if close is not None:entry.update(paper_action='EXIT_LONG',paper_position_identity=close)
    p=intent(scope=scope,instrument=instrument,market_context=context,reference_price=D(price),invalidation_price=D(stop),entry_context=entry,created_at=at,strategy_decision_identity=nonce,direction=direction)
    rows=[]
    for row in book.positions:
        mark=D(price)
        rows.append(PositionRiskSnapshot(row['position_id'],row['instrument_id'],row['canonical_symbol'],Direction.LONG,row['quantity'],row['strategy_configuration_identity'],mark,exact_product(mark,row['quantity']),D('.1')))
    exposure=exact_sum(tuple(x.exposure_amount for x in rows))
    snap=portfolio(tuple(rows),scope=scope,valuation_identity=book.state_identity,available_cash=book.cash_balance,equity=exact_sum((book.cash_balance,exposure)),as_of=max(T,book.updated_at),risk_state=RiskState.NORMAL)
    cm=cost.cost_model
    rc=TradeRiskCosts(cm.trading_fee_rate,cm.trading_fee_rate,cm.spread_rate,cm.spread_rate,cm.slippage_rate,cm.slippage_rate)
    sizing=assumptions(entry_reference_price=D(price),entry_context=context,proposed_quantity=D(quantity),costs=rc)
    i=inputs(proposal=p,snapshot=snap,sizing=sizing,hp=safety_policy(expectancy_currency='USD'),health_evidence=health(p,metrics=metrics(expectancy_currency='USD')),at=at)
    r=ExecutionRequest(ExecutionAuthorization(p,inspect_risk(i)),'PAPER_INTERNAL',at)
    m=fill_market(r,reference_price=D(price),source_identity=context.data_identity,priced_at=at,observed_at=at)
    return PaperCommand(r,m,cost,at)


def calc(command):
    return calculate_paper_fill(command.request,command.market,command.assumptions,command.request.action,executed_at=command.executed_at,evaluated_at=command.executed_at)


def position_row(command):
    c=calc(command);r=command.request
    return dict(position_id=UUID(int=50),position_identity=ID,instrument_id=r.authorization.intent.instrument.instrument_id,canonical_symbol='BTC/USD',direction='LONG',quantity=r.quantity,average_entry_price=c.leg.modeled_execution_price,strategy_family=r.authorization.intent.strategy.family,strategy_version=r.authorization.intent.strategy.version,strategy_configuration_identity=r.authorization.intent.strategy.configuration_identity,entry_reference_notional=c.leg.reference_notional,entry_cost_basis=c.leg.gross_notional,entry_fee=c.leg.fee,entry_spread_cost=c.leg.spread_cost,entry_slippage_cost=c.leg.slippage_cost,opened_at=command.executed_at)


def test_hand_calculated_long_entry_and_close_oracle():
    book=empty_book();entry=command_for(book);buy=calc(entry)
    assert buy.leg.modeled_execution_price==D('102') and buy.leg.fee==D('1.02')
    assert cash_after(book.cash_balance,buy.leg)==D('9896.98')
    pos=position_row(entry)
    after=empty_book(cash_balance=D('9896.98'),positions=(pos,),updated_at=AT,accounting_identities=(ID,))
    close=command_for(after,price='120',stop='108',close=ID,at=AT+timedelta(seconds=1))
    sell=calc(close)
    assert sell.leg.modeled_execution_price==D('117.6') and sell.leg.fee==D('1.176')
    assert full_close(pos,sell.leg)==(D('20'),D('13.404'))
    assert cash_after(after.cash_balance,sell.leg)==D('10013.404')
    reconcile(entry,book,AT);reconcile(close,after,close.executed_at)


@pytest.mark.parametrize('quantity',['.5','2'])
def test_partial_exit_and_oversell_are_explicitly_rejected(quantity):
    pos=position_row(command_for(empty_book()))
    from app.execution.paper_fill.pricing import PaperLegCalculation
    from app.strategies.decisions import DecisionType
    leg=PaperLegCalculation(Direction.LONG,DecisionType.EXIT_LONG,D('120'),D(quantity),costs())
    with pytest.raises(PaperExecutionError):full_close(pos,leg)


def test_insufficient_cash_never_borrows_or_resizes():
    with pytest.raises(PaperExecutionError):cash_after(D('1'),calc(command_for(empty_book())).leg)


@pytest.mark.parametrize('cash',['NaN','Infinity','-1'])
def test_book_rejects_unknown_nonfinite_or_negative_cash(cash):
    with pytest.raises(ValueError):empty_book(cash_balance=D(cash))


@pytest.mark.parametrize('field,value',[('cash_balance',D('9999')),('accounting_identities',('sha256:'+'f'*64,)),('status','disabled')])
def test_material_book_change_invalidates_approval(field,value):
    book=empty_book();command=command_for(book)
    with pytest.raises(PaperExecutionError):reconcile(command,replace(book,**{field:value}),AT)


def test_close_action_is_bound_in_exact_approved_intent_not_an_override():
    entry=command_for(empty_book());pos=position_row(entry)
    book=empty_book(positions=(pos,),cash_balance=D('9896.98'),updated_at=AT)
    close=command_for(book,close=ID,at=AT+timedelta(seconds=1))
    assert entry.request.side.value=='BUY' and close.request.side.value=='SELL'
    assert close.request.action.value=='EXIT_LONG'
    assert close.request.action_identity!=entry.request.action_identity
    assert close.request.authorization.decision.outcome.value=='APPROVED'
    from app.strategies.decisions import DecisionType
    with pytest.raises(ValueError):calculate_paper_fill(entry.request,entry.market,entry.assumptions,DecisionType.EXIT_LONG,executed_at=AT,evaluated_at=AT)


@pytest.mark.parametrize('marker',[None,'ENTER_SHORT','EXIT_SHORT','BUY','EXIT_LONG'])
def test_malformed_explicit_close_attribution_rejected(marker):
    context={'basis':'test','paper_action':marker,'paper_position_identity':'bad'}
    with pytest.raises(ValueError):intent_action(intent(entry_context=context))


def test_short_approval_cannot_pass_portfolio_orchestration_reconciliation():
    command=command_for(empty_book(),direction=Direction.SHORT,stop='110')
    with pytest.raises(PaperExecutionError,match='SHORT'):reconcile(command,empty_book(),AT)


@pytest.mark.parametrize('change',['expiry','market_source','market_price','costs','asof','scope'])
def test_revalidation_rejects_stale_or_changed_context(change):
    book=empty_book();c=command_for(book)
    if change=='expiry':
        with pytest.raises(ValueError):c.request.authorization.validate_at(AT+timedelta(minutes=1))
    elif change=='market_source':
        with pytest.raises(PaperExecutionError):reconcile(replace(c,market=replace(c.market,source_identity='sha256:'+'e'*64)),book,AT)
    elif change=='market_price':
        with pytest.raises(PaperExecutionError):reconcile(replace(c,market=replace(c.market,reference_price=D('101'))),book,AT)
    elif change=='costs':
        with pytest.raises(PaperExecutionError):reconcile(replace(c,assumptions=costs(trading_fee_rate=D('.02'))),book,AT)
    elif change=='asof':
        with pytest.raises(PaperExecutionError):reconcile(c,book,AT+timedelta(seconds=1))
    else:
        with pytest.raises(PaperExecutionError):reconcile(c,replace(book,user_id=UUID(int=99)),AT)


def test_immutable_deterministic_command_and_equivalent_utc():
    c=command_for(empty_book())
    assert c.command_identity==command_for(empty_book()).command_identity
    assert replace(c,executed_at=AT.astimezone(timezone(timedelta(hours=1)))).command_identity==c.command_identity
    assert replace(c,assumptions=costs(trading_fee_rate=D('.02'))).command_identity!=c.command_identity
    with pytest.raises(FrozenInstanceError):c.executed_at=AT


def test_book_identity_and_long_arithmetic_ignore_ambient_decimal_rounding():
    c=command_for(empty_book());pos=position_row(c)
    from app.execution.paper_fill.pricing import PaperLegCalculation
    from app.strategies.decisions import DecisionType
    with localcontext() as ctx:
        ctx.prec=4;ctx.rounding=ROUND_UP
        leg=PaperLegCalculation(Direction.LONG,DecisionType.EXIT_LONG,D('120'),D('1'),costs())
        assert full_close(pos,leg)==(D('20'),D('13.404'))


def test_result_exact_wire_round_trip():
    c=command_for(empty_book());r=c.request
    result=PaperEconomicResult(r.request_identity,c.command_identity,r.intent_identity,r.risk_decision_identity,r.order_identity,ID,ID,UUID(int=5),'ENTER_LONG',D('1'),D('102'),D('102'),D('1.02'),D('10000'),D('9896.98'),None,None,None,None,AT)
    assert restore_result(to_wire(result))==result
    assert to_wire(result)['cash_after']=='9896.98'
    with pytest.raises(FrozenInstanceError):result.cash_after=D('0')


def test_four_hash_seeds_preserve_paper_command_and_book_identity():
    script="""import sys,json
sys.path.insert(0,'tests')
from test_paper_execution import empty_book,command_for
b=empty_book();c=command_for(b)
print(json.dumps({'book':b.state_identity,'command':c.command_identity},sort_keys=True))
"""
    result=[]
    for seed in ('1','2','3','4'):
        r=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        result.append(json.loads(r.stdout))
    assert all(x==result[0] for x in result)



def test_explicit_unknown_action_without_target_cannot_mean_entry():
    with pytest.raises(ValueError):intent_action(intent(entry_context={'paper_action':None,'basis':'test'}))


def test_receipt_tampering_cannot_preserve_result_identity():
    c=command_for(empty_book());r=c.request
    result=PaperEconomicResult(r.request_identity,c.command_identity,r.intent_identity,r.risk_decision_identity,r.order_identity,ID,ID,UUID(int=5),'ENTER_LONG',D('1'),D('102'),D('102'),D('1.02'),D('10000'),D('9896.98'),None,None,None,None,AT)
    wire=to_wire(result);wire['cash_after']='9999'
    with pytest.raises(PaperExecutionError):restore_result(wire)


def test_0008_frozen_migration_compiles_offline_without_connections():
    import importlib.util,io
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    path=Path(__file__).parents[1]/'alembic/versions/0008_paper_accounting.py'
    spec=importlib.util.spec_from_file_location('expected_11d_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=io.StringIO();context=MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})
    with Operations.context(context):module.upgrade()
    sql=output.getvalue()
    assert module.down_revision=='0007_execution_artifacts'
    assert sql.count('CREATE TABLE ')==2
    assert 'ALTER TABLE paper_portfolios ALTER COLUMN cash_balance TYPE NUMERIC' in sql
    assert "action='EXIT_LONG'" in sql
    assert 'paper_position_guard' in sql and 'paper_accounting_immutable' in sql
    assert 'UPDATE paper_portfolios' not in sql and 'INSERT INTO paper_portfolios' not in sql


def test_paper_models_exact_precision_constraints_and_open_position_uniqueness():
    from app.execution.paper.models import positions,accounting
    from app.portfolio.models import PaperPortfolio
    assert PaperPortfolio.__table__.c.cash_balance.type.scale is None
    assert PaperPortfolio.__table__.c.starting_balance.type.scale==2
    assert any(i.unique and i.name=='uq_paper_position_open_instrument' for i in positions.indexes)
    assert positions.c.opened_at.type.timezone and accounting.c.executed_at.type.timezone
    assert accounting.c.net_realized_pnl.type.scale is None
    assert set(positions.c.status.type.__class__.__name__) # concrete SQL type, no new lifecycle enum


def test_paper_service_has_no_network_api_clock_or_execution_adapter_dependency():
    import ast
    folder=Path(__file__).parents[1]/'app/execution/paper'
    for file in folder.glob('*.py'):
        for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
            if isinstance(node,ast.Constant):assert not isinstance(node.value,float)
            if isinstance(node,ast.Call):
                name=ast.unparse(node.func)
                assert not name.endswith(('.commit','.connect','.now','.utcnow','.submit','.request'))
                assert name not in ('float','create_engine')
            if isinstance(node,ast.ImportFrom):assert not any(x in (node.module or '') for x in ('httpx','fastapi','app.venues','app.market_data'))


def test_current_state_evidence_cannot_be_mutated_through_nested_positions():
    pos=position_row(command_for(empty_book()));book=empty_book(positions=(pos,))
    original=book.state_identity;pos['quantity']=D('999')
    assert book.positions[0]['quantity']==D('1') and book.state_identity==original
    with pytest.raises(TypeError):book.positions[0]['quantity']=D('2')
