"""One reference format and conflict guard for derived graph versions."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from bank_project.alignment.models import AlignmentError


def require_revision(actual: int, expected: int | None, message: str):
    if expected is not None and actual != expected:
        raise AlignmentError(message, 409)


@dataclass(frozen=True)
class GraphVersion:
    stage: Literal["match", "resolution"]
    run_id: str
    revision: int

    @property
    def reference(self):
        return f"{self.stage}:{self.run_id}:{self.revision}"

    @classmethod
    def parse(cls, reference: str):
        try:
            stage, identifier, revision = reference.split(":")
            if (
                stage not in {"match", "resolution"}
                or not revision.isascii()
                or not revision.isdigit()
            ):
                raise ValueError("invalid graph version")
            return cls(stage, str(UUID(identifier)), int(revision))
        except ValueError as exc:
            raise AlignmentError("派生图版本标识无效") from exc
