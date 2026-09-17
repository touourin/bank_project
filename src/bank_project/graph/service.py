from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.contracts.models import ExtractionBatch, GraphPatch, ResolutionResult


class GraphService:
    """晏子怡：图谱构建，待实现。"""

    def build(self, batch: ExtractionBatch, resolution: ResolutionResult) -> GraphPatch:
        raise FeatureNotImplemented("图谱构建尚未实现")
