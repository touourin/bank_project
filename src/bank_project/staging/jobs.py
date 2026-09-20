"""Durable ingestion queue. Retry creates a fresh invisible attempt; publication is fenced."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from bank_project.intake.models import IntakeError

from .store import dumps


class IntakeJobs:
    def __init__(self, database):
        self.database = database
        with database.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS intake_jobs (
                id CHAR(36) CHARACTER SET ascii PRIMARY KEY, created_at VARCHAR(40) NOT NULL,
                state VARCHAR(16) NOT NULL, name VARCHAR(255) NOT NULL, path VARCHAR(512) NOT NULL,
                options JSON NOT NULL, digest CHAR(64) NOT NULL, size BIGINT NOT NULL,
                rows_done BIGINT NOT NULL DEFAULT 0, attempt INT NOT NULL DEFAULT 0,
                owner CHAR(36) NULL, heartbeat DOUBLE NULL, batch_id CHAR(36) NULL,
                error TEXT NULL, INDEX job_queue(state,created_at)) ENGINE=InnoDB""")
            db.execute(
                "CREATE TABLE IF NOT EXISTS intake_queue_guard (id INT PRIMARY KEY) ENGINE=InnoDB"
            )
            db.execute("INSERT IGNORE INTO intake_queue_guard VALUES(1)")

    @staticmethod
    def _admit(db):
        db.execute("SELECT id FROM intake_queue_guard WHERE id=1 FOR UPDATE")
        db.fetchone()
        db.execute("SELECT COUNT(*) FROM intake_jobs WHERE state IN ('queued','running')")
        if db.fetchone()[0] >= 50:
            raise IntakeError("接入队列已有 50 个待处理任务，请稍后重试", status=429)

    def create(self, name, path, options, digest, size):
        key = str(uuid4())
        with self.database.connect() as db:
            self._admit(db)
            db.execute(
                "INSERT INTO intake_jobs(id,created_at,state,name,path,options,digest,size) VALUES(%s,%s,'queued',%s,%s,%s,%s,%s)",
                (key, datetime.now(UTC).isoformat(), name, str(path), dumps(options), digest, size),
            )
        return self.get(key)

    def get(self, key):
        with self.database.connect() as db:
            db.execute(
                "SELECT id,created_at,state,name,size,rows_done,attempt,batch_id,error FROM intake_jobs WHERE id=%s",
                (key,),
            )
            row = db.fetchone()
        if not row:
            raise IntakeError("接入任务不存在", status=404)
        return dict(
            zip(
                (
                    "id",
                    "created_at",
                    "status",
                    "name",
                    "size_bytes",
                    "rows_done",
                    "attempt",
                    "batch_id",
                    "error",
                ),
                row,
                strict=True,
            )
        )

    def list(self):
        with self.database.connect() as db:
            db.execute("SELECT id FROM intake_jobs ORDER BY created_at DESC LIMIT 30")
            keys = [row[0] for row in db.fetchall()]
        return [self.get(key) for key in keys]

    def retry(self, key):
        with self.database.connect() as db:
            self._admit(db)
            db.execute(
                "UPDATE intake_jobs SET state='queued',owner=NULL,error=NULL,rows_done=0 WHERE id=%s AND state IN ('failed','cancelled')",
                (key,),
            )
            if not db.rowcount:
                raise IntakeError("只有失败或取消的任务可以重试", status=409)
        return self.get(key)

    def cancel(self, key):
        with self.database.connect() as db:
            db.execute(
                "UPDATE intake_jobs SET state='cancelled',owner=NULL WHERE id=%s AND state IN ('queued','running')",
                (key,),
            )
            if not db.rowcount:
                raise IntakeError("任务已结束", status=409)
        return self.get(key)

    def claim(self):
        with self.database.connect() as db:
            # Recovery after abrupt worker exit; original uploaded file remains available.
            db.execute(
                "UPDATE intake_batches b JOIN intake_jobs j ON b.id=j.batch_id SET b.state='failed' WHERE b.state='building' AND j.state='running' AND j.heartbeat<UNIX_TIMESTAMP()-90"
            )
            db.execute(
                "UPDATE intake_jobs SET state='failed',owner=NULL,error='处理进程中断，可重新解析已上传文件' WHERE state='running' AND heartbeat<UNIX_TIMESTAMP()-90"
            )
            db.execute(
                "SELECT id,path,name,options,digest FROM intake_jobs WHERE state='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            row = db.fetchone()
            if row is None:
                return None
            owner = str(uuid4())
            db.execute(
                "UPDATE intake_jobs SET state='running',owner=%s,heartbeat=UNIX_TIMESTAMP(),attempt=attempt+1,rows_done=0,batch_id=NULL WHERE id=%s",
                (owner, row[0]),
            )
        return dict(zip(("id", "path", "name", "options", "digest"), row, strict=True)) | {
            "owner": owner,
            "options": json.loads(row[3]),
        }

    def bind(self, job, batch_id):
        with self.database.connect() as db:
            db.execute(
                "UPDATE intake_jobs SET batch_id=%s WHERE id=%s AND owner=%s AND state='running'",
                (batch_id, job["id"], job["owner"]),
            )
            if not db.rowcount:
                raise IntakeError("任务处理权已失效", status=409)

    def heartbeat(self, job, rows=None):
        with self.database.connect() as db:
            db.execute(
                "UPDATE intake_jobs SET heartbeat=UNIX_TIMESTAMP(),rows_done=COALESCE(%s,rows_done) WHERE id=%s AND owner=%s AND state='running'",
                (rows, job["id"], job["owner"]),
            )
            if not db.rowcount:
                # MySQL reports changed rows; a heartbeat within the same second may be unchanged.
                db.execute(
                    "SELECT 1 FROM intake_jobs WHERE id=%s AND owner=%s AND state='running'",
                    (job["id"], job["owner"]),
                )
                if not db.fetchone():
                    raise IntakeError("任务已取消或处理权已失效", status=409)

    def publish(self, job, writer, warnings):
        batch = writer.batch
        batch.table_count, batch.warnings = len(batch.tables), list(warnings)
        with self.database.connect() as db:
            db.execute("SELECT state,owner FROM intake_jobs WHERE id=%s FOR UPDATE", (job["id"],))
            state, owner = db.fetchone()
            if state != "running" or owner != job["owner"]:
                raise IntakeError("任务已取消或处理权已失效", status=409)
            db.execute(
                "UPDATE intake_batches SET state='ready',metadata=%s WHERE id=%s AND state='building'",
                (dumps(batch.model_dump(exclude={"tables"})), batch.id),
            )
            if not db.rowcount:
                raise IntakeError("暂存批次状态不正确", status=409)
            db.execute(
                "UPDATE intake_jobs SET state='completed',rows_done=%s,batch_id=%s,owner=NULL WHERE id=%s",
                (batch.row_count, batch.id, job["id"]),
            )
        writer.published = True

    def fail(self, job, message):
        with self.database.connect() as db:
            db.execute(
                "UPDATE intake_jobs SET state='failed',error=%s,owner=NULL WHERE id=%s AND owner=%s AND state='running'",
                (message, job["id"], job["owner"]),
            )
