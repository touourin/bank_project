import json
import re

import pymysql

from bank_project.adapters.values import json_scalar
from bank_project.contracts.errors import DependencyUnavailable, IntakeError, LimitExceeded
from bank_project.contracts.intake import RawInput
from bank_project.contracts.models import ImportRequest
from bank_project.contracts.schema import IntakeLimits


class MySQLReader:
    def __init__(self, tables: list[str], limits: IntakeLimits, connection: dict):
        self.tables, self.limits, self.connection = frozenset(tables), limits, connection

    def read(self, request: ImportRequest) -> RawInput:
        table = request.source_uri.removeprefix("mysql:")
        if not request.source_uri.startswith("mysql:") or table not in self.tables:
            raise IntakeError("MySQL 数据源只允许 BANK_MYSQL_SOURCE_TABLES 中的表名")
        if request.table is not None and request.table != table:
            raise IntakeError("table 与 MySQL 数据源不一致")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", table):
            raise IntakeError("数据库表配置不是合法标识符")
        query = f"SELECT * FROM `{table}` LIMIT %s"
        pieces = []
        size = 2
        try:
            with pymysql.connect(
                **self.connection,
                charset="utf8mb4",
                autocommit=False,
                cursorclass=pymysql.cursors.SSDictCursor,
                connect_timeout=5,
                read_timeout=30,
                write_timeout=10,
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                    cursor.execute(query, (self.limits.rows + 1,))
                    for row in cursor:
                        if len(pieces) >= self.limits.rows:
                            raise LimitExceeded("MySQL 结果行数超限，请按分区准备更小的数据源")
                        piece = json.dumps(
                            row, ensure_ascii=False, default=json_scalar, allow_nan=False
                        ).encode()
                        size += len(piece) + 1
                        if size > self.limits.file_bytes:
                            raise LimitExceeded("MySQL 快照大小超限")
                        pieces.append(piece)
                conn.rollback()
        except IntakeError:
            raise
        except (pymysql.MySQLError, OSError, ValueError) as exc:
            raise DependencyUnavailable("MySQL 读取失败，请检查连接、只读权限和表结构") from exc
        return RawInput(filename=f"{table}.json", content=b"[" + b",".join(pieces) + b"]")
