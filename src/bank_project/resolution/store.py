"""Persistent immutable input and transactional, revision-checked human decisions."""

import json
import sqlite3
import time
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from bank_project.alignment.models import AlignmentError

from .adapter import brief, conflicts
from .engine.contracts import digest
from .models import Audit, Candidate, ResolutionRun
from .projection import memberships, project, update_run

LEASE_SECONDS = 120


def now():
    return datetime.now(UTC).isoformat()


class ResolutionStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS resolution_runs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL, original BLOB,
                    owner TEXT NOT NULL, expires REAL NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _expire(self, db):
        for run_id, value in db.execute(
            "SELECT id,metadata FROM resolution_runs WHERE expires>0 AND expires<?", (time.time(),)
        ).fetchall():
            run = ResolutionRun.model_validate_json(value)
            if run.status == "analyzing":
                run.status, run.error, run.progress = (
                    "failed",
                    "任务执行租约失效，请重新创建消歧任务；原图未修改",
                    "分析中断",
                )
                self._write(db, run)
            db.execute("UPDATE resolution_runs SET expires=0 WHERE id=?", (run_id,))

    @staticmethod
    def _read(db, run_id):
        row = db.execute(
            "SELECT metadata,original FROM resolution_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            raise AlignmentError("消歧记录不存在", 404)
        return ResolutionRun.model_validate_json(row[0]), row[1]

    @staticmethod
    def _write(db, run):
        db.execute(
            "UPDATE resolution_runs SET metadata=? WHERE id=?", (run.model_dump_json(), run.id)
        )

    def create(self, kind, source_id):
        run = ResolutionRun(
            id=str(uuid4()),
            name="实体消歧",
            source_kind=kind,
            source_id=source_id,
            created_at=now(),
        )
        owner = str(uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            if db.execute("SELECT 1 FROM resolution_runs WHERE expires>0").fetchone():
                raise AlignmentError("已有实体消歧分析在执行，请等待完成", 409)
            db.execute(
                "INSERT INTO resolution_runs VALUES(?,?,?,NULL,?,?)",
                (run.id, run.created_at, run.model_dump_json(), owner, time.time() + LEASE_SECONDS),
            )
        return run, owner

    def heartbeat(self, run_id, owner):
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE resolution_runs SET expires=? WHERE id=? AND owner=? AND expires>=?",
                (time.time() + LEASE_SECONDS, run_id, owner, time.time()),
            )
            if cursor.rowcount != 1:
                raise AlignmentError("消歧任务已失去执行租约", 409)

    def finish(self, run, owner, graph=None):
        packed = (
            zlib.compress(json.dumps(graph, ensure_ascii=False, allow_nan=False).encode())
            if graph is not None
            else None
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM resolution_runs WHERE id=? AND owner=? AND expires>=?",
                (run.id, owner, time.time()),
            ).fetchone():
                raise AlignmentError("消歧任务已失去执行租约", 409)
            db.execute(
                "UPDATE resolution_runs SET metadata=?,original=?,expires=0 WHERE id=?",
                (run.model_dump_json(), packed, run.id),
            )
        return run

    def progress(self, run_id, owner, message):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM resolution_runs WHERE id=? AND owner=? AND expires>=?",
                (run_id, owner, time.time()),
            ).fetchone():
                raise AlignmentError("消歧任务已失去执行租约", 409)
            run, _ = self._read(db, run_id)
            run.progress = message
            self._write(db, run)

    def get(self, run_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            return self._read(db, run_id)[0]

    def list(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            runs = [
                ResolutionRun.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT metadata FROM resolution_runs ORDER BY created_at DESC"
                )
            ]
        return [
            run.model_copy(update={"candidates": [], "audits": [], "merges": []}) for run in runs
        ]

    def graph(self, run_id):
        with self.connect() as db:
            run, packed = self._read(db, run_id)
        if run.status != "ready" or packed is None:
            raise AlignmentError("消歧输入尚未准备完成", 409)
        return project(run, json.loads(zlib.decompress(packed)))

    def original(self, run_id):
        with self.connect() as db:
            run, packed = self._read(db, run_id)
        if run.status != "ready" or packed is None:
            raise AlignmentError("消歧输入尚未准备完成", 409)
        return json.loads(zlib.decompress(packed))

    def decide(self, run_id, request, *, manual=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run, packed = self._read(db, run_id)
            if run.status != "ready" or packed is None:
                raise AlignmentError("请等待候选分析完成后再进行人工校验", 409)
            if run.revision != request.expected_revision:
                raise AlignmentError("审核版本已变化，请刷新后重新确认，避免覆盖他人决定", 409)
            graph = json.loads(zlib.decompress(packed))
            by_id = {node["id"]: node for node in graph["nodes"]}
            if manual:
                ids = list(dict.fromkeys(request.node_ids))
                if (
                    len(ids) < 2
                    or len(ids) != len(request.node_ids)
                    or any(mid not in by_id for mid in ids)
                ):
                    raise AlignmentError("人工合并需要至少两个互不重复且存在的原始节点")
                candidate_id = digest(sorted(ids))[:32]
                candidate = next((c for c in run.candidates if c.id == candidate_id), None)
                if candidate is None:
                    candidate = Candidate(
                        id=candidate_id,
                        node_ids=ids,
                        nodes=[brief(by_id[mid]) for mid in ids],
                        score=0,
                        reasons=["人工指定候选；未经模型判断"],
                        evidence={"origin": "manual"},
                        conflicts=conflicts([by_id[mid] for mid in ids]),
                    )
                    run.candidates.append(candidate)
                action = "merge"
            else:
                candidate = next((c for c in run.candidates if c.id == request.candidate_id), None)
                if candidate is None:
                    raise AlignmentError("消歧候选不存在", 404)
                action = request.action
            if request.canonical_id is not None and request.canonical_id not in candidate.node_ids:
                raise AlignmentError("保留节点必须来自当前候选的原始节点")
            previous = candidate.status
            owner, groups = memberships(graph, run.candidates)
            affected = set().union(*(groups[owner[mid]] for mid in candidate.node_ids))
            candidate.status = {"merge": "merged", "reject": "rejected", "reset": "pending"}[action]
            candidate.canonical_id = (
                (request.canonical_id or candidate.node_ids[0]) if action == "merge" else None
            )
            run.revision += 1
            candidate.decision_order = run.revision
            update_run(run, graph)  # Validates whole clusters and cannot-link before commit.
            run.audits.append(
                Audit(
                    id=str(uuid4()),
                    action="manual" if manual else action,
                    candidate_id=candidate.id,
                    canonical_id=candidate.canonical_id,
                    reviewer=request.reviewer,
                    note=request.note,
                    created_at=now(),
                    revision=run.revision,
                    previous_status=previous,
                    source_nodes=[brief(by_id[mid]) for mid in sorted(affected)],
                )
            )
            run.progress = f"已保存人工校验；剩余 {run.summary.pending_count} 组候选"
            self._write(db, run)
        return run
