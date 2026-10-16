from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer


class PaperPortfolioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    environment: Literal["PAPER"]
    base_currency: Literal["USD"]
    starting_balance: Decimal
    cash_balance: Decimal
    status: Literal["active", "disabled"]

    @field_serializer("starting_balance", "cash_balance")
    def serialize_money(self, value: Decimal) -> str:
        # JSON decimal strings preserve cents without binary floating-point conversion.
        return format(value, ".2f")