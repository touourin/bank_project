"""Read-only MySQL source adapter. Credentials live only for the request."""

from contextlib import contextmanager
from typing import Annotated

import pymysql
from pydantic import Field, SecretStr, StringConstraints

from bank_project.intake.models import (
    Column,
    DataRow,
    ForeignKey,
    IntakeError,
    Limits,
    ParsedSource,
    ParsedTable,
    StrictRequest,
)
from bank_project.intake.values import cell_text

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class MysqlConnection(StrictRequest):
    host: Name
    port: int = Field(default=3306, ge=1, le=65535)
    database: Name
    user: Name
    password: SecretStr = Field(default=SecretStr(""), max_length=4096)


class MysqlRequest(StrictRequest):
    connection: MysqlConnection | None = None


class MysqlImport(MysqlRequest):
    tables: list[Name] = Field(min_length=1, max_length=30)


class CatalogTable(StrictRequest):
    name: str
    comment: str
    estimated_rows: int


def _identifier(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


class MysqlSource:
    def __init__(self, limits: Limits):
        self.limits = limits

    @contextmanager
    def connect(self, params: MysqlConnection):
        connection = None
        try:
            connection = pymysql.connect(
                host=params.host,
                port=params.port,
                user=params.user,
                password=params.password.get_secret_value(),
                database=params.database,
                charset="utf8mb4",
                connect_timeout=5,
                read_timeout=15,
                write_timeout=15,
                autocommit=False,
                local_infile=False,
            )
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            yield connection
        except pymysql.MySQLError as exc:
            raise IntakeError(
                "MySQL 连接或读取失败，请检查地址、账号权限及网络；本次未保存数据", status=502
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _catalog(connection, database: str) -> list[CatalogTable]:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME""",
                (database,),
            )
            return [
                CatalogTable(name=name, comment=comment or "", estimated_rows=count or 0)
                for name, comment, count in cursor.fetchall()
            ]

    def catalog(self, params: MysqlConnection) -> list[CatalogTable]:
        with self.connect(params) as connection:
            return self._catalog(connection, params.database)

    def read(self, params: MysqlConnection, names: list[str], sink=None) -> ParsedSource:
        names = list(dict.fromkeys(names))
        if len(names) > self.limits.max_tables:
            raise IntakeError("所选表数超过单批次上限")
        tables = []
        total_cells = 0
        with self.connect(params) as connection:
            catalog = {table.name for table in self._catalog(connection, params.database)}
            if any(name not in catalog for name in names):
                raise IntakeError("所选表已不存在或当前账号无权读取，请刷新表清单")
            for name in names:
                table = self._read_table(
                    connection, params.database, name, self.limits.max_cells - total_cells, sink
                )
                total_cells += len(table.columns) * (table.row_count if sink else len(table.rows))
                tables.append(table)
        return ParsedSource(
            tables, ["MySQL 通过只读事务获取数据；行数超限会拒绝整个批次，不截断保存。"]
        )

    def _read_table(
        self, connection, database: str, name: str, remaining_cells: int, sink=None
    ) -> ParsedTable:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT, COLUMN_KEY
                FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION""",
                (database, name),
            )
            columns = [
                Column(
                    name=n,
                    data_type=t,
                    type_origin="declared",
                    nullable=nullable == "YES",
                    comment=comment or "",
                    primary_key=key == "PRI",
                )
                for n, t, nullable, comment, key in cursor.fetchall()
            ]
            if not columns or len(columns) > self.limits.max_columns:
                raise IntakeError("无法读取表结构或字段数超限", location=name)
            cursor.execute(
                """SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA,
                REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL
                ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION""",
                (database, name),
            )
            foreign_keys: dict[str, ForeignKey] = {}
            for key, col, target_schema, target_table, target_col in cursor.fetchall():
                foreign_keys.setdefault(
                    key,
                    ForeignKey(
                        name=key,
                        columns=[],
                        target_schema=target_schema,
                        target_table=target_table,
                        target_columns=[],
                    ),
                )
                foreign_keys[key].columns.append(col)
                foreign_keys[key].target_columns.append(target_col)
        # Project the inspected columns explicitly; retain declared ordering.
        projection = ", ".join(_identifier(column.name) for column in columns)
        pk = [c.name for c in columns if c.primary_key]
        order = " ORDER BY " + ", ".join(map(_identifier, pk)) if pk else ""
        query = (
            f"SELECT {projection} FROM {_identifier(database)}.{_identifier(name)}{order} LIMIT %s"
        )
        rows, number = [], 0
        if sink:
            sink.start(name, columns, list(foreign_keys.values()))
        with connection.cursor(pymysql.cursors.SSCursor) as cursor:
            cursor.execute(query, (min(self.limits.max_rows, remaining_cells // len(columns)) + 1,))
            while records := cursor.fetchmany(100):
                for record in records:
                    number += 1
                    if number > self.limits.max_rows or number * len(columns) > remaining_cells:
                        raise IntakeError(
                            "所选数据超过行数或单元格上限，请拆分数据源；本次未保存任何表",
                            location=name,
                        )
                    row = DataRow(
                        number=number,
                        values=[
                            cell_text(value, self.limits, f"{name} · 第 {number} 行")
                            for value in record
                        ],
                    )
                    if len(row.model_dump_json().encode()) > 2 * 1024 * 1024:
                        raise IntakeError("单行内容超过 2 MB", location=name)
                    if sink:
                        sink.append(row)
                    else:
                        rows.append(row)
        if sink:
            sink.finish(columns)
        return ParsedTable(name, columns, rows, list(foreign_keys.values()), row_count=number)
