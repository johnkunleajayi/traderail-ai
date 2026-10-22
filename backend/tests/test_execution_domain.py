"""11A immutable contract facts only; no venue, simulated economics or persistence."""
import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta, timezone
from decimal import Decimal, localcontext, ROUND_UP
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from app.execution import *
from app.risk import Direction, RiskOutcome, to_wire
from app.risk.integrated.inspection import inspect_risk, RiskEvaluationInspection
from app.risk.integrated.models import GateName
from test_integrated_risk import inputs, AT, T, OTHER, assumptions, inventory, switch
from test_trade_risk_sizing import intent, D


def request(*, i=None, created_at=AT, venue='PAPER_TEST'):
    i = i or inputs()
    return ExecutionRequest(ExecutionAuthorization(i.intent, inspect_risk(i)), venue, created_at)


def fill(req, quantity='1', name='fill-1', at=None, price='100.00000000'):
    return Fill(req, name, D(quantity), D(price), at or AT+timedelta(seconds=2))


def order(req, state=OrderState.CREATED, *, at=None, fills=(), accepted=None, venue=None):
    return Order(req, state, at or AT+timedelta(seconds=3), accepted, venue, fills)


def test_original_approved_risk_is_exact_authority_and_quantity_source():
    i = inputs(); report = inspect_risk(i)
    auth = ExecutionAuthorization(i.intent, report)
    req = ExecutionRequest(auth, 'PAPER_TEST', AT)
    assert report.decision.outcome is RiskOutcome.APPROVED
    assert auth.decision.decision_identity == report.decision.decision_identity
    assert req.quantity == auth.quantity == D('1')
    assert req.intent_identity == i.intent.intent_identity
    assert req.risk_decision_identity == report.decision.decision_identity
    assert req.environment is ExecutionEnvironment.PAPER and req.side is OrderSide.BUY
    assert not {'quantity','risk_fraction','price','outcome','override'} & {f.name for f in fields(req) if f.init}


def test_rejected_risk_never_constructs_execution_authorization():
    i = inputs(switch_inventory=inventory(switches=(switch(),)))
    with pytest.raises(ValueError): ExecutionAuthorization(i.intent, inspect_risk(i))


def test_different_exact_intent_rejected_even_if_same_instrument():
    i = inputs(); other = replace(i.intent, strategy_decision_identity=OTHER)
    with pytest.raises(ValueError): ExecutionAuthorization(other, inspect_risk(i))


@pytest.mark.parametrize('kind', ['missing','duplicate','rejected','legacy','spliced'])
def test_all_four_exact_gate_records_and_integrated_provenance_required(kind):
    i = inputs(); report = inspect_risk(i)
    if kind == 'missing': report = replace(report, gates=report.gates[:-1])
    elif kind == 'duplicate': report = replace(report, gates=(report.gates[0],)*4)
    elif kind == 'rejected':
        bad = inspect_risk(inputs(switch_inventory=inventory(switches=(switch(),))))
        report = replace(report, gates=bad.gates)
    elif kind == 'legacy':
        from app.risk import RiskFinding, RiskReason
        d = replace(report.decision, findings=(RiskFinding(RiskReason.DATA_HEALTH_CONFIRMED,{}),))
        report = replace(report, decision=d)
    else:
        other = inspect_risk(inputs(sizing=assumptions(proposed_quantity=D('2'))))
        report = replace(report, gates=other.gates)
    with pytest.raises(ValueError): ExecutionAuthorization(i.intent, report)


@pytest.mark.parametrize('offset', [-1, 27, 28])
def test_request_cannot_use_future_or_expired_approval(offset):
    with pytest.raises(ValueError): request(created_at=AT+timedelta(seconds=offset))


def test_explicit_revalidation_at_future_submission_instant():
    req = request()
    req.authorization.validate_at(AT)
    with pytest.raises(ValueError): req.authorization.validate_at(AT+timedelta(seconds=27))
    assert req.authorization.decision.approval_expires_at == T+timedelta(seconds=30)


@pytest.mark.parametrize('env', [ExecutionEnvironment.LAB,ExecutionEnvironment.SHADOW,ExecutionEnvironment.LIVE])
def test_only_paper_request_contract_enabled(env):
    i = inputs(); scope = replace(i.intent.scope, environment=env)
    proposal = replace(i.intent, scope=scope)
    snap = replace(i.snapshot, scope=scope)
    cfg = replace(i.configuration, environment=env)
    report = inspect_risk(inputs(proposal=proposal,snapshot=snap,cfg=cfg))
    # Research/Live lifecycle eligibility still belongs to Risk. If it rejects,
    # authorization fails; even a permitted LAB report cannot enable execution.
    with pytest.raises(ValueError): ExecutionRequest(ExecutionAuthorization(proposal,report), 'PAPER_TEST', AT)


def test_short_paper_contract_does_not_enable_live_shorting():
    req = request(i=inputs(proposal=intent(direction=Direction.SHORT)))
    assert req.side is OrderSide.SELL and req.environment is ExecutionEnvironment.PAPER


@pytest.mark.parametrize('value', ['','paper-test','PAPER TEST',None,42])
def test_explicit_canonical_venue_identity(value):
    with pytest.raises(ValueError): request(venue=value)


def test_retry_identity_and_binding_are_stable():
    a = request(); b = request()
    validate_retry(a,b)
    assert a.request_identity == b.request_identity
    assert a.action_identity == b.action_identity and a.client_order_identity == b.client_order_identity
    assert a.order_identity == b.order_identity
    assert ApprovalConsumptionBinding(a) == ApprovalConsumptionBinding(b)
    assert not {'consumed','consumed_at','persisted'} & {f.name for f in fields(ApprovalConsumptionBinding(a))}


@pytest.mark.parametrize('change', ['time','venue','approval','quantity'])
def test_changed_request_is_conflict_not_a_second_economic_action(change):
    a = request()
    if change == 'time': b = request(created_at=AT+timedelta(seconds=1))
    elif change == 'venue': b = request(venue='ANOTHER_PAPER')
    elif change == 'approval': b = request(i=inputs(at=AT+timedelta(seconds=1)),created_at=AT+timedelta(seconds=1))
    else: b = request(i=inputs(sizing=assumptions(proposed_quantity=D('2'))))
    assert a.client_order_identity == b.client_order_identity and a.order_identity == b.order_identity
    assert a.request_identity != b.request_identity
    with pytest.raises(ValueError,match='conflict'): validate_retry(a,b)


def test_different_intent_is_different_action_not_a_retry():
    a = request(); i = inputs(proposal=intent(strategy_decision_identity=OTHER)); b = request(i=i)
    assert a.action_identity != b.action_identity
    with pytest.raises(ValueError): validate_retry(a,b)


def test_immutable_contracts_and_no_portfolio_mutation():
    req = request(); original = to_wire(req.authorization.report)
    f = fill(req); o = order(req,OrderState.FILLED,fills=(f,),accepted=AT)
    pos = PositionLifecycleReference(req,PositionLifecycleState.OPEN,(),AT)
    for obj in (req.authorization,req,ApprovalConsumptionBinding(req),f,o,pos):
        name = next(f.name for f in fields(obj) if f.init)
        with pytest.raises(FrozenInstanceError): setattr(obj,name,getattr(obj,name))
    assert to_wire(req.authorization.report) == original


def test_fill_provenance_and_exact_strings_are_observations_only():
    req = request(); f = fill(req,quantity='0.400000000000000000',price='0.00000000000001234567890')
    assert f.intent_identity == req.intent_identity
    assert f.risk_decision_identity == req.risk_decision_identity
    assert f.request_identity == req.request_identity
    assert f.order_identity == req.order_identity and f.client_order_identity == req.client_order_identity
    assert to_wire(f)['price'] == '0.00000000000001234567890'
    changed = replace(f,price=D('0.00000000000001234567891'))
    assert changed.fill_identity == f.fill_identity and changed.observation_identity != f.observation_identity


@pytest.mark.parametrize('name', ['quantity','price'])
@pytest.mark.parametrize('value', [D('0'),D('-1'),D('NaN'),D('Infinity'),1.0,1,'1'])
def test_fill_requires_exact_positive_decimal_values(name,value):
    req = request(); args = dict(request=req,venue_fill_identity='fill',quantity=D('1'),price=D('100'),filled_at=AT)
    args[name] = value
    with pytest.raises(ValueError): Fill(**args)


def test_invalid_fill_and_order_contexts_are_rejected():
    req = request(); other = request(venue='OTHER')
    with pytest.raises(ValueError): fill(req,quantity='2')
    with pytest.raises(ValueError): fill(req,at=AT-timedelta(seconds=1))
    f = fill(req,quantity='.4')
    for rows in ((f,f),(f,replace(f,price=D('101'))),(fill(other),)):
        with pytest.raises(ValueError): order(req,OrderState.FILLED,fills=rows,accepted=AT)
    with pytest.raises(ValueError): order(req,OrderState.FILLED,fills=(fill(req),),accepted=None)
    with pytest.raises(ValueError): order(req,OrderState.FILLED,fills=(fill(req),),accepted=AT+timedelta(seconds=3))


def test_partial_full_and_ordering_contracts_without_simulation():
    req = request(); a = fill(req,quantity='.4',name='A'); b = fill(req,quantity='.6',name='B')
    with localcontext() as ctx:
        ctx.prec=2; ctx.rounding=ROUND_UP
        full = order(req,OrderState.FILLED,fills=(b,a),accepted=AT)
    assert full.filled_quantity == D('1') and full.execution_state is ExecutionState.COMPLETED
    assert full.observation_identity == order(req,OrderState.FILLED,fills=(a,b),accepted=AT).observation_identity
    partial = order(req,OrderState.PARTIALLY_FILLED,fills=(a,),accepted=AT)
    validate_order_transition(partial,full)
    assert partial.execution_state is ExecutionState.IN_PROGRESS
    with pytest.raises(ValueError): order(req,OrderState.PARTIALLY_FILLED,accepted=AT)
    with pytest.raises(ValueError): order(req,OrderState.FILLED,fills=(a,),accepted=AT)
    with pytest.raises(ValueError): order(req,OrderState.ACCEPTED,fills=(a,),accepted=AT)
    with pytest.raises(ValueError): order(req,OrderState.CANCELLED,fills=(a,b),accepted=AT)
    with pytest.raises(ValueError): order(req,OrderState.FILLED,fills=(fill(req,quantity='.6',name='C'),b),accepted=AT)


@pytest.mark.parametrize('target', [OrderState.ACCEPTED,OrderState.CANCELLED,OrderState.REJECTED,OrderState.UNKNOWN])
def test_created_order_valid_transitions(target):
    req = request(); before = order(req)
    after = order(req,target,accepted=AT if target is OrderState.ACCEPTED else None)
    validate_order_transition(before,after)


@pytest.mark.parametrize('target', [OrderState.PARTIALLY_FILLED,OrderState.FILLED,OrderState.CANCELLED,OrderState.UNKNOWN])
def test_accepted_order_valid_transitions(target):
    req = request(); before = order(req,OrderState.ACCEPTED,accepted=AT,venue='venue-order')
    rows = (fill(req,quantity='.4' if target is OrderState.PARTIALLY_FILLED else '1'),) if target in (OrderState.PARTIALLY_FILLED,OrderState.FILLED) else ()
    after = order(req,target,fills=rows,accepted=AT,venue='venue-order')
    validate_order_transition(before,after)


@pytest.mark.parametrize('target', [OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED,OrderState.FILLED,OrderState.CANCELLED,OrderState.REJECTED])
def test_unknown_requires_authoritative_reconciliation_not_resubmission(target):
    req = request(); before = order(req,OrderState.UNKNOWN)
    rows = (fill(req,quantity='.4' if target is OrderState.PARTIALLY_FILLED else '1'),) if target in (OrderState.PARTIALLY_FILLED,OrderState.FILLED) else ()
    accepted = AT if target in (OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED,OrderState.FILLED) else None
    validate_order_transition(before,order(req,target,fills=rows,accepted=accepted))
    with pytest.raises(ValueError): validate_order_transition(before,order(req))


@pytest.mark.parametrize('terminal', [OrderState.FILLED,OrderState.CANCELLED,OrderState.REJECTED])
def test_terminal_order_cannot_restart_or_gain_fill(terminal):
    req = request(); rows=(fill(req),) if terminal is OrderState.FILLED else ()
    before = order(req,terminal,fills=rows,accepted=AT if rows else None)
    validate_order_transition(before,before)
    with pytest.raises(ValueError): validate_order_transition(before,order(req))
    with pytest.raises(ValueError): validate_order_transition(before,order(req,OrderState.ACCEPTED,accepted=AT))


def test_monotonic_observations_and_immutable_known_facts():
    req = request(); f = fill(req,quantity='.4')
    before = order(req,OrderState.PARTIALLY_FILLED,fills=(f,),accepted=AT,venue='venue-order')
    with pytest.raises(ValueError): validate_order_transition(before,replace(before,observed_at=AT+timedelta(seconds=2)))
    with pytest.raises(ValueError): validate_order_transition(before,replace(before,venue_order_identity='changed'))
    with pytest.raises(ValueError): validate_order_transition(before,order(req,OrderState.UNKNOWN,accepted=AT,venue='venue-order'))
    with pytest.raises(ValueError): validate_order_transition(before,replace(before,fills=(replace(f,price=D('99')),)))
    with pytest.raises(ValueError): validate_order_transition(before,order(request(venue='OTHER')))


def test_fill_can_be_reported_after_approval_expiry_without_creating_new_authority():
    req = request(); late = AT+timedelta(seconds=40)
    f = fill(req,at=late)
    observed = order(req,OrderState.FILLED,accepted=AT,fills=(f,),at=late)
    assert observed.state is OrderState.FILLED
    with pytest.raises(ValueError): req.authorization.validate_at(late)
    with pytest.raises(ValueError): order(req,OrderState.ACCEPTED,accepted=late,at=late)


def test_position_references_are_not_positions_or_ledger_updates():
    req = request(); opened = PositionLifecycleReference(req,PositionLifecycleState.OPEN,(),AT)
    closed = PositionLifecycleReference(req,PositionLifecycleState.CLOSED,(OTHER,),AT)
    assert opened.position_identity == closed.position_identity and opened.reference_identity != closed.reference_identity
    assert not {'quantity','equity','balance','pnl'} & {f.name for f in fields(opened)}
    for state,refs in ((PositionLifecycleState.CLOSED,()),(PositionLifecycleState.OPEN,(OTHER,)),
                       (PositionLifecycleState.CLOSED,(OTHER,OTHER)),(PositionLifecycleState.CLOSED,(req.order_identity,))):
        with pytest.raises(ValueError): PositionLifecycleReference(req,state,refs,AT)


def test_equivalent_decimals_utc_and_evidence_order_preserve_identity():
    i=inputs(); report=inspect_risk(i); a=request(i=i)
    reordered=ExecutionAuthorization(i.intent,replace(report,gates=tuple(reversed(report.gates))))
    b=ExecutionRequest(reordered,'PAPER_TEST',AT.astimezone(timezone(timedelta(hours=1))))
    assert a.request_identity == b.request_identity
    c=request(i=inputs(sizing=assumptions(proposed_quantity=D('1.00000'))))
    assert a.request_identity == c.request_identity
    assert to_wire(c)['authorization']['report']['gates']
    with pytest.raises(ValueError): request(created_at=AT.replace(tzinfo=None))


def test_venue_is_only_protocol_not_a_paper_or_live_implementation():
    assert ExecutionVenue._is_protocol
    with pytest.raises(TypeError): ExecutionVenue()
    import app.execution as module
    assert not hasattr(module,'PaperExecutionVenue') and not hasattr(module,'QuidaxExecutionVenue')


def test_no_io_hidden_clock_simulation_or_economic_execution_in_source():
    root=Path(__file__).parents[1]/'app/execution'
    for p in root.glob('*.py'):
        tree=ast.parse(p.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant): assert not isinstance(node.value,float)
            if isinstance(node,ast.Call):
                name=ast.unparse(node.func)
                assert name not in ('float','hash')
                assert not name.endswith(('.now','.utcnow','.submit','.lookup','.commit','.execute','.connect'))
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[node.module or ''] if isinstance(node,ast.ImportFrom) else [a.name for a in node.names]
                assert not any(any(x in n for x in ('httpx','requests','sqlalchemy','psycopg','fastapi','app.db','app.market_data')) for n in names)


def test_four_fresh_hash_seeds_preserve_request_order_fill_and_reference_identities():
    script="""import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_execution_domain import *
i=inputs();report=inspect_risk(i)
rows={r.gate.value:r for r in report.gates}
auth=ExecutionAuthorization(i.intent,replace(report,gates=tuple(rows[k] for k in set(rows))))
r=ExecutionRequest(auth,'PAPER_TEST',AT)
a=fill(r,quantity='.4',name='A');b=fill(r,quantity='.6',name='B')
fills={f.fill_identity:f for f in (a,b)}
o=order(r,OrderState.FILLED,accepted=AT,fills=tuple(fills[k] for k in set(fills)))
refs=(OTHER,'sha256:'+'c'*64)
p=PositionLifecycleReference(r,PositionLifecycleState.CLOSED,tuple(set(refs)),AT)
print(json.dumps({'request':r.request_identity,'client':r.client_order_identity,'order':o.order_identity,
 'observation':o.observation_identity,'position':p.reference_identity,'consumption':ApprovalConsumptionBinding(r).binding_identity},sort_keys=True))
"""
    results=[]
    for seed in ('1','2','3','4'):
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        results.append(json.loads(result.stdout))
    assert all(row==results[0] for row in results)


def test_pure_import_has_no_database_provider_or_api_dependency():
    script="import sys; import app.execution; assert not any(n in sys.modules for n in ('sqlalchemy','httpx','psycopg','fastapi','app.main'))"
    subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],check=True,capture_output=True,text=True)


def test_position_reference_progression_cannot_reopen_or_rewrite():
    req=request()
    opened=PositionLifecycleReference(req,PositionLifecycleState.OPEN,(),AT)
    closed=PositionLifecycleReference(req,PositionLifecycleState.CLOSED,(OTHER,),AT+timedelta(seconds=1))
    validate_position_reference_transition(opened,closed)
    validate_position_reference_transition(closed,closed)
    with pytest.raises(ValueError): validate_position_reference_transition(closed,replace(opened,observed_at=AT+timedelta(seconds=2)))
    with pytest.raises(ValueError): validate_position_reference_transition(closed,replace(closed,closing_order_identities=('sha256:'+'c'*64,)))
    with pytest.raises(ValueError): validate_position_reference_transition(closed,replace(closed,observed_at=AT))
    with pytest.raises(ValueError): validate_position_reference_transition(opened,replace(opened,opening_request=request(venue='OTHER')))


def test_approval_consumption_key_cannot_be_renewed_by_retry_creation_time():
    original=request(); changed=replace(original,created_at=AT+timedelta(seconds=1))
    a=ApprovalConsumptionBinding(original); b=ApprovalConsumptionBinding(changed)
    assert a.consumption_key==b.consumption_key and a.binding_identity!=b.binding_identity
    assert original.client_order_identity==changed.client_order_identity
    with pytest.raises(ValueError): validate_retry(original,changed)


def test_unknown_and_rejected_execution_states_are_explicit():
    req=request()
    assert order(req).execution_state is ExecutionState.READY
    assert order(req,OrderState.UNKNOWN).execution_state is ExecutionState.UNKNOWN
    assert order(req,OrderState.REJECTED).execution_state is ExecutionState.FAILED
    assert order(req,OrderState.CANCELLED).execution_state is ExecutionState.CANCELLED


def test_repeated_partial_fill_observation_may_only_accumulate_facts():
    req=request(); a=fill(req,quantity='.2',name='A'); b=fill(req,quantity='.3',name='B')
    old=order(req,OrderState.PARTIALLY_FILLED,fills=(a,),accepted=AT)
    new=order(req,OrderState.PARTIALLY_FILLED,fills=(b,a),accepted=AT,at=AT+timedelta(seconds=4))
    validate_order_transition(old,new)
    assert new.filled_quantity==D('.5')
    with pytest.raises(ValueError): validate_order_transition(new,replace(old,observed_at=new.observed_at))
