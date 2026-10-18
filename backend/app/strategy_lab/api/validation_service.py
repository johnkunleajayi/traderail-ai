"""Stored-only composition of existing 9D engines; no alternate research formulas."""
from dataclasses import dataclass
from datetime import timedelta
from sqlalchemy import select
from app.instruments.models import Instrument
from app.market_data.models import MarketCandle
from app.market_intelligence.types import Candle,DatasetContext,TIMEFRAME_SECONDS,InvalidCandleData
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.validation import TimeWindow,ValidationSplit
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration,identity
from app.strategy_lab.out_of_sample import OutOfSamplePlan,validate_out_of_sample
from app.strategy_lab.walk_forward import WalkForwardPlan,validate_walk_forward
from app.strategy_lab.sensitivity import SensitivityPlan,evaluate_sensitivity
from app.strategy_lab.benchmarks import BenchmarkConfiguration,BenchmarkDefinition,BenchmarkKind
from app.strategy_lab.benchmark_evaluation import evaluate_benchmarks
from app.strategy_lab.stress_testing import StressPlan
from app.strategy_lab.stress_testing import engine as stress_engine
from app.strategy_lab.validation_evidence import (ValidationEvidence,StressEvidence,StressFailure,
    StressFailureCode,DEFAULT_RESEARCH_POLICY,evaluate_validation_evidence)
from .validation_config import SENSITIVITY_CONFIGURATIONS,stress_scenarios,CAPITAL_FAILURE_MESSAGES,VALIDATION_API_VERSION
from .schemas import MIN_WARMUP_CANDLES
from .service import ExperimentError


@dataclass(frozen=True)
class CompletedValidation:
    request: object
    evidence: ValidationEvidence
    report: object
    validation_identity: str


def build_validation_plans(db,request,*,as_of):
    duration=timedelta(seconds=TIMEFRAME_SECONDS[request.timeframe])
    root=request.primary
    if root.end > as_of or (root.end-root.warmup_start)!=duration*request.requested_candle_count:
        raise ExperimentError(422,"invalid_validation_boundaries","History count and complete interval boundaries must agree")
    # Validate all declared windows before any research runs or data queries.
    for window in (root,*request.walk_forward_folds):
        if ((window.in_sample_start-window.warmup_start)<duration*MIN_WARMUP_CANDLES
            or any((value-root.warmup_start)%duration for value in (window.warmup_start,window.in_sample_start,window.out_of_sample_start,window.end))):
            raise ExperimentError(422,"invalid_validation_boundaries","Each window requires aligned boundaries and at least 52 warm-up candles")
    instrument=db.scalar(select(Instrument).where(Instrument.canonical_symbol==request.canonical_symbol).execution_options(autoflush=False))
    if instrument is None:
        raise ExperimentError(404,"instrument_not_found","Canonical instrument not found")
    query=select(MarketCandle).where(MarketCandle.instrument_id==instrument.id,MarketCandle.provider==request.provider,
        MarketCandle.timeframe==request.timeframe,MarketCandle.open_time>=root.warmup_start,MarketCandle.open_time<root.end
        ).order_by(MarketCandle.open_time).limit(request.requested_candle_count)
    stored=db.scalars(query.execution_options(autoflush=False)).all()
    if len(stored)!=request.requested_candle_count:
        raise ExperimentError(409,"insufficient_stored_history","Exact instrument/provider/timeframe history is unavailable; no history is ingested")
    candles=tuple(Candle(**{field:getattr(row,field) for field in Candle.__dataclass_fields__}) for row in stored)
    context=DatasetContext(instrument.id,instrument.canonical_symbol,request.provider,request.timeframe)
    model=ResearchCostModel(request.execution.fee_rate,request.execution.full_spread_rate,request.execution.slippage_rate,
        duration*request.execution.delay_candles)
    execution=HistoricalExecutionConfiguration(model,request.timeframe)
    def plan(window):
        selected=tuple(row for row in candles if window.warmup_start<=row.open_time<window.end)
        data=create_dataset(context,selected,warmup_start=window.warmup_start,evaluation_start=window.in_sample_start,
            evaluation_end=window.end,as_of=as_of)
        return OutOfSamplePlan(data,TimeWindow(window.warmup_start,window.in_sample_start),
            ValidationSplit(TimeWindow(window.in_sample_start,window.out_of_sample_start),TimeWindow(window.out_of_sample_start,window.end)),
            AdaptiveTrendStrategy().definition,execution,request.quantity,request.initial_capital)
    try:
        primary=plan(root)
        if any(row.retrieved_at>as_of for row in primary.dataset.candles):
            raise ValueError("Retrieval cutoff")
        folds=WalkForwardPlan(primary.dataset,tuple(plan(window) for window in request.walk_forward_folds))
        sensitivity=SensitivityPlan(primary,SENSITIVITY_CONFIGURATIONS[0],SENSITIVITY_CONFIGURATIONS)
        stress=StressPlan(primary,stress_scenarios(model,duration))
    except InvalidCandleData:
        raise ExperimentError(409,"invalid_candle_dataset","Stored candles failed canonical research validation") from None
    except (ValueError,ArithmeticError):
        raise ExperimentError(422,"validation_construction_failed","Declared windows or execution assumptions cannot construct the fixed validation plan") from None
    return primary,folds,sensitivity,stress


def collect_stress(stress_plan,primary,*,as_of):
    # Existing stress engine is fail-fast. Reuse its per-scenario runner/projection to retain
    # explicit capital failures and reuse the already-computed BASELINE, without new formulas.
    metrics=(primary.out_of_sample.performance,)
    completed=[];failures=[]
    for scenario in stress_plan.scenarios:
        try:
            result=primary if scenario.code=="BASELINE" else stress_engine._run(stress_plan,scenario,as_of)
            completed.append(stress_engine._scenario_result(scenario,result,metrics))
        except ValueError as error:
            if str(error) not in CAPITAL_FAILURE_MESSAGES:
                raise
            failure_id=identity("strategy-lab.api.stress-failure/v1",{"plan":stress_plan.plan_identity,
                "scenario":scenario.scenario_identity,"reason":StressFailureCode.UNAFFORDABLE_EXECUTION})
            failures.append(StressFailure(scenario.code,StressFailureCode.UNAFFORDABLE_EXECUTION,failure_id))
    source_id=identity("strategy-lab.api.stress-evidence/v1",{"plan":stress_plan.plan_identity,
        "completed":tuple(row.result_identity for row in completed),"failures":tuple(row.source_identity for row in failures)})
    return StressEvidence(stress_plan,tuple(completed),tuple(failures),source_id)


def run_stored_validation(db,request,*,as_of):
    primary_plan,fold_plan,sensitivity_plan,stress_plan=build_validation_plans(db,request,as_of=as_of)
    try:
        primary=validate_out_of_sample(primary_plan,AdaptiveTrendStrategy,as_of=as_of)
        walk=validate_walk_forward(fold_plan,AdaptiveTrendStrategy,as_of=as_of)
        sensitivity=evaluate_sensitivity(sensitivity_plan,as_of=as_of)
        benchmarks=evaluate_benchmarks(primary,BenchmarkConfiguration(tuple(BenchmarkDefinition(kind) for kind in BenchmarkKind)),as_of=as_of)
        stress=collect_stress(stress_plan,primary,as_of=as_of)
        evidence=ValidationEvidence(primary,walk,sensitivity,stress,benchmarks)
        report=evaluate_validation_evidence(evidence,DEFAULT_RESEARCH_POLICY)
    except (ValueError,ArithmeticError):
        raise ExperimentError(409,"validation_execution_failed","Historical validation could not complete with the declared research assumptions") from None
    result_id=identity("strategy-lab.api.validation/v1",{"api_version":VALIDATION_API_VERSION,"report":report.report_identity,
        "primary":primary.validation_identity,"walk_forward":walk.validation_identity,
        "sensitivity":sensitivity.sensitivity_identity,"stress":stress.source_identity})
    return CompletedValidation(request,evidence,report,result_id)
