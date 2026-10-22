"""Pure, full-quantity PAPER calculation evidence. No Order/Fill event is created."""
from dataclasses import dataclass, field
from datetime import datetime
from app.risk.canonical import require, timestamp
from app.risk.vocabulary import Direction
from app.strategies.decisions import DecisionType
from ..canonical import execution_identity
from ..request import ExecutionRequest
from ..vocabulary import ExecutionEnvironment
from .context import PaperMarketEvidence, PaperFillAssumptions
from .pricing import PaperLegCalculation


@dataclass(frozen=True, slots=True)
class PaperFillCalculation:
    request: ExecutionRequest
    market: PaperMarketEvidence
    assumptions: PaperFillAssumptions
    action: DecisionType
    executed_at: datetime
    evaluated_at: datetime
    eligible_at: datetime = field(init=False)
    leg: PaperLegCalculation = field(init=False)
    calculation_identity: str = field(init=False)

    def __post_init__(self):
        require(self.request, ExecutionRequest, "PAPER request")
        require(self.market, PaperMarketEvidence, "market evidence")
        require(self.assumptions, PaperFillAssumptions, "assumptions")
        require(self.action, DecisionType, "action")
        r, m = self.request, self.market
        if r.environment is not ExecutionEnvironment.PAPER or m.environment is not ExecutionEnvironment.PAPER:
            raise ValueError("Paper fill calculation is PAPER only")
        if m.request_identity != r.request_identity or m.instrument != r.authorization.intent.instrument:
            raise ValueError("Paper market evidence does not match the exact request/instrument context")
        if m.is_valid is not True: raise ValueError("Trusted market validity is unavailable or false")
        # A close must be explicitly bound into the exact Risk-approved intent.
        if self.action is not r.action:
            raise ValueError("Request does not authorize this exact action")
        for name in ("executed_at", "evaluated_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if not r.created_at <= self.executed_at <= self.evaluated_at:
            raise ValueError("Execution/evaluation times must be causal and explicit")
        r.authorization.validate_at(self.executed_at)
        r.authorization.validate_at(self.evaluated_at)
        if m.observed_at > self.executed_at:
            raise ValueError("Future market evidence cannot determine an earlier execution price")
        if self.evaluated_at >= m.valid_until or self.evaluated_at - m.priced_at > self.assumptions.max_market_age:
            raise ValueError("Market evidence is expired or stale")
        try:
            eligible = r.created_at + self.assumptions.cost_model.execution_delay
        except (ArithmeticError, OverflowError):
            raise ValueError("Paper execution delay cannot be evaluated safely") from None
        if self.executed_at < eligible: raise ValueError("Declared execution delay has not elapsed")
        leg = PaperLegCalculation(r.authorization.intent.direction, self.action, m.reference_price,
                                 r.quantity, self.assumptions)
        object.__setattr__(self, "eligible_at", eligible)
        object.__setattr__(self, "leg", leg)
        object.__setattr__(self, "calculation_identity", execution_identity("paper-fill-calculation-1.0.0", {
            "version":"1.0.0", "scope":"CALCULATION_ONLY", "request":r,
            "market":m, "assumptions":self.assumptions, "action":self.action,
            "executed_at":self.executed_at, "evaluated_at":self.evaluated_at,
            "eligible_at":eligible, "calculated_result":leg}))


def calculate_paper_fill(request, market, assumptions, action, *, executed_at, evaluated_at):
    """Return auditable calculation evidence only; never submit, consume or persist."""
    return PaperFillCalculation(request, market, assumptions, action, executed_at, evaluated_at)
