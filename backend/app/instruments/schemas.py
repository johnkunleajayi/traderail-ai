from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.assets.schemas import AssetResponse


class InstrumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    canonical_symbol: str
    base_asset: AssetResponse
    quote_asset: AssetResponse
    market_type: str
    status: Literal["active", "disabled"]
    price_precision: int
    quantity_precision: int