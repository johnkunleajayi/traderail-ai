"""Owner-facing outcome summaries, not internal gate engineering evidence."""
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from pydantic import Field, model_validator
from app.mandate_strategy.contracts import UtcEvidence, MandateObservationResult, MandateEvaluationError
from app.mandate_eligibility.contracts import Frozen
from app.risk.integrated.models import GateName, GateDisposition
from app.risk.reasons import RiskReason
from app.risk.vocabulary import RiskOutcome

SHA = r"^sha256:[0-9a-f]{64}$"

class MandateRiskStatus(StrEnum):
    NO_TRADE = "NO_TRADE"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"

class GateSummary(Frozen):
    gate: GateName
    outcome: GateDisposition

class RiskSummary(UtcEvidence):
    outcome: RiskOutcome
    decision_identity: str = Field(pattern=SHA)
    configuration_identity: str = Field(pattern=SHA)
    evaluated_at: datetime
    approval_expires_at: datetime | None
    gates: tuple[GateSummary, ...]
    blocking_reason_codes: tuple[RiskReason, ...]
    evaluated_quantity: Decimal | None
    modeled_entry_notional: Decimal | None
    modeled_entry_fee: Decimal | None
    modeled_entry_commitment: Decimal | None
    modeled_risk: Decimal | None

class AuthorizationSummary(UtcEvidence):
    registry_identity: str = Field(pattern=SHA)
    authorization_identity: str = Field(pattern=SHA)
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    approval_expires_at: datetime
    reused: bool
    expired: bool

class MandateRiskResult(Frozen):
    scope: Literal["MANDATE_RISK_AUTHORIZATION_ONLY"] = "MANDATE_RISK_AUTHORIZATION_ONLY"
    policy_version: Literal["mandate-risk-v1"] = "mandate-risk-v1"
    status: MandateRiskStatus
    strategy: MandateObservationResult
    intent_identity: str | None = Field(default=None, pattern=SHA)
    risk: RiskSummary | None = None
    authorization: AuthorizationSummary | None = None

    @model_validator(mode="after")
    def authority(self):
        if self.status is MandateRiskStatus.NO_TRADE:
            if self.strategy.outcome.value != "NO_TRADE" or any(v is not None for v in (self.intent_identity,self.risk,self.authorization)):
                raise ValueError("NO_TRADE must stop before Risk")
        else:
            if self.strategy.outcome.value != "ENTER_LONG" or self.intent_identity is None or self.risk is None:
                raise ValueError("Risk requires an actionable entry")
            if self.status.value != self.risk.outcome.value:
                raise ValueError("Risk outcome mismatch")
            if (self.authorization is not None) != (self.status is MandateRiskStatus.APPROVED):
                raise ValueError("Only approval has durable authorization")
        return self


class MandateRiskError(MandateEvaluationError):
    def __init__(self,code,message,status):
        super().__init__(code,message)
        self.status=status
