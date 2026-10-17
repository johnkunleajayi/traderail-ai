"""Centralized v1 research hypotheses, not empirically validated profit settings."""
from dataclasses import dataclass, fields
from decimal import Decimal

STRATEGY_FAMILY = "ADAPTIVE_TREND"
STRATEGY_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class AdaptiveTrendConfiguration:
    minimum_regime_confirmations: int = 3
    entry_min_ema_spread: Decimal = Decimal("0.0015")
    entry_fast_slope: Decimal = Decimal("0.0002")
    entry_slow_slope: Decimal = Decimal("0.0001")
    long_rsi_min: Decimal = Decimal("55")
    long_rsi_max: Decimal = Decimal("70")
    short_rsi_min: Decimal = Decimal("30")
    short_rsi_max: Decimal = Decimal("45")
    entry_min_normalized_histogram: Decimal = Decimal("0.0001")
    max_normalized_atr: Decimal = Decimal("0.02")
    max_realized_volatility: Decimal = Decimal("0.02")
    max_volatility_ratio: Decimal = Decimal("2")
    max_atr_ratio: Decimal = Decimal("2")
    exit_confirmation_dimensions: int = 2
    exit_min_ema_spread: Decimal = Decimal("0.0005")
    exit_fast_slope: Decimal = Decimal("0.0001")
    exit_slow_slope: Decimal = Decimal("0.00005")
    exit_long_rsi: Decimal = Decimal("50")
    exit_short_rsi: Decimal = Decimal("50")
    exit_min_normalized_histogram: Decimal = Decimal("0.00005")

    def __post_init__(self):
        integers = {"minimum_regime_confirmations", "exit_confirmation_dimensions"}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in integers:
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError("Confirmation settings must be positive integers")
            elif not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError("Adaptive Trend thresholds must be positive finite Decimals")
        if not 2 <= self.exit_confirmation_dimensions <= 3:
            raise ValueError("Exits require two or three deterioration dimensions")
        if not (0 < self.short_rsi_min < self.short_rsi_max < self.long_rsi_min < self.long_rsi_max < 100):
            raise ValueError("Invalid directional RSI bands")
        if not (0 < self.exit_long_rsi < 100 and 0 < self.exit_short_rsi < 100):
            raise ValueError("Invalid exit RSI thresholds")


DEFAULT_CONFIGURATION = AdaptiveTrendConfiguration()
