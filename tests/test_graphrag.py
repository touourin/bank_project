"""GraphRAG HTTP boundary and durable document handling without optional ML packages."""

import csv
import io
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from bank_project.api.graphrag import _call, build_router
from bank_project.graphrag import GraphRagError, GraphRagService, jobs, runtime
from bank_project.graphrag.parsers import UploadValidationError, parse_uploads
from bank_project.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path)


def test_txt_utf8_gb18030_and_lossless_source_snapshot(settings):
    service = GraphRagService(settings)
    for encoding in ("utf-8-sig", "gb18030"):
        dataset = service.upload(
            "甲企业与乙企业合作。\r\n贷款余额为NA。".encode(encoding), "银行资料.txt"
        )
        assert dataset["status"] == "ready"
        folder = service.root / dataset["key"]
        rows = list(csv.DictReader(io.StringIO((folder / "input/documents.csv").read_text())))
        assert rows[0]["text"] == "甲企业与乙企业合作。\n贷款余额为NA。"
        assert rows[0]["source_name"] == "银行资料.txt"
        assert not (folder / ".env").exists()
    assert len(GraphRagService(settings).list_datasets()) == 2


@pytest.mark.parametrize(
    "filename,content",
    [
        ("bad.txt", b"\x00\x00"),
        ("blank.txt", b"  \r\n"),
        ("../escape.txt", b"hi"),
        ("bad.txt", b"\xff"),
        ("file.xlsx", b"not a document"),
    ],
)
def test_reject_invalid_txt_without_creating_partial_dataset(settings, filename, content):
    with pytest.raises((GraphRagError, UploadValidationError)):
        GraphRagService(settings).upload(content, filename)
    assert GraphRagService(settings).list_datasets() == []


def test_migrated_parser_batch_is_atomic_and_retains_duplicate_provenance(tmp_path):
    documents = parse_uploads([("a.txt", b"same source"), ("b.txt", b"same source")])
    key = jobs.create_dataset(tmp_path, name="duplicate", description="", documents=documents)
    manifest = jobs._read_json(tmp_path / key / "manifest.json")
    assert manifest["document_count"] == 1
    assert manifest["duplicates"][0]["source_name"] == "b.txt"
    with pytest.raises(UploadValidationError):
        parse_uploads([("okay.txt", b"some source"), ("broken.json", b"{broken")])


def test_interrupted_process_is_durable_retryable_failure(settings, monkeypatch):
    service = GraphRagService(settings)
    job = service.upload(b"a document", "sample.txt")
    folder = service.root / job["key"]
    jobs._atomic_json(
        folder / "job.json", {**job, "status": "running", "pid": 999999999, "launch_time": 0}
    )
    monkeypatch.setattr(jobs, "_alive", lambda _: False)
    result = service.dataset(job["key"])
    assert result["status"] == "failed"
    assert "重试" in result["error"]
    assert jobs._read_json(folder / "job.json")["status"] == "failed"


def test_index_launch_reuses_active_job_and_passes_explicit_settings(settings, monkeypatch):
    service = GraphRagService(settings)
    job = service.upload(b"a document", "sample.txt")
    launches = []
    monkeypatch.setattr(runtime, "initialize", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime, "worker_environment", lambda _: {"BANK_DATA_DIR": str(settings.data_dir)}
    )

    def launch(command, **kwargs):
        launches.append((command, kwargs))
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    first = service.start_index(job["key"])
    second = service.start_index(job["key"])
    assert first["run_id"] == second["run_id"]
    assert len(launches) == 1
    assert "bank_project.graphrag.worker" in launches[0][0]
    assert launches[0][1]["env"]["BANK_DATA_DIR"] == str(settings.data_dir)


def test_index_launch_failure_is_persisted(settings, monkeypatch):
    service = GraphRagService(settings)
    job = service.upload(b"a document", "sample.txt")
    monkeypatch.setattr(runtime, "initialize", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "worker_environment", lambda _: {})

    def launch(*args, **kwargs):
        raise OSError("process unavailable")

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    assert service.start_index(job["key"])["status"] == "failed"
    assert service.dataset(job["key"])["status"] == "failed"


def test_paths_cannot_leave_dataset_root(tmp_path):
    with pytest.raises(ValueError):
        jobs._dataset(tmp_path, "../../other")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        jobs._dataset(tmp_path, "linked")


def test_redaction_masks_environment_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_GRAPHRAG_API_KEY", "super-private-key")
    assert "super-private-key" not in jobs.redact_error("failure: super-private-key", tmp_path)
    assert "token1234" not in jobs.redact_error("Authorization: Bearer token1234", tmp_path)


def test_api_upload_list_status_model_unavailable_and_auth(settings, monkeypatch):
    monkeypatch.setattr(runtime, "dependency_version", lambda: None)
    app = FastAPI()
    app.state.settings = settings
    app.include_router(build_router(settings))
    with TestClient(app) as client:
        upload = client.post(
            "/api/v1/graphrag/uploads?filename=report.txt", content="甲公司收购乙公司。".encode()
        )
        assert upload.status_code == 201
        key = upload.json()["key"]
        assert client.get("/api/v1/graphrag/datasets").json()[0]["key"] == key
        assert client.post(f"/api/v1/graphrag/datasets/{key}/index").status_code == 503
        assert client.get(f"/api/v1/graphrag/datasets/{key}/graph").status_code == 409
        assert client.get("/api/v1/graphrag/datasets/missing").status_code == 404
        assert (
            client.post(
                "/api/v1/graphrag/uploads?filename=report.txt",
                content=b"sample",
                headers={"origin": "https://foreign.example"},
            ).status_code
            == 403
        )
        bad_query = client.post(
            f"/api/v1/graphrag/datasets/{key}/query",
            json={"question": "who?", "method": "invented"},
        )
        assert bad_query.status_code == 422


def test_native_config_validation_error_never_exposes_synthetic_key(settings, monkeypatch):
    secret = "synthetic-private-graphrag-key"

    class NativeConfig(BaseModel):
        model: int

    with pytest.raises(ValidationError) as captured:
        NativeConfig.model_validate({"model": secret})
    native_error = captured.value
    assert secret in str(native_error)

    def invalid_config(*args):
        raise native_error

    monkeypatch.setattr(runtime, "initialize", invalid_config)
    service = GraphRagService(settings)
    job = service.upload(b"valid source", "source.txt")
    with pytest.raises(GraphRagError) as caught:
        service.start_index(job["key"])
    assert caught.value.status == 503
    assert secret not in str(caught.value)
    assert service.dataset(job["key"])["status"] == "ready"

    app = FastAPI()
    app.state.settings = settings
    app.include_router(build_router(settings))
    with TestClient(app) as client:
        response = client.post(f"/api/v1/graphrag/datasets/{job['key']}/index")
        assert response.status_code == 503
        assert secret not in response.text
        assert "input_value" not in response.text
        # User document validation remains readable, unlike native config diagnostics.
        empty = client.post("/api/v1/graphrag/uploads?filename=empty.txt", content=b"  ")
        assert empty.status_code == 400
        assert "正文为空" in empty.json()["detail"]

    # The HTTP boundary is also safe if a future native path escapes the facade.
    with pytest.raises(HTTPException) as boundary:
        _call(invalid_config)
    assert boundary.value.status_code == 503
    assert secret not in boundary.value.detail
