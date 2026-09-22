"""Normalize the public evaluation snapshot into the project's catalog contract."""

import re

from bank_project.alignment.catalog import Catalog
from bank_project.alignment.models import AlignmentError

from .client import MAX_RESPONSE_BYTES
from .serialization import canonical_json


def encode_catalog(document, revision):
    content = (canonical_json(document) + "\n").encode()
    if len(content) > MAX_RESPONSE_BYTES:
        raise AlignmentError("本体与维度内容合计超过 30 MiB 上限", 503)
    return Catalog(content, revision)


def remote_catalog(export, ready):
    revision, ontology_id = ready["dataset_revision"], ready["ontology_id"]
    if export["dataset_revision"] != revision or export["ontology_id"] != ontology_id:
        raise AlignmentError("远端图结构与就绪本体版本不一致", 409)
    nodes = [{"label": "OntologyDataset", "properties": {"revision": revision, "status": "ready"}}]
    for node in export["nodes"]:
        dimensions, hashes = node["nonempty_dimensions"], node["dimension_hashes"]
        if (
            not isinstance(dimensions, list)
            or not all(isinstance(d, str) for d in dimensions)
            or not isinstance(hashes, dict)
            or any(
                not isinstance(hashes.get(d), str) or not re.fullmatch(r"[0-9a-f]{64}", hashes[d])
                for d in dimensions
            )
            or ("why" in dimensions) != bool(hashes.get("why"))
        ):
            raise ValueError("inconsistent dimension metadata")
        nodes.append(
            {
                "label": "Concept",
                "properties": {
                    "node_id": node["node_id"],
                    "node_name": node["node_name"],
                    "dataset_revision": revision,
                    "node_semantic_type": node["node_semantic_type"],
                    "nonempty_dimensions": dimensions,
                    "dimension_hashes": hashes,
                },
            }
        )
    return encode_catalog(
        {
            "ontology_source": {
                "kind": "remote",
                "ontology_id": ontology_id,
                "dataset_revision": revision,
            },
            "graph": {
                "nodes": nodes,
                "relationships": [
                    {
                        "start": e["source_node_id"],
                        "end": e["target_node_id"],
                        "type": e["relation_type"].upper(),
                        "properties": {"dataset_revision": revision},
                    }
                    for e in export["relations"]
                ],
            },
        },
        revision,
    )
