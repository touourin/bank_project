"""Durable matching snapshots with compare-and-swap human review."""

import json
import sqlite3
from contextlib import contextmanager

from bank_project.alignment.models import AlignmentError


class MatchStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            # Single API process, same deployment boundary as existing alignment.
            for row in db.execute(
                "SELECT id,data FROM matches WHERE json_extract(data,'$.status')='running'"
            ).fetchall():
                value = json.loads(row[1])
                if value["status"] == "running":
                    value.update(
                        status="failed", error="匹配任务中断，请重新发起；已保存的原图不变"
                    )
                    db.execute("UPDATE matches SET data=? WHERE id=?", (json.dumps(value), row[0]))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                db.execute("PRAGMA journal_mode=WAL")
                yield db
        finally:
            db.close()

    def create(self, value):
        with self.connection() as db:
            db.execute(
                "INSERT INTO matches VALUES (?,?)",
                (value["id"], json.dumps(value, ensure_ascii=False)),
            )

    def get(self, identifier):
        with self.connection() as db:
            row = db.execute("SELECT data FROM matches WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise AlignmentError("匹配任务不存在", 404)
        return json.loads(row[0])

    def list(self):
        with self.connection() as db:
            rows = db.execute(
                "SELECT json_remove(data,'$.graph','$.nodes','$.edges','$.audits') FROM matches ORDER BY rowid DESC"
            ).fetchall()
        return [self.public(json.loads(r[0]), summary=True) for r in rows]

    @staticmethod
    def public(value, summary=False):
        result = {k: v for k, v in value.items() if k != "graph"}
        if summary:
            result.update(nodes=[], edges=[], audits=[])
        elif "edges" in result:
            # Old runs already contain direction-checked candidates. Expose their
            # suggestions without rewriting the saved graph or review revision.
            result["edges"] = [
                {
                    **edge,
                    "proposed_edge_type": edge.get(
                        "proposed_edge_type",
                        edge["candidates"][0] if len(edge.get("candidates", [])) == 1 else None,
                    ),
                }
                for edge in result["edges"]
            ]
        return result

    def mutate(self, identifier, action, expected_revision=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM matches WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise AlignmentError("匹配任务不存在", 404)
            value = json.loads(row[0])
            if expected_revision is not None and value["revision"] != expected_revision:
                raise AlignmentError("其他操作已更新匹配结果，请刷新后重试", 409)
            action(value)
            db.execute(
                "UPDATE matches SET data=? WHERE id=?",
                (json.dumps(value, ensure_ascii=False), identifier),
            )
        return value
