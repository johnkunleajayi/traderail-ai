"""0009 is a schema-only correction of the deployed lifecycle CHECK."""
import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint
from app.execution.paper.models import positions


def migration():
    path = Path(__file__).parents[1] / 'alembic/versions/0009_fix_paper_position_lifecycle.py'
    spec = importlib.util.spec_from_file_location('lifecycle_correction', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_matches_corrected_model_and_0008():
    m = migration()
    expected = str(next(c.sqltext for c in positions.constraints
                        if isinstance(c, CheckConstraint) and c.name == m.CONSTRAINT))
    assert m.CORRECTED == expected
    path = Path(__file__).parents[1] / 'alembic/versions/0008_paper_accounting.py'
    assert 'CHECK (' + expected + ')' in path.read_text()
    assert m.down_revision == '0008_paper_accounting'


def test_downgrade_restores_original_deployed_expression():
    m = migration()
    assert m.ORIGINAL == "(status='OPEN' AND quantity=opening_quantity AND closed_at IS NULL AND closing_request_identity IS NULL) OR (status='CLOSED' AND quantity=0 AND closed_at>=opened_at AND closing_request_identity IS NOT NULL)"


def test_both_directions_only_replace_one_check():
    m = migration()
    for direction, expression in ((m.upgrade, m.CORRECTED), (m.downgrade, m.ORIGINAL)):
        output = io.StringIO()
        ctx = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
        with Operations.context(ctx):
            direction()
        statements = [s.strip() for s in output.getvalue().split(';') if s.strip()]
        assert statements == [
            'ALTER TABLE paper_positions DROP CONSTRAINT ck_paper_position_lifecycle',
            'ALTER TABLE paper_positions ADD CONSTRAINT ck_paper_position_lifecycle CHECK (' + expression + ')',
        ]
