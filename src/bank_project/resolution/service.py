from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.contracts.models import ExtractionBatch, ResolutionResult


class ResolutionService:
    """晏子怡：实体消歧，待实现。"""

    def resolve(self, batch: ExtractionBatch) -> ResolutionResult:
        raise FeatureNotImplemented("实体消歧尚未实现")
