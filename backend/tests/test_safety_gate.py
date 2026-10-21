"""Deterministic synthetic safety evidence; no production numeric policy presets."""
import ast
from dataclasses import FrozenInstanceError,fields,replace
from datetime import timedelta,timezone
from decimal import localcontext,ROUND_UP
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app.risk import (Environment as E,KillSwitchScope as Scope,RiskDecision,RiskFinding,RiskOutcome,
    RiskReason as R,to_wire)
from app.risk.safety_gate import (DataTarget,HealthMetrics,InstrumentTarget,KillSwitchEvidence,
    KillSwitchInventory,KillSwitchStatus as KS,KillSwitchTarget,SafetyGatePolicy,SafetyGateResult,
    SafetyGateStatus as Status,StrategyHealth as H,StrategyHealthEvidence,StrategyLifecycleEvidence,
    StrategyTarget,SwitchCause,evaluate_safety_gate)
from app.strategy_lab.lifecycle import StrategyLifecycle,StrategyState as L
from app.strategy_lab.validation_evidence.models import Verdict
from app.strategies.configuration import StrategyDefinition
from test_trade_risk_sizing import AT,T,ID,OTHER,D,config,intent,run as trade_run

DEFAULT=object()


def policy(**changes):
    args=dict(version="synthetic-hypotheses-1",lifecycle_by_environment={
        E.LAB:(L.EXPERIMENTAL,L.BACKTESTED,L.VALIDATED),E.PAPER:(L.BACKTESTED,L.VALIDATED,L.PAPER),
        E.SHADOW:(L.SHADOW,),E.LIVE:(L.LIMITED_LIVE,L.LIVE)},permitted_health_states=(H.HEALTHY,),
        max_inventory_age=timedelta(seconds=30),max_switch_age=timedelta(seconds=30),
        max_health_age=timedelta(seconds=30),max_health_data_age=timedelta(seconds=30),max_lifecycle_age=timedelta(seconds=30),
        minimum_completed_trades=10,minimum_expectancy=D("0"),expectancy_currency="USDT",minimum_profit_factor=D("1"),
        maximum_drawdown_fraction=D("0.1"),maximum_stress_degradation=D("0.05"),
        require_data_sufficient=True,required_validation_verdicts=(Verdict.VALIDATION_CANDIDATE,))
    args.update(changes)
    return SafetyGatePolicy(**args)


def instrument_target(p=None):
    p=p or intent()
    return InstrumentTarget(p.instrument.instrument_id,p.instrument.canonical_symbol)


def target(scope=Scope.SYSTEM,p=None):
    p=p or intent()
    args={Scope.USER:dict(user_id=p.scope.user_id),Scope.PORTFOLIO:dict(portfolio_id=p.scope.portfolio_id),
        Scope.STRATEGY:dict(strategy=StrategyTarget(p.strategy.family,p.strategy.version,p.strategy.configuration_identity)),
        Scope.MARKET:dict(instrument=instrument_target(p)),
        Scope.DATA:dict(data=DataTarget(p.instrument.provider,instrument_target(p),p.instrument.timeframe,p.market_context.data_identity)),
        Scope.SYSTEM:{}}[scope]
    return KillSwitchTarget(scope,**args)


def switch(scope=Scope.SYSTEM,p=None,number=1,**changes):
    p=p or intent()
    args=dict(switch_id=UUID(int=number),target=target(scope,p),environment=p.scope.environment,
        active=True,cause=SwitchCause.MANUAL_CONTROL,activated_at=T-timedelta(seconds=1),expires_at=None,
        as_of=T,valid_until=T+timedelta(minutes=3),source_identity=ID)
    args.update(changes)
    return KillSwitchEvidence(**args)


def inventory(p=None,switches=(),**changes):
    p=p or intent()
    args=dict(intent_identity=p.intent_identity,scope=p.scope,market_context_identity=p.market_context.context_identity,
        covered_scopes=tuple(Scope),switches=switches,as_of=max((row.as_of for row in switches),default=T),
        valid_until=T+timedelta(minutes=3),source_identity=ID)
    args.update(changes)
    return KillSwitchInventory(**args)


def metrics(**changes):
    args=dict(completed_trade_count=20,expectancy=D("2"),expectancy_currency="USDT",profit_factor=D("1.5"),drawdown_fraction=D("0.02"),
        stress_degradation=D("0.01"),data_sufficient=True,validation_verdict=Verdict.VALIDATION_CANDIDATE,
        validation_report_identity=ID)
    args.update(changes)
    return HealthMetrics(**args)


def health(p=None,**changes):
    p=p or intent()
    args=dict(strategy=p.strategy,environment=p.scope.environment,state=H.HEALTHY,metrics=metrics(),
        window_start=T-timedelta(days=1),data_through=T,as_of=T,valid_until=T+timedelta(minutes=3),source_identity=ID)
    args.update(changes)
    return StrategyHealthEvidence(**args)


def lifecycle(p=None,**changes):
    p=p or intent()
    state={E.LAB:L.BACKTESTED,E.PAPER:L.PAPER,E.SHADOW:L.SHADOW,E.LIVE:L.LIVE}[p.scope.environment]
    args=dict(strategy=p.strategy,environment=p.scope.environment,lifecycle=StrategyLifecycle(state),
        as_of=T,valid_until=T+timedelta(minutes=3),source_identity=ID)
    args.update(changes)
    return StrategyLifecycleEvidence(**args)


def run(*,p=DEFAULT,cfg=DEFAULT,pol=DEFAULT,inv=DEFAULT,life=DEFAULT,h=DEFAULT,at=AT):
    p=intent() if p is DEFAULT else p
    reference=p if type(p) is type(intent()) else intent()
    return evaluate_safety_gate(p,config(reference.scope.environment) if cfg is DEFAULT else cfg,
        policy() if pol is DEFAULT else pol,inventory(reference) if inv is DEFAULT else inv,
        lifecycle(reference) if life is DEFAULT else life,health(reference) if h is DEFAULT else h,at)


def codes(result): return tuple(f.code for f in result.findings)


def observation(result,code): return next(f.evidence['observations'] for f in result.findings if f.code is code)


def test_healthy_and_complete_empty_inventory_passes_only_scoped_gate():
    r=run()
    assert r.status is Status.SAFETY_GATE_PASS and r.kill_switch_status is KS.KILL_SWITCH_PASS
    assert r.scope=="KILL_SWITCH_AND_STRATEGY_HEALTH_ONLY" and r.version=="1.0.0"
    assert r.lifecycle_eligible is True and not r.active_blocking_switches
    assert R.KILL_SWITCH_CLEAR in codes(r) and R.STRATEGY_LIFECYCLE_ELIGIBLE in codes(r)
    assert not isinstance(r,RiskDecision)


@pytest.mark.parametrize("scope",list(Scope))
def test_each_scope_active_rejects(scope):
    row=switch(scope);r=run(inv=inventory(switches=(row,)))
    assert r.status is Status.SAFETY_GATE_REJECTED and r.kill_switch_status is KS.KILL_SWITCH_REJECTED
    assert r.applicable_switches==r.active_blocking_switches==(row,)
    assert R[scope.value+"_KILL_SWITCH_ACTIVE"] in codes(r)


@pytest.mark.parametrize("scope",list(Scope))
def test_explicit_inactive_switch_does_not_block(scope):
    row=switch(scope,active=False,activated_at=None)
    r=run(inv=inventory(switches=(row,)))
    assert r.status is Status.SAFETY_GATE_PASS and r.applicable_switches==(row,) and not r.active_blocking_switches


@pytest.mark.parametrize("delta,status",[(-1,Status.SAFETY_GATE_PASS),(0,Status.SAFETY_GATE_PASS),(1,Status.SAFETY_GATE_REJECTED)])
def test_operational_expiry_exact_boundary(delta,status):
    row=switch(expires_at=AT+timedelta(microseconds=delta))
    r=run(inv=inventory(switches=(row,)))
    assert r.status is status


@pytest.mark.parametrize("scope",list(Scope))
def test_unknown_applicable_switch_state_is_not_inactive(scope):
    row=switch(scope,active=None,activated_at=None)
    r=run(inv=inventory(switches=(row,)))
    assert r.status is Status.SAFETY_GATE_REJECTED and R.SWITCH_EVIDENCE_UNAVAILABLE in codes(r)


@pytest.mark.parametrize("scope",list(Scope))
def test_wrong_environment_isolation(scope):
    row=switch(scope,environment=E.LAB)
    r=run(inv=inventory(switches=(row,)))
    assert r.status is Status.SAFETY_GATE_PASS and not r.applicable_switches


@pytest.mark.parametrize("scope",[Scope.USER,Scope.PORTFOLIO,Scope.STRATEGY,Scope.MARKET,Scope.DATA])
def test_wrong_target_isolation(scope):
    t=target(scope)
    if scope is Scope.USER:t=replace(t,user_id=UUID(int=999))
    elif scope is Scope.PORTFOLIO:t=replace(t,portfolio_id=UUID(int=999))
    elif scope is Scope.STRATEGY:t=replace(t,strategy=replace(t.strategy,configuration_identity=OTHER))
    elif scope is Scope.MARKET:t=replace(t,instrument=InstrumentTarget(UUID(int=999),"ETH/USDT"))
    else:t=replace(t,data=replace(t.data,timeframe="4h"))
    r=run(inv=inventory(switches=(switch(scope,target=t),)))
    assert r.status is Status.SAFETY_GATE_PASS and not r.applicable_switches


@pytest.mark.parametrize("change",["family","version","configuration"])
def test_strategy_target_exactness(change):
    t=target(Scope.STRATEGY)
    values={"family":dict(family="OTHER"),"version":dict(version="2.0.0"),"configuration":dict(configuration_identity=OTHER)}[change]
    if change in ("family","version"): values["configuration_identity"]=OTHER
    row=switch(Scope.STRATEGY,target=replace(t,strategy=replace(t.strategy,**values)))
    assert run(inv=inventory(switches=(row,))).status is Status.SAFETY_GATE_PASS


def test_explicit_family_version_switch_can_cover_all_configurations():
    t=target(Scope.STRATEGY); t=replace(t,strategy=replace(t.strategy,configuration_identity=None))
    p=intent(strategy=StrategyDefinition(t.strategy.family,t.strategy.version,{"research":D("1")}))
    r=run(p=p,inv=inventory(p,switches=(switch(Scope.STRATEGY,p=p,target=t),)))
    assert r.status is Status.SAFETY_GATE_REJECTED


@pytest.mark.parametrize("change",["uuid","symbol"])
def test_known_market_attribution_contradiction_fails_closed(change):
    t=instrument_target()
    t=replace(t,**({"instrument_id":UUID(int=999)} if change=="uuid" else {"canonical_symbol":"ETH/USDT"}))
    row=switch(Scope.MARKET,target=KillSwitchTarget(Scope.MARKET,instrument=t))
    assert R.SWITCH_EVIDENCE_MISMATCH in codes(run(inv=inventory(switches=(row,))))


@pytest.mark.parametrize("change",["provider","instrument","timeframe","data"])
def test_data_matching_requires_every_declared_dimension(change):
    t=target(Scope.DATA)
    changes={"provider":dict(provider="OTHER"),"instrument":dict(instrument=InstrumentTarget(UUID(int=99),"ETH/USDT")),
        "timeframe":dict(timeframe="15m"),"data":dict(data_identity=OTHER)}[change]
    row=switch(Scope.DATA,target=replace(t,data=replace(t.data,**changes)))
    assert run(inv=inventory(switches=(row,))).status is Status.SAFETY_GATE_PASS


def test_provider_wide_switch_is_explicit_not_symbol_inferred():
    t=KillSwitchTarget(Scope.DATA,data=DataTarget("BINANCE",None,None,None))
    r=run(inv=inventory(switches=(switch(Scope.DATA,target=t),)))
    assert R.DATA_KILL_SWITCH_ACTIVE in codes(r)


@pytest.mark.parametrize("kind",["switch","inventory"])
def test_future_switch_evidence_fails_closed(kind):
    if kind=="switch":
        row=switch(as_of=AT+timedelta(microseconds=1)); inv=inventory(switches=(row,))
    else:inv=inventory(as_of=AT+timedelta(microseconds=1))
    r=run(inv=inv)
    assert R.SWITCH_EVIDENCE_FUTURE in codes(r) and r.status is Status.SAFETY_GATE_REJECTED
    assert not r.active_blocking_switches


@pytest.mark.parametrize("kind",["switch","inventory"])
def test_stale_switch_evidence_fails_closed(kind):
    pol=policy(**({"max_switch_age":timedelta(seconds=2)} if kind=="switch" else {"max_inventory_age":timedelta(seconds=2)}))
    r=run(pol=pol,inv=inventory(switches=(switch(active=False),)))
    assert R.SWITCH_EVIDENCE_STALE in codes(r)


@pytest.mark.parametrize("kind",["switch","inventory"])
def test_evidence_expiry_is_separate_from_operational_expiry(kind):
    row=switch(active=False,valid_until=AT if kind=="switch" else T+timedelta(minutes=3))
    inv=inventory(switches=(row,),valid_until=AT if kind=="inventory" else T+timedelta(minutes=3))
    assert R.SWITCH_EVIDENCE_STALE in codes(run(inv=inv))


def test_expired_but_unverifiable_row_does_not_become_all_clear():
    row=switch(expires_at=T+timedelta(seconds=1),valid_until=AT)
    r=run(inv=inventory(switches=(row,)))
    assert not r.active_blocking_switches and R.SWITCH_EVIDENCE_STALE in codes(r)


def test_unrelated_unknown_stale_row_does_not_block_another_user():
    t=KillSwitchTarget(Scope.USER,user_id=UUID(int=999))
    row=switch(Scope.USER,target=t,active=None,activated_at=None,valid_until=AT)
    assert run(inv=inventory(switches=(row,))).status is Status.SAFETY_GATE_PASS


@pytest.mark.parametrize("scope",list(Scope))
def test_missing_scope_coverage_is_not_all_clear(scope):
    inv=inventory(covered_scopes=tuple(s for s in Scope if s is not scope))
    r=run(inv=inv)
    assert r.status is Status.SAFETY_GATE_REJECTED and R.SWITCH_EVIDENCE_UNAVAILABLE in codes(r)
    assert observation(r,R.SWITCH_EVIDENCE_UNAVAILABLE)[0]['missing_scopes']==(scope.value,)


@pytest.mark.parametrize("field",["intent_identity","scope","market_context_identity"])
def test_inventory_binding_mismatch(field):
    inv=inventory();value=OTHER if field!="scope" else replace(inv.scope,portfolio_id=UUID(int=999))
    assert R.SWITCH_EVIDENCE_MISMATCH in codes(run(inv=replace(inv,**{field:value})))


def test_multiple_switches_and_health_failures_all_visible_and_order_invariant():
    rows=tuple(switch(s,number=i) for i,s in enumerate(Scope,1))+(switch(Scope.SYSTEM,number=99),)
    h=health(state=H.UNHEALTHY,metrics=metrics(completed_trade_count=0,expectancy=D("-1"),profit_factor=None))
    a=run(inv=inventory(switches=rows),h=h)
    b=run(inv=inventory(switches=tuple(reversed(rows))),h=h)
    assert a.evaluation_identity==b.evaluation_identity
    assert len(a.active_blocking_switches)==7
    assert len(observation(a,R.SYSTEM_KILL_SWITCH_ACTIVE))==2
    assert {R[s.value+"_KILL_SWITCH_ACTIVE"] for s in Scope}<=set(codes(a))
    assert {R.STRATEGY_HEALTH_UNHEALTHY,R.INSUFFICIENT_HEALTH_EVIDENCE,R.HEALTH_METRIC_THRESHOLD_FAILED}<=set(codes(a))
    assert len(observation(a,R.HEALTH_METRIC_THRESHOLD_FAILED))==2


@pytest.mark.parametrize("state",list(H))
def test_each_health_state(state):
    r=run(h=health(state=state))
    assert r.status is (Status.SAFETY_GATE_PASS if state is H.HEALTHY else Status.SAFETY_GATE_REJECTED)
    assert r.health_evidence.state is state


def test_degraded_requires_explicit_permission():
    h=health(state=H.DEGRADED)
    assert R.STRATEGY_HEALTH_NOT_PERMITTED in codes(run(h=h))
    assert run(h=h,pol=policy(permitted_health_states=(H.HEALTHY,H.DEGRADED))).status is Status.SAFETY_GATE_PASS


@pytest.mark.parametrize("env,state",[(E.LAB,L.BACKTESTED),(E.PAPER,L.PAPER),(E.SHADOW,L.SHADOW),(E.LIVE,L.LIVE)])
def test_explicit_environment_lifecycle_eligibility(env,state):
    p=intent(environment=env); life=lifecycle(p,lifecycle=StrategyLifecycle(state))
    r=run(p=p,life=life)
    assert r.status is Status.SAFETY_GATE_PASS and r.lifecycle_eligible is True
    assert life.lifecycle.state is state


@pytest.mark.parametrize("env,state",[(E.LAB,L.LIVE),(E.PAPER,L.EXPERIMENTAL),(E.SHADOW,L.BACKTESTED),(E.LIVE,L.BACKTESTED),
    (E.LAB,L.SUSPENDED),(E.PAPER,L.RETIRED),(E.LIVE,L.LIVE_CANDIDATE)])
def test_ineligible_lifecycle_is_not_promoted_by_health(env,state):
    p=intent(environment=env);life=lifecycle(p,lifecycle=StrategyLifecycle(state))
    r=run(p=p,life=life)
    assert R.STRATEGY_LIFECYCLE_INELIGIBLE in codes(r) and r.status is Status.SAFETY_GATE_REJECTED
    assert life.lifecycle.state is state


@pytest.mark.parametrize("kind",["health","lifecycle"])
@pytest.mark.parametrize("field",["family","version","configuration","environment"])
def test_strategy_evidence_exact_attribution(kind,field):
    p=intent();strategy=p.strategy
    if field=="family":strategy=StrategyDefinition("OTHER",strategy.version,{})
    elif field=="version":strategy=StrategyDefinition(strategy.family,"2.0.0",{})
    elif field=="configuration":strategy=StrategyDefinition(strategy.family,strategy.version,{"test":D("1")})
    row=health(p) if kind=="health" else lifecycle(p)
    row=replace(row,environment=E.LAB) if field=="environment" else replace(row,strategy=strategy)
    r=run(**({"h":row} if kind=="health" else {"life":row}))
    assert R.STRATEGY_EVIDENCE_MISMATCH in codes(r)
    assert r.status is Status.SAFETY_GATE_REJECTED


@pytest.mark.parametrize("kind",["health","lifecycle"])
@pytest.mark.parametrize("failure",["future","stale","expired"])
def test_strategy_evidence_freshness(kind,failure):
    row=health() if kind=="health" else lifecycle()
    pol=policy()
    if failure=="future":row=replace(row,as_of=AT+timedelta(seconds=1))
    elif failure=="expired":row=replace(row,valid_until=AT)
    else:pol=replace(pol,**{"max_health_age" if kind=="health" else "max_lifecycle_age":timedelta(seconds=2)})
    r=run(pol=pol,**({"h":row} if kind=="health" else {"life":row}))
    assert (R.STRATEGY_EVIDENCE_FUTURE if failure=="future" else R.STRATEGY_EVIDENCE_STALE) in codes(r)


@pytest.mark.parametrize("field",["completed_trade_count","expectancy","profit_factor","drawdown_fraction",
    "stress_degradation","data_sufficient","validation_verdict"])
def test_required_unknown_metrics_do_not_mean_zero_or_healthy(field):
    changes={field:None}
    if field=="validation_verdict":changes['validation_report_identity']=None
    r=run(h=health(metrics=metrics(**changes)))
    assert R.INSUFFICIENT_HEALTH_EVIDENCE in codes(r) and r.status is Status.SAFETY_GATE_REJECTED
    assert any(row['metric']==field for row in observation(r,R.INSUFFICIENT_HEALTH_EVIDENCE))


@pytest.mark.parametrize("changes",[{"completed_trade_count":9},{"expectancy":D("-0.00000001")},
    {"profit_factor":D("0.999999999")},{"drawdown_fraction":D("0.10000001")},
    {"stress_degradation":D("0.050000001")},{"data_sufficient":False},
    {"validation_verdict":Verdict.FAILED_VALIDATION}])
def test_explicit_metric_vetoes(changes):
    r=run(h=health(metrics=metrics(**changes)))
    assert R.HEALTH_METRIC_THRESHOLD_FAILED in codes(r) and r.status is Status.SAFETY_GATE_REJECTED


def test_hand_worked_threshold_oracle_inclusive_and_no_scoring():
    m=metrics(completed_trade_count=10,expectancy=D("0"),profit_factor=D("1"),drawdown_fraction=D("0.1"),stress_degradation=D("0.05"))
    assert run(h=health(metrics=m)).status is Status.SAFETY_GATE_PASS
    # Large expectancy cannot offset one trade-count failure.
    m=replace(m,completed_trade_count=9,expectancy=D("1000000"))
    r=run(h=health(metrics=m))
    assert r.status is Status.SAFETY_GATE_REJECTED
    assert observation(r,R.HEALTH_METRIC_THRESHOLD_FAILED)[0]['metric']=='completed_trade_count'


def test_explicitly_disabled_metric_gates_do_not_invent_metrics():
    pol=policy(minimum_completed_trades=None,minimum_expectancy=None,minimum_profit_factor=None,
        maximum_drawdown_fraction=None,maximum_stress_degradation=None,require_data_sufficient=False,required_validation_verdicts=None)
    r=run(h=health(metrics=None),pol=pol)
    assert r.status is Status.SAFETY_GATE_PASS and r.health_evidence.metrics is None
    assert R.INSUFFICIENT_HEALTH_EVIDENCE in codes(run(h=health(metrics=None)))


@pytest.mark.parametrize("name",["p","cfg","pol","inv","life","h"])
def test_required_missing_or_malformed_inputs_fail_closed(name):
    for missing in (None,object()):
        r=run(**{name:missing})
        assert r.status is Status.SAFETY_GATE_REJECTED


def test_health_failure_is_preserved_when_switch_inventory_missing():
    r=run(inv=None,h=health(state=H.SUSPENDED))
    assert {R.SWITCH_EVIDENCE_UNAVAILABLE,R.STRATEGY_HEALTH_SUSPENDED}<=set(codes(r))


def test_active_switch_failure_is_preserved_when_health_missing():
    r=run(inv=inventory(switches=(switch(),)),h=None)
    assert {R.SYSTEM_KILL_SWITCH_ACTIVE,R.STRATEGY_EVIDENCE_UNAVAILABLE}<=set(codes(r))


def test_configuration_environment_mismatch_and_intent_validity():
    assert R.SAFETY_CONTEXT_MISMATCH in codes(run(cfg=config(E.LAB)))
    assert R.SAFETY_CONTEXT_MISMATCH in codes(run(at=T+timedelta(seconds=1)))
    assert R.CONTEXT_EXPIRED in codes(run(at=T+timedelta(minutes=2)))


@pytest.mark.parametrize("kind",["switch","inventory","health","lifecycle"])
def test_maximum_age_equality_is_allowed_but_next_instant_is_stale(kind):
    r=run(inv=inventory(switches=(switch(active=False),)),at=T+timedelta(seconds=30))
    assert r.status is Status.SAFETY_GATE_PASS
    assert run(inv=inventory(switches=(switch(active=False),)),at=T+timedelta(seconds=30,microseconds=1)).status is Status.SAFETY_GATE_REJECTED


@pytest.mark.parametrize("env",[E.SHADOW,E.LIVE])
@pytest.mark.parametrize("state",[L.EXPERIMENTAL,L.BACKTESTED,L.VALIDATED,L.PAPER,L.LIVE_CANDIDATE,L.SUSPENDED,L.RETIRED])
def test_policy_cannot_make_research_or_suspended_lifecycle_live(env,state):
    p=policy();mapping=dict(p.lifecycle_by_environment);mapping[env]=(state,)
    with pytest.raises(ValueError):replace(p,lifecycle_by_environment=mapping)


@pytest.mark.parametrize("state",[H.UNHEALTHY,H.SUSPENDED,H.UNKNOWN])
def test_policy_cannot_allow_prohibited_health_states(state):
    with pytest.raises(ValueError):policy(permitted_health_states=(H.HEALTHY,state))


@pytest.mark.parametrize("changes",[{"minimum_completed_trades":-1},{"minimum_completed_trades":True},
    {"minimum_expectancy":D("NaN")},{"minimum_profit_factor":D("-1")},
    {"maximum_drawdown_fraction":D("1.01")},{"maximum_stress_degradation":D("-0.01")},
    {"minimum_profit_factor":1.0},{"max_inventory_age":timedelta(0)},
    {"max_switch_age":timedelta(seconds=-1)},{"max_health_age":timedelta(0)},
    {"max_lifecycle_age":timedelta(0)},{"require_data_sufficient":None},{"version":""},
    {"required_validation_verdicts":()},{"permitted_health_states":None},{"permitted_health_states":(H.HEALTHY,H.HEALTHY)}])
def test_invalid_policy_contracts(changes):
    with pytest.raises(ValueError):policy(**changes)


def test_all_environment_policy_entries_must_be_explicit_typed_and_copied():
    mapping=dict(policy().lifecycle_by_environment)
    with pytest.raises(ValueError):policy(lifecycle_by_environment={E.PAPER:(L.PAPER,)})
    with pytest.raises(ValueError):policy(lifecycle_by_environment={key.value:value for key,value in mapping.items()})
    pol=policy(lifecycle_by_environment=mapping)
    mapping[E.PAPER]=()
    assert L.PAPER in pol.lifecycle_by_environment[E.PAPER]
    with pytest.raises(TypeError):pol.lifecycle_by_environment[E.PAPER]=()
    mapping=dict(pol.lifecycle_by_environment);mapping[E.PAPER]=()
    assert run(pol=replace(pol,lifecycle_by_environment=mapping)).status is Status.SAFETY_GATE_REJECTED


@pytest.mark.parametrize("field,bad",[("completed_trade_count",-1),("completed_trade_count",True),
    ("expectancy",D("NaN")),("expectancy",1.0),("profit_factor",D("Infinity")),("profit_factor",D("-1")),
    ("drawdown_fraction",D("1.1")),("stress_degradation",D("-1")),("data_sufficient",1),
    ("validation_report_identity",None),("validation_verdict",None)])
def test_malformed_metrics_rejected(field,bad):
    with pytest.raises(ValueError):metrics(**{field:bad})


@pytest.mark.parametrize("scope",list(Scope))
def test_switch_targets_cannot_add_another_scope(scope):
    t=target(scope)
    field='portfolio_id' if scope is Scope.USER else 'user_id'
    with pytest.raises(ValueError):replace(t,**{field:UUID(int=999)})


@pytest.mark.parametrize("changes",[{"active":1},{"active":True,"activated_at":None},
    {"activated_at":T+timedelta(seconds=1)},{"expires_at":T-timedelta(seconds=1)},
    {"valid_until":T},{"as_of":T.replace(tzinfo=None)},{"source_identity":"not-an-identity"},
    {"environment":"PAPER"},{"cause":"EMERGENCY"},{"switch_id":"1"}])
def test_invalid_switch_contract(changes):
    with pytest.raises(ValueError):switch(**changes)


def test_duplicate_conflicting_switch_observations_and_scope_coverage_rejected():
    row=switch()
    with pytest.raises(ValueError):inventory(switches=(row,replace(row,active=False)))
    with pytest.raises(ValueError):inventory(covered_scopes=(Scope.USER,Scope.USER))
    with pytest.raises(ValueError):inventory(covered_scopes=None)
    with pytest.raises(ValueError):replace(inventory(),switches=None)
    with pytest.raises(ValueError):inventory(switches=(replace(row,as_of=AT),),as_of=T)


@pytest.mark.parametrize("kind",['health','lifecycle'])
def test_strategy_evidence_invalid_time_or_type(kind):
    factory=health if kind=='health' else lifecycle
    with pytest.raises(ValueError):factory(as_of=T.replace(tzinfo=None))
    with pytest.raises(ValueError):factory(valid_until=T)
    with pytest.raises(ValueError):factory(environment='PAPER')
    if kind=='health':
        with pytest.raises(ValueError):factory(data_through=T+timedelta(seconds=1))
        with pytest.raises(ValueError):factory(window_start=T)
        with pytest.raises(ValueError):factory(state='HEALTHY')


def test_upstream_inputs_lifecycle_and_inventory_are_immutable():
    upstream=trade_run();before=to_wire(upstream)
    p=intent();inv=inventory(switches=(switch(),));h=health(state=H.UNHEALTHY);life=lifecycle()
    result=run(p=p,inv=inv,h=h,life=life)
    for obj in (p,inv,inv.switches[0],h,h.metrics,life,life.lifecycle,policy(),result):
        f=next(f.name for f in fields(obj) if f.init)
        with pytest.raises(FrozenInstanceError):setattr(obj,f,getattr(obj,f))
    assert to_wire(upstream)==before and life.lifecycle.state is L.PAPER
    assert result.status is Status.SAFETY_GATE_REJECTED


def test_decimal_utc_policy_and_evidence_order_normalization():
    a=run();zone=timezone(timedelta(hours=5,minutes=30));h=health();pol=policy()
    h=replace(h,metrics=replace(h.metrics,expectancy=D('2.0000'),profit_factor=D('1.5000')),
        as_of=h.as_of.astimezone(zone),valid_until=h.valid_until.astimezone(zone),
        window_start=h.window_start.astimezone(zone),data_through=h.data_through.astimezone(zone))
    mapping={key:tuple(reversed(value)) for key,value in reversed(tuple(pol.lifecycle_by_environment.items()))}
    pol=replace(pol,lifecycle_by_environment=mapping,minimum_expectancy=D('0.000'),maximum_drawdown_fraction=D('0.1000'))
    with localcontext() as ctx:
        ctx.prec=3;ctx.rounding=ROUND_UP
        b=run(h=h,pol=pol,at=AT.astimezone(zone))
    assert a.evaluation_identity==b.evaluation_identity
    assert to_wire(b)['health_evidence']['metrics']['expectancy']=='2.0000'


@pytest.mark.parametrize("change",['active','target','scope','expiry','as_of','source','cause','strategy','lifecycle','state',
    'metric','policy','environment','configuration','at','inventory_source','health_source','health_validity'])
def test_material_changes_alter_appropriate_identity(change):
    p=intent();row=switch(active=False);inv=inventory(switches=(row,));h=health();life=lifecycle();pol=policy();cfg=config();at=AT
    a=run(p=p,inv=inv,h=h,life=life,pol=pol)
    if change=='active':row=replace(row,active=True)
    elif change=='target':row=replace(row,target=target(Scope.USER))
    elif change=='scope':row=replace(row,target=target(Scope.PORTFOLIO))
    elif change=='expiry':row=replace(row,expires_at=T+timedelta(seconds=20))
    elif change=='as_of':row=replace(row,as_of=T-timedelta(microseconds=1))
    elif change=='source':row=replace(row,source_identity=OTHER)
    elif change=='cause':row=replace(row,cause=SwitchCause.EMERGENCY)
    elif change=='strategy':h=replace(h,strategy=StrategyDefinition('OTHER','1.0.0',{}))
    elif change=='lifecycle':life=replace(life,lifecycle=StrategyLifecycle(L.VALIDATED))
    elif change=='state':h=replace(h,state=H.DEGRADED)
    elif change=='metric':h=replace(h,metrics=replace(h.metrics,expectancy=D('3')))
    elif change=='policy':pol=replace(pol,minimum_completed_trades=11)
    elif change=='configuration':cfg=replace(cfg,version='other-hypothesis')
    elif change=='at':at=AT+timedelta(microseconds=1)
    elif change=='health_source':h=replace(h,source_identity=OTHER)
    elif change=='health_validity':h=replace(h,valid_until=h.valid_until-timedelta(microseconds=1))
    elif change=='environment':row=replace(row,environment=E.LAB)
    inv=replace(inv,switches=(row,),source_identity=OTHER if change=='inventory_source' else ID)
    b=run(p=p,inv=inv,h=h,life=life,pol=pol,cfg=cfg,at=at)
    assert a.evaluation_identity!=b.evaluation_identity


def test_no_final_approval_execution_or_position_action_fields():
    r=run()
    assert not isinstance(r,RiskDecision)
    names={f.name for f in fields(r)}
    assert not names.intersection({'orders','execution_authority','liquidation','positions','position_size','profit_probability'})
    assert set(Status)=={Status.SAFETY_GATE_PASS,Status.SAFETY_GATE_REJECTED}
    with pytest.raises(ValueError):replace(r,health_evidence=None)
    bad=run(inv=inventory(switches=(switch(),)))
    with pytest.raises(ValueError):replace(bad,status=Status.SAFETY_GATE_PASS)
    with pytest.raises(ValueError):replace(bad,kill_switch_status=KS.KILL_SWITCH_PASS)


def test_no_io_wall_clock_or_execution_dependencies():
    folder=Path(__file__).parents[1]/'app/risk/safety_gate'
    for file in folder.glob('*.py'):
        tree=ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[node.module or ''] if isinstance(node,ast.ImportFrom) else [n.name for n in node.names]
                assert not any(any(x in name.lower() for x in ('sqlalchemy','psycopg','httpx','requests','quidax','binance','fastapi')) for name in names)
            if isinstance(node,ast.Call):
                assert not ast.unparse(node.func).endswith(('.now','.utcnow','.commit','.execute','.submit_order'))


def test_explicit_aware_evaluation_instant_is_required():
    for at in (None,T.replace(tzinfo=None)):
        with pytest.raises(ValueError):run(at=at)


def test_four_hash_seeds_multiple_blockers_reordered_and_missing_inputs():
    script="""import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_safety_gate import *
rows={s.value:switch(s,number=i) for i,s in enumerate(Scope,1)}
ordered=tuple(rows[key] for key in set(rows))
h=health(state=H.UNHEALTHY,metrics=metrics(completed_trade_count=None,expectancy=D('-2'),profit_factor=None,data_sufficient=False))
a=run(inv=inventory(switches=ordered),h=h)
b=run(inv=inventory(switches=tuple(reversed(ordered))),h=h)
c=run(inv=None,h=h,life=None)
d=run(inv=object(),h=None,life=None)
print(json.dumps({'a':to_wire(a),'b':b.evaluation_identity,'missing':to_wire(c),'malformed':to_wire(d)},sort_keys=True))
"""
    outputs=[]
    for seed in ('1','2','3','4'):
        child=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        outputs.append(json.loads(child.stdout))
    assert all(row==outputs[0] for row in outputs)
    assert outputs[0]['a']['evaluation_identity']==outputs[0]['b']
    assert len(outputs[0]['a']['active_blocking_switches'])==6
    assert outputs[0]['a']['status']=='SAFETY_GATE_REJECTED'


@pytest.mark.parametrize("field",['as_of','valid_until'])
def test_required_switch_timestamps_cannot_be_unknown(field):
    with pytest.raises(ValueError):switch(**{field:None})


def test_stale_metric_window_cannot_be_relabelled_as_fresh_health():
    h=health(window_start=T-timedelta(days=3),data_through=T-timedelta(days=2))
    r=run(h=h)
    assert R.STRATEGY_EVIDENCE_STALE in codes(r)
    assert any(row['kind']=='HEALTH_DATA' for row in observation(r,R.STRATEGY_EVIDENCE_STALE))


def test_health_data_age_is_explicit_and_inclusive():
    pol=policy(max_health_age=timedelta(minutes=1),max_lifecycle_age=timedelta(minutes=1),
        max_inventory_age=timedelta(minutes=1),max_health_data_age=timedelta(seconds=3))
    assert run(pol=pol).status is Status.SAFETY_GATE_PASS
    assert R.STRATEGY_EVIDENCE_STALE in codes(run(pol=pol,at=AT+timedelta(microseconds=1)))
    with pytest.raises(ValueError):policy(max_health_data_age=timedelta(0))


def test_kill_status_cannot_claim_pass_when_switch_findings_reject():
    r=run(inv=inventory(covered_scopes=()))
    with pytest.raises(ValueError):replace(r,kill_switch_status=KS.KILL_SWITCH_PASS)


def test_expectancy_currency_is_known_and_exact_before_threshold_comparison():
    assert R.INSUFFICIENT_HEALTH_EVIDENCE in codes(run(h=health(metrics=metrics(expectancy_currency=None))))
    r=run(h=health(metrics=metrics(expectancy=D('99999999'),expectancy_currency='NGN')))
    assert r.status is Status.SAFETY_GATE_REJECTED and R.STRATEGY_EVIDENCE_MISMATCH in codes(r)
    assert any(row['kind']=='HEALTH_EXPECTANCY_CURRENCY' for row in observation(r,R.STRATEGY_EVIDENCE_MISMATCH))
    with pytest.raises(ValueError):policy(expectancy_currency=None)
    with pytest.raises(ValueError):policy(expectancy_currency='usdt')
    with pytest.raises(ValueError):metrics(expectancy_currency=1)


def test_unit_context_changes_evidence_and_policy_identities():
    a=health(); b=replace(a,metrics=replace(a.metrics,expectancy_currency='USD'))
    assert a.evidence_identity!=b.evidence_identity
    assert policy(expectancy_currency='USD').policy_identity!=policy().policy_identity


@pytest.mark.parametrize("field",['family','version'])
def test_known_strategy_configuration_cannot_be_misattributed_to_ignore_switch(field):
    t=target(Scope.STRATEGY)
    selector=replace(t.strategy,**{field:'OTHER' if field=='family' else '2.0.0'})
    row=switch(Scope.STRATEGY,target=replace(t,strategy=selector))
    assert R.SWITCH_EVIDENCE_MISMATCH in codes(run(inv=inventory(switches=(row,))))


def test_known_data_instrument_attribution_cannot_be_used_to_ignore_switch():
    t=target(Scope.DATA)
    t=replace(t,data=replace(t.data,instrument=InstrumentTarget(intent().instrument.instrument_id,'ETH/USDT')))
    row=switch(Scope.DATA,target=t)
    assert R.SWITCH_EVIDENCE_MISMATCH in codes(run(inv=inventory(switches=(row,))))
