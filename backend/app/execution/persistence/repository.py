"""Explicit PostgreSQL transaction participant. No engine creation, clock or commit.

Call inside an owned transaction (e.g. engine.begin()). A savepoint makes this
operation atomic even if a caller catches a conflict and continues that transaction.
PostgreSQL uniqueness arbitrates competing writers; no in-process lock is used.
"""
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from app.risk.canonical import require
from .contracts import ExecutionArtifacts, PersistenceReceipt
from . import models as m
from .records import request_row, consumption_row, order_row, fill_row


class PersistenceConflict(ValueError):
    """An identity is already bound to different immutable execution evidence."""


class PersistenceUnavailable(RuntimeError):
    """Sanitized storage failure; never includes SQL, credentials or payloads."""


def _put(connection, table, row, key):
    connection.execute(insert(table).values(**row).on_conflict_do_nothing().returning(table.c[key])).scalar_one_or_none()
    stored=connection.execute(select(table).where(table.c[key]==row[key])).mappings().one_or_none()
    if stored is None or any(stored[n] != row[n] for n in ("payload_identity","canonical_payload")):
        raise PersistenceConflict("Execution identity conflict; rollback required")
    return stored


def _order(connection, row):
    # ON CONFLICT waits for the competing writer. The following READ COMMITTED
    # statement then sees the winner; FOR UPDATE serializes lifecycle progression.
    connection.execute(insert(m.orders).values(**row).on_conflict_do_nothing().returning(m.orders.c.order_identity)).scalar_one_or_none()
    return connection.execute(select(m.orders).where(m.orders.c.order_identity==row["order_identity"]).with_for_update()).mappings().one_or_none()


def persist_artifacts(connection, artifacts: ExecutionArtifacts) -> PersistenceReceipt:
    require(artifacts, ExecutionArtifacts, "validated artifacts")
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise ValueError("An explicit PostgreSQL transaction is required")
    # SQLAlchemy may report READ COMMITTED even when the driver is in
    # AUTOCOMMIT mode. A logical Transaction alone is not proof of atomicity.
    driver = getattr(getattr(connection, "connection", None), "dbapi_connection", None)
    if getattr(driver, "autocommit", None) is not False:
        raise ValueError("Verified non-autocommit PostgreSQL connection required")
    try:
        isolation = connection.get_isolation_level()
    except DBAPIError:
        raise PersistenceUnavailable("Execution transaction context is unavailable") from None
    if isolation != "READ COMMITTED":
        raise ValueError("Persistence requires READ COMMITTED; retry stronger-isolation transactions externally")
    r=artifacts.request; consumption=consumption_row(r,artifacts.consumed_at)
    try:
        with connection.begin_nested():
            _put(connection,m.requests,request_row(r),"request_identity")
            _put(connection,m.consumptions,consumption,"consumption_identity")
            current=_order(connection,order_row(artifacts.observations[0]))
            if current is None or (current["request_identity"],current["client_order_identity"]) != (r.request_identity,r.client_order_identity):
                raise PersistenceConflict("Order/client identity conflict")
            # A caller must supply a validated history containing the exact stored
            # observation. No implicit rewind, skipped transition or rewritten fill.
            matches=[n for n,o in enumerate(artifacts.observations)
                     if current["canonical_payload"]==order_row(o)["canonical_payload"]]
            if not matches:
                raise PersistenceConflict("Stored lifecycle does not reconcile with supplied history")
            for audit in artifacts.fills:
                _put(connection,m.fills,fill_row(audit),"fill_identity")
            for observation in artifacts.observations[matches[-1]+1:]:
                row=order_row(observation)
                connection.execute(update(m.orders).where(m.orders.c.order_identity==r.order_identity).values(**row))
        return PersistenceReceipt(r.request_identity,r.action_identity,r.risk_decision_identity,
            consumption["consumption_identity"],r.client_order_identity,r.order_identity,
            artifacts.observations[-1].observation_identity,tuple(a.fill.fill_identity for a in artifacts.fills))
    except DBAPIError:
        raise PersistenceUnavailable("Execution persistence failed; transaction must not be committed on storage failure") from None
