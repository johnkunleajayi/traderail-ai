"""Bounded API contract: exact decimal strings and explicit UTC timestamps."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
import re
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, BeforeValidator, PlainSerializer, field_validator, model_validator
from app.market_data.candle_schemas import Timeframe
from app.market_regimes.config import REGIME_CONFIG, candidate_first_index
from app.strategies.decisions import DecisionType
from app.strategies.inputs import PositionState
from app.market_regimes.types import Regime

MIN_WARMUP_CANDLES = candidate_first_index(REGIME_CONFIG)
MAX_HISTORY_CANDLES = 300
API_VERSION = "1.0.0"


def decimal_input(value):
    if not isinstance(value, str) or not re.fullmatch(r"-?(?:(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,24})?|\.[0-9]{1,24})", value):
        raise ValueError("Use a decimal string with at most 24 integer and 24 fractional digits")
    return Decimal(value)


def timestamp_input(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})",value):
        raise ValueError("Use an ISO-8601 timestamp with an explicit timezone and at most microsecond precision")
    return value


MoneyInput = Annotated[Decimal, BeforeValidator(decimal_input,json_schema_input_type=str)]
Financial = Annotated[Decimal, PlainSerializer(lambda value: format(value,"f"),return_type=str)]
UTCDate = Annotated[AwareDatetime, PlainSerializer(lambda value: value.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),return_type=str)]


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fee_rate: MoneyInput = Field(ge=0,le=1)
    full_spread_rate: MoneyInput = Field(ge=0,le=1)
    slippage_rate: MoneyInput = Field(ge=0,le=1)
    delay_candles: int = Field(default=0,strict=True,ge=0,le=MAX_HISTORY_CANDLES)


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]{0,15}/[A-Z][A-Z0-9]{0,15}$")
    provider: Literal["BINANCE"] = "BINANCE"
    timeframe: Timeframe = "1h"
    requested_candle_count: int = Field(default=100,strict=True,ge=MIN_WARMUP_CANDLES+1,le=MAX_HISTORY_CANDLES)
    evaluation_start: Annotated[AwareDatetime,BeforeValidator(timestamp_input)]
    evaluation_end: Annotated[AwareDatetime,BeforeValidator(timestamp_input)]
    initial_capital: MoneyInput = Field(gt=0)
    quantity: MoneyInput = Field(gt=0)
    execution: ExecutionRequest
    strategy_family: Literal["ADAPTIVE_TREND"] = "ADAPTIVE_TREND"
    strategy_version: Literal["1.0.0"] = "1.0.0"

    @field_validator("evaluation_start","evaluation_end")
    @classmethod
    def utc(cls,value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def boundaries(self):
        if self.evaluation_start >= self.evaluation_end:
            raise ValueError("Evaluation start must precede the exclusive evaluation end")
        return self


class ExecutionResponse(BaseModel):
    fee_rate: Financial
    full_spread_rate: Financial
    slippage_rate: Financial
    delay_candles: int
    timing: str
    cost_model_identity: str
    execution_configuration_identity: str


class ExperimentMetadata(BaseModel):
    api_version: str
    experiment_identity: str
    dataset_identity: str
    research_result_identity: str
    strategy_family: str
    strategy_version: str
    strategy_configuration_identity: str
    canonical_symbol: str
    provider: str
    timeframe: str
    requested_candle_count: int
    candle_count: int
    warmup_start: UTCDate
    warmup_candle_count: int
    required_warmup_candles: int
    evaluation_start: UTCDate
    evaluation_end: UTCDate
    evaluation_candle_count: int
    initial_capital: Financial
    quantity: Financial
    execution: ExecutionResponse
    accounting_configuration_identity: str
    market_intelligence_version: str
    regime_engine_version: str


class SummaryResponse(BaseModel):
    initial_capital: Financial
    ending_equity: Financial
    total_return: Financial | None
    gross_return: Financial | None
    net_return: Financial | None
    max_drawdown: Financial | None
    completed_trade_count: int
    win_rate: Financial | None
    profit_factor: Financial | None
    expectancy: Financial | None
    exposure: Financial | None
    turnover: Financial | None
    fee_total: Financial
    spread_cost_total: Financial
    slippage_cost_total: Financial


class TradeResponse(BaseModel):
    trade_identity: str
    direction: PositionState
    entry_decision_time: UTCDate
    entry_fill_time: UTCDate
    entry_reference_price: Financial
    entry_fill_price: Financial
    exit_decision_time: UTCDate
    exit_fill_time: UTCDate
    exit_reference_price: Financial
    exit_fill_price: Financial
    quantity: Financial
    gross_pnl: Financial
    fees: Financial
    spread_cost: Financial
    slippage_cost: Financial
    net_pnl: Financial
    entry_reason_codes: tuple[str,...]
    exit_reason_codes: tuple[str,...]


class OpenPositionResponse(BaseModel):
    direction: PositionState
    quantity: Financial
    entry_decision_time: UTCDate
    entry_fill_time: UTCDate
    entry_reference_price: Financial
    entry_price: Financial
    entry_fee_recognized: Financial
    mark_price: Financial
    mark_timestamp: UTCDate
    unrealized_pnl: Financial
    reserved_capital: Financial
    restricted_short_proceeds: Financial
    entry_reason_codes: tuple[str,...]


class EquityResponse(BaseModel):
    timestamp: UTCDate
    cash: Financial
    realized_capital: Financial
    realized_pnl: Financial
    unrealized_pnl: Financial
    total_equity: Financial
    drawdown: Financial | None
    position_state: PositionState


class DecisionResponse(BaseModel):
    open_time: UTCDate
    timestamp: UTCDate
    decision_identity: str
    decision: DecisionType
    reason_codes: tuple[str,...]
    position_before: PositionState
    position_after: PositionState


class RegimeResponse(BaseModel):
    open_time: UTCDate
    timestamp: UTCDate
    regime: Regime
    candidate_regime: Regime
    confirmation_count: int
    confirmation_required: int
    retained_previous: bool
    previous_regime: Regime | None
    reason_codes: tuple[str,...]


class UnfilledResponse(BaseModel):
    decision_identity: str
    decision: DecisionType
    decision_time: UTCDate
    eligible_fill_time: UTCDate
    filled_state: PositionState
    reason_code: str


class ExperimentResponse(BaseModel):
    experiment: ExperimentMetadata
    summary: SummaryResponse
    trades: list[TradeResponse]
    open_position: OpenPositionResponse | None
    equity_curve: list[EquityResponse]
    decisions: list[DecisionResponse]
    regimes: list[RegimeResponse]
    unfilled_decisions: list[UnfilledResponse]
