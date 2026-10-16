from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

TIMEFRAME_SECONDS = MappingProxyType({"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400})


@dataclass(frozen=True)
class DatasetContext:
    instrument_id: UUID
    canonical_symbol: str
    provider: str
    timeframe: str


@dataclass(frozen=True)
class Candle:
    instrument_id: UUID
    provider: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    retrieved_at: datetime


@dataclass(frozen=True)
class FeatureRow:
    canonical_symbol: str
    provider: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    close: Decimal
    ema_20: Decimal | None
    ema_50: Decimal | None
    rsi_14: Decimal | None
    atr_14: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    simple_return: Decimal | None
    log_return: Decimal | None
    realized_volatility: Decimal | None


class InvalidCandleData(ValueError):
    def __init__(self, code: str, index: int | None = None):
        self.code = code
        self.index = index
        super().__init__(code if index is None else f"{code} at candle index {index}")
