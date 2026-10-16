from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP, localcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.market_intelligence.engine import calculate_features
from app.market_intelligence.numeric import calculation_context
from app.market_intelligence.primitives import atr, ema, macd, realized_volatility, returns, rsi, true_ranges
from app.market_intelligence.service import DatasetInstrumentNotFound, calculate_stored_features
from app.market_intelligence.types import Candle, DatasetContext, InvalidCandleData, TIMEFRAME_SECONDS

D = Decimal
ID = UUID("5c11d166-0000-4000-8000-000000000001")
CONTEXT = DatasetContext(ID, "BTC/USDT", "BINANCE", "1h")
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
LN2 = D("0.69314718055994530941723212145817656807550013436026")


def prices(count): return [D(index) for index in range(1, count + 1)]


def candles(values, timeframe="1h"):
    duration = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    return [Candle(ID,"BINANCE",timeframe,START + index * duration,
        START + (index + 1) * duration - timedelta(milliseconds=1),
        value,value * 2,value / 2,value,D("1"),START + (index + 1) * duration)
        for index,value in enumerate(values)]


def close_enough(actual, expected, tolerance=D("1e-46")):
    assert actual is not None
    with localcontext() as ctx:
        ctx.prec = 80
        assert abs(actual - expected) <= tolerance, (actual, expected)


def rational(numerator, denominator):
    with localcontext() as ctx:
        ctx.prec=80
        return D(numerator) / D(denominator)


def test_ema_known_sma_seed_and_recursion():
    assert ema(prices(5),3)==[None,None,D(2),D(3),D(4)]
    assert ema([D(1),D(1),D(4),D(7)],3)==[None,None,D(2),D("4.5")]
    assert ema(prices(19),20)==[None]*19
    assert ema([],20)==[]


def test_ema20_and50_linear_sequences_independent_expected():
    result=calculate_features(CONTEXT,candles(prices(80)),as_of=AS_OF)
    assert result[18].ema_20 is None and result[19].ema_20==D("10.5")
    assert result[48].ema_50 is None and result[49].ema_50==D("25.5")
    close_enough(result[-1].ema_20,D("70.5"))
    close_enough(result[-1].ema_50,D("55.5"))


def test_wilder_rsi_known_initial_and_next_values():
    closes=list(map(D,["44.34","44.09","44.15","43.61","44.33","44.83","45.10","45.42","45.84","46.08","45.89","46.03","45.61","46.28","46.28","46.00"]))
    result=rsi(closes,14)
    assert result[:14]==[None]*14
    # Hand-summed initial gains 3.34/losses 1.40; next Wilder weights give 43.42/22.12.
    close_enough(result[14],rational(16700,237))
    close_enough(result[15],rational(217100,3277))


@pytest.mark.parametrize("closes,expected", [([D(10)]*30,D(50)),(prices(30),D(100)),(list(reversed(prices(30))),D(0))])
def test_rsi_flat_rising_and_falling(closes,expected):
    result=rsi(closes)
    assert result[:14]==[None]*14 and all(value==expected for value in result[14:])


def test_true_range_includes_previous_close_for_price_gaps():
    assert true_ranges(list(map(D,[10,14,9])),list(map(D,[8,12,7])),list(map(D,[9,13,8])))==list(map(D,[2,5,6]))


def test_atr_seed_and_wilder_recursion_hand_calculated():
    highs=list(map(D,[10,12,13,15]));lows=list(map(D,[8,9,10,11]));closes=list(map(D,[9,11,12,14]))
    result=atr(highs,lows,closes,3)
    assert result[:2]==[None,None]
    close_enough(result[2],rational(8,3))
    close_enough(result[3],rational(28,9))


def test_atr14_first_fourteen_ranges_include_first_high_low():
    assert atr([D(11)]*15,[D(9)]*15,[D(10)]*15)==[None]*13+[D(2),D(2)]
    assert atr([D(10)]*15,[D(10)]*15,[D(10)]*15)==[None]*13+[D(0),D(0)]


def test_macd_independent_linear_sequence_warmups():
    line,signal,histogram=macd(prices(80))
    assert line[:25]==[None]*25 and signal[:33]==[None]*33 and histogram[:33]==[None]*33
    # A unit linear ramp has SMA-seeded EMA12=C-5.5 and EMA26=C-12.5: difference 7.
    for index in range(25,80): close_enough(line[index],D(7))
    for index in range(33,80):
        close_enough(signal[index],D(7));close_enough(histogram[index],D(0))


def test_macd_all_warmup_or_empty():
    assert macd(prices(10))==([None]*10,[None]*10,[None]*10)
    assert macd([])==([],[],[])


def test_simple_and_log_returns_known_values():
    simple,logs=returns(list(map(D,[2,4,2])))
    assert simple==[None,D(1),D("-0.5")]
    assert logs[0] is None
    close_enough(logs[1],LN2);close_enough(logs[2],LN2.copy_negate())
    assert returns([])==([],[])


def test_realized_volatility_population_unannualized_known_window():
    result=realized_volatility(list(map(D,[1,2,2,1])),3)
    assert result[:3]==[None]*3
    # Log returns [ln2,0,-ln2], population variance 2*(ln2)^2/3.
    with localcontext() as ctx:
        ctx.prec=80
        expected=LN2*(D(2)/D(3)).sqrt()
    close_enough(result[3],expected)


def test_volatility20_warmup_constant_geometric_returns():
    result=realized_volatility([D(2)**index for index in range(25)])
    assert result[:20]==[None]*20 and result[20:]==[D(0)]*5
    assert realized_volatility([D(1)]*10)==[None]*10


def test_engine_warmups_and_frozen_original_close():
    source=candles([D("82000.12345678")]*2)
    result=calculate_features(CONTEXT,source,as_of=AS_OF)
    assert result[0].close is source[0].close
    for field in ("ema_20","ema_50","rsi_14","atr_14","macd","macd_signal","macd_histogram","simple_return","log_return","realized_volatility"):
        assert getattr(result[0],field) is None
    assert result[1].simple_return==D(0) and result[1].log_return==D(0)
    with pytest.raises(FrozenInstanceError): result[0].close=D(1)
    assert calculate_features(CONTEXT,[],as_of=AS_OF)==[]


def test_engine_all_warmup_first_indices():
    result=calculate_features(CONTEXT,candles(prices(60)),as_of=AS_OF)
    first={field:next(i for i,item in enumerate(result) if getattr(item,field) is not None) for field in (
        "ema_20","ema_50","rsi_14","atr_14","macd","macd_signal","macd_histogram","simple_return","log_return","realized_volatility")}
    assert first==dict(ema_20=19,ema_50=49,rsi_14=14,atr_14=13,macd=25,macd_signal=33,macd_histogram=33,
                      simple_return=1,log_return=1,realized_volatility=20)


def test_no_lookahead_prefix_and_changed_future_tail():
    source=candles(prices(100));full=calculate_features(CONTEXT,source,as_of=AS_OF)
    for count in (1,14,20,26,34,50,75):
        assert calculate_features(CONTEXT,source[:count],as_of=AS_OF)==full[:count]
    altered=source[:60]+[replace(c,open=c.open*10,high=c.high*10,low=c.low*10,close=c.close*10) for c in source[60:]]
    assert calculate_features(CONTEXT,altered,as_of=AS_OF)[:60]==full[:60]


def test_deterministic_context_and_input_not_mutated():
    source=candles(prices(70));before=[asdict(c) for c in source]
    expected=calculate_features(CONTEXT,source,as_of=AS_OF)
    with localcontext() as ctx:
        ctx.prec=8;ctx.rounding=ROUND_UP
        assert calculate_features(CONTEXT,source,as_of=AS_OF)==expected
        assert rsi(prices(30))==[None]*14+[D(100)]*16
    assert calculate_features(CONTEXT,source,as_of=AS_OF)==expected
    assert [asdict(c) for c in source]==before


@pytest.mark.parametrize("scale",[D("1000.12345678"),D("0.0000000000000000000000000000000000000001")])
def test_btc_like_and_very_small_decimal_prices(scale):
    source=candles([scale*D(index) for index in range(1,61)])
    result=calculate_features(CONTEXT,source,as_of=AS_OF)
    assert result[-1].close==source[-1].close and result[-1].rsi_14==D(100)
    close_enough(result[-1].ema_20,scale*D("50.5"),tolerance=scale*D("1e-44"))
    assert all(value is None or isinstance(value,D) and value.is_finite()
               for row in result for field,value in asdict(row).items() if field not in ("canonical_symbol","provider","timeframe","open_time","close_time"))


@pytest.mark.parametrize("timeframe",TIMEFRAME_SECONDS)
def test_supported_timeframes_and_utc_output(timeframe):
    context=replace(CONTEXT,timeframe=timeframe)
    offset=timezone(timedelta(hours=1))
    source=[replace(c,open_time=c.open_time.astimezone(offset),close_time=c.close_time.astimezone(offset),retrieved_at=c.retrieved_at.astimezone(offset))
            for c in candles(prices(60),timeframe)]
    result=calculate_features(context,source,as_of=AS_OF)
    assert result[0].open_time==START and result[0].open_time.tzinfo==timezone.utc
    assert result[-1].timeframe==timeframe


@pytest.mark.parametrize("kind,code",[("gap","internal_gap"),("reverse","non_chronological"),("duplicate","duplicate_timestamp"),
    ("provider","mixed_context"),("instrument","mixed_context"),("timeframe","mixed_context")])
def test_invalid_dataset_order_and_context(kind,code):
    source=candles(prices(5))
    if kind=="gap":source.pop(2)
    if kind=="reverse":source.reverse()
    if kind=="duplicate":source.insert(1,source[0])
    if kind=="provider":source[1]=replace(source[1],provider="QUIDAX")
    if kind=="instrument":source[1]=replace(source[1],instrument_id=UUID(int=2))
    if kind=="timeframe":source[1]=replace(source[1],timeframe="4h")
    with pytest.raises(InvalidCandleData) as error:calculate_features(CONTEXT,source,as_of=AS_OF)
    assert error.value.code==code


@pytest.mark.parametrize("patch",[{"close":1.1},{"volume":-D(1)},{"high":D("NaN")},{"close":D(0)},
    {"high":D("0.1")},{"low":D(100)},{"open_time":START.replace(tzinfo=None)},
    {"open_time":START+timedelta(seconds=1)},{"close_time":START+timedelta(hours=2)},
    {"retrieved_at":START},{"close_time":START}])
def test_malformed_candles_rejected(patch):
    source=candles(prices(5));source[0]=replace(source[0],**patch)
    with pytest.raises(InvalidCandleData):calculate_features(CONTEXT,source,as_of=AS_OF)


def test_incomplete_candle_even_in_last_millisecond_rejected():
    source=candles(prices(1))
    with pytest.raises(InvalidCandleData) as error:calculate_features(CONTEXT,source,as_of=source[0].close_time)
    assert error.value.code=="incomplete_candle"
    assert len(calculate_features(CONTEXT,source,as_of=START+timedelta(hours=1)))==1


def test_invalid_as_of_and_unsupported_timeframe():
    with pytest.raises(InvalidCandleData):calculate_features(CONTEXT,[],as_of=START.replace(tzinfo=None))
    with pytest.raises(InvalidCandleData):calculate_features(replace(CONTEXT,timeframe="5m"),[],as_of=AS_OF)
    with pytest.raises(InvalidCandleData):calculate_features(CONTEXT,[None],as_of=AS_OF)


@pytest.mark.parametrize("function,args",[(ema,([1.1],2)),(rsi,([D(0)],14)),(returns,([D(-1)],)),
    (realized_volatility,([D(1)],1)),(ema,([D(1)],True)),(atr,([D(2)],[D(3)],[D(1)],14))])
def test_invalid_primitive_inputs(function,args):
    with pytest.raises(ValueError):function(*args)


def test_read_only_store_service_filters_context_and_orders_without_writes():
    source=candles(prices(60));db=Mock()
    db.scalar.return_value=SimpleNamespace(id=ID,canonical_symbol="BTC/USDT")
    db.scalars.return_value.all.return_value=source
    result=calculate_stored_features(db,"BTC/USDT","BINANCE","1h",as_of=AS_OF)
    assert result==calculate_features(CONTEXT,source,as_of=AS_OF)
    query=db.scalars.call_args.args[0];sql=str(query)
    assert "market_candles.instrument_id" in sql and "market_candles.provider" in sql and "market_candles.timeframe" in sql
    assert "ORDER BY market_candles.open_time" in sql and "LIMIT" not in sql
    assert "close_time <=" not in sql  # Never hide incomplete or gapped input by filtering.
    db.add.assert_not_called();db.execute.assert_not_called();db.commit.assert_not_called();db.flush.assert_not_called()


def test_store_unknown_instrument_and_gap_fail_closed():
    db=Mock();db.scalar.return_value=None
    with pytest.raises(DatasetInstrumentNotFound):calculate_stored_features(db,"UNKNOWN/USDT","BINANCE","1h",as_of=AS_OF)
    db.scalars.assert_not_called()
    db.scalar.return_value=SimpleNamespace(id=ID,canonical_symbol="BTC/USDT")
    source=candles(prices(10));source.pop(3);db.scalars.return_value.all.return_value=source
    with pytest.raises(InvalidCandleData,match="internal_gap"):
        calculate_stored_features(db,"BTC/USDT","BINANCE","1h",as_of=AS_OF)
    db.commit.assert_not_called()
