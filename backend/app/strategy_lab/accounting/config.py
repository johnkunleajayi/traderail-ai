"""Versioned accounting conventions; not a venue margin specification."""
from dataclasses import dataclass, asdict
from app.strategy_lab.execution.config import identity


@dataclass(frozen=True, slots=True)
class AccountingConfiguration:
    version: str = "1.0.0"
    denomination: str = "canonical_quote_asset"
    marking: str = "completed_evaluation_candle_close"
    fee_recognition: str = "incurred_at_fill"
    realized_returns: str = "completed_trades_only"
    drawdown_baseline: str = "explicit_initial_capital_then_evaluation_equity"
    short_collateral: str = "own_entry_reference_notional_reserved_proceeds_restricted"
    short_exposure_limit: str = "one_times_equity_at_entry_reference_and_completed_marks"
    initial_position: str = "CASH_only_no_fabricated_entry_basis"

    @property
    def configuration_identity(self):
        return identity("strategy-lab.accounting-configuration/v1", asdict(self))


ACCOUNTING_CONFIG = AccountingConfiguration()
