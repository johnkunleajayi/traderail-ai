"""Synthetic policy hypotheses and independent Decimal oracles. No live limits."""
import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta, timezone
from decimal import Decimal, localcontext, ROUND_UP
import json
from pathlib import Path
import subprocess
import sys
import os
from uuid import UUID

import pytest

from app.risk import Environment, RiskDecision, RiskFinding, RiskOutcome, RiskReason as R, RiskState as S, to_wire
from app.risk.risk_states import (DrawdownEvidence, DrawdownMeasurement, NewRiskCapacity, RiskStateEvaluation,
    RiskStatePolicy, RiskStateProjection, RiskStateStatus as Status, StateTransition as Transition,
    evaluate_risk_state, transition_state)
from test_trade_risk_sizing import AT, T, ID, OTHER, D, config, snapshot


def policy(**changes):
    args = dict(version="synthetic-hypothesis-1", caution_entry=D("0.03"), defensive_entry=D("0.06"),
        suspended_entry=D("0.1"), caution_recovery=D("0.02"), defensive_recovery=D("0.04"),
        suspended_recovery=D("0.08"), normal_multiplier=D("1"), caution_multiplier=D("0.5"),
        defensive_multiplier=D("0.1"), suspended_multiplier=D("0"), max_evidence_age=timedelta(seconds=30))
    args.update(changes)
    return RiskStatePolicy(**args)


def evidence(snap, **changes):
    args = dict(scope=snap.scope, snapshot_identity=snap.snapshot_identity, valuation_identity=snap.valuation_identity,
        current_equity=snap.equity, historical_peak=D("10000"), previous_state=snap.risk_state,
        peak_as_of=T-timedelta(days=1), as_of=snap.as_of, expires_at=snap.expires_at, source_identity=ID)
    args.update(changes)
    return DrawdownEvidence(**args)


def capacity(snap, cfg, **changes):
    args = dict(scope=snap.scope, snapshot_identity=snap.snapshot_identity, configuration_identity=cfg.configuration_identity,
        intent_identity=ID, upstream_identity=ID, base_permitted_risk=D("40"), proposed_modeled_risk=D("1"))
    args.update(changes)
    return NewRiskCapacity(**args)


def run(equity="10000", previous=S.NORMAL, *, snap=None, cfg=None, pol=None, ev=None, cap=None, at=AT):
    snap = snap or snapshot(equity=D(equity), available_cash=D(equity), risk_state=previous)
    cfg = cfg or config(environment=snap.scope.environment)
    return evaluate_risk_state(snap, cfg, pol or policy(), ev or evidence(snap), cap or capacity(snap,cfg), at)


def codes(result):
    return tuple(f.code for f in result.findings)


@pytest.mark.parametrize("peak,current,derived,amount,fraction", [
    ("10000","10000","10000","0","0"), ("10000","9600","10000","400","0.04"),
    ("10000","11000","11000","0","0"), ("10000","0","10000","10000","1"),
    ("0.000001","0.0000009","0.000001","0.0000001","0.1"),
])
def test_independent_drawdown_oracles(peak,current,derived,amount,fraction):
    m=DrawdownMeasurement(D(peak),D(current))
    assert (m.derived_peak,m.drawdown_amount,m.drawdown_fraction)==tuple(map(D,(derived,amount,fraction)))


@pytest.mark.parametrize("equity,state,multiplier,adjusted", [
    ("10000",S.NORMAL,"1","40"),("9600",S.CAUTION,"0.5","20"),
    ("9300",S.DEFENSIVE,"0.1","4"),("9000",S.SUSPENDED,"0","0")])
def test_independent_state_capacity_oracles(equity,state,multiplier,adjusted):
    r=run(equity)
    assert r.projection.evaluated_state is state
    assert r.projection.multiplier==D(multiplier)
    assert r.projection.adjusted_permitted_risk==D(adjusted)
    assert r.status is (Status.RISK_STATE_REJECTED if state is S.SUSPENDED else Status.RISK_STATE_PASS)


@pytest.mark.parametrize("equity,state",[("9700.0000000001",S.NORMAL),("9700",S.CAUTION),
    ("9699.9999999999",S.CAUTION),("9400.0000000001",S.CAUTION),("9400",S.DEFENSIVE),
    ("9399.9999999999",S.DEFENSIVE),("9000.0000000001",S.DEFENSIVE),("9000",S.SUSPENDED),
    ("8999.9999999999",S.SUSPENDED)])
def test_exact_entry_boundaries(equity,state):
    assert run(equity).projection.evaluated_state is state


@pytest.mark.parametrize("previous,equity,expected,transition", [
    (S.CAUTION,"9799.9999999999",S.CAUTION,Transition.UNCHANGED),
    (S.CAUTION,"9800",S.NORMAL,Transition.RECOVERED),
    (S.CAUTION,"9800.0000000001",S.NORMAL,Transition.RECOVERED),
    (S.DEFENSIVE,"9599.9999999999",S.DEFENSIVE,Transition.UNCHANGED),
    (S.DEFENSIVE,"9600",S.CAUTION,Transition.RECOVERED),
    (S.DEFENSIVE,"9600.0000000001",S.CAUTION,Transition.RECOVERED),
    (S.SUSPENDED,"9199.9999999999",S.SUSPENDED,Transition.UNCHANGED),
    (S.SUSPENDED,"9200",S.DEFENSIVE,Transition.RECOVERED),
    (S.SUSPENDED,"9200.0000000001",S.DEFENSIVE,Transition.RECOVERED)])
def test_exact_recovery_boundaries(previous,equity,expected,transition):
    r=run(equity,previous)
    assert (r.projection.evaluated_state,r.projection.transition)==(expected,transition)


@pytest.mark.parametrize("previous",list(S))
def test_severe_deterioration_has_priority_and_never_reverses(previous):
    r=run("8500",previous)
    assert r.projection.evaluated_state is S.SUSPENDED
    assert r.status is Status.RISK_STATE_REJECTED


@pytest.mark.parametrize("previous,expected",[(S.CAUTION,S.NORMAL),(S.DEFENSIVE,S.CAUTION),(S.SUSPENDED,S.DEFENSIVE)])
def test_full_equity_recovery_is_only_one_step(previous,expected):
    assert run("11000",previous).projection.evaluated_state is expected


def test_caution_threshold_oscillation_does_not_flap():
    state=S.NORMAL
    seen=[]
    for equity in ("9699","9701","9699","9701","9800"):
        state=run(equity,state).projection.evaluated_state
        seen.append(state)
    assert seen==[S.CAUTION,S.CAUTION,S.CAUTION,S.CAUTION,S.NORMAL]


@pytest.mark.parametrize("changes",[
    {"caution_entry":D("0")},{"caution_entry":D("0.06")},{"defensive_entry":D("0.1")},
    {"suspended_entry":D("1.1")},{"caution_recovery":D("0.03")},
    {"defensive_recovery":D("0.06")},{"suspended_recovery":D("0.1")},
    {"defensive_recovery":D("0.01")},{"suspended_recovery":D("0.03")},
    {"normal_multiplier":D("1.01")},{"caution_multiplier":D("-0.1")},
    {"normal_multiplier":D("0.4")},{"defensive_multiplier":D("0.6")},
    {"suspended_multiplier":D("0.01")},{"max_evidence_age":timedelta(0)},
    {"max_evidence_age":timedelta(seconds=-1)},{"normal_multiplier":1.0},
    {"version":""}, {"caution_entry":D("NaN")}, {"suspended_recovery":D("Infinity")},
])
def test_policy_invalid_inputs_rejected(changes):
    with pytest.raises(ValueError): policy(**changes)


@pytest.mark.parametrize("state",list(S))
def test_multiplier_never_increases_capacity(state):
    r=run("10000",state)
    assert 0 <= r.projection.adjusted_permitted_risk <= r.projection.base_permitted_risk


@pytest.mark.parametrize("equity,proposed,status",[("9600","20",Status.RISK_STATE_PASS),
    ("9600","20.00000000000001",Status.RISK_STATE_REJECTED),
    ("9300","4",Status.RISK_STATE_PASS),("9300","4.00000000000001",Status.RISK_STATE_REJECTED)])
def test_capacity_exact_boundary(equity,proposed,status):
    s=snapshot(equity=D(equity),available_cash=D(equity))
    r=run(snap=s,cap=capacity(s,config(),proposed_modeled_risk=D(proposed)))
    assert r.status is status


@pytest.mark.parametrize("state",[S.CAUTION,S.DEFENSIVE])
def test_reduced_states_can_be_configured_to_permit_zero_new_risk(state):
    p=policy(caution_multiplier=D("0"),defensive_multiplier=D("0"))
    r=run("9600" if state is S.CAUTION else "9300",pol=p)
    assert r.projection.adjusted_permitted_risk==0
    assert r.status is Status.RISK_STATE_REJECTED


@pytest.mark.parametrize("proposed",["0","0.00000001","1"])
def test_suspended_always_rejects_even_zero_proposal(proposed):
    s=snapshot(equity=D("9000"),available_cash=D("9000"))
    r=run(snap=s,cap=capacity(s,config(),proposed_modeled_risk=D(proposed)))
    assert r.status is Status.RISK_STATE_REJECTED
    assert R.SUSPENDED_STATE_ACTIVE in codes(r)


def test_stale_age_and_expiry_boundaries():
    assert run(at=T+timedelta(seconds=30)).status is Status.RISK_STATE_PASS
    assert R.DRAWDOWN_EVIDENCE_STALE in codes(run(at=T+timedelta(seconds=30,microseconds=1)))
    s=snapshot()
    e=evidence(s,expires_at=AT)
    assert R.DRAWDOWN_EVIDENCE_STALE in codes(run(snap=s,ev=e))
    s=replace(s,expires_at=AT)
    assert R.DRAWDOWN_EVIDENCE_STALE in codes(run(snap=s))


def test_future_evidence_rejected():
    assert R.DRAWDOWN_EVIDENCE_FUTURE in codes(run(at=T-timedelta(microseconds=1)))


@pytest.mark.parametrize("change",["portfolio","user","environment","currency","snapshot","valuation","previous","as_of"])
def test_binding_mismatch_rejected(change):
    s=snapshot(); e=evidence(s)
    if change in ("portfolio","user","environment","currency"):
        field={"portfolio":"portfolio_id","user":"user_id","environment":"environment","currency":"base_currency"}[change]
        value={"portfolio":UUID(int=999),"user":UUID(int=999),"environment":Environment.LAB,"currency":"USD"}[change]
        e=replace(e,scope=replace(e.scope,**{field:value}))
    else:
        field={"snapshot":"snapshot_identity","valuation":"valuation_identity","previous":"previous_state","as_of":"as_of"}[change]
        value={"snapshot":OTHER,"valuation":OTHER,"previous":S.CAUTION,"as_of":T+timedelta(seconds=1)}[change]
        e=replace(e,**{field:value})
    assert R.DRAWDOWN_CONTEXT_MISMATCH in codes(run(snap=s,ev=e))


def test_equity_and_optional_drawdown_must_reconcile():
    s=snapshot()
    assert R.DRAWDOWN_EQUITY_MISMATCH in codes(run(snap=s,ev=evidence(s,current_equity=D("9999"))))
    s=replace(s,drawdown_fraction=D("0.01"))
    assert R.DRAWDOWN_EQUITY_MISMATCH in codes(run(snap=s))
    assert run(snap=replace(s,drawdown_fraction=D("0"))).status is Status.RISK_STATE_PASS


def test_zero_peak_is_not_zero_drawdown():
    s=snapshot()
    r=run(snap=s,ev=evidence(s,historical_peak=D("0")))
    assert r.projection is None
    assert codes(r)==(R.INVALID_PEAK_EQUITY,)
    s=replace(s,equity=D("0"),available_cash=D("0"))
    assert R.INVALID_PEAK_EQUITY in codes(run(snap=s,ev=evidence(s,historical_peak=D("0"))))


def test_zero_current_equity_with_positive_peak_is_suspended():
    s=snapshot(equity=D("0"),available_cash=D("0"))
    r=run(snap=s,cap=capacity(s,config(),base_permitted_risk=D("0")))
    assert r.projection.measurement.drawdown_fraction==D("1")
    assert r.projection.evaluated_state is S.SUSPENDED
    assert r.status is Status.RISK_STATE_REJECTED


@pytest.mark.parametrize("bad",[D("-1"),D("NaN"),D("Infinity"),D("-Infinity"),1.0,None])
@pytest.mark.parametrize("field",["historical_peak","current_equity"])
def test_measurement_rejects_invalid_financial_values(field,bad):
    args=dict(historical_peak=D("100"),current_equity=D("99"));args[field]=bad
    with pytest.raises(ValueError): DrawdownMeasurement(**args)


@pytest.mark.parametrize("field",["historical_peak","current_equity"])
@pytest.mark.parametrize("bad",[D("-1"),D("NaN"),D("Infinity"),1.0,None])
def test_evidence_rejects_invalid_values(field,bad):
    with pytest.raises(ValueError): evidence(snapshot(),**{field:bad})


@pytest.mark.parametrize("name",["snapshot","configuration","policy","evidence","capacity"])
def test_missing_required_input_is_typed_rejection(name):
    s=snapshot();c=config()
    args=dict(snapshot=s,configuration=c,policy=policy(),evidence=evidence(s),capacity=capacity(s,c),evaluated_at=AT)
    args[name]=None
    r=evaluate_risk_state(**args)
    assert r.status is Status.RISK_STATE_REJECTED
    assert codes(r)==(R.DRAWDOWN_EVIDENCE_UNAVAILABLE,)


def test_policy_cannot_bypass_existing_hard_or_operating_drawdown_limit():
    assert R.INVALID_STATE_POLICY in codes(run(pol=policy(suspended_entry=D("0.11"))))
    c=config();c=replace(c,operating_limits=replace(c.operating_limits,max_drawdown_fraction=D("0.09")))
    assert R.INVALID_STATE_POLICY in codes(run(cfg=c))
    assert run(pol=policy(suspended_entry=D("0.1"))).status is Status.RISK_STATE_PASS


def test_base_capacity_cannot_inflate_existing_trade_risk_ceiling():
    s=snapshot();c=config()
    assert R.STATE_BASE_CAPACITY_INVALID in codes(run(snap=s,cap=capacity(s,c,base_permitted_risk=D("50.00001"))))
    assert run(snap=s,cap=capacity(s,c,base_permitted_risk=D("50"))).status is Status.RISK_STATE_PASS


@pytest.mark.parametrize("field",["snapshot_identity","configuration_identity","scope"])
def test_upstream_capacity_binding(field):
    s=snapshot();c=config();cap=capacity(s,c)
    value=OTHER if field!="scope" else replace(s.scope,environment=Environment.LAB)
    assert R.DRAWDOWN_CONTEXT_MISMATCH in codes(run(snap=s,cap=replace(cap,**{field:value})))


@pytest.mark.parametrize("field",["base_permitted_risk","proposed_modeled_risk"])
@pytest.mark.parametrize("bad",[D("-1"),D("NaN"),D("Infinity"),1.0,None])
def test_capacity_invalid_values(field,bad):
    with pytest.raises(ValueError): capacity(snapshot(),config(),**{field:bad})


def test_zero_capacity_and_zero_proposal_are_not_implicit_permissions():
    s=snapshot();c=config()
    for changes in ({"base_permitted_risk":D("0")},{"proposed_modeled_risk":D("0")}):
        assert run(snap=s,cap=capacity(s,c,**changes)).status is Status.RISK_STATE_REJECTED


@pytest.mark.parametrize("env",list(Environment))
def test_environment_is_bound_without_granting_execution_capability(env):
    assert run(snap=snapshot(env)).scope=="RISK_STATE_ONLY"


def test_inputs_and_existing_positions_are_not_mutated():
    from test_portfolio_risk import position
    s=snapshot(open_positions=(position(),)); c=config();e=evidence(s);cap=capacity(s,c);p=policy()
    before=to_wire(s)
    result=run(snap=s,cfg=c,ev=e,cap=cap,pol=p)
    assert to_wire(s)==before
    for obj in (s,e,cap,p,result,result.projection,result.projection.measurement):
        name=next(f.name for f in fields(obj) if f.init)
        with pytest.raises(FrozenInstanceError): setattr(obj,name,getattr(obj,name))
    assert s.open_positions[0]==position()


def test_decimal_timezone_and_ambient_precision_invariance():
    s=snapshot();c=config();e=evidence(s);cap=capacity(s,c);p=policy();a=run(snap=s,ev=e,cap=cap,pol=p)
    zone=timezone(timedelta(hours=5,minutes=30))
    e2=replace(e,current_equity=D("10000.0000"),historical_peak=D("10000.00"),as_of=e.as_of.astimezone(zone),
        peak_as_of=e.peak_as_of.astimezone(zone),expires_at=e.expires_at.astimezone(zone))
    p2=replace(p,caution_entry=D("0.030000"))
    cap2=replace(cap,base_permitted_risk=D("40.0000"))
    with localcontext() as ctx:
        ctx.prec=3;ctx.rounding=ROUND_UP
        b=run(snap=s,ev=e2,cap=cap2,pol=p2,at=AT.astimezone(zone))
    assert a.evaluation_identity==b.evaluation_identity
    assert a.projection.measurement.measurement_identity==b.projection.measurement.measurement_identity
    assert to_wire(b)["projection"]["base_permitted_risk"]=="40.0000"


def test_threshold_comparison_does_not_use_rounded_ratio():
    # 1/3 is repeating; a finite threshold just below it must still trigger entry.
    m=DrawdownMeasurement(D("3"),D("2"))
    p=policy(caution_entry=D("0.3333333333333333333333333333333333333333333333333333333333"),
        defensive_entry=D("0.5"),suspended_entry=D("0.8"),caution_recovery=D("0.2"),
        defensive_recovery=D("0.4"),suspended_recovery=D("0.7"))
    assert transition_state(m,S.NORMAL,p)==(S.CAUTION,Transition.ESCALATED)


@pytest.mark.parametrize("part",["peak","equity","previous","asof","source","policy","recovery","multiplier","proposed","base","upstream","at","scope"])
def test_material_changes_alter_identity(part):
    s=snapshot();c=config();p=policy();e=evidence(s);cap=capacity(s,c);a=run(snap=s,ev=e,cap=cap,pol=p)
    at=AT
    if part=="peak": e=replace(e,historical_peak=D("10001"))
    elif part=="equity":
        s=replace(s,equity=D("9999"),available_cash=D("9999"));e=evidence(s);cap=capacity(s,c)
    elif part=="previous":
        s=replace(s,risk_state=S.CAUTION);e=evidence(s);cap=capacity(s,c)
    elif part=="asof":
        s=replace(s,as_of=T+timedelta(seconds=1));e=evidence(s);cap=capacity(s,c)
    elif part=="source":e=replace(e,source_identity=OTHER)
    elif part=="policy":p=replace(p,caution_entry=D("0.031"))
    elif part=="recovery":p=replace(p,caution_recovery=D("0.019"))
    elif part=="multiplier":p=replace(p,caution_multiplier=D("0.4"))
    elif part=="proposed":cap=replace(cap,proposed_modeled_risk=D("2"))
    elif part=="base":cap=replace(cap,base_permitted_risk=D("39"))
    elif part=="upstream":cap=replace(cap,upstream_identity=OTHER)
    elif part=="at":at=AT+timedelta(microseconds=1)
    elif part=="scope":
        s=replace(s,scope=replace(s.scope,portfolio_id=UUID(int=999)));e=evidence(s);cap=capacity(s,c)
    assert run(snap=s,ev=e,cap=cap,pol=p,at=at).evaluation_identity!=a.evaluation_identity


def test_repeated_execution_and_reason_order_are_deterministic():
    a=run("9600");b=run("9600")
    assert a==b
    assert codes(a)==tuple(sorted(codes(a),key=lambda x:x.value))
    assert replace(a,findings=tuple(reversed(a.findings))).evaluation_identity==a.evaluation_identity


@pytest.mark.parametrize("field",["as_of","peak_as_of","expires_at"])
def test_naive_evidence_timestamps_rejected(field):
    with pytest.raises(ValueError): evidence(snapshot(),**{field:T.replace(tzinfo=None)})


def test_explicit_aware_evaluation_instant_required():
    for at in (None,T.replace(tzinfo=None)):
        with pytest.raises(ValueError):run(at=at)


def test_malformed_time_relations_rejected():
    with pytest.raises(ValueError):evidence(snapshot(),peak_as_of=T+timedelta(seconds=1))
    with pytest.raises(ValueError):evidence(snapshot(),expires_at=T)


def test_projection_cannot_forge_state_or_capacity():
    p=run("9600").projection
    for changes in ({"evaluated_state":S.NORMAL},{"multiplier":D("1")},
                    {"adjusted_permitted_risk":D("40")},{"transition":Transition.RECOVERED}):
        with pytest.raises(ValueError):replace(p,**changes)


def test_pass_cannot_be_forged_without_required_evidence():
    r=run()
    with pytest.raises(ValueError):replace(r,projection=None)
    with pytest.raises(ValueError):replace(r,evidence_identity=None)
    with pytest.raises(ValueError):replace(r,findings=(RiskFinding(R.DRAWDOWN_EVIDENCE_STALE,{}),))
    with pytest.raises(ValueError):replace(run("9000"),status=Status.RISK_STATE_PASS)


def test_scoped_pass_is_not_final_approval_and_does_not_touch_positions():
    r=run()
    assert not isinstance(r,RiskDecision)
    assert r.status.value=="RISK_STATE_PASS" and r.scope=="RISK_STATE_ONLY"
    assert r.version=="1.0.0"
    names={f.name for f in fields(r)}
    assert not names.intersection({"order","orders","execution_authority","liquidation","exit_orders"})
    assert R.DRAWDOWN_HEALTHY in codes(r)
    assert R.PROPOSED_RISK_WITHIN_STATE_CAPACITY in codes(r)


def test_no_io_or_execution_dependencies():
    folder=Path(__file__).parents[1]/"app/risk/risk_states"
    for file in folder.glob("*.py"):
        tree=ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[node.module or ""] if isinstance(node,ast.ImportFrom) else [n.name for n in node.names]
                assert not any(any(x in name.lower() for x in ("sqlalchemy","psycopg","httpx","requests","quidax","binance","fastapi")) for name in names)
            if isinstance(node,ast.Call):
                name=ast.unparse(node.func)
                assert not name.endswith((".now",".utcnow",".commit",".execute",".submit_order"))


def test_fresh_process_hash_seed_identity_and_findings_are_stable():
    script="""import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_risk_states import run, snapshot, config, capacity, evidence, D, AT, S
from app.risk import to_wire
from app.risk.risk_states import RiskStateEvaluation
valid=run('9600')
s=snapshot()
bad=run(snap=s,ev=evidence(s,current_equity=D('9999'),historical_peak=D('0')))
# Reconstruct findings from a hash-seed-ordered collection.
by_code={f.code.value:f for f in valid.findings}
rows=tuple(by_code[k] for k in set(by_code))
from dataclasses import replace
print(json.dumps({'valid':valid.evaluation_identity,'reordered':replace(valid,findings=rows).evaluation_identity,
                 'invalid':bad.evaluation_identity,'codes':[f.code.value for f in bad.findings]},sort_keys=True))
"""
    rows=[]
    for seed in ('1','2','3','4'):
        child=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        rows.append(json.loads(child.stdout))
    assert all(row==rows[0] for row in rows)
    assert rows[0]['valid']==rows[0]['reordered']
    assert rows[0]['codes']==['DRAWDOWN_EQUITY_MISMATCH']


@pytest.mark.parametrize("previous,threshold,recovery",[(S.DEFENSIVE,"9400","9600"),(S.SUSPENDED,"9000","9200")])
def test_more_severe_states_do_not_flap_at_entry_threshold(previous,threshold,recovery):
    state=previous
    for equity in (D(threshold)+D("0.01"),D(threshold)-D("0.01"),D(threshold)+D("0.01")):
        state=run(str(equity),state).projection.evaluated_state
        assert state is previous
    assert run(recovery,state).projection.transition is Transition.RECOVERED


def test_configuration_environment_mismatch_rejects():
    assert R.DRAWDOWN_CONTEXT_MISMATCH in codes(run(cfg=config(Environment.LIVE)))


def test_new_peak_is_evidence_only_and_fraction_reconciles():
    s=snapshot(equity=D("11000"),available_cash=D("11000"),drawdown_fraction=D("0"))
    e=evidence(s)
    r=run(snap=s,ev=e)
    assert r.projection.measurement.derived_peak==D("11000")
    assert e.historical_peak==D("10000")
    assert s.drawdown_fraction==0


@pytest.mark.parametrize("field",["expires_at","peak_as_of"])
def test_evidence_timestamp_changes_identity(field):
    s=snapshot();e=evidence(s)
    a=run(snap=s,ev=e)
    b=run(snap=s,ev=replace(e,**{field:getattr(e,field)-timedelta(microseconds=1)}))
    assert a.evaluation_identity!=b.evaluation_identity


def test_healthy_zero_proposal_is_unavailable_not_falsely_over_budget():
    s=snapshot();c=config()
    r=run(snap=s,cap=capacity(s,c,proposed_modeled_risk=D("0")))
    assert R.STATE_NEW_RISK_UNAVAILABLE in codes(r)
    assert R.PROPOSED_RISK_EXCEEDS_STATE_CAPACITY not in codes(r)


def test_low_ambient_precision_does_not_change_repeating_drawdown():
    a=DrawdownMeasurement(D("3"),D("2"))
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_UP
        b=DrawdownMeasurement(D("3.00"),D("2.00"))
    assert a.measurement_identity==b.measurement_identity
    assert a.drawdown_fraction==D("0.33333333333333333333333333333333333333333333333333")
