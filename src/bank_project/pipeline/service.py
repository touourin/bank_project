from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.contracts.models import ExtractionBatch, ImportRequest, RunResult


class Pipeline:
    """Reserved orchestration entry point; no steps execute in the framework."""

    def import_data(self, request: ImportRequest) -> RunResult:
        raise FeatureNotImplemented("数据导入与流水线尚未实现")

    def run(self, batch: ExtractionBatch) -> RunResult:
        raise FeatureNotImplemented("批次处理尚未实现")

    def get_run(self, dataset_id: str, run_id: str) -> RunResult:
        raise FeatureNotImplemented("运行记录查询尚未实现")
