from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.assets.models import Asset
from app.assets.seed import ASSET_SEEDS, SEED_NAMESPACE, seed_assets
from app.instruments.models import Instrument

INSTRUMENT_SEEDS = tuple(
    {
        "id": uuid5(SEED_NAMESPACE, "instrument:SPOT:" + base + "/USD"),
        "base_symbol": base, "quote_symbol": "USD", "canonical_symbol": base + "/USD",
        "market_type": "SPOT", "status": "active", "price_precision": 2, "quantity_precision": 8,
    }
    for base in ("BTC", "ETH")
)


def seed_market_domain(db: Session) -> None:
    """Caller owns the transaction. Existing reference metadata is never overwritten."""
    seed_assets(db)
    assets = {asset.symbol: asset.id for asset in db.scalars(
        select(Asset).where(Asset.symbol.in_([item["symbol"] for item in ASSET_SEEDS]))
    ).all()}
    for seed in INSTRUMENT_SEEDS:
        values = {key: value for key, value in seed.items() if key not in ("base_symbol", "quote_symbol")}
        values["base_asset_id"] = assets[seed["base_symbol"]]
        values["quote_asset_id"] = assets[seed["quote_symbol"]]
        db.execute(insert(Instrument).values(**values).on_conflict_do_nothing(
            index_elements=[Instrument.canonical_symbol]))


def main():
    from app.core.config import get_settings
    from app.db.session import get_engine
    from sqlalchemy.exc import SQLAlchemyError
    try:
        with Session(get_engine(str(get_settings().database_url))) as db, db.begin():
            seed_market_domain(db)
    except SQLAlchemyError:
        # Connection errors can contain private database URLs/passwords.
        raise SystemExit("Market reference seeding failed; database details suppressed.") from None
    print("Market reference seeds applied safely.")


if __name__ == "__main__":
    main()