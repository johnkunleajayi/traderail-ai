import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import create_app
from app.core.config import Settings

@pytest.fixture
def client():
    return TestClient(create_app(Settings(_env_file=None)))

def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_allowed_origin(client):
    response = client.options("/api/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

def test_untrusted_origin(client):
    response = client.options("/api/health", headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers

def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("TRADERAIL_DATABASE_URL", "postgresql+psycopg://user:password@db.example:5432/traderail")
    monkeypatch.setenv("TRADERAIL_CORS_ORIGINS", '["https://traderail.example"]')
    settings = Settings(_env_file=None)
    assert settings.database_url.hosts()[0]["host"] == "db.example"
    assert settings.cors_origins == ["https://traderail.example"]

def test_reject_non_postgres():
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///test.db", _env_file=None)

def test_custom_origin():
    client = TestClient(create_app(Settings(cors_origins=["https://traderail.example"], _env_file=None)))
    assert client.get("/api/health", headers={"Origin": "https://traderail.example"}).headers["access-control-allow-origin"] == "https://traderail.example"