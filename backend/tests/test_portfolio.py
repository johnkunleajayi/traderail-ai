import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint
from sqlalchemy.dialects import postgresql

from app.auth.dependencies import get_current_user
from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.portfolio.models import PaperPortfolio
from app.portfolio.schemas import PaperPortfolioResponse
from app.portfolio.service import ensure_paper_portfolio


@pytest.fixture
def portfolios():
    """Mock SQLAlchemy boundaries only; real PostgreSQL checks run separately."""
    db = Mock()
    rows = {}
    statements = []
    user = SimpleNamespace(id=uuid.uuid4())
    def execute(statement):
        statements.append(statement)
        params = statement.compile().params
        user_id = params["user_id"]
        if user_id not in rows:
            now = datetime.now(timezone.utc)
            rows[user_id] = PaperPortfolio(
                id=params["id"], user_id=user_id, environment="PAPER",
                base_currency="USDT", starting_balance=Decimal("10000.00"),
                cash_balance=Decimal("10000.00"), status="active",
                created_at=now, updated_at=now,
            )
    def scalar(statement):
        return rows.get(statement.compile().params["user_id_1"])
    db.execute.side_effect = execute
    db.scalar.side_effect = scalar
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return SimpleNamespace(db=db, rows=rows, user=user, statements=statements,
                           app=app, client=TestClient(app))


def test_authenticated_creation_and_defaults(portfolios):
    response = portfolios.client.get("/api/portfolio")
    assert response.status_code == 200
    assert response.json() == {
        "environment": "PAPER", "base_currency": "USDT",
        "starting_balance": "10000.00", "cash_balance": "10000.00", "status": "active",
    }
    assert Decimal(response.json()["starting_balance"]) == Decimal("10000.00")
    assert response.headers["cache-control"] == "no-store"
    assert isinstance(portfolios.rows[portfolios.user.id].cash_balance, Decimal)
    portfolios.db.commit.assert_called_once()


def test_existing_user_gets_missing_portfolio(portfolios):
    assert portfolios.user.id not in portfolios.rows
    portfolios.client.get("/api/portfolio")
    assert portfolios.user.id in portfolios.rows


def test_repeated_access_never_duplicates_or_resets(portfolios):
    first = portfolios.client.get("/api/portfolio")
    row = portfolios.rows[portfolios.user.id]
    original_id = row.id
    row.cash_balance = Decimal("8765.43")
    second = portfolios.client.get("/api/portfolio")
    assert len(portfolios.rows) == 1
    assert row.id == original_id
    assert Decimal(second.json()["cash_balance"]) == Decimal("8765.43")
    assert Decimal(second.json()["starting_balance"]) == Decimal(first.json()["starting_balance"])
    assert row.starting_balance == Decimal("10000.00")


def test_users_are_isolated(portfolios):
    first_id = portfolios.user.id
    portfolios.client.get("/api/portfolio")
    portfolios.rows[first_id].cash_balance = Decimal("1234.56")
    portfolios.user.id = uuid.uuid4()
    response = portfolios.client.get("/api/portfolio")
    assert Decimal(response.json()["cash_balance"]) == Decimal("10000.00")
    assert len(portfolios.rows) == 2
    assert portfolios.rows[first_id].cash_balance == Decimal("1234.56")


def test_anonymous_access_is_401():
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/api/portfolio")
    assert response.status_code == 401


def test_database_constraints_and_decimal_types():
    table = PaperPortfolio.__table__
    assert set(table.columns.keys()) == {
        "id", "user_id", "environment", "base_currency", "starting_balance",
        "cash_balance", "status", "created_at", "updated_at",
    }
    unique = [tuple(column.name for column in constraint.columns)
              for constraint in table.constraints if isinstance(constraint, UniqueConstraint)]
    assert ("user_id",) in unique
    assert list(table.columns.user_id.foreign_keys)[0].target_fullname == "users.id"
    for name in ("starting_balance", "cash_balance"):
        column = table.columns[name]
        assert isinstance(column.type, Numeric)
        assert column.type.scale == (2 if name == "starting_balance" else None)
        if name == "cash_balance": assert column.type.precision is None
        assert column.type.asdecimal
        assert column.default.arg == Decimal("10000.00")
    checks = {str(constraint.sqltext) for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
    assert "environment = 'PAPER'" in checks and "base_currency = 'USDT'" in checks
    assert table.columns.created_at.type.timezone and table.columns.updated_at.type.timezone
    assert table.columns.updated_at.onupdate is not None


def test_creation_uses_postgres_atomic_insert_without_update(portfolios):
    ensure_paper_portfolio(portfolios.db, portfolios.user.id)
    sql = str(portfolios.statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (user_id) DO NOTHING" in sql
    assert "DO UPDATE" not in sql
    portfolios.db.commit.assert_not_called()


def test_response_money_is_exact_and_hides_internal_fields(portfolios):
    ensure_paper_portfolio(portfolios.db, portfolios.user.id)
    row = portfolios.rows[portfolios.user.id]
    row.cash_balance = Decimal("9999.99")
    result = PaperPortfolioResponse.model_validate(row).model_dump(mode="json")
    assert result["cash_balance"] == "9999.99"
    assert "user_id" not in result and "id" not in result
    assert "created_at" not in result and "updated_at" not in result


def test_database_errors_are_sanitized(portfolios):
    from sqlalchemy.exc import OperationalError
    portfolios.db.execute.side_effect = OperationalError("private-query", {}, Exception("private-password"))
    response = portfolios.client.get("/api/portfolio")
    assert response.status_code == 503
    assert "private-password" not in response.text
    assert "private-query" not in response.text
