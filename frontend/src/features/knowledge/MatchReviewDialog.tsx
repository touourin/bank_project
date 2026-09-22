import { useCallback, useState } from "react";
import { Select } from "antd";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { ErrorNotice } from "../../ui/Feedback";
import { ReviewNoteFields } from "../conversion/ReviewNoteFields";
import { useConceptSearch } from "../conversion/useConceptSearch";
import { knowledgeApi } from "./api";
import type { MatchNode, MatchEdge } from "./types";

export type MatchReviewTarget =
  { target: "node"; value: MatchNode } | { target: "edge"; value: MatchEdge };
export type MatchReviewValues = {
  selection?: string;
  reviewer: string;
  note: string;
};

export function MatchReviewDialog({
  token,
  runId,
  editing,
  onSave,
  onClose,
}: {
  token: string;
  runId: string;
  editing: MatchReviewTarget;
  onSave: (values: MatchReviewValues) => Promise<void>;
  onClose: () => void;
}) {
  const [selection, setSelection] = useState<string | undefined>(
    (editing.target === "node"
      ? editing.value.boid
      : editing.value.edge_type) ?? undefined,
  );
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const concepts = useConceptSearch(
    useCallback(
      (query: string, signal: AbortSignal) =>
        editing.target === "node"
          ? knowledgeApi.concepts(token, runId, query, signal)
          : Promise.resolve([]),
      [token, runId, editing.target],
    ),
    editing.target === "node" ? editing.value.trace.candidates : undefined,
  );
  const options =
    editing.target === "edge"
      ? editing.value.candidates.map((value) => ({ value, label: value }))
      : (concepts.data ?? []).map((concept) => ({
          value: concept.id,
          label: `${concept.name} · ${concept.id}${concept.score != null ? ` · ${concept.score.toFixed(3)}` : ""}`,
        }));
  if (
    editing.target === "node" &&
    selection &&
    !options.some((item) => item.value === selection)
  )
    options.unshift({ value: selection, label: selection });
  return (
    <ConfirmDialog
      title={editing.target === "node" ? "修改节点 boid" : "修改边类型"}
      confirmLabel="保存修改"
      onClose={onClose}
      onConfirm={() => onSave({ selection, reviewer, note })}
    >
      <p className="hint">原始元素 ID：{editing.value.id}</p>
      <label className="knowledge-field">
        {editing.target === "node" ? "本体概念（可搜索）" : "关系类型"}
        <Select
          aria-label={editing.target === "node" ? "选择本体概念" : "选择边类型"}
          allowClear
          showSearch
          filterOption={editing.target !== "node"}
          loading={concepts.loading}
          onSearch={editing.target === "node" ? concepts.search : undefined}
          value={selection}
          onChange={setSelection}
          placeholder="清除选择可移除挂载"
          options={options}
        />
      </label>
      {concepts.error && (
        <ErrorNotice message={concepts.error} onRetry={concepts.refresh} />
      )}
      <ReviewNoteFields
        reviewer={reviewer}
        note={note}
        onReviewer={setReviewer}
        onNote={setNote}
      />
    </ConfirmDialog>
  );
}
