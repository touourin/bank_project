"""WHY import validates upstream identity and never publishes incomplete failures."""

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from bank_project.alignment.models import AlignmentError
from bank_project.risk.catalog import RiskCatalog
from bank_project.risk.import_why import MAX_RESPONSE_BYTES, import_why, main
from bank_project.risk.propagation import canonical_json

REVISION = "a" * 64
WHY = [{"rule_name": "现金限额", "priority": "高", "rule_result": "100000"}]


@pytest.fixture
def snapshot(tmp_path):
    nodes = [{"label": "OntologyDataset", "properties": {"revision": REVISION, "status": "ready"}}]
    for key in ("cash", "limit"):
        nodes.append(
            {
                "label": "Concept",
                "key": f"Concept:{REVISION}:{key}",
                "properties": {
                    "node_id": key,
                    "node_name": key,
                    "dataset_revision": REVISION,
                },
            }
        )
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "summary": {"sha256": "old"},
                "graph": {
                    "nodes": nodes,
                    "relationships": [
                        {
                            "start": f"Concept:{REVISION}:limit",
                            "end": f"Concept:{REVISION}:cash",
                            "type": "INHERES_IN",
                            "properties": {"dataset_revision": REVISION},
                        }
                    ],
                },
            }
        )
    )
    return path


def response(key, why=WHY, **changes):
    return {
        "node_id": key,
        "dataset_revision": REVISION,
        "dimensions": {"why": why},
        "dimension_hashes": {"why": hashlib.sha256(canonical_json(why).encode()).hexdigest()}
        if why
        else {},
        **changes,
    }


def offline(tmp_path, values):
    path = tmp_path / "dimensions.json"
    path.write_text(json.dumps(values))
    return path


def test_offline_import_preserves_input_and_records_full_coverage(snapshot, tmp_path):
    original = snapshot.read_bytes()
    source = offline(tmp_path, [response("cash", None), response("limit")])
    output = tmp_path / "enriched.json"
    summary = asyncio.run(import_why(snapshot, REVISION, output, dimensions_file=source))
    assert snapshot.read_bytes() == original
    assert summary["coverage_complete"] is True
    assert summary["verified_node_count"] == 2
    assert summary["why_node_count"] == 1 and summary["empty_why_node_count"] == 1
    c = RiskCatalog.load(output, REVISION)
    assert c.why_by_id == {"limit": WHY}
    assert c.sha256 == summary["snapshot_sha256"]
    exported = json.loads(output.read_bytes())
    assert "sha256" not in exported["summary"]
    assert (
        exported["risk_why_import"]["base_snapshot_sha256"] == hashlib.sha256(original).hexdigest()
    )


def test_explicit_partial_import_reports_unverified_nodes(snapshot, tmp_path):
    source = offline(tmp_path, [response("limit")])
    output = tmp_path / "partial.json"
    with pytest.raises(AlignmentError, match="未覆盖"):
        asyncio.run(import_why(snapshot, REVISION, output, dimensions_file=source))
    assert not output.exists()
    summary = asyncio.run(
        import_why(snapshot, REVISION, output, dimensions_file=source, node_ids=["limit"])
    )
    assert summary["coverage_complete"] is False
    assert summary["unverified_node_count"] == 1
    assert summary["verified_node_ids"] == ["limit"]


def test_existing_why_cannot_change_inside_same_revision(snapshot, tmp_path):
    original = offline(tmp_path, [response("cash", None), response("limit")])
    enriched = tmp_path / "first.json"
    asyncio.run(import_why(snapshot, REVISION, enriched, dimensions_file=original))
    different = offline(
        tmp_path, [response("cash", None), response("limit", {"rule_name": "不同内容"})]
    )
    output = tmp_path / "second.json"
    with pytest.raises(AlignmentError, match="已有 WHY"):
        asyncio.run(import_why(enriched, REVISION, output, dimensions_file=different))
    assert not output.exists()


@pytest.mark.parametrize(
    "invalid",
    [
        response("limit", dataset_revision="b" * 64),
        response("other"),
        response("limit", dimension_hashes={}),
        response("limit", dimension_hashes={"why": "f" * 64}),
        response("limit", dimensions={}),
        response("limit", dimensions={"why": "not structured"}),
    ],
)
def test_bad_offline_evidence_never_creates_output(snapshot, tmp_path, invalid):
    source = offline(tmp_path, [response("cash", None), invalid])
    output = tmp_path / "enriched.json"
    with pytest.raises(AlignmentError):
        asyncio.run(import_why(snapshot, REVISION, output, dimensions_file=source))
    assert not output.exists()


def test_duplicate_responses_and_duplicate_selections_rejected(snapshot, tmp_path):
    source = offline(tmp_path, [response("cash"), response("cash"), response("limit")])
    with pytest.raises(AlignmentError):
        asyncio.run(import_why(snapshot, REVISION, tmp_path / "out.json", dimensions_file=source))
    with pytest.raises(AlignmentError):
        asyncio.run(
            import_why(
                snapshot,
                REVISION,
                tmp_path / "out.json",
                dimensions_file=source,
                node_ids=["cash", "cash"],
            )
        )


def test_existing_output_and_same_input_cannot_be_overwritten(snapshot, tmp_path):
    source = offline(tmp_path, [response("cash"), response("limit")])
    existing = tmp_path / "exists.json"
    existing.write_text("keep")
    for output in (snapshot, existing):
        original = output.read_bytes()
        with pytest.raises(AlignmentError, match="不能覆盖"):
            asyncio.run(import_why(snapshot, REVISION, output, dimensions_file=source))
        assert output.read_bytes() == original


def test_remote_transport_pins_revision_and_reads_why_only(snapshot, tmp_path):
    requested = []

    def handler(request):
        assert str(request.url) == "http://ontology.test/v2/ontologies/id/concept/dimensions"
        payload = json.loads(request.content)
        assert payload["dimensions"] == ["why"] and payload["expected_revision"] == REVISION
        requested.append(payload["node_id"])
        return httpx.Response(200, json=response(payload["node_id"]))

    output = tmp_path / "api.json"
    summary = asyncio.run(
        import_why(
            snapshot,
            REVISION,
            output,
            base_url="http://ontology.test/v2/ontologies/id",
            transport=httpx.MockTransport(handler),
        )
    )
    assert requested == ["cash", "limit"]
    assert summary["coverage_complete"] is True
    assert summary["source_type"] == "concept_dimensions_api"


@pytest.mark.parametrize(
    "failure", ["version", "node", "oversized", "status", "redirect", "timeout", "json"]
)
def test_later_remote_failure_discards_all_results(snapshot, tmp_path, failure):
    def handler(request):
        key = json.loads(request.content)["node_id"]
        if key == "cash":
            return httpx.Response(200, json=response(key))
        if failure == "version":
            return httpx.Response(200, json=response(key, dataset_revision="b" * 64))
        if failure == "node":
            return httpx.Response(200, json=response("cash"))
        if failure == "oversized":
            return httpx.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1))
        if failure == "status":
            return httpx.Response(503)
        if failure == "redirect":
            return httpx.Response(307, headers={"location": "http://elsewhere.test"})
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, content=b"invalid json")

    output = tmp_path / "api.json"
    with pytest.raises(AlignmentError):
        asyncio.run(
            import_why(
                snapshot,
                REVISION,
                output,
                base_url="http://ontology.test",
                transport=httpx.MockTransport(handler),
            )
        )
    assert not output.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://username:secret@ontology.test",
        "file:///tmp/why",
        "http://ontology.test?key=secret",
        "http://ontology.test#fragment",
        "http://ontology.test/retrieve",
        "http://ontology.test:wrong",
    ],
)
def test_invalid_or_credential_urls_never_call_transport(snapshot, tmp_path, url):
    def handler(request):
        pytest.fail("invalid URL must not reach transport")

    with pytest.raises(AlignmentError):
        asyncio.run(
            import_why(
                snapshot,
                REVISION,
                tmp_path / "out.json",
                base_url=url,
                transport=httpx.MockTransport(handler),
            )
        )


def test_cli_help_and_offline_command(snapshot, tmp_path, capsys):
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    assert "--dimensions-file" in capsys.readouterr().out
    source = offline(tmp_path, [response("cash"), response("limit")])
    assert (
        main(
            [
                "--snapshot",
                str(snapshot),
                "--revision",
                REVISION,
                "--output",
                str(tmp_path / "cli.json"),
                "--dimensions-file",
                str(source),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["coverage_complete"] is True
    assert Path(report["output"]).is_file()
