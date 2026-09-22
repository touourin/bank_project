"""Expose complete ontology topology independently from business instance graphs."""


def topology(catalog):
    return {
        "id": f"ontology:{catalog.ontology_id or 'local'}:{catalog.revision}",
        "name": "BFO 本体",
        "nodes": [
            {
                "id": key,
                "name": detail.name,
                "type": detail.semantic_type or "本体概念",
                "boid": key,
                "properties": {
                    "has_why": detail.has_why,
                    "parents": [p.model_dump() for p in detail.parents],
                },
            }
            for key in catalog.names
            for detail in [catalog.describe(key)]
        ],
        "edges": [
            {
                "id": f"ontology-edge:{i}",
                "source": e["source"],
                "target": e["target"],
                "edge_type": e["type"],
                "properties": {"relation_type": e["type"]},
            }
            for i, e in enumerate(catalog.relations)
        ],
    }
