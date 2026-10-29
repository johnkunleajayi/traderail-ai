"""Exact completed-candle binding for explicit callers; no data fetching."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

class ExpectedCandleMismatch(ValueError):
    pass

@dataclass(frozen=True)
class ExpectedCandle:
    instrument_id: UUID
    open_time: datetime
    close_time: datetime
    canonical_symbol: str = "BTC/USDT"
    provider: str = "BINANCE"
    timeframe: str = "1h"

    def require_fields(self, instrument_id, symbol, provider, timeframe, opened, closed):
        if (instrument_id,symbol,provider,timeframe,opened,closed) != (
            self.instrument_id,self.canonical_symbol,self.provider,self.timeframe,self.open_time,self.close_time):
            raise ExpectedCandleMismatch("Actual observation differs from expected completed candle")

    def require(self, context, candle):
        self.require_fields(context.instrument_id,context.canonical_symbol,context.provider,context.timeframe,
                            candle.open_time,candle.close_time)

    def require_market(self, market):
        self.require_fields(market.instrument_id,market.canonical_symbol,market.provider,market.timeframe,
                            market.candle_open_time,market.candle_close_time)
