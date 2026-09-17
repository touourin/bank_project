from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.contracts.models import ExtractionBatch, SemanticReport


class SemanticService:
    """晏子怡对接本体负责人：语义挂载与校验，待实现。"""

    def validate(self, batch: ExtractionBatch) -> SemanticReport:
        raise FeatureNotImplemented("语义挂载与校验尚未实现")
