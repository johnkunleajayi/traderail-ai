"""Strict mandate intent. Allocation is a preference, never a trusted balance."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer
from .domain import RiskLevel, TradingStyle, MandateStatus

class MandateIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment: Literal["PAPER"] = "PAPER"
    capital_allocation: Decimal = Field(gt=0, allow_inf_nan=False)
    allocation_currency: Literal["USDT"] = "USDT"
    instrument: Literal["BTC/USDT"] = "BTC/USDT"
    risk_level: RiskLevel
    trading_style: TradingStyle
    direction_capability: Literal["LONG_ONLY"] = "LONG_ONLY"

    @field_validator("capital_allocation", mode="before")
    @classmethod
    def exact_allocation(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Use an exact decimal string for capital allocation")
        return value

    @field_serializer("capital_allocation", when_used="json")
    def serialize_allocation(self, value: Decimal) -> str:
        return format(value, "f")

class LifecycleAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class MandateResponse(MandateIntent):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)
    id: UUID
    user_id: UUID
    status: MandateStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Mandate timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)
