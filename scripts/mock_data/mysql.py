"""Offline fixture loading; never called by generic ingestion or conversion services."""

import argparse
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pymysql

from bank_project.settings import Settings

from .common import DEFAULT_OUTPUT, JOURNEY_TABLE, ROOT, TAG_TABLE, load_schema, read_csv
from .validation import validate_directory

DDL_PATH = ROOT / "database/mysql/schema.sql"


@dataclass(frozen=True)
class Table:
    source: str
    name: str
    comment: str
    indexes: tuple[tuple[str, tuple[str, ...]], ...] = ()


TABLES = (
    Table(TAG_TABLE, "customer_tags", "合成客户标签快照；来源 CCM_C_CUST_FLAG_INFO"),
    Table(
        JOURNEY_TABLE,
        "customer_journey_events",
        "合成客户旅程事件；来源 E_CRM_C_CUST_TOUR_EVT_SUM",
        (
            ("idx_customer_date", ("CUST_ID", "OCCUR_DT")),
            ("idx_event_type_date", ("EVT_TYPE", "OCCUR_DT")),
        ),
    ),
)


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", value):
        raise ValueError("SQL 标识符无效")
    return f"`{value}`"


def literal(value: str) -> str:
    if "\\" in value or "\x00" in value:
        raise ValueError("SQL 注释包含不支持的字符")
    return "'" + value.replace("'", "''") + "'"


def mysql_type(column: dict) -> str:
    kind = column["sql_type"].lower()
    if kind == "string":
        return "LONGTEXT"
    if kind == "int" or re.fullmatch(r"varchar\([1-9][0-9]*\)|decimal\([0-9]+,[0-9]+\)", kind):
        return kind.upper()
    raise ValueError("字段契约中含有不支持的 MySQL 类型")


def create_statement(table: Table, specification: dict) -> str:
    columns = []
    for column in specification["columns"]:
        required = (
            column["name"] in specification["primary_key"] or column.get("source_nullable") is False
        )
        description = column["description"]
        if column.get("format") == "json_object":
            description += "；JSON 原文，保留十进制数精度"
        columns.append(
            f"  {identifier(column['name'])} {mysql_type(column)} "
            f"{'NOT NULL' if required else 'NULL'} COMMENT {literal(description)}"
        )
    columns.append(
        "  PRIMARY KEY (" + ", ".join(map(identifier, specification["primary_key"])) + ")"
    )
    for name, fields in table.indexes:
        columns.append(f"  KEY {identifier(name)} (" + ", ".join(map(identifier, fields)) + ")")
    for column in specification["columns"]:
        if column.get("format") == "json_object":
            field = identifier(column["name"])
            columns.append(
                f"  CHECK ({field} IS NULL OR (JSON_VALID({field}) AND JSON_TYPE({field}) = 'OBJECT'))"
            )
    return (
        f"CREATE TABLE IF NOT EXISTS {identifier(table.name)} (\n"
        + ",\n".join(columns)
        + f"\n) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin COMMENT={literal(table.comment)};"
    )


def schema_sql(schema: dict) -> str:
    return (
        "-- Generated from configs/bank/schema.json by scripts/load_mock_mysql.py --write-schema.\n"
        "-- Synthetic source data only. Use the configured database; no DROP/TRUNCATE/ALTER.\n\n"
        + "\n\n".join(create_statement(t, schema["tables"][t.source]) for t in TABLES)
        + "\n"
    )


def record_values(row: dict, columns: list[dict]) -> tuple:
    values = []
    for column in columns:
        raw = row[column["name"]]
        kind = column["sql_type"].lower()
        if raw == "":
            value = None
        elif kind.startswith("decimal("):
            value = Decimal(raw)
        elif kind == "int":
            value = int(raw)
        else:
            value = raw
        values.append(value)
    return tuple(values)


def verify_layout(cursor, table: Table, spec: dict) -> None:
    cursor.execute(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
        (table.name,),
    )
    actual = cursor.fetchall()
    expected = []
    for column in spec["columns"]:
        kind = mysql_type(column).lower()
        required = column["name"] in spec["primary_key"] or column.get("source_nullable") is False
        expected.append(
            (
                column["name"],
                kind,
                "NO" if required else "YES",
                "utf8mb4_0900_bin" if kind.startswith("varchar") or kind == "longtext" else None,
            )
        )
    if list(actual) != expected:
        raise ValueError(f"{table.name}: 已有表结构与字段契约不同，拒绝修改或导入")
    cursor.execute(
        "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME=%s AND INDEX_NAME='PRIMARY' ORDER BY SEQ_IN_INDEX",
        (table.name,),
    )
    if [item[0] for item in cursor.fetchall()] != spec["primary_key"]:
        raise ValueError(f"{table.name}: 已有表主键不同，拒绝导入")
    cursor.execute(
        "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
        (table.name,),
    )
    if cursor.fetchone() != ("InnoDB",):
        raise ValueError(f"{table.name}: 必须使用支持事务的 InnoDB")


def load_rows(connection, schema: dict, data: dict) -> dict:
    """Both tables commit together. Repeated identical rows are skipped; conflicts roll back."""
    report = {}
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(CONCAT(DATABASE(), '.mock-fixtures'), 0)")
        if cursor.fetchone() != (1,):
            raise ValueError("另一个 mock 导入正在进行")
        try:
            for table in TABLES:
                spec = schema["tables"][table.source]
                cursor.execute(create_statement(table, spec))
                verify_layout(cursor, table, spec)
            connection.begin()
            for table in TABLES:
                spec = schema["tables"][table.source]
                columns = spec["columns"]
                names = [c["name"] for c in columns]
                fields = ", ".join(map(identifier, names))
                positions = [names.index(key) for key in spec["primary_key"]]
                cursor.execute(f"SELECT {fields} FROM {identifier(table.name)} FOR UPDATE")
                existing = {tuple(row[i] for i in positions): row for row in cursor.fetchall()}
                missing, unchanged = [], 0
                for row in data[table.source]:
                    values = record_values(row, columns)
                    previous = existing.get(tuple(values[i] for i in positions))
                    if previous is None:
                        missing.append(values)
                    elif previous == values:
                        unchanged += 1
                    else:
                        raise ValueError(
                            f"{table.name}: 已有同主键记录内容不同；两张表的本次写入全部回滚"
                        )
                insert = (
                    f"INSERT INTO {identifier(table.name)} ({fields}) VALUES ("
                    + ", ".join(["%s"] * len(names))
                    + ")"
                )
                for start in range(0, len(missing), 250):
                    cursor.executemany(insert, missing[start : start + 250])
                cursor.execute(f"SELECT COUNT(*) FROM {identifier(table.name)}")
                report[table.name] = {
                    "inserted": len(missing),
                    "unchanged": unchanged,
                    "total": cursor.fetchone()[0],
                }
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.execute("SELECT RELEASE_LOCK(CONCAT(DATABASE(), '.mock-fixtures'))")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将最终 mock 导入 MySQL 两张来源表，不覆盖已有不同数据"
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-schema", action="store_true", help="仅导出建表 SQL，不连接数据库")
    args = parser.parse_args()
    schema = load_schema()
    if args.write_schema:
        DDL_PATH.parent.mkdir(parents=True, exist_ok=True)
        DDL_PATH.write_text(schema_sql(schema), encoding="utf-8")
        print(f"已写入 {DDL_PATH}")
        return
    errors = validate_directory(args.input_dir)
    if errors:
        parser.exit(1, "mock 校验失败：\n" + "\n".join(errors[:10]) + "\n")
    data = {
        t.source: read_csv(args.input_dir / schema["tables"][t.source]["filename"])[1]
        for t in TABLES
    }
    settings = Settings()
    if not settings.mysql_password or not settings.mysql_password.get_secret_value():
        parser.exit(1, "请设置 BANK_MYSQL_PASSWORD；本地新库可运行 make mysql-up。\n")
    try:
        with pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            database=settings.mysql_database,
            user=settings.mysql_user,
            password=settings.mysql_password.get_secret_value(),
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=5,
            read_timeout=60,
            write_timeout=60,
            init_command="SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION'",
        ) as connection:
            report = load_rows(connection, schema, data)
    except pymysql.MySQLError as exc:
        parser.exit(
            1,
            f"MySQL 导入失败（错误码 {exc.args[0]}），请检查连接、权限和表结构；未提交本次数据。\n",
        )
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    print(
        json.dumps(
            {"database": settings.mysql_database, "tables": report}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
