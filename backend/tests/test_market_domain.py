import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, SmallInteger, UniqueConstraint
from sqlalchemy.dialects import postgresql

from app.assets.models import Asset
from app.assets.seed import ASSET_SEEDS, SEED_NAMESPACE
from app.core.config import Settings
from app.db.session import get_session
from app.instruments.models import Instrument
from app.instruments.seed import INSTRUMENT_SEEDS, seed_market_domain
from app.main import create_app


@pytest.fixture
def domain():
    """Mock SQLAlchemy boundaries; real Neon constraints are verified separately."""
    assets, instruments, statements = {}, {}, []
    db = Mock()

    def execute(statement):
        statements.append(statement)
        values = statement.compile().params
        if statement.table.name == "assets":
            if values["symbol"] not in assets:
                assets[values["symbol"]] = Asset(**values)
        elif statement.table.name == "instruments":
            if values["canonical_symbol"] not in instruments:
                base = next(item for item in assets.values() if item.id == values["base_asset_id"])
                quote = next(item for item in assets.values() if item.id == values["quote_asset_id"])
                instruments[values["canonical_symbol"]] = Instrument(**values, base_asset=base, quote_asset=quote)

    def scalars(statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = sorted(assets.values(), key=lambda item: item.symbol) if entity is Asset else sorted(
            instruments.values(), key=lambda item: item.canonical_symbol)
        return SimpleNamespace(all=lambda: rows)

    def scalar(statement):
        return instruments.get(statement.compile().params["canonical_symbol_1"])

    db.execute.side_effect = execute
    db.scalars.side_effect = scalars
    db.scalar.side_effect = scalar
    seed_market_domain(db)
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session] = lambda: db
    return SimpleNamespace(assets=assets, instruments=instruments, db=db,
                           statements=statements, client=TestClient(app))


def unique_columns(table):
    return {tuple(column.name for column in item.columns)
            for item in table.constraints if isinstance(item, UniqueConstraint)}


def test_asset_symbol_unique_and_timestamps():
    assert ("symbol",) in unique_columns(Asset.__table__)
    assert Asset.__table__.columns.created_at.type.timezone
    assert Asset.__table__.columns.updated_at.type.timezone
    assert Asset.__table__.columns.updated_at.onupdate is not None


def test_instrument_constraints_and_precision():
    table = Instrument.__table__
    assert ("canonical_symbol",) in unique_columns(table)
    assert ("base_asset_id", "quote_asset_id", "market_type") in unique_columns(table)
    checks = {str(item.sqltext) for item in table.constraints if isinstance(item, CheckConstraint)}
    assert "base_asset_id <> quote_asset_id" in checks
    assert "price_precision BETWEEN 0 AND 18" in checks
    assert "quantity_precision BETWEEN 0 AND 18" in checks
    assert isinstance(table.columns.price_precision.type, SmallInteger)
    assert isinstance(table.columns.quantity_precision.type, SmallInteger)
    assert table.columns.created_at.type.timezone and table.columns.updated_at.type.timezone
    assert table.columns.market_type.server_default.arg == "SPOT"
    # Extensible metadata, not a SPOT-only enum that forbids later market types.
    assert not any("market_type = 'SPOT'" in value for value in checks)
    assert {fk.target_fullname for fk in table.columns.base_asset_id.foreign_keys} == {"assets.id"}
    assert {fk.target_fullname for fk in table.columns.quote_asset_id.foreign_keys} == {"assets.id"}


def test_seed_has_only_requested_reference_data(domain):
    assert set(domain.assets) == {"BTC", "ETH", "USD"}
    assert set(domain.instruments) == {"BTC/USD", "ETH/USD"}
    assert {item.market_type for item in domain.instruments.values()} == {"SPOT"}
    assert domain.assets["BTC"].asset_type == domain.assets["ETH"].asset_type == "CRYPTO"
    assert domain.assets["USD"].asset_type == "FIAT"


def test_seed_ids_are_deterministic(domain):
    for seed in ASSET_SEEDS:
        assert seed["id"] == uuid.uuid5(SEED_NAMESPACE, "asset:" + seed["symbol"])
        assert domain.assets[seed["symbol"]].id == seed["id"]
    for seed in INSTRUMENT_SEEDS:
        assert seed["id"] == uuid.uuid5(SEED_NAMESPACE, "instrument:SPOT:" + seed["canonical_symbol"])
        assert domain.instruments[seed["canonical_symbol"]].id == seed["id"]


def test_repeated_seed_preserves_rows_ids_and_metadata(domain):
    original_ids = {key: value.id for key, value in domain.instruments.items()}
    domain.assets["BTC"].name = "Preserved reference name"
    domain.instruments["BTC/USD"].status = "disabled"
    domain.instruments["BTC/USD"].price_precision = 4
    seed_market_domain(domain.db)
    seed_market_domain(domain.db)
    assert len(domain.assets) == 3 and len(domain.instruments) == 2
    assert {key: value.id for key, value in domain.instruments.items()} == original_ids
    assert domain.assets["BTC"].name == "Preserved reference name"
    assert domain.instruments["BTC/USD"].status == "disabled"
    assert domain.instruments["BTC/USD"].price_precision == 4
    for statement in domain.statements:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT" in sql and "DO NOTHING" in sql and "DO UPDATE" not in sql
    domain.db.commit.assert_not_called()


def test_assets_listing_is_public_and_read_only(domain):
    before = len(domain.statements)
    response = domain.client.get("/api/assets")
    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["BTC", "ETH", "USD"]
    assert response.json()[0] == {"symbol": "BTC", "name": "Bitcoin", "asset_type": "CRYPTO", "status": "active"}
    assert len(domain.statements) == before
    domain.db.commit.assert_not_called()
    assert domain.client.post("/api/assets", json={}).status_code == 405


def test_instruments_listing(domain):
    response = domain.client.get("/api/instruments")
    assert response.status_code == 200
    assert [item["canonical_symbol"] for item in response.json()] == ["BTC/USD", "ETH/USD"]
    for item in response.json():
        assert item["market_type"] == "SPOT"
        assert item["quote_asset"]["symbol"] == "USD"
        assert isinstance(item["price_precision"], int)
        assert isinstance(item["quantity_precision"], int)
        assert set(item) == {"canonical_symbol", "base_asset", "quote_asset", "market_type", "status",
                             "price_precision", "quantity_precision"}


@pytest.mark.parametrize("symbol", ["BTC/USD", "ETH/USD", "BTC%2FUSD"])
def test_canonical_symbol_lookup(domain, symbol):
    response = domain.client.get("/api/instruments/" + symbol)
    assert response.status_code == 200
    result = response.json()
    assert result["canonical_symbol"] == symbol.replace("%2F", "/")
    assert result["base_asset"]["symbol"] + "/" + result["quote_asset"]["symbol"] == result["canonical_symbol"]
    assert domain.client.post("/api/instruments/" + symbol, json={}).status_code == 405


@pytest.mark.parametrize("symbol", ["UNKNOWN/USD", "btc/USD", "BTC/UNKNOWN"])
def test_unknown_instrument_404(domain, symbol):
    response = domain.client.get("/api/instruments/" + symbol)
    assert response.status_code == 404
    assert response.json() == {"detail": "Instrument not found"}


def test_no_venue_or_market_data_fields():
    assert set(Asset.__table__.columns.keys()) == {
        "id", "symbol", "name", "asset_type", "status", "created_at", "updated_at"}
    assert set(Instrument.__table__.columns.keys()) == {
        "id", "base_asset_id", "quote_asset_id", "canonical_symbol", "market_type",
        "status", "price_precision", "quantity_precision", "created_at", "updated_at"}
    assert set(seed["canonical_symbol"] for seed in INSTRUMENT_SEEDS) == {"BTC/USD", "ETH/USD"}


def test_database_errors_are_sanitized(domain):
    from sqlalchemy.exc import OperationalError
    domain.db.scalars.side_effect = OperationalError("private-query", {}, Exception("private-password"))
    response = domain.client.get("/api/instruments")
    assert response.status_code == 503
    assert "private-password" not in response.text and "private-query" not in response.text