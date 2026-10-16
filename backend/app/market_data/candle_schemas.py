from datetime import timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_serializer, field_validator, model_validator

Timeframe = Literal["15m", "1h", "4h", "1d"]


class ProviderCandle(BaseModel):
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: Decimal = Field(gt=0, allow_inf_nan=False)
    high: Decimal = Field(gt=0, allow_inf_nan=False)
    low: Decimal = Field(gt=0, allow_inf_nan=False)
    close: Decimal = Field(gt=0, allow_inf_nan=False)
    volume: Decimal = Field(ge=0, allow_inf_nan=False)
    retrieved_at: AwareDatetime

    @field_validator("open", "high", "low", "close", "volume", mode="before")
    @classmethod
    def decimal_only(cls, value):
        if isinstance(value, (bool, float)):
            raise ValueError("Financial values require Decimal, never float")
        return value

    @field_validator("open_time", "close_time", "retrieved_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def consistent_candle(self):
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("Impossible OHLC relationships")
        if not self.open_time < self.close_time <= self.retrieved_at:
            raise ValueError("Invalid or unfinished candle interval")
        return self

    @field_serializer("open", "high", "low", "close", "volume")
    def decimal_string(self, value: Decimal) -> str:
        return format(value, "f")


class CandleResponse(ProviderCandle):
    canonical_symbol: str
    provider: str
    timeframe: Timeframe
