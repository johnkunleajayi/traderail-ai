from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.venues.schemas import VenueInstrumentResponse, VenueResponse
from app.venues.service import find_venue, list_venue_instruments, list_venues

router = APIRouter(prefix="/venues", tags=["venues"])


@router.get("", response_model=list[VenueResponse])
def venues(db: Session = Depends(get_session)):
    return list_venues(db)


@router.get("/{code}", response_model=VenueResponse)
def venue(code: str = Path(min_length=1, max_length=32), db: Session = Depends(get_session)):
    result = find_venue(db, code)
    if result is None:
        raise HTTPException(404, "Venue not found")
    return result


@router.get("/{code}/instruments", response_model=list[VenueInstrumentResponse])
def venue_instruments(code: str = Path(min_length=1, max_length=32),
                      db: Session = Depends(get_session)):
    result = find_venue(db, code)
    if result is None:
        raise HTTPException(404, "Venue not found")
    return list_venue_instruments(db, result.id)