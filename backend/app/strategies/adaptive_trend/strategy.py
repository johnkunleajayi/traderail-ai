"""Stateless Adaptive Trend opportunity decisions using existing analytical data."""
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from types import MappingProxyType
from app.market_regimes.types import Regime
from app.strategies.configuration import StrategyDefinition
from app.strategies.decisions import DecisionType, StrategyDecision
from app.strategies.inputs import PositionState, StrategyInput
from .config import AdaptiveTrendConfiguration, DEFAULT_CONFIGURATION, STRATEGY_FAMILY, STRATEGY_VERSION

REQUIRED_EVIDENCE = ("normalized_ema_spread", "ema20_slope", "ema50_slope",
                     "normalized_macd_histogram", "normalized_atr", "volatility_ratio", "atr_ratio")
NONNEGATIVE_EVIDENCE = ("normalized_atr", "volatility_ratio", "atr_ratio")
ENTRY_FAILURE_CODES = MappingProxyType({"regime": "REGIME_NOT_CONFIRMED", "structure": "EMA_STRUCTURE_NOT_CONFIRMED",
    "slopes": "EMA_SLOPES_NOT_CONFIRMED", "momentum": "MOMENTUM_NOT_CONFIRMED",
    "close_position": "CLOSE_POSITION_NOT_CONFIRMED", "volatility": "VOLATILITY_TOO_HIGH"})


@dataclass(frozen=True, slots=True)
class AdaptiveTrendStrategy:
    configuration: AdaptiveTrendConfiguration = DEFAULT_CONFIGURATION
    definition: StrategyDefinition = field(init=False)

    def __post_init__(self):
        if not isinstance(self.configuration, AdaptiveTrendConfiguration):
            raise ValueError("A typed Adaptive Trend configuration is required")
        object.__setattr__(self, "definition", StrategyDefinition(STRATEGY_FAMILY, STRATEGY_VERSION,
                                                                 asdict(self.configuration)))

    def _confirmed(self, observation: StrategyInput, direction: Regime) -> bool:
        evidence = observation.regime.evidence
        return (observation.regime.regime == direction and evidence.candidate == direction
            and not evidence.retained_previous and evidence.confirmation_required > 0
            and evidence.confirmation_count >= max(self.configuration.minimum_regime_confirmations,
                                                   evidence.confirmation_required)
            and evidence.candidate_history_required > 0
            and evidence.available_candles >= evidence.candidate_history_required)

    def _entry_checks(self, observation: StrategyInput, long: bool) -> dict[str, bool]:
        f, e, c = observation.intelligence, observation.regime.evidence, self.configuration
        if long:
            structure = f.ema_20 > f.ema_50 and e.normalized_ema_spread >= c.entry_min_ema_spread
            slopes = e.ema20_slope >= c.entry_fast_slope and e.ema50_slope >= c.entry_slow_slope
            momentum = (c.long_rsi_min <= f.rsi_14 <= c.long_rsi_max and f.macd > 0
                and f.macd > f.macd_signal and f.macd_histogram > 0
                and e.normalized_macd_histogram >= c.entry_min_normalized_histogram)
            close_position = f.close > f.ema_20 and f.close > f.ema_50
        else:
            structure = f.ema_20 < f.ema_50 and e.normalized_ema_spread <= c.entry_min_ema_spread.copy_negate()
            slopes = e.ema20_slope <= c.entry_fast_slope.copy_negate() and e.ema50_slope <= c.entry_slow_slope.copy_negate()
            momentum = (c.short_rsi_min <= f.rsi_14 <= c.short_rsi_max and f.macd < 0
                and f.macd < f.macd_signal and f.macd_histogram < 0
                and e.normalized_macd_histogram <= c.entry_min_normalized_histogram.copy_negate())
            close_position = f.close < f.ema_20 and f.close < f.ema_50
        return {"regime": self._confirmed(observation, Regime.BULL_TREND if long else Regime.BEAR_TREND),
            "structure": structure, "slopes": slopes, "momentum": momentum, "close_position": close_position,
            "volatility": (e.normalized_atr <= c.max_normalized_atr
                and f.realized_volatility <= c.max_realized_volatility
                and e.volatility_ratio <= c.max_volatility_ratio and e.atr_ratio <= c.max_atr_ratio)}

    def _exit_checks(self, observation: StrategyInput, long: bool) -> dict[str, bool]:
        f, e, c = observation.intelligence, observation.regime.evidence, self.configuration
        if long:
            structure = (f.ema_20 <= f.ema_50 and e.normalized_ema_spread <= c.exit_min_ema_spread.copy_negate()
                         and f.close < f.ema_50)
            slopes = (e.ema20_slope <= c.exit_fast_slope.copy_negate()
                and e.ema50_slope <= c.exit_slow_slope.copy_negate() and f.close < f.ema_20)
            momentum = (f.rsi_14 <= c.exit_long_rsi and f.macd < f.macd_signal and f.macd_histogram < 0
                and e.normalized_macd_histogram <= c.exit_min_normalized_histogram.copy_negate())
        else:
            structure = f.ema_20 >= f.ema_50 and e.normalized_ema_spread >= c.exit_min_ema_spread and f.close > f.ema_50
            slopes = e.ema20_slope >= c.exit_fast_slope and e.ema50_slope >= c.exit_slow_slope and f.close > f.ema_20
            momentum = (f.rsi_14 >= c.exit_short_rsi and f.macd > f.macd_signal and f.macd_histogram > 0
                and e.normalized_macd_histogram >= c.exit_min_normalized_histogram)
        return {"structure": structure, "slopes": slopes, "momentum": momentum}

    def decide(self, observation: StrategyInput) -> StrategyDecision:
        if not isinstance(observation, StrategyInput) or observation.definition != self.definition:
            raise ValueError("Adaptive Trend requires its matching validated strategy input")
        f, e, c = observation.intelligence, observation.regime.evidence, self.configuration
        evidence = {"position_state": observation.position_state.value, "regime": observation.regime.regime.value,
            "candidate_regime": e.candidate.value, "confirmation_count": e.confirmation_count,
            "regime_confirmation_required": e.confirmation_required,
            "entry_confirmation_required": max(c.minimum_regime_confirmations, e.confirmation_required),
            "retained_previous": e.retained_previous, "close": f.close, "ema_20": f.ema_20, "ema_50": f.ema_50,
            "rsi_14": f.rsi_14, "macd": f.macd, "macd_signal": f.macd_signal,
            "macd_histogram": f.macd_histogram, "realized_volatility": f.realized_volatility,
            **{name: getattr(e, name) for name in REQUIRED_EVIDENCE}}
        def result(decision, *codes):
            return StrategyDecision(observation, decision, tuple(codes), evidence)
        if observation.regime.regime == Regime.UNSAFE:
            return result(DecisionType.NO_ACTION, "DATA_UNSAFE")
        if observation.regime.regime == Regime.HIGH_VOLATILITY:
            return result(DecisionType.NO_ACTION, "HIGH_VOLATILITY_REGIME")
        if observation.position_state == PositionState.CASH and observation.regime.regime not in (Regime.BULL_TREND, Regime.BEAR_TREND):
            return result(DecisionType.NO_ACTION, "REGIME_NOT_TRENDING")
        if any(getattr(e, name) is None for name in REQUIRED_EVIDENCE):
            return result(DecisionType.NO_ACTION, "ANALYTICAL_EVIDENCE_INCOMPLETE")
        if any(getattr(e, name) < Decimal("0") for name in NONNEGATIVE_EVIDENCE):
            return result(DecisionType.NO_ACTION, "ANALYTICAL_EVIDENCE_INVALID")
        if observation.position_state == PositionState.CASH:
            long = observation.regime.regime == Regime.BULL_TREND
            checks = self._entry_checks(observation, long)
            evidence["entry_confirmations"] = checks
            if all(checks.values()):
                return result(DecisionType.ENTER_LONG if long else DecisionType.ENTER_SHORT,
                              "LONG_TREND_CONFIRMED" if long else "SHORT_TREND_CONFIRMED")
            return result(DecisionType.NO_ACTION, *(ENTRY_FAILURE_CODES[name] for name, passed in checks.items() if not passed))
        long = observation.position_state == PositionState.LONG
        checks = self._exit_checks(observation, long)
        opposite = self._confirmed(observation, Regime.BEAR_TREND if long else Regime.BULL_TREND)
        evidence["exit_confirmations"] = checks
        evidence["opposite_trend_confirmed"] = opposite
        evidence["exit_confirmation_count"] = sum(checks.values())
        evidence["exit_confirmation_required"] = c.exit_confirmation_dimensions
        if opposite or sum(checks.values()) >= c.exit_confirmation_dimensions:
            codes = ["LONG_TREND_INVALIDATED" if long else "SHORT_TREND_INVALIDATED"]
            if opposite:
                codes.append("OPPOSITE_TREND_CONFIRMED")
            codes.extend("EXIT_" + name.upper() + "_CONFIRMED" for name, passed in checks.items() if passed)
            return result(DecisionType.EXIT_LONG if long else DecisionType.EXIT_SHORT, *codes)
        return result(DecisionType.NO_ACTION, "LONG_EXIT_NOT_CONFIRMED" if long else "SHORT_EXIT_NOT_CONFIRMED")
