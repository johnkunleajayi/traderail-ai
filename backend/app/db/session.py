from functools import lru_cache

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


@lru_cache
def get_engine(database_url: str) -> Engine:
    return create_engine(database_url, poolclass=NullPool, pool_pre_ping=True,
                         connect_args={"connect_timeout": 5})


def get_session(request: Request):
    with Session(get_engine(str(request.app.state.settings.database_url))) as session:
        yield session