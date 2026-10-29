"""Bounded maintenance with isolated sessions/provider; never a real database."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from httpx import ReadTimeout
from sqlalchemy.exc import SQLAlchemyError
from app.mandate_runner import market as m
from app.market_data.historical_mappings import HistoricalTarget, HistoricalMappingUnavailable
from app.market_data.provider import ProviderUnavailable
from app.market_data.candle_store import CandleIntegrityConflict
from app.market_intelligence.types import InvalidCandleData

AT=datetime(2026,10,26,12,1,tzinfo=timezone.utc)
ID=uuid4()
TARGET=HistoricalTarget("BTC/USDT","BINANCE","BTCUSDT")

def history():
    latest=m.expected_candle(AT)[0]
    values=[]
    for i in reversed(range(m.HISTORY)):
        start=latest-i*m.HOUR
        values.append(SimpleNamespace(id=uuid4(),instrument_id=ID,provider="BINANCE",timeframe="1h",
            open_time=start,close_time=start+m.HOUR-m.MILLISECOND,retrieved_at=AT-timedelta(seconds=1),
            open=Decimal("100.12345678901234567890123456789"),high=Decimal("102"),low=Decimal("99"),
            close=Decimal("101"),volume=Decimal("1.234567890123456789")))
    return values

@pytest.fixture
def harness(monkeypatch):
    state=SimpleNamespace(rows=history(),network_active=False,commits=0,rollbacks=0,fail_commit=False)
    @contextmanager
    def begin():
        before=list(state.rows)
        try:
            yield
            if state.fail_commit:raise SQLAlchemyError("private connection details")
            state.commits+=1
        except Exception:
            state.rows=before;state.rollbacks+=1;raise
    @contextmanager
    def session():
        yield SimpleNamespace(begin=begin)
    @contextmanager
    def provider_factory():
        state.network_active=True
        try:yield state.provider
        finally:state.network_active=False
    state.provider=Mock()
    state.session=session;state.provider_factory=provider_factory
    state.read=Mock(side_effect=lambda db,at:(TARGET,ID,list(state.rows)))
    monkeypatch.setattr(m,"read_window",state.read)
    state.fetch=Mock(return_value=[])
    monkeypatch.setattr(m,"fetch_historical_candles",state.fetch)
    def persist(db,i,t,tf,batch,limit):
        state.rows=history()
        return SimpleNamespace(fetched=limit,inserted=1,unchanged=limit-1)
    state.persist=Mock(side_effect=persist)
    monkeypatch.setattr(m,"persist_candles",state.persist)
    state.run=lambda clock=lambda:AT:m.maintain_market(session,clock=clock,provider_factory=provider_factory)
    return state

@pytest.mark.parametrize("minute,second",[(0,0),(1,0),(59,59)])
def test_expected_completed_identity(minute,second):
    at=AT.replace(minute=minute,second=second)
    start,end,expiry=m.expected_candle(at)
    assert start==AT.replace(hour=11,minute=0,second=0)
    assert end==AT.replace(minute=0,second=0)-m.MILLISECOND
    assert expiry==AT.replace(minute=10,second=0)

def test_timezone_normalized():
    assert m.expected_candle(AT)==m.expected_candle(AT.astimezone(timezone(timedelta(hours=1))))

@pytest.mark.parametrize("offset",[0,1,50,99])
def test_gap_fetches_only_required_suffix(offset):
    rows=history();del rows[-1-offset]
    assert m.fetch_limit(rows,AT)==offset+1

def test_empty_bootstrap_bounded():assert m.fetch_limit([],AT)==m.HISTORY==100

def test_complete_skips_network(harness):
    result=harness.run()
    assert result.safe_for_evaluation and result.status=="READY" and result.ingestion=="NOT_ATTEMPTED"
    assert result.candle_id==harness.rows[-1].id
    harness.fetch.assert_not_called();harness.persist.assert_not_called()

@pytest.mark.parametrize("offset",[0,5,99])
def test_recent_gap_repaired_with_existing_store(harness,offset):
    del harness.rows[-1-offset]
    result=harness.run()
    assert result.status=="READY" and result.safe_for_evaluation
    assert result.ingestion=="PERSISTED" and result.inserted==1
    assert harness.fetch.call_args.args[1:3]==("1h",offset+1)
    assert harness.persist.call_args.args[1:4]==(ID,TARGET,"1h")

def test_empty_uses_only_current_window(harness):
    harness.rows=[]
    assert harness.run().status=="READY"
    assert harness.fetch.call_args.args[2]==100

def test_default_uses_existing_binance_factory(harness,monkeypatch):
    harness.rows=[]
    def existing():
        yield harness.provider
    monkeypatch.setattr(m,"get_historical_provider",existing)
    assert m.maintain_market(harness.session,clock=lambda:AT).status=="READY"

def test_network_outside_transaction(harness):
    harness.rows=[]
    def fetch(*args):
        assert harness.commits==1 and harness.network_active
        return []
    harness.fetch.side_effect=fetch
    assert harness.run().safe_for_evaluation

def test_provider_failure_explicit_and_sanitized(harness):
    harness.rows=[];harness.fetch.side_effect=ProviderUnavailable("secret host")
    r=harness.run()
    assert r.status=="PROVIDER_FAILED" and not r.safe_for_evaluation and "secret" not in repr(r)
    harness.persist.assert_not_called()

@pytest.mark.parametrize("error,status",[(CandleIntegrityConflict(AT),"INTEGRITY_CONFLICT"),
    (ProviderUnavailable(),"PROVIDER_FAILED"),(SQLAlchemyError("secret"),"STORAGE_FAILED")])
def test_store_failure_rolls_back(harness,error,status):
    harness.rows=[]
    def failure(*args):
        harness.rows=history();raise error
    harness.persist.side_effect=failure
    r=harness.run()
    assert r.status==status and not r.safe_for_evaluation and harness.rows==[] and harness.rollbacks==1
    assert r.inserted==0 and "secret" not in repr(r)

def test_commit_failure_never_returns_ready(harness):
    # Fail only the writing transaction commit.
    harness.rows=[]
    original=harness.persist.side_effect
    def persist(*args):
        result=original(*args);harness.fail_commit=True;return result
    harness.persist.side_effect=persist
    r=harness.run()
    assert r.status=="STORAGE_FAILED" and not r.safe_for_evaluation and harness.rows==[]

def test_candle_rollover_does_not_silently_substitute(harness):
    harness.rows=[];times=iter([AT,AT+timedelta(hours=1)])
    r=harness.run(clock=lambda:next(times))
    assert r.reason_code=="CANDLE_BOUNDARY_CHANGED" and not r.safe_for_evaluation
    harness.persist.assert_not_called()

def test_clock_backwards_blocks(harness):
    times=iter([AT,AT-timedelta(seconds=1)])
    assert harness.run(clock=lambda:next(times)).reason_code=="CLOCK_MOVED_BACKWARD"

def test_freshness_is_market_close_not_retrieval(harness):
    r=harness.run(clock=lambda:AT.replace(minute=10))
    assert r.freshness=="STALE" and not r.safe_for_evaluation
    assert r.fresh_until==AT.replace(minute=10)

@pytest.mark.parametrize("change",["float","future","gap","incomplete"])
def test_invalid_evidence_fails_closed(change):
    rows=history()
    if change=="float":rows[-1].close=101.0
    if change=="future":rows[-1].retrieved_at=AT+timedelta(seconds=1)
    if change=="gap":del rows[5]
    if change=="incomplete":rows[-1].close_time=AT+timedelta(hours=1)
    with pytest.raises(InvalidCandleData):m.assess(rows,ID,AT)

def test_missing_latest_not_safe():
    latest,missing,fresh,safe=m.assess(history()[:-1],ID,AT)
    assert latest is None and missing==1 and fresh=="STALE" and not safe

def test_decimal_is_preserved():
    rows=history();before=rows[0].open
    assert m.assess(rows,ID,AT)[3]
    assert rows[0].open==before and isinstance(rows[0].open,Decimal)

def test_missing_mapping_explicit(harness):
    harness.read.side_effect=HistoricalMappingUnavailable()
    assert harness.run().status=="MARKET_UNAVAILABLE"
    harness.fetch.assert_not_called()

def test_storage_read_failure_explicit(harness):
    harness.read.side_effect=SQLAlchemyError("secret")
    r=harness.run();assert r.status=="STORAGE_FAILED" and "secret" not in repr(r)

def test_unsafe_postwrite_validation_rolls_back(harness):
    harness.rows=[]
    def persist(*args):
        harness.rows=history();harness.rows[-1].close=1.0
        return SimpleNamespace(fetched=100,inserted=100,unchanged=0)
    harness.persist.side_effect=persist
    r=harness.run()
    assert r.status=="EVIDENCE_UNSAFE" and harness.rows==[] and r.ingestion=="ROLLED_BACK"

def test_window_query_has_explicit_bounds(monkeypatch):
    instrument=SimpleNamespace(id=ID,market_type="SPOT")
    monkeypatch.setattr(m,"resolve_historical_target",lambda *args:TARGET)
    monkeypatch.setattr(m,"find_instrument",lambda *args:instrument)
    db=Mock();db.scalars.return_value.all.return_value=[]
    assert m.read_window(db,AT)==(TARGET,ID,[])
    params=db.scalars.call_args.args[0].compile().params
    assert params["provider_1"]=="BINANCE" and params["timeframe_1"]=="1h"
    assert params["open_time_2"]==m.expected_candle(AT)[0] and params["param_1"]==100

def test_no_trading_or_scheduler_dependencies():
    source=Path(m.__file__).read_text()
    for token in ("mandate_paper", "evaluate_strategy(", "evaluate_and_register", "execute_paper", "sleep(","while True", "app.execution"):
        assert token not in source


def test_network_timeout_explicit(harness):
    harness.rows=[];harness.fetch.side_effect=ReadTimeout("private hostname")
    r=harness.run()
    assert r.status=="PROVIDER_FAILED" and r.ingestion=="FAILED" and not r.safe_for_evaluation
    assert "hostname" not in repr(r)
    harness.persist.assert_not_called()
