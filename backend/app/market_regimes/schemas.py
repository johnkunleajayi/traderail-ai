from dataclasses import asdict
from decimal import Decimal
from pydantic import AwareDatetime, BaseModel, field_serializer
from app.market_regimes.config import RegimeConfiguration
from app.market_regimes.types import Evidence, Regime, RegimeResult


def decimal_strings(value):
    """Serialize domain Decimals as exact fixed-point strings, including configuration."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: decimal_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [decimal_strings(item) for item in value]
    return value


class ConfirmationResponse(BaseModel):
    count: int
    required: int
    previous_regime: Regime | None
    retained_previous: bool
    available_candles: int
    candidate_history_required: int


class RegimeRowResponse(BaseModel):
    open_time: AwareDatetime | None
    close_time: AwareDatetime | None
    regime: Regime
    candidate_regime: Regime
    confirmation: ConfirmationResponse
    evidence: Evidence
    reason_codes: tuple[str, ...]

    @field_serializer("evidence", when_used="json")
    def evidence_strings(self, value: Evidence):
        return decimal_strings(asdict(value))

    @classmethod
    def from_result(cls, result: RegimeResult):
        e = result.evidence
        return cls(open_time=result.open_time, close_time=result.close_time, regime=result.regime,
            candidate_regime=e.candidate, confirmation=ConfirmationResponse(count=e.confirmation_count,
                required=e.confirmation_required, previous_regime=e.previous_regime,
                retained_previous=e.retained_previous, available_candles=e.available_candles,
                candidate_history_required=e.candidate_history_required),
            evidence=e, reason_codes=e.reason_codes)


class RegimeResponse(BaseModel):
    canonical_symbol: str
    base_asset: str
    quote_asset: str
    provider: str
    timeframe: str
    candle_count: int
    dataset_start: AwareDatetime
    dataset_end: AwareDatetime
    regime_engine_version: str
    feature_engine_version: str
    regime_configuration: RegimeConfiguration
    regimes: list[RegimeRowResponse]
    latest_regime: RegimeRowResponse

    @field_serializer("regime_configuration", when_used="json")
    def configuration_strings(self, value: RegimeConfiguration):
        return decimal_strings(asdict(value))
