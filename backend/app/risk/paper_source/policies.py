"""Explicit founder-approved PAPER hypotheses; not venue fee claims."""
from datetime import timedelta
from decimal import Decimal as D
from types import MappingProxyType
from app.risk.vocabulary import Environment as E
from app.risk.portfolio_risk import PortfolioRiskPolicy
from app.risk.risk_states import RiskStatePolicy
from app.risk.safety_gate import SafetyGatePolicy, StrategyHealth
from app.risk.trade_risk import TradeRiskCosts, TradeSizeConstraints
from app.strategy_lab.lifecycle import StrategyState

MARKET_POLICY_VERSION = "paper-market-validity-v1"
MARKET_MAX_AGE = MappingProxyType({"15m":timedelta(minutes=5),"1h":timedelta(minutes=10),
    "4h":timedelta(minutes=20),"1d":timedelta(minutes=60)})
# Adaptive Trend is single-timeframe. The declared setup timeframe is used;
# no unused 4h/15m/1d confirmation evidence is manufactured.
SYMBOL, PROVIDER, TIMEFRAME, HISTORY = "BTC/USDT", "BINANCE", "1h", 100
COST_VERSION = "paper-costs-v1"
COSTS = TradeRiskCosts(D(".001"),D(".001"),D(".001"),D(".001"),D(".0005"),D(".0005"))
CONSTRAINTS = TradeSizeConstraints(None,None,None,None,None,None)
PORTFOLIO_POLICY = PortfolioRiskPolicy("paper-portfolio-risk-v1",D(".80"),D(".70"),
    timedelta(hours=24),timedelta(hours=12))
STATE_POLICY = RiskStatePolicy("paper-risk-state-v1",D(".02"),D(".04"),D(".06"),
    D(".015"),D(".035"),D(".055"),D("1.00"),D(".75"),D(".50"),D("0"),timedelta(minutes=5))
SAFETY_POLICY = SafetyGatePolicy("paper-safety-v1",
    {E.LAB:(),E.PAPER:(StrategyState.PAPER,),E.SHADOW:(),E.LIVE:()},
    (StrategyHealth.HEALTHY,),timedelta(minutes=5),timedelta(minutes=5),
    timedelta(minutes=15),MARKET_MAX_AGE[TIMEFRAME],timedelta(hours=24),
    None,None,None,None,None,None,False,None)
ACTIVATION_VERSION = "paper-controlled-activation-v1"
