import pytest
from pydantic import ValidationError

from bank_project.settings import Settings


@pytest.mark.parametrize("password", [None, "", "   "])
def test_neo4j_requires_nonempty_credentials(password):
    with pytest.raises(ValidationError, match="BANK_NEO4J_PASSWORD must be set"):
        Settings(graph_backend="neo4j", neo4j_password=password, _env_file=None)


@pytest.mark.parametrize(
    "uri",
    ["https://localhost", "bolt://", "bolt://host:invalid", "bolt://host:0", "bolt://host/db"],
)
def test_invalid_neo4j_addresses_are_rejected_before_connecting(uri):
    with pytest.raises(ValidationError, match="BANK_NEO4J_URI"):
        Settings(graph_backend="neo4j", neo4j_uri=uri, neo4j_password="test-secret", _env_file=None)


def test_configuration_errors_do_not_echo_credentials():
    secret = "private-test-credential"
    with pytest.raises(ValidationError) as error:
        Settings(
            graph_backend="neo4j",
            neo4j_uri=f"bolt://user:{secret}@localhost",
            neo4j_password=secret,
            _env_file=None,
        )
    assert secret not in str(error.value)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_health_check_requires_a_finite_positive_timeout(timeout):
    with pytest.raises(ValidationError):
        Settings(graph_backend="none", health_timeout_seconds=timeout, _env_file=None)


def test_local_framework_does_not_require_database_credentials():
    settings = Settings(graph_backend="none", neo4j_password=None, _env_file=None)
    assert settings.neo4j_password is None


def test_encrypted_routing_uri_is_supported():
    settings = Settings(
        graph_backend="neo4j",
        neo4j_uri="neo4j+s://db.example:7687?policy=region",
        neo4j_password="test-secret",
        _env_file=None,
    )
    assert settings.neo4j_uri.startswith("neo4j+s://")
