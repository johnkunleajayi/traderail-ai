from dataclasses import dataclass, field
from app.risk.canonical import identity, require
from app.risk.integrated.inputs import RiskEvaluationInputs
from app.risk.vocabulary import Environment
from app.risk.reasons import RiskReason
from app.execution.authorization import ExecutionAuthorization
from app.execution.canonical import seal


@dataclass(frozen=True, slots=True)
class ExecutionEvidenceReferences:
    """Exact approved references, not a claim that current market data is fresh."""
    market_source_identity: str
    market_context_identity: str
    costs_identity: str
    valuation_identity: str

    def __post_init__(self):
        for name in ('market_source_identity','market_context_identity','costs_identity','valuation_identity'):
            identity(getattr(self,name),name)


@dataclass(frozen=True, slots=True)
class AuthorizationPackage:
    authorization: ExecutionAuthorization
    inputs: RiskEvaluationInputs
    evidence_references: ExecutionEvidenceReferences
    registry_identity: str = field(init=False)

    def __post_init__(self):
        require(self.authorization,ExecutionAuthorization,'authorization')
        require(self.inputs,RiskEvaluationInputs,'original risk inputs')
        require(self.evidence_references,ExecutionEvidenceReferences,'execution references')
        a=self.authorization; i=self.inputs; d=a.decision; r=self.evidence_references
        if i.intent.scope.environment is not Environment.PAPER:
            raise ValueError('Registry is PAPER only')
        if (a.intent!=i.intent or d.configuration!=i.configuration or d.portfolio_snapshot!=i.snapshot
                or d.market_context!=i.market_context or d.evaluated_at!=i.evaluated_at):
            raise ValueError('Authorization and original inputs differ')
        finding=next(f for f in d.findings if f.code is RiskReason.INTEGRATED_EVALUATION_RECORDED)
        manifests=[row['source_evidence'] for row in finding.evidence['gate_findings'] if row['gate']=='INTEGRATION']
        if len(manifests)!=1 or manifests[0]['inputs_identity']!=i.inputs_identity:
            raise ValueError('Original inputs do not match the approved integrated manifest')
        if (r.market_source_identity!=i.market_context.data_identity
                or r.market_context_identity!=i.market_context.context_identity
                or i.sizing_assumptions is None
                or r.costs_identity!=i.sizing_assumptions.costs.costs_identity
                or r.valuation_identity!=i.snapshot.valuation_identity):
            raise ValueError('Execution references differ from approved evidence')
        seal(self,'authorization-registry-1.0.0','registry_identity')
