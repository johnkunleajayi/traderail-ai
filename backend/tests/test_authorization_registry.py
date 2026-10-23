"""Registry contracts/codec; physical PostgreSQL cases run on isolated branch."""
import importlib.util
import io
from dataclasses import replace,FrozenInstanceError
from datetime import timedelta,timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
import os,sys,subprocess,json
from uuid import UUID
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.execution.authorization import ExecutionAuthorization
from app.execution.registry.codec import encode,decode
from app.execution.registry.contracts import AuthorizationPackage,ExecutionEvidenceReferences
from app.risk.integrated.inspection import inspect_risk
from app.risk.authorization_service import evaluate_and_register
from app.execution.registry.repository import AuthorizationUnavailable
from app.risk.canonical import to_wire
from app.risk.vocabulary import RiskOutcome
from test_paper_execution import empty_book,command_for
import test_paper_execution as fixtures


def sample(book=None,**kwargs):
    collected=[];original=fixtures.inspect_risk
    def inspect(i):
        collected.append(i);return original(i)
    fixtures.inspect_risk=inspect
    try:command=command_for(book or empty_book(),**kwargs)
    finally:fixtures.inspect_risk=original
    i=collected[0]
    refs=ExecutionEvidenceReferences(i.market_context.data_identity,i.market_context.context_identity,
        i.sizing_assumptions.costs.costs_identity,i.snapshot.valuation_identity)
    return AuthorizationPackage(command.request.authorization,i,refs),command


def test_lossless_reconstruction_and_original_command():
    p,c=sample()
    for obj in (p,c):
        restored=decode(encode(obj))
        assert restored==obj and encode(restored)==encode(obj)
        assert to_wire(restored)==to_wire(obj)
    assert decode(encode(p)).authorization.quantity==p.authorization.quantity
    assert decode(encode(p)).authorization.decision.approval_expires_at==p.authorization.decision.approval_expires_at


def test_all_four_gates_and_approved_only():
    p,_=sample();i=p.inputs
    rejected=replace(i,sizing_assumptions=replace(i.sizing_assumptions,proposed_quantity=Decimal('999999')))
    report=inspect_risk(rejected)
    assert report.decision.outcome is RiskOutcome.REJECTED
    with pytest.raises(ValueError):ExecutionAuthorization(i.intent,report)
    assert len(p.authorization.report.gates)==4


def test_rejected_workflow_never_writes(monkeypatch):
    p,_=sample();i=replace(p.inputs,sizing_assumptions=replace(p.inputs.sizing_assumptions,proposed_quantity=Decimal('999999')))
    write=Mock();monkeypatch.setattr('app.risk.authorization_service.persist_authorization',write)
    report,saved=evaluate_and_register(Mock(),i,user_id=i.intent.scope.user_id,evidence_references=p.evidence_references)
    assert report.decision.outcome is RiskOutcome.REJECTED and saved is None
    write.assert_not_called()


def test_approved_workflow_passes_exact_package_to_transaction(monkeypatch):
    p,_=sample();write=Mock(side_effect=lambda c,p,**kw:p)
    monkeypatch.setattr('app.risk.authorization_service.persist_authorization',write)
    connection=Mock()
    report,saved=evaluate_and_register(connection,p.inputs,user_id=p.inputs.intent.scope.user_id,evidence_references=p.evidence_references)
    assert report.decision==p.authorization.decision and saved==p
    assert {g.record_identity for g in report.gates}=={g.record_identity for g in p.authorization.report.gates}
    write.assert_called_once_with(connection,p,user_id=p.inputs.intent.scope.user_id,as_of=p.inputs.evaluated_at)


def test_owner_is_checked_before_evaluation():
    p,_=sample();connection=Mock()
    with pytest.raises(AuthorizationUnavailable):
        evaluate_and_register(connection,p.inputs,user_id=UUID(int=999),evidence_references=p.evidence_references)
    connection.execute.assert_not_called()


def test_immutable_and_bound_evidence():
    p,_=sample()
    with pytest.raises(FrozenInstanceError):p.registry_identity='other'
    for name in ('market_source_identity','market_context_identity','costs_identity','valuation_identity'):
        with pytest.raises(ValueError):
            replace(p,evidence_references=replace(p.evidence_references,**{name:'sha256:'+'f'*64}))


def test_unknown_types_and_floats_are_rejected():
    with pytest.raises(ValueError):decode(['record','os.system',{}])
    with pytest.raises(ValueError):encode(1.5)
    with pytest.raises(ValueError):decode(['decimal','NaN'])


def test_derived_identity_tampering_fails_closed():
    p,_=sample();wire=encode(p)
    wire[2]['registry_identity']=['scalar','sha256:'+'f'*64]
    with pytest.raises(ValueError):decode(wire)


def test_exact_decimal_and_utc_values_in_free_evidence():
    value={'tiny':Decimal('0.00000000000000000000000100'),'text':'1.00',
        'instant':sample()[0].inputs.evaluated_at.astimezone(timezone(timedelta(hours=1)))}
    restored=decode(encode(value))
    assert restored['tiny'].as_tuple()==value['tiny'].as_tuple() and restored['text']=='1.00'
    assert restored['instant']==value['instant'] and restored['instant'].utcoffset()==timedelta(0)


def test_inputs_manifest_cannot_be_rebound():
    p,_=sample()
    changed=replace(p.inputs,health_evidence=replace(p.inputs.health_evidence,source_identity='sha256:'+'f'*64))
    with pytest.raises(ValueError,match='manifest'):
        replace(p,inputs=changed)


def test_equivalent_decimal_configuration_preserves_registry_identity():
    from test_paper_fill import costs
    a,_=sample(cost=costs(trading_fee_rate=Decimal('.01')))
    b,_=sample(cost=costs(trading_fee_rate=Decimal('.010000')))
    assert a.registry_identity==b.registry_identity


@pytest.mark.parametrize('field,value',[('quantity',Decimal('2')),('outcome','REJECTED'),('user_id',UUID(int=999))])
def test_loaded_metadata_must_match_full_package(field,value):
    from app.execution.registry.repository import _auth_row,load_authorization,RegistryUnavailable
    p,_=sample();row=_auth_row(p);row[field]=value
    c=Mock();c.execute.return_value.mappings.return_value.one_or_none.return_value=row
    with pytest.raises(RegistryUnavailable):load_authorization(c,p.registry_identity,user_id=p.inputs.intent.scope.user_id)


def test_changed_payload_conflicts_before_insert():
    from app.execution.registry.repository import _auth_row,persist_authorization,RegistryConflict
    p,_=sample();row=_auth_row(p);row['canonical_payload']='changed'
    owner=Mock();owner.scalar_one_or_none.return_value=p.inputs.intent.scope.portfolio_id
    stored=Mock();stored.mappings.return_value.one_or_none.return_value=row
    c=Mock();c.dialect.name='postgresql';c.in_transaction.return_value=True
    c.connection.dbapi_connection.autocommit=False;c.get_isolation_level.return_value='READ COMMITTED'
    c.begin_nested.return_value.__enter__=Mock();c.begin_nested.return_value.__exit__=Mock(return_value=False)
    c.execute.side_effect=[owner,stored]
    with pytest.raises(RegistryConflict):persist_authorization(c,p,user_id=p.inputs.intent.scope.user_id,as_of=p.inputs.evaluated_at)
    assert c.execute.call_count==2


def test_four_fresh_hash_seeds_preserve_registry_and_reconstruction():
    script="""import sys,json
sys.path.insert(0,'tests')
from test_authorization_registry import sample
from app.execution.registry.codec import encode,decode
p,c=sample()
print(json.dumps([p.registry_identity,decode(encode(p)).registry_identity,c.command_identity],sort_keys=True))
"""
    results=[]
    for seed in ('1','2','3','4'):
        r=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        results.append(json.loads(r.stdout))
    assert all(x==results[0] for x in results)


def test_frozen_migration_only_adds_registry_and_guards_downgrade():
    path=Path(__file__).parents[1]/'alembic/versions/0010_paper_authorizations.py'
    spec=importlib.util.spec_from_file_location('registry_migration',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.down_revision=='0009_paper_position_lifecycle' and len(m.revision)<=32
    output=io.StringIO();ctx=MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})
    with Operations.context(ctx):m.upgrade();m.downgrade()
    sql=output.getvalue()
    assert 'CREATE TABLE paper_authorizations' in sql and 'CREATE TABLE paper_authorized_commands' in sql
    assert 'FOREIGN KEY(portfolio_id, user_id)' in sql
    assert sql.count('CREATE TRIGGER immutable_authorization')==2
    assert sql.index('EXISTS(SELECT 1 FROM paper_authorizations)')<sql.index('DROP TABLE paper_authorized_commands')
    assert not any(x in sql for x in ('DELETE FROM','TRUNCATE','UPDATE paper_'))
