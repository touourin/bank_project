import re
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BANK_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    neo4j_enabled: bool = False
    neo4j_uri: NonEmpty = "bolt://127.0.0.1:7690"
    neo4j_user: NonEmpty = "neo4j"
    neo4j_password: SecretStr | None = None
    neo4j_database: NonEmpty = "neo4j"

    # Read-only source credentials; connect only during an explicitly enabled import.
    mysql_host: NonEmpty = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: NonEmpty = "bank_project"
    mysql_user: NonEmpty = "bank_app"
    mysql_password: SecretStr | None = None
    mysql_source_enabled: bool = False
    mysql_source_tables: list[str] = Field(default_factory=list)

    data_dir: Path = Path("data/preparation")
    import_root: Path = Path("examples/mock")
    mapping_dir: Path = Path("configs/mappings")
    max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_records: int = Field(default=50000, ge=1, le=100000)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=16)
    api_token: SecretStr | None = None

    model_enabled: bool = False
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    model_prompt_file: Path | None = None
    model_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)

    health_timeout_seconds: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    # Reject the old switch so an existing configuration cannot silently disable Neo4j.
    legacy_graph_backend: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BANK_GRAPH_BACKEND", "graph_backend"),
        exclude=True,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_preparation(self) -> Self:
        if self.api_token is not None and len(self.api_token.get_secret_value()) < 24:
            raise ValueError("BANK_API_TOKEN must contain at least 24 characters")
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", table) for table in self.mysql_source_tables
        ):
            raise ValueError("BANK_MYSQL_SOURCE_TABLES must contain SQL identifiers only")
        if self.mysql_source_enabled and (
            not self.mysql_source_tables
            or self.mysql_password is None
            or not self.mysql_password.get_secret_value()
        ):
            raise ValueError("MySQL source requires an explicit table allowlist and password")
        if self.model_enabled:
            if not self.model_base_url or not self.model_name or not self.model_name.strip():
                raise ValueError(
                    "BANK_MODEL_BASE_URL and BANK_MODEL_NAME are required when the model is enabled"
                )
            try:
                uri = urlsplit(self.model_base_url)
                valid = (
                    uri.scheme in {"http", "https"}
                    and bool(uri.hostname)
                    and uri.port != 0
                    and uri.username is None
                    and uri.password is None
                    and not uri.query
                    and not uri.fragment
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(
                    "BANK_MODEL_BASE_URL must be an HTTP(S) base URL without credentials or query parameters"
                )
        return self

    @model_validator(mode="after")
    def validate_neo4j(self) -> Self:
        if self.legacy_graph_backend is not None:
            raise ValueError(
                "BANK_GRAPH_BACKEND has been replaced: use BANK_NEO4J_ENABLED=false "
                "instead of none, or BANK_NEO4J_ENABLED=true instead of neo4j; remove the old key"
            )
        if not self.neo4j_enabled:
            return self
        if self.neo4j_password is None or not self.neo4j_password.get_secret_value().strip():
            raise ValueError("BANK_NEO4J_PASSWORD must be set when Neo4j is enabled")
        try:
            uri = urlsplit(self.neo4j_uri)
            valid = (
                uri.scheme in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
                and bool(uri.hostname)
                and uri.port != 0
                and uri.username is None
                and uri.password is None
                and not uri.fragment
                and uri.path in {"", "/"}
                and (not uri.query or uri.scheme.startswith("neo4j"))
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("BANK_NEO4J_URI must be a Neo4j URI without embedded credentials")
        return self
