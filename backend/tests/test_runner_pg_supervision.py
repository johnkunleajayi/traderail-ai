"""Offline supervision/inspection-contract tests, not physical PostgreSQL proof."""
from contextlib import contextmanager
from decimal import Decimal
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID
import pytest
from integration import runner_postgres as h
from test_runner_pg_harness import target_files
from integration.runner_supervision import (Deadline,EvidenceLog,SupervisionError,
    WorkerCleanupError,WorkerLimits,stop_owned,wait_gate)


class Process:
    def __init__(self,*,stubborn=False,**kwargs):
        self.kwargs=kwargs;self.pid=None;self.alive=False;self.exitcode=None
        self.stubborn=stubborn;self.terminated=False;self.joined=[]
    def start(self):self.pid=123;self.alive=True
    def is_alive(self):return self.alive
    def terminate(self):
        self.terminated=True
        if not self.stubborn:self.alive=False;self.exitcode=-15
    def join(self,timeout):self.joined.append(timeout)


def test_monotonic_deadline_and_startup_delay():
    clock=[0.0];deadline=Deadline(120,clock=lambda:clock[0])
    clock[0]=90
    assert deadline.remaining()==30
    clock[0]=120
    with pytest.raises(SupervisionError):deadline.remaining()


def test_gate_wait_is_bounded_without_releasing_it():
    clock=[0.0];deadline=Deadline(1,clock=lambda:clock[0]);calls=[]
    def wait(timeout):calls.append(timeout);clock[0]+=.4;return False
    gate=SimpleNamespace(wait=wait,set=Mock(side_effect=AssertionError('Must never release')))
    with pytest.raises(SupervisionError,match='PAUSED_DEADLINE'):
        wait_gate(gate,deadline,'PAUSED_DEADLINE')
    assert all(0<t<=.2 for t in calls)
    gate.set.assert_not_called()


def test_stop_owned_confirms_termination_but_not_rollback():
    process=Process();process.start();log=EvidenceLog()
    stop_owned(process,Deadline(1),log,'A')
    assert process.terminated and process.joined and not process.is_alive()
    assert log.rows[-1]['event']=='TERMINATED'
    assert log.rows[-1]['transaction_outcome']=='UNCONFIRMED_REQUIRES_DURABLE_INSPECTION'


def test_stop_owned_cleanup_failure_is_fail_closed():
    process=Process(stubborn=True);process.start();log=EvidenceLog()
    with pytest.raises(WorkerCleanupError):stop_owned(process,Deadline(1),log,'A')
    assert process.terminated and process.joined and process.is_alive()
    assert log.rows[-1]['event']=='FAILED'


def test_owned_finite_process_really_terminates_without_database():
    import multiprocessing,time
    process=multiprocessing.get_context('spawn').Process(target=time.sleep,args=(60,))
    try:
        process.start();stop_owned(process,Deadline(10),EvidenceLog(),'A')
        assert not process.is_alive() and process.exitcode is not None
    finally:
        if process.is_alive():process.terminate();process.join(10)
        process.close()


def test_evidence_is_fresh_append_only_and_separate_per_worker(tmp_path):
    path=tmp_path/'events.jsonl';log=EvidenceLog(path)
    log.record('STARTUP_AUTHORIZED',worker='A',source='SUPERVISOR')
    log.record('PRE_COMMIT',worker='A',confirmation='APPLICATION_REPORT_ONLY',
        transaction_outcome='UNCONFIRMED_REQUIRES_DURABLE_INSPECTION')
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    assert [row['event'] for row in rows]==['STARTUP_AUTHORIZED','PRE_COMMIT']
    assert log.worker_path('A')!=log.worker_path('B')!=log.worker_path('INSPECT1')
    with pytest.raises(FileExistsError):EvidenceLog(path)
    with pytest.raises(SupervisionError):log.record('RUNNING',password='forbidden')
    assert 'password' not in path.read_text()


def test_startup_failure_is_distinct_and_sanitized():
    pipe=SimpleNamespace(poll=lambda t:True,recv=lambda:('FAILED',{'category':'SOCKET_FAILURE','phase':'BOOTSTRAP'}))
    with pytest.raises(h.Refused,match='^WORKER_STARTUP_SOCKET_FAILURE$'):
        h.receive(pipe,'READY')


def test_delayed_readiness_uses_startup_deadline_not_twenty_seconds(monkeypatch):
    ticks=[0.0]
    monkeypatch.setattr(h.time,'monotonic',lambda:ticks[0])
    def poll(timeout):ticks[0]+=10;return ticks[0]>=30
    pipe=SimpleNamespace(poll=poll,recv=lambda:('READY',None))
    assert h.receive(pipe,'READY',deadline=Deadline(120)) is None
    assert ticks[0]==30


def test_independent_worker_bootstrap_and_lifecycle_reports(monkeypatch,tmp_path):
    engines=[];boot=[];sequences=[]
    def engine_for(*args):
        engine=SimpleNamespace(dispose=Mock());engines.append(engine);return engine
    monkeypatch.setattr(h,'engine_for',engine_for)
    monkeypatch.setattr(h,'worker_bootstrap',boot.append)
    monkeypatch.setattr(h.event,'listens_for',lambda *args,**kw:lambda fn:fn)
    monkeypatch.setattr(h,'cycle_once',lambda *args,**kw:SimpleNamespace(
        state='COMPLETED',outcome_code='PAPER_EXECUTED',persisted=True,reused=False))
    for name in ('A','B'):
        sent=[];pipe=SimpleNamespace(send=sent.append,close=lambda:None)
        def wait(timeout):
            assert sent[-1][0]=='READY';return True
        h.worker('unread-config','unread-approval','private-schema',{'timeline':h.ScenarioTimeline()},
            'normal',pipe,None,start=SimpleNamespace(wait=wait),worker_name=name,events_path=str(tmp_path/(name+'.jsonl')))
        sequences.append([kind for kind,value in sent])
        committed=next(value for kind,value in sent if kind=='COMMITTED')
        assert committed['confirmation']=='APPLICATION_REPORT_ONLY'
        assert committed['transaction_outcome']=='UNCONFIRMED_REQUIRES_DURABLE_INSPECTION'
    assert engines[0] is not engines[1] and boot==engines
    assert all(sequence.index('READY')<sequence.index('RUNNING')<sequence.index('COMMITTED') for sequence in sequences)


@pytest.fixture
def coordinator(monkeypatch):
    processes=[];events=[];pipes=[];log=EvidenceLog()
    class Context:
        def Event(self):
            event=SimpleNamespace(set=Mock());events.append(event);return event
        def Pipe(self):
            pair=(SimpleNamespace(close=Mock(),send=Mock()),SimpleNamespace(close=Mock()))
            pipes.extend(pair);return pair
        def Process(self,**kwargs):
            process=Process(**kwargs);processes.append(process);return process
    monkeypatch.setattr(h.mp,'get_context',lambda name:Context())
    execute=Mock(side_effect=AssertionError('No economic replay before durable evidence'))
    monkeypatch.setattr(h,'cycle_once',execute)
    def receive(pipe,wanted,**kwargs):
        if wanted=='PRE_COMMIT':kwargs['observed']['LOCK']=111
        if wanted=='RESULT':return {'outcome':'PAPER_EXECUTED','persisted':True}
        return None
    monkeypatch.setattr(h,'receive',receive)
    return SimpleNamespace(processes=processes,events=events,pipes=pipes,log=log,receive=receive,
        execute=execute,args=SimpleNamespace(env_file='unread-config',approval='unread-approval'),
        f={'timeline':h.ScenarioTimeline()})


def test_stop_during_paused_transaction_never_releases_or_starts_waiter(coordinator,monkeypatch):
    a=coordinator
    def receive(pipe,wanted,**kw):
        if wanted=='PRE_COMMIT':raise h.Refused('SIMULATED_TRANSPORT_STOP')
        return a.receive(pipe,wanted,**kw)
    monkeypatch.setattr(h,'receive',receive)
    with pytest.raises(h.Refused,match='SIMULATED_TRANSPORT_STOP'):
        h.concurrent(object(),a.f,a.args,'private-schema','race',evidence=a.log)
    a.events[0].set.assert_not_called()  # shared commit-release gate
    a.events[2].set.assert_not_called()  # B's trading start gate
    assert all(p.terminated and not p.is_alive() and p.joined for p in a.processes)
    a.execute.assert_not_called()
    assert 'COMMIT_RELEASE_AUTHORIZED' not in [row['event'] for row in a.log.rows]
    assert a.log.rows[-1]['event']=='WORKER_CLEANUP_COMPLETE'


def test_cleanup_failure_preserves_primary_failure_and_still_stops_other_workers(coordinator,monkeypatch):
    a=coordinator
    def receive(pipe,wanted,**kw):
        if wanted=='PRE_COMMIT':
            a.processes[0].stubborn=True
            raise h.Refused('PRIMARY_ASSERTION_FAILURE')
        return a.receive(pipe,wanted,**kw)
    monkeypatch.setattr(h,'receive',receive)
    with pytest.raises(WorkerCleanupError) as error:
        h.concurrent(object(),a.f,a.args,'private-schema','race',evidence=a.log)
    assert str(error.value.primary)=='PRIMARY_ASSERTION_FAILURE'
    assert all(process.terminated for process in a.processes)
    assert not any(process.is_alive() for process in a.processes[1:])
    a.events[0].set.assert_not_called()
    assert any(row['event']=='CASE_FAILED' for row in a.log.rows)


@pytest.mark.parametrize('mode',['kill','recovery'])
def test_uncertain_durable_inspection_blocks_recovery_cycle(coordinator,monkeypatch,mode):
    a=coordinator
    def receive(pipe,wanted,**kw):
        if kw.get('worker_name','').startswith('INSPECT') and wanted=='RESULT':
            raise h.Refused('UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
        return a.receive(pipe,wanted,**kw)
    monkeypatch.setattr(h,'receive',receive)
    with pytest.raises(h.Refused,match='UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION'):
        h.concurrent(object(),a.f,a.args,'private-schema',mode,evidence=a.log)
    a.execute.assert_not_called()
    a.events[0].set.assert_not_called()
    assert all(not p.is_alive() for p in a.processes)


@pytest.mark.parametrize('state',['ROLLED_BACK','COMMITTED','PARTIAL','PENDING','WRONG_REFERENCE'])
def test_durable_inspection_reads_under_mandate_lock_and_refuses_ambiguity(monkeypatch,state):
    mid=UUID(int=77);cid=UUID(int=4);pid=UUID(int=8)
    row=None if state in ('ROLLED_BACK','PARTIAL') else dict(state='COMPLETED',outcome_code='PAPER_EXECUTED',
        request_identity='request',registry_identity='registry',position_id=pid)
    if state=='PENDING':row['state']='PENDING'
    if state=='WRONG_REFERENCE':row['request_identity']='wrong'
    queries=[]
    class Result:
        def __init__(self,value):self.value=value
        def scalar_one(self):return self.value
        def mappings(self):return self
        def one_or_none(self):return self.value
        def one(self):return self.value
    class Connection:
        def execute(self,statement,params=None):
            sql=str(statement);queries.append(sql)
            if 'FOR UPDATE' in sql:return Result(mid)
            if 'FROM paper_mandate_work' in sql:return Result(row)
            if 'cash_balance' in sql:return Result(Decimal('10000'))
            if 'count(*)' in sql:return Result(0 if state=='ROLLED_BACK' else 1)
            if 'request_identity,position_id' in sql:return Result({'request_identity':'request','position_id':pid})
            if 'registry_identity' in sql:return Result('registry')
            raise AssertionError(sql)
    @contextmanager
    def begin():yield Connection()
    count=Mock();monkeypatch.setattr(h,'expected_counts',count)
    f={'mandate':mid,'rows':[{'id':cid}]*131}
    if state in ('ROLLED_BACK','COMMITTED'):
        assert h.inspect_cycle_outcome(SimpleNamespace(begin=begin),f)==state
    else:
        with pytest.raises(h.Refused,match='UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION'):
            h.inspect_cycle_outcome(SimpleNamespace(begin=begin),f)
    assert 'FOR UPDATE' in queries[0]
    if state=='COMMITTED':count.assert_called_once()


def test_probe_is_read_only_and_has_no_execution_or_shared_engine_argument():
    source=inspect.getsource(h.database_probe)
    assert 'cycle_once' not in source and 'engine=engine_for(' in source
    assert 'inspect_cycle_outcome(engine,f)' in source
    assert 'pg_blocking_pids' in source
    assert 'engine' not in inspect.signature(h.database_probe).parameters



def test_worker_bootstrap_failure_never_enters_execution(monkeypatch,tmp_path):
    engine=SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(h,'engine_for',lambda *args:engine)
    monkeypatch.setattr(h.event,'listens_for',lambda *args,**kw:lambda fn:fn)
    monkeypatch.setattr(h,'worker_bootstrap',Mock(side_effect=RuntimeError('private-host password')))
    execute=Mock();monkeypatch.setattr(h,'cycle_once',execute)
    sent=[];pipe=SimpleNamespace(send=sent.append,close=lambda:None)
    with pytest.raises(SystemExit):
        h.worker('unread','unread','private',{'timeline':h.ScenarioTimeline()},'normal',pipe,None,
            start=Mock(),events_path=str(tmp_path/'worker.jsonl'))
    execute.assert_not_called();engine.dispose.assert_called_once()
    assert not any(kind in ('READY','RUNNING','COMMITTED') for kind,value in sent)
    text=(tmp_path/'worker.jsonl').read_text()
    assert 'private-host' not in text and 'password' not in text
    assert 'WORKER_BOOTSTRAP_FAILED' in text


@pytest.mark.parametrize('cleanup_failure',[False,True])
def test_pass_artifact_requires_resource_cleanup_and_full_finalization(target_files,monkeypatch,capsys,cleanup_failure):
    from integration.runner_clock import ClockSample,ClockMeasurement
    from datetime import datetime,timezone
    at=datetime(2026,10,28,12,tzinfo=timezone.utc)
    report=ClockMeasurement((ClockSample(at,at,at,0),))
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),get_isolation_level=lambda:'READ COMMITTED')
    @contextmanager
    def connect():yield connection
    engine=SimpleNamespace(connect=connect,dispose=Mock(side_effect=RuntimeError('private metadata') if cleanup_failure else None))
    monkeypatch.setattr(h,'engine_for',lambda *args:engine)
    monkeypatch.setattr(h,'require_public_state',lambda *args:None)
    monkeypatch.setattr(h,'verify_timeouts',lambda *args:None)
    monkeypatch.setattr(h,'measure_clock',lambda c:report)
    monkeypatch.setattr(h,'baseline',lambda e:{})
    monkeypatch.setattr(h,'start_preflight_watchdog',lambda:SimpleNamespace(cancel=lambda:None))
    output=h.ROOT/'work/finalization'
    args=['--phase','preflight','--approval',str(target_files.approval),'--confirm-child',
        target_files.data['child_branch_id'],'--artifacts',str(output)]
    assert h.main(args)==(1 if cleanup_failure else 0)
    journal=[json.loads(line) for line in next(output.glob('*-events.jsonl')).read_text().splitlines()]
    events=[row['event'] for row in journal]
    text=capsys.readouterr().out
    if cleanup_failure:
        assert not list(output.glob('*-preflight.json'))
        assert 'PASS_READY' not in events and 'RESOURCE_CLEANUP_FAILED' in events
        assert 'PASS: RUNNER POSTGRES' not in text
    else:
        assert json.loads(next(output.glob('*-preflight.json')).read_text())['status']=='PASS'
        assert events.index('ASSERTIONS_COMPLETE')<events.index('BASELINE_VERIFIED')<events.index('RESOURCE_CLEANUP_COMPLETE')<events.index('PASS_READY')
        assert not list(output.glob('*.pending'))
        assert 'PASS: RUNNER POSTGRES' in text
    assert 'private metadata' not in text



def test_cleanup_evidence_failure_does_not_mask_primary_or_skip_termination(coordinator,monkeypatch):
    a=coordinator;original=a.log.record
    def record(event,**fields):
        if event=='WORKER_CLEANUP_COMPLETE':raise OSError('private filesystem detail')
        original(event,**fields)
    a.log.record=record
    def receive(pipe,wanted,**kw):
        if wanted=='PRE_COMMIT':raise h.Refused('PRIMARY_FAILURE')
        return a.receive(pipe,wanted,**kw)
    monkeypatch.setattr(h,'receive',receive)
    with pytest.raises(WorkerCleanupError) as error:
        h.concurrent(object(),a.f,a.args,'private-schema','race',evidence=a.log)
    assert str(error.value.primary)=='PRIMARY_FAILURE'
    assert all(p.terminated and not p.is_alive() for p in a.processes)
    a.events[0].set.assert_not_called()


@pytest.mark.parametrize('collision',[False,True])
def test_catalog_details_and_final_pass_have_distinct_paths(target_files,monkeypatch,capsys,collision):
    from integration.runner_clock import ClockSample,ClockMeasurement
    from datetime import datetime,timezone
    at=datetime(2026,10,28,12,tzinfo=timezone.utc)
    measurement=ClockMeasurement((ClockSample(at,at,at,0),))
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),get_isolation_level=lambda:'READ COMMITTED')
    @contextmanager
    def connect():yield connection
    engine=SimpleNamespace(connect=connect,dispose=Mock())
    monkeypatch.setattr(h,'engine_for',lambda *args:engine)
    monkeypatch.setattr(h,'require_public_state',lambda *args:None)
    monkeypatch.setattr(h,'measure_clock',lambda c:measurement)
    monkeypatch.setattr(h,'baseline',lambda e:{})
    output=h.ROOT/'work/catalog-finalization';output.mkdir()
    (output/'baseline.json').write_text(json.dumps({'public_baseline':{},
        'target_sha256':target_files.data['target_sha256'],'child_branch_id':target_files.data['child_branch_id']}))
    run='abcdef0123456789'
    monkeypatch.setattr(h,'uuid4',lambda:SimpleNamespace(hex=run))
    details={'tables':{'fixture':{'columns':[]}},'skip_locked':'NOT_USED_BY_PRODUCTION'}
    monkeypatch.setattr(h,'catalog',Mock(return_value=details))
    final=output/(run+'-catalog.json')
    original=b'previous evidence must survive'
    if collision:final.write_bytes(original)
    args=['--phase','catalog','--env-file',str(target_files.env),'--approval',str(target_files.approval),
        '--confirm-child',target_files.data['child_branch_id'],'--artifacts',str(output)]
    assert h.main(args)==(1 if collision else 0)
    assert json.loads((output/(run+'-catalog-details.json')).read_text())==details
    journal=[json.loads(line) for line in (output/(run+'-catalog-events.jsonl')).read_text().splitlines()]
    assert 'BASELINE_VERIFIED' in [row['event'] for row in journal]
    text=capsys.readouterr().out
    if collision:
        assert final.read_bytes()==original
        assert (output/(run+'-catalog.json.pending')).exists()
        assert journal[-1]['category']=='PASS_ARTIFACT_ALREADY_EXISTS'
        assert 'category=PASS_ARTIFACT_ALREADY_EXISTS' in text
        assert 'PASS: RUNNER POSTGRES' not in text
    else:
        assert json.loads(final.read_text())['status']=='PASS'
        assert not list(output.glob('*.pending'))
        assert 'PASS: RUNNER POSTGRES CATALOG' in text
    engine.dispose.assert_called_once()


@pytest.mark.parametrize('commit_failure',[False,True])
def test_contracts_failure_retains_operation_and_sanitized_constraint(target_files,monkeypatch,capsys,commit_failure):
    from sqlalchemy.exc import IntegrityError
    from integration.runner_scenario import ScenarioTimeline
    timeline=ScenarioTimeline()
    original=RuntimeError('password=SECRET_HOST SQL VALUES PRIVATE_ROW')
    original.sqlstate='23514'
    original.diag=SimpleNamespace(constraint_name='ck_paper_work_times')
    failure=IntegrityError('SQL SECRET_HOST',{'password':'PRIVATE_ROW'},original)
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),get_isolation_level=lambda:'READ COMMITTED',
        execute=lambda *args:SimpleNamespace(scalar_one=lambda:timeline.terminal_retry_at.replace(year=2027)))
    @contextmanager
    def connect():yield connection
    engine=SimpleNamespace(connect=connect,dispose=Mock())
    monkeypatch.setattr(h,'engine_for',lambda *args:engine)
    monkeypatch.setattr(h,'require_public_state',lambda *args:None)
    monkeypatch.setattr(h,'baseline',lambda e:{})
    monkeypatch.setattr(h,'make_lab',Mock())
    from app.mandate_runner import runtime
    monkeypatch.setattr(runtime,'verify_schema',Mock())
    monkeypatch.setattr(h,'seed',lambda *args:{})
    def fail_pending(*args,before_commit=None):
        if commit_failure:before_commit()
        raise failure
    monkeypatch.setattr(h,'pending',fail_pending)
    output=h.ROOT/'work/contracts-diagnostics';output.mkdir()
    (output/'baseline.json').write_text(json.dumps({'public_baseline':{},
        'target_sha256':target_files.data['target_sha256'],'child_branch_id':target_files.data['child_branch_id']}))
    assert h.main(['--phase','contracts','--env-file',str(target_files.env),'--approval',str(target_files.approval),
        '--confirm-child',target_files.data['child_branch_id'],'--artifacts',str(output)])==1
    events=next(output.glob('*-events.jsonl')).read_text()
    rows=[json.loads(line) for line in events.splitlines()]
    failure_row=next(row for row in rows if row['event']=='VALIDATION_FAILED')
    operation='CONTRACTS_INITIAL_WORK_COMMIT' if commit_failure else 'CONTRACTS_INITIAL_WORK_CREATE'
    assert failure_row['operation']==operation
    assert failure_row['constraint']=='ck_paper_work_times'
    assert failure_row['sqlstate']=='23514' and failure_row['category']=='IntegrityError'
    assert next(row for row in rows if row.get('operation')==operation)['event']=='CONTRACT_OPERATION'
    assert not list(output.glob('*-contracts.json'))
    text=events+capsys.readouterr().out
    assert all(secret not in text for secret in ('SECRET_HOST','PRIVATE_ROW','password='))


@pytest.mark.parametrize('state',['23514','P0001'])
def test_expected_contract_rejections_still_rollback_savepoint(state):
    from sqlalchemy.exc import IntegrityError
    events=[]
    @contextmanager
    def savepoint():
        events.append('BEGIN')
        try:yield
        except BaseException:
            events.append('ROLLBACK');raise
    original=RuntimeError('SECRET');original.sqlstate=state
    with h.rejected(SimpleNamespace(begin_nested=savepoint)):
        raise IntegrityError('SECRET SQL',{'secret':'SECRET'},original)
    assert events==['BEGIN','ROLLBACK']


def test_unrecognized_driver_constraint_and_sqlstate_are_redacted():
    original=SimpleNamespace(sqlstate='SECRET_HOST',diag=SimpleNamespace(constraint_name='password=SECRET'))
    log=EvidenceLog();log.record('CONTRACT_OPERATION',operation='CONTRACTS_VALID_ATTEMPT',source='SUPERVISOR')
    fields=h.postgres_failure_fields(SimpleNamespace(orig=original),log)
    assert fields=={'sqlstate':None,'constraint':'UNRECOGNIZED_CONSTRAINT','operation':'CONTRACTS_VALID_ATTEMPT'}
    assert 'SECRET' not in json.dumps(fields)



def test_worker_reports_server_pid_not_proxy_cancellation_identifier(monkeypatch,tmp_path):
    callbacks={};sent=[];queries=[]
    engine=SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(h,'engine_for',lambda *args:engine)
    monkeypatch.setattr(h,'worker_bootstrap',lambda e:None)
    def listener(engine,name):
        def install(fn):callbacks[name]=fn;return fn
        return install
    monkeypatch.setattr(h.event,'listens_for',listener)
    # Reproduce retained RACE evidence: libpq exposes a negative proxy identifier.
    connection=SimpleNamespace(connection=SimpleNamespace(dbapi_connection=SimpleNamespace(
        info=SimpleNamespace(backend_pid=-1391619678))))
    def execute(query):
        queries.append(str(query))
        # Exercise the nested event: PID observation must not recurse into LOCK.
        callbacks['before_cursor_execute'](connection,None,str(query),None,None,False)
        return SimpleNamespace(scalar_one=lambda:24680)
    connection.execute=execute
    def cycle(*args,**kwargs):
        callbacks['before_cursor_execute'](connection,None,
            'SELECT id FROM trading_mandates FOR UPDATE',None,None,False)
        return SimpleNamespace(state='COMPLETED',outcome_code='PAPER_EXECUTED',persisted=True,reused=False)
    monkeypatch.setattr(h,'cycle_once',cycle)
    h.worker('unread','unread','private',{'timeline':h.ScenarioTimeline()},'normal',
        SimpleNamespace(send=sent.append,close=lambda:None),None,
        start=SimpleNamespace(wait=lambda timeout:True),events_path=str(tmp_path/'worker.jsonl'))
    assert queries==['SELECT pg_backend_pid()']
    assert [value for kind,value in sent if kind=='LOCK']==[24680]
    assert '-1391619678' not in (tmp_path/'worker.jsonl').read_text()
    assert any(kind=='RESULT' for kind,value in sent)
    engine.dispose.assert_called_once()
