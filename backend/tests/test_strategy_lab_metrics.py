"""Hand-verifiable research accounting examples; no database or provider fixtures."""
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, localcontext
import pytest
from app.strategy_lab.costs import CostBreakdown, ResearchCostModel
from app.strategy_lab.experiment import snapshot_configuration
from app.strategy_lab import metrics as m

D = Decimal


def trade(pnl, fees="0", spread="0", slippage="0"):
    return m.RealizedTrade(D(pnl), CostBreakdown(D(fees), D(spread), D(slippage)))


def test_explicit_cost_conventions_and_no_exchange_defaults():
    # On 1000 notional: 1 fee + half of 4 full spread + 3 slippage = 6.
    model=ResearchCostModel(D(".001"),D(".004"),D(".003"),timedelta(seconds=2,microseconds=1))
    costs=model.costs_for_notional(D("1000"))
    assert costs == CostBreakdown(D("1"),D("2"),D("3"))
    assert costs.total == D("6")
    assert model.costs_for_notional(D("0")) == CostBreakdown()
    assert model.execution_delay == timedelta(seconds=2,microseconds=1)
    assert model.configuration()["execution_delay_microseconds"] == 2000001
    assert model.configuration()["spread_convention"] == "full_bid_ask_spread_half_per_leg"
    with pytest.raises(TypeError): ResearchCostModel()
    with pytest.raises(FrozenInstanceError): model.spread_rate=D(".1")
    with pytest.raises(FrozenInstanceError): costs.fees=D("9")


@pytest.mark.parametrize("name",["trading_fee_rate","spread_rate","slippage_rate"])
@pytest.mark.parametrize("value",[D("-.001"),D("1.0001"),D("NaN"),D("Infinity"),.01,0,True])
def test_invalid_cost_rates(name,value):
    args={"trading_fee_rate":D("0"),"spread_rate":D("0"),"slippage_rate":D("0")}
    args[name]=value
    with pytest.raises(ValueError): ResearchCostModel(**args)


def test_cost_boundaries_and_delay_validation():
    assert ResearchCostModel(D("1"),D("1"),D("1")).costs_for_notional(D("2")) == CostBreakdown(D("2"),D("1"),D("2"))
    for delay in (-1,1.0,timedelta(microseconds=-1)):
        with pytest.raises(ValueError): ResearchCostModel(D("0"),D("0"),D("0"),delay)
    for value in (D("-1"),D("NaN"),1.0):
        with pytest.raises(ValueError): ResearchCostModel(D("0"),D("0"),D("0")).costs_for_notional(value)
    for field in ("fees","spread_cost","slippage_cost"):
        with pytest.raises(ValueError): CostBreakdown(**{field:D("-1")})


def test_cost_configuration_integrates_without_changing_part1():
    model=ResearchCostModel(D(".001"),D(".002"),D(".003"),timedelta(seconds=1))
    original=snapshot_configuration(model.configuration())
    changed=model.configuration();changed["trading_fee_rate"]=D(".004")
    assert original == snapshot_configuration(model.configuration())
    assert original != snapshot_configuration(changed)
    other=ResearchCostModel(D(".0010"),D(".0020"),D(".0030"),timedelta(seconds=1))
    assert original == snapshot_configuration(other.configuration())
    assert original != snapshot_configuration(ResearchCostModel(D(".001"),D(".002"),D(".003"),timedelta(seconds=2)).configuration())


def test_independent_returns_and_cost_aggregation():
    # Gross 100 - 40 + 0 = 60; costs 3 + 6 + 0 = 9; net = 51.
    trades=[trade("100","1","1","1"),trade("-40","2","2","2"),trade("0")]
    assert m.realized_pnl_aggregation(trades) == m.RealizedPnlTotals(D("60"),D("3"),D("3"),D("3"),D("51"))
    assert m.gross_return(D("1000"),trades) == D(".06")
    assert m.net_return(D("1000"),trades) == D(".051")
    assert m.total_return(D("1000"),D("1051")) == D(".051")
    assert m.total_return(D("1000"),D("900")) == D("-.1")
    assert m.total_return(D("1000"),D("0")) == D("-1")
    assert m.total_fees(trades) == m.total_spread_cost(trades) == m.total_slippage_cost(trades) == D("3")
    assert trades[0].net_pnl == D("97") and trades[1].net_pnl == D("-46")


def test_hand_verifiable_net_trade_statistics():
    trades=[trade("12","2"),trade("20"),trade("-5"),trade("0")]
    assert m.trade_count(trades)==4
    assert m.win_rate(trades)==D(".5")
    assert m.loss_rate(trades)==D(".25")
    assert m.profit_factor(trades)==D("6")
    assert m.average_win(trades)==D("15")
    assert m.average_loss(trades)==D("5")
    assert m.expectancy(trades)==D("6.25")
    # Costs can turn a gross win into a net loss, without profitability claims.
    outcome=[trade("1","2")]
    assert m.win_rate(outcome)==D("0") and m.loss_rate(outcome)==D("1")
    assert m.expectancy(outcome)==D("-1")


@pytest.mark.parametrize("pnl,pf,avg_win,avg_loss,win_rate,loss_rate",[
    (["10","20"],None,D("15"),None,D("1"),D("0")),
    (["-10","-20"],D("0"),None,D("15"),D("0"),D("1")),
    (["0","0"],None,None,None,D("0"),D("0")),
])
def test_profit_factor_and_no_wins_or_losses(pnl,pf,avg_win,avg_loss,win_rate,loss_rate):
    outcomes=[trade(value) for value in pnl]
    assert m.profit_factor(outcomes)==pf
    assert m.average_win(outcomes)==avg_win and m.average_loss(outcomes)==avg_loss
    assert m.win_rate(outcomes)==win_rate and m.loss_rate(outcomes)==loss_rate


def test_drawdown_from_running_peak_without_lookahead_or_hidden_baseline():
    values=[D("100"),D("120"),D("90"),D("108"),D("150"),D("120")]
    assert m.drawdown_series(values)==(D("0"),D("0"),D(".25"),D(".1"),D("0"),D(".2"))
    assert m.maximum_drawdown(values)==D(".25")
    assert m.drawdown_series(values[:3])==m.drawdown_series(values)[:3]
    assert m.drawdown_series([D("0"),D("0"),D("100"),D("0")])==(None,None,D("0"),D("1"))
    assert m.maximum_drawdown([D("0"),D("0")]) is None
    assert m.maximum_drawdown([D("100"),D("110")])==D("0")
    assert m.maximum_drawdown([D("100")])==D("0")


def test_duration_weighted_exposure_percentage_and_unsigned_turnover():
    assert m.exposure_percentage(timedelta(hours=3),timedelta(hours=12))==D("25")
    assert m.exposure_percentage(timedelta(0),timedelta(hours=12))==D("0")
    assert m.exposure_percentage(timedelta(hours=12),timedelta(hours=12))==D("100")
    assert m.exposure_percentage(timedelta(microseconds=1),timedelta(microseconds=4))==D("25")
    # Two 500 entry/exit legs are 1000 gross notional, not 500 or zero.
    assert m.turnover([D("500"),D("500")],D("1000"))==D("1")
    assert m.turnover([D("1500")],D("1000"))==D("1.5")
    with pytest.raises(ValueError): m.exposure_percentage(timedelta(hours=2),timedelta(hours=1))
    with pytest.raises(ValueError): m.exposure_percentage(timedelta(seconds=-1),timedelta(hours=1))
    with pytest.raises(ValueError): m.exposure_percentage(1,timedelta(hours=1))
    with pytest.raises(ValueError): m.turnover([D("-1")],D("1000"))


def test_empty_inputs_and_undefined_denominators():
    assert m.realized_pnl_aggregation([])==m.RealizedPnlTotals(*(D("0"),)*5)
    assert m.trade_count([])==0
    assert m.total_fees([])==m.total_spread_cost([])==m.total_slippage_cost([])==D("0")
    for function in (m.win_rate,m.loss_rate,m.profit_factor,m.average_win,m.average_loss,m.expectancy):
        assert function([]) is None
    assert m.drawdown_series([])==()
    assert m.maximum_drawdown([]) is None
    assert m.gross_return(D("100"),[])==m.net_return(D("100"),[])==D("0")
    assert m.turnover([],D("100"))==D("0")
    assert m.total_return(D("0"),D("10")) is None
    assert m.gross_return(D("0"),[trade("10")]) is None
    assert m.net_return(D("0"),[trade("10")]) is None
    assert m.turnover([],D("0")) is None
    assert m.exposure_percentage(timedelta(0),timedelta(0)) is None


@pytest.mark.parametrize("value",[D("-1"),D("NaN"),D("Infinity"),1.0,"100",True])
def test_invalid_equity_capital_and_notionals_are_rejected(value):
    for function in (m.drawdown_series,m.maximum_drawdown):
        with pytest.raises(ValueError): function([value])
    with pytest.raises(ValueError): m.total_return(value,D("100"))
    with pytest.raises(ValueError): m.total_return(D("100"),value)
    with pytest.raises(ValueError): m.gross_return(value,[])
    with pytest.raises(ValueError): m.net_return(value,[])
    with pytest.raises(ValueError): m.turnover([value],D("100"))


def test_malformed_trade_and_cost_observations_rejected():
    for value in (D("NaN"),D("Infinity"),1.0,True):
        with pytest.raises(ValueError): m.RealizedTrade(value)
        with pytest.raises(ValueError): CostBreakdown(value)
    with pytest.raises(ValueError): m.RealizedTrade(D("1"),None)
    with pytest.raises(ValueError): m.trade_count([D("1")])
    with pytest.raises(ValueError): m.expectancy([None])


def test_exact_aggregation_and_costs_preserve_long_decimals_and_cancellation():
    large=D("123456789012345678901234567890123456789012345678901234567890.123456789")
    tiny=D(".00000000000000000000000000000000000000000000000000000000001")
    outcomes=[m.RealizedTrade(large),m.RealizedTrade(large.copy_negate()),m.RealizedTrade(tiny)]
    assert m.realized_pnl_aggregation(outcomes).gross_pnl==tiny
    model=ResearchCostModel(D(".00000000000000000001"),D("0"),D("0"))
    assert model.costs_for_notional(large).fees==D("1234567890123456789012345678901234567890.12345678901234567890123456789")
    assert m.total_fees([trade("0",".1"),trade("0",".2")])==D(".3")


def test_determinism_and_decimal_context_independence():
    outcomes=[trade("1"),trade("0"),trade("0")]
    expected=D("0."+"3"*50)
    with localcontext() as context:
        context.prec=2;context.rounding=ROUND_DOWN
        first=(m.expectancy(outcomes),m.win_rate(outcomes),m.total_return(D("3"),D("4")))
        assert first==(expected,expected,expected)
        exact=m.realized_pnl_aggregation([trade("100.000001"),trade("-.000001")])
    with localcontext() as context:
        context.prec=80
        assert first==(m.expectancy(outcomes),m.win_rate(outcomes),m.total_return(D("3"),D("4")))
        assert exact.net_pnl==D("100")
    assert outcomes==[trade("1"),trade("0"),trade("0")]
    assert m.realized_pnl_aggregation(outcomes)==m.realized_pnl_aggregation(outcomes)
