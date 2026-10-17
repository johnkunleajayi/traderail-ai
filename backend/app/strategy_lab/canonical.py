"""Versioned, type-safe canonical serialization without Decimal context rounding."""
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json


def utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def decimal_token(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Non-finite Decimal is not reproducible")
    sign, digits, exponent = value.as_tuple()
    if not any(digits):
        return "0e0"
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    return ("-" if sign else "") + "".join(map(str, digits)) + "e" + str(exponent)


def _node(value):
    # Tags distinguish Decimal, integer, string and boolean without reserved-key collisions.
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, Decimal):
        return ["decimal", decimal_token(value)]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Configuration keys must be strings")
        return ["map", [[key, _node(value[key])] for key in sorted(value)]]
    if isinstance(value, (list, tuple)):
        return ["list", [_node(item) for item in value]]
    raise ValueError("Unsupported canonical value; floats are not accepted")


def canonical_json(value) -> str:
    return json.dumps(_node(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(namespace: str, canonical_payload: str) -> str:
    return "sha256:" + hashlib.sha256((namespace + "\n" + canonical_payload).encode("utf-8")).hexdigest()
