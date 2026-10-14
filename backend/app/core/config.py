from functools import lru_cache
from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRADERAIL_", extra="ignore")
    app_name: str = "TradeRail"
    database_url: PostgresDsn = "postgresql+psycopg://traderail:traderail@localhost:5432/traderail"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

@lru_cache
def get_settings() -> Settings:
    return Settings()