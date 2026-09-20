"""Streaming parser parity and task/storage integration on explicitly enabled local MySQL."""

import os
from io import BytesIO
from zipfile import ZipFile

import pytest
from test_intake import workbook

from bank_project.intake.files import parse_file
from bank_project.intake.models import IntakeError, Limits
from bank_project.intake.streaming import parse_into


class Sink:
    def __init__(self):
        self.tables = []

    def start(self, name, columns, foreign_keys=()):
        self.tables.append({"name": name, "columns": columns, "rows": []})

    def append(self, row):
        self.tables[-1]["rows"].append(row)

    def finish(self, columns, warnings=()):
        self.tables[-1]["columns"] = columns


@pytest.mark.parametrize(
    "suffix,content",
    [
        (
            "csv",
            '编号,金额,备注\r\n001,12345678901234567890.1200," 中 文\r\n下一行 "\r\n002,,x\r\n'.encode(
                "utf-8-sig"
            ),
        ),
        (
            "xlsx",
            workbook(
                {
                    "客户": [
                        ["id", "姓名", "备注"],
                        ["001", "甲", None],
                        [],
                        ["002", "乙", "中文"],
                    ],
                    "字段说明": [["说明"]],
                }
            ),
        ),
    ],
)
def test_streamed_parser_retains_values_types_rows(tmp_path, suffix, content):
    path = tmp_path / ("data." + suffix)
    path.write_bytes(content)
    sink = Sink()
    parse_into(path, path.name, Limits(), sink)
    expected = parse_file(content, path.name, Limits())
    for actual, table in zip(sink.tables, expected.tables, strict=True):
        assert actual["rows"] == table.rows
        assert actual["columns"] == table.columns


@pytest.mark.parametrize(
    "rows",
    [
        [["id"], ["A", "=1+2"]],
        [["id", "value"], ["A", "=1+2"]],
        [["id", "id"], ["A", "B"]],
    ],
)
def test_streamed_xlsx_rejects_invalid_cells(tmp_path, rows):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(workbook({"客户": rows}))
    with pytest.raises(IntakeError):
        parse_into(path, path.name, Limits(), Sink())


def test_shared_strings_are_resolved_from_disk(tmp_path):
    raw = workbook({"客户": [["id"], ["001"]]})
    path = tmp_path / "shared.xlsx"
    with ZipFile(BytesIO(raw)) as original, ZipFile(path, "w") as target:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(
                    b'<c r="A2" t="inlineStr"><is><t>001</t></is></c>',
                    b'<c r="A2" t="s"><v>0</v></c>',
                )
            target.writestr(item, data)
        target.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>001</t></si></sst>',
        )
    sink = Sink()
    parse_into(path, path.name, Limits(), sink)
    assert sink.tables[0]["rows"][0].values == ["001"]


def test_streaming_sink_does_not_accumulate_rows(tmp_path):
    from bank_project.intake.files import TableReader

    count = 0

    def receive(row):
        nonlocal count
        count += 1

    reader = TableReader("test", ["id"], Limits(), receive)
    for i in range(10000):
        reader.add(i + 2, [str(i)])
    assert reader.finish().rows == [] and count == 10000


@pytest.fixture
def mysql(tmp_path):
    if not os.getenv("BANK_TEST_MYSQL"):
        pytest.skip("requires explicit BANK_TEST_MYSQL=1; creates isolated test batches only")
    from bank_project.settings import Settings
    from bank_project.staging.database import StagingDatabase
    from bank_project.staging.jobs import IntakeJobs
    from bank_project.staging.store import MysqlBatchStore

    s = Settings(data_dir=tmp_path, model_api_key=None)
    if not s.staging_mysql_database.endswith("_test"):
        pytest.fail(
            "MySQL integration tests require an isolated BANK_STAGING_MYSQL_DATABASE ending in _test"
        )
    store = MysqlBatchStore(StagingDatabase(s))
    jobs = IntakeJobs(store.database)
    batch_ids = []
    job_ids = []
    yield s, store, jobs, batch_ids, job_ids
    with store.database.connect() as db:
        for key in job_ids:
            db.execute("SELECT batch_id FROM intake_jobs WHERE id=%s", (key,))
            row = db.fetchone()
            if row and row[0]:
                batch_ids.append(row[0])
            db.execute("DELETE FROM intake_jobs WHERE id=%s", (key,))
        for key in set(batch_ids):
            db.execute("DELETE FROM intake_batches WHERE id=%s", (key,))


def test_mysql_snapshot_samples_sql_join_and_fidelity(mysql):
    from bank_project.alignment.models import Selection
    from bank_project.alignment.sources import StagedSources
    from bank_project.staging.keys import KeyIndex

    _, store, _, ids, _ = mysql

    def save(text):
        batch = store.save(
            parse_file(text.encode(), "test.csv", Limits()),
            name="test",
            source_kind="file",
            source="test",
        )
        ids.append(batch.id)
        return batch

    left = save("id,name\n001,甲\n01,乙\nA,丙\na,丁\nA ,戊\n,空\n")
    right = save("id,name\n001,甲\nA,丙\na,丁\n")
    read = StagedSources(store, 1)  # sampling must not apply a whole-table memory limit
    sources = read.read([Selection(batch_id=b.id, table_id=b.tables[0].id) for b in (left, right)])
    assert sources[0].staged and len(sources[0].rows) <= 5
    index = KeyIndex(store)
    assert index.stats(*sources, ["id"], ["id"]) == (False, 3, 2)
    assert list(index.pairs(*sources, ["id"], ["id"])) == [(0, 0), (2, 1), (3, 2)]
    # Even deliberate digest collisions must not merge distinct identifiers.
    keys = [index.ensure(s, ["id"]) for s in sources]
    with store.database.connect() as db:
        for k in keys:
            db.execute("UPDATE staging_keys SET digest=%s WHERE key_set=%s", (b"x" * 32, k))
    assert index.stats(*sources, ["id"], ["id"]) == (False, 3, 2)
    store.delete(left.id)
    with pytest.raises(IntakeError):
        store.get(left.id)
    assert store.get(left.id, include_deleted=True).row_count == 6
    assert len(list(store.iter_rows(left.tables[0].id))) == 6
    with store.database.connect() as db:
        for k in keys:
            db.execute("DELETE FROM staging_key_sets WHERE id=%s", (k,))


def test_mysql_job_cancel_retry_and_atomic_visibility(mysql, tmp_path):
    from bank_project.intake.worker import run_job

    _, store, jobs, ids, job_ids = mysql
    path = tmp_path / "job.csv"
    path.write_text("id\n001\n002\n")
    new = jobs.create("test.csv", path, {}, "x" * 64, path.stat().st_size)
    job_ids.append(new["id"])
    claimed = jobs.claim()
    assert claimed["id"] == new["id"]
    jobs.cancel(new["id"])
    run_job(store, jobs, claimed, Limits())
    assert jobs.get(new["id"])["status"] == "cancelled"
    jobs.retry(new["id"])
    claimed = jobs.claim()
    run_job(store, jobs, claimed, Limits())
    result = jobs.get(new["id"])
    assert result["status"] == "completed" and result["attempt"] == 2
    assert store.get(result["batch_id"]).row_count == 2


def test_async_upload_api_and_partial_body_cleanup(mysql, tmp_path):
    from fastapi.testclient import TestClient

    from bank_project.intake.worker import run_job
    from bank_project.main import create_app

    s, store, jobs, ids, job_ids = mysql
    s.intake_max_upload_mb = 1
    with TestClient(create_app(s)) as client:
        result = client.post("/api/v1/intake/uploads?filename=hello.csv", content=b"id\n001\n")
        assert result.status_code == 202
        job_ids.append(result.json()["id"])
        run_job(store, jobs, jobs.claim(), s.intake_limits())
        job = client.get("/api/v1/intake/jobs/" + result.json()["id"]).json()
        assert job["status"] == "completed"
        assert "path" not in job and "options" not in job
        before = set((tmp_path / "uploads").iterdir())
        result = client.post(
            "/api/v1/intake/uploads?filename=large.csv", content=(b"a" * 65536 for _ in range(17))
        )
        assert result.status_code == 413
        assert set((tmp_path / "uploads").iterdir()) == before


def test_confirmed_template_splits_rows_merges_identity_and_preserves_conflicts(mysql):
    from types import SimpleNamespace

    from bank_project.alignment.models import AlignmentError, SourceTable
    from bank_project.alignment.templates import (
        GraphTemplate,
        TemplateEdge,
        TemplateNode,
        TemplateProperty,
        validate_template,
    )
    from bank_project.staging.compile import TemplateCompiler
    from bank_project.staging.keys import KeyIndex

    _, store, _, ids, _ = mysql

    def source(text):
        batch = store.save(
            parse_file(text.encode(), "sample.csv", Limits()),
            name="test-template",
            source_kind="file",
            source="sample",
        )
        ids.append(batch.id)
        return SourceTable(
            batch=batch, table=batch.tables[0], rows=store.samples(batch.tables[0]), staged=True
        )

    one = source("cust,name,account,balance\nC001,甲,A001,1000\nC001,甲,A002,2000\n")
    two = source("cust,gender\nC001,女\n")

    def node(id, src, concept, key, fields, scope):
        return TemplateNode(
            id=id,
            table_id=src.table.id,
            concept_id=concept,
            identity_scope=scope,
            key_columns=[key],
            properties=[TemplateProperty(column=k, name=v) for k, v in fields.items()],
        )

    template = GraphTemplate(
        nodes=[
            node("customer", one, "customer", "cust", {"name": "姓名"}, "customers"),
            node("account", one, "account", "account", {"balance": "余额"}, "accounts"),
            node("details", two, "customer", "cust", {"gender": "性别"}, "customers"),
        ],
        edges=[
            TemplateEdge(
                id="owns",
                source="customer",
                target="account",
                name="持有",
                reason="客户与账户同一来源行",
            )
        ],
    )
    catalog = SimpleNamespace(names={"customer": "客户", "account": "账户"})
    template = validate_template(template, [one, two], catalog)
    compiler = TemplateCompiler(store, KeyIndex(store))
    version = compiler.compile([one, two], template, lambda: None)
    nodes = list(compiler.rows(version, "nodes"))
    edges = list(compiler.rows(version, "edges"))
    assert len(nodes) == 3 and len(edges) == 2
    customer = next(n for n in nodes if n["concept_id"] == "customer")
    assert customer["fields"] == {"姓名": "甲", "性别": "女"}
    assert all(e["source"] == customer["id"] for e in edges)
    bad = source("cust,name,account,balance\nC001,甲,A001,1000\nC001,乙,A002,2000\n")
    template.nodes = [node("customer", bad, "customer", "cust", {"name": "姓名"}, "customers")]
    template.edges = []
    with pytest.raises(AlignmentError, match="不同值"):
        compiler.compile([bad], template, lambda: None)
    with store.database.connect() as db:
        for table in ("graph_draft_nodes", "graph_draft_bindings", "graph_draft_edges"):
            db.execute(f"DELETE FROM {table} WHERE version=%s", (version,))
