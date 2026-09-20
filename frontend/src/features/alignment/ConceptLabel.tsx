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
      {parents && "parents" in concept && (
        <small>
          直属上级：
          {concept.parents.length
            ? concept.parents.map((p) => `${p.name}（${p.id}）`).join("、")
            : "该版本未记录上级"}
        </small>
      )}
    </span>
  );
}
