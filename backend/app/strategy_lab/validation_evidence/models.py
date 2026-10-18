"""Immutable evidence envelopes and auditable research outcomes; no authority."""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from app.strategy_lab.benchmark_evaluation import BenchmarkComparison
from app.strategy_lab.out_of_sample import OutOfSampleResult
from app.strategy_lab.walk_forward import WalkForwardResult
from app.strategy_lab.sensitivity.engine import SensitivityResult
from app.strategy_lab.stress_testing import StressPlan, StressResult, ScenarioResult
from .policy import ValidationPolicy


class Verdict(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    VALIDATION_CANDIDATE = "VALIDATION_CANDIDATE"


class Category(str, Enum):
    EVIDENCE_COVERAGE = "EVIDENCE_COVERAGE"
    RETURN_QUALITY = "RETURN_QUALITY"
    DRAWDOWN_CONTROL = "DRAWDOWN_CONTROL"
    WALK_FORWARD_CONSISTENCY = "WALK_FORWARD_CONSISTENCY"
    PARAMETER_STABILITY = "PARAMETER_STABILITY"
    COST_RESILIENCE = "COST_RESILIENCE"
    BENCHMARK_CONTEXT = "BENCHMARK_CONTEXT"


class Status(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class StressFailureCode(str, Enum):
    UNAFFORDABLE_EXECUTION = "UNAFFORDABLE_EXECUTION"


@dataclass(frozen=True, slots=True)
class StressFailure:
    scenario_code: str
    reason: StressFailureCode
    source_identity: str  # Caller-recorded failed-attempt manifest; no invented metrics.

    def __post_init__(self):
        if (not isinstance(self.scenario_code, str) or not self.scenario_code
            or not isinstance(self.reason, StressFailureCode) or not isinstance(self.source_identity, str) or not self.source_identity):
            raise ValueError("Typed, provenance-bearing stress failure required")


@dataclass(frozen=True, slots=True)
class StressEvidence:
    plan: StressPlan
    completed: tuple[ScenarioResult, ...]
    failures: tuple[StressFailure, ...]
    source_identity: str

    def __post_init__(self):
        if not isinstance(self.plan, StressPlan) or not isinstance(self.source_identity, str) or not self.source_identity:
            raise ValueError("A declared stress plan and source identity are required")
        object.__setattr__(self, "completed", tuple(self.completed))
        object.__setattr__(self, "failures", tuple(self.failures))
        if any(not isinstance(row, ScenarioResult) for row in self.completed) or any(not isinstance(row, StressFailure) for row in self.failures):
            raise ValueError("Typed completed/failed stress evidence required")

    @classmethod
    def from_result(cls, result: StressResult):
        if not isinstance(result, StressResult):
            raise ValueError("Existing StressResult required")
        if result.baseline not in result.scenarios:
            raise ValueError("Stress result omitted its baseline")
        return cls(result.plan, result.scenarios, (), result.stress_identity)


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    oos: OutOfSampleResult
    walk_forward: WalkForwardResult | None = None
    sensitivity: SensitivityResult | None = None
    stress: StressEvidence | None = None
    benchmarks: tuple[BenchmarkComparison, ...] = ()

    def __post_init__(self):
        if not isinstance(self.oos, OutOfSampleResult):
            raise ValueError("Existing paired validation result required")
        for name, expected in (("walk_forward", WalkForwardResult), ("sensitivity", SensitivityResult), ("stress", StressEvidence)):
            if getattr(self, name) is not None and not isinstance(getattr(self, name), expected):
                raise ValueError(f"Invalid {name} evidence")
        object.__setattr__(self, "benchmarks", tuple(self.benchmarks))
        if any(not isinstance(row, BenchmarkComparison) for row in self.benchmarks):
            raise ValueError("Typed benchmark comparisons required")


@dataclass(frozen=True, slots=True)
class CriterionResult:
    code: str
    category: Category
    observed: Decimal | int | bool | str | None
    required: Decimal | int | bool | str | None
    status: Status
    reason_code: str
    explanation: str


@dataclass(frozen=True, slots=True)
class ResearchValidationReport:
    strategy_family: str
    strategy_version: str
    strategy_configuration_identity: str
    dataset_identity: str
    policy: ValidationPolicy
    source_identities: tuple[tuple[str, str], ...]
    verdict: Verdict
    criteria: tuple[CriterionResult, ...]
    insufficiency_reasons: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    passed_criteria: tuple[str, ...]
    benchmark_context: tuple[BenchmarkComparison, ...]
    evidence_identity: str
    report_identity: str
