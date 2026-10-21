"""Read-only evidence accessor over the unchanged 10F orchestration."""
from dataclasses import dataclass
from app.risk.canonical import require
from app.risk.decision import RiskDecision
from .inputs import RiskEvaluationInputs
from .models import GateRecord
from .evaluator import _collect, _finalize


@dataclass(frozen=True, slots=True)
class RiskEvaluationInspection:
    decision: RiskDecision
    gates: tuple[GateRecord, ...]


def inspect_risk(inputs: RiskEvaluationInputs) -> RiskEvaluationInspection:
    """Evaluate once; retain the exact gate results used by the finalizer."""
    require(inputs, RiskEvaluationInputs, "integrated inputs")
    gates = _collect(inputs)
    return RiskEvaluationInspection(_finalize(inputs, gates), gates)
