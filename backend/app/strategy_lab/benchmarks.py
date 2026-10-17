"""Passive comparison definitions only; no return generation or simulator."""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

BENCHMARK_VERSION = "1.0.0"


class BenchmarkKind(str, Enum):
    CASH = "CASH"
    BUY_AND_HOLD = "BUY_AND_HOLD"


@dataclass(frozen=True)
class BenchmarkDefinition:
    kind: BenchmarkKind

    def __post_init__(self):
        if not isinstance(self.kind, BenchmarkKind):
            raise ValueError("Invalid benchmark kind")

    @property
    def market_exposure(self) -> Decimal:
        return Decimal("0") if self.kind == BenchmarkKind.CASH else Decimal("1")

    def configuration(self) -> dict:
        return {"kind": self.kind.value, "market_exposure": self.market_exposure,
                "period_source": "declared_validation_evaluation_windows"}


@dataclass(frozen=True)
class BenchmarkConfiguration:
    definitions: tuple[BenchmarkDefinition, ...]

    def __post_init__(self):
        if not isinstance(self.definitions, (tuple, list)):
            raise ValueError("Benchmarks must be an explicit sequence")
        rows = tuple(self.definitions)
        if not rows or any(not isinstance(row, BenchmarkDefinition) for row in rows):
            raise ValueError("At least one benchmark definition is required")
        if len({row.kind for row in rows}) != len(rows):
            raise ValueError("Duplicate benchmark definitions")
        # Comparison membership is meaningful; presentation order is not.
        object.__setattr__(self, "definitions", tuple(sorted(rows, key=lambda row: row.kind.value)))

    def configuration(self) -> dict:
        return {"benchmark_version": BENCHMARK_VERSION,
                "definitions": [row.configuration() for row in self.definitions]}
