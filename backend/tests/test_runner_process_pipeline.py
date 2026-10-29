"""Process discovery over real Step 4 + Strategy/Risk/Paper services.
Isolated SQL ledger only; no physical concurrent-connection or Neon proof.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID,uuid4
import pytest
from app.mandate_runner import process as process_module, cycle
from app.mandate_runner.process import PaperRunner, Candidate, RunnerStopping, due, recover_uncertain
from app.mandate_runner.config import RunnerSettings
from app.mandate_runner.repository import ensure_work
from app.mandate_runner.market import expected_candle
from app.mandate_runner.cycle import CycleResult
from test_runner_pipeline import pipeline, RunnerSession
from test_mandate_paper import MID,USER,economic_counts
from test_integrated_9c_audit import frozen_dataset
from test_paper_production_lifecycle import add_candles

class ProcessSession(RunnerSession):
    def execute(self,statement):return self.db.execute(statement)

def prepared(a):
    @contextmanager
    def session():yield ProcessSession(a.db)
    a.process_sessions=session;a.events=[]
    def runner(**kw):
        return PaperRunner(session,RunnerSettings(_env_file=None,paper_runner_enabled=True,
            database_url='postgresql+psycopg://test_guard:test_guard@127.0.0.1:1/blocked_test_database'),
            kw.pop('stop',Event()),lambda event,**fields:a.events.append((event,fields)),
            clock=lambda:a.now,**kw)
    a.runner=runner
    return a

def test_two_real_eligible_mandates_independent_work(pipeline):
    a=prepared(pipeline('no_trades'))
    second=deepcopy(a.db.rows['trading_mandates'][0]);second['id']=UUID(int=88)
    a.db.rows['trading_mandates'].append(second)
    assert a.runner().poll_once()==2
    rows=a.db.rows['paper_mandate_work']
    assert len(rows)==2 and {r['mandate_id'] for r in rows}=={MID,UUID(int=88)}
    assert all(r['outcome_code']=='NO_TRADE' and r['state']=='COMPLETED' for r in rows)
    assert all(n==0 for n in economic_counts(a.db).values())

def test_two_runner_stale_discovery_claims_cannot_duplicate_entry(pipeline,monkeypatch):
    a=prepared(pipeline())
    # Force both independent runners to act on an advisory stale due snapshot.
    # The real Step 4 repository/transaction remains authoritative.
    monkeypatch.setattr(process_module,'due',lambda *a:True)
    first=a.runner();second=a.runner()
    assert first.poll_once()==1 and second.poll_once()==1
    assert len(a.db.rows['paper_mandate_work'])==1
    assert all(n==1 for n in economic_counts(a.db).values())
    assert a.events[-1][1]['reused'] is True
    assert any('FOR UPDATE' in str(q) for q in a.db.statements if hasattr(q,'_for_update_arg') and q._for_update_arg is not None)

def test_restart_does_not_invoke_completed_no_trade(pipeline):
    a=prepared(pipeline('no_trades'));assert a.runner().poll_once()==1
    before=deepcopy(a.db.rows)
    forbidden=Mock(side_effect=AssertionError('Completed candle must not be retried'))
    assert a.runner(processor=forbidden).poll_once()==0
    forbidden.assert_not_called();assert a.db.rows==before

def test_real_hold_completed_candle_not_retried(pipeline):
    a=prepared(pipeline());assert a.runner().poll_once()==1
    ds=frozen_dataset('long_profit');add_candles(a.db,ds.candles[:132])
    a.now=ds.candles[131].close_time+timedelta(minutes=1)
    assert a.runner().poll_once()==1
    assert a.db.rows['paper_mandate_work'][-1]['outcome_code']=='POSITION_HELD'
    assert a.runner().poll_once()==0
    assert all(n==1 for n in economic_counts(a.db).values())

def test_paused_and_retired_not_discovered(pipeline):
    a=prepared(pipeline('no_trades'))
    for state in ('PAUSED','RETIRED'):
        a.db.rows['trading_mandates'][0]['status']=state
        assert a.runner().poll_once()==0
    assert not a.db.rows['paper_mandate_work']

def test_unsupported_risk_preference_not_processed(pipeline):
    a=prepared(pipeline('no_trades'));a.db.rows['trading_mandates'][0]['risk_level']='GROWTH'
    forbidden=Mock(side_effect=AssertionError('Not eligible'))
    assert a.runner(processor=forbidden).poll_once()==0
    forbidden.assert_not_called()

def test_failure_in_one_mandate_keeps_second_working(pipeline):
    a=prepared(pipeline('no_trades'))
    second=deepcopy(a.db.rows['trading_mandates'][0]);second['id']=UUID(int=88)
    a.db.rows['trading_mandates'].append(second)
    def processor(factory,mid,**kw):
        if mid==MID:raise RuntimeError('private metadata')
        return cycle.process_one(factory,mid,**kw)
    r=a.runner(processor=processor)
    assert r.poll_once()==1 and MID in r.suppressed
    assert a.db.rows['paper_mandate_work'][0]['mandate_id']==UUID(int=88)

def test_shutdown_during_cycle_rolls_back_then_restart_is_safe(pipeline,monkeypatch):
    a=prepared(pipeline());stop=Event();original=cycle.run_paper
    def stop_after_economics(*args,**kw):
        result=original(*args,**kw);stop.set();return result
    monkeypatch.setattr(cycle,'run_paper',stop_after_economics)
    with pytest.raises(RunnerStopping):a.runner(stop=stop).poll_once()
    assert all(n==0 for n in economic_counts(a.db).values())
    assert not a.db.rows['paper_mandate_work'] and not a.db.rows['paper_authorizations']
    monkeypatch.setattr(cycle,'run_paper',original)
    assert a.runner().poll_once()==1
    assert all(n==1 for n in economic_counts(a.db).values())

def test_completed_close_barrier_survives_process_restart(pipeline):
    a=prepared(pipeline());assert a.runner().poll_once()==1
    ds=frozen_dataset('long_profit');add_candles(a.db,ds.candles)
    a.now=ds.candles[-1].close_time+timedelta(minutes=1)
    assert a.runner().poll_once()==1
    assert a.db.rows['paper_mandate_work'][-1]['outcome_code']=='POSITION_CLOSED'
    before=deepcopy(a.db.rows);a.now+=timedelta(days=1)
    forbidden=Mock(side_effect=AssertionError('First lifecycle is terminal'))
    assert a.runner(processor=forbidden).poll_once()==0
    assert a.db.rows==before

def pending(a):
    candle=max(a.db.rows['market_candles'],key=lambda row:row['open_time'])
    with a.process_sessions() as db:
        with db.begin():
            row=ensure_work(db.connection(),MID,candle['id'],runner_version='paper-runner-v1',
                created_at=a.now,fresh_until=expected_candle(a.now)[2])
    return row

def test_uncertain_existing_work_quarantined_under_sql_locks(pipeline):
    a=prepared(pipeline('no_trades'));row=pending(a)
    result=CycleResult(MID,'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE','VERIFY_DURABLE_STATE_BEFORE_RETRY',row['work_id'])
    assert recover_uncertain(a.process_sessions,Candidate(MID,USER),result,a.now)=='QUARANTINED'
    forbidden=Mock(side_effect=AssertionError('Reconciliation required'))
    assert a.runner(processor=forbidden).poll_once()==0
    assert a.db.rows['paper_mandate_work'][0]['state']=='RECONCILIATION_REQUIRED'
    assert not a.db.rows['paper_authorizations']

def test_uncertain_commit_read_recovery_does_not_execute(pipeline):
    a=prepared(pipeline('no_trades'));assert a.runner().poll_once()==1
    row=a.db.rows['paper_mandate_work'][0]
    result=CycleResult(MID,'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE','VERIFY_DURABLE_STATE_BEFORE_RETRY',row['work_id'])
    before=deepcopy(a.db.rows)
    assert recover_uncertain(a.process_sessions,Candidate(MID,USER),result,a.now)=='COMMITTED'
    assert a.db.rows==before

def test_retry_wait_due_time_respected(pipeline):
    a=prepared(pipeline('no_trades'));row=pending(a)
    actual=a.db.rows['paper_mandate_work'][0]
    actual.update(state='RETRY_WAIT',retry_at=a.now+timedelta(seconds=30),outcome_code='UNAVAILABLE',reason_code='UNAVAILABLE')
    candidate=Candidate(MID,USER)
    assert not due(a.process_sessions,candidate,a.now)
    assert due(a.process_sessions,candidate,a.now+timedelta(seconds=30))

def test_downtime_has_no_historical_trade_replay(pipeline):
    a=prepared(pipeline());a.now+=timedelta(days=3)
    assert a.runner().poll_once()==1
    assert all(n==0 for n in economic_counts(a.db).values())
    assert not a.db.rows['paper_authorizations']
    assert not a.db.rows['paper_mandate_work']  # latest canonical candle is absent

def test_empty_rollback_recovery_is_read_only(pipeline):
    a=prepared(pipeline('no_trades'))
    result=CycleResult(MID,'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE','VERIFY_DURABLE_STATE_BEFORE_RETRY',uuid4())
    assert recover_uncertain(a.process_sessions,Candidate(MID,USER),result,a.now)=='ROLLED_BACK'
    assert not a.db.rows['paper_mandate_work']


def test_live_mandate_is_never_discovered(pipeline):
    a=prepared(pipeline('no_trades'))
    a.db.rows['trading_mandates'][0]['environment']='LIVE'
    forbidden=Mock(side_effect=AssertionError('PAPER runner must never process LIVE'))
    assert a.runner(processor=forbidden).poll_once()==0
    forbidden.assert_not_called()
