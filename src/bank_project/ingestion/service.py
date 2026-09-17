from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.contracts.models import ImportRequest, SourceArtifact


class IngestionService:
    """邓王璘：数据接入，待实现。"""

    def ingest(self, request: ImportRequest) -> SourceArtifact:
        raise FeatureNotImplemented("数据接入尚未实现")
