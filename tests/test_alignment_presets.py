"""Default plans preserve rows and fields without approving uncertain identities."""

import pytest
from fastapi.testclient import TestClient
from test_alignment import FakeRetriever, catalog_bytes, source
from test_alignment_proposals import proposal, ready_run

from bank_project.alignment.catalog import Catalog
from bank_project.alignment.models import AlignmentError
from bank_project.alignment.presets import row_record_template
from bank_project.alignment.templates import GraphTemplate, TemplateEdge, validate_template
from bank_project.main import create_app
from bank_project.settings import Settings


def test_default_keeps_every_field_even_repeated_and_null_keys_without_mutating_input():
    src = source(values=[[None, "甲"], [None, "乙"], ["001", "甲"], ["001", "乙"]])
    src.table.columns[0].primary_key = True
    result = proposal(src=src, retriever=FakeRetriever(score=0.194))
    original = result.model_copy(deep=True)
    node = result.template.nodes[0]
    node.key_columns = ["name"]
    node.properties = node.properties[:1]
    edited = result.model_copy(deep=True)
    default = row_record_template([src], result.tables, result.template, Catalog(catalog_bytes()))
    assert default.mode == "row_records" and not default.confirmed
    assert len(default.nodes) == 1 and not default.edges
    node = default.nodes[0]
    assert node.key_columns == [] and node.identity_scope == "row:customers"
    assert [(p.column, p.name) for p in node.properties] == [("id", "id"), ("name", "name")]
    assert node.concept_id == "customer"
    assert result == edited
    assert result.tables[0].trace == original.tables[0].trace
    assert result.tables[0].confidence == 0.194
    assert validate_template(default, [src], Catalog(catalog_bytes())).confirmed
    assert (
        row_record_template([src], result.tables, result.template, Catalog(catalog_bytes()))
        == default
    )


def test_default_uses_whole_table_choice_for_split_objects_and_keeps_manual_single_type():
    src, catalog = source(), Catalog(catalog_bytes())
    result = proposal()
    node = result.template.nodes[0]
    account = node.model_copy(deep=True, update={"id": "account", "concept_id": "account"})
    result.template.nodes.append(account)
    result.template.edges = [
        TemplateEdge(id="rel", source=node.id, target="account", name="持有", reason="测试")
    ]
    default = row_record_template([src], result.tables, result.template, catalog)
    assert len(default.nodes) == 1 and default.nodes[0].concept_id == "customer"
    assert default.edges == []
    result.template.nodes = [account]
    changed = row_record_template([src], result.tables, result.template, catalog)
    assert changed.nodes[0].concept_id == "account"
    assert changed.nodes[0].concept_name == "账户"


@pytest.mark.parametrize("status", ["unavailable", "mismatch"])
def test_default_does_not_select_random_object_on_lookup_failure(status):
    src = source()
    result = proposal(retriever=FakeRetriever(status=status))
    default = row_record_template([src], result.tables, GraphTemplate(), Catalog(catalog_bytes()))
    assert not default.nodes
    assert "bank / customers" in default.note


def test_default_does_not_choose_one_participant_as_the_whole_record():
    src, result = source(), proposal(retriever=FakeRetriever(node_id=None))
    candidate = proposal().template.nodes[0]
    current = GraphTemplate(nodes=[candidate, candidate.model_copy(update={"id": "receiver"})])
    default = row_record_template([src], result.tables, current, Catalog(catalog_bytes()))
    assert not default.nodes and default.note


@pytest.mark.parametrize("change", ["key", "missing_field", "shared_identity", "duplicate_table"])
def test_default_label_cannot_conceal_custom_merging_or_partial_fields(change):
    src, result, catalog = source(), proposal(), Catalog(catalog_bytes())
    default = row_record_template([src], result.tables, result.template, catalog)
    node = default.nodes[0]
    if change == "key":
        node.key_columns = ["id"]
    elif change == "missing_field":
        node.properties.pop()
    elif change == "shared_identity":
        node.identity_scope = "shared"
    else:
        default.nodes.append(node.model_copy(update={"id": "extra"}))
    with pytest.raises(AlignmentError, match="默认方案"):
        validate_template(default, [src], catalog)


def test_preview_is_readonly_accepts_current_type_edits_and_preserves_ontology_version(tmp_path):
    path = tmp_path / "ontology.json"
    path.write_bytes(catalog_bytes())
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, ontology_snapshot=path))
    with TestClient(app) as client:
        store = app.state.alignment.store
        run = ready_run(store, proposal())
        current = run.result.template.model_dump()
        current["nodes"][0]["concept_id"] = "account"
        url = f"/api/v1/alignment/runs/{run.id}/template/default"
        response = client.post(url, json=current)
        assert response.status_code == 200
        assert response.json()["nodes"][0]["concept_id"] == "account"
        assert response.json()["mode"] == "row_records"
        assert not response.json()["confirmed"]
        assert store.get(run.id) == run and len(store.list()) == 1
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM jobs WHERE active=1").fetchone()[0] == 0
        # Changing the snapshot blocks preset creation as well as adoption.
        path.write_bytes(path.read_bytes() + b" ")
        assert client.post(url, json=current).status_code == 409
