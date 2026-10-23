"""One causal, stored-only Adaptive Trend observation. No ingestion or providers."""
from dataclasses import asdict
from datetime import timedelta
from sqlalchemy import select
from app.assets.models import Asset
from app.instruments.models import Instrument
from app.market_data.models import MarketCandle
from app.market_intelligence.types import DatasetContext,Candle,TIMEFRAME_SECONDS
from app.market_intelligence.engine import calculate_features
from app.market_regimes.engine import classify_regimes
from app.strategies.adaptive_trend import AdaptiveTrendStrategy
from app.strategies.inputs import StrategyInput,PositionState
from app.strategies.contract import evaluate_strategy
from app.risk.canonical import timestamp,values
from app.risk.context import MarketRiskContext
from app.strategy_lab.canonical import fingerprint,canonical_json
from .policies import SYMBOL,PROVIDER,TIMEFRAME,HISTORY,MARKET_MAX_AGE,MARKET_POLICY_VERSION


class PaperEvidenceUnavailable(ValueError):
    pass


def digest(namespace,payload):
    return fingerprint(namespace,canonical_json(values(payload)))


def analyze(context,candles,*,at):
    at=timestamp(at)
    if (context.canonical_symbol,context.provider,context.timeframe)!=(SYMBOL,PROVIDER,TIMEFRAME):
        raise PaperEvidenceUnavailable("Unsupported controlled PAPER setup")
    if len(candles)!=HISTORY:
        raise PaperEvidenceUnavailable("Exact declared analytical history unavailable")
    duration=timedelta(seconds=TIMEFRAME_SECONDS[TIMEFRAME])
    expected_close=candles[-1].open_time+duration
    expiry=expected_close+MARKET_MAX_AGE[TIMEFRAME]
    if not expected_close<=at<expiry or any(c.retrieved_at>at for c in candles):
        raise PaperEvidenceUnavailable("Completed market history is stale or future-dated")
    features=calculate_features(context,candles,as_of=at)
    regimes=classify_regimes(features,as_of=at)
    strategy=AdaptiveTrendStrategy()
    observation=StrategyInput(context,candles[-1],features[-1],regimes[-1],strategy.definition,at,PositionState.CASH)
    decision=evaluate_strategy(strategy,observation)
    market=MarketRiskContext(context,candles[-1].close_time,at,expiry,
        digest("paper-market-data/v1",{"policy":MARKET_POLICY_VERSION,"history":candles}),
        digest("paper-market-intelligence/v1",features[-1]),digest("paper-market-regime/v1",regimes[-1]),regimes[-1].regime)
    return observation,decision,market


def read_observation(c,*,at):
    row=c.execute(select(Instrument.__table__).where(Instrument.canonical_symbol==SYMBOL,
        Instrument.status=="active",Instrument.market_type=="SPOT").with_for_update(read=True)).mappings().one_or_none()
    if row is None:
        raise PaperEvidenceUnavailable("Canonical PAPER market unavailable")
    assets=c.execute(select(Asset.id,Asset.symbol).where(Asset.id.in_([row['base_asset_id'],row['quote_asset_id']]),
        Asset.status=='active').with_for_update(read=True)).all()
    if dict(assets)!={row['base_asset_id']:'BTC',row['quote_asset_id']:'USDT'}:
        raise PaperEvidenceUnavailable("Canonical market currency unavailable")
    stored=c.execute(select(MarketCandle.__table__).where(MarketCandle.instrument_id==row['id'],
        MarketCandle.provider==PROVIDER,MarketCandle.timeframe==TIMEFRAME,MarketCandle.close_time<at)
        .order_by(MarketCandle.open_time.desc()).limit(HISTORY).with_for_update(read=True)).mappings().all()
    candles=tuple(Candle(**{name:r[name] for name in Candle.__dataclass_fields__}) for r in reversed(stored))
    return analyze(DatasetContext(row['id'],SYMBOL,PROVIDER,TIMEFRAME),candles,at=at)
