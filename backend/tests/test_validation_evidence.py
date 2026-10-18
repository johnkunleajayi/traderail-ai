"""Independent policy oracles over synthetic immutable research result projections.

These unit fixtures intentionally declare metric observations; they do not claim new
strategy backtests. A separate integration test consumes real engine-produced artifacts.
"""
from dataclasses import replace, asdict, FrozenInstanceError, fields
from datetime import timedelta
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path
import ast
import pytest
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.adaptive_trend.config import DEFAULT_CONFIGURATION
from app.strategy_lab.out_of_sample import OutOfSampleResult
from app.strategy_lab.out_of_sample.comparison import compare_performance
from app.strategy_lab.walk_forward import WalkForwardPlan, WalkForwardResult
from app.strategy_lab.walk_forward.engine import FoldResult
from app.strategy_lab.walk_forward.summary import summarize_folds
from app.strategy_lab.sensitivity.plan import SensitivityPlan, changed_fields
from app.strategy_lab.sensitivity.engine import SensitivityResult, VariantResult
from app.strategy_lab.stress_testing import StressPlan, StressScenario
from app.strategy_lab.stress_testing.engine import ScenarioResult
from app.strategy_lab.benchmark_evaluation import evaluate_benchmarks
from app.strategy_lab.benchmarks import BenchmarkConfiguration, BenchmarkDefinition, BenchmarkKind
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration, identity
from app.strategy_lab.validation_evidence import *
from app.strategy_lab.validation_evidence.engine import verdict_from_criteria
from test_out_of_sample_validation import flat_result, AS_OF, D

TEST_POLICY = replace(DEFAULT_RESEARCH_POLICY, minimum_oos_completed_trades=20,
    minimum_walk_forward_folds=1, minimum_active_folds=1, minimum_stress_scenarios=2,
    minimum_sensitivity_variants=1)


def metric():
    return replace(flat_result().out_of_sample.performance,total_return=D('.10'),gross_return=D('.12'),
        net_return=D('.08'),maximum_drawdown=D('.10'),trade_count=40,win_rate=D('.6'),loss_rate=D('.3'),
        profit_factor=D('1.5'),average_win=D(2),average_loss=D(1),expectancy=D('.5'),
        exposure_percentage=D(50),turnover=D(2),total_fees=D(1),total_spread_cost=D(1),total_slippage_cost=D(1))


def research_projection(template, plan, metrics):
    replay=replace(template.replay,strategy_definition=plan.strategy_definition)
    execution=replace(template.execution,configuration=plan.execution_configuration,quantity=plan.quantity)
    result_id=identity('test.synthetic.research/v1',{'source':template.result_identity,
        'plan':plan.plan_identity,'metrics':asdict(metrics)})
    return replace(template,replay=replay,execution=execution,performance=metrics,result_identity=result_id)


def paired_projection(source,metrics,plan=None):
    plan=plan or source.plan
    ins=research_projection(source.in_sample,plan,metrics)
    oos=research_projection(source.out_of_sample,plan,metrics)
    return OutOfSampleResult(plan,ins,oos,compare_performance(ins.performance,oos.performance),
        identity('test.synthetic.paired/v1',{'plan':plan.plan_identity,'is':ins.result_identity,'oos':oos.result_identity}))


@lru_cache(None)
def passive_rows():
    return evaluate_benchmarks(flat_result(),BenchmarkConfiguration(tuple(BenchmarkDefinition(kind) for kind in BenchmarkKind)),as_of=AS_OF)


def benchmarks_for(primary):
    if primary.plan.dataset_identity != flat_result().plan.dataset_identity:
        return evaluate_benchmarks(primary,BenchmarkConfiguration(tuple(BenchmarkDefinition(kind) for kind in BenchmarkKind)),as_of=AS_OF+timedelta(days=200))
    output=[]
    for row in passive_rows():
        b=row.benchmark; m=primary.out_of_sample.performance
        pairs=(('total_return',m.total_return,b.total_return),('maximum_drawdown',m.maximum_drawdown,b.maximum_drawdown),
            ('exposure_percentage',m.exposure_percentage,b.exposure_percentage),('turnover',m.turnover,b.turnover),
            ('total_cost',primary.out_of_sample.incurred_costs.total,b.costs.total))
        from app.strategy_lab.numeric import difference
        differences=tuple((key,None if left is None or right is None else difference(left,right)) for key,left,right in pairs)
        output.append(replace(row,strategy_result_identity=primary.out_of_sample.result_identity,differences=differences,
            comparison_identity=identity('test.synthetic.comparison/v1',{'source':row.comparison_identity,'strategy':primary.out_of_sample.result_identity,'differences':differences})))
    return tuple(output)


def fixture(*,oos=None,fold=None,variant=None,stress=None,source=None,walk_source=None):
    # All use the same explicit dataset and assumptions. Only declared test metric observations differ.
    base=paired_projection(source or flat_result(),oos or metric())
    wf_plan=walk_source.plan if walk_source is not None else WalkForwardPlan(base.plan.dataset,(base.plan,))
    templates=walk_source.folds if walk_source is not None else (FoldResult(0,base,'fixture'),)
    folded=tuple(FoldResult(row.index,paired_projection(row.validation,fold or base.out_of_sample.performance),
        identity('test.fold/v1',{'index':row.index,'metrics':asdict(fold or base.out_of_sample.performance)})) for row in templates)
    wf=WalkForwardResult(wf_plan,folded,summarize_folds(tuple(row.validation.out_of_sample.performance for row in folded)),
        identity('test.wf/v1',tuple(row.validation.validation_identity for row in folded)))
    alt=replace(DEFAULT_CONFIGURATION,entry_min_ema_spread=D('.0016'))
    definition=AdaptiveTrendStrategy(alt).definition
    variant_result=paired_projection(base,variant or base.out_of_sample.performance,replace(base.plan,strategy_definition=definition))
    sensitivity_plan=SensitivityPlan(base.plan,DEFAULT_CONFIGURATION,(DEFAULT_CONFIGURATION,alt))
    baseline=VariantResult(DEFAULT_CONFIGURATION,base.plan.strategy_definition.configuration_identity,(),base,(base.out_of_sample.performance,),())
    alternate=VariantResult(alt,definition.configuration_identity,changed_fields(DEFAULT_CONFIGURATION,alt),variant_result,
        (variant_result.out_of_sample.performance,),())
    sensitivity=SensitivityResult(sensitivity_plan,baseline,(alternate,),identity('test.sensitivity/v1',{
        'base':base.validation_identity,'variant':variant_result.validation_identity}))
    base_scenario=StressScenario('BASELINE',base.plan.execution_configuration.cost_model)
    worse=StressScenario('ADVERSE',ResearchCostModel(D('.001'),D('.002'),D('.001')))
    stress_plan=StressPlan(base.plan,(base_scenario,worse))
    stressed=paired_projection(base,stress or base.out_of_sample.performance,replace(base.plan,
        execution_configuration=HistoricalExecutionConfiguration(worse.costs,'1h')))
    def scenario_row(scenario,result):
        return ScenarioResult(scenario,result,(result.out_of_sample.performance,),(),
            identity('test.scenario/v1',{'scenario':scenario.scenario_identity,'validation':result.validation_identity}))
    stress_evidence=StressEvidence(stress_plan,(scenario_row(base_scenario,base),scenario_row(worse,stressed)),(),
        identity('test.stress/v1',{'base':base.validation_identity,'stress':stressed.validation_identity}))
    return ValidationEvidence(base,wf,sensitivity,stress_evidence,benchmarks_for(base))


def evaluate(evidence=None,policy=TEST_POLICY):
    return evaluate_validation_evidence(evidence or fixture(),policy)


def test_candidate_requires_every_mandatory_criterion():
    result=evaluate()
    assert result.verdict is Verdict.VALIDATION_CANDIDATE
    assert not result.insufficiency_reasons and not result.failure_reasons
    assert set(row.category for row in result.criteria)==set(Category)
    assert all(row.status in (Status.PASS,Status.SUFFICIENT,Status.NOT_APPLICABLE) for row in result.criteria)
    assert all(row.reason_code and row.explanation and row.code for row in result.criteria)
    assert result.strategy_family=='ADAPTIVE_TREND' and result.strategy_version=='1.0.0'


@pytest.mark.parametrize('change',[
    {'minimum_oos_completed_trades':41}, {'minimum_walk_forward_folds':2}, {'minimum_active_folds':2},
    {'minimum_stress_scenarios':3}, {'minimum_sensitivity_variants':2}])
def test_insufficient_quantities_do_not_become_failure(change):
    result=evaluate(policy=replace(TEST_POLICY,**change))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE and result.insufficiency_reasons


@pytest.mark.parametrize('field',['expectancy','profit_factor','maximum_drawdown'])
def test_undefined_required_metric_is_insufficient(field):
    baseline=metric(); baseline=replace(baseline,**{field:None})
    result=evaluate(fixture(oos=baseline))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize('name,value,code',[
    ('maximum_drawdown',D('.21'),'OOS_DRAWDOWN'),('expectancy',D('-.01'),'OOS_EXPECTANCY'),
    ('profit_factor',D('1.09'),'OOS_PROFIT_FACTOR')])
def test_quality_failure_only_after_sufficiency(name,value,code):
    result=evaluate(fixture(oos=replace(metric(),**{name:value})))
    assert result.verdict is Verdict.FAILED_VALIDATION
    assert next(row for row in result.criteria if row.code==code).status is Status.FAIL


def test_bad_fold_consistency_and_worst_drawdown_are_not_averaged_away():
    result=evaluate(fixture(fold=replace(metric(),net_return=D('-.01'),maximum_drawdown=D('.26'))))
    assert result.verdict is Verdict.FAILED_VALIDATION
    codes={row.code for row in result.criteria if row.status is Status.FAIL}
    assert {'NON_LOSING_ACTIVE_FOLDS','WORST_FOLD_DRAWDOWN'} <= codes


@pytest.mark.parametrize('change',[{'net_return':D('-.01')},{'net_return':D('.05')},{'trade_count':15}])
def test_any_adverse_variant_fails_stability_without_selection(change):
    result=evaluate(fixture(variant=replace(metric(),**change)))
    assert result.verdict is Verdict.FAILED_VALIDATION
    assert any(row.category is Category.PARAMETER_STABILITY and row.status is Status.FAIL for row in result.criteria)


@pytest.mark.parametrize('change',[{'net_return':D('-.01')},{'net_return':D('.04')},{'trade_count':15}])
def test_adverse_cost_evidence_is_not_discarded(change):
    result=evaluate(fixture(stress=replace(metric(),**change)))
    assert result.verdict is Verdict.FAILED_VALIDATION
    assert any(row.category is Category.COST_RESILIENCE and row.status is Status.FAIL for row in result.criteria)


@pytest.mark.parametrize('name',['walk_forward','sensitivity','stress','benchmarks'])
def test_missing_required_source_is_insufficient(name):
    result=evaluate(replace(fixture(),**{name:() if name=='benchmarks' else None}))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE


def test_strong_return_cannot_override_failed_risk_or_missing_evidence():
    result=evaluate(fixture(oos=replace(metric(),total_return=D(100),maximum_drawdown=D('.9'))))
    assert result.verdict is Verdict.FAILED_VALIDATION
    result=evaluate(fixture(oos=replace(metric(),total_return=D(100),maximum_drawdown=D('.9'),trade_count=1)))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE and result.failure_reasons


def test_trade_evidence_disappearance_is_insufficient_not_invented_zero_metrics():
    result=evaluate(fixture(variant=replace(metric(),trade_count=0,expectancy=None,profit_factor=None)))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE
    result=evaluate(fixture(stress=replace(metric(),trade_count=0,expectancy=None,profit_factor=None)))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE


def test_benchmark_underperformance_is_informational_unless_declared_mandatory():
    evidence=fixture(oos=replace(metric(),total_return=D('-.01')))
    assert evaluate(evidence).verdict is Verdict.VALIDATION_CANDIDATE
    for field in ('cash_treatment','buy_and_hold_treatment'):
        result=evaluate(evidence,replace(TEST_POLICY,**{field:BenchmarkTreatment.MANDATORY}))
        assert result.verdict is Verdict.FAILED_VALIDATION
    optional=replace(TEST_POLICY,require_cash_comparison=False,require_buy_and_hold_comparison=False)
    assert evaluate(replace(evidence,benchmarks=()),optional).verdict is Verdict.VALIDATION_CANDIDATE


def test_unaffordable_attempt_is_explicit_failure_with_no_fabricated_metrics():
    evidence=fixture(); stress=evidence.stress
    base=next(row for row in stress.completed if row.scenario.code=='BASELINE')
    failed=StressFailure('ADVERSE',StressFailureCode.UNAFFORDABLE_EXECUTION,'captured-attempt-sha256')
    result=evaluate(replace(evidence,stress=replace(stress,completed=(base,),failures=(failed,))))
    assert result.verdict is Verdict.FAILED_VALIDATION
    assert 'STRESSED_EXECUTION_UNAFFORDABLE' in result.failure_reasons
    assert any(key=='STRESS_FAILURE:ADVERSE' for key,value in result.source_identities)


def test_reproducibility_policy_and_meaningful_evidence_changes():
    evidence=fixture(); first=evaluate(evidence)
    assert evaluate(evidence)==first
    other=evaluate(evidence,replace(TEST_POLICY,maximum_oos_drawdown=D('.19')))
    assert first.report_identity!=other.report_identity and first.policy.policy_identity!=other.policy.policy_identity
    changed=evaluate(fixture(oos=replace(metric(),net_return=D('.081'))))
    assert first.report_identity!=changed.report_identity and first.evidence_identity!=changed.evidence_identity
    with localcontext() as context:
        context.prec=6
        assert evaluate(evidence)==first
    assert evaluate(evidence,replace(TEST_POLICY,maximum_oos_drawdown=D('.20000')))==first


def test_ordering_of_criteria_sources_variants_scenarios_cannot_change_verdict_or_identity():
    evidence=fixture(); result=evaluate(evidence)
    reordered=replace(evidence,benchmarks=tuple(reversed(evidence.benchmarks)),
        stress=replace(evidence.stress,completed=tuple(reversed(evidence.stress.completed))))
    assert evaluate(reordered)==result
    assert verdict_from_criteria(tuple(reversed(result.criteria)))==result.verdict
    assert len(result.criteria)==len({row.code for row in result.criteria})


def test_immutability_and_governance_boundary():
    evidence=fixture(); report=evaluate(evidence)
    with pytest.raises(FrozenInstanceError): report.verdict=Verdict.FAILED_VALIDATION
    with pytest.raises(FrozenInstanceError): evidence.oos.out_of_sample.performance.trade_count=999
    with pytest.raises(FrozenInstanceError): report.policy.maximum_oos_drawdown=D(1)
    with pytest.raises(TypeError): evidence.oos.plan.strategy_definition.parameters['entry_fast_slope']=D(0)
    assert not {'selected','best','rank','lifecycle','promotion','authorization','execution_authority'} & {field.name for field in fields(report)}
    assert set(item.value for item in Verdict)=={'INSUFFICIENT_EVIDENCE','FAILED_VALIDATION','VALIDATION_CANDIDATE'}


@pytest.mark.parametrize('change',[{'maximum_oos_drawdown':.2},{'minimum_oos_completed_trades':True},
    {'minimum_stress_scenarios':1},{'maximum_stress_return_degradation':D('-1')},
    {'minimum_variant_trade_retention':D('1.1')},{'cash_treatment':'MANDATORY'},{'minimum_oos_profit_factor':D('NaN')}])
def test_malformed_policy_rejected(change):
    with pytest.raises(ValueError): replace(TEST_POLICY,**change)


def test_source_binding_rejects_foreign_benchmark_and_duplicate_stress():
    evidence=fixture()
    row=evidence.benchmarks[0]
    foreign=replace(row,benchmark=replace(row.benchmark,initial_capital=D(1)))
    with pytest.raises(ValueError,match='Benchmark'):
        evaluate(replace(evidence,benchmarks=(foreign,)))
    duplicate=replace(evidence.stress,completed=evidence.stress.completed+(evidence.stress.completed[0],))
    with pytest.raises(ValueError,match='[Dd]uplicate'):
        evaluate(replace(evidence,stress=duplicate))


def test_missing_declared_stress_or_variant_remains_visible_as_insufficient():
    evidence=fixture()
    baseline=tuple(row for row in evidence.stress.completed if row.scenario.code=='BASELINE')
    assert evaluate(replace(evidence,stress=replace(evidence.stress,completed=baseline))).verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert evaluate(replace(evidence,sensitivity=replace(evidence.sensitivity,variants=()))).verdict is Verdict.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize('value,expected',[(D('.20'),Verdict.VALIDATION_CANDIDATE),
    (D('.20000000000000000001'),Verdict.FAILED_VALIDATION)])
def test_drawdown_boundary_exact_decimal(value,expected):
    assert evaluate(fixture(oos=replace(metric(),maximum_drawdown=value))).verdict is expected


@pytest.mark.parametrize('field,value,expected',[
    ('expectancy',D(0),Verdict.VALIDATION_CANDIDATE),('expectancy',D('-.000000000000000001'),Verdict.FAILED_VALIDATION),
    ('profit_factor',D('1.10'),Verdict.VALIDATION_CANDIDATE),('profit_factor',D('1.09999999999999999'),Verdict.FAILED_VALIDATION)])
def test_return_quality_boundaries(field,value,expected):
    assert evaluate(fixture(oos=replace(metric(),**{field:value}))).verdict is expected


@pytest.mark.parametrize('source,value,expected',[
    ('variant',D('.06'),Verdict.VALIDATION_CANDIDATE),('variant',D('.059999999999999999'),Verdict.FAILED_VALIDATION),
    ('stress',D('.05'),Verdict.VALIDATION_CANDIDATE),('stress',D('.049999999999999999'),Verdict.FAILED_VALIDATION)])
def test_absolute_return_degradation_boundaries(source,value,expected):
    assert evaluate(fixture(**{source:replace(metric(),net_return=value)})).verdict is expected


@pytest.mark.parametrize('count,expected',[(20,Verdict.VALIDATION_CANDIDATE),(19,Verdict.FAILED_VALIDATION)])
def test_trade_retention_boundary(count,expected):
    assert evaluate(fixture(variant=replace(metric(),trade_count=count))).verdict is expected


def test_absent_baseline_and_declared_benchmark_metric_are_insufficient():
    evidence=fixture()
    adverse=tuple(row for row in evidence.stress.completed if row.scenario.code!='BASELINE')
    result=evaluate(replace(evidence,stress=replace(evidence.stress,completed=adverse)))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert any(row.code=='STRESS_BASELINE_COVERAGE' and row.status is Status.INSUFFICIENT for row in result.criteria)
    result=evaluate(fixture(oos=replace(metric(),total_return=None)))
    assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE


def test_optional_sensitivity_is_explicitly_informational_and_all_variants_retained():
    evidence=fixture(variant=replace(metric(),net_return=D('-.5')))
    report=evaluate(evidence,replace(TEST_POLICY,require_sensitivity_stability=False))
    assert report.verdict is Verdict.VALIDATION_CANDIDATE
    rows=tuple(row for row in report.criteria if row.category is Category.PARAMETER_STABILITY)
    assert rows and all(row.status is Status.NOT_APPLICABLE for row in rows)
    assert any(key.startswith('SENSITIVITY:') for key,value in report.source_identities)


def test_second_better_variant_cannot_mask_first_adverse_variant_or_change_order_identity():
    evidence=fixture(variant=replace(metric(),net_return=D('-.01')))
    sensitivity=evidence.sensitivity
    config=replace(DEFAULT_CONFIGURATION,entry_min_ema_spread=D('.0017'))
    definition=AdaptiveTrendStrategy(config).definition
    result=paired_projection(evidence.oos,metric(),replace(evidence.oos.plan,strategy_definition=definition))
    variant=VariantResult(config,definition.configuration_identity,changed_fields(DEFAULT_CONFIGURATION,config),
        result,(result.out_of_sample.performance,),())
    updated=replace(sensitivity,plan=SensitivityPlan(evidence.oos.plan,DEFAULT_CONFIGURATION,
        (DEFAULT_CONFIGURATION,sensitivity.variants[0].configuration,config)),variants=sensitivity.variants+(variant,))
    report=evaluate(replace(evidence,sensitivity=updated))
    assert report.verdict is Verdict.FAILED_VALIDATION
    assert evaluate(replace(evidence,sensitivity=replace(updated,variants=tuple(reversed(updated.variants)))))==report
    assert sum(key.startswith('SENSITIVITY:') for key,value in report.source_identities)==2


def test_walk_forward_fold_order_and_baseline_reference_do_not_change_report():
    from test_walk_forward_validation import flat_result as walk_result
    from test_out_of_sample_validation import plan as paired_plan
    from app.strategy_lab.out_of_sample import validate_out_of_sample
    wf=walk_result()
    source=validate_out_of_sample(paired_plan(wf.plan.dataset),AdaptiveTrendStrategy,as_of=AS_OF+timedelta(days=200))
    evidence=fixture(source=source,walk_source=wf)
    report=evaluate(evidence)
    reordered=replace(evidence,walk_forward=replace(evidence.walk_forward,folds=tuple(reversed(evidence.walk_forward.folds))))
    assert evaluate(reordered)==report
    assert next(row.observed for row in report.criteria if row.code=='WALK_FORWARD_FOLDS')==2


def test_malformed_financial_evidence_and_wrong_metric_projection_rejected():
    with pytest.raises(ValueError,match='finite Decimal'):
        evaluate(fixture(oos=replace(metric(),expectancy=D('NaN'))))
    evidence=fixture()
    bad=replace(evidence.sensitivity.variants[0],oos_metrics=(replace(metric(),trade_count=999),))
    with pytest.raises(ValueError,match='projection'):
        evaluate(replace(evidence,sensitivity=replace(evidence.sensitivity,variants=(bad,))))
    foreign=replace(evidence.oos,out_of_sample=replace(evidence.oos.out_of_sample,dataset_fingerprint='foreign'))
    with pytest.raises(ValueError,match='frozen'):
        evaluate(replace(evidence,oos=foreign))


def test_real_source_artifacts_consumed_without_rerunning_any_engine(monkeypatch):
    from test_execution_stress import actual
    from app.strategy_lab.sensitivity import evaluate_sensitivity
    from app.strategy_lab.out_of_sample import engine as oos_engine
    from app.strategy_lab.walk_forward import engine as wf_engine
    from app.strategy_lab.execution import engine as execution_engine
    from app.strategy_lab.accounting import engine as accounting_engine
    from app.strategy_lab.sensitivity import engine as sensitivity_engine
    from app.strategy_lab.stress_testing import engine as stress_engine
    stress=actual()  # Existing cached deterministic research fixture; no providers or live database.
    primary=stress.baseline.validation
    alternate=replace(DEFAULT_CONFIGURATION,entry_min_ema_spread=D('.0016'))
    sensitivity=evaluate_sensitivity(SensitivityPlan(primary.plan,DEFAULT_CONFIGURATION,(DEFAULT_CONFIGURATION,alternate)),as_of=AS_OF)
    benchmarks=evaluate_benchmarks(primary,BenchmarkConfiguration(tuple(BenchmarkDefinition(kind) for kind in BenchmarkKind)),as_of=AS_OF)
    evidence=ValidationEvidence(primary,sensitivity=sensitivity,stress=StressEvidence.from_result(stress),benchmarks=benchmarks)
    def forbidden(*args,**kwargs): raise AssertionError('A research engine was rerun')
    for module,name in ((oos_engine,'validate_out_of_sample'),(oos_engine,'_evaluate_period'),
        (wf_engine,'validate_walk_forward'),(execution_engine,'simulate_execution'),
        (accounting_engine,'account_simulation'),(sensitivity_engine,'evaluate_sensitivity'),(stress_engine,'evaluate_stress')):
        monkeypatch.setattr(module,name,forbidden)
    monkeypatch.setattr(AdaptiveTrendStrategy,'decide',forbidden)
    report=evaluate_validation_evidence(evidence)
    assert report.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert report.strategy_configuration_identity==primary.plan.strategy_definition.configuration_identity
    assert evaluate_validation_evidence(evidence)==report


def test_module_has_no_database_provider_lifecycle_optimizer_or_execution_dependency():
    root=Path(__file__).parents[1]/'app'/'strategy_lab'/'validation_evidence'
    for source in root.glob('*.py'):
        tree=ast.parse(source.read_text())
        imports=[node.module or '' for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names]
        assert not any(name.startswith(('sqlalchemy','psycopg','httpx','requests','app.db','app.market_data','app.strategy_lab.lifecycle',
            'app.strategies.adaptive_trend')) for name in imports)
        forbidden={'replay_dataset','simulate_execution','account_simulation','evaluate_sensitivity','evaluate_stress',
            'validate_out_of_sample','validate_walk_forward'}
        calls={node.func.id for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)}
        assert not calls & forbidden


def test_inner_benchmark_presentation_order_is_canonical_without_source_mutation():
    evidence=fixture(); report=evaluate(evidence)
    reversed_rows=tuple(replace(row,differences=tuple(reversed(row.differences))) for row in reversed(evidence.benchmarks))
    assert evaluate(replace(evidence,benchmarks=reversed_rows))==report
    assert evidence.benchmarks==benchmarks_for(evidence.oos)


def test_changed_baseline_configuration_cannot_hide_behind_unchanged_identity():
    evidence=fixture()
    baseline=replace(evidence.sensitivity.baseline,configuration=replace(DEFAULT_CONFIGURATION,entry_min_ema_spread=D('.2')))
    with pytest.raises(ValueError,match='baseline/configuration'):
        evaluate(replace(evidence,sensitivity=replace(evidence.sensitivity,baseline=baseline)))


def test_default_policy_hypotheses_are_explicit_and_not_an_execution_permission():
    policy=DEFAULT_RESEARCH_POLICY
    assert policy.minimum_oos_completed_trades==30 and policy.minimum_walk_forward_folds==3
    assert policy.minimum_active_folds==3 and policy.minimum_non_losing_fold_proportion==D('.67')
    assert policy.maximum_oos_drawdown==D('.20') and policy.maximum_worst_fold_drawdown==D('.25')
    assert policy.minimum_oos_expectancy==0 and policy.minimum_oos_profit_factor==D('1.10')
    assert policy.minimum_stress_scenarios==4 and policy.minimum_sensitivity_variants==2
    assert policy.cash_treatment is policy.buy_and_hold_treatment is BenchmarkTreatment.INFORMATIONAL
    assert policy.maximum_stress_return_degradation==D('.03') and policy.maximum_sensitivity_return_degradation==D('.02')
    assert not any('permission' in field.name or 'promotion' in field.name for field in fields(policy))
