"""Strict local archival input; no providers, dotenv, or database imports."""
from datetime import datetime
from decimal import Decimal
from typing import Literal, Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, BeforeValidator
from app.market_intelligence.types import Candle, DatasetContext
from app.strategy_lab.dataset import create_dataset


def decimal_string(value):
    if not isinstance(value,str) or len(value)>100:
        raise ValueError('Finite decimal strings required')
    result=Decimal(value)
    if not result.is_finite():raise ValueError('Finite decimal strings required')
    return result

Money=Annotated[Decimal,BeforeValidator(decimal_string)]

class Frozen(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)

class LocalCandle(Frozen):
    open_time: AwareDatetime
    close_time: AwareDatetime
    retrieved_at: AwareDatetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume: Money

class HistoricalInput(Frozen):
    format: Literal['traderail-historical-input-v1']
    provenance: str=Field(min_length=1,max_length=200)
    source_kind: Literal['SYNTHETIC_FIXTURE','LOCAL_ARCHIVE_UNVERIFIED']
    instrument_id: UUID
    instrument: Literal['BTC/USDT']
    provider: Literal['BINANCE']
    timeframe: Literal['1h']
    warmup_start: AwareDatetime
    evaluation_start: AwareDatetime
    evaluation_end: AwareDatetime
    as_of: AwareDatetime
    split_at: AwareDatetime
    candles: tuple[LocalCandle,...]=Field(min_length=1,max_length=300)

    def dataset(self):
        context=DatasetContext(self.instrument_id,self.instrument,self.provider,self.timeframe)
        candles=tuple(Candle(self.instrument_id,self.provider,self.timeframe,**row.model_dump()) for row in self.candles)
        return create_dataset(context,candles,warmup_start=self.warmup_start,
            evaluation_start=self.evaluation_start,evaluation_end=self.evaluation_end,as_of=self.as_of)
