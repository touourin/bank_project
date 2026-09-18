import pytest
from pydantic import ValidationError

from bank_project.bootstrap import build_services
from bank_project.settings import Settings


@pytest.mark.parametrize("password", [None, "", "   "])
def test_neo4j_requires_nonempty_credentials(password):
    with pytest.raises(ValidationError, match="BANK_NEO4J_PASSWORD must be set"):
        Settings(neo4j_enabled=True, neo4j_password=password, _env_file=None)


@pytest.mark.parametrize(
    "uri",
    ["https://localhost", "bolt://", "bolt://host:invalid", "bolt://host:0", "bolt://host/db"],
)
def test_invalid_neo4j_addresses_are_rejected_before_connecting(uri):
    with pytest.raises(ValidationError, match="BANK_NEO4J_URI"):
        Settings(neo4j_enabled=True, neo4j_uri=uri, neo4j_password="test-secret", _env_file=None)


def test_configuration_errors_do_not_echo_credentials():
    secret = "private-test-credential"
    with pytest.raises(ValidationError) as error:
        Settings(
            neo4j_enabled=True,
            neo4j_uri=f"bolt://user:{secret}@localhost",
            neo4j_password=secret,
            _env_file=None,
        )
    assert secret not in str(error.value)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_health_check_requires_a_finite_positive_timeout(timeout):
    with pytest.raises(ValidationError):
        Settings(neo4j_enabled=False, health_timeout_seconds=timeout, _env_file=None)


def test_local_framework_does_not_require_database_credentials():
    settings = Settings(neo4j_enabled=False, neo4j_password=None, _env_file=None)
    assert settings.neo4j_password is None


def test_encrypted_routing_uri_is_supported():
    settings = Settings(
        neo4j_enabled=True,
        neo4j_uri="neo4j+s://db.example:7687?policy=region",
        neo4j_password="test-secret",
        _env_file=None,
    )
    assert settings.neo4j_uri.startswith("neo4j+s://")


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_neo4j_switch_is_loaded_from_environment(monkeypatch, enabled):
    monkeypatch.setenv("BANK_NEO4J_ENABLED", enabled)
    monkeypatch.setenv("BANK_NEO4J_PASSWORD", "test-secret")
    settings = Settings(_env_file=None)
    assert settings.neo4j_enabled is (enabled == "true")


def test_mysql_settings_are_loaded_and_masked_without_connecting(tmp_path):
    env_file = tmp_path / ".env"
    secret = "private-mysql-credential"
    env_file.write_text(
        "BANK_NEO4J_ENABLED=false\n"
        "BANK_MYSQL_HOST=mysql.example\n"
        "BANK_MYSQL_PORT=3307\n"
        "BANK_MYSQL_DATABASE=test_bank\n"
        "BANK_MYSQL_USER=test_app\n"
        f"BANK_MYSQL_PASSWORD={secret}\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env_file)
    assert (settings.mysql_host, settings.mysql_port) == ("mysql.example", 3307)
    assert (settings.mysql_database, settings.mysql_user) == ("test_bank", "test_app")
    assert settings.mysql_password.get_secret_value() == secret
    assert secret not in repr(settings)
    assert secret not in settings.model_dump_json()
    assert build_services(settings).storage is None


@pytest.mark.parametrize("port", [0, 65536, "not-a-port"])
def test_invalid_mysql_ports_are_rejected(port):
    with pytest.raises(ValidationError, match="mysql_port"):
        Settings(mysql_port=port, _env_file=None)


@pytest.mark.parametrize("source", ["environment", "dotenv", "constructor"])
def test_old_graph_switch_cannot_silently_disable_neo4j(tmp_path, monkeypatch, source):
    arguments = {"_env_file": None}
    if source == "environment":
        monkeypatch.setenv("BANK_GRAPH_BACKEND", "neo4j")
    elif source == "dotenv":
        env_file = tmp_path / ".env"
        env_file.write_text("BANK_GRAPH_BACKEND=neo4j\n", encoding="utf-8")
        arguments["_env_file"] = env_file
    else:
        arguments["graph_backend"] = "neo4j"
    with pytest.raises(ValidationError, match="BANK_GRAPH_BACKEND has been replaced"):
        Settings(**arguments)


def test_old_backend_value_is_not_accepted_as_boolean():
    with pytest.raises(ValidationError, match="neo4j_enabled"):
        Settings(neo4j_enabled="none", _env_file=None)


@pytest.mark.parametrize(
    "values",
    [
        {"api_token": "too-short"},
        {"model_enabled": True},
        {
            "model_enabled": True,
            "model_name": "test",
            "model_base_url": "https://secret:password@host/v1",
        },
        {"model_enabled": True, "model_name": "test", "model_base_url": "file:///private"},
        {"mysql_source_enabled": True, "mysql_password": "secret"},
        {"mysql_source_tables": ["orders; DROP TABLE customers"]},
        {"max_file_bytes": 0},
        {"max_concurrent_jobs": 0},
        {"max_concurrent_jobs": 17},
    ],
)
def test_preparation_configuration_rejects_invalid_values(values):
    with pytest.raises(ValidationError):
        Settings(**values, _env_file=None)
