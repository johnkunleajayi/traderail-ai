"""Minimal inspection contract: exact decimals, UTC times, identities, no raw state."""
from datetime import timezone
from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, PlainSerializer
from app.risk.vocabulary import RiskOutcome
from app.risk.reasons import RiskReason
from app.risk.integrated.models import GateName, GateDisposition

Financial = Annotated[Decimal, PlainSerializer(lambda x: format(x, "f"), return_type=str)]
UTCDate = Annotated[AwareDatetime, PlainSerializer(
    lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), return_type=str)]


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_identity: str = Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$", max_length=71)


class GateInspection(BaseModel):
    gate: GateName
    outcome: GateDisposition
    reason_codes: list[RiskReason]
    blocking_reason_codes: list[RiskReason]
    result_identity: str | None
    record_identity: str


class EvaluationResponse(BaseModel):
    outcome: RiskOutcome
    intent_identity: str
    proposed_quantity: Financial | None
    evaluated_quantity: Financial | None
    modeled_risk: Financial | None
    valuation_currency: str
    gates: list[GateInspection]
    blocking_reason_codes: list[RiskReason]
    decision_identity: str
    evaluated_at: UTCDate
    approval_expires_at: UTCDate | None
    evidence_identities: dict[str, str | None]
