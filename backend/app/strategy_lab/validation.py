"""Immutable chronological research partitions; no optimization or execution."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from .canonical import utc_timestamp
from .dataset import ResearchDataset
from app.market_intelligence.types import Candle

VALIDATION_VERSION = "1.0.0"


class ValidationKind(str, Enum):
    IN_SAMPLE = "IN_SAMPLE"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    WALK_FORWARD = "WALK_FORWARD"


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self):
        utc_timestamp(self.start)
        utc_timestamp(self.end)
        start = self.start.astimezone(timezone.utc)
        end = self.end.astimezone(timezone.utc)
        if start >= end:
            raise ValueError("Window start must precede exclusive end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def configuration(self) -> dict:
        return {"start": utc_timestamp(self.start), "end": utc_timestamp(self.end)}


@dataclass(frozen=True)
class ValidationSplit:
    in_sample: TimeWindow
    out_of_sample: TimeWindow | None = None

    def __post_init__(self):
        if not isinstance(self.in_sample, TimeWindow):
            raise ValueError("An explicit in-sample window is required")
        if self.out_of_sample is not None:
            if not isinstance(self.out_of_sample, TimeWindow):
                raise ValueError("Invalid out-of-sample window")
            if self.in_sample.end > self.out_of_sample.start:
                raise ValueError("OOS must follow in-sample without overlap")

    @property
    def configuration_cutoff(self) -> datetime:
        """Exclusive end of data allowed for this split's earlier configuration."""
        return self.in_sample.end

    @property
    def evaluation_window(self) -> TimeWindow:
        return self.out_of_sample if self.out_of_sample is not None else self.in_sample

    def configuration(self) -> dict:
        return {"in_sample": self.in_sample.configuration(),
                "out_of_sample": self.out_of_sample.configuration() if self.out_of_sample is not None else None,
                "configuration_cutoff": utc_timestamp(self.configuration_cutoff)}


@dataclass(frozen=True)
class ValidationPlan:
    kind: ValidationKind
    splits: tuple[ValidationSplit, ...]

    def __post_init__(self):
        if not isinstance(self.kind, ValidationKind):
            raise ValueError("Invalid validation kind")
        if not isinstance(self.splits, (tuple, list)):
            raise ValueError("Splits must be an ordered sequence")
        rows = tuple(self.splits)
        if not rows or any(not isinstance(row, ValidationSplit) for row in rows):
            raise ValueError("At least one valid split is required")
        if self.kind == ValidationKind.IN_SAMPLE:
            if len(rows) != 1 or rows[0].out_of_sample is not None:
                raise ValueError("IN_SAMPLE requires one in-sample-only split")
        elif self.kind == ValidationKind.OUT_OF_SAMPLE:
            if len(rows) != 1 or rows[0].out_of_sample is None:
                raise ValueError("OUT_OF_SAMPLE requires one paired split")
        elif len(rows) < 2 or any(row.out_of_sample is None for row in rows):
            raise ValueError("WALK_FORWARD requires at least two paired splits")
        for previous, current in zip(rows, rows[1:]):
            # Deliberately conservative: no reused training or evaluation observations.
            if previous.evaluation_window.end > current.in_sample.start:
                raise ValueError("Walk-forward folds must advance without any overlap")
        object.__setattr__(self, "splits", rows)

    @property
    def evaluation_windows(self) -> tuple[TimeWindow, ...]:
        return tuple(row.evaluation_window for row in self.splits)

    def validate_for(self, dataset: ResearchDataset) -> None:
        if not isinstance(dataset, ResearchDataset):
            raise ValueError("Expected a research dataset")
        boundaries = {row.open_time for row in dataset.evaluation_candles}
        boundaries.add(dataset.evaluation_end)
        for split in self.splits:
            windows = (split.in_sample,) if split.out_of_sample is None else (split.in_sample, split.out_of_sample)
            for window in windows:
                if (window.start < dataset.evaluation_start or window.end > dataset.evaluation_end
                    or window.start not in boundaries or window.end not in boundaries):
                    raise ValueError("Validation windows must use explicit evaluation candle boundaries")

    def _split(self, index: int) -> ValidationSplit:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.splits):
            raise ValueError("Invalid split index")
        return self.splits[index]

    def in_sample_candles(self, dataset: ResearchDataset, index: int = 0) -> tuple[Candle, ...]:
        self.validate_for(dataset)
        window = self._split(index).in_sample
        return tuple(row for row in dataset.evaluation_candles if window.start <= row.open_time < window.end)

    def out_of_sample_candles(self, dataset: ResearchDataset, index: int = 0) -> tuple[Candle, ...]:
        self.validate_for(dataset)
        window = self._split(index).out_of_sample
        if window is None:
            return ()
        return tuple(row for row in dataset.evaluation_candles if window.start <= row.open_time < window.end)

    def configuration(self) -> dict:
        return {"validation_version": VALIDATION_VERSION, "kind": self.kind.value,
                "interval_convention": "inclusive_start_exclusive_end",
                "fold_policy": "strictly_disjoint_chronological",
                "splits": [row.configuration() for row in self.splits],
                "evaluation_windows": [window.configuration() for window in self.evaluation_windows]}
