"""MySQL boundary tests use a fake driver, never real bank data or credentials."""

from decimal import Decimal

import pymysql
import pytest

from bank_project.intake.models import IntakeError, Limits
from bank_project.intake.mysql import MysqlConnection, MysqlSource


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, params=None):
        self.connection.calls.append((sql, params))
        if "information_schema.TABLES" in sql:
            self.result = [("customers", "客户", 2)]
        elif "information_schema.COLUMNS" in sql:
            self.result = [
                ("id", "varchar(20)", "NO", "编号", "PRI"),
                ("amount", "decimal(30,4)", "YES", "金额", ""),
                ("region", "varchar(20)", "YES", "", ""),
            ]
        elif "KEY_COLUMN_USAGE" in sql:
            self.result = [
                ("fk_composite", "id", "bank", "related", "customer_id"),
                ("fk_composite", "region", "bank", "related", "region_id"),
            ]
        elif sql.startswith("SELECT `"):
            self.result = self.connection.rows

    def fetchall(self):
        return self.result

    def fetchmany(self, n):
        rows, self.result = self.result[:n], self.result[n:]
        return rows


class Connection:
    def __init__(self):
        self.calls = []
        self.rows = [
            ("00001", Decimal("12345678901234567890.1200"), "上海"),
            ("00002", None, "北京"),
        ]
        self.closed = False

    def cursor(self, *_):
        return Cursor(self)

    def close(self):
        self.closed = True


def params():
    return MysqlConnection(host="db", database="bank", user="reader", password="secret-never-store")


@pytest.fixture
def connection(monkeypatch):
    connection = Connection()

    def connect(**kwargs):
        assert kwargs["local_infile"] is False
        assert kwargs["read_timeout"] == 15
        return connection

    monkeypatch.setattr(pymysql, "connect", connect)
    return connection


def test_mysql_reads_declared_keys_and_preserves_decimal(connection):
    result = MysqlSource(Limits()).read(params(), ["customers"])
    table = result.tables[0]
    assert table.rows[0].values == ["00001", "12345678901234567890.1200", "上海"]
    assert table.rows[1].values[1] is None
    assert table.columns[0].primary_key
    assert table.columns[1].data_type == "decimal(30,4)"
    assert table.foreign_keys[0].columns == ["id", "region"]
    assert table.foreign_keys[0].target_columns == ["customer_id", "region_id"]
    assert connection.calls[0][0] == "SET TRANSACTION READ ONLY"
    assert connection.calls[1][0] == "START TRANSACTION WITH CONSISTENT SNAPSHOT"
    assert connection.closed
    assert "secret-never-store" not in repr(result)


def test_mysql_table_names_must_come_from_catalog(connection):
    with pytest.raises(IntakeError, match="所选表"):
        MysqlSource(Limits()).read(params(), ["customers`; DROP TABLE customers; --"])
    assert not any("DROP TABLE" in sql for sql, _ in connection.calls)
    assert connection.closed


def test_mysql_limit_rejects_instead_of_truncating(connection):
    with pytest.raises(IntakeError, match="上限"):
        MysqlSource(Limits(max_rows=1)).read(params(), ["customers"])
    assert connection.closed


def test_mysql_driver_errors_do_not_echo_credentials(monkeypatch):
    def fail(**_):
        raise pymysql.OperationalError("secret-never-store")

    monkeypatch.setattr(pymysql, "connect", fail)
    with pytest.raises(IntakeError) as error:
        MysqlSource(Limits()).catalog(params())
    assert "secret-never-store" not in str(error.value)
