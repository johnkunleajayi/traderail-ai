"""Real runner + Paper/Strategy/Risk/accounting over the isolated SQL ledger.
No physical PostgreSQL locking/trigger/concurrency claims.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from sqlalchemy import select
from sqlalchemy.sql.dml import Insert,Update
import pytest
from app.mandate_runner import cycle
from app.mandate_runner.market import MaintenanceResult, expected_candle
from app.mandate_runner.models import work
from app.market_data.models import MarketCandle
from app.market_data.completed import ExpectedCandle, ExpectedCandleMismatch
from app.mandates.models import TradingMandate
from app.instruments.models import Instrument
from app.mandate_paper import service as paper
from app.mandate_paper import positions as management
from paper_ledger_double import Ledger, Result, matches
from test_mandate_paper import seeded, Session, MID, USER, economic_counts, api
from test_mandate_paper_positions import opened, advance
from test_integrated_9c_audit import frozen_dataset
from test_paper_production_lifecycle import add_candles

class RunnerLedger(Ledger):
    def execute(self,statement):
        is_work=isinstance(statement,(Insert,Update)) and statement.table.name==work.name
        result=super().execute(statement)
        if is_work and isinstance(statement,Insert):
            for row in self.rows[work.name]:
                for column in work.c:
                    if column.name not in row:
                        row[column.name]=0 if column.name=='attempts' else 'PENDING' if column.name=='state' else None
        if is_work and isinstance(statement,Update) and statement._returning:
            found=[r for r in self.rows[work.name] if all(matches(c,r) for c in statement._where_criteria)]
            return Result(deepcopy(found))
        return result

class RunnerSession(Session):
    def begin(self):return self.db.begin()
    def begin_nested(self):return self.db.begin_nested()
    def scalar(self,query):
        if query.column_descriptions[0]['entity'] is MarketCandle:
            row=self.db.execute(query).mappings().one_or_none()
            return SimpleNamespace(**row) if row else None
        return super().scalar(query)

@pytest.fixture
def pipeline(monkeypatch):
    def create(kind='long_profit'):
        seeded_db,at=seeded(kind);db=RunnerLedger();db.rows.update(deepcopy(seeded_db.rows))
        a=SimpleNamespace(db=db,now=at)
        @contextmanager
        def session():yield RunnerSession(db)
        a.session=session
        def maintenance(*args,**kw):
            latest=max(db.rows['market_candles'],key=lambda row:row['open_time'])
            start,end,until=expected_candle(a.now)
            return MaintenanceResult('READY','COMPLETED_WINDOW_READY',a.now,start,end,until,
                instrument_id=latest['instrument_id'],candle_id=latest['id'],freshness='FRESH',safe_for_evaluation=True)
        monkeypatch.setattr(cycle,'maintain_market',maintenance)
        a.run=lambda:cycle.process_one(session,MID,clock=lambda:a.now)
        return a
    return create

@pytest.mark.parametrize('kind',['no_trades','short_profit'])
def test_real_no_trade_stops_before_economics_and_deduplicates(pipeline,kind):
    a=pipeline(kind);first=a.run()
    assert first.state=='COMPLETED' and first.outcome_code=='NO_TRADE',first
    assert all(n==0 for n in economic_counts(a.db).values())
    before=deepcopy(a.db.rows)
    assert a.run().reused and a.db.rows==before

def test_real_entry_hold_exit_and_terminal_recovery(pipeline):
    a=pipeline();entry=a.run()
    assert entry.state=='COMPLETED' and entry.outcome_code=='PAPER_EXECUTED',entry
    assert all(n==1 for n in economic_counts(a.db).values())
    assert entry.registry_identity and entry.request_identity and entry.position_id
    before=deepcopy(a.db.rows);assert a.run().reused and a.db.rows==before
    ds=frozen_dataset('long_profit')
    add_candles(a.db,ds.candles[:132]);a.now=ds.candles[131].close_time+timedelta(minutes=1)
    held=a.run()
    assert held.state=='COMPLETED' and held.outcome_code=='POSITION_HELD',held
    assert all(n==1 for n in economic_counts(a.db).values())
    add_candles(a.db,ds.candles);a.now=ds.candles[-1].close_time+timedelta(minutes=1)
    closed=a.run()
    assert closed.state=='COMPLETED' and closed.outcome_code=='POSITION_CLOSED',closed
    assert a.db.rows['paper_positions'][0]['status']=='CLOSED'
    assert all(n==2 for t,n in economic_counts(a.db).items() if t!='paper_positions')
    assert closed.registry_identity!=entry.registry_identity and closed.request_identity!=entry.request_identity
    before=deepcopy(a.db.rows);a.now+=timedelta(days=2)
    recovered=a.run()
    assert recovered.reused and recovered.reason_code=='LIFECYCLE_ALREADY_CLOSED'
    assert a.db.rows==before

def test_real_accounting_failure_rolls_back_economics_and_records_work(pipeline):
    a=pipeline();a.db.fail_table='paper_accounting_entries'
    result=a.run()
    assert result.state in ('RETRY_WAIT','SKIPPED'),result
    assert all(n==0 for n in economic_counts(a.db).values())
    assert not a.db.rows['paper_authorizations']
    assert a.db.rows['paper_mandate_work'][0]['state']==result.state

def expectation(a):
    row=max(a.db.rows['market_candles'],key=lambda r:r['open_time'])
    return ExpectedCandle(row['instrument_id'],row['open_time'],row['close_time'])

def test_real_entry_expected_candle_mismatch_prevents_execution(api,monkeypatch):
    a=api();right=expectation(a)
    wrong=ExpectedCandle(right.instrument_id,right.open_time-timedelta(hours=1),right.close_time-timedelta(hours=1))
    execute=Mock(side_effect=AssertionError('Must not execute mismatched candle'))
    monkeypatch.setattr(paper,'execute_entry',execute)
    with pytest.raises(ExpectedCandleMismatch):
        paper.run(a.session,USER,MID,evaluated_at=a.now[0],clock=lambda:a.executed[0],expected_candle=wrong)
    a.session.rollback()
    assert all(n==0 for n in economic_counts(a.db).values())
    execute.assert_not_called()

@pytest.mark.parametrize('exit_now',[False,True])
def test_real_position_observation_requires_exact_candle(api,monkeypatch,exit_now):
    a,_=opened(api)
    if exit_now:advance(a)
    right=expectation(a)
    wrong=ExpectedCandle(right.instrument_id,right.open_time-timedelta(hours=1),right.close_time-timedelta(hours=1))
    before=deepcopy(a.db.rows)
    with pytest.raises(ExpectedCandleMismatch):
        paper.run(a.session,USER,MID,evaluated_at=a.now[0],clock=lambda:a.executed[0],expected_candle=wrong)
    a.session.rollback();assert a.db.rows==before


def test_real_entry_risk_rejection_is_completed_without_authorization(pipeline):
    a=pipeline();a.db.rows['trading_mandates'][0]['capital_allocation']=Decimal('10000')
    result=a.run()
    assert result.state=='COMPLETED' and result.outcome_code=='RISK_REJECTED',result
    assert not a.db.rows['paper_authorizations']
    assert all(n==0 for n in economic_counts(a.db).values())

def test_real_exit_active_system_switch_blocks_without_close(pipeline):
    a=pipeline();assert a.run().outcome_code=='PAPER_EXECUTED'
    ds=frozen_dataset('long_profit');add_candles(a.db,ds.candles)
    a.now=ds.candles[-1].close_time+timedelta(minutes=1)
    for row in a.db.rows['paper_kill_switch_states']:
        if row['scope']=='SYSTEM':row['active']=True
    result=a.run()
    assert result.state=='COMPLETED' and result.outcome_code=='EXIT_RISK_REJECTED',result
    assert all(n==1 for n in economic_counts(a.db).values())
    assert a.db.rows['paper_positions'][0]['status']=='OPEN'
