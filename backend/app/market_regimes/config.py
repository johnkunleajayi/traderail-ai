from dataclasses import dataclass, fields
from decimal import Decimal
from app.market_intelligence.config import FEATURE_CONFIG


@dataclass(frozen=True)
class RegimeConfiguration:
    engine_version: str = "1.0.0"
    persistence_window: int = 3
    high_volatility_confirmation: int = 2
    breakout_confirmation: int = 1
    slope_lookback: int = 3
    breakout_lookback: int = 20
    volatility_baseline_window: int = 20
    trend_spread: Decimal = Decimal("0.0015")
    trend_fast_slope: Decimal = Decimal("0.0002")
    trend_slow_slope: Decimal = Decimal("0.0001")
    momentum_up_rsi: Decimal = Decimal("55")
    momentum_down_rsi: Decimal = Decimal("45")
    range_spread: Decimal = Decimal("0.001")
    range_fast_slope: Decimal = Decimal("0.00015")
    range_slow_slope: Decimal = Decimal("0.0001")
    range_rsi_low: Decimal = Decimal("40")
    range_rsi_high: Decimal = Decimal("60")
    range_histogram: Decimal = Decimal("0.0005")
    range_atr_ceiling: Decimal = Decimal("0.02")
    range_volatility_ceiling: Decimal = Decimal("0.01")
    breakout_displacement: Decimal = Decimal("0.002")
    breakout_atr_displacement: Decimal = Decimal("0.5")
    breakout_atr_floor: Decimal = Decimal("0.001")
    breakout_volatility_ratio: Decimal = Decimal("1.2")
    breakout_atr_ratio: Decimal = Decimal("1.1")
    high_volatility_ratio: Decimal = Decimal("2.5")
    high_volatility_atr_floor: Decimal = Decimal("0.005")
    high_volatility_return_floor: Decimal = Decimal("0.003")
    volatility_baseline_floor: Decimal = Decimal("0.001")
    atr_baseline_floor: Decimal = Decimal("0.001")

    def __post_init__(self):
        if not isinstance(self.engine_version, str) or not self.engine_version:
            raise ValueError("Configuration version required")
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "engine_version":
                continue
            if field.type is Decimal:
                if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                    raise ValueError("Thresholds must be positive finite Decimals")
            elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("Windows must be positive integers")
        if not (0 < self.momentum_down_rsi < self.momentum_up_rsi < 100
                and 0 < self.range_rsi_low < self.range_rsi_high < 100):
            raise ValueError("Invalid RSI thresholds")


def measurement_first_indices():
    f = FEATURE_CONFIG
    macd_line = max(f.macd_fast_period, f.macd_slow_period) - 1
    return {"ema_20": f.ema_periods[0] - 1, "ema_50": f.ema_periods[1] - 1,
        "rsi_14": f.rsi_period, "atr_14": f.atr_period - 1, "macd": macd_line,
        "macd_signal": macd_line + f.macd_signal_period - 1,
        "macd_histogram": macd_line + f.macd_signal_period - 1,
        "simple_return": 1, "log_return": 1, "realized_volatility": f.realized_volatility_window}


def candidate_first_index(config: RegimeConfiguration) -> int:
    first = measurement_first_indices()
    return max(max(first.values()), max(first["ema_20"], first["ema_50"]) + config.slope_lookback,
        config.breakout_lookback,
        first["realized_volatility"] + config.volatility_baseline_window,
        first["atr_14"] + config.volatility_baseline_window)


REGIME_CONFIG = RegimeConfiguration()
