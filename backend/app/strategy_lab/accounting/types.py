"""Immutable quote-currency research accounting and provenance."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from app.market_intelligence.types import DatasetContext
from app.strategies.inputs import PositionState
from app.strategy_lab.costs import CostBreakdown
from app.strategy_lab.metrics import RealizedPnlTotals
from app.strategy_lab.replay.types import ReplayResult
from app.strategy_lab.execution.types import ExecutionSimulation, SimulatedFill
from .config import AccountingConfiguration


@dataclass(frozen=True, slots=True)
class CompletedTrade:
    entry_fill: SimulatedFill
    exit_fill: SimulatedFill
    direction: PositionState
    quantity: Decimal
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_reference_price: Decimal
    exit_reference_price: Decimal
    entry_fill_price: Decimal
    exit_fill_price: Decimal
    gross_pnl: Decimal
    costs: CostBreakdown
    net_pnl: Decimal
    dataset_prefix_fingerprint: str
    strategy_family: str
    strategy_version: str
    strategy_configuration_identity: str
    execution_configuration_identity: str
    trade_identity: str


@dataclass(frozen=True, slots=True)
class EquityObservation:
    timestamp: datetime
    context: DatasetContext
    cash: Decimal
    reserved_capital: Decimal
    restricted_short_proceeds: Decimal
    realized_capital: Decimal
    gross_realized_pnl: Decimal
    realized_pnl: Decimal
    completed_trade_net_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal
    incurred_costs: CostBreakdown
    position_state: PositionState
    quantity: Decimal
    mark_price: Decimal
    mark_open_time: datetime
    price_source: str
    dataset_prefix_fingerprint: str
    applied_fill_identities: tuple[str, ...]
    observation_identity: str


@dataclass(frozen=True, slots=True)
class OpenPosition:
    direction: PositionState
    quantity: Decimal
    entry_price: Decimal
    entry_reference_price: Decimal
    entry_fill: SimulatedFill
    entry_fee_recognized: Decimal
    mark_price: Decimal
    mark_timestamp: datetime
    unrealized_pnl: Decimal
    reserved_capital: Decimal
    restricted_short_proceeds: Decimal


@dataclass(frozen=True, slots=True)
class PerformanceResults:
    total_return: Decimal | None
    gross_return: Decimal | None
    net_return: Decimal | None
    maximum_drawdown: Decimal | None
    trade_count: int
    win_rate: Decimal | None
    loss_rate: Decimal | None
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    expectancy: Decimal | None
    exposure_percentage: Decimal | None
    turnover: Decimal | None
    total_fees: Decimal
    total_spread_cost: Decimal
    total_slippage_cost: Decimal


@dataclass(frozen=True, slots=True)
class ResearchResult:
    dataset_fingerprint: str
    context: DatasetContext
    accounting_configuration: AccountingConfiguration
    initial_capital: Decimal
    replay: ReplayResult
    execution: ExecutionSimulation
    completed_trades: tuple[CompletedTrade, ...]
    open_position: OpenPosition | None
    equity_curve: tuple[EquityObservation, ...]
    completed_trade_totals: RealizedPnlTotals
    incurred_costs: CostBreakdown
    performance: PerformanceResults
    result_identity: str
