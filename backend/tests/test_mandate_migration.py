"""Offline PostgreSQL DDL, never a physical database connection."""
import importlib.util
import io
from pathlib import Path
from alembic.operations import Operations
from alembic.migration import MigrationContext
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint
from app.mandates.models import TradingMandate

def migration():
    path=Path(__file__).parents[1]/"alembic/versions/0013_trading_mandates.py"
    spec=importlib.util.spec_from_file_location("mandate_migration",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def ddl(direction):
    buffer=io.StringIO()
    ctx=MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"output_buffer":buffer})
    with Operations.context(ctx):getattr(migration(),direction)()
    return buffer.getvalue()

def test_revision_chain():
    m=migration();assert m.down_revision=="0012_paper_usdt_risk" and len(m.revision)<=32
    cfg=Config();cfg.set_main_option("script_location",str(Path(__file__).parents[1]/"alembic"))
    assert ScriptDirectory.from_config(cfg).get_revision(m.revision).down_revision==m.down_revision

def test_upgrade_matches_model_only_adds_mandates():
    sql=ddl("upgrade")
    assert sql.count("CREATE TABLE ")==1 and "CREATE TABLE trading_mandates" in sql
    for constraint in TradingMandate.__table__.constraints:
        if isinstance(constraint,CheckConstraint):assert str(constraint.sqltext) in sql
    for token in ["REFERENCES users (id) ON DELETE RESTRICT","REFERENCES instruments (canonical_symbol) ON DELETE RESTRICT","capital_allocation NUMERIC NOT NULL","TIMESTAMP WITH TIME ZONE","CREATE INDEX ix_trading_mandates_owner_created","BEFORE INSERT OR UPDATE OR DELETE","NEW.user_id","NEW.created_at","Mandates must begin as DRAFT","Only DRAFT or PAUSED intent can be edited","Retire mandates","GREATEST(statement_timestamp(), OLD.updated_at)"]:assert token in sql,token
    assert "ALTER TABLE" not in sql and "UPDATE paper_" not in sql and "execution_" not in sql

def test_downgrade_preserves_persisted_intent():
    sql=ddl("downgrade")
    assert sql.index("LOCK TABLE")<sql.index("IF EXISTS(SELECT 1 FROM trading_mandates)")<sql.index("DROP TABLE")
    assert "DELETE FROM" not in sql and "DROP FUNCTION traderail_mandate_guard()" in sql
