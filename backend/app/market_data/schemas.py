from datetime import timezone
from decimal import Decimal
from pydantic import AwareDatetime, BaseModel, Field, field_serializer, field_validator


class ProviderTicker(BaseModel):
    last_price: Decimal = Field(gt=0, allow_inf_nan=False)
    source_timestamp: AwareDatetime | None
    retrieved_at: AwareDatetime

    @field_validator("last_price", mode="before")
    @classmethod
    def reject_float(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Use Decimal for financial values")
        return value

    @field_validator("source_timestamp", "retrieved_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc) if value is not None else None

    @field_serializer("last_price")
    def price_string(self, value: Decimal) -> str:
        return format(value, "f")


class TickerResponse(ProviderTicker):
    canonical_symbol: str
    quote_currency: str
    venue: str
