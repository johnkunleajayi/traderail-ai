"""Correct the deployed 0008 lifecycle CHECK without changing economic facts."""
from alembic import op

revision = '0009_paper_position_lifecycle'
down_revision = '0008_paper_accounting'
branch_labels = None
depends_on = None

CONSTRAINT = 'ck_paper_position_lifecycle'
ORIGINAL = "(status='OPEN' AND quantity=opening_quantity AND closed_at IS NULL AND closing_request_identity IS NULL) OR (status='CLOSED' AND quantity=0 AND closed_at>=opened_at AND closing_request_identity IS NOT NULL)"
CORRECTED = "(status='OPEN' AND quantity>0 AND quantity=opening_quantity AND closed_at IS NULL AND closing_request_identity IS NULL) OR (status='CLOSED' AND quantity=0 AND closed_at IS NOT NULL AND closed_at>=opened_at AND closing_request_identity IS NOT NULL)"


def _replace(expression):
    # PostgreSQL transactional DDL restores the old CHECK if validation fails.
    # ADD CHECK validates every existing row; never repair or coerce history.
    op.drop_constraint(CONSTRAINT, 'paper_positions', type_='check')
    op.create_check_constraint(CONSTRAINT, 'paper_positions', expression)


def upgrade():
    _replace(CORRECTED)


def downgrade():
    _replace(ORIGINAL)
