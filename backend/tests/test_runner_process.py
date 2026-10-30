"""Offline process/discovery/configuration tests; no database or provider I/O."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID,uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from app.mandate_runner.config import RunnerSettings
from app.mandate_runner.cycle import CycleResult
from app.mandate_runner import process as p, runtime

AT=datetime(2026,10,27,12,1,tzinfo=timezone.utc)
URL='postgresql+psycopg://test_guard:test_guard@127.0.0.1:1/blocked_test_database'

def settings(**kw):return RunnerSettings(_env_file=None,database_url=URL,**kw)

@pytest.fixture
def h(monkeypatch):
    h=SimpleNamespace(now=AT,records={},barriers=set(),calls=[],events=[],candidates=[p.Candidate(UUID(int=11),UUID(int=1)),p.Candidate(UUID(int=22),UUID(int=2))])
    h.stop=Event();h.config=settings(paper_runner_enabled=True,paper_runner_page_size=1)
    def page(factory,after,size):
        return tuple(c for c in h.candidates if after is None or c.mandate_id>after)[:size]
    monkeypatch.setattr(p,'discover_page',page)
    def due(factory,c,at,*,reasons=None):
        opened,_,until=p.expected_candle(at)
        if at>=until or c.mandate_id in h.barriers:return False
        row=h.records.get((c.mandate_id,opened))
        return row is None or row.state=='RETRY_WAIT'
    monkeypatch.setattr(p,'due',due)
    def execute(factory,mid,**kw):
        h.calls.append(mid);at=kw['clock']();key=(mid,p.expected_candle(at)[0])
        if key not in h.records:
            h.records[key]=CycleResult(mid,'COMPLETED','NO_TRADE','EVALUATION_COMPLETED',uuid4(),True)
        return h.records[key]
    h.execute=execute
    h.make=lambda:p.PaperRunner(None,h.config,h.stop,lambda event,**kw:h.events.append((event,kw)),clock=lambda:h.now,processor=h.execute)
    h.runner=h.make();return h

def test_disabled_defaults_and_no_implicit_dotenv():
    s=settings()
    assert not s.paper_runner_enabled and RunnerSettings.model_config['env_file'] is None
    assert 'test_guard' not in repr(s)

@pytest.mark.parametrize('changes',[dict(paper_runner_poll_seconds=0),dict(paper_runner_poll_seconds=301),
    dict(paper_runner_page_size=0),dict(paper_runner_connect_timeout_seconds=31),
    dict(paper_runner_network_timeout_seconds=31),dict(paper_runner_lock_timeout_ms=15000),
    dict(paper_runner_statement_timeout_ms=1000,paper_runner_lock_timeout_ms=1000)])
def test_configuration_bounds(changes):
    with pytest.raises(ValidationError):settings(**changes)

def test_enabled_requires_explicit_psycopg_target(monkeypatch):
    monkeypatch.delenv('TRADERAIL_DATABASE_URL',raising=False)
    with pytest.raises(ValidationError):RunnerSettings(_env_file=None,paper_runner_enabled=True)
    with pytest.raises(ValidationError):RunnerSettings(_env_file=None,paper_runner_enabled=True,database_url='postgresql://u:p@localhost/db')

def test_enable_env_switch(monkeypatch):
    monkeypatch.setenv('TRADERAIL_PAPER_RUNNER_ENABLED','true')
    assert settings().paper_runner_enabled

def test_two_eligible_mandates_processed_independently(h):
    assert h.runner.poll_once()==2
    assert h.calls==[c.mandate_id for c in h.candidates]
    assert len(h.records)==2

def test_completed_no_trade_hold_and_rejection_not_retried(h):
    h.runner.poll_once()
    for key,row in list(h.records.items()):
        h.records[key]=replace(row,outcome_code='POSITION_HELD')
    assert h.runner.poll_once()==0 and len(h.calls)==2

def test_restart_uses_durable_state_not_memory(h):
    h.runner.poll_once();fresh=h.make()
    assert fresh.poll_once()==0 and len(h.calls)==2

def test_downtime_only_latest_candle_is_considered(h):
    h.now+=timedelta(days=4)
    assert h.runner.poll_once()==2
    assert {key[1] for key in h.records}=={p.expected_candle(h.now)[0]}

def test_outside_freshness_window_never_discovers_or_trades(h,monkeypatch):
    h.now=AT.replace(minute=10)
    discovery=Mock(side_effect=AssertionError('No stale catch-up'))
    monkeypatch.setattr(p,'discover_page',discovery)
    assert h.runner.poll_once()==0
    discovery.assert_not_called()

def test_one_failure_does_not_stop_another(h):
    normal=h.execute
    def fail(factory,mid,**kw):
        if mid==h.candidates[0].mandate_id:raise RuntimeError('private host and password')
        return normal(factory,mid,**kw)
    h.runner.processor=fail
    assert h.runner.poll_once()==1
    assert h.candidates[0].mandate_id in h.runner.suppressed
    assert h.candidates[1].mandate_id in h.calls
    assert 'password' not in repr(h.events)

def test_persisted_reconciliation_and_closed_barriers(h):
    h.barriers={c.mandate_id for c in h.candidates}
    assert h.runner.poll_once()==0 and h.calls==[]

def test_unpersisted_reconciliation_never_blindly_retries(h,monkeypatch):
    recovery=Mock(return_value='QUARANTINED');monkeypatch.setattr(p,'recover_uncertain',recovery)
    h.runner.processor=lambda factory,mid,**kw:CycleResult(mid,'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE','VERIFY_DURABLE_STATE_BEFORE_RETRY')
    assert h.runner.poll_once()==2
    assert h.runner.poll_once()==0 and recovery.call_count==2

def test_recovery_failure_suppresses_only_affected_mandate(h,monkeypatch):
    monkeypatch.setattr(p,'recover_uncertain',Mock(side_effect=SQLAlchemyError('private host')))
    first=h.candidates[0].mandate_id;normal=h.execute
    h.runner.processor=lambda factory,mid,**kw:CycleResult(mid,'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE','VERIFY_DURABLE_STATE_BEFORE_RETRY') if mid==first else normal(factory,mid,**kw)
    assert h.runner.poll_once()==2
    assert first in h.runner.suppressed
    assert h.candidates[1].mandate_id in h.calls

def test_shutdown_between_mandates_stops_new_work(h):
    normal=h.execute
    def stop(factory,mid,**kw):
        result=normal(factory,mid,**kw);h.stop.set();return result
    h.runner.processor=stop
    assert h.runner.poll_once()==1 and len(h.calls)==1

def test_cancellation_unwinds_instead_of_becoming_reconciliation(h):
    def stop(factory,mid,**kw):
        h.stop.set();kw['clock']()
    h.runner.processor=stop
    with pytest.raises(p.RunnerStopping):h.runner.poll_once()
    assert not h.runner.suppressed

def test_wait_is_interruptible_and_disabled_serve_never_polls(h):
    h.runner.settings=settings()
    h.runner.poll_once=Mock(side_effect=AssertionError('Disabled'))
    h.runner.serve();h.runner.poll_once.assert_not_called()
    assert h.events[-1][0]=='RUNNER_DISABLED'

def test_serve_uses_event_wait_not_busy_loop(h):
    h.runner.poll_once=Mock()
    h.runner.stop=SimpleNamespace(is_set=lambda:False,wait=Mock(side_effect=p.RunnerStopping()))
    h.runner.serve()
    h.runner.stop.wait.assert_called_once_with(h.config.paper_runner_poll_seconds)
    assert h.events[-1][0]=='RUNNER_STOPPED'

def test_discovery_failure_logs_and_returns(h,monkeypatch):
    monkeypatch.setattr(p,'discover_page',Mock(side_effect=SQLAlchemyError('private connection')))
    assert h.runner.poll_once()==0
    assert any(event=='DISCOVERY_FAILED' for event,fields in h.events)

def test_engine_timeouts_are_explicit(monkeypatch):
    factory=Mock(return_value=object());monkeypatch.setattr(runtime,'create_engine',factory)
    hooks=[]
    def listen(engine,name):
        assert engine is factory.return_value and name=='begin'
        def register(fn):hooks.append(fn);return fn
        return register
    monkeypatch.setattr(runtime.event,'listens_for',listen)
    configured=Mock();monkeypatch.setattr(runtime,'_configure_timeouts',configured)
    config=settings();assert runtime.make_engine(config) is factory.return_value
    kw=factory.call_args.kwargs
    assert kw['isolation_level']=='READ COMMITTED' and kw['hide_parameters'] is True
    assert kw['connect_args']=={'connect_timeout':5}
    assert kw['poolclass'] is runtime.NullPool
    first,second=object(),object()
    # Each transaction may be assigned a different backend by the pooler.
    hooks[0](SimpleNamespace(connection=SimpleNamespace(dbapi_connection=first)))
    hooks[0](SimpleNamespace(connection=SimpleNamespace(dbapi_connection=second)))
    assert configured.call_args_list==[ ((first,config),), ((second,config),) ]


@pytest.mark.parametrize('limits',[(15000,3000,30000),(30000,10000,60000)])
def test_timeout_setup_verified_in_transaction_before_use(limits):
    events=[]
    class Connection:
        autocommit=False
        def cursor(self):return Cursor(self)
        def close(self):events.append('CLOSE')
    class Cursor:
        def __init__(self,connection):self.connection=connection
        def __enter__(self):return self
        def __exit__(self,*args):events.append('CURSOR_CLOSED')
        def execute(self,sql,params):
            assert self.connection.autocommit is False
            events.append((sql,params))
        def fetchone(self):return (True,True,True)
    connection=Connection()
    runtime._configure_timeouts(connection,settings(
        paper_runner_statement_timeout_ms=limits[0],paper_runner_lock_timeout_ms=limits[1],
        paper_runner_idle_transaction_timeout_ms=limits[2]))
    assert connection.autocommit is False
    assert events[0][1]==tuple(str(value)+'ms' for value in limits)
    assert events[0][0].count('%s, true)')==3
    assert 'false)' not in events[0][0]
    assert events[1][1]==limits and 'current_setting' in events[1][0]
    assert events[-1]=='CURSOR_CLOSED' and 'CLOSE' not in events


@pytest.mark.parametrize('failure',['SET','VERIFY','MISMATCH','AUTOCOMMIT'])
def test_timeout_initialization_failure_discards_connection(failure):
    closed=[]
    class Connection:
        autocommit=failure=='AUTOCOMMIT'
        def cursor(self):return Cursor()
        def close(self):closed.append(True)
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,params):
            if (failure=='SET' and 'set_config' in sql) or (failure=='VERIFY' and 'current_setting' in sql):
                raise RuntimeError('secret host password')
        def fetchone(self):return (True,False,True) if failure=='MISMATCH' else (True,True,True)
    with pytest.raises(RuntimeError,match='^Runner database timeout initialization failed$'):
        runtime._configure_timeouts(Connection(),settings())
    assert closed==[True]


def test_provider_uses_existing_adapter_and_bounded_http(monkeypatch):
    client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
    factory=Mock(return_value=client);monkeypatch.setattr(runtime.httpx,'Client',factory)
    with runtime.provider_factory(settings())() as provider:
        assert isinstance(provider,runtime.BinanceHistoricalProvider)
    kw=factory.call_args.kwargs
    assert kw['trust_env'] is False and kw['follow_redirects'] is False
    assert kw['timeout'].read==8 and kw['timeout'].connect==3

def test_disabled_main_never_constructs_engine(monkeypatch,capsys):
    monkeypatch.setattr(runtime,'RunnerSettings',lambda **kw:settings())
    engine=Mock(side_effect=AssertionError('No database'));monkeypatch.setattr(runtime,'make_engine',engine)
    assert runtime.main([])==0
    assert json.loads(capsys.readouterr().out)['event']=='RUNNER_DISABLED'
    engine.assert_not_called()

def test_startup_error_sanitized(monkeypatch,capsys):
    monkeypatch.setattr(runtime,'RunnerSettings',Mock(side_effect=ValueError('secret password hostname')))
    assert runtime.main([])==2
    output=capsys.readouterr().out
    assert 'secret' not in output and 'hostname' not in output and 'password' not in output
    assert json.loads(output)['event']=='RUNNER_STARTUP_FAILED'

def test_missing_explicit_env_file_fails_closed(monkeypatch,capsys):
    load=Mock();monkeypatch.setattr(runtime,'RunnerSettings',load)
    assert runtime.main(['--env-file','not-a-real-runner-config.env'])==2
    load.assert_not_called()
    assert 'not-a-real-runner-config' not in capsys.readouterr().out

def test_log_allowlist_never_emits_raw_errors_or_connection_data(caplog):
    with caplog.at_level('INFO',logger='traderail.paper_runner'):
        runtime.emit('CYCLE_RESULT',mandate_id=uuid4(),reason_code='postgresql://secret@host',password='private',hostname='private',state='COMPLETED',persisted=True)
    record=json.loads(caplog.records[-1].message)
    assert record['reason_code']=='REDACTED' and record['persisted'] is True
    assert 'password' not in record and 'hostname' not in record

def test_signal_handlers_request_stop_and_restore(monkeypatch):
    handlers={};calls=[]
    def signal(sig,fn):
        old=handlers.get(sig,'previous');handlers[sig]=fn;calls.append((sig,fn));return old
    monkeypatch.setattr(runtime.signal,'signal',signal)
    stop=Event()
    with runtime.shutdown_signals(stop):
        handlers[runtime.signal.SIGTERM](None,None)
        assert stop.is_set()
    assert all(value=='previous' for value in handlers.values())

@pytest.mark.parametrize('revision,present,ok',[('0013_trading_mandates',True,False),('0014_paper_mandate_work',False,False),('0014_paper_mandate_work',True,False),('0015_paper_work_code_checks',False,False),('0015_paper_work_code_checks',True,True)])
def test_schema_preflight_readonly(revision,present,ok):
    statements=[]
    class Connection:
        dialect=SimpleNamespace(name='postgresql')
        def execute(self,q):
            statements.append(str(q))
            return SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[revision]),scalar_one=lambda:present)
    @contextmanager
    def connection():yield Connection()
    engine=SimpleNamespace(connect=connection)
    if ok:runtime.verify_schema(engine)
    else:
        with pytest.raises(RuntimeError):runtime.verify_schema(engine)
    assert all(sql.startswith('SELECT ') for sql in statements)

def test_no_fastapi_startup_hook_or_trading_duplication():
    main=Path(__file__).parents[1]/'app/main.py'
    assert 'mandate_runner' not in main.read_text()
    source=Path(p.__file__).read_text()
    for token in ('execute_paper(', 'evaluate_strategy(', 'evaluate_and_register(', 'uvicorn','threading.Thread'):
        assert token not in source


def test_readonly_admission_failure_can_recover_without_blocking_others(h,monkeypatch):
    original=p.due
    first=h.candidates[0].mandate_id;failed=[False]
    def admission(factory,candidate,at,**kwargs):
        if candidate.mandate_id==first and not failed[0]:
            failed[0]=True;raise SQLAlchemyError('private connection')
        return original(factory,candidate,at)
    monkeypatch.setattr(p,'due',admission)
    assert h.runner.poll_once()==1 and not h.runner.suppressed
    assert h.runner.poll_once()==1 and len(h.calls)==2

def test_enabled_main_owns_engine_and_disposes_without_fastapi(monkeypatch):
    config=settings(paper_runner_enabled=True)
    engine=Mock();schema=Mock();serve=Mock()
    monkeypatch.setattr(runtime,'RunnerSettings',lambda **kw:config)
    monkeypatch.setattr(runtime,'make_engine',lambda s:engine)
    monkeypatch.setattr(runtime,'verify_schema',schema)
    monkeypatch.setattr(runtime,'sessionmaker',lambda **kw:'isolated-factory')
    runner=Mock(return_value=SimpleNamespace(serve=serve));monkeypatch.setattr(runtime,'PaperRunner',runner)
    @contextmanager
    def signals(stop):yield
    monkeypatch.setattr(runtime,'shutdown_signals',signals)
    assert runtime.main([])==0
    schema.assert_called_once_with(engine);serve.assert_called_once()
    engine.dispose.assert_called_once()
    assert runner.call_args.args[0]=='isolated-factory'

def test_help_does_not_load_settings_or_engine(monkeypatch,capsys):
    load=Mock(side_effect=AssertionError('Help must be offline'));monkeypatch.setattr(runtime,'RunnerSettings',load)
    with pytest.raises(SystemExit) as error:runtime.main(['--help'])
    assert error.value.code==0 and 'PAPER mandate runner' in capsys.readouterr().out
    load.assert_not_called()


def test_schema_failure_never_starts_loop_and_disposes(monkeypatch,capsys):
    config=settings(paper_runner_enabled=True);engine=Mock()
    monkeypatch.setattr(runtime,'RunnerSettings',lambda **kw:config)
    monkeypatch.setattr(runtime,'make_engine',lambda s:engine)
    monkeypatch.setattr(runtime,'verify_schema',Mock(side_effect=RuntimeError('private endpoint')))
    loop=Mock(side_effect=AssertionError('No process without schema'))
    monkeypatch.setattr(runtime,'PaperRunner',loop)
    assert runtime.main([])==2
    loop.assert_not_called();engine.dispose.assert_called_once()
    assert 'private' not in capsys.readouterr().out


@pytest.mark.parametrize('initialization_failure',[False,True])
def test_real_sqlalchemy_begin_orders_configuration_before_transaction_body(monkeypatch,initialization_failure):
    # Exercise real SQLAlchemy transaction events with an explicitly injected
    # DBAPI double. No driver connection (including SQLite) is opened.
    from sqlalchemy import create_engine
    from sqlalchemy.engine import Connection
    engine=create_engine(URL,poolclass=runtime.NullPool)
    events=[]
    monkeypatch.setattr(runtime,'create_engine',lambda *args,**kwargs:engine)
    def initialize(connection,config):
        events.append('CONFIGURE')
        if initialization_failure:raise RuntimeError('configuration failed')
    monkeypatch.setattr(runtime,'_configure_timeouts',initialize)
    runtime.make_engine(settings())
    injected=SimpleNamespace(dbapi_connection=object(),is_valid=True,
        commit=Mock(),rollback=Mock(),close=Mock())
    try:
        with Connection(engine,connection=injected) as c:
            if initialization_failure:
                with pytest.raises(RuntimeError,match='configuration failed'):
                    with c.begin():events.append('APPLICATION_BODY')
                assert events==['CONFIGURE']
            else:
                for _ in range(2):
                    with c.begin():events.append('APPLICATION_BODY')
                assert events==['CONFIGURE','APPLICATION_BODY','CONFIGURE','APPLICATION_BODY']
    finally:engine.dispose()



def test_idle_heartbeat_uses_monotonic_sixty_seconds_without_changing_polling(h,monkeypatch):
    ticks=[0.0];monkeypatch.setattr(p,'monotonic',lambda:ticks[0])
    h.now=h.now.replace(minute=20)
    for tick in (0,15,30,45,60,75,90,105,120):
        ticks[0]=tick;assert h.runner.poll_once()==0
    assert [event for event,fields in h.events].count('RUNNER_HEARTBEAT')==3
    assert [event for event,fields in h.events].count('POLL_STATUS')==1
    assert h.events[0][1]['reason_code']=='OUTSIDE_CANDLE_FRESHNESS_WINDOW'
    assert h.calls==[] and h.config.paper_runner_poll_seconds==15


@pytest.mark.parametrize('reason,expected',[('INELIGIBLE','NO_ELIGIBLE_MANDATES'),
    ('ALREADY_PROCESSED','ALREADY_PROCESSED'),('RETRY_WAIT','RETRY_WAIT'),
    ('RECONCILIATION_OR_TERMINAL_BARRIER','RECONCILIATION_OR_TERMINAL_BARRIER')])
def test_poll_reports_actual_admission_reason_without_extra_work(h,monkeypatch,reason,expected):
    def admission(factory,candidate,at,*,reasons=None):
        reasons.append(reason);return False
    monkeypatch.setattr(p,'due',admission)
    assert h.runner.poll_once()==0 and h.calls==[]
    assert next(fields['reason_code'] for event,fields in h.events if event=='POLL_STATUS')==expected


def test_no_active_mandates_is_reported_once_not_every_poll(h):
    h.candidates=[]
    h.runner.poll_once();h.runner.poll_once()
    statuses=[fields for event,fields in h.events if event=='POLL_STATUS']
    assert statuses==[{'reason_code':'NO_ELIGIBLE_MANDATES'}]


def test_evaluation_status_changes_are_immediate_and_no_trade_is_completed(h,monkeypatch):
    h.runner.poll_once()
    assert ('POLL_STATUS',{'reason_code':'EVALUATION_COMPLETED'}) in h.events
    def done(factory,candidate,at,*,reasons=None):
        reasons.append('ALREADY_PROCESSED');return False
    monkeypatch.setattr(p,'due',done)
    h.runner.poll_once();h.runner.poll_once()
    statuses=[fields['reason_code'] for event,fields in h.events if event=='POLL_STATUS']
    assert statuses==['EVALUATION_COMPLETED','ALREADY_PROCESSED']
    assert len(h.calls)==2


def test_logging_failure_cannot_suppress_mandate_or_duplicate_completed_evaluation(h):
    h.runner._emit=Mock(side_effect=OSError('secret logging failure'))
    assert h.runner.poll_once()==2
    assert h.runner.poll_once()==0
    assert not h.runner.suppressed and len(h.calls)==2


def test_logging_failure_does_not_skip_uncertain_recovery(h,monkeypatch):
    h.runner._emit=Mock(side_effect=OSError('secret logging failure'))
    recovery=Mock(return_value='QUARANTINED');monkeypatch.setattr(p,'recover_uncertain',recovery)
    h.runner.processor=lambda *args,**kwargs:CycleResult(h.candidates[0].mandate_id,
        'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE','VERIFY_DURABLE_STATE_BEFORE_RETRY')
    assert h.runner.poll_once()==2
    assert recovery.call_count==2 and len(h.runner.suppressed)==2
    assert h.runner.poll_once()==0 and recovery.call_count==2


def test_heartbeat_timestamp_is_utc_and_metadata_is_allowlisted(caplog):
    with caplog.at_level('INFO',logger='traderail.paper_runner'):
        runtime.emit('RUNNER_HEARTBEAT',reason_code='NO_ELIGIBLE_MANDATES',password='SECRET')
    record=json.loads(caplog.records[-1].message)
    assert datetime.fromisoformat(record['timestamp']).utcoffset()==timedelta(0)
    assert set(record)=={'event','timestamp','reason_code'}
    assert 'SECRET' not in json.dumps(record)


@pytest.mark.parametrize('state,persisted,expected',[
    ('RECONCILIATION_REQUIRED',True,'RECONCILIATION_REQUIRED'),
    ('SKIPPED',True,'EVALUATION_SKIPPED'),('COMPLETED',False,'EVALUATION_PENDING')])
def test_poll_never_labels_unconfirmed_or_skipped_work_as_completed(h,state,persisted,expected):
    h.runner.processor=lambda factory,mid,**kwargs:CycleResult(mid,state,'TEST_RESULT','TEST_REASON',
        uuid4(),persisted)
    h.runner.poll_once()
    assert ('POLL_STATUS',{'reason_code':expected}) in h.events
