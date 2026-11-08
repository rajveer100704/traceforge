"""Application settings (environment variables prefixed with TRACEFORGE_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRACEFORGE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["local", "test", "dev", "prod"] = "local"
    data_dir: Path = Path(".traceforge")
    database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    queue_backend: Literal["inline", "rq"] = "inline"
    queue_name: str = "traceforge"
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "traceforge"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_region: str = "us-east-1"
    sandbox_provider: Literal["local", "docker"] = "local"
    sandbox_image: str = "traceforge-sandbox:0.1"
    # Local sandbox executes candidate code as a host subprocess. It is restricted to trusted
    # (deterministic demo) policies unless explicitly overridden for single-user research.
    allow_untrusted_local_execution: bool = False
    # Control-plane auth: when set, /api/v1 requires the X-API-Key header. Required in prod.
    api_key: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=list)
    max_ingest_bytes: int = Field(default=5_000_000, ge=1024)
    log_level: str = "INFO"
    log_payloads: bool = False
    otel_enabled: bool = False
    otel_console_export: bool = False
    raw_retention_days: int = Field(default=30, ge=1)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    stale_lease_seconds: int = Field(default=900, ge=30)

    @model_validator(mode="after")
    def _defaults(self) -> Settings:
        if self.database_url is None:
            db_path = (self.data_dir / "traceforge.db").resolve()
            object.__setattr__(self, "database_url", f"sqlite:///{db_path.as_posix()}")
        if self.env == "prod" and self.allow_untrusted_local_execution:
            raise ValueError("allow_untrusted_local_execution is forbidden in prod")
        if self.env == "prod" and self.api_key is None:
            raise ValueError("TRACEFORGE_API_KEY must be set in prod")
        return self

    @property
    def resolved_database_url(self) -> str:
        assert self.database_url is not None
        return self.database_url

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
