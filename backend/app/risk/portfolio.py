"""Input snapshots only. Unknown measurements are None, never fabricated zero."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

from .canonical import decimal, identity, require, seal, symbol, timestamp
from .context import PortfolioScope
from .vocabulary import Direction, RiskState


@dataclass(frozen=True, slots=True)
class PositionRiskSnapshot:
    position_id: UUID
    instrument_id: UUID
    canonical_symbol: str
    direction: Direction
    quantity: Decimal
    strategy_configuration_identity: str
    mark_price: Decimal | None
    exposure_amount: Decimal | None
    open_risk_amount: Decimal | None
    position_identity: str = field(init=False)

    def __post_init__(self):
        require(self.position_id, UUID, "position_id")
        require(self.instrument_id, UUID, "instrument_id")
        symbol(self.canonical_symbol)
        require(self.direction, Direction, "direction")
        decimal(self.quantity, "quantity", positive=True)
        identity(self.strategy_configuration_identity, "strategy configuration")
        for name in ("mark_price", "exposure_amount", "open_risk_amount"):
            value = getattr(self, name)
            if value is not None:
                decimal(value, name, positive=name == "mark_price")
        seal(self, "position-snapshot", "position_identity")


@dataclass(frozen=True, slots=True)
class PortfolioRiskSnapshot:
    scope: PortfolioScope
    as_of: datetime
    expires_at: datetime
    valuation_identity: str
    equity: Decimal
    available_cash: Decimal
    risk_state: RiskState
    open_positions: tuple[PositionRiskSnapshot, ...] | None = None
    open_risk: Decimal | None = None
    gross_exposure: Decimal | None = None
    long_exposure: Decimal | None = None
    short_exposure: Decimal | None = None
    correlated_exposure: Decimal | None = None
    instrument_concentration: Mapping[str, Decimal | None] | None = None
    strategy_concentration: Mapping[str, Decimal | None] | None = None
    drawdown_fraction: Decimal | None = None
    snapshot_identity: str = field(init=False)

    def __post_init__(self):
        require(self.scope, PortfolioScope, "scope")
        require(self.risk_state, RiskState, "risk_state")
        identity(self.valuation_identity, "valuation_identity")
        for name in ("as_of", "expires_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if self.expires_at <= self.as_of:
            raise ValueError("Snapshot expiry must follow its observation")
        for name in ("equity", "available_cash", "open_risk", "gross_exposure", "long_exposure",
                     "short_exposure", "correlated_exposure", "drawdown_fraction"):
            value = getattr(self, name)
            if value is not None:
                decimal(value, name, fraction=name == "drawdown_fraction")
            elif name in ("equity", "available_cash"):
                raise ValueError(name + " is required")
        if self.open_positions is not None:
            if not isinstance(self.open_positions, (tuple, list)):
                raise ValueError("Positions require an explicit sequence or None")
            rows = tuple(self.open_positions)
            for row in rows:
                require(row, PositionRiskSnapshot, "position")
            if len({row.position_id for row in rows}) != len(rows):
                raise ValueError("Duplicate position identifier")
            object.__setattr__(self, "open_positions", tuple(sorted(rows, key=lambda row: str(row.position_id))))
        for name in ("instrument_concentration", "strategy_concentration"):
            mapping = getattr(self, name)
            if mapping is not None:
                if not isinstance(mapping, Mapping):
                    raise ValueError("Concentration requires a mapping or None")
                for key, value in mapping.items():
                    symbol(key) if name == "instrument_concentration" else identity(key, "strategy concentration")
                    if value is not None:
                        decimal(value, name, fraction=True)
                object.__setattr__(self, name, MappingProxyType(dict(mapping)))
        seal(self, "portfolio-snapshot", "snapshot_identity")
