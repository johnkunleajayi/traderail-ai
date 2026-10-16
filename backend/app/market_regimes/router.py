from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session
from app.db.session import get_session
from app.market_data.candle_schemas import Timeframe
from app.market_intelligence.config import DEFAULT_LIMIT, MAX_LIMIT
from app.market_intelligence.service import DatasetInstrumentNotFound
from app.market_intelligence.types import InvalidCandleData
from app.market_regimes.schemas import RegimeResponse, RegimeRowResponse
from app.market_regimes.service import IntelligenceUnavailable, StoredRegimesUnavailable, read_stored_regimes

router = APIRouter(prefix="/market-regimes", tags=["market-regimes"])


@router.get("/{base}/{quote}", response_model=RegimeResponse)
def regimes(response: Response,
            base: str = Path(pattern=r"^[A-Z][A-Z0-9]{0,15}$"),
            quote: str = Path(pattern=r"^[A-Z][A-Z0-9]{0,15}$"),
            timeframe: Timeframe = Query(default="1h"),
            provider: Literal["BINANCE"] = Query(default="BINANCE"),
            limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
            db: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    headers = {"Cache-Control": "no-store"}
    try:
        dataset = read_stored_regimes(db, f"{base}/{quote}", provider, timeframe,
                                     limit=limit, as_of=datetime.now(timezone.utc))
    except DatasetInstrumentNotFound:
        raise HTTPException(404, {"code": "instrument_not_found", "message": "Instrument not found"}, headers=headers) from None
    except StoredRegimesUnavailable:
        raise HTTPException(409, {"code": "stored_candles_unavailable", "message": "No stored candles for this dataset"}, headers=headers) from None
    except InvalidCandleData as error:
        raise HTTPException(409, {"code": "invalid_candle_dataset", "reason": error.code,
                                 "candle_index": error.index}, headers=headers) from None
    except IntelligenceUnavailable:
        raise HTTPException(409, {"code": "measurement_unavailable", "message": "Dataset exceeds supported numeric range"}, headers=headers) from None
    rows = [RegimeRowResponse.from_result(row) for row in dataset.rows]
    latest = dataset.rows[-1]
    return RegimeResponse(canonical_symbol=dataset.canonical_symbol, base_asset=dataset.base_asset,
        quote_asset=dataset.quote_asset, provider=dataset.provider, timeframe=dataset.timeframe,
        candle_count=len(rows), dataset_start=rows[0].open_time, dataset_end=rows[-1].close_time,
        regime_engine_version=latest.configuration.engine_version, feature_engine_version=latest.feature_engine_version,
        regime_configuration=latest.configuration, regimes=rows, latest_regime=rows[-1])
