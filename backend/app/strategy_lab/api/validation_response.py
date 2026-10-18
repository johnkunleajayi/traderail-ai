"""Frontend inspection contracts; Decimal/UTC projection only, no research formulas."""
from dataclasses import asdict,is_dataclass
from datetime import datetime,timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from pydantic import BaseModel,field_serializer
from app.strategy_lab.walk_forward.summary import WalkForwardSummary
from app.strategy_lab.sensitivity.engine import SensitivityComparison
from app.strategy_lab.stress_testing.engine import StressComparison
from app.strategy_lab.validation_evidence import CriterionResult,Verdict
from .schemas import Financial,ExecutionResponse
from .validation_request import ValidationBoundaries


def wire(value):
    if isinstance(value,Decimal):return format(value,'f')
    if isinstance(value,datetime):return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    if isinstance(value,Enum):return value.value
    if is_dataclass(value):return wire(asdict(value))
    if isinstance(value,dict):return {key:wire(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [wire(item) for item in value]
    if isinstance(value,float):raise ValueError("Financial float is not part of this API")
    return value


class MetricsResponse(BaseModel):
    total_return: Financial|None
    gross_return: Financial|None
    net_return: Financial|None
    maximum_drawdown: Financial|None
    trade_count: int
    win_rate: Financial|None
    loss_rate: Financial|None
    profit_factor: Financial|None
    average_win: Financial|None
    average_loss: Financial|None
    expectancy: Financial|None
    exposure_percentage: Financial|None
    turnover: Financial|None
    total_fees: Financial
    total_spread_cost: Financial
    total_slippage_cost: Financial


class ValidationMetadata(BaseModel):
    api_version: str
    validation_identity: str
    report_identity: str
    evidence_identity: str
    strategy_family: str
    strategy_version: str
    strategy_configuration_identity: str
    dataset_identity: str
    canonical_symbol: str
    provider: str
    timeframe: str
    candle_count: int
    primary_boundaries: ValidationBoundaries
    initial_capital: Financial
    quantity: Financial
    baseline_execution: ExecutionResponse
    policy_identity: str
    policy_version: str
    policy_configuration: dict[str,Any]
    verdict: Verdict
    source_evidence_identities: dict[str,str]


class PrimaryResponse(BaseModel):
    validation_identity: str
    backtest_identity: str
    metrics: MetricsResponse


class FoldResponse(BaseModel):
    index: int
    boundaries: ValidationBoundaries
    fold_identity: str
    validation_identity: str
    backtest_identity: str
    metrics: MetricsResponse


class WalkForwardResponse(BaseModel):
    validation_identity: str
    active_folds: int
    summary: WalkForwardSummary
    folds: list[FoldResponse]

    @field_serializer('summary',when_used='json')
    def summary_strings(self,value):return wire(value)


class SensitivityResponse(BaseModel):
    configuration_identity: str
    validation_identity: str
    changed_fields: tuple[str,...]
    changed_parameters: dict[str,Financial]
    metrics: MetricsResponse
    differences: tuple[SensitivityComparison,...]

    @field_serializer('differences',when_used='json')
    def differences_strings(self,value):return wire(value)


class BenchmarkResponse(BaseModel):
    kind: str
    dataset_identity: str
    window_identity: str
    canonical_symbol: str
    quote_currency: str
    initial_capital: Financial
    ending_equity: Financial
    total_return: Financial|None
    maximum_drawdown: Financial|None
    exposure_percentage: Financial|None
    turnover: Financial|None
    fees: Financial
    spread_cost: Financial
    slippage_cost: Financial
    comparison_identity: str
    result_identity: str
    strategy_minus_benchmark: dict[str,Financial|None]


class StressResponse(BaseModel):
    code: str
    scenario_identity: str
    assumptions: ExecutionResponse
    validation_identity: str|None
    result_identity: str|None
    metrics: MetricsResponse|None
    differences: tuple[StressComparison,...]|None
    execution_failure: str|None
    failure_source_identity: str|None

    @field_serializer('differences',when_used='json')
    def differences_strings(self,value):return wire(value)


class ExplanationResponse(BaseModel):
    insufficiency_reasons: tuple[str,...]
    failure_reasons: tuple[str,...]
    passed_criteria: tuple[str,...]
    benchmark_context: list[BenchmarkResponse]


class ValidationResponse(BaseModel):
    validation: ValidationMetadata
    primary_oos: PrimaryResponse
    walk_forward: WalkForwardResponse
    sensitivity: list[SensitivityResponse]
    sensitivity_baseline_configuration_identity: str
    sensitivity_identity: str
    benchmarks: list[BenchmarkResponse]
    stress: list[StressResponse]
    stress_evidence_identity: str
    criteria: tuple[CriterionResult,...]
    verdict_explanation: ExplanationResponse

    @field_serializer('criteria',when_used='json')
    def criterion_strings(self,value):return wire(value)
