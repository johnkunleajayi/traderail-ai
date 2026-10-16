from collections.abc import Sequence
from decimal import Decimal
from app.market_intelligence.types import FeatureRow
from app.market_regimes.config import RegimeConfiguration
from app.market_regimes.types import Evidence


def measure_evidence(rows: Sequence[FeatureRow], index: int, config: RegimeConfiguration) -> Evidence:
    row = rows[index]
    past = rows[index - config.slope_lookback]
    prior = rows[index - config.volatility_baseline_window:index]
    baseline = sum((item.realized_volatility for item in prior), Decimal(0)) / Decimal(len(prior))
    atr_baseline = sum((item.atr_14 / item.close for item in prior), Decimal(0)) / Decimal(len(prior))
    normalized_atr = row.atr_14 / row.close
    structure = rows[index - config.breakout_lookback:index]  # Strictly prior closes, never the current bar.
    low, high = min(item.close for item in structure), max(item.close for item in structure)
    return Evidence(normalized_ema_spread=(row.ema_20 - row.ema_50) / row.close,
        ema20_slope=(row.ema_20 - past.ema_20) / (Decimal(config.slope_lookback) * row.close),
        ema50_slope=(row.ema_50 - past.ema_50) / (Decimal(config.slope_lookback) * row.close),
        close_vs_ema20=(row.close - row.ema_20) / row.close,
        close_vs_ema50=(row.close - row.ema_50) / row.close,
        rsi_14=row.rsi_14, macd=row.macd, macd_signal=row.macd_signal,
        macd_histogram=row.macd_histogram, normalized_macd_histogram=row.macd_histogram / row.close,
        normalized_atr=normalized_atr, realized_volatility=row.realized_volatility,
        volatility_baseline=baseline, volatility_ratio=row.realized_volatility / max(baseline, config.volatility_baseline_floor),
        normalized_atr_baseline=atr_baseline, atr_ratio=normalized_atr / max(atr_baseline, config.atr_baseline_floor),
        reference_low=low, reference_high=high,
        upside_displacement=(row.close - high) / row.close, downside_displacement=(low - row.close) / row.close,
        upside_atr_displacement=(row.close - high) / row.atr_14 if row.atr_14 > 0 else None,
        downside_atr_displacement=(low - row.close) / row.atr_14 if row.atr_14 > 0 else None)
