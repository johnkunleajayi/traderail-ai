from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.instruments.schemas import InstrumentResponse
from app.instruments.service import find_instrument, list_instruments

router = APIRouter(tags=["instruments"])


@router.get("/instruments", response_model=list[InstrumentResponse])
def instruments(db: Session = Depends(get_session)):
    return list_instruments(db)


# The path converter preserves the slash in a canonical symbol such as BTC/USD.
@router.get("/instruments/{canonical_symbol:path}", response_model=InstrumentResponse)
def instrument(canonical_symbol: str = Path(min_length=3, max_length=96),
               db: Session = Depends(get_session)):
    result = find_instrument(db, canonical_symbol)
    if result is None:
        raise HTTPException(404, "Instrument not found")
    return result