import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Numeric, UniqueConstraint

from app.auth.dependencies import get_current_user
from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.market_data.binance import get_historical_provider
from app.market_data.candle_schemas import ProviderCandle
from app.market_data.candle_store import CandleIntegrityConflict, persist_candles, validated_batch
from app.market_data.historical_mappings import HistoricalTarget
from app.market_data.historical_service import ingest_historical_candles
from app.market_data.models import MarketCandle
from app.market_data.provider import ProviderUnavailable
from app.market_data.quality import INTERVAL_SECONDS, analyze_quality
from app.market_data.candle_schemas import CandleResponse

UTC=timezone.utc
START=datetime(2026,10,15,tzinfo=UTC)
NOW=datetime(2026,10,16,tzinfo=UTC)
ORIGIN={"Origin":"http://127.0.0.1:5173"}


def provider_candle(timeframe="1h",start=START):
    return ProviderCandle(open_time=start,close_time=start+timedelta(seconds=INTERVAL_SECONDS[timeframe])-timedelta(milliseconds=1),
        open=Decimal("10.123456789012345678901234567890123456789"), high=Decimal("12"),low=Decimal("9"),close=Decimal("11"),
        volume=Decimal("0.123456789012345678901234567890123456789"),retrieved_at=NOW)


def normalized(canonical="BTC/USDT",timeframe="1h",start=START):
    return CandleResponse(**provider_candle(timeframe,start).model_dump(),canonical_symbol=canonical,provider="BINANCE",timeframe=timeframe)


@pytest.fixture
def store():
    db=Mock();rows={};statements=[];instruments={key:SimpleNamespace(id=uuid.uuid4(),canonical_symbol=key,status="active")
        for key in ("BTC/USDT","ETH/USDT","BTC/NGN","ETH/NGN","USDT/NGN","BTC/USD","ETH/USD")}
    def selected(statement):
        params=statement.compile().params
        return [row for row in rows.values() if all(getattr(row,key.rsplit("_",1)[0],None)==value
                for key,value in params.items() if not key.startswith("param_"))]
    def scalar(statement):
        statements.append(statement)
        params=statement.compile().params
        if getattr(statement,"is_insert",False):
            key=tuple(params[field] for field in ("instrument_id","provider","timeframe","open_time"))
            if key in rows:return None
            values={key:value for key,value in params.items() if key!="id"}
            row=MarketCandle(id=uuid.uuid4(),**values,created_at=NOW,updated_at=NOW);rows[key]=row
            return row.id
        if statement.column_descriptions[0]["entity"].__name__=="Instrument":
            return instruments.get(params["canonical_symbol_1"])
        matches=selected(statement);return matches[0] if matches else None
    def scalars(statement):
        matches=sorted(selected(statement),key=lambda row:row.open_time,reverse=True)
        if statement._limit_clause is not None:matches=matches[:statement._limit_clause.value]
        return SimpleNamespace(all=lambda:matches)
    def execute(statement):
        matches=sorted(selected(statement),key=lambda row:row.open_time)
        return SimpleNamespace(all=lambda:[(row.open_time,row.close_time) for row in matches])
    db.scalar.side_effect=scalar;db.scalars.side_effect=scalars;db.execute.side_effect=execute
    provider=Mock();provider.fetch_candles.return_value=[provider_candle()]
    app=create_app(Settings(_env_file=None));app.dependency_overrides[get_session]=lambda:db
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=uuid.uuid4(),status="active")
    app.dependency_overrides[get_historical_provider]=lambda:provider
    return SimpleNamespace(db=db,rows=rows,statements=statements,instruments=instruments,provider=provider,
                           client=TestClient(app),app=app)


def test_postgresql_schema_numeric_and_unique_identity():
    table=MarketCandle.__table__
    assert ("instrument_id","provider","timeframe","open_time") in {
        tuple(c.name for c in constraint.columns) for constraint in table.constraints if isinstance(constraint,UniqueConstraint)}
    for field in ("open","high","low","close","volume"):
        assert isinstance(table.c[field].type,Numeric) and table.c[field].type.asdecimal
        assert table.c[field].type.scale is None and table.c[field].type.precision is None
    for field in ("open_time","close_time","retrieved_at","created_at","updated_at"):
        assert table.c[field].type.timezone
    assert {fk.target_fullname for fk in table.c.instrument_id.foreign_keys}=={"instruments.id"}


@pytest.mark.parametrize("canonical",["BTC/USDT","ETH/USDT"])
@pytest.mark.parametrize("timeframe",INTERVAL_SECONDS)
def test_ingestion_default_provenance_decimal_and_idempotence(store,canonical,timeframe):
    store.provider.fetch_candles.return_value=[provider_candle(timeframe)]
    target=HistoricalTarget(canonical,"BINANCE",canonical.replace("/",""))
    result=ingest_historical_candles(store.db,target,timeframe,1,store.provider)
    assert (result.inserted,result.unchanged)==(1,0)
    original=next(iter(store.rows.values()))
    again=ingest_historical_candles(store.db,target,timeframe,1,store.provider)
    assert (again.inserted,again.unchanged)==(0,1) and len(store.rows)==1
    assert next(iter(store.rows.values())) is original
    assert original.provider=="BINANCE" and isinstance(original.open,Decimal)
    assert original.open==provider_candle().open and original.volume==provider_candle().volume
    store.db.commit.assert_not_called()


def test_changed_price_or_volume_conflicts_without_overwrite(store):
    target=HistoricalTarget("BTC/USDT","BINANCE","BTCUSDT")
    candle=normalized();persist_candles(store.db,store.instruments["BTC/USDT"].id,target,"1h",[candle],1)
    original=next(iter(store.rows.values()))
    for patch in ({"close":Decimal("10")},{"volume":Decimal("5")}):
        changed=CandleResponse.model_validate({**candle.model_dump(),**patch})
        with pytest.raises(CandleIntegrityConflict):
            persist_candles(store.db,original.instrument_id,target,"1h",[changed],1)
        assert original.close==candle.close and original.volume==candle.volume


def test_new_retrieval_time_does_not_change_existing_row(store):
    target=HistoricalTarget("BTC/USDT","BINANCE","BTCUSDT");identifier=store.instruments["BTC/USDT"].id
    candle=normalized();persist_candles(store.db,identifier,target,"1h",[candle],1)
    later=CandleResponse.model_validate({**candle.model_dump(),"retrieved_at":NOW+timedelta(minutes=1)})
    result=persist_candles(store.db,identifier,target,"1h",[later],1)
    assert result.unchanged==1 and next(iter(store.rows.values())).retrieved_at==NOW


@pytest.mark.parametrize("mutation",["duplicate","float","identity","invalid-high","unfinished"])
def test_invalid_batch_never_writes(store,mutation):
    candle=normalized();target=HistoricalTarget("BTC/USDT","BINANCE","BTCUSDT")
    if mutation=="duplicate":batch=[candle,candle]
    else:
        changed=candle.model_copy(deep=True)
        if mutation=="float":changed.open=1.1
        if mutation=="identity":changed.provider="QUIDAX"
        if mutation=="invalid-high":changed.high=Decimal("1")
        if mutation=="unfinished":changed.close_time=NOW+timedelta(days=10)
        batch=[changed]
    with pytest.raises(ProviderUnavailable):persist_candles(store.db,uuid.uuid4(),target,"1h",batch,2)
    assert store.rows=={};store.db.scalar.assert_not_called()


def test_malformed_provider_never_persists(store):
    store.provider.fetch_candles.return_value=[SimpleNamespace(open=1.1)]
    response=store.client.post("/api/market-data/candle-ingestions/BTC/USDT",headers=ORIGIN)
    assert response.status_code==503 and store.rows=={}
    store.db.rollback.assert_called_once()


def test_api_ingestion_storage_quality_and_order(store):
    store.provider.fetch_candles.return_value=[provider_candle(start=START+timedelta(hours=2)),provider_candle()]
    url="/api/market-data/candle-ingestions/BTC/USDT?timeframe=1h&limit=2"
    response=store.client.post(url,headers=ORIGIN)
    assert response.status_code==200 and response.json()["inserted"]==2
    assert store.client.post(url,headers=ORIGIN).json()["unchanged"]==2
    stored=store.client.get("/api/market-data/stored-candles/BTC/USDT?timeframe=1h&provider=BINANCE")
    assert stored.status_code==200
    assert [row["open_time"] for row in stored.json()]==["2026-10-15T00:00:00Z","2026-10-15T02:00:00Z"]
    assert stored.json()[0]["open"]==str(provider_candle().open)
    quality=store.client.get("/api/market-data/candle-quality/BTC/USDT?timeframe=1h&provider=BINANCE").json()
    assert quality["candle_count"]==2 and quality["duplicate_count"]==0
    assert quality["gaps"][0]["missing_count"]==1 and len(store.rows)==2
    assert quality["provider"]=="BINANCE"


def test_api_conflict_rolls_back(store):
    url="/api/market-data/candle-ingestions/BTC/USDT?limit=1"
    assert store.client.post(url,headers=ORIGIN).status_code==200
    changed=provider_candle().model_copy(update={"close":Decimal("10")})
    store.provider.fetch_candles.return_value=[changed]
    response=store.client.post(url,headers=ORIGIN)
    assert response.status_code==409 and response.json()["detail"]["code"]=="candle_integrity_conflict"
    store.db.rollback.assert_called_once()


def test_auth_origin_and_no_destructive_routes(store):
    url="/api/market-data/candle-ingestions/BTC/USDT"
    assert store.client.post(url).status_code==403
    store.app.dependency_overrides.pop(get_current_user)
    assert store.client.post(url,headers=ORIGIN).status_code==401
    store.provider.fetch_candles.assert_not_called()
    assert store.client.delete("/api/market-data/stored-candles/BTC/USDT").status_code==405


@pytest.mark.parametrize("canonical",["BTC/NGN","ETH/NGN","USDT/NGN","BTC/USD","ETH/USD"])
def test_unsupported_ingestion_never_persists(store,canonical):
    response=store.client.post("/api/market-data/candle-ingestions/"+canonical,headers=ORIGIN)
    assert response.status_code==409 and not store.rows
    store.provider.fetch_candles.assert_not_called()


@pytest.mark.parametrize("query",["timeframe=5m","limit=0","limit=1001"])
def test_invalid_ingestion_query(store,query):
    assert store.client.post("/api/market-data/candle-ingestions/BTC/USDT?"+query,headers=ORIGIN).status_code==422
    assert not store.rows;store.provider.fetch_candles.assert_not_called()


def test_freshness_uses_latest_completed_interval_and_gaps_not_retrieval():
    day=datetime(2026,10,15,tzinfo=UTC)
    intervals=[(day+timedelta(hours=h),day+timedelta(hours=h+1)-timedelta(milliseconds=1)) for h in (10,11,13)]
    quality=analyze_quality("BTC/USDT","BINANCE","1h",intervals,day+timedelta(hours=14,minutes=15))
    assert quality.freshness=="fresh" and quality.candle_count==3
    assert quality.gaps[0].first_missing_open_time==day+timedelta(hours=12)
    assert quality.gaps[0].last_missing_open_time==day+timedelta(hours=12)
    assert quality.missing_interval_count==1
    stale=analyze_quality("BTC/USDT","BINANCE","1h",intervals,day+timedelta(hours=15))
    assert stale.freshness=="stale"
    assert len(intervals)==3


@pytest.mark.parametrize("timeframe",INTERVAL_SECONDS)
def test_timeframe_freshness_boundary_and_empty_dataset(timeframe):
    seconds=INTERVAL_SECONDS[timeframe]
    candle=provider_candle(timeframe)
    fresh=analyze_quality("BTC/USDT","BINANCE",timeframe,[(candle.open_time,candle.close_time)],START+timedelta(seconds=seconds))
    assert fresh.freshness=="fresh" and fresh.expected_latest_close_time==candle.close_time
    stale=analyze_quality("BTC/USDT","BINANCE",timeframe,[(candle.open_time,candle.close_time)],START+timedelta(seconds=2*seconds))
    assert stale.freshness=="stale"
    empty=analyze_quality("BTC/USDT","BINANCE",timeframe,[],NOW)
    assert empty.freshness=="empty" and empty.candle_count==0 and empty.latest_close_time is None


def test_duplicate_quality_reports_extra_rows():
    candle=provider_candle();interval=(candle.open_time,candle.close_time)
    quality=analyze_quality("BTC/USDT","BINANCE","1h",[interval,interval],NOW)
    assert quality.duplicate_count==1
