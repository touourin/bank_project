"""Durable runs with a single leased job across API workers sharing this SQLite file."""

import json
import sqlite3
import time
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import AlignmentError, Run, SourceTable

LEASE_SECONDS = 60


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL, sources BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL, kind TEXT NOT NULL,
                    owner TEXT NOT NULL, expires REAL NOT NULL, active INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(active) WHERE active=1;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _read(db, run_id):
        row = db.execute("SELECT metadata FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise AlignmentError("分析记录不存在", 404)
        return Run.model_validate_json(row[0])

    @staticmethod
    def _write(db, run):
        db.execute("UPDATE runs SET metadata=? WHERE id=?", (run.model_dump_json(), run.id))

    def _expire(self, db):
        for seq, run_id, kind in db.execute(
            "SELECT sequence,run_id,kind FROM jobs WHERE active=1 AND expires<?", (time.time(),)
        ).fetchall():
            run = self._read(db, run_id)
            message = "任务因服务中断或租约过期而停止，请重新发起；当前已发布图谱保持不变"
            if kind == "analyze":
                run.status, run.error, run.progress = "failed", message, "分析中断"
            else:
                run.graph_status, run.graph_error = "failed", message
            self._write(db, run)
            db.execute("UPDATE jobs SET active=0 WHERE sequence=?", (seq,))

    def _claim(self, db, run_id, kind):
        self._expire(db)
        if db.execute("SELECT 1 FROM jobs WHERE active=1").fetchone():
            raise AlignmentError("已有分析或图谱生成任务在执行，请等待完成", 409)
        owner = str(uuid4())
        cursor = db.execute(
            "INSERT INTO jobs(run_id,kind,owner,expires,active) VALUES(?,?,?,?,1)",
            (run_id, kind, owner, time.time() + LEASE_SECONDS),
        )
        return cursor.lastrowid, owner

    def create(self, sources: list[SourceTable]):
        run = Run(id=str(uuid4()), created_at=datetime.now(UTC).isoformat())
        content = zlib.compress(
            json.dumps([s.model_dump() for s in sources], ensure_ascii=False).encode()
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sequence, owner = self._claim(db, run.id, "analyze")
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                (run.id, run.created_at, run.model_dump_json(), content),
            )
        return run, sequence, owner

    def start_graph(self, run_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            run = self._read(db, run_id)
            if (
                run.status != "ready"
                or not run.result
                or not (
                    any(t.status == "mapped" for t in run.result.tables)
                    or (run.result.template and run.result.template.nodes)
                )
            ):
                raise AlignmentError("没有可生成实例的匹配结果")
            if run.graph_status == "ready":
                raise AlignmentError("该分析版本已生成图谱，请查看已发布版本", 409)
            sequence, owner = self._claim(db, run_id, "graph")
            run.graph_status, run.graph_error = "building", None
            self._write(db, run)
        return run, sequence, owner

    def get(self, run_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            return self._read(db, run_id)

    def revise(self, run_id, result):
        """Branch immutable mapping evidence; published versions remain untouched."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            original = self._read(db, run_id)
            if original.status != "ready" or original.graph_status == "building":
                raise AlignmentError("请等待当前任务完成后再修改匹配", 409)
            run = Run(
                id=str(uuid4()),
                created_at=datetime.now(UTC).isoformat(),
                status="ready",
                progress="人工修改已保存，请核对后生成图谱",
                result=result,
                based_on_run_id=original.id,
            )
            db.execute(
                "INSERT INTO runs SELECT ?,?, ?,sources FROM runs WHERE id=?",
                (run.id, run.created_at, run.model_dump_json(), original.id),
            )
        return run

    def list(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            return [
                Run.model_validate_json(r[0])
                for r in db.execute("SELECT metadata FROM runs ORDER BY created_at DESC LIMIT 30")
            ]

    def sources(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT sources FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise AlignmentError("分析记录不存在", 404)
        return [SourceTable.model_validate(s) for s in json.loads(zlib.decompress(row[0]))]

    def heartbeat(self, sequence, owner):
        with self.connect() as db:
            now = time.time()
            count = db.execute(
                "UPDATE jobs SET expires=? WHERE sequence=? AND owner=? AND active=1 AND expires>=?",
                (now + LEASE_SECONDS, sequence, owner, now),
            ).rowcount
            if not count:
                raise AlignmentError("任务已失去执行租约，请重新发起", 409)

    def update(self, run_id, sequence, owner, *, finish=False, **changes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM jobs WHERE sequence=? AND owner=? AND active=1 AND expires>=?",
                (sequence, owner, time.time()),
            ).fetchone():
                raise AlignmentError("任务已失去执行租约", 409)
            run = self._read(db, run_id)
            run = Run.model_validate({**run.model_dump(), **changes})
            self._write(db, run)
            if finish:
                db.execute("UPDATE jobs SET active=0 WHERE sequence=?", (sequence,))
            return run
