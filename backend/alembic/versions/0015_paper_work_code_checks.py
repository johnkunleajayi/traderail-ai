"""Correct only the two machine-code CHECK expressions installed by 0014.

Existing rows are validated under an exclusive table lock. Any incompatible
row aborts the transaction; no data is rewritten. Downgrade deliberately refuses
rather than reinstalling the known defective expressions.
"""
from alembic import op

revision = "0015_paper_work_code_checks"
down_revision = "0014_paper_mandate_work"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("LOCK TABLE paper_mandate_work IN ACCESS EXCLUSIVE MODE")
    for field in ("outcome_code", "reason_code"):
        name = "ck_paper_work_" + field
        op.drop_constraint(name, "paper_mandate_work", type_="check")
        op.create_check_constraint(name, "paper_mandate_work",
            f"{field} IS NULL OR {field} ~ '^[A-Z][A-Z0-9_]{{0,63}}$'")


def downgrade():
    raise RuntimeError("Downgrade refused: restoring defective machine-code CHECK constraints is unsafe")
