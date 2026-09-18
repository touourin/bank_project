import io
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from bank_project.adapters.model_client.compatible import CompatibleModel
from bank_project.adapters.parsing.parser import FileParser
from bank_project.adapters.sources.mysql import MySQLReader
from bank_project.contracts.errors import DependencyUnavailable, IntakeError
from bank_project.contracts.intake import RawInput
from bank_project.contracts.models import ImportRequest
from bank_project.contracts.schema import IntakeLimits
from bank_project.extraction.validation import decimal_string

TEXT = "甲公司向乙公司转账100元。"
GOOD = {
    "entities": [
        {"id": "a", "type": "organization", "name": "甲公司", "quote": "甲公司"},
        {"id": "b", "type": "organization", "name": "乙公司", "quote": "乙公司"},
    ],
    "events": [
        {
            "id": "e",
            "type": "transfer",
            "participants": [
                {"entity_id": "a", "role": "payer"},
                {"entity_id": "b", "role": "payee"},
            ],
            "quote": TEXT,
            "properties": {"amount": "100"},
        }
    ],
    "relations": [
        {
            "subject_id": "a",
            "predicate": "transfers_to",
            "object_id": "b",
            "event_id": "e",
            "quote": TEXT,
        }
    ],
}


def model_for(handler):
    return CompatibleModel(
        "http://model.test/v1", "model", "test-secret", 1, httpx.MockTransport(handler)
    )


def response(payload=GOOD, finish="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": finish,
                    "message": {"content": json.dumps(payload, ensure_ascii=False)},
                }
            ]
        },
    )


def test_model_transport_validates_and_preserves_directed_events():
    def handler(request):
        assert str(request.url) == "http://model.test/v1/chat/completions"
        data = json.loads(request.content)
        assert data["messages"][1]["content"] == TEXT
        assert data["response_format"] == {"type": "json_object"}
        return response()

    result = model_for(handler).extract(TEXT)
    assert result.events[0].participants[0].role == "payer"
    assert result.relations[0].subject_id == "a"
    assert result.events[0].occurred_on is None


@pytest.mark.parametrize(
    "problem",
    [
        "fabricated_quote",
        "missing_entity",
        "duplicate_id",
        "truncated",
        "broken_json",
        "extra_field",
    ],
)
def test_bad_model_outputs_are_never_accepted(problem):
    payload = json.loads(json.dumps(GOOD))
    if problem == "fabricated_quote":
        payload["entities"][0]["quote"] = "不在原文的描述"
    elif problem == "missing_entity":
        payload["relations"][0]["object_id"] = "unknown"
    elif problem == "duplicate_id":
        payload["entities"][1]["id"] = "a"
    elif problem == "extra_field":
        payload["events"][0]["extra"] = "unexpected"

    def handler(request):
        if problem == "broken_json":
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]},
            )
        return response(payload, "length" if problem == "truncated" else "stop")

    with pytest.raises(IntakeError):
        model_for(handler).extract(TEXT)


def test_rate_limits_retry_but_credentials_and_response_body_are_not_exposed(monkeypatch):
    monkeypatch.setattr(
        "bank_project.adapters.model_client.compatible.time.sleep", lambda delay: None
    )
    attempts = []

    def retry(request):
        attempts.append(request)
        return (
            httpx.Response(429, text="private-upstream-message")
            if len(attempts) < 3
            else response()
        )

    model_for(retry).extract(TEXT)
    assert len(attempts) == 3

    def deny(request):
        return httpx.Response(401, text="test-secret private-upstream-message")

    with pytest.raises(DependencyUnavailable) as error:
        model_for(deny).extract(TEXT)
    assert "test-secret" not in str(error.value) and "private-upstream" not in str(error.value)
    with pytest.raises(DependencyUnavailable):
        model_for(
            lambda request: httpx.Response(302, headers={"location": "http://evil.test"})
        ).extract(TEXT)


def test_pdf_keeps_page_provenance_and_rejects_scanned_pages():
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 150 Td (A machine was repaired.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    data = io.BytesIO()
    writer.write(data)
    request = ImportRequest(
        dataset_id="d", batch_id="b", source_system="s", source_uri="upload:test.pdf"
    )
    parsed = FileParser(IntakeLimits()).parse(
        RawInput(filename="test.pdf", content=data.getvalue()), request
    )
    assert parsed.blocks[0].locator == "pdf:page:1"
    assert "machine was repaired" in parsed.blocks[0].text
    writer.add_blank_page(width=200, height=200)
    data = io.BytesIO()
    writer.write(data)
    with pytest.raises(IntakeError, match="OCR"):
        FileParser(IntakeLimits()).parse(
            RawInput(filename="test.pdf", content=data.getvalue()), request
        )


def test_mysql_reads_allowlisted_snapshot_without_accepting_sql(monkeypatch):
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, args=None):
            queries.append((sql, args))

        def __iter__(self):
            return iter(
                [
                    {
                        "order_id": "0001",
                        "amount": Decimal("12.3400"),
                        "created_at": datetime(2026, 9, 18, 13, 45, 52, 123456),
                        "day": date(2026, 9, 18),
                        "duration": timedelta(hours=-27, microseconds=-123456),
                    }
                ]
            )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

        def rollback(self):
            pass

    monkeypatch.setattr(
        "bank_project.adapters.sources.mysql.pymysql.connect", lambda **kwargs: Connection()
    )
    reader = MySQLReader(["orders"], IntakeLimits(), {})
    request = ImportRequest(
        dataset_id="d", batch_id="b", source_system="erp", source_uri="mysql:orders"
    )
    raw = reader.read(request)
    assert json.loads(raw.content) == [
        {
            "order_id": "0001",
            "amount": "12.3400",
            "created_at": "2026-09-18T13:45:52.123456",
            "day": "2026-09-18",
            "duration": "-PT97200.123456S",
        }
    ]
    assert queries[0][0] == "SET TRANSACTION READ ONLY"
    assert queries[2] == ("SELECT * FROM `orders` LIMIT %s", (50001,))
    with pytest.raises(IntakeError):
        reader.read(request.model_copy(update={"source_uri": "mysql:orders;DROP TABLE orders"}))
    with pytest.raises(IntakeError):
        reader.read(request.model_copy(update={"source_uri": "mysql:secrets"}))


@pytest.mark.parametrize(
    "number", ["NaN", "Infinity", "1e999999999", "0.000000001", "1000000000000000000", True, 1.25]
)
def test_invalid_decimal_values_are_rejected_without_rounding(number):
    with pytest.raises(IntakeError):
        decimal_string(number)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": [None]},
        {"choices": {}},
        {"choices": []},
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": '{"entities":[]}'}}]},
    ],
)
def test_missing_model_result_fields_are_failures_not_successful_empty_results(payload):
    model = CompatibleModel(
        "http://model/v1",
        "test",
        "",
        1,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(IntakeError):
        model.extract(TEXT)


def test_explicit_empty_model_results_are_valid():
    model = CompatibleModel("http://model/v1", "test", "", 1)
    payload = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": '{"entities":[],"events":[],"relations":[]}'},
            }
        ]
    }
    assert model._parse(json.dumps(payload).encode(), TEXT).entities == []


def test_model_rejects_compressed_response_before_reading_it():
    class UnreadableStream(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Compressed model response must not be decoded")
            yield b""

    def respond(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=UnreadableStream())

    model = CompatibleModel(
        "http://model/v1", "test", "", 1, transport=httpx.MockTransport(respond)
    )
    with pytest.raises(IntakeError, match="identity"):
        model.extract(TEXT)
