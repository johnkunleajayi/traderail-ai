from fastapi import APIRouter, Depends, HTTPException, Path, Response
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.market_data.provider import MarketDataProvider, ProviderUnavailable
from app.market_data.quidax import get_public_provider
from app.market_data.schemas import TickerResponse
from app.market_data.service import (InstrumentNotFound, MappingUnavailable, TickerTarget,
                                     fetch_ticker, resolve_ticker_target)

router = APIRouter(prefix="/market-data", tags=["market-data"])


def ticker_target(canonical_symbol: str = Path(min_length=3, max_length=96),
                  db: Session = Depends(get_session)) -> TickerTarget:
    try:
        return resolve_ticker_target(db, canonical_symbol)
    except InstrumentNotFound:
        raise HTTPException(404, {"code": "instrument_not_found", "message": "Instrument not found"}) from None
    except MappingUnavailable:
        raise HTTPException(409, {"code": "venue_mapping_unavailable", "message": "No active Quidax venue mapping"}) from None


@router.get("/tickers/{canonical_symbol:path}", response_model=TickerResponse)
def ticker(response: Response, target: TickerTarget = Depends(ticker_target),
           provider: MarketDataProvider = Depends(get_public_provider)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return fetch_ticker(target, provider)
    except ProviderUnavailable:
        raise HTTPException(503, {"code": "ticker_unavailable", "message": "Market price is temporarily unavailable"},
                            headers={"Cache-Control": "no-store"}) from None
