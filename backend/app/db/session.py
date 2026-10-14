from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from app.core.config import get_settings

@lru_cache
def get_engine() -> Engine:
    # Connections are opened only when requested. External poolers suit serverless deployment.
    return create_engine(str(get_settings().database_url), poolclass=NullPool, pool_pre_ping=True)

def get_session():
    with Session(get_engine()) as session:
        yield session