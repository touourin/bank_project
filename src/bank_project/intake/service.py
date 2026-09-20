"""Orchestration joins source adapters to storage, without downstream business rules."""

from contextlib import contextmanager
from hashlib import sha256
from threading import BoundedSemaphore

from bank_project.intake.files import parse_file
from bank_project.intake.models import BatchDetail, IntakeError, Limits
from bank_project.intake.mysql import MysqlConnection, MysqlImport, MysqlSource
from bank_project.intake.store import BatchStore


class IntakeService:
    def __init__(
        self,
        store: BatchStore,
        limits: Limits,
        mysql: MysqlSource,
        configured_mysql: MysqlConnection | None = None,
    ):
        self.store = store
        self.limits = limits
        self.mysql = mysql
        self._slots = BoundedSemaphore(2)
        self._configured_mysql = configured_mysql

    def connection(self, supplied: MysqlConnection | None) -> MysqlConnection:
        connection = supplied or self._configured_mysql
        if connection is None:
            raise IntakeError(
                "项目未配置 MySQL 密码，请完善 .env 中的 BANK_MYSQL_* 配置，或填写自定义连接"
            )
        return connection

    @contextmanager
    def _admit(self):
        if not self._slots.acquire(blocking=False):
            raise IntakeError("已有两个接入任务在运行，请稍后重试", status=503)
        try:
            yield
        finally:
            self._slots.release()

    def upload(self, content: bytes, filename: str, **options) -> BatchDetail:
        with self._admit():
            parsed = parse_file(content, filename, self.limits, **options)
            return self.store.save(
                parsed,
                name=filename,
                source_kind="file",
                source=filename,
                sha256=sha256(content).hexdigest(),
            )

    def import_mysql(self, request: MysqlImport) -> BatchDetail:
        with self._admit():
            params = self.connection(request.connection)
            parsed = self.mysql.read(params, request.tables)
            return self.store.save(
                parsed,
                name=params.database,
                source_kind="mysql",
                source=f"{params.host}:{params.port}/{params.database}",
            )
