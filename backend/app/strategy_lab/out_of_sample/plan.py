"""Explicit frozen IS/OOS research assumptions; no parameter selection."""
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_regimes.config import REGIME_CONFIG, candidate_first_index
from app.strategies.configuration import StrategyDefinition
from app.strategies.inputs import PositionState
from app.strategy_lab.accounting.config import ACCOUNTING_CONFIG, AccountingConfiguration
from app.strategy_lab.dataset import ResearchDataset, create_dataset
from app.strategy_lab.execution.config import HistoricalExecutionConfiguration, identity
from app.strategy_lab.numeric import require_decimal
from app.strategy_lab.validation import TimeWindow, ValidationKind, ValidationPlan, ValidationSplit

VALIDATION_ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class OutOfSamplePlan:
    dataset: ResearchDataset
    warmup: TimeWindow
    split: ValidationSplit
    strategy_definition: StrategyDefinition
    execution_configuration: HistoricalExecutionConfiguration
    quantity: Decimal
    initial_capital: Decimal
    accounting_configuration: AccountingConfiguration = ACCOUNTING_CONFIG
    initial_state: PositionState = PositionState.CASH
    plan_identity: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.dataset, ResearchDataset) or not isinstance(self.dataset.candles, tuple) or not self.dataset.candles:
            raise ValueError("A frozen research dataset is required")
        if not isinstance(self.warmup, TimeWindow) or not isinstance(self.split, ValidationSplit):
            raise ValueError("Explicit typed warm-up and paired validation windows are required")
        if self.split.out_of_sample is None:
            raise ValueError("An explicit OOS window is required")
        validation = ValidationPlan(ValidationKind.OUT_OF_SAMPLE, (self.split,))
        validation.validate_for(self.dataset)
        data, ins, oos = self.dataset, self.split.in_sample, self.split.out_of_sample
        if (self.warmup.start != data.warmup_start or self.warmup.end != data.evaluation_start
            or ins.start != self.warmup.end or ins.end != oos.start or oos.end != data.evaluation_end):
            raise ValueError("Warm-up, IS and OOS must exactly partition the frozen dataset without gaps or hidden history")
        # Validate snapshot integrity without consulting a clock, provider or database.
        archival_cutoff = max(*(row.retrieved_at for row in data.candles), data.evaluation_end)
        checked = create_dataset(data.context, data.candles, warmup_start=data.warmup_start,
            evaluation_start=data.evaluation_start, evaluation_end=data.evaluation_end, as_of=archival_cutoff)
        if checked.dataset_fingerprint != data.dataset_fingerprint:
            raise ValueError("Frozen dataset fingerprint mismatch")
        if len(data.warmup_candles) < candidate_first_index(REGIME_CONFIG):
            raise ValueError("Insufficient explicit analytical warm-up")
        if not isinstance(self.strategy_definition, StrategyDefinition):
            raise ValueError("An immutable strategy definition is required")
        if not isinstance(self.execution_configuration, HistoricalExecutionConfiguration):
            raise ValueError("A typed historical execution configuration is required")
        if self.execution_configuration.timeframe != data.context.timeframe:
            raise ValueError("Execution timeframe must match the frozen dataset")
        if (not isinstance(self.accounting_configuration, AccountingConfiguration)
            or self.accounting_configuration != ACCOUNTING_CONFIG):
            raise ValueError("Only the unchanged v1 accounting conventions are supported")
        if self.initial_state is not PositionState.CASH:
            raise ValueError("Independent IS and OOS evaluations require a declared CASH start")
        for name in ("quantity", "initial_capital"):
            if require_decimal(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be positive")
        object.__setattr__(self, "plan_identity", identity("strategy-lab.oos-plan/v1", self.configuration()))

    @property
    def dataset_identity(self):
        return self.dataset.dataset_fingerprint

    @property
    def oos_analytical_context(self):
        # Explicitly includes all IS candles as analytics-only history, not IS performance/state.
        return TimeWindow(self.warmup.start, self.split.out_of_sample.start)

    def configuration(self):
        return {"validation_engine_version": VALIDATION_ENGINE_VERSION,
            "dataset_identity": self.dataset_identity, "warmup": self.warmup.configuration(),
            "validation": ValidationPlan(ValidationKind.OUT_OF_SAMPLE, (self.split,)).configuration(),
            "oos_analytical_context": self.oos_analytical_context.configuration(),
            "state_boundary": "independent_CASH_no_position_or_pending_fill_carry",
            "strategy_configuration_identity": self.strategy_definition.configuration_identity,
            "execution": self.execution_configuration.configuration(),
            "accounting": asdict(self.accounting_configuration),
            "initial_state": self.initial_state, "quantity": self.quantity,
            "initial_capital_per_period": self.initial_capital,
            "feature_configuration": asdict(FEATURE_CONFIG), "regime_configuration": asdict(REGIME_CONFIG)}
