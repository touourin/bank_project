"""Fixture persistence must preserve source types and be repeatable without overwrites."""

from decimal import Decimal

import pytest
from dotenv import dotenv_values

from scripts.mock_data.common import load_schema
from scripts.mock_data.mysql import DDL_PATH, record_values, schema_sql
from scripts.prepare_local_mysql import prepare


def test_database_schema_tracks_source_contract_and_preserves_json_text():
    sql = schema_sql(load_schema())
    assert DDL_PATH.read_text(encoding="utf-8") == sql
    assert "`PROPERTIES` LONGTEXT" in sql
    assert "JSON_VALID(`PROPERTIES`)" in sql
    assert "PRIMARY KEY (`cust_ind`)" in sql
    assert "PRIMARY KEY (`DT`, `ROWKEY`)" in sql
    # Mock-only non-null assumptions must not become undocumented bank requirements.
    assert "`KEY_FLAG` VARCHAR(1) NULL" in sql


def test_loading_keeps_leading_zeros_decimal_precision_and_json_original():
    columns = [
        {"name": "id", "sql_type": "varchar(50)"},
        {"name": "amount", "sql_type": "decimal(26,8)"},
        {"name": "count", "sql_type": "int"},
        {"name": "properties", "sql_type": "STRING"},
        {"name": "optional", "sql_type": "varchar(20)"},
    ]
    raw_json = '{"金额":123456789012345678.12345678,"账号":"000012"}'
    row = {
        "id": "00001",
        "amount": "123456789012345678.12345678",
        "count": "7",
        "properties": raw_json,
        "optional": "",
    }
    assert record_values(row, columns) == (
        "00001",
        Decimal("123456789012345678.12345678"),
        7,
        raw_json,
        None,
    )


def test_local_database_setup_preserves_existing_configuration_and_credentials(
    tmp_path, monkeypatch
):
    for key in ("BANK_MYSQL_PASSWORD", "BANK_MYSQL_USER", "BANK_MYSQL_HOST"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("BANK_NEO4J_PASSWORD=leave-this-alone\nBANK_MYSQL_PASSWORD=\n")
    prepare(tmp_path)
    first = env.read_bytes()
    root_password = (tmp_path / "data/mysql/root-password").read_bytes()
    prepare(tmp_path)
    assert env.read_bytes() == first
    assert (tmp_path / "data/mysql/root-password").read_bytes() == root_password
    values = dotenv_values(env)
    assert values["BANK_NEO4J_PASSWORD"] == "leave-this-alone"
    assert len(values["BANK_MYSQL_PASSWORD"]) >= 32
    assert env.stat().st_mode & 0o077 == 0
    assert (tmp_path / "data/mysql/root-password").stat().st_mode & 0o077 == 0


def test_local_database_setup_refuses_external_database(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_MYSQL_HOST", "existing-db.example")
    with pytest.raises(ValueError, match="外部 MySQL"):
        prepare(tmp_path)
    assert not (tmp_path / ".env").exists()
