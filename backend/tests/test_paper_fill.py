"""11B oracle pricing and exact request-bound calculation evidence, no ledger/venue."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta, timezone
from decimal import Decimal, localcontext, ROUND_UP
import ast
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from app.execution.paper_fill import *
from app.execution import ExecutionEnvironment
from app.risk import Direction, to_wire
from app.strategies.decisions import DecisionType as A
from app.strategy_lab.costs import ResearchCostModel
from test_execution_domain import request
from test_integrated_risk import inputs, assumptions, AT, T, OTHER
from test_trade_risk_sizing import intent

D=Decimal
SOURCE='sha256:'+'d'*64


def costs(**changes):
    args=dict(trading_fee_rate=D('.01'),spread_rate=D('.02'),slippage_rate=D('.01'),execution_delay=timedelta(0))
    args.update(changes)
    return PaperFillAssumptions(ResearchCostModel(**args),timedelta(seconds=5))


def market(r,**changes):
    args=dict(request_identity=r.request_identity,instrument=r.authorization.intent.instrument,
        environment=ExecutionEnvironment.PAPER,reference_price=D('100'),priced_at=AT,
        observed_at=AT,valid_until=AT+timedelta(seconds=20),source_identity=SOURCE,is_valid=True)
    args.update(changes)
    return PaperMarketEvidence(**args)


def calculate(r=None,m=None,c=None,action=A.ENTER_LONG,executed_at=AT,evaluated_at=AT):
    r=r or request()
    return calculate_paper_fill(r,m or market(r),c or costs(),action,
        executed_at=executed_at,evaluated_at=evaluated_at)


@pytest.mark.parametrize('direction,action,price,gross,fee,cash',[
    (Direction.LONG,A.ENTER_LONG,'102','204','2.04','-206.04'),
    (Direction.LONG,A.EXIT_LONG,'98','196','1.96','194.04'),
    (Direction.SHORT,A.ENTER_SHORT,'98','196','1.96','194.04'),
    (Direction.SHORT,A.EXIT_SHORT,'102','204','2.04','-206.04')])
def test_four_leg_hand_calculated_oracles(direction,action,price,gross,fee,cash):
    # Independent literal oracle: 100 reference, Q=2, half-spread=1/unit,
    # slippage=1/unit; fee 1% of modeled gross, not hidden in price.
    x=PaperLegCalculation(direction,action,D('100'),D('2'),costs())
    assert x.modeled_execution_price==D(price)
    assert x.reference_notional==D('200') and x.gross_notional==D(gross)
    assert x.spread_cost==x.slippage_cost==D('2')
    assert x.fee==D(fee) and x.net_cash_effect==D(cash)
    assert x.pricing.fee_rate==D('.01')
    assert x.quantity==D('2')


@pytest.mark.parametrize('direction,action,expected',[
    (Direction.LONG,A.ENTER_LONG,'101'),(Direction.LONG,A.EXIT_LONG,'99'),
    (Direction.SHORT,A.ENTER_SHORT,'99'),(Direction.SHORT,A.EXIT_SHORT,'101')])
@pytest.mark.parametrize('component',['spread','slippage'])
def test_each_adverse_component_direction(direction,action,expected,component):
    c=costs(trading_fee_rate=D('0'),spread_rate=D('.02') if component=='spread' else D('0'),
            slippage_rate=D('.01') if component=='slippage' else D('0'))
    leg=PaperLegCalculation(direction,action,D('100'),D('1'),c)
    assert leg.modeled_execution_price==D(expected)
    assert leg.fee==0
    impact=leg.spread_impact_per_unit if component=='spread' else leg.slippage_impact_per_unit
    assert impact==(D('1') if expected=='101' else D('-1'))


@pytest.mark.parametrize('direction,action,cash',[(Direction.LONG,A.ENTER_LONG,'-202'),(Direction.SHORT,A.ENTER_SHORT,'198')])
def test_fees_remain_separate_from_execution_price(direction,action,cash):
    c=costs(spread_rate=D('0'),slippage_rate=D('0'))
    x=PaperLegCalculation(direction,action,D('100'),D('2'),c)
    assert x.modeled_execution_price==D('100') and x.gross_notional==D('200')
    assert x.fee==D('2') and x.net_cash_effect==D(cash)


@pytest.mark.parametrize('direction,action',[(Direction.LONG,A.ENTER_LONG),(Direction.SHORT,A.ENTER_SHORT)])
def test_request_bound_engine_uses_only_approved_quantity(direction,action):
    i=inputs(proposal=intent(direction=direction),sizing=assumptions(proposed_quantity=D('2')))
    r=request(i=i);m=market(r)
    before=to_wire(r)
    result=calculate(r,m,action=action)
    assert result.leg.quantity==r.quantity==D('2')
    assert result.request is r and result.market is m
    assert to_wire(r)==before
    assert result.request.risk_decision_identity==r.risk_decision_identity
    assert result.eligible_at==r.created_at
    assert not {'order','fill','consumed','position'} & {f.name for f in fields(result)}
    with pytest.raises(TypeError): calculate_paper_fill(r,m,costs(),action,executed_at=AT,evaluated_at=AT,quantity=D('3'))


@pytest.mark.parametrize('action',[A.EXIT_LONG,A.EXIT_SHORT,A.ENTER_SHORT,A.NO_ACTION])
def test_entry_approval_cannot_authorize_exit_or_opposite_action(action):
    with pytest.raises(ValueError): calculate(action=action)


@pytest.mark.parametrize('direction,action',[(Direction.LONG,A.ENTER_SHORT),(Direction.SHORT,A.ENTER_LONG),
    (Direction.LONG,A.EXIT_SHORT),(Direction.SHORT,A.EXIT_LONG),(Direction.LONG,A.NO_ACTION)])
def test_unsupported_direction_action_combinations_fail_closed(direction,action):
    with pytest.raises(ValueError): PaperLegCalculation(direction,action,D('100'),D('1'),costs())


@pytest.mark.parametrize('field',['reference_price','quantity'])
@pytest.mark.parametrize('value',[D('0'),D('-1'),D('NaN'),D('Infinity'),D('-Infinity'),1.0,1,'1',None])
def test_bad_financial_inputs_rejected(field,value):
    args=dict(direction=Direction.LONG,action=A.ENTER_LONG,reference_price=D('100'),quantity=D('1'),assumptions=costs())
    args[field]=value
    with pytest.raises(ValueError): PaperLegCalculation(**args)


@pytest.mark.parametrize('field',['trading_fee_rate','spread_rate','slippage_rate'])
@pytest.mark.parametrize('value',[D('-.01'),D('1.01'),D('NaN'),D('Infinity'),.01,None])
def test_invalid_cost_assumptions_fail_closed(field,value):
    with pytest.raises(ValueError): costs(**{field:value})


def test_combined_adverse_costs_must_allow_positive_price():
    with pytest.raises(ValueError): costs(spread_rate=D('1'),slippage_rate=D('.5'))
    with pytest.raises(ValueError): costs(execution_delay=timedelta(microseconds=-1))
    with pytest.raises(ValueError): replace(costs(),max_market_age=timedelta(0))


@pytest.mark.parametrize('field',['reference_price'])
@pytest.mark.parametrize('value',[D('0'),D('-1'),D('NaN'),D('Infinity'),1.0,None])
def test_invalid_trusted_market_price_rejected(field,value):
    with pytest.raises(ValueError): market(request(),**{field:value})


@pytest.mark.parametrize('field',['request','instrument_id','symbol','provider','timeframe','environment'])
def test_market_request_and_canonical_context_must_match(field):
    r=request();m=market(r)
    if field=='request': m=replace(m,request_identity=OTHER)
    elif field=='environment': m=replace(m,environment=ExecutionEnvironment.LIVE)
    else:
        from uuid import UUID
        key={'instrument_id':'instrument_id','symbol':'canonical_symbol','provider':'provider','timeframe':'timeframe'}[field]
        value={'instrument_id':UUID(int=999),'symbol':'ETH/USDT','provider':'OTHER','timeframe':'4h'}[field]
        m=replace(m,instrument=replace(m.instrument,**{key:value}))
    with pytest.raises(ValueError): calculate(r,m)


@pytest.mark.parametrize('validity',[False,None])
def test_unknown_and_invalid_market_health_never_mean_valid(validity):
    r=request()
    with pytest.raises(ValueError): calculate(r,market(r,is_valid=validity))


def test_stale_source_price_cannot_be_refreshed_by_recent_observation():
    r=request();m=market(r,priced_at=AT-timedelta(seconds=6))
    with pytest.raises(ValueError,match='stale'): calculate(r,m)
    # Max-age equality is accepted, expiry equality is not.
    assert calculate(r,market(r,priced_at=AT-timedelta(seconds=5))).leg.reference_price==D('100')
    expired=market(r,priced_at=AT-timedelta(seconds=2),observed_at=AT-timedelta(seconds=1),valid_until=AT)
    with pytest.raises(ValueError,match='expired'): calculate(r,expired)


def test_future_market_cannot_determine_earlier_fill():
    r=request();future=AT+timedelta(seconds=1)
    m=market(r,priced_at=future,observed_at=future)
    with pytest.raises(ValueError,match='Future'): calculate(r,m)
    with pytest.raises(ValueError,match='Future'): calculate(r,m,executed_at=AT,evaluated_at=future)


def test_explicit_times_and_delay_are_causal_and_not_auto_next_open():
    r=request();c=costs(execution_delay=timedelta(seconds=2))
    with pytest.raises(ValueError,match='delay'): calculate(r,c=c)
    x=calculate(r,c=c,executed_at=AT+timedelta(seconds=2),evaluated_at=AT+timedelta(seconds=2))
    assert x.eligible_at==AT+timedelta(seconds=2)
    with pytest.raises(ValueError): calculate(r,executed_at=AT-timedelta(seconds=1))
    with pytest.raises(ValueError): calculate(r,executed_at=AT+timedelta(seconds=1),evaluated_at=AT)
    with pytest.raises(ValueError): calculate(r,executed_at=AT.replace(tzinfo=None))


def test_expired_approval_is_not_renewed_by_market_freshness_or_delay():
    r=request();late=AT+timedelta(seconds=27)
    m=market(r,priced_at=late,observed_at=late,valid_until=late+timedelta(seconds=10))
    with pytest.raises(ValueError,match='expired'): calculate(r,m,executed_at=late,evaluated_at=late)
    with pytest.raises(ValueError): calculate(r,c=costs(execution_delay=timedelta(hours=1)))


def test_exact_small_and_large_decimal_oracles_and_ambient_context_independence():
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_UP
        x=PaperLegCalculation(Direction.LONG,A.ENTER_LONG,D('.000001'),D('.01'),costs())
    assert x.modeled_execution_price==D('.00000102')
    assert x.gross_notional==D('.0000000102')
    assert x.fee==D('.000000000102') and x.net_cash_effect==D('-.000000010302')
    c=costs(trading_fee_rate=D('0'),spread_rate=D('0'),slippage_rate=D('0'))
    large=PaperLegCalculation(Direction.LONG,A.ENTER_LONG,D('12345678901234567890.123456789'),D('1'),c)
    assert to_wire(large)['pricing']['fill_price']=='12345678901234567890.1234567890'


def test_unsafe_arithmetic_is_controlled_not_nan_or_fake_fill(monkeypatch):
    module=importlib.import_module('app.execution.paper_fill.pricing')
    def broken(*args): raise OverflowError('numeric failure')
    monkeypatch.setattr(module,'price_fill',broken)
    with pytest.raises(ValueError,match='arithmetic'): calculate()


def test_existing_price_primitive_is_called_once_without_research_simulation(monkeypatch):
    module=importlib.import_module('app.execution.paper_fill.pricing');original=module.price_fill;calls=[]
    def observed(*args): calls.append(args);return original(*args)
    def forbidden(*args,**kwargs): raise AssertionError('No execution, provider or persistence allowed')
    monkeypatch.setattr(module,'price_fill',observed)
    sim=importlib.import_module('app.strategy_lab.execution.engine')
    monkeypatch.setattr(sim,'simulate_execution',forbidden)
    result=calculate()
    assert len(calls)==1 and result.leg.pricing==original(*calls[0])


def test_results_and_inputs_are_immutable_and_repeated_calculation_identical():
    r=request();m=market(r);c=costs();before=(to_wire(r),to_wire(m),to_wire(c))
    a=calculate(r,m,c);b=calculate(r,m,c)
    assert a==b and a.calculation_identity==b.calculation_identity
    assert (to_wire(r),to_wire(m),to_wire(c))==before
    for obj,name in ((a,'action'),(a.leg,'quantity'),(m,'is_valid'),(c,'max_market_age'),(a.leg.pricing,'fill_price')):
        with pytest.raises(FrozenInstanceError): setattr(obj,name,None)
    with pytest.raises(TypeError): PaperFillCalculation(r,m,c,A.ENTER_LONG,AT,AT,leg=a.leg)


@pytest.mark.parametrize('change',['price','source','validity','priced_at','observed_at','fee','spread','slippage','delay','age','execution_time','evaluation_time','request_quantity'])
def test_material_changes_alter_result_identity(change):
    r=request();m=market(r);c=costs();execute=evaluate=AT
    a=calculate(r,m,c)
    if change=='price':m=replace(m,reference_price=D('101'))
    elif change=='source':m=replace(m,source_identity='sha256:'+'e'*64)
    elif change=='validity':m=replace(m,valid_until=m.valid_until+timedelta(seconds=1))
    elif change=='priced_at':m=replace(m,priced_at=AT-timedelta(seconds=1))
    elif change=='observed_at':m=replace(m,priced_at=AT-timedelta(seconds=2),observed_at=AT-timedelta(seconds=1))
    elif change in ('fee','spread','slippage'):
        key={'fee':'trading_fee_rate','spread':'spread_rate','slippage':'slippage_rate'}[change]
        c=costs(**{key:D('.03')})
    elif change=='delay':c=costs(execution_delay=timedelta(seconds=1));execute=evaluate=AT+timedelta(seconds=1)
    elif change=='age':c=replace(c,max_market_age=timedelta(seconds=4))
    elif change=='execution_time':execute=evaluate=AT+timedelta(seconds=1)
    elif change=='evaluation_time':evaluate=AT+timedelta(seconds=1)
    else:r=request(i=inputs(sizing=assumptions(proposed_quantity=D('2'))));m=market(r)
    assert calculate(r,m,c,executed_at=execute,evaluated_at=evaluate).calculation_identity!=a.calculation_identity


def test_equivalent_decimal_and_utc_instants_preserve_identity():
    r=request();a=calculate(r)
    m=market(r,reference_price=D('100.000000'))
    c=costs(trading_fee_rate=D('.0100'),spread_rate=D('.02000'),slippage_rate=D('.010'))
    zone=timezone(timedelta(hours=5,minutes=30))
    m=replace(m,priced_at=AT.astimezone(zone),observed_at=AT.astimezone(zone))
    b=calculate(r,m,c,executed_at=AT.astimezone(zone),evaluated_at=AT.astimezone(zone))
    assert a.calculation_identity==b.calculation_identity


def test_four_fresh_hash_seeds_preserve_bound_and_four_leg_identities():
    script="""import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_paper_fill import *
from app.execution import ExecutionAuthorization,ExecutionRequest
from app.risk.integrated.inspection import inspect_risk
from dataclasses import replace
i=inputs();report=inspect_risk(i);rows={g.gate.value:g for g in report.gates}
auth=ExecutionAuthorization(i.intent,replace(report,gates=tuple(rows[k] for k in set(rows))))
r=ExecutionRequest(auth,'PAPER_TEST',AT)
x=calculate(r)
pairs={(Direction.LONG,A.ENTER_LONG),(Direction.LONG,A.EXIT_LONG),(Direction.SHORT,A.ENTER_SHORT),(Direction.SHORT,A.EXIT_SHORT)}
legs={a.value:PaperLegCalculation(d,a,D('100'),D('2'),costs()).calculation_identity for d,a in pairs}
print(json.dumps({'fill':x.calculation_identity,'leg':x.leg.calculation_identity,'legs':legs},sort_keys=True))
"""
    results=[]
    for seed in ('1','2','3','4'):
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).parents[1],
            env=dict(os.environ,PYTHONHASHSEED=seed),capture_output=True,text=True,check=True)
        results.append(json.loads(result.stdout))
    assert all(x==results[0] for x in results)


def test_no_provider_io_ledger_consumption_order_or_api_capability():
    folder=Path(__file__).parents[1]/'app/execution/paper_fill'
    for p in folder.glob('*.py'):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node,ast.Constant): assert not isinstance(node.value,float)
            if isinstance(node,ast.Call):
                name=ast.unparse(node.func)
                assert name not in ('float','hash','Fill','Order')
                assert not name.endswith(('.now','.utcnow','.commit','.execute','.connect','.submit','.lookup','simulate_execution'))
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[node.module or ''] if isinstance(node,ast.ImportFrom) else [a.name for a in node.names]
                assert not any(any(x in n for x in ('httpx','requests','sqlalchemy','psycopg','fastapi','app.db','app.market_data')) for n in names)


def test_zero_cost_model_does_not_fabricate_costs():
    c=costs(trading_fee_rate=D('0'),spread_rate=D('0'),slippage_rate=D('0'))
    result=calculate(c=c)
    assert result.leg.reference_notional==result.leg.gross_notional==D('100')
    assert result.leg.fee==result.leg.spread_cost==result.leg.slippage_cost==0
    assert result.leg.net_cash_effect==D('-100')


def test_real_decimal_overflow_fails_closed():
    with pytest.raises(ValueError,match='arithmetic'):
        PaperLegCalculation(Direction.LONG,A.ENTER_LONG,D('1e999999'),D('10'),costs())


def test_delay_overflow_is_controlled():
    r=request()
    with pytest.raises(ValueError,match='delay'):
        calculate(r,c=costs(execution_delay=timedelta.max))
