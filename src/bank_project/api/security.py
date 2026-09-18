import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


async def authorize(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> None:
    token = request.app.state.api_token
    if token and (
        credentials is None
        or not hmac.compare_digest(credentials.credentials.encode(), token.encode())
    ):
        raise HTTPException(401, "需要有效的 Bearer token", headers={"WWW-Authenticate": "Bearer"})


class RequestGuardMiddleware:
    """Authenticate and bound concurrent preparation work before parsing request bodies."""

    preparation_paths = {"/api/v1/imports", "/api/v1/imports/upload", "/api/v1/extractions"}

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes
        self.active_jobs = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope["app"].state
        max_bytes = getattr(state, "max_request_bytes", self.max_bytes)
        token = getattr(state, "api_token", None)
        if token and scope.get("path", "").startswith("/api/"):
            header = next(
                (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"),
                "",
            )
            scheme, _, credential = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                credential.encode(), token.encode()
            ):
                return await JSONResponse(
                    {"detail": "需要有效的 Bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise HTTPException(413, "请求体大小超限")
            return message

        lengths = [value for name, value in scope.get("headers", []) if name == b"content-length"]
        try:
            too_large = len(lengths) > 1 or (lengths and not 0 <= int(lengths[0]) <= max_bytes)
        except ValueError:
            too_large = True
        if too_large:
            return await JSONResponse(
                {"error": "LimitExceeded", "detail": "请求体大小超限"}, status_code=413
            )(scope, receive, send)
        preparation = (
            scope.get("method") == "POST"
            and scope.get("path", "").rstrip("/") in self.preparation_paths
        )
        if not preparation:
            return await self.app(scope, limited_receive, send)
        # ASGI requests on one event loop cannot interleave this check and increment.
        # Reject excess work immediately; do not retain queued uploads in memory.
        if self.active_jobs >= getattr(state, "max_concurrent_jobs", 2):
            return await JSONResponse(
                {"error": "ServiceBusy", "detail": "导入或转换任务已达并发上限，请稍后重试"},
                status_code=503,
                headers={"Retry-After": "1"},
            )(scope, receive, send)
        self.active_jobs += 1
        try:
            return await self.app(scope, limited_receive, send)
        finally:
            self.active_jobs -= 1
