from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.instruments.schemas import InstrumentResponse


class VenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    name: str
    venue_type: str
    status: Literal["active", "disabled"]


class VenueInstrumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    venue: VenueResponse
    instrument: InstrumentResponse
    venue_symbol: str
    status: Literal["active", "disabled"]