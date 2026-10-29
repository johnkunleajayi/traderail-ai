"""Server-only process configuration; no implicit dotenv or database target."""
from pydantic import Field, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class RunnerSettings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix="TRADERAIL_",env_file=None,extra="ignore",hide_input_in_errors=True)
    database_url: PostgresDsn | None = Field(default=None,repr=False,exclude=True)
    paper_runner_enabled: bool = False
    paper_runner_poll_seconds: int = Field(default=15,ge=5,le=300)
    paper_runner_page_size: int = Field(default=100,ge=1,le=1000)
    paper_runner_connect_timeout_seconds: int = Field(default=5,ge=2,le=30)
    paper_runner_statement_timeout_ms: int = Field(default=15000,ge=1000,le=60000)
    paper_runner_lock_timeout_ms: int = Field(default=3000,ge=100,le=10000)
    paper_runner_idle_transaction_timeout_ms: int = Field(default=30000,ge=5000,le=120000)
    paper_runner_network_timeout_seconds: int = Field(default=8,ge=1,le=30)

    @model_validator(mode="after")
    def operational_bounds(self):
        if self.paper_runner_enabled and (self.database_url is None or self.database_url.scheme!="postgresql+psycopg"):
            raise ValueError("Enabled PAPER runner requires an explicit PostgreSQL psycopg database URL")
        if self.paper_runner_lock_timeout_ms>=self.paper_runner_statement_timeout_ms:
            raise ValueError("Lock timeout must be less than statement timeout")
        return self
