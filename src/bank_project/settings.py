from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BANK_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    graph_backend: Literal["none", "neo4j"] = "none"
    neo4j_uri: NonEmpty = "bolt://127.0.0.1:7690"
    neo4j_user: NonEmpty = "neo4j"
    neo4j_password: SecretStr | None = None
    neo4j_database: NonEmpty = "neo4j"
    health_timeout_seconds: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_neo4j(self) -> Self:
        if self.graph_backend == "none":
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
