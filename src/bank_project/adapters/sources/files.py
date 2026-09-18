import os
import stat
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

from bank_project.contracts.errors import IntakeError, LimitExceeded, NotFound
from bank_project.contracts.intake import RawInput
from bank_project.contracts.models import ImportRequest


class InboxReader:
    def __init__(self, root: Path, max_bytes: int):
        self.root = root.resolve()
        self.max_bytes = max_bytes

    def read(self, request: ImportRequest) -> RawInput:
        relative = request.source_uri.removeprefix("file:")
        path = PurePosixPath(relative)
        if (
            not request.source_uri.startswith("file:")
            or not relative
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in relative
            or "\x00" in relative
            or not path.name
        ):
            raise IntakeError("source_uri 必须为 file:收件目录内的相对路径；禁止绝对路径和越界访问")
        try:
            # openat + O_NOFOLLOW on every component avoids symlink and path-swap escapes.
            with ExitStack() as stack:
                parent = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stack.callback(os.close, parent)
                for part in path.parts[:-1]:
                    parent = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                    )
                    stack.callback(os.close, parent)
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise IntakeError("数据源必须是普通文件")
                    if info.st_size > self.max_bytes:
                        raise LimitExceeded("原始文件大小超限")
                    content = handle.read(self.max_bytes + 1)
        except FileNotFoundError as exc:
            raise NotFound("收件目录或源文件不存在") from exc
        except OSError as exc:
            raise IntakeError("源文件无法安全读取；请检查权限及符号链接") from exc
        if len(content) > self.max_bytes:
            raise LimitExceeded("原始文件大小超限")
        return RawInput(filename=path.name, content=content)
