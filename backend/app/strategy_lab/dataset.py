"""Explicit completed-candle research boundaries and immutable dataset snapshots."""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from app.market_intelligence.types import Candle, DatasetContext, TIMEFRAME_SECONDS
from app.market_intelligence.validation import validate_dataset
from .canonical import canonical_json, fingerprint, utc_timestamp

DATASET_FORMAT = "strategy-lab.dataset/v1"


@dataclass(frozen=True)
class ResearchDataset:
    context: DatasetContext
    warmup_start: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    candles: tuple[Candle, ...]
    dataset_fingerprint: str

    @property
    def candle_count(self) -> int:
        return len(self.candles)

    @property
    def warmup_candles(self) -> tuple[Candle, ...]:
        return tuple(row for row in self.candles if row.open_time < self.evaluation_start)

    @property
    def evaluation_candles(self) -> tuple[Candle, ...]:
        return tuple(row for row in self.candles if row.open_time >= self.evaluation_start)

    def history_through(self, open_time: datetime) -> tuple[Candle, ...]:
        """Causal prefix for a supplied candle; never exposes later observations."""
        utc_timestamp(open_time)
        for index, row in enumerate(self.candles):
            if row.open_time == open_time:
                return self.candles[:index + 1]
        raise ValueError("Timestamp is not in the supplied dataset")


def create_dataset(context: DatasetContext, candles: Sequence[Candle], *,
                   warmup_start: datetime, evaluation_start: datetime,
                   evaluation_end: datetime, as_of: datetime) -> ResearchDataset:
    # Completion is checked against caller-supplied observational time, not a future boundary.
    for boundary in (warmup_start, evaluation_start, evaluation_end):
        utc_timestamp(boundary)
    rows = validate_dataset(context, candles, as_of)
    if not rows:
        raise ValueError("A research dataset requires evaluation candles")
    duration = timedelta(seconds=TIMEFRAME_SECONDS[context.timeframe])
    if not (warmup_start <= evaluation_start < evaluation_end):
        raise ValueError("Invalid research boundaries")
    if rows[0].open_time != warmup_start or rows[-1].open_time + duration != evaluation_end:
        raise ValueError("Supplied candles must exactly cover the declared boundaries")
    if not any(row.open_time == evaluation_start for row in rows):
        raise ValueError("Evaluation start must be a supplied candle boundary")
    payload = {
        "canonical_symbol": context.canonical_symbol, "provider": context.provider,
        "timeframe": context.timeframe, "warmup_start": utc_timestamp(warmup_start),
        "evaluation_start": utc_timestamp(evaluation_start),
        "evaluation_end": utc_timestamp(evaluation_end), "candle_count": len(rows),
        "candles": [{"open_time": utc_timestamp(row.open_time),
                     "close_time": utc_timestamp(row.close_time),
                     "retrieved_at": utc_timestamp(row.retrieved_at),
                     "open": row.open, "high": row.high, "low": row.low,
                     "close": row.close, "volume": row.volume} for row in rows],
    }
    return ResearchDataset(context, warmup_start, evaluation_start, evaluation_end, rows,
                           fingerprint(DATASET_FORMAT, canonical_json(payload)))
