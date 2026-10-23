"""Server-only approval-to-registry workflow. No HTTP evidence or execution."""
from app.risk.canonical import require
from app.risk.vocabulary import RiskOutcome
from app.risk.integrated.inputs import RiskEvaluationInputs
from app.risk.integrated.inspection import inspect_risk
from app.execution.authorization import ExecutionAuthorization
from app.execution.registry.contracts import AuthorizationPackage,ExecutionEvidenceReferences
from app.execution.registry.repository import persist_authorization,AuthorizationUnavailable


def evaluate_and_register(connection,inputs,*,user_id,evidence_references):
    """Evaluate once and persist only APPROVED, within the caller's transaction.

    The bounded production PAPER source calls this service within its authoritative
    evidence transaction. Rejections never create an executable registry package.
    No record is consumed, no execution request is placed, no capital is mutated.
    """
    require(inputs,RiskEvaluationInputs,'trusted risk inputs')
    require(evidence_references,ExecutionEvidenceReferences,'execution references')
    if inputs.intent.scope.user_id!=user_id:raise AuthorizationUnavailable('Intent is unavailable')
    report=inspect_risk(inputs)
    if report.decision.outcome is RiskOutcome.REJECTED:return report,None
    package=AuthorizationPackage(ExecutionAuthorization(inputs.intent,report),inputs,evidence_references)
    saved=persist_authorization(connection,package,user_id=user_id,as_of=inputs.evaluated_at)
    return report,saved
