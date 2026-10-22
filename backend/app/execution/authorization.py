"""Check authorization provenance/context, never recalculate whether a trade is safe."""
from dataclasses import dataclass, field
from app.risk.canonical import require, timestamp
from app.risk.intent import TradeIntent
from app.risk.decision import RiskDecision
from app.risk.vocabulary import RiskOutcome
from app.risk.reasons import RiskReason
from app.risk.integrated.inspection import RiskEvaluationInspection
from app.risk.integrated.models import (MANDATORY_GATES, INTEGRATED_RISK_VERSION,
    GateName, GateDisposition, GateRecord)
from .canonical import seal


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    intent: TradeIntent
    report: RiskEvaluationInspection
    authorization_identity: str = field(init=False)

    def __post_init__(self):
        require(self.intent, TradeIntent, "intent")
        require(self.report, RiskEvaluationInspection, "integrated risk report")
        decision = self.report.decision
        require(decision, RiskDecision, "risk decision")
        if decision.outcome is not RiskOutcome.APPROVED or decision.approval_expires_at is None:
            raise ValueError("Execution requires a time-bounded APPROVED risk decision")
        if decision.intent_identity != self.intent.intent_identity:
            raise ValueError("Approval belongs to a different exact intent")
        rows = self.report.gates
        if not isinstance(rows, tuple) or any(type(row) is not GateRecord for row in rows):
            raise ValueError("Exact immutable gate records required")
        if len(rows) != len(MANDATORY_GATES) or {row.gate for row in rows} != set(MANDATORY_GATES):
            raise ValueError("All four mandatory risk gates are required")
        if any(row.disposition is not GateDisposition.PASS for row in rows):
            raise ValueError("Every mandatory gate must have passed")
        normalized = tuple(sorted(rows, key=lambda row: row.gate.value))
        object.__setattr__(self, "report", RiskEvaluationInspection(decision, normalized))
        reasons = {f.code: f for f in decision.findings}
        if RiskReason.INTEGRATED_GATE_COVERAGE_CONFIRMED not in reasons:
            raise ValueError("Integrated risk coverage attestation required")
        try:
            entries = reasons[RiskReason.INTEGRATED_EVALUATION_RECORDED].evidence["gate_findings"]
            manifests = [row["source_evidence"] for row in entries if row["gate"] == "INTEGRATION"]
            if len(manifests) != 1:
                raise ValueError("Ambiguous integrated manifest")
            manifest = manifests[0]
            expected = tuple({"gate": row.gate.value, "disposition": row.disposition.value,
                "record_identity": row.record_identity, "source_result_identity": row.result.evaluation_identity,
                "source_outcome": row.result.status.value, "source_scope": row.result.scope} for row in normalized)
            if manifest["version"] != INTEGRATED_RISK_VERSION or tuple(manifest["gates"]) != expected:
                raise ValueError("Gate evidence does not belong to this approval")
        except (KeyError, TypeError) as error:
            raise ValueError("Integrated approval provenance is unavailable") from error
        by_gate = {row.gate: row.result for row in normalized}
        for row in normalized:
            result = row.result
            if (result.configuration_identity != decision.configuration_identity
                    or result.evaluated_at != decision.evaluated_at):
                raise ValueError("Gate evaluation context mismatch")
            if hasattr(result, "intent_identity") and result.intent_identity != self.intent.intent_identity:
                raise ValueError("Gate intent mismatch")
            if hasattr(result, "snapshot_identity") and result.snapshot_identity != decision.snapshot_identity:
                raise ValueError("Gate snapshot mismatch")
        trade = by_gate[GateName.TRADE_RISK]
        portfolio = by_gate[GateName.PORTFOLIO_RISK]
        state = by_gate[GateName.RISK_STATE]
        if (portfolio.trade_risk_identity != trade.evaluation_identity
                or portfolio.projection.proposed_base_quantity != trade.evaluated_projection.quantity
                or portfolio.projection.proposed_risk != trade.evaluated_projection.modeled_loss
                or state.projection.proposed_modeled_risk != trade.evaluated_projection.modeled_loss):
            raise ValueError("Approved proposal evidence is inconsistent")
        seal(self, "authorization-1.0.0", "authorization_identity")

    @property
    def decision(self):
        return self.report.decision

    @property
    def quantity(self):
        return next(row.result.evaluated_projection.quantity for row in self.report.gates
                    if row.gate is GateName.TRADE_RISK)

    def validate_at(self, as_of):
        at = timestamp(as_of)
        if not self.decision.evaluated_at <= at < self.decision.approval_expires_at:
            raise ValueError("Approval is future-dated or expired; fresh risk evaluation is required")
