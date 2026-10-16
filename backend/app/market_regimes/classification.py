from dataclasses import dataclass, replace
from app.market_regimes.config import RegimeConfiguration
from app.market_regimes.types import Evidence, Regime


def classify_evidence(evidence: Evidence, config: RegimeConfiguration) -> tuple[Regime, Evidence]:
    e, c = evidence, config
    momentum_up = e.rsi_14 >= c.momentum_up_rsi and e.macd > 0 and e.macd >= e.macd_signal and e.macd_histogram >= 0
    momentum_down = e.rsi_14 <= c.momentum_down_rsi and e.macd < 0 and e.macd <= e.macd_signal and e.macd_histogram <= 0
    structure_up = (e.normalized_ema_spread >= c.trend_spread and e.ema20_slope >= c.trend_fast_slope
        and e.ema50_slope >= c.trend_slow_slope and e.close_vs_ema20 > 0 and e.close_vs_ema50 > 0)
    structure_down = (e.normalized_ema_spread <= -c.trend_spread and e.ema20_slope <= -c.trend_fast_slope
        and e.ema50_slope <= -c.trend_slow_slope and e.close_vs_ema20 < 0 and e.close_vs_ema50 < 0)
    reasons = []
    if momentum_up: reasons.append("MOMENTUM_UP_CONFIRMED")
    if momentum_down: reasons.append("MOMENTUM_DOWN_CONFIRMED")
    if structure_up: reasons.append("STRUCTURE_UP")
    if structure_down: reasons.append("STRUCTURE_DOWN")
    if (max(e.volatility_ratio, e.atr_ratio) >= c.high_volatility_ratio
        and e.normalized_atr >= c.high_volatility_atr_floor
        and e.realized_volatility >= c.high_volatility_return_floor):
        candidate = Regime.HIGH_VOLATILITY
        reasons.append("VOLATILITY_ELEVATED")
    else:
        volatility_confirmation = (e.normalized_atr >= c.breakout_atr_floor
            and (e.volatility_ratio >= c.breakout_volatility_ratio or e.atr_ratio >= c.breakout_atr_ratio))
        up = (e.upside_displacement >= c.breakout_displacement and e.upside_atr_displacement is not None
            and e.upside_atr_displacement >= c.breakout_atr_displacement and momentum_up and volatility_confirmation)
        down = (e.downside_displacement >= c.breakout_displacement and e.downside_atr_displacement is not None
            and e.downside_atr_displacement >= c.breakout_atr_displacement and momentum_down and volatility_confirmation)
        if up or down:
            candidate = Regime.BREAKOUT
            evidence = replace(evidence, breakout_direction="UP" if up else "DOWN")
            reasons.extend(("BREAKOUT_UP" if up else "BREAKOUT_DOWN", "BREAKOUT_VOLATILITY_CONFIRMED"))
        elif structure_up and momentum_up:
            candidate = Regime.BULL_TREND
            reasons.append("TREND_UP_EVIDENCE")
        elif structure_down and momentum_down:
            candidate = Regime.BEAR_TREND
            reasons.append("TREND_DOWN_EVIDENCE")
        elif (abs(e.normalized_ema_spread) <= c.range_spread and abs(e.ema20_slope) <= c.range_fast_slope
            and abs(e.ema50_slope) <= c.range_slow_slope and c.range_rsi_low <= e.rsi_14 <= c.range_rsi_high
            and abs(e.normalized_macd_histogram) <= c.range_histogram
            and e.normalized_atr <= c.range_atr_ceiling and e.realized_volatility <= c.range_volatility_ceiling):
            candidate = Regime.RANGE
            reasons.append("RANGE_STRUCTURE")
        else:
            candidate = Regime.UNKNOWN
            reasons.append("CONFLICTING_EVIDENCE" if (structure_up or structure_down or momentum_up or momentum_down) else "INSUFFICIENT_EVIDENCE")
    return candidate, replace(evidence, candidate=candidate, reason_codes=tuple(reasons))


@dataclass
class Confirmation:
    candidate: tuple | None = None
    count: int = 0
    established: Regime | None = None

    def apply(self, candidate: Regime, evidence: Evidence, config: RegimeConfiguration) -> tuple[Regime, Evidence]:
        previous = self.established
        if candidate == Regime.UNKNOWN:
            self.candidate, self.count, self.established = None, 0, None
            return candidate, replace(evidence, previous_regime=previous)
        key = (candidate, evidence.breakout_direction)
        self.count = self.count + 1 if key == self.candidate else 1
        self.candidate = key
        required = (config.high_volatility_confirmation if candidate == Regime.HIGH_VOLATILITY else
                    config.breakout_confirmation if candidate == Regime.BREAKOUT else config.persistence_window)
        reasons = evidence.reason_codes
        retained = False
        if self.count >= required:
            output = candidate
            self.established = candidate
            reasons += (("TREND_UP_CONFIRMED" if candidate == Regime.BULL_TREND else
                        "TREND_DOWN_CONFIRMED" if candidate == Regime.BEAR_TREND else "REGIME_CONFIRMED"),)
        else:
            ordinary = (Regime.BULL_TREND, Regime.BEAR_TREND, Regime.RANGE)
            retained = previous in ordinary and candidate in ordinary
            output = previous if retained else Regime.UNKNOWN
            if not retained: self.established = None
            reasons += ("CONFIRMATION_PENDING",) + (("PERSISTENCE_RETAINED",) if retained else ())
        return output, replace(evidence, confirmation_count=self.count, confirmation_required=required,
            previous_regime=previous, retained_previous=retained, reason_codes=reasons)
