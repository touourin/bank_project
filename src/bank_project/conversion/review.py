"""Common readiness and ontology guards, independent of review persistence."""

from bank_project.alignment.models import AlignmentError


def require_reviewable(status: str, *, blocked: bool = False):
    if status != "ready" or blocked:
        raise AlignmentError("请等待当前任务完成后再修改匹配", 409)


def review_catalog(ontology, revision: str, sha256: str, ontology_id=None):
    return ontology.pinned(revision, sha256, ontology_id)
