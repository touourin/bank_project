from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.contracts.models import EvidenceResult, GraphQuery, QueryResult


class QueryService:
    """Reserved query entry point; no database reads or graph traversal."""

    def query(self, request: GraphQuery) -> QueryResult:
        raise FeatureNotImplemented("图谱查询尚未实现")

    def evidence(self, dataset_id: str, evidence_id: str) -> EvidenceResult:
        raise FeatureNotImplemented("证据查询尚未实现")
