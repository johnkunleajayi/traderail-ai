"""Completed stored-candle mark only. No indicators, strategy, refresh or network."""
from datetime import timedelta
from sqlalchemy import select
from app.assets.models import Asset
from app.instruments.models import Instrument
from app.market_data.models import MarketCandle
from app.market_intelligence.types import Candle, DatasetContext, TIMEFRAME_SECONDS
from app.market_intelligence.validation import validate_dataset
from app.risk.paper_source.policies import SYMBOL, PROVIDER, TIMEFRAME, MARKET_MAX_AGE, MARKET_POLICY_VERSION
from app.risk.canonical import values
from app.strategy_lab.canonical import fingerprint, canonical_json
from .contracts import Valuation


def current_mark(c, trades, at):
    try:
        instrument=c.execute(select(Instrument.__table__).where(Instrument.canonical_symbol==SYMBOL,
            Instrument.status=='active',Instrument.market_type=='SPOT')).mappings().one_or_none()
        if instrument is None or any(t.instrument_id!=instrument['id'] for t in trades):
            raise ValueError('Canonical instrument unavailable')
        assets=c.execute(select(Asset.id,Asset.symbol).where(Asset.id.in_([instrument['base_asset_id'],instrument['quote_asset_id']]),
            Asset.status=='active')).all()
        if dict(assets)!={instrument['base_asset_id']:'BTC',instrument['quote_asset_id']:'USDT'}:
            raise ValueError('Currency mismatch')
        rows=c.execute(select(MarketCandle.__table__).where(MarketCandle.instrument_id==instrument['id'],
            MarketCandle.provider==PROVIDER,MarketCandle.timeframe==TIMEFRAME,MarketCandle.close_time<at)
            .order_by(MarketCandle.open_time.desc()).limit(1)).mappings().all()
        if not rows:
            return Valuation(state='UNAVAILABLE',reason_codes=('COMPLETED_MARK_UNAVAILABLE',))
        candle=Candle(**{name:rows[0][name] for name in Candle.__dataclass_fields__})
        context=DatasetContext(instrument['id'],SYMBOL,PROVIDER,TIMEFRAME)
        validate_dataset(context,(candle,),at)
        if candle.retrieved_at>at:
            raise ValueError('Future retrieval')
        valid_until=candle.open_time+timedelta(seconds=TIMEFRAME_SECONDS[TIMEFRAME])+MARKET_MAX_AGE[TIMEFRAME]
        evidence=fingerprint('mandate.completed-mark/v1',canonical_json(values(
            {'context':context,'candle':candle,'policy':MARKET_POLICY_VERSION})))
        details=dict(priced_at=candle.close_time,valid_until=valid_until,source='BINANCE canonical stored BTC/USDT 1h close',
            evidence_identity=evidence)
        if at>=valid_until:
            return Valuation(state='STALE',reason_codes=('COMPLETED_MARK_STALE',),**details)
        return Valuation(state='FRESH',price=candle.close,**details)
    except (ValueError,KeyError,TypeError,ArithmeticError):
        return Valuation(state='UNAVAILABLE',reason_codes=('COMPLETED_MARK_INVALID',))
