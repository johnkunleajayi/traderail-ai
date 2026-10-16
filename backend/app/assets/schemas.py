from typing import Literal

from pydantic import BaseModel, ConfigDict


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    symbol: str
    name: str
    asset_type: str
    status: Literal["active", "disabled"]