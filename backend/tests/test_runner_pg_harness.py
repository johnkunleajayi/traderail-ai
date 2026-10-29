"""Offline safety tests only. Never opt out of the test database safety guard."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from integration import runner_postgres as h

URL='postgresql+psycopg://fixture:fixture@ep-fixture.neon.tech/fixture?sslmode=require'

@pytest.fixture
def target_files(tmp_path,monkeypatch):
    backend=tmp_path/'backend';backend.mkdir()
    env=backend/'.env.integration';env.write_text('TRADERAIL_DATABASE_URL='+URL+'\nTRADERAIL_PAPER_RUNNER_ENABLED=false\n')
    work=tmp_path/'work';work.mkdir()
    approval=work/'approval.json'
    data={'disposable':True,'operator_confirmed_child':True,'child_branch_id':'br-child-fixture',
        'parent_branch_id':'br-parent-fixture','target_sha256':h.fingerprint(make_url(URL))}
    approval.write_text(json.dumps(data));monkeypatch.setattr(h,'BACKEND',backend);monkeypatch.setattr(h,'ROOT',tmp_path)
    return SimpleNamespace(env=env,approval=approval,data=data)

def test_plan_never_loads_secrets_or_constructs_engine(monkeypatch,capsys):
    load=Mock(side_effect=AssertionError('No secret access'));engine=Mock(side_effect=AssertionError('No connection'))
    monkeypatch.setattr(h,'dotenv_values',load);monkeypatch.setattr(h,'create_engine',engine)
    assert h.main(['--plan'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['connection'] is False and report['production_skip_locked'] is False
    load.assert_not_called();engine.assert_not_called()

def test_default_is_offline_plan(monkeypatch,capsys):
    monkeypatch.setattr(h,'create_engine',Mock(side_effect=AssertionError('No connection')))
    assert h.main([])==0 and json.loads(capsys.readouterr().out)['status']=='OFFLINE_PLAN_ONLY'

def test_target_only_explicit_integration_file(target_files,monkeypatch):
    monkeypatch.setenv('TRADERAIL_DATABASE_URL','postgresql://production-must-never-be-used')
    url=h.target(target_files.env,target_files.approval)
    assert url.host=='ep-fixture.neon.tech' and url.drivername=='postgresql+psycopg'
    with pytest.raises(h.Refused):h.target(target_files.env.parent/'.env',target_files.approval)

@pytest.mark.parametrize('change',[{'disposable':False},{'operator_confirmed_child':False},
    {'child_branch_id':'br-parent-fixture'},{'target_sha256':'0'*64},{'child_branch_id':'not-a-branch'}])
def test_target_approval_fail_closed(target_files,change):
    data={**target_files.data,**change};target_files.approval.write_text(json.dumps(data))
    with pytest.raises(h.Refused):h.target(target_files.env,target_files.approval)

@pytest.mark.parametrize('url',[URL.replace('ep-fixture.','ep-fixture-pooler.'),
    URL.replace('.neon.tech','.example.invalid'),URL.replace('sslmode=require','sslmode=disable'),
    URL+'&host=other',URL+'&options=unsafe',URL.replace('postgresql+psycopg','sqlite')])
def test_unsafe_targets_rejected_without_connection(target_files,url):
    target_files.env.write_text('TRADERAIL_DATABASE_URL='+url+'\n')
    with pytest.raises((h.Refused,ArgumentError)):
        h.target(target_files.env,target_files.approval)

def test_runner_must_remain_disabled(target_files):
    target_files.env.write_text('TRADERAIL_DATABASE_URL='+URL+'\nTRADERAIL_PAPER_RUNNER_ENABLED=true\n')
    with pytest.raises(h.Refused,match='DISABLED'):h.target(target_files.env,target_files.approval)

def test_interpolation_and_environment_fallback_never_used(target_files,monkeypatch):
    monkeypatch.setenv('TRADERAIL_DATABASE_URL',URL)
    target_files.env.write_text('TRADERAIL_DATABASE_URL=${TRADERAIL_DATABASE_URL}\n')
    with pytest.raises(ArgumentError):h.target(target_files.env,target_files.approval)

def test_ambiguous_url_aliases_rejected(target_files):
    target_files.env.write_text('TRADERAIL_DATABASE_URL='+URL+'\nDATABASE_URL='+URL.replace('/fixture?','/other?')+'\n')
    with pytest.raises(h.Refused,match='AMBIGUOUS'):h.target(target_files.env,target_files.approval)

def test_engine_limits_and_private_search_path(target_files,monkeypatch):
    factory=Mock(return_value=object());monkeypatch.setattr(h,'create_engine',factory)
    schema='tr_runner_'+'a'*16+'_race'
    h.engine_for(target_files.env,target_files.approval,schema)
    options=factory.call_args.kwargs
    assert options['hide_parameters'] is True and options['isolation_level']=='READ COMMITTED'
    assert 'search_path='+schema+',pg_catalog' in options['connect_args']['options']
    assert options['connect_args']['connect_timeout']==10
    assert 'public' not in options['connect_args']['options']
    with pytest.raises(h.Refused):h.engine_for(target_files.env,target_files.approval,'public; DROP SCHEMA public')

def test_missing_approval_never_constructs_engine(monkeypatch,capsys):
    engine=Mock(side_effect=AssertionError('No target approval'));monkeypatch.setattr(h,'create_engine',engine)
    assert h.main(['--phase','preflight'])==1
    engine.assert_not_called();assert 'APPROVAL_FILE_REQUIRED' in capsys.readouterr().out

def test_no_continuous_runner_or_legacy_verifier_or_dotenv_fallback():
    source=Path(h.__file__).read_text()
    for forbidden in ('app.core.config','get_settings(', 'load_dotenv(', 'verify_current_paper_postgres',
        'PaperRunner(', '.serve(', 'TRUNCATE ', 'DISABLE TRIGGER', 'pg_terminate_backend'):
        assert forbidden not in source
    assert 'contextmanager(no_provider)' in source

def test_migration_loader_uses_operations_not_env_py(monkeypatch):
    seen=[]
    fake=SimpleNamespace(upgrade=lambda:seen.append('upgrade'))
    monkeypatch.setattr(h,'script_directory',lambda:SimpleNamespace(get_revision=lambda rev:SimpleNamespace(module=fake)))
    @contextmanager
    def operations(ctx):yield
    monkeypatch.setattr(h.Operations,'context',operations)
    monkeypatch.setattr(h.MigrationContext,'configure',lambda c:object())
    h.apply(object(),h.HEAD,'upgrade')
    assert seen==['upgrade']

def test_bad_worker_response_fails_without_raw_details():
    pipe=SimpleNamespace(poll=lambda *a:True,recv=lambda:('ERROR',{'secret':'must not be exposed'}))
    with pytest.raises(h.Refused,match='WORKER_FAILED') as error:h.receive(pipe,'RESULT')
    assert 'secret' not in str(error.value)

def test_revision_chain_and_source_coverage():
    script=h.script_directory()
    assert script.get_heads()==["0015_paper_work_code_checks"]
    assert script.get_revision(h.HEAD).down_revision==h.WORK_REV
    assert script.get_revision(h.WORK_REV).down_revision==h.BASE
    assert set(h.CASES)=={'contracts','locks','race','rollback','kill','recovery','barriers','close'}
    source=Path(h.__file__).read_text()
    for token in ('pg_blocking_pids',"get_context('spawn')",'stop_owned(',"mode=='recovery'",
        'POPULATED_DOWNGRADE_CHANGED_HISTORY','RECONCILIATION_BARRIER_BYPASSED','TERMINAL_CLOSE_BARRIER_BYPASSED'):
        assert token in source


def test_approval_outside_ignored_work_refused_before_read(target_files,monkeypatch):
    ordinary=target_files.env.parent/'.env'
    def forbidden(*args,**kwargs):raise AssertionError('Normal env must not be read')
    monkeypatch.setattr(Path,'read_text',forbidden)
    with pytest.raises(h.Refused,match='IGNORED_WORK_JSON'):
        h.approval_path(ordinary)

def test_unconfirmed_example_never_accepted(target_files):
    data=dict(target_files.data,operator_confirmed_child=False)
    target_files.approval.write_text(json.dumps(data))
    with pytest.raises(h.Refused,match='APPROVAL_REQUIRED'):
        h.target(target_files.env,target_files.approval)

def test_receive_records_actual_blocking_holder():
    messages=iter([('LOCK',123),('READY',None)])
    pipe=SimpleNamespace(poll=lambda *args:True,recv=lambda:next(messages))
    observed={}
    assert h.receive(pipe,'READY',observed=observed) is None
    assert observed['LOCK']==123

def test_worker_eof_sanitized():
    def eof():raise EOFError('private data')
    pipe=SimpleNamespace(poll=lambda *args:True,recv=eof)
    with pytest.raises(h.Refused,match='WORKER_CONNECTION_CLOSED') as error:
        h.receive(pipe,'RESULT')
    assert 'private' not in str(error.value)


def test_worker_cleanup_failure_is_explicit_without_raw_errors(monkeypatch):
    from sqlalchemy import event
    engine=Mock();engine.dispose.side_effect=RuntimeError('private endpoint password')
    monkeypatch.setattr(h,'engine_for',lambda *a:engine)
    monkeypatch.setattr(event,'listens_for',lambda *a,**k:lambda fn:fn)
    monkeypatch.setattr(h,'worker_bootstrap',lambda e:None)
    monkeypatch.setattr(h,'cycle_once',lambda *a,**kw:SimpleNamespace(state='COMPLETED',outcome_code='NO_TRADE',persisted=True,reused=False))
    sent=[];pipe=SimpleNamespace(send=sent.append,close=lambda:None)
    with pytest.raises(SystemExit) as error:
        h.worker('ignored','ignored','ignored',{'timeline':h.ScenarioTimeline()},'normal',pipe,None,start=SimpleNamespace(wait=lambda timeout:True))
    assert error.value.code==1
    assert sent[-1][0]=='ERROR' and sent[-1][1]['category']=='WORKER_CLEANUP_FAILED'
    assert 'private' not in repr(sent)

def test_coordinator_cleanup_failure_reported_separately(target_files,monkeypatch,capsys):
    from datetime import datetime,timezone
    engine=Mock();engine.dispose.side_effect=RuntimeError('private endpoint')
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
        get_isolation_level=lambda:'READ COMMITTED',
        execute=lambda *a:SimpleNamespace(scalar_one=lambda:datetime.now(timezone.utc)))
    @contextmanager
    def connect():yield connection
    engine.connect=connect
    monkeypatch.setattr(h,'engine_for',lambda *a:engine)
    monkeypatch.setattr(h,'revision',lambda c:[h.BASE])
    monkeypatch.setattr(h,'require_public_state',Mock())
    monkeypatch.setattr(h,'baseline',lambda e:{})
    monkeypatch.setattr(h,'verify_timeouts',lambda c:None)
    args=['--phase','preflight','--approval',str(target_files.approval),
        '--confirm-child',target_files.data['child_branch_id'],'--artifacts',str(h.ROOT/'work/results')]
    assert h.main(args)==1
    output=capsys.readouterr().out
    assert 'stage=CLEANUP' in output and 'DATABASE_RESOURCE_RELEASE_FAILED' in output
    assert 'private' not in output

def test_harness_does_not_claim_skip_locked_or_ignore_worker_exit():
    source=Path(h.__file__).read_text()
    assert 'with_for_update(skip_locked=True)' not in source
    assert "'holder':holder,'waiter':waiter" in source
    assert "observer_channel.send((holder_events['LOCK'],waiting_pid))" in source
    assert 'a.exitcode==0 and b.exitcode==0' in source


def test_pytest_connection_guard_still_blocks_harness(target_files):
    from conftest import TestDatabaseSafetyError
    engine=h.engine_for(target_files.env,target_files.approval)
    try:
        with pytest.raises(TestDatabaseSafetyError,match='physical database connections are forbidden'):
            engine.connect()
    finally:engine.dispose()

@pytest.mark.parametrize('close_case',[False,True])
def test_fixture_evidence_uses_real_unchanged_strategy(close_case):
    from datetime import datetime,timedelta,timezone
    from uuid import uuid4
    from app.market_intelligence.types import Candle,DatasetContext
    from app.risk.paper_source.market import analyze
    from app.strategies.inputs import PositionState
    from app.strategies.decisions import DecisionType
    now=datetime(2026,10,27,12,1,tzinfo=timezone.utc);iid=uuid4()
    rows=h.fixture_rows(iid,now,close_case=close_case)
    candles=tuple(Candle(**{key:r[key] for key in Candle.__dataclass_fields__}) for r in rows)
    context=DatasetContext(iid,'BTC/USDT','BINANCE','1h')
    entry_at=rows[130]['close_time']+timedelta(minutes=1)
    _,entry,_=analyze(context,candles[31:131],at=entry_at)
    assert entry.decision is DecisionType.ENTER_LONG
    if close_case:
        _,exit,_=analyze(context,candles[-100:],at=now,position_state=PositionState.LONG)
        assert exit.decision is DecisionType.EXIT_LONG
        assert rows[-1]['open_time']==now.replace(hour=11,minute=0)
    else:
        assert rows[130]['open_time']==now.replace(hour=11,minute=0)
        assert all(c.retrieved_at<=now for c in candles[:131])


def test_symlinked_integration_file_refused_before_dotenv_read(target_files,monkeypatch):
    monkeypatch.setattr(Path,'is_symlink',lambda self:True)
    load=Mock(side_effect=AssertionError('Must not load symlink target'))
    monkeypatch.setattr(h,'dotenv_values',load)
    with pytest.raises(h.Refused,match='DIRECT_INTEGRATION_FILE_REQUIRED'):
        h.target(target_files.env,target_files.approval)
    load.assert_not_called()

@pytest.mark.parametrize('mode',['race','kill','recovery'])
def test_coordinator_control_flow_without_process_or_database(monkeypatch,mode):
    processes=[];pipes=[];queries=[];commands=[]
    class Process:
        def __init__(self,**kwargs):
            self.kwargs=kwargs;self.pid=None;self.exitcode=None;self.alive=False;self.terminated=False
            processes.append(self)
        def start(self):self.pid=len(processes);self.alive=True
        def join(self,timeout):
            self.alive=False
            if self.exitcode is None:self.exitcode=0
        def terminate(self):self.terminated=True;self.alive=False;self.exitcode=-15
        def is_alive(self):return self.alive
    class Context:
        def Event(self):return SimpleNamespace(set=lambda:None,is_set=lambda:True)
        def Pipe(self):
            parent=SimpleNamespace(role='a' if not pipes else 'b',close=lambda:None,send=commands.append)
            child=SimpleNamespace(close=lambda:None);pipes.extend((parent,child));return parent,child
    # Attach the fake process constructor without creating an OS process.
    context=Context();context.Process=Process
    monkeypatch.setattr(h.mp,'get_context',lambda name:context)
    def receive(pipe,wanted,timeout=30,observed=None,**kwargs):
        if wanted in ('READY','PRE_COMMIT'):
            if observed is not None:observed['LOCK']=111
            return None
        if wanted=='LOCK':return 222
        if kwargs.get('worker_name')=='OBSERVER':return 'CONTENTION_CONFIRMED'
        if kwargs.get('worker_name','').startswith('INSPECT'):
            return 'ROLLED_BACK' if mode=='kill' and kwargs['worker_name']=='INSPECT1' else 'COMMITTED'
        return dict(state='COMPLETED',outcome='PAPER_EXECUTED',persisted=True,reused=pipe.role=='b')
    monkeypatch.setattr(h,'receive',receive)
    class Connection:
        def execute(self,sql,params):
            queries.append((str(sql),params));return SimpleNamespace(scalar_one=lambda:True)
    @contextmanager
    def connect():yield Connection()
    engine=SimpleNamespace(connect=connect)
    count=Mock();monkeypatch.setattr(h,'expected_counts',count)
    monkeypatch.setattr(h,'counts',lambda engine:{name:0 for name in h.ECONOMIC})
    execute=Mock(return_value=SimpleNamespace(reused=True,state='COMPLETED',outcome_code='PAPER_EXECUTED'))
    monkeypatch.setattr(h,'cycle_once',execute)
    monkeypatch.setattr(h,'inspect_cycle_outcome',Mock(side_effect=['ROLLED_BACK','COMMITTED'] if mode=='kill' else ['COMMITTED','COMMITTED']))
    h.concurrent(engine,{'timeline':h.ScenarioTimeline()},SimpleNamespace(env_file='integration-config',approval='approval'),
        'tr_runner_'+'a'*16+'_race',mode)
    assert all(proc.kwargs['args'][3]['timeline']==h.ScenarioTimeline() for proc in processes)
    assert len(processes)==(4 if mode=='race' else 3)
    assert all(not proc.is_alive() for proc in processes)
    count.assert_called_once_with(engine,1)
    if mode=='race':
        assert not any(proc.terminated for proc in processes)
        assert (111,222) in commands
        execute.assert_not_called()
    else:
        assert processes[0].terminated
        execute.assert_called_once()


@pytest.mark.parametrize('head,present,count,allowed',[
    (h.BASE,None,0,True),(h.BASE,'paper_mandate_work',0,False),
    (h.HEAD,'paper_mandate_work',0,True),(h.HEAD,'paper_mandate_work',1,False),
    (h.HEAD,None,0,False)])
def test_public_work_never_becomes_fixture_storage(monkeypatch,head,present,count,allowed):
    monkeypatch.setattr(h,'revision',lambda c:[head])
    queries=[]
    class Connection:
        def execute(self,statement):
            sql=str(statement);queries.append(sql)
            return SimpleNamespace(scalar_one=lambda:present if 'to_regclass' in sql else count)
    if allowed:h.require_public_state(Connection(),head)
    else:
        with pytest.raises(h.Refused):h.require_public_state(Connection(),head)
    assert all(query.startswith('SELECT ') for query in queries)


def test_progress_is_flushed_and_sanitizes_identifiers(monkeypatch):
    output=Mock();monkeypatch.setattr('builtins.print',output)
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',True)
    h.progress('TABLE_FINGERPRINT',table='market_candles')
    output.assert_called_with('PROGRESS: TABLE_FINGERPRINT; table=market_candles',flush=True)
    h.progress('TABLE_FINGERPRINT',table='private endpoint/password')
    output.assert_called_with('PROGRESS: TABLE_FINGERPRINT; table=REDACTED_IDENTIFIER',flush=True)


@pytest.mark.parametrize('connect,settings,valid',[
    ('10',(True,True,True),True),('0',(True,True,True),False),
    ('10',(False,True,True),False),('10',(True,False,True),False),
    ('10',(True,True,False),False)])
def test_actual_timeout_settings_fail_closed(monkeypatch,capsys,connect,settings,valid):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',True)
    connection=SimpleNamespace(connection=SimpleNamespace(dbapi_connection=SimpleNamespace(
        info=SimpleNamespace(get_parameters=lambda:{'connect_timeout':connect,'password':'never-print'}))),
        execute=lambda q:SimpleNamespace(one=lambda:settings))
    if valid:
        h.verify_timeouts(connection)
        assert 'TIMEOUTS_VERIFIED' in capsys.readouterr().out
    else:
        with pytest.raises(h.Refused):h.verify_timeouts(connection)
        assert 'TIMEOUTS_VERIFIED' not in capsys.readouterr().out


def test_watchdog_exits_only_finite_subprocess_without_database():
    import subprocess,sys
    script=("import sys,time; sys.path.insert(0,'backend'); "
        "from integration import runner_postgres as h; "
        "h.STAGE='CONNECTION_ESTABLISHING'; h.start_preflight_watchdog(.1); time.sleep(10)")
    result=subprocess.run([sys.executable,'-c',script],cwd=h.ROOT,
        capture_output=True,text=True,timeout=15)
    assert result.returncode==1
    assert 'stage=CONNECTION_ESTABLISHING; category=PREFLIGHT_DEADLINE_EXCEEDED' in result.stdout
    assert not result.stderr


def test_cancelled_watchdog_does_not_exit(monkeypatch):
    called=[]
    class Timer:
        def __init__(self,seconds,callback):self.seconds=seconds;self.callback=callback;self.daemon=False
        def start(self):called.append('started')
        def cancel(self):called.append('cancelled')
    monkeypatch.setattr(h.threading,'Timer',Timer)
    timer=h.start_preflight_watchdog()
    assert timer.seconds==180 and timer.daemon
    timer.cancel()
    assert called==['started','cancelled']


def test_preflight_progress_preserves_both_full_baseline_passes():
    source=Path(h.__file__).read_text()
    for stage in ('CONNECTION_ESTABLISHING','REVISION_WORK_TABLE_CHECK','CLOCK_CHECK',
        'TABLE_FINGERPRINT','CATALOG_INSPECTION','BASELINE_ARTIFACT_HANDLING',
        'SECOND_FINGERPRINT_PASS','FINAL_VERIFICATION'):
        assert stage in source
    assert 'before=baseline(engine)' in source
    assert "require(before==baseline(engine),'PUBLIC_BASELINE_CHANGED')" in source
    assert "PREFLIGHT_DIAGNOSTICS=args.phase=='preflight'" in source
    assert 'if watchdog is not None:watchdog.cancel()' in source


def test_preflight_offline_sequence_and_watchdog_cancellation(target_files,monkeypatch,capsys):
    from datetime import datetime,timezone
    timer=Mock();monkeypatch.setattr(h,'start_preflight_watchdog',lambda:timer)
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
        get_isolation_level=lambda:'READ COMMITTED',
        execute=lambda *a:SimpleNamespace(scalar_one=lambda:datetime.now(timezone.utc)))
    @contextmanager
    def connect():yield connection
    engine=SimpleNamespace(connect=connect,dispose=Mock())
    monkeypatch.setattr(h,'engine_for',lambda *a:engine)
    monkeypatch.setattr(h,'verify_timeouts',Mock())
    monkeypatch.setattr(h,'require_public_state',Mock())
    baseline=Mock(return_value={'table':{'rows':1,'sha256':'fixture'}})
    monkeypatch.setattr(h,'baseline',baseline)
    args=['--phase','preflight','--approval',str(target_files.approval),
        '--confirm-child',target_files.data['child_branch_id'],'--artifacts',str(h.ROOT/'work/results')]
    assert h.main(args)==0
    assert baseline.call_count==2
    assert h.verify_timeouts.call_count==2
    timer.cancel.assert_called_once()
    output=capsys.readouterr().out
    stages=['TARGET_APPROVAL','CONNECTION_ESTABLISHING','REVISION_WORK_TABLE_CHECK',
        'CLOCK_CHECK','FIRST_FINGERPRINT_PASS','BASELINE_ARTIFACT_HANDLING',
        'SECOND_FINGERPRINT_PASS','FINAL_CONNECTION_ESTABLISHING','FINAL_VERIFICATION']
    offsets=[output.index('PROGRESS: '+stage) for stage in stages]
    assert offsets==sorted(offsets)
    assert 'PASS: RUNNER POSTGRES PREFLIGHT' in output
    assert 'fixture@' not in output and 'neon.tech' not in output
    assert h.PREFLIGHT_DIAGNOSTICS is False


@pytest.mark.parametrize('message,category',[
    ('connection timeout expired','CONNECTION_TIMEOUT'),
    ('SSL error: certificate verify failed','TLS_FAILURE'),
    ('connection refused','SOCKET_FAILURE'),
    ('unrecognized configuration parameter: fixture','STARTUP_OPTIONS_FAILURE'),
    ('unsupported startup parameter: fixture','STARTUP_OPTIONS_FAILURE'),
    ('opaque failure','UNKNOWN_CONNECTION_FAILURE')])
def test_connection_failure_categories_never_emit_driver_text(message,category,capsys):
    secret=' private-host user=private password=private'
    assert h.connection_failure(RuntimeError(message+secret))==category
    assert h.connection_failure(RuntimeError(message+secret),initialization=True)=='INITIALIZATION_FAILURE'
    assert not capsys.readouterr().out


def test_dbapi_hook_preserves_arguments_and_stage_order(monkeypatch,capsys):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',True)
    callbacks=[]
    monkeypatch.setattr(h.event,'listens_for',lambda engine,name:lambda fn:callbacks.append(fn) or fn)
    h.install_connection_diagnostics(object())
    result=object();connect=Mock(return_value=result)
    args=['private'];params={'connect_timeout':10,'options':'unchanged','password':'private'}
    assert callbacks[0](SimpleNamespace(connect=connect),None,args,params) is result
    connect.assert_called_once_with(*args,**params)
    output=capsys.readouterr().out
    assert output.splitlines()==[
        'PROGRESS: DBAPI_CONNECTION_ESTABLISHING',
        'PROGRESS: DBAPI_CONNECTION_ESTABLISHED',
        'PROGRESS: SQLALCHEMY_CONNECTION_INITIALIZING']
    assert 'private' not in output


def test_dbapi_hook_failure_is_sanitized_and_not_retried(monkeypatch,capsys):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',True)
    callbacks=[]
    monkeypatch.setattr(h.event,'listens_for',lambda engine,name:lambda fn:callbacks.append(fn) or fn)
    h.install_connection_diagnostics(object())
    connect=Mock(side_effect=RuntimeError('connection timeout expired private-host password'))
    with pytest.raises(h.Refused,match='^CONNECTION_TIMEOUT$'):
        callbacks[0](SimpleNamespace(connect=connect),None,[],{})
    connect.assert_called_once()
    output=capsys.readouterr().out
    assert 'DBAPI_CONNECTION_ESTABLISHED' not in output and 'private' not in output


def test_initialization_failure_is_distinct_without_raw_output(monkeypatch):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',True)
    monkeypatch.setattr(h,'STAGE','SQLALCHEMY_CONNECTION_INITIALIZING')
    engine=SimpleNamespace(connect=Mock(side_effect=RuntimeError('private endpoint')))
    with pytest.raises(h.Refused,match='^INITIALIZATION_FAILURE$'):
        with h.diagnostic_connection(engine):pytest.fail('must not yield')


def test_post_connect_query_failure_not_misclassified(monkeypatch):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',True)
    @contextmanager
    def connect():yield object()
    with pytest.raises(ValueError,match='fixture-query'):
        with h.diagnostic_connection(SimpleNamespace(connect=connect)):
            raise ValueError('fixture-query')


def test_diagnostics_not_installed_outside_preflight(target_files,monkeypatch):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',False)
    monkeypatch.setattr(h,'create_engine',Mock(return_value=object()))
    hook=Mock();monkeypatch.setattr(h,'install_connection_diagnostics',hook)
    h.engine_for(target_files.env,target_files.approval)
    hook.assert_not_called()


@pytest.mark.parametrize('wrapped',[False,True])
def test_psycopg_typed_timeout_wins_over_socket_message(wrapped):
    from psycopg.errors import ConnectionTimeout
    from sqlalchemy.exc import OperationalError
    error=ConnectionTimeout('connection refused private endpoint')
    if wrapped:error=OperationalError(None,None,error)
    assert h.connection_failure(error)=='CONNECTION_TIMEOUT'


@pytest.mark.parametrize('error',[
    OSError(__import__('errno').ETIMEDOUT,'private endpoint'),
    RuntimeError('connection failed: operation timed out'),
    RuntimeError('connection refused; timeout expired'),
    TimeoutError('private endpoint'),
])
def test_transport_timeout_not_misclassified_as_socket(error):
    assert h.connection_failure(error)=='CONNECTION_TIMEOUT'


def test_old_five_second_setting_fails_timeout_verification(monkeypatch):
    connection=SimpleNamespace(connection=SimpleNamespace(dbapi_connection=SimpleNamespace(
        info=SimpleNamespace(get_parameters=lambda:{'connect_timeout':'5'}))))
    with pytest.raises(h.Refused,match='CONNECTION_TIMEOUT_NOT_ACTIVE'):
        h.verify_timeouts(connection)


def test_other_harness_deadlines_unchanged(target_files,monkeypatch):
    factory=Mock(return_value=object());monkeypatch.setattr(h,'create_engine',factory)
    h.engine_for(target_files.env,target_files.approval)
    args=factory.call_args.kwargs['connect_args']
    assert args['connect_timeout']==10
    assert 'statement_timeout=30000' in args['options']
    assert 'lock_timeout=10000' in args['options']
    assert 'idle_in_transaction_session_timeout=60000' in args['options']
    assert h.PREFLIGHT_DEADLINE_SECONDS==180



def test_catalog_connection_diagnostics_enabled_without_preflight(target_files,monkeypatch):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',False)
    monkeypatch.setattr(h,'CATALOG_DIAGNOSTICS',True)
    engine=object();monkeypatch.setattr(h,'create_engine',Mock(return_value=engine))
    hook=Mock();monkeypatch.setattr(h,'install_connection_diagnostics',hook)
    callbacks={}
    monkeypatch.setattr(h.event,'listens_for',lambda e,name:lambda fn:callbacks.setdefault(name,fn))
    h.engine_for(target_files.env,target_files.approval)
    hook.assert_called_once_with(engine)
    calls=Mock();monkeypatch.setattr(h,'progress',calls)
    callback=callbacks['before_cursor_execute']
    callback(None,None,'private SQL',{'password':'private'},None,False)
    callback(None,None,'other SQL',{},None,False)
    assert calls.call_args_list==[__import__('unittest.mock',fromlist=['call']).call('CATALOG_SQL_1'),
        __import__('unittest.mock',fromlist=['call']).call('CATALOG_SQL_2')]


def test_catalog_transport_failure_uses_existing_allowlist(monkeypatch,capsys):
    monkeypatch.setattr(h,'PREFLIGHT_DIAGNOSTICS',False)
    monkeypatch.setattr(h,'CATALOG_DIAGNOSTICS',True)
    callbacks=[]
    monkeypatch.setattr(h.event,'listens_for',lambda e,name:lambda fn:callbacks.append(fn) or fn)
    h.install_connection_diagnostics(object())
    dialect=SimpleNamespace(connect=Mock(side_effect=RuntimeError('connection refused private-host')))
    with pytest.raises(h.Refused,match='^SOCKET_FAILURE$'):
        callbacks[0](dialect,None,[],{})
    assert 'DBAPI_CONNECTION_ESTABLISHING' in capsys.readouterr().out


def test_catalog_reflection_keeps_operation_and_result(monkeypatch,capsys):
    monkeypatch.setattr(h,'CATALOG_DIAGNOSTICS',True)
    result=[{'name':'fixture'}];method=Mock(return_value=result)
    inspector=SimpleNamespace(get_columns=method)
    assert h.catalog_reflection(inspector,'get_columns','paper_positions') is result
    method.assert_called_once_with('paper_positions')
    assert 'CATALOG_COLUMNS; table=paper_positions' in capsys.readouterr().out


def test_catalog_diagnostic_stages_preserve_required_checks():
    import inspect
    source=inspect.getsource(h.catalog)
    for stage in ('CATALOG_RUNTIME_SCHEMA_CHECK','CATALOG_INSPECTOR_CREATION',
        'CATALOG_TRIGGER_DEFINITIONS','CATALOG_GUARD_FUNCTION_DEFINITIONS','CATALOG_PARTIAL_OPEN_INDEX'):
        assert stage in source
    for check in ('CHECK_CONSTRAINT_MISSING','PRIMARY_KEY_MISMATCH','FOREIGN_KEY_MISMATCH',
        'COLUMN_SET_MISMATCH','INDEX_MISSING','IMMUTABILITY_TRIGGER_MISSING','GUARD_FUNCTION_MISSING','OPEN_POSITION_INDEX_MISSING'):
        assert check in source


@pytest.mark.parametrize('correction_only',[False,True])
def test_corrected_migration_order_never_downgrades_0015(correction_only):
    steps=h.migration_steps(correction_only=correction_only)
    expected=(('upgrade',h.HEAD,h.HEAD),) if correction_only else (
        ('upgrade',h.WORK_REV,h.WORK_REV),('downgrade',h.WORK_REV,h.BASE),
        ('upgrade',h.WORK_REV,h.WORK_REV),('upgrade',h.HEAD,h.HEAD))
    assert steps==expected and steps[-1][2]=='0015_paper_work_code_checks'
    assert all(not(direction=='downgrade' and module==h.HEAD) for direction,module,new in steps)


@pytest.mark.parametrize('pattern',["^[A-Z][A-Z0-9_]{0,63}$","^[A-Z][A-Z0-9_](0, 63)$",".*"])
def test_catalog_requires_intended_corrected_patterns(pattern):
    rows=[{'name':'ck_paper_work_'+field,'sqltext':field+" IS NULL OR "+field+"::text ~ '"+pattern+"'::text"}
        for field in ('outcome_code','reason_code')]
    if pattern=="^[A-Z][A-Z0-9_]{0,63}$":h.require_corrected_code_checks(rows)
    else:
        with pytest.raises(h.Refused,match='CORRECTED_CODE_CHECK_REQUIRED'):h.require_corrected_code_checks(rows)


@pytest.mark.parametrize('missing',['outcome_code','reason_code'])
def test_catalog_refuses_missing_corrected_constraint(missing):
    rows=[{'name':'ck_paper_work_'+field,'sqltext':field+" IS NULL OR "+field+" ~ '^[A-Z][A-Z0-9_]{0,63}$'"}
        for field in ('outcome_code','reason_code') if field!=missing]
    with pytest.raises(h.Refused,match='CORRECTED_CODE_CHECK_REQUIRED'):h.require_corrected_code_checks(rows)


@pytest.mark.parametrize('correction_only',[False,True])
def test_migration_failure_rolls_back_without_claiming_corrected_revision(monkeypatch,correction_only):
    events=[];current=[h.WORK_REV if correction_only else h.BASE]
    class Connection:
        def execute(self,query,params=None):
            if params:current[0]=params['v']
    @contextmanager
    def transaction():
        original=current[0]
        try:yield Connection()
        except BaseException:
            current[0]=original;events.append('ROLLBACK');raise
    monkeypatch.setattr(h,'baseline',lambda e:{})
    monkeypatch.setattr(h,'require_public_state',lambda c,expected:None)
    monkeypatch.setattr(h,'revision',lambda c:[current[0]])
    def apply(c,module,direction):
        events.append((module,direction))
        if module==h.HEAD:raise RuntimeError('corrective validation failed')
    monkeypatch.setattr(h,'apply',apply)
    from app.mandate_runner import runtime
    verified=Mock();monkeypatch.setattr(runtime,'verify_schema',verified)
    with pytest.raises(RuntimeError,match='corrective validation failed'):
        h.migrate(SimpleNamespace(begin=transaction),correction_only=correction_only)
    assert current[0]==(h.WORK_REV if correction_only else h.BASE)
    assert events[-1]=='ROLLBACK'
    verified.assert_not_called()
