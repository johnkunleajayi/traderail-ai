"""Existing canonical JSON/SHA-256, with a separate execution namespace."""
from dataclasses import fields
from app.risk.canonical import values
from app.strategy_lab.canonical import canonical_json, fingerprint


def execution_identity(kind, payload):
    return fingerprint("execution." + kind + "/v1", canonical_json(values(payload)))


def seal(instance, kind, name):
    payload = {f.name: getattr(instance, f.name) for f in fields(instance) if f.init}
    object.__setattr__(instance, name, execution_identity(kind, payload))
