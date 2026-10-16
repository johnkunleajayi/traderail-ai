from datetime import datetime
from typing import Literal
from pydantic import BaseModel
from app.market_data.candle_schemas import Timeframe


class IngestionResult(BaseModel):
    canonical_symbol: str
    provider: str
    timeframe: Timeframe
    fetched: int
    inserted: int
    unchanged: int


class CandleGap(BaseModel):
    first_missing_open_time: datetime
    last_missing_open_time: datetime
    missing_count: int


class DatasetQuality(BaseModel):
    canonical_symbol: str
    provider: str
    timeframe: Timeframe
    candle_count: int
    earliest_open_time: datetime | None
    latest_close_time: datetime | None
    expected_interval_seconds: int
    duplicate_count: int
    gaps: list[CandleGap]
    missing_interval_count: int
    freshness: Literal["empty", "fresh", "stale"]
    expected_latest_close_time: datetime
    evaluated_at: datetime
