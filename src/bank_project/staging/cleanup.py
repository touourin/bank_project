"""Restartable, bounded physical removal of explicitly purged intake batches."""

import logging
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)


class BatchCleanup:
    def __init__(self, database, upload_dir: Path):
        self.database, self.upload_dir = database, upload_dir.resolve()

    def _upload(self, path):
        """Only server-owned UUID uploads may be unlinked, never caller-selected files."""
        file = Path(path)
        UUID(file.name)
        if file.parent.resolve() != self.upload_dir or file.is_symlink():
            raise ValueError("Upload path is outside managed storage")
        return file

    def collect(self):
        with self.database.connect() as db:
            db.execute(
                "SELECT id FROM intake_batches WHERE state='purging' AND deleted=TRUE "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            batch = db.fetchone()
            if batch is None:
                return False
            batch_id = batch[0]
            # Lock the batch until this small transaction completes; multiple workers are safe.
            db.execute(
                "SELECT id,state,path FROM intake_jobs WHERE batch_id=%s FOR UPDATE", (batch_id,)
            )
            jobs = db.fetchall()
            if any(row[1] != "completed" for row in jobs):
                raise ValueError("Cannot purge an unfinished intake job")
            files = [self._upload(row[2]) for row in jobs if row[2]]
            db.execute("SELECT id FROM intake_tables WHERE batch_id=%s LIMIT 1", (batch_id,))
            table = db.fetchone()
            if table:
                db.execute("DELETE FROM intake_rows WHERE table_id=%s LIMIT 1000", (table[0],))
                if not db.rowcount:
                    db.execute("DELETE FROM intake_tables WHERE id=%s", (table[0],))
            else:
                # Files are unlinked first. Retrying after a crash is safe, including an
                # uncertain DB commit, because no completed job can be retried or published.
                for file in files:
                    db.execute(
                        "SELECT 1 FROM intake_jobs WHERE path=%s AND (batch_id IS NULL OR batch_id<>%s) LIMIT 1",
                        (str(file), batch_id),
                    )
                    if db.fetchone() is None:
                        file.unlink(missing_ok=True)
                db.execute("DELETE FROM intake_jobs WHERE batch_id=%s", (batch_id,))
                db.execute("DELETE FROM intake_batches WHERE id=%s", (batch_id,))
            return True

    def run(self, stopping):
        while not stopping.is_set():
            try:
                stopping.wait(0.05 if self.collect() else 2)
            except Exception as exc:
                logger.warning("Batch cleanup will retry (%s)", type(exc).__name__)
                stopping.wait(5)
