from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from app.market_regimes.config import RegimeConfiguration


class Regime(str, Enum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Evidence:
    normalized_ema_spread: Decimal | None = None
    ema20_slope: Decimal | None = None
    ema50_slope: Decimal | None = None
    close_vs_ema20: Decimal | None = None
    close_vs_ema50: Decimal | None = None
    rsi_14: Decimal | None = None
    macd: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_histogram: Decimal | None = None
    normalized_macd_histogram: Decimal | None = None
    normalized_atr: Decimal | None = None
    realized_volatility: Decimal | None = None
    volatility_baseline: Decimal | None = None
    volatility_ratio: Decimal | None = None
    normalized_atr_baseline: Decimal | None = None
    atr_ratio: Decimal | None = None
    reference_low: Decimal | None = None
    reference_high: Decimal | None = None
    upside_displacement: Decimal | None = None
    downside_displacement: Decimal | None = None
    upside_atr_displacement: Decimal | None = None
    downside_atr_displacement: Decimal | None = None
    breakout_direction: str | None = None
    candidate: Regime = Regime.UNKNOWN
    confirmation_count: int = 0
    confirmation_required: int = 0
    retained_previous: bool = False
    previous_regime: Regime | None = None
    available_candles: int = 0
    candidate_history_required: int = 0
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegimeResult:
    canonical_symbol: str | None
    provider: str | None
    timeframe: str | None
    open_time: datetime | None
    close_time: datetime | None
    regime: Regime
    evidence: Evidence
    configuration: RegimeConfiguration
    feature_engine_version: str
