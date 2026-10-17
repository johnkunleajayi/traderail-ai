"""A single canonical analytical observation with deterministic causal validation."""
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from collections.abc import Mapping
from app.market_intelligence.types import Candle, DatasetContext, FeatureRow
from app.market_intelligence.validation import validate_dataset
from app.market_intelligence.numeric import calculation_context
from app.market_regimes.config import RegimeConfiguration
from app.market_regimes.types import Evidence, Regime, RegimeResult
from app.market_regimes.validation import validate_row
from app.strategy_lab.canonical import canonical_json, fingerprint, utc_timestamp
from .configuration import StrategyDefinition, reason_codes

STRATEGY_INPUT_FORMAT = "strategy.input/v2"


class PositionState(str, Enum):
    CASH = "CASH"
    LONG = "LONG"
    SHORT = "SHORT"


def canonical_values(value):
    if isinstance(value, datetime):
        return utc_timestamp(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: canonical_values(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_values(item) for item in value]
    return value


def validate_regime(result: RegimeResult, intelligence: FeatureRow) -> None:
    if (not isinstance(result, RegimeResult) or not isinstance(result.regime, Regime)
        or not isinstance(result.evidence, Evidence) or not isinstance(result.configuration, RegimeConfiguration)
        or not isinstance(result.feature_engine_version, str) or not result.feature_engine_version.strip()):
        raise ValueError("A complete typed regime result and engine versions are required")
    evidence = result.evidence
    if not isinstance(evidence.reason_codes, tuple):
        raise ValueError("Regime reason codes must be immutable")
    reason_codes(evidence.reason_codes)
    non_decimal = {"breakout_direction", "candidate", "confirmation_count", "confirmation_required",
        "retained_previous", "previous_regime", "available_candles", "candidate_history_required", "reason_codes"}
    for item in fields(Evidence):
        value = getattr(evidence, item.name)
        if item.name not in non_decimal and value is not None:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("Regime evidence must preserve finite Decimal values")
    for name in ("confirmation_count", "confirmation_required", "available_candles", "candidate_history_required"):
        value = getattr(evidence, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Invalid regime evidence counts")
    if (evidence.available_candles < 1 or (evidence.confirmation_count > 0 and evidence.confirmation_required == 0)
        or not isinstance(evidence.retained_previous, bool) or not isinstance(evidence.candidate, Regime)
        or (evidence.previous_regime is not None and not isinstance(evidence.previous_regime, Regime))
        or evidence.breakout_direction not in (None, "UP", "DOWN")):
        raise ValueError("Invalid regime evidence structure")
    for name in ("rsi_14", "macd", "macd_signal", "macd_histogram", "realized_volatility"):
        value = getattr(evidence, name)
        if value is not None and value != getattr(intelligence, name):
            raise ValueError("Regime evidence does not match supplied intelligence")


@dataclass(frozen=True, slots=True)
class StrategyInput:
    context: DatasetContext
    candle: Candle
    intelligence: FeatureRow
    regime: RegimeResult
    definition: StrategyDefinition
    as_of: datetime
    position_state: PositionState = PositionState.CASH
    input_identity: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.position_state, PositionState):
            raise ValueError("An explicit CASH/LONG/SHORT thesis-state enum is required")
        if not isinstance(self.definition, StrategyDefinition):
            raise ValueError("An immutable strategy definition is required")
        if not isinstance(self.candle, Candle):
            raise ValueError("A typed completed candle is required")
        utc_timestamp(self.as_of)
        for value in (self.candle.open_time, self.candle.close_time, self.candle.retrieved_at):
            utc_timestamp(value)
        as_of = self.as_of.astimezone(timezone.utc)
        candle_utc = replace(self.candle, open_time=self.candle.open_time.astimezone(timezone.utc),
            close_time=self.candle.close_time.astimezone(timezone.utc),
            retrieved_at=self.candle.retrieved_at.astimezone(timezone.utc))
        validate_dataset(self.context, (candle_utc,), as_of)
        if candle_utc.retrieved_at > as_of:
            raise ValueError("Candle retrieval cannot be in the future of the observation boundary")
        if not isinstance(self.intelligence, FeatureRow):
            raise ValueError("A complete intelligence row is required")
        for item in fields(FeatureRow):
            if item.name not in {"canonical_symbol", "provider", "timeframe", "open_time", "close_time"}:
                value = getattr(self.intelligence, item.name)
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise ValueError("All current intelligence measurements must be available finite Decimals")
        expected = (self.context.canonical_symbol, self.context.provider, self.context.timeframe)
        with calculation_context():
            problem = validate_row(self.intelligence, 0, None, expected, as_of)
        if problem:
            raise ValueError(problem)
        validate_regime(self.regime, self.intelligence)
        for row in (self.intelligence, self.regime):
            if (row.canonical_symbol, row.provider, row.timeframe) != expected:
                raise ValueError("Analytical instrument/provider/timeframe mismatch")
            utc_timestamp(row.open_time)
            utc_timestamp(row.close_time)
            if (row.open_time.astimezone(timezone.utc) != self.candle.open_time.astimezone(timezone.utc)
                or row.close_time.astimezone(timezone.utc) != self.candle.close_time.astimezone(timezone.utc)):
                raise ValueError("Analytical timestamp mismatch; future or shifted rows are not allowed")
        if self.intelligence.close != self.candle.close:
            raise ValueError("Intelligence close does not match the completed candle")
        candle = asdict(self.candle)
        candle.pop("instrument_id")  # Validate local linkage, identify canonical content across databases.
        payload = {"context": {"canonical_symbol": expected[0], "provider": expected[1], "timeframe": expected[2]},
            "candle": candle, "intelligence": asdict(self.intelligence), "regime": asdict(self.regime),
            "configuration_identity": self.definition.configuration_identity,
            "position_state": self.position_state.value}
        object.__setattr__(self, "input_identity", fingerprint(STRATEGY_INPUT_FORMAT,
                           canonical_json(canonical_values(payload))))
