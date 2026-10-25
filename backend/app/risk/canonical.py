"""Reuse TradeRail canonical hashing; exact wire values are a separate concern."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import re
from types import MappingProxyType
from uuid import UUID

from app.strategy_lab.canonical import canonical_json, fingerprint, utc_timestamp

DOMAIN_VERSION = "1.0.0"
SYMBOL = r"[A-Z][A-Z0-9]{0,15}/[A-Z][A-Z0-9]{0,15}(?::[A-Z0-9_-]{1,62})?"


def require(value, kind, name):
    if type(value) is not kind:
        raise ValueError(f"{name} requires {kind.__name__}")
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} requires non-empty trimmed text")
    return value


def identity(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"{name} requires a canonical SHA-256 identity")
    return value


def decimal(value, name, *, positive=False, fraction=False):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} requires a finite {'positive' if positive else 'nonnegative'} Decimal")
    if fraction and value > 1:
        raise ValueError(f"{name} must be a fraction between zero and one")
    return value


def timestamp(value):
    utc_timestamp(value)  # existing aware-time validation, without local-time assumptions
    return value.astimezone(timezone.utc)


def symbol(value):
    if not isinstance(value, str) or not re.fullmatch(SYMBOL, value):
        raise ValueError("A canonical instrument symbol is required")
    base, quote = value.split(":")[0].split("/")
    if base == quote:
        raise ValueError("Base and quote must differ")
    return value


def context(value):
    from app.market_intelligence.types import DatasetContext, TIMEFRAME_SECONDS
    require(value, DatasetContext, "instrument context")
    require(value.instrument_id, UUID, "instrument_id")
    symbol(value.canonical_symbol)
    if not isinstance(value.provider, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value.provider):
        raise ValueError("An explicit provider is required")
    if value.timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("Unsupported timeframe")
    return value


def values(value, *, wire=False):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return utc_timestamp(value).replace("+00:00", "Z")
    if isinstance(value, timedelta):
        return (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite Decimal")
        return format(value, "f") if wire else value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: values(getattr(value, f.name), wire=wire) for f in fields(value)
                if not (f.metadata.get("omit_when_none") and getattr(value, f.name) is None)}
    if isinstance(value, Mapping):
        return {key: values(item, wire=wire) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [values(item, wire=wire) for item in value]
    return value


def seal(instance, namespace, field_name):
    payload = {f.name: values(getattr(instance, f.name)) for f in fields(instance) if f.init
               and not (f.metadata.get("omit_when_none") and getattr(instance, f.name) is None)}
    object.__setattr__(instance, field_name, fingerprint("risk." + namespace + "/v1", canonical_json(payload)))


# Evidence is data, never an escape hatch for executable orders or credentials.
_FORBIDDEN = frozenset({"order", "orders", "execution", "execution_authority", "credentials", "api_key",
    "api_secret", "client_secret", "access_token", "refresh_token", "private_key", "leverage",
    "position_size", "position_sizing", "quantity", "notional", "trade_intent"})


def freeze_evidence(value):
    if not isinstance(value, Mapping):
        raise ValueError("Evidence must be a mapping")
    canonical_json(value)  # reject floats, invalid keys and unsupported/mutable custom objects
    def freeze(item):
        if isinstance(item, Mapping):
            for key in item:
                normalized = key.strip().lower().replace("-", "_")
                if normalized in _FORBIDDEN or normalized.startswith(("order_", "execution_", "credential_")):
                    raise ValueError("Evidence cannot carry credentials, orders or sizing authority")
            return MappingProxyType({key: freeze(entry) for key, entry in item.items()})
        if isinstance(item, (tuple, list)):
            return tuple(freeze(entry) for entry in item)
        return item
    return freeze(value)


def to_wire(value):
    """Detached exact-string serialization, not an API or execution contract."""
    result = values(value, wire=True)
    canonical_json(result)
    return result
