"""Immutable eligibility contracts, with exact intent separated from policy references."""
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer, model_validator
from app.mandates.domain import RiskLevel, TradingStyle
from app.market_intelligence.types import TIMEFRAME_SECONDS
from app.strategy_lab.canonical import canonical_json, fingerprint, utc_timestamp


def canonical_identity(namespace: str, value) -> str:
    def primitive(item):
        if isinstance(item, BaseModel): return primitive(item.model_dump(mode="python"))
        if isinstance(item, datetime): return utc_timestamp(item)
        if isinstance(item, UUID): return str(item)
        if isinstance(item, StrEnum): return item.value
        if isinstance(item, dict): return {key: primitive(part) for key, part in item.items()}
        if isinstance(item, (tuple, list)): return [primitive(part) for part in item]
        return item
    return fingerprint("mandate." + namespace + "/v1", canonical_json(primitive(value)))


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class MandateContext(Frozen):
    id: UUID
    user_id: UUID
    environment: str
    capital_allocation: Decimal = Field(gt=0, allow_inf_nan=False)
    allocation_currency: str
    instrument: str
    risk_level: str
    trading_style: str
    direction_capability: str
    status: str
    created_at: datetime
    updated_at: datetime

    @field_validator("capital_allocation", mode="before")
    @classmethod
    def exact_decimal(cls, value):
        if not isinstance(value, Decimal): raise ValueError("Authoritative allocation must be Decimal")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc(cls, value):
        utc_timestamp(value)
        return value.astimezone(timezone.utc)

    @field_serializer("capital_allocation", when_used="json")
    def wire_decimal(self, value): return format(value, "f")


class InstrumentReference(Frozen):
    id: UUID
    canonical_symbol: str
    market_type: str
    status: str


class RiskConfigurationReference(Frozen):
    preference: RiskLevel
    version: str = Field(min_length=1)
    configuration_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class TechnicalConfiguration(Frozen):
    strategy_family: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    configuration_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    timeframe: str
    timeframe_role: Literal["SETUP_EVALUATION"] = "SETUP_EVALUATION"
    position_capabilities: tuple[Literal["LONG"], Literal["CASH"]] = ("LONG", "CASH")

    @field_validator("timeframe")
    @classmethod
    def known_timeframe(cls, value):
        if value not in TIMEFRAME_SECONDS: raise ValueError("Unsupported canonical timeframe")
        return value


class TechnicalMapping(Frozen):
    # Future mappings must be explicit versioned server-owned product decisions.
    environment: Literal["PAPER"] = "PAPER"
    instrument: Literal["BTC/USDT"] = "BTC/USDT"
    allocation_currency: Literal["USDT"] = "USDT"
    direction_capability: Literal["LONG_ONLY"] = "LONG_ONLY"
    trading_style: TradingStyle
    risk_level: RiskLevel
    configurations: tuple[TechnicalConfiguration, ...] = Field(min_length=1)

    @field_validator("configurations")
    @classmethod
    def ordered_configurations(cls, value):
        return tuple(sorted(set(value), key=lambda ref: canonical_identity("configuration-reference", ref)))


class EligibilityPolicy(Frozen):
    version: str = Field(min_length=1)
    risk_references: tuple[RiskConfigurationReference, ...] = ()
    technical_mappings: tuple[TechnicalMapping, ...] = ()

    @model_validator(mode="after")
    def unambiguous(self):
        if len({ref.preference for ref in self.risk_references}) != len(self.risk_references):
            raise ValueError("Duplicate risk preference references")
        keys = [(rule.environment, rule.instrument, rule.allocation_currency, rule.direction_capability, rule.trading_style, rule.risk_level) for rule in self.technical_mappings]
        if len(set(keys)) != len(keys): raise ValueError("Duplicate technical eligibility mapping")
        return self

    @property
    def policy_identity(self):
        payload = self.model_dump(mode="python")
        payload["risk_references"] = sorted(payload["risk_references"], key=lambda row: canonical_identity("risk-reference", row))
        payload["technical_mappings"] = sorted(payload["technical_mappings"], key=lambda row: canonical_identity("technical-mapping", row))
        return canonical_identity("eligibility-policy", payload)


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"


class EligibilityReason(StrEnum):
    MANDATE_DRAFT = "MANDATE_DRAFT"
    MANDATE_PAUSED = "MANDATE_PAUSED"
    MANDATE_RETIRED = "MANDATE_RETIRED"
    MANDATE_STATUS_UNSUPPORTED = "MANDATE_STATUS_UNSUPPORTED"
    ENVIRONMENT_UNSUPPORTED = "ENVIRONMENT_UNSUPPORTED"
    INSTRUMENT_UNSUPPORTED = "INSTRUMENT_UNSUPPORTED"
    CURRENCY_UNSUPPORTED = "CURRENCY_UNSUPPORTED"
    DIRECTION_UNSUPPORTED = "DIRECTION_UNSUPPORTED"
    RISK_PREFERENCE_UNSUPPORTED = "RISK_PREFERENCE_UNSUPPORTED"
    TRADING_STYLE_UNSUPPORTED = "TRADING_STYLE_UNSUPPORTED"
    INSTRUMENT_UNAVAILABLE = "INSTRUMENT_UNAVAILABLE"
    STYLE_MAPPING_UNCONFIGURED = "STYLE_MAPPING_UNCONFIGURED"
    STRATEGY_MAPPING_UNCONFIGURED = "STRATEGY_MAPPING_UNCONFIGURED"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    TECHNICAL_CONFIGURATION_ELIGIBLE = "TECHNICAL_CONFIGURATION_ELIGIBLE"


class TechnicalEligibility(Frozen):
    risk_configuration: RiskConfigurationReference | None = None
    eligible_configurations: tuple[TechnicalConfiguration, ...] = ()


class EligibilityResult(Frozen):
    scope: Literal["TECHNICAL_ELIGIBILITY_ONLY"] = "TECHNICAL_ELIGIBILITY_ONLY"
    mandate_identity: UUID
    mandate_context_identity: str
    eligibility_identity: str
    status: EligibilityStatus
    user_intent: MandateContext
    technical_eligibility: TechnicalEligibility
    reason_codes: tuple[EligibilityReason, ...]
    resolver_version: Literal["1.0.0"] = "1.0.0"
    policy_version: str | None
    policy_identity: str | None
    resolved_at: datetime

    @field_validator("resolved_at")
    @classmethod
    def utc(cls, value):
        utc_timestamp(value)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def no_partial_eligibility(self):
        if self.status is EligibilityStatus.ELIGIBLE:
            if not self.technical_eligibility.eligible_configurations or not self.technical_eligibility.risk_configuration or self.reason_codes != (EligibilityReason.TECHNICAL_CONFIGURATION_ELIGIBLE,):
                raise ValueError("Complete technical eligibility is required")
        elif self.technical_eligibility.eligible_configurations or not self.reason_codes or EligibilityReason.TECHNICAL_CONFIGURATION_ELIGIBLE in self.reason_codes:
            raise ValueError("Ineligible results cannot carry eligible configurations")
        return self
