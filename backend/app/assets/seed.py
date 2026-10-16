from uuid import UUID, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.assets.models import Asset

# Stable namespace: seed IDs are identical across runs and databases.
SEED_NAMESPACE = UUID("52eca53b-1223-481d-a52c-896730352a38")
ASSET_SEEDS = (
    {"id": uuid5(SEED_NAMESPACE, "asset:BTC"), "symbol": "BTC", "name": "Bitcoin", "asset_type": "CRYPTO", "status": "active"},
    {"id": uuid5(SEED_NAMESPACE, "asset:ETH"), "symbol": "ETH", "name": "Ethereum", "asset_type": "CRYPTO", "status": "active"},
    {"id": uuid5(SEED_NAMESPACE, "asset:USD"), "symbol": "USD", "name": "US Dollar", "asset_type": "FIAT", "status": "active"},
)


def seed_assets(db: Session) -> None:
    for values in ASSET_SEEDS:
        db.execute(insert(Asset).values(**values).on_conflict_do_nothing(index_elements=[Asset.symbol]))