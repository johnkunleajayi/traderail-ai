"""Final 9C composed audit: real default Adaptive Trend, synthetic candles, literal oracles.

No scripted strategy, fabricated analytics, provider data or threshold changes. Reusable
immutable cases are cached only in this test harness; fresh-run tests bypass replay caching.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from uuid import UUID
import pytest
from app.market_intelligence.types import Candle, DatasetContext
from app.market_intelligence import engine as intelligence_engine
from app.market_regimes import engine as regime_engine
from app.strategies import contract
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.decisions import DecisionType
from app.strategies.inputs import PositionState
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.replay import replay_dataset, transition_state
from app.strategy_lab.execution import simulate_execution
from app.strategy_lab.accounting import account_simulation

D=Decimal
F=Fraction
START=datetime(2025,1,1,tzinfo=timezone.utc)
AS_OF=START+timedelta(days=60)
CONTEXT=DatasetContext(UUID(int=1),"BTC/USDT","BINANCE","1h")
CAPITAL=D(10000)
COSTS=ResearchCostModel(D(".001"),D(".002"),D(".001"))
HIGH_COSTS=replace(COSTS,trading_fee_rate=D(".01"))
# Direction times (+1,+1,-1): sustained drift with pullbacks, never a forced decision.
# The explicit 128-candle warm-up is part of every frozen dataset's fingerprint.
SPECS={
    "long_profit":((-1,100),(1,100),(-1,19)),
    "long_loss":((-1,100),(1,32),(-1,13)),
    "short_profit":((1,100),(-1,100),(1,19)),
    "short_loss":((1,100),(-1,32),(1,13)),
    "multiple":((-1,100),(1,100),(-1,100),(1,19)),
    "no_trades":((0,140),),
    "open_long":((-1,100),(1,32)),
    "open_short":((1,100),(-1,32)),
    "unfilled":((-1,100),(1,31)),
    "small_winner":((-1,100),(1,55),(-1,17)),
    "gap_entry":((-1,100),(1,32)),
}


@lru_cache(None)
def frozen_dataset(name):
    value=D(200); previous=D(200); rows=[]
    for direction,length in SPECS[name]:
        for offset in range(length):
            value+=D(direction)*D((1,1,-1)[offset%3])
            index=len(rows)
            opening=D(180) if name=="gap_entry" and index==131 else previous
            rows.append(Candle(CONTEXT.instrument_id,CONTEXT.provider,CONTEXT.timeframe,
                START+timedelta(hours=index),START+timedelta(hours=index+1,milliseconds=-1),
                opening,max(opening,value)+D(".1"),min(opening,value)-D(".1"),value,D(10),START+timedelta(days=20)))
            previous=value
    return create_dataset(CONTEXT,tuple(rows),warmup_start=START,evaluation_start=rows[128].open_time,
        evaluation_end=rows[-1].open_time+timedelta(hours=1),as_of=AS_OF)


@lru_cache(None)
def cached_replay(name):
    return replay_dataset(frozen_dataset(name),AdaptiveTrendStrategy(),as_of=AS_OF)


def pipeline(name,*,costs=COSTS,quantity=D(1),capital=CAPITAL,fresh=False):
    dataset=frozen_dataset(name)
    replay=replay_dataset(dataset,AdaptiveTrendStrategy(),as_of=AS_OF) if fresh else cached_replay(name)
    execution=simulate_execution(dataset,replay,quantity=quantity,cost_model=costs,as_of=AS_OF)
    return account_simulation(dataset,replay,execution,initial_capital=capital,as_of=AS_OF)


@lru_cache(None)
def case(name):
    return pipeline("small_winner",costs=HIGH_COSTS) if name=="cost_flip" else pipeline(name)


# Independently calculated with rational cash debits/credits, not production helpers.
# Fields: entry index, exit decision index, entry/exit reference, entry/exit adjusted,
# total fees, spread, slippage, gross, net, ending realized capital/equity.
ORACLES={
    "long_profit":(130,217,"177","194","177.354","193.612",".370966",".371",".371","17","15.887034","10015.887034"),
    "long_loss":(130,143,"177","174","177.354","173.652",".351006",".351",".351","-3","-4.053006","9995.946994"),
    "short_profit":(130,217,"223","206","222.554","206.412",".428966",".429",".429","17","15.713034","10015.713034"),
    "short_loss":(130,143,"223","226","222.554","226.452",".449006",".449",".449","-3","-4.347006","9995.652994"),
    "cost_flip":(130,170,"177","179","177.354","178.642","3.55996",".356",".356","2","-2.27196","9997.72804"),
}


@pytest.mark.parametrize("name",list(ORACLES))
def test_complete_default_adaptive_trend_matches_independent_literal_oracle(name):
    result=case(name)
    assert result.replay.strategy_definition.family=="ADAPTIVE_TREND"
    assert result.replay.strategy_definition.version=="1.0.0"
    assert result.replay.strategy_definition==AdaptiveTrendStrategy().definition
    assert len(result.completed_trades)==1
    enter,exit,*values=ORACLES[name]
    expected=tuple(D(value) for value in values)
    trade=result.completed_trades[0]; final=result.equity_curve[-1]
    actual=(trade.entry_reference_price,trade.exit_reference_price,trade.entry_fill_price,trade.exit_fill_price,
        trade.costs.fees,trade.costs.spread_cost,trade.costs.slippage_cost,trade.gross_pnl,trade.net_pnl,final.realized_capital)
    assert actual==expected
    assert trade.entry_timestamp==START+timedelta(hours=enter+1)
    assert trade.exit_timestamp==START+timedelta(hours=exit+1)
    assert final.cash==final.total_equity==expected[-1]
    assert final.unrealized_pnl==0 and result.open_position is None
    assert result.performance.total_return==F(expected[-1]-CAPITAL)/F(CAPITAL)
    assert result.performance.trade_count==1


def test_multiple_sequential_actual_adaptive_trend_trades_have_independent_cash_total():
    result=case("multiple")
    assert len(result.completed_trades)==2
    first,second=result.completed_trades
    assert (first.direction,second.direction)==(PositionState.LONG,PositionState.SHORT)
    assert first.net_pnl==D("15.887034")
    assert (second.entry_reference_price,second.exit_reference_price)==(D(190),D(172))
    assert (second.entry_fill_price,second.exit_fill_price)==(D("189.620"),D("172.344"))
    assert second.costs.fees==D(".361964")
    assert second.costs.spread_cost==second.costs.slippage_cost==D(".362")
    assert second.gross_pnl==D(18) and second.net_pnl==D("16.914036")
    assert result.equity_curve[-1].total_equity==D("10032.801070")
    assert result.completed_trade_totals.gross_pnl==D(35)
    assert result.open_position is None
    assert [row.decision.decision for row in result.replay.records if row.decision.decision is not DecisionType.NO_ACTION]==[
        DecisionType.ENTER_LONG,DecisionType.EXIT_LONG,DecisionType.ENTER_SHORT,DecisionType.EXIT_SHORT]


def test_no_trade_dataset_runs_through_real_intelligence_regime_and_strategy():
    result=case("no_trades")
    assert result.replay.records
    assert all(row.intelligence.rsi_14==50 for row in result.replay.records)
    assert all(row.regime.regime.value=="RANGE" for row in result.replay.records)
    assert all(row.decision.decision is DecisionType.NO_ACTION for row in result.replay.records)
    assert not result.execution.fills and not result.completed_trades and result.open_position is None
    assert all(row.total_equity==CAPITAL and row.position_state is PositionState.CASH for row in result.equity_curve)
    assert result.performance.trade_count==0
    assert result.performance.win_rate is result.performance.loss_rate is result.performance.profit_factor is None
    assert result.performance.total_return==result.performance.exposure_percentage==result.performance.turnover==0


@pytest.mark.parametrize("name,direction,entry,fee,mark,unrealized,realized,equity,cash,reserve,proceeds",[
    ("open_long",PositionState.LONG,"177.354",".177354","178",".646","9999.822646","10000.468646","9822.468646","0","0"),
    ("open_short",PositionState.SHORT,"222.554",".222554","222",".554","9999.777446","10000.331446","9776.777446","223","222.554"),
])
def test_open_position_independent_oracle_no_fabricated_realization(name,direction,entry,fee,mark,unrealized,realized,equity,cash,reserve,proceeds):
    result=case(name); last=result.equity_curve[-1]; position=result.open_position
    assert not result.completed_trades
    assert position.direction is last.position_state is direction
    assert position.entry_price==D(entry) and position.entry_fee_recognized==D(fee)
    assert last.mark_price==D(mark) and last.unrealized_pnl==D(unrealized)
    assert last.realized_capital==D(realized) and last.total_equity==D(equity)
    assert last.cash==D(cash) and last.reserved_capital==D(reserve) and last.restricted_short_proceeds==D(proceeds)
    assert last.realized_pnl==D(fee).copy_negate()
    assert last.gross_realized_pnl==last.completed_trade_net_pnl==0
    assert result.performance.gross_return==result.performance.net_return==0
    assert result.performance.trade_count==0
    assert len(result.execution.fills)==1


def test_final_unfilled_decision_and_future_fill_keep_previous_observations_intact():
    earlier,later=case("unfilled"),case("open_long")
    assert earlier.replay.final_state is PositionState.LONG
    assert earlier.execution.final_filled_state is PositionState.CASH
    assert earlier.equity_curve[-1].position_state is PositionState.CASH
    assert len(earlier.execution.unfilled_decisions)==1 and not earlier.execution.fills
    assert earlier.open_position is None and not earlier.completed_trades
    assert earlier.equity_curve[-1].cash==earlier.equity_curve[-1].total_equity==CAPITAL
    assert earlier.incurred_costs.total==0
    assert earlier.replay.records==later.replay.records[:len(earlier.replay.records)]
    assert earlier.equity_curve==later.equity_curve[:len(earlier.equity_curve)]
    assert later.execution.fills[0].decision==earlier.execution.unfilled_decisions[0].decision


def test_costs_alone_flip_small_gross_winner_without_changing_strategy():
    lower,higher=case("small_winner"),case("cost_flip")
    assert lower.replay==higher.replay
    assert lower.completed_trades[0].gross_pnl==higher.completed_trades[0].gross_pnl==2
    assert lower.completed_trades[0].net_pnl==D(".932004")
    assert higher.completed_trades[0].net_pnl==D("-2.27196")
    assert lower.performance.win_rate==1 and higher.performance.loss_rate==1
    assert [fill.pricing.fill_price for fill in lower.execution.fills]==[fill.pricing.fill_price for fill in higher.execution.fills]
    assert lower.execution.configuration.configuration_identity!=higher.execution.configuration.configuration_identity
    assert lower.result_identity!=higher.result_identity


@pytest.mark.parametrize("name",["long_profit","long_loss","short_profit","short_loss","multiple","cost_flip"])
def test_composed_cost_reconciliation_with_independent_fraction_arithmetic(name):
    result=case(name)
    for trade in result.completed_trades:
        fees=F(trade.costs.fees); spread=F(trade.costs.spread_cost); slip=F(trade.costs.slippage_cost)
        from_reference=F(trade.gross_pnl)-spread-slip-fees
        quantity=F(trade.quantity)
        # Separate rational cash-flow view, never production metric/pricing/accounting helpers.
        entry_cash=F(trade.entry_fill_price)*quantity
        exit_cash=F(trade.exit_fill_price)*quantity
        cash_profit=exit_cash-entry_cash if trade.direction is PositionState.LONG else entry_cash-exit_cash
        assert from_reference==cash_profit-fees==F(trade.net_pnl)
    assert F(result.equity_curve[-1].total_equity)==F(CAPITAL)+sum((F(t.net_pnl) for t in result.completed_trades),F(0))
    assert sum((F(fill.pricing.fee_amount) for fill in result.execution.fills),F(0))==F(result.performance.total_fees)


def test_composed_future_append_preserves_all_six_artifact_layers():
    earlier,later=case("long_profit"),case("multiple")
    count=len(earlier.replay.records)
    assert earlier.replay.records==later.replay.records[:count]
    assert [row.intelligence for row in earlier.replay.records]==[row.intelligence for row in later.replay.records[:count]]
    assert [row.regime for row in earlier.replay.records]==[row.regime for row in later.replay.records[:count]]
    assert [row.decision for row in earlier.replay.records]==[row.decision for row in later.replay.records[:count]]
    assert earlier.execution.fills==later.execution.fills[:len(earlier.execution.fills)]
    assert earlier.completed_trades==later.completed_trades[:len(earlier.completed_trades)]
    assert earlier.equity_curve==later.equity_curve[:len(earlier.equity_curve)]
    assert earlier.dataset_fingerprint!=later.dataset_fingerprint
    assert earlier.result_identity!=later.result_identity


@pytest.mark.parametrize("name",["long_profit","long_loss","short_profit","short_loss","multiple","no_trades","open_long","open_short","unfilled","cost_flip"])
def test_replay_thesis_and_delayed_filled_state_remain_consistent(name):
    result=case(name);dataset=frozen_dataset("small_winner" if name=="cost_flip" else name)
    thesis=PositionState.CASH
    for record in result.replay.records:
        assert record.position_before is thesis
        thesis=transition_state(thesis,record.decision.decision)
        assert record.position_after is thesis
    assert result.replay.final_state is thesis
    filled=PositionState.CASH;cursor=0
    for row,candle in zip(result.equity_curve,dataset.evaluation_candles,strict=True):
        while cursor<len(result.execution.fills) and result.execution.fills[cursor].eligible_fill_timestamp==candle.open_time:
            fill=result.execution.fills[cursor]
            assert fill.position_before is filled
            filled=transition_state(filled,fill.action)
            assert fill.position_after is filled
            assert fill.quantity==result.execution.quantity==D(1)
            # Default next-open shift, not erroneous equality of decision-time thesis and fill-time state.
            assert fill.eligible_fill_timestamp==fill.decision_timestamp+timedelta(milliseconds=1)
            cursor+=1
        assert row.position_state is filled
        assert row.quantity==(D(0) if filled is PositionState.CASH else D(1))
        assert row.cash>=0 and row.total_equity>=0
        assert F(row.total_equity)==F(row.realized_capital)+F(row.unrealized_pnl)
        if filled is PositionState.LONG:
            assert F(row.total_equity)==F(row.cash)+F(row.mark_price)*F(row.quantity)
        elif filled is PositionState.SHORT:
            assert F(row.total_equity)==F(row.cash)+F(row.reserved_capital)+F(row.restricted_short_proceeds)-F(row.mark_price)*F(row.quantity)
            assert F(row.mark_price)*F(row.quantity)<=F(row.total_equity)
    assert filled is result.execution.final_filled_state
    assert (result.open_position.direction if result.open_position else PositionState.CASH) is filled
    assert cursor==len(result.execution.fills)


def test_next_open_reference_is_not_same_close_even_when_synthetic_bar_gaps():
    result=case("gap_entry");fill=result.execution.fills[0]
    assert fill.decision.evidence["close"]==D(177)
    assert fill.pricing.reference_open==D(180)
    assert fill.pricing.fill_price==D("180.36")
    assert fill.pricing.fee_amount==D(".18036")
    assert result.equity_curve[-1].unrealized_pnl==D("-2.36")
    assert result.equity_curve[-1].total_equity==D("9997.45964")


def test_real_engines_receive_only_prefixes_and_shared_contract_uses_actual_strategy(monkeypatch):
    seen=[];data=frozen_dataset("long_loss")
    features=intelligence_engine.calculate_features;regimes=regime_engine.classify_regimes;evaluate=contract.evaluate_strategy
    def measure(context,rows,**kwargs):
        index=len(rows)-1
        assert tuple(rows)==data.candles[:index+1]
        seen.append(("intelligence",rows[-1].open_time))
        return features(context,rows,**kwargs)
    def classify(rows,**kwargs):
        assert len(rows)==(rows[-1].open_time-START)//timedelta(hours=1)+1
        seen.append(("regime",rows[-1].open_time))
        return regimes(rows,**kwargs)
    def decide(strategy,observation):
        assert isinstance(strategy,AdaptiveTrendStrategy)
        assert observation.definition==AdaptiveTrendStrategy().definition
        assert observation.regime.open_time==observation.intelligence.open_time==observation.candle.open_time
        seen.append(("strategy",observation.candle.open_time))
        return evaluate(strategy,observation)
    monkeypatch.setattr(intelligence_engine,"calculate_features",measure)
    monkeypatch.setattr(regime_engine,"classify_regimes",classify)
    monkeypatch.setattr(contract,"evaluate_strategy",decide)
    result=pipeline("long_loss",fresh=True)
    assert seen==[(stage,candle.open_time) for candle in data.evaluation_candles for stage in ("intelligence","regime","strategy")]
    assert result==case("long_loss")


def test_independent_drawdown_exposure_turnover_and_warmup_accounting():
    result=case("long_profit");curve=result.equity_curve
    assert len(curve)==91  # Explicit indices 128..218, never the warm-up candles.
    assert curve[0].mark_open_time==START+timedelta(hours=128)
    peak=F(CAPITAL);declines=[]
    for row in curve:
        value=F(row.total_equity);peak=max(peak,value);declines.append((peak-value)/peak)
    # Independent exact rational expectations, with only justified Decimal-50 division tolerance.
    assert abs(F(result.performance.maximum_drawdown)-max(declines))<F(1,10**49)
    # Fill 131 open through exit 218 open: 87 exposed hours out of 91 evaluation hours.
    assert abs(F(result.performance.exposure_percentage)-F(8700,91))<F(1,10**47)
    assert result.performance.turnover==D(".0370966")  # (177.354 + 193.612) / 10000.
    assert curve[-1].timestamp==frozen_dataset("long_profit").candles[-1].close_time


@pytest.mark.parametrize("name",["long_profit","short_profit"])
def test_unaffordable_real_adaptive_entries_rejected_without_resizing(name):
    source=cached_replay(name)
    with pytest.raises(ValueError,match="Insufficient"):
        pipeline(name,capital=D(1))
    execution=simulate_execution(frozen_dataset(name),source,quantity=D(100),cost_model=COSTS,as_of=AS_OF)
    assert all(fill.quantity==100 for fill in execution.fills)
    with pytest.raises(ValueError,match="Insufficient"):
        account_simulation(frozen_dataset(name),source,execution,initial_capital=CAPITAL,as_of=AS_OF)


def test_short_collateral_convention_is_explicit_and_never_spendable_proceeds():
    result=case("open_short");row=result.equity_curve[-1]
    assert result.accounting_configuration.short_collateral=="own_entry_reference_notional_reserved_proceeds_restricted"
    assert result.accounting_configuration.short_exposure_limit=="one_times_equity_at_entry_reference_and_completed_marks"
    assert row.reserved_capital==223 and row.restricted_short_proceeds==D("222.554")
    assert row.cash==D("9776.777446")  # 10000 - own collateral 223 - entry fee .222554.


def test_complete_pipeline_twice_reproducible_not_a_cached_result_comparison():
    first=pipeline("long_loss",fresh=True)
    with localcontext() as context:
        context.prec=8
        second=pipeline("long_loss",fresh=True,costs=ResearchCostModel(D(".0010"),D(".0020"),D(".0010")),quantity=D("1.00"),capital=D("10000.00"))
    assert first==second
    assert [r.decision for r in first.replay.records]==[r.decision for r in second.replay.records]
    assert first.execution.fills==second.execution.fills
    assert first.completed_trades==second.completed_trades
    assert first.equity_curve==second.equity_curve and first.performance==second.performance
    assert first.result_identity==second.result_identity


@pytest.mark.parametrize("changed",["cost","delay","quantity","capital"])
def test_meaningful_research_configuration_changes_appropriate_identity(changed):
    first=case("long_loss")
    args={"costs":replace(COSTS,trading_fee_rate=D(".002"))} if changed=="cost" else (
        {"costs":replace(COSTS,execution_delay=timedelta(hours=1))} if changed=="delay" else
        {"quantity":D(2)} if changed=="quantity" else {"capital":D(20000)})
    second=pipeline("long_loss",**args)
    assert first.replay==second.replay  # Costs/sizing/capital do not leak into strategy decisions.
    assert first.result_identity!=second.result_identity
    if changed!="capital":
        assert first.execution.simulation_identity!=second.execution.simulation_identity
        assert first.execution.fills[0].fill_identity!=second.execution.fills[0].fill_identity
    else:
        assert first.execution==second.execution
        assert first.equity_curve[0].observation_identity!=second.equity_curve[0].observation_identity


def test_composed_pipeline_has_no_provider_or_database_access(monkeypatch):
    import socket
    import httpx
    from sqlalchemy.engine import Engine
    def forbidden(*args,**kwargs):raise AssertionError("Frozen research pipeline must not access network or database")
    monkeypatch.setattr(socket,"create_connection",forbidden)
    monkeypatch.setattr(httpx.Client,"send",forbidden)
    monkeypatch.setattr(httpx.AsyncClient,"send",forbidden)
    monkeypatch.setattr(Engine,"connect",forbidden)
    assert pipeline("short_loss",fresh=True)==case("short_loss")


def test_delayed_real_strategy_exit_stays_unfilled_without_erasing_actual_position():
    result=pipeline("long_loss",costs=replace(COSTS,execution_delay=timedelta(hours=1)))
    assert result.replay.final_state is PositionState.CASH
    assert result.execution.final_filled_state is PositionState.LONG
    assert result.open_position.direction is PositionState.LONG
    assert len(result.execution.fills)==len(result.execution.unfilled_decisions)==1
    assert result.execution.fills[0].eligible_fill_timestamp==START+timedelta(hours=132)
    assert result.execution.unfilled_decisions[0].eligible_fill_timestamp==START+timedelta(hours=145)
    assert result.equity_curve[3].position_state is PositionState.CASH  # Candle 131: entry still pending.
    assert result.equity_curve[4].position_state is PositionState.LONG  # Candle 132: entry filled.
    assert not result.completed_trades
    assert result.open_position.entry_price==D("178.356")
    assert result.equity_curve[-1].realized_capital==D("9999.821644")
    assert result.equity_curve[-1].unrealized_pnl==D("-5.356")
    assert result.equity_curve[-1].total_equity==D("9994.465644")
    assert abs(F(result.performance.exposure_percentage)-F(1300,17))<F(1,10**47)


def test_nominally_fundable_short_still_cannot_violate_unleveraged_equity_limit():
    # Required cash 223 + .222554 fits 223.3, but adverse entry costs leave
    # equity 222.631446 below the 223 reference exposure. No implicit leverage.
    with pytest.raises(ValueError,match="one-times"):
        pipeline("open_short",capital=D("223.3"))
