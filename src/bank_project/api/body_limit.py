"""Bound request bodies before FastAPI parses JSON or buffers uploads."""

from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        path = scope["path"]
        if not path.startswith(("/api/v1/intake", "/api/v1/alignment")):
            return await self.app(scope, receive, send)
        limit = (
            scope["app"].state.intake.limits.max_upload_bytes
            if path == "/api/v1/intake/uploads"
            else (1024 * 1024 if path.endswith("/template") else 64 * 1024)
        )
        if path == "/api/v1/intake/uploads":
            from bank_project.intake.models import IntakeError

            consumed = 0

            async def bounded_receive():
                nonlocal consumed
                message = await receive()
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise IntakeError("请求内容超过大小上限，未保存数据", status=413)
                return message

            return await self.app(scope, bounded_receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                response = JSONResponse(
                    {"detail": "请求内容超过大小上限，未保存数据"}, status_code=413
                )
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
