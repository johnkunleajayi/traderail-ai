from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.market_data.binance import get_historical_provider
from app.market_data.candle_schemas import CandleResponse, Timeframe
from app.market_data.historical_mappings import (HistoricalMappingUnavailable, HistoricalTarget,
                                                 resolve_historical_target)
from app.market_data.historical_service import fetch_historical_candles
from app.market_data.provider import HistoricalMarketDataProvider, ProviderUnavailable
from app.market_data.service import InstrumentNotFound

router = APIRouter(prefix="/market-data", tags=["market-data"])


def historical_target(canonical_symbol: str = Path(min_length=3, max_length=96),
                      db: Session = Depends(get_session)) -> HistoricalTarget:
    try:
        return resolve_historical_target(db, canonical_symbol)
    except InstrumentNotFound:
        raise HTTPException(404, {"code": "instrument_not_found", "message": "Instrument not found"}) from None
    except HistoricalMappingUnavailable:
        raise HTTPException(409, {"code": "historical_provider_unsupported",
                                 "message": "No historical data provider mapping for this instrument"}) from None


@router.get("/candles/{canonical_symbol:path}", response_model=list[CandleResponse])
def candles(response: Response, timeframe: Timeframe = Query(default="1h"),
            limit: int = Query(default=100, ge=1, le=1000),
            target: HistoricalTarget = Depends(historical_target),
            provider: HistoricalMarketDataProvider = Depends(get_historical_provider)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return fetch_historical_candles(target, timeframe, limit, provider)
    except ProviderUnavailable:
        raise HTTPException(503, {"code": "historical_data_unavailable",
                                 "message": "Historical candles are temporarily unavailable"},
                            headers={"Cache-Control": "no-store"}) from None
