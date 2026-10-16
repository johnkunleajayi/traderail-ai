from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets.models import Asset


def list_assets(db: Session) -> list[Asset]:
    return list(db.scalars(select(Asset).order_by(Asset.symbol)).all())