"""Application and data source configuration; secrets stay on the server."""

from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BANK_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    data_dir: Path = Path("data")
    api_token: SecretStr | None = None
    intake_max_upload_mb: int = Field(default=512, ge=1, le=4096)
    intake_max_expanded_mb: int = Field(default=8192, ge=1, le=65536)
    staging_backend: Literal["sqlite", "mysql"] = "sqlite"
    staging_mysql_host: NonEmpty = "127.0.0.1"
    staging_mysql_port: int = Field(default=3306, ge=1, le=65535)
    staging_mysql_database: NonEmpty = "bank_staging"
    staging_mysql_user: NonEmpty = "bank_staging"
    staging_mysql_password: SecretStr | None = None
    intake_max_rows: int = Field(default=2_000_000, ge=1, le=50_000_000)
    intake_max_cells: int = Field(default=200_000_000, ge=1, le=2_000_000_000)

    mysql_host: NonEmpty = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: NonEmpty = "bank_project"
    mysql_user: NonEmpty = "bank_app"
    mysql_password: SecretStr | None = None

    neo4j_uri: NonEmpty = "bolt://127.0.0.1:7690"
    neo4j_user: NonEmpty = "neo4j"
    neo4j_password: SecretStr | None = None
    neo4j_database: NonEmpty = "neo4j"
    ontology_snapshot: Path = Path("data/ontology/snapshot.json")
    ontology_revision: str | None = None
    retrieve_base_url: str | None = None
    retrieve_timeout_seconds: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    retrieve_concurrency: int = Field(default=3, ge=1, le=8)
    alignment_min_confidence: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    alignment_batch_columns: int = Field(default=32, ge=1, le=64)
    alignment_model_concurrency: int = Field(default=3, ge=1, le=8)

    model_provider: Literal["dashscope", "openai_compatible"] = "dashscope"
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    model_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    model_max_tokens: int = Field(default=4096, ge=1, le=32768)
    model_max_retries: int = Field(default=2, ge=0, le=3)

    @field_validator("retrieve_base_url")
    @classmethod
    def validate_retrieve_url(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path.endswith("/retrieve")
        ):
            raise ValueError(
                "retrieve 地址需要 HTTP(S) 本体根路径，不能包含凭据、查询参数或末尾 /retrieve"
            )
        return value

    def intake_limits(self):
        from bank_project.intake.models import Limits

        if self.staging_backend == "sqlite":
            return Limits(
                max_upload_bytes=min(self.intake_max_upload_mb, 20) * 1024 * 1024,
                max_rows=min(self.intake_max_rows, 50000),
                max_cells=min(self.intake_max_cells, 2000000),
            )
        return Limits(
            max_upload_bytes=self.intake_max_upload_mb * 1024 * 1024,
            max_expanded_bytes=self.intake_max_expanded_mb * 1024 * 1024,
            max_rows=self.intake_max_rows,
            max_cells=self.intake_max_cells,
            max_columns=2048,
        )

    @model_validator(mode="after")
    def validate_api_token(self) -> Self:
        if self.api_token is not None and len(self.api_token.get_secret_value()) < 24:
            raise ValueError("BANK_API_TOKEN must contain at least 24 characters")
        if self.staging_backend == "mysql" and not self.staging_mysql_password:
            raise ValueError("内部暂存库需要 BANK_STAGING_MYSQL_PASSWORD")
        if self.staging_backend == "mysql" and (self.staging_mysql_user == self.mysql_user):
            raise ValueError("内部暂存与外部数据源必须使用独立账号")
        return self
