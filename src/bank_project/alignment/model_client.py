"""Bounded OpenAI-compatible JSON transport. No provider errors or credentials escape."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx

from bank_project.settings import Settings

from .models import AlignmentError, IncompleteModelOutput


class JsonModel:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client

    @asynccontextmanager
    async def transport(self):
        if self.client is not None:
            yield self.client
        else:
            async with httpx.AsyncClient(
                timeout=self.settings.model_timeout_seconds, follow_redirects=False
            ) as client:
                yield client

    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(
            s.model_api_key
            and (s.model_provider == "dashscope" or (s.model_base_url and s.model_name))
        )

    async def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> dict:
        s = self.settings
        if not self.configured:
            raise AlignmentError("请先配置大模型地址、模型名和密钥", 503)
        base = s.model_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        payload = {
            "model": s.model_name or "qwen3.7-plus",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens if max_tokens is not None else s.model_max_tokens,
            "response_format": {"type": "json_object"},
        }
        if s.model_provider == "dashscope":
            payload["enable_thinking"] = False
        async with self.transport() as client:
            for attempt in range(s.model_max_retries + 1):
                try:
                    # Include streaming reads in a wall-clock deadline and cap the response body.
                    async with asyncio.timeout(s.model_timeout_seconds):
                        async with client.stream(
                            "POST",
                            base.rstrip("/") + "/chat/completions",
                            json=payload,
                            headers={
                                "Authorization": "Bearer " + s.model_api_key.get_secret_value()
                            },
                        ) as response:
                            response.raise_for_status()
                            data = bytearray()
                            async for chunk in response.aiter_bytes():
                                data.extend(chunk)
                                if len(data) > 2 * 1024 * 1024:
                                    raise AlignmentError("模型响应过大，本表未完成分析")
                    result = json.loads(data)
                    choice = result["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise IncompleteModelOutput("模型输出达到 token 上限")
                    if choice.get("finish_reason") != "stop":
                        raise AlignmentError("模型输出未完整结束，本次结果未采用")
                    text = choice["message"]["content"]
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                    value = json.loads(text)
                    if not isinstance(value, dict):
                        raise ValueError("not an object")
                    return value
                except (httpx.HTTPError, TimeoutError) as exc:
                    retryable = not isinstance(
                        exc, httpx.HTTPStatusError
                    ) or exc.response.status_code in {429, 500, 502, 503, 504}
                    if attempt < s.model_max_retries and retryable:
                        await asyncio.sleep(2**attempt)
                        continue
                    raise AlignmentError("模型请求失败或超时，请检查服务配置后重试", 503) from exc
                except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                    raise AlignmentError("模型未返回完整、有效的 JSON，本表未完成分析") from exc
        raise AssertionError("unreachable")
