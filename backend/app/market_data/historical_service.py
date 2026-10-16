from app.market_data.candle_schemas import CandleResponse, Timeframe
from app.market_data.historical_mappings import HistoricalTarget
from app.market_data.provider import HistoricalMarketDataProvider


def fetch_historical_candles(target: HistoricalTarget, timeframe: Timeframe, limit: int,
                             provider: HistoricalMarketDataProvider) -> list[CandleResponse]:
    candles = provider.fetch_candles(target.provider_symbol, timeframe, limit)
    return [CandleResponse(**candle.model_dump(), canonical_symbol=target.canonical_symbol,
                           provider=target.provider, timeframe=timeframe) for candle in candles]
