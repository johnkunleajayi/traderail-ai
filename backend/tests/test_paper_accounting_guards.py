"""Frozen migration/model safety regressions; physical cases verified separately."""
import importlib.util
import io
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint
from app.execution.paper.models import positions


def migration():
    path=Path(__file__).parents[1]/"alembic/versions/0008_paper_accounting.py"
    spec=importlib.util.spec_from_file_location("paper_guard_regression",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_downgrade_checks_both_economic_tables_before_any_drop():
    output=io.StringIO()
    context=MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"output_buffer":output})
    with Operations.context(context):migration().downgrade()
    sql=output.getvalue()
    guard=sql[:sql.index("DROP TABLE")]
    assert "EXISTS(SELECT 1 FROM paper_positions)" in guard
    assert "EXISTS(SELECT 1 FROM paper_accounting_entries)" in guard
    assert "WHERE" not in guard.split("EXISTS(SELECT 1 FROM paper_positions)")[0]
    assert "cash_balance<>round(cash_balance,2)" in guard
    assert "action='EXIT_LONG'" in guard
    assert "RAISE EXCEPTION" in guard
    assert not any(word in sql for word in ("DELETE FROM", "UPDATE ", "TRUNCATE", "INSERT INTO"))


def test_downgrade_guard_has_no_status_filter_for_open_closed_or_entry_only_history():
    class Recorder:
        def __init__(self):self.sql=[];self.drops=[]
        def execute(self,sql):self.sql.append(sql)
        def drop_table(self,name):self.drops.append(name)
        def __getattr__(self,name):return lambda *a,**kw:None
    m=migration();r=Recorder();m.op=r;m.downgrade()
    assert r.sql[0].index("EXISTS(SELECT 1 FROM paper_positions)")<r.sql[0].index("EXISTS(SELECT 1 FROM execution_requests")
    assert "EXISTS(SELECT 1 FROM paper_accounting_entries)" in r.sql[0]
    assert "status=" not in r.sql[0] and "action='ENTER_LONG'" not in r.sql[0]
    assert r.drops==["paper_accounting_entries","paper_positions"]


def test_closed_lifecycle_explicitly_rejects_unknown_closure_timestamp():
    sql=str(next(x.sqltext for x in positions.constraints if isinstance(x,CheckConstraint) and x.name=="ck_paper_position_lifecycle"))
    expected="(status='OPEN' AND quantity>0 AND quantity=opening_quantity AND closed_at IS NULL AND closing_request_identity IS NULL) OR (status='CLOSED' AND quantity=0 AND closed_at IS NOT NULL AND closed_at>=opened_at AND closing_request_identity IS NOT NULL)"
    assert sql==expected
    assert "CHECK ("+expected+")" in migration().TABLE_SQL[0]
