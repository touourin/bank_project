# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Bounded exact vector retrieval for pilot experiments; no full NxN allocation."""

from bank_project.resolution.engine.contracts import Corpus, ResolverConfig, require


def vector_neighbors(corpus: Corpus, data: dict, config: ResolverConfig) -> dict[str, list[str]]:
    """Validate a versioned embedding sidecar and retrieve deterministic neighbors."""
    import numpy as np

    require(
        isinstance(data, dict) and data.get("corpus_sha256") == corpus.sha256,
        "Embedding sidecar belongs to a different corpus",
    )
    require(
        isinstance(data.get("model_version"), str) and bool(data["model_version"]),
        "Embedding sidecar needs model_version",
    )
    vectors = data.get("vectors")
    mids = sorted(mention.mention_id for mention in corpus.mentions)
    if not isinstance(vectors, dict):
        message = "Embeddings must be an object covering all records exactly"
        raise TypeError(message)
    require(set(vectors) == set(mids), "Embeddings must cover all records exactly")
    require(
        len(mids) <= config.max_vector_records,
        "Pilot exact-vector retrieval limit exceeded; use a smaller corpus or implement ANN",
    )
    for vector in vectors.values():
        require(isinstance(vector, list) and bool(vector), "Vectors must be nonempty arrays")
        require(
            all(
                isinstance(value, (int, float)) and not isinstance(value, bool) for value in vector
            ),
            "Vector coordinates must be numeric",
        )
    require(
        len({len(vector) for vector in vectors.values()}) == 1,
        "Inconsistent vector dimensions",
    )
    matrix = np.asarray([vectors[mid] for mid in mids], dtype=np.float64)
    require(bool(np.isfinite(matrix).all()), "Non-finite vector coordinate")
    norms = np.linalg.norm(matrix, axis=1)
    require(
        bool(np.isfinite(norms).all()) and bool((norms > 0).all()),
        "Zero or non-finite vector norm",
    )
    matrix /= norms[:, None]
    neighbors = {}
    for start in range(0, len(mids), 128):
        scores = matrix[start : start + 128] @ matrix.T
        for offset, row in enumerate(scores):
            index = start + offset
            row[index] = -np.inf
            order = np.argsort(-row, kind="stable")[
                : min(config.embedding_neighbors, len(mids) - 1)
            ]
            neighbors[mids[index]] = [mids[int(other)] for other in order]
    return neighbors
