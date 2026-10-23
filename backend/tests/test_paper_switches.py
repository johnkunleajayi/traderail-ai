"""Server-owned persistence boundary with isolated connections; no live database."""
from dataclasses import replace
from datetime import timedelta
import importlib.util
import io
from pathlib import Path
from unittest.mock import Mock
import os
import subprocess
import sys
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects import postgresql
from app.risk import KillSwitchScope as Scope, RiskReason as R
from app.risk.api.schemas import EvaluationRequest
from app.risk.paper_switches import (PaperSwitchUnavailable, targets_for, provision_inactive, load_inventory)
from app.risk.canonical import canonical_json, values
from app.risk.safety_gate.switch_evaluation import evaluate_switches
from app.risk.safety_gate.findings import Findings
from test_safety_gate import intent, policy, AT


def connection(p, *, active=None, missing=None, changed_at=None):
    rows = [dict(target_identity=t.target_identity, environment="PAPER", scope=t.scope.value,
        target_payload=canonical_json(values(t)), active=t.scope == active,
        cause="OPERATIONS", changed_at=changed_at or AT-timedelta(seconds=1))
        for t in targets_for(p) if t.scope != missing]
    c=Mock(); c.dialect.name="postgresql"; c.in_transaction.return_value=True
    c.connection.dbapi_connection.autocommit=False
    c.get_isolation_level.return_value="READ COMMITTED"
    owner=Mock(); owner.scalar_one_or_none.return_value=p.scope.portfolio_id
    market=Mock(); market.scalar_one_or_none.return_value=p.instrument.instrument_id
    result=Mock(); result.mappings.return_value.all.return_value=rows
    c.execute.side_effect=[owner,market,result]
    return c,rows


def load(c,p,**kwargs):
    return load_inventory(c,p,user_id=p.scope.user_id,observed_at=AT,
        valid_until=AT+timedelta(seconds=20),**kwargs)


def evaluated(inv,p,at=AT):
    findings=Findings()
    result=evaluate_switches(p,inv,policy(),at,findings)
    return result,findings.freeze()


def test_complete_persisted_inactive_coverage_passes():
    p=intent(); c,_=connection(p)
    inv=load(c,p)
    assert set(inv.covered_scopes)==set(Scope)
    assert len(inv.switches)==6
    assert evaluated(inv,p)[0][2] is True
    assert all(not s.active for s in inv.switches)
    assert "FOR SHARE" in str(c.execute.call_args.args[0].compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize("scope",list(Scope))
def test_every_active_scope_blocks(scope):
    p=intent(); c,_=connection(p,active=scope)
    inv=load(c,p); result,findings=evaluated(inv,p)
    assert not result[2] and len(result[1])==1
    assert any(f.code.value==scope.value+"_KILL_SWITCH_ACTIVE" for f in findings)


@pytest.mark.parametrize("scope",list(Scope))
def test_missing_any_scope_fails_closed(scope):
    p=intent(); c,_=connection(p,missing=scope)
    with pytest.raises(PaperSwitchUnavailable): load(c,p)


def test_wrong_subject_rejected():
    p=intent(); c,rows=connection(p)
    rows[0]["target_payload"]="{}"
    with pytest.raises(PaperSwitchUnavailable): load(c,p)


def test_future_changed_state_rejected():
    p=intent(); c,_=connection(p,changed_at=AT+timedelta(seconds=1))
    with pytest.raises(PaperSwitchUnavailable): load(c,p)


def test_stale_read_rejected_by_existing_gate():
    p=intent(); c,_=connection(p); inv=load(c,p)
    result,findings=evaluated(inv,p,AT+timedelta(seconds=21))
    assert not result[2]
    assert any(f.code is R.SWITCH_EVIDENCE_STALE for f in findings)


def test_unknown_storage_fails_closed():
    p=intent(); c,_=connection(p)
    c.execute.side_effect=OperationalError("hidden",{},Exception())
    with pytest.raises(PaperSwitchUnavailable,match="unavailable"): load(c,p)


def test_wrong_owner_cannot_resolve():
    p=intent(); c,_=connection(p)
    with pytest.raises(PaperSwitchUnavailable):
        load_inventory(c,p,user_id=UUID(int=987),observed_at=AT,valid_until=AT+timedelta(seconds=20))
    c.execute.assert_not_called()


def test_missing_owned_portfolio_fails_closed():
    p=intent(); c,_=connection(p)
    result=Mock(); result.scalar_one_or_none.return_value=None
    c.execute.side_effect=[result,result]
    with pytest.raises(PaperSwitchUnavailable): load(c,p)


def test_provision_is_explicit_and_never_overwrites():
    p=intent(); c,_=connection(p); owner=Mock();owner.scalar_one_or_none.return_value=p.scope.portfolio_id
    c.execute.side_effect=None;c.execute.return_value=owner
    provision_inactive(c,p,user_id=p.scope.user_id,changed_at=AT)
    inserts=[call.args[0] for call in c.execute.call_args_list[2:]]
    assert len(inserts)==6
    for statement in inserts:
        assert "ON CONFLICT DO NOTHING" in str(statement)
        assert statement.compile().params["active"] is False


def test_http_cannot_supply_controls():
    with pytest.raises(ValidationError):
        EvaluationRequest(intent_identity="sha256:"+"a"*64, switches=[],active=False)


def test_canonical_read_identity_independent_of_row_order():
    p=intent(); c,_=connection(p); a=load(c,p)
    c,rows=connection(p); rows.reverse()
    assert load(c,p)==a


def test_four_fresh_hash_seeds():
    code = """import sys;sys.path.insert(0,'tests')
from test_paper_switches import connection,load,intent,evaluated
p=intent();c,rows=connection(p);rows.sort(key=lambda r:hash(r['target_identity']))
inv=load(c,p)
print(inv.inventory_identity,tuple(f.code.value for f in evaluated(inv,p)[1]))
"""
    results=[subprocess.check_output([sys.executable,"-B","-c",code],
        env={**os.environ,"PYTHONHASHSEED":str(seed)},text=True) for seed in (1,7,42,123)]
    assert len(set(results))==1


def test_migration_contains_no_seed_defaults_and_protects_controls():
    path=Path(__file__).parents[1]/"alembic/versions/0011_paper_kill_switches.py"
    spec=importlib.util.spec_from_file_location("switch_migration",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=io.StringIO()
    ctx=MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"output_buffer":output})
    with Operations.context(ctx): module.upgrade();module.downgrade()
    sql=output.getvalue()
    assert "active BOOLEAN NOT NULL" in sql and "DEFAULT" not in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "IF EXISTS(SELECT 1 FROM paper_kill_switch_states)" in sql
    assert len(module.revision)<=32 and module.down_revision=="0010_paper_authorizations"
