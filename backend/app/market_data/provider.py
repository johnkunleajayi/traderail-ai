from typing import Protocol
from app.market_data.schemas import ProviderTicker
from app.market_data.candle_schemas import ProviderCandle, Timeframe


class ProviderUnavailable(Exception):
    """Public source failed validation or could not be reached; no fallback data."""


class MarketDataProvider(Protocol):
    def fetch_ticker(self, venue_symbol: str) -> ProviderTicker: ...


class HistoricalMarketDataProvider(Protocol):
    """Historical capability, separate from public tickers and execution venues."""
    def fetch_candles(self, provider_symbol: str, timeframe: "Timeframe", limit: int) -> list["ProviderCandle"]: ...
