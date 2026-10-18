"""Explicit chronological windows and fixed strategy/policy; no caller tuning."""
from typing import Annotated, Literal
from datetime import timezone
from pydantic import BaseModel,ConfigDict,Field,AwareDatetime,BeforeValidator,field_validator,model_validator
from app.market_data.candle_schemas import Timeframe
from .schemas import MoneyInput,ExecutionRequest,timestamp_input,MIN_WARMUP_CANDLES,MAX_HISTORY_CANDLES
from .validation_config import MAX_VALIDATION_FOLDS


class ValidationBoundaries(BaseModel):
    model_config=ConfigDict(extra="forbid")
    warmup_start: Annotated[AwareDatetime,BeforeValidator(timestamp_input)]
    in_sample_start: Annotated[AwareDatetime,BeforeValidator(timestamp_input)]
    out_of_sample_start: Annotated[AwareDatetime,BeforeValidator(timestamp_input)]
    end: Annotated[AwareDatetime,BeforeValidator(timestamp_input)]

    @field_validator("warmup_start","in_sample_start","out_of_sample_start","end")
    @classmethod
    def utc(cls,value): return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def chronology(self):
        if not self.warmup_start < self.in_sample_start < self.out_of_sample_start < self.end:
            raise ValueError("Warm-up, IS and OOS boundaries must be strictly chronological")
        return self


class ValidationRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    canonical_symbol: str=Field(pattern=r"^[A-Z][A-Z0-9]{0,15}/[A-Z][A-Z0-9]{0,15}$")
    provider: Literal["BINANCE"]="BINANCE"
    timeframe: Timeframe="1h"
    requested_candle_count: int=Field(strict=True,ge=MIN_WARMUP_CANDLES+2,le=MAX_HISTORY_CANDLES)
    primary: ValidationBoundaries
    walk_forward_folds: tuple[ValidationBoundaries,...]=Field(min_length=1,max_length=MAX_VALIDATION_FOLDS)
    initial_capital: MoneyInput=Field(gt=0)
    quantity: MoneyInput=Field(gt=0)
    execution: ExecutionRequest
    strategy_family: Literal["ADAPTIVE_TREND"]="ADAPTIVE_TREND"
    strategy_version: Literal["1.0.0"]="1.0.0"

    @model_validator(mode="after")
    def fold_boundaries(self):
        previous=None
        for fold in self.walk_forward_folds:
            if fold.warmup_start < self.primary.warmup_start or fold.end > self.primary.end:
                raise ValueError("Every fold must lie inside the declared primary history")
            if previous is not None and previous.end > fold.warmup_start:
                raise ValueError("Folds must progress forward without overlapping any history")
            previous=fold
        return self
