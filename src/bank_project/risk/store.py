"""Single-API durable jobs, immutable case content and atomic review gates."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from bank_project.alignment.models import AlignmentError


def now():
    return datetime.now(UTC).isoformat()


def dumps(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def content_hash(case):
    return digest(
        {
            k: case[k]
            for k in ("name", "description", "rule_pack", "source_binding", "validation_issues")
        }
    )


class RiskStore:
    TABLES = {"jobs", "cases", "executions"}

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            for table in sorted(self.TABLES):
                db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY,data TEXT NOT NULL)"
                )
            for table in ("jobs", "executions"):
                for identifier, raw in db.execute(
                    f"SELECT id,data FROM {table} WHERE json_extract(data,'$.status')='running'"
                ).fetchall():
                    value = json.loads(raw)
                    value.update(
                        status="failed",
                        error="服务重启导致任务中断，请重新提交；已保存记录仍可审核",
                        updated_at=now(),
                    )
                    db.execute(f"UPDATE {table} SET data=? WHERE id=?", (dumps(value), identifier))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    def _table(self, table):
        if table not in self.TABLES:
            raise ValueError("unknown risk storage table")
        return table

    def put(self, table, value, *, create=False):
        table = self._table(table)
        with self.connection() as db:
            if create:
                db.execute(f"INSERT INTO {table} VALUES (?,?)", (value["id"], dumps(value)))
            else:
                result = db.execute(
                    f"UPDATE {table} SET data=? WHERE id=?", (dumps(value), value["id"])
                )
                if result.rowcount != 1:
                    raise AlignmentError("风险记录不存在", 404)

    def get(self, table, identifier, db=None):
        table = self._table(table)
        if db is None:
            with self.connection() as db:
                return self.get(table, identifier, db)
        row = db.execute(f"SELECT data FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise AlignmentError("风险记录不存在", 404)
        return json.loads(row[0])

    def list(self, table, case_id=None):
        table = self._table(table)
        with self.connection() as db:
            if case_id:
                rows = db.execute(
                    f"SELECT data FROM {table} WHERE json_extract(data,'$.case_id')=? ORDER BY rowid DESC",
                    (case_id,),
                ).fetchall()
            else:
                rows = db.execute(f"SELECT data FROM {table} ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def expect(case, request):
        if (
            case["version"] != request.expected_version
            or case["content_hash"] != request.expected_hash
        ):
            raise AlignmentError("规则或审核状态已变化，请刷新后重新确认", 409)
        if content_hash(case) != case["content_hash"]:
            raise AlignmentError("规则内容完整性校验失败", 409)

    def review(self, identifier, request, validate):
        with self.connection() as db:
            case = self.get("cases", identifier, db)
            self.expect(case, request)
            if request.action == "approve":
                if not request.evidence_confirmed:
                    raise AlignmentError("请先核对 WHY 原文、规则参数与传导适用性并确认依据")
                validate(case)
            before = case["review_status"]
            case.update(
                version=case["version"] + 1,
                review_status="approved" if request.action == "approve" else "rejected",
                execution_status="ready" if request.action == "approve" else "blocked",
                updated_at=now(),
            )
            case["audits"].append(
                {
                    "at": now(),
                    "action": request.action,
                    "reason": request.reason,
                    "evidence_confirmed": request.evidence_confirmed,
                    "previous_status": before,
                    "version": case["version"],
                    "content_hash": case["content_hash"],
                }
            )
            db.execute("UPDATE cases SET data=? WHERE id=?", (dumps(case), identifier))
            return case

    def begin_execution(self, identifier, request, execution, validate):
        with self.connection() as db:
            case = self.get("cases", identifier, db)
            self.expect(case, request)
            if case["review_status"] != "approved" or case["execution_status"] != "ready":
                raise AlignmentError("规则尚未通过人工审核，不能执行", 409)
            validate(case)
            execution.update(
                case_id=identifier,
                case_version=case["version"],
                content_hash=case["content_hash"],
                rule_pack=case["rule_pack"],
                source_binding=case["source_binding"],
            )
            db.execute("INSERT INTO executions VALUES (?,?)", (execution["id"], dumps(execution)))
            return execution
