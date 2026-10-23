"""PostgreSQL DDL generation; physical verification is a separate guarded step."""
import importlib.util
import io
from pathlib import Path
from alembic.operations import Operations
from alembic.migration import MigrationContext


def test_controlled_currency_migration_refuses_economic_history_and_preserves_cash():
    path=Path(__file__).parents[1]/'alembic/versions/0012_paper_usdt_risk.py'
    spec=importlib.util.spec_from_file_location('paper_usdt_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=io.StringIO()
    context=MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})
    with Operations.context(context):module.upgrade()
    sql=output.getvalue()
    assert module.down_revision=='0011_paper_kill_switches' and len(module.revision)<=32
    assert sql.index('LOCK TABLE')<sql.index('Active USD Paper history')<sql.index("SET base_currency='USDT'")
    for name in ('paper_positions','paper_accounting_entries','paper_authorizations','execution_requests'):
        assert 'EXISTS(SELECT 1 FROM '+name in sql
    assert 'cash_balance<>' in sql and 'starting_balance<>' in sql
    assert 'SET cash_balance' not in sql and 'SET starting_balance' not in sql
    assert "currency IN ('USD','USDT')" in sql
    assert 'BEFORE UPDATE OR DELETE' in sql


def test_downgrade_cannot_destroy_activation_or_economic_facts():
    path=Path(__file__).parents[1]/'alembic/versions/0012_paper_usdt_risk.py'
    spec=importlib.util.spec_from_file_location('paper_usdt_migration_down',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=io.StringIO()
    context=MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})
    with Operations.context(context):module.downgrade()
    sql=output.getvalue()
    assert sql.index('prevents downgrade')<sql.index('DROP TABLE')
    assert 'EXISTS(SELECT 1 FROM paper_risk_initializations)' in sql
    assert 'EXISTS(SELECT 1 FROM paper_strategy_activations)' in sql
    assert 'SET cash_balance' not in sql
