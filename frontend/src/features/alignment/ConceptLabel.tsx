import type { ConceptRef, ConceptDetail } from "./types";

export function ConceptLabel({
  concept,
  parents = false,
}: {
  concept: ConceptRef | ConceptDetail;
  parents?: boolean;
}) {
  return (
    <span className="concept-label">
      <strong>{concept.name}</strong>
      <code>{concept.id}</code>
      {"semantic_type" in concept && concept.semantic_type && (
        <small>{concept.semantic_type}</small>
      )}
      {parents && "parents" in concept && (
        <small>
          直属上级：
          {Array.isArray(concept.parents) && concept.parents.length
            ? concept.parents.map((p) => `${p.name}（${p.id}）`).join("、")
            : "该版本未记录上级"}
        </small>
      )}
    </span>
  );
}
