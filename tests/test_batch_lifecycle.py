"""Deletion must preserve history and cannot race admission of a new analysis."""

import json
import sqlite3
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bank_project.alignment.models import AlignmentError, Selection
from bank_project.alignment.sources import StagedSources
from bank_project.alignment.store import RunStore
from bank_project.intake.files import parse_file
from bank_project.intake.lifecycle import BatchLifecycle
from bank_project.intake.models import IntakeError, Limits
from bank_project.intake.store import BatchStore
from bank_project.main import create_app
from bank_project.settings import Settings


@pytest.fixture
def stores(tmp_path):
    batches = BatchStore(tmp_path / "batches.sqlite3")
    batch = batches.save(
        parse_file(b"id\n001\n002\n", "same.csv", Limits()),
        name="same.csv",
        source_kind="file",
        source="same.csv",
    )
    runs = RunStore(tmp_path / "runs.sqlite3")
    selection = [Selection(batch_id=batch.id, table_id=batch.tables[0].id)]

    def read():
        return StagedSources(batches, 100).read(selection)

    return batches, runs, batch, read


def test_remove_restore_and_purge_are_separate_and_auth_protected(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, api_token="test-token-for-batch-lifecycle-only"
    )
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer test-token-for-batch-lifecycle-only"

        def upload():
            return client.post(
                "/api/v1/intake/uploads?filename=same.csv", content=b"id\n001\n"
            ).json()

        one, two = upload(), upload()
        path = f"/api/v1/intake/batches/{one['id']}"
        assert client.delete(path + "/permanent").status_code == 409
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404
        removed = client.get("/api/v1/intake/batches?removed=true").json()
        assert [b["id"] for b in removed["items"]] == [one["id"]]
        assert client.get(path + "?removed=true").json()["removed"]
        assert client.get(f"{path}/tables/{one['tables'][0]['id']}?removed=true").json()["rows"][0][
            "values"
        ] == ["001"]
        assert client.post(path + "/restore").status_code == 200
        assert client.get(path).status_code == 200
        client.delete(path)
        assert client.get(path + "/references").json() == {"count": 0, "run_ids": []}
        client.headers.pop("Authorization")
        assert client.delete(path + "/permanent").status_code == 401
        client.headers["Authorization"] = "Bearer test-token-for-batch-lifecycle-only"
        assert client.delete(path + "/permanent").json() == {"status": "deleted"}
        assert client.get(path + "?removed=true").status_code == 404
        assert client.get(f"/api/v1/intake/batches/{two['id']}").status_code == 200


def test_references_survive_revision_and_backfill_beyond_recent_list(stores):
    batches, runs, batch, read = stores
    sources = read()
    run, sequence, owner = runs.create(sources)
    runs.update(run.id, sequence, owner, status="ready", finish=True)
    revised = runs.revise(run.id, None)
    # Simulate pre-upgrade history, including an old referenced run outside the UI's 30.
    with runs.connect() as db:
        for _ in range(35):
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                (str(uuid4()), "9999", run.model_dump_json(), zlib.compress(b"[]")),
            )
        db.execute("DROP TABLE run_batches")
    runs = RunStore(runs.path)
    lifecycle = BatchLifecycle(batches, runs)
    lifecycle.remove(batch.id)
    references = lifecycle.references(batch.id)
    assert references.count == 2 and set(references.run_ids) == {run.id, revised.id}
    with pytest.raises(IntakeError, match="2 个历史分析"):
        lifecycle.purge(batch.id)
    assert runs.sources(run.id)[0].rows[0].values == ["001"]
    assert batches.get(batch.id, include_deleted=True).row_count == 2


def test_analysis_admission_serializes_removal_and_records_reference(stores):
    batches, runs, batch, read = stores
    # Separate RunStore instances use the shared database lock, not a process-local mutex.
    lifecycle = BatchLifecycle(batches, RunStore(runs.path))
    entered, release, deleting = threading.Event(), threading.Event(), threading.Event()

    def delayed_read():
        sources = read()
        entered.set()
        assert release.wait(5)
        return sources

    def remove_and_purge():
        deleting.set()
        lifecycle.remove(batch.id)
        lifecycle.purge(batch.id)

    with ThreadPoolExecutor(2) as pool:
        analysis = pool.submit(runs.create_from, delayed_read)
        assert entered.wait(5)
        removal = pool.submit(remove_and_purge)
        assert deleting.wait(5)
        assert not removal.done()
        release.set()
        run, _, _ = analysis.result(timeout=5)
        with pytest.raises(IntakeError, match="历史分析"):
            removal.result(timeout=5)
    assert lifecycle.references(batch.id).run_ids == [run.id]


def test_deleted_source_cannot_enter_new_analysis(stores):
    batches, runs, batch, read = stores
    lifecycle = BatchLifecycle(batches, runs)
    lifecycle.remove(batch.id)
    with pytest.raises(AlignmentError):
        runs.create_from(read)
    lifecycle.purge(batch.id)
    with pytest.raises(AlignmentError):
        runs.create_from(read)
    assert runs.list() == []


def test_sqlite_migration_preserves_existing_batches(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE batches(id TEXT PRIMARY KEY, created_at TEXT, metadata TEXT)")
        metadata = {
            "id": "legacy",
            "created_at": "2026",
            "name": "old.csv",
            "source_kind": "file",
            "source": "old.csv",
            "row_count": 0,
            "table_count": 0,
        }
        db.execute("INSERT INTO batches VALUES(?,?,?)", ("legacy", "2026", json.dumps(metadata)))
    store = BatchStore(path)
    assert store.get("legacy").name == "old.csv"
    store.delete("legacy")
    assert BatchStore(path).list(0, 10, removed=True).total == 1


def test_cleanup_rejects_external_paths_and_symlinks(tmp_path):
    from bank_project.staging.cleanup import BatchCleanup

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    external = tmp_path / str(uuid4())
    external.write_text("keep")
    cleanup = BatchCleanup(None, uploads)
    with pytest.raises(ValueError):
        cleanup._upload(str(external))
    link = uploads / str(uuid4())
    link.symlink_to(external)
    with pytest.raises(ValueError):
        cleanup._upload(str(link))
    assert external.read_text() == "keep"
