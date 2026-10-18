"""Hand-checkable causal accounting; no real datasets, credentials or providers."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import UUID
import subprocess
import sys
import pytest
from app.market_intelligence.types import Candle, DatasetContext
from app.strategies.configuration import StrategyDefinition
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.inputs import PositionState
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.replay import replay_dataset
from app.strategy_lab.execution import simulate_execution
from app.strategy_lab.accounting import account_simulation
from app.strategy_lab.accounting.trades import complete_trade
from app.strategy_lab.numeric import difference, exact_product, exact_sum, ratio
from app.strategy_lab import metrics

D=Decimal
START=datetime(2025,1,1,tzinfo=timezone.utc)
AS_OF=START+timedelta(days=100)
CONTEXT=DatasetContext(UUID(int=1),"BTC/USDT","BINANCE","1h")
ZERO=ResearchCostModel(D(0),D(0),D(0))
ENTER=DecisionType.ENTER_LONG
EXIT=DecisionType.EXIT_LONG
SHORT=DecisionType.ENTER_SHORT
COVER=DecisionType.EXIT_SHORT
NONE=DecisionType.NO_ACTION


class ScenarioStrategy:
    def __init__(self,actions):
        self.actions=tuple(actions)
        self.definition=StrategyDefinition("ACCOUNTING_TEST","1.0.0",{"scenario":tuple(action.value for action in actions)})
    def decide(self,observation):
        index=(observation.candle.open_time-START)//timedelta(hours=1)-52
        action=self.actions[index] if index<len(self.actions) else NONE
        return StrategyDecision(observation,action,("SYNTHETIC_RESEARCH",),{"close":observation.candle.close})


def data(opens=(100,100,110),closes=None):
    closes=closes or opens
    prices=[(D(100),D(100))]*52+[(D(o),D(c)) for o,c in zip(opens,closes,strict=True)]
    rows=tuple(Candle(CONTEXT.instrument_id,"BINANCE","1h",START+timedelta(hours=i),
        START+timedelta(hours=i+1,milliseconds=-1),o,max(o,c)+D(1),min(o,c)-D(1),c,D(1),
        START+timedelta(days=10)) for i,(o,c) in enumerate(prices))
    return create_dataset(CONTEXT,rows,warmup_start=START,evaluation_start=rows[52].open_time,
        evaluation_end=rows[-1].open_time+timedelta(hours=1),as_of=AS_OF)


def artifacts(opens=(100,100,110),closes=None,actions=(ENTER,EXIT),costs=ZERO,quantity=D(1)):
    dataset=data(opens,closes)
    replay=replay_dataset(dataset,ScenarioStrategy(actions),as_of=AS_OF)
    execution=simulate_execution(dataset,replay,quantity=quantity,cost_model=costs,as_of=AS_OF)
    return dataset,replay,execution


def account(*,opens=(100,100,110),closes=None,actions=(ENTER,EXIT),costs=ZERO,quantity=D(1),capital=D(1000)):
    args=artifacts(opens,closes,actions,costs,quantity)
    return account_simulation(*args,initial_capital=capital,as_of=AS_OF)


@pytest.mark.parametrize("direction,exit_price,expected",[("long",110,D(10)),("long",90,D(-10)),
    ("short",90,D(10)),("short",110,D(-10)),("long",100,D(0)),("short",100,D(0))])
def test_profitable_losing_and_breakeven_long_short(direction,exit_price,expected):
    result=account(opens=(100,100,exit_price),actions=(ENTER,EXIT) if direction=="long" else (SHORT,COVER))
    trade=result.completed_trades[0]
    assert trade.gross_pnl==trade.net_pnl==expected
    assert trade.direction is (PositionState.LONG if direction=="long" else PositionState.SHORT)
    assert result.equity_curve[-1].cash==D(1000)+expected
    assert result.equity_curve[-1].total_equity==D(1000)+expected
    assert result.performance.total_return==expected/D(1000)
    assert result.performance.gross_return==result.performance.net_return==expected/D(1000)
    assert result.open_position is None
    assert result.performance.trade_count==1


@pytest.mark.parametrize("fee,spread,slip,expected",[
    (".01","0","0","7.9"),("0",".02","0","7.9"),("0","0",".01","7.9"),
    (".001",".02",".005","6.64015"),("0","0","0","10")])
def test_long_cost_isolation_and_exact_combined_reconciliation(fee,spread,slip,expected):
    costs=ResearchCostModel(D(fee),D(spread),D(slip))
    result=account(costs=costs)
    trade=result.completed_trades[0]
    assert trade.net_pnl==D(expected)
    assert difference(trade.gross_pnl,trade.costs.total)==trade.net_pnl
    fill_economic=difference(trade.exit_fill_price,trade.entry_fill_price)
    assert difference(fill_economic,trade.costs.fees)==trade.net_pnl
    assert result.equity_curve[-1].cash==exact_sum((D(1000),trade.net_pnl))
    assert result.performance.total_fees==trade.costs.fees
    assert result.performance.total_spread_cost==trade.costs.spread_cost
    assert result.performance.total_slippage_cost==trade.costs.slippage_cost


def test_short_combined_costs_use_reference_movement_without_double_counting():
    result=account(opens=(100,100,90),actions=(SHORT,COVER),costs=ResearchCostModel(D(".001"),D(".02"),D(".005")))
    trade=result.completed_trades[0]
    assert trade.gross_pnl==10
    assert trade.entry_fill_price==D("98.5") and trade.exit_fill_price==D("91.35")
    assert trade.costs.fees==D(".18985")
    assert trade.costs.spread_cost==D("1.9") and trade.costs.slippage_cost==D(".95")
    assert trade.net_pnl==D("6.96015")
    assert result.equity_curve[-1].total_equity==D("1006.96015")


def test_multiple_trades_win_loss_breakeven_denominators_and_metrics():
    result=account(opens=(100,100,110,100,120,100,100),actions=(ENTER,EXIT,SHORT,COVER,ENTER,EXIT))
    assert [trade.net_pnl for trade in result.completed_trades]==[D(10),D(-20),D(0)]
    p=result.performance
    assert p.trade_count==3
    assert p.win_rate==p.loss_rate==ratio(D(1),D(3))
    assert p.profit_factor==D(".5")
    assert p.average_win==D(10) and p.average_loss==D(20)
    assert p.expectancy==ratio(D(-10),D(3))
    assert p.gross_return==p.net_return==p.total_return==D("-.01")
    assert result.equity_curve[-1].cash==D(990)
    assert result.completed_trade_totals.net_pnl==D(-10)


@pytest.mark.parametrize("actions,prices,expected",[((),(100,100,100),None),
    ((ENTER,EXIT),(100,100,110),None),((ENTER,EXIT),(100,100,100),None),((ENTER,EXIT),(100,100,90),D(0))])
def test_profit_factor_undefined_cases(actions,prices,expected):
    result=account(opens=prices,actions=actions)
    assert result.performance.profit_factor==expected
    if not actions:
        assert result.performance.win_rate is result.performance.loss_rate is None
        assert result.performance.average_win is result.performance.average_loss is result.performance.expectancy is None


def test_open_long_retained_and_marked_not_realized_or_exited():
    costs=ResearchCostModel(D(".01"),D(".02"),D(0))
    result=account(opens=(100,100,120),actions=(ENTER,),costs=costs)
    last=result.equity_curve[-1]
    assert not result.completed_trades
    assert result.open_position.direction is PositionState.LONG
    assert result.open_position.entry_price==D(101)
    assert last.cash==D("897.99")
    assert last.realized_capital==D("998.99")
    assert last.realized_pnl==D("-1.01")  # Only entry fee is realized.
    assert last.gross_realized_pnl==last.completed_trade_net_pnl==0
    assert last.unrealized_pnl==D(19)
    assert last.total_equity==D("1017.99")
    assert last.total_equity==exact_sum((last.realized_capital,last.unrealized_pnl))
    assert result.performance.net_return==0 and result.performance.gross_return==0
    assert result.performance.total_return==D(".01799")
    assert result.performance.total_fees==D("1.01")
    assert result.performance.total_spread_cost==D(1)
    assert result.performance.trade_count==0
    assert result.open_position.entry_fill==result.execution.fills[0]


def test_open_short_segregates_proceeds_and_collateral_with_unrealized_mark():
    costs=ResearchCostModel(D(".01"),D(".02"),D(0))
    result=account(opens=(100,100,80),actions=(SHORT,),costs=costs)
    last=result.equity_curve[-1]
    assert result.open_position.direction is PositionState.SHORT
    assert last.cash==D("899.01")
    assert last.reserved_capital==D(100)
    assert last.restricted_short_proceeds==D(99)
    assert last.realized_capital==D("999.01")
    assert last.realized_pnl==D("-.99")
    assert last.unrealized_pnl==D(19)
    assert last.total_equity==D("1018.01")
    assert last.total_equity==exact_sum((last.cash,last.reserved_capital,last.restricted_short_proceeds,D(-80)))
    assert not result.completed_trades
    assert result.performance.net_return==0 and result.performance.total_fees==D(".99")


def test_pending_final_entry_has_no_cost_no_position_and_no_fabricated_exit():
    result=account(opens=(100,),actions=(ENTER,),costs=ResearchCostModel(D(".01"),D(".02"),D(".01")))
    assert not result.execution.fills
    assert len(result.execution.unfilled_decisions)==1
    assert result.open_position is None and not result.completed_trades
    assert result.equity_curve[0].total_equity==D(1000)
    assert result.incurred_costs.total==0


def test_pending_exit_leaves_long_open_without_realizing_gain():
    result=account(opens=(100,100,120),actions=(ENTER,NONE,EXIT))
    assert len(result.execution.unfilled_decisions)==1
    assert result.open_position.direction is PositionState.LONG
    assert not result.completed_trades
    assert result.equity_curve[-1].unrealized_pnl==D(20)
    assert result.equity_curve[-1].realized_pnl==0


def test_no_trades_cash_equity_and_undefined_zero_capital_metrics():
    result=account(actions=(),capital=D(0))
    assert not result.completed_trades and not result.open_position
    assert all(row.total_equity==0 and row.cash==0 for row in result.equity_curve)
    assert result.performance.total_return is result.performance.gross_return is result.performance.net_return is None
    assert result.performance.maximum_drawdown is result.performance.turnover is None
    assert result.performance.exposure_percentage==0


def test_drawdown_uses_initial_capital_then_actual_marked_equity():
    result=account(opens=(100,100,100,100),closes=(100,120,90,100),actions=(ENTER,NONE,EXIT))
    assert [row.total_equity for row in result.equity_curve]==[D(1000),D(1020),D(990),D(1000)]
    assert result.performance.maximum_drawdown==ratio(D(30),D(1020))
    fee_first=account(opens=(100,100),actions=(ENTER,),costs=ResearchCostModel(D(".01"),D(0),D(0)))
    assert fee_first.performance.maximum_drawdown==D(".001")


def test_exposure_and_turnover_use_actual_fill_intervals_and_unsigned_adjusted_legs():
    result=account()
    assert result.performance.exposure_percentage==ratio(D(100),D(3))
    assert result.performance.turnover==D(".21")
    delayed=account(opens=(100,100,100,110,110),actions=(ENTER,EXIT),costs=replace(ZERO,execution_delay=timedelta(hours=1)))
    assert delayed.performance.exposure_percentage==D(20)
    assert delayed.performance.turnover==D(".21")
    open_result=account(actions=(ENTER,))
    assert open_result.performance.exposure_percentage==ratio(D(200),D(3))
    assert open_result.performance.turnover==D(".1")


@pytest.mark.parametrize("direction",["long","short"])
def test_insufficient_initial_funding_rejected_without_resizing(direction):
    with pytest.raises(ValueError,match="Insufficient"):
        account(actions=(ENTER,) if direction=="long" else (SHORT,),quantity=D(2),capital=D(199))
    with pytest.raises(ValueError):
        account(actions=(ENTER,) if direction=="long" else (SHORT,),capital=D(100),costs=ResearchCostModel(D(".01"),D(0),D(0)))


def test_long_funding_exact_boundary_includes_entry_fee():
    result=account(opens=(100,100),actions=(ENTER,),capital=D(101),costs=ResearchCostModel(D(".01"),D(0),D(0)))
    assert result.equity_curve[-1].cash==0
    assert result.equity_curve[-1].total_equity==D(100)
    with pytest.raises(ValueError):
        account(opens=(100,100),actions=(ENTER,),capital=D("100.9999999999999999999999999"),costs=ResearchCostModel(D(".01"),D(0),D(0)))


def test_short_one_times_equity_limit_at_mark_and_entry_reference():
    exact=account(opens=(100,100),actions=(SHORT,),capital=D(100))
    assert exact.equity_curve[-1].cash==0 and exact.equity_curve[-1].total_equity==D(100)
    with pytest.raises(ValueError,match="one-times"):
        account(opens=(100,100,101),actions=(SHORT,),capital=D(100))
    with pytest.raises(ValueError,match="one-times"):
        account(opens=(100,100),actions=(SHORT,),capital=D(100),costs=ResearchCostModel(D(0),D(".02"),D(0)))


def test_short_cover_cannot_create_negative_cash_or_hidden_borrowing():
    with pytest.raises(ValueError,match="settle"):
        account(opens=(100,100,4000),actions=(SHORT,COVER),capital=D(1000))


@pytest.mark.parametrize("capital",[D(-1),D("NaN"),D("Infinity"),1.0,1,True,None])
def test_invalid_capital_rejected(capital):
    with pytest.raises(ValueError): account(capital=capital)


def test_non_cash_initial_replay_requires_missing_entry_basis_instead_of_fabrication():
    dataset=data()
    replay=replay_dataset(dataset,ScenarioStrategy(()),as_of=AS_OF,initial_state=PositionState.LONG)
    execution=simulate_execution(dataset,replay,quantity=D(1),cost_model=ZERO,as_of=AS_OF)
    with pytest.raises(ValueError,match="CASH start"):
        account_simulation(dataset,replay,execution,initial_capital=D(1000),as_of=AS_OF)


def test_costs_reconcile_across_completed_trades_and_open_entry():
    costs=ResearchCostModel(D(".001"),D(".02"),D(".005"))
    result=account(opens=(100,100,110,100,120),actions=(ENTER,EXIT,ENTER),costs=costs,quantity=D(2))
    closed=result.completed_trades[0]; open_fill=result.open_position.entry_fill
    assert result.incurred_costs.fees==exact_sum((closed.costs.fees,open_fill.pricing.fee_amount))
    assert result.incurred_costs.spread_cost==exact_sum((closed.costs.spread_cost,open_fill.pricing.spread_cost))
    assert result.incurred_costs.slippage_cost==exact_sum((closed.costs.slippage_cost,open_fill.pricing.slippage_cost))
    last=result.equity_curve[-1]
    assert last.realized_pnl==difference(closed.net_pnl,open_fill.pricing.fee_amount)
    assert last.completed_trade_net_pnl==closed.net_pnl
    assert last.total_equity==exact_sum((D(1000),last.realized_pnl,last.unrealized_pnl))
    assert result.performance.total_fees==result.incurred_costs.fees
    assert result.performance.net_return==ratio(closed.net_pnl,D(1000))


def test_repeated_runs_decimal_scale_and_ambient_context_are_deterministic():
    args=artifacts(costs=ResearchCostModel(D(".001"),D(".02"),D(".005")),quantity=D(".1234567890123456789"))
    first=account_simulation(*args,initial_capital=D(1000),as_of=AS_OF)
    with localcontext() as ctx:
        ctx.prec=6
        second=account_simulation(*args,initial_capital=D("1000.000"),as_of=AS_OF+timedelta(days=1))
    assert first==second
    assert first.result_identity==second.result_identity
    assert first.completed_trades[0].net_pnl==difference(first.completed_trades[0].gross_pnl,first.completed_trades[0].costs.total)


def test_appended_future_cannot_change_earlier_equity_or_completed_trades():
    actions=(ENTER,EXIT,SHORT,COVER,ENTER)
    early=account(opens=(100,100,110),actions=actions)
    later=account(opens=(100,100,110,100,90,100,120),actions=actions)
    assert early.equity_curve==later.equity_curve[:3]
    assert early.completed_trades==later.completed_trades[:1]
    assert early.result_identity!=later.result_identity


def test_price_marks_are_exact_completed_closes_and_warmup_is_excluded():
    args=artifacts(opens=(100,100,110),closes=(101,103,111))
    result=account_simulation(*args,initial_capital=D(1000),as_of=AS_OF)
    assert len(result.equity_curve)==3
    assert [row.mark_price for row in result.equity_curve]==[D(101),D(103),D(111)]
    assert [row.timestamp for row in result.equity_curve]==[row.close_time for row in args[0].evaluation_candles]
    assert all(row.mark_open_time>=args[0].evaluation_start for row in result.equity_curve)
    assert all(row.price_source=="completed_evaluation_candle_close" for row in result.equity_curve)


def test_result_composes_provenance_and_is_deeply_immutable():
    args=artifacts();result=account_simulation(*args,initial_capital=D(1000),as_of=AS_OF)
    assert result.dataset_fingerprint==args[0].dataset_fingerprint
    assert result.replay is args[1] and result.execution is args[2]
    assert result.accounting_configuration.version=="1.0.0"
    assert result.completed_trades[0].entry_fill is result.execution.fills[0]
    assert result.completed_trades[0].exit_fill is result.execution.fills[1]
    assert result.result_identity.startswith("sha256:")
    with pytest.raises(FrozenInstanceError): result.initial_capital=D(1)
    with pytest.raises(FrozenInstanceError): result.equity_curve[0].cash=D(1)
    with pytest.raises(FrozenInstanceError): result.completed_trades[0].net_pnl=D(1)
    with pytest.raises(FrozenInstanceError): result.performance.total_return=D(1)


def test_initial_capital_changes_result_and_observation_identity():
    first=account();second=account(capital=D(2000))
    assert first.result_identity!=second.result_identity
    assert first.equity_curve[0].observation_identity!=second.equity_curve[0].observation_identity


def test_bad_fill_prices_or_costs_cannot_bypass_part2_integrity():
    dataset,replay,execution=artifacts()
    fill=execution.fills[0]
    bad=replace(fill,pricing=replace(fill.pricing,fill_price=D(1)))
    with pytest.raises(ValueError,match="artifacts"):
        account_simulation(dataset,replay,replace(execution,fills=(bad,*execution.fills[1:])),initial_capital=D(1000),as_of=AS_OF)


def test_metrics_delegate_to_existing_9a_primitives(monkeypatch):
    names=[item.name for item in fields(type(account().performance))]
    calls=[]
    for name in names:
        original=getattr(metrics,name)
        def wrapped(*args,_name=name,_fn=original,**kwargs):
            calls.append(_name)
            return _fn(*args,**kwargs)
        monkeypatch.setattr(metrics,name,wrapped)
    result=account()
    assert set(names)<=set(calls)
    assert result.performance.net_return==D(".01")


def test_no_strategy_indicator_or_regime_calculation_in_accounting(monkeypatch):
    from app.market_intelligence import engine as intelligence
    from app.market_regimes import engine as regimes
    from app.strategies import contract
    args=artifacts()
    def forbidden(*args,**kwargs): raise AssertionError("No analytical/strategy recalculation")
    monkeypatch.setattr(intelligence,"calculate_features",forbidden)
    monkeypatch.setattr(regimes,"classify_regimes",forbidden)
    monkeypatch.setattr(contract,"evaluate_strategy",forbidden)
    assert account_simulation(*args,initial_capital=D(1000),as_of=AS_OF).performance.trade_count==1


def test_accounting_never_mutates_source_inputs():
    args=artifacts();before=repr(args)
    account_simulation(*args,initial_capital=D(1000),as_of=AS_OF)
    assert repr(args)==before


def test_fresh_process_imports_without_database_provider_or_web_dependencies():
    source="""
import sys
from app.strategy_lab.accounting import account_simulation
assert not any(any(n == x or n.startswith(x + '.') for x in
    ('sqlalchemy','fastapi','httpx','requests','app.db','app.market_data','app.core.config')) for n in sys.modules)
"""
    result=subprocess.run([sys.executable,"-c",source],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize("short,exit_price",[(False,90),(False,110),(True,90),(True,110)])
def test_high_precision_profitable_and_losing_cost_reconciliation(short,exit_price):
    costs=ResearchCostModel(D(".00123"),D(".00345"),D(".00089"))
    quantity=D(".12345678901234567890123456789")
    result=account(opens=(100,100,exit_price),actions=(SHORT,COVER) if short else (ENTER,EXIT),costs=costs,quantity=quantity)
    trade=result.completed_trades[0]
    reference_move=D(100-exit_price) if short else D(exit_price-100)
    assert trade.gross_pnl==exact_product(reference_move,quantity)
    assert difference(trade.gross_pnl,trade.costs.total)==trade.net_pnl
    adjusted_move=difference(trade.entry_fill_price,trade.exit_fill_price) if short else difference(trade.exit_fill_price,trade.entry_fill_price)
    assert difference(exact_product(adjusted_move,quantity),trade.costs.fees)==trade.net_pnl
    assert result.equity_curve[-1].cash==exact_sum((D(1000),trade.net_pnl))


def test_later_entry_must_fit_capital_after_prior_loss():
    with pytest.raises(ValueError,match="Insufficient"):
        account(opens=(100,100,90,100),actions=(ENTER,EXIT,ENTER),capital=D(105))


def test_positive_cash_only_result_has_zero_returns_and_zero_costs():
    result=account(actions=())
    assert result.performance.total_return==result.performance.gross_return==result.performance.net_return==0
    assert result.performance.maximum_drawdown==result.performance.turnover==0
    assert result.performance.exposure_percentage==0
    assert result.incurred_costs.total==0


def test_malformed_execution_configuration_rejected():
    dataset,replay,execution=artifacts()
    with pytest.raises(ValueError):
        account_simulation(dataset,replay,replace(execution,configuration=None),initial_capital=D(1000),as_of=AS_OF)
