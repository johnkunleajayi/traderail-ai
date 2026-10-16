from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRADERAIL_", extra="ignore", hide_input_in_errors=True)
    app_name: str = "TradeRail"
    database_url: PostgresDsn = "postgresql+psycopg://traderail:traderail@localhost:5432/traderail"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://127.0.0.1:8000/api/auth/google/callback"
    session_secret: SecretStr = SecretStr("")
    frontend_url: str = "http://127.0.0.1:5173"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "none"] = "lax"
    session_max_age: int = Field(default=604800, ge=300, le=2592000)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret.get_secret_value()
                    and self.session_secret.get_secret_value())

    @model_validator(mode="after")
    def validate_auth(self):
        configured = [bool(self.google_client_id), bool(self.google_client_secret.get_secret_value()),
                      bool(self.session_secret.get_secret_value())]
        if any(configured) and not all(configured):
            raise ValueError("Configure Google client ID, client secret, and session secret together")
        if self.session_secret.get_secret_value() and len(self.session_secret.get_secret_value()) < 32:
            raise ValueError("Session secret must contain at least 32 characters")
        for name in ("frontend_url", "google_redirect_uri"):
            parsed = urlsplit(getattr(self, name))
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError(f"{name} must be an absolute HTTP(S) URL without credentials, query or fragment")
            if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError(f"{name} requires HTTPS outside loopback development")
        frontend = urlsplit(self.frontend_url)
        if frontend.path not in ("", "/"):
            raise ValueError("frontend_url must be an origin without a path")
        self.frontend_url = self.frontend_url.rstrip("/")
        if frontend.scheme == "https":
            if not self.session_cookie_secure or urlsplit(self.google_redirect_uri).scheme != "https":
                raise ValueError("Production HTTPS requires Secure cookies and an HTTPS callback")
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SameSite=None requires Secure cookies")
        if "*" in self.cors_origins:
            raise ValueError("Credentialed CORS requires explicit origins")
        if self.auth_enabled and self.frontend_url not in self.cors_origins:
            raise ValueError("frontend_url must be included in cors_origins")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()