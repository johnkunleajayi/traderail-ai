"""Trusted pair evidence only. No estimation, provider calls or symbol heuristics."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import re

from app.market_intelligence.types import TIMEFRAME_SECONDS
from app.risk.canonical import identity, require, seal, symbol, text, timestamp


@dataclass(frozen=True, slots=True)
class CorrelationEvidence:
    instrument_a: str
    instrument_b: str
    coefficient: Decimal
    provider: str
    timeframe: str
    method: str
    observation_count: int
    window_start: datetime
    window_end: datetime
    as_of: datetime
    expires_at: datetime
    source_identity: str
    evidence_identity: str = field(init=False)

    def __post_init__(self):
        symbol(self.instrument_a)
        symbol(self.instrument_b)
        if self.instrument_a == self.instrument_b:
            raise ValueError("Identical instruments need no empirical self-correlation")
        a, b = sorted((self.instrument_a, self.instrument_b))
        object.__setattr__(self, "instrument_a", a)
        object.__setattr__(self, "instrument_b", b)
        if (not isinstance(self.coefficient, Decimal) or not self.coefficient.is_finite()
                or not Decimal("-1") <= self.coefficient <= Decimal("1")):
            raise ValueError("Correlation requires a finite Decimal between -1 and 1")
        if not isinstance(self.provider, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.provider):
            raise ValueError("Explicit correlation provider required")
        if not isinstance(self.timeframe, str) or self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported correlation timeframe")
        text(self.method, "method")
        require(self.observation_count, int, "observation_count")
        if self.observation_count < 2:
            raise ValueError("At least two declared correlation observations are required")
        for name in ("window_start", "window_end", "as_of", "expires_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if not self.window_start < self.window_end <= self.as_of < self.expires_at:
            raise ValueError("Correlation window and validity must be causal")
        identity(self.source_identity, "source_identity")
        seal(self, "correlation-evidence-1.0.0", "evidence_identity")

    @property
    def pair(self):
        return self.instrument_a, self.instrument_b

    @property
    def observation_context(self):
        return (self.provider, self.timeframe, self.method, self.observation_count, self.window_start, self.window_end)
