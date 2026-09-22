"""Shared review rules must preserve each source's publication and revision semantics."""

import asyncio
from types import SimpleNamespace

import pytest
from test_alignment import FakeModel, FakeRetriever, ignore_progress, source
from test_knowledge_matching import service

from bank_project.alignment.analyzer import Analyzer
from bank_project.alignment.catalog import Catalog
from bank_project.alignment.editing import revise_mapping
from bank_project.alignment.models import AlignmentError, MappingEdit, MappingEditRequest
from bank_project.alignment.service import AlignmentService
from bank_project.alignment.store import RunStore
from bank_project.api.knowledge import MatchDecision
from bank_project.conversion.versions import GraphVersion


def conversions(tmp_path):
    text = service(tmp_path)
    catalog = Catalog.load(text.settings.ontology_snapshot)
    result = asyncio.run(
        Analyzer(FakeModel(), FakeRetriever()).analyze([source()], catalog, ignore_progress)
    )
    store = RunStore(tmp_path / "table-runs.db")
    table, sequence, owner = store.create([source()])
    table = store.update(
        table.id, sequence, owner, finish=True, status="ready", result=result.model_dump()
    )
    tables = AlignmentService(
        text.settings, None, store, SimpleNamespace(relation_index=None), None
    )

    async def convert_text():
        run = await text.start_match("graphrag", "dataset")
        await asyncio.gather(*text.tasks)
        return text.store.get(run["id"])

    return tables, table, text, asyncio.run(convert_text()), catalog


def test_table_and_text_review_keep_pinned_ontology_when_current_source_changes(tmp_path):
    tables, table, text, match, _ = conversions(tmp_path)
    snapshot = text.settings.ontology_snapshot
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    for review in (
        lambda: tables.edit(
            table.id, MappingEditRequest(table_id="customers", concept_id="account", reason="核对")
        ),
        lambda: text.review(
            match["id"],
            MatchDecision(target="node", target_id="a", boid="account", expected_revision=1),
        ),
    ):
        review()
    assert tables.store.get(table.id) == table
    assert text.store.get(match["id"])["nodes"][0]["boid"] == "account"


def test_review_rejects_tampered_archived_ontology(tmp_path):
    tables, table, text, match, _ = conversions(tmp_path)
    for ontology, revision, digest in (
        (tables.ontology, table.result.revision, table.result.snapshot_sha256),
        (text.ontology, match["ontology_revision"], match["snapshot_sha256"]),
    ):
        path = ontology.snapshots.path(digest)
        path.write_bytes(path.read_bytes() + b" ")
        with pytest.raises(AlignmentError, match="校验失败"):
            ontology.pinned(revision, digest)


def test_review_attribution_is_stable_across_table_forks_and_text_revisions(tmp_path):
    tables, table, text, match, catalog = conversions(tmp_path)
    # Legacy edits must remain readable without inventing their author or version.
    table.result.tables[0].manual_edits.append(
        MappingEdit(created_at="2026-01-01", reason="旧记录")
    )
    legacy = tables.store.revise(table.id, table.result)
    request = MappingEditRequest(
        table_id="customers", concept_id="account", reason="人工核对", reviewer=" 张三 "
    )
    first = tables.edit(legacy.id, request)
    edits = first.result.tables[0].manual_edits
    assert edits[0].id is None and edits[0].version is None and edits[0].reviewer == ""
    assert edits[1].id and edits[1].version == first.id and edits[1].reviewer == "张三"
    second = tables.store.revise(
        first.id,
        revise_mapping(
            first.result,
            request.model_copy(update={"column": "name", "reviewer": "李四"}),
            catalog,
            [source()],
        ),
    )
    updated = second.result.tables[0].manual_edits
    assert updated[1] == edits[1]
    assert updated[2].id != edits[1].id and updated[2].version == second.id
    assert tables.store.get(first.id) == first
    assert RunStore(tables.store.path).get(second.id) == second

    decision = MatchDecision(
        target="node",
        target_id="a",
        boid="account",
        expected_revision=1,
        reviewer=" 张三 ",
        note="人工核对",
    )
    reviewed = text.review(match["id"], decision)
    audit = reviewed["audits"][0]
    assert audit["id"] and audit["reviewer"] == "张三" and audit["revision"] == 2
    again = text.review(
        match["id"], decision.model_copy(update={"boid": None, "expected_revision": 2})
    )
    assert again["audits"][0] == audit
    assert again["audits"][1]["id"] != audit["id"]


@pytest.mark.parametrize(
    "reference",
    [
        "match:bad:1",
        "match:bad:-1",
        "match:bad:²",
        "resolution:missing",
        "other:00000000-0000-0000-0000-000000000001:1",
    ],
)
def test_malformed_versions_are_validation_errors_instead_of_storage_failures(reference):
    with pytest.raises(AlignmentError, match="版本标识无效") as error:
        GraphVersion.parse(reference)
    assert error.value.status == 422
