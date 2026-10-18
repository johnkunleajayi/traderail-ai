"""Versioned next-open convention and exact bridge from existing duration-based costs."""
from dataclasses import dataclass, field
from app.market_intelligence.types import TIMEFRAME_SECONDS
from app.strategies.inputs import canonical_values
from app.strategy_lab.canonical import canonical_json, fingerprint
from app.strategy_lab.costs import ResearchCostModel
from app.strategy_lab.numeric import duration_microseconds

EXECUTION_VERSION = "1.0.0"


def identity(namespace, payload):
    return fingerprint(namespace, canonical_json(canonical_values(payload)))


@dataclass(frozen=True, slots=True)
class HistoricalExecutionConfiguration:
    cost_model: ResearchCostModel
    timeframe: str
    delay_candles: int = field(init=False)
    cost_model_identity: str = field(init=False)
    configuration_identity: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.cost_model, ResearchCostModel):
            raise ValueError("A typed research cost model is required")
        if not isinstance(self.timeframe, str) or self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported execution timeframe")
        model = self.cost_model
        ResearchCostModel(model.trading_fee_rate, model.spread_rate, model.slippage_rate, model.execution_delay)
        microseconds = duration_microseconds(model.execution_delay, "execution_delay")
        period = TIMEFRAME_SECONDS[self.timeframe] * 1000000
        delay, remainder = divmod(microseconds, period)
        if remainder:
            raise ValueError("Execution delay must be a whole number of candles for this timeframe")
        object.__setattr__(self, "delay_candles", delay)
        object.__setattr__(self, "cost_model_identity", identity("strategy-lab.cost-model/v1", model.configuration()))
        object.__setattr__(self, "configuration_identity", identity("strategy-lab.execution-configuration/v1", self.configuration()))

    def configuration(self):
        return {"execution_version": EXECUTION_VERSION, "timing": "NEXT_COMPLETED_CANDLE_OPEN",
                "timeframe": self.timeframe, "additional_delay_candles": self.delay_candles,
                "eligible_index_offset": 1 + self.delay_candles,
                "price_adjustment": "additive_adverse_half_spread_and_slippage_on_reference_open",
                "fee_basis": "absolute_quantity_times_adjusted_fill_price_in_quote_currency",
                "cost_model": self.cost_model.configuration()}
