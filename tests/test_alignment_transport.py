import asyncio
import json

import httpx
import pytest

from bank_project.alignment.model_client import JsonModel
from bank_project.alignment.models import AlignmentError
from bank_project.settings import Settings


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_model_uses_json_mode_and_only_retries_transient_failure(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["enable_thinking"] is False
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"result":"ok"}'}}]
            },
        )

    transport(monkeypatch, handler)
    model = JsonModel(Settings(_env_file=None, model_api_key="never-output", model_max_retries=1))
    assert asyncio.run(model.complete("system", "data")) == {"result": "ok"}
    assert len(requests) == 2


@pytest.mark.parametrize(
    "status,body",
    [
        (401, {"detail": "sensitive-body"}),
        (200, {"choices": [{"finish_reason": "length", "message": {"content": "sensitive-body"}}]}),
        (200, {"choices": [{"finish_reason": "stop", "message": {"content": "sensitive-body"}}]}),
    ],
)
def test_model_failure_never_leaks_response_or_key(monkeypatch, status, body):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    transport(monkeypatch, handler)
    model = JsonModel(Settings(_env_file=None, model_api_key="never-output"))
    with pytest.raises(AlignmentError) as raised:
        asyncio.run(model.complete("system", "data"))
    assert "sensitive-body" not in str(raised.value) and "never-output" not in str(raised.value)
    assert len(requests) == 1
