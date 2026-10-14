from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class HealthResponse(BaseModel):
    status: str = "healthy"

@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Application liveness only; does not check PostgreSQL."""
    return HealthResponse()