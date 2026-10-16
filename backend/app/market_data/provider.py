from typing import Protocol
from app.market_data.schemas import ProviderTicker


class ProviderUnavailable(Exception):
    """Public source failed validation or could not be reached; no fallback data."""


class MarketDataProvider(Protocol):
    def fetch_ticker(self, venue_symbol: str) -> ProviderTicker: ...
