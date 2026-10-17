"""Analytical decisions are data, never orders or execution permissions."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from app.strategy_lab.canonical import canonical_json, fingerprint, utc_timestamp
from .configuration import freeze_mapping, reason_codes as validate_reasons
from .inputs import StrategyInput

STRATEGY_DECISION_FORMAT = "strategy.decision/v1"


class DecisionType(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    EXIT_LONG = "EXIT_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT_SHORT = "EXIT_SHORT"
    NO_ACTION = "NO_ACTION"


@dataclass(frozen=True, slots=True, init=False)
class StrategyDecision:
    strategy_family: str
    strategy_version: str
    timestamp: datetime
    decision: DecisionType
    reason_codes: tuple[str, ...]
    evidence: Mapping = field(hash=False)
    configuration_identity: str
    input_identity: str
    decision_identity: str

    def __init__(self, observation: StrategyInput, decision: DecisionType,
                 reason_codes: tuple[str, ...], evidence: Mapping):
        if not isinstance(observation, StrategyInput) or not isinstance(decision, DecisionType):
            raise ValueError("A validated strategy input and explicit decision type are required")
        reasons = validate_reasons(reason_codes)
        frozen = freeze_mapping(evidence)
        timestamp = observation.candle.close_time.astimezone(timezone.utc)
        payload = {"strategy_family": observation.definition.family, "strategy_version": observation.definition.version,
            "timestamp": utc_timestamp(timestamp), "decision": decision.value, "reason_codes": reasons,
            "evidence": frozen, "configuration_identity": observation.definition.configuration_identity,
            "input_identity": observation.input_identity}
        for name, value in payload.items():
            object.__setattr__(self, name, timestamp if name == "timestamp" else value)
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "decision_identity", fingerprint(STRATEGY_DECISION_FORMAT, canonical_json(payload)))
