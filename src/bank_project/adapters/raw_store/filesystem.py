"""Atomic local persistence. One batch lock covers each complete stage.

The service owns this directory; callers can never choose filesystem paths.
Deploy one writer pod with a persistent volume, or replace this port with a DB.
"""

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from bank_project.contracts.document import DocumentExtraction
from bank_project.contracts.errors import Conflict, DependencyUnavailable, IntakeError
from bank_project.contracts.intake import ExtractionState, StoredExtraction, StoredImport
from bank_project.contracts.models import SourceArtifact

T = TypeVar("T", bound=BaseModel)


def _hash(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class FileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, category: str, key: str, suffix: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise IntakeError("存储标识无效")
        folder = self.root / category
        if folder.is_symlink():
            raise DependencyUnavailable("存储目录不能是符号链接")
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / f"{key}.{suffix}"
        if path.is_symlink():
            raise DependencyUnavailable("存储文件不能是符号链接")
        return path

    def _write(self, path: Path, content: bytes) -> None:
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def _read_model(self, path: Path, model: type[T]) -> T | None:
        try:
            return model.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            return None
        except (ValueError, ValidationError) as exc:
            raise DependencyUnavailable("持久化记录损坏，请检查存储，不会自动覆盖") from exc

    @contextmanager
    def lock(self, dataset_id: str, batch_id: str) -> Iterator[None]:
        path = self._path("locks", _hash(dataset_id, batch_id), "lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Conflict("该批次正在处理，请稍后重试") from exc
            yield
        finally:
            os.close(fd)

    def save(self, content: bytes) -> SourceArtifact:
        digest = hashlib.sha256(content).hexdigest()
        path = self._path("raw", digest, "bin")
        self._write(path, content)
        return SourceArtifact(uri=f"sha256:{digest}", digest=digest)

    def read(self, artifact: SourceArtifact) -> bytes:
        if artifact.uri != f"sha256:{artifact.digest}":
            raise IntakeError("原始文件标识与摘要不一致")
        content = self._path("raw", artifact.digest, "bin").read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.digest:
            raise DependencyUnavailable("原始文件摘要校验失败")
        return content

    def load_import(self, dataset_id: str, batch_id: str) -> StoredImport | None:
        return self._read_model(
            self._path("imports", _hash(dataset_id, batch_id), "json"), StoredImport
        )

    def save_import(self, value: StoredImport) -> None:
        self._write(
            self._path("imports", _hash(value.request.dataset_id, value.request.batch_id), "json"),
            value.model_dump_json().encode(),
        )

    def load_extraction(self, dataset_id: str, batch_id: str) -> StoredExtraction | None:
        return self._read_model(
            self._path("extractions", _hash(dataset_id, batch_id), "json"), StoredExtraction
        )

    def save_extraction(self, value: StoredExtraction) -> None:
        self._write(
            self._path("extractions", _hash(value.batch.dataset_id, value.batch.batch_id), "json"),
            value.model_dump_json().encode(),
        )

    def load_state(self, dataset_id: str, batch_id: str) -> ExtractionState | None:
        return self._read_model(
            self._path("states", _hash(dataset_id, batch_id), "json"), ExtractionState
        )

    def save_state(self, value: ExtractionState) -> None:
        self._write(
            self._path("states", _hash(value.dataset_id, value.batch_id), "json"),
            value.model_dump_json().encode(),
        )

    def get(self, key: str) -> DocumentExtraction | None:
        return self._read_model(self._path("model_cache", key, "json"), DocumentExtraction)

    def put(self, key: str, value: DocumentExtraction) -> None:
        self._write(self._path("model_cache", key, "json"), value.model_dump_json().encode())
