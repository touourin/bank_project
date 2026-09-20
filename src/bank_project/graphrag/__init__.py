"""TXT ingestion, native GraphRAG indexing and graph-backed question answering."""

from .runtime import GraphRagError
from .service import GraphRagService

__all__ = ["GraphRagError", "GraphRagService"]
