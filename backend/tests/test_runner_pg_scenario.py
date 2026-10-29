"""Historical harness scenarios only; no PostgreSQL, provider or production clock changes."""
import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import inspect
import json
import pickle
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID
import pytest
from integration import runner_postgres as h
from integration.runner_scenario import ScenarioTimeline, ScenarioClock
from test_runner_pipeline import pipeline


def test_timeline_is_immutable_json_and_spawn_serializable():
    timeline=ScenarioTimeline()
    with pytest.raises(FrozenInstanceError):timeline.entry_at=datetime.now(timezone.utc)
    assert ScenarioTimeline.from_dict(json.loads(json.dumps(timeline.to_dict())))==timeline
    assert pickle.loads(pickle.dumps(timeline))==timeline
    assert pickle.loads(pickle.dumps(timeline.clock(timeline.hold_at)))()==timeline.hold_at
    invalid=timeline.to_dict();invalid['exit_at']=timeline.entry_at.isoformat()
    with pytest.raises(ValueError):ScenarioTimeline.from_dict(invalid)
    with pytest.raises(ValueError):ScenarioClock(datetime(2026,1,1))


@pytest.mark.parametrize('phase',h.CASES)
def test_eight_phases_have_no_wall_clock_or_hour_window(phase):
    functions={'contracts':h.contracts,'locks':h.locks,'race':h.concurrent,
        'rollback':h.rollback,'kill':h.concurrent,'recovery':h.concurrent,
        'barriers':h.barriers,'close':h.close_lifecycle}
    for fn in (functions[phase],h.seed,h.pending,h.worker,h.cycle_once):
        source=inspect.getsource(fn)
        assert 'datetime.now' not in source and 'fresh_clock' not in source
        assert 'utc_now' not in source
    source=inspect.getsource(h.main)
    assert 'fresh_clock' not in source and 'WAIT_FOR_FIRST_FIVE' not in source
    # Admission still proves the complete simulated timeline is historical.
    assert "timeline.terminal_retry_at<server_now" in source


def test_every_safety_cycle_call_has_an_explicit_time():
    tree=ast.parse(inspect.getsource(h))
    calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
        and isinstance(node.func,ast.Name) and node.func.id=='cycle_once']
    assert calls and all('at' in {kw.arg for kw in node.keywords} for node in calls)
    assert inspect.signature(h.cycle_once).parameters['at'].default is inspect.Parameter.empty
    assert 'Deadline(' in inspect.getsource(h.receive)
    assert 'Deadline(' in inspect.getsource(h.concurrent)
    assert 'time.monotonic' in inspect.getsource(h.Deadline)


def test_cycle_bridge_has_no_real_clock_fallback(monkeypatch):
    from app.mandate_runner import cycle
    timeline=ScenarioTimeline();seen=[];mid=UUID(int=77)
    monkeypatch.setattr(h,'sessionmaker',lambda **kw:'sessions')
    def process(sessions,mandate_id,*,clock,provider_factory):
        assert sessions=='sessions' and mandate_id==mid
        seen.append(clock())
        with pytest.raises(h.Refused,match='EXTERNAL_PROVIDER_FORBIDDEN'):
            with provider_factory():pass
        return 'result'
    monkeypatch.setattr(cycle,'process_one',process)
    for at in (timeline.entry_at,timeline.retry_at,timeline.hold_at,timeline.exit_at,timeline.terminal_retry_at):
        assert h.cycle_once(object(),{'mandate':mid,'timeline':timeline},at=at)=='result'
    assert seen==[timeline.entry_at,timeline.retry_at,timeline.hold_at,timeline.exit_at,timeline.terminal_retry_at]
    with pytest.raises(TypeError):h.cycle_once(object(),{'mandate':mid})


def test_shifted_fixture_reuses_real_entry_hold_exit_and_rejects_stale():
    from app.market_intelligence.types import Candle,DatasetContext
    from app.risk.paper_source.market import analyze,PaperEvidenceUnavailable
    from app.strategies.inputs import PositionState
    timeline=ScenarioTimeline();iid=UUID(int=1)
    rows=h.fixture_rows(iid,timeline.entry_at)
    candles=tuple(Candle(**{key:r[key] for key in Candle.__dataclass_fields__}) for r in rows)
    context=DatasetContext(iid,'BTC/USDT','BINANCE','1h')
    for index,at,position,expected in (
        (130,timeline.entry_at,PositionState.CASH,'ENTER_LONG'),
        (131,timeline.hold_at,PositionState.LONG,'NO_ACTION'),
        (218,timeline.exit_at,PositionState.LONG,'EXIT_LONG')):
        observation,decision,market=analyze(context,candles[index-99:index+1],at=at,position_state=position)
        assert decision.decision.value==expected
        assert market.expires_at>at
    with pytest.raises(PaperEvidenceUnavailable):
        analyze(context,candles[31:131],at=timeline.stale_at)


def historical_pipeline(pipeline):
    a=pipeline();timeline=ScenarioTimeline();offset=timeline.entry_at-a.now
    for rows in a.db.rows.values():
        for row in rows:
            for key,value in tuple(row.items()):
                if isinstance(value,datetime):row[key]=value+offset
    a.now=timeline.entry_at
    return a,timeline


def test_real_risk_and_accounting_receive_same_scenario_clock(pipeline,monkeypatch):
    from app.execution.registry.repository import load_authorization
    from test_mandate_paper import MID,USER,economic_counts
    a,timeline=historical_pipeline(pipeline)
    monkeypatch.setattr(h,'sessionmaker',lambda **kw:a.session)
    f={'mandate':MID,'timeline':timeline}
    entry=h.cycle_once(object(),f,at=timeline.entry_at)
    assert entry.outcome_code=='PAPER_EXECUTED' and entry.persisted
    package=load_authorization(a.db,entry.registry_identity,user_id=USER)
    assert package.authorization.decision.evaluated_at==timeline.entry_at
    assert all(row['executed_at']==timeline.entry_at for row in a.db.rows['paper_accounting_entries'])
    package.authorization.validate_at(timeline.entry_at)
    with pytest.raises(ValueError,match='expired'):
        package.authorization.validate_at(timeline.expired_at)
    # An expired completed result is recoverable; its authorization is not renewed.
    before=deepcopy(a.db.rows);a.now=timeline.expired_at
    again=h.cycle_once(object(),f,at=timeline.expired_at)
    assert again.reused and a.db.rows==before
    assert load_authorization(a.db,entry.registry_identity,user_id=USER)==package




def test_scenario_stale_work_deadline_still_rejects():
    from app.mandate_runner.repository import record_attempt,record_result
    from test_mandate_runner import Connection,row
    timeline=ScenarioTimeline()
    c=Connection(row(created_at=timeline.entry_at,updated_at=timeline.entry_at,
        fresh_until=timeline.stale_at))
    with pytest.raises(ValueError,match='stale'):
        record_attempt(c,c.row['work_id'],at=timeline.stale_at)
    assert not any(getattr(statement,'is_update',False) for statement in c.statements)
    with pytest.raises(ValueError,match='original freshness'):
        record_result(c,c.row['work_id'],state='RETRY_WAIT',at=timeline.retry_at,
            outcome_code='RETRY',retry_at=timeline.stale_at)


def test_historical_real_pipeline_hold_exit_retry(pipeline,monkeypatch):
    from test_mandate_paper import MID,economic_counts
    a,timeline=historical_pipeline(pipeline)
    monkeypatch.setattr(h,'sessionmaker',lambda **kw:a.session)
    f={'mandate':MID,'timeline':timeline}
    rows=h.fixture_rows(UUID(int=1),timeline.entry_at)
    # The SQL ledger double has no server defaults; physical PostgreSQL supplies these.
    for row in rows:row.update(created_at=row['retrieved_at'],updated_at=row['retrieved_at'])
    entry=h.cycle_once(object(),f,at=timeline.entry_at)
    assert entry.outcome_code=='PAPER_EXECUTED'
    a.db.rows['market_candles'].extend(rows[131:132]);a.now=timeline.hold_at
    before=economic_counts(a.db)
    held=h.cycle_once(object(),f,at=timeline.hold_at)
    assert held.outcome_code=='POSITION_HELD' and economic_counts(a.db)==before
    a.db.rows['market_candles'].extend(rows[132:]);a.now=timeline.exit_at
    closed=h.cycle_once(object(),f,at=timeline.exit_at)
    assert closed.outcome_code=='POSITION_CLOSED' and closed.registry_identity!=entry.registry_identity
    before=deepcopy(a.db.rows);a.now=timeline.terminal_retry_at
    again=h.cycle_once(object(),f,at=timeline.terminal_retry_at)
    assert again.reused and again.outcome_code=='POSITION_CLOSED' and a.db.rows==before



def test_worker_receives_serialized_scenario_time(monkeypatch):
    timeline=ScenarioTimeline.from_dict(json.loads(json.dumps(ScenarioTimeline().to_dict())))
    f={'timeline':timeline};engine=SimpleNamespace(dispose=lambda:None)
    monkeypatch.setattr(h,'engine_for',lambda *a:engine)
    monkeypatch.setattr(h,'worker_bootstrap',lambda e:None)
    monkeypatch.setattr(h.event,'listens_for',lambda *a,**kw:lambda fn:fn)
    execute=Mock(return_value=SimpleNamespace(state='COMPLETED',outcome_code='NO_TRADE',persisted=True,reused=False))
    monkeypatch.setattr(h,'cycle_once',execute)
    sent=[];pipe=SimpleNamespace(send=sent.append,close=lambda:None)
    h.worker('not-read','not-read','not-used',f,'normal',pipe,None,start=SimpleNamespace(wait=lambda timeout:True))
    execute.assert_called_once_with(engine,f,at=timeline.entry_at)
    assert any(kind=='RESULT' for kind,value in sent)


def test_barrier_recovery_and_due_receive_scenario_time(monkeypatch):
    from app.mandate_runner import process
    timeline=ScenarioTimeline();f={'timeline':timeline,'mandate':UUID(int=77),'user':UUID(int=2)}
    monkeypatch.setattr(h,'pending',lambda *a:{'work_id':UUID(int=9)})
    monkeypatch.setattr(h,'sessionmaker',lambda **kw:'sessions')
    recover=Mock(return_value='QUARANTINED');due=Mock(return_value=False)
    monkeypatch.setattr(process,'recover_uncertain',recover);monkeypatch.setattr(process,'due',due)
    execute=Mock(return_value=SimpleNamespace(reused=True,state='RECONCILIATION_REQUIRED'))
    monkeypatch.setattr(h,'cycle_once',execute);monkeypatch.setattr(h,'counts',lambda e:{'fixture':0})
    h.barriers(object(),f)
    assert recover.call_args.args[-1]==timeline.entry_at
    assert due.call_args.args[-1]==timeline.entry_at
    assert execute.call_args.kwargs['at']==timeline.entry_at



def test_unused_approval_cannot_execute_at_scenario_expiry(pipeline):
    from app.mandate_risk.service import evaluate
    from app.mandate_paper.service import run
    from app.mandate_paper.contracts import PaperRunError
    from app.execution.registry.repository import load_authorization
    from test_runner_pipeline import RunnerSession
    from test_mandate_paper import MID,USER,economic_counts
    a,timeline=historical_pipeline(pipeline)
    session=RunnerSession(a.db)
    approved=evaluate(session,USER,MID,evaluated_at=timeline.entry_at)
    assert approved.status.value=='APPROVED'
    package=load_authorization(a.db,approved.authorization.registry_identity,user_id=USER)
    assert package.authorization.decision.approval_expires_at==timeline.expired_at
    assert all(n==0 for n in economic_counts(a.db).values())
    with pytest.raises(PaperRunError):
        run(session,USER,MID,evaluated_at=timeline.entry_at,clock=ScenarioClock(timeline.expired_at))
    assert all(n==0 for n in economic_counts(a.db).values())
    assert load_authorization(a.db,approved.authorization.registry_identity,user_id=USER)==package
