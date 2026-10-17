"""Immutable explicit strategy definitions using existing research serialization."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
import re
from app.strategy_lab.canonical import canonical_json, fingerprint
from app.strategy_lab.experiment import ConfigurationSnapshot, snapshot_configuration

STRATEGY_CONFIGURATION_FORMAT = "strategy.configuration/v1"
FORBIDDEN_FIELDS = frozenset({"order", "orders", "order_id", "order_type", "side", "quantity", "notional",
    "position_size", "position_sizing", "leverage", "time_in_force", "limit_price", "stop_price",
    "execution", "execution_authority", "trade_intent", "venue", "venue_symbol", "account",
    "account_id", "api_key", "api_secret", "credentials", "profit_probability", "probability_of_profit",
    "confidence_of_profit", "profit_confidence"})


def freeze_mapping(values: Mapping) -> Mapping:
    # Existing serializer checks all types and rejects float/non-finite numeric values.
    snapshot_configuration(values)
    def freeze(value):
        if isinstance(value, Mapping):
            for key in value:
                normalized = key.strip().lower().replace("-", "_")
                if normalized in FORBIDDEN_FIELDS or normalized.startswith(("order_", "execution_", "trade_intent_", "position_size_")):
                    raise ValueError("Execution, sizing, credentials and profit-probability fields are not strategy data")
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        if isinstance(value, (list, tuple)):
            return tuple(freeze(item) for item in value)
        return value
    return freeze(values)


def reason_codes(values) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError("Reason codes must be an explicit ordered sequence")
    rows = tuple(values)
    if (not rows or any(not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) for code in rows)
        or len(set(rows)) != len(rows)):
        raise ValueError("Distinct non-empty deterministic reason codes are required")
    return rows


@dataclass(frozen=True, slots=True, init=False)
class StrategyDefinition:
    family: str
    version: str
    parameters: Mapping = field(hash=False, repr=False)
    configuration: ConfigurationSnapshot
    configuration_identity: str

    def __init__(self, family: str, version: str, parameters: Mapping):
        if not isinstance(family, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", family):
            raise ValueError("An explicit uppercase strategy family identifier is required")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", version):
            raise ValueError("An explicit strategy version is required")
        frozen = freeze_mapping(parameters)
        snapshot = snapshot_configuration(frozen)
        identity = fingerprint(STRATEGY_CONFIGURATION_FORMAT, canonical_json({"family": family,
            "version": version, "configuration_fingerprint": snapshot.configuration_fingerprint}))
        for name, value in (("family", family), ("version", version), ("parameters", frozen),
                            ("configuration", snapshot), ("configuration_identity", identity)):
            object.__setattr__(self, name, value)
