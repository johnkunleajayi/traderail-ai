"""Map existing validation evidence to API contracts without reinterpretation."""
from dataclasses import asdict
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration
from .schemas import ExecutionResponse
from .validation_config import VALIDATION_API_VERSION
from .validation_response import (wire,MetricsResponse,ValidationMetadata,PrimaryResponse,FoldResponse,
    WalkForwardResponse,SensitivityResponse,BenchmarkResponse,StressResponse,ExplanationResponse,ValidationResponse)


def execution_response(config):
    model=config.cost_model
    return ExecutionResponse(fee_rate=model.trading_fee_rate,full_spread_rate=model.spread_rate,
        slippage_rate=model.slippage_rate,delay_candles=config.delay_candles,
        timing=config.configuration()['timing'],cost_model_identity=config.cost_model_identity,
        execution_configuration_identity=config.configuration_identity)


def benchmark_response(comparison):
    row=comparison.benchmark
    return BenchmarkResponse(kind=row.kind.value,dataset_identity=row.dataset_identity,window_identity=row.window_identity,
        canonical_symbol=row.canonical_symbol,quote_currency=row.quote_currency,initial_capital=row.initial_capital,
        ending_equity=row.ending_equity,total_return=row.total_return,maximum_drawdown=row.maximum_drawdown,
        exposure_percentage=row.exposure_percentage,turnover=row.turnover,fees=row.costs.fees,
        spread_cost=row.costs.spread_cost,slippage_cost=row.costs.slippage_cost,
        comparison_identity=comparison.comparison_identity,result_identity=row.result_identity,
        strategy_minus_benchmark=dict(comparison.differences))


def validation_response(completed):
    request,evidence,report=completed.request,completed.evidence,completed.report
    primary,walk,sensitivity,stress=evidence.oos,evidence.walk_forward,evidence.sensitivity,evidence.stress
    metadata=ValidationMetadata(api_version=VALIDATION_API_VERSION,validation_identity=completed.validation_identity,
        report_identity=report.report_identity,evidence_identity=report.evidence_identity,
        strategy_family=report.strategy_family,strategy_version=report.strategy_version,
        strategy_configuration_identity=report.strategy_configuration_identity,dataset_identity=report.dataset_identity,
        canonical_symbol=request.canonical_symbol,provider=request.provider,timeframe=request.timeframe,
        candle_count=primary.plan.dataset.candle_count,primary_boundaries=request.primary,
        initial_capital=primary.plan.initial_capital,quantity=primary.plan.quantity,
        baseline_execution=execution_response(primary.plan.execution_configuration),policy_identity=report.policy.policy_identity,
        policy_version=report.policy.version,policy_configuration=wire(report.policy.configuration()),verdict=report.verdict,
        source_evidence_identities=dict(report.source_identities))
    folds=[FoldResponse(index=row.index,boundaries=request.walk_forward_folds[row.index],fold_identity=row.fold_identity,
        validation_identity=row.validation.validation_identity,backtest_identity=row.validation.out_of_sample.result_identity,
        metrics=MetricsResponse(**asdict(row.validation.out_of_sample.performance))) for row in walk.folds]
    probes=[SensitivityResponse(configuration_identity=row.configuration_identity,validation_identity=row.validation.validation_identity,
        changed_fields=row.changed_fields,changed_parameters={field:getattr(row.configuration,field) for field in row.changed_fields},
        metrics=MetricsResponse(**asdict(row.oos_metrics[0])),differences=row.differences) for row in sensitivity.variants]
    benchmarks=[benchmark_response(row) for row in report.benchmark_context]
    successful={row.scenario.code:row for row in stress.completed}
    failures={row.scenario_code:row for row in stress.failures}
    scenarios=[]
    for scenario in stress.plan.scenarios:
        result=successful.get(scenario.code); failure=failures.get(scenario.code)
        scenarios.append(StressResponse(code=scenario.code,scenario_identity=scenario.scenario_identity,
            assumptions=execution_response(HistoricalExecutionConfiguration(scenario.costs,request.timeframe)),
            validation_identity=None if result is None else result.validation.validation_identity,
            result_identity=None if result is None else result.result_identity,
            metrics=None if result is None else MetricsResponse(**asdict(result.oos_metrics[0])),
            differences=None if result is None else result.differences,
            execution_failure=None if failure is None else failure.reason.value,
            failure_source_identity=None if failure is None else failure.source_identity))
    return ValidationResponse(validation=metadata,
        primary_oos=PrimaryResponse(validation_identity=primary.validation_identity,backtest_identity=primary.out_of_sample.result_identity,
            metrics=MetricsResponse(**asdict(primary.out_of_sample.performance))),
        walk_forward=WalkForwardResponse(validation_identity=walk.validation_identity,
            active_folds=walk.summary.fold_count-walk.summary.zero_trade_folds,summary=walk.summary,folds=folds),
        sensitivity=probes,sensitivity_baseline_configuration_identity=sensitivity.baseline.configuration_identity,
        sensitivity_identity=sensitivity.sensitivity_identity,benchmarks=benchmarks,stress=scenarios,stress_evidence_identity=stress.source_identity,
        criteria=report.criteria,verdict_explanation=ExplanationResponse(insufficiency_reasons=report.insufficiency_reasons,
            failure_reasons=report.failure_reasons,passed_criteria=report.passed_criteria,benchmark_context=benchmarks))
