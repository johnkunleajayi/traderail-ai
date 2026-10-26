"""Frozen owner-facing PAPER outcomes, reusing the existing execution response."""
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID
from pydantic import ConfigDict, Field, model_validator
from app.mandate_eligibility.contracts import Frozen
from app.mandate_strategy.contracts import ObservationOutcome
from app.mandate_risk.contracts import MandateRiskError
from app.execution.api.schemas import PaperExecutionResponse, UTCDate
from app.risk.vocabulary import RiskOutcome
from app.strategies.decisions import DecisionType

class PaperRunStatus(StrEnum):
    NO_TRADE="NO_TRADE"
    RISK_REJECTED="RISK_REJECTED"
    PAPER_EXECUTED="PAPER_EXECUTED"

class EntryExecution(PaperExecutionResponse):
    model_config=ConfigDict(frozen=True,extra="forbid")

class PaperRunResult(Frozen):
    scope: Literal["MANDATE_PAPER_ENTRY_ONLY"]="MANDATE_PAPER_ENTRY_ONLY"
    status: PaperRunStatus
    mandate_identity: UUID
    eligibility_identity: str
    strategy_observation_identity: str
    strategy_decision_identity: str
    strategy_decision: DecisionType
    strategy_outcome: ObservationOutcome
    capital_allocation: Decimal = Field(gt=0,allow_inf_nan=False)
    allocation_currency: Literal["USDT"]="USDT"
    risk_outcome: RiskOutcome | None=None
    intent_identity: str | None=None
    decision_identity: str | None=None
    registry_identity: str | None=None
    authorization_identity: str | None=None
    approved_quantity: Decimal | None=None
    execution: EntryExecution | None=None
    reused: bool=False
    reason_codes: tuple[str,...]=()

    @model_validator(mode="after")
    def boundaries(self):
        if self.status is PaperRunStatus.NO_TRADE:
            if self.strategy_outcome is not ObservationOutcome.NO_TRADE or any(x is not None for x in (self.risk_outcome,self.intent_identity,self.decision_identity,self.execution,self.registry_identity,self.authorization_identity,self.approved_quantity)):
                raise ValueError("NO_TRADE stops before authorization/execution")
        elif self.status is PaperRunStatus.RISK_REJECTED:
            if self.risk_outcome is not RiskOutcome.REJECTED or self.intent_identity is None or self.decision_identity is None or any(x is not None for x in (self.execution,self.registry_identity,self.authorization_identity,self.approved_quantity)):
                raise ValueError("Rejected Risk cannot execute")
        else:
            e=self.execution
            if (self.risk_outcome is not RiskOutcome.APPROVED or self.strategy_decision is not DecisionType.ENTER_LONG
                or self.strategy_outcome is not ObservationOutcome.ENTER_LONG or e is None
                or any(x is None for x in (self.registry_identity,self.authorization_identity,self.approved_quantity))
                or e.action!="ENTER_LONG" or e.position_effect!="OPENED" or e.currency!="USDT"
                or e.quantity!=self.approved_quantity or e.intent_identity!=self.intent_identity or e.decision_identity!=self.decision_identity):
                raise ValueError("Execution must be the exact approved PAPER entry")
        return self

class PaperRunError(MandateRiskError):
    pass


class PositionRunResult(Frozen):
    """Position management evidence; the acquisition identity remains immutable."""
    scope: Literal["MANDATE_PAPER_POSITION"] = "MANDATE_PAPER_POSITION"
    environment: Literal["PAPER"] = "PAPER"
    status: Literal["POSITION_HELD", "EXIT_RISK_REJECTED", "POSITION_CLOSED"]
    mandate_identity: UUID
    position_id: UUID
    position_identity: str
    opening_registry_identity: str
    opening_eligibility_identity: str
    evaluated_at: UTCDate
    strategy_timestamp: UTCDate
    opening_intent_identity: str
    opening_decision_identity: str
    capital_allocation: Decimal = Field(gt=0, allow_inf_nan=False)
    allocation_currency: Literal["USDT"] = "USDT"
    strategy_decision: DecisionType
    strategy_decision_identity: str
    strategy_input_identity: str
    risk_outcome: RiskOutcome | None = None
    intent_identity: str | None = None
    decision_identity: str | None = None
    registry_identity: str | None = None
    authorization_identity: str | None = None
    approved_quantity: Decimal | None = None
    execution: EntryExecution | None = None
    reused: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def position_boundary(self):
        if self.status == "POSITION_HELD":
            if self.strategy_decision is not DecisionType.NO_ACTION or any(x is not None for x in (
                self.risk_outcome, self.intent_identity, self.decision_identity, self.registry_identity,
                self.authorization_identity, self.approved_quantity, self.execution)):
                raise ValueError("HOLD cannot authorize or execute a close")
        elif self.status == "EXIT_RISK_REJECTED":
            if (self.strategy_decision is not DecisionType.EXIT_LONG or self.risk_outcome is not RiskOutcome.REJECTED
                or self.intent_identity is None or self.decision_identity is None
                or any(x is not None for x in (self.registry_identity, self.authorization_identity, self.approved_quantity, self.execution))):
                raise ValueError("Rejected exit cannot execute")
        else:
            e = self.execution
            if (self.strategy_decision is not DecisionType.EXIT_LONG or self.risk_outcome is not RiskOutcome.APPROVED
                or any(x is None for x in (self.registry_identity, self.authorization_identity, self.approved_quantity))
                or e is None or e.action != "EXIT_LONG" or e.position_effect != "CLOSED" or e.currency != "USDT"
                or e.position_id != self.position_id or e.quantity != self.approved_quantity
                or e.intent_identity != self.intent_identity or e.decision_identity != self.decision_identity):
                raise ValueError("Close must be the exact newly authorized position exit")
        return self
