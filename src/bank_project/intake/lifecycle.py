"""Coordinate batch removal with durable analysis references, never with source databases."""

from .models import BatchReferences, IntakeError, PurgeResult


class BatchLifecycle:
    def __init__(self, batches, runs):
        self.batches, self.runs = batches, runs

    def references(self, batch_id):
        with self.runs.source_guard() as db:
            self.batches.get(batch_id, include_deleted=True)
            return BatchReferences(**self.runs.batch_references(db, batch_id))

    def remove(self, batch_id):
        with self.runs.source_guard():
            self.batches.delete(batch_id)

    def restore(self, batch_id):
        with self.runs.source_guard():
            self.batches.restore(batch_id)
        return self.batches.get(batch_id)

    def purge(self, batch_id):
        with self.runs.source_guard() as db:
            self.batches.get(batch_id, include_deleted=True)
            references = self.runs.batch_references(db, batch_id)
            if references["count"]:
                examples = "、".join(key[:8] for key in references["run_ids"])
                raise IntakeError(
                    f"该批次被 {references['count']} 个历史分析版本引用（{examples}），"
                    "不能彻底删除；可保留在已移除列表中",
                    status=409,
                )
            return PurgeResult(status=self.batches.purge(batch_id))
