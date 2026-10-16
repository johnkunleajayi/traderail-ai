from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from app.db.session import get_session
from app.market_data.binance import BinanceHistoricalProvider
from app.market_data.quidax import QuidaxPublicProvider
from app.market_data import store_router
from app.market_intelligence.engine import calculate_features
from app.market_intelligence.types import DatasetContext
from app.market_regimes import service
from app.market_regimes.config import REGIME_CONFIG
from app.market_regimes.engine import classify_regimes
from app.market_regimes.schemas import decimal_strings
from test_market_intelligence import ID, AS_OF, candles, prices


@pytest.fixture
def api(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only regime API must not call providers or ingestion")
    monkeypatch.setattr(BinanceHistoricalProvider,"fetch_candles",forbidden)
    monkeypatch.setattr(QuidaxPublicProvider,"fetch_ticker",forbidden)
    monkeypatch.setattr(store_router,"ingest_historical_candles",forbidden)
    db=Mock()
    instrument=SimpleNamespace(id=ID,canonical_symbol="BTC/USDT",base_asset=SimpleNamespace(symbol="BTC"),quote_asset=SimpleNamespace(symbol="USDT"))
    def resolved(query):
        assert query.is_select and query.get_execution_options()["autoflush"] is False
        return instrument if instrument.canonical_symbol is not None else None
    db.scalar.side_effect=resolved
    source=candles(prices(100))
    def selected(query):
        assert query.is_select and query.get_execution_options()["autoflush"] is False
        assert "ORDER BY market_candles.open_time DESC" in str(query)
        assert "close_time <=" not in str(query)
        limit=query._limit_clause.value
        return SimpleNamespace(all=lambda:list(reversed(source[-limit:])))
    db.scalars.side_effect=selected
    app=create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session]=lambda:db
    with TestClient(app) as client:
        yield client,db,source,instrument
    for method in ("add","add_all","execute","flush","commit","rollback","delete","merge","bulk_save_objects"):
        getattr(db,method).assert_not_called()


@pytest.mark.parametrize("base",["BTC","ETH"])
@pytest.mark.parametrize("timeframe",["15m","1h","4h","1d"])
def test_canonical_route_context_chronology_and_latest_row(api,base,timeframe):
    client,db,source,instrument=api
    instrument.canonical_symbol=f"{base}/USDT";instrument.base_asset.symbol=base
    source[:]=candles(prices(100),timeframe)
    result=client.get(f"/api/market-regimes/{base}/USDT",params={"timeframe":timeframe,"provider":"BINANCE","limit":100})
    assert result.status_code==200 and result.headers["cache-control"]=="no-store"
    body=result.json()
    assert (body["canonical_symbol"],body["base_asset"],body["quote_asset"],body["provider"],body["timeframe"])==(f"{base}/USDT",base,"USDT","BINANCE",timeframe)
    assert body["candle_count"]==len(body["regimes"])==100
    assert body["regime_engine_version"]==body["feature_engine_version"]=="1.0.0"
    assert body["regime_configuration"]==decimal_strings(asdict(REGIME_CONFIG))
    assert body["latest_regime"]==body["regimes"][-1]
    assert body["dataset_start"]==body["regimes"][0]["open_time"]
    assert body["dataset_end"]==body["regimes"][-1]["close_time"]
    times=[row["open_time"] for row in body["regimes"]]
    assert times==sorted(times)


def test_exact_decimal_evidence_configuration_and_existing_engines_are_reused(api):
    client,_,source,_=api
    original=[asdict(row) for row in source]
    result=client.get("/api/market-regimes/BTC/USDT").json()
    measured=calculate_features(DatasetContext(ID,"BTC/USDT","BINANCE","1h"),source,as_of=AS_OF)
    expected=classify_regimes(measured,as_of=AS_OF)
    for actual,domain in zip(result["regimes"],expected):
        assert actual["regime"]==domain.regime.value
        assert actual["candidate_regime"]==domain.evidence.candidate.value
        assert actual["evidence"]==decimal_strings(asdict(domain.evidence)) | {"reason_codes":list(domain.evidence.reason_codes)}
        for field,value in asdict(domain.evidence).items():
            if isinstance(value,Decimal): assert actual["evidence"][field]==format(value,"f")
        assert actual["reason_codes"]==list(domain.evidence.reason_codes)
        assert actual["confirmation"]["count"]==domain.evidence.confirmation_count
        assert actual["confirmation"]["required"]==domain.evidence.confirmation_required
        assert actual["confirmation"]["retained_previous"]==domain.evidence.retained_previous
    assert [asdict(row) for row in source]==original
    assert result["regime_configuration"]["trend_spread"]=="0.0015"
    assert result==client.get("/api/market-regimes/BTC/USDT").json()


@pytest.mark.parametrize("limit",[1,50,55,100,1000])
def test_requested_boundary_warmup_and_no_hidden_history(api,limit):
    client,db,source,_=api
    body=client.get("/api/market-regimes/BTC/USDT",params={"limit":limit}).json()
    assert body["candle_count"]==min(limit,len(source))
    assert body["regimes"][0]["regime"]=="UNKNOWN"
    assert body["regimes"][0]["reason_codes"]==["WARMUP_REQUIRED","INSUFFICIENT_EVIDENCE"]
    assert body["regimes"][0]["evidence"]["normalized_ema_spread"] is None
    assert db.scalars.call_args.args[0]._limit_clause.value==limit
    if limit<=50: assert all(row["regime"]=="UNKNOWN" for row in body["regimes"])


def test_default_limit_matches_intelligence_convention(api):
    client,db,_,_=api
    assert client.get("/api/market-regimes/BTC/USDT").status_code==200
    assert db.scalars.call_args.args[0]._limit_clause.value==100


@pytest.mark.parametrize("params",[{"limit":0},{"limit":1001},{"limit":-1},{"limit":"bad"},{"limit":"1.5"},
    {"provider":"QUIDAX"},{"provider":"UNKNOWN"},{"timeframe":"5m"}])
def test_invalid_queries_422_without_database_access(api,params):
    client,db,_,_=api
    assert client.get("/api/market-regimes/BTC/USDT",params=params).status_code==422
    db.scalar.assert_not_called();db.scalars.assert_not_called()


def test_unknown_404_and_empty_existing_instrument_409(api):
    client,db,source,instrument=api
    instrument.canonical_symbol=None
    result=client.get("/api/market-regimes/UNKNOWN/USDT")
    assert result.status_code==404 and result.json()["detail"]["code"]=="instrument_not_found"
    db.scalars.assert_not_called()
    instrument.canonical_symbol="BTC/USDT";source.clear()
    result=client.get("/api/market-regimes/BTC/USDT")
    assert result.status_code==409 and result.json()["detail"]["code"]=="stored_candles_unavailable"
    assert result.headers["cache-control"]=="no-store"
    instrument.canonical_symbol="BTC/NGN";instrument.quote_asset.symbol="NGN"
    result=client.get("/api/market-regimes/BTC/NGN")
    assert result.status_code==409  # Never substitute another quote market.


@pytest.mark.parametrize("problem,reason",[("gap","internal_gap"),("duplicate","duplicate_timestamp"),
    ("incomplete","incomplete_candle"),("ohlc","impossible_ohlc")])
def test_invalid_stored_candles_cannot_become_fabricated_regime_rows(api,problem,reason):
    client,_,source,_=api
    if problem=="gap": source.pop(70)
    elif problem=="duplicate": source[-1]=source[-2]
    elif problem=="ohlc": source[70]=replace(source[70],high=Decimal("0.1"))
    else:
        boundary=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
        shift=boundary-source[-1].open_time
        source[:]=[replace(row,open_time=row.open_time+shift,close_time=row.close_time+shift,retrieved_at=row.retrieved_at+shift) for row in source]
    response=client.get("/api/market-regimes/BTC/USDT")
    assert response.status_code==409
    assert response.json()["detail"]["code"]=="invalid_candle_dataset"
    assert response.json()["detail"]["reason"]==reason
    assert "regimes" not in response.json()


def test_engine_unsafe_is_200_and_retains_safe_prefix_and_quarantined_suffix(api,monkeypatch):
    client,_,source,_=api
    measured=calculate_features(DatasetContext(ID,"BTC/USDT","BINANCE","1h"),source,as_of=AS_OF)
    measured[70]=replace(measured[70],rsi_14=Decimal(101))
    monkeypatch.setattr(service,"calculate_stored_features",lambda *args,**kwargs:measured)
    response=client.get("/api/market-regimes/BTC/USDT")
    assert response.status_code==200
    body=response.json()
    assert body["regimes"][69]["regime"]!="UNSAFE"
    assert body["regimes"][70]["regime"]=="UNSAFE"
    assert body["regimes"][70]["reason_codes"]==["DATA_UNSAFE","INVALID_RSI"]
    assert all(row["regime"]=="UNSAFE" for row in body["regimes"][70:])
    assert body["latest_regime"]==body["regimes"][-1]
    assert body==client.get("/api/market-regimes/BTC/USDT").json()


def test_service_uses_single_completion_boundary_and_one_classification(api,monkeypatch):
    client,_,_,_=api
    original_features=service.calculate_stored_features;original_classify=service.classify_regimes
    feature_call=Mock(wraps=original_features);regime_call=Mock(wraps=original_classify)
    monkeypatch.setattr(service,"calculate_stored_features",feature_call)
    monkeypatch.setattr(service,"classify_regimes",regime_call)
    assert client.get("/api/market-regimes/BTC/USDT?limit=55").status_code==200
    assert feature_call.call_count==regime_call.call_count==1
    assert feature_call.call_args.kwargs["as_of"]==regime_call.call_args.kwargs["as_of"]
    assert feature_call.call_args.kwargs["limit"]==55
    assert len(regime_call.call_args.args[0])==55


def test_numeric_intelligence_failure_is_controlled_without_invented_rows(api,monkeypatch):
    client,_,_,_=api
    def unavailable(*args,**kwargs): raise ValueError("Numeric range")
    monkeypatch.setattr(service,"calculate_stored_features",unavailable)
    response=client.get("/api/market-regimes/BTC/USDT")
    assert response.status_code==409 and response.json()["detail"]["code"]=="measurement_unavailable"
    assert "regimes" not in response.json()
