"""Server-only evidence boundary. No HTTP field can provide trusted risk state."""
from datetime import datetime
from typing import Protocol
from uuid import UUID
from app.risk.integrated.inputs import RiskEvaluationInputs


class TrustedEvidenceUnavailable(Exception):
    pass


class TrustedRiskSource(Protocol):
    def resolve(self, *, user_id: UUID, intent_identity: str, evaluated_at: datetime) -> RiskEvaluationInputs:
        """Construct exact intent and complete evidence from authoritative server state."""
        ...


class UnavailableRiskSource:
    def resolve(self, *, user_id: UUID, intent_identity: str, evaluated_at: datetime) -> RiskEvaluationInputs:
        # PaperPortfolio cash alone cannot establish equity, open risk, kill switches,
        # lifecycle, strategy health or platform policy. Never fabricate them.
        raise TrustedEvidenceUnavailable()


def get_trusted_risk_source() -> TrustedRiskSource:
    # Deliberately unconfigured until authoritative sources exist. Tests may inject
    # immutable trusted fixtures through FastAPI's server-side dependency overrides.
    return UnavailableRiskSource()
