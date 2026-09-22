"""Content-addressed evidence survives remote upgrades and application restarts."""

import json
import re
from pathlib import Path
from uuid import uuid4

from bank_project.alignment.catalog import Catalog
from bank_project.alignment.models import AlignmentError


class SnapshotStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AlignmentError("本体依据的哈希格式无效", 409)
        return self.directory / f"{digest}.json"

    def save(self, catalog: Catalog) -> None:
        target = self.path(catalog.sha256)
        if target.exists():
            self.load(catalog.revision, catalog.sha256)
            return
        temporary = self.directory / f".{uuid4()}.tmp"
        try:
            temporary.write_bytes(catalog.content)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def import_legacy(self, path: Path) -> None:
        """Keep original bytes even when the selected remote revision has changed."""
        try:
            if path.stat().st_size > 30 * 1024 * 1024:
                return
            content = path.read_bytes()
            revisions = {
                node["properties"]["revision"]
                for node in json.loads(content)["graph"]["nodes"]
                if node["label"] == "OntologyDataset"
                and node["properties"].get("status") == "ready"
            }
            catalogs = [Catalog(content, revision) for revision in sorted(revisions)]
        except (OSError, ValueError, KeyError, TypeError, AttributeError, AlignmentError):
            return
        for catalog in catalogs:
            self.save(catalog)

    def load(self, revision: str, digest: str) -> Catalog:
        catalog = Catalog.load(self.path(digest), revision)
        if catalog.sha256 != digest:
            raise AlignmentError("历史本体依据校验失败，请恢复对应备份", 409)
        return catalog
