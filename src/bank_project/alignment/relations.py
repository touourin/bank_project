"""Exact composite-key joins; ambiguous targets are never resolved by taking the first row."""

from collections import Counter
from hashlib import sha256

from .models import RelationMapping, RelationProposal, SourceTable, TableMapping


def join_keys(source: SourceTable, columns: list[str]):
    positions = [
        next(i for i, c in enumerate(source.table.columns) if c.name == name) for name in columns
    ]
    for row in source.rows:
        values = tuple(row.values[i] for i in positions)
        yield None if any(v is None or v == "" for v in values) else values


def map_relations(
    sources: list[SourceTable],
    mappings: list[TableMapping],
    proposals: dict[str, list[RelationProposal]],
    index=None,
) -> list[RelationMapping]:
    by_id = {s.table.id: s for s in sources}
    mapped = {m.table_id for m in mappings if m.status == "mapped"}
    candidates = []
    for source in sources:
        for fk in source.table.foreign_keys:
            targets = [
                s
                for s in sources
                if s.batch.id == source.batch.id
                and s.table.name == fk.target_table
                and s.batch.source == fk.target_schema
            ]
            # Some MySQL sources include a host-qualified source; only the batch's declared DB is eligible.
            if not targets:
                targets = [
                    s
                    for s in sources
                    if s.batch.id == source.batch.id
                    and s.table.name == fk.target_table
                    and s.batch.source.endswith("/" + fk.target_schema)
                ]
            if len(targets) == 1:
                candidates.append(
                    (
                        source,
                        targets[0].table.id,
                        fk.columns,
                        fk.target_columns,
                        "declared",
                        f"来源声明外键：{fk.name}",
                    )
                )
            else:
                candidates.append(
                    (
                        source,
                        "",
                        fk.columns,
                        fk.target_columns,
                        "declared",
                        f"外键 {fk.name} 的目标表未选择或不明确：{fk.target_schema}.{fk.target_table}",
                    )
                )
        for p in proposals.get(source.table.id, []):
            candidates.append(
                (
                    source,
                    p.target_table_id,
                    p.source_columns,
                    p.target_columns,
                    "candidate",
                    p.reason,
                )
            )
    results, seen = [], set()
    for source, target_id, left, right, origin, reason in candidates:
        key = (source.table.id, target_id, tuple(left), tuple(right))
        if key in seen:
            continue
        seen.add(key)
        relation = RelationMapping(
            id=sha256(repr(key).encode()).hexdigest()[:24],
            source_table_id=source.table.id,
            target_table_id=target_id,
            source_columns=left,
            target_columns=right,
            origin=origin,
            reason=reason,
            status="skipped",
        )
        target = by_id.get(target_id)
        if not target:
            relation.warnings.append("目标表不在本次所选范围内")
        elif source.table.id not in mapped or target_id not in mapped:
            relation.warnings.append("关联两端的表尚未通过本次匹配")
        elif (
            not left
            or len(left) != len(right)
            or len(left) != len(set(left))
            or len(right) != len(set(right))
            or not set(left) <= {c.name for c in source.table.columns}
            or not set(right) <= {c.name for c in target.table.columns}
        ):
            relation.warnings.append("连接字段缺失、重复或不对应")
        else:
            if source.staged or target.staged:
                if index is None or not (source.staged and target.staged):
                    relation.warnings.append("暂存索引不可用，请重新分析")
                    results.append(relation)
                    continue
                duplicate, matched, missing = index.stats(source, target, left, right)
            else:
                target_counts = Counter(k for k in join_keys(target, right) if k is not None)
                duplicate = any(count > 1 for count in target_counts.values())
                matched = missing = 0
                for key_value in join_keys(source, left):
                    if key_value is not None:
                        matched += key_value in target_counts
                        missing += key_value not in target_counts
            if duplicate:
                relation.warnings.append("目标连接字段不唯一，跳过整条关联，避免错误匹配")
            else:
                relation.matched_rows = matched
                if missing:
                    relation.warnings.append(f"{missing} 行找不到目标；保留源实例但不创建对应边")
                relation.status = "ready" if relation.matched_rows else "skipped"
                if not relation.matched_rows:
                    relation.warnings.append("没有精确相等的非空连接值")
        results.append(relation)
    return results
