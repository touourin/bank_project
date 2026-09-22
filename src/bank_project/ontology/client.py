"""Bounded read-only HTTP access to a single ontology and revision."""

import json

import httpx

from bank_project.alignment.models import AlignmentError

MAX_RESPONSE_BYTES = 30 * 1024 * 1024


class OntologyClient:
    def __init__(self, settings, *, transport=None):
        self.base_url = settings.ontology_base_url
        self.timeout = settings.retrieve_timeout_seconds
        self.transport = transport

    def session(self):
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=min(5, self.timeout)),
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
        )

    async def request(
        self, client, method, path, *, revision=None, payload=None, limit=MAX_RESPONSE_BYTES
    ):
        kwargs = (
            {"json": payload}
            if payload is not None
            else {"params": {"expected_revision": revision} if revision else {}}
        )
        async with client.stream(method, self.base_url + path, **kwargs) as response:
            if response.status_code == 409:
                raise AlignmentError("远端本体版本已变化，请核对版本配置后重试", 409)
            if response.status_code != 200:
                raise AlignmentError(f"远端本体返回 HTTP {response.status_code}", 503)
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > limit:
                    raise AlignmentError("远端本体响应超过读取上限", 503)
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("expected ontology object")
        return value

    async def ready(self, client, expected):
        value = await self.request(client, "GET", "/ready", revision=expected)
        if (
            value.get("ready") is not True
            or not value.get("dataset_revision")
            or not value.get("ontology_id")
        ):
            raise AlignmentError("远端本体尚未就绪", 503)
        if expected and value["dataset_revision"] != expected:
            raise AlignmentError("远端本体与请求版本不一致", 409)
        # A proxy must not serve a different ontology under the configured v2 URL.
        if (
            "/v2/ontologies/" in self.base_url
            and value["ontology_id"] != self.base_url.rsplit("/", 1)[-1]
        ):
            raise AlignmentError("远端响应与配置的本体 ID 不一致", 409)
        return value

    async def dimensions(self, client, catalog, node_id, names):
        value = await self.request(
            client,
            "POST",
            "/concept/dimensions",
            payload={
                "node_id": node_id,
                "dimensions": names,
                "expected_revision": catalog.revision,
            },
            limit=1024 * 1024,
        )
        if (
            value.get("ontology_id") != catalog.ontology_id
            or value.get("dataset_revision") != catalog.revision
            or value.get("node_id") != node_id
        ):
            raise AlignmentError("维度响应的本体、版本或节点不一致", 409)
        return value
