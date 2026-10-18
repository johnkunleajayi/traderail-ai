"""Regression coverage: no live database or credentials are used."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from conftest import TestDatabaseSafetyError
from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app

DUMMY_URL = "postgresql+psycopg://dummy:private_dummy@application.invalid:5432/traderail"

@pytest.mark.parametrize("operation", ["connect", "raw_connection", "session", "pool_recreation"])
def test_sqlalchemy_physical_connections_fail_closed(operation):
    engine = create_engine(DUMMY_URL)
    with pytest.raises(TestDatabaseSafetyError, match="TEST DATABASE SAFETY") as error:
        if operation == "session":
            with Session(engine) as session:
                session.execute(text("SELECT 1"))
        elif operation == "pool_recreation":
            engine.dispose()
            engine.connect()
        else:
            getattr(engine, operation)()
    assert "private_dummy" not in str(error.value)
    assert "application.invalid" not in str(error.value)

@pytest.mark.parametrize("connector", [psycopg.connect, psycopg.Connection.connect, psycopg.AsyncConnection.connect])
def test_direct_driver_connections_fail_closed(connector):
    with pytest.raises(TestDatabaseSafetyError):
        connector("host=application.invalid password=private_dummy")


def test_environment_is_neutralized_before_application_import():
    assert os.environ["TRADERAIL_DATABASE_URL"].endswith("/blocked_test_database")
    assert Settings().database_url.hosts()[0]["host"] == "127.0.0.1"


def test_explicit_application_settings_still_cannot_connect():
    app = create_app(Settings(database_url=DUMMY_URL, _env_file=None))
    @app.get("/test-db-probe")
    def probe():
        from app.db.session import get_engine
        with Session(get_engine(str(app.state.settings.database_url))) as db:
            db.execute(text("SELECT 1"))
    with pytest.raises(TestDatabaseSafetyError):
        TestClient(app).get("/test-db-probe")


def test_explicit_mocked_dependency_override_remains_supported():
    from fastapi import Depends
    app = create_app(Settings(database_url=DUMMY_URL, _env_file=None))
    isolated = Mock()
    isolated.scalar.return_value = 7
    app.dependency_overrides[get_session] = lambda: isolated
    @app.get("/test-isolated-probe")
    def probe(db=Depends(get_session)):
        return {"value": db.scalar(text("SELECT 7"))}
    assert TestClient(app).get("/test-isolated-probe").json() == {"value": 7}
    isolated.scalar.assert_called_once()


def test_python_subprocess_inherits_guard_without_domain_dependencies():
    script = """import os, socket, sys
assert os.environ['TRADERAIL_DATABASE_URL'].endswith('/blocked_test_database')
assert 'sqlalchemy' not in sys.modules
try:
    socket.create_connection(('127.0.0.1', 5432))
except RuntimeError as error:
    assert 'TEST DATABASE SAFETY' in str(error)
else:
    raise AssertionError('network guard missing')
"""
    child_env = dict(os.environ, TRADERAIL_DATABASE_URL=DUMMY_URL)
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=child_env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_subprocess_direct_libpq_and_sqlalchemy_cannot_bypass_network_guard():
    script = """import psycopg
from sqlalchemy import create_engine
for connect in (psycopg.connect, psycopg.Connection.connect, psycopg.AsyncConnection.connect,
                create_engine('postgresql+psycopg://dummy:dummy@127.0.0.1:1/test').connect):
    try:
        if getattr(connect, '__self__', None) is not None and hasattr(connect.__self__, 'dialect'):
            connect()
        else:
            connect('host=127.0.0.1 port=1 dbname=test')
    except RuntimeError as error:
        assert 'TEST DATABASE SAFETY' in str(error)
    else:
        raise AssertionError('driver guard missing')
"""
    child_env = dict(os.environ, TRADERAIL_DATABASE_URL=DUMMY_URL)
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=child_env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
