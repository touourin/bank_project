"""Version-pinned retrieve transport, with bounded concurrency and per-run query reuse."""

import asyncio
import json
from collections import OrderedDict
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError


class Candidate(BaseModel):
    node_id: str = Field(min_length=1, max_length=200)
    node_name: str | None = Field(default=None, max_length=1000)
    score: float | None = Field(default=None, strict=True, ge=0, le=1, allow_inf_nan=False)


class RetrieveResponse(BaseModel):
    dataset_revision: str
    node_id: str | None = Field(default=None, max_length=200)
    confident: bool = Field(strict=True)
    match_method: str = Field(max_length=80)
    candidates: list[Candidate] = Field(default_factory=list, max_length=20)


class RetrievalResult(BaseModel):
    query: str
    status: Literal["ok", "mismatch", "unavailable"] = "ok"
    response: RetrieveResponse | None = None
    detail: str = ""


class SearchSession(Protocol):
    async def search_many(self, queries: list[str]) -> list[RetrievalResult]: ...


class RetrievalPort(Protocol):
    async def check_revision(self, revision: str) -> None: ...

    def session(self, revision: str) -> AbstractAsyncContextManager[SearchSession]: ...


class RetrievalFailure(Exception):
    def __init__(self, detail: str, status: str = "unavailable"):
        self.detail, self.status = detail, status


class RetrieveClient:
    def __init__(self, base_url: str | None, timeout: float = 60, concurrency: int = 3):
        self.base_url, self.timeout, self.concurrency = base_url, timeout, concurrency
        self.slots = asyncio.Semaphore(concurrency)

    @asynccontextmanager
    async def session(self, revision: str):
        # Internal ontology requests must not accidentally go through a system HTTP proxy.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=min(5, self.timeout)),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            yield RetrievalSession(self, client, revision)

    async def check_revision(self, revision: str):
        async with self.session(revision) as session:
            value = await session.request("GET", "/ready", params={"expected_revision": revision})
            if value.get("dataset_revision") != revision:
                raise RetrievalFailure("retrieve 与任务本体版本不一致，请核对本体配置", "mismatch")


class RetrievalSession:
    def __init__(self, adapter: RetrieveClient, client: httpx.AsyncClient, revision: str):
        self.adapter, self.client, self.revision = adapter, client, revision
        self.cache: OrderedDict[str, RetrievalResult] = OrderedDict()

    async def request(self, method: str, path: str, **kwargs) -> dict:
        if not self.adapter.base_url:
            raise RetrievalFailure("请先配置 BANK_ONTOLOGY_BASE_URL")
        url = self.adapter.base_url.rstrip("/") + path
        try:
            async with self.adapter.slots, asyncio.timeout(self.adapter.timeout):
                for attempt in range(2):
                    async with self.client.stream(method, url, **kwargs) as response:
                        if response.status_code == 409:
                            raise RetrievalFailure(
                                "retrieve 本体版本已变化，请同步后重新分析", "mismatch"
                            )
                        if response.status_code == 404:
                            raise RetrievalFailure(
                                "retrieve 未找到配置的本体，请检查接口地址和本体 ID"
                            )
                        if response.status_code in {429, 502, 503, 504} and attempt == 0:
                            await asyncio.sleep(0.5)
                            continue
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 1024 * 1024:
                                raise RetrievalFailure("retrieve 返回内容超过 1 MiB，未采用结果")
                    value = json.loads(data)
                    if not isinstance(value, dict):
                        raise ValueError("expected object")
                    return value
        except (httpx.HTTPError, TimeoutError) as exc:
            raise RetrievalFailure("retrieve 请求失败或超时，请检查服务后重试") from exc
        except (ValueError, TypeError) as exc:
            raise RetrievalFailure("retrieve 返回格式不符合约定，未采用结果") from exc
        raise AssertionError("unreachable")

    async def search(self, query: str) -> RetrievalResult:
        query = query.strip()
        if query in self.cache:
            self.cache.move_to_end(query)
            return self.cache[query].model_copy(deep=True)
        result = RetrievalResult(query=query)
        try:
            if not 1 <= len(query) <= 4000:
                raise RetrievalFailure("检索词不能为空或超过 4,000 字符")
            value = await self.request(
                "POST", "/retrieve", json={"query": query, "expected_revision": self.revision}
            )
            response = RetrieveResponse.model_validate(value)
            if response.dataset_revision != self.revision:
                raise RetrievalFailure("retrieve 返回的本体版本与任务不一致", "mismatch")
            ids = [c.node_id for c in response.candidates]
            if len(ids) != len(set(ids)) or (response.node_id and response.node_id not in ids):
                raise RetrievalFailure("retrieve 命中节点与候选列表不一致，未采用结果")
            result.response = response
        except RetrievalFailure as exc:
            result.status, result.detail = exc.status, exc.detail
        except ValidationError:
            result.status, result.detail = "unavailable", "retrieve 返回字段或分数格式不合法"
        # Failures are not reused; a transient failure must remain retryable.
        if result.status == "ok":
            self.cache[query] = result.model_copy(deep=True)
            if len(self.cache) > 2048:
                self.cache.popitem(last=False)
        return result

    async def search_many(self, queries: list[str]) -> list[RetrievalResult]:
        unique = iter(dict.fromkeys(q.strip() for q in queries))
        found: dict[str, RetrievalResult] = {}

        async def worker():
            for query in unique:
                found[query] = await self.search(query)

        # A fixed worker pool bounds both requests and task allocation for wide tables.
        async with asyncio.TaskGroup() as group:
            for _ in range(self.adapter.concurrency):
                group.create_task(worker())
        return [found[q.strip()].model_copy(deep=True) for q in queries]
