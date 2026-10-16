import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.market_data.provider import MarketDataProvider, ProviderUnavailable
from app.market_data.quidax import QuidaxPublicProvider, get_public_provider, normalize_ticker
from app.market_data.service import (MappingUnavailable, InstrumentNotFound, TickerTarget,
                                     fetch_ticker, resolve_ticker_target)

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)
AT = 1792108800000


def payload(last="1234.567890123456789012", at=AT):
    # Synthetic values only for offline validation; never application fallbacks.
    return {"status": "success", "data": {"opaque-id": {"ticker": {"last": last}, "at": at}}}


def test_decimal_and_timestamp_normalization():
    result = normalize_ticker(payload(), "opaque-id", NOW)
    assert result.last_price == Decimal("1234.567890123456789012")
    assert isinstance(result.last_price, Decimal)
    assert result.source_timestamp == NOW
    assert result.retrieved_at == NOW
    assert json.loads(result.model_dump_json())["last_price"] == "1234.567890123456789012"


def test_timestamp_absent_is_not_invented():
    data = payload(); del data["data"]["opaque-id"]["at"]
    assert normalize_ticker(data, "opaque-id", NOW).source_timestamp is None


@pytest.mark.parametrize("last", [None, True, False, 1.1, "NaN", "Infinity", "-1", "0", "no-price", {}, "1e200", "1e-1000"])
def test_invalid_prices_rejected(last):
    with pytest.raises(ProviderUnavailable):
        normalize_ticker(payload(last), "opaque-id", NOW)


@pytest.mark.parametrize("at", [None, True, 0, -1, "1792108800000", 1.1, AT + 3600000])
def test_invalid_timestamps_rejected(at):
    with pytest.raises(ProviderUnavailable):
        normalize_ticker(payload(at=at), "opaque-id", NOW)


@pytest.mark.parametrize("data", [{}, [], {"status": "error"}, {"status": "success", "data": {}},
    {"status": "success", "data": {"different-market": {"ticker": {"last": "100"}}}},
    {"status": "success", "data": {"opaque-id": {"ticker": {}}}}])
def test_malformed_envelopes_rejected(data):
    with pytest.raises(ProviderUnavailable):
        normalize_ticker(data, "opaque-id", NOW)


def test_adapter_uses_exact_argument_and_decimal_json_without_credentials():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/opaque-id")
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(200, content=b'{"status":"success","data":{"opaque-id":{"ticker":{"last":1234.567890123456789012}}}}')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = QuidaxPublicProvider(client).fetch_ticker("opaque-id")
    assert result.last_price == Decimal("1234.567890123456789012")


@pytest.mark.parametrize("kind", ["network", "timeout", "status", "redirect", "html", "oversized", "nan"])
def test_provider_failures_have_no_fallback(kind):
    def handler(request):
        if kind == "network": raise httpx.ConnectError("private-provider-details", request=request)
        if kind == "timeout": raise httpx.ReadTimeout("private-provider-details", request=request)
        if kind == "status": return httpx.Response(429)
        if kind == "redirect": return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})
        if kind == "oversized": return httpx.Response(200, content=b" " * 65537)
        if kind == "nan": return httpx.Response(200, content=b'{"bad":NaN}')
        return httpx.Response(200, text="<html>error</html>")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderUnavailable):
            QuidaxPublicProvider(client).fetch_ticker("opaque-id")


def database(mapping=True):
    db = Mock()
    instrument = SimpleNamespace(id="canonical-id", canonical_symbol="TEST/QUOTE", status="active",
                                 quote_asset=SimpleNamespace(symbol="QUOTE"))
    db.scalar.side_effect = [instrument, SimpleNamespace(venue_symbol="Stored.Mixed-Identifier") if mapping else None]
    return db


def test_mapping_resolution_uses_stored_identifier_and_active_venue_filters():
    db = database()
    result = resolve_ticker_target(db, "TEST/QUOTE")
    assert result == TickerTarget("TEST/QUOTE", "QUOTE", "QUIDAX", "Stored.Mixed-Identifier")
    query = db.scalar.call_args_list[1].args[0]
    sql = str(query)
    assert "venue_instrument_mappings.instrument_id" in sql and "venues.code" in sql
    assert "venues.status" in sql and "venue_instrument_mappings.status" in sql
    assert "canonical-id" in query.compile().params.values()


@pytest.mark.parametrize("canonical", ["BTC/USD", "ETH/USD"])
def test_unmapped_canonical_does_not_fabricate_target(canonical):
    with pytest.raises(MappingUnavailable): resolve_ticker_target(database(False), canonical)


def test_unknown_canonical_stops_before_mapping():
    db = Mock(); db.scalar.return_value = None
    with pytest.raises(InstrumentNotFound): resolve_ticker_target(db, "MISSING/QUOTE")
    assert db.scalar.call_count == 1


def test_provider_protocol_accepts_another_implementation():
    class OtherProvider:
        def fetch_ticker(self, venue_symbol):
            assert venue_symbol == "Stored.Mixed-Identifier"
            return normalize_ticker(payload(), "opaque-id", NOW)
    provider: MarketDataProvider = OtherProvider()
    result = fetch_ticker(resolve_ticker_target(database(), "TEST/QUOTE"), provider)
    assert result.canonical_symbol == "TEST/QUOTE" and result.quote_currency == "QUOTE"
    assert result.venue == "QUIDAX" and result.last_price == Decimal("1234.567890123456789012")


def client(db, provider):
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_public_provider] = lambda: provider
    return TestClient(app)


def test_full_api_flow_preserves_decimal_contract_and_hides_raw_data():
    provider = Mock(); provider.fetch_ticker.return_value = normalize_ticker(payload(), "opaque-id", NOW)
    response = client(database(), provider).get("/api/market-data/tickers/TEST/QUOTE")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    provider.fetch_ticker.assert_called_once_with("Stored.Mixed-Identifier")
    assert response.json() == {"canonical_symbol": "TEST/QUOTE", "quote_currency": "QUOTE", "venue": "QUIDAX",
        "last_price": "1234.567890123456789012", "source_timestamp": "2026-10-16T00:00:00Z",
        "retrieved_at": "2026-10-16T00:00:00Z"}


@pytest.mark.parametrize("canonical", ["BTC/USD", "ETH/USD"])
def test_unmapped_api_returns_controlled_response_without_provider_call(canonical):
    provider = Mock()
    response = client(database(False), provider).get("/api/market-data/tickers/" + canonical)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "venue_mapping_unavailable"
    provider.fetch_ticker.assert_not_called()


def test_unknown_api_returns_404():
    db = Mock();db.scalar.return_value = None
    provider = Mock()
    assert client(db, provider).get("/api/market-data/tickers/MISSING/QUOTE").status_code == 404
    provider.fetch_ticker.assert_not_called()


def test_api_provider_failure_is_controlled_without_price_or_private_details():
    provider = Mock();provider.fetch_ticker.side_effect = ProviderUnavailable("private-details")
    response = client(database(), provider).get("/api/market-data/tickers/TEST/QUOTE")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ticker_unavailable"
    assert "last_price" not in response.text and "private-details" not in response.text


def test_canonical_to_stored_mapping_to_http_adapter_api():
    def handler(request):
        assert request.url.path.endswith("/Stored.Mixed-Identifier")
        return httpx.Response(200, content=b'{"status":"success","data":{"Stored.Mixed-Identifier":{"ticker":{"last":0.000000123456789012345}}}}')
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        response = client(database(), QuidaxPublicProvider(http)).get("/api/market-data/tickers/TEST/QUOTE")
    assert response.status_code == 200
    assert response.json()["last_price"] == "0.000000123456789012345"
    assert response.json()["canonical_symbol"] == "TEST/QUOTE"


def test_disabled_instrument_cannot_receive_a_ticker():
    db = database()
    instrument = SimpleNamespace(id="disabled", canonical_symbol="TEST/QUOTE", status="disabled",
                                 quote_asset=SimpleNamespace(symbol="QUOTE"))
    db.scalar.side_effect = [instrument, SimpleNamespace(venue_symbol="anything")]
    with pytest.raises(MappingUnavailable): resolve_ticker_target(db, "TEST/QUOTE")


def test_normalized_contract_rejects_float_and_naive_time():
    from pydantic import ValidationError
    from app.market_data.schemas import ProviderTicker
    with pytest.raises(ValidationError):
        ProviderTicker(last_price=1.1, source_timestamp=None, retrieved_at=NOW)
    with pytest.raises(ValidationError):
        ProviderTicker(last_price=Decimal("1"), source_timestamp=None, retrieved_at=datetime(2026, 10, 16))
