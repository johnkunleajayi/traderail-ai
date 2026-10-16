from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
from app.db.session import get_session
from app.market_data.binance import BinanceHistoricalProvider
from app.market_data.quidax import QuidaxPublicProvider
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_intelligence.engine import calculate_features
from app.market_intelligence.service import calculate_stored_features
from app.market_intelligence.types import DatasetContext
from test_market_intelligence import ID, AS_OF, candles, prices


@pytest.fixture
def api(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Intelligence must never contact providers")
    monkeypatch.setattr(BinanceHistoricalProvider, "fetch_candles", forbidden)
    monkeypatch.setattr(QuidaxPublicProvider, "fetch_ticker", forbidden)
    db = Mock()
    db.scalar.return_value = SimpleNamespace(id=ID, canonical_symbol="BTC/USDT")
    source = candles(prices(100))
    def selected(query):
        limit = query._limit_clause.value
        return SimpleNamespace(all=lambda: list(reversed(source[-limit:])))
    db.scalars.side_effect = selected
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        yield client, db, source
    for method in ("add", "add_all", "execute", "commit", "flush", "delete"):
        getattr(db, method).assert_not_called()


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT"])
@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h", "1d"])
def test_api_canonical_context_and_timeframes(api, symbol, timeframe):
    client, db, source = api
    source[:] = candles(prices(100), timeframe)
    db.scalar.return_value.canonical_symbol = symbol
    response = client.get(f"/api/market-intelligence/{symbol}", params={"timeframe":timeframe,"provider":"BINANCE"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["canonical_symbol"], body["provider"], body["timeframe"], body["candle_count"]) == (symbol,"BINANCE",timeframe,100)
    assert body["first_open_time"] == source[0].open_time.isoformat().replace("+00:00","Z")
    assert body["latest_close_time"] == source[-1].close_time.isoformat().replace("+00:00","Z")
    timestamps = [row["open_time"] for row in body["features"]]
    assert timestamps == sorted(timestamps)


def test_metadata_decimal_strings_nulls_and_exact_independent_engine_match(api):
    client, db, source = api
    response = client.get("/api/market-intelligence/BTC/USDT")
    assert response.status_code == 200
    body = response.json()
    expected = asdict(FEATURE_CONFIG); expected["ema_periods"] = list(expected["ema_periods"])
    assert body["feature_configuration"] == expected
    assert expected["engine_version"] == "1.0.0"
    assert expected["ema_periods"] == [20,50]
    assert (expected["rsi_period"],expected["atr_period"]) == (14,14)
    assert (expected["macd_fast_period"],expected["macd_slow_period"],expected["macd_signal_period"]) == (12,26,9)
    assert expected["realized_volatility_window"] == 20 and expected["annualization"] is False
    calculated = calculate_features(DatasetContext(ID,"BTC/USDT","BINANCE","1h"),source,as_of=AS_OF)
    for actual, domain in zip(body["features"], calculated):
        assert set(actual) == set(asdict(domain)) - {"canonical_symbol","provider","timeframe"}
        for key,value in asdict(domain).items():
            if isinstance(value,Decimal):
                assert actual[key] == format(value,"f")
            elif value is None:
                assert actual[key] is None
    assert body["features"][0]["ema_20"] is None
    assert body["features"][48]["ema_50"] is None
    assert body == client.get("/api/market-intelligence/BTC/USDT").json()


@pytest.mark.parametrize("limit", [1,20,50,1000])
def test_limit_uses_exact_latest_window_and_preserves_warmup(api,limit):
    client, db, source = api
    body = client.get("/api/market-intelligence/BTC/USDT",params={"limit":limit}).json()
    selected = source[-limit:]
    assert body["candle_count"] == len(selected)
    assert body["features"][0]["close"] == str(selected[0].close)
    assert body["features"][0]["ema_20"] is None
    assert body["features"][0]["simple_return"] is None
    query = db.scalars.call_args.args[0]
    assert query._limit_clause.value == limit and "DESC" in str(query)
    assert query.get_execution_options()["autoflush"] is False
    assert "close_time <=" not in str(query)
    assert db.scalar.call_args.args[0].get_execution_options()["autoflush"] is False


@pytest.mark.parametrize("params",[{"limit":0},{"limit":1001},{"limit":-1},{"limit":"x"},
    {"provider":"QUIDAX"},{"provider":"UNKNOWN"},{"timeframe":"5m"}])
def test_invalid_parameters_rejected_before_query(api,params):
    client,db,_=api
    assert client.get("/api/market-intelligence/BTC/USDT",params=params).status_code == 422
    db.scalar.assert_not_called(); db.scalars.assert_not_called()


def test_empty_and_unknown_canonical_instruments(api):
    client,db,source=api
    source.clear()
    response=client.get("/api/market-intelligence/BTC/USDT")
    assert response.status_code==409 and response.json()["detail"]["code"]=="stored_candles_unavailable"
    # Existing canonical NGN market with no stored history is not substituted.
    db.scalar.return_value.canonical_symbol="BTC/NGN"
    assert client.get("/api/market-intelligence/BTC/NGN").json()["detail"]["code"]=="stored_candles_unavailable"
    db.scalar.return_value=None
    response=client.get("/api/market-intelligence/UNKNOWN/USDT")
    assert response.status_code==404 and response.json()["detail"]["code"]=="instrument_not_found"


@pytest.mark.parametrize("defect,reason",[("gap","internal_gap"),("incomplete","incomplete_candle"),("malformed","impossible_ohlc")])
def test_invalid_stored_dataset_fails_closed(api,defect,reason):
    client,db,source=api
    if defect=="gap": source.pop(70)
    elif defect=="incomplete":
        now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
        shift=now-source[-1].open_time
        source[:]=[replace(row,open_time=row.open_time+shift,close_time=row.close_time+shift,retrieved_at=row.retrieved_at+shift) for row in source]
    else: source[50]=replace(source[50],high=Decimal("0.1"))
    response=client.get("/api/market-intelligence/BTC/USDT")
    assert response.status_code==409
    assert response.json()["detail"]["code"]=="invalid_candle_dataset"
    assert response.json()["detail"]["reason"]==reason
    assert "features" not in response.json()


def test_gap_is_not_hidden_inside_requested_window(api):
    client,_,source=api
    source.pop(-3)
    response=client.get("/api/market-intelligence/BTC/USDT?limit=5")
    assert response.status_code==409 and response.json()["detail"]["reason"]=="internal_gap"


def test_service_unbounded_interface_remains_reusable():
    db=Mock(); source=candles(prices(60))
    db.scalar.return_value=SimpleNamespace(id=ID,canonical_symbol="BTC/USDT")
    db.scalars.return_value.all.return_value=source
    rows=calculate_stored_features(db,"BTC/USDT","BINANCE","1h",as_of=AS_OF)
    assert len(rows)==60 and "LIMIT" not in str(db.scalars.call_args.args[0])


@pytest.mark.parametrize("limit",[0,1001,True,1.5])
def test_service_defends_limit(api,limit):
    _,db,_=api
    with pytest.raises(ValueError):
        calculate_stored_features(db,"BTC/USDT","BINANCE","1h",as_of=AS_OF,limit=limit)
    db.scalar.assert_not_called()

