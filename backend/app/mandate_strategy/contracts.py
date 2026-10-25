"""Immutable explainable observations, not TradeIntents or Risk decisions."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from app.mandate_eligibility.contracts import Frozen, MandateContext, TechnicalConfiguration
from app.market_regimes.types import Regime
from app.strategies.decisions import DecisionType
from app.strategy_lab.canonical import utc_timestamp

SHA = r"^sha256:[0-9a-f]{64}$"

class ObservationOutcome(StrEnum):
    ENTER_LONG = "ENTER_LONG"
    NO_TRADE = "NO_TRADE"

class ObservationReason(StrEnum):
    LONG_PROPOSAL_ONLY = "LONG_PROPOSAL_ONLY"
    STRATEGY_NO_ACTION = "STRATEGY_NO_ACTION"
    UNSUPPORTED_DIRECTION = "UNSUPPORTED_DIRECTION"
    NO_HELD_LONG_CONTEXT = "NO_HELD_LONG_CONTEXT"

class UtcEvidence(Frozen):
    @field_validator('*')
    @classmethod
    def utc_times(cls, value):
        if isinstance(value, datetime):
            utc_timestamp(value)
            return value.astimezone(timezone.utc)
        return value

class MarketEvidence(UtcEvidence):
    instrument_id: UUID
    canonical_symbol: Literal["BTC/USDT"]
    provider: Literal["BINANCE"]
    timeframe: Literal["1h"]
    history_count: int
    market_policy_version: str
    market_evidence_identity: str = Field(pattern=SHA)
    intelligence_identity: str = Field(pattern=SHA)
    regime_identity: str = Field(pattern=SHA)
    candle_open_time: datetime
    candle_close_time: datetime
    retrieved_at: datetime
    valid_until: datetime

class RegimeEvidence(Frozen):
    regime: Regime
    candidate: Regime
    confirmation_count: int
    confirmation_required: int
    retained_previous: bool
    reason_codes: tuple[str, ...]

class StrategyDecisionEvidence(UtcEvidence):
    strategy_family: str
    strategy_version: str
    configuration_identity: str = Field(pattern=SHA)
    input_identity: str = Field(pattern=SHA)
    decision_identity: str = Field(pattern=SHA)
    timestamp: datetime
    decision: DecisionType
    reason_codes: tuple[str, ...]

class MandateObservationResult(UtcEvidence):
    scope: Literal["STRATEGY_DECISION_ONLY"] = "STRATEGY_DECISION_ONLY"
    status: Literal["EVALUATED"] = "EVALUATED"
    orchestration_version: Literal["mandate-strategy-v1"] = "mandate-strategy-v1"
    evaluation_identity: str = Field(pattern=SHA)
    mandate_identity: UUID
    user_intent: MandateContext
    eligibility_identity: str = Field(pattern=SHA)
    eligibility_policy_version: str
    eligibility_policy_identity: str = Field(pattern=SHA)
    technical_configuration: TechnicalConfiguration
    portfolio_identity: UUID
    book_identity: str = Field(pattern=SHA)
    position_context: Literal["CASH"] = "CASH"
    market: MarketEvidence
    regime: RegimeEvidence
    strategy_decision: StrategyDecisionEvidence
    outcome: ObservationOutcome
    reason_codes: tuple[ObservationReason, ...]
    evaluated_at: datetime

class MandateEvaluationError(ValueError):
    def __init__(self, code: str, message: str, reasons: tuple[str, ...] = ()):
        super().__init__(message)
        self.code, self.reasons = code, reasons
