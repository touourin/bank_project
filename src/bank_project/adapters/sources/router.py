from bank_project.contracts.errors import DependencyUnavailable, IntakeError
from bank_project.contracts.intake import RawInput
from bank_project.contracts.models import ImportRequest
from bank_project.ports import SourceReader


class SourceRouter:
    def __init__(self, inbox: SourceReader, mysql: SourceReader | None = None):
        self.inbox, self.mysql = inbox, mysql

    def read(self, request: ImportRequest) -> RawInput:
        if request.source_uri.startswith("file:"):
            return self.inbox.read(request)
        if request.source_uri.startswith("mysql:"):
            if self.mysql is None:
                raise DependencyUnavailable("MySQL 源读取未启用，请设置 BANK_MYSQL_SOURCE_ENABLED")
            return self.mysql.read(request)
        raise IntakeError("只支持 file:相对路径、mysql:已配置表名，或使用文件上传接口")
