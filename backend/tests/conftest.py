"""Fail closed before test-module collection. Production code is untouched."""
import os
from pathlib import Path

# Override inherited/live settings before any test imports the application.
# Keep the inherited guard path for Python subprocess tests (no app imports there).
_BOOTSTRAP = str(Path(__file__).parent / "db_safety_bootstrap")
os.environ["TRADERAIL_DATABASE_URL"] = "postgresql+psycopg://test_guard:test_guard@127.0.0.1:1/blocked_test_database"
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (_BOOTSTRAP, os.environ.get("PYTHONPATH"))))

import psycopg
from sqlalchemy import event
from sqlalchemy.engine import Engine

class TestDatabaseSafetyError(RuntimeError):
    """A test attempted a physical database connection instead of isolation."""
    __test__ = False


def deny_database_connection(*args, **kwargs):
    # Never include URLs, credentials or connection arguments in the error.
    raise TestDatabaseSafetyError(
        "TEST DATABASE SAFETY: physical database connections are forbidden in this suite. "
        "Use an explicitly injected isolated mocked session/dependency override. "
        "Application/.env database access is never allowed."
    )


# Covers SQLAlchemy Session, Engine, raw_connection and pool recreation.
event.listen(Engine, "do_connect", deny_database_connection)
# Also cover direct driver use, which bypasses SQLAlchemy entirely.
psycopg.connect = deny_database_connection
psycopg.Connection.connect = classmethod(deny_database_connection)
psycopg.AsyncConnection.connect = classmethod(deny_database_connection)

# No real PostgreSQL opt-in: a name suffix or localhost URL does not prove isolation.
# Existing dependency-overridden/mock sessions need no physical connection.
