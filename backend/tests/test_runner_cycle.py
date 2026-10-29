"""One-cycle control/transaction tests with isolated persistence doubles.
Not physical PostgreSQL concurrency proof.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from sqlalchemy.exc import SQLAlchemyError
from app.mandate_runner import cycle as r
from app.mandate_runner.market import MaintenanceResult
from app.mandate_runner.repository import work_identity
from app.mandate_eligibility.contracts import EligibilityStatus
from app.mandates.models import TradingMandate
from app.mandates.domain import MandateError
from app.mandate_paper.contracts import PaperRunError
from app.market_data.completed import ExpectedCandleMismatch

AT=datetime(2026,10,26,12,1,tzinfo=timezone.utc)

@pytest.fixture
def h(monkeypatch):
    h=SimpleNamespace(mid=uuid4(),uid=uuid4(),instrument=uuid4(),candle_id=uuid4(),now=AT,
        data={'work':None,'effects':0,'authority':0},active_transactions=0,run_calls=0,commits=0,
        rollbacks=0,fail_commit=False,phase=0)
    h.mandate=SimpleNamespace(id=h.mid,user_id=h.uid,status='ACTIVE')
    h.candle=SimpleNamespace(id=h.candle_id,instrument_id=h.instrument,
        open_time=AT.replace(hour=11,minute=0),close_time=AT.replace(minute=0)-timedelta(milliseconds=1))
    h.market=MaintenanceResult('READY','COMPLETED_WINDOW_READY',AT,h.candle.open_time,h.candle.close_time,
        AT.replace(minute=10),instrument_id=h.instrument,candle_id=h.candle_id,
        freshness='FRESH',safe_for_evaluation=True)
    h.eligibility=SimpleNamespace(status=EligibilityStatus.ELIGIBLE,eligibility_identity='eligibility')
    @contextmanager
    def begin(nested=False):
        saved=deepcopy(h.data);h.active_transactions+=1
        try:
            yield
            if not nested and h.fail_commit:raise SQLAlchemyError('private connection info')
            if not nested:h.commits+=1
        except Exception:
            h.data=saved;h.rollbacks+=1;raise
        finally:h.active_transactions-=1
    class Session:
        def begin(self):return begin()
        def begin_nested(self):return begin(True)
        def connection(self):return self
        def scalar(self,q):
            entity=q.column_descriptions[0]['entity']
            return h.mandate if entity is TradingMandate else h.candle
    @contextmanager
    def session():yield Session()
    h.session=session
    def eligibility(*args,**kwargs):return h.eligibility
    monkeypatch.setattr(r,'inspect_eligibility',eligibility)
    def owned(*args,**kwargs):
        assert kwargs['lock'] is True
        return h.mandate
    monkeypatch.setattr(r,'owned',owned)
    def maintenance(*args,**kwargs):
        assert h.active_transactions==0
        return h.market
    h.maintenance=Mock(side_effect=maintenance);monkeypatch.setattr(r,'maintain_market',h.maintenance)
    monkeypatch.setattr(r,'load_work',lambda *a:h.data['work'])
    def barrier(*a,**kw):
        row=h.data['work']
        return row if row is not None and (row['state']=='RECONCILIATION_REQUIRED' or row['outcome_code']=='POSITION_CLOSED') else None
    monkeypatch.setattr(r,'barrier_work',barrier)
    def ensure(*a,**kw):
        h.data['work']=dict(work_id=work_identity(h.mid,h.candle_id),state='PENDING',attempts=0,
            fresh_until=kw['fresh_until'],updated_at=kw['created_at'],retry_at=None,outcome_code=None,reason_code=None)
        return h.data['work']
    h.ensure=Mock(side_effect=ensure);monkeypatch.setattr(r,'ensure_work',h.ensure)
    def attempt(*a,**kw):
        row=h.data['work'];assert row['state'] not in r.TERMINAL
        row['attempts']+=1;row['state']='PENDING';row['retry_at']=None
        return row
    monkeypatch.setattr(r,'record_attempt',attempt)
    def result(*a,**kw):
        row=h.data['work'];assert row['state'] not in r.TERMINAL
        if kw['state']=='RETRY_WAIT':assert kw['at']<=kw['retry_at']<row['fresh_until']
        row.update(kw);return row
    monkeypatch.setattr(r,'record_result',result)
    h.outcome='NO_TRADE';h.error=None;h.reused=False
    def run(*args,**kw):
        h.run_calls+=1
        assert args[1]==h.uid and args[2]==h.mid
        assert kw['evaluated_at']==h.now and kw['expected_candle'].open_time==h.candle.open_time
        kw['clock']()
        if h.error:
            h.data['effects']+=1;h.data['authority']+=1
            raise h.error
        if h.outcome in ('PAPER_EXECUTED','POSITION_CLOSED') and not h.reused:
            h.data['effects']+=1;h.data['authority']+=1
        execution=SimpleNamespace(request_identity='request',position_id=h.mid) if h.outcome in ('PAPER_EXECUTED','POSITION_CLOSED') else None
        return SimpleNamespace(status=h.outcome,reused=h.reused,registry_identity='registry' if execution else None,
            execution=execution,position_id=h.mid if execution else None)
    h.run=Mock(side_effect=run);monkeypatch.setattr(r,'run_paper',h.run)
    h.process=lambda:r.process_one(h.session,h.mid,clock=lambda:h.now)
    return h

@pytest.mark.parametrize('outcome',['NO_TRADE','PAPER_EXECUTED','POSITION_HELD','RISK_REJECTED','EXIT_RISK_REJECTED','POSITION_CLOSED'])
def test_outcomes_are_successful_completed_evaluations(h,outcome):
    h.outcome=outcome;result=h.process()
    assert result.state=='COMPLETED' and result.persisted and result.outcome_code==outcome
    assert h.run_calls==1 and h.data['work']['attempts']==1
    assert h.data['effects']==(1 if outcome in ('PAPER_EXECUTED','POSITION_CLOSED') else 0)

@pytest.mark.parametrize('outcome',['NO_TRADE','PAPER_EXECUTED','POSITION_HELD','POSITION_CLOSED'])
def test_duplicate_and_restart_recover_without_invocation(h,outcome):
    h.outcome=outcome;first=h.process();second=h.process()
    assert second.reused and second.work_id==first.work_id and h.run_calls==1
    assert h.ensure.call_count==1
    assert h.data['effects']<=1

@pytest.mark.parametrize('status',['PAUSED','RETIRED'])
def test_pause_or_retire_race_rechecked_under_lock(h,monkeypatch,status):
    def changed(*a,**kw):
        h.mandate.status=status
        h.eligibility=SimpleNamespace(status=EligibilityStatus.INELIGIBLE,eligibility_identity='changed')
        return h.mandate
    monkeypatch.setattr(r,'owned',changed)
    result=h.process()
    assert result.state=='SKIPPED' and result.persisted and h.run_calls==0

def test_ineligible_preflight_never_fetches(h):
    h.eligibility.status=EligibilityStatus.INELIGIBLE
    assert h.process().state=='SKIPPED'
    h.maintenance.assert_not_called();h.run.assert_not_called()

def test_stale_current_candle_cannot_create_backdated_work(h):
    h.now=AT.replace(minute=11)
    result=h.process()
    assert result.state=='SKIPPED' and not result.persisted and h.data['work'] is None
    h.run.assert_not_called()

def test_stale_existing_work_is_durably_skipped(h):
    h.error=PaperRunError('temporary','unavailable',503)
    assert h.process().state=='RETRY_WAIT'
    h.now=AT.replace(minute=11)
    assert h.process().state=='SKIPPED' and h.data['work']['state']=='SKIPPED'
    assert h.run_calls==1

def test_missing_canonical_candle_never_fabricates_work(h):
    h.candle=None
    result=h.process()
    assert result.state=='RETRY_WAIT' and not result.persisted
    h.ensure.assert_not_called();h.run.assert_not_called()

def test_expected_candle_mismatch_rolls_back_authority_and_economics(h):
    h.error=ExpectedCandleMismatch('wrong observed candle')
    result=h.process()
    assert result.state=='SKIPPED' and result.outcome_code=='EXPECTED_CANDLE_MISMATCH'
    assert h.data['effects']==h.data['authority']==0 and h.rollbacks==1

def test_actual_candle_id_changed_stops_before_paper(h):
    h.market=replace(h.market,candle_id=uuid4())
    assert h.process().outcome_code=='EXPECTED_CANDLE_MISMATCH'
    h.run.assert_not_called()

def test_retry_due_and_recovery_does_not_duplicate(h):
    h.error=PaperRunError('temporary','safe unavailable',503)
    first=h.process()
    assert first.state=='RETRY_WAIT' and h.data['effects']==0
    h.error=None;h.outcome='PAPER_EXECUTED'
    assert h.process().state=='RETRY_WAIT' and h.run_calls==1
    h.now+=timedelta(seconds=30)
    assert h.process().state=='COMPLETED' and h.data['work']['attempts']==2
    assert h.process().reused and h.data['effects']==1

@pytest.mark.parametrize('code',['paper_run_reconciliation_required','mandate_authorization_mismatch','mandate_authorization_conflict'])
def test_reconciliation_is_terminal_and_never_resubmits(h,code):
    h.error=PaperRunError(code,'private detail',409)
    result=h.process()
    assert result.state=='RECONCILIATION_REQUIRED' and 'private' not in repr(result)
    assert h.data['effects']==0
    assert h.process().reused and h.run_calls==1

def test_unexpected_exception_rolls_back_then_blocks(h):
    h.error=RuntimeError('secret details')
    assert h.process().state=='RECONCILIATION_REQUIRED'
    assert h.data['effects']==0 and h.data['authority']==0

def test_closed_receipt_recovery_never_reenters(h):
    h.outcome='POSITION_CLOSED';h.reused=True
    result=h.process()
    assert result.state=='SKIPPED' and result.reason_code=='LIFECYCLE_ALREADY_CLOSED'
    assert h.data['effects']==0

def test_integrity_conflict_blocks_and_persists(h):
    h.market=replace(h.market,status='INTEGRITY_CONFLICT',safe_for_evaluation=False)
    assert h.process().state=='RECONCILIATION_REQUIRED'
    h.run.assert_not_called()

@pytest.mark.parametrize('status',['PROVIDER_FAILED','STORAGE_FAILED'])
def test_transient_maintenance_failure_persists_wait_where_candle_exists(h,status):
    h.market=replace(h.market,status=status,safe_for_evaluation=False)
    result=h.process()
    assert result.state=='RETRY_WAIT' and result.persisted
    h.run.assert_not_called()

def test_unsafe_evidence_skips(h):
    h.market=replace(h.market,status='EVIDENCE_UNSAFE',safe_for_evaluation=False)
    assert h.process().state=='SKIPPED'
    h.run.assert_not_called()

def test_uncertain_commit_never_reports_success(h):
    # Only fail the final outer transaction commit, after an economic action.
    original=h.run.side_effect
    def run(*args,**kw):
        result=original(*args,**kw);h.fail_commit=True;return result
    h.run.side_effect=run;h.outcome='PAPER_EXECUTED'
    result=h.process()
    assert result.state=='RECONCILIATION_REQUIRED' and not result.persisted
    assert h.data['effects']==0

def test_execution_clock_rechecks_after_evaluation(h):
    original=h.run.side_effect
    def run(*args,**kw):
        result=original(*args,**kw);h.now=AT.replace(minute=11);return result
    h.run.side_effect=run;h.outcome='PAPER_EXECUTED'
    assert h.process().state=='SKIPPED' and h.data['effects']==0


def test_reconciliation_blocks_later_candles_before_market_maintenance(h):
    h.error=PaperRunError('paper_run_reconciliation_required','unknown',409)
    assert h.process().state=='RECONCILIATION_REQUIRED'
    h.now+=timedelta(hours=1);h.maintenance.reset_mock()
    assert h.process().state=='RECONCILIATION_REQUIRED'
    assert h.run_calls==1
    h.maintenance.assert_not_called()

def test_closed_lifecycle_blocks_later_candles_before_market_maintenance(h):
    h.outcome='POSITION_CLOSED'
    assert h.process().state=='COMPLETED'
    h.now+=timedelta(hours=1);h.maintenance.reset_mock()
    result=h.process()
    assert result.state=='SKIPPED' and result.reason_code=='LIFECYCLE_ALREADY_CLOSED'
    h.maintenance.assert_not_called()
    assert h.run_calls==1 and h.data['effects']==1

def test_barrier_rechecked_after_maintenance_race(h,monkeypatch):
    calls=[0]
    def barrier(*args,**kw):
        calls[0]+=1
        if calls[0]==1:return None
        return dict(work_id=uuid4(),state='RECONCILIATION_REQUIRED',outcome_code='UNKNOWN',reason_code='RECONCILE')
    monkeypatch.setattr(r,'barrier_work',barrier)
    assert h.process().state=='RECONCILIATION_REQUIRED'
    h.run.assert_not_called()

def test_result_persistence_failure_rolls_back_economic_success(h,monkeypatch):
    original=r.record_result
    def fail_once(*args,**kwargs):
        if kwargs['state']=='COMPLETED':
            raise SQLAlchemyError('result persistence failed')
        return original(*args,**kwargs)
    monkeypatch.setattr(r,'record_result',fail_once);h.outcome='PAPER_EXECUTED'
    result=h.process()
    assert result.state=='RETRY_WAIT' and h.data['effects']==h.data['authority']==0


def test_runner_version_change_does_not_create_independent_work(h,monkeypatch):
    first=h.process();monkeypatch.setattr(r,'RUNNER_VERSION','paper-runner-v2')
    second=h.process()
    assert second.work_id==first.work_id and second.reused and h.run_calls==1

def test_recovery_of_completed_work_after_same_candle_expiry(h):
    first=h.process();h.now=AT.replace(minute=11)
    second=h.process()
    assert second.reused and second.work_id==first.work_id and h.run_calls==1


def test_missing_candle_after_expiry_skips_instead_of_retries(h):
    h.candle=None;h.now=AT.replace(minute=11)
    assert h.process().state=='SKIPPED'
    h.run.assert_not_called()

def test_missing_candle_conflict_requires_reconciliation(h):
    h.candle=None;h.market=replace(h.market,status='INTEGRITY_CONFLICT',safe_for_evaluation=False)
    assert h.process().state=='RECONCILIATION_REQUIRED'
    h.run.assert_not_called()
