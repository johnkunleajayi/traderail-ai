from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session
from app.db.session import get_session
from app.market_data.candle_schemas import Timeframe
from app.market_intelligence.config import DEFAULT_LIMIT, MAX_LIMIT, FEATURE_CONFIG
from app.market_intelligence.schemas import IntelligenceResponse
from app.market_intelligence.service import DatasetInstrumentNotFound, calculate_stored_features
from app.market_intelligence.types import InvalidCandleData

router = APIRouter(prefix="/market-intelligence", tags=["market-intelligence"])


@router.get("/{canonical_symbol:path}", response_model=IntelligenceResponse)
def intelligence(response: Response, canonical_symbol: str = Path(min_length=3, max_length=96),
                 timeframe: Timeframe = Query(default="1h"),
                 provider: Literal["BINANCE"] = Query(default="BINANCE"),
                 limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
                 db: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        rows = calculate_stored_features(db, canonical_symbol, provider, timeframe,
                                         as_of=datetime.now(timezone.utc), limit=limit)
    except DatasetInstrumentNotFound:
        raise HTTPException(404, {"code": "instrument_not_found", "message": "Instrument not found"}) from None
    except InvalidCandleData as error:
        raise HTTPException(409, {"code": "invalid_candle_dataset", "reason": error.code,
                                 "candle_index": error.index}, headers={"Cache-Control": "no-store"}) from None
    except ValueError:
        raise HTTPException(409, {"code": "measurement_unavailable", "message": "Dataset exceeds supported numeric range"}) from None
    if not rows:
        raise HTTPException(409, {"code": "stored_candles_unavailable", "message": "No stored candles for this dataset"},
                            headers={"Cache-Control": "no-store"})
    return IntelligenceResponse(canonical_symbol=canonical_symbol, provider=provider, timeframe=timeframe,
        candle_count=len(rows), first_open_time=rows[0].open_time, latest_close_time=rows[-1].close_time,
        feature_configuration=FEATURE_CONFIG, features=[asdict(row) for row in rows])
