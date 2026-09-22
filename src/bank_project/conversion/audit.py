"""Capture a review independently of each source's storage representation."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel


class ReviewStamp(BaseModel):
    # Optional for records written before review IDs and actors were recorded.
    id: str | None = None
    created_at: str
    reviewer: str = ""


def review_record(*, before, after, reviewer, **details):
    """Snapshot both sides so later edits cannot rewrite the audit evidence."""
    return {
        **details,
        "id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "reviewer": reviewer.strip(),
        "before": deepcopy(before),
        "after": deepcopy(after),
    }
