"""Independent intended-pattern checks; no physical PostgreSQL claims."""
import importlib.util
import io
from pathlib import Path
import re
from unittest.mock import Mock
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint
from app.mandate_runner.models import work


def migration():
    path=Path(__file__).parents[1]/"alembic/versions/0015_paper_work_code_checks.py"
    spec=importlib.util.spec_from_file_location("code_check_correction",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def statements():
    stream=io.StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="postgresql",
            opts={"as_sql":True,"output_buffer":stream})):
        migration().upgrade()
    return stream.getvalue()


@pytest.mark.parametrize("field",["outcome_code","reason_code"])
def test_correct_quantifier_and_model_migration_agreement(field):
    expression=str(next(c for c in work.constraints if isinstance(c,CheckConstraint)
        and c.name=="ck_paper_work_"+field).sqltext)
    expected=field+" IS NULL OR "+field+" ~ '^[A-Z][A-Z0-9_]{0,63}$'"
    assert expression==expected
    assert expected in statements()
    assert "(0, 63)" not in expression+statements()


@pytest.mark.parametrize("code,valid",[("VERIFIED",True),("TEST_ONLY",True),("A",True),
    ("A"*64,True),("",False),("a",False),("1BAD",False),("A"*65,False),
    ("HAS SPACE",False),("BAD-CODE",False),("A_\u00e9",False),("AB0, 63",False)])
def test_generated_patterns_enforce_independent_machine_code_examples(code,valid):
    for field in ("outcome_code","reason_code"):
        expression=str(next(c for c in work.constraints if c.name=="ck_paper_work_"+field).sqltext)
        pattern=expression.split(" ~ '")[1][:-1]
        assert (re.fullmatch(pattern,code) is not None)==valid
    # This shared ASCII regex subset is verified offline; PostgreSQL acceptance
    # must additionally be established in the authorized physical campaign.


def test_upgrade_only_replaces_two_constraints_without_data_mutation():
    sql=statements()
    assert migration().down_revision=="0014_paper_mandate_work"
    assert len(migration().revision)<=32
    statements_list=[item.strip() for item in sql.split(';') if item.strip()]
    assert len(statements_list)==5
    assert statements_list[0]=="LOCK TABLE paper_mandate_work IN ACCESS EXCLUSIVE MODE"
    for field in ("outcome_code","reason_code"):
        name="ck_paper_work_"+field
        assert "ALTER TABLE paper_mandate_work DROP CONSTRAINT "+name in sql
        assert "ALTER TABLE paper_mandate_work ADD CONSTRAINT "+name+" CHECK" in sql
    assert all(token not in sql for token in ("NOT VALID","DELETE","UPDATE","INSERT","DROP TABLE","TRUNCATE"))


def test_downgrade_refuses_before_any_schema_or_data_change(monkeypatch):
    module=migration();ops=Mock();monkeypatch.setattr(module,"op",ops)
    with pytest.raises(RuntimeError,match="Downgrade refused"):
        module.downgrade()
    assert ops.mock_calls==[]
