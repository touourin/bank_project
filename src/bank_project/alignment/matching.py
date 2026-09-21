"""Turn retrieval evidence into conservative mapping decisions, independently of transport."""

from .catalog import Catalog
from .models import RetrievalTrace, ScoredConcept
from .retrieval import RetrievalResult


def reviewable_candidate(trace: RetrievalTrace, catalog: Catalog) -> ScoredConcept | None:
    """Share the same reviewable suggestion rule across table and graph matching."""
    if (
        trace.status in {"matched", "review"}
        and trace.selected is not None
        and trace.selected.id in catalog.names
    ):
        return trace.selected
    return None


def decide(result: RetrievalResult, target: str, name: str, catalog: Catalog, threshold: float):
    trace = RetrievalTrace(target=target, name=name, query=result.query, status="unmatched")
    if result.status != "ok":
        trace.status, trace.detail = result.status, result.detail
        return trace
    if result.response is None:
        trace.status, trace.detail = "unavailable", "retrieve 未返回有效响应"
        return trace
    response = result.response
    if response.dataset_revision != catalog.revision or any(
        c.node_id not in catalog.names for c in response.candidates
    ):
        trace.status, trace.detail = "mismatch", "返回的节点不属于本次本体版本，未采用结果"
        return trace
    trace.confident, trace.match_method = response.confident, response.match_method
    trace.candidates = [
        ScoredConcept(**catalog.describe(c.node_id).model_dump(), score=c.score)
        for c in response.candidates
    ]
    trace.selected = next((c for c in trace.candidates if c.id == response.node_id), None)
    # A candidate is visible for review even if the server declines to select a node.
    if trace.selected is None and trace.candidates:
        trace.selected = trace.candidates[0]
    if trace.selected is None:
        trace.detail = "接口没有返回候选节点，请人工选择或补充字段说明"
    elif (
        response.node_id == trace.selected.id
        and trace.confident
        and trace.selected.score is not None
        and trace.selected.score >= threshold
    ):
        trace.status = "matched"
        trace.detail = f"retrieve 命中 {trace.selected.name}，得分 {trace.selected.score:.3f}"
    else:
        trace.status = "review"
        score = f"{trace.selected.score:.3f}" if trace.selected.score is not None else "缺失"
        trace.detail = f"候选为 {trace.selected.name}，得分 {score}；未达到自动采用条件，请人工核对"
    return trace
