"""Serialized owner-scoped entry evaluation; no consumption or economic mutation."""
from dataclasses import replace
from decimal import DecimalException
from sqlalchemy import select
from app.mandates.service import owned
from app.mandate_strategy.service import evaluate_mandate
from app.mandate_strategy.contracts import MandateEvaluationError, ObservationOutcome
from app.mandate_eligibility.contracts import canonical_identity
from app.risk.paper_source.service import assemble, owned_untouched_book
from app.risk.paper_source.market import PaperEvidenceUnavailable
from app.risk.paper_source.policies import CONSTRAINTS, COSTS
from app.risk.paper_policy import configuration_for_paper_portfolio, PaperRiskPolicyUnavailable
from app.risk.paper_switches import PaperSwitchUnavailable
from app.risk.authorization_service import evaluate_and_register
from app.risk.integrated.models import GateName
from app.risk.integrated.inspection import inspect_risk
from app.risk.vocabulary import RiskOutcome, Direction
from app.execution.registry.contracts import ExecutionEvidenceReferences
from app.execution.registry.models import authorizations
from app.execution.registry.repository import load_authorization, RegistryUnavailable, RegistryConflict, AuthorizationUnavailable
from app.strategy_lab.numeric import exact_product
from .contracts import (MandateRiskResult, MandateRiskStatus, RiskSummary, GateSummary, AuthorizationSummary, MandateRiskError)


def proposal_context(strategy):
    # No wall clock in the proposal retry key: a retry cannot renew permission.
    key=canonical_identity("risk-proposal", {"policy":"mandate-risk-v1", "observation":strategy.evaluation_identity})
    return {"mandate_risk_proposal_identity":key, "mandate_provenance":{
        "mandate_identity":str(strategy.mandate_identity),
        "eligibility_identity":strategy.eligibility_identity,
        "eligibility_policy_identity":strategy.eligibility_policy_identity,
        "eligibility_policy_version":strategy.eligibility_policy_version,
        "technical_configuration_identity":strategy.technical_configuration.configuration_identity,
        "strategy_observation_identity":strategy.evaluation_identity,
        "capital_allocation":strategy.user_intent.capital_allocation,
        "allocation_currency":strategy.user_intent.allocation_currency,
        "allocation_rule":"entry-cash-including-fee-v1"}}


def previous_package(c, strategy, context, user_id):
    # Existing durable registry is authoritative. The locked mandate + portfolio
    # serialize competing calls; no process-local cache or approval reissue.
    ids=c.execute(select(authorizations.c.registry_identity).where(authorizations.c.user_id==user_id,
        authorizations.c.portfolio_id==strategy.portfolio_identity).order_by(authorizations.c.registry_identity)).scalars().all()
    matches=[]
    for registry_id in ids:
        package=load_authorization(c,registry_id,user_id=user_id)
        if package.inputs.intent.entry_context.get("mandate_risk_proposal_identity")==context["mandate_risk_proposal_identity"]:
            matches.append(package)
    if len(matches)>1:
        raise RegistryUnavailable("Conflicting stored mandate authorizations")
    return matches[0] if matches else None


def reconcile(inputs, strategy, context, book):
    i=inputs.intent; s=inputs.sizing_assumptions; m=inputs.market_context
    if (i.scope.user_id!=strategy.user_intent.user_id or i.scope.portfolio_id!=strategy.portfolio_identity
        or i.scope.environment.value!=strategy.user_intent.environment or i.scope.base_currency!=strategy.user_intent.allocation_currency
        or i.direction is not Direction.LONG or i.strategy_decision_identity!=strategy.strategy_decision.decision_identity
        or i.strategy_input_identity!=strategy.strategy_decision.input_identity
        or i.strategy.configuration_identity!=strategy.technical_configuration.configuration_identity
        or i.strategy.family!=strategy.technical_configuration.strategy_family or i.strategy.version!=strategy.technical_configuration.strategy_version
        or i.instrument.instrument_id!=strategy.market.instrument_id or i.instrument.canonical_symbol!=strategy.market.canonical_symbol
        or i.instrument.provider!=strategy.market.provider or i.instrument.timeframe!=strategy.market.timeframe
        or m.data_identity!=strategy.market.market_evidence_identity or m.intelligence_identity!=strategy.market.intelligence_identity
        or m.regime_identity!=strategy.market.regime_identity or m.data_through!=strategy.market.candle_close_time
        or inputs.snapshot.valuation_identity!=strategy.book_identity
        or inputs.snapshot.available_cash!=book.cash_balance or inputs.snapshot.equity!=book.cash_balance
        or inputs.snapshot.open_positions!=()
        or inputs.configuration!=configuration_for_paper_portfolio(i.scope)
        or any(i.entry_context.get(k)!=v for k,v in context.items())
        or s is None or s.costs!=COSTS or s.proposed_quantity is not None
        or s.constraints!=replace(CONSTRAINTS,max_entry_cash_commitment=strategy.user_intent.capital_allocation)):
        raise MandateEvaluationError("mandate_risk_context_mismatch", "The proposal no longer matches the authoritative mandate observation")


def summarize(strategy, report, package, *, reused, at):
    decision=report.decision
    trade=next(row.result for row in report.gates if row.gate is GateName.TRADE_RISK)
    projection=trade.evaluated_projection if trade is not None else None
    risk=RiskSummary(outcome=decision.outcome,decision_identity=decision.decision_identity,
        configuration_identity=decision.configuration_identity,evaluated_at=decision.evaluated_at,
        approval_expires_at=decision.approval_expires_at,
        gates=tuple(GateSummary(gate=row.gate,outcome=row.disposition) for row in sorted(report.gates,key=lambda row:row.gate.value)),
        blocking_reason_codes=tuple(code for code in decision.reason_codes if code.outcome is RiskOutcome.REJECTED),
        evaluated_quantity=projection.quantity if projection else None,
        modeled_entry_notional=projection.notional if projection else None,
        modeled_entry_fee=exact_product(projection.quantity,trade.sizing.loss_model.entry_fee) if projection else None,
        modeled_entry_commitment=projection.required_cash if projection else None,
        modeled_risk=projection.modeled_loss if projection else None)
    auth=None
    if package is not None:
        auth=AuthorizationSummary(registry_identity=package.registry_identity,
            authorization_identity=package.authorization.authorization_identity,quantity=package.authorization.quantity,
            approval_expires_at=decision.approval_expires_at,reused=reused,expired=at>=decision.approval_expires_at)
    return MandateRiskResult(status=MandateRiskStatus(decision.outcome.value),strategy=strategy,
        intent_identity=decision.intent_identity,risk=risk,authorization=auth)


def evaluate(db, user_id, mandate_id, *, evaluated_at):
    # Reuse lifecycle ownership locking; activation/editing cannot race this proposal.
    owned(db,user_id,mandate_id,lock=True)
    strategy=evaluate_mandate(db,user_id,mandate_id,evaluated_at=evaluated_at)
    if strategy.outcome is ObservationOutcome.NO_TRADE:
        return MandateRiskResult(status=MandateRiskStatus.NO_TRADE,strategy=strategy)
    c=db.connection()
    try:
        book=owned_untouched_book(c,user_id)
        if book.state_identity!=strategy.book_identity:
            raise MandateEvaluationError("mandate_book_changed", "Authoritative Paper book changed during evaluation")
        context=proposal_context(strategy)
        package=previous_package(c,strategy,context,user_id)
        # The existing source reconstructs every mandatory gate input in this same
        # transaction. Re-reading evidence must reconcile, never silently substitute.
        inputs,decision=assemble(c,user_id,evaluated_at,proposal_context=context,
            size_constraints=replace(CONSTRAINTS,max_entry_cash_commitment=strategy.user_intent.capital_allocation))
        if inputs is None or decision.decision_identity!=strategy.strategy_decision.decision_identity:
            raise MandateEvaluationError("mandate_risk_context_mismatch", "Strategy evidence changed during evaluation")
        reconcile(inputs,strategy,context,book)
        if package is not None:
            reconcile(package.inputs,strategy,context,book)
            if package.authorization.decision.evaluated_at>evaluated_at:
                raise MandateEvaluationError("mandate_risk_context_mismatch", "Stored approval is future-dated")
            # Current controls remain mandatory even for an unchanged observation.
            # A passing recheck does not persist/reissue/extend the old approval.
            current=inspect_risk(inputs)
            if current.decision.outcome is RiskOutcome.REJECTED:
                return summarize(strategy,current,None,reused=False,at=evaluated_at)
            return summarize(strategy,package.authorization.report,package,reused=True,at=evaluated_at)
        refs=ExecutionEvidenceReferences(inputs.market_context.data_identity,inputs.market_context.context_identity,
            inputs.sizing_assumptions.costs.costs_identity,inputs.snapshot.valuation_identity)
        report,package=evaluate_and_register(c,inputs,user_id=user_id,evidence_references=refs)
        return summarize(strategy,report,package,reused=False,at=evaluated_at)
    except (PaperEvidenceUnavailable,PaperSwitchUnavailable,PaperRiskPolicyUnavailable,DecimalException,OverflowError):
        raise MandateEvaluationError("trusted_mandate_risk_unavailable", "Required authoritative PAPER Risk evidence is unavailable") from None
    except (RegistryConflict,AuthorizationUnavailable):
        raise MandateRiskError("mandate_authorization_conflict", "The owned authorization cannot be reconciled",409) from None
    except RegistryUnavailable:
        raise MandateRiskError("mandate_registry_unavailable", "Authorization registry is unavailable",503) from None
