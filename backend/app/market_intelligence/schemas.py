from decimal import Decimal
from pydantic import AwareDatetime, BaseModel, field_serializer
from app.market_intelligence.config import FeatureConfiguration


class FeatureResponse(BaseModel):
    open_time: AwareDatetime
    close_time: AwareDatetime
    close: Decimal
    ema_20: Decimal | None
    ema_50: Decimal | None
    rsi_14: Decimal | None
    atr_14: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    simple_return: Decimal | None
    log_return: Decimal | None
    realized_volatility: Decimal | None

    @field_serializer("close", "ema_20", "ema_50", "rsi_14", "atr_14", "macd", "macd_signal",
                      "macd_histogram", "simple_return", "log_return", "realized_volatility")
    def decimal_string(self, value: Decimal | None) -> str | None:
        return format(value, "f") if value is not None else None


class IntelligenceResponse(BaseModel):
    canonical_symbol: str
    provider: str
    timeframe: str
    candle_count: int
    first_open_time: AwareDatetime
    latest_close_time: AwareDatetime
    feature_configuration: FeatureConfiguration
    features: list[FeatureResponse]
