"""Caller-owned PostgreSQL transactions. No commits, scheduling or financial math."""
from datetime import datetime, timezone
from uuid import UUID, uuid5, NAMESPACE_URL
import re
from sqlalchemy import select, update, or_
from sqlalchemy.dialects.postgresql import insert
from .models import work

TERMINAL = frozenset(("COMPLETED", "SKIPPED", "RECONCILIATION_REQUIRED"))
STATES = TERMINAL | {"PENDING", "RETRY_WAIT"}

def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Aware UTC timestamp required")
    return value.astimezone(timezone.utc)

def work_identity(mandate_id, candle_id):
    if not isinstance(mandate_id, UUID) or not isinstance(candle_id, UUID):
        raise ValueError("Canonical UUID identities required")
    return uuid5(NAMESPACE_URL, f"traderail:paper-mandate-candle:{mandate_id}:{candle_id}")

def transaction(c):
    if (c.dialect.name != "postgresql" or not c.in_transaction()
        or c.connection.dbapi_connection.autocommit is not False
        or c.get_isolation_level() != "READ COMMITTED"):
        raise ValueError("Explicit PostgreSQL READ COMMITTED transaction required")

def ensure_work(c, mandate_id, candle_id, *, runner_version, created_at, fresh_until):
    transaction(c)
    key = work_identity(mandate_id, candle_id)
    at, until = utc(created_at), utc(fresh_until)
    if until <= at or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", runner_version):
        raise ValueError("Valid version and freshness deadline required")
    c.execute(insert(work).values(work_id=key, mandate_id=mandate_id, candle_id=candle_id,
        runner_version=runner_version, created_at=at, updated_at=at, fresh_until=until)
        .on_conflict_do_nothing(constraint="uq_paper_work_mandate_candle"))
    row = c.execute(select(work).where(work.c.mandate_id==mandate_id, work.c.candle_id==candle_id)
                    .with_for_update()).mappings().one()
    if row["work_id"] != key:
        raise ValueError("Conflicting durable work identity")
    # A new runner version or retry cannot replace original deadline/state.
    return row

def record_attempt(c, work_id, *, at):
    transaction(c); at=utc(at)
    row=c.execute(select(work).where(work.c.work_id==work_id).with_for_update()).mappings().one()
    if (row["state"] in TERMINAL or at < row["updated_at"] or at >= row["fresh_until"]
        or (row["retry_at"] is not None and at < row["retry_at"])):
        raise ValueError("Work is terminal, not due or stale")
    return c.execute(update(work).where(work.c.work_id==work_id).values(state="PENDING",
        attempts=row["attempts"]+1, last_attempt_at=at, updated_at=at, retry_at=None)
        .returning(work)).mappings().one()

def record_result(c, work_id, *, state, at, outcome_code, reason_code=None, retry_at=None, **references):
    transaction(c); at=utc(at)
    if state not in STATES - {"PENDING"}:
        raise ValueError("Explicit result state required")
    for code in (outcome_code, reason_code):
        if code is not None and (not isinstance(code,str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}",code)):
            raise ValueError("Safe machine code required; no raw error text")
    if outcome_code is None:
        raise ValueError("Outcome code required")
    if set(references) - {"eligibility_identity","market_evidence_identity","registry_identity","request_identity","position_id"}:
        raise ValueError("Only canonical references may be stored")
    row=c.execute(select(work).where(work.c.work_id==work_id).with_for_update()).mappings().one()
    if row["state"] in TERMINAL or at < row["updated_at"]:
        raise ValueError("Terminal or future work cannot be rewritten")
    retry=utc(retry_at) if retry_at is not None else None
    if (state=="RETRY_WAIT" and (retry is None or not at<=retry<row["fresh_until"])) or (state!="RETRY_WAIT" and retry is not None):
        raise ValueError("Retry must fall within original freshness window")
    return c.execute(update(work).where(work.c.work_id==work_id).values(state=state, updated_at=at,
        outcome_code=outcome_code, reason_code=reason_code, retry_at=retry, **references)
        .returning(work)).mappings().one()


def load_work(c, mandate_id, candle_id):
    """Locked recovery precedes insertion, including after freshness expiry."""
    transaction(c)
    return c.execute(select(work).where(work.c.mandate_id==mandate_id,work.c.candle_id==candle_id)
                     .with_for_update()).mappings().one_or_none()


def barrier_work(c, mandate_id, *, lock=False):
    """Unresolved reconciliation or a closed first lifecycle stops later candles."""
    transaction(c)
    query=select(work).where(work.c.mandate_id==mandate_id,
        or_(work.c.state=='RECONCILIATION_REQUIRED',
            (work.c.outcome_code=='POSITION_CLOSED') & work.c.state.in_(['COMPLETED','SKIPPED'])))
    query=query.order_by(work.c.created_at.desc(),work.c.work_id).limit(1)
    if lock:
        query=query.with_for_update()
    return c.execute(query).mappings().one_or_none()
