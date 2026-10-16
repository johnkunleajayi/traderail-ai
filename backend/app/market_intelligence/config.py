from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureConfiguration:
    engine_version: str = "1.0.0"
    ema_periods: tuple[int, int] = (20, 50)
    rsi_period: int = 14
    atr_period: int = 14
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    realized_volatility_window: int = 20
    annualization: bool = False
    ema_initialization: str = "sma_seed"
    rsi_initialization: str = "mean_first_period_changes_then_wilder;flat=50"
    atr_initialization: str = "mean_first_period_tr_including_first_high_low_then_wilder"
    volatility_convention: str = "population_stddev_log_returns"
    decimal_precision: int = 50
    decimal_rounding: str = "ROUND_HALF_EVEN"


FEATURE_CONFIG = FeatureConfiguration()
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
