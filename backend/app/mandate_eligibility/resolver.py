"""Pure compatibility resolution. No hidden clock, reads, evaluation or mutation."""
from datetime import datetime
from app.mandates.domain import RiskLevel, TradingStyle
from .contracts import (EligibilityPolicy, EligibilityReason as Reason, EligibilityResult,
    EligibilityStatus, InstrumentReference, MandateContext, TechnicalEligibility, canonical_identity)


def resolve(mandate: MandateContext, policy: EligibilityPolicy | None,
            instrument: InstrumentReference | None, *, resolved_at: datetime) -> EligibilityResult:
    if not isinstance(mandate, MandateContext): raise ValueError("Immutable mandate context required")
    reasons = []
    if mandate.status != "ACTIVE":
        reasons.append({"DRAFT": Reason.MANDATE_DRAFT, "PAUSED": Reason.MANDATE_PAUSED,
                        "RETIRED": Reason.MANDATE_RETIRED}.get(mandate.status, Reason.MANDATE_STATUS_UNSUPPORTED))
    for invalid, reason in (
        (mandate.environment != "PAPER", Reason.ENVIRONMENT_UNSUPPORTED),
        (mandate.instrument != "BTC/USDT", Reason.INSTRUMENT_UNSUPPORTED),
        (mandate.allocation_currency != "USDT", Reason.CURRENCY_UNSUPPORTED),
        (mandate.direction_capability != "LONG_ONLY", Reason.DIRECTION_UNSUPPORTED),
        (mandate.trading_style not in tuple(item.value for item in TradingStyle), Reason.TRADING_STYLE_UNSUPPORTED),
    ):
        if invalid: reasons.append(reason)
    if instrument is None or instrument.canonical_symbol != mandate.instrument or instrument.market_type != "SPOT" or instrument.status != "active":
        reasons.append(Reason.INSTRUMENT_UNAVAILABLE)
    risk = None
    configurations = ()
    if policy is None:
        reasons.append(Reason.POLICY_UNAVAILABLE)
    else:
        if not isinstance(policy, EligibilityPolicy): raise ValueError("Typed server-owned policy required")
        risk = next((ref for ref in policy.risk_references if ref.preference == mandate.risk_level), None)
        if mandate.risk_level not in tuple(item.value for item in RiskLevel) or risk is None:
            reasons.append(Reason.RISK_PREFERENCE_UNSUPPORTED)
        matches = tuple(rule for rule in policy.technical_mappings if
            (rule.environment, rule.instrument, rule.allocation_currency, rule.direction_capability, rule.trading_style, rule.risk_level) ==
            (mandate.environment, mandate.instrument, mandate.allocation_currency, mandate.direction_capability, mandate.trading_style, mandate.risk_level))
        if not matches:
            reasons.extend((Reason.STYLE_MAPPING_UNCONFIGURED, Reason.STRATEGY_MAPPING_UNCONFIGURED))
        elif not reasons:
            configurations = tuple(sorted(set(matches[0].configurations), key=lambda ref: canonical_identity("configuration-reference", ref)))
    # Fixed lexical ordering makes simultaneous blockers independent of hash order.
    codes = tuple(sorted(set(reasons), key=lambda reason: reason.value)) if reasons else (Reason.TECHNICAL_CONFIGURATION_ELIGIBLE,)
    technical = TechnicalEligibility(risk_configuration=risk if mandate.environment == "PAPER" else None,
                                     eligible_configurations=configurations)
    context_identity = canonical_identity("mandate-context", mandate)
    version = policy.version if policy else None
    policy_identity = policy.policy_identity if policy else None
    status = EligibilityStatus.INELIGIBLE if reasons else EligibilityStatus.ELIGIBLE
    payload = {"resolver_version": "1.0.0", "mandate": context_identity, "policy": policy_identity,
               "instrument": instrument, "technical": technical, "reasons": codes, "status": status}
    return EligibilityResult(mandate_identity=mandate.id, mandate_context_identity=context_identity,
        eligibility_identity=canonical_identity("technical-eligibility", payload), status=status,
        user_intent=mandate, technical_eligibility=technical, reason_codes=codes,
        policy_version=version, policy_identity=policy_identity, resolved_at=resolved_at)
