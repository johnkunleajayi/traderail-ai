"""Explicit, bounded stored-market maintenance. No strategy/Risk/execution calls.

Session factory must yield a fresh SQLAlchemy Session. This service owns its
short read/write transactions; network I/O never runs inside them. READY means
market data passes validation, not that strategy or Risk will permit a trade.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Literal
from httpx import HTTPError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.instruments.service import find_instrument
from app.market_data.binance import get_historical_provider
from app.market_data.candle_store import persist_candles, CandleIntegrityConflict
from app.market_data.historical_mappings import resolve_historical_target, HistoricalMappingUnavailable
from app.market_data.historical_service import fetch_historical_candles
from app.market_data.models import MarketCandle
from app.market_data.provider import ProviderUnavailable
from app.market_data.quality import analyze_quality
from app.market_data.service import InstrumentNotFound
from app.market_intelligence.types import Candle, DatasetContext, InvalidCandleData
from app.market_intelligence.validation import validate_dataset
from app.risk.paper_source.policies import SYMBOL, PROVIDER, TIMEFRAME, HISTORY, MARKET_MAX_AGE
from .repository import utc

HOUR = timedelta(hours=1)
MILLISECOND = timedelta(milliseconds=1)

@dataclass(frozen=True)
class MaintenanceResult:
    status: Literal["READY", "PROVIDER_FAILED", "STORAGE_FAILED", "INTEGRITY_CONFLICT", "EVIDENCE_UNSAFE", "MARKET_UNAVAILABLE"]
    reason_code: str
    evaluated_at: datetime
    expected_open_time: datetime
    expected_close_time: datetime
    fresh_until: datetime
    instrument_id: UUID | None = None
    candle_id: UUID | None = None
    canonical_symbol: str = SYMBOL
    provider: str = PROVIDER
    timeframe: str = TIMEFRAME
    ingestion: Literal["NOT_ATTEMPTED", "FAILED", "PERSISTED", "ROLLED_BACK"] = "NOT_ATTEMPTED"
    fetched: int = 0
    inserted: int = 0
    unchanged: int = 0
    candle_count: int = 0
    missing_interval_count: int = 0
    freshness: Literal["UNAVAILABLE", "FRESH", "STALE", "EMPTY"] = "UNAVAILABLE"
    safe_for_evaluation: bool = False


def utc_now():
    return datetime.now(timezone.utc)


def expected_candle(at):
    boundary = utc(at).replace(minute=0, second=0, microsecond=0)
    return boundary-HOUR, boundary-MILLISECOND, boundary+MARKET_MAX_AGE[TIMEFRAME]


def read_window(db, at):
    target=resolve_historical_target(db,SYMBOL,PROVIDER)
    instrument=find_instrument(db,SYMBOL)
    if instrument is None or instrument.market_type != "SPOT":
        raise HistoricalMappingUnavailable()
    latest,_,_=expected_candle(at)
    first=latest-HOUR*(HISTORY-1)
    rows=list(db.scalars(select(MarketCandle).where(MarketCandle.instrument_id==instrument.id,
        MarketCandle.provider==PROVIDER,MarketCandle.timeframe==TIMEFRAME,
        MarketCandle.open_time>=first,MarketCandle.open_time<=latest)
        .order_by(MarketCandle.open_time).limit(HISTORY)).all())
    return target,instrument.id,rows


def fetch_limit(rows, at):
    latest,_,_=expected_candle(at)
    present={r.open_time for r in rows}
    missing=[latest-HOUR*i for i in range(HISTORY) if latest-HOUR*i not in present]
    return (latest-min(missing))//HOUR+1 if missing else 0


def assess(rows, instrument_id, at):
    latest,close,until=expected_candle(at)
    intervals=[(r.open_time,r.close_time) for r in rows]
    quality=analyze_quality(SYMBOL,PROVIDER,TIMEFRAME,intervals,at)
    missing=HISTORY-len({r.open_time for r in rows})
    candles=tuple(Candle(**{key:getattr(r,key) for key in Candle.__dataclass_fields__}) for r in rows)
    validate_dataset(DatasetContext(instrument_id,SYMBOL,PROVIDER,TIMEFRAME),candles,at)
    if any(r.retrieved_at>at for r in rows):
        raise InvalidCandleData("future_retrieval")
    latest_row=next((r for r in rows if r.open_time==latest and r.close_time==close),None)
    freshness="FRESH" if latest_row is not None and at<until else "STALE" if rows else "EMPTY"
    safe=(len(rows)==HISTORY and missing==0 and quality.missing_interval_count==0
          and quality.duplicate_count==0 and freshness=="FRESH")
    return latest_row,missing,freshness,safe


def maintain_market(session_factory, *, clock=utc_now, provider_factory=None):
    """Maintain only the current completed BTC/USDT 1h analytical window.

    Default provider is the existing public Binance adapter. Injected factories
    permit offline tests. No automatic loop, hidden catch-up or historical trade.
    """
    at=utc(clock())
    opened,closed,until=expected_candle(at)
    base=dict(evaluated_at=at,expected_open_time=opened,expected_close_time=closed,fresh_until=until)
    instrument_id=None
    ingestion="NOT_ATTEMPTED"
    counts={}
    def result(status,reason,**extra):
        return MaintenanceResult(status,reason,instrument_id=instrument_id,ingestion=ingestion,**base,**counts,**extra)
    try:
        with session_factory() as db:
            with db.begin():
                target,instrument_id,rows=read_window(db,at)
                limit=fetch_limit(rows,at)
        if limit:
            factory=provider_factory or contextmanager(get_historical_provider)
            try:
                with factory() as provider:
                    batch=fetch_historical_candles(target,TIMEFRAME,limit,provider)
            except (ProviderUnavailable, HTTPError):
                ingestion="FAILED"
                return result("PROVIDER_FAILED","HISTORICAL_DATA_UNAVAILABLE")
        checked_at=utc(clock())
        if checked_at<at:
            return result("EVIDENCE_UNSAFE","CLOCK_MOVED_BACKWARD")
        if expected_candle(checked_at)[0]!=opened:
            return result("EVIDENCE_UNSAFE","CANDLE_BOUNDARY_CHANGED")
        base['evaluated_at']=checked_at
        with session_factory() as db:
            with db.begin():
                current_target,current_id,_=read_window(db,checked_at)
                if current_target!=target or current_id!=instrument_id:
                    return result("EVIDENCE_UNSAFE","MARKET_CONTEXT_CHANGED")
                if limit:
                    # Existing persistence validates exact Decimal, identity and
                    # completed intervals, and rejects changed stored OHLCV.
                    stored=persist_candles(db,instrument_id,target,TIMEFRAME,batch,limit)
                    ingestion="PERSISTED"
                    counts.update(fetched=stored.fetched,inserted=stored.inserted,unchanged=stored.unchanged)
                _,_,rows=read_window(db,checked_at)
                latest,missing,freshness,safe=assess(rows,instrument_id,checked_at)
                response=result("READY" if safe else "EVIDENCE_UNSAFE",
                    "COMPLETED_WINDOW_READY" if safe else "CURRENT_WINDOW_INCOMPLETE_OR_STALE",
                    candle_id=latest.id if latest else None,candle_count=len(rows),
                    missing_interval_count=missing,freshness=freshness,safe_for_evaluation=safe)
            # Returning only after commit ensures storage errors cannot report READY.
        return response
    except CandleIntegrityConflict:
        ingestion="ROLLED_BACK";counts.clear()
        return result("INTEGRITY_CONFLICT","STORED_CANDLE_CONFLICT")
    except ProviderUnavailable:
        ingestion="ROLLED_BACK";counts.clear()
        return result("PROVIDER_FAILED","INVALID_PROVIDER_BATCH")
    except (InvalidCandleData,ValueError,TypeError,AttributeError):
        # Invalid post-write evidence rolls back the whole maintenance transaction.
        ingestion="ROLLED_BACK" if ingestion=="PERSISTED" else ingestion
        counts.clear()
        return result("EVIDENCE_UNSAFE","INVALID_MARKET_EVIDENCE")
    except (HistoricalMappingUnavailable,InstrumentNotFound):
        return result("MARKET_UNAVAILABLE","CANONICAL_MARKET_UNAVAILABLE")
    except SQLAlchemyError:
        ingestion="ROLLED_BACK" if ingestion=="PERSISTED" else ingestion
        counts.clear()
        return result("STORAGE_FAILED","MARKET_STORAGE_UNAVAILABLE")
