"""Typed research configuration binding through existing experiment fingerprinting."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_regimes.config import REGIME_CONFIG
from .benchmarks import BenchmarkConfiguration
from .costs import ResearchCostModel
from .dataset import ResearchDataset
from .experiment import ExperimentSpecification, create_experiment
from .validation import ValidationPlan

RESEARCH_CONFIGURATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class ResearchConfiguration:
    validation: ValidationPlan
    benchmarks: BenchmarkConfiguration
    cost_model: ResearchCostModel

    def __post_init__(self):
        for value, expected in ((self.validation, ValidationPlan),
                                (self.benchmarks, BenchmarkConfiguration),
                                (self.cost_model, ResearchCostModel)):
            if not isinstance(value, expected):
                raise ValueError("Research configuration requires typed validation, benchmarks and costs")

    def configuration(self) -> dict:
        benchmark = self.benchmarks.configuration()
        benchmark["evaluation_windows"] = [window.configuration() for window in self.validation.evaluation_windows]
        return {"research_configuration_version": RESEARCH_CONFIGURATION_VERSION,
                "validation": self.validation.configuration(), "benchmarks": benchmark,
                "cost_model": self.cost_model.configuration()}


def create_research_experiment(dataset: ResearchDataset, *, research: ResearchConfiguration,
                              strategy_configuration: Mapping, created_at: datetime,
                              strategy_family: str | None = None, strategy_version: str | None = None,
                              market_intelligence_version: str = FEATURE_CONFIG.engine_version,
                              regime_engine_version: str = REGIME_CONFIG.engine_version) -> ExperimentSpecification:
    if not isinstance(research, ResearchConfiguration) or not isinstance(strategy_configuration, Mapping):
        raise ValueError("Typed research configuration and a strategy configuration mapping are required")
    research.validation.validate_for(dataset)
    # Existing immutable snapshot/identity/manifest machinery remains the source of truth.
    return create_experiment(dataset, strategy_configuration={"strategy": strategy_configuration,
        "research": research.configuration()}, created_at=created_at,
        strategy_family=strategy_family, strategy_version=strategy_version,
        market_intelligence_version=market_intelligence_version, regime_engine_version=regime_engine_version)
