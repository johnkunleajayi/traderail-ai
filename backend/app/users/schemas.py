from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    name: str
    avatar_url: str | None
    country: str | None
    base_currency: str
    status: str
    created_at: datetime
    updated_at: datetime