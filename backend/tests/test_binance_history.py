import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.market_data.binance import (BinanceHistoricalProvider, INTERVALS, KLINES_URL,
                                     get_historical_provider, normalize_klines)
from app.market_data.candle_schemas import ProviderCandle
from app.market_data.historical_mappings import (HISTORICAL_SYMBOLS, HistoricalMappingUnavailable,
                                                resolve_historical_target)
from app.market_data.historical_service import fetch_historical_candles
from app.market_data.provider import HistoricalMarketDataProvider, ProviderUnavailable
from app.market_data.service import InstrumentNotFound

START = 1792022400000
NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)
DURATIONS = {"15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}


def row(timeframe="1h", start=START):
    # Offline fixture in Binance's documented 12-position format; not fallback data.
    return [start, "10.12345678901234567890123456789", "12.00000000", "9.00000000",
            "11.00000000", "123.12345678901234567890123456789", start + DURATIONS[timeframe] - 1,
            "1000.00", 42, "60.00", "500.00", "0"]


@pytest.mark.parametrize("timeframe", DURATIONS)
def test_documented_order_decimal_utc_and_all_timeframes(timeframe):
    result = normalize_klines([row(timeframe)], timeframe, 1, NOW)[0]
    assert result.open == Decimal("10.12345678901234567890123456789")
    assert result.high == Decimal("12") and result.low == Decimal("9") and result.close == Decimal("11")
    assert result.volume == Decimal("123.12345678901234567890123456789")
    assert all(isinstance(getattr(result, k), Decimal) for k in ("open", "high", "low", "close", "volume"))
    assert result.open_time == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert result.close_time == result.open_time + timedelta(milliseconds=DURATIONS[timeframe] - 1)
    assert result.retrieved_at == NOW
    assert json.loads(result.model_dump_json())["open"] == row()[1]


@pytest.mark.parametrize("timeframe", DURATIONS)
def test_adapter_interval_mapping_and_completed_utc_boundary(timeframe):
    def handler(request):
        assert request.url.host == "data-api.binance.vision"
        assert request.url.path == "/api/v3/klines"
        assert request.method == "GET"
        assert request.url.params["symbol"] == "OpaqueSymbol"
        assert request.url.params["interval"] == timeframe
        assert request.url.params["timeZone"] == "0"
        assert request.url.params["limit"] == "1"
        assert (int(request.url.params["endTime"]) + 1) % DURATIONS[timeframe] == 0
        assert "authorization" not in request.headers and "x-mbx-apikey" not in request.headers
        assert "signature" not in request.url.params
        return httpx.Response(200, json=[row(timeframe)])
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = BinanceHistoricalProvider(http).fetch_candles("OpaqueSymbol", timeframe, 1)
    assert len(result) == 1
    assert set(INTERVALS) == set(DURATIONS)


def test_chronological_normalization_without_value_changes():
    later = row(start=START + DURATIONS["1h"])
    result = normalize_klines([later, row()], "1h", 2, NOW)
    assert [c.open_time for c in result] == sorted(c.open_time for c in result)
    assert result[0].open == result[1].open == Decimal(row()[1])


def test_duplicate_rejected_not_silently_deduplicated():
    with pytest.raises(ProviderUnavailable): normalize_klines([row(), row()], "1h", 2, NOW)


@pytest.mark.parametrize("index,value", [(1,None), (1,True), (1,10.1), (1,"NaN"), (2,"Infinity"),
    (3,"-1"), (4,"0"), (5,"-1"), (5,"bad"), (1,"1e200"), (1,"1e1000000"), (1,"1e-1000")])
def test_invalid_financial_values_reject_whole_batch(index,value):
    bad=row();bad[index]=value
    with pytest.raises(ProviderUnavailable): normalize_klines([bad], "1h", 1, NOW)


@pytest.mark.parametrize("patch", [{2:"8"}, {2:"10.5"}, {3:"10.5"}, {3:"11.5"}, {2:"8",3:"13"}])
def test_impossible_ohlc_rejected(patch):
    bad=row()
    for k,v in patch.items(): bad[k]=v
    with pytest.raises(ProviderUnavailable): normalize_klines([bad], "1h", 1, NOW)


@pytest.mark.parametrize("index,value", [(0,True),(0,None),(0,"1792022400000"),(0,-1),(0,START+1),
                                        (6,START-1),(6,True),(6,START+3600000)])
def test_invalid_timestamps(index,value):
    bad=row();bad[index]=value
    with pytest.raises(ProviderUnavailable): normalize_klines([bad], "1h", 1, NOW)


def test_future_unfinished_candles_rejected():
    future=row(start=START+86400000)
    with pytest.raises(ProviderUnavailable): normalize_klines([future], "1h", 1, NOW)


@pytest.mark.parametrize("data", [[], {}, {"code":-1}, [None], [[]], [row()[:-1]], [row()+["extra"]]])
def test_malformed_or_empty_response_has_no_fallback(data):
    with pytest.raises(ProviderUnavailable): normalize_klines(data,"1h",100,NOW)


@pytest.mark.parametrize("timeframe,limit", [("5m",1),("1H",1),("1h",0),("1h",1001),("1h",True)])
def test_unsupported_request_rejected_without_network(timeframe,limit):
    handler=Mock()
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderUnavailable): BinanceHistoricalProvider(http).fetch_candles("any",timeframe,limit)
    handler.assert_not_called()


@pytest.mark.parametrize("kind", ["network","timeout","rate_limit","http","redirect","malformed","oversized"])
def test_provider_failures_sanitized_without_fallback(kind):
    def handler(request):
        if kind=="network": raise httpx.ConnectError("private-details",request=request)
        if kind=="timeout": raise httpx.ReadTimeout("private-details",request=request)
        if kind=="rate_limit": return httpx.Response(429)
        if kind=="http": return httpx.Response(500)
        if kind=="redirect": return httpx.Response(302,headers={"Location":"https://untrusted.invalid"})
        if kind=="oversized": return httpx.Response(200,content=b" "*1048577)
        return httpx.Response(200,text="bad-json")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderUnavailable): BinanceHistoricalProvider(http).fetch_candles("any","1h",1)


def db_for(canonical,status="active"):
    db=Mock();db.scalar.return_value=SimpleNamespace(canonical_symbol=canonical,status=status)
    return db


@pytest.mark.parametrize("canonical,symbol", [("BTC/USDT","BTCUSDT"),("ETH/USDT","ETHUSDT")])
def test_separate_provider_mapping(canonical,symbol):
    db=db_for(canonical)
    target=resolve_historical_target(db,canonical)
    assert target.provider=="BINANCE" and target.provider_symbol==symbol
    assert target.canonical_symbol==canonical and db.scalar.call_count==1
    assert set(HISTORICAL_SYMBOLS)=={"BINANCE"}
    # Resolution reads canonical instruments, never Quidax execution-venue mappings.
    assert "venues" not in str(db.scalar.call_args.args[0])


@pytest.mark.parametrize("canonical", ["BTC/NGN","ETH/NGN","USDT/NGN","BTC/USD","ETH/USD"])
def test_unsupported_markets_do_not_substitute(canonical):
    with pytest.raises(HistoricalMappingUnavailable): resolve_historical_target(db_for(canonical),canonical)


def test_unknown_or_disabled_canonical():
    db=Mock();db.scalar.return_value=None
    with pytest.raises(InstrumentNotFound): resolve_historical_target(db,"UNKNOWN/USDT")
    with pytest.raises(HistoricalMappingUnavailable): resolve_historical_target(db_for("BTC/USDT","disabled"),"BTC/USDT")


def client(db,provider):
    app=create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session]=lambda:db
    app.dependency_overrides[get_historical_provider]=lambda:provider
    return TestClient(app)


def test_provider_protocol_and_canonical_api_full_http_flow():
    def handler(request):
        assert request.url.params["symbol"]=="BTCUSDT"
        return httpx.Response(200,json=[row()])
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider: HistoricalMarketDataProvider=BinanceHistoricalProvider(http)
        response=client(db_for("BTC/USDT"),provider).get("/api/market-data/candles/BTC/USDT?timeframe=1h&limit=1")
    assert response.status_code==200 and response.headers["cache-control"]=="no-store"
    item=response.json()[0]
    assert item==dict(canonical_symbol="BTC/USDT",provider="BINANCE",timeframe="1h",
        open_time="2026-10-15T00:00:00Z",close_time="2026-10-15T00:59:59.999000Z",
        open=row()[1],high=row()[2],low=row()[3],close=row()[4],volume=row()[5],retrieved_at=item["retrieved_at"])
    assert "QUIDAX" not in response.text and "BTCUSDT" not in response.text


@pytest.mark.parametrize("timeframe", DURATIONS)
def test_api_each_canonical_timeframe(timeframe):
    provider=Mock();provider.fetch_candles.return_value=normalize_klines([row(timeframe)],timeframe,1,NOW)
    response=client(db_for("ETH/USDT"),provider).get(f"/api/market-data/candles/ETH/USDT?timeframe={timeframe}&limit=1")
    assert response.status_code==200
    provider.fetch_candles.assert_called_once_with("ETHUSDT",timeframe,1)
    assert response.json()[0]["provider"]=="BINANCE"


@pytest.mark.parametrize("query", ["timeframe=5m","timeframe=1H","limit=0","limit=1001","limit=abc","limit=1.5"])
def test_bad_api_parameters(query):
    provider=Mock()
    assert client(db_for("BTC/USDT"),provider).get("/api/market-data/candles/BTC/USDT?"+query).status_code==422
    provider.fetch_candles.assert_not_called()


@pytest.mark.parametrize("canonical", ["BTC/NGN","ETH/NGN","USDT/NGN","BTC/USD","ETH/USD"])
def test_unmapped_api_no_network_or_prices(canonical):
    provider=Mock();response=client(db_for(canonical),provider).get("/api/market-data/candles/"+canonical)
    assert response.status_code==409 and response.json()["detail"]["code"]=="historical_provider_unsupported"
    provider.fetch_candles.assert_not_called()


def test_unknown_api_and_sanitized_provider_failure():
    db=Mock();db.scalar.return_value=None;provider=Mock()
    assert client(db,provider).get("/api/market-data/candles/UNKNOWN/USDT").status_code==404
    provider.fetch_candles.assert_not_called()
    provider.fetch_candles.side_effect=ProviderUnavailable("private-details")
    response=client(db_for("BTC/USDT"),provider).get("/api/market-data/candles/BTC/USDT")
    assert response.status_code==503 and response.headers["cache-control"]=="no-store"
    assert "private-details" not in response.text and '"open"' not in response.text


def test_another_historical_provider_can_implement_protocol():
    class OtherProvider:
        def fetch_candles(self,provider_symbol,timeframe,limit):
            assert provider_symbol=="BTCUSDT"
            return normalize_klines([row()],timeframe,limit,NOW)
    provider: HistoricalMarketDataProvider=OtherProvider()
    target=resolve_historical_target(db_for("BTC/USDT"),"BTC/USDT")
    result=fetch_historical_candles(target,"1h",1,provider)
    assert result[0].provider=="BINANCE" and result[0].canonical_symbol=="BTC/USDT"


def test_float_or_naive_contract_rejected_zero_volume_allowed():
    values=normalize_klines([row()],"1h",1,NOW)[0].model_dump()
    with pytest.raises(ValidationError): ProviderCandle(**{**values,"open":1.1})
    with pytest.raises(ValidationError): ProviderCandle(**{**values,"open_time":datetime(2026,10,15)})
    assert ProviderCandle(**{**values,"volume":Decimal("0")}).volume==Decimal("0")
