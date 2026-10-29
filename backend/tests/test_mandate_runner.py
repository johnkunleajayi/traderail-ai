"""Offline persistence/DDL contracts; physical PostgreSQL is never contacted."""
import importlib.util
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from app.mandate_runner.models import work
from app.mandate_runner.repository import ensure_work, record_attempt, record_result, work_identity, utc

AT=datetime(2026,10,26,12,1,tzinfo=timezone.utc)

def migration():
    path=Path(__file__).parents[1]/"alembic/versions/0014_paper_mandate_work.py"
    spec=importlib.util.spec_from_file_location("runner_migration",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def ddl(direction):
    output=io.StringIO()
    ctx=MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"output_buffer":output})
    with Operations.context(ctx):getattr(migration(),direction)()
    return output.getvalue()

class Connection:
    dialect=SimpleNamespace(name="postgresql")
    connection=SimpleNamespace(dbapi_connection=SimpleNamespace(autocommit=False))
    def __init__(self,row):self.row=row;self.statements=[]
    def in_transaction(self):return True
    def get_isolation_level(self):return "READ COMMITTED"
    def execute(self,statement):
        self.statements.append(statement)
        return SimpleNamespace(mappings=lambda:SimpleNamespace(one=lambda:self.row))

def row(**changes):
    value=dict(work_id=uuid4(),mandate_id=uuid4(),candle_id=uuid4(),state="PENDING",attempts=0,
        updated_at=AT,created_at=AT,fresh_until=AT+timedelta(minutes=9),retry_at=None)
    value.update(changes);return value

def test_revision_head_and_length():
    m=migration();assert m.down_revision=="0013_trading_mandates" and len(m.revision)<=32
    config=Config();config.set_main_option("script_location",str(Path(__file__).parents[1]/"alembic"))
    assert ScriptDirectory.from_config(config).get_heads()==["0015_paper_work_code_checks"]

def test_model_migration_checks_match():
    sql=ddl("upgrade")
    for constraint in work.constraints:
        if isinstance(constraint,CheckConstraint) and constraint.name not in ("ck_paper_work_outcome_code","ck_paper_work_reason_code"):
            assert str(constraint.sqltext) in sql
    assert sql.count("CREATE TABLE ")==1
    for token in ("REFERENCES trading_mandates", "REFERENCES market_candles", "REFERENCES paper_authorizations",
        "REFERENCES execution_requests", "REFERENCES paper_positions", "TIMESTAMP WITH TIME ZONE",
        "CREATE INDEX ix_paper_work_due", "BEFORE INSERT OR UPDATE OR DELETE",
        "c.close_time < NEW.created_at", "interval '1 hour 10 minutes'", "Terminal work is immutable", "Attempt must be due, fresh and recorded exactly once"):
        assert token in sql
    assert "NUMERIC" not in sql and "UPDATE paper_portfolios" not in sql

def test_unique_key_excludes_version():
    unique=next(c for c in work.constraints if isinstance(c,UniqueConstraint))
    assert tuple(unique.columns.keys())==("mandate_id","candle_id")

def test_downgrade_refuses_history():
    sql=ddl("downgrade")
    assert sql.index("LOCK TABLE")<sql.index("IF EXISTS")<sql.index("DROP TABLE")
    assert "DELETE FROM" not in sql and "TRUNCATE" not in sql

def test_identity_is_deterministic_not_versioned():
    mandate,candle=uuid4(),uuid4()
    assert work_identity(mandate,candle)==work_identity(mandate,candle)
    assert work_identity(mandate,candle)!=work_identity(mandate,uuid4())
    assert work_identity(mandate,candle)!=work_identity(uuid4(),candle)

def test_ensure_uses_atomic_conflict_and_returns_original_state():
    r=row(state="COMPLETED");r["work_id"]=work_identity(r["mandate_id"],r["candle_id"])
    c=Connection(r)
    assert ensure_work(c,r["mandate_id"],r["candle_id"],runner_version="runner-v2",created_at=AT,
        fresh_until=AT+timedelta(minutes=1)) is r
    sql=str(c.statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT uq_paper_work_mandate_candle DO NOTHING" in sql
    assert "FOR UPDATE" in str(c.statements[1].compile(dialect=postgresql.dialect()))

def test_conflicting_work_identity_rejects():
    r=row();c=Connection(r)
    with pytest.raises(ValueError,match="Conflicting"):
        ensure_work(c,r["mandate_id"],r["candle_id"],runner_version="v1",created_at=AT,fresh_until=AT+timedelta(minutes=1))

@pytest.mark.parametrize("state",["COMPLETED","SKIPPED","RECONCILIATION_REQUIRED"])
def test_terminal_work_cannot_attempt_or_rewrite(state):
    r=row(state=state);c=Connection(r)
    with pytest.raises(ValueError):record_attempt(c,r["work_id"],at=AT)
    with pytest.raises(ValueError):record_result(c,r["work_id"],state="SKIPPED",at=AT,outcome_code="STALE")

def test_attempt_increments_under_row_lock():
    r=row(attempts=2);c=Connection(r)
    record_attempt(c,r["work_id"],at=AT)
    params=c.statements[-1].compile().params
    assert params["attempts"]==3 and params["retry_at"] is None
    assert "FOR UPDATE" in str(c.statements[0])

@pytest.mark.parametrize("changes",[dict(fresh_until=AT),dict(retry_at=AT+timedelta(seconds=1)),dict(updated_at=AT+timedelta(seconds=1))])
def test_not_due_stale_future_attempt_rejects(changes):
    r=row(**changes)
    with pytest.raises(ValueError):record_attempt(Connection(r),r["work_id"],at=AT)

@pytest.mark.parametrize("state",["RETRY_WAIT","COMPLETED","SKIPPED","RECONCILIATION_REQUIRED"])
def test_supported_results_persist_codes_and_references(state):
    r=row(attempts=1);c=Connection(r)
    retry=AT+timedelta(minutes=1) if state=="RETRY_WAIT" else None
    record_result(c,r["work_id"],state=state,at=AT,outcome_code="POSITION_HELD",retry_at=retry,
        market_evidence_identity="evidence",position_id=uuid4())
    params=c.statements[-1].compile().params
    assert params["state"]==state and params["market_evidence_identity"]=="evidence"

@pytest.mark.parametrize("retry",[None,AT-timedelta(seconds=1),AT+timedelta(minutes=9)])
def test_retry_deadline_enforced(retry):
    r=row()
    with pytest.raises(ValueError):record_result(Connection(r),r["work_id"],state="RETRY_WAIT",at=AT,outcome_code="UNAVAILABLE",retry_at=retry)

@pytest.mark.parametrize("code",["credential=secret","raw error text", "", "x"*65])
def test_raw_error_text_not_accepted(code):
    r=row()
    with pytest.raises(ValueError):record_result(Connection(r),r["work_id"],state="SKIPPED",at=AT,outcome_code=code)

def test_no_financial_payload_allowed():
    r=row()
    with pytest.raises(ValueError):record_result(Connection(r),r["work_id"],state="COMPLETED",at=AT,outcome_code="DONE",cash_balance="10000")
    assert not set(work.c.keys()) & {"quantity","fee","pnl","cash_balance","capital_allocation"}

def test_utc_exactness_and_naive_rejection():
    value=AT.replace(microsecond=123456)
    assert utc(value)==value
    with pytest.raises(ValueError):utc(value.replace(tzinfo=None))

def test_transaction_required():
    c=Connection(row());c.dialect=SimpleNamespace(name="sqlite")
    with pytest.raises(ValueError):record_attempt(c,uuid4(),at=AT)

def test_no_commit_or_trading_imports():
    import app.mandate_runner.repository as repository
    source=Path(repository.__file__).read_text()
    for forbidden in (".commit(",".rollback(","app.risk", "app.execution", "httpx", "sleep("):
        assert forbidden not in source


def test_locked_work_recovery_and_cross_candle_barrier_sql():
    from app.mandate_runner.repository import load_work,barrier_work
    class C(Connection):
        def execute(self,statement):
            self.statements.append(statement)
            return SimpleNamespace(mappings=lambda:SimpleNamespace(one_or_none=lambda:None))
    c=C(row());mandate,candle=uuid4(),uuid4()
    assert load_work(c,mandate,candle) is None
    sql=str(c.statements[-1].compile(dialect=postgresql.dialect()))
    assert 'FOR UPDATE' in sql and 'mandate_id' in sql and 'candle_id' in sql
    assert barrier_work(c,mandate,lock=True) is None
    compiled=c.statements[-1].compile(dialect=postgresql.dialect())
    assert 'FOR UPDATE' in str(compiled) and 'RECONCILIATION_REQUIRED' in compiled.params.values()
    assert 'POSITION_CLOSED' in compiled.params.values()
