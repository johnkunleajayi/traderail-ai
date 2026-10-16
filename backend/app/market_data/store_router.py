from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_session
from app.instruments.service import find_instrument
from app.market_data.binance import get_historical_provider
from app.market_data.candle_router import historical_target
from app.market_data.candle_schemas import CandleResponse, Timeframe
from app.market_data.candle_store import CandleIntegrityConflict, dataset_quality, stored_candles
from app.market_data.historical_mappings import HistoricalTarget
from app.market_data.historical_service import ingest_historical_candles
from app.market_data.provider import HistoricalMarketDataProvider, ProviderUnavailable
from app.market_data.store_schemas import DatasetQuality, IngestionResult
from app.market_data.service import InstrumentNotFound
from app.users.models import User

router = APIRouter(prefix="/market-data", tags=["market-data-store"])


def ingestion_permission(request: Request, user: User = Depends(get_current_user)):
    if request.headers.get("origin") != request.app.state.settings.frontend_url:
        raise HTTPException(403, "Untrusted request origin")
    return user


@router.post("/candle-ingestions/{canonical_symbol:path}", response_model=IngestionResult,
             dependencies=[Depends(ingestion_permission)])
def ingest(response: Response, timeframe: Timeframe = Query(default="1h"),
           limit: int = Query(default=100, ge=1, le=1000),
           target: HistoricalTarget = Depends(historical_target), db: Session = Depends(get_session),
           provider: HistoricalMarketDataProvider = Depends(get_historical_provider)):
    response.headers["Cache-Control"] = "no-store"
    try:
        result = ingest_historical_candles(db, target, timeframe, limit, provider)
        db.commit()
        return result
    except InstrumentNotFound:
        db.rollback()
        raise HTTPException(404, {"code": "instrument_not_found", "message": "Instrument not found"}) from None
    except CandleIntegrityConflict as error:
        db.rollback()
        raise HTTPException(409, {"code": "candle_integrity_conflict", "canonical_symbol": target.canonical_symbol,
            "provider": target.provider, "timeframe": timeframe, "open_time": error.open_time.isoformat(),
            "message": "Stored OHLCV differs; entire ingestion was rolled back"}) from None
    except ProviderUnavailable:
        db.rollback()
        raise HTTPException(503, {"code": "historical_data_unavailable", "message": "No candles were stored"}) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, {"code": "candle_storage_unavailable", "message": "Candle storage is temporarily unavailable"}) from None


@router.get("/stored-candles/{canonical_symbol:path}", response_model=list[CandleResponse])
def stored(response: Response, timeframe: Timeframe = Query(default="1h"),
           provider: Literal["BINANCE"] = Query(default="BINANCE"), limit: int = Query(default=100, ge=1, le=1000),
           target: HistoricalTarget = Depends(historical_target), db: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    instrument = find_instrument(db, target.canonical_symbol)
    return stored_candles(db, instrument.id, target.canonical_symbol, provider, timeframe, limit)


@router.get("/candle-quality/{canonical_symbol:path}", response_model=DatasetQuality)
def quality(response: Response, timeframe: Timeframe = Query(default="1h"),
            provider: Literal["BINANCE"] = Query(default="BINANCE"),
            target: HistoricalTarget = Depends(historical_target), db: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    instrument = find_instrument(db, target.canonical_symbol)
    return dataset_quality(db, instrument.id, target.canonical_symbol, provider, timeframe)
