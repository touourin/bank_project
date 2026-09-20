"""Separate, restartable intake worker. Owns parsing; API remains responsive."""

import logging
import threading
from pathlib import Path

from bank_project.intake.models import IntakeError
from bank_project.intake.streaming import parse_into
from bank_project.settings import Settings
from bank_project.staging.database import StagingDatabase
from bank_project.staging.jobs import IntakeJobs
from bank_project.staging.store import MysqlBatchStore

logger = logging.getLogger(__name__)


def run_job(store, jobs, job, limits):
    stopped = threading.Event()
    lost = []

    def heartbeat():
        while not stopped.wait(10):
            try:
                jobs.heartbeat(job)
            except Exception as exc:
                lost.append(exc)
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        options = dict(job["options"])
        mysql_payload = options.pop("mysql", None)
        connection = None
        if mysql_payload:
            from bank_project.intake.mysql import MysqlConnection, MysqlSource
            from bank_project.staging.secrets import decrypt_connection

            payload = decrypt_connection(store.database.settings.data_dir, mysql_payload)
            connection = MysqlConnection.model_validate(payload["connection"])
        source = (
            f"{connection.host}:{connection.port}/{connection.database}"
            if connection
            else job["name"]
        )
        with store.writer(
            name=job["name"],
            source_kind="mysql" if connection else "file",
            source=source,
            sha256=job["digest"] or None,
        ) as writer:
            jobs.bind(job, writer.batch.id)

            def progress(count):
                if lost:
                    raise IntakeError("任务处理权已失效，可重新解析", status=409)
                jobs.heartbeat(job, count)

            writer.progress = progress
            if connection:
                warnings = MysqlSource(limits).read(connection, payload["tables"], writer).warnings
            else:
                warnings = parse_into(Path(job["path"]), job["name"], limits, writer, **options)
            if lost:
                raise IntakeError("任务处理权已失效，可重新解析", status=409)
            jobs.publish(job, writer, warnings)
    except Exception as exc:
        message = (
            exc.message
            if isinstance(exc, IntakeError)
            else "解析或暂存失败，请检查数据库、文件格式和磁盘后重试"
        )
        logger.warning("Intake job %s failed (%s)", job["id"], type(exc).__name__)
        jobs.fail(job, message)
    finally:
        stopped.set()
        thread.join(timeout=15)


def main():
    import signal

    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    database = StagingDatabase(settings)
    store, jobs = MysqlBatchStore(database), IntakeJobs(database)
    from bank_project.staging.compile import TemplateCompiler
    from bank_project.staging.keys import KeyIndex

    compiler = TemplateCompiler(store, KeyIndex(store))
    limits = settings.intake_limits()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    while not stopping.is_set():
        try:
            job = jobs.claim()
            if job:
                run_job(store, jobs, job, limits)
            else:
                cleaned = store.collect_failed()
                cleaned = compiler.collect() or cleaned
                stopping.wait(0.1 if cleaned else 2)
        except Exception as exc:
            logger.warning("Intake worker waiting (%s)", type(exc).__name__)
            stopping.wait(5)


if __name__ == "__main__":
    main()
