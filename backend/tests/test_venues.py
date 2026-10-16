import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql

from app.assets.models import Asset
from app.assets.seed import ASSET_SEEDS
from app.core.config import Settings
from app.db.session import get_session
from app.instruments.models import Instrument
from app.instruments.seed import INSTRUMENT_SEEDS
from app.main import create_app
from app.venues.models import Venue, VenueInstrumentMapping
from app.venues.seed import SEED_NAMESPACE, VENUE_SEEDS, VERIFIED_MAPPING_SEEDS, seed_venues_and_mappings


@pytest.fixture
def venues():
    db = Mock()
    rows, mappings, statements = {}, [], []
    assets = {row["symbol"]: Asset(**row) for row in ASSET_SEEDS}
    instruments = {}
    for seed in INSTRUMENT_SEEDS:
        base, quote = assets[seed["base_symbol"]], assets[seed["quote_symbol"]]
        values = {k: v for k, v in seed.items() if k not in ("base_symbol", "quote_symbol")}
        instruments[seed["canonical_symbol"]] = Instrument(**values, base_asset_id=base.id,
            quote_asset_id=quote.id, base_asset=base, quote_asset=quote)

    def execute(statement):
        statements.append(statement)
        values = statement.compile().params
        if statement.table.name == "venues" and values["code"] not in rows:
            rows[values["code"]] = Venue(**values)

        elif statement.table.name == "venue_instrument_mappings":
            if not any(row.venue_id == values["venue_id"] and row.instrument_id == values["instrument_id"] for row in mappings):
                venue = next(row for row in rows.values() if row.id == values["venue_id"])
                instrument = next(row for row in instruments.values() if row.id == values["instrument_id"])
                mappings.append(VenueInstrumentMapping(**values, venue=venue, instrument=instrument))

    def scalar(statement):
        params = statement.compile().params
        return rows.get(params["code_1"]) if "code_1" in params else instruments.get(params["canonical_symbol_1"])

    def scalars(statement):
        if statement.column_descriptions[0]["entity"] is Venue:
            values = sorted(rows.values(), key=lambda item: item.code)
        else:
            venue_id = statement.compile().params["venue_id_1"]
            values = sorted((item for item in mappings if item.venue_id == venue_id),
                            key=lambda item: item.venue_symbol)
        return SimpleNamespace(all=lambda: values)

    db.execute.side_effect = execute
    db.scalar.side_effect = scalar
    db.scalars.side_effect = scalars
    seed_venues_and_mappings(db)
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session] = lambda: db
    return SimpleNamespace(db=db, rows=rows, mappings=mappings, statements=statements,
                           client=TestClient(app))


def unique_columns(table):
    return {tuple(column.name for column in constraint.columns)
            for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}


def test_venue_unique_code_and_seed(venues):
    assert ("code",) in unique_columns(Venue.__table__)
    assert set(venues.rows) == {"QUIDAX"}
    row = venues.rows["QUIDAX"]
    assert row.name == "Quidax" and row.venue_type == "CRYPTO_EXCHANGE"
    assert row.id == uuid.uuid5(SEED_NAMESPACE, "venue:QUIDAX")


def test_mapping_unique_keys_and_links():
    table = VenueInstrumentMapping.__table__
    assert ("venue_id", "instrument_id") in unique_columns(table)
    assert ("venue_id", "venue_symbol") in unique_columns(table)
    assert {fk.target_fullname for fk in table.columns.venue_id.foreign_keys} == {"venues.id"}
    assert {fk.target_fullname for fk in table.columns.instrument_id.foreign_keys} == {"instruments.id"}
    for model in (Venue, VenueInstrumentMapping):
        assert model.__table__.columns.created_at.type.timezone
        assert model.__table__.columns.updated_at.type.timezone
        assert model.__table__.columns.updated_at.onupdate is not None


def test_seed_is_deterministic_idempotent_and_preserves_metadata(venues):
    original_id = venues.rows["QUIDAX"].id
    venues.rows["QUIDAX"].name = "Preserved display metadata"
    venues.rows["QUIDAX"].status = "disabled"
    seed_venues_and_mappings(venues.db)
    seed_venues_and_mappings(venues.db)
    assert len(venues.rows) == 1
    assert venues.rows["QUIDAX"].id == original_id == VENUE_SEEDS[0]["id"]
    assert venues.rows["QUIDAX"].name == "Preserved display metadata"
    assert venues.rows["QUIDAX"].status == "disabled"
    for statement in venues.statements:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT" in sql and "DO NOTHING" in sql
        assert "DO UPDATE" not in sql
    venues.db.commit.assert_not_called()


def test_only_verified_exact_markets_are_seeded(venues):
    expected = {"BTC/USDT": "btcusdt", "ETH/USDT": "ethusdt", "BTC/NGN": "btcngn",
                "ETH/NGN": "ethngn", "USDT/NGN": "usdtngn"}
    assert {row.instrument.canonical_symbol: row.venue_symbol for row in venues.mappings} == expected
    assert all(row.instrument.canonical_symbol not in ("BTC/USD", "ETH/USD") for row in venues.mappings)
    original = [(row.id, row.venue_symbol) for row in venues.mappings]
    venues.mappings[0].status = "disabled"
    seed_venues_and_mappings(venues.db)
    seed_venues_and_mappings(venues.db)
    assert [(row.id, row.venue_symbol) for row in venues.mappings] == original
    assert venues.mappings[0].status == "disabled"


def test_public_venue_and_verified_mapping_api(venues):
    expected = {"code": "QUIDAX", "name": "Quidax", "venue_type": "CRYPTO_EXCHANGE", "status": "active"}
    assert venues.client.get("/api/venues").json() == [expected]
    response = venues.client.get("/api/venues/QUIDAX")
    assert response.status_code == 200 and response.json() == expected
    mappings = venues.client.get("/api/venues/QUIDAX/instruments")
    assert mappings.status_code == 200
    assert {item["instrument"]["canonical_symbol"]: item["venue_symbol"] for item in mappings.json()} == {
        canonical: symbol for _, canonical, symbol in VERIFIED_MAPPING_SEEDS}
    assert all(set(item) == {"venue", "instrument", "venue_symbol", "status"} for item in mappings.json())
    venues.db.commit.assert_not_called()


@pytest.mark.parametrize("path", ["/api/venues/UNKNOWN", "/api/venues/UNKNOWN/instruments"])
def test_unknown_venue_is_404(venues, path):
    response = venues.client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Venue not found"}


def test_mapping_api_returns_exact_symbol_and_canonical_reference(venues):
    # Synthetic venue fixture only, never a claimed Quidax mapping.
    other = Venue(id=uuid.uuid4(), code="TEST_ALPHA", name="Synthetic venue",
                  venue_type="CRYPTO_EXCHANGE", status="active")
    venues.rows[other.code] = other
    assets = {row["symbol"]: Asset(**row) for row in ASSET_SEEDS}
    seed = INSTRUMENT_SEEDS[0]
    instrument = Instrument(
        id=seed["id"], base_asset_id=assets["BTC"].id, quote_asset_id=assets["USD"].id,
        canonical_symbol=seed["canonical_symbol"], market_type=seed["market_type"],
        status=seed["status"], price_precision=seed["price_precision"],
        quantity_precision=seed["quantity_precision"], base_asset=assets["BTC"], quote_asset=assets["USD"],
    )
    symbol = "Mixed.Case:Base/Quote"
    venues.mappings.append(VenueInstrumentMapping(
        id=uuid.uuid4(), venue_id=other.id, instrument_id=instrument.id,
        venue_symbol=symbol, status="active", venue=other, instrument=instrument,
    ))
    response = venues.client.get("/api/venues/TEST_ALPHA/instruments")
    assert response.status_code == 200
    item = response.json()[0]
    assert item["venue_symbol"] == symbol
    assert item["venue"]["code"] == "TEST_ALPHA"
    assert item["instrument"]["canonical_symbol"] == "BTC/USD"
    assert set(item) == {"venue", "instrument", "venue_symbol", "status"}
    assert len(venues.client.get("/api/venues/QUIDAX/instruments").json()) == len(VERIFIED_MAPPING_SEEDS)


def test_canonical_model_stays_venue_neutral():
    assert set(Instrument.__table__.columns.keys()) == {
        "id", "base_asset_id", "quote_asset_id", "canonical_symbol", "market_type",
        "status", "price_precision", "quantity_precision", "created_at", "updated_at",
    }
    assert set(Venue.__table__.columns.keys()) == {
        "id", "code", "name", "venue_type", "status", "created_at", "updated_at",
    }
    assert set(VenueInstrumentMapping.__table__.columns.keys()) == {
        "id", "venue_id", "instrument_id", "venue_symbol", "status", "created_at", "updated_at",
    }


@pytest.mark.parametrize("path", ["/api/venues", "/api/venues/QUIDAX", "/api/venues/QUIDAX/instruments"])
def test_no_write_api(venues, path):
    assert venues.client.post(path, json={}).status_code == 405
    assert venues.client.delete(path).status_code == 405


def test_database_errors_are_sanitized(venues):
    from sqlalchemy.exc import OperationalError
    venues.db.scalars.side_effect = OperationalError("private-query", {}, Exception("private-password"))
    response = venues.client.get("/api/venues")
    assert response.status_code == 503
    assert "private-password" not in response.text and "private-query" not in response.text