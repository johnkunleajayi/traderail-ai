"""Full synthetic risk oracles. Numeric values are explicit test hypotheses only."""
import ast
from dataclasses import FrozenInstanceError,fields,replace
from datetime import datetime,timedelta,timezone
from decimal import localcontext,ROUND_UP
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

from app.risk import (Environment,RiskDecision,RiskFinding,RiskOutcome as O,RiskReason as R,RiskState,to_wire)
from app.risk.integrated import RiskEvaluationInputs,GateName as G,GateDisposition as D,GateFinding,GateRecord,evaluate_risk
from app.risk.integrated.expiry import ApprovalValidity,ValidityBoundary,derive_approval_validity
from app.risk.integrated.evaluator import _collect,_finalize
from app.risk.risk_states import NewRiskCapacity,evaluate_risk_state
from app.strategies.configuration import StrategyDefinition
from test_trade_risk_sizing import AT,T,ID,OTHER,D as Decimal,assumptions,intent,market,combined_costs
from test_portfolio_risk import (portfolio,position,configuration,policy as portfolio_policy,context,correlation)
from test_risk_states import policy as state_policy,evidence as drawdown
from test_safety_gate import (policy as safety_policy,inventory,health,lifecycle,switch,metrics,Scope,H,L)

engine=importlib.import_module('app.risk.integrated.evaluator')
DEFAULT=object()


def inputs(*,proposal=None,snapshot=None,cfg=None,sizing=None,pp=None,sp=None,hp=None,at=AT,**changes):
    proposal=proposal or intent();snapshot=snapshot or portfolio();cfg=cfg or configuration()
    pp=pp or portfolio_policy();sp=sp or state_policy();hp=hp or safety_policy()
    args=dict(intent=proposal,configuration=cfg,snapshot=snapshot,market_context=proposal.market_context,evaluated_at=at,
        sizing_assumptions=sizing or assumptions(proposed_quantity=Decimal('1')),portfolio_policy=pp,
        portfolio_context=context(proposal,snapshot,pp),state_policy=sp,drawdown_evidence=drawdown(snapshot),
        safety_policy=hp,switch_inventory=inventory(proposal),lifecycle_evidence=lifecycle(proposal),health_evidence=health(proposal))
    args.update(changes)
    return RiskEvaluationInputs(**args)


def entries(decision,code): return next(f.evidence['gate_findings'] for f in decision.findings if f.code is code)


def manifest(decision):return entries(decision,R.INTEGRATED_EVALUATION_RECORDED)[0]['source_evidence']


def records_by_gate(i):return {r.gate:r for r in _collect(i)}


def codes(decision):return decision.reason_codes


def test_complete_hand_calculated_approval_oracle():
    i=inputs();records=records_by_gate(i);d=evaluate_risk(i)
    assert type(d) is RiskDecision and d.outcome is O.APPROVED
    assert all(row.disposition is D.PASS for row in records.values())
    trade=records[G.TRADE_RISK].result
    assert trade.sizing.permitted_monetary_risk==Decimal('50') # 10000 * .005
    assert trade.sizing.loss_model.total_loss==Decimal('10') # 100 - 90, zero costs
    assert trade.evaluated_projection.quantity==Decimal('1')
    assert trade.evaluated_projection.modeled_loss==Decimal('10')
    assert trade.evaluated_projection.notional==trade.evaluated_projection.required_cash==Decimal('100')
    p=records[G.PORTFOLIO_RISK].result.projection
    assert (p.before.open_risk,p.after.open_risk,p.before.gross_exposure,p.after.gross_exposure)==tuple(map(Decimal,('0','10','0','100')))
    assert p.after.available_cash==Decimal('9900')
    state=records[G.RISK_STATE].result.projection
    assert state.measurement.drawdown_fraction==0 and state.evaluated_state is RiskState.NORMAL
    assert state.base_permitted_risk==state.adjusted_permitted_risk==Decimal('50')
    assert state.proposed_modeled_risk==Decimal('10')
    assert len(i.switch_inventory.covered_scopes)==6 and not records[G.SAFETY_GATE].result.active_blocking_switches
    # Age horizons are T+30; configured approval cap is AT+30 = T+33. Earliest wins.
    assert d.approval_expires_at==T+timedelta(seconds=30)
    assert d.intent_identity==i.intent.intent_identity
    assert R.INTEGRATED_GATE_COVERAGE_CONFIRMED in codes(d)
    assert len(manifest(d)['gates'])==4
    assert all(row['disposition']=='PASS' for row in manifest(d)['gates'])


def test_suspended_rejection_oracle_a():
    snap=portfolio(equity=Decimal('9000'),available_cash=Decimal('9000'))
    i=inputs(snapshot=snap);rows=records_by_gate(i);d=evaluate_risk(i)
    assert rows[G.TRADE_RISK].disposition is D.PASS and rows[G.PORTFOLIO_RISK].disposition is D.PASS
    assert rows[G.RISK_STATE].result.projection.evaluated_state is RiskState.SUSPENDED
    assert rows[G.SAFETY_GATE].disposition is D.PASS
    assert d.outcome is O.REJECTED and R.SUSPENDED_STATE_ACTIVE in codes(d)
    assert d.approval_expires_at is None


def test_portfolio_and_system_rejection_oracle_b():
    cfg=configuration();cfg=replace(cfg,operating_limits=replace(cfg.operating_limits,max_instrument_concentration_fraction=Decimal('.1')))
    p=intent();snap=portfolio((position(exposure='990',risk='10'),))
    i=inputs(proposal=p,snapshot=snap,cfg=cfg,switch_inventory=inventory(p,switches=(switch(p=p),)))
    rows=records_by_gate(i);d=evaluate_risk(i)
    assert rows[G.TRADE_RISK].disposition is D.PASS
    assert rows[G.PORTFOLIO_RISK].result.projection.after.gross_exposure==Decimal('1090')
    assert rows[G.RISK_STATE].disposition is D.PASS
    assert {R.INSTRUMENT_CONCENTRATION_EXCEEDED,R.SYSTEM_KILL_SWITCH_ACTIVE}<=set(codes(d))
    assert d.outcome is O.REJECTED


def test_stale_health_rejection_oracle_c():
    h=health(as_of=T-timedelta(seconds=31),data_through=T-timedelta(seconds=31))
    i=inputs(health_evidence=h);rows=records_by_gate(i);d=evaluate_risk(i)
    assert all(rows[g].disposition is D.PASS for g in (G.TRADE_RISK,G.PORTFOLIO_RISK,G.RISK_STATE))
    assert d.outcome is O.REJECTED and R.STRATEGY_EVIDENCE_STALE in codes(d)


def test_individually_passing_records_from_different_snapshot_reject_oracle_d():
    i=inputs();records=_collect(i)
    changed=inputs(snapshot=portfolio(available_cash=Decimal('9999')))
    assert all(r.disposition is D.PASS for r in records)
    d=_finalize(changed,records)
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)
    assert d.approval_expires_at is None


@pytest.mark.parametrize('gate',list(G))
def test_each_mandatory_gate_cannot_be_removed(gate):
    i=inputs();rows=tuple(r for r in _collect(i) if r.gate is not gate)
    d=_finalize(i,rows)
    assert d.outcome is O.REJECTED and R.INTEGRATED_GATE_BLOCKED in codes(d)


def test_duplicate_gate_coverage_does_not_compensate_for_missing_gate():
    i=inputs();rows=_collect(i)
    assert _finalize(i,(rows[0],rows[0],rows[2],rows[3])).outcome is O.REJECTED
    assert _finalize(i,()).outcome is O.REJECTED


def test_no_bypass_or_result_injection_parameter():
    i=inputs()
    for key in ('override','skip_safety','admin_bypass','gate_results','enabled_gates'):
        with pytest.raises(TypeError):evaluate_risk(i,**{key:True})
        with pytest.raises(TypeError):replace(i,**{key:True})


def test_trade_rejection_blocks_dependent_calculations_but_preserves_independent_safety():
    p=intent();i=inputs(proposal=p,sizing=assumptions(proposed_quantity=Decimal('6')),
        switch_inventory=inventory(p,switches=(switch(p=p),)),health_evidence=health(p,state=H.UNHEALTHY))
    rows=records_by_gate(i);d=evaluate_risk(i)
    assert rows[G.TRADE_RISK].disposition is D.REJECTED
    for gate in (G.PORTFOLIO_RISK,G.RISK_STATE):
        assert rows[gate].disposition is D.BLOCKED and rows[gate].result is None
    assert rows[G.SAFETY_GATE].disposition is D.REJECTED
    assert {R.TRADE_RISK_LIMIT_EXCEEDED,R.INTEGRATED_GATE_BLOCKED,R.SYSTEM_KILL_SWITCH_ACTIVE,R.STRATEGY_HEALTH_UNHEALTHY}<=set(codes(d))
    assert d.outcome is O.REJECTED


@pytest.mark.parametrize('field',['sizing_assumptions','portfolio_policy','portfolio_context','state_policy','drawdown_evidence',
    'safety_policy','switch_inventory','lifecycle_evidence','health_evidence'])
def test_missing_required_gate_evidence_never_becomes_permission(field):
    i=replace(inputs(),**{field:None});d=evaluate_risk(i)
    assert d.outcome is O.REJECTED and d.approval_expires_at is None
    assert len(manifest(d)['gates'])==4


@pytest.mark.parametrize('field',['intent','configuration','snapshot','market_context','evaluated_at'])
def test_missing_root_contract_fails_before_any_evaluation(field):
    with pytest.raises(ValueError):replace(inputs(),**{field:None})


@pytest.mark.parametrize('change',['user','portfolio','environment','root_market','entry_market','future_snapshot','future_market'])
def test_context_root_mismatch_records_exact_rejection_not_fabricated_context(change):
    i=inputs()
    if change in ('user','portfolio'):
        name='user_id' if change=='user' else 'portfolio_id'
        i=replace(i,snapshot=replace(i.snapshot,scope=replace(i.snapshot.scope,**{name:UUID(int=999)})))
    elif change=='environment':i=replace(i,configuration=replace(i.configuration,environment=Environment.LAB))
    elif change=='root_market':i=replace(i,market_context=market(data_identity=OTHER))
    elif change=='entry_market':i=replace(i,sizing_assumptions=replace(i.sizing_assumptions,entry_context=market(data_identity=OTHER)))
    elif change=='future_snapshot':i=replace(i,snapshot=replace(i.snapshot,as_of=AT+timedelta(seconds=1)))
    else:i=replace(i,market_context=market(observed_at=AT+timedelta(seconds=1)))
    d=evaluate_risk(i)
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)
    assert d.portfolio_snapshot is i.snapshot and d.market_context is i.market_context and d.configuration is i.configuration


@pytest.mark.parametrize('change',['instrument_id','canonical_symbol','provider','timeframe'])
def test_different_market_instrument_context_is_rejected(change):
    i=inputs();v={'instrument_id':UUID(int=77),'canonical_symbol':'ETH/USDT','provider':'OTHER','timeframe':'4h'}[change]
    market_changed=replace(i.market_context,instrument=replace(i.market_context.instrument,**{change:v}))
    d=evaluate_risk(replace(i,market_context=market_changed))
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)


@pytest.mark.parametrize('field',['intent_identity','configuration_identity','snapshot_identity','evaluated_at','assumptions_identity'])
def test_trade_result_context_cannot_be_spliced(monkeypatch,field):
    original=engine.evaluate_trade_risk
    # Use an independently valid record, not an invalid mutated dataclass.
    other_i=inputs()
    if field=='intent_identity':other_i=inputs(proposal=intent(strategy_decision_identity=OTHER))
    elif field=='configuration_identity':other_i=inputs(cfg=replace(configuration(),version='other-context'))
    elif field=='snapshot_identity':other_i=inputs(snapshot=portfolio(available_cash=Decimal('9999')))
    elif field=='evaluated_at':other_i=inputs(at=AT+timedelta(seconds=1))
    else:other_i=inputs(sizing=assumptions(proposed_quantity=Decimal('2')))
    source=original(other_i.intent,other_i.configuration,other_i.snapshot,other_i.sizing_assumptions,other_i.evaluated_at)
    monkeypatch.setattr(engine,'evaluate_trade_risk',lambda *args:source)
    d=evaluate_risk(inputs())
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)
    assert {g['gate']:g['disposition'] for g in manifest(d)['gates']}[G.PORTFOLIO_RISK.value]=='BLOCKED'


@pytest.mark.parametrize('gate',[G.PORTFOLIO_RISK,G.RISK_STATE,G.SAFETY_GATE])
def test_individually_valid_gate_result_from_different_context_is_rejected(monkeypatch,gate):
    other=inputs(at=AT+timedelta(seconds=1));source=records_by_gate(other)[gate].result
    assert source is not None
    names={G.PORTFOLIO_RISK:'evaluate_portfolio_risk',G.RISK_STATE:'evaluate_risk_state',G.SAFETY_GATE:'evaluate_safety_gate'}
    monkeypatch.setattr(engine,names[gate],lambda *args:source)
    d=evaluate_risk(inputs())
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)


def test_state_capacity_is_derived_from_actual_trade_loss_and_budget(monkeypatch):
    original=engine.evaluate_risk_state;seen=[]
    def wrapper(s,c,p,e,capacity,at):
        seen.append(capacity)
        return original(s,c,p,e,capacity,at)
    monkeypatch.setattr(engine,'evaluate_risk_state',wrapper)
    i=inputs(sizing=assumptions(proposed_quantity=Decimal('2')))
    d=evaluate_risk(i)
    assert d.outcome is O.APPROVED and seen[0].proposed_modeled_risk==Decimal('20')
    assert seen[0].base_permitted_risk==Decimal('50')
    assert seen[0].intent_identity==i.intent.intent_identity


def test_smaller_state_proposal_cannot_authorize_larger_exposure(monkeypatch):
    original=engine.evaluate_risk_state
    def wrapper(s,c,p,e,capacity,at):return original(s,c,p,e,replace(capacity,proposed_modeled_risk=Decimal('1')),at)
    monkeypatch.setattr(engine,'evaluate_risk_state',wrapper)
    d=evaluate_risk(inputs())
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)


@pytest.mark.parametrize('change',['family','version','configuration'])
def test_wrong_strategy_health_does_not_authorize_intent(change):
    h=health();strategy=h.strategy
    if change=='family':strategy=StrategyDefinition('OTHER',strategy.version,{})
    elif change=='version':strategy=StrategyDefinition(strategy.family,'2.0.0',{})
    else:strategy=StrategyDefinition(strategy.family,strategy.version,{'test':Decimal('1')})
    d=evaluate_risk(inputs(health_evidence=replace(h,strategy=strategy)))
    assert d.outcome is O.REJECTED and R.STRATEGY_EVIDENCE_MISMATCH in codes(d)


@pytest.mark.parametrize('gate',[G.TRADE_RISK,G.PORTFOLIO_RISK,G.RISK_STATE,G.SAFETY_GATE])
def test_unavailable_or_malformed_gate_cannot_partially_approve(monkeypatch,gate):
    name={G.TRADE_RISK:'evaluate_trade_risk',G.PORTFOLIO_RISK:'evaluate_portfolio_risk',
        G.RISK_STATE:'evaluate_risk_state',G.SAFETY_GATE:'evaluate_safety_gate'}[gate]
    monkeypatch.setattr(engine,name,lambda *args:None)
    d=evaluate_risk(inputs())
    assert d.outcome is O.REJECTED and R.INTEGRATED_EVIDENCE_UNAVAILABLE in codes(d)


def test_gate_exception_is_sanitized_and_other_independent_failures_survive(monkeypatch):
    def broken(*args):raise ValueError('PRIVATE_SENTINEL_DO_NOT_EXPOSE')
    monkeypatch.setattr(engine,'evaluate_trade_risk',broken)
    i=inputs(switch_inventory=inventory(switches=(switch(),)))
    d=evaluate_risk(i)
    assert d.outcome is O.REJECTED and R.SYSTEM_KILL_SWITCH_ACTIVE in codes(d)
    assert 'PRIVATE_SENTINEL' not in json.dumps(to_wire(d))
    assert R.INTEGRATED_GATE_BLOCKED in codes(d)


def test_portfolio_failure_does_not_hide_independent_state_and_safety_failures():
    i=inputs(snapshot=portfolio(equity=Decimal('9000'),available_cash=Decimal('9000')),
             switch_inventory=inventory(switches=(switch(),)),health_evidence=health(state=H.UNHEALTHY))
    i=replace(i,portfolio_context=replace(i.portfolio_context,snapshot_identity=OTHER))
    d=evaluate_risk(i)
    assert {R.RISK_CONTEXT_MISMATCH,R.SUSPENDED_STATE_ACTIVE,R.SYSTEM_KILL_SWITCH_ACTIVE,R.STRATEGY_HEALTH_UNHEALTHY}<=set(codes(d))


@pytest.mark.parametrize('kind',['drawdown','inventory','lifecycle','health'])
def test_future_evidence_fails_closed(kind):
    i=inputs();future=AT+timedelta(seconds=1)
    name={'drawdown':'drawdown_evidence','inventory':'switch_inventory','lifecycle':'lifecycle_evidence','health':'health_evidence'}[kind]
    row=replace(getattr(i,name),as_of=future)
    assert evaluate_risk(replace(i,**{name:row})).outcome is O.REJECTED


@pytest.mark.parametrize('kind',['drawdown','inventory','lifecycle','health'])
def test_stale_evidence_fails_closed(kind):
    i=inputs(at=T+timedelta(seconds=31))
    d=evaluate_risk(i)
    assert d.outcome is O.REJECTED and d.approval_expires_at is None


@pytest.mark.parametrize('boundary',['intent','market','snapshot','portfolio','drawdown','inventory','lifecycle','health','switch','correlation'])
def test_expiry_uses_earliest_explicit_validity_boundary(boundary):
    end=AT+timedelta(seconds=2);i=inputs()
    if boundary=='intent':i=inputs(proposal=intent(expires_at=end))
    elif boundary=='market':i=inputs(proposal=intent(market_context=market(expires_at=end)),sizing=assumptions(entry_context=market(expires_at=end),proposed_quantity=Decimal('1')))
    elif boundary=='snapshot':i=inputs(snapshot=portfolio(expires_at=end))
    elif boundary=='portfolio':i=replace(i,portfolio_context=replace(i.portfolio_context,expires_at=end))
    elif boundary=='drawdown':i=replace(i,drawdown_evidence=replace(i.drawdown_evidence,expires_at=end))
    elif boundary=='inventory':i=replace(i,switch_inventory=replace(i.switch_inventory,valid_until=end))
    elif boundary=='lifecycle':i=replace(i,lifecycle_evidence=replace(i.lifecycle_evidence,valid_until=end))
    elif boundary=='health':i=replace(i,health_evidence=replace(i.health_evidence,valid_until=end))
    elif boundary=='switch':i=replace(i,switch_inventory=inventory(switches=(switch(active=False,valid_until=end),)))
    else:
        snap=portfolio((position('ETH/USDT'),));i=inputs(snapshot=snap)
        i=replace(i,portfolio_context=replace(i.portfolio_context,correlations=(correlation(expires_at=end),)))
    d=evaluate_risk(i)
    assert d.outcome is O.APPROVED and d.approval_expires_at==end


@pytest.mark.parametrize('field',['max_inventory_age','max_switch_age','max_health_age','max_health_data_age','max_lifecycle_age'])
def test_expiry_includes_each_safety_freshness_horizon(field):
    hp=replace(safety_policy(),**{field:timedelta(seconds=5)})
    inv=inventory(switches=(switch(active=False),))
    d=evaluate_risk(inputs(hp=hp,switch_inventory=inv))
    assert d.outcome is O.APPROVED and d.approval_expires_at==T+timedelta(seconds=5)


def test_expiry_includes_drawdown_and_correlation_age():
    i=inputs(sp=replace(state_policy(),max_evidence_age=timedelta(seconds=5)))
    assert evaluate_risk(i).approval_expires_at==T+timedelta(seconds=5)
    snap=portfolio((position('ETH/USDT'),));pp=replace(portfolio_policy(),operating_correlation_max_age=timedelta(seconds=5))
    i=inputs(snapshot=snap,pp=pp)
    i=replace(i,portfolio_context=replace(i.portfolio_context,correlations=(correlation(),)))
    assert evaluate_risk(i).approval_expires_at==T+timedelta(seconds=5)


def test_maximum_approval_age_is_respected():
    c=configuration();c=replace(c,operating_limits=replace(c.operating_limits,max_approval_age=timedelta(seconds=1)))
    d=evaluate_risk(inputs(cfg=c))
    assert d.outcome is O.APPROVED and d.approval_expires_at==AT+timedelta(seconds=1)


def test_gate_age_boundary_can_pass_but_no_positive_approval_interval_remains():
    i=inputs(at=T+timedelta(seconds=30));rows=_collect(i)
    assert all(r.disposition is D.PASS for r in rows)
    d=evaluate_risk(i)
    assert d.outcome is O.REJECTED and R.INTEGRATED_APPROVAL_EXPIRY_UNAVAILABLE in codes(d)


def test_expired_operational_switch_does_not_create_a_false_expiry_constraint():
    row=switch(expires_at=T+timedelta(seconds=1))
    d=evaluate_risk(inputs(switch_inventory=inventory(switches=(row,))))
    assert d.outcome is O.APPROVED and d.approval_expires_at==T+timedelta(seconds=30)


def test_irrelevant_switch_row_does_not_shorten_evidence_validity():
    from app.risk.safety_gate import KillSwitchTarget
    row=switch(target=KillSwitchTarget(Scope.USER,user_id=UUID(int=999)),valid_until=T+timedelta(seconds=1))
    assert evaluate_risk(inputs(switch_inventory=inventory(switches=(row,)))).outcome is O.APPROVED


def test_inability_to_derive_expiry_is_controlled_rejection(monkeypatch):
    def broken(*args):raise OverflowError('PRIVATE_TIMESTAMP_DETAIL')
    monkeypatch.setattr(engine,'derive_approval_validity',broken)
    d=evaluate_risk(inputs())
    assert d.outcome is O.REJECTED and R.INTEGRATED_APPROVAL_EXPIRY_UNAVAILABLE in codes(d)
    assert 'PRIVATE_TIMESTAMP_DETAIL' not in json.dumps(to_wire(d))


def test_rejected_is_not_pending_and_requires_new_evaluation():
    i=inputs(switch_inventory=inventory(switches=(switch(),)))
    rejected=evaluate_risk(i)
    assert rejected.outcome is O.REJECTED and rejected.approval_expires_at is None
    fresh=inputs(switch_inventory=inventory(switches=(switch(active=False),)))
    approved=evaluate_risk(fresh)
    assert approved.outcome is O.APPROVED and approved.decision_identity!=rejected.decision_identity
    assert rejected.outcome is O.REJECTED


def test_approval_is_exact_intent_not_general_instrument_permission():
    a=evaluate_risk(inputs());b=evaluate_risk(inputs(proposal=intent(strategy_decision_identity=OTHER)))
    assert a.outcome is b.outcome is O.APPROVED
    assert a.intent_identity!=b.intent_identity and a.decision_identity!=b.decision_identity


def test_input_results_findings_and_decision_are_immutable():
    i=inputs();before=to_wire(i);rows=_collect(i);d=evaluate_risk(i)
    for obj in (i,rows[0],rows[0].result,rows[0].findings[0],d):
        key=next(f.name for f in fields(obj) if f.init)
        with pytest.raises(FrozenInstanceError):setattr(obj,key,getattr(obj,key))
    assert to_wire(i)==before
    with pytest.raises(TypeError):d.findings[0].evidence['changed']=True


def test_equivalent_decimals_timezones_and_ordering_preserve_identity():
    i=inputs();a=evaluate_risk(i);zone=timezone(timedelta(hours=5,minutes=30))
    p=replace(i.intent,reference_price=Decimal('100.000'),invalidation_price=Decimal('90.00'),decision_at=T.astimezone(zone))
    j=inputs(proposal=p,sizing=replace(i.sizing_assumptions,proposed_quantity=Decimal('1.000'),requested_risk_fraction=Decimal('.005000')),at=AT.astimezone(zone))
    with localcontext() as ctx:
        ctx.prec=3;ctx.rounding=ROUND_UP
        b=evaluate_risk(j)
    assert a.decision_identity==b.decision_identity
    rows=_collect(i)
    assert _finalize(i,tuple(reversed(rows))).decision_identity==a.decision_identity
    assert replace(a,findings=tuple(reversed(a.findings))).decision_identity==a.decision_identity
    assert to_wire(b)['intent']['reference_price']=='100.000'


@pytest.mark.parametrize('change',['equity','cost','size','requested_risk','portfolio_policy','state_policy','safety_policy',
    'context_source','drawdown_peak','inventory_source','health_metric','time','strategy'])
def test_material_inputs_change_final_identity(change):
    i=inputs();a=evaluate_risk(i)
    if change=='equity':i=inputs(snapshot=portfolio(equity=Decimal('10001')))
    elif change=='cost':i=inputs(sizing=assumptions(proposed_quantity=Decimal('1'),costs=combined_costs()))
    elif change=='size':i=inputs(sizing=assumptions(proposed_quantity=Decimal('2')))
    elif change=='requested_risk':i=inputs(sizing=assumptions(proposed_quantity=Decimal('1'),requested_risk_fraction=Decimal('.004')))
    elif change=='portfolio_policy':i=inputs(pp=replace(portfolio_policy(),version='new-hypothesis'))
    elif change=='state_policy':i=inputs(sp=replace(state_policy(),caution_entry=Decimal('.031')))
    elif change=='safety_policy':i=inputs(hp=replace(safety_policy(),minimum_completed_trades=11))
    elif change=='context_source':i=replace(i,portfolio_context=replace(i.portfolio_context,source_identity=OTHER))
    elif change=='drawdown_peak':i=replace(i,drawdown_evidence=replace(i.drawdown_evidence,historical_peak=Decimal('10001')))
    elif change=='inventory_source':i=replace(i,switch_inventory=replace(i.switch_inventory,source_identity=OTHER))
    elif change=='health_metric':i=replace(i,health_evidence=replace(i.health_evidence,metrics=replace(i.health_evidence.metrics,expectancy=Decimal('3'))))
    elif change=='time':i=inputs(at=AT+timedelta(seconds=1))
    else:i=inputs(proposal=intent(strategy=StrategyDefinition('TEST_RESEARCH','1.0.1',{})))
    assert evaluate_risk(i).decision_identity!=a.decision_identity


def test_leaf_typed_findings_preserve_gate_outcomes_and_blocking_status():
    d=evaluate_risk(inputs(switch_inventory=inventory(switches=(switch(),))))
    row=entries(d,R.SYSTEM_KILL_SWITCH_ACTIVE)[0]
    assert row['gate']=='SAFETY_GATE' and row['gate_outcome']=='REJECTED' and row['blocking'] is True
    assert row['source_result_identity'].startswith('sha256:')
    positive=entries(d,R.TRADE_RISK_WITHIN_LIMIT)[0]
    assert positive['gate']=='TRADE_RISK' and positive['blocking'] is False
    assert codes(d)==tuple(sorted(codes(d),key=lambda c:c.value))


def test_evaluators_are_called_once_in_fixed_order(monkeypatch):
    seen=[]
    for name in ('evaluate_trade_risk','evaluate_portfolio_risk','evaluate_risk_state','evaluate_safety_gate'):
        original=getattr(engine,name)
        def wrapper(*args,_name=name,_original=original):
            seen.append(_name);return _original(*args)
        monkeypatch.setattr(engine,name,wrapper)
    assert evaluate_risk(inputs()).outcome is O.APPROVED
    assert seen==['evaluate_trade_risk','evaluate_portfolio_risk','evaluate_risk_state','evaluate_safety_gate']


def test_no_financial_math_io_or_execution_implementation_added():
    folder=Path(__file__).parents[1]/'app/risk/integrated'
    for file in folder.glob('*.py'):
        tree=ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[node.module or ''] if isinstance(node,ast.ImportFrom) else [n.name for n in node.names]
                assert not any(any(x in name.lower() for x in ('sqlalchemy','psycopg','httpx','requests','fastapi','app.db','app.market_data')) for name in names)
            if isinstance(node,ast.Call):
                assert not ast.unparse(node.func).endswith(('.now','.utcnow','.commit','.execute','.submit_order'))
                assert not ast.unparse(node.func).endswith(('calculate_trade_size','project_size','unit_loss','exact_product','ratio'))
    assert not any(f.name in ('order','orders','execution_authority','consumed','consumption_id') for f in fields(evaluate_risk(inputs())))


def test_existing_decision_rejection_audit_exception_never_weakens_approval():
    from test_risk_domain import decision as legacy_decision,snapshot as legacy_snapshot,scope as legacy_scope
    wrong=legacy_snapshot(scope=replace(legacy_scope(),user_id=UUID(int=999)))
    reason=RiskFinding(R.INTEGRATED_CONTEXT_MISMATCH,{'context':'test'})
    d=legacy_decision(portfolio_snapshot=wrong,outcome=O.REJECTED,findings=(reason,),approval_expires_at=None)
    assert d.outcome is O.REJECTED
    with pytest.raises(ValueError):replace(d,outcome=O.APPROVED,approval_expires_at=AT+timedelta(seconds=1))
    with pytest.raises(ValueError):legacy_decision(portfolio_snapshot=wrong,outcome=O.REJECTED,
        findings=(RiskFinding(R.CONTEXT_EXPIRED,{}),),approval_expires_at=None)


def test_four_fresh_hash_seeds_full_approval_rejections_order_context_and_expiry():
    script="""import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_integrated_risk import *
i=inputs();a=evaluate_risk(i)
single=evaluate_risk(inputs(snapshot=portfolio(equity=Decimal('9000'),available_cash=Decimal('9000'))))
trade_rejected=evaluate_risk(inputs(sizing=assumptions(proposed_quantity=Decimal('6'))))
rows={s.value:switch(s,number=n) for n,s in enumerate(Scope,1)}
ordered=tuple(rows[k] for k in set(rows))
multi_i=inputs(switch_inventory=inventory(switches=ordered),health_evidence=health(state=H.UNHEALTHY))
multi=evaluate_risk(multi_i)
reordered=evaluate_risk(replace(multi_i,switch_inventory=inventory(switches=tuple(reversed(ordered)))))
wrong=evaluate_risk(replace(i,market_context=market(data_identity=OTHER)))
print(json.dumps({'approved':to_wire(a),'single':to_wire(single),'trade_rejected':to_wire(trade_rejected),'multi':to_wire(multi),
                 'reordered':reordered.decision_identity,'mismatch':to_wire(wrong)},sort_keys=True))
"""
    rows=[]
    for seed in ('1','2','3','4'):
        child=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        rows.append(json.loads(child.stdout))
    assert all(row==rows[0] for row in rows)
    assert rows[0]['approved']['outcome']=='APPROVED'
    assert rows[0]['trade_rejected']['outcome']=='REJECTED'
    assert rows[0]['approved']['approval_expires_at']==to_wire(T+timedelta(seconds=30))
    assert rows[0]['multi']['decision_identity']==rows[0]['reordered']
    assert rows[0]['mismatch']['outcome']=='REJECTED'


@pytest.mark.parametrize('field',['portfolio_intent','portfolio_snapshot','portfolio_policy','drawdown_scope','drawdown_equity',
    'drawdown_snapshot','drawdown_state','switch_intent','switch_scope','switch_market'])
def test_all_context_input_bindings_reject_before_any_possible_final_approval(field):
    i=inputs()
    if field.startswith('portfolio_'):
        name={'portfolio_intent':'intent_identity','portfolio_snapshot':'snapshot_identity','portfolio_policy':'policy_identity'}[field]
        i=replace(i,portfolio_context=replace(i.portfolio_context,**{name:OTHER}))
    elif field.startswith('drawdown_'):
        name={'drawdown_scope':'scope','drawdown_equity':'current_equity','drawdown_snapshot':'snapshot_identity','drawdown_state':'previous_state'}[field]
        value={'scope':replace(i.intent.scope,portfolio_id=UUID(int=999)),'current_equity':Decimal('9999'),
               'snapshot_identity':OTHER,'previous_state':RiskState.CAUTION}[name]
        i=replace(i,drawdown_evidence=replace(i.drawdown_evidence,**{name:value}))
    else:
        name={'switch_intent':'intent_identity','switch_scope':'scope','switch_market':'market_context_identity'}[field]
        value=replace(i.intent.scope,user_id=UUID(int=999)) if name=='scope' else OTHER
        i=replace(i,switch_inventory=replace(i.switch_inventory,**{name:value}))
    d=evaluate_risk(i)
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)


def test_state_peak_must_match_exact_drawdown_evidence_even_if_result_claims_pass(monkeypatch):
    from app.risk.risk_states import DrawdownMeasurement
    original=engine.evaluate_risk_state
    def changed(*args):
        result=original(*args)
        projection=replace(result.projection,measurement=DrawdownMeasurement(Decimal('9999'),Decimal('10000')))
        return replace(result,projection=projection)
    monkeypatch.setattr(engine,'evaluate_risk_state',changed)
    d=evaluate_risk(inputs())
    assert d.outcome is O.REJECTED and R.INTEGRATED_CONTEXT_MISMATCH in codes(d)


def test_nonfinite_missing_and_untyped_values_cannot_enter_integrated_contract():
    with pytest.raises(ValueError):inputs(sizing=assumptions(entry_reference_price=Decimal('NaN')))
    with pytest.raises(ValueError):inputs(snapshot=portfolio(equity=Decimal('Infinity')))
    with pytest.raises(ValueError):replace(inputs(),state_policy=object())
    with pytest.raises(ValueError):replace(inputs(),evaluated_at=T.replace(tzinfo=None))


def test_unsupported_valuation_and_live_shorting_remain_rejected():
    i=inputs();scope=replace(i.intent.scope,base_currency='USD')
    p=replace(i.intent,scope=scope)
    snap=replace(i.snapshot,scope=scope)
    d=evaluate_risk(inputs(proposal=p,snapshot=snap))
    assert d.outcome is O.REJECTED and R.RISK_CURRENCY_UNSUPPORTED in codes(d)
    from app.risk import Direction
    p=intent(direction=Direction.SHORT,environment=Environment.LIVE)
    snap=replace(portfolio(),scope=p.scope)
    cfg=replace(configuration(),environment=Environment.LIVE)
    d=evaluate_risk(inputs(proposal=p,snapshot=snap,cfg=cfg))
    assert d.outcome is O.REJECTED and R.SHORT_RESEARCH_ONLY in codes(d)


def test_immutable_expiry_evidence_and_ordering():
    a=ValidityBoundary('A',AT+timedelta(seconds=1),ID)
    b=ValidityBoundary('B',AT+timedelta(seconds=2),OTHER)
    x=ApprovalValidity(AT,(a,b));y=ApprovalValidity(AT,(b,a))
    assert x.validity_identity==y.validity_identity and x.expires_at==a.valid_until
    with pytest.raises(ValueError):ApprovalValidity(AT,())
    with pytest.raises(ValueError):ApprovalValidity(AT,(a,a))
    with pytest.raises(ValueError):ApprovalValidity(a.valid_until,(a,))
