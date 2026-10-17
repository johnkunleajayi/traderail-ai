from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import pytest
from app.market_intelligence.types import Candle, DatasetContext, InvalidCandleData
from app.strategy_lab.canonical import canonical_json, decimal_token, fingerprint
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.experiment import create_experiment, create_manifest, snapshot_configuration
from uuid import UUID

START = datetime(2025, 1, 1, tzinfo=timezone.utc)
CONTEXT = DatasetContext(UUID(int=1), "BTC/USDT", "BINANCE", "1h")


def rows(count=5):
    return [Candle(CONTEXT.instrument_id, CONTEXT.provider, CONTEXT.timeframe,
        START + timedelta(hours=i), START + timedelta(hours=i+1, milliseconds=-1),
        Decimal("83415.32000000"), Decimal("83420"), Decimal("83400"),
        Decimal("83416.123456789012345678901234567890"), Decimal("0.000000001"),
        START + timedelta(hours=i+1)) for i in range(count)]


def dataset(data=None, context=CONTEXT, evaluation_start=None, **kwargs):
    return create_dataset(context, rows() if data is None else data,
        warmup_start=kwargs.pop("warmup_start", START),
        evaluation_start=evaluation_start or START + timedelta(hours=2),
        evaluation_end=kwargs.pop("evaluation_end", START + timedelta(hours=5)),
        as_of=kwargs.pop("as_of", START + timedelta(days=1)), **kwargs)


def test_explicit_boundaries_warmup_and_causal_prefix():
    source=rows(); result=dataset(source)
    assert result.candle_count == 5
    assert result.warmup_candles == tuple(source[:2])
    assert result.evaluation_candles == tuple(source[2:])
    assert result.history_through(source[2].open_time) == tuple(source[:3])
    assert all(row.open_time <= source[2].open_time for row in result.history_through(source[2].open_time))
    changed=source[:]; changed[-1]=replace(changed[-1], volume=Decimal("5"))
    assert dataset(changed).history_through(source[2].open_time) == result.history_through(source[2].open_time)
    assert dataset(changed).dataset_fingerprint != result.dataset_fingerprint
    assert source == rows()
    source.clear()
    assert result.candle_count == 5
    with pytest.raises(FrozenInstanceError):
        result.evaluation_start = START
    with pytest.raises(ValueError):
        result.history_through(START + timedelta(days=3))


def test_zero_warmup_is_explicit():
    assert not dataset(evaluation_start=START).warmup_candles


@pytest.mark.parametrize("field,value", [("open",Decimal("83415.33")),
    ("high",Decimal("83421")),("low",Decimal("83399")),
    ("close",Decimal("83416.2")),("volume",Decimal("2")),
    ("retrieved_at", START+timedelta(hours=8))])
def test_every_candle_snapshot_value_changes_identity(field,value):
    source=rows(); source[0]=replace(source[0],**{field:value})
    assert dataset(source).dataset_fingerprint != dataset().dataset_fingerprint


@pytest.mark.parametrize("symbol,provider,timeframe",[("ETH/USDT","BINANCE","1h"),
    ("BTC/USDT","OTHER","1h"),("BTC/USDT","BINANCE","4h")])
def test_context_changes_identity(symbol,provider,timeframe):
    context=replace(CONTEXT,canonical_symbol=symbol,provider=provider,timeframe=timeframe)
    hours=4 if timeframe=="4h" else 1
    source=[replace(row,provider=provider,timeframe=timeframe,open_time=START+timedelta(hours=i*hours),
        close_time=START+timedelta(hours=(i+1)*hours,milliseconds=-1),
        retrieved_at=START+timedelta(hours=(i+1)*hours)) for i,row in enumerate(rows())]
    assert dataset(source,context,evaluation_start=START+timedelta(hours=2*hours),
        evaluation_end=START+timedelta(hours=5*hours)).dataset_fingerprint != dataset().dataset_fingerprint


def test_boundaries_change_fingerprint_without_hidden_history():
    original=dataset()
    assert dataset(evaluation_start=START+timedelta(hours=3)).dataset_fingerprint != original.dataset_fingerprint
    assert dataset(rows()[1:],warmup_start=START+timedelta(hours=1)).dataset_fingerprint != original.dataset_fingerprint
    assert dataset(rows()[:-1],evaluation_end=START+timedelta(hours=4)).dataset_fingerprint != original.dataset_fingerprint
    for source in (rows()[1:], rows()[:-1], rows(6), []):
        with pytest.raises(ValueError):
            dataset(source)
    with pytest.raises(ValueError):
        dataset(evaluation_start=START+timedelta(minutes=30))
    with pytest.raises(ValueError):
        dataset(evaluation_start=START+timedelta(hours=5))


@pytest.mark.parametrize("kind",["gap","duplicate","unordered","mixed","float","incomplete"])
def test_invalid_input_never_sorted_repaired_or_interpolated(kind):
    source=rows(); kwargs={}
    if kind=="gap": source.pop(2)
    if kind=="duplicate": source[2]=source[1]
    if kind=="unordered": source[1],source[2]=source[2],source[1]
    if kind=="mixed": source[2]=replace(source[2],provider="OTHER")
    if kind=="float": source[2]=replace(source[2],close=83416.0)
    if kind=="incomplete": kwargs["as_of"]=START+timedelta(hours=4,minutes=59)
    with pytest.raises(InvalidCandleData): dataset(source,**kwargs)


def test_timezone_numeric_scale_and_database_uuid_are_not_economic_changes():
    original=dataset(); zone=timezone(timedelta(hours=1))
    source=[replace(row,open=row.open.quantize(Decimal(".01")),
        instrument_id=UUID(int=2),open_time=row.open_time.astimezone(zone),
        close_time=row.close_time.astimezone(zone),retrieved_at=row.retrieved_at.astimezone(zone)) for row in rows()]
    result=dataset(source,replace(CONTEXT,instrument_id=UUID(int=2)),
        warmup_start=START.astimezone(zone),evaluation_start=(START+timedelta(hours=2)).astimezone(zone),
        evaluation_end=(START+timedelta(hours=5)).astimezone(zone))
    assert result.dataset_fingerprint == original.dataset_fingerprint


def test_independent_canonical_hash_contract():
    expected='["map",[["a",["decimal","123e-2"]],["z",["list",[["int","2"],["bool",true],["null"]]]]]]'
    assert canonical_json({"z":[2,True,None],"a":Decimal("1.2300")}) == expected
    assert fingerprint("test/v1",expected) == "sha256:"+hashlib.sha256(("test/v1\n"+expected).encode()).hexdigest()
    assert fingerprint("other/v1",expected) != fingerprint("test/v1",expected)


def test_decimal_precision_independent_of_ambient_context():
    value=Decimal("0.000000000000123456789012345678901234567890000")
    with localcontext() as ctx:
        ctx.prec=3
        assert decimal_token(value)=="12345678901234567890123456789e-41"
        first=dataset().dataset_fingerprint
    with localcontext() as ctx:
        ctx.prec=80
        assert dataset().dataset_fingerprint == first
    assert decimal_token(Decimal("-0.000")) == decimal_token(Decimal("0"))


@pytest.mark.parametrize("value",[1.1,float("nan"),Decimal("NaN"),Decimal("Infinity"),{1:"bad"},set()])
def test_unsupported_config_rejected(value):
    with pytest.raises(ValueError): snapshot_configuration({"value":value})


def test_configuration_order_types_and_deep_immutability():
    source={"b":[{"x":Decimal(".10"),"y":True}],"a":3}
    first=snapshot_configuration(source)
    assert first == snapshot_configuration({"a":3,"b":[{"y":True,"x":Decimal(".100")} ]})
    source["b"][0]["x"]=Decimal(".2")
    assert first != snapshot_configuration(source)
    assert len({snapshot_configuration({"v":v}).configuration_fingerprint for v in (1,True,"1",Decimal(1))}) == 4
    assert snapshot_configuration({"v":[1,2]}) != snapshot_configuration({"v":[2,1]})


def test_experiment_and_manifest_identity_excludes_informational_timestamp():
    data=dataset()
    a=create_experiment(data,strategy_configuration={"x":Decimal(".2")},created_at=START)
    b=create_experiment(data,strategy_configuration={"x":Decimal(".20")},created_at=START+timedelta(days=1))
    assert a.identity == b.identity
    assert a.experiment_id == b.experiment_id
    assert a.identity.market_intelligence_version == "1.0.0"
    assert a.identity.regime_engine_version == "1.0.0"
    assert a.identity.strategy_family is a.identity.strategy_version is None
    manifest=create_manifest(data,a)
    assert manifest.identity == create_manifest(data,b).identity
    assert manifest.created_at != create_manifest(data,b).created_at
    assert manifest.warmup_candle_count==2 and manifest.evaluation_candle_count==3
    for overrides in ({"strategy_configuration":{"x":Decimal(".3")}},
        {"market_intelligence_version":"2"},{"regime_engine_version":"2"},
        {"strategy_family":"placeholder","strategy_version":"v0"}):
        args={"strategy_configuration":{"x":Decimal(".2")},"created_at":START};args.update(overrides)
        assert create_experiment(data,**args).experiment_id != a.experiment_id
    with pytest.raises(ValueError): create_manifest(dataset(evaluation_start=START),a)
    with pytest.raises(ValueError): create_experiment(data,strategy_configuration={},created_at=START.replace(tzinfo=None))
    with pytest.raises(ValueError): create_experiment(data,strategy_configuration={},created_at=START,strategy_family="unpaired")


def test_independent_repeated_construction():
    assert dataset() == dataset()
    assert create_manifest(dataset(),create_experiment(dataset(),strategy_configuration={},created_at=START)) == create_manifest(dataset(),create_experiment(dataset(),strategy_configuration={},created_at=START))
