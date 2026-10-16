from collections.abc import Sequence
from datetime import datetime, timezone
from app.market_intelligence.numeric import deterministic
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_intelligence.primitives import atr, ema, macd, realized_volatility, returns, rsi
from app.market_intelligence.types import Candle, DatasetContext, FeatureRow
from app.market_intelligence.validation import validate_dataset


@deterministic
def calculate_features(context: DatasetContext, candles: Sequence[Candle], *, as_of: datetime) -> list[FeatureRow]:
    rows = validate_dataset(context, candles, as_of)
    closes = [row.close for row in rows]
    ema20, ema50, rsi14 = ema(closes, FEATURE_CONFIG.ema_periods[0]), ema(closes, FEATURE_CONFIG.ema_periods[1]), rsi(closes, FEATURE_CONFIG.rsi_period)
    atr14 = atr([row.high for row in rows], [row.low for row in rows], closes, FEATURE_CONFIG.atr_period)
    line, signal, histogram = macd(closes)
    simple, logarithmic = returns(closes)
    volatility = realized_volatility(closes, FEATURE_CONFIG.realized_volatility_window)
    return [FeatureRow(canonical_symbol=context.canonical_symbol, provider=context.provider, timeframe=context.timeframe,
        open_time=row.open_time.astimezone(timezone.utc), close_time=row.close_time.astimezone(timezone.utc), close=row.close,
        ema_20=ema20[index], ema_50=ema50[index], rsi_14=rsi14[index], atr_14=atr14[index],
        macd=line[index], macd_signal=signal[index], macd_histogram=histogram[index],
        simple_return=simple[index], log_return=logarithmic[index], realized_volatility=volatility[index])
        for index, row in enumerate(rows)]
