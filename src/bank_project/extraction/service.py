from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.contracts.models import ExtractionBatch, ImportRequest, SourceArtifact


class ExtractionService:
    """邓王璘：数据抽取，待实现。"""

    def extract(self, request: ImportRequest, artifact: SourceArtifact) -> ExtractionBatch:
        raise FeatureNotImplemented("数据抽取尚未实现")
