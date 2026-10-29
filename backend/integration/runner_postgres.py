"""Finite disposable-Neon verification only. Default --plan never connects.

No normal dotenv/settings loader, branch creation, continuous runner, HTTP or
automatic cleanup. Destructive phases require out-of-band child-branch approval.
Run from repository root with backend/.venv/Scripts/python.exe.
"""
import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import errno
import hashlib
import json
import multiprocessing as mp
import os
import threading
from pathlib import Path
import re
import sys
import time
from uuid import UUID, uuid4
from dotenv import dotenv_values
from psycopg.errors import ConnectionTimeout
from sqlalchemy import create_engine, inspect, select, text, insert, update, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT=Path(__file__).resolve().parents[2]
BACKEND=ROOT/'backend'
if str(BACKEND) not in sys.path:sys.path.insert(0,str(BACKEND))
from integration.runner_scenario import ScenarioTimeline, ScenarioClock
from integration.runner_clock import measure_clock, validate_real_clock_boundaries
from integration.runner_supervision import (Deadline, WorkerLimits, EvidenceLog, SupervisionError,
    WorkerCleanupError, wait_gate, stop_owned, join_owned)
WORK_REV='0014_paper_mandate_work'
HEAD='0015_paper_work_code_checks'
BASE='0013_trading_mandates'
CASES=('contracts','locks','race','rollback','kill','recovery','barriers','close')
ECONOMIC=('execution_requests','execution_approval_consumptions','execution_orders','execution_fills','paper_positions','paper_accounting_entries')
STAGE='OFFLINE_PLAN'
PREFLIGHT_DIAGNOSTICS=False
CATALOG_DIAGNOSTICS=False
PREFLIGHT_DEADLINE_SECONDS=180

class Refused(RuntimeError):pass

def require(condition,code):
    if not condition:raise Refused(code)

def progress(stage, *, table=None):
    """Only internal stage codes and sanitized schema identifiers may be emitted."""
    global STAGE
    if not (PREFLIGHT_DIAGNOSTICS or CATALOG_DIAGNOSTICS):return
    STAGE=stage
    suffix=''
    if table is not None:
        safe=table if re.fullmatch(r'[a-z_][a-z0-9_]{0,62}',table) else 'REDACTED_IDENTIFIER'
        suffix='; table='+safe
    print('PROGRESS: '+stage+suffix,flush=True)


def start_preflight_watchdog(seconds=PREFLIGHT_DEADLINE_SECONDS):
    # A daemon thread cannot interrupt a blocked driver call. Exit only this
    # finite harness process; never signal a database backend or another process.
    def expired():
        print('STOP: stage='+STAGE+'; category=PREFLIGHT_DEADLINE_EXCEEDED; retain child branch for inspection',flush=True)
        os._exit(1)
    timer=threading.Timer(seconds,expired)
    timer.daemon=True
    timer.start()
    return timer


def connection_failure(error, *, initialization=False):
    """Inspect driver text privately; return only a fixed diagnostic category.

    Message matching is best-effort, not proof of the remote root cause.
    No raw exception, parameters or connection metadata leave this function.
    """
    if initialization:return 'INITIALIZATION_FAILURE'
    original=getattr(error,'orig',error)
    message=str(original).lower()
    if isinstance(original,(ConnectionTimeout,TimeoutError)) or (
        isinstance(original,OSError) and (original.errno==errno.ETIMEDOUT or getattr(original,'winerror',None)==10060)):
        return 'CONNECTION_TIMEOUT'
    if any(token in message for token in ('unrecognized configuration parameter',
        'invalid value for parameter', 'unsupported startup parameter',
        'unsupported startup option', 'invalid command-line argument for server process')):
        return 'STARTUP_OPTIONS_FAILURE'
    if any(token in message for token in ('ssl error','ssl connection','tls error',
        'certificate verify failed','certificate verification failed','ssl handshake','tls handshake')):
        return 'TLS_FAILURE'
    if any(token in message for token in (
        'connection timeout','timed out','timeout expired')):
        return 'CONNECTION_TIMEOUT'
    if isinstance(original,(ConnectionError,OSError)) or any(token in message for token in (
        'connection refused','network is unreachable','no route to host',
        'could not translate host name','failed to resolve host',
        'connection reset by peer','server closed the connection unexpectedly')):
        return 'SOCKET_FAILURE'
    return 'UNKNOWN_CONNECTION_FAILURE'


def install_connection_diagnostics(engine):
    @event.listens_for(engine,'do_connect')
    def connect(dialect,record,cargs,cparams):
        progress('DBAPI_CONNECTION_ESTABLISHING')
        try:
            # Preserve SQLAlchemy's normal dialect connection call and every
            # original argument, including TLS, timeout and startup options.
            connection=dialect.connect(*cargs,**cparams)
        except Exception as error:
            raise Refused(connection_failure(error)) from None
        progress('DBAPI_CONNECTION_ESTABLISHED')
        progress('SQLALCHEMY_CONNECTION_INITIALIZING')
        return connection


@contextmanager
def diagnostic_connection(engine):
    # Catch only acquisition/initialization, never mislabel later query errors.
    try:
        manager=engine.connect()
        connection=manager.__enter__()
    except Exception as error:
        if not (PREFLIGHT_DIAGNOSTICS or CATALOG_DIAGNOSTICS) or isinstance(error,Refused):raise
        raise Refused(connection_failure(error,
            initialization=STAGE=='SQLALCHEMY_CONNECTION_INITIALIZING')) from None
    try:
        if PREFLIGHT_DIAGNOSTICS or CATALOG_DIAGNOSTICS:progress('SQLALCHEMY_CONNECTION_READY')
        yield connection
    except BaseException:
        if not manager.__exit__(*sys.exc_info()):raise
    else:
        manager.__exit__(None,None,None)


def verify_timeouts(c):
    progress('TIMEOUT_SETTINGS_CHECK')
    parameters=c.connection.dbapi_connection.info.get_parameters()
    require(str(parameters.get('connect_timeout'))=='10','CONNECTION_TIMEOUT_NOT_ACTIVE')
    settings=c.execute(text("SELECT current_setting('statement_timeout')::interval = interval '30 seconds', current_setting('lock_timeout')::interval = interval '10 seconds', current_setting('idle_in_transaction_session_timeout')::interval = interval '60 seconds'")).one()
    require(tuple(settings)==(True,True,True),'SESSION_TIMEOUTS_NOT_ACTIVE')
    print('PROGRESS: TIMEOUTS_VERIFIED; connect=10s; statement=30s; lock=10s; idle_transaction=60s',flush=True)


def fingerprint(url):
    return hashlib.sha256(f'{url.host.lower()}:{url.port or 5432}/{url.database}'.encode()).hexdigest()

def approval_path(value):
    path=Path(value).resolve()
    require(path.is_relative_to((ROOT/'work').resolve()) and path.suffix=='.json','APPROVAL_MUST_BE_IGNORED_WORK_JSON')
    require(path.is_file(),'APPROVAL_FILE_REQUIRED')
    return path


def target(env_file,approval_file):
    require(Path(env_file).resolve()==(BACKEND/'.env.integration').resolve(),'INTEGRATION_FILE_REQUIRED')
    require(not Path(env_file).is_symlink() and not (BACKEND/'.env.integration').is_symlink(), 'DIRECT_INTEGRATION_FILE_REQUIRED')
    require(Path(env_file).is_file() and Path(approval_file).is_file(),'EXPLICIT_FILES_REQUIRED')
    data=dotenv_values(env_file,interpolate=False)
    a,b=data.get('TRADERAIL_DATABASE_URL'),data.get('DATABASE_URL')
    require(not(a and b and a!=b),'AMBIGUOUS_INTEGRATION_URL')
    require(bool(a or b),'INTEGRATION_URL_REQUIRED')
    url=make_url(a or b)
    require(url.drivername in ('postgresql','postgresql+psycopg'),'POSTGRESQL_REQUIRED')
    require(bool(url.host and url.host.endswith('.neon.tech')) and '-pooler' not in url.host,'DIRECT_NEON_REQUIRED')
    require(bool(url.username and url.password and url.database),'COMPLETE_TARGET_REQUIRED')
    require(set(url.query)<= {'sslmode','channel_binding'} and url.query.get('sslmode') in ('require','verify-full'),'TLS_REQUIRED')
    approval=json.loads(approval_path(approval_file).read_text())
    require(approval.get('disposable') is True and approval.get('operator_confirmed_child') is True,'DISPOSABLE_CHILD_APPROVAL_REQUIRED')
    child,parent=approval.get('child_branch_id',''),approval.get('parent_branch_id','')
    require(bool(re.fullmatch(r'br-[a-zA-Z0-9-]+',child)) and bool(re.fullmatch(r'br-[a-zA-Z0-9-]+',parent)) and child!=parent,'DISTINCT_CHILD_REQUIRED')
    require(approval.get('target_sha256')==fingerprint(url),'TARGET_BINDING_MISMATCH')
    require(data.get('TRADERAIL_PAPER_RUNNER_ENABLED','false').lower() in ('false','0','no','off'),'RUNNER_MUST_STAY_DISABLED')
    return url.set(drivername='postgresql+psycopg')

def engine_for(env_file,approval_file,schema='public'):
    require(schema=='public' or bool(re.fullmatch(r'tr_runner_[0-9a-f]{16}_[a-z]+',schema)),'INVALID_FIXTURE_SCHEMA')
    engine=create_engine(target(env_file,approval_file),poolclass=NullPool,hide_parameters=True,echo=False,
        isolation_level='READ COMMITTED',connect_args={'connect_timeout':10,
        'options':f'-c search_path={schema},pg_catalog -c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000'})
    if PREFLIGHT_DIAGNOSTICS or CATALOG_DIAGNOSTICS:install_connection_diagnostics(engine)
    if CATALOG_DIAGNOSTICS:
        query_number=0
        @event.listens_for(engine,'before_cursor_execute')
        def query_started(c,cursor,statement,parameters,context,many):
            nonlocal query_number
            query_number+=1
            progress('CATALOG_SQL_'+str(query_number))
    return engine

def revision(c):return c.execute(text('SELECT version_num FROM alembic_version')).scalars().all()


def require_public_state(c,expected):
    require(revision(c)==[expected],'UNEXPECTED_PUBLIC_REVISION')
    present=c.execute(text("SELECT to_regclass('public.paper_mandate_work')")).scalar_one()
    if expected==BASE:
        require(present is None,'WORK_TABLE_MUST_BE_ABSENT')
    else:
        require(present is not None,'WORK_TABLE_REQUIRED')
        require(c.execute(text('SELECT count(*) FROM public.paper_mandate_work')).scalar_one()==0,'PUBLIC_WORK_MUST_REMAIN_EMPTY')

def baseline(engine):
    progress('BASELINE_CONNECTION_ESTABLISHING')
    with diagnostic_connection(engine) as c:
        progress('BASELINE_CONNECTION_ESTABLISHED')
        if PREFLIGHT_DIAGNOSTICS:verify_timeouts(c)
        names=c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
        result={}
        for name in names:
            if name in ('alembic_version','paper_mandate_work'):continue
            progress('TABLE_FINGERPRINT',table=name)
            quoted=c.dialect.identifier_preparer.quote(name)
            digest=hashlib.sha256();count=0
            for value in c.execute(text(f'SELECT to_jsonb(t)::text FROM public.{quoted} t ORDER BY to_jsonb(t)::text')).scalars():
                digest.update(value.encode());digest.update(b'\n');count+=1
            result[name]={'rows':count,'sha256':digest.hexdigest()}
        progress('CATALOG_INSPECTION')
        schema_queries=(
            "SELECT cl.relname,a.attname,format_type(a.atttypid,a.atttypmod) typ,a.attnotnull,pg_get_expr(d.adbin,d.adrelid) def FROM pg_attribute a JOIN pg_class cl ON cl.oid=a.attrelid JOIN pg_namespace n ON n.oid=cl.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='public' AND cl.relkind IN ('r','p') AND cl.relname NOT IN ('alembic_version','paper_mandate_work') AND a.attnum>0 AND NOT a.attisdropped ORDER BY cl.relname,a.attnum",
            "SELECT cl.relname,k.conname,pg_get_constraintdef(k.oid) def FROM pg_constraint k JOIN pg_class cl ON cl.oid=k.conrelid JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='public' AND cl.relname NOT IN ('alembic_version','paper_mandate_work') ORDER BY cl.relname,k.conname",
            "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename NOT IN ('alembic_version','paper_mandate_work') ORDER BY tablename,indexname",
            "SELECT cl.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) def FROM pg_trigger t JOIN pg_class cl ON cl.oid=t.tgrelid JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='public' AND cl.relname<>'paper_mandate_work' AND NOT t.tgisinternal ORDER BY cl.relname,t.tgname",
            "SELECT p.proname,pg_get_functiondef(p.oid) def FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND p.proname<>'traderail_paper_work_guard' ORDER BY p.proname,p.oid")
        shapes=[]
        for number,q in enumerate(schema_queries,1):
            progress('CATALOG_QUERY_'+str(number))
            shapes.append([dict(r) for r in c.execute(text(q)).mappings()])
        result['__unrelated_schema__']={'sha256':hashlib.sha256(json.dumps(shapes,sort_keys=True,default=str).encode()).hexdigest()}
        return result

def script_directory():
    cfg=Config();cfg.set_main_option('script_location',str(BACKEND/'alembic'))
    return ScriptDirectory.from_config(cfg)

def apply(c,rev,direction):
    # Bypass env.py entirely: it otherwise has a normal .env/settings fallback.
    module=script_directory().get_revision(rev).module
    with Operations.context(MigrationContext.configure(c)):
        getattr(module,direction)()

def migration_steps(*,correction_only=False):
    # Exercise the historical empty-table round trip BEFORE installing 0015.
    # The known defective 0015 downgrade is never invoked.
    return ((('upgrade',HEAD,HEAD),) if correction_only else (
        ('upgrade',WORK_REV,WORK_REV),('downgrade',WORK_REV,BASE),
        ('upgrade',WORK_REV,WORK_REV),('upgrade',HEAD,HEAD)))


def migrate(engine,*,correction_only=False):
    before=baseline(engine)
    with engine.begin() as c:
        c.execute(text('LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE'))
        require_public_state(c,WORK_REV if correction_only else BASE)
        for direction,module,new in migration_steps(correction_only=correction_only):
            apply(c,module,direction)
            c.execute(text('UPDATE alembic_version SET version_num=:v'),{'v':new})
            require(revision(c)==[new],'REVISION_STAMP_FAILED')
        require_public_state(c,HEAD)
    require(before==baseline(engine),'PUBLIC_BASELINE_CHANGED')
    from app.mandate_runner.runtime import verify_schema
    verify_schema(engine)


def require_corrected_code_checks(definitions):
    for field in ('outcome_code','reason_code'):
        matches=[row for row in definitions if row['name']=='ck_paper_work_'+field]
        require(len(matches)==1 and "'^[A-Z][A-Z0-9_]{0,63}$'" in matches[0]['sqltext']
            and '(0, 63)' not in matches[0]['sqltext'],'CORRECTED_CODE_CHECK_REQUIRED')


def catalog_reflection(inspector,method,name):
    progress('CATALOG_'+method.removeprefix('get_').upper(),table=name)
    return getattr(inspector,method)(name)


def catalog(engine):
    from app.mandate_runner.models import work
    from app.mandate_runner.runtime import verify_schema
    from app.db.base import Base
    progress('CATALOG_RUNTIME_SCHEMA_CHECK')
    verify_schema(engine)
    progress('CATALOG_INSPECTOR_CREATION')
    inspector=inspect(engine)
    relevant=('users','assets','instruments','trading_mandates','market_candles','paper_portfolios','paper_authorizations','paper_authorized_commands',
        *ECONOMIC,'paper_kill_switch_states','paper_risk_initializations','paper_strategy_activations','paper_mandate_work')
    report={}
    for name in relevant:
        table=Base.metadata.tables[name]
        check_definitions=catalog_reflection(inspector,'get_check_constraints',name)
        if name=='paper_mandate_work':require_corrected_code_checks(check_definitions)
        checks={r['name'] for r in check_definitions}
        require({k.name for k in table.constraints if k.__class__.__name__=='CheckConstraint'}<=checks,'CHECK_CONSTRAINT_MISSING')
        require(set(catalog_reflection(inspector,'get_pk_constraint',name)['constrained_columns'])==set(table.primary_key.columns.keys()),'PRIMARY_KEY_MISMATCH')
        unique={tuple(r['column_names']) for r in catalog_reflection(inspector,'get_unique_constraints',name)}
        for constraint in table.constraints:
            if constraint.__class__.__name__=='UniqueConstraint':require(tuple(constraint.columns.keys()) in unique,'UNIQUENESS_MISSING')
        actual_fks=catalog_reflection(inspector,'get_foreign_keys',name)
        for constraint in table.foreign_key_constraints:
            cols=tuple(constraint.columns.keys());elements=tuple(constraint.elements)
            require(any(tuple(f['constrained_columns'])==cols and f['referred_table']==elements[0].column.table.name
                and tuple(f['referred_columns'])==tuple(e.column.name for e in elements)
                and f.get('options',{}).get('ondelete','NO ACTION')==(constraint.ondelete or 'NO ACTION') for f in actual_fks),'FOREIGN_KEY_MISMATCH')
        actual_columns={v['name']:v for v in catalog_reflection(inspector,'get_columns',name)}
        require(set(actual_columns)==set(table.c.keys()),'COLUMN_SET_MISMATCH')
        for column in table.c:
            physical=actual_columns[column.name]
            require(physical['nullable']==column.nullable,'NULLABILITY_MISMATCH')
            require(str(physical['type'].compile(dialect=engine.dialect))==str(column.type.compile(dialect=engine.dialect)),'COLUMN_TYPE_MISMATCH')
            if column.type.__class__.__name__=='DateTime':require(physical['type'].timezone is True,'UTC_COLUMN_TYPE_MISMATCH')
        indices=catalog_reflection(inspector,'get_indexes',name)
        for index in table.indexes:
            require(any(i['name']==index.name and bool(i['unique'])==index.unique and tuple(i['column_names'])==tuple(e.name for e in index.expressions) for i in indices),'INDEX_MISSING')
        report[name]={'columns':[{**v,'type':str(v['type'])} for v in actual_columns.values()],
            'pk':catalog_reflection(inspector,'get_pk_constraint',name),'checks':sorted(checks),'check_definitions':check_definitions,'unique':sorted(list(x) for x in unique),'indexes':indices,'foreign_keys':actual_fks}
    progress('CATALOG_DEFINITION_CONNECTION')
    with diagnostic_connection(engine) as c:
        progress('CATALOG_TRIGGER_DEFINITIONS')
        triggers=c.execute(text("""SELECT cl.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) definition FROM pg_trigger t
            JOIN pg_class cl ON cl.oid=t.tgrelid JOIN pg_namespace n ON n.oid=cl.relnamespace
            WHERE n.nspname=current_schema() AND NOT t.tgisinternal ORDER BY cl.relname,t.tgname""")).mappings().all()
        required={('paper_mandate_work','paper_work_guard'),('trading_mandates','trading_mandate_guard'),
            ('execution_requests','immutable_audit'),('execution_fills','immutable_audit'),
            ('execution_approval_consumptions','immutable_audit'),('execution_approval_consumptions','consumption_guard'),
            ('execution_orders','order_guard'),('paper_positions','paper_position_guard'),
            ('paper_accounting_entries','paper_accounting_immutable'),('paper_authorizations','immutable_authorization'),
            ('paper_authorized_commands','immutable_authorization'),('paper_kill_switch_states','paper_switch_guard'),
            ('paper_risk_initializations','immutable_paper_activation'),('paper_strategy_activations','immutable_paper_activation')}
        require(required<={(r['relname'],r['tgname']) for r in triggers if r['tgenabled'] in ('O','A')},'IMMUTABILITY_TRIGGER_MISSING')
        progress('CATALOG_GUARD_FUNCTION_DEFINITIONS')
        functions=c.execute(text("SELECT p.proname,pg_get_functiondef(p.oid) definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.prokind='f' AND p.pronargs=0 AND p.proname::text=ANY(:names) ORDER BY p.proname"),
            {'names':['traderail_execution_immutable','traderail_execution_order_guard','traderail_execution_consumption_guard','traderail_paper_position_guard','traderail_paper_switch_guard','traderail_mandate_guard','traderail_paper_work_guard']}).mappings().all()
        require(len(functions)==7,'GUARD_FUNCTION_MISSING')
        progress('CATALOG_PARTIAL_OPEN_INDEX')
        partial=c.execute(text("SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid=to_regclass('uq_paper_position_open_instrument')")).scalar_one()
        require('UNIQUE' in partial and 'WHERE' in partial and 'OPEN' in partial,'OPEN_POSITION_INDEX_MISSING')
    return {'tables':report,'triggers':[dict(r) for r in triggers],'guard_functions':[dict(r) for r in functions],'partial_open_index':partial,'skip_locked':'NOT_USED_BY_PRODUCTION'}

def make_lab(engine,schema):
    require(bool(re.fullmatch(r'tr_runner_[0-9a-f]{16}_[a-z]+',schema)),'INVALID_FIXTURE_SCHEMA')
    with engine.begin() as c:
        require(revision(c)==[HEAD],'PUBLIC_0015_REQUIRED')
        require(c.execute(text('SELECT to_regnamespace(:s)'),{'s':schema}).scalar_one() is None,'FRESH_FIXTURE_SCHEMA_REQUIRED')
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f"COMMENT ON SCHEMA \"{schema}\" IS 'RUNNER_PG_VERIFICATION_ONLY'"))
        c.execute(text(f'SET LOCAL search_path="{schema}",pg_catalog'))
        for rev in reversed(list(script_directory().walk_revisions(base='base',head=HEAD))):apply(c,rev.revision,'upgrade')
        c.execute(text('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)'))
        c.execute(text('INSERT INTO alembic_version VALUES (:v)'),{'v':HEAD})

def fixture_rows(instrument_id,now,*,close_case=False):
    """Pure established test data; no settings loader, database or provider."""
    path=str(BACKEND/'tests')
    if path not in sys.path:sys.path.append(path)
    from test_integrated_9c_audit import frozen_dataset
    start=now.replace(minute=0,second=0,microsecond=0)-timedelta(hours=1)
    original=frozen_dataset('long_profit').candles
    offset=start-original[-1 if close_case else 130].open_time
    rows=[]
    for source in original:
        row={key:getattr(source,key) for key in source.__dataclass_fields__}
        row.update(id=uuid4(),instrument_id=instrument_id,open_time=source.open_time+offset,
            close_time=source.close_time+offset,retrieved_at=source.close_time+offset+timedelta(milliseconds=1))
        rows.append(row)
    return rows


def seed(engine,timeline):
    from app.users.models import User
    from app.portfolio.models import PaperPortfolio
    from app.instruments.models import Instrument
    from app.market_data.models import MarketCandle
    from app.mandates.service import create,transition
    from app.mandates.schemas import MandateIntent
    from app.mandates.domain import MandateStatus
    require(type(timeline) is ScenarioTimeline,'SCENARIO_TIMELINE_REQUIRED')
    now=timeline.entry_at
    with sessionmaker(bind=engine)() as db:
        with db.begin():
            iid=db.scalar(select(Instrument.id).where(Instrument.canonical_symbol=='BTC/USDT'))
            require(iid is not None,'CANONICAL_INSTRUMENT_REQUIRED')
            uid,pid=uuid4(),uuid4();born=now-timedelta(days=30)
            db.execute(insert(User).values(id=uid,google_id='RUNNER_PG_'+uid.hex,email=uid.hex+'@example.invalid',name='Disposable runner fixture',base_currency='USD',status='active',created_at=born,updated_at=born))
            db.execute(insert(PaperPortfolio).values(id=pid,user_id=uid,environment='PAPER',base_currency='USDT',starting_balance=Decimal('10000'),cash_balance=Decimal('10000'),status='active',created_at=born,updated_at=born))
            rows=fixture_rows(iid,now)
            require(len(rows)==219 and rows[131]['close_time']+timedelta(milliseconds=1,minutes=1)==timeline.hold_at
                and rows[-1]['close_time']+timedelta(milliseconds=1,minutes=1)==timeline.exit_at,
                'SCENARIO_DATASET_TIMELINE_MISMATCH')
            db.execute(insert(MarketCandle),rows[:131])
            mandate=create(db,uid,MandateIntent(capital_allocation='500',risk_level='BALANCED',trading_style='MEDIUM_TERM'))
            db.flush();mid=mandate.id
            transition(db,uid,mid,MandateStatus.ACTIVE)
    return {'user':uid,'portfolio':pid,'mandate':mid,'rows':rows,'timeline':timeline}

def no_provider():
    raise Refused('EXTERNAL_PROVIDER_FORBIDDEN')
    yield

def cycle_once(engine,fixture,*,at):
    from app.mandate_runner.cycle import process_one
    return process_one(sessionmaker(bind=engine),fixture['mandate'],provider_factory=contextmanager(no_provider),
        clock=ScenarioClock(at))

def counts(engine):
    with engine.connect() as c:
        return {name:c.execute(text('SELECT count(*) FROM '+name)).scalar_one() for name in (*ECONOMIC,'paper_authorizations','paper_authorized_commands')}

def expected_counts(engine,n):
    values=counts(engine)
    require(all(value==(1 if name=='paper_positions' else n) for name,value in values.items()),'DUPLICATE_OR_MISSING_ECONOMIC_FACT')
    with engine.connect() as c:
        receipts=c.execute(text('SELECT * FROM paper_accounting_entries ORDER BY executed_at')).mappings().all()
        cash=c.execute(text('SELECT cash_balance FROM paper_portfolios')).scalar_one()
        pos=c.execute(text('SELECT quantity,opening_quantity,status FROM paper_positions')).mappings().one()
        require(isinstance(cash,Decimal) and cash==receipts[-1]['cash_after'],'CASH_CHANGED_WITHOUT_CANONICAL_RECEIPT')
        require(receipts[0]['cash_before']==Decimal('10000'),'ENTRY_CASH_ORIGIN_CHANGED')
        for previous,current in zip(receipts,receipts[1:]):
            require(current['cash_before']==previous['cash_after'],'CASH_RECEIPT_CHAIN_BROKEN')
        require(pos['opening_quantity']==receipts[0]['quantity'],'POSITION_ACQUISITION_QUANTITY_CHANGED')
        require(pos['quantity']==(pos['opening_quantity'] if n==1 else Decimal('0')),'DUPLICATE_POSITION_MUTATION')

@contextmanager
def rejected(c,states=('23514','P0001')):
    try:
        with c.begin_nested():yield
    except DBAPIError as error:
        require(getattr(error.orig,'sqlstate',None) in states,'UNEXPECTED_SQLSTATE')
    else:raise Refused('INVALID_WRITE_WAS_ACCEPTED')

def pending(engine,f,*,before_commit=None):
    from app.mandate_runner.repository import ensure_work
    with engine.begin() as c:
        at=f['timeline'].entry_at
        row=ensure_work(c,f['mandate'],f['rows'][130]['id'],runner_version='pg-verification-v1',
            created_at=at,fresh_until=f['rows'][130]['open_time']+timedelta(hours=1,minutes=10))
        if before_commit is not None:before_commit()
    return row

def contracts(engine,f,*,evidence=None):
    from app.mandate_runner.models import work
    from app.mandate_runner.repository import ensure_work,record_attempt,record_result
    log=evidence if evidence is not None else EvidenceLog()
    def operation(code):log.record('CONTRACT_OPERATION',operation=code,source='SUPERVISOR')
    operation('CONTRACTS_INITIAL_WORK_CREATE')
    row=pending(engine,f,before_commit=lambda:operation('CONTRACTS_INITIAL_WORK_COMMIT'));wid=row['work_id']
    operation('CONTRACTS_TRANSACTION_BEGIN')
    with engine.begin() as c:
        with rejected(c):apply(c,WORK_REV,'downgrade')
        require(c.execute(select(work.c.work_id)).scalar_one()==wid and revision(c)==[HEAD],'PENDING_DOWNGRADE_CHANGED_HISTORY')
        at=f['timeline'].entry_at+timedelta(seconds=1)
        operation('CONTRACTS_IDEMPOTENT_RECONSTRUCTION')
        retry=ensure_work(c,f['mandate'],f['rows'][130]['id'],runner_version='pg-verification-v2',created_at=at,fresh_until=row['fresh_until'])
        require(row['created_at'].utcoffset()==timedelta(0) and row['fresh_until'].utcoffset()==timedelta(0),'UTC_ROUND_TRIP_FAILED')
        require(retry['work_id']==wid and retry['runner_version']=='pg-verification-v1','VERSION_BYPASSED_DEDUPLICATION')
        operation('CONTRACTS_EXPECTED_REJECTIONS')
        duplicate=dict(row);duplicate['work_id']=uuid4()
        with rejected(c,('23505',)):c.execute(insert(work).values(**duplicate))
        for values in (dict(state='INVALID'),dict(state='COMPLETED',outcome_code='DONE'),dict(state='RETRY_WAIT',retry_at=None),
                       dict(attempts=-1),dict(last_attempt_at=at),dict(updated_at=row['created_at']-timedelta(seconds=1)),
                       dict(state='SKIPPED',outcome_code=None),dict(state='RETRY_WAIT',retry_at=at-timedelta(days=1)),
                       dict(state='RETRY_WAIT',retry_at=row['fresh_until']),dict(retry_at=at),dict(reason_code='raw error text'),dict(runner_version='changed'),dict(fresh_until=at+timedelta(days=1))):
            with rejected(c):c.execute(update(work).where(work.c.work_id==wid).values(**values))
        with rejected(c,('23503',)):c.execute(update(work).where(work.c.work_id==wid).values(registry_identity='sha256:'+'0'*64))
        operation('CONTRACTS_VALID_ATTEMPT')
        record_attempt(c,wid,at=at)
        operation('CONTRACTS_VALID_SKIPPED_RESULT')
        record_result(c,wid,state='SKIPPED',at=at,outcome_code='VERIFIED',reason_code='TEST_ONLY')
        with rejected(c):c.execute(update(work).where(work.c.work_id==wid).values(state='PENDING'))
        with rejected(c):c.execute(work.delete().where(work.c.work_id==wid))
        operation('CONTRACTS_TERMINAL_SNAPSHOT')
        before=c.execute(select(work)).mappings().all()
        with rejected(c):apply(c,WORK_REV,'downgrade')
        require(c.execute(select(work)).mappings().all()==before and revision(c)==[HEAD],'POPULATED_DOWNGRADE_CHANGED_HISTORY')
        from app.mandates.models import TradingMandate
        operation('CONTRACTS_LOAD_MANDATE_TEMPLATE')
        original=c.execute(select(TradingMandate.__table__).where(TradingMandate.id==f['mandate'])).mappings().one()
        for state in ('COMPLETED','RECONCILIATION_REQUIRED'):
            mid=uuid4();intent=dict(original);intent.update(id=mid,status='DRAFT')
            operation('CONTRACTS_'+state+'_MANDATE_INSERT')
            c.execute(insert(TradingMandate).values(**intent))
            operation('CONTRACTS_'+state+'_MANDATE_ACTIVATE')
            c.execute(update(TradingMandate).where(TradingMandate.id==mid).values(status='ACTIVE'))
            now=f['timeline'].entry_at+timedelta(seconds=2)
            operation('CONTRACTS_'+state+'_WORK_CREATE')
            other=ensure_work(c,mid,f['rows'][130]['id'],runner_version='pg-verification-v1',created_at=now,fresh_until=row['fresh_until'])
            if state=='COMPLETED':
                operation('CONTRACTS_COMPLETED_VALID_ATTEMPT')
                record_attempt(c,other['work_id'],at=now)
            operation('CONTRACTS_'+state+'_VALID_RESULT')
            record_result(c,other['work_id'],state=state,at=now,outcome_code='VERIFIED')
            with rejected(c):c.execute(update(work).where(work.c.work_id==other['work_id']).values(reason_code='CHANGED'))
            with rejected(c):c.execute(work.delete().where(work.c.work_id==other['work_id']))
        operation('CONTRACTS_TRANSACTION_COMMIT')
    operation('CONTRACTS_TRANSACTION_COMPLETE')


def locks(engine,f):
    from app.mandates.service import owned
    from app.mandate_runner.repository import load_work
    from app.mandate_runner.models import work
    row=pending(engine,f)
    with engine.connect() as a,engine.connect() as b:
        with a.begin(),b.begin():
            a.execute(text('SELECT id FROM trading_mandates WHERE id=:m FOR UPDATE'),{'m':f['mandate']})
            load_work(a,f['mandate'],f['rows'][130]['id'])
            # Validate the blocking claim actually used by production.
            b.execute(text("SET LOCAL lock_timeout='250ms'"))
            with rejected(b,('55P03',)):load_work(b,f['mandate'],f['rows'][130]['id'])
            from app.execution.paper.book import capture_book
            capture_book(a,f['portfolio'],lock=True)
            with rejected(b,('55P03',)):capture_book(b,f['portfolio'],lock=True)
            a.execute(text('SELECT id FROM market_candles WHERE id=:i FOR SHARE'),{'i':f['rows'][130]['id']})
            with rejected(b,('55P03',)):
                b.execute(text('SELECT id FROM market_candles WHERE id=:i FOR UPDATE'),{'i':f['rows'][130]['id']})
        with b.begin():require(load_work(b,f['mandate'],f['rows'][130]['id']) is not None,'LOCK_NOT_RELEASED')


def worker_bootstrap(engine):
    from app.mandate_runner.runtime import verify_schema
    verify_schema(engine)
    with diagnostic_connection(engine) as c:
        require(c.execute(text('SELECT 1')).scalar_one()==1,'WORKER_BOOTSTRAP_FAILED')


def worker(env_file,approval,schema,f,mode,pipe,release,*,start,limits=WorkerLimits(),events_path=None,worker_name='A'):
    engine=None;failed=False;log=None;phase='BOOTSTRAP';original_record_result=None
    def emit(kind,value=None):
        fields={'worker':worker_name,'source':'WORKER_REPORT'}
        if kind=='LOCK':fields['backend_pid']=value
        elif isinstance(value,dict):fields.update(value)
        if log is not None:log.record(kind,**fields)
        pipe.send((kind,value))
    try:
        log=EvidenceLog(events_path)
        emit('BOOTSTRAPPING')
        # Only serializable config paths/fixture values cross the process boundary.
        # Every child constructs and owns its own engine and all of its sessions.
        engine=engine_for(env_file,approval,schema)
        @event.listens_for(engine,'do_connect')
        def connecting(dialect,record,cargs,cparams):
            try:return dialect.connect(*cargs,**cparams)
            except Exception as error:
                emit('FAILED',{'category':connection_failure(error),'phase':phase,
                    'transaction_outcome':'UNCONFIRMED_REQUIRES_DURABLE_INSPECTION'})
                raise
        worker_bootstrap(engine)
        from app.mandate_runner import cycle
        if mode=='before':
            original=cycle.record_result;original_record_result=original
            def pause(*args,**kw):
                result=original(*args,**kw)
                if kw['state']=='COMPLETED':
                    emit('PRE_COMMIT',{'confirmation':'APPLICATION_REPORT_ONLY',
                        'transaction_outcome':'UNCONFIRMED_REQUIRES_DURABLE_INSPECTION'})
                    wait_gate(release,Deadline(limits.coordination),'WORKER_PRE_COMMIT_DEADLINE')
                return result
            cycle.record_result=pause
        @event.listens_for(engine,'before_cursor_execute')
        def attempting(c,cursor,statement,parameters,context,many):
            if 'trading_mandates' in statement and 'FOR UPDATE' in statement:
                # Proxy/libpq cancellation identifiers need not be server process IDs.
                # Observe the actual backend on this exact lock-attempt connection.
                emit('LOCK',c.execute(text('SELECT pg_backend_pid()')).scalar_one())
        emit('READY');phase='WAIT_START'
        wait_gate(start,Deadline(limits.startup+limits.coordination),'WORKER_START_GATE_DEADLINE')
        phase='EXECUTION';emit('RUNNING')
        result=cycle_once(engine,f,at=f['timeline'].entry_at)
        if result.persisted:
            emit('COMMITTED',{'confirmation':'APPLICATION_REPORT_ONLY',
                'transaction_outcome':'UNCONFIRMED_REQUIRES_DURABLE_INSPECTION'})
        emit('RESULT',{'state':result.state,'outcome':result.outcome_code,'persisted':result.persisted,'reused':result.reused})
        if mode=='after':
            phase='WAIT_AFTER_RESULT'
            wait_gate(release,Deadline(limits.coordination),'WORKER_AFTER_RESULT_DEADLINE')
    except BaseException as error:
        failed=True
        code=str(error) if isinstance(error,(Refused,SupervisionError)) else (
            'WORKER_BOOTSTRAP_FAILED' if phase=='BOOTSTRAP' else 'WORKER_EXECUTION_FAILED')
        try:
            emit('FAILED',{'category':code,'phase':phase,
                'transaction_outcome':'UNCONFIRMED_REQUIRES_DURABLE_INSPECTION'})
            emit('ERROR',{'category':code,'sqlstate':getattr(getattr(error,'orig',None),'sqlstate',None)})
        except BaseException:pass
    finally:
        try:
            if engine is not None:engine.dispose()
        except Exception:
            failed=True
            try:
                emit('FAILED',{'category':'WORKER_CLEANUP_FAILED','phase':'CLEANUP'})
                emit('ERROR',{'category':'WORKER_CLEANUP_FAILED','sqlstate':None})
            except BaseException:pass
        if original_record_result is not None:cycle.record_result=original_record_result
        try:pipe.close()
        except BaseException:failed=True
    if failed:raise SystemExit(1)


def receive(pipe,wanted,timeout=30,observed=None,*,deadline=None,log=None,worker_name='A'):
    deadline=deadline or Deadline(timeout)
    while True:
        remaining=deadline.remaining('WORKER_'+wanted+'_DEADLINE')
        if not pipe.poll(min(.2,remaining)):continue
        try:kind,value=pipe.recv()
        except (EOFError,OSError):raise Refused('WORKER_CONNECTION_CLOSED') from None
        deadline.remaining('WORKER_'+wanted+'_DEADLINE')
        if observed is not None:observed[kind]=value
        if log is not None:
            # Child messages are claims, never database commit/rollback proof.
            fields={'worker':worker_name,'source':'WORKER_REPORT'}
            if kind=='LOCK' and type(value) is int:fields['backend_pid']=value
            log.record(kind,**fields)
        if kind in ('ERROR','FAILED'):
            category=value.get('category') if isinstance(value,dict) else None
            allowed={'CONNECTION_TIMEOUT','TLS_FAILURE','SOCKET_FAILURE','STARTUP_OPTIONS_FAILURE',
                'INITIALIZATION_FAILURE','UNKNOWN_CONNECTION_FAILURE','WORKER_BOOTSTRAP_FAILED',
                'WORKER_EXECUTION_FAILED','WORKER_CLEANUP_FAILED'}
            if category in allowed:
                raise Refused(('WORKER_STARTUP_' if wanted=='READY' else 'WORKER_EXECUTION_')+category)
            raise Refused('WORKER_FAILED')
        if kind==wanted:return value


def inspect_cycle_outcome(engine,f):
    """Fresh durable evidence under the production mandate lock; no execution.

    Failure/partial/conflicting facts remain uncertain. A dead process or a
    worker COMMITTED message alone is never enough to authorize a test replay.
    """
    from app.mandate_runner.models import work
    with engine.begin() as c:
        require(c.execute(text('SELECT id FROM trading_mandates WHERE id=:m FOR UPDATE'),
            {'m':f['mandate']}).scalar_one()==f['mandate'],'FIXTURE_MANDATE_MISSING')
        row=c.execute(select(work).where(work.c.mandate_id==f['mandate'],
            work.c.candle_id==f['rows'][130]['id'])).mappings().one_or_none()
        values={name:c.execute(text('SELECT count(*) FROM '+name)).scalar_one()
            for name in (*ECONOMIC,'paper_authorizations','paper_authorized_commands')}
        if row is None and all(value==0 for value in values.values()):
            require(c.execute(text('SELECT cash_balance FROM paper_portfolios')).scalar_one()==Decimal('10000'),
                'UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
            for name in ('paper_risk_initializations','paper_strategy_activations','paper_kill_switch_states'):
                require(c.execute(text('SELECT count(*) FROM '+name)).scalar_one()==0,
                    'UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
            return 'ROLLED_BACK'
        require(row is not None and row['state']=='COMPLETED' and row['outcome_code']=='PAPER_EXECUTED'
            and all(value==1 for value in values.values()),'UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
        receipt=c.execute(text('SELECT request_identity,position_id FROM paper_accounting_entries')).mappings().one()
        registry=c.execute(text('SELECT registry_identity FROM paper_authorizations')).scalar_one()
        require(row['request_identity']==receipt['request_identity'] and row['position_id']==receipt['position_id']
            and row['registry_identity']==registry,'UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
        expected_counts(engine,1)
        return 'COMMITTED'


def database_probe(env_file,approval,schema,f,role,pipe,limits,events_path):
    """Owned read-only observer/inspector. Never calls trading orchestration."""
    engine=None;failed=False;log=None;phase='BOOTSTRAP'
    try:
        log=EvidenceLog(events_path);log.record('BOOTSTRAPPING',worker=role,source='WORKER_REPORT')
        engine=engine_for(env_file,approval,schema)
        worker_bootstrap(engine)
        with engine.connect() as observer:
            pipe.send(('READY',None));log.record('READY',worker=role,source='WORKER_REPORT')
            start=Deadline(limits.startup+limits.coordination)
            while not pipe.poll(min(.2,start.remaining('PROBE_START_DEADLINE'))):pass
            command=pipe.recv();phase='EXECUTION'
            log.record('RUNNING',worker=role,source='WORKER_REPORT')
            if role=='OBSERVER':
                holder,waiter=command
                require(type(holder) is int and type(waiter) is int,'INVALID_OBSERVER_PIDS')
                until=Deadline(limits.lock_observation);blocked=False
                while not blocked:
                    until.remaining('LOCK_OBSERVATION_DEADLINE')
                    blocked=bool(observer.execute(text('SELECT :holder=ANY(pg_blocking_pids(:waiter))'),
                        {'holder':holder,'waiter':waiter}).scalar_one())
                    until.remaining('LOCK_OBSERVATION_DEADLINE')
                    if not blocked:time.sleep(min(.05,until.remaining('LOCK_OBSERVATION_DEADLINE')))
                result='CONTENTION_CONFIRMED'
            else:
                require(role in ('INSPECT1','INSPECT2') and command=='INSPECT','INVALID_INSPECTION_COMMAND')
                result=inspect_cycle_outcome(engine,f)
            log.record('DATABASE_OBSERVED',worker=role,source='POSTGRESQL',durable_state=result)
            pipe.send(('RESULT',result))
    except BaseException as error:
        failed=True
        category=str(error) if isinstance(error,(Refused,SupervisionError)) else (
            connection_failure(error) if isinstance(error,DBAPIError) and getattr(error.orig,'sqlstate',None) is None
            else 'WORKER_BOOTSTRAP_FAILED' if phase=='BOOTSTRAP' else 'WORKER_EXECUTION_FAILED')
        try:
            if log is not None:log.record('FAILED',worker=role,source='WORKER_REPORT',category=category,phase=phase)
            pipe.send(('FAILED',{'category':category,'phase':phase}))
        except BaseException:pass
    finally:
        try:
            if engine is not None:engine.dispose()
        except BaseException:
            failed=True
            try:
                if log is not None:log.record('FAILED',worker=role,source='WORKER_REPORT',category='WORKER_CLEANUP_FAILED',phase='CLEANUP')
                pipe.send(('FAILED',{'category':'WORKER_CLEANUP_FAILED','phase':'CLEANUP'}))
            except BaseException:pass
        try:pipe.close()
        except BaseException:failed=True
    if failed:raise SystemExit(1)


def concurrent(engine,f,args,schema,mode,*,evidence=None,limits=WorkerLimits()):
    ctx=mp.get_context('spawn');release=ctx.Event();start_a=ctx.Event();start_b=ctx.Event()
    pa,ca=ctx.Pipe();pb,cb=ctx.Pipe();log=evidence or EvidenceLog()
    a=ctx.Process(target=worker,args=(args.env_file,args.approval,schema,f,'after' if mode=='recovery' else 'before',ca,release),
        kwargs={'start':start_a,'limits':limits,'events_path':log.worker_path('A'),'worker_name':'A'})
    b=None;primary=None;owned=[('A',a)];probe_pipes=[];inspection_number=0
    def spawn_probe(role):
        parent,child=ctx.Pipe();probe_pipes.extend((parent,child))
        process=ctx.Process(target=database_probe,args=(args.env_file,args.approval,schema,f,role,child,limits,log.worker_path(role)))
        owned.append((role,process))
        log.record('STARTUP_AUTHORIZED',worker=role,source='SUPERVISOR');process.start()
        return process,parent
    def inspect_durable(execution):
        nonlocal inspection_number
        inspection_number+=1;role='INSPECT'+str(inspection_number)
        process,channel=spawn_probe(role)
        startup=Deadline(min(limits.startup,execution.remaining('WORKER_EXECUTION_DEADLINE')))
        receive(channel,'READY',deadline=startup,log=log,worker_name=role)
        log.record('INSPECTION_AUTHORIZED',worker=role,source='SUPERVISOR');channel.send('INSPECT')
        result=receive(channel,'RESULT',deadline=execution,log=log,worker_name=role)
        join_owned(process,execution,log,role)
        require(result in ('ROLLED_BACK','COMMITTED'),'UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
        return result
    try:
        startup=Deadline(limits.startup)
        log.record('STARTUP_AUTHORIZED',worker='A',source='SUPERVISOR')
        a.start()
        if mode=='race':
            b=ctx.Process(target=worker,args=(args.env_file,args.approval,schema,f,'normal',cb,release),
                kwargs={'start':start_b,'limits':limits,'events_path':log.worker_path('B'),'worker_name':'B'})
            owned.append(('B',b))
            log.record('STARTUP_AUTHORIZED',worker='B',source='SUPERVISOR');b.start()
        observer_process,observer_channel=spawn_probe('OBSERVER') if mode=='race' else (None,None)
        receive(pa,'READY',deadline=startup,log=log)
        if b is not None:receive(pb,'READY',deadline=startup,log=log,worker_name='B')
        if observer_process is not None:receive(observer_channel,'READY',deadline=startup,log=log,worker_name='OBSERVER')
        log.record('BOOTSTRAP_COMPLETE',source='SUPERVISOR')
        # All observer connection/bootstrap work completed before trading locks.
        with nullcontext():
            execution=Deadline(limits.execution)
            log.record('RUN_AUTHORIZED',worker='A',source='SUPERVISOR');start_a.set()
            if mode=='recovery':
                result=receive(pa,'RESULT',deadline=execution,log=log)
                require(result['outcome']=='PAPER_EXECUTED' and result['persisted'],'ENTRY_NOT_COMMITTED')
                stop_owned(a,Deadline(limits.cleanup),log,'A')
                durable=inspect_durable(execution)
                require(durable=='COMMITTED','COMMIT_RESTART_RECOVERY_FAILED')
                log.record('DATABASE_OUTCOME_CONFIRMED',durable_state=durable,source='POSTGRESQL')
                execution.remaining('WORKER_EXECUTION_DEADLINE')
                again=cycle_once(engine,f,at=f['timeline'].entry_at)
                require(again.reused and again.state=='COMPLETED','COMMIT_RESTART_RECOVERY_FAILED')
            else:
                holder_events={}
                receive(pa,'PRE_COMMIT',observed=holder_events,deadline=execution,log=log)
                require('LOCK' in holder_events,'HOLDER_LOCK_NOT_OBSERVED')
                if mode=='kill':
                    stop_owned(a,Deadline(limits.cleanup),log,'A')
                    durable=inspect_durable(execution)
                    require(durable=='ROLLED_BACK','UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
                    log.record('DATABASE_OUTCOME_CONFIRMED',durable_state=durable,source='POSTGRESQL')
                    execution.remaining('WORKER_EXECUTION_DEADLINE')
                    again=cycle_once(engine,f,at=f['timeline'].entry_at)
                    require(again.state=='COMPLETED' and again.outcome_code=='PAPER_EXECUTED','TERMINATION_RECOVERY_FAILED')
                else:
                    log.record('RUN_AUTHORIZED',worker='B',source='SUPERVISOR');start_b.set()
                    waiting_pid=receive(pb,'LOCK',deadline=execution,log=log,worker_name='B')
                    log.record('OBSERVATION_AUTHORIZED',worker='OBSERVER',source='SUPERVISOR')
                    observer_channel.send((holder_events['LOCK'],waiting_pid))
                    coordination=Deadline(min(limits.coordination,execution.remaining('WORKER_EXECUTION_DEADLINE')))
                    blocked=receive(observer_channel,'RESULT',deadline=coordination,log=log,worker_name='OBSERVER')
                    require(blocked=='CONTENTION_CONFIRMED','SECOND_WORKER_DID_NOT_WAIT_FOR_CLAIM')
                    join_owned(observer_process,execution,log,'OBSERVER')
                    log.record('CONTENTION_CONFIRMED',source='POSTGRESQL')
                    # This is the ONLY release: successful test coordination, never cleanup.
                    log.record('COMMIT_RELEASE_AUTHORIZED',worker='A',source='SUPERVISOR');release.set()
                    first=receive(pa,'RESULT',deadline=execution,log=log)
                    second=receive(pb,'RESULT',deadline=execution,log=log,worker_name='B')
                    require(first['state']=='COMPLETED' and first['outcome']=='PAPER_EXECUTED' and first['persisted']
                        and second['state']=='COMPLETED' and second['outcome']=='PAPER_EXECUTED'
                        and second['persisted'] and second['reused'],'RACE_DID_NOT_RECOVER_ONE_FACT')
                    join_owned(a,execution,log,'A');join_owned(b,execution,log,'B')
                    require(a.exitcode==0 and b.exitcode==0,'RACE_WORKER_DID_NOT_EXIT_CLEANLY')
            execution.remaining('WORKER_EXECUTION_DEADLINE')
            durable=inspect_durable(execution)
            require(durable=='COMMITTED','UNCERTAIN_TRANSACTION_REQUIRES_INSPECTION')
            log.record('DATABASE_OUTCOME_CONFIRMED',durable_state=durable,source='POSTGRESQL')
            expected_counts(engine,1)
    except BaseException as error:
        primary=error
        try:log.record('CASE_FAILED',category=str(error) if isinstance(error,(Refused,SupervisionError)) else type(error).__name__,source='SUPERVISOR')
        except BaseException:pass
        raise
    finally:
        # No release/start gates are set here. Paused work must not be committed
        # as a side effect of shutting down a failed verification phase.
        cleanup_failed=False;cleanup=Deadline(limits.cleanup)
        for name,process in owned:
            if process is not None:
                try:stop_owned(process,cleanup,log,name)
                except BaseException:cleanup_failed=True
        for pipe in (pa,ca,pb,cb,*probe_pipes):
            try:pipe.close()
            except BaseException:cleanup_failed=True
        if cleanup_failed:raise WorkerCleanupError(primary) from None
        try:log.record('WORKER_CLEANUP_COMPLETE',source='SUPERVISOR')
        except BaseException:raise WorkerCleanupError(primary) from None


def rollback(engine,f):
    from app.mandate_runner import cycle
    original=cycle.record_result
    def fail(*args,**kw):
        result=original(*args,**kw)
        if kw['state']=='COMPLETED':raise RuntimeError('SIMULATED_WORKER_FAILURE')
        return result
    cycle.record_result=fail
    try:result=cycle_once(engine,f,at=f['timeline'].entry_at)
    finally:cycle.record_result=original
    require(result.state=='RECONCILIATION_REQUIRED' and result.persisted,'ROLLBACK_NOT_QUARANTINED')
    require(all(v==0 for v in counts(engine).values()),'ROLLBACK_LEFT_ECONOMIC_FACTS')
    with engine.connect() as c:
        require(c.execute(text('SELECT cash_balance FROM paper_portfolios')).scalar_one()==Decimal('10000'),'ROLLBACK_CHANGED_CASH')
        for table in ('paper_risk_initializations','paper_strategy_activations','paper_kill_switch_states'):
            require(c.execute(text('SELECT count(*) FROM '+table)).scalar_one()==0,'ROLLBACK_LEFT_AUTHORITY_STATE')
    from app.mandate_runner.process import due,Candidate
    require(not due(sessionmaker(bind=engine),Candidate(f['mandate'],f['user']),f['timeline'].entry_at),'RECONCILIATION_BARRIER_BYPASSED')

def barriers(engine,f):
    from app.mandate_runner.process import recover_uncertain,due,Candidate
    from app.mandate_runner.cycle import CycleResult
    row=pending(engine,f)
    unknown=CycleResult(f['mandate'],'RECONCILIATION_REQUIRED','DURABLE_STATE_UNAVAILABLE',
        'VERIFY_DURABLE_STATE_BEFORE_RETRY',row['work_id'])
    outcome=recover_uncertain(sessionmaker(bind=engine),Candidate(f['mandate'],f['user']),unknown,f['timeline'].entry_at)
    require(outcome=='QUARANTINED','UNCERTAIN_WORK_NOT_QUARANTINED')
    require(not due(sessionmaker(bind=engine),Candidate(f['mandate'],f['user']),f['timeline'].entry_at),'RECONCILIATION_BARRIER_BYPASSED')
    repeated=cycle_once(engine,f,at=f['timeline'].entry_at)
    require(repeated.reused and repeated.state=='RECONCILIATION_REQUIRED','RESTART_IGNORED_RECONCILIATION')
    require(all(v==0 for v in counts(engine).values()),'RECONCILIATION_CREATED_ECONOMICS')


def close_lifecycle(engine,f):
    from app.strategy_lab.numeric import exact_sum
    from app.market_data.models import MarketCandle
    from app.execution.paper.models import accounting,positions
    entry=cycle_once(engine,f,at=f['timeline'].entry_at)
    require(entry.outcome_code=='PAPER_EXECUTED' and entry.persisted,'HISTORICAL_ACQUISITION_FIXTURE_FAILED')
    before=counts(engine)
    with engine.begin() as c:c.execute(insert(MarketCandle),f['rows'][131:132])
    held=cycle_once(engine,f,at=f['timeline'].hold_at)
    require(held.outcome_code=='POSITION_HELD' and held.persisted,'GENUINE_HOLD_DID_NOT_HOLD')
    require(counts(engine)==before,'HOLD_CHANGED_ECONOMIC_FACTS')
    expected_counts(engine,1)
    with engine.begin() as c:c.execute(insert(MarketCandle),f['rows'][132:])
    result=cycle_once(engine,f,at=f['timeline'].exit_at)
    require(result.outcome_code=='POSITION_CLOSED' and result.persisted,'GENUINE_EXIT_DID_NOT_CLOSE')
    expected_counts(engine,2)
    with engine.connect() as c:
        receipts=c.execute(select(accounting).order_by(accounting.c.executed_at)).mappings().all()
        pos=c.execute(select(positions)).mappings().one()
        cash=c.execute(text('SELECT cash_balance FROM paper_portfolios WHERE id=:p'),{'p':f['portfolio']}).scalar_one()
        require(pos['status']=='CLOSED' and pos['quantity']==0 and pos['closed_at'] is not None,'POSITION_NOT_CLOSED')
        require(receipts[0]['decision_identity']!=receipts[1]['decision_identity'],'ENTRY_APPROVAL_REUSED_FOR_EXIT')
        require(isinstance(cash,Decimal) and cash==exact_sum((Decimal('10000'),receipts[1]['net_realized_pnl'])),'PNL_CASH_RECONCILIATION_FAILED')
    from app.mandate_runner.process import due,Candidate
    require(not due(sessionmaker(bind=engine),Candidate(f['mandate'],f['user']),f['timeline'].terminal_retry_at),'TERMINAL_CLOSE_BARRIER_BYPASSED')
    before=counts(engine);again=cycle_once(engine,f,at=f['timeline'].terminal_retry_at)
    require(again.reused and counts(engine)==before,'CLOSE_RETRY_DUPLICATED_ECONOMICS')
    expected_counts(engine,2)

def plan():
    return {'status':'OFFLINE_PLAN_ONLY','connection':False,'phases':['preflight','migration','correction','catalog','real-clock',*CASES],
        'base_revision':BASE,'head_revision':HEAD,'production_skip_locked':False,
        'cleanup':'DISPOSE_APPROVED_CHILD_BRANCH; no row deletes, truncation or trigger disabling'}


def postgres_failure_fields(error,log):
    original=getattr(error,'orig',None)
    state=getattr(original,'sqlstate',None)
    state=state if isinstance(state,str) and re.fullmatch(r'[0-9A-Z]{5}',state) else None
    name=getattr(getattr(original,'diag',None),'constraint_name',None)
    allowed={
        'uq_paper_work_mandate_candle','paper_mandate_work_pkey',
        'paper_mandate_work_mandate_id_fkey','paper_mandate_work_candle_id_fkey',
        'paper_mandate_work_registry_identity_fkey','paper_mandate_work_request_identity_fkey',
        'paper_mandate_work_position_id_fkey',
        *('ck_paper_work_'+suffix for suffix in ('state','attempts','times','retry',
            'completed_attempt','outcome','version','outcome_code','reason_code')),
        *('ck_mandate_'+suffix for suffix in ('environment','currency','instrument','direction',
            'allocation','risk_level','style','status','timestamps'))}
    constraint=name if isinstance(name,str) and name in allowed else 'UNRECOGNIZED_CONSTRAINT' if name is not None else None
    operation=next((row['operation'] for row in reversed(log.rows)
        if row['event']=='CONTRACT_OPERATION'),None) if log is not None else None
    return {'sqlstate':state,'constraint':constraint,'operation':operation}

def main(argv=None):
    global STAGE,PREFLIGHT_DIAGNOSTICS,CATALOG_DIAGNOSTICS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='store_true')
    parser.add_argument('--phase',choices=('preflight','migration','correction','catalog','real-clock',*CASES))
    parser.add_argument('--env-file',default=str(BACKEND/'.env.integration'))
    parser.add_argument('--approval')
    parser.add_argument('--confirm-child')
    parser.add_argument('--artifacts',default=str(ROOT/'work/runner-pg-results'))
    args=parser.parse_args(argv)
    if args.plan or args.phase is None:
        print(json.dumps(plan(),sort_keys=True));return 0
    engine=None;lab=None;before=None;exit_code=1;watchdog=None;clock_measurement=None;phase_log=None;success_report=None;output=None;run=None
    timeline=ScenarioTimeline() if args.phase in CASES else None
    PREFLIGHT_DIAGNOSTICS=args.phase=='preflight'
    CATALOG_DIAGNOSTICS=args.phase=='catalog'
    try:
        if PREFLIGHT_DIAGNOSTICS:watchdog=start_preflight_watchdog()
        STAGE='TARGET_APPROVAL'
        progress('TARGET_APPROVAL')
        require(args.approval is not None,'APPROVAL_FILE_REQUIRED')
        approval=json.loads(approval_path(args.approval).read_text())
        require(args.confirm_child==approval.get('child_branch_id'),'EXPLICIT_CHILD_CONFIRMATION_REQUIRED')
        output=Path(args.artifacts).resolve()
        require(output.is_relative_to((ROOT/'work').resolve()),'ARTIFACTS_MUST_STAY_IN_IGNORED_WORK')
        engine=engine_for(args.env_file,args.approval)
        run=uuid4().hex[:16];output.mkdir(parents=True,exist_ok=True)
        phase_log=EvidenceLog(output/(run+'-'+args.phase+'-events.jsonl'))
        phase_log.record('VALIDATION_STARTED',phase=args.phase,source='SUPERVISOR')
        STAGE=args.phase.upper()
        progress('CONNECTION_ESTABLISHING')
        with diagnostic_connection(engine) as c:
            progress('CONNECTION_ESTABLISHED')
            if PREFLIGHT_DIAGNOSTICS:verify_timeouts(c)
            require(c.dialect.name=='postgresql' and c.get_isolation_level()=='READ COMMITTED','POSTGRESQL_READ_COMMITTED_REQUIRED')
            progress('REVISION_WORK_TABLE_CHECK')
            require_public_state(c,BASE if args.phase in ('preflight','migration') else WORK_REV if args.phase=='correction' else HEAD)
            if timeline is not None:
                # Deterministic safety phases need only prove historical input;
                # wall-clock alignment belongs to the separate observation lane.
                server_now=c.execute(text('SELECT clock_timestamp()')).scalar_one()
                require(timeline.terminal_retry_at<server_now,'HISTORICAL_SCENARIO_REQUIRED')
            else:
                progress('CLOCK_CHECK')
                clock_measurement=measure_clock(c)
                (output/(run+'-clock-measurement.json')).write_text(json.dumps({
                    'phase':args.phase,'child_branch_id':approval['child_branch_id'],
                    'target_sha256':approval['target_sha256'],
                    'measurement':clock_measurement.to_dict()},indent=2))
                print('CLOCK: '+clock_measurement.status,flush=True)
                require(clock_measurement.status=='WITHIN_TOLERANCE',
                    'CLOCK_MEASUREMENT_'+clock_measurement.status)
        progress('FIRST_FINGERPRINT_PASS')
        before=baseline(engine)
        progress('BASELINE_ARTIFACT_HANDLING')
        baseline_file=output/'baseline.json'
        if args.phase=='preflight' and not baseline_file.exists():
            baseline_file.write_text(json.dumps({'public_baseline':before,'target_sha256':approval['target_sha256'],
                'child_branch_id':approval['child_branch_id']},indent=2))
        require(baseline_file.is_file(),'PREFLIGHT_BASELINE_REQUIRED')
        accepted=json.loads(baseline_file.read_text())
        require(accepted['public_baseline']==before and accepted['target_sha256']==approval['target_sha256']
            and accepted['child_branch_id']==approval['child_branch_id'],'ACCEPTED_BASELINE_OR_TARGET_CHANGED')
        if args.phase=='real-clock':
            boundary_report=validate_real_clock_boundaries(clock_measurement,fixture_rows)
            (output/(run+'-real-clock-boundaries.json')).write_text(json.dumps(boundary_report,indent=2))
        elif args.phase in ('migration','correction'):migrate(engine,correction_only=args.phase=='correction')
        elif args.phase=='catalog':
            (output/(run+'-catalog-details.json')).write_text(json.dumps(catalog(engine),default=str,indent=2))
        elif args.phase in CASES:
            schema='tr_runner_'+run+'_'+args.phase
            (output/(run+'-fixture-manifest.json')).write_text(json.dumps({'phase':args.phase,'fixture_schema':schema,'public_baseline':before,'scenario':timeline.to_dict(),
                'worker_event_files':[Path(phase_log.worker_path(name)).name for name in (('A','B','OBSERVER','INSPECT1') if args.phase=='race' else ('A','INSPECT1','INSPECT2') if args.phase in ('kill','recovery') else ())]},indent=2))
            phase_log.record('SCHEMA_BOOTSTRAP_AUTHORIZED',schema=schema,source='SUPERVISOR')
            make_lab(engine,schema)
            phase_log.record('SCHEMA_BOOTSTRAP_COMPLETE',schema=schema,source='SUPERVISOR')
            lab=engine_for(args.env_file,args.approval,schema)
            from app.mandate_runner.runtime import verify_schema
            verify_schema(lab)
            phase_log.record('SEED_AUTHORIZED',schema=schema,source='SUPERVISOR')
            f=seed(lab,timeline)
            phase_log.record('CASE_AUTHORIZED',phase=args.phase,schema=schema,source='SUPERVISOR')
            if args.phase=='contracts':contracts(lab,f,evidence=phase_log)
            elif args.phase=='locks':locks(lab,f)
            elif args.phase in ('race','kill','recovery'):concurrent(lab,f,args,schema,args.phase,evidence=phase_log)
            elif args.phase=='rollback':rollback(lab,f)
            elif args.phase=='barriers':barriers(lab,f)
            elif args.phase=='close':close_lifecycle(lab,f)
        phase_log.record('ASSERTIONS_COMPLETE',source='SUPERVISOR')
        progress('SECOND_FINGERPRINT_PASS')
        require(before==baseline(engine),'PUBLIC_BASELINE_CHANGED')
        progress('FINAL_CONNECTION_ESTABLISHING')
        with diagnostic_connection(engine) as c:
            progress('FINAL_VERIFICATION')
            if PREFLIGHT_DIAGNOSTICS:verify_timeouts(c)
            require_public_state(c,BASE if args.phase=='preflight' else HEAD)
        phase_log.record('BASELINE_VERIFIED',source='SUPERVISOR')
        success_report={'status':'PASS','phase':args.phase,
            'fixture_schema':schema if args.phase in CASES else None,'public_baseline':before,
            'scenario':timeline.to_dict() if timeline is not None else None,
            'clock_measurement':clock_measurement.to_dict() if clock_measurement is not None else None}
        exit_code=0
    except BaseException as error:
        # No tracebacks, raw database errors, URLs, hostnames or credentials.
        diagnostics=postgres_failure_fields(error,phase_log)
        state=diagnostics['sqlstate']
        code=str(error) if isinstance(error,(Refused,SupervisionError)) else type(error).__name__
        if CATALOG_DIAGNOSTICS and not isinstance(error,Refused) and STAGE=='SQLALCHEMY_CONNECTION_INITIALIZING':
            code=connection_failure(error,initialization=True)
        if isinstance(error,WorkerCleanupError) and error.primary is not None:
            primary=error.primary
            primary_code=str(primary) if isinstance(primary,(Refused,SupervisionError)) else type(primary).__name__
            print('STOP: stage='+STAGE+'; category='+primary_code+'; retain child branch for inspection',flush=True)
        print('STOP: stage='+('WORKER_CLEANUP' if isinstance(error,WorkerCleanupError) else STAGE)+
            '; category='+code+'; SQLSTATE='+str(state)+'; retain child branch for inspection',flush=True)
        if phase_log is not None:
            try:phase_log.record('VALIDATION_FAILED',category=code,source='SUPERVISOR',**diagnostics)
            except BaseException:print('STOP: stage=EVIDENCE; category=FAILURE_EVIDENCE_WRITE_FAILED',flush=True)
        exit_code=1
    finally:
        progress('RESOURCE_RELEASE')
        cleanup_failed=False
        for resource in (lab,engine):
            try:
                if resource is not None:resource.dispose()
            except Exception:cleanup_failed=True
        if cleanup_failed:
            print('STOP: stage=CLEANUP; category=DATABASE_RESOURCE_RELEASE_FAILED; retain child branch for inspection')
            exit_code=1
        if phase_log is not None:
            try:phase_log.record('RESOURCE_CLEANUP_FAILED' if cleanup_failed else 'RESOURCE_CLEANUP_COMPLETE',source='SUPERVISOR')
            except BaseException:
                print('STOP: stage=EVIDENCE; category=CLEANUP_EVIDENCE_WRITE_FAILED',flush=True);exit_code=1
        if watchdog is not None:watchdog.cancel()
        PREFLIGHT_DIAGNOSTICS=False
        CATALOG_DIAGNOSTICS=False
    if exit_code==0 and success_report is not None:
        try:
            phase_log.record('PASS_READY',source='SUPERVISOR')
            final_path=output/(run+'-'+args.phase+'.json')
            pending=final_path.with_suffix('.json.pending')
            with pending.open('x',encoding='utf-8') as stream:
                json.dump(success_report,stream,indent=2);stream.flush();os.fsync(stream.fileno())
            require(not final_path.exists(),'PASS_ARTIFACT_ALREADY_EXISTS')
            pending.rename(final_path)
            print('PASS: RUNNER POSTGRES '+args.phase.upper()+'; public baseline unchanged; fixtures retained on disposable child',flush=True)
        except BaseException as error:
            # Fixed categories only: raw filesystem/serialization errors may contain secrets.
            category=('PASS_ARTIFACT_ALREADY_EXISTS' if isinstance(error,Refused) and str(error)=='PASS_ARTIFACT_ALREADY_EXISTS'
                else 'PASS_PENDING_ARTIFACT_ALREADY_EXISTS' if isinstance(error,FileExistsError)
                else 'PASS_EVIDENCE_PERMISSION_DENIED' if isinstance(error,PermissionError)
                else 'PASS_EVIDENCE_SERIALIZATION_FAILED' if isinstance(error,(TypeError,ValueError))
                else 'PASS_EVIDENCE_FILESYSTEM_FAILED' if isinstance(error,OSError)
                else 'PASS_EVIDENCE_WRITE_FAILED')
            try:phase_log.record('FINALIZATION_FAILED',category=category,source='SUPERVISOR')
            except BaseException:pass
            print('STOP: stage=EVIDENCE; category='+category+'; retain child branch for inspection',flush=True)
            exit_code=1
    return exit_code

if __name__=='__main__':raise SystemExit(main())
