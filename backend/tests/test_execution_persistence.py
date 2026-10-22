"""11C unit/SQL contract tests. No physical PostgreSQL or alternate DB is used.

The recording transaction double checks repository statements and controlled conflict
interleavings, not PostgreSQL's implementation of locks or physical round-trips.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace, FrozenInstanceError
from datetime import timedelta, timezone
from decimal import Decimal
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sqlalchemy import UniqueConstraint, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.dml import Insert, Update
from sqlalchemy.exc import IntegrityError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.execution import Fill, Order, OrderState
from app.execution.persistence import *
from app.execution.persistence import models as m
from app.execution.persistence.records import request_row, consumption_row, order_row, fill_row
from app.execution.persistence.repository import _put
from test_execution_domain import request
from test_paper_fill import calculate, costs, market
from test_integrated_risk import AT
D=Decimal
TABLES=(m.requests,m.consumptions,m.orders,m.fills)


def bundle(*, req=None, full=False, at=AT):
    r=req or request()
    created=Order(r,OrderState.CREATED,at,None,None,())
    if not full: return ExecutionArtifacts(r,at,(created,))
    c=calculate(r,executed_at=at+timedelta(seconds=1),evaluated_at=at+timedelta(seconds=1))
    f=Fill(r,'PAPER_FIXTURE_FILL',r.quantity,c.leg.modeled_execution_price,c.executed_at)
    accepted=Order(r,OrderState.ACCEPTED,at,at,None,())
    filled=Order(r,OrderState.FILLED,c.executed_at,at,None,(f,))
    return ExecutionArtifacts(r,at,(created,accepted,filled),(FillAudit(f,c),))


class Result:
    def __init__(self,row=None): self.row=row
    def scalar_one_or_none(self): return self.row
    def mappings(self): return self
    def one_or_none(self): return self.row


class RecordingTransaction:
    """SQL recording unit double; deliberately no connection/driver escape hatch."""
    dialect=SimpleNamespace(name='postgresql')
    def __init__(self):
        self.rows={t.name:{} for t in TABLES};self.statements=[];self.active=True;self.fail_at=None
        self.connection=SimpleNamespace(dbapi_connection=SimpleNamespace(autocommit=False))
    def in_transaction(self): return self.active
    def get_isolation_level(self): return 'READ COMMITTED'
    @contextmanager
    def begin_nested(self):
        saved=deepcopy(self.rows)
        try: yield
        except BaseException:
            self.rows=saved
            raise
    def execute(self,statement):
        self.statements.append(statement)
        if self.fail_at==len(self.statements): raise IntegrityError('private SQL',{},Exception('private credentials'))
        table=statement.table if isinstance(statement,(Insert,Update)) else statement.get_final_froms()[0]
        rows=self.rows[table.name];key=list(table.primary_key)[0].name
        if isinstance(statement,Insert):
            row={col:v.value for col,v in statement._values.items()}
            unique=[[c.name for c in table.primary_key]]+[[c.name for c in u.columns] for u in table.constraints if isinstance(u,UniqueConstraint)]
            if any(all(old[c]==row[c] for c in group) for old in rows.values() for group in unique): return Result()
            rows[row[key]]=row;return Result(row[key])
        criterion=statement._where_criteria[0]
        found=next((r for r in rows.values() if r[criterion.left.name]==criterion.right.value),None)
        if isinstance(statement,Update):
            if found: found.update({col:v.value for col,v in statement._values.items()})
            return Result()
        return Result(deepcopy(found))


def test_first_consumption_and_exact_retry_have_identical_receipt_and_counts():
    db=RecordingTransaction();b=bundle(full=True)
    first=persist_artifacts(db,b);before=deepcopy(db.rows)
    assert persist_artifacts(db,b)==first and db.rows==before
    assert [len(db.rows[t.name]) for t in TABLES]==[1,1,1,1]
    assert first.request_identity==b.request.request_identity
    assert first.decision_identity==b.request.risk_decision_identity
    assert first.fill_identities==(b.fills[0].fill.fill_identity,)


def test_creation_and_explicit_lifecycle_progression_are_separate_storage_actions():
    db=RecordingTransaction();b=bundle()
    persist_artifacts(db,b)
    persist_artifacts(db,bundle(req=b.request,full=True))
    assert next(iter(db.rows[m.orders.name].values()))['state']=='FILLED'
    with pytest.raises(PersistenceConflict): persist_artifacts(db,b)


@pytest.mark.parametrize('change',['time','venue'])
def test_changed_payload_under_same_economic_action_conflicts(change):
    db=RecordingTransaction();b=bundle();persist_artifacts(db,b);before=deepcopy(db.rows)
    r=replace(b.request,**({'created_at':AT+timedelta(seconds=1)} if change=='time' else {'venue_identity':'OTHER'}))
    assert r.action_identity==b.request.action_identity
    with pytest.raises(PersistenceConflict): persist_artifacts(db,bundle(req=r,at=r.created_at))
    assert db.rows==before


def test_same_approval_cannot_bind_a_different_request_or_action():
    db=RecordingTransaction();b=bundle();r=b.request
    row=consumption_row(r,AT);_put(db,m.requests,request_row(r),'request_identity')
    _put(db,m.consumptions,row,'consumption_identity')
    with pytest.raises(PersistenceConflict):
        _put(db,m.consumptions,dict(row,request_identity='sha256:'+'f'*64,payload_identity='sha256:'+'a'*64),'consumption_identity')


@pytest.mark.parametrize('table,key,mutate',[
    (m.orders,'order_identity','order_identity'),(m.fills,'fill_identity','fill_identity'),
    (m.fills,'fill_identity','payload_identity')])
def test_duplicate_order_client_fill_or_calculation_identity_cannot_change_meaning(table,key,mutate):
    db=RecordingTransaction();b=bundle(full=True);persist_artifacts(db,b)
    row=order_row(b.observations[-1]) if table is m.orders else fill_row(b.fills[0])
    row[mutate]='sha256:'+'f'*64
    with pytest.raises(PersistenceConflict): _put(db,table,row,key)


@pytest.mark.parametrize('step',range(1,11))
def test_failure_at_each_statement_rolls_back_all_partial_artifacts(step):
    db=RecordingTransaction();db.fail_at=step
    with pytest.raises(PersistenceUnavailable,match='Execution persistence failed') as error:
        persist_artifacts(db,bundle(full=True))
    assert all(not rows for rows in db.rows.values())
    assert 'private' not in str(error.value)


def test_outer_transaction_still_owns_commit_and_can_rollback_everything():
    db=RecordingTransaction();saved=deepcopy(db.rows)
    with pytest.raises(RuntimeError):
        with db.begin_nested():
            persist_artifacts(db,bundle(full=True))
            raise RuntimeError('outer caller rollback')
    assert db.rows==saved
    assert not hasattr(db,'commit')


def test_competing_same_request_writer_observes_winner_and_does_not_duplicate():
    # Deterministic race-response contract: loser INSERT returns no row, then
    # its new READ COMMITTED SELECT observes the winner. Real PG lock testing deferred.
    db=RecordingTransaction();b=bundle();winner=persist_artifacts(db,b)
    first_statements=len(db.statements)
    assert persist_artifacts(db,b)==winner
    retry=db.statements[first_statements:]
    assert all(s._post_values_clause is not None for s in retry if isinstance(s,Insert))
    assert len(db.rows[m.consumptions.name])==1


def test_competing_conflicting_approval_cannot_get_a_second_consumption():
    db=RecordingTransaction();b=bundle();persist_artifacts(db,b)
    loser=consumption_row(b.request,AT+timedelta(seconds=1))
    with pytest.raises(PersistenceConflict): _put(db,m.consumptions,loser,'consumption_identity')
    assert len(db.rows[m.consumptions.name])==1


def test_lossless_decimal_and_utc_bindings_and_link_reconstruction():
    b=bundle(full=True);row=fill_row(b.fills[0]);db=RecordingTransaction();persist_artifacts(db,b)
    assert row['execution_price']==D('102') and row['fee']==D('1.02')
    assert row['gross_notional']==D('102') and row['net_cash_effect']==D('-103.02')
    for name in ('quantity','execution_price','reference_price','fee','net_cash_effect'):
        assert type(row[name]) is Decimal
        assert m.fills.c[name].type.scale is None and m.fills.c[name].type.precision is None
    assert row['filled_at'].utcoffset()==timedelta(0)
    assert m.fills.c.filled_at.type.timezone
    assert row['evidence']['calculation']['leg']['pricing']['fee_amount']==format(b.fills[0].calculation.leg.fee,'f')
    # Detached audit serialization retains the exact tiny/high precision string.
    from app.execution.persistence.records import audit_fields
    payload={'price':D('0.0000000000001234567890123456789')}
    assert '1234567890123456789e-31' in audit_fields('fixture',payload)['canonical_payload']
    req=db.rows[m.requests.name][row['request_identity']]
    order=db.rows[m.orders.name][row['order_identity']]
    consumption=next(iter(db.rows[m.consumptions.name].values()))
    assert order['request_identity']==req['request_identity']==consumption['request_identity']
    assert req['decision_identity']==consumption['decision_identity']
    assert req['intent_identity']==consumption['intent_identity']
    assert req['user_id']==b.request.authorization.intent.scope.user_id


@pytest.mark.parametrize('problem',['missing_lifecycle','list','noncreated','missing_audit','duplicate_audit','wrong_time','expired'])
def test_invalid_lifecycle_and_incomplete_evidence_fail_closed(problem):
    b=bundle(full=True)
    with pytest.raises(ValueError):
        if problem=='missing_lifecycle': replace(b,observations=())
        elif problem=='list': replace(b,observations=list(b.observations))
        elif problem=='noncreated': replace(b,observations=b.observations[1:])
        elif problem=='missing_audit': replace(b,fills=())
        elif problem=='duplicate_audit': replace(b,fills=b.fills*2)
        elif problem=='wrong_time': replace(b,consumed_at=AT+timedelta(seconds=1))
        else: replace(b,consumed_at=AT+timedelta(days=1))


@pytest.mark.parametrize('field,value', [('price',D('99')),('quantity',D('.5')),('filled_at',AT)])
def test_fill_must_exactly_match_existing_11b_evidence(field,value):
    b=bundle(full=True);a=b.fills[0]
    with pytest.raises(ValueError): FillAudit(replace(a.fill,**{field:value}),a.calculation)


def test_mismatched_request_cannot_reuse_another_requests_order_or_audit():
    b=bundle(full=True);other=request(venue='ANOTHER')
    with pytest.raises(ValueError): replace(b,request=other)
    with pytest.raises(ValueError): FillAudit(replace(b.fills[0].fill,request=other),b.fills[0].calculation)


def test_invalid_transition_is_not_repaired_by_storage():
    b=bundle(full=True)
    with pytest.raises(ValueError): replace(b,observations=(b.observations[0],b.observations[-1]))


def test_database_constraints_bind_single_use_and_exact_relations():
    def uniques(table): return {tuple(c.name for c in u.columns) for u in table.constraints if isinstance(u,UniqueConstraint)}
    assert ('decision_identity',) in uniques(m.consumptions)
    assert ('action_identity',) in uniques(m.requests) and ('client_order_identity',) in uniques(m.orders)
    assert ('calculation_identity',) in uniques(m.fills)
    for t in TABLES:
        sql=str(CreateTable(t).compile(dialect=dialect()))
        assert 'TIMESTAMP WITH TIME ZONE' in sql
        assert 'NUMERIC(' not in sql
    assert any(isinstance(c,ForeignKeyConstraint) and len(c.columns)==7 for c in m.consumptions.constraints)
    assert 'RESTRICT' in str(CreateTable(m.fills).compile(dialect=dialect()))


def migration_module():
    path=Path(__file__).parents[1]/'alembic/versions/0007_execution_artifacts.py'
    spec=importlib.util.spec_from_file_location('migration_11c',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def migration_sql(direction):
    module=migration_module();output=io.StringIO()
    context=MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})
    with Operations.context(context): getattr(module,direction)()
    return output.getvalue()


def test_migration_upgrade_downgrade_compile_offline_and_match_models():
    module=migration_module();up=migration_sql('upgrade');down=migration_sql('downgrade')
    assert module.down_revision=='0006_market_candles'
    assert module.revision=='0007_execution_artifacts'
    assert up.count('CREATE TABLE ')==4 and down.count('DROP TABLE ')==4
    assert up.count('CREATE TRIGGER ')==5
    assert 'BEFORE UPDATE OR DELETE' in up and "consumed_at < r.created_at" in up
    assert up.index('CREATE TABLE execution_requests')<up.index('CREATE TABLE execution_approval_consumptions')
    assert down.index('DROP TABLE execution_fills')<down.index('DROP TABLE execution_requests')
    for t in TABLES:
        current=str(CreateTable(t).compile(dialect=dialect()))
        legacy=current.replace(" OR (direction='LONG' AND side='SELL' AND action='EXIT_LONG')", "")
        assert legacy==str(CreateTable(module.Base.metadata.tables[t.name]).compile(dialect=dialect()))


def test_guard_is_unchanged_and_no_physical_db_opt_in_exists():
    from conftest import TestDatabaseSafetyError
    from sqlalchemy import create_engine
    with pytest.raises(TestDatabaseSafetyError):
        create_engine('postgresql+psycopg://dummy:dummy@127.0.0.1:1/test').connect()


@pytest.mark.parametrize('setting',['inactive','nonpostgres','isolation'])
def test_explicit_supported_transaction_required(setting):
    db=RecordingTransaction()
    if setting=='inactive': db.active=False
    if setting=='nonpostgres': db.dialect=SimpleNamespace(name='sqlite')
    if setting=='isolation': db.get_isolation_level=lambda:'SERIALIZABLE'
    with pytest.raises(ValueError): persist_artifacts(db,bundle())
    assert not db.statements


def test_immutable_input_and_receipt():
    b=bundle();receipt=persist_artifacts(RecordingTransaction(),b)
    with pytest.raises(FrozenInstanceError): b.consumed_at=AT
    with pytest.raises(FrozenInstanceError): receipt.order_identity='changed'


def test_storage_contains_no_execution_portfolio_provider_or_clock_calls():
    import ast
    folder=Path(__file__).parents[1]/'app/execution/persistence'
    for path in folder.glob('*.py'):
        for n in ast.walk(ast.parse(path.read_text())):
            if isinstance(n,ast.Call):
                name=ast.unparse(n.func)
                assert not name.endswith(('.commit','.connect','.now','.utcnow','.submit','calculate_paper_fill','price_fill'))
                assert name not in ('Order','Fill','float')
            if isinstance(n,ast.ImportFrom):
                assert not any(x in (n.module or '') for x in ('app.portfolio','app.market_data','fastapi','httpx','app.venues'))


def test_four_hash_seeds_have_identical_artifact_and_row_identities():
    script="""import json,sys
sys.path.insert(0,'tests')
from test_execution_persistence import bundle
from app.execution.persistence.records import request_row,fill_row
b=bundle(full=True)
print(json.dumps({'artifact':b.artifact_identity,'request':request_row(b.request)['payload_identity'],'fill':fill_row(b.fills[0])['payload_identity']},sort_keys=True))
"""
    results=[]
    for seed in ('1','2','3','4'):
        r=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        results.append(json.loads(r.stdout))
    assert all(x==results[0] for x in results)



def test_equivalent_utc_consumption_and_numeric_representations_preserve_identity():
    from app.execution.persistence.records import audit_fields
    b=bundle();other=replace(b,consumed_at=AT.astimezone(timezone(timedelta(hours=1))))
    assert b.artifact_identity==other.artifact_identity
    assert persist_artifacts(RecordingTransaction(),b)==persist_artifacts(RecordingTransaction(),other)
    assert audit_fields('fixture',{'q':D('1.000'),'rate':D('0.0100')})==audit_fields('fixture',{'rate':D('.01'),'q':D('1')})


def test_later_unchanged_terminal_observation_preserves_immutable_financial_rows():
    b=bundle(full=True);db=RecordingTransaction();persist_artifacts(db,b)
    before=deepcopy(db.rows[m.fills.name]);last=replace(b.observations[-1],observed_at=AT+timedelta(seconds=2))
    later=replace(b,observations=b.observations+(last,))
    persist_artifacts(db,later)
    assert db.rows[m.fills.name]==before
    assert next(iter(db.rows[m.orders.name].values()))['observation_identity']==last.observation_identity


def test_storage_does_not_recalculate_fills_or_invoke_any_venue(monkeypatch):
    b=bundle(full=True)
    def forbidden(*a,**kw): raise AssertionError('pricing or economic simulation was called')
    import app.execution.paper_fill.engine as engine
    import app.execution.paper_fill.pricing as pricing
    monkeypatch.setattr(engine,'calculate_paper_fill',forbidden)
    monkeypatch.setattr(pricing,'price_fill',forbidden)
    assert persist_artifacts(RecordingTransaction(),b).fill_identities


def test_invalid_current_state_cannot_be_spliced_into_lifecycle_history():
    b=bundle(full=True);db=RecordingTransaction();persist_artifacts(db,b)
    alternative=replace(b.observations[-1],observed_at=AT+timedelta(seconds=2))
    shortened=replace(b,observations=b.observations[:-1]+(alternative,))
    with pytest.raises(PersistenceConflict): persist_artifacts(db,shortened)


def test_input_identities_are_sensitive_to_material_ownership_time_and_cost_context():
    b=bundle(full=True);c=replace(b.fills[0].calculation,assumptions=costs(trading_fee_rate=D('.02')))
    changed=replace(b,fills=(FillAudit(b.fills[0].fill,c),))
    assert changed.artifact_identity!=b.artifact_identity
    assert fill_row(changed.fills[0])['payload_identity']!=fill_row(b.fills[0])['payload_identity']
    db=RecordingTransaction();persist_artifacts(db,b)
    with pytest.raises(PersistenceConflict): persist_artifacts(db,changed)


@pytest.mark.parametrize('autocommit',[True,None,'unknown'])
def test_autocommit_or_unknown_driver_transaction_state_fails_closed(autocommit):
    db=RecordingTransaction();db.connection.dbapi_connection.autocommit=autocommit
    with pytest.raises(ValueError,match='non-autocommit'): persist_artifacts(db,bundle())
    assert not db.statements


def test_driver_failure_reading_transaction_mode_is_sanitized():
    db=RecordingTransaction()
    def broken(): raise IntegrityError('private SQL',{},Exception('private credentials'))
    db.get_isolation_level=broken
    with pytest.raises(PersistenceUnavailable) as error: persist_artifacts(db,bundle())
    assert 'private' not in str(error.value)
    assert not db.statements
