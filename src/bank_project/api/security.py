"""Shared authentication and same-origin protection for business APIs."""

from secrets import compare_digest
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def authorize(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    token = request.app.state.settings.api_token
    if token and (
        not credentials
        or not compare_digest(credentials.credentials.encode(), token.get_secret_value().encode())
    ):
        raise HTTPException(401, "请填写有效的访问凭证", headers={"WWW-Authenticate": "Bearer"})
    origin = request.headers.get("origin")
    if origin:
        try:
            origin_host = urlsplit(origin).hostname
        except ValueError:
            origin_host = None
        host = request.url.hostname
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if not origin_host or not (origin_host == host or {origin_host, host} <= local_hosts):
            raise HTTPException(403, "不允许跨站访问业务接口")
