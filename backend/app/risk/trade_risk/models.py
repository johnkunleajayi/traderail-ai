"""Immutable mathematical evidence; a gate pass is never a final RiskDecision."""
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum

from app.risk.canonical import decimal, identity, require, seal, timestamp
from app.risk.reasons import RiskFinding, RiskReason
from app.risk.vocabulary import RiskOutcome

SIZING_VERSION = "1.0.0"


class TradeRiskStatus(str, Enum):
    TRADE_RISK_PASS = "TRADE_RISK_PASS"
    TRADE_RISK_REJECTED = "TRADE_RISK_REJECTED"


class SizingConstraint(str, Enum):
    RISK_BUDGET = "RISK_BUDGET"
    OPERATING_RISK_LIMIT = "OPERATING_RISK_LIMIT"
    PLATFORM_RISK_LIMIT = "PLATFORM_RISK_LIMIT"
    NOTIONAL_LIMIT = "NOTIONAL_LIMIT"
    AVAILABLE_CASH = "AVAILABLE_CASH"
    MAXIMUM_QUANTITY = "MAXIMUM_QUANTITY"
    QUANTITY_INCREMENT = "QUANTITY_INCREMENT"
    MINIMUM_QUANTITY = "MINIMUM_QUANTITY"


class TradeRiskCalculationError(ValueError):
    def __init__(self, code: RiskReason, message: str):
        require(code, RiskReason, "reason code")
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class UnitLossModel:
    assumed_entry_price: Decimal
    invalidation_price: Decimal
    adverse_price_loss: Decimal
    modeled_adverse_entry_price: Decimal
    modeled_adverse_exit_price: Decimal
    entry_spread_cost: Decimal
    entry_slippage_cost: Decimal
    entry_fee: Decimal
    exit_spread_cost: Decimal
    exit_slippage_cost: Decimal
    exit_fee: Decimal
    total_entry_cost: Decimal
    total_exit_cost: Decimal
    total_loss: Decimal
    model_identity: str = field(init=False)

    def __post_init__(self):
        for item in fields(self):
            if item.init:
                decimal(getattr(self, item.name), item.name,
                        positive=item.name in ("assumed_entry_price", "invalidation_price", "adverse_price_loss",
                                               "modeled_adverse_entry_price", "modeled_adverse_exit_price", "total_loss"))
        seal(self, "unit-loss-" + SIZING_VERSION, "model_identity")


@dataclass(frozen=True, slots=True)
class SizeProjection:
    quantity: Decimal
    reference_notional: Decimal
    notional: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    modeled_loss: Decimal
    required_cash: Decimal
    projection_identity: str = field(init=False)

    def __post_init__(self):
        for item in fields(self):
            if item.init:
                decimal(getattr(self, item.name), item.name)
        seal(self, "size-projection-" + SIZING_VERSION, "projection_identity")


@dataclass(frozen=True, slots=True)
class TradeSizingResult:
    intent_identity: str
    configuration_identity: str
    snapshot_identity: str
    assumptions_identity: str
    strategy_reference_price: Decimal
    effective_risk_fraction: Decimal
    permitted_monetary_risk: Decimal
    loss_model: UnitLossModel
    raw_max_quantity: Decimal
    constrained_max_quantity: Decimal
    notional_limit: Decimal
    available_cash_limit: Decimal
    maximum_projection: SizeProjection
    binding_constraints: tuple[SizingConstraint, ...]
    version: str = field(default=SIZING_VERSION, init=False)
    calculation_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("intent_identity", "configuration_identity", "snapshot_identity", "assumptions_identity"):
            identity(getattr(self, name), name)
        require(self.loss_model, UnitLossModel, "loss_model")
        require(self.maximum_projection, SizeProjection, "maximum_projection")
        decimal(self.strategy_reference_price, "strategy_reference_price", positive=True)
        for name in ("effective_risk_fraction", "permitted_monetary_risk", "raw_max_quantity",
                     "constrained_max_quantity", "notional_limit", "available_cash_limit"):
            decimal(getattr(self, name), name, fraction=name == "effective_risk_fraction")
        if (self.constrained_max_quantity > self.raw_max_quantity
                or self.maximum_projection.quantity != self.constrained_max_quantity
                or self.maximum_projection.modeled_loss > self.permitted_monetary_risk
                or self.maximum_projection.notional > self.notional_limit
                or self.maximum_projection.required_cash > self.available_cash_limit):
            raise ValueError("Conservative maximum sizing invariant violated")
        if not isinstance(self.binding_constraints, tuple):
            raise ValueError("Binding constraints must be an immutable tuple")
        for binding in self.binding_constraints:
            require(binding, SizingConstraint, "binding constraint")
        object.__setattr__(self, "binding_constraints", tuple(sorted(set(self.binding_constraints), key=lambda v: v.value)))
        seal(self, "trade-sizing-" + SIZING_VERSION, "calculation_identity")


@dataclass(frozen=True, slots=True)
class TradeRiskEvaluation:
    intent_identity: str
    configuration_identity: str
    snapshot_identity: str
    assumptions_identity: str
    evaluated_at: datetime
    status: TradeRiskStatus
    sizing: TradeSizingResult | None
    evaluated_projection: SizeProjection | None
    findings: tuple[RiskFinding, ...]
    scope: str = field(default="TRADE_RISK_ONLY", init=False)
    version: str = field(default=SIZING_VERSION, init=False)
    evaluation_identity: str = field(init=False)

    def __post_init__(self):
        for name in ("intent_identity", "configuration_identity", "snapshot_identity", "assumptions_identity"):
            identity(getattr(self, name), name)
        object.__setattr__(self, "evaluated_at", timestamp(self.evaluated_at))
        require(self.status, TradeRiskStatus, "status")
        if not isinstance(self.findings, tuple) or not self.findings:
            raise ValueError("Typed immutable findings are required")
        for finding in self.findings:
            require(finding, RiskFinding, "finding")
        if len({item.code for item in self.findings}) != len(self.findings):
            raise ValueError("Duplicate trade-risk reason")
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda f: f.code.value)))
        if self.sizing is not None:
            require(self.sizing, TradeSizingResult, "sizing")
            for name in ("intent_identity", "configuration_identity", "snapshot_identity", "assumptions_identity"):
                if getattr(self.sizing, name) != getattr(self, name):
                    raise ValueError("Sizing must bind the same evaluated inputs")
        if self.evaluated_projection is not None:
            require(self.evaluated_projection, SizeProjection, "evaluated_projection")
        rejected = any(f.code.outcome is RiskOutcome.REJECTED for f in self.findings)
        if self.status is TradeRiskStatus.TRADE_RISK_PASS:
            if (rejected or self.sizing is None or self.evaluated_projection is None
                    or self.evaluated_projection.quantity <= 0):
                raise ValueError("Trade-risk pass requires nonzero sizing and no rejection findings")
            p = self.evaluated_projection
            if (p.quantity > self.sizing.constrained_max_quantity
                    or p.modeled_loss > self.sizing.permitted_monetary_risk
                    or p.notional > self.sizing.notional_limit
                    or p.required_cash > self.sizing.available_cash_limit):
                raise ValueError("Trade-risk pass cannot exceed computed trade-level bounds")
        elif not rejected:
            raise ValueError("Trade-risk rejection requires a rejection finding")
        seal(self, "trade-risk-evaluation-" + SIZING_VERSION, "evaluation_identity")
