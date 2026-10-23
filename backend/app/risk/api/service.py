"""Inspection adapter only. All risk authority remains with the 10F finalizer."""
from app.risk.canonical import require, timestamp
from app.risk.integrated.inputs import RiskEvaluationInputs
from app.risk.integrated.inspection import inspect_risk
from app.risk.integrated.models import GateName, MANDATORY_GATES
from app.risk.vocabulary import RiskOutcome
from .schemas import EvaluationResponse, GateInspection


class RiskInspectionError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def run_inspection(source, user_id, intent_identity, evaluated_at):
    at = timestamp(evaluated_at)
    registered_report = None
    try:
        from app.risk.paper_source.service import ProductionPaperRiskSource
        if isinstance(source, ProductionPaperRiskSource):
            inputs, registered_report = source.inspect_registered(user_id=user_id, intent_identity=intent_identity, evaluated_at=at)
        else:
            inputs = source.resolve(user_id=user_id, intent_identity=intent_identity, evaluated_at=at)
        require(inputs, RiskEvaluationInputs, "trusted risk inputs")
    except Exception:
        raise RiskInspectionError(503, "trusted_risk_evidence_unavailable",
            "Authoritative risk evidence is unavailable") from None
    # A grant permits inspection of the authenticated operator's own proposals only.
    # There is no caller-supplied user, portfolio or cross-owner delegation field.
    if inputs.intent.intent_identity != intent_identity or inputs.intent.scope.user_id != user_id:
        raise RiskInspectionError(404, "risk_intent_unavailable", "Risk intent is unavailable")
    if inputs.evaluated_at != at:
        raise RiskInspectionError(409, "risk_context_mismatch", "Risk evaluation context does not match")
    try:
        report = registered_report if registered_report is not None else inspect_risk(inputs)
        decision = report.decision
        by_gate = {row.gate: row for row in report.gates}
        trade = by_gate[GateName.TRADE_RISK].result
        projection = trade.evaluated_projection if trade is not None else None
        assumptions = inputs.sizing_assumptions
        identities = {
            "inputs": inputs.inputs_identity,
            "configuration": inputs.configuration.configuration_identity,
            "portfolio_snapshot": inputs.snapshot.snapshot_identity,
            "market_context": inputs.market_context.context_identity,
        }
        for name, attr in (("sizing_assumptions", "assumptions_identity"), ("portfolio_policy", "policy_identity"),
            ("portfolio_context", "context_identity"), ("state_policy", "policy_identity"),
            ("drawdown_evidence", "evidence_identity"), ("safety_policy", "policy_identity"),
            ("switch_inventory", "inventory_identity"), ("lifecycle_evidence", "evidence_identity"),
            ("health_evidence", "evidence_identity")):
            row = getattr(inputs, name)
            identities[name] = getattr(row, attr) if row is not None else None
        return EvaluationResponse(
            outcome=decision.outcome, intent_identity=decision.intent_identity,
            proposed_quantity=assumptions.proposed_quantity if assumptions is not None else None,
            evaluated_quantity=projection.quantity if projection is not None else None,
            modeled_risk=projection.modeled_loss if projection is not None else None,
            valuation_currency=inputs.snapshot.scope.base_currency,
            gates=[GateInspection(gate=gate, outcome=by_gate[gate].disposition,
                reason_codes=[f.reason.code for f in by_gate[gate].findings],
                blocking_reason_codes=[f.reason.code for f in by_gate[gate].findings if f.blocking],
                result_identity=by_gate[gate].result.evaluation_identity if by_gate[gate].result is not None else None,
                record_identity=by_gate[gate].record_identity) for gate in MANDATORY_GATES],
            blocking_reason_codes=[code for code in decision.reason_codes if code.outcome is RiskOutcome.REJECTED],
            decision_identity=decision.decision_identity, evaluated_at=decision.evaluated_at,
            approval_expires_at=decision.approval_expires_at, evidence_identities=identities)
    except Exception:
        raise RiskInspectionError(503, "risk_evaluation_unavailable", "Risk evaluation is unavailable") from None
