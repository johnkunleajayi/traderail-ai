"""Explicit process resources, bounded I/O, signals and sanitized JSON logs."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import signal
import sys
from threading import Event
from uuid import UUID
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from app.market_data.binance import BinanceHistoricalProvider
from .config import RunnerSettings
from .process import PaperRunner


def emit(event,**fields):
    record={'event':event if re.fullmatch(r'[A-Z_]{1,64}',event) else 'INVALID_EVENT',
            'timestamp':datetime.now(timezone.utc).isoformat()}
    for key in ('mandate_id','work_id'):
        value=fields.get(key)
        if isinstance(value,UUID):record[key]=str(value)
    for key in ('state','outcome_code','reason_code'):
        value=fields.get(key)
        if value is not None:
            record[key]=value if isinstance(value,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',value) else 'REDACTED'
    for key in ('persisted','reused'):
        if isinstance(fields.get(key),bool):record[key]=fields[key]
    logging.getLogger('traderail.paper_runner').info(json.dumps(record,sort_keys=True))


def make_engine(settings):
    # Separate from the HTTP engine; no FastAPI imports or global startup hooks.
    return create_engine(str(settings.database_url),poolclass=NullPool,echo=False,hide_parameters=True,
        isolation_level='READ COMMITTED',connect_args={
            'connect_timeout':settings.paper_runner_connect_timeout_seconds,
            'options':f'-c statement_timeout={settings.paper_runner_statement_timeout_ms} '
                      f'-c lock_timeout={settings.paper_runner_lock_timeout_ms} '
                      f'-c idle_in_transaction_session_timeout={settings.paper_runner_idle_transaction_timeout_ms}'})


def verify_schema(engine):
    # Read-only preflight, never Alembic upgrade or schema initialization.
    with engine.connect() as c:
        if c.dialect.name!='postgresql':raise RuntimeError('PostgreSQL required')
        revisions=c.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
        if revisions!=['0015_paper_work_code_checks']:raise RuntimeError('Runner schema revision required')
        present=c.execute(text("""SELECT to_regclass('paper_mandate_work') IS NOT NULL
            AND to_regprocedure('traderail_paper_work_guard()') IS NOT NULL
            AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('paper_mandate_work')
                AND tgname='paper_work_guard' AND tgenabled='O' AND NOT tgisinternal)
            AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('paper_mandate_work')
                AND conname='uq_paper_work_mandate_candle' AND contype='u')""")).scalar_one()
        if present is not True:raise RuntimeError('Runner schema protections required')


def provider_factory(settings):
    @contextmanager
    def provider():
        with httpx.Client(timeout=httpx.Timeout(settings.paper_runner_network_timeout_seconds,
                connect=min(3,settings.paper_runner_network_timeout_seconds)),trust_env=False,
                follow_redirects=False) as client:
            yield BinanceHistoricalProvider(client)
    return provider


@contextmanager
def shutdown_signals(stop):
    previous={}
    def request_stop(signum,frame):stop.set()
    try:
        for signum in (signal.SIGINT,signal.SIGTERM):
            previous[signum]=signal.signal(signum,request_stop)
        yield
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Explicitly launched PAPER mandate runner; disabled by default.')
    parser.add_argument('--env-file',default=None,help='Explicit operator dotenv file; otherwise environment variables only.')
    args=parser.parse_args(argv)
    logger=logging.getLogger('traderail.paper_runner');logger.setLevel(logging.INFO);logger.propagate=False
    handler=logging.StreamHandler(sys.stdout);handler.setFormatter(logging.Formatter('%(message)s'));logger.addHandler(handler)
    engine=None
    try:
        if args.env_file is not None and not Path(args.env_file).is_file():
            raise ValueError("Explicit configuration file is unavailable")
        settings=RunnerSettings(_env_file=args.env_file)
        if not settings.paper_runner_enabled:
            emit('RUNNER_DISABLED');return 0
        engine=make_engine(settings);verify_schema(engine)
        stop=Event()
        with shutdown_signals(stop):
            runner=PaperRunner(sessionmaker(bind=engine),settings,stop,emit,provider_factory=provider_factory(settings))
            runner.serve()
        return 0
    except KeyboardInterrupt:
        emit('RUNNER_STOPPED');return 0
    except Exception:
        emit('RUNNER_STARTUP_FAILED',reason_code='CONFIGURATION_SCHEMA_OR_RUNTIME_UNAVAILABLE')
        return 2
    finally:
        if engine is not None:engine.dispose()
        logger.removeHandler(handler);handler.close()
