from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.assets.schemas import AssetResponse
from app.assets.service import list_assets
from app.db.session import get_session

router = APIRouter(tags=["assets"])


@router.get("/assets", response_model=list[AssetResponse])
def assets(db: Session = Depends(get_session)):
    return list_assets(db)